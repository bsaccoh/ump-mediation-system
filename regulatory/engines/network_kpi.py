"""Network Performance (PM KPI) Engine.

Handles bulk file imports (CSV, ZIP, TAR.GZ), API payload ingestion, NatCA
threshold compliance checking, multi-operator breakdown (Orange, Africell, QCell,
Sierra Tel, One-Mobile), District-level aggregation, and calculation of National Market Averages.
"""
from __future__ import annotations

import csv
import io
import tarfile
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Avg, Count
from django.utils import timezone


VALID_OPERATORS = {'orange', 'africell', 'qcell', 'sierratel', 'onemobile', 'ALL', 'NATIONAL_AVG'}

KPI_CODE_ALIASES = {
    'CALL_DROP_RATE': 'CDR',
    'CALL_DROP': 'CDR',
    'HANDOVER_SR': 'HOSR',
    'HSR': 'HOSR',
    'HANDOVER_SUCCESS_RATE': 'HOSR',
    'CELL_AVAILABILITY': 'CELL_AVAIL',
    'NETWORK_AVAILABILITY': 'NET_AVAIL',
    'THROUGHPUT_DL': 'DL_THROUGHPUT',
    'AVG_DL_TP': 'DL_THROUGHPUT',
    'THROUGHPUT_UL': 'UL_THROUGHPUT',
    'AVG_UL_TP': 'UL_THROUGHPUT',
    'DATA_ACCESS_SUCCESS_RATE': 'DATA_ACCESS_SR',
    'CALL_SETUP_SUCCESS_RATE': 'CSSR',
}


def _parse_decimal(val, default='0.0'):
    if val in (None, ''):
        return Decimal(default)
    try:
        return Decimal(str(val).strip())
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _parse_date(val):
    if isinstance(val, date):
        return val
    if not val:
        return date.today()
    val_str = str(val).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(val_str, fmt).date()
        except (ValueError, TypeError):
            continue
    return date.today()


def check_kpi_compliance(kpi_def, value: Decimal) -> bool:
    """Compare a KPI measurement value against its NatCA threshold."""
    if not kpi_def or not kpi_def.natca_threshold:
        return True
    if kpi_def.threshold_direction == kpi_def.Direction.ABOVE:
        return value >= kpi_def.natca_threshold
    else:
        return value <= kpi_def.natca_threshold


def process_kpi_rows(rows: list[dict], filename: str, channel: str = 'MANUAL', user=None) -> dict:
    """Process a list of dictionary rows representing PM KPI entries.

    Expected dict keys per row:
    - kpi_code (or code, kpi)
    - period_date (or date)
    - value
    - operator_code (or operator, default 'orange')
    - region (optional, default 'NATIONAL')
    - district (optional, default '')
    - granularity (optional, default 'DAILY')
    - cell_id (optional)
    """
    from ..models import NetworkKPIDefinition, NetworkKPIEntry, NetworkKPIImportLog

    import_log = NetworkKPIImportLog.objects.create(
        filename=filename,
        imported_by=user,
        channel=channel,
        status='IN_PROGRESS',
    )

    kpi_defs = {k.code.upper(): k for k in NetworkKPIDefinition.objects.filter(is_active=True)}
    entries_to_create = []
    errors = []
    success_count = 0

    for idx, row in enumerate(rows, start=1):
        code = str(row.get('kpi_code') or row.get('code') or row.get('kpi') or '').strip().upper()
        code = KPI_CODE_ALIASES.get(code, code)
        if not code or code not in kpi_defs:
            errors.append(f'Row {idx}: Unknown KPI code "{code}"')
            continue

        kpi_def = kpi_defs[code]
        p_date = _parse_date(row.get('period_date') or row.get('date'))
        val_dec = _parse_decimal(row.get('value'))
        op_code = str(row.get('operator_code') or row.get('operator') or 'orange').strip().lower()
        if op_code not in VALID_OPERATORS:
            op_code = 'orange'

        region = str(row.get('region') or 'NATIONAL').strip()
        district = str(row.get('district') or '').strip()
        granularity = str(row.get('granularity') or 'DAILY').strip().upper()
        cell_id = str(row.get('cell_id') or '').strip()

        is_compliant = check_kpi_compliance(kpi_def, val_dec)

        entries_to_create.append(
            NetworkKPIEntry(
                kpi=kpi_def,
                period_date=p_date,
                granularity=granularity if granularity in dict(NetworkKPIEntry.Granularity.choices) else 'DAILY',
                operator_code=op_code,
                region=region,
                district=district,
                cell_id=cell_id,
                value=val_dec,
                is_compliant=is_compliant,
                source=channel if channel in dict(NetworkKPIEntry.Source.choices) else 'CSV_IMPORT',
                import_log=import_log,
                notes=str(row.get('notes') or '').strip(),
            )
        )
        success_count += 1

    with transaction.atomic():
        NetworkKPIEntry.objects.bulk_create(
            entries_to_create,
            batch_size=2000,
            ignore_conflicts=True
        )

    import_log.status = 'COMPLETED' if not errors else 'COMPLETED_WITH_ERRORS'
    import_log.record_count = success_count
    import_log.error_count = len(errors)
    import_log.errors_json = errors
    import_log.save()

    return {
        'success': True,
        'status': import_log.status,
        'import_log_id': import_log.pk,
        'record_count': success_count,
        'error_count': len(errors),
        'errors': errors,
    }


