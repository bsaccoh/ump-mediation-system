"""SFTP transport: uploads the rendered file to portal.host:portal.directory."""
import io
import logging

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
        remote_dir = (portal.directory or '.').rstrip('/') or '.'
        # Honour the per-operator placeholders on remote paths too.
        remote_dir = (remote_dir
                      .replace('{operator}', (ctx.get('operator') or 'unknown').lower())
                      .replace('{vendor}', (ctx.get('vendor') or 'unknown').lower())
                      .replace('{ne}', (ctx.get('network_element') or '').lower()))

        if not host or not username:
            raise ValueError(f'OutputPortal "{portal.name}" missing host/username')

        transport = paramiko.Transport((host, port))
        try:
            transport.connect(username=username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            try:
                self._ensure_dir(sftp, remote_dir)
                remote_path = f'{remote_dir}/{filename}'
                with sftp.open(remote_path, 'wb') as remote_f:
                    remote_f.write(payload)
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
