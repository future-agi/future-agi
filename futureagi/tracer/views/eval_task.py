import json
import uuid as uuid_module
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import wraps
from itertools import islice

import structlog
from django.conf import settings
from django.db import DatabaseError, connection, models, transaction
from django.db.models import (
    Count,
    Exists,
    F,
    Func,
    Max,
    OuterRef,
    Prefetch,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Cast, Coalesce, Left, Length, NullIf
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers
from rest_framework import status as drf_status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.fields import DateTimeField
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param
from rest_framework.viewsets import ModelViewSet

from model_hub.models.evals_metric import EvalTemplate
from tfc.temporal.eval_tasks.client import (
    signal_pause_eval_task_workflow,
    start_eval_task_workflow_sync,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiErrorResponseSerializer,
    EmptyRequestSerializer,
)
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.serializers.eval_task import (
    EVAL_TASK_USAGE_MAX_PAGE,
    EditEvalTaskSerializer,
    EvalTaskCreateResponseSerializer,
    EvalTaskDeleteRequestSerializer,
    EvalTaskIdQuerySerializer,
    EvalTaskListQuerySerializer,
    EvalTaskListWithProjectNameQuerySerializer,
    EvalTaskMessageResponseSerializer,
    EvalTaskSerializer,
    EvalTaskUpdateRequestSerializer,
    EvalTaskUpdateResponseSerializer,
    EvalTaskUsageQuerySerializer,
    EvalTaskUsageResponseSerializer,
)
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.eval_tasks.edit_options import validate_edit_action
from tracer.services.eval_tasks.entries import soft_delete_live
from tracer.services.filter_principal_context import (
    FilterPrincipalContextError,
    bind_request_my_annotations_principal,
)
from tracer.utils.filters import FilterEngine
from tracer.utils.helper import get_default_eval_task_config

logger = structlog.get_logger(__name__)


class _RegexpReplace(Func):
    """
    PostgreSQL `regexp_replace(string, pattern, replacement, flags)`.

    Used by get_eval_task_logs to normalize raw error strings inside the
    database so we can GROUP BY a canonical form and collapse thousands of
    near-duplicate errors (which only differ by span UUID) into a small
    set of distinct error groups.

    `output_field` is set explicitly because Django can't infer the
    result type when mixing a TextField source (`eval_explanation`) with
    Value() literal CharFields — it raises "Expression contains mixed
    types: TextField, CharField" otherwise.
    """

    function = "regexp_replace"
    arity = 4
    output_field = models.TextField()


_USAGE_DETAIL_TEXT_MAX_CHARS = settings.EVAL_TASK_USAGE_DETAIL_TEXT_MAX_CHARS
_USAGE_JSON_PREVIEW_MAX_CHARS = settings.EVAL_TASK_USAGE_JSON_PREVIEW_MAX_CHARS
_USAGE_MAPPING_PATH_LIMIT = settings.EVAL_TASK_USAGE_MAPPING_PATH_LIMIT
_USAGE_MAPPING_ENTRY_LIMIT = settings.EVAL_TASK_USAGE_MAPPING_ENTRY_LIMIT
_USAGE_MAPPING_JSON_MAX_CHARS = settings.EVAL_TASK_USAGE_MAPPING_JSON_MAX_CHARS
_USAGE_OMITTED_FIELDS_LIMIT = settings.EVAL_TASK_USAGE_OMITTED_FIELDS_LIMIT
_USAGE_AGGREGATION_JSON_MAX_CHARS = settings.EVAL_TASK_USAGE_AGGREGATION_JSON_MAX_CHARS
_USAGE_AGGREGATION_JSON_MAX_UNITS = settings.EVAL_TASK_USAGE_AGGREGATION_JSON_MAX_UNITS
_EVAL_TASK_ERROR_TEXT_MAX_CHARS = settings.EVAL_TASK_ERROR_TEXT_MAX_CHARS
_EVAL_TASK_WARNING_KEY_LIMIT = settings.EVAL_TASK_WARNING_KEY_LIMIT
_EVAL_TASK_WARNING_KEY_MAX_CHARS = settings.EVAL_TASK_WARNING_KEY_MAX_CHARS
_EVAL_TASK_WARNING_MESSAGE_MAX_CHARS = settings.EVAL_TASK_WARNING_MESSAGE_MAX_CHARS
_EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT = (
    settings.EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT
)
_EVAL_TASK_LIST_COMPATIBILITY_RELATION_LIMIT = (
    settings.EVAL_TASK_LIST_COMPATIBILITY_RELATION_LIMIT
)
_EVAL_TASK_LIST_WALL_MS = settings.INTERACTIVE_READ_DEFAULT_WALL_MS
_EVAL_TASK_ROOT_MAX_PAGE_SIZE = settings.INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE
_EVAL_TASK_LIST_MAX_OFFSET = settings.EVAL_TASK_LIST_MAX_OFFSET
_EVAL_TASK_LIST_DEFAULT_PAGE_SIZE = settings.EVAL_TASK_LIST_DEFAULT_PAGE_SIZE
_EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE = (
    settings.EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE
)
_EVAL_TASK_LIST_MAX_RESPONSE_UNITS = (
    settings.INTERACTIVE_READ_DEFAULT_MAX_RESPONSE_UNITS
)
_EVAL_TASK_LIST_COMPATIBILITY_FILTER_UNITS = (
    settings.EVAL_TASK_LIST_COMPATIBILITY_FILTER_UNITS
)
_EVAL_TASK_ROOT_JSON_PREFLIGHT_UNITS = settings.EVAL_TASK_ROOT_JSON_PREFLIGHT_UNITS


class _EvalTaskPageNumberPagination(ExtendedPageNumberPagination):
    """Bound the legacy root list route's caller-controlled ``limit``."""

    max_page_size = _EVAL_TASK_ROOT_MAX_PAGE_SIZE

    def get_schema_operation_parameters(self, view):
        parameters = super().get_schema_operation_parameters(view)
        for parameter in parameters:
            schema = parameter.get("schema", {})
            if parameter.get("name") == self.page_query_param:
                schema["minimum"] = 1
                parameter["description"] = (
                    "A one-based page number. Deep pages whose offset exceeds "
                    f"{_EVAL_TASK_LIST_MAX_OFFSET} are rejected."
                )
            elif parameter.get("name") == self.page_size_query_param:
                schema.update(
                    {
                        "minimum": 1,
                        "maximum": self.max_page_size,
                        "default": self.page_size,
                    }
                )
        return parameters


class _BoundedEvalTaskListQuerySerializer(EvalTaskListQuerySerializer):
    page_size = serializers.IntegerField(
        required=False,
        default=_EVAL_TASK_LIST_DEFAULT_PAGE_SIZE,
        min_value=1,
        max_value=_EVAL_TASK_ROOT_MAX_PAGE_SIZE,
    )


class _BoundedEvalTaskListWithProjectNameQuerySerializer(
    EvalTaskListWithProjectNameQuerySerializer
):
    page_size = serializers.IntegerField(
        required=False,
        default=_EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE,
        min_value=1,
        max_value=_EVAL_TASK_ROOT_MAX_PAGE_SIZE,
    )


class _EvalTaskListRowResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField(allow_null=True)
    project_name = serializers.CharField(required=False)
    status = serializers.CharField(allow_null=True)
    run_type = serializers.CharField(allow_null=True)
    filters_applied = serializers.JSONField(allow_null=True)
    created_at = serializers.DateTimeField()
    evals_applied = serializers.ListField(child=serializers.CharField(allow_null=True))
    sampling_rate = serializers.FloatField(allow_null=True)
    last_run = serializers.DateTimeField(allow_null=True)


class _EvalTaskListResultResponseSerializer(serializers.Serializer):
    metadata = serializers.DictField(child=serializers.IntegerField(min_value=0))
    table = _EvalTaskListRowResponseSerializer(many=True)
    config = serializers.ListField(child=serializers.JSONField())


class _EvalTaskListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = _EvalTaskListResultResponseSerializer()


class EvalTaskCompatibilityScopeTooBroad(Exception):
    """The exact Python compatibility filter would exceed its bounded scan."""


class EvalTaskPageDepthExceeded(Exception):
    """A numbered page would require an excessive OFFSET scan."""


class EvalTaskResponseTooLarge(Exception):
    """A finite page still exceeds the interactive response budget."""


def _validate_eval_task_page_depth(page_number, page_size):
    if int(page_size) > _EVAL_TASK_ROOT_MAX_PAGE_SIZE:
        raise EvalTaskResponseTooLarge
    offset = int(page_number) * int(page_size)
    if offset > _EVAL_TASK_LIST_MAX_OFFSET:
        raise EvalTaskPageDepthExceeded
    return offset


def _ensure_eval_task_response_bounded(value):
    """Conservatively bound renderer work without constructing a second JSON copy."""

    remaining = _EVAL_TASK_LIST_MAX_RESPONSE_UNITS
    stack = [value]
    while stack:
        item = stack.pop()
        if item is None or isinstance(item, bool):
            remaining -= 4
        elif isinstance(item, str):
            # One Unicode code point needs at most four UTF-8 bytes.
            remaining -= 4 * len(item) + 2
        elif isinstance(item, int | float):
            remaining -= 32
        elif isinstance(item, dict):
            remaining -= 2 + 2 * len(item)
            for key, child in item.items():
                remaining -= 4 * len(str(key)) + 2
                stack.append(child)
        elif isinstance(item, list | tuple):
            remaining -= 2 + len(item)
            stack.extend(item)
        else:
            remaining -= 4 * len(str(item)) + 2
        if remaining < 0:
            raise EvalTaskResponseTooLarge


def _bounded_eval_task_compatibility_rows(queryset):
    """Materialize at most one bounded compatibility-filter candidate set.

    A few legacy filters intentionally retain Python ``FilterEngine`` semantics
    (notably Unicode case folding and arbitrary result-dict fields).  They
    cannot be translated exactly to PostgreSQL, but they must not turn a page
    request into an unbounded table read.  Fetch one sentinel row beyond the
    cap and fail closed before serialization when the scope is too broad.
    """

    # Clear the broad relation prefetch and avoid hydrating large JSON fields
    # that the compatibility result table never emits. ``filters`` remains
    # selected because it is part of the public row.
    if hasattr(queryset, "prefetch_related") and hasattr(queryset, "only"):
        queryset = queryset.prefetch_related(None).only(
            "id",
            "project_id",
            "project__id",
            "project__name",
            "name",
            "status",
            "run_type",
            "filters",
            "created_at",
            "sampling_rate",
            "last_run",
        )
    if hasattr(queryset, "annotate") and hasattr(queryset, "values_list"):
        preflight = list(
            queryset.annotate(
                _compat_filter_chars=Coalesce(
                    Length(Cast("filters", output_field=models.TextField())),
                    Value(0),
                )
            ).values_list("id", "_compat_filter_chars")[
                : _EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT + 1
            ]
        )
        if len(preflight) > _EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT:
            raise EvalTaskCompatibilityScopeTooBroad
        if (
            sum(4 * int(filter_chars or 0) for _task_id, filter_chars in preflight)
            > _EVAL_TASK_LIST_COMPATIBILITY_FILTER_UNITS
        ):
            raise EvalTaskResponseTooLarge
        task_ids = [task_id for task_id, _filter_chars in preflight]
        rows = list(queryset.filter(id__in=task_ids)) if task_ids else []
    else:
        rows = list(queryset[: _EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT + 1])
    if len(rows) > _EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT:
        raise EvalTaskCompatibilityScopeTooBroad
    return rows


def _execute_eval_task_query_with_deadline(
    deadline, execute, sql, params, many, context
):
    """Execute one query after shrinking its PostgreSQL statement timeout."""

    remaining_ms = deadline.remaining_ms(floor_ms=1)
    context["cursor"].cursor.execute(
        "SELECT set_config('statement_timeout', %s, true)",
        (f"{remaining_ms}ms",),
    )
    result = execute(sql, params, many, context)
    deadline.remaining_ms(floor_ms=1)
    return result


@contextmanager
def _bounded_eval_task_read_transaction(deadline):
    """Apply the one request deadline to every PostgreSQL statement.

    Django's ``statement_timeout`` is per statement.  Updating it from an
    execute wrapper before each query makes the timeout shrink with the one
    monotonic request wall instead of granting every count/prefetch/page query
    a fresh 8.5 seconds.  The raw driver cursor deliberately bypasses the
    wrapper for the ``SET LOCAL`` itself.
    """

    def execute_with_remaining_timeout(execute, sql, params, many, context):
        return _execute_eval_task_query_with_deadline(
            deadline, execute, sql, params, many, context
        )

    with transaction.atomic():
        if connection.vendor != "postgresql":
            yield
            deadline.remaining_ms(floor_ms=1)
            return
        with connection.execute_wrapper(execute_with_remaining_timeout):
            yield


def _bounded_eval_task_read(view_method):
    """Run one eval-task read response inside its shared bounded PG wall."""

    @wraps(view_method)
    def wrapped(view, request, *args, **kwargs):
        deadline = ReadDeadline.start(_EVAL_TASK_LIST_WALL_MS)
        try:
            with _bounded_eval_task_read_transaction(deadline):
                response = view_method(view, request, *args, **kwargs)
                deadline.remaining_ms(floor_ms=1)
                return response
        except (ReadDeadlineExceeded, DatabaseError) as exc:
            logger.warning(
                "eval_task.read_unavailable",
                action=view_method.__name__,
                error_type=type(exc).__name__,
            )
            return view._gm.custom_error_response(
                drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                "Evaluation tasks are temporarily unavailable. Please retry.",
                code="eval_task_read_unavailable",
            )

    return wrapped


def _bounded_usage_text(value):
    text = "" if value is None else str(value)
    if len(text) <= _USAGE_DETAIL_TEXT_MAX_CHARS:
        return text
    return f"{text[:_USAGE_DETAIL_TEXT_MAX_CHARS]} [truncated]"


def _extract_partial_input_warnings(output_metadata):
    if not isinstance(output_metadata, dict):
        return []
    warnings = output_metadata.get("warnings") or []
    if isinstance(warnings, dict):
        warnings = [warnings]
    if not isinstance(warnings, list):
        return []
    result = []
    for warning in warnings:
        if not isinstance(warning, dict) or warning.get("type") != "partial_input":
            continue

        def bounded_keys(value):
            if not isinstance(value, list | tuple):
                return []
            keys = {
                str(key)[:_EVAL_TASK_WARNING_KEY_MAX_CHARS]
                for key in value
                if isinstance(key, str | int | float | bool)
            }
            return sorted(keys)[:_EVAL_TASK_WARNING_KEY_LIMIT]

        message = warning.get("message")
        if not isinstance(message, str):
            message = ""
        result.append(
            {
                "type": "partial_input",
                "empty_keys": bounded_keys(warning.get("empty_keys")),
                "filled_keys": bounded_keys(warning.get("filled_keys")),
                "message": message[:_EVAL_TASK_WARNING_MESSAGE_MAX_CHARS],
            }
        )
    return result


_USAGE_PERIOD_DELTAS = {
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "180d": timedelta(days=180),
    "365d": timedelta(days=365),
}
_USAGE_BUCKET_DELTAS = {
    "30m": timedelta(minutes=5),
    "1h": timedelta(minutes=10),
    "6h": timedelta(minutes=30),
    "1d": timedelta(hours=1),
    "7d": timedelta(hours=6),
    "30d": timedelta(days=1),
    "90d": timedelta(days=1),
    "180d": timedelta(days=1),
    "365d": timedelta(days=1),
}
_USAGE_BUCKET_ORIGIN = datetime(2000, 1, 1, tzinfo=UTC)
_USAGE_MAX_CHART_POINTS = settings.EVAL_TASK_USAGE_MAX_CHART_POINTS
_USAGE_AGGREGATION_ROW_LIMIT = settings.EVAL_TASK_USAGE_AGGREGATION_ROW_LIMIT


def _usage_bucket_delta(period, start_date, end_date, *, custom=False):
    if not custom:
        return _USAGE_BUCKET_DELTAS[period]
    duration = end_date - start_date
    if duration <= timedelta(minutes=30):
        return timedelta(minutes=5)
    if duration <= timedelta(hours=6):
        return timedelta(minutes=30)
    if duration <= timedelta(days=1):
        return timedelta(hours=1)
    if duration <= timedelta(days=7):
        return timedelta(hours=6)
    return timedelta(days=1)


def _floor_usage_bucket(value, bucket_delta):
    elapsed = value - _USAGE_BUCKET_ORIGIN
    bucket_count = int(elapsed.total_seconds() // bucket_delta.total_seconds())
    return _USAGE_BUCKET_ORIGIN + bucket_count * bucket_delta


def _build_usage_chart(aggregate_rows, start_date, end_date, bucket_delta):
    """Zero-fill an already aggregated, finite set of usage buckets."""

    rows_by_bucket = {row["bucket"]: row for row in aggregate_rows}
    current_bucket = _floor_usage_bucket(start_date, bucket_delta)
    last_bucket = _floor_usage_bucket(
        end_date - timedelta(microseconds=1), bucket_delta
    )
    chart_data = []
    while current_bucket <= last_bucket:
        if len(chart_data) >= _USAGE_MAX_CHART_POINTS:
            raise ValueError("Evaluation usage chart exceeds its bounded point limit")
        row = rows_by_bucket.get(current_bucket, {})
        avg_score = row.get("avg_score")
        chart_data.append(
            {
                "timestamp": current_bucket.isoformat(),
                "calls": int(row.get("calls") or 0),
                "pass_count": int(row.get("pass_count") or 0),
                "fail_count": int(row.get("fail_count") or 0),
                "avg_score": round(float(avg_score), 3)
                if avg_score is not None
                else None,
                "avg_latency_ms": 0,
            }
        )
        current_bucket += bucket_delta
    return chart_data


def _bounded_usage_aggregation_candidates_queryset(base_qs):
    # Only successful terminal span rows contain an aggregatable value.
    # Applying this before the newest-first cap prevents a large pending drain
    # (or skipped/error rows) from crowding completed results out of the finite
    # candidate page.
    return (
        base_qs.filter(
            status=EvalEntryStatus.COMPLETED,
            error=False,
            deleted=False,
            observation_span_id__isnull=False,
        )
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)[: _USAGE_AGGREGATION_ROW_LIMIT + 1]
    )


def _bounded_usage_aggregation_rows(base_qs):
    """Read a deterministic, finite newest-first aggregation candidate page.

    Candidate IDs are materialized before any ObservationSpan join. Date
    filters applied by the caller can therefore inspect at most 5,000 point
    identities rather than scanning an eval task's entire history.
    """

    candidate_ids = list(_bounded_usage_aggregation_candidates_queryset(base_qs))
    sampled = len(candidate_ids) > _USAGE_AGGREGATION_ROW_LIMIT
    candidate_ids = candidate_ids[:_USAGE_AGGREGATION_ROW_LIMIT]
    return candidate_ids, sampled


def _ensure_usage_aggregation_json_bounded(rows_qs):
    """Fail before hydrating an excessive deterministic-output JSON payload."""

    output_text = Coalesce(
        Cast("output_str_list", output_field=models.TextField()),
        Value(""),
        output_field=models.TextField(),
    )
    output_length = Length(output_text)
    preflight = rows_qs.aggregate(
        usage_output_chars=Coalesce(Sum(output_length), Value(0)),
        usage_output_max_chars=Coalesce(Max(output_length), Value(0)),
    )
    total_chars = int(preflight["usage_output_chars"] or 0)
    max_chars = int(preflight["usage_output_max_chars"] or 0)
    if (
        max_chars > _USAGE_AGGREGATION_JSON_MAX_CHARS
        or 4 * total_chars > _USAGE_AGGREGATION_JSON_MAX_UNITS
    ):
        raise EvalTaskResponseTooLarge


def _hydrate_usage_aggregation_rows(candidate_ids, *, span_start=None, span_end=None):
    """Hydrate only a finite set of aggregation candidates."""

    rows_qs = EvalLogger.objects.filter(
        id__in=candidate_ids,
        status=EvalEntryStatus.COMPLETED,
        error=False,
        deleted=False,
        observation_span_id__isnull=False,
    )
    if span_start is not None:
        rows_qs = rows_qs.filter(observation_span__created_at__gte=span_start)
    if span_end is not None:
        rows_qs = rows_qs.filter(observation_span__created_at__lte=span_end)
    _ensure_usage_aggregation_json_bounded(rows_qs)
    return list(
        rows_qs.order_by("-created_at", "-id").values(
            "id",
            "created_at",
            "status",
            "error",
            "observation_span_id",
            "custom_eval_config_id",
            "custom_eval_config__name",
            "custom_eval_config__eval_template_id",
            "custom_eval_config__eval_template__output_type_normalized",
            "output_bool",
            "output_float",
            "output_str_list",
        )[:_USAGE_AGGREGATION_ROW_LIMIT]
    )


def _usage_aggregation_metadata(*, candidate_count, sampled, matched_count):
    return {
        "query_complete": not sampled,
        "sampled": sampled,
        "error": "sample_limit" if sampled else None,
        "provenance": "newest_eval_task_candidates",
        "row_limit": _USAGE_AGGREGATION_ROW_LIMIT,
        "rows_scanned": candidate_count,
        "rows_matched": matched_count,
    }


def _bounded_usage_count(queryset):
    """Return a finite count and whether it is only a proven lower bound."""

    count = queryset.order_by().values("id")[: _USAGE_AGGREGATION_ROW_LIMIT + 1].count()
    return min(
        count, _USAGE_AGGREGATION_ROW_LIMIT
    ), count > _USAGE_AGGREGATION_ROW_LIMIT


def _bounded_period_usage_rows(queryset):
    """Return finite newest usage rows for stats and chart publication."""

    rows = list(
        queryset.order_by("-created_at", "-id").values(
            "created_at", "status", "output_bool", "output_float"
        )[: _USAGE_AGGREGATION_ROW_LIMIT + 1]
    )
    sampled = len(rows) > _USAGE_AGGREGATION_ROW_LIMIT
    if sampled:
        rows = rows[:_USAGE_AGGREGATION_ROW_LIMIT]
    return rows, sampled


def _terminal_usage_queryset(queryset):
    """Limit usage calls/logs to attempts which actually reached a result."""

    return queryset.filter(
        status__in=(EvalEntryStatus.COMPLETED, EvalEntryStatus.ERRORED)
    )


def _usage_logs_page_metadata(
    *,
    include_summary,
    runs_period,
    period_sampled,
    page_number,
    page_size,
    page_row_count,
    more_rows_exist,
    page_limit_reached=False,
):
    """Publish internally consistent page-plus-one count metadata.

    Summary reads know the selected-period count exactly unless their finite
    5,000-row sample filled. Log-only reads deliberately skip that count, so
    the current page plus its lookahead is the proven lower bound.
    """

    if include_summary and not period_sampled:
        logs_count = runs_period
        total_pages = max(1, (logs_count + page_size - 1) // page_size)
        return logs_count, total_pages, False

    if page_number > 1 and page_row_count == 0:
        raise ValueError("Evaluation usage page is out of range.")

    offset = (page_number - 1) * page_size if page_row_count else 0
    known_page_count = offset + page_row_count + int(more_rows_exist)
    logs_count = max(runs_period if include_summary else 0, known_page_count)
    count_is_lower_bound = (
        period_sampled if include_summary else more_rows_exist
    ) or page_limit_reached
    lower_bound_pages = (logs_count + page_size - 1) // page_size
    total_pages = max(
        1,
        lower_bound_pages,
        page_number + int(more_rows_exist and not page_limit_reached),
    )
    if page_limit_reached:
        total_pages = page_number
    return logs_count, total_pages, count_is_lower_bound


def _bounded_usage_preview(value, original_length):
    text = "" if value is None else str(value)
    if original_length is not None and original_length > len(text):
        return f"{text} [truncated]"
    return text


def _usage_json_path_expression(field_name, path, *, literal=False):
    """Extract JSONB through parameterized text path segments.

    ``KeyTransform`` compiles a single numeric-looking key such as ``"0"``
    to PostgreSQL's integer ``-> 0`` array operator. Eval mappings historically
    treat the root span-attribute object as a dict, so use
    ``jsonb_extract_path(jsonb, text...)`` to keep root keys textual while still
    allowing numeric nested segments to address arrays.
    """

    segments = (str(path),) if literal else tuple(str(path).split("."))
    return Func(
        F(field_name),
        *(Value(segment) for segment in segments),
        function="jsonb_extract_path",
        output_field=models.JSONField(),
    )


def _usage_json_text_annotation(field_name, path):
    source = Coalesce(
        Cast(
            _usage_json_path_expression(field_name, path),
            output_field=models.TextField(),
        ),
        Value(""),
        output_field=models.TextField(),
    )
    return (
        Left(source, _USAGE_JSON_PREVIEW_MAX_CHARS),
        Length(source),
    )


def _usage_json_literal_text_annotation(field_name, key):
    source = Coalesce(
        Cast(
            _usage_json_path_expression(field_name, key, literal=True),
            output_field=models.TextField(),
        ),
        Value(""),
        output_field=models.TextField(),
    )
    return (
        Left(source, _USAGE_JSON_PREVIEW_MAX_CHARS),
        Length(source),
    )


def _bounded_eval_task_error_groups_queryset(queryset):
    """Group errored entries while keeping every selected text value finite."""

    empty_text = Value("", output_field=models.TextField())
    error_source = Coalesce(
        NullIf("eval_explanation", Value("")),
        "error_message",
        empty_text,
        output_field=models.TextField(),
    )
    bounded_source = Left(error_source, _EVAL_TASK_ERROR_TEXT_MAX_CHARS)
    normalized_expr = _RegexpReplace(
        _RegexpReplace(
            F("usage_error_source"),
            Value(r"^Error during evaluation:\s*"),
            Value(""),
            Value(""),
        ),
        Value(r" for span [a-f0-9-]+$"),
        Value(""),
        Value(""),
    )
    return (
        queryset.filter(
            status=EvalEntryStatus.ERRORED,
            deleted=False,
        )
        .annotate(
            usage_error_source=bounded_source,
            usage_error_source_length=Length(error_source),
            normalized=normalized_expr,
        )
        .values("normalized")
        .annotate(
            count=Count("id"),
            sample=Max("usage_error_source"),
            sample_length=Max("usage_error_source_length"),
        )
        .order_by("-count", "normalized")
    )


def _bounded_eval_task_warning_rows_queryset(queryset):
    """Project only a bounded JSON preview of terminal partial-input warnings."""

    warnings_preview, warnings_length = _usage_json_text_annotation(
        "output_metadata", "warnings"
    )
    return (
        queryset.filter(
            status=EvalEntryStatus.COMPLETED,
            deleted=False,
            output_metadata__has_key="warnings",
        )
        .annotate(
            usage_warnings=warnings_preview,
            usage_warnings_length=warnings_length,
        )
        .values("id", "usage_warnings", "usage_warnings_length")
        .order_by("-created_at", "-id")
    )


def _build_eval_task_warning_groups(rows, *, group_limit):
    """Build finite warning groups from already bounded SQL projections."""

    warning_groups_by_key = {}
    warning_text_truncated = False
    for row in rows:
        preview = row.get("usage_warnings")
        original_length = row.get("usage_warnings_length")
        if _usage_preview_was_truncated(preview, original_length):
            warning_text_truncated = True
            continue
        warnings = _parse_usage_json_preview(preview, original_length)
        for warning in _extract_partial_input_warnings({"warnings": warnings}):
            empty_keys = warning["empty_keys"]
            filled_keys = warning["filled_keys"]
            key = tuple(empty_keys)
            if key not in warning_groups_by_key:
                warning_groups_by_key[key] = {
                    "type": "partial_input",
                    "empty_keys": empty_keys,
                    "filled_keys": filled_keys,
                    "message": warning["message"]
                    or (
                        "Eval ran with some inputs empty. "
                        "Result may be less reliable. "
                        "Ignore if this is intentional."
                    ),
                    "count": 0,
                }
            warning_groups_by_key[key]["count"] += 1

    groups = sorted(
        warning_groups_by_key.values(),
        key=lambda group: group["count"],
        reverse=True,
    )[:group_limit]
    return groups, len(warning_groups_by_key), warning_text_truncated


def _bounded_usage_logs_queryset(queryset):
    """Project a log page without transferring large span/eval JSON blobs."""

    empty_text = Value("", output_field=models.TextField())
    reason_source = Coalesce(
        NullIf("eval_explanation", Value("")),
        "error_message",
        empty_text,
        output_field=models.TextField(),
    )
    output_source = Coalesce("output_str", empty_text, output_field=models.TextField())
    error_source = Coalesce(
        "error_message", empty_text, output_field=models.TextField()
    )
    warnings_preview, warnings_length = _usage_json_text_annotation(
        "output_metadata", "warnings"
    )
    explanation_source = Coalesce(
        Cast("results_explanation", output_field=models.TextField()),
        Value(""),
        output_field=models.TextField(),
    )
    return (
        _terminal_usage_queryset(queryset)
        .annotate(
            usage_reason=Left(reason_source, _USAGE_DETAIL_TEXT_MAX_CHARS),
            usage_reason_length=Length(reason_source),
            usage_output_str=Left(output_source, _USAGE_DETAIL_TEXT_MAX_CHARS),
            usage_output_str_length=Length(output_source),
            usage_error_message=Left(error_source, _USAGE_DETAIL_TEXT_MAX_CHARS),
            usage_error_message_length=Length(error_source),
            usage_warnings=warnings_preview,
            usage_warnings_length=warnings_length,
            usage_results_explanation=Left(
                explanation_source, _USAGE_JSON_PREVIEW_MAX_CHARS
            ),
            usage_results_explanation_length=Length(explanation_source),
        )
        .select_related(
            "custom_eval_config",
            "custom_eval_config__eval_template",
            "trace_session",
        )
        .only(
            "id",
            "created_at",
            "status",
            "error",
            "output_bool",
            "output_float",
            "target_type",
            "observation_span_id",
            "trace_id",
            "trace_session_id",
            "trace_session__id",
            "trace_session__name",
            "custom_eval_config_id",
            "custom_eval_config__id",
            "custom_eval_config__name",
            "custom_eval_config__model",
            "custom_eval_config__eval_template_id",
            "custom_eval_config__eval_template__output_type_normalized",
        )
        .order_by("-created_at", "-id")
    )


def _bounded_usage_span_projection(span_ids, mapping_paths, *, project_id=None):
    """Fetch finite span identity plus only the JSON paths this page needs."""

    annotations = {}
    input_preview, input_length = _usage_json_text_annotation(
        "span_attributes", "input"
    )
    input_value_preview, input_value_length = _usage_json_literal_text_annotation(
        "span_attributes", "input.value"
    )
    annotations.update(
        usage_input=input_preview,
        usage_input_length=input_length,
        usage_input_value=input_value_preview,
        usage_input_value_length=input_value_length,
    )
    for ordinal, path in enumerate(mapping_paths):
        preview, length = _usage_json_text_annotation("span_attributes", path)
        annotations[f"usage_mapping_{ordinal}"] = preview
        annotations[f"usage_mapping_{ordinal}_length"] = length
    spans_qs = ObservationSpan.all_objects.filter(id__in=span_ids)
    if project_id is not None:
        spans_qs = spans_qs.filter(project_id=project_id)
    return (
        spans_qs.annotate(**annotations)
        .values("id", "name", "trace_id", *annotations)
        .order_by()
    )


def _parse_usage_json_preview(value, original_length):
    if value in (None, ""):
        return None
    if original_length is not None and original_length > len(value):
        return f"{value} [truncated]"
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _usage_preview_was_truncated(value, original_length):
    return bool(value) and original_length is not None and original_length > len(value)


def _append_usage_omission(omitted_fields, field_name):
    if len(omitted_fields) < _USAGE_OMITTED_FIELDS_LIMIT:
        omitted_fields.append(field_name)
    elif "additional_fields" not in omitted_fields:
        omitted_fields.append("additional_fields")


def _bounded_usage_config_mappings(config_ids):
    """Fetch each config mapping once and never transfer oversized JSON."""

    if not config_ids:
        return {}
    mapping_text = Cast("mapping", output_field=models.TextField())
    rows = (
        CustomEvalConfig.all_objects.filter(id__in=config_ids)
        .annotate(
            usage_mapping_json=Left(mapping_text, _USAGE_MAPPING_JSON_MAX_CHARS),
            usage_mapping_length=Length(mapping_text),
        )
        .values("id", "usage_mapping_json", "usage_mapping_length")
    )
    result = {}
    for row in rows:
        mapping = {}
        oversized = (row["usage_mapping_length"] or 0) > _USAGE_MAPPING_JSON_MAX_CHARS
        if not oversized:
            try:
                parsed = json.loads(row["usage_mapping_json"] or "{}")
                if isinstance(parsed, dict):
                    mapping = parsed
            except (TypeError, ValueError):
                pass
        result[str(row["id"])] = {"mapping": mapping, "oversized": oversized}
    return result


def _bounded_usage_span_context(logs_page, *, project_id=None):
    """Return bounded per-span input/mapping context for at most one log page."""

    span_ids = {log.observation_span_id for log in logs_page if log.observation_span_id}
    config_ids = {
        str(log.custom_eval_config_id) for log in logs_page if log.custom_eval_config_id
    }
    config_mappings = _bounded_usage_config_mappings(config_ids)
    bounded_entries_by_config = {}
    invalid_entries_by_config = {}
    ordered_paths = []
    for config_id in sorted(config_ids):
        mapping_record = config_mappings.get(config_id, {})
        mapping = mapping_record.get("mapping") or {}
        candidate_entries = list(islice(mapping.items(), _USAGE_MAPPING_ENTRY_LIMIT))
        valid_entries = [
            (str(variable), path)
            for variable, path in candidate_entries
            if isinstance(path, str) and path
        ]
        bounded_entries_by_config[config_id] = valid_entries
        invalid_entries_by_config[config_id] = len(candidate_entries) - len(
            valid_entries
        )
        for _, path in valid_entries:
            if (
                path not in ordered_paths
                and len(ordered_paths) < _USAGE_MAPPING_PATH_LIMIT
            ):
                ordered_paths.append(path)
    mapping_paths = ordered_paths
    rows_by_id = {
        str(row["id"]): row
        for row in _bounded_usage_span_projection(
            span_ids, mapping_paths, project_id=project_id
        )
    }
    contexts = {}
    for log in logs_page:
        row = rows_by_id.get(str(log.observation_span_id))
        if not row:
            continue
        input_value = _parse_usage_json_preview(
            row["usage_input"], row["usage_input_length"]
        )
        if not input_value:
            input_value = _parse_usage_json_preview(
                row["usage_input_value"], row["usage_input_value_length"]
            )
        if not input_value:
            input_value = row["name"] or ""
        mappings = {}
        config_id = (
            str(log.custom_eval_config_id) if log.custom_eval_config_id else None
        )
        config_record = config_mappings.get(config_id, {})
        config_mapping = config_record.get("mapping") or {}
        config_entries = bounded_entries_by_config.get(config_id, [])
        path_ordinals = {path: i for i, path in enumerate(mapping_paths)}
        for variable, path in config_entries:
            ordinal = path_ordinals.get(path)
            if ordinal is None:
                continue
            resolved = _parse_usage_json_preview(
                row[f"usage_mapping_{ordinal}"],
                row[f"usage_mapping_{ordinal}_length"],
            )
            if resolved is not None:
                mappings[str(variable)] = resolved
        omitted_fields = []
        if config_record.get("oversized"):
            _append_usage_omission(omitted_fields, "input_variables.mapping_oversized")
        elif len(config_mapping) > _USAGE_MAPPING_ENTRY_LIMIT:
            _append_usage_omission(omitted_fields, "input_variables.additional_entries")
        if invalid_entries_by_config.get(config_id):
            _append_usage_omission(omitted_fields, "input_variables.invalid_entries")
        if _usage_preview_was_truncated(
            row["usage_input"], row["usage_input_length"]
        ) or _usage_preview_was_truncated(
            row["usage_input_value"], row["usage_input_value_length"]
        ):
            _append_usage_omission(omitted_fields, "span_input_tail")
        for variable, path in config_entries:
            ordinal = path_ordinals.get(path)
            if ordinal is None:
                _append_usage_omission(omitted_fields, f"input_variables.{variable}")
            elif _usage_preview_was_truncated(
                row[f"usage_mapping_{ordinal}"],
                row[f"usage_mapping_{ordinal}_length"],
            ):
                _append_usage_omission(
                    omitted_fields, f"input_variables.{variable}_tail"
                )
        contexts[str(log.id)] = {
            "name": row["name"],
            "trace_id": row["trace_id"],
            "input": input_value,
            "input_variables": mappings,
            "omitted_fields": omitted_fields,
        }
    return contexts


def _aggregate_usage_chart_rows(rows, bucket_delta):
    """Aggregate one finite usage page into chart buckets in memory."""

    buckets = defaultdict(
        lambda: {
            "calls": 0,
            "pass_count": 0,
            "fail_count": 0,
            "score_sum": 0.0,
            "score_count": 0,
        }
    )
    for row in rows:
        row_status = row.get("status")
        if row_status not in (
            EvalEntryStatus.COMPLETED,
            EvalEntryStatus.ERRORED,
        ):
            continue
        bucket = _floor_usage_bucket(row["created_at"], bucket_delta)
        values = buckets[bucket]
        values["calls"] += 1
        if row_status == EvalEntryStatus.ERRORED:
            values["fail_count"] += 1
            continue
        if row["output_bool"] is True:
            values["pass_count"] += 1
            values["score_sum"] += 1.0
            values["score_count"] += 1
        elif row["output_bool"] is False:
            values["fail_count"] += 1
            values["score_count"] += 1
        if row["output_float"] is not None:
            values["score_sum"] += float(row["output_float"])
            values["score_count"] += 1

    return [
        {
            "bucket": bucket,
            "calls": values["calls"],
            "pass_count": values["pass_count"],
            "fail_count": values["fail_count"],
            "avg_score": (
                values["score_sum"] / values["score_count"]
                if values["score_count"]
                else None
            ),
        }
        for bucket, values in sorted(buckets.items())
    ]


def _compute_eval_aggregation(rows):
    """Per-eval-config rollup for one eval task.

    Returns a dict keyed by ``CustomEvalConfig.name`` so the FE can render
    one row per configured eval. Value shape:

        {"id": str, "name": str, "output_type": str, "aggregated_score": ...}

    ``aggregated_score`` depends on the eval's ``output_type_normalized``:
      * ``percentage``    → ``Avg(output_float)``, rounded to 4 dp.
      * ``pass_fail``     → pass-rate as 0–100 pct, 2 dp (matches the
        ``pass_rate`` field on the legacy ``get_usage`` shape).
      * ``deterministic`` → ``{choice: pct}`` dict, 2 dp. Only choices that
        actually appeared in the data are included.

    The input is the finite newest-first page returned by
    :func:`_bounded_usage_aggregation_rows`; this function never evaluates a
    queryset or performs another database read.
    """
    grouped = defaultdict(list)
    config_metadata = {}
    for row in rows:
        if row.get("status") != EvalEntryStatus.COMPLETED or row.get("error") is True:
            continue
        config_id = row["custom_eval_config_id"]
        if config_id is None:
            continue
        grouped[config_id].append(row)
        config_metadata[config_id] = {
            "name": row["custom_eval_config__name"] or "Evaluation",
            "template_id": row["custom_eval_config__eval_template_id"],
            "output_type": row[
                "custom_eval_config__eval_template__output_type_normalized"
            ]
            or "pass_fail",
        }

    result = {}
    for config_id, config_rows in grouped.items():
        metadata = config_metadata[config_id]
        output_type = metadata["output_type"]
        aggregated_score = None
        if output_type == "percentage":
            scores = [
                float(row["output_float"])
                for row in config_rows
                if row["output_float"] is not None
            ]
            aggregated_score = round(sum(scores) / len(scores), 4) if scores else None
        elif output_type == "pass_fail":
            choices = [
                row["output_bool"]
                for row in config_rows
                if row["output_bool"] is not None
            ]
            aggregated_score = (
                round(sum(choice is True for choice in choices) / len(choices) * 100, 2)
                if choices
                else None
            )
        elif output_type == "deterministic":
            counter = Counter()
            tally = 0
            for choices in (row["output_str_list"] for row in config_rows):
                if not choices:
                    continue
                tally += 1
                counter.update(set(choices))
            aggregated_score = (
                {
                    choice: round(count / tally * 100, 2)
                    for choice, count in counter.items()
                }
                if tally
                else {}
            )

        name = metadata["name"]
        result[name] = {
            "id": str(config_id),
            "name": name,
            "output_type": output_type,
            "aggregated_score": aggregated_score,
        }
    return result


def _compute_span_aggregation(rows):
    """Per-span pivot of raw eval values for one eval task.

    Returns ``{span_id → {eval_name → {id, name, output_type, value}}}``.
    ``value`` is the raw column read for the eval's output type — no
    averaging. Session/trace-target rows (``observation_span_id IS NULL``)
    are filtered out.

    When the same ``(span, eval_config)`` has multiple rows (re-runs),
    the latest by ``created_at`` wins via the ORDER BY + first-seen set.
    """
    result = defaultdict(dict)
    seen = set()
    for row in rows:
        if row.get("status") != EvalEntryStatus.COMPLETED or row.get("error") is True:
            continue
        observation_span_id = row["observation_span_id"]
        config_id = row["custom_eval_config_id"]
        if observation_span_id is None or config_id is None:
            continue
        key = (observation_span_id, config_id)
        if key in seen:
            continue
        seen.add(key)

        output_type = (
            row["custom_eval_config__eval_template__output_type_normalized"]
            or "pass_fail"
        )
        if output_type == EvalTemplate.OutputTypeNormalized.PERCENTAGE:
            value = row["output_float"]
        elif output_type == EvalTemplate.OutputTypeNormalized.PASS_FAIL:
            value = row["output_bool"]
        elif output_type == EvalTemplate.OutputTypeNormalized.DETERMINISTIC:
            value = row["output_str_list"]
        else:
            value = None

        name = row["custom_eval_config__name"] or "Evaluation"
        result[str(observation_span_id)][name] = {
            "id": str(config_id),
            "name": name,
            "output_type": output_type,
            "value": value,
        }
    return dict(result)


_EVAL_TASK_LIST_NUMBER_FIELDS = {"sampling_rate": "sampling_rate"}
_EVAL_TASK_LIST_DATETIME_FIELDS = {
    "created_at": "created_at",
    "last_run": "last_run",
}
# Keep every result-table text filter and text sort in ``FilterEngine``.
# Python's Unicode ``str.lower`` and code-point ordering are not equivalent to
# PostgreSQL's locale-dependent ILIKE/collation semantics, and a different
# ordering would move rows across page boundaries.
_EVAL_TASK_LIST_SORT_FIELDS = {
    **_EVAL_TASK_LIST_NUMBER_FIELDS,
    **_EVAL_TASK_LIST_DATETIME_FIELDS,
}


def _eval_task_number_filter_q(field_name, filter_op, filter_value):
    """Compile FilterEngine's NORMAL numeric semantics for one model field."""

    if filter_op == "is_null":
        return Q(**{f"{field_name}__isnull": True})
    if filter_op == "is_not_null":
        return Q(**{f"{field_name}__isnull": False})

    if filter_op in ("between", "not_between"):
        if not (
            isinstance(filter_value, list)
            and len(filter_value) == 2
            and all(isinstance(value, int | float) for value in filter_value)
        ):
            return None
        lower, upper = filter_value
        inside = Q(**{f"{field_name}__gte": lower}) & Q(**{f"{field_name}__lte": upper})
        if filter_op == "not_between":
            return Q(**{f"{field_name}__isnull": False}) & ~inside
        return inside

    if not isinstance(filter_value, int | float):
        return None
    lookup = {
        "greater_than": "gt",
        "less_than": "lt",
        "equals": "exact",
        "greater_than_or_equal": "gte",
        "less_than_or_equal": "lte",
    }.get(filter_op)
    if filter_op == "not_equals":
        return Q(**{f"{field_name}__isnull": False}) & ~Q(**{field_name: filter_value})
    if lookup is None:
        return None
    return Q(**{f"{field_name}__{lookup}": filter_value})


def _eval_task_filter_datetime(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _eval_task_datetime_filter_q(field_name, filter_op, filter_value):
    """Compile FilterEngine's strict UTC datetime operations."""

    if filter_op == "is_null":
        return Q(**{f"{field_name}__isnull": True})
    if filter_op == "is_not_null":
        return Q(**{f"{field_name}__isnull": False})

    if filter_op in ("between", "not_between"):
        if not isinstance(filter_value, list) or len(filter_value) != 2:
            return None
        bounds = [_eval_task_filter_datetime(value) for value in filter_value]
        if any(value is None for value in bounds):
            return None
        inside = Q(**{f"{field_name}__gte": bounds[0]}) & Q(
            **{f"{field_name}__lte": bounds[1]}
        )
        if filter_op == "not_between":
            return Q(**{f"{field_name}__isnull": False}) & ~inside
        return inside

    value = _eval_task_filter_datetime(filter_value)
    if value is None:
        return None
    if filter_op == "equals":
        return Q(**{f"{field_name}__date": value.date()})
    if filter_op == "not_equals":
        return Q(**{f"{field_name}__isnull": False}) & ~Q(
            **{f"{field_name}__date": value.date()}
        )
    lookup = {
        "greater_than": "gt",
        "less_than": "lt",
        "greater_than_or_equal": "gte",
        "less_than_or_equal": "lte",
    }.get(filter_op)
    if lookup is None:
        return None
    return Q(**{f"{field_name}__{lookup}": value})


def _eval_task_list_orm_queryset(
    queryset, *, filters, sort_params, include_project_name
):
    """Return an exactly translatable task-list queryset, or ``None``.

    ``FilterEngine`` accepts arbitrary result-dict fields. Only the scalar
    public task columns whose Python semantics can be reproduced in SQL take
    this path. Everything else deliberately falls back to the legacy engine.
    """

    sort_fields = dict(_EVAL_TASK_LIST_SORT_FIELDS)

    candidate = queryset
    for filter_item in filters:
        column_id, filter_config = FilterEngine._normalize_filter_params(filter_item)
        if not column_id or not filter_config:
            continue
        if filter_config.get("col_type") not in (None, "NORMAL"):
            return None

        filter_type = filter_config.get("filter_type")
        filter_op = filter_config.get("filter_op")
        filter_value = filter_config.get("filter_value")
        condition = None
        if filter_type == "number" and column_id in _EVAL_TASK_LIST_NUMBER_FIELDS:
            condition = _eval_task_number_filter_q(
                _EVAL_TASK_LIST_NUMBER_FIELDS[column_id], filter_op, filter_value
            )
        elif filter_type == "datetime" and column_id in _EVAL_TASK_LIST_DATETIME_FIELDS:
            condition = _eval_task_datetime_filter_q(
                _EVAL_TASK_LIST_DATETIME_FIELDS[column_id], filter_op, filter_value
            )
        else:
            return None
        if condition is None:
            return None
        candidate = candidate.filter(condition)

    if sort_params:
        ordering = []
        for sort_param in sort_params:
            field_name = sort_fields.get(sort_param.get("column_id"))
            if field_name is None:
                return None
            if sort_param.get("direction", "asc") == "desc":
                ordering.append(F(field_name).desc(nulls_first=True))
            else:
                ordering.append(F(field_name).asc(nulls_last=True))
        # Preserve the legacy -created_at tie ordering, with ID as the final
        # deterministic cursor so adjacent numbered pages cannot overlap.
        ordering.append(F("created_at").desc())
        ordering.append(F("id").asc())
        candidate = candidate.order_by(*ordering)
    else:
        candidate = candidate.order_by("-created_at", "id")
    return candidate


def _serialize_eval_task_list_page(tasks, *, include_project_name):
    datetime_field = DateTimeField()
    result = []
    for eval_task in tasks:
        row = {
            "id": str(eval_task.id),
            "name": eval_task.name,
            "status": eval_task.status,
            "run_type": eval_task.run_type,
            "filters_applied": eval_task.filters,
            "created_at": datetime_field.to_representation(eval_task.created_at),
            "evals_applied": [
                eval_config.name for eval_config in eval_task._list_evals
            ],
            "sampling_rate": eval_task.sampling_rate,
            "last_run": datetime_field.to_representation(eval_task.last_run)
            if eval_task.last_run is not None
            else None,
        }
        if include_project_name:
            row["project_name"] = eval_task.project.name
        result.append(row)
    return result


def _eval_task_progress_by_id(tasks):
    """Batch historical progress for a finite root-list page.

    ``EvalTaskSerializer.get_progress`` issues one grouped query per historical
    task.  The root route can return a large page, so serialize that field from
    one grouped query instead of allowing a page-sized N+1 query pattern.
    """

    historical_ids = [
        str(task.id) for task in tasks if task.run_type == RunType.HISTORICAL
    ]
    if not historical_ids:
        return {}

    counts_by_task = defaultdict(Counter)
    rows = (
        EvalLogger.objects.filter(eval_task_id__in=historical_ids)
        .values("eval_task_id", "status")
        .annotate(n=Count("id"))
        .order_by()
    )
    for row in rows:
        counts_by_task[str(row["eval_task_id"])][row["status"]] = row["n"]

    progress_by_id = {}
    for task_id in historical_ids:
        counts = counts_by_task[task_id]
        done = (
            counts.get(EvalEntryStatus.COMPLETED, 0)
            + counts.get(EvalEntryStatus.ERRORED, 0)
            + counts.get(EvalEntryStatus.SKIPPED, 0)
        )
        remaining = counts.get(EvalEntryStatus.PENDING, 0) + counts.get(
            EvalEntryStatus.RUNNING, 0
        )
        total = done + remaining
        progress_by_id[task_id] = {
            "dispatched": total,
            "completed": done,
            "missing": remaining,
            "percent": round(100.0 * done / total, 2) if total else None,
        }
    return progress_by_id


class EvalTaskView(BaseModelViewSetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = _EvalTaskPageNumberPagination
    _gm = GeneralMethods()
    serializer_class = EvalTaskSerializer

    @swagger_auto_schema(
        responses={
            400: ApiErrorResponseSerializer,
            422: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        }
    )
    @_bounded_eval_task_read
    def list(self, request, *args, **kwargs):
        """Return one finite page without per-task progress queries."""

        requested_page = request.query_params.get(
            self.pagination_class.page_query_param, 1
        )
        requested_limit = request.query_params.get(
            self.pagination_class.page_size_query_param,
            self.pagination_class.page_size,
        )
        if requested_page in self.pagination_class.last_page_strings:
            return self._eval_task_page_depth_response()
        try:
            # DRF's legacy ``page=last`` shortcut can turn a small request into
            # an arbitrary deep OFFSET, so the bounded route accepts only an
            # explicit finite numbered page.
            page_number = int(requested_page)
            page_size = min(int(requested_limit), _EVAL_TASK_ROOT_MAX_PAGE_SIZE)
            if page_number < 1 or page_size < 1:
                raise ValueError
            _validate_eval_task_page_depth(page_number - 1, page_size)
        except EvalTaskPageDepthExceeded:
            return self._eval_task_page_depth_response()
        except (TypeError, ValueError):
            return self._gm.bad_request(
                "page and limit must be positive integers for this bounded read."
            )

        queryset = self.filter_queryset(self.get_queryset())
        preflight_queryset = (
            queryset.select_related(None)
            .prefetch_related(None)
            .only("id")
            .annotate(
                _root_json_chars=(
                    Coalesce(
                        Length(Cast("filters", output_field=models.TextField())),
                        Value(0),
                    )
                    + Coalesce(
                        Length(Cast("evals_details", output_field=models.TextField())),
                        Value(0),
                    )
                    + Coalesce(
                        Length(Cast("failed_spans", output_field=models.TextField())),
                        Value(0),
                    )
                )
            )
        )
        page = self.paginate_queryset(preflight_queryset)
        page_refs = list(page if page is not None else preflight_queryset)
        if (
            sum(4 * int(task._root_json_chars or 0) for task in page_refs)
            > _EVAL_TASK_ROOT_JSON_PREFLIGHT_UNITS
        ):
            return self._eval_task_response_too_large()

        task_ids = [task.id for task in page_refs]
        tasks = (
            list(queryset.prefetch_related(None).filter(id__in=task_ids))
            if task_ids
            else []
        )
        try:
            self._attach_bounded_compatibility_evals(tasks)
        except EvalTaskCompatibilityScopeTooBroad:
            return self._eval_task_response_too_large()

        serializer = self.get_serializer(tasks, many=True)
        # This field is filled below from one grouped query. Serializer fields
        # are deep-copied per instance, so removing it here is request-local.
        serializer.child.fields.pop("progress", None)
        rows = list(serializer.data)
        progress_by_id = _eval_task_progress_by_id(tasks)
        for task, row in zip(tasks, rows, strict=True):
            row["progress"] = progress_by_id.get(str(task.id))

        try:
            _ensure_eval_task_response_bounded(rows)
        except EvalTaskResponseTooLarge:
            return self._eval_task_response_too_large()

        if page is not None:
            return self.get_paginated_response(rows)
        return self._gm.success_response(rows)

    @swagger_auto_schema(
        responses={
            200: EvalTaskSerializer,
            400: ApiErrorResponseSerializer,
            422: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        }
    )
    @_bounded_eval_task_read
    def retrieve(self, request, *args, **kwargs):
        """Return one task without unbounded JSON or relation hydration."""

        queryset = self.filter_queryset(self.get_queryset())
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        lookup_value = self.kwargs[lookup_url_kwarg]
        preflight_queryset = (
            queryset.select_related(None)
            .prefetch_related(None)
            .only("id")
            .annotate(
                _root_json_chars=(
                    Coalesce(
                        Length(Cast("filters", output_field=models.TextField())),
                        Value(0),
                    )
                    + Coalesce(
                        Length(Cast("evals_details", output_field=models.TextField())),
                        Value(0),
                    )
                    + Coalesce(
                        Length(Cast("failed_spans", output_field=models.TextField())),
                        Value(0),
                    )
                )
            )
        )
        preflight = get_object_or_404(
            preflight_queryset,
            **{self.lookup_field: lookup_value},
        )
        self.check_object_permissions(request, preflight)
        if (
            4 * int(preflight._root_json_chars or 0)
            > _EVAL_TASK_ROOT_JSON_PREFLIGHT_UNITS
        ):
            return self._eval_task_response_too_large()

        instance = queryset.prefetch_related(None).get(pk=preflight.pk)
        try:
            self._attach_bounded_compatibility_evals([instance])
        except EvalTaskCompatibilityScopeTooBroad:
            return self._eval_task_response_too_large()
        data = self.get_serializer(instance).data
        try:
            _ensure_eval_task_response_bounded(data)
        except EvalTaskResponseTooLarge:
            return self._eval_task_response_too_large()
        return Response(data)

    def _eval_task_page_depth_response(self):
        return self._gm.custom_error_response(
            drf_status.HTTP_422_UNPROCESSABLE_ENTITY,
            (
                "This page is too deep for an interactive read. Narrow the "
                "scope or request an earlier page."
            ),
            code="eval_task_page_depth_exceeded",
        )

    def _eval_task_response_too_large(self):
        return self._gm.custom_error_response(
            drf_status.HTTP_422_UNPROCESSABLE_ENTITY,
            (
                "This page is too large to render interactively. Request a "
                "smaller page or narrow the scope."
            ),
            code="eval_task_response_too_large",
        )

    def _get_request_organization(self):
        # Returns None for unauthenticated requests (e.g. drf-yasg's fake view
        # during OpenAPI generation) instead of raising on AnonymousUser, which
        # would otherwise silently drop request bodies from the generated schema.
        org = getattr(self.request, "organization", None)
        if org is not None:
            return org
        user = getattr(self.request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        return getattr(user, "organization", None)

    def _project_workspace_scope_q(self, organization_id):
        workspace = getattr(self.request, "workspace", None)
        if not workspace:
            return Q()
        if getattr(workspace, "is_default", False):
            return (
                Q(project__workspace=workspace)
                | Q(
                    project__workspace__is_default=True,
                    project__workspace__organization_id=organization_id,
                )
                | Q(
                    project__workspace__isnull=True,
                    project__organization_id=organization_id,
                )
            )
        return Q(project__workspace=workspace)

    def _scope_eval_task_queryset(self, queryset):
        organization = self._get_request_organization()
        if organization is None:
            return queryset.none()
        organization_id = organization.id
        return queryset.filter(
            project__organization_id=organization_id,
            project__deleted=False,
        ).filter(self._project_workspace_scope_q(organization_id))

    def _scope_project_queryset(self, queryset):
        organization = self._get_request_organization()
        if organization is None:
            return queryset.none()
        organization_id = organization.id
        workspace = getattr(self.request, "workspace", None)
        queryset = queryset.filter(organization_id=organization_id, deleted=False)
        if not workspace:
            return queryset
        if getattr(workspace, "is_default", False):
            return queryset.filter(
                Q(workspace=workspace)
                | Q(
                    workspace__is_default=True,
                    workspace__organization_id=organization_id,
                )
                | Q(workspace__isnull=True, organization_id=organization_id)
            )
        return queryset.filter(workspace=workspace)

    def _scope_custom_eval_config_queryset(self, queryset, project_id=None):
        organization = self._get_request_organization()
        if organization is None:
            return queryset.none()
        organization_id = organization.id
        queryset = queryset.filter(
            deleted=False,
            project__organization_id=organization_id,
            project__deleted=False,
        ).filter(self._project_workspace_scope_q(organization_id))
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset

    def _invalid_eval_ids_for_project(self, eval_ids, project_id):
        requested_ids = {str(eval_id) for eval_id in (eval_ids or [])}
        if not requested_ids:
            return []
        visible_ids = {
            str(eval_id)
            for eval_id in self._scope_custom_eval_config_queryset(
                CustomEvalConfig.objects.all(), project_id=project_id
            )
            .filter(id__in=requested_ids)
            .values_list("id", flat=True)
        }
        return sorted(requested_ids - visible_ids)

    def get_serializer(self, *args, **kwargs):
        serializer = super().get_serializer(*args, **kwargs)
        fields = getattr(serializer, "fields", None)
        if fields is None and getattr(serializer, "child", None) is not None:
            fields = getattr(serializer.child, "fields", None)
        if not fields:
            return serializer
        if "project" in fields:
            fields["project"].queryset = self._scope_project_queryset(
                Project.objects.all()
            )
        if "evals" in fields:
            fields["evals"].queryset = self._scope_custom_eval_config_queryset(
                CustomEvalConfig.objects.all()
            )
        return serializer

    def get_queryset(self):
        eval_task_id = self.kwargs.get("pk")

        # Get base queryset with automatic filtering from mixin
        queryset = self._scope_eval_task_queryset(super().get_queryset())
        queryset = queryset.select_related("project")
        queryset = queryset.prefetch_related(
            Prefetch(
                "evals",
                queryset=self._scope_custom_eval_config_queryset(
                    CustomEvalConfig.objects.all()
                ).only("id", "name", "project_id"),
            )
        )

        if eval_task_id:
            queryset = queryset.filter(id=eval_task_id)

        project_id = self.request.query_params.get("project_id")
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        search_name = self.request.query_params.get("name")
        if search_name:
            queryset = queryset.filter(name__icontains=search_name)

        # A stable tie-breaker is required for numbered read-more pages.
        return queryset.order_by("-created_at", "id")

    def _eval_task_orm_page(self, queryset, query_data, *, include_project_name):
        filters = query_data.get("filters", [])
        sort_params = query_data.get("sort_params", [])
        queryset = _eval_task_list_orm_queryset(
            queryset,
            filters=filters,
            sort_params=sort_params,
            include_project_name=include_project_name,
        )
        if queryset is None:
            return None

        # Both legacy list implementations discard tasks whose related manager
        # has no visible CustomEvalConfig. The related manager subclasses
        # CustomEvalConfig.objects, so deleted configs are invisible here too.
        active_eval = self._scope_custom_eval_config_queryset(
            CustomEvalConfig.objects.all()
        ).filter(eval_tasks=OuterRef("pk"))
        queryset = queryset.annotate(_has_active_eval=Exists(active_eval)).filter(
            _has_active_eval=True
        )
        page_number = query_data.get("page_number", 0)
        page_size = query_data.get("page_size", _EVAL_TASK_LIST_DEFAULT_PAGE_SIZE)
        start = _validate_eval_task_page_depth(page_number, page_size)
        total_rows = queryset.count()
        end = start + int(page_size)
        page_queryset = queryset.prefetch_related(None).select_related("project")[
            start:end
        ]
        tasks = self._attach_bounded_compatibility_evals(list(page_queryset))
        result = _serialize_eval_task_list_page(
            tasks, include_project_name=include_project_name
        )
        return result, total_rows

    def _attach_bounded_compatibility_evals(self, tasks):
        """Attach finite, tenant-scoped eval names without broad prefetches."""

        task_ids = [task.id for task in tasks]
        if not task_ids:
            return tasks

        eval_configs = list(
            self._scope_custom_eval_config_queryset(CustomEvalConfig.objects.all())
            .filter(eval_tasks__id__in=task_ids)
            .annotate(_prefetch_related_val_evaltask_id=F("eval_tasks__id"))
            .only("id", "name", "project_id", "created_at")
            .order_by("_prefetch_related_val_evaltask_id", "-created_at", "id")[
                : _EVAL_TASK_LIST_COMPATIBILITY_RELATION_LIMIT + 1
            ]
        )
        if len(eval_configs) > _EVAL_TASK_LIST_COMPATIBILITY_RELATION_LIMIT:
            raise EvalTaskCompatibilityScopeTooBroad

        evals_by_task = defaultdict(list)
        for config in eval_configs:
            evals_by_task[str(config._prefetch_related_val_evaltask_id)].append(config)
        for task in tasks:
            task._list_evals = evals_by_task[str(task.id)]
            task._prefetched_objects_cache = {
                **getattr(task, "_prefetched_objects_cache", {}),
                "evals": task._list_evals,
            }
        return tasks

    @validated_request(request_serializer=EvalTaskSerializer)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @validated_request(
        request_serializer=EvalTaskSerializer,
        partial_request_validation=True,
        strict_request_validation=False,
    )
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        # Cascade soft-delete to the task's loggers and eval results so they
        # don't outlive the deleted task (mirrors mark_eval_tasks_deleted).
        now = timezone.now()
        EvalTaskLogger.objects.filter(eval_task_id=instance.id).update(
            deleted=True, deleted_at=now
        )
        EvalLogger.objects.filter(eval_task_id=instance.id).update(
            deleted=True, deleted_at=now
        )
        instance.delete()

    @validated_request(
        request_serializer=EvalTaskSerializer,
        responses={
            200: EvalTaskCreateResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
    )
    def create(self, request, *args, **kwargs):
        try:
            data = request.data.copy()
            data["status"] = EvalTaskStatus.PENDING
            filters = data.get("filters") or {}
            project_id = data.get("project")
            if (
                project_id
                and not self._scope_project_queryset(Project.objects.all())
                .filter(id=project_id)
                .exists()
            ):
                return self._gm.bad_request("Project not found")
            if project_id:
                filters["project_id"] = project_id
            data["filters"] = filters

            data["last_run"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            invalid_eval_ids = self._invalid_eval_ids_for_project(
                [eval_config.id for eval_config in serializer.validated_data["evals"]],
                project_id,
            )
            if invalid_eval_ids:
                return self._gm.bad_request(
                    "Eval configs not found for project: " + ", ".join(invalid_eval_ids)
                )
            eval_task = serializer.save()

            # The workflow's first step materializes entries, so create returns
            # immediately even for large tasks.
            start_eval_task_workflow_sync(eval_task)

            return self._gm.success_response({"id": eval_task.id})

        except ValidationError as exc:
            return self._gm.bad_request(exc.detail)
        except FilterPrincipalContextError as exc:
            return self._gm.bad_request(str(exc))
        except Exception as exc:
            logger.exception(
                "eval_task.create_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Evaluation task could not be created")

    @action(detail=False, methods=["get"], pagination_class=None)
    @validated_request(
        query_serializer=_BoundedEvalTaskListQuerySerializer,
        responses={
            200: _EvalTaskListResponseSerializer,
            400: ApiErrorResponseSerializer,
            422: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
    )
    @_bounded_eval_task_read
    def list_eval_tasks(self, request, *args, **kwargs):
        """
        List Eval Tasks filtered
        """
        try:
            query_data = request.validated_query_data
            _validate_eval_task_page_depth(
                query_data.get("page_number", 0),
                query_data.get("page_size", _EVAL_TASK_LIST_DEFAULT_PAGE_SIZE),
            )

            queryset = self.get_queryset()
            orm_page = self._eval_task_orm_page(
                queryset, query_data, include_project_name=False
            )
            if orm_page is not None:
                result, total_rows = orm_page
            else:
                # Compatibility path for arbitrary result-dict filters/sorts
                # whose FilterEngine behavior cannot be translated exactly.
                # Preserve those semantics for normal scopes, but never hydrate
                # an unbounded task population merely to return one page.
                compatibility_rows = self._attach_bounded_compatibility_evals(
                    _bounded_eval_task_compatibility_rows(queryset)
                )
                result = _serialize_eval_task_list_page(
                    [task for task in compatibility_rows if task._list_evals],
                    include_project_name=False,
                )

                filters = query_data.get("filters", [])
                if filters:
                    result = FilterEngine(result).apply_filters(filters)

                sort_params = query_data.get("sort_params", [])
                if sort_params:
                    for sort_param in reversed(sort_params):
                        sort_key = sort_param.get("column_id")
                        reverse = sort_param.get("direction", "asc") == "desc"

                        def sort_key_func(x):
                            value = x.get(sort_key)  # noqa: B023
                            return (value is None, value)

                        result.sort(key=sort_key_func, reverse=reverse)

                total_rows = len(result)
                page_number = query_data.get("page_number", 0)
                page_size = query_data.get(
                    "page_size", _EVAL_TASK_LIST_DEFAULT_PAGE_SIZE
                )
                start = int(page_number) * int(page_size)
                result = result[start : start + int(page_size)]

            # Update config to include project name
            config = get_default_eval_task_config()

            response = {
                "metadata": {
                    "total_rows": total_rows,
                },
                "table": result,
                "config": config,
            }

            _ensure_eval_task_response_bounded(response)

            return self._gm.success_response(response)

        except (ReadDeadlineExceeded, DatabaseError):
            raise
        except EvalTaskCompatibilityScopeTooBroad:
            return self._gm.custom_error_response(
                drf_status.HTTP_422_UNPROCESSABLE_ENTITY,
                (
                    "This filter or sort is too broad to evaluate exactly. "
                    "Select a project or enter a more specific name and retry."
                ),
                code="eval_task_filter_scope_too_large",
            )
        except EvalTaskPageDepthExceeded:
            return self._eval_task_page_depth_response()
        except EvalTaskResponseTooLarge:
            return self._eval_task_response_too_large()
        except Exception as exc:
            logger.exception(
                "eval_task.list_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Evaluation tasks could not be loaded")

    # Maximum number of distinct error groups returned per task. Most tasks
    # produce 1-5 distinct error types; this cap is a safety net for tasks
    # with many varied custom-eval failures and keeps the payload bounded.
    _ERROR_GROUPS_LIMIT = settings.EVAL_TASK_ERROR_GROUPS_LIMIT
    _WARNING_GROUPS_LIMIT = settings.EVAL_TASK_WARNING_GROUPS_LIMIT
    _WARNING_LOG_SCAN_LIMIT = settings.EVAL_TASK_WARNING_LOG_SCAN_LIMIT

    @validated_request(
        responses={
            400: ApiErrorResponseSerializer,
            422: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        }
    )
    @action(detail=False, methods=["get"])
    @_bounded_eval_task_read
    def get_eval_task_logs(self, request, *args, **kwargs):
        try:
            eval_task_id = self.request.query_params.get("eval_task_id")
            eval_task = (
                self._scope_eval_task_queryset(EvalTask.objects)
                .only(
                    "id",
                    "project_id",
                    "start_time",
                    "end_time",
                    "status",
                    "run_type",
                    "row_type",
                )
                .get(id=eval_task_id)
            )

            # Progress counts — cheap aggregate, indexed COUNTs. Counted by the
            # entry's lifecycle ``status`` (not the ``error``/``skipped_reason``
            # result columns): a pending/running entry has error=False and
            # skipped_reason=null, so result-column counting would tally every
            # not-yet-run entry as a success. ``total_count`` is every
            # materialized entry (the manager already excludes soft-deleted),
            # so while a task is pending Total shows the full set and success/
            # errors start at 0 and climb as the drain executes.
            counts = EvalLogger.objects.filter(
                eval_task_id=eval_task_id,
                deleted=False,
            ).aggregate(
                total_count=Count("id"),
                success_count=Count("id", filter=Q(status=EvalEntryStatus.COMPLETED)),
                errors_count=Count("id", filter=Q(status=EvalEntryStatus.ERRORED)),
                # Skipped: the eval never ran (e.g. a mapped span attribute
                # was absent). Counted separately so it stays out of the
                # success and failure tallies.
                skipped_count=Count("id", filter=Q(status=EvalEntryStatus.SKIPPED)),
                # Partial-input warnings live in
                # output_metadata.warnings as a JSON array. has_key on
                # the JSONField gives us a cheap "any warnings?" filter
                # without scanning the contents.
                warnings_count=Count(
                    "id",
                    filter=Q(
                        status=EvalEntryStatus.COMPLETED,
                        output_metadata__has_key="warnings",
                    ),
                ),
            )

            # ── Pre-aggregate error groups in SQL ──
            #
            # Previously this endpoint returned a raw ArrayAgg of every
            # error string — for tasks with thousands of failures that's
            # multi-MB of payload, slow to serialize, and forced the
            # frontend to walk every string just to count duplicates.
            #
            # Instead we normalize each error in the DB (strip the
            # uniform "Error during evaluation: " prefix and the trailing
            # " for span <uuid>" so duplicates collapse), GROUP BY the
            # normalized form, and return one row per distinct error type
            # with a count and one sample. The payload becomes ~100 bytes
            # per group instead of ~200 bytes per error row.
            #
            # The frontend's classifier (classifyTaskError.js) does a
            # second pattern-match pass on the sample to attach a title,
            # icon, severity, and "How to fix" hints. The normalization
            # rules here are kept in sync with that classifier — see
            # core-frontend/src/sections/common/EvalsTasks/classifyTaskError.js
            error_group_rows = list(
                _bounded_eval_task_error_groups_queryset(
                    EvalLogger.objects.filter(eval_task_id=eval_task_id)
                )[: self._ERROR_GROUPS_LIMIT + 1]
            )
            error_groups = [
                {
                    "normalized": row["normalized"] or "Unknown error",
                    "count": row["count"],
                    "sample": _bounded_usage_preview(
                        row["sample"], row["sample_length"]
                    ),
                }
                for row in error_group_rows[: self._ERROR_GROUPS_LIMIT]
            ]
            error_text_truncated = any(
                (row["sample_length"] or 0) > _EVAL_TASK_ERROR_TEXT_MAX_CHARS
                for row in error_group_rows[: self._ERROR_GROUPS_LIMIT]
            )

            warning_rows = list(
                _bounded_eval_task_warning_rows_queryset(
                    EvalLogger.objects.filter(eval_task_id=eval_task_id)
                )[: self._WARNING_LOG_SCAN_LIMIT]
            )
            (
                warning_groups,
                warning_group_count,
                warning_text_truncated,
            ) = _build_eval_task_warning_groups(
                warning_rows,
                group_limit=self._WARNING_GROUPS_LIMIT,
            )

            result = {
                "start_time": eval_task.start_time,
                "end_time": eval_task.end_time,
                # Task status travels with the counts (same response) so the
                # frontend can keep polling until it observes a terminal status,
                # and the fetch that first sees "completed" already carries the
                # final tallies — no off-by-one-tick stale count.
                "status": eval_task.status,
                # Duration is only meaningful for historical runs (which finalize
                # with an end_time). Continuous tasks never end, so the frontend
                # hides the Duration card based on this.
                "run_type": eval_task.run_type,
                "errors_count": counts["errors_count"],
                "success_count": counts["success_count"],
                "skipped_count": counts["skipped_count"],
                "warnings_count": counts["warnings_count"],
                "total_count": counts["total_count"],
                "error_groups": error_groups,
                "warning_groups": warning_groups,
                # Indicates whether we capped at _ERROR_GROUPS_LIMIT — the
                # frontend can show a "showing top 50 error types" hint.
                "error_groups_truncated": len(error_group_rows)
                > self._ERROR_GROUPS_LIMIT
                or error_text_truncated,
                "warning_groups_truncated": counts["warnings_count"]
                > self._WARNING_LOG_SCAN_LIMIT
                or warning_group_count > self._WARNING_GROUPS_LIMIT
                or warning_text_truncated,
                "row_type": eval_task.row_type,
            }

            _ensure_eval_task_response_bounded(result)
            return self._gm.success_response(result)

        except (ReadDeadlineExceeded, DatabaseError):
            raise
        except EvalTask.DoesNotExist:
            return self._gm.bad_request(f"EvalTask with id {eval_task_id} not found.")
        except EvalTaskResponseTooLarge:
            return self._eval_task_response_too_large()
        except Exception as exc:
            logger.exception(
                "eval_task.logs_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Evaluation task logs could not be loaded")

    # ──────────────────────────────────────────────────────────────────
    # GET /tracer/eval-task/get_usage/
    #
    # Stats row + time-series chart + paginated logs for one eval task.
    # Mirrors `EvalUsageStatsView`'s response shape so the frontend reuses
    # `UsageChart`, `DataTable` and `DataTablePagination` unchanged. The
    # computations use the bounded helpers above so this request cannot expand
    # into an unbounded task-history scan.
    # ──────────────────────────────────────────────────────────────────
    @validated_request(
        query_serializer=EvalTaskUsageQuerySerializer,
        responses={
            200: EvalTaskUsageResponseSerializer,
            400: ApiErrorResponseSerializer,
            422: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"], pagination_class=None)
    @_bounded_eval_task_read
    def get_usage(self, request, *args, **kwargs):
        try:
            query_data = request.validated_query_data
            eval_task_id = str(query_data["eval_task_id"])
            is_aggregation = (
                query_data["eval_aggregation"] or query_data["span_aggregation"]
            )
            eval_task_qs = self._scope_eval_task_queryset(EvalTask.objects).only(
                "id", "project_id"
            )
            if query_data["include_summary"] and not is_aggregation:
                configured_evals_qs = CustomEvalConfig.objects.select_related(
                    "eval_template"
                ).only(
                    "id",
                    "name",
                    "model",
                    "eval_template_id",
                    "eval_template__id",
                    "eval_template__output_type_normalized",
                )
                eval_task_qs = eval_task_qs.prefetch_related(
                    Prefetch("evals", queryset=configured_evals_qs)
                )
            eval_task = eval_task_qs.filter(id=eval_task_id).first()
            if eval_task is None:
                return self._gm.bad_request(
                    f"EvalTask with id {eval_task_id} not found."
                )

            # ── Query params ──
            page_size = query_data["page_size"]
            period = query_data["period"]
            # Optional eval filter — tasks may run multiple evals; the UI
            # passes this when the user picks one from the dropdown.
            eval_id = query_data.get("eval_id")
            eval_id_filter = str(eval_id) if eval_id else None
            requested_start = query_data.get("start_date")
            requested_end = query_data.get("end_date")
            if requested_start is not None and requested_end is not None:
                start_date = requested_start
                end_date = requested_end
                period_requested = "custom"
                period_used = "custom"
            else:
                end_date = timezone.now()
                start_date = end_date - _USAGE_PERIOD_DELTAS[period]
                period_requested = period
                period_used = period

            # ── Aggregation short-circuit ──
            # When either flag is set, return ONLY the aggregated payload.
            # Soft-deleted rows are excluded (intentional departure from the
            # legacy path) so rollups reflect the user's current view of
            # the data. For compatibility, optional explicit bounds apply
            # inclusively to the linked span's created_at. With no explicit
            # bounds this stays task-wide and avoids a span join entirely.
            #
            # Spans-only semantics: session-target rows (``observation_span_id
            # IS NULL``) are excluded from both aggregations so the row set
            # is consistent whether or not a date range is supplied.
            #
            # The finite newest-first aggregation page publishes explicit
            # completeness metadata instead of silently scanning all history.
            eval_aggregation = query_data["eval_aggregation"]
            span_aggregation = query_data["span_aggregation"]
            if is_aggregation:
                agg_base_qs = EvalLogger.objects.filter(
                    eval_task_id=str(eval_task_id),
                    deleted=False,
                )
                if eval_id_filter:
                    agg_base_qs = agg_base_qs.filter(
                        custom_eval_config_id=eval_id_filter
                    )
                aggregation_candidate_ids, aggregation_sampled = (
                    _bounded_usage_aggregation_rows(agg_base_qs)
                )
                aggregation_rows = _hydrate_usage_aggregation_rows(
                    aggregation_candidate_ids,
                    span_start=requested_start,
                    span_end=requested_end,
                )
                aggregation_metadata = _usage_aggregation_metadata(
                    candidate_count=len(aggregation_candidate_ids),
                    sampled=aggregation_sampled,
                    matched_count=len(aggregation_rows),
                )
                agg_response = {
                    "eval_task_id": str(eval_task_id),
                    "aggregation_metadata": aggregation_metadata,
                }
                if requested_start is not None or requested_end is not None:
                    agg_response.update(
                        period_requested="custom",
                        period_used="custom",
                    )
                if eval_aggregation:
                    agg_response["eval_aggregation"] = _compute_eval_aggregation(
                        aggregation_rows
                    )
                if span_aggregation:
                    agg_response["span_aggregation"] = _compute_span_aggregation(
                        aggregation_rows
                    )
                _ensure_eval_task_response_bounded(agg_response)
                return self._gm.success_response(agg_response)

            # ── Configured evals on this task (drives the filter dropdown) ──
            # Read the task's finite configured-eval relation. Deriving this
            # dropdown through all historical EvalLogger rows made an empty
            # selected period scan the task's entire history before paging.
            include_summary = query_data["include_summary"]
            evals_meta = []
            if include_summary:
                configured_eval_configs = list(eval_task.evals.all())
                evals_meta = [
                    {
                        "id": str(config.id),
                        "name": config.name or "Evaluation",
                        "output_type": (
                            config.eval_template.output_type_normalized
                            if config.eval_template
                            else "pass_fail"
                        ),
                        "template_id": (
                            str(config.eval_template_id)
                            if config.eval_template_id
                            else None
                        ),
                        "model": config.model,
                    }
                    for config in configured_eval_configs
                ]

            # ── Base queryset ──
            # Match the existing get_eval_task_logs filter exactly so any
            # task that shows logs also shows usage. Soft-deleted predecessor
            # work items are excluded so re-evaluation never double-counts a
            # superseded result and the partial time index stays applicable.
            base_qs = _terminal_usage_queryset(
                EvalLogger.objects.filter(
                    eval_task_id=str(eval_task_id),
                    deleted=False,
                )
            )
            if eval_id_filter:
                base_qs = base_qs.filter(custom_eval_config_id=eval_id_filter)

            period_qs = base_qs.filter(
                created_at__gte=start_date,
                created_at__lt=end_date,
            )
            total_runs = 0
            total_runs_is_lower_bound = False
            period_rows = []
            period_sampled = False
            if include_summary:
                total_runs, total_runs_is_lower_bound = _bounded_usage_count(base_qs)
                period_rows, period_sampled = _bounded_period_usage_rows(period_qs)
            runs_period = len(period_rows)
            success_count = sum(
                row["status"] == EvalEntryStatus.COMPLETED for row in period_rows
            )
            error_count = sum(
                row["status"] == EvalEntryStatus.ERRORED for row in period_rows
            )
            pass_rate = (
                round((success_count / runs_period * 100), 2) if runs_period > 0 else 0
            )

            # ── Chart data — bucket by period and aggregate ──
            chart_data = []
            if include_summary and runs_period > 0:
                bucket_delta = _usage_bucket_delta(
                    period,
                    start_date,
                    end_date,
                    custom=period_used == "custom",
                )
                aggregate_rows = _aggregate_usage_chart_rows(period_rows, bucket_delta)
                chart_data = _build_usage_chart(
                    aggregate_rows,
                    start_date,
                    end_date,
                    bucket_delta,
                )

            # ── Paginated logs ──
            # Load scalar log/config/session fields in one bounded page. Span
            # JSON is deliberately projected in one separate PK-IN query below,
            # so a page never transfers each span's unrestricted attribute map.
            logs_qs = _bounded_usage_logs_queryset(period_qs)

            page_number = query_data["page"]
            offset = (page_number - 1) * page_size
            logs_page = list(logs_qs[offset : offset + page_size + 1])
            more_rows_exist = len(logs_page) > page_size
            if more_rows_exist:
                logs_page = logs_page[:page_size]
            page_limit_reached = (
                more_rows_exist and page_number == EVAL_TASK_USAGE_MAX_PAGE
            )
            has_more = more_rows_exist and not page_limit_reached

            if not include_summary and page_number > 1 and not logs_page:
                return self._gm.bad_request("Evaluation usage page is out of range.")

            span_context_by_log = _bounded_usage_span_context(
                logs_page, project_id=eval_task.project_id
            )
            log_items = []
            for log in logs_page:
                # Derive a Pass/Fail label and a normalized 0-1 score from
                # the typed output columns. EvalLogger splits output across
                # output_bool / output_float / output_str depending on the
                # eval template's output type — see the model definition.
                if log.status == EvalEntryStatus.ERRORED:
                    result_label = "Error"
                    score = None
                    status = "error"
                elif log.status != EvalEntryStatus.COMPLETED:
                    # The queryset is terminal-only. Keep this defense in depth
                    # so a future caller cannot render in-flight/skipped work as
                    # a successful evaluation.
                    continue
                elif log.output_bool is True:
                    result_label = "Passed"
                    score = 1.0
                    status = "success"
                elif log.output_bool is False:
                    result_label = "Failed"
                    score = 0.0
                    status = "success"
                elif log.output_float is not None:
                    score = float(log.output_float)
                    result_label = "Passed" if score >= 0.5 else "Failed"
                    status = "success"
                elif log.usage_output_str_length:
                    result_label = log.usage_output_str[:50]
                    score = None
                    status = "success"
                else:
                    result_label = ""
                    score = None
                    status = "success"

                span_context = span_context_by_log.get(str(log.id))
                trace_session = log.trace_session
                config = log.custom_eval_config
                target_type = log.target_type

                # Build a short input summary. Span-target and trace-target
                # rows both have an observation_span (trace target = root
                # span); session-target rows fall back to the session name.
                input_str = ""
                if span_context:
                    input_value = span_context.get("input")
                    if isinstance(input_value, (dict, list)):
                        input_str = json.dumps(input_value, default=str)[:200]
                    elif input_value not in (None, ""):
                        input_str = str(input_value)[:200]
                    else:
                        input_str = (span_context.get("name") or "")[:200]
                elif trace_session:
                    input_str = (trace_session.name or "")[:200]

                reason = _bounded_usage_preview(
                    log.usage_reason,
                    log.usage_reason_length,
                )

                warnings = _parse_usage_json_preview(
                    log.usage_warnings,
                    log.usage_warnings_length,
                )
                if isinstance(warnings, dict):
                    warnings = [warnings]
                elif not isinstance(warnings, list):
                    warnings = []
                results_explanation = _parse_usage_json_preview(
                    log.usage_results_explanation,
                    log.usage_results_explanation_length,
                )
                omitted_fields = list((span_context or {}).get("omitted_fields") or [])
                if log.observation_span_id and not span_context:
                    omitted_fields.append("span_context")
                for field_name, value, original_length in (
                    ("reason_tail", log.usage_reason, log.usage_reason_length),
                    (
                        "output_str_tail",
                        log.usage_output_str,
                        log.usage_output_str_length,
                    ),
                    (
                        "error_message_tail",
                        log.usage_error_message,
                        log.usage_error_message_length,
                    ),
                    ("warnings_tail", log.usage_warnings, log.usage_warnings_length),
                    (
                        "results_explanation_tail",
                        log.usage_results_explanation,
                        log.usage_results_explanation_length,
                    ),
                ):
                    if _usage_preview_was_truncated(value, original_length):
                        omitted_fields.append(field_name)

                log_items.append(
                    {
                        "id": str(log.id),
                        "input": input_str,
                        "result": result_label,
                        "score": score,
                        "reason": reason,
                        "status": status,
                        "source": "eval_task",
                        "warnings": warnings or [],
                        "created_at": (
                            log.created_at.isoformat() if log.created_at else ""
                        ),
                        # Cross-references for the side panel — let users
                        # jump back to the source span/trace/session in the
                        # observe page. Span and trace rows expose span/trace
                        # IDs (trace target = root span); session rows expose
                        # session_id with both other IDs NULL.
                        "span_id": (
                            str(log.observation_span_id)
                            if log.observation_span_id
                            else None
                        ),
                        "trace_id": (
                            str(span_context.get("trace_id"))
                            if span_context and span_context.get("trace_id")
                            else str(log.trace_id)
                            if log.trace_id
                            else None
                        ),
                        "session_id": (
                            str(trace_session.id) if trace_session else None
                        ),
                        "eval_id": str(config.id) if config else None,
                        "eval_name": config.name if config else None,
                        "model": config.model if config else None,
                        "detail": {
                            "detail_complete": not omitted_fields,
                            "omitted_fields": omitted_fields,
                            "eval_name": config.name if config else None,
                            "model": config.model if config else None,
                            "warnings": warnings or [],
                            "output_type": (
                                config.eval_template.output_type_normalized
                                if config and config.eval_template
                                else None
                            ),
                            # PR3: target_type lets the FE side panel switch
                            # labels per row (Span ID vs Session ID etc.)
                            # without having to look up the parent EvalTask.
                            "target_type": target_type,
                            "span_name": (
                                span_context.get("name") if span_context else None
                            ),
                            "span_id": (
                                str(log.observation_span_id)
                                if log.observation_span_id
                                else None
                            ),
                            "trace_id": (
                                str(span_context.get("trace_id"))
                                if span_context and span_context.get("trace_id")
                                else str(log.trace_id)
                                if log.trace_id
                                else None
                            ),
                            "session_id": (
                                str(trace_session.id) if trace_session else None
                            ),
                            "session_name": (
                                trace_session.name if trace_session else None
                            ),
                            "output_bool": log.output_bool,
                            "output_float": log.output_float,
                            "output_str": _bounded_usage_preview(
                                log.usage_output_str,
                                log.usage_output_str_length,
                            ),
                            "results_explanation": results_explanation,
                            "error_message": _bounded_usage_preview(
                                log.usage_error_message,
                                log.usage_error_message_length,
                            ),
                            "input_variables": (
                                span_context.get("input_variables", {})
                                if span_context
                                else {}
                            ),
                        },
                    }
                )

            next_url = (
                replace_query_param(
                    request.build_absolute_uri(), "page", page_number + 1
                )
                if has_more
                else None
            )
            previous_url = (
                replace_query_param(
                    request.build_absolute_uri(), "page", page_number - 1
                )
                if page_number > 1
                else None
            )
            logs_count, logs_total_pages, logs_count_is_lower_bound = (
                _usage_logs_page_metadata(
                    include_summary=include_summary,
                    runs_period=runs_period,
                    period_sampled=period_sampled,
                    page_number=page_number,
                    page_size=page_size,
                    page_row_count=len(log_items),
                    more_rows_exist=more_rows_exist,
                    page_limit_reached=page_limit_reached,
                )
            )
            logs_result = {
                "count": logs_count,
                "next": next_url,
                "previous": previous_url,
                "results": log_items,
                "total_pages": logs_total_pages,
                "current_page": page_number,
                "has_more": has_more,
                "count_is_lower_bound": logs_count_is_lower_bound,
                "page_limit_reached": page_limit_reached,
            }
            response = {
                "eval_task_id": str(eval_task_id),
                "logs": logs_result,
                # The selected window is never silently widened to all task
                # history. Empty periods remain bounded, honest empty charts.
                "period_requested": period_requested,
                "period_used": period_used,
            }
            if include_summary:
                summary_sampled = period_sampled or total_runs_is_lower_bound
                response.update(
                    stats={
                        "total_runs": total_runs,
                        "runs_period": runs_period,
                        "success_count": success_count,
                        "error_count": error_count,
                        "pass_rate": pass_rate,
                        "total_runs_is_lower_bound": total_runs_is_lower_bound,
                        "runs_period_is_lower_bound": period_sampled,
                    },
                    evals=evals_meta,
                    chart=chart_data,
                    query_complete=not summary_sampled,
                    query_status="sampled" if summary_sampled else "complete",
                    query_sampled=summary_sampled,
                    provenance="newest_eval_task_rows",
                )
                if summary_sampled:
                    response["error"] = "sample_limit"
            _ensure_eval_task_response_bounded(response)
            return self._gm.success_response(response)

        except (ReadDeadlineExceeded, DatabaseError):
            raise
        except ValidationError as exc:
            return self._gm.bad_request(exc.detail)
        except EvalTaskResponseTooLarge:
            return self._eval_task_response_too_large()
        except Exception as exc:
            logger.exception(
                "eval_task.get_usage_failed",
                error_type=type(exc).__name__,
                eval_task_id=request.query_params.get("eval_task_id"),
            )
            return self._gm.bad_request("Evaluation task usage could not be loaded")

    @validated_request(
        request_serializer=EvalTaskDeleteRequestSerializer,
        responses={
            200: EvalTaskMessageResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["post"])
    def mark_eval_tasks_deleted(self, request, *args, **kwargs):
        try:
            eval_task_ids = self.request.data.get("eval_task_ids", [])
            if not eval_task_ids:
                return self._gm.bad_request("No eval task IDs provided")

            if not isinstance(eval_task_ids, list):
                return self._gm.bad_request("eval_task_ids must be a list")

            for eid in eval_task_ids:
                try:
                    uuid_module.UUID(str(eid))
                except (ValueError, AttributeError):
                    return self._gm.bad_request(f"Invalid UUID: {eid}")

            eval_tasks = self._scope_eval_task_queryset(EvalTask.objects).filter(
                id__in=eval_task_ids,
            )
            if not eval_tasks.exists():
                return self._gm.bad_request("No eval tasks found for the provided IDs")

            running_tasks = eval_tasks.filter(status=EvalTaskStatus.RUNNING)
            if running_tasks.exists():
                return self._gm.bad_request(
                    "Cannot delete running eval tasks. Pause them first."
                )

            eval_tasks.update(
                deleted=True, deleted_at=timezone.now(), status=EvalTaskStatus.DELETED
            )

            EvalTaskLogger.objects.filter(eval_task_id__in=eval_task_ids).update(
                deleted=True, deleted_at=timezone.now()
            )
            EvalLogger.objects.filter(eval_task_id__in=eval_task_ids).update(
                deleted=True, deleted_at=timezone.now()
            )

            return self._gm.success_response(
                {"message": "Eval tasks marked as deleted successfully"}
            )

        except Exception as exc:
            logger.exception(
                "eval_task.delete_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Evaluation tasks could not be deleted")

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        query_serializer=EvalTaskIdQuerySerializer,
        responses={
            200: EvalTaskMessageResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["post"])
    def pause_eval_task(self, request, *args, **kwargs):
        try:
            eval_task_id = self.request.query_params.get("eval_task_id")
            if not eval_task_id:
                return self._gm.bad_request("Eval task ID is required")

            try:
                eval_task = self._scope_eval_task_queryset(EvalTask.objects).get(
                    id=eval_task_id,
                )
            except EvalTask.DoesNotExist:
                return self._gm.bad_request("Eval task not found")

            if eval_task.status != EvalTaskStatus.RUNNING:
                return self._gm.bad_request(
                    f"Cannot pause eval task with status '{eval_task.status}'. "
                    "Only running tasks can be paused."
                )

            eval_task.status = EvalTaskStatus.PAUSED
            eval_task.save()

            # Nudge the running workflow to stop launching new evals immediately.
            # Best-effort: the paused status above is the durable signal the
            # workflow also honours at its next batch boundary.
            signal_pause_eval_task_workflow(eval_task.id)

            return self._gm.success_response(
                {"message": "Eval task paused successfully"}
            )

        except Exception as exc:
            logger.exception(
                "eval_task.pause_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Evaluation task could not be paused")

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        query_serializer=EvalTaskIdQuerySerializer,
        responses={
            200: EvalTaskMessageResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["post"])
    def unpause_eval_task(self, request, *args, **kwargs):
        try:
            eval_task_id = self.request.query_params.get("eval_task_id")
            if not eval_task_id:
                return self._gm.bad_request("Eval task ID is required")

            try:
                eval_task = self._scope_eval_task_queryset(EvalTask.objects).get(
                    id=eval_task_id,
                )
            except EvalTask.DoesNotExist:
                return self._gm.bad_request("Eval task not found")

            if eval_task.status != EvalTaskStatus.PAUSED:
                return self._gm.bad_request(
                    f"Cannot unpause eval task with status '{eval_task.status}'. "
                    "Only paused tasks can be resumed."
                )

            eval_task.status = EvalTaskStatus.PENDING
            eval_task.save(update_fields=["status"])

            # Pause exits the workflow; resuming starts a fresh run that picks up
            # the remaining pending/running entries.
            start_eval_task_workflow_sync(eval_task, replace_existing=True)

            return self._gm.success_response(
                {"message": "Eval task unpaused successfully"}
            )

        except Exception as exc:
            logger.exception(
                "eval_task.unpause_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Evaluation task could not be resumed")

    @action(detail=False, methods=["get"], pagination_class=None)
    @validated_request(
        query_serializer=_BoundedEvalTaskListWithProjectNameQuerySerializer,
        responses={
            200: _EvalTaskListResponseSerializer,
            400: ApiErrorResponseSerializer,
            422: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
    )
    @_bounded_eval_task_read
    def list_eval_tasks_with_project_name(self, request, *args, **kwargs):
        """
        List Eval Tasks filtered
        """
        try:
            query_data = request.validated_query_data
            _validate_eval_task_page_depth(
                query_data.get("page_number", 0),
                query_data.get(
                    "page_size", _EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE
                ),
            )

            queryset = self.get_queryset()
            orm_page = self._eval_task_orm_page(
                queryset, query_data, include_project_name=True
            )
            if orm_page is not None:
                result, total_rows = orm_page
            else:
                # Preserve FilterEngine's arbitrary result-dict semantics when
                # a requested field/type/operator cannot be compiled to ORM.
                compatibility_rows = self._attach_bounded_compatibility_evals(
                    _bounded_eval_task_compatibility_rows(queryset)
                )
                result = _serialize_eval_task_list_page(
                    [task for task in compatibility_rows if task._list_evals],
                    include_project_name=True,
                )

                filters = query_data.get("filters", [])
                if filters:
                    result = FilterEngine(result).apply_filters(filters)

                sort_params = query_data.get("sort_params", [])
                if sort_params:
                    for sort_param in reversed(sort_params):
                        sort_key = sort_param.get("column_id")
                        reverse = sort_param.get("direction", "asc") == "desc"

                        def sort_key_func(x):
                            value = x.get(sort_key)  # noqa: B023
                            return (value is None, value)

                        result.sort(key=sort_key_func, reverse=reverse)

                total_rows = len(result)
                page_number = query_data.get("page_number", 0)
                page_size = query_data.get(
                    "page_size", _EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE
                )
                start = int(page_number) * int(page_size)
                result = result[start : start + int(page_size)]

            # Update config to include project name
            config = get_default_eval_task_config(is_project_name_visible=True)

            response = {
                "metadata": {
                    "total_rows": total_rows,
                },
                "table": result,
                "config": config,
            }

            _ensure_eval_task_response_bounded(response)

            return self._gm.success_response(response)

        except (ReadDeadlineExceeded, DatabaseError):
            raise
        except EvalTaskCompatibilityScopeTooBroad:
            return self._gm.custom_error_response(
                drf_status.HTTP_422_UNPROCESSABLE_ENTITY,
                (
                    "This filter or sort is too broad to evaluate exactly. "
                    "Select a project or enter a more specific name and retry."
                ),
                code="eval_task_filter_scope_too_large",
            )
        except EvalTaskPageDepthExceeded:
            return self._eval_task_page_depth_response()
        except EvalTaskResponseTooLarge:
            return self._eval_task_response_too_large()
        except Exception as exc:
            logger.exception(
                "eval_task.list_with_project_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Evaluation tasks could not be loaded")

    @validated_request(
        request_serializer=EvalTaskUpdateRequestSerializer,
        responses={
            200: EvalTaskUpdateResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["patch"])
    def update_eval_task(self, request, *args, **kwargs):
        """
        Update an evaluation task with either fresh run or edit & re-run logic.

        Fresh Run: Deletes all previous results and starts completely fresh
        Edit & Re-run: Preserves existing results and only runs missing evaluations
        """
        try:
            eval_task_id = self.request.data.get("eval_task_id")
            if not eval_task_id:
                return self._gm.bad_request("Eval task ID is required")

            # Validate input data
            serializer = EditEvalTaskSerializer(data=self.request.data)
            if not serializer.is_valid():
                logger.error(
                    f"Invalid data for eval task update {eval_task_id}: {serializer.errors}"
                )
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
            if "filters" in validated_data:
                validated_data["filters"] = bind_request_my_annotations_principal(
                    request,
                    validated_data["filters"],
                )
            edit_type = validated_data["edit_type"]

            # Get eval task with row-level locking to prevent concurrent modifications
            with transaction.atomic():
                try:
                    # Lock only the EvalTask row. Workspace scoping joins through
                    # nullable Project.workspace for legacy rows, and PostgreSQL
                    # rejects FOR UPDATE on the nullable side of that outer join.
                    eval_task = (
                        self._scope_eval_task_queryset(
                            EvalTask.no_workspace_objects.select_for_update(
                                of=("self",)
                            )
                        )
                        .prefetch_related("evals")
                        .get(id=eval_task_id)
                    )
                except EvalTask.DoesNotExist:
                    return self._gm.bad_request("Eval task not found")

                # Validate task state
                if eval_task.status == EvalTaskStatus.RUNNING:
                    return self._gm.bad_request(
                        "Cannot update a running evaluation task. Please pause it first."
                    )

                if eval_task.status == EvalTaskStatus.DELETED:
                    return self._gm.bad_request(
                        "Cannot update a deleted evaluation task."
                    )

                original_evals = set(eval_task.evals.values_list("id", flat=True))
                original_run_type = eval_task.run_type
                update_fields = self._extract_update_fields(validated_data)

                # Validate the requested evals belong to the task's project.
                requested_evals = validated_data.get("evals")
                if requested_evals is not None:
                    invalid_eval_ids = self._invalid_eval_ids_for_project(
                        requested_evals, eval_task.project_id
                    )
                    if invalid_eval_ids:
                        return self._gm.bad_request(
                            "Eval configs not found for task project: "
                            + ", ".join(invalid_eval_ids)
                        )

                new_evals = (
                    set(requested_evals)
                    if requested_evals is not None
                    else original_evals
                )
                evals_changed = (
                    requested_evals is not None and new_evals != original_evals
                )
                rows_changed = any(
                    field in update_fields
                    and update_fields[field] != getattr(eval_task, field)
                    for field in ("filters", "sampling_rate", "spans_limit")
                )
                new_run_type = update_fields.get("run_type")

                # Enforce which rerun action is allowed for what changed.
                action_error = validate_edit_action(
                    edit_type,
                    original_run_type=original_run_type,
                    new_run_type=new_run_type,
                    evals_changed=evals_changed,
                    rows_changed=rows_changed,
                )
                if action_error:
                    return self._gm.bad_request(action_error)

                # Switching continuous -> historical needs a row limit (continuous
                # never had one).
                if (
                    new_run_type == RunType.HISTORICAL
                    and original_run_type == RunType.CONTINUOUS
                    and not update_fields.get("spans_limit")
                    and not eval_task.spans_limit
                ):
                    return self._gm.bad_request(
                        "Switching to a historical task requires a row limit."
                    )

                # Write the desired config (evals are an m2m the serializer sets).
                update_fields["status"] = EvalTaskStatus.PENDING
                update_fields["last_run"] = timezone.now()
                task_serializer = self.get_serializer(
                    eval_task, data=update_fields, partial=True
                )
                task_serializer.is_valid(raise_exception=True)
                eval_task = task_serializer.save()

                effective_run_type = update_fields.get("run_type", original_run_type)
                if effective_run_type == RunType.CONTINUOUS and (
                    rows_changed
                    or evals_changed
                    or original_run_type != effective_run_type
                    or edit_type == "fresh_run"
                ):
                    # A selection/eval-set edit invalidates an arrival delta:
                    # rows older than the overlap may newly match (or stop
                    # matching). Reset inside this locked transaction so the
                    # next reconcile performs a complete start-time proof.
                    EvalTask.objects.filter(id=eval_task.id).update(
                        continuous_cursor=None
                    )
                    eval_task.continuous_cursor = None

                # Delete & rerun wipes live entries first; the workflow then
                # reconciles (materialize/diff) and drains for both cases, so the
                # request returns without doing that work synchronously.
                if edit_type == "fresh_run":
                    soft_delete_live(eval_task)
                # Temporal must not see the rerun until the PENDING state and
                # all config/result changes are committed.  A fast worker can
                # otherwise attempt its guarded PENDING -> RUNNING transition
                # against the old row and leave an active workflow stranded
                # behind a PENDING task.  Keep callback failures non-robust so
                # the existing API error contract still surfaces dispatch
                # failures; the committed PENDING row is safe to retry.
                transaction.on_commit(
                    lambda: start_eval_task_workflow_sync(
                        eval_task, replace_existing=True
                    )
                )

                return self._gm.success_response(
                    {
                        "message": (
                            f"Evaluation task '{eval_task.name}' has been "
                            "updated successfully."
                        ),
                        "edit_type": edit_type,
                        "task_id": str(eval_task_id),
                    }
                )

        except ValidationError as exc:
            return self._gm.bad_request(exc.detail)
        except FilterPrincipalContextError as exc:
            return self._gm.bad_request(str(exc))
        except Exception as exc:
            logger.exception(
                "eval_task.update_failed",
                eval_task_id=eval_task_id,
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Evaluation task could not be updated")

    def _extract_update_fields(self, validated_data):
        """Extract valid update fields from validated data.

        ``row_type`` is intentionally absent from the allow-list — it's
        immutable after task creation (the serializer rejects it earlier,
        this is a belt-and-braces guard so any future code path that
        bypasses the serializer still can't write it through).
        """
        update_fields = {}
        allowed_fields = [
            "name",
            "filters",
            "sampling_rate",
            "spans_limit",
            "evals",
            "run_type",
        ]

        for field in allowed_fields:
            value = validated_data.get(field)
            if value is not None:
                update_fields[field] = value

        return update_fields

    @validated_request(
        responses={
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
            422: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        }
    )
    @action(detail=False, methods=["get"])
    @_bounded_eval_task_read
    def get_eval_details(self, request, *args, **kwargs):
        try:
            eval_id = self.request.query_params.get("eval_id")
            if not eval_id:
                return self._gm.bad_request("eval_id is required")

            queryset = (
                self._scope_eval_task_queryset(EvalTask.objects)
                .select_related("project")
                .get(id=eval_id)
            )

            eval_configs = list(
                self._scope_custom_eval_config_queryset(
                    CustomEvalConfig.objects.select_related("eval_template")
                )
                .filter(eval_tasks=queryset)
                .order_by("-created_at", "id")[
                    : _EVAL_TASK_LIST_COMPATIBILITY_RELATION_LIMIT + 1
                ]
            )
            if len(eval_configs) > _EVAL_TASK_LIST_COMPATIBILITY_RELATION_LIMIT:
                return self._eval_task_response_too_large()

            # Build rich eval objects so the frontend can render eval cards
            # with name, mapping, model, template info — not just bare UUIDs.
            evals_rich = []
            for eval_config in eval_configs:
                template = eval_config.eval_template
                evals_rich.append(
                    {
                        "id": str(eval_config.id),
                        "name": eval_config.name,
                        "template_id": str(template.id) if template else None,
                        "templateId": str(template.id) if template else None,
                        "mapping": eval_config.mapping or {},
                        "model": eval_config.model,
                        "config": eval_config.config or {},
                        "error_localizer": eval_config.error_localizer,
                        "evalType": template.eval_type if template else None,
                        "templateType": (
                            template.template_type if template else "single"
                        ),
                        "outputType": (
                            template.output_type_normalized if template else None
                        ),
                    }
                )

            result = {
                "id": str(queryset.id),
                "name": queryset.name,
                "project_id": queryset.project.id,
                "project_name": queryset.project.name,
                "status": queryset.status,
                "filters_applied": queryset.filters,
                "created_at": queryset.created_at,
                "evals_applied": evals_rich,
                "spans_limit": queryset.spans_limit,
                "sampling_rate": queryset.sampling_rate,
                "last_run": queryset.last_run,
                "run_type": queryset.run_type,
                "row_type": queryset.row_type,
            }

            _ensure_eval_task_response_bounded(result)
            return self._gm.success_response(result)

        except (ReadDeadlineExceeded, DatabaseError):
            raise
        except EvalTask.DoesNotExist:
            return self._gm.not_found("Eval task not found")
        except EvalTaskResponseTooLarge:
            return self._eval_task_response_too_large()
        except Exception as exc:
            logger.exception(
                "eval_task.details_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Evaluation task details could not be loaded")
