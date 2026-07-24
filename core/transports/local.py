"""Local-directory transport: writes the rendered file to portal.directory."""
import os

from core.transports.base import Transport


class LocalTransport(Transport):
    def deliver(self, payload: bytes, filename: str, portal, context: dict = None) -> str:
        import os
        ctx = context or {}
        directory = portal.resolve_directory(
            operator=ctx.get('operator'),
            vendor=ctx.get('vendor'),
            network_element=ctx.get('network_element'),
        )
        os.makedirs(directory, exist_ok=True)
        dest = os.path.join(directory, filename)
        # Write to a unique temp file in the same dir, then atomically replace —
        # avoids partial files and reduces the window where a reader can lock the
        # final path. (Parallel workers each get their own temp name via pid.)
        tmp = os.path.join(directory, f'.{filename}.tmp-{os.getpid()}')
        with open(tmp, 'wb') as f:
            f.write(payload)
        os.replace(tmp, dest)
        return dest
