#!/usr/bin/env python3
"""
Huawei MSC CDR Decoder - CloudMSoftX3000
=========================================
Complete decoder for MSC CDR files with standardized output format.

Supports: MOC, MTC, Transit, SMS-MO, SMS-MT, Forwarding, Roaming, Gateway, HLR
Based on: CloudMSOFTX3000 V500R012C35 ASN.1 CDR Description

Author: Claude Assistant
Version: 3.0 - Complete Field Mapping
"""

import os
import sys
import csv
import json
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
from collections import defaultdict

from core.utils.prepaid import derive_msc_prepaid_flag
from functools import lru_cache

# =============================================================================
# STANDARDIZED OUTPUT FIELDS (matching mediation system)
# =============================================================================

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
    'ROAMING_ICR_INDICATOR', 'CALL_END_DATETIME', 'FORWARDED_NUMBER', 'REDIRECTING_NUMBER',
    'TAC', 'CALL_CATEGORY',
    'DIAG_FOR_TERM', 'DISCONNECT_PARTY', 'CALLER_PORTED_FLAG', 'CALLED_PORTED_FLAG',
    'LOCATION_ROUTING_NUMBER', 'CALL_ORIGIN', 'PARTIAL_RECORD_TYPE_VAL',
    'CHARGED_PARTY_VAL', 'CAMEL_PHASE', 'SERVICE_KEY', 'CAMEL_SERVICE_KEY',
    'LAST_LOCATION_STR', 'SUBS_CATEGORY_RAW', 'SWITCH_IDENTITY',
]

# =============================================================================
# BIG DATA OUTPUT FIELDS
# =============================================================================

BIG_DATA_OUTPUT_FIELDS = [
    'ACCESS_METHOD_IDENTIFIER',
    'CALLING_PARTY_NUMBER', 'CALLED_PARTY_NUMBER',
    'IMEI', 'IMSI',
    'CALL_START_DATE', 'CALL_START_TIME', 'CHARGEABLE_DURATION',
    'MARKET_CATEGORY_TYPE', 'MOBILE_COUNTRY_CODE', 'MOBILE_NETWORK_CODE',
    'CELL_ID', 'LAC_ID', 'ORIGIN_LOC_INFO',
    'CALL_TYPE', 'SERVICE_USAGE_DIRECTION',
    'INCOMING_ROUTE', 'OUTGOING_ROUTE', 'MSC_IDENTIFICATION', 'SWITCH_IDENTITY',
    'CALL_REFERENCE_NUMBER', 'SERVICE_CENTRE_ADDRESS', 'BEARER_SERVICE_CODE',
    'DISCONNECTING_PARTY', 'CHARGED_PARTY',
    'FIRST_CALLED_LOCATION_INF', 'LAST_CALLED_LOCATION_INF',
    'FIRST_CALLING_LOCATION_INF', 'LAST_CALLING_LOCATION_INF',
    'ORIGINAL_CALLED_NUMBER', 'TERMINATING_LOCATION_INFORMATION',
    'CALLING_NUMBER_PORT_FLAG', 'CALLED_NUMBER_PORT_FLAG',
    'CALLING_NUMBER_SERVICE_PROVIDER', 'CALLED_NUMBER_SERVICE_PROVIDER',
    'SYSTEM_TYPE', 'SUBS_CATEGORY',
    'CALLING_NUMBER_COUNTRY_CODE', 'CALLED_NUMBER_COUNTRY_CODE',
    'ROAMER_TYPE', 'ROAMING_TYPE', 'CALL_CATEGORY', 'LOCATION_ROUTING_NUMBER',
    'ORIGINATING_MEMBER', 'TERMINATING_MEMBER',
    'CF_REDIRECTINGNUMBER', 'CAMELPHASE', 'SERVICEKEY',
    'MS_CALL_REF', 'CALLORIGIN', 'PARTIAL_RECORD_TYPE', 'PARTIAL_RECORD_NO',
    'CDR_RECORD_NUMBER', 'CALL_END_DATETIME', 'CAUSE_FOR_TERM', 'DIAG_FOR_TERM',
]

# Default home identity (Orange SL) — used as a fallback when no operator config
# is available (standalone decode, unmigrated DB, or unknown operator). The
# active operator's real identity is resolved at runtime via home_identity().
HOME_PLMN   = '61901'
HOME_MCC    = '619'
HOME_MNC    = '01'
POSTPAID_IMSI_PREFIX = '6190176100'

# Cache of resolved home identities, keyed by operator code (config is stable
# during a run). Cleared by clear_home_identity_cache() in tests.
_HOME_IDENTITY_CACHE: dict = {}


def clear_home_identity_cache() -> None:
    _HOME_IDENTITY_CACHE.clear()


def home_identity():
    """Return (home_plmn, home_mcc, home_mnc) for the active operator, falling
    back to the Orange defaults above when the operator registry is
    unavailable. Used for roaming detection / home-subscriber checks.
    Prepaid/postpaid is NOT derived here — it comes from the CAMEL
    serviceKey/camelPhase signal (see core.utils.prepaid.derive_msc_prepaid_flag)."""
    try:
        from core.operator_context import get_operator
        code = get_operator()
    except Exception:
        code = None

    if code in _HOME_IDENTITY_CACHE:
        return _HOME_IDENTITY_CACHE[code]

    plmn, mcc, mnc = HOME_PLMN, HOME_MCC, HOME_MNC
    try:
        from reference.models import Operator
        op = Operator.objects.filter(code=code).first()
        if op:
            plmn = op.home_plmn or plmn
            mcc = op.home_mcc or mcc
            mnc = op.home_mnc or mnc
    except Exception:
        pass

    result = (plmn, mcc, mnc)
    _HOME_IDENTITY_CACHE[code] = result
    return result

# Map internal ORIGINAL_CALL_TYPE → BIG_DATA CALL_TYPE codes
_BIGDATA_CT_MAP = {
    'MOC':      'MOC',
    'MTC':      'MTC',
    'GWIN':     'GWI',
    'GWOUT':    'GWO',
    'SMS-MO':   'SMSMO',
    'SMSMO_IW': 'SMSMO',
    'SMS-MT':   'SMSMT',
    'SMSMT_GW': 'SMSMT',
}
# CallForwarding: tag 0xAF = CF, tag 0xB1 = RCF

# SYSTEM_TYPE enum
_SYSTEM_TYPE_MAP = {
    '0': 'Unknown', '1': 'iuUTRAN', '2': 'gERAN', '3': 'accessVoBB',
    '4': 'LTE', '5': 'NR',
}

# DISCONNECTING_PARTY enum
_DISCONNECT_PARTY_MAP = {
    '0': 'unknownparty', '1': 'callingPartyRelease',
    '2': 'calledPartyRelease', '3': 'networkRelease',
}

# subscriberCategory enum
_SUBS_CATEGORY_MAP = {
    '0': 'unknownuser', '1': 'frenchuser', '2': 'englishuser',
    '3': 'germanuser', '4': 'russianuser', '5': 'spanishuser',
    '6': 'specialuser', '9': 'reserveuser', '10': 'commonuser',
    '11': 'superioruser', '12': 'datacalluser', '13': 'testcalluser',
    '14': 'spareuser', '15': 'payphoneuser', '32': 'coinuser',
}


def _sig(n: str) -> str:
    """Last 8 significant digits of a phone number string, for fuzzy comparison."""
    d = ''.join(c for c in n if c.isdigit())
    return d[-8:] if len(d) > 8 else d


def _b_candidate(n: str, c: str) -> bool:
    """Return True if n is a non-empty B-party distinct from C (by last-8-digit comparison)."""
    return bool(n) and _sig(n) != _sig(c)


def _bd_ref_to_num(ref: str) -> str:
    """Convert a hex CALL_REF to decimal number string."""
    if not ref:
        return ''
    try:
        return str(int(ref, 16))
    except (ValueError, TypeError):
        try:
            return str(int(ref))
        except (ValueError, TypeError):
            return ref


# Module-level caches: loaded once per process, shared across all files decoded
# in the same worker.  Keys are PLMN string (mcc+mnc) or digit prefix.
_MCCMNC_CACHE: dict = {}          # '61901' → 'Orange SL'
_MCCMNC_LOADED: bool = False
_CC_CACHE: dict = {}               # '232' → '232', '447' → '44', …
_CC_LOADED: bool = False


def _ensure_mccmnc_cache() -> None:
    global _MCCMNC_CACHE, _MCCMNC_LOADED
    if _MCCMNC_LOADED:
        return
    try:
        from reference.models import MccMnc
        for row in MccMnc.objects.filter(enabled=True).values('mcc', 'mnc', 'operator'):
            _MCCMNC_CACHE[row['mcc'] + row['mnc']] = row['operator']
        _MCCMNC_LOADED = True
    except Exception:
        _MCCMNC_LOADED = True  # don't retry on every record if DB unavailable


def _ensure_cc_cache() -> None:
    global _CC_CACHE, _CC_LOADED
    if _CC_LOADED:
        return
    try:
        from reference.models import NumberingPlan
        for row in NumberingPlan.objects.filter(enabled=True).values('prefix', 'country_code'):
            if row['country_code']:
                _CC_CACHE[row['prefix']] = row['country_code']
        _CC_LOADED = True
    except Exception:
        _CC_LOADED = True


def _bd_operator_by_imsi(imsi: str) -> str:
    """Lookup operator name from IMSI first 5 digits (MCC+MNC). Single DB load, then in-memory."""
    if not imsi or len(imsi) < 5:
        return ''
    _ensure_mccmnc_cache()
    # Try 5-digit PLMN (MCC3+MNC2) then 6-digit (MCC3+MNC3)
    return (_MCCMNC_CACHE.get(imsi[:5])
            or _MCCMNC_CACHE.get(imsi[:6])
            or '')


def _bd_country_code(number: str, ton: str = '') -> str:
    """
    Extract ITU-T country code from a phone number.
    Returns empty string for local (national) numbers.
    Single DB load into _CC_CACHE, then pure dict lookups.
    """
    if not number:
        return ''
    digits = ''.join(c for c in number if c.isdigit())
    if not digits:
        return ''

    if ton == '2':
        return '232'

    _ensure_cc_cache()
    for length in (4, 3, 2, 1):
        cc = _CC_CACHE.get(digits[:length])
        if cc:
            return cc

    # Fallback: hard-coded Sierra Leone and North America
    if digits.startswith('232'):
        return '232'
    if digits.startswith('1') and len(digits) == 11:
        return '1'
    return ''


