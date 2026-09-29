"""KPI Analysis workspace tests. Fixtures exist only in the isolated test DB."""
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import (
    Cell, DriveTestFile, DriveTestSession, Measurement, RadioMeasurement,
    Sector, ServiceMeasurement, Site,
)
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@STATIC
class KpiAnalysisTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.user = U.objects.create_user('viewer', password='x')
        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.qcell = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')
        cls.site = Site.objects.create(site_id='FT-001', name='Freetown Central', operator=cls.orange, latitude=8.48, longitude=-13.23)
        cls.sec = Sector.objects.create(site=cls.site, sector_id='A')
        cls.cell1 = Cell.objects.create(cell_id='LTE-001', operator=cls.orange, sector=cls.sec, technology='4G')
        cls.cell2 = Cell.objects.create(cell_id='LTE-002', operator=cls.orange, sector=cls.sec, technology='4G')

        cls.sess = DriveTestSession.objects.create(
            operator=cls.orange, test_date='2026-09-24', uploaded_by=cls.user, status='COMPLETED',
            total_measurements=5)
        cls.file1 = DriveTestFile.objects.create(
            session=cls.sess, original_filename='walk_001.trp', file_path='p', file_size=1,
            sha256='a' * 64, status='COMPLETED')

        def meas(seq, lat, lon, cell=None, rssi=None, speed=None):
            m = Measurement.objects.create(
                drive_file=cls.file1, sequence_num=seq, captured_at=datetime(2026, 9, 24, 12, 0, seq),
                latitude=lat, longitude=lon, matched_cell=cell, speed_kmh=speed,
                match_method='exact_ecgi' if cell else '')
            RadioMeasurement.objects.create(measurement=m, technology='4G', rssi=rssi)
            return m

        m1 = meas(1, 8.48, -13.23, cls.cell1, rssi=-80.0, speed=30.0)   # good RSSI
        m2 = meas(2, 8.49, -13.24, cls.cell1, rssi=-90.0, speed=40.0)   # fair RSSI
        meas(3, 8.50, -13.25, cls.cell1, rssi=-80.0, speed=20.0)        # good RSSI
        meas(4, 8.51, -13.26, cls.cell2, rssi=-110.0)                  # very poor RSSI
        meas(5, 8.52, -13.27, None, rssi=None)                          # unmatched, no RSSI

        ServiceMeasurement.objects.create(
            measurement=m1, service_type='VOICE', outcome='SUCCESS', call_duration_s=30, mos=4.2)
        ServiceMeasurement.objects.create(
            measurement=m2, service_type='VOICE', outcome='DROPPED', call_duration_s=5)

        # ── Session with no service/voice data at all (still real, just sparse) ──
        cls.sess_novoice = DriveTestSession.objects.create(
            operator=cls.orange, test_date='2026-09-22', uploaded_by=cls.user, status='COMPLETED',
            total_measurements=1)
        file2 = DriveTestFile.objects.create(
            session=cls.sess_novoice, original_filename='novoice.trp', file_path='p', file_size=1,
            sha256='d' * 64, status='COMPLETED')
        m3 = Measurement.objects.create(
            drive_file=file2, sequence_num=1, captured_at=datetime(2026, 9, 22, 9, 0, 0),
            latitude=8.4, longitude=-13.2)
        RadioMeasurement.objects.create(measurement=m3, technology='3G')

        # ── Processing / failed sessions — no KPI data ──
        cls.sess_processing = DriveTestSession.objects.create(
            operator=cls.qcell, test_date='2026-09-20', uploaded_by=cls.user, status='PROCESSING')
        DriveTestFile.objects.create(
            session=cls.sess_processing, original_filename='p.trp', file_path='p', file_size=1,
            sha256='b' * 64, status='PARSING')

        cls.sess_failed = DriveTestSession.objects.create(
            operator=cls.qcell, test_date='2026-09-18', uploaded_by=cls.user, status='FAILED')
        DriveTestFile.objects.create(
            session=cls.sess_failed, original_filename='f.trp', file_path='p', file_size=1,
            sha256='c' * 64, status='FAILED', error_message='boom')

        cls.url = reverse('drive_test:kpi_analysis')

    def setUp(self):
        self.client.force_login(self.user)

    def get(self, **q):
        return self.client.get(self.url, q)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.get().status_code, 302)

    def test_no_session_selected_shows_selector_and_prompt(self):
        r = self.get()
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context['session'])
        self.assertContains(r, 'Select a drive-test session to analyze.')
        refs = {s.session_ref for s in r.context['sessions']}
        self.assertEqual(refs, {self.sess.session_ref, self.sess_novoice.session_ref,
                                 self.sess_processing.session_ref, self.sess_failed.session_ref})

    def test_kpi_values_come_from_the_real_kpi_service(self):
        from drive_test.services.kpi import DriveTestKpiService
        expected = DriveTestKpiService(self.sess).compute()
        r = self.get(session=self.sess.session_ref)
        self.assertEqual(r.context['kpi'], expected)

    def test_missing_is_not_zero_for_sparse_session(self):
        r = self.get(session=self.sess_novoice.session_ref)
        kpi = r.context['kpi']
        # No call records at all -> these are genuinely unavailable, not zero.
        self.assertEqual(kpi['voice']['total_calls'], 0)
        self.assertIsNone(kpi['voice']['cssr_percent'])
        self.assertIsNone(kpi['voice']['dcr_percent'])
        self.assertIsNone(kpi['voice']['mean_mos'])
        self.assertIsNone(kpi['signal']['mean_rssi'])          # no RSSI recorded -> None, not 0
        self.assertContains(r, 'No voice call records available')
        self.assertContains(r, 'No RSSI data available')       # no RSSI recorded on that measurement

    def test_voice_kpis_reflect_real_records_not_manufactured(self):
        r = self.get(session=self.sess.session_ref)
        kpi = r.context['kpi']
        self.assertEqual(kpi['voice']['total_calls'], 2)
        self.assertEqual(kpi['voice']['connected'], 1)
        self.assertEqual(kpi['voice']['dropped'], 1)
        self.assertEqual(kpi['voice']['mos_sample_count'], 1)
        self.assertEqual(r.context['voice_failed'], 0)
        self.assertContains(r, '2 call attempt')

    def test_technology_distribution_only_shows_observed_technologies(self):
        r = self.get(session=self.sess.session_ref)
        dist = r.context['tech_dist']
        self.assertEqual([d['technology'] for d in dist], ['4G'])
        self.assertEqual(dist[0]['count'], 5)
        self.assertEqual(dist[0]['pct'], 100.0)
        self.assertNotContains(r, '5G')

    def test_by_technology_breakdown_uses_same_formulas(self):
        r = self.get(session=self.sess.session_ref)
        rows = r.context['by_tech']
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['technology'], '4G')
        self.assertEqual(row['measurements'], 5)
        self.assertEqual(row['voice']['total_calls'], 2)

    def test_by_cell_breakdown_and_unmatched_is_never_a_fake_cell(self):
        r = self.get(session=self.sess.session_ref)
        rows = {row['cell_code']: row for row in r.context['by_cell']}
        self.assertEqual(rows['LTE-001']['measurements'], 3)
        self.assertEqual(rows['LTE-002']['measurements'], 1)
        unmatched = [row for row in r.context['by_cell'] if row['cell_id'] is None]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]['measurements'], 1)
        self.assertContains(r, 'Unmatched')
        self.assertNotIn('AMBIGUOUS', [row.get('technology') for row in r.context['by_cell']])

    def test_data_quality_indicator_reuses_data_quality_band(self):
        from drive_test.services import data_quality as dq
        r = self.get(session=self.sess.session_ref)
        quality = r.context['quality']
        # No DataQualityResult has been computed for this file -> no score yet, honestly "no result".
        self.assertFalse(quality.qr_has_result)
        self.assertIsNone(quality.qr_score)

    def test_processing_session_shows_banner_not_zeros(self):
        r = self.get(session=self.sess_processing.session_ref)
        self.assertContains(r, 'PROCESSING')
        self.assertContains(r, 'still processing')
        self.assertIsNone(r.context['kpi'])

    def test_failed_session_shows_banner_not_zeros(self):
        r = self.get(session=self.sess_failed.session_ref)
        self.assertContains(r, 'PROCESSING FAILED')
        self.assertIsNone(r.context['kpi'])

    def test_filters_narrow_session_selector(self):
        r = self.get(operator='qcell')
        refs = {s.session_ref for s in r.context['sessions']}
        self.assertEqual(refs, {self.sess_processing.session_ref, self.sess_failed.session_ref})
        r = self.get(technology='3G')
        refs = {s.session_ref for s in r.context['sessions']}
        self.assertEqual(refs, {self.sess_novoice.session_ref})

    def test_unknown_session_ref_is_handled_without_500(self):
        r = self.get(session='DT-NOPE')
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context['kpi'])

    def test_empty_state_when_no_sessions_at_all(self):
        DriveTestSession.objects.all().delete()
        r = self.get()
        self.assertContains(r, 'No drive-test sessions available.')

    def test_speed_never_called_throughput(self):
        r = self.get(session=self.sess.session_ref)
        self.assertContains(r, 'Vehicle')
        self.assertNotContains(r, 'Throughput')
