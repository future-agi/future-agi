import concurrent.futures
import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from typing import Any

import structlog
from django.conf import settings
from django.core.cache import cache as django_cache
from django.db import close_old_connections
from django.db.models import (
    Avg,
    Case,
    Count,
    Exists,
    F,
    FloatField,
    IntegerField,
    JSONField,
    OuterRef,
    Q,
    Subquery,
    When,
)
from django.db.models.functions import JSONObject, Round
from django.utils import timezone
from drf_yasg import openapi
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager
from analytics.utils import (
    MixpanelEvents,
    MixpanelTypes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.models.choices import (
    AnnotationTypeChoices,
    DataTypeChoices,
    FeedbackSourceChoices,
)
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.evals_metric import Feedback
from model_hub.models.run_prompt import PromptVersion
from model_hub.models.score import Score
from model_hub.views.scores import (
    _auto_complete_queue_items,
    _auto_create_queue_items_for_default_queues,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace
from tracer.selectors.trace_filter_reads import (
    CURSOR_REQUIRED_CODE,
    CURSOR_REQUIRED_MESSAGE,
    PAGE_DEPTH_EXCEEDED_CODE,
    PAGE_DEPTH_EXCEEDED_MESSAGE,
    bounded_numbered_page_depth_exceeded,
    numbered_page_depth_exceeded,
)
from tracer.serializers.filters import (
    ObserveGraphDataQuerySerializer,
    ObserveGraphDataRequestSerializer,
    ObserveGraphDataResponseSerializer,
    PageDepthExceededErrorSerializer,
)
from tracer.serializers.observation_span import (
    ObservationAttributeListQuerySerializer,
    ObservationAttributeListResponseSerializer,
    ObservationSpanSerializer,
    RootSpansQuerySerializer,
    RootSpansResponseSerializer,
    SpanExportQuerySerializer,
    SpanIndexQuerySerializer,
    SpanListQuerySerializer,
    SpanObserveIndexQuerySerializer,
    SpanObserveListQuerySerializer,
    SpanObserveListResponseSerializer,
    SpanPrototypeListResponseSerializer,
    SubmitFeedbackActionTypeSerializer,
    SubmitFeedbackSerializer,
)
from tracer.serializers.trace import TraceSerializer
from tracer.services.clickhouse.attribute_reads import (
    ATTRIBUTE_READ_EXPLICIT_SEGMENT,
    AttributeReadMetadata,
    AttributeReadSelector,
    IncompleteLatestStateReplay,
    InvalidAttributeKey,
    InvalidAttributeSearch,
    merge_read_metadata,
)
from tracer.services.clickhouse.bounded_graph_reads import BoundedGraphReadError
from tracer.services.clickhouse.graph_action_deadline import (
    GraphActionUnavailable,
    bounded_graph_action_request,
    finish_graph_action_response,
    graph_action_postgres_budget,
    graph_action_remaining_ms,
    start_graph_action_deadline,
)
from tracer.services.clickhouse.graph_dispatch import (
    enforce_exact_graph_data_contract,
    fetch_annotation_graph_ch,
    fetch_eval_graph_ch,
    fetch_system_metric_graph_ch,
    graph_payload_is_publishable,
)
from tracer.services.clickhouse.list_cursor import (
    ListCursorError,
    cursor_page_metadata,
    cursor_scope_for_request,
    decode_list_cursor,
    encode_list_cursor,
    exact_total_explicitly_required,
    frozen_window_filter,
    snapshot_cursor_supported,
)
from tracer.services.clickhouse.list_request_deadline import bounded_list_request
from tracer.services.clickhouse.page_dedup import paginate_deduped
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
    is_clickhouse_api_read_unavailable_error,
    is_clickhouse_query_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
from tracer.services.clickhouse.v2.span_selectors import (
    bound_observe_list_value,
    flatten_span_attributes_into_entry,
    merge_content_rows,
)
from tracer.services.filter_attestation import (
    applied_filter_attestation,
    graph_execution_filters,
    graph_query_evidence,
)
from tracer.services.filter_principal_context import (
    FilterPrincipalContextError,
    bind_request_my_annotations_principal,
)
from tracer.utils.annotations import build_annotation_subqueries
from tracer.utils.bounded_csv import (
    BOUNDED_SPAN_EXPORT_PAGE_SIZE,
    bounded_page_csv_response,
)
from tracer.utils.create_otel_span import create_single_otel_span
from tracer.utils.eval import (
    evaluate_observation_span,
    evaluate_observation_span_observe,
)
from tracer.utils.filters import FilterEngine
from tracer.utils.helper import (
    get_annotation_labels_by_project,
    get_annotation_labels_for_project,
    get_default_span_config,
    update_column_config_based_on_eval_config,
    update_span_column_config_based_on_annotations,
)
from tracer.utils.otel import (
    ResourceLimitError,
    calculate_cost_from_tokens,
)
from tracer.utils.property_registry import validate_property_graph_namespace
from tracer.utils.sql_queries import SQL_query_handler

logger = structlog.get_logger(__name__)


SPAN_LIST_WALL_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
SPAN_LIST_CANDIDATE_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
SPAN_LIST_ENRICHMENT_TIMEOUT_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
SPAN_LIST_READ_SETTINGS = {
    "max_threads": 1,
    "max_block_size": settings.OBSERVABILITY_LIST_MAX_BLOCK_SIZE,
    "max_memory_usage": settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES,
    "max_bytes_to_read": settings.OBSERVABILITY_LIST_MAX_BYTES,
    "max_result_rows": settings.OBSERVABILITY_LIST_MAX_RESULT_ROWS,
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}


def _span_filtered_page_depth_exceeded(
    filters: list[dict], page_number: int, page_size: int
) -> bool:
    """Preflight finite numbered-page work without reading ClickHouse."""

    if numbered_page_depth_exceeded(
        page_number=page_number,
        page_size=page_size,
    ):
        return True

    has_non_time_filter = any(
        isinstance(item, dict)
        and (item.get("column_id") or item.get("columnId"))
        not in {"created_at", "start_time"}
        for item in filters
    )
    return has_non_time_filter and bounded_numbered_page_depth_exceeded(
        page_number=page_number,
        page_size=page_size,
        classify_batch_size=settings.OBSERVABILITY_NAVIGATION_SCAN_PAGE_SIZE,
        seed_batch_size=settings.OBSERVABILITY_NAVIGATION_SCAN_PAGE_SIZE,
    )


SPAN_NAVIGATION_CANDIDATE_LIMIT = settings.OBSERVABILITY_NAVIGATION_CANDIDATE_LIMIT
SPAN_NAVIGATION_SCAN_PAGE_SIZE = settings.OBSERVABILITY_NAVIGATION_SCAN_PAGE_SIZE
SPAN_NAVIGATION_MAX_QUERIES = settings.OBSERVABILITY_NAVIGATION_MAX_QUERIES
SPAN_NAVIGATION_WALL_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS


class SpanNavigationReadUnavailable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _span_page_identity_sets(
    rows: list[dict],
    *,
    default_project_id: str | None = None,
) -> tuple[
    list[tuple[str, str, str, object]],
    list[tuple[str, str]],
    dict[tuple[str, str], tuple[str, str, str]],
]:
    """Build physical and external span keys without an identity downgrade."""

    physical: list[tuple[str, str, str, object]] = []
    external: list[tuple[str, str]] = []
    app_identity_by_external: dict[tuple[str, str], tuple[str, str, str]] = {}
    for row in rows:
        project_id = str(row.get("project_id") or default_project_id or "")
        trace_id = str(row.get("trace_id") or "")
        span_id = str(row.get("id") or "")
        start_time = row.get("start_time")
        if not project_id or not trace_id or not span_id:
            continue
        if not row.get("project_id"):
            row["project_id"] = project_id
        external_key = (trace_id, span_id)
        app_key = (project_id, trace_id, span_id)
        previous = app_identity_by_external.setdefault(external_key, app_key)
        if previous != app_key:
            # Eval/score tables cannot reliably carry project identity. A page
            # containing this collision cannot be decorated safely.
            raise ValueError("ambiguous trace-scoped span identity")
        external.append(external_key)
        if start_time is not None:
            physical.append((project_id, trace_id, span_id, start_time))
    return (
        list(dict.fromkeys(physical)),
        list(dict.fromkeys(external)),
        app_identity_by_external,
    )


def _span_cursor_order_for_partial_page(
    *, rows: list[dict], bounded_page: Any, cursor_state: Any
) -> tuple[Any, ...]:
    """Return the exact row boundary or a progressed empty scan boundary."""

    if rows:
        row = rows[-1]
        return (
            row.get("start_time"),
            str(row.get("id", "")),
            str(row.get("trace_id", "")),
            str(row.get("project_id", "")),
        )
    if cursor_state is not None:
        return tuple(cursor_state.order)
    checkpoint_time = (
        bounded_page.continuation_before_start_time
        or bounded_page.continuation_slice_end
    )
    if checkpoint_time is None:
        raise ValueError("partial span page has no continuation checkpoint")
    token = bounded_page.continuation_before_id
    if isinstance(token, tuple) and len(token) == 3:
        return checkpoint_time, *(str(value) for value in token)
    return checkpoint_time, "\U0010ffff", "\U0010ffff", "\U0010ffff"


class AddObservationSpanAnnotationsSerializer(serializers.Serializer):
    observation_span_id = serializers.CharField(required=False, allow_blank=True)
    trace_id = serializers.UUIDField(required=False)
    annotation_values = serializers.DictField(child=serializers.JSONField())
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("observation_span_id") and not attrs.get("trace_id"):
            raise serializers.ValidationError(
                "observation_span_id or trace_id is required."
            )
        return attrs


def _validate_add_annotation_value(
    validate_fn, annotation_type, label_settings, given_value
):
    """Map the raw add_annotations value to typed fields and validate.

    Returns an error message string, or None if valid.
    """
    from model_hub.models.choices import AnnotationTypeChoices

    value = value_float = value_bool = value_str_list = None
    if annotation_type == AnnotationTypeChoices.TEXT.value:
        value = str(given_value) if given_value is not None else None
    elif annotation_type in [
        AnnotationTypeChoices.NUMERIC.value,
        AnnotationTypeChoices.STAR.value,
    ]:
        try:
            value_float = float(given_value)
        except (TypeError, ValueError):
            return f"Expected a numeric value, got: {given_value}"
    elif annotation_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
        if isinstance(given_value, bool):
            value_bool = given_value
        elif isinstance(given_value, str):
            value_bool = given_value.lower() in ("up", "true", "1")
        else:
            return f"Expected a boolean value, got: {given_value}"
    elif annotation_type == AnnotationTypeChoices.CATEGORICAL.value:
        if isinstance(given_value, list):
            value_str_list = given_value
        elif isinstance(given_value, str):
            value_str_list = [v.strip() for v in given_value.split(",")]
        else:
            return f"Expected a list or string, got: {type(given_value).__name__}"
    else:
        value = str(given_value) if given_value is not None else None

    return validate_fn(
        label_type=annotation_type,
        label_settings=label_settings,
        value=value,
        value_float=value_float,
        value_bool=value_bool,
        value_str_list=value_str_list,
    )


def _to_score_value(annotation_type, given_value):
    """Convert AnnotateDrawer value format → Score.value JSON format."""
    if annotation_type in [
        AnnotationTypeChoices.STAR.value,
    ]:
        return {"rating": float(given_value)}
    elif annotation_type == AnnotationTypeChoices.NUMERIC.value:
        return {"value": float(given_value)}
    elif annotation_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
        return {"value": str(given_value)}
    elif annotation_type == AnnotationTypeChoices.CATEGORICAL.value:
        return {
            "selected": given_value if isinstance(given_value, list) else [given_value]
        }
    else:
        # text and fallback
        return {"text": str(given_value)}


def _get_configured_output_type(custom_eval_config):
    """Get the configured output type from an eval's template config.

    Returns the output type string ("Pass/Fail", "score", "choices") or None
    if unavailable.
    """
    if (
        custom_eval_config
        and getattr(custom_eval_config, "eval_template", None)
        and custom_eval_config.eval_template
    ):
        eval_template_config = custom_eval_config.eval_template.config or {}
        return eval_template_config.get("output")
    return None


def _build_eval_metric_entry(
    output_float, output_bool, output_str_list, configured_output_type
):
    """Determine score and outputType based on eval template config.

    For Pass/Fail evals, prioritises output_bool over output_float so that
    stale float values (left behind by re-runs) don't mask the boolean result.

    Returns (score, output_type_str) or (None, None) when no score data exists.
    """
    # str_list can come from CH as a JSON string '[]' or from PG as a Python list
    parsed_str_list = None
    if output_str_list:
        if isinstance(output_str_list, list):
            parsed_str_list = output_str_list
        elif isinstance(output_str_list, str) and output_str_list.startswith("["):
            try:
                parsed_str_list = json.loads(output_str_list)
            except json.JSONDecodeError:
                pass

    # str_list always wins (choices type) - but only if it has data
    if parsed_str_list and len(parsed_str_list) > 0:
        return parsed_str_list, "str_list"

    # Config says Pass/Fail → prefer output_bool
    if configured_output_type == "Pass/Fail" and output_bool is not None:
        return (100.0 if output_bool else 0.0), "bool"

    # Float score (default path, or fallback for Pass/Fail when output_bool is absent)
    if output_float is not None:
        score = round(output_float * 100, 2)
        # If config says Pass/Fail but only float is stored (e.g. DeterministicEvaluator),
        # preserve the configured output type so the frontend renders Pass/Fail correctly.
        if configured_output_type == "Pass/Fail":
            return score, "Pass/Fail"
        return score, configured_output_type or "float"

    # Bool without Pass/Fail config
    if output_bool is not None:
        return (100.0 if output_bool else 0.0), "bool"

    return None, None


def _get_request_organization(request):
    return getattr(request, "organization", None) or request.user.organization


def _project_workspace_scope_q(request, project_prefix="project__"):
    organization = _get_request_organization(request)
    organization_field = f"{project_prefix}organization"
    scope = Q(**{organization_field: organization})

    workspace = getattr(request, "workspace", None)
    if not workspace:
        # API-key and other non-workspace requests still belong to exactly one
        # organization.  Returning an empty Q here turns any caller that relies
        # on this helper into an unscoped cross-tenant lookup.
        return scope

    workspace_field = f"{project_prefix}workspace"
    organization_id = getattr(workspace, "organization_id", None) or getattr(
        organization, "id", None
    )

    if getattr(workspace, "is_default", False):
        scope &= (
            Q(**{workspace_field: workspace})
            | Q(
                **{
                    f"{workspace_field}__is_default": True,
                    f"{workspace_field}__organization_id": organization_id,
                }
            )
            | Q(
                **{
                    f"{workspace_field}__isnull": True,
                    f"{project_prefix}organization_id": organization_id,
                }
            )
        )
        return scope

    return scope & Q(**{workspace_field: workspace})


def allowed_root_spans_for_request(
    trace_ids: list[str],
    *,
    organization,
    project_scope_q,
    project_ids: list[str] | None = None,
) -> dict[str, str]:
    """Resolve ``{trace_id: root_span_id}`` for *trace_ids*, returning only traces
    whose owning project is org/workspace-accessible. Collector traces have no PG
    ``Trace`` row, so the project_id is learned from CH and re-checked against the
    PG ``Project`` authority. FAIL CLOSED: an untenanted / cross-org trace is dropped
    (no key) — same response shape as before.

    ``project_ids`` (optional) only prunes the CH scan; the PG re-check stays the
    tenant boundary, so it can narrow results but never widen them. Pass a
    superset of the traces' owning projects, else a valid root is silently dropped.
    """
    if not trace_ids:
        return {}

    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        roots = reader.root_ids_by_trace_ids(
            [str(tid) for tid in trace_ids], project_ids=project_ids
        )

    # Candidate project_ids from the lean root projection, to verify against PG.
    candidate_project_ids = {pid for _, pid in roots.values() if pid}
    if not candidate_project_ids:
        return {}

    allowed_project_ids = {
        str(pid)
        for pid in Project.objects.filter(
            project_scope_q,
            id__in=candidate_project_ids,
            organization=organization,
        ).values_list("id", flat=True)
    }
    if not allowed_project_ids:
        return {}

    return {
        tid: span_id
        for tid, (span_id, pid) in roots.items()
        if pid is not None and pid in allowed_project_ids
    }


class ObservationSpanView(BaseModelViewSetMixin, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = ObservationSpanSerializer

    def get_queryset(self):
        observation_span_id = self.kwargs.get("pk")
        # Get base queryset with automatic filtering from mixin
        query_Set = (
            super()
            .get_queryset()
            .filter(project__organization=_get_request_organization(self.request))
        )

        if observation_span_id:
            return query_Set.filter(id=observation_span_id)

        project_id = self.request.query_params.get("project_id")
        project_version_id = self.request.query_params.get("project_version_id")
        trace_id = self.request.query_params.get("trace_id")
        page_number = self.request.query_params.get("page_number", 0)
        page_size = self.request.query_params.get("page_size", 30)

        if project_id:
            query_Set = query_Set.filter(project_id=project_id)

        if project_version_id:
            query_Set = query_Set.filter(project_version_id=project_version_id)

        if trace_id:
            query_Set = query_Set.filter(trace_id=trace_id)

        start = int(page_number) * int(page_size)
        end = start + int(page_size)

        return query_Set[start:end]

    @staticmethod
    def _to_iso(value):
        if not value:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    def _span_queryset_postgres(self, request, project_id, project_version_id=None):
        qs = ObservationSpan.no_workspace_objects.filter(
            _project_workspace_scope_q(request),
            project_id=project_id,
            project__organization=_get_request_organization(request),
        )
        if project_version_id:
            qs = qs.filter(project_version_id=project_version_id)
        return qs.select_related("trace", "end_user").order_by(
            "-start_time", "-created_at"
        )

    def _span_row_from_postgres(self, span):
        end_user = getattr(span, "end_user", None)
        return {
            "span_id": span.id,
            "input": span.input,
            "output": span.output,
            "trace_id": str(span.trace_id),
            "created_at": self._to_iso(span.created_at),
            "node_type": span.observation_type,
            "span_name": span.name,
            "user_id": getattr(end_user, "user_id", None) if end_user else None,
            "user_id_type": (
                getattr(end_user, "user_id_type", None) if end_user else None
            ),
            "user_id_hash": (
                getattr(end_user, "user_id_hash", None) if end_user else None
            ),
            "start_time": self._to_iso(span.start_time),
            "status": span.status,
            "latency_ms": span.latency_ms,
            "total_tokens": span.total_tokens,
            "prompt_tokens": span.prompt_tokens,
            "completion_tokens": span.completion_tokens,
            "model": span.model,
            "provider": span.provider,
            "cost": round(span.cost, 6) if span.cost else 0,
        }

    def _list_spans_postgres(
        self, request, project_id, validated_data, project_version_id=None
    ):
        qs = self._span_queryset_postgres(
            request, project_id, project_version_id=project_version_id
        )
        total_count = qs.count()
        page_number = validated_data.get("page_number", 0)
        page_size = validated_data.get("page_size", 30)
        start = page_number * page_size
        rows = [
            self._span_row_from_postgres(span) for span in qs[start : start + page_size]
        ]
        column_config = get_default_span_config(include_user_fields=True)
        return self._gm.success_response(
            {
                "metadata": {"total_rows": total_count},
                "table": rows,
                "config": column_config,
                "column_config": column_config,
            }
        )

    @staticmethod
    def _metric_field(metric_id):
        return {
            "latency": "latency_ms",
            "avg_latency": "latency_ms",
            "latency_ms": "latency_ms",
            "tokens": "total_tokens",
            "total_tokens": "total_tokens",
            "prompt_tokens": "prompt_tokens",
            "completion_tokens": "completion_tokens",
            "cost": "cost",
        }.get(metric_id, metric_id)

    def _system_metric_graph_postgres(
        self, request, project_id, filters, interval, metric_id
    ):
        field_name = self._metric_field(metric_id)
        rows = []
        for span in self._span_queryset_postgres(request, project_id):
            value = getattr(span, field_name, None)
            if value is None:
                continue
            rows.append(
                {
                    "timestamp": self._to_iso(span.start_time or span.created_at),
                    "value": float(value),
                }
            )
        return {"metric_name": metric_id, "data": rows}

    @validated_request(
        responses={
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        }
    )
    def retrieve(self, request, *args, **kwargs):
        from tracer.services.clickhouse.v2.trace_detail_reads import (
            TraceDetailNotFound,
            TraceDetailReadUnavailable,
        )

        try:
            observation_span_id = kwargs.get("pk")
            organization = _get_request_organization(request)
            project_manager = getattr(Project, "no_workspace_objects", Project.objects)
            authorized_project_ids = [
                str(project_id)
                for project_id in project_manager.filter(
                    _project_workspace_scope_q(request, project_prefix=""),
                    organization=organization,
                    deleted=False,
                ).values_list("id", flat=True)[:4097]
            ]
            if len(authorized_project_ids) > 4096:
                raise TraceDetailReadUnavailable("project_scope_too_large")

            # Direct-write CH25 is the sole span source.  The project dimension
            # above is the tenant authority; no PG ObservationSpan existence
            # check or span-attribute fallback is valid after cutover.
            analytics = V2AnalyticsQueryService()
            return self._retrieve_clickhouse(
                request,
                observation_span_id,
                analytics,
                authorized_project_ids=authorized_project_ids,
            )
        except TraceDetailNotFound:
            return self._gm.bad_request(get_error_message("OBSERVATION_SPAN_NOT_FOUND"))
        except TraceDetailReadUnavailable as exc:
            logger.warning(
                "span_detail_bounded_read_incomplete",
                span_id=str(kwargs.get("pk") or ""),
                error_code=exc.code,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Span details are temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as exc:
            logger.exception(
                "span_detail_request_failed",
                span_id=str(kwargs.get("pk") or ""),
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Span details could not be loaded")

    def _retrieve_clickhouse(
        self,
        request,
        observation_span_id,
        analytics,
        *,
        authorized_project_ids,
    ):
        """Retrieve span detail from ClickHouse with eval metrics."""
        from tracer.constants.provider_logos import PROVIDER_LOGOS
        from tracer.services.clickhouse.v2.trace_detail_reads import read_span_detail

        config_by_id = {}

        def _resolve_eval_config_ids(project_id):
            configs = list(
                CustomEvalConfig.no_workspace_objects.filter(
                    project_id=project_id,
                    project__organization=_get_request_organization(request),
                    deleted=False,
                ).select_related("eval_template")[:4097]
            )
            if len(configs) > 4096:
                from tracer.services.clickhouse.v2.trace_detail_reads import (
                    TraceDetailReadUnavailable,
                )

                raise TraceDetailReadUnavailable("eval_config_scope_too_large")
            config_by_id.update({str(config.id): config for config in configs})
            return tuple(config_by_id)

        detail_read = read_span_detail(
            analytics=analytics,
            project_ids=list(authorized_project_ids),
            span_id=str(observation_span_id),
            eval_config_ids_resolver=_resolve_eval_config_ids,
            # This endpoint renders span fields and eval metrics only. Avoid an
            # unrelated score-table read that adds latency and can make an
            # otherwise valid span detail unavailable.
            include_annotations=False,
        )
        matching_rows = [
            candidate
            for candidate in detail_read.spans
            if str(candidate.get("id") or "") == str(observation_span_id)
        ]
        if len(matching_rows) != 1:
            from tracer.services.clickhouse.v2.trace_detail_reads import (
                TraceDetailReadUnavailable,
            )

            raise TraceDetailReadUnavailable("ambiguous_span_identity")
        row = matching_rows[0]
        provider = row.get("provider")

        # Parse JSON string fields from CH (stored as String columns)
        import json as _json

        def _parse_json(val, default=None):
            """Safely parse a JSON string; return default if not a string or invalid."""
            if default is None:
                default = {}
            if not val or not isinstance(val, str):
                return val if val is not None else default
            try:
                return _json.loads(val)
            except (ValueError, TypeError):
                return default

        # Build span_attributes from the raw JSON string or decomposed maps

        span_attrs_raw = row.get("span_attributes") or "{}"
        try:
            span_attrs = (
                _json.loads(span_attrs_raw)
                if isinstance(span_attrs_raw, str)
                else span_attrs_raw
            )
        except (ValueError, TypeError):
            span_attrs = {}
        if not isinstance(span_attrs, dict):
            span_attrs = {}
        # Direct writes split scalar values into typed Maps and structured
        # overflow into span_attributes. Always union all sources; overflow
        # keeps precedence when a malformed producer duplicates a key.
        for k, v in (row.get("attrs_string") or {}).items():
            span_attrs.setdefault(k, v)
        for k, v in (row.get("attrs_number") or {}).items():
            span_attrs.setdefault(k, v)
        for k, v in (row.get("attrs_bool") or {}).items():
            span_attrs.setdefault(k, bool(v))
        # Build metadata from CH JSON column
        metadata_raw = row.get("metadata_json") or "{}"
        metadata = _parse_json(metadata_raw, default={})

        observation_span = {
            "id": str(row["id"]),
            "project": str(row["project_id"]),
            "project_version": (
                str(row["project_version_id"])
                if row.get("project_version_id")
                else None
            ),
            "trace": str(row["trace_id"]),
            "parent_span_id": (
                str(row["parent_span_id"]) if row.get("parent_span_id") else None
            ),
            "name": row.get("name"),
            "observation_type": row.get("observation_type"),
            "start_time": row.get("start_time"),
            "end_time": row.get("end_time"),
            "input": _parse_json(row.get("input")),
            "output": _parse_json(row.get("output")),
            "model": row.get("model"),
            "model_parameters": _parse_json(row.get("model_parameters")),
            "latency_ms": row.get("latency_ms"),
            "org_id": None,
            "org_user_id": None,
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
            "total_tokens": row.get("total_tokens"),
            "response_time": None,
            "eval_id": None,
            "cost": (
                round(row["cost"], 6)
                if row.get("cost") and row["cost"] > 0
                else row.get("cost")
            ),
            "status": row.get("status"),
            "status_message": row.get("status_message"),
            "tags": _parse_json(row.get("tags"), default=[]),
            "metadata": metadata,
            "span_events": _parse_json(row.get("span_events"), default=[]),
            "provider": provider,
            "provider_logo": PROVIDER_LOGOS.get(provider.lower()) if provider else None,
            "span_attributes": span_attrs,
            "custom_eval_config": (
                str(row["custom_eval_config_id"])
                if row.get("custom_eval_config_id")
                and str(row["custom_eval_config_id"]) in config_by_id
                else None
            ),
            "eval_status": None,
            "prompt_version": None,
        }

        # Handle prompt version name (from PG, small config table)
        if observation_span["prompt_version"]:
            try:
                prompt_version = PromptVersion.objects.get(
                    id=observation_span["prompt_version"]
                )
                observation_span["prompt_template_id"] = str(
                    prompt_version.original_template.id
                )
                observation_span["prompt_name"] = (
                    str(prompt_version.original_template.name)
                    + " - "
                    + str(prompt_version.template_version)
                )
            except PromptVersion.DoesNotExist:
                observation_span["prompt_version"] = None

        children_span_ids = [str(candidate["id"]) for candidate in detail_read.spans]

        # Fetch eval metrics from CH
        evals_metrics = {}
        if children_span_ids:
            eval_rows = list(detail_read.evals)

            # Get config names from PG (small config table)
            config_ids = list(
                {
                    str(r.get("eval_config_id") or r.get("config_id"))
                    for r in eval_rows
                    if r.get("eval_config_id") or r.get("config_id")
                }
            )
            config_name_map = {}
            config_output_type_map = {}
            if config_ids:
                configs = [
                    config_by_id[config_id]
                    for config_id in config_ids
                    if config_id in config_by_id
                ]
                for c in configs:
                    config_name_map[str(c.id)] = c.name
                    config_output_type_map[str(c.id)] = _get_configured_output_type(c)

            # Keys with a completed score or an error — a terminal result always
            # wins over a non-terminal/skipped marker regardless of CH row order.
            terminal_keys: set[str] = set()
            # Precedence among non-terminal/skipped rows for the same key.
            _status_rank = {"pending": 1, "running": 2, "skipped": 3}

            for eval_row in eval_rows:
                config_id = str(
                    eval_row.get("eval_config_id") or eval_row.get("config_id") or ""
                )
                span_id = eval_row.get("span_id")
                config_name = config_name_map.get(
                    config_id, eval_row.get("eval_type_id", "score")
                )
                if not config_name:
                    config_name = "score"

                name_suffix = (
                    f" ( child span - {span_id} )"
                    if span_id != str(observation_span_id)
                    else ""
                )

                key = f"{config_id}**{span_id}"

                _row_status = (eval_row.get("status") or "").lower()
                if (
                    eval_row.get("error")
                    or eval_row.get("output_str") == "ERROR"
                    or _row_status == "errored"
                ):
                    evals_metrics[key] = {
                        "score": None,
                        "name": f"{config_name}{name_suffix}",
                        "explanation": eval_row.get("error_message"),
                        "error": True,
                    }
                    terminal_keys.add(key)
                    continue

                # A non-terminal lifecycle status wins over the output columns:
                # the CH mirror stores 0 for a NULL bool, so a queued/running/
                # skipped row can carry stale output that would otherwise be
                # rendered as a real score. Surface the status marker instead
                # (a completed row for the same key still overrides it below).
                status = (eval_row.get("status") or "").lower()
                if status in _status_rank:
                    if key not in terminal_keys:
                        existing = evals_metrics.get(key)
                        if not (
                            existing
                            and _status_rank.get(existing.get("status"), 0)
                            >= _status_rank[status]
                        ):
                            entry = {
                                "score": None,
                                "name": f"{config_name}{name_suffix}",
                                "explanation": eval_row.get("eval_explanation"),
                                "status": status,
                            }
                            if status == "skipped" and eval_row.get("skipped_reason"):
                                entry["skipped_reason"] = eval_row.get("skipped_reason")
                                if not entry["explanation"]:
                                    entry["explanation"] = eval_row.get(
                                        "skipped_reason"
                                    )
                            evals_metrics[key] = entry
                    continue

                configured_output_type = config_output_type_map.get(config_id)
                score, output_type = _build_eval_metric_entry(
                    eval_row.get("output_float"),
                    eval_row.get("output_bool"),
                    eval_row.get("output_str_list"),
                    configured_output_type,
                )
                if score is not None or output_type is not None:
                    evals_metrics[key] = {
                        "score": score,
                        "name": f"{config_name}{name_suffix}",
                        "explanation": eval_row.get("eval_explanation"),
                        "output_type": output_type,
                    }
                    terminal_keys.add(key)

        return self._gm.success_response(
            {"observation_span": observation_span, "evals_metrics": evals_metrics}
        )

    @action(detail=False, methods=["get"])
    def retrieve_loading(self, request, *args, **kwargs):
        # CH25-TODO: this endpoint serves "still computing" placeholders
        # for evals not yet completed. It walks project_version.eval_tags
        # (PG only) and inner-loops EvalLogger lookups by (span FK, config
        # FK), which are both PG primary keys. Leaving PG-resident until
        # EvalLogger lives in CH as well — at that point the inner loop
        # becomes a single CH eval-lookup keyed by (span_id, config_id).
        try:
            observation_span_id = request.query_params.get("observation_span_id")
            if not observation_span_id:
                return self._gm.bad_request("observation_span_id is required")

            try:
                observation_span_obj = ObservationSpan.objects.get(
                    _project_workspace_scope_q(request),
                    id=observation_span_id,
                    project__organization=_get_request_organization(request),
                )
            except ObservationSpan.DoesNotExist:
                logger.exception(
                    f"Observation span with id {observation_span_id} does not exist for this organization."
                )
                return self._gm.bad_request(
                    get_error_message("OBSERVATION_SPAN_NOT_FOUND")
                )

            serializer = self.get_serializer(observation_span_obj)
            observation_span = serializer.data

            # Get project version and eval_tags
            project_version = observation_span_obj.project_version
            if not project_version:
                return self._gm.bad_request(
                    "Project version not found for this observation span"
                )

            eval_tags = project_version.eval_tags or []

            # Fetch all children span IDs
            children_span_ids = fetch_children_span_ids(observation_span_obj)
            children_span_ids.append(observation_span["id"])

            # Prepare eval metrics dictionary
            evals_metrics = {}

            # Get all relevant observation spans
            observation_spans = ObservationSpan.objects.filter(id__in=children_span_ids)
            observation_spans = observation_spans.filter(
                _project_workspace_scope_q(request),
                project__organization=_get_request_organization(request),
            )
            eval_tags = observation_span_obj.project_version.eval_tags

            eval_config_mapping = {
                str(eval_tag["custom_eval_config_id"]): eval_tag["value"]
                for eval_tag in eval_tags
                if eval_tag["type"] == "OBSERVATION_SPAN_TYPE"
            }

            custom_eval_config_ids = {
                eval_tag["custom_eval_config_id"] for eval_tag in eval_tags
            }
            custom_eval_configs = CustomEvalConfig.objects.filter(
                id__in=custom_eval_config_ids, deleted=False
            ).select_related("eval_template")
            name_suffix = ""

            for custom_eval_config in custom_eval_configs:
                for span in observation_spans:
                    if (
                        span.observation_type
                        != eval_config_mapping.get(str(custom_eval_config.id)).lower()
                    ):
                        continue

                    eval_logger = EvalLogger.objects.filter(
                        observation_span=span, custom_eval_config=custom_eval_config
                    ).first()

                    config_name = custom_eval_config.name

                    name_suffix = (
                        f" ( child span - {span.id} )"
                        if str(span.id) != str(observation_span_id)
                        else ""
                    )

                    if not eval_logger:
                        key = f"{custom_eval_config.id}**{span.id}"
                        evals_metrics[key] = {
                            "score": None,
                            "name": f"{config_name}{name_suffix}",
                            "explanation": None,
                            "loading": True,
                        }
                        continue

                    # Handle error case
                    if eval_logger.error or eval_logger.output_str == "ERROR":
                        key = f"{custom_eval_config.id}**{span.id}"
                        evals_metrics[key] = {
                            "score": None,
                            "name": f"{config_name}{name_suffix}",
                            "explanation": eval_logger.error_message,
                            "error": True,
                        }

                    else:
                        configured_output_type = _get_configured_output_type(
                            custom_eval_config
                        )
                        score, output_type = _build_eval_metric_entry(
                            eval_logger.output_float,
                            eval_logger.output_bool,
                            eval_logger.output_str_list,
                            configured_output_type,
                        )
                        if score is not None or output_type is not None:
                            key = f"{custom_eval_config.id}**{span.id}"
                            evals_metrics[key] = {
                                "score": score,
                                "name": f"{config_name}{name_suffix}",
                                "explanation": eval_logger.eval_explanation,
                                "output_type": output_type,
                            }

            return self._gm.success_response(
                {"observation_span": observation_span, "evals_metrics": evals_metrics}
            )

        except Exception as e:
            logger.exception(f"Error in fetching observation span: {str(e)}")
            return self._gm.bad_request(
                f"Error retrieving observation span {get_error_message('FAILED_GET_OBSERVATION_SPAN')}"
            )

    @validated_request(
        query_serializer=RootSpansQuerySerializer,
        responses={200: RootSpansResponseSerializer},
    )
    @action(detail=False, methods=["get"], url_path="root-spans")
    def root_spans(self, request, *args, **kwargs):
        """
        Given a list of trace_ids, return the root span ID for each trace.
        Root span = the span where parent_span_id IS NULL for that trace.

        Query params (repeated): trace_ids (required,
        ?trace_ids=<id>&trace_ids=<id>) + optional project_ids (prunes the CH
        scan). Response: { "result": { "<trace_id>": "<span_id>", ... } }
        """
        try:
            trace_ids = request.validated_query_data["trace_ids"]
            project_ids = request.validated_query_data.get("project_ids") or None

            # Collector traces have no PG ``Trace`` row; the gate resolves the root
            # span + tenant from CH/PG-Project instead (fail closed). See selector.
            org = _get_request_organization(request)
            result = allowed_root_spans_for_request(
                trace_ids,
                organization=org,
                project_scope_q=_project_workspace_scope_q(request, project_prefix=""),
                project_ids=project_ids,
            )
            return self._gm.success_response(result)
        except Exception as e:
            # fail closed: any CH/PG error returns no data, never a partial leak
            logger.exception("Error fetching root spans", error=str(e))
            return self._gm.bad_request("Error fetching root spans")

    @action(detail=False, methods=["post"])
    def bulk_create(self, request, *args, **kwargs):
        try:
            observation_span_data = self.request.data.get("observation_spans")
            if observation_span_data is None:
                observation_span_data = self.request.data.get("spans", [])
            if not observation_span_data:
                return self._gm.bad_request("observation_spans is required")

            for observation_span in observation_span_data:
                if not observation_span.get("id"):
                    observation_span["id"] = f"span_{uuid.uuid4().hex[:16]}"
                observation_span["project"] = Project.objects.get(
                    _project_workspace_scope_q(self.request, project_prefix=""),
                    id=observation_span["project"],
                    organization=_get_request_organization(self.request),
                )
                if observation_span.get("project_version"):
                    observation_span["project_version"] = ProjectVersion.objects.get(
                        _project_workspace_scope_q(self.request),
                        id=observation_span["project_version"],
                        project=observation_span["project"],
                        project__organization=_get_request_organization(self.request),
                    )
                observation_span["trace"] = Trace.objects.get(
                    _project_workspace_scope_q(self.request),
                    id=observation_span["trace"],
                    project=observation_span["project"],
                    project__organization=_get_request_organization(self.request),
                )

                prompt_tokens = observation_span.get("prompt_tokens") or 0
                completion_tokens = observation_span.get("completion_tokens") or 0
                model = observation_span.get("model")
                cost = calculate_cost_from_tokens(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    model=model,
                    organization_id=(
                        getattr(request, "organization", None)
                        or request.user.organization
                    ).id,
                )

                observation_span["cost"] = cost

            spans = [ObservationSpan(**req) for req in observation_span_data]
            added_observation_spans = ObservationSpan.objects.bulk_create(spans)
            ids = [span.id for span in added_observation_spans]
            return self._gm.success_response({"Observation Span IDs": ids})
        except Exception as e:
            logger.exception(f"Error in creating observation spans in bulk: {str(e)}")
            return self._gm.bad_request(
                f"Error creating bulk observation spans: {get_error_message('FAILED_TO_CREATE_OBS_SPAN_BULK')}"
            )

    def create(self, request, *args, **kwargs):
        try:
            if "id" in self.request.data:
                serializer = self.get_serializer(data=request.data)
                if serializer.is_valid():
                    observation_span = serializer.save(id=request.data["id"])

                    return self._gm.success_response(
                        {"id": observation_span.id}, status=201
                    )
            else:
                serializer = self.get_serializer(data=request.data)
                if serializer.is_valid():
                    observation_span = serializer.save()

                    return self._gm.success_response(
                        {"id": observation_span.id}, status=201
                    )
            return self._gm.bad_request(serializer.errors)
        except Exception as e:
            logger.exception(f"Error in creating observation span: {str(e)}")
            return self._gm.bad_request(
                f"Error creating observation span: {get_error_message('FAILED_CREATION_OBSERVATION_SPAN')}"
            )

    @action(detail=False, methods=["post"])
    def create_otel_span(self, request, *args, **kwargs):
        try:
            data_arr = self.request.data
            organization_id = (
                getattr(self.request, "organization", None)
                or self.request.user.organization
            ).id
            user_id = self.request.user.id
            workspace_id = getattr(getattr(request, "workspace", None), "id", None)
            created_span_ids = []

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_config = {
                    executor.submit(
                        create_single_otel_span,
                        data,
                        organization_id,
                        user_id,
                        workspace_id,
                    ): data
                    for data in data_arr
                }

                for future in concurrent.futures.as_completed(future_to_config):
                    observation_span = future.result()
                    created_span_ids.append(observation_span.id)

            if request.headers.get("X-Api-Key") is not None:
                properties = get_mixpanel_properties(
                    user=request.user, span=observation_span
                )
                track_mixpanel_event(
                    MixpanelEvents.SDK_OBSERVE_CREATE.value, properties
                )
            return self._gm.success_response({"ids": created_span_ids}, status=201)
        except ResourceLimitError as e:
            logger.warning(
                f"Resource limit error in creating observation span: {str(e)}"
            )
            return self._gm.bad_request(str(e))
        except ValueError as e:
            logger.warning(f"Invalid OTEL observation span payload: {str(e)}")
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception(f"Error in creating observation span: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Error creating observation span: {get_error_message('FAILED_CREATION_OBSERVATION_SPAN')}"
            )

    @bounded_list_request(
        wall_ms=SPAN_LIST_WALL_DEADLINE_MS,
        resource="prototype_spans",
        unavailable_message="Span data is temporarily unavailable. Please retry.",
    )
    @validated_request(
        query_serializer=SpanListQuerySerializer,
        responses={
            200: SpanPrototypeListResponseSerializer,
            400: ApiErrorResponseSerializer,
            422: PageDepthExceededErrorSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def list_spans(self, request, *args, **kwargs):
        """
        List spans filtered by project ID and project version ID with optimized queries.
        """
        project_version_id = ""
        try:
            serializer = SpanListQuerySerializer(data=request.query_params)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            validated_data = dict(serializer.validated_data)
            validated_data["filters"] = bind_request_my_annotations_principal(
                request,
                validated_data.get("filters", []),
            )
            project_version_id = str(validated_data["project_version_id"])

            # Tenant gate via PG (ProjectVersion + Project.organization).
            project_version = ProjectVersion.objects.get(
                _project_workspace_scope_q(request),
                id=project_version_id,
                project__organization=_get_request_organization(request),
            )

            # Direct-write CH25-only path post-migration. D-027: the previous PG fallback
            # (huge ObservationSpan.objects.filter + per-config metric
            # annotations + Score subqueries + Python pivot, ~270 LOC)
            # was deleted. CH is the authoritative span + eval store; the
            # eval/annotation pivots live in `_list_spans_non_observe_clickhouse`
            # via SpanListQueryBuilderV2. Legacy routing is not a fallback.
            analytics = V2AnalyticsQueryService()
            return self._list_spans_non_observe_clickhouse(
                request,
                project_version_id,
                project_version,
                analytics,
                validated_data,
                read_deadline=kwargs.get("read_deadline"),
            )

        except ProjectVersion.DoesNotExist:
            return self._gm.bad_request("Project version not found")
        except UnsupportedFilterShapeError:
            return self._gm.bad_request("Span filter configuration is invalid")
        except FilterPrincipalContextError as exc:
            return self._gm.bad_request(str(exc))
        except Exception as exc:
            if is_read_budget_error(exc) or is_clickhouse_query_error(exc):
                logger.warning(
                    "span_list_query_unavailable",
                    project_version_id=str(project_version_id),
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Span data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "span_list_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Span data could not be loaded")

    @action(detail=False, methods=["post"])
    def submit_feedback(self, request, *args, **kwargs):
        try:
            serializer = SubmitFeedbackSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            validated_data = serializer.validated_data
            observation_span_id = validated_data.get("observation_span_id", None)
            custom_eval_config_id = validated_data.get("custom_eval_config_id", None)
            feedback_value = validated_data.get("feedback_value", None)
            feedback_explanation = validated_data.get("feedback_explanation", None)
            feedback_improvement = validated_data.get("feedback_improvement", None)

            try:
                observation_span = ObservationSpan.objects.get(
                    _project_workspace_scope_q(request),
                    id=observation_span_id,
                    project__organization=_get_request_organization(request),
                )
            except ObservationSpan.DoesNotExist:
                raise Exception("Observation span not found")  # noqa: B904

            try:
                custom_eval_config = CustomEvalConfig.objects.get(
                    _project_workspace_scope_q(request),
                    id=custom_eval_config_id,
                    project__organization=_get_request_organization(request),
                )
            except CustomEvalConfig.DoesNotExist:
                raise Exception("Custom eval config not found")  # noqa: B904

            try:
                EvalLogger.objects.get(
                    observation_span=observation_span,
                    custom_eval_config_id=custom_eval_config_id,
                    deleted=False,
                )
            except EvalLogger.DoesNotExist:
                raise Exception("No eval associated with this span ")  # noqa: B904

            eval_template = custom_eval_config.eval_template

            feedback = Feedback.objects.create(
                source=(
                    FeedbackSourceChoices.EXPERIMENT.value
                    if observation_span.project_version
                    else FeedbackSourceChoices.OBSERVE.value
                ),
                source_id=observation_span_id,
                value=feedback_value,
                explanation=feedback_explanation,
                eval_template=eval_template,
                feedback_improvement=feedback_improvement,
                user=request.user,
                custom_eval_config_id=custom_eval_config_id,
                organization=observation_span.project.organization,
                workspace=observation_span.project.workspace,
            )

            trace = Trace.objects.get(id=observation_span.trace.id)
            trace_data = TraceSerializer(trace).data

            # get_fewshots = RAG()
            embedding_manager = EmbeddingManager()

            embedding_manager.data_formatter(
                eval_id=eval_template.id,
                row_dict=trace_data,
                inputs_formater=[observation_span.id],
                organization_id=observation_span.project.organization.id,
                workspace_id=(
                    observation_span.project.workspace.id
                    if observation_span.project.workspace
                    else None
                ),
            )
            embedding_manager.close()

            return self._gm.success_response({"feedback_id": str(feedback.id)})
        except Exception as e:
            logger.exception(f"Error in submitting the feedback: {str(e)}")
            return self._gm.bad_request(
                f"Error submitting feedback: {get_error_message('FAILED_TO_CREATE_FEEDBACK')}"
            )

    @action(detail=False, methods=["post"], url_path="update-tags")
    def update_tags(self, request, *args, **kwargs):
        """Update tags for an observation span."""
        try:
            span_id = request.data.get("span_id")
            if not span_id:
                return self._gm.bad_request("span_id is required")
            span = ObservationSpan.objects.get(
                _project_workspace_scope_q(request),
                id=span_id,
                project__organization=_get_request_organization(request),
            )
            tags = request.data.get("tags")
            if tags is None:
                return self._gm.bad_request("tags field is required")
            if not isinstance(tags, list):
                return self._gm.bad_request("tags must be a list")
            span.tags = tags
            span.save(update_fields=["tags"])
            return self._gm.success_response({"id": str(span.id), "tags": span.tags})
        except ObservationSpan.DoesNotExist:
            return self._gm.bad_request("Observation span not found")
        except Exception as e:
            logger.exception(f"Error updating span tags: {e}")
            return self._gm.bad_request("Error updating tags")

    @action(detail=False, methods=["post"])
    def submit_feedback_action_type(self, request, *args, **kwargs):
        try:
            serializer = SubmitFeedbackActionTypeSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            validated_data = serializer.validated_data
            observation_span_id = validated_data.get("observation_span_id", None)
            action_type = validated_data.get("action_type", None)
            custom_eval_config_id = validated_data.get("custom_eval_config_id", None)
            feedback_id = validated_data.get("feedback_id", None)

            try:
                feedback = Feedback.objects.get(
                    id=feedback_id, user=request.user, source_id=observation_span_id
                )
                feedback.action_type = action_type
                feedback.save(update_fields=["action_type"])
            except Feedback.DoesNotExist:
                raise Exception("Feedback not found")  # noqa: B904

            try:
                observation_span = ObservationSpan.objects.get(
                    _project_workspace_scope_q(request),
                    id=observation_span_id,
                    project__organization=_get_request_organization(request),
                )
            except ObservationSpan.DoesNotExist:
                raise Exception("Observation span not found")  # noqa: B904

            try:
                custom_eval_config = CustomEvalConfig.objects.get(
                    _project_workspace_scope_q(request),
                    id=custom_eval_config_id,
                    project__organization=_get_request_organization(request),
                )
            except CustomEvalConfig.DoesNotExist:
                raise Exception("Custom eval config not found")  # noqa: B904

            if action_type == "retune":
                pass  ### This is coz we are using mapping_fields fxn in utils

            elif action_type == "recalculate":
                try:
                    eval_logger = EvalLogger.objects.get(
                        observation_span=observation_span,
                        custom_eval_config=custom_eval_config,
                        deleted=False,
                    )
                    task_id = eval_logger.eval_task_id

                    eval_logger.deleted = True
                    eval_logger.deleted_at = timezone.now()
                    eval_logger.save(update_fields=["deleted", "deleted_at"])
                except EvalLogger.DoesNotExist:
                    raise Exception("No eval associated with this span")  # noqa: B904

                properties = get_mixpanel_properties(
                    user=request.user,
                    span=observation_span,
                    eval=custom_eval_config.eval_template,
                    count=1,
                    type=MixpanelTypes.FEEDBACK.value,
                )
                track_mixpanel_event(MixpanelEvents.EVAL_RUN_STARTED.value, properties)

                if observation_span.project_version:
                    status = evaluate_observation_span(
                        str(observation_span.id),
                        str(custom_eval_config.id),
                        task_id,
                        feedback_id,
                    )
                else:
                    status = evaluate_observation_span_observe(
                        str(observation_span.id),
                        str(custom_eval_config.id),
                        task_id,
                        feedback_id,
                    )

                if status:
                    count = 1
                    failed = 0
                else:
                    failed = 1
                    count = 0
                properties = get_mixpanel_properties(
                    user=request.user,
                    span=observation_span,
                    eval=custom_eval_config.eval_template,
                    count=count,
                    failed=failed,
                    type=MixpanelTypes.FEEDBACK.value,
                )
                track_mixpanel_event(
                    MixpanelEvents.EVAL_RUN_COMPLETED.value, properties
                )

            return self._gm.success_response(
                {"message": "Action type submitted successfully"}
            )
        except Exception:
            logger.exception("Error in submitting the feedback action type")
            return self._gm.bad_request(
                "Unable to submit feedback action. Please try again."
            )

    @bounded_list_request(
        wall_ms=SPAN_LIST_WALL_DEADLINE_MS,
        resource="observe_spans",
        unavailable_message="Span data is temporarily unavailable. Please retry.",
    )
    @validated_request(
        query_serializer=SpanObserveListQuerySerializer,
        responses={
            200: SpanObserveListResponseSerializer,
            400: ApiErrorResponseSerializer,
            422: PageDepthExceededErrorSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def list_spans_observe(self, request, *args, **kwargs):
        try:
            validated_data = dict(request.validated_query_data)
            if kwargs.get("bounded_export"):
                validated_data.update(
                    page_number=0,
                    page_size=BOUNDED_SPAN_EXPORT_PAGE_SIZE,
                    cursor_mode=True,
                )
            validated_data["filters"] = bind_request_my_annotations_principal(
                request,
                validated_data.get("filters", []),
            )

            project_id = (
                str(validated_data["project_id"])
                if validated_data.get("project_id")
                else None
            )
            org = _get_request_organization(request)

            org_project_ids = None
            if project_id:
                try:
                    Project.objects.get(
                        _project_workspace_scope_q(self.request, project_prefix=""),
                        id=project_id,
                        organization=org,
                    )
                except Project.DoesNotExist:
                    return self._gm.bad_request("Project not found or access denied")
            else:
                org_project_ids = list(
                    Project.objects.filter(
                        _project_workspace_scope_q(self.request, project_prefix=""),
                        organization=org,
                        deleted=False,
                    ).values_list("id", flat=True)
                )
                # An organization with no visible projects is an exact empty
                # scope. Do not construct an org-scoped builder with an empty
                # project list: BaseQueryBuilder would otherwise fall back to
                # the single-project predicate and bind ``project_id=None``.
                if not org_project_ids:
                    return self._gm.success_response(
                        {
                            "metadata": {"total_rows": 0},
                            "table": [],
                            "config": get_default_span_config(include_user_fields=True),
                        }
                    )

            # Direct-write CH25-only path post-migration. D-027: the previous PG fallback
            # body (ObservationSpan.objects.filter + per-config metric
            # annotations + Score subqueries + Python pivot, ~350 LOC) was
            # deleted. CH is the authoritative span + eval store and the
            # pivot now lives in `_list_spans_clickhouse` via
            # SpanListQueryBuilderV2. A CH read failure surfaces via the outer
            # handler instead of silently degrading to the empty post-migration
            # Postgres path, which masked CH failures as "0 rows".
            analytics = V2AnalyticsQueryService()
            return self._list_spans_clickhouse(
                request,
                project_id,
                validated_data,
                analytics,
                org_project_ids=org_project_ids,
                org=org,
                read_deadline=kwargs.get("read_deadline"),
            )

        except ListCursorError as exc:
            return self._gm.custom_error_response(
                status.HTTP_400_BAD_REQUEST, str(exc), code=exc.code
            )
        except UnsupportedFilterShapeError:
            return self._gm.bad_request("Span filter configuration is invalid")
        except FilterPrincipalContextError as exc:
            return self._gm.bad_request(str(exc))
        except Exception as exc:
            if is_clickhouse_api_read_unavailable_error(exc):
                logger.warning(
                    "observe_span_list_query_unavailable",
                    project_id=str(
                        getattr(request, "validated_query_data", {}).get(
                            "project_id", ""
                        )
                    ),
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Span data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "observe_span_list_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Span data could not be loaded",
                code="server_error",
            )

    def _list_spans_clickhouse(
        self,
        request,
        project_id,
        validated_data,
        analytics,
        org_project_ids=None,
        org=None,
        read_deadline=None,
    ):
        """List spans from the direct-write ClickHouse 25 schema."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        read_deadline = read_deadline or ReadDeadline.start(SPAN_LIST_WALL_DEADLINE_MS)

        org_scope = bool(org_project_ids)
        if org is None:
            org = _get_request_organization(request)
        # Keep the v1 class import only for its schema-agnostic static pivot
        # helpers below. Query construction is always SpanListQueryBuilderV2.

        filters = list(validated_data.get("filters", []) or [])
        attested_filters = list(filters)
        page_number = validated_data["page_number"]
        page_size = validated_data["page_size"]
        cursor_token = validated_data.get("cursor")
        cursor_requested = bool(cursor_token or validated_data.get("cursor_mode"))
        scope_project_ids = [
            str(value)
            for value in (org_project_ids or ([project_id] if project_id else []))
        ]
        cursor_scope = cursor_scope_for_request(request, project_ids=scope_project_ids)
        cursor_query = dict(validated_data)
        cursor_state = None
        if cursor_token:
            cursor_state = decode_list_cursor(
                cursor_token,
                resource="observe_spans",
                scope=cursor_scope,
                query=cursor_query,
                page_size=page_size,
            )
            if (
                len(cursor_state.order) != 4
                or not isinstance(cursor_state.order[0], datetime)
                or not all(isinstance(value, str) for value in cursor_state.order[1:])
            ):
                raise ListCursorError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            filters.append(frozen_window_filter(cursor_state))
            page_number = 0
        # P3b step2 precondition — user_id → end_user reverse-resolve (CH, not PG).
        # The old PG `EndUser.objects.get(user_id=…).id` FREEZES post-step2: a
        # NET-NEW user (first seen after the ingest get_or_create is dropped) has
        # NO `tracer_enduser` row, only a CH `end_users` row keyed by its
        # deterministic id + spans carrying that id — so the PG lookup raised
        # "User not found" and the list was empty for it. Instead, inject a
        # synthetic `user_id` filter and let the SHIPPED, remap-aware
        # `ClickHouseFilterBuilder._build_enduser_string_condition` resolve it:
        # it builds the curated id-set from `end_users FINAL` (historical + net-new
        # deterministic + straddler's both) and matches it against each span's
        # `end_user_id` resolved new→old via `end_user_id_remap`. This REPLACES the
        # bespoke `end_user_id=` builder arg (the only non-test caller of it) with
        # the canonical filter path — zero duplicated SQL, and net-new now returns
        # rows. Pre-flip a no-op vs the old single-id filter (gate B): historical /
        # straddler resolve to the same curated id-set. An unknown user resolves to
        # an EMPTY id-set → empty list (was an exception; net-new is no longer
        # "not found", the intended fix).
        user_id = validated_data.get("user_id")
        if user_id:
            filters.append(
                {
                    "column_id": "user_id",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": str(user_id),
                    },
                }
            )

        if not cursor_token and _span_filtered_page_depth_exceeded(
            filters, page_number, page_size
        ):
            logger.info(
                "span_list_page_depth_exceeded_preflight",
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
                page_size=page_size,
            )
            return self._gm.custom_error_response(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                PAGE_DEPTH_EXCEEDED_MESSAGE,
                code=PAGE_DEPTH_EXCEEDED_CODE,
            )

        # Eval configuration metadata is small and remains in PostgreSQL. Do
        # not infer which configs have data through PG ObservationSpan or
        # EvalLogger telemetry: direct-write CH25 is authoritative.
        eval_config_ids = []
        if org_scope:
            eval_configs = CustomEvalConfig.objects.filter(
                project_id__in=org_project_ids,
                deleted=False,
            ).select_related("eval_template")
            eval_config_ids = [str(c.id) for c in eval_configs]
        else:
            # Configuration metadata is already a finite project-scoped PG
            # read. A window-wide CH discovery query before the page selector
            # consumed the entire endpoint deadline on high-volume tenants.
            # Include all project configs; page-scoped eval hydration below is
            # bounded by the selected span identities and simply returns no
            # cell for configs without page data.
            eval_configs = list(
                CustomEvalConfig.objects.filter(
                    project_id=project_id, deleted=False
                ).select_related("eval_template")
            )
            eval_config_ids = [str(c.id) for c in eval_configs]

        # Labels can be project-local or org/shared labels referenced by span
        # scores. Completeness is project-local, so retain that mapping for the
        # residual classifier instead of feeding it an org-wide union.
        annotation_label_ids_by_project = None
        if org_scope:
            labels_by_project = get_annotation_labels_by_project(
                [str(project_id) for project_id in org_project_ids],
                organization=org,
            )
            annotation_label_ids_by_project = {
                project_key: [str(label.id) for label in labels]
                for project_key, labels in labels_by_project.items()
            }
            annotation_labels = list(
                {
                    str(label.id): label
                    for labels in labels_by_project.values()
                    for label in labels
                }.values()
            )
        else:
            annotation_labels = list(get_annotation_labels_for_project(project_id))
        annotation_label_ids = [str(lbl.id) for lbl in annotation_labels]
        label_types = {str(lbl.id): lbl.type for lbl in annotation_labels}

        # No `end_user_id=` arg: the user filter is now a synthetic `user_id`
        # filter in `filters` (resolved via the remap-aware `end_users` path
        # above), so the builder's bespoke single-id end_user path is unused here.
        cursor_supported = snapshot_cursor_supported(filters, resource="observe_spans")
        if cursor_state is not None and not cursor_supported:
            raise ListCursorError(
                "cursor_unsupported",
                "Cursor pagination is unavailable for this query shape.",
            )
        cursor_enabled = cursor_requested and cursor_supported
        builder = SpanListQueryBuilderV2(
            project_id=None if org_scope else str(project_id),
            project_ids=[str(p) for p in org_project_ids] if org_scope else None,
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            eval_config_ids=eval_config_ids,
            annotation_label_ids=annotation_label_ids,
            annotation_label_ids_by_project=annotation_label_ids_by_project,
            bounded_internal_scan=cursor_enabled,
        )
        requires_cursor = builder.requires_cursor_for_long_filtered_read()
        if requires_cursor and not cursor_supported:
            # A cursor-ineligible long filter must fail closed before any
            # broad legacy ClickHouse read can be attempted.
            raise UnsupportedFilterShapeError(
                "Long-window span filter is not cursor-safe"
            )
        if not cursor_requested and requires_cursor:
            if "cursor_mode" in request.query_params:
                return self._gm.custom_error_response(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    CURSOR_REQUIRED_MESSAGE,
                    code=CURSOR_REQUIRED_CODE,
                )
            logger.info(
                "span_list_legacy_numbered_cursor_compatibility",
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
            )
        # Custom-sort/time-only legacy reads do not apply the bounded
        # selector's keyset tuple. Advertising a cursor for that path would
        # replay the first page forever, so keep its existing numbered-page
        # contract and fail closed if a continuation somehow reaches it.
        if cursor_enabled and not builder.supports_bounded_filter_scan():
            if cursor_state is not None:
                raise ListCursorError(
                    "cursor_unsupported",
                    "Cursor pagination is unavailable for this query shape.",
                )
            cursor_enabled = False
            builder._bounded_internal_scan = False
        # Freeze the finite request window and keyset, not mutable physical
        # versions. ReplacingMergeTree merges can discard the old version a
        # ceiling needs, so each page resolves current latest state instead.
        page_read_settings = SPAN_LIST_READ_SETTINGS

        # Phase 1: Paginated spans (light columns — no input/output).
        bounded_page = None
        bounded_error_code = builder.bounded_filter_degraded_error_code()
        if bounded_error_code == "unsupported_filter_shape":
            raise UnsupportedFilterShapeError(
                "Span filter cannot be evaluated by the bounded list reader"
            )
        try:
            candidate_deadline_ms = read_deadline.remaining_ms(
                SPAN_LIST_CANDIDATE_DEADLINE_MS
            )
        except ReadDeadlineExceeded:
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Span data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        if builder.supports_bounded_filter_scan():
            from tracer.selectors.trace_filter_reads import read_bounded_filter_page
            from tracer.services.clickhouse.query_service import QueryResult

            bounded_page = read_bounded_filter_page(
                builder=builder,
                analytics=analytics,
                filters=filters,
                key_field="id",
                page_number=page_number,
                page_size=page_size,
                deadline_ms=candidate_deadline_ms,
                cursor_start_time=(
                    cursor_state.order[0] if cursor_state is not None else None
                ),
                cursor_order_token=(
                    tuple(cursor_state.order[1:]) if cursor_state is not None else None
                ),
                read_settings=page_read_settings,
                include_incomplete_rows=cursor_enabled,
                continuation_slice_start=(
                    cursor_state.scan_slice_start if cursor_state is not None else None
                ),
                continuation_slice_end=(
                    cursor_state.scan_slice_end if cursor_state is not None else None
                ),
                continuation_before_start_time=(
                    cursor_state.scan_before_start_time
                    if cursor_state is not None
                    else None
                ),
                continuation_before_id=(
                    cursor_state.scan_before_id if cursor_state is not None else None
                ),
                bounded_continuation=cursor_enabled,
            )
            if not bounded_page.complete:
                if bounded_page.error_code == PAGE_DEPTH_EXCEEDED_CODE:
                    logger.info(
                        "span_list_page_depth_exceeded",
                        project_id=str(project_id) if project_id else None,
                        page_number=page_number,
                        page_size=page_size,
                    )
                    return self._gm.custom_error_response(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        PAGE_DEPTH_EXCEEDED_MESSAGE,
                        code=PAGE_DEPTH_EXCEEDED_CODE,
                    )
                failed_attempt = next(
                    (
                        attempt
                        for attempt in bounded_page.attempts
                        if attempt.error_code is not None
                    ),
                    None,
                )
                safe_cursor_checkpoint = bool(
                    cursor_enabled and bounded_page.continuation_slice_end is not None
                )
                if failed_attempt is not None and not safe_cursor_checkpoint:
                    logger.warning(
                        "span_list_bounded_statement_failed",
                        project_id=str(project_id) if project_id else None,
                        page_number=page_number,
                        error_code=failed_attempt.error_code,
                    )
                    return self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Filtered span data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                if failed_attempt is not None:
                    logger.warning(
                        "span_list_bounded_statement_checkpoint_published",
                        project_id=str(project_id) if project_id else None,
                        page_number=page_number,
                        error_code=failed_attempt.error_code,
                    )
                logger.warning(
                    "span_list_bounded_read_incomplete",
                    project_id=str(project_id) if project_id else None,
                    page_number=page_number,
                    error_code=bounded_page.error_code,
                )
                if not cursor_enabled or bounded_page.continuation_slice_end is None:
                    return self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Filtered span data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
            result = QueryResult(
                data=bounded_page.rows,
                row_count=len(bounded_page.rows),
                backend_used="clickhouse",
                query_time_ms=bounded_page.elapsed_ms,
            )
            has_more = bounded_page.has_more
        elif bounded_error_code:
            logger.warning(
                "span_list_filter_unsupported",
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
                error_code=bounded_error_code,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Filtered span data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        else:
            query, params = builder.build()
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=read_deadline.remaining_ms(1_200),
                settings=page_read_settings,
            )

            result.data, has_more = paginate_deduped(
                result.data,
                ("project_id", "trace_id", "id"),
                page_number,
                page_size,
            )

        span_ids = [str(row.get("id", "")) for row in result.data if row.get("id")]
        # OTel span IDs are unique only within their trace. Carry that logical
        # identity through every page-scoped read and merge; a bare span ID can
        # otherwise attach content/evals/annotations from another trace.
        span_identities, span_entities, app_identity_by_external = (
            _span_page_identity_sets(
                result.data,
                default_project_id=None if org_scope else str(project_id),
            )
        )
        # Oldest created_at on the page — lower bound for the eval/annotation
        # reads below. Both tables are PARTITION BY toYYYYMM(created_at) and an
        # eval/score row cannot be created before its span row exists, so the
        # bound (with a 7-day margin in the builder) only prunes partitions
        # that cannot hold matches — measured 55x fewer rows read.
        page_created_ats = [
            row.get("created_at") for row in result.data if row.get("created_at")
        ]
        page_min_created_at = min(page_created_ats) if page_created_ats else None

        query_count = bounded_page.query_count if bounded_page is not None else 1
        query_rows_returned = (
            bounded_page.rows_returned if bounded_page is not None else len(result.data)
        )
        query_result_payload_bytes = (
            bounded_page.result_payload_bytes
            if bounded_page is not None
            else len(
                json.dumps(
                    result.data,
                    default=str,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        )

        # Build count SQL on the request thread: filter translation may read PG
        # metadata, whose connections must remain request-thread owned.
        if bounded_page is None:
            count_query, count_params = builder.build_count_query()
        else:
            count_query, count_params = "", {}

        end_user_ids = {
            str(row.get("end_user_id")) for row in result.data if row.get("end_user_id")
        }
        from tracer.services.clickhouse.v2.end_user_dict_reader import (
            resolve_end_user_fields,
        )

        def _stats(value, rows, query_executed: bool):
            if not query_executed:
                return value, 0, 0, 0
            return (
                value,
                1,
                len(rows),
                len(
                    json.dumps(
                        rows,
                        default=str,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ),
            )

        # Phases 1b/2/3 + count are independent once the page ids are known —
        # run them concurrently so request latency is Phase1 + max(rest), not
        # the serial sum. `analytics.ch_client` uses the driver's queue-backed
        # connection pool, so these independent reads do not share a socket.
        # Programming defects still propagate; only typed CH/read-budget
        # failures become a sanitized unavailable response below.
        def _fetch_content():
            if not span_identities:
                return _stats([], [], False)
            content_query, content_params = builder.build_content_query(
                span_ids, span_identities=span_identities
            )
            if not content_query:
                return _stats([], [], False)
            content_result = analytics.execute_ch_query(
                content_query,
                content_params,
                timeout_ms=read_deadline.remaining_ms(SPAN_LIST_ENRICHMENT_TIMEOUT_MS),
                settings=page_read_settings,
            )
            return _stats(content_result.data, content_result.data, True)

        def _fetch_count():
            if bounded_page is not None:
                return _stats(
                    bounded_page.total_rows_lower_bound,
                    [],
                    False,
                )
            # Short-TTL cache keyed by the query + bindings: the count re-scans
            # the full filtered window (measured 0.65-1.15s at 10M+ rows) and is
            # identical across pages of the same view. Value is exact; staleness
            # is bounded by the TTL.
            #
            # The time-window params are bucketed to the minute before hashing.
            # On a default (unfiltered) view start_date/end_date default to
            # `datetime.utcnow()`-based values that are microsecond-fresh per
            # request (base.py parse_time_range), so hashing them raw mints a new
            # key every request and the cache never hits on exactly the view it
            # targets. Minute-bucketing makes the key stable across a view's
            # pages; the count is a display value, so a sub-minute window drift
            # against the cached value is immaterial and TTL-bounded.
            cache_params = {
                k: (
                    v.replace(second=0, microsecond=0) if isinstance(v, datetime) else v
                )
                for k, v in count_params.items()
            }
            count_key = (
                "span_list_count:"
                + hashlib.sha256(
                    (count_query + repr(sorted(cache_params.items(), key=str))).encode()
                ).hexdigest()
            )
            cached_total = django_cache.get(count_key)
            if cached_total is not None:
                return _stats(cached_total, [], False)
            count_result = analytics.execute_ch_query(
                count_query,
                count_params,
                timeout_ms=read_deadline.remaining_ms(SPAN_LIST_ENRICHMENT_TIMEOUT_MS),
                settings=SPAN_LIST_READ_SETTINGS,
            )
            total = count_result.data[0].get("total", 0) if count_result.data else 0
            django_cache.set(count_key, total, timeout=60)
            return _stats(total, count_result.data, True)

        def _fetch_evals():
            if not (span_entities and eval_config_ids):
                return _stats({}, [], False)
            eval_query, eval_params = builder.build_eval_query(
                span_ids,
                created_after=page_min_created_at,
                span_entities=span_entities,
            )
            if not eval_query:
                return _stats({}, [], False)
            eval_result = analytics.execute_ch_query(
                eval_query,
                eval_params,
                timeout_ms=read_deadline.remaining_ms(SPAN_LIST_ENRICHMENT_TIMEOUT_MS),
                settings=SPAN_LIST_READ_SETTINGS,
            )
            external_map = SpanListQueryBuilder.pivot_eval_results(
                eval_result.data, key_by_trace=True
            )
            value = {
                app_identity_by_external[external_key]: values
                for external_key, values in external_map.items()
                if external_key in app_identity_by_external
            }
            return _stats(value, eval_result.data, True)

        def _fetch_annotations():
            if not (span_entities and annotation_label_ids):
                return _stats({}, [], False)
            ann_query, ann_params = builder.build_annotation_query(
                span_ids,
                created_after=page_min_created_at,
                span_entities=span_entities,
            )
            if not ann_query:
                return _stats({}, [], False)
            ann_result = analytics.execute_ch_query(
                ann_query,
                ann_params,
                timeout_ms=read_deadline.remaining_ms(SPAN_LIST_ENRICHMENT_TIMEOUT_MS),
                settings=SPAN_LIST_READ_SETTINGS,
            )
            external_map = SpanListQueryBuilder.pivot_annotation_results(
                ann_result.data, label_types, key_by_trace=True
            )
            value = {
                app_identity_by_external[external_key]: values
                for external_key, values in external_map.items()
                if external_key in app_identity_by_external
            }
            return _stats(value, ann_result.data, True)

        def _fetch_end_users():
            if not end_user_ids:
                return _stats({}, [], False)
            value = resolve_end_user_fields(
                end_user_ids,
                timeout_ms=read_deadline.remaining_ms(SPAN_LIST_ENRICHMENT_TIMEOUT_MS),
                settings=SPAN_LIST_READ_SETTINGS,
            )
            return _stats(value, list(value.items()), True)

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        future_names = {
            pool.submit(_fetch_content): "content",
            pool.submit(_fetch_count): "count",
            pool.submit(_fetch_evals): "evals",
            pool.submit(_fetch_annotations): "annotations",
            pool.submit(_fetch_end_users): "end_users",
        }
        completed: dict[str, tuple[Any, int, int, int]] = {}
        try:
            wait_seconds = read_deadline.remaining_ms() / 1000
            for future in concurrent.futures.as_completed(
                future_names, timeout=wait_seconds
            ):
                completed[future_names[future]] = future.result()
            read_deadline.remaining_ms()
        except (concurrent.futures.TimeoutError, ReadDeadlineExceeded) as exc:
            logger.warning(
                "span_list_enrichment_deadline_exceeded",
                error_type=type(exc).__name__,
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Span data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as exc:
            logger.warning(
                "span_list_enrichment_failed",
                error_type=type(exc).__name__,
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
                exc_info=True,
            )
            if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
                raise
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Span data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        if set(completed) != set(future_names.values()):
            raise AssertionError("span enrichment futures did not all complete")

        for _value, phase_queries, phase_rows, phase_bytes in completed.values():
            query_count += phase_queries
            query_rows_returned += phase_rows
            query_result_payload_bytes += phase_bytes

        content_rows = completed["content"][0]
        total_count = completed["count"][0]
        eval_map = completed["evals"][0]
        annotation_map = completed["annotations"][0]
        end_user_map = completed["end_users"][0]
        if span_identities and len(content_rows) < len(span_identities):
            logger.warning(
                "span_list_content_replay_incomplete",
                returned=len(content_rows),
                requested=len(span_identities),
                project_id=str(project_id) if project_id else None,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Span data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

        # Phase 1b merge: input/output/attributes_extra AND the typed attr maps
        # (attrs_string/attrs_number/attrs_bool) onto the page rows. The typed
        # maps are read by flatten_span_attributes_into_entry() below to populate
        # custom span-attribute columns — build_content_query fetches them, so
        # dropping them here renders every typed-map custom column empty. Use the
        # shared helper (null-safe factory defaults for the map keys), matching
        # the trace-list read path.
        merge_content_rows(
            result.data,
            content_rows,
            id_key=("project_id", "trace_id", "id", "start_time"),
            keys=(
                "input",
                "output",
                "attributes_extra",
                "attrs_string",
                "attrs_number",
                "attrs_bool",
            ),
        )

        # Build column config (from PG config tables)
        column_config = get_default_span_config(include_user_fields=True)
        column_config = update_column_config_based_on_eval_config(
            column_config, eval_configs
        )
        column_config = update_span_column_config_based_on_annotations(
            column_config, annotation_labels
        )

        # Batch-resolve end_user UUIDs → (user_id, user_id_type, user_id_hash)
        # so each row can surface the human-readable user identifier. CH only
        # stores the UUID; the curated display fields live on the v2 `end_users`
        # dimension (its dict). P3b step2 precondition: swap the PG
        # `EndUser.objects.filter(id__in=…)` lookup (which is EMPTY for a net-new
        # user's id — no PG row post-flip) for the SHIPPED, remap-aware
        # `end_user_dict_reader.resolve_end_user_fields`. It resolves each id
        # new→old through `end_user_id_remap` then `dictGetOrNull`s the curated
        # fields, so a net-new span's deterministic id (no remap entry → resolves
        # to itself) still yields its `end_users` fields, a straddler's new-id
        # span resolves to the old curated row, and a missing/orphan id → all-None
        # (faithful to the old FK miss). Returns {id (str): {user_id,
        # user_id_type, user_id_hash}}.
        # Format response matching PG format
        table_data = []
        for row in result.data:
            span_id = str(row.get("id", ""))
            span_entity = (
                str(row.get("project_id", "")),
                str(row.get("trace_id", "")),
                span_id,
            )
            cost = row.get("cost")
            eu = (
                end_user_map.get(str(row.get("end_user_id")))
                if row.get("end_user_id")
                else None
            )
            entry = {
                "project_id": str(row.get("project_id", "")),
                "span_id": span_id,
                "input": bound_observe_list_value(row.get("input", "")),
                "output": bound_observe_list_value(row.get("output", "")),
                "trace_id": str(row.get("trace_id", "")),
                "created_at": row.get("created_at"),
                "node_type": row.get("observation_type", ""),
                "span_name": row.get("name", ""),
                # `eu` is now a {user_id, user_id_type, user_id_hash} dict from
                # `resolve_end_user_fields` (was a PG EndUser instance) — read by
                # key, defaulting to None (the all-None record for a missing id).
                "user_id": eu.get("user_id") if eu else None,
                "user_id_type": eu.get("user_id_type") if eu else None,
                "user_id_hash": eu.get("user_id_hash") if eu else None,
                "start_time": row.get("start_time"),
                "status": row.get("status"),
                "latency_ms": row.get("latency_ms"),
                "total_tokens": row.get("total_tokens"),
                "prompt_tokens": row.get("prompt_tokens"),
                "completion_tokens": row.get("completion_tokens"),
                "model": row.get("model"),
                "provider": row.get("provider"),
                "cost": round(cost, 6) if cost else 0,
            }

            # Add eval metrics
            span_evals = eval_map.get(span_entity, {})
            for config in eval_configs:
                config_id = str(config.id)
                if config_id not in span_evals:
                    continue
                val = span_evals[config_id]
                # Lifecycle marker — ``{"status": ...}`` (pending/running/skipped)
                # or ``{"error": True}`` (errored): pass the whole marker through
                # on the ``config_id`` column so the cell renders the
                # loading / pending / skipped / error state instead of a blank.
                if isinstance(val, dict) and (
                    isinstance(val.get("status"), str) or val.get("error")
                ):
                    entry[config_id] = val
                # CHOICES eval: spread per-choice percentages into separate
                # columns keyed ``{config_id}**{choice}`` to match the
                # column config produced by
                # ``update_column_config_based_on_eval_config``.
                elif isinstance(val, dict) and not val.get("error") and val:
                    for choice, pct in val.items():
                        entry[f"{config_id}**{choice}"] = pct
                else:
                    entry[config_id] = val
                    if isinstance(val, dict):
                        entry[config_id] = val.get("score")
                    else:
                        entry[config_id] = val

            # Add annotations
            span_annotations = annotation_map.get(span_entity, {})
            for label in annotation_labels:
                label_id = str(label.id)
                if label_id in span_annotations:
                    entry[label_id] = span_annotations[label_id]

            # Include span attributes (typed maps + attributes_extra) for custom columns
            flatten_span_attributes_into_entry(entry, row)

            table_data.append(entry)

        try:
            read_deadline.remaining_ms()
        except ReadDeadlineExceeded:
            logger.warning(
                "span_list_response_deadline_exceeded",
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Span data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

        next_cursor = None
        cursor_seen_rows = (
            cursor_state.seen_rows
            if cursor_state is not None
            else page_number * page_size
        ) + len(result.data)
        cursor_has_more = False
        if cursor_enabled and (
            (bounded_page is None and has_more)
            or (
                bounded_page is not None
                and bounded_page.complete
                and bounded_page.has_more
            )
            or (
                bounded_page is not None
                and not bounded_page.complete
                and (
                    bounded_page.has_more
                    or bounded_page.continuation_slice_end is not None
                )
            )
        ):
            window_start, window_end = builder.parse_time_range(filters)
            next_cursor = encode_list_cursor(
                resource="observe_spans",
                scope=cursor_scope,
                query=cursor_query,
                page_size=page_size,
                window_start=window_start,
                window_end=window_end,
                order=_span_cursor_order_for_partial_page(
                    rows=result.data,
                    bounded_page=bounded_page,
                    cursor_state=cursor_state,
                ),
                seen_rows=cursor_seen_rows,
                scan_slice_start=(
                    bounded_page.continuation_slice_start
                    if bounded_page is not None and not bounded_page.has_more
                    else None
                ),
                scan_slice_end=(
                    bounded_page.continuation_slice_end
                    if bounded_page is not None and not bounded_page.has_more
                    else None
                ),
                scan_before_start_time=(
                    bounded_page.continuation_before_start_time
                    if bounded_page is not None and not bounded_page.has_more
                    else None
                ),
                scan_before_id=(
                    bounded_page.continuation_before_id
                    if bounded_page is not None and not bounded_page.has_more
                    else None
                ),
            )
            cursor_has_more = True

        metadata = {"total_rows": total_count}
        if bounded_page is not None:
            public_chunk_complete = bounded_page.complete or cursor_has_more
            metadata.update(
                {
                    "total_rows_is_lower_bound": True,
                    "has_more": cursor_has_more,
                    "query_complete": public_chunk_complete,
                    "query_status": (
                        "complete" if public_chunk_complete else bounded_page.status
                    ),
                    "query_error_code": (
                        None if public_chunk_complete else bounded_page.error_code
                    ),
                    "query_elapsed_ms": round(read_deadline.elapsed_ms(), 3),
                    "query_count": query_count,
                    "query_rows_returned": query_rows_returned,
                    "query_result_payload_bytes": query_result_payload_bytes,
                }
            )
        metadata.update(
            cursor_page_metadata(
                enabled=cursor_enabled,
                has_more=cursor_has_more,
                seen_rows=cursor_seen_rows,
                next_cursor=next_cursor,
                unseen_row_proven=bool(
                    bounded_page is not None and bounded_page.has_more
                ),
            )
        )
        if metadata.get(
            "total_rows_is_lower_bound"
        ) and exact_total_explicitly_required(
            request,
            validated_data,
            allow_exact_cursor_lower_bound=True,
        ):
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Span data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        if project_id:
            filter_evidence = applied_filter_attestation(
                project_id=project_id,
                observe_type="span",
                filters=attested_filters,
            )
            if filter_evidence["query_applied_filter_count"]:
                metadata.update(filter_evidence)
        response = {
            "metadata": metadata,
            "table": table_data,
            "config": column_config,
        }

        return self._gm.success_response(response)

    def _list_spans_non_observe_clickhouse(
        self,
        request,
        project_version_id,
        project_version,
        analytics,
        validated_data,
        *,
        read_deadline=None,
    ):
        """List prompt-version/eval-task spans from direct-write ClickHouse 25."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        filters = validated_data.get("filters", [])
        page_number = validated_data.get("page_number", 0)
        page_size = validated_data.get("page_size", 30)

        project_id = str(project_version.project_id)
        read_deadline = read_deadline or ReadDeadline.start(SPAN_LIST_WALL_DEADLINE_MS)

        if _span_filtered_page_depth_exceeded(filters, page_number, page_size):
            logger.info(
                "prototype_span_list_page_depth_exceeded_preflight",
                project_version_id=str(project_version_id),
                page_number=page_number,
                page_size=page_size,
            )
            return self._gm.custom_error_response(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                PAGE_DEPTH_EXCEEDED_MESSAGE,
                code=PAGE_DEPTH_EXCEEDED_CODE,
            )

        # Configuration metadata stays in PG; telemetry presence is CH-only.
        # The former EvalLogger→ObservationSpan PG join is empty/stale after
        # direct-write cutover and silently removed eval columns from this
        # task/prompt-version list.
        project_configs = list(
            CustomEvalConfig.objects.filter(
                project_id=project_id, deleted=False
            ).select_related("eval_template")
        )
        candidate_ids = [str(config.id) for config in project_configs]
        ids_with_data: set[str] = set()
        if candidate_ids:
            window_days = SpanListQueryBuilderV2.window_days_covering(filters)
            cache_key = (
                "span_list_non_observe_eval_cfgs:"
                + hashlib.sha256(
                    (
                        project_id
                        + "|"
                        + ",".join(sorted(candidate_ids))
                        + f"|w={window_days}"
                    ).encode()
                ).hexdigest()
            )
            cached_ids = django_cache.get(cache_key)
            if cached_ids is not None:
                ids_with_data = set(cached_ids)
            else:
                ids_with_data = set(
                    analytics.get_eval_config_ids_with_data_ch(
                        project_id,
                        timeout_ms=read_deadline.remaining_ms(
                            SPAN_LIST_ENRICHMENT_TIMEOUT_MS
                        ),
                        candidate_config_ids=candidate_ids,
                        window_days=window_days,
                    )
                )
                django_cache.set(cache_key, list(ids_with_data), timeout=120)
        eval_configs = [
            config for config in project_configs if str(config.id) in ids_with_data
        ]
        eval_config_ids = [str(c.id) for c in eval_configs]

        # Labels can be project-local or org/shared labels that are referenced
        # by span scores. Use the score-backed helper so span columns and
        # annotation filters match the actual data returned from ClickHouse.
        annotation_labels = get_annotation_labels_for_project(project_id)
        annotation_label_ids = [str(lbl.id) for lbl in annotation_labels]
        label_types = {str(lbl.id): lbl.type for lbl in annotation_labels}

        builder = SpanListQueryBuilderV2(
            project_id=project_id,
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            eval_config_ids=eval_config_ids,
            annotation_label_ids=annotation_label_ids,
            project_version_id=str(project_version_id),
        )

        # Phase 1: Paginated spans (light columns — no input/output). Filtered
        # task/prompt-version reads use the same bounded, latest-state reader as
        # Observe. The project-version predicate is compiled into both the seed
        # and exact classifier, so this path never widens to the whole project.
        bounded_page = None
        bounded_error_code = builder.bounded_filter_degraded_error_code()
        if bounded_error_code == "unsupported_filter_shape":
            raise UnsupportedFilterShapeError(
                "Span filter cannot be evaluated by the bounded list reader"
            )
        if builder.supports_bounded_filter_scan():
            from tracer.selectors.trace_filter_reads import read_bounded_filter_page
            from tracer.services.clickhouse.query_service import QueryResult

            bounded_page = read_bounded_filter_page(
                builder=builder,
                analytics=analytics,
                filters=filters,
                key_field="id",
                page_number=page_number,
                page_size=page_size,
                deadline_ms=read_deadline.remaining_ms(4_500),
            )
            if not bounded_page.complete:
                if bounded_page.error_code == PAGE_DEPTH_EXCEEDED_CODE:
                    logger.info(
                        "prototype_span_list_page_depth_exceeded",
                        project_version_id=str(project_version_id),
                        page_number=page_number,
                        page_size=page_size,
                    )
                    return self._gm.custom_error_response(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        PAGE_DEPTH_EXCEEDED_MESSAGE,
                        code=PAGE_DEPTH_EXCEEDED_CODE,
                    )
                logger.warning(
                    "non_observe_span_list_bounded_read_incomplete",
                    project_id=project_id,
                    project_version_id=str(project_version_id),
                    page_number=page_number,
                    error_code=bounded_page.error_code,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Filtered span data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            result = QueryResult(
                data=bounded_page.rows,
                row_count=len(bounded_page.rows),
                backend_used="clickhouse",
                query_time_ms=bounded_page.elapsed_ms,
            )
        elif not bounded_error_code:
            query, params = builder.build()
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=read_deadline.remaining_ms(SPAN_LIST_CANDIDATE_DEADLINE_MS),
            )
        else:
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Filtered span data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

            # Prefix-dedup pagination: Phase 1 dropped `LIMIT 1 BY id` (its
            # O(window) full sort OOM-crashed CH — see SpanListQueryBuilder.build)
            # and instead fetched the sorted prefix [0, offset + 2*page_size).
            # De-dup the prefix by span id and slice the page — every page is a
            # disjoint slice of the same globally de-duplicated stream, so a span
            # can never appear on two pages and none is skipped. See page_dedup.py.
            result.data, _has_more = paginate_deduped(
                result.data,
                ("project_id", "trace_id", "id"),
                page_number,
                page_size,
            )

        # Phase 1b: Fetch input/output for the page
        span_ids = [str(row.get("id", "")) for row in result.data if row.get("id")]
        span_identities, span_entities, app_identity_by_external = (
            _span_page_identity_sets(
                result.data,
                default_project_id=project_id,
            )
        )
        if span_identities:
            content_query, content_params = builder.build_content_query(
                span_ids, span_identities=span_identities
            )
            if content_query:
                content_result = analytics.execute_ch_query(
                    content_query,
                    content_params,
                    timeout_ms=read_deadline.remaining_ms(
                        SPAN_LIST_ENRICHMENT_TIMEOUT_MS
                    ),
                )
                merge_content_rows(
                    result.data,
                    content_result.data,
                    id_key=("project_id", "trace_id", "id", "start_time"),
                    keys=("input", "output"),
                )

        # A bounded filtered read intentionally reports the proven lower bound;
        # re-running the old full-window count would reintroduce the timeout the
        # bounded path avoids. Unfiltered reads retain their exact count.
        if bounded_page is not None:
            total_count = bounded_page.total_rows_lower_bound
        else:
            count_query, count_params = builder.build_count_query()
            count_result = analytics.execute_ch_query(
                count_query,
                count_params,
                timeout_ms=read_deadline.remaining_ms(SPAN_LIST_ENRICHMENT_TIMEOUT_MS),
            )
            total_count = (
                count_result.data[0].get("total", 0) if count_result.data else 0
            )

        # Phase 2: Eval scores
        eval_map = {}
        if span_entities and eval_config_ids:
            eval_query, eval_params = builder.build_eval_query(
                span_ids, span_entities=span_entities
            )
            if eval_query:
                eval_result = analytics.execute_ch_query(
                    eval_query,
                    eval_params,
                    timeout_ms=read_deadline.remaining_ms(5_000),
                )
                external_map = SpanListQueryBuilder.pivot_eval_results(
                    eval_result.data, key_by_trace=True
                )
                eval_map = {
                    app_identity_by_external[external_key]: values
                    for external_key, values in external_map.items()
                    if external_key in app_identity_by_external
                }

        # Phase 3: Annotations
        annotation_map = {}
        if span_entities and annotation_label_ids:
            ann_query, ann_params = builder.build_annotation_query(
                span_ids, span_entities=span_entities
            )
            if ann_query:
                ann_result = analytics.execute_ch_query(
                    ann_query,
                    ann_params,
                    timeout_ms=read_deadline.remaining_ms(5_000),
                )
                external_map = SpanListQueryBuilder.pivot_annotation_results(
                    ann_result.data, label_types, key_by_trace=True
                )
                annotation_map = {
                    app_identity_by_external[external_key]: values
                    for external_key, values in external_map.items()
                    if external_key in app_identity_by_external
                }

        # Build column config
        column_config = get_default_span_config()
        column_config = update_column_config_based_on_eval_config(
            column_config, eval_configs
        )
        column_config = update_span_column_config_based_on_annotations(
            column_config, annotation_labels
        )

        # Format response matching PG format
        table_data = []
        for row in result.data:
            span_id = str(row.get("id", ""))
            span_entity = (
                str(row.get("project_id", "")),
                str(row.get("trace_id", "")),
                span_id,
            )
            entry = {
                "project_id": str(row.get("project_id", "")),
                "node_type": row.get("observation_type", ""),
                "span_id": span_id,
                "input": row.get("input", ""),
                "output": row.get("output", ""),
                "trace_id": str(row.get("trace_id", "")),
                "span_name": row.get("name", ""),
                "start_time": row.get("start_time"),
                "status": row.get("status"),
            }

            # Add eval metrics
            span_evals = eval_map.get(span_entity, {})
            for config in eval_configs:
                config_id = str(config.id)
                if config_id not in span_evals:
                    continue
                val = span_evals[config_id]
                if isinstance(val, dict) and (
                    isinstance(val.get("status"), str) or val.get("error")
                ):
                    # Lifecycle marker — loading/pending/skipped or errored.
                    entry[config_id] = val
                elif (
                    isinstance(val, dict)
                    and not val.get("error")
                    and not val.get("score")
                    and val
                ):
                    for choice, pct in val.items():
                        entry[f"{config_id}**{choice}"] = pct
                elif isinstance(val, dict):
                    entry[config_id] = val.get("score")
                else:
                    entry[config_id] = val

            # Add annotations
            span_annotations = annotation_map.get(span_entity, {})
            for label in annotation_labels:
                label_id = str(label.id)
                if label_id in span_annotations:
                    entry[label_id] = span_annotations[label_id]

            table_data.append(entry)

        metadata = {"total_rows": total_count}
        if bounded_page is not None:
            metadata.update(
                {
                    "total_rows_is_lower_bound": True,
                    "has_more": bounded_page.has_more,
                    "query_complete": bounded_page.complete,
                    "query_status": bounded_page.status,
                    "query_error_code": bounded_page.error_code,
                    "query_elapsed_ms": round(bounded_page.elapsed_ms, 3),
                    "query_count": bounded_page.query_count,
                    "query_rows_returned": bounded_page.rows_returned,
                    "query_result_payload_bytes": bounded_page.result_payload_bytes,
                }
            )
        if metadata.get(
            "total_rows_is_lower_bound"
        ) and exact_total_explicitly_required(request, validated_data):
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Span data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

        response = {
            "column_config": column_config,
            "metadata": metadata,
            "table": table_data,
        }

        return self._gm.success_response(response)

    @bounded_graph_action_request(resource="span_graph")
    @validated_request(
        query_serializer=ObserveGraphDataQuerySerializer,
        request_serializer=ObserveGraphDataRequestSerializer,
        responses={
            200: ObserveGraphDataResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["post"])
    def get_graph_methods(self, request, *args, **kwargs):
        """
        Fetch data for the observe graph with optimized queries
        """
        deadline = kwargs.pop("_graph_action_deadline", None)
        deadline = deadline or start_graph_action_deadline()

        def finish(response):
            return finish_graph_action_response(deadline, response)

        try:
            body = request.validated_data
            refresh = request.validated_query_data.get("refresh", False)
            project_id = str(body["project_id"])

            try:
                with graph_action_postgres_budget(deadline):
                    project = Project.objects.get(
                        _project_workspace_scope_q(self.request, project_prefix=""),
                        id=project_id,
                        organization=_get_request_organization(request),
                    )
            except Project.DoesNotExist:
                return finish(
                    self._gm.bad_request("Project not found or access denied")
                )
            if project.trace_type != "observe":
                return finish(self._gm.bad_request("Project should be of type observe"))

            filters = bind_request_my_annotations_principal(
                request,
                body["filters"],
            )
            filters = graph_execution_filters(filters)
            _property = body["property"]
            interval = body["interval"]
            req_data_config = body["req_data_config"]
            try:
                validate_property_graph_namespace(
                    req_data_config.get("property_id"),
                    expected_definition_source="spans",
                )
            except ValueError:
                return finish(
                    self._gm.bad_request(
                        "property_id is not valid for this graph endpoint"
                    )
                )

            metric_type = req_data_config.get("type", None)
            if metric_type not in ["EVAL", "ANNOTATION", "SYSTEM_METRIC"]:
                return finish(self._gm.bad_request("Filter property type is not valid"))
            metric_id = req_data_config.get("id", "latency")
            # PostgreSQL remains authoritative for small config metadata and
            # authorization only. Telemetry still comes exclusively from CH25.
            if metric_type == "EVAL":
                with graph_action_postgres_budget(deadline):
                    eval_config_available = CustomEvalConfig.objects.filter(
                        id=metric_id,
                        project_id=project_id,
                        deleted=False,
                    ).exists()
                if not eval_config_available:
                    return finish(
                        self._gm.bad_request(
                            "Evaluation config is not available for this project"
                        )
                    )

            # CH-only path post-migration. D-027: the previous PG fallback
            # (ObservationSpan.objects.filter + per-config eval-metric
            # annotations + Score subqueries + Python pivot, ~270 LOC) was
            # deleted. SPAN_GRAPH is served by the three CH helpers
            # (fetch_system_metric_graph_ch / fetch_eval_graph_ch /
            # fetch_annotation_graph_ch).
            # Spans/traces are direct-write CH25 only. Use the process-wide V2
            # pool and return a typed unavailable response on bounded read
            # failure; never rebuild telemetry from stale PostgreSQL rows.
            analytics = V2AnalyticsQueryService()
            workspace_id = getattr(getattr(request, "workspace", None), "id", None)
            try:
                if metric_type == "SYSTEM_METRIC":
                    graph = fetch_system_metric_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        metric_id=metric_id,
                        observe_type="span",
                        refresh=refresh,
                        organization_id=(
                            str(project.organization_id)
                            if getattr(project, "organization_id", None)
                            else None
                        ),
                        workspace_id=str(workspace_id) if workspace_id else None,
                        timeout_ms=graph_action_remaining_ms(deadline),
                    )
                elif metric_type == "EVAL":
                    graph = fetch_eval_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        req_data_config=req_data_config,
                        observe_type="span",
                        refresh=refresh,
                        organization_id=(
                            str(project.organization_id)
                            if getattr(project, "organization_id", None)
                            else None
                        ),
                        workspace_id=str(workspace_id) if workspace_id else None,
                        timeout_ms=graph_action_remaining_ms(deadline),
                    )
                else:
                    graph = fetch_annotation_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        req_data_config=req_data_config,
                        observe_type="span",
                        refresh=refresh,
                        organization_id=(
                            str(project.organization_id)
                            if getattr(project, "organization_id", None)
                            else None
                        ),
                        workspace_id=str(workspace_id) if workspace_id else None,
                        timeout_ms=graph_action_remaining_ms(deadline),
                    )
                graph.update(
                    graph_query_evidence(
                        project_id=project_id,
                        observe_type="span",
                        filters=filters,
                    )
                )
                graph = enforce_exact_graph_data_contract(graph)
                if not graph_payload_is_publishable(
                    graph,
                    allow_sampled=False,
                ):
                    return finish(
                        self._gm.custom_error_response(
                            status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Graph data is temporarily unavailable. Please retry.",
                            code="service_unavailable",
                        )
                    )
                return finish(self._gm.success_response(graph))
            except Exception as exc:
                if not (
                    isinstance(exc, BoundedGraphReadError)
                    or is_clickhouse_api_read_unavailable_error(exc)
                ):
                    # A programming defect is not a successful degraded graph.
                    # Re-raise into the outer sanitized handler, which records
                    # the traceback without exposing it in the API response.
                    raise
                logger.warning(
                    "span_graph_query_unavailable",
                    project_id=project_id,
                    metric_type=metric_type,
                    metric_id=metric_id,
                    error_type=type(exc).__name__,
                )
                return finish(
                    self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Graph data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                )

        except GraphActionUnavailable:
            logger.warning(
                "span_graph_action_deadline_exceeded",
                project_id=str(
                    getattr(request, "validated_data", {}).get("project_id", "")
                ),
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Graph data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except UnsupportedFilterShapeError:
            return self._gm.bad_request("Graph filter configuration is invalid")
        except FilterPrincipalContextError as exc:
            return self._gm.bad_request(str(exc))
        except Exception as exc:
            logger.exception(
                "span_graph_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Graph data could not be loaded",
                code="server_error",
            )

    @validated_request(
        query_serializer=ObservationAttributeListQuerySerializer,
        responses={
            200: ObservationAttributeListResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def get_span_attributes_list(self, request, *args, **kwargs):
        """Distinct span_attributes keys for a project (spans surface).

        Query params:
            filters: JSON {"project_id": "<uuid>"} (required)

        Returns:
            List of attribute key strings.
        """
        project_id = request.validated_query_data["filters"]["project_id"]
        exact_key = request.validated_query_data.get("q")
        if not self._attribute_project_for_request(request, project_id):
            return self._gm.not_found("Project not found")

        selector = AttributeReadSelector(
            typed_only=True,
            json_attribute_mode="structured",
        )
        try:
            keys, metadata = self._get_span_attribute_inventory(
                project_id,
                selector=selector,
                exact_key=exact_key,
            )
            if not self._attribute_metadata_allows_success(
                metadata,
                has_verified_results=bool(keys),
                allow_empty_sample=exact_key is not None,
            ):
                return self._attribute_read_unavailable_response()
            return self._attribute_list_response(keys, metadata)
        except (InvalidAttributeKey, InvalidAttributeSearch):
            return self._gm.bad_request("Attribute filter configuration is invalid")
        except Exception as exc:
            if isinstance(exc, IncompleteLatestStateReplay) or (
                is_clickhouse_api_read_unavailable_error(exc)
            ):
                logger.warning(
                    "span_attribute_discovery_unavailable",
                    project_id=str(project_id),
                    error_type=type(exc).__name__,
                )
                return self._attribute_read_unavailable_response()
            logger.exception(
                "span_attribute_discovery_failed",
                project_id=str(project_id),
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Span attributes could not be loaded",
                code="server_error",
            )

    @validated_request(
        query_serializer=ObservationAttributeListQuerySerializer,
        responses={
            200: ObservationAttributeListResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def get_eval_attributes_list(self, request, *args, **kwargs):
        """Attribute paths the EvalPicker exposes per row_type.

        Query params:
            filters: JSON {"project_id": "<uuid>"} (required)
            row_type: spans | traces | sessions (default spans;
                      voiceCalls aliases to spans)

        Returns:
            spans/voiceCalls: distinct span_attributes keys
            traces:           trace fields + spans.<n>.<key>
            sessions:         session fields + traces.<i>.<trace_field>
                              + traces.<i>.spans.<j>.<key>

        Indexed positions are sized to the project's observed maxes;
        ordering of ``traces.<i>`` / ``spans.<n>`` slots is decided at
        resolve time (see ``_resolve_session_path`` / ``_resolve_trace_path``).
        """
        project_id = request.validated_query_data["filters"]["project_id"]
        row_type = request.validated_query_data["row_type"]
        exact_key = request.validated_query_data.get("q")
        if not self._attribute_project_for_request(request, project_id):
            return self._gm.not_found("Project not found")

        selector = AttributeReadSelector(
            typed_only=True,
            # Eval field mapping can resolve arbitrary JSON values.  Unlike a
            # filter picker it needs the key even when the value is an object,
            # null, or a JSON-only scalar.
            json_attribute_mode="all",
        )
        try:
            span_attribute_keys, discovery_metadata = (
                self._get_span_attribute_inventory(
                    project_id,
                    selector=selector,
                    exact_key=exact_key,
                )
            )
            if not self._attribute_metadata_allows_success(
                discovery_metadata,
                has_verified_results=bool(span_attribute_keys),
                allow_empty_sample=exact_key is not None,
            ):
                return self._attribute_read_unavailable_response()
            if row_type in ("spans", "voiceCalls"):
                return self._attribute_list_response(
                    span_attribute_keys, discovery_metadata
                )

            cardinality = selector.sample_cardinality(
                [project_id],
                # Trace pickers only need spans-per-trace and must not pay for
                # the targeted session lane. Session pickers explicitly opt in.
                ensure_session_sample=row_type == "sessions",
            )
            cardinality_has_verified_results = cardinality.max_spans_per_trace > 0 and (
                row_type != "sessions" or cardinality.max_traces_per_session > 0
            )
            if not self._attribute_metadata_allows_success(
                cardinality.metadata,
                has_verified_results=cardinality_has_verified_results,
                # A project with no verified session-bearing spans still has
                # valid static session fields (name/bookmarked). Publish those
                # with explicit sample coverage and zero invented
                # ``traces.<n>`` positions. Resource/deadline failures remain
                # degraded and continue to return the sanitized 503 below.
                allow_empty_sample=row_type == "sessions",
            ):
                return self._attribute_read_unavailable_response()
            metadata = merge_read_metadata(discovery_metadata, cardinality.metadata)
            if row_type == "traces":
                paths = self._build_trace_attribute_paths(
                    project_id,
                    span_attribute_keys,
                    max_spans=cardinality.max_spans_per_trace,
                )
                return self._attribute_list_response(paths, metadata)

            if row_type == "sessions":
                paths = self._build_session_attribute_paths(
                    project_id,
                    span_attribute_keys,
                    max_traces=cardinality.max_traces_per_session,
                    max_spans=cardinality.max_spans_per_trace,
                )
                return self._attribute_list_response(paths, metadata)

            return self._gm.bad_request("Unknown row type")
        except (InvalidAttributeKey, InvalidAttributeSearch):
            return self._gm.bad_request("Attribute filter configuration is invalid")
        except Exception as exc:
            if isinstance(exc, IncompleteLatestStateReplay) or (
                is_clickhouse_api_read_unavailable_error(exc)
            ):
                logger.warning(
                    "evaluation_attribute_discovery_unavailable",
                    project_id=str(project_id),
                    row_type=row_type,
                    error_type=type(exc).__name__,
                )
                return self._attribute_read_unavailable_response()
            logger.exception(
                "evaluation_attribute_discovery_failed",
                project_id=str(project_id),
                row_type=row_type,
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Evaluation attributes could not be loaded",
                code="server_error",
            )

    # Trace + session model fields the resolver allow-lists; mirrors the
    # frozensets in tracer.utils.eval. Hand-synced so a model change shows
    # up in both places at review time.
    _TRACE_PUBLIC_FIELDS = (
        "input",
        "output",
        "name",
        "error",
        "tags",
        "metadata",
        "external_id",
    )
    _SESSION_PUBLIC_FIELDS = ("name", "bookmarked")

    # Cap on how many entities to scan when computing observed maxes.
    # Most projects' traces have a few-to-dozens of spans; bounding the
    # sample keeps the path enumeration query cheap.
    _OBSERVED_MAX_SAMPLE_SIZE = 100

    @staticmethod
    def _attribute_metadata_allows_success(
        metadata: AttributeReadMetadata,
        *,
        has_verified_results: bool,
        allow_empty_sample: bool = False,
    ) -> bool:
        """Accept exact reads or an explicitly labelled finite-cap sample only."""

        return metadata.query_complete or (
            (has_verified_results or allow_empty_sample)
            and metadata.query_status == "sampled"
            and metadata.query_error_code == "sample_limit"
        )

    @staticmethod
    def _attribute_read_unavailable_response():
        return GeneralMethods().custom_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Attribute values are temporarily unavailable. Please retry.",
            code="service_unavailable",
        )

    @staticmethod
    def _attribute_list_response(paths: list[str], metadata: AttributeReadMetadata):
        response = GeneralMethods().success_response(paths)
        response.data.update(metadata.public_payload())
        return response

    @staticmethod
    def _attribute_project_for_request(request, project_id: str):
        project_manager = getattr(Project, "no_workspace_objects", Project.objects)
        return (
            project_manager.filter(
                _project_workspace_scope_q(request, project_prefix=""),
                id=project_id,
                organization=_get_request_organization(request),
                deleted=False,
            )
            .only("id")
            .first()
        )

    @staticmethod
    def _get_span_attribute_inventory(
        project_id: str,
        *,
        selector: AttributeReadSelector,
        exact_key: str | None,
    ) -> tuple[list[str], AttributeReadMetadata]:
        if exact_key is None:
            # Generic pickers need a useful bounded inventory, not a seven-day
            # first statement that can exceed the selector's 500k-row ceiling
            # on dense voice projects. The selector already defines six hours
            # as its production-safe dense segment. Prefer that frozen recent
            # sample; only sparse/empty projects pay for the existing adaptive
            # one-year search below.
            window_end = selector.query_window_end
            recent_read = selector.discover_keys(
                [project_id],
                window_start=window_end - ATTRIBUTE_READ_EXPLICIT_SEGMENT,
                window_end=window_end,
            )
            recent_metadata = recent_read.metadata
            recent_read_is_publishable = recent_metadata.query_complete or (
                recent_metadata.query_status == "sampled"
                and recent_metadata.query_error_code == "sample_limit"
            )
            if recent_read.rows and recent_read_is_publishable:
                # Even an exact six-hour result is only a sample of the
                # endpoint's historical inventory. Keep that coverage honest
                # instead of presenting it as a complete all-time key list.
                sampled_metadata = replace(
                    recent_metadata,
                    query_complete=False,
                    query_status="sampled",
                    query_error_code="sample_limit",
                )
                return [row.key for row in recent_read.rows], sampled_metadata

        read = selector.discover_keys([project_id], exact_key=exact_key)
        return [row.key for row in read.rows], read.metadata

    def _get_span_attribute_keys(self, project_id: str) -> list:
        """Project's distinct span_attributes keys, sourced from CH.

        Single source for both ``get_span_attributes_list`` (which wraps
        it in a DRF response) and the trace + session path builders.

        CH returns ``[{"key": ..., "type": ...}, ...]`` (spans picker
        renders type chips); the trace + session path builders need
        bare strings. The normalization loop below collapses both
        shapes to ``list[str]`` so callers never see dicts f-stringed
        into paths like ``traces.0.spans.0.{'key': '...', ...}``.

        CH25 close-out (2026-05-26): PG fallback removed alongside the
        routing toggle. Span attribute keys come from the CH ``attrs_*``
        typed-Map indexes (the authoritative inventory).
        """
        analytics = AnalyticsQueryService()
        raw = analytics.get_span_attribute_keys_ch(str(project_id))

        keys = []
        for item in raw or []:
            if isinstance(item, dict):
                k = item.get("key")
                if k:
                    keys.append(k)
            elif isinstance(item, str) and item:
                keys.append(item)
        return keys

    def _max_spans_per_trace(self, project_id: str) -> int:
        """Bounded CH25-only span cardinality sample for legacy callers."""

        return (
            AttributeReadSelector()
            .sample_cardinality([project_id], ensure_session_sample=False)
            .max_spans_per_trace
        )

    def _max_traces_per_session(self, project_id: str) -> int:
        """Bounded CH25-only trace cardinality sample for legacy callers."""

        return (
            AttributeReadSelector()
            .sample_cardinality([project_id])
            .max_traces_per_session
        )

    _SPAN_PUBLIC_FIELDS = (
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost",
        "response_time",
        "model",
        "name",
        "observation_type",
        "status",
        "status_message",
        "provider",
    )

    def _build_trace_attribute_paths(
        self,
        project_id: str,
        span_attribute_keys: list,
        *,
        max_spans: int | None = None,
    ) -> list:
        """Trace-level paths: trace fields + ``spans.<n>.<key>`` for each
        index up to the observed max spans-per-trace."""
        paths = list(self._TRACE_PUBLIC_FIELDS)
        if max_spans is None:
            max_spans = self._max_spans_per_trace(project_id)
        for i in range(max_spans):
            for field in self._SPAN_PUBLIC_FIELDS:
                paths.append(f"spans.{i}.{field}")
            for key in span_attribute_keys:
                paths.append(f"spans.{i}.{key}")
        return paths

    def _build_session_attribute_paths(
        self,
        project_id: str,
        span_attribute_keys: list,
        *,
        max_traces: int | None = None,
        max_spans: int | None = None,
    ) -> list:
        """Session-level paths: session fields + ``traces.<i>.<trace_field>``
        + ``traces.<i>.spans.<j>.<key>`` up to the observed max traces-per-
        session and spans-per-trace."""
        paths = list(self._SESSION_PUBLIC_FIELDS)
        if max_traces is None:
            max_traces = self._max_traces_per_session(project_id)
        if max_spans is None:
            max_spans = self._max_spans_per_trace(project_id)
        for i in range(max_traces):
            for trace_field in self._TRACE_PUBLIC_FIELDS:
                paths.append(f"traces.{i}.{trace_field}")
            for j in range(max_spans):
                for field in self._SPAN_PUBLIC_FIELDS:
                    paths.append(f"traces.{i}.spans.{j}.{field}")
                for key in span_attribute_keys:
                    paths.append(f"traces.{i}.spans.{j}.{key}")
        return paths

    @action(detail=False, methods=["get"])
    def get_observation_span_fields(self, request, *args, **kwargs):
        try:
            # Get fields from observation span model
            fields = []
            for field in ObservationSpan._meta.get_fields():
                field_type = field.get_internal_type()

                # Map Django field types to DataTypeChoices
                if field_type == "JSONField":
                    field_type = DataTypeChoices.JSON.value
                elif field_type == "CharField" or field_type == "TextField":
                    field_type = DataTypeChoices.TEXT.value
                elif field_type == "BooleanField":
                    field_type = DataTypeChoices.BOOLEAN.value
                elif field_type == "IntegerField":
                    field_type = DataTypeChoices.INTEGER.value
                elif field_type == "FloatField" or field_type == "DecimalField":
                    field_type = DataTypeChoices.FLOAT.value
                elif field_type == "ArrayField":
                    field_type = DataTypeChoices.ARRAY.value
                elif field_type == "DateTimeField":
                    field_type = DataTypeChoices.DATETIME.value
                else:
                    field_type = DataTypeChoices.OTHERS.value

                fields.append({"name": field.name, "type": field_type})

            # Add virtual field for child spans (not a model field)
            fields.append({"name": "child_spans", "type": DataTypeChoices.JSON.value})

            return self._gm.success_response(fields)

        except Exception as exc:
            logger.exception(
                "observation_span_fields_failed", error_type=type(exc).__name__
            )
            return self._gm.bad_request("Observation span fields could not be loaded")

    def _get_evaluation_details_clickhouse(
        self,
        observation_span_id,
        custom_eval_config_id,
        project_id,
        analytics,
    ):
        """Get evaluation details from ClickHouse."""
        # Span- and trace-target rows both anchor to observation_span_id;
        # session rows don't and are served by /trace-session/:id/eval_logs/.
        row = analytics.get_eval_detail_ch(
            observation_span_id,
            custom_eval_config_id,
            project_id=project_id,
        )
        if not row:
            return self._gm.bad_request(
                "No eval logger found for the given observation span id and custom eval config id"
            )

        output_metadata = row.get("output_metadata")
        if not output_metadata or not isinstance(output_metadata, dict):
            output_metadata = {}

        # Handle error case — consistent with retrieve() and _retrieve_clickhouse()
        if row.get("error") or row.get("output_str") == "ERROR":
            return self._gm.success_response(
                {
                    "error_analysis": output_metadata.get("error_analysis"),
                    "selected_input_key": output_metadata.get("selected_input_key"),
                    "input_data": output_metadata.get("input_data"),
                    "input_types": output_metadata.get("input_types"),
                    "score": None,
                    "explanation": row.get("error_message"),
                    "error": True,
                }
            )

        evaluation_result = (
            row.get("output_bool")
            if row.get("output_bool") is not None
            else (
                row.get("output_float")
                if row.get("output_float") is not None
                else row.get("output_str_list")
            )
        )
        evaluation_explanation = (
            row.get("eval_explanation")
            if row.get("eval_explanation")
            else row.get("error_message")
        )

        return self._gm.success_response(
            {
                "error_analysis": output_metadata.get("error_analysis"),
                "selected_input_key": output_metadata.get("selected_input_key"),
                "input_data": output_metadata.get("input_data"),
                "input_types": output_metadata.get("input_types"),
                "score": evaluation_result,
                "explanation": evaluation_explanation,
            }
        )

    @validated_request(
        responses={
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        }
    )
    @action(detail=False, methods=["get"])
    def get_evaluation_details(self, request, *args, **kwargs):
        observation_span_id = None
        try:
            observation_span_id = self.request.query_params.get(
                "observation_span_id", None
            )
            custom_eval_config_id = self.request.query_params.get(
                "custom_eval_config_id", None
            )

            if not observation_span_id or not custom_eval_config_id:
                return self._gm.bad_request(
                    "Observation span id and custom eval config id are required"
                )

            # The eval table has no project column.  Authorize the config through
            # the request's organization/workspace project scope before any CH
            # telemetry read, then pass that project anchor into the CH selector.
            # Use one generic absence response so foreign config UUIDs do not
            # become an existence oracle.
            config_scope = (
                CustomEvalConfig.no_workspace_objects.filter(
                    _project_workspace_scope_q(self.request),
                    id=custom_eval_config_id,
                    project__organization=_get_request_organization(self.request),
                    deleted=False,
                )
                .values("project_id")
                .first()
            )
            if config_scope is None:
                return self._gm.bad_request(
                    "No eval logger found for the given observation span id and custom eval config id"
                )
            project_id = str(config_scope["project_id"])

            # CH25-only path. V2AnalyticsQueryService keeps the direct CH25
            # connection while honoring the independently configured eval
            # table name.
            analytics = V2AnalyticsQueryService()
            return self._get_evaluation_details_clickhouse(
                observation_span_id,
                custom_eval_config_id,
                project_id,
                analytics,
            )

        except Exception as exc:
            if is_clickhouse_api_read_unavailable_error(exc):
                logger.warning(
                    "evaluation_detail_query_unavailable",
                    span_id=str(observation_span_id or ""),
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Evaluation details are temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "evaluation_detail_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Evaluation details could not be loaded",
                code="server_error",
            )

    @validated_request(
        query_serializer=SpanExportQuerySerializer,
        responses={
            200: openapi.Response(
                "Bounded CSV export; a terminal comment row discloses truncation.",
                schema=openapi.Schema(type=openapi.TYPE_STRING),
            ),
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
        produces=["text/csv"],
    )
    @action(detail=False, methods=["get"])
    def get_spans_export_data(self, request, *args, **kwargs):
        try:
            validated_data = request.validated_query_data
            project_id = str(validated_data["project_id"])
            project = Project.objects.filter(
                _project_workspace_scope_q(request, project_prefix=""),
                id=project_id,
                organization=_get_request_organization(request),
            ).first()
            if not project:
                return self._gm.bad_request("Project not found")

            page_response = self.list_spans_observe(request, bounded_export=True)
            if page_response.status_code != status.HTTP_200_OK:
                return page_response
            result = page_response.data.get("result", {})
            return bounded_page_csv_response(
                rows=result.get("table"),
                metadata=result.get("metadata"),
                filename=f"{project.name or 'project'}_spans.csv",
            )

        except Exception as exc:
            logger.exception(
                "observation_span_export_failed", error_type=type(exc).__name__
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Span export could not be generated",
                code="server_error",
            )

    @validated_request(request_serializer=AddObservationSpanAnnotationsSerializer)
    @action(detail=False, methods=["post"])
    def add_annotations(self, request, *args, **kwargs):
        try:
            data = request.validated_data
            observation_span_id = data.get("observation_span_id")
            annotation_values = data.get("annotation_values")
            trace_id = data.get("trace_id")
            notes = data.get("notes")

            if (not observation_span_id and not trace_id) or not annotation_values:
                raise Exception(
                    "Observation span id and annotation values are required"
                )

            try:
                if observation_span_id:
                    observation_span = ObservationSpan.objects.get(
                        _project_workspace_scope_q(request),
                        id=observation_span_id,
                        project__organization=_get_request_organization(request),
                    )
                elif trace_id:
                    observation_span = ObservationSpan.objects.get(
                        _project_workspace_scope_q(request),
                        trace_id=trace_id,
                        project__organization=_get_request_organization(request),
                        parent_span_id__isnull=True,
                    )
            except ObservationSpan.DoesNotExist:
                raise Exception("Observation span not found")  # noqa: B904

            failed_labels = []
            success_labels = []
            for label_id, given_annotation_value in annotation_values.items():
                try:
                    try:
                        annotation_label = AnnotationsLabels.objects.get(
                            id=label_id,
                            organization=getattr(request, "organization", None)
                            or request.user.organization,
                        )
                    except AnnotationsLabels.DoesNotExist:
                        raise Exception("Annotation label not found")  # noqa: B904

                    annotation_type = annotation_label.type

                    # Validate annotation value against label type and settings
                    from tracer.utils.annotation_validation import (
                        validate_annotation_value as validate_ann_value,
                    )

                    validation_error = _validate_add_annotation_value(
                        validate_ann_value,
                        annotation_type,
                        annotation_label.settings,
                        given_annotation_value,
                    )
                    if validation_error:
                        failed_labels.append(label_id)
                        continue

                    score_value = _to_score_value(
                        annotation_type, given_annotation_value
                    )

                    # Write to unified Score model.
                    # Use no_workspace_objects + _id fields to avoid the
                    # LEFT JOIN on nullable workspace FK that triggers
                    # PostgreSQL's "FOR UPDATE cannot be applied to the
                    # nullable side of an outer join".
                    #
                    # Resolve a default queue item up-front so the upsert
                    # lookup keys on queue_item — the per-queue Score
                    # uniqueness ``(source, label, annotator, queue_item)``
                    # would otherwise produce duplicate orphan rows on
                    # repeated writes from this legacy endpoint. Falls
                    # back to NULL if the source has no resolvable scope
                    # (rare, e.g. orphaned span).
                    from model_hub.utils.annotation_queue_helpers import (
                        resolve_default_queue_item_for_source,
                        tracer_project_id_for_source,
                    )

                    default_item = resolve_default_queue_item_for_source(
                        "observation_span",
                        observation_span,
                        request.user.organization,
                        request.user,
                    )
                    if default_item is None:
                        # Per-queue Score uniqueness requires a queue_item.
                        # Skip rather than insert with queue_item=NULL —
                        # NULL ≠ NULL in Postgres, so a silent orphan
                        # insert could accumulate duplicates the on_commit
                        # auto-attach hook can no longer migrate safely.
                        failed_labels.append(label_id)
                        logger.warning(
                            "score_skip_no_default_queue_scope",
                            source_type="observation_span",
                            source_id=str(observation_span.pk),
                            label_id=str(annotation_label.pk),
                        )
                        continue
                    tracer_project_id = tracer_project_id_for_source(
                        "observation_span", observation_span
                    )
                    score, _ = Score.no_workspace_objects.update_or_create(
                        observation_span_id=observation_span.pk,
                        label_id=annotation_label.pk,
                        annotator_id=request.user.pk,
                        queue_item=default_item,
                        deleted=False,
                        defaults={
                            "source_type": "observation_span",
                            "value": score_value,
                            "score_source": "human",
                            "notes": notes or "",
                            "organization": request.user.organization,
                            **(
                                {"tracer_project_id": tracer_project_id}
                                if tracer_project_id
                                else {}
                            ),
                        },
                    )
                    if notes is not None:
                        from model_hub.models.annotation_queues import QueueItemNote

                        if notes:
                            QueueItemNote.no_workspace_objects.update_or_create(
                                queue_item=default_item,
                                annotator=request.user,
                                deleted=False,
                                defaults={
                                    "notes": notes,
                                    "organization": request.user.organization,
                                    "workspace": getattr(request, "workspace", None)
                                    or default_item.workspace,
                                },
                            )
                        else:
                            QueueItemNote.no_workspace_objects.filter(
                                queue_item=default_item,
                                annotator=request.user,
                                deleted=False,
                            ).update(deleted=True, deleted_at=timezone.now())

                    success_labels.append(label_id)

                    # update projectversion annotations

                    if observation_span.project_version is not None:
                        annotation = observation_span.project_version.annotations
                        if annotation is not None:
                            annotation.labels.add(annotation_label)
                            annotation.save()
                        else:
                            annotation = Annotations.objects.create(
                                organization=getattr(request, "organization", None)
                                or request.user.organization,
                                name=f"Annotation for {observation_span.project_version.name}",
                            )
                            annotation.labels.add(annotation_label)
                            observation_span.project_version.annotations = annotation
                            observation_span.project_version.save()
                except AnnotationsLabels.DoesNotExist:
                    failed_labels.append(label_id)

            # Auto-create queue items for default queues and auto-complete (bidirectional sync)
            if success_labels:
                try:
                    _auto_create_queue_items_for_default_queues(
                        "observation_span", observation_span, success_labels
                    )
                except Exception:
                    logger.exception(
                        "Error in auto-creating queue items for default queues"
                    )
                try:
                    _auto_complete_queue_items(
                        "observation_span", observation_span, request.user
                    )
                except Exception:
                    logger.exception("Error in auto-completing queue items")

            if notes:
                try:
                    span_note = SpanNotes.objects.get(
                        span=observation_span, created_by_user=request.user
                    )
                    span_note.notes = notes
                    span_note.save(update_fields=["notes"])
                except SpanNotes.DoesNotExist:
                    SpanNotes.objects.create(
                        span=observation_span,
                        notes=notes,
                        created_by_user=request.user,
                        created_by_annotator=str(request.user.id),
                    )

            return self._gm.success_response(
                {
                    "id": str(observation_span.id),
                    "failed_labels": failed_labels,
                    "success_labels": success_labels,
                }
            )
        except Exception as e:
            logger.exception(f"Error in adding annotations: {str(e)}")

            return self._gm.bad_request(
                f"Error adding annotations: {get_error_message('FAILED_TO_ADD_ANNOTATIONS')}"
            )

    @action(detail=False, methods=["delete"])
    def delete_annotation_label(self, request, *args, **kwargs):
        try:
            label_id = self.request.query_params.get("label_id")
            if not label_id:
                return self._gm.bad_request("label_id query parameter is required")
            label = AnnotationsLabels.objects.get(
                _project_workspace_scope_q(request, project_prefix=""),
                id=label_id,
                organization=_get_request_organization(request),
            )
            # Check if label is in use by active annotation tasks
            if Annotations.objects.filter(labels=label_id, deleted=False).exists():
                return self._gm.bad_request(
                    "Cannot delete label: it is in use by active annotation tasks"
                )
            label.delete()
            Score.objects.filter(
                label_id=label_id, organization=_get_request_organization(request)
            ).update(deleted=True)

            return self._gm.success_response(
                {"message": "Annotation label deleted successfully"}
            )
        except AnnotationsLabels.DoesNotExist:
            return self._gm.bad_request("Annotation label not found")
        except Exception:
            logger.exception("Error deleting annotation label")
            return self._gm.bad_request(
                "Unable to delete the annotation label. Please try again."
            )

    def _bounded_span_navigation_response(
        self,
        *,
        project_id,
        span_id,
        filters,
        project_version_id=None,
        end_user_id=None,
    ):
        """Return adjacent trace ids from the exact bounded span-list order."""

        from tracer.selectors.trace_filter_reads import read_bounded_filter_neighbors
        from tracer.services.clickhouse.v2.query_builders.span_list import (
            SpanListQueryBuilderV2,
        )

        builder = SpanListQueryBuilderV2(
            project_id=str(project_id),
            page_number=0,
            page_size=SPAN_NAVIGATION_CANDIDATE_LIMIT,
            filters=list(filters or []),
            project_version_id=(
                str(project_version_id) if project_version_id is not None else None
            ),
            end_user_id=str(end_user_id) if end_user_id is not None else None,
            bounded_internal_scan=True,
            bounded_identity_only=True,
        )
        error_code = builder.bounded_filter_degraded_error_code()
        if error_code or not builder.supports_bounded_filter_scan():
            raise SpanNavigationReadUnavailable(
                error_code or "unsupported_filter_shape"
            )
        neighbors = read_bounded_filter_neighbors(
            builder=builder,
            analytics=V2AnalyticsQueryService(),
            filters=list(filters or []),
            key_field="id",
            target_id=str(span_id),
            scan_limit=SPAN_NAVIGATION_CANDIDATE_LIMIT,
            page_size=SPAN_NAVIGATION_SCAN_PAGE_SIZE,
            deadline_ms=SPAN_NAVIGATION_WALL_DEADLINE_MS,
            max_query_count=SPAN_NAVIGATION_MAX_QUERIES,
            require_unique_target=True,
        )
        if not neighbors.complete or neighbors.current is None:
            code = neighbors.error_code or "read_incomplete"
            if code in {"target_not_found", "ambiguous_identity"}:
                code = "ambiguous_span_identity"
            raise SpanNavigationReadUnavailable(code)

        newer_row = neighbors.newer
        older_row = neighbors.older
        return self._gm.success_response(
            {
                "next_trace_id": (
                    str(older_row.get("trace_id")) if older_row else None
                ),
                "previous_trace_id": (
                    str(newer_row.get("trace_id")) if newer_row else None
                ),
            }
        )

    @validated_request(query_serializer=SpanIndexQuerySerializer)
    @action(detail=False, methods=["get"])
    def get_trace_id_by_index_spans_as_base(self, request, *args, **kwargs):
        """
        Get the previous and next span id by index for non-observe projects.
        Mirrors the query/filter logic of list_spans.
        """
        # CH25-TODO: this endpoint is the prev/next navigation companion
        # to list_spans (non-observe). It needs the same eval/annotation
        # filter pivot that the CH SpanListQueryBuilder produces plus a
        # cursor-style "find by start_time before/after span_id" step.
        #
        # Wave-3 partial coverage (commit 93c5c415f): the reader exposes
        # `prev_next_span_by_start_time(project_id=, span_id=,
        # project_version_id=, observation_type=)` which covers the
        # unfiltered walk but
        #   (a) returns span_ids while this endpoint returns trace_ids,
        #       and
        #   (b) does not accept the eval/annotation/span-attribute
        #       filters this endpoint applies (FilterEngine pivots +
        #       build_annotation_subqueries) before walking.
        # The frontend always sends `filters` (could be []) so a
        # drop-in swap would silently change the navigation set under
        # any non-empty filter. Staying PG-only.
        #
        # Reader-gap proposal:
        #   prev_next_trace_id_by_span_start_time(*, project_id,
        #       span_id, project_version_id=None, observation_type=None,
        #       filters=None) -> tuple[Optional[str], Optional[str]]
        # where `filters` accepts the SpanListQueryBuilder filter shape
        # (system metrics + eval pivots + annotation joins + span
        # attributes) and the return is (prev_trace_id, next_trace_id).
        try:
            query = request.validated_query_data
            span_id = query["span_id"]
            project_version_id = str(query["project_version_id"])

            project_version = ProjectVersion.objects.get(
                _project_workspace_scope_q(request),
                id=project_version_id,
                project__organization=_get_request_organization(request),
            )

            return self._bounded_span_navigation_response(
                project_id=project_version.project_id,
                project_version_id=project_version_id,
                span_id=span_id,
                filters=query["filters"],
            )

            base_query = ObservationSpan.objects.filter(
                _project_workspace_scope_q(request),
                project_version_id=project_version_id,
                project__organization=_get_request_organization(request),
            ).annotate(
                node_type=F("observation_type"),
                span_id=F("id"),
                span_name=F("name"),
            )

            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    observation_span__project_id=project_version.project.id
                )
                .values("custom_eval_config_id")
                .distinct(),
                deleted=False,
            ).select_related("eval_template")

            for config in eval_configs:
                choices = (
                    config.eval_template.choices
                    if config.eval_template.choices
                    else None
                )
                metric_subquery = (
                    EvalLogger.objects.filter(
                        observation_span_id=OuterRef("id"),
                        custom_eval_config_id=config.id,
                        observation_span__project__organization=_get_request_organization(
                            request
                        ),
                    )
                    .exclude(Q(output_str="ERROR") | Q(error=True))
                    .values("custom_eval_config_id")
                    .annotate(
                        float_score=Round(Avg("output_float") * 100, 2),
                        bool_score=Round(
                            Avg(
                                Case(
                                    When(output_bool=True, then=100),
                                    When(output_bool=False, then=0),
                                    default=None,
                                    output_field=FloatField(),
                                )
                            ),
                            2,
                        ),
                        str_list_score=JSONObject(
                            **{
                                f"{value}": JSONObject(
                                    score=Round(
                                        100.0
                                        * Count(
                                            Case(
                                                When(
                                                    output_str_list__contains=[value],
                                                    then=1,
                                                ),
                                                default=None,
                                                output_field=IntegerField(),
                                            )
                                        )
                                        / Count("output_str_list"),
                                        2,
                                    )
                                )
                                for value in choices or []
                            }
                        ),
                    )
                    .values("float_score", "bool_score", "str_list_score")[:1]
                )

                base_query = base_query.annotate(
                    **{
                        f"metric_{config.id}": Case(
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_float__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(
                                        metric_subquery.values("float_score")
                                    )
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_bool__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(metric_subquery.values("bool_score"))
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_str_list__isnull=False,
                                    )
                                ),
                                then=Subquery(metric_subquery.values("str_list_score")),
                            ),
                            default=None,
                            output_field=JSONField(),
                        )
                    }
                )

            annotation_labels = get_annotation_labels_for_project(
                project_version.project.id
            )
            base_query = build_annotation_subqueries(
                base_query,
                annotation_labels,
                request.user.organization,
                span_filter_kwargs={"observation_span_id": OuterRef("id")},
            )

            filters = query["filters"]
            if filters:
                combined_filter_conditions = Q()

                system_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filters)
                )
                if system_filter_conditions:
                    combined_filter_conditions &= system_filter_conditions

                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filters
                    if (f.get("filter_config") or {}).get("col_type")
                    not in annotation_col_types
                    and f.get("column_id") not in annotation_column_ids
                ]

                eval_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_non_system_metrics(
                        non_annotation_filters
                    )
                )
                if eval_filter_conditions:
                    combined_filter_conditions &= eval_filter_conditions

                annotation_filter_conditions, extra_annotations = (
                    FilterEngine.get_filter_conditions_for_voice_call_annotations(
                        filters,
                        user_id=request.user.id,
                        span_filter_kwargs={"observation_span_id": OuterRef("id")},
                    )
                )
                if extra_annotations:
                    base_query = base_query.annotate(**extra_annotations)
                if annotation_filter_conditions:
                    combined_filter_conditions &= annotation_filter_conditions

                span_attribute_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_conditions:
                    combined_filter_conditions &= span_attribute_conditions

                if combined_filter_conditions:
                    base_query = base_query.filter(combined_filter_conditions)

            base_query = base_query.order_by("-start_time", "-id")

            current_span = base_query.filter(id=span_id).values("start_time").first()
            if not current_span:
                raise Exception("Span not found in the list")

            previous_trace = None
            next_trace = None

            if current_span["start_time"] is not None:
                previous_trace = (
                    base_query.filter(start_time__lt=current_span["start_time"])
                    .order_by("-start_time")
                    .values_list("trace_id", flat=True)
                    .first()
                )
                next_trace = (
                    base_query.filter(start_time__gt=current_span["start_time"])
                    .order_by("start_time")
                    .values_list("trace_id", flat=True)
                    .first()
                )

            response = {
                "next_trace_id": str(previous_trace) if previous_trace else None,
                "previous_trace_id": str(next_trace) if next_trace else None,
            }

            return self._gm.success_response(response)

        except SpanNavigationReadUnavailable as exc:
            logger.warning(
                "span_navigation_bounded_read_incomplete", error_code=exc.code
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Span navigation is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as exc:
            logger.exception("span_navigation_failed", error_type=type(exc).__name__)
            if is_read_budget_error(exc) or is_clickhouse_query_error(exc):
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Span navigation is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            return self._gm.bad_request("Span navigation could not be loaded")

    @validated_request(query_serializer=SpanObserveIndexQuerySerializer)
    @action(detail=False, methods=["get"])
    def get_trace_id_by_index_spans_as_observe(self, request, *args, **kwargs):
        """
        Get the previous and next trace id by index for observe projects.
        Mirrors the query/filter logic of list_spans_as_observe.
        """
        # CH25-TODO: observe sibling of get_trace_id_by_index_spans_as_base.
        # Same reader-gap rationale — staying on PG.
        #
        # Wave-3 partial coverage (commit 93c5c415f):
        # `prev_next_span_by_start_time` does the unfiltered walk but
        #   (a) returns span_ids while this endpoint returns trace_ids,
        #   (b) does not accept the eval/annotation/span-attribute
        #       filters this endpoint applies before walking, and
        #   (c) the observe variant also applies an `end_user_id` scope
        #       (from EndUser lookup) that the reader method doesn't
        #       expose.
        # The frontend always sends `filters` (could be []) so a
        # drop-in swap would silently change the navigation set under
        # any non-empty filter. Staying PG-only.
        #
        # Reader-gap proposal (shared with non-observe variant above):
        #   prev_next_trace_id_by_span_start_time(*, project_id,
        #       span_id, project_version_id=None, observation_type=None,
        #       end_user_id=None, filters=None)
        #       -> tuple[Optional[str], Optional[str]]
        try:
            query = request.validated_query_data
            span_id = query["span_id"]
            project_id = str(query["project_id"])
            user_id = query.get("user_id") or None

            project = Project.objects.get(
                _project_workspace_scope_q(request, project_prefix=""),
                id=project_id,
                organization=_get_request_organization(request),
            )
            if project.trace_type not in ("observe", "experiment"):
                raise SpanNavigationReadUnavailable("unsupported_project_type")
            if user_id:
                # The exact bounded classifier does not yet support the curated
                # end-user remap as an order-preserving predicate.  Fail closed
                # instead of silently navigating the unscoped span list.
                raise SpanNavigationReadUnavailable("unsupported_filter_modifiers")
            return self._bounded_span_navigation_response(
                project_id=project_id,
                span_id=span_id,
                filters=query["filters"],
            )

            # P3b step2 precondition — user_id → end_user reverse-resolve (CH, not
            # PG). The old PG `EndUser.objects.get(user_id=…).id` raised "User not
            # found" for a NET-NEW user (no `tracer_enduser` row post-step2). Read
            # the curated id-SET from CH `end_users` instead (historical + net-new
            # deterministic + straddler's both — the state-robust reverse-resolve,
            # PG_ORM_READ_MIGRATION). The id-set then filters the spans below via
            # `end_user_id__in` so a straddler's old + new ids both match.
            #
            # NOTE this endpoint's prev/next WALK stays PG (a documented CH25-TODO
            # reader-gap above): a span carrying a resolved end_user_id is matched
            # in PG `tracer_observationspan`. Post-step2 in production the collector
            # writes the deterministic end_user_id onto the PG span, so the walk
            # finds a net-new user's spans; it only fails to in a CH-ONLY rehearsal
            # where the net-new spans were manufactured in CH but not PG. An empty
            # id-set (unknown user) now yields an empty walk instead of raising —
            # net-new is no longer "User not found", the intended fix.
            end_user_ids: list[str] = []
            if user_id:
                from tracer.services.clickhouse.v2.end_user_dict_reader import (
                    resolve_end_user_ids_by_user_id,
                )

                end_user_ids = resolve_end_user_ids_by_user_id(
                    user_id, project_id=project_id
                )

            project = Project.objects.get(
                _project_workspace_scope_q(request, project_prefix=""),
                id=project_id,
                organization=_get_request_organization(request),
            )
            if project.trace_type not in ("observe", "experiment"):
                raise Exception("Project should be of type observe or experiment")

            base_query = ObservationSpan.objects.filter(
                _project_workspace_scope_q(request),
                project_id=project_id,
                project__organization=_get_request_organization(request),
            ).annotate(
                node_type=F("observation_type"),
                span_id=F("id"),
                span_name=F("name"),
                user_id=F("end_user__user_id"),
                user_id_type=F("end_user__user_id_type"),
                user_id_hash=F("end_user__user_id_hash"),
            )

            if end_user_ids:
                # IN over the curated id-set so a straddler's old + new ids both
                # match (single-id `=` would miss half its spans post-flip).
                base_query = base_query.filter(end_user_id__in=end_user_ids)

            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    observation_span__project_id=project_id,
                    observation_span__project__organization=_get_request_organization(
                        request
                    ),
                )
                .values("custom_eval_config_id")
                .distinct(),
                deleted=False,
            ).select_related("eval_template")

            for config in eval_configs:
                choices = (
                    config.eval_template.choices
                    if config.eval_template.choices
                    else None
                )
                metric_subquery = (
                    EvalLogger.objects.filter(
                        observation_span_id=OuterRef("id"),
                        custom_eval_config_id=config.id,
                        observation_span__project__organization=_get_request_organization(
                            request
                        ),
                    )
                    .exclude(Q(output_str="ERROR") | Q(error=True))
                    .values("custom_eval_config_id")
                    .annotate(
                        float_score=Round(Avg("output_float") * 100, 2),
                        bool_score=Round(
                            Avg(
                                Case(
                                    When(output_bool=True, then=100),
                                    When(output_bool=False, then=0),
                                    default=None,
                                    output_field=FloatField(),
                                )
                            ),
                            2,
                        ),
                        str_list_score=JSONObject(
                            **{
                                f"{value}": JSONObject(
                                    score=Round(
                                        100.0
                                        * Count(
                                            Case(
                                                When(
                                                    output_str_list__contains=[value],
                                                    then=1,
                                                ),
                                                default=None,
                                                output_field=IntegerField(),
                                            )
                                        )
                                        / Count("output_str_list"),
                                        2,
                                    )
                                )
                                for value in choices or []
                            }
                        ),
                    )
                    .values("float_score", "bool_score", "str_list_score")[:1]
                )

                base_query = base_query.annotate(
                    **{
                        f"metric_{config.id}": Case(
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_float__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(
                                        metric_subquery.values("float_score")
                                    )
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_bool__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(metric_subquery.values("bool_score"))
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_str_list__isnull=False,
                                    )
                                ),
                                then=Subquery(metric_subquery.values("str_list_score")),
                            ),
                            default=None,
                            output_field=JSONField(),
                        )
                    }
                )

            annotation_labels = get_annotation_labels_for_project(project_id)
            base_query = build_annotation_subqueries(
                base_query,
                annotation_labels,
                request.user.organization,
                span_filter_kwargs={"observation_span_id": OuterRef("id")},
            )

            filters = query["filters"]

            if filters:
                combined_filter_conditions = Q()

                system_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filters)
                )
                if system_filter_conditions:
                    combined_filter_conditions &= system_filter_conditions

                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filters
                    if (f.get("filter_config") or {}).get("col_type")
                    not in annotation_col_types
                    and f.get("column_id") not in annotation_column_ids
                ]

                eval_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_non_system_metrics(
                        non_annotation_filters
                    )
                )
                if eval_filter_conditions:
                    combined_filter_conditions &= eval_filter_conditions

                annotation_filter_conditions, extra_annotations = (
                    FilterEngine.get_filter_conditions_for_voice_call_annotations(
                        filters,
                        user_id=request.user.id,
                        span_filter_kwargs={"observation_span_id": OuterRef("id")},
                    )
                )
                if extra_annotations:
                    base_query = base_query.annotate(**extra_annotations)
                if annotation_filter_conditions:
                    combined_filter_conditions &= annotation_filter_conditions

                span_attribute_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_conditions:
                    combined_filter_conditions &= span_attribute_conditions

                has_eval_condition = FilterEngine.get_filter_conditions_for_has_eval(
                    filters, observe_type="span"
                )
                if has_eval_condition:
                    combined_filter_conditions &= has_eval_condition

                # Apply has_annotation filter
                has_annotation_condition = (
                    FilterEngine.get_filter_conditions_for_has_annotation(
                        filters, observe_type="span"
                    )
                )
                if has_annotation_condition:
                    combined_filter_conditions &= has_annotation_condition

                if combined_filter_conditions:
                    base_query = base_query.filter(combined_filter_conditions)

            base_query = base_query.order_by("-start_time", "-id")

            current_span = base_query.filter(id=span_id).values("start_time").first()
            if not current_span:
                raise Exception("Span not found in the list")

            previous_trace = None
            next_trace = None

            if current_span["start_time"] is not None:
                previous_trace = (
                    base_query.filter(start_time__lt=current_span["start_time"])
                    .order_by("-start_time")
                    .values_list("trace_id", flat=True)
                    .first()
                )
                next_trace = (
                    base_query.filter(start_time__gt=current_span["start_time"])
                    .order_by("start_time")
                    .values_list("trace_id", flat=True)
                    .first()
                )

            response = {
                "next_trace_id": str(previous_trace) if previous_trace else None,
                "previous_trace_id": str(next_trace) if next_trace else None,
            }

            return self._gm.success_response(response)

        except SpanNavigationReadUnavailable as exc:
            logger.warning(
                "span_navigation_bounded_read_incomplete", error_code=exc.code
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Span navigation is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as exc:
            logger.exception("span_navigation_failed", error_type=type(exc).__name__)
            if is_read_budget_error(exc) or is_clickhouse_query_error(exc):
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Span navigation is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            return self._gm.bad_request("Span navigation could not be loaded")


def get_observation_spans(filters):
    """
    Fetch an observation span based on its ID.
    Filters is a required object that must contain the following fields:
    - project_id (optional)
    - project_version_id (optional)
    - trace_id (optional)

    CH25-TODO: this helper feeds the legacy compare_traces and the
    PG-only retrieve fallback (now removed). The orphaned-span tree
    walk + dummy-parent construction is too entangled with the PG
    schema to lift to CH without a dedicated reader method (would
    need orphaned-span detection that compares parent_span_id against
    the same trace's id set). Staying PG-only until compare_traces is
    either retired or its callers move to the CH retrieve path.
    """
    project_id = filters.get("project_id", None)
    project_version_id = filters.get("project_version_id", None)
    trace_id = filters.get("trace_id", None)

    if not project_id and not project_version_id and not trace_id:
        raise Exception(
            "At least one of the following fields is required: observation_span_id, project_id, project_version_id, trace_id."
        )

    base_filters = {
        "project": project_id,
        "project_version": project_version_id,
        "trace": trace_id,
    }
    base_filters = {k: v for k, v in base_filters.items() if v is not None}

    response_data = []

    # Process actual parent spans
    response_data.extend(_process_parent_spans(base_filters))

    # Process orphaned spans
    response_data.extend(_process_orphaned_spans(base_filters))

    return response_data


def fetch_children_span_ids(root_span: ObservationSpan):
    try:
        rows = SQL_query_handler.fetch_children_ids_query(str(root_span.id))

        result_ids = [str(row[0]) for row in rows]

        return result_ids

    except Exception as e:
        logger.exception(f"Error in fetching children span ids: {str(e)}")
        return []


def fetch_children(root_span: ObservationSpan):
    try:
        close_old_connections()

        span_map = {}  # span_id -> span data structure
        parent_map = {}  # span_id -> parent_id

        rows = SQL_query_handler.fetch_children_query(str(root_span.id))
        updated_rows = [
            {
                "id": row[0],
                "parent_span_id": row[1],
                "name": row[2],
                "observation_type": row[3],
                "prompt_tokens": row[4],
                "total_tokens": row[5],
                "latency_ms": row[6],
                "completion_tokens": row[7],
                "span_events": row[8],
                "trace_id": row[9],
                "cost": row[10],
            }
            for row in rows
        ]

        # Batch queries to reduce DB hits
        total_span_ids = [span["id"] for span in updated_rows]

        eval_counts = fetch_evals_count(total_span_ids)
        annotation_counts = fetch_annotation_count(total_span_ids)

        # Build span objects
        for span in updated_rows:
            data = span
            if data["cost"] and data["cost"] > 0:
                data["cost"] = round(data["cost"], 6)
            data["total_evals_count"] = eval_counts.get(span["id"], 0)
            data["total_annotations_count"] = annotation_counts.get(span["id"], 0)
            span_map[span["id"]] = {"observation_span": data, "children": []}
            parent_map[span["id"]] = span["parent_span_id"]

        # Build tree
        root_data = {
            "id": root_span.id,
            "name": root_span.name,
            "observation_type": root_span.observation_type,
            "prompt_tokens": root_span.prompt_tokens,
            "total_tokens": root_span.total_tokens,
            "latency_ms": root_span.latency_ms,
            "completion_tokens": root_span.completion_tokens,
            "span_events": root_span.span_events,
            "total_evals_count": eval_counts.get(root_span.id, 0),
            "total_annotations_count": annotation_counts.get(root_span.trace.id, 0),
            "trace_id": str(root_span.trace.id),
            "parent_span_id": str(root_span.parent_span_id),
            "cost": (
                round(root_span.cost, 6) if root_span.cost and root_span.cost > 0 else 0
            ),
        }
        root_node = {"observation_span": root_data, "children": []}
        span_map[root_span.id] = root_node

        for span_id, node in span_map.items():
            parent_id = parent_map.get(span_id)
            if parent_id is not None and parent_id in span_map:
                children_list = span_map[parent_id].get("children", [])
                if isinstance(children_list, list):
                    children_list.append(node)

        return root_node["children"]

    except Exception as e:
        logger.exception(f"Error in fetching children: {str(e)}")
    finally:
        close_old_connections()


def fetch_annotation_count(span_ids: list[str]):
    """
    Fetch annotation count for a list of span ids.

    Args:
        span_ids (list[str]): List of span ids
    Returns:
        dict: Dictionary mapping span id to annotation count
    """
    annotation_results = (
        Score.objects.filter(
            observation_span_id__in=span_ids,
            deleted=False,
        )
        .values("observation_span_id")
        .annotate(count=Count("id"))
    )

    return {row["observation_span_id"]: row["count"] for row in annotation_results}


def fetch_evals_count(span_ids: list[str]):
    """
    Fetch evals count for a list of span ids.

    Args:
        span_ids (list[str]): List of span ids
    Returns:
        dict: Dictionary mapping span id to evals count
    """
    eval_results = (
        EvalLogger.objects.filter(observation_span_id__in=span_ids)
        .values("observation_span_id")
        .annotate(count=Count("id"))
    )

    return {row["observation_span_id"]: row["count"] for row in eval_results}


def _process_parent_spans(base_filters):
    """
    Process spans that have no parent (root spans).

    Args:
        base_filters (dict): Base query filters

    Returns:
        list: List of observation span data with children
    """
    parent_filters = {**base_filters, "parent_span_id__isnull": True}
    parent_spans = ObservationSpan.objects.filter(**parent_filters).order_by(
        "start_time"
    )

    return [_build_span_response(parent_span) for parent_span in parent_spans]


def _process_orphaned_spans(base_filters):
    """
    Process orphaned spans (spans with missing parents) and create dummy parents.

    Args:
        base_filters (dict): Base query filters

    Returns:
        list: List of dummy parent spans with their orphaned children
    """
    orphaned_spans = _find_orphaned_spans(base_filters)
    if not orphaned_spans:
        return []

    orphaned_groups = _group_orphaned_spans_by_parent(orphaned_spans)
    return [
        _create_dummy_parent_response(parent_id, children, base_filters)
        for parent_id, children in orphaned_groups.items()
    ]


def _find_orphaned_spans(base_filters):
    """
    Find spans that reference non-existent parent spans.

    Args:
        base_filters (dict): Base query filters

    Returns:
        list: List of orphaned ObservationSpan objects
    """
    parent_exists = ObservationSpan.objects.filter(
        id=OuterRef("parent_span_id"), **base_filters
    )

    orphaned_spans = (
        ObservationSpan.objects.filter(**base_filters, parent_span_id__isnull=False)
        .annotate(parent_exists=Exists(parent_exists))
        .filter(parent_exists=False)
    )

    return list(orphaned_spans)


def _group_orphaned_spans_by_parent(orphaned_spans):
    """
    Group orphaned spans by their missing parent_span_id.

    Args:
        orphaned_spans (list): List of orphaned ObservationSpan objects

    Returns:
        dict: Dictionary mapping parent_id to list of child spans
    """
    orphaned_groups = defaultdict(list)
    for span in orphaned_spans:
        orphaned_groups[span.parent_span_id].append(span)
    return orphaned_groups


def _create_dummy_parent_response(missing_parent_id, child_spans, base_filters):
    """
    Create a dummy parent span response for orphaned children.

    Args:
        missing_parent_id (str): ID of the missing parent span
        child_spans (list): List of orphaned child spans
        base_filters (dict): Base query filters

    Returns:
        dict: Dummy parent span response with children
    """
    earliest_child = child_spans[0]

    dummy_parent_data = _create_dummy_parent_data(
        missing_parent_id, earliest_child, base_filters
    )

    dummy_children = [_build_span_response(child_span) for child_span in child_spans]

    return {"observation_span": dummy_parent_data, "children": dummy_children}


def _create_dummy_parent_data(missing_parent_id, reference_child, base_filters):
    """
    Create dummy parent span data structure.

    Args:
        missing_parent_id (str): ID of the missing parent span
        reference_child (ObservationSpan): Child span to inherit org data from
        base_filters (dict): Base query filters

    Returns:
        dict: Dummy parent span data
    """
    return {
        "id": missing_parent_id,
        "project": base_filters.get("project"),
        "project_version": base_filters.get("project_version"),
        "trace": base_filters.get("trace"),
        "parent_span_id": None,
        "name": f"[Missing Span] {missing_parent_id}",
        "observation_type": "unknown",
        "org_id": reference_child.org_id,
        "org_user_id": reference_child.org_user_id,
        "metadata": {"is_dummy": True, "reason": "Parent span not yet exported"},
    }


def _build_span_response(span):
    """
    Build span response with eval and annotation counts.

    Args:
        span (ObservationSpan): The observation span object

    Returns:
        dict: Span response with observation_span data and children
    """
    data = ObservationSpanSerializer(span).data

    if data["cost"] and data["cost"] > 0:
        data["cost"] = round(data["cost"], 6)

    data["total_evals_count"] = _get_evals_count(span.id)
    data["total_annotations_count"] = _get_annotations_count(span)

    if data["prompt_version"]:
        try:
            prompt_version = PromptVersion.objects.get(id=data["prompt_version"])
            data["prompt_template_id"] = str(prompt_version.original_template.id)
            data["prompt_name"] = (
                str(prompt_version.original_template.name)
                + " - "
                + str(prompt_version.template_version)
            )

        except PromptVersion.DoesNotExist:
            data["prompt_version"] = None

    return {"observation_span": data, "children": fetch_children(span)}


def _get_evals_count(span_id):
    """
    Get evaluation count for a span.

    Args:
        span_id (str): The span ID

    Returns:
        int: Number of evaluations
    """
    count = EvalLogger.objects.filter(observation_span_id=span_id).count()
    return count if count is not None else 0


def _get_annotations_count(span):
    """
    Get annotation count for a span.

    Args:
        span (ObservationSpan): The observation span object

    Returns:
        int: Number of annotations
    """
    count = Score.objects.filter(observation_span=span, deleted=False).count()
    return count if count is not None else 0
