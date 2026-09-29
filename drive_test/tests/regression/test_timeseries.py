"""
Time-series payload tests.

The wire contract matters more than the numbers here: every column must stay
index-aligned, because the workspace panes address samples by INDEX rather than
timestamp. A column that drifts out of alignment desynchronises the cursor in a
way that is very hard to see and very easy to ship.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from drive_test.models import (
    DriveTestFile, DriveTestSession, Measurement, MeasurementEvent, RadioMeasurement,
)
from drive_test.services.timeseries import (
    ALL_METRICS, _nearest_index, _select_indices, session_timeseries,
)
from reference.models import Operator

_START = datetime(2026, 1, 18, 12, 0, 0)


class SelectIndicesTests(TestCase):
    """Decimation must never discard an extreme."""

    def test_short_series_is_untouched(self):
        self.assertEqual(_select_indices([1, 2, 3], 100), [0, 1, 2])

    def test_extremes_survive_decimation(self):
        # A flat series with one deep trough and one sharp peak buried in it.
        values = [-90.0] * 1000
        values[300] = -130.0   # coverage hole
        values[700] = -50.0    # peak
        kept = _select_indices(values, 50)

        self.assertLessEqual(len(kept), 50 + 2)
        self.assertIn(300, kept, 'the deepest sample was decimated away')
        self.assertIn(700, kept, 'the strongest sample was decimated away')

    def test_first_and_last_survive(self):
        kept = _select_indices(list(range(1000)), 40)
        self.assertEqual(kept[0], 0)
        self.assertEqual(kept[-1], 999)

    def test_indices_are_sorted_and_unique(self):
        kept = _select_indices([float(i % 17) for i in range(2000)], 64)
        self.assertEqual(kept, sorted(set(kept)))

    def test_all_null_series_still_returns_indices(self):
        kept = _select_indices([None] * 500, 20)
        self.assertTrue(kept)
        self.assertEqual(kept, sorted(set(kept)))


class NearestIndexTests(TestCase):
    def test_empty(self):
        self.assertIsNone(_nearest_index([], 10))

    def test_exact_and_between(self):
        offsets = [0, 100, 200, 300]
        self.assertEqual(_nearest_index(offsets, 0), 0)
        self.assertEqual(_nearest_index(offsets, 200), 2)
        self.assertEqual(_nearest_index(offsets, 140), 1)
        self.assertEqual(_nearest_index(offsets, 160), 2)

    def test_outside_range_clamps(self):
        offsets = [10, 20, 30]
        self.assertEqual(_nearest_index(offsets, -500), 0)
        self.assertEqual(_nearest_index(offsets, 9999), 2)


class SessionTimeseriesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operator = Operator.objects.create(code='orange', name='Orange', enabled=True)
        cls.user = get_user_model().objects.create_user(username='ts', password='pw')

    def _session(self, count=10, *, with_radio=True, tech='4G'):
        session = DriveTestSession.objects.create(
            operator=self.operator, test_date=timezone.now().date(),
            uploaded_by=self.user, status='COMPLETED',
        )
        drive_file = DriveTestFile.objects.create(
            session=session, original_filename='t.trp', file_path='/x',
            file_size=1, sha256=f'{session.pk:064d}',
        )
        for i in range(count):
            m = Measurement.objects.create(
                drive_file=drive_file, sequence_num=i,
                captured_at=_START + timedelta(seconds=i),
                latitude=8.0 + i * 0.001, longitude=-13.0 - i * 0.001,
                speed_kmh=float(i),
            )
            if with_radio:
                RadioMeasurement.objects.create(
                    measurement=m, technology=tech,
                    rsrp=-80.0 - i, sinr=float(i),
                )
        return session

    def test_columns_are_index_aligned(self):
        payload = session_timeseries(self._session(10))
        expected = payload['meta']['returned']

        for key in ('t', 'lat', 'lon', 'tech', 'cell', 'ids'):
            self.assertEqual(len(payload[key]), expected, f'{key} is misaligned')
        for name, values in payload['series'].items():
            self.assertEqual(len(values), expected, f'series.{name} is misaligned')

    def test_absent_metrics_are_reported_not_zeroed(self):
        payload = session_timeseries(self._session(5))
        meta = payload['meta']

        self.assertIn('rsrp', meta['available_metrics'])
        # Nothing populated the 5G columns, so they are absent — not zero.
        self.assertIn('ss_rsrp', meta['absent_metrics'])
        self.assertNotIn('ss_rsrp', payload['series'])

    def test_time_is_relative_milliseconds_from_first_sample(self):
        payload = session_timeseries(self._session(4))
        self.assertEqual(payload['t'][0], 0)
        self.assertEqual(payload['t'][1], 1000)

    def test_technology_is_coded(self):
        payload = session_timeseries(self._session(3, tech='5G'))
        self.assertEqual(set(payload['tech']), {4})

    def test_empty_session_returns_empty_columns(self):
        session = DriveTestSession.objects.create(
            operator=self.operator, test_date=timezone.now().date(),
            uploaded_by=self.user, status='COMPLETED',
        )
        payload = session_timeseries(session)

        self.assertEqual(payload['t'], [])
        self.assertEqual(payload['meta']['source_count'], 0)
        self.assertFalse(payload['meta']['decimated'])
        self.assertEqual(payload['meta']['available_metrics'], [])

    def test_session_without_radio_still_returns_core_metrics(self):
        payload = session_timeseries(self._session(5, with_radio=False))
        self.assertIn('speed', payload['meta']['available_metrics'])
        self.assertIn('rsrp', payload['meta']['absent_metrics'])

    def test_decimation_is_reported(self):
        payload = session_timeseries(self._session(300), max_points=50)
        meta = payload['meta']

        self.assertTrue(meta['decimated'])
        self.assertEqual(meta['source_count'], 300)
        self.assertLess(meta['returned'], 300)
        self.assertEqual(len(payload['t']), meta['returned'])

    def test_events_are_not_decimated_and_carry_an_index(self):
        session = self._session(300)
        MeasurementEvent.objects.create(
            session=session, occurred_at=_START + timedelta(seconds=150),
            event_type='CALL_DROP', severity='HIGH',
        )
        payload = session_timeseries(session, max_points=20)

        self.assertEqual(len(payload['events']), 1)
        event = payload['events'][0]
        self.assertEqual(event['type'], 'CALL_DROP')
        self.assertIsNotNone(event['idx'])
        self.assertLess(event['idx'], payload['meta']['returned'])

    def test_metric_filter_restricts_series(self):
        payload = session_timeseries(self._session(5), metrics=['rsrp'])
        self.assertEqual(list(payload['series']), ['rsrp'])

    def test_unknown_metric_is_ignored(self):
        payload = session_timeseries(self._session(5), metrics=['rsrp', 'not_a_metric'])
        self.assertEqual(list(payload['series']), ['rsrp'])

    def test_all_declared_metrics_are_resolvable(self):
        """Every key in ALL_METRICS must map to a real column."""
        payload = session_timeseries(self._session(3), metrics=ALL_METRICS)
        meta = payload['meta']
        self.assertEqual(
            sorted(meta['available_metrics'] + meta['absent_metrics']),
            sorted(ALL_METRICS),
        )


class TimeseriesEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operator = Operator.objects.create(code='orange', name='Orange', enabled=True)
        cls.user = get_user_model().objects.create_user(username='api', password='pw')
        cls.session = DriveTestSession.objects.create(
            operator=cls.operator, test_date=timezone.now().date(),
            uploaded_by=cls.user, status='COMPLETED',
        )

    def test_requires_login(self):
        url = reverse('drive_test:session_timeseries', args=[self.session.session_ref])
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_returns_payload(self):
        self.client.force_login(self.user)
        url = reverse('drive_test:session_timeseries', args=[self.session.session_ref])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('meta', body)
        self.assertEqual(body['meta']['session_ref'], self.session.session_ref)

    def test_unknown_session_is_404(self):
        self.client.force_login(self.user)
        url = reverse('drive_test:session_timeseries', args=['DT-NOPE'])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_max_points_is_clamped(self):
        self.client.force_login(self.user)
        url = reverse('drive_test:session_timeseries', args=[self.session.session_ref])
        # Garbage and out-of-range values must not raise.
        for value in ('0', '-5', 'abc', '99999999'):
            self.assertEqual(self.client.get(url, {'max_points': value}).status_code, 200)

    def test_workspace_page_renders(self):
        self.client.force_login(self.user)
        url = reverse('drive_test:session_workspace', args=[self.session.session_ref])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.session.session_ref)
