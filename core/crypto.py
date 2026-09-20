"""
Simple reversible encryption for sensitive fields (SFTP passwords, API keys).

Uses Fernet symmetric encryption keyed from Django's SECRET_KEY.
Falls back to base64 obfuscation if cryptography is not installed.
"""
import base64
import hashlib

from django.conf import settings

_PREFIX = 'enc::'

try:
    from cryptography.fernet import Fernet, InvalidToken

    def _fernet():
        key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(key))

    def encrypt_value(plaintext: str) -> str:
        if not plaintext:
            return plaintext
        return _PREFIX + _fernet().encrypt(plaintext.encode()).decode()

    def decrypt_value(stored: str) -> str:
        if not stored:
            return stored
        if not stored.startswith(_PREFIX):
            return stored
        try:
            return _fernet().decrypt(stored[len(_PREFIX):].encode()).decode()
        except (InvalidToken, Exception):
            return stored

except ImportError:
    def encrypt_value(plaintext: str) -> str:
        if not plaintext:
            return plaintext
        encoded = base64.b64encode(plaintext.encode()).decode()
        return _PREFIX + encoded

    def decrypt_value(stored: str) -> str:
        if not stored:
            return stored
        if not stored.startswith(_PREFIX):
            return stored
        try:
            return base64.b64decode(stored[len(_PREFIX):].encode()).decode()
        except Exception:
            return stored


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(_PREFIX)
