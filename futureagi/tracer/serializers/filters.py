import json
import math
from typing import Any

from rest_framework import serializers

from tfc.utils.api_serializers import (
    ApiErrorResponseSerializer,
    StrictInputMixin,
    StrictInputSerializer,
)
from tfc.utils.serializer_fields import JSON_VALUE_SCHEMA, JsonValueField  # noqa: F401
from tracer.utils.attribute_suggestion_contract import (
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)
from tracer.utils.filter_operators import (
    FILTER_TYPE_ALLOWED_OPS,
    JSON_ARRAY_FILTER_MAX_MEMBERS,
    JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES,
    JSON_ARRAY_FILTER_MAX_TOTAL_STRING_UTF8_BYTES,
    LIST_FILTER_OPS,
    NO_VALUE_FILTER_OPS,
    RANGE_FILTER_OPS,
    SPAN_ATTR_ALLOWED_OPS,
    STRUCTURED_SPAN_ATTR_ALLOWED_OPS,
    filter_op_is_allowed,
    normalize_filter_type,
    normalize_span_attribute_filter_type,
    validate_json_map_filter_value,
)
from tracer.utils.property_registry import (
    validate_property_filter_binding,
    validate_property_graph_binding,
)

FILTER_CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filter_type": {
            "type": "string",
            "description": "Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, array, or map. Legacy json is value-sensitive for SPAN_ATTRIBUTE filters: list values become array and object values become map.",
        },
        "filter_op": {
            "type": "string",
            "description": "Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null.",
        },
        "filter_value": {
            "description": "Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type.",
        },
        "col_type": {
            "type": "string",
            "description": "Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL.",
        },
        "attribute_value_types": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["string", "number", "boolean"],
                "x-nullable": True,
            },
            "description": "Optional storage-family provenance aligned one-for-one with filter_value for mixed SPAN_ATTRIBUTE in/not_in filters. Null entries retain filter_type semantics for manually entered values.",
        },
    },
    "required": ["filter_type", "filter_op"],
    "additionalProperties": False,
}


FILTER_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "column_id": {
            "type": "string",
            "description": "Column or attribute id to filter on.",
        },
        "property_id": {
            "type": "string",
            "description": "Optional stable namespaced Property Registry identity.",
        },
        "display_name": {
            "type": "string",
            "description": "Optional UI label for chips and saved views.",
        },
        "source": {
            "type": "string",
            "description": "Optional source surface for mixed-source filters, for example traces, datasets, or simulation.",
        },
        "output_type": {
            "type": "string",
            "description": "Optional metric output type metadata used by eval and annotation filters.",
        },
        "filter_config": FILTER_CONFIG_SCHEMA,
    },
    "required": ["column_id", "filter_config"],
    "additionalProperties": False,
}

FILTER_LIST_SCHEMA = {
    "type": "array",
    "items": FILTER_ITEM_SCHEMA,
}
FILTER_LIST_QUERY_PARAM_SCHEMA = {
    "type": "string",
    "description": "JSON-encoded canonical filter list.",
}

# Public filter payloads are compiled into ClickHouse expressions and, for
# exact aggregates, persisted in background-work identities.  Bound the shape
# before either operation so one authenticated request cannot create an
# unbounded AST, driver parameter set, or cache/work-queue cardinality.
FILTER_LIST_MAX_ITEMS = 32
FILTER_LIST_MAX_VALUES = 64
FILTER_VALUE_MAX_DEPTH = 8
FILTER_STRING_MAX_UTF8_BYTES = 4_096
FILTER_LIST_MAX_TOTAL_STRING_UTF8_BYTES = 65_536
FILTER_CONFIG_MAX_UTF8_BYTES = 128 * 1_024
BOUNDED_LIST_DATETIME_FILTER_OPS = frozenset(
    {
        "equals",
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
        "between",
        "not_equals",
        "not_between",
        "is_null",
        "is_not_null",
    }
)
BOUNDED_DATETIME_FILTER_DESCRIPTION = (
    "On trace, span, session, graph, and eval-task bounded reads, "
    "created_at/start_time datetime filters support equals, greater_than, "
    "greater_than_or_equal, less_than, less_than_or_equal, between, "
    "not_equals, not_between, is_null, and is_not_null. Missing bounds retain "
    "the finite default window: 30 days ago for the lower bound and request-time "
    "now for the upper bound. Between and not_between use half-open [start, end) "
    "ranges; not_equals excludes one DateTime64(6) microsecond. Because the "
    "physical created_at/start_time field is non-null, is_null returns an exact "
    "empty result without a ClickHouse read and is_not_null preserves the base "
    "window. Valid contradictions also return an exact empty result."
)
BOUNDED_FILTER_LIST_SCHEMA = {
    **FILTER_LIST_SCHEMA,
    "description": BOUNDED_DATETIME_FILTER_DESCRIPTION,
    "x-boundedDatetimeOperators": sorted(BOUNDED_LIST_DATETIME_FILTER_OPS),
}
BOUNDED_FILTER_LIST_QUERY_PARAM_SCHEMA = {
    "type": "string",
    "description": f"JSON-encoded canonical filter list. {BOUNDED_DATETIME_FILTER_DESCRIPTION}",
    "x-boundedDatetimeOperators": sorted(BOUNDED_LIST_DATETIME_FILTER_OPS),
}
BOUNDED_PAGE_NUMBER_HELP_TEXT = (
    "Zero-based numbered page. Pages whose required ordered work exceeds the "
    "finite read contract return HTTP 422 with code "
    "page_depth_exceeded; request an earlier page or narrow the time range."
)
JSON_OBJECT_QUERY_PARAM_SCHEMA = {
    "type": "string",
    "description": "JSON-encoded object.",
}
JSON_OBJECT_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
}

