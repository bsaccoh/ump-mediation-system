"""Column-oriented, server-decimated time-series builder.

Returns arrays (not row objects) so the wire format is stable from 400 rows to
400k. Missing values stay ``None`` in the arrays — the client renders a gap,
never a zero. ``available_metrics`` / ``absent_metrics`` let a pane say
"not available in source data" instead of drawing an empty chart.
"""
from __future__ import annotations

from .geo import GeoQueryService
from .thresholds import METRIC_META

ALL_METRICS = list(METRIC_META.keys())


def build_timeseries(campaign, *, metrics, technology='', limit=3000):
    geo = GeoQueryService(campaign)
    qs = geo.samples(technology=technology, valid_only=True)
    ids, total, decimated = GeoQueryService.decimate(qs, limit)

    avail_counts = geo.available_metrics(ALL_METRICS)
    available = [m for m, c in avail_counts.items() if c > 0]
    absent = [m for m in ALL_METRICS if avail_counts.get(m, 0) == 0]
    metrics = [m for m in metrics if m in METRIC_META] or (
        [m for m in ('rsrp', 'ss_rsrp', 'rscp', 'rxlev') if m in available][:1]
    )

    if not ids:
        return {
            't': [], 'lat': [], 'lon': [], 'ids': [], 'tech': [], 'series': {},
            'meta': {'total': 0, 'returned': 0, 'decimated': False,
                     'available_metrics': available, 'absent_metrics': absent,
                     'metrics': metrics},
        }

    from drive_test.models import Sample
    fields = ['id', 'timestamp', 'latitude', 'longitude', 'technology'] + metrics
    rows = list(
        Sample.objects.filter(id__in=ids).order_by('timestamp', 'id').values(*fields)
    )

    t0 = rows[0]['timestamp']
    out_t, out_lat, out_lon, out_ids, out_tech = [], [], [], [], []
    series = {m: [] for m in metrics}
    for r in rows:
        dt = r['timestamp']
        out_t.append(int((dt - t0).total_seconds() * 1000) if dt and t0 else None)
        out_lat.append(r['latitude'])
        out_lon.append(r['longitude'])
        out_ids.append(r['id'])
        out_tech.append(r['technology'] or '')
        for m in metrics:
            series[m].append(r[m])

    return {
        't': out_t, 'lat': out_lat, 'lon': out_lon, 'ids': out_ids, 'tech': out_tech,
        'series': series,
        'meta': {
            'total': total, 'returned': len(rows), 'decimated': decimated,
            'available_metrics': available, 'absent_metrics': absent,
            'metrics': metrics,
            'units': {m: METRIC_META[m][1] for m in metrics},
            'labels': {m: METRIC_META[m][0] for m in metrics},
            't0': t0.isoformat() if t0 else None,
        },
    }
