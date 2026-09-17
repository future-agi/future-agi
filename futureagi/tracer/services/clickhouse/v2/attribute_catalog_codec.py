"""Canonical scalar codec for the ingestion-fed span-attribute catalog.

This module is pure: it performs no ClickHouse I/O and is not wired into the
writer or read path yet. Its byte contract is mirrored by
``fi-collector/pkg/attributecatalog`` and pinned by one shared fixture file.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

FINGERPRINT_DOMAIN = b"futureagi.span-attribute-catalog.scalar.v1"
MAX_CANONICAL_NUMBER_LENGTH = 4096

ScalarKind = Literal["string", "number", "boolean"]


@dataclass(frozen=True, slots=True)
class CanonicalCatalogScalar:
    """The stable payload stored in one catalog value row."""

    kind: ScalarKind
    value_json: str
    search_text: str
    fingerprint: str


def encode_catalog_scalar(value: Any) -> CanonicalCatalogScalar:
    """Return the canonical JSON scalar and typed SHA-256 fingerprint.

    Only selectable scalar members are accepted. ``None``, arrays, and maps
    must not reach this function; map/JSON attributes are key-only and an array
    writer calls this once for each top-level scalar member it elects to emit.
    """

    if isinstance(value, bool):
        kind: ScalarKind = "boolean"
        value_json = "true" if value else "false"
        search_text = value_json
    elif isinstance(value, str):
        kind = "string"
        value_json = _canonical_json_string(value)
        search_text = value
    elif isinstance(value, (int, float, Decimal)):
        kind = "number"
        value_json = _canonical_json_number(value)
        search_text = value_json
    else:
        raise TypeError(
            f"catalog values must be JSON scalars, got {type(value).__name__}"
        )

    preimage = (
        FINGERPRINT_DOMAIN + b"\x00" + kind.encode() + b"\x00" + value_json.encode()
    )
    fingerprint = hashlib.sha256(preimage).hexdigest()
    return CanonicalCatalogScalar(kind, value_json, search_text, fingerprint)


def _canonical_json_string(value: str) -> str:
    parts = ['"']
    short_escapes = {
        '"': '\\"',
        "\\": "\\\\",
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
    for char in value:
        codepoint = ord(char)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError(
                "catalog strings must not contain unpaired UTF-16 surrogates"
            )
        if char in short_escapes:
            parts.append(short_escapes[char])
        elif codepoint < 0x20:
            parts.append(f"\\u{codepoint:04x}")
        else:
            parts.append(char)
    parts.append('"')
    return "".join(parts)


def _canonical_json_number(value: int | float | Decimal) -> str:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("catalog numbers must be finite")
        if value == 0:
            return "0"
        raw = repr(value)
    else:
        raw = str(value)

    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("catalog numbers must be finite JSON numbers") from exc
    if not number.is_finite():
        raise ValueError("catalog numbers must be finite")
    if number.is_zero():
        return "0"

    sign, digits_tuple, exponent = number.as_tuple()
    digits = "".join(str(digit) for digit in digits_tuple).lstrip("0")
    if not digits:
        return "0"
    trimmed = digits.rstrip("0")
    exponent += len(digits) - len(trimmed)
    digits = trimmed
    if exponent >= 0:
        projected_length = len(digits) + exponent + int(bool(sign))
        if projected_length > MAX_CANONICAL_NUMBER_LENGTH:
            raise ValueError("canonical catalog number exceeds 4096 bytes")
        canonical = digits + ("0" * exponent)
    else:
        point = len(digits) + exponent
        if point > 0:
            projected_length = len(digits) + 1 + int(bool(sign))
            if projected_length > MAX_CANONICAL_NUMBER_LENGTH:
                raise ValueError("canonical catalog number exceeds 4096 bytes")
            canonical = digits[:point] + "." + digits[point:]
        else:
            projected_length = 2 - point + len(digits) + int(bool(sign))
            if projected_length > MAX_CANONICAL_NUMBER_LENGTH:
                raise ValueError("canonical catalog number exceeds 4096 bytes")
            canonical = "0." + ("0" * -point) + digits
    if sign:
        canonical = "-" + canonical
    if len(canonical) > MAX_CANONICAL_NUMBER_LENGTH:
        raise ValueError("canonical catalog number exceeds 4096 bytes")
    return canonical