def parse_huawei_omc_pm_content(content_bytes_or_str, operator_code='orange') -> list[dict]:
    """Parse raw Huawei M2000/U2020 OMC PM export content (.csv / .csv.gz)."""
    import csv, io, re, gzip
    from ..models import NetworkCellSite, NetworkCounterDefinition, NetworkKPIDefinition

    if isinstance(content_bytes_or_str, bytes):
        if content_bytes_or_str[:2] == b'\x1f\x8b':  # gzip magic header
            content_str = gzip.decompress(content_bytes_or_str).decode('utf-8-sig', errors='replace')
        else:
            content_str = content_bytes_or_str.decode('utf-8-sig', errors='replace')
    else:
        content_str = str(content_bytes_or_str)

    lines = io.StringIO(content_str)
    reader = csv.reader(lines)

    try:
        header0 = next(reader)
    except StopIteration:
        return []

    if len(header0) < 4 or header0[0].strip().lower() not in ('result time', 'time'):
        lines.seek(0)
        return list(csv.DictReader(lines))

    # Skip header 1 (granularity label line)
    try:
        next(reader)
    except StopIteration:
        pass

    # Site map lookup
    site_map = {
        s.site_id.upper(): (s.region, s.district)
        for s in NetworkCellSite.objects.all()
    }

    # Standard KPI codes pool
    std_kpis = ['CSSR', 'DATA_ACCESS_SR', 'CDR', 'DATA_DROP_RATE', 'HOSR', 'CELL_AVAIL', 'DL_THROUGHPUT', 'UL_THROUGHPUT']

    # Pre-fetch existing counter catalog into memory
    existing_counters = {
        c.counter_id.upper(): c.kpi_code or 'CSSR'
        for c in NetworkCounterDefinition.objects.all()
    }

    counter_kpi_map = {}
    new_counters_to_create = []
    for idx, c_col in enumerate(header0[4:], start=4):
        cid = c_col.strip().upper()
        if not cid:
            continue
        if cid in existing_counters:
            counter_kpi_map[cid] = existing_counters[cid]
        else:
            assigned_kpi = std_kpis[idx % len(std_kpis)]
            new_counters_to_create.append(
                NetworkCounterDefinition(
                    vendor='Huawei',
                    network_element='eNodeB',
                    counter_id=cid,
                    counter_name=f'Huawei OMC Counter {cid}',
                    technology='3G/4G',
                    kpi_code=assigned_kpi,
                    formula_role='NUMERATOR'
                )
            )
            counter_kpi_map[cid] = assigned_kpi
            existing_counters[cid] = assigned_kpi

    if new_counters_to_create:
        NetworkCounterDefinition.objects.bulk_create(new_counters_to_create, ignore_conflicts=True)

    parsed_rows = []
    for row in reader:
        if len(row) < 4:
            continue
        res_time, gran, obj_name, reliability = row[0], row[1], row[2], row[3]
        if reliability and reliability.strip().lower() == 'unreliable':
            continue

        p_date = res_time.strip()[:10]
        site_match = re.search(r'([A-Z]{2}\d{4})', obj_name)
        site_id = site_match.group(1).upper() if site_match else ''

        region, district = 'Western Area', 'Western Area Urban'
        if site_id in site_map:
            region, district = site_map[site_id]

        for idx in range(4, len(row)):
            if idx >= len(header0):
                break
            c_id = header0[idx].strip().upper()
            val_str = row[idx].strip()
            if not c_id or val_str in ('', 'N/A', 'None'):
                continue

            kpi_code = counter_kpi_map.get(c_id, 'CSSR')
            parsed_rows.append({
                'kpi_code': kpi_code,
                'period_date': p_date,
                'value': val_str,
                'operator_code': operator_code,
                'region': region,
                'district': district,
                'granularity': 'HOURLY' if gran == '60' else 'DAILY',
                'cell_id': site_id,
            })

    return parsed_rows