def _build_bigdata_record(rec: dict, filename: str, record_num: int) -> dict:
    """Translate an internally-decoded MSC record dict into a BIG_DATA output record."""
    bd = {f: '' for f in BIG_DATA_OUTPUT_FIELDS}

    # Active operator's home identity (per-operator; Orange fallback).
    home_plmn, home_mcc, home_mnc = home_identity()

    ct  = rec.get('ORIGINAL_CALL_TYPE', '') or ''
    tag = rec.get('_RECORD_TAG', 0)

    if ct == 'CallForwarding':
        bigdata_ct = 'RCF' if tag == 0xB1 else 'CF'
    else:
        bigdata_ct = _BIGDATA_CT_MAP.get(ct, ct)

    imsi   = rec.get('CHARGED_PARTY_IMSI', '') or ''
    msisdn = rec.get('CHARGED_PARTY_MSISDN', '') or ''
    calling = rec.get('CALLING_NO', '') or ''
    called  = rec.get('CALLED_NO', '') or ''
    dialed  = rec.get('DIALED_NO', '') or ''
    net     = rec.get('NETWORK_ENTITY', '') or ''
    net_d   = ''.join(c for c in net if c.isdigit())
    lac     = rec.get('LAC_IDENTIFIER', '') or ''
    cell    = rec.get('CELL_ID_A', '') or ''
    msc_id  = rec.get('MSC_ID', '') or ''
    call_ref = rec.get('CALL_REF', '') or ''

    # 1. ACCESS_METHOD_IDENTIFIER — the served subscriber's MSISDN.
    # GW records: use calling party.  Terminating records: served = B (fallback CALLED_NO).
    # Originating records (MOC/CF/SMS-MO): served = A (fallback CALLING_NO).
    if bigdata_ct in ('GWI', 'GWO'):
        bd['ACCESS_METHOD_IDENTIFIER'] = calling
    elif bigdata_ct in ('MTC', 'SMSMT', 'RCF'):
        bd['ACCESS_METHOD_IDENTIFIER'] = msisdn or called
    else:
        bd['ACCESS_METHOD_IDENTIFIER'] = msisdn or calling

    # 2. IMEI
    bd['IMEI'] = rec.get('IMEI_A', '') or ''

    # 3. IMSI
    bd['IMSI'] = imsi

    # 4. CALLING_PARTY_NUMBER
    bd['CALLING_PARTY_NUMBER'] = rec.get('CALLING_NO', '') if bigdata_ct == 'SMSMT' else calling

    # 5. CALLED_PARTY_NUMBER
    if bigdata_ct == 'RCF':
        bd['CALLED_PARTY_NUMBER'] = dialed or rec.get('FORWARDED_NUMBER', '') or called
    elif bigdata_ct == 'SMSMT':
        bd['CALLED_PARTY_NUMBER'] = msisdn or called
    else:
        bd['CALLED_PARTY_NUMBER'] = called

    # 6 & 7. CALL_START_DATE / CALL_START_TIME
    start_dt = rec.get('START_DATETIME', '') or ''
    if len(start_dt) >= 14:
        bd['CALL_START_DATE'] = start_dt[:8]
        bd['CALL_START_TIME'] = start_dt[8:14]
    elif len(start_dt) >= 8:
        bd['CALL_START_DATE'] = start_dt[:8]

    # 8. CHARGEABLE_DURATION
    if bigdata_ct not in ('SMSMO', 'SMSMT'):
        bd['CHARGEABLE_DURATION'] = rec.get('CALL_DURATION', '') or ''

    # 9. MARKET_CATEGORY_TYPE — derived from the CAMEL IN-trigger
    #    (serviceKey / camelPhase), the same signal used for Subscriber Type /
    #    PREPAID_FLAG. IMSI prefixes are no longer used to classify prepaid.
    if bigdata_ct in ('MOC', 'CF', 'SMSMO'):
        _sk = rec.get('SERVICE_KEY') or rec.get('CAMEL_SERVICE_KEY') or rec.get('SERVICEKEY')
        _cp = rec.get('CAMEL_PHASE') or rec.get('CAMELPHASE')
        bd['MARKET_CATEGORY_TYPE'] = derive_msc_prepaid_flag(_sk, _cp)

    # 10 & 11. MOBILE_COUNTRY_CODE / MOBILE_NETWORK_CODE
    if bigdata_ct != 'RCF' and len(net_d) >= 5:
        bd['MOBILE_COUNTRY_CODE'] = net_d[:3]
        bd['MOBILE_NETWORK_CODE'] = net_d[3:]

    # 12. CELL_ID
    bd['CELL_ID'] = cell

    # 13. LAC_ID
    bd['LAC_ID'] = lac

    # 14. ORIGIN_LOC_INFO
    if bigdata_ct in ('SMSMO', 'SMSMT'):
        bd['ORIGIN_LOC_INFO'] = msc_id
    elif bigdata_ct in ('GWI', 'GWO'):
        bd['ORIGIN_LOC_INFO'] = net_d or rec.get('SWITCH_IDENTITY', '')
    else:
        bd['ORIGIN_LOC_INFO'] = msc_id

    # 15. INCOMING_ROUTE
    if bigdata_ct not in ('SMSMO', 'SMSMT'):
        bd['INCOMING_ROUTE'] = rec.get('ORIGINATING_TRUNK', '') or ''

    # 16. OUTGOING_ROUTE
    if bigdata_ct not in ('SMSMO', 'SMSMT'):
        bd['OUTGOING_ROUTE'] = rec.get('TERMINATING_TRUNK', '') or ''

    # 17. MSC_IDENTIFICATION
    bd['MSC_IDENTIFICATION'] = msc_id

    # 18. SWITCH_IDENTITY
    bd['SWITCH_IDENTITY'] = net_d

    # 19. CALL_REFERENCE_NUMBER
    if bigdata_ct != 'SMSMT':
        bd['CALL_REFERENCE_NUMBER'] = _bd_ref_to_num(call_ref)

    # 20. SERVICE_CENTRE_ADDRESS
    if bigdata_ct in ('SMSMO', 'SMSMT'):
        bd['SERVICE_CENTRE_ADDRESS'] = msc_id

    # 21. BEARER_SERVICE_CODE
    bd['BEARER_SERVICE_CODE'] = rec.get('BEARER_SERVICE_CODE', '') or ''

    # 22. DISCONNECTING_PARTY
    dp_raw = rec.get('DISCONNECT_PARTY', '') or ''
    bd['DISCONNECTING_PARTY'] = _DISCONNECT_PARTY_MAP.get(dp_raw, dp_raw)

    # 23. CALL_TYPE
    bd['CALL_TYPE'] = bigdata_ct

    # 24. SERVICE_USAGE_DIRECTION
    _dir_map = {'MOC': 'O', 'CF': 'O', 'SMSMO': 'O', 'GWO': 'O',
                'MTC': 'I', 'RCF': 'I', 'SMSMT': 'I', 'GWI': 'I'}
    bd['SERVICE_USAGE_DIRECTION'] = _dir_map.get(bigdata_ct, rec.get('CALL_DIRECTION', ''))

    # 25. CHARGED_PARTY
    bd['CHARGED_PARTY'] = rec.get('CHARGED_PARTY_VAL', '') or ''

    # 26. FIRST_CALLED_LOCATION_INF
    if bigdata_ct in ('MTC', 'SMSMT', 'GWI') and net_d:
        bd['FIRST_CALLED_LOCATION_INF'] = f"{net_d}{lac}{cell}"

    # 27. LAST_CALLED_LOCATION_INF
    if bigdata_ct in ('MTC', 'GWI'):
        bd['LAST_CALLED_LOCATION_INF'] = rec.get('LAST_LOCATION_STR', '') or ''

    # 28. FIRST_CALLING_LOCATION_INF
    if bigdata_ct in ('MOC', 'CF', 'SMSMO', 'GWO') and net_d:
        bd['FIRST_CALLING_LOCATION_INF'] = f"{net_d}{lac}{cell}"

    # 29. LAST_CALLING_LOCATION_INF
    if bigdata_ct in ('MOC', 'CF', 'GWO'):
        bd['LAST_CALLING_LOCATION_INF'] = rec.get('LAST_LOCATION_STR', '') or ''

    # 30. ORIGINAL_CALLED_NUMBER
    if bigdata_ct == 'SMSMO':
        bd['ORIGINAL_CALLED_NUMBER'] = called
    elif bigdata_ct == 'SMSMT':
        bd['ORIGINAL_CALLED_NUMBER'] = msisdn
    else:
        bd['ORIGINAL_CALLED_NUMBER'] = dialed or called

    # 31. TERMINATING_LOCATION_INFORMATION
    if bigdata_ct in ('MTC', 'RCF', 'SMSMT'):
        bd['TERMINATING_LOCATION_INFORMATION'] = msc_id

    # 32 & 33. PORT FLAGS
    if bigdata_ct not in ('SMSMO', 'SMSMT'):
        bd['CALLING_NUMBER_PORT_FLAG'] = rec.get('CALLER_PORTED_FLAG', '') or ''
        bd['CALLED_NUMBER_PORT_FLAG']  = rec.get('CALLED_PORTED_FLAG', '') or ''

    # 34. CALLING_NUMBER_SERVICE_PROVIDER
    if bigdata_ct in ('MOC', 'CF', 'RCF', 'SMSMO'):
        bd['CALLING_NUMBER_SERVICE_PROVIDER'] = _bd_operator_by_imsi(imsi)
    elif bigdata_ct == 'GWO':
        bd['CALLING_NUMBER_SERVICE_PROVIDER'] = net_d

    # 35. CALLED_NUMBER_SERVICE_PROVIDER
    called_imsi = rec.get('IMSI_B', '') or ''
    if bigdata_ct in ('MOC', 'CF', 'SMSMO') and called_imsi:
        bd['CALLED_NUMBER_SERVICE_PROVIDER'] = _bd_operator_by_imsi(called_imsi)
    elif bigdata_ct in ('MTC', 'SMSMT'):
        bd['CALLED_NUMBER_SERVICE_PROVIDER'] = _bd_operator_by_imsi(imsi)
    elif bigdata_ct == 'GWI':
        bd['CALLED_NUMBER_SERVICE_PROVIDER'] = net_d

    # 36. SYSTEM_TYPE
    if bigdata_ct not in ('RCF', 'GWI', 'GWO'):
        rat = rec.get('RAT_TYPE', '') or ''
        bd['SYSTEM_TYPE'] = _SYSTEM_TYPE_MAP.get(rat, rat)

    # 37. SUBS_CATEGORY
    sc_raw = rec.get('SUBS_CATEGORY_RAW', '') or ''
    bd['SUBS_CATEGORY'] = _SUBS_CATEGORY_MAP.get(sc_raw, sc_raw)

    # 38 & 39. COUNTRY CODES
    bd['CALLING_NUMBER_COUNTRY_CODE'] = _bd_country_code(calling)
    bd['CALLED_NUMBER_COUNTRY_CODE']  = _bd_country_code(called)

    # 40. ROAMER_TYPE
    if bigdata_ct in ('GWI', 'GWO'):
        bd['ROAMER_TYPE'] = '1' if net_d.startswith(home_plmn) else '0'
    else:
        bd['ROAMER_TYPE'] = '1' if imsi.startswith(home_plmn) else '0'

    # 41. ROAMING_TYPE
    # Sources: IMSI prefix (subscriber identity), NETWORK_ENTITY (serving PLMN),
    # ROAMING_ICR_INDICATOR (explicit roaming flag from tag 0x88).
    roaming_icr = rec.get('ROAMING_ICR_INDICATOR', '') == '1'
    ref_imsi = imsi if imsi else (net_d if bigdata_ct in ('GWI', 'GWO') else '')
    if ref_imsi and len(ref_imsi) >= 5:
        r_mcc = ref_imsi[:3]
        r_mnc = ref_imsi[3:5]
        is_home_sub = (r_mcc == home_mcc and r_mnc == home_mnc)
        # Outbound roaming: home IMSI but serving PLMN is not home PLMN
        is_outbound = is_home_sub and bool(net_d) and not net_d.startswith(home_plmn)
        if r_mcc != home_mcc:
            # Foreign IMSI → inbound roamer
            bd['ROAMING_TYPE'] = 'INTERNATIONAL'
        elif is_outbound or roaming_icr:
            # Home IMSI in foreign network, or switch set the roaming ICR flag
            bd['ROAMING_TYPE'] = 'INTERNATIONAL'
        elif r_mnc != home_mnc:
            bd['ROAMING_TYPE'] = 'NATIONAL'
        else:
            bd['ROAMING_TYPE'] = 'NON-ROAMER'
    elif roaming_icr:
        # No IMSI available but explicit roaming flag present
        bd['ROAMING_TYPE'] = 'INTERNATIONAL'

    # 42. CALL_CATEGORY — from _classify_call() in decode_msc_record
    bd['CALL_CATEGORY'] = rec.get('CALL_CATEGORY', '') or ''

    # 43. LOCATION_ROUTING_NUMBER
    if bigdata_ct not in ('SMSMO', 'SMSMT'):
        bd['LOCATION_ROUTING_NUMBER'] = rec.get('LOCATION_ROUTING_NUMBER', '') or ''

    # 43 & 44. SIP TRUNK MEMBERS
    if bigdata_ct in ('GWI', 'GWO'):
        bd['ORIGINATING_MEMBER'] = rec.get('ORIGINATING_MEMBER', '') or ''
        bd['TERMINATING_MEMBER'] = rec.get('TERMINATING_MEMBER', '') or ''

    # 45. CF_REDIRECTINGNUMBER
    if bigdata_ct == 'CF':
        bd['CF_REDIRECTINGNUMBER'] = rec.get('FORWARDED_NUMBER', '') or ''
    elif bigdata_ct in ('MTC', 'RCF', 'GWI', 'GWO'):
        bd['CF_REDIRECTINGNUMBER'] = rec.get('REDIRECTING_NUMBER', '') or ''

    # 46. CAMELPHASE — write for every record type that can carry CAMEL.
    # The previous rule only wrote it for MOC / CF, which meant the
    # downstream prepaid rule lost the camelPhase signal for SMSMO /
    # SMSMT / MTC / GWO / GWI even when their CAMEL fields had been
    # parsed by the decoder.
    bd['CAMELPHASE'] = rec.get('CAMEL_PHASE', '') or ''

    # 47. SERVICEKEY — SMS records put it in CAMEL_SERVICE_KEY (from the
    # 0xAC/0xAD cAMELSMSInformation tag); everything else uses the
    # SERVICE_KEY field promoted from BF20 / BF21 by
    # parse_camel_voice_information (per-call-type subscriber-leg rule).
    # Fall back to CAMEL_SERVICE_KEY in either case so any populated
    # signal lands in SERVICEKEY.
    bd['SERVICEKEY'] = (
        rec.get('SERVICE_KEY')
        or rec.get('CAMEL_SERVICE_KEY')
        or ''
    )

    # 48. MS_CALL_REF
    bd['MS_CALL_REF'] = _bd_ref_to_num(call_ref)

    # 49. CALLORIGIN
    bd['CALLORIGIN'] = rec.get('CALL_ORIGIN', '') or ''

    # 50 & 51. PARTIAL RECORD
    if bigdata_ct not in ('SMSMO', 'SMSMT'):
        bd['PARTIAL_RECORD_TYPE'] = rec.get('PARTIAL_RECORD_TYPE_VAL', '') or ''
        bd['PARTIAL_RECORD_NO']   = rec.get('PARTIAL_RECORD_NO', '') or ''

    # 52. CDR_RECORD_NUMBER
    bd['CDR_RECORD_NUMBER'] = str(record_num)

    # 53. CALL_END_DATETIME
    if bigdata_ct not in ('SMSMO', 'SMSMT'):
        bd['CALL_END_DATETIME'] = rec.get('CALL_END_DATETIME', '') or ''

    # 54. CAUSE_FOR_TERM
    if bigdata_ct not in ('SMSMO', 'SMSMT'):
        bd['CAUSE_FOR_TERM'] = rec.get('RESULT_CODE', '') or ''

    # 55. DIAG_FOR_TERM
    bd['DIAG_FOR_TERM'] = rec.get('DIAG_FOR_TERM', '') or ''

    return bd


# =============================================================================
# RECORD TYPE DEFINITIONS
# =============================================================================

