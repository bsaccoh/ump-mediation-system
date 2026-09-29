"""GIS / Coverage tests. Fixtures exist only in the isolated test DB."""
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import (
    Cell, Chiefdom, District, DriveTestFile, DriveTestSession, Finding, Measurement, RadioMeasurement, Region, Sector, Site,
)
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@STATIC
class GisTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('viewer', password='x')
        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.qcell = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')
        cls.reg = Region.objects.create(code='T-W', name='Western Area')
        cls.reg2 = Region.objects.create(code='T-N', name='North')
        cls.dis = District.objects.create(code='T-WU', name='Western Urban', region=cls.reg)
        cls.chief = Chiefdom.objects.create(code='T-C', name='Central', district=cls.dis)
        cls.site = Site.objects.create(site_id='FT-001', name='Freetown Central', operator=cls.orange,
                                       latitude=8.48, longitude=-13.23, chiefdom=cls.chief)
        cls.nosite = Site.objects.create(site_id='X-000', name='No GPS', operator=cls.orange)
        cls.zero = Site.objects.create(site_id='Z-000', name='Zero', operator=cls.orange, latitude=0.0, longitude=0.0)
        cls.sec = Sector.objects.create(site=cls.site, sector_id='A', azimuth_deg=120.0)
        Sector.objects.create(site=cls.site, sector_id='B')                       # no azimuth: not drawable
        cls.cell = Cell.objects.create(cell_id='LTE-001', operator=cls.orange, sector=cls.sec, technology='4G',
                                       latitude=8.481, longitude=-13.231)
        Cell.objects.create(cell_id='LTE-NOGPS', operator=cls.orange, sector=cls.sec, technology='4G')

        cls.s1 = DriveTestSession.objects.create(operator=cls.orange, test_date='2026-09-24', uploaded_by=cls.user, status='COMPLETED', total_measurements=5)
        cls.s2 = DriveTestSession.objects.create(operator=cls.qcell, test_date='2026-08-01', uploaded_by=cls.user, status='PROCESSING', total_measurements=1)
        cls.f1 = DriveTestFile.objects.create(session=cls.s1, original_filename='a.trp', file_path='p', file_size=1, sha256='a' * 64, status='COMPLETED')
        cls.f2 = DriveTestFile.objects.create(session=cls.s2, original_filename='b.trp', file_path='p', file_size=1, sha256='b' * 64, status='MATCHING')

        def meas(file, seq, lat, lon, sec, cell=None, tech='4G', **radio):
            m = Measurement.objects.create(drive_file=file, sequence_num=seq, captured_at=datetime(2026, 9, 24, 12, 0, sec) if sec < 60 else datetime(2026, 9, 24, 12, 50, 0),
                                           latitude=lat, longitude=lon, matched_cell=cell, match_method='exact_ecgi' if cell else '',
                                           match_confidence=1.0 if cell else None)
            RadioMeasurement.objects.create(measurement=m, technology=tech, **radio)
            return m

        cls.m1 = meas(cls.f1, 1, 8.4841, -13.2299, 1, cls.cell, rsrp=-95.0, sinr=18.0)
        cls.m2 = meas(cls.f1, 2, 8.4842, -13.2298, 2, None, tech='2G', rssi=-101.0)     # unmatched
        cls.m3 = meas(cls.f1, 3, 0.0, 0.0, 3, None, tech='2G', rssi=-80.0)              # no location
        cls.m4 = meas(cls.f1, 4, 8.4843, -13.2297, 4, None, tech='2G')                   # no metric values
        cls.bad = meas(cls.f1, 5, 8.5, -13.2, 5, None)
        Measurement.objects.filter(pk=cls.bad.pk).update(is_valid=False)
        cls.m5 = meas(cls.f1, 6, 8.4900, -13.2200, 3000, None, tech='2G', rssi=-90.0)   # >5 min later: new route segment
        cls.m6 = meas(cls.f2, 1, 7.9, -11.7, 6, None, tech='3G')                         # file still matching
        cls.fd = Finding.objects.create(session=cls.s1, finding_type='WEAK_SIGNAL', severity='HIGH', description='weak', measurement=cls.m1)
        Finding.objects.create(session=cls.s1, finding_type='ANOMALY', severity='LOW', description='no place')
        cls.url = reverse('drive_test:gis')
        cls.data = reverse('drive_test:gis_data')

    def setUp(self):
        self.client.force_login(self.user)

    def layer(self, layer, **q):
        r = self.client.get(self.data, {'layer': layer, **q})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def ids(self, **q):
        return sorted(f['properties']['id'] for f in self.layer('measurements', **q)['features'])

    def test_page_and_auth(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'GIS / Coverage')
        self.assertContains(r, 'Explore drive-test measurements, routes, network reference data and findings geographically.')
        self.assertContains(r, self.s1.session_ref)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.get(self.data, {'layer': 'summary'}).status_code, 302)

    def test_measurements_are_valid_and_located_only(self):
        d = self.layer('measurements')
        self.assertEqual(d['type'], 'FeatureCollection')
        ids = self.ids()
        self.assertEqual(ids, sorted([self.m1.pk, self.m2.pk, self.m4.pk, self.m5.pk, self.m6.pk]))
        self.assertNotIn(self.m3.pk, ids)      # (0, 0) is not a location
        self.assertNotIn(self.bad.pk, ids)     # parser-invalid records are never plotted
        for f in d['features']:
            lon, lat = f['geometry']['coordinates']
            self.assertTrue(-90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0))

    def test_feature_properties_are_real_and_sparse(self):
        by = {f['properties']['id']: f['properties'] for f in self.layer('measurements')['features']}
        p = by[self.m1.pk]
        self.assertEqual((p['t'], p['op'], p['tech'], p['match'], p['cell'], p['method'], p['conf']),
                         ('2026-09-24 12:00:01', 'Orange SL', '4G', 'MATCHED', 'LTE-001', 'exact_ecgi', 1.0))
        self.assertEqual(p['m'], {'rsrp': -95.0, 'sinr': 18.0})
        self.assertEqual(by[self.m2.pk]['match'], 'UNMATCHED')
        self.assertNotIn('cell', by[self.m2.pk])
        self.assertNotIn('m', by[self.m4.pk])          # nothing invented for a point without values
        self.assertEqual(by[self.m6.pk]['match'], 'UNKNOWN')   # not treated as unmatched while matching is pending
        self.assertNotIn('AMBIGUOUS', str(by))

    def test_filters(self):
        self.assertEqual(self.ids(session=self.s2.session_ref), [self.m6.pk])
        self.assertEqual(self.ids(operator='qcell'), [self.m6.pk])
        self.assertEqual(self.ids(technology='3G'), [self.m6.pk])
        self.assertEqual(self.ids(technology='4G'), [self.m1.pk])
        self.assertEqual(self.ids(date_from='2026-09-24T12:00:10'), [self.m5.pk])

    def test_region_district_follow_matched_cell_and_site(self):
        self.assertEqual(self.ids(region=self.reg.pk), [self.m1.pk])       # unmatched points get no region
        self.assertEqual(self.ids(district=self.dis.pk), [self.m1.pk])
        self.assertEqual(self.ids(region=self.reg2.pk), [])

    def test_route_segments_never_join_unrelated_fixes(self):
        route = self.layer('measurements')['route']
        self.assertEqual(len(route), 1)                       # f1: m1,m2,m4 joined; m5 is >5 min later; f2 has one fix
        self.assertEqual(len(route[0]), 3)
        only_one = self.layer('measurements', session=self.s2.session_ref)['route']
        self.assertEqual(only_one, [])                        # a single fix is not a route

    def test_bbox_and_thinning(self):
        self.assertEqual(self.ids(bbox='-13.24,8.47,-13.22,8.488'), sorted([self.m1.pk, self.m2.pk, self.m4.pk]))
        self.assertEqual(self.layer('measurements', bbox='bad')['shown'], 5)     # invalid bbox ignored
        from drive_test import views
        old = views.GIS_MAX_MEASUREMENTS
        views.GIS_MAX_MEASUREMENTS = 2
        try:
            d = self.layer('measurements')
        finally:
            views.GIS_MAX_MEASUREMENTS = old
        self.assertTrue(d['thinned'])
        self.assertLessEqual(d['shown'], 2)
        self.assertEqual(d['total_located'], 5)

    def test_sites_cells_sectors_only_with_real_locations(self):
        sites = self.layer('sites')['features']
        self.assertEqual([s['properties']['site_id'] for s in sites], ['FT-001'])       # no-GPS and (0,0) sites excluded
        self.assertEqual(sites[0]['properties']['region'], 'Western Area')
        self.assertEqual([c['properties']['cell_id'] for c in self.layer('cells')['features']], ['LTE-001'])
        sec = self.layer('sectors')['features']
        self.assertEqual([s['properties']['sector_id'] for s in sec], ['A'])
        self.assertEqual(sec[0]['geometry']['coordinates'], [-13.23, 8.48])               # the site's location, labelled as such
        self.assertEqual(self.layer('sites', region=self.reg2.pk)['features'], [])
        self.assertEqual(self.layer('cells', technology='3G')['features'], [])
        self.assertEqual(len(self.layer('sites', operator='qcell')['features']), 0)
        self.assertEqual(self.layer('sites', bbox='10,10,11,11')['features'], [])

    def test_findings_layer_uses_real_locations(self):
        feats = self.layer('findings')['features']
        self.assertEqual([f['properties']['id'] for f in feats], [self.fd.pk])            # the finding with no place is not plotted
        p = feats[0]['properties']
        self.assertEqual((p['severity'], p['cell'], p['sess'], p['site']), ('HIGH', 'LTE-001', self.s1.session_ref, 'Freetown Central'))
        self.assertEqual(self.layer('findings', session=self.s2.session_ref)['features'], [])

    def test_summary_counts_are_computed(self):
        s = self.layer('summary')
        self.assertEqual((s['total'], s['mapped'], s['without_location'], s['matched'], s['unmatched'], s['unknown'], s['invalid']),
                         (6, 5, 1, 1, 4, 1, 1))
        self.assertEqual((s['sites'], s['cells'], s['sectors']), (1, 1, 1))
        self.assertEqual((s['findings_total'], s['findings_located']), (2, 1))
        s2 = self.layer('summary', session=self.s2.session_ref)
        self.assertEqual((s2['total'], s2['mapped'], s2['unmatched'], s2['unknown']), (1, 1, 0, 1))

    def test_empty_and_bad_input(self):
        self.assertEqual(self.ids(cell='NOPE'), [])
        s = self.layer('summary', operator='qcell', technology='4G')
        self.assertEqual((s['total'], s['mapped']), (0, 0))
        r = self.client.get(self.data, {'layer': 'nope'})
        self.assertEqual(r.status_code, 400)
        r = self.client.get(self.data, {'layer': 'measurements', 'date_from': 'yesterday'})
        self.assertEqual(r.json()['features'], [])

    def test_failure_returns_friendly_error(self):
        from unittest import mock
        with mock.patch('drive_test.views._gis_route', side_effect=RuntimeError('boom /etc/x')):
            r = self.client.get(self.data, {'layer': 'measurements'})
        self.assertEqual(r.status_code, 500)
        self.assertNotIn('boom', r.content.decode())

    def test_constant_query_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count(layer):
            with CaptureQueriesContext(connection) as c:
                self.client.get(self.data, {'layer': layer})
            return len(c)
        before = {l: count(l) for l in ('measurements', 'sites', 'cells', 'sectors', 'findings')}
        for i in range(20):
            m = Measurement.objects.create(drive_file=self.f1, sequence_num=100 + i, captured_at=datetime(2026, 9, 24, 15, 0, i),
                                           latitude=8.4, longitude=-13.2, matched_cell=self.cell)
            RadioMeasurement.objects.create(measurement=m, technology='4G', rsrp=-90.0)
            Finding.objects.create(session=self.s1, finding_type='WEAK_SIGNAL', severity='LOW', description='x', measurement=m)
            Site.objects.create(site_id='S%d' % i, name='s', operator=self.orange, latitude=8.4, longitude=-13.2)
        self.assertEqual({l: count(l) for l in before}, before)
