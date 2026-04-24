"""
Huawei SGSN CDR Decoder
========================
Decodes SGSN (Serving GPRS Support Node) CDR files from Huawei equipment.
Supports S-CDR (PDP data sessions) per 3GPP TS 32.298.

SGSN handles 2G/3G packet-switched DATA sessions only.
SMS records are handled by the MSC stream — NOT SGSN.

Record Types:
- 18 (0x12): sgsnPDPRecord  — S-CDR (2G/3G GPRS data session)

ASN.1 BER outer record tag (context-specific, constructed):
- Type 18: 0xB2 (single-byte: 1011 0010)

Note: 0xB4 is the diagnostics [20] field tag INSIDE S-CDR records.
      It must NOT be used as an outer record marker.

Author: B-TEC Digital Solution Ltd
Version: 1.1
"""

import struct
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Orange Sierra Leone constants (for prepaid/postpaid classification)
POSTPAID_IMSI_PREFIX = '6190176100'
OSL_PLMNI = '61901'


class SGSNDecoder:
    """Decoder for Huawei SGSN CDR files (ASN.1 BER encoded)."""

    # Outer record type byte values
    RECORD_TYPE_PDP = 18    # sGSNPDPRecord (data session)

    # Outer tag bytes.
    # Huawei SGSN CDR files use 0xB4 (CONTEXT CONSTRUCTED tag 20) as the outer
    # record wrapper for sGSNPDPRecord, even though the *inner* recordType field
    # confirms type 18 (data session).  This is a vendor-specific encoding choice.
    # Both 0xB2 and 0xB4 are mapped to RECORD_TYPE_PDP — always DATA, never SMS.
    #
    # Note: 0xB4 also appears as the diagnostics [20] INNER field inside a record,
    # but since the outer scanner advances by rec_len after each outer record it
    # never re-enters the inner content.
    OUTER_TAGS = {
        0xB2: RECORD_TYPE_PDP,   # standard 3GPP outer tag for sGSNPDPRecord
        0xB4: RECORD_TYPE_PDP,   # Huawei variant outer tag (inner type byte = 18)
    }

    # RAT Type values
    RAT_TYPES = {
        0: 'Unknown',
        1: 'UTRAN',    # 3G
        2: 'GERAN',    # 2G
        3: 'WLAN',
        6: 'EUTRAN',   # 4G (should not appear in SGSN, but handle gracefully)
    }

    # PDP Type: (org_byte, type_byte) → name
    # org_byte: 0xF1 = IETF, 0x01 = ETSI/X.25
    # Huawei encodes pdpType as 2 bytes: [org][type], e.g. [0xF1][0x21] = IPv4
    PDP_TYPE_MAP = {
        (0xF1, 0x21): 'IPv4',
        (0xF1, 0x57): 'IPv6',
        (0xF1, 0x8D): 'IPv4v6',
        (0xF1, 0x02): 'PPP',
        (0x01, 0x01): 'X.25',
        (0x01, 0x02): 'PPP',
        # Legacy flat lookups (fallback)
        (0x00, 0x21): 'IPv4',
        (0x00, 0x57): 'IPv6',
    }

    # Cause for Record Closing (3GPP TS 32.298)
    CAUSE_FOR_CLOSING = {
        0:  'normalRelease',
        1:  'partialRecord',
        2:  'partialRecordMTCDT',
        4:  'abnormalRelease',
        16: 'volumeLimit',
        17: 'timeLimit',
        18: 'servingNodeChange',
        19: 'maxChangeCond',
        20: 'managementIntervention',
        21: 'intraSGSNIntersystemChange',
        22: 'rATChange',
        23: 'mSTimeZoneChange',
        24: 'sGSNPLMNIDChange',
    }

    def __init__(self):
        self.records = []
        self.file_info = {}
        self.errors = []

    def decode_file(self, filepath: str) -> List[Dict]:
        """
        Decode an SGSN CDR file and return list of parsed records.

        Args:
            filepath: Path to the SGSN CDR binary file.

        Returns:
            List of dicts containing decoded CDR fields.
        """
        self.records = []
        self.errors = []

        try:
            with open(filepath, 'rb') as f:
                data = f.read()

            self.file_info = {
                'filename': filepath,
                'file_size': len(data),
                'decode_time': datetime.now().isoformat(),
            }

            pos = 0
            record_num = 0
            found_tags = set()

            while pos < len(data) - 1:
                tag_byte = data[pos]

                # Check for known SGSN outer record tags
                if tag_byte in self.OUTER_TAGS:
                    found_tags.add(hex(tag_byte))
                    record_type = self.OUTER_TAGS[tag_byte]
                    pos += 1

                    # Parse length
                    rec_len, len_bytes = self._parse_length(data, pos)
                    if rec_len == 0 or pos + len_bytes + rec_len > len(data):
                        pos += len_bytes
                        continue

                    pos += len_bytes
                    rec_data = data[pos:pos + rec_len]

                    try:
                        record = self._decode_record(rec_data, record_num, record_type)
                        self.records.append(record)
                    except Exception as e:
                        self.errors.append({
                            'record_num': record_num,
                            'position': pos,
                            'error': str(e),
                        })
                        logger.error(f"Error decoding SGSN record {record_num} at pos {pos}: {e}")

                    pos += rec_len
                    record_num += 1
                else:
                    pos += 1

            logger.info(f"SGSN decoded {len(self.records)} records from {filepath}")
            if found_tags:
                logger.debug(f"SGSN found record tags: {found_tags}")
            if not self.records:
                logger.warning(
                    f"No SGSN records found in {filepath}. "
                    f"Expected outer tags: {[hex(t) for t in self.OUTER_TAGS.keys()]}"
                )
                print(f"[SGSN DECODER] First 100 bytes: {data[:100].hex()}")

            print(f"[SGSN DECODER] Total records decoded: {len(self.records)}, "
                  f"errors: {len(self.errors)}, file size: {len(data)} bytes")
            return self.records

        except Exception as e:
            logger.error(f"Error reading SGSN file {filepath}: {e}", exc_info=True)
            raise

    def _parse_length(self, data: bytes, pos: int) -> Tuple[int, int]:
        """Parse ASN.1 BER length field. Returns (length, bytes_consumed)."""
        if pos >= len(data):
            return 0, 0

        first_byte = data[pos]

        if first_byte & 0x80 == 0:
            # Short form
            return first_byte, 1
        else:
            # Long form
            num_bytes = first_byte & 0x7F
            if num_bytes == 0 or pos + 1 + num_bytes > len(data):
                return 0, 1
            length = int.from_bytes(data[pos + 1:pos + 1 + num_bytes], 'big')
            return length, 1 + num_bytes

    def _decode_record(self, data: bytes, record_num: int, record_type: int) -> Dict:
        """Decode a single SGSN CDR record from its inner content bytes."""
        # SGSN only handles data (PDP) sessions — always SGSN-CDR / DATA
        record_type_name = 'SGSN-CDR'
        service_type = 'DATA'

        record: Dict[str, Any] = {
            '_record_num': record_num,
            '_record_type_int': record_type,
            'record_type': record_type,
            'record_type_name': record_type_name,
            'service_type': service_type,
            'cdr_type': 'SGSN',
        }

        pos = 0
        while pos < len(data) - 1:
            tag = data[pos]

            # Extended tags (0x9F xx — primitive, 0xBF xx — constructed)
            if tag in (0x9F, 0xBF):
                if pos + 1 >= len(data):
                    break
                ext_tag = data[pos + 1]
                pos += 2
                if pos >= len(data):
                    break
                length, len_bytes = self._parse_length(data, pos)
                pos += len_bytes
                if pos + length > len(data):
                    break
                value = data[pos:pos + length]
                self._decode_extended_field(record, ext_tag, value)
                pos += length
                continue

            # Constructed tags in the 0xA0–0xBE range (inner sequences/sets)
            if 0xA0 <= tag <= 0xBE:
                pos += 1
                if pos >= len(data):
                    break
                length, len_bytes = self._parse_length(data, pos)
                pos += len_bytes
                if pos + length > len(data):
                    break
                inner = data[pos:pos + length]
                if tag == 0xAF:
                    # listOfTrafficVolumes [15] (SEQUENCE OF ChangeOfCharCondition)
                    self._decode_traffic_volumes(record, inner)
                elif tag == 0xA5:
                    # sgsnAddress [5] — GSNAddress as CHOICE (CONSTRUCTED).
                    # Huawei encodes: inner tag 0x80 + 4 bytes IPv4.
                    self._decode_choice_gsn_address(record, 'sgsn_address', inner)
                elif tag == 0xAB:
                    # ggsnAddressUsed [11] — GSNAddress as CHOICE (CONSTRUCTED).
                    # Huawei encodes: inner tag 0x80 + 4 bytes IPv4.
                    self._decode_choice_gsn_address(record, 'ggsn_address', inner)
                elif tag == 0xAE:
                    # servedPDPAddress [14] — PDPAddress as CHOICE (CONSTRUCTED).
                    self._decode_choice_pdp_address(record, inner)
                elif tag == 0xB4:
                    # diagnostics [20] (CHOICE, ignore payload)
                    record['diagnostics_raw'] = inner.hex()
                # Other constructed tags: skip content
                pos += length
                continue

            # SEQUENCE tag 0x30 — skip
            if tag == 0x30:
                pos += 1
                if pos >= len(data):
                    break
                length, len_bytes = self._parse_length(data, pos)
                pos += len_bytes + length
                continue

            # Primitive tags
            pos += 1
            if pos >= len(data):
                break
            length, len_bytes = self._parse_length(data, pos)
            pos += len_bytes
            if pos + length > len(data):
                break
            value = data[pos:pos + length]
            self._decode_primitive_field(record, tag, value)
            pos += length

        # Post-processing
        self._calculate_derived_fields(record)
        _apply_prepaid_flag(record)

        return record

    def _decode_primitive_field(self, record: Dict, tag: int, value: bytes):
        """Decode a primitive ASN.1 field by its context-specific tag."""

        if tag == 0x80:
            # networkInitiation [0] — BOOLEAN
            record['network_initiation'] = bool(value[0]) if value else False

        elif tag == 0x83:
            # servedIMSI [3] — TBCD
            record['imsi'] = self._tbcd_decode(value)

        elif tag == 0x84:
            # servedIMEI [4] — IMEI (TBCD encoded, 8 bytes = 15–16 digits).
            # Huawei SGSN uses tag [4] for IMEI, NOT for sgsnAddress.
            # The SGSN address comes as a CONSTRUCTED tag 0xA5 (see below).
            record['imei'] = self._tbcd_decode(value)

        elif tag == 0x85:
            # servedIMEISV [5] / sgsnAddress alternate — fall-back decode.
            # If 8 bytes → IMEI-SV (TBCD); if 4 or 5 bytes → treat as IP.
            if len(value) == 8:
                if not record.get('imei'):
                    record['imei'] = self._tbcd_decode(value)
            elif len(value) in (4, 5):
                self._decode_gsn_address(record, 'sgsn_address', value)

        elif tag == 0x86:
            # msNetworkCapability [6] — raw bytes
            record['ms_network_capability'] = value.hex() if value else None

        elif tag == 0x87:
            # routingArea [7] — Routing Area Code.
            # Standard 3GPP: 1-byte RAC.
            # Huawei variant: 6-byte full RAI (PLMN(3) + LAC(2) + RAC(1)).
            if value and len(value) == 1:
                record['rac'] = str(value[0])
            elif value and len(value) == 6:
                # Full RAI: extract PLMN, LAC, and RAC separately
                plmn = self._decode_plmn(value[0:3])
                lac_val = int.from_bytes(value[3:5], 'big')
                rac_val = value[5]
                record['rac'] = str(rac_val)
                if not record.get('lac'):
                    record['lac'] = str(lac_val)
                if not record.get('location_plmn'):
                    record['location_plmn'] = plmn
                if not record.get('serving_plmn'):
                    record['serving_plmn'] = plmn
            elif value:
                # Any other length — decode as integer (fallback)
                record['rac'] = str(int.from_bytes(value, 'big'))

        elif tag == 0x88:
            # locationAreaCode [8] — 2 bytes
            if value and len(value) >= 2:
                record['lac'] = str(int.from_bytes(value[:2], 'big'))

        elif tag == 0x89:
            # cellIdentifier [9] — 2 bytes
            if value and len(value) >= 2:
                record['cell_id'] = str(int.from_bytes(value[:2], 'big'))

        elif tag == 0x8A:
            # chargingID [10] — INTEGER
            record['charging_id'] = str(int.from_bytes(value, 'big')) if value else None

        elif tag == 0x8B:
            # ggsnAddressUsed [11] — GSNAddress (IP address)
            self._decode_gsn_address(record, 'ggsn_address', value)

        elif tag == 0x8C:
            # accessPointNameNI [12] — IA5String
            apn_raw = value.decode('ascii', errors='ignore').strip('\x00').strip()
            # APN is length-prefixed labels: each label is prefixed by its length byte
            apn_clean = self._decode_apn(value) or apn_raw
            record['apn'] = apn_clean
            record['apn_ni'] = apn_clean

        elif tag == 0x8D:
            # pdpType [13] — OCTET STRING (2 bytes: [org][type])
            # Huawei encoding: org=0xF1 (IETF), type=0x21 (IPv4) / 0x57 (IPv6) / 0x8D (IPv4v6)
            if value and len(value) >= 2:
                org_byte = value[-2]
                type_byte = value[-1]
                record['pdp_type_raw'] = f'0x{org_byte:02x}{type_byte:02x}'
                record['pdp_type'] = self.PDP_TYPE_MAP.get(
                    (org_byte, type_byte),
                    f'0x{type_byte:02x}'  # fallback: just show the type byte
                )
            elif value and len(value) == 1:
                # Single-byte encoding (rare)
                record['pdp_type'] = self.PDP_TYPE_MAP.get((0xF1, value[0]), f'0x{value[0]:02x}')

        elif tag == 0x8E:
            # servedPDPAddress [14] — PDPAddress (IP address or X.121)
            self._decode_pdp_address(record, value)

        elif tag == 0x90:
            # recordOpeningTime [16] — TimeStamp (BCD encoded, 9 bytes)
            ts = self._decode_timestamp(value)
            if ts:
                record['record_opening_time'] = ts
                if not record.get('start_time'):
                    record['start_time'] = ts

        elif tag == 0x91:
            # duration [17] — INTEGER (seconds)
            if value:
                duration = int.from_bytes(value, 'big')
                duration = max(0, min(duration, 2_147_483_647))
                record['duration_seconds'] = duration

        elif tag == 0x92:
            # sgsnChange [18] — BOOLEAN
            record['sgsn_change'] = bool(value[0]) if value else False

        elif tag == 0x93:
            # causeForRecClosing [19] — INTEGER
            if value:
                cause = int.from_bytes(value, 'big')
                record['cause_for_closing'] = cause
                record['cause_for_closing_name'] = self.CAUSE_FOR_CLOSING.get(cause, f'Unknown({cause})')

        elif tag == 0x95:
            # recordSequenceNumber [21] — INTEGER
            record['record_sequence_number'] = str(int.from_bytes(value, 'big')) if value else None

        elif tag == 0x96:
            # nodeID [22] — IA5String
            record['node_id'] = value.decode('ascii', errors='ignore').strip('\x00').strip()

        elif tag == 0x98:
            # localSequenceNumber [24] — INTEGER
            record['local_sequence_number'] = str(int.from_bytes(value, 'big')) if value else None

        elif tag == 0x99:
            # apnSelectionMode [25] — INTEGER ENUM
            record['apn_selection_mode'] = value[0] if value else None

        elif tag == 0x9A:
            # accessPointNameOI [26] — IA5String (operator identifier part of APN)
            record['apn_oi'] = value.decode('ascii', errors='ignore').strip('\x00').strip()

        elif tag == 0x9B:
            # servedMSISDN [27] — TBCD
            record['msisdn'] = self._tbcd_decode(value)

        elif tag == 0x9C:
            # chargingCharacteristics [28] — OCTET STRING (2 bytes)
            record['charging_characteristics'] = value.hex() if value else None

        elif tag == 0x9D:
            # chChSelectionMode [30] — INTEGER ENUM
            record['ch_selection_mode'] = value[0] if value else None

        elif tag == 0x9E:
            # rATType [75 in some encodings, or vendor tag 0x9E]
            rat = value[0] if value else None
            record['rat_type'] = rat
            record['rat_type_name'] = self.RAT_TYPES.get(rat, f'Unknown({rat})')

    def _decode_extended_field(self, record: Dict, ext_tag: int, value: bytes):
        """
        Decode extended ASN.1 field (0x9F xx or 0xBF xx tags).

        Confirmed Huawei SGSN tag mapping (verified from binary scan of 4391 records):
          0x9F 0x1F (tag 31) = userLocationInformation   — 544 records, 2 bytes
          0x9F 0x20 (tag 32) = rATType                   — 4391 records, 1 byte (2=GERAN)
          0x9F 0x21 (tag 33) = unknown vendor extension
          0x9F 0x4B (tag 75) = rATType (standard 3GPP encoding, fallback)
        """

        if ext_tag == 0x20:
            # rATType [32] — Huawei SGSN proprietary encoding.
            # Binary scan confirmed: present in all records, value 02 = GERAN (2G).
            rat = value[0] if value else None
            record['rat_type'] = rat
            record['rat_type_name'] = self.RAT_TYPES.get(rat, f'Unknown({rat})')

        elif ext_tag == 0x4B:
            # rATType [75] — standard 3GPP extended tag (fallback for other Huawei variants)
            rat = value[0] if value else None
            if not record.get('rat_type'):  # don't overwrite if 0x20 already set it
                record['rat_type'] = rat
                record['rat_type_name'] = self.RAT_TYPES.get(rat, f'Unknown({rat})')

        elif ext_tag == 0x1F:
            # userLocationInformation [31] — Huawei SGSN (CGI/SAI structure)
            record['user_location_info'] = value.hex() if value else None
            self._decode_user_location(record, value)

        elif ext_tag == 0x26:
            # start_time (Huawei extended)
            ts = self._decode_timestamp(value)
            if ts:
                record['start_time'] = ts

        elif ext_tag == 0x27:
            # stop_time (Huawei extended)
            ts = self._decode_timestamp(value)
            if ts:
                record['stop_time'] = ts

    def _decode_traffic_volumes(self, record: Dict, data: bytes):
        """Decode listOfTrafficVolumes (SEQUENCE OF ChangeOfCharCondition)."""
        pos = 0
        total_uplink = 0
        total_downlink = 0
        volumes = []

        while pos < len(data) - 1:
            tag = data[pos]
            pos += 1
            if pos >= len(data):
                break

            length, len_bytes = self._parse_length(data, pos)
            pos += len_bytes
            if pos + length > len(data):
                break

            inner = data[pos:pos + length]
            pos += length

            # Inner SEQUENCE or SET: decode each entry
            if tag == 0x30 or tag == 0x31:
                entry = self._decode_traffic_volume_entry(inner)
                if entry:
                    volumes.append(entry)
                    total_uplink += int(entry.get('data_volume_uplink', 0) or 0)
                    total_downlink += int(entry.get('data_volume_downlink', 0) or 0)

        record['traffic_volumes'] = volumes
        record['total_data_volume_uplink'] = str(total_uplink)
        record['total_data_volume_downlink'] = str(total_downlink)
        record['total_data_volume'] = str(total_uplink + total_downlink)
        record['data_volume_mb'] = round((total_uplink + total_downlink) / (1024 * 1024), 3)

    def _decode_traffic_volume_entry(self, data: bytes) -> Optional[Dict]:
        """
        Decode a single ChangeOfCharCondition entry (3GPP TS 32.298).

        Confirmed Huawei tag mapping (verified from binary analysis):
          0x81 = qosRequested   (QoS OCTET STRING, 13–15 bytes) — SKIP
          0x82 = qosNegotiated  (QoS OCTET STRING, 13–15 bytes) — SKIP
          0x83 = dataVolumeGPRSUplink   (INTEGER, 1–4 bytes)
          0x84 = dataVolumeGPRSDownlink (INTEGER, 1–4 bytes)
          0x85 = changeCondition (ENUMERATED, 1 byte)
          0x86 = changeTime (TimeStamp, 9 bytes)

        Length guard: data volumes fit in ≤ 8 bytes; QoS strings are 13–21 bytes.
        Any value > MAX_VOL_BYTES on a potential volume tag is a QoS OCTET STRING
        and must be skipped — otherwise int.from_bytes produces huge numbers.
        """
        MAX_VOL_BYTES = 8  # volumes ≤ 8 bytes; QoS strings 11-21 bytes

        entry = {}
        pos = 0

        while pos < len(data) - 1:
            tag = data[pos]
            pos += 1
            if pos >= len(data):
                break

            length, len_bytes = self._parse_length(data, pos)
            pos += len_bytes
            if pos + length > len(data):
                break

            value = data[pos:pos + length]
            pos += length

            if tag in (0x80, 0x85):
                # changeCondition — ENUMERATED (1 byte)
                if len(value) == 1:
                    entry['change_condition'] = value[0]

            elif tag in (0x81, 0x86):
                # changeTime — TimeStamp (9 bytes)
                if len(value) == 9:
                    entry['change_time'] = self._decode_timestamp(value)

            elif tag in (0x82, 0x83, 0x84):
                # Volume field OR QoS OCTET STRING — distinguish by length.
                # QoS strings are 11–21 bytes; volumes fit in ≤ MAX_VOL_BYTES.
                if len(value) > MAX_VOL_BYTES:
                    continue  # QoS field — skip to avoid integer overflow

                vol = int.from_bytes(value, 'big')
                if tag == 0x82:
                    # qosNegotiated (if long) already filtered above;
                    # short form = uplink volume in some Huawei variants
                    entry['data_volume_uplink'] = str(vol)
                elif tag == 0x83:
                    # dataVolumeGPRSUplink (standard) — or downlink if uplink already set
                    if 'data_volume_uplink' not in entry:
                        entry['data_volume_uplink'] = str(vol)
                    else:
                        entry['data_volume_downlink'] = str(vol)
                elif tag == 0x84:
                    # dataVolumeGPRSDownlink (standard)
                    entry['data_volume_downlink'] = str(vol)

        return entry if entry else None

    def _decode_choice_gsn_address(self, record: Dict, field_name: str, data: bytes):
        """
        Decode a GSNAddress wrapped in CHOICE encoding (CONSTRUCTED outer tag).

        Huawei encoding inside the CHOICE wrapper:
          0x80 + 4 bytes = iPBinV4Address [0]
          0x81 + 16 bytes = iPBinV6Address [1]
        """
        if not data:
            return
        pos = 0
        while pos < len(data) - 1:
            inner_tag = data[pos]; pos += 1
            if pos >= len(data): break
            inner_len, lb = self._parse_length(data, pos); pos += lb
            if pos + inner_len > len(data): break
            inner_val = data[pos:pos + inner_len]; pos += inner_len
            if inner_tag == 0x80 and inner_len == 4:
                record[field_name] = '.'.join(str(b) for b in inner_val)
                return
            elif inner_tag == 0x81 and inner_len == 16:
                record[field_name] = ':'.join(
                    f'{inner_val[i]:02x}{inner_val[i+1]:02x}' for i in range(0, 16, 2)
                )
                return
            elif inner_tag == 0x84 and inner_len == 4:
                # Some Huawei variants use 0x84 for IPv4
                record[field_name] = '.'.join(str(b) for b in inner_val)
                return
        # Fallback: try direct decode
        self._decode_gsn_address(record, field_name, data)

    def _decode_choice_pdp_address(self, record: Dict, data: bytes):
        """
        Decode a PDPAddress wrapped in CHOICE encoding (CONSTRUCTED outer tag).

        Huawei encoding inside the CHOICE wrapper:
          0xA0 (CONSTRUCTED [0]) + inner: 0x80 + 4 bytes = IPv4
          0xA1 (CONSTRUCTED [1]) + inner: 0x80 + 16 bytes = IPv6
        """
        if not data:
            return
        pos = 0
        while pos < len(data) - 1:
            inner_tag = data[pos]; pos += 1
            if pos >= len(data): break
            inner_len, lb = self._parse_length(data, pos); pos += lb
            if pos + inner_len > len(data): break
            inner_val = data[pos:pos + inner_len]; pos += inner_len
            if inner_tag in (0xA0, 0xA1):
                # CONSTRUCTED CHOICE: recurse one level
                self._decode_choice_pdp_address(record, inner_val)
                return
            elif inner_tag == 0x80 and inner_len == 4:
                record['pdp_address'] = '.'.join(str(b) for b in inner_val)
                return
            elif inner_tag == 0x81 and inner_len == 16:
                record['pdp_address'] = ':'.join(
                    f'{inner_val[i]:02x}{inner_val[i+1]:02x}' for i in range(0, 16, 2)
                )
                return

    def _decode_gsn_address(self, record: Dict, field_name: str, data: bytes):
        """
        Decode a GSNAddress (IPv4 or IPv6) from raw bytes.

        Huawei encoding variants:
          4 bytes  — raw IPv4
          5 bytes  — 0x04 (type=IPv4) + 4-byte IPv4
          16 bytes — raw IPv6
          17 bytes — 0x58/0x59 (type=IPv6) + 16-byte IPv6
        """
        if not data:
            return
        if len(data) == 4:
            # Raw IPv4
            record[field_name] = '.'.join(str(b) for b in data)
        elif len(data) == 5:
            # Type-prefixed IPv4: [type_byte][4 bytes]
            record[field_name] = '.'.join(str(b) for b in data[1:])
        elif len(data) == 16:
            # Raw IPv6
            record[field_name] = ':'.join(f'{data[i]:02x}{data[i+1]:02x}' for i in range(0, 16, 2))
        elif len(data) == 17:
            # Type-prefixed IPv6: [type_byte][16 bytes]
            addr = data[1:]
            record[field_name] = ':'.join(f'{addr[i]:02x}{addr[i+1]:02x}' for i in range(0, 16, 2))
        elif len(data) > 1:
            # Unknown length — try to decode the tail as IPv4
            addr = data[-4:] if len(data) >= 4 else data
            if len(addr) == 4:
                record[field_name] = '.'.join(str(b) for b in addr)
            else:
                record[field_name] = data.hex()

    def _decode_pdp_address(self, record: Dict, data: bytes):
        """Decode served PDP address (IP address)."""
        if not data:
            return
        if len(data) == 4:
            record['pdp_address'] = '.'.join(str(b) for b in data)
        elif len(data) > 4:
            # Skip first 2 bytes (address type)
            addr = data[2:] if len(data) >= 6 else data
            if len(addr) == 4:
                record['pdp_address'] = '.'.join(str(b) for b in addr)
            else:
                record['pdp_address'] = addr.hex()

    def _decode_apn(self, data: bytes) -> str:
        """Decode APN from length-prefixed label encoding."""
        if not data:
            return ''
        labels = []
        pos = 0
        while pos < len(data):
            if pos >= len(data):
                break
            label_len = data[pos]
            pos += 1
            if label_len == 0 or pos + label_len > len(data):
                break
            label = data[pos:pos + label_len].decode('ascii', errors='ignore')
            if label and all(c.isalnum() or c in '.-_' for c in label):
                labels.append(label)
            pos += label_len
        return '.'.join(labels) if labels else ''

    def _decode_user_location(self, record: Dict, data: bytes):
        """Decode user location information (Routing Area Identity etc.)."""
        if not data or len(data) < 6:
            return
        try:
            # CGI format: MCC+MNC(3) + LAC(2) + CI(2)
            plmn = self._decode_plmn(data[0:3])
            record['location_plmn'] = plmn
            if not record.get('serving_plmn') and plmn:
                record['serving_plmn'] = plmn
            if len(data) >= 5:
                lac = int.from_bytes(data[3:5], 'big')
                if not record.get('lac'):
                    record['lac'] = str(lac)
            if len(data) >= 7:
                ci = int.from_bytes(data[5:7], 'big')
                if not record.get('cell_id'):
                    record['cell_id'] = str(ci)
        except Exception:
            pass

    def _tbcd_decode(self, data: bytes) -> str:
        """Decode TBCD (Telephony Binary Coded Decimal) encoded number."""
        if not data:
            return ''
        result = ''
        for byte in data:
            low = byte & 0x0F
            high = (byte >> 4) & 0x0F
            if low != 0x0F:
                result += str(low)
            if high != 0x0F:
                result += str(high)
        return result

    def _decode_plmn(self, data: bytes) -> str:
        """Decode PLMN identifier (MCC + MNC) from 3 bytes."""
        if not data or len(data) < 3:
            return ''
        try:
            mcc1 = data[0] & 0x0F
            mcc2 = (data[0] >> 4) & 0x0F
            mcc3 = data[1] & 0x0F
            mnc3 = (data[1] >> 4) & 0x0F
            mnc1 = data[2] & 0x0F
            mnc2 = (data[2] >> 4) & 0x0F
            mcc = f'{mcc1}{mcc2}{mcc3}'
            mnc = f'{mnc1}{mnc2}' if mnc3 == 0x0F else f'{mnc1}{mnc2}{mnc3}'
            return f'{mcc}{mnc}'
        except Exception:
            return ''

    def _decode_timestamp(self, data: bytes) -> Optional[str]:
        """Decode BCD-encoded timestamp (9 bytes: YYMMDDHHmmssTzTz..)."""
        if not data:
            return None
        try:
            if len(data) >= 6:
                year = (data[0] >> 4) * 10 + (data[0] & 0x0F)
                month = (data[1] >> 4) * 10 + (data[1] & 0x0F)
                day = (data[2] >> 4) * 10 + (data[2] & 0x0F)
                hour = (data[3] >> 4) * 10 + (data[3] & 0x0F)
                minute = (data[4] >> 4) * 10 + (data[4] & 0x0F)
                second = (data[5] >> 4) * 10 + (data[5] & 0x0F)
                # Basic sanity
                if 1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60:
                    return f'20{year:02d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}'
            return None
        except Exception:
            return None

    def _calculate_derived_fields(self, record: Dict):
        """Calculate derived / convenience fields after raw parsing."""
        # Normalise calling_number = MSISDN
        record['calling_number'] = record.get('msisdn', '') or record.get('imsi', '')

        # called_number = APN for data, or destination for SMS
        record['called_number'] = record.get('apn', '')

        # Build stop_time from start + duration if missing
        start = record.get('start_time') or record.get('record_opening_time')
        stop = record.get('stop_time')
        if start and stop:
            try:
                from datetime import timedelta
                start_dt = datetime.strptime(start[:19], '%Y-%m-%d %H:%M:%S')
                stop_dt = datetime.strptime(stop[:19], '%Y-%m-%d %H:%M:%S')
                computed = int((stop_dt - start_dt).total_seconds())
                if not record.get('duration_seconds') and computed >= 0:
                    record['duration_seconds'] = max(0, min(computed, 2_147_483_647))
            except Exception:
                pass

        # Ensure volume defaults
        if 'total_data_volume_uplink' not in record:
            record['total_data_volume_uplink'] = '0'
        if 'total_data_volume_downlink' not in record:
            record['total_data_volume_downlink'] = '0'
        if 'total_data_volume' not in record:
            record['total_data_volume'] = '0'

        # Roaming detection: serving PLMN differs from OSL_PLMNI
        serving_plmn = record.get('serving_plmn', '') or record.get('location_plmn', '')
        if serving_plmn and not serving_plmn.startswith(OSL_PLMNI):
            record['is_roaming'] = True
        else:
            record['is_roaming'] = False

    def get_summary(self) -> Dict:
        """Return basic statistics about the decoded file."""
        if not self.records:
            return {'total_records': 0, 'errors': len(self.errors)}

        total_uplink = sum(int(r.get('total_data_volume_uplink', 0) or 0) for r in self.records)
        total_downlink = sum(int(r.get('total_data_volume_downlink', 0) or 0) for r in self.records)

        return {
            'total_records': len(self.records),
            'total_data_volume_bytes': total_uplink + total_downlink,
            'total_data_volume_mb': round((total_uplink + total_downlink) / (1024 * 1024), 2),
            'total_uplink_bytes': total_uplink,
            'total_downlink_bytes': total_downlink,
            'errors': len(self.errors),
            'file_info': self.file_info,
        }


