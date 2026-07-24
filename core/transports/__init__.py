"""Registry of OutputPortal transports keyed by portal_type."""
from core.transports.base import Transport
from core.transports.local import LocalTransport
from core.transports.sftp import SFTPTransport
from core.transports.stub import UnsupportedTransport


_REGISTRY = {
    'LOCAL': LocalTransport(),
    'SFTP': SFTPTransport(),
    'FTP': UnsupportedTransport('FTP'),
    'API': UnsupportedTransport('API'),
    'DATABASE': UnsupportedTransport('DATABASE'),
}


def get_transport(portal_type: str) -> Transport:
    """Return the transport for a given portal_type, or UnsupportedTransport."""
    return _REGISTRY.get(portal_type, UnsupportedTransport(portal_type or 'UNKNOWN'))


__all__ = ['Transport', 'get_transport']
