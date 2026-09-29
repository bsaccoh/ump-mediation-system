"""Measurement Explorer tests. Fixtures exist only in the isolated test DB."""
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import (
    Cell, Chiefdom, District, DriveTestFile, DriveTestSession, Measurement, RadioMeasurement, Region, Sector,
    ServiceMeasurement, Site,
)
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@STATIC
class MeasurementExplorerTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.user = U.objects.create_user('viewer', password='x')
        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.qcell = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')
        cls.reg = Region.objects.create(code='T-W', name='Western Area')
        cls.reg2 = Region.objects.create(code='T-N', name='North')
        cls.dis = District.objects.create(code='T-WU', name='Western Urban', region=cls.reg)
        cls.chief = Chiefdom.objects.create(code='T-C', name='Central', district=cls.dis)
        cls.site = Site.objects.create(site_id='FT-001', name='Freetown Central', operator=cls.orange,
                                       latitude=8.48, longitude=-13.23, chiefdom=cls.chief)
        cls.sec = Sector.objects.create(site=cls.site, sector_id='A')
        cls.cell = Cell.objects.create(cell_id='LTE-001', operator=cls.orange, sector=cls.sec, technology='4G')

        cls.done = DriveTestSession.objects.create(operator=cls.orange, test_date='2026-09-24', uploaded_by=cls.user, status='COMPLETED')
        cls.other = DriveTestSession.objects.create(operator=cls.qcell, test_date='2026-08-01', uploaded_by=cls.user, status='PROCESSING')
        cls.fd = DriveTestFile.objects.create(session=cls.done, original_filename='walk_001.trp', file_path='secret/p',
                                              file_size=10, sha256='a' * 64, status='COMPLETED')
        cls.fd2 = DriveTestFile.objects.create(session=cls.other, original_filename='q.trp', file_path='p',
                                               file_size=1, sha256='b' * 64, status='MATCHING')

        def meas(file, seq, lat, lon, cell=None, tech='4G', sec=0, **radio):
            m = Measurement.objects.create(drive_file=file, sequence_num=seq, captured_at=datetime(2026, 9, 24, 12, 0, sec),
                                           latitude=lat, longitude=lon, matched_cell=cell, obs_pci=77,
                                           match_method='exact_ecgi' if cell else '')
            RadioMeasurement.objects.create(measurement=m, technology=tech, **radio)
            return m

        cls.m1 = meas(cls.fd, 1, 8.4841, -13.2299, cls.cell, rsrp=-95.0, sinr=18.0, sec=1)
        cls.m2 = meas(cls.fd, 2, 0.0, 0.0, None, tech='2G', rssi=-101.0, sec=2)   # unmatched, no GPS
        cls.m3 = meas(cls.fd2, 1, 7.9, -11.7, None, tech='3G', sec=3)              # unknown (file still matching)
        cls.bad = meas(cls.fd, 3, 8.5, -13.2, None, sec=4)
        Measurement.objects.filter(pk=cls.bad.pk).update(is_valid=False)
        ServiceMeasurement.objects.create(measurement=cls.m1, service_type='VOICE', outcome='SUCCESS', call_duration_s=42)
        cls.url = reverse('drive_test:measurement_list')

    def setUp(self):
        self.client.force_login(self.user)

    def ids(self, **q):
        return sorted(m.pk for m in self.client.get(self.url, q).context['rows'])

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.get(reverse('drive_test:measurement_detail', args=[self.m1.pk])).status_code, 302)

    def test_lists_valid_measurements_only_with_summary(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.ids(), sorted([self.m1.pk, self.m2.pk, self.m3.pk]))
        self.assertEqual(r.context['summary'], {'total': 3, 'matched': 1, 'unmatched': 1, 'unknown': 1, 'invalid': 1})

    def test_filters(self):
        self.assertEqual(self.ids(session=self.other.session_ref), [self.m3.pk])
        self.assertEqual(self.ids(operator='orange'), sorted([self.m1.pk, self.m2.pk]))
        self.assertEqual(self.ids(technology='2G'), [self.m2.pk])
        self.assertEqual(self.ids(cell='LTE'), [self.m1.pk])
        self.assertEqual(self.ids(site='Freetown'), [self.m1.pk])
        self.assertEqual(self.ids(region=self.reg.pk), [self.m1.pk])
        self.assertEqual(self.ids(region=self.reg2.pk), [])
        self.assertEqual(self.ids(district=self.dis.pk), [self.m1.pk])
        self.assertEqual(self.ids(date_from='2026-09-24T12:00', date_to='2026-09-24T12:00'), [])
        self.assertEqual(self.ids(date_from='2026-09-24', date_to='2026-09-24'), sorted([self.m1.pk, self.m2.pk, self.m3.pk]))
        self.assertEqual(self.ids(date_from='2026-09-25'), [])

    def test_match_states(self):
        self.assertEqual(self.ids(match='matched'), [self.m1.pk])
        self.assertEqual(self.ids(match='unmatched'), [self.m2.pk])
        self.assertEqual(self.ids(match='unknown'), [self.m3.pk])
        r = self.client.get(self.url, {'match': 'bogus'})
        self.assertEqual(list(r.context['rows']), [])
        self.assertTrue(r.context['errors'])

    def test_signal_range_and_search(self):
        self.assertEqual(self.ids(metric='rsrp', max='-90'), [self.m1.pk])
        self.assertEqual(self.ids(metric='rsrp', max='-100'), [])
        self.assertEqual(self.ids(metric='rssi', min='-110'), [self.m2.pk])
        self.assertTrue(self.client.get(self.url, {'metric': 'rsrp', 'min': 'abc'}).context['errors'])
        self.assertEqual(self.ids(q='walk_001'), sorted([self.m1.pk, self.m2.pk]))
        self.assertEqual(self.ids(q='qcell'), [self.m3.pk])
        self.assertEqual(self.ids(q='3G'), [self.m3.pk])

    def test_invalid_date_reported(self):
        r = self.client.get(self.url, {'date_from': 'yesterday'})
        self.assertEqual(list(r.context['rows']), [])
        self.assertTrue(r.context['errors'])

    def test_default_page_size_and_pagination(self):
        for i in range(60):
            m = Measurement.objects.create(drive_file=self.fd, sequence_num=100 + i, captured_at=datetime(2026, 9, 24, 13, 0, i),
                                           latitude=8.4, longitude=-13.2)
            RadioMeasurement.objects.create(measurement=m, technology='4G', rsrp=-90.0)
        r = self.client.get(self.url)
        self.assertEqual(len(r.context['rows']), 50)
        self.assertEqual(r.context['page_obj'].paginator.count, 63)
        self.assertEqual(len(self.client.get(self.url, {'page': 2}).context['rows']), 13)
        self.assertEqual(len(self.client.get(self.url, {'per_page': 100}).context['rows']), 63)
        self.assertEqual(self.client.get(self.url, {'per_page': 7}).context['per_page'], 50)

    def test_rows_show_units_and_reference_vs_observed(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('-95 dBm', html)
        self.assertIn('LTE-001', html)
        self.assertIn('Observed: PCI 77', html)
        self.assertIn('UNKNOWN', html)

    def test_no_n_plus_one(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count():
            with CaptureQueriesContext(connection) as c:
                self.client.get(self.url)
            return len(c)

        before = count()
        for i in range(30):
            m = Measurement.objects.create(drive_file=self.fd, sequence_num=200 + i, captured_at=datetime(2026, 9, 24, 14, 0, i),
                                           latitude=8.4, longitude=-13.2, matched_cell=self.cell)
            RadioMeasurement.objects.create(measurement=m, technology='4G', rsrp=-90.0)
        self.assertEqual(count(), before)

    def test_empty_states(self):
        r = self.client.get(self.url, {'cell': 'NOPE'})
        self.assertContains(r, 'No measurements match your filters.')
        Measurement.objects.all().delete()
        self.assertContains(self.client.get(self.url), 'No measurements available')

    def test_detail_matched(self):
        r = self.client.get(reverse('drive_test:measurement_detail', args=[self.m1.pk]))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for text in ('walk_001.trp', self.done.session_ref, 'LTE-001', 'Freetown Central', '-95', 'exact_ecgi',
                     'Western Area', 'a' * 64):
            self.assertIn(text, html)
        self.assertNotIn('secret/p', html)          # filesystem path is never exposed
        self.assertEqual(r.context['state'], 'matched')

    def test_detail_missing_values_render_dash(self):
        r = self.client.get(reverse('drive_test:measurement_detail', args=[self.m2.pk]))
        html = r.content.decode()
        self.assertEqual(r.context['state'], 'unmatched')
        self.assertIn('Location unavailable for this measurement.', html)
        self.assertIn('No reference cell is associated with this measurement', html)
        self.assertIn('No service measurement associated with this record.', html)
        self.assertNotIn('0.000000', html)

    def test_detail_unknown_state_and_404(self):
        r = self.client.get(reverse('drive_test:measurement_detail', args=[self.m3.pk]))
        self.assertEqual(r.context['state'], 'unknown')
        self.assertContains(r, 'has not completed')
        self.assertEqual(self.client.get(reverse('drive_test:measurement_detail', args=[999999])).status_code, 404)

    def test_query_failure_shows_friendly_error(self):
        from unittest import mock
        with mock.patch('drive_test.views.Paginator', side_effect=RuntimeError('boom /etc/secret')):
            r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Unable to load measurements.')
        self.assertNotContains(r, 'boom')
