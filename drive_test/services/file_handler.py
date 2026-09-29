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


#: A profile must score at least this to be accepted. Below it the file is
#: treated as an unrecognised format rather than parsed on a weak guess.
_DETECTION_THRESHOLD = 0.3


def load_parser_class(profile) -> type | None:
    """Import the parser class a ParserProfile names, or None if unloadable."""
    import importlib

    if not profile or not profile.parser_class:
        return None
    try:
        module_path, cls_name = profile.parser_class.rsplit('.', 1)
        return getattr(importlib.import_module(module_path), cls_name)
    except Exception as exc:
        logger.warning('Parser profile %r names an unloadable class %r: %s',
                       profile.name, profile.parser_class, exc)
        return None


def score_parsers(file_path: Path) -> list[tuple[float, 'ParserProfile']]:
    """Score every active ParserProfile against a file, best first.

    Each parser scores itself via sniff(), so a format that can confirm its own
    structure outranks one matching on extension alone.
    """
    from drive_test.models import ParserProfile

    scored: list[tuple[float, 'ParserProfile']] = []
    for profile in ParserProfile.objects.filter(is_active=True):
        parser_cls = load_parser_class(profile)
        if parser_cls is None:
            continue
        try:
            confidence = float(parser_cls.sniff(file_path))
        except Exception:
            # A parser must never break detection for the others.
            logger.exception('Parser %r raised during sniff of %s',
                             profile.name, file_path.name)
            continue
        if confidence > 0.0:
            scored.append((confidence, profile))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def detect_parser(file_path: Path) -> 'ParserProfile | None':
    """Resolve the best-matching ParserProfile, or None when nothing fits.

    Picks the HIGHEST confidence rather than the first extension match. In this
    domain .csv, .txt and .log collide across formats, so first-match-wins
    decides on whichever profile happened to be created first — which silently
    mis-parses a file the moment a second format is added.
    """
    scored = score_parsers(file_path)
    if not scored:
        logger.info('No parser matched %s', file_path.name)
        return None

    confidence, profile = scored[0]
    if confidence < _DETECTION_THRESHOLD:
        logger.info('Best parser for %s was %r at %.2f, below the %.2f threshold',
                    file_path.name, profile.name, confidence, _DETECTION_THRESHOLD)
        return None

    if len(scored) > 1:
        runner_up_score, runner_up = scored[1]
        logger.debug('Parser for %s: %r (%.2f), next %r (%.2f)',
                     file_path.name, profile.name, confidence,
                     runner_up.name, runner_up_score)
    return profile


def capabilities_for(profile) -> 'ParserCapabilities | None':
    """The declared capabilities of the parser a profile names."""
    parser_cls = load_parser_class(profile)
    return getattr(parser_cls, 'capabilities', None) if parser_cls else None


def explain_detection_failure(file_path: Path) -> str:
    """A message saying WHY no parser matched.

    Detection now knows the difference between two very different situations,
    so the upload page should not report them identically:

        an unknown extension        — the format is not supported at all
        a known extension, no match — the format is supported, this file is
                                      not readable as it (truncated, corrupt,
                                      or renamed from something else)

    The second is by far the more common support question, and telling someone
    their file format is unsupported when it is actually damaged sends them in
    entirely the wrong direction.
    """
    from drive_test.models import ParserProfile

    ext = file_path.suffix.lower()
    if not ext:
        return ('The file has no extension, so its format could not be '
                'identified. Rename it with the correct extension and retry.')

    known = [
        p for p in ParserProfile.objects.filter(is_active=True)
        if ext in [e.lower() for e in (p.file_extensions or [])]
    ]
    if not known:
        supported = sorted({
            e.lower()
            for p in ParserProfile.objects.filter(is_active=True)
            for e in (p.file_extensions or [])
        })
        return (
            f'{ext} files are not a supported drive-test format. '
            f'Supported formats: {", ".join(supported) or "none configured"}.'
        )

    names = ', '.join(sorted(p.name for p in known))
    return (
        f'This looks like a {ext} file, which {names} normally reads, but its '
        f'contents could not be recognised. The file may be truncated, '
        f'corrupt, or renamed from another format.'
    )


def dispatch_processing(drive_file_id: int) -> None:
    """Queue async processing of a DriveTestFile via Celery (via JobRecord + tracked_task)."""
    from drive_test.tasks import process_drive_test_file
    process_drive_test_file(drive_file_id)
