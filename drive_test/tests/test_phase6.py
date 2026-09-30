"""Phase 6 tests: report assembly, PDF/Excel/CSV/GeoJSON/KML, generation, download.

Run: python manage.py test drive_test.tests.test_phase6 --settings=config.test_settings
"""
import json
import tempfile
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import AuditLog
from drive_test.models import Campaign, DriveTestFile, Project, Report, Sample
from drive_test.models.enums import ReportStatus, ReportType
from drive_test.services import report_data, report_exports, reports
from drive_test.services.report_excel import render_excel
from drive_test.services.report_pdf import render_pdf

User = get_user_model()
_MEDIA = tempfile.mkdtemp(prefix='dt-p6-')


class _Data:
    def _campaign(self, n=30):
        self.user = User.objects.create_user('op', password='x', is_operator=True)
        self.project = Project.objects.create(name='P', created_by=self.user)
        self.campaign = Campaign.objects.create(project=self.project, name='C', technology='LTE')
        self.dtf = DriveTestFile.objects.create(campaign=self.campaign, original_name='f.csv',
                                               sha256='f' * 64, detected_format='CSV')
        base = datetime(2026, 9, 1, 10, 0, 0)
        Sample.objects.bulk_create([
            Sample(campaign=self.campaign, drive_file=self.dtf,
                   timestamp=base + timedelta(seconds=i), latitude=8.48 + i * 0.001,
                   longitude=-13.22 + i * 0.001, technology='LTE', rsrp=-80 - (i % 40), sinr=10.0)
            for i in range(n)
        ])


class AssembleTests(_Data, TestCase):
    def test_executive_has_sections(self):
        self._campaign()
        r = Report(report_type=ReportType.EXECUTIVE, campaign=self.campaign, project=self.project)
        data = report_data.assemble(r)
        self.assertTrue(data['summary'])
        self.assertTrue(data['sections'])
        headings = [s['heading'] for s in data['sections']]
        self.assertTrue(any('custody' in h.lower() for h in headings))  # provenance always present

    def test_pdf_and_excel_bytes(self):
        self._campaign()
        r = Report(report_type=ReportType.TECHNICAL, campaign=self.campaign, project=self.project)
        data = report_data.assemble(r)
        pdf = render_pdf(data)
        self.assertTrue(pdf.startswith(b'%PDF'))
        xlsx = render_excel(data)
        self.assertTrue(xlsx[:2] == b'PK')  # xlsx is a zip


class ExportTests(_Data, TestCase):
    def test_csv_records_filter(self):
        self._campaign()
        out = report_exports.csv_export(self.campaign).decode()
        self.assertIn('# UMP Drive Test export', out)
        self.assertIn('latitude', out.splitlines()[1])

    def test_geojson_wellformed(self):
        self._campaign()
        doc = json.loads(report_exports.geojson_export(self.campaign, 'rsrp').decode())
        self.assertEqual(doc['type'], 'FeatureCollection')
        kinds = {f['properties']['kind'] for f in doc['features']}
        self.assertIn('route', kinds)
        self.assertIn('sample', kinds)

    def test_kml_wellformed(self):
        self._campaign()
        out = report_exports.kml_export(self.campaign, 'rsrp').decode()
        self.assertIn('<kml', out)
        self.assertIn('<LineString>', out)


@override_settings(MEDIA_ROOT=_MEDIA)
class GenerateAndDownloadTests(_Data, TestCase):
    def setUp(self):
        self._campaign()
        self.client.force_login(self.user)

    def _make(self, fmt='pdf', rtype=ReportType.EXECUTIVE):
        return Report.objects.create(report_type=rtype, campaign=self.campaign,
                                     project=self.project, params={'format': fmt},
                                     status=ReportStatus.PENDING, generated_by=self.user)

    def test_generate_sets_ready_and_artifact(self):
        for fmt in ('pdf', 'xlsx', 'csv', 'geojson', 'kml'):
            r = self._make(fmt=fmt)
            reports.generate(r)
            r.refresh_from_db()
            self.assertEqual(r.status, ReportStatus.READY, fmt)
            self.assertTrue(r.artifact_path.endswith('.' + fmt))

    def test_generate_view_and_download_audited(self):
        resp = self.client.post(reverse('drive_test:report_generate'), {
            'campaign': self.campaign.pk, 'report_type': ReportType.EXECUTIVE, 'format': 'pdf',
        })
        self.assertEqual(resp.status_code, 302)
        report = Report.objects.latest('generated_at')
        self.assertEqual(report.status, ReportStatus.READY)  # sync generation
        dl = self.client.get(reverse('drive_test:report_download', args=[report.ref]))
        self.assertEqual(dl.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action='EXPORT', entity_type='drive_test.Report').exists())

    def test_download_missing_artifact_404(self):
        r = self._make()  # never generated
        self.assertEqual(
            self.client.get(reverse('drive_test:report_download', args=[r.ref])).status_code, 404)

    def test_report_pages_render(self):
        self.assertEqual(self.client.get(reverse('drive_test:report_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('drive_test:report_generate')).status_code, 200)
