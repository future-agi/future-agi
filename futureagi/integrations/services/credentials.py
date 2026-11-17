import json
import re

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

# What a connection whose saved credentials cannot be read says, on its reads
# and on each request that needs those credentials.
UNREADABLE_CREDENTIALS = (
    "This connection's saved credentials can't be read. Reconnect it to use it again."
)


class UnreadableCredentials(ValueError):
    """Saved credentials that do not open with the configured key.

    A ValueError, so every caller that handles a failed decrypt keeps doing so.
    """


class CredentialManager:
    """Manages encryption, decryption, and masking of integration credentials."""

    @staticmethod
    def _get_fernet():
        key = settings.INTEGRATION_ENCRYPTION_KEY
        if not key:
            raise ValueError(
                "INTEGRATION_ENCRYPTION_KEY is not set. "
                "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        return Fernet(key.encode() if isinstance(key, str) else key)

    @classmethod
    def encrypt(cls, credentials: dict) -> bytes:
        """Encrypt a credentials dict into a Fernet-encrypted blob."""
        fernet = cls._get_fernet()
        plaintext = json.dumps(credentials).encode("utf-8")
        return fernet.encrypt(plaintext)

    @classmethod
    def decrypt(cls, encrypted_blob: bytes) -> dict:
        """Decrypt a Fernet-encrypted blob into a credentials dict.

        Raises UnreadableCredentials when the blob does not open with the
        configured key.
        """
        fernet = cls._get_fernet()
        try:
            plaintext = fernet.decrypt(encrypted_blob)
            return json.loads(plaintext.decode("utf-8"))
        except InvalidToken as exc:
            raise UnreadableCredentials(
                "Failed to decrypt credentials. The encryption key may have changed."
            ) from exc

    @classmethod
    def unreadable(cls, encrypted_blob) -> bool:
        """Whether saved credentials do not open with the configured key.

        Only that outcome counts: any other failure (no key configured, or
        contents that are not JSON) reads as False.
        """
        try:
            cls.decrypt(bytes(encrypted_blob))
        except UnreadableCredentials:
            return True
        except Exception:
            return False
        return False

    @staticmethod
    def mask_key(key_str: str) -> str:
        """Mask a key string, showing only the last 4 characters.

        Examples:
            "sk-lf-abcdef1234" -> "sk-lf-****1234"
            "pk-lf-abcdef1234" -> "pk-lf-****1234"
            "short" -> "****ort"
        """
        if not key_str:
            return ""
        # Find prefix pattern like "sk-lf-" or "pk-lf-"
        prefix_match = re.match(r"^([a-z]+-[a-z]+-)", key_str)
        if prefix_match:
            prefix = prefix_match.group(1)
            rest = key_str[len(prefix) :]
            visible = rest[-4:] if len(rest) >= 4 else rest
            return f"{prefix}****{visible}"
        # Fallback: show last 4 chars
        visible = key_str[-4:] if len(key_str) >= 4 else key_str
        return f"****{visible}"
