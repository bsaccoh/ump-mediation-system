"""
TEMS Pocket TRP Parser for the UMP Drive Test module.

TRP = TEMS Recording Package, a ZIP/OPC archive produced by InfoVista
TEMS Pocket.  The vendor measured (Huawei network, InfoVista tool).

Internal layout used here:
  trp/content.xml                             — session metadata + Tags
  trp/positions/wptrack.xml                   — GPX 1.1 GPS track
  trp/providers/sp1/cdf/declarations.cdf      — parameter ID -> name map
  trp/providers/sp1/cdf/data.cdf              — session-level param values
  trp/providers/sp1/services/services.xml     — call / MOS service events
  trp/providers/sp1/channels/ch1/channel.log  — per-second radio measurements

All three CDF files share the same encoding: first 8 bytes are a fixed header
(all zeros in observed files), the rest is zlib-compressed protobuf data.

Channel .log files share the same 8-byte-header + zlib layout; records inside
begin with an 8-byte little-endian .NET DateTime (100 ns ticks since 0001-01-01).
"""
from __future__ import annotations

import io
import logging
import struct
import zipfile
import zlib
import xml.etree.ElementTree as ET
from bisect import bisect_left
from datetime import datetime, timedelta, timezone as dt_tz
from pathlib import Path
from typing import Generator

from .base import DriveTestParser, ParsedMeasurement

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# .NET DateTime epoch offset in 100 ns ticks (0001-01-01 -> 1970-01-01)
_NET_EPOCH_TICKS = 621_355_968_000_000_000

# Valid .NET tick window: all of 2020-2035
_MIN_VALID_TICKS = 637_200_000_000_000_000   # ≈ 2020-01-01
_MAX_VALID_TICKS = 643_800_000_000_000_000   # ≈ 2036-01-01

# GPX 1.1 namespace
_GPX_NS = {'gpx': 'http://www.topografix.com/GPX/1/1'}

# Technology tag fragments -> canonical RAT string
_TECH_TAGS: dict[str, str] = {
    '5G': '5G', 'NR': '5G',
    '4G': '4G', 'LTE': '4G',
    '3G': '3G', 'WCDMA': '3G', 'UMTS': '3G',
    '2G': '2G', 'GSM': '2G', 'EDGE': '2G', 'GPRS': '2G',
}

# Radio.Common.Technology enum (id 4829)
_TECH_ENUM: dict[int, str] = {1: '2G', 2: '3G', 4: '4G', 8: '5G', 16: '5G'}

# Known parameter IDs of interest
_PID_RSSI_FULL = 100764    # Radio.Gsm.ServingCell.RssiFull (dBm float)
_PID_RSSI_SUB  = 100755    # Radio.Gsm.ServingCell.RssiSub
_PID_RXQUAL    = 100749    # Radio.Gsm.ServingCell.RxQualFull (0-7)
_PID_ARFCN     = 100752    # Radio.Gsm.ServingCell.Bcch.Arfcn
_PID_CUR_ARFCN = 100905    # Radio.Gsm.CurrentArfcn
_PID_LAC       = 100756    # Radio.Gsm.ServingCell.Lac
_PID_CI        = 100765    # Radio.Gsm.ServingCell.CellIdentity
_PID_BSIC      = 100751    # Radio.Gsm.ServingCell.Bsic
_PID_TA        = 100761    # Radio.Gsm.ServingCell.TimingAdvance
_PID_TXPOWER   = 100757    # Radio.Gsm.ServingCell.TxPower
_PID_SPEED     = 3111041   # Pocket.Location.SpeedInMetersPerSecond (m/s)
_PID_RSSI_CMN  = 3110665   # Pocket.Radio.Common.Rssi
_PID_TECH      = 4829      # Radio.Common.Technology (int enum)
_PID_MNO       = 3357      # Radio.Common.SimOperator (string "mcc-mnc")

# Map param_id -> (field_name_or_None, type_hint)
# type_hint: 'float' uses ieee754 little-endian; 'int' uses signed varint / LE int
_PARAM_FIELD: dict[int, tuple[str | None, str]] = {
    _PID_RSSI_FULL: ('rssi',      'float'),
    _PID_RSSI_SUB:  ('rssi',      'float'),
    _PID_RXQUAL:    ('rxqual',    'int'),     # GSM RxQual, 0-7, LOWER is better
    _PID_ARFCN:     ('obs_earfcn','int'),
    _PID_CUR_ARFCN: ('obs_earfcn','int'),
    _PID_LAC:       ('obs_lac',   'int'),
    _PID_CI:        ('obs_ci',    'int'),
    _PID_BSIC:      (None,        'int'),
    _PID_TA:        (None,        'int'),
    _PID_TXPOWER:   (None,        'int'),
    _PID_SPEED:     ('speed_kmh', 'float'),   # convert m/s -> km/h after
    _PID_RSSI_CMN:  ('rssi',      'float'),
    _PID_TECH:      ('technology','int'),
    _PID_MNO:       (None,        'str'),
}

_RAW_PARAM_KEY: dict[int, str] = {
    _PID_BSIC:    'gsm_bsic',
    _PID_TA:      'gsm_ta',
    _PID_TXPOWER: 'gsm_txpower',
    _PID_MNO:     'sim_operator',
}


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _cdf_decompress(raw: bytes) -> bytes:
    """Skip 8-byte CDF header then zlib-decompress the rest."""
    return zlib.decompress(raw[8:])


