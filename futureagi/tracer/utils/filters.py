import operator
import uuid
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from functools import reduce

from django.db.models import Exists, F, FloatField, OuterRef, Q, TextField, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Round
from django.utils.dateparse import parse_datetime as django_parse_datetime

from tracer.models.observability_provider import ProviderChoices
from tracer.utils.helper import extract_date


def _parse_datetime_value(val):
    """Parse a datetime value into a UTC-naive datetime.

    The frontend now sends offset-aware ISO strings reflecting the user's
    local timezone (e.g. ``2026-05-06T00:00:00+05:30``). We must convert to
    UTC before stripping tzinfo — otherwise a 5.5h silent skew creeps in for
    IST users picking "today". Naive strings (``2026-05-06T00:00:00``) and
    Z-suffixed strings (``2026-05-06T00:00:00.000Z``) are treated as already
    UTC, matching CH's storage convention.
    """
    if isinstance(val, datetime):
        if val.tzinfo is not None:
            val = val.astimezone(timezone.utc).replace(tzinfo=None)
        return val
    if isinstance(val, str):
        parsed = django_parse_datetime(val)
        if parsed is not None:
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        # Fallback: try extracting date via the existing helper.
        try:
            extracted = extract_date(val)
            if extracted is not None:
                return datetime.combine(extracted, datetime.min.time())
        except (ValueError, TypeError):
            pass
    return None


# Module-level constant for datetime filter operations
_CREATED_AT_OP_MAP = {
    "between": lambda qs, val: (
        qs.filter(created_at__gte=val[0], created_at__lte=val[1])
        if isinstance(val, (list, tuple)) and len(val) == 2
        else qs
    ),
    "not_in_between": lambda qs, val: (
        qs.exclude(created_at__gte=val[0], created_at__lte=val[1])
        if isinstance(val, (list, tuple)) and len(val) == 2
        else qs
    ),
    "greater_than": lambda qs, val: qs.filter(created_at__gt=val),
    "less_than": lambda qs, val: qs.filter(created_at__lt=val),
    "greater_than_or_equal": lambda qs, val: qs.filter(created_at__gte=val),
    "less_than_or_equal": lambda qs, val: qs.filter(created_at__lte=val),
    "equals": lambda qs, val: qs.filter(
        created_at__date=val.date() if isinstance(val, datetime) else val
    ),
    "not_equals": lambda qs, val: qs.exclude(
        created_at__date=val.date() if isinstance(val, datetime) else val
    ),
}


def apply_created_at_filters(qs, filters):
    """
    Apply created_at datetime filters to a queryset.
    Returns (filtered_qs, remaining_filters).
    """
    applied = set()
    for i, f in enumerate(filters):
        cfg = f.get("filter_config") or f.get("filterConfig") or {}
        if cfg.get("filter_type") != "datetime":
            continue
        op = cfg.get("filter_op")
        val = cfg.get("filter_value")
        if val is None:
            continue

        # Parse datetime values
        if isinstance(val, (list, tuple)):
            parsed = [_parse_datetime_value(v) for v in val]
            if any(p is None for p in parsed):
                continue
            val = parsed
        else:
            parsed = _parse_datetime_value(val)
            if parsed is None:
                continue
            val = parsed
        handler = _CREATED_AT_OP_MAP.get(op)
        if handler:
            qs = handler(qs, val)
            applied.add(i)
    remaining = [f for i, f in enumerate(filters) if i not in applied]
    return qs, remaining


class ColType(Enum):
    EVAL_METRIC = "EVAL_METRIC"
    SYSTEM_METRIC = "SYSTEM_METRIC"
    NORMAL = "NORMAL"
    ANNOTATION = "ANNOTATION"
    ANNOTATION_RUNS = "ANNOTATION_RUNS"
    VOICE_ANNOTATION = "VOICE_ANNOTATION"
    SPAN_ATTRIBUTE = "SPAN_ATTRIBUTE"
    PROMPT_METRIC = "PROMPT_METRIC"
    PROMPT_METRIC_RUNS = "PROMPT_METRIC_RUNS"


