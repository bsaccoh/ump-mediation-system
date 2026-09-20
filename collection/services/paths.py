"""Canonical, validated paths for the mediation data plane."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from django.conf import settings


_SAFE_SEGMENT = re.compile(r'^[a-z0-9][a-z0-9_.-]*$', re.IGNORECASE)
_CBS_SUBSTREAMS = {'data', 'voice', 'sms', 'recharge'}


def safe_segment(value: str | None, *, field: str, default: str = 'unknown') -> str:
    """Return a single safe path component or reject an unsafe configuration."""
    value = (value or default).strip().lower()
    if not _SAFE_SEGMENT.fullmatch(value) or value in {'.', '..'}:
        raise ValueError(f'Invalid {field} path segment')
    return value


class PathBuilder:
    """Build the canonical landing, archive, and processing paths in one place."""

    @staticmethod
    def stream_path(operator: str, stream: str, cbs_substream: str | None = None) -> tuple[str, ...]:
        operator = safe_segment(operator, field='operator')
        stream = safe_segment(stream, field='stream')
        if stream != 'cbs':
            return operator, stream
        if not cbs_substream:
            return operator, stream
        substream = safe_segment(cbs_substream, field='CBS substream')
        if substream not in _CBS_SUBSTREAMS:
            raise ValueError('CBS substream must be data, voice, sms, or recharge')
        return operator, stream, substream

    @classmethod
    def input_published(cls, operator: str, stream: str, cbs_substream: str | None = None) -> Path:
        return Path(settings.UMP_INPUT_ROOT).joinpath(*cls.stream_path(operator, stream, cbs_substream))

    @classmethod
    def input_staging(cls, operator: str, stream: str, cbs_substream: str | None = None) -> Path:
        return cls.input_published(operator, stream, cbs_substream) / 'staging'

    @classmethod
    def output_published(cls, downstream: str, operator: str, stream: str,
                         cbs_substream: str | None = None) -> Path:
        downstream = safe_segment(downstream, field='downstream')
        return Path(settings.UMP_OUTPUT_ROOT).joinpath(
            downstream, *cls.stream_path(operator, stream, cbs_substream)
        )

    @classmethod
    def output_staging(cls, downstream: str, operator: str, stream: str,
                       cbs_substream: str | None = None) -> Path:
        return cls.output_published(downstream, operator, stream, cbs_substream) / 'staging'

    @classmethod
    def input_archive(cls, operator: str, stream: str, *, cbs_substream: str | None = None,
                      timestamp: datetime | None = None) -> Path:
        timestamp = timestamp or datetime.utcnow()
        root = Path(settings.UMP_ARCHIVE_ROOT) / 'input'
        return root.joinpath(
            *cls.stream_path(operator, stream, cbs_substream),
            timestamp.strftime('%Y'), timestamp.strftime('%m'),
            timestamp.strftime('%d'), timestamp.strftime('%H'),
        )

    @classmethod
    def output_archive(cls, downstream: str, operator: str, stream: str, *,
                       cbs_substream: str | None = None,
                       timestamp: datetime | None = None) -> Path:
        timestamp = timestamp or datetime.utcnow()
        downstream = safe_segment(downstream, field='downstream')
        root = Path(settings.UMP_ARCHIVE_ROOT) / 'output'
        return root.joinpath(
            downstream, *cls.stream_path(operator, stream, cbs_substream),
            timestamp.strftime('%Y'), timestamp.strftime('%m'),
            timestamp.strftime('%d'), timestamp.strftime('%H'),
        )

    @classmethod
    def processing(cls, operator: str, stream: str,
                   cbs_substream: str | None = None) -> Path:
        return Path(settings.UMP_PROCESSING_ROOT).joinpath(
            *cls.stream_path(operator, stream, cbs_substream),
        )

    @classmethod
    def error_input(cls, operator: str, stream: str,
                    cbs_substream: str | None = None) -> Path:
        return Path(settings.UMP_ERROR_ROOT).joinpath(
            'input', *cls.stream_path(operator, stream, cbs_substream),
        )

    @classmethod
    def error_output(cls, downstream: str, operator: str, stream: str,
                     cbs_substream: str | None = None) -> Path:
        downstream = safe_segment(downstream, field='downstream')
        return Path(settings.UMP_ERROR_ROOT).joinpath(
            'output', downstream,
            *cls.stream_path(operator, stream, cbs_substream),
        )

    @classmethod
    def quarantine(cls, operator: str, stream: str,
                   cbs_substream: str | None = None) -> Path:
        return Path(settings.UMP_QUARANTINE_ROOT).joinpath(
            *cls.stream_path(operator, stream, cbs_substream),
        )

    @classmethod
    def validate_containment(cls, path: Path, root: Path) -> bool:
        """Verify ``path`` resolves inside ``root`` — prevents traversal attacks."""
        return is_within(path, root)

    @classmethod
    def validated_path(cls, path: Path, root: Path) -> Path:
        """Return ``path`` only if it resolves inside ``root``, else raise."""
        if not is_within(path, root):
            raise ValueError(
                f'Path {path} escapes root {root}',
            )
        return path


def is_within(path: Path, root: Path) -> bool:
    """True only when resolving ``path`` cannot escape ``root`` via symlinks."""
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True
