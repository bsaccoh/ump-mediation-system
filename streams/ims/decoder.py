"""
Huawei ATS9900 IMS CDR Decoder
================================
Decodes binary ASN.1 BER IMS CDR files from the Huawei ATS9900 softswitch.

Supports:
  - VoLTE / VoBB voice session CDRs
  - IMS event CDRs (REGISTER, SUBSCRIBE, …)
  - FMC (Fixed-Mobile Convergence) CDRs

Record tag: 0xBF 0x45  (context-specific constructed, tag=69)

Field tags are documented in:
  Huawei IMS CDR Format Description (ATS9900 / CCF).

Author: B-TEC Digital Solution Ltd
"""

import os
import re
import struct
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Record-level tag
# ---------------------------------------------------------------------------
IMS_RECORD_TAG = (0xBF, 0x45)   # 2-byte: constructed context-specific tag 69

# ---------------------------------------------------------------------------
# Role-of-node enum
# ---------------------------------------------------------------------------
ROLE_OF_NODE = {
    0: 'ORIGINATING',
    1: 'TERMINATING',
    2: 'PROXY',
    3: 'B2BUA',
}

# ---------------------------------------------------------------------------
# causeForRecordClosing enum (3GPP TS 32.298)
# ---------------------------------------------------------------------------
CAUSE_FOR_CLOSING = {
    0:  'SERVICE_DELIVERY_END_SUCCESSFULLY',
    1:  'partialRecord',
    2:  'partialRecordCallReestablishment',
    3:  'unsuccessfulCallAttempt',
    4:  'stableCallAbnormalTermination',
    5:  'cAMELInitCallRelease',
    6:  'unauthorizedRequestingNetwork',
    7:  'unauthorizedLCSClient',
    8:  'positionMethodFailure',
    9:  'unknownOrUnreachableLCSClient',
    10: 'listofDownstreamNodeChange',
    17: 'timeLimit',
    18: 'serviceChange',
    19: 'managementIntervention',
    20: 'intraSGSNIntersystemChange',
    21: 'rATChange',
    22: 'mSTimeZoneChange',
    23: 'sGSNPLMNIDChange',
    52: 'volumeLimit',
    53: 'timeLimit2',
    54: 'servingNodeChange',
    55: 'maxChangeCond',
    56: 'managementIntervention2',
    57: 'intraSGSNRAUorPeriodic',
    58: 'intraSGSNHOComplete',
    59: 'intraSGSNHOCancelled',
}

# ---------------------------------------------------------------------------
# Subscription ID type (3GPP TS 32.298)
# ---------------------------------------------------------------------------
SUBSCRIPTION_ID_TYPE = {
    0: 'END_USER_E164',
    1: 'END_USER_IMSI',
    2: 'END_USER_SIP_URI',
    3: 'END_USER_NAI',
    4: 'END_USER_PRIVATE',
}

# Service-Reason-Return-Code human descriptions (Huawei ATS9900 mnemonics)
SERVICE_REASON_CODE = {
    0:  'Normal end of session',
    1:  'Abnormal end of session',
    2:  'Call terminated by user',
    3:  'Call terminated by network',
    4:  'Service unavailable',
    5:  'Subscriber not reachable',
    6:  'Call rejected',
    7:  'No answer',
    8:  'Busy',
    9:  'Number invalid',
    10: 'Insufficient balance',
    11: 'Time-out',
}


# ===========================================================================
# Low-level BER helpers
# ===========================================================================

def _read_length(data: bytes, offset: int) -> Tuple[int, int]:
    """Read BER length at offset. Returns (length, bytes_consumed)."""
    if offset >= len(data):
        return 0, 0
    first = data[offset]
    if first & 0x80 == 0:
        return first, 1
    num_bytes = first & 0x7F
    if num_bytes == 0:
        return 0, 1  # indefinite length — treat as 0
    if offset + num_bytes >= len(data):
        return 0, 1
    length = 0
    for i in range(num_bytes):
        length = (length << 8) | data[offset + 1 + i]
    return length, 1 + num_bytes


def _read_tag(data: bytes, offset: int) -> Tuple[int, int, bool]:
    """Read BER tag. Returns (tag_number, bytes_consumed, is_constructed).

    For single-byte tags the full first byte is returned as the tag key so that
    callers can use the original byte value (e.g. 0x80, 0xA6, 0x9E) directly in
    comparisons.  Only multi-byte tags (first byte & 0x1F == 0x1F) return a
    synthesised integer > 255.
    """
    if offset >= len(data):
        return -1, 0, False
    b0 = data[offset]
    is_constructed = bool(b0 & 0x20)
    tag_low = b0 & 0x1F
    consumed = 1
    if tag_low == 0x1F:
        # Multi-byte tag — accumulate low-7-bit groups
        tag_num = 0
        while offset + consumed < len(data):
            byte = data[offset + consumed]
            consumed += 1
            tag_num = (tag_num << 7) | (byte & 0x7F)
            if not (byte & 0x80):
                break
        return tag_num, consumed, is_constructed
    # Single-byte tag: return the full original byte as the key
    return b0, consumed, is_constructed


def _parse_tlv_list(data: bytes) -> List[Tuple[int, bytes, bool]]:
    """Parse a flat sequence of TLV elements. Returns [(tag, value_bytes, is_constructed), ...]."""
    items = []
    offset = 0
    while offset < len(data):
        if offset >= len(data):
            break
        tag_num, tag_len, is_constr = _read_tag(data, offset)
        if tag_len == 0:
            break
        offset += tag_len
        length, len_bytes = _read_length(data, offset)
        offset += len_bytes
        if offset + length > len(data):
            length = len(data) - offset
        value = data[offset:offset + length]
        items.append((tag_num, value, is_constr))
        offset += length
    return items


def _bcd_decode(b: int) -> str:
    """Decode one BCD byte → 2 decimal digits string."""
    high = (b >> 4) & 0x0F
    low  = b & 0x0F
    h = str(high) if high <= 9 else ''
    l = str(low)  if low  <= 9 else ''
    return h + l


def _decode_telephony_address(data: bytes) -> str:
    """Decode a telephony address that may be ASCII digits OR BCD-encoded.

    Huawei IN/CAMEL addresses (e.g. SCF-Address) are BCD-packed high-nibble-
    first with 0xF as the filler nibble.  MSC/VLR-Number etc. arrive as
    plain ASCII digits.  This helper picks the right form heuristically.
    """
    if not data:
        return ''
    # Plain ASCII digits / +-style E.164?
    try:
        ascii_form = data.decode('ascii').strip('\x00').strip()
        if ascii_form and all(c.isdigit() or c in '+*#' for c in ascii_form):
            return ascii_form
    except Exception:
        pass
    # BCD-pack: high nibble first; 0xF terminates
    digits = []
    for b in data:
        hi = (b >> 4) & 0x0F
        lo = b & 0x0F
        if hi <= 9:
            digits.append(str(hi))
        else:
            break
        if lo <= 9:
            digits.append(str(lo))
        else:
            break
    return ''.join(digits)


