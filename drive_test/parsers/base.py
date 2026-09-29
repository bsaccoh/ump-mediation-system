"""
Abstract base class for drive-test file parsers.

Each parser reads a file and yields ParsedMeasurement objects.
Parsers must never write to the database — that is the task's responsibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Generator


@dataclass
class ParsedNeighbour:
    """One detected neighbour cell, ranked by received level (0 = strongest)."""

    rank: int
    rat: str = ''
    obs_pci: int | None = None          # 4G/5G
    obs_psc: int | None = None          # 3G
    obs_bsic: int | None = None         # 2G
    obs_arfcn: int | None = None
    rssi: float | None = None
    rscp: float | None = None
    ecio: float | None = None
    rsrp: float | None = None
    rsrq: float | None = None
    ss_rsrp: float | None = None
    raw_data: dict = field(default_factory=dict)


@dataclass
class ParsedCarrier:
    """One component carrier under carrier aggregation (index 0 = PCell)."""

    cc_index: int
    is_primary: bool = False
    arfcn: int | None = None
    band: str = ''
    bandwidth_mhz: float | None = None
    rsrp: float | None = None
    rsrq: float | None = None
    sinr: float | None = None
    mimo_layers: int | None = None
    modulation: str = ''
    dl_throughput_kbps: float | None = None
    ul_throughput_kbps: float | None = None
    raw_data: dict = field(default_factory=dict)


@dataclass
class ParsedBeam:
    """One 5G NR SSB beam."""

    ssb_index: int
    is_serving: bool = False
    ss_rsrp: float | None = None
    ss_rsrq: float | None = None
    ss_sinr: float | None = None
    raw_data: dict = field(default_factory=dict)


@dataclass
class ParsedEvent:
    """
    A timestamped event.

    Yielded either inside a ParsedMeasurement (when the source ties events to
    samples, as TRP does) or from ``DriveTestParser.parse_events`` (when the
    source is message-oriented, as .nmf and .qmdl are and where events arrive
    decoupled from sample cadence).
    """

    event_type: str                     # MeasurementEvent.EventType value
    occurred_at: datetime
    severity: str = 'INFO'
    latitude: float | None = None
    longitude: float | None = None
    technology: str = ''
    duration_ms: int | None = None
    description: str = ''
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ParserCapabilities:
    """
    What a format can carry, as opposed to what one file happened to contain.

    This is the difference between two facts the UI must never conflate:

        "this session recorded no RSRP"      — the drive produced none
        "this format cannot carry RSRP"      — it never could

    Showing an empty RSRP chart for a GSM-only log looks like a fault. Saying
    the source does not carry it is the truth, and it is the same
    absent-is-not-zero discipline applied one level up, to the format itself.

    `metrics` names the metric keys (as used by services.timeseries) that the
    format is able to produce. An empty set means "unknown", which is treated as
    no claim rather than as a claim of nothing.
    """

    metrics: frozenset[str] = frozenset()
    neighbours: bool = False
    events: bool = False
    carriers: bool = False
    beams: bool = False
    layer3: bool = False
    voice_quality: bool = False
    throughput: bool = False

    def supports(self, metric: str) -> bool:
        """True when the format can carry `metric`, or makes no claim either way."""
        return not self.metrics or metric in self.metrics


@dataclass
class ParsedMeasurement:
    """
    One point in time from a drive-test file.

    All numeric fields are optional; parsers should leave fields as None
    rather than guess or fill with zeros.  The task layer decides what to
    validate and flag.
    """

    # Required
    sequence_num: int
    captured_at: datetime       # UTC preferred; local allowed when UTC unknown
    latitude: float
    longitude: float

    # GPS quality
    altitude_m: float | None = None
    gps_accuracy_m: float | None = None
    gps_hdop: float | None = None
    speed_kmh: float | None = None
    heading_deg: float | None = None
    local_timestamp: datetime | None = None

    # Observed cell identifiers (A-tier: never overwritten later)
    obs_mcc: str = ''
    obs_mnc: str = ''
    obs_lac: int | None = None
    obs_ci: int | None = None
    obs_tac: int | None = None
    obs_eci: int | None = None
    obs_pci: int | None = None
    obs_earfcn: int | None = None
    obs_nrarfcn: int | None = None

    # Radio (all optional)
    technology: str = ''
    rssi: float | None = None
    rscp: float | None = None
    ecio: float | None = None
    rsrp: float | None = None
    rsrq: float | None = None
    sinr: float | None = None
    cqi: int | None = None
    ss_rsrp: float | None = None
    ss_rsrq: float | None = None
    ss_sinr: float | None = None
    dl_throughput_kbps: float | None = None
    ul_throughput_kbps: float | None = None

    # Service
    service_type: str = ''        # VOICE / SMS / DATA
    service_outcome: str = ''     # SUCCESS / FAILED / DROPPED / BLOCKED
    call_setup_time_ms: int | None = None
    call_duration_s: int | None = None
    mos: float | None = None
    throughput_kbps: float | None = None
    latency_ms: int | None = None
    packet_loss_pct: float | None = None

    # 2G quality
    rxqual: int | None = None
    c_over_i: float | None = None

    # Collections. Default-empty, so existing parsers that never set them are
    # unaffected and the persistence layer's bulk inserts become no-ops.
    neighbours: list['ParsedNeighbour'] = field(default_factory=list)
    carriers: list['ParsedCarrier'] = field(default_factory=list)
    beams: list['ParsedBeam'] = field(default_factory=list)
    events: list['ParsedEvent'] = field(default_factory=list)

    # Extra fields from the raw row (parser-specific overflow)
    raw_data: dict = field(default_factory=dict)

    # Validation
    is_valid: bool = True
    quality_flags: list[str] = field(default_factory=list)


class DriveTestParser:
    """
    Base class for all drive-test file parsers.

    Subclasses implement ``parse()`` which yields ParsedMeasurement objects.
    """

    name: str = 'base'
    file_extensions: list[str] = []
    magic_bytes: str = ''

    #: What this format can carry. Declared per subclass so the UI can say
    #: "this format has no neighbour data" rather than showing an empty panel.
    capabilities: ParserCapabilities = ParserCapabilities()

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def parse(self, file_path: Path) -> Generator[ParsedMeasurement, None, None]:
        """
        Yield ParsedMeasurement objects from file_path.
        Must never raise for individual bad rows — mark them invalid and continue.
        """
        raise NotImplementedError

    def parse_events(self, file_path: Path) -> Generator[ParsedEvent, None, None]:
        """
        Yield events that are not tied to a sample.

        Sample-oriented formats (CSV, TRP) attach events to the measurement they
        occurred at and leave this empty. Message-oriented formats (.nmf, .qmdl)
        carry Layer-3 signalling on its own clock, decoupled from sample cadence;
        without this hook they would have to invent a carrier measurement for
        every system information block.
        """
        return iter(())

    @classmethod
    def can_parse(cls, file_path: Path) -> bool:
        """Quick detection check — extension + optional magic bytes."""
        return cls.sniff(file_path) > 0.0

    @classmethod
    def sniff(cls, file_path: Path) -> float:
        """Confidence from 0.0 (cannot parse) to 1.0 (certain), for the registry.

        Resolution picks the HIGHEST confidence above a threshold rather than
        the first extension match, so a format whose extension collides with
        another (.txt, .log and .csv all overlap in this domain) is decided on
        content rather than on whichever profile happened to be created first.

        The default scores extension and magic bytes. Subclasses override to
        inspect structure — a container format can confirm its own members.

        Never raises: an unreadable file is simply not a match.
        """
        if cls.file_extensions:
            if file_path.suffix.lower() not in [e.lower() for e in cls.file_extensions]:
                return 0.0

        if not cls.magic_bytes:
            # Extension alone is weak evidence; content checks should beat it.
            return 0.5

        try:
            with open(file_path, 'rb') as fh:
                header = fh.read(max(4, len(cls.magic_bytes) // 2)).hex()
        except OSError:
            return 0.0

        return 0.9 if header.startswith(cls.magic_bytes.lower()) else 0.0
