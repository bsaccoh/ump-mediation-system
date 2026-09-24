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

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def parse(self, file_path: Path) -> Generator[ParsedMeasurement, None, None]:
        """
        Yield ParsedMeasurement objects from file_path.
        Must never raise for individual bad rows — mark them invalid and continue.
        """
        raise NotImplementedError

    @classmethod
    def can_parse(cls, file_path: Path) -> bool:
        """Quick detection check — extension + optional magic bytes."""
        if cls.file_extensions:
            if file_path.suffix.lower() not in [e.lower() for e in cls.file_extensions]:
                return False
        if cls.magic_bytes:
            try:
                with open(file_path, 'rb') as fh:
                    header = fh.read(len(cls.magic_bytes) // 2).hex()
                return header.startswith(cls.magic_bytes.lower())
            except OSError:
                return False
        return True