def _decode_timestamp(data: bytes) -> Optional[datetime]:
    """
    Decode a 9-byte IMS timestamp (3GPP TimeStamp OCTET STRING SIZE(9)).

    Huawei ATS9900 layout (verified against real CDR files):
      Byte  0   : Year last-2-digits BCD  (0x26 → 26 → 2026)
      Byte  1   : Month BCD (01–12)
      Byte  2   : Day BCD   (01–31)
      Byte  3   : Hour BCD  (00–23)
      Byte  4   : Minute BCD (00–59)
      Byte  5   : Second BCD (00–59)
      Bytes 6–8 : UTC offset / ms (not decoded)
    """
    if not data or len(data) < 6:
        return None
    try:
        year_2d = int(_bcd_decode(data[0]) or '0')
        year    = 2000 + year_2d if year_2d < 70 else 1900 + year_2d
        month   = int(_bcd_decode(data[1]) or '1')
        day     = int(_bcd_decode(data[2]) or '1')
        hour    = int(_bcd_decode(data[3]) or '0')
        minute  = int(_bcd_decode(data[4]) or '0')
        second  = int(_bcd_decode(data[5]) or '0')
        if not (1 <= month <= 12):
            month = 1
        if not (1 <= day <= 31):
            day = 1
        hour   = max(0, min(23, hour))
        minute = max(0, min(59, minute))
        second = max(0, min(59, second))
        return datetime(year, month, day, hour, minute, second,
                        tzinfo=timezone.utc)
    except Exception:
        return None


def _decode_string(data: bytes) -> str:
    """Decode GraphicString / UTF8String / IA5String bytes.

    Replaces control characters (newline, tab, NULL, etc.) with a SPACE so
    that downstream CSV output stays on one row (Excel splits rows on
    embedded newlines even inside quoted fields), while still preserving
    multi-token text such as SDP blocks.
    """
    for enc in ('utf-8', 'latin-1', 'ascii'):
        try:
            s = data.decode(enc)
            s = ''.join(c if c.isprintable() else ' ' for c in s)
            # Collapse runs of whitespace to a single space
            return ' '.join(s.split()).strip()
        except Exception:
            pass
    return data.hex()


def _decode_integer(data: bytes) -> int:
    """Decode BER INTEGER."""
    if not data:
        return 0
    result = 0
    for b in data:
        result = (result << 8) | b
    return result


# ---------------------------------------------------------------------------
# 3GPP TS 24.173 / 24.229 mappings used for display
# ---------------------------------------------------------------------------
MMTEL_SUPPLEMENTARY_SERVICE = {
    0:  'OIP',   1:  'OIR',   2:  'TIP',   3:  'TIR',
    4:  'HOLD',  5:  'CW',    6:  'CB',    7:  'CDIV',
    8:  'CONF',  9:  'MWI',   10: 'FA',    11: 'AoC',
    12: 'CUG',   13: 'PNM',   14: 'CRS',   15: 'CCBS',
    16: 'CCNR',  17: 'MCID',  18: 'CAT',   19: 'ICB',
    20: 'OCB',   21: 'ACR',
}

PRIVATE_USER_EQUIPMENT_TYPE = {
    0: 'IMEI', 1: 'ESN', 2: 'MEID', 3: 'EUI64', 4: 'MODIFIED_EUI64',
}

ONLINE_CHARGING_FLAG = {0: 'Offline_Charging', 1: 'Online_Charging'}


# ---------------------------------------------------------------------------
# SDP parser
# ---------------------------------------------------------------------------
import re as _re

_SDP_M_LINE     = _re.compile(r'm=(\w+)\s+(\d+)\s+([\w/]+)\s+(.+)', _re.I)
_SDP_RTPMAP     = _re.compile(r'a=rtpmap:(\d+)\s+([\w\-./]+)', _re.I)


def parse_sdp_block(sdp_text: str) -> dict:
    """Extract codec / port / payload info from an SDP-Media-Components dump.

    Looks for the m=<media> <port> <proto> <fmts> line and the rtpmap entries.
    Returns dict with keys:
      media_type, rtp_port, rtp_protocol, sip_codec_payload,
      telephone_event_payload, codec
    """
    out = {}
    if not sdp_text:
        return out

    # Find the first m= line
    m = _SDP_M_LINE.search(sdp_text)
    if m:
        out['media_type']   = m.group(1).lower()      # audio / video / text
        out['rtp_port']     = m.group(2)
        out['rtp_protocol'] = m.group(3).upper()
        # Payload types
        fmts = m.group(4).split()
        if fmts:
            out['sip_codec_payload'] = fmts[0]

    # Build a payload->codec lookup
    pt_to_codec = {}
    for rm in _SDP_RTPMAP.finditer(sdp_text):
        pt = rm.group(1)
        codec = rm.group(2)
        pt_to_codec[pt] = codec
        if 'telephone-event' in codec.lower() and not out.get('telephone_event_payload'):
            out['telephone_event_payload'] = pt

    # First non-DTMF codec
    if 'sip_codec_payload' in out:
        codec = pt_to_codec.get(out['sip_codec_payload'])
        if codec and 'telephone-event' in codec.lower():
            # If first PT was DTMF, try the next one
            for pt, c in pt_to_codec.items():
                if 'telephone-event' not in c.lower():
                    out['codec'] = c
                    break
        elif codec:
            out['codec'] = codec
    elif pt_to_codec:
        # No m-line — just pick any non-DTMF
        for pt, c in pt_to_codec.items():
            if 'telephone-event' not in c.lower():
                out['codec'] = c
                break
    return out


# ---------------------------------------------------------------------------
# Access-Network-Info parser (P-Access-Network-Info RFC 7913 / TS 24.229)
# ---------------------------------------------------------------------------

# Common 3GPP-access-network-spec → technology mapping
_TECH_MAP = {
    '3GPP-E-UTRAN-FDD':   'EUTRAN',
    '3GPP-E-UTRAN-TDD':   'EUTRAN',
    '3GPP-E-UTRAN':       'EUTRAN',
    '3GPP-UTRAN-FDD':     'UTRAN',
    '3GPP-UTRAN-TDD':     'UTRAN',
    '3GPP-UTRAN':         'UTRAN',
    '3GPP-GERAN':         'GERAN',
    '3GPP-NR':            'NR',
    '3GPP-NR-FDD':        'NR',
    '3GPP-NR-TDD':        'NR',
    '3GPP2-1X':           'CDMA',
    '3GPP2-HRPD':         'HRPD',
    'IEEE-802.11':        'WLAN',
    'IEEE-802.11a':       'WLAN',
    'IEEE-802.11b':       'WLAN',
    'IEEE-802.11g':       'WLAN',
    'IEEE-802.11n':       'WLAN',
    'DOCSIS':             'DOCSIS',
    'ADSL':               'ADSL',
    'ADSL2':              'ADSL',
    'VDSL':               'VDSL',
    'ETHERNET':           'ETHERNET',
}


