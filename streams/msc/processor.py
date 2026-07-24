"""
MSC CDR Processor
==================
Processes Huawei MSC CDR files (voice + SMS).
Pipeline: decode binary -> parse CSV -> create -> validate -> enrich -> normalize -> persist.
"""
import os
import csv
import json
import io
import re
import logging
from functools import lru_cache
from typing import Tuple, List

from core.base_processor import BaseProcessor
from core.utils.prepaid import normalize_prepaid_flag, derive_msc_prepaid_flag
from core.utils.timestamps import parse_mediation_timestamp
from streams.msc.decoder import decode_cdr_file, decode_cdr_file_to_records, is_binary_cdr
from streams.msc.cdr_fields import (
    OUTPUT_FIELDS, VALIDATION_RULES, ENRICHMENT_RULES, FIELD_MAPPING,
)

logger = logging.getLogger(__name__)

# Pre-compile validation regex patterns (avoid re-compiling per record × per field)
_COMPILED_PATTERNS = {}
for _fn, _rules in VALIDATION_RULES.items():
    _p = _rules.get('pattern')
    if _p:
        _COMPILED_PATTERNS[_fn] = re.compile(_p)

# Fields that are already stored as dedicated model columns — exclude from raw_data
# to reduce JSON blob size by ~60%.
_MODEL_COLUMN_FIELDS = frozenset({
    'CALLING_PARTY_NUMBER', 'CALLED_PARTY_NUMBER', 'ACCESS_METHOD_IDENTIFIER',
    'IMSI', 'IMEI', 'CALL_START_DATE', 'CALL_START_TIME', 'CHARGEABLE_DURATION',
    'CALL_TYPE', 'SERVICE_USAGE_DIRECTION', 'CELL_ID', 'LAC_ID',
    'MSC_IDENTIFICATION', 'INCOMING_ROUTE', 'OUTGOING_ROUTE',
    'CALL_REFERENCE_NUMBER', 'BEARER_SERVICE_CODE', 'CAUSE_FOR_TERM',
    'CF_REDIRECTINGNUMBER', 'ORIGINAL_CALLED_NUMBER', 'CALL_END_DATETIME',
    'CDR_RECORD_NUMBER', 'MS_CALL_REF',
})


# ---------------------------------------------------------------------------
# MSISDN normalization
# ---------------------------------------------------------------------------
# Sierra Leone operator prefix ranges (2-digit) used to decide whether a
# bare 8-digit local number should be promoted to international form.
SL_OPERATOR_PREFIXES_8 = (
    '72', '73', '74', '75', '76', '78', '79',     # Orange SL
    '30', '31', '32', '33', '34',                  # Africell SL (legacy + new)
    '77',                                          # Africell (re-allocated)
    '88', '90', '98', '99', '80',                  # Qcell / Smart / other SL mobile
    '22', '23', '25',                              # Sierratel fixed
)


@lru_cache(maxsize=8192)
def _normalize_msisdn(n: str) -> str:
    """Normalise a Sierra Leone MSISDN to international format (`232XXXXXXXX`).

    Rules (only applied to numbers that look like SL MSISDNs):
      '76576003'      → '23276576003'    (8-digit local → international)
      '076576003'     → '23276576003'    (9-digit with leading 0 → international)
      '23276576003'   → '23276576003'    (already international, unchanged)
      '+23276576003'  → '23276576003'    (strip +)
      '0023276576003' → '23276576003'    (strip 00 international prefix)

    Numbers that are alphanumeric (e.g. 'OrangeMoney'), short codes
    (≤ 6 digits), foreign (11+ digits not starting with 232), or already
    in formats we don't recognise are returned unchanged.
    """
    if not n:
        return n
    s = n.strip()
    # Don't touch alphanumeric sender IDs ('OrangeMoney', 'OrangeSL', etc.)
    if any(c.isalpha() for c in s):
        return s
    # Strip common international prefixes
    s = s.lstrip('+')
    if s.startswith('00'):
        s = s[2:]
    # Already in 232... form
    if s.startswith('232') and len(s) == 11 and s[3:5] in SL_OPERATOR_PREFIXES_8:
        return s
    # 9-digit local with leading 0
    if len(s) == 9 and s.startswith('0') and s[1:3] in SL_OPERATOR_PREFIXES_8:
        return '232' + s[1:]
    # 8-digit local (no CC, no leading 0)
    if len(s) == 8 and s[:2] in SL_OPERATOR_PREFIXES_8:
        return '232' + s
    # Anything else — leave untouched (foreign, short codes, etc.)
    return s


