"""Unit tests for ``roaming.engines.generate``.

* generate_roaming_file produces correct aggregates + total
* All inbound-roamer traffic is rated INBOUND (regardless of MOC/MTC) —
  this is the Day-2 bug fix
* File number format CDR-{code}-{YYYYMM}-IN with -N suffix on dup
* SHA-256 matches written bytes
* Cycle aggregates persisted, status flips to INVOICED
* Roaming-rate preferred over non-roaming rate when both exist
"""
import hashlib
from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase

from interconnect.models import (
    InterconnectPartner, InterconnectRate, BillingCycle,
)
from roaming.engines.generate import generate_roaming_file
from roaming.models import RoamingFile

from interconnect.tests._fixtures import make_msc_record


def _seed_partner_and_cycle(mcc='611', mnc='01', currency='USD',
                              voice_rate='0.30', sms_rate='0.10'):
    partner = InterconnectPartner.objects.create(
        code='LCSL', name='Lonestar Liberia', country='Liberia',
        mcc=mcc, mnc=mnc, is_roaming_partner=True, is_active=True,
        default_currency=currency,
    )
    InterconnectRate.objects.create(
        partner=partner, direction='INBOUND', service_type='VOICE',
        destination_type='INTERNATIONAL', time_of_day='ANY',
        unit='PER_MINUTE', rate=Decimal(voice_rate),
        currency=currency, effective_from=date(2025, 1, 1),
        is_roaming=True, is_active=True,
    )
    InterconnectRate.objects.create(
        partner=partner, direction='INBOUND', service_type='SMS',
        destination_type='INTERNATIONAL', time_of_day='ANY',
        unit='PER_SMS', rate=Decimal(sms_rate),
        currency=currency, effective_from=date(2025, 1, 1),
        is_roaming=True, is_active=True,
    )
    cycle = BillingCycle.objects.create(
        partner=partner, period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31), status='OPEN', is_roaming=True,
    )
    return partner, cycle


class GenerateRoamingFileTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        self.partner, self.cycle = _seed_partner_and_cycle()
        self.foreign_imsi = '61101123456789'  # MCC 611 MNC 01

    def test_basic_aggregation(self):
        # 1 MOC 60s + 1 MTC 120s = 3 min voice; 2 SMS
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=60,
                         start_time=datetime(2026, 3, 17, 10, 0))
        make_msc_record('MTC', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=120,
                         start_time=datetime(2026, 3, 17, 11, 0))
        make_msc_record('SMSMO', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=0,
                         start_time=datetime(2026, 3, 17, 12, 0))
        make_msc_record('SMSMT', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=0,
                         start_time=datetime(2026, 3, 17, 13, 0))

        rfile = generate_roaming_file(self.cycle)
        self.assertEqual(rfile.record_count, 4)
        self.assertEqual(rfile.voice_minutes, Decimal('3.000'))
        self.assertEqual(rfile.sms_count, 2)
        # 3 min × 0.30 + 2 sms × 0.10 = 0.90 + 0.20 = 1.10
        self.assertEqual(rfile.total_amount, Decimal('1.10'))
        self.assertEqual(rfile.currency, 'USD')

    def test_inbound_roamer_mtc_uses_inbound_rate(self):
        """Sprint-1 regression: MTC must be rated INBOUND (not OUTBOUND
        which has no roaming rate for this partner)."""
        make_msc_record('MTC', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=300,  # 5 min
                         start_time=datetime(2026, 3, 17, 10, 0))
        rfile = generate_roaming_file(self.cycle)
        # 5 min × 0.30 = 1.50 — would be 0.00 if MTC mapped to OUTBOUND
        self.assertEqual(rfile.total_amount, Decimal('1.50'))

    def test_home_imsis_excluded(self):
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi='61903123456789',  # home
                         duration=60, start_time=datetime(2026, 3, 17, 10, 0))
        # Foreign IMSI but wrong country
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi='61002123456789',  # Gambia — not Liberia
                         duration=60, start_time=datetime(2026, 3, 17, 11, 0))
        # Correct partner
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=60,
                         start_time=datetime(2026, 3, 17, 12, 0))
        rfile = generate_roaming_file(self.cycle)
        self.assertEqual(rfile.record_count, 1)  # Only the LCSL one

    def test_file_number_format(self):
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=60,
                         start_time=datetime(2026, 3, 17, 10, 0))
        rfile = generate_roaming_file(self.cycle)
        self.assertEqual(rfile.file_number, 'CDR-LCSL-202603-IN')

    def test_file_number_dedup_suffix(self):
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=60,
                         start_time=datetime(2026, 3, 17, 10, 0))
        rf1 = generate_roaming_file(self.cycle)
        rf2 = generate_roaming_file(self.cycle)
        self.assertEqual(rf1.file_number, 'CDR-LCSL-202603-IN')
        self.assertEqual(rf2.file_number, 'CDR-LCSL-202603-IN-2')

    def test_sha256_matches_file_bytes(self):
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=60,
                         start_time=datetime(2026, 3, 17, 10, 0))
        rfile = generate_roaming_file(self.cycle)
        body = rfile.csv_file.read()
        rfile.csv_file.close()
        self.assertEqual(rfile.sha256, hashlib.sha256(body).hexdigest())

    def test_cycle_aggregates_persisted(self):
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=180,
                         start_time=datetime(2026, 3, 17, 10, 0))
        make_msc_record('SMSMO', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=0,
                         start_time=datetime(2026, 3, 17, 11, 0))
        generate_roaming_file(self.cycle)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.our_voice_minutes, Decimal('3.000'))
        self.assertEqual(self.cycle.our_sms, 1)
        self.assertEqual(self.cycle.status, 'INVOICED')

    def test_csv_contains_header_block(self):
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi=self.foreign_imsi, duration=60,
                         start_time=datetime(2026, 3, 17, 10, 0))
        rfile = generate_roaming_file(self.cycle)
        body = rfile.csv_file.read().decode('utf-8')
        rfile.csv_file.close()
        self.assertIn('# Roaming settlement file', body)
        self.assertIn('# Partner:  LCSL', body)
        self.assertIn('# Total records: 1', body)

    def test_non_roaming_cycle_raises(self):
        self.cycle.is_roaming = False
        self.cycle.save()
        with self.assertRaises(ValueError):
            generate_roaming_file(self.cycle)

    def test_non_roaming_partner_raises(self):
        self.partner.is_roaming_partner = False
        self.partner.save()
        with self.assertRaises(ValueError):
            generate_roaming_file(self.cycle)

    def test_partner_without_mcc_raises(self):
        self.partner.mcc = ''
        self.partner.save()
        with self.assertRaises(ValueError):
            generate_roaming_file(self.cycle)


class RoamingRatePreferenceTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    """When both a roaming and a non-roaming rate exist, the roaming one
    should be preferred."""

    def test_roaming_rate_wins_when_both_present(self):
        partner = InterconnectPartner.objects.create(
            code='LCSL', name='LCSL', mcc='611', mnc='01',
            is_roaming_partner=True, is_active=True,
            default_currency='USD',
        )
        # Non-roaming rate at 0.10
        InterconnectRate.objects.create(
            partner=partner, direction='INBOUND', service_type='VOICE',
            destination_type='INTERNATIONAL', time_of_day='ANY',
            unit='PER_MINUTE', rate=Decimal('0.10'),
            currency='USD', effective_from=date(2025, 1, 1),
            is_roaming=False, is_active=True,
        )
        # Roaming rate at 0.50 — should win
        InterconnectRate.objects.create(
            partner=partner, direction='INBOUND', service_type='VOICE',
            destination_type='INTERNATIONAL', time_of_day='ANY',
            unit='PER_MINUTE', rate=Decimal('0.50'),
            currency='USD', effective_from=date(2025, 1, 1),
            is_roaming=True, is_active=True,
        )
        cycle = BillingCycle.objects.create(
            partner=partner, period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31), status='OPEN', is_roaming=True,
        )
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi='61101111111111', duration=120,
                         start_time=datetime(2026, 3, 17, 10, 0))
        rfile = generate_roaming_file(cycle)
        # 2 min × 0.50 (roaming) = 1.00; NOT 0.20 (non-roaming)
        self.assertEqual(rfile.total_amount, Decimal('1.00'))
