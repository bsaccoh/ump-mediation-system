"""Turn ParsedSample objects into Sample model instances.

Pure mapping: it copies parsed values across and resolves the operator FK from
the parsed operator code / PLMN when possible. It never invents a value the
parse did not produce, and it keeps observed identifiers as-is (obs_*).
"""
from __future__ import annotations

from drive_test.models import Sample

# Fields copied verbatim from ParsedSample → Sample (same names).
_DIRECT = (
    'latitude', 'longitude', 'altitude', 'speed', 'heading', 'hdop',
    'technology', 'mcc', 'mnc', 'plmn',
    'obs_cell_id', 'obs_pci', 'obs_psc', 'obs_bsic', 'obs_arfcn',
    'frequency', 'band', 'channel',
    'rsrp', 'rsrq', 'sinr', 'rssi', 'cqi',
    'ss_rsrp', 'ss_rsrq', 'ss_sinr', 'rscp', 'ecno', 'rxlev', 'rxqual',
    'dl_throughput', 'ul_throughput', 'latency_ms', 'packet_loss',
    'event_type', 'event_status',
)


class OperatorResolver:
    """Caches reference.Operator lookups by code and by (mcc, mnc)."""

    def __init__(self):
        self._by_code = {}
        self._by_plmn = {}
        self._loaded = False

    def _load(self):
        from reference.models import Operator
        for op in Operator.objects.all():
            self._by_code[op.code.lower()] = op
            if op.home_mcc and op.home_mnc:
                self._by_plmn[(op.home_mcc, op.home_mnc)] = op
            if op.home_plmn:
                self._by_plmn[(op.home_plmn[:3], op.home_plmn[3:])] = op
        self._loaded = True

    def resolve(self, parsed):
        if not self._loaded:
            self._load()
        code = (parsed.operator_code or '').strip().lower()
        if code and code in self._by_code:
            return self._by_code[code]
        if parsed.mcc and parsed.mnc:
            return self._by_plmn.get((parsed.mcc, parsed.mnc))
        return None


def build_sample(parsed, *, campaign, drive_file, operator=None, timestamp=None) -> Sample:
    """Construct (unsaved) Sample from a ParsedSample.

    ``timestamp`` may be supplied as a fallback when the row had none, so a
    sample without its own clock still orders after the ones before it.
    """
    kwargs = {attr: getattr(parsed, attr) for attr in _DIRECT}
    return Sample(
        campaign=campaign,
        drive_file=drive_file,
        operator=operator,
        timestamp=parsed.timestamp or timestamp,
        **kwargs,
    )
