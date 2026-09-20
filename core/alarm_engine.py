"""Alarm engine — evaluates live metrics against configurable thresholds.

Called periodically (from the monitoring service or a management command).
Creates / auto-resolves Alert rows so the monitoring dashboard shows real
alarms — never fabricated ones.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta
from pathlib import Path

import psutil
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from core.models import Alert, AlertThreshold

logger = logging.getLogger('mediation.alarm_engine')

_SEVERITY_MAP = {
    'warning': 'WARNING',
    'major': 'ERROR',
    'critical': 'CRITICAL',
}


def _resolve_stale(category: str, source: str):
    """Auto-resolve any open alerts for this metric that are now below threshold."""
    Alert.objects.filter(
        category=category, source=source, acknowledged=False,
    ).update(acknowledged=True, acknowledged_at=timezone.now())


def _raise_or_update(category: str, source: str, severity: str, message: str):
    """Create an alert if one isn't already open for this category+source+severity."""
    existing = Alert.objects.filter(
        category=category, source=source, acknowledged=False,
    ).first()
    if existing:
        if existing.severity == severity:
            return
        existing.severity = severity
        existing.message = message
        existing.save(update_fields=['severity', 'message'])
        return
    Alert.objects.create(
        category=category,
        source=source,
        severity=severity,
        message=message,
    )


def _evaluate_threshold(metric: str, current_value: float,
                        threshold: AlertThreshold, source: str):
    """Compare a live value against a threshold's three levels."""
    category = f'threshold_{metric}'
    if current_value >= threshold.critical_threshold:
        _raise_or_update(category, source, 'CRITICAL',
                         f'{threshold.get_metric_display()}: {current_value:.1f} '
                         f'(critical >= {threshold.critical_threshold})')
    elif current_value >= threshold.major_threshold:
        _raise_or_update(category, source, 'ERROR',
                         f'{threshold.get_metric_display()}: {current_value:.1f} '
                         f'(major >= {threshold.major_threshold})')
    elif current_value >= threshold.warning_threshold:
        _raise_or_update(category, source, 'WARNING',
                         f'{threshold.get_metric_display()}: {current_value:.1f} '
                         f'(warning >= {threshold.warning_threshold})')
    else:
        _resolve_stale(category, source)


def _count_files_in_dir(directory: Path) -> tuple[int, float]:
    """Return (file_count, oldest_file_age_seconds) for a directory tree."""
    count = 0
    oldest_mtime = None
    if not directory.is_dir():
        return 0, 0.0
    now = time.time()
    try:
        for entry in os.scandir(directory):
            if entry.is_file() and not entry.name.startswith('.'):
                count += 1
                try:
                    mtime = entry.stat().st_mtime
                    if oldest_mtime is None or mtime < oldest_mtime:
                        oldest_mtime = mtime
                except OSError:
                    pass
    except OSError:
        pass
    age = (now - oldest_mtime) if oldest_mtime else 0.0
    return count, age


def evaluate_hardware_thresholds(thresholds: dict[str, AlertThreshold]):
    """Evaluate CPU, memory, and disk thresholds against live psutil data."""
    hostname = getattr(settings, 'HOSTNAME', 'localhost')

    if 'cpu_percent' in thresholds:
        cpu = psutil.cpu_percent(interval=0.5)
        _evaluate_threshold('cpu_percent', cpu, thresholds['cpu_percent'], hostname)

    if 'memory_percent' in thresholds:
        mem = psutil.virtual_memory().percent
        _evaluate_threshold('memory_percent', mem, thresholds['memory_percent'], hostname)

    if 'disk_percent' in thresholds:
        try:
            disk = psutil.disk_usage(str(settings.UMP_STORAGE_ROOT)).percent
        except OSError:
            disk = psutil.disk_usage('/').percent
        _evaluate_threshold('disk_percent', disk, thresholds['disk_percent'], hostname)


def evaluate_collection_backlog(thresholds: dict[str, AlertThreshold]):
    """Count files in published input directories (UMP collection backlog)."""
    from collection.services.paths import PathBuilder

    if 'collection_backlog_count' not in thresholds and 'collection_backlog_age' not in thresholds:
        return

    total_count = 0
    max_age = 0.0
    operators = getattr(settings, 'OPERATORS', [])
    streams = ['msc', 'ims', 'pgw', 'sgsn', 'sgw', 'cbs']

    for operator in operators:
        for stream in streams:
            try:
                pub_dir = PathBuilder.input_published(operator, stream)
                count, age = _count_files_in_dir(pub_dir)
                total_count += count
                max_age = max(max_age, age)
            except (ValueError, OSError):
                pass

    if 'collection_backlog_count' in thresholds:
        _evaluate_threshold(
            'collection_backlog_count', total_count,
            thresholds['collection_backlog_count'], 'input_directories',
        )
    if 'collection_backlog_age' in thresholds:
        _evaluate_threshold(
            'collection_backlog_age', max_age,
            thresholds['collection_backlog_age'], 'input_directories',
        )


def evaluate_processing_backlog(thresholds: dict[str, AlertThreshold]):
    """Count CDRFiles stuck in COLLECTED/PENDING/PROCESSING states."""
    if 'processing_backlog_count' not in thresholds:
        return

    from collection.models import CDRFile
    backlog = CDRFile.objects.filter(
        status__in=['COLLECTED', 'PENDING', 'PROCESSING']
    ).count()
    _evaluate_threshold(
        'processing_backlog_count', backlog,
        thresholds['processing_backlog_count'], 'cdr_files',
    )


