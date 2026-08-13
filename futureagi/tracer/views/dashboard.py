import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import blake2b
from math import isfinite
from numbers import Real
from time import monotonic
from types import SimpleNamespace
from uuid import UUID

import structlog
from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from tfc.routers import uses_db
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiErrorResponseSerializer,
)
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tracer.db_routing import DATABASE_FOR_DASHBOARD_LIST
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.models.project import Project
from tracer.serializers.dashboard import (
    DashboardCreateUpdateSerializer,
    DashboardDetailSerializer,
    DashboardFilterValuesQuerySerializer,
    DashboardFilterValuesResponseSerializer,
    DashboardMetricsCatalogResponseSerializer,
    DashboardPreviewQuerySerializer,
    DashboardQueryApiResponseSerializer,
    DashboardQuerySerializer,
    DashboardRefreshQuerySerializer,
    DashboardSampleOptInSerializer,
    DashboardSerializer,
    DashboardWidgetSerializer,
)
from tracer.services.annotation_label_source import AnnotationScoreReadUnavailable
from tracer.services.clickhouse.attribute_cursor_state import (
    AttributeCursorStateError,
    load_attribute_cursor_seen_state,
    persist_attribute_cursor_seen_state,
)
from tracer.services.clickhouse.attribute_reads import (
    ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS,
    ATTRIBUTE_READ_MAX_PROJECTS,
    AttributeReadSelector,
    InvalidAttributeKey,
)
from tracer.services.clickhouse.client import (
    get_clickhouse_client,
    is_clickhouse_enabled,
)
from tracer.services.clickhouse.filter_value_reads import (
    SYSTEM_FILTER_VALUE_METRICS,
    read_end_user_filter_value_cursor_page,
    read_span_system_filter_value_cursor_page,
    read_span_system_filter_values,
)
from tracer.services.clickhouse.list_cursor import (
    ListCursorError,
    cursor_scope_for_request,
    decode_list_cursor,
    encode_list_cursor,
)
from tracer.services.clickhouse.query_builders.dashboard import (
    GRANULARITY_TO_CH,
    METRIC_UNITS,
    InvalidMetricCombinationError,
    _generate_time_buckets,
)
from tracer.services.clickhouse.query_builders.dataset_dashboard import (
    DATASET_FILTER_COLUMNS,
    DATASET_METRIC_UNITS,
    DatasetQueryBuilder,
)
from tracer.services.clickhouse.query_builders.simulation_dashboard import (
    _STRING_DIMENSION_METRICS,
    SIMULATION_FILTER_COLUMNS,
    SIMULATION_METRIC_UNITS,
    SimulationQueryBuilder,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
    is_clickhouse_api_read_unavailable_error,
    is_clickhouse_query_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
from tracer.services.dashboard_metrics_catalog import get_cached_metrics_catalog
from tracer.services.exact_aggregation_cache import (
    read_or_schedule_exact_snapshot,
)
from tracer.utils.workspace_scope import (
    project_queryset_for_request,
    project_workspace_scope_q,
)
from tracer.views.span_attributes import (
    is_attribute_api_read_unavailable_error,
    retained_attribute_window_start,
)

logger = structlog.get_logger(__name__)


class DashboardExactReadError(RuntimeError):
    """A dashboard refresh did not produce every requested exact metric."""


class DashboardQueryScopeError(ValueError):
    """A requested dashboard scope is outside the current workspace."""


class DashboardBoundedReadError(RuntimeError):
    """A dashboard fast read cannot safely produce chartable aggregate data."""

    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


def _project_queryset_for_dashboard_scope(workspace):
    """Return the canonical project scope used by dashboard cache and worker reads."""

    organization = workspace.organization
    scope_request = SimpleNamespace(
        organization=organization,
        workspace=workspace,
        user=SimpleNamespace(organization=organization, workspace=workspace),
    )
    return project_queryset_for_request(scope_request)


def _materialize_dashboard_query_scope(
    query_config,
    workspace,
    *,
    trace_metrics,
    dataset_metrics,
    expand_empty_scopes=True,
):
    """Freeze every implicit all-resource scope into the exact cache identity.

    Empty ``project_ids``/``dataset_ids`` mean "all current workspace rows" at
    the API boundary. Keeping that sentinel in a long-lived cache key can serve
    a project after it moves out of the workspace or omit a resource added
    later. Resolve, authorize, stringify, and sort the concrete IDs before any
    cache read or refresh is scheduled. Worker replay sets
    ``expand_empty_scopes=False`` because an empty list in the frozen identity
    is a concrete empty scope, not the public all-resources sentinel.
    """

    scoped = {**query_config}
    if trace_metrics:
        try:
            requested_project_ids = [
                str(UUID(str(value))) for value in scoped.get("project_ids") or []
            ]
        except (AttributeError, TypeError, ValueError) as exc:
            raise DashboardQueryScopeError(
                "One or more project_ids are invalid"
            ) from exc
        project_ids = []
        if requested_project_ids or expand_empty_scopes:
            project_queryset = _project_queryset_for_dashboard_scope(workspace)
            if requested_project_ids:
                project_queryset = project_queryset.filter(id__in=requested_project_ids)
            project_ids = sorted(
                str(value) for value in project_queryset.values_list("id", flat=True)
            )
        if requested_project_ids and len(project_ids) != len(requested_project_ids):
            raise DashboardQueryScopeError(
                "One or more project_ids do not belong to this workspace"
            )
        scoped["project_ids"] = project_ids

    if dataset_metrics:
        from model_hub.utils.workspace_scope import scoped_dataset_queryset

        organization = workspace.organization
        scope_request = SimpleNamespace(
            organization=organization,
            workspace=workspace,
            user=SimpleNamespace(organization=organization, workspace=workspace),
        )
        try:
            requested_dataset_ids = [
                str(UUID(str(value))) for value in scoped.get("dataset_ids") or []
            ]
        except (AttributeError, TypeError, ValueError) as exc:
            raise DashboardQueryScopeError(
                "Some dataset_ids are invalid or not in this workspace"
            ) from exc
        dataset_ids = []
        if requested_dataset_ids or expand_empty_scopes:
            dataset_queryset = scoped_dataset_queryset(scope_request)
            if requested_dataset_ids:
                dataset_queryset = dataset_queryset.filter(id__in=requested_dataset_ids)
            dataset_ids = sorted(
                str(value) for value in dataset_queryset.values_list("id", flat=True)
            )
        if requested_dataset_ids and len(dataset_ids) != len(requested_dataset_ids):
            raise DashboardQueryScopeError(
                "Some dataset_ids are invalid or not in this workspace"
            )
        scoped["dataset_ids"] = dataset_ids

    return scoped


# Exact dashboards may hydrate wide attribute Maps. Do not impose a row-count
# ceiling: wide but selective production reads can legitimately scan many
# compact rows. Bound the work by wall time, bytes, memory, result size, and
# concurrency instead so a read either completes or fails closed without
# monopolising the shared cluster.
_DASHBOARD_TRACE_READ_SETTINGS = {
    "max_threads": 4,
    "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
    "max_memory_usage": 36 * 1024 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "max_result_rows": 250_000,
    "max_result_bytes": 64 * 1024 * 1024,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}
_DASHBOARD_TRACE_MAX_CONCURRENT_METRICS = 2
_DASHBOARD_EXACT_QUERY_TIMEOUT_MS = 9_500

# Property/value pickers have a stricter five-second interaction contract than
# the general analytics surface. Begin the request-owned wall before project
# and cursor preparation, leaving one second for response construction and
# transport. Exhaustion returns an advancing cursor over the same unconsumed
# interval, so the smaller wall changes page density, never exactness or
# retained-history reachability.
_FILTER_VALUES_INTERACTIVE_TIMEOUT_MS = 4_000
_FILTER_VALUE_BATCH_CURSOR_RESOURCE = "dashboard_filter_value_project_batches"
_FILTER_VALUE_BATCH_CURSOR_MARKER = "project_batches_v1"
_FILTER_VALUE_RETAINED_START = datetime(1970, 1, 1, tzinfo=UTC)


def _run_filter_value_pg_read(deadline, read):
    """Materialize one picker metadata read inside the request-owned wall.

    The process-wide middleware allows PostgreSQL statements to run for thirty
    seconds, which is longer than the entire property-picker interaction SLA.
    Each authoritative ORM phase therefore consumes the same four-second wall
    as ClickHouse. The transaction is explicitly read-only and never retries;
    expiry fails closed at the public boundary instead of publishing an empty
    vocabulary.
    """

    timeout_ms = deadline.remaining_ms(_FILTER_VALUES_INTERACTIVE_TIMEOUT_MS)
    if connection.vendor != "postgresql":
        return read()
    already_in_atomic_block = connection.in_atomic_block
    try:
        # A SELECT-only production qualifier and a normal request can both
        # already own the outer transaction.  Opening another atomic block in
        # that case emits SAVEPOINT/RELEASE statements and does not buy an
        # additional timeout boundary, so run directly inside the proven outer
        # transaction.  Only create a read-only transaction when this helper
        # owns the boundary.
        transaction_context = (
            nullcontext() if already_in_atomic_block else transaction.atomic()
        )
        with transaction_context:
            with connection.cursor() as cursor:
                # A direct SELECT harness may already own a read-only outer
                # transaction. PostgreSQL rejects SET TRANSACTION after a
                # savepoint/prior statement, so only declare read-only when
                # this helper owns the outer transaction.
                if not already_in_atomic_block:
                    cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    "SET LOCAL statement_timeout = %s",
                    [f"{timeout_ms}ms"],
                )
            return read()
    except DatabaseError as exc:
        raise ReadDeadlineExceeded(
            "Filter-value PostgreSQL read exceeded its request deadline"
        ) from exc


def _filter_value_digest(value: str) -> str:
    """Match the exact string-value digest used by the CH value selectors."""

    return blake2b(str(value).encode("utf-8"), digest_size=16).hexdigest()


def _valid_filter_value_project_ids(raw_project_ids) -> tuple[str, ...]:
    """Canonicalize request-owned project ids without consulting tenancy data."""

    valid: set[str] = set()
    for raw_project_id in raw_project_ids or ():
        try:
            valid.add(str(UUID(str(raw_project_id))))
        except (AttributeError, TypeError, ValueError):
            # Preserve the historical endpoint behavior: malformed and foreign
            # ids contribute no data. They also must not become a physical
            # project boundary in a signed continuation.
            continue
    return tuple(sorted(valid))


def _bounded_authorized_filter_value_projects(
    request,
    project_ids: tuple[str, ...],
    *,
    deadline,
) -> tuple[str, ...]:
    """Authorize at most one selector-sized project batch under the request wall."""

    if len(project_ids) > ATTRIBUTE_READ_MAX_PROJECTS:
        raise ValueError("filter-value project authorization batch is too large")
    if not project_ids:
        return ()
    rows = _run_filter_value_pg_read(
        deadline,
        lambda: list(
            project_queryset_for_request(request)
            .filter(id__in=project_ids)
            .order_by("id")
            .values_list("id", flat=True)[: ATTRIBUTE_READ_MAX_PROJECTS + 1]
        ),
    )
    return tuple(str(project_id) for project_id in rows)


@dataclass(frozen=True)
class _FilterValueProjectScope:
    """One authorized physical project batch and its resumable logical scope."""

    mode: str
    requested_project_ids: tuple[str, ...]
    project_ids: tuple[str, ...] = ()
    batch_end_project_id: str = ""
    explicit_scan_offset: int = 0
    has_later_projects: bool = False

    @property
    def batched(self) -> bool:
        return self.mode != "fixed"

    def cursor_identity(self) -> dict:
        return {
            "mode": self.mode,
            **(
                {"requested_project_ids": self.requested_project_ids}
                if self.mode == "explicit"
                else {}
            ),
        }


def _next_filter_value_project_batch(
    request,
    scope: _FilterValueProjectScope,
    *,
    deadline,
) -> _FilterValueProjectScope:
    """Resolve one bounded next batch without materializing a workspace scope."""

    if scope.mode == "workspace":
        projects = project_queryset_for_request(request).order_by("id")
        if scope.batch_end_project_id:
            projects = projects.filter(id__gt=scope.batch_end_project_id)
        rows = _run_filter_value_pg_read(
            deadline,
            lambda: list(
                projects.values_list("id", flat=True)[: ATTRIBUTE_READ_MAX_PROJECTS + 1]
            ),
        )
        project_ids = tuple(
            str(project_id) for project_id in rows[:ATTRIBUTE_READ_MAX_PROJECTS]
        )
        return replace(
            scope,
            project_ids=project_ids,
            batch_end_project_id=(
                project_ids[-1] if project_ids else scope.batch_end_project_id
            ),
            has_later_projects=len(rows) > ATTRIBUTE_READ_MAX_PROJECTS,
        )

    if scope.mode != "explicit":
        return scope

    offset = int(scope.explicit_scan_offset)
    candidates = scope.requested_project_ids
    # Process exactly one finite candidate chunk per gesture. A foreign-heavy
    # explicit list must not issue O(N/64) PostgreSQL statements while trying
    # to fill one authorized batch; an empty authorized chunk is itself signed
    # progress and the next gesture resumes at this candidate offset.
    candidate_chunk = candidates[offset : offset + ATTRIBUTE_READ_MAX_PROJECTS]
    offset += len(candidate_chunk)
    authorized = _bounded_authorized_filter_value_projects(
        request,
        candidate_chunk,
        deadline=deadline,
    )
    project_ids = tuple(authorized)
    return replace(
        scope,
        project_ids=project_ids,
        batch_end_project_id=(
            project_ids[-1] if project_ids else scope.batch_end_project_id
        ),
        explicit_scan_offset=offset,
        has_later_projects=offset < len(candidates),
    )


def _prepare_filter_value_project_scope(
    request,
    raw_project_ids,
    *,
    deadline,
    cursor_token: str | None,
) -> _FilterValueProjectScope:
    """Choose the compatibility or bounded-batch project-scope contract."""

    requested = _valid_filter_value_project_ids(raw_project_ids)
    if raw_project_ids:
        if len(requested) <= ATTRIBUTE_READ_MAX_PROJECTS:
            authorized = _bounded_authorized_filter_value_projects(
                request,
                requested,
                deadline=deadline,
            )
            return _FilterValueProjectScope(
                mode="fixed",
                requested_project_ids=requested,
                project_ids=authorized,
                batch_end_project_id=authorized[-1] if authorized else "",
            )
        scope = _FilterValueProjectScope(
            mode="explicit",
            requested_project_ids=requested,
            has_later_projects=True,
        )
    else:
        scope = _FilterValueProjectScope(
            mode="workspace",
            requested_project_ids=(),
            has_later_projects=True,
        )
    # A resumed batch cursor carries (and re-authorizes) its own finite batch.
    # Avoid querying the first workspace page merely to throw it away.
    if cursor_token:
        return scope
    return _next_filter_value_project_batch(request, scope, deadline=deadline)


def _reauthorize_filter_value_cursor_batch(
    request,
    project_ids: tuple[str, ...],
    *,
    deadline,
) -> None:
    authorized = _bounded_authorized_filter_value_projects(
        request,
        project_ids,
        deadline=deadline,
    )
    if authorized != project_ids:
        raise ListCursorError(
            "cursor_mismatch",
            "The continuation cursor no longer matches this workspace.",
        )


@dataclass(frozen=True)
class _BatchedFilterValueCursor:
    scope: _FilterValueProjectScope
    cursor_scope: dict
    cursor_query: dict
    cursor_state: object | None
    physical_order: tuple
    seen_reference: tuple
    new_project_batch: bool


def _batched_filter_value_cursor(
    request,
    scope: _FilterValueProjectScope,
    *,
    deadline,
    cursor_token: str | None,
    page_size: int,
    lane: str,
    query: dict,
) -> _BatchedFilterValueCursor:
    """Decode one tenant-bound cursor and re-authorize its finite project batch."""

    cursor_scope = cursor_scope_for_request(request, project_ids=[])
    cursor_query = {
        **query,
        "project_scope": scope.cursor_identity(),
    }
    if not cursor_token:
        return _BatchedFilterValueCursor(
            scope,
            cursor_scope,
            cursor_query,
            None,
            (),
            (),
            True,
        )

    cursor_state = decode_list_cursor(
        cursor_token,
        resource=_FILTER_VALUE_BATCH_CURSOR_RESOURCE,
        scope=cursor_scope,
        query=cursor_query,
        page_size=page_size,
    )
    if len(cursor_state.order) != 8:
        raise ListCursorError("invalid_cursor", "The continuation cursor is invalid.")
    (
        marker,
        batch_end_project_id,
        raw_project_ids,
        has_later_projects,
        explicit_scan_offset,
        stored_lane,
        physical_order,
        seen_reference,
    ) = cursor_state.order
    if (
        marker != _FILTER_VALUE_BATCH_CURSOR_MARKER
        or stored_lane != lane
        or not isinstance(batch_end_project_id, str)
        or not isinstance(raw_project_ids, tuple)
        or len(raw_project_ids) > ATTRIBUTE_READ_MAX_PROJECTS
        or tuple(sorted(set(raw_project_ids))) != raw_project_ids
        or any(not isinstance(project_id, str) for project_id in raw_project_ids)
        or not isinstance(has_later_projects, bool)
        or not isinstance(explicit_scan_offset, int)
        or explicit_scan_offset < 0
        or not isinstance(physical_order, tuple)
        or not isinstance(seen_reference, tuple)
        or (
            scope.mode == "explicit"
            and explicit_scan_offset > len(scope.requested_project_ids)
        )
        or (scope.mode == "workspace" and explicit_scan_offset != 0)
        or (raw_project_ids and batch_end_project_id != raw_project_ids[-1])
        or (
            scope.mode == "explicit"
            and any(
                project_id
                not in scope.requested_project_ids[
                    max(
                        0, explicit_scan_offset - ATTRIBUTE_READ_MAX_PROJECTS
                    ) : explicit_scan_offset
                ]
                for project_id in raw_project_ids
            )
        )
        or (not raw_project_ids and not has_later_projects)
    ):
        raise ListCursorError("invalid_cursor", "The continuation cursor is invalid.")

    resumed_scope = replace(
        scope,
        project_ids=raw_project_ids,
        batch_end_project_id=batch_end_project_id,
        explicit_scan_offset=explicit_scan_offset,
        has_later_projects=has_later_projects,
    )
    new_project_batch = not raw_project_ids
    if raw_project_ids:
        _reauthorize_filter_value_cursor_batch(
            request,
            raw_project_ids,
            deadline=deadline,
        )
    else:
        resumed_scope = _next_filter_value_project_batch(
            request,
            resumed_scope,
            deadline=deadline,
        )
    return _BatchedFilterValueCursor(
        resumed_scope,
        cursor_scope,
        cursor_query,
        cursor_state,
        physical_order,
        seen_reference,
        new_project_batch,
    )


def _load_batched_filter_value_seen_state(
    cursor: _BatchedFilterValueCursor,
    *,
    page_size: int,
    window_start: datetime,
    window_end: datetime,
):
    binding = {
        "scope": cursor.cursor_scope,
        "query": cursor.cursor_query,
        "page_size": page_size,
        "window_start": window_start,
        "window_end": window_end,
    }
    seen_state = load_attribute_cursor_seen_state(
        cursor.seen_reference,
        resource=_FILTER_VALUE_BATCH_CURSOR_RESOURCE,
        binding=binding,
        validate_digest=lambda value: (
            len(value) == 32
            and all(character in "0123456789abcdef" for character in value)
        ),
    )
    if cursor.cursor_state is not None and (
        cursor.cursor_state.seen_rows != seen_state.seen_count
    ):
        raise ListCursorError("invalid_cursor", "The continuation cursor is invalid.")
    return seen_state, binding


