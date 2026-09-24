"""
Drive Test Analysis Engine

Evaluates Measurement rows against active RegulatoryRule/RegulatoryThreshold
definitions and bulk-creates Finding rows.

Also computes the DataQualityResult for a DriveTestFile.

Design constraints:
  - Never row-by-row: all writes use bulk_create / bulk_update
  - Never reads raw drive-test files (only DB rows)
  - Findings reference exact measurements and thresholds for traceability
"""
from __future__ import annotations

import logging
import operator as op_module
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Condition string → Python operator function
_OPS = {
    'lt': op_module.lt,
    '<': op_module.lt,
    'le': op_module.le,
    '<=': op_module.le,
    'gt': op_module.gt,
    '>': op_module.gt,
    'ge': op_module.ge,
    '>=': op_module.ge,
    'eq': op_module.eq,
    '==': op_module.eq,
    'ne': op_module.ne,
    '!=': op_module.ne,
}

# Metric name → how to extract a value from Measurement + related objects
# Resolved at runtime so we don't hold DB objects in memory during bulk ops.
_METRIC_EXTRACTORS: dict[str, str] = {
    # Radio metrics (from RadioMeasurement)
    'rsrp': 'radio.rsrp',
    'rsrq': 'radio.rsrq',
    'sinr': 'radio.sinr',
    'rssi': 'radio.rssi',
    'rscp': 'radio.rscp',
    'ecio': 'radio.ecio',
    'cqi': 'radio.cqi',
    'dl_throughput_kbps': 'radio.dl_throughput_kbps',
    'ul_throughput_kbps': 'radio.ul_throughput_kbps',
    # Service metrics (from ServiceMeasurement — uses the first matching service row)
    'call_setup_time_ms': 'service.call_setup_time_ms',
    'mos': 'service.mos',
    'latency_ms': 'service.latency_ms',
    'packet_loss_pct': 'service.packet_loss_pct',
    'throughput_kbps': 'service.throughput_kbps',
}


def _resolve_metric(measurement, metric: str) -> float | None:
    """Extract the numeric value for metric from a Measurement instance (with select_related)."""
    parts = _METRIC_EXTRACTORS.get(metric, '').split('.')
    if len(parts) != 2:
        return None
    source, attr = parts
    if source == 'radio':
        radio = getattr(measurement, '_radio_cache', None)
        if radio is None:
            return None
        return getattr(radio, attr, None)
    if source == 'service':
        services = getattr(measurement, '_service_cache', [])
        for svc in services:
            val = getattr(svc, attr, None)
            if val is not None:
                return val
    return None


def _finding_type_for_metric(metric: str) -> str:
    mapping = {
        'rsrp': 'WEAK_SIGNAL',
        'rsrq': 'HIGH_INTERFERENCE',
        'sinr': 'HIGH_INTERFERENCE',
        'rssi': 'WEAK_SIGNAL',
        'rscp': 'WEAK_SIGNAL',
        'ecio': 'HIGH_INTERFERENCE',
        'call_setup_time_ms': 'FAILED_CALL',
        'mos': 'DROP_CALL',
        'latency_ms': 'HIGH_LATENCY',
        'packet_loss_pct': 'POOR_DATA',
        'dl_throughput_kbps': 'LOW_THROUGHPUT',
        'ul_throughput_kbps': 'LOW_THROUGHPUT',
        'throughput_kbps': 'LOW_THROUGHPUT',
    }
    return mapping.get(metric, 'THRESHOLD_BREACH')


