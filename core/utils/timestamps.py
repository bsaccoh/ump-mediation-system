"""Timestamp parsing utilities for CDR processing."""
from datetime import datetime, timezone
from typing import Optional


def parse_mediation_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse mediation format timestamp (YYYYMMDDHHMMSS).

    Args:
        ts_str: Timestamp string in YYYYMMDDHHMMSS format.

    Returns:
        datetime or None if parsing fails.
    """
    if not ts_str:
        return None

    ts_str = str(ts_str).strip()
    if not ts_str or ts_str.lower() in ('none', 'null', ''):
        return None

    if len(ts_str) >= 14 and ts_str[:14].isdigit():
        try:
            dt = datetime.strptime(ts_str[:14], '%Y%m%d%H%M%S')
            return dt.replace(tzinfo=timezone.utc)
        except (ValueError, OverflowError):
            pass

    # Fallback: common formats
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%d/%m/%Y %H:%M:%S'):
        try:
            dt = datetime.strptime(ts_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except (ValueError, OverflowError):
            continue

    return None


def parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse various timestamp formats (legacy/generic).

    Tries multiple formats and returns the first successful parse.
    """
    if not ts_str:
        return None

    ts_str = str(ts_str).strip()
    if not ts_str or ts_str.lower() in ('none', 'null', ''):
        return None

    formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y%m%d%H%M%S',
        '%Y/%m/%d %H:%M:%S',
        '%d/%m/%Y %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S',
    ]

    for fmt in formats:
        try:
            return datetime.strptime(ts_str[:19], fmt)
        except (ValueError, OverflowError):
            continue

    # Last resort: 14-digit numeric
    if len(ts_str) >= 14 and ts_str[:14].isdigit():
        try:
            return datetime.strptime(ts_str[:14], '%Y%m%d%H%M%S')
        except (ValueError, OverflowError):
            pass

    return None
