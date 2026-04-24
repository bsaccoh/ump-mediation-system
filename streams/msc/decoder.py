#!/usr/bin/env python3
"""
Huawei MSC CDR Decoder - CloudMSoftX3000
=========================================
Complete decoder for MSC CDR files with standardized output format.

Supports: MOC, MTC, Transit, SMS-MO, SMS-MT, Forwarding, Roaming, Gateway, HLR
Based on: CloudMSOFTX3000 V500R012C35 ASN.1 CDR Description

Author: Claude Assistant
Version: 3.0 - Complete Field Mapping
"""

import os
import sys
import csv
import json
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
from collections import defaultdict

# =============================================================================
# STANDARDIZED OUTPUT FIELDS (matching mediation system)
# =============================================================================

OUTPUT_FIELDS = [
    'PREPAID_FLAG', 'SUBSCRIBER_CATEGORY', 'SUBSCRIBER_TYPE', 'EVENT_STATUS', 'NETWORK_RECORD_ID',
    'NETWORK_ENTITY', 'MSC_ID', 'CALL_REF', 'CALL_DIRECTION', 'ORIGINAL_CALL_TYPE',
    'MD_SPLIT_TYPE', 'TELESERVICE_CODE', 'BEARER_SERVICE_CODE', 'SERVICE_TYPE',
    'SERVICE_ID', 'CHARGED_PARTY_IMSI', 'CHARGED_PARTY_MSISDN', 'CALLING_NO',
    'CALLED_NO', 'DIALED_NO', 'START_DATETIME', 'UTC_TIME_OFFSET', 'CALL_DURATION',
    'CDR_FILE_NAME', 'OPERATOR_ID', 'ORIGINATING_TRUNK', 'ORIGINATING_MEMBER',
    'TERMINATING_TRUNK', 'TERMINATING_MEMBER', 'CELL_ID_A', 'MS_CLASSMARK',
    'IMSI_A', 'IMEI_A', 'IMSI_B', 'IMEI_B',
    'LOAD_DATE', 'RAT_TYPE', 'PARTIAL_RECORD_NO',
    'CHARGING_CHARACTERISTICS', 'RESULT_CODE', 'LAC_IDENTIFIER',
    'ROAMING_ICR_INDICATOR', 'CALL_END_DATETIME',
]

# =============================================================================
# RECORD TYPE DEFINITIONS
# =============================================================================

# Record type tags (context-specific constructed)
RECORD_TAGS = {
    0xA0: ('moCallRecord', 'O', 'MOC', 'V', 'VOICE'),              # Mobile Originated Call
    0xA1: ('mtCallRecord', 'T', 'MTC', 'V', 'VOICE'),              # Mobile Terminated Call
    0xA2: ('roamingRecord', 'O', 'ROAMING', 'V', 'VOICE'),         # Roaming Call
    0xA3: ('incGatewayRecord', 'T', 'GWIN', 'V', 'VOICE'),         # Incoming Gateway
    0xA4: ('outGatewayRecord', 'O', 'GWOUT', 'V', 'VOICE'),        # Outgoing Gateway
    0xA5: ('transitRecord', 'T', 'TRANSIT', 'V', 'VOICE'),         # Transit Call
    0xA6: ('moSMSRecord', 'O', 'SMSMO', 'E', 'SMS'),               # SMS Mobile Originated
    0xA7: ('mtSMSRecord', 'T', 'SMSMT', 'E', 'SMS'),               # SMS Mobile Terminated
    0xA8: ('moSMSIWRecord', 'O', 'SMSMO_IW', 'E', 'SMS'),          # SMS-MO Interworking
    0xA9: ('mtSMSGWRecord', 'T', 'SMSMT_GW', 'E', 'SMS'),          # SMS-MT Gateway
    0xAA: ('ssActionRecord', 'O', 'SS', 'S', 'SS'),                # Supplementary Service
    0xAB: ('hlrIntRecord', 'O', 'HLR', 'H', 'HLR'),                # HLR Interrogation
    0xAC: ('locUpdateRecord', 'O', 'LOCUPD', 'L', 'LOCUPD'),       # Location Update (VLR)
    0xAD: ('commonEquipRecord', 'O', 'EQUIP', 'Q', 'EQUIP'),       # Common Equipment
    0xAE: ('moEmergencyRecord', 'O', 'EMERG', 'V', 'VOICE'),       # Emergency Call
    0xAF: ('moCFRecord', 'O', 'CFW', 'V', 'VOICE'),                # Call Forwarding
    0xB0: ('termCAMELRecord', 'T', 'CAMEL', 'V', 'VOICE'),         # Terminating CAMEL
    0xB1: ('mtRoamingForward', 'T', 'MTRF', 'V', 'VOICE'),         # MT Roaming Forwarding
    0xB2: ('mSCsRVCCRecord', 'O', 'SRVCC', 'V', 'VOICE'),          # Single Radio Voice Call Continuity
    0xB3: ('mtLCSRecord', 'T', 'MTLCS', 'L', 'LCS'),               # MT Location Request
    0xB4: ('moLCSRecord', 'O', 'MOLCS', 'L', 'LCS'),               # MO Location Request
    0xB5: ('niLCSRecord', 'O', 'NILCS', 'L', 'LCS'),               # Network Induced Location Request
    0xB6: ('sipOrigRecord', 'O', 'SIPO', 'V', 'VOICE'),            # SIP Originated Call
    0xB7: ('sipTermRecord', 'T', 'SIPT', 'V', 'VOICE'),            # SIP Terminated Call
    0xB8: ('sipSMSMORecord', 'O', 'SIP_SMSMO', 'E', 'SMS'),        # SIP SMS-MO
    0xB9: ('sipSMSMTRecord', 'T', 'SIP_SMSMT', 'E', 'SMS'),        # SIP SMS-MT
    0xBA: ('imeiObsRecord', 'O', 'IMEI_OBS', 'I', 'IMEI'),         # IMEI Observation
    0xBF: ('extendedRecord', 'O', 'EXT', 'X', 'EXTENDED'),         # Extended format record
}

# Cause for termination codes
CAUSE_FOR_TERM = {
    0x00: 'normalRelease',
    0x01: 'partialRecord',
    0x02: 'partialRecordCallReestablishment',
    0x03: 'unsuccessfulCallAttempt',
    0x04: 'stableCallAbnormalTermination',
    0x05: 'cAMELInitCallRelease',
    0x06: 'unauthorizedRequestingNetwork',
    0x07: 'unauthorizedLCSClient',
    0x08: 'positionMethodFailure',
    0x09: 'unknownOrUnreachableLCSClient',
    0x10: 'normalRelease',
}

# Teleservice codes
TELESERVICE_CODES = {
    0x00: '0',    # All teleservices
    0x10: '16',   # All speech transmission services
    0x11: '17',   # Telephony
    0x12: '18',   # Emergency calls
    0x20: '32',   # All SMS services
    0x21: '33',   # Short Message MT/PP
    0x22: '22',   # Short Message MO/PP
    0x60: '96',   # All fax services
    0x61: '97',   # Fax Group 3
}

