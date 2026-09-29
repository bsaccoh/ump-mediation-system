"""Parser registry + confidence-based detection.

Detection asks every registered parser how confident it is (``sniff`` → 0..1)
and picks the highest above a threshold. There is no silent fallback: if no
parser is confident, detection returns None and the caller reports that the
format is unsupported, rather than mis-parsing it as CSV.
"""
from __future__ import annotations

from .base import DriveTestParser
from .csv_parser import CsvDriveTestParser
from .excel_parser import ExcelDriveTestParser
from .json_parser import JsonDriveTestParser
from .zip_parser import ZipDriveTestParser

# Registration order is not significance — detection is by confidence.
PARSERS: list[type[DriveTestParser]] = [
    CsvDriveTestParser,
    JsonDriveTestParser,
    ExcelDriveTestParser,
    ZipDriveTestParser,
]

MIN_CONFIDENCE = 0.3


def rank_parsers(path: str, exclude=()):
    """Return [(parser_cls, confidence), …] sorted high→low, confidence>0."""
    scored = []
    for cls in PARSERS:
        if cls in exclude:
            continue
        try:
            conf = cls.sniff(path)
        except Exception:
            conf = 0.0
        if conf > 0:
            scored.append((cls, conf))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def detect_parser(path: str, exclude=()) -> DriveTestParser | None:
    """Return an instance of the most confident parser, or None if none clears
    ``MIN_CONFIDENCE``.
    """
    ranked = rank_parsers(path, exclude=exclude)
    if ranked and ranked[0][1] >= MIN_CONFIDENCE:
        return ranked[0][0]()
    return None


def detect_format(path: str) -> tuple[str, float]:
    """Return (format_name, confidence); ('UNKNOWN', 0.0) when unsupported."""
    ranked = rank_parsers(path)
    if ranked and ranked[0][1] >= MIN_CONFIDENCE:
        return ranked[0][0].name, round(ranked[0][1], 3)
    return 'UNKNOWN', 0.0