def _encode_batched_filter_value_cursor(
    cursor: _BatchedFilterValueCursor,
    *,
    page_size: int,
    window_start: datetime,
    window_end: datetime,
    seen_state,
    state_binding: dict,
    appended_digests,
    lane: str,
    physical_order: tuple,
    physical_has_more: bool,
) -> tuple[bool, str, str | None]:
    """Persist global de-duplication and encode the next physical/batch step."""

    advance_project_batch = not physical_has_more and cursor.scope.has_later_projects
    has_more = physical_has_more or advance_project_batch
    if not has_more:
        return False, "exhausted", None
    appended = tuple(appended_digests)
    seen_reference = persist_attribute_cursor_seen_state(
        seen_state,
        appended,
        resource=_FILTER_VALUE_BATCH_CURSOR_RESOURCE,
        binding=state_binding,
        validate_digest=lambda value: (
            len(value) == 32
            and all(character in "0123456789abcdef" for character in value)
        ),
    )
    next_order = (
        _FILTER_VALUE_BATCH_CURSOR_MARKER,
        cursor.scope.batch_end_project_id,
        () if advance_project_batch else cursor.scope.project_ids,
        cursor.scope.has_later_projects,
        cursor.scope.explicit_scan_offset,
        lane,
        physical_order,
        seen_reference,
    )
    next_cursor = encode_list_cursor(
        resource=_FILTER_VALUE_BATCH_CURSOR_RESOURCE,
        scope=cursor.cursor_scope,
        query=cursor.cursor_query,
        page_size=page_size,
        window_start=window_start,
        window_end=window_end,
        order=next_order,
        seen_rows=seen_state.seen_count + len(appended),
    )
    return True, "continuation", next_cursor


def _empty_batched_filter_value_payload(
    cursor: _BatchedFilterValueCursor,
    *,
    page_size: int,
    lane: str,
    window_start: datetime,
    window_end: datetime,
    extra: dict | None = None,
) -> dict:
    """Return an exact no-read batch transition or a terminal empty result."""

    seen_state, state_binding = _load_batched_filter_value_seen_state(
        cursor,
        page_size=page_size,
        window_start=window_start,
        window_end=window_end,
    )
    has_more, browse_status, next_cursor = _encode_batched_filter_value_cursor(
        cursor,
        page_size=page_size,
        window_start=window_start,
        window_end=window_end,
        seen_state=seen_state,
        state_binding=state_binding,
        appended_digests=(),
        lane=lane,
        physical_order=(),
        physical_has_more=False,
    )
    return {
        "values": [],
        "query_complete": True,
        "query_status": "complete",
        "query_window_start": window_start,
        "query_window_end": window_end,
        "has_more": has_more,
        "browse_status": browse_status,
        "next_cursor": next_cursor,
        **(extra or {}),
    }


def _batched_configured_filter_value_page(
    cursor: _BatchedFilterValueCursor,
    *,
    page_size: int,
    lane: str,
    window_start: datetime,
    window_end: datetime,
    values: list[dict],
    search: str,
) -> dict:
    """Page one stable configured vocabulary after batched target authorization."""

    filtered_values = _filter_value_options_for_search(values, search)
    physical_order = cursor.physical_order
    if cursor.new_project_batch:
        offset = 0
    elif (
        len(physical_order) != 1
        or not isinstance(physical_order[0], int)
        or physical_order[0] < 0
        or physical_order[0] > len(filtered_values)
    ):
        raise ListCursorError("invalid_cursor", "The continuation cursor is invalid.")
    else:
        offset = physical_order[0]
    page_values = filtered_values[offset : offset + page_size]
    next_offset = offset + len(page_values)
    physical_has_more = next_offset < len(filtered_values)
    # Once any authorized config for this exact template has been found, every
    # option comes from that one stable template. Scanning later projects would
    # only rediscover the same vocabulary, so terminate the project walk after
    # the configured ordinal page finishes.
    found_cursor = replace(
        cursor,
        scope=replace(cursor.scope, has_later_projects=False),
    )
    seen_state, state_binding = _load_batched_filter_value_seen_state(
        found_cursor,
        page_size=page_size,
        window_start=window_start,
        window_end=window_end,
    )
    has_more, browse_status, next_cursor = _encode_batched_filter_value_cursor(
        found_cursor,
        page_size=page_size,
        window_start=window_start,
        window_end=window_end,
        seen_state=seen_state,
        state_binding=state_binding,
        appended_digests=(),
        lane=lane,
        physical_order=(next_offset,),
        physical_has_more=physical_has_more,
    )
    return {
        "values": page_values,
        "query_complete": True,
        "query_status": "complete",
        "has_more": has_more,
        "browse_status": browse_status,
        "next_cursor": next_cursor,
    }


def _legacy_filter_value_scope_metadata(
    payload: dict,
    scope: _FilterValueProjectScope,
) -> dict:
    """Label a no-cursor first-batch response truthfully when scope remains."""

    if not scope.has_later_projects:
        return payload
    return {
        **payload,
        "query_complete": False,
        "query_status": "sampled",
        "query_error_code": "sample_limit",
        "has_more": False,
        "browse_status": "limit_reached",
        "next_cursor": None,
    }


def _filter_value_options_for_search(values, search):
    """Apply one Unicode-aware, case-insensitive picker search on the server."""

    needle = str(search or "").strip().casefold()
    if not needle:
        return list(values)
    searchable_fields = ("value", "label", "name", "email", "description")
    return [
        option
        for option in values
        if any(
            needle
            in str("" if option.get(field) is None else option.get(field)).casefold()
            for field in searchable_fields
        )
    ]


def _configured_filter_value_identity(value):
    """Return a stable, type-aware identity for an arbitrary JSON value."""

    return (
        type(value).__name__,
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
    )


def _append_configured_filter_value_option(options, seen, choice):
    """Append one configured option without coercing its JSON value."""

    raw_value = choice
    raw_label = choice
    if isinstance(choice, dict):
        raw_value = choice.get("value")
        if raw_value is None or raw_value == "":
            raw_value = choice.get("label")
        if raw_value is None or raw_value == "":
            raw_value = choice.get("name")

        raw_label = choice.get("label")
        if raw_label is None or raw_label == "":
            raw_label = choice.get("name")
        if raw_label is None or raw_label == "":
            raw_label = raw_value

    if raw_value is None or raw_value == "":
        return
    identity = _configured_filter_value_identity(raw_value)
    if identity in seen:
        return
    seen.add(identity)
    options.append({"value": raw_value, "label": str(raw_label)})


def _finite_filter_value_cursor_page(
    request,
    *,
    project_ids,
    query,
    values,
    search,
    page_size,
    cursor_token,
    query_complete=True,
):
    """Return an exact signed ordinal page over a finite server-held vocabulary.

    The full finite vocabulary is bound into the cursor query identity.  This
    helper must never receive a changing database sample: ordinal continuation
    is safe only for configuration/static values with a stable order.
    """

    filtered_values = _filter_value_options_for_search(values, search)
    cursor_scope = cursor_scope_for_request(request, project_ids=project_ids)
    cursor_query = {
        **query,
        "search": search,
        "content_identity": list(values),
    }
    cursor_resource = "dashboard_configured_filter_values"
    if cursor_token:
        cursor_state = decode_list_cursor(
            cursor_token,
            resource=cursor_resource,
            scope=cursor_scope,
            query=cursor_query,
            page_size=page_size,
        )
        if (
            len(cursor_state.order) != 1
            or not isinstance(cursor_state.order[0], int)
            or cursor_state.order[0] < 0
            or cursor_state.order[0] > len(filtered_values)
        ):
            raise ListCursorError(
                "invalid_cursor",
                "The continuation cursor is invalid.",
            )
        offset = cursor_state.order[0]
        window_start = cursor_state.window_start
        window_end = cursor_state.window_end
    else:
        offset = 0
        window_start = datetime(1970, 1, 1, tzinfo=UTC)
        window_end = datetime.now(UTC)

    page_values = filtered_values[offset : offset + page_size]
    next_offset = offset + len(page_values)
    has_more = next_offset < len(filtered_values)
    next_cursor = None
    if has_more:
        next_cursor = encode_list_cursor(
            resource=cursor_resource,
            scope=cursor_scope,
            query=cursor_query,
            page_size=page_size,
            window_start=window_start,
            window_end=window_end,
            order=(next_offset,),
            seen_rows=next_offset,
        )

    payload = {
        "values": page_values,
        "query_complete": bool(query_complete),
        "query_status": "complete" if query_complete else "sampled",
        "has_more": has_more,
        "browse_status": (
            "continuation"
            if has_more
            else "exhausted"
            if query_complete
            else "limit_reached"
        ),
        "next_cursor": next_cursor,
    }
    if not query_complete:
        payload["query_error_code"] = "sample_limit"
    return payload


# Public dashboard misses must return in the same wall budget as the rest of
# the interactive analytics surface. The rollup route issues at most two
# statements (span states plus the independently keyed trace-count states),
# and both consume one request-owned deadline.
_DASHBOARD_INTERACTIVE_TIMEOUT_MS = 9_500
_DASHBOARD_ROLLUP_MAX_QUERIES = 2
_DASHBOARD_ROLLUP_MAX_POINTS = 10_000
_DASHBOARD_ROLLUP_READ_SETTINGS = {
    "max_threads": 4,
    "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
    "max_memory_usage": 36 * 1024 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "max_result_rows": _DASHBOARD_ROLLUP_MAX_POINTS + 1,
    "max_result_bytes": 32 * 1024 * 1024,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}

_DASHBOARD_ROLLUP_SUM_COLUMNS = {
    "tokens": "total_tokens_sum",
    "total_tokens": "total_tokens_sum",
    "input_tokens": "prompt_tokens_sum",
    "output_tokens": "completion_tokens_sum",
    "cost": "cost_sum",
}
_DASHBOARD_ROLLUP_LATENCY_INDEX = {
    "avg": 1,
    "median": 1,
    "p50": 1,
    "p95": 2,
    "p99": 3,
}


def _fetch_exact_dashboard_rows(
    *,
    analytics,
    sql,
    params,
    timeout_ms,
    settings,
):
    """Run one exact full-window statement without rewriting query semantics.

    ``start_date`` and ``end_date`` can scope window-global latest-state,
    deduplication, evaluation, and filter relations in addition to the final
    output buckets. Replaying the SQL with narrower values is therefore not an
    equivalent partition. The executor sends the builder's SQL and parameters
    through unchanged and relies on finite ClickHouse read/result settings plus
    the worker timeout to fail closed.
    """

    result = analytics.execute_ch_query(
        sql,
        params=params,
        timeout_ms=timeout_ms,
        settings=settings,
    )
    return list(result.data or [])


def _pending_dashboard_payload(query_config):
    """Return a structurally valid response with no chartable aggregate data."""

    now = datetime.now(UTC).isoformat()
    return {
        "metrics": [],
        "time_range": {"start": now, "end": now},
        "granularity": query_config.get("granularity", "day"),
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
        "query_refreshing": True,
    }


def _dashboard_metric_key(metric):
    return str(metric.get("id") or metric.get("name") or "").strip().lower()


def _dashboard_rollup_expression(metric):
    """Return (physical source, aggregate expression, estimate strategy).

    Expressions are selected only from this code-owned whitelist. No request
    value is interpolated as a table, column, function, or alias.
    """

    if metric.get("type", "system_metric") != "system_metric":
        return None
    metric_name = _dashboard_metric_key(metric)
    aggregation = str(metric.get("aggregation") or "avg").lower()

    if metric_name == "latency":
        quantile_index = _DASHBOARD_ROLLUP_LATENCY_INDEX.get(aggregation)
        if quantile_index is None:
            return None
        strategy = (
            "hourly_tdigest_p50_proxy_for_average"
            if aggregation == "avg"
            else "hourly_tdigest"
        )
        return (
            "spans_hourly_rollup",
            f"(quantilesTDigestMerge(0.5, 0.95, 0.99)(latency_q))[{quantile_index}]",
            strategy,
        )

    if metric_name in _DASHBOARD_ROLLUP_SUM_COLUMNS:
        column = _DASHBOARD_ROLLUP_SUM_COLUMNS[metric_name]
        if aggregation == "sum":
            expression = f"sumMerge({column})"
        elif aggregation == "avg":
            expression = f"sumMerge({column}) / greatest(countMerge(n), 1)"
        elif aggregation == "count":
            expression = "countMerge(n)"
        else:
            return None
        return "spans_hourly_rollup", expression, "hourly_aggregate_states"

    if metric_name == "error_rate":
        if aggregation == "avg":
            expression = (
                "countIfMerge(error_count) * 100.0 / greatest(countMerge(n), 1)"
            )
        elif aggregation == "sum":
            expression = "countIfMerge(error_count)"
        elif aggregation == "count":
            expression = "countMerge(n)"
        else:
            return None
        return "spans_hourly_rollup", expression, "hourly_aggregate_states"

    if metric_name in {"span_count", "traffic"} and aggregation in {
        "count",
        "count_distinct",
        "sum",
    }:
        return "spans_hourly_rollup", "countMerge(n)", "hourly_aggregate_states"

    if metric_name == "project" and aggregation in {"count", "count_distinct"}:
        return "spans_hourly_rollup", "uniqExact(project_id)", "hourly_rollup_keys"

    if metric_name == "trace_count" and aggregation in {
        "count",
        "count_distinct",
    }:
        return (
            "trace_count_rollup",
            "uniqExactMerge(uniq_traces_state)",
            "hourly_exact_trace_states",
        )
    return None


def _dashboard_resolved_config(query_config):
    """Freeze a rolling preset once and enforce the public point ceiling."""

    window_builder = DatasetQueryBuilder(query_config)
    window_start, window_end = window_builder.parse_time_range()
    if window_start >= window_end:
        raise DashboardBoundedReadError("invalid_window")
    buckets = _generate_time_buckets(
        window_start,
        window_end,
        query_config.get("granularity", "day"),
    )
    if len(buckets) > _DASHBOARD_ROLLUP_MAX_POINTS:
        raise DashboardBoundedReadError("sample_limit")
    return (
        {
            **query_config,
            "time_range": {
                "custom_start": window_start.isoformat(),
                "custom_end": window_end.isoformat(),
            },
        },
        window_start,
        window_end,
    )


def _dashboard_degraded_payload(
    query_config,
    *,
    error_code,
    refresh_state=None,
):
    """Return typed unavailability without chartable zero or empty-bucket data."""

    try:
        window_start, window_end = DatasetQueryBuilder(query_config).parse_time_range()
    except (TypeError, ValueError):
        window_start = window_end = datetime.now(UTC)
    metrics = []
    for metric in query_config.get("metrics", []):
        metric_key = _dashboard_metric_key(metric)
        metrics.append(
            {
                "id": str(metric.get("id") or ""),
                "name": str(
                    metric.get("display_name")
                    or metric.get("displayName")
                    or metric.get("name")
                    or ""
                ),
                "aggregation": metric.get("aggregation", "avg"),
                "unit": metric.get("unit") or METRIC_UNITS.get(metric_key, ""),
                "series": [],
                "query_complete": False,
                "query_status": "degraded",
                "query_sampled": False,
                "query_exact": False,
                "query_provenance": "bounded_unavailable",
                "query_error_code": error_code,
            }
        )
    payload = {
        "metrics": metrics,
        "time_range": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
        },
        "granularity": query_config.get("granularity", "day"),
        "query_complete": False,
        "query_status": "degraded",
        "query_sampled": False,
        "query_exact": False,
        "query_provenance": "bounded_unavailable",
        "query_error_code": error_code,
    }
    if isinstance(refresh_state, dict):
        for key in ("query_refreshing", "query_refresh_failed"):
            if key in refresh_state:
                payload[key] = refresh_state[key]
    return payload


def _dashboard_refresh_or_degraded(query_config, *, refresh_state, error_code):
    """Keep polling an exact refresh; expose unavailability only without one."""

    if (
        isinstance(refresh_state, dict)
        and refresh_state.get("query_status") == "pending"
    ):
        return deepcopy(refresh_state)
    return _dashboard_degraded_payload(
        query_config,
        error_code=error_code,
        refresh_state=refresh_state,
    )


def _validate_dashboard_rollup_result(result, expected_columns):
    columns = list(getattr(result, "columns", None) or [])
    rows = getattr(result, "data", None)
    if not isinstance(rows, list) or not set(expected_columns).issubset(columns):
        raise DashboardBoundedReadError("malformed_result")
    if len(rows) > _DASHBOARD_ROLLUP_MAX_POINTS:
        raise DashboardBoundedReadError("sample_limit")
    for row in rows:
        if not isinstance(row, dict):
            raise DashboardBoundedReadError("malformed_result")
        time_bucket = row.get("time_bucket")
        if not isinstance(time_bucket, (str, date, datetime)):
            raise DashboardBoundedReadError("malformed_result")
        for column in expected_columns:
            if column == "time_bucket":
                continue
            if column not in row:
                raise DashboardBoundedReadError("malformed_result")
            value = row.get(column)
            if value is not None and not isinstance(value, (Real, Decimal)):
                raise DashboardBoundedReadError("malformed_result")
            if isinstance(value, Decimal) and not value.is_finite():
                raise DashboardBoundedReadError("malformed_result")
            if isinstance(value, Real) and not isfinite(float(value)):
                raise DashboardBoundedReadError("malformed_result")
    return rows


def _decorate_dashboard_exact_payload(payload):
    """Attest cached/worker payloads produced by the exact snapshot lane."""

    decorated = deepcopy(payload)
    if not isinstance(decorated, dict):
        return decorated
    decorated["query_exact"] = True
    decorated["query_provenance"] = "exact_snapshot"
    for metric in decorated.get("metrics", []):
        if isinstance(metric, dict):
            metric["query_exact"] = True
            metric["query_provenance"] = "exact_snapshot"
    return decorated


def _dashboard_snapshot_is_renderable(payload):
    return bool(
        isinstance(payload, dict)
        and payload.get("query_complete") is True
        and payload.get("query_status") == "complete"
        and payload.get("query_sampled") is False
        and isinstance(payload.get("metrics"), list)
        and all(
            isinstance(metric, dict)
            and metric.get("query_complete") is True
            and metric.get("query_status") == "complete"
            and metric.get("query_sampled") is False
            for metric in payload["metrics"]
        )
    )


