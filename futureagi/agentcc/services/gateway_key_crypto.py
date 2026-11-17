"""Strict, explicit offline Fernet transformation; no shared runtime changes.

Never treats unreadable input as plaintext. Re-encrypt the exact authenticated
bytes (including JSON formatting), so stored plaintext semantics do not change.
"""

import base64
import json

from cryptography.fernet import Fernet, InvalidToken
from django.core.management.base import CommandError

PREFIX = "enc::"


def _fernet(value):
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
        if len(decoded) != 32 or base64.urlsafe_b64encode(decoded) != encoded:
            raise ValueError
        return Fernet(encoded)
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise CommandError(
            "Gateway transition requires canonical Fernet key inputs"
        ) from None


class KeyTransition:
    def __init__(self, source, target):
        self.target = _fernet(target)
        self.source = _fernet(source) if source is not None else None
        if source == target:
            raise CommandError("Gateway source and target key inputs must differ")

    def verifier(self):
        from copy import copy

        verifier = copy(self)
        verifier.source = None
        return verifier

    def convert(self, value, *, wrapped, nullable=False, blank=False):
        if value is None and nullable:
            return value, "empty"
        if blank and value in ("", b""):
            return value, "empty"
        try:
            if wrapped:
                if not isinstance(value, str) or not value.startswith(PREFIX):
                    raise ValueError
                token = value[len(PREFIX) :].encode("ascii")
            else:
                if not isinstance(value, (bytes, bytearray, memoryview)):
                    raise ValueError
                token = bytes(value)
            # Reject malformed/noncanonical wrappers rather than silently fixing them.
            raw = base64.b64decode(token, altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw) != token:
                raise ValueError
            try:
                clear = self.target.decrypt(token)
                kind = "target"
            except InvalidToken:
                if self.source is None:
                    raise
                clear = self.source.decrypt(token)
                kind = "source"
        except (ValueError, TypeError, UnicodeError, InvalidToken):
            raise CommandError(
                "Gateway ciphertext is unreadable or unsupported"
            ) from None
        try:
            payload = json.loads(clear)
            if not isinstance(payload, dict) or (
                wrapped and not isinstance(payload.get("v"), str)
            ):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise CommandError("Gateway ciphertext payload is unsupported") from None
        if kind == "target":
            return value, kind
        encrypted = self.target.encrypt(clear)
        return (PREFIX + encrypted.decode("ascii") if wrapped else encrypted), kind
