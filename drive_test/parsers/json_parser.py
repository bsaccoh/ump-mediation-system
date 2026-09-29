"""JSON drive-test parser.

Accepts a top-level array of row objects, or an object with a ``samples`` /
``data`` / ``rows`` array of row objects. Each row is a flat mapping of column
name to value (the same vocabulary as the CSV parser).
"""
from __future__ import annotations

import json
from typing import Iterator

from .base import DriveTestParser, ParsedSample, ParserCapabilities
from .fields import ColumnMapper, SYNONYMS, normalize_header

_CAPS = ParserCapabilities(metrics=frozenset(), events=True, data=True, voice=True)
_ARRAY_KEYS = ('samples', 'data', 'rows', 'measurements', 'records')


def _extract_rows(doc):
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for k in _ARRAY_KEYS:
            if isinstance(doc.get(k), list):
                return doc[k]
    return []


class JsonDriveTestParser(DriveTestParser):
    name = 'JSON'
    extensions = ('.json',)
    capabilities = _CAPS

    @classmethod
    def sniff(cls, path: str) -> float:
        if not path.lower().endswith('.json'):
            return 0.0
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return 0.0
        rows = _extract_rows(doc)
        if not rows or not isinstance(rows[0], dict):
            return 0.2
        known = sum(1 for h in rows[0] if normalize_header(h) in SYNONYMS)
        return 0.95 if known >= 2 else 0.4

    def parse(self, path: str) -> Iterator[ParsedSample]:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            doc = json.load(fh)
        rows = _extract_rows(doc)
        if not rows or not isinstance(rows[0], dict):
            return
        # Build the mapper from the union of keys across a lead window, so
        # rows with sparse keys still map every column that appears.
        keys: list[str] = []
        seen = set()
        for row in rows[:200]:
            if isinstance(row, dict):
                for k in row:
                    if k not in seen:
                        seen.add(k)
                        keys.append(k)
        mapper = ColumnMapper(keys)
        if not mapper.mapping:
            return
        for row in rows:
            if isinstance(row, dict):
                yield mapper.to_sample(row)
