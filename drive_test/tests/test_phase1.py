"""Phase 1 smoke tests: models, RBAC, CRUD, upload dedup, audit.

Run: python manage.py test drive_test --settings=config.test_settings
"""
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import AuditLog
from drive_test.models import Campaign, DriveTestFile, Project
from drive_test.models.enums import FileStatus

User = get_user_model()

_MEDIA = tempfile.mkdtemp(prefix='dt-test-media-')


class RbacTests(TestCase):
    def setUp(self):
        self.analyst = User.objects.create_user('ana', password='x', is_analyst=True)
        self.regulator = User.objects.create_user('reg', password='x', is_regulator=True)
        self.plain = User.objects.create_user('joe', password='x')

    def test_dashboard_requires_login(self):
        resp = self.client.get(reverse('drive_test:dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp.url)

    def test_analyst_can_view_dashboard(self):
        self.client.force_login(self.analyst)
        self.assertEqual(self.client.get(reverse('drive_test:dashboard')).status_code, 200)

    def test_regulator_can_view_dashboard(self):
        self.client.force_login(self.regulator)
        self.assertEqual(self.client.get(reverse('drive_test:dashboard')).status_code, 200)

    def test_plain_user_denied_view(self):
        self.client.force_login(self.plain)
        resp = self.client.get(reverse('drive_test:dashboard'))
        self.assertEqual(resp.status_code, 302)  # bounced to login by user_passes_test

    def test_regulator_cannot_create_project(self):
        # A pure regulator is view-only for management actions.
        self.client.force_login(self.regulator)
        resp = self.client.get(reverse('drive_test:project_create'))
        self.assertEqual(resp.status_code, 302)

    def test_analyst_can_reach_project_create(self):
        self.client.force_login(self.analyst)
        self.assertEqual(self.client.get(reverse('drive_test:project_create')).status_code, 200)


class CrudTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', password='x', is_analyst=True)
        self.client.force_login(self.user)

    def test_create_project_and_campaign_writes_audit(self):
        resp = self.client.post(reverse('drive_test:project_create'), {
            'name': 'National QA 2026', 'status': 'ACTIVE',
            'description': '', 'region': 'Western', 'district': '',
        })
        self.assertEqual(resp.status_code, 302)
        project = Project.objects.get(name='National QA 2026')
        self.assertEqual(project.created_by, self.user)
        self.assertTrue(AuditLog.objects.filter(
            action='CREATE', entity_type='drive_test.Project', entity_id=str(project.pk),
        ).exists())

        resp = self.client.post(
            reverse('drive_test:campaign_create', args=[project.pk]),
            {'name': 'Freetown LTE Benchmark', 'technology': 'LTE'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Campaign.objects.filter(name='Freetown LTE Benchmark', project=project).exists())

    def test_lists_render(self):
        for name in ('project_list', 'campaign_list', 'file_list'):
            self.assertEqual(self.client.get(reverse(f'drive_test:{name}')).status_code, 200)

    def test_placeholder_sections_render(self):
        for section in ('map', 'rf', 'coverage', 'events', 'reports'):
            resp = self.client.get(reverse('drive_test:placeholder', args=[section]))
            self.assertEqual(resp.status_code, 200)


@override_settings(MEDIA_ROOT=_MEDIA)
class UploadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('op', password='x', is_operator=True)
        self.client.force_login(self.user)
        self.project = Project.objects.create(name='P', created_by=self.user)
        self.campaign = Campaign.objects.create(project=self.project, name='C')

    def _upload(self, content=b'time,lat,lon\n1,8.4,-13.2\n', name='log.csv'):
        return self.client.post(
            reverse('drive_test:file_upload', args=[self.campaign.pk]),
            {'files': SimpleUploadedFile(name, content, content_type='text/csv')},
        )

    def test_upload_creates_file_with_hash(self):
        resp = self._upload()
        self.assertEqual(resp.status_code, 302)
        dtf = DriveTestFile.objects.get(campaign=self.campaign)
        self.assertEqual(len(dtf.sha256), 64)
        self.assertEqual(dtf.status, FileStatus.READY)
        self.assertTrue(dtf.size_bytes > 0)
        self.assertTrue(AuditLog.objects.filter(action='UPLOAD').exists())

    def test_identical_reupload_is_deduped(self):
        self._upload()
        self._upload()  # same content
        self.assertEqual(DriveTestFile.objects.filter(campaign=self.campaign).count(), 1)

    def test_upload_profiles_file(self):
        # Since Phase 2, upload profiles the file: real counts, not zero.
        self._upload()
        dtf = DriveTestFile.objects.get(campaign=self.campaign)
        self.assertEqual(dtf.detected_format, 'CSV')
        self.assertEqual(dtf.sample_count, 1)      # one data row
        self.assertTrue(dtf.gps_available)         # lat/lon present
