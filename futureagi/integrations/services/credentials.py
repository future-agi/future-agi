import base64
import json
import logging
import re
from typing import Any, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)

# Every Fernet token is urlsafe-base64 of (version byte 0x80 + 8-byte timestamp + …),
# which always renders to this prefix. Single source of truth — not re-spelled at call sites.
FERNET_PREFIX = "gAAAAA"


class CredentialManager:
    """Single interface for encrypting, decrypting, and masking secrets at rest.

    The ``encrypt``/``decrypt`` pair below is the raw Fernet layer. The
    ``*_secret`` / ``prepare_secret_for_storage`` / ``*_json`` methods are the
    typed, public secret contract that models, serializers, and migrations should
    consume so there is one encryption path in the codebase, not several:

      - detection verifies by decryption (never trusts the ``gAAAAA`` prefix),
      - encryption is idempotent and upgrades the legacy base64 format in place,
      - reads are dual-format (current Fernet + legacy base64),
      - an undecryptable ciphertext (rotated key) is logged, not silently dropped.
    """

    FERNET_PREFIX = FERNET_PREFIX

    @staticmethod
    def _get_fernet() -> Fernet:
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
        """Decrypt a Fernet-encrypted blob into a credentials dict."""
        fernet = cls._get_fernet()
        try:
            plaintext = fernet.decrypt(encrypted_blob)
            return json.loads(plaintext.decode("utf-8"))
        except InvalidToken:
            raise ValueError(
                "Failed to decrypt credentials. The encryption key may have changed."
            )

    # --- typed public secret contract (consumed by models + migrations) ---------

    @classmethod
    def _key_configured(cls) -> bool:
        return bool(getattr(settings, "INTEGRATION_ENCRYPTION_KEY", None))

    @staticmethod
    def _legacy_salt() -> bytes:
        return settings.SECRET_KEY[:16].encode()

    @classmethod
    def _legacy_encode(cls, plaintext: str) -> str:
        """The pre-Fernet at-rest format: base64(SECRET_KEY[:16] + plaintext)."""
        return base64.b64encode(cls._legacy_salt() + plaintext.encode()).decode()

    @classmethod
    def _is_legacy_encoded(cls, value: str) -> bool:
        try:
            return base64.b64decode(value, validate=True).startswith(cls._legacy_salt())
        except Exception:
            return False

    @classmethod
    def _legacy_decode(cls, value: str) -> str:
        return base64.b64decode(value)[16:].decode()

    @classmethod
    def is_fernet(cls, value: Any) -> bool:
        """True iff ``value`` is a Fernet token this key can decrypt. Verifies by
        decryption — a plaintext that merely starts with the prefix is rejected.
        With no key configured, falls back to the prefix so an existing token is
        not mistaken for plaintext and re-encoded."""
        if (
            not value
            or not isinstance(value, str)
            or not value.startswith(cls.FERNET_PREFIX)
        ):
            return False
        if not cls._key_configured():
            return True
        try:
            cls._get_fernet().decrypt(value.encode())
            return True
        except (InvalidToken, ValueError, TypeError):
            return False

    @classmethod
    def is_encrypted(cls, value: Any) -> bool:
        """True iff ``value`` is already stored (Fernet or legacy base64), not
        plaintext. Drives the encrypt-on-save decision."""
        if not value or not isinstance(value, str):
            return False
        return cls.is_fernet(value) or cls._is_legacy_encoded(value)

    @classmethod
    def encrypt_secret(cls, value: Optional[str]) -> Optional[str]:
        """Idempotently encrypt one plaintext secret for storage.

        Already-encrypted input is returned unchanged. With the key configured the
        result is a Fernet token; with the key absent the value is stored in the
        legacy reversible encoding (plus a warning) so saves never crash on an
        unconfigured deployment — dual-read keeps it readable and it upgrades to
        Fernet once the key is set."""
        if not value:
            return None
        if cls.is_encrypted(value):
            return value
        if not cls._key_configured():
            logger.warning(
                "INTEGRATION_ENCRYPTION_KEY is not set; storing secret with the legacy "
                "reversible encoding. Set the key and run migrate to upgrade secrets to "
                "Fernet at rest."
            )
            return cls._legacy_encode(value)
        return cls.encrypt({"v": value}).decode()

    @classmethod
    def decrypt_secret(cls, stored: Optional[str]) -> Optional[str]:
        """Dual-read a stored secret to plaintext. Returns ``None`` for empty input
        or a value that is neither Fernet nor legacy. A value that looks like a
        Fernet token but will not decrypt (rotated / changed key) is logged without
        the secret and returns ``None`` — so a rotation failure is visible rather
        than silently becoming an empty key."""
        if not stored:
            return None
        stored = str(stored)
        if stored.startswith(cls.FERNET_PREFIX):
            try:
                return cls.decrypt(stored.encode())["v"]
            except (ValueError, KeyError) as exc:
                logger.warning(
                    "Undecryptable Fernet secret (key rotated or "
                    "INTEGRATION_ENCRYPTION_KEY changed?): %s",
                    exc,
                )
                return None
        if cls._is_legacy_encoded(stored):
            return cls._legacy_decode(stored)
        return None

    @classmethod
    def prepare_secret_for_storage(
        cls, value: Optional[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        """Canonicalize one secret for at-rest storage. Returns
        ``(stored, plaintext)``:

          plaintext     -> (Fernet token, plaintext)
          legacy base64 -> (Fernet token, plaintext)   # upgraded in place
          Fernet token  -> (unchanged token, plaintext)  # no re-encrypt churn
          empty / None  -> (None, None)
        """
        if not value:
            return None, None
        if cls.is_fernet(value):
            return value, cls.decrypt_secret(value)
        plaintext = cls.decrypt_secret(value)
        if plaintext is None:
            plaintext = value  # raw plaintext, not a recognized ciphertext
        return cls.encrypt_secret(plaintext), plaintext

    @classmethod
    def reencrypt_value(cls, value: Any) -> Tuple[Any, bool]:
        """Recursively upgrade legacy-encoded secrets to Fernet from the recovered
        plaintext (never by re-wrapping the stored blob). Mirrors the model's
        recursive encrypt. Returns ``(new_value, changed)`` so a row is only written
        when something actually upgraded; Fernet values and non-secret types pass
        through untouched."""
        if isinstance(value, dict):
            out, changed = {}, False
            for k, v in value.items():
                nv, ch = cls.reencrypt_value(v)
                out[k] = nv
                changed = changed or ch
            return out, changed
        if isinstance(value, list):
            out, changed = [], False
            for item in value:
                nv, ch = cls.reencrypt_value(item)
                out.append(nv)
                changed = changed or ch
            return out, changed
        if (
            isinstance(value, str)
            and not value.startswith(cls.FERNET_PREFIX)
            and cls._is_legacy_encoded(value)
        ):
            plain = cls._legacy_decode(value)
            return cls.encrypt_secret(plain), True
        return value, False

    @classmethod
    def encrypt_json(cls, data: Any) -> Any:
        """Recursively encrypt every string secret in a JSON-able structure."""
        if isinstance(data, dict):
            return {k: cls.encrypt_json(v) for k, v in data.items()}
        if isinstance(data, list):
            return [cls.encrypt_json(v) for v in data]
        if isinstance(data, str):
            return cls.prepare_secret_for_storage(data)[0]
        return data

    @classmethod
    def decrypt_json(cls, data: Any) -> Any:
        """Recursively decrypt a JSON-able structure; non-secret strings pass through."""
        if isinstance(data, dict):
            return {k: cls.decrypt_json(v) for k, v in data.items()}
        if isinstance(data, list):
            return [cls.decrypt_json(v) for v in data]
        if isinstance(data, str):
            plain = cls.decrypt_secret(data)
            return plain if plain is not None else data
        return data

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