# Record type tags (context-specific constructed)
RECORD_TAGS = {
    0xA0: ('moCallRecord', 'O', 'MOC', 'VOICE', 'VOICE'),          # Mobile Originated Call
    0xA1: ('mtCallRecord', 'T', 'MTC', 'VOICE', 'VOICE'),          # Mobile Terminated Call
    0xA2: ('roamingRecord', 'O', 'ROAMING', 'VOICE', 'VOICE'),     # Roaming Call
    0xA3: ('incGatewayRecord', 'T', 'GWIN', 'VOICE', 'VOICE'),     # Incoming Gateway
    0xA4: ('outGatewayRecord', 'O', 'GWOUT', 'VOICE', 'VOICE'),    # Outgoing Gateway
    0xA5: ('transitRecord', 'T', 'TRANSIT', 'VOICE', 'VOICE'),     # Transit Call
    0xA6: ('moSMSRecord', 'O', 'SMS-MO', 'SMS', 'SMS'),             # SMS Mobile Originated
    0xA7: ('mtSMSRecord', 'T', 'SMS-MT', 'SMS', 'SMS'),             # SMS Mobile Terminated
    0xA8: ('moSMSIWRecord', 'O', 'SMSMO_IW', 'SMS', 'SMS'),        # SMS-MO Interworking
    0xA9: ('mtSMSGWRecord', 'T', 'SMSMT_GW', 'SMS', 'SMS'),        # SMS-MT Gateway
    0xAA: ('ssActionRecord', 'O', 'SS', 'SS', 'SS'),               # Supplementary Service
    0xAB: ('hlrIntRecord', 'O', 'HLR', 'HLR', 'HLR'),              # HLR Interrogation
    0xAC: ('locUpdateRecord', 'O', 'LOCUPD', 'LOCUPD', 'LOCUPD'),  # Location Update (VLR)
    0xAD: ('commonEquipRecord', 'O', 'EQUIP', 'EQUIP', 'EQUIP'),   # Common Equipment
    0xAE: ('moEmergencyRecord', 'O', 'EMERG', 'VOICE', 'VOICE'),   # Emergency Call
    0xAF: ('moCFRecord', 'O', 'CallForwarding', 'CallForwarding', 'VOICE'), # Call Forwarding
    0xB0: ('termCAMELRecord', 'T', 'CAMEL', 'VOICE', 'VOICE'),     # Terminating CAMEL
    0xB1: ('mtRoamingForward', 'T', 'CallForwarding', 'CallForwarding', 'VOICE'), # MT Roaming Forwarding
    0xB2: ('mSCsRVCCRecord', 'O', 'SRVCC', 'VOICE', 'VOICE'),      # Single Radio Voice Call Continuity
    0xB3: ('mtLCSRecord', 'T', 'MTLCS', 'LCS', 'LCS'),             # MT Location Request
    0xB4: ('moLCSRecord', 'O', 'MOLCS', 'LCS', 'LCS'),             # MO Location Request
    0xB5: ('niLCSRecord', 'O', 'NILCS', 'LCS', 'LCS'),             # Network Induced Location Request
    0xB6: ('sipOrigRecord', 'O', 'SIPO', 'VOICE', 'VOICE'),        # SIP Originated Call
    0xB7: ('sipTermRecord', 'T', 'SIPT', 'VOICE', 'VOICE'),        # SIP Terminated Call
    0xB8: ('sipSMSMORecord', 'O', 'SIP_SMSMO', 'SMS', 'SMS'),      # SIP SMS-MO
    0xB9: ('sipSMSMTRecord', 'T', 'SIP_SMSMT', 'SMS', 'SMS'),      # SIP SMS-MT
    0xBA: ('imeiObsRecord', 'O', 'IMEI_OBS', 'IMEI', 'IMEI'),      # IMEI Observation
    0xBF: ('extendedRecord', 'O', 'EXT', 'EXTENDED', 'EXTENDED'),  # Extended format record
}

# Cause for termination codes
CAUSE_FOR_TERM = {
    0x00: 'normalRelease',
    0x01: 'partialRecord',
    0x02: 'partialRecordCallReestablishment',
    0x03: 'unsuccessfulCallAttempt',
    0x04: 'stableCallAbnormalTermination',
    0x05: 'cAMELInitCallRelease',
    0x06: 'unauthorizedRequestingNetwork',
    0x07: 'unauthorizedLCSClient',
    0x08: 'positionMethodFailure',
    0x09: 'unknownOrUnreachableLCSClient',
    0x10: 'normalRelease',
}

# Teleservice codes
TELESERVICE_CODES = {
    0x00: '0',    # All teleservices
    0x10: '16',   # All speech transmission services
    0x11: '17',   # Telephony
    0x12: '18',   # Emergency calls
    0x20: '32',   # All SMS services
    0x21: '33',   # Short Message MT/PP
    0x22: '22',   # Short Message MO/PP
    0x60: '96',   # All fax services
    0x61: '97',   # Fax Group 3
}

# Bearer service codes
BEARER_CODES = {
    0x00: '0',    # All bearer services
    0x20: '32',   # All data CDA services
    0x21: '33',   # Data CDA 300 bps
    0x22: '34',   # Data CDA 1200 bps
    0x23: '35',   # Data CDA 2400 bps
    0x24: '36',   # Data CDA 4800 bps
    0x25: '37',   # Data CDA 9600 bps
    0x26: '38',   # General data CDA
}

# Charged party
CHARGED_PARTY = {
    0x00: 'callingParty',
    0x01: 'calledParty',
    0x02: 'noChargeParty',
}

# =============================================================================
# ASN.1 BER PARSER
# =============================================================================

