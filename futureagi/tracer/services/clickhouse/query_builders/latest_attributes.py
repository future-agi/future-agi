"""Scalar latest-state predicates for point-scoped span attribute reads.

The ``spans`` table is a wide ReplacingMergeTree. ``FINAL`` on a query that
also reads attribute Maps can retain whole Maps while merging versions and is
therefore unsafe even when the result set is small.  List pagination first
narrows work to a bounded set of span/trace ids; these helpers then retain only
the requested Map element and its existence bit in ``argMax`` states.

The returned SQL fragments are intentionally schema-v1-shaped.  V2 builders
route complete statements through ``V2RewriteMixin``, which rewrites the Map
column names at the single established boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tracer.services.clickhouse.query_builders.filters import (
    build_literal_text_predicate,
    normalize_filter_op,
)

_SAFE_KEY = re.compile(r"^[a-zA-Z0-9._\-]+$")
_MAP_BY_TYPE = {
    "text": (
        "span_attr_str",
        lambda value: value if isinstance(value, str) else str(value),
    ),
    "string": (
        "span_attr_str",
        lambda value: value if isinstance(value, str) else str(value),
    ),
    "number": ("span_attr_num", float),
    "boolean": (
        "span_attr_bool",
        lambda value: (
            int(value)
            if isinstance(value, bool)
            else (_ for _ in ()).throw(
                ValueError("boolean attribute values must be booleans")
            )
        ),
    ),
}

# These frontend SYSTEM_METRIC names are backed by ordinary columns and have
# any-span semantics in the trace grid.  They are safe to resolve after the
# root stream has been narrowed to a bounded trace-id batch.  Root-only fields
# (for example ``name`` / token totals / latency) deliberately stay in the root
# seed and are not listed here; ``span_name`` is the any-span alias for name.
_ANY_SPAN_SYSTEM_COLUMNS = {
    "service_name": "service_name",
    "model": "model",
    "provider": "provider",
    "status": "status",
    "observation_type": "observation_type",
    "span_kind": "observation_type",
    "node_type": "observation_type",
    "span_name": "name",
}

# These fields describe the canonical trace row rendered by the trace list.
# Unlike the any-span columns above, they must be evaluated only after the
# newest live in-window root has been selected.  The aliases mirror
# ``ClickHouseFilterBuilder.SYSTEM_METRIC_MAP`` for the root-only metrics that
# can safely be reduced to scalar latest-version states.
_ROOT_SYSTEM_COLUMNS = {
    "trace_id": ("trace_id", "text"),
    "project_id": ("project_id", "text"),
    "session": ("trace_session_id", "text"),
    "session_id": ("trace_session_id", "text"),
    "trace_session_id": ("trace_session_id", "text"),
    "name": ("name", "text"),
    "trace_name": ("trace_name", "text"),
    "latency": ("latency_ms", "number"),
    "latency_ms": ("latency_ms", "number"),
    "avg_latency": ("latency_ms", "number"),
    "cost": ("cost", "number"),
    "avg_cost": ("cost", "number"),
    "tokens": ("total_tokens", "number"),
    "total_tokens": ("total_tokens", "number"),
    "gen_ai.usage.total_tokens": ("total_tokens", "number"),
    "llm.token_count.total": ("total_tokens", "number"),
    "input_tokens": ("prompt_tokens", "number"),
    "prompt_tokens": ("prompt_tokens", "number"),
    "gen_ai.usage.prompt_tokens": ("prompt_tokens", "number"),
    "gen_ai.usage.input_tokens": ("prompt_tokens", "number"),
    "llm.token_count.prompt": ("prompt_tokens", "number"),
    "output_tokens": ("completion_tokens", "number"),
    "completion_tokens": ("completion_tokens", "number"),
    "gen_ai.usage.completion_tokens": ("completion_tokens", "number"),
    "gen_ai.usage.output_tokens": ("completion_tokens", "number"),
    "llm.token_count.completion": ("completion_tokens", "number"),
}
_NULLABLE_ROOT_SYSTEM_COLUMNS = {"trace_session_id"}

# Physical columns rendered by the span list.  Unlike trace predicates these
# are always evaluated on the selected span itself.  Nullable v1 columns are
# wrapped in ``tuple`` by the scalar reducer so a later explicit NULL clears an
# older value instead of being skipped by ``argMax``.  The same expressions are
# valid against the non-null/defaulted v2 columns after the established rewrite.
_SPAN_SYSTEM_COLUMNS = {
    "id": ("id", "text", False),
    "span_id": ("id", "text", False),
    "trace_id": ("trace_id", "text", False),
    "session": ("trace_session_id", "text", True),
    "session_id": ("trace_session_id", "text", True),
    "trace_session_id": ("trace_session_id", "text", True),
    "user": ("end_user_id", "text", True),
    "end_user_id": ("end_user_id", "text", True),
    "name": ("name", "text", False),
    "span_name": ("name", "text", False),
    "trace_name": ("trace_name", "text", True),
    "service_name": ("service_name", "text", False),
    "model": ("model", "text", True),
    "provider": ("provider", "text", True),
    "status": ("status", "text", True),
    "observation_type": ("observation_type", "text", False),
    "span_kind": ("observation_type", "text", False),
    "node_type": ("observation_type", "text", False),
    "latency": ("latency_ms", "number", True),
    "latency_ms": ("latency_ms", "number", True),
    "avg_latency": ("latency_ms", "number", True),
    "cost": ("cost", "number", True),
    "avg_cost": ("cost", "number", True),
    "tokens": ("total_tokens", "number", True),
    "total_tokens": ("total_tokens", "number", True),
    "gen_ai.usage.total_tokens": ("total_tokens", "number", True),
    "llm.token_count.total": ("total_tokens", "number", True),
    "input_tokens": ("prompt_tokens", "number", True),
    "prompt_tokens": ("prompt_tokens", "number", True),
    "gen_ai.usage.prompt_tokens": ("prompt_tokens", "number", True),
    "gen_ai.usage.input_tokens": ("prompt_tokens", "number", True),
    "llm.token_count.prompt": ("prompt_tokens", "number", True),
    "output_tokens": ("completion_tokens", "number", True),
    "completion_tokens": ("completion_tokens", "number", True),
    "gen_ai.usage.completion_tokens": ("completion_tokens", "number", True),
    "gen_ai.usage.output_tokens": ("completion_tokens", "number", True),
    "llm.token_count.completion": ("completion_tokens", "number", True),
}
_SPAN_TAG_COLUMNS = frozenset({"tag", "tags"})


@dataclass(frozen=True)
class LatestAttributePredicate:
    aggregates: tuple[str, ...]
    predicate: str
    params: dict[str, Any]


def _parts(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return (
        str(item.get("column_id") or item.get("columnId") or ""),
        item.get("filter_config") or item.get("filterConfig") or {},
    )


def is_span_attribute_filter(item: dict[str, Any]) -> bool:
    key, config = _parts(item)
    if key in {"created_at", "start_time"}:
        return False
    col_type = str(config.get("col_type") or config.get("colType") or "").upper()
    # The main filter compiler promotes known denormalised columns even when
    # older clients label them NORMAL or SPAN_ATTRIBUTE. Mirror that routing so
    # the scalar path preserves the same semantics.
    if key in _ANY_SPAN_SYSTEM_COLUMNS or key in _ROOT_SYSTEM_COLUMNS:
        return False
    return col_type == "SPAN_ATTRIBUTE"


def only_time_and_span_attribute_filters(filters: list[dict[str, Any]]) -> bool:
    return all(
        (item.get("column_id") or item.get("columnId")) in {"created_at", "start_time"}
        or is_span_attribute_filter(item)
        for item in filters
    )


def is_latest_trace_probe_filter(item: dict[str, Any]) -> bool:
    """Whether ``item`` has a scalar latest-state trace probe implementation."""

    if is_span_attribute_filter(item):
        return True
    key, config = _parts(item)
    col_type = str(config.get("col_type") or config.get("colType") or "").upper()
    return col_type in {"", "NORMAL", "SYSTEM_METRIC", "SPAN_ATTRIBUTE"} and key in {
        *_ANY_SPAN_SYSTEM_COLUMNS,
        *_ROOT_SYSTEM_COLUMNS,
    }


def is_latest_trace_root_probe_filter(item: dict[str, Any]) -> bool:
    """Whether ``item`` belongs on the selected canonical root row."""

    key, config = _parts(item)
    if is_span_attribute_filter(item):
        return key == "final_status"
    col_type = str(config.get("col_type") or config.get("colType") or "").upper()
    return (
        col_type in {"", "NORMAL", "SYSTEM_METRIC", "SPAN_ATTRIBUTE"}
        and key in _ROOT_SYSTEM_COLUMNS
    )


def _build_latest_scalar_column_predicate(
    item: dict[str, Any],
    *,
    index: int,
    column: str,
    value_type: str,
    alias_prefix: str,
    nullable: bool = False,
) -> LatestAttributePredicate:
    """Compile a physical column predicate over one scalar ``argMax``."""

    _, config = _parts(item)
    filter_type = str(
        config.get("filter_type") or config.get("filterType") or ""
    ).lower()
    if value_type == "text":
        if filter_type not in {"text", "string"}:
            raise ValueError("latest-state text column requires a text filter")
        coerce = str
    elif value_type == "number":
        if filter_type != "number":
            raise ValueError("latest-state numeric column requires a number filter")
        coerce = float
    else:  # pragma: no cover - mappings above are static
        raise ValueError("unsupported latest-state physical column type")

    op = normalize_filter_op(
        str(config.get("filter_op") or config.get("filterOp") or "")
    )
    raw_value = config.get("filter_value", config.get("filterValue"))
    alias = f"{alias_prefix}_{index}"
    # argMax skips rows whose first argument is NULL. Wrapping a nullable
    # physical column in a one-element tuple preserves an explicit latest NULL
    # (for example when a span clears its session) instead of resurrecting an
    # older non-NULL value.
    aggregate_value = f"tuple({column})" if nullable else column
    aggregate_suffix = ".1" if nullable else ""
    aggregates = (
        f"argMax({aggregate_value}, _peerdb_version){aggregate_suffix} AS {alias}",
    )
    params: dict[str, Any] = {}

    if op == "is_null":
        predicate = (
            f"({alias} IS NULL OR toString({alias}) = '')"
            if value_type == "text"
            else f"{alias} IS NULL"
        )
        return LatestAttributePredicate(aggregates, predicate, params)
    if op == "is_not_null":
        predicate = (
            f"({alias} IS NOT NULL AND toString({alias}) != '')"
            if value_type == "text"
            else f"{alias} IS NOT NULL"
        )
        return LatestAttributePredicate(aggregates, predicate, params)

    if op in {"in", "not_in"}:
        if not isinstance(raw_value, list) or not raw_value:
            raise ValueError(f"{op} requires a non-empty list")
        normalized: Any = tuple(coerce(value) for value in raw_value)
    elif op in {"between", "not_between"}:
        if not isinstance(raw_value, list) or len(raw_value) != 2:
            raise ValueError(f"{op} requires two values")
        normalized = tuple(coerce(value) for value in raw_value)
    else:
        if raw_value is None:
            raise ValueError(f"{op} requires a value")
        normalized = coerce(raw_value)

    lhs = alias
    if value_type == "text":
        lhs = f"lowerUTF8(toString({alias}))"
        if op in {"equals", "not_equals", "in", "not_in"}:
            if isinstance(normalized, tuple):
                normalized = tuple(str(value).lower() for value in normalized)
            else:
                normalized = str(normalized).lower()

    param = f"{alias_prefix}_param_{index}"
    if op == "equals":
        params[param] = normalized
        predicate = f"{lhs} = %({param})s"
    elif op == "not_equals":
        params[param] = normalized
        predicate = f"{lhs} != %({param})s"
    elif op == "in":
        params[param] = normalized
        predicate = f"{lhs} IN %({param})s"
    elif op == "not_in":
        params[param] = normalized
        predicate = f"{lhs} NOT IN %({param})s"
    elif op in {"contains", "not_contains", "starts_with", "ends_with"}:
        if value_type != "text":
            raise ValueError("text operation requires a text column")
        params[param] = str(normalized)
        predicate = build_literal_text_predicate(
            alias,
            param,
            op,
            case_insensitive=True,
        )
    elif op in {"between", "not_between"}:
        lo_param = f"{param}_lo"
        hi_param = f"{param}_hi"
        params[lo_param], params[hi_param] = normalized
        operator = "BETWEEN" if op == "between" else "NOT BETWEEN"
        predicate = f"{lhs} {operator} %({lo_param})s AND %({hi_param})s"
    else:
        operator = {
            "greater_than": ">",
            "greater_than_or_equal": ">=",
            "less_than": "<",
            "less_than_or_equal": "<=",
        }.get(op)
        if operator is None:
            raise ValueError("unsupported latest-state system operation")
        params[param] = normalized
        predicate = f"{lhs} {operator} %({param})s"

    return LatestAttributePredicate(aggregates, predicate, params)


def build_latest_column_predicate(
    item: dict[str, Any],
    *,
    index: int,
) -> LatestAttributePredicate:
    """Compile a supported physical any-span column to an ``argMax`` predicate."""

    key, config = _parts(item)
    try:
        column = _ANY_SPAN_SYSTEM_COLUMNS[key]
    except KeyError as exc:
        raise ValueError("unsupported latest-state system metric") from exc
    col_type = str(config.get("col_type") or config.get("colType") or "").upper()
    if col_type not in {"", "NORMAL", "SYSTEM_METRIC", "SPAN_ATTRIBUTE"}:
        raise ValueError("latest-state physical column requires SYSTEM_METRIC")
    return _build_latest_scalar_column_predicate(
        item,
        index=index,
        column=column,
        value_type="text",
        alias_prefix="latest_column_value",
    )


def build_latest_root_column_predicate(
    item: dict[str, Any],
    *,
    index: int,
) -> LatestAttributePredicate:
    """Compile a canonical-root physical column predicate."""

    key, config = _parts(item)
    col_type = str(config.get("col_type") or config.get("colType") or "").upper()
    if col_type not in {"", "NORMAL", "SYSTEM_METRIC", "SPAN_ATTRIBUTE"}:
        raise ValueError("latest-state root column requires SYSTEM_METRIC")
    try:
        column, value_type = _ROOT_SYSTEM_COLUMNS[key]
    except KeyError as exc:
        raise ValueError("unsupported latest-state root system metric") from exc
    return _build_latest_scalar_column_predicate(
        item,
        index=index,
        column=column,
        value_type=value_type,
        alias_prefix="latest_root_value",
        nullable=column in _NULLABLE_ROOT_SYSTEM_COLUMNS,
    )


def build_latest_trace_probe_predicate(
    item: dict[str, Any],
    *,
    index: int,
) -> LatestAttributePredicate:
    if is_span_attribute_filter(item):
        return build_latest_attribute_predicate(item, index=index)
    if is_latest_trace_root_probe_filter(item):
        return build_latest_root_column_predicate(item, index=index)
    return build_latest_column_predicate(item, index=index)


def build_latest_span_probe_predicate(
    item: dict[str, Any],
    *,
    index: int,
) -> LatestAttributePredicate:
    """Compile one span-list predicate over the span's latest scalar state.

    Known denormalised metrics use their physical column.  An unknown
    ``SYSTEM_METRIC`` intentionally follows ``ClickHouseFilterBuilder``'s
    compatibility rule and is treated as a typed span attribute.  Other
    unknown column shapes fail closed instead of being interpreted as SQL.
    """

    key, config = _parts(item)
    if key in {"created_at", "start_time"}:
        raise ValueError("time bounds are compiled outside the scalar predicate")
    col_type = str(config.get("col_type") or config.get("colType") or "").upper()

    if key in _SPAN_TAG_COLUMNS:
        if col_type not in {"", "NORMAL", "SYSTEM_METRIC", "SPAN_ATTRIBUTE"}:
            raise ValueError("latest-state span tags require SYSTEM_METRIC")
        filter_type = str(
            config.get("filter_type") or config.get("filterType") or ""
        ).lower()
        if filter_type not in {"text", "string"}:
            raise ValueError("latest-state span tags require a text filter")
        op = normalize_filter_op(
            str(config.get("filter_op") or config.get("filterOp") or "")
        )
        raw_value = config.get("filter_value", config.get("filterValue"))
        alias = f"latest_span_tags_{index}"
        aggregates = (f"argMax(tags, _peerdb_version) AS {alias}",)
        tags_array = f"JSONExtract({alias}, 'Array(String)')"
        params: dict[str, Any] = {}
        if op == "is_null":
            predicate = f"empty({tags_array})"
        elif op == "is_not_null":
            predicate = f"notEmpty({tags_array})"
        elif op in {"in", "not_in"}:
            if not isinstance(raw_value, list) or not raw_value:
                raise ValueError(f"{op} requires a non-empty list")
            param = f"latest_span_tags_param_{index}"
            params[param] = tuple(str(value).lower() for value in raw_value)
            member = (
                f"arrayExists(value -> lowerUTF8(value) IN %({param})s, {tags_array})"
            )
            predicate = (
                f"notEmpty({tags_array}) AND NOT ({member})"
                if op == "not_in"
                else member
            )
        else:
            if raw_value is None or isinstance(raw_value, list):
                raise ValueError(f"{op} requires one text value")
            param = f"latest_span_tags_param_{index}"
            if op in {"equals", "not_equals"}:
                params[param] = str(raw_value).lower()
                member = (
                    f"arrayExists(value -> lowerUTF8(value) = %({param})s, "
                    f"{tags_array})"
                )
            elif op in {"contains", "not_contains", "starts_with", "ends_with"}:
                params[param] = str(raw_value)
                match_op = "contains" if op == "not_contains" else op
                literal = build_literal_text_predicate(
                    "value",
                    param,
                    match_op,
                    case_insensitive=True,
                )
                member = f"arrayExists(value -> {literal}, {tags_array})"
            else:
                raise ValueError("unsupported latest-state span tag operation")
            predicate = (
                f"notEmpty({tags_array}) AND NOT ({member})"
                if op in {"not_equals", "not_contains"}
                else member
            )
        return LatestAttributePredicate(aggregates, predicate, params)

    physical = _SPAN_SYSTEM_COLUMNS.get(key)
    if physical is not None:
        if col_type not in {"", "NORMAL", "SYSTEM_METRIC", "SPAN_ATTRIBUTE"}:
            raise ValueError("latest-state span column requires SYSTEM_METRIC")
        column, value_type, nullable = physical
        return _build_latest_scalar_column_predicate(
            item,
            index=index,
            column=column,
            value_type=value_type,
            alias_prefix="latest_span_column_value",
            nullable=nullable,
        )

    if is_span_attribute_filter(item):
        return build_latest_attribute_predicate(item, index=index)
    if col_type == "SYSTEM_METRIC":
        # Unknown system metrics are attribute-backed in the canonical filter
        # compiler.  Change only the routing tag; preserve type/op/value.
        attribute_item = dict(item)
        attribute_config = dict(config)
        attribute_config["col_type"] = "SPAN_ATTRIBUTE"
        attribute_item["filter_config"] = attribute_config
        attribute_item.pop("filterConfig", None)
        return build_latest_attribute_predicate(attribute_item, index=index)
    raise ValueError("unsupported latest-state span probe filter")


def is_latest_span_probe_filter(item: dict[str, Any]) -> bool:
    """Whether ``item`` is exactly representable by the span scalar probe."""

    try:
        build_latest_span_probe_predicate(item, index=0)
    except (TypeError, ValueError):
        return False
    return True


def build_latest_attribute_predicate(
    item: dict[str, Any],
    *,
    index: int,
) -> LatestAttributePredicate:
    """Compile one attribute predicate over scalar ``argMax`` aliases."""

    key, config = _parts(item)
    if not _SAFE_KEY.fullmatch(key):
        raise ValueError("invalid span attribute key")

    filter_type = str(
        config.get("filter_type") or config.get("filterType") or ""
    ).lower()
    try:
        map_column, coerce = _MAP_BY_TYPE[filter_type]
    except KeyError as exc:
        raise ValueError("unsupported span attribute type") from exc

    op = normalize_filter_op(
        str(config.get("filter_op") or config.get("filterOp") or "")
    )
    raw_value = config.get("filter_value", config.get("filterValue"))
    exists_alias = f"latest_attr_exists_{index}"
    value_alias = f"latest_attr_value_{index}"
    aggregates = (
        f"argMax(mapContains({map_column}, '{key}'), _peerdb_version) AS {exists_alias}",
        f"argMax({map_column}['{key}'], _peerdb_version) AS {value_alias}",
    )
    params: dict[str, Any] = {}

    if op == "is_null":
        predicate = f"NOT {exists_alias}"
    elif op == "is_not_null":
        predicate = exists_alias
    else:
        if op in {"in", "not_in"}:
            if not isinstance(raw_value, list) or not raw_value:
                raise ValueError(f"{op} requires a non-empty list")
            normalized: Any = tuple(coerce(value) for value in raw_value)
        elif op in {"between", "not_between"}:
            if not isinstance(raw_value, list) or len(raw_value) != 2:
                raise ValueError(f"{op} requires two values")
            normalized = tuple(coerce(value) for value in raw_value)
        else:
            if raw_value is None:
                raise ValueError(f"{op} requires a value")
            normalized = coerce(raw_value)

        lhs = value_alias
        if filter_type in {"text", "string"} and op in {
            "equals",
            "not_equals",
            "in",
            "not_in",
        }:
            lhs = f"lowerUTF8({value_alias})"
            if isinstance(normalized, tuple):
                normalized = tuple(
                    value.lower() if isinstance(value, str) else value
                    for value in normalized
                )
            elif isinstance(normalized, str):
                normalized = normalized.lower()

        param = f"latest_attr_param_{index}"
        if op == "equals":
            params[param] = normalized
            comparison = f"{lhs} = %({param})s"
        elif op == "not_equals":
            params[param] = normalized
            comparison = f"{lhs} != %({param})s"
        elif op == "in":
            params[param] = normalized
            comparison = f"{lhs} IN %({param})s"
        elif op == "not_in":
            params[param] = normalized
            comparison = f"{lhs} NOT IN %({param})s"
        elif op in {"contains", "not_contains", "starts_with", "ends_with"}:
            params[param] = str(normalized)
            comparison = build_literal_text_predicate(
                value_alias,
                param,
                op,
                case_insensitive=True,
            )
        elif op in {"between", "not_between"}:
            lo_param = f"{param}_lo"
            hi_param = f"{param}_hi"
            params[lo_param], params[hi_param] = normalized
            operator = "BETWEEN" if op == "between" else "NOT BETWEEN"
            comparison = f"{value_alias} {operator} %({lo_param})s AND %({hi_param})s"
        else:
            operator = {
                "greater_than": ">",
                "greater_than_or_equal": ">=",
                "less_than": "<",
                "less_than_or_equal": "<=",
            }.get(op)
            if operator is None:
                raise ValueError("unsupported span attribute operation")
            params[param] = normalized
            comparison = f"{value_alias} {operator} %({param})s"
        predicate = f"{exists_alias} AND {comparison}"

    return LatestAttributePredicate(
        aggregates=aggregates,
        predicate=predicate,
        params=params,
    )


__all__ = [
    "LatestAttributePredicate",
    "build_latest_attribute_predicate",
    "build_latest_column_predicate",
    "build_latest_root_column_predicate",
    "build_latest_span_probe_predicate",
    "build_latest_trace_probe_predicate",
    "is_latest_span_probe_filter",
    "is_latest_trace_probe_filter",
    "is_latest_trace_root_probe_filter",
    "is_span_attribute_filter",
    "only_time_and_span_attribute_filters",
]
