"""Roaming reports.

Each helper returns ``list[dict]`` suitable for Chart.js (UI) and CSV.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.db.models import Sum, Count


def _parse_date(value, default=None):
    if not value:
        return default
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        return default


def top_partners(start=None, end=None) -> list[dict]:
    """Top roaming partners by total amount across files in the window."""
    from ..models import RoamingFile
    start = _parse_date(start)
    end = _parse_date(end)
    qs = RoamingFile.objects.exclude(status='DRAFT')
    if start:
        qs = qs.filter(generated_at__date__gte=start)
    if end:
        qs = qs.filter(generated_at__date__lte=end)
    agg = (qs.values('partner__code', 'partner__name', 'currency')
             .annotate(total_amount=Sum('total_amount'),
                        record_count=Sum('record_count'),
                        voice_minutes=Sum('voice_minutes'),
                        sms_count=Sum('sms_count'),
                        data_mb=Sum('data_mb'),
                        file_count=Count('id'))
             .order_by('-total_amount'))
    return [{
        'partner': r['partner__code'],
        'partner_name': r['partner__name'],
        'currency': r['currency'],
        'file_count': r['file_count'],
        'record_count': int(r['record_count'] or 0),
        'voice_minutes': float(r['voice_minutes'] or 0),
        'sms_count': int(r['sms_count'] or 0),
        'data_mb': float(r['data_mb'] or 0),
        'total_amount': float(r['total_amount'] or 0),
    } for r in agg]


def monthly_trend(start=None, end=None) -> list[dict]:
    """Monthly aggregate of roaming amount + records."""
    from ..models import RoamingFile
    start = _parse_date(start)
    end = _parse_date(end)
    qs = RoamingFile.objects.exclude(status='DRAFT')
    if start:
        qs = qs.filter(billing_cycle__period_end__gte=start)
    if end:
        qs = qs.filter(billing_cycle__period_end__lte=end)

    buckets: dict[str, dict[str, Decimal | int]] = {}
    for f in qs.select_related('billing_cycle').iterator():
        key = f.billing_cycle.period_end.strftime('%Y-%m')
        b = buckets.setdefault(key, {
            'records': 0, 'voice_minutes': Decimal('0'),
            'sms_count': 0, 'data_mb': Decimal('0'),
            'total_amount': Decimal('0'),
        })
        b['records'] += f.record_count
        b['voice_minutes'] += f.voice_minutes
        b['sms_count'] += f.sms_count
        b['data_mb'] += f.data_mb
        b['total_amount'] += f.total_amount

    return [
        {
            'month': k,
            'records': b['records'],
            'voice_minutes': float(b['voice_minutes']),
            'sms_count': b['sms_count'],
            'data_mb': float(b['data_mb']),
            'total_amount': float(b['total_amount']),
        } for k, b in sorted(buckets.items())
    ]


def top_countries(start=None, end=None) -> list[dict]:
    """Aggregate by partner.country (proxy for source country)."""
    from ..models import RoamingFile
    start = _parse_date(start)
    end = _parse_date(end)
    qs = RoamingFile.objects.exclude(status='DRAFT').select_related('partner')
    if start:
        qs = qs.filter(generated_at__date__gte=start)
    if end:
        qs = qs.filter(generated_at__date__lte=end)
    agg = (qs.values('partner__country')
             .annotate(total_amount=Sum('total_amount'),
                        record_count=Sum('record_count'),
                        partners=Count('partner', distinct=True))
             .order_by('-total_amount'))
    return [{
        'country': r['partner__country'] or '(unknown)',
        'partners': r['partners'],
        'record_count': int(r['record_count'] or 0),
        'total_amount': float(r['total_amount'] or 0),
    } for r in agg]


def open_disputes() -> list[dict]:
    """Open / under-review disputes."""
    from ..models import RoamingDispute
    qs = (RoamingDispute.objects
          .filter(status__in=['OPEN', 'UNDER_REVIEW'])
          .select_related('roaming_file', 'roaming_file__partner')
          .order_by('-opened_at'))
    return [{
        'id': d.pk,
        'dispute_ref': d.dispute_ref,
        'file_number': d.roaming_file.file_number,
        'file_id': d.roaming_file_id,
        'partner': d.roaming_file.partner.code,
        'claimed_amount': float(d.claimed_amount),
        'variance_amount': float(d.variance_amount),
        'status': d.status,
        'opened_at': d.opened_at.isoformat(),
        'days_open': (datetime.now().date() - d.opened_at.date()).days,
    } for d in qs]
