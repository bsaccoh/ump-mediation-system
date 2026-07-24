"""Interconnect reports.

Each helper returns ``list[dict]`` so the same payload feeds Chart.js (UI)
and CSV export (``views.reports_export``).

* :func:`traffic_by_partner` — per-partner voice-min / SMS / data-MB totals
* :func:`revenue_trend` — invoice totals bucketed by month
* :func:`top_destinations` — top N (service × destination) buckets for one partner
* :func:`ageing` — outstanding invoices in 0-30/31-60/61-90/90+ day buckets
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Optional

from django.db.models import Sum, Count, Q

from ..models import (
    Invoice, InvoiceLine, BillingCycle, Settlement, InterconnectPartner,
)


def _parse_date(value, default: Optional[date] = None) -> Optional[date]:
    if not value:
        return default
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# 1. Traffic by partner
# ---------------------------------------------------------------------------

def traffic_by_partner(start=None, end=None) -> List[Dict]:
    """Aggregate cycle-level traffic per partner within ``[start, end]``.

    Pulls from ``BillingCycle.our_*`` counters so it reflects rated traffic,
    and returns all active partners (with zero values if no traffic) to give
    a complete overview.
    """
    start = _parse_date(start)
    end = _parse_date(end)
    
    # 1. Fetch all active partners (excluding home operator)
    partners = InterconnectPartner.objects.filter(is_active=True).exclude(is_home=True)
    
    # 2. Fetch billing cycle aggregates grouped by partner code
    qs = BillingCycle.objects.select_related('partner')
    if start:
        qs = qs.filter(period_end__gte=start)
    if end:
        qs = qs.filter(period_start__lte=end)
        
    agg = (qs.values('partner__code')
             .annotate(voice_minutes=Sum('our_voice_minutes'),
                        voice_calls=Sum('our_voice_calls'),
                        sms=Sum('our_sms'),
                        data_mb=Sum('our_data_mb')))
                        
    # Map aggregate by partner code
    agg_map = {r['partner__code']: r for r in agg}
    
    rows = []
    for p in partners:
        a = agg_map.get(p.code, {})
        rows.append({
            'partner': p.code,
            'partner_name': p.name,
            'voice_minutes': float(a.get('voice_minutes') or 0),
            'voice_calls': int(a.get('voice_calls') or 0),
            'sms': int(a.get('sms') or 0),
            'data_mb': float(a.get('data_mb') or 0),
        })
        
    # 3. Sort so that partners with traffic come first, preserving default order for the rest
    rows.sort(key=lambda r: (r['voice_minutes'] == 0, -r['voice_minutes'], -r['sms']))
    return rows


# ---------------------------------------------------------------------------
# 2. Revenue trend (per month)
# ---------------------------------------------------------------------------

def revenue_trend(start=None, end=None, granularity='month') -> List[Dict]:
    """Monthly invoice totals split INBOUND vs OUTBOUND.

    ``granularity`` reserved for future ('week' / 'day').  Default month.
    """
    start = _parse_date(start)
    end = _parse_date(end)
    qs = Invoice.objects.exclude(status=Invoice.Status.VOID)
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if end:
        qs = qs.filter(created_at__date__lte=end)

    buckets: Dict[str, Dict[str, Decimal]] = {}
    for inv in qs.iterator():
        d = inv.billing_cycle.period_end
        key = d.strftime('%Y-%m')
        b = buckets.setdefault(key, {
            'INBOUND': Decimal('0'), 'OUTBOUND': Decimal('0'),
            'INBOUND_COUNT': 0, 'OUTBOUND_COUNT': 0,
        })
        b[inv.direction] += inv.total_local or inv.total
        b[f'{inv.direction}_COUNT'] += 1

    rows = []
    for k in sorted(buckets.keys()):
        b = buckets[k]
        rows.append({
            'month': k,
            'inbound': float(b['INBOUND']),
            'outbound': float(b['OUTBOUND']),
            'net': float(b['INBOUND'] - b['OUTBOUND']),
            'inbound_count': b['INBOUND_COUNT'],
            'outbound_count': b['OUTBOUND_COUNT'],
        })
    return rows


# ---------------------------------------------------------------------------
# 3. Top destinations
# ---------------------------------------------------------------------------

def top_destinations(partner=None, start=None, end=None, limit=20) -> List[Dict]:
    """Top N (service × destination) buckets by volume.

    If ``partner`` provided, filtered to that partner; else cross-partner top.
    """
    start = _parse_date(start)
    end = _parse_date(end)
    qs = InvoiceLine.objects.select_related('invoice', 'invoice__partner')
    if partner:
        qs = qs.filter(Q(invoice__partner_id=partner) | Q(invoice__partner__code=partner))
    if start:
        qs = qs.filter(invoice__billing_cycle__period_end__gte=start)
    if end:
        qs = qs.filter(invoice__billing_cycle__period_start__lte=end)

    agg = (qs.values('invoice__partner__code', 'invoice__partner__name',
                      'service_type', 'destination_type')
             .annotate(volume=Sum('volume'),
                        events=Sum('event_count'),
                        amount=Sum('amount'))
             .order_by('-volume')[:limit])

    return [{
        'partner': r['invoice__partner__code'],
        'partner_name': r['invoice__partner__name'],
        'service_type': r['service_type'],
        'destination_type': r['destination_type'],
        'volume': float(r['volume'] or 0),
        'events': int(r['events'] or 0),
        'amount': float(r['amount'] or 0),
    } for r in agg]


# ---------------------------------------------------------------------------
# 4. Ageing — outstanding invoices by bucket
# ---------------------------------------------------------------------------

AGEING_BUCKETS = [
    ('0-30',  0,  30),
    ('31-60', 31, 60),
    ('61-90', 61, 90),
    ('90+',   91, 10_000),
]


def ageing(as_of=None) -> List[Dict]:
    """Outstanding invoices grouped into 0-30 / 31-60 / 61-90 / 90+ day buckets.

    Each invoice is bucketed by ``(as_of - due_date).days``.  Returns one
    row per invoice (rather than aggregated) so the UI can drill in.
    """
    as_of = _parse_date(as_of, date.today())
    qs = (Invoice.objects.select_related('partner', 'billing_cycle')
          .exclude(status__in=[Invoice.Status.PAID, Invoice.Status.VOID]))

    rows = []
    for inv in qs.iterator():
        outstanding = inv.amount_outstanding
        if outstanding <= 0:
            continue
        due = inv.due_date or inv.billing_cycle.period_end
        days_overdue = (as_of - due).days
        bucket = '0-30'
        for name, lo, hi in AGEING_BUCKETS:
            if lo <= days_overdue <= hi:
                bucket = name
                break
        rows.append({
            'invoice_id': inv.pk,
            'invoice_number': inv.invoice_number,
            'partner': inv.partner.code,
            'partner_name': inv.partner.name,
            'direction': inv.direction,
            'total': float(inv.total),
            'paid': float(inv.amount_paid),
            'outstanding': float(outstanding),
            'currency': inv.currency,
            'due_date': due.isoformat(),
            'days_overdue': days_overdue,
            'bucket': bucket,
            'status': inv.status,
        })
    rows.sort(key=lambda r: -r['days_overdue'])
    return rows
