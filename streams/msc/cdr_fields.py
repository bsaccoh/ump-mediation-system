"""
MSC CDR Fields Configuration
=============================
Defines input fields, output fields, field mappings, validation rules,
enrichment rules, and short code mappings for Huawei MSC CDR processing.

Based on: CloudMSOFTX3000 V500R012C35 ASN.1 CDR Description
"""

# =============================================================================
# SECTION 1: INPUT_FIELDS
# =============================================================================
# Raw fields from Huawei ASN.1 BER CDR binary, organized by record type.
# Tags correspond to context-specific tags in the ASN.1 structure.

INPUT_FIELDS = {
    # -------------------------------------------------------------------------
    # MOC (Mobile Originated Call) - Record Tag 0xA0
    # -------------------------------------------------------------------------
    'MOC': {
        0x80: 'recordType',
        0x81: 'servedIMSI',
        0x82: 'servedIMEI',
        0x83: 'servedMSISDN',
        0x84: 'callingNumber',
        0x85: 'calledNumber',
        0x86: 'translatedNumber',
        0x88: 'roamingNumber',
        0x89: 'recordingEntity',
        0x91: 'msClassmark',
        0x93: 'seizureTime',
        0x94: 'msClassmark_alt',       # MOC: 0x94 = msClassmark
        0x96: 'answerTime',            # MOC: 0x96 = answerTime
        0x98: 'releaseTime',           # MOC: 0x98 = releaseTime
        0x99: 'callDuration',          # MOC: 0x99 = callDuration
        0x9D: 'callReference',
        0x9E: 'causeForTermination',
        0xA7: 'incomingTrunkGroup',
        0xA8: 'outgoingTrunkGroup',
        0xA9: 'locationAreaAndCell',
        0xAB: 'basicService',
        0xAE: 'basicService_alt',
        0xBC: 'diagnostics',
        # Extended tags (9F xx)
        '9F_1F': 'partialRecordNumber',
        '9F_2A': 'ratType',
        # Extended tags (9F 81 xx)
        '9F81_0C': 'incomingRoute',
        '9F81_0D': 'outgoingRoute',
        '9F81_11': 'additionalIMSI',
        '9F81_3C': 'globalAreaID',
        '9F81_3E': 'ratType_alt',
        '9F81_49': 'servedIMEI_B',
        '9F81_4A': 'networkCallReference',
        '9F81_4B': 'mscIncomingROUTE',
        '9F81_4D': 'calledIMSI',
        '9F81_60': 'roamingIndicator',
        '9F81_68': 'networkRecordID',
        '9F81_6D': 'partialRecordNumber_alt',
    },

    # -------------------------------------------------------------------------
    # MTC (Mobile Terminated Call) - Record Tag 0xA1
    # -------------------------------------------------------------------------
    'MTC': {
        0x80: 'recordType',
        0x81: 'servedIMSI',
        0x82: 'servedIMEI',
        0x83: 'servedMSISDN',
        0x84: 'callingNumber',
        0x85: 'calledNumber',
        0x89: 'recordingEntity',
        0x94: 'answerTime',            # MTC: 0x94 = answerTime
        0x95: 'releaseTime',
        0x96: 'callDuration',          # MTC: 0x96 = callDuration
        0x97: 'seizureTime',
        0x9D: 'callReference',
        0x9E: 'causeForTermination',
        0xA7: 'incomingTrunkGroup',
        0xA8: 'outgoingTrunkGroup',
        0xA9: 'locationAreaAndCell',
        0xAB: 'basicService',
        0xAC: 'locationAreaAndCell_alt',
        0xAE: 'basicService_alt',
        0xBC: 'diagnostics',
        '9F_1F': 'partialRecordNumber',
        '9F_2A': 'ratType',
        '9F81_0C': 'incomingRoute',
        '9F81_0D': 'outgoingRoute',
        '9F81_11': 'additionalIMSI',
        '9F81_3C': 'globalAreaID',
        '9F81_3E': 'ratType_alt',
        '9F81_49': 'servedIMEI_B',
        '9F81_4A': 'networkCallReference',
        '9F81_4D': 'calledIMSI',
        '9F81_60': 'roamingIndicator',
        '9F81_68': 'networkRecordID',
        '9F81_6D': 'partialRecordNumber_alt',
    },

    # -------------------------------------------------------------------------
    # SMSMT (SMS Mobile Terminated) - Record Tag 0xA7
    # Per Huawei spec, tag layout is DIFFERENT from voice records
    # -------------------------------------------------------------------------
    'SMSMT': {
        0x80: 'recordType',
        0x81: 'serviceCentre',          # SMSC E.164 address
        0x82: 'servedIMSI',             # IMSI of receiver
        0x83: 'servedIMEI',             # IMEI of receiver
        0x84: 'servedMSISDN',           # MSISDN of receiver (B-party/CALLED)
        0x85: 'msClassmark',            # Mobile station classmark (NOT a number)
        0x86: 'recordingEntity',        # MSC E.164 number
        0x88: 'deliveryTime',           # Timestamp
        0x8B: 'systemType',             # GERAN/UTRAN/SIP
        0xA7: 'locationAreaAndCell',    # Location info
        0xA9: 'smsResult',              # Cause of SMS failure
        0xAC: 'camelSMSInformation',
        0x9D: 'callReference',
        0x9E: 'causeForTermination',
        0xBC: 'diagnostics',
        # Extended: 9F 81 49 = origination (SMS sender = A-party/CALLING_NO)
        '9F81_49': 'origination',
        '9F81_3C': 'globalAreaID',
        '9F81_68': 'networkRecordID',
    },

    # -------------------------------------------------------------------------
    # SMSMO (SMS Mobile Originated) - Record Tag 0xA6
    # Per Huawei spec, tag layout is DIFFERENT from voice records
    # -------------------------------------------------------------------------
    'SMSMO': {
        0x80: 'recordType',
        0x81: 'servedIMSI',             # IMSI of sender
        0x82: 'servedIMEI',             # IMEI of sender
        0x83: 'servedMSISDN',           # MSISDN of sender (A-party/CALLING)
        0x84: 'msClassmark',            # Mobile station classmark (NOT a number)
        0x85: 'serviceCentre',          # SMSC E.164 address
        0x86: 'recordingEntity',        # MSC E.164 number
        0x88: 'messageReference',       # Reference from MS
        0x89: 'originationTime',        # Timestamp
        0x8C: 'destinationNumber',      # B-party = CALLED_NO
        0x8E: 'systemType',             # GERAN/UTRAN/SIP
        0xA7: 'locationAreaAndCell',    # Location info (for SMS)
        0xAA: 'smsResult',              # Cause of SMS failure
        0xAD: 'camelSMSInformation',
        0x9D: 'callReference',
        0x9E: 'causeForTermination',
        0xBC: 'diagnostics',
        '9F81_3C': 'globalAreaID',
        '9F81_49': 'servedIMEI_B',
        '9F81_68': 'networkRecordID',
    },

    # -------------------------------------------------------------------------
    # GWIN (Incoming Gateway) - Record Tag 0xA3
    # -------------------------------------------------------------------------
    'GWIN': {
        0x80: 'recordType',
        0x81: 'callingNumber',          # Gateway: 0x81 = callingNumber
        0x82: 'calledNumber',           # Gateway: 0x82 = calledNumber
        0x83: 'recordingEntity',        # Gateway: 0x83 = MSC ID
        0x86: 'seizureTime',            # Gateway: 0x86 = seizureTime
        0x87: 'answerTime',             # Gateway: 0x87 = answerTime
        0x88: 'releaseTime',            # Gateway: 0x88 = releaseTime
        0x89: 'callDuration',           # Gateway: 0x89 = callDuration
        0x8B: 'causeForTermination',    # Gateway: 0x8B = causeForTerm
        0xA4: 'incomingTrunkGroup',
        0xA5: 'outgoingTrunkGroup',
        0xAA: 'incomingRoute',
        0xAB: 'outgoingRoute_or_basicService',
        0xAC: 'diagnostics',
        0x9D: 'callReference',
        0x9E: 'causeForTermination_alt',
        0xBC: 'diagnostics_alt',
        '9F_1F': 'partialRecordNumber',
        '9F_2A': 'ratType',
        '9F81_0C': 'incomingRoute_ext',
        '9F81_0D': 'outgoingRoute_ext',
        '9F81_3C': 'globalAreaID',
        '9F81_68': 'networkRecordID',
    },

    # -------------------------------------------------------------------------
    # GWOUT (Outgoing Gateway) - Record Tag 0xA4
    # -------------------------------------------------------------------------
    'GWOUT': {
        0x80: 'recordType',
        0x81: 'callingNumber',
        0x82: 'calledNumber',
        0x83: 'recordingEntity',
        0x86: 'seizureTime',
        0x87: 'answerTime',
        0x88: 'releaseTime',
        0x89: 'callDuration',
        0x8B: 'causeForTermination',
        0xA4: 'incomingTrunkGroup',
        0xA5: 'outgoingTrunkGroup',
        0xAA: 'incomingRoute',
        0xAB: 'outgoingRoute_or_basicService',
        0xAC: 'diagnostics',
        0x9D: 'callReference',
        0x9E: 'causeForTermination_alt',
        0xBC: 'diagnostics_alt',
        '9F_1F': 'partialRecordNumber',
        '9F_2A': 'ratType',
        '9F81_0C': 'incomingRoute_ext',
        '9F81_0D': 'outgoingRoute_ext',
        '9F81_3C': 'globalAreaID',
        '9F81_68': 'networkRecordID',
    },

    # -------------------------------------------------------------------------
    # TRANSIT - Record Tag 0xA5
    # -------------------------------------------------------------------------
    'TRANSIT': {
        0x80: 'recordType',
        0x81: 'servedIMSI',
        0x82: 'servedIMEI',
        0x83: 'servedMSISDN',
        0x84: 'callingNumber',
        0x85: 'calledNumber',
        0x89: 'recordingEntity',
        0x94: 'answerTime',
        0x95: 'releaseTime',
        0x96: 'callDuration',
        0x97: 'seizureTime',
        0x9D: 'callReference',
        0x9E: 'causeForTermination',
        0xA7: 'incomingTrunkGroup',
        0xA8: 'outgoingTrunkGroup',
        0xAB: 'basicService',
        0xBC: 'diagnostics',
        '9F_1F': 'partialRecordNumber',
        '9F81_0C': 'incomingRoute',
        '9F81_0D': 'outgoingRoute',
        '9F81_3C': 'globalAreaID',
        '9F81_68': 'networkRecordID',
    },

    # -------------------------------------------------------------------------
    # ROAMING - Record Tag 0xA2
    # -------------------------------------------------------------------------
    'ROAMING': {
        0x80: 'recordType',
        0x81: 'servedIMSI',
        0x82: 'servedIMEI',
        0x83: 'servedMSISDN',
        0x84: 'callingNumber',
        0x85: 'calledNumber',
        0x88: 'roamingNumber',
        0x89: 'recordingEntity',
        0x94: 'answerTime',
        0x95: 'releaseTime',
        0x96: 'callDuration',
        0x97: 'seizureTime',
        0x9D: 'callReference',
        0x9E: 'causeForTermination',
        0xA7: 'incomingTrunkGroup',
        0xA8: 'outgoingTrunkGroup',
        0xA9: 'locationAreaAndCell',
        0xAB: 'basicService',
        0xBC: 'diagnostics',
        '9F_1F': 'partialRecordNumber',
        '9F_2A': 'ratType',
        '9F81_0C': 'incomingRoute',
        '9F81_0D': 'outgoingRoute',
        '9F81_3C': 'globalAreaID',
        '9F81_60': 'roamingIndicator',
        '9F81_68': 'networkRecordID',
    },

    # -------------------------------------------------------------------------
    # CALL_FORWARDING (formerly CFW) - Record Tag 0xAF
    # Same layout as MOC
    # -------------------------------------------------------------------------
    'CFW': {
        0x80: 'recordType',
        0x81: 'servedIMSI',
        0x82: 'servedIMEI',
        0x83: 'servedMSISDN',
        0x84: 'callingNumber',
        0x85: 'calledNumber',
        0x86: 'translatedNumber',
        0x89: 'recordingEntity',
        0x91: 'msClassmark',
        0x94: 'msClassmark_alt',
        0x96: 'answerTime',
        0x98: 'releaseTime',
        0x99: 'callDuration',
        0x9D: 'callReference',
        0x9E: 'causeForTermination',
        0xA7: 'incomingTrunkGroup',
        0xA8: 'outgoingTrunkGroup',
        0xA9: 'locationAreaAndCell',
        0xAB: 'basicService',
        0xBC: 'diagnostics',
        '9F81_0C': 'incomingRoute',
        '9F81_0D': 'outgoingRoute',
        '9F81_3C': 'globalAreaID',
        '9F81_49': 'servedIMEI_B',
        '9F81_68': 'networkRecordID',
    },

    # -------------------------------------------------------------------------
    # EMERG (Emergency Call) - Record Tag 0xAE
    # Same layout as MOC
    # -------------------------------------------------------------------------
    'EMERG': {
        0x80: 'recordType',
        0x81: 'servedIMSI',
        0x82: 'servedIMEI',
        0x83: 'servedMSISDN',
        0x84: 'callingNumber',
        0x85: 'calledNumber',
        0x89: 'recordingEntity',
        0x91: 'msClassmark',
        0x94: 'msClassmark_alt',
        0x96: 'answerTime',
        0x98: 'releaseTime',
        0x99: 'callDuration',
        0x9D: 'callReference',
        0x9E: 'causeForTermination',
        0xA9: 'locationAreaAndCell',
        0xAB: 'basicService',
        0xBC: 'diagnostics',
        '9F81_3C': 'globalAreaID',
        '9F81_68': 'networkRecordID',
    },
}


