"""
IMS CDR Fields Configuration
==============================
Defines validation rules and enrichment rules for Huawei ATS9900 IMS CDR records.
"""

# =============================================================================
# SECTION 1: VALIDATION_RULES
# =============================================================================

VALIDATION_RULES = {
    'calling_number': {
        'max_length': 100,
        'required': False,
        'description': 'A-party (originating) E.164 number or SIP user',
    },
    'called_number': {
        'max_length': 100,
        'required': False,
        'description': 'B-party (terminating) E.164 number or SIP user',
    },
    'imsi': {
        'max_length': 20,
        'pattern': r'^\d{0,20}$',
        'required': False,
        'description': 'IMSI of the served subscriber',
    },
    'msisdn': {
        'max_length': 20,
        'required': False,
        'description': 'MSISDN of the served subscriber',
    },
    'duration': {
        'required': False,
        'description': 'Session duration in seconds',
    },
    'session_id': {
        'max_length': 255,
        'required': False,
        'description': 'SIP Call-ID (session identifier)',
    },
    'icid': {
        'max_length': 255,
        'required': False,
        'description': 'IMS Charging Identifier',
    },
}


# =============================================================================
# SECTION 2: ENRICHMENT_RULES
# =============================================================================

ENRICHMENT_RULES = {
    # If msisdn is blank, try to derive from calling_number (for originating records)
    'msisdn': {
        'source_field': 'calling_number',
        'transform': 'copy',
        'condition': 'if_empty',
        'description': 'Derive MSISDN from calling number if blank',
    },
}
