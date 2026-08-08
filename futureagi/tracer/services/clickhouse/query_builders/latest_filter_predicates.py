"""Latest-state predicates for point-scoped list candidates."""

from __future__ import annotations

import math
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    build_literal_text_predicate,
    normalize_filter_op,
)
from tracer.utils.filter_operators import (
    JSON_ARRAY_FILTER_MAX_MEMBERS,
    JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES,
    JSON_ARRAY_FILTER_MAX_TOTAL_STRING_UTF8_BYTES,
    JSON_FILTER_SIGNED_INT_MAX,
    JSON_FILTER_SIGNED_INT_MIN,
    JSON_FILTER_UNSIGNED_INT_MAX,
    normalize_span_attribute_filter_type,
    validate_json_map_filter_value,
)

_MAX_ATTRIBUTE_KEY_UTF8_BYTES = 4096


def _validate_attribute_key(key: str) -> str:
    """Accept real customer keys while rejecting ambiguous control payloads."""

    try:
        encoded = key.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise UnsupportedFilterShapeError("attribute key must be valid UTF-8") from exc
    if not encoded or len(encoded) > _MAX_ATTRIBUTE_KEY_UTF8_BYTES:
        raise UnsupportedFilterShapeError("attribute key length is invalid")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in key):
        raise UnsupportedFilterShapeError("attribute key contains control characters")
    return key


def _strict_bool(value: object) -> int:
    if not isinstance(value, bool):
        raise ValueError("boolean values must be booleans")
    return int(value)


def _strict_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("text values must be strings")
    return value