def evaluate_failure_counts(thresholds: dict[str, AlertThreshold]):
    """Count recent distribution and decoder failures within the evaluation window."""
    from collection.models import CDRFile, DistributionLog

    for metric_key, model, status_filter in [
        ('distribution_failure_count', DistributionLog,
         Q(status='FAILED')),
        ('decoder_failure_count', CDRFile,
         Q(status='FAILED')),
    ]:
        if metric_key not in thresholds:
            continue
        threshold = thresholds[metric_key]
        window = timezone.now() - timedelta(seconds=threshold.evaluation_window_seconds)
        if model == DistributionLog:
            count = model.objects.filter(status_filter, delivered_at__gte=window).count()
        else:
            count = model.objects.filter(status_filter, created_at__gte=window).count()
        _evaluate_threshold(metric_key, count, threshold, 'pipeline')


def evaluate_zero_record_files(thresholds: dict[str, AlertThreshold]):
    """Count CDRFiles marked EMPTY within the evaluation window."""
    if 'zero_record_file_count' not in thresholds:
        return

    threshold = thresholds['zero_record_file_count']
    window = timezone.now() - timedelta(seconds=threshold.evaluation_window_seconds)
    from collection.models import CDRFile
    count = CDRFile.objects.filter(status='EMPTY', created_at__gte=window).count()
    _evaluate_threshold('zero_record_file_count', count, threshold, 'pipeline')


def evaluate_worker_availability(thresholds: dict[str, AlertThreshold]):
    """Check the number of online Celery workers.

    Raises a CRITICAL alert if the live worker count drops below the
    configured threshold. Only runs when Celery is reachable.
    """
    if 'worker_count' not in thresholds:
        return

    threshold = thresholds['worker_count']
    try:
        from celery import current_app
        inspector = current_app.control.inspect(timeout=2)
        active = inspector.active()
        worker_count = len(active) if active else 0
    except Exception:
        # Celery unavailable — treat as zero workers
        worker_count = 0

    # worker_count threshold works inversely: alert fires when count is LOW.
    # Re-use the existing _evaluate_threshold but with inverted semantics by
    # checking manually: alert if worker_count < warning_threshold.
    category = 'threshold_worker_count'
    wt = threshold.warning_threshold
    mt = threshold.major_threshold
    ct = threshold.critical_threshold

    if worker_count <= ct:
        _raise_or_update(category, 'celery', 'CRITICAL',
                         f'Only {worker_count} Celery worker(s) online (critical <= {ct})')
    elif worker_count <= mt:
        _raise_or_update(category, 'celery', 'ERROR',
                         f'Only {worker_count} Celery worker(s) online (major <= {mt})')
    elif worker_count <= wt:
        _raise_or_update(category, 'celery', 'WARNING',
                         f'Only {worker_count} Celery worker(s) online (warning <= {wt})')
    else:
        _resolve_stale(category, 'celery')


def evaluate_service_health():
    """Check if mediation systemd services are running (production only)."""
    if not getattr(settings, 'SERVICE_MODE', False):
        return

    services = [
        'mediation-collector',
        'mediation-decoder',
        'mediation-distributor',
        'mediation-api',
    ]
    for svc in services:
        try:
            import subprocess
            result = subprocess.run(
                ['systemctl', 'is-active', f'{svc}.service'],
                capture_output=True, text=True, timeout=5,
            )
            is_active = result.stdout.strip() == 'active'
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue

        category = 'service_health'
        if not is_active:
            _raise_or_update(category, svc, 'CRITICAL',
                             f'Service {svc} is not running')
        else:
            _resolve_stale(category, svc)


def run_alarm_evaluation():
    """Main entry point — run all threshold evaluations.

    Returns a summary dict of what was evaluated.
    """
    thresholds = {
        t.metric: t
        for t in AlertThreshold.objects.filter(enabled=True)
    }
    if not thresholds:
        return {'evaluated': 0, 'note': 'No enabled alert thresholds configured'}

    results = {'evaluated': len(thresholds), 'checks': []}

    try:
        evaluate_hardware_thresholds(thresholds)
        results['checks'].append('hardware')
    except Exception:
        logger.exception('Hardware threshold evaluation failed')

    try:
        evaluate_collection_backlog(thresholds)
        results['checks'].append('collection_backlog')
    except Exception:
        logger.exception('Collection backlog evaluation failed')

    try:
        evaluate_processing_backlog(thresholds)
        results['checks'].append('processing_backlog')
    except Exception:
        logger.exception('Processing backlog evaluation failed')

    try:
        evaluate_failure_counts(thresholds)
        results['checks'].append('failure_counts')
    except Exception:
        logger.exception('Failure count evaluation failed')

    try:
        evaluate_service_health()
        results['checks'].append('service_health')
    except Exception:
        logger.exception('Service health evaluation failed')

    try:
        evaluate_zero_record_files(thresholds)
        results['checks'].append('zero_record_files')
    except Exception:
        logger.exception('Zero-record file evaluation failed')

    try:
        evaluate_worker_availability(thresholds)
        results['checks'].append('worker_availability')
    except Exception:
        logger.exception('Worker availability evaluation failed')

    active_alarms = Alert.objects.filter(acknowledged=False).count()
    results['active_alarms'] = active_alarms

    return results
