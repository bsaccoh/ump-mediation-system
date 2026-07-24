"""Unit tests for ``regulatory.engines.qos``.

Locks in the Sprint-1 string-based result_code classifier:

* SUCCESS_CODES includes ``'normalRelease'`` (string emitted by the decoder)
* DROP_CODES includes ``'stableCallAbnormalTermination'``
* Anything else (incl. empty) → failed
* ASR / drop / ACD math
* Monthly rollup aggregates daily rows correctly
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from regulatory.engines.qos import (
    compute_daily_qos, compute_monthly_qos, qos_chart_data,
    SUCCESS_CODES, DROP_CODES,
)
from regulatory.models import QoSMetric

from interconnect.tests._fixtures import make_msc_record


class StringCodeClassifierTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    """The classifier must recognise the decoded string codes coming out of
    the IMS / MSC decoders, not just the numeric codes."""

    def test_success_codes_set_contains_normal_release(self):
        self.assertIn('normalRelease', SUCCESS_CODES)

    def test_drop_codes_set_contains_abnormal(self):
        self.assertIn('stableCallAbnormalTermination', DROP_CODES)


class DailyQoSTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        self.target_date = date(2026, 3, 17)  # Tuesday
        self.dt = datetime(2026, 3, 17, 10, 0)

    def _make(self, record_type, result_code, duration=60):
        return make_msc_record(record_type=record_type,
                                duration=duration,
                                start_time=self.dt,
                                result_code=result_code)

    def test_asr_100_when_all_normal_release(self):
        for _ in range(10):
            self._make('MOC', 'normalRelease', duration=60)
        m = compute_daily_qos(self.target_date)
        self.assertEqual(m.total_calls, 10)
        self.assertEqual(m.successful_calls, 10)
        self.assertEqual(m.asr_pct, Decimal('100.00'))
        self.assertEqual(m.drop_rate_pct, Decimal('0.00'))

    def test_drop_rate_with_mixed_codes(self):
        # 8 success, 2 drop → ASR 80%, drop 20%
        for _ in range(8):
            self._make('MOC', 'normalRelease')
        for _ in range(2):
            self._make('MOC', 'stableCallAbnormalTermination')
        m = compute_daily_qos(self.target_date)
        self.assertEqual(m.total_calls, 10)
        self.assertEqual(m.successful_calls, 8)
        self.assertEqual(m.dropped_calls, 2)
        self.assertEqual(m.asr_pct, Decimal('80.00'))
        self.assertEqual(m.drop_rate_pct, Decimal('20.00'))

    def test_unknown_code_counts_as_failed(self):
        self._make('MOC', 'someUnknownState')
        m = compute_daily_qos(self.target_date)
        self.assertEqual(m.total_calls, 1)
        self.assertEqual(m.successful_calls, 0)
        self.assertEqual(m.dropped_calls, 0)
        self.assertEqual(m.failed_calls, 1)

    def test_empty_result_code_counts_as_failed(self):
        self._make('MOC', '')
        m = compute_daily_qos(self.target_date)
        self.assertEqual(m.failed_calls, 1)

    def test_acd_seconds_is_average_duration(self):
        for d in (30, 60, 90):  # avg = 60
            self._make('MOC', 'normalRelease', duration=d)
        m = compute_daily_qos(self.target_date)
        self.assertEqual(m.acd_seconds, Decimal('60.00'))

    def test_only_moc_and_mtc_counted(self):
        """SMS / GW records are not voice calls — should not affect QoS."""
        self._make('SMSMO', 'normalRelease', duration=0)
        self._make('SMSMT', 'normalRelease', duration=0)
        self._make('MOC', 'normalRelease', duration=60)
        m = compute_daily_qos(self.target_date)
        self.assertEqual(m.total_calls, 1)  # only the MOC

    def test_availability_zero_when_no_traffic(self):
        m = compute_daily_qos(self.target_date)
        self.assertEqual(m.total_calls, 0)
        self.assertEqual(m.availability_pct, Decimal('0.00'))

    def test_idempotent_recompute(self):
        self._make('MOC', 'normalRelease')
        m1 = compute_daily_qos(self.target_date)
        m2 = compute_daily_qos(self.target_date)
        self.assertEqual(m1.pk, m2.pk)
        self.assertEqual(QoSMetric.objects.filter(metric_date=self.target_date,
                                                    granularity='DAILY').count(), 1)


class MonthlyRollupTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def test_monthly_aggregates_daily_rows(self):
        # 3 daily rows in March
        QoSMetric.objects.create(metric_date=date(2026, 3, 1), granularity='DAILY',
                                   total_calls=100, successful_calls=95,
                                   dropped_calls=3, failed_calls=2,
                                   asr_pct=Decimal('95.00'), acd_seconds=Decimal('60.00'),
                                   drop_rate_pct=Decimal('3.00'),
                                   availability_pct=Decimal('100.00'))
        QoSMetric.objects.create(metric_date=date(2026, 3, 2), granularity='DAILY',
                                   total_calls=200, successful_calls=180,
                                   dropped_calls=15, failed_calls=5,
                                   asr_pct=Decimal('90.00'), acd_seconds=Decimal('70.00'),
                                   drop_rate_pct=Decimal('7.50'),
                                   availability_pct=Decimal('100.00'))
        QoSMetric.objects.create(metric_date=date(2026, 3, 3), granularity='DAILY',
                                   total_calls=0, successful_calls=0,
                                   dropped_calls=0, failed_calls=0,
                                   asr_pct=Decimal('0'), acd_seconds=Decimal('0'),
                                   drop_rate_pct=Decimal('0'),
                                   availability_pct=Decimal('0'))

        m = compute_monthly_qos(2026, 3)
        self.assertEqual(m.total_calls, 300)
        self.assertEqual(m.successful_calls, 275)
        self.assertEqual(m.dropped_calls, 18)
        self.assertEqual(m.failed_calls, 7)
        # Recomputed ASR = 275/300 = 91.67%
        self.assertEqual(m.asr_pct, Decimal('91.67'))
        self.assertEqual(m.granularity, 'MONTHLY')


class QoSChartDataTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    """Note: the data-migration seeds 30 days of QoS rows, so all assertions
    here filter to an explicit date range to isolate test rows."""

    def setUp(self):
        # Drop any migration-seeded rows so this test owns the data
        QoSMetric.objects.all().delete()

    def test_returns_dict_with_series(self):
        QoSMetric.objects.create(
            metric_date=date(2026, 3, 1), granularity='DAILY',
            total_calls=100, successful_calls=95, dropped_calls=3, failed_calls=2,
            asr_pct=Decimal('95.00'), acd_seconds=Decimal('60.00'),
            drop_rate_pct=Decimal('3.00'), availability_pct=Decimal('100.00'),
        )
        d = qos_chart_data()
        self.assertIn('labels', d)
        self.assertEqual(d['labels'], ['2026-03-01'])
        self.assertEqual(d['asr'], [95.0])
        self.assertEqual(d['total_calls'], [100])

    def test_date_range_filter(self):
        for day in (1, 5, 10):
            QoSMetric.objects.create(
                metric_date=date(2026, 3, day), granularity='DAILY',
                total_calls=10,
            )
        d = qos_chart_data(start=date(2026, 3, 3), end=date(2026, 3, 8))
        self.assertEqual(d['labels'], ['2026-03-05'])
