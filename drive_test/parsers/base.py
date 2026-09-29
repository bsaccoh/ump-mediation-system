"""Parser framework for drive-test logs.

A parser reads one file and yields ``ParsedSample`` objects in the canonical,
RAT-agnostic shape. Parsers never write to the database — normalization and
persistence are the service layer's job. A field the source does not carry is
left ``None`` (never coerced to 0), so "missing" stays distinct from "zero".

New vendor formats are added by subclassing ``DriveTestParser`` and registering
it — no caller changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator


# ---------------------------------------------------------------------------
# Canonical parsed shapes
# ---------------------------------------------------------------------------

@dataclass
class ParsedNeighbour:
    rank: int = 0
    rat: str = ''
    obs_pci: int | None = None
    obs_psc: int | None = None
    obs_bsic: int | None = None
    obs_arfcn: int | None = None
    rsrp: float | None = None
    rsrq: float | None = None
    rscp: float | None = None
    ecno: float | None = None
    rssi: float | None = None
    ss_rsrp: float | None = None


@dataclass
class ParsedSample:
    # Time + position
    timestamp: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    speed: float | None = None
    heading: float | None = None
    hdop: float | None = None

    # Identity
    technology: str = ''
    mcc: str = ''
    mnc: str = ''
    plmn: str = ''
    operator_code: str = ''

    # Serving cell (observed)
    obs_cell_id: str = ''
    obs_pci: int | None = None
    obs_psc: int | None = None
    obs_bsic: int | None = None
    obs_arfcn: int | None = None
    frequency: float | None = None
    band: str = ''
    channel: int | None = None

    # RF metrics (all optional)
    rsrp: float | None = None
    rsrq: float | None = None
    sinr: float | None = None
    rssi: float | None = None
    cqi: float | None = None
    ss_rsrp: float | None = None
    ss_rsrq: float | None = None
    ss_sinr: float | None = None
    rscp: float | None = None
    ecno: float | None = None
    rxlev: float | None = None
    rxqual: float | None = None

    # Data / transport
    dl_throughput: float | None = None
    ul_throughput: float | None = None
    latency_ms: float | None = None
    packet_loss: float | None = None

    event_type: str = ''
    event_status: str = ''

    neighbours: list[ParsedNeighbour] = field(default_factory=list)


@dataclass(frozen=True)
class ParserCapabilities:
    """What a *format* can carry, distinct from what one file happened to hold.

    ``metrics`` names the canonical metric keys the format is able to produce;
    an empty set means "makes no claim". The UI uses this to say "this format
    cannot carry X" instead of showing an empty panel that looks like a fault.
    """
    metrics: frozenset[str] = frozenset()
    neighbours: bool = False
    events: bool = False
    voice: bool = False
    data: bool = False

    def supports(self, metric: str) -> bool:
        return not self.metrics or metric in self.metrics


# ---------------------------------------------------------------------------
# Parser base class
# ---------------------------------------------------------------------------

class DriveTestParser:
    """Abstract base. Concrete parsers set ``name``/``extensions`` and
    implement :meth:`sniff` and :meth:`parse`.
    """
    name: str = 'base'
    extensions: tuple[str, ...] = ()
    capabilities: ParserCapabilities = ParserCapabilities()

    @classmethod
    def sniff(cls, path: str) -> float:
        """Return confidence in [0.0, 1.0] that this parser handles ``path``."""
        return 0.0

    def parse(self, path: str) -> Iterator[ParsedSample]:
        raise NotImplementedError

    def profile(self, path: str, limit: int = 5000) -> dict:
        """Cheap pre-ingest summary from up to ``limit`` samples.

        Returns detected technology/operator, GPS availability, an approximate
        sample count, bounding box and time span — without persisting anything.
        """
        count = 0
        gps = 0
        techs: set[str] = set()
        mccmnc: set[str] = set()
        min_lat = min_lon = None
        max_lat = max_lon = None
        first_ts = last_ts = None

        for s in self.parse(path):
            count += 1
            if s.technology:
                techs.add(s.technology)
            if s.mcc and s.mnc:
                mccmnc.add(f'{s.mcc}-{s.mnc}')
            if s.latitude is not None and s.longitude is not None:
                gps += 1
                min_lat = s.latitude if min_lat is None else min(min_lat, s.latitude)
                max_lat = s.latitude if max_lat is None else max(max_lat, s.latitude)
                min_lon = s.longitude if min_lon is None else min(min_lon, s.longitude)
                max_lon = s.longitude if max_lon is None else max(max_lon, s.longitude)
            if s.timestamp is not None:
                first_ts = s.timestamp if first_ts is None else min(first_ts, s.timestamp)
                last_ts = s.timestamp if last_ts is None else max(last_ts, s.timestamp)
            if count >= limit:
                break

        return {
            'format': self.name,
            'sample_count': count,
            'truncated': count >= limit,
            'gps_available': (gps > 0) if count else None,
            'gps_fix_ratio': round(gps / count, 3) if count else None,
            'technologies': sorted(techs),
            'plmns': sorted(mccmnc),
            'bbox': (
                [min_lat, min_lon, max_lat, max_lon]
                if min_lat is not None else None
            ),
            'time_span': [
                first_ts.isoformat() if first_ts else None,
                last_ts.isoformat() if last_ts else None,
            ],
        }


# ---------------------------------------------------------------------------
# Shared value coercion helpers (used by concrete parsers)
# ---------------------------------------------------------------------------

_TS_FORMATS = (
    '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S.%f',
    '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%d/%m/%Y %H:%M:%S',
    '%m/%d/%Y %H:%M:%S', '%H:%M:%S.%f', '%H:%M:%S',
)


def to_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s == '' or s.lower() in ('na', 'n/a', 'null', 'none', '-', '--'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(v) -> int | None:
    f = to_float(v)
    return int(f) if f is not None else None


def to_str(v) -> str:
    if v is None:
        return ''
    return str(v).strip()


def to_timestamp(v) -> datetime | None:
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)  # USE_TZ is False → naive
    s = str(v).strip()
    if s == '':
        return None
    # Epoch seconds / millis
    if re.fullmatch(r'\d{10}(\.\d+)?', s):
        return datetime.utcfromtimestamp(float(s))
    if re.fullmatch(r'\d{13}', s):
        return datetime.utcfromtimestamp(int(s) / 1000.0)
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # ISO 8601 with timezone → drop tz to stay naive
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None
