"""AgentDefinition's encrypted-only credential boundary.

The wire format and deployment key are the existing ProviderCredentials format.
Unlike encrypt_token, new client input is ALWAYS encrypted, including enc::.
Keep this small codec stable: the fenced historical migration also uses it.
"""

from django.conf import settings

from integrations.services.credentials import CredentialManager

PREFIX = "enc::"
ERROR = "Agent credential storage is unavailable"
KEY_ERROR = (
    "Agent credentials require a valid persistent INTEGRATION_ENCRYPTION_KEY; "
    "configure the same key for all processes and restarts (not an ephemeral key)"
)


def require_persistent_key():
    """Mirror Falcon's policy without importing an EE dependency into Simulate."""
    try:
        if getattr(settings, "INTEGRATION_ENCRYPTION_KEY_EPHEMERAL", False):
            raise ValueError
        # Validates missing/malformed keys without exposing crypto diagnostics.
        CredentialManager._get_fernet()
    except Exception:
        raise ValueError(KEY_ERROR) from None


def encrypt_api_key(value: str | None) -> str | None:
    require_persistent_key()
    if value is None or value == "":
        return None
    try:
        if not isinstance(value, str):
            raise ValueError
        encrypted = PREFIX + CredentialManager.encrypt({"v": value}).decode("utf-8")
        if decrypt_api_key(encrypted) != value:
            raise ValueError
        return encrypted
    except Exception:
        # Neither ciphertext, client input nor crypto-library diagnostics escape.
        raise ValueError(ERROR) from None


def decrypt_api_key(value: str | None) -> str:
    if value is None or value == "":
        return ""
    try:
        if not isinstance(value, str) or not value.startswith(PREFIX):
            raise ValueError
        payload = CredentialManager.decrypt(value[len(PREFIX) :].encode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("v"), str):
            raise ValueError
        return payload["v"]
    except Exception:
        raise ValueError(ERROR) from None
