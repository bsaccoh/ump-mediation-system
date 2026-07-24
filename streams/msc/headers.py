"""
MSC CDR output header mapping.

Each tuple is (output_header, model_field).  Same convention as
``streams/ims/headers.py``.  Used by ``dashboard/views.py::cdr_export``
to generate CSV.
"""

MSC_HEADERS = [
    ('Record Type',         'record_type'),
    ('Service Type',        'service_type'),
    ('Direction',           'call_direction'),
    ('Calling Number',      'calling_number'),
    ('Called Number',       'called_number'),
    ('Dialed Number',       'dialed_number'),
    ('Charged MSISDN',      'charged_msisdn'),
    ('Subscriber Type',     'prepaid_flag'),
    ('IMSI',                'imsi'),
    ('IMEI',                'imei'),
    ('Start Time',          'start_time'),
    ('End Time',            'end_time'),
    ('Duration (s)',        'duration'),
    ('MSC ID',              'msc_id'),
    ('SMSC GT',             'smsc_address'),
    ('Cell ID',             'cell_id'),
    ('LAC',                 'lac'),
    ('RAT Type',            'rat_type'),
    ('Originating Trunk',   'originating_trunk'),
    ('Terminating Trunk',   'terminating_trunk'),
    ('Teleservice Code',    'teleservice_code'),
    ('Result Code',         'result_code'),
    ('Roaming',             'roaming_indicator'),
    ('Call Category',       'call_category'),
    ('Forwarded To',        'forwarded_number'),
    ('Redirecting Number',  'redirecting_number'),
    ('Network Record ID',   'network_record_id'),
    ('Call Reference',      'call_reference'),
    ('Status',              'status'),
]


def build_mapping_json() -> dict:
    return {h: s for h, s in MSC_HEADERS}
