"""Encryption at rest for secrets, URLs and header values (DESIGN.md section 5.3, K2).

``WEBHOOKS_ENCRYPTION_KEYS`` is a comma-separated list of Fernet keys read from the secret store: the first
encrypts, all decrypt. With no key configured nothing is written or sent (the hosted acceptance gate). To rotate, put
the new key first, run ``manage.py webhooks_reencrypt`` (``webhooks.reencrypt``) until it reports 0 rows left on the
older keys, then remove them from the list.

FA's Sentry captures the variables of every frame, both for an error and for the stack it attaches to an error log
line, and scrubs them by name only. So:

- no frame that raises or logs here holds key material: the keys are parsed in :func:`_load`, which returns either
  the cipher or the index of a bad entry, and the error is logged and raised by :func:`_refuse`, whose frame holds
  neither;
- :func:`encrypt` and :func:`encrypt_json` check the keys before anything else, and drop the plaintext from their
  frame before they refuse, so neither that log line's stack nor the error's frames hold it. A value that cannot be
  encoded is refused the same way. Their arguments are also named ``secret_*``, so a name-based scrubber redacts
  them in any other case;
- no frame of this module holds a ciphertext when anything here logs or raises (its name, ``blob``, is not one a
  scrubber matches). :func:`decrypt`, :func:`rotate` and :func:`on_newest_key` check the keys before they touch the
  ciphertext and drop it from their frame before they refuse; otherwise they move it into a one-item list and hand
  it to the cipher straight out of that list, so their frame holds the empty list while the cipher runs and when a
  ciphertext that did not decrypt is logged (WA6). The cipher's own frames, which do hold it, are cleared from that
  error before the log line. :func:`decrypt_json` hands its ciphertext to :func:`decrypt` the same way.
"""

from __future__ import annotations

import json
import logging
import os
from typing import NoReturn

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from webhooks.core.redact import clear_exception_frames

log = logging.getLogger("webhooks.crypto")


class KeysMissing(RuntimeError):
    """No WEBHOOKS_ENCRYPTION_KEYS: webhook secrets and URLs cannot be stored or read."""


class KeysInvalid(RuntimeError):
    """An entry of WEBHOOKS_ENCRYPTION_KEYS is not a Fernet key. The message names the entry's position only."""


def _load(newest_only: bool = False) -> MultiFernet | int | None:
    """The cipher; or the 0-based index of the first entry that is not a Fernet key; or None when none is set.
    ``newest_only``: a cipher of the first entry alone (what decrypts with it is already on the newest key)."""
    secret_entries = [
        e.strip()
        for e in os.environ.get("WEBHOOKS_ENCRYPTION_KEYS", "").split(",")
        if e.strip()
    ]
    if not secret_entries:
        return None
    secret_ciphers = []
    for i, secret_entry in enumerate(secret_entries):
        try:
            secret_ciphers.append(Fernet(secret_entry.encode("ascii")))
        except Exception:  # noqa: BLE001 - ValueError, binascii.Error, UnicodeEncodeError
            return i
    return MultiFernet(secret_ciphers[:1] if newest_only else secret_ciphers)


def _refuse(cipher: int | None) -> NoReturn:
    """Raise for what :func:`_load` returned when it built no cipher; a malformed key is also logged (WA6)."""
    if cipher is None:
        raise KeysMissing("WEBHOOKS_ENCRYPTION_KEYS is not set")
    log.error(
        "WEBHOOKS_ENCRYPTION_KEYS has an entry that is not a Fernet key",
        extra={"metric": "webhooks.keys_invalid", "entry": cipher, "alert": "WA6"},
    )
    raise KeysInvalid(
        f"WEBHOOKS_ENCRYPTION_KEYS entry {cipher} (0-based) is not a Fernet key"
    )


def _fernet() -> MultiFernet:
    cipher = _load()
    if not isinstance(cipher, MultiFernet):
        _refuse(cipher)
    return cipher


