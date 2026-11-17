"""Unintegrated, explicit webhook credential codec; no ORM or API normalization.

Callers supply independently trusted, authorized row context and BOTH ciphertext
fields with credential_storage_version=STORAGE_VERSION in one transaction. Each
field is encrypted, including an explicitly empty headers dict / secret string.
Those values INSIDE an authenticated active envelope are the only empty sentinels.
None/absent ciphertext is NEVER empty, even at version 1; version 0, missing, bool
or unknown versions refuse. Nullable columns are for schema expansion, not clears.
BinaryField adapters returning memoryview must explicitly copy it to bytes.

Normalized headers means the existing webhook_headers.header_map result after
existing serializer validation/placeholder resolution. Header values may be any
JSON values (not only strings); name case and insertion order are preserved. Raw
null/falsy/pair inputs belong to that existing normalization boundary, NOT here.
No API null/default/secret-sentinel policy, masked merge or read DTO is introduced.

Migration ONLY: preserve_for_migration encrypts an exact JSON original in a
separate purpose/state domain; recover/verify expose it only to that internal
workflow. Delivery refuses NEEDS_REENTRY even for an otherwise valid/empty
preserved original. Legitimate reentry writes a NEW active envelope; no metadata
state flip activates an old one. Main owns the fence, all-row conversion,
verification BEFORE plaintext clearing, routing, rotation and backup rollback.

Context checks authenticate payload bindings using the existing shared-key
primitive, NOT native AAD or per-tenant keys. No fallback, key source, crypto
algorithm, logger, decrypting ORM property or application integration lives here.
"""

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from functools import wraps
from typing import Literal, TypeAlias

from integrations.services.credentials import CredentialManager

JSONValue: TypeAlias = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)
HeaderMap: TypeAlias = dict[str, JSONValue]
STORAGE_VERSION = 1
_ACTIVE = "agentcc.webhook.credentials.active"
_PRESERVED = "agentcc.webhook.credentials.preserved"
_KEYS = frozenset(
    (
        "version",
        "purpose",
        "state",
        "organization_id",
        "webhook_id",
        "field",
        "destination",
        "value",
    )
)


class ErrorCode(StrEnum):
    INVALID_CONTEXT = "INVALID_CONTEXT"
    INVALID_VALUE = "INVALID_VALUE"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    INVALID_CIPHERTEXT = "INVALID_CIPHERTEXT"
    CRYPTO_FAILURE = "CRYPTO_FAILURE"
    INVALID_ENVELOPE = "INVALID_ENVELOPE"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    NEEDS_REENTRY = "NEEDS_REENTRY"
    NOT_PRESERVED = "NOT_PRESERVED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class WebhookCredentialStorageError(Exception):
    """Only a closed code may cross the storage boundary; no payload/cause."""

    def __init__(self, code: ErrorCode):
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, repr=False)
class StorageContext:
    """Exact persisted context supplied by a trusted caller, not client claims."""

    organization_id: str
    webhook_id: str
    field: Literal["headers", "secret"]
    destination: str


class _Refusal(Exception):
    def __init__(self, code):
        self.code = code


