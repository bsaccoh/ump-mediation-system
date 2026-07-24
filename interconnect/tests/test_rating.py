"""Unit tests for ``interconnect.engines.rating``.

Locks in the Sprint-1 fixes:

* Foreign-partner attribution by ``country_code`` prefix
* ``is_primary_for_country`` flag suppresses double-counting when partners
  share a CC (e.g. VODAUK + BTUK both = 44)
* ``_time_of_day`` peak / off-peak / weekend rules
* Rate-cache + ``.values()`` path produces the same buckets as the
  pre-optimised code (regression guard)
"""
from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase

from interconnect.engines import rating as R
from interconnect.models import InterconnectRate

from ._fixtures import (
    make_partner, make_rate, make_cycle, make_msc_record,
)


# ---------------------------------------------------------------------------
# _time_of_day
# ---------------------------------------------------------------------------

class TimeOfDayTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def test_weekday_peak_window(self):
        # Tuesday 10:00 → PEAK
        self.assertEqual(R._time_of_day(datetime(2026, 3, 17, 10, 0)), 'PEAK')

    def test_weekday_off_peak_evening(self):
        # Tuesday 20:00 → OFF_PEAK
        self.assertEqual(R._time_of_day(datetime(2026, 3, 17, 20, 0)), 'OFF_PEAK')

    def test_weekday_off_peak_early(self):
        # Tuesday 06:00 → OFF_PEAK
        self.assertEqual(R._time_of_day(datetime(2026, 3, 17, 6, 0)), 'OFF_PEAK')

    def test_weekend(self):
        # Saturday 10:00 → WEEKEND (overrides peak window)
        self.assertEqual(R._time_of_day(datetime(2026, 3, 21, 10, 0)), 'WEEKEND')
        # Sunday 22:00 → WEEKEND
        self.assertEqual(R._time_of_day(datetime(2026, 3, 22, 22, 0)), 'WEEKEND')

    def test_none(self):
        self.assertEqual(R._time_of_day(None), 'ANY')


# ---------------------------------------------------------------------------
# rate_record (per-record charge math)
# ---------------------------------------------------------------------------

class RateRecordTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def test_basic_rate_times_volume(self):
        partner = make_partner('TST')
        rate = make_rate(partner, rate='0.10', setup_fee='0', min_charge='0')
        amount = R.rate_record(Decimal('5'), rate)
        self.assertEqual(amount, Decimal('0.500000'))

    def test_setup_fee_added(self):
        partner = make_partner('TST')
        rate = make_rate(partner, rate='0.10', setup_fee='0.01', min_charge='0')
        amount = R.rate_record(Decimal('5'), rate)
        self.assertEqual(amount, Decimal('0.510000'))

    def test_min_charge_floor(self):
        partner = make_partner('TST')
        rate = make_rate(partner, rate='0.10', setup_fee='0', min_charge='1.00')
        # Raw would be 0.10; min_charge wins
        amount = R.rate_record(Decimal('1'), rate)
        self.assertEqual(amount, Decimal('1.00'))


# ---------------------------------------------------------------------------
# Foreign-partner attribution (Sprint-1 fix)
# ---------------------------------------------------------------------------

class ForeignAttributionTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    """Two partners share CC 44 (VODAUK + BTUK).  Only the primary should
    receive uncategorised UK traffic — this is the regression guard for the
    pre-Sprint-1 "every International matches every foreign" bug."""

    def setUp(self):
        self.vodauk = make_partner('VODAUK', is_local=False, country_code='44',
                                    is_primary=True, currency='GBP')
        self.btuk = make_partner('BTUK', is_local=False, country_code='44',
                                  is_primary=False, currency='GBP')
        self.mtngh = make_partner('MTNGH', is_local=False, country_code='233',
                                    is_primary=True, currency='USD')

    def _record(self, called):
        return {
            'record_type': 'MOC',
            'calling_number': '23276111111',
            'called_number': called,
        }

    def test_uk_msisdn_matches_primary_only(self):
        rec = self._record('447712345678')
        self.assertTrue(R._record_belongs_to_partner_dict(rec, self.vodauk))
        self.assertFalse(R._record_belongs_to_partner_dict(rec, self.btuk))

    def test_uk_msisdn_with_double_zero_prefix(self):
        # "00 44 ..." should normalise to "44..." and still match.
        rec = self._record('0044771234567890')  # length-padded
        self.assertTrue(R._record_belongs_to_partner_dict(rec, self.vodauk))
        self.assertFalse(R._record_belongs_to_partner_dict(rec, self.btuk))

    def test_uk_msisdn_with_plus_prefix(self):
        rec = self._record('+447712345678')
        self.assertTrue(R._record_belongs_to_partner_dict(rec, self.vodauk))

    def test_ghana_msisdn_only_matches_ghana_partner(self):
        rec = self._record('233244000000')
        self.assertTrue(R._record_belongs_to_partner_dict(rec, self.mtngh))
        self.assertFalse(R._record_belongs_to_partner_dict(rec, self.vodauk))
        self.assertFalse(R._record_belongs_to_partner_dict(rec, self.btuk))

    def test_sl_local_msisdn_does_not_match_foreign(self):
        # 23276... is Orange SL — should not bleed into any foreign partner.
        rec = self._record('23276555555')
        self.assertFalse(R._record_belongs_to_partner_dict(rec, self.vodauk))
        self.assertFalse(R._record_belongs_to_partner_dict(rec, self.mtngh))

    def test_local_dial_form_no_match(self):
        # "0771234..." (UK local-dial) without explicit CC should NOT match.
        rec = self._record('07712345678')
        self.assertFalse(R._record_belongs_to_partner_dict(rec, self.vodauk))


# ---------------------------------------------------------------------------
# Local-partner attribution by MSISDN prefix
# ---------------------------------------------------------------------------

class LocalAttributionTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        self.africell = make_partner('AFRIC', is_local=True, is_primary=True)
        self.qcell = make_partner('QCELL', is_local=True, is_primary=True)
        self.orange = make_partner('ORANG', is_local=True, is_home=True, is_primary=True)

    def test_orange_outbound_to_africell(self):
        rec = {'record_type': 'MOC',
                'calling_number': '23276111111',  # Orange (76)
                'called_number': '23277222222'}    # Africell (77)
        self.assertTrue(R._record_belongs_to_partner_dict(rec, self.africell))
        self.assertFalse(R._record_belongs_to_partner_dict(rec, self.qcell))

    def test_africell_to_orange_inbound(self):
        rec = {'record_type': 'MTC',
                'calling_number': '23277111111',  # Africell
                'called_number': '23276222222'}    # Orange
        # Direction MTC → other party = calling = Africell
        self.assertTrue(R._record_belongs_to_partner_dict(rec, self.africell))

    def test_unknown_msisdn_no_match(self):
        rec = {'record_type': 'MOC',
                'calling_number': '23276111111',
                'called_number': ''}
        self.assertFalse(R._record_belongs_to_partner_dict(rec, self.africell))


# ---------------------------------------------------------------------------
# get_applicable_rate — fallback precedence
# ---------------------------------------------------------------------------

class RateLookupFallbackTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        self.partner = make_partner('AFRIC')
        # Build rates: exact LOCAL+PEAK, exact LOCAL+ANY, NATIONAL+ANY
        make_rate(self.partner, dest='LOCAL', tod='PEAK', rate='0.12')
        make_rate(self.partner, dest='LOCAL', tod='ANY',  rate='0.10')
        make_rate(self.partner, dest='NATIONAL', tod='ANY', rate='0.05')

    def test_exact_match_preferred(self):
        rate = R.get_applicable_rate(self.partner, 'INBOUND', 'VOICE',
                                       'LOCAL', date(2026, 3, 15), 'PEAK')
        self.assertEqual(rate.rate, Decimal('0.12'))

    def test_falls_back_to_any_tod(self):
        # No LOCAL+WEEKEND rate exists → falls back to LOCAL+ANY (0.10)
        rate = R.get_applicable_rate(self.partner, 'INBOUND', 'VOICE',
                                       'LOCAL', date(2026, 3, 15), 'WEEKEND')
        self.assertEqual(rate.rate, Decimal('0.10'))

    def test_falls_back_to_national(self):
        # No INTERNATIONAL rate exists → falls back to NATIONAL+ANY (0.05)
        rate = R.get_applicable_rate(self.partner, 'INBOUND', 'VOICE',
                                       'INTERNATIONAL', date(2026, 3, 15), 'PEAK')
        self.assertEqual(rate.rate, Decimal('0.05'))

    def test_returns_none_when_no_rate(self):
        rate = R.get_applicable_rate(self.partner, 'INBOUND', 'DATA',
                                       'LOCAL', date(2026, 3, 15), 'PEAK')
        self.assertIsNone(rate)


