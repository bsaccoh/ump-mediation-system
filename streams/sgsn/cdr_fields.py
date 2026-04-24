"""
SGSN CDR Field Definitions
===========================
Defines input fields (from ASN.1 BER decoding), output fields (for SGSNRecord),
field mappings, validation rules, and enrichment rules for SGSN CDR processing.

Based on 3GPP TS 32.298 S-CDR (sgsnPDPRecord) field specifications and
Huawei SGSN CDR implementation.

Author: B-TEC Digital Solution Ltd
Version: 1.0
"""


# =============================================================================
# SECTION 1: INPUT_FIELDS
# Raw fields from SGSN ASN.1 BER CDR as decoded by SGSNDecoder.
# =============================================================================

INPUT_FIELDS = {
    'record_type': {
        'tag': 0x80, 'type': 'integer',
        'description': 'CDR record type (18=S-CDR, 20=SMS-MO, 21=SMS-MT)',
    },
    'network_initiation': {
        'tag': 0x80, 'type': 'boolean',
        'description': 'Network-initiated PDP context flag',
    },
    'served_imsi': {
        'tag': 0x83, 'type': 'tbcd',
        'description': 'IMSI of the served subscriber (TBCD encoded)',
    },
    'served_imei': {
        'tag': 0x85, 'type': 'tbcd',
        'description': 'IMEI of the UE (TBCD encoded)',
    },
    'sgsn_address': {
        'tag': 0x84, 'type': 'ip_address',
        'description': 'SGSN IP address (GSNAddress)',
    },
    'routing_area': {
        'tag': 0x87, 'type': 'bytes',
        'description': 'Routing Area Code',
    },
    'location_area_code': {
        'tag': 0x88, 'type': 'integer',
        'description': 'Location Area Code (2 bytes)',
    },
    'cell_identifier': {
        'tag': 0x89, 'type': 'integer',
        'description': 'Cell identifier (2 bytes)',
    },
    'charging_id': {
        'tag': 0x8A, 'type': 'integer',
        'description': 'Charging ID assigned by SGSN',
    },
    'ggsn_address_used': {
        'tag': 0x8B, 'type': 'ip_address',
        'description': 'GGSN IP address used for this PDP context',
    },
    'access_point_name_ni': {
        'tag': 0x8C, 'type': 'string',
        'description': 'APN Network Identifier',
    },
    'pdp_type': {
        'tag': 0x8D, 'type': 'bytes',
        'description': 'PDP type (PPP, IPv4, IPv6, IPv4v6)',
    },
    'served_pdp_address': {
        'tag': 0x8E, 'type': 'ip_address',
        'description': 'PDP address assigned to UE',
    },
    'list_of_traffic_volumes': {
        'tag': 0xAF, 'type': 'sequence',
        'description': 'List of traffic volume changes (uplink + downlink per interval)',
    },
    'record_opening_time': {
        'tag': 0x90, 'type': 'timestamp',
        'description': 'Timestamp when CDR record was opened (BCD 9 bytes)',
    },
    'duration': {
        'tag': 0x91, 'type': 'integer',
        'description': 'Session duration in seconds',
    },
    'cause_for_rec_closing': {
        'tag': 0x93, 'type': 'integer',
        'description': 'Cause for record closing (normalRelease, timeLimit, etc.)',
    },
    'record_sequence_number': {
        'tag': 0x95, 'type': 'integer',
        'description': 'Sequence number of partial CDR',
    },
    'node_id': {
        'tag': 0x96, 'type': 'string',
        'description': 'SGSN node identifier (hostname)',
    },
    'local_sequence_number': {
        'tag': 0x98, 'type': 'integer',
        'description': 'Local sequence number within node',
    },
    'apn_selection_mode': {
        'tag': 0x99, 'type': 'integer',
        'description': 'APN selection mode enum',
    },
    'access_point_name_oi': {
        'tag': 0x9A, 'type': 'string',
        'description': 'APN Operator Identifier',
    },
    'served_msisdn': {
        'tag': 0x9B, 'type': 'tbcd',
        'description': 'MSISDN of the served subscriber (TBCD encoded)',
    },
    'charging_characteristics': {
        'tag': 0x9C, 'type': 'bytes',
        'description': 'Charging characteristics flags (2 bytes)',
    },
    'rat_type': {
        'tag': '9f4b', 'type': 'integer',
        'description': 'Radio Access Technology type (1=UTRAN/3G, 2=GERAN/2G)',
    },
}


# =============================================================================
# SECTION 2: OUTPUT_FIELDS
# Standardized output fields mapped to SGSNRecord model columns.
# =============================================================================

