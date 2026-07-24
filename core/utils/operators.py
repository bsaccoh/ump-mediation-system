"""
Operator classification helpers shared across dashboard analytics and the
interconnect billing module.

Source of truth for the Sierra Leone operator prefix map.  Extracted from
``dashboard/views.py`` so both the Traffic Matrix dashboard and the
``interconnect`` app can call ``classify_operator(msisdn)`` without
duplicating the table.
"""
from typing import Optional


# 2-digit MSISDN prefix → operator name (after stripping CC '232' and leading 0).
SL_OPERATOR_PREFIX_MAP = {
    # Orange SL
    '72': 'Orange', '73': 'Orange', '74': 'Orange', '75': 'Orange',
    '76': 'Orange', '78': 'Orange', '79': 'Orange',
    # Africell SL
    '77': 'Africell', '30': 'Africell', '31': 'Africell',
    '32': 'Africell', '33': 'Africell', '34': 'Africell',
    # Qcell
    '80': 'Qcell', '88': 'Qcell',
    # Smart Mobile
    '90': 'Smart', '99': 'Smart', '98': 'Smart',
    # Sierratel (fixed line)
    '22': 'Sierratel', '23': 'Sierratel', '25': 'Sierratel',
}

# Default host network name, used when no active operator is set. The active
# operator is resolved per request/file via core.operator_context; its slug
# maps to the classification name (e.g. 'orange' -> 'Orange').
HOME_OPERATOR = 'Orange'


def home_operator_name(operator_code: Optional[str] = None) -> str:
    """Classification name of the home network for the active (or given) operator.

    Operator slugs ('orange', 'africell', 'qcell') map to the
    classify_operator() names ('Orange', 'Africell', 'Qcell') via capitalize().
    """
    code = operator_code
    if code is None:
        try:
            from core.operator_context import get_operator
            code = get_operator()
        except Exception:
            code = None
    if not code:
        return HOME_OPERATOR
    return code.capitalize()

# Canonical display order for matrix rows/columns and dropdowns.
SL_OPERATOR_ORDER = [
    'Orange', 'Africell', 'Qcell', 'Smart', 'Sierratel',
    'Other SL', 'International', 'Short Code', 'Alphanumeric', 'Unknown',
]


def classify_operator(msisdn: Optional[str]) -> str:
    """Return the operator name for a Sierra Leone MSISDN.

    Returns one of: ``Orange``, ``Africell``, ``Qcell``, ``Smart``,
    ``Sierratel``, ``Other SL``, ``International``, ``Short Code``,
    ``Alphanumeric``, ``Unknown``.
    """
    if not msisdn:
        return 'Unknown'
    s = msisdn.strip()
    if any(c.isalpha() for c in s):
        return 'Alphanumeric'
    s = s.lstrip('+').lstrip('0')
    if s.startswith('00'):
        s = s[2:]
    digits_only = ''.join(c for c in s if c.isdigit())
    if not digits_only:
        return 'Unknown'
    # Strip 232 country code if present
    rest = digits_only[3:] if digits_only.startswith('232') else digits_only
    # Short codes
    if len(rest) <= 6:
        return 'Short Code'
    # Standard 8-digit Sierra Leone MSISDN
    if len(rest) == 8:
        return SL_OPERATOR_PREFIX_MAP.get(rest[:2], 'Other SL')
    # 9-digit local-dial format (leading 0)
    if len(rest) == 9 and rest[0] == '0':
        return SL_OPERATOR_PREFIX_MAP.get(rest[1:3], 'Other SL')
    # Anything 7+ digits not matching SL → foreign
    if len(rest) >= 7:
        return 'International'
    return 'Unknown'


def is_home_operator(msisdn: Optional[str], operator_code: Optional[str] = None) -> bool:
    """True if the MSISDN belongs to the active (or given) operator's network."""
    return classify_operator(msisdn) == home_operator_name(operator_code)