# =============================================================================
# SECTION 2: OUTPUT_FIELDS
# =============================================================================
# Standardized mediation output fields. These are the columns written to CSV
# after decoding, and the fields stored in the CDRRecord model.

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
    'TAC', 'NODE_ID',
]


# =============================================================================
# SECTION 3: FIELD_MAPPING
# =============================================================================
# Maps decoded CSV column names (mediation format) to CDRRecord model fields.
# Derived from cdr_processor.py _create_record() logic.

FIELD_MAPPING = {
    # --- Mediation format (uppercase) -> CDRRecord model fields ---
    'ORIGINAL_CALL_TYPE': 'record_type',
    'SERVICE_TYPE': 'service_type',
    'SERVICE_ID': 'service_type_fallback',

    # Parties
    'CALLING_NO': 'calling_number',
    'CHARGED_PARTY_MSISDN': 'calling_number_fallback',
    'CALLED_NO': 'called_number',
    'DIALED_NO': 'called_number_fallback',

    # Identity
    'CHARGED_PARTY_IMSI': 'imsi',
    'IMSI_A': 'imsi_fallback',
    'IMEI_A': 'imei',

    # Duration
    'CALL_DURATION': 'duration',

    # Location
    'CELL_ID_A': 'cell_id',
    'LAC_IDENTIFIER': 'lac',
    'MSC_ID': 'msc_id',

    # Timestamps (mediation format: YYYYMMDDHHMMSS)
    'START_DATETIME': 'start_time',
    'CALL_END_DATETIME': 'end_time',

    # Service type code mapping
    # V -> VOICE, E -> SMS, D -> DATA
    '_SERVICE_TYPE_MAP': {
        'V': 'VOICE',
        'E': 'SMS',
        'D': 'DATA',
    },

    # --- Legacy format (lowercase) -> CDRRecord model fields ---
    'record_type': 'record_type',
    'original_call_type': 'record_type',
    'type': 'record_type',
    'calling_number': 'calling_number',
    'calling_party': 'calling_number',
    'called_number': 'called_number',
    'called_party': 'called_number',
    'served_imsi': 'imsi',
    'imsi': 'imsi',
    'served_imei': 'imei',
    'imei': 'imei',
    'duration': 'duration',
    'call_duration': 'duration',
    'first_cell_id': 'cell_id',
    'cell_id': 'cell_id',
    'first_lac': 'lac',
    'lac': 'lac',
    'served_msc': 'msc_id',
    'msc_address': 'msc_id',
    'start_time': 'start_time',
    'answer_time': 'start_time',
    'end_time': 'end_time',
    'release_time': 'end_time',
}


