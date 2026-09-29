"""
Benchmark Scoring Engine.

Computes weighted composite scores and letter grades for operators within
a BenchmarkCampaign. Uses the same KPI formulas as DriveTestKpiService
(via services.comparison) — no KPI math is duplicated here.

Scoring pipeline:
  1. Collect raw KPI values per operator from the campaign's sessions
  2. Normalize each KPI to 0–100 using industry-standard reference ranges
  3. Apply configurable weights to produce a composite score
  4. Assign letter grade (A/B/C/D/F) based on composite score
"""
from __future__ import annotations

import logging
from datetime import datetime

from django.db.models import Avg, Count, Q
from django.utils import timezone

from .kpi import DriveTestKpiService as KPI

logger = logging.getLogger(__name__)

# Industry-standard reference ranges for normalization (0–100).
# Each tuple: (metric_key, min_acceptable, ideal, higher_is_better)
NORMALIZATION_RANGES = {
    'coverage':  (50.0, 100.0, True),
    'rssi_mean': (-110.0, -65.0, True),
    'cssr':      (80.0, 100.0, True),
    'dcr':       (0.0, 10.0, False),     # lower is better
    'mos':       (1.0, 4.5, True),
    'speed':     (0.0, 120.0, True),
}


def normalize_kpi(key, raw_value):
    """Normalize a raw KPI value to 0–100 based on reference ranges."""
    if raw_value is None:
        return None
    if key not in NORMALIZATION_RANGES:
        return None
    low, high, higher_is_better = NORMALIZATION_RANGES[key]
    if higher_is_better:
        score = (raw_value - low) / (high - low) * 100
    else:
        score = (1.0 - (raw_value - low) / (high - low)) * 100
    return max(0.0, min(100.0, round(score, 1)))


def grade_from_score(score):
    """Assign a letter grade from a composite score (0–100)."""
    if score >= 90:
        return 'A'
    if score >= 75:
        return 'B'
    if score >= 60:
        return 'C'
    if score >= 45:
        return 'D'
    return 'F'


GRADE_LABELS = {
    'A': 'Excellent',
    'B': 'Good',
    'C': 'Satisfactory',
    'D': 'Poor',
    'F': 'Failing',
}

GRADE_COLORS = {
    'A': '#059669',
    'B': '#22c55e',
    'C': '#eab308',
    'D': '#f97316',
    'F': '#dc2626',
}


def compute_operator_kpis(meas_qs):
    """Extract the raw KPI values from a Measurement queryset."""
    total = meas_qs.count()
    if total == 0:
        return {}

    signal = KPI._signal_kpis(meas_qs, total)
    voice = KPI._voice_kpis(meas_qs)
    mobility = KPI._mobility_kpis(meas_qs)

    return {
        'coverage': signal['coverage']['coverage_percent'],
        'rssi_mean': signal.get('mean_rssi'),
        'cssr': voice.get('cssr_percent'),
        'dcr': voice.get('dcr_percent'),
        'mos': voice.get('mean_mos'),
        'speed': mobility.get('mean_speed_kmh'),
    }


def score_campaign(campaign):
    """
    Run the full scoring pipeline for a BenchmarkCampaign.

    Returns a list of BenchmarkScore objects (already saved).
    """
    from drive_test.models import BenchmarkScore, Measurement
    from reference.models import Operator

    weights = campaign.kpi_weights or campaign.default_weights()
    weight_sum = sum(weights.values())
    if weight_sum == 0:
        weight_sum = 100

    session_ids = list(campaign.sessions.values_list('pk', flat=True))
    if not session_ids:
        return []

    base_meas = Measurement.objects.filter(
        is_valid=True,
        drive_file__session_id__in=session_ids,
    )

    op_ids = (
        base_meas.values_list('drive_file__session__operator_id', flat=True)
        .distinct()
    )
    operators = Operator.objects.filter(pk__in=list(op_ids)).order_by('name')

    results = []
    for op in operators:
        op_meas = base_meas.filter(drive_file__session__operator_id=op.pk)
        total = op_meas.count()
        session_count = (
            op_meas.values('drive_file__session_id').distinct().count()
        )

        raw_values = compute_operator_kpis(op_meas)
        normalized = {}
        for key in weights:
            normalized[key] = normalize_kpi(key, raw_values.get(key))

        # Weighted composite
        weighted_sum = 0.0
        applied_weight = 0.0
        for key, weight in weights.items():
            norm_val = normalized.get(key)
            if norm_val is not None:
                weighted_sum += norm_val * weight
                applied_weight += weight

        composite = round(weighted_sum / applied_weight, 1) if applied_weight > 0 else 0.0
        grade = grade_from_score(composite)

        score_obj, _ = BenchmarkScore.objects.update_or_create(
            campaign=campaign,
            operator=op,
            defaults={
                'composite_score': composite,
                'grade': grade,
                'kpi_scores': normalized,
                'kpi_values': raw_values,
                'sessions_count': session_count,
                'measurements_count': total,
            },
        )
        results.append(score_obj)

    campaign.status = 'COMPLETED'
    campaign.scored_at = timezone.now()
    campaign.save(update_fields=['status', 'scored_at'])

    return results


def campaign_trend(operator, months=12):
    """
    Compute operator performance trend from completed benchmark campaigns
    over the last N months. Returns a list of {date, score, grade} dicts.
    """
    from drive_test.models import BenchmarkScore

    cutoff = timezone.now() - timezone.timedelta(days=months * 30)
    scores = (
        BenchmarkScore.objects
        .filter(
            operator=operator,
            campaign__status='COMPLETED',
            campaign__scored_at__gte=cutoff,
        )
        .select_related('campaign')
        .order_by('campaign__date_from')
    )
    return [
        {
            'date': s.campaign.date_from.isoformat(),
            'label': s.campaign.name,
            'score': s.composite_score,
            'grade': s.grade,
        }
        for s in scores
    ]


def auto_assign_sessions(campaign):
    """Auto-populate a campaign's sessions based on date range and region."""
    from drive_test.models import DriveTestSession

    qs = DriveTestSession.objects.filter(
        test_date__gte=campaign.date_from,
        test_date__lte=campaign.date_to,
        status='COMPLETED',
    )
    if campaign.region_id:
        qs = qs.filter(region_id=campaign.region_id)
    campaign.sessions.set(qs)
    return qs.count()
