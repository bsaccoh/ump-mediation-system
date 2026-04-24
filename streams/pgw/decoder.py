"""
Huawei PGW CDR Decoder
======================
Decodes PGW (Packet Gateway) CDR files from Huawei LTE/EPC equipment.
Supports both SGW-CDR and PGW-CDR record types per 3GPP TS 32.298.

Record Types:
- 79 (0x4F): pGWRecord (PGW-CDR)
- 78 (0x4E): sGWRecord (SGW-CDR)
- 85 (0x55): Internal Huawei PGW record type

Author: B-TEC Digital Solution Ltd
Version: 1.0
"""

import struct
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
import logging

logger = logging.getLogger(__name__)


class PGWDecoder:
    """Decoder for Huawei PGW CDR files (ASN.1 BER encoded)"""

    # Record type constants
    RECORD_TYPE_PGW = 79  # 0x4F - Standard 3GPP
    RECORD_TYPE_SGW = 78  # 0x4E - Standard 3GPP
    RECORD_TYPE_HUAWEI_PGW = 85  # 0x55 - Huawei specific

    # ASN.1 Tags for PGW CDR fields (context-specific)
    TAGS = {
        0x80: 'record_type',
        0x81: 'network_initiated_pdp',
        0x83: 'served_imsi',
        0x84: 'pgw_address',
        0x85: 'charging_id',
        0x86: 'serving_node_address',
        0x87: 'apn',
        0x88: 'pdn_type',
        0x89: 'served_pdp_pdn_address',
        0x8a: 'dynamic_address_flag',
        0x8b: 'dynamic_address_flag_ext',
        0x8c: 'record_opening_time',
        0x8d: 'duration',
        0x8e: 'cause_for_rec_closing',
        0x8f: 'diagnostics',
        0x90: 'record_sequence_number',
        0x91: 'node_id_int',
        0x92: 'node_id',
        0x93: 'record_extensions',
        0x94: 'local_sequence_number',
        0x95: 'apn_selection_mode',
        0x96: 'served_msisdn',
        0x97: 'charging_characteristics',
        0x98: 'ch_selection_mode',
        0x99: 'ims_signaling_context',
        0x9a: 'external_charging_id',
        0x9b: 'serving_node_plmn',
        0x9c: 'ps_furnish_charging_info',
        0x9d: 'served_imeisv',
        0x9e: 'rat_type',
    }

    # Extended tags (2-byte: 9f xx)
    EXTENDED_TAGS = {
        0x1f: 'ms_timezone',
        0x20: 'record_opening_time_ext',
        0x21: 'user_location_info',
        0x22: 'service_data_container',
        0x23: 'user_location_info_ext',
        0x24: 'serving_node_type',
        0x25: 'pgw_plmn_identifier',
        0x26: 'start_time',
        0x27: 'stop_time',
        0x28: 'served_pdp_pdn_address_ext',
        0x29: 'pdp_pdn_type_ext',
        0x2a: 'dynamic_address_flag_ext2',
        0x2b: 'sgw_change',
        0x2c: 'charging_per_ip_can_session',
        0x2d: 'ciot_optimization_indicator',
        0x2e: 'uli_timestamp',
        0x2f: 'three_gpp2_user_location_info',
    }

    # Cause for Record Closing values
    CAUSE_FOR_CLOSING = {
        0: 'normalRelease',
        1: 'partialRecord',
        4: 'abnormalRelease',
        16: 'volumeLimit',
        17: 'timeLimit',
        18: 'servingNodeChange',
        19: 'maxChangeCond',
        20: 'managementIntervention',
        21: 'intraSGSNIntersystemChange',
        22: 'rATChange',
        23: 'mSTimeZoneChange',
        24: 'sGSNPLMNIDChange',
        52: 'unauthorizedRequestingNetwork',
        53: 'unauthorizedLCSClient',
        54: 'positionMethodFailure',
        58: 'unknownOrUnreachableLCSClient',
        59: 'listofDownstreamNodeChange',
    }

    # RAT Type values
    RAT_TYPES = {
        1: 'UTRAN',
        2: 'GERAN',
        3: 'WLAN',
        4: 'GAN',
        5: 'HSPA_Evolution',
        6: 'EUTRAN',  # LTE
        7: 'Virtual',
        8: 'EUTRAN_NB_IoT',
        9: 'LTE_M',
        10: 'NR',  # 5G
    }

    # PDN Type values
    PDN_TYPES = {
        0: 'IPv4',
        1: 'IPv4',
        2: 'IPv6',
        3: 'IPv4v6',
        4: 'Non-IP',
    }

    def __init__(self):
        self.records = []
        self.file_info = {}
        self.errors = []

    def decode_file(self, filepath: str) -> List[Dict]:
        """
        Decode a PGW CDR file and return list of parsed records.

        Args:
            filepath: Path to the PGW CDR file

        Returns:
            List of dictionaries containing decoded CDR fields
        """
        self.records = []
        self.errors = []

        try:
            with open(filepath, 'rb') as f:
                data = f.read()

            self.file_info = {
                'filename': filepath,
                'file_size': len(data),
                'decode_time': datetime.now().isoformat()
            }

            # Find and decode all records
            pos = 0
            record_num = 0
            tag_scan = 0
            found_tags = set()
            first_bytes = data[0:100].hex() if len(data) > 0 else 'EMPTY'

            while pos < len(data) - 2:
                # Scan all tag patterns for debugging
                two_byte_tag = data[pos:pos+2]
                if two_byte_tag[0:1] in (b'\xbf', b'\xb0', b'\xa0'):
                    found_tags.add(two_byte_tag.hex())
                    tag_scan += 1
                
                # Look for PGW record tag (bf 4f or bf 4e)
                if data[pos:pos+2] == b'\xbf\x4f' or data[pos:pos+2] == b'\xbf\x4e':
                    print(f"[DECODER] Found PGW tag at position {pos}: {data[pos:pos+2].hex()}")
                    record_type = data[pos+1]
                    pos += 2

                    # Get record length
                    rec_len, len_bytes = self._parse_length(data, pos)
                    print(f"[DECODER] Record length: {rec_len}, length_bytes: {len_bytes}, next_pos: {pos + len_bytes}")
                    pos += len_bytes

                    # Extract record data
                    rec_data = data[pos:pos + rec_len]

                    try:
                        record = self._decode_record(rec_data, record_num, record_type)
                        self.records.append(record)
                    except Exception as e:
                        self.errors.append({
                            'record_num': record_num,
                            'position': pos,
                            'error': str(e)
                        })
                        logger.error(f"Error decoding record {record_num}: {e}")

                    pos += rec_len
                    record_num += 1
                else:
                    pos += 1

            logger.info(f"Decoded {len(self.records)} records from {filepath}")
            logger.info(f"DEBUG: Scanned {tag_scan} potential tag locations, found tags: {found_tags}")
            if not self.records:
                logger.warning(f"No PGW records found in {filepath}. Looking for tags 0xbf4f or 0xbf4e")
            print(f"[DECODER] Total records decoded: {len(self.records)}")
            print(f"[DECODER] First 100 bytes (hex): {first_bytes}")
            print(f"[DECODER] File size: {len(data)} bytes")
            return self.records

        except Exception as e:
            logger.error(f"Error reading file {filepath}: {e}")
            raise

    def _parse_length(self, data: bytes, pos: int) -> Tuple[int, int]:
        """Parse ASN.1 BER length field"""
        if pos >= len(data):
            return 0, 0

        first_byte = data[pos]

        if first_byte & 0x80 == 0:
            return first_byte, 1
        else:
            num_bytes = first_byte & 0x7f
            if pos + 1 + num_bytes > len(data):
                return 0, 1
            length = int.from_bytes(data[pos+1:pos+1+num_bytes], 'big')
            return length, 1 + num_bytes

    def _decode_record(self, data: bytes, record_num: int, record_type_tag: int) -> Dict:
        """Decode a single PGW CDR record"""
        record = {
            '_record_num': record_num,
            '_record_tag': record_type_tag,
            '_raw_length': len(data),
            'service_type': 'DATA',
        }

        pos = 0
        while pos < len(data) - 1:
            tag = data[pos]

            # Handle extended tags (9f xx)
            if tag == 0x9f:
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

            # Handle constructed tags (a0-bf range)
            if tag >= 0xa0 and tag <= 0xbf:
                pos += 1
                if pos >= len(data):
                    break

                length, len_bytes = self._parse_length(data, pos)
                pos += len_bytes

                if pos + length > len(data):
                    break

                if tag == 0xa4:
                    self._decode_address_constructed(record, 'pgw_address', data[pos:pos+length])
                elif tag == 0xa6:
                    self._decode_address_constructed(record, 'serving_node_address', data[pos:pos+length])
                elif tag == 0xa9:
                    self._decode_service_data_list(record, data[pos:pos+length])
                elif tag == 0xac:
                    self._decode_traffic_volumes(record, data[pos:pos+length])

                pos += length
                continue

            # Handle SEQUENCE tags (30)
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

        return record

    def _decode_primitive_field(self, record: Dict, tag: int, value: bytes):
        """Decode a primitive ASN.1 field"""

        if tag == 0x80:
            record['record_type'] = value[0] if value else None
            record['record_type_name'] = 'PGW-CDR' if value and value[0] in [79, 85] else 'SGW-CDR'

        elif tag == 0x83:
            record['imsi'] = self._tbcd_decode(value)

        elif tag == 0x85:
            charging_id = int.from_bytes(value, 'big') if value else None
            record['charging_id'] = str(charging_id) if charging_id is not None else None

        elif tag == 0x87:
            apn_raw = value.decode('ascii', errors='ignore').strip()
            apn_clean = ''.join(c for c in apn_raw if c.isalnum() or c in '.-_')
            record['apn'] = apn_clean if apn_clean else 'unknown'
            record['apn_raw'] = apn_raw

        elif tag == 0x88:
            pdn_val = int.from_bytes(value, 'big') if value else 0
            record['pdn_type_raw'] = pdn_val
            record['pdn_type'] = self.PDN_TYPES.get(pdn_val & 0x0f, f'Unknown({pdn_val})')

        elif tag == 0x8a or tag == 0x8b:
            record['dynamic_address_flag'] = value[0] == 0xff if value else False

        elif tag == 0x8c:
            ts = self._decode_timestamp(value)
            # Only use as timestamp if it decoded to a valid datetime string (19 chars)
            if ts and len(ts) == 19:
                record['record_opening_time'] = ts
                record['start_time'] = ts

        elif tag == 0x8d:
            if len(value) == 9:
                # Huawei CDR type 0x55: 9-byte field carries recordOpeningTime (BCD timestamp),
                # not an integer duration. Decode it as the session start time.
                ts = self._decode_timestamp(value)
                record['record_opening_time'] = ts
                record['start_time'] = ts
            else:
                # Standard 3GPP: integer duration in seconds
                record['duration_raw'] = value.hex()
                duration_seconds = int.from_bytes(value, 'big') if value else 0
                MAX_DURATION_SECONDS = 30 * 24 * 3600  # 2,592,000 seconds
                if duration_seconds > MAX_DURATION_SECONDS:
                    if len(value) > 4:
                        duration_seconds = int.from_bytes(value[:4], 'big')
                    if duration_seconds > MAX_DURATION_SECONDS:
                        duration_seconds = int.from_bytes(value[:2], 'big') if len(value) >= 2 else (value[0] if value else 0)
                record['duration_seconds'] = max(0, min(int(duration_seconds), 2_147_483_647))

        elif tag == 0x8e:
            # duration [14] — INTEGER seconds (3GPP TS 32.298 PGW-CDR & SGW-CDR)
            # Observed: 1–4 bytes integer (e.g. 0x04=4s, 0x0e10=3600s)
            # Rare fallback: 9-byte BCD stop_time on some Huawei variants
            if len(value) == 9:
                ts = self._decode_timestamp(value)
                if ts:
                    record['stop_time'] = ts
            else:
                duration_seconds = int.from_bytes(value, 'big') if value else 0
                MAX_DURATION_SECONDS = 30 * 24 * 3600  # 2,592,000 s
                if duration_seconds > MAX_DURATION_SECONDS:
                    if len(value) > 4:
                        duration_seconds = int.from_bytes(value[:4], 'big')
                    if duration_seconds > MAX_DURATION_SECONDS:
                        duration_seconds = int.from_bytes(value[:2], 'big') if len(value) >= 2 else (value[0] if value else 0)
                record['duration_seconds'] = max(0, min(int(duration_seconds), 2_147_483_647))

        elif tag == 0x8f:
            # causeForRecClosing [15] — ENUMERATED (3GPP TS 32.298)
            cause = value[0] if len(value) == 1 else int.from_bytes(value, 'big')
            record['cause_for_closing'] = cause
            record['cause_for_closing_name'] = self.CAUSE_FOR_CLOSING.get(cause, f'Unknown({cause})')

        elif tag == 0x91:
            record['node_id_int'] = str(int.from_bytes(value, 'big')) if value else None

        elif tag == 0x92:
            record['node_id'] = value.decode('ascii', errors='ignore').strip()

        elif tag == 0x94:
            record['local_sequence_number'] = str(int.from_bytes(value, 'big')) if value else None

        elif tag == 0x95:
            record['apn_selection_mode'] = value[0] if value else None

        elif tag == 0x96:
            record['msisdn'] = self._tbcd_decode(value)

        elif tag == 0x97:
            record['charging_characteristics'] = value.hex() if value else None

        elif tag == 0x98:
            record['ch_selection_mode'] = value[0] if value else None

        elif tag == 0x9b:
            record['serving_plmn'] = self._decode_plmn(value)

        elif tag == 0x9d:
            record['imeisv'] = self._tbcd_decode(value)
            if record.get('imeisv') and len(record['imeisv']) >= 14:
                record['imei'] = record['imeisv'][:14]
                record['sv'] = record['imeisv'][14:16] if len(record['imeisv']) >= 16 else ''

        elif tag == 0x9e:
            rat = value[0] if value else None
            record['rat_type'] = rat
            record['rat_type_name'] = self.RAT_TYPES.get(rat, f'Unknown({rat})')

    def _decode_extended_field(self, record: Dict, ext_tag: int, value: bytes):
        """Decode extended ASN.1 field (9f xx tags)"""

        if ext_tag == 0x1f:
            record['ms_timezone'] = value.hex() if value else None

        elif ext_tag == 0x20:
            # Huawei PGW proprietary ULI field — carries 3GPP TS 29.274 ULI bytes
            # (same bitmask format as 0x21/0x23 on other Huawei variants)
            record['huawei_ext_20_raw'] = value.hex() if value else None
            self._decode_user_location(record, value)

        elif ext_tag == 0x21 or ext_tag == 0x23:
            record['user_location_info'] = value.hex() if value else None
            self._decode_user_location(record, value)

        elif ext_tag == 0x22:
            record['service_data_container_raw'] = value.hex() if value else None

        elif ext_tag == 0x24:
            record['serving_node_type'] = value[0] if value else None

        elif ext_tag == 0x25:
            record['pgw_plmn'] = self._decode_plmn(value)

        elif ext_tag == 0x26:
            record['start_time'] = self._decode_timestamp(value)

        elif ext_tag == 0x27:
            record['stop_time'] = self._decode_timestamp(value)

    def _decode_address_constructed(self, record: Dict, field_name: str, data: bytes):
        """Decode constructed address field"""
        pos = 0
        while pos < len(data) - 2:
            tag = data[pos]
            pos += 1

            if pos >= len(data):
                break

            length, len_bytes = self._parse_length(data, pos)
            pos += len_bytes

            if pos + length > len(data):
                break

            value = data[pos:pos + length]

            if tag == 0x80:
                if len(value) >= 4:
                    ip = f"{value[-4]}.{value[-3]}.{value[-2]}.{value[-1]}"
                    record[field_name] = ip
            elif tag == 0x81:
                record[f'{field_name}_v6'] = value.hex()

            pos += length

    def _decode_traffic_volumes(self, record: Dict, data: bytes):
        """Decode list of traffic data volumes"""
        pos = 0
        total_uplink = 0
        total_downlink = 0
        volumes = []

        while pos < len(data) - 2:
            if data[pos] != 0x30:
                pos += 1
                continue

            pos += 1
            if pos >= len(data):
                break

            seq_len, len_bytes = self._parse_length(data, pos)
            pos += len_bytes

            if pos + seq_len > len(data):
                break

            seq_data = data[pos:pos + seq_len]
            volume = self._decode_traffic_volume_entry(seq_data)
            if volume:
                volumes.append(volume)
                uplink_val = volume.get('data_volume_uplink', 0)
                downlink_val = volume.get('data_volume_downlink', 0)
                # Convert string values back to int for accumulation
                total_uplink += int(uplink_val) if uplink_val and isinstance(uplink_val, str) else (uplink_val or 0)
                total_downlink += int(downlink_val) if downlink_val and isinstance(downlink_val, str) else (downlink_val or 0)

            pos += seq_len

        record['traffic_volumes'] = volumes
        record['total_data_volume_uplink'] = str(total_uplink)
        record['total_data_volume_downlink'] = str(total_downlink)
        record['total_data_volume'] = str(total_uplink + total_downlink)
        record['data_volume_mb'] = round((total_uplink + total_downlink) / (1024 * 1024), 3)

    def _decode_traffic_volume_entry(self, data: bytes) -> Optional[Dict]:
        """Decode a single traffic volume entry"""
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

            if tag == 0x82:
                # Standard 3GPP [2]: dataVolumeGPRSUplink — present in a small subset of records
                entry['data_volume_uplink'] = str(int.from_bytes(value, 'big')) if value else '0'
            elif tag == 0x83:
                # Huawei [3]: dataVolumeGPRSUplink (shifted +1 from standard)
                entry['data_volume_uplink'] = str(int.from_bytes(value, 'big')) if value else '0'
            elif tag == 0x84:
                # Huawei [4]: dataVolumeGPRSDownlink (shifted +1 from standard)
                entry['data_volume_downlink'] = str(int.from_bytes(value, 'big')) if value else '0'
            elif tag == 0x85:
                # Huawei [5]: changeCondition enum (NOT a volume; shifted +1 from standard)
                entry['change_condition'] = str(int.from_bytes(value, 'big')) if value else None
            elif tag == 0x86:
                if length == 9:
                    entry['change_time'] = self._decode_timestamp(value)
                else:
                    entry['change_condition'] = str(int.from_bytes(value, 'big')) if value else None
            elif tag == 0x88:
                entry['change_time'] = self._decode_timestamp(value)

            pos += length

        return entry if entry else None

    def _decode_service_data_list(self, record: Dict, data: bytes):
        """Decode list of service data (rating groups)"""
        record['service_data_raw'] = data.hex()

        rating_groups = []
        pos = 0

        while pos < len(data) - 2:
            if data[pos:pos+2] == b'\xa0\x06':
                pos += 2
                if pos + 4 <= len(data) and data[pos:pos+2] == b'\x80\x04':
                    rg_id = int.from_bytes(data[pos+2:pos+6], 'big')
                    rating_groups.append(rg_id)
                    pos += 6
                else:
                    pos += 1
            else:
                pos += 1

        if rating_groups:
            record['rating_groups'] = rating_groups

    def _decode_user_location(self, record: Dict, data: bytes):
        """
        Decode User Location Information (ULI) bytes.

        3GPP TS 29.274 Table 8.22-2 — byte 0 is a bitmask of present location types:
          bit 0 (0x01) = CGI   → type(1) + PLMN(3) + LAC(2) + CI(2)    = 8 bytes
          bit 1 (0x02) = SAI   → type(1) + PLMN(3) + LAC(2) + SAC(2)   = 8 bytes
          bit 2 (0x04) = RAI   → type(1) + PLMN(3) + LAC(2) + RAC(2)   = 8 bytes
          bit 3 (0x08) = TAI   → type(1) + PLMN(3) + TAC(2)            = 6 bytes
          bit 4 (0x10) = ECGI  → type(1) + PLMN(3) + ECI(4)            = 8 bytes
          0x18 = TAI+ECGI      → type(1) + PLMN(3) + TAC(2) + PLMN(3) + ECI(4) = 13 bytes

        Huawei observed values:
          0x18 = TAI+ECGI (13 bytes) — most common LTE record
          0x01 = CGI       (8 bytes) — 2G/3G records via PGW/SGW
          0x10 = ECGI      (8 bytes) — ECGI-only
          0x08 = TAI       (6 bytes) — TAI-only
        """
        if not data or len(data) < 4:
            return

        try:
            geo_type = data[0]

            if geo_type == 0x18 or geo_type == 0x13:
                # TAI + ECGI (most common LTE): PLMN(3)+TAC(2)+PLMN(3)+ECI(4)
                if len(data) >= 6:
                    record['location_plmn'] = self._decode_plmn(data[1:4])
                    record['tac'] = str(int.from_bytes(data[4:6], 'big'))
                if len(data) >= 13:
                    # ECGI PLMN at [6:9], ECI at [9:13] (28-bit, top 4 bits spare)
                    eci = int.from_bytes(data[9:13], 'big') & 0x0FFFFFFF
                    record['eci'] = str(eci)
                    record['cell_id'] = str(eci)

            elif geo_type == 0x01 or geo_type == 0x02 or geo_type == 0x04:
                # CGI / SAI / RAI: PLMN(3) + LAC(2) + CI/SAC/RAC(2) = 8 bytes total
                if len(data) >= 4:
                    record['location_plmn'] = self._decode_plmn(data[1:4])
                if len(data) >= 6:
                    record['lac'] = str(int.from_bytes(data[4:6], 'big'))
                if len(data) >= 8:
                    record['cell_id'] = str(int.from_bytes(data[6:8], 'big'))

            elif geo_type == 0x10 or geo_type == 0x11:
                # ECGI only: PLMN(3) + ECI(4) = 8 bytes total
                if len(data) >= 4:
                    record['location_plmn'] = self._decode_plmn(data[1:4])
                if len(data) >= 8:
                    eci = int.from_bytes(data[4:8], 'big') & 0x0FFFFFFF
                    record['eci'] = str(eci)
                    record['cell_id'] = str(eci)

            elif geo_type == 0x08 or geo_type == 0x0d:
                # TAI only: PLMN(3) + TAC(2) = 6 bytes total
                if len(data) >= 4:
                    record['location_plmn'] = self._decode_plmn(data[1:4])
                if len(data) >= 6:
                    record['tac'] = str(int.from_bytes(data[4:6], 'big'))

            else:
                # Unknown type — best-effort: always try PLMN at [1:4]
                if len(data) >= 4:
                    record['location_plmn'] = self._decode_plmn(data[1:4])
                # If 13+ bytes assume TAI+ECGI layout
                if len(data) >= 13:
                    record['tac'] = str(int.from_bytes(data[4:6], 'big'))
                    eci = int.from_bytes(data[9:13], 'big') & 0x0FFFFFFF
                    record['eci'] = str(eci)
                    record['cell_id'] = str(eci)
                elif len(data) >= 8:
                    # Assume CGI / ECGI 8-byte layout
                    record['cell_id'] = str(int.from_bytes(data[6:8], 'big'))

        except Exception:
            pass

    def _tbcd_decode(self, data: bytes) -> str:
        """Decode TBCD (Telephony Binary Coded Decimal) encoded number"""
        if not data:
            return ''

        result = ''
        for byte in data:
            low = byte & 0x0f
            high = (byte >> 4) & 0x0f

            if low != 0x0f:
                result += str(low)
            if high != 0x0f:
                result += str(high)

        return result

    def _decode_plmn(self, data: bytes) -> str:
        """Decode PLMN identifier (MCC + MNC)"""
        if not data or len(data) < 3:
            return ''

        mcc1 = data[0] & 0x0f
        mcc2 = (data[0] >> 4) & 0x0f
        mcc3 = data[1] & 0x0f
        mnc3 = (data[1] >> 4) & 0x0f
        mnc1 = data[2] & 0x0f
        mnc2 = (data[2] >> 4) & 0x0f

        mcc = f"{mcc1}{mcc2}{mcc3}"

        if mnc3 == 0x0f:
            mnc = f"{mnc1}{mnc2}"
        else:
            mnc = f"{mnc1}{mnc2}{mnc3}"

        return f"{mcc}{mnc}"

    def _decode_timestamp(self, data: bytes) -> Optional[str]:
        """Decode timestamp from various formats"""
        if not data:
            return None

        try:
            if len(data) == 9:
                year = (data[0] >> 4) * 10 + (data[0] & 0x0f)
                month = (data[1] >> 4) * 10 + (data[1] & 0x0f)
                day = (data[2] >> 4) * 10 + (data[2] & 0x0f)
                hour = (data[3] >> 4) * 10 + (data[3] & 0x0f)
                minute = (data[4] >> 4) * 10 + (data[4] & 0x0f)
                second = (data[5] >> 4) * 10 + (data[5] & 0x0f)
                return f"20{year:02d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

            elif len(data) == 13:
                year = (data[1] >> 4) * 10 + (data[1] & 0x0f)
                month = (data[2] >> 4) * 10 + (data[2] & 0x0f)
                day = (data[3] >> 4) * 10 + (data[3] & 0x0f)
                hour = (data[5] >> 4) * 10 + (data[5] & 0x0f)
                minute = (data[6] >> 4) * 10 + (data[6] & 0x0f)
                second = (data[7] >> 4) * 10 + (data[7] & 0x0f)
                return f"20{year:02d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

            elif len(data) == 8:
                year = (data[0] >> 4) * 10 + (data[0] & 0x0f)
                month = (data[1] >> 4) * 10 + (data[1] & 0x0f)
                day = (data[2] >> 4) * 10 + (data[2] & 0x0f)
                hour = (data[3] >> 4) * 10 + (data[3] & 0x0f)
                minute = (data[4] >> 4) * 10 + (data[4] & 0x0f)
                second = (data[5] >> 4) * 10 + (data[5] & 0x0f)
                return f"20{year:02d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

            return None

        except Exception as e:
            logger.debug(f"Error decoding timestamp: {e}")
            return None

    def _calculate_derived_fields(self, record: Dict):
        """Calculate derived fields from raw data"""
        start = record.get('start_time') or record.get('record_opening_time')
        stop = record.get('stop_time')

        if start and stop:
            try:
                start_dt = datetime.strptime(start[:19], '%Y-%m-%d %H:%M:%S')
                stop_dt = datetime.strptime(stop[:19], '%Y-%m-%d %H:%M:%S')
                computed = int((stop_dt - start_dt).total_seconds())
                record['duration_seconds'] = max(0, min(computed, 2_147_483_647))
            except Exception:
                pass

        record['calling_number'] = record.get('msisdn', '')
        record['called_number'] = record.get('apn', '')

        if 'total_data_volume' not in record:
            record['total_data_volume'] = '0'
            record['total_data_volume_uplink'] = '0'
            record['total_data_volume_downlink'] = '0'
            record['data_volume_mb'] = 0

        record['cdr_type'] = 'PGW'
        record['service_type'] = 'DATA'

        serving_plmn = record.get('serving_plmn', '')
        if serving_plmn and not serving_plmn.startswith('619'):
            record['is_roaming'] = True
        else:
            record['is_roaming'] = False

    def get_summary(self) -> Dict:
        """Get summary statistics of decoded records"""
        if not self.records:
            return {'total_records': 0}

        # Convert string values back to integers for calculation
        total_uplink = sum(int(r.get('total_data_volume_uplink', 0) or 0) for r in self.records)
        total_downlink = sum(int(r.get('total_data_volume_downlink', 0) or 0) for r in self.records)

        apns = {}
        rat_types = {}
        causes = {}

        for r in self.records:
            apn = r.get('apn', 'unknown')
            apns[apn] = apns.get(apn, 0) + 1

            rat = r.get('rat_type_name', 'unknown')
            rat_types[rat] = rat_types.get(rat, 0) + 1

            cause = r.get('cause_for_closing_name', 'unknown')
            causes[cause] = causes.get(cause, 0) + 1

        return {
            'total_records': len(self.records),
            'total_data_volume_bytes': total_uplink + total_downlink,
            'total_data_volume_mb': round((total_uplink + total_downlink) / (1024 * 1024), 2),
            'total_data_volume_gb': round((total_uplink + total_downlink) / (1024 * 1024 * 1024), 3),
            'total_uplink_bytes': total_uplink,
            'total_downlink_bytes': total_downlink,
            'apn_distribution': apns,
            'rat_type_distribution': rat_types,
            'cause_distribution': causes,
            'errors': len(self.errors),
            'file_info': self.file_info
        }


def decode_pgw_file(filepath: str) -> Tuple[List[Dict], Dict]:
    """Decode a PGW CDR file. Returns (records, summary)."""
    decoder = PGWDecoder()
    records = decoder.decode_file(filepath)
    summary = decoder.get_summary()
    return records, summary
