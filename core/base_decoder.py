"""
Base Decoder
=============
Abstract base class for all CDR binary decoders.
Decoders are pure Python - no Django imports. They take a file path
and return a list of dicts.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Tuple, Optional


class BaseDecoder(ABC):
    """Abstract base for CDR binary file decoders.

    Subclasses must implement:
        - decode_file(file_path) -> List[dict]
        - is_binary(file_path) -> bool

    Decoders should have ZERO Django/ORM dependencies so they can be
    tested and used from CLI scripts independently.
    """

    @abstractmethod
    def decode_file(self, file_path: str) -> Tuple[bool, str, int]:
        """Decode a binary CDR file.

        Args:
            file_path: Absolute path to the binary file.

        Returns:
            Tuple of (success, output_path_or_error, record_count)
        """
        ...

    @staticmethod
    @abstractmethod
    def is_binary(file_path: str) -> bool:
        """Check if a file is a binary CDR that this decoder can handle."""
        ...

    @staticmethod
    def read_file_bytes(file_path: str) -> bytes:
        """Read entire file as bytes."""
        with open(file_path, 'rb') as f:
            return f.read()
