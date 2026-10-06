"""Typed suggestions from the observed value index, without catalog lifecycle gates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from tracer.services.clickhouse.v2.attribute_catalog_codec import (
    MAX_ARRAY_STRING_VALUE_BYTES,
    MAX_CANONICAL_VALUE_BYTES,
    MAX_FOLDED_VALUE_BYTES,
    MAX_STRING_VALUE_BYTES,
    encode_catalog_scalar,
)
from tracer.utils.property_registry import (
    parse_property_registry_id,
    validate_property_source_binding,
)

from .codec import like_contains_pattern, stable_property_id
from .cursor import validate_page_size, validate_scope
from .reader import ObservedRead, PropertyCatalogUnavailable, observed_table
from .runtime_limits import RUNTIME_LIMITS
from .source_adapters import system_property_value_adapter
from .value_cursor import (
    decode_property_catalog_value_cursor,
    encode_property_catalog_value_cursor,
    normalize_property_catalog_value_query,
)

PROPERTY_CATALOG_VALUE_ADAPTER = "span_attribute_value"
PROPERTY_CATALOG_VALUE_MAX_PAGE_SIZE = RUNTIME_LIMITS.max_page_size
PROPERTY_CATALOG_VALUE_MAX_SEARCH_BYTES = RUNTIME_LIMITS.max_search_bytes
_ATTRIBUTE_TYPES = ("string", "number", "boolean", "array", "map", "json")
_SELECTABLE_ATTRIBUTE_TYPES = frozenset(_ATTRIBUTE_TYPES[:4])
_ATTRIBUTE_TYPE_RANK = {kind: i + 1 for i, kind in enumerate(_ATTRIBUTE_TYPES)}


class PropertyCatalogValueUnavailable(PropertyCatalogUnavailable):
    pass


class PropertyCatalogValueNotReady(PropertyCatalogValueUnavailable):
    """Only explicit native-adapter dispatch, never a failed observation query."""


@dataclass(frozen=True, slots=True)
class PropertyCatalogValue:
    value: Any
    attribute_type: str
    scalar_kind: str
    value_fingerprint: str
    value_json: str
    first_seen: datetime
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class PropertyCatalogValuePage:
    values: tuple[PropertyCatalogValue, ...]
    has_more: bool
    next_cursor: str | None
    attribute_types: tuple[str, ...]
    query_count: int


class PropertyCatalogValueReader:
    def __init__(self, executor=None, *, catalog_database, deadline=None):
        self.observed = ObservedRead(
            executor, catalog_database=catalog_database, deadline=deadline
        )

    def read_page(self, *, scope, query, page_size, cursor_token=None):
        scope = self._validate_scope(scope)
        query = normalize_property_catalog_value_query(query)
        validate_page_size(page_size)
        decoded = parse_property_registry_id(query["property_id"])
        validate_property_source_binding(decoded, query["source"])
        source_kind = decoded["property_kind"]
        if source_kind == "custom_attribute":
            stable_property_id(source_kind, decoded["metric_name"])
        if source_kind not in {"custom_attribute", "system_attribute"}:
            raise PropertyCatalogValueNotReady("native_value_adapter")
        if (
            source_kind == "system_attribute"
            and system_property_value_adapter(
                decoded["definition_source"], decoded["metric_name"]
            )
            != PROPERTY_CATALOG_VALUE_ADAPTER
        ):
            raise PropertyCatalogValueNotReady("native_value_adapter")
        if query["attribute_type"] and query["attribute_type"] not in _ATTRIBUTE_TYPES:
            raise ValueError("invalid attribute_type")
        if len(query["search"].encode()) > PROPERTY_CATALOG_VALUE_MAX_SEARCH_BYTES:
            raise ValueError("search exceeds the property catalog limit")
        cursor = (
            decode_property_catalog_value_cursor(
                cursor_token, scope=scope, query=query, page_size=page_size
            )
            if cursor_token
            else None
        )
        if not scope["project_ids"]:
            return PropertyCatalogValuePage((), False, None, (), 0)
        params = {
            **ObservedRead.scope_params(scope),
            "source_kind": source_kind,
            "attribute_key": decoded["metric_name"],
        }
        predicate = """k.organization_id = %(organization_id)s AND k.workspace_id = %(workspace_id)s
            AND k.project_id IN %(project_ids)s AND k.source_kind = %(source_kind)s
            AND k.attribute_key = %(attribute_key)s"""
        type_sql = f"""