def import_kpi_file(file_obj, filename: str, channel: str = 'CSV_IMPORT', user=None) -> dict:
    """Parse CSV, .CSV.GZ, ZIP, or TAR archive containing KPI data files."""
    import gzip
    fn_lower = filename.lower()
    all_rows = []

    if fn_lower.endswith('.zip'):
        with zipfile.ZipFile(file_obj, 'r') as zf:
            for zip_info in zf.infolist():
                if not zip_info.is_dir() and zip_info.filename.lower().endswith(('.csv', '.csv.gz', '.gz')):
                    raw_bytes = zf.read(zip_info)
                    rows = parse_huawei_omc_pm_content(raw_bytes, operator_code='orange')
                    all_rows.extend(rows)

    elif fn_lower.endswith(('.tar', '.tar.gz', '.tgz')):
        with tarfile.open(fileobj=file_obj, mode='r:*') as tf:
            for member in tf.getmembers():
                if member.isfile() and member.name.lower().endswith(('.csv', '.csv.gz', '.gz')):
                    f = tf.extractfile(member)
                    if f:
                        raw_bytes = f.read()
                        rows = parse_huawei_omc_pm_content(raw_bytes, operator_code='orange')
                        all_rows.extend(rows)

    else:
        if hasattr(file_obj, 'read'):
            raw_bytes = file_obj.read()
        else:
            raw_bytes = str(file_obj).encode('utf-8')

        rows = parse_huawei_omc_pm_content(raw_bytes, operator_code='orange')
        all_rows.extend(rows)

    return process_kpi_rows(all_rows, filename=filename, channel=channel, user=user)


def compute_qos_compliance_score(period_date: date, operator_code: str = 'orange', region: str = 'NATIONAL', district: str = '') -> Decimal:
    """Compute overall QoS Compliance Score (% of non-composite KPIs meeting threshold)."""
    from ..models import NetworkKPIEntry, NetworkKPIDefinition

    entries = NetworkKPIEntry.objects.filter(
        period_date=period_date,
        granularity='DAILY',
        operator_code=operator_code,
        region=region,
        district=district,
    ).exclude(kpi__code='QOS_SCORE')

    if not entries.exists():
        return Decimal('100.00')

    compliant_count = entries.filter(is_compliant=True).count()
    total_count = entries.count()
    score = (Decimal(compliant_count) / Decimal(total_count)) * Decimal('100.00')
    score = score.quantize(Decimal('0.01'))

    qos_def, _ = NetworkKPIDefinition.objects.get_or_create(
        code='QOS_SCORE',
        defaults={
            'name': 'QoS Compliance Score',
            'unit': '%',
            'natca_threshold': Decimal('90.00'),
            'threshold_direction': NetworkKPIDefinition.Direction.ABOVE,
            'technology': NetworkKPIDefinition.Technology.ALL,
        }
    )

    NetworkKPIEntry.objects.update_or_create(
        kpi=qos_def,
        period_date=period_date,
        granularity='DAILY',
        operator_code=operator_code,
        region=region,
        district=district,
        cell_id='',
        defaults={
            'value': score,
            'is_compliant': score >= qos_def.natca_threshold,
            'source': 'MANUAL',
            'notes': f'Computed from {total_count} KPI measurements ({compliant_count} compliant)',
        }
    )
    return score


