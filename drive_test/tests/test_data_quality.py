"""Data Quality workspace tests. Fixtures exist only in the isolated test DB."""
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import (
    Cell, DataQualityResult, DriveTestFile, DriveTestSession, Measurement,
    RadioMeasurement, Sector, Site,
)
from drive_test.services.analysis import QualityAssessor
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@STATIC
class DataQualityTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.user = U.objects.create_user('viewer', password='x')
        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.qcell = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')
        cls.site = Site.objects.create(site_id='FT-001', name='Freetown Central', operator=cls.orange, latitude=8.48, longitude=-13.23)
        cls.sec = Sector.objects.create(site=cls.site, sector_id='A')
        cls.cell = Cell.objects.create(cell_id='LTE-001', operator=cls.orange, sector=cls.sec, technology='4G')

        # ── Session 1: Orange, mostly good — 3 valid+matched, 1 valid+unmatched, 1 no-GPS ──
        cls.sess = DriveTestSession.objects.create(
            operator=cls.orange, test_date='2026-09-24', uploaded_by=cls.user, status='COMPLETED')
        cls.file1 = DriveTestFile.objects.create(
            session=cls.sess, original_filename='walk_001.trp', file_path='p', file_size=1,
            sha256='a' * 64, status='COMPLETED')

        def meas(file, seq, lat, lon, cell=None, tech='4G', valid=True, with_ids=True):
            m = Measurement.objects.create(
                drive_file=file, sequence_num=seq, captured_at=datetime(2026, 9, 24, 12, 0, seq),
                latitude=lat, longitude=lon, matched_cell=cell, is_valid=valid,
                match_method='exact_ecgi' if cell else '',
                # Realistic observed identifiers — present even when unmatched (e.g. an
                # identifier the reference table doesn't know), so "missing identifiers"
                # only flags records that genuinely carry none.
                obs_mcc='619' if with_ids else '', obs_mnc='01' if with_ids else '',
                obs_lac=100 if with_ids else None, obs_ci=(seq if with_ids else None))
            RadioMeasurement.objects.create(measurement=m, technology=tech, rsrp=-95.0)
            return m

        meas(cls.file1, 1, 8.48, -13.23, cls.cell)
        meas(cls.file1, 2, 8.49, -13.24, cls.cell)
        meas(cls.file1, 3, 8.50, -13.25, cls.cell)
        meas(cls.file1, 4, 8.51, -13.26, None)                          # valid, unmatched, identifiers present
        meas(cls.file1, 5, 0.0, 0.0, None, with_ids=False)              # no GPS, no identifiers -> genuinely incomplete
        cls.qr1 = QualityAssessor().assess(cls.file1)

        # ── Session 2: Qcell, still processing — no DataQualityResult yet ──
        cls.sess_processing = DriveTestSession.objects.create(
            operator=cls.qcell, test_date='2026-09-20', uploaded_by=cls.user, status='PROCESSING')
        DriveTestFile.objects.create(
            session=cls.sess_processing, original_filename='p.trp', file_path='p', file_size=1,
            sha256='b' * 64, status='PARSING')

        # ── Session 3: Qcell, failed — no DataQualityResult ──
        cls.sess_failed = DriveTestSession.objects.create(
            operator=cls.qcell, test_date='2026-09-18', uploaded_by=cls.user, status='FAILED')
        DriveTestFile.objects.create(
            session=cls.sess_failed, original_filename='f.trp', file_path='p', file_size=1,
            sha256='c' * 64, status='FAILED', error_message='boom')

        cls.url = reverse('drive_test:data_quality')

    def setUp(self):
        self.client.force_login(self.user)

    def refs(self, **q):
        return sorted(s.session_ref for s in self.client.get(self.url, q).context['rows'])

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_page_loads_and_lists_all_sessions(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.refs(), sorted([self.sess.session_ref, self.sess_processing.session_ref, self.sess_failed.session_ref]))

    def test_summary_uses_real_backend_values(self):
        s = self.client.get(self.url).context['summary']
        self.assertEqual(s['sessions_assessed'], 1)          # only sess has a DataQualityResult
        self.assertEqual(s['measurements_assessed'], 5)
        self.assertEqual(s['invalid_records'], 0)
        self.assertEqual(s['unmatched_measurements'], 2)     # unmatched + no-GPS
        self.assertGreaterEqual(s['quality_issues'], 1)      # at least "missing GPS" / "unmatched"

    def test_session_row_rollup_matches_quality_assessor(self):
        rows = {s.session_ref: s for s in self.client.get(self.url).context['rows']}
        row = rows[self.sess.session_ref]
        self.assertTrue(row.qr_has_result)
        self.assertEqual(row.qr_valid, self.qr1.valid_records)
        self.assertEqual(row.qr_matched, self.qr1.matched_cells)
        self.assertEqual(row.qr_unmatched, self.qr1.unmatched_cells)
        # Same formula QualityAssessor itself used, reapplied at (single-file) session level.
        self.assertAlmostEqual(row.qr_score, self.qr1.overall_score, places=2)

    def test_processing_and_failed_sessions_are_not_shown_as_scored(self):
        rows = {s.session_ref: s for s in self.client.get(self.url).context['rows']}
        proc = rows[self.sess_processing.session_ref]
        failed = rows[self.sess_failed.session_ref]
        self.assertFalse(proc.qr_has_result)
        self.assertTrue(proc.qr_is_processing)
        self.assertIsNone(proc.qr_score)
        self.assertFalse(failed.qr_has_result)
        self.assertTrue(failed.qr_is_failed)
        r = self.client.get(self.url)
        self.assertContains(r, 'PROCESSING')
        self.assertContains(r, 'FAILED')

    def test_filters(self):
        self.assertEqual(self.refs(operator='qcell'), sorted([self.sess_processing.session_ref, self.sess_failed.session_ref]))
        self.assertEqual(self.refs(technology='4G'), [self.sess.session_ref])
        self.assertEqual(self.refs(session=self.sess.session_ref[:8].lower()), [self.sess.session_ref])
        self.assertEqual(self.refs(date_from='2026-09-19', date_to='2026-09-21'), [self.sess_processing.session_ref])
        # completeness=(5-0-1)/5*100=80, accuracy=3/5*100=60 -> overall=70 -> WARNING band (50-80).
        self.assertEqual(self.refs(status='warning'), [self.sess.session_ref])
        self.assertEqual(self.refs(status='good'), [])
        self.assertEqual(self.refs(status='pending'), sorted([self.sess_processing.session_ref, self.sess_failed.session_ref]))
        self.assertEqual(self.refs(status='bogus'), [])                 # unrecognised -> none, reported as an error
        self.assertIn('Unrecognised quality status.', self.client.get(self.url, {'status': 'bogus'}).context['errors'])
        self.assertEqual(self.refs(date_from='garbage'), [])            # never a 500

    def test_issue_rows_are_derived_from_stored_fields_only(self):
        issues = self.client.get(self.url).context['issues']
        by_label = {i['issue']: i for i in issues}
        self.assertIn('Missing GPS coordinates', by_label)
        self.assertEqual(by_label['Missing GPS coordinates']['count'], self.qr1.missing_gps)
        self.assertIn('Unmatched measurements', by_label)
        self.assertEqual(by_label['Unmatched measurements']['count'], self.qr1.unmatched_cells)
        self.assertNotIn('Invalid records', by_label)      # 0 invalid records -> not shown as an issue

    def test_no_issues_empty_state_is_honest(self):
        Measurement.objects.filter(drive_file=self.file1, sequence_num__in=[4, 5]).delete()
        QualityAssessor().assess(self.file1)
        r = self.client.get(self.url, {'session': self.sess.session_ref})
        self.assertContains(r, 'No data-quality issues detected')

    def test_category_breakdown_uses_real_match_states_no_ambiguous(self):
        r = self.client.get(self.url, {'session': self.sess.session_ref})
        cat = r.context['category']
        self.assertEqual(cat['reference']['matched'], 3)
        self.assertEqual(cat['reference']['unmatched'], 2)
        self.assertEqual(cat['reference']['unknown'], 0)
        self.assertNotIn('ambiguous', cat['reference'])
        self.assertNotContains(r, 'AMBIGUOUS')

    def test_session_detail_json_endpoint(self):
        url = reverse('drive_test:data_quality_session_detail', args=[self.sess.session_ref])
        d = self.client.get(url).json()
        self.assertEqual(d['session_ref'], self.sess.session_ref)
        self.assertEqual(len(d['files']), 1)
        self.assertEqual(d['files'][0]['filename'], 'walk_001.trp')
        self.assertEqual(d['files'][0]['quality']['matched'], self.qr1.matched_cells)
        self.assertIn('cell_matching_url', d)
        self.assertIn('gis_url', d)

    def test_session_detail_json_requires_login(self):
        self.client.logout()
        url = reverse('drive_test:data_quality_session_detail', args=[self.sess.session_ref])
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_unknown_session_404s(self):
        url = reverse('drive_test:data_quality_session_detail', args=['DT-NOPE'])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_empty_state_when_no_sessions_at_all(self):
        DriveTestSession.objects.all().delete()
        r = self.client.get(self.url)
        self.assertContains(r, 'No drive-test sessions available.')

    def test_filtered_empty_state(self):
        r = self.client.get(self.url, {'q': 'zzzzzz'})
        self.assertContains(r, 'No quality results match your filters.')
