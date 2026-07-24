"""Seed comprehensive roaming demo data so every page shows content.

Usage::

    python manage.py seed_roaming_demo               # additive
    python manage.py seed_roaming_demo --reset       # wipe roaming rows first
    python manage.py seed_roaming_demo --generate    # also run file generator
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from interconnect.models import InterconnectPartner, InterconnectRate, BillingCycle
from roaming.models import RoamingFile, RoamingDispute


# (code, name, country, country_code, mcc, mnc, currency, voice_rate, sms_rate)
PARTNERS = [
    ('LCSL',   'Lonestar Cell SL (Liberia)',  'Liberia',            '231', '611', '01', 'USD', '0.30', '0.10'),
    ('AIRGM',  'Airtel Gambia',               'Gambia',             '220', '610', '02', 'USD', '0.28', '0.10'),
    ('SAFKE',  'Safaricom Kenya',             'Kenya',              '254', '639', '02', 'USD', '0.32', '0.12'),
    ('NEDNL',  'KPN Netherlands',             'Netherlands',        '31',  '204', '08', 'EUR', '0.45', '0.15'),
    ('USVZ',   'Verizon US',                  'United States',      '1',   '310', '26', 'USD', '0.40', '0.15'),
    ('AIRGNG', 'Airtel Nigeria',              'Nigeria',            '234', '621', '20', 'USD', '0.32', '0.11'),
    ('GHALN',  'Generic Ghana',               'Ghana',              '233', '620', '',  'USD', '0.30', '0.10'),
]


class Command(BaseCommand):
    help = 'Seed comprehensive roaming demo data.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Delete all roaming partners + cycles + files + disputes first.')
        parser.add_argument('--generate', action='store_true',
                            help='Run the file generator for each cycle.')

    @transaction.atomic
    def handle(self, *args, **opts):
        if opts['reset']:
            self._reset()

        n_partners = self._seed_partners()
        n_cycles = self._seed_cycles()

        if opts['generate']:
            n_files = self._generate_files()
        else:
            n_files = 0

        n_disputes = self._seed_disputes() if n_files else 0

        self.stdout.write(self.style.SUCCESS(
            f'\nSeed complete:\n'
            f'  Roaming partners  +{n_partners}  (total {InterconnectPartner.objects.filter(is_roaming_partner=True).count()})\n'
            f'  Roaming cycles    +{n_cycles}    (total {BillingCycle.objects.filter(is_roaming=True).count()})\n'
            f'  Files generated   +{n_files}     (total {RoamingFile.objects.count()})\n'
            f'  Disputes          +{n_disputes}  (total {RoamingDispute.objects.count()})\n'
        ))

    def _reset(self):
        RoamingDispute.objects.all().delete()
        RoamingFile.objects.all().delete()
        BillingCycle.objects.filter(is_roaming=True).delete()
        # InterconnectRate roaming flag: only delete the ones we'll re-add
        InterconnectRate.objects.filter(is_roaming=True).delete()
        InterconnectPartner.objects.filter(is_roaming_partner=True).delete()
        self.stdout.write('Reset: cleared roaming partners + rates + cycles + files + disputes.')

    def _seed_partners(self) -> int:
        added = 0
        for code, name, country, cc, mcc, mnc, currency, vr, sr in PARTNERS:
            partner, created = InterconnectPartner.objects.update_or_create(
                code=code,
                defaults=dict(
                    name=name, country=country, country_code=cc,
                    mcc=mcc, mnc=mnc,
                    is_local=False, is_home=False,
                    is_roaming_partner=True, is_active=True,
                    default_currency=currency,
                ),
            )
            if created:
                added += 1
            # Roaming rate cards
            InterconnectRate.objects.update_or_create(
                partner=partner, direction='INBOUND', service_type='VOICE',
                destination_type='INTERNATIONAL', time_of_day='ANY',
                effective_from=date(2025, 1, 1),
                defaults=dict(rate=Decimal(vr), unit='PER_MINUTE',
                              currency=currency, is_roaming=True, is_active=True),
            )
            InterconnectRate.objects.update_or_create(
                partner=partner, direction='INBOUND', service_type='SMS',
                destination_type='INTERNATIONAL', time_of_day='ANY',
                effective_from=date(2025, 1, 1),
                defaults=dict(rate=Decimal(sr), unit='PER_SMS',
                              currency=currency, is_roaming=True, is_active=True),
            )
            InterconnectRate.objects.update_or_create(
                partner=partner, direction='INBOUND', service_type='DATA',
                destination_type='INTERNATIONAL', time_of_day='ANY',
                effective_from=date(2025, 1, 1),
                defaults=dict(rate=Decimal('0.05'), unit='PER_MB',
                              currency=currency, is_roaming=True, is_active=True),
            )
        return added

    def _seed_cycles(self) -> int:
        added = 0
        # March 2026 cycle for each roaming partner
        for code, *_ in PARTNERS:
            partner = InterconnectPartner.objects.get(code=code)
            _, created = BillingCycle.objects.update_or_create(
                partner=partner,
                period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
                defaults={'status': 'OPEN', 'is_roaming': True},
            )
            if created:
                added += 1
        return added

    def _generate_files(self) -> int:
        from roaming.engines.generate import generate_roaming_file
        from django.utils import timezone
        added = 0
        for cyc in BillingCycle.objects.filter(is_roaming=True,
                                                 period_start=date(2026, 3, 1)):
            try:
                rf = generate_roaming_file(cyc)
                # Flip most files to a non-DRAFT status so reports populate
                if added % 3 == 2:
                    rf.status = RoamingFile.Status.SETTLED
                    rf.settled_at = timezone.now()
                elif added % 2 == 0:
                    rf.status = RoamingFile.Status.SENT
                    rf.sent_at = timezone.now()
                else:
                    rf.status = RoamingFile.Status.FINAL
                rf.save(update_fields=['status', 'sent_at', 'settled_at'])
                added += 1
                self.stdout.write(f'  {rf.file_number}: {rf.record_count} records, '
                                  f'{rf.total_amount} {rf.currency} [{rf.status}]')
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'  {cyc.partner.code}: {e}'))
        return added

    def _seed_disputes(self) -> int:
        added = 0
        # Pick the highest-revenue file and log a sample dispute
        rfile = RoamingFile.objects.order_by('-total_amount').first()
        if not rfile:
            return 0
        _, created = RoamingDispute.objects.update_or_create(
            dispute_ref='DISP-2026-DEMO-001',
            defaults=dict(
                roaming_file=rfile,
                raised_by=f'{rfile.partner.code.lower()}.finance@example.com',
                claimed_volume=rfile.voice_minutes * Decimal('0.95'),
                claimed_amount=rfile.total_amount * Decimal('0.92'),
                description=(
                    f'{rfile.partner.name} disputes the voice-minute aggregate; '
                    'claims 8% less than our records show.'
                ),
                status='OPEN',
            ),
        )
        if created:
            added += 1
        return added
