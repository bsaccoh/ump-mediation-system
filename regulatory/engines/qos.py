"""QoS/KPI engine.

Day 1 ships a working ``compute_daily_qos(date)`` so the seed migration can
back-fill 30 days.  Day 3 adds monthly rollup + chart payload helpers.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Avg, Q
from django.utils import timezone


# Result-code interpretation — MSCRecord stores decoded string values
# (Huawei MSC convention).  Map to the three high-level outcomes.
SUCCESS_CODES = {
    'normalRelease', 'callPartiallyAccepted', 'unsuccessfulCallAttempt',
    # Numeric codes kept for forward-compat in case the decoder ever emits ints.
    0, 16,
}
DROP_CODES = {
    'stableCallAbnormalTermination',
    # Numeric equivalents
    41, 42, 47,
}


def compute_daily_qos(target: date):
    """Recompute the QoSMetric row for one day from MSC CDRs.

    Returns the upserted ``QoSMetric``.  Safe to call repeatedly.
    """
    # Lazy imports keep apps loadable when streams app is not installed
    from streams.msc.models import MSCRecord
    from ..models import QoSMetric

    start_dt = datetime.combine(target, time.min)
    end_dt = datetime.combine(target + timedelta(days=1), time.min)

    # We only care about call records (MOC + MTC); SMS records use the same
    # `result_code` field but typically a different code space.
    qs = MSCRecord.objects.filter(
        start_time__gte=start_dt, start_time__lt=end_dt,
        record_type__in=['MOC', 'MTC'],
    )

    agg = qs.aggregate(
        total=Count('id'),
        success=Count('id', filter=Q(result_code__in=SUCCESS_CODES)),
        dropped=Count('id', filter=Q(result_code__in=DROP_CODES)),
        avg_duration=Avg('duration'),
    )
    total = agg['total'] or 0
    success = agg['success'] or 0
    dropped = agg['dropped'] or 0
    failed = max(total - success - dropped, 0)
    avg_duration = float(agg['avg_duration'] or 0)

    if total:
        asr = (Decimal(success) / Decimal(total)) * Decimal('100')
        drop = (Decimal(dropped) / Decimal(total)) * Decimal('100')
    else:
        asr = Decimal('0')
        drop = Decimal('0')

    # Availability heuristic: 100% if any traffic flowed that day, else 0%.
    # A real network-availability metric would come from PMs / OSS.
    availability = Decimal('100.00') if total else Decimal('0.00')

    obj, _ = QoSMetric.objects.update_or_create(
        metric_date=target, granularity='DAILY',
        defaults=dict(
            total_calls=total,
            successful_calls=success,
            dropped_calls=dropped,
            failed_calls=failed,
            asr_pct=asr.quantize(Decimal('0.01')),
            acd_seconds=Decimal(str(avg_duration)).quantize(Decimal('0.01')),
            drop_rate_pct=drop.quantize(Decimal('0.01')),
            availability_pct=availability,
            source='COMPUTED',
        ),
    )
    return obj


def compute_monthly_qos(year: int, month: int):
    """Aggregate daily metrics into a monthly summary row."""
    from ..models import QoSMetric
    qs = QoSMetric.objects.filter(
        granularity='DAILY',
        metric_date__year=year, metric_date__month=month,
    )
    total = sum(m.total_calls for m in qs)
    success = sum(m.successful_calls for m in qs)
    dropped = sum(m.dropped_calls for m in qs)
    failed = sum(m.failed_calls for m in qs)
    if total:
        asr = (Decimal(success) / Decimal(total)) * Decimal('100')
        drop = (Decimal(dropped) / Decimal(total)) * Decimal('100')
    else:
        asr = Decimal('0')
        drop = Decimal('0')
    # ACD = sum(success_calls * day_acd) / sum(success_calls), simplified to mean of daily ACDs
    valid_days = [m for m in qs if m.successful_calls > 0]
    acd = (sum(float(m.acd_seconds) for m in valid_days) / max(len(valid_days), 1)) if valid_days else 0.0
    avail_days = [float(m.availability_pct) for m in qs]
    availability = (sum(avail_days) / max(len(avail_days), 1)) if avail_days else Decimal('0')

    obj, _ = QoSMetric.objects.update_or_create(
        metric_date=date(year, month, 1), granularity='MONTHLY',
        defaults=dict(
            total_calls=total, successful_calls=success,
            dropped_calls=dropped, failed_calls=failed,
            asr_pct=asr.quantize(Decimal('0.01')),
            acd_seconds=Decimal(str(acd)).quantize(Decimal('0.01')),
            drop_rate_pct=drop.quantize(Decimal('0.01')),
            availability_pct=Decimal(str(availability)).quantize(Decimal('0.01')),
            source='COMPUTED',
        ),
    )
    return obj


def qos_chart_data(start: date | None = None, end: date | None = None) -> dict:
    """Return Chart.js-ready time-series payload for the daily metrics."""
    from ..models import QoSMetric
    qs = QoSMetric.objects.filter(granularity='DAILY').order_by('metric_date')
    if start:
        qs = qs.filter(metric_date__gte=start)
    if end:
        qs = qs.filter(metric_date__lte=end)
    labels = [m.metric_date.isoformat() for m in qs]
    return {
        'labels': labels,
        'asr': [float(m.asr_pct) for m in qs],
        'acd': [float(m.acd_seconds) for m in qs],
        'drop': [float(m.drop_rate_pct) for m in qs],
        'availability': [float(m.availability_pct) for m in qs],
        'total_calls': [m.total_calls for m in qs],
        'successful': [m.successful_calls for m in qs],
        'dropped': [m.dropped_calls for m in qs],
        'failed': [m.failed_calls for m in qs],
    }
