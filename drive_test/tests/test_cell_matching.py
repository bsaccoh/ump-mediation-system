"""Cell Matching workspace tests. Fixtures exist only in the isolated test DB."""
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import (
    Cell, Chiefdom, District, DriveTestFile, DriveTestSession, Measurement, RadioMeasurement, Region, Sector, Site,
)
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@STATIC
class CellMatchingTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('viewer', password='x')
        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.qcell = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')
        cls.reg = Region.objects.create(code='T-W', name='Western Area')
        cls.reg2 = Region.objects.create(code='T-N', name='North')
        cls.dis = District.objects.create(code='T-WU', name='Western Urban', region=cls.reg)
        chief = Chiefdom.objects.create(code='T-C', name='Central', district=cls.dis)
        cls.site = Site.objects.create(site_id='FT-001', name='Freetown Central', operator=cls.orange,
                                       latitude=8.48, longitude=-13.23, chiefdom=chief)
        cls.sec = Sector.objects.create(site=cls.site, sector_id='A')
        cls.cell = Cell.objects.create(cell_id='LTE-001', operator=cls.orange, sector=cls.sec, technology='4G')

        cls.s1 = DriveTestSession.objects.create(operator=cls.orange, test_date='2026-09-24', uploaded_by=cls.user, status='COMPLETED', total_measurements=5)
        cls.s2 = DriveTestSession.objects.create(operator=cls.qcell, test_date='2026-08-01', uploaded_by=cls.user, status='PROCESSING', total_measurements=1)
        cls.f1 = DriveTestFile.objects.create(session=cls.s1, original_filename='a.trp', file_path='p', file_size=1, sha256='a' * 64, status='COMPLETED')
        cls.f2 = DriveTestFile.objects.create(session=cls.s2, original_filename='b.trp', file_path='p', file_size=1, sha256='b' * 64, status='MATCHING')

        def meas(file, seq, sec, cell=None, method='', conf=None, tech='4G', **kw):
            m = Measurement.objects.create(drive_file=file, sequence_num=seq, captured_at=datetime(2026, 9, 24, 12, 0, sec), latitude=8.48,
                                           longitude=-13.23, matched_cell=cell, match_method=method, match_confidence=conf, **kw)
            RadioMeasurement.objects.create(measurement=m, technology=tech)
            return m

        cls.m1 = meas(cls.f1, 1, 1, cls.cell, 'exact_ecgi', 1.0, obs_mcc='619', obs_mnc='01', obs_eci=1234)
        cls.m2 = meas(cls.f1, 2, 2, cls.cell, 'pci_earfcn', 0.85, obs_pci=77, obs_earfcn=1650)
        cls.m3 = meas(cls.f1, 3, 3, None, tech='2G', obs_mcc='619', obs_mnc='01', obs_lac=20028, obs_ci=10102)   # identifiers, no match
        cls.m4 = meas(cls.f1, 4, 4, None, tech='2G')                                                          # nothing to match on
        cls.bad = meas(cls.f1, 5, 5, None)
        Measurement.objects.filter(pk=cls.bad.pk).update(is_valid=False)
        cls.m6 = meas(cls.f2, 1, 6, None, tech='3G')                                                          # file still matching
        cls.url = reverse('drive_test:cell_matching')

    def setUp(self):
        self.client.force_login(self.user)

    def ids(self, **q):
        return sorted(m.pk for m in self.client.get(self.url, q).context['rows'])

    def test_page_access_and_navigation(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Review how drive-test measurements are associated with the network reference cells.')
        self.assertContains(r, 'cell-matching')                          # sidebar entry
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_summary_and_match_rate(self):
        r = self.client.get(self.url)
        c = r.context
        self.assertEqual(c['summary'], {'total': 5, 'matched': 2, 'unmatched': 2, 'unknown': 1, 'invalid': 1})
        self.assertEqual(c['match_rate'], 50.0)                         # 2 / (2 + 2); unknown and invalid excluded
        self.assertEqual({(m['match_method'], m['n']) for m in c['methods']}, {('exact_ecgi', 1), ('pci_earfcn', 1)})
        self.assertNotContains(r, 'AMBIGUOUS')

    def test_no_match_rate_without_eligible_records(self):
        self.assertIsNone(self.client.get(self.url, {'session': self.s2.session_ref}).context['match_rate'])

    def test_status_filters_including_documented_upper_case(self):
        self.assertEqual(self.ids(status='MATCHED'), sorted([self.m1.pk, self.m2.pk]))
        self.assertEqual(self.ids(status='UNMATCHED'), sorted([self.m3.pk, self.m4.pk]))
        self.assertEqual(self.ids(status='UNKNOWN'), [self.m6.pk])
        r = self.client.get(self.url, {'status': 'AMBIGUOUS'})
        self.assertEqual(list(r.context['rows']), [])
        self.assertTrue(r.context['errors'])

    def test_other_filters(self):
        self.assertEqual(self.ids(session=self.s2.session_ref), [self.m6.pk])
        self.assertEqual(self.ids(operator='qcell'), [self.m6.pk])
        self.assertEqual(self.ids(technology='2G'), sorted([self.m3.pk, self.m4.pk]))
        self.assertEqual(self.ids(cell='LTE-001'), sorted([self.m1.pk, self.m2.pk]))
        self.assertEqual(self.ids(site='FT-001'), sorted([self.m1.pk, self.m2.pk]))
        self.assertEqual(self.ids(q='pci_earfcn'), [self.m2.pk])         # searchable by method
        self.assertEqual(self.ids(date_from='2026-09-24T12:00:03', date_to='2026-09-24T12:00:04'), sorted([self.m3.pk, self.m4.pk]))

    def test_region_district_follow_matched_cell_and_site(self):
        both = sorted([self.m1.pk, self.m2.pk])
        self.assertEqual(self.ids(region=self.reg.pk), both)             # unmatched records never acquire a region
        self.assertEqual(self.ids(district=self.dis.pk), both)
        self.assertEqual(self.ids(region=self.reg2.pk), [])
        self.assertEqual(self.ids(status='UNMATCHED', region=self.reg.pk), [])

    def test_identifier_availability_filter(self):
        self.assertEqual(self.ids(status='UNMATCHED', ident='yes'), [self.m3.pk])
        self.assertEqual(self.ids(status='UNMATCHED', ident='no'), [self.m4.pk])

    def test_invalid_records_are_not_listed(self):
        self.assertNotIn(self.bad.pk, self.ids())

    def test_rows_show_actual_method_confidence_cell_site_and_identifiers(self):
        html = self.client.get(self.url).content.decode()
        for text in ('exact_ecgi', '1.00', 'pci_earfcn', '0.85', 'LTE-001', 'Freetown Central', 'Western Area › Western Urban',
                     'ECI 1234', 'LAC 20028', 'CI 10102', 'PCI 77', 'EARFCN 1650', 'MCC-MNC 619-01'):
            self.assertIn(text, html)
        for absent in ('BSIC', 'PSC', 'AMBIGUOUS'):
            self.assertNotIn(absent, html)

    def test_unmatched_rows_show_no_fabricated_cell_and_distinct_messages(self):
        r = self.client.get(self.url, {'status': 'UNMATCHED'})
        for m in r.context['rows']:
            self.assertIsNone(m.ref_cell)
            self.assertIsNone(m.ref_site)
        html = r.content.decode()
        self.assertIn('No reference cell matched.', html)                # m3: had identifiers
        self.assertIn('No matchable cell identifier is available for this measurement.', html)   # m4

    def test_links_to_measurement_cell_and_site(self):
        html = self.client.get(self.url, {'status': 'MATCHED'}).content.decode()
        self.assertIn(reverse('drive_test:measurement_detail', args=[self.m1.pk]), html)
        self.assertIn(reverse('drive_test:cell_detail', args=[self.cell.pk]), html)
        self.assertIn(reverse('drive_test:site_detail', args=[self.site.pk]), html)
        d = self.client.get(reverse('drive_test:measurement_detail', args=[self.m3.pk]))
        self.assertContains(d, 'cell-matching/?session=%s&amp;status=unmatched' % self.s1.session_ref)

    def test_pagination_loads_only_the_requested_page(self):
        for i in range(60):
            m = Measurement.objects.create(drive_file=self.f1, sequence_num=100 + i, captured_at=datetime(2026, 9, 24, 13, 0, i), latitude=1, longitude=1)
            RadioMeasurement.objects.create(measurement=m, technology='4G')
        r = self.client.get(self.url)
        self.assertEqual(len(r.context['rows']), 50)
        self.assertEqual(r.context['page_obj'].paginator.count, 65)
        self.assertEqual(len(self.client.get(self.url, {'page': 2}).context['rows']), 15)

    def test_no_n_plus_one(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count():
            with CaptureQueriesContext(connection) as c:
                self.client.get(self.url)
            return len(c)
        before = count()
        for i in range(25):
            m = Measurement.objects.create(drive_file=self.f1, sequence_num=200 + i, captured_at=datetime(2026, 9, 24, 14, 0, i),
                                           latitude=1, longitude=1, matched_cell=self.cell, match_method='exact_ecgi', match_confidence=1.0)
            RadioMeasurement.objects.create(measurement=m, technology='4G')
        self.assertEqual(count(), before)

    def test_empty_states(self):
        self.assertContains(self.client.get(self.url, {'cell': 'NOPE'}), 'No measurements match the selected matching filters.')
        Measurement.objects.all().delete()
        self.assertContains(self.client.get(self.url), 'No measurement records are available for cell matching.')

    def test_failure_is_friendly(self):
        from unittest import mock
        with mock.patch('drive_test.views.Paginator', side_effect=RuntimeError('boom /etc/x')):
            r = self.client.get(self.url)
        self.assertContains(r, 'Unable to load cell-matching results.')
        self.assertNotContains(r, 'boom')
