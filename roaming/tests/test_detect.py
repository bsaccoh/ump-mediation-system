"""Unit tests for ``roaming.engines.detect``."""
from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase

from interconnect.models import InterconnectPartner
from roaming.engines.detect import (
    detect_inbound_roamers, attribute_to_partner, _prefix, HOME_MCC,
)

from interconnect.tests._fixtures import make_msc_record


class PrefixHelperTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def test_extracts_mcc_mnc(self):
        self.assertEqual(_prefix('61101234567890'), ('611', '01'))

    def test_short_imsi_returns_none(self):
        self.assertIsNone(_prefix('123'))

    def test_empty_returns_none(self):
        self.assertIsNone(_prefix(''))
        self.assertIsNone(_prefix(None))


class AttributionTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        # Exact MCC+MNC partner
        InterconnectPartner.objects.create(
            code='LCSL', name='Lonestar Liberia',
            mcc='611', mnc='01', is_roaming_partner=True, is_active=True,
        )
        # Country-wide partner (blank MNC)
        InterconnectPartner.objects.create(
            code='GHC', name='Generic Ghana',
            mcc='620', mnc='', is_roaming_partner=True, is_active=True,
        )
        # Inactive partner — should NOT match
        InterconnectPartner.objects.create(
            code='DEAD', name='Inactive', mcc='999', mnc='99',
            is_roaming_partner=True, is_active=False,
        )

    def test_exact_match(self):
        p = attribute_to_partner('611', '01')
        self.assertEqual(p.code, 'LCSL')

    def test_country_fallback(self):
        # No exact (620, 02) match, falls back to country-wide (620, '')
        p = attribute_to_partner('620', '02')
        self.assertEqual(p.code, 'GHC')

    def test_no_match_returns_none(self):
        self.assertIsNone(attribute_to_partner('234', '15'))

    def test_inactive_ignored(self):
        self.assertIsNone(attribute_to_partner('999', '99'))

    def test_non_roaming_partner_ignored(self):
        InterconnectPartner.objects.create(
            code='IC', name='Plain interconnect',
            mcc='888', mnc='01', is_roaming_partner=False, is_active=True,
        )
        self.assertIsNone(attribute_to_partner('888', '01'))


class DetectionTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        InterconnectPartner.objects.create(
            code='LCSL', name='Lonestar Liberia',
            mcc='611', mnc='01', is_roaming_partner=True, is_active=True,
        )

    def test_home_imsis_excluded(self):
        # Home (619, 03) IMSI on a record — should NOT count
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi='61903123456789', duration=60,
                         start_time=datetime(2026, 3, 17, 10, 0))
        rows = detect_inbound_roamers(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(rows, [])

    def test_foreign_imsi_detected_and_attributed(self):
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi='61101123456789', duration=120,
                         start_time=datetime(2026, 3, 17, 10, 0))
        rows = detect_inbound_roamers(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['mcc'], '611')
        self.assertEqual(rows[0]['mnc'], '01')
        self.assertEqual(rows[0]['partner_code'], 'LCSL')
        self.assertEqual(rows[0]['record_count'], 1)
        self.assertEqual(rows[0]['voice_minutes'], 2.0)  # 120s / 60

    def test_multiple_prefixes_aggregated_separately(self):
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi='61101111111111', duration=60,
                         start_time=datetime(2026, 3, 17, 10, 0))
        make_msc_record('MTC', '23276222222', '23277000000',
                         imsi='61002222222222', duration=120,
                         start_time=datetime(2026, 3, 17, 11, 0))
        rows = detect_inbound_roamers(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(len(rows), 2)
        plmns = {r['plmn'] for r in rows}
        self.assertEqual(plmns, {'61101', '61002'})

    def test_sms_counted_separately_from_voice(self):
        make_msc_record('SMSMO', '23276111111', '23277000000',
                         imsi='61101111111111', duration=0,
                         start_time=datetime(2026, 3, 17, 10, 0))
        rows = detect_inbound_roamers(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(rows[0]['sms_count'], 1)
        self.assertEqual(rows[0]['voice_minutes'], 0.0)

    def test_out_of_window_records_ignored(self):
        make_msc_record('MOC', '23276111111', '23277000000',
                         imsi='61101111111111', duration=60,
                         start_time=datetime(2025, 1, 1, 10, 0))  # before window
        rows = detect_inbound_roamers(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(rows, [])

    def test_sample_imsis_capped_at_5(self):
        for i in range(10):
            make_msc_record('MOC', '23276111111', '23277000000',
                             imsi=f'6110100000000{i}', duration=60,
                             start_time=datetime(2026, 3, 17, 10, i))
        rows = detect_inbound_roamers(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(rows[0]['record_count'], 10)
        self.assertLessEqual(len(rows[0]['sample_imsis']), 5)

    def test_empty_window_returns_empty_list(self):
        rows = detect_inbound_roamers(date(2030, 1, 1), date(2030, 1, 31))
        self.assertEqual(rows, [])
