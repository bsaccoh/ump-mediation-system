"""
Session Detail workspace tests. Fixtures live only in the isolated test DB; they exist to
exercise states the real session cannot (findings, processing, failure, matched cells).
"""
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import (
    Cell, DriveTestFile, DriveTestSession, Finding, Measurement, RadioMeasurement, Sector, Site,
)
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


class SessionDetailTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('sd', password='x')
        cls.op = Operator.objects.create(code='orange', name='Orange Sierra Leone',
                                         home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.site = Site.objects.create(site_id='S1', operator=cls.op, name='Freetown-001')
        cls.sector = Sector.objects.create(site=cls.site, sector_id='A')
        cls.cell = Cell.objects.create(cell_id='Orange-LTE-001', operator=cls.op, sector=cls.sector,
                                       technology='4G')

        cls.session = DriveTestSession.objects.create(
            operator=cls.op, test_date='2026-09-24', uploaded_by=cls.user,
            status='COMPLETED', total_measurements=3, title='<b>x</b>')
        f = DriveTestFile.objects.create(session=cls.session, original_filename='a.trp', file_path='p',
                                         file_size=1, sha256='a' * 64, status='COMPLETED')
        cls.ms = []
        for i, (lat, cell) in enumerate([(8.48, cls.cell), (8.49, None), (0.0, None)], start=1):
            m = Measurement.objects.create(
                drive_file=f, sequence_num=i, captured_at=datetime(2026, 9, 24, 12, 0, i),
                latitude=lat, longitude=-13.2 if lat else 0.0, matched_cell=cell,
                match_method='exact_cgi' if cell else '')
            RadioMeasurement.objects.create(measurement=m, technology='4G' if i < 3 else '2G',
                                            rssi=-67.0 if i == 1 else None)
            cls.ms.append(m)
        cls.finding = Finding.objects.create(
            session=cls.session, measurement=cls.ms[0], finding_type='WEAK_SIGNAL', severity='HIGH',
            cell=cls.cell, description='Poor LTE coverage', measured_value=-118.0, threshold_value=-110.0)

    def setUp(self):
        self.client.force_login(self.user)
        self.ref = self.session.session_ref

    @STATIC
    def test_page_escapes_and_shows_real_values(self):
        r = self.client.get(reverse('drive_test:session_detail', args=[self.ref]) + '?back=q%3Dx')
        self.assertContains(r, self.ref)
        self.assertContains(r, 'Orange Sierra Leone')
        self.assertContains(r, 'Download Report')
        self.assertContains(r, 'href="/drive-test/sessions/?q=x"')
        self.assertNotContains(r, '<b>x</b>')          # title is escaped
        self.assertContains(r, '2G / 4G')              # technologies from measurements
        self.assertEqual(r.context['gps_points'], 2)   # (0,0) sentinel excluded
        self.assertEqual(r.context['matched_count'], 1)
        self.assertEqual(r.context['unmatched_count'], 2)

    @STATIC
    def test_processing_session_shows_banner_and_no_tabs(self):
        s = DriveTestSession.objects.create(operator=self.op, test_date='2026-09-24',
                                            uploaded_by=self.user, status='PROCESSING')
        DriveTestFile.objects.create(session=s, original_filename='b.trp', file_path='p', file_size=1,
                                     sha256='b' * 64, status='MATCHING')
        r = self.client.get(reverse('drive_test:session_detail', args=[s.session_ref]))
        self.assertContains(r, 'still being processed')
        self.assertContains(r, 'Report unavailable')
        self.assertNotContains(r, 'dt-sd-tabs')

    @STATIC
    def test_failed_session_shows_safe_reason(self):
        s = DriveTestSession.objects.create(operator=self.op, test_date='2026-09-24',
                                            uploaded_by=self.user, status='FAILED')
        DriveTestFile.objects.create(session=s, original_filename='c.trp', file_path='p', file_size=1,
                                     sha256='c' * 64, status='FAILED',
                                     error_message='FileNotFoundError: C:\\secret\\dir\\c.trp\nTraceback...')
        r = self.client.get(reverse('drive_test:session_detail', args=[s.session_ref]))
        self.assertContains(r, 'PROCESSING FAILED')
        self.assertNotContains(r, 'secret')
        self.assertNotContains(r, 'Traceback')

    def test_findings_endpoint_evidence_and_lineage(self):
        d = self.client.get(reverse('drive_test:session_findings', args=[self.ref])).json()
        self.assertEqual(d['total'], 1)
        f = d['rows'][0]
        self.assertEqual((f['severity'], f['category'], f['technology']), ('HIGH', 'Weak Signal', '4G'))
        self.assertEqual((f['cell'], f['sector'], f['site'], f['operator']),
                         ('Orange-LTE-001', 'A', 'Freetown-001', 'Orange Sierra Leone'))
        self.assertEqual(f['measurement_seq'], 1)
        self.assertEqual(f['measured_value'], -118.0)
        self.assertEqual((f['lat'], f['lon']), (8.48, -13.2))   # falls back to the measurement's position

    def test_map_data_includes_finding_layer_data(self):
        d = self.client.get(reverse('drive_test:session_map_data', args=[self.ref])).json()
        kinds = [x['properties']['ftype'] for x in d['features']]
        self.assertEqual(kinds.count('measurement'), 2)   # (0,0) excluded
        self.assertEqual(kinds.count('finding'), 1)

    def test_measurement_filters_and_page_sizes(self):
        url = reverse('drive_test:session_measurements', args=[self.ref])
        self.assertEqual(self.client.get(url).json()['total'], 3)
        self.assertEqual(self.client.get(url, {'tech': '2G'}).json()['total'], 1)
        self.assertEqual(self.client.get(url, {'cell': 'lte-001'}).json()['total'], 1)
        self.assertEqual(self.client.get(url, {'time_from': '12:00:02'}).json()['total'], 2)
        self.assertEqual(self.client.get(url, {'time_to': '12:00:01'}).json()['total'], 1)
        self.assertEqual(self.client.get(url, {'per_page': '7'}).json()['per_page'], 50)
        row = self.client.get(url).json()['rows'][0]
        self.assertEqual((row['cell_id'], row['match'], row['rsrp']), ('Orange-LTE-001', 'matched', None))

    def test_endpoints_require_login(self):
        self.client.logout()
        for n in ('session_findings', 'session_measurements', 'session_map_data', 'session_kpis'):
            self.assertEqual(self.client.get(reverse(f'drive_test:{n}', args=[self.ref])).status_code, 302)
