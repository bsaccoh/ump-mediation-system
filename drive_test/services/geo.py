"""GeoQueryService — the spatial seam.

All spatial access to samples goes through here so the storage (FloatField +
bbox today) can become PostGIS later without changing callers. Decimation is
server-side: the browser never receives millions of raw points.
"""
from __future__ import annotations

from django.db.models import Count


class GeoQueryService:
    def __init__(self, campaign):
        self.campaign = campaign

    def samples(self, *, technology='', operator_id=None, valid_only=True,
                metric=None, bbox=None):
        """Return a Sample queryset scoped to the campaign and filters.

        ``bbox`` is (min_lat, min_lon, max_lat, max_lon). When ``metric`` is
        given, rows lacking that metric are excluded (so the map plots only
        classifiable points).
        """
        from drive_test.models import Sample

        qs = Sample.objects.filter(campaign=self.campaign,
                                   latitude__isnull=False, longitude__isnull=False)
        if valid_only:
            qs = qs.filter(is_valid=True)
        if technology:
            qs = qs.filter(technology=technology)
        if operator_id:
            qs = qs.filter(operator_id=operator_id)
        if metric:
            qs = qs.filter(**{f'{metric}__isnull': False})
        if bbox:
            min_lat, min_lon, max_lat, max_lon = bbox
            qs = qs.filter(latitude__gte=min_lat, latitude__lte=max_lat,
                           longitude__gte=min_lon, longitude__lte=max_lon)
        return qs

    @staticmethod
    def decimate(qs, limit):
        """Even-stride decimation preserving temporal order and coverage.

        Returns (rows, total, decimated) where rows respects ``limit``. Stride
        sampling keeps the route shape rather than truncating to a prefix.
        """
        total = qs.count()
        qs = qs.order_by('timestamp', 'id')
        if limit and total > limit:
            stride = total // limit + 1
            ids = list(qs.values_list('id', flat=True))
            keep = ids[::stride]
            return keep, total, True
        return list(qs.values_list('id', flat=True)), total, False

    def available_metrics(self, metrics):
        """Return {metric: non_null_count} for the given metric keys, one query."""
        from drive_test.models import Sample

        agg = {m: Count(m) for m in metrics}
        row = Sample.objects.filter(campaign=self.campaign).aggregate(**agg)
        return {m: (row.get(m) or 0) for m in metrics}
