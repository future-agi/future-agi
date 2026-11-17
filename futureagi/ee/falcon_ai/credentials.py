"""Falcon-only credential boundary; shared legacy readers remain unchanged."""

from django.conf import settings

from agentcc.services.credential_manager import (
    ENCRYPTED_PREFIX,
    decrypt_token,
    encrypt_token,
)
from integrations.services.credentials import CredentialManager

AUTH_REQUIRED = "CONNECTOR_AUTH_REQUIRED"


class FalconCredentialError(RuntimeError):
    """A connector credential cannot be safely stored or read."""


def require_persistent_key():
    """Validate Falcon's key policy without changing shared helper semantics."""
    try:
        if getattr(settings, "INTEGRATION_ENCRYPTION_KEY_EPHEMERAL", False):
            raise ValueError
        # Constructing Fernet validates missing/malformed configured keys. Never
        # include the shared helper's exception text or the key in our error.
        CredentialManager._get_fernet()
    except Exception:
        raise FalconCredentialError("Connector credential key unavailable") from None


def encrypt_falcon_token(value: str) -> str:
    """Encrypt plaintext, including plaintext that happens to start with enc::.

    Unlike the shared idempotent helper, this function takes *plaintext only*.
    It is called at provider/input boundaries, never on stored ciphertext.
    """
    require_persistent_key()
    if not value:
        return ""
    try:
        if value.startswith(ENCRYPTED_PREFIX):
            # The shared helper deliberately treats this prefix as already
            # encrypted. Provider tokens and user input have no such guarantee.
            blob = CredentialManager.encrypt({"v": value})
            return ENCRYPTED_PREFIX + blob.decode("utf-8")
        return encrypt_token(value)
    except Exception:
        raise FalconCredentialError("Connector credential encryption failed") from None


def decrypt_falcon_token(value: str) -> str:
    """Read encrypted storage only; an empty optional credential stays empty."""
    require_persistent_key()
    if not value:
        return ""
    if not value.startswith(ENCRYPTED_PREFIX):
        raise FalconCredentialError("Connector credential requires encrypted storage")
    try:
        plaintext = decrypt_token(value)
        if not isinstance(plaintext, str) or not plaintext:
            raise ValueError
        return plaintext
    except Exception:
        raise FalconCredentialError("Connector credential decryption failed") from None
