"""Spatial clustering of events into problem areas.

Deterministic grid clustering (no sklearn): events of the same type falling in
the same ~275 m lat/lon cell become one ProblemArea. This is what turns
thousands of individual breach events into a handful of actionable areas on the
map. Idempotent per campaign.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict

from drive_test.models import Event, ProblemArea
from drive_test.models.enums import Severity

logger = logging.getLogger('drive_test')

GRID_DEG = 0.0025           # ~275 m at Sierra Leone latitudes
MIN_AREA_EVENTS = 2         # a lone event is not an "area"

_SEV_RANK = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}
_RANK_SEV = {v: k for k, v in _SEV_RANK.items()}

_TYPE_LABEL = {
    'NO_SERVICE': 'No Service', 'LOW_DL_THROUGHPUT': 'Low Throughput',
    'HIGH_LATENCY': 'High Latency', 'HIGH_PACKET_LOSS': 'High Packet Loss',
}


def _label(event_type):
    if event_type in _TYPE_LABEL:
        return _TYPE_LABEL[event_type]
    if event_type.startswith('POOR_'):
        return 'Poor ' + event_type[5:].replace('_', ' ').title()
    return event_type.replace('_', ' ').title()


def _haversine_m(a, b):
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi = math.radians(b[0] - a[0])
    dlam = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def cluster_problem_areas(campaign):
    """(Re)build ProblemAreas for a campaign from its events. Returns count."""
    ProblemArea.objects.filter(campaign=campaign).delete()  # SET_NULL frees events

    events = list(
        Event.objects.filter(campaign=campaign, latitude__isnull=False,
                             longitude__isnull=False)
        .values('id', 'event_type', 'severity', 'latitude', 'longitude', 'measured_value')
    )
    buckets = defaultdict(list)
    for e in events:
        key = (e['event_type'],
               round(e['latitude'] / GRID_DEG),
               round(e['longitude'] / GRID_DEG))
        buckets[key].append(e)

    created = 0
    for (event_type, _gy, _gx), members in buckets.items():
        if len(members) < MIN_AREA_EVENTS:
            continue
        lats = [m['latitude'] for m in members]
        lons = [m['longitude'] for m in members]
        vals = [m['measured_value'] for m in members if m['measured_value'] is not None]
        worst_rank = max(_SEV_RANK.get(m['severity'], 2) for m in members)
        bbox = (min(lats), min(lons), max(lats), max(lons))
        dist = _haversine_m((bbox[0], bbox[1]), (bbox[2], bbox[3]))

        area = ProblemArea.objects.create(
            campaign=campaign,
            area_type=_label(event_type),
            centroid_lat=sum(lats) / len(lats),
            centroid_lon=sum(lons) / len(lons),
            min_lat=bbox[0], min_lon=bbox[1], max_lat=bbox[2], max_lon=bbox[3],
            affected_distance_m=round(dist, 1),
            sample_count=len(members),
            severity=_RANK_SEV[worst_rank],
            stats={
                'event_type': event_type,
                'event_count': len(members),
                'mean_measured': round(sum(vals) / len(vals), 2) if vals else None,
            },
        )
        Event.objects.filter(id__in=[m['id'] for m in members]).update(problem_area=area)
        created += 1

    logger.info('Clustered %d problem area(s) for campaign %s', created, campaign.pk)
    return created
