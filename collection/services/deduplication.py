"""
File Deduplication
===================
Hash-based file deduplication to prevent processing the same file twice.
"""
from core.utils.hashing import file_md5
from collection.models import CDRFile


def check_duplicate(file_path: str) -> bool:
    """Check if a file has already been processed.

    Args:
        file_path: Path to the file to check.

    Returns:
        True if this file is a duplicate.
    """
    file_hash = file_md5(file_path)
    return CDRFile.objects.filter(
        file_hash=file_hash,
        status__in=[CDRFile.Status.COMPLETED, CDRFile.Status.PROCESSING]
    ).exists()


def get_file_hash(file_path: str) -> str:
    """Get MD5 hash of a file."""
    return file_md5(file_path)