def _read_dashboard_rollup_fast_path(
    query_config,
    *,
    refresh_state=None,
    deadline=None,
):
    """Read simple trace metrics from bounded materialized hourly states."""

    if (
        query_config.get("filters")
        or query_config.get("breakdowns")
        or query_config.get("granularity") == "minute"
    ):
        return _dashboard_refresh_or_degraded(
            query_config,
            refresh_state=refresh_state,
            error_code="bounded_shape_unavailable",
        )

    metrics = query_config.get("metrics", [])
    prepared = []
    for index, metric in enumerate(metrics):
        if metric.get("source", "traces") not in {"traces", "both", "all"}:
            return _dashboard_refresh_or_degraded(
                query_config,
                refresh_state=refresh_state,
                error_code="bounded_shape_unavailable",
            )
        if metric.get("filters"):
            return _dashboard_refresh_or_degraded(
                query_config,
                refresh_state=refresh_state,
                error_code="bounded_shape_unavailable",
            )
        expression = _dashboard_rollup_expression(metric)
        if expression is None:
            return _dashboard_refresh_or_degraded(
                query_config,
                refresh_state=refresh_state,
                error_code="bounded_shape_unavailable",
            )
        source, aggregate_expression, strategy = expression
        prepared.append(
            {
                "index": index,
                "metric": metric,
                "source": source,
                "alias": f"metric_{index}",
                "expression": aggregate_expression,
                "strategy": strategy,
            }
        )

    try:
        frozen_config, window_start, window_end = _dashboard_resolved_config(
            query_config
        )
    except DashboardBoundedReadError as exc:
        return _dashboard_refresh_or_degraded(
            query_config,
            error_code=exc.error_code,
            refresh_state=refresh_state,
        )

    project_ids = frozen_config.get("project_ids", [])
    builder = DashboardQueryBuilderV2(frozen_config)
    if not project_ids:
        metric_results = []
        for item in prepared:
            metric_info = builder.metric_info(item["metric"])
            metric_info.update(
                {
                    "source": "traces",
                    "query_complete": True,
                    "query_status": "complete",
                    "query_sampled": False,
                }
            )
            metric_results.append((metric_info, []))
        formatted = builder.format_results(metric_results)
        formatted.update(
            {
                "query_complete": True,
                "query_status": "complete",
                "query_sampled": False,
                "query_exact": True,
                "query_provenance": "authorized_empty_scope",
                "query_count": 0,
            }
        )
        for metric in formatted["metrics"]:
            metric.update(
                {
                    "query_exact": True,
                    "query_provenance": "authorized_empty_scope",
                }
            )
        if isinstance(refresh_state, dict):
            for key in ("query_refreshing", "query_refresh_failed"):
                if key in refresh_state:
                    formatted[key] = refresh_state[key]
        return formatted

    bucket_fn = GRANULARITY_TO_CH.get(frozen_config.get("granularity"))
    if bucket_fn is None:
        return _dashboard_refresh_or_degraded(
            frozen_config,
            error_code="bounded_shape_unavailable",
            refresh_state=refresh_state,
        )

    grouped = {}
    for item in prepared:
        grouped.setdefault(item["source"], []).append(item)
    if len(grouped) > _DASHBOARD_ROLLUP_MAX_QUERIES:
        return _dashboard_refresh_or_degraded(
            frozen_config,
            error_code="bounded_shape_unavailable",
            refresh_state=refresh_state,
        )

    if deadline is None:
        deadline = ReadDeadline.start(_DASHBOARD_INTERACTIVE_TIMEOUT_MS)
    try:
        deadline.remaining_ms(_DASHBOARD_INTERACTIVE_TIMEOUT_MS)
    except ReadDeadlineExceeded:
        return _dashboard_refresh_or_degraded(
            frozen_config,
            error_code="read_budget_exceeded",
            refresh_state=refresh_state,
        )
    rows_by_index = {item["index"]: [] for item in prepared}
    started = monotonic()
    query_count = 0
    rows_returned = 0
    try:
        # These materialized views are fed by the direct-write CH25 spans table;
        # bind the query to the same physical generation explicitly.
        analytics = V2AnalyticsQueryService()
        if not bool(getattr(analytics, "supports_per_query_read_settings", True)):
            return _dashboard_refresh_or_degraded(
                frozen_config,
                error_code="read_settings_unavailable",
                refresh_state=refresh_state,
            )
        for source, items in grouped.items():
            select_values = ",\n       ".join(
                f"{item['expression']} AS {item['alias']}" for item in items
            )
            if source == "spans_hourly_rollup":
                table = "spans_hourly_rollup"
            elif source == "trace_count_rollup":
                table = "trace_count_rollup"
            else:  # Defensive fence; source values are code-owned above.
                raise DashboardBoundedReadError("bounded_shape_unavailable")
            query = (
                f"SELECT {bucket_fn}(hour) AS time_bucket,\n"
                f"       {select_values}\n"
                f"FROM {table}\n"
                "PREWHERE project_id IN %(project_ids)s\n"
                "WHERE hour >= %(start_date)s\n"
                "  AND hour < %(end_date)s\n"
                "GROUP BY time_bucket\n"
                "ORDER BY time_bucket\n"
                "LIMIT %(result_limit)s"
            )
            expected_columns = ["time_bucket", *(item["alias"] for item in items)]
            result = analytics.execute_ch_query(
                query,
                {
                    "project_ids": project_ids,
                    "start_date": window_start,
                    "end_date": window_end,
                    "result_limit": _DASHBOARD_ROLLUP_MAX_POINTS + 1,
                },
                timeout_ms=deadline.remaining_ms(_DASHBOARD_INTERACTIVE_TIMEOUT_MS),
                settings=_DASHBOARD_ROLLUP_READ_SETTINGS,
            )
            rows = _validate_dashboard_rollup_result(result, expected_columns)
            query_count += 1
            rows_returned += len(rows)
            for item in items:
                rows_by_index[item["index"]] = [
                    {
                        "time_bucket": row["time_bucket"],
                        "value": row[item["alias"]],
                    }
                    for row in rows
                ]
    except DashboardBoundedReadError as exc:
        return _dashboard_refresh_or_degraded(
            frozen_config,
            error_code=exc.error_code,
            refresh_state=refresh_state,
        )
    except Exception as exc:
        if is_read_budget_error(exc):
            error_code = "read_budget_exceeded"
            logger.warning(
                "dashboard_rollup_read_budget_exceeded",
                query_count=query_count,
            )
        elif is_clickhouse_query_error(exc):
            error_code = "query_failed"
            logger.warning(
                "dashboard_rollup_read_unavailable",
                error_type=type(exc).__name__,
            )
        else:
            error_code = "query_failed"
            logger.exception(
                "dashboard_rollup_read_failed",
                error_type=type(exc).__name__,
            )
        return _dashboard_refresh_or_degraded(
            frozen_config,
            error_code=error_code,
            refresh_state=refresh_state,
        )

    metric_results = []
    for item in prepared:
        metric_info = builder.metric_info(item["metric"])
        metric_info.update(
            {
                "source": "traces",
                "query_complete": True,
                "query_status": "complete",
                "query_sampled": False,
                "query_sampling_strategy": item["strategy"],
                "query_sampling_interval_seconds": 3_600,
            }
        )
        metric_results.append((metric_info, rows_by_index[item["index"]]))
    formatted = builder.format_results(metric_results)
    formatted.update(
        {
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
            "query_exact": False,
            "query_provenance": "materialized_rollup",
            "query_count": query_count,
            "query_rows_returned": rows_returned,
            "query_elapsed_ms": round((monotonic() - started) * 1000, 2),
        }
    )
    if isinstance(refresh_state, dict):
        for key in ("query_refreshing", "query_refresh_failed"):
            if key in refresh_state:
                formatted[key] = refresh_state[key]
    for item, formatted_metric in zip(prepared, formatted["metrics"], strict=True):
        formatted_metric.update(
            {
                "query_exact": False,
                "query_provenance": "materialized_rollup",
                "query_sampling_strategy": item["strategy"],
                "query_sampling_interval_seconds": 3_600,
            }
        )
    try:
        deadline.remaining_ms(floor_ms=1)
    except ReadDeadlineExceeded:
        return _dashboard_refresh_or_degraded(
            frozen_config,
            error_code="read_budget_exceeded",
            refresh_state=refresh_state,
        )
    return formatted


def _read_public_dashboard_query(
    query_config,
    *,
    cache_identity,
    refresh,
    deadline=None,
    try_rollup=True,
):
    """Serve cached exact data or one bounded, honest cold response."""

    if deadline is None:
        deadline = ReadDeadline.start(_DASHBOARD_INTERACTIVE_TIMEOUT_MS)
    try:
        # Snapshot scheduling may spend up to two seconds in Redis/Temporal.
        # Reserve that time inside the same public wall instead of beginning a
        # fresh dispatch after synchronous ClickHouse work consumed the budget.
        deadline.remaining_ms(floor_ms=2_100)
    except ReadDeadlineExceeded:
        return _dashboard_degraded_payload(
            query_config,
            error_code="read_budget_exceeded",
        )
    if not try_rollup:
        # This is the post-synchronous-read fallback. Starting Redis/Temporal
        # reconciliation here would create a fresh wall after ClickHouse used
        # most of the request budget. Background refresh remains available on
        # the normal cold path; this response fails fast and honestly.
        return _dashboard_degraded_payload(
            query_config,
            error_code="read_budget_exceeded",
        )
    try:
        snapshot = read_or_schedule_exact_snapshot(
            "dashboard-query",
            cache_identity,
            refresh=bool(refresh),
            pending_payload=_pending_dashboard_payload(query_config),
        )
    except Exception:
        logger.exception("dashboard_exact_snapshot_schedule_failed")
        snapshot = {"query_refreshing": False, "query_refresh_failed": True}
    if _dashboard_snapshot_is_renderable(snapshot):
        return _decorate_dashboard_exact_payload(snapshot)
    return _read_dashboard_rollup_fast_path(
        query_config,
        refresh_state=snapshot if isinstance(snapshot, dict) else None,
        deadline=deadline,
    )


def _complete_empty_metric_results(builder, source):
    """Return exact empty rows for a concrete resource scope with no members."""

    results = []
    for metric in builder.metrics:
        metric_info = builder.metric_info(metric)
        metric_info.update(
            {
                "source": source,
                "query_complete": True,
                "query_status": "complete",
                "query_sampled": False,
            }
        )
        results.append((metric_info, []))
    return results


DASHBOARD_FILTER_COL_TYPE_TO_METRIC_TYPE = {
    "SYSTEM_METRIC": "system_metric",
    "EVAL_METRIC": "eval_metric",
    "ANNOTATION": "annotation_metric",
    "SPAN_ATTRIBUTE": "custom_attribute",
    "CUSTOM_COLUMN": "custom_column",
}

DASHBOARD_FILTER_OP_TO_INTERNAL = {
    "equals": "equal_to",
    "not_equals": "not_equal_to",
    "in": "contains",
    "not_in": "not_contains",
    "contains": "str_contains",
    "not_contains": "str_not_contains",
    "is_not_null": "is_set",
    "is_null": "is_not_set",
}

DASHBOARD_INTERNAL_FILTER_OP_TO_API = {
    internal_op: api_op
    for api_op, internal_op in DASHBOARD_FILTER_OP_TO_INTERNAL.items()
}

DASHBOARD_METRIC_TYPE_TO_FILTER_COL_TYPE = {
    metric_type: col_type
    for col_type, metric_type in DASHBOARD_FILTER_COL_TYPE_TO_METRIC_TYPE.items()
}

_DASHBOARD_CANONICAL_FILTER_KEYS = {
    "column_id",
    "display_name",
    "source",
    "output_type",
    "filter_config",
}
_DASHBOARD_NUMERIC_FILTER_OPS = {
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
    "between",
    "not_between",
}
_DASHBOARD_LEGACY_NUMERIC_FILTER_OPS = {
    "is_numeric": ("not_equals", 0),
    "is_not_numeric": ("equals", 0),
}


def _legacy_dashboard_filter_type(filter_item, filter_op):
    """Infer the canonical validation type without changing legacy semantics."""

    raw_type = str(
        filter_item.get("attribute_type")
        or filter_item.get("data_type")
        or filter_item.get("filter_type")
        or ""
    ).lower()
    type_aliases = {
        "string": "text",
        "str": "text",
        "float": "number",
        "integer": "number",
        "int": "number",
        "date": "datetime",
        "object": "map",
        "json": "map" if isinstance(filter_item.get("value"), dict) else "array",
        "list": "array",
    }
    if raw_type:
        return type_aliases.get(raw_type, raw_type)
    if isinstance(filter_item.get("value"), bool):
        return "boolean"
    if filter_op in _DASHBOARD_NUMERIC_FILTER_OPS:
        return "number"
    return "text"


def _canonicalize_persisted_dashboard_filter_for_read(filter_item):
    """Return one canonical filter from either current or legacy storage.

    Dashboard widgets created before the canonical filter contract persisted
    the query builder's flattened ``metric_name``/``operator``/``value``
    shape.  Writes remain strict; this adapter exists only on the widget read
    path so those immutable historical configs can still be executed.
    """

    if not isinstance(filter_item, dict):
        return filter_item

    config = filter_item.get("filter_config")
    if "column_id" in filter_item and isinstance(config, dict):
        return {**filter_item, "filter_config": dict(config)}

    canonical_filter = filter_item.get("canonical_filter")
    if isinstance(canonical_filter, dict):
        restored = {
            key: value
            for key, value in canonical_filter.items()
            if key in _DASHBOARD_CANONICAL_FILTER_KEYS
        }
        canonical_config = restored.get("filter_config")
        if isinstance(canonical_config, dict):
            restored["filter_config"] = dict(canonical_config)
        return restored

    column_id = filter_item.get("metric_name")
    operator = filter_item.get("operator")
    metric_type = filter_item.get("metric_type") or "system_metric"
    col_type = DASHBOARD_METRIC_TYPE_TO_FILTER_COL_TYPE.get(metric_type)
    if not column_id or not operator or not col_type:
        # Let the strict serializer reject unknown/malformed historical data;
        # its details are sanitized by the caller before crossing the API.
        return filter_item

    legacy_numeric_op = _DASHBOARD_LEGACY_NUMERIC_FILTER_OPS.get(operator)
    if legacy_numeric_op:
        filter_op, filter_value = legacy_numeric_op
        filter_type = "number"
    else:
        filter_op = DASHBOARD_INTERNAL_FILTER_OP_TO_API.get(operator, operator)
        filter_value = filter_item.get("value")
        filter_type = _legacy_dashboard_filter_type(filter_item, filter_op)
    if filter_op in {"in", "not_in"} and not isinstance(filter_value, list):
        filter_value = [filter_value]

    canonical_config = {
        "filter_type": filter_type,
        "filter_op": filter_op,
        "filter_value": filter_value,
        "col_type": col_type,
    }
    restored = {
        "column_id": column_id,
        "filter_config": canonical_config,
    }
    for key in ("display_name", "source", "output_type"):
        if filter_item.get(key) is not None:
            restored[key] = filter_item[key]
    return restored


def _canonicalize_persisted_dashboard_query_filters_for_read(query_config):
    """Canonicalize legacy read filters in memory; never mutate caller JSON."""

    if not isinstance(query_config, dict):
        return query_config
    restored = dict(query_config)
    filters = query_config.get("filters")
    if isinstance(filters, list):
        restored["filters"] = [
            _canonicalize_persisted_dashboard_filter_for_read(filter_item)
            for filter_item in filters
        ]

    metrics = query_config.get("metrics")
    if isinstance(metrics, list):
        restored_metrics = []
        for metric in metrics:
            if not isinstance(metric, dict):
                restored_metrics.append(metric)
                continue
            metric_copy = dict(metric)
            metric_filters = metric.get("filters")
            if isinstance(metric_filters, list):
                metric_copy["filters"] = [
                    _canonicalize_persisted_dashboard_filter_for_read(filter_item)
                    for filter_item in metric_filters
                ]
            restored_metrics.append(metric_copy)
        restored["metrics"] = restored_metrics
    return restored


class DashboardReadQuerySerializer(DashboardQuerySerializer):
    """Accept historical filter storage shapes on query/read endpoints only.

    Dashboard writes continue to use the strict canonical serializer.  The
    read-only query endpoint, however, must be able to replay a saved widget's
    historical flattened metric filters when the frontend submits that same
    config as an ad-hoc query.
    """

    class Meta(DashboardQuerySerializer.Meta):
        # This adapter changes runtime read compatibility only. Keep the public
        # request-body component identical to the existing DashboardQuery
        # contract so generated clients do not see a new schema/ref.
        ref_name = "DashboardQuery"

    def to_internal_value(self, data):
        # Compatibility canonicalization must never iterate or silently coerce
        # malformed collection values. Although FilterListField's parser can
        # decode an optional empty query-param value, DRF rejects explicit JSON
        # ``null`` before that parser for this body field. Preserve the existing
        # DashboardQuery request contract and reject every non-list shape with a
        # bounded validation error before the read adapter touches it.
        if isinstance(data, dict):
            if "filters" in data and not isinstance(data["filters"], list):
                raise serializers.ValidationError(
                    {"filters": ["Expected a list of filter objects."]}
                )

            metrics = data.get("metrics")
            if "metrics" in data and not isinstance(metrics, list):
                raise serializers.ValidationError(
                    {"metrics": ["Expected a list of metric objects."]}
                )

            if isinstance(metrics, list):
                metric_errors = [{} for _metric in metrics]
                has_metric_filter_error = False
                for index, metric in enumerate(metrics):
                    if (
                        isinstance(metric, dict)
                        and "filters" in metric
                        and not isinstance(metric["filters"], list)
                    ):
                        metric_errors[index] = {
                            "filters": ["Expected a list of filter objects."]
                        }
                        has_metric_filter_error = True
                if has_metric_filter_error:
                    raise serializers.ValidationError({"metrics": metric_errors})

        return super().to_internal_value(
            _canonicalize_persisted_dashboard_query_filters_for_read(data)
        )


def _dashboard_filter_to_internal(filter_item):
    config = filter_item.get("filter_config") if isinstance(filter_item, dict) else None
    if not isinstance(config, dict):
        return filter_item

    col_type = config.get("col_type") or "SYSTEM_METRIC"
    metric_type = DASHBOARD_FILTER_COL_TYPE_TO_METRIC_TYPE.get(
        col_type, "system_metric"
    )
    filter_type = config.get("filter_type") or "text"
    internal = {
        "metric_type": metric_type,
        "metric_name": filter_item.get("column_id"),
        "operator": DASHBOARD_FILTER_OP_TO_INTERNAL.get(
            config.get("filter_op"), config.get("filter_op")
        ),
        "value": config.get("filter_value"),
        "source": filter_item.get("source", "traces"),
        # The bounded dashboard lane reuses the trace list classifier, whose
        # public filter contract is this validated canonical object. Keep it
        # as data; the dashboard SQL compiler continues to use the flattened
        # fields above.
        "canonical_filter": filter_item,
    }
    if filter_item.get("output_type"):
        internal["output_type"] = filter_item["output_type"]
    if metric_type == "custom_attribute":
        internal["attribute_type"] = "number" if filter_type == "number" else "string"
    return internal


def _normalize_dashboard_query_filters(query_config):
    """Translate canonical API filters to the dashboard builders' internal shape."""
    query_config = dict(query_config)
    query_config["filters"] = [
        _dashboard_filter_to_internal(filter_item)
        for filter_item in query_config.get("filters", [])
    ]
    metrics = []
    for metric in query_config.get("metrics", []):
        metric_copy = dict(metric)
        metric_copy["filters"] = [
            _dashboard_filter_to_internal(filter_item)
            for filter_item in metric_copy.get("filters", [])
        ]
        metrics.append(metric_copy)
    query_config["metrics"] = metrics
    return query_config