EVAL_TASK_FILTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {
            "type": "string",
            "x-nullable": True,
            "description": "Project scope for the evaluation task.",
        },
        "date_range": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 2,
            "description": "Half-open [start, end) ISO timestamps, normalized to UTC.",
        },
        "date_preset": {
            "type": "string",
            "enum": [
                "30m",
                "6h",
                "today",
                "yesterday",
                "7d",
                "30d",
                "3m",
                "6m",
                "12m",
                "custom",
            ],
            "description": (
                "Which time-window preset the user chose. The frontend resolves "
                "it to date_range at save time; this records the intent so a "
                "relative window can be re-anchored on the next save. Never read "
                "when building a query — date_range remains authoritative. Absent "
                "on tasks predating this field. The enum documents the accepted "
                "values; it is not enforced."
            ),
        },
        "created_at": {
            "type": "string",
            "description": "Exclusive lower-bound ISO timestamp for legacy task filters, normalized to UTC.",
        },
        "session_id": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Trace session id(s) to constrain the task.",
        },
        "trace_id": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Trace id(s) to constrain linked-source tasks.",
        },
        "span_id": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Observation span id(s) to constrain linked-source tasks.",
        },
        "observation_type": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Observation span type(s), for example llm, tool, or chain.",
        },
        "filters": FILTER_LIST_SCHEMA,
        "span_attributes_filters": FILTER_LIST_SCHEMA,
    },
    "additionalProperties": False,
}

FILTER_ITEM_ALLOWED_KEYS = set(FILTER_ITEM_SCHEMA["properties"])
FILTER_CONFIG_ALLOWED_KEYS = set(FILTER_CONFIG_SCHEMA["properties"])
FILTER_ITEM_REQUIRED_KEYS = set(FILTER_ITEM_SCHEMA["required"])
FILTER_CONFIG_REQUIRED_KEYS = set(FILTER_CONFIG_SCHEMA["required"])
EVAL_TASK_FILTER_ALLOWED_KEYS = set(EVAL_TASK_FILTERS_SCHEMA["properties"])


class PageDepthExceededErrorSerializer(ApiErrorResponseSerializer):
    """Typed, non-retryable envelope for the finite numbered-page ceiling."""

    code = serializers.ChoiceField(choices=("page_depth_exceeded",))


class RejectUnknownFieldsMixin(StrictInputMixin):
    """Backward-compatible name for strict request serializers."""


class ObserveGraphMetricConfigField(serializers.JSONField):
    ALLOWED_KEYS = {
        "id",
        "type",
        "output_type",
        "eval_output_type",
        "choices",
        "value",
        "filter_op",
        "filter_value",
        "property_id",
        "source",
    }

    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": ["SYSTEM_METRIC", "EVAL", "ANNOTATION"],
                },
                "output_type": {"type": "string"},
                "eval_output_type": {"type": "string"},
                "choices": {"type": "array", "items": {"type": "string"}},
                "value": {},
                "filter_op": {"type": "string"},
                "filter_value": {},
                "property_id": {
                    "type": "string",
                    "description": "Stable Property Registry identity.",
                },
                "source": {
                    "type": "string",
                    "enum": ["traces", "sessions"],
                },
            },
            "required": ["id", "type"],
            "additionalProperties": False,
        }

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not isinstance(value, dict):
            raise serializers.ValidationError("req_data_config must be an object.")
        extra_keys = sorted(set(value) - self.ALLOWED_KEYS)
        if extra_keys:
            raise serializers.ValidationError(
                f"Unknown req_data_config keys: {', '.join(extra_keys)}"
            )
        if "id" not in value:
            raise serializers.ValidationError("req_data_config.id is required.")
        if "type" not in value:
            raise serializers.ValidationError("req_data_config.type is required.")
        if value["type"] not in ("SYSTEM_METRIC", "EVAL", "ANNOTATION"):
            raise serializers.ValidationError(
                "req_data_config.type must be SYSTEM_METRIC, EVAL, or ANNOTATION."
            )
        has_property_id = "property_id" in value
        has_source = "source" in value
        if has_property_id != has_source:
            raise serializers.ValidationError(
                "req_data_config.property_id and source must be provided together."
            )
        if has_property_id:
            property_id = value.get("property_id")
            source = value.get("source")
            if not isinstance(property_id, str) or not property_id.strip():
                raise serializers.ValidationError(
                    "req_data_config.property_id must be a non-empty string."
                )
            if not isinstance(source, str) or not source.strip():
                raise serializers.ValidationError(
                    "req_data_config.source must be a non-empty string."
                )
            if source not in ("traces", "sessions"):
                raise serializers.ValidationError(
                    "req_data_config.source must be traces or sessions."
                )
            try:
                validate_property_graph_binding(
                    property_id,
                    metric_name=value["id"],
                    graph_type=value["type"],
                    source=source,
                )
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
        # Both fields absent is the explicit compatibility contract for graph
        # clients predating the Property Registry. Supplying only one is never
        # interpreted as legacy because that could silently change adapters.
        return value


def parse_filter_list_payload(data):
    """Decode the canonical filter-list payload from body or query params."""
    if data in (None, ""):
        return []
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, RecursionError) as exc:
            raise serializers.ValidationError("Filters must be valid JSON.") from exc
    if data is None:
        return []
    if not isinstance(data, list):
        raise serializers.ValidationError("Filters must be a list.")
    return data


