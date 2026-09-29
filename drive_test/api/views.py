"""JSON read APIs for the Map Analysis workspace.

All classification happens here (backend is the source of truth); the client
only paints. Payloads are decimated column/point arrays — never row dumps.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from drive_test.access import dt_view_required
from drive_test.models import Campaign, Sample
from drive_test.services.geo import GeoQueryService
from drive_test.services.thresholds import METRIC_META, classify, resolve_bands
from drive_test.services.timeseries import ALL_METRICS, build_timeseries


def _bbox_from_request(request):
    raw = request.GET.get('bbox', '').strip()
    if not raw:
        return None
    try:
        parts = [float(x) for x in raw.split(',')]
        if len(parts) == 4:
            return tuple(parts)
    except ValueError:
        pass
    return None


@login_required
@dt_view_required
def campaign_map(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    geo = GeoQueryService(campaign)
    technology = request.GET.get('technology', '').strip()
    metric = request.GET.get('metric', '').strip()
    limit = min(int(request.GET.get('limit', 6000) or 6000), 20000)
    bbox = _bbox_from_request(request)

    avail_counts = geo.available_metrics(ALL_METRICS)
    available = [m for m, c in avail_counts.items() if c > 0]
    absent = [m for m in ALL_METRICS if avail_counts.get(m, 0) == 0]
    if metric not in METRIC_META:
        metric = available[0] if available else ''

    bands = resolve_bands(metric, technology, campaign) if metric else []

    # Route track (all valid positions, decimated for the polyline).
    track_qs = geo.samples(technology=technology, bbox=bbox)
    track_ids, track_total, _ = GeoQueryService.decimate(track_qs, 3000)
    track_rows = list(
        Sample.objects.filter(id__in=track_ids).order_by('timestamp', 'id')
        .values_list('latitude', 'longitude')
    )
    route = [[lat, lon] for lat, lon in track_rows]

    # Coloured metric points (only rows carrying the metric).
    points = []
    if metric:
        pt_qs = geo.samples(technology=technology, metric=metric, bbox=bbox)
        pt_ids, pt_total, decimated = GeoQueryService.decimate(pt_qs, limit)
        rows = list(
            Sample.objects.filter(id__in=pt_ids).order_by('timestamp', 'id')
            .values('id', 'latitude', 'longitude', metric)
        )
        for r in rows:
            band = classify(r[metric], bands)
            points.append({
                'id': r['id'], 'lat': r['latitude'], 'lon': r['longitude'],
                'v': r[metric],
                'color': band['color'] if band else 'none',
                'band': band['label'] if band else 'Unclassified',
            })
    else:
        pt_total, decimated = 0, False

    # Centre on the track's midpoint when present.
    center = None
    if route:
        lats = [p[0] for p in route]
        lons = [p[1] for p in route]
        center = [sum(lats) / len(lats), sum(lons) / len(lons)]

    return JsonResponse({
        'route': route,
        'points': points,
        'center': center,
        'meta': {
            'metric': metric,
            'label': METRIC_META.get(metric, ('', ''))[0],
            'unit': METRIC_META.get(metric, ('', ''))[1],
            'bands': bands,
            'available_metrics': available,
            'absent_metrics': absent,
            'point_total': pt_total,
            'point_returned': len(points),
            'route_total': track_total,
            'decimated': decimated,
        },
    })


@login_required
@dt_view_required
def campaign_timeseries(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    metrics = [m for m in request.GET.get('metrics', '').split(',') if m]
    technology = request.GET.get('technology', '').strip()
    limit = min(int(request.GET.get('limit', 3000) or 3000), 10000)
    return JsonResponse(build_timeseries(
        campaign, metrics=metrics, technology=technology, limit=limit,
    ))


@login_required
@dt_view_required
def campaign_thresholds(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    technology = request.GET.get('technology', '').strip()
    out = {}
    for metric in METRIC_META:
        bands = resolve_bands(metric, technology, campaign)
        if bands:
            out[metric] = bands
    return JsonResponse({'thresholds': out})


@login_required
@dt_view_required
def sample_detail(request, pk):
    """Full sample inspector payload, grouped by layer, with provenance."""
    s = get_object_or_404(Sample.objects.select_related('cell', 'operator'), pk=pk)

    def block(pairs):
        return [{'k': k, 'v': v} for k, v in pairs]

    serving = {
        'observed': block([
            ('Cell ID', s.obs_cell_id or None), ('PCI', s.obs_pci),
            ('PSC', s.obs_psc), ('BSIC', s.obs_bsic), ('ARFCN', s.obs_arfcn),
            ('Band', s.band or None),
        ]),
        'matched': None,
        'match_method': s.get_match_method_display(),
        'match_confidence': s.match_confidence,
    }
    if s.cell_id:
        serving['matched'] = block([
            ('Cell', str(s.cell)), ('Site', s.cell.site_name or None),
            ('PCI', s.cell.pci), ('Band', s.cell.band or None),
        ])

    return JsonResponse({
        'id': s.id,
        'timestamp': s.timestamp.isoformat() if s.timestamp else None,
        'technology': s.technology or None,
        'position': block([
            ('Latitude', s.latitude), ('Longitude', s.longitude),
            ('Altitude', s.altitude), ('Speed', s.speed), ('Heading', s.heading),
            ('HDOP', s.hdop),
        ]),
        'identity': block([
            ('Operator', s.operator.name if s.operator else None),
            ('MCC', s.mcc or None), ('MNC', s.mnc or None), ('PLMN', s.plmn or None),
        ]),
        'serving_cell': serving,
        'rf': block([
            ('RSRP', s.rsrp), ('RSRQ', s.rsrq), ('SINR', s.sinr), ('RSSI', s.rssi),
            ('CQI', s.cqi), ('SS-RSRP', s.ss_rsrp), ('SS-RSRQ', s.ss_rsrq),
            ('SS-SINR', s.ss_sinr), ('RSCP', s.rscp), ('Ec/No', s.ecno),
            ('RxLev', s.rxlev), ('RxQual', s.rxqual),
        ]),
        'data': block([
            ('DL (kbps)', s.dl_throughput), ('UL (kbps)', s.ul_throughput),
            ('Latency (ms)', s.latency_ms), ('Packet Loss (%)', s.packet_loss),
        ]),
        'quality_flags': s.quality_flags or [],
    })
