from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.utils import timezone
from bs4 import BeautifulSoup

from collection.models import CDRFile, DistributionLog
from collection.views import _format_file_size, distribution_dashboard
from portals.models import OutputPortal, DistributionRule, OutputSchema
from core.dispatcher import dispatch_in_memory


class DistributionDashboardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='testadmin', password='password123', is_staff=True)
        self.factory = RequestFactory()

    def test_format_file_size_zero_and_none(self):
        self.assertEqual(_format_file_size(0), '0')
        self.assertEqual(_format_file_size(None), '0')
        self.assertEqual(_format_file_size(-10), '0')
        self.assertEqual(_format_file_size(500), '500 B')
        self.assertEqual(_format_file_size(2048), '2.0 KB')
        self.assertEqual(_format_file_size(15435576), '14.7 MB')

    def test_dashboard_table_renders_zero_size_and_retries_and_skipped(self):
        cdr_file = CDRFile.objects.create(
            filename='test_cdr.csv',
            status=CDRFile.Status.COMPLETED,
            records_total=0,
            file_size=0,
            operator_code='orange',
            decoder_type='MSC',
        )
        portal = OutputPortal.objects.create(
            name='TestPortal',
            portal_type='LOCAL',
            output_format='CSV',
            directory='test',
            is_active=True,
        )
        schema = OutputSchema.objects.create(
            name='TestSchema',
            stream_type='MSC',
        )
        rule = DistributionRule.objects.create(
            name='TestRule',
            output_portal=portal,
            output_schema=schema,
            stream_type='MSC',
            is_active=True,
        )
        DistributionLog.objects.create(
            cdr_file=cdr_file,
            rule=rule,
            output_portal=portal,
            filename='test_output.csv',
            record_count=0,
            file_size=0,
            retry_count=0,
            status=DistributionLog.Status.SKIPPED,
            skip_reason='Zero records or empty file size',
            delivered_at=timezone.now(),
        )

        req = self.factory.get('/collection/distribution/')
        req.user = self.user
        response = distribution_dashboard(req)
        self.assertEqual(response.status_code, 200)

        soup = BeautifulSoup(response.content, 'html.parser')
        row = soup.find('tr', {'data-status': 'SKIPPED'})
        self.assertIsNotNone(row)

        cols = [td.get_text(strip=True) for td in row.find_all('td')]
        # col 0: Checkbox, col 1: Delivered, col 2: Rule, col 3: Stream, col 4: Portal,
        # col 5: Filename, col 6: Records, col 7: Size, col 8: Retries, col 9: Status
        self.assertEqual(cols[5], 'test_output.csv')  # Filename
        self.assertEqual(cols[6], '0')  # Records
        self.assertEqual(cols[7], '0')  # Size
        self.assertEqual(cols[8], '0')  # Retries
        self.assertIn('SKIPPED', cols[9])  # Status

    def test_dispatch_in_memory_restricts_zero_records(self):
        cdr_file = CDRFile.objects.create(
            filename='empty_cdr.csv',
            status=CDRFile.Status.COMPLETED,
            records_total=0,
            file_size=0,
            operator_code='orange',
            decoder_type='MSC',
        )
        portal = OutputPortal.objects.create(
            name='LocalPortal',
            portal_type='LOCAL',
            output_format='CSV',
            directory='test',
            is_active=True,
        )
        schema = OutputSchema.objects.create(
            name='TestSchema2',
            stream_type='MSC',
        )
        rule = DistributionRule.objects.create(
            name='EmptyTestRule',
            output_portal=portal,
            output_schema=schema,
            stream_type='MSC',
            is_active=True,
        )

        summaries = dispatch_in_memory(cdr_file, records=[])
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]['status'], 'SKIPPED')

        log = DistributionLog.objects.filter(cdr_file=cdr_file, rule=rule).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, DistributionLog.Status.SKIPPED)
        self.assertEqual(log.record_count, 0)
        self.assertEqual(log.file_size, 0)
        self.assertIn('records', log.skip_reason.lower())
