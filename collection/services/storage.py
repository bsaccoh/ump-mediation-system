"""Per-operator input storage paths.

Layout: ``DATA_DIR/{operator}/input/{vendor}/{network_element}/<original filename>``.
Operator / vendor / network-element are directory segments only — the file keeps
its original name. Unknown segments fall back to a safe token so a file is never
lost.
"""
import os

from django.conf import settings


def input_storage_dir(operator=None, vendor=None, network_element=None,
                      decoder_type=None) -> str:
    """Return (and create) the per-operator input directory for a file."""
    op = (operator or 'unknown').lower()
    vend = (vendor or 'unknown').lower()
    ne = (network_element or decoder_type or 'unknown').lower()
    directory = os.path.join(settings.DATA_DIR, op, 'input', vend, ne)
    os.makedirs(directory, exist_ok=True)
    return directory


def archive_storage_dir(operator=None, vendor=None, network_element=None,
                        decoder_type=None) -> str:
    """Return (and create) the per-operator archive dir, mirroring the input tree:
    DATA_DIR/{operator}/archive/{vendor}/{ne}/."""
    op = (operator or 'unknown').lower()
    vend = (vendor or 'unknown').lower()
    ne = (network_element or decoder_type or 'unknown').lower()
    directory = os.path.join(settings.DATA_DIR, op, 'archive', vend, ne)
    os.makedirs(directory, exist_ok=True)
    return directory


def duplicates_storage_dir(operator=None, vendor=None, network_element=None,
                           decoder_type=None) -> str:
    """Return (and create) the per-operator duplicates dir, mirroring the input
    tree: DATA_DIR/{operator}/duplicates/{vendor}/{ne}/."""
    op = (operator or 'unknown').lower()
    vend = (vendor or 'unknown').lower()
    ne = (network_element or decoder_type or 'unknown').lower()
    directory = os.path.join(settings.DATA_DIR, op, 'duplicates', vend, ne)
    os.makedirs(directory, exist_ok=True)
    return directory


def _move_into(path: str, dest_dir: str) -> str:
    """Move `path` into `dest_dir`, keeping the name (-N suffix on clash)."""
    import shutil

    base = os.path.basename(path)
    dest = os.path.join(dest_dir, base)
    if os.path.abspath(dest) == os.path.abspath(path):
        return path
    stem, ext = os.path.splitext(base)
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(dest_dir, f'{stem}-{n}{ext}')
        n += 1
    shutil.move(path, dest)
    return dest


def archive_file(path: str, operator=None, vendor=None, network_element=None,
                 decoder_type=None) -> str:
    """Move a processed input file into its per-operator archive dir."""
    return _move_into(path, archive_storage_dir(operator, vendor, network_element, decoder_type))


def duplicate_file(path: str, operator=None, vendor=None, network_element=None,
                   decoder_type=None) -> str:
    """Move a duplicate input file into its per-operator duplicates dir."""
    return _move_into(path, duplicates_storage_dir(operator, vendor, network_element, decoder_type))
