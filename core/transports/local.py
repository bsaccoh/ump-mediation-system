"""Local-directory transport with verified staging and publication."""

from core.transports.base import Transport
from collection.services.transfer import publish_bytes


class LocalTransport(Transport):
    def deliver(self, payload: bytes, filename: str, portal, context: dict = None) -> str:
        ctx = context or {}
        published_directory = portal.resolve_directory(
            operator=ctx.get('operator'),
            vendor=ctx.get('vendor'),
            network_element=ctx.get('network_element'),
            cbs_substream=ctx.get('cbs_substream'),
            downstream=ctx.get('downstream'),
            context='published',
        )
        staging_directory = portal.resolve_directory(
            operator=ctx.get('operator'),
            vendor=ctx.get('vendor'),
            network_element=ctx.get('network_element'),
            cbs_substream=ctx.get('cbs_substream'),
            downstream=ctx.get('downstream'),
            context='staging',
        )
        # Write to a unique temp file in the same dir, then atomically replace —
        # avoids partial files and reduces the window where a reader can lock the
        # final path. (Parallel workers each get their own temp name via pid.)
        return str(publish_bytes(payload, staging_directory, published_directory, filename))
