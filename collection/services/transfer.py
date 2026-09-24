"""Verified transfers that never expose partial files in published directories."""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024
_REPLACE_RETRIES = 4
_REPLACE_BACKOFF = (0.1, 0.3, 0.5, 1.0)


def _replace_with_retry(src: Path, dst: Path) -> None:
    """os.replace with retry for transient Windows file locks (antivirus, indexer)."""
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != 'win32' or attempt == _REPLACE_RETRIES - 1:
                raise
            delay = _REPLACE_BACKOFF[min(attempt, len(_REPLACE_BACKOFF) - 1)]
            logger.debug(f'os.replace blocked (attempt {attempt + 1}), retrying in {delay}s')
            time.sleep(delay)

_UMP_ROOTS = None


def _get_ump_roots() -> list[Path]:
    """Lazily build the list of valid UMP root directories."""
    global _UMP_ROOTS
    if _UMP_ROOTS is None:
        _UMP_ROOTS = [
            Path(getattr(settings, attr)).resolve(strict=False)
            for attr in (
                'UMP_STORAGE_ROOT', 'UMP_INPUT_ROOT', 'UMP_OUTPUT_ROOT',
                'UMP_ARCHIVE_ROOT', 'UMP_PROCESSING_ROOT', 'UMP_ERROR_ROOT',
                'UMP_QUARANTINE_ROOT', 'DATA_DIR',
            )
            if hasattr(settings, attr)
        ]
    return _UMP_ROOTS


def _validate_containment(directory: Path) -> None:
    """Raise if ``directory`` resolves outside all known UMP roots."""
    resolved = directory.resolve(strict=False)
    for root in _get_ump_roots():
        try:
            resolved.relative_to(root)
            return
        except ValueError:
            continue
    raise ValueError(
        f'Path {directory} is outside all UMP storage roots',
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(_CHUNK_SIZE), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_filename(filename: str) -> str:
    if not filename or Path(filename).name != filename or filename in {'.', '..'}:
        raise ValueError('Filename must not contain a path')
    return filename


def publish_file(source: str | Path, staging_directory: str | Path,
                 published_directory: str | Path, *, filename: str | None = None,
                 remove_source: bool = False) -> Path:
    """Copy to staging, verify, then atomically publish within the destination FS."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    filename = _validate_filename(filename or source.name)
    staging_directory = Path(staging_directory)
    published_directory = Path(published_directory)
    _validate_containment(staging_directory)
    _validate_containment(published_directory)
    staging_directory.mkdir(parents=True, exist_ok=True)
    published_directory.mkdir(parents=True, exist_ok=True)

    staged = staging_directory / f'.{filename}.{uuid.uuid4().hex}.part'
    destination = published_directory / filename
    try:
        with source.open('rb') as source_file, staged.open('xb') as staging_file:
            shutil.copyfileobj(source_file, staging_file, _CHUNK_SIZE)
            staging_file.flush()
            os.fsync(staging_file.fileno())
        if staged.stat().st_size != source.stat().st_size or _sha256(staged) != _sha256(source):
            raise IOError(f'Verification failed for {source.name}')
        _replace_with_retry(staged, destination)
        if remove_source:
            source.unlink()
        return destination
    finally:
        if staged.exists():
            staged.unlink()


def publish_bytes(payload: bytes, staging_directory: str | Path,
                  published_directory: str | Path, filename: str) -> Path:
    """Write a generated payload into staging, verify its size, then publish it."""
    filename = _validate_filename(filename)
    staging_directory = Path(staging_directory)
    published_directory = Path(published_directory)
    _validate_containment(staging_directory)
    _validate_containment(published_directory)
    staging_directory.mkdir(parents=True, exist_ok=True)
    published_directory.mkdir(parents=True, exist_ok=True)
    staged = staging_directory / f'.{filename}.{uuid.uuid4().hex}.part'
    destination = published_directory / filename
    try:
        with staged.open('xb') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if staged.stat().st_size != len(payload):
            raise IOError(f'Verification failed for generated output {filename}')
        _replace_with_retry(staged, destination)
        return destination
    finally:
        if staged.exists():
            staged.unlink()
