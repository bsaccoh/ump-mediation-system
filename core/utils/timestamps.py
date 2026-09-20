"""Timestamp parsing utilities for CDR processing."""
from datetime import datetime
from functools import lru_cache
from typing import Optional


@lru_cache(maxsize=4096)
def parse_mediation_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse mediation format timestamp (YYYYMMDDHHMMSS).

    Returns naive datetime (USE_TZ=False in this project).
    """
    if not ts_str:
        return None

    ts_str = str(ts_str).strip()
    if not ts_str or ts_str.lower() in ('none', 'null', ''):
        return None

    # Fast path: 14-digit numeric (YYYYMMDDHHMMSS) — avoids strptime overhead
    if len(ts_str) >= 14 and ts_str[:14].isdigit():
        s = ts_str[:14]
        try:
            return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]),
                            int(s[8:10]), int(s[10:12]), int(s[12:14]))
        except (ValueError, OverflowError):
            pass

    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%d/%m/%Y %H:%M:%S'):
        try:
            return datetime.strptime(ts_str, fmt)
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