SELECT DISTINCT toString(k.attribute_type) AS attribute_type
FROM {observed_table(self.observed.database, "observed_attribute_keys")} AS k
PREWHERE {predicate}
ORDER BY attribute_type LIMIT 7
"""
        try:
            types = tuple(
                row["attribute_type"]
                for row in self.observed.execute(type_sql, params, 7)
            )
            if len(set(types)) != len(types) or not set(types) <= set(_ATTRIBUTE_TYPES):
                raise PropertyCatalogValueUnavailable("invalid_observed_types")
            selected = tuple(
                t
                for t in types
                if t in _SELECTABLE_ATTRIBUTE_TYPES
                and (not query["attribute_type"] or t == query["attribute_type"])
            )
            if not selected:
                return PropertyCatalogValuePage(
                    (), False, None, types, self.observed.query_count
                )
            after = cursor.order if cursor else (0, "", "")
            params.update(
                attribute_types=selected,
                search=like_contains_pattern(query["search"]),
                after_rank=after[0],
                after_fingerprint=after[1],
                after_json=after[2],
                limit=page_size + 1,
            )
            sql = f"""
SELECT toString(k.attribute_type) AS attribute_type,
       indexOf(['string','number','boolean','array','map','json'], toString(k.attribute_type)) AS attribute_type_rank,
       k.value_fingerprint AS value_fingerprint, k.value_json AS value_json,
       min(k.value_search_text_folded) AS value_search_text_folded,
       min(k.first_seen) AS first_seen, max(k.last_seen) AS last_seen
FROM {observed_table(self.observed.database, "observed_attribute_values")} AS k
PREWHERE {predicate}
WHERE k.attribute_type IN %(attribute_types)s
  AND k.value_search_text_folded LIKE %(search)s
GROUP BY k.attribute_type, k.value_fingerprint, k.value_json
HAVING tuple(attribute_type_rank, value_fingerprint, value_json)
    > tuple(%(after_rank)s, %(after_fingerprint)s, %(after_json)s)
ORDER BY attribute_type_rank, value_fingerprint, value_json LIMIT %(limit)s
"""
            rows = self.observed.execute(sql, params, page_size + 1)
            values = tuple(self._decode_value(row) for row in rows)
            previous = after
            for value in values:
                position = (
                    _ATTRIBUTE_TYPE_RANK[value.attribute_type],
                    value.value_fingerprint,
                    value.value_json,
                )
                if position <= previous:
                    raise PropertyCatalogValueUnavailable("value_order_invalid")
                previous = position
            has_more = len(values) > page_size
            values = values[:page_size]
            next_cursor = None
            if has_more:
                last = values[-1]
                next_cursor = encode_property_catalog_value_cursor(
                    scope=scope,
                    query=query,
                    page_size=page_size,
                    order=(
                        _ATTRIBUTE_TYPE_RANK[last.attribute_type],
                        last.value_fingerprint,
                        last.value_json,
                    ),
                )
            return PropertyCatalogValuePage(
                values, has_more, next_cursor, types, self.observed.query_count
            )
        except PropertyCatalogValueUnavailable:
            raise
        except PropertyCatalogUnavailable as exc:
            raise PropertyCatalogValueUnavailable(exc.reason) from exc

    @staticmethod
    def _decode_value(row):
        def reject_constant(value):
            raise ValueError("non-finite catalog value")

        try:
            raw = row["value_json"]
            if (
                not isinstance(raw, str)
                or len(raw.encode()) > MAX_CANONICAL_VALUE_BYTES
            ):
                raise ValueError("invalid value size")
            value = json.loads(raw, parse_float=Decimal, parse_constant=reject_constant)
            encoded = encode_catalog_scalar(value)
            kind = row["attribute_type"]
            if isinstance(value, str) and len(value.encode()) > (
                MAX_ARRAY_STRING_VALUE_BYTES
                if kind == "array"
                else MAX_STRING_VALUE_BYTES
            ):
                raise ValueError("ineligible string value")
            fingerprint = row["value_fingerprint"]
            if isinstance(fingerprint, bytes):
                fingerprint = fingerprint.decode("ascii")
            if (
                kind not in _SELECTABLE_ATTRIBUTE_TYPES
                or (kind != "array" and kind != encoded.kind)
                or encoded.value_json != raw
                or encoded.fingerprint != fingerprint
                or encoded.search_text.casefold() != row["value_search_text_folded"]
                or len(row["value_search_text_folded"].encode())
                > MAX_FOLDED_VALUE_BYTES
                or row["first_seen"] > row["last_seen"]
            ):
                raise ValueError("invalid canonical value")
            return PropertyCatalogValue(
                value,
                kind,
                encoded.kind,
                encoded.fingerprint,
                raw,
                row["first_seen"],
                row["last_seen"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PropertyCatalogValueUnavailable("value_payload_invalid") from exc

    _validate_scope = staticmethod(validate_scope)
