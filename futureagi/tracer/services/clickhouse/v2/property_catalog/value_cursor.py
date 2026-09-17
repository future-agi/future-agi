"""Signed keyset cursors for activated native property-value pages.

The value cursor is deliberately separate from both the definition catalog
cursor and the legacy span-value cursor.  It pins the exact activated catalog
snapshot and retained-value window while binding every authorization and query
input that can change membership in a page.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from django.conf import settings
from django.core import signing

from tracer.services.clickhouse.v2.property_catalog.cursor import (
    normalize_property_catalog_scope,
)
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS

PROPERTY_CATALOG_VALUE_CURSOR_VERSION = 1
PROPERTY_CATALOG_VALUE_CURSOR_SALT = "tracer.property-catalog-value-cursor.v1"
PROPERTY_CATALOG_VALUE_CURSOR_MAX_AGE_SECONDS = RUNTIME_LIMITS.cursor_max_age_seconds
PROPERTY_CATALOG_VALUE_CURSOR_MAX_BYTES = RUNTIME_LIMITS.cursor_max_bytes
PROPERTY_CATALOG_VALUE_CURSOR_MAX_PAGE_SIZE = RUNTIME_LIMITS.max_page_size

_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")


class PropertyCatalogValueCursorError(ValueError):
    """Sanitized value-cursor error safe to expose at the HTTP boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PropertyCatalogValueCursor:
    catalog_epoch: int
    catalog_revision: int
    activation_fingerprint: str
    window_start: datetime
    window_end: datetime
    order: tuple[int, str]


