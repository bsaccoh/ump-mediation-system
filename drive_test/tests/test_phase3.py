"""Phase 3 tests: threshold classification, geo decimation, map/timeseries APIs.

Run: python manage.py test drive_test.tests.test_phase3 --settings=config.test_settings
"""
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from drive_test.models import Campaign, DriveTestFile, KpiThreshold, Project, Sample
from drive_test.services.geo import GeoQueryService
from drive_test.services.thresholds import DEFAULT_BANDS, classify, resolve_bands

User = get_user_model()


class ThresholdTests(TestCase):
    def test_classify_bands(self):
        bands = DEFAULT_BANDS['rsrp']
        self.assertEqual(classify(-70, bands)['label'], 'Excellent')
        self.assertEqual(classify(-85, bands)['label'], 'Good')
        self.assertEqual(classify(-120, bands)['label'], 'Critical')
        self.assertIsNone(classify(None, bands))  # missing stays unclassified

    def test_resolve_prefers_db_over_default(self):
        custom = [{'label': 'Only', 'min': None, 'max': None, 'rank': 1, 'color': 'good'}]
        KpiThreshold.objects.create(metric='rsrp', technology='', bands=custom, is_active=True)
        bands = resolve_bands('rsrp')
        self.assertEqual(bands, custom)

    def test_resolve_falls_back_to_default(self):
        self.assertEqual(resolve_bands('sinr'), DEFAULT_BANDS['sinr'])


class _DataMixin:
    def _make(self, n=30, with_gps=True):
        self.user = User.objects.create_user('ana', password='x', is_analyst=True)
        self.project = Project.objects.create(name='P', created_by=self.user)
        self.campaign = Campaign.objects.create(project=self.project, name='C', technology='LTE')
        self.dtf = DriveTestFile.objects.create(
            campaign=self.campaign, original_name='f.csv', sha256='b' * 64)
        base = datetime(2026, 9, 1, 10, 0, 0)
        rows = []
        for i in range(n):
            rows.append(Sample(
                campaign=self.campaign, drive_file=self.dtf,
                timestamp=base + timedelta(seconds=i),
                latitude=8.48 + i * 0.001 if with_gps else None,
                longitude=-13.22 + i * 0.001 if with_gps else None,
                technology='LTE', rsrp=-80 - i, sinr=10.0,
            ))
        Sample.objects.bulk_create(rows)


class GeoTests(_DataMixin, TestCase):
    def test_decimation_caps_and_flags(self):
        self._make(n=50)
        geo = GeoQueryService(self.campaign)
        ids, total, decimated = GeoQueryService.decimate(geo.samples(), 10)
        self.assertEqual(total, 50)
        self.assertTrue(decimated)
        self.assertLessEqual(len(ids), 11)

    def test_available_metrics(self):
        self._make(n=5)
        geo = GeoQueryService(self.campaign)
        counts = geo.available_metrics(['rsrp', 'sinr', 'rscp'])
        self.assertEqual(counts['rsrp'], 5)
        self.assertEqual(counts['rscp'], 0)  # absent


class ApiTests(_DataMixin, TestCase):
    def setUp(self):
        self._make(n=20)
        self.client.force_login(self.user)

    def test_map_api_classifies_points(self):
        url = reverse('drive_test:drive_test_api:campaign_map', args=[self.campaign.pk])
        d = self.client.get(url + '?metric=rsrp').json()
        self.assertEqual(d['meta']['metric'], 'rsrp')
        self.assertTrue(len(d['points']) > 0)
        self.assertTrue(len(d['route']) > 0)
        self.assertIn('rsrp', d['meta']['available_metrics'])
        self.assertIn('rscp', d['meta']['absent_metrics'])
        self.assertIn(d['points'][0]['color'],
                      ['excellent', 'good', 'fair', 'poor', 'critical', 'none'])

    def test_timeseries_api_columns(self):
        url = reverse('drive_test:drive_test_api:campaign_timeseries', args=[self.campaign.pk])
        d = self.client.get(url + '?metrics=rsrp').json()
        self.assertEqual(len(d['t']), len(d['series']['rsrp']))
        self.assertEqual(len(d['ids']), len(d['t']))
        self.assertIn('rscp', d['meta']['absent_metrics'])

    def test_sample_detail_api(self):
        s = Sample.objects.filter(campaign=self.campaign).first()
        url = reverse('drive_test:drive_test_api:sample_detail', args=[s.pk])
        d = self.client.get(url).json()
        self.assertEqual(d['id'], s.pk)
        self.assertEqual(d['technology'], 'LTE')

    def test_map_page_renders(self):
        self.assertEqual(
            self.client.get(reverse('drive_test:map_analysis', args=[self.campaign.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('drive_test:map_index')).status_code, 302)  # single → redirect

    def test_api_requires_login(self):
        self.client.logout()
        url = reverse('drive_test:drive_test_api:campaign_map', args=[self.campaign.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
