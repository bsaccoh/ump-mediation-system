"""Upload handling — stream an uploaded log to disk, hash it, dedup it.

No parsing happens here (that is Phase 2). This only lands the raw file safely
under MEDIA_ROOT and records a DriveTestFile row, marking a re-upload of
identical content as DUPLICATE rather than double-counting it.
"""
import hashlib
import logging
import re
from pathlib import Path

from django.conf import settings

from drive_test.models import DriveTestFile
from drive_test.models.enums import FileStatus

logger = logging.getLogger('drive_test')

_CHUNK = 1024 * 1024  # 1 MiB
_SAFE = re.compile(r'[^A-Za-z0-9._-]+')

# Formats accepted for upload in Phase 1 (parsers arrive in Phase 2).
ALLOWED_EXTENSIONS = {'.csv', '.xlsx', '.xls', '.json', '.txt', '.zip'}


def _safe_name(name: str) -> str:
    base = Path(name).name
    cleaned = _SAFE.sub('_', base).strip('_')
    return cleaned or 'upload.bin'


def campaign_raw_dir(campaign) -> Path:
    root = Path(settings.MEDIA_ROOT) / 'drive_test' / str(campaign.project_id) / str(campaign.id) / 'raw'
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_upload(campaign, uploaded_file, user):
    """Persist one uploaded file. Returns ``(DriveTestFile, created: bool)``.

    ``created`` is False when the identical content already exists for this
    campaign — in that case the returned row is the existing one and nothing is
    written twice.
    """
    original = _safe_name(uploaded_file.name)
    ext = Path(original).suffix.lower()
    dest_dir = campaign_raw_dir(campaign)
    tmp_path = dest_dir / f'.incoming-{original}'

    sha = hashlib.sha256()
    size = 0
    with open(tmp_path, 'wb') as fh:
        for chunk in uploaded_file.chunks(_CHUNK):
            fh.write(chunk)
            sha.update(chunk)
            size += len(chunk)
    digest = sha.hexdigest()

    existing = DriveTestFile.objects.filter(campaign=campaign, sha256=digest).first()
    if existing:
        tmp_path.unlink(missing_ok=True)
        return existing, False

    final_path = dest_dir / f'{digest[:12]}-{original}'
    tmp_path.replace(final_path)

    status = FileStatus.READY if ext in ALLOWED_EXTENSIONS else FileStatus.PENDING
    dtf = DriveTestFile.objects.create(
        campaign=campaign,
        original_name=original,
        stored_path=str(final_path),
        size_bytes=size,
        sha256=digest,
        detected_format=ext.lstrip('.').upper(),
        status=status,
        uploaded_by=user if getattr(user, 'is_authenticated', False) else None,
    )
    logger.info('Stored drive-test upload %s (%d bytes) for campaign %s',
                original, size, campaign.id)
    return dtf, True