OUTPUT_FIELDS = {
    'PREPAID_FLAG': {
        'type': 'string', 'max_length': 1,
        'description': '1=PREPAID, 0=POSTPAID (based on IMSI prefix)',
    },
    'SUBSCRIBER_CATEGORY': {
        'type': 'string', 'max_length': 1,
        'description': '1=PREPAID, 0=POSTPAID, 2=UNKNOWN',
    },
    'record_type': {
        'type': 'string', 'max_length': 20,
        'description': 'Record type name (SGSN-CDR, SGSN-SMOMO, SGSN-SMTC)',
    },
    'service_type': {
        'type': 'string', 'max_length': 10, 'default': 'DATA',
        'description': 'DATA or SMS',
    },
    'calling_number': {
        'type': 'string', 'max_length': 20,
        'description': 'MSISDN of the subscriber',
    },
    'called_number': {
        'type': 'string', 'max_length': 100,
        'description': 'APN for data, or destination number for SMS',
    },
    'imsi': {
        'type': 'string', 'max_length': 20,
        'description': 'IMSI of the subscriber',
    },
    'imei': {
        'type': 'string', 'max_length': 20,
        'description': 'IMEI of the UE',
    },
    'apn': {
        'type': 'string', 'max_length': 100,
        'description': 'Access Point Name (network identifier)',
    },
    'pdp_type': {
        'type': 'string', 'max_length': 20,
        'description': 'PDP context type (IPv4, IPv6, PPP)',
    },
    'sgsn_address': {
        'type': 'string', 'max_length': 50,
        'description': 'SGSN IP address',
    },
    'ggsn_address': {
        'type': 'string', 'max_length': 50,
        'description': 'GGSN IP address used',
    },
    'rat_type': {
        'type': 'string', 'max_length': 20,
        'description': 'Radio Access Technology name (GERAN, UTRAN)',
    },
    'start_time': {
        'type': 'datetime',
        'description': 'Session start time',
    },
    'end_time': {
        'type': 'datetime',
        'description': 'Session end time',
    },
    'duration': {
        'type': 'integer',
        'description': 'Session duration in seconds',
    },
    'data_volume_up': {
        'type': 'bigint', 'default': 0,
        'description': 'Total uplink data volume in bytes',
    },
    'data_volume_down': {
        'type': 'bigint', 'default': 0,
        'description': 'Total downlink data volume in bytes',
    },
    'cell_id': {
        'type': 'string', 'max_length': 20,
        'description': 'Cell identifier',
    },
    'lac': {
        'type': 'string', 'max_length': 20,
        'description': 'Location Area Code',
    },
    'rac': {
        'type': 'string', 'max_length': 20,
        'description': 'Routing Area Code',
    },
    'cause_for_closing': {
        'type': 'string', 'max_length': 50,
        'description': 'Cause for record closing (human-readable)',
    },
    'charging_id': {
        'type': 'string', 'max_length': 50,
        'description': 'SGSN charging identifier',
    },
    'node_id': {
        'type': 'string', 'max_length': 100,
        'description': 'SGSN node identifier',
    },
    'charging_characteristics': {
        'type': 'string', 'max_length': 10,
        'description': 'Charging characteristics flags',
    },
    'serving_plmn': {
        'type': 'string', 'max_length': 10,
        'description': 'Serving PLMN (MCC+MNC)',
    },
    'is_roaming': {
        'type': 'boolean', 'default': False,
        'description': 'True if subscriber is roaming',
    },
}


# =============================================================================
# SECTION 3: FIELD_MAPPING
# Maps SGSNDecoder output field names -> SGSNRecord field names.
# =============================================================================

FIELD_MAPPING = {
    'record_type':      'record_type_name',
    'service_type':     'service_type',
    'calling_number':   'msisdn',
    'called_number':    'apn',
    'imsi':             'imsi',
    'imei':             'imei',
    'start_time':       ('start_time', 'record_opening_time'),
    'end_time':         'stop_time',
    'duration':         'duration_seconds',
    'data_volume_up':   'total_data_volume_uplink',
    'data_volume_down': 'total_data_volume_downlink',
    'apn':              'apn',
    'pdp_type':         'pdp_type',
    'sgsn_address':     'sgsn_address',
    'ggsn_address':     'ggsn_address',
    'rat_type':         'rat_type_name',
    'cell_id':          'cell_id',
    'lac':              'lac',
    'rac':              'rac',
    'charging_id':      'charging_id',
    'node_id':          'node_id',
    'serving_plmn':     ('serving_plmn', 'location_plmn'),
    'is_roaming':       'is_roaming',
}


# =============================================================================
# SECTION 4: VALIDATION_RULES
# =============================================================================

VALIDATION_RULES = {
    'imsi': {
        'required': False,
        'max_length': 15,
        'pattern': r'^\d{10,15}$',
        'description': 'IMSI must be 10-15 digits',
    },
    'calling_number': {
        'required': False,
        'max_length': 15,
        'pattern': r'^\d{7,15}$',
        'description': 'MSISDN must be 7-15 digits',
    },
    'duration': {
        'required': False,
        'type': 'integer',
        'min_value': 0,
        'max_value': 604800,
        'description': 'Duration must be 0-604800 seconds (7 days)',
    },
    'data_volume_up': {
        'required': False,
        'type': 'integer',
        'min_value': 0,
        'description': 'Uplink volume must be non-negative',
    },
    'data_volume_down': {
        'required': False,
        'type': 'integer',
        'min_value': 0,
        'description': 'Downlink volume must be non-negative',
    },
    'imei': {
        'required': False,
        'max_length': 20,
        'description': 'IMEI max 20 chars',
    },
}


# =============================================================================
# SECTION 5: ENRICHMENT_RULES
# =============================================================================

ENRICHMENT_RULES = {
    'service_type': {
        'transform': 'default_value',
        'default': 'DATA',
        'condition': 'if_empty',
        'description': 'Default to DATA for non-SMS SGSN records',
    },
    'calling_number': {
        'transform': 'copy',
        'source_field': 'msisdn',
        'condition': 'if_empty',
        'description': 'Use MSISDN as calling number',
    },
    'called_number': {
        'transform': 'copy',
        'source_field': 'apn',
        'condition': 'if_empty',
        'description': 'Use APN as called number for data sessions',
    },
    'PREPAID_FLAG': {
        'transform': 'imsi_prefix',
        'prefix': '6190176100',
        'match_value': '0',
        'default_value': '1',
        'description': 'IMSI starting with 6190176100 = POSTPAID (0), else PREPAID (1)',
    },
    'SUBSCRIBER_CATEGORY': {
        'transform': 'imsi_prefix',
        'prefix': '6190176100',
        'match_value': '0',
        'default_value': '1',
        'description': 'Same as PREPAID_FLAG; 2=UNKNOWN when IMSI absent',
    },
}
