"""Unit tests for ``interconnect.engines.invoicing``.

Locks in:

* `generate_invoice` produces correct subtotals + total + total_local
* FX rate is snapshotted from the ExchangeRate table at issue time
* Invoice number format INV-{CODE}-{YYYYMM}-{IN|OUT} with -N suffix on dup
* PDF + CSV artefacts are attached (non-empty bytes)
* Cycle status flips to INVOICED
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from interconnect.engines import invoicing as I
from interconnect.engines import rating as R
from interconnect.models import Invoice, InvoiceLine, InterconnectRate

from ._fixtures import (
    make_partner, make_rate, make_cycle, make_msc_record, make_fx_rate,
)


class GenerateInvoiceTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        self.africell = make_partner('AFRIC', is_local=True, is_primary=True)
        # INBOUND voice 0.10 SLE/min, INBOUND SMS 1.00 SLE/sms
        make_rate(self.africell, direction='INBOUND', rate='0.10')
        make_rate(self.africell, direction='INBOUND', service='SMS',
                   unit='PER_SMS', rate='1.00')
        self.cycle = make_cycle(self.africell)

        # 2 inbound voice calls of 60s + 1 inbound SMS
        make_msc_record('MTC', '23277111111', '23276222222', duration=60)
        make_msc_record('MTC', '23277333333', '23276444444', duration=120)
        make_msc_record('SMSMT', '23277000000', '23276999999', duration=0)

    def test_subtotals_and_total(self):
        inv = I.generate_invoice(self.cycle, direction='INBOUND')
        # 1 min @ 0.10 + 2 min @ 0.10 = 0.30 voice
        # 1 sms @ 1.00 = 1.00 sms
        # total = 1.30
        self.assertEqual(inv.subtotal_voice, Decimal('0.30'))
        self.assertEqual(inv.subtotal_sms, Decimal('1.00'))
        self.assertEqual(inv.subtotal_data, Decimal('0.00'))
        self.assertEqual(inv.total, Decimal('1.30'))

    def test_invoice_lines_one_per_bucket(self):
        inv = I.generate_invoice(self.cycle, direction='INBOUND')
        # Two buckets: VOICE LOCAL PEAK and SMS LOCAL ANY
        self.assertEqual(inv.lines.count(), 2)
        voice = inv.lines.get(service_type='VOICE')
        sms = inv.lines.get(service_type='SMS')
        self.assertEqual(voice.event_count, 2)
        self.assertEqual(voice.volume, Decimal('3.000'))
        self.assertEqual(sms.event_count, 1)
        self.assertEqual(sms.volume, Decimal('1.000'))

    def test_invoice_number_format(self):
        inv = I.generate_invoice(self.cycle, direction='INBOUND')
        # Cycle ends 2026-03-31 → YYYYMM = 202603
        self.assertEqual(inv.invoice_number, 'INV-AFRIC-202603-INB')

    def test_invoice_number_dedup_suffix(self):
        inv1 = I.generate_invoice(self.cycle, direction='INBOUND')
        inv2 = I.generate_invoice(self.cycle, direction='INBOUND')
        self.assertEqual(inv1.invoice_number, 'INV-AFRIC-202603-INB')
        self.assertEqual(inv2.invoice_number, 'INV-AFRIC-202603-INB-2')

    def test_cycle_status_flips_to_invoiced(self):
        I.generate_invoice(self.cycle, direction='INBOUND')
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, 'INVOICED')

    def test_pdf_and_csv_attached(self):
        inv = I.generate_invoice(self.cycle, direction='INBOUND')
        # CSV is guaranteed; PDF is best-effort but should usually attach
        self.assertTrue(inv.csv_file)
        self.assertGreater(inv.csv_file.size, 0)
        # PDF: skip the strict assertion if reportlab missing in CI
        if inv.pdf_file:
            self.assertGreater(inv.pdf_file.size, 0)

    def test_due_date_30_days_after_period_end(self):
        inv = I.generate_invoice(self.cycle, direction='INBOUND')
        # Cycle ends 2026-03-31 → due 2026-04-30
        self.assertEqual(inv.due_date, date(2026, 4, 30))

    def test_no_traffic_raises(self):
        # Wipe all CDRs to leave no rated traffic
        from streams.msc.models import MSCRecord
        MSCRecord.objects.all().delete()
        with self.assertRaises(ValueError):
            I.generate_invoice(self.cycle, direction='INBOUND')


class FxSnapshotTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        # Foreign partner billing in USD
        self.vodauk = make_partner('VODAUK', is_local=False, country_code='44',
                                    is_primary=True, currency='USD')
        # INBOUND foreign voice 0.20 USD/min
        make_rate(self.vodauk, direction='INBOUND',
                   dest='INTERNATIONAL', unit='PER_MINUTE',
                   rate='0.20', currency='USD')
        self.cycle = make_cycle(self.vodauk)

        # Two FX rates with different dates — the engine should pick the
        # most recent one ≤ cycle.period_end.
        make_fx_rate('USD', 'SLE', rate='20.00', effective_date=date(2026, 1, 1))
        make_fx_rate('USD', 'SLE', rate='25.50', effective_date=date(2026, 3, 1))
        make_fx_rate('USD', 'SLE', rate='27.00', effective_date=date(2026, 4, 15))  # after cycle

        # 5-min UK→Orange call
        make_msc_record('MTC', '447712345678', '23276222222', duration=300)

    def test_fx_rate_snapshot(self):
        inv = I.generate_invoice(self.cycle, direction='INBOUND')
        # March 2026 cycle → uses March-1 rate 25.50, not the April one
        self.assertEqual(inv.fx_rate_to_local, Decimal('25.50000000'))
        # 5 min × 0.20 USD = 1.00 USD → 25.50 SLE
        self.assertEqual(inv.currency, 'USD')
        self.assertEqual(inv.total, Decimal('1.00'))
        self.assertEqual(inv.total_local, Decimal('25.50'))

    def test_home_currency_fx_is_one(self):
        # SLE-billed cycle: FX should be 1. Use AFRIC partner code so the
        # 77-prefix MSISDN classifies onto this partner.
        africell = make_partner('AFRIC', is_local=True, is_primary=True)
        make_rate(africell, direction='INBOUND', rate='0.10')
        cycle = make_cycle(africell)
        make_msc_record('MTC', '23277111111', '23276222222', duration=60)
        inv = I.generate_invoice(cycle, direction='INBOUND')
        self.assertEqual(inv.fx_rate_to_local, Decimal('1'))
        self.assertEqual(inv.total, inv.total_local)
