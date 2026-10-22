"""Signed current-observation value keysets, separate from native value cursors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tracer.services.clickhouse.v2.attribute_catalog_codec import (
    MAX_CANONICAL_VALUE_BYTES,
)

from .codec import CUSTOM_ATTRIBUTE_PREFIX
from .cursor import (
    PropertyCatalogCursorError,
    decode_current_cursor,
    encode_current_cursor,
)
from .runtime_limits import RUNTIME_LIMITS

PROPERTY_CATALOG_VALUE_CURSOR_VERSION = 2
PROPERTY_CATALOG_VALUE_CURSOR_SALT = "tracer.property-catalog-value-cursor.v2"
PROPERTY_CATALOG_VALUE_CURSOR_MAX_AGE_SECONDS = RUNTIME_LIMITS.cursor_max_age_seconds
PROPERTY_CATALOG_VALUE_CURSOR_MAX_BYTES = RUNTIME_LIMITS.cursor_max_bytes
PROPERTY_CATALOG_VALUE_CURSOR_MAX_PAGE_SIZE = RUNTIME_LIMITS.max_page_size


class PropertyCatalogValueCursorError(PropertyCatalogCursorError):
    pass


@dataclass(frozen=True, slots=True)
class PropertyCatalogValueCursor:
    order: tuple[int, str, str]


def normalize_property_catalog_value_query(query: dict[str, Any]) -> dict[str, Any]:
    property_id = str(query.get("property_id") or "")
    return {
        "property_id": (
            property_id
            if property_id.startswith(CUSTOM_ATTRIBUTE_PREFIX)
            else property_id.strip()
        ),
        "source": str(query.get("source") or "").strip(),
        "attribute_type": str(query.get("attribute_type") or "").strip(),
        "search": str(query.get("search") or "").strip().casefold(),
    }


def _validate_order(order):
    if (
        not isinstance(order, (list, tuple))
        or len(order) != 3
        or type(order[0]) is not int
        or not 1 <= order[0] <= 6
        or not isinstance(order[1], str)
        or re.fullmatch(r"[0-9a-f]{64}", order[1]) is None
        or not isinstance(order[2], str)
        or not order[2]
        or len(order[2].encode("utf-8", errors="surrogatepass"))
        > MAX_CANONICAL_VALUE_BYTES
    ):
        raise PropertyCatalogValueCursorError(
            "invalid_cursor", "The property-value continuation cursor is invalid."
        )
    return tuple(order)


def encode_property_catalog_value_cursor(*, scope, query, page_size, order):
    return encode_current_cursor(
        salt=PROPERTY_CATALOG_VALUE_CURSOR_SALT,
        scope=scope,
        query=normalize_property_catalog_value_query(query),
        page_size=page_size,
        order=_validate_order(order),
    )


def decode_property_catalog_value_cursor(token, *, scope, query, page_size):
    order = decode_current_cursor(
        token=token,
        salt=PROPERTY_CATALOG_VALUE_CURSOR_SALT,
        scope=scope,
        query=normalize_property_catalog_value_query(query),
        page_size=page_size,
        error_type=PropertyCatalogValueCursorError,
    )
    return PropertyCatalogValueCursor(order=_validate_order(order))
