"""
Tier 3 — parser-level regression. No database.

Fingerprints what the parser YIELDS, before anything is persisted. Paired with
the Tier 1 database fingerprint this localises a regression precisely:

    Tier 3 fails, Tier 1 fails  →  the parser changed
    Tier 3 passes, Tier 1 fails →  persistence changed (the ss_rsrp class of bug)

Runs in seconds because it never touches the ORM.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, datetime

from django.test import SimpleTestCase

from drive_test.parsers.trp_parser import TrpDriveTestParser

from . import compare_or_write, corpus_files

# Scalar fields whose presence we count. Collections (neighbours, events,
# carriers, beams) are counted separately once the contract widens.
_TRACKED = [
    'technology', 'rssi', 'rscp', 'ecio', 'rsrp', 'rsrq', 'sinr', 'cqi',
    'ss_rsrp', 'ss_rsrq', 'ss_sinr', 'dl_throughput_kbps', 'ul_throughput_kbps',
    'latitude', 'longitude', 'altitude_m', 'speed_kmh', 'heading_deg',
    'gps_accuracy_m', 'gps_hdop',
    'rxqual', 'c_over_i',
    'obs_mcc', 'obs_mnc', 'obs_lac', 'obs_ci', 'obs_tac', 'obs_eci',
    'obs_pci', 'obs_earfcn', 'obs_nrarfcn',
    'service_type', 'service_outcome', 'call_setup_time_ms', 'call_duration_s',
    'mos', 'throughput_kbps', 'latency_ms', 'packet_loss_pct',
]

#: Collections on ParsedMeasurement. Counted rather than presence-checked —
#: a format that carries none yields 0, which is a fact worth asserting.
_COLLECTIONS = ['neighbours', 'carriers', 'beams', 'events']


def _encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _is_present(value) -> bool:
    """Empty string counts as absent; 0 and 0.0 do NOT.

    SINR 0 dB and CQI 0 are real readings. Treating them as missing is the
    exact mistake this suite exists to catch.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value != ''
    return True


class TrpParserStreamTests(SimpleTestCase):
    """Fingerprint the ParsedMeasurement stream for every corpus file."""

    def test_parser_stream_is_stable(self):
        files = corpus_files()
        if not files:
            self.skipTest('TRP corpus not present (data/drive-test/raw/*/trp/)')

        for source in files:
            with self.subTest(file=source.name):
                self._check(source)

    def _check(self, source):
        parser = TrpDriveTestParser()

        digest = hashlib.sha256()
        count = 0
        present = {f: 0 for f in _TRACKED}
        collected = {c: 0 for c in _COLLECTIONS}
        samples_with = {c: 0 for c in _COLLECTIONS}
        quality_flags: dict[str, int] = {}
        first_ts = last_ts = None

        for pm in parser.parse(source):
            count += 1
            row = dataclasses.asdict(pm)

            # raw_data is parser-internal overflow and is intentionally excluded:
            # it holds vendor columns that may legitimately vary between releases.
            row.pop('raw_data', None)

            digest.update(
                json.dumps(row, sort_keys=True, default=_encode).encode('utf-8')
            )

            for field in _TRACKED:
                if _is_present(row.get(field)):
                    present[field] += 1

            for name in _COLLECTIONS:
                items = getattr(pm, name, None) or []
                collected[name] += len(items)
                if items:
                    samples_with[name] += 1

            for flag in (pm.quality_flags or []):
                quality_flags[flag] = quality_flags.get(flag, 0) + 1

            if pm.captured_at is not None:
                if first_ts is None or pm.captured_at < first_ts:
                    first_ts = pm.captured_at
                if last_ts is None or pm.captured_at > last_ts:
                    last_ts = pm.captured_at

        actual = {
            'measurement_count': count,
            'stream_sha256': digest.hexdigest(),
            'present_counts': present,
            'absent_counts': {f: count - n for f, n in present.items()},
            'collection_item_counts': collected,
            'samples_with_collection': samples_with,
            'quality_flags': dict(sorted(quality_flags.items())),
            'first_captured_at': first_ts.isoformat() if first_ts else None,
            'last_captured_at': last_ts.isoformat() if last_ts else None,
        }

        compare_or_write(self, actual, source, 'parser')