class ASN1Parser:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
    
    def remaining(self) -> int:
        return len(self.data) - self.pos
    
    def read_byte(self) -> int:
        if self.pos >= len(self.data):
            raise ValueError("End of data")
        b = self.data[self.pos]
        self.pos += 1
        return b
    
    def peek_byte(self) -> int:
        if self.pos >= len(self.data):
            return -1
        return self.data[self.pos]
    
    def read_bytes(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError("Not enough data")
        result = self.data[self.pos:self.pos + n]
        self.pos += n
        return result
    
    def read_tag(self) -> Tuple[int, int, bool, int]:
        """Returns (tag_class, tag_number, is_constructed, full_tag)"""
        start_pos = self.pos
        b = self.read_byte()
        tag_class = (b >> 6) & 0x03
        is_constructed = bool(b & 0x20)
        tag_number = b & 0x1F
        full_tag = b
        
        if tag_number == 0x1F:
            # Long form tag
            tag_number = 0
            while True:
                b = self.read_byte()
                full_tag = (full_tag << 8) | b
                tag_number = (tag_number << 7) | (b & 0x7F)
                if not (b & 0x80):
                    break
        
        return tag_class, tag_number, is_constructed, full_tag
    
    def read_length(self) -> int:
        b = self.read_byte()
        if b < 0x80:
            return b
        num_octets = b & 0x7F
        if num_octets == 0:
            return -1  # Indefinite
        length = 0
        for _ in range(num_octets):
            length = (length << 8) | self.read_byte()
        return length
    
    def read_tlv(self) -> Tuple[int, int, bool, int, bytes]:
        """Returns (tag_class, tag_number, is_constructed, full_tag, value)"""
        tag_class, tag_number, is_constructed, full_tag = self.read_tag()
        length = self.read_length()
        if length < 0:
            raise ValueError("Indefinite length not supported")
        value = self.read_bytes(length)
        return tag_class, tag_number, is_constructed, full_tag, value

# =============================================================================
# DATA TYPE DECODERS
# =============================================================================

def decode_tbcd(data: bytes) -> str:
    """Decode TBCD (Telephony BCD) string"""
    result = []
    for byte in data:
        low = byte & 0x0F
        high = (byte >> 4) & 0x0F
        
        if low <= 9:
            result.append(str(low))
        elif low == 0x0A:
            result.append('*')
        elif low == 0x0B:
            result.append('#')
        elif low == 0x0C:
            result.append('a')
        elif low == 0x0D:
            result.append('b')
        elif low == 0x0E:
            result.append('c')
        elif low == 0x0F:
            pass
        
        if high <= 9:
            result.append(str(high))
        elif high == 0x0A:
            result.append('*')
        elif high == 0x0B:
            result.append('#')
        elif high == 0x0C:
            result.append('a')
        elif high == 0x0D:
            result.append('b')
        elif high == 0x0E:
            result.append('c')
        elif high == 0x0F:
            pass
    
    return ''.join(result)

def decode_address(data: bytes) -> Tuple[str, str]:
    """Decode GSM address (AddressString)"""
    if len(data) < 1:
        return '', ''

    noa_npi = data[0]
    type_ind = f'{noa_npi:02X}'
    ton = (noa_npi >> 4) & 0x07  # Type of Number (bits 6-5-4)

    if ton == 5 and len(data) > 1:
        # TON=5: Alphanumeric (GSM 7-bit / reversed-BCD ASCII encoding)
        # Per Huawei spec: ASCII characters encoded by reversed BCD codes
        # Each byte has its nibbles swapped: ASCII 0x31 stored as 0x13
        number = decode_reversed_bcd_ascii(data[1:])
    else:
        # Standard TBCD number encoding
        number = decode_tbcd(data[1:]) if len(data) > 1 else ''

    return number, type_ind


def decode_reversed_bcd_ascii(data: bytes) -> str:
    """Decode reversed-BCD-encoded ASCII string.

    Per Huawei CloudMSOFTX3000 spec: when origination/destination is a string
    of ASCII characters, each byte has its nibbles swapped.
    Example: '123456' (ASCII 31 32 33 34 35 36) stored as 13 23 33 43 53 63.
    Filler nibble 0xF is used for padding when odd number of nibbles.
    """
    result = []
    for byte in data:
        # Swap nibbles back to get original ASCII byte
        swapped = ((byte & 0x0F) << 4) | ((byte >> 4) & 0x0F)
        if swapped == 0xF0 or (swapped >> 4) == 0x0F:
            # Filler byte (0xF_ padding) — skip
            continue
        if 0x20 <= swapped <= 0x7E:
            result.append(chr(swapped))
        else:
            # Non-printable — keep as hex for safety
            result.append(f'{swapped:02X}')
    return ''.join(result)

def decode_bcd_timestamp(data: bytes) -> Tuple[str, str]:
    """Decode BCD timestamp"""
    if len(data) < 6:
        return '', '+0:00'
    
    try:
        year_ns = ((data[0] >> 4) * 10) + (data[0] & 0x0F)
        month_ns = ((data[1] >> 4) * 10) + (data[1] & 0x0F)
        day_ns = ((data[2] >> 4) * 10) + (data[2] & 0x0F)
        hour_ns = ((data[3] >> 4) * 10) + (data[3] & 0x0F)
        minute_ns = ((data[4] >> 4) * 10) + (data[4] & 0x0F)
        second_ns = ((data[5] >> 4) * 10) + (data[5] & 0x0F)
        
        ns_valid = (1 <= month_ns <= 12 and 1 <= day_ns <= 31 and 
                    0 <= hour_ns <= 23 and 0 <= minute_ns <= 59 and 0 <= second_ns <= 59)
        
        year_sw = (data[0] & 0x0F) * 10 + ((data[0] >> 4) & 0x0F)
        month_sw = (data[1] & 0x0F) * 10 + ((data[1] >> 4) & 0x0F)
        day_sw = (data[2] & 0x0F) * 10 + ((data[2] >> 4) & 0x0F)
        hour_sw = (data[3] & 0x0F) * 10 + ((data[3] >> 4) & 0x0F)
        minute_sw = (data[4] & 0x0F) * 10 + ((data[4] >> 4) & 0x0F)
        second_sw = (data[5] & 0x0F) * 10 + ((data[5] >> 4) & 0x0F)
        
        sw_valid = (1 <= month_sw <= 12 and 1 <= day_sw <= 31 and 
                    0 <= hour_sw <= 23 and 0 <= minute_sw <= 59 and 0 <= second_sw <= 59)
        
        if ns_valid:
            year, month, day, hour, minute, second = year_ns, month_ns, day_ns, hour_ns, minute_ns, second_ns
        elif sw_valid:
            year, month, day, hour, minute, second = year_sw, month_sw, day_sw, hour_sw, minute_sw, second_sw
        else:
            return '', '+0:00'
        
        if year >= 70:
            year += 1900
        else:
            year += 2000
        
        dt_str = f"{year:04d}{month:02d}{day:02d}{hour:02d}{minute:02d}{second:02d}"
        
        offset = '+0:00'
        if len(data) >= 7:
            tz_byte = data[6]
            if tz_byte == 0x2B:
                tz_sign = '+'
                if len(data) >= 9:
                    tz_hour = ((data[7] >> 4) * 10) + (data[7] & 0x0F)
                    tz_min = ((data[8] >> 4) * 10) + (data[8] & 0x0F)
                else:
                    tz_hour, tz_min = 0, 0
                offset = f"{tz_sign}{tz_hour}:{tz_min:02d}"
            elif tz_byte == 0x2D:
                tz_sign = '-'
                if len(data) >= 9:
                    tz_hour = ((data[7] >> 4) * 10) + (data[7] & 0x0F)
                    tz_min = ((data[8] >> 4) * 10) + (data[8] & 0x0F)
                else:
                    tz_hour, tz_min = 0, 0
                offset = f"{tz_sign}{tz_hour}:{tz_min:02d}"
        
        return dt_str, offset
    except Exception as e:
        return '', '+0:00'

def decode_integer(data: bytes) -> int:
    if not data:
        return 0
    value = 0
    for byte in data:
        value = (value << 8) | byte
    return value

def decode_unsigned(data: bytes) -> int:
    if not data:
        return 0
    value = 0
    for byte in data:
        value = (value << 8) | byte
    if value > 0xFFFFFFFF:
        return value & 0xFFFFFFFF
    return value

def decode_duration(data: bytes) -> int:
    if not data:
        return 0
    if len(data) > 4:
        data = data[:4]
    value = 0
    for byte in data:
        value = (value << 8) | byte
    return value

def decode_octet_string(data: bytes) -> str:
    return data.hex().upper()

def decode_ia5_string(data: bytes) -> str:
    try:
        return data.decode('ascii')
    except:
        return data.decode('latin-1', errors='ignore')

def decode_location(data: bytes) -> Tuple[str, str]:
    if len(data) < 4:
        return '', ''
    
    if len(data) >= 7:
        lac = (data[3] << 8) | data[4]
        cell_id = (data[5] << 8) | data[6]
    elif len(data) >= 5:
        lac = (data[0] << 8) | data[1]
        cell_id = (data[3] << 8) | data[4] if len(data) > 4 else (data[2] << 8) | data[3]
    else:
        lac = (data[0] << 8) | data[1] if len(data) >= 2 else 0
        cell_id = (data[2] << 8) | data[3] if len(data) >= 4 else 0
    
    return str(lac), str(cell_id)

def decode_global_area_id(data: bytes) -> Tuple[str, str, str]:
    if len(data) < 5:
        return '', '', ''
    
    mcc1 = data[0] & 0x0F
    mcc2 = (data[0] >> 4) & 0x0F
    mcc3 = data[1] & 0x0F
    mnc3 = (data[1] >> 4) & 0x0F
    mnc1 = data[2] & 0x0F
    mnc2 = (data[2] >> 4) & 0x0F
    
    mcc = f"{mcc1}{mcc2}{mcc3}"
    if mnc3 == 0x0F:
        mnc = f"{mnc1}{mnc2}"
    else:
        mnc = f"{mnc1}{mnc2}{mnc3}"
    
    lac = (data[3] << 8) | data[4]
    cell_id = (data[5] << 8) | data[6] if len(data) >= 7 else 0
    
    return f"{mcc}{mnc}", str(lac), str(cell_id)

def decode_basic_service(data: bytes) -> Tuple[str, str]:
    if len(data) < 2:
        return '', ''
    
    try:
        parser = ASN1Parser(data)
        tc, tn, ic, ft, val = parser.read_tlv()
        
        if tc == 2:
            code = decode_unsigned(val)
            if tn == 1:
                return TELESERVICE_CODES.get(code, str(code)), ''
            elif tn == 2:
                return '', BEARER_CODES.get(code, str(code))
        
        return '', ''
    except:
        return '', ''

def decode_trunk_group(data: bytes) -> Tuple[str, str]:
    try:
        parser = ASN1Parser(data)
        route = ''
        member = ''
        
        while parser.remaining() > 0:
            tc, tn, ic, ft, val = parser.read_tlv()
            if tc == 2:
                if tn == 0 or tn == 1:
                    try:
                        decoded = val.decode('ascii')
                        if decoded.isprintable():
                            route = decoded
                        else:
                            route = val.hex().upper()
                    except:
                        route = val.hex().upper()
                elif tn == 1:
                    member = str(decode_unsigned(val))
            elif tc == 0:
                if tn == 4:
                    try:
                        decoded = val.decode('ascii')
                        if decoded.isprintable():
                            route = decoded
                    except:
                        pass
        
        return route, member
    except:
        try:
            decoded = data.decode('ascii')
            if decoded.isprintable():
                return decoded, ''
        except:
            pass
        return '', ''

def decode_route_name(data: bytes) -> str:
    if not data or len(data) < 3:
        return ''
    
    try:
        pos = 0
        while pos < len(data) - 2:
            tag = data[pos]
            
            if tag == 0x81:
                length = data[pos + 1]
                if length > 0 and pos + 2 + length <= len(data):
                    name_bytes = data[pos + 2:pos + 2 + length]
                    name = name_bytes.decode('ascii', errors='ignore')
                    if name and all(c.isprintable() or c.isspace() for c in name):
                        return name.strip()
            
            elif tag == 0x80:
                length = data[pos + 1]
                if length > 0 and pos + 2 + length <= len(data):
                    name_bytes = data[pos + 2:pos + 2 + length]
                    try:
                        name = name_bytes.decode('ascii', errors='ignore')
                        if name and all(c.isprintable() or c.isspace() for c in name):
                            return name.strip()
                    except:
                        pass
            
            pos += 1
        
        try:
            decoded = data.decode('ascii', errors='ignore')
            if decoded and len(decoded) > 2 and all(c.isprintable() or c.isspace() for c in decoded):
                return decoded.strip()
        except:
            pass
        
    except:
        pass
    
    return ''

# =============================================================================
# MSC CDR RECORD DECODER
# =============================================================================

# ---------------------------------------------------------------------------
# Call classification helpers
# ---------------------------------------------------------------------------

# Orange Sierra Leone MSISDN prefixes (international E.164 + local formats)
_ORANGE_SL_PREFIXES = (
    '23276', '23275', '23274', '23279', '23278', '23273',
    '076', '075', '074', '079', '078', '073',
    '76', '75', '74', '79', '78', '73',
)


def _num_clean(n: str) -> str:
    """Strip all non-digit characters from a number string."""
    return ''.join(c for c in (n or '') if c.isdigit())


def _is_special(n: str) -> bool:
    """Short codes / special service numbers (1–6 digits)."""
    return 1 <= len(_num_clean(n)) <= 6


def _is_international(n: str) -> bool:
    """Return True if the number is an international destination (not SL)."""
    n = (n or '').strip()
    if n.startswith('+'):
        return not n.startswith('+232')
    if n.startswith('00'):
        return not n.startswith('00232')
    # 11+ digit number not beginning with SL country code
    d = _num_clean(n)
    return len(d) >= 11 and not d.startswith('232')


def _is_onnet(n: str) -> bool:
    """Return True if the number belongs to Orange Sierra Leone."""
    n = (n or '').strip()
    return any(n.startswith(p) for p in _ORANGE_SL_PREFIXES)


def _classify_call(record: Dict):
    """
    Classify every MSC CDR record into a CALL_CATEGORY based on record type,
    subscriber IMSI, serving PLMN, and called/calling party numbers.

    Categories produced
    -------------------
    Voice MO    : OUTBOUND_ROAMING | SPECIAL_SERVICE | INTERNATIONAL
                  | NATIONAL_ONNET | NATIONAL_OFFNET
    Voice MT    : INBOUND_ROAMING | INCOMING_INTERNATIONAL
                  | INCOMING_ONNET | INCOMING_NATIONAL
    Roaming     : OUTBOUND_ROAMING | INBOUND_ROAMING
    Gateway in  : INCOMING_INTERNATIONAL | INCOMING_NATIONAL
    Gateway out : OUTGOING_INTERNATIONAL | OUTGOING_NATIONAL
    Transit     : TRANSIT_INTERNATIONAL | TRANSIT_NATIONAL
    CF          : CF_OUTBOUND_ROAMING | CF_ROAMING | CF_INTERNATIONAL
                  | CF_NATIONAL_ONNET | CF_NATIONAL_OFFNET
    Roaming CF  : RCF_INTERNATIONAL | RCF_NATIONAL
    SMS MO      : OUTBOUND_ROAMING_SMS | SMS_INTERNATIONAL
                  | SMS_NATIONAL_ONNET | SMS_NATIONAL_OFFNET
    SMS MT      : INBOUND_ROAMING_SMS | SMS_INCOMING_INTERNATIONAL
                  | SMS_INCOMING_NATIONAL
    Special     : EMERGENCY | SUPPLEMENTARY_SERVICE | LOCATION_UPDATE | SRVCC
    Default     : OTHER
    """
    call_type    = record.get('ORIGINAL_CALL_TYPE', '')
    imsi         = record.get('CHARGED_PARTY_IMSI', '') or ''
    net          = _num_clean(record.get('NETWORK_ENTITY', '') or '')
    called       = record.get('CALLED_NO', '') or record.get('DIALED_NO', '')
    calling      = record.get('CALLING_NO', '')
    forwarded    = record.get('FORWARDED_NUMBER', '')
    roaming_flag = record.get('ROAMING_ICR_INDICATOR', '') == '1'

    # Home subscriber determination (priority order):
    #   1. IMSI available → use PLMN prefix 61901 (Orange SL home PLMN)
    #   2. IMSI missing   → fall back to CHARGED_PARTY_MSISDN: Orange SL MSISDN prefixes
    #      or SL country code 232 (avoids false roaming_in for records with no IMSI)
    if imsi:
        is_home_sub = imsi.startswith('61901')
    else:
        msisdn_fb = record.get('CHARGED_PARTY_MSISDN', '') or ''
        is_home_sub = _is_onnet(msisdn_fb) or msisdn_fb.startswith('232')

    # Foreign serving PLMN: NETWORK_ENTITY has ≥5 digits and does NOT start with SL MCC 619.
    # When NETWORK_ENTITY comes from the Global Area ID tag (9F813C) it reflects the
    # actual serving cell's PLMN, making this a reliable roaming signal.
    # When it falls back to the IMSI prefix it equals the home PLMN (61901…) → not foreign.
    net_is_foreign = len(net) >= 5 and not net.startswith('619')

    # Outbound roaming: home subscriber served by a foreign network.
    # Use ROAMING_ICR_INDICATOR (visitedPlmn tag 0x88 was present) OR confirmed foreign PLMN.
    roaming_out = is_home_sub and (roaming_flag or net_is_foreign)

    # Inbound roaming: foreign subscriber (IMSI not from home PLMN) served in SL.
    # Guard: only assert inbound roaming when we actually have an IMSI to check;
    # records with empty IMSI default to is_home_sub=True above so roaming_in=False.
    roaming_in  = not is_home_sub

    # ---- MOC: Mobile Originated Call ----------------------------------------
    if call_type == 'MOC':
        if roaming_out:                     cat = 'OUTBOUND_ROAMING'
        elif _is_special(called):           cat = 'SPECIAL_SERVICE'
        elif _is_international(called):     cat = 'INTERNATIONAL'
        elif _is_onnet(called):             cat = 'NATIONAL_ONNET'
        else:                               cat = 'NATIONAL_OFFNET'

    # ---- MTC: Mobile Terminated Call ----------------------------------------
    elif call_type == 'MTC':
        if roaming_in:                      cat = 'INBOUND_ROAMING'
        elif _is_international(calling):    cat = 'INCOMING_INTERNATIONAL'
        elif _is_onnet(calling):            cat = 'INCOMING_ONNET'
        else:                               cat = 'INCOMING_NATIONAL'

    # ---- ROAMING record (0xA2 roamingRecord) --------------------------------
    elif call_type == 'ROAMING':
        cat = 'OUTBOUND_ROAMING' if is_home_sub else 'INBOUND_ROAMING'

    # ---- Gateway: Incoming (GWIN) -------------------------------------------
    # GWIN = call arriving at the MSC from the PSTN/external network.
    # Guard: only classify as INBOUND_ROAMING when an IMSI is present confirming
    # the served subscriber is foreign — otherwise every GWIN without IMSI would
    # fall into roaming_in (because is_home_sub defaults to False via MSISDN fallback).
    elif call_type == 'GWIN':
        if imsi and roaming_in:             cat = 'INBOUND_ROAMING'
        elif _is_international(calling):    cat = 'INCOMING_INTERNATIONAL'
        else:                               cat = 'INCOMING_NATIONAL'

    # ---- Gateway: Outgoing (GWOUT) ------------------------------------------
    # GWOUT = call leaving the MSC toward the PSTN/external network.
    # Same IMSI guard as GWIN for outbound roaming classification.
    elif call_type == 'GWOUT':
        if imsi and roaming_out:            cat = 'OUTBOUND_ROAMING'
        elif _is_international(called):     cat = 'OUTGOING_INTERNATIONAL'
        else:                               cat = 'OUTGOING_NATIONAL'

    # ---- Transit ------------------------------------------------------------
    elif call_type == 'TRANSIT':
        if _is_international(calling) or _is_international(called):
            cat = 'TRANSIT_INTERNATIONAL'
        else:
            cat = 'TRANSIT_NATIONAL'

    # ---- Call Forwarding (moCFRecord 0xAF, detected via tag 0x86/0x8B) ------
    elif call_type == 'CallForwarding':
        if roaming_out:                         cat = 'CF_OUTBOUND_ROAMING'
        elif roaming_flag:                      cat = 'CF_ROAMING'
        elif _is_international(forwarded):      cat = 'CF_INTERNATIONAL'
        elif _is_onnet(forwarded):              cat = 'CF_NATIONAL_ONNET'
        else:                                   cat = 'CF_NATIONAL_OFFNET'

    # ---- MT Roaming Forwarding (0xB1 mtRoamingForward) ---------------------
    elif call_type == 'ROAMING_FORWARDING':
        cat = 'RCF_INTERNATIONAL' if _is_international(forwarded) else 'RCF_NATIONAL'

    # ---- SMS Mobile Originated ----------------------------------------------
    elif call_type in ('SMS-MO', 'SMSMO_IW'):
        if roaming_out:                     cat = 'OUTBOUND_ROAMING_SMS'
        elif _is_international(called):     cat = 'SMS_INTERNATIONAL'
        elif _is_onnet(called):             cat = 'SMS_NATIONAL_ONNET'
        else:                               cat = 'SMS_NATIONAL_OFFNET'

    # ---- SMS Mobile Terminated ----------------------------------------------
    elif call_type in ('SMS-MT', 'SMSMT_GW'):
        if roaming_in:                      cat = 'INBOUND_ROAMING_SMS'
        elif _is_international(calling):    cat = 'SMS_INCOMING_INTERNATIONAL'
        else:                               cat = 'SMS_INCOMING_NATIONAL'

    # ---- Special record types -----------------------------------------------
    elif call_type == 'EMERG':              cat = 'EMERGENCY'
    elif call_type == 'SS':                 cat = 'SUPPLEMENTARY_SERVICE'
    elif call_type == 'LOCUPD':             cat = 'LOCATION_UPDATE'
    elif call_type == 'SRVCC':             cat = 'SRVCC'
    else:                                   cat = 'OTHER'

    record['CALL_CATEGORY'] = cat

def decode_msc_record(data: bytes, filename: str, record_tag: int) -> Dict[str, Any]:
    """Decode a single MSC CDR record"""
    record = {field: '' for field in OUTPUT_FIELDS}
    
    rt_info = RECORD_TAGS.get(record_tag, ('unknown', 'O', 'UNKNOWN', 'U', 'UNKNOWN'))
    rt_name, direction, call_type, service_type, service_id = rt_info
    
    record['PREPAID_FLAG'] = '2'
    record['SUBSCRIBER_CATEGORY'] = '2'
    record['SUBSCRIBER_TYPE'] = '0'
    record['EVENT_STATUS'] = '1'
    record['CALL_DIRECTION'] = direction
    record['ORIGINAL_CALL_TYPE'] = call_type
    record['SERVICE_TYPE'] = service_type
    record['SERVICE_ID'] = service_id
    record['MD_SPLIT_TYPE'] = 'E'
    record['CDR_FILE_NAME'] = filename
    record['LOAD_DATE'] = datetime.now().strftime('%Y%m%d%H%M%S')
    record['UTC_TIME_OFFSET'] = '+0:00'
    
    if call_type in ['SMS-MO', 'SMS-MT']:
        record['TELESERVICE_CODE'] = '22'
        record['CALL_DURATION'] = '1'
    
    try:
        pos = 0
        while pos < len(data) - 1:
            tag_byte = data[pos]
            
            if tag_byte == 0x9F:
                if pos + 1 >= len(data):
                    break
                
                ext1 = data[pos + 1]
                
                if ext1 & 0x80:
                    if pos + 2 >= len(data):
                        break
                    ext2 = data[pos + 2]
                    
                    len_pos = pos + 3
                    if len_pos >= len(data):
                        break
                    
                    length, consumed = parse_length(data, len_pos)
                    if length < 0:
                        pos += 1
                        continue
                    
                    val_start = len_pos + consumed
                    if val_start + length > len(data):
                        pos += 1
                        continue
                    
                    value = data[val_start:val_start + length]
                    inner_tag = ext2 & 0x7F
                    # Pass the prefix byte so the handler can disambiguate
                    # 0x9F 81 xx (most tags) from 0x9F 86 05 (calledServiceKey).
                    process_extended_tag_81(record, inner_tag, value, prefix=ext1 & 0x7F)

                    pos = val_start + length
                    continue
                else:
                    len_pos = pos + 2
                    if len_pos >= len(data):
                        break
                    
                    length, consumed = parse_length(data, len_pos)
                    if length < 0:
                        pos += 1
                        continue
                    
                    val_start = len_pos + consumed
                    if val_start + length > len(data):
                        pos += 1
                        continue
                    
                    value = data[val_start:val_start + length]
                    inner_tag = ext1 & 0x7F
                    
                    process_extended_tag(record, inner_tag, value)
                    
                    pos = val_start + length
                    continue
            
            elif tag_byte == 0xBF:
                if pos + 1 >= len(data):
                    break
                
                ext1 = data[pos + 1]
                len_pos = pos + 2
                
                if len_pos >= len(data):
                    break
                
                length, consumed = parse_length(data, len_pos)
                if length < 0:
                    pos += 1
                    continue
                
                val_start = len_pos + consumed
                if val_start + length > len(data):
                    pos += 1
                    continue
                
                value = data[val_start:val_start + length]
                
                process_bf_tag(record, ext1, value)
                
                pos = val_start + length
                continue
            
            elif 0x80 <= tag_byte <= 0xBE:
                len_pos = pos + 1
                if len_pos >= len(data):
                    break
                
                length, consumed = parse_length(data, len_pos)
                if length < 0:
                    pos += 1
                    continue
                
                val_start = len_pos + consumed
                if val_start + length > len(data):
                    pos += 1
                    continue
                
                value = data[val_start:val_start + length]
                
                process_standard_tag(record, tag_byte, value)
                
                pos = val_start + length
                continue
            
            pos += 1
        
        if not record['CALL_REF'] and record['NETWORK_RECORD_ID']:
            record['CALL_REF'] = record['NETWORK_RECORD_ID']

        if not record['START_DATETIME'] and record['CALL_END_DATETIME']:
            record['START_DATETIME'] = record['CALL_END_DATETIME']

        if record['START_DATETIME'] and not record['CALL_END_DATETIME']:
            record['CALL_END_DATETIME'] = record['START_DATETIME']

        # Final pass: compute PREPAID_FLAG (IMSI prefix) and SUBSCRIBER_CATEGORY (CAMEL rules).
        # CAMEL-aware records will have had _apply_subscriber_category() called already
        # during tag processing with real serviceKey/camelPhase values.
        # For records with no CAMEL tags, this call uses serviceKey=0 / camelPhase=0.
        _apply_prepaid_flag(record)
        if record.get('SUBSCRIBER_CATEGORY') == '2':
            _apply_subscriber_category(record)

        # Call Classification (Roaming, National, etc.)
        _classify_call(record)

        # -----------------------------------------------------------------------
        # CF field consistency pass
        # -----------------------------------------------------------------------
        # For CallForwarding records we expect three distinct parties:
        #   A → CALLING_NO     (the original caller)
        #   B → CALLED_NO      (the subscriber with CF active; from tag 0x85 calledNumber)
        #   C → FORWARDED_NUMBER (the final destination; from tag 0x87 connectedNumber)
        #
        # If C is missing (0x87 absent) but REDIRECTING_NUMBER (0x8A) is present and
        # different from B, use it as C — some switch variants populate 0x8A for C.
        # If CALLED_NO is empty (0x85/0x83 absent), fall back to REDIRECTING_NUMBER for B.
        # -----------------------------------------------------------------------
        if record.get('ORIGINAL_CALL_TYPE') == 'CallForwarding':
            fwd        = (record.get('FORWARDED_NUMBER') or '').strip()
            redir      = (record.get('REDIRECTING_NUMBER') or '').strip()
            called_now = (record.get('CALLED_NO') or '').strip()
            # servedMSISDN from tag 0x83 — most authoritative source for B
            served     = (record.get('CF_SERVED_MSISDN') or '').strip()

            if called_now and fwd and _sig(called_now) == _sig(fwd):
                # CALLED_NO == FORWARDED_NUMBER: tag 0x85 captured C instead of B.
                # Recovery priority: served (0x83) > redirecting (0x8A).
                if _b_candidate(served, fwd):
                    record['CALLED_NO'] = served
                    record['DIALED_NO'] = served
                elif _b_candidate(redir, fwd):
                    record['CALLED_NO'] = redir
                    record['DIALED_NO'] = redir
                # else: B genuinely equals C in this CDR — nothing to recover
            elif not fwd and redir:
                # No C decoded yet — use REDIRECTING_NUMBER as forwarding destination
                record['FORWARDED_NUMBER'] = redir
            elif not called_now:
                # B absent entirely
                if _b_candidate(served, fwd):
                    record['CALLED_NO'] = served
                    record['DIALED_NO'] = served
                elif _b_candidate(redir, fwd):
                    record['CALLED_NO'] = redir
                    record['DIALED_NO'] = redir
                elif fwd:
                    record['CALLED_NO'] = fwd
                    record['DIALED_NO'] = fwd

    except Exception as e:
        pass

    return record

def process_standard_tag(record: Dict, tag: int, value: bytes):
    """Process standard context-specific tags (0x80-0xBE)"""

    call_type = record.get('ORIGINAL_CALL_TYPE', '')
    is_gateway = call_type in ['GWIN', 'GWOUT']
    # is_cf: explicit CallForwarding record (tag 0xAF moCFRecord / 0xB1 mtRoamingForward).
    # Tag layout for moCFRecord (per Huawei CDR actual behaviour):
    #   0x83 = servedMSISDN  → B (subscriber with CF active)  → CALLED_NO / DIALED_NO
    #   0x84 = callingNumber → A (original caller)             → CALLING_NO
    #   0x85 = calledNumber  → B (same as 0x83, the dialled number) → CALLED_NO / DIALED_NO
    #   0x87 = connectedNumber → C (final forwarded-to party)  → FORWARDED_NUMBER
    #   0x86 = translatedNumber → C (alternative source for C) → FORWARDED_NUMBER
    is_cf  = call_type == 'CallForwarding'
    is_mtc = call_type == 'MTC'
    is_moc = call_type in ['MOC', 'EMERG'] or is_cf
    is_smsmt = call_type == 'SMS-MT'
    is_smsmo = call_type == 'SMS-MO'
    is_sms = is_smsmt or is_smsmo

    # =========================================================================
    # SMS-MT record tags (per Huawei CloudMSOFTX3000 V500R012C35 ASN.1 spec)
    # Tag layout is DIFFERENT from voice MOC/MTC records:
    #   0x80 = recordType (ENUMERATED, value 0x07)
    #   0x81 = serviceCentre (ADDRESS) - SMSC E.164 address
    #   0x82 = servedIMSI (TBCD-STRING) - IMSI of receiver
    #   0x83 = servedIMEI (TBCD-STRING) - IMEI of receiver
    #   0x84 = servedMSISDN (ADDRESS) - MSISDN of receiver (B-party/CALLED)
    #   0x85 = msClassmark (OCTET STRING) - NOT a phone number
    #   0x86 = recordingEntity (ADDRESS) - MSC E.164 number
    #   0x88 = deliveryTime (OCTET STRING timestamp)
    #   0xA9 = smsResult (CHOICE)
    #   0x9F 81 49 = origination (ADDRESS) - sender number (A-party/CALLING)
    # =========================================================================
    if is_smsmt:
        if tag == 0x80:
            pass  # recordType

        elif tag == 0x81:
            # serviceCentre - E.164 address of SMS service center (SMSC)
            sc_addr, _ = decode_address(value)
            if sc_addr and len(sc_addr) <= 20:
                record['MSC_ID'] = sc_addr  # Store SMSC address in MSC_ID

        elif tag == 0x82:
            # servedIMSI - IMSI of the receiving party
            imsi = decode_tbcd(value)
            if imsi and len(imsi) <= 15 and imsi.isdigit():
                record['CHARGED_PARTY_IMSI'] = imsi
                record['IMSI_A'] = imsi
                if len(imsi) >= 5:
                    record['NETWORK_ENTITY'] = imsi[:5]
                    record['OPERATOR_ID'] = imsi[:5]

        elif tag == 0x83:
            # servedIMEI - IMEI of the receiving party
            imei = decode_tbcd(value)
            if imei and len(imei) <= 16:
                record['IMEI_A'] = imei

        elif tag == 0x84:
            # servedMSISDN - MSISDN of the RECEIVING party (B-party = CALLED_NO)
            msisdn, _ = decode_address(value)
            if msisdn and len(msisdn) <= 20:
                record['CALLED_NO'] = msisdn
                record['DIALED_NO'] = msisdn
                record['CHARGED_PARTY_MSISDN'] = msisdn

        elif tag == 0x85:
            # Check if this could be the sender address (some MSC variants use 0x85 for origination)
            # Standard Huawei uses 0x85 for msClassmark, but if it looks like an address...
            if len(value) >= 3 and (value[0] & 0x80):
                orig, _ = decode_address(value)
                if orig and len(orig) <= 20 and any(c.isdigit() for c in orig):
                    record['CALLING_NO'] = orig
            else:
                record['MS_CLASSMARK'] = value.hex()

        elif tag == 0x86:
            # recordingEntity - E.164 number of the visited MSC
            msc, _ = decode_address(value)
            if msc and len(msc) <= 20:
                record['NETWORK_ENTITY'] = msc if not record.get('NETWORK_ENTITY') else record['NETWORK_ENTITY']

        elif tag == 0x88:
            # deliveryTime - timestamp when message was sent to the MS
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['START_DATETIME'] = dt
                record['UTC_TIME_OFFSET'] = offset

        elif tag == 0xA9:
            # smsResult - cause of SMS failure
            parse_diagnostics(record, value)

        elif tag == 0x8B:
            # systemType - GERAN/UTRAN/SIP
            record['RAT_TYPE'] = str(decode_unsigned(value))

        elif tag == 0xAC:
            # cAMELSMSInformation — parse serviceKey / camelPhase to derive PREPAID_FLAG
            parse_camel_sms_information(record, value)

        else:
            # For any other standard tags in SMSMT, use common handling
            _process_common_standard_tag(record, tag, value, is_gateway, is_moc)

        return

    # =========================================================================
    # SMS-MO record tags (per Huawei CloudMSOFTX3000 V500R012C35 ASN.1 spec)
    # Tag layout is DIFFERENT from voice MOC/MTC records:
    #   0x80 = recordType (ENUMERATED, value 0x06)
    #   0x81 = servedIMSI (TBCD-STRING) - IMSI of sender
    #   0x82 = servedIMEI (TBCD-STRING) - IMEI of sender
    #   0x83 = servedMSISDN (ADDRESS) - MSISDN of sender (A-party/CALLING)
    #   0x84 = msClassmark (OCTET STRING) - NOT a phone number
    #   0x85 = serviceCentre (ADDRESS) - SMSC E.164 address
    #   0x86 = recordingEntity (ADDRESS) - MSC E.164 number
    #   0x88 = messageReference (OCTET STRING)
    #   0x89 = originationTime (OCTET STRING timestamp)
    #   0x8C = destinationNumber (ADDRESS) - dest number (B-party/CALLED)
    # =========================================================================
    if is_smsmo:
        if tag == 0x80:
            pass  # recordType

        elif tag == 0x81:
            # servedIMSI - IMSI of the sending party
            imsi = decode_tbcd(value)
            if imsi and len(imsi) <= 15 and imsi.isdigit():
                record['CHARGED_PARTY_IMSI'] = imsi
                record['IMSI_A'] = imsi
                if len(imsi) >= 5:
                    record['NETWORK_ENTITY'] = imsi[:5]
                    record['OPERATOR_ID'] = imsi[:5]

        elif tag == 0x82:
            # servedIMEI - IMEI of the sending party
            imei = decode_tbcd(value)
            if imei and len(imei) <= 16:
                record['IMEI_A'] = imei

        elif tag == 0x83:
            # servedMSISDN - MSISDN of the SENDING party (A-party = CALLING_NO)
            msisdn, _ = decode_address(value)
            if msisdn and len(msisdn) <= 20:
                record['CALLING_NO'] = msisdn
                record['CHARGED_PARTY_MSISDN'] = msisdn

        elif tag == 0x84:
            # msClassmark - mobile station classmark (NOT a phone number)
            record['MS_CLASSMARK'] = value.hex()

        elif tag == 0x85:
            # serviceCentre - E.164 address of SMS service center (SMSC)
            sc_addr, _ = decode_address(value)
            if sc_addr and len(sc_addr) <= 20:
                record['MSC_ID'] = sc_addr  # Store SMSC address in MSC_ID

        elif tag == 0x86:
            # recordingEntity - E.164 number of the visited MSC
            msc, _ = decode_address(value)
            if msc and len(msc) <= 20:
                record['NETWORK_ENTITY'] = msc if not record.get('NETWORK_ENTITY') else record['NETWORK_ENTITY']

        elif tag == 0x88:
            # messageReference - reference provided by the MS
            record['CALL_REF'] = str(decode_integer(value))

        elif tag == 0x89:
            # originationTime - time message was received by MSC from subscriber
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['START_DATETIME'] = dt
                record['UTC_TIME_OFFSET'] = offset

        elif tag == 0x8C:
            # destinationNumber - destination subscriber number (B-party = CALLED_NO)
            dest, _ = decode_address(value)
            if dest and len(dest) <= 20:
                record['CALLED_NO'] = dest
                record['DIALED_NO'] = dest

        elif tag == 0x8E:
            # systemType - GERAN/UTRAN/SIP
            record['RAT_TYPE'] = str(decode_unsigned(value))

        elif tag == 0xAA:
            # smsResult - cause of SMS failure
            parse_diagnostics(record, value)

        elif tag == 0xAD:
            # cAMELSMSInformation — parse serviceKey / camelPhase to derive PREPAID_FLAG
            parse_camel_sms_information(record, value)

        else:
            # For any other standard tags in SMSMO, use common handling
            _process_common_standard_tag(record, tag, value, is_gateway, is_moc)

        return

    # =========================================================================
    # Voice / Gateway / Other record tags (original logic)
    # =========================================================================
    if tag == 0x80:
        pass

    elif tag == 0x81:
        if is_gateway:
            if not record.get('CALLING_NO'):
                calling, _ = decode_address(value)
                if calling and len(calling) <= 20 and calling[0].isdigit():
                    record['CALLING_NO'] = calling
        else:
            imsi = decode_tbcd(value)
            if len(imsi) <= 15 and imsi.isdigit():
                record['CHARGED_PARTY_IMSI'] = imsi
                record['IMSI_A'] = imsi
                if len(imsi) >= 5:
                    record['NETWORK_ENTITY'] = imsi[:5]
                    record['OPERATOR_ID'] = imsi[:5]

    elif tag == 0x82:
        if is_gateway:
            if not record.get('CALLED_NO'):
                called, _ = decode_address(value)
                if called and len(called) <= 20 and called[0].isdigit():
                    record['CALLED_NO'] = called
                    record['DIALED_NO'] = called
        else:
            imei = decode_tbcd(value)
            if len(imei) <= 16 and imei.replace('a', '').replace('b', '').replace('c', '').isdigit():
                record['IMEI_A'] = imei

    elif tag == 0x83:
        if is_gateway:
            msc, _ = decode_address(value)
            if msc and len(msc) <= 20:
                record['MSC_ID'] = msc
        elif is_cf:
            # moCFRecord / mtRoamingForward: tag 0x83 = servedMSISDN = the subscriber
            # who has CF activated (party B). This is what the original caller dialled,
            # so it maps to CALLED_NO / DIALED_NO — NOT to CALLING_NO.
            # Also stored in CF_SERVED_MSISDN so the consistency pass can recover B
            # even if tag 0x85 later overwrites CALLED_NO with C.
            msisdn, _ = decode_address(value)
            if msisdn and len(msisdn) <= 15:
                record['CHARGED_PARTY_MSISDN'] = msisdn
                record['CF_SERVED_MSISDN']     = msisdn
                record['CALLED_NO']  = msisdn
                record['DIALED_NO']  = msisdn
        else:
            msisdn, _ = decode_address(value)
            if msisdn and len(msisdn) <= 15:
                record['CHARGED_PARTY_MSISDN'] = msisdn
                if record['CALL_DIRECTION'] == 'O' and not record['CALLING_NO']:
                    record['CALLING_NO'] = msisdn
                if record['CALL_DIRECTION'] == 'T' and not record['CALLED_NO']:
                    record['CALLED_NO'] = msisdn
                    record['DIALED_NO'] = msisdn

    elif tag == 0x84:
        calling, _ = decode_address(value)
        if calling and len(calling) <= 20:
            record['CALLING_NO'] = calling
            if not record['CHARGED_PARTY_MSISDN']:
                record['CHARGED_PARTY_MSISDN'] = calling

    elif tag == 0x85:
        addr, _ = decode_address(value)
        if addr and len(addr) <= 20:
            if is_cf:
                # moCFRecord: 0x85 = calledNumber = the number A originally dialled (party B).
                # Party C (final connected destination) comes from 0x87 (connectedNumber).
                record['CALLED_NO'] = addr
                record['DIALED_NO'] = addr
            elif is_mtc:
                # MTC: 0x85 = connectedNumber — only meaningful in CF context.
                record['FORWARDED_NUMBER'] = addr
            else:
                # MOC: 0x85 = calledNumber (what the subscriber dialled = party B).
                record['CALLED_NO'] = addr
                record['DIALED_NO'] = addr

    elif tag == 0x86:
        if is_gateway:
            # seizureTime for GWIN/GWOUT — use only if answerTime (0x87) not yet set
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                if not record['START_DATETIME']:
                    record['START_DATETIME'] = dt
                    record['UTC_TIME_OFFSET'] = offset
        elif is_mtc:
            # recordingEntity — E.164 address of the recording MSC
            msc, _ = decode_address(value)
            if msc and len(msc) <= 20 and not record.get('MSC_ID'):
                record['MSC_ID'] = msc
        else:
            # translatedNumber / forwardedToNumber — the number the call was forwarded TO.
            # DIALED_NO stays as the original called number (already set from tag 0x85).
            trans, _ = decode_address(value)
            if trans and len(trans) <= 20:
                record['FORWARDED_NUMBER'] = trans
                # If an MOC record has a translated/forwarded number, it's a CF event
                if record.get('ORIGINAL_CALL_TYPE') == 'MOC':
                    record['ORIGINAL_CALL_TYPE'] = 'CallForwarding'
                    record['SERVICE_TYPE'] = 'CallForwarding'
    
    elif tag == 0x87:
        if is_gateway:
            # answerTime for GWIN/GWOUT
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['START_DATETIME'] = dt
                record['UTC_TIME_OFFSET'] = offset
        else:
            # connectedNumber — the final connected party (C) after forwarding.
            # Since 0x87 is definitively C, we can detect at decode time whether
            # calledNumber (0x85) also captured C instead of B, and fix it immediately
            # using servedMSISDN (0x83 → CF_SERVED_MSISDN) as the authoritative B.
            ct = record.get('ORIGINAL_CALL_TYPE', '')
            if ct == 'CallForwarding':
                connected, _ = decode_address(value)
                if connected and len(connected) <= 20:
                    record['FORWARDED_NUMBER'] = connected
                    # If CALLED_NO == connectedNumber, tag 0x85 had C not B.
                    # Restore B from servedMSISDN (0x83) if available and distinct.
                    called_now = (record.get('CALLED_NO') or '').strip()
                    served     = (record.get('CF_SERVED_MSISDN') or '').strip()
                    if called_now and _sig(called_now) == _sig(connected):
                        if _b_candidate(served, connected):
                            record['CALLED_NO'] = served
                            record['DIALED_NO'] = served
    
    elif tag == 0x88:
        if is_gateway:
            dt, _ = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['CALL_END_DATETIME'] = dt
        else:
            roam, _ = decode_address(value)
            if roam:
                record['ROAMING_ICR_INDICATOR'] = '1'
    
    elif tag == 0x89:
        if is_gateway:
            dur = decode_duration(value)
            if dur < 100000:
                record['CALL_DURATION'] = str(dur)
        else:
            msc, _ = decode_address(value)
            if msc and len(msc) <= 20:
                record['MSC_ID'] = msc
    
    elif tag == 0x8B:
        if is_gateway:
            cause = decode_unsigned(value)
            record['RESULT_CODE'] = CAUSE_FOR_TERM.get(cause, str(cause))
        else:
            # redirectionCounter
            count = decode_unsigned(value)
            if count > 0:
                record['ORIGINAL_CALL_TYPE'] = 'CallForwarding'
                record['SERVICE_TYPE'] = 'CallForwarding'

    elif tag == 0x8D:
        if is_gateway:
            # callReference for GWIN/GWOUT — OCTET STRING
            record['CALL_REF'] = value.hex().upper()

    elif tag == 0x9B:
        if is_mtc:
            # causeForTerm for MTC
            cause = decode_unsigned(value)
            record['RESULT_CODE'] = CAUSE_FOR_TERM.get(cause, str(cause))

    elif tag == 0x91:
        record['MS_CLASSMARK'] = str(decode_unsigned(value))
    
    elif tag == 0x93:
        # seizureTime — time the circuit was seized (before answer).
        # For MOC: do NOT use as START_DATETIME; answerTime (0x96) is the correct start.
        # For non-MOC records: use as START_DATETIME only if not already set.
        dt, offset = decode_bcd_timestamp(value)
        if dt and len(dt) == 14:
            if not is_moc:
                if not record['START_DATETIME']:
                    record['START_DATETIME'] = dt
                    record['UTC_TIME_OFFSET'] = offset

    elif tag == 0x94:
        if is_moc:
            record['MS_CLASSMARK'] = str(decode_unsigned(value))
        else:
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['START_DATETIME'] = dt
                record['UTC_TIME_OFFSET'] = offset

    elif tag == 0x95:
        dt, _ = decode_bcd_timestamp(value)
        if dt and len(dt) == 14:
            record['CALL_END_DATETIME'] = dt

    elif tag == 0x96:
        if is_moc:
            # answerTime — the actual moment the call was answered.
            # This is the correct START_DATETIME for MOC; duration is billed from this point.
            dt, offset = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['START_DATETIME'] = dt  # always overwrite — answerTime beats seizureTime
                record['UTC_TIME_OFFSET'] = offset
        else:
            dur = decode_duration(value)
            if dur < 100000:
                record['CALL_DURATION'] = str(dur)
    
    elif tag == 0x97:
        dt, offset = decode_bcd_timestamp(value)
        if dt and len(dt) == 14:
            record['START_DATETIME'] = dt
            record['UTC_TIME_OFFSET'] = offset
    
    elif tag == 0x98:
        if is_moc:
            dt, _ = decode_bcd_timestamp(value)
            if dt and len(dt) == 14:
                record['CALL_END_DATETIME'] = dt
    
    elif tag == 0x99:
        if is_moc:
            dur = decode_duration(value)
            if dur < 100000:
                record['CALL_DURATION'] = str(dur)
    
    elif tag == 0x8A:
        redir, _ = decode_address(value)
        if redir and len(redir) <= 20:
            record['REDIRECTING_NUMBER'] = redir

    elif tag == 0x9D:
        # callReferenceNumber is OCTET STRING — display as uppercase hex (standard telecom format)
        record['CALL_REF'] = value.hex().upper()

    elif tag == 0x9E:
        cause = decode_unsigned(value)
        record['RESULT_CODE'] = CAUSE_FOR_TERM.get(cause, str(cause))

    elif tag == 0x9C:
        # disconnectingParty — who released the call (Huawei extension)
        record['DISCONNECT_PARTY'] = str(decode_unsigned(value))

    elif tag == 0x9F:
        # callerPortedFlag BOOLEAN (some Huawei variants use 0x9F for this)
        record['CALLER_PORTED_FLAG'] = '1' if value and value[0] else '0'

    elif tag == 0xA0:
        # calledPortedFlag BOOLEAN (context-specific, class 0)
        if len(value) == 1:
            record['CALLED_PORTED_FLAG'] = '1' if value[0] else '0'

    elif tag == 0xA1:
        # locationRoutingNumber — NUMBER PORTABILITY routing number
        lrn, _ = decode_address(value)
        if lrn and len(lrn) <= 20:
            record['LOCATION_ROUTING_NUMBER'] = lrn

    elif tag == 0xA4:
        if is_gateway:
            route = decode_route_name(value)
            if route:
                record['ORIGINATING_TRUNK'] = route
    
    elif tag == 0xA5:
        if is_gateway:
            route = decode_route_name(value)
            if route:
                record['TERMINATING_TRUNK'] = route
    
    elif tag == 0xA7:
        route = decode_route_name(value)
        if route:
            record['ORIGINATING_TRUNK'] = route
    
    elif tag == 0xA8:
        route = decode_route_name(value)
        if route:
            record['TERMINATING_TRUNK'] = route
    
    elif tag == 0xA9:
        parse_location(record, value)
    
    elif tag == 0xAA:
        route = decode_route_name(value)
        if route:
            record['ORIGINATING_TRUNK'] = route
    
    elif tag == 0xAB:
        if len(value) >= 2 and value[0] == 0x81:
            route = decode_route_name(value)
            if route:
                record['TERMINATING_TRUNK'] = route
        else:
            ts, bs = decode_basic_service(value)
            if ts:
                record['TELESERVICE_CODE'] = ts
            if bs:
                record['BEARER_SERVICE_CODE'] = bs
    
    elif tag == 0xAC:
        if is_gateway:
            parse_diagnostics(record, value)
        else:
            parse_location(record, value)
    
    elif tag == 0xAE:
        ts, bs = decode_basic_service(value)
        if ts:
            record['TELESERVICE_CODE'] = ts
        if bs:
            record['BEARER_SERVICE_CODE'] = bs
    
    elif tag == 0xBC:
        parse_diagnostics(record, value)


def _process_common_standard_tag(record: Dict, tag: int, value: bytes, is_gateway: bool, is_moc: bool):
    """Process standard tags common to all record types (location, routes, diagnostics, etc.)"""

    if tag == 0xA7:
        # location (constructed) for SMS records
        parse_location(record, value)

    elif tag == 0xA9:
        parse_location(record, value)

    elif tag == 0xAC:
        if is_gateway:
            parse_diagnostics(record, value)
        else:
            parse_location(record, value)

    elif tag == 0x9D:
        # callReferenceNumber is OCTET STRING — display as uppercase hex
        record['CALL_REF'] = value.hex().upper()

    elif tag == 0x9E:
        cause = decode_unsigned(value)
        record['RESULT_CODE'] = CAUSE_FOR_TERM.get(cause, str(cause))

    elif tag == 0xBC:
        parse_diagnostics(record, value)


def process_extended_tag(record: Dict, tag: int, value: bytes):
    """Process 9F xx extended tags"""

    if tag == 0x1F:
        record['PARTIAL_RECORD_NO'] = str(decode_unsigned(value))

    elif tag == 0x25:
        # serviceKey (Huawei MSOFTX3000) — "The CAMEL service logic to be
        # applied. It will be present only if CAMEL is applied" — i.e. this
        # IS the authoritative prepaid signal for the CDR's subscriber.
        sk = decode_unsigned(value)
        if sk:
            record['SERVICE_KEY'] = str(sk)
            record['CAMEL_SERVICE_KEY'] = str(sk)

    elif tag == 0x2A:
        record['RAT_TYPE'] = str(decode_unsigned(value))

    elif tag == 0x49:
        # Fallback for 2-byte origination tag (0x9F 49)
        orig, _ = decode_address(value)
        if orig and len(orig) <= 20:
            record['CALLING_NO'] = orig

def process_extended_tag_81(record: Dict, tag: int, value: bytes, prefix: int = 0x01):
    """Process multi-byte 9F xx xx extended tags.

    ``prefix`` is ``ext1 & 0x7F`` — i.e. ``0x01`` for the ``0x9F 81 xx``
    family (the bulk of Huawei extended tags), or ``0x06`` for
    ``0x9F 86 05`` (``calledServiceKey``).
    """

    # calledServiceKey (Huawei MSOFTX3000, tag 0x9F 86 05) — NOT used for
    # prepaid detection.  It marks an IN service for the CALLED party
    # (e.g. VAS for the call recipient).  Stored separately so a postpaid
    # caller calling a prepaid callee doesn't get misclassified as prepaid.
    if prefix == 0x06 and tag == 0x05:
        csk = decode_unsigned(value)
        if csk:
            record['CALLED_SERVICE_KEY'] = str(csk)
        return

    if tag == 0x0C:
        if not record.get('ORIGINATING_TRUNK'):
            route = decode_ia5_string(value)
            if route and all(c.isprintable() for c in route):
                record['ORIGINATING_TRUNK'] = route
    
    elif tag == 0x0D:
        if not record.get('TERMINATING_TRUNK'):
            route = decode_ia5_string(value)
            if route and all(c.isprintable() for c in route):
                record['TERMINATING_TRUNK'] = route
    
    elif tag == 0x11:
        imsi = decode_tbcd(value)
        if imsi and len(imsi) <= 15 and imsi.isdigit() and not record['IMSI_A']:
            record['IMSI_A'] = imsi
    
    elif tag == 0x3C:
        mcc_mnc, lac, cell = decode_global_area_id(value)
        if lac:
            record['LAC_IDENTIFIER'] = lac
        if cell:
            record['CELL_ID_A'] = cell
        # Always override NETWORK_ENTITY with the serving cell's PLMN (MCC+MNC).
        # This is more authoritative than the IMSI prefix (home PLMN) set by tag 0x81.
        if mcc_mnc:
            record['NETWORK_ENTITY'] = mcc_mnc
        # 5-byte = LTE TAI (MCC/MNC 3 bytes + TAC 2 bytes); lac holds the TAC value
        if len(value) == 5:
            record['TAC'] = lac
    
    elif tag == 0x3E:
        record['RAT_TYPE'] = str(decode_unsigned(value))
    
    elif tag == 0x49:
        call_type = record.get('ORIGINAL_CALL_TYPE', '')
        if call_type == 'SMS-MT':
            # 0x9F 81 49 = origination — SMS sender MSISDN or alphanumeric ID (A-party)
            origination, _ = decode_address(value)
            if origination and len(origination) <= 20:
                record['CALLING_NO'] = origination
        elif call_type == 'MTC':
            # 0x9F 81 49 = redirectingNumber — the number CF diverted the call away from
            redir, _ = decode_address(value)
            if redir and len(redir) <= 20:
                record['REDIRECTING_NUMBER'] = redir
        elif call_type in ('SMS-MO', 'SMSMO_IW'):
            # 0x9F 81 49 = callReference for SMSMO (OCTET STRING)
            record['CALL_REF'] = value.hex().upper()
        else:
            # MOC / CF / GW / other voice: this is typically IMEI_B
            imei = decode_tbcd(value)
            if imei and len(imei) <= 16:
                record['IMEI_B'] = imei

    elif tag == 0x4A:
        # callReferenceNumber (extended) — OCTET STRING, display as uppercase hex
        record['CALL_REF'] = value.hex().upper()
    
    elif tag == 0x4B:
        dt, offset = decode_bcd_timestamp(value)
        if dt and len(dt) == 14 and not record['START_DATETIME']:
            record['START_DATETIME'] = dt
            record['UTC_TIME_OFFSET'] = offset
    
    elif tag == 0x4D:
        # calledIMSI is AddressString: byte 0 is NOA/NPI type, TBCD starts at byte 1
        raw_imsi = value[1:] if len(value) > 1 else value
        imsi = decode_tbcd(raw_imsi)
        if imsi and len(imsi) <= 15 and imsi.isdigit():
            record['IMSI_B'] = imsi
    
    elif tag == 0x60:
        ri = decode_unsigned(value)
        record['ROAMING_ICR_INDICATOR'] = str(ri) if ri else ''
    
    elif tag == 0x68:
        record['NETWORK_RECORD_ID'] = str(decode_unsigned(value))
    
    elif tag == 0x6D:
        record['PARTIAL_RECORD_NO'] = str(decode_unsigned(value))

    elif tag == 0x6F:
        # servedIMSI for GWIN records (0x9F 81 6F)
        call_type = record.get('ORIGINAL_CALL_TYPE', '')
        if call_type in ('GWIN', 'GWOUT'):
            imsi = decode_tbcd(value)
            if imsi and len(imsi) <= 15 and imsi.isdigit():
                if not record.get('CHARGED_PARTY_IMSI'):
                    record['CHARGED_PARTY_IMSI'] = imsi

def _extract_camel_fields(value: bytes):
    """
    Parse a cAMELSMSInformation or cAMELCallLegInformation constructed field.
    Returns (service_key: int, camel_phase: int) using only local variables —
    nothing is written to the record dict.
    """
    service_key = 0
    camel_phase = 0
    try:
        pos = 0
        while pos < len(value) - 1:
            tag = value[pos]
            if pos + 1 >= len(value):
                break
            length, consumed = parse_length(value, pos + 1)
            if length < 0:
                pos += 1
                continue
            val_start = pos + 1 + consumed
            if val_start + length > len(value):
                break
            inner = value[val_start:val_start + length]

            if tag == 0x80:   # serviceKey INTEGER
                service_key = decode_unsigned(inner)
            elif tag == 0x82: # cAMELPhase INTEGER
                camel_phase = decode_unsigned(inner)

            pos = val_start + length
    except Exception:
        pass
    return service_key, camel_phase


def parse_camel_sms_information(record: Dict, value: bytes):
    """Parse cAMELSMSInformation (tag 0xAC/0xAD) and update SUBSCRIBER_CATEGORY."""
    service_key, camel_phase = _extract_camel_fields(value)
    _apply_subscriber_category(record, service_key, camel_phase)
    if service_key:
        record['CAMEL_SERVICE_KEY'] = str(service_key)
    if camel_phase:
        record['CAMEL_PHASE'] = str(camel_phase)


def parse_camel_voice_information(record: Dict, value: bytes, leg_tag: int = 0x20):
    """Parse cAMELCallLegInformation BF tags for voice and update SUBSCRIBER_CATEGORY.

    The subscriber for every record type is on the **originating leg
    (BF20)**.  MTC is the exception: the called subscriber's CAMEL info
    isn't reliable for prepaid detection from an incoming-call CDR
    (per-operator policy), so MTC's prepaid classification is left blank
    by the processor — we still extract BF21's serviceKey here for
    traceability under ``CAMEL_LEG_1_*`` but never feed it into the
    prepaid rule.

    =================  ====================  ========================
    Call type          Subscriber leg        CAMEL signal
    =================  ====================  ========================
    MOC, EMERG         originating (BF20)    BF20 only
    CallForwarding     originating (BF20)    BF20 only (forwarding subscriber)
    GWIN / GWOUT       originating (BF20)    BF20 (transit leg)
    MTC                — (not classified)    BF21 captured for traceability
    SMSMO, SMSMT       n/a here              uses cAMELSMSInformation (0xAC/0xAD)
    =================  ====================  ========================
    """
    service_key, camel_phase = _extract_camel_fields(value)
    _apply_subscriber_category(record, service_key, camel_phase)

    # All non-MTC records: subscriber on originating leg (BF20).
    # MTC: deliberately not classified — its BF21 goes into CAMEL_LEG_1_*.
    subscriber_leg = 0x20

    if leg_tag == subscriber_leg:
        if service_key:
            record['SERVICE_KEY'] = str(service_key)
            record['CAMEL_SERVICE_KEY'] = str(service_key)
        if camel_phase:
            record['CAMEL_PHASE'] = str(camel_phase)
    else:
        # Non-subscriber leg — kept for traceability, not used for prepaid.
        n = leg_tag - 0x20  # 0 for BF20, 1 for BF21, …
        if service_key:
            record[f'CAMEL_LEG_{n}_SERVICE_KEY'] = str(service_key)
        if camel_phase:
            record[f'CAMEL_LEG_{n}_PHASE'] = str(camel_phase)


# OSL_PLMNI = Orange Sierra Leone PLMN ID (MCC 619 + MNC 01).
OSL_PLMNI = '61901'


def _apply_prepaid_flag(record: Dict):
    """Derive PREPAID_FLAG from CAMEL IN-trigger fields.

    Rule:
      * SERVICE_KEY (or CAMEL_SERVICE_KEY) OR CAMEL_PHASE has a value → prepaid
      * both blank                                                    → postpaid

    Writes ``'0'`` (postpaid) or ``'1'`` (prepaid) for CSV-export back-compat;
    the processor pipeline derives the canonical ``'PREPAID'`` / ``'POSTPAID'``
    string via the same :func:`core.utils.prepaid.derive_msc_prepaid_flag`.
    """
    sk = (record.get('SERVICE_KEY')
          or record.get('CAMEL_SERVICE_KEY')
          or record.get('SERVICEKEY'))  # post-BIG_DATA spelling, no underscore
    cp = record.get('CAMEL_PHASE') or record.get('CAMELPHASE')
    imsi = record.get('IMSI') or record.get('CHARGED_PARTY_IMSI')
    flag = derive_msc_prepaid_flag(sk, cp, imsi=imsi)
    record['PREPAID_FLAG'] = '1' if flag == 'PREPAID' else '0'


def _apply_subscriber_category(record: Dict, service_key: int = 0, camel_phase: int = 0):
    """
    Derive SUBSCRIBER_CATEGORY using CAMEL serviceKey/camelPhase per the CDR spec.

    Rules (per CloudMSOFTX3000 V500R012C35 spec table):
      • MOC / CF / CFW / EMERG (originating voice):
          home IMSI (OSL_PLMNI) + serviceKey=0 + camelPhase=0 → '0' (postpaid)
          home IMSI + serviceKey > 0 or camelPhase > 0         → '1' (prepaid)
      • SMSMO (originating SMS):
          home IMSI + cAMELSMSInformation.serviceKey=0/absent  → '0' (postpaid)
          home IMSI + serviceKey > 0                           → '1' (prepaid)
      • MTC / SMSMT / GWI / GWO / RCF (empty rule in spec)   → '2' (unknown)
      • Roaming IMSI (non-OSL_PLMNI):
          CAMEL present → '1' (prepaid)
          no CAMEL      → '2' (unknown)
    """
    imsi = record.get('CHARGED_PARTY_IMSI', '') or ''
    call_type = record.get('ORIGINAL_CALL_TYPE', '') or ''
    is_home = imsi.startswith(home_identity()[0])  # active operator's home PLMN

    ORIGINATING_TYPES = {'MOC', 'EMERG', 'CallForwarding', 'SMS-MO'}
    has_camel = service_key > 0 or camel_phase > 0

    if not is_home:
        record['SUBSCRIBER_CATEGORY'] = '1' if has_camel else '2'
        return

    if has_camel:
        record['SUBSCRIBER_CATEGORY'] = '1'
    elif call_type in ORIGINATING_TYPES:
        record['SUBSCRIBER_CATEGORY'] = '0'
    else:
        # MTC, SMSMT, GWIN, GWOUT, RCF — empty rule, cannot determine
        record['SUBSCRIBER_CATEGORY'] = '2'


def process_bf_tag(record: Dict, tag: int, value: bytes):
    """Process BF xx constructed tags"""

    if tag == 0x1F:
        parse_diagnostics(record, value)

    # BF20 / BF21 / BF22 / BF24 etc. — cAMELCallLegInformation for voice.
    # BF20 is the originating leg (calling party's CAMEL info → CDR subject);
    # BF21+ are terminating / forwarding legs (other party's CAMEL info).
    elif 0x20 <= tag <= 0x2F:
        parse_camel_voice_information(record, value, leg_tag=tag)

def parse_location(record: Dict, value: bytes):
    """Parse location constructed field"""
    try:
        pos = 0
        while pos < len(value) - 2:
            tag = value[pos]
            
            if tag == 0x80:
                length, consumed = parse_length(value, pos + 1)
                if length > 0:
                    lac = decode_unsigned(value[pos + 1 + consumed:pos + 1 + consumed + length])
                    record['LAC_IDENTIFIER'] = str(lac)
                    pos += 1 + consumed + length
                    continue
            
            elif tag == 0x81:
                length, consumed = parse_length(value, pos + 1)
                if length > 0:
                    cell = decode_unsigned(value[pos + 1 + consumed:pos + 1 + consumed + length])
                    record['CELL_ID_A'] = str(cell)
                    pos += 1 + consumed + length
                    continue
            
            pos += 1
    except:
        pass

def parse_diagnostics(record: Dict, value: bytes):
    """Parse diagnostics field — extracts cause code and sets both RESULT_CODE and DIAG_FOR_TERM."""
    # Priority per BIG_DATA spec:
    # gsm0408Cause(0x80) > gsm0902MapErrorValue(0x81) > ccittQ767Cause(0x82) >
    # networkSpecificCause(0x83) > manufacturerSpecificCause(0x84)
    _diag_tag_names = {
        0x80: 'gsm0408Cause',
        0x81: 'gsm0902MapErrorValue',
        0x82: 'ccittQ767Cause',
        0x83: 'networkSpecificCause',
        0x84: 'manufacturerSpecificCause',
    }
    try:
        if len(value) >= 2:
            pos = 0
            while pos < len(value) - 1:
                tag = value[pos]
                length, consumed = parse_length(value, pos + 1)
                if length < 0:
                    pos += 1
                    continue
                inner = value[pos + 1 + consumed: pos + 1 + consumed + length]
                if tag in _diag_tag_names:
                    cause = decode_unsigned(inner)
                    if not record.get('RESULT_CODE'):
                        record['RESULT_CODE'] = str(cause)
                    if not record.get('DIAG_FOR_TERM'):
                        record['DIAG_FOR_TERM'] = f"{_diag_tag_names[tag]}:{cause}"
                    return  # highest-priority cause found
                pos += 1 + consumed + length
    except Exception:
        pass

# =============================================================================
# FILE PROCESSING
# =============================================================================

def parse_length(data: bytes, pos: int) -> Tuple[int, int]:
    """Parse ASN.1 length at position, returns (length, bytes_consumed)"""
    if pos >= len(data):
        return -1, 0
    
    first_len = data[pos]
    
    if first_len < 0x80:
        return first_len, 1
    elif first_len == 0x81:
        if pos + 1 >= len(data):
            return -1, 0
        return data[pos + 1], 2
    elif first_len == 0x82:
        if pos + 2 >= len(data):
            return -1, 0
        return (data[pos + 1] << 8) | data[pos + 2], 3
    elif first_len == 0x83:
        if pos + 3 >= len(data):
            return -1, 0
        return (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3], 4
    elif first_len == 0x84:
        if pos + 4 >= len(data):
            return -1, 0
        return (data[pos + 1] << 24) | (data[pos + 2] << 16) | (data[pos + 3] << 8) | data[pos + 4], 5
    
    return -1, 0

def find_cdr_records(data: bytes) -> List[Tuple[int, bytes]]:
    """Find all CDR records in file"""
    records = []
    pos = 0
    
    if pos < len(data) and data[pos] == 0x30:
        length, consumed = parse_length(data, pos + 1)
        if length > 0:
            pos += 1 + consumed
    
    container_data = None
    
    while pos < len(data) - 2:
        tag = data[pos]
        
        if tag == 0xA0:
            length, consumed = parse_length(data, pos + 1)
            if length >= 0:
                pos += 1 + consumed + length
                continue
        
        elif tag == 0xA1:
            length, consumed = parse_length(data, pos + 1)
            if length > 0:
                content_start = pos + 1 + consumed
                container_data = data[content_start:content_start + length]
                pos = content_start + length
                break
        
        elif tag == 0xA2:
            length, consumed = parse_length(data, pos + 1)
            if length >= 0:
                pos += 1 + consumed + length
                continue
        
        pos += 1
    
    if container_data:
        records = parse_records_from_container(container_data)
    else:
        records = scan_for_records(data)
    
    return records

def parse_records_from_container(data: bytes) -> List[Tuple[int, bytes]]:
    """Parse individual CDR records from container"""
    records = []
    pos = 0
    
    while pos < len(data) - 2:
        tag = data[pos]
        
        if 0xA0 <= tag <= 0xBF:
            length, consumed = parse_length(data, pos + 1)
            
            if length > 0 and length < 10000:
                content_start = pos + 1 + consumed
                if content_start + length <= len(data):
                    records.append((tag, data[content_start:content_start + length]))
                    pos = content_start + length
                    continue
        
        pos += 1
    
    return records

def scan_for_records(data: bytes) -> List[Tuple[int, bytes]]:
    """Scan file for records (fallback method)"""
    records = []
    pos = 0
    
    while pos < len(data) - 5:
        tag = data[pos]
        
        if 0xA0 <= tag <= 0xBF:
            length, consumed = parse_length(data, pos + 1)
            
            if 50 < length < 5000:
                content_start = pos + 1 + consumed
                if content_start + length <= len(data):
                    if content_start + 3 < len(data) and data[content_start] == 0x80:
                        records.append((tag, data[content_start:content_start + length]))
                        pos = content_start + length
                        continue
        
        pos += 1
    
    return records

def process_file(input_file: str, output_file: str = None, output_format: str = 'csv', verbose: bool = False):
    """Process MSC CDR file and return BIG_DATA output records."""
    filename = os.path.basename(input_file)

    with open(input_file, 'rb') as f:
        data = f.read()

    records = find_cdr_records(data)

    if not records:
        return []

    output_records = []
    stats = defaultdict(int)

    for record_num, (record_tag, record_data) in enumerate(records, start=1):
        internal = decode_msc_record(record_data, filename, record_tag)

        rt_info = RECORD_TAGS.get(record_tag, ('unknown', 'O', 'UNKNOWN', 'U', 'UNKNOWN'))
        if not internal.get('ORIGINAL_CALL_TYPE') or internal['ORIGINAL_CALL_TYPE'] in ('', 'UNKNOWN'):
            internal['ORIGINAL_CALL_TYPE'] = rt_info[2]

        if internal.get('ORIGINAL_CALL_TYPE') in ('ROAMING', 'EMERG', 'TRANSIT'):
            continue

        internal['_RECORD_TAG'] = record_tag
        bd = _build_bigdata_record(internal, filename, record_num)
        output_records.append(bd)
        stats[rt_info[0]] += 1

    if output_file is None:
        output_file = f"{os.path.splitext(input_file)[0]}_decoded.{output_format}"

    if output_format == 'json':
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_records, f, indent=2)
    else:
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=BIG_DATA_OUTPUT_FIELDS, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(output_records)

    return output_records

