"""Levy + USF computation engine.

For one calendar month::

    gross_revenue = interconnect_inbound + retail_total
    levy_amount   = gross_revenue * RegulatoryProfile.levy_pct / 100
    usf_amount    = gross_revenue * RegulatoryProfile.usf_pct  / 100
    total_payable = levy_amount + usf_amount

The OUTBOUND interconnect total is captured for reference but is **not**
added to gross revenue (that's money we pay out, not money we earn).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import AuditLog

from ..models import (
    RegulatoryProfile, RetailRevenue, LeviedPeriod,
)


TWO_PLACES = Decimal('0.01')


def _money(value) -> Decimal:
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _interconnect_totals(year: int, month: int) -> tuple[Decimal, Decimal]:
    """Sum interconnect Invoice.total_local for the month, by direction.

    Filter on the billing-cycle's period_end month so the row aligns with
    the regulatory period regardless of when the invoice was created.
    """
    from interconnect.models import Invoice

    base = (Invoice.objects
            .exclude(status=Invoice.Status.VOID)
            .filter(billing_cycle__period_end__year=year,
                     billing_cycle__period_end__month=month))
    inbound = base.filter(direction='INBOUND').aggregate(
        s=Sum('total_local'))['s'] or Decimal('0')
    outbound = base.filter(direction='OUTBOUND').aggregate(
        s=Sum('total_local'))['s'] or Decimal('0')
    return Decimal(inbound), Decimal(outbound)


def _retail_total(year: int, month: int) -> Decimal:
    try:
        r = RetailRevenue.objects.get(period_year=year, period_month=month)
    except RetailRevenue.DoesNotExist:
        return Decimal('0')
    return Decimal(r.total)


def _due_date(year: int, month: int) -> date:
    """Levy is due by the 15th of the month following the period end."""
    nxt_year, nxt_month = (year, month + 1) if month < 12 else (year + 1, 1)
    return date(nxt_year, nxt_month, 15)


@transaction.atomic
def compute_levy(period_year: int, period_month: int, user=None) -> LeviedPeriod:
    """Compute & upsert LeviedPeriod for the given month."""
    if not (1 <= period_month <= 12):
        raise ValueError(f'Invalid month: {period_month}')

    profile = RegulatoryProfile.get_or_create_default()

    inbound, outbound = _interconnect_totals(period_year, period_month)
    retail = _retail_total(period_year, period_month)
    gross = inbound + retail

    levy_amount = _money(gross * profile.levy_pct / Decimal('100'))
    usf_amount = _money(gross * profile.usf_pct / Decimal('100'))
    total_payable = _money(levy_amount + usf_amount)

    obj, _created = LeviedPeriod.objects.update_or_create(
        period_year=period_year, period_month=period_month,
        defaults=dict(
            interconnect_inbound=_money(inbound),
            interconnect_outbound=_money(outbound),
            retail_total=_money(retail),
            gross_revenue=_money(gross),
            levy_pct=profile.levy_pct,
            usf_pct=profile.usf_pct,
            levy_amount=levy_amount,
            usf_amount=usf_amount,
            total_payable=total_payable,
            currency=profile.home_currency,
            status=LeviedPeriod.Status.COMPUTED,
            due_date=_due_date(period_year, period_month),
            computed_at=timezone.now(),
            computed_by=user if user and user.is_authenticated else None,
        ),
    )

    # Audit
    try:
        AuditLog.objects.create(
            user=user if user and user.is_authenticated else None,
            action='LEVY_COMPUTED',
            entity_type='LeviedPeriod',
            entity_id=str(obj.pk),
            description=(f'Period {period_year}-{period_month:02d}: '
                          f'gross={gross} levy={levy_amount} usf={usf_amount}'),
            extra_data={
                'inbound': str(inbound), 'outbound': str(outbound),
                'retail': str(retail),
            },
        )
    except Exception:
        pass

    return obj


@transaction.atomic
def mark_levy_paid(levy: LeviedPeriod, payment_date=None, reference: str = '', user=None) -> LeviedPeriod:
    levy.status = LeviedPeriod.Status.PAID
    levy.paid_at = timezone.now()
    levy.payment_reference = reference
    levy.save(update_fields=['status', 'paid_at', 'payment_reference', 'updated_at'])

    try:
        AuditLog.objects.create(
            user=user if user and user.is_authenticated else None,
            action='LEVY_PAID',
            entity_type='LeviedPeriod',
            entity_id=str(levy.pk),
            description=(f'Period {levy.period_year}-{levy.period_month:02d} '
                          f'paid {levy.total_payable} {levy.currency} ref={reference}'),
            extra_data={'payment_date': payment_date.isoformat() if payment_date else None},
        )
    except Exception:
        pass

    return levy