# ---------------------------------------------------------------------------
# apply_rates end-to-end on synthetic MSC data
# ---------------------------------------------------------------------------

class ApplyRatesTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        self.africell = make_partner('AFRIC', is_local=True, is_primary=True)
        # INBOUND voice 0.10 SLE/min, OUTBOUND voice 0.08 SLE/min
        make_rate(self.africell, direction='INBOUND',  rate='0.10')
        make_rate(self.africell, direction='OUTBOUND', rate='0.08')
        # SMS: 1 SLE/sms
        make_rate(self.africell, direction='INBOUND', service='SMS',
                   unit='PER_SMS', rate='1.00')
        self.cycle = make_cycle(self.africell)

    def test_orange_calls_africell_outbound_minute(self):
        # Orange (76) → Africell (77), 60s = 1 min, OUTBOUND rate 0.08
        make_msc_record(record_type='MOC',
                         calling='23276111111', called='23277222222',
                         duration=60)
        result = R.apply_rates(self.cycle)
        keys = list(result.buckets.keys())
        self.assertEqual(len(keys), 1)
        bucket = result.buckets[keys[0]]
        self.assertEqual(bucket.direction, 'OUTBOUND')
        self.assertEqual(bucket.service_type, 'VOICE')
        self.assertEqual(bucket.event_count, 1)
        self.assertEqual(bucket.volume, Decimal('1.000'))
        self.assertEqual(bucket.amount, Decimal('0.080000'))

    def test_africell_calls_orange_inbound(self):
        make_msc_record(record_type='MTC',
                         calling='23277111111', called='23276222222',
                         duration=120)  # 2 min
        result = R.apply_rates(self.cycle)
        bucket = next(iter(result.buckets.values()))
        self.assertEqual(bucket.direction, 'INBOUND')
        self.assertEqual(bucket.volume, Decimal('2.000'))
        self.assertEqual(bucket.amount, Decimal('0.200000'))

    def test_inbound_sms(self):
        make_msc_record(record_type='SMSMT',
                         calling='23277111111', called='23276222222',
                         duration=0)
        result = R.apply_rates(self.cycle)
        bucket = next(iter(result.buckets.values()))
        self.assertEqual(bucket.service_type, 'SMS')
        self.assertEqual(bucket.event_count, 1)
        self.assertEqual(bucket.amount, Decimal('1.000000'))

    def test_non_africell_skipped(self):
        # Orange → Qcell (80) — not Africell — skip
        make_msc_record(record_type='MOC',
                         calling='23276111111', called='23280000000',
                         duration=60)
        result = R.apply_rates(self.cycle)
        self.assertEqual(len(result.buckets), 0)
        self.assertEqual(result.skipped_other_partner, 1)

    def test_cycle_aggregates_persisted(self):
        make_msc_record(record_type='MOC',
                         calling='23276111111', called='23277222222',
                         duration=180)  # 3 min OUTBOUND
        make_msc_record(record_type='MTC',
                         calling='23277333333', called='23276444444',
                         duration=60)   # 1 min INBOUND
        R.apply_rates(self.cycle)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.our_voice_minutes, Decimal('4.000'))
        self.assertEqual(self.cycle.our_voice_calls, 2)
        # Status should flip OPEN → CLOSED after rating
        self.assertEqual(self.cycle.status, 'CLOSED')

    def test_no_rate_no_attribution_unrated_zero(self):
        """If no rates exist for direction/service, the record is counted as
        unrated rather than silently swallowed."""
        InterconnectRate.objects.all().delete()
        make_msc_record(record_type='MOC',
                         calling='23276111111', called='23277222222',
                         duration=60)
        result = R.apply_rates(self.cycle)
        self.assertEqual(result.unrated_count, 1)
        self.assertEqual(len(result.buckets), 0)
