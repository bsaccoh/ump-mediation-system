"""
Drive Test File Handler

Responsibilities:
  - SHA-256 dedup detection before accepting a file
  - Atomic staging write to incoming/ then archive to raw/
  - Parser auto-detection (by extension and magic bytes)
  - Dispatching processing jobs via Celery

The original file path is always immutable after staging; processing only
reads it and writes derived data to separate tables.
"""

import hashlib
import logging
import shutil
from pathlib import Path

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Storage sub-paths under UMP_STORAGE_ROOT/drive-test/
_SUBPATHS = {
    'incoming': 'drive-test/incoming',
    'raw': 'drive-test/raw',
    'processing': 'drive-test/processing',
    'failed': 'drive-test/failed',
    'archive': 'drive-test/archive',
}


def _storage_root() -> Path:
    return Path(getattr(settings, 'UMP_STORAGE_ROOT', settings.BASE_DIR / 'storage'))


def _dir(key: str) -> Path:
    p = _storage_root() / _SUBPATHS[key]
    p.mkdir(parents=True, exist_ok=True)
    return p


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def check_duplicate(sha256: str) -> bool:
    """Return True if a DriveTestFile with this SHA-256 already exists."""
    from drive_test.models import DriveTestFile
    return DriveTestFile.objects.filter(sha256=sha256).exists()


def stage_file(source_path: Path, original_filename: str, operator_code: str) -> tuple[Path, str]:
    """
    Copy the upload to immutable storage and return (stored_path, sha256).
    Raises FileExistsError if a duplicate is detected.
    """
    sha256 = sha256_of_file(source_path)
    if check_duplicate(sha256):
        raise FileExistsError(f'Duplicate drive test file (sha256={sha256})')

    dest_dir = _dir('raw') / operator_code / source_path.suffix.lstrip('.') or 'unknown'
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f'{sha256[:16]}_{original_filename}'
    shutil.copy2(source_path, dest)
    logger.info('Staged drive test file: %s -> %s', original_filename, dest)
    return dest, sha256


def detect_parser(file_path: Path) -> 'ParserProfile | None':
    """
    Look up a ParserProfile by file extension and (optionally) magic bytes.
    Returns None if no profile matches — caller should record as 'unknown'.
    """
    from drive_test.models import ParserProfile

    ext = file_path.suffix.lower()
    candidates = ParserProfile.objects.filter(is_active=True)

    # Extension match first
    for profile in candidates:
        exts = profile.file_extensions or []
        if ext in [e.lower() for e in exts]:
            # Magic bytes check if configured
            if profile.magic_bytes:
                try:
                    with open(file_path, 'rb') as fh:
                        header = fh.read(16).hex()
                    if header.startswith(profile.magic_bytes.lower()):
                        return profile
                except OSError:
                    pass
            else:
                return profile

    return None


def dispatch_processing(drive_file_id: int) -> None:
    """Queue async processing of a DriveTestFile via Celery (via JobRecord + tracked_task)."""
    from drive_test.tasks import process_drive_test_file
    process_drive_test_file(drive_file_id)