# =============================================================================
# Prepaid / Postpaid helpers (mirrors MSC decoder logic)
# =============================================================================

def _apply_prepaid_flag(record: Dict):
    """
    Determine PREPAID_FLAG and SUBSCRIBER_CATEGORY from IMSI prefix.

    PREPAID_FLAG:
        '1' = PREPAID  (default — if IMSI doesn't match postpaid prefix)
        '0' = POSTPAID (IMSI starts with POSTPAID_IMSI_PREFIX)

    SUBSCRIBER_CATEGORY:
        '1' = PREPAID
        '0' = POSTPAID
        '2' = UNKNOWN (IMSI not available)
    """
    imsi = record.get('imsi', '') or ''

    if not imsi:
        record['PREPAID_FLAG'] = '1'
        record['SUBSCRIBER_CATEGORY'] = '2'
        return

    if imsi.startswith(POSTPAID_IMSI_PREFIX):
        record['PREPAID_FLAG'] = '0'
        record['SUBSCRIBER_CATEGORY'] = '0'
    else:
        record['PREPAID_FLAG'] = '1'
        record['SUBSCRIBER_CATEGORY'] = '1'


# =============================================================================
# Module-level convenience function
# =============================================================================

def decode_sgsn_file(filepath: str) -> Tuple[List[Dict], Dict]:
    """Decode an SGSN CDR file. Returns (records, summary)."""
    decoder = SGSNDecoder()
    records = decoder.decode_file(filepath)
    summary = decoder.get_summary()
    return records, summary
