"""Threshold-driven event-detection engine.

Scans a campaign's samples in time order and raises a network Event wherever a
metric breaches its poor/critical band (from KpiThreshold — never hard-coded),
or where a positioned sample has no serving signal at all. To avoid one event
per sample across a long poor stretch, breaches are grouped into **contiguous
runs** and one Event is emitted per run, anchored at its worst sample.

Only real breaches produce events — nothing is fabricated. Data-quality flags
(no_gps, invalid_coord, …) are NOT events; they live in the file's DQ report.
"""
from __future__ import annotations

import logging

from drive_test.models import Event, Sample
from drive_test.models.enums import EventStatus, Severity
from drive_test.services.thresholds import METRIC_META, classify, resolve_bands

logger = logging.getLogger('drive_test')

# Fixed event-type vocabulary (not free text). Metric-breach events are named
# POOR_<METRIC>; the rest are explicit.
NO_SERVICE = 'NO_SERVICE'
LOW_THROUGHPUT = 'LOW_DL_THROUGHPUT'
HIGH_LATENCY = 'HIGH_LATENCY'
HIGH_PACKET_LOSS = 'HIGH_PACKET_LOSS'

# Metrics evaluated against classification bands (poor/critical → breach).
_BAND_METRICS = ['rsrp', 'rsrq', 'sinr', 'ss_rsrp', 'ss_rsrq', 'ss_sinr',
                 'rscp', 'ecno', 'rxlev']

# Absolute-threshold data metrics (breach when worse than the bound).
_ABS_RULES = {
    LOW_THROUGHPUT: ('dl_throughput', 'lt', 1000.0),     # < 1 Mbps
    HIGH_LATENCY: ('latency_ms', 'gt', 200.0),           # > 200 ms
    HIGH_PACKET_LOSS: ('packet_loss', 'gt', 5.0),        # > 5 %
}

# Only runs at least this long become an event (filters transient single dips).
MIN_RUN = 2

_BREACH_RANKS = {'poor', 'critical'}
_SEVERITY_BY_COLOR = {'critical': Severity.CRITICAL, 'poor': Severity.HIGH}


def _engine_event_types():
    types = {NO_SERVICE} | set(_ABS_RULES)
    for m in _BAND_METRICS:
        types.add(f'POOR_{m.upper()}')
    return types


def _emit(campaign, event_type, run, kpi, threshold_value, severity):
    """Create one Event for a breach run, anchored at its worst sample."""
    worst = run[0]
    Event.objects.create(
        campaign=campaign,
        sample_id=worst['sample_id'],
        event_type=event_type,
        severity=severity,
        timestamp=worst['ts'],
        latitude=worst['lat'],
        longitude=worst['lon'],
        operator_id=worst['operator_id'],
        technology=worst['tech'],
        kpi=kpi,
        measured_value=worst['value'],
        threshold_value=threshold_value,
        status=EventStatus.OPEN,
    )


def _scan_metric(campaign, rows, metric, event_type, breach_fn, worst_key, threshold_value):
    """Walk time-ordered rows, grouping consecutive breaches into runs."""
    run = []
    count = 0

    def flush():
        nonlocal run, count
        if len(run) >= MIN_RUN:
            run.sort(key=worst_key)  # worst first
            sev = run[0].get('severity', Severity.HIGH)
            _emit(campaign, event_type, run, metric, threshold_value, sev)
            count += 1
        run = []

    for r in rows:
        val = r['value']
        breach, sev = breach_fn(val)
        if breach:
            r['severity'] = sev
            run.append(r)
        else:
            flush()
    flush()
    return count


def _rows(campaign, metric, technology=''):
    qs = Sample.objects.filter(campaign=campaign, is_valid=True,
                               **{f'{metric}__isnull': False})
    if technology:
        qs = qs.filter(technology=technology)
    out = []
    for s in qs.order_by('timestamp', 'id').values(
        'id', 'timestamp', 'latitude', 'longitude', 'operator_id', 'technology', metric,
    ):
        out.append({
            'sample_id': s['id'], 'ts': s['timestamp'],
            'lat': s['latitude'], 'lon': s['longitude'],
            'operator_id': s['operator_id'], 'tech': s['technology'] or '',
            'value': s[metric],
        })
    return out


def detect_events(campaign):
    """(Re)generate engine events for a campaign. Idempotent. Returns count."""
    # Clear only engine-generated events; leave any others intact.
    Event.objects.filter(campaign=campaign, event_type__in=_engine_event_types()).delete()

    total = 0
    # Band-classified RF metrics.
    for metric in _BAND_METRICS:
        rows = _rows(campaign, metric)
        if not rows:
            continue
        bands = resolve_bands(metric, '', campaign)
        if not bands:
            continue

        def breach_fn(val, _bands=bands):
            band = classify(val, _bands)
            if band and band['color'] in _BREACH_RANKS:
                return True, _SEVERITY_BY_COLOR.get(band['color'], Severity.HIGH)
            return False, None

        # worst = lowest value for higher-is-better metrics (all band metrics here).
        total += _scan_metric(
            campaign, rows, metric, f'POOR_{metric.upper()}',
            breach_fn, worst_key=lambda r: r['value'], threshold_value=None,
        )

    # Absolute-threshold data metrics.
    for event_type, (metric, op, bound) in _ABS_RULES.items():
        rows = _rows(campaign, metric)
        if not rows:
            continue
        if op == 'lt':
            def breach_fn(val, _b=bound): return (val < _b, Severity.MEDIUM)
            worst_key = lambda r: r['value']         # lowest first
        else:
            def breach_fn(val, _b=bound): return (val > _b, Severity.MEDIUM)
            worst_key = lambda r: -r['value']        # highest first
        total += _scan_metric(
            campaign, rows, metric, event_type,
            breach_fn, worst_key=worst_key, threshold_value=bound,
        )

    logger.info('Event detection for campaign %s produced %d events', campaign.pk, total)
    return total