def validate_filter_list_complexity(filters: list[Any]) -> None:
    """Reject filter payloads whose finite shape exceeds the public contract."""

    if len(filters) > FILTER_LIST_MAX_ITEMS:
        raise serializers.ValidationError(
            f"At most {FILTER_LIST_MAX_ITEMS} filters may be applied at once."
        )

    total_string_bytes = 0

    def check_string(
        value: str,
        *,
        field: str,
        max_utf8_bytes: int = FILTER_STRING_MAX_UTF8_BYTES,
    ) -> None:
        nonlocal total_string_bytes
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise serializers.ValidationError(f"{field} must be valid UTF-8.") from exc
        if len(encoded) > max_utf8_bytes:
            raise serializers.ValidationError(
                f"{field} must be at most {max_utf8_bytes} "
                f"UTF-8 bytes ({max_utf8_bytes} UTF-8 byte limit)."
            )
        total_string_bytes += len(encoded)
        if total_string_bytes > FILTER_LIST_MAX_TOTAL_STRING_UTF8_BYTES:
            raise serializers.ValidationError(
                "Filter strings exceed the "
                f"{FILTER_LIST_MAX_TOTAL_STRING_UTF8_BYTES} UTF-8 byte request limit."
            )

    def check_value(
        value: Any,
        *,
        field: str,
        depth: int = 0,
        max_string_utf8_bytes: int = FILTER_STRING_MAX_UTF8_BYTES,
    ) -> None:
        if depth > FILTER_VALUE_MAX_DEPTH:
            raise serializers.ValidationError(
                f"{field} supports at most {FILTER_VALUE_MAX_DEPTH} nested levels."
            )
        if isinstance(value, str):
            check_string(
                value,
                field=field,
                max_utf8_bytes=max_string_utf8_bytes,
            )
            return
        if isinstance(value, list):
            if len(value) > FILTER_LIST_MAX_VALUES:
                raise serializers.ValidationError(
                    f"{field} supports at most {FILTER_LIST_MAX_VALUES} values."
                )
            for item in value:
                check_value(
                    item,
                    field=field,
                    depth=depth + 1,
                    max_string_utf8_bytes=max_string_utf8_bytes,
                )
            return
        if isinstance(value, dict):
            if len(value) > FILTER_LIST_MAX_VALUES:
                raise serializers.ValidationError(
                    f"{field} supports at most {FILTER_LIST_MAX_VALUES} object members."
                )
            for key, item in value.items():
                if isinstance(key, str):
                    check_string(key, field=f"{field} key")
                check_value(
                    item,
                    field=field,
                    depth=depth + 1,
                    max_string_utf8_bytes=max_string_utf8_bytes,
                )

    for index, item in enumerate(filters):
        if not isinstance(item, dict):
            # FilterItemField owns the canonical type error. Complexity checking
            # deliberately remains safe for partially malformed input.
            continue
        column_id = item.get("column_id")
        if isinstance(column_id, str):
            check_string(column_id, field=f"Filter {index + 1} column_id")
        for optional_key in (
            "property_id",
            "display_name",
            "source",
            "output_type",
        ):
            optional_value = item.get(optional_key)
            if isinstance(optional_value, str):
                check_string(
                    optional_value,
                    field=f"Filter {index + 1} {optional_key}",
                )
        config = item.get("filter_config")
        if not isinstance(config, dict):
            continue
        try:
            config_bytes = len(
                json.dumps(
                    config,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8", errors="strict")
            )
        except (TypeError, ValueError, UnicodeEncodeError):
            # Canonical value/type validators below provide the precise error.
            config_bytes = 0
        if config_bytes > FILTER_CONFIG_MAX_UTF8_BYTES:
            raise serializers.ValidationError(
                "Filter config exceeds the "
                f"{FILTER_CONFIG_MAX_UTF8_BYTES} UTF-8 byte request limit."
            )
        for config_key in ("filter_type", "filter_op", "col_type"):
            config_value = config.get(config_key)
            if isinstance(config_value, str):
                check_string(
                    config_value,
                    field=f"Filter {index + 1} {config_key}",
                )
        if "filter_value" in config:
            filter_value = config["filter_value"]
            attribute_value_types = config.get("attribute_value_types")
            has_aligned_picker_provenance = bool(
                config.get("col_type") == "SPAN_ATTRIBUTE"
                and config.get("filter_op") in LIST_FILTER_OPS
                and isinstance(filter_value, list)
                and isinstance(attribute_value_types, list)
                and len(filter_value) == len(attribute_value_types)
            )
            if has_aligned_picker_provenance:
                if len(filter_value) > FILTER_LIST_MAX_VALUES:
                    raise serializers.ValidationError(
                        f"Filter {index + 1} value supports at most "
                        f"{FILTER_LIST_MAX_VALUES} values."
                    )
                for selected_value, selected_type in zip(
                    filter_value, attribute_value_types, strict=True
                ):
                    check_value(
                        selected_value,
                        field=f"Filter {index + 1} value",
                        depth=1,
                        max_string_utf8_bytes=(
                            TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
                            if selected_type == "string"
                            else FILTER_STRING_MAX_UTF8_BYTES
                        ),
                    )
            else:
                check_value(
                    filter_value,
                    field=f"Filter {index + 1} value",
                )
        if "attribute_value_types" in config:
            check_value(
                config["attribute_value_types"],
                field=f"Filter {index + 1} attribute value types",
            )


class FilterItemField(serializers.JSONField):
    """JSON field with explicit OpenAPI shape for a single filter item.

    Runtime validation is intentionally strict, matching the generated API
    schema pattern used by mature schema-first systems: callers send the
    canonical snake_case filter contract or get a validation error.
    """

    class Meta:
        swagger_schema_fields = FILTER_ITEM_SCHEMA

    allow_session_numeric_membership = False

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not isinstance(value, dict):
            raise serializers.ValidationError("Filter item must be an object.")

        missing_keys = sorted(FILTER_ITEM_REQUIRED_KEYS - set(value))
        if missing_keys:
            raise serializers.ValidationError(
                f"Missing filter item keys: {', '.join(missing_keys)}"
            )

        extra_keys = sorted(set(value) - FILTER_ITEM_ALLOWED_KEYS)
        if extra_keys:
            raise serializers.ValidationError(
                f"Unknown filter item keys: {', '.join(extra_keys)}"
            )

        column_id = value.get("column_id")
        if not isinstance(column_id, str) or not column_id.strip():
            raise serializers.ValidationError("column_id must be a non-empty string.")

        config = value.get("filter_config")
        if not isinstance(config, dict):
            raise serializers.ValidationError("Filter config must be an object.")

        missing_config_keys = sorted(FILTER_CONFIG_REQUIRED_KEYS - set(config))
        if missing_config_keys:
            raise serializers.ValidationError(
                f"Missing filter config keys: {', '.join(missing_config_keys)}"
            )

        extra_config_keys = sorted(set(config) - FILTER_CONFIG_ALLOWED_KEYS)
        if extra_config_keys:
            raise serializers.ValidationError(
                f"Unknown filter config keys: {', '.join(extra_config_keys)}"
            )

        property_id = value.get("property_id")
        if property_id is not None:
            if not isinstance(property_id, str) or not property_id.strip():
                raise serializers.ValidationError(
                    "property_id must be a non-empty string."
                )
            try:
                validate_property_filter_binding(
                    property_id,
                    column_id=value.get("column_id"),
                    column_type=config.get("col_type"),
                    source=value.get("source"),
                )
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc

        filter_value = config.get("filter_value")
        attribute_value_types = config.get("attribute_value_types")
        is_span_attribute = config.get("col_type") == "SPAN_ATTRIBUTE"
        filter_type = (
            normalize_span_attribute_filter_type(
                config.get("filter_type"), filter_value
            )
            if is_span_attribute
            else normalize_filter_type(config.get("filter_type"))
        )
        config["filter_type"] = filter_type
        filter_op = config.get("filter_op")
        allowed_ops = (
            STRUCTURED_SPAN_ATTR_ALLOWED_OPS.get(filter_type)
            if is_span_attribute and filter_type in STRUCTURED_SPAN_ATTR_ALLOWED_OPS
            else FILTER_TYPE_ALLOWED_OPS.get(filter_type)
        )
        if allowed_ops is None:
            raise serializers.ValidationError(
                f"Unsupported filter_type {filter_type!r}."
            )
        if filter_op not in allowed_ops and not filter_op_is_allowed(
            filter_type,
            filter_op,
            column_id=value.get("column_id"),
            column_type=config.get("col_type"),
            allow_session_numeric_membership=self.allow_session_numeric_membership,
        ):
            raise serializers.ValidationError(
                f"Unsupported filter_op {filter_op!r} for filter_type {filter_type!r}."
            )

        if filter_op in RANGE_FILTER_OPS:
            if not isinstance(filter_value, list) or len(filter_value) != 2:
                raise serializers.ValidationError(
                    f"{filter_op!r} requires a two-value filter_value list."
                )
        elif filter_op in LIST_FILTER_OPS:
            if not isinstance(filter_value, list) or not filter_value:
                raise serializers.ValidationError(
                    f"{filter_op!r} requires a non-empty filter_value list."
                )
        elif filter_op not in NO_VALUE_FILTER_OPS and "filter_value" not in config:
            raise serializers.ValidationError(f"{filter_op!r} requires filter_value.")

        is_session_numeric_membership = (
            self.allow_session_numeric_membership
            and filter_type == "number"
            and filter_op in LIST_FILTER_OPS
            and config.get("col_type") == "SYSTEM_METRIC"
        )
        if is_session_numeric_membership:
            normalized_values = []
            for item in filter_value:
                if isinstance(item, bool):
                    raise serializers.ValidationError(
                        "Session numeric membership values must be finite numbers."
                    )
                try:
                    normalized_item = float(item)
                except (TypeError, ValueError, OverflowError):
                    raise serializers.ValidationError(
                        "Session numeric membership values must be finite numbers."
                    ) from None
                if not math.isfinite(normalized_item):
                    raise serializers.ValidationError(
                        "Session numeric membership values must be finite numbers."
                    )
                normalized_values.append(normalized_item)
            config["filter_value"] = normalized_values
            filter_value = normalized_values

        if attribute_value_types is not None:
            if not is_span_attribute or filter_op not in LIST_FILTER_OPS:
                raise serializers.ValidationError(
                    "attribute_value_types is only supported for SPAN_ATTRIBUTE "
                    "in/not_in filters."
                )
            if (
                not isinstance(attribute_value_types, list)
                or not isinstance(filter_value, list)
                or len(attribute_value_types) != len(filter_value)
            ):
                raise serializers.ValidationError(
                    "attribute_value_types must align one-for-one with filter_value."
                )
            if any(
                value not in (None, "string", "number", "boolean")
                for value in attribute_value_types
            ):
                raise serializers.ValidationError(
                    "attribute_value_types entries must be string, number, "
                    "boolean, or null."
                )

        if is_span_attribute:
            # Structured span values live in the direct-write JSON overflow.
            # Their canonical operator vocabulary already exists in the
            # shared contract, while the legacy typed-Map span contract stays
            # intentionally scalar-only.
            span_allowed_ops = (
                STRUCTURED_SPAN_ATTR_ALLOWED_OPS[filter_type]
                if filter_type in STRUCTURED_SPAN_ATTR_ALLOWED_OPS
                else SPAN_ATTR_ALLOWED_OPS.get(filter_type)
            )
            if span_allowed_ops is None:
                raise serializers.ValidationError(
                    f"Unsupported filter_type {filter_type!r} for SPAN_ATTRIBUTE."
                )
            if filter_op not in span_allowed_ops:
                raise serializers.ValidationError(
                    f"Unsupported filter_op {filter_op!r} for SPAN_ATTRIBUTE "
                    f"filter_type {filter_type!r}."
                )

            if filter_op not in NO_VALUE_FILTER_OPS:
                if filter_type == "array":
                    if not isinstance(filter_value, list) or not filter_value:
                        raise serializers.ValidationError(
                            "Array SPAN_ATTRIBUTE filters require a non-empty "
                            "list of JSON scalar values."
                        )
                    if len(filter_value) > JSON_ARRAY_FILTER_MAX_MEMBERS:
                        raise serializers.ValidationError(
                            "Array SPAN_ATTRIBUTE filters support at most "
                            f"{JSON_ARRAY_FILTER_MAX_MEMBERS} selected values."
                        )
                    total_string_bytes = 0
                    for item in filter_value:
                        if item is None or item == "":
                            raise serializers.ValidationError(
                                "Array SPAN_ATTRIBUTE values must be non-empty "
                                "JSON scalars."
                            )
                        if isinstance(item, str):
                            item_bytes = len(item.encode("utf-8"))
                            if item_bytes > JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES:
                                raise serializers.ValidationError(
                                    "Array SPAN_ATTRIBUTE string values must be at "
                                    f"most {JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES} "
                                    "UTF-8 bytes."
                                )
                            total_string_bytes += item_bytes
                            if (
                                total_string_bytes
                                > JSON_ARRAY_FILTER_MAX_TOTAL_STRING_UTF8_BYTES
                            ):
                                raise serializers.ValidationError(
                                    "Array SPAN_ATTRIBUTE string values exceed the "
                                    f"{JSON_ARRAY_FILTER_MAX_TOTAL_STRING_UTF8_BYTES} "
                                    "UTF-8 byte request limit."
                                )
                            continue
                        if isinstance(item, bool):
                            continue
                        if isinstance(item, int):
                            if not (-(1 << 63) <= item <= (1 << 64) - 1):
                                raise serializers.ValidationError(
                                    "Array SPAN_ATTRIBUTE integers must fit "
                                    "Int64 or UInt64."
                                )
                            continue
                        if isinstance(item, float):
                            try:
                                numeric_item = float(item)
                            except (TypeError, ValueError, OverflowError):
                                raise serializers.ValidationError(
                                    "Array SPAN_ATTRIBUTE numbers must be finite."
                                ) from None
                            if not math.isfinite(numeric_item):
                                raise serializers.ValidationError(
                                    "Array SPAN_ATTRIBUTE numbers must be finite."
                                )
                            continue
                        raise serializers.ValidationError(
                            "Nested JSON filter values are not supported for "
                            "Array SPAN_ATTRIBUTE filters."
                        )
                    values_to_check = filter_value
                elif filter_type == "map":
                    try:
                        config["filter_value"] = validate_json_map_filter_value(
                            filter_value
                        )
                    except ValueError as exc:
                        raise serializers.ValidationError(str(exc)) from exc
                    # The shared map validator has already checked every
                    # member's type/range/UTF-8 bounds.
                    values_to_check = []
                elif attribute_value_types is not None:
                    if filter_type not in {"text", "number", "boolean"}:
                        raise serializers.ValidationError(
                            "Mixed typed value provenance is only supported for "
                            "text, number, or boolean SPAN_ATTRIBUTE filters."
                        )
                    fallback_type = {
                        "text": "string",
                        "number": "number",
                        "boolean": "boolean",
                    }[filter_type]
                    normalized_values = []
                    for item, storage_type in zip(
                        filter_value, attribute_value_types, strict=True
                    ):
                        effective_type = storage_type or fallback_type
                        if effective_type == "string":
                            if not isinstance(item, str):
                                raise serializers.ValidationError(
                                    "String SPAN_ATTRIBUTE values must be strings."
                                )
                            normalized_values.append(item)
                        elif effective_type == "number":
                            if isinstance(item, bool):
                                raise serializers.ValidationError(
                                    "Number SPAN_ATTRIBUTE values must be finite numbers."
                                )
                            try:
                                numeric_item = float(item)
                            except (TypeError, ValueError, OverflowError):
                                raise serializers.ValidationError(
                                    "Number SPAN_ATTRIBUTE values must be finite numbers."
                                ) from None
                            if not math.isfinite(numeric_item):
                                raise serializers.ValidationError(
                                    "Number SPAN_ATTRIBUTE values must be finite numbers."
                                )
                            normalized_values.append(numeric_item)
                        else:
                            if not isinstance(item, bool):
                                raise serializers.ValidationError(
                                    "Boolean SPAN_ATTRIBUTE values must be true or false."
                                )
                            normalized_values.append(item)
                    config["filter_value"] = normalized_values
                    values_to_check = []
                elif filter_op in RANGE_FILTER_OPS | LIST_FILTER_OPS:
                    values_to_check = filter_value
                else:
                    values_to_check = [filter_value]

                if any(item is None for item in values_to_check):
                    raise serializers.ValidationError(
                        f"{filter_op!r} requires non-null SPAN_ATTRIBUTE values."
                    )
                if filter_type == "text" and any(
                    not isinstance(item, str) for item in values_to_check
                ):
                    raise serializers.ValidationError(
                        "Text SPAN_ATTRIBUTE values must be strings."
                    )
                if filter_type == "number":
                    normalized_values = []
                    for item in values_to_check:
                        if isinstance(item, bool):
                            raise serializers.ValidationError(
                                "Number SPAN_ATTRIBUTE values must be finite numbers."
                            )
                        try:
                            normalized_item = float(item)
                        except (TypeError, ValueError, OverflowError):
                            raise serializers.ValidationError(
                                "Number SPAN_ATTRIBUTE values must be finite numbers."
                            ) from None
                        if not math.isfinite(normalized_item):
                            raise serializers.ValidationError(
                                "Number SPAN_ATTRIBUTE values must be finite numbers."
                            )
                        normalized_values.append(normalized_item)
                    config["filter_value"] = (
                        normalized_values
                        if filter_op in RANGE_FILTER_OPS | LIST_FILTER_OPS
                        else normalized_values[0]
                    )
                if filter_type == "boolean" and any(
                    not isinstance(item, bool) for item in values_to_check
                ):
                    raise serializers.ValidationError(
                        "Boolean SPAN_ATTRIBUTE values must be true or false."
                    )

        return value


class SessionFilterItemField(FilterItemField):
    """Filter item with the bounded session-aggregate membership extension."""

    allow_session_numeric_membership = True


class FilterListField(serializers.ListField):
    """List wrapper that carries the exact filter-item OpenAPI shape.

    drf-yasg treats bare JSONField children as open-ended objects even when the
    child has swagger_schema_fields. Defining the array schema on the list field
    keeps the generated contract aligned with the runtime validator.
    """

    child = FilterItemField()

    class Meta:
        swagger_schema_fields = FILTER_LIST_SCHEMA

    def to_internal_value(self, data):
        parsed = parse_filter_list_payload(data)
        validate_filter_list_complexity(parsed)
        return super().to_internal_value(parsed)


class SessionFilterListField(FilterListField):
    """Session-only filter list; all other surfaces retain the FE contract."""

    child = SessionFilterItemField()


class BoundedFilterListField(FilterListField):
    """Filter list whose datetime predicates fit one prunable interval."""

    class Meta:
        swagger_schema_fields = BOUNDED_FILTER_LIST_SCHEMA

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        # Import lazily so serializer/OpenAPI module discovery does not need to
        # initialize the ClickHouse service package. The builder remains the
        # single source of truth for DateTime64(6) boundary normalization.
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        try:
            BaseQueryBuilder.parse_time_range(value, strict=True)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value


class SessionBoundedFilterListField(BoundedFilterListField):
    """Bounded session-only filter list with numeric aggregate membership."""

    child = SessionFilterItemField()


class FilterListQueryParamField(serializers.CharField):
    """Query-param version of FilterListField.

    Query strings carry filters as JSON text (`filters=[...]`). The runtime
    validator still parses and checks the canonical filter-list shape, while
    OpenAPI correctly advertises a string parameter instead of an array of
    repeated query params.
    """

    class Meta:
        swagger_schema_fields = FILTER_LIST_QUERY_PARAM_SCHEMA

    def to_internal_value(self, data):
        return FilterListField().run_validation(data)


class SessionFilterListQueryParamField(FilterListQueryParamField):
    def to_internal_value(self, data):
        return SessionFilterListField().run_validation(data)


class BoundedFilterListQueryParamField(FilterListQueryParamField):
    """List filter contract whose time predicate is one prunable interval."""

    class Meta:
        swagger_schema_fields = BOUNDED_FILTER_LIST_QUERY_PARAM_SCHEMA

    def to_internal_value(self, data):
        return BoundedFilterListField().run_validation(data)


class SessionBoundedFilterListQueryParamField(BoundedFilterListQueryParamField):
    def to_internal_value(self, data):
        return SessionBoundedFilterListField().run_validation(data)


class JsonObjectQueryParamField(serializers.Field):
    class Meta:
        swagger_schema_fields = JSON_OBJECT_QUERY_PARAM_SCHEMA

    def to_internal_value(self, data):
        if data in (None, ""):
            return {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError("Value must be valid JSON.") from exc
        if not isinstance(data, dict):
            raise serializers.ValidationError("Value must be an object.")
        return data

    def to_representation(self, value):
        return value or {}


class JsonObjectField(serializers.JSONField):
    """Body/response JSON object field with explicit OpenAPI shape.

    DRF's DictField(child=JSONField) validates runtime data correctly, but it
    documents nested values as objects only. Eval configs and mappings are JSON
    objects whose values can be strings, numbers, booleans, arrays, or objects,
    so expose that contract directly.
    """

    class Meta:
        swagger_schema_fields = JSON_OBJECT_SCHEMA

    def to_internal_value(self, data):
        if data == "":
            return {}
        value = super().to_internal_value(data)
        if value is None:
            return None if self.allow_null else {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("Value must be an object.")
        return value

    def to_representation(self, value):
        if value is None and self.allow_null:
            return None
        return value or {}


class SortParamField(serializers.JSONField):
    ALLOWED_KEYS = {"column_id", "direction"}
    REQUIRED_KEYS = {"column_id"}

    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "properties": {
                "column_id": {"type": "string"},
                "direction": {"type": "string", "enum": ["asc", "desc"]},
            },
            "required": ["column_id"],
            "additionalProperties": False,
        }

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not isinstance(value, dict):
            raise serializers.ValidationError("Sort item must be an object.")
        missing = sorted(self.REQUIRED_KEYS - set(value))
        if missing:
            raise serializers.ValidationError(
                f"Missing sort item keys: {', '.join(missing)}"
            )
        extra = sorted(set(value) - self.ALLOWED_KEYS)
        if extra:
            raise serializers.ValidationError(
                f"Unknown sort item keys: {', '.join(extra)}"
            )
        direction = value.get("direction", "desc")
        if direction not in ("asc", "desc"):
            raise serializers.ValidationError("direction must be 'asc' or 'desc'.")
        return {"column_id": value["column_id"], "direction": direction}


class SortParamListQueryParamField(serializers.CharField):
    class Meta:
        swagger_schema_fields = {
            "type": "string",
            "description": "JSON-encoded list of sort params.",
        }

    def to_internal_value(self, data):
        sort_params = parse_filter_list_payload(data)
        return serializers.ListField(child=SortParamField()).run_validation(sort_params)


class MetricSortParamField(SortParamField):
    ALLOWED_KEYS = {"column_id", "direction", "col_type"}

    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "properties": {
                "column_id": {"type": "string"},
                "direction": {"type": "string", "enum": ["asc", "desc"]},
                "col_type": {"type": "string"},
            },
            "required": ["column_id"],
            "additionalProperties": False,
        }

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        raw = data if isinstance(data, dict) else {}
        if raw.get("col_type"):
            value["col_type"] = raw["col_type"]
        return value


class MetricSortParamListField(serializers.ListField):
    child = MetricSortParamField()


class MetricSortParamListQueryParamField(serializers.CharField):
    class Meta:
        swagger_schema_fields = {
            "type": "string",
            "description": "JSON-encoded list of metric sort params.",
        }

    def to_internal_value(self, data):
        sort_params = parse_filter_list_payload(data)
        return MetricSortParamListField().run_validation(sort_params)


class ObserveGraphDataRequestSerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    filters = BoundedFilterListField(required=False, default=list)
    interval = serializers.ChoiceField(
        choices=["hour", "day", "week", "month"],
        required=False,
        default="day",
    )
    property = serializers.CharField(
        required=False, allow_blank=True, default="average"
    )
    req_data_config = ObserveGraphMetricConfigField()


class ObserveGraphDataQuerySerializer(StrictInputSerializer):
    allow_sampled = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "Deprecated compatibility parameter. Observe graphs always return "
            "complete exact data or a retryable error."
        ),
    )
    refresh = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Recompute and atomically replace the last complete exact result.",
    )


