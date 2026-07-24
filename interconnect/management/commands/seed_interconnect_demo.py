"""Seed comprehensive demo data so every Interconnect page shows
something meaningful for a fresh demo / screenshot.

Usage::

    python manage.py seed_interconnect_demo                # add to current state
    python manage.py seed_interconnect_demo --reset        # wipe rates/cycles/invoices/etc first
    python manage.py seed_interconnect_demo --run-rating   # also run the rating engine against real CDRs

What gets seeded
----------------
* Rate cards — INBOUND + OUTBOUND × VOICE/SMS/DATA for all 5 SL partners
  and 3 foreign carriers, varying by destination tier and time-of-day.
* Exchange rates — historical USD/EUR/GBP/SLE chain across 6 months.
* Billing cycles — last 3 months for every active non-home partner.
* Invoices — INBOUND + OUTBOUND for the most recent month per partner,
  with a mix of statuses (DRAFT / ISSUED / SENT / PART_PAID / PAID /
  OVERDUE / DISPUTED).  Lines are auto-generated even when no CDR
  traffic exists so finance can see the full UI.
* Settlements — full payments for PAID invoices, partial for PART_PAID.
* Reconciliation records — sample (service, destination) variance rows
  with mix of MATCHED / OPEN / DISPUTED.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from interconnect.models import (
    InterconnectPartner, InterconnectRate, ExchangeRate, BillingCycle,
    Invoice, InvoiceLine, ReconciliationRecord, Settlement,
)


# ---------------------------------------------------------------------------
# Rate-card matrix
#   (direction, service, dest, tod) → rate per local partner currency
# ---------------------------------------------------------------------------

LOCAL_RATES = [
    # Voice — INBOUND (we charge partner for termination)
    ('INBOUND',  'VOICE', 'LOCAL',         'PEAK',     'PER_MINUTE', '0.12'),
    ('INBOUND',  'VOICE', 'LOCAL',         'OFF_PEAK', 'PER_MINUTE', '0.08'),
    ('INBOUND',  'VOICE', 'LOCAL',         'WEEKEND',  'PER_MINUTE', '0.06'),
    ('INBOUND',  'VOICE', 'LOCAL',         'ANY',      'PER_MINUTE', '0.10'),
    # Voice — OUTBOUND (partner charges us)
    ('OUTBOUND', 'VOICE', 'LOCAL',         'PEAK',     'PER_MINUTE', '0.10'),
    ('OUTBOUND', 'VOICE', 'LOCAL',         'OFF_PEAK', 'PER_MINUTE', '0.07'),
    ('OUTBOUND', 'VOICE', 'LOCAL',         'WEEKEND',  'PER_MINUTE', '0.05'),
    ('OUTBOUND', 'VOICE', 'LOCAL',         'ANY',      'PER_MINUTE', '0.08'),
    # SMS — both directions
    ('INBOUND',  'SMS',   'LOCAL',         'ANY',      'PER_SMS',    '0.05'),
    ('OUTBOUND', 'SMS',   'LOCAL',         'ANY',      'PER_SMS',    '0.04'),
    # Data
    ('INBOUND',  'DATA',  'LOCAL',         'ANY',      'PER_MB',     '0.02'),
    ('OUTBOUND', 'DATA',  'LOCAL',         'ANY',      'PER_MB',     '0.018'),
]

FOREIGN_RATES = [
    ('INBOUND',  'VOICE', 'INTERNATIONAL', 'ANY',      'PER_MINUTE', '0.22'),
    ('OUTBOUND', 'VOICE', 'INTERNATIONAL', 'ANY',      'PER_MINUTE', '0.18'),
    ('INBOUND',  'SMS',   'INTERNATIONAL', 'ANY',      'PER_SMS',    '0.08'),
    ('OUTBOUND', 'SMS',   'INTERNATIONAL', 'ANY',      'PER_SMS',    '0.06'),
    ('INBOUND',  'DATA',  'INTERNATIONAL', 'ANY',      'PER_MB',     '0.04'),
    ('OUTBOUND', 'DATA',  'INTERNATIONAL', 'ANY',      'PER_MB',     '0.035'),
]

# Historical FX rates (effective_date, from, to, rate)
FX_HISTORY = [
    # USD → SLE
    ('2025-12-01', 'USD', 'SLE', '22.10'),
    ('2026-01-01', 'USD', 'SLE', '22.25'),
    ('2026-02-01', 'USD', 'SLE', '22.40'),
    ('2026-03-01', 'USD', 'SLE', '22.55'),
    ('2026-04-01', 'USD', 'SLE', '22.70'),
    ('2026-05-01', 'USD', 'SLE', '22.85'),
    # EUR → SLE
    ('2025-12-01', 'EUR', 'SLE', '24.20'),
    ('2026-01-01', 'EUR', 'SLE', '24.30'),
    ('2026-02-01', 'EUR', 'SLE', '24.40'),
    ('2026-03-01', 'EUR', 'SLE', '24.55'),
    ('2026-04-01', 'EUR', 'SLE', '24.65'),
    ('2026-05-01', 'EUR', 'SLE', '24.75'),
    # GBP → SLE
    ('2025-12-01', 'GBP', 'SLE', '28.30'),
    ('2026-01-01', 'GBP', 'SLE', '28.45'),
    ('2026-02-01', 'GBP', 'SLE', '28.60'),
    ('2026-03-01', 'GBP', 'SLE', '28.75'),
    ('2026-04-01', 'GBP', 'SLE', '28.90'),
    ('2026-05-01', 'GBP', 'SLE', '29.05'),
]

# Synthetic traffic volumes (volume, event_count) per (service, partner-tier)
# used only when no real CDR data exists for the cycle
SYNTHETIC_TRAFFIC = {
    'local': {
        'VOICE': [(5400, 7800), (4200, 6100), (6800, 9500), (3100, 4600)],
        'SMS':   [(45, 45), (120, 120), (80, 80), (200, 200)],
        'DATA':  [(2400, 850), (1800, 620), (3200, 1100)],
    },
    'foreign': {
        'VOICE': [(820, 410), (1100, 540), (610, 320)],
        'SMS':   [(35, 35), (60, 60)],
        'DATA':  [(8500, 220), (12000, 310)],
    },
}


class Command(BaseCommand):
    help = 'Seed comprehensive Interconnect demo data.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Wipe rates/cycles/invoices/recon/settlements first.')
        parser.add_argument('--run-rating', action='store_true',
                            help='Run the rating engine on cycles that overlap real CDRs.')

    @transaction.atomic
    def handle(self, *args, **opts):
        if opts['reset']:
            self._reset()

        rates_added = self._seed_rates()
        fx_added = self._seed_fx_history()
        cycles_added = self._seed_cycles()

        if opts['run_rating']:
            self._run_rating_where_possible()

        invoices_added = self._seed_invoices()
        settlements_added = self._seed_settlements()
        recon_added = self._seed_reconciliations()

        self.stdout.write(self.style.SUCCESS(
            f'\nSeed complete:\n'
            f'  Rates           +{rates_added}  (total {InterconnectRate.objects.count()})\n'
            f'  FX rates        +{fx_added}  (total {ExchangeRate.objects.count()})\n'
            f'  Billing cycles  +{cycles_added}  (total {BillingCycle.objects.count()})\n'
            f'  Invoices        +{invoices_added}  (total {Invoice.objects.count()})\n'
            f'  Settlements     +{settlements_added}  (total {Settlement.objects.count()})\n'
            f'  Reconciliation  +{recon_added}  (total {ReconciliationRecord.objects.count()})\n'
        ))

    # ---------------------------------------------------------------------
    def _reset(self):
        Settlement.objects.all().delete()
        ReconciliationRecord.objects.all().delete()
        InvoiceLine.objects.all().delete()
        Invoice.objects.all().delete()
        BillingCycle.objects.all().delete()
        InterconnectRate.objects.all().delete()
        self.stdout.write('Reset: cleared rates/cycles/invoices/recon/settlements.')

    # ---------------------------------------------------------------------
    def _seed_rates(self) -> int:
        added = 0
        effective = date(2025, 1, 1)
        for partner in InterconnectPartner.objects.filter(is_active=True, is_home=False):
            rates = LOCAL_RATES if partner.is_local else FOREIGN_RATES
            for direction, service, dest, tod, unit, rate in rates:
                _, created = InterconnectRate.objects.update_or_create(
                    partner=partner, direction=direction, service_type=service,
                    destination_type=dest, time_of_day=tod,
                    effective_from=effective,
                    defaults=dict(
                        unit=unit, rate=Decimal(rate),
                        currency=partner.default_currency,
                        min_charge=Decimal('0.001'),
                        setup_fee=Decimal('0') if service == 'DATA' else Decimal('0.01'),
                        is_active=True,
                        notes=f'Demo rate for {partner.code} {direction} {service}',
                    ),
                )
                if created:
                    added += 1
        return added

    # ---------------------------------------------------------------------
    def _seed_fx_history(self) -> int:
        added = 0
        for d, frm, to, rate in FX_HISTORY:
            _, created = ExchangeRate.objects.update_or_create(
                from_currency=frm, to_currency=to,
                effective_date=date.fromisoformat(d),
                defaults=dict(rate=Decimal(rate), source='CENTRAL_BANK',
                              notes='Demo historical rate'),
            )
            if created:
                added += 1
        return added

    # ---------------------------------------------------------------------
    def _seed_cycles(self) -> int:
        added = 0
        today = date.today()
        # Last 3 calendar months, ending in the current month
        months = []
        cursor = date(today.year, today.month, 1)
        for _ in range(3):
            start = cursor
            # Last day of month
            if start.month == 12:
                end = date(start.year, 12, 31)
                cursor = date(start.year + 1, 1, 1)
            else:
                end = date(start.year, start.month + 1, 1) - timedelta(days=1)
                cursor = date(start.year, start.month - 1, 1) if start.month > 1 \
                          else date(start.year - 1, 12, 1)
            months.append((start, end))
        months.reverse()

        for partner in InterconnectPartner.objects.filter(is_active=True, is_home=False):
            for start, end in months:
                _, created = BillingCycle.objects.update_or_create(
                    partner=partner, period_start=start, period_end=end,
                    defaults={'status': BillingCycle.Status.OPEN},
                )
                if created:
                    added += 1
        return added

    # ---------------------------------------------------------------------
    def _run_rating_where_possible(self) -> None:
        from interconnect.engines.rating import apply_rates
        from streams.msc.models import MSCRecord
        if not MSCRecord.objects.exists():
            return
        agg = MSCRecord.objects.aggregate(
            mn=__import__('django.db.models', fromlist=['Min']).Min('start_time'),
            mx=__import__('django.db.models', fromlist=['Max']).Max('start_time'),
        )
        cdr_start, cdr_end = agg['mn'].date(), agg['mx'].date()
        # Run on cycles whose window overlaps the CDR data
        cycles = BillingCycle.objects.filter(
            period_start__lte=cdr_end, period_end__gte=cdr_start,
        )
        self.stdout.write(f'Running rating on {cycles.count()} cycle(s) overlapping CDRs…')
        for c in cycles:
            try:
                r = apply_rates(c)
                self.stdout.write(f'  {c.partner.code} {c.period_start}..{c.period_end}: '
                                  f'{r.summary()["event_total"]} events rated.')
            except Exception as e:  # pragma: no cover
                self.stdout.write(self.style.WARNING(f'  {c}: {e}'))

    # ---------------------------------------------------------------------
    def _seed_invoices(self) -> int:
        """Generate INBOUND + OUTBOUND invoices per cycle, with synthetic
        traffic when the cycle has no rated lines."""
        added = 0
        today = date.today()
        STATUS_CHOICES = [
            (Invoice.Status.DRAFT,    None,  0),
            (Invoice.Status.ISSUED,   -45,   0),
            (Invoice.Status.SENT,     -30,   0),
            (Invoice.Status.PART_PAID,-25, 0.4),
            (Invoice.Status.PAID,     -20, 1.0),
            (Invoice.Status.OVERDUE,  -75,   0),
            (Invoice.Status.DISPUTED, -40,   0),
        ]

        for cycle in BillingCycle.objects.select_related('partner'):
            # Skip cycle if it already has invoices
            if cycle.invoices.exists():
                continue
            partner = cycle.partner

            for direction in ('INBOUND', 'OUTBOUND'):
                # Vary status by cycle age
                cycle_age = (today - cycle.period_end).days
                if cycle_age > 60:
                    status_seed = random.choice([
                        Invoice.Status.PAID, Invoice.Status.OVERDUE,
                        Invoice.Status.DISPUTED,
                    ])
                elif cycle_age > 30:
                    status_seed = random.choice([
                        Invoice.Status.PAID, Invoice.Status.PART_PAID,
                        Invoice.Status.SENT, Invoice.Status.OVERDUE,
                    ])
                elif cycle_age > 0:
                    status_seed = random.choice([
                        Invoice.Status.SENT, Invoice.Status.ISSUED,
                        Invoice.Status.PART_PAID,
                    ])
                else:
                    status_seed = Invoice.Status.DRAFT

                inv = self._make_synthetic_invoice(cycle, direction, status_seed)
                if inv:
                    added += 1
        return added

    def _make_synthetic_invoice(self, cycle, direction, status):
        partner = cycle.partner
        tier = 'local' if partner.is_local else 'foreign'

        # Build lines using synthetic traffic distribution
        applicable = InterconnectRate.objects.filter(
            partner=partner, direction=direction, is_active=True,
            effective_from__lte=cycle.period_end,
        )
        if not applicable.exists():
            return None

        # Pick one rate per service to keep the invoice compact
        per_service_rate = {}
        for rate in applicable.order_by('service_type', '-effective_from'):
            per_service_rate.setdefault(rate.service_type, rate)

        if not per_service_rate:
            return None

        # Generate ascending invoice number
        base = f'INV-{partner.code}-{cycle.period_end.strftime("%Y%m")}-{direction[:3]}'
        seq = 1
        invoice_number = base
        while Invoice.objects.filter(invoice_number=invoice_number).exists():
            seq += 1
            invoice_number = f'{base}-{seq}'

        # FX
        from interconnect.engines.invoicing import _fx_rate
        currency = partner.default_currency
        fx = _fx_rate(currency, cycle.period_end)

        issued_at = None
        if status not in (Invoice.Status.DRAFT,):
            issued_at = timezone.make_aware(
                datetime.combine(cycle.period_end + timedelta(days=2), time(10, 0))
            )
        due_date = cycle.period_end + timedelta(days=30)

        inv = Invoice.objects.create(
            partner=partner, billing_cycle=cycle, direction=direction,
            invoice_number=invoice_number, currency=currency,
            fx_rate_to_local=fx, status=status,
            issued_at=issued_at, due_date=due_date,
        )

        subtotals = {'VOICE': Decimal('0'), 'SMS': Decimal('0'), 'DATA': Decimal('0')}
        for service, rate in per_service_rate.items():
            volumes = SYNTHETIC_TRAFFIC[tier].get(service, [(100, 100)])
            volume_units, events = random.choice(volumes)
            volume = Decimal(str(volume_units))
            unit_rate = rate.rate
            amount = (volume * unit_rate + rate.setup_fee).quantize(Decimal('0.000001'))
            InvoiceLine.objects.create(
                invoice=inv, rate=rate,
                service_type=service,
                destination_type=rate.destination_type,
                time_of_day=rate.time_of_day,
                volume=volume, event_count=events,
                unit=rate.unit, unit_rate=unit_rate,
                amount=amount, currency=currency,
                description=f'{service} {rate.destination_type} ({events} events)',
            )
            if service in subtotals:
                subtotals[service] += amount

        total = sum(subtotals.values())
        inv.subtotal_voice = subtotals['VOICE']
        inv.subtotal_sms = subtotals['SMS']
        inv.subtotal_data = subtotals['DATA']
        inv.total = total
        inv.total_local = total * fx
        inv.save()
        return inv

    # ---------------------------------------------------------------------
    def _seed_settlements(self) -> int:
        added = 0
        for inv in Invoice.objects.filter(status__in=[
            Invoice.Status.PAID, Invoice.Status.PART_PAID,
        ]).select_related('partner', 'billing_cycle'):
            if inv.settlements.exists():
                continue
            target = inv.total if inv.status == Invoice.Status.PAID else (
                inv.total * Decimal('0.4')
            )
            # Maybe split into two payments
            if random.random() < 0.4 and target > Decimal('100'):
                amt1 = (target * Decimal('0.6')).quantize(Decimal('0.01'))
                amt2 = (target - amt1).quantize(Decimal('0.01'))
                amounts = [amt1, amt2]
            else:
                amounts = [target.quantize(Decimal('0.01'))]
            for i, amount in enumerate(amounts):
                d = inv.due_date - timedelta(days=random.randint(0, 14)) if inv.due_date else date.today()
                Settlement.objects.create(
                    invoice=inv, amount=amount,
                    currency=inv.currency,
                    fx_rate_to_local=inv.fx_rate_to_local,
                    amount_local=(amount * inv.fx_rate_to_local).quantize(Decimal('0.01')),
                    payment_date=d,
                    payment_method=random.choice(['WIRE', 'SWIFT', 'MPESA']),
                    payment_reference=f'REF-{inv.invoice_number}-{i + 1}',
                    notes='Demo payment',
                )
                added += 1
        return added

    # ---------------------------------------------------------------------
    def _seed_reconciliations(self) -> int:
        added = 0
        for cycle in BillingCycle.objects.exclude(invoices__isnull=True).distinct():
            if cycle.reconciliations.exists():
                continue
            # Pull our-side totals from posted invoices
            for line in InvoiceLine.objects.filter(
                invoice__billing_cycle=cycle,
                invoice__direction='INBOUND',
            ):
                # Introduce 0–8 % random variance for realism
                variance = Decimal(str(random.uniform(-0.08, 0.08)))
                partner_vol = (line.volume * (Decimal('1') + variance)).quantize(Decimal('0.001'))
                partner_amt = (line.amount * (Decimal('1') + variance)).quantize(Decimal('0.000001'))
                var_vol = partner_vol - line.volume
                var_amt = partner_amt - line.amount
                pct = ((var_vol / line.volume) * Decimal('100')).quantize(Decimal('0.01')) if line.volume else Decimal('0')
                status = (
                    ReconciliationRecord.Status.MATCHED if abs(pct) < Decimal('1.00')
                    else random.choice([
                        ReconciliationRecord.Status.OPEN,
                        ReconciliationRecord.Status.DISPUTED,
                    ])
                )
                ReconciliationRecord.objects.update_or_create(
                    billing_cycle=cycle,
                    service_type=line.service_type,
                    destination_type=line.destination_type,
                    defaults=dict(
                        partner=cycle.partner,
                        our_volume=line.volume, our_amount=line.amount,
                        partner_volume=partner_vol, partner_amount=partner_amt,
                        variance_volume=var_vol, variance_amount=var_amt,
                        variance_pct=pct, status=status,
                        partner_file_ref=f'demo-{cycle.partner.code}-{cycle.period_end:%Y%m}.csv',
                        resolution_notes='Auto-seeded for demo.',
                    ),
                )
                added += 1
        return added
