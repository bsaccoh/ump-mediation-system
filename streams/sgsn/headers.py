"""SGSN CDR output header mapping."""

SGSN_HEADERS = [
    ('Record Type',         'record_type'),
    ('Service Type',        'service_type'),
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
    ('PDP Type',            'pdp_type'),
    ('SGSN Address',        'sgsn_address'),
    ('GGSN Address',        'ggsn_address'),
    ('Node ID',             'node_id'),
    ('Cell ID',             'cell_id'),
    ('LAC',                 'lac'),
    ('RAC',                 'rac'),
    ('Serving PLMN',        'serving_plmn'),
    ('Cause',               'cause_for_closing'),
    ('Roaming',             'is_roaming'),
    ('Charging ID',         'charging_id'),
    ('Status',              'status'),
]


def build_mapping_json() -> dict:
    return {h: s for h, s in SGSN_HEADERS}