# =============================================================================
# SECTION 4: VALIDATION_RULES
# =============================================================================
# Per-field validation constraints applied after decoding.

VALIDATION_RULES = {
    'CALLING_NO': {
        'max_length': 50,
        'pattern': r'^[\d\*\#\+a-zA-Z\s\-\.]*$',
        'required': False,
        'description': 'Calling party number or alphanumeric sender ID (A-party)',
    },
    'CALLED_NO': {
        'max_length': 50,
        'pattern': r'^[\d\*\#\+a-zA-Z\s\-\.]*$',
        'required': False,
        'description': 'Called party number (B-party)',
    },
    'CHARGED_PARTY_IMSI': {
        'max_length': 20,
        'required': False,
        'description': 'IMSI of the charged subscriber',
    },
    'CHARGED_PARTY_MSISDN': {
        'max_length': 20,
        'required': False,
        'description': 'MSISDN of the charged subscriber',
    },
    'IMSI_A': {
        'max_length': 20,
        'required': False,
    },
    'IMEI_A': {
        'max_length': 20,
        'required': False,
    },
    'IMSI_B': {
        'max_length': 15,
        'pattern': r'^\d{0,15}$',
        'required': False,
    },
    'IMEI_B': {
        'max_length': 20,
        'required': False,
    },
    'CALL_DURATION': {
        'max_length': 10,
        'pattern': r'^\d+$',
        'required': False,
        'description': 'Duration in seconds',
    },
    'START_DATETIME': {
        'max_length': 14,
        'pattern': r'^\d{14}$',
        'required': False,
        'description': 'YYYYMMDDHHMMSS format',
    },
    'CALL_END_DATETIME': {
        'max_length': 14,
        'pattern': r'^\d{0,14}$',
        'required': False,
    },
    'CELL_ID_A': {
        'max_length': 50,
        'required': False,
    },
    'LAC_IDENTIFIER': {
        'max_length': 20,
        'required': False,
    },
    'MSC_ID': {
        'max_length': 50,
        'required': False,
    },
    'ORIGINAL_CALL_TYPE': {
        'max_length': 20,
        'pattern': r'^[A-Z_]+$',
        'required': False,
        'description': 'Record type: MOC, MTC, SMSMO, SMSMT, GWIN, GWOUT, etc. BIG_DATA uses CALL_TYPE.',
    },
    'NETWORK_ENTITY': {
        'max_length': 50,
        'required': False,
    },
    'OPERATOR_ID': {
        'max_length': 10,
        'required': False,
    },
    'RESULT_CODE': {
        'max_length': 50,
        'required': False,
    },
}