class FilterEngine:
    DEFAULT_FIELD_MAP = {
        "avg_cost": "row_avg_cost",
        "avg_latency": "row_avg_latency_ms",
        "latency": "latency_ms",
        "node_type": "node_type",
        "trace_id": "trace_id",
        "span_id": "id",
        "created_at": "created_at",
        "run_name": "run_name",
        "span_name": "span_name",
        "trace_name": "trace_name",
        "session_id": "session_id",
        "prompt_template_version": "prompt_template_version",
        "labels": "labels",
        "unique_traces": "unique_traces",
        "first_used": "first_used",
        "last_used": "last_used",
        "avg_input_tokens": "avg_input_tokens",
        "avg_output_tokens": "avg_output_tokens",
        "user_id": "user_id",
        "status": "status",
        "start_time": "start_time",
        "prompt_label_id": "prompt_label_id",
        "prompt_label_name": "prompt_label_name",
    }

    # Voice system metric IDs that need custom SYSTEM_METRIC handling.
    # These are filtered via JSON extraction from span_attributes rather
    # than direct column lookups.
    VOICE_SYSTEM_METRIC_IDS = {
        "turn_count",
        "agent_talk_percentage",
        "avg_agent_latency_ms",
        "bot_wpm",
        "user_wpm",
        "user_interruption_count",
        "ai_interruption_count",
    }

    VOICE_METRIC_DEFINITIONS = {
        "turn_count": {
            "json_keys": ["call.total_turns"],
            "annotation": "_voice_turn_count",
            "output_field": FloatField(),
        },
        "agent_talk_percentage": {
            "json_keys": ["call.talk_ratio"],
            "annotation": "_voice_agent_talk_pct",
            "output_field": FloatField(),
            "is_computed_percentage": True,
        },
        "avg_agent_latency_ms": {
            "json_keys": ["avg_agent_latency_ms"],
            "annotation": "_voice_avg_latency",
            "output_field": FloatField(),
        },
        "bot_wpm": {
            "json_keys": ["call.bot_wpm"],
            "annotation": "_voice_bot_wpm",
            "output_field": FloatField(),
        },
        "user_wpm": {
            "json_keys": ["call.user_wpm"],
            "annotation": "_voice_user_wpm",
            "output_field": FloatField(),
        },
        "user_interruption_count": {
            "json_keys": ["user_interruption_count"],
            "annotation": "_voice_user_interruptions",
            "output_field": FloatField(),
        },
        "ai_interruption_count": {
            "json_keys": ["ai_interruption_count"],
            "annotation": "_voice_ai_interruptions",
            "output_field": FloatField(),
        },
    }

    def __init__(self, objects):
        self.objects = objects

    @staticmethod
    def _validate_and_convert_filter_value(filter_value, filter_type, filter_op):
        """
        Validate and convert filter values to appropriate types.

        Args:
            filter_value: The raw filter value from request
            filter_type: Type of filter (number, text, boolean, etc.)
            filter_op: Filter operation (equals, between, etc.)

        Returns:
            tuple: (is_valid, converted_value)
        """
        try:
            if filter_type == "number":
                if filter_op in ["between", "not_in_between"]:
                    if not (isinstance(filter_value, list) and len(filter_value) == 2):
                        return False, None
                    return True, [float(filter_value[0]), float(filter_value[1])]
                else:
                    return True, float(filter_value)
            elif filter_type == "boolean":
                if isinstance(filter_value, str):
                    return True, filter_value.lower() == "true"
                return True, bool(filter_value)
            elif filter_type in ["text", "array"]:
                return True, filter_value
            else:
                return True, filter_value
        except (ValueError, TypeError, IndexError):
            return False, None

    @staticmethod
    def _normalize_filter_params(filter_item):
        """
        Normalize filter parameters to handle both camelCase and snake_case.

        Args:
            filter_item: Dictionary containing filter parameters

        Returns:
            tuple: (column_id, filter_config) with normalized parameter names
        """
        column_id = filter_item.get("columnId") or filter_item.get("column_id")
        filter_config = filter_item.get("filterConfig") or filter_item.get(
            "filter_config", {}
        )
        return column_id, filter_config

    def apply_filters(self, filters):
        filtered_objects = self.objects

        for filter_item in filters:
            column_id = filter_item.get("column_id")
            filter_config = filter_item.get("filter_config", {})
            col_type = filter_config.get("col_type", ColType.NORMAL)

            if isinstance(col_type, str):
                col_type = ColType[col_type]

            if not column_id or not filter_config:
                continue

            filter_type = filter_config.get("filter_type")
            filter_op = filter_config.get("filter_op")
            filter_value = filter_config.get("filter_value")
            if filter_type == "number":
                filtered_objects = self._filter_number(
                    filtered_objects, column_id, filter_op, filter_value, col_type
                )
            elif filter_type in ["text", "array"]:
                filtered_objects = self._filter_text(
                    filtered_objects, column_id, filter_op, filter_value, col_type
                )
            elif filter_type == "boolean":
                filtered_objects = self._filter_boolean(
                    filtered_objects, column_id, filter_value, col_type
                )
            elif filter_type == "datetime":
                filtered_objects = self._filter_datetime(
                    filtered_objects, column_id, filter_op, filter_value, col_type
                )
            elif filter_type == "array":
                filtered_objects = self._filter_array(
                    filtered_objects, column_id, filter_op, filter_value, col_type
                )
            else:
                raise ValueError(f"Invalid filter type: {filter_type}")

        return filtered_objects

    def _filter_number(self, objects, column_id, filter_op, filter_value, col_type):
        operator_map = {
            "greater_than": lambda x, y: x > y,
            "less_than": lambda x, y: x < y,
            "equals": lambda x, y: x == y,
            "not_equals": lambda x, y: x != y,
            "greater_than_or_equal": lambda x, y: x >= y,
            "less_than_or_equal": lambda x, y: x <= y,
            "between": lambda x, y: y[0] <= x <= y[1],
            "not_in_between": lambda x, y: x < y[0] or x > y[1],
        }

        if filter_op not in operator_map:
            raise ValueError(f"Invalid filter operation: {filter_op}")

        result = []
        for obj in objects:
            if col_type == ColType.EVAL_METRIC:
                eval_metric = obj.get("evals_metrics", {})
                eval_metric_obj = eval_metric.get(column_id, {})
                try:
                    eval_metric_value = float(eval_metric_obj.get("score", 0))
                except (ValueError, TypeError):
                    continue  # Skip invalid values instead of crashing
                if operator_map[filter_op](eval_metric_value, filter_value):
                    result.append(obj)
            elif col_type == ColType.SYSTEM_METRIC:
                system_metric = obj.get("system_metrics", {})
                try:
                    system_metric_value = float(system_metric.get(column_id, 0))
                except (ValueError, TypeError):
                    continue  # Skip invalid values instead of crashing
                if operator_map[filter_op](system_metric_value, filter_value):
                    result.append(obj)
            else:
                try:
                    obj_value = float(obj.get(column_id, 0))
                except (ValueError, TypeError):
                    continue  # Skip invalid values instead of crashing
                if operator_map[filter_op](obj_value, filter_value):
                    result.append(obj)
        return result

    def _filter_text(self, objects, column_id, filter_op, filter_value, col_type):
        filter_value = filter_value.lower()
        text_ops = {
            "contains": lambda x: filter_value in x.lower(),
            "not_contains": lambda x: filter_value not in x.lower(),
            "equals": lambda x: x.lower() == filter_value,
            "not_equals": lambda x: x.lower() != filter_value,
            "starts_with": lambda x: x.lower().startswith(filter_value),
            "ends_with": lambda x: x.lower().endswith(filter_value),
            "in": lambda x: x.lower() in filter_value,
            "not_in": lambda x: x.lower() not in filter_value,
        }

        if filter_op not in text_ops:
            raise ValueError(f"Invalid filter operation: {filter_op}")

        result = []
        for obj in objects:
            if col_type == ColType.EVAL_METRIC:
                eval_metric = obj.get("evals_metrics", {})
                eval_metric_obj = eval_metric.get(str(column_id), {})
                eval_metric_value = str(eval_metric_obj.get("score", "")).lower()
                if text_ops[filter_op](eval_metric_value):
                    result.append(obj)
            elif col_type == ColType.SYSTEM_METRIC:
                system_metric = obj.get("system_metrics", {})
                system_metric_value = str(system_metric.get(column_id, "")).lower()
                if text_ops[filter_op](system_metric_value):
                    result.append(obj)
            else:
                if text_ops[filter_op](str(obj.get(column_id, "")).lower()):
                    result.append(obj)
        return result

    def _filter_boolean(self, objects, column_id, filter_value, col_type):
        filter_value = filter_value.lower()
        if filter_value not in ["true", "false"]:
            raise ValueError("Invalid filter value. Allowed values are: true, false")

        return [
            obj
            for obj in objects
            if (obj.get(column_id, "").lower() == "true") == (filter_value == "true")
        ]

    def _filter_datetime(self, objects, column_id, filter_op, filter_value, col_type):
        # Use the timezone-aware helper so offset-aware ISO strings from the
        # frontend (e.g. ``2026-05-06T00:00:00+05:30``) are normalised to UTC.
        # The legacy strptime with a hardcoded 'Z' format crashed on those.
        if isinstance(filter_value, str):
            parsed = _parse_datetime_value(filter_value)
            if parsed is None:
                raise ValueError(f"Invalid datetime filter value: {filter_value!r}")
            filter_value = parsed
        elif isinstance(filter_value, list):
            parsed_list = [_parse_datetime_value(v) for v in filter_value]
            if any(p is None for p in parsed_list):
                raise ValueError(
                    f"Invalid datetime filter value in list: {filter_value!r}"
                )
            filter_value = parsed_list

        operator_map = {
            "equals": lambda x, y: x.date() == y.date(),
            "not_equals": lambda x, y: x.date() != y.date(),
            "greater_than": lambda x, y: x > y,
            "less_than": lambda x, y: x < y,
            "greater_than_or_equal": lambda x, y: x >= y,
            "less_than_or_equal": lambda x, y: x <= y,
            "between": lambda x, y: y[0] <= x <= y[1],
            "not_in_between": lambda x, y: x < y[0] or x > y[1],
        }

        if filter_op not in operator_map:
            raise ValueError(f"Invalid filter operation: {filter_op}")
        return [
            obj
            for obj in objects
            if obj.get(column_id) is not None
            and operator_map[filter_op](
                datetime.strptime(
                    (_convert_to_datetime_format(str(obj.get(column_id)))),
                    "%Y-%m-%dT%H:%M:%S.%fZ",
                ),
                filter_value,
            )
        ]

    def _filter_array(self, objects, column_id, filter_op, filter_value, col_type):
        result = []
        for obj in objects:
            column_value = obj.get(column_id, [])
            if not isinstance(column_value, list):
                continue

            if filter_op == "contains":
                if filter_value in column_value:
                    result.append(obj)
            elif filter_op == "not_contains":
                if filter_value not in column_value:
                    result.append(obj)

        return result

    @classmethod
    def get_sort_conditions_system_metrics(cls, sort_params, field_map=None):
        """
        Convert sort parameters to Django ORM ordering conditions

        Args:
            sort_params (list): List of dicts with columnId and direction
            Example: [{"column_id": "avg_cost", "direction": "asc"}]
            field_map (dict, optional): Custom mapping of column_id to ORM field name.
                When None, uses the default map for system metrics.

        Returns:
            list: List of sort conditions for Django ORM
        """
        if field_map is None:
            field_map = {
                "avg_cost": "row_avg_cost",
                "avg_latency": "row_avg_latency_ms",
                "run_name": "run_name",
            }

        sort_conditions = []

        for param in sort_params:
            column_id = param.get("column_id")
            direction = param.get("direction", "asc")

            if not column_id:
                continue

            field_name = field_map.get(column_id)
            if not field_name:
                continue

            if direction.lower() == "desc":
                field_name = f"-{field_name}"

            sort_conditions.append(field_name)

        return sort_conditions

    @staticmethod
    def get_filter_conditions_for_system_metrics(filters, field_map=None):
        """
        Convert filter conditions into Django Q objects for filtering aggregated fields.

        Expected filter format:
        [
            {
                "columnId": "avg_cost",
                "filterConfig": {
                    "filterOp": "equals",
                    "filterType": "number",
                    "filterValue": 0.5
                }
            }
        ]
        """
        if not filters:
            return Q()

        system_metrics_filter_conditions = []
        for filter_item in filters:
            # Use normalization helper for consistent parameter handling
            column_id, filter_config = FilterEngine._normalize_filter_params(
                filter_item
            )

            if filter_config.get("col_type") == ColType.SPAN_ATTRIBUTE.value:
                continue

            # Skip if not a valid filter
            if column_id in [
                "avg_score",
                "avg_latency",
                "latency",
                "avg_cost",
                "node_type",
                "trace_id",
                "span_id",
                "created_at",
                "run_name",
                "span_name",
                "trace_name",
                "session_id",
                "prompt_template_version",
                "labels",
                "avg_input_tokens",
                "avg_output_tokens",
                "unique_traces",
                "first_used",
                "last_used",
                "user_id",
                "status",
                "start_time",
                "prompt_label_id",
                "prompt_label_name",
                "total_cost",
                "total_tokens",
                "total_traces_count",
                "duration",
                "end_time",
                "first_message",
                "last_message",
            ]:
                system_metrics_filter_conditions.append(
                    {"column_id": column_id, "filter_config": filter_config}
                )

        # Map of filter operations to Django filter suffixes
        operator_map = {
            "greater_than": "gt",
            "less_than": "lt",
            "equals": "exact",
            "not_equals": "ne",
            "greater_than_or_equal": "gte",
            "less_than_or_equal": "lte",
            "between": "range",
            "not_in_between": "exclude",
            "contains": "icontains",
        }

        # Map column IDs to their aggregated field names
        if field_map is None:
            field_map = FilterEngine.DEFAULT_FIELD_MAP

        q_objects = []

        for filter_item in system_metrics_filter_conditions:
            column_id = filter_item.get("column_id")
            filter_config = filter_item.get("filter_config", {})

            if not column_id or not filter_config:
                continue

            filter_op = str(filter_config.get("filter_op"))
            filter_value = filter_config.get("filter_value")
            filter_type = filter_config.get("filter_type")

            if not all([filter_op, filter_value is not None, filter_type]):
                continue

            if filter_type in ("number", "boolean"):
                is_valid, converted_value = (
                    FilterEngine._validate_and_convert_filter_value(
                        filter_value, filter_type, filter_op
                    )
                )
                if not is_valid:
                    continue
                filter_value = converted_value

            # Get the mapped field name for the aggregated column
            field_name = field_map.get(column_id)
            if not field_name:
                continue

            # Get the Django filter operator
            django_operator = operator_map.get(filter_op)
            if not django_operator:
                continue

            # Handle comma-separated values for equals/not_equals — split into list for __in lookup
            if (
                filter_op in ("equals", "not_equals")
                and isinstance(filter_value, str)
                and "," in filter_value
            ):
                filter_value = [v.strip() for v in filter_value.split(",") if v.strip()]

            # Validate UUID fields
            if column_id in ["trace_id", "session_id"]:
                if isinstance(filter_value, list):
                    try:
                        [uuid.UUID(str(v)) for v in filter_value]
                    except ValueError:
                        return Q(pk__isnull=True)
                else:
                    try:
                        uuid.UUID(str(filter_value))
                    except ValueError:
                        return Q(pk__isnull=True)

            # Handle duration fields — convert numeric seconds to timedelta
            if column_id == "duration" and filter_type == "number":
                if isinstance(filter_value, (list, tuple)):
                    filter_value = [timedelta(seconds=float(v)) for v in filter_value]
                else:
                    filter_value = timedelta(seconds=float(filter_value))

            # Handle datetime filter type - extract only date part
            if filter_type == "datetime":
                # Extract date from filter_value
                if isinstance(filter_value, (list, tuple)):
                    # For between/not_in_between operations
                    date_values = [extract_date(v) for v in filter_value]
                    if None in date_values:
                        continue  # Skip invalid date values
                    filter_value = date_values
                else:
                    # For single value operations
                    date_value = extract_date(filter_value)
                    if date_value is None:
                        continue  # Skip invalid date value
                    filter_value = date_value

                # Use __date lookup for date-only comparison
                field_name = f"{field_name}__date"

            # Handle special cases for aggregated fields
            if filter_op == "not_equals":
                q_objects.append(~Q(**{f"{field_name}": filter_value}))
            elif filter_op == "between":
                if isinstance(filter_value, list | tuple) and len(filter_value) == 2:
                    q_objects.append(
                        Q(**{f"{field_name}__gte": filter_value[0]})
                        & Q(**{f"{field_name}__lte": filter_value[1]})
                    )
            elif filter_op == "not_in_between":
                if isinstance(filter_value, list | tuple) and len(filter_value) == 2:
                    q_objects.append(
                        ~(
                            Q(**{f"{field_name}__gte": filter_value[0]})
                            & Q(**{f"{field_name}__lte": filter_value[1]})
                        )
                    )
            elif filter_op == "contains":
                if isinstance(filter_value, list):
                    # Special handling for array fields like labels
                    if column_id == "labels":
                        # Use __overlap for PostgreSQL array fields to check if arrays share any elements
                        q_objects.append(Q(**{f"{field_name}__overlap": filter_value}))
                    else:
                        # Use __in to match any value in the list for non-array fields
                        q_objects.append(Q(**{f"{field_name}__in": filter_value}))
                else:
                    # Keep existing behavior for single value contains
                    q_objects.append(Q(**{f"{field_name}__icontains": filter_value}))
            elif isinstance(filter_value, list) and filter_op == "equals":
                q_objects.append(Q(**{f"{field_name}__in": filter_value}))
            elif isinstance(filter_value, list) and filter_op == "not_equals":
                q_objects.append(~Q(**{f"{field_name}__in": filter_value}))
            else:
                filter_param = {f"{field_name}__{django_operator}": filter_value}
                q_objects.append(Q(**filter_param))

        # Combine all Q objects with AND operation
        if q_objects:
            return reduce(operator.and_, q_objects)

        return Q()

    @staticmethod
    def get_filter_conditions_for_voice_system_metrics(
        filters, span_attrs_field="span_attributes"
    ):
        """Build Q objects and annotations for voice system metric filters.

        Voice metrics (agent latency, turn count, etc.) are stored inside the
        ``span_attributes`` JSONField on the root ObservationSpan, which is
        annotated onto the Trace queryset.  This method:

        1. Extracts voice metric filters from the filter list.
        2. Builds ``KeyTextTransform`` + ``Cast`` annotations to expose the
           JSON values as typed columns.
        3. Returns Django Q objects that filter on those annotations.

        Args:
            filters: The full filter list from the request.
            span_attrs_field: Name of the annotated JSON field on the queryset
                (default ``"span_attributes"``).

        Returns:
            A ``(Q_conditions, annotations_dict)`` tuple.  ``annotations_dict``
            should be applied to the queryset via ``.annotate(**annotations_dict)``
            **before** filtering with ``Q_conditions``.
        """
        if not filters:
            return Q(), {}

        operator_map = {
            "greater_than": "gt",
            "less_than": "lt",
            "equals": "exact",
            "not_equals": "ne",
            "greater_than_or_equal": "gte",
            "less_than_or_equal": "lte",
            "between": "range",
            "not_in_between": "exclude",
        }

        annotations = {}
        q_objects = []

        for filter_item in filters:
            column_id, filter_config = FilterEngine._normalize_filter_params(
                filter_item
            )
            col_type = (
                filter_config.get("col_type") or filter_config.get("colType") or ""
            )

            if column_id not in FilterEngine.VOICE_SYSTEM_METRIC_IDS:
                continue
            if col_type != ColType.SYSTEM_METRIC.value:
                continue

            defn = FilterEngine.VOICE_METRIC_DEFINITIONS.get(column_id)
            if not defn:
                continue

            filter_op = str(
                filter_config.get("filter_op") or filter_config.get("filterOp", "")
            )
            filter_value = filter_config.get(
                "filter_value", filter_config.get("filterValue")
            )
            filter_type = filter_config.get("filter_type") or filter_config.get(
                "filterType"
            )

            if not filter_op or filter_value is None or not filter_type:
                continue

            # Voice system metrics are always numeric — reject non-number filters.
            if filter_type != "number":
                continue

            is_valid, converted_value = FilterEngine._validate_and_convert_filter_value(
                filter_value, filter_type, filter_op
            )
            if not is_valid:
                continue
            filter_value = converted_value

            json_keys = defn["json_keys"]
            expr = KeyTextTransform(json_keys[0], span_attrs_field)
            for key in json_keys[1:]:
                expr = KeyTextTransform(key, expr)

            annotation_name = defn["annotation"]
            cast_expr = Cast(expr, output_field=defn["output_field"])

            if defn.get("is_computed_percentage"):
                annotations[annotation_name] = Round(
                    cast_expr / (cast_expr + Value(1)) * Value(100)
                )
            else:
                # All other metrics: round to integer.
                annotations[annotation_name] = Round(cast_expr)

            # Build the Q condition
            field_name = annotation_name
            django_op = operator_map.get(filter_op)
            if not django_op:
                continue

            if filter_op == "not_equals":
                q_objects.append(~Q(**{f"{field_name}": filter_value}))
            elif filter_op == "between":
                if isinstance(filter_value, (list, tuple)) and len(filter_value) == 2:
                    q_objects.append(
                        Q(**{f"{field_name}__gte": filter_value[0]})
                        & Q(**{f"{field_name}__lte": filter_value[1]})
                    )
            elif filter_op == "not_in_between":
                if isinstance(filter_value, (list, tuple)) and len(filter_value) == 2:
                    q_objects.append(
                        ~(
                            Q(**{f"{field_name}__gte": filter_value[0]})
                            & Q(**{f"{field_name}__lte": filter_value[1]})
                        )
                    )
            else:
                q_objects.append(Q(**{f"{field_name}__{django_op}": filter_value}))

        combined_q = reduce(operator.and_, q_objects) if q_objects else Q()
        return combined_q, annotations

    @staticmethod
    def get_sql_filter_conditions_for_system_metrics(filters, query):
        """
        Converts filter conditions into SQL WHERE conditions for evaluation metrics.

        :param filters: List of filter conditions in the expected format.
        :param query: Base SQL query to which filter conditions should be applied.
        :return: Modified SQL query with appended WHERE conditions.
        """

        if not filters:
            return query

        # Map filter operations to SQL syntax
        operator_map = {
            "greater_than": ">",
            "less_than": "<",
            "equals": "=",
            "not_equals": "!=",
            "greater_than_or_equal": ">=",
            "less_than_or_equal": "<=",
            "between": "BETWEEN",
            "not_in_between": "NOT BETWEEN",
            "contains": "ILIKE",
        }

        # Map filterable column names to SQL field names
        field_map = {
            "avg_score": "em.float_score",
            "avg_latency": "os.latency_ms",
            "avg_cost": "os.cost",
            "node_type": "os.observation_type",
            "trace_id": "t.id",
            "span_id": "os.id",
            "created_at": "os.created_at",
            "run_name": "t.run_name",
        }

        conditions = []
        for filter_item in filters:
            column_id = filter_item.get("columnId")
            filter_config = filter_item.get("filterConfig", {})

            if not column_id or not filter_config:
                continue

            filter_op = filter_config.get("filterOp")
            filter_value = filter_config.get("filterValue")
            filter_type = filter_config.get("filterType")

            if not all([filter_op, filter_value is not None, filter_type]):
                continue

            sql_column = field_map.get(column_id)
            if not sql_column:
                continue

            if "_id" in column_id:
                try:
                    str(uuid.UUID(column_id))
                except ValueError:
                    continue

            sql_operator = operator_map.get(filter_op)
            if not sql_operator:
                continue

            # Handle different filter operations
            if filter_op in [
                "equals",
                "greater_than",
                "less_than",
                "greater_than_or_equal",
                "less_than_or_equal",
                "not_equals",
            ]:
                conditions.append(f"{sql_column} {sql_operator} '{filter_value}'")
            elif (
                filter_op in ["between", "not_in_between"]
                and isinstance(filter_value, list)
                and len(filter_value) == 2
            ):
                conditions.append(
                    f"{sql_column} {sql_operator} '{filter_value[0]}' AND '{filter_value[1]}'"
                )
            elif filter_op == "contains":
                conditions.append(f"{sql_column} {sql_operator} '%{filter_value}%'")

        if conditions:
            query += " AND " + " AND ".join(conditions)

        return query

    @staticmethod
    def get_sql_filter_conditions_for_cte_system_metrics(filters):
        """
        Converts filter conditions into SQL WHERE/HAVING conditions for CTE-based queries.
        Returns WHERE conditions that can be applied to the base CTE.

        :param filters: List of filter conditions.
        :return: Tuple of (SQL string with placeholders, list of parameters) or (None, [])
        """
        if not filters:
            return None, []

        # Map filter operations to SQL syntax
        operator_map = {
            "greater_than": ">",
            "less_than": "<",
            "equals": "=",
            "not_equals": "!=",
            "greater_than_or_equal": ">=",
            "less_than_or_equal": "<=",
            "between": "BETWEEN",
            "not_in_between": "NOT BETWEEN",
            "contains": "ILIKE",
        }

        # Import cost calculation SQL from shared location
        from model_hub.utils.SQL_queries import MODEL_COST_CALCULATION_SQL

        # Map filterable column names to SQL field names (for base CTE)
        # For HAVING clause, we need to use the actual aggregate expressions, not aliases
        # PostgreSQL doesn't always recognize aliases in HAVING for complex expressions
        field_map = {
            "avg_cost": f"COALESCE(ROUND(AVG({MODEL_COST_CALCULATION_SQL}), 6), 0.0)",
            "avg_latency": "COALESCE(ROUND(AVG(os.latency_ms), 2), 0.0)",
            "avg_input_tokens": "COALESCE(ROUND(AVG(os.prompt_tokens), 2), 0.0)",
            "avg_output_tokens": "COALESCE(ROUND(AVG(os.completion_tokens), 2), 0.0)",
            "unique_traces": "COUNT(DISTINCT os.trace_id)",
            "first_used": "MIN(os.created_at)",
            "last_used": "MAX(os.created_at)",
            "prompt_template_version": "pv.template_version",
            "prompt_label_id": "os.prompt_label_id",
            "prompt_label_name": "pl.name",
        }

        having_conditions = []
        params = []

        for filter_item in filters:
            # Use normalization helper for consistent parameter handling
            column_id, filter_config = FilterEngine._normalize_filter_params(
                filter_item
            )

            if filter_config.get("col_type") == ColType.SPAN_ATTRIBUTE.value:
                continue

            # Skip if not a system metric
            if column_id not in field_map:
                continue

            filter_op = filter_config.get("filter_op")
            filter_value = filter_config.get("filter_value")
            filter_type = filter_config.get("filter_type")

            if not all([filter_op, filter_value is not None, filter_type]):
                continue

            # Validate and convert filter values
            is_valid, converted_value = FilterEngine._validate_and_convert_filter_value(
                filter_value, filter_type, filter_op
            )
            if not is_valid:
                continue

            sql_column = field_map.get(column_id)
            sql_operator = operator_map.get(filter_op)

            if not sql_column or not sql_operator:
                continue

            # Use parameterized queries to prevent SQL injection
            if filter_op in [
                "equals",
                "greater_than",
                "less_than",
                "greater_than_or_equal",
                "less_than_or_equal",
                "not_equals",
            ]:
                having_conditions.append(f"{sql_column} {sql_operator} %s")
                params.append(converted_value)
            elif (
                filter_op == "between"
                and isinstance(converted_value, list)
                and len(converted_value) == 2
            ):
                having_conditions.append(f"{sql_column} BETWEEN %s AND %s")
                params.extend(converted_value)
            elif (
                filter_op == "not_in_between"
                and isinstance(converted_value, list)
                and len(converted_value) == 2
            ):
                having_conditions.append(f"{sql_column} NOT BETWEEN %s AND %s")
                params.extend(converted_value)
            elif filter_op == "contains":
                having_conditions.append(f"{sql_column} {sql_operator} %s")
                params.append(f"%{converted_value}%")

        # Return HAVING clause conditions with parameters
        if having_conditions:
            return " HAVING " + " AND ".join(having_conditions), params
        return None, []

    @staticmethod
    def get_sql_filter_conditions_for_cte_eval_metrics(filters):
        """
        Converts filter conditions into SQL HAVING conditions for eval metrics in final SELECT.
        Returns conditions that can be applied after LEFT JOINs in the final SELECT.

        :param filters: List of filter conditions.
        :return: Tuple of (SQL string with placeholders, list of parameters) or (None, [])
        """
        if not filters:
            return None, []

        # Map filter operations to SQL syntax
        operator_map = {
            "greater_than": ">",
            "less_than": "<",
            "equals": "=",
            "not_equals": "!=",
            "greater_than_or_equal": ">=",
            "less_than_or_equal": "<=",
            "between": "BETWEEN",
            "not_in_between": "NOT BETWEEN",
        }

        conditions = []
        params = []

        for filter_item in filters:
            column_id, filter_config = FilterEngine._normalize_filter_params(
                filter_item
            )
            col_type = filter_config.get("col_type", ColType.EVAL_METRIC.value)

            if filter_config.get("col_type") == ColType.SPAN_ATTRIBUTE.value:
                continue

            # Skip if it's a system metric
            if column_id in [
                "avg_cost",
                "avg_latency",
                "avg_input_tokens",
                "avg_output_tokens",
                "unique_traces",
                "first_used",
                "last_used",
                "prompt_template_version",
                "prompt_label_id",
                "prompt_label_name",
                "has_eval",
                "has_annotation",
            ]:
                continue

            # Skip voice system metric filters — handled separately
            if column_id in FilterEngine.VOICE_SYSTEM_METRIC_IDS:
                continue

            filter_op = filter_config.get("filter_op")
            filter_value = filter_config.get("filter_value")
            filter_type = filter_config.get("filter_type")

            if not all([filter_op, filter_value is not None, filter_type]):
                continue

            # Validate and convert filter values
            is_valid, converted_value = FilterEngine._validate_and_convert_filter_value(
                filter_value, filter_type, filter_op
            )
            if not is_valid:
                continue

            sql_operator = operator_map.get(filter_op)
            if not sql_operator:
                continue

            if filter_type == "number":
                # For eval metrics, handle both regular and choice-based metrics
                # column_id format: "config_id" or "config_id**choice_name"

                if "**" in column_id:
                    # Choice-based metric: config_id**choice_name
                    config_part, choice_part = column_id.split("**", 1)
                    sanitized_config = config_part.replace("-", "_").replace("*", "_")
                    # For choice metrics: metric_xxx->'choice'->>'score'
                    # Use parameterized query for the choice_part as well to prevent injection
                    metric_column = (
                        f"(metric_{sanitized_config}->%s->>'score')::numeric"
                    )

                    if filter_op in [
                        "equals",
                        "greater_than",
                        "less_than",
                        "greater_than_or_equal",
                        "less_than_or_equal",
                        "not_equals",
                    ]:
                        conditions.append(f"{metric_column} {sql_operator} %s")
                        params.extend([choice_part, converted_value])
                    elif (
                        filter_op == "between"
                        and isinstance(converted_value, list)
                        and len(converted_value) == 2
                    ):
                        conditions.append(f"{metric_column} BETWEEN %s AND %s")
                        params.extend([choice_part] + converted_value)
                    elif (
                        filter_op == "not_in_between"
                        and isinstance(converted_value, list)
                        and len(converted_value) == 2
                    ):
                        conditions.append(f"{metric_column} NOT BETWEEN %s AND %s")
                        params.extend([choice_part] + converted_value)
                else:
                    # Regular metric: config_id
                    # Replace '*' and '-' with '_' to match metric column naming
                    sanitized_column_id = column_id.replace("*", "_").replace("-", "_")
                    # For regular metrics: metric_xxx->>'score'
                    metric_column = f"(metric_{sanitized_column_id}->>'score')::numeric"

                    if filter_op in [
                        "equals",
                        "greater_than",
                        "less_than",
                        "greater_than_or_equal",
                        "less_than_or_equal",
                        "not_equals",
                    ]:
                        conditions.append(f"{metric_column} {sql_operator} %s")
                        params.append(converted_value)
                    elif (
                        filter_op == "between"
                        and isinstance(converted_value, list)
                        and len(converted_value) == 2
                    ):
                        conditions.append(f"{metric_column} BETWEEN %s AND %s")
                        params.extend(converted_value)
                    elif (
                        filter_op == "not_in_between"
                        and isinstance(converted_value, list)
                        and len(converted_value) == 2
                    ):
                        conditions.append(f"{metric_column} NOT BETWEEN %s AND %s")
                        params.extend(converted_value)

        # Return WHERE clause conditions for final SELECT with parameters
        if conditions:
            return " WHERE " + " AND ".join(conditions), params
        return None, []

    @staticmethod
    def get_sql_filter_conditions_for_eval_metrics(filters, query):
        conditions = []
        having = None

        for filter_item in filters:
            column_id = filter_item.get("columnId")
            filter_config = filter_item.get("filterConfig", {})

            if (
                not column_id
                or not filter_config
                or column_id
                in [
                    "avg_score",
                    "avg_latency",
                    "avg_cost",
                    "node_type",
                    "trace_id",
                    "span_id",
                    "run_name",
                    "rank",
                    "has_eval",
                    "has_annotation",
                ]
            ):
                continue

            filter_type = filter_config.get("filterType")
            filter_op = filter_config.get("filterOp")
            filter_value = filter_config.get("filterValue")

            if filter_type == "number":
                metric_val = None
                avg_filter = column_id.split("_")
                if len(avg_filter) > 1:
                    column_id = avg_filter[0]
                    metric_val = avg_filter[1]
                    having = f"""having COALESCE(
                                (jsonb_extract_path_text(jsonb_object_agg(
                                    'metric_' || COALESCE(em.custom_eval_config_id, sls.custom_eval_config_id),
                                    COALESCE(
                                        CASE
                                            WHEN em.float_score IS NOT NULL THEN jsonb_build_object('score', em.float_score)
                                            WHEN em.bool_score IS NOT NULL THEN jsonb_build_object('score', em.bool_score)
                                            WHEN sls.str_list_score IS NOT NULL THEN sls.str_list_score
                                            ELSE jsonb_build_object('score', 0.0)
                                        END,
                                        jsonb_build_object('score', 0.0)
                                    )
                                ), 'metric_{column_id}', '{metric_val}', 'score'))::numeric, 0.0
                            )"""

                metric_column_id = f"metric_{column_id}->>'score'"
                if filter_op == "greater_than":
                    if metric_val and having:
                        having += f" > {filter_value};"
                    else:
                        conditions.append(
                            f"({metric_column_id})::numeric > {filter_value}"
                        )
                elif filter_op == "less_than":
                    if metric_val and having:
                        having += f" < {filter_value};"
                    else:
                        conditions.append(
                            f"({metric_column_id})::numeric < {filter_value}"
                        )
                elif filter_op == "equals":
                    if metric_val and having:
                        having += f" = {filter_value};"
                    else:
                        conditions.append(
                            f"({metric_column_id})::numeric = {filter_value}"
                        )
                elif filter_op == "not_equals":
                    if metric_val and having:
                        having += f" <> {filter_value};"
                    else:
                        conditions.append(
                            f"({metric_column_id})::numeric <> {filter_value}"
                        )
                elif (
                    filter_op == "between"
                    and isinstance(filter_value, list | tuple)
                    and len(filter_value) == 2
                ):
                    if metric_val and having:
                        having += f"BETWEEN {filter_value[0]} AND {filter_value[1]};"
                    else:
                        conditions.append(
                            f"({metric_column_id})::numeric BETWEEN {filter_value[0]} AND {filter_value[1]}"
                        )
                elif (
                    filter_op == "not_in_between"
                    and isinstance(filter_value, list | tuple)
                    and len(filter_value) == 2
                ):
                    if metric_val and having:
                        having += (
                            f" NOT BETWEEN {filter_value[0]} AND {filter_value[1]};"
                        )
                    else:
                        conditions.append(
                            f"({metric_column_id})::numeric NOT BETWEEN {filter_value[0]} AND {filter_value[1]}"
                        )

            elif filter_type == "boolean":
                conditions.append(f"bool_pass_rate = {filter_value}")

            elif filter_type == "array":
                conditions.append(f"str_list_values::text ILIKE '%{filter_value}%'")

        if conditions:
            query += " AND " + " AND ".join(conditions)

        return query, having

    @staticmethod
    def get_filter_conditions_for_non_system_metrics(filters):
        eval_filter_conditions = Q()
        for filter_item in filters:
            column_id = filter_item.get("column_id")
            filter_config = filter_item.get("filter_config", {})
            col_type = (
                filter_config.get("col_type", ColType.EVAL_METRIC.value)
                if "col_type" in filter_config
                else filter_item.get("col_type", ColType.EVAL_METRIC.value)
            )

            if col_type in [
                ColType.SPAN_ATTRIBUTE.value,
                ColType.ANNOTATION.value,
                ColType.SYSTEM_METRIC.value,
            ]:
                continue

            # Skip if not a valid filter
            if (
                not column_id
                or not filter_config
                or column_id
                in [
                    "avg_score",
                    "avg_latency",
                    "avg_cost",
                    "node_type",
                    "trace_id",
                    "span_id",
                    "created_at",
                    "run_name",
                    "span_name",
                    "trace_name",
                    "session_id",
                    "prompt_template_version",
                    "labels",
                    "avg_input_tokens",
                    "avg_output_tokens",
                    "unique_traces",
                    "first_used",
                    "last_used",
                    "prompt_label_id",
                    "user_id",
                    "status",
                    "start_time",
                    "has_eval",
                    "has_annotation",
                ]
            ):
                continue

            filter_type = filter_config.get("filter_type")
            filter_op = filter_config.get("filter_op")
            filter_value = filter_config.get("filter_value")

            # Replace '*' with '_' in column_id
            column_id = column_id.replace("*", "_")

            metric_column_id = ""

            if col_type == ColType.PROMPT_METRIC.value:
                metric_column_id = f"metric_{column_id}"
            elif col_type == ColType.EVAL_METRIC.value:
                metric_column_id = f"metric_{column_id}"

            if filter_type == "number":
                # Append 'metric_' at the beginning and '__score' at the end of column_id
                if col_type == ColType.ANNOTATION_RUNS.value:
                    metric_column_id = f"annotation_{column_id}__score"
                elif col_type not in [
                    ColType.PROMPT_METRIC.value,
                ]:
                    metric_column_id = f"metric_{column_id}__score"

                # Normalize filter_value: frontend may send ["70", ""] for single-value ops
                _fv = filter_value
                if isinstance(_fv, (list, tuple)):
                    _fv = _fv[0] if len(_fv) > 0 and _fv[0] not in (None, "") else _fv
                try:
                    _fv = float(_fv) if not isinstance(_fv, (int, float)) else _fv
                except (ValueError, TypeError):
                    pass

                # UI shows scores as 0-100 but DB stores output_float as 0-1.
                # Divide by 100 for eval metric filters (not annotations or prompts).
                if col_type == ColType.EVAL_METRIC.value and isinstance(
                    _fv, (int, float)
                ):
                    _fv = _fv / 100.0

                score_condition = None

                # Create score conditions based on the modified column_id
                if filter_op == "greater_than":
                    score_condition = Q(**{f"{metric_column_id}__gt": _fv})
                elif filter_op == "less_than":
                    score_condition = Q(**{f"{metric_column_id}__lt": _fv})
                elif filter_op == "equals":
                    score_condition = Q(**{f"{metric_column_id}": _fv})
                elif filter_op == "not_equals":
                    score_condition = ~Q(**{f"{metric_column_id}": _fv})
                elif (
                    filter_op == "between"
                    and isinstance(filter_value, list | tuple)
                    and len(filter_value) == 2
                ):
                    _lo, _hi = filter_value[0], filter_value[1]
                    try:
                        _lo = float(_lo)
                        _hi = float(_hi)
                    except (ValueError, TypeError):
                        pass
                    if col_type == ColType.EVAL_METRIC.value:
                        if isinstance(_lo, (int, float)):
                            _lo = _lo / 100.0
                        if isinstance(_hi, (int, float)):
                            _hi = _hi / 100.0
                    score_condition = Q(
                        **{
                            f"{metric_column_id}__gte": _lo,
                            f"{metric_column_id}__lte": _hi,
                        }
                    )
                elif (
                    filter_op == "not_in_between"
                    and isinstance(filter_value, list | tuple)
                    and len(filter_value) == 2
                ):
                    _lo, _hi = filter_value[0], filter_value[1]
                    try:
                        _lo = float(_lo)
                        _hi = float(_hi)
                    except (ValueError, TypeError):
                        pass
                    if col_type == ColType.EVAL_METRIC.value:
                        if isinstance(_lo, (int, float)):
                            _lo = _lo / 100.0
                        if isinstance(_hi, (int, float)):
                            _hi = _hi / 100.0
                    score_condition = ~Q(
                        **{
                            f"{metric_column_id}__gte": _lo,
                            f"{metric_column_id}__lte": _hi,
                        }
                    )
                elif filter_op == "greater_than_or_equal":
                    score_condition = Q(**{f"{metric_column_id}__gte": _fv})
                elif filter_op == "less_than_or_equal":
                    score_condition = Q(**{f"{metric_column_id}__lte": _fv})
                if score_condition:
                    eval_filter_conditions &= score_condition

            elif filter_type == "boolean":
                if col_type in [ColType.PROMPT_METRIC.value]:
                    eval_filter_conditions &= Q(**{f"{metric_column_id}": filter_value})
                else:
                    eval_filter_conditions &= Q(bool_pass_rate=filter_value)

            elif filter_type == "array":

                if col_type in [
                    ColType.PROMPT_METRIC.value,
                    ColType.EVAL_METRIC.value,
                ]:
                    if isinstance(filter_value, list):
                        array_conditions = Q()
                        for value in filter_value:
                            array_conditions &= Q(
                                **{f"{metric_column_id}__icontains": value}
                            )
                        eval_filter_conditions &= array_conditions
                    else:
                        eval_filter_conditions &= Q(
                            **{f"{metric_column_id}__icontains": filter_value}
                        )
            elif filter_type == "text":
                # Skip EVAL_METRIC as it stores numeric scores, not text data
                if col_type == ColType.EVAL_METRIC.value:
                    continue

                eval_filter_conditions &= Q(**{f"{metric_column_id}": filter_value})
        return eval_filter_conditions

    @staticmethod
    def get_filter_conditions_for_voice_call_annotations(
        filters, user_id=None, span_filter_kwargs=None
    ):
        """
        Create Django Q objects for filtering voice call traces based on annotations.

        Voice call annotations are stored as JSON objects on the queryset
        (e.g. {"score": 4.0, "annotators": [...]}) unlike list_traces which stores
        scalar values. This method handles three types of annotation filters:

        1. Annotation value filters (col_type=ANNOTATION):
           Filters on annotation score/value within the JSON structure.
        2. Annotator filter (column_id=annotator):
           Filters traces annotated by specific user(s).
        3. My annotations filter (column_id=my_annotations):
           Filters traces annotated by the current user.

        Note: col_type is a top-level field on the filter item, not inside filter_config.

        Expected filter formats:
        [
            {
                "column_id": "<annotation_label_uuid>",
                "col_type": "ANNOTATION",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 3.0
                }
            },
            {
                "column_id": "<annotation_label_uuid>",
                "col_type": "ANNOTATION",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": "up"   // or "down"
                }
            },
            {
                "column_id": "<annotation_label_uuid>",
                "col_type": "ANNOTATION",
                "filter_config": {
                    "filter_type": "annotator",
                    "filter_value": "<user_id>"   // or ["id1", "id2"]
                }
            },
            {
                "column_id": "<annotation_label_uuid>**thumbs_up",
                "col_type": "ANNOTATION",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 2
                }
            },
            {
                "column_id": "<annotation_label_uuid>**thumbs_down",
                "col_type": "ANNOTATION",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "between",
                    "filter_value": [1, 5]
                }
            },
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": "<user_id>"
                }
            },
            {
                "column_id": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": true
                }
            }
        ]

        Args:
            filters: List of filter conditions
            user_id: Current user's ID (needed for my_annotations filter)

        Returns:
            tuple[Q, dict]: A Django Q object for filtering, and a dict of extra
                annotations that must be applied to the queryset before the Q filter.
        """
        from model_hub.models.score import Score

        if not filters:
            return Q(), {}

        # Build a Q object for matching scores to the outer row.
        # For span-level queries, callers pass span_filter_kwargs directly.
        # For the default trace-level context, match scores on the trace
        # directly OR on any observation span belonging to the trace.
        if span_filter_kwargs is not None:
            source_q = Q(**span_filter_kwargs)
        else:
            source_q = Q(trace_id=OuterRef("trace_id")) | Q(
                observation_span__trace_id=OuterRef("trace_id")
            )

        q_conditions = Q()
        extra_annotations = {}

        for filter_item in filters:
            column_id, filter_config = FilterEngine._normalize_filter_params(
                filter_item
            )

            if not column_id or not filter_config:
                continue

            col_type = (
                filter_config.get("col_type", "")
                if "col_type" in filter_config
                else filter_item.get("col_type", "")
            )

            # --- Backwards-compat shim for legacy "my_annotations" filter ---
            # Canonical form is `annotator = [current_user_id]`. Stale saved
            # views and old clients still send the toggle shape; rewrite it
            # in-place into the annotator branch below.
            if column_id == "my_annotations":
                filter_value = filter_config.get("filter_value")
                if isinstance(filter_value, str):
                    filter_value = filter_value.lower() == "true"
                if filter_value and user_id:
                    q_conditions &= Q(
                        Exists(
                            Score.objects.filter(
                                source_q,
                                annotator_id=user_id,
                                deleted=False,
                            )
                        )
                    )
                continue

            # --- Handle "annotator" filter (across ALL annotation labels) ---
            # Uses DB-level Exists to check if any annotation exists by the user.
            # For per-label annotator filtering, use filter_type="annotator" with
            # col_type=ANNOTATION which checks the annotators JSON map via has_key.
            if column_id == "annotator":
                filter_value = filter_config.get("filter_value")
                if filter_value:
                    if isinstance(filter_value, list):
                        q_conditions &= Q(
                            Exists(
                                Score.objects.filter(
                                    source_q,
                                    annotator_id__in=filter_value,
                                    deleted=False,
                                )
                            )
                        )
                    else:
                        q_conditions &= Q(
                            Exists(
                                Score.objects.filter(
                                    source_q,
                                    annotator_id=filter_value,
                                    deleted=False,
                                )
                            )
                        )
                continue

            # --- Handle annotation value filters (col_type=ANNOTATION) ---
            if col_type != ColType.ANNOTATION.value:
                continue

            filter_type = filter_config.get("filter_type")
            filter_op = filter_config.get("filter_op")
            filter_value = filter_config.get("filter_value")

            # Parse column_id for sub-field separator
            # e.g. "uuid**thumbs_up" → base_column_id=uuid, sub_field=thumbs_up
            sub_field = None
            if "**" in column_id:
                base_column_id, sub_field = column_id.split("**", 1)
                annotation_field = f"annotation_{base_column_id}"
            else:
                annotation_field = f"annotation_{column_id}"

            if filter_type == "number":
                is_valid, converted_value = (
                    FilterEngine._validate_and_convert_filter_value(
                        filter_value, filter_type, filter_op
                    )
                )
                if not is_valid:
                    continue
                filter_value = converted_value

                # Use sub_field if present (e.g. thumbs_up/thumbs_down counts)
                # otherwise default to score
                if sub_field:
                    score_field = f"{annotation_field}__{sub_field}"
                else:
                    score_field = f"{annotation_field}__score"
                score_condition = None

                if filter_op == "greater_than":
                    score_condition = Q(**{f"{score_field}__gt": filter_value})
                elif filter_op == "less_than":
                    score_condition = Q(**{f"{score_field}__lt": filter_value})
                elif filter_op == "equals":
                    score_condition = Q(**{f"{score_field}": filter_value})
                elif filter_op == "not_equals":
                    score_condition = ~Q(**{f"{score_field}": filter_value})
                elif filter_op == "greater_than_or_equal":
                    score_condition = Q(**{f"{score_field}__gte": filter_value})
                elif filter_op == "less_than_or_equal":
                    score_condition = Q(**{f"{score_field}__lte": filter_value})
                elif (
                    filter_op == "between"
                    and isinstance(filter_value, list)
                    and len(filter_value) == 2
                ):
                    score_condition = Q(
                        **{
                            f"{score_field}__gte": filter_value[0],
                            f"{score_field}__lte": filter_value[1],
                        }
                    )
                elif (
                    filter_op == "not_in_between"
                    and isinstance(filter_value, list)
                    and len(filter_value) == 2
                ):
                    score_condition = ~Q(
                        **{
                            f"{score_field}__gte": filter_value[0],
                            f"{score_field}__lte": filter_value[1],
                        }
                    )

                if score_condition:
                    q_conditions &= score_condition

            elif filter_type == "boolean":
                # Thumbs up/down annotations: {"thumbs_up": N, "thumbs_down": M, ...}
                # filter_value: "up"/"down", "Thumbs Up"/"Thumbs Down", or bool
                if isinstance(filter_value, str):
                    val = filter_value.lower().replace(" ", "_")
                    if val in ("up", "true", "thumbs_up"):
                        q_conditions &= Q(**{f"{annotation_field}__thumbs_up__gt": 0})
                    elif val in ("down", "thumbs_down"):
                        q_conditions &= Q(**{f"{annotation_field}__thumbs_down__gt": 0})
                elif filter_value is True:
                    q_conditions &= Q(**{f"{annotation_field}__thumbs_up__gt": 0})
                elif filter_value is False:
                    q_conditions &= Q(**{f"{annotation_field}__thumbs_down__gt": 0})

            elif filter_type == "categorical":
                # Categorical annotations: the pre-annotated JSON has
                # {choice_label: count, ...}. Filter by checking the count
                # for the selected choice(s) is > 0.
                # Thumbs Up/Down annotations use keys "thumbs_up"/"thumbs_down"
                # but the frontend sends display labels "Thumbs Up"/"Thumbs Down".
                _THUMBS_MAP = {"Thumbs Up": "thumbs_up", "Thumbs Down": "thumbs_down"}
                raw_values = filter_value if isinstance(filter_value, list) else [filter_value]
                values = [_THUMBS_MAP.get(v, v) for v in raw_values]

                if filter_op in ("equals", "in", "contains"):
                    choice_q = Q()
                    for val in values:
                        choice_q |= Q(**{f"{annotation_field}__{val}__gt": 0})
                    q_conditions &= choice_q
                elif filter_op in ("not_equals", "not_in", "not_contains"):
                    for val in values:
                        q_conditions &= ~Q(**{f"{annotation_field}__{val}__gt": 0})

            elif filter_type == "text":
                # Text annotations store the raw value in Score.value JSON
                # under the "text" key.  Use Exists subqueries against Score
                # so that starts_with / ends_with operate on the actual text,
                # not on a stringified JSON blob.
                base_ann_filter = {
                    "observation_span__trace_id": OuterRef("trace_id"),
                    "label_id": (column_id if not sub_field else base_column_id),
                    "value__text__isnull": False,
                    "deleted": False,
                }
                if filter_op == "contains":
                    q_conditions &= Q(
                        Exists(
                            Score.objects.filter(
                                **base_ann_filter,
                                value__text__icontains=filter_value,
                            )
                        )
                    )
                elif filter_op == "equals":
                    q_conditions &= Q(
                        Exists(
                            Score.objects.filter(
                                **base_ann_filter,
                                value__text__iexact=filter_value,
                            )
                        )
                    )
                elif filter_op == "not_contains":
                    q_conditions &= ~Q(
                        Exists(
                            Score.objects.filter(
                                **base_ann_filter,
                                value__text__icontains=filter_value,
                            )
                        )
                    )
                elif filter_op == "not_equals":
                    q_conditions &= ~Q(
                        Exists(
                            Score.objects.filter(
                                **base_ann_filter,
                                value__text__iexact=filter_value,
                            )
                        )
                    )
                elif filter_op == "starts_with":
                    q_conditions &= Q(
                        Exists(
                            Score.objects.filter(
                                **base_ann_filter,
                                value__text__istartswith=filter_value,
                            )
                        )
                    )
                elif filter_op == "ends_with":
                    q_conditions &= Q(
                        Exists(
                            Score.objects.filter(
                                **base_ann_filter,
                                value__text__iendswith=filter_value,
                            )
                        )
                    )

            elif filter_type == "annotator":
                # Check if a specific user annotated this label
                # annotators is a map keyed by user_id, so use has_key
                annotators_field = f"{annotation_field}__annotators"
                if isinstance(filter_value, list):
                    annotator_conditions = Q()
                    for uid in filter_value:
                        annotator_conditions |= Q(
                            **{f"{annotators_field}__has_key": str(uid)}
                        )
                    q_conditions &= annotator_conditions
                elif filter_value:
                    q_conditions &= Q(
                        **{f"{annotators_field}__has_key": str(filter_value)}
                    )

            elif filter_type == "array":
                # Categorical annotations: {"choice1": 3, "choice2": 0, ...}
                # Filter traces where the selected choice has count > 0
                if isinstance(filter_value, list):
                    array_conditions = Q()
                    for value in filter_value:
                        array_conditions &= Q(**{f"{annotation_field}__{value}__gt": 0})
                    q_conditions &= array_conditions
                else:
                    q_conditions &= Q(**{f"{annotation_field}__{filter_value}__gt": 0})

        return q_conditions, extra_annotations

    @staticmethod
    def get_filter_conditions_for_simulation_calls(remove_simulation_calls=False):
        """
        Creates a Djanog Q object for filtering Future AGI simulation calls

        Expected filter format:
        [
            {
                "column_id": "attribute1",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "value",
                    "filter_value": "value",
                },
            }
        ]

        Return:
            Q: A django object for filtering
        """
        if not remove_simulation_calls or remove_simulation_calls == "false":
            return Q()

        VAPI_PHONE_NUMBERS = [
            "+18568806998",
            "+17755715840",
            "+13463424590",
            "+12175683677",
            "+12175696753",
            "+12175683493",
            "+12175681887",
            "+12176018447",
            "+12176018280",
            "+12175696862",
            "+19168660414",
            "+19163473349",
            "+18563161617",
            "+13463619738",
            "+19847339395",
        ]
        remove_simulation_calls_filters = Q()

        # Handling case for VAPI call logs
        # Use span_attributes (canonical) - eval_attributes is deprecated
        remove_simulation_calls_filters |= Q(provider=ProviderChoices.VAPI) & Q(
            span_attributes__raw_log__customer__number__in=VAPI_PHONE_NUMBERS
        )

        # Handling case for Retell call logs
        remove_simulation_calls_filters |= Q(provider=ProviderChoices.RETELL) & Q(
            span_attributes__raw_log__from_number__in=VAPI_PHONE_NUMBERS
        )

        return remove_simulation_calls_filters

    @staticmethod
    def get_filter_conditions_for_has_eval(filters, observe_type="trace"):
        """
        Create a Django Q object to filter traces/spans that have at least one eval run.

        Expected filter format:
        [
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": true
                }
            }
        ]

        Args:
            filters: List of filter conditions
            observe_type: "trace" or "span" - determines whether to filter
                          on EvalLogger.trace_id or EvalLogger.observation_span_id

        Returns:
            Q: A Django Q object for filtering
        """
        if not filters:
            return Q()

        for filter_item in filters:
            column_id, filter_config = FilterEngine._normalize_filter_params(
                filter_item
            )
            if column_id != "has_eval":
                continue

            filter_value = filter_config.get("filter_value")
            # Normalize to bool
            if isinstance(filter_value, str):
                filter_value = filter_value.lower() == "true"

            if not filter_value:
                continue

            from tracer.models.observation_span import EvalLogger

            if observe_type == "span":
                return Q(
                    Exists(
                        EvalLogger.objects.filter(
                            observation_span_id=OuterRef("id"),
                        )
                    )
                )
            else:
                return Q(
                    Exists(
                        EvalLogger.objects.filter(
                            trace_id=OuterRef("id"),
                        )
                    )
                )

        return Q()

    @staticmethod
    def get_filter_conditions_for_has_annotation(
        filters, observe_type="trace", annotation_label_ids=None
    ):
        """
        Create a Django Q object to filter traces/spans by annotation completeness.

        "Non annotated" (filter_value=false) means the trace/span is missing
        at least one of the project's configured annotation labels.

        When ``annotation_label_ids`` is provided, checks that ALL labels are
        present (fully annotated).  Without it, falls back to simple existence.
        """
        if not filters:
            return Q()

        for filter_item in filters:
            column_id, filter_config = FilterEngine._normalize_filter_params(
                filter_item
            )
            if column_id != "has_annotation":
                continue

            filter_value = filter_config.get("filter_value")
            if isinstance(filter_value, str):
                filter_value = filter_value.lower() == "true"

            from model_hub.models.score import Score

            label_ids = annotation_label_ids or []

            if label_ids:
                # Completeness check: fully annotated = has score for ALL labels.
                # Score.trace_id is often NULL — annotations are stored on spans.
                # Must check BOTH Score.trace_id AND Score.observation_span.trace_id.
                if observe_type == "span":
                    score_filter = Q(observation_span_id=OuterRef("id"))
                else:
                    score_filter = Q(trace_id=OuterRef("id")) | Q(
                        observation_span__trace_id=OuterRef("id")
                    )
                fully_annotated_q = Q()
                for lid in label_ids:
                    fully_annotated_q &= Q(
                        Exists(Score.objects.filter(score_filter, label_id=lid))
                    )
                return fully_annotated_q if filter_value else ~fully_annotated_q
            else:
                # Fallback: simple existence check
                if observe_type == "span":
                    exists_q = Q(
                        Exists(
                            Score.objects.filter(
                                observation_span_id=OuterRef("id"),
                            )
                        )
                    )
                else:
                    exists_q = Q(
                        Exists(
                            Score.objects.filter(
                                Q(trace_id=OuterRef("id"))
                                | Q(observation_span__trace_id=OuterRef("id")),
                            )
                        )
                    )
                return exists_q if filter_value else ~exists_q

        return Q()

    @staticmethod
    def get_filter_conditions_for_span_attributes(filters):
        """
        Create Django Q objects for filtering observation spans based on their span_attributes field.

        Expected filter format:
        [
            {
                "column_id": "attribute1",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": "value",
                    "col_type": "SPAN_ATTRIBUTE"
                }
            }
        ]

        Returns:
            Q: A Django Q object for filtering
        """
        if not filters:
            return Q()

        # Use span_attributes (canonical) for all filters - eval_attributes is deprecated
        text_operator_map = {
            "contains": lambda col, val: Q(
                **{f"span_attributes__{col}__icontains": val}
            ),
            "not_contains": lambda col, val: ~Q(
                **{f"span_attributes__{col}__icontains": val}
            ),
            "equals": lambda col, val: Q(span_attributes__contains={col: val}),
            "not_equals": lambda col, val: ~Q(span_attributes__contains={col: val}),
            "starts_with": lambda col, val: Q(
                **{f"span_attributes__{col}__startswith": val}
            ),
            "ends_with": lambda col, val: Q(
                **{f"span_attributes__{col}__endswith": val}
            ),
            "is_null": lambda col, _: ~Q(span_attributes__has_key=col)
            | Q(span_attributes__contains={col: None}),
            "is_not_null": lambda col, _: Q(span_attributes__has_key=col)
            & ~Q(span_attributes__contains={col: None}),
        }

        number_operator_map = {
            "equals": lambda col, val: Q(span_attributes__contains={col: val}),
            "not_equals": lambda col, val: ~Q(span_attributes__contains={col: val}),
            "greater_than": lambda col, val: Q(**{f"span_attributes__{col}__gt": val}),
            "less_than": lambda col, val: Q(**{f"span_attributes__{col}__lt": val}),
            "greater_than_or_equal": lambda col, val: Q(
                **{f"span_attributes__{col}__gte": val}
            ),
            "less_than_or_equal": lambda col, val: Q(
                **{f"span_attributes__{col}__lte": val}
            ),
            "between": lambda col, val: Q(**{f"span_attributes__{col}__gte": val[0]})
            & Q(**{f"span_attributes__{col}__lte": val[1]}),
            "not_in_between": lambda col, val: ~(
                Q(**{f"span_attributes__{col}__gte": val[0]})
                & Q(**{f"span_attributes__{col}__lte": val[1]})
            ),
        }

        boolean_operator_map = {
            "equals": lambda col, val: Q(
                span_attributes__contains={
                    col: val.lower() == "true" if isinstance(val, str) else bool(val)
                }
            ),
            "not_equals": lambda col, val: ~Q(
                span_attributes__contains={
                    col: val.lower() == "true" if isinstance(val, str) else bool(val)
                }
            ),
        }

        span_attribute_filter_conditions = Q()

        for filter_item in filters:
            # Use normalization helper for consistent parameter handling
            column_id, filter_config = FilterEngine._normalize_filter_params(
                filter_item
            )
            col_type = (
                filter_config.get("col_type", "")
                if "col_type" in filter_config
                else filter_item.get("col_type", "")
            )

            # Skip if not a span attribute filter
            if (
                not column_id
                or not filter_config
                or col_type != ColType.SPAN_ATTRIBUTE.value
            ):
                continue

            filter_type = filter_config.get("filter_type")
            filter_op = filter_config.get("filter_op")
            filter_value = filter_config.get("filter_value", None)

            if filter_op not in ["is_null", "is_not_null"] and filter_value is None:
                continue

            operator_map = None
            if filter_type == "text":
                operator_map = text_operator_map
            elif filter_type == "number":
                # Use helper method for validation and conversion
                (
                    is_valid,
                    converted_value,
                ) = FilterEngine._validate_and_convert_filter_value(
                    filter_value, filter_type, filter_op
                )
                if not is_valid:
                    continue
                filter_value = converted_value
                operator_map = number_operator_map
            elif filter_type == "boolean":
                # Use helper method for validation and conversion
                (
                    is_valid,
                    converted_value,
                ) = FilterEngine._validate_and_convert_filter_value(
                    filter_value, filter_type, filter_op
                )
                if not is_valid:
                    continue
                filter_value = converted_value
                operator_map = boolean_operator_map

            if not operator_map or filter_op not in operator_map:
                continue

            try:
                operation = operator_map[filter_op]
                condition = operation(column_id, filter_value)

                # For all operations except is_null, ensure the attribute exists and span_attributes field is not null
                if filter_op != "is_null" and filter_op != "is_not_null":
                    base_condition = Q(span_attributes__isnull=False) & Q(
                        span_attributes__has_key=column_id
                    )
                    condition = base_condition & condition
                else:
                    # For null checks, still ensure span_attributes field exists
                    base_condition = Q(span_attributes__isnull=False)
                    condition = base_condition & condition

                span_attribute_filter_conditions &= condition
            except (ValueError, TypeError):
                # Skip filters with type conversion errors
                continue

        return span_attribute_filter_conditions


def _convert_to_datetime_format(date_str):
    date_formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S.%f%z",
    ]

    for date_format in date_formats:
        try:
            date_obj = datetime.strptime(date_str, date_format)
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"Date string '{date_str}' does not match any known format")

    return str(date_obj.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
