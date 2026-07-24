"""PGW CDR output header mapping (see streams/ims/headers.py for the pattern)."""

PGW_HEADERS = [
    ('Record Type',         'record_type'),
    ('MSISDN',              'calling_number'),
    ('APN',                 'apn'),
    ('IMSI',                'imsi'),
    ('IMEI',                'imei'),
    ('Start Time',          'start_time'),
    ('End Time',            'end_time'),
    ('Duration (s)',        'duration'),
    ('Upload (bytes)',      'data_volume_up'),
    ('Download (bytes)',    'data_volume_down'),
    ('Total Data (MB)',     'data_volume_mb'),
    ('RAT Type',            'rat_type'),
    ('PDN Type',            'pdn_type'),
    ('PGW Address',         'pgw_address'),
    ('SGW Address',         'sgw_address'),
    ('Node ID',             'node_id'),
    ('Cell ID',             'cell_id'),
    ('TAC',                 'lac'),
    ('Serving PLMN',        'serving_plmn'),
    ('Cause',               'cause_for_closing'),
    ('Roaming',             'is_roaming'),
    ('Charging ID',         'charging_id'),
    ('Status',              'status'),
]


def build_mapping_json() -> dict:
    return {h: s for h, s in PGW_HEADERS}
