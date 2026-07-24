"""
CBS CDR Processor
==================
Pass-through processor for Huawei CBS (Charging and Billing System) CDR files.

CBS files are already human-readable pipe-delimited (|) plain text with a
header row — no binary decoding required.

Supported sub-types and their actual filename patterns (from CBS-SW V500R023C00LG9031 spec):
  VOICE      → cbs_cdr_voice_*.add
  SMS        → cbs_cdr_sms_*.add
  DATA       → cbs_cdr_data_*.add
  RECHARGE   → cbs_cdr_vou_*.add          (voucher recharge)
  ADJUSTMENT → cbs_cdr_adj_*.add
  LOAN       → cbs_cdr_loan_*.add
  MON        → cbs_cdr_mon_*.add          (recurring charging / MONthly)
  CM         → cbs_cdr_cm_*.add           (management flow)

Key field names differ per sub-type.  The processor resolves promoted fields
(subscriber_id, msisdn, charge_amount, event_time) by header name, not
by position — because Voice/SMS/Data have 351 common fields before their
unique extension, while Recharge/Adjustment/Loan have completely different layouts.
"""
import csv
import io
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Tuple, List

from core.base_processor import BaseProcessor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filename substring → CBS sub-type  (checked in order; first match wins)
# Patterns derived from actual file name examples in CBS-SW V500R023C00LG9031.
# ---------------------------------------------------------------------------
CBS_TYPE_PATTERNS = [
    ('cbs_cdr_voice', 'VOICE'),
    ('cbs_cdr_sms',   'SMS'),
    ('cbs_cdr_data',  'DATA'),
    ('cbs_cdr_vou',   'RECHARGE'),    # voucher / recharge
    ('cbs_cdr_adj',   'ADJUSTMENT'),
    ('cbs_cdr_loan',  'LOAN'),
    ('cbs_cdr_mon',   'MON'),         # recurring / monthly charging
    ('cbs_cdr_cm',    'CM'),          # management flow
]