def _strict_finite_number(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("number values must be finite numbers")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("number values must be finite numbers") from exc
    if not math.isfinite(normalized):
        raise ValueError("number values must be finite numbers")
    return normalized


_MAP_BY_TYPE: dict[str, tuple[str, Callable[[object], object]]] = {
    "text": ("span_attr_str", _strict_text),
    "string": ("span_attr_str", _strict_text),
    "number": ("span_attr_num", _strict_finite_number),
    "boolean": ("span_attr_bool", _strict_bool),
}
_JSON_ARRAY_ALLOWED_OPS = frozenset(
    {"contains", "not_contains", "is_null", "is_not_null"}
)
_JSON_MAP_ALLOWED_OPS = frozenset(
    {
        "equals",
        "not_equals",
        "contains",
        "not_contains",
        "is_null",
        "is_not_null",
    }
)
_TRACE_ROOT_COLUMNS = {
    "trace_id": ("trace_id", "text", False),
    "project_id": ("project_id", "text", False),
    "session": ("trace_session_id", "text", True),
    "session_id": ("trace_session_id", "text", True),
    "trace_session_id": ("trace_session_id", "text", True),
    "name": ("name", "text", False),
    "trace_name": ("trace_name", "text", False),
    "latency": ("latency_ms", "number", True),
    "latency_ms": ("latency_ms", "number", True),
    "cost": ("cost", "number", True),
    "total_tokens": ("total_tokens", "number", True),
    "prompt_tokens": ("prompt_tokens", "number", True),
    "completion_tokens": ("completion_tokens", "number", True),
}
_SPAN_COLUMNS = {
    "id": ("id", "text", False),
    "span_id": ("id", "text", False),
    "trace_id": ("trace_id", "text", False),
    "session": ("trace_session_id", "text", True),
    "session_id": ("trace_session_id", "text", True),
    "trace_session_id": ("trace_session_id", "text", True),
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
    "cost": ("cost", "number", True),
    "total_tokens": ("total_tokens", "number", True),
    "prompt_tokens": ("prompt_tokens", "number", True),
    "completion_tokens": ("completion_tokens", "number", True),
}
_TRACE_ANY_SPAN_COLUMNS = {
    key: value
    for key, value in _SPAN_COLUMNS.items()
    if key
    in {
        "span_id",
        "span_name",
        "service_name",
        "model",
        "provider",
        "status",
        "observation_type",
        "span_kind",
        "node_type",
    }
}
_INTERNAL_ROOT_METRIC_TYPE = "INTERNAL_ROOT_METRIC"
_CALL_TYPE_RAW_VALUE = (
    "coalesce("
    "nullIf(JSONExtractString(span_attributes_raw, 'raw_log', 'type'), ''), "
    "nullIf(JSONExtractString(JSONExtractString(span_attributes_raw, 'raw_log'), "
    "'type'), ''), "
    "nullIf(JSONExtractString(span_attr_str['raw_log'], 'type'), '')"
    ")"
)
_CALL_TYPE_VALUE = (
    "multiIf("
    f"{_CALL_TYPE_RAW_VALUE} = 'inboundPhoneCall', 'inbound', "
    f"{_CALL_TYPE_RAW_VALUE} = 'outboundPhoneCall', 'outbound', "
    "null)"
)


class UnsupportedFilterShapeError(ValueError):
    """A bounded list cannot represent the supplied filter without guessing."""


@dataclass(frozen=True)
class LatestFilterPredicate:
    aggregates: tuple[str, ...]
    predicate: str
    seed_predicate: str
    params: dict[str, Any]
    scope: str
    # Optional raw-row predicate that is guaranteed to contain every physical
    # identity whose latest state satisfies ``predicate``.  Callers must still
    # replay those identities and apply ``predicate``; this is only a witness
    # selector, never the source of truth.
    raw_witness_predicate: str | None = None
    # Cheap typed-Map key-presence superset for graph-only candidate probes.
    # Unlike ``raw_witness_predicate`` this deliberately never compares the
    # attribute value; the latest-state classifier still applies the complete
    # value predicate before a row can become a graph point.
    raw_key_witness_predicate: str | None = None
    raw_witness_rank: int | None = None


def _parts(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(item, dict):
        raise UnsupportedFilterShapeError("filter must be an object")
    raw_key = item.get("column_id") if "column_id" in item else item.get("columnId")
    config = item.get("filter_config") or item.get("filterConfig") or {}
    if not isinstance(raw_key, str) or not raw_key or not isinstance(config, dict):
        raise UnsupportedFilterShapeError("filter key and config are required")
    return raw_key, config


def _normalize_value(
    config: dict[str, Any], coerce: Callable[[object], object]
) -> tuple[str, object | None]:
    operation = normalize_filter_op(
        str(config.get("filter_op") or config.get("filterOp") or "")
    )
    raw = config.get("filter_value", config.get("filterValue"))
    if operation in {"is_null", "is_not_null"}:
        return operation, None
    if operation in {"in", "not_in"}:
        if not isinstance(raw, list) or not raw:
            raise UnsupportedFilterShapeError(f"{operation} requires values")
        if any(value is None or value == "" for value in raw):
            raise UnsupportedFilterShapeError(f"{operation} requires non-empty values")
        return operation, tuple(coerce(value) for value in raw)
    if operation in {"between", "not_between"}:
        if not isinstance(raw, list) or len(raw) != 2:
            raise UnsupportedFilterShapeError(f"{operation} requires two values")
        if any(value is None or value == "" for value in raw):
            raise UnsupportedFilterShapeError(f"{operation} requires non-empty values")
        return operation, tuple(coerce(value) for value in raw)
    if raw is None or raw == "":
        raise UnsupportedFilterShapeError(f"{operation} requires a value")
    return operation, coerce(raw)


def _comparison(
    *,
    alias: str,
    exists_alias: str | None,
    config: dict[str, Any],
    coerce: Callable[[object], object],
    value_type: str,
    index: int,
) -> tuple[str, dict[str, Any]]:
    operation, value = _normalize_value(config, coerce)
    if operation == "is_null":
        return (f"NOT {exists_alias}" if exists_alias else f"{alias} IS NULL"), {}
    if operation == "is_not_null":
        return (exists_alias or f"{alias} IS NOT NULL"), {}

    lhs = alias
    if value_type == "text" and operation in {"equals", "not_equals", "in", "not_in"}:
        lhs = f"lowerUTF8(toString({alias}))"
        if isinstance(value, tuple):
            value = tuple(str(item).lower() for item in value)
        else:
            value = str(value).lower()

    param = f"latest_filter_param_{index}"
    params: dict[str, Any] = {}
    if operation in {"equals", "not_equals", "in", "not_in"}:
        params[param] = value
        operator = {
            "equals": "=",
            "not_equals": "!=",
            "in": "IN",
            "not_in": "NOT IN",
        }[operation]
        predicate = f"{lhs} {operator} %({param})s"
    elif operation in {"contains", "not_contains", "starts_with", "ends_with"}:
        if value_type != "text":
            raise UnsupportedFilterShapeError("text operation requires text")
        params[param] = str(value)
        predicate = build_literal_text_predicate(
            alias,
            param,
            operation,
            case_insensitive=True,
        )
    elif operation in {"between", "not_between"}:
        low_param = f"{param}_low"
        high_param = f"{param}_high"
        if not isinstance(value, tuple) or len(value) != 2:
            raise UnsupportedFilterShapeError("between requires two values")
        params[low_param], params[high_param] = value
        operator = "BETWEEN" if operation == "between" else "NOT BETWEEN"
        predicate = f"{alias} {operator} %({low_param})s AND %({high_param})s"
    else:
        operator = {
            "greater_than": ">",
            "greater_than_or_equal": ">=",
            "less_than": "<",
            "less_than_or_equal": "<=",
        }.get(operation)
        if operator is None:
            raise UnsupportedFilterShapeError(f"unsupported operation {operation!r}")
        params[param] = value
        predicate = f"{alias} {operator} %({param})s"
    if exists_alias:
        predicate = f"{exists_alias} AND {predicate}"
    return predicate, params


def _raw_uuid_seed_predicate(
    *,
    column: str,
    config: dict[str, Any],
    params: dict[str, Any],
    index: int,
) -> str | None:
    """Return an index-usable UUID equality seed without changing semantics.

    UUID-shaped identity columns are exposed to the frontend as text, so their
    latest-state classifier remains the ordinary case-insensitive text
    comparison. For canonical UUID equality/IN values, however, the raw
    identity column is an exact candidate superset and lets ClickHouse apply
    its skip indexes before reading wider columns. Invalid/non-canonical values
    and every negative or substring operation deliberately keep the generic
    text predicate.
    """

    operation = normalize_filter_op(
        str(config.get("filter_op") or config.get("filterOp") or "")
    )
    if operation not in {"equals", "in"}:
        return None
    param = f"latest_filter_param_{index}"
    value = params.get(param)
    values = value if isinstance(value, tuple) else (value,)
    if not values:
        return None
    try:
        canonical = tuple(str(uuid.UUID(str(item))) for item in values)
    except (AttributeError, TypeError, ValueError):
        return None
    if canonical != tuple(str(item) for item in values):
        return None
    operator = "=" if operation == "equals" else "IN"
    return f"{column} {operator} %({param})s"


def _attribute_plan(
    item: dict[str, Any], *, index: int, scope: str
) -> LatestFilterPredicate:
    raw_key, config = _parts(item)
    key = _validate_attribute_key(raw_key)
    raw_filter_value = config.get("filter_value", config.get("filterValue"))
    filter_type = normalize_span_attribute_filter_type(
        str(config.get("filter_type") or config.get("filterType") or ""),
        raw_filter_value,
    )
    attribute_value_types = config.get(
        "attribute_value_types", config.get("attributeValueTypes")
    )
    if attribute_value_types is not None:
        return _mixed_typed_attribute_plan(
            key=key,
            config=config,
            filter_type=filter_type,
            attribute_value_types=attribute_value_types,
            index=index,
            scope=scope,
        )
    if filter_type == "array":
        return _json_array_attribute_plan(
            key=key,
            config=config,
            index=index,
            scope=scope,
        )
    if filter_type == "map":
        return _json_map_attribute_plan(
            key=key,
            config=config,
            index=index,
            scope=scope,
        )
    try:
        map_column, coerce = _MAP_BY_TYPE[filter_type]
    except KeyError as exc:
        raise UnsupportedFilterShapeError("unsupported attribute type") from exc
    exists_alias = f"latest_attr_exists_{index}"
    value_alias = f"latest_attr_value_{index}"
    key_param = f"latest_filter_key_{index}"
    bound_key = f"%({key_param})s"
    predicate, params = _comparison(
        alias=value_alias,
        exists_alias=exists_alias,
        config=config,
        coerce=coerce,
        value_type="text" if filter_type in {"text", "string"} else filter_type,
        index=index,
    )

    seed_predicate, seed_params = _comparison(
        alias=f"{map_column}[{bound_key}]",
        exists_alias=f"mapContains({map_column}, {bound_key})",
        config=config,
        coerce=coerce,
        value_type="text" if filter_type in {"text", "string"} else filter_type,
        index=index,
    )
    if seed_params != params:
        raise AssertionError("latest and seed predicates must share bound values")
    # Preserve the semantic raw-row comparison before adding optional physical
    # index companions.  The deployed string-value bloom is built with
    # ASCII-only ``lower()`` while the public equality contract uses
    # ``lowerUTF8()``.  An ASCII filter value does not imply that stored values
    # are ASCII (for example, the Kelvin sign folds to ``k`` under Unicode), so
    # that companion must never participate in an exhaustive raw witness.
    exact_seed_predicate = seed_predicate
    params[key_param] = key
    operation = normalize_filter_op(
        str(config.get("filter_op") or config.get("filterOp") or "")
    )
    if map_column == "span_attr_num" and operation in {"equals", "in"}:
        bound_value = params[f"latest_filter_param_{index}"]
        normalized_values = (
            bound_value if isinstance(bound_value, tuple) else (bound_value,)
        )
        # The numeric Map value bloom is expression-based. Keep the exact
        # key/value comparison as the semantic predicate and add the implied
        # values expression solely so ClickHouse can prune value-absent parts.
        numeric_values = "mapValues(span_attr_num)"
        if operation == "equals":
            index_predicate = f"has({numeric_values}, %(latest_filter_param_{index})s)"
        else:
            placeholders = []
            for value_index, item_value in enumerate(normalized_values):
                index_param = f"latest_filter_index_{index}_{value_index}"
                params[index_param] = item_value
                placeholders.append(f"%({index_param})s")
            index_predicate = f"hasAny({numeric_values}, [{', '.join(placeholders)}])"
        seed_predicate = f"({seed_predicate}) AND {index_predicate}"

    positive_witness_operations = {
        "equals",
        "in",
        "contains",
        "starts_with",
        "ends_with",
        "between",
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
        "is_not_null",
    }
    key_witness_predicate = (
        f"(indexHint(has(mapKeys({map_column}), {bound_key})) AND "
        f"has({map_column}.keys, {bound_key}))"
    )
    # Negative/is-null shapes need absence semantics and JSON filters do not
    # reach this compiler. Keep the graph key probe limited to positive value
    # shapes (including is-not-null) for which key presence is a superset.
    raw_key_witness_predicate = (
        key_witness_predicate if operation in positive_witness_operations else None
    )
    raw_witness_predicate = None
    if operation in positive_witness_operations:
        raw_witness_predicate = key_witness_predicate
        if operation in {"equals", "in"}:
            # Positive scalar equality is safe to apply to raw physical rows:
            # every latest-live match necessarily has one physical live row
            # with the same key/value. The latest-state classifier remains the
            # source of truth and removes stale versions and tombstones.
            exhaustive_value_predicate = (
                exact_seed_predicate
                if map_column == "span_attr_str"
                else seed_predicate
            )
            raw_witness_predicate = (
                f"({key_witness_predicate}) AND ({exhaustive_value_predicate})"
            )
    return LatestFilterPredicate(
        aggregates=(
            f"argMax(mapContains({map_column}, {bound_key}), _peerdb_version) AS {exists_alias}",
            f"argMax({map_column}[{bound_key}], _peerdb_version) AS {value_alias}",
        ),
        predicate=predicate,
        seed_predicate=seed_predicate,
        params=params,
        scope=scope,
        raw_witness_predicate=raw_witness_predicate,
        raw_key_witness_predicate=raw_key_witness_predicate,
        raw_witness_rank=(
            {"equals": 0, "in": 0}.get(operation, 10)
            if operation in positive_witness_operations
            else None
        ),
    )


def _mixed_typed_attribute_plan(
    *,
    key: str,
    config: dict[str, Any],
    filter_type: str,
    attribute_value_types: object,
    index: int,
    scope: str,
) -> LatestFilterPredicate:
    """Preserve picker storage provenance in bounded latest-state reads."""

    operation = normalize_filter_op(
        str(config.get("filter_op") or config.get("filterOp") or "")
    )
    raw_values = config.get("filter_value", config.get("filterValue"))
    if operation not in {"in", "not_in"}:
        raise UnsupportedFilterShapeError(
            "attribute_value_types is only supported for in/not_in filters"
        )
    if (
        not isinstance(raw_values, list)
        or not raw_values
        or not isinstance(attribute_value_types, list)
        or len(attribute_value_types) != len(raw_values)
    ):
        raise UnsupportedFilterShapeError(
            "attribute_value_types must align one-for-one with filter_value"
        )

    fallback_storage_type = {
        "text": "string",
        "string": "string",
        "number": "number",
        "boolean": "boolean",
    }.get(filter_type)
    if fallback_storage_type is None:
        raise UnsupportedFilterShapeError(
            "mixed typed attributes require text, number, or boolean values"
        )

    grouped_values: dict[str, list[object]] = {}
    for raw_value, selected_type in zip(raw_values, attribute_value_types, strict=True):
        storage_type = selected_type or fallback_storage_type
        if storage_type not in {"string", "number", "boolean"}:
            raise UnsupportedFilterShapeError(
                "attribute value type must be string, number, or boolean"
            )
        if raw_value is None or raw_value == "":
            raise UnsupportedFilterShapeError(f"{operation} requires non-empty values")
        grouped_values.setdefault(storage_type, []).append(raw_value)

    key_param = f"latest_filter_key_{index}"
    bound_key = f"%({key_param})s"
    params: dict[str, Any] = {key_param: key}
    aggregates: list[str] = []
    latest_exists: list[str] = []
    latest_matches: list[str] = []
    seed_exists: list[str] = []
    seed_matches: list[str] = []
    key_witnesses: list[str] = []
    storage_metadata: dict[str, tuple[str, Callable[[object], object], bool]] = {
        "string": ("span_attr_str", _strict_text, True),
        "number": ("span_attr_num", _strict_finite_number, False),
        "boolean": ("span_attr_bool", _strict_bool, False),
    }

    for storage_type in ("string", "number", "boolean"):
        values = grouped_values.get(storage_type)
        if not values:
            continue
        map_column, coerce, case_insensitive = storage_metadata[storage_type]
        try:
            normalized_values = tuple(coerce(value) for value in values)
        except (TypeError, ValueError, OverflowError) as exc:
            raise UnsupportedFilterShapeError(str(exc)) from exc
        if case_insensitive:
            normalized_values = tuple(value.lower() for value in normalized_values)

        suffix = f"{index}_{storage_type}"
        exists_alias = f"latest_attr_exists_{suffix}"
        value_alias = f"latest_attr_value_{suffix}"
        value_param = f"latest_filter_param_{suffix}"
        params[value_param] = normalized_values
        aggregates.extend(
            (
                f"argMax(mapContains({map_column}, {bound_key}), _peerdb_version) AS {exists_alias}",
                f"argMax({map_column}[{bound_key}], _peerdb_version) AS {value_alias}",
            )
        )

        latest_lhs = (
            f"lowerUTF8(toString({value_alias}))" if case_insensitive else value_alias
        )
        seed_value = f"{map_column}[{bound_key}]"
        seed_lhs = (
            f"lowerUTF8(toString({seed_value}))" if case_insensitive else seed_value
        )
        latest_exists.append(exists_alias)
        latest_matches.append(f"({exists_alias} AND {latest_lhs} IN %({value_param})s)")
        map_exists = f"mapContains({map_column}, {bound_key})"
        seed_exists.append(map_exists)
        seed_matches.append(f"({map_exists} AND {seed_lhs} IN %({value_param})s)")
        key_witnesses.append(
            f"(indexHint(has(mapKeys({map_column}), {bound_key})) "
            f"AND has({map_column}.keys, {bound_key}))"
        )

    if not latest_matches:
        raise UnsupportedFilterShapeError(
            "mixed typed attribute filter has no selected values"
        )

    latest_positive = " OR ".join(latest_matches)
    seed_positive = " OR ".join(seed_matches)
    if operation == "in":
        predicate = f"({latest_positive})"
        seed_predicate = f"({seed_positive})"
        raw_witness_predicate = f"({seed_positive})"
        raw_key_witness_predicate = f"({' OR '.join(key_witnesses)})"
        raw_witness_rank = 0
    else:
        predicate = f"(({' OR '.join(latest_exists)}) AND NOT ({latest_positive}))"
        seed_predicate = f"(({' OR '.join(seed_exists)}) AND NOT ({seed_positive}))"
        # Negative membership requires absence knowledge and therefore cannot
        # use a raw-row witness without risking an incomplete latest replay.
        raw_witness_predicate = None
        raw_key_witness_predicate = None
        raw_witness_rank = None

    return LatestFilterPredicate(
        aggregates=tuple(aggregates),
        predicate=predicate,
        seed_predicate=seed_predicate,
        params=params,
        scope=scope,
        raw_witness_predicate=raw_witness_predicate,
        raw_key_witness_predicate=raw_key_witness_predicate,
        raw_witness_rank=raw_witness_rank,
    )


def _normalize_json_array_values(
    config: dict[str, Any],
) -> tuple[str, tuple[tuple[str, object], ...]]:
    """Validate the canonical JSON-array membership shape.

    The public contract represents selected array members as a non-empty list.
    Only JSON scalar members have a stable, type-aware ClickHouse comparison.
    Nested selected objects/arrays are rejected rather than compared by their
    serialization order.
    """

    operation = normalize_filter_op(
        str(config.get("filter_op") or config.get("filterOp") or "")
    )
    if operation not in _JSON_ARRAY_ALLOWED_OPS:
        raise UnsupportedFilterShapeError(
            f"unsupported JSON array operation {operation!r}"
        )
    if operation in {"is_null", "is_not_null"}:
        return operation, ()

    raw_values = config.get("filter_value", config.get("filterValue"))
    if not isinstance(raw_values, list) or not raw_values:
        raise UnsupportedFilterShapeError(
            f"{operation} requires a non-empty list of JSON scalar values"
        )
    if len(raw_values) > JSON_ARRAY_FILTER_MAX_MEMBERS:
        raise UnsupportedFilterShapeError(
            f"JSON array filters support at most {JSON_ARRAY_FILTER_MAX_MEMBERS} values"
        )

    normalized: list[tuple[str, object]] = []
    total_string_bytes = 0
    for value in raw_values:
        if value is None or value == "":
            raise UnsupportedFilterShapeError(
                f"{operation} requires non-empty JSON scalar values"
            )
        if isinstance(value, str):
            value_bytes = len(value.encode("utf-8"))
            if value_bytes > JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES:
                raise UnsupportedFilterShapeError(
                    "JSON array string member exceeds the UTF-8 byte limit"
                )
            total_string_bytes += value_bytes
            if total_string_bytes > JSON_ARRAY_FILTER_MAX_TOTAL_STRING_UTF8_BYTES:
                raise UnsupportedFilterShapeError(
                    "JSON array string members exceed the request byte limit"
                )
            normalized.append(("string", value))
        elif isinstance(value, bool):
            normalized.append(("boolean", int(value)))
        elif isinstance(value, int):
            if JSON_FILTER_SIGNED_INT_MIN <= value <= JSON_FILTER_SIGNED_INT_MAX:
                normalized.append(("integer", value))
            elif 0 <= value <= JSON_FILTER_UNSIGNED_INT_MAX:
                normalized.append(("unsigned_integer", value))
            else:
                raise UnsupportedFilterShapeError(
                    "JSON array integers must fit Int64 or UInt64"
                )
            # JavaScript's JSON.parse intentionally has one Number type, so a
            # picker cannot preserve whether the source literal was ``1`` or
            # ``1.0``.  For exactly representable integers, match the CH25
            # Double representation as well.  Large integers remain integer-
            # only so the >2^53 precision guarantee is unchanged.
            if abs(value) <= (1 << 53):
                normalized.append(("number", float(value)))
        elif isinstance(value, float):
            try:
                number = _strict_finite_number(value)
            except ValueError as exc:
                raise UnsupportedFilterShapeError(
                    "JSON array numbers must be finite"
                ) from exc
            normalized.append(("number", number))
        else:
            raise UnsupportedFilterShapeError(
                "nested JSON filter values are not supported"
            )
    return operation, tuple(normalized)


def _json_array_membership_predicate(
    *,
    array_expression: str,
    values: tuple[tuple[str, object], ...],
    operation: str,
    index: int,
) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {}
    grouped_values: dict[str, list[object]] = {
        "string": [],
        "boolean": [],
        "integer": [],
        "unsigned_integer": [],
        "number": [],
    }
    for value_type, value in values:
        if value not in grouped_values[value_type]:
            grouped_values[value_type].append(value)

    item_alias = f"latest_json_item_{index}"
    item_type = f"toString(JSONType({item_alias}))"
    clauses: list[str] = []
    for value_type, members in grouped_values.items():
        if not members:
            continue
        param = f"latest_filter_json_{index}_{value_type}"
        params[param] = tuple(members)
        if value_type == "string":
            comparison = (
                f"{item_type} = 'String' "
                f"AND JSONExtractString({item_alias}) IN %({param})s"
            )
        elif value_type == "boolean":
            comparison = (
                f"{item_type} = 'Bool' AND JSONExtractBool({item_alias}) IN %({param})s"
            )
        elif value_type == "integer":
            comparison = (
                f"{item_type} = 'Int64' AND JSONExtractInt({item_alias}) IN %({param})s"
            )
        elif value_type == "unsigned_integer":
            comparison = (
                f"{item_type} = 'UInt64' "
                f"AND JSONExtractUInt({item_alias}) IN %({param})s"
            )
        elif value_type == "number":
            comparison = (
                f"{item_type} = 'Double' "
                f"AND JSONExtractFloat({item_alias}) IN %({param})s"
            )
        else:  # pragma: no cover - guarded by _normalize_json_array_values
            raise AssertionError(f"unsupported JSON scalar type {value_type!r}")
        clauses.append(f"({comparison})")

    membership = (
        f"arrayExists({item_alias} -> ({' OR '.join(clauses)}), {array_expression})"
    )

    if operation == "contains":
        return membership, params
    if operation == "not_contains":
        return f"NOT ({membership})", params
    raise AssertionError(f"membership predicate received {operation!r}")


def _json_array_attribute_plan(
    *,
    key: str,
    config: dict[str, Any],
    index: int,
    scope: str,
) -> LatestFilterPredicate:
    """Compile a latest-state array membership predicate over JSON overflow."""

    operation, values = _normalize_json_array_values(config)
    exists_alias = f"latest_json_array_exists_{index}"
    value_alias = f"latest_json_array_value_{index}"
    key_param = f"latest_filter_key_{index}"
    bound_key = f"%({key_param})s"
    source_exists = (
        f"JSONHas(span_attributes_raw, {bound_key}) "
        f"AND toString(JSONType(span_attributes_raw, {bound_key})) = 'Array'"
    )
    source_value = f"JSONExtractArrayRaw(span_attributes_raw, {bound_key})"
    params: dict[str, Any] = {key_param: key}
    # Structured overflow has no skip index. Keep the ordered seed on the
    # project/date primary key and parse JSON only while replaying its bounded
    # candidate identities against latest state.
    seed_predicate = "1 = 1"

    if operation == "is_null":
        predicate = f"NOT {exists_alias}"
    elif operation == "is_not_null":
        predicate = exists_alias
    else:
        predicate_membership, predicate_params = _json_array_membership_predicate(
            array_expression=value_alias,
            values=values,
            operation=operation,
            index=index,
        )
        params.update(predicate_params)
        predicate = f"{exists_alias} AND {predicate_membership}"

    return LatestFilterPredicate(
        aggregates=(
            f"argMax(({source_exists}), _peerdb_version) AS {exists_alias}",
            f"argMax({source_value}, _peerdb_version) AS {value_alias}",
        ),
        predicate=predicate,
        seed_predicate=seed_predicate,
        params=params,
        scope=scope,
    )


def _json_map_scalar_variants(value: object) -> tuple[tuple[str, object], ...]:
    """Return type-aware ClickHouse JSON comparisons for one scalar."""

    if isinstance(value, str):
        return (("string", value),)
    if isinstance(value, bool):
        return (("boolean", int(value)),)
    if isinstance(value, int):
        if JSON_FILTER_SIGNED_INT_MIN <= value <= JSON_FILTER_SIGNED_INT_MAX:
            variants: list[tuple[str, object]] = [("integer", value)]
        elif 0 <= value <= JSON_FILTER_UNSIGNED_INT_MAX:
            variants = [("unsigned_integer", value)]
        else:  # pragma: no cover - shared validator rejects this first
            raise UnsupportedFilterShapeError(
                "JSON map integers must fit Int64 or UInt64"
            )
        # Browser JSON has a single Number representation.  Match an exactly
        # representable integral Double as well, mirroring array membership.
        if abs(value) <= (1 << 53):
            variants.append(("number", float(value)))
        return tuple(variants)
    if isinstance(value, float):
        return (("number", value),)
    raise UnsupportedFilterShapeError("JSON map members must be JSON scalars")


def _json_map_member_predicate(
    *,
    object_expression: str,
    member_key: str,
    member_value: object,
    index: int,
    member_index: int,
) -> tuple[str, dict[str, Any]]:
    """Compile one bound, type-aware direct member equality predicate."""

    key_param = f"latest_filter_map_key_{index}_{member_index}"
    bound_key = f"%({key_param})s"
    params: dict[str, Any] = {key_param: member_key}
    member_type = f"toString(JSONType({object_expression}, {bound_key}))"
    comparisons: list[str] = []
    for value_type, normalized_value in _json_map_scalar_variants(member_value):
        value_param = f"latest_filter_map_value_{index}_{member_index}_{value_type}"
        params[value_param] = normalized_value
        bound_value = f"%({value_param})s"
        if value_type == "string":
            extraction = f"JSONExtractString({object_expression}, {bound_key})"
            json_type = "String"
        elif value_type == "boolean":
            extraction = f"JSONExtractBool({object_expression}, {bound_key})"
            json_type = "Bool"
        elif value_type == "integer":
            extraction = f"JSONExtractInt({object_expression}, {bound_key})"
            json_type = "Int64"
        elif value_type == "unsigned_integer":
            extraction = f"JSONExtractUInt({object_expression}, {bound_key})"
            json_type = "UInt64"
        elif value_type == "number":
            extraction = f"JSONExtractFloat({object_expression}, {bound_key})"
            json_type = "Double"
        else:  # pragma: no cover - guarded above
            raise AssertionError(f"unsupported JSON scalar type {value_type!r}")
        comparisons.append(
            f"({member_type} = '{json_type}' AND {extraction} = {bound_value})"
        )

    return (
        f"JSONHas({object_expression}, {bound_key}) AND ({' OR '.join(comparisons)})",
        params,
    )


def _normalize_json_map_values(
    config: dict[str, Any],
) -> tuple[str, dict[str, object]]:
    operation = normalize_filter_op(
        str(config.get("filter_op") or config.get("filterOp") or "")
    )
    if operation not in _JSON_MAP_ALLOWED_OPS:
        raise UnsupportedFilterShapeError(
            f"unsupported JSON map operation {operation!r}"
        )
    if operation in {"is_null", "is_not_null"}:
        return operation, {}
    raw_value = config.get("filter_value", config.get("filterValue"))
    try:
        normalized = validate_json_map_filter_value(raw_value)
    except ValueError as exc:
        raise UnsupportedFilterShapeError(str(exc)) from exc
    return operation, normalized


def _json_map_attribute_plan(
    *,
    key: str,
    config: dict[str, Any],
    index: int,
    scope: str,
) -> LatestFilterPredicate:
    """Compile bounded flat-object semantics over the JSON overflow column.

    ``contains`` means that every supplied direct member exists and equals its
    supplied type-aware scalar. ``equals`` additionally requires the stored
    object to have exactly that many direct members.  Negative operations are
    complements within the object-typed domain, matching scalar/array filter
    behaviour where a missing or differently typed attribute is not a match.
    """

    operation, members = _normalize_json_map_values(config)
    exists_alias = f"latest_json_map_exists_{index}"
    value_alias = f"latest_json_map_value_{index}"
    key_param = f"latest_filter_key_{index}"
    bound_key = f"%({key_param})s"
    source_exists = (
        f"JSONHas(span_attributes_raw, {bound_key}) "
        f"AND toString(JSONType(span_attributes_raw, {bound_key})) = 'Object'"
    )
    source_value = f"JSONExtractRaw(span_attributes_raw, {bound_key})"
    params: dict[str, Any] = {key_param: key}

    # The JSON overflow has no matching skip index.  Never parse it in the
    # ordered seed scan; parsing is limited to latest-state candidate replay.
    seed_predicate = "1 = 1"
    if operation == "is_null":
        predicate = f"NOT {exists_alias}"
    elif operation == "is_not_null":
        predicate = exists_alias
    else:
        member_predicates: list[str] = []
        for member_index, (member_key, member_value) in enumerate(members.items()):
            member_predicate, member_params = _json_map_member_predicate(
                object_expression=value_alias,
                member_key=member_key,
                member_value=member_value,
                index=index,
                member_index=member_index,
            )
            member_predicates.append(f"({member_predicate})")
            params.update(member_params)
        containment = " AND ".join(member_predicates)
        exact = f"JSONLength({value_alias}) = {len(members)} AND {containment}"
        core = exact if operation in {"equals", "not_equals"} else containment
        if operation in {"not_equals", "not_contains"}:
            core = f"NOT ({core})"
        predicate = f"{exists_alias} AND ({core})"

    return LatestFilterPredicate(
        aggregates=(
            f"argMax(({source_exists}), _peerdb_version) AS {exists_alias}",
            f"argMax({source_value}, _peerdb_version) AS {value_alias}",
        ),
        predicate=predicate,
        seed_predicate=seed_predicate,
        params=params,
        scope=scope,
    )


def compile_span_attribute_row_predicate(
    item: dict[str, Any], *, index: int = 0
) -> tuple[str, dict[str, Any]]:
    """Compile one canonical span-attribute filter for a physical span row.

    List queries use :class:`LatestFilterPredicate` to replay candidate
    identities through ``argMax``. Dashboard aggregates already operate on a
    finite physical-row input, so they need the same type-aware semantics on
    that row rather than aggregate aliases. Keeping this compiler beside the
    latest-state compiler prevents text/number/bool and overflow JSON filters
    from drifting between the two API surfaces.
    """

    key, config = _parts(item)
    key = _validate_attribute_key(key)
    raw_value = config.get("filter_value", config.get("filterValue"))
    filter_type = normalize_span_attribute_filter_type(
        str(config.get("filter_type") or config.get("filterType") or ""),
        raw_value,
    )

    if filter_type not in {"array", "map"}:
        plan = _attribute_plan(item, index=index, scope="span")
        return plan.seed_predicate, dict(plan.params)

    key_param = f"latest_filter_key_{index}"
    bound_key = f"%({key_param})s"
    source_exists = (
        f"JSONHas(span_attributes_raw, {bound_key}) "
        f"AND toString(JSONType(span_attributes_raw, {bound_key})) = "
        f"'{('Array' if filter_type == 'array' else 'Object')}'"
    )
    params: dict[str, Any] = {key_param: key}

    if filter_type == "array":
        operation, values = _normalize_json_array_values(config)
        if operation == "is_null":
            return f"NOT ({source_exists})", params
        if operation == "is_not_null":
            return source_exists, params
        membership, membership_params = _json_array_membership_predicate(
            array_expression=f"JSONExtractArrayRaw(span_attributes_raw, {bound_key})",
            values=values,
            operation=operation,
            index=index,
        )
        params.update(membership_params)
        return f"({source_exists}) AND ({membership})", params

    operation, members = _normalize_json_map_values(config)
    if operation == "is_null":
        return f"NOT ({source_exists})", params
    if operation == "is_not_null":
        return source_exists, params

    object_expression = f"JSONExtractRaw(span_attributes_raw, {bound_key})"
    member_predicates: list[str] = []
    for member_index, (member_key, member_value) in enumerate(members.items()):
        member_predicate, member_params = _json_map_member_predicate(
            object_expression=object_expression,
            member_key=member_key,
            member_value=member_value,
            index=index,
            member_index=member_index,
        )
        member_predicates.append(f"({member_predicate})")
        params.update(member_params)
    containment = " AND ".join(member_predicates)
    exact = f"JSONLength({object_expression}) = {len(members)} AND {containment}"
    core = exact if operation in {"equals", "not_equals"} else containment
    if operation in {"not_equals", "not_contains"}:
        core = f"NOT ({core})"
    return f"({source_exists}) AND ({core})", params


def compile_exact_graph_filter_predicates(
    filters: list[dict[str, Any]],
    *,
    project_id: str,
    observe_type: str,
    annotation_label_ids: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Compile exact graph filters, including overflow JSON arrays/maps.

    ``ClickHouseFilterBuilderV2`` remains the canonical compiler for scalar,
    relational, eval, and annotation filters. Structured SPAN_ATTRIBUTE values
    use :func:`compile_span_attribute_row_predicate`, which preserves JSON type
    domains (missing/null/wrong-type never masquerade as an empty value).

    Span graphs apply each structured predicate to the contributing physical
    row. Trace graphs wrap each predicate in its own latest-live full-window
    trace-membership selector, matching the scalar any-span contract even when
    separate filters are satisfied by different sibling spans.
    """

    normalized_observe_type = str(observe_type or "trace").strip().lower()
    if normalized_observe_type not in {"trace", "span"}:
        raise UnsupportedFilterShapeError("observe_type must be trace or span")

    # Lazy imports avoid the base-filter <-> v2-filter module cycle.
    from tracer.services.clickhouse.v2.query_builders.filters import (
        ClickHouseFilterBuilderV2,
        rewrite_v1_sql_to_v2,
    )

    ordinary_filters: list[dict[str, Any]] = []
    structured_filters: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(filters or []):
        if not isinstance(item, dict):
            raise UnsupportedFilterShapeError("filter must be an object")
        config_key = "filter_config" if "filter_config" in item else "filterConfig"
        config = item.get(config_key) or {}
        if not isinstance(config, dict):
            raise UnsupportedFilterShapeError("filter config must be an object")
        col_type = config.get("col_type") or config.get("colType")
        raw_value = config.get("filter_value", config.get("filterValue"))
        filter_type = str(config.get("filter_type") or config.get("filterType") or "")
        if col_type == ClickHouseFilterBuilderV2.SPAN_ATTRIBUTE:
            normalized_type = normalize_span_attribute_filter_type(
                filter_type,
                raw_value,
            )
            # Legacy filter_type=json is value-sensitive. Normalize scalar JSON
            # too so it reaches the typed Map compiler as text/number/boolean.
            if normalized_type != filter_type:
                normalized_config = dict(config)
                normalized_config[
                    "filter_type" if "filter_type" in config else "filterType"
                ] = normalized_type
                item = {**item, config_key: normalized_config}
            if normalized_type in {"array", "map"}:
                structured_filters.append((index, item))
                continue
        ordinary_filters.append(item)

    query_mode = (
        ClickHouseFilterBuilderV2.QUERY_MODE_SPAN
        if normalized_observe_type == "span"
        else ClickHouseFilterBuilderV2.QUERY_MODE_TRACE
    )
    builder = ClickHouseFilterBuilderV2(
        table="spans",
        project_id=project_id,
        query_mode=query_mode,
        span_date_scope=True,
        annotation_label_ids=list(annotation_label_ids or ()),
        annotation_label_set_known=annotation_label_ids is not None,
    )
    ordinary_clause, params = builder.translate(ordinary_filters)
    clauses = [ordinary_clause] if ordinary_clause else []
    params = dict(params)

    for index, item in structured_filters:
        row_clause, row_params = compile_span_attribute_row_predicate(
            item,
            index=index,
        )
        row_clause = rewrite_v1_sql_to_v2(row_clause)
        if not row_clause:
            raise UnsupportedFilterShapeError(
                "structured attribute predicate cannot be empty"
            )
        if normalized_observe_type == "trace":
            row_clause = f"""
            trace_id IN (
                SELECT DISTINCT trace_id
                FROM spans FINAL
                PREWHERE project_id = toUUID(%(project_id)s)
                  AND start_time >= %(snapshot_start_date)s
                  AND start_time < %(snapshot_end_date)s
                WHERE is_deleted = 0
                  AND {row_clause}
            )
            """
        clauses.append(row_clause)
        params.update(row_params)

    return " AND ".join(f"({clause})" for clause in clauses), params


def _column_plan(
    item: dict[str, Any],
    *,
    index: int,
    column: str,
    value_type: str,
    nullable: bool,
    scope: str,
) -> LatestFilterPredicate:
    _, config = _parts(item)
    filter_type = str(
        config.get("filter_type") or config.get("filterType") or ""
    ).lower()
    if value_type == "text" and filter_type not in {"text", "string", "categorical"}:
        raise UnsupportedFilterShapeError("text column requires a text filter")
    if value_type == "number" and filter_type != "number":
        raise UnsupportedFilterShapeError("numeric column requires a number filter")
    if value_type == "datetime" and filter_type not in {"date", "datetime"}:
        raise UnsupportedFilterShapeError("datetime column requires a datetime filter")
    alias = f"latest_column_value_{index}"
    aggregate_value = f"tuple({column})" if nullable else column
    suffix = ".1" if nullable else ""
    predicate, params = _comparison(
        alias=alias,
        exists_alias=None,
        config=config,
        coerce=str if value_type in {"text", "datetime"} else float,
        value_type=value_type,
        index=index,
    )
    seed_predicate, seed_params = _comparison(
        alias=column,
        exists_alias=None,
        config=config,
        coerce=str if value_type in {"text", "datetime"} else float,
        value_type=value_type,
        index=index,
    )
    operation = normalize_filter_op(
        str(config.get("filter_op") or config.get("filterOp") or "")
    )
    if (
        value_type == "text"
        and column not in ClickHouseFilterBuilder._UUID_COLUMNS
        and operation in {"is_null", "is_not_null"}
    ):
        # The public direct-column text contract treats the empty-string
        # sentinel as null (see ClickHouseFilterBuilder._build_column_condition).
        # CH25 stores hot text columns such as ``model`` as non-Nullable String,
        # so a bare ``IS NULL`` can never match them. UUID columns (nullable or
        # not) must keep their native null predicate: comparing UUID to ``''``
        # is a ClickHouse type error. Keep the bounded latest-state compiler
        # semantically identical to the direct compiler for both the ordered
        # seed and replay predicates.
        if operation == "is_null":
            predicate = f"({alias} IS NULL OR {alias} = '')"
            seed_predicate = f"({column} IS NULL OR {column} = '')"
        else:
            predicate = f"({alias} IS NOT NULL AND {alias} != '')"
            seed_predicate = f"({column} IS NOT NULL AND {column} != '')"
    if column == "trace_session_id" or (column == "trace_id" and scope == "root"):
        seed_predicate = (
            _raw_uuid_seed_predicate(
                column=column,
                config=config,
                params=seed_params,
                index=index,
            )
            or seed_predicate
        )
    if seed_params != params:
        raise AssertionError("latest and seed predicates must share bound values")
    return LatestFilterPredicate(
        aggregates=(f"argMax({aggregate_value}, _peerdb_version){suffix} AS {alias}",),
        predicate=predicate,
        seed_predicate=seed_predicate,
        params=params,
        scope=scope,
    )


def _expression_plan(
    item: dict[str, Any],
    *,
    index: int,
    expression: str,
    value_type: str,
    nullable: bool,
    scope: str,
) -> LatestFilterPredicate:
    """Compile a latest-state predicate for a derived scalar expression."""

    _, config = _parts(item)
    filter_type = str(
        config.get("filter_type") or config.get("filterType") or ""
    ).lower()
    if value_type == "text" and filter_type not in {"text", "string", "categorical"}:
        raise UnsupportedFilterShapeError("text expression requires a text filter")
    if value_type == "number" and filter_type != "number":
        raise UnsupportedFilterShapeError("numeric expression requires a number filter")
    alias = f"latest_expression_value_{index}"
    predicate, params = _comparison(
        alias=alias,
        exists_alias=None,
        config=config,
        coerce=str if value_type == "text" else float,
        value_type=value_type,
        index=index,
    )
    seed_predicate, seed_params = _comparison(
        alias=expression,
        exists_alias=None,
        config=config,
        coerce=str if value_type == "text" else float,
        value_type=value_type,
        index=index,
    )
    if seed_params != params:
        raise AssertionError("latest and seed predicates must share bound values")
    aggregate_value = f"tuple({expression})" if nullable else expression
    suffix = ".1" if nullable else ""
    return LatestFilterPredicate(
        aggregates=(f"argMax({aggregate_value}, _peerdb_version){suffix} AS {alias}",),
        predicate=predicate,
        seed_predicate=seed_predicate,
        params=params,
        scope=scope,
    )


_NUMERIC_SYSTEM_COLUMNS = {
    "latency_ms",
    "cost",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
}
_DATETIME_SYSTEM_COLUMNS = {"start_time", "end_time", "created_at"}


def _system_metric_plan(
    item: dict[str, Any],
    *,
    index: int,
    trace_mode: bool,
    filter_builder_cls: type[ClickHouseFilterBuilder] = ClickHouseFilterBuilder,
) -> LatestFilterPredicate:
    """Preserve legacy SYSTEM_METRIC aliases with latest-version semantics.

    The old filter compiler accepts denormalised aliases and, for unknown
    metrics, falls back to the typed span maps.  Represent those forms here so
    they remain candidate-scoped instead of forcing the list back to a broad
    window scan.  End-user display strings still require their dimension
    lookup and are intentionally handled by the residual candidate compiler.
    """

    key, config = _parts(item)
    if key in filter_builder_cls._ENDUSER_STRING_COLUMNS:
        raise UnsupportedFilterShapeError(
            "end-user string metric needs residual lookup"
        )

    if key in filter_builder_cls.VOICE_SYSTEM_METRIC_STR_MAP:
        mapped = dict(item)
        mapped["column_id"] = filter_builder_cls.VOICE_SYSTEM_METRIC_STR_MAP[key]
        return _attribute_plan(
            mapped,
            index=index,
            scope="any" if trace_mode else "span",
        )

    filter_type = str(
        config.get("filter_type") or config.get("filterType") or ""
    ).lower()
    if key in filter_builder_cls.VOICE_SYSTEM_METRIC_EXPRS:
        return _expression_plan(
            item,
            index=index,
            expression=filter_builder_cls.VOICE_SYSTEM_METRIC_EXPRS[key],
            value_type="number",
            nullable=True,
            scope="any" if trace_mode else "span",
        )
    if key in filter_builder_cls.VOICE_SYSTEM_METRIC_STR_EXPRS:
        return _expression_plan(
            item,
            index=index,
            expression=filter_builder_cls.VOICE_SYSTEM_METRIC_STR_EXPRS[key],
            value_type="text",
            nullable=True,
            scope="any" if trace_mode else "span",
        )

    column = filter_builder_cls.SYSTEM_METRIC_MAP.get(key)
    if column is None:
        # This is the legacy compiler's documented fallback.  Keep the real
        # customer key bound as data and classify its latest typed-map value.
        return _attribute_plan(
            item,
            index=index,
            scope="any" if trace_mode else "span",
        )

    if column in _NUMERIC_SYSTEM_COLUMNS:
        value_type = "number"
    elif column in _DATETIME_SYSTEM_COLUMNS:
        value_type = "datetime"
    else:
        value_type = "text"
    if value_type == "number" and filter_type != "number":
        raise UnsupportedFilterShapeError("numeric system metric requires number")

    mapped_root_only = key in filter_builder_cls.ROOT_ONLY_SYSTEM_METRICS or (
        key != "span_name" and column in filter_builder_cls.ROOT_ONLY_SYSTEM_METRICS
    )
    scope = (
        "root" if trace_mode and mapped_root_only else ("any" if trace_mode else "span")
    )
    nullable = column not in {
        "id",
        "trace_id",
        "project_id",
        "name",
        "observation_type",
    }
    return _column_plan(
        item,
        index=index,
        column=column,
        value_type=value_type,
        nullable=nullable,
        scope=scope,
    )


def _call_type_plan(item: dict[str, Any], *, index: int) -> LatestFilterPredicate:
    _, config = _parts(item)
    alias = f"latest_json_value_{index}"
    predicate, params = _comparison(
        alias=alias,
        exists_alias=None,
        config=config,
        coerce=str,
        value_type="text",
        index=index,
    )
    seed_predicate, seed_params = _comparison(
        alias=_CALL_TYPE_VALUE,
        exists_alias=None,
        config=config,
        coerce=str,
        value_type="text",
        index=index,
    )
    if seed_params != params:
        raise AssertionError("latest and seed predicates must share bound values")
    return LatestFilterPredicate(
        aggregates=(
            f"argMax(tuple({_CALL_TYPE_VALUE}), _peerdb_version).1 AS {alias}",
        ),
        predicate=predicate,
        seed_predicate=seed_predicate,
        params=params,
        scope="any",
    )


def compile_trace_filter_plans(
    filters: list[dict[str, Any]],
    *,
    filter_builder_cls: type[ClickHouseFilterBuilder] = ClickHouseFilterBuilder,
) -> list[LatestFilterPredicate]:
    plans: list[LatestFilterPredicate] = []
    for item in filters:
        key, config = _parts(item)
        if key in {"created_at", "start_time"}:
            continue
        col_type = str(config.get("col_type") or config.get("colType") or "").upper()
        index = len(plans)
        if key == "call_type":
            plans.append(_call_type_plan(item, index=index))
        elif (
            col_type == _INTERNAL_ROOT_METRIC_TYPE
            and key == "observation_type"
            and item.get("_eval_task_trace_root") is True
        ):
            plans.append(
                _column_plan(
                    item,
                    index=index,
                    column="observation_type",
                    value_type="text",
                    nullable=False,
                    scope="root",
                )
            )
        elif key in _TRACE_ROOT_COLUMNS:
            column, value_type, nullable = _TRACE_ROOT_COLUMNS[key]
            plans.append(
                _column_plan(
                    item,
                    index=index,
                    column=column,
                    value_type=value_type,
                    nullable=nullable,
                    scope="root",
                )
            )
        elif key in _TRACE_ANY_SPAN_COLUMNS:
            column, value_type, nullable = _TRACE_ANY_SPAN_COLUMNS[key]
            plans.append(
                _column_plan(
                    item,
                    index=index,
                    column=column,
                    value_type=value_type,
                    nullable=nullable,
                    scope="any",
                )
            )
        elif col_type == "SPAN_ATTRIBUTE":
            # Trace attribute filters retain their documented any-span
            # semantics: separate child spans may satisfy separate filters.
            plans.append(_attribute_plan(item, index=index, scope="any"))
        elif col_type in {"SYSTEM_METRIC", "TRACE_END_USER"}:
            plans.append(
                _system_metric_plan(
                    item,
                    index=index,
                    trace_mode=True,
                    filter_builder_cls=filter_builder_cls,
                )
            )
        else:
            raise UnsupportedFilterShapeError(f"unsupported trace filter {key!r}")
    return plans


def compile_span_filter_plans(
    filters: list[dict[str, Any]],
) -> list[LatestFilterPredicate]:
    plans: list[LatestFilterPredicate] = []
    for item in filters:
        key, config = _parts(item)
        if key in {"created_at", "start_time"}:
            continue
        col_type = str(config.get("col_type") or config.get("colType") or "").upper()
        index = len(plans)
        if key == "call_type":
            plans.append(_call_type_plan(item, index=index))
        elif key in _SPAN_COLUMNS:
            column, value_type, nullable = _SPAN_COLUMNS[key]
            plans.append(
                _column_plan(
                    item,
                    index=index,
                    column=column,
                    value_type=value_type,
                    nullable=nullable,
                    scope="span",
                )
            )
        elif col_type == "SPAN_ATTRIBUTE":
            plans.append(_attribute_plan(item, index=index, scope="span"))
        elif col_type in {"SYSTEM_METRIC", "TRACE_END_USER"}:
            plans.append(_system_metric_plan(item, index=index, trace_mode=False))
        else:
            raise UnsupportedFilterShapeError(f"unsupported span filter {key!r}")
    return plans


_CANDIDATE_RESIDUAL_KEYS = {
    "annotator",
    "has_annotation",
    "has_eval",
    "my_annotations",
    *ClickHouseFilterBuilder._ENDUSER_STRING_COLUMNS,
}
_CANDIDATE_RESIDUAL_TYPES = {"ANNOTATION", "EVAL_METRIC"}


def _is_candidate_residual_filter(item: dict[str, Any]) -> bool:
    key, config = _parts(item)
    col_type = str(config.get("col_type") or config.get("colType") or "").upper()
    return key in _CANDIDATE_RESIDUAL_KEYS or col_type in _CANDIDATE_RESIDUAL_TYPES


def partition_trace_filter_plans(
    filters: list[dict[str, Any]],
    *,
    filter_builder_cls: type[ClickHouseFilterBuilder] = ClickHouseFilterBuilder,
) -> tuple[list[LatestFilterPredicate], list[dict[str, Any]]]:
    """Split scalar latest predicates from candidate-scoped relational ones."""

    supported: list[dict[str, Any]] = []
    residual: list[dict[str, Any]] = []
    for item in filters:
        key, _ = _parts(item)
        if key in {"created_at", "start_time"}:
            supported.append(item)
        elif _is_candidate_residual_filter(item):
            residual.append(item)
        else:
            # Validate each leaf before combining it so malformed payloads do
            # not get mislabeled as a relational residual.
            compile_trace_filter_plans([item], filter_builder_cls=filter_builder_cls)
            supported.append(item)
    return (
        compile_trace_filter_plans(supported, filter_builder_cls=filter_builder_cls),
        residual,
    )


def partition_span_filter_plans(
    filters: list[dict[str, Any]],
) -> tuple[list[LatestFilterPredicate], list[dict[str, Any]]]:
    """Span equivalent of :func:`partition_trace_filter_plans`."""

    supported: list[dict[str, Any]] = []
    residual: list[dict[str, Any]] = []
    for item in filters:
        key, _ = _parts(item)
        if key in {"created_at", "start_time"}:
            supported.append(item)
        elif _is_candidate_residual_filter(item):
            residual.append(item)
        else:
            compile_span_filter_plans([item])
            supported.append(item)
    return compile_span_filter_plans(supported), residual


def supports_trace_filters(filters: list[dict[str, Any]]) -> bool:
    try:
        partition_trace_filter_plans(filters)
    except (TypeError, ValueError):
        return False
    return True


def supports_span_filters(filters: list[dict[str, Any]]) -> bool:
    try:
        partition_span_filter_plans(filters)
    except (TypeError, ValueError):
        return False
    return True


def targets_trace_filter_domain(filters: list[dict[str, Any]]) -> bool:
    """Whether a request is intended for the bounded trace filter compiler."""

    for item in filters:
        try:
            key, config = _parts(item)
        except (TypeError, ValueError):
            continue
        if key in {"created_at", "start_time"}:
            continue
        col_type = str(config.get("col_type") or config.get("colType") or "").upper()
        if (
            key == "call_type"
            or key in _TRACE_ROOT_COLUMNS
            or key in _TRACE_ANY_SPAN_COLUMNS
            or col_type
            in {
                "ANNOTATION",
                "EVAL_METRIC",
                "SPAN_ATTRIBUTE",
                "SYSTEM_METRIC",
                "TRACE_END_USER",
            }
            or key in _CANDIDATE_RESIDUAL_KEYS
        ):
            return True
    return False


def targets_span_filter_domain(filters: list[dict[str, Any]]) -> bool:
    """Whether a request is intended for the bounded span filter compiler."""

    for item in filters:
        try:
            key, config = _parts(item)
        except (TypeError, ValueError):
            continue
        if key in {"created_at", "start_time"}:
            continue
        col_type = str(config.get("col_type") or config.get("colType") or "").upper()
        if (
            key == "call_type"
            or key in _SPAN_COLUMNS
            or col_type
            in {
                "ANNOTATION",
                "EVAL_METRIC",
                "SPAN_ATTRIBUTE",
                "SYSTEM_METRIC",
                "TRACE_END_USER",
            }
            or key in _CANDIDATE_RESIDUAL_KEYS
        ):
            return True
    return False


__all__ = [
    "LatestFilterPredicate",
    "UnsupportedFilterShapeError",
    "compile_exact_graph_filter_predicates",
    "compile_span_attribute_row_predicate",
    "compile_span_filter_plans",
    "compile_trace_filter_plans",
    "partition_span_filter_plans",
    "partition_trace_filter_plans",
    "supports_span_filters",
    "supports_trace_filters",
    "targets_span_filter_domain",
    "targets_trace_filter_domain",
]
