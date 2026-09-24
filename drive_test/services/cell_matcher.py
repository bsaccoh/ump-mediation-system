"""
Cell Reference Matcher

Matches observed raw cell identifiers from drive-test measurements against
the authoritative Cell reference table using a cascade of strategies:

  1. Exact match  — obs identifiers fully match a Cell row
  2. PCI+EARFCN   — 4G physical-layer match (high confidence without full ECGI)
  3. Proximity    — nearest active cell within max_distance_m (GPS-based fallback)

The three data tiers are strictly respected:
  A  obs_* fields on Measurement  — never modified here
  B  matched_cell FK               — set from Cell table (authoritative)
  C  match_confidence              — derived score

Usage:
    matcher = CellReferenceMatcher(operator_id=1)
    results = matcher.match_bulk(measurements_qs)
    # returns list of (measurement_id, cell_id, confidence, method)
"""

import logging
import math
from typing import Optional

from django.db.models import QuerySet

logger = logging.getLogger(__name__)

_EARTH_RADIUS_M = 6_371_000.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres between two GPS coordinates."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


class CellReferenceMatcher:
    """
    Matches a queryset of Measurement rows to Cell reference rows.

    All DB queries are batched; measurements are updated via bulk_update
    to avoid row-by-row saves.
    """

    def __init__(
        self,
        operator_id: int,
        max_distance_m: float = 3000.0,
        confidence_exact: float = 1.0,
        confidence_pci: float = 0.85,
        confidence_proximity: float = 0.60,
    ):
        self.operator_id = operator_id
        self.max_distance_m = max_distance_m
        self.confidence_exact = confidence_exact
        self.confidence_pci = confidence_pci
        self.confidence_proximity = confidence_proximity

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match_bulk(self, measurements_qs: QuerySet) -> int:
        """
        Match all unmatched measurements in the queryset.
        Returns the number of measurements that were successfully matched.
        """
        from drive_test.models import Measurement, Cell

        # Load active cells for this operator into memory (reference data is small)
        cells = list(
            Cell.objects.filter(operator_id=self.operator_id, is_active=True)
            .select_related('sector__site')
        )

        # Build lookup structures
        cgi_index: dict[str, object] = {}
        ecgi_index: dict[str, object] = {}
        pci_earfcn_index: dict[tuple, list] = {}

        for cell in cells:
            if cell.cgi:
                cgi_index[cell.cgi] = cell
            if cell.ecgi:
                ecgi_index[cell.ecgi] = cell
            if cell.pci is not None and cell.earfcn is not None:
                key = (cell.pci, cell.earfcn)
                pci_earfcn_index.setdefault(key, []).append(cell)

        unmatched = list(
            measurements_qs.filter(matched_cell__isnull=True, is_valid=True)
        )
        if not unmatched:
            return 0

        matched_count = 0
        to_update = []

        for m in unmatched:
            cell, confidence, method = self._match_single(
                m, cgi_index, ecgi_index, pci_earfcn_index, cells
            )
            if cell is not None:
                m.matched_cell = cell
                m.match_confidence = confidence
                m.match_method = method
                to_update.append(m)
                matched_count += 1

        if to_update:
            Measurement.objects.bulk_update(
                to_update, ['matched_cell', 'match_confidence', 'match_method']
            )

        logger.info(
            'Cell matching complete: %d/%d matched for operator_id=%d',
            matched_count, len(unmatched), self.operator_id,
        )
        return matched_count

    # ------------------------------------------------------------------
    # Private matching strategies
    # ------------------------------------------------------------------

    def _match_single(self, m, cgi_index, ecgi_index, pci_earfcn_index, cells):
        # Strategy 1: exact CGI match (2G/3G)
        if m.obs_mcc and m.obs_mnc and m.obs_lac is not None and m.obs_ci is not None:
            cgi = f'{m.obs_mcc}-{m.obs_mnc}-{m.obs_lac}-{m.obs_ci}'
            if cgi in cgi_index:
                return cgi_index[cgi], self.confidence_exact, 'exact_cgi'

        # Strategy 2: exact ECGI match (4G)
        if m.obs_mcc and m.obs_mnc and m.obs_eci is not None:
            ecgi = f'{m.obs_mcc}-{m.obs_mnc}-{m.obs_eci}'
            if ecgi in ecgi_index:
                return ecgi_index[ecgi], self.confidence_exact, 'exact_ecgi'

        # Strategy 3: PCI + EARFCN match (4G — high confidence physical layer)
        if m.obs_pci is not None and m.obs_earfcn is not None:
            candidates = pci_earfcn_index.get((m.obs_pci, m.obs_earfcn), [])
            if len(candidates) == 1:
                return candidates[0], self.confidence_pci, 'pci_earfcn'
            elif len(candidates) > 1 and m.latitude and m.longitude:
                # Multiple cells share the same PCI/EARFCN — pick nearest
                nearest = self._nearest_cell(m.latitude, m.longitude, candidates)
                if nearest:
                    return nearest, self.confidence_pci, 'pci_earfcn_proximity'

        # Strategy 4: GPS proximity fallback
        if m.latitude and m.longitude:
            geo_cells = [c for c in cells if c.latitude is not None and c.longitude is not None]
            nearest = self._nearest_cell(m.latitude, m.longitude, geo_cells)
            if nearest:
                return nearest, self.confidence_proximity, 'proximity'

        return None, None, ''

    def _nearest_cell(self, lat: float, lon: float, candidates: list) -> Optional[object]:
        best_cell = None
        best_dist = self.max_distance_m

        for cell in candidates:
            if cell.latitude is None or cell.longitude is None:
                continue
            dist = _haversine_m(lat, lon, cell.latitude, cell.longitude)
            if dist < best_dist:
                best_dist = dist
                best_cell = cell

        return best_cell