# Bearer service codes
BEARER_CODES = {
    0x00: '0',    # All bearer services
    0x20: '32',   # All data CDA services
    0x21: '33',   # Data CDA 300 bps
    0x22: '34',   # Data CDA 1200 bps
    0x23: '35',   # Data CDA 2400 bps
    0x24: '36',   # Data CDA 4800 bps
    0x25: '37',   # Data CDA 9600 bps
    0x26: '38',   # General data CDA
}

# Charged party
CHARGED_PARTY = {
    0x00: 'callingParty',
    0x01: 'calledParty',
    0x02: 'noChargeParty',
}

# =============================================================================
# ASN.1 BER PARSER
# =============================================================================

class ASN1Parser:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
    
    def remaining(self) -> int:
        return len(self.data) - self.pos
    
    def read_byte(self) -> int:
        if self.pos >= len(self.data):
            raise ValueError("End of data")
        b = self.data[self.pos]
        self.pos += 1
        return b
    
    def peek_byte(self) -> int:
        if self.pos >= len(self.data):
            return -1
        return self.data[self.pos]
    
    def read_bytes(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError("Not enough data")
        result = self.data[self.pos:self.pos + n]
        self.pos += n
        return result
    
    def read_tag(self) -> Tuple[int, int, bool, int]:
        """Returns (tag_class, tag_number, is_constructed, full_tag)"""
        start_pos = self.pos
        b = self.read_byte()
        tag_class = (b >> 6) & 0x03
        is_constructed = bool(b & 0x20)
        tag_number = b & 0x1F
        full_tag = b
        
        if tag_number == 0x1F:
            # Long form tag
            tag_number = 0
            while True:
                b = self.read_byte()
                full_tag = (full_tag << 8) | b
                tag_number = (tag_number << 7) | (b & 0x7F)
                if not (b & 0x80):
                    break
        
        return tag_class, tag_number, is_constructed, full_tag
    
    def read_length(self) -> int:
        b = self.read_byte()
        if b < 0x80:
            return b
        num_octets = b & 0x7F
        if num_octets == 0:
            return -1  # Indefinite
        length = 0
        for _ in range(num_octets):
            length = (length << 8) | self.read_byte()
        return length
    
    def read_tlv(self) -> Tuple[int, int, bool, int, bytes]:
        """Returns (tag_class, tag_number, is_constructed, full_tag, value)"""
        tag_class, tag_number, is_constructed, full_tag = self.read_tag()
        length = self.read_length()
        if length < 0:
            raise ValueError("Indefinite length not supported")
        value = self.read_bytes(length)
        return tag_class, tag_number, is_constructed, full_tag, value

# =============================================================================
# DATA TYPE DECODERS
# =============================================================================

def decode_tbcd(data: bytes) -> str:
    """Decode TBCD (Telephony BCD) string"""
    result = []
    for byte in data:
        low = byte & 0x0F
        high = (byte >> 4) & 0x0F
        
        if low <= 9:
            result.append(str(low))
        elif low == 0x0A:
            result.append('*')
        elif low == 0x0B:
            result.append('#')
        elif low == 0x0C:
            result.append('a')
        elif low == 0x0D:
            result.append('b')
        elif low == 0x0E:
            result.append('c')
        elif low == 0x0F:
            pass
        
        if high <= 9:
            result.append(str(high))
        elif high == 0x0A:
            result.append('*')
        elif high == 0x0B:
            result.append('#')
        elif high == 0x0C:
            result.append('a')
        elif high == 0x0D:
            result.append('b')
        elif high == 0x0E:
            result.append('c')
        elif high == 0x0F:
            pass
    
    return ''.join(result)

def decode_address(data: bytes) -> Tuple[str, str]:
    """Decode GSM address (AddressString)"""
    if len(data) < 1:
        return '', ''

    noa_npi = data[0]
    type_ind = f'{noa_npi:02X}'
    ton = (noa_npi >> 4) & 0x07  # Type of Number (bits 6-5-4)

    if ton == 5 and len(data) > 1:
        # TON=5: Alphanumeric (GSM 7-bit / reversed-BCD ASCII encoding)
        # Per Huawei spec: ASCII characters encoded by reversed BCD codes
        # Each byte has its nibbles swapped: ASCII 0x31 stored as 0x13
        number = decode_reversed_bcd_ascii(data[1:])
    else:
        # Standard TBCD number encoding
        number = decode_tbcd(data[1:]) if len(data) > 1 else ''

    return number, type_ind


def decode_reversed_bcd_ascii(data: bytes) -> str:
    """Decode reversed-BCD-encoded ASCII string.

    Per Huawei CloudMSOFTX3000 spec: when origination/destination is a string
    of ASCII characters, each byte has its nibbles swapped.
    Example: '123456' (ASCII 31 32 33 34 35 36) stored as 13 23 33 43 53 63.
    Filler nibble 0xF is used for padding when odd number of nibbles.
    """
    result = []
    for byte in data:
        # Swap nibbles back to get original ASCII byte
        swapped = ((byte & 0x0F) << 4) | ((byte >> 4) & 0x0F)
        if swapped == 0xF0 or (swapped >> 4) == 0x0F:
            # Filler byte (0xF_ padding) — skip
            continue
        if 0x20 <= swapped <= 0x7E:
            result.append(chr(swapped))
        else:
            # Non-printable — keep as hex for safety
            result.append(f'{swapped:02X}')
    return ''.join(result)

def decode_bcd_timestamp(data: bytes) -> Tuple[str, str]:
    """Decode BCD timestamp"""
    if len(data) < 6:
        return '', '+0:00'
    
    try:
        year_ns = ((data[0] >> 4) * 10) + (data[0] & 0x0F)
        month_ns = ((data[1] >> 4) * 10) + (data[1] & 0x0F)
        day_ns = ((data[2] >> 4) * 10) + (data[2] & 0x0F)
        hour_ns = ((data[3] >> 4) * 10) + (data[3] & 0x0F)
        minute_ns = ((data[4] >> 4) * 10) + (data[4] & 0x0F)
        second_ns = ((data[5] >> 4) * 10) + (data[5] & 0x0F)
        
        ns_valid = (1 <= month_ns <= 12 and 1 <= day_ns <= 31 and 
                    0 <= hour_ns <= 23 and 0 <= minute_ns <= 59 and 0 <= second_ns <= 59)
        
        year_sw = (data[0] & 0x0F) * 10 + ((data[0] >> 4) & 0x0F)
        month_sw = (data[1] & 0x0F) * 10 + ((data[1] >> 4) & 0x0F)
        day_sw = (data[2] & 0x0F) * 10 + ((data[2] >> 4) & 0x0F)
        hour_sw = (data[3] & 0x0F) * 10 + ((data[3] >> 4) & 0x0F)
        minute_sw = (data[4] & 0x0F) * 10 + ((data[4] >> 4) & 0x0F)
        second_sw = (data[5] & 0x0F) * 10 + ((data[5] >> 4) & 0x0F)
        
        sw_valid = (1 <= month_sw <= 12 and 1 <= day_sw <= 31 and 
                    0 <= hour_sw <= 23 and 0 <= minute_sw <= 59 and 0 <= second_sw <= 59)
        
        if ns_valid:
            year, month, day, hour, minute, second = year_ns, month_ns, day_ns, hour_ns, minute_ns, second_ns
        elif sw_valid:
            year, month, day, hour, minute, second = year_sw, month_sw, day_sw, hour_sw, minute_sw, second_sw
        else:
            return '', '+0:00'
        
        if year >= 70:
            year += 1900
        else:
            year += 2000
        
        dt_str = f"{year:04d}{month:02d}{day:02d}{hour:02d}{minute:02d}{second:02d}"
        
        offset = '+0:00'
        if len(data) >= 7:
            tz_byte = data[6]
            if tz_byte == 0x2B:
                tz_sign = '+'
                if len(data) >= 9:
                    tz_hour = ((data[7] >> 4) * 10) + (data[7] & 0x0F)
                    tz_min = ((data[8] >> 4) * 10) + (data[8] & 0x0F)
                else:
                    tz_hour, tz_min = 0, 0
                offset = f"{tz_sign}{tz_hour}:{tz_min:02d}"
            elif tz_byte == 0x2D:
                tz_sign = '-'
                if len(data) >= 9:
                    tz_hour = ((data[7] >> 4) * 10) + (data[7] & 0x0F)
                    tz_min = ((data[8] >> 4) * 10) + (data[8] & 0x0F)
                else:
                    tz_hour, tz_min = 0, 0
                offset = f"{tz_sign}{tz_hour}:{tz_min:02d}"
        
        return dt_str, offset
    except Exception as e:
        return '', '+0:00'

def decode_integer(data: bytes) -> int:
    if not data:
        return 0
    value = 0
    for byte in data:
        value = (value << 8) | byte
    return value

def decode_unsigned(data: bytes) -> int:
    if not data:
        return 0
    value = 0
    for byte in data:
        value = (value << 8) | byte
    if value > 0xFFFFFFFF:
        return value & 0xFFFFFFFF
    return value

def decode_duration(data: bytes) -> int:
    if not data:
        return 0
    if len(data) > 4:
        data = data[:4]
    value = 0
    for byte in data:
        value = (value << 8) | byte
    return value

def decode_octet_string(data: bytes) -> str:
    return data.hex().upper()

def decode_ia5_string(data: bytes) -> str:
    try:
        return data.decode('ascii')
    except:
        return data.decode('latin-1', errors='ignore')

def decode_location(data: bytes) -> Tuple[str, str]:
    if len(data) < 4:
        return '', ''
    
    if len(data) >= 7:
        lac = (data[3] << 8) | data[4]
        cell_id = (data[5] << 8) | data[6]
    elif len(data) >= 5:
        lac = (data[0] << 8) | data[1]
        cell_id = (data[3] << 8) | data[4] if len(data) > 4 else (data[2] << 8) | data[3]
    else:
        lac = (data[0] << 8) | data[1] if len(data) >= 2 else 0
        cell_id = (data[2] << 8) | data[3] if len(data) >= 4 else 0
    
    return str(lac), str(cell_id)

def decode_global_area_id(data: bytes) -> Tuple[str, str, str]:
    if len(data) < 5:
        return '', '', ''
    
    mcc1 = data[0] & 0x0F
    mcc2 = (data[0] >> 4) & 0x0F
    mcc3 = data[1] & 0x0F
    mnc3 = (data[1] >> 4) & 0x0F
    mnc1 = data[2] & 0x0F
    mnc2 = (data[2] >> 4) & 0x0F
    
    mcc = f"{mcc1}{mcc2}{mcc3}"
    if mnc3 == 0x0F:
        mnc = f"{mnc1}{mnc2}"
    else:
        mnc = f"{mnc1}{mnc2}{mnc3}"
    
    lac = (data[3] << 8) | data[4]
    cell_id = (data[5] << 8) | data[6] if len(data) >= 7 else 0
    
    return f"{mcc}{mnc}", str(lac), str(cell_id)

def decode_basic_service(data: bytes) -> Tuple[str, str]:
    if len(data) < 2:
        return '', ''
    
    try:
        parser = ASN1Parser(data)
        tc, tn, ic, ft, val = parser.read_tlv()
        
        if tc == 2:
            code = decode_unsigned(val)
            if tn == 1:
                return TELESERVICE_CODES.get(code, str(code)), ''
            elif tn == 2:
                return '', BEARER_CODES.get(code, str(code))
        
        return '', ''
    except:
        return '', ''

def decode_trunk_group(data: bytes) -> Tuple[str, str]:
    try:
        parser = ASN1Parser(data)
        route = ''
        member = ''
        
        while parser.remaining() > 0:
            tc, tn, ic, ft, val = parser.read_tlv()
            if tc == 2:
                if tn == 0 or tn == 1:
                    try:
                        decoded = val.decode('ascii')
                        if decoded.isprintable():
                            route = decoded
                        else:
                            route = val.hex().upper()
                    except:
                        route = val.hex().upper()
                elif tn == 1:
                    member = str(decode_unsigned(val))
            elif tc == 0:
                if tn == 4:
                    try:
                        decoded = val.decode('ascii')
                        if decoded.isprintable():
                            route = decoded
                    except:
                        pass
        
        return route, member
    except:
        try:
            decoded = data.decode('ascii')
            if decoded.isprintable():
                return decoded, ''
        except:
            pass
        return '', ''

def decode_route_name(data: bytes) -> str:
    if not data or len(data) < 3:
        return ''
    
    try:
        pos = 0
        while pos < len(data) - 2:
            tag = data[pos]
            
            if tag == 0x81:
                length = data[pos + 1]
                if length > 0 and pos + 2 + length <= len(data):
                    name_bytes = data[pos + 2:pos + 2 + length]
                    name = name_bytes.decode('ascii', errors='ignore')
                    if name and all(c.isprintable() or c.isspace() for c in name):
                        return name.strip()
            
            elif tag == 0x80:
                length = data[pos + 1]
                if length > 0 and pos + 2 + length <= len(data):
                    name_bytes = data[pos + 2:pos + 2 + length]
                    try:
                        name = name_bytes.decode('ascii', errors='ignore')
                        if name and all(c.isprintable() or c.isspace() for c in name):
                            return name.strip()
                    except:
                        pass
            
            pos += 1
        
        try:
            decoded = data.decode('ascii', errors='ignore')
            if decoded and len(decoded) > 2 and all(c.isprintable() or c.isspace() for c in decoded):
                return decoded.strip()
        except:
            pass
        
    except:
        pass
    
    return ''

# =============================================================================
# MSC CDR RECORD DECODER
# =============================================================================

def decode_msc_record(data: bytes, filename: str, record_tag: int) -> Dict[str, Any]:
    """Decode a single MSC CDR record"""
    record = {field: '' for field in OUTPUT_FIELDS}
    
    rt_info = RECORD_TAGS.get(record_tag, ('unknown', 'O', 'UNKNOWN', 'U', 'UNKNOWN'))
    rt_name, direction, call_type, service_type, service_id = rt_info
    
    record['PREPAID_FLAG'] = '2'
    record['SUBSCRIBER_CATEGORY'] = '2'
    record['SUBSCRIBER_TYPE'] = '0'
    record['EVENT_STATUS'] = '1'
    record['CALL_DIRECTION'] = direction
    record['ORIGINAL_CALL_TYPE'] = call_type
    record['SERVICE_TYPE'] = service_type
    record['SERVICE_ID'] = service_id
    record['MD_SPLIT_TYPE'] = 'E'
    record['CDR_FILE_NAME'] = filename
    record['LOAD_DATE'] = datetime.now().strftime('%Y%m%d%H%M%S')
    record['UTC_TIME_OFFSET'] = '+0:00'
    
    if call_type in ['SMSMO', 'SMSMT']:
        record['TELESERVICE_CODE'] = '22'
        record['CALL_DURATION'] = '1'
    
    try:
        pos = 0
        while pos < len(data) - 1:
            tag_byte = data[pos]
            
            if tag_byte == 0x9F:
                if pos + 1 >= len(data):
                    break
                
                ext1 = data[pos + 1]
                
                if ext1 & 0x80:
                    if pos + 2 >= len(data):
                        break
                    ext2 = data[pos + 2]
                    
                    len_pos = pos + 3
                    if len_pos >= len(data):
                        break
                    
                    length, consumed = parse_length(data, len_pos)
                    if length < 0:
                        pos += 1
                        continue
                    
                    val_start = len_pos + consumed
                    if val_start + length > len(data):
                        pos += 1
                        continue
                    
                    value = data[val_start:val_start + length]
                    inner_tag = ext2 & 0x7F
                    
                    process_extended_tag_81(record, inner_tag, value)
                    
                    pos = val_start + length
                    continue
                else:
                    len_pos = pos + 2
                    if len_pos >= len(data):
                        break
                    
                    length, consumed = parse_length(data, len_pos)
                    if length < 0:
                        pos += 1
                        continue
                    
                    val_start = len_pos + consumed
                    if val_start + length > len(data):
                        pos += 1
                        continue
                    
                    value = data[val_start:val_start + length]
                    inner_tag = ext1 & 0x7F
                    
                    process_extended_tag(record, inner_tag, value)
                    
                    pos = val_start + length
                    continue
            
            elif tag_byte == 0xBF:
                if pos + 1 >= len(data):
                    break
                
                ext1 = data[pos + 1]
                len_pos = pos + 2
                
                if len_pos >= len(data):
                    break
                
                length, consumed = parse_length(data, len_pos)
                if length < 0:
                    pos += 1
                    continue
                
                val_start = len_pos + consumed
                if val_start + length > len(data):
                    pos += 1
                    continue
                
                value = data[val_start:val_start + length]
                
                process_bf_tag(record, ext1, value)
                
                pos = val_start + length
                continue
            
            elif 0x80 <= tag_byte <= 0xBE:
                len_pos = pos + 1
                if len_pos >= len(data):
                    break
                
                length, consumed = parse_length(data, len_pos)
                if length < 0:
                    pos += 1
                    continue
                
                val_start = len_pos + consumed
                if val_start + length > len(data):
                    pos += 1
                    continue
                
                value = data[val_start:val_start + length]
                
                process_standard_tag(record, tag_byte, value)
                
                pos = val_start + length
                continue
            
            pos += 1
        
        if not record['CALL_REF'] and record['NETWORK_RECORD_ID']:
            record['CALL_REF'] = record['NETWORK_RECORD_ID']

        if not record['START_DATETIME'] and record['CALL_END_DATETIME']:
            record['START_DATETIME'] = record['CALL_END_DATETIME']

        if record['START_DATETIME'] and not record['CALL_END_DATETIME']:
            record['CALL_END_DATETIME'] = record['START_DATETIME']

        # Final pass: compute PREPAID_FLAG (IMSI prefix) and SUBSCRIBER_CATEGORY (CAMEL rules).
        # CAMEL-aware records will have had _apply_subscriber_category() called already
        # during tag processing with real serviceKey/camelPhase values.
        # For records with no CAMEL tags, this call uses serviceKey=0 / camelPhase=0.
        _apply_prepaid_flag(record)
        if record.get('SUBSCRIBER_CATEGORY') == '2':
            _apply_subscriber_category(record)

    except Exception as e:
        pass

    return record

def process_standard_tag(record: Dict, tag: int, value: bytes):
    """Process standard context-specific tags (0x80-0xBE)"""

    call_type = record.get('ORIGINAL_CALL_TYPE', '')
    is_gateway = call_type in ['GWIN', 'GWOUT']
    is_moc = call_type in ['MOC', 'EMERG', 'CFW']
    is_smsmt = call_type == 'SMSMT'
    is_smsmo = call_type == 'SMSMO'
    is_sms = is_smsmt or is_smsmo

    # =========================================================================
    # SMS-MT record tags (per Huawei CloudMSOFTX3000 V500R012C35 ASN.1 spec)
    # Tag layout is DIFFERENT from voice MOC/MTC records:
    #   0x80 = recordType (ENUMERATED, value 0x07)
    #   0x81 = serviceCentre (ADDRESS) - SMSC E.164 address
    #   0x82 = servedIMSI (TBCD-STRING) - IMSI of receiver
    #   0x83 = servedIMEI (TBCD-STRING) - IMEI of receiver
    #   0x84 = servedMSISDN (ADDRESS) - MSISDN of receiver (B-party/CALLED)
    #   0x85 = msClassmark (OCTET STRING) - NOT a phone number
    #   0x86 = recordingEntity (ADDRESS) - MSC E.164 number
    #   0x88 = deliveryTime (OCTET STRING timestamp)
    #   0xA9 = smsResult (CHOICE)
    #   0x9F 81 49 = origination (ADDRESS) - sender number (A-party/CALLING)
    # =========================================================================
    if is_smsmt:
        if tag == 0x80:
            pass  # recordType

        elif tag == 0x81:
            # serviceCentre - E.164 address of SMS service center (SMSC)
            sc_addr, _ = decode_address(value)
            if sc_addr and len(sc_addr) <= 20:
                record['MSC_ID'] = sc_addr  # Store SMSC address in MSC_ID

        elif tag == 0x82:
            # servedIMSI - IMSI of the receiving party
            imsi = decode_tbcd(value)
            if imsi and len(imsi) <= 15 and imsi.isdigit():
                record['CHARGED_PARTY_IMSI'] = imsi
                record['IMSI_A'] = imsi
                if len(imsi) >= 5:
                    record['NETWORK_ENTITY'] = imsi[:5]
                    record['OPERATOR_ID'] = imsi[:5]

        elif tag == 0x83:
            # servedIMEI - IMEI of the receiving party
            imei = decode_tbcd(value)
            if imei and len(imei) <= 16:
                record['IMEI_A'] = imei

        elif tag == 0x84:
            # servedMSISDN - MSISDN of the RECEIVING party (B-party = CALLED_NO)
            msisdn, _ = decode_address(value)
            if msisdn and len(msisdn) <= 20:
                record['CALLED_NO'] = msisdn
                record['DIALED_NO'] = msisdn
                record['CHARGED_PARTY_MSISDN'] = msisdn

        elif tag == 0x85:
            # msClassmark - mobile station classmark (NOT a phone number)
            record['MS_CLASSMARK'] = value.hex()

        elif tag == 0x86:
            # recordingEntity - E.164 number of the visited MSC
            msc, _ = decode_address(value)
            if msc and len(msc) <= 20:
                record['NETWORK_ENTITY'] = msc if not record.get('NETWORK_ENTITY') else record['NETWORK_ENTITY']

        elif tag == 0x88:
            # deliveryTime - timestamp when message was sent to the MS
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['START_DATETIME'] = dt
                record['UTC_TIME_OFFSET'] = offset

        elif tag == 0xA9:
            # smsResult - cause of SMS failure
            parse_diagnostics(record, value)

        elif tag == 0x8B:
            # systemType - GERAN/UTRAN/SIP
            record['RAT_TYPE'] = str(decode_unsigned(value))

        elif tag == 0xAC:
            # cAMELSMSInformation — parse serviceKey / camelPhase to derive PREPAID_FLAG
            parse_camel_sms_information(record, value)

        else:
            # For any other standard tags in SMSMT, use common handling
            _process_common_standard_tag(record, tag, value, is_gateway, is_moc)

        return

    # =========================================================================
    # SMS-MO record tags (per Huawei CloudMSOFTX3000 V500R012C35 ASN.1 spec)
    # Tag layout is DIFFERENT from voice MOC/MTC records:
    #   0x80 = recordType (ENUMERATED, value 0x06)
    #   0x81 = servedIMSI (TBCD-STRING) - IMSI of sender
    #   0x82 = servedIMEI (TBCD-STRING) - IMEI of sender
    #   0x83 = servedMSISDN (ADDRESS) - MSISDN of sender (A-party/CALLING)
    #   0x84 = msClassmark (OCTET STRING) - NOT a phone number
    #   0x85 = serviceCentre (ADDRESS) - SMSC E.164 address
    #   0x86 = recordingEntity (ADDRESS) - MSC E.164 number
    #   0x88 = messageReference (OCTET STRING)
    #   0x89 = originationTime (OCTET STRING timestamp)
    #   0x8C = destinationNumber (ADDRESS) - dest number (B-party/CALLED)
    # =========================================================================
    if is_smsmo:
        if tag == 0x80:
            pass  # recordType

        elif tag == 0x81:
            # servedIMSI - IMSI of the sending party
            imsi = decode_tbcd(value)
            if imsi and len(imsi) <= 15 and imsi.isdigit():
                record['CHARGED_PARTY_IMSI'] = imsi
                record['IMSI_A'] = imsi
                if len(imsi) >= 5:
                    record['NETWORK_ENTITY'] = imsi[:5]
                    record['OPERATOR_ID'] = imsi[:5]

        elif tag == 0x82:
            # servedIMEI - IMEI of the sending party
            imei = decode_tbcd(value)
            if imei and len(imei) <= 16:
                record['IMEI_A'] = imei

        elif tag == 0x83:
            # servedMSISDN - MSISDN of the SENDING party (A-party = CALLING_NO)
            msisdn, _ = decode_address(value)
            if msisdn and len(msisdn) <= 20:
                record['CALLING_NO'] = msisdn
                record['CHARGED_PARTY_MSISDN'] = msisdn

        elif tag == 0x84:
            # msClassmark - mobile station classmark (NOT a phone number)
            record['MS_CLASSMARK'] = value.hex()

        elif tag == 0x85:
            # serviceCentre - E.164 address of SMS service center (SMSC)
            sc_addr, _ = decode_address(value)
            if sc_addr and len(sc_addr) <= 20:
                record['MSC_ID'] = sc_addr  # Store SMSC address in MSC_ID

        elif tag == 0x86:
            # recordingEntity - E.164 number of the visited MSC
            msc, _ = decode_address(value)
            if msc and len(msc) <= 20:
                record['NETWORK_ENTITY'] = msc if not record.get('NETWORK_ENTITY') else record['NETWORK_ENTITY']

        elif tag == 0x88:
            # messageReference - reference provided by the MS
            record['CALL_REF'] = decode_octet_string(value)

        elif tag == 0x89:
            # originationTime - time message was received by MSC from subscriber
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['START_DATETIME'] = dt
                record['UTC_TIME_OFFSET'] = offset

        elif tag == 0x8C:
            # destinationNumber - destination subscriber number (B-party = CALLED_NO)
            dest, _ = decode_address(value)
            if dest and len(dest) <= 20:
                record['CALLED_NO'] = dest
                record['DIALED_NO'] = dest

        elif tag == 0x8E:
            # systemType - GERAN/UTRAN/SIP
            record['RAT_TYPE'] = str(decode_unsigned(value))

        elif tag == 0xAA:
            # smsResult - cause of SMS failure
            parse_diagnostics(record, value)

        elif tag == 0xAD:
            # cAMELSMSInformation — parse serviceKey / camelPhase to derive PREPAID_FLAG
            parse_camel_sms_information(record, value)

        else:
            # For any other standard tags in SMSMO, use common handling
            _process_common_standard_tag(record, tag, value, is_gateway, is_moc)

        return

    # =========================================================================
    # Voice / Gateway / Other record tags (original logic)
    # =========================================================================
    if tag == 0x80:
        pass

    elif tag == 0x81:
        if is_gateway:
            if not record.get('CALLING_NO'):
                calling, _ = decode_address(value)
                if calling and len(calling) <= 20 and calling[0].isdigit():
                    record['CALLING_NO'] = calling
        else:
            imsi = decode_tbcd(value)
            if len(imsi) <= 15 and imsi.isdigit():
                record['CHARGED_PARTY_IMSI'] = imsi
                record['IMSI_A'] = imsi
                if len(imsi) >= 5:
                    record['NETWORK_ENTITY'] = imsi[:5]
                    record['OPERATOR_ID'] = imsi[:5]

    elif tag == 0x82:
        if is_gateway:
            if not record.get('CALLED_NO'):
                called, _ = decode_address(value)
                if called and len(called) <= 20 and called[0].isdigit():
                    record['CALLED_NO'] = called
                    record['DIALED_NO'] = called
        else:
            imei = decode_tbcd(value)
            if len(imei) <= 16 and imei.replace('a', '').replace('b', '').replace('c', '').isdigit():
                record['IMEI_A'] = imei

    elif tag == 0x83:
        if is_gateway:
            msc, _ = decode_address(value)
            if msc and len(msc) <= 20:
                record['MSC_ID'] = msc
        else:
            msisdn, _ = decode_address(value)
            if msisdn and len(msisdn) <= 15:
                record['CHARGED_PARTY_MSISDN'] = msisdn
                if record['CALL_DIRECTION'] == 'O' and not record['CALLING_NO']:
                    record['CALLING_NO'] = msisdn

    elif tag == 0x84:
        calling, _ = decode_address(value)
        if calling and len(calling) <= 20:
            record['CALLING_NO'] = calling
            if not record['CHARGED_PARTY_MSISDN']:
                record['CHARGED_PARTY_MSISDN'] = calling

    elif tag == 0x85:
        called, _ = decode_address(value)
        if called and len(called) <= 20:
            record['CALLED_NO'] = called
            record['DIALED_NO'] = called

    elif tag == 0x86:
        if is_gateway:
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                if not record['START_DATETIME']:
                    record['START_DATETIME'] = dt
                    record['UTC_TIME_OFFSET'] = offset
        else:
            trans, _ = decode_address(value)
            if trans and len(trans) <= 20:
                record['DIALED_NO'] = trans
    
    elif tag == 0x87:
        if is_gateway:
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['START_DATETIME'] = dt
                record['UTC_TIME_OFFSET'] = offset
    
    elif tag == 0x88:
        if is_gateway:
            dt, _ = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['CALL_END_DATETIME'] = dt
        else:
            roam, _ = decode_address(value)
            if roam:
                record['ROAMING_ICR_INDICATOR'] = '1'
    
    elif tag == 0x89:
        if is_gateway:
            dur = decode_duration(value)
            if dur < 100000:
                record['CALL_DURATION'] = str(dur)
        else:
            msc, _ = decode_address(value)
            if msc and len(msc) <= 20:
                record['MSC_ID'] = msc
    
    elif tag == 0x8B:
        if is_gateway:
            cause = decode_unsigned(value)
            record['RESULT_CODE'] = CAUSE_FOR_TERM.get(cause, str(cause))
    
    elif tag == 0x91:
        record['MS_CLASSMARK'] = str(decode_unsigned(value))
    
    elif tag == 0x93:
        # seizureTime — time the circuit was seized (before answer).
        # For MOC: do NOT use as START_DATETIME; answerTime (0x96) is the correct start.
        # For non-MOC records: use as START_DATETIME only if not already set.
        dt, offset = decode_bcd_timestamp(value)
        if dt and len(dt) == 14:
            if not is_moc:
                if not record['START_DATETIME']:
                    record['START_DATETIME'] = dt
                    record['UTC_TIME_OFFSET'] = offset

    elif tag == 0x94:
        if is_moc:
            record['MS_CLASSMARK'] = str(decode_unsigned(value))
        else:
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['START_DATETIME'] = dt
                record['UTC_TIME_OFFSET'] = offset

    elif tag == 0x95:
        dt, _ = decode_bcd_timestamp(value)
        if dt and len(dt) == 14:
            record['CALL_END_DATETIME'] = dt

    elif tag == 0x96:
        if is_moc:
            # answerTime — the actual moment the call was answered.
            # This is the correct START_DATETIME for MOC; duration is billed from this point.
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['START_DATETIME'] = dt  # always overwrite — answerTime beats seizureTime
                record['UTC_TIME_OFFSET'] = offset
        else:
            dur = decode_duration(value)
            if dur < 100000:
                record['CALL_DURATION'] = str(dur)
    
    elif tag == 0x97:
        dt, offset = decode_bcd_timestamp(value)
        if dt and len(dt) == 14:
            record['START_DATETIME'] = dt
            record['UTC_TIME_OFFSET'] = offset
    
    elif tag == 0x98:
        if is_moc:
            dt, _ = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['CALL_END_DATETIME'] = dt
    
    elif tag == 0x99:
        if is_moc:
            dur = decode_duration(value)
            if dur < 100000:
                record['CALL_DURATION'] = str(dur)
    
    elif tag == 0x9D:
        record['CALL_REF'] = decode_octet_string(value)
    
    elif tag == 0x9E:
        cause = decode_unsigned(value)
        record['RESULT_CODE'] = CAUSE_FOR_TERM.get(cause, str(cause))
    
    elif tag == 0xA4:
        if is_gateway:
            route = decode_route_name(value)
            if route:
                record['ORIGINATING_TRUNK'] = route
    
    elif tag == 0xA5:
        if is_gateway:
            route = decode_route_name(value)
            if route:
                record['TERMINATING_TRUNK'] = route
    
    elif tag == 0xA7:
        route = decode_route_name(value)
        if route:
            record['ORIGINATING_TRUNK'] = route
    
    elif tag == 0xA8:
        route = decode_route_name(value)
        if route:
            record['TERMINATING_TRUNK'] = route
    
    elif tag == 0xA9:
        parse_location(record, value)
    
    elif tag == 0xAA:
        route = decode_route_name(value)
        if route:
            record['ORIGINATING_TRUNK'] = route
    
    elif tag == 0xAB:
        if len(value) >= 2 and value[0] == 0x81:
            route = decode_route_name(value)
            if route:
                record['TERMINATING_TRUNK'] = route
        else:
            ts, bs = decode_basic_service(value)
            if ts:
                record['TELESERVICE_CODE'] = ts
            if bs:
                record['BEARER_SERVICE_CODE'] = bs
    
    elif tag == 0xAC:
        if is_gateway:
            parse_diagnostics(record, value)
        else:
            parse_location(record, value)
    
    elif tag == 0xAE:
        ts, bs = decode_basic_service(value)
        if ts:
            record['TELESERVICE_CODE'] = ts
        if bs:
            record['BEARER_SERVICE_CODE'] = bs
    
    elif tag == 0xBC:
        parse_diagnostics(record, value)


def _process_common_standard_tag(record: Dict, tag: int, value: bytes, is_gateway: bool, is_moc: bool):
    """Process standard tags common to all record types (location, routes, diagnostics, etc.)"""

    if tag == 0xA7:
        # location (constructed) for SMS records
        parse_location(record, value)

    elif tag == 0xA9:
        parse_location(record, value)

    elif tag == 0xAC:
        if is_gateway:
            parse_diagnostics(record, value)
        else:
            parse_location(record, value)

    elif tag == 0x9D:
        record['CALL_REF'] = decode_octet_string(value)

    elif tag == 0x9E:
        cause = decode_unsigned(value)
        record['RESULT_CODE'] = CAUSE_FOR_TERM.get(cause, str(cause))

    elif tag == 0xBC:
        parse_diagnostics(record, value)


def process_extended_tag(record: Dict, tag: int, value: bytes):
    """Process 9F xx extended tags"""
    
    if tag == 0x1F:
        record['PARTIAL_RECORD_NO'] = str(decode_unsigned(value))
    
    elif tag == 0x2A:
        record['RAT_TYPE'] = str(decode_unsigned(value))

def process_extended_tag_81(record: Dict, tag: int, value: bytes):
    """Process 9F 81 xx extended tags"""
    
    if tag == 0x0C:
        if not record.get('ORIGINATING_TRUNK'):
            route = decode_ia5_string(value)
            if route and all(c.isprintable() for c in route):
                record['ORIGINATING_TRUNK'] = route
    
    elif tag == 0x0D:
        if not record.get('TERMINATING_TRUNK'):
            route = decode_ia5_string(value)
            if route and all(c.isprintable() for c in route):
                record['TERMINATING_TRUNK'] = route
    
    elif tag == 0x11:
        imsi = decode_tbcd(value)
        if imsi and len(imsi) <= 15 and imsi.isdigit() and not record['IMSI_A']:
            record['IMSI_A'] = imsi
    
    elif tag == 0x3C:
        mcc_mnc, lac, cell = decode_global_area_id(value)
        if lac:
            record['LAC_IDENTIFIER'] = lac
        if cell:
            record['CELL_ID_A'] = cell
        if mcc_mnc and not record['NETWORK_ENTITY']:
            record['NETWORK_ENTITY'] = mcc_mnc
    
    elif tag == 0x3E:
        record['RAT_TYPE'] = str(decode_unsigned(value))
    
    elif tag == 0x49:
        call_type = record.get('ORIGINAL_CALL_TYPE', '')
        if call_type == 'SMSMT':
            # 0x9F 81 49 = origination - SMS sender (A-party = CALLING_NO)
            # Per Huawei spec the field is an AddressString that may be either:
            #   - a numeric MSISDN (TON 0/1/2), or
            #   - an alphanumeric sender ID (TON 5, e.g. "OrangeSL", "OrangeMoney")
            # CALLING_NO is allowed to hold both forms.
            origination, _ = decode_address(value)
            if origination and len(origination) <= 20:
                record['CALLING_NO'] = origination
        elif call_type == 'SMSMO':
            # 0x9F 81 49 = not defined for SMSMO in same way; keep generic
            imei = decode_tbcd(value)
            if imei and len(imei) <= 16:
                record['IMEI_B'] = imei
        else:
            # Voice records: this is typically IMEI_B
            imei = decode_tbcd(value)
            if imei and len(imei) <= 16:
                record['IMEI_B'] = imei

    elif tag == 0x4A:
        record['CALL_REF'] = decode_octet_string(value)
    
    elif tag == 0x4B:
        dt, offset = decode_bcd_timestamp(value)
        if dt and len(dt) == 14 and not record['START_DATETIME']:
            record['START_DATETIME'] = dt
            record['UTC_TIME_OFFSET'] = offset
    
    elif tag == 0x4D:
        imsi = decode_tbcd(value)
        if imsi and len(imsi) <= 15 and imsi.isdigit():
            record['IMSI_B'] = imsi
    
    elif tag == 0x60:
        ri = decode_unsigned(value)
        record['ROAMING_ICR_INDICATOR'] = str(ri) if ri else ''
    
    elif tag == 0x68:
        record['NETWORK_RECORD_ID'] = str(decode_unsigned(value))
    
    elif tag == 0x6D:
        record['PARTIAL_RECORD_NO'] = str(decode_unsigned(value))

def _extract_camel_fields(value: bytes):
    """
    Parse a cAMELSMSInformation or cAMELCallLegInformation constructed field.
    Returns (service_key: int, camel_phase: int) using only local variables —
    nothing is written to the record dict.
    """
    service_key = 0
    camel_phase = 0
    try:
        pos = 0
        while pos < len(value) - 1:
            tag = value[pos]
            if pos + 1 >= len(value):
                break
            length, consumed = parse_length(value, pos + 1)
            if length < 0:
                pos += 1
                continue
            val_start = pos + 1 + consumed
            if val_start + length > len(value):
                break
            inner = value[val_start:val_start + length]

            if tag == 0x80:   # serviceKey INTEGER
                service_key = decode_unsigned(inner)
            elif tag == 0x82: # cAMELPhase INTEGER
                camel_phase = decode_unsigned(inner)

            pos = val_start + length
    except Exception:
        pass
    return service_key, camel_phase


def parse_camel_sms_information(record: Dict, value: bytes):
    """Parse cAMELSMSInformation (tag 0xAC/0xAD) and update SUBSCRIBER_CATEGORY."""
    service_key, camel_phase = _extract_camel_fields(value)
    _apply_subscriber_category(record, service_key, camel_phase)


def parse_camel_voice_information(record: Dict, value: bytes):
    """Parse cAMELCallLegInformation BF tags for voice MOC and update SUBSCRIBER_CATEGORY."""
    service_key, camel_phase = _extract_camel_fields(value)
    _apply_subscriber_category(record, service_key, camel_phase)


# Postpaid IMSI prefix — subscribers whose IMSI starts with this prefix are postpaid.
# All other IMSIs are treated as prepaid.
POSTPAID_IMSI_PREFIX = '6190176100'

# OSL_PLMNI = Orange Sierra Leone PLMN ID (MCC 619 + MNC 01).
OSL_PLMNI = '61901'


def _apply_prepaid_flag(record: Dict):
    """
    Derive PREPAID_FLAG from IMSI prefix only.

    Rules:
      • IMSI starts with POSTPAID_IMSI_PREFIX → '0' (postpaid)
      • All other IMSIs                       → '1' (prepaid)
    """
    imsi = record.get('CHARGED_PARTY_IMSI', '') or ''
    if imsi.startswith(POSTPAID_IMSI_PREFIX):
        record['PREPAID_FLAG'] = '0'
    else:
        record['PREPAID_FLAG'] = '1'


def _apply_subscriber_category(record: Dict, service_key: int = 0, camel_phase: int = 0):
    """
    Derive SUBSCRIBER_CATEGORY using CAMEL serviceKey/camelPhase per the CDR spec.

    Rules (per CloudMSOFTX3000 V500R012C35 spec table):
      • MOC / CF / CFW / EMERG (originating voice):
          home IMSI (OSL_PLMNI) + serviceKey=0 + camelPhase=0 → '0' (postpaid)
          home IMSI + serviceKey > 0 or camelPhase > 0         → '1' (prepaid)
      • SMSMO (originating SMS):
          home IMSI + cAMELSMSInformation.serviceKey=0/absent  → '0' (postpaid)
          home IMSI + serviceKey > 0                           → '1' (prepaid)
      • MTC / SMSMT / GWI / GWO / RCF (empty rule in spec)   → '2' (unknown)
      • Roaming IMSI (non-OSL_PLMNI):
          CAMEL present → '1' (prepaid)
          no CAMEL      → '2' (unknown)
    """
    imsi = record.get('CHARGED_PARTY_IMSI', '') or ''
    call_type = record.get('ORIGINAL_CALL_TYPE', '') or ''
    is_home = imsi.startswith(OSL_PLMNI)

    ORIGINATING_TYPES = {'MOC', 'EMERG', 'CFW', 'SMSMO'}
    has_camel = service_key > 0 or camel_phase > 0

    if not is_home:
        record['SUBSCRIBER_CATEGORY'] = '1' if has_camel else '2'
        return

    if has_camel:
        record['SUBSCRIBER_CATEGORY'] = '1'
    elif call_type in ORIGINATING_TYPES:
        record['SUBSCRIBER_CATEGORY'] = '0'
    else:
        # MTC, SMSMT, GWIN, GWOUT, RCF — empty rule, cannot determine
        record['SUBSCRIBER_CATEGORY'] = '2'


def process_bf_tag(record: Dict, tag: int, value: bytes):
    """Process BF xx constructed tags"""

    if tag == 0x1F:
        parse_diagnostics(record, value)

    # BF20 / BF21 / BF22 / BF24 etc. — cAMELCallLegInformation for voice MOC
    elif 0x20 <= tag <= 0x2F:
        parse_camel_voice_information(record, value)

def parse_location(record: Dict, value: bytes):
    """Parse location constructed field"""
    try:
        pos = 0
        while pos < len(value) - 2:
            tag = value[pos]
            
            if tag == 0x80:
                length, consumed = parse_length(value, pos + 1)
                if length > 0:
                    lac = decode_unsigned(value[pos + 1 + consumed:pos + 1 + consumed + length])
                    record['LAC_IDENTIFIER'] = str(lac)
                    pos += 1 + consumed + length
                    continue
            
            elif tag == 0x81:
                length, consumed = parse_length(value, pos + 1)
                if length > 0:
                    cell = decode_unsigned(value[pos + 1 + consumed:pos + 1 + consumed + length])
                    record['CELL_ID_A'] = str(cell)
                    pos += 1 + consumed + length
                    continue
            
            pos += 1
    except:
        pass

def parse_diagnostics(record: Dict, value: bytes):
    """Parse diagnostics field"""
    try:
        if len(value) >= 2:
            pos = 0
            while pos < len(value) - 2:
                tag = value[pos]
                
                if tag == 0x80:
                    length, consumed = parse_length(value, pos + 1)
                    if length > 0:
                        cause = decode_unsigned(value[pos + 1 + consumed:pos + 1 + consumed + length])
                        if not record['RESULT_CODE']:
                            record['RESULT_CODE'] = str(cause)
                        return
                
                pos += 1
    except:
        pass

# =============================================================================
# FILE PROCESSING
# =============================================================================

def parse_length(data: bytes, pos: int) -> Tuple[int, int]:
    """Parse ASN.1 length at position, returns (length, bytes_consumed)"""
    if pos >= len(data):
        return -1, 0
    
    first_len = data[pos]
    
    if first_len < 0x80:
        return first_len, 1
    elif first_len == 0x81:
        if pos + 1 >= len(data):
            return -1, 0
        return data[pos + 1], 2
    elif first_len == 0x82:
        if pos + 2 >= len(data):
            return -1, 0
        return (data[pos + 1] << 8) | data[pos + 2], 3
    elif first_len == 0x83:
        if pos + 3 >= len(data):
            return -1, 0
        return (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3], 4
    elif first_len == 0x84:
        if pos + 4 >= len(data):
            return -1, 0
        return (data[pos + 1] << 24) | (data[pos + 2] << 16) | (data[pos + 3] << 8) | data[pos + 4], 5
    
    return -1, 0

def find_cdr_records(data: bytes) -> List[Tuple[int, bytes]]:
    """Find all CDR records in file"""
    records = []
    pos = 0
    
    if pos < len(data) and data[pos] == 0x30:
        length, consumed = parse_length(data, pos + 1)
        if length > 0:
            pos += 1 + consumed
    
    container_data = None
    
    while pos < len(data) - 2:
        tag = data[pos]
        
        if tag == 0xA0:
            length, consumed = parse_length(data, pos + 1)
            if length >= 0:
                pos += 1 + consumed + length
                continue
        
        elif tag == 0xA1:
            length, consumed = parse_length(data, pos + 1)
            if length > 0:
                content_start = pos + 1 + consumed
                container_data = data[content_start:content_start + length]
                pos = content_start + length
                break
        
        elif tag == 0xA2:
            length, consumed = parse_length(data, pos + 1)
            if length >= 0:
                pos += 1 + consumed + length
                continue
        
        pos += 1
    
    if container_data:
        records = parse_records_from_container(container_data)
    else:
        records = scan_for_records(data)
    
    return records

def parse_records_from_container(data: bytes) -> List[Tuple[int, bytes]]:
    """Parse individual CDR records from container"""
    records = []
    pos = 0
    
    while pos < len(data) - 2:
        tag = data[pos]
        
        if 0xA0 <= tag <= 0xBF:
            length, consumed = parse_length(data, pos + 1)
            
            if length > 0 and length < 10000:
                content_start = pos + 1 + consumed
                if content_start + length <= len(data):
                    records.append((tag, data[content_start:content_start + length]))
                    pos = content_start + length
                    continue
        
        pos += 1
    
    return records

def scan_for_records(data: bytes) -> List[Tuple[int, bytes]]:
    """Scan file for records (fallback method)"""
    records = []
    pos = 0
    
    while pos < len(data) - 5:
        tag = data[pos]
        
        if 0xA0 <= tag <= 0xBF:
            length, consumed = parse_length(data, pos + 1)
            
            if 50 < length < 5000:
                content_start = pos + 1 + consumed
                if content_start + length <= len(data):
                    if content_start + 3 < len(data) and data[content_start] == 0x80:
                        records.append((tag, data[content_start:content_start + length]))
                        pos = content_start + length
                        continue
        
        pos += 1
    
    return records

def process_file(input_file: str, output_file: str = None, output_format: str = 'csv', verbose: bool = False):
    """Process MSC CDR file"""
    filename = os.path.basename(input_file)
    
    with open(input_file, 'rb') as f:
        data = f.read()
    
    records = find_cdr_records(data)
    
    if not records:
        return []
    
    decoded = []
    stats = defaultdict(int)
    
    for record_tag, record_data in records:
        record = decode_msc_record(record_data, filename, record_tag)
        rt_info = RECORD_TAGS.get(record_tag, ('unknown', 'O', 'UNKNOWN', 'U', 'UNKNOWN'))
        record['ORIGINAL_CALL_TYPE'] = rt_info[2]
        decoded.append(record)
        stats[rt_info[0]] += 1
    
    if output_file is None:
        output_file = f"{os.path.splitext(input_file)[0]}_decoded.{output_format}"
    
    if output_format == 'json':
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(decoded, f, indent=2)
    else:
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(decoded)
    
    return decoded

# =============================================================================
# UMP INTEGRATION FUNCTIONS
# =============================================================================

def decode_cdr_file(input_path: str, output_path: str = None) -> Tuple[bool, str, int]:
    """
    Decode a binary CDR file to CSV - for UMP integration
    
    Returns:
        tuple: (success, output_path or error message, record_count)
    """
    if not os.path.exists(input_path):
        return False, f"File not found: {input_path}", 0
    
    try:
        decoded = process_file(input_path, output_path, 'csv', False)
        
        if not decoded:
            return False, "No records decoded from file", 0
        
        if output_path is None:
            output_path = f"{os.path.splitext(input_path)[0]}_decoded.csv"
        
        return True, output_path, len(decoded)
        
    except Exception as e:
        return False, str(e), 0

def is_binary_cdr(filepath: str) -> bool:
    """
    Check if a file is a binary CDR file (vs already decoded CSV)
    """
    try:
        with open(filepath, 'rb') as f:
            header = f.read(512)
        
        # Check for CSV indicators
        try:
            text = header.decode('utf-8')
            # If it's valid UTF-8 and contains CSV-like content
            if any(field in text.upper() for field in ['PREPAID_FLAG', 'CALLING_NO', 'CHARGED_PARTY', 'ORIGINAL_CALL_TYPE']):
                return False
            if 'record_type' in text.lower() or 'calling_number' in text.lower():
                return False
        except:
            pass
        
        # Check for ASN.1/binary indicators
        if header and (header[0] == 0x30 or 0xA0 <= header[0] <= 0xBF):
            return True
        
        # Check for high number of non-printable characters
        non_printable = sum(1 for b in header if b < 32 and b not in (9, 10, 13))
        if non_printable > len(header) * 0.3:
            return True
        
        return False
        
    except:
        return False

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Huawei MSC CDR Decoder (CloudMSoftX3000)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python huawei_decoder.py bFTMSC*.dat
  python huawei_decoder.py input.dat -o output.csv
  python huawei_decoder.py input.dat -f json -v
        """
    )
    
    parser.add_argument('input_file', help='Input CDR file')
    parser.add_argument('-o', '--output', help='Output file path')
    parser.add_argument('-f', '--format', choices=['csv', 'json'], default='csv', help='Output format')
    parser.add_argument('-v', '--verbose', action='store_true', help='Show sample records')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input_file):
        print(f"Error: File not found: {args.input_file}")
        sys.exit(1)
    
    process_file(args.input_file, args.output, args.format, args.verbose)

if __name__ == '__main__':
    main()
