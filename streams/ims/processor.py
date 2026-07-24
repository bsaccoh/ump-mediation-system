"""
IMS CDR Processor
==================
Processes Huawei ATS9900 IMS CDR binary files.

Pipeline:
  decode binary (ASN.1 BER) → in-memory records → create IMSRecord
  → validate → enrich → normalize → batch insert.

Supports VoLTE, VoBB, FMC and IMS supplementary service CDRs.
"""
import logging
import re
from typing import List, Tuple

from core.base_processor import BaseProcessor
from core.utils.prepaid import normalize_prepaid_flag
from streams.ims.cdr_fields import VALIDATION_RULES, ENRICHMENT_RULES
from streams.ims.decoder import decode_file as ims_decode_file

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sierra Leone home-network constants
# ---------------------------------------------------------------------------
HOME_IMSI_PREFIX     = '61901'        # Orange SL home subscribers (MCC+MNC)
HOME_MCC             = '619'
HOME_MNC             = '01'
POSTPAID_IMSI_PREFIX = '6190176100'   # Orange SL postpaid IMSI block

_ORANGE_SL_PREFIXES = (
    '23276', '23275', '23274', '23279', '23278', '23273',
    '076',   '075',   '074',   '079',   '078',   '073',
    '76',    '75',    '74',    '79',    '78',    '73',
)