def require_keys() -> None:
    """The K2 gate as a public check: raises :class:`KeysMissing` when ``WEBHOOKS_ENCRYPTION_KEYS`` is not set and
    :class:`KeysInvalid` (logged, WA6) when an entry is not a Fernet key. It is the check :func:`encrypt` makes
    first; the API calls it before it handles any URL, secret or header value from a request, so a refusal never has
    plaintext in a frame that logs."""
    _fernet()


def keys_ready() -> bool:
    """Whether a secret could be written now: at least one key, and every entry a Fernet key. Nothing is logged."""
    return isinstance(_load(), MultiFernet)


def encrypt(secret_text: str) -> bytes:
    cipher = _load()
    if not isinstance(cipher, MultiFernet):
        del secret_text  # K2: before the refusal logs and raises (module docstring)
        _refuse(cipher)
    try:
        secret_bytes = secret_text.encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate. The error's repr quotes the whole text, so it is not chained in.
        del secret_text
        raise ValueError("the text to encrypt is not valid Unicode") from None
    return cipher.encrypt(secret_bytes)


def encrypt_json(secret_value) -> bytes:
    cipher = _load()
    if not isinstance(cipher, MultiFernet):
        del secret_value  # K2: before the refusal logs and raises (module docstring)
        _refuse(cipher)
    try:
        secret_bytes = json.dumps(
            secret_value, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        # json's own frames hold the value (as ``o``), and a UnicodeEncodeError's repr quotes the whole text.
        error_type = type(exc).__name__
        clear_exception_frames(exc)
        del exc, secret_value
        raise ValueError(
            f"the value to encrypt is not JSON text ({error_type})"
        ) from None
    return cipher.encrypt(secret_bytes)


def decrypt(blob: bytes | memoryview | None) -> str | None:
    if blob is None:
        return None
    cipher = _load()
    if not isinstance(cipher, MultiFernet):
        del blob  # K2: before the refusal logs and raises (module docstring)
        _refuse(cipher)
    secret_blob = [blob]
    del blob  # K2: the cipher takes it out of the list (module docstring)
    try:
        return cipher.decrypt(bytes(secret_blob.pop())).decode("utf-8")
    except InvalidToken as exc:
        clear_exception_frames(exc)  # the cipher's frames hold the ciphertext
        del exc
        log.error(
            "webhook ciphertext did not decrypt",
            extra={"metric": "webhooks.decrypt_failed", "alert": "WA6"},
        )
        raise InvalidToken() from None


def decrypt_json(blob):
    secret_blob = [blob]
    del blob  # K2: decrypt may log; this frame holds an empty list by then
    secret_text = decrypt(secret_blob.pop())
    return None if secret_text is None else json.loads(secret_text)


# ---- WH-1b: rotation (webhooks.reencrypt) ------------------------------------------------------------------------


def on_newest_key(blob: bytes | memoryview) -> bool:
    """Whether the newest key alone decrypts ``blob``."""
    cipher = _load(newest_only=True)
    if not isinstance(cipher, MultiFernet):
        del blob  # K2: before the refusal logs and raises (module docstring)
        _refuse(cipher)
    secret_blob = [blob]
    del blob  # K2: the cipher takes it out of the list (module docstring)
    try:
        cipher.decrypt(bytes(secret_blob.pop()))
        return True
    except InvalidToken:
        return False


def rotate(blob: bytes | memoryview) -> bytes:
    """The same plaintext re-encrypted under the newest key. Raises ``InvalidToken`` (logged, WA6) when no key of the
    list decrypts it; the plaintext never leaves ``MultiFernet.rotate``'s frame, and no frame here holds ``blob`` when
    anything logs (module docstring)."""
    cipher = _load()
    if not isinstance(cipher, MultiFernet):
        del blob  # K2: before the refusal logs and raises (module docstring)
        _refuse(cipher)
    secret_blob = [blob]
    del blob  # K2: the cipher takes it out of the list (module docstring)
    try:
        return cipher.rotate(bytes(secret_blob.pop()))
    except InvalidToken as exc:
        clear_exception_frames(exc)  # the cipher's frames hold the ciphertext
        del exc
        log.error(
            "webhook ciphertext did not decrypt",
            extra={"metric": "webhooks.decrypt_failed", "alert": "WA6"},
        )
        raise InvalidToken() from None
