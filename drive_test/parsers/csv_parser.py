"""
Generic CSV Drive-Test Parser

Handles CSV exports from most major drive-test tools (NEMO, TEMS, Spirent, etc.)
and custom operator CSV formats.

Column name matching is case-insensitive and handles common aliases
(e.g. 'lat', 'latitude', 'Latitude', 'LAT', 'gps_lat', 'GPS_Latitude').
Unknown columns are preserved in raw_data.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone as dt_tz
from pathlib import Path
from typing import Generator

from .base import DriveTestParser, ParsedMeasurement, ParserCapabilities

logger = logging.getLogger(__name__)

# Column alias maps: canonical_name -> [possible CSV header names] (lowercase)
_ALIASES: dict[str, list[str]] = {
    'timestamp': [
        'timestamp', 'time', 'datetime', 'date_time', 'date time',
        'gps_time', 'gps time', 'utc_time', 'utc time', 'capture_time',
    ],
    'latitude': ['latitude', 'lat', 'gps_lat', 'gps_latitude', 'y', 'lat_deg'],
    'longitude': ['longitude', 'lon', 'lng', 'long', 'gps_lon', 'gps_longitude', 'x', 'lon_deg'],
    'altitude_m': ['altitude', 'altitude_m', 'alt', 'alt_m', 'height', 'elevation'],
    'gps_accuracy_m': ['gps_accuracy', 'accuracy', 'hdop_m', 'position_accuracy'],
    'gps_hdop': ['hdop', 'gps_hdop', 'horizontal_dop'],
    'speed_kmh': ['speed', 'speed_kmh', 'speed_km_h', 'velocity', 'velocity_kmh'],
    'heading_deg': ['heading', 'heading_deg', 'bearing', 'course', 'direction'],
    'technology': ['technology', 'tech', 'rat', 'network_type', 'access_tech', 'generation'],
    'mcc': ['mcc', 'mobile_country_code'],
    'mnc': ['mnc', 'mobile_network_code'],
    'lac': ['lac', 'location_area_code', 'location_area'],
    'ci': ['ci', 'cell_id', 'cell_identity', 'cellid'],
    'tac': ['tac', 'tracking_area_code', 'ta'],
    'eci': ['eci', 'e_utran_cell_id', 'eutran_cell_id', 'ecid'],
    'pci': ['pci', 'physical_cell_id', 'physical_cell_identity', 'pcid'],
    'earfcn': ['earfcn', 'ear_fcn', 'dl_earfcn', 'downlink_earfcn'],
    'nrarfcn': ['nrarfcn', 'nr_arfcn', 'ssb_arfcn'],
    'rssi': ['rssi', 'rxlev', 'rx_level', 'signal_strength'],
    'rscp': ['rscp', 'rx_signal_code_power'],
    'ecio': ['ecio', 'ec_io', 'ec_n0'],
    'rsrp': ['rsrp', 'reference_signal_received_power', 'rs_rp'],
    'rsrq': ['rsrq', 'reference_signal_received_quality', 'rs_rq'],
    'sinr': ['sinr', 'snr', 'signal_to_noise_ratio', 'rs_sinr'],
    'cqi': ['cqi', 'channel_quality_indicator', 'channel_quality_index'],
    'dl_throughput_kbps': ['dl_throughput', 'dl_throughput_kbps', 'downlink_throughput', 'download_speed'],
    'ul_throughput_kbps': ['ul_throughput', 'ul_throughput_kbps', 'uplink_throughput', 'upload_speed'],
    'service_type': ['service_type', 'service', 'test_type', 'call_type'],
    'service_outcome': ['service_outcome', 'outcome', 'call_status', 'result', 'status'],
    'call_setup_time_ms': ['call_setup_time', 'call_setup_time_ms', 'cst_ms', 'setup_time'],
    'call_duration_s': ['call_duration', 'call_duration_s', 'duration_s', 'duration'],
    'mos': ['mos', 'mean_opinion_score', 'voice_quality', 'mos_lqo'],
    'throughput_kbps': ['throughput', 'throughput_kbps', 'data_throughput'],
    'latency_ms': ['latency', 'latency_ms', 'rtt', 'ping_ms', 'round_trip_time'],
    'packet_loss_pct': ['packet_loss', 'packet_loss_pct', 'loss_pct', 'pl'],
}

# Build reverse lookup: lowercase alias -> canonical
_REVERSE: dict[str, str] = {}
for _canon, _aliases in _ALIASES.items():
    for _alias in _aliases:
        _REVERSE[_alias] = _canon


def _build_col_map(headers: list[str]) -> dict[str, str]:
    """Map CSV header names to canonical field names. Unrecognised headers pass through."""
    col_map = {}
    for h in headers:
        canonical = _REVERSE.get(h.strip().lower().replace(' ', '_'))
        col_map[h] = canonical or h  # preserve unknown headers as-is
    return col_map


def _parse_ts(value: str) -> datetime | None:
    """Try to parse a timestamp string; return None on failure."""
    if not value:
        return None
    formats = [
        '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f',
        '%d/%m/%Y %H:%M:%S', '%m/%d/%Y %H:%M:%S',
        '%Y%m%d%H%M%S',
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(value.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_tz.utc)
            return dt
        except ValueError:
            continue
    return None


def _f(val: str) -> float | None:
    try:
        return float(val) if val.strip() else None
    except (ValueError, AttributeError):
        return None


def _i(val: str) -> int | None:
    try:
        return int(float(val)) if val.strip() else None
    except (ValueError, AttributeError):
        return None


class CsvDriveTestParser(DriveTestParser):
    """
    Generic CSV parser for drive-test data exports.
    Tolerates common header naming variations via _ALIASES lookup.
    Unknown columns are stored in raw_data for fidelity.
    """

    name = 'CSV Generic'
    file_extensions = ['.csv', '.txt']

    #: Deliberately makes NO metric claim. This parser maps whatever columns a
    #: file happens to have via _ALIASES, so what it can carry is a property of
    #: the file, not of the format. An empty `metrics` set means "no claim",
    #: which the UI reads as "cannot say" rather than "carries nothing" — the
    #: honest answer for a generic tabular importer.
    capabilities = ParserCapabilities(
        metrics=frozenset(),
        events=False,
        voice_quality=True,   # 'mos' is an understood alias
        throughput=True,      # 'dl_throughput' / 'ul_throughput' are understood
    )

    @classmethod
    def sniff(cls, file_path: Path) -> float:
        """Score on header content, not just extension.

        .csv and .txt collide with several formats in this domain, so a generic
        importer must not win on extension alone. Recognising known column
        aliases in the header is what distinguishes a drive-test export from
        any other delimited file, and keeps this below a container format that
        can confirm its own structure.
        """
        if file_path.suffix.lower() not in cls.file_extensions:
            return 0.0

        try:
            with open(file_path, 'rb') as fh:
                head = fh.read(8192).decode('utf-8-sig', errors='replace')
        except OSError:
            return 0.0

        line = head.splitlines()[0] if head.splitlines() else ''
        if not line:
            return 0.0

        known = {alias for aliases in _ALIASES.values() for alias in aliases}
        for delimiter in (',', ';', '\t'):
            cells = [c.strip().strip('"').lower() for c in line.split(delimiter)]
            if len(cells) < 2:
                continue
            matched = sum(1 for c in cells if c in known)
            if matched >= 2:
                # Scale with how much of the header we recognise, capped below
                # a structural match so a real container format always wins.
                return min(0.75, 0.35 + 0.05 * matched)

        return 0.0

    def parse(self, file_path: Path) -> Generator[ParsedMeasurement, None, None]:
        encoding = self.config.get('encoding', 'utf-8-sig')
        delimiter = self.config.get('delimiter', ',')

        try:
            content = file_path.read_bytes().decode(encoding, errors='replace')
        except OSError as exc:
            logger.error('CSV parser: cannot read %s: %s', file_path, exc)
            return

        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        if reader.fieldnames is None:
            logger.warning('CSV parser: empty or header-less file %s', file_path)
            return

        col_map = _build_col_map(list(reader.fieldnames))
        known = set(_ALIASES.keys())

        for seq, raw_row in enumerate(reader, start=1):
            row: dict[str, str] = {col_map[k]: v for k, v in raw_row.items() if k}

            # Timestamp
            ts_raw = row.get('timestamp', '')
            captured_at = _parse_ts(ts_raw)
            if captured_at is None:
                captured_at = datetime.now(dt_tz.utc)
                flags = ['NO_TIMESTAMP']
            else:
                flags = []

            # Coordinates — mandatory
            lat = _f(row.get('latitude', ''))
            lon = _f(row.get('longitude', ''))
            if lat is None or lon is None:
                flags.append('NO_GPS')
                is_valid = False
            else:
                is_valid = True

            # Service outcome normalisation
            raw_outcome = (row.get('service_outcome') or '').strip().upper()
            outcome_map = {
                'OK': 'SUCCESS', 'PASS': 'SUCCESS', 'SUCCESS': 'SUCCESS',
                'FAIL': 'FAILED', 'FAILED': 'FAILED', 'NO': 'FAILED',
                'DROP': 'DROPPED', 'DROPPED': 'DROPPED',
                'BLOCK': 'BLOCKED', 'BLOCKED': 'BLOCKED',
            }
            service_outcome = outcome_map.get(raw_outcome, raw_outcome or 'SUCCESS')

            # Overflow: unrecognised columns
            extra = {k: v for k, v in row.items() if k not in known and v}

            yield ParsedMeasurement(
                sequence_num=seq,
                captured_at=captured_at,
                latitude=lat or 0.0,
                longitude=lon or 0.0,
                altitude_m=_f(row.get('altitude_m', '')),
                gps_accuracy_m=_f(row.get('gps_accuracy_m', '')),
                gps_hdop=_f(row.get('gps_hdop', '')),
                speed_kmh=_f(row.get('speed_kmh', '')),
                heading_deg=_f(row.get('heading_deg', '')),
                obs_mcc=row.get('mcc', ''),
                obs_mnc=row.get('mnc', ''),
                obs_lac=_i(row.get('lac', '')),
                obs_ci=_i(row.get('ci', '')),
                obs_tac=_i(row.get('tac', '')),
                obs_eci=_i(row.get('eci', '')),
                obs_pci=_i(row.get('pci', '')),
                obs_earfcn=_i(row.get('earfcn', '')),
                obs_nrarfcn=_i(row.get('nrarfcn', '')),
                technology=row.get('technology', '').strip(),
                rssi=_f(row.get('rssi', '')),
                rscp=_f(row.get('rscp', '')),
                ecio=_f(row.get('ecio', '')),
                rsrp=_f(row.get('rsrp', '')),
                rsrq=_f(row.get('rsrq', '')),
                sinr=_f(row.get('sinr', '')),
                cqi=_i(row.get('cqi', '')),
                dl_throughput_kbps=_f(row.get('dl_throughput_kbps', '')),
                ul_throughput_kbps=_f(row.get('ul_throughput_kbps', '')),
                service_type=(row.get('service_type') or '').strip().upper(),
                service_outcome=service_outcome,
                call_setup_time_ms=_i(row.get('call_setup_time_ms', '')),
                call_duration_s=_i(row.get('call_duration_s', '')),
                mos=_f(row.get('mos', '')),
                throughput_kbps=_f(row.get('throughput_kbps', '')),
                latency_ms=_i(row.get('latency_ms', '')),
                packet_loss_pct=_f(row.get('packet_loss_pct', '')),
                raw_data=extra,
                is_valid=is_valid,
                quality_flags=flags,
            )
