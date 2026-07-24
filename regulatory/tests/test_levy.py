"""Unit tests for ``regulatory.engines.levy``.

* Gross = interconnect INBOUND + retail
* Levy = gross × levy_pct / 100
* USF  = gross × usf_pct  / 100
* total_payable = levy + USF
* OUTBOUND interconnect captured but NOT in gross
* mark_levy_paid flips status + audit-log written
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import AuditLog
from regulatory.engines.levy import compute_levy, mark_levy_paid
from regulatory.models import RegulatoryProfile, RetailRevenue, LeviedPeriod

from interconnect.tests._fixtures import (
    make_partner, make_rate, make_cycle, make_msc_record,
)
from interconnect.engines.invoicing import generate_invoice


def _build_invoiced_cycle():
    """Set up Africell with 100 INBOUND minutes + 50 OUTBOUND minutes, invoiced."""
    africell = make_partner('AFRIC', is_local=True, is_primary=True)
    make_rate(africell, direction='INBOUND', rate='0.10')
    make_rate(africell, direction='OUTBOUND', rate='0.08')
    cycle = make_cycle(africell)
    # 100 inbound min
    for _ in range(100):
        make_msc_record('MTC', '23277000000', '23276111111', duration=60)
    # 50 outbound min
    for _ in range(50):
        make_msc_record('MOC', '23276000000', '23277111111', duration=60)
    generate_invoice(cycle, direction='INBOUND')
    generate_invoice(cycle, direction='OUTBOUND')
    return cycle


class LevyMathTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def test_gross_equals_inbound_plus_retail(self):
        _build_invoiced_cycle()
        # Inbound: 100 min × 0.10 = 10.00 SLE
        # Retail: voice 100 + sms 20 + data 50 + other 5 = 175.00 SLE
        RetailRevenue.objects.create(
            period_year=2026, period_month=3,
            voice_revenue=Decimal('100'), sms_revenue=Decimal('20'),
            data_revenue=Decimal('50'), other_revenue=Decimal('5'),
        )
        levy = compute_levy(2026, 3)
        self.assertEqual(levy.interconnect_inbound, Decimal('10.00'))
        self.assertEqual(levy.interconnect_outbound, Decimal('4.00'))  # 50×0.08
        self.assertEqual(levy.retail_total, Decimal('175.00'))
        # Gross = 10 + 175 = 185 (outbound NOT counted)
        self.assertEqual(levy.gross_revenue, Decimal('185.00'))

    def test_levy_pct_and_usf_pct_applied(self):
        _build_invoiced_cycle()
        RetailRevenue.objects.create(
            period_year=2026, period_month=3,
            voice_revenue=Decimal('990'),  # gross will be 1000 with the 10 IC inbound
            sms_revenue=Decimal('0'), data_revenue=Decimal('0'),
            other_revenue=Decimal('0'),
        )
        # Defaults from profile: levy 0.5% USF 1.0%
        levy = compute_levy(2026, 3)
        self.assertEqual(levy.gross_revenue, Decimal('1000.00'))
        self.assertEqual(levy.levy_amount, Decimal('5.00'))   # 0.5%
        self.assertEqual(levy.usf_amount, Decimal('10.00'))   # 1.0%
        self.assertEqual(levy.total_payable, Decimal('15.00'))

    def test_status_flips_to_computed(self):
        _build_invoiced_cycle()
        levy = compute_levy(2026, 3)
        self.assertEqual(levy.status, 'COMPUTED')
        self.assertIsNotNone(levy.computed_at)

    def test_recompute_overwrites_existing(self):
        _build_invoiced_cycle()
        levy1 = compute_levy(2026, 3)
        first_id = levy1.pk
        # Add retail revenue then recompute
        RetailRevenue.objects.create(period_year=2026, period_month=3,
                                       voice_revenue=Decimal('500'))
        levy2 = compute_levy(2026, 3)
        # Same PK (update_or_create) — and gross is updated
        self.assertEqual(levy2.pk, first_id)
        self.assertGreater(levy2.gross_revenue, levy1.gross_revenue)

    def test_due_date_is_15th_of_next_month(self):
        _build_invoiced_cycle()
        levy = compute_levy(2026, 3)
        self.assertEqual(levy.due_date, date(2026, 4, 15))
        # December rolls over into January
        levy_dec = LeviedPeriod(period_year=2026, period_month=12,
                                  gross_revenue=Decimal('0'))
        from regulatory.engines.levy import _due_date
        self.assertEqual(_due_date(2026, 12), date(2027, 1, 15))

    def test_invalid_month_raises(self):
        with self.assertRaises(ValueError):
            compute_levy(2026, 13)

    def test_audit_log_written(self):
        _build_invoiced_cycle()
        before = AuditLog.objects.filter(action='LEVY_COMPUTED').count()
        compute_levy(2026, 3)
        after = AuditLog.objects.filter(action='LEVY_COMPUTED').count()
        self.assertEqual(after, before + 1)

    def test_mark_paid_writes_audit_and_status(self):
        _build_invoiced_cycle()
        levy = compute_levy(2026, 3)
        mark_levy_paid(levy, payment_date=date(2026, 4, 10),
                        reference='SWIFT-XYZ-001')
        levy.refresh_from_db()
        self.assertEqual(levy.status, 'PAID')
        self.assertIsNotNone(levy.paid_at)
        self.assertEqual(levy.payment_reference, 'SWIFT-XYZ-001')
        self.assertTrue(AuditLog.objects.filter(action='LEVY_PAID').exists())


class ProfileDefaultsTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def test_singleton_creates_only_once(self):
        p1 = RegulatoryProfile.get_or_create_default()
        p2 = RegulatoryProfile.get_or_create_default()
        self.assertEqual(p1.pk, p2.pk)
        self.assertEqual(RegulatoryProfile.objects.count(), 1)

    def test_default_percentages(self):
        p = RegulatoryProfile.get_or_create_default()
        self.assertEqual(p.levy_pct, Decimal('0.5000'))
        self.assertEqual(p.usf_pct, Decimal('1.0000'))
