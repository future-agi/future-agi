"""Current native definitions combined with the observed-span key index."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.utils.property_registry import normalize_custom_attribute_source

from .codec import like_contains_pattern
from .cursor import (
    decode_property_catalog_cursor,
    encode_property_catalog_cursor,
    normalize_property_catalog_query,
    validate_page_size,
    validate_scope,
)
from .models import PropertyCategory, PropertyDefinition, PropertyKind, PropertyRole
from .runtime_limits import RUNTIME_LIMITS
from .source_adapters import (
    CurrentDefinitionSource,
    _span_attribute_aggregations,
    definition_metric,
    definition_order,
    resolve_span_attribute_type,
    source_matches,
)

PROPERTY_CATALOG_MAX_PAGE_SIZE = RUNTIME_LIMITS.max_page_size
PROPERTY_CATALOG_MAX_SEARCH_BYTES = RUNTIME_LIMITS.max_search_bytes
PROPERTY_CATALOG_QUERY_WALL_MS = RUNTIME_LIMITS.query_wall_ms


class PropertyCatalogUnavailable(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("The property catalog is temporarily unavailable.")


@dataclass(frozen=True, slots=True)
class PropertyCatalogPage:
    metrics: tuple[dict[str, Any], ...]
    has_more: bool
    next_cursor: str | None
    total: None = None
    total_is_exact: bool = False
    category_counts: dict[str, int] | None = None
    category_counts_exact: bool = False


def observed_table(database, table):
    if (
        not isinstance(database, str)
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database) is None
    ):
        raise ValueError("invalid observed catalog database")
    if table not in {"observed_attribute_keys", "observed_attribute_values"}:
        raise ValueError("invalid observed catalog table")
    return f"\x60{database}\x60.\x60{table}\x60"


class ObservedRead:
    """One request budget and lazy, SELECT-only catalog connection."""

    def __init__(self, executor=None, *, catalog_database, deadline=None):
        self.database = catalog_database
        self.deadline = deadline or ReadDeadline.start(PROPERTY_CATALOG_QUERY_WALL_MS)
        self.executor = executor
        self.query_count = 0

    def execute(self, sql, params, limit):
        try:
            if self.executor is None:
                from .connection import PropertyCatalogReadExecutor

                self.executor = PropertyCatalogReadExecutor(
                    max_wall_ms=self.deadline.remaining_ms(floor_ms=1),
                )
            result = self.executor.execute(
                sql,
                params,
                timeout_ms=self.deadline.remaining_ms(floor_ms=1),
                settings={
                    **RUNTIME_LIMITS.clickhouse_read_settings,
                    "max_result_rows": limit,
                },
            )
            self.query_count += 1
            self.deadline.remaining_ms(floor_ms=1)
            if (
                not isinstance(result.data, list)
                or len(result.data) > limit
                or not all(isinstance(row, dict) for row in result.data)
            ):
                raise ValueError("invalid observed query result")
            return result.data
        except PropertyCatalogUnavailable:
            raise
        except Exception as exc:
            raise PropertyCatalogUnavailable("query_failed") from exc

    @staticmethod
    def scope_params(scope):
        return {
            "organization_id": scope["organization_id"],
            "workspace_id": scope["workspace_id"],
            "project_ids": tuple(scope["project_ids"]),
        }


class PropertyCatalogReader:
    def __init__(
        self, executor=None, *, catalog_database, definition_source=None, deadline=None
    ):
        self.observed = ObservedRead(
            executor, catalog_database=catalog_database, deadline=deadline
        )
        self.definitions = definition_source or CurrentDefinitionSource(
            self.observed.deadline
        )

    def read_page(self, *, scope, query, page_size, cursor_token=None):
        scope = self._validate_scope(scope)
        query = self._validate_query(query)
        validate_page_size(page_size)
        cursor = (
            decode_property_catalog_cursor(
                cursor_token, scope=scope, query=query, page_size=page_size
            )
            if cursor_token
            else None
        )
        after = cursor.order if cursor else None
        try:
            definitions = list(
                self.definitions.read_page(
                    scope=scope, query=query, after=after, limit=page_size + 1
                )
            )
            definitions.extend(self._observed_keys(scope, query, after, page_size + 1))
            self.observed.deadline.remaining_ms(floor_ms=1)
        except PropertyCatalogUnavailable:
            raise
        except Exception as exc:
            raise PropertyCatalogUnavailable("definition_read_failed") from exc
        definitions.sort(key=definition_order)
        visible = definitions[:page_size]
        has_more = len(definitions) > page_size
        next_cursor = (
            encode_property_catalog_cursor(
                scope=scope,
                query=query,
                page_size=page_size,
                order=definition_order(visible[-1]),
            )
            if has_more
            else None
        )
        return PropertyCatalogPage(
            tuple(definition_metric(d) for d in visible), has_more, next_cursor
        )

    def _observed_keys(self, scope, query, after, limit):
        if (
            not scope["project_ids"]
            or query.get("category") not in {"", "custom_attribute"}
            or query.get("property_kind") not in {"", "custom_attribute"}
            or not source_matches(query.get("source", ""), "traces", ())
            or (after is not None and after[:3] > (3, 0, "traces"))
        ):
            return ()
        key_after = (
            after[3:5]
            if after is not None and after[:3] == (3, 0, "traces")
            else ("", "")
        )
        sql = f"""