def parse_access_network_info(ani: str) -> dict:
    """Parse the P-Access-Network-Info header value.

    Examples:
      '3GPP-E-UTRAN;utran-cell-id-3gpp=619011016010d90e;"ue-ip=10.10.26.19"'
      '3GPP-UTRAN-FDD;utran-cell-id-3gpp=619015234abcd'
      '3GPP-GERAN;cgi-3gpp=61901123456789'

    Returns a dict with keys:
      technology, serving_plmn, tac, lac, cell_id, enodeb_id, ue_ip
    Any field that can't be derived is omitted.
    """
    out = {}
    if not ani:
        return out

    # First token (before first ';') is the access-network spec
    parts = [p.strip() for p in ani.split(';') if p.strip()]
    if not parts:
        return out

    spec = parts[0].upper().strip('"')
    tech = _TECH_MAP.get(spec, '')
    if not tech:
        # Best-effort: keep the bare token if it looks 3GPP-ish
        if 'EUTRAN' in spec:  tech = 'EUTRAN'
        elif 'UTRAN' in spec: tech = 'UTRAN'
        elif 'GERAN' in spec: tech = 'GERAN'
        elif 'NR' in spec:    tech = 'NR'
        elif 'WLAN' in spec or '802.11' in spec: tech = 'WLAN'
    if tech:
        out['technology'] = tech

    # Iterate key=value pairs for cell info, IP, etc.
    for p in parts[1:]:
        p = p.strip().strip('"')
        if '=' not in p:
            # Non-key/value markers like 'network-provided' — ignore
            continue
        k, _, v = p.partition('=')
        k = k.strip().lower()
        v = v.strip().strip('"').strip()

        # All cell-ID-like keys share PLMN+location+cell layout
        if k in ('utran-cell-id-3gpp', 'eutran-cell-id-3gpp',
                 'cgi-3gpp', 'i-wlan-node-id',
                 'utran-sai-3gpp', 'gstn-location'):
            _parse_cell_id_value(v, tech, k, out)
        elif k in ('ue-ip', 'ue-ip-address', 'p-access-network-info-ue-ip'):
            # IP can be IPv4 or IPv6; keep raw, processor will validate
            out['ue_ip'] = v
        elif k == 'apn':
            out['apn'] = v

    return out


def _hex_to_dec(hex_str: str) -> str:
    """Convert a hex string to its decimal representation as a string.
    Returns the original string if conversion fails."""
    try:
        return str(int(hex_str, 16))
    except (ValueError, TypeError):
        return hex_str


def _parse_cell_id_value(v: str, tech: str, key: str, out: dict) -> None:
    """Parse a hex cell-id value depending on the technology and key.

    Formats (per RFC 7913 / 3GPP TS 24.229):
      utran-cell-id-3gpp  : MCC(3) + MNC(2|3) + LAC(4) + UC-ID(7)   = 16 or 17 hex
      utran-sai-3gpp      : MCC(3) + MNC(2|3) + LAC(4) + SAC(4)     = 13 or 14 hex
      eutran-cell-id-3gpp : MCC(3) + MNC(2|3) + TAC(4) + ECI(7)     = 16 or 17 hex
      cgi-3gpp (GERAN)    : MCC(3) + MNC(2|3) + LAC(4) + CI(4)      = 13 or 14 hex

    All hex location/cell identifiers (TAC, LAC, Cell-ID/ECI/SAC) are
    converted to **decimal strings** in the output, matching how operator
    tools and 3GPP TS 23.003 display them.  eNodeB-ID is already decimal.
    PLMN stays in MCC+MNC digit form (e.g. '61901').
    """
    if not v:
        return
    if not all(c in '0123456789abcdefABCDEF' for c in v):
        return
    L = len(v)
    if L < 13:
        return

    # Pick PLMN length by total hex length: 2-digit MNC vs 3-digit MNC
    candidates = []
    if L in (13, 16):           # 2-digit MNC
        candidates.append((5,))
    elif L in (14, 17):         # 3-digit MNC
        candidates.append((6,))
    else:
        candidates.extend([(5,), (6,)])

    for (plmn_len,) in candidates:
        plmn = v[:plmn_len]
        rest = v[plmn_len:]
        if not rest:
            continue
        out['serving_plmn'] = plmn

        if key == 'eutran-cell-id-3gpp' or tech == 'EUTRAN':
            # TAC(4) + ECI(7)
            if len(rest) >= 11:
                tac_hex = rest[:4]
                eci_hex = rest[4:11]
                out['tac']     = _hex_to_dec(tac_hex)
                out['cell_id'] = _hex_to_dec(eci_hex)
                try:
                    eci = int(eci_hex, 16)
                    out['enodeb_id'] = str(eci >> 8)
                except Exception:
                    pass
            return

        if key == 'utran-sai-3gpp':
            # LAC(4) + SAC(4) — SAC is the 3G Service-Area-Code (cell identifier)
            if len(rest) >= 8:
                out['lac']     = _hex_to_dec(rest[:4])
                out['cell_id'] = _hex_to_dec(rest[4:8])
            return

        if key == 'utran-cell-id-3gpp' or tech == 'UTRAN':
            # LAC(4) + UC-ID(4 or 7)
            if len(rest) >= 8:
                out['lac']     = _hex_to_dec(rest[:4])
                out['cell_id'] = _hex_to_dec(rest[4:])
            return

        if key == 'cgi-3gpp' or tech == 'GERAN':
            # LAC(4) + CI(4)
            if len(rest) >= 8:
                out['lac']     = _hex_to_dec(rest[:4])
                out['cell_id'] = _hex_to_dec(rest[4:8])
            return
        return


def _extract_uri_number(uri: str) -> str:
    """
    Extract the E.164 / MSISDN number from a SIP URI or tel URI.
    sip:+23276500000@domain  → +23276500000
    tel:+23276500000         → +23276500000
    sip:76500000@domain      → 76500000
    """
    if not uri:
        return ''
    uri = uri.strip()
    if uri.lower().startswith('tel:'):
        return uri[4:].split(';')[0].strip()
    if uri.lower().startswith('sip:') or uri.lower().startswith('sips:'):
        user_part = uri.split(':', 1)[1].split('@')[0]
        return user_part.strip()
    return uri


# ===========================================================================
# Top-level file scanner
# ===========================================================================

def find_ims_records(data: bytes) -> List[Tuple[bytes]]:
    """
    Scan binary data for IMS CDR records (tag 0xBF 0x45).
    Returns list of raw value bytes for each record found.
    """
    records = []
    offset = 0
    while offset < len(data) - 2:
        # Look for 0xBF 0x45 (2-byte tag)
        if data[offset] == 0xBF and data[offset + 1] == 0x45:
            tag_end = offset + 2
            length, len_bytes = _read_length(data, tag_end)
            body_start = tag_end + len_bytes
            body_end   = body_start + length
            if body_end <= len(data):
                records.append(data[body_start:body_end])
            offset = body_end if body_end > offset else offset + 1
        else:
            offset += 1
    return records


# ===========================================================================
# Per-field tag handlers
# ===========================================================================

# Single-byte context-specific tags (0x80 – 0xBF range, first byte only)
_SINGLE_TAGS = {
    0x80: 'record_type_raw',        # [0]  recordType
    0x81: 'retransmission',         # [1]  retransmission
    0x82: 'sip_method',             # [2]  SIP method (INVITE / MESSAGE / …)
    0x83: 'role_of_node_raw',       # [3]  roleOfNode
    0x84: 'node_address_raw',       # [4]  nodeAddress (primitive, short form)
    0x85: 'session_id',             # [5]  session-Id (Huawei: full SIP Call-ID)
    0x86: 'session_id_alt',         # [6]  alt session-Id (older builds)
    0x87: 'called_party_raw',       # [7]  called-Party-Address (primitive)
    0x88: 'cause_for_closing_raw',  # [8]  causeForRecordClosing (some versions)
    0x89: 'request_timestamp',      # [9]  serviceRequestTimeStamp
    0x8A: 'answer_timestamp',       # [10] serviceDeliveryStartTimeStamp
    0x8B: 'end_timestamp',          # [11] serviceDeliveryEndTimeStamp
    0x8C: 'record_open_time',       # [12] recordOpeningTime
    0x8D: 'record_close_time',      # [13] recordClosureTime
    0x8F: 'sequence_number',        # [15] localRecordSequenceNumber
    0x90: 'cause_for_closing_raw',  # [16] causeForRecordClosing (3GPP standard)
    0x91: 'cause_for_closing_raw',  # [17] causeForRecordClosing (Huawei ATS9900 build)
    0x92: 'icid',                   # [18] iMS-Charging-Identifier (older builds)
    0x93: 'icid',                   # [19] iMS-Charging-Identifier (Huawei ATS9900 prod)
    0x94: 'service_reason_code_raw', # [20] serviceReasonReturnCode (raw int)
    # 0x96 intentionally NOT mapped here — let _EXT_TAG_FIELDS[150]
    # handle it as 'online_charging_flag' for consistent mnemonic output.
    0x97: 'access_network_type',    # [23] accessNetworkInformation (short enum)
    0x98: 'event',                  # [24] event
    0x99: 'access_network_info',    # [25] accessNetworkInformation (string)
    0x9A: 'service_context_id',     # [26] serviceContextId
    0x9D: 'access_network_info',    # [29] P-Access-Network-Info (long form)
    0x9E: 'service_context_id',     # [30] serviceContextId (Huawei: ats9900@huawei.com)
}


