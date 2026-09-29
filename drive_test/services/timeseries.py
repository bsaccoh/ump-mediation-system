"""
Session time-series assembly for the analysis workspace.

Returns COLUMN ARRAYS, not row objects. Index *i* refers to the same sample in
every column, which is what lets the map, the charts, the event timeline and the
detail pane share one cursor: a pane broadcasts an index, never a timestamp, so
no pane ever has to search for the nearest point.

410 samples today, 400k later — the wire format must not have to change, so
decimation happens here and is reported explicitly rather than applied silently.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Default cap on returned samples. Above this the series is decimated.
DEFAULT_MAX_POINTS = 4000

#: Technology codes. Small ints keep the payload compact and give the chart a
#: stable y-ordering (2G at the bottom, 5G at the top).
TECH_CODES = {'2G': 1, '3G': 2, '4G': 3, '5G': 4}

#: Metrics sourced from RadioMeasurement.
_RADIO_METRICS = {
    'rsrp': 'rsrp', 'rsrq': 'rsrq', 'sinr': 'sinr', 'rssi': 'rssi',
    'rscp': 'rscp', 'ecio': 'ecio', 'cqi': 'cqi',
    'ss_rsrp': 'ss_rsrp', 'ss_rsrq': 'ss_rsrq', 'ss_sinr': 'ss_sinr',
    'rxqual': 'rxqual', 'c_over_i': 'c_over_i',
    'dl_kbps': 'dl_throughput_kbps', 'ul_kbps': 'ul_throughput_kbps',
}

#: Metrics sourced from Measurement itself.
_CORE_METRICS = {
    'speed': 'speed_kmh',
    'heading': 'heading_deg',
    'altitude': 'altitude_m',
}

ALL_METRICS = list(_RADIO_METRICS) + list(_CORE_METRICS)


def _select_indices(values: list, max_points: int) -> list[int]:
    """Choose which sample indices to keep, preserving extremes.

    Buckets the series and keeps the argmin and argmax of `values` in each
    bucket. Naive stride decimation would drop the deepest point of a coverage
    hole — the single most important sample on the chart — so it is not used.

    `values` is the metric the caller is actually viewing. Every other column is
    carried along at the chosen indices, so all columns stay index-aligned.
    """
    total = len(values)
    if total <= max_points:
        return list(range(total))

    # Two indices per bucket (min and max), so half as many buckets as points.
    bucket_count = max(1, max_points // 2)
    size = total / bucket_count
    keep: set[int] = {0, total - 1}

    for b in range(bucket_count):
        start = int(b * size)
        end = min(int((b + 1) * size), total)
        if start >= end:
            continue

        lo_idx = hi_idx = None
        lo = hi = None
        for i in range(start, end):
            v = values[i]
            if v is None:
                continue
            if lo is None or v < lo:
                lo, lo_idx = v, i
            if hi is None or v > hi:
                hi, hi_idx = v, i

        if lo_idx is None:
            # Bucket is entirely null for this metric — keep its first sample so
            # the gap is still represented on the chart.
            keep.add(start)
        else:
            keep.add(lo_idx)
            keep.add(hi_idx)

    return sorted(keep)


def session_timeseries(session, *, metrics=None, primary='rsrp',
                       max_points=DEFAULT_MAX_POINTS) -> dict:
    """Build the column-oriented payload for one session.

    Args:
        session:    DriveTestSession instance.
        metrics:    metric keys to include; None means every available metric.
        primary:    the metric decimation preserves extremes of — normally the
                    one the user is viewing.
        max_points: cap on returned samples.
    """
    from drive_test.models import Measurement, MeasurementEvent

    requested = [m for m in (metrics or ALL_METRICS) if m in ALL_METRICS]

    rows = list(
        Measurement.objects
        .filter(drive_file__session=session, is_valid=True)
        .select_related('radio')
        .order_by('captured_at', 'drive_file_id', 'sequence_num')
        .values(
            'id', 'captured_at', 'latitude', 'longitude', 'matched_cell_id',
            'speed_kmh', 'heading_deg', 'altitude_m',
            'radio__technology',
            *{f'radio__{_RADIO_METRICS[m]}' for m in requested if m in _RADIO_METRICS},
        )
    )

    if not rows:
        return {
            't': [], 'lat': [], 'lon': [], 'tech': [], 'cell': [], 'ids': [],
            'series': {}, 'events': [],
            'meta': {
                'session_ref': session.session_ref,
                'source_count': 0, 'returned': 0, 'decimated': False,
                'available_metrics': [], 'absent_metrics': requested,
                'unsupported_metrics': _unsupported_metrics(session, requested),
                'primary_metric': None, 'started_at': None, 'event_count': 0,
            },
        }

    def _value(row, metric):
        if metric in _RADIO_METRICS:
            return row.get(f'radio__{_RADIO_METRICS[metric]}')
        return row.get(_CORE_METRICS[metric])

    # A metric with no non-null value anywhere is ABSENT, not zero. The client
    # uses this to say "not available in source data" rather than drawing an
    # empty chart that looks like a fault.
    available, absent = [], []
    for metric in requested:
        if any(_value(r, metric) is not None for r in rows):
            available.append(metric)
        else:
            absent.append(metric)

    # Of the absent metrics, which could the source format never have carried?
    # "This drive recorded no RSRP" and "this format cannot carry RSRP" are
    # different facts, and only the second is a property of the file. Reporting
    # them together would tell the reader nothing about which they are seeing.
    unsupported = _unsupported_metrics(session, absent)

    decimate_on = primary if primary in available else (available[0] if available else None)
    if decimate_on:
        indices = _select_indices([_value(r, decimate_on) for r in rows], max_points)
    else:
        indices = list(range(min(len(rows), max_points)))

    started = rows[0]['captured_at']

    def _offset_ms(dt):
        return int((dt - started).total_seconds() * 1000) if dt else 0

    kept = [rows[i] for i in indices]

    payload = {
        'ids': [r['id'] for r in kept],
        't': [_offset_ms(r['captured_at']) for r in kept],
        'lat': [r['latitude'] for r in kept],
        'lon': [r['longitude'] for r in kept],
        'tech': [TECH_CODES.get(r['radio__technology'] or '', 0) for r in kept],
        'cell': [r['matched_cell_id'] for r in kept],
        'series': {m: [_value(r, m) for r in kept] for m in available},
    }

    # Events carry their own timestamps and are NOT decimated — dropping an
    # event would hide the thing the engineer opened the session to find.
    # `idx` points at the nearest retained sample so a click can move the cursor.
    kept_offsets = payload['t']
    events = []
    for ev in (MeasurementEvent.objects
               .filter(session=session)
               .order_by('occurred_at')
               .values('id', 'occurred_at', 'event_type', 'severity',
                       'latitude', 'longitude', 'description')):
        offset = _offset_ms(ev['occurred_at'])
        events.append({
            'id': ev['id'],
            't': offset,
            'type': ev['event_type'],
            'severity': ev['severity'],
            'lat': ev['latitude'],
            'lon': ev['longitude'],
            'label': ev['description'],
            'idx': _nearest_index(kept_offsets, offset),
        })

    payload['events'] = events
    payload['meta'] = {
        'session_ref': session.session_ref,
        'source_count': len(rows),
        'returned': len(kept),
        'decimated': len(kept) < len(rows),
        'available_metrics': available,
        'absent_metrics': absent,
        'unsupported_metrics': unsupported,
        'primary_metric': decimate_on,
        'started_at': started.isoformat(),
        'event_count': len(events),
    }
    return payload


def _unsupported_metrics(session, absent: list[str]) -> list[str]:
    """Which absent metrics the session's source formats cannot carry at all.

    A session may hold files from several formats, so a metric counts as
    unsupported only when NO contributing format claims it — if any source
    could have produced it, its absence is a fact about the drive rather than
    about the format.

    A parser declaring no metrics makes no claim (the generic CSV importer maps
    whatever columns a file has), and no claim never marks anything unsupported.
    """
    from drive_test.models import DriveTestFile
    from drive_test.services.file_handler import capabilities_for

    profiles = {
        f.parser_profile
        for f in DriveTestFile.objects.filter(session=session)
                                      .select_related('parser_profile')
        if f.parser_profile_id
    }
    if not profiles:
        return []

    claims = [c for c in (capabilities_for(p) for p in profiles) if c and c.metrics]
    if not claims:
        return []

    return [m for m in absent if not any(c.supports(m) for c in claims)]


def _nearest_index(offsets: list[int], target: int) -> int | None:
    """Index of the retained sample closest in time to `target`."""
    if not offsets:
        return None

    lo, hi = 0, len(offsets) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if offsets[mid] < target:
            lo = mid + 1
        else:
            hi = mid

    if lo > 0 and abs(offsets[lo - 1] - target) <= abs(offsets[lo] - target):
        return lo - 1
    return lo
