"""Data-quality evaluation for an ingest.

Data-quality problems are NOT network findings — they describe the log, not the
network, and are kept in their own report. The accumulator runs in one pass over
the parsed stream so a large file is never scanned twice.
"""
from __future__ import annotations

_RF_ATTRS = ('rsrp', 'rsrq', 'sinr', 'rssi', 'ss_rsrp', 'ss_sinr', 'rscp', 'ecno', 'rxlev')


def _has_rf(parsed) -> bool:
    return any(getattr(parsed, a) is not None for a in _RF_ATTRS)


def _valid_coord(lat, lon) -> bool:
    if lat is None or lon is None:
        return False
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    if lat == 0 and lon == 0:  # null-island: almost always a bad fix
        return False
    return True


class QualityAccumulator:
    """Single-pass data-quality tally over ParsedSample objects."""

    def __init__(self):
        self.total = 0
        self.gps_present = 0
        self.invalid_coord = 0
        self.ts_present = 0
        self.non_monotonic = 0
        self.rf_present = 0
        self.duplicate = 0
        self._last_ts = None
        self._seen = set()

    def evaluate(self, parsed):
        """Tally one sample; return (is_valid, quality_flags)."""
        self.total += 1
        flags = []

        has_coord = parsed.latitude is not None and parsed.longitude is not None
        coord_ok = _valid_coord(parsed.latitude, parsed.longitude)
        if has_coord and coord_ok:
            self.gps_present += 1
        elif has_coord and not coord_ok:
            self.invalid_coord += 1
            flags.append('invalid_coord')
        else:
            flags.append('no_gps')

        if parsed.timestamp is not None:
            self.ts_present += 1
            if self._last_ts is not None and parsed.timestamp < self._last_ts:
                self.non_monotonic += 1
                flags.append('non_monotonic_time')
            self._last_ts = parsed.timestamp
        else:
            flags.append('no_timestamp')

        if _has_rf(parsed):
            self.rf_present += 1
        else:
            flags.append('no_rf')

        if parsed.timestamp is not None:
            key = (parsed.timestamp, parsed.latitude, parsed.longitude, parsed.obs_cell_id)
            if key in self._seen:
                self.duplicate += 1
                flags.append('duplicate')
            else:
                self._seen.add(key)

        is_valid = coord_ok or has_coord is False  # invalid only on a bad coord
        if 'invalid_coord' in flags:
            is_valid = False
        return is_valid, flags

    def summary(self) -> tuple[float | None, dict]:
        """Return (overall_score_pct, report_dict). Score is None for an empty
        file — never a misleading 0.
        """
        t = self.total
        if t == 0:
            return None, {'total_samples': 0, 'note': 'No samples parsed.'}

        gps_cov = 100.0 * self.gps_present / t
        ts_valid = 100.0 * self.ts_present / t
        rf_valid = 100.0 * self.rf_present / t
        dup_pct = 100.0 * self.duplicate / t
        invalid_pct = 100.0 * self.invalid_coord / t

        # Weighted overall: coverage of usable dimensions minus defects.
        overall = max(0.0, min(100.0,
            0.35 * gps_cov + 0.25 * ts_valid + 0.30 * rf_valid
            + 0.10 * 100.0 - dup_pct - invalid_pct))

        report = {
            'total_samples': t,
            'gps_coverage_pct': round(gps_cov, 1),
            'valid_timestamps_pct': round(ts_valid, 1),
            'valid_rf_samples_pct': round(rf_valid, 1),
            'duplicate_pct': round(dup_pct, 2),
            'invalid_coord_pct': round(invalid_pct, 2),
            'non_monotonic_time': self.non_monotonic,
            'counts': {
                'gps_present': self.gps_present,
                'invalid_coord': self.invalid_coord,
                'timestamp_present': self.ts_present,
                'rf_present': self.rf_present,
                'duplicate': self.duplicate,
            },
        }
        return round(overall, 1), report