# ---------------------------------------------------------------------------
# Per-type field name → promoted column mapping
# Each entry is a list of candidate header names tried in order; first hit wins.
# Field names sourced from CBS-SW V500R023C00LG9031 CDR Reference:
#   - Common fields (4.2.1): CDR_ID, BEGIN_TIME, TOTAL_CHARGE, etc.
#   - Voice/SMS/Data extended (4.2.2): CallingPartyNumber, CallingPartyIMSI
#   - Recharge (4.3.1):    RECHARGE_LOG_ID, ACCT_ID, PRI_IDENTITY, RECHARGE_AMT
#   - Adjustment (4.3.3):  ADJUST_LOG_ID, ACCT_ID, PRI_IDENTITY, ADJUST_AMT
#   - Loan (4.3.4):        SUB_ID, PRI_IDENTITY, OPER_DATE, LOAN_AMT
# ---------------------------------------------------------------------------
CBS_FIELD_MAP = {
    # ---- subscriber / account identifier (the primary account key) ----------
    'subscriber_id': {
        'VOICE':      ['CDR_ID', 'DEFAULT_ACCT_ID', 'ACCT_ID'],
        'SMS':        ['CDR_ID', 'DEFAULT_ACCT_ID', 'ACCT_ID'],
        'DATA':       ['CDR_ID', 'DEFAULT_ACCT_ID', 'ACCT_ID'],
        'RECHARGE':   ['ACCT_ID', 'SUB_ID', 'RECHARGE_LOG_ID'],
        'ADJUSTMENT': ['ACCT_ID', 'SUB_ID', 'ADJUST_LOG_ID'],
        'LOAN':       ['SUB_ID'],
        'MON':        ['CDR_ID', 'DEFAULT_ACCT_ID', 'ACCT_ID'],
        'CM':         ['CDR_ID', 'DEFAULT_ACCT_ID', 'ACCT_ID'],
        'UNKNOWN':    ['ACCT_ID', 'CDR_ID', 'SUB_ID'],
    },
    # ---- MSISDN / primary identity ------------------------------------------
    'msisdn': {
        'VOICE':      ['CallingPartyNumber', 'PRI_IDENTITY', 'DEFAULT_SERV_NUM'],
        'SMS':        ['CallingPartyNumber', 'PRI_IDENTITY', 'DEFAULT_SERV_NUM'],
        'DATA':       ['CallingPartyNumber', 'PRI_IDENTITY', 'DEFAULT_SERV_NUM'],
        'RECHARGE':   ['PRI_IDENTITY', 'DEFAULT_SERV_NUM'],
        'ADJUSTMENT': ['PRI_IDENTITY', 'DEFAULT_SERV_NUM'],
        'LOAN':       ['PRI_IDENTITY', 'DEFAULT_SERV_NUM'],
        'MON':        ['CallingPartyNumber', 'PRI_IDENTITY', 'DEFAULT_SERV_NUM'],
        'CM':         ['CallingPartyNumber', 'PRI_IDENTITY', 'DEFAULT_SERV_NUM'],
        'UNKNOWN':    ['PRI_IDENTITY', 'CallingPartyNumber', 'DEFAULT_SERV_NUM'],
    },
    # ---- charge / amount deducted -------------------------------------------
    'charge_amount': {
        'VOICE':      ['TOTAL_CHARGE', 'CHARGE_FEE', 'REAL_CHARGE'],
        'SMS':        ['TOTAL_CHARGE', 'CHARGE_FEE', 'REAL_CHARGE'],
        'DATA':       ['TOTAL_CHARGE', 'CHARGE_FEE', 'REAL_CHARGE'],
        'RECHARGE':   ['RECHARGE_AMT', 'ACTUAL_RECHARGE_AMT'],
        'ADJUSTMENT': ['ADJUST_AMT', 'ACTUAL_ADJUST_AMT'],
        'LOAN':       ['LOAN_AMT', 'ACTUAL_LOAN_AMT'],
        'MON':        ['TOTAL_CHARGE', 'CHARGE_FEE', 'REAL_CHARGE'],
        'CM':         ['TOTAL_CHARGE', 'CHARGE_FEE'],
        'UNKNOWN':    ['TOTAL_CHARGE', 'CHARGE_FEE', 'RECHARGE_AMT', 'ADJUST_AMT'],
    },
    # ---- event / transaction timestamp --------------------------------------
    'event_time': {
        'VOICE':      ['BEGIN_TIME', 'START_TIME', 'OPER_TIME'],
        'SMS':        ['BEGIN_TIME', 'START_TIME', 'OPER_TIME'],
        'DATA':       ['BEGIN_TIME', 'START_TIME', 'OPER_TIME'],
        'RECHARGE':   ['BEGIN_TIME', 'OPER_TIME', 'RECHARGE_TIME'],
        'ADJUSTMENT': ['OPER_TIME', 'BEGIN_TIME'],
        'LOAN':       ['OPER_DATE', 'BEGIN_TIME', 'OPER_TIME'],
        'MON':        ['BEGIN_TIME', 'OPER_TIME'],
        'CM':         ['BEGIN_TIME', 'OPER_TIME'],
        'UNKNOWN':    ['BEGIN_TIME', 'OPER_TIME', 'OPER_DATE', 'START_TIME'],
    },
}


def _detect_cbs_type(filename: str, source=None) -> str:
    """Detect CBS sub-type from filename, DataSource hint, or return UNKNOWN."""
    fname = filename.lower()
    for pattern, cbs_type in CBS_TYPE_PATTERNS:
        if pattern in fname:
            return cbs_type
    if source and getattr(source, 'cbs_type_hint', '').strip():
        return source.cbs_type_hint.strip().upper()
    return 'UNKNOWN'


def _resolve_field(raw: dict, cbs_type: str, purpose: str) -> str:
    """Look up a promoted field value by trying candidate header names in order."""
    candidates = CBS_FIELD_MAP.get(purpose, {}).get(cbs_type, [])
    # Also try UNKNOWN fallbacks if not found in specific type
    fallbacks = CBS_FIELD_MAP.get(purpose, {}).get('UNKNOWN', [])
    for name in candidates + [f for f in fallbacks if f not in candidates]:
        val = raw.get(name)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ''