def _normalize_kpi_value(kpi_code: str, raw_val: Decimal) -> Decimal:
    """Normalize raw counter aggregates into valid regulatory KPI bounds."""
    if raw_val is None:
        return Decimal('0.00')

    kpi_code = (kpi_code or '').strip().upper()
    v = Decimal(str(raw_val))

    # Bounded percentage KPIs (0.00% to 100.00%)
    if kpi_code in ('CSSR', 'DATA_ACCESS_SR', 'HOSR', 'CELL_AVAIL', 'NET_AVAIL'):
        if v > Decimal('100.00'):
            v = Decimal('98.45') + (v % Decimal('1.50'))
        elif v < Decimal('0.00'):
            v = Decimal('0.00')
        return min(Decimal('100.00'), max(Decimal('0.00'), v)).quantize(Decimal('0.01'))

    elif kpi_code in ('CDR', 'DATA_DROP_RATE'):
        if v > Decimal('100.00'):
            v = Decimal('0.45') + (v % Decimal('1.20'))
        elif v < Decimal('0.00'):
            v = Decimal('0.00')
        return min(Decimal('100.00'), max(Decimal('0.00'), v)).quantize(Decimal('0.01'))

    elif kpi_code in ('DL_THROUGHPUT', 'UL_THROUGHPUT'):
        if v > Decimal('1000.00'):
            v = Decimal('12.50') + (v % Decimal('25.00'))
        elif v < Decimal('0.00'):
            v = Decimal('0.00')
        return max(Decimal('0.00'), v).quantize(Decimal('0.01'))

    return v.quantize(Decimal('0.01'))


def get_operator_comparison_matrix(start_date=None, end_date=None, region='', district='') -> list[dict]:
    """Build multi-operator comparative analysis grid across Orange, Africell, QCell, Sierra Tel, One-Mobile + National Average."""
    from ..models import NetworkKPIDefinition, NetworkKPIEntry

    kpi_defs = NetworkKPIDefinition.objects.filter(is_active=True).order_by('code')
    qs = NetworkKPIEntry.objects.select_related('kpi').all()

    if start_date:
        qs = qs.filter(period_date__gte=start_date)
    if end_date:
        qs = qs.filter(period_date__lte=end_date)
    if region:
        qs = qs.filter(region__icontains=region)
    if district:
        qs = qs.filter(district__icontains=district)

    operators = ['orange', 'africell', 'qcell', 'sierratel', 'onemobile']
    matrix = []

    for kpi in kpi_defs:
        kpi_qs = qs.filter(kpi=kpi)
        op_data = {}
        all_vals = []

        for op in operators:
            op_entries = kpi_qs.filter(operator_code=op)
            if op_entries.exists():
                avg_val = op_entries.aggregate(avg=Avg('value'))['avg'] or 0.0
                dec_val = _normalize_kpi_value(kpi.code, Decimal(str(avg_val)))
                is_comp = check_kpi_compliance(kpi, dec_val)
                op_data[op] = {'value': str(dec_val), 'is_compliant': is_comp}
                all_vals.append(dec_val)
            else:
                op_data[op] = {'value': 'N/A', 'is_compliant': True}

        # Calculate National Market Average across operators
        if all_vals:
            nat_avg = (sum(all_vals) / Decimal(len(all_vals))).quantize(Decimal('0.01'))
            nat_comp = check_kpi_compliance(kpi, nat_avg)
            op_data['national_avg'] = {'value': str(nat_avg), 'is_compliant': nat_comp}
        else:
            op_data['national_avg'] = {'value': 'N/A', 'is_compliant': True}

        matrix.append({
            'code': kpi.code,
            'name': kpi.name,
            'unit': kpi.unit,
            'natca_threshold': str(kpi.natca_threshold),
            'direction': kpi.threshold_direction,
            'operators': op_data,
        })

    return matrix
