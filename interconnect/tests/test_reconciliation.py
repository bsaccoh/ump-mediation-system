"""Unit tests for ``interconnect.engines.reconciliation``.

* Flexible column-name detection (service_type / service / type aliases)
* Service / destination value normalisation (e.g. "VOICE", "Voice", "vo" → VOICE)
* Variance % computed correctly relative to our side
* MATCHED threshold (< 1 %) vs OPEN status
"""
import io
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from interconnect.engines import reconciliation as Rc
from interconnect.engines import invoicing as I
from interconnect.models import (
    ReconciliationRecord, InvoiceLine,
)

from ._fixtures import (
    make_partner, make_rate, make_cycle, make_msc_record,
)


def _csv(data: str) -> SimpleUploadedFile:
    return SimpleUploadedFile('partner.csv', data.encode('utf-8'),
                                content_type='text/csv')


class ColumnDetectionTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    """The parser should accept multiple synonyms per column."""

    def setUp(self):
        africell = make_partner('AFRIC', is_local=True, is_primary=True)
        self.cycle = make_cycle(africell)

    def test_canonical_column_names(self):
        data = 'service_type,destination_type,volume,amount\nVOICE,LOCAL,100,10.00\n'
        result = Rc.import_partner_cdr(self.cycle, _csv(data))
        self.assertEqual(result['partner_buckets'], 1)
        rec = ReconciliationRecord.objects.get()
        self.assertEqual(rec.service_type, 'VOICE')
        self.assertEqual(rec.partner_volume, Decimal('100'))

    def test_alias_columns(self):
        data = 'service,dest,minutes,charge\nvoice,local,200,20.00\n'
        result = Rc.import_partner_cdr(self.cycle, _csv(data))
        self.assertEqual(result['partner_buckets'], 1)
        rec = ReconciliationRecord.objects.get()
        self.assertEqual(rec.service_type, 'VOICE')
        self.assertEqual(rec.destination_type, 'LOCAL')

    def test_destination_aliases_normalised(self):
        data = 'service,destination,volume\nSMS,intl,50\n'
        Rc.import_partner_cdr(self.cycle, _csv(data))
        rec = ReconciliationRecord.objects.get()
        self.assertEqual(rec.destination_type, 'INTERNATIONAL')

    def test_missing_required_column_raises(self):
        # No "service" column — should raise
        data = 'destination,volume\nLOCAL,100\n'
        with self.assertRaises(ValueError):
            Rc.import_partner_cdr(self.cycle, _csv(data))


class VarianceTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    """Diff math: variance_volume = partner − ours; variance_pct vs ours."""

    def setUp(self):
        self.africell = make_partner('AFRIC', is_local=True, is_primary=True)
        make_rate(self.africell, direction='INBOUND', rate='0.10')
        self.cycle = make_cycle(self.africell)

        # Our side: 100 minutes via 100 1-min calls
        for _ in range(100):
            make_msc_record('MTC', '23277000000', '23276999999', duration=60)
        self.invoice = I.generate_invoice(self.cycle, direction='INBOUND')
        # InvoiceLine: 100 min @ 0.10 = 10.00
        self.our_line = self.invoice.lines.get()

    def test_zero_variance_marked_matched(self):
        """Partner reports the same totals as us → < 1% variance → MATCHED."""
        # The bucket is (VOICE, LOCAL) since rates use destination LOCAL
        data = f'service,destination,volume,amount\nVOICE,{self.our_line.destination_type},100,10.00\n'
        Rc.import_partner_cdr(self.cycle, _csv(data))
        rec = ReconciliationRecord.objects.get(
            service_type='VOICE', destination_type=self.our_line.destination_type,
        )
        self.assertEqual(rec.our_volume, Decimal('100.000'))
        self.assertEqual(rec.partner_volume, Decimal('100.000'))
        self.assertEqual(rec.variance_volume, Decimal('0.000'))
        self.assertEqual(rec.status, 'MATCHED')

    def test_negative_variance_open(self):
        """Partner reports 50% less than us → wide variance → OPEN."""
        data = f'service,destination,volume,amount\nVOICE,{self.our_line.destination_type},50,5.00\n'
        Rc.import_partner_cdr(self.cycle, _csv(data))
        rec = ReconciliationRecord.objects.get(
            service_type='VOICE', destination_type=self.our_line.destination_type,
        )
        self.assertEqual(rec.partner_volume, Decimal('50.000'))
        self.assertEqual(rec.variance_volume, Decimal('-50.000'))
        self.assertEqual(rec.variance_pct, Decimal('-50.00'))
        self.assertEqual(rec.status, 'OPEN')

    def test_partner_only_bucket_created(self):
        """A bucket present only in the partner's file still gets a row."""
        data = 'service,destination,volume,amount\nDATA,INTERNATIONAL,500,25.00\n'
        Rc.import_partner_cdr(self.cycle, _csv(data))
        rec = ReconciliationRecord.objects.get(
            service_type='DATA', destination_type='INTERNATIONAL',
        )
        self.assertEqual(rec.our_volume, Decimal('0'))
        self.assertEqual(rec.partner_volume, Decimal('500.000'))


class ComputeVarianceTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    """`compute_variance` re-aggregates our side from invoice lines."""

    def setUp(self):
        self.africell = make_partner('AFRIC', is_local=True, is_primary=True)
        make_rate(self.africell, direction='INBOUND', rate='0.10')
        self.cycle = make_cycle(self.africell)
        for _ in range(10):
            make_msc_record('MTC', '23277000000', '23276999999', duration=60)
        invoice = I.generate_invoice(self.cycle, direction='INBOUND')
        self.line = invoice.lines.get()

    def test_refreshes_our_side_from_invoice_lines(self):
        # Seed a recon row with stale data
        ReconciliationRecord.objects.create(
            partner=self.africell, billing_cycle=self.cycle,
            service_type='VOICE', destination_type=self.line.destination_type,
            our_volume=Decimal('0'), our_amount=Decimal('0'),
            partner_volume=Decimal('10'), partner_amount=Decimal('1'),
            status='OPEN',
        )
        n = Rc.compute_variance(self.cycle)
        self.assertEqual(n, 1)
        rec = ReconciliationRecord.objects.get()
        self.assertEqual(rec.our_volume, Decimal('10.000'))
        self.assertEqual(rec.variance_volume, Decimal('0.000'))
