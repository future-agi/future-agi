"""Deterministic codecs for unified property definitions.

The functions in this module are pure and deliberately independent of Django,
PostgreSQL, ClickHouse, and Kafka.  They form the byte contract shared by
definition producers, qualification, and the eventual catalog reader.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import unicodedata
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

MAX_DEFINITION_JSON_BYTES = 32 * 1024
MAX_IDENTITY_COMPONENT_BYTES = 4 * 1024
MAX_SEARCH_COMPONENT_BYTES = 8 * 1024
MAX_CANONICAL_NUMBER_LENGTH = 4 * 1024
ZERO_UUID = "00000000-0000-0000-0000-000000000000"

_PROPERTY_ID_KINDS = frozenset(
    {
        "system_attribute",
        "custom_attribute",
        "eval_template",
        "eval_config",
        "annotation",
        "dataset_column",
    }
)
_UUID_PROPERTY_ID_KINDS = frozenset(
    {"eval_template", "eval_config", "annotation", "dataset_column"}
)
_HEX_DIGITS = frozenset("0123456789abcdef")


class CatalogCodecError(ValueError):
    """A value cannot be represented by the property-catalog contract."""


def casefold_text(value: str, *, field: str = "text") -> str:
    """Return the exact catalog fold: Python ``str.casefold()``, only.

    Unicode normalization is intentionally not performed.  Existing property
    ordering uses casefold semantics, so composing or compatibility-normalizing
    here would silently change pagination order.
    """

    validate_text(value, field=field, max_bytes=MAX_SEARCH_COMPONENT_BYTES)
    return value.casefold()


def like_contains_pattern(value: str) -> str:
    """Escape one literal substring for a ClickHouse ``LIKE`` predicate."""

    validate_text(
        value,
        field="search",
        max_bytes=MAX_SEARCH_COMPONENT_BYTES,
        allow_empty=True,
    )
    if not value:
        return "%"
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def validate_text(
    value: str,
    *,
    field: str,
    max_bytes: int,
    allow_empty: bool = False,
) -> str:
    """Validate one identity/search field without rewriting it."""

    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CatalogCodecError(
            f"{field} contains an invalid Unicode surrogate"
        ) from exc
    if not allow_empty and not value:
        raise CatalogCodecError(f"{field} must not be empty")
    if len(encoded) > max_bytes:
        raise CatalogCodecError(f"{field} exceeds {max_bytes} UTF-8 bytes")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise CatalogCodecError(f"{field} contains a control character")
    return value


def stable_property_id(
    property_kind: str,
    source_key: str | UUID,
    *,
    primary_source: str = "",
) -> str:
    """Build the public, namespaced identity for a property definition."""

    kind = str(property_kind)
    if kind not in _PROPERTY_ID_KINDS:
        raise CatalogCodecError(f"unsupported property kind: {kind!r}")

    raw_key = str(source_key)
    validate_text(
        raw_key,
        field="source_key",
        max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
    )

    if kind == "system_attribute":
        validate_text(
            primary_source,
            field="primary_source",
            max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
        )
        if ":" in primary_source:
            raise CatalogCodecError("system primary_source must not contain ':'")
        return f"{kind}:{primary_source}:{raw_key}"

    if primary_source:
        raise CatalogCodecError(f"{kind} property IDs do not accept primary_source")
    if kind in _UUID_PROPERTY_ID_KINDS:
        try:
            parsed_key = UUID(raw_key)
        except ValueError as exc:
            raise CatalogCodecError(f"{kind} source_key must be a UUID") from exc
        if parsed_key.int == 0:
            raise CatalogCodecError(f"{kind} source_key must not be the all-zero UUID")
        raw_key = str(parsed_key)
    return f"{kind}:{raw_key}"


def canonical_uuid(
    value: str | UUID,
    *,
    field: str,
    allow_zero: bool = False,
) -> str:
    """Return one lowercase canonical UUID string or fail closed."""

    try:
        parsed = UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise CatalogCodecError(f"{field} must be a UUID") from exc
    if not allow_zero and parsed.int == 0:
        raise CatalogCodecError(f"{field} must not be the all-zero UUID")
    return str(parsed)


def canonical_json(
    value: Mapping[str, Any],
    *,
    max_bytes: int = MAX_DEFINITION_JSON_BYTES,
) -> str:
    """Encode a JSON object with stable keys and a hard UTF-8 byte ceiling."""

    if not isinstance(value, Mapping):
        raise TypeError("canonical property definitions must be mappings")
    _validate_json_value(value, path="$", seen=set())
    try:
        payload = _encode_json_value(value)
        encoded = payload.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise CatalogCodecError("property definition is not canonical JSON") from exc
    if len(encoded) > max_bytes:
        raise CatalogCodecError(
            f"canonical property definition exceeds {max_bytes} UTF-8 bytes"
        )
    return payload


def canonical_json_sha256(payload: str) -> str:
    """Hash an already-canonical UTF-8 JSON payload."""

    try:
        encoded = payload.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CatalogCodecError("canonical JSON contains an invalid surrogate") from exc
    return hashlib.sha256(encoded).hexdigest()


def framed_sha256(domain: str, *components: str | int | bool | None) -> str:
    """Hash unambiguous length-prefixed fields under an explicit domain."""

    validate_text(
        domain,
        field="digest domain",
        max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
    )
    digest = hashlib.sha256()
    domain_bytes = domain.encode("utf-8")
    digest.update(struct.pack(">I", len(domain_bytes)))
    digest.update(domain_bytes)
    for component in components:
        if component is None:
            encoded = b"<null>"
        elif isinstance(component, bool):
            encoded = b"true" if component else b"false"
        else:
            encoded = str(component).encode("utf-8")
        digest.update(struct.pack(">Q", len(encoded)))
        digest.update(encoded)
    return digest.hexdigest()


def require_sha256(value: str, *, field: str) -> str:
    """Validate the lowercase transport representation of a SHA-256 digest."""

    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if len(value) != 64 or any(char not in _HEX_DIGITS for char in value):
        raise CatalogCodecError(f"{field} must be 64 lowercase hex characters")
    return value


def combine_search_text(*components: str, source_tokens: Sequence[str] = ()) -> str:
    """Casefold and de-duplicate bounded search components in source order."""

    folded: list[str] = []
    seen: set[str] = set()
    for index, component in enumerate((*components, *source_tokens)):
        if not component:
            continue
        candidate = casefold_text(component, field=f"search component {index}")
        if candidate not in seen:
            seen.add(candidate)
            folded.append(candidate)
    result = " ".join(folded)
    if len(result.encode("utf-8")) > MAX_DEFINITION_JSON_BYTES:
        raise CatalogCodecError("combined search text exceeds 32768 UTF-8 bytes")
    return result


def _validate_json_value(value: Any, *, path: str, seen: set[int]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise CatalogCodecError(
                    f"{path} contains an invalid surrogate"
                ) from exc
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CatalogCodecError(f"{path} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise CatalogCodecError(f"{path} contains a cycle")
        seen.add(identity)
        for key, member in value.items():
            if not isinstance(key, str):
                raise CatalogCodecError(f"{path} contains a non-string object key")
            _validate_json_value(member, path=f"{path}.{key}", seen=seen)
        seen.remove(identity)
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise CatalogCodecError(f"{path} contains a cycle")
        seen.add(identity)
        for index, member in enumerate(value):
            _validate_json_value(member, path=f"{path}[{index}]", seen=seen)
        seen.remove(identity)
        return
    raise CatalogCodecError(f"{path} contains unsupported {type(value).__name__}")


def _encode_json_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        encoded = str(value)
        if len(encoded) > MAX_CANONICAL_NUMBER_LENGTH:
            raise CatalogCodecError("canonical JSON integer exceeds 4096 bytes")
        return encoded
    if isinstance(value, float):
        return _canonical_json_float(value)
    if isinstance(value, Mapping):
        return (
            "{"
            + ",".join(
                f"{_encode_json_value(key)}:{_encode_json_value(value[key])}"
                for key in sorted(value)
            )
            + "}"
        )
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode_json_value(member) for member in value) + "]"
    raise CatalogCodecError(f"unsupported canonical JSON value {type(value).__name__}")


def _canonical_json_float(value: float) -> str:
    """Encode a finite IEEE-754 value as minimal fixed-point JSON."""

    if not math.isfinite(value):
        raise CatalogCodecError("canonical JSON numbers must be finite")
    if value == 0:
        return "0"
    try:
        number = Decimal(repr(value))
    except InvalidOperation as exc:  # pragma: no cover - repr(float) is numeric
        raise CatalogCodecError("canonical JSON number is invalid") from exc

    sign, digits_tuple, exponent = number.as_tuple()
    digits = "".join(str(digit) for digit in digits_tuple).lstrip("0")
    if not digits:
        return "0"
    trimmed = digits.rstrip("0")
    exponent += len(digits) - len(trimmed)
    digits = trimmed
    if exponent >= 0:
        canonical = digits + ("0" * exponent)
    else:
        point = len(digits) + exponent
        canonical = (
            digits[:point] + "." + digits[point:]
            if point > 0
            else "0." + ("0" * -point) + digits
        )
    if sign:
        canonical = "-" + canonical
    if len(canonical) > MAX_CANONICAL_NUMBER_LENGTH:
        raise CatalogCodecError("canonical JSON number exceeds 4096 bytes")
    return canonical
