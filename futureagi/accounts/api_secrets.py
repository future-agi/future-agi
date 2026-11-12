"""API secrets at rest: a keyed hash to verify them, never the plaintext.

READINESS L03 (PLT-07): a Future AGI API secret is stored as HMAC-SHA256 under a
server-side pepper, the key is looked up by its public ``api_key``, and the
presented secret is compared in constant time.

The stored hash is ``hmac-sha256$<pepper id>$<hex digest>``. The pepper id is a
one-way fingerprint of the pepper, so a hash written under a retired pepper still
verifies (and is re-hashed under the current one on its next successful use).
fi-collector verifies the same string (``fi-collector/pkg/auth/secrethash.go``);
the golden vector in both test suites keeps the two in step.

System keys are the platform's own credential: it presents them to fi-collector,
the agentcc gateway and its websocket relay, and the Keys page shows them. An
agent's write-only ``ingest`` key is handed by the platform to every launch of
that agent's sandbox. Both keep a Fernet-encrypted copy as well
(``seal(..., recoverable=True)``; ``api_key_lookup.RECOVERABLE_TYPES``). User and
MCP keys keep no recoverable copy; their secret is shown once, when created.

This module is pure: no ORM. ``accounts.api_key_lookup`` does the database side.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

HASH_SCHEME = "hmac-sha256"
# The plaintext secret a presented key may not exceed (the column was 255 wide).
MAX_SECRET_LENGTH = 255
MIN_PEPPER_LENGTH = 32
# The four columns ``seal`` writes; a save that names update_fields must include them.
SEALED_FIELDS = (
    "secret_key",
    "secret_key_hash",
    "secret_key_hint",
    "secret_key_ciphertext",
)
REVOCATION_PREFIX = "apikey:"

_LOCAL_ENV_TYPES = ("local", "test")
_PEPPER_ID_LABEL = b"futureagi/api-secret-pepper-id/v1\x00"
_LOCAL_PEPPER_LABEL = b"futureagi/api-secret-pepper/local-v1"
_LOCAL_FERNET_LABEL = b"futureagi/api-secret-fernet/local-v1"


class ApiSecretConfigError(ImproperlyConfigured):
    """A pepper or an encryption key the secret store must provide is missing."""


class ApiSecretNotRecoverable(Exception):
    """The key keeps no recoverable secret (a user or MCP key, or a hash-only ingest key), or it cannot be decrypted."""


@dataclass(frozen=True)
class Pepper:
    id: str
    key: bytes


@dataclass(frozen=True)
class Verdict:
    ok: bool
    # True when the row should be re-sealed: a legacy plaintext row, or a hash
    # written under a retired pepper.
    needs_rehash: bool = False


def _env_type() -> str:
    return str(getattr(settings, "ENV_TYPE", os.getenv("ENV_TYPE", "local"))).lower()


def _secret_key_bytes() -> bytes:
    value = getattr(settings, "SECRET_KEY", "")
    return value if isinstance(value, bytes) else str(value).encode("utf-8")


def pepper_id(key: bytes) -> str:
    """The first 8 hex characters of a one-way fingerprint of the pepper."""
    return hashlib.sha256(_PEPPER_ID_LABEL + key).hexdigest()[:8]


def local_pepper_from_secret_key(secret_key: bytes) -> bytes:
    """The pepper local and test installs derive when none is configured."""
    return (
        hmac.new(secret_key, _LOCAL_PEPPER_LABEL, hashlib.sha256).hexdigest().encode()
    )


@lru_cache(maxsize=8)
def _resolve_peppers(
    configured: str, retired: tuple[str, ...], env_type: str, secret_key: bytes
) -> tuple[Pepper, dict[str, Pepper]]:
    if configured:
        if len(configured) < MIN_PEPPER_LENGTH:
            raise ApiSecretConfigError(
                f"FI_API_SECRET_PEPPER must be at least {MIN_PEPPER_LENGTH} characters."
            )
        current_key = configured.encode("utf-8")
    elif env_type in _LOCAL_ENV_TYPES:
        current_key = local_pepper_from_secret_key(secret_key)
    else:
        raise ApiSecretConfigError(
            "FI_API_SECRET_PEPPER is required outside local and test environments: "
            "API secrets are stored as a keyed hash under it."
        )
    current = Pepper(pepper_id(current_key), current_key)
    known = {current.id: current}
    for raw in retired:
        if raw:
            old = Pepper(pepper_id(raw.encode("utf-8")), raw.encode("utf-8"))
            known.setdefault(old.id, old)
    return current, known


def peppers() -> tuple[Pepper, dict[str, Pepper]]:
    """(current pepper, every pepper a stored hash may name, by id)."""
    retired = getattr(settings, "API_SECRET_PEPPERS_RETIRED", ()) or ()
    if isinstance(retired, str):
        retired = [part.strip() for part in retired.split(",")]
    return _resolve_peppers(
        str(getattr(settings, "API_SECRET_PEPPER", "") or ""),
        tuple(retired),
        _env_type(),
        _secret_key_bytes(),
    )


def _digest(pepper: Pepper, secret: str) -> bytes:
    return (
        hmac.new(pepper.key, secret.encode("utf-8"), hashlib.sha256)
        .hexdigest()
        .encode()
    )


def hash_secret(secret: str, pepper: Pepper | None = None) -> str:
    pepper = pepper or peppers()[0]
    return f"{HASH_SCHEME}${pepper.id}${_digest(pepper, secret).decode()}"


def _parse(stored_hash: str) -> tuple[str, bytes] | None:
    parts = stored_hash.split("$")
    if len(parts) != 3 or parts[0] != HASH_SCHEME or len(parts[2]) != 64:
        return None
    return parts[1], parts[2].encode("utf-8", "replace")


_ABSENT_DIGEST = b"0" * 64
# Parsed in place of a missing hash, so every path does the same work. Its pepper
# id ("-" is not hex) never names a real pepper, so it can never verify.
_ABSENT_HASH = f"{HASH_SCHEME}$--------${_ABSENT_DIGEST.decode()}"


def verify_secret(stored_hash: str, legacy_plaintext: str, presented: str) -> Verdict:
    """Constant-time check of a presented secret against one stored row.

    Every path parses one hash, looks up one pepper, computes one HMAC and does
    one ``compare_digest``, so an unknown key, a wrong secret and a right one cost
    the same. ``stored_hash`` and ``legacy_plaintext`` are both empty for "no such key".
    """
    current, known = peppers()
    parsed = _parse(stored_hash or _ABSENT_HASH)
    pid, expected = parsed if parsed is not None else ("", _ABSENT_DIGEST)
    pepper = known.get(pid)
    candidate = _digest(pepper or current, presented)
    if legacy_plaintext and not stored_hash:
        # A row not sealed yet (before migration 0027, or written by a pod of
        # the previous release): compare the plaintext instead.
        ok = hmac.compare_digest(
            legacy_plaintext.encode("utf-8"), presented.encode("utf-8")
        )
        return Verdict(ok, ok)
    ok = (
        hmac.compare_digest(candidate, expected)
        and pepper is not None
        and parsed is not None
        and bool(stored_hash)
    )
    return Verdict(ok, ok and pid != current.id)


def stored_plaintext(stored_hash: str, plaintext: str) -> str:
    """The plaintext a stored row may be trusted to hold, or ``""``.

    Once a row has a hash, the hash is the secret's record: a plaintext beside it
    counts only if it verifies against it. A pod of the previous release that
    saves a sealed row regenerates its empty ``secret_key`` and so writes a fresh
    random value beside the real hash; that value is no secret of the key and is
    never sealed, presented or revealed. With no hash, the plaintext is a legacy
    secret (a row migration 0027 has not sealed yet, or one an old pod created).
    """
    if not plaintext or not stored_hash:
        return plaintext
    return plaintext if verify_secret(stored_hash, "", plaintext).ok else ""


def secret_hint(secret: str) -> str:
    """What the Keys table shows: the first and last four characters.

    The same string ``model_hub.models.api_key.mask_key`` gives (a test pins it);
    computed here so accounts does not import model_hub's models.
    """
    if len(secret) <= 8:
        visible = min(4, len(secret))
        return secret[:visible] + "*" * max(len(secret) - visible, 4)
    return secret[:4] + "*" * min(max(len(secret) - 8, 0), 10) + secret[-4:]


def _configured_fernet() -> Fernet | None:
    key = getattr(settings, "API_SECRET_ENCRYPTION_KEY", "") or ""
    if not key:
        return None
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except ValueError as exc:
        raise ApiSecretConfigError(
            "INTEGRATION_ENCRYPTION_KEY is not a Fernet key (32 random bytes, "
            "URL-safe base64): system API keys are stored encrypted under it."
        ) from exc


def _derived_fernet() -> Fernet:
    """The key a local or test install without INTEGRATION_ENCRYPTION_KEY uses."""
    derived = hmac.new(_secret_key_bytes(), _LOCAL_FERNET_LABEL, hashlib.sha256)
    return Fernet(base64.urlsafe_b64encode(derived.digest()))


def _fernet() -> Fernet:
    """The key a new ciphertext is written under."""
    configured = _configured_fernet()
    if configured is not None:
        return configured
    if _env_type() in _LOCAL_ENV_TYPES:
        return _derived_fernet()
    raise ApiSecretConfigError(
        "INTEGRATION_ENCRYPTION_KEY is required outside local and test environments: "
        "system API keys are stored encrypted under it."
    )


def _decryption_keys() -> MultiFernet:
    """Every key a stored ciphertext may be under: the configured one, then the derived.

    A local install that reached migration 0027 without INTEGRATION_ENCRYPTION_KEY
    sealed its system keys under the key derived from SECRET_KEY; ``./bin/install``
    fills the variable on existing installs later (bin/lib/secrets.sh), and those
    keys must still decrypt. ``manage.py api_secrets backfill`` then moves them
    under the configured key (``reencrypt_under_configured_key``).
    """
    configured = _configured_fernet()
    keys = [configured] if configured is not None else []
    return MultiFernet([*keys, _derived_fernet()])


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        return _decryption_keys().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ApiSecretNotRecoverable(
            "The system API key cannot be decrypted: INTEGRATION_ENCRYPTION_KEY changed "
            "(or SECRET_KEY, for a key a local install sealed before it was set)."
        ) from exc


CIPHERTEXT_CONFIGURED = "configured"
CIPHERTEXT_DERIVED = "derived"
CIPHERTEXT_UNDECRYPTABLE = "undecryptable"


def _opens(fernet: Fernet | None, token: str) -> bytes | None:
    if fernet is None:
        return None
    try:
        return fernet.decrypt(token.encode("ascii"))
    except (InvalidToken, ValueError):
        return None


def ciphertext_key(token: str) -> str:
    """Which key a stored ciphertext opens under (one of the ``CIPHERTEXT_*`` names).

    With no INTEGRATION_ENCRYPTION_KEY configured, the derived key is the one new
    ciphertexts are written under, so ``derived`` is then the expected answer.
    """
    if _opens(_configured_fernet(), token) is not None:
        return CIPHERTEXT_CONFIGURED
    if _opens(_derived_fernet(), token) is not None:
        return CIPHERTEXT_DERIVED
    return CIPHERTEXT_UNDECRYPTABLE


def reencrypt_under_configured_key(token: str) -> str | None:
    """The ciphertext under INTEGRATION_ENCRYPTION_KEY of a token sealed under the derived key.

    ``None`` when there is nothing to move: no key is configured, the token is
    already under it, or it opens under neither (``decrypt_secret`` reports that).
    """
    configured = _configured_fernet()
    if configured is None or _opens(configured, token) is not None:
        return None
    plaintext = _opens(_derived_fernet(), token)
    if plaintext is None:
        return None
    return configured.encrypt(plaintext).decode("ascii")


def seal(secret: str, *, recoverable: bool) -> dict[str, str]:
    """The column values that store ``secret``: never the plaintext itself."""
    return {
        "secret_key": "",
        "secret_key_hash": hash_secret(secret),
        "secret_key_hint": secret_hint(secret),
        "secret_key_ciphertext": encrypt_secret(secret) if recoverable else "",
    }


def revocation_payloads(api_key: str, plaintext: str | None = None) -> list[str]:
    """What Django publishes on ``fi:auth:revoke`` when a key is disabled or deleted.

    ``apikey:<sha256(api_key)>`` lets fi-collector evict every cached entry of the
    key without knowing its secret. Collectors released before this change only
    understand ``sha256(api_key:secret)``, sent too while the plaintext is known.
    """
    payloads = [REVOCATION_PREFIX + hashlib.sha256(api_key.encode("utf-8")).hexdigest()]
    if plaintext:
        payloads.append(hashlib.sha256(f"{api_key}:{plaintext}".encode()).hexdigest())
    return payloads
