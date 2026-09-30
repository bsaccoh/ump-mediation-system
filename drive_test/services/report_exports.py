"""Raw and geospatial exports: CSV, GeoJSON, KML.

CSV records the active filter in a header comment so an extract is self-describing.
GeoJSON/KML colour points by the same threshold bands as the map, and always
label the route as observed measurement (never a predicted coverage polygon).
"""
from __future__ import annotations

import csv
import io
import json

from drive_test.services.geo import GeoQueryService

# Local band → hex (same values as the PDF renderer / CSS tokens).
_BAND_HEX = {
    'excellent': '#1a9850', 'good': '#91cf60', 'fair': '#fee08b',
    'poor': '#fc8d59', 'critical': '#d73027', 'none': '#9ca3af',
}

_CSV_FIELDS = ['id', 'timestamp', 'latitude', 'longitude', 'technology',
               'obs_cell_id', 'rsrp', 'rsrq', 'sinr', 'ss_rsrp', 'ss_sinr',
               'rscp', 'ecno', 'rxlev', 'dl_throughput', 'ul_throughput',
               'latency_ms', 'packet_loss']


def csv_export(campaign, technology='', limit=100000) -> bytes:
    from drive_test.models import Sample
    geo = GeoQueryService(campaign)
    qs = geo.samples(technology=technology, valid_only=False)
    ids, total, decimated = GeoQueryService.decimate(qs, limit)

    buf = io.StringIO()
    buf.write(f'# UMP Drive Test export — campaign="{campaign.name}"'
              f' technology="{technology or "ALL"}" rows={len(ids)}'
              f' of {total}{" (decimated)" if decimated else ""}\n')
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction='ignore')
    writer.writeheader()
    for row in (Sample.objects.filter(id__in=ids).order_by('timestamp', 'id')
                .values(*_CSV_FIELDS).iterator()):
        writer.writerow(row)
    return buf.getvalue().encode('utf-8')


def _colored_points(campaign, metric, technology=''):
    from drive_test.models import Sample
    from drive_test.services.thresholds import classify, resolve_bands
    geo = GeoQueryService(campaign)
    bands = resolve_bands(metric, technology, campaign) if metric else []
    qs = geo.samples(technology=technology, metric=metric) if metric else geo.samples(technology=technology)
    ids, _total, _dec = GeoQueryService.decimate(qs, 10000)
    fields = ['id', 'latitude', 'longitude', 'timestamp'] + ([metric] if metric else [])
    rows = list(Sample.objects.filter(id__in=ids).order_by('timestamp', 'id').values(*fields))
    for r in rows:
        val = r.get(metric) if metric else None
        band = classify(val, bands) if metric else None
        r['_color'] = _BAND_HEX.get(band['color'], '#9ca3af') if band else '#9ca3af'
        r['_val'] = val
    return rows


def _route(campaign, technology=''):
    geo = GeoQueryService(campaign)
    ids, _t, _d = GeoQueryService.decimate(geo.samples(technology=technology), 5000)
    from drive_test.models import Sample
    return list(Sample.objects.filter(id__in=ids).order_by('timestamp', 'id')
                .values_list('longitude', 'latitude'))  # GeoJSON is lon,lat


def geojson_export(campaign, metric='', technology='') -> bytes:
    features = []
    route = _route(campaign, technology)
    if len(route) > 1:
        features.append({
            'type': 'Feature',
            'properties': {'kind': 'route', 'label': 'Observed drive route'},
            'geometry': {'type': 'LineString', 'coordinates': [[lon, lat] for lon, lat in route]},
        })
    for r in _colored_points(campaign, metric, technology):
        if r['latitude'] is None:
            continue
        features.append({
            'type': 'Feature',
            'properties': {'kind': 'sample', 'metric': metric or None,
                           'value': r['_val'], 'marker-color': r['_color']},
            'geometry': {'type': 'Point', 'coordinates': [r['longitude'], r['latitude']]},
        })
    fc = {'type': 'FeatureCollection', 'features': features}
    return json.dumps(fc).encode('utf-8')


def kml_export(campaign, metric='', technology='') -> bytes:
    def esc(s):
        return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
             f'<name>{esc(campaign.name)} — drive test</name>']

    route = _route(campaign, technology)
    if len(route) > 1:
        coords = ' '.join(f'{lon},{lat},0' for lon, lat in route)
        parts.append('<Placemark><name>Observed drive route</name>'
                     '<Style><LineStyle><color>ff64748b</color><width>3</width></LineStyle></Style>'
                     f'<LineString><coordinates>{coords}</coordinates></LineString></Placemark>')

    for r in _colored_points(campaign, metric, technology):
        if r['latitude'] is None:
            continue
        # KML colour is aabbggrr; convert #rrggbb → ff bb gg rr.
        h = r['_color'].lstrip('#')
        kml_color = 'ff' + h[4:6] + h[2:4] + h[0:2]
        label = f'{metric}={r["_val"]}' if metric else 'sample'
        parts.append(
            f'<Placemark><name>{esc(label)}</name>'
            f'<Style><IconStyle><color>{kml_color}</color></IconStyle></Style>'
            f'<Point><coordinates>{r["longitude"]},{r["latitude"]},0</coordinates></Point></Placemark>')

    parts.append('</Document></kml>')
    return '\n'.join(parts).encode('utf-8')
