"""Network Performance (PM KPI) Engine.

Handles bulk file imports (CSV, ZIP, TAR.GZ), API payload ingestion, NatCA
threshold compliance checking, and calculation of the composite QoS Compliance Score.
"""
from __future__ import annotations

import csv
import io
import tarfile
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone


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
    - region (optional, default 'NATIONAL')
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
        if not code or code not in kpi_defs:
            errors.append(f'Row {idx}: Unknown KPI code "{code}"')
            continue

        kpi_def = kpi_defs[code]
        p_date = _parse_date(row.get('period_date') or row.get('date'))
        val_dec = _parse_decimal(row.get('value'))
        region = str(row.get('region') or 'NATIONAL').strip()
        granularity = str(row.get('granularity') or 'DAILY').strip().upper()
        cell_id = str(row.get('cell_id') or '').strip()

        is_compliant = check_kpi_compliance(kpi_def, val_dec)

        entries_to_create.append(
            NetworkKPIEntry(
                kpi=kpi_def,
                period_date=p_date,
                granularity=granularity if granularity in dict(NetworkKPIEntry.Granularity.choices) else 'DAILY',
                region=region,
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
        # Upsert entries
        for entry in entries_to_create:
            NetworkKPIEntry.objects.update_or_create(
                kpi=entry.kpi,
                period_date=entry.period_date,
                granularity=entry.granularity,
                region=entry.region,
                cell_id=entry.cell_id,
                defaults={
                    'value': entry.value,
                    'is_compliant': entry.is_compliant,
                    'source': entry.source,
                    'import_log': entry.import_log,
                    'notes': entry.notes,
                }
            )

    import_log.record_count = success_count
    import_log.error_count = len(errors)
    import_log.status = 'COMPLETED' if not errors else ('PARTIAL' if success_count > 0 else 'FAILED')
    import_log.errors_json = errors[:50]  # Store top 50 errors
    import_log.save()

    return {
        'success': True,
        'import_log_id': import_log.pk,
        'record_count': success_count,
        'error_count': len(errors),
        'errors': errors,
    }


def import_kpi_file(file_obj, filename: str, channel: str = 'CSV_IMPORT', user=None) -> dict:
    """Parse CSV, ZIP, or TAR archive containing KPI data files."""
    fn_lower = filename.lower()
    all_rows = []

    if fn_lower.endswith('.zip'):
        with zipfile.ZipFile(file_obj, 'r') as zf:
            for zip_info in zf.infolist():
                if not zip_info.is_dir() and zip_info.filename.lower().endswith('.csv'):
                    content = zf.read(zip_info).decode('utf-8-sig', errors='replace')
                    reader = csv.DictReader(io.StringIO(content))
                    all_rows.extend(list(reader))

    elif fn_lower.endswith(('.tar', '.tar.gz', '.tgz')):
        with tarfile.open(fileobj=file_obj, mode='r:*') as tf:
            for member in tf.getmembers():
                if member.isfile() and member.name.lower().endswith('.csv'):
                    f = tf.extractfile(member)
                    if f:
                        content = f.read().decode('utf-8-sig', errors='replace')
                        reader = csv.DictReader(io.StringIO(content))
                        all_rows.extend(list(reader))

    else:
        # Standard CSV / Text file
        if hasattr(file_obj, 'read'):
            content = file_obj.read()
            if isinstance(content, bytes):
                content = content.decode('utf-8-sig', errors='replace')
        else:
            content = str(file_obj)

        reader = csv.DictReader(io.StringIO(content))
        all_rows.extend(list(reader))

    return process_kpi_rows(all_rows, filename=filename, channel=channel, user=user)


def compute_qos_compliance_score(period_date: date, region: str = 'NATIONAL') -> Decimal:
    """Compute overall QoS Compliance Score (% of non-composite KPIs meeting threshold)."""
    from ..models import NetworkKPIEntry, NetworkKPIDefinition

    entries = NetworkKPIEntry.objects.filter(
        period_date=period_date,
        granularity='DAILY',
        region=region,
    ).exclude(kpi__code='QOS_SCORE')

    if not entries.exists():
        return Decimal('100.00')

    compliant_count = entries.filter(is_compliant=True).count()
    total_count = entries.count()
    score = (Decimal(compliant_count) / Decimal(total_count)) * Decimal('100.00')
    score = score.quantize(Decimal('0.01'))

    # Update or create the QOS_SCORE entry
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
        region=region,
        cell_id='',
        defaults={
            'value': score,
            'is_compliant': score >= qos_def.natca_threshold,
            'source': 'MANUAL',
            'notes': f'Computed from {total_count} KPI measurements ({compliant_count} compliant)',
        }
    )
    return score