def _handle_calling_party_list(value: bytes, rec: dict) -> None:
    """Parse list-Of-Calling-Party-Address (tag 0xA6).
    Sub-tags recognised:
      0x80 = sIP-URI            (sip:user@domain)
      0x81 = tEL-URI            (tel:+E164)
      0x82 = IMSI               (Huawei extension)
      0x83 = MIN  / canonical   (Huawei extension)
      0x84 = IMPI (private id)  (Huawei extension)
    Universal SEQUENCE (0x30) wrapping is unwrapped before iteration.
    """
    items = _parse_tlv_list(value)
    if len(items) == 1 and items[0][0] in (0x30, 0x31):
        items = _parse_tlv_list(items[0][1])
    for tag, val, _ in items:
        uri = _decode_string(val)
        if tag == 0x80:   # SIP-URI
            if not rec.get('calling_sip_uri'):
                rec['calling_sip_uri'] = uri
            if not rec.get('calling_number'):
                rec['calling_number'] = _extract_uri_number(uri)
        elif tag == 0x81:  # TEL-URI
            if not rec.get('calling_number'):
                rec['calling_number'] = _extract_uri_number(uri)
        elif tag == 0x82:  # IMSI
            rec['calling_imsi'] = uri
        elif tag == 0x83:  # MIN / canonical MSISDN
            rec['calling_min'] = uri
        elif tag == 0x84:  # IMPI
            rec['calling_impi'] = uri


def _handle_called_party(value: bytes, rec: dict) -> None:
    """Parse called-Party-Address (tag 0xA7 / 0xA8). Same sub-tag layout
    as the calling-party block (see _handle_calling_party_list)."""
    items = _parse_tlv_list(value)
    if len(items) == 1 and items[0][0] in (0x30, 0x31):
        items = _parse_tlv_list(items[0][1])
    for tag, val, _ in items:
        uri = _decode_string(val)
        if tag == 0x80:   # SIP-URI
            if not rec.get('called_sip_uri'):
                rec['called_sip_uri'] = uri
            if not rec.get('called_number'):
                rec['called_number'] = _extract_uri_number(uri)
        elif tag == 0x81:  # TEL-URI
            if not rec.get('called_number'):
                rec['called_number'] = _extract_uri_number(uri)
        elif tag == 0x82:  # IMSI
            rec['called_imsi'] = uri
        elif tag == 0x83:  # MIN / canonical MSISDN
            rec['called_min'] = uri
        elif tag == 0x84:  # IMPI
            rec['called_impi'] = uri


def _handle_subscription_id_list(value: bytes, rec: dict) -> None:
    """Parse list-of-subscription-ID.

    The list can hold ONE OR MORE entries, each being a SEQUENCE with:
      [0] subscription-Id-Type INTEGER  (END_USER_IMSI / E164 / SIP_URI / NAI / PRIVATE)
      [1] subscription-Id-Data UTF8String
    Some Huawei variants add:
      [2] role  (0 = served / 1 = calling / 2 = called)
    Each entry can be wrapped in a universal SEQUENCE (0x30) or appear directly.
    """
    items = _parse_tlv_list(value)
    # Wrap-detection: each subscription-ID entry can be wrapped in a
    # universal SEQUENCE (0x30) / SET (0x31), or in context constructed
    # tags (0xA0 / 0xA1 / 0xA2).  Unwrap one level before scanning sub-tags.
    entries = []
    if items and all(t in (0x30, 0x31, 0xA0, 0xA1, 0xA2) for t, _, _ in items):
        for _, sub, _ in items:
            entries.append(_parse_tlv_list(sub))
    else:
        entries.append(items)

    for entry in entries:
        id_type = None
        id_data = None
        role    = None
        for tag, val, _ in entry:
            if tag == 0x80:
                id_type = _decode_integer(val)
            elif tag == 0x81:
                id_data = _decode_string(val)
            elif tag == 0x82:
                role = _decode_integer(val)
        if id_type is None or not id_data:
            continue
        type_name = SUBSCRIPTION_ID_TYPE.get(id_type, '')
        # Map role to field prefix; default = served (the charged party)
        prefix = ''
        if role == 1:
            prefix = 'calling_'
        elif role == 2:
            prefix = 'called_'

        if type_name == 'END_USER_IMSI':
            key = f'{prefix}imsi'
            if not rec.get(key):
                rec[key] = id_data
            # Also set served imsi when no role given
            if not prefix and not rec.get('imsi'):
                rec['imsi'] = id_data
        elif type_name == 'END_USER_E164':
            if prefix:
                key = f'{prefix}min'
                if not rec.get(key):
                    rec[key] = _extract_uri_number(id_data)
            elif not rec.get('msisdn'):
                rec['msisdn'] = _extract_uri_number(id_data)
        elif type_name == 'END_USER_SIP_URI':
            num = _extract_uri_number(id_data)
            if prefix:
                key = f'{prefix}impi'
                if not rec.get(key):
                    rec[key] = id_data
                if num and not rec.get(f'{prefix}min'):
                    rec[f'{prefix}min'] = num
            else:
                if not rec.get('private_user_identity'):
                    rec['private_user_identity'] = id_data
                if num and not rec.get('msisdn'):
                    rec['msisdn'] = num
        elif type_name in ('END_USER_NAI', 'END_USER_PRIVATE'):
            if not rec.get('private_user_identity'):
                rec['private_user_identity'] = id_data


def _handle_ioi(value: bytes, rec: dict) -> None:
    """Parse interOperatorIdentifiers.

    The outer value may be wrapped in a universal SEQUENCE (0x30) before the
    [0]/[1] originating/terminating IOI strings — unwrap if necessary.
    """
    items = _parse_tlv_list(value)
    # Unwrap universal SEQUENCE or SET if that is the only top-level element
    if len(items) == 1 and items[0][0] in (0x30, 0x31):
        items = _parse_tlv_list(items[0][1])
    for tag, val, _ in items:
        s = _decode_string(val)
        if tag == 0x80:
            if not rec.get('originating_ioi'):
                rec['originating_ioi'] = s
        elif tag == 0x81:
            if not rec.get('terminating_ioi'):
                rec['terminating_ioi'] = s


def _handle_charged_party(value: bytes, rec: dict) -> None:
    """Parse charged-Party CHOICE (SIP URI or tel URI)."""
    for tag, val, _ in _parse_tlv_list(value):
        uri = _decode_string(val)
        if tag in (0x80, 0x81):
            rec['charged_party'] = _extract_uri_number(uri) or uri