class DashboardViewSet(BaseModelViewSetMixin, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = DashboardSerializer
    lookup_value_regex = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

    def get_queryset(self):
        return super().get_queryset().select_related("created_by", "updated_by")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return DashboardDetailSerializer
        if self.action in ("create", "update", "partial_update"):
            return DashboardCreateUpdateSerializer
        return DashboardSerializer

    def _get_trace_query_timeout_ms(self, trace_config):
        """Return the dashboard-wide interactive/worker query ceiling."""
        return _DASHBOARD_EXACT_QUERY_TIMEOUT_MS

    @staticmethod
    def _run_metric_queries(
        builder,
        source,
        fetch_rows,
        *,
        max_workers=4,
        prepared_queries=None,
    ):
        """Build + execute each metric in parallel; return [(metric_info, rows)].

        Invalid combinations and explicit read-budget exhaustion are isolated
        to the affected metric. Programming, compiler, and transport defects
        still propagate so they cannot masquerade as valid empty charts.
        """
        work_items = (
            [(metric, None, None) for metric in builder.metrics]
            if prepared_queries is None
            else list(prepared_queries)
        )
        if not work_items:
            return []

        def _exec_one(work_item):
            metric, prepared_sql, prepared_params = work_item
            metric_info = builder.metric_info(metric)
            metric_info["source"] = source
            try:
                if prepared_sql is None:
                    sql, params = builder.build_metric_query(metric)
                else:
                    sql, params = prepared_sql, prepared_params
                rows = fetch_rows(sql, params)
                metric_info.update(
                    {
                        "query_complete": True,
                        "query_status": "complete",
                        "query_sampled": False,
                    }
                )
                return (metric_info, rows)
            except InvalidMetricCombinationError as e:
                metric_info.update(
                    {
                        "query_complete": False,
                        "query_status": "degraded",
                        "query_error_code": "query_failed",
                        "error": str(e),
                    }
                )
                return (metric_info, [])
            except Exception as exc:
                if not is_read_budget_error(exc):
                    raise
                logger.warning(
                    "dashboard_metric_read_budget_exceeded",
                    metric_name=str(metric_info.get("name") or metric_info.get("id")),
                )
                metric_info.update(
                    {
                        "query_complete": False,
                        "query_status": "degraded",
                        "query_error_code": "read_budget_exceeded",
                        "error": "This dashboard metric exceeded its read budget.",
                    }
                )
                return (metric_info, [])

        if len(work_items) == 1:
            return [_exec_one(work_items[0])]

        with ThreadPoolExecutor(max_workers=min(len(work_items), max_workers)) as pool:
            futures = [pool.submit(_exec_one, item) for item in work_items]
        return [f.result() for f in futures]

    @staticmethod
    def _prepare_metric_queries(builder):
        """Build each metric once before concurrent full-window execution."""

        prepared = []
        for metric in builder.metrics:
            try:
                sql, params = builder.build_metric_query(metric)
            except InvalidMetricCombinationError as exc:
                raise DashboardExactReadError(
                    "one or more dashboard metrics cannot be read exactly"
                ) from exc
            prepared.append((metric, sql, params))
        return tuple(prepared)

    def _format_merged_metric_results(self, query_config, all_metric_results):
        formatter = DatasetQueryBuilder(
            {**query_config, "metrics": query_config["metrics"]}
        )
        start_date, end_date = formatter.parse_time_range()
        from tracer.services.clickhouse.query_builders.dashboard_base import (
            _generate_time_buckets,
        )

        all_buckets = _generate_time_buckets(
            start_date, end_date, formatter.granularity
        )
        unit_map = {**METRIC_UNITS, **DATASET_METRIC_UNITS, **SIMULATION_METRIC_UNITS}
        formatted_metrics = []
        for metric_info, rows in all_metric_results:
            formatted_metrics.append(
                formatter._format_metric_result(
                    metric_info, rows, all_buckets, unit_map
                )
            )

        return {
            "metrics": formatted_metrics,
            "time_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "granularity": formatter.granularity,
        }

    def _run_simulation_analytics_queries(self, analytics, simulation_config):
        builder = SimulationQueryBuilder(simulation_config)
        return DashboardViewSet._run_metric_queries(
            builder,
            "simulation",
            lambda sql, params: (
                analytics.execute_ch_query(sql, params, timeout_ms=10000).data
            ),
        )

    def _run_simulation_clickhouse_queries(self, ch_client, simulation_config):
        def _fetch_rows(sql, params):
            rows, column_types, _ = ch_client.execute_read(sql, params)
            col_names = [ct[0] for ct in column_types]
            return [dict(zip(col_names, row, strict=True)) for row in rows]

        builder = SimulationQueryBuilder(simulation_config)
        return DashboardViewSet._run_metric_queries(builder, "simulation", _fetch_rows)

    def _normalize_metric_sources(self, metrics):
        """Route simulation-scoped trace attributes through the trace builder.

        The metric picker can save trace attributes with ``source=simulation``
        for simulation workflow widgets. Those attributes still live on spans,
        so sending them to ``SimulationQueryBuilder`` yields empty series.
        """
        normalized = []
        for metric in metrics:
            metric_copy = dict(metric)
            if (
                metric_copy.get("source") == "simulation"
                and metric_copy.get("type") == "custom_attribute"
            ):
                metric_copy["source"] = "traces"
            normalized.append(metric_copy)
        return normalized

    @uses_db(DATABASE_FOR_DASHBOARD_LIST, feature_key="feature:dashboard_list")
    def list(self, request, *args, **kwargs):
        try:
            # Route the main list read to replica when "feature:dashboard_list"
            # is opted in. Note: DashboardSerializer.get_widget_count() does
            # an `obj.widgets.filter().count()` per row that goes through the
            # router for DashboardWidget (and likely lands on `default`).
            # That's a pre-existing N+1 we are NOT fixing here — pure-routing
            # change only. Fixing the serializer is a separate refactor.
            queryset = self.get_queryset().using(DATABASE_FOR_DASHBOARD_LIST)
            serializer = DashboardSerializer(
                queryset, many=True, context={"request": request}
            )
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.error(f"Failed to list dashboards: {e}", exc_info=True)
            return self._gm.bad_request("Failed to list dashboards.")

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = DashboardDetailSerializer(
                instance, context={"request": request}
            )
            return self._gm.success_response(serializer.data)
        except Dashboard.DoesNotExist:
            return self._gm.not_found("Dashboard not found.")
        except Exception as e:
            logger.error(f"Failed to retrieve dashboard: {e}", exc_info=True)
            return self._gm.bad_request("Failed to retrieve dashboard.")

    def create(self, request, *args, **kwargs):
        try:
            serializer = DashboardCreateUpdateSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            dashboard = serializer.save(
                workspace=request.workspace,
                created_by=request.user,
                updated_by=request.user,
            )
            response_serializer = DashboardDetailSerializer(
                dashboard, context={"request": request}
            )
            return self._gm.success_response(response_serializer.data)
        except Exception as e:
            logger.error(f"Failed to create dashboard: {e}", exc_info=True)
            return self._gm.bad_request("Failed to create dashboard.")

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = DashboardCreateUpdateSerializer(
                instance, data=request.data, partial=kwargs.get("partial", False)
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            dashboard = serializer.save(updated_by=request.user)
            response_serializer = DashboardDetailSerializer(
                dashboard, context={"request": request}
            )
            return self._gm.success_response(response_serializer.data)
        except Exception as e:
            logger.error(f"Failed to update dashboard: {e}", exc_info=True)
            return self._gm.bad_request("Failed to update dashboard.")

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            deleted_at = timezone.now()
            DashboardWidget.objects.filter(
                dashboard=instance,
                deleted=False,
            ).update(deleted=True, deleted_at=deleted_at)
            instance.deleted = True
            instance.deleted_at = deleted_at
            instance.updated_by = request.user
            instance.save(
                update_fields=["deleted", "deleted_at", "updated_by", "updated_at"]
            )
            return self._gm.success_response("Dashboard deleted successfully.")
        except Exception as e:
            logger.error(f"Failed to delete dashboard: {e}", exc_info=True)
            return self._gm.bad_request("Failed to delete dashboard.")

    # ------------------------------------------------------------------
    # Query endpoint — routes each metric to the right builder by source
    # ------------------------------------------------------------------

    @validated_request(
        request_serializer=DashboardReadQuerySerializer,
        query_serializer=DashboardRefreshQuerySerializer,
        responses={
            200: DashboardQueryApiResponseSerializer,
            400: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"])
    def query(self, request):
        """Execute a widget query and return chart data.

        Each metric carries a ``source`` field ("traces" or "datasets").
        Metrics are partitioned by source and dispatched to the appropriate
        query builder.  Results are merged into a single response.

        Each metric is validated against the canonical query contract before
        it reaches any query builder.
        """
        query_config = _normalize_dashboard_query_filters(request.validated_data)
        # Kept in the request schema for older clients, but aggregation never
        # takes a sampled execution path.
        query_config["allow_sampled"] = False

        query_config["metrics"] = self._normalize_metric_sources(
            query_config["metrics"]
        )

        # Partition metrics by source
        # "both" source metrics (e.g. annotations) go to trace_metrics
        trace_metrics = [
            m
            for m in query_config["metrics"]
            if m.get("source") in ("traces", "both", "all")
        ]
        dataset_metrics = [
            m for m in query_config["metrics"] if m.get("source") == "datasets"
        ]
        # Authorization must be rechecked before serving a cached result; the
        # concrete scope is also part of the key so moves/additions cannot reuse
        # an all-resources snapshot produced for different membership.
        try:
            query_config = _materialize_dashboard_query_scope(
                query_config,
                request.workspace,
                trace_metrics=trace_metrics,
                dataset_metrics=dataset_metrics,
            )
        except DashboardQueryScopeError as exc:
            return self._gm.bad_request(str(exc))

        refresh = request.validated_query_data["refresh"]
        cache_identity = {
            "workspace_id": str(request.workspace.id),
            "query_config": query_config,
        }
        return self._gm.success_response(
            _read_public_dashboard_query(
                query_config,
                cache_identity=cache_identity,
                refresh=refresh,
            )
        )

    # ------------------------------------------------------------------
    # Unified metrics endpoint — all sources, no workflow selector
    # ------------------------------------------------------------------

    @validated_request(
        responses={
            200: DashboardMetricsCatalogResponseSerializer,
            400: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def metrics(self, request):
        """Return all available metrics across traces and datasets.

        Backward compat: if ``workflow`` param is provided, return only
        that source's metrics in the old grouped format.
        """
        workflow = request.query_params.get("workflow", "")
        workspace = request.workspace

        # Backward compat — old clients pass workflow
        if workflow == "dataset":
            return self._metrics_dataset_legacy(request)

        # --- Unified: collect from all sources ---
        try:
            metrics = get_cached_metrics_catalog(
                workspace,
                project_ids_param=request.query_params.get("project_ids", ""),
                agent_definition_id=(
                    request.query_params.get("agent_definition_id", "") or ""
                ),
                per_eval_config=(request.query_params.get("per_eval_config") == "true"),
                include_custom_attributes=(
                    request.query_params.get("exclude_custom_attributes") != "true"
                ),
            )

            # --- Optional server-side filtering & pagination ---
            search = request.query_params.get("search", "").strip()
            category = request.query_params.get("category", "").strip()
            source = request.query_params.get("source", "").strip()
            page = request.query_params.get("page", "")
            page_size = request.query_params.get("page_size", "")

            # If no pagination params, return all (backward compat)
            if (
                not page
                and not page_size
                and not search
                and not category
                and not source
            ):
                return self._gm.success_response({"metrics": metrics})

            # Filter by category
            if category:
                metrics = [m for m in metrics if m.get("category") == category]

            # Filter by source (eval metrics with source="all" only show
            # in the Evals tab, not in every source tab)
            if source:
                metrics = [
                    m
                    for m in metrics
                    if m.get("source") == source or source in (m.get("sources") or [])
                ]

            # Filter by search (case-insensitive contains on display_name and name)
            if search:
                q = search.lower()
                metrics = [
                    m
                    for m in metrics
                    if q in (m.get("display_name") or "").lower()
                    or q in (m.get("name") or "").lower()
                ]

            total = len(metrics)
            try:
                page = max(int(page) if page else 1, 1)
                page_size = min(max(int(page_size) if page_size else 50, 1), 200)
            except (ValueError, TypeError):
                page = 1
                page_size = 50
            start = (page - 1) * page_size
            end = start + page_size
            page_metrics = metrics[start:end]

            return self._gm.success_response(
                {
                    "metrics": page_metrics,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "has_more": end < total,
                }
            )

        except Exception as e:
            logger.error("fetch_metrics_failed", error=str(e))
            return self._gm.bad_request(
                "Failed to fetch metrics. Please try again later."
            )

    # ------------------------------------------------------------------
    # Legacy metrics endpoints (backward compat)
    # ------------------------------------------------------------------

    def _metrics_observability_legacy(self, request):
        """Return observability metrics in the old grouped format."""
        project_ids_str = request.query_params.get("project_ids", "")
        project_ids = [pid.strip() for pid in project_ids_str.split(",") if pid.strip()]

        if not project_ids:
            project_ids = list(
                Project.objects.filter(
                    workspace=request.workspace,
                ).values_list("id", flat=True)
            )
            project_ids = [str(pid) for pid in project_ids]
        else:
            valid_projects = Project.objects.filter(
                id__in=project_ids,
                workspace=request.workspace,
            )
            if valid_projects.count() != len(project_ids):
                return self._gm.bad_request("Some project_ids are invalid")

        system_metrics = [
            {
                "name": "project",
                "display_name": "Project",
                "type": "string",
                "unit": "",
            },
            {
                "name": "latency",
                "display_name": "Latency",
                "type": "number",
                "unit": "ms",
            },
            {
                "name": "error_rate",
                "display_name": "Error Rate",
                "type": "number",
                "unit": "%",
            },
            {
                "name": "tokens",
                "display_name": "Tokens",
                "type": "number",
                "unit": "tokens",
            },
            {
                "name": "input_tokens",
                "display_name": "Input Tokens",
                "type": "number",
                "unit": "tokens",
            },
            {
                "name": "output_tokens",
                "display_name": "Output Tokens",
                "type": "number",
                "unit": "tokens",
            },
            {
                "name": "time_to_first_token",
                "display_name": "Time to First Token",
                "type": "number",
                "unit": "ms",
            },
            {"name": "cost", "display_name": "Cost", "type": "number", "unit": "$"},
        ]

        eval_metrics = []
        eval_configs = CustomEvalConfig.no_workspace_objects.filter(
            project__in=project_ids
        ).values("id", "name")
        for ec in eval_configs:
            eval_metrics.append(
                {
                    "name": str(ec["id"]),
                    "display_name": ec["name"],
                    "output_type": "SCORE",
                }
            )

        annotation_metrics = []
        try:
            from tracer.models.trace_annotation import AnnotationLabel

            annotation_labels = AnnotationLabel.no_workspace_objects.filter(
                project__in=project_ids
            ).values("id", "name", "label_type")
            for al in annotation_labels:
                annotation_metrics.append(
                    {
                        "name": str(al["id"]),
                        "display_name": al["name"],
                        "output_type": al.get("label_type", "float"),
                    }
                )
        except (ImportError, Exception):
            pass

        # CH-only span attribute key inventory. PG fallback removed
        # post-migration — the attrs_* typed-Map indexes on CH are the
        # authoritative source of which keys exist for a project.
        custom_attributes = []
        # Attribute inventory is served by CH25/V2, whose configuration is
        # independent from the legacy ClickHouse feature gate.
        analytics = AnalyticsQueryService()
        for pid in project_ids:
            try:
                keys = analytics.get_span_attribute_keys_ch(pid)
            except Exception as exc:
                logger.warning(
                    "dashboard_span_attribute_discovery_failed",
                    project_id=pid,
                    error_type=type(exc).__name__,
                )
                keys = []
            for key in keys:
                key_name = key.get("key") if isinstance(key, dict) else key
                key_type = (
                    key.get("type", "string") if isinstance(key, dict) else "string"
                )
                attr = {
                    "name": key_name,
                    "display_name": key_name,
                    "type": key_type,
                }
                if attr not in custom_attributes:
                    custom_attributes.append(attr)

        return self._gm.success_response(
            {
                "system_metrics": system_metrics,
                "eval_metrics": eval_metrics,
                "annotation_metrics": annotation_metrics,
                "custom_attributes": custom_attributes,
            }
        )

    def _metrics_dataset_legacy(self, request):
        """Return dataset metrics in the old grouped format."""
        try:
            workspace = request.workspace

            system_metrics = [
                {
                    "name": "row_count",
                    "display_name": "Row Count",
                    "type": "number",
                    "unit": "",
                },
                {
                    "name": "prompt_tokens",
                    "display_name": "Prompt Tokens",
                    "type": "number",
                    "unit": "tokens",
                },
                {
                    "name": "completion_tokens",
                    "display_name": "Completion Tokens",
                    "type": "number",
                    "unit": "tokens",
                },
                {
                    "name": "total_tokens",
                    "display_name": "Total Tokens",
                    "type": "number",
                    "unit": "tokens",
                },
                {
                    "name": "response_time",
                    "display_name": "Response Time",
                    "type": "number",
                    "unit": "ms",
                },
                {
                    "name": "cell_error_rate",
                    "display_name": "Cell Error Rate",
                    "type": "number",
                    "unit": "%",
                },
            ]

            eval_metrics = []
            try:
                from model_hub.models.evals_metric import UserEvalMetric

                user_eval_metrics = (
                    UserEvalMetric.no_workspace_objects.filter(
                        dataset__workspace=workspace,
                    )
                    .select_related("template")
                    .values("template__id", "template__name", "template__config")
                    .distinct()
                )
                seen_templates = set()
                for uem in user_eval_metrics:
                    tid = str(uem["template__id"])
                    if tid in seen_templates:
                        continue
                    seen_templates.add(tid)
                    config = uem["template__config"] or {}
                    output_type = "SCORE"
                    if isinstance(config, dict):
                        ot = config.get("output_type", "").upper()
                        if ot in ("PASS_FAIL", "CHOICE", "SCORE"):
                            output_type = ot
                    eval_metrics.append(
                        {
                            "name": tid,
                            "display_name": uem["template__name"],
                            "output_type": output_type,
                        }
                    )
            except (ImportError, Exception) as e:
                logger.warning(f"Failed to load eval metrics for dataset: {e}")

            annotation_metrics = []
            try:
                from model_hub.models.develop_annotations import AnnotationsLabels

                labels = AnnotationsLabels.no_workspace_objects.filter(
                    workspace=workspace,
                ).values("id", "name", "type")
                for label in labels:
                    annotation_metrics.append(
                        {
                            "name": str(label["id"]),
                            "display_name": label["name"],
                            "output_type": label.get("type", "numeric"),
                        }
                    )
            except (ImportError, Exception):
                pass

            custom_columns = []
            try:
                from model_hub.models.develop_dataset import Column

                cols = (
                    Column.no_workspace_objects.filter(
                        dataset__workspace=workspace,
                        dataset__deleted=False,
                        data_type__in=["float", "integer", "boolean"],
                    )
                    .values("id", "name", "data_type")
                    .distinct()
                )
                seen_names = set()
                for col in cols:
                    if col["name"] in seen_names:
                        continue
                    seen_names.add(col["name"])
                    custom_columns.append(
                        {
                            "name": str(col["id"]),
                            "display_name": col["name"],
                            "type": (
                                "number" if col["data_type"] != "boolean" else "boolean"
                            ),
                            "data_type": col["data_type"],
                        }
                    )
            except (ImportError, Exception):
                pass

            return self._gm.success_response(
                {
                    "system_metrics": system_metrics,
                    "eval_metrics": eval_metrics,
                    "annotation_metrics": annotation_metrics,
                    "custom_columns": custom_columns,
                }
            )
        except Exception as e:
            logger.error("fetch_dataset_metrics_failed", error=str(e))
            return self._gm.bad_request(
                "Failed to fetch dataset metrics. Please try again later."
            )

    # ------------------------------------------------------------------
    # Filter values — unified with source-based routing
    # ------------------------------------------------------------------

    # Fixed lookback for all value scans — `spans` is partitioned by
    # toDate(start_time), so this is what prunes. Unbounded scans read up to
    # 70 GiB on the largest tenant and timed out on 23% of calls.
    # Settings-overridable so ops can shrink it without a deploy.
    FILTER_VALUES_DEFAULT_LOOKBACK_DAYS = 7

    @validated_request(
        query_serializer=DashboardFilterValuesQuerySerializer,
        responses={
            200: DashboardFilterValuesResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def filter_values(self, request):
        """Return distinct values for a given metric/attribute, for filter value picker."""
        query_params = request.validated_query_data
        metric_name = query_params["metric_name"]
        metric_type = query_params["metric_type"]
        source = query_params["source"]
        raw_project_ids = query_params.get("project_ids", [])
        search = query_params.get("search", "").strip()

        # Route by source
        if source == "datasets":
            return self._filter_values_dataset(request, metric_name, metric_type)
        if source == "dataset_column":
            # Per-column suggestions for the dataset detail filter panel.
            # `metric_name` carries the column_id (UUID) in this flow so the
            # frontend can reuse the same hook wiring as traces/datasets.
            return self._filter_values_dataset_column(
                request,
                dataset_id=str(query_params.get("dataset_id") or ""),
                column_id=metric_name,
            )
        if source == "simulation":
            return self._filter_values_simulation(request, metric_name, metric_type)

        # One wall owns every PG/CH phase, including project authorization and
        # post-query label hydration. Starting only inside the system selector
        # let a slow metadata read consume the five-second interaction contract
        # before the bounded ClickHouse statement even began.
        filter_value_deadline = ReadDeadline.start(
            _FILTER_VALUES_INTERACTIVE_TIMEOUT_MS
        )

        # Traces source (default). A fixed explicit scope keeps the existing
        # cursor contract. Workspace and large explicit scopes resolve only one
        # authorized selector-sized batch per physical request.
        try:
            project_scope = _prepare_filter_value_project_scope(
                request,
                raw_project_ids,
                deadline=filter_value_deadline,
                cursor_token=query_params.get("cursor"),
            )
        except ReadDeadlineExceeded:
            logger.warning("filter_values_project_scope_deadline_exceeded")
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Filter values are temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        project_ids = list(project_scope.project_ids)

        try:
            if metric_type == "annotation_metric" and metric_name == "annotator":
                from accounts.models.user import User
                from tracer.services.annotation_label_source import (
                    AnnotationLabelScoresProjectPG,
                )

                # Annotation Scores remain authoritative in PostgreSQL.  Pin
                # this read to their denormalized tracer project key: the
                # legacy CDC score table and direct-write CH25 spans are not
                # co-located and cannot be joined safely after cutover.
                page_size = query_params.get("page_size")
                cursor_token = query_params.get("cursor")
                source_reader = AnnotationLabelScoresProjectPG()
                if not project_ids and not project_scope.batched:
                    if page_size is None:
                        return self._gm.success_response({"values": []})
                    return self._gm.success_response(
                        {
                            "values": [],
                            "query_complete": True,
                            "query_status": "complete",
                            "has_more": False,
                            "browse_status": "exhausted",
                            "next_cursor": None,
                        }
                    )
                if not project_ids and page_size is None:
                    return self._gm.success_response(
                        _legacy_filter_value_scope_metadata(
                            {"values": []},
                            project_scope,
                        )
                    )
                if page_size is not None:
                    page_size = int(page_size)
                    if project_scope.batched:
                        batch_lane = "annotation_annotators"
                        batched_cursor = _batched_filter_value_cursor(
                            request,
                            project_scope,
                            deadline=filter_value_deadline,
                            cursor_token=cursor_token,
                            page_size=page_size,
                            lane=batch_lane,
                            query={
                                "metric_name": metric_name,
                                "metric_type": metric_type,
                                "source": source,
                                "search": search,
                            },
                        )
                        project_scope = batched_cursor.scope
                        project_ids = list(project_scope.project_ids)
                        cursor_state = batched_cursor.cursor_state
                        window_start = (
                            cursor_state.window_start
                            if cursor_state is not None
                            else _FILTER_VALUE_RETAINED_START
                        )
                        window_end = (
                            cursor_state.window_end
                            if cursor_state is not None
                            else datetime.now(UTC)
                        )
                        if not project_ids:
                            return self._gm.success_response(
                                _empty_batched_filter_value_payload(
                                    batched_cursor,
                                    page_size=page_size,
                                    lane=batch_lane,
                                    window_start=window_start,
                                    window_end=window_end,
                                )
                            )
                        physical_order = batched_cursor.physical_order
                        if batched_cursor.new_project_batch:
                            annotator_after = None
                        elif len(physical_order) != 1 or not isinstance(
                            physical_order[0], str
                        ):
                            raise ListCursorError(
                                "invalid_cursor",
                                "The continuation cursor is invalid.",
                            )
                        else:
                            try:
                                annotator_after = (
                                    UUID(physical_order[0])
                                    if physical_order[0]
                                    else None
                                )
                            except (TypeError, ValueError) as exc:
                                raise ListCursorError(
                                    "invalid_cursor",
                                    "The continuation cursor is invalid.",
                                ) from exc
                        seen_state, state_binding = (
                            _load_batched_filter_value_seen_state(
                                batched_cursor,
                                page_size=page_size,
                                window_start=window_start,
                                window_end=window_end,
                            )
                        )
                        physical_users, physical_has_more = _run_filter_value_pg_read(
                            filter_value_deadline,
                            lambda: source_reader.annotator_page_for_projects(
                                project_ids,
                                page_size=page_size,
                                search=search,
                                after_id=annotator_after,
                            ),
                        )
                        users = [
                            user
                            for user in physical_users
                            if not seen_state.contains(
                                _filter_value_digest(str(user["id"]))
                            )
                        ]
                        appended_digests = tuple(
                            _filter_value_digest(str(user["id"])) for user in users
                        )
                        has_more, browse_status, next_cursor = (
                            _encode_batched_filter_value_cursor(
                                batched_cursor,
                                page_size=page_size,
                                window_start=window_start,
                                window_end=window_end,
                                seen_state=seen_state,
                                state_binding=state_binding,
                                appended_digests=appended_digests,
                                lane=batch_lane,
                                physical_order=(
                                    str(physical_users[-1]["id"])
                                    if physical_has_more and physical_users
                                    else str(annotator_after or ""),
                                ),
                                physical_has_more=physical_has_more,
                            )
                        )
                    else:
                        cursor_scope = cursor_scope_for_request(
                            request,
                            project_ids=project_ids,
                        )
                        cursor_query = {
                            "metric_name": metric_name,
                            "metric_type": metric_type,
                            "source": source,
                            "project_ids": sorted(str(value) for value in project_ids),
                            "search": search,
                        }
                        cursor_resource = "dashboard_annotation_annotators"
                        if cursor_token:
                            cursor_state = decode_list_cursor(
                                cursor_token,
                                resource=cursor_resource,
                                scope=cursor_scope,
                                query=cursor_query,
                                page_size=page_size,
                            )
                            if len(cursor_state.order) != 1 or not isinstance(
                                cursor_state.order[0], str
                            ):
                                raise ListCursorError(
                                    "invalid_cursor",
                                    "The continuation cursor is invalid.",
                                )
                            try:
                                annotator_after = UUID(cursor_state.order[0])
                            except (TypeError, ValueError) as exc:
                                raise ListCursorError(
                                    "invalid_cursor",
                                    "The continuation cursor is invalid.",
                                ) from exc
                            window_start = cursor_state.window_start
                            window_end = cursor_state.window_end
                            seen_rows = cursor_state.seen_rows
                        else:
                            annotator_after = None
                            window_start = _FILTER_VALUE_RETAINED_START
                            window_end = datetime.now(UTC)
                            seen_rows = 0

                        users, has_more = _run_filter_value_pg_read(
                            filter_value_deadline,
                            lambda: source_reader.annotator_page_for_projects(
                                project_ids,
                                page_size=page_size,
                                search=search,
                                after_id=annotator_after,
                            ),
                        )
                else:
                    annotator_ids = _run_filter_value_pg_read(
                        filter_value_deadline,
                        lambda: source_reader.annotator_ids_for_projects(project_ids),
                    )
                    users = _run_filter_value_pg_read(
                        filter_value_deadline,
                        lambda: list(
                            User.objects.filter(id__in=annotator_ids)
                            .values("id", "name", "email")
                            .order_by("name", "email")
                        ),
                    )
                    has_more = False

                values = []
                for u in users:
                    user_id = str(u["id"])
                    name = (u.get("name") or "").strip()
                    email = (u.get("email") or "").strip()
                    label = name or email or user_id
                    option = {"value": user_id, "label": label}
                    if name:
                        option["name"] = name
                    if email:
                        option["email"] = email
                    if name and email and email != name:
                        option["description"] = email
                    values.append(option)
                if page_size is None:
                    return self._gm.success_response(
                        _legacy_filter_value_scope_metadata(
                            {
                                "values": _filter_value_options_for_search(
                                    values, search
                                ),
                            },
                            project_scope,
                        )
                    )

                if project_scope.batched:
                    return self._gm.success_response(
                        {
                            "values": values,
                            "query_complete": True,
                            "query_status": "complete",
                            "has_more": has_more,
                            "browse_status": browse_status,
                            "next_cursor": next_cursor,
                        }
                    )

                next_cursor = None
                if has_more:
                    next_cursor = encode_list_cursor(
                        resource=cursor_resource,
                        scope=cursor_scope,
                        query=cursor_query,
                        page_size=page_size,
                        window_start=window_start,
                        window_end=window_end,
                        order=(str(users[-1]["id"]),),
                        seen_rows=seen_rows + len(users),
                    )
                return self._gm.success_response(
                    {
                        "values": values,
                        "query_complete": True,
                        "query_status": "complete",
                        "has_more": has_more,
                        "browse_status": "continuation" if has_more else "exhausted",
                        "next_cursor": next_cursor,
                    }
                )

            # Filter-value reads are backed exclusively by the direct-write
            # CH25 tables.  Using the legacy service here silently targets the
            # wrong cluster in split deployments even though the SQL names the
            # same ``spans``/``end_users`` tables.
            analytics = V2AnalyticsQueryService()

            if metric_type == "system_metric":
                if not project_ids and not project_scope.batched:
                    return self._gm.success_response(
                        {
                            "values": [],
                            "query_complete": True,
                            "query_status": "complete",
                            "has_more": False,
                            "browse_status": "exhausted",
                            "next_cursor": None,
                        }
                    )
                if not project_ids and query_params.get("page_size") is None:
                    return self._gm.success_response(
                        _legacy_filter_value_scope_metadata(
                            {"values": []},
                            project_scope,
                        )
                    )
                enduser_string_cols = {
                    "user": "user_id",
                    "user_id": "user_id",
                    "user_id_type": "user_id_type",
                }

                def system_value_options(raw_values):
                    if metric_name == "session":
                        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
                            resolve_session_fields,
                        )

                        session_fields = resolve_session_fields(
                            raw_values,
                            project_ids=project_ids,
                            deadline=filter_value_deadline,
                        )
                        options = []
                        for value in raw_values:
                            fields = session_fields.get(value, {})
                            display_name = fields.get("display_name")
                            external_id = fields.get("external_session_id")
                            option = {
                                "value": value,
                                "label": str(display_name or external_id or value),
                            }
                            if display_name and external_id:
                                option["description"] = str(external_id)
                            options.append(option)
                        return options
                    if metric_name == "project":
                        name_map = dict(
                            _run_filter_value_pg_read(
                                filter_value_deadline,
                                lambda: list(
                                    Project.objects.filter(
                                        id__in=project_ids,
                                        workspace=request.workspace,
                                    ).values_list("id", "name")
                                ),
                            )
                        )
                        normalized_names = {
                            str(key): value for key, value in name_map.items()
                        }
                        return [
                            {
                                "value": value,
                                "label": normalized_names.get(value, value),
                            }
                            for value in raw_values
                        ]
                    return [{"value": value, "label": value} for value in raw_values]

                label_backed_system_metric = metric_name in {"project", "session"}

                def search_hydrated_system_options(options):
                    return (
                        _filter_value_options_for_search(options, search)
                        if label_backed_system_metric
                        else options
                    )

                # Session/project labels are hydrated after their raw ids are
                # read. Searching the ids in CH would make a displayed-name
                # match permanently unreachable.
                storage_search = "" if label_backed_system_metric else search

                page_size = query_params.get("page_size")
                cursor_token = query_params.get("cursor")
                if page_size is not None:
                    page_size = int(page_size)
                    if project_scope.batched:
                        batch_query = {
                            "metric_name": metric_name,
                            "metric_type": metric_type,
                            "source": source,
                            "search": search,
                        }
                        batch_lane = (
                            "end_user"
                            if metric_name in enduser_string_cols
                            else "span_system"
                        )
                        batched_cursor = _batched_filter_value_cursor(
                            request,
                            project_scope,
                            deadline=filter_value_deadline,
                            cursor_token=cursor_token,
                            page_size=page_size,
                            lane=batch_lane,
                            query=batch_query,
                        )
                        project_scope = batched_cursor.scope
                        project_ids = list(project_scope.project_ids)
                        cursor_state = batched_cursor.cursor_state
                        window_start = (
                            cursor_state.window_start
                            if cursor_state is not None
                            else _FILTER_VALUE_RETAINED_START
                        )
                        window_end = (
                            cursor_state.window_end
                            if cursor_state is not None
                            else datetime.now(UTC)
                        )
                        if not project_ids:
                            return self._gm.success_response(
                                _empty_batched_filter_value_payload(
                                    batched_cursor,
                                    page_size=page_size,
                                    lane=batch_lane,
                                    window_start=window_start,
                                    window_end=window_end,
                                )
                            )
                        seen_state, state_binding = (
                            _load_batched_filter_value_seen_state(
                                batched_cursor,
                                page_size=page_size,
                                window_start=window_start,
                                window_end=window_end,
                            )
                        )

                        if metric_name in enduser_string_cols:
                            physical_order = batched_cursor.physical_order
                            if batched_cursor.new_project_batch:
                                value_after = None
                            elif len(physical_order) != 1 or not isinstance(
                                physical_order[0], str
                            ):
                                raise ListCursorError(
                                    "invalid_cursor",
                                    "The continuation cursor is invalid.",
                                )
                            else:
                                value_after = physical_order[0] or None
                            page_read = read_end_user_filter_value_cursor_page(
                                analytics,
                                project_ids=project_ids,
                                source_column=enduser_string_cols[metric_name],
                                page_size=page_size,
                                search=search,
                                value_after=value_after,
                                deadline=filter_value_deadline,
                            )
                            values = tuple(
                                value
                                for value in page_read.values
                                if not seen_state.contains(_filter_value_digest(value))
                            )
                            appended_digests = tuple(
                                _filter_value_digest(value) for value in values
                            )
                            has_more, browse_status, next_cursor = (
                                _encode_batched_filter_value_cursor(
                                    batched_cursor,
                                    page_size=page_size,
                                    window_start=window_start,
                                    window_end=window_end,
                                    seen_state=seen_state,
                                    state_binding=state_binding,
                                    appended_digests=appended_digests,
                                    lane=batch_lane,
                                    physical_order=(page_read.next_value_after or "",),
                                    physical_has_more=page_read.has_more,
                                )
                            )
                            return self._gm.success_response(
                                {
                                    "values": search_hydrated_system_options(
                                        system_value_options(values)
                                    ),
                                    "query_complete": True,
                                    "query_status": "complete",
                                    "has_more": has_more,
                                    "browse_status": browse_status,
                                    "next_cursor": next_cursor,
                                }
                            )

                        if metric_name not in SYSTEM_FILTER_VALUE_METRICS:
                            return self._gm.success_response(
                                {
                                    "values": [],
                                    "query_complete": True,
                                    "query_status": "complete",
                                    "has_more": False,
                                    "browse_status": "exhausted",
                                    "next_cursor": None,
                                }
                            )

                        physical_order = batched_cursor.physical_order
                        if batched_cursor.new_project_batch:
                            segment_end = window_end
                            segment_start = None
                            value_after = None
                        elif (
                            len(physical_order) != 3
                            or not isinstance(physical_order[0], datetime)
                            or (
                                physical_order[1] is not None
                                and not isinstance(physical_order[1], datetime)
                            )
                            or not isinstance(physical_order[2], str)
                        ):
                            raise ListCursorError(
                                "invalid_cursor",
                                "The continuation cursor is invalid.",
                            )
                        else:
                            segment_end = physical_order[0]
                            segment_start = physical_order[1]
                            value_after = physical_order[2] or None
                        page_read = read_span_system_filter_value_cursor_page(
                            analytics,
                            project_ids=project_ids,
                            metric_name=metric_name,
                            page_size=page_size,
                            window_start=window_start,
                            window_end=window_end,
                            search=storage_search,
                            segment_end=segment_end,
                            segment_start=segment_start,
                            value_after=value_after,
                            seen_value_digests=seen_state.digests,
                            seen_value_contains=seen_state.contains,
                            seen_value_count=seen_state.seen_count,
                            deadline=filter_value_deadline,
                        )
                        appended_digests = (
                            page_read.appended_value_digests
                            or page_read.seen_value_digests[len(seen_state.digests) :]
                        )
                        has_more, browse_status, next_cursor = (
                            _encode_batched_filter_value_cursor(
                                batched_cursor,
                                page_size=page_size,
                                window_start=window_start,
                                window_end=window_end,
                                seen_state=seen_state,
                                state_binding=state_binding,
                                appended_digests=appended_digests,
                                lane=batch_lane,
                                physical_order=(
                                    page_read.next_segment_end,
                                    page_read.next_segment_start,
                                    page_read.next_value_after or "",
                                ),
                                physical_has_more=page_read.has_more,
                            )
                        )
                        return self._gm.success_response(
                            {
                                "values": search_hydrated_system_options(
                                    system_value_options(page_read.values)
                                ),
                                **page_read.metadata(),
                                "has_more": has_more,
                                "browse_status": browse_status,
                                "next_cursor": next_cursor,
                            }
                        )

                    cursor_scope = cursor_scope_for_request(
                        request,
                        project_ids=project_ids,
                    )
                    cursor_query = {
                        "metric_name": metric_name,
                        "metric_type": metric_type,
                        "source": source,
                        "project_ids": sorted(str(value) for value in project_ids),
                        "search": search,
                    }
                    cursor_resource = "dashboard_system_filter_values"
                    if metric_name in enduser_string_cols:
                        if cursor_token:
                            cursor_state = decode_list_cursor(
                                cursor_token,
                                resource=cursor_resource,
                                scope=cursor_scope,
                                query=cursor_query,
                                page_size=page_size,
                            )
                            if (
                                len(cursor_state.order) != 1
                                or not isinstance(cursor_state.order[0], str)
                                or not cursor_state.order[0]
                            ):
                                raise ListCursorError(
                                    "invalid_cursor",
                                    "The continuation cursor is invalid.",
                                )
                            value_after = cursor_state.order[0]
                            window_start = cursor_state.window_start
                            window_end = cursor_state.window_end
                        else:
                            value_after = None
                            window_start = datetime(1970, 1, 1, tzinfo=UTC)
                            window_end = datetime.now(UTC)
                        page_read = read_end_user_filter_value_cursor_page(
                            analytics,
                            project_ids=project_ids,
                            source_column=enduser_string_cols[metric_name],
                            page_size=page_size,
                            search=search,
                            value_after=value_after,
                            deadline=filter_value_deadline,
                        )
                        next_cursor = None
                        if page_read.has_more:
                            next_cursor = encode_list_cursor(
                                resource=cursor_resource,
                                scope=cursor_scope,
                                query=cursor_query,
                                page_size=page_size,
                                window_start=window_start,
                                window_end=window_end,
                                order=(page_read.next_value_after,),
                                seen_rows=0,
                            )
                        return self._gm.success_response(
                            {
                                "values": search_hydrated_system_options(
                                    system_value_options(page_read.values)
                                ),
                                "query_complete": True,
                                "query_status": "complete",
                                "has_more": page_read.has_more,
                                "browse_status": page_read.browse_status,
                                "next_cursor": next_cursor,
                            }
                        )

                    if metric_name not in SYSTEM_FILTER_VALUE_METRICS:
                        return self._gm.success_response(
                            {
                                "values": [],
                                "query_complete": True,
                                "query_status": "complete",
                                "has_more": False,
                                "browse_status": "exhausted",
                                "next_cursor": None,
                            }
                        )

                    selector = AttributeReadSelector(
                        typed_only=True,
                        json_attribute_mode="arrays",
                        wall_timeout_ms=filter_value_deadline.remaining_ms(
                            ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS
                        ),
                    )
                    if cursor_token:
                        cursor_state = decode_list_cursor(
                            cursor_token,
                            resource=cursor_resource,
                            scope=cursor_scope,
                            query=cursor_query,
                            page_size=page_size,
                        )
                        if (
                            len(cursor_state.order) != 4
                            or not isinstance(cursor_state.order[0], datetime)
                            or not isinstance(cursor_state.order[1], datetime)
                            or not isinstance(cursor_state.order[2], str)
                            or not isinstance(cursor_state.order[3], tuple)
                        ):
                            raise ListCursorError(
                                "invalid_cursor",
                                "The continuation cursor is invalid.",
                            )
                        segment_end = cursor_state.order[0]
                        segment_start = cursor_state.order[1]
                        value_after = cursor_state.order[2] or None
                        seen_reference = cursor_state.order[3]
                        window_start = cursor_state.window_start
                        window_end = cursor_state.window_end
                    else:
                        window_end = datetime.now(UTC)
                        retained_start = selector.retained_window_start(
                            project_ids,
                            window_end=window_end,
                        )
                        window_start = retained_attribute_window_start(
                            retained_start,
                            window_end=window_end,
                        )
                        segment_end = window_end
                        segment_start = None
                        value_after = None
                        seen_reference = ()

                    state_binding = {
                        "scope": cursor_scope,
                        "query": cursor_query,
                        "page_size": page_size,
                        "window_start": window_start,
                        "window_end": window_end,
                    }
                    seen_state = load_attribute_cursor_seen_state(
                        seen_reference,
                        resource=cursor_resource,
                        binding=state_binding,
                        validate_digest=lambda value: (
                            len(value) == 32
                            and all(char in "0123456789abcdef" for char in value)
                        ),
                    )
                    if cursor_token and (
                        cursor_state.seen_rows != seen_state.seen_count
                    ):
                        raise ListCursorError(
                            "invalid_cursor",
                            "The continuation cursor is invalid.",
                        )
                    page_read = read_span_system_filter_value_cursor_page(
                        analytics,
                        project_ids=project_ids,
                        metric_name=metric_name,
                        page_size=page_size,
                        window_start=window_start,
                        window_end=window_end,
                        search=storage_search,
                        segment_end=segment_end,
                        segment_start=segment_start,
                        value_after=value_after,
                        seen_value_digests=seen_state.digests,
                        seen_value_contains=seen_state.contains,
                        seen_value_count=seen_state.seen_count,
                        deadline=filter_value_deadline,
                    )
                    next_cursor = None
                    if page_read.has_more:
                        appended_digests = (
                            page_read.appended_value_digests
                            or (page_read.seen_value_digests[len(seen_state.digests) :])
                        )
                        seen_reference = persist_attribute_cursor_seen_state(
                            seen_state,
                            appended_digests,
                            resource=cursor_resource,
                            binding=state_binding,
                            validate_digest=lambda value: (
                                len(value) == 32
                                and all(char in "0123456789abcdef" for char in value)
                            ),
                        )
                        next_cursor = encode_list_cursor(
                            resource=cursor_resource,
                            scope=cursor_scope,
                            query=cursor_query,
                            page_size=page_size,
                            window_start=window_start,
                            window_end=window_end,
                            order=(
                                page_read.next_segment_end,
                                page_read.next_segment_start,
                                page_read.next_value_after or "",
                                seen_reference,
                            ),
                            seen_rows=(seen_state.seen_count + len(appended_digests)),
                        )
                    return self._gm.success_response(
                        {
                            "values": search_hydrated_system_options(
                                system_value_options(page_read.values)
                            ),
                            **page_read.metadata(),
                            "has_more": page_read.has_more,
                            "browse_status": page_read.browse_status,
                            "next_cursor": next_cursor,
                        }
                    )

                if metric_name in enduser_string_cols:
                    enduser_col = enduser_string_cols[metric_name]
                    try:
                        sql = (
                            f"SELECT DISTINCT {enduser_col} AS val "
                            f"FROM end_users FINAL "
                            f"WHERE project_id IN %(project_ids)s "
                            f"AND is_deleted = 0 "
                            f"AND {enduser_col} IS NOT NULL "
                            f"AND {enduser_col} != '' "
                            f"ORDER BY val "
                            f"LIMIT 500"
                        )
                        result = analytics.execute_ch_query(
                            sql,
                            {"project_ids": project_ids},
                            timeout_ms=filter_value_deadline.remaining_ms(
                                _FILTER_VALUES_INTERACTIVE_TIMEOUT_MS
                            ),
                        )
                        values = [
                            {"value": row["val"], "label": row["val"]}
                            for row in result.data
                        ]
                    except Exception as exc:
                        if is_clickhouse_api_read_unavailable_error(exc):
                            logger.warning(
                                "filter_values_ch_query_unavailable",
                                metric_name=metric_name,
                                error_type=type(exc).__name__,
                            )
                            return self._gm.custom_error_response(
                                status.HTTP_503_SERVICE_UNAVAILABLE,
                                "Filter values are temporarily unavailable. Please retry.",
                                code="service_unavailable",
                            )
                        logger.exception(
                            "filter_values_programming_error",
                            metric_name=metric_name,
                            error_type=type(exc).__name__,
                        )
                        return self._gm.custom_error_response(
                            status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "Filter values could not be loaded",
                            code="server_error",
                        )
                    return self._gm.success_response(
                        _legacy_filter_value_scope_metadata(
                            {"values": values},
                            project_scope,
                        )
                    )

                if metric_name not in SYSTEM_FILTER_VALUE_METRICS:
                    return self._gm.success_response({"values": []})

                try:
                    value_read = read_span_system_filter_values(
                        analytics,
                        project_ids=project_ids,
                        metric_name=metric_name,
                        # The legacy response has no continuation contract, so
                        # it cannot exhaustively walk raw ids and then search
                        # hydrated Project/Session labels. Preserve its bounded
                        # raw-value search instead of filtering only the first
                        # 20 unrelated ids. Cursor callers above own displayed-
                        # label search.
                        search=search,
                        limit=20 if search else 500,
                        lookback_days=int(
                            getattr(
                                settings,
                                "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS",
                                self.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS,
                            )
                        ),
                        deadline=filter_value_deadline,
                    )
                    values = list(value_read.values)
                except Exception as exc:
                    if is_clickhouse_api_read_unavailable_error(exc):
                        logger.warning(
                            "filter_values_ch_query_unavailable",
                            metric_name=metric_name,
                            error_type=type(exc).__name__,
                        )
                        return self._gm.custom_error_response(
                            status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Filter values are temporarily unavailable. Please retry.",
                            code="service_unavailable",
                        )
                    logger.exception(
                        "filter_values_programming_error",
                        metric_name=metric_name,
                        error_type=type(exc).__name__,
                    )
                    return self._gm.custom_error_response(
                        status.HTTP_500_INTERNAL_SERVER_ERROR,
                        "Filter values could not be loaded",
                        code="server_error",
                    )

                values = system_value_options(values)
                return self._gm.success_response(
                    _legacy_filter_value_scope_metadata(
                        {"values": values, **value_read.metadata()},
                        project_scope,
                    )
                )

            elif metric_type == "eval_metric":
                # Observe exposes CustomEvalConfig ids while older dashboard
                # widgets can still carry EvalTemplate ids. Resolve either id
                # through a config attached to the already-authorized project
                # set; a guessed config/template UUID from another tenant or
                # project must not reveal its output definition or choices.
                from django.core.exceptions import ValidationError
                from django.db.models import Q

                page_size = query_params.get("page_size")
                cursor_token = query_params.get("cursor")
                finite_query = {
                    "metric_name": metric_name,
                    "metric_type": metric_type,
                    "source": source,
                    **(
                        {"project_scope": project_scope.cursor_identity()}
                        if project_scope.batched
                        else {
                            "project_ids": sorted(str(value) for value in project_ids)
                        }
                    ),
                }
                finite_cursor_project_ids = [] if project_scope.batched else project_ids
                batched_eval_cursor = None
                batched_eval_lane = "configured_eval_template"
                configured_window_start = _FILTER_VALUE_RETAINED_START
                configured_window_end = datetime.now(UTC)

                try:

                    def eval_config_queryset():
                        return CustomEvalConfig.no_workspace_objects.filter(
                            project_workspace_scope_q(request),
                            project__deleted=False,
                            eval_template__deleted=False,
                        )

                    def read_eval_config():
                        # Resolve the one requested config/template through its
                        # project relation. This remains a constant-cardinality
                        # indexed lookup even when the logical project scope is
                        # a workspace with thousands of projects.
                        config = (
                            eval_config_queryset()
                            .filter(id=metric_name)
                            .select_related("eval_template")
                            .first()
                        )
                        if config is None:
                            return None
                        if (
                            project_scope.mode != "workspace"
                            and str(config.project_id)
                            not in project_scope.requested_project_ids
                        ):
                            return None
                        return config

                    eval_config = _run_filter_value_pg_read(
                        filter_value_deadline,
                        read_eval_config,
                    )
                    if eval_config is None and project_scope.mode == "workspace":
                        eval_config = _run_filter_value_pg_read(
                            filter_value_deadline,
                            lambda: (
                                eval_config_queryset()
                                .filter(eval_template_id=metric_name)
                                .select_related("eval_template")
                                .first()
                            ),
                        )
                    elif eval_config is None and project_scope.mode == "fixed":
                        eval_config = _run_filter_value_pg_read(
                            filter_value_deadline,
                            lambda: (
                                eval_config_queryset()
                                .filter(
                                    eval_template_id=metric_name,
                                    project_id__in=project_ids,
                                )
                                .select_related("eval_template")
                                .first()
                            ),
                        )
                    elif eval_config is None:
                        batched_eval_cursor = _batched_filter_value_cursor(
                            request,
                            project_scope,
                            deadline=filter_value_deadline,
                            cursor_token=cursor_token,
                            page_size=int(page_size or 1),
                            lane=batched_eval_lane,
                            query={
                                "metric_name": metric_name,
                                "metric_type": metric_type,
                                "source": source,
                                "search": search,
                            },
                        )
                        project_scope = batched_eval_cursor.scope
                        project_ids = list(project_scope.project_ids)
                        cursor_state = batched_eval_cursor.cursor_state
                        configured_window_start = (
                            cursor_state.window_start
                            if cursor_state is not None
                            else _FILTER_VALUE_RETAINED_START
                        )
                        configured_window_end = (
                            cursor_state.window_end
                            if cursor_state is not None
                            else configured_window_end
                        )
                        if project_ids:
                            eval_config = _run_filter_value_pg_read(
                                filter_value_deadline,
                                lambda: (
                                    eval_config_queryset()
                                    .filter(
                                        eval_template_id=metric_name,
                                        project_id__in=project_ids,
                                    )
                                    .select_related("eval_template")
                                    .first()
                                ),
                            )
                except (TypeError, ValueError, ValidationError):
                    eval_config = None

                if eval_config is None:
                    if batched_eval_cursor is not None:
                        if page_size is None:
                            return self._gm.success_response(
                                _legacy_filter_value_scope_metadata(
                                    {"values": []},
                                    project_scope,
                                )
                            )
                        return self._gm.success_response(
                            _empty_batched_filter_value_payload(
                                batched_eval_cursor,
                                page_size=int(page_size),
                                lane=batched_eval_lane,
                                window_start=configured_window_start,
                                window_end=configured_window_end,
                            )
                        )
                    if page_size is None:
                        return self._gm.success_response({"values": []})
                    return self._gm.success_response(
                        _finite_filter_value_cursor_page(
                            request,
                            project_ids=finite_cursor_project_ids,
                            query=finite_query,
                            values=[],
                            search=search,
                            page_size=int(page_size),
                            cursor_token=cursor_token,
                        )
                    )

                eval_template = eval_config.eval_template
                template_config = eval_template.config or {}
                output_type = "SCORE"
                if isinstance(template_config, dict):
                    normalized_output = (
                        (template_config.get("output") or "")
                        .upper()
                        .replace("/", "_")
                        .replace(" ", "_")
                    )
                    if normalized_output in {
                        "PASS_FAIL",
                        "CHOICE",
                        "CHOICES",
                        "SCORE",
                    }:
                        output_type = normalized_output

                if output_type == "PASS_FAIL":
                    values = [
                        {"value": "Passed", "label": "Passed"},
                        {"value": "Failed", "label": "Failed"},
                    ]
                elif output_type in {"CHOICE", "CHOICES"}:
                    values = []
                    seen_values = set()
                    for choice in eval_template.choices or []:
                        _append_configured_filter_value_option(
                            values,
                            seen_values,
                            choice,
                        )
                else:
                    # Score evals use numeric entry rather than a misleading
                    # categorical vocabulary.
                    values = []

                if page_size is not None:
                    if batched_eval_cursor is not None:
                        return self._gm.success_response(
                            _batched_configured_filter_value_page(
                                batched_eval_cursor,
                                page_size=int(page_size),
                                lane=batched_eval_lane,
                                window_start=configured_window_start,
                                window_end=configured_window_end,
                                values=values,
                                search=search,
                            )
                        )
                    return self._gm.success_response(
                        _finite_filter_value_cursor_page(
                            request,
                            project_ids=finite_cursor_project_ids,
                            query=finite_query,
                            values=values,
                            search=search,
                            page_size=int(page_size),
                            cursor_token=cursor_token,
                        )
                    )
                values = _filter_value_options_for_search(values, search)

            elif metric_type == "annotation_metric":
                # Annotation filter values are derived from the label
                # definition (settings) and, for categorical annotations, from
                # stored scores. Older imported/backfilled labels can have
                # real choices in Score.value without settings.options; relying
                # only on settings makes the value dropdown empty even though
                # the annotation metric itself is available.
                from django.core.exceptions import ValidationError
                from django.db.models import Q

                from model_hub.models.develop_annotations import AnnotationsLabels

                page_size = query_params.get("page_size")
                cursor_token = query_params.get("cursor")
                finite_query = {
                    "metric_name": metric_name,
                    "metric_type": metric_type,
                    "source": source,
                    **(
                        {"project_scope": project_scope.cursor_identity()}
                        if project_scope.batched
                        else {
                            "project_ids": sorted(str(value) for value in project_ids)
                        }
                    ),
                }
                finite_cursor_project_ids = [] if project_scope.batched else project_ids

                try:
                    request_organization = getattr(request, "organization", None)
                    if request_organization is None:
                        request_organization = _run_filter_value_pg_read(
                            filter_value_deadline,
                            lambda: request.workspace.organization,
                        )

                    def read_annotation_label():
                        label_queryset = AnnotationsLabels.no_workspace_objects.filter(
                            pk=metric_name,
                            organization=request_organization,
                            deleted=False,
                        ).filter(
                            Q(workspace=request.workspace) | Q(workspace__isnull=True)
                        )
                        label_queryset = label_queryset.filter(
                            Q(project__isnull=True)
                            | (
                                Q(project__deleted=False)
                                & project_workspace_scope_q(request)
                            )
                        )
                        label = label_queryset.first()
                        if (
                            label is not None
                            and label.project_id is not None
                            and project_scope.mode != "workspace"
                            and str(label.project_id)
                            not in project_scope.requested_project_ids
                        ):
                            return None
                        return label

                    label = _run_filter_value_pg_read(
                        filter_value_deadline,
                        read_annotation_label,
                    )
                except (TypeError, ValueError, ValidationError):
                    label = None
                if label is None:
                    if page_size is None:
                        return self._gm.success_response({"values": []})
                    return self._gm.success_response(
                        _finite_filter_value_cursor_page(
                            request,
                            project_ids=finite_cursor_project_ids,
                            query=finite_query,
                            values=[],
                            search=search,
                            page_size=int(page_size),
                            cursor_token=cursor_token,
                        )
                    )

                label_type = label.type
                label_settings = label.settings or {}

                if label_type == "categorical":
                    values = []
                    configured_seen_values = set()
                    for opt in label_settings.get("options", []):
                        _append_configured_filter_value_option(
                            values,
                            configured_seen_values,
                            opt,
                        )

                    # The stored-Score vocabulary is a changing bounded sample,
                    # not a snapshot. Never ordinal-page it: a Score write
                    # between requests could otherwise move values around the
                    # offset and cause a silent skip/repeat. Cursor callers get
                    # every stable configured option plus explicit metadata that
                    # historical stored-only discovery remains incomplete.
                    if page_size is not None:
                        return self._gm.success_response(
                            _finite_filter_value_cursor_page(
                                request,
                                project_ids=finite_cursor_project_ids,
                                query=finite_query,
                                values=values,
                                search=search,
                                page_size=int(page_size),
                                cursor_token=cursor_token,
                                query_complete=False,
                            )
                        )

                    # Stored categorical choices are read from authoritative
                    # Score rows via tracer_project_id.  This avoids a cross-
                    # cluster legacy-score/direct-span subquery.
                    import json

                    from tracer.services.annotation_label_source import (
                        AnnotationLabelScoresProjectPG,
                    )

                    seen_stored_values = {str(option["value"]) for option in values}

                    def add_stored_value_option(raw_value):
                        if raw_value in (None, ""):
                            return
                        value = str(raw_value)
                        if not value or value in seen_stored_values:
                            return
                        seen_stored_values.add(value)
                        values.append({"value": value, "label": value})

                    stored_values = _run_filter_value_pg_read(
                        filter_value_deadline,
                        lambda: (
                            AnnotationLabelScoresProjectPG().categorical_values_for_label(
                                label.id, project_ids
                            )
                        ),
                    )
                    for payload_value in stored_values:
                        try:
                            payload = json.loads(payload_value)
                        except (TypeError, ValueError):
                            payload = payload_value
                        raw_values = []
                        if isinstance(payload, dict):
                            selected = payload.get("selected")
                            if isinstance(selected, list):
                                raw_values.extend(selected)
                            elif selected not in (None, ""):
                                raw_values.append(selected)
                            for key in ("value", "label", "text"):
                                val = payload.get(key)
                                if val not in (None, ""):
                                    raw_values.append(val)
                        elif isinstance(payload, list):
                            raw_values.extend(payload)
                        elif payload not in (None, ""):
                            raw_values.append(payload)
                        for raw_value in raw_values:
                            add_stored_value_option(raw_value)
                elif label_type == "star":
                    no_of_stars = label_settings.get("no_of_stars", 5)
                    values = [
                        {"value": str(i), "label": f"{i} star{'s' if i != 1 else ''}"}
                        for i in range(1, no_of_stars + 1)
                    ]
                elif label_type == "thumbs_up_down":
                    values = [
                        {"value": "thumbs_up", "label": "Thumbs Up"},
                        {"value": "thumbs_down", "label": "Thumbs Down"},
                    ]
                else:
                    # text / numeric — no predefined values
                    values = []

                if page_size is not None:
                    return self._gm.success_response(
                        _finite_filter_value_cursor_page(
                            request,
                            project_ids=finite_cursor_project_ids,
                            query=finite_query,
                            values=values,
                            search=search,
                            page_size=int(page_size),
                            cursor_token=cursor_token,
                        )
                    )
                values = _filter_value_options_for_search(values, search)
                if label_type == "categorical":
                    return self._gm.success_response(
                        _legacy_filter_value_scope_metadata(
                            {"values": values},
                            project_scope,
                        )
                    )

            elif metric_type == "custom_attribute":
                # metric_name is an exact key request. It must not depend on
                # the bounded browse inventory, where any rare key can be
                # outside the sample.
                try:
                    page_size = query_params.get("page_size")
                    cursor_token = query_params.get("cursor")
                    attribute_type = query_params.get("attribute_type")
                    if not project_ids and not project_scope.batched:
                        return self._gm.success_response(
                            {
                                "values": [],
                                "query_complete": True,
                                "query_status": "complete",
                                "has_more": False,
                                "browse_status": "exhausted",
                                "next_cursor": None,
                                **(
                                    {"attribute_type": attribute_type}
                                    if attribute_type
                                    else {}
                                ),
                            }
                        )
                    if not project_ids and page_size is None:
                        return self._gm.success_response(
                            _legacy_filter_value_scope_metadata(
                                {"values": []},
                                project_scope,
                            )
                        )
                    if page_size is not None:
                        page_size = int(page_size)
                        if project_scope.batched:
                            batch_lane = "custom_attribute"
                            batched_cursor = _batched_filter_value_cursor(
                                request,
                                project_scope,
                                deadline=filter_value_deadline,
                                cursor_token=cursor_token,
                                page_size=page_size,
                                lane=batch_lane,
                                query={
                                    "metric_name": metric_name,
                                    "metric_type": metric_type,
                                    "source": source,
                                    "search": search,
                                    "attribute_type": attribute_type,
                                },
                            )
                            project_scope = batched_cursor.scope
                            project_ids = list(project_scope.project_ids)
                            cursor_state = batched_cursor.cursor_state
                            window_start = (
                                cursor_state.window_start
                                if cursor_state is not None
                                else _FILTER_VALUE_RETAINED_START
                            )
                            window_end = (
                                cursor_state.window_end
                                if cursor_state is not None
                                else datetime.now(UTC)
                            )
                            if not project_ids:
                                return self._gm.success_response(
                                    _empty_batched_filter_value_payload(
                                        batched_cursor,
                                        page_size=page_size,
                                        lane=batch_lane,
                                        window_start=window_start,
                                        window_end=window_end,
                                        extra=(
                                            {"attribute_type": attribute_type}
                                            if attribute_type
                                            else None
                                        ),
                                    )
                                )
                            physical_order = batched_cursor.physical_order
                            if batched_cursor.new_project_batch:
                                segment_end = window_end
                                before_identity = None
                                resume_identity = None
                                resume_member_offset = 0
                                segment_start = None
                            elif len(physical_order) != 5:
                                raise ListCursorError(
                                    "invalid_cursor",
                                    "The continuation cursor is invalid.",
                                )
                            else:
                                (
                                    segment_end,
                                    raw_before_identity,
                                    raw_resume_identity,
                                    resume_member_offset,
                                    segment_start,
                                ) = physical_order
                                if (
                                    not isinstance(segment_end, datetime)
                                    or not isinstance(raw_before_identity, tuple)
                                    or len(raw_before_identity) not in {0, 4}
                                    or not isinstance(raw_resume_identity, tuple)
                                    or len(raw_resume_identity) not in {0, 4}
                                    or (raw_before_identity and raw_resume_identity)
                                    or not isinstance(resume_member_offset, int)
                                    or resume_member_offset < 0
                                    or (
                                        segment_start is not None
                                        and not isinstance(segment_start, datetime)
                                    )
                                ):
                                    raise ListCursorError(
                                        "invalid_cursor",
                                        "The continuation cursor is invalid.",
                                    )
                                before_identity = raw_before_identity or None
                                resume_identity = raw_resume_identity or None
                                for identity in (
                                    before_identity,
                                    resume_identity,
                                ):
                                    if identity is not None and (
                                        not all(
                                            isinstance(value, str)
                                            for value in identity[:3]
                                        )
                                        or not isinstance(identity[3], datetime)
                                    ):
                                        raise ListCursorError(
                                            "invalid_cursor",
                                            "The continuation cursor is invalid.",
                                        )

                            seen_state, state_binding = (
                                _load_batched_filter_value_seen_state(
                                    batched_cursor,
                                    page_size=page_size,
                                    window_start=window_start,
                                    window_end=window_end,
                                )
                            )
                            selector = AttributeReadSelector(
                                typed_only=True,
                                json_attribute_mode="arrays",
                                wall_timeout_ms=filter_value_deadline.remaining_ms(
                                    ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS
                                ),
                            )
                            page_read = selector.read_value_cursor_page(
                                project_ids,
                                metric_name,
                                page_size=page_size,
                                window_start=window_start,
                                window_end=window_end,
                                segment_end=segment_end,
                                segment_start=segment_start,
                                before_identity=before_identity,
                                resume_identity=resume_identity,
                                resume_member_offset=resume_member_offset,
                                seen_value_digests=seen_state.digests,
                                seen_value_contains=seen_state.contains,
                                seen_value_count=seen_state.seen_count,
                                search=search,
                                attribute_type=attribute_type,
                            )
                            if not page_read.metadata.query_complete:
                                logger.warning(
                                    "filter_value_cursor_incomplete",
                                    metric_name=metric_name,
                                    error_code=(page_read.metadata.query_error_code),
                                )
                                return self._gm.custom_error_response(
                                    status.HTTP_503_SERVICE_UNAVAILABLE,
                                    "Filter values are temporarily unavailable. Please retry.",
                                    code="service_unavailable",
                                )
                            appended_digests = (
                                page_read.appended_value_digests
                                or page_read.seen_value_digests[
                                    len(seen_state.digests) :
                                ]
                            )
                            has_more, browse_status, next_cursor = (
                                _encode_batched_filter_value_cursor(
                                    batched_cursor,
                                    page_size=page_size,
                                    window_start=window_start,
                                    window_end=window_end,
                                    seen_state=seen_state,
                                    state_binding=state_binding,
                                    appended_digests=appended_digests,
                                    lane=batch_lane,
                                    physical_order=(
                                        page_read.next_segment_end,
                                        page_read.next_before_identity or (),
                                        page_read.next_resume_identity or (),
                                        page_read.next_resume_member_offset,
                                        page_read.next_segment_start,
                                    ),
                                    physical_has_more=page_read.has_more,
                                )
                            )
                            values = [
                                {
                                    "value": row.value,
                                    "type": row.type,
                                    "label": (
                                        "true"
                                        if row.value is True
                                        else "false"
                                        if row.value is False
                                        else str(row.value)
                                    ),
                                }
                                for row in page_read.rows
                            ]
                            return self._gm.success_response(
                                {
                                    "values": values,
                                    **page_read.metadata.public_payload(),
                                    "has_more": has_more,
                                    "browse_status": browse_status,
                                    "next_cursor": next_cursor,
                                    **(
                                        {"attribute_type": attribute_type}
                                        if attribute_type
                                        else {}
                                    ),
                                }
                            )

                        cursor_scope = cursor_scope_for_request(
                            request,
                            project_ids=project_ids,
                        )
                        cursor_query = {
                            "metric_name": metric_name,
                            "metric_type": metric_type,
                            "source": source,
                            "project_ids": sorted(str(value) for value in project_ids),
                            "search": search,
                            "attribute_type": attribute_type,
                        }
                        selector = None
                        if cursor_token:
                            cursor_state = decode_list_cursor(
                                cursor_token,
                                resource="dashboard_filter_values",
                                scope=cursor_scope,
                                query=cursor_query,
                                page_size=page_size,
                            )
                            if len(cursor_state.order) != 5:
                                raise ListCursorError(
                                    "invalid_cursor",
                                    "The continuation cursor is invalid.",
                                )
                            (
                                segment_end,
                                raw_before_identity,
                                raw_resume_identity,
                                resume_member_offset,
                                seen_reference,
                            ) = cursor_state.order
                            if (
                                not isinstance(segment_end, datetime)
                                or not isinstance(raw_before_identity, tuple)
                                or len(raw_before_identity) not in {0, 4}
                                or not isinstance(raw_resume_identity, tuple)
                                or len(raw_resume_identity) not in {0, 4}
                                or (raw_before_identity and raw_resume_identity)
                                or not isinstance(resume_member_offset, int)
                                or resume_member_offset < 0
                            ):
                                raise ListCursorError(
                                    "invalid_cursor",
                                    "The continuation cursor is invalid.",
                                )
                            before_identity = None
                            if raw_before_identity:
                                if not all(
                                    isinstance(value, str)
                                    for value in raw_before_identity[:3]
                                ) or not isinstance(raw_before_identity[3], datetime):
                                    raise ListCursorError(
                                        "invalid_cursor",
                                        "The continuation cursor is invalid.",
                                    )
                                before_identity = raw_before_identity
                            resume_identity = None
                            if raw_resume_identity:
                                if not all(
                                    isinstance(value, str)
                                    for value in raw_resume_identity[:3]
                                ) or not isinstance(raw_resume_identity[3], datetime):
                                    raise ListCursorError(
                                        "invalid_cursor",
                                        "The continuation cursor is invalid.",
                                    )
                                resume_identity = raw_resume_identity
                            window_start = cursor_state.window_start
                            window_end = cursor_state.window_end
                            segment_start = cursor_state.scan_slice_start
                            scan_slice_end = cursor_state.scan_slice_end
                            if (
                                (segment_start is None) != (scan_slice_end is None)
                                or scan_slice_end is not None
                                and scan_slice_end != segment_end
                            ):
                                raise ListCursorError(
                                    "invalid_cursor",
                                    "The continuation cursor is invalid.",
                                )
                        else:
                            selector = AttributeReadSelector(
                                typed_only=True,
                                json_attribute_mode="arrays",
                                wall_timeout_ms=filter_value_deadline.remaining_ms(
                                    ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS
                                ),
                            )
                            window_end = datetime.now(UTC)
                            retained_start = selector.retained_window_start(
                                project_ids,
                                window_end=window_end,
                            )
                            window_start = retained_attribute_window_start(
                                retained_start,
                                window_end=window_end,
                            )
                            segment_end = window_end
                            segment_start = None
                            before_identity = None
                            resume_identity = None
                            resume_member_offset = 0
                            seen_reference = ()

                        state_binding = {
                            "scope": cursor_scope,
                            "query": cursor_query,
                            "page_size": page_size,
                            "window_start": window_start,
                            "window_end": window_end,
                        }
                        seen_state = load_attribute_cursor_seen_state(
                            seen_reference,
                            resource="dashboard_filter_values",
                            binding=state_binding,
                            validate_digest=lambda value: (
                                len(value) == 32
                                and all(char in "0123456789abcdef" for char in value)
                            ),
                        )
                        if cursor_token and (
                            cursor_state.seen_rows != seen_state.seen_count
                        ):
                            raise ListCursorError(
                                "invalid_cursor",
                                "The continuation cursor is invalid.",
                            )

                        # Cursor decode and server-held seen-state lookup are
                        # part of the same public four-second wall.  A resumed
                        # page must capture the *remaining* duration only after
                        # those phases finish; otherwise read_value_cursor_page
                        # would start the earlier duration anew and outlive the
                        # interaction contract.
                        if selector is None:
                            selector = AttributeReadSelector(
                                typed_only=True,
                                json_attribute_mode="arrays",
                                wall_timeout_ms=filter_value_deadline.remaining_ms(
                                    ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS
                                ),
                            )
                        page_read = selector.read_value_cursor_page(
                            project_ids,
                            metric_name,
                            page_size=page_size,
                            window_start=window_start,
                            window_end=window_end,
                            segment_end=segment_end,
                            segment_start=segment_start,
                            before_identity=before_identity,
                            resume_identity=resume_identity,
                            resume_member_offset=resume_member_offset,
                            seen_value_digests=seen_state.digests,
                            seen_value_contains=seen_state.contains,
                            seen_value_count=seen_state.seen_count,
                            search=search,
                            attribute_type=attribute_type,
                            continue_operation=not bool(cursor_token),
                        )
                        if not page_read.metadata.query_complete:
                            logger.warning(
                                "filter_value_cursor_incomplete",
                                metric_name=metric_name,
                                error_code=page_read.metadata.query_error_code,
                            )
                            return self._gm.custom_error_response(
                                status.HTTP_503_SERVICE_UNAVAILABLE,
                                "Filter values are temporarily unavailable. Please retry.",
                                code="service_unavailable",
                            )
                        values = [
                            {
                                "value": row.value,
                                "type": row.type,
                                "label": (
                                    "true"
                                    if row.value is True
                                    else "false"
                                    if row.value is False
                                    else str(row.value)
                                ),
                            }
                            for row in page_read.rows
                        ]
                        next_cursor = None
                        if page_read.has_more:
                            appended_digests = (
                                page_read.appended_value_digests
                                or (
                                    page_read.seen_value_digests[
                                        len(seen_state.digests) :
                                    ]
                                )
                            )
                            seen_reference = persist_attribute_cursor_seen_state(
                                seen_state,
                                appended_digests,
                                resource="dashboard_filter_values",
                                binding=state_binding,
                                validate_digest=lambda value: (
                                    len(value) == 32
                                    and all(
                                        char in "0123456789abcdef" for char in value
                                    )
                                ),
                            )
                            next_cursor = encode_list_cursor(
                                resource="dashboard_filter_values",
                                scope=cursor_scope,
                                query=cursor_query,
                                page_size=page_size,
                                window_start=window_start,
                                window_end=window_end,
                                order=(
                                    page_read.next_segment_end,
                                    page_read.next_before_identity or (),
                                    page_read.next_resume_identity or (),
                                    page_read.next_resume_member_offset,
                                    seen_reference,
                                ),
                                seen_rows=(
                                    seen_state.seen_count + len(appended_digests)
                                ),
                                scan_slice_start=page_read.next_segment_start,
                                scan_slice_end=(
                                    page_read.next_segment_end
                                    if page_read.next_segment_start is not None
                                    else None
                                ),
                            )
                        return self._gm.success_response(
                            {
                                "values": values,
                                **page_read.metadata.public_payload(),
                                "has_more": page_read.has_more,
                                "browse_status": page_read.browse_status,
                                "next_cursor": next_cursor,
                                **(
                                    {"attribute_type": attribute_type}
                                    if attribute_type
                                    else {}
                                ),
                            }
                        )

                    selector = AttributeReadSelector(
                        typed_only=True,
                        json_attribute_mode="arrays",
                        wall_timeout_ms=filter_value_deadline.remaining_ms(
                            ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS
                        ),
                    )
                    read = selector.read_values(
                        project_ids,
                        metric_name,
                        search=search,
                        max_values=20 if search else 500,
                    )
                    values = [
                        {
                            "value": row.value,
                            "type": row.type,
                            "label": (
                                "true"
                                if row.value is True
                                else "false"
                                if row.value is False
                                else str(row.value)
                            ),
                        }
                        for row in read.rows
                    ]
                    metadata = read.metadata.public_payload()
                    if not read.metadata.query_complete:
                        if read.metadata.query_error_code == "sample_limit":
                            # The bounded selector completed its finite sample,
                            # but cannot claim a complete distribution (or
                            # global absence). Publish both non-empty and empty
                            # samples with explicit coverage metadata. Every
                            # resource/timeout/partial replay remains a
                            # retryable error instead of an empty 200 response.
                            metadata["query_status"] = "sampled"
                        else:
                            logger.warning(
                                "filter_values_custom_attribute_incomplete",
                                metric_name=metric_name,
                                error_code=read.metadata.query_error_code,
                            )
                            return self._gm.custom_error_response(
                                status.HTTP_503_SERVICE_UNAVAILABLE,
                                "Filter values are temporarily unavailable. Please retry.",
                                code="service_unavailable",
                            )
                    return self._gm.success_response(
                        _legacy_filter_value_scope_metadata(
                            {
                                "values": values,
                                **metadata,
                            },
                            project_scope,
                        )
                    )
                except AttributeCursorStateError as exc:
                    if exc.code == "cursor_state_unavailable":
                        return self._gm.custom_error_response(
                            status.HTTP_503_SERVICE_UNAVAILABLE,
                            str(exc),
                            code="service_unavailable",
                        )
                    return self._gm.custom_error_response(
                        status.HTTP_400_BAD_REQUEST,
                        str(exc),
                        code=exc.code,
                    )
                except ListCursorError as exc:
                    return self._gm.custom_error_response(
                        status.HTTP_400_BAD_REQUEST,
                        str(exc),
                        code=exc.code,
                    )
                except InvalidAttributeKey:
                    return self._gm.bad_request("Invalid attribute key")
                except Exception as exc:
                    if is_attribute_api_read_unavailable_error(exc):
                        logger.warning(
                            "filter_values_ch_query_unavailable",
                            metric_name=metric_name,
                            error_type=type(exc).__name__,
                        )
                        return self._gm.custom_error_response(
                            status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Filter values are temporarily unavailable. Please retry.",
                            code="service_unavailable",
                        )
                    logger.exception(
                        "filter_values_programming_error",
                        metric_name=metric_name,
                        error_type=type(exc).__name__,
                    )
                    return self._gm.custom_error_response(
                        status.HTTP_500_INTERNAL_SERVER_ERROR,
                        "Filter values could not be loaded",
                        code="server_error",
                    )
            else:
                values = []

            return self._gm.success_response({"values": values})
        except AnnotationScoreReadUnavailable:
            logger.warning("fetch_annotation_filter_values_unavailable")
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Filter values are temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except AttributeCursorStateError as exc:
            if exc.code == "cursor_state_unavailable":
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    str(exc),
                    code="service_unavailable",
                )
            return self._gm.custom_error_response(
                status.HTTP_400_BAD_REQUEST,
                str(exc),
                code=exc.code,
            )
        except ListCursorError as exc:
            return self._gm.custom_error_response(
                status.HTTP_400_BAD_REQUEST,
                str(exc),
                code=exc.code,
            )
        except Exception as exc:
            if is_clickhouse_api_read_unavailable_error(exc):
                logger.warning(
                    "fetch_filter_values_unavailable",
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Filter values are temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "fetch_filter_values_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Filter values could not be loaded",
                code="server_error",
            )

    def _filter_values_dataset(self, request, metric_name, metric_type):
        """Return distinct filter values for dataset source."""
        try:
            if not is_clickhouse_enabled():
                return self._gm.success_response({"values": []})

            analytics = AnalyticsQueryService()
            workspace_id = str(request.workspace.id)

            if metric_type == "system_metric":
                col_expr = DATASET_FILTER_COLUMNS.get(metric_name)
                if not col_expr:
                    return self._gm.success_response({"values": []})

                if metric_name == "dataset":
                    sql = (
                        "SELECT DISTINCT name AS val "
                        "FROM model_hub_dataset FINAL "
                        "WHERE _peerdb_is_deleted = 0 "
                        "AND deleted = 0 "
                        "AND workspace_id = toUUID(%(workspace_id)s) "
                        "AND name != '' "
                        "ORDER BY val "
                        "LIMIT 500"
                    )
                else:
                    sql = (
                        f"SELECT DISTINCT {col_expr} AS val "
                        f"FROM model_hub_cell AS c FINAL "
                        f"WHERE c._peerdb_is_deleted = 0 "
                        f"AND c.dataset_id IN ("
                        f"SELECT id FROM model_hub_dataset FINAL "
                        f"WHERE _peerdb_is_deleted = 0 "
                        f"AND deleted = 0 "
                        f"AND workspace_id = toUUID(%(workspace_id)s)"
                        f") "
                        f"AND {col_expr} != '' "
                        f"ORDER BY val "
                        f"LIMIT 500"
                    )

                result = analytics.execute_ch_query(
                    sql, {"workspace_id": workspace_id}, timeout_ms=5000
                )
                values = [
                    {"value": row["val"], "label": row["val"]} for row in result.data
                ]
            else:
                values = []

            return self._gm.success_response({"values": values})
        except Exception as e:
            logger.error("fetch_dataset_filter_values_failed", error=str(e))
            return self._gm.bad_request(
                "Failed to fetch filter values. Please try again later."
            )

    def _filter_values_dataset_column(self, request, dataset_id, column_id):
        """Return distinct non-empty cell values for a single (dataset, column).

        Powers the dataset detail filter panel's value dropdown and the
        dataset AI-filter smart-mode value grounding. For `array` / `json`
        columns we parse each cell's JSON and emit the individual elements
        (leaf strings for dicts) so the suggestion set is element-level
        rather than raw serialized blobs.
        """
        import json
        import uuid as _uuid

        from model_hub.models.develop_dataset import Column

        # --- Input validation --------------------------------------------
        if not dataset_id or not column_id:
            return self._gm.bad_request(
                "dataset_id and metric_name (column_id) are required"
            )
        try:
            _uuid.UUID(str(dataset_id))
            _uuid.UUID(str(column_id))
        except ValueError:
            return self._gm.bad_request("dataset_id / column_id must be UUIDs")

        # --- Ownership check via PG (cheap, definitive) ------------------
        try:
            column = Column.objects.select_related("dataset").get(
                id=column_id,
                dataset_id=dataset_id,
                dataset__workspace=request.workspace,
                deleted=False,
            )
        except Column.DoesNotExist:
            return self._gm.success_response({"values": []})

        if not is_clickhouse_enabled():
            return self._gm.success_response({"values": []})

        analytics = AnalyticsQueryService()
        try:
            sql = (
                "SELECT DISTINCT value AS val "
                "FROM model_hub_cell FINAL "
                "WHERE _peerdb_is_deleted = 0 "
                "AND dataset_id = toUUID(%(dataset_id)s) "
                "AND column_id = toUUID(%(column_id)s) "
                "AND value != '' "
                "ORDER BY val "
                "LIMIT 500"
            )
            result = analytics.execute_ch_query(
                sql,
                {"dataset_id": str(dataset_id), "column_id": str(column_id)},
                timeout_ms=5000,
            )
            raw = [row["val"] for row in result.data if row.get("val")]
        except Exception as e:
            logger.warning(
                "dataset_column_filter_values_query_failed",
                dataset_id=str(dataset_id),
                column_id=str(column_id),
                error=str(e)[:200],
            )
            return self._gm.success_response({"values": []})

        # Flatten list / dict cells to their elements so the dropdown
        # suggests "English" instead of '["English","French"]'. Fall back
        # to the raw serialized string when parse fails or the structure
        # has nothing enumerable.
        def _expand(serialized):
            if column.data_type not in ("array", "json"):
                return [serialized]
            try:
                parsed = json.loads(serialized)
            except (ValueError, TypeError):
                return [serialized]
            if isinstance(parsed, list):
                out = []
                for elem in parsed:
                    if isinstance(elem, (str, int, float, bool)):
                        s = str(elem).strip()
                        if s:
                            out.append(s)
                    elif isinstance(elem, dict):
                        for v in elem.values():
                            if isinstance(v, (str, int, float)):
                                s = str(v).strip()
                                if s:
                                    out.append(s)
                return out or [serialized]
            if isinstance(parsed, dict):
                out = []
                for v in parsed.values():
                    if isinstance(v, (str, int, float)):
                        s = str(v).strip()
                        if s:
                            out.append(s)
                return out or [serialized]
            return [serialized]

        seen = set()
        values = []
        for raw_val in raw:
            for v in _expand(raw_val):
                if v not in seen:
                    seen.add(v)
                    values.append(v)
                if len(values) >= 500:
                    break
            if len(values) >= 500:
                break
        values.sort(key=lambda s: s.lower())
        return self._gm.success_response(
            {"values": [{"value": v, "label": v} for v in values]}
        )

    def _filter_values_simulation(self, request, metric_name, metric_type):
        """Return distinct filter values for simulation source."""
        try:
            if not is_clickhouse_enabled():
                return self._gm.success_response({"values": []})

            analytics = AnalyticsQueryService()
            workspace_id = str(request.workspace.id)

            if metric_type == "system_metric":
                col_expr = SIMULATION_FILTER_COLUMNS.get(metric_name)
                if not col_expr:
                    return self._gm.success_response({"values": []})

                sql = (
                    f"SELECT DISTINCT {col_expr} AS val "
                    f"FROM simulate_call_execution AS c FINAL "
                    f"WHERE c._peerdb_is_deleted = 0 "
                    f"AND c.deleted = 0 "
                    f"AND dictGetOrDefault('simulate_scenario_dict', 'workspace_id', "
                    f"c.scenario_id, NULL) = toUUID(%(workspace_id)s) "
                    f"AND {self._simulation_filter_value_presence_expr(metric_name, col_expr)} "
                    f"ORDER BY val "
                    f"LIMIT 500"
                )
                result = analytics.execute_ch_query(
                    sql, {"workspace_id": workspace_id}, timeout_ms=5000
                )
                values = [
                    {"value": row["val"], "label": row["val"]} for row in result.data
                ]
            else:
                values = []

            return self._gm.success_response({"values": values})
        except Exception as e:
            logger.error("fetch_simulation_filter_values_failed", error=str(e))
            return self._gm.bad_request(
                "Failed to fetch filter values. Please try again later."
            )

    def _simulation_filter_value_presence_expr(self, metric_name, col_expr):
        if metric_name in _STRING_DIMENSION_METRICS:
            return f"{col_expr} IS NOT NULL AND {col_expr} != ''"
        return f"{col_expr} IS NOT NULL"

    @action(detail=False, methods=["get"], url_path="simulation-agents")
    def simulation_agents(self, request):
        """Return simulation agents with their observability project links."""
        from simulate.models.agent_definition import AgentDefinition

        agents = AgentDefinition.objects.filter(
            workspace=request.workspace,
            deleted=False,
        ).select_related(
            "observability_provider",
            "observability_provider__project",
        )

        result = []
        for a in agents:
            obs_project_id = None
            obs_project_name = None
            if hasattr(a, "observability_provider") and a.observability_provider:
                try:
                    project = a.observability_provider.project
                    if project:
                        obs_project_id = str(project.id)
                        obs_project_name = project.name
                except Exception:
                    pass

            result.append(
                {
                    "id": str(a.id),
                    "name": a.agent_name,
                    "agent_type": a.agent_type,
                    "observability_project_id": obs_project_id,
                    "observability_project_name": obs_project_name,
                }
            )

        return self._gm.success_response({"agents": result})


class DashboardWidgetViewSet(BaseModelViewSetMixin, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = DashboardWidgetSerializer

    def get_queryset(self):
        dashboard_id = self.kwargs.get("dashboard_pk") or self.kwargs.get(
            "dashboard_id"
        )
        return DashboardWidget.objects.filter(
            dashboard_id=dashboard_id,
            dashboard__workspace=self.request.workspace,
            dashboard__deleted=False,
        )

    def _get_trace_query_timeout_ms(self, trace_config):
        return DashboardViewSet._get_trace_query_timeout_ms(self, trace_config)

    def _run_simulation_clickhouse_queries(self, ch_client, simulation_config):
        return DashboardViewSet._run_simulation_clickhouse_queries(
            self, ch_client, simulation_config
        )

    def _normalize_metric_sources(self, metrics):
        return DashboardViewSet._normalize_metric_sources(self, metrics)

    def create(self, request, *args, **kwargs):
        try:
            dashboard_id = self.kwargs.get("dashboard_pk") or self.kwargs.get(
                "dashboard_id"
            )
            dashboard = Dashboard.objects.get(
                id=dashboard_id,
                workspace=request.workspace,
            )

            serializer = DashboardWidgetSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            widget = serializer.save(
                dashboard=dashboard,
                created_by=request.user,
            )
            dashboard.updated_by = request.user
            dashboard.save(update_fields=["updated_by", "updated_at"])

            response_serializer = DashboardWidgetSerializer(widget)
            return self._gm.success_response(response_serializer.data)
        except Dashboard.DoesNotExist:
            return self._gm.not_found("Dashboard not found.")
        except Exception as e:
            logger.error(f"Failed to create widget: {e}", exc_info=True)
            return self._gm.bad_request("Failed to create widget.")

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = DashboardWidgetSerializer(
                instance, data=request.data, partial=kwargs.get("partial", False)
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            widget = serializer.save()
            instance.dashboard.updated_by = request.user
            instance.dashboard.save(update_fields=["updated_by", "updated_at"])

            response_serializer = DashboardWidgetSerializer(widget)
            return self._gm.success_response(response_serializer.data)
        except Http404:
            return self._gm.not_found("Widget not found.")
        except Exception as e:
            logger.error(f"Failed to update widget: {e}", exc_info=True)
            return self._gm.bad_request("Failed to update widget.")

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            dashboard = instance.dashboard
            instance.delete()
            dashboard.updated_by = request.user
            dashboard.save(update_fields=["updated_by", "updated_at"])
            return self._gm.success_response("Widget deleted successfully.")
        except Http404:
            return self._gm.not_found("Widget not found.")
        except Exception as e:
            logger.error(f"Failed to delete widget: {e}", exc_info=True)
            return self._gm.bad_request("Failed to delete widget.")

    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request, *args, **kwargs):
        """Batch update widget positions."""
        try:
            dashboard_id = self.kwargs.get("dashboard_pk") or self.kwargs.get(
                "dashboard_id"
            )
            dashboard = Dashboard.objects.get(
                id=dashboard_id, workspace=request.workspace
            )
            order = request.data.get("order", [])
            if not isinstance(order, list):
                return self._gm.bad_request("order must be a list of widget IDs.")

            widgets = DashboardWidget.objects.filter(dashboard=dashboard, deleted=False)
            widget_map = {str(w.id): w for w in widgets}

            updates = []
            update_fields = {"position"}
            for idx, item in enumerate(order):
                # Support both plain IDs and {id, width} objects
                if isinstance(item, dict):
                    widget_id = item.get("id")
                    width = item.get("width")
                else:
                    widget_id = item
                    width = None
                widget = widget_map.get(str(widget_id))
                if widget:
                    widget.position = idx
                    if width is not None:
                        widget.width = max(1, min(12, int(width)))
                        update_fields.add("width")
                    updates.append(widget)

            if updates:
                DashboardWidget.objects.bulk_update(updates, list(update_fields))
                dashboard.updated_by = request.user
                dashboard.save(update_fields=["updated_by", "updated_at"])

            return self._gm.success_response("Widgets reordered.")
        except Dashboard.DoesNotExist:
            return self._gm.not_found("Dashboard not found.")
        except Exception as e:
            logger.error(f"Failed to reorder widgets: {e}", exc_info=True)
            return self._gm.bad_request("Failed to reorder widgets.")

    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate_widget(self, request, *args, **kwargs):
        """Duplicate a widget."""
        try:
            instance = self.get_object()
            new_widget = DashboardWidget.objects.create(
                dashboard=instance.dashboard,
                name=f"{instance.name} (Copy)",
                position=instance.position + 1,
                width=instance.width,
                height=instance.height,
                query_config=instance.query_config,
                chart_config=instance.chart_config,
                created_by=request.user,
            )
            instance.dashboard.updated_by = request.user
            instance.dashboard.save(update_fields=["updated_by", "updated_at"])
            return self._gm.success_response(DashboardWidgetSerializer(new_widget).data)
        except Exception as e:
            logger.error(f"Failed to duplicate widget: {e}", exc_info=True)
            return self._gm.bad_request("Failed to duplicate widget.")

    def _execute_ch_query_config(
        self,
        query_config,
        workspace,
        *,
        refresh=False,
        _exact_worker=False,
        cache_identity_override=None,
        _read_deadline=None,
    ):
        """Execute a query_config against ClickHouse and return formatted results.

        Routes each metric to the appropriate builder based on source.
        """
        read_deadline = _read_deadline or ReadDeadline.start(
            _DASHBOARD_INTERACTIVE_TIMEOUT_MS
        )
        read_query_config = _canonicalize_persisted_dashboard_query_filters_for_read(
            query_config
        )
        frozen_dataset_ids = serializers.empty
        if _exact_worker and cache_identity_override is not None:
            # ``dataset_ids`` is internal cache-identity state, not part of the
            # public query contract. Remove it for strict public-shape
            # validation, then restore it before scope reauthorization.
            read_query_config = dict(read_query_config)
            frozen_dataset_ids = read_query_config.pop("dataset_ids", serializers.empty)
        serializer = DashboardQuerySerializer(data=read_query_config)
        if not serializer.is_valid():
            logger.warning(
                "dashboard_widget_query_config_invalid",
                invalid_fields=sorted(serializer.errors),
            )
            return self._gm.bad_request("Dashboard query configuration is invalid.")
        query_config = _normalize_dashboard_query_filters(serializer.validated_data)
        if frozen_dataset_ids is not serializers.empty:
            query_config["dataset_ids"] = frozen_dataset_ids
        query_config["allow_sampled"] = False

        query_config["metrics"] = self._normalize_metric_sources(
            query_config["metrics"]
        )

        trace_metrics = [
            m
            for m in query_config["metrics"]
            if m.get("source") in ("traces", "both", "all")
        ]
        dataset_metrics = [
            m for m in query_config["metrics"] if m.get("source") == "datasets"
        ]
        simulation_metrics = [
            m for m in query_config["metrics"] if m.get("source") == "simulation"
        ]

        try:
            query_config = _materialize_dashboard_query_scope(
                query_config,
                workspace,
                trace_metrics=trace_metrics,
                dataset_metrics=dataset_metrics,
                expand_empty_scopes=not (
                    _exact_worker and cache_identity_override is not None
                ),
            )
        except DashboardQueryScopeError as exc:
            return self._gm.bad_request(str(exc))

        cache_identity = (
            deepcopy(cache_identity_override)
            if cache_identity_override is not None
            else {
                "workspace_id": str(workspace.id),
                # Exact-worker normalization adds internal canonical filter
                # objects. Keep them out of the public cache key and never let
                # recursive execution mutate the key selected by this request.
                "query_config": deepcopy(query_config),
            }
        )
        if not _exact_worker:
            return self._gm.success_response(
                _read_public_dashboard_query(
                    query_config,
                    cache_identity=cache_identity,
                    refresh=bool(refresh),
                    deadline=read_deadline,
                )
            )

        # One worker invocation owns one wall budget. Every metric statement,
        # including later executor waves and later data sources, receives only
        # the time still left on this same deadline. This prevents N metrics or
        # trace/dataset/simulation sequencing from multiplying the 9.5-second
        # production ceiling while preserving each metric's indivisible
        # full-window SQL.
        # Freeze one concrete wall-clock window before any builder prepares its
        # metric SQL. Preset windows must not drift by microseconds across
        # concurrent source queries or later response formatting.
        window_builder = DatasetQueryBuilder(query_config)
        window_start, window_end = window_builder.parse_time_range()
        query_config = {
            **query_config,
            "time_range": {
                "custom_start": window_start.isoformat(),
                "custom_end": window_end.isoformat(),
            },
        }

        ch_client = None
        legacy_analytics = None
        metric_results = []
        trace_analytics = None
        trace_builder = None
        trace_prepared = ()
        dataset_builder = None
        dataset_prepared = ()
        simulation_builder = None
        simulation_prepared = ()
        if trace_metrics:
            trace_config = {
                **query_config,
                "metrics": trace_metrics,
                # Force raw latest-state spans instead of the independently
                # refreshed attribute rollup for customer-visible exact totals.
                "require_versioned_snapshot": True,
            }
            project_ids = trace_config.get("project_ids", [])
            # Reuse the exact same scope helper as cache-key materialization.
            # Default workspaces deliberately include legacy workspace-null
            # projects; strict ``workspace=`` equality rejected those already
            # authorized IDs before ClickHouse could run.
            valid_count = (
                _project_queryset_for_dashboard_scope(workspace)
                .filter(id__in=project_ids)
                .count()
            )
            if valid_count != len(project_ids):
                return self._gm.bad_request(
                    "Some project_ids are invalid or not in this workspace"
                )
            trace_config["project_ids"] = [str(pid) for pid in project_ids]
            query_config["project_ids"] = trace_config["project_ids"]
            trace_config["organization_id"] = str(workspace.organization_id)
            trace_config["workspace_id"] = str(workspace.id)
            trace_analytics = V2AnalyticsQueryService()
            trace_builder = DashboardQueryBuilderV2(trace_config)
            if project_ids:
                trace_prepared = DashboardViewSet._prepare_metric_queries(trace_builder)
            else:
                metric_results.extend(
                    _complete_empty_metric_results(trace_builder, "traces")
                )

        if dataset_metrics:
            ds_config = {
                **query_config,
                "metrics": dataset_metrics,
                # ``dataset_ids`` is the concrete, PG-authorized scope. A
                # strict ClickHouse workspace predicate would silently drop
                # legacy workspace-null datasets that belong to a default
                # workspace's canonical scope.
                "workspace_id": "",
                "exact_snapshot_dimensions": True,
            }
            dataset_builder = DatasetQueryBuilder(ds_config)
            if query_config.get("dataset_ids"):
                dataset_prepared = DashboardViewSet._prepare_metric_queries(
                    dataset_builder
                )
            else:
                metric_results.extend(
                    _complete_empty_metric_results(dataset_builder, "datasets")
                )

        if simulation_metrics:
            sim_config = {
                **query_config,
                "metrics": simulation_metrics,
                "workspace_id": str(workspace.id),
                "exact_snapshot_dimensions": True,
            }
            simulation_builder = SimulationQueryBuilder(sim_config)
            simulation_prepared = DashboardViewSet._prepare_metric_queries(
                simulation_builder
            )

        read_settings = dict(_DASHBOARD_TRACE_READ_SETTINGS)
        if dataset_prepared or simulation_prepared:
            ch_client = get_clickhouse_client()
            legacy_analytics = AnalyticsQueryService()
            legacy_analytics._ch_client = ch_client

        if trace_prepared:

            def _fetch_trace_rows(sql, params):
                return _fetch_exact_dashboard_rows(
                    analytics=trace_analytics,
                    sql=sql,
                    params=params,
                    timeout_ms=read_deadline.remaining_ms(
                        _DASHBOARD_EXACT_QUERY_TIMEOUT_MS
                    ),
                    settings=read_settings,
                )

            metric_results.extend(
                DashboardViewSet._run_metric_queries(
                    trace_builder,
                    "traces",
                    _fetch_trace_rows,
                    max_workers=_DASHBOARD_TRACE_MAX_CONCURRENT_METRICS,
                    prepared_queries=trace_prepared,
                )
            )

        if dataset_prepared:
            if legacy_analytics is None:
                raise DashboardExactReadError("dataset query executor is unavailable")

            def _fetch_ds_rows(sql, params):
                return _fetch_exact_dashboard_rows(
                    analytics=legacy_analytics,
                    sql=sql,
                    params=params,
                    timeout_ms=read_deadline.remaining_ms(
                        _DASHBOARD_EXACT_QUERY_TIMEOUT_MS
                    ),
                    settings=read_settings,
                )

            metric_results.extend(
                DashboardViewSet._run_metric_queries(
                    dataset_builder,
                    "datasets",
                    _fetch_ds_rows,
                    prepared_queries=dataset_prepared,
                )
            )

        if simulation_prepared:
            if legacy_analytics is None:
                raise DashboardExactReadError(
                    "simulation query executor is unavailable"
                )

            def _fetch_simulation_rows(sql, params):
                return _fetch_exact_dashboard_rows(
                    analytics=legacy_analytics,
                    sql=sql,
                    params=params,
                    timeout_ms=read_deadline.remaining_ms(
                        _DASHBOARD_EXACT_QUERY_TIMEOUT_MS
                    ),
                    settings=read_settings,
                )

            metric_results.extend(
                DashboardViewSet._run_metric_queries(
                    simulation_builder,
                    "simulation",
                    _fetch_simulation_rows,
                    prepared_queries=simulation_prepared,
                )
            )

        if any(
            metric_info.get("query_complete") is not True
            or metric_info.get("query_status") != "complete"
            or metric_info.get("query_sampled") is True
            or bool(metric_info.get("error"))
            for metric_info, _rows in metric_results
        ):
            raise DashboardExactReadError(
                "one or more dashboard metrics did not complete exactly"
            )

        # A statement can finish just inside its server timeout while result
        # collection/coordination crosses the worker wall. Never format or
        # publish that late result as a completed exact snapshot.
        try:
            read_deadline.remaining_ms(floor_ms=1)
        except ReadDeadlineExceeded as exc:
            raise DashboardExactReadError(
                "dashboard exact read deadline exceeded"
            ) from exc

        # Format using DatasetQueryBuilder (compatible format_results)
        formatter_config = {**query_config, "workspace_id": str(workspace.id)}
        formatter = DatasetQueryBuilder(formatter_config)

        if trace_metrics and not dataset_metrics and not simulation_metrics:
            project_ids = query_config.get("project_ids", [])
            project_name_map = dict(
                Project.objects.filter(
                    id__in=project_ids if project_ids else [],
                ).values_list("id", "name")
            )
            project_name_map = {str(k): v for k, v in project_name_map.items()}
            formatted = DashboardQueryBuilderV2(query_config).format_results(
                metric_results, project_name_map=project_name_map
            )
        else:
            formatted = formatter.format_results(metric_results)

        formatted.update(
            {
                "query_complete": True,
                "query_status": "complete",
                "query_sampled": False,
                "query_exact": True,
                "query_provenance": "exact_snapshot",
            }
        )
        for formatted_metric in formatted.get("metrics", []):
            formatted_metric.update(
                {
                    "query_exact": True,
                    "query_provenance": "exact_snapshot",
                }
            )
        # Formatting and ORM-backed display-name hydration are part of this
        # refresh too. A payload returned after the wall expires would still
        # be published atomically by the exact-aggregation activity, so fence
        # it here immediately before handing it to that publisher.
        try:
            read_deadline.remaining_ms(floor_ms=1)
        except ReadDeadlineExceeded as exc:
            raise DashboardExactReadError(
                "dashboard exact read deadline exceeded"
            ) from exc
        return self._gm.success_response(formatted)

    @validated_request(
        request_serializer=DashboardSampleOptInSerializer,
        query_serializer=DashboardRefreshQuerySerializer,
        responses={
            200: DashboardQueryApiResponseSerializer,
            400: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="query")
    def execute_query(self, request, *args, **kwargs):
        """Execute the widget's query_config against ClickHouse and return results."""
        try:
            if not is_clickhouse_enabled():
                return self._gm.bad_request("ClickHouse is not enabled.")

            widget = self.get_object()
            if not widget.query_config or not widget.query_config.get("metrics"):
                return self._gm.bad_request(
                    "Widget has no query configuration or metrics defined."
                )
            query_config = {
                **widget.query_config,
                "allow_sampled": False,
            }

            refresh = request.validated_query_data["refresh"]
            return self._execute_ch_query_config(
                query_config,
                request.workspace,
                refresh=refresh,
            )
        except Exception as exc:
            if is_read_budget_error(exc) or is_clickhouse_query_error(exc):
                logger.warning(
                    "widget_query_read_unavailable",
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Dashboard data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "widget_query_execution_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Dashboard query could not be completed")

    @validated_request(
        request_serializer=DashboardPreviewQuerySerializer,
        query_serializer=DashboardRefreshQuerySerializer,
        responses={
            200: DashboardQueryApiResponseSerializer,
            400: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"], url_path="preview")
    def preview_query(self, request, *args, **kwargs):
        """Execute an ad-hoc query_config without saving, for live preview."""
        try:
            if not is_clickhouse_enabled():
                return self._gm.bad_request("ClickHouse is not enabled.")

            query_config = {
                **request.validated_data["query_config"],
                "allow_sampled": False,
            }

            refresh = request.validated_query_data["refresh"]
            return self._execute_ch_query_config(
                query_config,
                request.workspace,
                refresh=refresh,
            )
        except Exception as exc:
            if is_read_budget_error(exc) or is_clickhouse_query_error(exc):
                logger.warning(
                    "query_preview_read_unavailable",
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Dashboard data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "query_preview_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Dashboard query could not be completed")