# Record types kept in the decoded output. Anything else (RCF, EXT, EMERG,
# LOCUPD, TRANSIT, SS, HLR, SIP*, ROAMING, …) is dropped before processing.
# Env-overridable via MSC_OUTPUT_RECORD_TYPES (comma-separated).
DEFAULT_OUTPUT_RECORD_TYPES = {'CF', 'GWI', 'GWO', 'MOC', 'MTC', 'SMSMT', 'SMSMO'}

# MSISDN classifications that count as "our subscriber" (home / national SL).
_HOME_SUBSCRIBER_NETS = {'Orange', 'Africell', 'Qcell', 'Smart', 'Sierratel', 'Other SL'}


def _output_record_types():
    from django.conf import settings
    configured = getattr(settings, 'MSC_OUTPUT_RECORD_TYPES', None)
    return set(configured) if configured else DEFAULT_OUTPUT_RECORD_TYPES


def _is_home_subscriber(number: str) -> bool:
    """True if the number belongs to a Sierra Leone (home/national) network.

    Used to gate prepaid/postpaid classification: only our own subscribers get
    a Subscriber Type. International / unknown originators (e.g. an SMS-MO
    interworking message from a foreign number) are left blank rather than
    defaulting to POSTPAID.
    """
    from core.utils.operators import classify_operator
    return classify_operator(number) in _HOME_SUBSCRIBER_NETS


