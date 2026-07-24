"""Placeholder transports for FTP / API / DATABASE — phase 2."""
import logging

from core.transports.base import Transport

logger = logging.getLogger(__name__)


class UnsupportedTransport(Transport):
    """Logs a warning and raises — used until real implementations land."""

    def __init__(self, label: str):
        self.label = label

    def deliver(self, payload: bytes, filename: str, portal, context: dict = None) -> str:
        msg = f'{self.label} transport not implemented yet (portal={portal.name})'
        logger.warning(msg)
        raise NotImplementedError(msg)
