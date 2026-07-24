"""
IMS Output Header Mapping
==========================
Defines the final CSV column order and source-field mapping for IMS CDR
output, per the user-supplied spec (see docs).

Each tuple is (output_header, model_field).  Empty source means the
column is reserved but not yet populated by the decoder.
"""

IMS_HEADERS_HUAWEI = [
    # --- Record identity -----------------------------------------------
    ('Record-Type',                          'record_type'),
    ('SIP-Method',                           'sip_method'),
    ('Role-Of-Node',                         'role_of_node'),
    ('Node-Address',                         'node_address'),
    ('Session-ID',                           'session_id'),

    # --- Parties (bare numbers — no tel:/sip: prefix) -------------------
    ('Calling-Party',                        'calling_number'),
    ('Called-Party',                         'called_number'),

    # --- Timing --------------------------------------------------------
    ('Service-Request-Time-Stamp',           'request_time'),
    ('Service-Delivery-Start-Time-Stamp',    'start_time'),
    ('Service-Delivery-End-Time-Stamp',      'end_time'),
    ('Record-Opening-Time',                  'start_time'),
    ('Record-Closure-Time',                  'end_time'),
    ('Duration',                             'duration'),
    ('Ringing-Duration',                     'ringing_duration'),

    # --- Call classification / closing --------------------------------
    ('Call-Property',                        'call_property'),
    ('Cause-For-Record-Closing',             'cause_for_closing'),
    ('Service-Reason-Return-Code',           'service_reason_code'),
    ('IMS-Charging-Identifier',              'icid'),
    ('Charging-Category',                    'charging_category'),
    ('Online-Charging-Flag',                 'online_charging_flag'),
    ('Charged-Party',                        'charged_party'),

    # --- Media identifier ---------------------------------------------
    ('SDP-Media-Identifier',                 'sdp_media_identifier'),

    # --- Access network (just the spec, e.g. '3GPP-E-UTRAN') -----------
    ('Access-Network-Information',           'access_network_spec'),

    # --- Subscriber identifiers ---------------------------------------
    ('IMSI',                                 'imsi'),
    ('IMEI',                                 'private_user_equipment_info_value'),

    # --- Codec / RTP from SDP -----------------------------------------
    ('Codec',                                'codec'),
    ('RTP-Port',                             'rtp_port'),

    # --- Network elements --------------------------------------------
    ('MSC-Number',                           'msc_number'),
    ('VLR-Number',                           'vlr_number'),

    # --- IN / CAMEL --------------------------------------------------
    ('Service-Key',                          'in_service_key'),
    ('Call-Reference-Number',                'in_call_reference'),
    ('SCF-Address',                          'in_scf_address'),

    # --- MMTel -------------------------------------------------------
    ('Subscriber-Role',                      'subscriber_role'),
    ('Supplementary-Service',                'supplementary_service'),

    # --- Flags / type -------------------------------------------------
    ('Retransmission',                       'retransmission'),
    ('Accounting-Record-Type',               'accounting_record_type'),
    ('Call-Type',                            'call_type'),

    # --- SIP-Response-Time (= 200 OK / answer timestamp) --------------
    ('SIP-Response-Time',                    'start_time'),

    # --- Raw URI form party addresses (keep tel:/sip: prefix) ---------
    ('Requested-Party-Address',              'requested_party_address_raw'),
    ('Dialled-Party-Address',                'dialled_party_address'),
    ('Called-Asserted-Identity',             'called_asserted_identity_raw'),

    # --- Inter-operator identifiers -----------------------------------
    ('Inter-Operator-Originating-IOI',       'originating_ioi'),
    ('Inter-Operator-Terminating-IOI',       'terminating_ioi'),

    # --- Equipment-info type label ------------------------------------
    ('Private-User-Equipment-Info-Type',     'private_user_equipment_info_type'),

    # --- TADS / SDP details -------------------------------------------
    ('TADS-Indication',                      'tads_indication'),
    ('SIP Codec Payload',                    'sip_codec_payload'),
    ('Telephone Event Payload',              'telephone_event_payload'),
    ('Media Type',                           'media_type'),
    ('RTP Protocol',                         'rtp_protocol'),
]


def build_mapping_json() -> dict:
    return {h: s for h, s in IMS_HEADERS_HUAWEI}
