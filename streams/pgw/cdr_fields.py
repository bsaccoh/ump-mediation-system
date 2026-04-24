"""
PGW CDR Field Definitions
=========================
Defines input fields (from ASN.1 BER decoding), output fields (for PGWRecord model),
field mappings, validation rules, and enrichment rules for PGW CDR processing.

Based on 3GPP TS 32.298 PGW-CDR / SGW-CDR field specifications and
Huawei PGW CDR implementation.

Author: B-TEC Digital Solution Ltd
Version: 1.0
"""


# =============================================================================
# SECTION 1: INPUT_FIELDS
# Raw fields from PGW ASN.1 BER CDR as decoded by PGWDecoder.
# =============================================================================

INPUT_FIELDS = {
    'record_type': {
        'tag': 0x80, 'type': 'integer',
        'description': 'CDR record type identifier (79=PGW-CDR, 78=SGW-CDR, 85=Huawei PGW)',
    },
    'served_imsi': {
        'tag': 0x83, 'type': 'tbcd',
        'description': 'IMSI of the served subscriber (TBCD encoded)',
    },
    'charging_id': {
        'tag': 0x85, 'type': 'integer',
        'description': 'Charging ID assigned by the PGW for this bearer',
    },
    'apn': {
        'tag': 0x87, 'type': 'string',
        'description': 'Access Point Name (network identifier)',
    },
    'pdn_type': {
        'tag': 0x88, 'type': 'integer',
        'description': 'PDN type (0/1=IPv4, 2=IPv6, 3=IPv4v6)',
    },
    'record_opening_time': {
        'tag': 0x8c, 'type': 'timestamp',
        'description': 'Timestamp when the CDR record was opened',
    },
    'cause_for_rec_closing': {
        'tag': 0x8e, 'type': 'integer',
        'description': 'Reason the CDR record was closed',
    },
    'node_id': {
        'tag': 0x92, 'type': 'string',
        'description': 'Node ID in string form (PGW hostname or identifier)',
    },
    'local_sequence_number': {
        'tag': 0x94, 'type': 'integer',
        'description': 'Local sequence number within the node',
    },
    'served_msisdn': {
        'tag': 0x96, 'type': 'tbcd',
        'description': 'MSISDN of the served subscriber (TBCD encoded)',
    },
    'served_imeisv': {
        'tag': 0x9d, 'type': 'tbcd',
        'description': 'IMEISV of the served UE (TBCD encoded)',
    },
    'rat_type': {
        'tag': 0x9e, 'type': 'integer',
        'description': 'Radio Access Technology type (6=EUTRAN/LTE)',
    },
    'start_time': {
        'tag': '9f26', 'type': 'timestamp',
        'description': 'Session start time',
    },
    'stop_time': {
        'tag': '9f27', 'type': 'timestamp',
        'description': 'Session stop time',
    },
}


# =============================================================================
# SECTION 2: OUTPUT_FIELDS
# Standardized output fields mapped to PGWRecord model columns.
# =============================================================================

OUTPUT_FIELDS = {
    'record_type': {
        'type': 'string', 'max_length': 20,
        'description': 'Record type name (PGW-CDR or SGW-CDR)',
    },
    'service_type': {
        'type': 'string', 'max_length': 10, 'default': 'DATA',
        'description': 'Always DATA for PGW records',
    },
    'calling_number': {
        'type': 'string', 'max_length': 20,
        'description': 'MSISDN of the subscriber',
    },
    'called_number': {
        'type': 'string', 'max_length': 100,
        'description': 'APN used for the data session',
    },
    'imsi': {
        'type': 'string', 'max_length': 20,
        'description': 'IMSI of the subscriber',
    },
    'imei': {
        'type': 'string', 'max_length': 20,
        'description': 'IMEI of the UE (first 14 digits of IMEISV)',
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
    'apn': {
        'type': 'string', 'max_length': 100,
        'description': 'Access Point Name',
    },
    'rat_type': {
        'type': 'string', 'max_length': 20,
        'description': 'Radio Access Technology name',
    },
    'charging_id': {
        'type': 'bigint',
        'description': 'PGW charging identifier',
    },
}


# =============================================================================
# SECTION 3: FIELD_MAPPING
# Maps PGW decoder output field names -> PGWRecord field names.
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
    'cell_id':          ('cell_id', 'eci'),
    'lac':              ('tac', 'routing_area'),
    'node_id':          'node_id',
    'apn':              'apn',
    'rat_type':         'rat_type_name',
    'charging_id':      'charging_id',
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
    'apn': {
        'required': False,
        'max_length': 100,
        'pattern': r'^[a-zA-Z0-9._\-]+$',
        'description': 'APN must contain only valid characters',
    },
    'duration': {
        'required': False,
        'type': 'integer',
        'min_value': 0,
        'max_value': 604800,
        'description': 'Duration must be between 0 and 604800 seconds (7 days)',
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
        'description': 'IMEI/IMEISV max 20 chars',
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
        'description': 'PGW records are always DATA',
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
}
