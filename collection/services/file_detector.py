"""
File Detector
==============
Auto-detect decoder type from filename patterns and extensions.
"""
from core.enums import DecoderType

# Filename patterns (checked FIRST - more specific than extensions)
FILENAME_PATTERNS = {
    # IMS / ATS9900 patterns — check before generic 'pgw' / 'sgw' to avoid misclassification.
    # Real Huawei ATS9900 filenames start with bFTATS01 / FTATS01 (FT = Frontier Technologies).
    'ftats':   DecoderType.IMS,   # bFTATS01..., FTATS02..., etc.
    'ats9900': DecoderType.IMS,
    'ats01':   DecoderType.IMS,
    'ats02':   DecoderType.IMS,
    'ims_cdr': DecoderType.IMS,
    'imscdr':  DecoderType.IMS,
    'volte':   DecoderType.IMS,
    'vobb':    DecoderType.IMS,
    'fmc_cdr': DecoderType.IMS,
    # PS-domain
    'pgw': DecoderType.PGW,
    'ggsn': DecoderType.PGW,
    'sgw': DecoderType.SGW,
    'sgsn': DecoderType.SGSN,
    'w_abr': DecoderType.OCS,
    'w_smo': DecoderType.OCS,
    'w_smr': DecoderType.OCS,
    'w_rec': DecoderType.OCS,
    'gprs_': DecoderType.OCS,
    # CBS sub-type patterns — all resolve to CBS decoder type;
    # the processor further discriminates via CBS_TYPE_PATTERNS.
    # Patterns match actual Huawei CBS-SW V500R023C00LG9031 filenames, e.g.:
    #   cbs_cdr_voice_*.add  cbs_cdr_vou_*.add  cbs_cdr_adj_*.add
    # Order: longest/most-specific first so 'cbs_cdr_voice' beats 'cbs_cdr'.
    'cbs_cdr_voice': DecoderType.CBS,   # Voice CDRs
    'cbs_cdr_sms':   DecoderType.CBS,   # SMS CDRs
    'cbs_cdr_data':  DecoderType.CBS,   # Data CDRs
    'cbs_cdr_vou':   DecoderType.CBS,   # Recharge / voucher CDRs
    'cbs_cdr_adj':   DecoderType.CBS,   # Adjustment CDRs
    'cbs_cdr_loan':  DecoderType.CBS,   # Loan CDRs
    'cbs_cdr_mon':   DecoderType.CBS,   # Recurring / monthly CDRs
    'cbs_cdr_cm':    DecoderType.CBS,   # Management-flow CDRs
    'cbs_cdr':       DecoderType.CBS,   # Generic CBS CDR fallback
}

# Extension mapping (fallback)
EXTENSION_MAP = {
    '.dat': DecoderType.MSC,
    '.bin': DecoderType.MSC,
    '.asn': DecoderType.MSC,
    '.ber': DecoderType.MSC,
    '.unl': DecoderType.OCS,
    '.add': DecoderType.CBS,
    '.csv': DecoderType.CSV,
    '.txt': DecoderType.CSV,
}


class FileClassification:
    """Result of classifying an incoming file (operator/vendor/NE/decoder)."""

    __slots__ = ('operator', 'vendor', 'network_element', 'decoder_type', 'matched_pattern')

    def __init__(self, operator=None, vendor=None, network_element=None,
                 decoder_type=None, matched_pattern=None):
        self.operator = operator                  # Operator code (slug) or None
        self.vendor = vendor                       # vendor slug or None
        self.network_element = network_element     # 'msc', 'pgw', ... or None
        self.decoder_type = decoder_type           # DecoderType value
        self.matched_pattern = matched_pattern     # SourcePattern that matched, or None

    def __repr__(self):
        return (f'FileClassification(operator={self.operator!r}, vendor={self.vendor!r}, '
                f'network_element={self.network_element!r}, decoder_type={self.decoder_type!r})')


def classify_file(filename: str) -> 'FileClassification':
    """Resolve (operator, vendor, network_element, decoder_type) from a filename.

    Driven by reference.SourcePattern rows (priority-ordered, first match wins).
    Falls back to extension/filename decoder detection with operator/vendor/NE
    left None when nothing matches. Tolerant of an unmigrated DB (returns the
    decoder-only fallback).
    """
    import re

    fname_lower = filename.lower()
    try:
        from reference.models import SourcePattern
        patterns = list(
            SourcePattern.objects.filter(enabled=True)
            .select_related('operator')
            .order_by('priority', 'id')
        )
    except Exception:
        patterns = []

    for sp in patterns:
        try:
            hit = (re.search(sp.pattern, fname_lower, re.IGNORECASE)
                   if sp.is_regex else sp.pattern.lower() in fname_lower)
        except re.error:
            hit = False
        if hit:
            decoder = sp.decoder_type
            if not decoder or decoder == DecoderType.AUTO:
                decoder = detect_decoder_type(filename)
            return FileClassification(
                operator=sp.operator.code,
                vendor=sp.vendor,
                network_element=sp.network_element,
                decoder_type=decoder,
                matched_pattern=sp,
            )

    # No pattern matched — decoder-only fallback (back-compat).
    return FileClassification(decoder_type=detect_decoder_type(filename))


def detect_decoder_type(filename: str) -> str:
    """Detect decoder type from filename.

    Checks filename patterns first (most specific), then falls back
    to extension mapping.

    Args:
        filename: Original filename (e.g. 'bFTMSX01_20260317.dat')

    Returns:
        DecoderType string (e.g. 'MSC', 'PGW', etc.)
    """
    fname_lower = filename.lower()

    # 1. Check filename patterns first
    for pattern, decoder in FILENAME_PATTERNS.items():
        if pattern in fname_lower:
            return decoder

    # 2. Fall back to extension
    import os
    ext = os.path.splitext(filename)[1].lower()
    if ext in EXTENSION_MAP:
        return EXTENSION_MAP[ext]

    return DecoderType.AUTO