def _handle_dialed_party(value: bytes, rec: dict) -> None:
    """Parse dialed-Party-Address (tag 0xBF 81 4B).

    Sets both:
      dialed_number          — stripped (digits / bare number)
      dialled_party_address  — full URI form (e.g. 'tel:079505419')
    """
    for tag, val, _ in _parse_tlv_list(value):
        uri = _decode_string(val)
        if tag in (0x80, 0x81):
            if not rec.get('dialled_party_address'):
                rec['dialled_party_address'] = uri
            if not rec.get('dialed_number'):
                rec['dialed_number'] = _extract_uri_number(uri) or uri


def _handle_called_asserted(value: bytes, rec: dict) -> None:
    """Parse list-Of-Called-Asserted-Identity.

    Sets both:
      called_asserted_identity      — stripped (bare digits)
      called_asserted_identity_raw  — raw URI form (keeps 'tel:' prefix)
    """
    items = _parse_tlv_list(value)
    if len(items) == 1 and items[0][0] in (0x30, 0x31):
        items = _parse_tlv_list(items[0][1])
    for tag, val, _ in items:
        uri = _decode_string(val)
        if tag in (0x80, 0x81) and uri:
            if not rec.get('called_asserted_identity_raw'):
                rec['called_asserted_identity_raw'] = uri
            if not rec.get('called_asserted_identity'):
                rec['called_asserted_identity'] = _extract_uri_number(uri) or uri


def _handle_private_user_equipment_info(value: bytes, rec: dict) -> None:
    """Parse Private-User-Equipment-Info.

    Layout:
      [0] Private-User-Equipment-Info-Type    INTEGER  (0=IMEI, 1=ESN, 2=MEID, ...)
      [1] Private-User-Equipment-Info-Value   GraphicString
    Sets:
      private_user_equipment_info_type = 'IMEI' / 'ESN' / …
      private_user_equipment_info_value = raw value (e.g. '35125339-616639-0')
      imei = digits-only when type is IMEI
    """
    info_type = None
    info_val  = ''
    for tag, val, _ in _parse_tlv_list(value):
        if tag == 0x80:
            info_type = _decode_integer(val)
        elif tag == 0x81:
            info_val = _decode_string(val)
    if info_val:
        type_name = PRIVATE_USER_EQUIPMENT_TYPE.get(info_type, 'IMEI' if info_type is None else str(info_type))
        rec['private_user_equipment_info_type']  = type_name
        rec['private_user_equipment_info_value'] = info_val
        if type_name == 'IMEI' and not rec.get('imei'):
            rec['imei'] = ''.join(c for c in info_val if c.isdigit())[:20]


def _handle_list_of_in_information(value: bytes, rec: dict) -> None:
    """Parse List-Of-IN-Information (CAMEL/IN trigger details).

    Per Huawei ATS9900, each entry of the SEQUENCE-OF holds (in order):
      [0] Service-Key                     INTEGER
      [1] Call-Reference-Number           OCTET STRING (hex)
      [2] Fci-Free-Format-Data
      [3] Fci-Free-Format-Data-Manner
      [4] Default-Call-Handling
      [5] Scf-Address                     GraphicString (E.164)
      [10] IN-Bypass                      INTEGER

    Captures the first entry only.
    """
    items = _parse_tlv_list(value)
    # Iterate first entry only — entry can be wrapped in 0x30 / 0xA0 / 0xA1
    for outer_tag, outer_val, _ in items:
        if outer_tag not in (0x30, 0xA0, 0xA1, 0xA2):
            continue
        for tag, val, _ in _parse_tlv_list(outer_val):
            s = _decode_string(val).strip()
            if tag == 0x80 and not rec.get('in_service_key'):
                rec['in_service_key'] = str(_decode_integer(val))
            elif tag == 0x81 and not rec.get('in_call_reference'):
                # Some builds encode the call-ref as raw bytes — hex-encode
                if all(32 <= b < 127 for b in val):
                    rec['in_call_reference'] = s
                else:
                    rec['in_call_reference'] = val.hex().upper()
            elif tag == 0x84 and not rec.get('default_call_handling'):
                rec['default_call_handling'] = s
            elif tag in (0x85, 0x86) and not rec.get('in_scf_address'):
                # SCF-Address is BCD-encoded in Huawei IN payload — try
                # both ASCII (defensive) and BCD-pack decoding.
                rec['in_scf_address'] = _decode_telephony_address(val)
            elif tag in (0x8A, 0x89) and not rec.get('in_bypass'):
                rec['in_bypass'] = str(_decode_integer(val))
        break  # only take the first IN entry


def _handle_incomplete_cdr_indication(value: bytes, rec: dict) -> None:
    """Parse Incomplete-CDR-Indication (constructed [18]).

    Sub-tags:
      [0] aCRStartLost    INTEGER (0=FALSE, 1=TRUE)
      [1] aCRInterimLost  INTEGER (0=NO, 1=YES, 2=PARTIAL)
      [2] aCRStopLost     INTEGER (0=FALSE, 1=TRUE)
    Serialises to the Huawei display form:
      'aCRStartLost:FALSE aCRInterimLost:NO aCRStopLost:FALSE'
    """
    start_lost  = 'FALSE'
    interim_lst = 'NO'
    stop_lost   = 'FALSE'
    for tag, val, _ in _parse_tlv_list(value):
        v = _decode_integer(val)
        if tag == 0x80:
            start_lost = 'TRUE' if v else 'FALSE'
        elif tag == 0x81:
            interim_lst = {0: 'NO', 1: 'YES', 2: 'PARTIAL'}.get(v, str(v))
        elif tag == 0x82:
            stop_lost = 'TRUE' if v else 'FALSE'
    rec['incomplete_cdr_indication'] = (
        f'aCRStartLost:{start_lost} aCRInterimLost:{interim_lst} aCRStopLost:{stop_lost}'
    )


def _handle_list_of_sdp_media_components(value: bytes, rec: dict) -> None:
    """Capture SDP info as a compact text dump (codec / port / direction)."""
    txt = _decode_string(value)
    # Strip non-printable
    txt = ''.join(c for c in txt if c.isprintable())
    if txt and not rec.get('sdp_media_components'):
        rec['sdp_media_components'] = txt[:1000]
    # Try to spot the codec for SDP-Media-Identifier
    low = txt.lower()
    if 'amr-wb' in low or 'audio' in low:
        rec.setdefault('sdp_media_identifier', 'VOICECALL')
    elif 'video' in low or 'h264' in low or 'vp8' in low:
        rec.setdefault('sdp_media_identifier', 'VIDEOCALL')