def _closed(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except _Refusal as failure:
            code = failure.code
        except Exception:
            code = ErrorCode.CRYPTO_FAILURE
        # Raise OUTSIDE the handler: not just suppressed display of a raw cause.
        raise WebhookCredentialStorageError(code) from None

    return guarded


def _context(context):
    if type(context) is not StorageContext:
        raise _Refusal(ErrorCode.INVALID_CONTEXT)
    for value in (
        context.organization_id,
        context.webhook_id,
        context.field,
        context.destination,
    ):
        if type(value) is not str or not value:
            raise _Refusal(ErrorCode.INVALID_CONTEXT)
    if context.field not in ("headers", "secret"):
        raise _Refusal(ErrorCode.INVALID_CONTEXT)


def _copy_json(value, code):
    """Exact builtins only: never invoke an object's coercion/iterator hooks."""
    kind = type(value)
    if value is None or kind is str or kind is bool or kind is int:
        return value
    if kind is float and math.isfinite(value):
        return value
    if kind is list:
        return [_copy_json(item, code) for item in value]
    if kind is dict:
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise _Refusal(code)
            result[key] = _copy_json(item, code)
        return result
    raise _Refusal(code)


def _value(value, field, preserved, code):
    try:
        result = _copy_json(value, code)
    except RecursionError:
        raise _Refusal(code) from None
    if field == "secret":
        if type(result) is not str:
            raise _Refusal(code)
    elif not preserved:
        if type(result) is not dict or any(not name for name in result):
            raise _Refusal(code)
    return result


def _encrypt(context, value, preserved):
    _context(context)
    value = _value(value, context.field, preserved, ErrorCode.INVALID_VALUE)
    envelope = {
        "version": STORAGE_VERSION,
        "purpose": _PRESERVED if preserved else _ACTIVE,
        "state": "needs_reentry" if preserved else "active",
        "organization_id": context.organization_id,
        "webhook_id": context.webhook_id,
        "field": context.field,
        "destination": context.destination,
        "value": value,
    }
    ciphertext = CredentialManager.encrypt(envelope)
    if type(ciphertext) is not bytes or not ciphertext:
        raise _Refusal(ErrorCode.CRYPTO_FAILURE)
    return ciphertext


def _same_context(envelope, context):
    return all(
        envelope[name] == getattr(context, name)
        for name in ("organization_id", "webhook_id", "field", "destination")
    )


def _decrypt(context, storage_version, ciphertext):
    _context(context)
    if type(storage_version) is not int or storage_version != STORAGE_VERSION:
        raise _Refusal(ErrorCode.UNSUPPORTED_VERSION)
    if type(ciphertext) is not bytes or not ciphertext:
        raise _Refusal(ErrorCode.INVALID_CIPHERTEXT)
    envelope = CredentialManager.decrypt(ciphertext)
    if type(envelope) is not dict or envelope.keys() != _KEYS:
        raise _Refusal(ErrorCode.INVALID_ENVELOPE)
    if type(envelope["version"]) is not int or envelope["version"] != STORAGE_VERSION:
        raise _Refusal(ErrorCode.UNSUPPORTED_VERSION)
    for name in _KEYS - {"version", "value"}:
        if type(envelope[name]) is not str:
            raise _Refusal(ErrorCode.INVALID_ENVELOPE)
    domain = (envelope["purpose"], envelope["state"])
    if domain not in ((_ACTIVE, "active"), (_PRESERVED, "needs_reentry")):
        raise _Refusal(ErrorCode.INVALID_ENVELOPE)
    if not _same_context(envelope, context):
        raise _Refusal(ErrorCode.CONTEXT_MISMATCH)
    preserved = domain == (_PRESERVED, "needs_reentry")
    value = _value(
        envelope["value"], context.field, preserved, ErrorCode.INVALID_ENVELOPE
    )
    return value, preserved


@_closed
def encrypt_for_storage(
    context: StorageContext, normalized_value: HeaderMap | str
) -> bytes:
    """Encrypt explicit normalized input, including empty; no defaults or coercion."""
    return _encrypt(context, normalized_value, False)


@_closed
def decrypt_for_delivery(
    context: StorageContext, *, storage_version: int, ciphertext: bytes
) -> HeaderMap | str:
    """Authenticate then validate before returning active ephemeral material."""
    value, preserved = _decrypt(context, storage_version, ciphertext)
    if preserved:
        raise _Refusal(ErrorCode.NEEDS_REENTRY)
    return value


@_closed
def preserve_for_migration(context: StorageContext, original: JSONValue) -> bytes:
    """Migration-only opaque preservation, never normalization or stringification."""
    return _encrypt(context, original, True)


@_closed
def recover_for_migration(
    context: StorageContext, *, storage_version: int, ciphertext: bytes
) -> JSONValue:
    """Migration-only recovery of needs_reentry originals; never use for delivery."""
    value, preserved = _decrypt(context, storage_version, ciphertext)
    if not preserved:
        raise _Refusal(ErrorCode.NOT_PRESERVED)
    return value


@_closed
def verify_preserved_for_migration(
    context: StorageContext,
    original: JSONValue,
    *,
    storage_version: int,
    ciphertext: bytes,
) -> None:
    """Compare a recovered original with exact JSON types, order and float sign."""
    value, preserved = _decrypt(context, storage_version, ciphertext)
    if not preserved:
        raise _Refusal(ErrorCode.NOT_PRESERVED)
    original = _value(original, context.field, True, ErrorCode.INVALID_VALUE)
    if json.dumps(value, allow_nan=False) != json.dumps(original, allow_nan=False):
        raise _Refusal(ErrorCode.VERIFICATION_FAILED)
