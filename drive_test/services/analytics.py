"""Campaign analytics — RF, coverage, data, voice and per-cell reports.

Reads through the ORM, summarises with services.stats, and classifies with
DB-resolved thresholds. Absent metrics are reported as absent (never zero-filled),
and thin samples are flagged rather than published as if precise.
"""
from __future__ import annotations

import math

from django.db.models import Avg, Count, Max, Min

from .stats import band_histogram, describe
from .thresholds import METRIC_META, resolve_bands
from .timeseries import ALL_METRICS

RF_METRICS = ['rsrp', 'rsrq', 'sinr', 'rssi', 'cqi', 'ss_rsrp', 'ss_rsrq',
              'ss_sinr', 'rscp', 'ecno', 'rxlev', 'rxqual']
DATA_METRICS = ['dl_throughput', 'ul_throughput', 'latency_ms', 'packet_loss']
COVERAGE_METRIC = {'LTE': 'rsrp', 'NR': 'ss_rsrp', 'UMTS': 'rscp', 'GSM': 'rxlev'}


def _values(campaign, metric, technology=''):
    from drive_test.models import Sample
    qs = Sample.objects.filter(campaign=campaign, is_valid=True, **{f'{metric}__isnull': False})
    if technology:
        qs = qs.filter(technology=technology)
    return list(qs.values_list(metric, flat=True))


def _available(campaign, metrics):
    from drive_test.models import Sample
    agg = {m: Count(m) for m in metrics}
    row = Sample.objects.filter(campaign=campaign).aggregate(**agg)
    return [m for m in metrics if (row.get(m) or 0) > 0]


def _haversine_km(a, b):
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def route_distance_km(campaign, technology=''):
    from drive_test.models import Sample
    qs = Sample.objects.filter(campaign=campaign, latitude__isnull=False, longitude__isnull=False)
    if technology:
        qs = qs.filter(technology=technology)
    pts = list(qs.order_by('timestamp', 'id').values_list('latitude', 'longitude'))
    total = 0.0
    for i in range(1, len(pts)):
        seg = _haversine_km(pts[i - 1], pts[i])
        if seg < 5:  # guard against GPS jumps inflating distance
            total += seg
    return round(total, 2)


def rf_report(campaign, technology=''):
    metrics = [m for m in _available(campaign, RF_METRICS)]
    reports = []
    for m in metrics:
        vals = _values(campaign, m, technology)
        if not vals:
            continue
        bands = resolve_bands(m, technology, campaign)
        reports.append({
            'metric': m,
            'label': METRIC_META[m][0],
            'unit': METRIC_META[m][1],
            'stats': describe(vals),
            'histogram': band_histogram(vals, bands),
        })
    return reports


def coverage_report(campaign, technology=''):
    avail = _available(campaign, list(COVERAGE_METRIC.values()))
    metric = COVERAGE_METRIC.get(technology) if technology else None
    if metric not in avail:
        metric = avail[0] if avail else None
    if not metric:
        return {'metric': None}

    vals = _values(campaign, metric, technology)
    bands = resolve_bands(metric, technology, campaign)
    hist = band_histogram(vals, bands)
    distance = route_distance_km(campaign, technology)
    # Approximate per-class distance by sample share (labelled as an estimate).
    for row in hist:
        row['distance_km'] = round(distance * row['pct'] / 100.0, 2) if distance else None
    return {
        'metric': metric,
        'label': METRIC_META[metric][0],
        'unit': METRIC_META[metric][1],
        'stats': describe(vals),
        'histogram': hist,
        'total_distance_km': distance,
        'distance_is_estimate': True,
    }


def data_report(campaign, technology=''):
    metrics = _available(campaign, DATA_METRICS)
    out = []
    for m in metrics:
        vals = _values(campaign, m, technology)
        out.append({'metric': m, 'label': METRIC_META[m][0], 'unit': METRIC_META[m][1],
                    'stats': describe(vals)})
    return out


def cell_report(campaign, limit=200):
    """Aggregate by observed serving cell id (no reference match required)."""
    from drive_test.models import Sample
    rows = (
        Sample.objects.filter(campaign=campaign).exclude(obs_cell_id='')
        .values('obs_cell_id', 'technology')
        .annotate(samples=Count('id'), avg_rsrp=Avg('rsrp'), avg_sinr=Avg('sinr'),
                  avg_rscp=Avg('rscp'), avg_ss_rsrp=Avg('ss_rsrp'),
                  first_seen=Min('timestamp'), last_seen=Max('timestamp'))
        .order_by('-samples')[:limit]
    )
    def r2(v):
        return round(v, 1) if v is not None else None
    return [{
        'cell_id': r['obs_cell_id'], 'technology': r['technology'] or '—',
        'samples': r['samples'],
        'avg_rsrp': r2(r['avg_rsrp']), 'avg_sinr': r2(r['avg_sinr']),
        'avg_rscp': r2(r['avg_rscp']), 'avg_ss_rsrp': r2(r['avg_ss_rsrp']),
        'first_seen': r['first_seen'], 'last_seen': r['last_seen'],
    } for r in rows]


# Voice call-event keyword vocabulary (used only if the log carried events).
_VOICE = {
    'attempt': ('attempt', 'setup', 'origination', 'call_attempt'),
    'drop': ('drop', 'dropped'),
    'block': ('block', 'blocked', 'fail', 'setup_timeout'),
}


def voice_report(campaign, technology=''):
    from drive_test.models import Sample
    qs = Sample.objects.filter(campaign=campaign).exclude(event_type='')
    if technology:
        qs = qs.filter(technology=technology)
    events = [e.lower() for e in qs.values_list('event_type', flat=True)]
    if not events:
        return {'has_data': False}
    def count(keys):
        return sum(1 for e in events if any(k in e for k in keys))
    attempts = count(_VOICE['attempt'])
    drops = count(_VOICE['drop'])
    blocks = count(_VOICE['block'])
    return {
        'has_data': True,
        'attempts': attempts, 'drops': drops, 'blocks': blocks,
        'cssr': round(100.0 * (attempts - blocks) / attempts, 1) if attempts else None,
        'dcr': round(100.0 * drops / attempts, 1) if attempts else None,
    }


def store_campaign_rollups(campaign):
    """Persist campaign-scope KpiResult rows for available metrics (idempotent)."""
    from drive_test.models import KpiResult
    metrics = _available(campaign, ALL_METRICS)
    for m in metrics:
        stats = describe(_values(campaign, m))
        if not stats:
            continue
        KpiResult.objects.update_or_create(
            campaign=campaign, scope_type=KpiResult.Scope.CAMPAIGN, scope_key='', metric=m,
            defaults={
                'count': stats['count'], 'min_value': stats['min'], 'mean_value': stats['mean'],
                'median_value': stats['median'], 'p10_value': stats['p10'],
                'p50_value': stats['p50'], 'p90_value': stats['p90'], 'max_value': stats['max'],
            },
        )
