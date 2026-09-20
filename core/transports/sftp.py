"""SFTP transport: uploads the rendered file to portal.host:portal.directory."""
import io
import logging
import posixpath
import uuid

import paramiko

from core.transports.base import Transport

logger = logging.getLogger(__name__)


class SFTPTransport(Transport):
    def deliver(self, payload: bytes, filename: str, portal, context: dict = None) -> str:
        host = portal.host
        port = portal.port or 22
        username = portal.username
        password = portal.password
        ctx = context or {}
        remote_dir = str(portal.resolve_directory(
            operator=ctx.get('operator'),
            vendor=ctx.get('vendor'),
            network_element=ctx.get('network_element'),
            cbs_substream=ctx.get('cbs_substream'),
            downstream=ctx.get('downstream'),
            context='published',
        )).replace('\\', '/').rstrip('/') or '.'

        if not host or not username:
            raise ValueError(f'OutputPortal "{portal.name}" missing host/username')

        transport = paramiko.Transport((host, port))
        try:
            transport.connect(username=username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            try:
                if not filename or posixpath.basename(filename) != filename:
                    raise ValueError('Output filename must not contain a path')
                self._ensure_dir(sftp, remote_dir)
                staging_dir = f'{remote_dir}/staging'
                self._ensure_dir(sftp, staging_dir)
                staged_path = f'{staging_dir}/.{filename}.{uuid.uuid4().hex}.part'
                remote_path = f'{remote_dir}/{filename}'
                with sftp.open(staged_path, 'wb') as remote_f:
                    remote_f.write(payload)
                if sftp.stat(staged_path).st_size != len(payload):
                    raise IOError(f'Verification failed for {filename}')
                sftp.rename(staged_path, remote_path)
                logger.info(f'SFTP delivered {filename} to {host}:{remote_path} ({len(payload)} bytes)')
                return f'sftp://{host}{remote_path}'
            finally:
                sftp.close()
        finally:
            transport.close()

    @staticmethod
    def _ensure_dir(sftp, remote_dir: str) -> None:
        try:
            sftp.stat(remote_dir)
        except IOError:
            parts = remote_dir.split('/')
            cur = ''
            for p in parts:
                if not p:
                    cur = '/' if not cur else cur
                    continue
                cur = f'{cur}/{p}' if cur else p
                try:
                    sftp.stat(cur)
                except IOError:
                    sftp.mkdir(cur)