def _handle_mmtel(value: bytes, rec: dict) -> None:
    """Parse mMTelInformation.

    Sub-tags:
      0x80 = subscriberRole          (0 = ORIG, 1 = TERM)
      0x81 = listOfSupplServices     SEQUENCE OF (each item with [0]=service-id)
      0x82 = forwardingInformation   SEQUENCE
               [0] = forwarded-to-number   (tel:/sip:)
               [1] = redirecting-number    (original B)
               [2] = diversion-reason
               [3] = diversion-count
    """
    def _extract_service_ids(sub_value: bytes) -> list:
        names = []
        for stag, sval, _ in _parse_tlv_list(sub_value):
            if stag == 0x80:
                sid = _decode_integer(sval)
                names.append(MMTEL_SUPPLEMENTARY_SERVICE.get(sid, str(sid)))
            elif stag in (0x30, 0x31, 0xA0, 0xA1, 0xA2):
                # Each supplementary service entry is itself a SEQUENCE/SET
                # whose first element is the service-id
                for stag2, sval2, _ in _parse_tlv_list(sval):
                    if stag2 == 0x80:
                        sid = _decode_integer(sval2)
                        names.append(MMTEL_SUPPLEMENTARY_SERVICE.get(sid, str(sid)))
                        break
        return names

    for tag, val, _ in _parse_tlv_list(value):
        # Two known Huawei layouts:
        #   Standard 3GPP : [0] role, [1] supplementaryServices
        #   ATS9900 prod : [0] supplementaryServices, [1] role
        if tag == 0x80 and len(val) <= 2:
            # Treat short primitive as role
            role_val = _decode_integer(val)
            role_str = 'ORIGINATING' if role_val == 0 else 'TERMINATING'
            rec.setdefault('mmtel_role', role_str)
            rec.setdefault('subscriber_role', role_str)
        elif tag == 0x81 and len(val) <= 2:
            # Short primitive at [1] — Huawei ATS9900 role
            role_val = _decode_integer(val)
            role_str = 'ORIGINATING' if role_val == 0 else 'TERMINATING'
            rec.setdefault('mmtel_role', role_str)
            rec.setdefault('subscriber_role', role_str)
        elif tag in (0xA0, 0xA1, 0x81):
            # Constructed list of supplementary services (or primitive list)
            names = _extract_service_ids(val)
            if names and not rec.get('supplementary_service'):
                rec['supplementary_service'] = ','.join(names)
        elif tag in (0xA2, 0x82):   # forwardingInformation
            for ftag, fval, _ in _parse_tlv_list(val):
                if ftag in (0x80, 0xA0):
                    fwd = _extract_uri_number(_decode_string(fval)) or _decode_string(fval)
                    if fwd and not rec.get('forwarded_number'):
                        rec['forwarded_number'] = fwd
                elif ftag in (0x81, 0xA1):
                    red = _extract_uri_number(_decode_string(fval)) or _decode_string(fval)
                    if red and not rec.get('redirecting_number'):
                        rec['redirecting_number'] = red
                elif ftag == 0x82:
                    rec['diversion_reason'] = _decode_string(fval)
                elif ftag == 0x83:
                    rec['diversion_count'] = _decode_integer(fval)


# Multi-byte extended tag numbers (after initial 0x9F or 0xBF bytes):
# These are (first_byte, second_byte) pairs identifying the full tag.
# We use the decoded tag_number from _read_tag().

# Extended tag number → field name mapping.
# Tag numbers in ASN.1 BER multi-byte encoding for 0x9F NN or 0xBF NN:
# 0x9F 0x81 0x48 → tag_num = 0x81*128 + 0x48 & 0x7F = depends on BER encoding.
# Actual values decoded by _read_tag():
#   0x9F 0x01 = tag 1 in context class primitive  etc.
# The multi-byte tags from the spec use the high-tag format:
#   First byte: 0x9F (primitive) or 0xBF (constructed) means tag >= 31 follows.
#   Then each continuation byte has bit7 = 1 if more, bit7=0 for last.
#   Value = concatenation of low 7 bits.
# Examples from the document:
#   0x9F 81 48 → 0x81=10000001, 0x48=01001000 → (1&0x7F)<<7 | (0x48&0x7F) = 128+72 = 200
#   0x9F 81 58 → (1<<7)|0x58 = 128+88 = 216
#   0x9F 81 16 → (1<<7)|0x16 = 128+22 = 150
#   0x9F 81 17 → (1<<7)|0x17 = 151  [charged-Party]
#   0x9F 81 18 → 152  [call-description]
#   0x9F 81 19 → 153  [group-number]
#   0x9F 81 1A → 154  [short-number]
#   0x9F 81 4B → context tag for dialed-Party (constructed 0xBF 81 4B = same value)
#   0x9F 81 4C → 196  [ringing-Duration]
#   0x9F 81 4E → 198  [carrier-Identification-Code]
#   0x9F 81 53 → 203  [np-Route-Number]
#   0x9F 81 55 → 205  [serviceIdentifier constructed]
#   0x9F 81 56 → 206  [diversionreason]
#   0x9F 81 57 → 207  [diversion-Count]
#   0x9F 81 58 → 216  [chargingCategory]
#   0x9F 81 59 → 217  [serverUserType]
#   0x9F 81 5A → 218  [privateNetworkIndication]
#   0x9F 81 5C → 220  [related-call-reference]
#   0x9F 81 5E → 222  [call-property]
#   0x9F 81 62 → 226  [tariffPulses]
#   0x9F 81 66 → 230  [groupID]
#   0x9F 81 67 → 231  [privateNumber]
#   0x9F 81 69 → 233  [sDP-Media-Identifier]
#   0x9F 81 6B → 235  [msc-number]
#   0x9F 81 6C → 236  [vlr-number]
#   0x9F 81 7A → 250  [first-level-bill-group]
#   0x9F 81 7B → 251  [second-level-bill-group]

_EXT_TAG_FIELDS = {
    # Multi-byte tag math: (b1 & 0x7F) << 7 | (b2 & 0x7F)
    # i.e. 0x9F 81 XX → 128 + XX, 0x9F 83 XX → 384 + XX
    150: 'online_charging_flag',          # 0x9F 81 16  (128+22)
    # 151 = charged-Party (constructed) — handled separately
    152: 'call_description',              # 0x9F 81 18
    153: 'group_number',                  # 0x9F 81 19
    154: 'short_number',                  # 0x9F 81 1A
    160: 'accounting_record_type',        # 0x9F 81 20
    161: 'tads_indication',               # 0x9F 81 21
    162: 'is_volte_call_type',            # 0x9F 81 22
    163: 'requested_party_address',       # 0x9F 81 23
    164: 'connected_number',              # 0x9F 81 24
    165: 'network_call_reference',        # 0x9F 81 25
    166: 'visited_network_id',            # 0x9F 81 26
    167: 'province',                      # 0x9F 81 27
    168: 'roam_type',                     # 0x9F 81 28
    169: 'user_agent_value',              # 0x9F 81 29
    170: 'sip_from_uri',                  # 0x9F 81 2A
    171: 'incomplete_cdr_indication',     # 0x9F 81 2B
    172: 'additional_calling_party',      # 0x9F 81 2C
    173: 'additional_called_party',       # 0x9F 81 2D
    200: 'duration_raw',                  # 0x9F 81 48  (128+72)
    # 203 = dialed-Party-Address (constructed BF 81 4B) — handled separately
    204: 'ringing_duration_raw',          # 0x9F 81 4C  (was 196 — wrong)
    206: 'carrier_code',                  # 0x9F 81 4E  (was 198 — wrong)
    211: 'np_routing_number',             # 0x9F 81 53  (was 203 — wrong)
    214: 'diversion_reason',              # 0x9F 81 56  (was 206 — wrong)
    215: 'diversion_count_raw',           # 0x9F 81 57  (was 207 — wrong)
    216: 'charging_category_raw',         # 0x9F 81 58
    217: 'served_subscriber_type',        # 0x9F 81 59
    218: 'private_network_indication',    # 0x9F 81 5A
    219: 'media_type',                    # 0x9F 81 5B
    220: 'related_call_reference',        # 0x9F 81 5C
    222: 'call_property_raw',             # 0x9F 81 5E
    226: 'tariff_pulses',                 # 0x9F 81 62
    230: 'group_id',                      # 0x9F 81 66
    231: 'private_number',                # 0x9F 81 67
    233: 'sdp_media_identifier',          # 0x9F 81 69
    235: 'msc_number',                    # 0x9F 81 6B
    236: 'vlr_number',                    # 0x9F 81 6C
    237: 'forwarded_number',              # 0x9F 81 6D
    238: 'apn',                           # 0x9F 81 6E
    239: 'redirecting_number',            # 0x9F 81 6F
    250: 'first_level_bill_group',        # 0x9F 81 7A
    251: 'second_level_bill_group',       # 0x9F 81 7B
    # 0x9F 83 XX range (3-byte multi-byte tags, observed in Huawei VoLTE)
    410: 'ims_3gpp_online_charging_raw',  # 0x9F 83 1A  (384+26)
}