# Map sip_method → service_type
_SVC_MAP = {
    'INVITE':    'VOICE',
    'MESSAGE':   'SMS',
    'REGISTER':  'EVENT',
    'SUBSCRIBE': 'EVENT',
    'NOTIFY':    'EVENT',
    'OPTIONS':   'EVENT',
    'PUBLISH':   'EVENT',
    'REFER':     'EVENT',
    'UPDATE':    'VOICE',   # mid-session update → still a voice session
    'INFO':      'VOICE',
    'BYE':       'VOICE',
    'CANCEL':    'VOICE',
    'ACK':       'VOICE',
    'PRACK':     'VOICE',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_onnet(n: str) -> bool:
    n = (n or '').strip()
    return any(n.startswith(p) for p in _ORANGE_SL_PREFIXES)


def _is_international(n: str) -> bool:
    n = (n or '').strip()
    if n.startswith('+'):
        return not n.startswith('+232')
    if n.startswith('00'):
        return not n.startswith('00232')
    d = ''.join(c for c in n if c.isdigit())
    return len(d) >= 11 and not d.startswith('232')


def _classify_ims_call(rec: dict) -> str:
    """
    Classify an IMS CDR record into a call category string.
    Uses call_property (LOCAL/NATIONAL/INTERNATIONAL/EMERGENCY),
    role_of_node (ORIGINATING/TERMINATING), IMSI prefix, and SIP method.
    """
    imsi       = (rec.get('imsi')        or '').strip()
    call_prop  = (rec.get('call_property') or '').upper()
    role       = (rec.get('role_of_node')  or rec.get('mmtel_role') or '').upper()
    sip_method = (rec.get('sip_method')    or '').upper()
    called     = (rec.get('called_number') or '').strip()
    calling    = (rec.get('calling_number') or '').strip()

    is_home_sub    = imsi.startswith(HOME_IMSI_PREFIX)
    is_terminating = 'TERM' in role
    is_originating = 'ORIG' in role or (not is_terminating)

    # ---- SMS (SIP MESSAGE) ---------------------------------------------------
    if sip_method == 'MESSAGE':
        # Only flag as roaming if we actually have a foreign IMSI.
        # If IMSI is absent (AS-CDR / app-server SMS), skip the roaming check
        # and classify by number instead.
        if imsi and not is_home_sub:
            if is_terminating:
                return 'INBOUND_ROAMING_SMS'
            return 'OUTBOUND_ROAMING_SMS'
        if _is_international(called):
            return 'SMS_INTERNATIONAL'
        if _is_onnet(called):
            return 'SMS_NATIONAL_ONNET'
        return 'SMS_NATIONAL_OFFNET'

    # ---- Event (non-session methods) ----------------------------------------
    if sip_method in ('REGISTER', 'SUBSCRIBE', 'NOTIFY', 'OPTIONS', 'PUBLISH'):
        return 'SUPPLEMENTARY_SERVICE'

    # ---- Emergency -----------------------------------------------------------
    if 'EMERGENCY' in call_prop:
        return 'EMERGENCY'

    # ---- Roaming: foreign IMSI on home IMS core — only when IMSI is known ----
    if imsi and not is_home_sub:
        return 'INBOUND_ROAMING' if is_terminating else 'OUTBOUND_ROAMING'

    # ---- Use call_property when available ------------------------------------
    # Accept Huawei mnemonics ('Local-Call', 'National-Call' …) and bare
    # forms ('LOCAL', 'NATIONAL') so older records still classify.
    if 'INTERNATIONAL' in call_prop:
        return 'INCOMING_INTERNATIONAL' if is_terminating else 'INTERNATIONAL'

    if 'NATIONAL' in call_prop or 'LOCAL' in call_prop:
        if is_terminating:
            if calling and _is_onnet(calling):
                return 'INCOMING_ONNET'
            return 'INCOMING_NATIONAL'
        # Originating
        if called and _is_onnet(called):
            return 'NATIONAL_ONNET'
        return 'NATIONAL_OFFNET'

    # ---- Fallback: infer from number analysis --------------------------------
    target = called if is_originating else calling
    if target:
        if _is_international(target):
            return 'INCOMING_INTERNATIONAL' if is_terminating else 'INTERNATIONAL'
        if _is_onnet(target):
            return 'INCOMING_ONNET' if is_terminating else 'NATIONAL_ONNET'

    return 'NATIONAL_OFFNET'


# ---------------------------------------------------------------------------
# In-memory record store (shared between decode() and parse_records())
# ---------------------------------------------------------------------------

class IMSProcessor(BaseProcessor):
    """Processor for Huawei ATS9900 IMS CDR binary files."""

    def __init__(self):
        super().__init__()
        self._decoded_records: List[dict] = []

    # -------------------------------------------------------------------------
    # BaseProcessor abstract methods
    # -------------------------------------------------------------------------

    def decode(self, file_path: str) -> Tuple[bool, str, int]:
        """
        Decode the IMS binary file using the BER decoder.
        Records are kept in memory and returned via parse_records().
        Returns (success, file_path, record_count).
        """
        records, stats = ims_decode_file(file_path)
        self._decoded_records = records
        if stats.get('total', 0) == 0 and not records:
            return False, 'No IMS records found in file', 0
        return True, file_path, len(records)

    def parse_records(self, file_path: str):
        """
        Yield decoded record dicts.
        decode() must have been called first; the file_path argument is ignored
        (records are already in memory from the decode step).
        """
        for rec in self._decoded_records:
            yield rec

    def create_record(self, raw: dict, cdr_file):
        """Map a decoded IMS record dict to an IMSRecord model instance."""
        from streams.ims.models import IMSRecord

        # Determine service type from SIP method
        sip_method   = (raw.get('sip_method') or '').upper()
        record_type  = raw.get('record_type') or 'ATS_CDR'
        service_type = _SVC_MAP.get(sip_method, 'VOICE')
        if 'EVENT' in record_type:
            service_type = 'EVENT'

        # Party numbers
        calling = (raw.get('calling_number') or '').strip()
        called  = (raw.get('called_number')  or '').strip()
        dialed  = (raw.get('dialed_number')  or called).strip()

        # Subscriber identity
        imsi   = (raw.get('imsi')   or '').strip()
        msisdn = (raw.get('msisdn') or '').strip()

        # Prepaid flag — Orange SL rule:
        #   IMSI starts with '6190176100'  → POSTPAID
        #   otherwise                      → PREPAID
        # (Empty IMSI defaults to PREPAID because the vast majority of
        #  Orange SL subscribers are prepaid; the postpaid IMSI block is
        #  a small reserved range.)
        if imsi and imsi.startswith(POSTPAID_IMSI_PREFIX):
            prepaid_flag = normalize_prepaid_flag('POSTPAID')
        else:
            prepaid_flag = normalize_prepaid_flag('PREPAID')

        # Duration
        try:
            duration = int(raw.get('duration') or 0)
        except (TypeError, ValueError):
            duration = 0
        try:
            ringing_duration = int(raw.get('ringing_duration') or 0)
        except (TypeError, ValueError):
            ringing_duration = 0

        # Sequence number
        try:
            sequence_number = int(raw.get('sequence_number') or 0) or None
        except (TypeError, ValueError):
            sequence_number = None

        # Diversion count
        try:
            diversion_count = int(raw.get('diversion_count') or 0)
        except (TypeError, ValueError):
            diversion_count = 0

        # Call classification
        call_cat = _classify_ims_call(raw)

        # Roaming indicator
        roaming_indicator = ''
        if 'ROAMING' in call_cat:
            roaming_indicator = 'INTERNATIONAL'
        elif 'INTERNATIONAL' in call_cat:
            roaming_indicator = ''  # international call, not roaming
        else:
            roaming_indicator = 'NON-ROAMER'

        # Node address (decoded from tag 0x84 nodeAddress)
        node_address = (raw.get('node_address_raw') or '').strip()

        record = IMSRecord(
            file=cdr_file,
            source=cdr_file.source,

            # Identity
            record_type=record_type,
            service_type=service_type,
            role_of_node=(raw.get('role_of_node') or '').strip(),
            sip_method=sip_method,
            session_id=(raw.get('session_id') or '')[:255],
            icid=(raw.get('icid') or '')[:255],

            # Parties
            calling_number=calling[:100],
            called_number=called[:100],
            dialed_number=dialed[:100],
            charged_party=(raw.get('charged_party') or '')[:100],
            calling_sip_uri=(raw.get('calling_sip_uri') or '')[:255],
            called_sip_uri=(raw.get('called_sip_uri') or '')[:255],

            # Subscriber (served)
            imsi=imsi[:20],
            msisdn=msisdn[:20],
            imei=(raw.get('imei') or '')[:20],
            private_user_identity=(raw.get('private_user_identity') or '')[:255],
            prepaid_flag=prepaid_flag,

            # A-party (calling)
            calling_imsi=(raw.get('calling_imsi') or '')[:20],
            calling_min=(raw.get('calling_min')  or '')[:50],
            calling_impi=(raw.get('calling_impi') or '')[:255],

            # B-party (called)
            called_imsi=(raw.get('called_imsi') or '')[:20],
            called_min=(raw.get('called_min')  or '')[:50],
            called_impi=(raw.get('called_impi') or '')[:255],

            # Media / session info
            media_type=(raw.get('media_type') or '')[:30],
            served_subscriber_type=(raw.get('served_subscriber_type') or '')[:20],
            access_network_info=(raw.get('access_network_info') or '')[:512],

            # Access network — parsed from access_network_info / RAT tags
            technology=(raw.get('technology') or '')[:20],
            serving_plmn=(raw.get('serving_plmn') or '')[:10],
            tac=(raw.get('tac') or '')[:20],
            lac=(raw.get('lac') or '')[:20],
            cell_id=(raw.get('cell_id') or '')[:30],
            enodeb_id=(raw.get('enodeb_id') or '')[:20],
            ue_ip=raw.get('ue_ip') or None,
            apn=(raw.get('apn') or '')[:100],

            # Call forwarding
            forwarded_number=(raw.get('forwarded_number') or '')[:100],
            redirecting_number=(raw.get('redirecting_number') or '')[:100],

            # Additional ATS9900 fields
            retransmission=bool(raw.get('retransmission')),
            accounting_record_type=(raw.get('accounting_record_type') or '')[:30],
            sdp_media_identifier=(raw.get('sdp_media_identifier') or '')[:30],
            sdp_media_components=(raw.get('sdp_media_components') or '')[:1000],
            tads_indication=(raw.get('tads_indication') or '')[:20],
            incomplete_cdr_indication=(raw.get('incomplete_cdr_indication') or '')[:80],
            is_volte_call_type=(raw.get('is_volte_call_type') or '')[:20],
            requested_party_address=(raw.get('requested_party_address') or '')[:100],
            called_asserted_identity=(raw.get('called_asserted_identity') or '')[:100],
            connected_number=(raw.get('connected_number') or '')[:100],
            network_call_reference=(raw.get('network_call_reference') or '')[:100],
            visited_network_id=(raw.get('visited_network_id') or '')[:100],
            province=(raw.get('province') or '')[:50],
            roam_type=(raw.get('roam_type') or '')[:30],
            user_agent_value=(raw.get('user_agent_value') or '')[:255],
            additional_calling_party=(raw.get('additional_calling_party') or '')[:100],
            additional_called_party=(raw.get('additional_called_party') or '')[:100],
            sip_from_uri=(raw.get('sip_from_uri') or '')[:255],
            ims_3gpp_online_charging=(raw.get('ims_3gpp_online_charging_raw') in (1, '1', True)),

            # IN / CAMEL trigger
            in_service_key=(raw.get('in_service_key') or '')[:20],
            in_call_reference=(raw.get('in_call_reference') or '')[:50],
            in_scf_address=(raw.get('in_scf_address') or '')[:50],
            in_bypass=(raw.get('in_bypass') or '')[:10],
            default_call_handling=(raw.get('default_call_handling') or '')[:30],

            # SDP-derived (codec / port / payload types)
            codec=(raw.get('codec') or '')[:50],
            rtp_port=str(raw.get('rtp_port') or '')[:10],
            rtp_protocol=(raw.get('rtp_protocol') or '')[:20],
            sip_codec_payload=str(raw.get('sip_codec_payload') or '')[:10],
            telephone_event_payload=str(raw.get('telephone_event_payload') or '')[:10],

            # Subscriber-role / equipment-type / network spec / call-type
            subscriber_role=(raw.get('subscriber_role') or raw.get('mmtel_role') or '')[:20],
            private_user_equipment_info_type=(raw.get('private_user_equipment_info_type') or '')[:20],
            private_user_equipment_info_value=(raw.get('private_user_equipment_info_value') or '')[:40],
            access_network_spec=(raw.get('access_network_spec') or '')[:50],

            # Raw URI-form party addresses (keep tel:/sip: prefix)
            dialled_party_address=(raw.get('dialled_party_address') or '')[:255],
            requested_party_address_raw=(raw.get('requested_party_address_raw') or '')[:255],
            called_asserted_identity_raw=(raw.get('called_asserted_identity_raw') or '')[:255],

            # Timing
            request_time=raw.get('request_timestamp'),
            start_time=raw.get('start_time') or raw.get('answer_timestamp') or raw.get('request_timestamp'),
            end_time=raw.get('end_timestamp'),
            duration=duration,
            ringing_duration=ringing_duration,

            # Network / routing
            node_address=node_address[:100],
            msc_number=(raw.get('msc_number') or '')[:50],
            vlr_number=(raw.get('vlr_number') or '')[:50],
            originating_ioi=(raw.get('originating_ioi') or '')[:100],
            terminating_ioi=(raw.get('terminating_ioi') or '')[:100],
            np_routing_number=(raw.get('np_routing_number') or '')[:50],
            carrier_code=(raw.get('carrier_code') or '')[:50],
            call_property=(raw.get('call_property') or '')[:30],
            roaming_indicator=roaming_indicator[:30],
            call_category=call_cat[:50],

            # Charging / service
            charging_category=(raw.get('charging_category') or '')[:30],
            service_context_id=(raw.get('service_context_id') or '')[:100],
            sequence_number=sequence_number,
            cause_for_closing=(raw.get('cause_for_closing') or '')[:100],
            service_reason_code=(raw.get('service_reason_code') or '')[:50],
            online_charging_flag=(raw.get('online_charging_flag') or '')[:10],
            supplementary_service=(raw.get('supplementary_service') or '')[:100],
            diversion_reason=(raw.get('diversion_reason') or '')[:50],
            diversion_count=diversion_count,

            raw_data={k: str(v) if v is not None else '' for k, v in raw.items()
                      if not k.startswith('_') and k not in ('start_time',
                          'request_timestamp', 'answer_timestamp', 'end_timestamp',
                          'record_open_time', 'record_close_time')},
            status=IMSRecord.Status.VALID,
        )
        return record

    def validate_record(self, record, raw: dict) -> List[str]:
        """Apply IMS validation rules."""
        errors = []
        for field_name, rules in VALIDATION_RULES.items():
            value = raw.get(field_name, '')
            if not value:
                if rules.get('required', False):
                    errors.append(f'{field_name}: required')
                continue
            max_len = rules.get('max_length')
            if max_len and len(str(value)) > max_len:
                errors.append(f'{field_name}: exceeds {max_len} chars')
            pattern = rules.get('pattern')
            if pattern and value:
                if not re.match(pattern, str(value)):
                    errors.append(f'{field_name}: pattern mismatch')
        if errors:
            raw['_validation_errors'] = errors[:5]
            record.raw_data = raw
        return errors

    def _derive_call_type(self, record) -> str:
        """Map (sip_method, media_type) → human-readable Call-Type."""
        m  = (record.sip_method or '').upper()
        mt = (record.media_type or '').lower()
        if m == 'MESSAGE':
            return 'IMS SMS'
        if m == 'REGISTER':
            return 'IMS Registration'
        if m in ('SUBSCRIBE', 'NOTIFY', 'PUBLISH', 'OPTIONS'):
            return 'IMS Event'
        # Session methods — INVITE / BYE / UPDATE / ACK / CANCEL / PRACK / INFO / REFER
        if mt == 'video':
            return 'IMS VoLTE Video'
        if mt == 'text':
            return 'IMS RCS Text'
        return 'IMS VoLTE Voice'

    def enrich_record(self, record, raw: dict) -> None:
        """Enrich the IMS record.

        - Fill served MSISDN from calling_number for originating records if blank.
        - Map served IMSI/MSISDN onto the right A/B-party slot using role_of_node:
            ORIGINATING → calling_*  (served subscriber is the A-party)
            TERMINATING → called_*   (served subscriber is the B-party)
          Only fills slots that are still empty.
        - Mirror calling_number / called_number into calling_min / called_min
          when MIN-style fields are still blank.
        """
        role = (record.role_of_node or '').upper()

        # 1. Default MSISDN from A-party number for originating records
        if not record.msisdn and record.calling_number:
            if 'ORIG' in role or not role:
                record.msisdn = record.calling_number[:20]

        # 2. Distribute served IMSI/MSISDN to A or B slot based on role
        if record.imsi:
            if 'ORIG' in role and not record.calling_imsi:
                record.calling_imsi = record.imsi
            elif 'TERM' in role and not record.called_imsi:
                record.called_imsi = record.imsi
        if record.msisdn:
            if 'ORIG' in role and not record.calling_min:
                record.calling_min = record.msisdn
            elif 'TERM' in role and not record.called_min:
                record.called_min = record.msisdn

        # 3. MIN fallback from the party number when nothing else is known
        if not record.calling_min and record.calling_number:
            record.calling_min = record.calling_number[:50]
        if not record.called_min and record.called_number:
            record.called_min = record.called_number[:50]

        # 4. Subscriber-role fallback to role_of_node
        if not record.subscriber_role and record.role_of_node:
            record.subscriber_role = record.role_of_node[:20]

        # 5. Derive Call-Type (IMS VoLTE Voice / IMS SMS / etc.)
        if not record.call_type:
            record.call_type = self._derive_call_type(record)[:50]

    def _needs_decoding(self, file_path: str) -> bool:
        """IMS binary files always need decoding through our BER decoder."""
        return True

    # -------------------------------------------------------------------------
    # CDR-pair correlation (post-process)
    # -------------------------------------------------------------------------

    def post_process(self, cdr_file) -> None:
        """Link originating ↔ terminating IMS records that share the same ICID.

        Pairing scope is cross-file but time-bounded (24-hour window) so the
        end-of-file run is cheap.  Late-arriving pairs are picked up by the
        ``correlate_orphans`` management command.
        """
        from streams.ims.models import IMSRecord
        from streams.ims.correlation import correlate_unpaired_ims

        unpaired = (IMSRecord.objects
                    .filter(file=cdr_file, paired_record__isnull=True)
                    .exclude(icid='')
                    .exclude(icid__isnull=True))
        pair_count = correlate_unpaired_ims(unpaired, candidate_window_days=1)
        if pair_count:
            logger.info(
                f'IMS correlation: linked {pair_count} CDR pair(s) for file #{cdr_file.pk}'
            )
