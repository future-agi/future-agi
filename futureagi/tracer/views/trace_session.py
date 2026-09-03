import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from functools import wraps

try:
    import orjson

    _json_loads = orjson.loads
except ImportError:
    _json_loads = json.loads

import structlog
from django.conf import settings
from django.db import OperationalError, connection, models, transaction
from django.db.models import (
    Count,
    DurationField,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
)
from django.db.models.functions import (
    Coalesce,
    Round,
)
from django.http import Http404
from django.utils import timezone
from drf_yasg import openapi
from rest_framework import status as drf_status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import (
    EndUser,
    EvalLogger,
    ObservationSpan,
)
from tracer.models.project import Project, ProjectSourceChoices
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.selectors.trace_filter_reads import (
    PAGE_DEPTH_EXCEEDED_CODE,
    PAGE_DEPTH_EXCEEDED_MESSAGE,
    bounded_numbered_page_depth_exceeded,
    numbered_page_depth_exceeded,
    read_bounded_filter_page,
)
from tracer.serializers.eval_task import PaginationQuerySerializer
from tracer.serializers.filters import (
    ObserveGraphDataErrorResponseSerializer,
    ObserveGraphDataQuerySerializer,
    ObserveGraphDataResponseSerializer,
    PageDepthExceededErrorSerializer,
)
from tracer.serializers.trace import TraceSessionListResponseSerializer
from tracer.serializers.trace_session import (
    TraceSessionExportQuerySerializer,
    TraceSessionFilterValuesQuerySerializer,
    TraceSessionGraphDataRequestSerializer,
    TraceSessionListQuerySerializer,
    TraceSessionRetrieveQuerySerializer,
    TraceSessionSerializer,
)
from tracer.services.clickhouse.bounded_graph_reads import BoundedGraphReadError
from tracer.services.clickhouse.filter_value_reads import (
    read_session_message_filter_values,
)
from tracer.services.clickhouse.graph_action_deadline import (
    GraphActionUnavailable,
    finish_graph_action_response,
    graph_action_postgres_budget,
    graph_action_remaining_ms,
    start_graph_action_deadline,
)
from tracer.services.clickhouse.graph_dispatch import (
    degraded_graph_response,
    enforce_exact_graph_data_contract,
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
    list_cursor_boundary_fingerprint,
)
from tracer.services.clickhouse.query_builders.base import NIL_UUID, BaseQueryBuilder
from tracer.services.clickhouse.query_builders.eval_status import (
    non_terminal_eval_marker,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
    is_clickhouse_api_read_unavailable_error,
    is_clickhouse_query_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.session_graph import (
    SESSION_SYSTEM_METRICS,
    fetch_session_graph_ch,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
from tracer.services.filter_attestation import (
    applied_filter_attestation,
    graph_execution_filters,
    graph_query_evidence,
)
from tracer.services.filter_principal_context import (
    FilterPrincipalContextError,
    bind_request_my_annotations_principal,
)
from tracer.utils.bounded_csv import (
    BOUNDED_SESSION_EXPORT_PAGE_SIZE,
    bounded_page_csv_response,
)
from tracer.utils.filters import FilterEngine, apply_created_at_filters
from tracer.utils.helper import (
    FieldConfig,
    ensure_project_session_property_identities,
    format_datetime_fields_to_iso,
    format_datetime_to_iso,
    get_annotation_labels_by_project,
    get_annotation_labels_for_project,
    get_default_project_session_config,
)
from tracer.utils.property_registry import validate_property_graph_namespace
from tracer.utils.session import get_session_navigation

# Module loggers — declared AFTER all imports (E402). Both are the same
# structlog logger bound to this module; the two names are kept for the
# call-sites that historically used each.
logger = structlog.get_logger(__name__)
session_logger = structlog.get_logger(__name__)

# All interactive session-list reads share one environment-backed wall deadline.
# Individual phases receive only the request's remaining time, so concurrent
# finite enrichments cannot extend the endpoint beyond that ceiling.
SESSION_LIST_WALL_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
SESSION_LIST_QUERY_TIMEOUT_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
SESSION_LIST_ENRICHMENT_TIMEOUT_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
SESSION_FILTER_VALUE_WALL_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
SESSION_LIST_FILTER_MAX_CANDIDATES = settings.SESSION_LIST_FILTER_MAX_CANDIDATES
SESSION_LIST_FILTER_MAX_SEED_ATTEMPTS = settings.SESSION_LIST_FILTER_MAX_SEED_ATTEMPTS
SESSION_LIST_FILTER_MAX_QUERIES = settings.SESSION_LIST_FILTER_MAX_QUERIES
SESSION_LIST_READ_SETTINGS = {
    "max_threads": settings.SESSION_LIST_READ_MAX_THREADS,
    "max_block_size": settings.OBSERVABILITY_LIST_MAX_BLOCK_SIZE,
    "read_overflow_mode": "throw",
    "max_bytes_to_read": settings.OBSERVABILITY_LIST_MAX_BYTES,
    "max_memory_usage": settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES,
    "timeout_overflow_mode": "throw",
}
SESSION_LIST_RESULT_BYTES = settings.SESSION_LIST_MAX_RESULT_BYTES
SESSION_LIST_ATTRIBUTE_RESULT_ROWS = settings.SESSION_LIST_ATTRIBUTE_MAX_RESULT_ROWS
SESSION_GRAPH_RETRYABLE_ERROR_CODES = {
    "deadline_exceeded",
    "read_budget_exceeded",
    "sample_limit",
}


def _session_read_settings(*, max_result_rows: int) -> dict[str, int | str]:
    """Return hard server-side bounds for one session-list CH read."""

    if max_result_rows <= 0:
        raise ValueError("max_result_rows must be positive")
    return {
        **SESSION_LIST_READ_SETTINGS,
        "max_result_rows": int(max_result_rows),
        "max_result_bytes": SESSION_LIST_RESULT_BYTES,
        "result_overflow_mode": "throw",
    }


def _read_session_filter_project_in_scope(*, request, project_id, deadline) -> bool:
    """Authorize one picker project inside the shared request deadline.

    PostgreSQL's timeout is per statement, so derive it from the same
    monotonic deadline later passed to ClickHouse and session-label hydration.
    The post-read check also enforces the wall on non-PostgreSQL test lanes.
    """

    try:
        if connection.vendor == "postgresql":
            with transaction.atomic():
                timeout_ms = deadline.remaining_ms(floor_ms=1)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        [str(timeout_ms)],
                    )
                in_scope = (
                    _project_queryset_for_request(request)
                    .filter(id=project_id)
                    .exists()
                )
        else:
            in_scope = (
                _project_queryset_for_request(request).filter(id=project_id).exists()
            )
        deadline.remaining_ms(floor_ms=1)
        return in_scope
    except ReadDeadlineExceeded:
        raise
    except OperationalError as exc:
        raise ReadDeadlineExceeded(
            "Session filter project authorization exceeded its request deadline"
        ) from exc


def _execute_session_list_query_with_deadline(
    deadline, execute, sql, params, many, context
):
    """Execute one PostgreSQL statement inside the session request wall."""

    remaining_ms = deadline.remaining_ms(floor_ms=1)
    try:
        context["cursor"].cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (str(remaining_ms),),
        )
        result = execute(sql, params, many, context)
    except OperationalError as exc:
        raise ReadDeadlineExceeded(
            "Session list PostgreSQL read exceeded its request deadline"
        ) from exc
    deadline.remaining_ms(floor_ms=1)
    return result


@contextmanager
def _bounded_session_list_postgres_reads(deadline):
    """Give every session-list PostgreSQL statement the shrinking wall."""

    transaction_started = False

    def execute_with_remaining_timeout(execute, sql, params, many, context):
        nonlocal transaction_started
        if not connection.in_atomic_block and not transaction_started:
            stack.enter_context(transaction.atomic())
            transaction_started = True
        return _execute_session_list_query_with_deadline(
            deadline, execute, sql, params, many, context
        )

    if connection.vendor != "postgresql":
        yield
        deadline.remaining_ms(floor_ms=1)
        return

    # Installing an execute wrapper is connection-lazy. Start the transaction
    # only when the first real ORM statement arrives, so pure/mocked early exits
    # do not open a database socket while production statements still receive
    # transaction-local shrinking timeouts.
    with ExitStack() as stack:
        stack.enter_context(connection.execute_wrapper(execute_with_remaining_timeout))
        yield
        deadline.remaining_ms(floor_ms=1)


