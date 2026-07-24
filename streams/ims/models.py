"""
IMS CDR Record Model
=====================
Stores decoded and processed IMS (ATS9900) CDR records.
Covers VoLTE, VoBB, FMC and IMS supplementary services.
"""
from django.db import models


class IMSRecord(models.Model):
    """A single decoded IMS CDR record from the Huawei ATS9900."""

    class Status(models.TextChoices):
        VALID   = 'VALID',   'Valid'
        INVALID = 'INVALID', 'Invalid'
        DUPLICATE = 'DUPLICATE', 'Duplicate'

    # -------------------------------------------------------------------------
    # Foreign keys
    # -------------------------------------------------------------------------
    file = models.ForeignKey(
        'collection.CDRFile', on_delete=models.CASCADE, db_constraint=False,
        related_name='ims_records', db_index=True,
    )
    source = models.ForeignKey(
        'collection.DataSource', on_delete=models.SET_NULL, db_constraint=False,
        null=True, blank=True, db_index=True,
    )
    # CDR-pair correlation — the matching ORIG ↔ TERM record (shared ICID)
    paired_record = models.ForeignKey(
        'self', on_delete=models.SET_NULL,
        null=True, blank=True, db_index=True,
        related_name='paired_records_reverse',
        help_text='Linked CDR pair (originating ↔ terminating record with the same ICID).',
    )

    # -------------------------------------------------------------------------
    # Record identity
    # -------------------------------------------------------------------------
    record_type    = models.CharField(max_length=50, db_index=True)  # ATS_CDR, EVENT_CDR …
    service_type   = models.CharField(max_length=30, default='VOICE', db_index=True)  # VOICE / SMS / EVENT
    role_of_node   = models.CharField(max_length=20, blank=True)     # ORIGINATING / TERMINATING
    sip_method     = models.CharField(max_length=20, blank=True)     # INVITE, REGISTER, SUBSCRIBE …
    session_id     = models.CharField(max_length=255, blank=True, db_index=True)  # SIP Call-ID
    icid           = models.CharField(max_length=255, blank=True, db_index=True)  # IMS Charging Identifier

    # -------------------------------------------------------------------------
    # Party information
    # -------------------------------------------------------------------------
    calling_number  = models.CharField(max_length=100, blank=True, db_index=True)  # A-party
    called_number   = models.CharField(max_length=100, blank=True, db_index=True)  # B-party
    dialed_number   = models.CharField(max_length=100, blank=True)                 # original dialed
    charged_party   = models.CharField(max_length=100, blank=True)                 # IMPU of charged sub
    calling_sip_uri = models.CharField(max_length=255, blank=True)
    called_sip_uri  = models.CharField(max_length=255, blank=True)

    # -------------------------------------------------------------------------
    # Subscriber identity (served subscriber — the charged party)
    # -------------------------------------------------------------------------
    imsi    = models.CharField(max_length=20, blank=True, db_index=True)
    msisdn  = models.CharField(max_length=20, blank=True, db_index=True)
    imei    = models.CharField(max_length=20, blank=True)
    private_user_identity = models.CharField(max_length=255, blank=True)  # IMPI (sip:…@home-domain)
    prepaid_flag = models.CharField(max_length=10, blank=True, db_index=True)

    # -------------------------------------------------------------------------
    # A-party (calling) identifiers — distinct from the served subscriber
    # for terminating records and for VoLTE-to-VoLTE flows.
    # -------------------------------------------------------------------------
    calling_imsi  = models.CharField(max_length=20, blank=True, db_index=True)
    calling_min   = models.CharField(max_length=50, blank=True)   # Mobile Identification No / canonical MSISDN
    calling_impi  = models.CharField(max_length=255, blank=True)  # SIP private identity of A-party

    # -------------------------------------------------------------------------
    # B-party (called) identifiers
    # -------------------------------------------------------------------------
    called_imsi   = models.CharField(max_length=20, blank=True, db_index=True)
    called_min    = models.CharField(max_length=50, blank=True)
    called_impi   = models.CharField(max_length=255, blank=True)

    # -------------------------------------------------------------------------
    # Media / session characteristics
    # -------------------------------------------------------------------------
    media_type             = models.CharField(max_length=30, blank=True)   # audio / video / text / fax …
    served_subscriber_type = models.CharField(max_length=20, blank=True)   # IMS / CS / PS
    access_network_info    = models.CharField(max_length=512, blank=True)  # P-Access-Network-Info raw

    # -------------------------------------------------------------------------
    # Access network — parsed from access_network_info
    # -------------------------------------------------------------------------
    technology        = models.CharField(max_length=20, blank=True, db_index=True)  # EUTRAN / UTRAN / GERAN / NR / WLAN
    serving_plmn      = models.CharField(max_length=10, blank=True, db_index=True)  # MCC+MNC (e.g. 61901)
    tac               = models.CharField(max_length=20, blank=True)   # LTE Tracking Area Code (hex)
    lac               = models.CharField(max_length=20, blank=True)   # 2G/3G Location Area Code
    cell_id           = models.CharField(max_length=30, blank=True)   # ECI / Cell-ID (hex or decimal)
    enodeb_id         = models.CharField(max_length=20, blank=True)   # eNodeB-ID (upper 20 bits of ECI)
    ue_ip             = models.GenericIPAddressField(null=True, blank=True)
    apn               = models.CharField(max_length=100, blank=True, db_index=True)

    # -------------------------------------------------------------------------
    # Call forwarding / diversion
    # -------------------------------------------------------------------------
    forwarded_number    = models.CharField(max_length=100, blank=True)  # target of CF (where it's going)
    redirecting_number  = models.CharField(max_length=100, blank=True)  # original B before CF

    # -------------------------------------------------------------------------
    # Additional Huawei ATS9900 fields (from real VoLTE CDR sample)
    # -------------------------------------------------------------------------
    retransmission              = models.BooleanField(default=False)
    accounting_record_type      = models.CharField(max_length=30, blank=True)   # SIS_RECORD / EVENT_RECORD …
    sdp_media_identifier        = models.CharField(max_length=30, blank=True)   # VOICECALL / VIDEOCALL / MESSAGE
    sdp_media_components        = models.TextField(blank=True)                  # Raw SDP dump (audio codec, ports …)
    tads_indication             = models.CharField(max_length=20, blank=True)   # CS / PS / IMS
    incomplete_cdr_indication   = models.CharField(max_length=80, blank=True)   # aCRStartLost / aCRInterimLost / aCRStopLost
    is_volte_call_type          = models.CharField(max_length=20, blank=True)
    requested_party_address     = models.CharField(max_length=100, blank=True)
    called_asserted_identity    = models.CharField(max_length=100, blank=True)
    connected_number            = models.CharField(max_length=100, blank=True)
    network_call_reference      = models.CharField(max_length=100, blank=True)
    visited_network_id          = models.CharField(max_length=100, blank=True)
    province                    = models.CharField(max_length=50, blank=True)
    roam_type                   = models.CharField(max_length=30, blank=True)
    user_agent_value            = models.CharField(max_length=255, blank=True)
    additional_calling_party    = models.CharField(max_length=100, blank=True)
    additional_called_party     = models.CharField(max_length=100, blank=True)
    sip_from_uri                = models.CharField(max_length=255, blank=True)
    ims_3gpp_online_charging    = models.BooleanField(default=False)

    # IN / CAMEL trigger (from List-Of-IN-Information) ------------------------
    in_service_key              = models.CharField(max_length=20, blank=True)
    in_call_reference           = models.CharField(max_length=50, blank=True)
    in_scf_address              = models.CharField(max_length=50, blank=True)
    in_bypass                   = models.CharField(max_length=10, blank=True)
    default_call_handling       = models.CharField(max_length=30, blank=True)

    # SDP-derived (parsed from List-Of-SDP-Media-Components) ------------------
    codec                       = models.CharField(max_length=50, blank=True)    # e.g. AMR-WB/16000
    rtp_port                    = models.CharField(max_length=10, blank=True)    # e.g. 28818
    rtp_protocol                = models.CharField(max_length=20, blank=True)    # e.g. RTP/AVP
    sip_codec_payload           = models.CharField(max_length=10, blank=True)    # e.g. 104
    telephone_event_payload     = models.CharField(max_length=10, blank=True)    # e.g. 100

    # Subscriber role / classification ---------------------------------------
    subscriber_role             = models.CharField(max_length=20, blank=True)    # from MMTel: ORIGINATING / TERMINATING
    private_user_equipment_info_type  = models.CharField(max_length=20, blank=True)  # IMEI / ESN / MEID …
    private_user_equipment_info_value = models.CharField(max_length=40, blank=True)  # raw form, e.g. '35125339-616639-0'
    access_network_spec         = models.CharField(max_length=50, blank=True)    # 3GPP-E-UTRAN / 3GPP-UTRAN-FDD …
    call_type                   = models.CharField(max_length=50, blank=True)    # IMS VoLTE Voice / IMS SMS / …

    # Raw-text-preserved party addresses (keep tel:/sip: prefix) -------------
    dialled_party_address       = models.CharField(max_length=255, blank=True)
    requested_party_address_raw = models.CharField(max_length=255, blank=True)
    called_asserted_identity_raw = models.CharField(max_length=255, blank=True)

    # -------------------------------------------------------------------------
    # Timing
    # -------------------------------------------------------------------------
    request_time  = models.DateTimeField(null=True, blank=True)   # SIP INVITE received
    start_time    = models.DateTimeField(null=True, blank=True, db_index=True)  # 200 OK (answer)
    end_time      = models.DateTimeField(null=True, blank=True)   # BYE / session end
    duration      = models.IntegerField(default=0)                # seconds
    ringing_duration = models.IntegerField(default=0)             # seconds

    # -------------------------------------------------------------------------
    # Network / routing
    # -------------------------------------------------------------------------
    node_address        = models.CharField(max_length=100, blank=True)  # ATS9900 node
    msc_number          = models.CharField(max_length=50, blank=True)
    vlr_number          = models.CharField(max_length=50, blank=True)
    originating_ioi     = models.CharField(max_length=100, blank=True)
    terminating_ioi     = models.CharField(max_length=100, blank=True)
    np_routing_number   = models.CharField(max_length=50, blank=True)
    carrier_code        = models.CharField(max_length=50, blank=True)
    call_property       = models.CharField(max_length=30, blank=True)   # LOCAL / NATIONAL / INTL
    roaming_indicator   = models.CharField(max_length=30, blank=True)
    call_category       = models.CharField(max_length=50, blank=True)

    # -------------------------------------------------------------------------
    # Charging / service
    # -------------------------------------------------------------------------
    charging_category   = models.CharField(max_length=30, blank=True)
    service_context_id  = models.CharField(max_length=100, blank=True)
    sequence_number     = models.BigIntegerField(null=True, blank=True)
    cause_for_closing   = models.CharField(max_length=100, blank=True)
    service_reason_code = models.CharField(max_length=50, blank=True)
    online_charging_flag = models.CharField(max_length=10, blank=True)
    supplementary_service = models.CharField(max_length=100, blank=True)
    diversion_reason    = models.CharField(max_length=50, blank=True)
    diversion_count     = models.IntegerField(default=0)

    # -------------------------------------------------------------------------
    # Raw data and status
    # -------------------------------------------------------------------------
    raw_data   = models.JSONField(default=dict, blank=True)
    status     = models.CharField(max_length=10, choices=Status.choices, default=Status.VALID)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ims_records'
        ordering = ['-start_time']
        indexes = [
            models.Index(fields=['start_time', 'record_type']),
            models.Index(fields=['calling_number', 'start_time']),
            models.Index(fields=['called_number', 'start_time']),
            models.Index(fields=['imsi', 'start_time']),
            models.Index(fields=['calling_imsi', 'start_time']),
            models.Index(fields=['called_imsi', 'start_time']),
            models.Index(fields=['session_id']),
            models.Index(fields=['file', 'record_type']),
        ]
        verbose_name = 'IMS CDR Record'
        verbose_name_plural = 'IMS CDR Records'

    def __str__(self):
        return f'{self.record_type} | {self.calling_number} -> {self.called_number} | {self.start_time}'
