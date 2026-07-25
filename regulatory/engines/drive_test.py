"""Drive Test Processing & Analysis Engine.

Handles multi-vendor drive test file parsing (.csv, .trp, .lpg, .nmf, .zip, .tar.gz),
sample extraction, campaign statistical analysis, and NatCA compliance evaluation.
"""
from __future__ import annotations

import csv
import io
import re
import tarfile
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone


def _dec(val, default=None):
    if val in (None, ''):
        return default
    try:
        return Decimal(str(val).strip())
    except (InvalidOperation, ValueError, TypeError):
        return default


def _bool(val):
    if val in (None, ''):
        return None
    s = str(val).strip().lower()
    if s in ('1', 'true', 'yes', 'pass', 'success', 'ok'):
        return True
    if s in ('0', 'false', 'no', 'fail', 'failure', 'drop'):
        return False
    return None


def parse_drive_test_file(file_obj, filename: str, campaign) -> int:
    """Parse drive test measurement file (.csv, .trp, .lpg, .nmf, .zip, .tar.gz) into DriveTestSample rows.

    Returns count of samples created.
    """
    from ..models import DriveTestSample

    fn_lower = filename.lower()
    raw_samples = []

    if fn_lower.endswith('.zip'):
        with zipfile.ZipFile(file_obj, 'r') as zf:
            for info in zf.infolist():
                if not info.is_dir():
                    content = zf.read(info).decode('utf-8-sig', errors='replace')
                    raw_samples.extend(_parse_content(content, info.filename))

    elif fn_lower.endswith(('.tar', '.tar.gz', '.tgz')):
        with tarfile.open(fileobj=file_obj, mode='r:*') as tf:
            for member in tf.getmembers():
                if member.isfile():
                    f = tf.extractfile(member)
                    if f:
                        content = f.read().decode('utf-8-sig', errors='replace')
                        raw_samples.extend(_parse_content(content, member.name))

    else:
        if hasattr(file_obj, 'read'):
            content = file_obj.read()
            if isinstance(content, bytes):
                content = content.decode('utf-8-sig', errors='replace')
        else:
            content = str(file_obj)

        raw_samples.extend(_parse_content(content, filename))

    # Bulk insert samples
    samples_to_create = []
    for s in raw_samples:
        lat = _dec(s.get('latitude') or s.get('lat'))
        lon = _dec(s.get('longitude') or s.get('lon') or s.get('lng'))
        if lat is None or lon is None:
            continue

        samples_to_create.append(
            DriveTestSample(
                campaign=campaign,
                latitude=lat,
                longitude=lon,
                timestamp=s.get('timestamp'),
                rsrp=_dec(s.get('rsrp')),
                rsrq=_dec(s.get('rsrq')),
                sinr=_dec(s.get('sinr')),
                dl_throughput=_dec(s.get('dl_throughput') or s.get('dl_tp')),
                ul_throughput=_dec(s.get('ul_throughput') or s.get('ul_tp')),
                cssr_status=_bool(s.get('cssr_status') or s.get('cssr')),
                drop_status=_bool(s.get('drop_status') or s.get('drop')),
                handover_status=_bool(s.get('handover_status') or s.get('ho')),
                ping_rtt=_dec(s.get('ping_rtt') or s.get('ping') or s.get('rtt')),
                voice_mos=_dec(s.get('voice_mos') or s.get('mos')),
                technology=str(s.get('technology') or campaign.technology or '4G').strip(),
                cell_id=str(s.get('cell_id') or '').strip(),
                pci=int(s['pci']) if s.get('pci') and str(s['pci']).isdigit() else None,
                earfcn=int(s['earfcn']) if s.get('earfcn') and str(s['earfcn']).isdigit() else None,
            )
        )

    with transaction.atomic():
        # Clear existing samples for this campaign if re-uploading
        DriveTestSample.objects.filter(campaign=campaign).delete()
        DriveTestSample.objects.bulk_create(samples_to_create, batch_size=1000)

    return len(samples_to_create)


def _parse_content(content: str, filename: str) -> list[dict]:
    """Inspect header/format and extract raw sample dictionaries."""
    lines = content.splitlines()
    if not lines:
        return []

    fn_lower = filename.lower()

    # CSV or Tab-delimited (covers TEMS export, Nemo export, SwissQual export, CSV)
    delimiter = '\t' if '\t' in lines[0] else (';' if ';' in lines[0] else ',')
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)

    # Normalize column field names
    rows = []
    for r in reader:
        norm_row = {}
        for k, v in r.items():
            if not k:
                continue
            k_clean = k.strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_')
            norm_row[k_clean] = v

        # Map common vendor aliases
        mapped = {}
        mapped['latitude'] = norm_row.get('latitude') or norm_row.get('lat') or norm_row.get('y')
        mapped['longitude'] = norm_row.get('longitude') or norm_row.get('lon') or norm_row.get('lng') or norm_row.get('x')
        mapped['rsrp'] = norm_row.get('rsrp') or norm_row.get('lte_rsrp') or norm_row.get('nr_rsrp') or norm_row.get('ss_rsrp')
        mapped['rsrq'] = norm_row.get('rsrq') or norm_row.get('lte_rsrq') or norm_row.get('nr_rsrq')
        mapped['sinr'] = norm_row.get('sinr') or norm_row.get('snr') or norm_row.get('lte_sinr') or norm_row.get('cinr')
        mapped['dl_throughput'] = norm_row.get('dl_throughput') or norm_row.get('pdsch_throughput') or norm_row.get('dl_tp_mbps') or norm_row.get('dl_rate')
        mapped['ul_throughput'] = norm_row.get('ul_throughput') or norm_row.get('pusch_throughput') or norm_row.get('ul_tp_mbps') or norm_row.get('ul_rate')
        mapped['cssr_status'] = norm_row.get('cssr_status') or norm_row.get('call_setup_success') or norm_row.get('cssr')
        mapped['drop_status'] = norm_row.get('drop_status') or norm_row.get('call_drop') or norm_row.get('dropped')
        mapped['handover_status'] = norm_row.get('handover_status') or norm_row.get('handover_success') or norm_row.get('hosr')
        mapped['ping_rtt'] = norm_row.get('ping_rtt') or norm_row.get('ping_ms') or norm_row.get('rtt') or norm_row.get('latency')
        mapped['voice_mos'] = norm_row.get('voice_mos') or norm_row.get('polqa_mos') or norm_row.get('pesq_mos') or norm_row.get('mos')
        mapped['technology'] = norm_row.get('technology') or norm_row.get('tech') or norm_row.get('rat')
        mapped['cell_id'] = norm_row.get('cell_id') or norm_row.get('cid') or norm_row.get('cellid')
        mapped['pci'] = norm_row.get('pci') or norm_row.get('physcellid')
        mapped['earfcn'] = norm_row.get('earfcn') or norm_row.get('arfcn')

        if mapped['latitude'] and mapped['longitude']:
            rows.append(mapped)

    return rows


