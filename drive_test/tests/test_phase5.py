"""Phase 5 tests: event engine, clustering, comparison, event workflow, pages.

Run: python manage.py test drive_test.tests.test_phase5 --settings=config.test_settings
"""
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import AuditLog
from drive_test.models import Campaign, DriveTestFile, Event, ProblemArea, Project, Sample
from drive_test.models.enums import EventStatus, Severity
from drive_test.services import comparison
from drive_test.services.events import detect_events
from drive_test.services.problem_areas import cluster_problem_areas

User = get_user_model()


class EventEngineMixin:
    def _campaign(self):
        self.user = User.objects.create_user('ana', password='x', is_analyst=True)
        self.project = Project.objects.create(name='P', created_by=self.user)
        self.campaign = Campaign.objects.create(project=self.project, name='C', technology='LTE')
        self.dtf = DriveTestFile.objects.create(campaign=self.campaign, original_name='f.csv', sha256='e' * 64)
        return self.campaign

    def _samples(self, rsrps, lat=8.48, lon=-13.22):
        base = datetime(2026, 9, 1, 10, 0, 0)
        rows = []
        for i, r in enumerate(rsrps):
            rows.append(Sample(
                campaign=self.campaign, drive_file=self.dtf,
                timestamp=base + timedelta(seconds=i),
                latitude=lat, longitude=lon, technology='LTE', rsrp=r,
            ))
        Sample.objects.bulk_create(rows)


class EventEngineTests(EventEngineMixin, TestCase):
    def test_breach_run_creates_single_event(self):
        self._campaign()
        # 5 good then 4 critical (< -110) then 5 good → one POOR_RSRP event.
        self._samples([-80] * 5 + [-115] * 4 + [-80] * 5)
        n = detect_events(self.campaign)
        self.assertEqual(n, 1)
        e = Event.objects.get(campaign=self.campaign)
        self.assertEqual(e.event_type, 'POOR_RSRP')
        self.assertEqual(e.severity, Severity.CRITICAL)
        self.assertEqual(e.measured_value, -115)  # worst sample anchored

    def test_short_dip_below_min_run_ignored(self):
        self._campaign()
        self._samples([-80, -80, -115, -80, -80])  # single critical sample
        self.assertEqual(detect_events(self.campaign), 0)

    def test_good_signal_no_events(self):
        self._campaign()
        self._samples([-70] * 20)
        self.assertEqual(detect_events(self.campaign), 0)

    def test_detection_is_idempotent(self):
        self._campaign()
        self._samples([-115] * 6)
        detect_events(self.campaign)
        detect_events(self.campaign)
        self.assertEqual(Event.objects.filter(campaign=self.campaign).count(), 1)


class ClusteringTests(EventEngineMixin, TestCase):
    def test_events_cluster_into_area(self):
        self._campaign()
        # Two separate critical runs at the same location → 2 events, 1 area.
        base = datetime(2026, 9, 1, 10, 0, 0)
        rows = []
        seq = [-115] * 3 + [-80] * 3 + [-115] * 3
        for i, r in enumerate(seq):
            rows.append(Sample(campaign=self.campaign, drive_file=self.dtf,
                               timestamp=base + timedelta(seconds=i),
                               latitude=8.4800, longitude=-13.2200, technology='LTE', rsrp=r))
        Sample.objects.bulk_create(rows)
        self.assertEqual(detect_events(self.campaign), 2)
        areas = cluster_problem_areas(self.campaign)
        self.assertEqual(areas, 1)
        pa = ProblemArea.objects.get(campaign=self.campaign)
        self.assertEqual(pa.sample_count, 2)
        self.assertEqual(pa.severity, Severity.CRITICAL)
        # members linked
        self.assertEqual(Event.objects.filter(problem_area=pa).count(), 2)

    def test_clustering_idempotent(self):
        self._campaign()
        self._samples([-115] * 4 + [-80] * 3 + [-115] * 4)
        detect_events(self.campaign)
        cluster_problem_areas(self.campaign)
        cluster_problem_areas(self.campaign)
        self.assertEqual(ProblemArea.objects.filter(campaign=self.campaign).count(), 1)


class ComparisonTests(EventEngineMixin, TestCase):
    def test_technology_comparison(self):
        self._campaign()
        base = datetime(2026, 9, 1, 10, 0, 0)
        rows = []
        for i in range(10):
            rows.append(Sample(campaign=self.campaign, drive_file=self.dtf,
                               timestamp=base + timedelta(seconds=i), technology='LTE',
                               latitude=8.48, longitude=-13.22, rsrp=-85))
        Sample.objects.bulk_create(rows)
        table = comparison.technology_comparison([self.campaign.pk])
        self.assertIn('LTE', table['dimensions'])
        rsrp_row = next(m for m in table['metrics'] if m['key'] == 'rsrp')
        self.assertEqual(rsrp_row['values']['LTE'], -85.0)


class EventPageTests(EventEngineMixin, TestCase):
    def setUp(self):
        self._campaign()
        self._samples([-115] * 6)
        detect_events(self.campaign)
        cluster_problem_areas(self.campaign)
        self.client.force_login(self.user)

    def test_pages_render(self):
        for name, args in [
            ('event_list', [self.campaign.pk]),
            ('problem_area_list', [self.campaign.pk]),
            ('events_index', []),
            ('comparison', []),
        ]:
            self.assertEqual(self.client.get(reverse(f'drive_test:{name}', args=args)).status_code, 200, name)

    def test_events_api(self):
        url = reverse('drive_test:drive_test_api:campaign_events', args=[self.campaign.pk])
        d = self.client.get(url).json()
        self.assertTrue(len(d['events']) >= 1)
        self.assertTrue(len(d['problem_areas']) >= 0)

    def test_status_update_audited(self):
        e = Event.objects.filter(campaign=self.campaign).first()
        self.client.post(reverse('drive_test:event_set_status', args=[e.pk]),
                         {'status': EventStatus.RESOLVED})
        e.refresh_from_db()
        self.assertEqual(e.status, EventStatus.RESOLVED)
        self.assertTrue(AuditLog.objects.filter(action='UPDATE', entity_type='drive_test.Event').exists())