# Constructed extended tags needing special handling
# Tag numbers computed as: (b1 & 0x7F) << 7 | (b2 & 0x7F)  for 0xBF 8X YY
#                        : (b1 & 0x7F) << 7 | (b2 & 0x7F)  for 0xBF 8X YY (3-byte 0xBF 83 XX → 384+XX)
_EXT_CONSTRUCTED = {
    151: '_charged_party',                     # 0xBF 81 17  (128+23)
    203: '_dialed_party',                      # 0xBF 81 4B  (128+75)
    # 0xBF 83 XX range — production Huawei VoLTE CDR
    410: '_ims_3gpp_online_charging_flag',     # 0xBF 83 1A  (sometimes constructed)
    413: '_list_of_in_information',            # 0xBF 83 1D  (384+29)
    418: '_private_user_equipment_info',       # 0xBF 83 22  (384+34)
}

# chargingCategory enum — Huawei ATS9900 mnemonics (matches CLI display)
_CHARGING_CATEGORY = {
    0: 'CHARGE_NORMAL',
    1: 'CHARGE_PREPAID',
    2: 'CHARGE_HOT_BILLING',
    3: 'CHARGE_FLAT_RATE',
    4: 'CHARGE_FREE',
    5: 'CHARGE_POSTPAID',
}

# call-property enum — Huawei ATS9900 mnemonics
_CALL_PROPERTY = {
    0: 'Unknown-Call',
    1: 'Local-Call',
    2: 'National-Call',
    3: 'International-Call',
    4: 'Emergency-Call',
}


# ===========================================================================
# Main record decoder
# ===========================================================================

def decode_ims_record(body: bytes) -> dict:
    """
    Decode the body bytes of one ATS9900 IMS CDR record into a flat dict.
    """
    rec = {}
    calling_list_raw = None

    for tag_num, value, is_constructed in _parse_tlv_list(body):

        # ---- Single-byte simple tags ----------------------------------------
        if tag_num in _SINGLE_TAGS:
            field = _SINGLE_TAGS[tag_num]
            if field in ('request_timestamp', 'answer_timestamp', 'end_timestamp',
                         'record_open_time', 'record_close_time'):
                rec[field] = _decode_timestamp(value)
            elif field in ('record_type_raw', 'role_of_node_raw',
                           'cause_for_closing_raw', 'sequence_number'):
                rec[field] = _decode_integer(value)
            elif field == 'retransmission':
                rec[field] = bool(value and value[0])
            else:
                rec[field] = _decode_string(value)

        # ---- Constructed sequence tags (context-specific) -------------------
        # NOTE: _read_tag now returns the full first byte for single-byte tags,
        # so comparisons use the raw byte value (0xA6, 0xA7, 0xAE …).

        elif tag_num == 0xA4:  # nodeAddress (ctx constructed [4])
            # Inner: 0x81 or 0x80 = FQDN string
            for stag, sval, _ in _parse_tlv_list(value):
                if stag in (0x80, 0x81):
                    rec['node_address_raw'] = _decode_string(sval)
                    break

        elif tag_num in (0xA6, 0x26, 0x27):  # list-Of-Calling-Party-Address
            # 0xA6 = context constructed [6]   (Huawei ATS9900)
            # 0x26 / 0x27 = universal constructed variants seen in some 3GPP stacks
            _handle_calling_party_list(value, rec)

        elif tag_num in (0xA7, 0xA8, 0x27, 0x28):  # called-Party-Address
            # 0xA7 = context constructed [7]   (Huawei ATS9900)
            # 0xA8 = context constructed [8]   (alternative encoding)
            _handle_called_party(value, rec)

        elif tag_num in (0xAE, 0x2E):  # interOperatorIdentifiers (ctx [14] / universal)
            _handle_ioi(value, rec)

        elif tag_num in (0xBB, 0x3B, 0xB3, 0xB1, 0xB2, 31):  # list-of-subscription-ID
            # 31    = multi-byte BF 1F (Huawei ATS9900 production — observed in real CDR)
            # 0xB1  = context [49] constructed (3GPP TS 32.298 IMS CDR canonical)
            # 0xB2  = context [50] constructed (Huawei variant)
            # 0xB3  = context [51] constructed (alt)
            # 0xBB  = context [59] constructed (Huawei ATS9900 default — older builds)
            # 0x3B  = universal SEQUENCE-OF tag (rare)
            _handle_subscription_id_list(value, rec)

        elif tag_num == 0xB5:   # list-Of-SDP-Media-Components (Huawei single-byte)
            _handle_list_of_sdp_media_components(value, rec)

        elif tag_num == 0x6E:   # MMTel-Information (Huawei single-byte private)
            _handle_mmtel(value, rec)

        elif tag_num == 0xB2:   # Incomplete-CDR-Indication (Huawei constructed)
            _handle_incomplete_cdr_indication(value, rec)

        elif tag_num in (0xB9, 0x39):  # mMTelInformation (ctx [57] / universal)
            _handle_mmtel(value, rec)

        elif tag_num in (0xB3, 0x33):  # called-asserted-identity list
            _handle_called_asserted(value, rec)

        # ---- Extended multi-byte tags (tag_num >= 128) ----------------------
        elif tag_num in _EXT_TAG_FIELDS:
            field = _EXT_TAG_FIELDS[tag_num]
            if field == 'duration_raw':
                rec['duration_raw'] = _decode_integer(value)
            elif field == 'ringing_duration_raw':
                rec['ringing_duration_raw'] = _decode_integer(value)
            elif field == 'diversion_count_raw':
                rec['diversion_count'] = _decode_integer(value)
            elif field == 'online_charging_flag':
                rec['online_charging_flag'] = str(_decode_integer(value))
            elif field == 'charging_category_raw':
                raw_val = _decode_integer(value)
                rec['charging_category'] = _CHARGING_CATEGORY.get(raw_val, str(raw_val))
            elif field == 'call_property_raw':
                raw_val = _decode_integer(value)
                rec['call_property'] = _CALL_PROPERTY.get(raw_val, str(raw_val))
            elif field in ('msc_number', 'vlr_number', 'carrier_code',
                           'np_routing_number', 'group_number', 'private_number',
                           'related_call_reference', 'first_level_bill_group',
                           'second_level_bill_group', 'group_id', 'diversion_reason',
                           'imei', 'media_type', 'served_subscriber_type',
                           'apn'):
                rec[field] = _decode_string(value)
            elif field == 'sdp_media_identifier':
                # Huawei encodes this as an enum integer:
                #   0 = VOICECALL  1 = VIDEOCALL  2 = MESSAGE  3 = FAX
                # Only override the SDP-parser-derived value when the
                # enum is set to something meaningful.
                _SDP_ID = {0: 'VOICECALL', 1: 'VIDEOCALL', 2: 'MESSAGE', 3: 'FAX'}
                int_val = _decode_integer(value)
                mnemonic = _SDP_ID.get(int_val)
                if mnemonic and not rec.get('sdp_media_identifier'):
                    rec['sdp_media_identifier'] = mnemonic
                # else: leave whatever the SDP parser already set
            elif field in ('forwarded_number', 'redirecting_number',
                           'connected_number',
                           'additional_calling_party', 'additional_called_party',
                           'sip_from_uri'):
                # These often arrive as tel:/sip: URIs — normalise to bare digits
                s = _decode_string(value)
                rec[field] = _extract_uri_number(s) or s
            elif field == 'requested_party_address':
                # Store BOTH raw URI (with tel:) and stripped form
                s = _decode_string(value)
                rec['requested_party_address_raw'] = s
                rec[field] = _extract_uri_number(s) or s
            else:
                rec[field] = _decode_integer(value) if len(value) <= 4 else _decode_string(value)

        elif tag_num in _EXT_CONSTRUCTED:
            handler = _EXT_CONSTRUCTED[tag_num]
            if handler == '_charged_party':
                _handle_charged_party(value, rec)
            elif handler == '_dialed_party':
                _handle_dialed_party(value, rec)
            elif handler == '_private_user_equipment_info':
                _handle_private_user_equipment_info(value, rec)
            elif handler == '_list_of_in_information':
                _handle_list_of_in_information(value, rec)
            elif handler == '_ims_3gpp_online_charging_flag':
                # Some builds wrap the boolean flag in a constructed tag
                for st, sv, _c in _parse_tlv_list(value):
                    rec['ims_3gpp_online_charging_raw'] = _decode_integer(sv)
                    break

    # -------------------------------------------------------------------------
    # Post-process accumulated fields
    # -------------------------------------------------------------------------
    rec['record_type'] = _map_record_type(rec.get('record_type_raw'))
    rec['role_of_node'] = ROLE_OF_NODE.get(rec.get('role_of_node_raw', -1), '')
    rec['cause_for_closing'] = CAUSE_FOR_CLOSING.get(rec.get('cause_for_closing_raw', -1), '')
    rec['duration'] = rec.get('duration_raw', 0)
    rec['ringing_duration'] = rec.get('ringing_duration_raw', 0)

    # Service-Reason-Return-Code: just the integer (user spec)
    if 'service_reason_code_raw' in rec:
        try:
            rec['service_reason_code'] = str(int(rec['service_reason_code_raw']))
        except (TypeError, ValueError):
            pass

    # Online-Charging-Flag mnemonic: 0 → Offline_Charging, 1 → Online_Charging
    ocf = rec.get('online_charging_flag', '')
    if ocf in ('0', '1', 0, 1):
        rec['online_charging_flag'] = ONLINE_CHARGING_FLAG.get(int(ocf), str(ocf))

    # SDP parser — extract codec / port / payloads from sdp_media_components
    sdp_txt = rec.get('sdp_media_components', '')
    if sdp_txt:
        sdp_info = parse_sdp_block(sdp_txt)
        for k, v in sdp_info.items():
            if v and not rec.get(k):
                rec[k] = v

    # Access-Network-Spec: first token of access_network_info (e.g. '3GPP-E-UTRAN')
    ani = rec.get('access_network_info', '')
    if ani and not rec.get('access_network_spec'):
        rec['access_network_spec'] = ani.split(';')[0].strip().strip('"')

    # Start time: prefer answer time (200 OK), fall back to request time (INVITE)
    if not rec.get('start_time'):
        rec['start_time'] = rec.get('answer_timestamp') or rec.get('request_timestamp')

    # Use MSISDN as calling_number when calling_number is a full SIP URI without digits
    if rec.get('msisdn') and not rec.get('calling_number'):
        rec['calling_number'] = rec['msisdn']

    # Dialed number default to called
    if not rec.get('dialed_number'):
        rec['dialed_number'] = rec.get('called_number', '')

    # ---- Derive access-network info (technology, cell, TAC, PLMN, UE IP) ----
    ani = rec.get('access_network_info', '')
    if ani:
        parsed = parse_access_network_info(ani)
        for k, v in parsed.items():
            if not rec.get(k):
                rec[k] = v

    # ---- APN: when explicit APN tag is present (Huawei extension 0x9F 81 6E) -
    # We expose the field so downstream code (e.g. correlation) can use it.
    if not rec.get('apn'):
        # If service_context_id smells like a SIP-AS context, leave empty.
        # If it's an APN-NI like "ims" / "internet", surface it.
        svc_ctx = (rec.get('service_context_id') or '').lower()
        if svc_ctx in ('ims', 'internet', 'mms', 'wap'):
            rec['apn'] = svc_ctx
        elif 'ims' in svc_ctx and '@' not in svc_ctx:
            rec['apn'] = 'ims'

    return rec


