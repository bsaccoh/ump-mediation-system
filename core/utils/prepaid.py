"""Helpers for normalizing the prepaid/postpaid flag across streams."""


def normalize_prepaid_flag(value) -> str:
    """Convert decoder output ('0'/'1' or 'PREPAID'/'POSTPAID') to a canonical
    string used for filtering: 'PREPAID', 'POSTPAID', or '' (unknown)."""
    if value in ('0', 0):
        return 'POSTPAID'
    if value in ('1', 1):
        return 'PREPAID'
    if value in ('PREPAID', 'POSTPAID'):
        return value
    return ''


# 3GPP TS 32.298 §4.5 chargingCharacteristics — bit layout for **octet 1**:
#
#   bit 8 7 6 5   4 3 2 1
#       Spare'0000' N P F H
#
# Octet 2 is reserved.  Octet 1 alone carries the four profile flags:
#   bit 1 (mask 0x01)  H — Hot billing
#   bit 2 (mask 0x02)  F — Flat rate
#   bit 3 (mask 0x04)  P — Prepaid              ← prepaid signal
#   bit 4 (mask 0x08)  N — Normal (postpaid)
#
# When the value is stored as a 2-byte big-endian hex string (the common
# 3GPP encoding, e.g. '0400'), octet 1 is the HIGH byte, so the P bit
# lives at mask 0x0400 inside the uint16.  When stored as a single byte
# (e.g. '04'), the P bit lives at mask 0x04.  We accept both.
CC_PREPAID_MASK_BYTE  = 0x04   # P flag in octet 1 alone
CC_PREPAID_MASK_WORD  = 0x0400 # P flag in 2-octet big-endian uint16
CC_SINGLE_BYTE_MAX    = 0xFF   # values ≤ 0xFF are treated as single-byte


def derive_prepaid_from_cc(charging_characteristics) -> str:
    """Derive prepaid/postpaid from 3GPP ``chargingCharacteristics`` (CC).

    Applies to PGW, SGSN, SGW (and any other data-CDR stream that carries
    the standard CC field).  Returns:

      * ``'PREPAID'`` if the **P** flag (bit 3 of octet 1) is set.
      * ``'POSTPAID'`` otherwise (blank, unparseable, P-bit clear).

    Accepted input formats:
      * single byte hex string: ``'04'``, ``'4'``, ``'0x04'``
      * two-byte hex string:    ``'0400'``, ``'0x0400'``, ``'400'``
      * native int (some decoders emit ints, e.g. ``0x0400``)
      * empty / None / non-hex → POSTPAID
    """
    # Native int
    if isinstance(charging_characteristics, int):
        cc_int = charging_characteristics
    else:
        cc = (str(charging_characteristics or '')).strip().lower()
        if not cc:
            return 'POSTPAID'
        if cc.startswith('0x'):
            cc = cc[2:]
        try:
            cc_int = int(cc, 16)
        except ValueError:
            return 'POSTPAID'

    # Auto-detect 1-byte vs 2-byte encoding by magnitude.
    mask = CC_PREPAID_MASK_BYTE if cc_int <= CC_SINGLE_BYTE_MAX else CC_PREPAID_MASK_WORD
    return 'PREPAID' if (cc_int & mask) else 'POSTPAID'


# Back-compat alias — earlier code imported the PGW-specific name.
derive_pgw_prepaid_flag = derive_prepaid_from_cc


def derive_msc_prepaid_flag(service_key, camel_phase, imsi=None) -> str:
    """Derive prepaid/postpaid for MSC CDRs from CAMEL IN-trigger fields.

    Rule:
      * EITHER ``service_key`` or ``camel_phase`` has a value
        → **PREPAID** (CAMEL IN trigger fired during the call).
      * BOTH blank
        → **POSTPAID** (no IN dipping happened).

    Values that count as blank:
      * ``None`` / empty string / whitespace-only string
      * literal ``'0'`` / integer ``0`` — upstream emits ``'0'`` when the
        binary tag was absent, not as a real "phase zero" trigger.

    ``imsi`` is accepted but no longer used.  An earlier rule treated the
    IMSI block ``6190176100*`` as authoritative postpaid; that turned out
    to be unreliable (some prepaid subscribers also live in that block),
    so we now rely solely on the CAMEL signal.  The parameter stays in
    the signature for API back-compat — callers can keep passing IMSI.
    """
    def _has_value(v):
        if v in (None, '', 0, '0'):
            return False
        return bool(str(v).strip())
    return 'PREPAID' if (_has_value(service_key) or _has_value(camel_phase)) else 'POSTPAID'