def analyse_campaign(campaign, user=None):
    """Aggregate campaign samples and compare against NatCA regulatory benchmarks."""
    from ..models import DriveTestSample, DriveTestAnalysis

    samples = DriveTestSample.objects.filter(campaign=campaign)
    total_samples = samples.count()

    if total_samples == 0:
        analysis, _ = DriveTestAnalysis.objects.update_or_create(
            campaign=campaign,
            defaults={
                'total_samples': 0,
                'natca_compliant': False,
                'analysed_by': user,
            }
        )
        campaign.status = campaign.Status.ANALYSED
        campaign.save()
        return analysis

    # Compute averages
    def _avg(field):
        vals = [float(getattr(s, field)) for s in samples if getattr(s, field) is not None]
        return Decimal(str(sum(vals) / len(vals))).quantize(Decimal('0.01')) if vals else Decimal('0.00')

    avg_rsrp = _avg('rsrp')
    avg_rsrq = _avg('rsrq')
    avg_sinr = _avg('sinr')
    avg_dl_tp = _avg('dl_throughput')
    avg_ul_tp = _avg('ul_throughput')
    avg_ping = _avg('ping_rtt')
    avg_mos = _avg('voice_mos')

    # CSSR, Drop Rate, Handover Success Rate
    cssr_samples = [s.cssr_status for s in samples if s.cssr_status is not None]
    cssr_pct = (Decimal(sum(1 for x in cssr_samples if x)) / Decimal(len(cssr_samples)) * Decimal('100.00')).quantize(Decimal('0.01')) if cssr_samples else Decimal('100.00')

    drop_samples = [s.drop_status for s in samples if s.drop_status is not None]
    drop_rate_pct = (Decimal(sum(1 for x in drop_samples if x)) / Decimal(len(drop_samples)) * Decimal('100.00')).quantize(Decimal('0.01')) if drop_samples else Decimal('0.00')

    ho_samples = [s.handover_status for s in samples if s.handover_status is not None]
    ho_sr_pct = (Decimal(sum(1 for x in ho_samples if x)) / Decimal(len(ho_samples)) * Decimal('100.00')).quantize(Decimal('0.01')) if ho_samples else Decimal('100.00')

    # Coverage Percentage: RSRP >= -110 dBm is acceptable coverage
    good_coverage = samples.filter(rsrp__gte=Decimal('-110.00')).count()
    coverage_pct = (Decimal(good_coverage) / Decimal(total_samples) * Decimal('100.00')).quantize(Decimal('0.01'))

    # NatCA Compliance Checks:
    # 1. Coverage >= 90%
    # 2. RSRP >= -105 dBm average
    # 3. DL Throughput >= 5 Mbps average
    # 4. CSSR >= 95%
    # 5. Drop Rate <= 2%
    is_compliant = (
        coverage_pct >= Decimal('90.00') and
        avg_rsrp >= Decimal('-105.00') and
        avg_dl_tp >= Decimal('5.00') and
        cssr_pct >= Decimal('95.00') and
        drop_rate_pct <= Decimal('2.00')
    )

    analysis, _ = DriveTestAnalysis.objects.update_or_create(
        campaign=campaign,
        defaults={
            'total_samples': total_samples,
            'coverage_pct': coverage_pct,
            'avg_rsrp': avg_rsrp,
            'avg_rsrq': avg_rsrq,
            'avg_sinr': avg_sinr,
            'avg_dl_throughput': avg_dl_tp,
            'avg_ul_throughput': avg_ul_tp,
            'cssr_pct': cssr_pct,
            'drop_rate_pct': drop_rate_pct,
            'handover_sr_pct': ho_sr_pct,
            'avg_ping_rtt': avg_ping,
            'avg_voice_mos': avg_mos,
            'natca_compliant': is_compliant,
            'analysis_json': {
                'good_coverage_count': good_coverage,
                'bad_coverage_count': total_samples - good_coverage,
            },
            'analysed_by': user,
        }
    )

    campaign.status = campaign.Status.ANALYSED
    campaign.save()

    return analysis
