"""Excel (.xlsx/.xls) drive-test parser, backed by openpyxl (read-only)."""
from __future__ import annotations

from typing import Iterator

from .base import DriveTestParser, ParsedSample, ParserCapabilities
from .fields import ColumnMapper, SYNONYMS, normalize_header

_CAPS = ParserCapabilities(metrics=frozenset(), events=True, data=True, voice=True)


class ExcelDriveTestParser(DriveTestParser):
    name = 'XLSX'
    extensions = ('.xlsx', '.xlsm')
    capabilities = _CAPS

    @classmethod
    def sniff(cls, path: str) -> float:
        if not path.lower().endswith(cls.extensions):
            return 0.0
        try:
            import openpyxl  # noqa: F401
        except ImportError:  # pragma: no cover
            return 0.0
        try:
            headers = cls._headers(path)
        except Exception:
            return 0.0
        known = sum(1 for h in headers if normalize_header(h) in SYNONYMS)
        return 0.9 if known >= 2 else 0.5

    @staticmethod
    def _headers(path):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
                return [str(c) if c is not None else '' for c in row]
            return []
        finally:
            wb.close()

    def parse(self, path: str) -> Iterator[ParsedSample]:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            rows = ws.iter_rows(values_only=True)
            try:
                header = next(rows)
            except StopIteration:
                return
            headers = [str(c) if c is not None else f'col{i}' for i, c in enumerate(header)]
            mapper = ColumnMapper(headers)
            if not mapper.mapping:
                return
            for values in rows:
                row = {headers[i]: values[i] for i in range(min(len(headers), len(values)))}
                yield mapper.to_sample(row)
        finally:
            wb.close()