SELECT k.attribute_key AS attribute_key, min(k.key_folded) AS key_folded,
       arraySort(groupUniqArray(toString(k.attribute_type))) AS attribute_types,
       min(k.first_seen) AS first_seen, max(k.last_seen) AS last_seen
FROM {observed_table(self.observed.database, "observed_attribute_keys")} AS k
PREWHERE k.organization_id = %(organization_id)s AND k.workspace_id = %(workspace_id)s
  AND k.project_id IN %(project_ids)s AND k.source_kind = 'custom_attribute'
WHERE k.attribute_key != '' AND k.key_folded LIKE %(search)s
GROUP BY k.attribute_key
HAVING tuple(key_folded, attribute_key) > tuple(%(after_folded)s, %(after_key)s)
  AND (%(role)s = '' OR (%(role)s = 'metric' AND attribute_types = ['number'])
       OR (%(role)s = 'dimension' AND attribute_types != ['number']))
ORDER BY key_folded, attribute_key LIMIT %(limit)s
"""
        params = {
            **ObservedRead.scope_params(scope),
            "search": like_contains_pattern(query["search"]),
            "after_folded": key_after[0],
            "after_key": key_after[1],
            "role": query.get("role", ""),
            "limit": limit,
        }
        definitions = []
        for row in self.observed.execute(sql, params, limit):
            key = row["attribute_key"]
            if (
                not isinstance(key, str)
                or not key
                or row["key_folded"] != key.casefold()
            ):
                raise PropertyCatalogUnavailable("invalid_observed_key")
            types = tuple(sorted(set(row["attribute_types"])))
            if not types or not set(types) <= {
                "string",
                "number",
                "boolean",
                "array",
                "map",
                "json",
            }:
                raise PropertyCatalogUnavailable("invalid_observed_types")
            value_type = resolve_span_attribute_type(types)
            definitions.append(
                PropertyDefinition(
                    property_kind=PropertyKind.CUSTOM_ATTRIBUTE,
                    source_key=key,
                    category=PropertyCategory.CUSTOM_ATTRIBUTE,
                    category_rank=3,
                    source_rank=0,
                    definition_source="observed_attribute_keys",
                    primary_source="traces",
                    source_tokens=("attribute", "span", "traces", *types),
                    value_adapter="span_attribute_value",
                    name=key,
                    display_name=key,
                    value_type=value_type,
                    output_type=value_type,
                    role=(
                        PropertyRole.METRIC
                        if value_type == "number"
                        else PropertyRole.DIMENSION
                    ),
                    details={
                        "data_type": value_type,
                        "attribute_types": list(types),
                        "attribute_types_exact": False,
                        "allowed_aggregations": list(
                            _span_attribute_aggregations(types)
                        ),
                    },
                )
            )
        return tuple(definitions)

    _validate_scope = staticmethod(validate_scope)

    @staticmethod
    def _validate_query(query):
        normalized = normalize_property_catalog_query(query)
        if normalized["source"] and (
            normalized["category"] == "custom_attribute"
            or normalized["property_kind"] == "custom_attribute"
        ):
            normalized["source"] = normalize_custom_attribute_source(
                normalized["source"]
            )
        if len(normalized["search"].encode()) > PROPERTY_CATALOG_MAX_SEARCH_BYTES:
            raise ValueError("search exceeds the property catalog limit")
        if normalized.get("role", "") not in {"", "metric", "dimension"}:
            raise ValueError("invalid property role")
        return normalized
