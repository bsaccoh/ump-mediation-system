"""Transport ABC for delivering rendered output to a downstream OutputPortal."""
from abc import ABC, abstractmethod


class Transport(ABC):
    """Abstract delivery transport. Concrete subclasses implement .deliver()."""

    @abstractmethod
    def deliver(self, payload: bytes, filename: str, portal, context: dict = None) -> str:
        """Send `payload` to `portal` under `filename`.

        `context` carries per-file routing hints (operator, vendor,
        network_element) used to resolve the per-operator output directory.

        Returns the destination path or URI on success.
        Raise on failure — the dispatcher catches and logs to DistributionLog.
        """
        ...