# =============================================================================
# UMP INTEGRATION FUNCTIONS
# =============================================================================

def decode_cdr_file_to_records(input_path: str) -> Tuple[bool, list, int]:
    """Decode a binary CDR file directly to in-memory dicts.

    Skips the CSV write/read round-trip for better performance.

    Returns:
        tuple: (success, list_of_record_dicts or error_message, record_count)
    """
    if not os.path.exists(input_path):
        return False, f"File not found: {input_path}", 0

    try:
        filename = os.path.basename(input_path)

        with open(input_path, 'rb') as f:
            data = f.read()

        records = find_cdr_records(data)
        if not records:
            return False, "No records decoded from file", 0

        output_records = []
        for record_num, (record_tag, record_data) in enumerate(records, start=1):
            internal = decode_msc_record(record_data, filename, record_tag)

            rt_info = RECORD_TAGS.get(record_tag, ('unknown', 'O', 'UNKNOWN', 'U', 'UNKNOWN'))
            if not internal.get('ORIGINAL_CALL_TYPE') or internal['ORIGINAL_CALL_TYPE'] in ('', 'UNKNOWN'):
                internal['ORIGINAL_CALL_TYPE'] = rt_info[2]

            if internal.get('ORIGINAL_CALL_TYPE') in ('ROAMING', 'EMERG', 'TRANSIT'):
                continue

            internal['_RECORD_TAG'] = record_tag
            bd = _build_bigdata_record(internal, filename, record_num)
            output_records.append(bd)

        return True, output_records, len(output_records)

    except Exception as e:
        return False, str(e), 0


