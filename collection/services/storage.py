"""Per-operator input storage paths.

Layout: ``DATA_DIR/{operator}/input/{vendor}/{network_element}/<original filename>``.
Operator / vendor / network-element are directory segments only — the file keeps
its original name. Unknown segments fall back to a safe token so a file is never
lost.
"""
import os

from django.conf import settings
from collection.services.paths import PathBuilder
from collection.services.transfer import publish_file


def input_storage_dir(operator=None, vendor=None, network_element=None,
                      decoder_type=None) -> str:
    """Return the canonical input published directory for a collected file."""
    stream = (decoder_type or network_element or 'unknown').lower()
    directory = PathBuilder.input_published(operator or 'unknown', stream)
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def processing_storage_dir(operator=None, network_element=None,
                           decoder_type=None, cbs_substream=None) -> str:
    """Return the canonical processing directory for a file about to be decoded."""
    stream = (decoder_type or network_element or 'unknown').lower()
    directory = PathBuilder.processing(operator or 'unknown', stream, cbs_substream)
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def archive_storage_dir(operator=None, vendor=None, network_element=None,
                        decoder_type=None, cbs_substream=None) -> str:
    """Return the date/hour-partitioned original-input archive directory."""
    stream = (decoder_type or network_element or 'unknown').lower()
    directory = PathBuilder.input_archive(
        operator or 'unknown', stream, cbs_substream=cbs_substream,
    )
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


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
                 decoder_type=None, cbs_substream=None) -> str:
    """Archive only after a verified staged copy is ready."""
    archive_dir = archive_storage_dir(
        operator, vendor, network_element, decoder_type, cbs_substream,
    )
    return str(publish_file(
        path,
        os.path.join(archive_dir, 'staging'),
        archive_dir,
        remove_source=True,
    ))


def output_archive_storage_dir(downstream=None, operator=None,
                               network_element=None, decoder_type=None,
                               cbs_substream=None) -> str:
    """Return the date/hour-partitioned output archive directory."""
    stream = (decoder_type or network_element or 'unknown').lower()
    directory = PathBuilder.output_archive(
        downstream or 'unknown', operator or 'unknown', stream,
        cbs_substream=cbs_substream,
    )
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def output_archive_file(path: str, downstream=None, operator=None,
                        network_element=None, decoder_type=None,
                        cbs_substream=None) -> str:
    """Archive a delivered output file via staging-then-publish."""
    archive_dir = output_archive_storage_dir(
        downstream, operator, network_element, decoder_type, cbs_substream,
    )
    return str(publish_file(
        path,
        os.path.join(archive_dir, 'staging'),
        archive_dir,
        remove_source=True,
    ))


def error_storage_dir(operator=None, stream=None, decoder_type=None,
                      cbs_substream=None) -> str:
    """Return the error directory for failed input files."""
    stream = (stream or decoder_type or 'unknown').lower()
    directory = PathBuilder.error_input(operator or 'unknown', stream, cbs_substream)
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def quarantine_storage_dir(operator=None, stream=None, decoder_type=None,
                           cbs_substream=None) -> str:
    """Return the quarantine directory for suspicious files."""
    stream = (stream or decoder_type or 'unknown').lower()
    directory = PathBuilder.quarantine(operator or 'unknown', stream, cbs_substream)
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def duplicate_file(path: str, operator=None, vendor=None, network_element=None,
                   decoder_type=None) -> str:
    """Move a duplicate input file into its per-operator duplicates dir."""
    return _move_into(path, duplicates_storage_dir(operator, vendor, network_element, decoder_type))