class AnalysisEngine:
    """
    Runs threshold evaluation for all measurements in a DriveTestFile.

    Usage:
        engine = AnalysisEngine(drive_file)
        finding_count = engine.run()
    """

    BATCH_SIZE = 500

    def __init__(self, drive_file):
        self.drive_file = drive_file
        self.session = drive_file.session
        self.operator_id = self.session.operator_id
        self.today = date.today()

    def run(self) -> int:
        """Run the full analysis. Returns the number of Findings created."""
        rules_with_thresholds = self._load_rules()
        if not rules_with_thresholds:
            logger.info('No active regulatory rules — skipping analysis for file %d', self.drive_file.id)
            return 0

        total_findings = 0
        offset = 0

        while True:
            batch = self._load_measurement_batch(offset)
            if not batch:
                break

            findings = self._evaluate_batch(batch, rules_with_thresholds)
            if findings:
                from drive_test.models import Finding
                Finding.objects.bulk_create(findings, ignore_conflicts=True)
                total_findings += len(findings)

            offset += self.BATCH_SIZE

        # Update session finding count
        from drive_test.models import Finding
        self.session.finding_count = Finding.objects.filter(session=self.session).count()
        self.session.save(update_fields=['finding_count'])

        logger.info('Analysis complete: %d findings for file %d', total_findings, self.drive_file.id)
        return total_findings

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_rules(self) -> list[tuple]:
        """
        Load active (rule, threshold) pairs applicable to this operator.
        Returns list of (RegulatoryRule, RegulatoryThreshold, op_fn).
        """
        from drive_test.models import RegulatoryThreshold
        thresholds = (
            RegulatoryThreshold.objects
            .filter(
                rule__is_active=True,
                rule__effective_from__lte=self.today,
                effective_from__lte=self.today,
            )
            .filter(
                rule__effective_to__isnull=True,
                effective_to__isnull=True,
            )
            .filter(
                # Threshold applies to this operator or to all operators
                operator__isnull=True,
            )
            .select_related('rule')
        )
        # Also include operator-specific thresholds
        from django.db.models import Q
        thresholds = (
            RegulatoryThreshold.objects
            .filter(
                rule__is_active=True,
                rule__effective_from__lte=self.today,
            )
            .filter(
                Q(rule__effective_to__isnull=True) | Q(rule__effective_to__gte=self.today)
            )
            .filter(
                Q(operator__isnull=True) | Q(operator_id=self.operator_id)
            )
            .filter(
                effective_from__lte=self.today,
            )
            .filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=self.today)
            )
            .select_related('rule')
        )

        result = []
        for t in thresholds:
            fn = _OPS.get(t.rule.condition)
            if fn is None:
                continue
            result.append((t.rule, t, fn))
        return result

    def _load_measurement_batch(self, offset: int) -> list:
        """Load a batch of measurements with their radio and service relations pre-loaded."""
        from drive_test.models import Measurement

        measurements = list(
            Measurement.objects
            .filter(drive_file=self.drive_file, is_valid=True)
            .prefetch_related('radio', 'services')
            .order_by('sequence_num')[offset:offset + self.BATCH_SIZE]
        )
        # Cache radio and service on each measurement for fast lookup
        for m in measurements:
            try:
                m._radio_cache = m.radio
            except Exception:
                m._radio_cache = None
            m._service_cache = list(m.services.all())
        return measurements

    def _evaluate_batch(self, measurements: list, rules: list[tuple]) -> list:
        """Check each measurement against all rules. Return Finding objects (unsaved)."""
        from drive_test.models import Finding

        findings = []
        for m in measurements:
            for rule, threshold, op_fn in rules:
                # Technology filter
                if rule.technology and m._radio_cache:
                    if getattr(m._radio_cache, 'technology', '') != rule.technology:
                        continue

                value = _resolve_metric(m, rule.metric)
                if value is None:
                    continue

                # Check against critical threshold first, then warning
                breaches_critical = op_fn(value, threshold.critical_value)
                if not breaches_critical:
                    if threshold.warning_value is not None:
                        breaches_warning = op_fn(value, threshold.warning_value)
                        if not breaches_warning:
                            continue
                        severity = 'MEDIUM'
                    else:
                        continue
                else:
                    severity = 'CRITICAL' if abs(value - threshold.critical_value) > abs(threshold.critical_value) * 0.3 else 'HIGH'

                findings.append(Finding(
                    session=self.session,
                    measurement=m,
                    finding_type=_finding_type_for_metric(rule.metric),
                    severity=severity,
                    latitude=m.latitude,
                    longitude=m.longitude,
                    cell=m.matched_cell,
                    description=(
                        f'{rule.name}: observed {rule.metric}={value:.2f}{threshold.unit} '
                        f'({rule.condition} {threshold.critical_value}{threshold.unit})'
                    ),
                    measured_value=value,
                    threshold_value=threshold.critical_value,
                    threshold=threshold,
                ))

        return findings


class QualityAssessor:
    """Computes DataQualityResult for a DriveTestFile after processing."""

    def assess(self, drive_file) -> 'DataQualityResult':
        from drive_test.models import DataQualityResult, Measurement

        qs = Measurement.objects.filter(drive_file=drive_file)
        total = qs.count()
        valid = qs.filter(is_valid=True).count()
        invalid = total - valid
        missing_gps = qs.filter(latitude=0.0, longitude=0.0).count()
        missing_cell = qs.filter(
            obs_mcc='', obs_mnc='', obs_lac__isnull=True,
            obs_ci__isnull=True, obs_tac__isnull=True, obs_eci__isnull=True,
        ).count()
        matched = qs.filter(matched_cell__isnull=False).count()
        unmatched = valid - matched

        match_rate = (matched / valid * 100) if valid > 0 else 0.0
        completeness = ((total - invalid - missing_gps) / total * 100) if total > 0 else 0.0
        accuracy = match_rate
        overall = (completeness * 0.5 + accuracy * 0.5)

        issues = []
        if missing_gps > 0:
            issues.append(f'{missing_gps} records missing GPS coordinates')
        if missing_cell > 0:
            issues.append(f'{missing_cell} records missing cell identifiers')
        if unmatched > 0:
            issues.append(f'{unmatched} records could not be matched to reference cells')

        result, _ = DataQualityResult.objects.update_or_create(
            drive_file=drive_file,
            defaults=dict(
                total_records=total,
                valid_records=valid,
                invalid_records=invalid,
                missing_gps=missing_gps,
                missing_cell_id=missing_cell,
                matched_cells=matched,
                unmatched_cells=unmatched,
                match_rate_pct=round(match_rate, 2),
                completeness_score=round(completeness, 2),
                accuracy_score=round(accuracy, 2),
                overall_score=round(overall, 2),
                issues=issues,
            ),
        )
        return result
