"""File hashing utilities for deduplication."""
import hashlib


def file_md5(file_path: str) -> str:
    """Calculate MD5 hash of a file.

    Args:
        file_path: Path to the file.

    Returns:
        Hex digest string.
    """
    h = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()