def _parse_event_time(value: str):
    """Parse CBS timestamps (YYYYMMDDHHmmss, YYYYMMDDHHmm, YYYYMMDD) → datetime or None."""
    if not value or not value.strip():
        return None
    v = value.strip()
    for fmt in ('%Y%m%d%H%M%S', '%Y%m%d%H%M', '%Y%m%d', '%y%m%d%H%M%S'):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def _parse_charge(value: str):
    """Parse charge amount to Decimal or None."""
    if not value or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


class CBSProcessor(BaseProcessor):
    """
    Pass-through processor for Huawei CBS CDR files.

    Pipeline:
      decode()        → count rows, return original path unchanged
      parse_records() → pipe-delimited DictReader, header-keyed dicts
      create_record() → resolve key fields by header name per cbs_type
      validate_record() → pass-through (no rules for CBS)
      enrich_record()   → no-op
    """

    # -------------------------------------------------------------------------
    # decode — no-op pass-through
    # -------------------------------------------------------------------------

    def decode(self, file_path: str) -> Tuple[bool, str, int]:
        """CBS files are plain text — return unchanged, count data rows."""
        try:
            count = 0
            with open(file_path, encoding='utf-8', errors='replace') as f:
                header_skipped = False
                for line in f:
                    if not line.strip():
                        continue
                    if not header_skipped:
                        header_skipped = True   # first non-empty line = header
                        continue
                    count += 1
            return True, file_path, count
        except Exception as e:
            logger.error(f'CBSProcessor.decode error: {e}')
            return False, str(e), 0

    # -------------------------------------------------------------------------
    # parse_records — pipe-delimited DictReader
    # -------------------------------------------------------------------------

    def parse_records(self, file_path: str):
        """Yield one dict per data row, keyed by the file's own header row.

        If the file has no recognisable text header (all header tokens are
        numeric), positional keys 'field_0', 'field_1', ... are used instead.
        """
        try:
            with open(file_path, encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            logger.error(f'CBSProcessor.parse_records cannot open {file_path}: {e}')
            return

        lines = [ln for ln in content.splitlines() if ln.strip()]
        if not lines:
            return

        buf = io.StringIO('\n'.join(lines))
        reader = csv.DictReader(buf, delimiter='|')

        # Guard: if DictReader's fieldnames all look numeric, assume no header
        try:
            fieldnames = reader.fieldnames or []
            all_numeric = bool(fieldnames) and all(
                (fn or '').strip().lstrip('-').isdigit() for fn in fieldnames
            )
            if all_numeric:
                # Positional fallback: regenerate without header consumption
                first_cols = lines[0].split('|')
                keys = [f'field_{i}' for i in range(len(first_cols))]
                buf2 = io.StringIO('\n'.join(lines))
                reader = csv.DictReader(buf2, fieldnames=keys, delimiter='|')
        except Exception:
            pass

        for row in reader:
            if row:
                yield dict(row)

    # -------------------------------------------------------------------------
    # create_record
    # -------------------------------------------------------------------------

    def create_record(self, raw: dict, cdr_file):
        """Map parsed CBS row → CBSRecord, resolving fields by header name."""
        from streams.cbs.models import CBSRecord

        source = getattr(cdr_file, 'source', None)
        cbs_type = _detect_cbs_type(cdr_file.filename, source)

        subscriber_id = _resolve_field(raw, cbs_type, 'subscriber_id')
        msisdn        = _resolve_field(raw, cbs_type, 'msisdn')
        charge_raw    = _resolve_field(raw, cbs_type, 'charge_amount')
        event_raw     = _resolve_field(raw, cbs_type, 'event_time')

        return CBSRecord(
            file=cdr_file,
            source=source,
            cbs_type=cbs_type,
            subscriber_id=subscriber_id[:100],
            msisdn=msisdn[:20],
            charge_amount=_parse_charge(charge_raw),
            event_time=_parse_event_time(event_raw),
            raw_data=raw,
            status=CBSRecord.Status.VALID,
        )

    # -------------------------------------------------------------------------
    # validate / enrich — pass-through for CBS
    # -------------------------------------------------------------------------

    def validate_record(self, record, raw: dict) -> List[str]:
        """CBS is collect-and-forward — no validation rules applied."""
        return []

    def enrich_record(self, record, raw: dict) -> None:
        """No enrichment needed for CBS pass-through."""
        pass
