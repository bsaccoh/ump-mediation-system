"""Technology Analysis & Operator Comparison workspace tests."""
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import (
    Cell, Chiefdom, District, DriveTestFile, DriveTestSession, Measurement,
    RadioMeasurement, Region, Sector, ServiceMeasurement, Site,
)
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@STATIC
class OperatorAnalysisTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.user = U.objects.create_user('viewer', password='x')
        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.qcell = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')

        cls.region = Region.objects.create(code='W', name='Western Area')
        cls.district = District.objects.create(code='FT', name='Freetown', region=cls.region)
        cls.chiefdom = Chiefdom.objects.create(code='C1', name='Central', district=cls.district)
        cls.site = Site.objects.create(site_id='FT-001', name='Freetown Central', operator=cls.orange,
                                       latitude=8.48, longitude=-13.23, chiefdom=cls.chiefdom)
        cls.sec = Sector.objects.create(site=cls.site, sector_id='A')
        cls.cell_4g = Cell.objects.create(cell_id='LTE-001', operator=cls.orange, sector=cls.sec, technology='4G')
        cls.cell_3g = Cell.objects.create(cell_id='UMTS-001', operator=cls.orange, sector=cls.sec, technology='3G')

        # ── Orange session: 4G (3 meas, matched, 1 voice) + 3G (2 meas, matched) ──
        cls.sess_orange = DriveTestSession.objects.create(
            operator=cls.orange, test_date='2026-09-24', uploaded_by=cls.user, status='COMPLETED')
        f1 = DriveTestFile.objects.create(
            session=cls.sess_orange, original_filename='o.trp', file_path='p', file_size=1, sha256='a' * 64, status='COMPLETED')

        def meas(file, seq, tech, cell=None, rssi=None, speed=None):
            m = Measurement.objects.create(
                drive_file=file, sequence_num=seq, captured_at=datetime(2026, 9, 24, 12, 0, seq),
                latitude=8.48, longitude=-13.23, matched_cell=cell, speed_kmh=speed,
                match_method='exact_ecgi' if cell else '')
            RadioMeasurement.objects.create(measurement=m, technology=tech, rssi=rssi)
            return m

        m1 = meas(f1, 1, '4G', cls.cell_4g, rssi=-80.0, speed=40.0)
        meas(f1, 2, '4G', cls.cell_4g, rssi=-90.0, speed=30.0)
        meas(f1, 3, '4G', cls.cell_4g, rssi=-80.0)
        meas(f1, 4, '3G', cls.cell_3g, rssi=-100.0)
        meas(f1, 5, '3G', cls.cell_3g, rssi=-95.0)
        ServiceMeasurement.objects.create(measurement=m1, service_type='VOICE', outcome='SUCCESS', mos=4.0)

        # ── Qcell session: 4G only (2 meas, 1 unmatched) ──
        cls.sess_qcell = DriveTestSession.objects.create(
            operator=cls.qcell, test_date='2026-09-20', uploaded_by=cls.user, status='COMPLETED')
        f2 = DriveTestFile.objects.create(
            session=cls.sess_qcell, original_filename='q.trp', file_path='p', file_size=1, sha256='b' * 64, status='COMPLETED')
        meas(f2, 1, '4G', None, rssi=-70.0)
        meas(f2, 2, '4G', None, rssi=-70.0)

        cls.url = reverse('drive_test:operator_analysis')

    def setUp(self):
        self.client.force_login(self.user)

    def get(self, **q):
        return self.client.get(self.url, q)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.get().status_code, 302)

    def test_page_loads_with_real_scope(self):
        r = self.get()
        self.assertEqual(r.status_code, 200)
        scope = r.context['scope']
        self.assertEqual(scope['measurements'], 7)
        self.assertEqual(scope['sessions'], 2)
        self.assertEqual(scope['operators'], 2)
        self.assertEqual(scope['technologies'], 2)

    def test_operator_summary_uses_real_data_only(self):
        rows = {r['code']: r for r in self.get().context['operator_summary']}
        self.assertEqual(rows['orange']['measurements'], 5)
        self.assertEqual(rows['orange']['technologies'], 2)
        self.assertEqual(rows['orange']['calls'], 1)
        self.assertEqual(rows['qcell']['measurements'], 2)
        self.assertEqual(rows['qcell']['calls'], 0)
        # SierraTel/Africell/One Mobile were never uploaded -> never appear.
        self.assertNotIn('sierratel', rows)
        self.assertNotIn('africell', rows)

    def test_technology_summary_only_shows_observed_technologies(self):
        rows = {r['technology']: r['measurements'] for r in self.get().context['technology_summary']}
        self.assertEqual(rows, {'4G': 5, '3G': 2})
        self.assertNotIn('5G', rows)
        self.assertNotIn('2G', rows)

    def test_kpi_values_reuse_the_real_kpi_service_formulas(self):
        from drive_test.services.kpi import DriveTestKpiService as KPI
        from drive_test.models import Measurement

        r = self.get()
        orange_row = next(x for x in r.context['operator_kpis'] if x['operator'].code == 'orange')
        expected_meas = Measurement.objects.filter(drive_file__session__operator=self.orange, is_valid=True)
        expected = KPI._signal_kpis(expected_meas, expected_meas.count())
        self.assertEqual(orange_row['signal'], expected)
        self.assertEqual(orange_row['voice']['total_calls'], 1)
        self.assertEqual(orange_row['voice']['cssr_percent'], 100.0)

    def test_operators_and_technologies_are_never_ranked_or_scored(self):
        r = self.get()
        codes = [row['operator'].code for row in r.context['operator_kpis']]
        self.assertEqual(codes, sorted(codes))  # alphabetical, not sorted by any KPI
        self.assertNotContains(r, 'Winner')
        self.assertNotContains(r, 'Best Operator')
        self.assertNotContains(r, 'Rank')
        self.assertNotContains(r, 'PASS')
        self.assertNotContains(r, 'FAIL')
        self.assertNotContains(r, 'COMPLIANT')

    def test_operator_technology_matrix_dash_means_no_data_not_zero(self):
        matrix = self.get(metric='measurements').context['matrix']
        self.assertEqual(matrix['technologies'], ['3G', '4G'])
        by_op = {row['operator'].code: {c['technology']: c for c in row['cells']} for row in matrix['rows']}
        self.assertEqual(by_op['orange']['4G']['value'], 3)
        self.assertEqual(by_op['orange']['3G']['value'], 2)
        self.assertIsNone(by_op['qcell']['3G']['value'])   # Qcell never tested 3G here -> dash, not 0
        self.assertEqual(by_op['qcell']['4G']['value'], 2)

    def test_matrix_metric_selector_switches_values(self):
        matrix = self.get(metric='coverage').context['matrix']
        by_op = {row['operator'].code: {c['technology']: c for c in row['cells']} for row in matrix['rows']}
        # Orange 4G: rssi -80,-90,-80 -> good/fair/good => 3/3 covered = 100%
        self.assertEqual(by_op['orange']['4G']['value'], 100.0)

    def test_geographic_breakdown_excludes_unmatched(self):
        rows = self.get().context['geographic']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['region'], self.region)
        # 5 Orange measurements are matched to a site in Western Area; Qcell's 2 are unmatched.
        self.assertEqual(rows[0]['measurements'], 5)

    def test_filters_operator_and_technology(self):
        r = self.get(operator='qcell')
        self.assertEqual(r.context['scope']['measurements'], 2)
        codes = [row['code'] for row in r.context['operator_summary']]
        self.assertEqual(codes, ['qcell'])

        r = self.get(technology='3G')
        self.assertEqual(r.context['scope']['measurements'], 2)
        self.assertEqual([row['technology'] for row in r.context['technology_summary']], ['3G'])

    def test_invalid_region_filter_is_reported_not_500(self):
        r = self.get(region='not-a-number')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Unrecognised region.', r.context['errors'])
        self.assertFalse(r.context['has_any'])

    def test_empty_state_when_filters_match_nothing(self):
        r = self.get(operator='orange', technology='5G')
        self.assertContains(r, 'No comparison data available.')

    def test_empty_state_when_no_sessions_at_all(self):
        DriveTestSession.objects.all().delete()
        r = self.get()
        self.assertContains(r, 'No drive-test sessions available.')

    def test_data_quality_context_reuses_data_quality_not_a_new_score(self):
        # No DataQualityResult has been computed for any file in this fixture yet.
        ctx = self.get().context['quality_context']
        self.assertIsNone(ctx)

    def test_default_tab_is_technology_and_can_switch(self):
        r = self.get()
        self.assertEqual(r.context['active_tab'], 'technology')
        r = self.get(tab='operator')
        self.assertEqual(r.context['active_tab'], 'operator')
        r = self.get(tab='bogus')
        self.assertEqual(r.context['active_tab'], 'technology')
