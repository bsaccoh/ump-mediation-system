"""CSV / delimited-text drive-test parser."""
from __future__ import annotations

import csv
from typing import Iterator

from .base import DriveTestParser, ParsedSample, ParserCapabilities
from .fields import ColumnMapper, SYNONYMS, normalize_header

_CAPS = ParserCapabilities(metrics=frozenset(), events=True, data=True, voice=True)


def _open_text(path):
    return open(path, 'r', encoding='utf-8-sig', errors='replace', newline='')


def _detect_dialect(sample: str):
    try:
        return csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        class _D(csv.Dialect):
            delimiter = ','
            quotechar = '"'
            doublequote = True
            skipinitialspace = True
            lineterminator = '\r\n'
            quoting = csv.QUOTE_MINIMAL
        return _D()


class CsvDriveTestParser(DriveTestParser):
    name = 'CSV'
    extensions = ('.csv', '.txt', '.tsv')
    capabilities = _CAPS

    @classmethod
    def sniff(cls, path: str) -> float:
        lower = path.lower()
        if not lower.endswith(cls.extensions):
            return 0.0
        base = 0.85 if lower.endswith('.csv') or lower.endswith('.tsv') else 0.35
        try:
            with _open_text(path) as fh:
                head = fh.readline()
        except OSError:
            return 0.0
        if not head:
            return 0.0
        dialect = _detect_dialect(head)
        headers = [h for h in head.strip().split(dialect.delimiter)]
        known = sum(1 for h in headers if normalize_header(h) in SYNONYMS)
        if known >= 2:
            return min(0.98, base + 0.1)
        if known == 0 and base < 0.5:
            return 0.1  # a .txt with no recognisable columns — probably not ours
        return base

    def parse(self, path: str) -> Iterator[ParsedSample]:
        with _open_text(path) as fh:
            head = fh.readline()
            if not head:
                return
            dialect = _detect_dialect(head)
            fh.seek(0)
            reader = csv.DictReader(fh, dialect=dialect)
            mapper = ColumnMapper(reader.fieldnames or [])
            if not mapper.mapping:
                return
            for row in reader:
                yield mapper.to_sample(row)