# =============================================================================
# SECTION 5: ENRICHMENT_RULES
# =============================================================================
# Enrichment logic applied after validation. Derives computed fields from raw data.

ENRICHMENT_RULES = {
    'NETWORK_ENTITY': {
        'source_field': 'CHARGED_PARTY_IMSI',
        'transform': 'first_n_digits',
        'n': 5,
        'description': 'MCC+MNC derived from first 5 digits of IMSI',
        'condition': 'if_empty',
    },
    'OPERATOR_ID': {
        'source_field': 'CHARGED_PARTY_IMSI',
        'transform': 'first_n_digits',
        'n': 5,
        'description': 'Operator identifier from IMSI prefix',
        'condition': 'if_empty',
    },
    'CALL_END_DATETIME': {
        'source_field': 'START_DATETIME',
        'transform': 'copy',
        'description': 'If no end time, copy start time (for SMS instant delivery)',
        'condition': 'if_empty',
    },
    'CALL_DURATION': {
        'source_field': None,
        'transform': 'default_value',
        'default': '1',
        'description': 'SMS records default to duration 1',
        'condition': 'if_sms_and_empty',
    },
    'TELESERVICE_CODE': {
        'source_field': None,
        'transform': 'default_value',
        'default': '22',
        'description': 'SMS records default to teleservice code 22',
        'condition': 'if_sms_and_empty',
    },
}
