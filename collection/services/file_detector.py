"""
File Detector
==============
Auto-detect decoder type from filename patterns and extensions.
"""
from core.enums import DecoderType

# Filename patterns (checked FIRST - more specific than extensions)
FILENAME_PATTERNS = {
    'pgw': DecoderType.PGW,
    'ggsn': DecoderType.PGW,
    'sgw': DecoderType.SGW,
    'sgsn': DecoderType.SGSN,
    'w_abr': DecoderType.OCS,
    'w_smo': DecoderType.OCS,
    'w_smr': DecoderType.OCS,
    'w_rec': DecoderType.OCS,
    'gprs_': DecoderType.OCS,
    'cbs_cdr': DecoderType.CBS,
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
