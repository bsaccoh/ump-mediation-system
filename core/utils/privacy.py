"""
Subscriber-data privacy helpers.

Used wherever raw CDR fields (MSISDN, IMSI, IMEI) are surfaced outside the
mediation pipeline itself — e.g. audit-case CDR drill-down — so regulatory
staff see enough of the identifier to correlate records without exposing the
full subscriber number.
"""


def mask_msisdn(value, visible_start=3, visible_end=2):
    """Mask a subscriber number, keeping the leading country/prefix digits
    and a short trailing suffix: '23276123456' -> '232***3456' with defaults
    tuned to a 2-3 digit country code plus the last 4 digits.
    """
    if not value:
        return value
    value = str(value)
    if len(value) <= visible_start + visible_end:
        return '*' * len(value)
    return f'{value[:visible_start]}{"*" * (len(value) - visible_start - visible_end)}{value[-visible_end:]}'
