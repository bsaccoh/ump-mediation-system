"""Decode-only mode: render output CSV from in-memory records, no DB writes."""
import tempfile
from pathlib import Path

from django.test import TestCase

from collection.models import CDRFile, DistributionLog
from core.dispatcher import dispatch_in_memory
from portals.models import OutputPortal, OutputSchema, DistributionRule
from streams.msc.models import MSCRecord


class DispatchInMemoryTests(TestCase):
    databases = {'default'}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cdr = CDRFile.objects.create(
            filename='bFTMSX01_x.dat', file_path='/dev/null', file_size=1,
            status='COMPLETED', decoder_type='MSC',
            operator_code='orange', vendor='huawei', network_element='msc',
        )
        portal = OutputPortal.objects.create(
            name='BIGDATA_MSC', portal_type='LOCAL', output_format='CSV',
            stream_type='MSC', directory=self.tmp, is_active=True,
        )
        schema = OutputSchema.objects.create(
            name='MSC out', stream_type='MSC',
            mapping_json=[['Record Type', 'record_type'],
                          ['Calling', 'calling_number'],
                          ['Subscriber Type', 'prepaid_flag']],
        )
        DistributionRule.objects.create(
            name='msc->bigdata', stream_type='MSC',
            output_portal=portal, output_schema=schema, is_active=True,
        )

    def test_renders_csv_without_persisting(self):
        # UNSAVED MSCRecord instances (decode-only mode never saves them).
        records = [
            MSCRecord(file=self.cdr, record_type='MOC', calling_number='23276111111',
                      prepaid_flag='PREPAID'),
            MSCRecord(file=self.cdr, record_type='MTC', calling_number='23277222222',
                      prepaid_flag=''),
        ]
        summaries = dispatch_in_memory(self.cdr, records)

        # Nothing was written to the records table.
        self.assertEqual(MSCRecord.objects.count(), 0)

        # The output CSV exists with the mapped header + 2 rows.
        out_files = list(Path(self.tmp).rglob('*.csv'))
        self.assertEqual(len(out_files), 1)
        text = out_files[0].read_text(encoding='utf-8').strip().splitlines()
        self.assertEqual(text[0], 'Record Type,Calling,Subscriber Type')
        self.assertEqual(len(text), 3)  # header + 2 records
        self.assertIn('MOC,23276111111,PREPAID', text[1])

        # Delivery logged, success.
        self.assertTrue(any(s['status'] == 'SUCCESS' for s in summaries))
        self.assertEqual(DistributionLog.objects.filter(
            status=DistributionLog.Status.SUCCESS).count(), 1)

    def test_output_filename_keeps_original_stem(self):
        dispatch_in_memory(self.cdr, [MSCRecord(file=self.cdr, record_type='MOC')])
        out = list(Path(self.tmp).rglob('*.csv'))[0]
        self.assertEqual(out.name, 'bFTMSX01_x.csv')  # original name, .csv ext

    def test_rule_filter_applied_in_memory(self):
        """A second downstream with a postpaid-only filter gets only matching rows."""
        ipacs_dir = tempfile.mkdtemp()
        portal = OutputPortal.objects.create(
            name='IPACS_MSC', portal_type='LOCAL', output_format='CSV',
            stream_type='MSC', directory=ipacs_dir, is_active=True,
        )
        schema = OutputSchema.objects.create(
            name='IPACS out', stream_type='MSC',
            mapping_json=[['Record Type', 'record_type'],
                          ['Subscriber Type', 'prepaid_flag']],
        )
        DistributionRule.objects.create(
            name='msc->ipacs', stream_type='MSC', output_portal=portal,
            output_schema=schema, is_active=True,
            filter_logic='{"prepaid_flag": "POSTPAID"}',
        )

        records = [
            MSCRecord(file=self.cdr, record_type='MOC', prepaid_flag='PREPAID'),
            MSCRecord(file=self.cdr, record_type='MTC', prepaid_flag='POSTPAID'),
            MSCRecord(file=self.cdr, record_type='CF', prepaid_flag='POSTPAID'),
        ]
        dispatch_in_memory(self.cdr, records)

        # BIGDATA (no filter) gets all 3; IPACS (postpaid filter) gets 2.
        bigdata = list(Path(self.tmp).rglob('*.csv'))[0].read_text().strip().splitlines()
        ipacs = list(Path(ipacs_dir).rglob('*.csv'))[0].read_text().strip().splitlines()
        self.assertEqual(len(bigdata), 1 + 3)   # header + 3
        self.assertEqual(len(ipacs), 1 + 2)     # header + 2 postpaid
        self.assertNotIn('PREPAID', '\n'.join(ipacs[1:]))