class ObserveGraphDataPointSerializer(serializers.Serializer):
    """One exact, rollup-derived, or explicitly sampled graph point."""

    timestamp = serializers.CharField()
    value = serializers.FloatField(allow_null=True)
    primary_traffic = serializers.FloatField(required=False, allow_null=True)


class ObserveGraphDataResultSerializer(serializers.Serializer):
    metric_name = serializers.CharField(allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    data = ObserveGraphDataPointSerializer(
        many=True,
        help_text=(
            "Graph points. A sampled series is published only with complete "
            "declared stratum coverage; degraded reads never publish points."
        ),
    )
    query_complete = serializers.BooleanField(required=False)
    query_exact = serializers.BooleanField(required=False)
    query_provenance = serializers.ChoiceField(
        choices=("materialized_rollup", "bounded_candidates", "exact_snapshot"),
        required=False,
    )
    query_status = serializers.ChoiceField(
        choices=("complete", "sampled", "degraded", "pending"), required=False
    )
    query_error_code = serializers.ChoiceField(
        choices=("sample_limit", "read_budget_exceeded", "query_failed"),
        required=False,
    )
    query_window_start = serializers.CharField(required=False)
    query_window_end = serializers.CharField(required=False)
    query_applied_filter_version = serializers.ChoiceField(
        choices=("canonical-json-sha256-v1",), required=False
    )
    query_applied_filter_sha256 = serializers.RegexField(
        r"^[0-9a-f]{64}$", required=False
    )
    query_applied_filter_count = serializers.IntegerField(required=False, min_value=0)
    query_sample_size = serializers.IntegerField(required=False, min_value=0)
    query_count = serializers.IntegerField(required=False, min_value=0)
    query_elapsed_ms = serializers.FloatField(required=False, min_value=0)
    query_rows_returned = serializers.IntegerField(required=False, min_value=0)
    query_result_bytes = serializers.IntegerField(required=False, min_value=0)
    query_total_rows_lower_bound = serializers.IntegerField(required=False, min_value=0)
    query_sampled = serializers.BooleanField(required=False)
    query_completed_at = serializers.DateTimeField(required=False)
    query_cached = serializers.BooleanField(required=False)
    query_refresh_failed = serializers.BooleanField(required=False)
    query_refreshing = serializers.BooleanField(required=False)
    query_snapshot_version_ceiling = serializers.IntegerField(
        required=False, min_value=1
    )
    query_sampling_strategy = serializers.ChoiceField(
        choices=(
            "time_stratified_latest_state",
            "bounded_latest_state_prefix",
            "newest_trace_candidates",
        ),
        required=False,
    )
    query_sampling_strata = serializers.IntegerField(required=False, min_value=0)
    query_sampling_strata_completed = serializers.IntegerField(
        required=False, min_value=0
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        incomplete = (
            attrs.get("query_complete") is False
            or attrs.get("query_status") == "degraded"
        )
        explicitly_sampled = attrs.get("query_status") == "sampled"
        if incomplete and not explicitly_sampled and attrs.get("data"):
            raise serializers.ValidationError(
                {
                    "data": (
                        "Incomplete graph reads must be explicitly sampled "
                        "before publishing graph points."
                    )
                }
            )
        if explicitly_sampled and attrs.get("query_complete") is not False:
            raise serializers.ValidationError(
                {"query_complete": "Sampled graph reads must be incomplete."}
            )
        if explicitly_sampled:
            planned = attrs.get("query_sampling_strata")
            completed = attrs.get("query_sampling_strata_completed")
            if (
                not attrs.get("query_sampling_strategy")
                or not isinstance(planned, int)
                or planned < 1
                or completed != planned
            ):
                raise serializers.ValidationError(
                    {
                        "query_sampling_strata_completed": (
                            "Sampled graph reads require complete coverage of "
                            "every declared sampling stratum."
                        )
                    }
                )
        return attrs


class ObserveGraphDataResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ObserveGraphDataResultSerializer()


class ObserveGraphDataErrorResultSerializer(ObserveGraphDataResultSerializer):
    message = serializers.CharField()


class ObserveGraphDataErrorResponseSerializer(ApiErrorResponseSerializer):
    """Retryable graph error envelope with typed degradation metadata."""

    result = ObserveGraphDataErrorResultSerializer()


class EvalTaskFiltersField(serializers.JSONField):
    """Strict serializer for the saved EvalTask filter object.

    Eval tasks store a small wrapper object around canonical filter lists
    because the dispatcher needs task-scoping keys (`project_id`, `date_range`)
    alongside span attribute filters. Keep that wrapper typed and reject unknown
    keys instead of silently dropping them in `parsing_evaltask_filters`.
    """

    class Meta:
        swagger_schema_fields = EVAL_TASK_FILTERS_SCHEMA

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("Eval task filters must be an object.")

        extra_keys = sorted(set(value) - EVAL_TASK_FILTER_ALLOWED_KEYS)
        if extra_keys:
            raise serializers.ValidationError(
                f"Unknown eval task filter keys: {', '.join(extra_keys)}"
            )

        bounded_time_filters = []
        if "date_range" in value:
            date_range = value["date_range"]
            if not isinstance(date_range, list) or len(date_range) != 2:
                raise serializers.ValidationError(
                    "date_range must be a two-value list."
                )
            bounded_time_filters.append(
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": date_range,
                    },
                }
            )

        if "created_at" in value:
            created_at = value["created_at"]
            if created_at in (None, ""):
                raise serializers.ValidationError(
                    "created_at must be a valid ISO-8601 timestamp."
                )
            bounded_time_filters.append(
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "greater_than",
                        "filter_value": created_at,
                    },
                }
            )

        if bounded_time_filters:
            from tracer.services.clickhouse.query_builders.base import (
                BaseQueryBuilder,
            )

            try:
                # Validate the combined interval once so a individually valid
                # legacy floor cannot make date_range empty after intersection.
                BaseQueryBuilder.parse_time_range(bounded_time_filters, strict=True)

                def normalize_utc(raw_value):
                    exact_start, _ = BaseQueryBuilder.parse_time_range(
                        [
                            {
                                "column_id": "created_at",
                                "filter_config": {
                                    "filter_type": "datetime",
                                    "filter_op": "equals",
                                    "filter_value": raw_value,
                                },
                            }
                        ],
                        strict=True,
                    )
                    return f"{exact_start.isoformat()}Z"

                if "date_range" in value:
                    value["date_range"] = [
                        normalize_utc(item) for item in value["date_range"]
                    ]
                if "created_at" in value:
                    value["created_at"] = normalize_utc(value["created_at"])
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc

        for key in ("session_id", "trace_id", "span_id", "observation_type"):
            filter_value = value.get(key)
            if filter_value is None:
                continue
            if isinstance(filter_value, str):
                filter_value = [filter_value]
                value[key] = filter_value
            if not isinstance(filter_value, list) or not all(
                isinstance(item, str) and item for item in filter_value
            ):
                raise serializers.ValidationError(
                    f"{key} must be a list of non-empty strings."
                )

        for filter_list_key in ("filters", "span_attributes_filters"):
            if filter_list_key in value:
                value[filter_list_key] = BoundedFilterListField().run_validation(
                    value[filter_list_key]
                )

        return value


def filter_list_field(**kwargs):
    return FilterListField(**kwargs)


def filter_list_query_param_field(**kwargs):
    return FilterListQueryParamField(**kwargs)


def bounded_filter_list_query_param_field(
    **kwargs: Any,
) -> BoundedFilterListQueryParamField:
    return BoundedFilterListQueryParamField(**kwargs)


def session_filter_list_query_param_field(**kwargs):
    return SessionFilterListQueryParamField(**kwargs)


def session_bounded_filter_list_field(**kwargs):
    return SessionBoundedFilterListField(**kwargs)


def session_bounded_filter_list_query_param_field(
    **kwargs: Any,
) -> SessionBoundedFilterListQueryParamField:
    return SessionBoundedFilterListQueryParamField(**kwargs)


def eval_task_filters_field(**kwargs):
    return EvalTaskFiltersField(**kwargs)


def json_object_query_param_field(**kwargs):
    return JsonObjectQueryParamField(**kwargs)


def json_object_field(**kwargs):
    return JsonObjectField(**kwargs)