def normalize_property_catalog_value_query(query: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize the complete logical value-query identity."""

    return {
        "property_id": str(query.get("property_id") or "").strip(),
        "source": str(query.get("source") or "").strip(),
        "attribute_type": str(query.get("attribute_type") or "").strip(),
        "search": str(query.get("search") or "").strip().casefold(),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _aware_utc(value: datetime, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _datetime_to_micros(value: datetime, label: str) -> int:
    value = _aware_utc(value, label)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _micros_to_datetime(value: Any, label: str) -> datetime:
    if type(value) is not int or value < 0:
        raise PropertyCatalogValueCursorError(
            "invalid_cursor", "The property-value continuation cursor is invalid."
        )
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)
    except OverflowError as exc:
        raise PropertyCatalogValueCursorError(
            "invalid_cursor", "The property-value continuation cursor is invalid."
        ) from exc


def _validate_order(value: Any) -> tuple[int, str]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise PropertyCatalogValueCursorError(
            "invalid_cursor", "The property-value continuation cursor is invalid."
        )
    attribute_type_rank, value_fingerprint = value
    if (
        type(attribute_type_rank) is not int
        or not 1 <= attribute_type_rank <= 6
        or not isinstance(value_fingerprint, str)
        or _SHA256_RE.fullmatch(value_fingerprint) is None
    ):
        raise PropertyCatalogValueCursorError(
            "invalid_cursor", "The property-value continuation cursor is invalid."
        )
    return attribute_type_rank, value_fingerprint


def _max_age_seconds() -> int:
    configured = int(
        getattr(
            settings,
            "PROPERTY_CATALOG_VALUE_CURSOR_MAX_AGE_SECONDS",
            PROPERTY_CATALOG_VALUE_CURSOR_MAX_AGE_SECONDS,
        )
    )
    return max(1, configured)


def encode_property_catalog_value_cursor(
    *,
    scope: dict[str, Any],
    query: dict[str, Any],
    page_size: int,
    catalog_epoch: int,
    catalog_revision: int,
    activation_fingerprint: str,
    window_start: datetime,
    window_end: datetime,
    order: tuple[int, str] | list[Any],
) -> str:
    if (
        type(page_size) is not int
        or not 1 <= page_size <= PROPERTY_CATALOG_VALUE_CURSOR_MAX_PAGE_SIZE
        or type(catalog_epoch) is not int
        or not 1 <= catalog_epoch <= 65_535
        or type(catalog_revision) is not int
        or catalog_revision < 1
        or not isinstance(activation_fingerprint, str)
        or _SHA256_RE.fullmatch(activation_fingerprint) is None
    ):
        raise ValueError("invalid property catalog value cursor state")
    checked_order = _validate_order(order)
    start_us = _datetime_to_micros(window_start, "window_start")
    end_us = _datetime_to_micros(window_end, "window_end")
    if start_us >= end_us:
        raise ValueError("property catalog value window must be non-empty")

    payload = {
        "v": PROPERTY_CATALOG_VALUE_CURSOR_VERSION,
        "scope": _digest(normalize_property_catalog_scope(scope)),
        "query": _digest(normalize_property_catalog_value_query(query)),
        "page_size": page_size,
        "catalog_epoch": catalog_epoch,
        "catalog_revision": catalog_revision,
        "activation_fingerprint": activation_fingerprint,
        "window_start_us": start_us,
        "window_end_us": end_us,
        "order": list(checked_order),
    }
    token = signing.dumps(
        payload,
        key=settings.SECRET_KEY,
        salt=PROPERTY_CATALOG_VALUE_CURSOR_SALT,
        compress=True,
    )
    if len(token.encode("utf-8")) > PROPERTY_CATALOG_VALUE_CURSOR_MAX_BYTES:
        raise ValueError("property catalog value cursor exceeds its transport bound")
    return token


def decode_property_catalog_value_cursor(
    token: str,
    *,
    scope: dict[str, Any],
    query: dict[str, Any],
    page_size: int,
) -> PropertyCatalogValueCursor:
    if (
        not isinstance(token, str)
        or not token
        or len(token.encode("utf-8")) > PROPERTY_CATALOG_VALUE_CURSOR_MAX_BYTES
    ):
        raise PropertyCatalogValueCursorError(
            "invalid_cursor", "The property-value continuation cursor is invalid."
        )
    try:
        payload = signing.loads(
            token,
            key=settings.SECRET_KEY,
            salt=PROPERTY_CATALOG_VALUE_CURSOR_SALT,
            max_age=_max_age_seconds(),
        )
    except signing.SignatureExpired as exc:
        raise PropertyCatalogValueCursorError(
            "cursor_expired", "The property-value continuation cursor has expired."
        ) from exc
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise PropertyCatalogValueCursorError(
            "invalid_cursor", "The property-value continuation cursor is invalid."
        ) from exc

    if (
        not isinstance(payload, dict)
        or payload.get("v") != PROPERTY_CATALOG_VALUE_CURSOR_VERSION
    ):
        raise PropertyCatalogValueCursorError(
            "invalid_cursor", "The property-value continuation cursor is invalid."
        )
    if (
        payload.get("scope") != _digest(normalize_property_catalog_scope(scope))
        or payload.get("query")
        != _digest(normalize_property_catalog_value_query(query))
        or payload.get("page_size") != page_size
    ):
        raise PropertyCatalogValueCursorError(
            "cursor_mismatch",
            "The property-value continuation cursor does not match this request.",
        )

    epoch = payload.get("catalog_epoch")
    revision = payload.get("catalog_revision")
    activation_fingerprint = payload.get("activation_fingerprint")
    if (
        type(epoch) is not int
        or not 1 <= epoch <= 65_535
        or type(revision) is not int
        or revision < 1
        or not isinstance(activation_fingerprint, str)
        or _SHA256_RE.fullmatch(activation_fingerprint) is None
    ):
        raise PropertyCatalogValueCursorError(
            "invalid_cursor", "The property-value continuation cursor is invalid."
        )
    window_start = _micros_to_datetime(payload.get("window_start_us"), "window_start")
    window_end = _micros_to_datetime(payload.get("window_end_us"), "window_end")
    if window_start >= window_end:
        raise PropertyCatalogValueCursorError(
            "invalid_cursor", "The property-value continuation cursor is invalid."
        )
    return PropertyCatalogValueCursor(
        catalog_epoch=epoch,
        catalog_revision=revision,
        activation_fingerprint=activation_fingerprint,
        window_start=window_start,
        window_end=window_end,
        order=_validate_order(payload.get("order")),
    )


__all__ = [
    "PropertyCatalogValueCursor",
    "PropertyCatalogValueCursorError",
    "decode_property_catalog_value_cursor",
    "encode_property_catalog_value_cursor",
    "normalize_property_catalog_value_query",
]
