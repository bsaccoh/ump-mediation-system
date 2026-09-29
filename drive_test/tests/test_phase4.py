"""Phase 4 tests: statistics, analytics reports, roll-ups, pages.

Run: python manage.py test drive_test.tests.test_phase4 --settings=config.test_settings
"""
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from drive_test.models import Campaign, DriveTestFile, KpiResult, Project, Sample
from drive_test.services import analytics
from drive_test.services.stats import band_histogram, describe, percentile
from drive_test.services.thresholds import DEFAULT_BANDS

User = get_user_model()


class StatsTests(TestCase):
    def test_percentile_interpolates(self):
        s = [0, 10, 20, 30, 40]
        self.assertEqual(percentile(s, 0), 0)
        self.assertEqual(percentile(s, 100), 40)
        self.assertEqual(percentile(s, 50), 20)

    def test_describe_basic(self):
        d = describe([1, 2, 3, 4, 5])
        self.assertEqual(d['count'], 5)
        self.assertEqual(d['min'], 1)
        self.assertEqual(d['max'], 5)
        self.assertEqual(d['mean'], 3.0)
        self.assertFalse(d['sufficient'])  # < MIN_SAMPLES

    def test_describe_empty_is_none(self):
        self.assertIsNone(describe([]))
        self.assertIsNone(describe([None, None]))

    def test_band_histogram_percentages(self):
        bands = DEFAULT_BANDS['rsrp']
        hist = band_histogram([-70, -70, -120, None], bands)
        excellent = next(h for h in hist if h['label'] == 'Excellent')
        critical = next(h for h in hist if h['label'] == 'Critical')
        self.assertEqual(excellent['count'], 2)
        self.assertEqual(critical['count'], 1)
        self.assertEqual(excellent['pct'], 66.7)  # 2 of 3 non-null


class AnalyticsMixin:
    def _make(self, n=40):
        self.user = User.objects.create_user('ana', password='x', is_analyst=True)
        self.project = Project.objects.create(name='P', created_by=self.user)
        self.campaign = Campaign.objects.create(project=self.project, name='C', technology='LTE')
        self.dtf = DriveTestFile.objects.create(campaign=self.campaign, original_name='f.csv', sha256='c' * 64)
        base = datetime(2026, 9, 1, 10, 0, 0)
        rows = []
        for i in range(n):
            rows.append(Sample(
                campaign=self.campaign, drive_file=self.dtf,
                timestamp=base + timedelta(seconds=i),
                latitude=8.48 + i * 0.0005, longitude=-13.22 + i * 0.0005,
                technology='LTE', obs_cell_id='CELL-%d' % (i % 3),
                rsrp=-80 - (i % 40), sinr=10.0, dl_throughput=20000.0,
            ))
        Sample.objects.bulk_create(rows)


class AnalyticsServiceTests(AnalyticsMixin, TestCase):
    def test_rf_report_has_metrics(self):
        self._make()
        reps = analytics.rf_report(self.campaign)
        metrics = {r['metric'] for r in reps}
        self.assertIn('rsrp', metrics)
        self.assertIn('sinr', metrics)
        rsrp = next(r for r in reps if r['metric'] == 'rsrp')
        self.assertEqual(rsrp['stats']['count'], 40)
        self.assertTrue(any(h['count'] > 0 for h in rsrp['histogram']))

    def test_coverage_report_distance_and_classes(self):
        self._make()
        cov = analytics.coverage_report(self.campaign, 'LTE')
        self.assertEqual(cov['metric'], 'rsrp')
        self.assertIsNotNone(cov['total_distance_km'])
        self.assertTrue(cov['distance_is_estimate'])

    def test_cell_report_groups_by_observed_cell(self):
        self._make()
        cells = analytics.cell_report(self.campaign)
        self.assertEqual(len(cells), 3)  # CELL-0/1/2
        self.assertTrue(all(c['samples'] > 0 for c in cells))

    def test_rollups_persisted(self):
        self._make()
        analytics.store_campaign_rollups(self.campaign)
        rsrp = KpiResult.objects.get(campaign=self.campaign, metric='rsrp',
                                     scope_type=KpiResult.Scope.CAMPAIGN)
        self.assertEqual(rsrp.count, 40)
        # idempotent
        analytics.store_campaign_rollups(self.campaign)
        self.assertEqual(KpiResult.objects.filter(campaign=self.campaign, metric='rsrp').count(), 1)

    def test_absent_metric_not_reported(self):
        self._make()
        reps = analytics.rf_report(self.campaign)
        self.assertNotIn('rscp', {r['metric'] for r in reps})  # never measured → absent


class AnalyticsPageTests(AnalyticsMixin, TestCase):
    def setUp(self):
        self._make(n=25)
        self.client.force_login(self.user)

    def test_all_sections_render(self):
        for section in ('rf', 'coverage', 'data', 'cells', 'voice', 'handover'):
            url = reverse('drive_test:campaign_analytics_section', args=[self.campaign.pk, section])
            self.assertEqual(self.client.get(url).status_code, 200, section)

    def test_index_chooser_renders(self):
        self.assertEqual(self.client.get(reverse('drive_test:analytics_index', args=['rf'])).status_code, 200)
