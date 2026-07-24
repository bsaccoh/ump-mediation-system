"""Unit tests for ``regulatory.engines.reports``.

* Each of the 4 generator functions returns a dict with expected keys
* generate_report persists a RegulatoryReport row with PDF + XLSX attached
* AuditLog entry written
* PDF and XLSX renderers produce non-empty bytes with sane magic numbers
"""
from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase

from core.models import AuditLog
from regulatory.engines.reports import (
    generate_report, generate_traffic_report, generate_revenue_report,
    generate_subscriber_report, generate_interconnect_summary,
    render_pdf, render_xlsx,
)
from regulatory.models import RegulatoryReport, RetailRevenue

from interconnect.tests._fixtures import (
    make_partner, make_rate, make_cycle, make_msc_record,
)
from interconnect.engines.invoicing import generate_invoice


class PayloadShapeTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        africell = make_partner('AFRIC', is_local=True, is_primary=True)
        make_rate(africell, direction='INBOUND', rate='0.10')
        cycle = make_cycle(africell)
        for _ in range(5):
            make_msc_record('MTC', '23277111111', '23276222222', duration=60,
                             imsi='619011111111111')
        for _ in range(3):
            make_msc_record('MOC', '23276111111', '23277333333', duration=120,
                             imsi='619011111111111')
        generate_invoice(cycle, direction='INBOUND')
        RetailRevenue.objects.create(period_year=2026, period_month=3,
                                       voice_revenue=Decimal('1000'))
        self.start, self.end = date(2026, 3, 1), date(2026, 3, 31)

    def test_traffic_payload_keys(self):
        p = generate_traffic_report(self.start, self.end)
        for key in ('period_start', 'period_end', 'voice_calls_msc',
                    'voice_minutes_msc', 'sms_count', 'gateway_legs',
                    'data_sessions_pgw'):
            self.assertIn(key, p)

    def test_traffic_counts(self):
        p = generate_traffic_report(self.start, self.end)
        # 5 MTC + 3 MOC = 8 voice calls
        self.assertEqual(p['voice_calls_msc'], 8)
        # (5×60 + 3×120) / 60 = 11 minutes
        self.assertEqual(p['voice_minutes_msc'], 11.0)

    def test_revenue_payload(self):
        p = generate_revenue_report(self.start, self.end)
        self.assertGreater(p['interconnect_inbound'], 0)
        self.assertEqual(p['retail_total'], 1000.0)
        # Gross = inbound + retail
        self.assertEqual(p['gross_revenue'],
                          p['interconnect_inbound'] + p['retail_total'])
        self.assertEqual(len(p['retail_rows']), 1)

    def test_subscriber_payload(self):
        p = generate_subscriber_report(self.start, self.end)
        self.assertIn('distinct_imsi_msc', p)
        # One distinct IMSI across 8 records
        self.assertEqual(p['distinct_imsi_msc'], 1)
        self.assertIn('note', p)

    def test_interconnect_summary_payload(self):
        p = generate_interconnect_summary(self.start, self.end)
        self.assertIn('rows', p)
        self.assertGreaterEqual(len(p['rows']), 1)
        # Each row should have the per-partner fields
        first = p['rows'][0]
        for k in ('partner', 'voice_minutes', 'inbound', 'outbound', 'outstanding'):
            self.assertIn(k, first)


class GenerateReportPersistsTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        africell = make_partner('AFRIC', is_local=True, is_primary=True)
        make_rate(africell, direction='INBOUND', rate='0.10')
        cycle = make_cycle(africell)
        for _ in range(3):
            make_msc_record('MTC', '23277111111', '23276222222', duration=60)
        generate_invoice(cycle, direction='INBOUND')
        self.start, self.end = date(2026, 3, 1), date(2026, 3, 31)

    def test_persists_with_artefacts(self):
        rep = generate_report('TRAFFIC', self.start, self.end)
        self.assertIsInstance(rep, RegulatoryReport)
        self.assertEqual(rep.report_type, 'TRAFFIC')
        self.assertEqual(rep.status, 'DRAFT')
        # Both files attached
        self.assertTrue(rep.pdf_file)
        self.assertTrue(rep.xlsx_file)
        self.assertGreater(rep.pdf_file.size, 0)
        self.assertGreater(rep.xlsx_file.size, 0)

    def test_audit_log_written(self):
        before = AuditLog.objects.filter(action='REGULATORY_REPORT_GENERATED').count()
        generate_report('REVENUE', self.start, self.end)
        after = AuditLog.objects.filter(action='REGULATORY_REPORT_GENERATED').count()
        self.assertEqual(after, before + 1)

    def test_unknown_report_type_raises(self):
        with self.assertRaises(ValueError):
            generate_report('NOT_A_TYPE', self.start, self.end)


class RendererSanityTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        africell = make_partner('AFRIC', is_local=True, is_primary=True)
        make_rate(africell, direction='INBOUND', rate='0.10')
        cycle = make_cycle(africell)
        make_msc_record('MTC', '23277111111', '23276222222', duration=60)
        generate_invoice(cycle, direction='INBOUND')
        self.payload = generate_traffic_report(date(2026, 3, 1), date(2026, 3, 31))

    def test_pdf_magic_number(self):
        data = render_pdf(self.payload, 'TRAFFIC')
        self.assertTrue(data.startswith(b'%PDF-'))
        self.assertGreater(len(data), 500)

    def test_xlsx_magic_number(self):
        data = render_xlsx(self.payload, 'TRAFFIC')
        # XLSX = zip file, magic 'PK'
        self.assertTrue(data.startswith(b'PK'))
        self.assertGreater(len(data), 500)