def decode_cdr_file(input_path: str, output_path: str = None) -> Tuple[bool, str, int]:
    """
    Decode a binary CDR file to CSV - for UMP integration
    
    Returns:
        tuple: (success, output_path or error message, record_count)
    """
    if not os.path.exists(input_path):
        return False, f"File not found: {input_path}", 0
    
    try:
        decoded = process_file(input_path, output_path, 'csv', False)
        
        if not decoded:
            return False, "No records decoded from file", 0
        
        if output_path is None:
            output_path = f"{os.path.splitext(input_path)[0]}_decoded.csv"
        
        return True, output_path, len(decoded)
        
    except Exception as e:
        return False, str(e), 0

def is_binary_cdr(filepath: str) -> bool:
    """
    Check if a file is a binary CDR file (vs already decoded CSV)
    """
    try:
        with open(filepath, 'rb') as f:
            header = f.read(512)
        
        # Check for CSV indicators (both legacy and BIG_DATA headers)
        try:
            text = header.decode('utf-8')
            if any(field in text.upper() for field in [
                'PREPAID_FLAG', 'CALLING_NO', 'CHARGED_PARTY', 'ORIGINAL_CALL_TYPE',
                'ACCESS_METHOD_IDENTIFIER', 'CALLING_PARTY_NUMBER', 'CALL_TYPE',
                'CALL_START_DATE', 'CHARGEABLE_DURATION',
            ]):
                return False
            if 'record_type' in text.lower() or 'calling_number' in text.lower():
                return False
        except:
            pass
        
        # Check for ASN.1/binary indicators
        if header and (header[0] == 0x30 or 0xA0 <= header[0] <= 0xBF):
            return True
        
        # Check for high number of non-printable characters
        non_printable = sum(1 for b in header if b < 32 and b not in (9, 10, 13))
        if non_printable > len(header) * 0.3:
            return True
        
        return False
        
    except:
        return False

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Huawei MSC CDR Decoder (CloudMSoftX3000)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python huawei_decoder.py bFTMSC*.dat
  python huawei_decoder.py input.dat -o output.csv
  python huawei_decoder.py input.dat -f json -v
        """
    )
    
    parser.add_argument('input_file', help='Input CDR file')
    parser.add_argument('-o', '--output', help='Output file path')
    parser.add_argument('-f', '--format', choices=['csv', 'json'], default='csv', help='Output format')
    parser.add_argument('-v', '--verbose', action='store_true', help='Show sample records')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input_file):
        print(f"Error: File not found: {args.input_file}")
        sys.exit(1)
    
    process_file(args.input_file, args.output, args.format, args.verbose)

if __name__ == '__main__':
    main()