def _map_record_type(raw: Optional[int]) -> str:
    """Map 3GPP IMS record type integer to a readable string.

    Values 63-70 are defined in 3GPP TS 32.298 §5.1.2.
    Huawei ATS9900 AS-CDRs use type 69 (aSRecord).
    """
    if raw is None:
        return 'ATS_CDR'
    type_map = {
        # Generic / Huawei internal
        0: 'ATS_CDR',
        1: 'EVENT_CDR',
        2: 'START_CDR',
        3: 'INTERIM_CDR',
        4: 'STOP_CDR',
        # 3GPP TS 32.298 IMS record types
        60: 'IMS_CDR',
        63: 'SCSCF_CDR',
        64: 'PCSCF_CDR',
        65: 'ICSCF_CDR',
        66: 'MRFC_CDR',
        67: 'MGCF_CDR',
        68: 'BGCF_CDR',
        69: 'AS_CDR',       # Application Server (ATS9900)
        70: 'ECSCF_CDR',
        71: 'IBCF_CDR',
        72: 'TRF_CDR',
        73: 'TF_CDR',
        74: 'ATCF_CDR',
    }
    return type_map.get(raw, f'IMS_CDR_{raw}')


# ===========================================================================
# Public API
# ===========================================================================

def decode_file(file_path: str) -> Tuple[List[dict], dict]:
    """
    Decode all IMS CDR records from a binary file.

    Returns:
        (records, stats) where records is a list of decoded dicts
        and stats has counts by record_type.
    """
    stats = {'total': 0, 'voice': 0, 'event': 0, 'errors': 0}
    records = []

    if not os.path.exists(file_path):
        logger.error(f'IMS decoder: file not found: {file_path}')
        return records, stats

    try:
        with open(file_path, 'rb') as f:
            data = f.read()
    except Exception as e:
        logger.error(f'IMS decoder: cannot read {file_path}: {e}')
        return records, stats

    raw_records = find_ims_records(data)
    stats['total'] = len(raw_records)

    for i, body in enumerate(raw_records, start=1):
        try:
            rec = decode_ims_record(body)
            rec['_seq'] = i
            rec['_source_file'] = os.path.basename(file_path)
            records.append(rec)
            rt = rec.get('record_type', '')
            if 'EVENT' in rt:
                stats['event'] += 1
            else:
                stats['voice'] += 1
        except Exception as e:
            stats['errors'] += 1
            logger.debug(f'IMS record {i} decode error: {e}')

    logger.info(f'IMS decoder: {file_path} → {len(records)} records '
                f'(voice={stats["voice"]} event={stats["event"]} errors={stats["errors"]})')
    return records, stats


def is_ims_file(file_path: str) -> bool:
    """Quick check: does the file contain at least one IMS record tag (0xBF 0x45)?"""
    try:
        with open(file_path, 'rb') as f:
            data = f.read(65536)  # check first 64 KB
        return b'\xbf\x45' in data
    except Exception:
        return False
