"""Processing Monitor & Job Operations workspace tests."""
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from drive_test.models import (
    DataQualityResult, DeviceManufacturer, DeviceModel, DriveTestFile,
    DriveTestSession, Measurement, ParserProfile, RadioMeasurement, TestDevice,
)
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@STATIC
class ProcessingMonitorTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.user = U.objects.create_user('viewer', password='x')
        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.qcell = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')
        cls.profile = ParserProfile.objects.create(name='TEMS Pocket', parser_class='drive_test.parsers.trp_parser.TrpDriveTestParser')

        mfr = DeviceManufacturer.objects.create(name='Samsung')
        model = DeviceModel.objects.create(manufacturer=mfr, model_name='Galaxy S21')
        cls.device = TestDevice.objects.create(serial_number='SN-1', device_model=model, label='Test phone #1')

        # ── Completed session/file, with a DataQualityResult ────────────────────
        cls.sess_ok = DriveTestSession.objects.create(
            operator=cls.orange, test_date='2026-09-24', uploaded_by=cls.user, status='COMPLETED')
        cls.file_ok = DriveTestFile.objects.create(
            session=cls.sess_ok, original_filename='ok.trp', file_path='p', file_size=100, sha256='a' * 64,
            status='COMPLETED', parser_profile=cls.profile, measurement_count=3,
            processing_started_at=timezone.now() - timedelta(minutes=5),
            processing_completed_at=timezone.now() - timedelta(minutes=4),
        )

        def meas(file, seq, cell=None, valid=True, device=None):
            m = Measurement.objects.create(
                drive_file=file, sequence_num=seq, captured_at=datetime(2026, 9, 24, 12, 0, seq),
                latitude=8.48, longitude=-13.23, matched_cell=cell, is_valid=valid,
                match_method='exact_ecgi' if cell else '', test_device=device)
            RadioMeasurement.objects.create(measurement=m, technology='4G', rssi=-85.0)
            return m

        meas(cls.file_ok, 1, cell=None, device=cls.device)
        meas(cls.file_ok, 2, cell=None)
        meas(cls.file_ok, 3, cell=None, valid=False)
        DataQualityResult.objects.create(
            drive_file=cls.file_ok, total_records=3, valid_records=2, invalid_records=1,
            matched_cells=0, unmatched_cells=2, overall_score=70.0,
        )

        # ── Actively processing file (MATCHING) ─────────────────────────────────
        cls.sess_proc = DriveTestSession.objects.create(
            operator=cls.qcell, test_date='2026-09-25', uploaded_by=cls.user, status='PROCESSING')
        cls.file_proc = DriveTestFile.objects.create(
            session=cls.sess_proc, original_filename='proc.trp', file_path='p', file_size=50, sha256='b' * 64,
            status='MATCHING', parser_profile=cls.profile, measurement_count=10,
            processing_started_at=timezone.now() - timedelta(seconds=30),
        )

        # ── Failed file, error message includes a filesystem path to redact ────
        cls.sess_failed = DriveTestSession.objects.create(
            operator=cls.orange, test_date='2026-09-26', uploaded_by=cls.user, status='FAILED')
        cls.file_failed = DriveTestFile.objects.create(
            session=cls.sess_failed, original_filename='bad.trp', file_path='p', file_size=10, sha256='c' * 64,
            status='FAILED', parser_profile=cls.profile,
            processing_started_at=timezone.now() - timedelta(minutes=2),
            processing_completed_at=timezone.now() - timedelta(minutes=1),
            error_message=r"FileNotFoundError: Drive test file not found on disk: C:\Users\bob\uploads\bad.trp",
        )

        # ── Queued (never started) file ──────────────────────────────────────────
        cls.sess_queued = DriveTestSession.objects.create(
            operator=cls.qcell, test_date='2026-09-27', uploaded_by=cls.user, status='PENDING')
        cls.file_queued = DriveTestFile.objects.create(
            session=cls.sess_queued, original_filename='queued.trp', file_path='p', file_size=5, sha256='d' * 64,
            status='RECEIVED',
        )

        # uploaded_at is auto_now_add; set deterministic values (bypassing that) so the
        # date-range filter test is not at the mercy of when the suite actually runs.
        DriveTestFile.objects.filter(pk=cls.file_ok.pk).update(uploaded_at=datetime(2026, 9, 24, 9, 0))
        DriveTestFile.objects.filter(pk=cls.file_proc.pk).update(uploaded_at=datetime(2026, 9, 25, 9, 0))
        DriveTestFile.objects.filter(pk=cls.file_failed.pk).update(uploaded_at=datetime(2026, 9, 26, 9, 0))
        DriveTestFile.objects.filter(pk=cls.file_queued.pk).update(uploaded_at=datetime(2026, 9, 27, 9, 0))

        cls.url = reverse('drive_test:processing_monitor')

    def setUp(self):
        self.client.force_login(self.user)

    def get(self, **q):
        return self.client.get(self.url, q)

    def detail_url(self, f):
        return reverse('drive_test:processing_detail', args=[f.pk])

    # ── Access ───────────────────────────────────────────────────────────────
    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.get().status_code, 302)

    # ── Summary / listing ───────────────────────────────────────────────────
    def test_page_loads_with_real_summary(self):
        r = self.get()
        self.assertEqual(r.status_code, 200)
        s = r.context['summary']
        self.assertEqual(s['total'], 4)
        self.assertEqual(s['completed'], 1)
        self.assertEqual(s['processing'], 1)
        self.assertEqual(s['failed'], 1)
        self.assertEqual(s['queued'], 1)

    def test_table_shows_real_measurement_count_and_device(self):
        r = self.get()
        rows = {f.pk: f for f in r.context['files']}
        self.assertEqual(rows[self.file_ok.pk].measurement_count, 3)
        self.assertEqual(rows[self.file_ok.pk].device_label, 'Test phone #1')
        # invalid_count comes straight from the stored DataQualityResult, not recomputed
        self.assertEqual(rows[self.file_ok.pk].invalid_count, 1)
        # A file with no DataQualityResult yet shows '—', never a fabricated 0
        self.assertIsNone(rows[self.file_proc.pk].invalid_count)

    def test_current_stage_labels_reflect_real_status(self):
        r = self.get()
        rows = {f.pk: f for f in r.context['files']}
        self.assertEqual(rows[self.file_ok.pk].stage_label, 'Completed')
        self.assertEqual(rows[self.file_proc.pk].stage_label, 'Cell Matching')
        self.assertEqual(rows[self.file_queued.pk].stage_label, 'Queued')
        self.assertEqual(rows[self.file_failed.pk].stage_label, 'Failed')

    def test_failed_error_is_sanitised_no_path_no_traceback(self):
        r = self.get()
        rows = {f.pk: f for f in r.context['files']}
        err = rows[self.file_failed.pk].safe_error
        self.assertIn('FileNotFoundError', err)
        self.assertNotIn('C:\\Users', err)
        self.assertNotIn('Traceback', err)
        self.assertContains(r, 'Reason:')

    def test_no_retry_button_for_failed_job(self):
        r = self.get()
        self.assertNotContains(r, 'Retry')

    # ── Filters ──────────────────────────────────────────────────────────────
    def test_search_filter(self):
        r = self.get(q='bad.trp')
        codes = [f.pk for f in r.context['files']]
        self.assertEqual(codes, [self.file_failed.pk])

    def test_operator_filter(self):
        r = self.get(operator='qcell')
        codes = sorted(f.pk for f in r.context['files'])
        self.assertEqual(codes, sorted([self.file_proc.pk, self.file_queued.pk]))

    def test_status_filter(self):
        r = self.get(status='COMPLETED')
        self.assertEqual([f.pk for f in r.context['files']], [self.file_ok.pk])

    def test_format_filter(self):
        r = self.get(format='TEMS Pocket')
        self.assertEqual(r.context['summary']['total'], 3)  # all but the queued file, which has no parser_profile

    def test_invalid_status_filter_reported_not_500(self):
        r = self.get(status='NOT_A_STATUS')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Unrecognised status.', r.context['errors'])

    def test_date_filter(self):
        r = self.get(date_from='2026-09-26', date_to='2026-09-26')
        self.assertEqual([f.pk for f in r.context['files']], [self.file_failed.pk])

    # ── Empty states ─────────────────────────────────────────────────────────
    def test_empty_state_when_filters_match_nothing(self):
        r = self.get(q='does-not-exist')
        self.assertContains(r, 'No processing jobs match the selected filters.')

    def test_empty_state_when_no_jobs_at_all(self):
        DriveTestFile.objects.all().delete()
        DriveTestSession.objects.all().delete()
        r = self.get()
        self.assertContains(r, 'No processing jobs found.')

    # ── Pagination ───────────────────────────────────────────────────────────
    def test_pagination_per_page(self):
        r = self.get(per_page=50)
        self.assertEqual(r.context['per_page'], 50)
        self.assertEqual(len(r.context['files']), 4)
        self.assertEqual(r.context['page_obj'].paginator.num_pages, 1)

    def test_pagination_rejects_unlisted_page_size(self):
        r = self.get(per_page=2)  # not one of PAGE_SIZES -> falls back to the default
        self.assertEqual(r.context['per_page'], 20)

    # ── Detail page ──────────────────────────────────────────────────────────
    def test_detail_page_completed_timeline_all_done(self):
        r = self.client.get(self.detail_url(self.file_ok))
        self.assertEqual(r.status_code, 200)
        states = {s['key']: s['state'] for s in r.context['timeline']}
        self.assertTrue(all(v == 'done' for v in states.values()))
        self.assertEqual(r.context['stats']['valid_measurements'], 2)
        self.assertEqual(r.context['stats']['invalid_measurements'], 1)

    def test_detail_page_processing_shows_current_stage(self):
        r = self.client.get(self.detail_url(self.file_proc))
        states = {s['key']: s['state'] for s in r.context['timeline']}
        self.assertEqual(states['matching'], 'current')
        self.assertEqual(states['upload'], 'done')
        self.assertEqual(states['completed'], 'pending')

    def test_detail_page_failed_marks_first_unconfirmed_stage_failed(self):
        r = self.client.get(self.detail_url(self.file_failed))
        states = {s['key']: s['state'] for s in r.context['timeline']}
        # This file never inserted measurements or matched anything -> parsing is
        # the first stage without durable evidence, so that is the failure marker.
        self.assertEqual(states['upload'], 'done')
        self.assertEqual(states['detection'], 'done')
        self.assertEqual(states['parsing'], 'failed')
        self.assertNotIn('done', [states['insert'], states['matching'], states['completed']])
        self.assertContains(r, 'PROCESSING FAILED')
        self.assertContains(r, 'upload/process again using the existing workflow')

    def test_detail_page_navigation_gated_by_real_data(self):
        r = self.client.get(self.detail_url(self.file_queued))
        self.assertFalse(r.context['can_view_measurements'])
        self.assertFalse(r.context['can_view_quality'])
        self.assertNotContains(r, 'View Measurements')
        self.assertNotContains(r, 'View Data Quality')

        r2 = self.client.get(self.detail_url(self.file_ok))
        self.assertTrue(r2.context['can_view_measurements'])
        self.assertTrue(r2.context['can_view_quality'])
        self.assertContains(r2, 'View Measurements')
        self.assertContains(r2, 'View Data Quality')