class MSCProcessor(BaseProcessor):
    """Processor for Huawei MSC CDR files (voice + SMS)."""

    def decode_to_records(self, file_path: str):
        """Decode ASN.1 BER binary directly to in-memory dicts (fast path).

        Only the configured output record types are kept (CF/GWI/GWO/MOC/MTC/
        SMSMT/SMSMO by default); other types are dropped here so they never
        reach the output file or the per-file counts."""
        result = decode_cdr_file_to_records(file_path)
        # decode_cdr_file_to_records returns (success, records, count).
        if not isinstance(result, tuple) or len(result) != 3:
            return result
        success, records, count = result
        if success and isinstance(records, list):
            allowed = _output_record_types()
            records = [r for r in records if (r.get('CALL_TYPE') or '') in allowed]
            count = len(records)
        return (success, records, count)

    def decode(self, file_path: str) -> Tuple[bool, str, int]:
        """Decode ASN.1 BER binary to CSV (legacy fallback)."""
        from django.conf import settings

        base_name = os.path.splitext(os.path.basename(file_path))[0]
        decoded_dir = str(settings.DECODED_DIR / 'msc')
        os.makedirs(decoded_dir, exist_ok=True)
        decoded_path = os.path.join(decoded_dir, f'{base_name}_decoded.csv')

        return decode_cdr_file(file_path, decoded_path)

    def parse_records(self, file_path: str):
        """Generator: parse CSV rows into dicts."""
        with open(file_path, 'r', encoding='utf-8', errors='ignore', newline='') as f:
            content = f.read()

        content = content.replace('\r\n', '\n').replace('\r', '\n')

        # Detect delimiter
        first_line = content.split('\n')[0] if '\n' in content else content[:1000]
        if '|' in first_line:
            delimiter = '|'
        elif ';' in first_line:
            delimiter = ';'
        elif '\t' in first_line:
            delimiter = '\t'
        else:
            delimiter = ','

        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        for row in reader:
            # Normalize keys
            normalized = {}
            for key, value in row.items():
                if key:
                    k = key.strip()
                    v = str(value).strip() if value else None
                    normalized[k] = v
            yield normalized

    def create_record(self, raw: dict, cdr_file):
        """Create MSCRecord from a BIG_DATA CSV row."""
        from streams.msc.models import MSCRecord

        # Support BIG_DATA format (primary) and legacy internal format (fallback)
        is_bigdata  = 'CALL_TYPE' in raw or 'CALLING_PARTY_NUMBER' in raw
        is_internal = 'ORIGINAL_CALL_TYPE' in raw or 'CALLING_NO' in raw

        if is_bigdata:
            call_type = raw.get('CALL_TYPE') or 'UNKNOWN'
            # Map BIG_DATA CALL_TYPE → human-readable service_type
            # BIG_DATA CALL_TYPE uses the full 5-letter SMS variant
            # ('SMSMO' / 'SMSMT').  The previous map used 3-letter
            # 'SMO'/'SMT' keys that never matched, so SMS records ended
            # up with service_type='VOICE'.
            svc_map = {
                'MOC': 'VOICE', 'MTC': 'VOICE',
                'CF':  'CALLFORWARDING', 'RCF': 'CALLFORWARDING',
                'SMSMO': 'SMS', 'SMSMT': 'SMS',
                'SMO':   'SMS', 'SMT':   'SMS',   # 3-letter back-compat
                'GWI': 'VOICE', 'GWO': 'VOICE',
            }
            service_type = svc_map.get(call_type, 'VOICE')

            calling = _normalize_msisdn(raw.get('CALLING_PARTY_NUMBER') or '')
            called  = _normalize_msisdn(raw.get('CALLED_PARTY_NUMBER')  or '')
            imsi    = raw.get('IMSI') or ''
            imei    = raw.get('IMEI') or ''

            # Originating SMSC GT — populated for SMSMO / SMSMT records.
            # For SMSMT this is the SMSC that delivered the message to the MSC;
            # for SMSMO it's the SMSC the subscriber sent the message to.
            # Kept as its own column (not jammed into calling_number).
            smsc_address = ''
            if call_type in ('SMSMT', 'SMSMO'):
                smsc_address = (raw.get('SERVICE_CENTRE_ADDRESS') or '').strip()

            # ----- Pseudo-forwarding detection ----------------------------
            # Huawei MSC's tag 0xAF is shared between real call-forwarding
            # and on-net direct MOC routing. The on-net case shows
            # called == forwarded_to == redirecting (all the same number).
            # Detect this for ALL record types and:
            #   1. Reclassify CF → MOC so users can find on-net calls under
            #      the natural record type.
            #   2. Blank out CF_REDIRECTINGNUMBER / ORIGINAL_CALLED_NUMBER
            #      so the downstream forwarded_number / redirecting_number /
            #      dialed_number fields stay empty when there's no real
            #      forwarding — fixes "MOC row shows same number in all 4
            #      called/dialed/forwarded/redirecting columns".
            def _norm(n):
                n = (n or '').strip().lstrip('+').lstrip('0')
                return n[3:] if n.startswith('232') else n
            _called = _norm(called)
            _fwd    = _norm(raw.get('CF_REDIRECTINGNUMBER'))
            _redir  = _norm(raw.get('ORIGINAL_CALLED_NUMBER'))
            _no_real_forward = (
                _called
                and (not _fwd or _called == _fwd)
                and (not _redir or _called == _redir)
            )
            if _no_real_forward:
                if call_type == 'CF':
                    call_type = 'MOC'
                    service_type = 'VOICE'
                    raw['ORIGINAL_CALL_TYPE'] = 'MOC'
                    raw['_RECLASSIFIED_FROM'] = 'CF (pseudo-forwarding)'
                # No real forwarding — clear the duplicate B-number fields.
                raw['CF_REDIRECTINGNUMBER'] = ''
                raw['ORIGINAL_CALLED_NUMBER'] = ''

            # Build start datetime from CALL_START_DATE + CALL_START_TIME
            start_str = (raw.get('CALL_START_DATE') or '') + (raw.get('CALL_START_TIME') or '')
            start_time = parse_mediation_timestamp(start_str) if len(start_str) >= 8 else None
            end_time   = parse_mediation_timestamp(raw.get('CALL_END_DATETIME'))

            if service_type == 'SMS' and start_time and not end_time:
                end_time = start_time

            duration_str = raw.get('CHARGEABLE_DURATION') or '0'
            try:
                duration = int(float(duration_str))
            except (ValueError, TypeError):
                duration = 0

            # CAMEL IN-trigger → prepaid_flag.
            #   - SERVICEKEY / CAMEL_PHASE populated → PREPAID
            #   - both blank                         → POSTPAID
            # Applied ONLY to record types where the CDR subscriber is the
            # originating party on our network:
            #   * MOC   — our subscriber making a voice call
            #   * SMSMO — our subscriber sending an SMS
            #   * CF    — our subscriber's call-forwarding leg
            # Other record types get blank because the subscriber's billing
            # type can't be reliably derived from the CDR:
            #   * MTC   — incoming voice; we hold the called party but the
            #             CAMEL signal in MTC is the calling party's
            #   * SMSMT — incoming SMS; same reasoning as MTC
            #   * GWI / GWO — gateway transit legs; subscriber is on a
            #             foreign network, not ours
            #
            # Accepts both decoder spellings: the binary decoder dict uses
            # underscores ('SERVICE_KEY', 'CAMEL_PHASE'); BIG_DATA CSV drops
            # the underscore ('SERVICEKEY', 'CAMELPHASE').
            # GWO is intentionally excluded: outgoing-gateway (trunk-side) CDRs
            # carry no CAMEL serviceKey/camelPhase and no IMSI/IMEI, so prepaid
            # cannot be derived — every GWO would read POSTPAID. The real
            # subscriber type for that call is on the paired MOC record.
            CLASSIFY_PREPAID_FOR = {'MOC', 'SMSMO', 'CF', 'RCF'}
            # Only classify when the served subscriber is on our network. An
            # international / foreign originator (e.g. an SMS-MO interworking
            # message) is not our subscriber, so Subscriber Type stays blank
            # instead of defaulting to POSTPAID.
            served = (raw.get('ACCESS_METHOD_IDENTIFIER')
                      or raw.get('CALLING_PARTY_NUMBER') or '')
            if call_type in CLASSIFY_PREPAID_FOR and _is_home_subscriber(served):
                sk = (raw.get('SERVICEKEY')
                      or raw.get('SERVICE_KEY')
                      or raw.get('CAMEL_SERVICE_KEY'))
                cp = raw.get('CAMELPHASE') or raw.get('CAMEL_PHASE')
                prepaid_flag = derive_msc_prepaid_flag(sk, cp, imsi=imsi)
            else:
                prepaid_flag = ''

            # Roaming indicator — prefer CALL_CATEGORY (richer, from _classify_call),
            # fall back to ROAMING_TYPE (IMSI-prefix based).
            call_cat = (raw.get('CALL_CATEGORY') or '').upper()
            roaming_type = (raw.get('ROAMING_TYPE') or '').upper()
            _roaming_cats = {
                'OUTBOUND_ROAMING', 'INBOUND_ROAMING',
                'OUTBOUND_ROAMING_SMS', 'INBOUND_ROAMING_SMS',
                'CF_OUTBOUND_ROAMING', 'CF_ROAMING',
            }
            if call_cat in _roaming_cats or 'ROAMING' in call_cat:
                roaming_indicator = 'INTERNATIONAL'
            elif 'INTERNATIONAL' in roaming_type:
                roaming_indicator = 'INTERNATIONAL'
            elif 'NATIONAL' in roaming_type:
                roaming_indicator = 'NATIONAL'
            else:
                roaming_indicator = 'NON-ROAMER'

            record = MSCRecord(
                file=cdr_file,
                source=cdr_file.source,
                record_type=call_type,
                service_type=service_type,
                network_record_id=raw.get('CDR_RECORD_NUMBER') or '',
                call_reference=raw.get('CALL_REFERENCE_NUMBER') or raw.get('MS_CALL_REF') or '',
                call_direction=raw.get('SERVICE_USAGE_DIRECTION') or '',
                calling_number=str(calling)[:50] if calling else '-',
                called_number=str(called)[:50] if called else '',
                dialed_number=_normalize_msisdn(raw.get('ORIGINAL_CALLED_NUMBER') or ''),
                charged_msisdn=_normalize_msisdn(raw.get('ACCESS_METHOD_IDENTIFIER') or ''),
                imsi=str(imsi)[:20] if imsi else '',
                prepaid_flag=prepaid_flag,
                imei=str(imei)[:20] if imei else '',
                imsi_b='',
                imei_b='',
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                cell_id=raw.get('CELL_ID') or '',
                lac=raw.get('LAC_ID') or '',
                tac='',
                msc_id=raw.get('MSC_IDENTIFICATION') or '',
                smsc_address=smsc_address[:30],
                rat_type=raw.get('SYSTEM_TYPE') or '',
                originating_trunk=raw.get('INCOMING_ROUTE') or '',
                terminating_trunk=raw.get('OUTGOING_ROUTE') or '',
                teleservice_code='',
                bearer_service_code=raw.get('BEARER_SERVICE_CODE') or '',
                result_code=raw.get('CAUSE_FOR_TERM') or '',
                roaming_indicator=roaming_indicator,
                forwarded_number=_normalize_msisdn(raw.get('CF_REDIRECTINGNUMBER') or ''),
                # For CF records, redirecting_number = B (CF subscriber, the "redirected from" party)
                # which maps to CALLED_PARTY_NUMBER after the decoder consistency pass.
                # For other record types, reuse CF_REDIRECTINGNUMBER (incoming CF destination).
                redirecting_number=_normalize_msisdn(
                    (raw.get('CALLED_PARTY_NUMBER') or '') if call_type == 'CF'
                    else (raw.get('CF_REDIRECTINGNUMBER') or '')
                ),
                call_category=raw.get('CALL_CATEGORY') or raw.get('ROAMING_TYPE') or '',
                raw_data=self._slim_raw_data(raw),
                status=MSCRecord.Status.VALID,
            )
            return record

        if is_internal:
            # Legacy internal format — backward compatibility
            record_type  = raw.get('ORIGINAL_CALL_TYPE') or 'UNKNOWN'
            service_type = raw.get('SERVICE_TYPE') or raw.get('SERVICE_ID') or 'VOICE'
            service_map  = {'V': 'VOICE', 'E': 'SMS', 'D': 'DATA', 'F': 'FORWARDING'}
            service_type = service_map.get(service_type, service_type.upper())

            calling = raw.get('CALLING_NO') or ''
            if not calling:
                charged = raw.get('CHARGED_PARTY_MSISDN') or ''
                called  = raw.get('CALLED_NO') or ''
                if charged and charged != called:
                    calling = charged
                elif charged and any(c.isalpha() for c in charged):
                    calling = charged
            called = raw.get('CALLED_NO') or raw.get('DIALED_NO') or ''
            imsi   = raw.get('CHARGED_PARTY_IMSI') or raw.get('IMSI_A') or ''
            imei   = raw.get('IMEI_A') or ''

            duration_str = raw.get('CALL_DURATION') or '0'
            try:
                duration = int(float(duration_str))
            except (ValueError, TypeError):
                duration = 0

            start_time = parse_mediation_timestamp(raw.get('START_DATETIME'))
            end_time   = parse_mediation_timestamp(raw.get('CALL_END_DATETIME'))
            if service_type == 'SMS' and start_time and not end_time:
                end_time = start_time

            call_category = raw.get('CALL_CATEGORY') or 'OTHER'
            cat_upper = call_category.upper()
            if 'ROAMING' in cat_upper:
                roaming_indicator = 'ROAMING'
            elif 'INTERNATIONAL' in cat_upper:
                roaming_indicator = 'INTERNATIONAL'
            else:
                roaming_indicator = 'NATIONAL'

            record = MSCRecord(
                file=cdr_file,
                source=cdr_file.source,
                record_type=record_type,
                service_type=service_type,
                network_record_id=raw.get('NETWORK_RECORD_ID') or '',
                call_reference=raw.get('CALL_REF') or '',
                call_direction=raw.get('CALL_DIRECTION') or '',
                calling_number=str(calling)[:50] if calling else '-',
                called_number=str(called)[:50] if called else '',
                dialed_number=raw.get('DIALED_NO') or '',
                charged_msisdn=raw.get('CHARGED_PARTY_MSISDN') or '',
                imsi=str(imsi)[:20] if imsi else '',
                # Only MOC / SMSMO / CF (originating-leg) records get a
                # prepaid classification.  MTC / SMSMT / GWI / GWO leave
                # the flag blank — their CDRs don't carry a reliable
                # billing-type CAMEL signal for the on-net subscriber.
                prepaid_flag=(
                    normalize_prepaid_flag(raw.get('PREPAID_FLAG'))
                    if record_type in {'MOC', 'SMSMO', 'CF', 'RCF'}
                    and _is_home_subscriber(
                        raw.get('CHARGED_PARTY_MSISDN') or raw.get('CALLING_NO') or '')
                    else ''
                ),
                imei=str(imei)[:20] if imei else '',
                imsi_b=raw.get('IMSI_B') or '',
                imei_b=raw.get('IMEI_B') or '',
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                cell_id=raw.get('CELL_ID_A') or '',
                lac=raw.get('LAC_IDENTIFIER') or '',
                tac=raw.get('TAC') or '',
                msc_id=raw.get('MSC_ID') or '',
                smsc_address=((raw.get('SERVICE_CENTRE_ADDRESS') or '')[:30]
                              if (raw.get('ORIGINAL_CALL_TYPE') or '').upper() in
                                 ('SMSMT', 'SMSMO', 'SMS-MT', 'SMS-MO', 'SMSMO_IW', 'SMSMT_GW')
                              else ''),
                rat_type=raw.get('RAT_TYPE') or '',
                originating_trunk=raw.get('ORIGINATING_TRUNK') or '',
                terminating_trunk=raw.get('TERMINATING_TRUNK') or '',
                teleservice_code=raw.get('TELESERVICE_CODE') or '',
                bearer_service_code=raw.get('BEARER_SERVICE_CODE') or '',
                result_code=raw.get('RESULT_CODE') or '',
                roaming_indicator=roaming_indicator,
                forwarded_number=raw.get('FORWARDED_NUMBER') or '',
                redirecting_number=raw.get('REDIRECTING_NUMBER') or '',
                call_category=call_category,
                raw_data=self._slim_raw_data(raw),
                status=MSCRecord.Status.VALID,
            )
            return record

        return None

    @staticmethod
    def _slim_raw_data(raw: dict) -> dict:
        """Strip model-column fields and empties to reduce JSON blob size."""
        return {
            k: v for k, v in raw.items()
            if v and k not in _MODEL_COLUMN_FIELDS and not k.startswith('_')
        }

    def validate_record(self, record, raw: dict) -> List[str]:
        """Apply validation rules from cdr_fields.VALIDATION_RULES."""
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

            compiled = _COMPILED_PATTERNS.get(field_name)
            if compiled and value:
                if not compiled.match(str(value)):
                    errors.append(f'{field_name}: pattern mismatch')

        if errors:
            raw['_validation_errors'] = errors[:5]
            record.raw_data = self._slim_raw_data(raw)

        return errors

    def enrich_record(self, record, raw: dict) -> None:
        """Apply enrichment rules from cdr_fields.ENRICHMENT_RULES."""
        modified = False

        for target_field, rule in ENRICHMENT_RULES.items():
            current_value = raw.get(target_field, '')
            condition = rule.get('condition', 'if_empty')

            if condition == 'if_empty' and current_value:
                continue
            elif condition == 'if_sms_and_empty':
                call_type = raw.get('ORIGINAL_CALL_TYPE', '')
                if call_type not in ('SMSMO', 'SMSMT', 'SMSMO_IW', 'SMSMT_GW',
                                      'SIP_SMSMO', 'SIP_SMSMT'):
                    continue
                if current_value:
                    continue

            transform = rule.get('transform')
            source_field = rule.get('source_field')

            if transform == 'first_n_digits':
                source_val = raw.get(source_field, '')
                n = rule.get('n', 5)
                if source_val and len(source_val) >= n:
                    raw[target_field] = source_val[:n]
                    modified = True
            elif transform == 'copy':
                source_val = raw.get(source_field, '')
                if source_val:
                    raw[target_field] = source_val
                    modified = True
            elif transform == 'default_value':
                raw[target_field] = rule.get('default', '')
                modified = True

        if modified:
            record.raw_data = raw

    # -------------------------------------------------------------------------
    # CDR-pair correlation (post-process)
    # -------------------------------------------------------------------------

    def post_process(self, cdr_file) -> None:
        """Link MOC ↔ MTC records that share the same call_reference.

        Time-bounded to 24 h so end-of-file runs are cheap.  Late-arriving
        pairs are caught by the ``correlate_orphans`` management command.
        """
        from streams.msc.models import MSCRecord
        from streams.msc.correlation import correlate_unpaired_msc

        unpaired = (MSCRecord.objects
                    .filter(file=cdr_file, paired_record__isnull=True)
                    .exclude(call_reference='')
                    .exclude(call_reference__isnull=True))
        pair_count = correlate_unpaired_msc(unpaired, candidate_window_days=1)
        if pair_count:
            logger.info(
                f'MSC correlation: linked {pair_count} CDR pair(s) for file #{cdr_file.pk}'
            )