def _net_ticks_to_dt(ticks: int) -> datetime:
    """Convert .NET DateTime ticks to timezone-aware UTC datetime."""
    us = (ticks - _NET_EPOCH_TICKS) // 10
    return datetime(1970, 1, 1, tzinfo=dt_tz.utc) + timedelta(microseconds=us)


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a protobuf-style unsigned varint. Returns (value, new_pos)."""
    result = shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift >= 64:
            raise ValueError('varint overflow')
    raise ValueError('truncated varint')


def _varint_bytes(n: int) -> bytes:
    """Encode n as a protobuf varint (unsigned)."""
    if n < 0:
        n = n & 0xFFFFFFFFFFFFFFFF  # treat as uint64
    buf = []
    while True:
        bits = n & 0x7F
        n >>= 7
        if n:
            buf.append(bits | 0x80)
        else:
            buf.append(bits)
            break
    return bytes(buf)


def _parse_iso_dt(s: str, date_hint: datetime | None = None) -> datetime | None:
    """
    Parse an ISO 8601 timestamp string.  Handles:
      - Full:  "2026-01-17T23:57:28.113Z"
      - Time-only with Z: "23:57:36.835Z"  (uses date_hint.date())
    """
    if not s:
        return None
    s = s.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_tz.utc)
        return dt
    except ValueError:
        pass
    # Time-only fallback
    if date_hint and len(s) <= 16 and 'T' not in s and '-' not in s:
        try:
            date_str = date_hint.strftime('%Y-%m-%d') + 'T' + s
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_tz.utc)
            return dt
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Parse systeminformation.xml + serviceprovider.xml — device info
# ---------------------------------------------------------------------------

def read_sim_imsi(file_path) -> str:
    """IMSI of the test SIM from the TRP's service-provider record, or '' when absent.

    Used only to identify the operator (IMSI prefix = home PLMN). Callers must not persist or expose it.
    """
    import re
    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            for name in zf.namelist():
                if name.startswith('trp/providers/') and name.endswith('/serviceprovider.xml'):
                    text = zf.read(name).decode('utf-8', 'replace')
                    m = re.search(r'<Name>IMSI</Name>\s*<Value[^>]*>\s*(\d{10,16})\s*</Value>', text)
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return ''


def _parse_device_info(zf: zipfile.ZipFile) -> dict:
    """
    Extract device information from:
      trp/systeminformation.xml  → Manufacturer + Model
      trp/providers/sp1/serviceprovider.xml → IMEI, Label, IMSI, MSISDN
    Returns dict with keys: manufacturer, model_name, imei, label, imsi, sim_msisdn.
    All values are strings; missing fields are empty strings.
    """
    info: dict[str, str] = {
        'manufacturer': '', 'model_name': '',
        'imei': '', 'label': '', 'imsi': '', 'sim_msisdn': '',
    }

    def _tag(el: ET.Element) -> str:
        return el.tag.split('}', 1)[-1] if '}' in el.tag else el.tag

    # 1. systeminformation.xml
    try:
        raw = zf.read('trp/systeminformation.xml')
        root = ET.fromstring(raw.decode('utf-8', errors='replace'))
        for el in root.iter():
            if _tag(el) == 'Computer':
                for child in el:
                    t = _tag(child)
                    if t == 'Manufacturer' and child.text:
                        info['manufacturer'] = child.text.strip()
                    elif t == 'Model' and child.text:
                        info['model_name'] = child.text.strip()
                break
    except Exception as exc:
        logger.debug('TRP: cannot parse systeminformation.xml: %s', exc)

    # 2. serviceprovider.xml (try sp1 … sp4)
    for sp_idx in range(1, 5):
        sp_path = f'trp/providers/sp{sp_idx}/serviceprovider.xml'
        if sp_path not in zf.namelist():
            continue
        try:
            raw = zf.read(sp_path)
            root = ET.fromstring(raw.decode('utf-8', errors='replace'))
            for prop in root.iter():
                if _tag(prop) != 'Property':
                    continue
                name_el = next((c for c in prop if _tag(c) == 'Name'), None)
                val_el  = next((c for c in prop if _tag(c) == 'Value'), None)
                if name_el is None or val_el is None:
                    continue
                pname = (name_el.text or '').strip()
                pval  = (val_el.text or '').strip()
                if pname == 'IMEI'   and pval and not info['imei']:
                    info['imei'] = pval
                elif pname == 'Label'  and pval and not info['label']:
                    info['label'] = pval
                elif pname == 'IMSI'   and pval and not info['imsi']:
                    info['imsi'] = pval
                elif pname == 'MSISDN' and pval and not info['sim_msisdn']:
                    info['sim_msisdn'] = pval
            break
        except Exception as exc:
            logger.debug('TRP: cannot parse %s: %s', sp_path, exc)

    if info['model_name'] or info['imei']:
        logger.debug(
            'TRP: device: manufacturer=%s model=%s imei=%s label=%s',
            info['manufacturer'], info['model_name'], info['imei'], info['label'],
        )
    return info


# ---------------------------------------------------------------------------
# Parse content.xml — session metadata
# ---------------------------------------------------------------------------

def _parse_content_xml(zf: zipfile.ZipFile) -> dict:
    """
    Extract: session_start, session_stop, technology, tags list.
    Returns a dict with keys: start_dt, stop_dt, technology, tags.
    """
    result = {'start_dt': None, 'stop_dt': None, 'technology': '', 'tags': []}
    try:
        raw = zf.read('trp/content.xml')
        root = ET.fromstring(raw.decode('utf-8', errors='replace'))
    except Exception as exc:
        logger.warning('TRP: cannot parse content.xml: %s', exc)
        return result

    ns = ''
    # Strip namespace from tag (it's usually xmlns="...")
    def _tag(el):
        return el.tag.split('}', 1)[-1] if '}' in el.tag else el.tag

    def _find_text(parent, *path):
        cur = parent
        for segment in path:
            found = None
            for child in cur:
                if _tag(child) == segment:
                    found = child
                    break
            if found is None:
                return None
            cur = found
        return cur.text

    tags_text = _find_text(root, 'Tags') or ''
    result['tags'] = [t.strip() for t in tags_text.split(';') if t.strip()]

    # Technology from Tags
    for tag in result['tags']:
        tag_up = tag.upper()
        for key, rat in _TECH_TAGS.items():
            if key in tag_up:
                result['technology'] = rat
                break
        if result['technology']:
            break

    # Session time window
    try:
        start_el = root.find('.//{*}StartTime/{*}Time')
        if start_el is not None and start_el.text:
            result['start_dt'] = _parse_iso_dt(start_el.text)
        stop_el = root.find('.//{*}StopTime/{*}Time')
        if stop_el is not None and stop_el.text:
            result['stop_dt'] = _parse_iso_dt(stop_el.text, result['start_dt'])
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Parse wptrack.xml — GPS track
# ---------------------------------------------------------------------------

def _parse_gps_track(zf: zipfile.ZipFile) -> list[tuple[datetime, float, float, float]]:
    """
    Parse trp/positions/wptrack.xml (GPX 1.1).
    Returns sorted list of (timestamp_utc, lat, lon, alt_m).
    """
    fixes: list[tuple[datetime, float, float, float]] = []
    try:
        raw = zf.read('trp/positions/wptrack.xml')
        root = ET.fromstring(raw.decode('utf-8', errors='replace'))
    except Exception as exc:
        logger.warning('TRP: cannot parse wptrack.xml: %s', exc)
        return fixes

    # Try with namespace first, then without
    for ns_key in (_GPX_NS, {'gpx': ''}):
        trkpts = root.findall('.//gpx:trkpt', ns_key)
        if not trkpts:
            # Try without namespace prefix
            trkpts = root.findall('.//{http://www.topografix.com/GPX/1/1}trkpt')
        if not trkpts:
            trkpts = root.findall('.//trkpt')
        if trkpts:
            break

    for pt in trkpts:
        try:
            lat = float(pt.get('lat', '0'))
            lon = float(pt.get('lon', '0'))
        except ValueError:
            continue

        ts = None
        for time_tag in (
            '{http://www.topografix.com/GPX/1/1}time',
            'time',
        ):
            el = pt.find(time_tag)
            if el is not None and el.text:
                ts = _parse_iso_dt(el.text)
                break

        if ts is None:
            continue

        alt = 0.0
        for alt_tag in (
            '{http://www.topografix.com/GPX/1/1}geoidheight',
            '{http://www.topografix.com/GPX/1/1}ele',
            'geoidheight', 'ele',
        ):
            el = pt.find(alt_tag)
            if el is not None and el.text:
                try:
                    alt = float(el.text)
                    break
                except ValueError:
                    pass

        fixes.append((ts, lat, lon, alt))

    fixes.sort(key=lambda x: x[0])
    return fixes


# ---------------------------------------------------------------------------
# Parse declarations.cdf — parameter ID -> name map
# ---------------------------------------------------------------------------

def _parse_declarations(zf: zipfile.ZipFile) -> dict[int, str]:
    """
    Decompress declarations.cdf and extract the parameter ID -> name map.

    The file contains a sequence of protobuf records, each encoding:
      field 1 (0x0A, LEN):  parameter name string
      field 2 (0x10, VARINT): integer parameter ID

    Approach: scan forward for every 0x10 byte (field 2 varint tag).
    For each candidate, read the varint param_id and then look *backward*
    for the preceding 0x0A [length] [name] pattern whose string ends
    at the position of the 0x10 byte.  This tolerates leading framing
    bytes and extra fields between records without needing to understand
    the outer message structure.
    """
    param_map: dict[int, str] = {}
    try:
        raw = zf.read('trp/providers/sp1/cdf/declarations.cdf')
        data = _cdf_decompress(raw)
    except Exception as exc:
        logger.warning('TRP: cannot decompress declarations.cdf: %s', exc)
        return param_map

    n = len(data)
    pos = 0
    while pos < n - 1:
        if data[pos] != 0x10:
            pos += 1
            continue

        # Read candidate param_id varint
        try:
            param_id, after = _read_varint(data, pos + 1)
        except Exception:
            pos += 1
            continue

        if param_id <= 0:
            pos += 1
            continue

        # Look backward for 0x0A [len_byte] [name string of len_byte bytes]
        # where the string ends exactly at pos (the 0x10 tag position).
        name = ''
        for length in range(1, min(200, pos - 1)):
            str_start = pos - length
            if str_start < 2:
                break
            len_byte = data[str_start - 1]
            if len_byte != length:
                continue
            if data[str_start - 2] != 0x0A:
                continue
            # Candidate: check it decodes to a valid UTF-8 string
            chunk = data[str_start:pos]
            try:
                candidate = chunk.decode('utf-8')
                # Sanity: parameter names are dotted identifiers
                if all(c.isalnum() or c in '._- ' for c in candidate):
                    name = candidate
                    break
            except UnicodeDecodeError:
                pass

        if name:
            param_map[param_id] = name

        pos = after  # advance past the varint we just consumed

    logger.debug('TRP: loaded %d parameter declarations', len(param_map))
    return param_map


# ---------------------------------------------------------------------------
# Parse data.cdf — session-level parameter values
# ---------------------------------------------------------------------------

def _parse_data_cdf(
    zf: zipfile.ZipFile,
    param_map: dict[int, str],
) -> dict[int, dict[str, object]]:
    """
    Parse all measurement records from data.cdf.

    data.cdf is the main per-second timeseries from TEMS Pocket.
    Records are varint-length-prefixed protobuf messages, each containing:
      field 1 (LEN): sub-message { unix_ts_seconds (varint), key (varint) }
      field 3 (LEN): sub-message { param_id (varint), ... value ... }

    Returns dict: unix_ts_seconds -> {param_name: value}.
    Multiple records at the same second are merged (last-write wins).
    """
    result: dict[int, dict[str, object]] = {}
    try:
        raw = zf.read('trp/providers/sp1/cdf/data.cdf')
        data = _cdf_decompress(raw)
    except Exception as exc:
        logger.warning('TRP: cannot decompress data.cdf: %s', exc)
        return result

    pos = 0
    dlen = len(data)
    while pos < dlen:
        try:
            rec_len, pos = _read_varint(data, pos)
        except Exception:
            break

        if rec_len == 0:
            pos += 1
            continue
        if pos + rec_len > dlen:
            break

        rec = data[pos:pos + rec_len]
        pos += rec_len

        # Extract timestamp (field 1 LEN sub-message, field 1 inside = unix seconds)
        ts_sec: int = 0
        rpos = 0
        slot: dict[str, object] = {}

        while rpos < len(rec):
            try:
                tag, rpos = _read_varint(rec, rpos)
                wire_type = tag & 0x07
                field_num = tag >> 3

                if wire_type == 2:
                    sub_len, rpos = _read_varint(rec, rpos)
                    sub = rec[rpos:rpos + sub_len]
                    rpos += sub_len

                    if field_num == 1:
                        # Timestamp sub-message: field 1 = unix_ts_seconds
                        if len(sub) >= 2:
                            try:
                                inner_tag, ipos = _read_varint(sub, 0)
                                if (inner_tag & 0x07) == 0:
                                    ts_sec, _ = _read_varint(sub, ipos)
                            except Exception:
                                pass
                    elif field_num >= 3:
                        # Param value sub-message
                        _extract_param_value(sub, param_map, slot)

                elif wire_type == 0:
                    _, rpos = _read_varint(rec, rpos)
                elif wire_type == 1:
                    rpos += 8
                elif wire_type == 5:
                    rpos += 4
                else:
                    rpos += 1
            except Exception:
                break

        if ts_sec and slot:
            if ts_sec not in result:
                result[ts_sec] = {}
            result[ts_sec].update(slot)

    logger.debug('TRP: extracted %d time slots from data.cdf', len(result))
    return result


def _extract_param_value(
    sub: bytes,
    param_map: dict[int, str],
    out: dict[str, object],
) -> None:
    """
    Try to read (param_id, value) from a protobuf sub-message.
    Field 1 = param_id (varint); subsequent field = value.
    Writes into `out` keyed by param_name.
    """
    if len(sub) < 2:
        return
    try:
        spos = 0
        tag0, spos = _read_varint(sub, spos)
        if (tag0 & 0x07) != 0:
            return  # field 1 must be varint
        param_id, spos = _read_varint(sub, spos)

        if spos >= len(sub):
            return

        param_name = param_map.get(param_id, '')
        if not param_name:
            return

        # Next field determines value type
        tag1, spos = _read_varint(sub, spos)
        wire_type = tag1 & 0x07

        if wire_type == 0:
            raw_val, _ = _read_varint(sub, spos)
            out[param_name] = raw_val
        elif wire_type == 5:  # 32-bit float
            if spos + 4 <= len(sub):
                out[param_name] = struct.unpack_from('<f', sub, spos)[0]
        elif wire_type == 1:  # 64-bit double
            if spos + 8 <= len(sub):
                out[param_name] = struct.unpack_from('<d', sub, spos)[0]
        elif wire_type == 2:  # string
            str_len, spos = _read_varint(sub, spos)
            if spos + str_len <= len(sub):
                try:
                    out[param_name] = sub[spos:spos + str_len].decode('utf-8')
                except UnicodeDecodeError:
                    pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Parse services.xml — call / MOS events
# ---------------------------------------------------------------------------

def _parse_services(zf: zipfile.ZipFile) -> list[dict]:
    """
    Parse trp/providers/sp1/services/services.xml.

    Each <ServiceInformation> is a property bag (not attribute-based):
      Properties/Property[Name=Identity]/Value/Id       -> service type id
      Properties/Property[Name=ServiceAction]/Value/Id  -> 1=Start, 2=Stop
      Properties/Property[Name=ServiceSessionIdentity]/Value -> session id
      Properties/Property[Name=UtcTime]/Value/Time      -> timestamp

    We pair Start/Stop events by ServiceSessionIdentity and return
    one dict per active voice call period (VoiceQuality, id=4101).

    Returns list of dicts: {start_dt, stop_dt, service_type, mos,
                             duration_s, phone, outcome, svc_id}.
    """
    events: list[dict] = []
    try:
        raw = zf.read('trp/providers/sp1/services/services.xml')
        root = ET.fromstring(raw.decode('utf-8', errors='replace'))
    except Exception as exc:
        logger.warning('TRP: cannot parse services.xml: %s', exc)
        return events

    def _tag(el: ET.Element) -> str:
        return el.tag.split('}', 1)[-1] if '}' in el.tag else el.tag

    def _subtag_text(el: ET.Element, subtag: str) -> str:
        for c in el:
            if _tag(c) == subtag:
                return (c.text or '').strip()
        return (el.text or '').strip()

    def _prop_dict(si: ET.Element) -> dict:
        """Build a flat dict from the Properties and Settings children."""
        d: dict = {}
        for section_name in ('Properties', 'Settings'):
            section = next((c for c in si if _tag(c) == section_name), None)
            if section is None:
                continue
            for prop in section:
                if _tag(prop) != 'Property':
                    continue
                name_el = next((c for c in prop if _tag(c) == 'Name'), None)
                val_el  = next((c for c in prop if _tag(c) == 'Value'), None)
                if name_el is None:
                    continue
                pname = (name_el.text or '').strip()
                if val_el is None:
                    d[pname] = ''
                    continue

                if pname == 'Identity':
                    d['_svc_id']   = _subtag_text(val_el, 'Id')
                    d['_svc_name'] = _subtag_text(val_el, 'Value')
                elif pname == 'ServiceAction':
                    d['_action_id']  = _subtag_text(val_el, 'Id')
                    d['_action_val'] = _subtag_text(val_el, 'Value')
                elif pname == 'ServiceSessionIdentity':
                    d['_session_id'] = (val_el.text or '').strip()
                elif pname == 'UtcTime':
                    d['_utc_time'] = _subtag_text(val_el, 'Time')
                elif pname == 'PhoneNumber':
                    d['_phone'] = _subtag_text(val_el, 'Content')
                elif pname == 'Status':
                    d['_status'] = _subtag_text(val_el, 'Value')
                elif pname in ('Duration', 'ExecutionTime'):
                    dur_str = (val_el.text or '').strip()
                    if not dur_str:
                        dur_str = _subtag_text(val_el, 'Value')
                    d['_duration_str'] = dur_str
                elif 'MOS' in pname.upper():
                    try:
                        d['_mos'] = float(_subtag_text(val_el, 'Value') or val_el.text or '0')
                    except ValueError:
                        pass
        return d

    # Collect all start/stop events keyed by session_id
    starts: dict[str, dict] = {}
    stops:  dict[str, dict] = {}

    phone_by_dial_session: dict[str, str] = {}  # Dial session_id -> phone number

    for si in root.iter():
        if _tag(si) != 'ServiceInformation':
            continue

        pd = _prop_dict(si)
        svc_id    = pd.get('_svc_id', '')
        action_id = pd.get('_action_id', '')
        sess_id   = pd.get('_session_id', '')
        utc_time  = pd.get('_utc_time', '')

        if not sess_id or not utc_time:
            continue

        if action_id == '1':
            starts[sess_id] = pd
        elif action_id == '2':
            stops[sess_id] = pd

        # Track phone number from Dial events
        if svc_id == '4096' and pd.get('_phone'):
            phone_by_dial_session[sess_id] = pd['_phone']

    # Also capture phone from Settings on VoiceQuality events?
    # (In this file, phone is stored on the Dial session)
    # Try to infer the phone number: it was on the adjacent Dial session
    # We'll pass it through on the returned events below.

    # For each VoiceQuality Start, pair with its Stop to build a call event
    vq_start_sessions = [
        sid for sid, pd in starts.items()
        if pd.get('_svc_id') == '4101'
    ]
    # Also get the most recent phone number seen (applies to all calls)
    phone = next(iter(phone_by_dial_session.values()), '')

    for sess_id in sorted(vq_start_sessions, key=lambda s: starts[s].get('_utc_time', '')):
        start_pd = starts[sess_id]
        stop_pd  = stops.get(sess_id, {})

        start_dt = _parse_iso_dt(start_pd.get('_utc_time', ''))
        stop_dt  = _parse_iso_dt(stop_pd.get('_utc_time', ''), start_dt) if stop_pd else None

        if start_dt is None:
            continue

        duration_s: float | None = None
        for dur_key in ('_duration_str',):
            dur_str = (stop_pd or start_pd).get(dur_key, '')
            if dur_str.startswith('PT') and dur_str.endswith('S'):
                try:
                    duration_s = float(dur_str[2:-1])
                    break
                except ValueError:
                    pass
        if duration_s is None and stop_dt is not None:
            duration_s = (stop_dt - start_dt).total_seconds()

        status = (stop_pd.get('_status') or '').upper()
        outcome = {'EXECUTED': 'SUCCESS', 'USERSTOPPED': 'SUCCESS',
                   'FAILED': 'FAILED', 'DROPPED': 'DROPPED'}.get(status, 'SUCCESS')

        mos = start_pd.get('_mos') or stop_pd.get('_mos') if stop_pd else start_pd.get('_mos')

        events.append({
            'start_dt':     start_dt,
            'stop_dt':      stop_dt,
            'service_type': 'VOICE',
            'mos':          mos,
            'duration_s':   duration_s,
            'phone':        start_pd.get('_phone') or phone,
            'outcome':      outcome,
            'svc_id':       start_pd.get('_svc_id', ''),
        })

    logger.debug('TRP: parsed %d VoiceQuality call events from services.xml', len(events))
    return events


# ---------------------------------------------------------------------------
# Parse ch1/channel.log — per-second radio measurements
# ---------------------------------------------------------------------------

def _parse_ch1(
    zf: zipfile.ZipFile,
    session_start: datetime | None,
    session_stop:  datetime | None,
    param_map: dict[int, str],
) -> dict[datetime, dict]:
    """
    Parse trp/providers/sp1/channels/ch1/channel.log.

    Record format (after decompression):
      [8 bytes: .NET DateTime int64 LE] [2 bytes: 05 00] [N bytes: payload]

    Scans for valid .NET DateTime boundaries, extracts payloads for records
    within the session time window, and attempts protobuf decoding.

    Returns dict: timestamp_utc -> {param_name: value}.
    """
    radio_ts: dict[datetime, dict] = {}

    ch1_path = 'trp/providers/sp1/channels/ch1/channel.log'
    if ch1_path not in zf.namelist():
        logger.debug('TRP: ch1/channel.log not present')
        return radio_ts

    try:
        raw = zf.read(ch1_path)
        data = _cdf_decompress(raw)
    except Exception as exc:
        logger.warning('TRP: cannot decompress ch1/channel.log: %s', exc)
        return radio_ts

    # Build session window in ticks
    start_ticks = _MIN_VALID_TICKS
    stop_ticks  = _MAX_VALID_TICKS
    margin_ticks = 5_000_000_000  # 0.5 s safety margin in ticks (1 tick = 100 ns)

    if session_start:
        us_from_epoch = int((session_start - datetime(1970, 1, 1, tzinfo=dt_tz.utc)).total_seconds() * 1_000_000)
        start_ticks = _NET_EPOCH_TICKS + us_from_epoch * 10 - margin_ticks

    if session_stop:
        us_from_epoch = int((session_stop - datetime(1970, 1, 1, tzinfo=dt_tz.utc)).total_seconds() * 1_000_000)
        stop_ticks = _NET_EPOCH_TICKS + us_from_epoch * 10 + margin_ticks

    # Scan the decompressed blob for timestamp positions
    # A valid record starts with 8 bytes that decode to a plausible .NET DateTime,
    # followed by the 2-byte type marker 05 00.
    timestamp_positions: list[tuple[int, int]] = []  # (byte_offset, ticks)

    i = 0
    dlen = len(data)
    while i <= dlen - 10:
        ticks = struct.unpack_from('<q', data, i)[0]
        if (_MIN_VALID_TICKS <= ticks <= _MAX_VALID_TICKS
                and data[i + 8] == 0x05 and data[i + 9] == 0x00):
            timestamp_positions.append((i, ticks))
            i += 10  # skip past the header we just verified
        else:
            i += 1

    if not timestamp_positions:
        logger.debug('TRP: no valid .NET DateTime records found in ch1')
        return radio_ts

    logger.debug('TRP: found %d ch1 records, session window: %d-%d ticks',
                 len(timestamp_positions), start_ticks, stop_ticks)

    # Reverse lookup: param_name -> param_id
    name_to_id = {v: k for k, v in param_map.items()}

    # For each record in the session window, extract payload and decode
    for idx, (offset, ticks) in enumerate(timestamp_positions):
        if ticks < start_ticks or ticks > stop_ticks:
            continue

        # Payload = bytes between this record's header end and next record's start
        payload_start = offset + 10
        payload_end = (timestamp_positions[idx + 1][0]
                       if idx + 1 < len(timestamp_positions)
                       else dlen)
        payload = data[payload_start:payload_end]

        if not payload:
            continue

        ts = _net_ticks_to_dt(ticks)
        params = _decode_ch1_payload(payload, param_map)
        if params:
            # Merge into existing entry for this second (truncate to second)
            ts_sec = ts.replace(microsecond=0)
            if ts_sec not in radio_ts:
                radio_ts[ts_sec] = {}
            radio_ts[ts_sec].update(params)

    logger.debug('TRP: decoded %d ch1 time slots with radio data', len(radio_ts))
    return radio_ts


def _decode_ch1_payload(
    payload: bytes,
    param_map: dict[int, str],
) -> dict[str, object]:
    """
    Best-effort protobuf scan of a ch1 record payload.

    Tries two strategies:
    1. Flat message: field 1 = param_id, field 2 = value
    2. Nested: each LEN sub-message contains (param_id, value)
    """
    params: dict[str, object] = {}
    out: dict[str, object] = {}

    # Strategy 1: treat payload as a flat protobuf message
    try:
        _extract_param_value(payload, param_map, out)
    except Exception:
        pass

    # Strategy 2: scan for LEN sub-messages and try each
    pos = 0
    while pos < len(payload) - 1:
        try:
            tag, pos = _read_varint(payload, pos)
            wire_type = tag & 0x07
            if wire_type == 2:
                sub_len, pos = _read_varint(payload, pos)
                sub = payload[pos:pos + sub_len]
                pos += sub_len
                _extract_param_value(sub, param_map, out)
            elif wire_type == 0:
                _, pos = _read_varint(payload, pos)
            elif wire_type == 5:
                pos += 4
            elif wire_type == 1:
                pos += 8
            else:
                pos += 1
        except Exception:
            break

    # Translate extracted param names to our simplified key space
    for param_name, value in out.items():
        # Find the param_id to look up the field mapping
        for pid, name in param_map.items():
            if name == param_name and pid in _PARAM_FIELD:
                field_name, type_hint = _PARAM_FIELD[pid]
                try:
                    if type_hint == 'float':
                        value = float(value)
                        # speed: m/s -> km/h
                        if pid == _PID_SPEED:
                            value = value * 3.6
                    elif type_hint == 'int':
                        value = int(value)
                        if pid == _PID_TECH:
                            value = _TECH_ENUM.get(int(value), '')
                except (ValueError, TypeError):
                    pass

                if field_name:
                    params[field_name] = value
                raw_key = _RAW_PARAM_KEY.get(pid)
                if raw_key:
                    params[raw_key] = value
                break

    return params


# ---------------------------------------------------------------------------
# Main parser class
# ---------------------------------------------------------------------------

class TrpDriveTestParser(DriveTestParser):
    """
    TEMS Pocket TRP drive-test file parser.

    Yields one ParsedMeasurement per GPS fix from the wptrack.xml track,
    enriched with:
      - Per-second radio measurements from ch1/channel.log (nearest-neighbour)
      - Session-level params from data.cdf (ARFCN, LAC, CI)
      - Call / MOS events from services.xml
      - Technology from content.xml Tags
    """

    name = 'TEMS Pocket TRP'
    file_extensions = ['.trp']
    magic_bytes = '504b'  # ZIP "PK" header

    def parse(self, file_path: Path) -> Generator[ParsedMeasurement, None, None]:
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                yield from self._parse_zip(zf, file_path)
        except zipfile.BadZipFile as exc:
            logger.error('TRP parser: not a valid ZIP archive: %s (%s)', file_path, exc)
        except Exception:
            logger.exception('TRP parser: unexpected error parsing %s', file_path)

    def _parse_zip(
        self,
        zf: zipfile.ZipFile,
        file_path: Path,
    ) -> Generator[ParsedMeasurement, None, None]:

        # ---- 1. Session metadata + device info ----
        meta = _parse_content_xml(zf)
        technology = meta['technology']
        device_info = _parse_device_info(zf)

        # ---- 2. GPS track ----
        gps_fixes = _parse_gps_track(zf)
        if not gps_fixes:
            logger.warning('TRP parser: no GPS fixes in %s', file_path)
            return

        # ---- 3. Parameter schema ----
        param_map = _parse_declarations(zf)

        # Reverse lookup helpers
        def _pname(pid: int) -> str:
            return param_map.get(pid, '')

        # ---- 4. Per-second timeseries from data.cdf ----
        # Returns {unix_ts_seconds: {param_name: value}}
        data_ts = _parse_data_cdf(zf, param_map)

        # Propagate sparse cell-identity values (LAC, CI, ARFCN) forward through
        # the timeseries — TEMS only logs them on change, not every second.
        sparse_params = [
            _pname(_PID_LAC), _pname(_PID_CI),
            _pname(_PID_ARFCN), _pname(_PID_CUR_ARFCN),
            _pname(_PID_BSIC),
        ]
        sparse_params = [p for p in sparse_params if p]

        last_known: dict[str, object] = {}
        for ts_key in sorted(data_ts.keys()):
            slot = data_ts[ts_key]
            for sp in sparse_params:
                if sp in slot:
                    last_known[sp] = slot[sp]
                elif sp in last_known:
                    slot[sp] = last_known[sp]

        # Sorted unix-second keys for binary-search
        data_ts_keys = sorted(data_ts.keys())

        # Technology from first slot that has it
        if not technology:
            tech_pname = _pname(_PID_TECH)
            if tech_pname:
                for slot in data_ts.values():
                    tv = slot.get(tech_pname)
                    if tv is not None:
                        technology = _TECH_ENUM.get(int(tv), '')
                        break

        # MCC/MNC from first slot
        sess_obs_mcc = ''
        sess_obs_mnc = ''
        mno_pname = _pname(_PID_MNO)
        if mno_pname:
            for slot in data_ts.values():
                mno = slot.get(mno_pname)
                if isinstance(mno, str) and '-' in mno:
                    parts = mno.split('-', 1)
                    sess_obs_mcc = parts[0].strip()
                    sess_obs_mnc = parts[1].strip()
                    break

        # ---- 5. Service events ----
        service_events = _parse_services(zf)

        # ---- 6. Convert param names to field aliases for fast lookup ----
        # Build a map: param_id -> field label (same as _PARAM_FIELD but keyed by name)
        name_to_field: dict[str, tuple[str | None, str]] = {}
        for pid, (field_name, type_hint) in _PARAM_FIELD.items():
            pn = _pname(pid)
            if pn:
                name_to_field[pn] = (field_name, type_hint, pid)

        def _slot_to_radio(slot: dict) -> dict:
            """Convert a data.cdf slot (param_name -> raw_value) to radio field dict."""
            out: dict = {}
            for pname, val in slot.items():
                entry = name_to_field.get(pname)
                if entry is None:
                    continue
                field_name, type_hint, pid = entry
                try:
                    if type_hint == 'float':
                        val = float(val)
                        if pid == _PID_SPEED:
                            val = val * 3.6  # m/s -> km/h
                    elif type_hint == 'int':
                        val = int(val)
                        if pid == _PID_TECH:
                            val = _TECH_ENUM.get(int(val), '')
                    elif type_hint == 'str':
                        val = str(val)
                except (ValueError, TypeError):
                    continue

                if field_name:
                    # For rssi: prefer RssiFull over Common.Rssi (don't overwrite if we already have it)
                    if field_name == 'rssi' and 'rssi' in out:
                        if pid not in (_PID_RSSI_FULL, _PID_RSSI_SUB):
                            continue
                    out[field_name] = val

                raw_key = _RAW_PARAM_KEY.get(pid)
                if raw_key:
                    out[raw_key] = val

            return out

        # ---- 7. Yield one measurement per GPS fix ----
        def _to_int(v) -> int | None:
            try:
                return int(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        def _to_float(v) -> float | None:
            try:
                return float(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        for seq, (ts, lat, lon, alt) in enumerate(gps_fixes, start=1):
            ts_unix = int(ts.timestamp())

            # Find nearest data.cdf slot (binary search, within 2 seconds)
            radio_params: dict = {}
            if data_ts_keys:
                idx = bisect_left(data_ts_keys, ts_unix)
                candidates = []
                if idx < len(data_ts_keys):
                    candidates.append(data_ts_keys[idx])
                if idx > 0:
                    candidates.append(data_ts_keys[idx - 1])
                if candidates:
                    best_key = min(candidates, key=lambda k: abs(k - ts_unix))
                    if abs(best_key - ts_unix) <= 2:
                        radio_params = _slot_to_radio(data_ts[best_key])

            # Correlate service events
            active_event: dict | None = None
            for ev in service_events:
                ev_stop = ev['stop_dt'] or (ev['start_dt'] + timedelta(
                    seconds=ev.get('duration_s') or 0))
                if ev['start_dt'] <= ts <= ev_stop:
                    active_event = ev
                    break

            svc_type    = ''
            svc_outcome = ''
            mos_val: float | None = None
            call_dur_s: int | None = None
            if active_event:
                svc_type    = 'VOICE'
                svc_outcome = active_event['outcome']
                mos_val     = active_event.get('mos')
                d           = active_event.get('duration_s')
                call_dur_s  = int(d) if d is not None else None

            rssi  = _to_float(radio_params.get('rssi'))
            rxq   = _to_int(radio_params.get('rxqual'))
            spd   = _to_float(radio_params.get('speed_kmh'))
            arfcn = _to_int(radio_params.get('obs_earfcn'))
            lac   = _to_int(radio_params.get('obs_lac'))
            ci    = _to_int(radio_params.get('obs_ci'))
            tech  = str(radio_params.get('technology') or technology)

            # RSSI sanity: GSM RssiFull is typically -110..-47 dBm
            if rssi is not None and not (-130.0 <= rssi <= -30.0):
                rssi = None

            # RxQual is a 0-7 band (3GPP TS 45.008). Anything else is not RxQual.
            if rxq is not None and not (0 <= rxq <= 7):
                rxq = None

            raw: dict = {}
            for rk in ('gsm_bsic', 'gsm_ta', 'gsm_txpower'):
                if rk in radio_params:
                    raw[rk] = radio_params[rk]
            if meta.get('tags'):
                raw['trp_tags'] = ';'.join(meta['tags'])
            if active_event and active_event.get('phone'):
                raw['dialed_number'] = active_event['phone']
            if device_info.get('model_name') or device_info.get('imei'):
                raw['__device__'] = device_info

            yield ParsedMeasurement(
                sequence_num=seq,
                captured_at=ts,
                latitude=lat,
                longitude=lon,
                altitude_m=_to_float(alt) if alt else None,
                speed_kmh=spd,
                obs_mcc=sess_obs_mcc,
                obs_mnc=sess_obs_mnc,
                obs_lac=lac,
                obs_ci=ci,
                obs_earfcn=arfcn,
                technology=tech,
                rssi=rssi,
                rxqual=rxq,
                service_type=svc_type,
                service_outcome=svc_outcome,
                call_duration_s=call_dur_s,
                mos=_to_float(mos_val),
                raw_data=raw,
                is_valid=True,
            )
