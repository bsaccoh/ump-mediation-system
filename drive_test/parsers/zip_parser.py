"""ZIP archive parser — dispatches each member to the best-fit parser.

Members are extracted to a temp dir (with path-traversal protection) and each
supported member is parsed via the registry. Unsupported members are skipped.
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from typing import Iterator

from .base import DriveTestParser, ParsedSample, ParserCapabilities

_CAPS = ParserCapabilities(metrics=frozenset(), events=True, data=True, voice=True)


class ZipDriveTestParser(DriveTestParser):
    name = 'ZIP'
    extensions = ('.zip',)
    capabilities = _CAPS

    @classmethod
    def sniff(cls, path: str) -> float:
        if not path.lower().endswith('.zip'):
            return 0.0
        return 0.9 if zipfile.is_zipfile(path) else 0.0

    def parse(self, path: str) -> Iterator[ParsedSample]:
        from .registry import detect_parser  # lazy — avoids circular import

        with zipfile.ZipFile(path) as zf:
            with tempfile.TemporaryDirectory(prefix='dt-zip-') as tmp:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename
                    # Path-traversal guard: never write outside tmp.
                    dest = os.path.realpath(os.path.join(tmp, name))
                    if not dest.startswith(os.path.realpath(tmp) + os.sep):
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with zf.open(info) as src, open(dest, 'wb') as out:
                        out.write(src.read())
                    parser = detect_parser(dest, exclude=(ZipDriveTestParser,))
                    if parser is None:
                        continue
                    yield from parser.parse(dest)
