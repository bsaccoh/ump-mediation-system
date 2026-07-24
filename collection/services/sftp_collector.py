"""
SFTP Collector Service
=======================
Connects to remote SFTP sources, downloads new CDR files,
and creates CDRFile records to trigger processing.
"""
import os
import fnmatch
import logging
from datetime import datetime

import paramiko

from django.conf import settings
from collection.models import DataSource, CDRFile
from collection.services.file_detector import detect_decoder_type, classify_file
from collection.services.storage import input_storage_dir
from collection.services.deduplication import get_file_hash, check_duplicate

logger = logging.getLogger(__name__)


class SFTPCollector:
    """Collect CDR files from a remote SFTP source."""

    def __init__(self, source: DataSource):
        self.source = source
        self.transport = None
        self.sftp = None

    def connect(self):
        """Establish SFTP connection."""
        host = self.source.sftp_host
        port = self.source.sftp_port or 22
        username = self.source.sftp_username
        password = self.source.sftp_password
        key_path = self.source.sftp_key_path

        logger.info(f'Connecting to {host}:{port} as {username}')

        self.transport = paramiko.Transport((host, port))

        if key_path and os.path.exists(key_path):
            pkey = paramiko.RSAKey.from_private_key_file(key_path)
            self.transport.connect(username=username, pkey=pkey)
        else:
            self.transport.connect(username=username, password=password)

        self.sftp = paramiko.SFTPClient.from_transport(self.transport)
        logger.info(f'Connected to {host}')

    def disconnect(self):
        """Close SFTP connection."""
        try:
            if self.sftp:
                self.sftp.close()
            if self.transport:
                self.transport.close()
        except Exception:
            pass

    def list_remote_files(self) -> list:
        """List matching files on remote server."""
        remote_path = self.source.sftp_remote_path or '.'
        pattern = self.source.sftp_file_pattern or '*.dat'

        try:
            all_files = self.sftp.listdir(remote_path)
        except IOError as e:
            logger.error(f'Cannot list {remote_path}: {e}')
            return []

        # Filter by glob pattern
        matched = [f for f in all_files if fnmatch.fnmatch(f, pattern)]
        logger.info(f'Found {len(matched)} files matching "{pattern}" in {remote_path} (of {len(all_files)} total)')
        return matched

    def is_already_collected(self, filename: str) -> bool:
        """Check if file was already collected from this source."""
        return CDRFile.objects.filter(
            source=self.source,
            filename=filename
        ).exists()

    def download_file(self, remote_filename: str, cls=None) -> str:
        """Download a file from the remote server into the per-operator tree.

        Returns local file path on success, empty string on failure.
        """
        remote_path = self.source.sftp_remote_path or '.'
        remote_full = f'{remote_path}/{remote_filename}'

        cls = cls or classify_file(remote_filename)
        decoder_type = self.source.decoder_type
        if not decoder_type or decoder_type == 'AUTO':
            decoder_type = cls.decoder_type

        # Store under DATA_DIR/{operator}/input/{vendor}/{ne}/<original name>.
        # Fall back to the DataSource's configured vendor/NE when no pattern matched.
        local_dir = input_storage_dir(
            cls.operator,
            cls.vendor or (self.source.vendor or None),
            cls.network_element or (self.source.network_element or None),
            decoder_type,
        )
        local_filename = remote_filename  # keep original name (path carries vendor/op)
        local_path = os.path.join(local_dir, local_filename)

        try:
            self.sftp.get(remote_full, local_path)
            file_size = os.path.getsize(local_path)
            logger.info(f'Downloaded {remote_filename} ({file_size:,} bytes)')
            return local_path
        except Exception as e:
            logger.error(f'Failed to download {remote_filename}: {e}')
            if os.path.exists(local_path):
                os.remove(local_path)
            return ''

    def collect(self) -> dict:
        """Run full collection cycle for this source.

        Returns dict with stats: {collected, skipped, failed, errors}.
        """
        stats = {'collected': 0, 'skipped': 0, 'failed': 0, 'errors': []}

        try:
            self.connect()
            remote_files = self.list_remote_files()

            for filename in remote_files:
                # Skip already collected
                if self.is_already_collected(filename):
                    stats['skipped'] += 1
                    continue

                # Classify once (operator/vendor/NE/decoder) and reuse for both
                # storage routing and CDRFile tagging.
                cls = classify_file(filename)

                # Download
                local_path = self.download_file(filename, cls)
                if not local_path:
                    stats['failed'] += 1
                    stats['errors'].append(f'Download failed: {filename}')
                    continue

                # Dedup by hash
                file_hash = get_file_hash(local_path)
                if check_duplicate(local_path):
                    os.remove(local_path)
                    stats['skipped'] += 1
                    logger.info(f'Duplicate hash for {filename}, skipped')
                    continue

                decoder_type = self.source.decoder_type
                if not decoder_type or decoder_type == 'AUTO':
                    decoder_type = cls.decoder_type

                # Create CDRFile — signal triggers processing
                file_size = os.path.getsize(local_path)
                CDRFile.objects.create(
                    source=self.source,
                    filename=filename,
                    file_path=local_path,
                    file_size=file_size,
                    file_hash=file_hash,
                    decoder_type=decoder_type,
                    operator_code=cls.operator or '',
                    vendor=cls.vendor or (self.source.vendor or ''),
                    network_element=cls.network_element or (self.source.network_element or ''),
                    status=CDRFile.Status.PENDING,
                )
                stats['collected'] += 1
                logger.info(f'Collected {filename} (decoder={decoder_type})')

        except Exception as e:
            msg = f'SFTP collection error for {self.source.name}: {e}'
            logger.error(msg, exc_info=True)
            stats['errors'].append(str(e))
        finally:
            self.disconnect()

        return stats


def poll_source(source: DataSource) -> dict:
    """Poll a single SFTP data source for new files.

    Updates source status fields after collection.
    Returns stats dict.
    """
    collector = SFTPCollector(source)
    stats = collector.collect()

    # Update source record
    source.last_poll_time = datetime.now()
    if stats['errors']:
        source.last_poll_status = f"Error: {stats['errors'][0][:100]}"
    else:
        source.last_poll_status = (
            f"OK: {stats['collected']} new, {stats['skipped']} skipped"
        )
    source.files_collected += stats['collected']
    source.save(update_fields=['last_poll_time', 'last_poll_status', 'files_collected'])

    logger.info(
        f'Poll complete for {source.name}: '
        f'collected={stats["collected"]}, skipped={stats["skipped"]}, '
        f'failed={stats["failed"]}'
    )
    return stats