def _bounded_session_list_request(view_method):
    """Own one wall across session scope, overlay, ClickHouse, and formatting."""

    @wraps(view_method)
    def wrapped(view, request, *args, **kwargs):
        deadline = ReadDeadline.start(SESSION_LIST_WALL_DEADLINE_MS)
        try:
            with _bounded_session_list_postgres_reads(deadline):
                response = view_method(
                    view,
                    request,
                    *args,
                    _session_list_read_deadline=deadline,
                    **kwargs,
                )
                deadline.remaining_ms(floor_ms=1)
                return response
        except (ReadDeadlineExceeded, OperationalError) as exc:
            session_logger.warning(
                "session_list_request_deadline_exceeded",
                error_type=type(exc).__name__,
            )
            return view._gm.custom_error_response(
                drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                "Session data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

    # Several focused unit boundaries intentionally use the historical
    # one-level ``.__wrapped__`` escape hatch to supply already-validated data.
    # The runtime closure still invokes ``view_method`` (the validated wrapper),
    # while introspection reaches the original action and its fallback deadline.
    wrapped.__wrapped__ = getattr(view_method, "__wrapped__", view_method)
    return wrapped


def _bounded_session_graph_request(view_method):
    """Start the session-graph wall before runtime request validation."""

    @wraps(view_method)
    def wrapped(view, request, *args, **kwargs):
        deadline = start_graph_action_deadline()
        kwargs.pop("_graph_action_deadline", None)
        try:
            response = view_method(
                view,
                request,
                *args,
                _graph_action_deadline=deadline,
                **kwargs,
            )
            return finish_graph_action_response(deadline, response)
        except GraphActionUnavailable:
            session_logger.warning(
                "session_graph_action_deadline_exceeded",
                project_id=str(
                    getattr(request, "validated_data", {}).get("project_id", "")
                ),
            )
            return view._gm.custom_error_response(
                drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                "Session graph data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

    # Preserve the established direct unit boundary while the runtime closure
    # continues to execute the validated wrapper copied above.
    wrapped.__wrapped__ = getattr(view_method, "__wrapped__", view_method)
    return wrapped


def _session_list_cursor_order_for_partial_page(*, rows, bounded_page, cursor_state):
    """Return a stable public order tuple, including checkpoint-only pages."""

    if rows:
        last = rows[-1]
        return (
            last.get("_seed_order_start") or last.get("start_time"),
            str(last.get("_seed_order_id") or last.get("session_id") or ""),
        )
    if cursor_state is not None:
        return tuple(cursor_state.order)
    if bounded_page.continuation_before_start_time is not None:
        return (
            bounded_page.continuation_before_start_time,
            str(bounded_page.continuation_before_id or ""),
        )
    if bounded_page.continuation_slice_end is not None:
        return bounded_page.continuation_slice_end, "\U0010ffff" * 8
    raise RuntimeError("session continuation has no stable order boundary")


def _merge_session_attribute_sources(attr_row: dict) -> dict:
    """Union direct-write typed Maps with structured overflow attributes.

    Scalar attributes are written to the three typed Maps while JSON objects
    and arrays remain in ``attributes_extra``.  Every physical source can be
    populated on the same span, so merge them unconditionally.  Structured
    JSON wins on duplicate keys, matching the other direct-write read paths.
    """

    raw = attr_row.get("span_attributes_raw", "{}")
    try:
        attrs = _json_loads(raw) if isinstance(raw, str) and raw else (raw or {})
    except (json.JSONDecodeError, ValueError, TypeError):
        attrs = {}
    if not isinstance(attrs, dict):
        attrs = {}
    for map_name in ("attrs_string", "attrs_number", "attrs_bool"):
        typed_map = attr_row.get(map_name) or {}
        if not isinstance(typed_map, dict):
            continue
        for key, value in typed_map.items():
            if map_name == "attrs_bool":
                value = bool(value)
            attrs.setdefault(key, value)
    return attrs


def _resolve_session_ids_to_canonical(
    analytics,
    session_ids,
    *,
    deadline: ReadDeadline | None = None,
    settings: dict | None = None,
):
    """Map ``{input trace_session_id -> survivor (canonical old) id}``.

    Resolve each caller id to its consolidation group's survivor via the SAME
    survivor map the span side uses (``survivor_map_subquery``), so the input side
    can't disagree: a new id, a non-survivor old, and the survivor all map to the
    survivor; an unmapped id (1:1 / net-new) maps to itself. Pre-flip a no-op
    (gate B). See id_remap_sql.
    """
    from tracer.services.clickhouse.v2.id_remap_sql import (
        bounded_survivor_map_subquery,
    )

    ids = {str(s) for s in (session_ids or []) if s}
    if not ids:
        return {}
    # The caller always owns a finite page/detail id set. Do not materialize the
    # global window-function survivor map and filter it afterward: on large
    # production remap tables that intermediate alone can exceed the 256 MiB
    # read ceiling. Discover and expand only groups touched by these ids.
    bounded_map = bounded_survivor_map_subquery(
        "trace_session_id_remap", candidate_param="ids"
    )
    q = (
        "SELECT toString(any_id) AS any_id, toString(survivor_id) AS survivor_id "
        f"FROM ({bounded_map}) "
        "WHERE any_id IN %(ids)s"
    )
    timeout_ms = (
        deadline.remaining_ms(SESSION_LIST_ENRICHMENT_TIMEOUT_MS)
        if deadline is not None
        else 5000
    )
    res = analytics.execute_ch_query(
        q,
        {"ids": tuple(ids)},
        timeout_ms=timeout_ms,
        **({"settings": settings} if settings is not None else {}),
    )
    id_to_survivor = {}
    for row in res.data or []:
        if isinstance(row, dict):
            id_to_survivor[str(row.get("any_id"))] = str(row.get("survivor_id"))
        else:
            id_to_survivor[str(row[0])] = str(row[1])
    return {i: id_to_survivor.get(i, i) for i in ids}


def _expand_session_group(
    analytics,
    canonical_session_id: str,
    *,
    deadline: ReadDeadline | None = None,
) -> tuple[str, ...]:
    """Return all trace_session_ids (old + new) that share the same canonical.

    For a single session detail lookup this replaces the heavy
    ``LEFT JOIN (survivor_map_subquery)`` with a cheap ``IN (...)`` filter
    that uses the bloom index on ``trace_session_id``.
    """
    q = (
        "SELECT DISTINCT toString(old_id) AS id "
        "FROM trace_session_id_remap FINAL "
        "WHERE new_id = ("
        "  SELECT new_id FROM trace_session_id_remap FINAL "
        "  WHERE old_id = %(canonical_id)s LIMIT 1"
        ") "
        "UNION ALL "
        "SELECT DISTINCT toString(new_id) AS id "
        "FROM trace_session_id_remap FINAL "
        "WHERE old_id = %(canonical_id)s"
    )
    res = analytics.execute_ch_query(
        q,
        {"canonical_id": canonical_session_id},
        timeout_ms=(deadline.remaining_ms(3_000) if deadline else 3_000),
    )
    ids = {canonical_session_id}
    for row in res.data or []:
        val = str(row.get("id") if isinstance(row, dict) else row[0])
        if val and val != "00000000-0000-0000-0000-000000000000":
            ids.add(val)
    return tuple(ids)


def _get_request_organization(request):
    return getattr(request, "organization", None) or getattr(
        request.user, "organization", None
    )


def _project_workspace_scope_q(request, project_prefix="project__"):
    organization = _get_request_organization(request)
    scope = Q(**{f"{project_prefix}organization": organization})
    workspace = getattr(request, "workspace", None)
    if workspace:
        if getattr(workspace, "is_default", False):
            scope &= (
                Q(**{f"{project_prefix}workspace": workspace})
                | Q(
                    **{
                        f"{project_prefix}workspace__is_default": True,
                        f"{project_prefix}workspace__organization": organization,
                    }
                )
                | Q(**{f"{project_prefix}workspace__isnull": True})
            )
        else:
            scope &= Q(**{f"{project_prefix}workspace": workspace})
    return scope


def _project_queryset_for_request(request):
    manager = getattr(Project, "no_workspace_objects", Project.objects)
    return manager.filter(
        _project_workspace_scope_q(request, project_prefix=""),
        deleted=False,
    )


def _trace_session_queryset_for_request(request):
    manager = getattr(TraceSession, "no_workspace_objects", TraceSession.objects)
    return manager.filter(
        _project_workspace_scope_q(request),
        project__deleted=False,
        deleted=False,
    )


def _resolve_ch_session_fields(request, trace_session_id):
    """Resolve a CH-only session (fi-collector ingests to CH, not PG).

    Returns the curated CH ``trace_sessions_dict`` fields (``project_id``,
    ``display_name``, ``external_session_id``, …) when the session exists
    AND the caller's workspace can access its project. Returns ``None`` when
    the session is unknown or out of scope — callers map that to "not found"
    so the CH fallback enforces the same tenant gate as the PG queryset.
    """
    from tracer.services.clickhouse.v2.trace_session_dict_reader import (
        resolve_session_fields,
    )

    # Workspace-scoped lookup — the project is discovered from the resolved
    # fields below, so there's no single project_id to scope the read by here.
    session_fields = resolve_session_fields([trace_session_id]).get(
        str(trace_session_id)
    )
    if not session_fields:
        return None
    if not (
        _project_queryset_for_request(request)
        .filter(id=session_fields["project_id"])
        .exists()
    ):
        return None
    return session_fields


def _resolve_end_user_ids_for_user_id(
    user_id,
    *,
    org,
    org_scope,
    project_id,
    deadline: ReadDeadline | None = None,
):
    """Resolve a string ``user_id`` to the set of CH ``end_user`` UUIDs.

    The CH ``spans`` table keys users by the UUID ``end_user_id``, not the
    string ``user_id``, so a string filter must be reverse-resolved. Prefers
    the curated CH ``end_users`` dimension (state-robust across the P3b id
    cutover). ClickHouse is authoritative; an empty CH result stays empty and a
    CH failure propagates instead of consulting the stale PostgreSQL mirror.

    Returns ``(ids, display_row)`` where ``display_row`` is the first matched
    PG row's display fields (``user_id``/``user_id_type``/``user_id_hash``) or
    ``None`` — used to label the single-user (cross-project) detail page.
    """
    from tracer.services.clickhouse.v2.end_user_dict_reader import (
        resolve_end_user_ids_by_user_id,
    )

    timeout_ms = (
        deadline.remaining_ms(SESSION_LIST_ENRICHMENT_TIMEOUT_MS)
        if deadline is not None
        else None
    )
    ids = resolve_end_user_ids_by_user_id(
        user_id,
        organization_id=org.id if org else None,
        project_id=(project_id if (not org_scope and project_id) else None),
        timeout_ms=timeout_ms,
        settings=(
            _session_read_settings(max_result_rows=10_000)
            if deadline is not None
            else None
        ),
    )

    display_row = None
    if ids:
        # This finite metadata read labels the single-user detail view only; it
        # never decides ClickHouse membership. Missing/stale PG metadata simply
        # leaves the CH-derived row labels to page enrichment below.
        end_user_qs = EndUser.objects.filter(user_id=user_id)
        if org:
            end_user_qs = end_user_qs.filter(organization=org)
        if not org_scope and project_id:
            end_user_qs = end_user_qs.filter(project_id=project_id)
        display_row = end_user_qs.values(
            "id", "user_id", "user_id_type", "user_id_hash"
        ).first()
    return ids, display_row


def _soft_delete_trace_session_tree(trace_sessions):
    now = timezone.now()
    sessions = list(trace_sessions)
    if not sessions:
        return

    session_ids = [session.id for session in sessions]
    trace_ids = list(
        Trace.no_workspace_objects.filter(
            session_id__in=session_ids,
            deleted=False,
        ).values_list("id", flat=True)
    )

    if trace_ids:
        ObservationSpan.no_workspace_objects.filter(
            trace_id__in=trace_ids,
            deleted=False,
        ).update(deleted=True, deleted_at=now)
        EvalLogger.no_workspace_objects.filter(
            trace_id__in=trace_ids,
            deleted=False,
        ).update(deleted=True, deleted_at=now)
        Trace.no_workspace_objects.filter(
            id__in=trace_ids,
            deleted=False,
        ).update(deleted=True, deleted_at=now)

    EvalLogger.no_workspace_objects.filter(
        trace_session_id__in=session_ids,
        deleted=False,
    ).update(deleted=True, deleted_at=now)
    TraceSession.no_workspace_objects.filter(
        id__in=session_ids,
        deleted=False,
    ).update(deleted=True, deleted_at=now)


class TraceSessionView(BaseModelViewSetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = TraceSessionSerializer

    def _empty_session_list_response(self, project=None, *, export=False):
        if export:
            return self._gm.success_response(
                {
                    "table": {
                        "total_cost",
                        "duration",
                        "total_traces_count",
                        "start_time",
                        "end_time",
                        "first_message",
                        "last_message",
                        "session_id",
                        "created_at",
                    }
                }
            )

        return self._gm.success_response(
            {
                "metadata": {"total_rows": 0},
                "table": [],
                "config": ensure_project_session_property_identities(
                    (project.session_config if project else None)
                    or get_default_project_session_config()
                ),
            }
        )

    def get_queryset(self):
        trace_session_id = self.kwargs.get("pk")
        # Get base queryset with automatic filtering from mixin
        queryset = (
            super()
            .get_queryset()
            .filter(
                _project_workspace_scope_q(self.request),
                project__deleted=False,
            )
        )

        if trace_session_id:
            queryset = queryset.filter(id=trace_session_id)

        project_id = self.request.query_params.get("project_id")
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        return queryset

    @staticmethod
    def _upsert_overlay(trace_session_id, *, project_id, bookmarked, display_name):
        """Single write path for the TraceSessionOverlay (DESIGN §5 / §5.1).

        Used by BOTH the PG-backed ``perform_update`` (post-save mirror) and
        the CH-only write path (no PG ``TraceSession`` row). One invariant,
        one code path, no drift.
        """
        from tracer.models.trace_session import TraceSessionOverlay

        TraceSessionOverlay.objects.update_or_create(
            trace_session_id=trace_session_id,
            defaults={
                "project_id": project_id,
                "bookmarked": bookmarked,
                "display_name": display_name,
            },
        )

    def perform_update(self, serializer):
        """Persist a TraceSession update AND mirror the UI overlay (slice 2b).

        The overlay fields are read from the POST-SAVE instance, never from
        ``validated_data``: a partial PATCH (e.g. only ``{"bookmarked": true}``)
        carries no ``name``, so sourcing ``display_name`` from ``validated_data``
        would clobber an existing rename. ``instance`` always reflects the full
        current state.
        """
        with transaction.atomic():
            instance = serializer.save()
            self._upsert_overlay(
                instance.id,
                project_id=instance.project_id,
                bookmarked=instance.bookmarked,
                display_name=instance.name,
            )

    @staticmethod
    def _build_update_response(session_id, *, project_id, bookmarked, name, created_at):
        """Shared response builder for PATCH — same shape from PG and CH paths.

        Uses ``TraceSessionSerializer``'s ``DateTimeField`` to format
        ``created_at`` so both paths produce the same ISO representation.
        """
        from rest_framework.fields import DateTimeField

        dt_field = DateTimeField()
        return Response(
            {
                "id": str(session_id),
                "project": str(project_id),
                "bookmarked": bookmarked,
                "name": name,
                "created_at": dt_field.to_representation(created_at),
            }
        )

    def update(self, request, *args, **kwargs):
        # Narrow the Http404 catch to the object lookup ONLY. Any 404 from
        # validation, signals, or nested lookups must propagate normally.
        partial = kwargs.get("partial", False)
        try:
            instance = self.get_object()
        except Http404:
            return self._update_ch_only_session(request, partial=partial)
        # PG path — standard DRF validate+save, then the shared response
        # builder so both paths produce byte-identical JSON shapes.
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance = serializer.instance
        return self._build_update_response(
            instance.id,
            project_id=instance.project_id,
            bookmarked=instance.bookmarked,
            name=instance.name,
            created_at=instance.created_at,
        )

    def _update_ch_only_session(self, request, *, partial):
        """Overlay-only write path for a CH-only (collector) session."""
        trace_session_id = self.kwargs.get("pk")
        session_fields = _resolve_ch_session_fields(request, trace_session_id)
        if not session_fields:
            raise Http404("Trace session not found")

        serializer = self.get_serializer(data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        project_id = session_fields["project_id"]

        current_bookmarked = bool(session_fields.get("bookmarked"))
        current_name = session_fields.get("display_name")
        new_bookmarked = (
            validated["bookmarked"]
            if validated.get("bookmarked") is not None
            else current_bookmarked
        )
        new_name = validated.get("name", current_name)

        with transaction.atomic():
            self._upsert_overlay(
                trace_session_id,
                project_id=project_id,
                bookmarked=new_bookmarked,
                display_name=new_name,
            )

        return self._build_update_response(
            trace_session_id,
            project_id=project_id,
            bookmarked=new_bookmarked,
            name=new_name,
            created_at=session_fields.get("first_seen"),
        )

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
        except Http404:
            return self._destroy_ch_only_session(request)
        self.perform_destroy(instance)
        return Response(status=drf_status.HTTP_204_NO_CONTENT)

    def _destroy_ch_only_session(self, request):
        """Soft-delete a CH-only (collector) session under the same tenant gate."""
        from datetime import UTC, datetime

        from tracer.models.trace_session import TraceSessionOverlay

        trace_session_id = self.kwargs.get("pk")
        session_fields = _resolve_ch_session_fields(request, trace_session_id)
        if not session_fields:
            raise Http404("Trace session not found")

        project_id = session_fields["project_id"]

        TraceSessionOverlay.objects.filter(trace_session_id=trace_session_id).delete()

        # Mark as deleted in the CH trace_sessions RMT: INSERT a row with
        # is_deleted=1 and a newer version so FINAL picks it up.
        from tracer.services.clickhouse.v2.curated_writer import (
            _TRACE_SESSION_COLUMNS,
            _get_client,
            _reset_client,
        )

        try:
            client = _get_client()
            now = datetime.now(UTC)
            row = [
                str(project_id),
                str(trace_session_id),
                session_fields.get("external_session_id", ""),
                session_fields.get("first_seen", now),
                now,  # version — newer than the live row
                1,  # is_deleted
            ]
            client.insert(
                "trace_sessions", [row], column_names=list(_TRACE_SESSION_COLUMNS)
            )
        except Exception as e:
            _reset_client()
            logger.warning(
                "ch_only_session_delete_marker_failed",
                trace_session_id=str(trace_session_id),
                error=str(e)[:200],
            )

        return Response(status=drf_status.HTTP_204_NO_CONTENT)

    def perform_destroy(self, instance):
        _soft_delete_trace_session_tree([instance])

    def retrieve(self, request, *args, **kwargs):
        try:
            query_serializer = TraceSessionRetrieveQuerySerializer(
                data=request.query_params
            )
            if not query_serializer.is_valid():
                return self._gm.bad_request(query_serializer.errors)
            query_data = query_serializer.validated_data

            trace_session_id = self.kwargs.get("pk")

            analytics = V2AnalyticsQueryService()

            try:
                trace_session = self.get_queryset().get(id=trace_session_id)
                project_id = trace_session.project.id
            except TraceSession.DoesNotExist:
                session_fields = _resolve_ch_session_fields(request, trace_session_id)
                if not session_fields:
                    return self._gm.bad_request("Session not found.")
                project_id = session_fields["project_id"]

            return self._retrieve_clickhouse(
                request,
                trace_session_id,
                project_id,
                analytics,
                query_data,
            )
        except OperationalError as e:
            logger.exception(
                "trace_session_retrieve_timeout",
                session_id=str(self.kwargs.get("pk")),
                error=str(e),
            )
            return Response(
                {
                    "status": False,
                    "result": (
                        "Session detail unavailable: query exceeded time "
                        "budget. Retry shortly."
                    ),
                },
                status=drf_status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except Exception as e:
            logger.exception(
                "trace_session_retrieve_failed",
                session_id=str(self.kwargs.get("pk")),
                error=str(e),
            )
            return self._gm.bad_request("Error retrieving trace session.")

    def _retrieve_clickhouse(
        self,
        request,
        trace_session_id,
        project_id,
        analytics,
        query_data,
    ):
        """Retrieve session detail from ClickHouse."""
        page_number = query_data["page_number"]
        page_size = query_data["page_size"]
        page_start = page_number * page_size
        read_deadline = ReadDeadline.start(SESSION_LIST_WALL_DEADLINE_MS)

        # P3b step1.5: resolve the session's canonical ID and expand it to
        # all group member IDs (old + new). Use IN (...) instead of the heavy
        # LEFT JOIN (survivor_map_subquery) to avoid OOM on large remap tables.
        requested_session_id = str(trace_session_id)
        canonical_session_id = _resolve_session_ids_to_canonical(
            analytics,
            [requested_session_id],
            deadline=read_deadline,
        ).get(requested_session_id, requested_session_id)
        session_group_ids = _expand_session_group(
            analytics,
            canonical_session_id,
            deadline=read_deadline,
        )

        # Get session-level aggregates from CH
        agg_query = """
            SELECT
                min(start_time) AS session_start,
                max(end_time) AS session_end,
                round(sum(cost), 6) AS total_cost,
                sum(total_tokens) AS total_tokens,
                count(DISTINCT trace_id) AS total_traces,
                toString(argMaxIf(end_user_id, start_time, isNotNull(end_user_id) AND end_user_id != toUUID('00000000-0000-0000-0000-000000000000'))) AS end_user_id
            FROM spans
            WHERE project_id = %(project_id)s
              AND trace_session_id IN %(session_group_ids)s
              AND is_deleted = 0
        """
        agg_result = analytics.execute_ch_query(
            agg_query,
            {"project_id": str(project_id), "session_group_ids": session_group_ids},
            timeout_ms=read_deadline.remaining_ms(5_000),
        )

        agg = agg_result.data[0] if agg_result.data else {}
        session_start = agg.get("session_start")
        session_end = agg.get("session_end")
        duration = 0
        if session_start and session_end:
            try:
                duration = (session_end - session_start).total_seconds()
            except (TypeError, AttributeError):
                duration = 0

        end_user_id = agg.get("end_user_id") or ""
        null_uuid = "00000000-0000-0000-0000-000000000000"
        user_id_label = None
        if end_user_id and end_user_id != null_uuid:
            try:
                from tracer.services.clickhouse.v2.end_user_dict_reader import (
                    resolve_end_user_fields,
                )

                user_map = resolve_end_user_fields(
                    [end_user_id],
                    timeout_ms=read_deadline.remaining_ms(1_500),
                    settings=_session_read_settings(max_result_rows=1),
                )
                user_id_label = (user_map.get(end_user_id) or {}).get("user_id")
            except Exception:
                logger.debug(
                    "session_retrieve_user_id_resolve_failed",
                    end_user_id=end_user_id,
                )

        session_metadata = {
            "session_id": str(trace_session_id),
            "duration": duration,
            "total_cost": agg.get("total_cost", 0) or 0,
            "total_traces": agg.get("total_traces", 0),
            "start_time": format_datetime_to_iso(session_start),
            "end_time": format_datetime_to_iso(session_end),
            "total_tokens": agg.get("total_tokens", 0),
            "user_id": user_id_label,
        }

        # Get paginated trace data from CH
        trace_query = """
            SELECT
                toString(trace_id) AS trace_id,
                any(input) AS input,
                any(output) AS output,
                min(CASE WHEN parent_span_id IS NULL OR parent_span_id = '' THEN latency_ms ELSE NULL END) AS root_latency_ms,
                round(sum(cost), 6) AS total_cost,
                min(start_time) AS trace_min_start_time,
                sum(total_tokens) AS total_tokens,
                sum(prompt_tokens) AS input_tokens,
                sum(completion_tokens) AS output_tokens
            FROM spans
            WHERE project_id = %(project_id)s
              AND trace_session_id IN %(session_group_ids)s
              AND is_deleted = 0
            GROUP BY trace_id
            ORDER BY trace_min_start_time ASC
            LIMIT %(limit)s
            OFFSET %(offset)s
        """
        trace_result = analytics.execute_ch_query(
            trace_query,
            {
                "project_id": str(project_id),
                "session_group_ids": session_group_ids,
                "limit": page_size + 1,
                "offset": page_start,
            },
            timeout_ms=read_deadline.remaining_ms(SESSION_LIST_QUERY_TIMEOUT_MS),
        )

        has_next = len(trace_result.data) > page_size
        traces_data = trace_result.data[:page_size]

        if not traces_data:
            next_session_id, previous_session_id = get_session_navigation(
                request,
                project_id,
                trace_session_id,
                query_data,
                deadline=read_deadline,
            )
            session_metadata["next_session_id"] = next_session_id
            session_metadata["previous_session_id"] = previous_session_id
            return self._gm.success_response(
                {
                    "session_metadata": session_metadata,
                    "response": [],
                    "next": False,
                }
            )

        # The eval table has no project_id and customer trace IDs are not
        # globally unique. Resolve the project's config IDs in Postgres first,
        # then require both that candidate set and the page's trace IDs during
        # CH discovery. A bare trace-id read could otherwise discover another
        # project's eval when both projects use the same trace ID.
        trace_ids = [r["trace_id"] for r in traces_data]
        eval_configs: list = []
        if trace_ids:
            candidate_config_ids = [
                str(config_id)
                for config_id in CustomEvalConfig.objects.filter(
                    project_id=project_id,
                    deleted=False,
                ).values_list("id", flat=True)
            ]
            # A CH read failure must surface (via retrieve()'s outer error
            # handler), not fail open to "this session has no eval scores".
            pre_config_ids = analytics.get_eval_config_ids_for_traces_ch(
                trace_ids,
                candidate_config_ids,
                timeout_ms=read_deadline.remaining_ms(3_000),
            )
            if pre_config_ids:
                eval_configs = list(
                    CustomEvalConfig.objects.filter(
                        project_id=project_id,
                        id__in=pre_config_ids,
                        deleted=False,
                    ).select_related("eval_template")
                )

        eval_map = {}
        if eval_configs and trace_ids:
            config_ids = [str(c.id) for c in eval_configs]
            eval_rows = analytics.get_trace_eval_scores_ch(
                trace_ids,
                config_ids,
                timeout_ms=read_deadline.remaining_ms(5_000),
            )
            for row in eval_rows:
                key = (row["trace_id"], row["config_id"])
                if row.get("float_count", 0) > 0:
                    eval_map[key] = {"score": row["float_score"], "type": "float"}
                elif row.get("bool_count", 0) > 0:
                    eval_map[key] = {"score": row["bool_score"], "type": "bool"}
                elif (row.get("error_count", 0) or 0) > 0:
                    # No completed score but the eval errored — surface an error
                    # marker (errored wins over the non-terminal states).
                    eval_map[key] = {"type": "error"}
                else:
                    # No completed score: surface the eval's lifecycle status
                    # (skipped > running > pending) so the cell renders a
                    # loading / pending / skipped state instead of vanishing.
                    marker = non_terminal_eval_marker(row)
                    if marker is not None:
                        eval_map[key] = {"type": "status", **marker}

        response = []
        for trace_row in traces_data:
            trace_id_str = trace_row["trace_id"]
            result = {
                "trace_id": trace_id_str,
                "input": trace_row.get("input"),
                "output": trace_row.get("output"),
                "system_metrics": {
                    "total_latency_ms": trace_row.get("root_latency_ms", 0),
                    "user_id": user_id_label,
                    "total_cost": trace_row.get("total_cost", 0),
                    "start_time": format_datetime_to_iso(
                        trace_row.get("trace_min_start_time")
                    ),
                    "total_tokens": trace_row.get("total_tokens", 0),
                    "input_tokens": trace_row.get("input_tokens", 0),
                    "output_tokens": trace_row.get("output_tokens", 0),
                },
            }

            eval_metrics = {}
            for config in eval_configs:
                config_id_str = str(config.id)
                key = (trace_id_str, config_id_str)
                data = eval_map.get(key)
                if data and data["type"] in ("float", "bool"):
                    eval_metrics[config_id_str] = {
                        "score": data["score"],
                        "name": config.name,
                        "explanation": None,
                    }
                elif data and data["type"] == "error":
                    eval_metrics[config_id_str] = {
                        "score": None,
                        "name": config.name,
                        "explanation": None,
                        "error": True,
                    }
                elif data and data["type"] == "status":
                    entry = {
                        "score": None,
                        "name": config.name,
                        "explanation": data.get("skipped_reason"),
                        "status": data["status"],
                    }
                    if data.get("skipped_reason"):
                        entry["skipped_reason"] = data["skipped_reason"]
                    eval_metrics[config_id_str] = entry

            result["evals_metrics"] = eval_metrics
            response.append(result)

        next_session_id, previous_session_id = get_session_navigation(
            request,
            project_id,
            trace_session_id,
            query_data,
            deadline=read_deadline,
        )
        session_metadata["next_session_id"] = next_session_id
        session_metadata["previous_session_id"] = previous_session_id

        return self._gm.success_response(
            {
                "session_metadata": session_metadata,
                "response": response,
                "next": has_next,
            }
        )

    @validated_request(
        query_serializer=TraceSessionFilterValuesQuerySerializer,
        responses={
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"], pagination_class=None)
    def get_session_filter_values(self, request, *args, **kwargs):
        """
        Return distinct values for a session-level column.
        Used by the filter panel's value picker for session-specific fields
        (session_id, user_id, first_message, etc.).

        Query params:
            project_id: required
            column: canonical session column name, e.g. "session_id"
            search: optional search substring
            page: page number (0-based), default 0
            page_size: default 50
        """
        read_deadline = ReadDeadline.start(SESSION_FILTER_VALUE_WALL_DEADLINE_MS)
        try:
            query_serializer = TraceSessionFilterValuesQuerySerializer(
                data=request.query_params
            )
            if not query_serializer.is_valid():
                return self._gm.bad_request(query_serializer.errors)

            query_params = query_serializer.validated_data
            project_id = str(query_params["project_id"])
            if not _read_session_filter_project_in_scope(
                request=request,
                project_id=project_id,
                deadline=read_deadline,
            ):
                return self._gm.bad_request("Project not found")
            column = query_params["column"]
            search = query_params.get("search", "")
            page = query_params.get("page", 0)
            page_size = query_params.get("page_size", 50)

            # Map frontend column names to ClickHouse expressions
            COLUMN_MAP = {
                "session_id": "trace_session_id",
                "user_id": "user_id",
                "first_message": "first_message",
                "last_message": "last_message",
            }

            ch_column = COLUMN_MAP.get(column)
            if not ch_column:
                return self._gm.bad_request("Unsupported session filter column.")

            # Every picker branch reads the direct-write CH25 cluster.  Do not
            # route session dimensions through the legacy analytics client.
            analytics = V2AnalyticsQueryService()

            if ch_column == "trace_session_id":
                from tracer.services.clickhouse.v2.id_remap_sql import (
                    remap_left_join,
                    resolved_id_expr,
                )

                # Resolve new→old through trace_session_id_remap and group by
                # the survivor id so a cross-cutover straddler (which has BOTH
                # an old and a deterministic row in trace_sessions) is listed
                # ONCE — matching the span-backed value paths below. Reading
                # trace_sessions directly would surface both rows as duplicates.
                search_clause = (
                    "AND (external_session_id ILIKE %(search)s "
                    "OR toString(trace_session_id) ILIKE %(search)s)"
                    if search
                    else ""
                )
                ts_join = remap_left_join(
                    "ts.trace_session_id", "trace_session_id_remap", "ts_remap"
                )
                resolved_ts = resolved_id_expr("ts.trace_session_id", "ts_remap")
                query = f"""
                SELECT
                    toString(val_id) AS val,
                    any(label) AS label
                FROM (
                    SELECT
                        {resolved_ts} AS val_id,
                        ts.external_session_id AS label
                    FROM (
                        SELECT trace_session_id, external_session_id
                        FROM trace_sessions FINAL
                        WHERE project_id = %(project_id)s
                          AND is_deleted = 0
                          {search_clause}
                    ) AS ts
                    {ts_join}
                )
                WHERE val_id != toUUID('{NIL_UUID}')
                GROUP BY val_id
                ORDER BY label, val
                LIMIT %(limit)s OFFSET %(offset)s
                """
                result = analytics.execute_ch_query(
                    query,
                    {
                        "project_id": project_id,
                        "limit": page_size + 1,
                        "offset": page * page_size,
                        **({"search": f"%{search}%"} if search else {}),
                    },
                    timeout_ms=read_deadline.remaining_ms(
                        SESSION_LIST_QUERY_TIMEOUT_MS
                    ),
                    settings={
                        **_session_read_settings(max_result_rows=page_size + 1),
                    },
                )
                read_deadline.remaining_ms(floor_ms=1)
                has_more = len(result.data) > page_size
                page_rows = result.data[:page_size]
                session_ids = [str(row["val"]) for row in page_rows]
                from tracer.services.clickhouse.v2.trace_session_dict_reader import (
                    resolve_session_fields,
                )

                session_fields = resolve_session_fields(
                    session_ids,
                    project_id=project_id,
                    deadline=read_deadline,
                )
                values = []
                for row in page_rows:
                    value = str(row["val"])
                    fields = session_fields.get(value, {})
                    label = (
                        fields.get("display_name")
                        or fields.get("external_session_id")
                        or row.get("label")
                        or value
                    )
                    values.append({"value": value, "label": str(label)})
                read_deadline.remaining_ms(floor_ms=1)
                return self._gm.success_response({"values": values, "next": has_more})

            if ch_column in ("first_message", "last_message"):
                try:
                    value_read = read_session_message_filter_values(
                        analytics,
                        project_id=project_id,
                        message_position=(
                            "first" if ch_column == "first_message" else "last"
                        ),
                        search=search,
                        page=page,
                        page_size=page_size,
                    )
                except Exception as exc:
                    if not is_clickhouse_api_read_unavailable_error(exc):
                        raise
                    session_logger.warning(
                        "session_message_filter_values_unavailable",
                        column=column,
                        error_type=type(exc).__name__,
                    )
                    return self._gm.custom_error_response(
                        drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Session filter values are temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                value_metadata = value_read.metadata()
                if not value_read.query_complete and (
                    value_read.query_error_code != "sample_limit"
                    or not value_read.values
                ):
                    session_logger.warning(
                        "session_message_filter_values_incomplete",
                        column=column,
                        error_code=value_read.query_error_code,
                    )
                    return self._gm.custom_error_response(
                        drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Session filter values are temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                return self._gm.success_response(
                    {
                        "values": list(value_read.values),
                        "next": value_read.has_more,
                        **value_metadata,
                    }
                )

            # User labels come from the curated CH end_users dimension.

            if ch_column == "user_id":
                search_clause = "AND user_id ILIKE %(search)s" if search else ""
                query = f"""
                SELECT DISTINCT user_id AS val
                FROM end_users FINAL
                WHERE project_id = %(project_id)s
                  AND is_deleted = 0
                  AND user_id != ''
                  {search_clause}
                ORDER BY val
                LIMIT %(limit)s OFFSET %(offset)s
                """
            params = {
                "project_id": project_id,
                "limit": page_size + 1,
                "offset": page * page_size,
            }
            if search:
                params["search"] = f"%{search}%"

            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=read_deadline.remaining_ms(SESSION_LIST_QUERY_TIMEOUT_MS),
                settings={
                    **_session_read_settings(max_result_rows=page_size + 1),
                },
            )
            read_deadline.remaining_ms(floor_ms=1)
            has_more = len(result.data) > page_size
            page_rows = result.data[:page_size]
            values = [
                str(row.get("val", "") if isinstance(row, dict) else row[0])
                for row in page_rows
                if (row.get("val") if isinstance(row, dict) else row[0])
            ]
            read_deadline.remaining_ms(floor_ms=1)
            return self._gm.success_response({"values": values, "next": has_more})

        except Exception as exc:
            if is_read_budget_error(exc):
                session_logger.warning(
                    "session_filter_values_deadline_exceeded",
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Session filter values are temporarily unavailable. Please retry.",
                    code="read_budget_exceeded",
                )
            if is_clickhouse_api_read_unavailable_error(exc):
                session_logger.warning(
                    "session_filter_values_query_unavailable",
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Session filter values are temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            session_logger.exception(
                "session_filter_values_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                drf_status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Session filter values could not be loaded",
                code="server_error",
            )

    @_bounded_session_graph_request
    @validated_request(
        query_serializer=ObserveGraphDataQuerySerializer,
        request_serializer=TraceSessionGraphDataRequestSerializer,
        responses={
            200: ObserveGraphDataResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ObserveGraphDataErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["post"])
    def get_session_graph_data(self, request, *args, **kwargs):
        """
        Fetch time-series session metrics for the observe graph.

        Supports the same metric types as the trace graph endpoint:
        - SYSTEM_METRIC: latency, tokens, cost, error_rate, session_count,
          avg_duration, avg_traces_per_session — all aggregated at session level
        - EVAL: eval scores averaged across sessions
        - ANNOTATION: annotation scores averaged across sessions

        Response shape matches trace graph: {metric_name, data: [{timestamp, value, primary_traffic}]}
        """
        deadline = kwargs.pop("_graph_action_deadline", None)
        deadline_injected = deadline is not None
        if deadline is None:
            deadline = start_graph_action_deadline()

        def finish(response):
            if deadline_injected:
                return response
            return finish_graph_action_response(deadline, response)

        try:
            body = request.validated_data
            refresh = request.validated_query_data.get("refresh", False)
            project_id = str(body["project_id"])
            with graph_action_postgres_budget(deadline):
                project = _project_queryset_for_request(request).get(id=project_id)
            req_data_config = body["req_data_config"]
            try:
                validate_property_graph_namespace(
                    req_data_config.get("property_id"),
                    expected_definition_source="sessions",
                )
            except ValueError:
                return finish(
                    self._gm.bad_request(
                        "property_id is not valid for this graph endpoint"
                    )
                )
            metric_type = req_data_config.get("type")
            metric_id = str(req_data_config.get("id") or "session_count")
            if project.trace_type != "observe":
                return finish(
                    self._gm.bad_request(
                        "Project id is required and project should be of type observe"
                    )
                )
            if metric_type not in {"SYSTEM_METRIC", "EVAL", "ANNOTATION"}:
                return finish(self._gm.bad_request("Filter property type is not valid"))
            if (
                metric_type == "SYSTEM_METRIC"
                and metric_id not in SESSION_SYSTEM_METRICS
            ):
                return finish(self._gm.bad_request("Session graph metric is not valid"))
            # PostgreSQL remains authoritative only for this small config ownership
            # record. Session, trace, span, eval-result, and annotation telemetry
            # below are read exclusively from direct-write CH25.
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

            filters = bind_request_my_annotations_principal(
                request,
                body["filters"],
            )
            filters = graph_execution_filters(filters)
            try:
                graph = fetch_session_graph_ch(
                    analytics=V2AnalyticsQueryService(),
                    project_id=project_id,
                    filters=filters,
                    interval=body["interval"],
                    req_data_config=req_data_config,
                    refresh=refresh,
                    wall_deadline_ms=graph_action_remaining_ms(deadline),
                    organization_id=str(project.organization_id),
                    workspace_id=(
                        str(request.workspace.id)
                        if getattr(request, "workspace", None)
                        else None
                    ),
                )
            except Exception as exc:
                if isinstance(exc, BoundedGraphReadError):
                    if exc.error_code == "unsupported_filter_shape":
                        raise UnsupportedFilterShapeError(
                            "session graph filter configuration is invalid"
                        ) from None
                    if not (
                        exc.error_code in SESSION_GRAPH_RETRYABLE_ERROR_CODES
                        or exc.retryable
                    ):
                        raise
                elif not is_clickhouse_api_read_unavailable_error(exc):
                    raise
                session_logger.warning(
                    "session_graph_query_degraded",
                    project_id=project_id,
                    metric_type=metric_type,
                    metric_id=metric_id,
                    error_type=type(exc).__name__,
                )
                graph = degraded_graph_response(metric_id, exc)

            graph.update(
                graph_query_evidence(
                    project_id=project_id,
                    observe_type="session",
                    filters=filters,
                )
            )
            graph = enforce_exact_graph_data_contract(graph)

            # A fully executed, explicitly labelled sample is a usable bounded
            # graph response. Partial/failed reads remain a typed retryable error
            # with no ClickHouse diagnostics exposed to the frontend.
            if not graph_payload_is_publishable(
                graph,
                allow_sampled=False,
            ):
                graph = {
                    **graph,
                    "data": [],
                    "query_complete": False,
                    "query_status": "degraded",
                    "query_sampled": False,
                    "message": (
                        "Session graph data is temporarily unavailable. Please retry."
                    ),
                }
                return finish(
                    self._gm.custom_error_response(
                        drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                        graph,
                        code="service_unavailable",
                    )
                )
            return finish(self._gm.success_response(graph))
        except GraphActionUnavailable:
            if deadline_injected:
                raise
            session_logger.warning(
                "session_graph_action_deadline_exceeded",
                project_id=str(
                    getattr(request, "validated_data", {}).get("project_id", "")
                ),
            )
            return self._gm.custom_error_response(
                drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                "Session graph data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Project.DoesNotExist:
            return self._gm.bad_request("Project not found")
        except UnsupportedFilterShapeError:
            return self._gm.bad_request("Graph filter configuration is invalid")
        except FilterPrincipalContextError as exc:
            return self._gm.bad_request(str(exc))
        except Exception as exc:
            bounded_retryable = isinstance(exc, BoundedGraphReadError) and (
                exc.error_code in SESSION_GRAPH_RETRYABLE_ERROR_CODES or exc.retryable
            )
            if bounded_retryable or is_clickhouse_api_read_unavailable_error(exc):
                session_logger.warning(
                    "session_graph_query_unavailable",
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Session graph data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            session_logger.exception(
                "session_graph_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                drf_status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Session graph data could not be loaded",
                code="server_error",
            )

    @_bounded_session_list_request
    @validated_request(
        query_serializer=TraceSessionListQuerySerializer,
        responses={
            200: TraceSessionListResponseSerializer,
            400: ApiErrorResponseSerializer,
            422: PageDepthExceededErrorSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def list_sessions(self, request, *args, **kwargs):
        """
        List traces filtered by project ID and project version ID with optimized queries.
        """
        try:
            read_deadline = kwargs.pop("_session_list_read_deadline", None)
            if read_deadline is None:
                # ``inspect.unwrap``-based unit boundaries intentionally call
                # this original method without its public wrapper. Preserve a
                # finite CH/enrichment budget for that path instead of raising
                # KeyError; real HTTP calls always receive the outer wrapper's
                # earlier deadline and PostgreSQL execute wrapper.
                read_deadline = ReadDeadline.start(SESSION_LIST_WALL_DEADLINE_MS)
            validated_data = dict(request.validated_query_data)
            if kwargs.get("bounded_export"):
                validated_data.update(
                    page_number=0,
                    page_size=BOUNDED_SESSION_EXPORT_PAGE_SIZE,
                    cursor_mode=True,
                )
            validated_data["filters"] = bind_request_my_annotations_principal(
                request,
                validated_data.get("filters", []),
            )
            filters = list(validated_data.get("filters", []) or [])
            has_non_time_filter = any(
                isinstance(item, dict)
                and (item.get("column_id") or item.get("columnId"))
                not in {"created_at", "start_time"}
                for item in filters
            )
            has_filtered_membership = bool(validated_data.get("user_id")) or (
                validated_data.get("bookmarked") is not None
            )
            page_number = validated_data.get("page_number", 0)
            page_size = validated_data.get("page_size", 30)
            if numbered_page_depth_exceeded(
                page_number=page_number,
                page_size=page_size,
            ) or (
                (has_non_time_filter or has_filtered_membership)
                and bounded_numbered_page_depth_exceeded(
                    page_number=page_number,
                    page_size=page_size,
                    max_candidates=SESSION_LIST_FILTER_MAX_CANDIDATES,
                    classify_batch_size=(
                        SessionListQueryBuilderV2.recommended_filter_classify_batch_size()
                    ),
                    seed_batch_size=(
                        SessionListQueryBuilderV2.recommended_filter_seed_batch_size()
                    ),
                    max_seed_attempts=SESSION_LIST_FILTER_MAX_SEED_ATTEMPTS,
                    max_query_count=SESSION_LIST_FILTER_MAX_QUERIES,
                )
            ):
                session_logger.info(
                    "session_list_page_depth_exceeded",
                    page_number=page_number,
                    page_size=page_size,
                )
                return self._gm.custom_error_response(
                    drf_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    PAGE_DEPTH_EXCEEDED_MESSAGE,
                    code=PAGE_DEPTH_EXCEEDED_CODE,
                )
            export = kwargs.get("export", False) if kwargs else False
            project_id = (
                str(validated_data["project_id"])
                if validated_data.get("project_id")
                else None
            )

            org = (
                getattr(self.request, "organization", None)
                or self.request.user.organization
            )

            # Org-scoped mode: when no project_id is supplied, list sessions
            # from every project in the org. Used by the cross-project user
            # detail page.
            org_scope = not project_id
            if org_scope:
                org_project_ids = list(
                    _project_queryset_for_request(request)
                    .filter(
                        deleted=False,
                        trace_type__in=("observe", "experiment"),
                    )
                    .exclude(source=ProjectSourceChoices.SIMULATOR.value)
                    .values_list("id", flat=True)
                )
                project = None
                # An organization with no Observe/Experiment projects has an
                # exact empty result. Do not construct a CH builder with
                # ``project_id=None`` (which stringifies to the invalid
                # ``"None"`` scope) or issue any database query.
                if not org_project_ids:
                    return self._empty_session_list_response(export=export)
            else:
                project = _project_queryset_for_request(request).get(id=project_id)
                if project.source == ProjectSourceChoices.SIMULATOR.value:
                    return self._empty_session_list_response(project, export=export)
                org_project_ids = None

            # The physical ClickHouse timestamp is non-null. A valid datetime
            # contradiction (including ``is_null``) is therefore an exact
            # empty result and must not spend the session endpoint's read
            # budget on bookmark resolution, candidate selection, or counts.
            if BaseQueryBuilder.analyze_bounded_datetime_filters(
                filters,
                strict=True,
            ).empty:
                return self._empty_session_list_response(project, export=export)

            # Sessions are direct-write CH25 telemetry. Bind the endpoint and
            # its bookmark-remap read to the matching service explicitly; the
            # legacy CDC builder/service is not a fallback or routing option.
            analytics = V2AnalyticsQueryService()
            bookmarked = validated_data.get("bookmarked")

            # CH-derived-dimensions cutover (DESIGN §5.2): the ``bookmarked``
            # filter no longer forces the whole read onto PG. ``bookmarked`` is a
            # THREE-state flag (None = no filter, True = bookmarked only, False =
            # NON-bookmarked only). For True/False we resolve the bookmarked
            # ``trace_session_id``s from the small PG ``TraceSessionOverlay``
            # (the UI-sourced overlay) and pass them to the CH path as a synthetic
            # ``trace_session_id IN (…)`` / ``NOT IN (…)`` filter — the same
            # synthetic-id trick the user_id scoping already uses. The whole list
            # then runs through the existing CH builder for all three states; the
            # PG aggregate branch below is the CH-failure fallback only.
            bookmark_filter = self._build_bookmark_filter(
                bookmarked,
                org_project_ids if org_scope else [project_id],
                analytics=analytics,
                deadline=read_deadline,
            )
            try:
                return self._list_sessions_clickhouse(
                    request,
                    project_id,
                    project,
                    analytics,
                    validated_data,
                    org_project_ids=org_project_ids,
                    bookmark_filter=bookmark_filter,
                    read_deadline=read_deadline,
                    export=export,
                )
            except UnsupportedFilterShapeError:
                raise
            except Exception as e:
                logger.exception(
                    "ClickHouse session-list failed",
                    error_type=type(e).__name__,
                )
                if not (is_read_budget_error(e) or is_clickhouse_query_error(e)):
                    raise
                # ClickHouse is the sole span/session fact source. Falling back
                # to the incomplete PG mirror both changes results and repeats
                # the incident's broad query under load. Fail closed with a
                # sanitized retryable response instead.
                return self._gm.custom_error_response(
                    drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Session data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )

            filters = validated_data.get("filters", [])
            sort_params = validated_data.get("sort_params", [])

            trace_sessions_qs = (
                TraceSession.objects.filter(project_id__in=org_project_ids)
                if org_scope
                else TraceSession.objects.filter(project_id=project_id)
            )
            if bookmarked is not None:
                trace_sessions_qs = trace_sessions_qs.filter(bookmarked=bookmarked)
            trace_sessions_qs, remaining_filters = apply_created_at_filters(
                trace_sessions_qs, filters
            )

            if not trace_sessions_qs.exists():
                return self._empty_session_list_response(project, export=export)

            session_ids = trace_sessions_qs.values("id")

            user_id = validated_data.get("user_id") or None

            end_user_filter = {}
            if user_id:
                # In org-scoped mode the same user_id may have multiple
                # EndUser rows (one per project) — match all of them.
                if org_scope:
                    end_user_qs = EndUser.objects.filter(
                        user_id=user_id,
                        organization=org,
                        deleted=False,
                    )
                    if not end_user_qs.exists():
                        raise Exception("User not found")
                    end_user_filter["end_user__in"] = list(end_user_qs)
                else:
                    try:
                        end_user = EndUser.objects.get(
                            user_id=user_id,
                            organization=org,
                            deleted=False,
                            project=project,
                        )
                        end_user_filter["end_user"] = end_user
                    except EndUser.DoesNotExist:
                        raise Exception("User not found")  # noqa: B904

            # In org-scoped mode with a user filter, narrow session_ids to
            # only those linked to this user's spans BEFORE the heavy
            # aggregation. Without this, the GROUP BY scans every session
            # in every org project and exceeds PG's 30s statement_timeout.
            # In single-project mode session_ids is already bounded by
            # project_id, so the planner handles it without help.
            if org_scope and end_user_filter:
                user_session_ids = list(
                    ObservationSpan.objects.filter(
                        trace__session_id__in=session_ids,
                        **end_user_filter,
                    )
                    .values_list("trace__session_id", flat=True)
                    .distinct()
                )
                if not user_session_ids:
                    return self._gm.success_response(
                        {
                            "metadata": {"total_rows": 0},
                            "table": [],
                            "config": get_default_project_session_config(),
                        }
                    )
                session_ids = TraceSession.objects.filter(
                    id__in=user_session_ids
                ).values("id")

            fm_lm_columns = {"first_message", "last_message"}
            needs_first_last = any(
                f.get("column_id") in fm_lm_columns for f in remaining_filters
            ) or any(s.get("column_id") in fm_lm_columns for s in sort_params)

            pre_agg_fields = {"user_id": "end_user__user_id"}
            pre_agg_q = FilterEngine.get_filter_conditions_for_system_metrics(
                [f for f in remaining_filters if f.get("column_id") in pre_agg_fields],
                field_map=pre_agg_fields,
            )
            remaining_filters = [
                f for f in remaining_filters if f.get("column_id") not in pre_agg_fields
            ]

            base_query = (
                ObservationSpan.objects.filter(
                    pre_agg_q, trace__session_id__in=session_ids, **end_user_filter
                )
                .values("trace__session_id")
                .annotate(
                    start_time=Min("start_time"),
                    end_time=Max("end_time"),
                    total_cost=Coalesce(
                        Round(Sum("cost", output_field=FloatField()), 6),
                        0.0,
                    ),
                    total_tokens=Coalesce(
                        Sum(
                            F("total_tokens"),
                            output_field=models.IntegerField(),
                        ),
                        0,
                    ),
                    traces_count=Count("trace_id", distinct=True),
                    session_created_at=Min("trace__session__created_at"),
                )
                .annotate(
                    duration_val=ExpressionWrapper(
                        F("end_time") - F("start_time"),
                        output_field=DurationField(),
                    ),
                )
            )

            if needs_first_last:
                base_query = base_query.annotate(
                    first_message=Subquery(
                        ObservationSpan.objects.filter(
                            trace__session_id=OuterRef("trace__session_id"),
                            **end_user_filter,
                        )
                        .order_by("start_time")
                        .values("input")[:1]
                    ),
                    last_message=Subquery(
                        ObservationSpan.objects.filter(
                            trace__session_id=OuterRef("trace__session_id"),
                            **end_user_filter,
                        )
                        .order_by("-start_time")
                        .values("input")[:1]
                    ),
                )

            session_field_map = {
                "total_cost": "total_cost",
                "total_tokens": "total_tokens",
                "total_traces_count": "traces_count",
                "start_time": "start_time",
                "end_time": "end_time",
                "created_at": "session_created_at",
                "session_id": "trace__session_id",
                "duration": "duration_val",
                "first_message": "first_message",
                "last_message": "last_message",
            }

            # Separate score filters from system metric filters
            score_label_ids = (
                {
                    str(label.id)
                    for label in AnnotationsLabels.objects.filter(
                        project_id=project_id, deleted=False
                    )
                }
                if remaining_filters
                else set()
            )
            system_filters = []
            score_filters = []
            for f in remaining_filters:
                col_id = f.get("column_id", "")
                if col_id in score_label_ids:
                    score_filters.append(f)
                else:
                    system_filters.append(f)

            if system_filters:
                q_filters = FilterEngine.get_filter_conditions_for_system_metrics(
                    system_filters, field_map=session_field_map
                )
                base_query = base_query.filter(q_filters)

            # Apply score-based filters using Score model
            if score_filters:
                for sf in score_filters:
                    col_id = sf.get("column_id")
                    fc = sf.get("filter_config", {})
                    filter_op = fc.get("filter_op", "equals")
                    filter_val = fc.get("filter_value")

                    base_score_q = Score.objects.filter(
                        trace_session_id=OuterRef("trace__session_id"),
                        label_id=col_id,
                        deleted=False,
                    )

                    if filter_op == "is_not_null":
                        base_query = base_query.filter(Exists(base_score_q))
                    elif filter_op == "is_null":
                        base_query = base_query.exclude(Exists(base_score_q))
                    else:
                        # Value-based filter — support multi-select via __in
                        # Frontend sends comma-joined string for arrays; split it
                        if isinstance(filter_val, str) and "," in filter_val:
                            filter_val = [
                                v.strip() for v in filter_val.split(",") if v.strip()
                            ]

                        if filter_op == "equals":
                            if isinstance(filter_val, list):
                                score_q = base_score_q.filter(value__in=filter_val)
                            else:
                                score_q = base_score_q.filter(value=filter_val)
                            base_query = base_query.filter(Exists(score_q))
                        elif filter_op == "not_equals":
                            if isinstance(filter_val, list):
                                score_q = base_score_q.filter(value__in=filter_val)
                            else:
                                score_q = base_score_q.filter(value=filter_val)
                            base_query = base_query.exclude(Exists(score_q))
                        elif filter_op == "contains":
                            score_q = base_score_q.filter(value__icontains=filter_val)
                            base_query = base_query.filter(Exists(score_q))
                        else:
                            # Unknown op — fall back to existence check
                            base_query = base_query.filter(Exists(base_score_q))

            page_number = validated_data["page_number"]
            page_size = validated_data["page_size"]
            start = page_number * page_size
            end_idx = start + page_size

            order_fields = (
                FilterEngine.get_sort_conditions_system_metrics(
                    sort_params, field_map=session_field_map
                )
                if sort_params
                else []
            )
            base_query = (
                base_query.order_by(*order_fields)
                if order_fields
                else base_query.order_by("-start_time")
            )

            if not remaining_filters:
                count_query = (
                    ObservationSpan.objects.filter(
                        pre_agg_q,
                        trace__session_id__in=session_ids,
                        **end_user_filter,
                    )
                    .values("trace__session_id")
                    .distinct()
                )
                total_rows = count_query.count()
            else:
                total_rows = base_query.count()
            paginated_spans = list(base_query if export else base_query[start:end_idx])

            paginated_session_ids = [
                str(span["trace__session_id"]) for span in paginated_spans
            ]
            # PG-FALLBACK branch (reached only when the primary CH path raised):
            # its curated reads stay FULLY ON PG so it degrades gracefully on a
            # CH outage. The CH dict/overlay cutover (DESIGN §4.3 / §5.2) lives
            # ONLY on the primary ``_list_sessions_clickhouse`` path; the CH dict
            # readers re-raise on error, so routing this fallback through them
            # would make a CH outage fail the whole request instead of degrading.
            # PG sources (tracer_enduser FK, TraceSession.name) still exist until
            # the contract step (P4), so this read remains valid in the interim.
            end_user_map = self._fetch_end_user_info_pg(
                paginated_session_ids, end_user_filter
            )

            # session name = PG TraceSession.name (the legacy back-fill, PG-only).
            session_name_map = {
                str(sid): name
                for sid, name in TraceSession.objects.filter(
                    id__in=paginated_session_ids
                ).values_list("id", "name")
            }

            result = [
                self._build_row(span, needs_first_last, end_user_map, session_name_map)
                for span in paginated_spans
            ]

            if not needs_first_last:
                if paginated_session_ids:
                    first_last_map = self._fetch_first_last_messages(
                        paginated_session_ids, end_user_filter
                    )
                    for item in result:
                        messages = first_last_map.get(item["session_id"], {})
                        item["first_message"] = messages.get("first_message")
                        item["last_message"] = messages.get("last_message")

            # Fetch scores for paginated sessions
            annotation_labels = list(
                AnnotationsLabels.objects.filter(project_id=project_id, deleted=False)
            )
            if annotation_labels and paginated_session_ids:
                try:
                    scores_map = self._fetch_session_scores(
                        paginated_session_ids, annotation_labels
                    )
                    for item in result:
                        sid = item["session_id"]
                        session_scores = scores_map.get(sid, {})
                        for label in annotation_labels:
                            lid = str(label.id)
                            item[lid] = session_scores.get(lid)
                except Exception:
                    session_logger.exception("Failed to fetch session scores")

            format_datetime_fields_to_iso(
                result, ["start_time", "end_time", "created_at"]
            )

            default_session_config = get_default_project_session_config()
            config = ensure_project_session_property_identities(
                (project.session_config if project else None) or default_session_config
            )

            # Append score columns to config
            if annotation_labels:
                score_configs = self._build_score_column_config(
                    annotation_labels, project_id=project_id
                )
                for sc in score_configs:
                    if not any(c["id"] == sc["id"] for c in config):
                        config.append(sc)

            response = {
                "metadata": {"total_rows": total_rows},
                "table": result,
                "config": config,
            }

            return self._gm.success_response(response)

        except Project.DoesNotExist:
            return self._gm.bad_request("Project not found")
        except ListCursorError as exc:
            return self._gm.custom_error_response(
                drf_status.HTTP_400_BAD_REQUEST, str(exc), code=exc.code
            )
        except UnsupportedFilterShapeError:
            return self._gm.bad_request("Session filter configuration is invalid")
        except FilterPrincipalContextError as exc:
            return self._gm.bad_request(str(exc))
        except Exception as exc:
            if is_clickhouse_api_read_unavailable_error(exc):
                session_logger.warning(
                    "session_list_query_unavailable",
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Session data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            session_logger.exception(
                "session_list_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                drf_status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Session data could not be loaded",
                code="server_error",
            )

    @staticmethod
    def _build_row(span, needs_first_last, end_user_map, session_name_map=None):
        session_id = str(span["trace__session_id"])
        start_time = span["start_time"]
        end_time = span["end_time"]
        duration = span.get("duration_val")
        end_user = end_user_map.get(session_id, {})
        return {
            "total_cost": span["total_cost"] or 0,
            "total_tokens": span["total_tokens"],
            "duration": (duration.total_seconds() if duration else 0),
            "total_traces_count": span["traces_count"],
            "start_time": start_time,
            "end_time": end_time,
            "first_message": (span.get("first_message") if needs_first_last else None),
            "last_message": (span.get("last_message") if needs_first_last else None),
            "session_id": session_id,
            "session_name": (session_name_map or {}).get(session_id),
            "created_at": span["session_created_at"],
            "user_id": end_user.get("user_id"),
            "user_id_type": end_user.get("user_id_type"),
            "user_id_hash": end_user.get("user_id_hash"),
        }

    @staticmethod
    def _fetch_first_last_messages(session_ids, end_user_filter):
        """Fetch first and last messages for a small set of session IDs.

        Uses DISTINCT ON instead of correlated subqueries for performance.
        """
        if not session_ids:
            return {}

        base_qs = ObservationSpan.objects.filter(
            trace__session_id__in=session_ids, **end_user_filter
        )

        first_spans = (
            base_qs.order_by("trace__session_id", "start_time")
            .distinct("trace__session_id")
            .values("trace__session_id", "input")
        )

        last_spans = (
            base_qs.order_by("trace__session_id", "-start_time")
            .distinct("trace__session_id")
            .values("trace__session_id", "input")
        )

        result = {}
        for row in first_spans:
            sid = str(row["trace__session_id"])
            result[sid] = {"first_message": row["input"], "last_message": None}

        for row in last_spans:
            sid = str(row["trace__session_id"])
            if sid in result:
                result[sid]["last_message"] = row["input"]
            else:
                result[sid] = {"first_message": None, "last_message": row["input"]}

        return result

    @staticmethod
    def _fetch_end_user_info_pg(session_ids, end_user_filter):
        """PG-only end-user read for the CH-failure FALLBACK branch.

        This is the ORIGINAL pre-cutover read, kept intact so the PG-fallback
        path stays fully on PG and degrades gracefully when ClickHouse is down
        (the CH dict reader in ``_fetch_end_user_info`` re-raises on error, which
        would defeat the fallback). It traverses the PG ``ObservationSpan.end_user``
        FK into ``tracer_enduser`` via ``DISTINCT ON (trace__session_id)`` ordered
        by ``-start_time`` — i.e. the LATEST span that has an end user, per
        session. (The primary path's ``_fetch_end_user_info`` reproduces this same
        latest-span semantic with ``argMaxIf(end_user_id, start_time, …)``.) These
        PG sources remain valid until the contract step (P4) drops the FK/table.
        """
        if not session_ids:
            return {}

        rows = (
            ObservationSpan.objects.filter(
                trace__session_id__in=session_ids,
                end_user__isnull=False,
                **end_user_filter,
            )
            .order_by("trace__session_id", "-start_time")
            .distinct("trace__session_id")
            .values(
                "trace__session_id",
                "end_user__user_id",
                "end_user__user_id_type",
                "end_user__user_id_hash",
            )
        )

        return {
            str(row["trace__session_id"]): {
                "user_id": row["end_user__user_id"],
                "user_id_type": row["end_user__user_id_type"],
                "user_id_hash": row["end_user__user_id_hash"],
            }
            for row in rows
        }

    @staticmethod
    def _fetch_end_user_info(
        session_ids,
        analytics,
        project_ids=None,
        deadline: ReadDeadline | None = None,
    ):
        """Fetch curated end-user fields for a session page from CH — PRIMARY
        ``_list_sessions_clickhouse`` path ONLY (the PG-fallback branch uses
        ``_fetch_end_user_info_pg`` so it degrades gracefully on a CH outage).

        CH-derived-dimensions cutover (DESIGN §4.3 / §5.2). The old read
        traversed the PG ``ObservationSpan.end_user`` FK into ``tracer_enduser``
        (``end_user__user_id``/``__user_id_type``/``__user_id_hash``). That FK
        and table retire at P4, so the read now restructures:

          1. Resolve the per-session ``end_user_id`` from the CH ``spans`` table
             — ``argMaxIf(end_user_id, start_time, <has-user>)`` over the
             session's spans that carry an end user (``end_user_id``
             non-null/non-NIL). The ordering key is ``start_time``, which MATCHES
             the old PG read exactly: ``_fetch_end_user_info_pg`` does
             ``end_user__isnull=False`` then ``DISTINCT ON(trace__session_id)
             ORDER BY trace__session_id, -start_time`` — i.e. the LATEST span
             with a user, the same max-``start_time`` pick.
             NOTE — the only residual delta is the tie-break among spans sharing
             the EXACT same max ``start_time`` but DIFFERENT end users: PG
             ``DISTINCT ON`` (single ``-start_time`` order) and CH ``argMaxIf``
             both pick an arbitrary one, not guaranteed to be the same row. This
             bites only multi-user sessions with start_time ties; sessions are
             single-user in practice. If it ever matters, make the order total
             on both sides (e.g. argMax over a ``(start_time, id)`` tuple).
          2. Batch-resolve ``{end_user_id -> {user_id, user_id_type,
             user_id_hash}}`` from ``end_users_dict`` (the curated labels), with
             the FK-faithful NULL/'' normalization the reader documents.

        Returns ``{session_id -> {user_id, user_id_type, user_id_hash}}``; a
        session with no end-user span is simply absent (caller defaults to a
        ``{}`` record → all-None fields), matching the old left-join miss.
        """
        from tracer.services.clickhouse.v2.end_user_dict_reader import (
            resolve_end_user_fields,
        )

        ids = [str(s) for s in (session_ids or []) if s]
        if not ids:
            return {}

        # P3b step1.5 — SESSION-id re-key (DESIGN §3 / id_remap_sql). This label
        # (latest end_user per session) is fed the session-list page's session ids,
        # which post-cutover can carry BOTH a straddler's old id AND its (phantom)
        # new id (the no-filter browse list stays bare — gate B). Resolve each
        # span's `trace_session_id` new→old through `trace_session_id_remap` and
        # GROUP on the RESOLVED id, so `argMaxIf` picks the latest user across the
        # UNIFIED old+new span set; then — mirroring `end_user_dict_reader` (commit
        # 9e4ba4f7e: "result key stays the caller's input id") — re-key the output
        # so EVERY caller input id (old or new) maps to its canonical session's
        # label. The query filters the resolved id against the CANONICAL ids of the
        # inputs. Pre-flip NO span matches a `new_id` (resolved == own) AND no input
        # is a new_id, so this is a byte-identical no-op (gate B). NB the
        # `end_user_id` label is itself remap-resolved downstream in
        # `resolve_end_user_fields` (prior slice), so a straddler whose user label
        # also straddles still resolves to one user.
        from tracer.services.clickhouse.v2.id_remap_sql import (
            bounded_survivor_map_subquery,
            resolved_id_expr,
        )

        ts_resolved = resolved_id_expr("latest_trace_session_id", "ts_remap")

        # input id -> canonical (old) id; an OLD id maps to itself, a NEW id to its
        # old_id. Used to (a) scope the resolved GROUP BY and (b) re-key the result.
        read_settings = (
            _session_read_settings(max_result_rows=max(1, len(ids)))
            if deadline is not None
            else None
        )
        input_to_canon = _resolve_session_ids_to_canonical(
            analytics,
            ids,
            deadline=deadline,
            settings=read_settings,
        )
        canonical_ids = {input_to_canon.get(i, i) for i in ids}

        proj_list = [str(p) for p in (project_ids or []) if p]
        proj_clause = ""
        params = {
            "session_ids": tuple(canonical_ids),
            "nil": NIL_UUID,
        }
        if proj_list:
            proj_clause = "AND project_id IN %(project_ids)s"
            params["project_ids"] = tuple(proj_list)

        # ``argMaxIf`` carries the "span has an end_user" predicate INSIDE the
        # aggregate (the latest span whose end_user_id is non-null/non-NIL),
        # rather than in WHERE. Filtering on ``end_user_id`` in WHERE while it is
        # also the argMax target trips the CH 25.3 analyzer ("aggregate function
        # found in WHERE", Code 184). Sessions with no qualifying span yield the
        # 0-UUID (NIL) default and are dropped in the Python pass below. The
        # resolved `trace_session_id` is the GROUP key (so old+new straddler spans
        # land in ONE group keyed by the OLD/canonical id).
        ts_map = bounded_survivor_map_subquery(
            "trace_session_id_remap", candidate_param="session_ids"
        )
        eu_by_session_q = f"""
            WITH
            ts_survivor_map AS ({ts_map}),
            candidate_span_identities AS (
                SELECT DISTINCT project_id, trace_id, id, start_time
                FROM spans
                PREWHERE 1 = 1
                  {proj_clause}
                  AND (
                      trace_session_id IN %(session_ids)s
                      OR trace_session_id IN (
                          SELECT any_id
                          FROM ts_survivor_map
                          WHERE survivor_id IN %(session_ids)s
                      )
                  )
            ),
            latest_candidate_spans AS (
                SELECT
                    project_id,
                    trace_id,
                    id,
                    start_time,
                    argMax(tuple(trace_session_id), _version).1
                        AS latest_trace_session_id,
                    argMax(tuple(end_user_id), _version).1 AS latest_end_user_id,
                    argMax(is_deleted, _version) AS latest_is_deleted
                FROM spans
                PREWHERE 1 = 1
                  {proj_clause}
                  AND (project_id, trace_id, id, start_time) IN (
                      SELECT project_id, trace_id, id, start_time
                      FROM candidate_span_identities
                  )
                GROUP BY project_id, trace_id, id, start_time
            ),
            resolved_candidate_spans AS (
                SELECT
                    {ts_resolved} AS session_id,
                    latest_end_user_id AS end_user_id,
                    start_time,
                    trace_id,
                    id
                FROM latest_candidate_spans
                LEFT JOIN ts_survivor_map AS ts_remap
                    ON latest_trace_session_id = ts_remap.any_id
                WHERE latest_is_deleted = 0
                  AND {ts_resolved} IN %(session_ids)s
            )
            SELECT
                toString(session_id) AS session_id,
                toString(
                    argMaxIf(
                        end_user_id,
                        tuple(start_time, trace_id, id),
                        end_user_id IS NOT NULL AND end_user_id != toUUID(%(nil)s)
                    )
                ) AS end_user_id
            FROM resolved_candidate_spans
            GROUP BY session_id
        """
        timeout_ms = (
            deadline.remaining_ms(SESSION_LIST_ENRICHMENT_TIMEOUT_MS)
            if deadline is not None
            else 10000
        )
        result = analytics.execute_ch_query(
            eu_by_session_q,
            params,
            timeout_ms=timeout_ms,
            **({"settings": read_settings} if read_settings is not None else {}),
        )

        eu_by_canonical: dict[str, str] = {}
        for row in result.data:
            raw_sid = row.get("session_id") if isinstance(row, dict) else row[0]
            raw_euid = row.get("end_user_id") if isinstance(row, dict) else row[1]
            sid = str(raw_sid) if raw_sid else ""
            euid = str(raw_euid) if raw_euid else ""
            if sid and euid and euid != NIL_UUID:
                eu_by_canonical[sid] = euid

        # Re-key by every caller input id → its canonical session's end_user (so a
        # bare-browse straddler row keyed by either the old OR the new id resolves).
        session_to_eu: dict[str, str] = {}
        for i in ids:
            euid = eu_by_canonical.get(input_to_canon.get(i, i))
            if euid:
                session_to_eu[i] = euid

        if not session_to_eu:
            return {}

        # Step 2 — end_user_id → curated fields from the CH dict (batch).
        fields_by_eu = resolve_end_user_fields(
            set(session_to_eu.values()),
            timeout_ms=(
                deadline.remaining_ms(SESSION_LIST_ENRICHMENT_TIMEOUT_MS)
                if deadline is not None
                else None
            ),
            settings=read_settings,
        )

        out: dict[str, dict] = {}
        for sid, euid in session_to_eu.items():
            rec = fields_by_eu.get(euid)
            if rec is None:
                # Present end_user_id with no curated row (orphan) → all-None,
                # faithful to the old FK miss.
                out[sid] = {
                    "user_id": None,
                    "user_id_type": None,
                    "user_id_hash": None,
                }
            else:
                out[sid] = rec
        return out

    @staticmethod
    def _fetch_session_names(
        session_ids,
        project_ids=None,
        deadline: ReadDeadline | None = None,
    ):
        """Resolve ``{session_id -> display name}`` for a page of session ids.

        CH-derived-dimensions cutover (DESIGN §5.2). The display name is
        ``COALESCE(overlay.display_name, trace_sessions_dict.external_session_id)``:
        the immutable external session id comes from the CH ``trace_sessions``
        dict (replacing the old back-fill from PG ``TraceSession.name``), and the
        optional user rename override comes from the PG ``TraceSessionOverlay``
        (the UI-sourced overlay, soft-id linked by ``trace_session_id``).

        ``project_ids`` scopes the overlay read defensively (the overlay is
        unique on ``trace_session_id`` so a page's ids can't collide across
        tenants, but scoping keeps the query tenant-bounded). A session absent
        from both the dict and the overlay → ``None`` (faithful to the old miss).
        """
        from tracer.models.trace_session import TraceSessionOverlay
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            resolve_external_session_ids,
        )

        ids = [str(s) for s in (session_ids or []) if s]
        if not ids:
            return {}

        # External session id from the CH dict (immutable identity).
        external_map = resolve_external_session_ids(
            ids,
            timeout_ms=(
                deadline.remaining_ms(SESSION_LIST_ENRICHMENT_TIMEOUT_MS)
                if deadline is not None
                else None
            ),
            settings=(
                _session_read_settings(max_result_rows=max(1, len(ids)))
                if deadline is not None
                else None
            ),
        )

        # display_name override from the PG overlay (UI rename). Soft-id by
        # trace_session_id; scope by project set when known.
        display_map = {}
        overlay_qs = TraceSessionOverlay.objects.filter(
            trace_session_id__in=ids,
            deleted=False,
        )
        proj_list = [str(p) for p in (project_ids or []) if p]
        if proj_list:
            overlay_qs = overlay_qs.filter(project_id__in=proj_list)
        display_map = {
            str(tsid): name
            for tsid, name in overlay_qs.values_list("trace_session_id", "display_name")
        }

        out: dict[str, str | None] = {}
        for sid in ids:
            override = display_map.get(sid)
            out[sid] = override if override else external_map.get(sid)
        return out

    @staticmethod
    def _build_bookmark_filter(
        bookmarked,
        project_ids,
        analytics=None,
        *,
        deadline: ReadDeadline | None = None,
    ):
        """Build the synthetic ``trace_session_id`` IN/NOT-IN filter for the
        three-state ``bookmarked`` flag (DESIGN §5.2), or ``None`` for no filter.

        The bookmark state is now a PG ``TraceSessionOverlay`` row
        (``bookmarked=True``), not a column on the (CH-bound) session. So:

          • ``None``  → ``None`` (no filter; the CH builder lists everything).
          • ``True``  → ``trace_session_id IN  (overlay bookmarked ids)``. When
            no session is bookmarked the id set is empty; we emit ``[NIL_UUID]``
            so the ``in`` filter matches NOTHING (never silently lists all).
          • ``False`` → ``trace_session_id NOT IN (overlay bookmarked ids)``.
            An empty id set makes ``not_in`` a no-op (``1 = 1``) → all sessions,
            which is correct (nothing is bookmarked ⇒ everything is non-bookmarked).

        The id set is scoped to the request's project(s) so one tenant's read can
        never select another tenant's bookmarked sessions.
        """
        if bookmarked is None:
            return None

        from tracer.models.trace_session import TraceSessionOverlay

        proj_list = [str(p) for p in (project_ids or []) if p]
        overlay_qs = TraceSessionOverlay.objects.filter(
            bookmarked=True,
            deleted=False,
        )
        if proj_list:
            overlay_qs = overlay_qs.filter(project_id__in=proj_list)
        ids = [str(t) for t in overlay_qs.values_list("trace_session_id", flat=True)]
        if ids and analytics is not None:
            canonical_ids = _resolve_session_ids_to_canonical(
                analytics,
                ids,
                deadline=deadline,
                settings=(
                    _session_read_settings(max_result_rows=max(1, len(ids)))
                    if deadline is not None
                    else None
                ),
            )
            ids = sorted({canonical_ids.get(sid, sid) for sid in ids})

        if bookmarked:
            # IN over an empty set must match nothing — use the NIL sentinel
            # (the filter builder turns `in []` into 0=1, but going through the
            # sentinel keeps a uniform non-empty value list either way).
            return {
                "column_id": "trace_session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ids or [NIL_UUID],
                },
            }
        # bookmarked is False → NON-bookmarked only. NOT IN over an empty set is
        # a no-op (all sessions), which is the correct semantics.
        return {
            "column_id": "trace_session_id",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "not_in",
                "filter_value": ids,
            },
        }

    def _list_sessions_clickhouse(
        self,
        request,
        project_id,
        project,
        analytics,
        validated_data,
        org_project_ids=None,
        bookmark_filter=None,
        read_deadline: ReadDeadline | None = None,
        export: bool = False,
    ):
        """List sessions using ClickHouse backend.

        When ``org_project_ids`` is provided the builder is constructed
        with `project_ids=...` and the session list spans all projects in
        the org.

        ``bookmark_filter`` is the optional synthetic ``trace_session_id``
        IN/NOT-IN filter (built by ``_build_bookmark_filter`` from the PG
        ``TraceSessionOverlay``) that implements the three-state ``bookmarked``
        flag against the CH path (DESIGN §5.2). ``None`` ⇒ no bookmark filtering.

        Export responses must be complete. A numbered or lower-bound page is
        therefore rejected instead of being serialized as a silently truncated
        CSV file.
        """
        if read_deadline is None:
            read_deadline = ReadDeadline.start(SESSION_LIST_WALL_DEADLINE_MS)

        org_scope = bool(org_project_ids)
        # Organization in scope — the canonical resolver used across this view
        # (request is a param here; the helper is pure getattr, no query). Needed
        # by the user_id → end_user_id resolution below.
        org = _get_request_organization(request)
        filters = list(validated_data.get("filters", []) or [])
        attested_filters = list(filters)
        sort_params = validated_data.get("sort_params", [])
        page_number = validated_data.get("page_number", 0)
        page_size = validated_data.get("page_size", 30)
        user_id_qp = validated_data.get("user_id") or getattr(
            request, "query_params", {}
        ).get("user_id")

        # Support user_id supplied as a structural filter (the cross-project
        # user-detail page prepends one) or as a query param. The CH `spans`
        # table keys users by the UUID `end_user_id`, not the string `user_id`,
        # so we strip every user_id filter out, reverse-resolve the string
        # value(s) to end_user UUIDs and re-inject a synthetic `end_user_id`
        # filter. The original OPERATOR and the FULL value list are preserved:
        # `user_id in [alice, bob]` must match BOTH, and not_equals / is_null
        # must not be silently rewritten to `in`.
        user_id_op: str | None = None
        user_id_values: list[str] = []
        _remaining = []
        for _f in filters:
            _col, _cfg = FilterEngine._normalize_filter_params(_f)
            if _col == "user_id":
                if user_id_op is None:
                    user_id_op = _cfg.get("filter_op") or "in"
                _val = _cfg.get("filter_value")
                if isinstance(_val, list):
                    user_id_values.extend(str(v) for v in _val if v not in (None, ""))
                elif _val not in (None, ""):
                    user_id_values.append(str(_val))
                continue
            _remaining.append(_f)
        filters = _remaining

        # The query-param user_id (cross-project user-detail page) is an
        # implicit single-user match and also drives per-row user-display.
        if user_id_qp:
            user_id_values.insert(0, str(user_id_qp))
            if user_id_op is None:
                user_id_op = "in"

        # Resolve the raw user_id(s) to end_user UUIDs and inject a synthetic
        # end_user_id filter (scoped by org, and project when in project mode).
        #
        # P3b step2 precondition — the reverse-resolve prefers the curated CH
        # `end_users` dimension over PG `EndUser.objects` (PG_ORM_READ_MIGRATION,
        # Slice B). PG FREEZES post-step2: a NET-NEW user (first seen after the
        # ingest get_or_create is dropped) has NO `tracer_enduser` row, so PG
        # returns [] → empty list. The curated CH dimension instead yields the
        # state-robust id-SET (historical OLD-id row + net-new DETERMINISTIC-id
        # row + a straddler's BOTH) — see `_resolve_end_user_ids_for_user_id`.
        #
        # P3b step1.5 (DESIGN §3 / id_remap_sql) — the resolved ids are CURATED
        # keys (OLD pre-sweep). SessionListQueryBuilder extracts this synthetic
        # `end_user_id` filter (`_ENDUSER_ID_FILTER_COLS`) and binds it to the
        # id-remap-RESOLVED `end_user_id` column (`_build_resolved_user_clause`),
        # so a STRADDLER's NEW (deterministic-id) spans resolve new→old and
        # select under the same OLD id. A net-new id has no remap entry (resolves
        # to itself), so no double-count.
        _NULL_USER_OPS = {"is_null", "is_not_null"}
        _NEGATED_USER_OPS = {"not_in", "not_equals"}
        _SUPPORTED_USER_OPS = _NULL_USER_OPS | _NEGATED_USER_OPS | {"in", "equals"}

        if user_id_op and user_id_op not in _SUPPORTED_USER_OPS:
            return self._gm.bad_request(
                f"Unsupported operator '{user_id_op}' for user_id filter. "
                f"Supported: {sorted(_SUPPORTED_USER_OPS)}"
            )

        end_user_display = None
        if user_id_op in _NULL_USER_OPS:
            # Presence/absence of any user — no value resolution needed; the
            # builder maps this to session membership over end_user_id.
            filters.append(
                {
                    "column_id": "end_user_id",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": user_id_op,
                    },
                }
            )
        elif user_id_values:
            _ids: list[str] = []
            for _uv in dict.fromkeys(user_id_values):  # dedup, keep order
                _resolved, _display = _resolve_end_user_ids_for_user_id(
                    _uv,
                    org=org,
                    org_scope=org_scope,
                    project_id=project_id,
                    deadline=read_deadline,
                )
                _ids.extend(_resolved)
                # Only the single query-param user labels the displayed rows.
                if (
                    _display is not None
                    and end_user_display is None
                    and user_id_qp is not None
                    and _uv == str(user_id_qp)
                ):
                    end_user_display = _display
            _ids = list(dict.fromkeys(_ids))
            _out_op = "not_in" if user_id_op in _NEGATED_USER_OPS else "in"
            # An unresolved value-set means "no such user". For inclusive ops
            # that is an empty result (NIL sentinel matches nothing); for
            # negated ops it is a no-op (everything matches), so skip injection.
            inject = True
            if not _ids:
                if _out_op == "in":
                    _ids = [NIL_UUID]
                else:
                    inject = False
            if inject:
                filters.append(
                    {
                        "column_id": "end_user_id",
                        "filter_config": {
                            "col_type": "SYSTEM_METRIC",
                            "filter_type": "text",
                            "filter_op": _out_op,
                            "filter_value": _ids,
                        },
                    }
                )

        # Three-state bookmark filter (DESIGN §5.2): a synthetic
        # ``trace_session_id`` IN/NOT-IN over the PG overlay's bookmarked ids,
        # resolved by ``_build_bookmark_filter``. None ⇒ no bookmark filtering.
        if bookmark_filter is not None:
            filters.append(bookmark_filter)

        cursor_token = validated_data.get("cursor")
        cursor_requested = bool(cursor_token or validated_data.get("cursor_mode"))
        cursor_supported = not sort_params and not export
        if cursor_token and not cursor_supported:
            raise ListCursorError(
                "cursor_unsupported",
                "Cursor pagination is unavailable for this query shape.",
            )
        cursor_enabled = cursor_requested and cursor_supported
        cursor_scope = cursor_scope_for_request(
            request,
            project_ids=[
                str(value)
                for value in (org_project_ids or ([project_id] if project_id else []))
            ],
        )
        # Bind the token to resolved end-user/bookmark membership as well as
        # the public request. If either independently mutable lookup changes,
        # the continuation fails closed instead of changing membership mid-page.
        cursor_query = {**validated_data, "filters": list(filters)}
        cursor_state = None
        if cursor_token:
            cursor_state = decode_list_cursor(
                cursor_token,
                resource="observe_sessions",
                scope=cursor_scope,
                query=cursor_query,
                page_size=page_size,
            )
            filters.append(frozen_window_filter(cursor_state))
            page_number = 0

        needs_annotation_completeness = any(
            (item.get("column_id") or item.get("columnId")) == "has_annotation"
            for item in filters
            if isinstance(item, dict)
        )
        annotation_label_ids = []
        annotation_label_ids_by_project = None
        if needs_annotation_completeness:
            if org_scope:
                labels_by_project = get_annotation_labels_by_project(
                    [str(value) for value in org_project_ids],
                    organization=org,
                )
                annotation_label_ids_by_project = {
                    project_key: [str(label.id) for label in labels]
                    for project_key, labels in labels_by_project.items()
                }
            else:
                annotation_labels = get_annotation_labels_for_project(project_id)
                annotation_label_ids = [str(label.id) for label in annotation_labels]

        builder = SessionListQueryBuilderV2(
            project_id=None if org_scope else str(project_id),
            project_ids=[str(p) for p in org_project_ids] if org_scope else None,
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            sort_params=sort_params,
            user_id=None,
            annotation_label_ids=annotation_label_ids,
            annotation_label_ids_by_project=annotation_label_ids_by_project,
            bounded_internal_scan=cursor_enabled,
        )
        candidate_cursor = bool(
            cursor_enabled and builder.supports_candidate_cursor_page()
        )
        if (
            cursor_enabled
            and not candidate_cursor
            and not builder.supports_bounded_filter_scan()
        ):
            if cursor_state is not None:
                raise ListCursorError(
                    "cursor_unsupported",
                    "Cursor pagination is unavailable for this query shape.",
                )
            cursor_enabled = False
            builder._bounded_internal_scan = False

        def _page_read_settings(max_result_rows):
            # The cursor freezes window/keyset progress only. Each finite read
            # resolves current latest state without a merge-unstable ceiling.
            return _session_read_settings(max_result_rows=max_result_rows)

        # Phase 1: select the exact page identities before hydrating cost/token,
        # content, and attributes. Default/session-level shapes use the narrow
        # one-query candidate selector. Scalar span attributes (for example
        # ``final_status``) use adjacent root-session seed pages plus finite
        # latest-state classification. The latter avoids the historical
        # full-window GROUP BY that timed out on high-volume tenants while
        # preserving the same newest-session page membership.
        candidate_first = builder.supports_candidate_first_page() and (
            not cursor_enabled or candidate_cursor
        )
        bounded_page = None
        candidate_cursor_has_more = False
        if not candidate_first and not builder.supports_bounded_filter_scan():
            degraded_error_code = builder.bounded_filter_degraded_error_code()
            logger.warning(
                "bounded_session_list_shape_unsupported",
                filter_count=len(filters),
                sort_count=len(sort_params),
                error_code=degraded_error_code,
            )
            if degraded_error_code == "unsupported_filter_shape":
                raise UnsupportedFilterShapeError(
                    "session filter configuration is invalid"
                )
            return self._gm.custom_error_response(
                drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                "This session filter or sort is temporarily unavailable. Please retry shortly.",
                code="service_unavailable",
            )

        candidate_total_count: int | None = None
        if candidate_first:
            if candidate_cursor:
                before_start_time = (
                    cursor_state.order[0] if cursor_state is not None else None
                )
                before_session_id = (
                    str(cursor_state.order[1]) if cursor_state is not None else None
                )
                page_query, page_params = builder.build_candidate_cursor_page_query(
                    before_start_time=before_start_time,
                    before_session_id=before_session_id,
                )
            else:
                page_query, page_params = builder.build_candidate_page_query()
            page_result = analytics.execute_ch_query(
                page_query,
                page_params,
                timeout_ms=read_deadline.remaining_ms(SESSION_LIST_QUERY_TIMEOUT_MS),
                settings=_page_read_settings(page_size + 1),
            )
            candidate_rows = list(page_result.data or [])
            page_candidates = candidate_rows[:page_size]
            if candidate_cursor:
                candidate_cursor_has_more = len(candidate_rows) > page_size
                prior_seen_rows = (
                    cursor_state.seen_rows if cursor_state is not None else 0
                )
                candidate_total_count = prior_seen_rows + (
                    int(candidate_rows[0].get("remaining_count", 0) or 0)
                    if candidate_rows
                    else 0
                )
            elif page_candidates:
                candidate_total_count = int(
                    page_candidates[0].get("total_count", 0) or 0
                )
        else:
            bounded_page = read_bounded_filter_page(
                builder=builder,
                analytics=analytics,
                filters=filters,
                key_field="session_id",
                page_number=page_number,
                page_size=page_size,
                deadline_ms=read_deadline.remaining_ms(SESSION_LIST_QUERY_TIMEOUT_MS),
                # Seed acquisition stays broad; exact attribute replay uses the
                # builder's smaller production-safe classifier recommendation.
                max_candidates=SESSION_LIST_FILTER_MAX_CANDIDATES,
                max_seed_attempts=SESSION_LIST_FILTER_MAX_SEED_ATTEMPTS,
                max_query_count=SESSION_LIST_FILTER_MAX_QUERIES,
                classify_batch_size=builder.recommended_filter_classify_batch_size(),
                read_settings=_page_read_settings(SESSION_LIST_FILTER_MAX_CANDIDATES),
                cursor_start_time=(
                    cursor_state.order[0] if cursor_state is not None else None
                ),
                cursor_order_token=(
                    cursor_state.order[1] if cursor_state is not None else None
                ),
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
                        "session_list_page_depth_exceeded",
                        project_id=str(project_id) if project_id else None,
                        page_number=page_number,
                        page_size=page_size,
                    )
                    return self._gm.custom_error_response(
                        drf_status.HTTP_422_UNPROCESSABLE_ENTITY,
                        PAGE_DEPTH_EXCEEDED_MESSAGE,
                        code=PAGE_DEPTH_EXCEEDED_CODE,
                    )
                logger.warning(
                    "session_list_bounded_read_incomplete",
                    project_id=str(project_id) if project_id else None,
                    page_number=page_number,
                    error_code=bounded_page.error_code,
                    query_count=bounded_page.query_count,
                )
                if not cursor_enabled or bounded_page.continuation_slice_end is None:
                    return self._gm.custom_error_response(
                        drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Filtered session data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
            page_candidates = bounded_page.rows
            # As on bounded trace/span lists, this is the proven count prefix.
            # Re-running the legacy full-window count would reintroduce the
            # exact timeout this path removes.
            candidate_total_count = (
                (cursor_state.seen_rows if cursor_state is not None else 0)
                + bounded_page.total_rows_lower_bound
                if cursor_enabled
                else bounded_page.total_rows_lower_bound
            )

        if org_scope and any(
            max(
                int(row.get("project_count", 1) or 1),
                int(row.get("max_project_count", 1) or 1),
            )
            > 1
            for row in page_candidates
        ):
            # Session IDs are the public/enrichment key.  Never merge data from
            # two projects if a malformed import reused that UUID; relational
            # classification reports the collision before any ID-only hydration.
            session_logger.warning(
                "organization_session_identity_collision",
                collision_count=sum(
                    max(
                        int(row.get("project_count", 1) or 1),
                        int(row.get("max_project_count", 1) or 1),
                    )
                    > 1
                    for row in page_candidates
                ),
            )
            return self._gm.custom_error_response(
                drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                "Session data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

        candidate_ids = [
            str(row.get("session_id", ""))
            for row in page_candidates
            if row.get("session_id")
        ]

        # Once the finite page identities are known, every remaining CH read is
        # independent and candidate-scoped. Run those reads concurrently under
        # the one request wall deadline: endpoint time is selector + slowest
        # enrichment, never selector plus independent per-query allowances.
        metrics_query = ""
        metrics_params: dict = {}
        content_query = ""
        content_params: dict = {}
        attrs_query = ""
        attrs_params: dict = {}
        if candidate_ids:
            metrics_query, metrics_params = builder.build_page_metrics_query(
                candidate_ids
            )
            content_query, content_params = builder.build_content_query(candidate_ids)
            attrs_query, attrs_params = builder.build_span_attributes_query(
                candidate_ids
            )

        # Get total count — skip the expensive count query when we can infer
        # the total from the Phase 1 result size.
        count_query = ""
        count_params: dict = {}
        if candidate_total_count is None:
            # An out-of-range page has no row to carry count() OVER(). Run the
            # identity-only exact counter, never the historical raw aggregate.
            count_query, count_params = builder.build_candidate_count_query()

        curated_project_ids = org_project_ids or ([project_id] if project_id else None)

        def _execute_page_query(query, params, max_result_rows):
            return analytics.execute_ch_query(
                query,
                params,
                timeout_ms=read_deadline.remaining_ms(
                    SESSION_LIST_ENRICHMENT_TIMEOUT_MS
                ),
                settings=_page_read_settings(max_result_rows),
            )

        tasks: dict[str, tuple] = {}
        if metrics_query:
            tasks["metrics"] = (
                _execute_page_query,
                (metrics_query, metrics_params, max(1, len(candidate_ids))),
            )
        if content_query:
            tasks["content"] = (
                _execute_page_query,
                (content_query, content_params, max(1, len(candidate_ids))),
            )
        if attrs_query:
            tasks["attributes"] = (
                _execute_page_query,
                (
                    attrs_query,
                    attrs_params,
                    SESSION_LIST_ATTRIBUTE_RESULT_ROWS,
                ),
            )
        if count_query:
            tasks["count"] = (
                _execute_page_query,
                (count_query, count_params, 1),
            )
        completed: dict[str, object] = {}
        if tasks:
            pool = ThreadPoolExecutor(max_workers=min(4, len(tasks)))
            futures = {
                pool.submit(callable_, *call_args): phase
                for phase, (callable_, call_args) in tasks.items()
            }
            try:
                # These two helpers use clickhouse-connect singleton readers
                # and a PG overlay read, so keep them on the request thread.
                # They overlap the native-driver futures above while sharing
                # the same remaining wall deadline.
                if candidate_ids:
                    try:
                        completed["names"] = self._fetch_session_names(
                            candidate_ids,
                            curated_project_ids,
                            read_deadline,
                        )
                    except Exception as exc:
                        if not (
                            is_read_budget_error(exc) or is_clickhouse_query_error(exc)
                        ):
                            raise
                        # A display-name lookup is optional enrichment: the
                        # selector and page hydration are already exact. Keep
                        # the rows/cursor and fall back to a null display label.
                        session_logger.warning(
                            "session_list_name_enrichment_unavailable",
                            project_id=str(project_id) if project_id else None,
                            page_number=page_number,
                            error_type=type(exc).__name__,
                        )
                        completed["names"] = {}
                    try:
                        completed["end_users"] = self._fetch_end_user_info(
                            candidate_ids,
                            analytics,
                            curated_project_ids,
                            read_deadline,
                        )
                    except Exception as exc:
                        if not (
                            is_read_budget_error(exc) or is_clickhouse_query_error(exc)
                        ):
                            raise
                        # User labels are likewise presentation-only here;
                        # membership was decided by the bounded selector. A CH
                        # memory/timeout failure must not discard that exact page
                        # or advance a different cursor checkpoint on retry.
                        session_logger.warning(
                            "session_list_end_user_enrichment_unavailable",
                            project_id=str(project_id) if project_id else None,
                            page_number=page_number,
                            error_type=type(exc).__name__,
                        )
                        completed["end_users"] = {}
                for future, phase in futures.items():
                    completed[phase] = future.result(
                        timeout=read_deadline.remaining_ms() / 1000
                    )
                read_deadline.remaining_ms()
            except (FuturesTimeoutError, ReadDeadlineExceeded) as exc:
                logger.warning(
                    "session_list_deadline_exceeded",
                    project_id=str(project_id) if project_id else None,
                    page_number=page_number,
                    error_type=type(exc).__name__,
                )
                raise
            except Exception as exc:
                logger.warning(
                    "session_list_enrichment_failed",
                    project_id=str(project_id) if project_id else None,
                    page_number=page_number,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                raise
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

        metrics_result = completed.get("metrics")
        metrics_by_id = {
            str(row.get("session_id", "")): row
            for row in (metrics_result.data if metrics_result is not None else [])
        }
        # Preserve the candidate selector's deterministic order rather than
        # depending on GROUP BY output order from the hydration query.
        actual_data = [
            metrics_by_id[sid] for sid in candidate_ids if sid in metrics_by_id
        ]
        if len(actual_data) != len(candidate_ids):
            session_logger.warning(
                "session_list_metrics_hydration_incomplete",
                expected_rows=len(candidate_ids),
                actual_rows=len(actual_data),
            )
            return self._gm.custom_error_response(
                drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                "Session data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        session_ids_page = [str(row.get("session_id", "")) for row in actual_data]
        content_result = completed.get("content")
        content_map = {
            str(row.get("session_id", "")): row
            for row in (content_result.data if content_result is not None else [])
        }
        for row in actual_data:
            session_id = str(row.get("session_id", ""))
            content = content_map.get(session_id, {})
            row["first_message"] = content.get("first_message", "")
            row["last_message"] = content.get("last_message", "")

        if candidate_total_count is not None:
            total_count = candidate_total_count
        else:
            count_result = completed.get("count")
            if (
                org_scope
                and count_result is not None
                and count_result.data
                and int(count_result.data[0].get("max_project_count", 1) or 1) > 1
            ):
                session_logger.warning(
                    "organization_session_identity_collision",
                    collision_count=1,
                )
                return self._gm.custom_error_response(
                    drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Session data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            total_count = (
                count_result.data[0].get("total", 0)
                if count_result is not None and count_result.data
                else 0
            )

        formatted = builder.format_sessions(
            [(list(row.values())) for row in actual_data],
            list(actual_data[0].keys()) if actual_data else [],
        )
        fallback_row_project_id = str(
            project_id
            or (
                org_project_ids[0]
                if org_project_ids is not None and len(org_project_ids) == 1
                else ""
            )
        )
        project_id_by_session = {
            str(row.get("session_id") or ""): str(
                row.get("project_id") or fallback_row_project_id
            )
            for row in page_candidates
            if row.get("session_id")
        }
        name_map = completed.get("names") or {}
        end_user_map = completed.get("end_users") or {}
        attr_result = completed.get("attributes")
        attr_result_data = attr_result.data if attr_result is not None else []
        for entry in formatted:
            session_id = str(entry.get("session_id", ""))
            entry_project_id = project_id_by_session.get(session_id, "")
            if not entry_project_id:
                session_logger.warning(
                    "session_list_project_evidence_missing",
                    session_id=session_id,
                )
                return self._gm.custom_error_response(
                    drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Session data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            entry["project_id"] = entry_project_id
            entry["session_name"] = name_map.get(session_id)
            entry["created_at"] = entry.get("start_time")
            end_user = end_user_map.get(session_id, {})
            entry["user_id"] = end_user.get("user_id")
            entry["user_id_type"] = end_user.get("user_id_type")
            entry["user_id_hash"] = end_user.get("user_id_hash")

        # Inject user info when a user_id filter is active. The EndUser
        # row was already resolved above when we built the synthetic
        # filter, so no extra DB hit is needed here. In org-scoped mode
        # multiple EndUser rows can match (one per project) — we pick
        # the first; the display fields are typically identical across
        # rows for the same logical user.
        if end_user_display and formatted:
            for entry in formatted:
                entry["user_id"] = end_user_display["user_id"]
                entry["user_id_type"] = end_user_display["user_id_type"]
                entry["user_id_hash"] = end_user_display["user_id_hash"]

        # Phase 3: Aggregated span attributes for custom columns
        _SKIP_ATTR_PREFIXES = (
            "raw.",
            "llm.input_messages",
            "llm.output_messages",
            "input.value",
            "output.value",
        )
        _MAX_ATTR_KEYS_PER_SESSION = 50
        if session_ids_page and attr_result_data:
            aggregated_attrs: dict[str, dict] = {}
            for attr_row in attr_result_data:
                sid = str(attr_row.get("session_id", ""))
                if (
                    sid in aggregated_attrs
                    and len(aggregated_attrs[sid]) >= _MAX_ATTR_KEYS_PER_SESSION
                ):
                    continue
                attrs = _merge_session_attribute_sources(attr_row)
                if sid not in aggregated_attrs:
                    aggregated_attrs[sid] = {}
                for key, value in attrs.items():
                    if len(aggregated_attrs[sid]) >= _MAX_ATTR_KEYS_PER_SESSION:
                        break
                    if key.startswith(_SKIP_ATTR_PREFIXES):
                        continue
                    if isinstance(value, str) and len(value) > 500:
                        continue
                    if key not in aggregated_attrs[sid]:
                        aggregated_attrs[sid][key] = (
                            set() if isinstance(value, (str, int, float, bool)) else []
                        )
                    if isinstance(value, (str, int, float, bool)):
                        aggregated_attrs[sid][key].add(
                            value if not isinstance(value, bool) else str(value).lower()
                        )
            for entry in formatted:
                sid = entry.get("session_id", "")
                session_attrs = aggregated_attrs.get(sid, {})
                for key, values in session_attrs.items():
                    if key not in entry:
                        if isinstance(values, set):
                            vals = sorted(values, key=str)
                            entry[key] = vals[0] if len(vals) == 1 else vals
                        else:
                            entry[key] = values

        # Build config with annotation metric columns (mirrors the PG path)
        config = ensure_project_session_property_identities(
            (project.session_config if project else None)
            or get_default_project_session_config()
        )
        _pid = project_id or (project.id if project else None)
        annotation_labels = (
            list(AnnotationsLabels.objects.filter(project_id=_pid, deleted=False))
            if _pid
            else []
        )
        if annotation_labels:
            score_configs = self._build_score_column_config(
                annotation_labels, project_id=_pid
            )
            for sc in score_configs:
                if not any(c["id"] == sc["id"] for c in config):
                    config.append(sc)

            # Attach score data to each session row
            if session_ids_page:
                scores_map = self._fetch_session_scores(
                    session_ids_page, annotation_labels
                )
                for entry in formatted:
                    sid = entry.get("session_id", "")
                    session_scores = scores_map.get(sid, {})
                    for label in annotation_labels:
                        lid = str(label.id)
                        entry[lid] = session_scores.get(lid)

        cursor_seen_rows = (
            cursor_state.seen_rows if cursor_state is not None else 0
        ) + len(page_candidates)
        next_cursor = None
        cursor_has_more = False
        if candidate_cursor and candidate_cursor_has_more:
            window_start, window_end = builder.parse_time_range(filters)
            last_candidate = page_candidates[-1]
            next_cursor = encode_list_cursor(
                resource="observe_sessions",
                scope=cursor_scope,
                query=cursor_query,
                page_size=page_size,
                window_start=window_start,
                window_end=window_end,
                order=(
                    last_candidate["session_start"],
                    str(last_candidate["session_id"]),
                ),
                seen_rows=cursor_seen_rows,
                total_rows=total_count,
            )
            cursor_has_more = True
        elif (
            cursor_enabled
            and bounded_page is not None
            and (
                (bounded_page.complete and bounded_page.has_more)
                or (
                    not bounded_page.complete
                    and (
                        bounded_page.has_more
                        or bounded_page.continuation_slice_end is not None
                    )
                )
            )
        ):
            window_start, window_end = builder.parse_time_range(filters)
            next_cursor = encode_list_cursor(
                resource="observe_sessions",
                scope=cursor_scope,
                query=cursor_query,
                page_size=page_size,
                window_start=window_start,
                window_end=window_end,
                order=_session_list_cursor_order_for_partial_page(
                    rows=page_candidates,
                    bounded_page=bounded_page,
                    cursor_state=cursor_state,
                ),
                seen_rows=cursor_seen_rows,
                scan_slice_start=(
                    bounded_page.continuation_slice_start
                    if not bounded_page.has_more
                    else None
                ),
                scan_slice_end=(
                    bounded_page.continuation_slice_end
                    if not bounded_page.has_more
                    else None
                ),
                scan_before_start_time=(
                    bounded_page.continuation_before_start_time
                    if not bounded_page.has_more
                    else None
                ),
                scan_before_id=(
                    bounded_page.continuation_before_id
                    if not bounded_page.has_more
                    else None
                ),
            )
            cursor_has_more = True

        metadata = {"total_rows": total_count}
        if candidate_cursor:
            metadata.update(
                {
                    "total_rows_exact": total_count,
                    "total_rows_is_lower_bound": False,
                    "has_more": cursor_has_more,
                    "next_cursor": next_cursor,
                    "next_cursor_fingerprint": (
                        list_cursor_boundary_fingerprint(next_cursor)
                    ),
                    "query_complete": True,
                    "query_status": "complete",
                    "query_error_code": None,
                }
            )
        elif bounded_page is not None:
            # The selector proves page membership and whether another page
            # exists, but it may stop once that ordered prefix is proved.  Its
            # count is therefore a lower bound, not an exact full-window count.
            public_chunk_complete = bounded_page.complete or cursor_has_more
            metadata.update(
                {
                    "total_rows_is_lower_bound": True,
                    "has_more": cursor_has_more
                    if cursor_enabled
                    else bounded_page.has_more,
                    "query_complete": public_chunk_complete,
                    "query_status": (
                        "complete" if public_chunk_complete else bounded_page.status
                    ),
                    "query_error_code": (
                        None if public_chunk_complete else bounded_page.error_code
                    ),
                }
            )
            candidate_seed_is_sampled = getattr(
                builder, "filter_candidate_seed_is_sampled", None
            )
            if callable(candidate_seed_is_sampled) and candidate_seed_is_sampled():
                # Candidate order came from the insert-only session rollup;
                # every returned row was still replayed through finite latest
                # state, but the full ordering source cannot retract historical
                # versions. Expose that distinction instead of labelling the
                # fast page exact.
                metadata.update(
                    {
                        "query_complete": public_chunk_complete,
                        "query_status": (
                            "complete" if public_chunk_complete else bounded_page.status
                        ),
                        "query_exact": False,
                        "query_provenance": "spans_per_session_candidate",
                        "ordering_exact": False,
                    }
                )
        if not candidate_cursor:
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
        if export and (
            page_number != 0
            or metadata.get("total_rows_is_lower_bound")
            or total_count != len(formatted)
        ):
            return self._gm.custom_error_response(
                drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                "A complete session export is temporarily unavailable. Narrow the filters and retry.",
                code="service_unavailable",
            )
        if metadata.get(
            "total_rows_is_lower_bound"
        ) and exact_total_explicitly_required(
            request,
            validated_data,
            allow_exact_cursor_lower_bound=True,
        ):
            return self._gm.custom_error_response(
                drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                "Session data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        if project_id:
            filter_evidence = applied_filter_attestation(
                project_id=project_id,
                observe_type="session",
                filters=attested_filters,
            )
            if filter_evidence["query_applied_filter_count"]:
                metadata.update(filter_evidence)

        return self._gm.success_response(
            {
                "metadata": metadata,
                "table": formatted,
                "config": config,
            }
        )

    @staticmethod
    def _fetch_session_scores(session_ids, annotation_labels):
        """Fetch Score data for paginated sessions, grouped by session + label."""
        if not session_ids or not annotation_labels:
            return {}

        scores = (
            Score.objects.filter(
                trace_session_id__in=session_ids,
                label__in=annotation_labels,
                deleted=False,
            )
            .select_related("label", "annotator")
            .order_by("trace_session_id", "label_id", "-created_at")
        )

        # Build: {session_id: {label_id: aggregated_value}}
        result = defaultdict(dict)
        for score in scores:
            sid = str(score.trace_session_id)
            lid = str(score.label_id)
            label_type = score.label.type
            val = score.value

            # Extract the display value from the Score JSON
            if label_type in (
                AnnotationTypeChoices.NUMERIC.value,
                AnnotationTypeChoices.STAR.value,
            ):
                display = (
                    val.get("value") or val.get("rating")
                    if isinstance(val, dict)
                    else val
                )
                try:
                    display = float(display)
                except (TypeError, ValueError):
                    display = None
            elif label_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
                raw = val.get("value") if isinstance(val, dict) else val
                display = raw
            elif label_type == AnnotationTypeChoices.CATEGORICAL.value:
                display = val.get("selected") if isinstance(val, dict) else val
            elif label_type == AnnotationTypeChoices.TEXT.value:
                display = val.get("text") if isinstance(val, dict) else val
            else:
                display = val

            # For multi-annotator, keep the latest (scores are ordered by -created_at)
            if lid not in result[sid]:
                annotator_name = ""
                if score.annotator:
                    annotator_name = score.annotator.name or score.annotator.email
                result[sid][lid] = {
                    "score": display,
                    "annotators": (
                        {
                            str(score.annotator_id): {
                                "user_id": str(score.annotator_id),
                                "user_name": annotator_name,
                                "score": display,
                            }
                        }
                        if score.annotator_id
                        else {}
                    ),
                }
            else:
                # Add this annotator's score
                if score.annotator_id:
                    aid = str(score.annotator_id)
                    annotator_name = ""
                    if score.annotator:
                        annotator_name = score.annotator.name or score.annotator.email
                    result[sid][lid]["annotators"][aid] = {
                        "user_id": aid,
                        "user_name": annotator_name,
                        "score": display,
                    }

        return result

    @staticmethod
    def _build_score_column_config(annotation_labels, project_id=None):
        """Build column config entries for score labels."""
        # Batch-fetch distinct annotators for all labels from Score.
        #
        # P3b step2 precondition (PG_ORM_READ_MIGRATION, Slice F): the read is
        # ALREADY project-scoped by ``label_id__in`` — an ``AnnotationsLabels`` id
        # belongs to exactly one project, so a Score on one of THIS project's
        # labels is, by construction, a Score in THIS project. The former
        # ``trace_session_id__in=TraceSession.objects.filter(project_id=…)``
        # subquery was therefore REDUNDANT for project scoping AND actively
        # harmful: post-flip the ingest ``get_or_create`` is dropped, so a NET-NEW
        # session (first seen after the flip) has NO PG ``trace_session`` row →
        # its ``trace_session_id`` is absent from that subquery → every annotator
        # who scored a net-new session was SILENTLY DROPPED from the label's
        # annotator set. Dropping the subquery surfaces those net-new-session
        # scores while keeping the historical row-set unchanged (parity-verified
        # on pg-test: HEAD subquery scope == this label-only scope on real data).
        #
        # NB the literal ``Score.project_id = project_id`` alternative is WRONG
        # here: ``Score.project`` is a nullable FK to ``model_hub.DevelopAI`` (NOT
        # ``tracer.Project``) and the tracer-side annotation write path leaves it
        # NULL on purpose (see ``tracer/views/annotation.py``
        # ``_process_single_annotation`` and ``backfill_scores``) — so a
        # ``project_id`` filter would drop EVERY
        # NULL-project session score (historical parity failure). ``label_id__in``
        # is the same scope the sibling live read ``_fetch_session_scores`` uses.
        # ``project_id`` is retained in the signature (both call sites pass it) but
        # is no longer needed to scope this read.
        label_ids = [label.id for label in annotation_labels]
        score_filter = {
            "label_id__in": label_ids,
            "trace_session_id__isnull": False,
            "deleted": False,
        }
        annotator_rows = (
            Score.objects.filter(**score_filter)
            .values(
                "label_id",
                "annotator_id",
                "annotator__name",
                "annotator__email",
            )
            .distinct()
        )
        label_annotators_map = {}
        for row in annotator_rows:
            lid = str(row["label_id"])
            uid = str(row["annotator_id"])
            if lid not in label_annotators_map:
                label_annotators_map[lid] = {}
            label_annotators_map[lid][uid] = {
                "user_id": uid,
                "user_name": row["annotator__name"]
                or row["annotator__email"]
                or "Unknown",
            }

        configs = []
        for label in annotation_labels:
            label_type = label.type
            if label_type == AnnotationTypeChoices.CATEGORICAL.value:
                output_type = "list"
            elif label_type == AnnotationTypeChoices.TEXT.value:
                output_type = "text"
            elif label_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
                output_type = "boolean"
            else:
                output_type = "float"

            choices = []
            if label_type == AnnotationTypeChoices.CATEGORICAL.value:
                choices = [
                    opt["label"] for opt in (label.settings or {}).get("options", [])
                ]

            config = asdict(
                FieldConfig(
                    id=str(label.id),
                    name=label.name,
                    group_by="Annotation Metrics",
                    is_visible=True,
                    output_type=output_type,
                    reverse_output=False,
                    annotation_label_type=label_type,
                    choices=choices if choices else None,
                    settings=label.settings,
                    annotators=label_annotators_map.get(str(label.id)),
                )
            )
            config.update(
                {
                    "property_id": f"annotation:{label.id}",
                    "property_kind": "annotation",
                    "property_source": "sessions",
                }
            )
            configs.append(config)
        return configs

    @validated_request(
        query_serializer=TraceSessionExportQuerySerializer,
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
    def get_trace_session_export_data(self, request, *args, **kwargs):
        """
        Export traces filtered by project ID and project version ID with optimized queries.
        """
        try:
            validated_data = request.validated_query_data
            response = self.list_sessions(request, bounded_export=True)

            if response.status_code != 200:
                return response

            project_id = str(validated_data["project_id"])
            project = _project_queryset_for_request(request).get(id=project_id)

            result = response.data.get("result", {})
            return bounded_page_csv_response(
                rows=result.get("table"),
                metadata=result.get("metadata"),
                filename=f"{project.name or 'project'}_sessions.csv",
            )

        except Exception as exc:
            logger.exception(
                "session_export_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.internal_server_error_response(
                "Sessions could not be exported"
            )

    @action(detail=True, methods=["get"])
    def eval_logs(self, request, *args, **kwargs):
        """Session-scoped eval log feed for TracesDrawer's "Evals" tab.

        Session-level eval results are walled off from span/trace surfaces
        by ``target_type='session'`` — this endpoint is the only place
        they appear.

        Query params:
            page (int, 0-indexed, default 0)
            page_size (int, default 25, max 100)

        Returns:
            Paginated response: {total, page, page_size, items}. Each item
            carries the same fields ``EvalTaskView.get_usage`` exposes, minus
            span/trace-only fields (NULL on session rows per the
            ``eval_logger_target_type_fks`` check constraint).
        """
        try:
            # get_object() applies the org-scoped queryset filter and
            # raises 404 if the caller can't access the session.
            trace_session_id = self.kwargs.get("pk")
            try:
                session = self.get_object()
                session_id = str(session.id)
                session_name = session.name or ""
            except Http404:
                session_fields = _resolve_ch_session_fields(request, trace_session_id)
                if not session_fields:
                    return self._gm.bad_request("Session not found.")
                session_id = str(trace_session_id)
                session_name = (
                    session_fields.get("display_name")
                    or session_fields.get("external_session_id")
                    or ""
                )

            qp = PaginationQuerySerializer(data=request.query_params)
            qp.is_valid(raise_exception=True)
            page = qp.validated_data["page"]
            page_size = qp.validated_data["page_size"]

            # Query the authoritative eval table through the CH25 connection.
            from tracer.services.clickhouse.eval_logger_table import (
                eval_logger_live_state_columns,
                eval_logger_source,
                eval_logger_version_column,
            )

            analytics = V2AnalyticsQueryService()
            read_deadline = ReadDeadline.start(SESSION_LIST_WALL_DEADLINE_MS)
            el_table, _ = eval_logger_source()
            _, el_pred = eval_logger_source(
                "latest_eval", include_cdc_tombstone_guard=True
            )
            el_version = eval_logger_version_column(el_table)
            live_projection = ", ".join(
                f"eval_scan.{column}"
                for column in eval_logger_live_state_columns(el_table)
            )
            read_settings = {
                **SESSION_LIST_READ_SETTINGS,
                "max_result_bytes": SESSION_LIST_RESULT_BYTES,
                "result_overflow_mode": "throw",
            }

            count_q = f"""
                SELECT count() AS cnt
                FROM (
                    SELECT
                        eval_scan.id,
                        {live_projection}
                    FROM {el_table} AS eval_scan
                    WHERE eval_scan.trace_session_id = %(sid)s
                      AND eval_scan.target_type = 'session'
                    ORDER BY eval_scan.{el_version} DESC
                    LIMIT 1 BY eval_scan.id
                ) AS latest_eval
                WHERE {el_pred}
            """
            count_r = analytics.execute_ch_query(
                count_q,
                {"sid": session_id},
                timeout_ms=read_deadline.remaining_ms(SESSION_LIST_QUERY_TIMEOUT_MS),
                settings={**read_settings, "max_result_rows": 1},
            )
            total = count_r.data[0]["cnt"] if count_r.data else 0

            items = []
            if total > 0:
                start = page * page_size
                logs_q = f"""
                    SELECT
                        toString(id) AS id,
                        toString(custom_eval_config_id) AS custom_eval_config_id,
                        output_bool,
                        output_float,
                        output_str,
                        error,
                        error_message,
                        eval_explanation,
                        results_explanation,
                        target_type,
                        created_at
                    FROM (
                        SELECT
                            eval_scan.id,
                            eval_scan.custom_eval_config_id,
                            eval_scan.output_bool,
                            eval_scan.output_float,
                            eval_scan.output_str,
                            eval_scan.error,
                            eval_scan.error_message,
                            eval_scan.eval_explanation,
                            eval_scan.results_explanation,
                            eval_scan.target_type,
                            eval_scan.created_at,
                            {live_projection}
                        FROM {el_table} AS eval_scan
                        WHERE eval_scan.trace_session_id = %(sid)s
                          AND eval_scan.target_type = 'session'
                        ORDER BY eval_scan.{el_version} DESC
                        LIMIT 1 BY eval_scan.id
                    ) AS latest_eval
                    WHERE {el_pred}
                    ORDER BY created_at DESC, id DESC
                    LIMIT %(limit)s OFFSET %(offset)s
                """
                logs_r = analytics.execute_ch_query(
                    logs_q,
                    {"sid": session_id, "limit": page_size, "offset": start},
                    timeout_ms=read_deadline.remaining_ms(
                        SESSION_LIST_QUERY_TIMEOUT_MS
                    ),
                    settings={
                        **read_settings,
                        "max_result_rows": page_size,
                    },
                )

                config_ids = {
                    r["custom_eval_config_id"]
                    for r in logs_r.data
                    if r.get("custom_eval_config_id")
                }
                config_map = {}
                if config_ids:
                    configs = CustomEvalConfig.objects.filter(
                        id__in=config_ids,
                        deleted=False,
                    ).select_related("eval_template")
                    config_map = {str(c.id): c for c in configs}

                for log in logs_r.data:
                    error = bool(log.get("error"))
                    output_bool = log.get("output_bool")
                    output_float = log.get("output_float")
                    output_str = log.get("output_str")
                    if error:
                        result_label = "Error"
                        score_val = None
                        status = "error"
                    elif output_bool == 1:
                        result_label = "Passed"
                        score_val = 1.0
                        status = "success"
                    elif output_bool == 0 and output_bool is not None:
                        result_label = "Failed"
                        score_val = 0.0
                        status = "success"
                    elif output_float is not None:
                        score_val = float(output_float)
                        result_label = "Passed" if score_val >= 0.5 else "Failed"
                        status = "success"
                    elif output_str:
                        result_label = str(output_str)[:50]
                        score_val = None
                        status = "success"
                    else:
                        result_label = ""
                        score_val = None
                        status = "success"

                    config = config_map.get(log.get("custom_eval_config_id"))
                    reason = (
                        log.get("eval_explanation") or log.get("error_message") or ""
                    )
                    created = log.get("created_at")

                    items.append(
                        {
                            "id": log["id"],
                            "input": session_name[:200],
                            "result": result_label,
                            "score": score_val,
                            "reason": reason,
                            "status": status,
                            "eval_status": None,
                            "source": "eval_task",
                            "created_at": (
                                created.isoformat()
                                if hasattr(created, "isoformat")
                                else str(created or "")
                            ),
                            "session_id": session_id,
                            "eval_id": str(config.id) if config else None,
                            "eval_name": config.name if config else None,
                            "model": config.model if config else None,
                            "detail": {
                                "eval_name": config.name if config else None,
                                "model": config.model if config else None,
                                "output_type": (
                                    config.eval_template.output_type_normalized
                                    if config and config.eval_template
                                    else None
                                ),
                                "target_type": log.get("target_type"),
                                "session_id": session_id,
                                "session_name": session_name,
                                "output_bool": output_bool,
                                "output_float": output_float,
                                "output_str": output_str,
                                "results_explanation": log.get("results_explanation"),
                                "error_message": log.get("error_message"),
                            },
                        }
                    )

            return self._gm.success_response(
                {
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "items": items,
                }
            )
        except ValidationError:
            return self._gm.bad_request("Session evaluation log request is invalid")
        except Exception as exc:
            if is_clickhouse_api_read_unavailable_error(exc):
                logger.warning(
                    "session_eval_logs_unavailable",
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    drf_status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Evaluation logs are temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "session_eval_logs_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                drf_status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Evaluation logs could not be loaded",
                code="server_error",
            )
