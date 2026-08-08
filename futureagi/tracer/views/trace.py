import concurrent.futures
import csv
import io
import json
import math
import re
import traceback
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

import pandas as pd
import structlog
from django.contrib.postgres.aggregates import ArrayAgg
from django.db import models
from django.db.models import (
    Avg,
    BooleanField,
    Case,
    CharField,
    Count,
    Exists,
    F,
    FloatField,
    IntegerField,
    JSONField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce, JSONObject, Round
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from model_hub.models.score import Score
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.models.custom_eval_config import CustomEvalConfig, EvalOutputType
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.selectors.trace_filter_reads import (
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
from tracer.serializers.trace import (
    TraceAgentGraphQuerySerializer,
    TraceDetailResponseSerializer,
    TraceExportQuerySerializer,
    TraceIndexQuerySerializer,
    TraceListQuerySerializer,
    TraceObserveIndexQuerySerializer,
    TraceObserveListQuerySerializer,
    TraceObserveListResponseSerializer,
    TraceSerializer,
    TraceVoiceCallListQuerySerializer,
    TraceVoiceCallListResponseSerializer,
    UserCodeExampleResponseSerializer,
    UsersQuerySerializer,
    UsersResponseSerializer,
)
from tracer.services.clickhouse.bounded_graph_reads import BoundedGraphReadError
from tracer.services.clickhouse.graph_dispatch import (
    enforce_exact_graph_data_contract,
    fetch_agent_graph_ch,
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
from tracer.services.clickhouse.page_dedup import paginate_deduped
from tracer.services.clickhouse.query_builders.base import NIL_UUID
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.query_builders.user_list import (
    UnsupportedBoundedUserListQuery,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
    is_clickhouse_api_read_unavailable_error,
    is_clickhouse_query_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.v2.query_builders.agent_graph import (
    AgentGraphQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
from tracer.services.clickhouse.v2.span_reader import merge_span_attributes
from tracer.services.clickhouse.v2.span_selectors import (
    flatten_span_attributes_into_entry,
    merge_content_rows,
)
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailNotFound,
    TraceDetailReadUnavailable,
    read_trace_detail,
)
from tracer.services.filter_principal_context import (
    FilterPrincipalContextError,
    bind_request_my_annotations_principal,
)
from tracer.services.observability_providers import ObservabilityService
from tracer.services.users_list_manager import UsersListManager
from tracer.utils.annotations import (
    build_annotation_subqueries as _build_annotation_subqueries_impl,
)
from tracer.utils.filters import FilterEngine
from tracer.utils.helper import (
    eval_output_type_for_config,
    flatten_eval_score_into_entry,
    get_annotation_labels_by_project,
    get_annotation_labels_for_project,
    get_default_trace_config,
    get_project_eval_configs,
    select_eval_score,
    update_column_config_based_on_eval_config,
    update_span_column_config_based_on_annotations,
)
from tracer.utils.otel import CallAttributes, ConversationAttributes
from tracer.views.observation_span import get_observation_spans

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiErrorResponseSerializer,
    500: ApiErrorResponseSerializer,
}

# The outer infrastructure ceiling is 30 s.  Keep one exact request below it
# while admitting the serializer's maximum 500-row page without increasing CH
# concurrency: five 100-trace content chunks + five attribute chunks + one
# packed eval replay + one annotation span-map replay + the two-phase optional
# user replay consume at most fourteen 900 ms worker slots.  At two workers the
# modeled enrichment ceiling is seven waves (6.3 s); after the bounded 8 s
# candidate phase that is 14.3 s.  The extra 1.7 s covers response assembly and
# scheduler jitter.  Healthy reads still return immediately.
TRACE_LIST_WALL_DEADLINE_MS = 16_000
# The candidate reader remains a single bounded pass. Production qualification
# showed a complete heavy-tenant proof just beyond the former 2.5 s ceiling, so
# allow it up to 8 s while retaining the separately modeled enrichment budget.
# This is only a ceiling: healthy reads return immediately and issue no
# additional query.
TRACE_LIST_CANDIDATE_DEADLINE_MS = 8_000
TRACE_LIST_ENRICHMENT_TIMEOUT_MS = 900
# Page-local content/attribute hydration is exact but can still make ClickHouse
# read a wide part when a caller requests the serializer's 500-row maximum.
# High-volume qualification showed 100 identities remain below the locked
# 512 MiB per-query read ceiling while a single 500-identity replay can cross it
# on a continuation page. Split only the finite public page; every chunk is
# still required and the response fails closed if any chunk is unavailable.
TRACE_LIST_ENRICHMENT_CHUNK_SIZE = 100
# Enrichments are page-local and individually bounded, but fanning every
# optional field out at once can exceed ClickHouse's admission limit on a busy
# tenant. Two workers retain overlap without turning one list request into a
# five-query concurrency spike.
TRACE_LIST_ENRICHMENT_MAX_WORKERS = 2
# The annotation relation is exact, but PostgreSQL score discovery must not
# materialize an unbounded project history into one request. 50k scored span
# identities is intentionally above the former 5,001-row regression while
# still placing a hard ceiling on Python memory and the subsequent CH IN set.
# Pages above this bound fail closed (503); they are never silently truncated.
TRACE_LIST_ANNOTATION_SCORE_SPAN_LIMIT = 50_000
TRACE_LIST_READ_SETTINGS = {
    "max_threads": 1,
    "max_block_size": 8192,
    "max_memory_usage": 256 * 1024 * 1024,
    "max_bytes_to_read": 512 * 1024 * 1024,
    "max_result_rows": 5_001,
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}
TRACE_NAVIGATION_CANDIDATE_LIMIT = 4_095
TRACE_NAVIGATION_SCAN_PAGE_SIZE = 200
TRACE_NAVIGATION_MAX_QUERIES = 128
TRACE_NAVIGATION_WALL_DEADLINE_MS = 20_000
_CLICKHOUSE_ERROR_CODE_RE = re.compile(r"\bcode:\s*(\d+)\b", re.IGNORECASE)
_OPTIONAL_USER_ENRICHMENT_ERROR_CODES = frozenset({497})


class AnnotationScoreReadBoundExceeded(ReadDeadlineExceeded):
    """Exact annotation hydration cannot fit its bounded read contract."""


def _clickhouse_error_code(exc: Exception) -> int | None:
    """Extract only the numeric CH code; never expose the server message."""

    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    match = _CLICKHOUSE_ERROR_CODE_RE.search(str(exc))
    return int(match.group(1)) if match else None


def _is_optional_user_enrichment_failure(exc: Exception) -> bool:
    """Failures that may omit a label without invalidating the trace page.

    Query compiler/programming errors deliberately do not qualify.  Code 497
    covers the historical read-only privilege failure that affected this
    optional presentation field; bounded-resource and transport failures use
    the shared narrow classifiers.
    """

    return (
        is_read_budget_error(exc)
        or is_clickhouse_query_error(exc)
        or _clickhouse_error_code(exc) in _OPTIONAL_USER_ENRICHMENT_ERROR_CODES
    )


def _collect_trace_enrichment_futures(
    future_names: dict[concurrent.futures.Future, str],
    *,
    timeout_seconds: float,
) -> tuple[dict[str, Any], tuple[str, int | None] | None]:
    """Collect required enrichments while allowing only ``users`` to degrade.

    A socket-stalled optional user lookup may remain unfinished after the wall
    wait.  It is cancelled and omitted only when every required future is done.
    Any unfinished required phase, required exception, or user programming
    error still propagates to the endpoint's existing 503/error handling.
    """

    results: dict[str, Any] = {}
    user_degradation: tuple[str, int | None] | None = None

    def consume(future: concurrent.futures.Future) -> None:
        nonlocal user_degradation
        future_name = future_names[future]
        try:
            results[future_name] = future.result()
        except Exception as exc:
            if future_name != "users" or not _is_optional_user_enrichment_failure(exc):
                raise
            results[future_name] = None
            user_degradation = (type(exc).__name__, _clickhouse_error_code(exc))

    try:
        for future in concurrent.futures.as_completed(
            future_names, timeout=timeout_seconds
        ):
            consume(future)
    except concurrent.futures.TimeoutError:
        # Consume every completed future first so a required exception can never
        # be hidden merely because the optional users future also stalled.
        for future in future_names:
            if future.done() and future_names[future] not in results:
                consume(future)
        pending = [future for future in future_names if not future.done()]
        if any(future_names[future] != "users" for future in pending):
            raise
        for future in pending:
            future.cancel()
        if pending:
            results["users"] = None
            user_degradation = ("TimeoutError", None)

    return results, user_degradation


def _decode_trace_list_cursor_order(
    order: tuple[Any, ...], *, org_scope: bool
) -> str | tuple[str, str]:
    """Validate the opaque trace order and return its reader tiebreak token."""

    valid_single_project_order = (
        not org_scope
        and len(order) == 2
        and isinstance(order[0], datetime)
        and isinstance(order[1], str)
    )
    valid_org_order = (
        org_scope
        and len(order) == 3
        and isinstance(order[0], datetime)
        and isinstance(order[1], str)
        and isinstance(order[2], str)
    )
    if not (valid_single_project_order or valid_org_order):
        raise ListCursorError("invalid_cursor", "The continuation cursor is invalid.")
    return (order[1], order[2]) if org_scope else order[1]


def _trace_list_cursor_order_for_row(
    row: dict[str, Any], *, org_scope: bool
) -> tuple[Any, ...]:
    """Freeze the exact public result order without exposing cursor internals."""

    base_order = (row.get("start_time"), str(row.get("trace_id", "")))
    if org_scope:
        return (*base_order, str(row.get("project_id", "")))
    return base_order


def _trace_list_cursor_order_for_partial_page(
    *,
    rows: list[dict[str, Any]],
    bounded_page: Any,
    cursor_state: Any,
    org_scope: bool,
) -> tuple[Any, ...]:
    """Return a public boundary for a progressed empty transport page."""

    if rows:
        return _trace_list_cursor_order_for_row(rows[-1], org_scope=org_scope)
    if cursor_state is not None:
        return tuple(cursor_state.order)
    checkpoint_time = (
        bounded_page.continuation_before_start_time
        or bounded_page.continuation_slice_end
    )
    if checkpoint_time is None:
        raise ValueError("partial trace page has no continuation checkpoint")
    token = bounded_page.continuation_before_id
    if org_scope:
        if isinstance(token, tuple) and len(token) == 2:
            return checkpoint_time, str(token[0]), str(token[1])
        return checkpoint_time, "\U0010ffff", "\U0010ffff"
    return checkpoint_time, str(token) if token is not None else "\U0010ffff"


class TraceNavigationReadUnavailable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class TraceTagsUpdateSerializer(serializers.Serializer):
    tags = serializers.ListField(child=serializers.CharField(), allow_empty=True)


def _sanitize_nonfinite_floats(value):
    """Recursively replace NaN/+-Infinity floats with ``None``.

    ClickHouse aggregates (``avgIf``, ``sumIf`` over NULLs) and arbitrary
    user-supplied metadata/span attributes can carry ``NaN``/``Infinity``
    floats through to the response. DRF's default ``json.dumps`` rejects
    them with ``Out of range float values are not JSON compliant`` and
    returns a 500, so scrub the payload once before serialization.
    """
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return None
    if isinstance(value, dict):
        return {k: _sanitize_nonfinite_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_nonfinite_floats(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_nonfinite_floats(v) for v in value)
    return value


def _trace_attribute_value_token(value: Any) -> tuple[str, str]:
    """Return a stable exact token for heterogeneous custom-attribute values."""

    if isinstance(value, (dict, list)):
        return (
            "structured",
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )
    if isinstance(value, bool):
        return ("bool", str(value).lower())
    return (type(value).__name__, str(value))


def _append_trace_attribute_value(values: list[Any], value: Any) -> None:
    """Append one value exactly once, including scalar/JSON type changes."""

    normalized = str(value).lower() if isinstance(value, bool) else value
    token = _trace_attribute_value_token(normalized)
    if all(_trace_attribute_value_token(existing) != token for existing in values):
        values.append(normalized)


def _iter_merged_trace_attribute_rows(
    replay_row: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Yield legacy packed physical span attribute maps.

    New CH25 trace-list reads project only explicitly requested keys and do not
    use this packed shape: packing all physical rows into one ``groupArray``
    bypasses row limits while retaining unbounded server/client memory.  This
    helper remains for the historical expanded shape and focused compatibility
    mocks.  The tuple keeps typed maps aligned with ``attributes_extra``;
    merging them independently would incorrectly retain a typed value that the
    same span's extra JSON overrides.
    """

    packed_rows = replay_row.get("attribute_rows")
    if packed_rows is None:
        yield merge_span_attributes(
            replay_row.get("attrs_string"),
            replay_row.get("attrs_number"),
            replay_row.get("attrs_bool"),
            replay_row.get("attributes_extra", "{}"),
        )
        return

    for physical_row in packed_rows or ():
        if not isinstance(physical_row, (list, tuple)) or len(physical_row) != 4:
            raise ValueError("invalid packed trace attribute replay row")
        attributes_extra, attrs_string, attrs_number, attrs_bool = physical_row
        yield merge_span_attributes(
            attrs_string,
            attrs_number,
            attrs_bool,
            attributes_extra,
        )


def _decode_projected_trace_attribute_value(raw_value: Any) -> Any:
    """Decode one exact JSON scalar/object projected by the CH25 attr reader.

    The query emits JSON for every storage family so strings remain distinct
    from numbers/booleans and structured ``attributes_extra`` values retain
    their shape.  Invalid payloads are never treated as missing data: callers
    fail the read closed instead of publishing a silently incomplete column.
    """

    if not isinstance(raw_value, str):
        return raw_value
    if not raw_value:
        raise ValueError("empty projected trace attribute value")
    try:
        return json.loads(raw_value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid projected trace attribute JSON") from exc


_SIMULATOR_CALL_EXECUTION_KEYS = (
    "fi.simulator.call_execution_id",
    "fi.simulator.callExecutionId",
    "call_execution_id",
    "callExecutionId",
)


def _first_string_value(*sources, keys):
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return str(value)

        fi = source.get("fi")
        if isinstance(fi, dict):
            simulator = fi.get("simulator")
            if isinstance(simulator, dict):
                value = simulator.get("call_execution_id") or simulator.get(
                    "callExecutionId"
                )
                if value not in (None, ""):
                    return str(value)
    return None


def _is_uuid(value):
    try:
        UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


def _safe_float(value, default=0.0):
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def _safe_parse_metadata(raw):
    """Parse a metadata JSON string from CH, returning {} on failure."""
    if isinstance(raw, dict):
        return raw
    if not raw or not isinstance(raw, str):
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _pivot_voice_detail_eval_rows(
    eval_rows: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Aggregate exact per-span eval rows into the voice-detail contract.

    ``read_trace_detail`` has already replayed the latest physical version of
    each eval row and removed tombstones.  This is intentionally a pure,
    bounded Python reduction over that candidate set; it must not issue a
    second trace-wide ClickHouse query after the reader's shared deadline.
    """

    grouped: dict[str, list[dict[str, Any]]] = {}
    for eval_row in eval_rows:
        config_id = str(eval_row.get("eval_config_id") or "")
        if config_id:
            grouped.setdefault(config_id, []).append(eval_row)

    pivoted: dict[str, dict[str, Any]] = {}
    for config_id, rows in grouped.items():
        completed: list[dict[str, Any]] = []
        errored = False
        status_counts = {"skipped": 0, "running": 0, "pending": 0}
        skipped_reason = None

        for eval_row in rows:
            row_status = str(eval_row.get("status") or "").lower()
            row_error = bool(eval_row.get("error")) or row_status == "errored"
            row_error = row_error or eval_row.get("output_str") == "ERROR"
            if row_error:
                errored = True
                continue
            if row_status in status_counts:
                status_counts[row_status] += 1
                if row_status == "skipped" and not skipped_reason:
                    skipped_reason = eval_row.get("skipped_reason")
                continue
            completed.append(eval_row)

        # Completed rows have precedence over a partial error, matching the
        # list endpoint's completed > errored > lifecycle state contract.
        if completed:
            parsed_choice_lists: list[list[str]] = []
            for eval_row in completed:
                raw_choices = eval_row.get("output_str_list")
                if isinstance(raw_choices, str):
                    try:
                        raw_choices = json.loads(raw_choices)
                    except (json.JSONDecodeError, TypeError):
                        raw_choices = None
                if isinstance(raw_choices, (list, tuple)) and raw_choices:
                    parsed_choice_lists.append([str(value) for value in raw_choices])

            if parsed_choice_lists:
                choice_counts: dict[str, int] = {}
                for choices in parsed_choice_lists:
                    for choice in set(choices):
                        choice_counts[choice] = choice_counts.get(choice, 0) + 1
                total = len(parsed_choice_lists)
                pivoted[config_id] = {
                    "per_choice": {
                        choice: round(100.0 * count / total, 2)
                        for choice, count in choice_counts.items()
                    }
                }
                continue

            float_values = [
                value
                for value in (row.get("output_float") for row in completed)
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            ]
            bool_values = [
                bool(value)
                for value in (row.get("output_bool") for row in completed)
                if value is not None
            ]
            pivoted[config_id] = {
                "avg_score": (
                    round(100.0 * sum(float_values) / len(float_values), 2)
                    if float_values
                    else None
                ),
                "pass_rate": (
                    round(100.0 * sum(bool_values) / len(bool_values), 2)
                    if bool_values
                    else None
                ),
                "count": len(completed),
            }
            continue

        if errored:
            pivoted[config_id] = {"error": True}
        elif status_counts["skipped"]:
            marker: dict[str, Any] = {"status": "skipped"}
            if skipped_reason:
                marker["skipped_reason"] = skipped_reason
            pivoted[config_id] = marker
        elif status_counts["running"]:
            pivoted[config_id] = {"status": "running"}
        elif status_counts["pending"]:
            pivoted[config_id] = {"status": "pending"}

    return pivoted


def _build_agent_graph_pg(project_id, filters, builder):
    """Build a small PostgreSQL-backed agent graph when ClickHouse is unavailable."""
    spans_qs = (
        ObservationSpan.no_workspace_objects.filter(
            project_id=project_id,
            deleted=False,
            trace__deleted=False,
            project__deleted=False,
            start_time__gte=builder.start_date,
            start_time__lt=builder.end_date,
        )
        .exclude(start_time__isnull=True)
        .order_by("-created_at")
    )

    span_rows = list(
        spans_qs.values(
            "id",
            "trace_id",
            "parent_span_id",
            "name",
            "observation_type",
            "latency_ms",
            "total_tokens",
            "cost",
            "status",
            "created_at",
            "start_time",
        )[:5000]
    )

    span_objects = []
    for row in span_rows:
        obj = {
            **row,
            "id": str(row["id"]),
            "trace_id": str(row["trace_id"]),
            "parent_span_id": str(row["parent_span_id"] or ""),
            "system_metrics": {
                "latency": row.get("latency_ms"),
                "latency_ms": row.get("latency_ms"),
                "total_tokens": row.get("total_tokens"),
                "tokens": row.get("total_tokens"),
                "cost": row.get("cost"),
                "status": row.get("status"),
                "name": row.get("name"),
                "span_name": row.get("name"),
            },
        }
        span_objects.append(obj)

    if filters:
        try:
            span_objects = FilterEngine(span_objects).apply_filters(filters)
        except Exception as exc:
            logger.warning(
                "Agent graph PG fallback could not apply filters",
                error=str(exc),
            )

    node_map = {}
    edge_map = {}
    all_span_by_id = {str(row["id"]): row for row in span_rows}

    def node_id(name, node_type):
        return AgentGraphQueryBuilderV2._make_node_id(
            str(name or ""),
            str(node_type or "unknown"),
        )

    def ensure_node(name, node_type):
        nid = node_id(name, node_type)
        if nid not in node_map:
            node_map[nid] = {
                "id": nid,
                "name": str(name or ""),
                "type": str(node_type or "unknown"),
                "span_count": 0,
                "_latency_sum": 0.0,
                "_latency_count": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "error_count": 0,
                "_trace_ids": set(),
            }
        return node_map[nid]

    for row in span_objects:
        node = ensure_node(row.get("name"), row.get("observation_type"))
        node["span_count"] += 1
        latency = row.get("latency_ms")
        if latency is not None:
            node["_latency_sum"] += _safe_float(latency)
            node["_latency_count"] += 1
        node["total_tokens"] += int(row.get("total_tokens") or 0)
        node["total_cost"] += _safe_float(row.get("cost"))
        node["error_count"] += 1 if row.get("status") == "ERROR" else 0
        node["_trace_ids"].add(str(row.get("trace_id")))

    for child in span_objects:
        parent_id = child.get("parent_span_id")
        if not parent_id:
            continue
        parent = all_span_by_id.get(str(parent_id))
        if not parent:
            continue

        source_name = parent.get("name")
        source_type = parent.get("observation_type")
        target_name = child.get("name")
        target_type = child.get("observation_type")
        ensure_node(source_name, source_type)
        ensure_node(target_name, target_type)
        key = (source_name, source_type, target_name, target_type)
        edge = edge_map.setdefault(
            key,
            {
                "source": node_id(source_name, source_type),
                "target": node_id(target_name, target_type),
                "transition_count": 0,
                "_latency_sum": 0.0,
                "_latency_count": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "error_count": 0,
                "_trace_ids": set(),
            },
        )
        edge["transition_count"] += 1
        latency = child.get("latency_ms")
        if latency is not None:
            edge["_latency_sum"] += _safe_float(latency)
            edge["_latency_count"] += 1
        edge["total_tokens"] += int(child.get("total_tokens") or 0)
        edge["total_cost"] += _safe_float(child.get("cost"))
        edge["error_count"] += 1 if child.get("status") == "ERROR" else 0
        edge["_trace_ids"].add(str(child.get("trace_id")))

    nodes = []
    for node in node_map.values():
        latency_count = node.pop("_latency_count")
        latency_sum = node.pop("_latency_sum")
        trace_ids = node.pop("_trace_ids")
        node["avg_latency_ms"] = (
            round(latency_sum / latency_count, 2) if latency_count else 0
        )
        node["total_cost"] = round(node["total_cost"], 6)
        node["trace_count"] = len(trace_ids)
        nodes.append(node)

    edges = []
    for edge in edge_map.values():
        latency_count = edge.pop("_latency_count")
        latency_sum = edge.pop("_latency_sum")
        trace_ids = edge.pop("_trace_ids")
        edge["avg_latency_ms"] = (
            round(latency_sum / latency_count, 2) if latency_count else 0
        )
        edge["total_cost"] = round(edge["total_cost"], 6)
        edge["trace_count"] = len(trace_ids)
        edge["is_self_loop"] = edge["source"] == edge["target"]
        edges.append(edge)

    nodes.sort(key=lambda item: item["span_count"], reverse=True)
    edges.sort(key=lambda item: item["transition_count"], reverse=True)
    return {
        "nodes": nodes[: builder.max_nodes],
        "edges": edges[: builder.max_edges],
    }


def _get_request_organization(request):
    return getattr(request, "organization", None) or getattr(
        getattr(request, "user", None), "organization", None
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
    project_manager = getattr(Project, "no_workspace_objects", Project.objects)
    return project_manager.filter(
        _project_workspace_scope_q(request, project_prefix=""),
        deleted=False,
    )


def _project_version_queryset_for_request(request):
    project_version_manager = getattr(
        ProjectVersion, "no_workspace_objects", ProjectVersion.objects
    )
    return project_version_manager.filter(
        _project_workspace_scope_q(request),
        deleted=False,
        project__deleted=False,
    )


def _trace_session_queryset_for_request(request):
    trace_session_manager = getattr(
        TraceSession, "no_workspace_objects", TraceSession.objects
    )
    return trace_session_manager.filter(
        _project_workspace_scope_q(request),
        deleted=False,
        project__deleted=False,
    )


def _soft_delete_trace_tree(traces):
    now = timezone.now()
    trace_ids = [trace.id for trace in traces if trace]
    if not trace_ids:
        return []

    ObservationSpan.no_workspace_objects.filter(trace_id__in=trace_ids).update(
        deleted=True, deleted_at=now
    )
    EvalLogger.no_workspace_objects.filter(trace_id__in=trace_ids).update(
        deleted=True, deleted_at=now
    )
    try:
        from tracer.models.trace_annotation import TraceAnnotation

        TraceAnnotation.no_workspace_objects.filter(trace_id__in=trace_ids).update(
            deleted=True, deleted_at=now
        )
    except Exception:
        logger.warning("trace_annotation_soft_delete_failed", trace_ids=trace_ids)

    Trace.no_workspace_objects.filter(id__in=trace_ids).update(
        deleted=True, deleted_at=now
    )
    return [str(trace_id) for trace_id in trace_ids]


def _simulation_context_for_voice_call(
    *,
    organization_id,
    span_attributes=None,
    eval_attributes=None,
    raw_log=None,
    metadata=None,
    processed_log=None,
):
    """Return canonical simulator context for a voice trace, if one exists."""

    call_execution_id = _first_string_value(
        span_attributes,
        eval_attributes,
        raw_log,
        metadata,
        processed_log,
        keys=_SIMULATOR_CALL_EXECUTION_KEYS,
    )

    call = None
    if call_execution_id:
        if not _is_uuid(call_execution_id):
            logger.warning(
                "voice_call_invalid_simulator_call_execution_id",
                call_execution_id=call_execution_id,
            )
        else:
            try:
                from simulate.models.test_execution import CallExecution

                call = (
                    CallExecution.objects.select_related("test_execution", "scenario")
                    .filter(
                        id=call_execution_id,
                        test_execution__run_test__organization_id=organization_id,
                    )
                    .first()
                )
            except Exception:
                logger.warning(
                    "voice_call_simulation_context_lookup_failed",
                    call_execution_id=call_execution_id,
                )

    if call is None:
        provider_call_id = None
        if isinstance(processed_log, dict):
            provider_call_id = processed_log.get("call_id")
        if provider_call_id is None and isinstance(raw_log, dict):
            provider_call_id = raw_log.get("id") or raw_log.get("call_id")

        if provider_call_id:
            try:
                from simulate.models.test_execution import CallExecution

                call = (
                    CallExecution.objects.select_related("test_execution", "scenario")
                    .filter(
                        Q(customer_call_id=provider_call_id)
                        | Q(service_provider_call_id=provider_call_id),
                        test_execution__run_test__organization_id=organization_id,
                    )
                    .order_by("-created_at")
                    .first()
                )
            except Exception:
                logger.warning(
                    "voice_call_simulation_context_lookup_failed",
                    provider_call_id=str(provider_call_id),
                )

    if call is None:
        return {}

    scenario_graph = {}
    scenario_graph_id = None
    if call.scenario_id:
        try:
            from simulate.models.scenario_graph import ScenarioGraph

            graph = (
                ScenarioGraph.objects.filter(
                    scenario_id=call.scenario_id, is_active=True
                )
                .order_by("-created_at")
                .first()
            )
            if graph:
                scenario_graph_id = str(graph.id)
                scenario_graph = (
                    graph.graph_config.get("graph_data", {})
                    if isinstance(graph.graph_config, dict)
                    else {}
                )
        except Exception:
            logger.warning(
                "voice_call_scenario_graph_lookup_failed",
                call_execution_id=str(call.id),
                scenario_id=str(call.scenario_id),
            )

    return {
        "call_execution_id": str(call.id),
        "test_execution_id": str(call.test_execution_id),
        "scenario_id": str(call.scenario_id) if call.scenario_id else None,
        "scenario_name": call.scenario.name if call.scenario_id else None,
        "scenario_graph_id": scenario_graph_id,
        "scenario_graph": scenario_graph,
    }


def _annotation_score_span_ids(
    annotation_label_ids,
    project_id,
    *,
    max_span_ids=TRACE_LIST_ANNOTATION_SCORE_SPAN_LIMIT,
):
    """Return a bounded scored-span identity set for one tracer project.

    A public trace page must never enumerate every span in its candidate traces
    just to discover which ones have annotations: one trace can legitimately
    contain millions (or more) unannotated spans.  ``Score`` is authoritative
    for annotations and carries the indexed, denormalized tracer project id, so
    discover only observation-span ids that can contribute to the requested
    label columns.  The subsequent ClickHouse lookup intersects this finite set
    with the page's trace ids and time window.

    ``Score.project_id`` is deliberately not used here; that FK belongs to the
    model-hub project id space.  ``tracer_project_id`` is the observability
    tenancy boundary and is populated/backfilled by the direct-write rollout.

    The ``LIMIT + 1`` sentinel is fail-closed: exceeding the explicit bound
    raises before ClickHouse is contacted. It never publishes a partial
    annotation map and never materializes the project's full score history.
    A missing project scope with requested labels is also rejected; a trace id
    is customer-controlled and cannot serve as an organization tenant key.
    """
    if not annotation_label_ids:
        return ()
    if not project_id:
        raise AnnotationScoreReadBoundExceeded(
            "annotation score hydration requires an explicit tracer project"
        )
    finite_limit = max(int(max_span_ids), 1)
    rows = tuple(
        str(span_id)
        for span_id in Score.no_workspace_objects.filter(
            tracer_project_id=project_id,
            label_id__in=annotation_label_ids,
            trace_id__isnull=True,
            observation_span_id__isnull=False,
            deleted=False,
        )
        .order_by()
        .values_list("observation_span_id", flat=True)
        .distinct()[: finite_limit + 1]
        if span_id
    )
    if len(rows) > finite_limit:
        raise AnnotationScoreReadBoundExceeded(
            "annotation score span identity limit exceeded"
        )
    return rows


def _annotation_score_span_ids_by_project(
    annotation_label_ids_by_project,
    trace_identities,
    *,
    max_span_ids=TRACE_LIST_ANNOTATION_SCORE_SPAN_LIMIT,
):
    """Discover a finite, tenant-qualified scored-span set for an org page.

    The page's ``(project_id, trace_id)`` identities are the authorization and
    candidate boundary.  PostgreSQL can identify legacy span-linked scores by
    tracer project but cannot join the direct-write ClickHouse span table, so
    this reads only those candidate projects and lets one pair-scoped CH replay
    intersect the finite score ids with the exact page trace identities.
    """
    candidate_project_ids = tuple(
        dict.fromkeys(
            str(candidate_project_id)
            for candidate_project_id, candidate_trace_id in trace_identities or ()
            if candidate_project_id and candidate_trace_id
        )
    )
    labels_by_project = {
        str(candidate_project_id): tuple(
            dict.fromkeys(str(label_id) for label_id in label_ids if label_id)
        )
        for candidate_project_id, label_ids in (
            annotation_label_ids_by_project or {}
        ).items()
        if str(candidate_project_id) in candidate_project_ids
    }
    label_ids = tuple(
        dict.fromkeys(
            label_id
            for candidate_project_id in candidate_project_ids
            for label_id in labels_by_project.get(candidate_project_id, ())
        )
    )
    if not candidate_project_ids or not label_ids:
        return {}

    finite_limit = max(int(max_span_ids), 1)
    rows = tuple(
        (str(row_project_id), str(span_id))
        for row_project_id, span_id in Score.no_workspace_objects.filter(
            tracer_project_id__in=candidate_project_ids,
            label_id__in=label_ids,
            trace_id__isnull=True,
            observation_span_id__isnull=False,
            deleted=False,
        )
        .order_by()
        .values_list("tracer_project_id", "observation_span_id")
        .distinct()[: finite_limit + 1]
        if row_project_id and span_id
    )
    if len(rows) > finite_limit:
        raise AnnotationScoreReadBoundExceeded(
            "annotation score span identity limit exceeded"
        )

    rows_by_project: dict[str, list[str]] = {}
    for row_project_id, span_id in rows:
        # The tenant predicate already constrains this, but keep an explicit
        # in-process guard so malformed/mocked rows cannot widen the page.
        if row_project_id not in labels_by_project:
            raise AnnotationScoreReadBoundExceeded(
                "annotation score replay escaped candidate project scope"
            )
        rows_by_project.setdefault(row_project_id, []).append(span_id)
    return {
        row_project_id: tuple(dict.fromkeys(span_ids))
        for row_project_id, span_ids in rows_by_project.items()
    }


def _build_annotation_map_from_scores(
    trace_ids,
    annotation_label_ids,
    label_types,
    span_trace_map=None,
    analytics=None,
    project_id=None,
    start_date=None,
    end_date=None,
    trace_identities=None,
    annotation_label_ids_by_project=None,
):
    """Fetch annotation values from PG Score table and build annotation_map.

    Always reads from PG to guarantee read-after-write consistency —
    annotations are written to PG first and CDC replication to ClickHouse
    may lag, causing newly created annotations to be invisible.

    ``project_id``/``start_date``/``end_date`` scope the span->trace CH
    lookup when this builds the map itself (span_trace_map not supplied).
    Direct-write callers supply their V2 analytics service; the fallback is
    also explicitly V2 so routing flags can never select the legacy cluster.

    Returns:
        Dict mapping trace_id -> label_id -> structured annotation data
        matching the format produced by build_annotation_subqueries (PG ORM path).
    """
    org_scoped = trace_identities is not None
    effective_label_ids = tuple(
        dict.fromkeys(
            [
                *(annotation_label_ids or ()),
                *(
                    label_id
                    for label_ids in (annotation_label_ids_by_project or {}).values()
                    for label_id in label_ids
                ),
            ]
        )
    )
    if not trace_ids or not effective_label_ids:
        return {}
    if span_trace_map is None:
        if org_scoped:
            scored_span_ids_by_project = _annotation_score_span_ids_by_project(
                annotation_label_ids_by_project,
                trace_identities,
            )
            scored_span_identities = tuple(
                (candidate_project_id, span_id)
                for candidate_project_id, span_ids in scored_span_ids_by_project.items()
                for span_id in span_ids
            )
            if scored_span_identities:
                analytics = analytics or V2AnalyticsQueryService()
                span_trace_map = analytics.get_span_trace_map(
                    trace_ids,
                    trace_identities=trace_identities,
                    scored_span_identities=scored_span_identities,
                )
            else:
                span_trace_map = {}
        else:
            scored_span_ids = _annotation_score_span_ids(
                annotation_label_ids, project_id
            )
            if scored_span_ids:
                analytics = analytics or V2AnalyticsQueryService()
                span_trace_map = analytics.get_span_trace_map(
                    trace_ids,
                    project_id=project_id,
                    start_date=start_date,
                    end_date=end_date,
                    scored_span_ids=scored_span_ids,
                )
            else:
                # Direct trace-linked Score rows still need to be returned, but a
                # project with no span-linked scores requires no ClickHouse query.
                span_trace_map = {}
    annotation_read_kwargs = {"project_id": project_id}
    if org_scoped:
        annotation_read_kwargs.update(
            trace_identities=trace_identities,
            annotation_label_ids_by_project=annotation_label_ids_by_project,
        )
    return _build_annotation_map_from_scores_pg(
        trace_ids,
        effective_label_ids if org_scoped else annotation_label_ids,
        label_types,
        span_trace_map,
        **annotation_read_kwargs,
    )


def _build_annotation_map_from_scores_ch(trace_ids, annotation_label_ids, label_types):
    """ClickHouse implementation of annotation map builder."""
    import json

    from accounts.models.user import User

    analytics = AnalyticsQueryService()

    sql = """
    SELECT
        toString(trace_id) AS trace_id,
        toString(label_id) AS label_id,
        value,
        toString(annotator_id) AS annotator_id
    FROM model_hub_score FINAL
    WHERE trace_id IN %(trace_ids)s
      AND label_id IN %(label_ids)s
      AND _peerdb_is_deleted = 0
    """
    params = {
        "trace_ids": tuple(str(t) for t in trace_ids),
        "label_ids": tuple(str(lid) for lid in annotation_label_ids),
    }
    result = analytics.execute_ch_query(sql, params)

    # Collect unique annotator IDs for name resolution
    annotator_ids = set()
    for row in result.data:
        aid = row.get("annotator_id")
        if aid and aid != "00000000-0000-0000-0000-000000000000":
            annotator_ids.add(aid)

    # Batch lookup annotator names from PG
    user_name_map = {}
    if annotator_ids:
        users = User.objects.filter(id__in=list(annotator_ids)).values(
            "id", "name", "email"
        )
        for u in users:
            uid = str(u["id"])
            user_name_map[uid] = u["name"] or u["email"] or "Unknown"

    annotation_map = {}
    for row in result.data:
        tid = row["trace_id"]
        lid = row["label_id"]
        uid = row.get("annotator_id")
        if uid == "00000000-0000-0000-0000-000000000000":
            uid = None
        user_name = user_name_map.get(uid, "Unknown") if uid else "Unknown"
        ltype = label_types.get(lid, "").lower()
        annotation_map.setdefault(tid, {})

        # Parse the value JSON string from CH
        raw_val = row.get("value", "{}")
        if isinstance(raw_val, str):
            try:
                val = json.loads(raw_val)
            except (json.JSONDecodeError, TypeError):
                val = {}
        else:
            val = raw_val if isinstance(raw_val, dict) else {}

        if ltype in ("numeric", "star"):
            value_key = "value" if ltype == "numeric" else "rating"
            score_val = val.get(value_key) if isinstance(val, dict) else val
            try:
                score_val = float(score_val) if score_val is not None else None
            except (ValueError, TypeError):
                score_val = None
            if score_val is None:
                continue
            entry = annotation_map[tid].setdefault(
                lid, {"score": None, "_sum": 0.0, "_count": 0, "annotators": {}}
            )
            entry["_sum"] += score_val
            entry["_count"] += 1
            entry["score"] = int(entry["_sum"] / entry["_count"])
            if uid:
                anno = entry["annotators"].setdefault(
                    uid,
                    {
                        "user_id": uid,
                        "user_name": user_name,
                        "_sum": 0.0,
                        "_count": 0,
                        "score": None,
                    },
                )
                anno["_sum"] += score_val
                anno["_count"] += 1
                anno["score"] = anno["_sum"] / anno["_count"]

        elif ltype == "thumbs_up_down":
            thumb_val = val.get("value") if isinstance(val, dict) else val
            is_up = thumb_val in (True, "up", 1, "true")
            entry = annotation_map[tid].setdefault(
                lid, {"thumbs_up": 0, "thumbs_down": 0, "annotators": {}}
            )
            if is_up:
                entry["thumbs_up"] += 1
            else:
                entry["thumbs_down"] += 1
            if uid:
                anno = entry["annotators"].setdefault(
                    uid,
                    {
                        "user_id": uid,
                        "user_name": user_name,
                        "_up": 0,
                        "_down": 0,
                        "score": None,
                    },
                )
                if is_up:
                    anno["_up"] += 1
                else:
                    anno["_down"] += 1
                total = anno["_up"] + anno["_down"]
                anno["score"] = (anno["_up"] / total) * 100.0 if total else None

        elif ltype == "categorical":
            selected = (
                val.get("selected", [])
                if isinstance(val, dict)
                else (val if isinstance(val, list) else [])
            )
            entry = annotation_map[tid].setdefault(lid, {"annotators": {}})
            for choice in selected:
                entry[choice] = entry.get(choice, 0) + 1
            if uid:
                anno = entry["annotators"].setdefault(
                    uid,
                    {
                        "user_id": uid,
                        "user_name": user_name,
                        "value": [],
                    },
                )
                anno["value"] = list({*anno["value"], *selected})

        elif ltype == "text":
            text_val = val.get("text", val) if isinstance(val, dict) else val
            entry = annotation_map[tid].setdefault(
                lid, {"score": text_val, "annotators": {}}
            )
            entry["score"] = text_val
            if uid:
                entry["annotators"][uid] = {
                    "user_id": uid,
                    "user_name": user_name,
                    "value": text_val,
                }
        else:
            annotation_map[tid].setdefault(lid, {"score": val, "annotators": {}})

    # Strip internal accumulators before returning — same rationale as
    # the PG path.
    for trace_entry in annotation_map.values():
        for label_entry in trace_entry.values():
            label_entry.pop("_sum", None)
            label_entry.pop("_count", None)
            for anno in label_entry.get("annotators", {}).values():
                anno.pop("_sum", None)
                anno.pop("_count", None)
                anno.pop("_up", None)
                anno.pop("_down", None)

    return annotation_map


def _build_annotation_map_from_scores_pg(
    trace_ids,
    annotation_label_ids,
    label_types,
    span_trace_map=None,
    *,
    project_id=None,
    trace_identities=None,
    annotation_label_ids_by_project=None,
):
    """PG fallback implementation of annotation map builder.

    Per-queue scoring means a single (trace, label, annotator) can now
    have multiple Score rows — one per queue review context. The trace
    list aggregate must average across *every* contribution, not collapse
    them by annotator. We accumulate counts/sums while iterating and
    average per-annotator within their queues as well so the per-annotator
    breakdown stays meaningful (one number per annotator, averaging their
    queues).
    """
    from django.db.models import Q

    span_trace_map = span_trace_map or {}
    org_scoped = trace_identities is not None
    candidate_trace_identities = tuple(
        dict.fromkeys(
            (str(candidate_project_id), str(candidate_trace_id))
            for candidate_project_id, candidate_trace_id in trace_identities or ()
            if candidate_project_id and candidate_trace_id
        )
    )
    candidate_trace_identity_set = set(candidate_trace_identities)
    span_ids = list(span_trace_map.keys())
    annotation_map = {}
    # Trace- or span-linked scores by column id (no dropped-table JOIN).
    if org_scoped:
        trace_ids_by_project: dict[str, list[str]] = {}
        span_ids_by_project: dict[str, list[str]] = {}
        for candidate_project_id, candidate_trace_id in candidate_trace_identities:
            trace_ids_by_project.setdefault(candidate_project_id, []).append(
                candidate_trace_id
            )
        for candidate_project_id, candidate_span_id in span_ids:
            span_ids_by_project.setdefault(str(candidate_project_id), []).append(
                str(candidate_span_id)
            )

        # Keep SQL topology O(candidate projects), not O(candidate spans).
        # A page may map tens of thousands of legacy scored spans; emitting one
        # OR node per identity can overflow PostgreSQL's parser/parameter stack.
        # Project-qualified IN predicates preserve composite tenant identity
        # while keeping the expression tree finite and shallow.
        entity_scope = Q(pk__in=[])
        for candidate_project_id in trace_ids_by_project:
            allowed_project_labels = tuple(
                dict.fromkeys(
                    str(label_id)
                    for label_id in (annotation_label_ids_by_project or {}).get(
                        candidate_project_id, ()
                    )
                    if label_id
                )
            )
            if not allowed_project_labels:
                continue
            candidate_project_trace_ids = tuple(
                dict.fromkeys(trace_ids_by_project[candidate_project_id])
            )
            if candidate_project_trace_ids:
                entity_scope |= Q(
                    tracer_project_id=candidate_project_id,
                    trace_id__in=candidate_project_trace_ids,
                    label_id__in=allowed_project_labels,
                )
            candidate_project_span_ids = tuple(
                dict.fromkeys(span_ids_by_project.get(candidate_project_id, ()))
            )
            if candidate_project_span_ids:
                entity_scope |= Q(
                    tracer_project_id=candidate_project_id,
                    observation_span_id__in=candidate_project_span_ids,
                    label_id__in=allowed_project_labels,
                )
    else:
        entity_scope = Q(trace_id__in=trace_ids)
        if span_ids:
            entity_scope |= Q(observation_span_id__in=span_ids)
    score_filters = {
        "label_id__in": annotation_label_ids,
        "deleted": False,
    }
    if project_id:
        score_filters["tracer_project_id"] = project_id
    elif org_scoped:
        score_filters["tracer_project_id__in"] = tuple(
            dict.fromkeys(
                candidate_project_id
                for candidate_project_id, _ in candidate_trace_identities
            )
        )
    scores = list(
        Score.no_workspace_objects.filter(
            entity_scope,
            **score_filters,
        ).select_related("annotator")[: TRACE_LIST_ANNOTATION_SCORE_SPAN_LIMIT + 1]
    )
    if len(scores) > TRACE_LIST_ANNOTATION_SCORE_SPAN_LIMIT:
        raise AnnotationScoreReadBoundExceeded("annotation score row limit exceeded")

    for s in scores:
        score_project_id = str(getattr(s, "tracer_project_id", None) or "")
        if org_scoped:
            tid = (
                (score_project_id, str(s.trace_id))
                if s.trace_id
                else span_trace_map.get((score_project_id, str(s.observation_span_id)))
            )
            if tid not in candidate_trace_identity_set:
                continue
            allowed_labels = {
                str(label_id)
                for label_id in (annotation_label_ids_by_project or {}).get(
                    score_project_id, ()
                )
            }
            if str(s.label_id) not in allowed_labels:
                continue
        else:
            tid = (
                str(s.trace_id)
                if s.trace_id
                else span_trace_map.get(str(s.observation_span_id))
            )
        if not tid or tid == "None":
            continue
        lid = str(s.label_id)
        uid = str(s.annotator_id) if s.annotator_id else None
        user_name = (
            (s.annotator.name or s.annotator.email or "Unknown")
            if s.annotator
            else "Unknown"
        )
        ltype = label_types.get(lid, "").lower()
        annotation_map.setdefault(tid, {})
        val = s.value  # JSONField

        if ltype in ("numeric", "star"):
            value_key = "value" if ltype == "numeric" else "rating"
            score_val = val.get(value_key) if isinstance(val, dict) else val
            try:
                score_val = float(score_val) if score_val is not None else None
            except (ValueError, TypeError):
                score_val = None
            if score_val is None:
                continue
            entry = annotation_map[tid].setdefault(
                lid, {"score": None, "_sum": 0.0, "_count": 0, "annotators": {}}
            )
            entry["_sum"] += score_val
            entry["_count"] += 1
            entry["score"] = int(entry["_sum"] / entry["_count"])
            if uid:
                anno = entry["annotators"].setdefault(
                    uid,
                    {
                        "user_id": uid,
                        "user_name": user_name,
                        "_sum": 0.0,
                        "_count": 0,
                        "score": None,
                    },
                )
                anno["_sum"] += score_val
                anno["_count"] += 1
                anno["score"] = anno["_sum"] / anno["_count"]

        elif ltype == "thumbs_up_down":
            thumb_val = val.get("value") if isinstance(val, dict) else val
            is_up = thumb_val in (True, "up", 1, "true")
            entry = annotation_map[tid].setdefault(
                lid,
                {
                    "thumbs_up": 0,
                    "thumbs_down": 0,
                    "annotators": {},
                },
            )
            if is_up:
                entry["thumbs_up"] += 1
            else:
                entry["thumbs_down"] += 1
            if uid:
                anno = entry["annotators"].setdefault(
                    uid,
                    {
                        "user_id": uid,
                        "user_name": user_name,
                        "_up": 0,
                        "_down": 0,
                        "score": None,
                    },
                )
                if is_up:
                    anno["_up"] += 1
                else:
                    anno["_down"] += 1
                total = anno["_up"] + anno["_down"]
                anno["score"] = (anno["_up"] / total) * 100.0 if total else None

        elif ltype == "categorical":
            selected = (
                val.get("selected", [])
                if isinstance(val, dict)
                else (val if isinstance(val, list) else [])
            )
            entry = annotation_map[tid].setdefault(lid, {"annotators": {}})
            for choice in selected:
                entry[choice] = entry.get(choice, 0) + 1
            if uid:
                anno = entry["annotators"].setdefault(
                    uid,
                    {
                        "user_id": uid,
                        "user_name": user_name,
                        "value": [],
                    },
                )
                anno["value"] = list({*anno["value"], *selected})

        elif ltype == "text":
            text_val = val.get("text", val) if isinstance(val, dict) else val
            entry = annotation_map[tid].setdefault(
                lid, {"score": text_val, "annotators": {}}
            )
            # Keep latest text as the aggregate display (text doesn't average)
            entry["score"] = text_val
            if uid:
                entry["annotators"][uid] = {
                    "user_id": uid,
                    "user_name": user_name,
                    "value": text_val,
                }
        else:
            annotation_map[tid].setdefault(lid, {"score": val, "annotators": {}})

    # Strip internal aggregation accumulators so the JSON payload stays
    # clean. The frontend only needs `score`, per-annotator scores, and
    # the categorical/thumbs counts.
    for trace_entry in annotation_map.values():
        for label_entry in trace_entry.values():
            label_entry.pop("_sum", None)
            label_entry.pop("_count", None)
            for anno in label_entry.get("annotators", {}).values():
                anno.pop("_sum", None)
                anno.pop("_count", None)
                anno.pop("_up", None)
                anno.pop("_down", None)

    return annotation_map


class TraceView(BaseModelViewSetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = TraceSerializer

    @staticmethod
    def _to_finite_number(value):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return parsed

    @staticmethod
    def _round_metric(value):
        """Round a numeric metric to an integer for display.

        Returns None for non-numeric or non-finite values.  This ensures
        the API response matches the ClickHouse filter expressions so
        that filtering and display always agree.
        """
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return int(round(parsed))

    def _extract_voice_turn_and_talk_metrics(self, attrs: dict, raw_log: dict):
        """Extract normalized per-call voice metrics for UI consumption."""
        attrs = attrs or {}
        raw_log = raw_log or {}
        call_attrs = attrs.get("call") if isinstance(attrs.get("call"), dict) else {}
        perf = (raw_log.get("artifact") or {}).get("performanceMetrics") or {}
        structured = (raw_log.get("analysis") or {}).get("structuredData") or {}

        turn_count = self._round_metric(attrs.get("call.total_turns"))

        talk_ratio_candidates = [
            attrs.get("call.talk_ratio"),
            attrs.get("call.talkRatio"),
            attrs.get("talkRatio"),
            call_attrs.get("talk_ratio"),
            call_attrs.get("talkRatio"),
            perf.get("talkRatio"),
            structured.get("talkRatio"),
            structured.get("talk_ratio"),
            attrs.get("avg_talk_ratio"),
            (
                (attrs.get("metrics_data") or {}).get("talk_ratio")
                if isinstance(attrs.get("metrics_data"), dict)
                else None
            ),
        ]

        talk_ratio = None
        for candidate in talk_ratio_candidates:
            parsed = self._to_finite_number(candidate)
            if parsed is not None and parsed >= 0:
                talk_ratio = parsed
                break

        if talk_ratio is None:
            agent_percentage_candidates = [
                attrs.get("call.agent_talk_percentage"),
                attrs.get("call.agentTalkPercentage"),
                attrs.get("agentTalkPercentage"),
                call_attrs.get("agent_talk_percentage"),
                call_attrs.get("agentTalkPercentage"),
                structured.get("agentTalkPercentage"),
            ]
            for candidate in agent_percentage_candidates:
                parsed = self._to_finite_number(candidate)
                if parsed is None or parsed < 0 or parsed > 100:
                    continue
                if parsed >= 100:
                    talk_ratio = None
                else:
                    talk_ratio = parsed / (100 - parsed)
                break

        agent_talk_percentage = None
        bot_talk_pct = None
        user_talk_pct = None
        if talk_ratio is not None:
            denominator = talk_ratio + 1
            if denominator > 0:
                raw_bot_pct = (talk_ratio / denominator) * 100
                agent_talk_percentage = round(raw_bot_pct, 2)
                # Integer split rendered by the FE (no client-side rounding).
                bot_talk_pct = round(raw_bot_pct)
                user_talk_pct = 100 - bot_talk_pct

        return {
            "turn_count": turn_count,
            "talk_ratio": talk_ratio,
            "agent_talk_percentage": agent_talk_percentage,
            "bot_talk_pct": bot_talk_pct,
            "user_talk_pct": user_talk_pct,
        }

    def get_queryset(self):
        trace_id = self.kwargs.get("pk")

        # Get base queryset with automatic filtering from mixin
        query_Set = super().get_queryset()
        organization = _get_request_organization(self.request)
        if organization:
            query_Set = query_Set.filter(project__organization=organization)

        if trace_id:
            return query_Set.filter(id=trace_id)

        project_id = self.request.query_params.get("project_id")
        project_version_id = self.request.query_params.get("project_version_id")
        trace_ids = self.request.query_params.get("trace_ids")

        if project_id:
            query_Set = query_Set.filter(project_id=project_id)

        if project_version_id:
            query_Set = query_Set.filter(project_version_id=project_version_id)

        if trace_ids:
            trace_ids = trace_ids.split(",")
            query_Set = (
                query_Set.filter(id__in=trace_ids) if len(trace_ids) > 0 else query_Set
            )

        return query_Set

    def perform_destroy(self, instance):
        _soft_delete_trace_tree([instance])

    @swagger_auto_schema(
        responses={
            200: TraceDetailResponseSerializer,
            **ERROR_RESPONSES,
            503: ApiErrorResponseSerializer,
        },
    )
    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve a trace by its ID.
        """
        from tracer.services.clickhouse.v2.trace_detail_reads import (
            TraceDetailReadUnavailable,
        )

        try:
            trace_id = kwargs.get("pk")
            from tracer.services.clickhouse.v2.query_builders.trace_detail import (
                TraceDetailHandlerV2,
            )

            handler = TraceDetailHandlerV2(
                view=self,
                request=request,
                pk=trace_id,
                analytics=V2AnalyticsQueryService(),
            )
            return self._gm.success_response(handler.fetch())
        except Trace.DoesNotExist:
            return self._gm.bad_request(
                f"error retrieving trace {get_error_message('ERROR_GETTING_TRACE')}"
            )
        except TraceDetailReadUnavailable as exc:
            logger.warning(
                "trace_detail_bounded_read_incomplete",
                trace_id=str(kwargs.get("pk") or ""),
                error_code=exc.code,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace details are temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as exc:
            logger.exception(
                "trace_detail_request_failed",
                trace_id=str(kwargs.get("pk") or ""),
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Trace details could not be loaded")

    # Keys to strip from the list response (heavy / detail-only fields).
    _VOICE_CALL_HEAVY_KEYS = frozenset(
        {
            "transcript",
            "messages",
            "recording",
            "stereo_recording_url",
            "call_metadata",
            "analysis_data",
            "evaluation_data",
            "error_message",
            "observation_span",
            "call_logs",
            "raw_log",
        }
    )

    @staticmethod
    def _build_recording_dict(attrs):
        """Build a recording dict from span attributes. Shared by list & detail."""

        def _get(key):
            return attrs.get(key)

        return {
            "mono": {
                "combined_url": _get(
                    f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_COMBINED}"
                ),
                "customer_url": _get(
                    f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_CUSTOMER}"
                ),
                "assistant_url": _get(
                    f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_ASSISTANT}"
                ),
            },
            "stereo_url": _get(
                f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.STEREO}"
            ),
        }

    @staticmethod
    def _recording_available(recording):
        """True when the recording dict carries any playable URL. Collector pulls
        drop raw_log so process_raw_logs can't infer this; derive it from the
        recovered URLs (mirrors transcript_available)."""
        rec = recording or {}
        mono = rec.get("mono") or {}
        return bool(
            rec.get("stereo_url")
            or mono.get("combined_url")
            or mono.get("customer_url")
            or mono.get("assistant_url")
        )

    @staticmethod
    def _coerce_raw_log(value):
        """raw_log rides in span attributes as a JSON string (collector path) or a
        dict (legacy PG+CDC). Return a dict either way so process_raw_logs can
        recompute status/duration/recording_available/transcript from it."""
        if isinstance(value, str):
            try:
                return json.loads(value) or {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return value or {}

    def populate_call_logs_result(
        self, qs, eval_configs, annotation_labels=None, *, detail_mode=False
    ):
        results = []
        # Materialize qs so we can do a single bulk-fetch for the agent-eval
        # output_str fallback below (otherwise we'd N×M query inside the loop —
        # one lookup per (trace × choices/score config) pair).
        qs = list(qs)

        # Pre-fetch EvalLogger.output_str for traces × configs whose template
        # output type is "choices" or "score". Agent-evaluator writes the result
        # as a Python dict literal in output_str (e.g. "{'score': 0.0,
        # 'choice': 'never'}") when output_float/output_str_list are empty.
        # Keyed by (trace_id, config_id); only the most recent row per pair.
        _str_lookup_configs = [
            c
            for c in eval_configs
            if (
                (getattr(getattr(c, "eval_template", None), "config", None) or {}).get(
                    "output"
                )
            )
            in (EvalOutputType.CHOICES.value, EvalOutputType.SCORE.value)
        ]
        output_str_map: dict[tuple, EvalLogger] = {}
        if _str_lookup_configs and qs:
            trace_ids_for_lookup = [t.id for t in qs]
            for log in (
                EvalLogger.objects.filter(
                    trace_id__in=trace_ids_for_lookup,
                    custom_eval_config_id__in=[c.id for c in _str_lookup_configs],
                    deleted=False,
                )
                .order_by("trace_id", "custom_eval_config_id", "-created_at")
                .only("trace_id", "custom_eval_config_id", "output_str")
            ):
                key = (log.trace_id, log.custom_eval_config_id)
                if key not in output_str_map:  # first hit = most recent
                    output_str_map[key] = log

        for trace in qs:
            attrs = getattr(trace, "span_attributes", None) or {}
            metadata = getattr(trace, "metadata", None) or {}

            # Extract values from span_attributes (flattened keys)
            def attr(key: str):
                return attrs.get(key)  # noqa: B023

            recording = self._build_recording_dict(attrs)

            # Raw provider payload if present (collector ships it as JSON string)
            raw_log = self._coerce_raw_log(attrs.get("raw_log"))
            provider = trace.provider or "vapi"

            processed_log = ObservabilityService.process_raw_logs(
                raw_log, provider, span_attributes=attrs
            )
            voice_metrics = self._extract_voice_turn_and_talk_metrics(attrs, raw_log)

            # Observation spans are served by the detail endpoint — skip
            # serialization here (~2.8 MB per row).
            observation_span = []

            # Use the stored call.duration from eval_attributes as the single
            # source of truth so the API response always matches the metric.
            stored_duration = attrs.get(CallAttributes.DURATION)
            if stored_duration is not None:
                stored_duration = int(stored_duration)

            # TODO: Verification via testing pending
            result = {
                **processed_log,
                "id": str(trace.id),
                "trace_id": str(trace.id),
                "call_metadata": metadata,
                "recording": recording,
                "recording_available": self._recording_available(recording),
                "observation_span": observation_span,
                "turn_count": voice_metrics.get("turn_count"),
                "talk_ratio": voice_metrics.get("talk_ratio"),
                "agent_talk_percentage": voice_metrics.get("agent_talk_percentage"),
                "bot_talk_pct": voice_metrics.get("bot_talk_pct"),
                "user_talk_pct": voice_metrics.get("user_talk_pct"),
                "avg_agent_latency_ms": self._round_metric(
                    attr("avg_agent_latency_ms")
                ),
                "user_wpm": self._round_metric(attr(CallAttributes.USER_WPM)),
                "bot_wpm": self._round_metric(attr(CallAttributes.BOT_WPM)),
                "user_interruption_count": self._round_metric(
                    attr("user_interruption_count")
                ),
                "ai_interruption_count": self._round_metric(
                    attr("ai_interruption_count")
                ),
            }
            if stored_duration is not None:
                result["duration_seconds"] = stored_duration

            # Add metrics per eval config
            metrics = {}
            for config in eval_configs:
                data = getattr(trace, f"metric_{config.id}", None)
                metric_type = getattr(trace, f"metric_type_{config.id}", None)
                reason = getattr(trace, f"metric_reason_{config.id}", None)
                error = getattr(trace, f"error_{config.id}", False)
                metric_name = getattr(config, "name", None) or (
                    getattr(config, "eval_template", None).name
                    if getattr(config, "eval_template", None)
                    else None
                )

                metric_entry = {
                    "name": metric_name,
                    "output_type": metric_type,
                    "reason": reason,
                    "error": error,
                }

                if isinstance(data, list):
                    # str_list type returns a direct array of choices
                    metric_entry["output"] = data
                elif isinstance(data, dict) and "score" in data.keys():
                    score_val = data.get("score")
                    if metric_type == EvalOutputType.PASS_FAIL:
                        metric_entry["output"] = "Pass" if score_val > 0 else "Fail"
                    else:
                        metric_entry["output"] = (
                            round(score_val, 2)
                            if isinstance(score_val, int | float)
                            else score_val
                        )
                elif isinstance(data, dict) and data:
                    per_choice = []
                    for choice_key, val in data.items():
                        score_val = val.get("score") if isinstance(val, dict) else None
                        choice_score = (
                            round(score_val, 2)
                            if isinstance(score_val, int | float)
                            else score_val
                        )
                        if choice_score > 0:
                            per_choice.append(choice_key)
                    metric_entry["output"] = per_choice

                # New agent-evaluator path: when the legacy fields are empty,
                # read the chosen bucket (or numeric score) from
                # EvalLogger.output_str — stored as a Python dict literal like
                # "{'score': 0.0, 'choice': 'never'}". Uses the bulk-fetched
                # map built before the trace loop (no per-row query).
                if metric_entry.get("output") in (None, [], ""):
                    tpl = getattr(config, "eval_template", None)
                    tpl_output = (
                        (getattr(tpl, "config", None) or {}).get("output")
                        if tpl is not None
                        else None
                    )
                    log = output_str_map.get((trace.id, config.id))
                    if (
                        log
                        and log.output_str
                        and tpl_output
                        in (
                            EvalOutputType.CHOICES.value,
                            EvalOutputType.SCORE.value,
                        )
                    ):
                        try:
                            import ast as _ast_mod

                            parsed = _ast_mod.literal_eval(log.output_str)
                        except (ValueError, SyntaxError):
                            parsed = None
                        if isinstance(parsed, dict):
                            if tpl_output == EvalOutputType.CHOICES.value:
                                choice = parsed.get("choice")
                                if choice:
                                    metric_entry["output"] = [choice]
                                    metric_entry["output_type"] = (
                                        EvalOutputType.CHOICES.value
                                    )
                                    # Mirror as top-level `score` so the
                                    # drawer's `e?.score ?? e?.output ?? e?.value`
                                    # lookup hits a string and renders verbatim
                                    # — avoids a frontend renderer change.
                                    metric_entry["score"] = choice
                            elif tpl_output == EvalOutputType.SCORE.value:
                                score_val = parsed.get("score")
                                if isinstance(score_val, (int, float)):
                                    # output_str's score is 0–1; backend convention
                                    # for score evals is 0–100 (consistent with the
                                    # output_float * 100 branch above).
                                    metric_entry["output"] = round(
                                        float(score_val) * 100, 2
                                    )
                                    metric_entry["output_type"] = (
                                        EvalOutputType.SCORE.value
                                    )

                metrics[str(config.id)] = metric_entry
            if metrics:
                result["eval_outputs"] = metrics

            # Add annotation outputs — flatten onto the row for frontend grid compatibility
            if annotation_labels:
                annotation_outputs = {}
                for label in annotation_labels:
                    avg_value = getattr(trace, f"annotation_{label.id}", None)
                    if avg_value is not None:
                        result[str(label.id)] = avg_value
                        annotation_outputs[str(label.id)] = avg_value
                if annotation_outputs:
                    result["annotation_outputs"] = annotation_outputs

            # In list mode, strip heavy fields to keep the response lightweight.
            if not detail_mode:
                for key in self._VOICE_CALL_HEAVY_KEYS:
                    result.pop(key, None)

            results.append(result)

        return results

    @staticmethod
    def _build_annotation_subqueries(base_query, annotation_labels, organization):
        """
        Annotate *base_query* with aggregated annotation subqueries for every
        label in *annotation_labels*.

        Delegates to ``tracer.utils.annotations.build_annotation_subqueries``.
        """
        return _build_annotation_subqueries_impl(
            base_query, annotation_labels, organization
        )

    def get_eval_configs(self, project_id, base_query):
        eval_configs = CustomEvalConfig.objects.filter(
            id__in=EvalLogger.objects.filter(
                trace_id__in=Trace.objects.filter(project_id=project_id).values("id")
            )
            .values("custom_eval_config_id")
            .distinct(),
            deleted=False,
        ).select_related("eval_template")

        for config in eval_configs:
            metric_subquery = (
                EvalLogger.objects.filter(
                    trace_id=OuterRef("id"),
                    custom_eval_config_id=config.id,
                    error=False,
                )
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
                )
                .values("float_score", "bool_score")[:1]
            )

            str_list_subquery = EvalLogger.objects.filter(
                trace_id=OuterRef("id"),
                custom_eval_config_id=config.id,
                output_str_list__isnull=False,
                error=False,
            ).values("output_str_list")[:1]

            base_query = base_query.annotate(
                **{
                    f"metric_{config.id}": Case(
                        When(
                            Exists(
                                EvalLogger.objects.filter(
                                    trace_id=OuterRef("id"),
                                    custom_eval_config_id=config.id,
                                    output_float__isnull=False,
                                )
                            ),
                            then=JSONObject(
                                score=Subquery(metric_subquery.values("float_score"))
                            ),
                        ),
                        When(
                            Exists(
                                EvalLogger.objects.filter(
                                    trace_id=OuterRef("id"),
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
                                    trace_id=OuterRef("id"),
                                    custom_eval_config_id=config.id,
                                    output_str_list__isnull=False,
                                )
                            ),
                            then=Subquery(str_list_subquery),
                        ),
                        default=None,
                        output_field=JSONField(),
                    ),
                    f"metric_type_{config.id}": Case(
                        When(
                            Exists(
                                EvalLogger.objects.filter(
                                    trace_id=OuterRef("id"),
                                    custom_eval_config_id=config.id,
                                    output_float__isnull=False,
                                )
                            ),
                            then=Value(EvalOutputType.SCORE),
                        ),
                        When(
                            Exists(
                                EvalLogger.objects.filter(
                                    trace_id=OuterRef("id"),
                                    custom_eval_config_id=config.id,
                                    output_bool__isnull=False,
                                )
                            ),
                            then=Value(EvalOutputType.PASS_FAIL),
                        ),
                        When(
                            Exists(
                                EvalLogger.objects.filter(
                                    trace_id=OuterRef("id"),
                                    custom_eval_config_id=config.id,
                                    output_str_list__isnull=False,
                                )
                            ),
                            then=Value(EvalOutputType.CHOICES),
                        ),
                        default=None,
                        output_field=JSONField(),
                    ),
                    f"metric_reason_{config.id}": Subquery(
                        metric_subquery.values("eval_explanation")
                    ),
                    f"error_{config.id}": Case(
                        When(
                            ~Exists(
                                EvalLogger.objects.filter(
                                    trace_id=OuterRef("id"),
                                    custom_eval_config_id=config.id,
                                    error=False,
                                )
                            )
                            & Exists(
                                EvalLogger.objects.filter(
                                    trace_id=OuterRef("id"),
                                    custom_eval_config_id=config.id,
                                    error=True,
                                )
                            ),
                            then=Value(True),
                        ),
                        default=Value(False),
                        output_field=BooleanField(),
                    ),
                }
            )
        return eval_configs, base_query

    @validated_request(request_serializer=TraceTagsUpdateSerializer)
    @action(detail=True, methods=["patch"], url_path="tags")
    def update_tags(self, request, *args, **kwargs):
        """Update tags for a trace."""
        try:
            trace_id = kwargs.get("pk")
            trace = self.get_queryset().get(id=trace_id)
            tags = request.validated_data["tags"]
            trace.tags = tags
            trace.save(update_fields=["tags", "updated_at"])
            return self._gm.success_response({"id": str(trace.id), "tags": trace.tags})
        except Trace.DoesNotExist:
            return self._gm.bad_request("Trace not found")
        except Exception as e:
            logger.exception(f"Error updating trace tags: {e}")
            return self._gm.bad_request("Error updating tags")

    @action(detail=False, methods=["get"])
    def get_properties(self, request, *args, **kwargs):
        """
        Fetch all properties for graphing.
        """
        try:
            properties = [
                "Count",
                "Percentile Empty",
                "Average",
                "Sum",
                "Standard Deviation",
                "P50",
                "P75",
                "P95",
            ]

            return self._gm.success_response(properties)

        except Exception as exc:
            logger.exception("trace_properties_failed", error_type=type(exc).__name__)
            return self._gm.bad_request("Trace properties could not be loaded")

    @validated_request(
        responses={
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        }
    )
    @action(detail=False, methods=["get"])
    def get_eval_names(self, request, *args, **kwargs):
        """
        Fetch all evaluation template names.
        """
        project_id = None
        try:
            project_id = self.request.query_params.get("project_id", None)
            project = (
                _project_queryset_for_request(self.request)
                .filter(id=project_id)
                .first()
            )

            if not project_id or not project or project.trace_type != "observe":
                return self._gm.bad_request(
                    "Project id is required and project should be of type observe"
                )

            name = self.request.query_params.get("name", None)

            # ClickHouse dispatch: resolve which eval config IDs have data
            # Eval results live on CH25, while their physical table name is an
            # independent rollout choice. The V2 service keeps the CH25
            # connection and resolves the authoritative configured eval table.
            analytics = V2AnalyticsQueryService()
            # CH-only path. Legacy PG fallback removed: EvalLogger lives in
            # CH now and the PG `tracer_evallogger` table is destined for
            # deletion. If CH errors, propagate so the operator sees it.
            #
            # Resolve this project's configs from PG (project FK), then ask CH
            # which have EVER produced eval data via the candidate-id fast path.
            # window_days=None on purpose: the eval-name/metric picker must not
            # depend on 30-day recency — a historically-run eval must stay
            # listable. The custom_eval_config_id IN (…) scope hits the eval
            # table's leading sort key, so unbounded-in-time stays memory-safe
            # (no OOM) unlike the old trace-join discovery.
            project_config_ids = [
                str(cid)
                for cid in CustomEvalConfig.objects.filter(
                    project_id=project_id, deleted=False
                ).values_list("id", flat=True)
            ]
            eval_config_ids = (
                analytics.get_eval_config_ids_with_data_ch(
                    str(project_id),
                    candidate_config_ids=project_config_ids,
                    window_days=None,
                )
                if project_config_ids
                else []
            )

            # Config lookup always from PG (small config table)
            configs = (
                CustomEvalConfig.objects.filter(
                    id__in=eval_config_ids,
                    deleted=False,
                    eval_template__config__output__in=["score", "Pass/Fail", "choices"],
                )
                .select_related("eval_template")
                .values(
                    "name",
                    "id",
                    output_type=F("eval_template__config__output"),
                    choices=F("eval_template__choices"),
                )
                .distinct()
            )
            if name:
                configs = configs.filter(name__icontains=name)
                return self._gm.success_response(configs)

            return self._gm.success_response(configs)

        except Exception as exc:
            if is_clickhouse_api_read_unavailable_error(exc):
                logger.warning(
                    "evaluation_name_picker_query_unavailable",
                    project_id=str(project_id or ""),
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Evaluation names are temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "evaluation_name_picker_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Evaluation names could not be loaded",
                code="server_error",
            )

    @validated_request(
        query_serializer=TraceListQuerySerializer,
        responses={
            400: ApiErrorResponseSerializer,
            422: PageDepthExceededErrorSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def list_traces(self, request, *args, **kwargs):
        """
        List traces filtered by project ID and project version ID with optimized queries.
        """
        try:
            query_params = request.validated_query_data
            project_version_id = str(query_params["project_version_id"])
            # Tenant gate via PG (org/workspace-scoped ProjectVersion).
            project_version = (
                _project_version_queryset_for_request(request)
                .filter(id=project_version_id)
                .first()
            )
            if not project_version:
                return self._gm.bad_request("Project version not found")

            # CH-only path post-migration. D-027: the previous PG fallback
            # body (Trace.objects.filter + 6 ObservationSpan Subquery
            # annotations for node_type / trace_name / span_attributes /
            # start_time / status + per-config EvalLogger metric pivot +
            # build_annotation_subqueries + 4-stage filter combinator +
            # Python pivot) was deleted. CH path lives in
            # _list_traces_clickhouse via the direct-write CH25 builder. Bind
            # the matching V2 service explicitly: routing configuration must
            # never send authoritative telemetry reads to the legacy cluster.
            analytics = V2AnalyticsQueryService()
            return self._list_traces_clickhouse(
                request, project_version_id, analytics, query_params
            )

        except UnsupportedFilterShapeError:
            return self._gm.bad_request("Trace filter configuration is invalid")
        except Exception as exc:
            if is_read_budget_error(exc) or is_clickhouse_query_error(exc):
                logger.warning(
                    "trace_list_query_unavailable",
                    project_version_id=str(
                        getattr(request, "validated_query_data", {}).get(
                            "project_version_id", ""
                        )
                    ),
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Trace data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "trace_list_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Trace data could not be loaded")

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
        try:
            body = request.validated_data
            allow_sampled = request.validated_query_data["allow_sampled"]
            refresh = request.validated_query_data.get("refresh", False)
            project_id = str(body["project_id"])
            project = (
                _project_queryset_for_request(self.request)
                .filter(id=project_id)
                .first()
            )

            if not project_id or not project or project.trace_type != "observe":
                return self._gm.bad_request(
                    "Project id is required and project should be of type observe"
                )

            # Get parameters
            filters = bind_request_my_annotations_principal(
                request,
                body["filters"],
            )
            interval = body["interval"]
            req_data_config = body["req_data_config"]

            metric_type = req_data_config.get("type", None)
            if metric_type not in ["EVAL", "ANNOTATION", "SYSTEM_METRIC"]:
                return self._gm.bad_request("Filter property type is not valid")
            metric_id = req_data_config.get("id", "latency")
            # PostgreSQL remains authoritative for small config metadata and
            # authorization only. Telemetry still comes exclusively from CH25.
            if (
                metric_type == "EVAL"
                and not CustomEvalConfig.objects.filter(
                    id=metric_id,
                    project_id=project_id,
                    deleted=False,
                ).exists()
            ):
                return self._gm.bad_request(
                    "Evaluation config is not available for this project"
                )

            # CH-only path post-migration. D-027: the previous PG fallback
            # (root_span_qs / all_span_qs Subquery annotations over Trace
            # + per-config metric pivot + Score subqueries for annotations
            # + 4-stage filter combinator + dispatch into
            # get_eval_graph_data / get_annotation_graph_data /
            # get_system_metric_data with PG trace_ids_queryset) was
            # deleted. CH path lives in the three fetch_*_graph_ch helpers.
            # If neither TIME_SERIES (SYSTEM_METRIC) / EVAL_METRICS (EVAL)
            # / ANNOTATION_GRAPH (ANNOTATION) is routed to CH, that's a
            # config error — surface it as a 400.
            # Graph telemetry is direct-write CH25 data. Bind explicitly to the
            # pooled V2 client; the legacy CH connection and PostgreSQL span /
            # trace models are never a fallback for this endpoint.
            analytics = V2AnalyticsQueryService()
            try:
                if metric_type == "SYSTEM_METRIC":
                    graph = fetch_system_metric_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        metric_id=metric_id,
                        observe_type="trace",
                        refresh=refresh,
                    )
                elif metric_type == "EVAL":
                    graph = fetch_eval_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        req_data_config=req_data_config,
                        observe_type="trace",
                        refresh=refresh,
                    )
                else:
                    graph = fetch_annotation_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        req_data_config=req_data_config,
                        observe_type="trace",
                        refresh=refresh,
                    )
                graph = enforce_exact_graph_data_contract(graph)
                if not graph_payload_is_publishable(
                    graph,
                    allow_sampled=allow_sampled,
                ):
                    return self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Graph data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                return self._gm.success_response(graph)
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
                    "trace_graph_query_unavailable",
                    project_id=project_id,
                    metric_type=metric_type,
                    metric_id=metric_id,
                    error_type=type(exc).__name__,
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
                "trace_graph_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Graph data could not be loaded",
                code="server_error",
            )

    @action(detail=False, methods=["post"])
    def bulk_create(self, request, *args, **kwargs):
        try:
            traces_data = self.request.data.get("traces", [])
            for trace in traces_data:
                project = (
                    _project_queryset_for_request(request)
                    .filter(id=trace.get("project"))
                    .first()
                )
                if not project:
                    raise ValueError("Project not found")

                project_version = None
                project_version_id = trace.get("project_version")
                if project_version_id:
                    project_version = (
                        _project_version_queryset_for_request(request)
                        .filter(id=project_version_id)
                        .first()
                    )
                    if not project_version or project_version.project_id != project.id:
                        raise ValueError("Project version not found")

                session = None
                session_id = trace.get("session")
                if session_id:
                    session = (
                        _trace_session_queryset_for_request(request)
                        .filter(id=session_id)
                        .first()
                    )
                    if not session or session.project_id != project.id:
                        raise ValueError("Session not found")

                trace["project"] = project
                trace["project_version"] = project_version
                trace["session"] = session
            traces = [Trace(**trace) for trace in traces_data]
            added_traces = Trace.objects.bulk_create(traces)
            traceIds = [trace.id for trace in added_traces]

            return self._gm.success_response({"Trace IDs": traceIds})
        except Exception as e:
            logger.exception(f"Error in creating bulk trace: {str(e)}")
            return self._gm.bad_request(
                f"Error creating bulk traces: {get_error_message('ERROR_CREATING_TRACES')}"
            )

    @action(detail=False, methods=["post"])
    def compare_traces(self, request, *args, **kwargs):
        """
        Compare traces across project versions with optimized queries.
        """
        # CH25-TODO: this endpoint has no CH dispatch. It does:
        #   1. Trace.objects + per-trace ObservationSpan Subquery to
        #      derive node_type / avg_latency / avg_cost (the per-trace
        #      part could be lifted to reader.per_trace_aggregate / the
        #      per-trace rollup, but it's an ORM subquery, not a Python
        #      walk).
        #   2. EvalLogger pivot via .annotate(Round/Avg/Case/JSONObject)
        #      that produces per-config float/bool/str_list rows — pure
        #      PG EvalLogger reads.
        #   3. get_observation_spans() helper
        #      (observation_span.py:get_observation_spans), which is
        #      documented KEEP-PG: it walks the orphaned-span tree and
        #      constructs dummy parents, a schema-coupled pattern that
        #      CHSpanReader doesn't expose.
        # Migrating cleanly needs (a) a CH cross-version comparison
        # reader (eval pivots across project_versions in one query) and
        # (b) the orphaned-span tree builder lifted to CH. Until both
        # exist, or compare_traces is retired in favor of the per-trace
        # CH retrieve path, this stays PG.
        try:
            project_version_ids = self.request.data.get("project_version_ids", [])
            index = self.request.data.get("index", 0)

            if not project_version_ids:
                return self._gm.success_response(
                    {"trace_comparison": {}, "total_traces": 0, "index": 0}
                )

            # First verify all project versions are visible in this workspace.
            existing_versions = _project_version_queryset_for_request(request).filter(
                id__in=project_version_ids
            )
            existing_ids = {str(v.id) for v in existing_versions}
            requested_ids = [
                str(project_version_id) for project_version_id in project_version_ids
            ]
            if len(existing_ids) != len(requested_ids):
                missing_ids = set(requested_ids) - existing_ids
                return self._gm.success_response(
                    {
                        "trace_comparison": {},
                        "total_traces": 0,
                        "index": 0,
                        "message": f"Some project versions not found: {', '.join(missing_ids)}",
                    }
                )
            project_version_ids = requested_ids

            # Get all traces for the project versions in a single query
            traces = (
                Trace.objects.filter(project_version_id__in=project_version_ids)
                .select_related("project_version")
                .annotate(
                    node_type=Subquery(
                        ObservationSpan.objects.filter(
                            trace_id=OuterRef("id"), parent_span_id__isnull=True
                        ).values("observation_type")[:1]
                    ),
                    avg_latency=Subquery(
                        ObservationSpan.objects.filter(
                            trace_id=OuterRef("id"), parent_span_id__isnull=True
                        ).values("latency_ms")[:1]
                    ),
                    avg_cost=Subquery(
                        ObservationSpan.objects.filter(trace_id=OuterRef("id"))
                        .exclude(total_tokens__isnull=True)
                        .values("trace_id")
                        .annotate(avg=Avg("total_tokens"))
                        .values("avg")[:1]
                    ),
                )
            )

            # Group traces by input
            input_grouped_traces = {}
            for trace in traces:
                if str(trace.input) not in input_grouped_traces:
                    input_grouped_traces[str(trace.input)] = {}
                input_grouped_traces[str(trace.input)][
                    str(trace.project_version_id)
                ] = trace

            # Get eval metrics in a single query
            eval_metrics = (
                EvalLogger.objects.filter(
                    trace_id__in=Trace.objects.filter(
                        project_version_id__in=project_version_ids
                    ).values("id")
                )
                .values(
                    "trace_id",
                    "custom_eval_config_id",
                    "custom_eval_config__name",
                    "custom_eval_config__eval_template__choices",
                    "custom_eval_config__eval_template__config",
                )
                .annotate(
                    avg_float_score=Round(Avg("output_float") * 100, 2),
                    bool_pass_rate=Round(
                        Avg(
                            Case(
                                When(output_bool=True, then=100),
                                When(output_bool=False, then=0),
                                default=None,
                                output_field=models.FloatField(),
                            )
                        ),
                        2,
                    ),
                    str_list_values=ArrayAgg("output_str_list", distinct=True),
                    str_list_score=JSONObject(
                        **{
                            f"{value}": JSONObject(
                                score=Round(
                                    Avg(
                                        Case(
                                            When(
                                                output_str_list__contains=[value],
                                                then=100,
                                            ),
                                            default=0,
                                            output_field=FloatField(),
                                        )
                                    ),
                                    2,
                                )
                            )
                            for value in {
                                element
                                for sublist in EvalLogger.objects.filter(
                                    trace_id__in=Trace.objects.filter(
                                        project_version_id__in=project_version_ids
                                    ).values("id"),
                                    output_str_list__isnull=False,
                                )
                                .values_list("output_str_list", flat=True)
                                .distinct()
                                for element in sublist
                            }
                        }
                    ),
                    total_evaluations=models.Count("id"),
                    error_count=models.Count(
                        Case(
                            When(Q(output_str="ERROR") | Q(error=True), then=1),
                            output_field=models.IntegerField(),
                        )
                    ),
                )
            )

            total_eval_configs = {}
            # Convert eval metrics to nested dictionary
            eval_metrics_by_trace: dict[Any, Any] = {}
            for metric in eval_metrics:
                trace_id = str(metric["trace_id"])
                if trace_id not in eval_metrics_by_trace:
                    eval_metrics_by_trace[trace_id] = {}

                choices = (
                    metric["custom_eval_config__eval_template__choices"]
                    if metric["custom_eval_config__eval_template__choices"]
                    else None
                )
                eval_template_output_type = (
                    metric["custom_eval_config__eval_template__config"].get(
                        "output", "score"
                    )
                    if metric["custom_eval_config__eval_template__config"]
                    else "score"
                )

                if (
                    choices
                    and eval_template_output_type == EvalOutputType.CHOICES.value
                ):
                    for choice in choices:
                        if choice in metric["str_list_score"]:
                            score = metric["str_list_score"][choice]["score"]
                            eval_metrics_by_trace[trace_id][
                                str(metric["custom_eval_config_id"]) + "**" + choice
                            ] = {
                                "score": score,
                                "name": metric["custom_eval_config__name"]
                                + " - "
                                + choice,
                            }
                            if (
                                str(metric["custom_eval_config_id"]) + "**" + choice
                                not in total_eval_configs
                            ):
                                total_eval_configs[
                                    str(metric["custom_eval_config_id"]) + "**" + choice
                                ] = metric["custom_eval_config__name"] + " - " + choice
                else:
                    score = (
                        metric["avg_float_score"]
                        if metric["avg_float_score"] is not None
                        else metric["bool_pass_rate"]
                    )
                    eval_metrics_by_trace[trace_id][
                        str(metric["custom_eval_config_id"])
                    ] = {"score": score, "name": metric["custom_eval_config__name"]}
                    if str(metric["custom_eval_config_id"]) not in total_eval_configs:
                        total_eval_configs[str(metric["custom_eval_config_id"])] = (
                            metric["custom_eval_config__name"]
                        )

            # Create trace comparisons
            trace_comparisons = []
            for _input_value, traces_by_version in input_grouped_traces.items():
                # Only include inputs that have traces for all requested project versions
                if all(
                    str(version_id) in traces_by_version
                    for version_id in project_version_ids
                ):
                    comparison_obj = {}
                    for project_version_id in project_version_ids:
                        trace = traces_by_version[str(project_version_id)]
                        trace_data = TraceSerializer(trace).data

                        # Add project version name
                        trace_data["project_version_name"] = trace.project_version.name

                        # Add eval metrics
                        trace_data["evals_metrics"] = eval_metrics_by_trace.get(
                            str(trace.id), {}
                        )

                        # Add system metrics
                        trace_data["system_metrics"] = {
                            "avg_latency_ms": trace.avg_latency or 0,
                            "avg_cost": trace.avg_cost or 0,
                        }

                        # Add node type
                        trace_data["node_type"] = trace.node_type or "chain"

                        # Add observation spans
                        trace_data["observation_spans"] = get_observation_spans(
                            {
                                "project_id": trace_data["project"],
                                "project_version_id": trace_data["project_version"],
                                "trace_id": trace_data["id"],
                            }
                        )

                        comparison_obj[str(project_version_id)] = trace_data

                    trace_comparisons.append(comparison_obj)

            if len(trace_comparisons) <= index:
                index = 0

            response = {
                "trace_comparison": (
                    {} if len(trace_comparisons) == 0 else trace_comparisons[index]
                ),
                "total_traces": len(trace_comparisons),
                "index": index,
                "total_eval_configs": total_eval_configs,
            }

            return self._gm.success_response(response)

        except Exception as e:
            logger.exception(f"Error in comparing the traces: {str(e)}")

            return self._gm.bad_request(
                f"Error comparing traces: {get_error_message('ERROR_COMPARING_TRACES')}"
            )

    @validated_request(query_serializer=TraceIndexQuerySerializer)
    @action(detail=False, methods=["get"])
    def get_trace_id_by_index(self, request, *args, **kwargs):
        """
        Get the previous and next trace id by index using efficient database queries.
        """
        # CH25-TODO: PG-only prev/next navigation for experiment traces
        # (project_version-scoped). Needs the same eval/annotation
        # filter pivot the CH TraceListQueryBuilder produces plus a
        # "by-start_time prev/next" step.
        #
        # Wave-3 partial coverage (commit 93c5c415f): the reader exposes
        # `prev_next_trace_by_start_time(*, project_id, trace_id,
        # project_version_id=None)` which does an unfiltered walk and
        # returns (prev_trace_id, next_trace_id) — the correct return
        # shape. It does NOT accept the eval/annotation/span-attribute
        # filters this endpoint applies (FilterEngine pivots +
        # _build_annotation_subqueries) before walking. The frontend
        # always sends `filters` (verified in
        # components/traceDetailDrawer/trace-detail-drawer.jsx) so a
        # drop-in swap would silently change the navigation set under
        # any non-empty filter. Staying PG-only.
        #
        # Reader-gap proposal:
        #   prev_next_trace_by_start_time_with_filters(*, project_id,
        #       trace_id, project_version_id=None, filters=None)
        #       -> tuple[Optional[str], Optional[str]]
        # where `filters` accepts the TraceListQueryBuilder filter
        # shape (system metrics + eval pivots + annotation joins + span
        # attributes). On filters=None / [] it would degrade to the
        # existing `prev_next_trace_by_start_time`.
        try:
            query = request.validated_query_data
            trace_id = str(query["trace_id"])
            project_version_id = str(query["project_version_id"])
            project_version = (
                _project_version_queryset_for_request(request)
                .filter(id=project_version_id)
                .first()
            )
            if not project_version:
                raise Exception("Project version not found")  # noqa: B904

            # Base query with annotations
            base_query = Trace.objects.filter(
                project=project_version.project,
                project_version=project_version,
            ).annotate(
                node_type=Subquery(
                    ObservationSpan.objects.filter(trace_id=OuterRef("id")).values(
                        "observation_type"
                    )[:1]
                ),
                trace_id=F("id"),
                trace_name=Subquery(
                    ObservationSpan.objects.filter(
                        trace_id=OuterRef("id"), parent_span_id__isnull=True
                    ).values("name")[:1]
                ),
                # Fetch span_attributes from root span (fallback to eval_attributes for old data)
                span_attributes=Subquery(
                    ObservationSpan.objects.filter(
                        trace_id=OuterRef("id"), parent_span_id__isnull=True
                    )
                    .annotate(_attrs=Coalesce("span_attributes", "eval_attributes"))
                    .values("_attrs")[:1]
                ),
                start_time=Coalesce(
                    Subquery(
                        ObservationSpan.objects.filter(
                            trace_id=OuterRef("id"), parent_span_id__isnull=True
                        )
                        .order_by("start_time")
                        .values("start_time")[:1]
                    ),
                    "created_at",
                ),
            )

            # Get all eval configs from the project version
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    trace_id__in=Trace.objects.filter(
                        project_version_id=project_version_id
                    ).values("id")
                )
                .values("custom_eval_config_id")
                .distinct(),
                deleted=False,
            ).select_related("eval_template")

            # Add annotations for each eval metric dynamically
            for config in eval_configs:
                choices = (
                    config.eval_template.choices
                    if config.eval_template.choices
                    else None
                )

                metric_subquery = (
                    EvalLogger.objects.filter(
                        trace_id=OuterRef("id"), custom_eval_config_id=config.id
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
                                        trace_id=OuterRef("id"),
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
                                        trace_id=OuterRef("id"),
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
                                        trace_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_str_list__isnull=False,
                                    )
                                ),
                                then=Subquery(metric_subquery.values("str_list_score")),
                            ),
                            default=JSONObject(
                                score=Value(0.0, output_field=FloatField())
                            ),
                            output_field=JSONField(),
                        )
                    }
                )
            # Add Root Span Annotations
            annotation_labels = get_annotation_labels_for_project(
                project_version.project.id
            )
            base_query = self._build_annotation_subqueries(
                base_query, annotation_labels, request.user.organization
            )

            # Apply filters from request
            filters = query["filters"]
            if filters:
                system_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filters)
                )
                if system_filter_conditions:
                    base_query = base_query.filter(system_filter_conditions)

                # Separate annotation filters from eval filters
                def _get_col_type(f):
                    fc = f.get("filter_config", {})
                    return fc.get("col_type", f.get("col_type", ""))

                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filters
                    if _get_col_type(f) not in annotation_col_types
                    and f.get("column_id") not in annotation_column_ids
                ]

                eval_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_non_system_metrics(
                        non_annotation_filters
                    )
                )
                if eval_filter_conditions:
                    base_query = base_query.filter(eval_filter_conditions)

                # Apply annotation filters (score, annotator, my_annotations)
                annotation_filter_conditions, extra_annotations = (
                    FilterEngine.get_filter_conditions_for_voice_call_annotations(
                        filters, user_id=request.user.id
                    )
                )
                if extra_annotations:
                    base_query = base_query.annotate(**extra_annotations)
                if annotation_filter_conditions:
                    base_query = base_query.filter(annotation_filter_conditions)

                # Get span attribute filters
                span_attribute_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_conditions:
                    base_query = base_query.filter(span_attribute_conditions)

            base_query = base_query.order_by("-start_time", "-id")

            current_trace = base_query.filter(id=trace_id).values("start_time").first()
            if not current_trace:
                raise Exception("Trace not found in the list")

            previous_trace = None
            next_trace = None

            if current_trace["start_time"] is not None:
                previous_trace = (
                    base_query.filter(start_time__lt=current_trace["start_time"])
                    .order_by("-start_time")
                    .values_list("id", flat=True)
                    .first()
                )

                next_trace = (
                    base_query.filter(start_time__gt=current_trace["start_time"])
                    .order_by("start_time")
                    .values_list("id", flat=True)
                    .first()
                )

            response = {
                "next_trace_id": str(previous_trace) if previous_trace else None,
                "previous_trace_id": str(next_trace) if next_trace else None,
            }

            return self._gm.success_response(response)

        except Exception as exc:
            logger.exception("trace_navigation_failed", error_type=type(exc).__name__)
            return self._gm.bad_request("Trace navigation could not be loaded")

    @validated_request(
        query_serializer=TraceObserveListQuerySerializer,
        responses={
            200: TraceObserveListResponseSerializer,
            **ERROR_RESPONSES,
            422: PageDepthExceededErrorSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def list_traces_of_session(self, request, *args, **kwargs):
        """
        List traces filtered by project ID with optimized queries.
        """
        try:
            export = kwargs.get("export", False) if kwargs else False
            # CH-only path doesn't honor export=True (no unbounded-walk
            # surface in TraceListQueryBuilder yet). Fail loud rather
            # than serve a silently truncated CSV. Tracked as a
            # follow-up: move the export to a Temporal job that streams
            # unbounded rows from CH.
            if export:
                return self._gm.bad_request(
                    "Non-voice trace export beyond the first page is not "
                    "supported by the CH-only path post-migration. The "
                    "legacy PG export skipped pagination; the CH path "
                    "always paginates. Follow-up: a Temporal-driven "
                    "unbounded-walk export against CH."
                )

            validated_data = dict(request.validated_query_data)
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

            # Org-scoped mode: when no project_id is supplied the caller wants
            # traces from every project in the org (e.g. the cross-project
            # user detail page at /dashboard/users/:userId).
            org_scope = not project_id
            if org_scope:
                org_project_ids = list(
                    _project_queryset_for_request(request)
                    .filter(
                        trace_type__in=("observe", "experiment"),
                    )
                    .values_list("id", flat=True)
                )
            else:
                project = (
                    _project_queryset_for_request(request).filter(id=project_id).first()
                )
                if not project or project.trace_type not in ("observe", "experiment"):
                    return self._gm.bad_request(
                        "Project should be of type observe or experiment"
                    )
                org_project_ids = None

            # CH-only path post-migration. D-027: the previous PG fallback
            # body (Trace.objects + _root_span_qs / _all_span_qs /
            # _end_user_span_qs Subquery annotations + per-config EvalLogger
            # metric pivot + build_annotation_subqueries + 4-stage filter
            # combinator + Python pivot) was deleted. CH path lives in
            # _list_traces_of_session_clickhouse via the direct-write CH25
            # builder. Routing flags are intentionally not consulted for this
            # authoritative telemetry path. (NOTE: the legacy PG path supported
            # export=True by skipping pagination; the CH path always
            # paginates. Export of traces-of-session beyond the first page
            # is unsupported post-migration — feature parity tracked as a
            # follow-up if needed.)
            analytics = V2AnalyticsQueryService()
            return self._list_traces_of_session_clickhouse(
                request,
                project_id,
                validated_data,
                analytics,
                org_project_ids=org_project_ids,
                org=org,
            )

        except ListCursorError as exc:
            return self._gm.custom_error_response(
                status.HTTP_400_BAD_REQUEST, str(exc), code=exc.code
            )
        except UnsupportedFilterShapeError:
            return self._gm.bad_request("Trace filter configuration is invalid")
        except FilterPrincipalContextError as exc:
            return self._gm.bad_request(str(exc))
        except Exception as exc:
            if is_clickhouse_api_read_unavailable_error(exc):
                logger.warning(
                    "observe_trace_list_query_unavailable",
                    project_id=str(
                        getattr(request, "validated_query_data", {}).get(
                            "project_id", ""
                        )
                    ),
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Trace data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "observe_trace_list_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Trace data could not be loaded",
                code="server_error",
            )

    @validated_request(
        query_serializer=TraceVoiceCallListQuerySerializer,
        responses={
            200: TraceVoiceCallListResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
            422: PageDepthExceededErrorSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"], pagination_class=None)
    def list_voice_calls(self, request, *args, **kwargs):
        """
        List voice/conversation traces for a project in an optimized way and
        return a response similar to the provided call object schema.

        Query params:
        - project_id (required)
        - page (1-based, optional, default 1)
        - page_size (optional, default 30)
        """
        project_id = ""
        try:
            validated_data = getattr(request, "validated_query_data", None)
            if not validated_data:
                # Direct unit calls unwrap ``validated_request``. Keep those
                # calls validated by the same serializer without double-validating
                # normal HTTP requests.
                serializer = TraceVoiceCallListQuerySerializer(
                    data=request.query_params
                )
                if not serializer.is_valid():
                    return self._gm.bad_request(serializer.errors)
                validated_data = serializer.validated_data
            validated_data = dict(validated_data)
            validated_data["filters"] = bind_request_my_annotations_principal(
                request,
                validated_data.get("filters", []),
            )
            project_id = str(validated_data["project_id"])
            remove_simulation_calls = validated_data.get(
                "remove_simulation_calls", False
            )

            # Validate project exists
            Project.objects.get(
                id=project_id,
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )

            # CH-only path post-migration. D-027: the previous PG fallback
            # body (Trace.objects + has_conversation_root Exists +
            # span_attributes Subquery from root_span_qs + per-config
            # EvalLogger metric pivot via self.get_eval_configs +
            # build_annotation_subqueries + 5-stage filter combinator +
            # ExtendedPageNumberPagination + populate_call_logs_result on
            # the PG queryset) was deleted. CH path lives in
            # _list_voice_calls_clickhouse via VoiceCallListQueryBuilder.
            # Per-query routing gate was removed in the CH25 close-out — CH
            # is the single source of truth; CH failures propagate.
            analytics = V2AnalyticsQueryService()
            return self._list_voice_calls_clickhouse(
                request,
                project_id,
                validated_data,
                remove_simulation_calls,
                analytics,
            )

        except ListCursorError as exc:
            return self._gm.custom_error_response(
                status.HTTP_400_BAD_REQUEST, str(exc), code=exc.code
            )
        except NotFound:
            raise
        except Project.DoesNotExist:
            return self._gm.bad_request("Project not found")
        except FilterPrincipalContextError as exc:
            return self._gm.bad_request(str(exc))
        except Exception as exc:
            if is_read_budget_error(exc) or is_clickhouse_query_error(exc):
                logger.warning(
                    "voice_call_list_query_unavailable",
                    project_id=str(project_id),
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Voice call data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "voice_call_list_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.bad_request("Voice call data could not be loaded")

    # ------------------------------------------------------------------
    # Voice call detail — returns heavy fields for a single call
    # ------------------------------------------------------------------

    # Observation type → system metric key mapping for latency aggregation
    _SPAN_TYPE_TO_METRIC = {
        "stt": "transcriber",
        "llm": "model",
        "tts": "voice",
    }

    def _compute_voice_system_metrics(self, spans) -> dict:
        """Aggregate child span latencies into system metrics by observation type."""
        metrics = {}
        for span in spans:
            metric_key = self._SPAN_TYPE_TO_METRIC.get(span.observation_type)
            if metric_key and span.latency_ms:
                metrics[metric_key] = metrics.get(metric_key, 0) + span.latency_ms
        if not metrics:
            return {}
        return {"system_metrics": metrics}

    def _compute_voice_system_metrics_from_ch(self, child_rows: list) -> dict:
        """Aggregate child span latencies from ClickHouse rows."""
        metrics = {}
        for child in child_rows:
            metric_key = self._SPAN_TYPE_TO_METRIC.get(child.get("observation_type"))
            latency = child.get("latency_ms")
            if metric_key and latency:
                metrics[metric_key] = metrics.get(metric_key, 0) + latency
        if not metrics:
            return {}
        return {"system_metrics": metrics}

    @action(detail=False, methods=["get"])
    def voice_call_detail(self, request, *args, **kwargs):
        """
        Return the heavy / detail-only fields for a single voice call.

        Query params:
        - trace_id (required) — UUID of the voice call trace.
        """
        try:
            trace_id = request.query_params.get("trace_id") or request.query_params.get(
                "traceId"
            )
            if not trace_id:
                return self._gm.bad_request("trace_id is required")

            # Scope the ClickHouse identity read up front.  The exact reader
            # resolves latest span versions/tombstones inside only these
            # authorized projects, so a colliding public trace id cannot select
            # another tenant via an arbitrary LIMIT 1.
            project_ids = [
                str(project_id)
                for project_id in _project_queryset_for_request(request)
                .values_list("id", flat=True)
                .order_by("id")[:4097]
            ]
            eval_configs_by_project: dict[str, list[CustomEvalConfig]] = {}

            def resolve_eval_config_ids(selected_project_id: str) -> list[str]:
                eval_configs, eval_config_ids = get_project_eval_configs(
                    selected_project_id
                )
                eval_configs_by_project[str(selected_project_id)] = eval_configs
                return eval_config_ids

            detail = read_trace_detail(
                analytics=V2AnalyticsQueryService(),
                project_ids=project_ids,
                trace_id=str(trace_id),
                eval_config_ids_resolver=resolve_eval_config_ids,
                # Voice detail renders call/provider fields and eval outputs;
                # it never consumes annotation rows.  Avoid coupling this
                # endpoint to the legacy score table (and spending an extra
                # query) when annotations cannot affect the response.
                include_annotations=False,
                deadline_ms=6000,
            )
            eval_configs = eval_configs_by_project.get(str(detail.project_id), [])
            return self._voice_call_detail_clickhouse(
                request,
                trace_id,
                detail,
                eval_configs,
            )
        except TraceDetailNotFound:
            return self._gm.not_found("trace_id not found")
        except TraceDetailReadUnavailable as e:
            logger.warning(
                "voice_call_detail_bounded_read_incomplete",
                trace_id=str(request.query_params.get("trace_id") or ""),
                error_code=e.code,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Voice call details are temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as e:
            logger.exception("voice_call_detail_error", error=str(e))
            return self._gm.bad_request("Voice call details could not be loaded")

    def _voice_call_detail_clickhouse(self, request, trace_id, detail, eval_configs):
        """Return heavy voice-call detail fields from ClickHouse."""
        project_id = detail.project_id
        root_rows = [
            span
            for span in detail.spans
            if span.get("parent_span_id") in (None, "")
            and str(span.get("observation_type") or "").lower() == "conversation"
        ]
        if not root_rows:
            return self._gm.not_found("No conversation root span found in CH")

        # ``read_trace_detail`` returns exact rows ordered by physical start
        # identity. Voice traces normally have one conversation root; choosing
        # by time/id keeps malformed multi-root data deterministic.
        row = min(
            root_rows,
            key=lambda span: (span.get("start_time"), str(span.get("id") or "")),
        )
        child_rows = [
            span
            for span in detail.spans
            if span.get("parent_span_id") not in (None, "")
        ]
        provider = row.get("provider") or "vapi"

        # Parse attributes_extra to get raw_log
        span_attrs_raw = row.get("span_attributes", "{}")
        try:
            span_attrs = (
                json.loads(span_attrs_raw)
                if isinstance(span_attrs_raw, str)
                else (span_attrs_raw or {})
            )
        except (json.JSONDecodeError, TypeError):
            span_attrs = {}
        if not isinstance(span_attrs, dict):
            span_attrs = {}
        # Union typed Maps: voice spans keep call.* scalars in attrs_string/number while
        # input/output.value overflow into attributes_extra; reading it alone drops call.* metrics.
        for k, v in (row.get("attrs_string") or {}).items():
            span_attrs.setdefault(k, v)
        for k, v in (row.get("attrs_number") or {}).items():
            span_attrs.setdefault(k, v)
        for k, v in (row.get("attrs_bool") or {}).items():
            span_attrs.setdefault(k, bool(v))
        # eval_attributes is not a top-level column on the CH `spans` table,
        # but the adapter merges it into `attributes_extra` under the key
        # "eval_attributes". Extract it so simulation_context can resolve
        # fi.simulator.call_execution_id and similar keys.
        eval_attrs = span_attrs.get("eval_attributes", {}) or {}

        raw_log = self._coerce_raw_log(span_attrs.get("raw_log"))
        metadata_raw = row.get("metadata_json") or "{}"
        try:
            metadata = (
                json.loads(metadata_raw)
                if isinstance(metadata_raw, str)
                else (metadata_raw or {})
            )
        except (json.JSONDecodeError, TypeError):
            metadata = {}

        processed_log = ObservabilityService.process_raw_logs(
            raw_log, provider, span_attributes=span_attrs
        )
        # Collector-routed pulls carry no raw_log (OTLP); span start_time is the call start.
        if not raw_log and not processed_log.get("started_at"):
            _st = row.get("start_time")
            if _st:
                processed_log["started_at"] = (
                    _st.isoformat() if hasattr(_st, "isoformat") else str(_st)
                )
        simulation_context = _simulation_context_for_voice_call(
            organization_id=getattr(_get_request_organization(request), "id", None),
            span_attributes=span_attrs,
            eval_attributes=eval_attrs,
            raw_log=raw_log,
            metadata=metadata,
            processed_log=processed_log,
        )
        voice_metrics = self._extract_voice_turn_and_talk_metrics(span_attrs, raw_log)

        attr_str = row.get("attrs_string") or {}
        recording = self._build_recording_dict(attr_str)

        # Build observation_span array — root span first
        root_span_id = str(row.get("span_id", row.get("id", "")))
        observation_span = [
            {
                "id": root_span_id,
                "trace_id": str(trace_id),
                "name": "conversation",
                "observation_type": "conversation",
                "status": row.get("status"),
                "start_time": (
                    str(row.get("start_time", "")) if row.get("start_time") else None
                ),
                "end_time": (
                    str(row.get("end_time", "")) if row.get("end_time") else None
                ),
                "latency_ms": row.get("latency_ms"),
                "provider": provider,
                "span_attributes": span_attrs,
                "metadata": metadata,
            }
        ]

        for child in child_rows:
            child_attrs_raw = child.get("span_attributes", "{}")
            try:
                child_span_attrs = (
                    json.loads(child_attrs_raw)
                    if isinstance(child_attrs_raw, str)
                    else (child_attrs_raw or {})
                )
            except (json.JSONDecodeError, TypeError):
                child_span_attrs = {}

            child_attr_str = child.get("attrs_string") or {}
            child_attr_num = child.get("attrs_number") or {}
            child_attr_bool = child.get("attrs_bool") or {}
            for k, v in child_attr_str.items():
                child_span_attrs.setdefault(k, v)
            for k, v in child_attr_num.items():
                child_span_attrs.setdefault(k, v)
            for k, v in child_attr_bool.items():
                child_span_attrs.setdefault(k, v)

            observation_span.append(
                {
                    "id": str(child.get("id", "")),
                    "trace_id": str(trace_id),
                    "name": child.get("name", ""),
                    "observation_type": child.get("observation_type", ""),
                    "status": child.get("status"),
                    "status_message": child.get("status_message"),
                    "start_time": (
                        str(child.get("start_time", ""))
                        if child.get("start_time")
                        else None
                    ),
                    "end_time": (
                        str(child.get("end_time", ""))
                        if child.get("end_time")
                        else None
                    ),
                    "latency_ms": child.get("latency_ms"),
                    "model": child.get("model"),
                    "provider": child.get("provider"),
                    "prompt_tokens": child.get("prompt_tokens"),
                    "completion_tokens": child.get("completion_tokens"),
                    "total_tokens": child.get("total_tokens"),
                    "cost": child.get("cost"),
                    "input": child.get("input", ""),
                    "output": child.get("output", ""),
                    "parent_span_id": (
                        str(child.get("parent_span_id", ""))
                        if child.get("parent_span_id")
                        else None
                    ),
                    "span_attributes": child_span_attrs,
                    "metadata": _safe_parse_metadata(child.get("metadata_json")),
                    "tags": child.get("tags") or [],
                }
            )

        # Collector-routed pulls drop raw_log (OTLP); recover the transcript from
        # attrs_string (stored as a JSON string, not in attributes_extra).
        if not processed_log.get("transcript"):
            stored = attr_str.get("fi.conversation.transcript") or span_attrs.get(
                "fi.conversation.transcript"
            )
            if isinstance(stored, str):
                try:
                    stored = json.loads(stored)
                except (json.JSONDecodeError, TypeError):
                    stored = None
            if isinstance(stored, list) and stored:
                processed_log["transcript"] = stored
                processed_log["transcript_available"] = True
                if not processed_log.get("message_count"):
                    processed_log["message_count"] = len(stored)

        eval_outputs = {}
        trace_evals = _pivot_voice_detail_eval_rows(detail.evals)

        for config in eval_configs:
            config_id = str(config.id)
            metric_name = getattr(config, "name", None) or (
                getattr(config, "eval_template", None).name
                if getattr(config, "eval_template", None)
                else None
            )
            eval_template_config = (
                config.eval_template.config
                if getattr(config, "eval_template", None)
                else {}
            ) or {}
            output_type = eval_template_config.get("output", "score")

            if config_id not in trace_evals:
                eval_outputs[config_id] = {
                    "name": metric_name,
                    "output_type": output_type,
                    "output": None,
                    "reason": None,
                    "error": None,
                }
                continue

            scores = trace_evals[config_id]
            metric_entry = {"name": metric_name, "output_type": output_type}
            # All rows errored — surface the error state to the frontend.
            if isinstance(scores, dict) and scores.get("error"):
                metric_entry["error"] = True
                eval_outputs[config_id] = metric_entry
                continue
            # Non-terminal / skipped eval — surface the lifecycle status so the
            # detail drawer renders a loading / pending / skipped state.
            if isinstance(scores, dict) and isinstance(scores.get("status"), str):
                metric_entry["status"] = scores["status"]
                if scores.get("skipped_reason"):
                    metric_entry["skipped_reason"] = scores["skipped_reason"]
                eval_outputs[config_id] = metric_entry
                continue
            if isinstance(scores, dict):
                if scores.get("per_choice"):
                    metric_entry["output"] = [
                        k for k, v in scores["per_choice"].items() if v > 0
                    ]
                elif "str_list" in scores and scores["str_list"]:
                    metric_entry["output"] = scores["str_list"]
                elif "avg_score" in scores or "pass_rate" in scores:
                    score_val = (
                        scores.get("pass_rate")
                        if output_type == EvalOutputType.PASS_FAIL.value
                        else scores.get("avg_score")
                    )
                    if output_type == "Pass/Fail":
                        metric_entry["output"] = (
                            "Pass"
                            if isinstance(score_val, (int, float)) and score_val > 0
                            else "Fail"
                        )
                    else:
                        # The bounded reducer uses the list endpoint's
                        # pre-scaled 0..100 score contract.
                        metric_entry["output"] = score_val
                else:
                    metric_entry["output"] = None
            else:
                metric_entry["output"] = scores
            eval_outputs[config_id] = metric_entry

        # Duration from span attributes
        attrs_num = row.get("attrs_number") or {}
        stored_duration = attrs_num.get(CallAttributes.DURATION)

        # See PG path for rationale — do not set customer_latency_metrics /
        # customer_cost_breakdown; they flow in via the list merge or fall
        # back to raw_log provider metrics on the frontend.
        result = {
            **processed_log,
            **simulation_context,
            "id": str(trace_id),
            "trace_id": str(trace_id),
            "project_id": str(project_id),
            "provider_call_id": processed_log.get("call_id"),
            "recording": recording,
            "recording_available": self._recording_available(recording),
            "call_metadata": metadata,
            "observation_span": observation_span,
            "eval_outputs": eval_outputs,
            "turn_count": voice_metrics.get("turn_count"),
            "talk_ratio": voice_metrics.get("talk_ratio"),
            "agent_talk_percentage": voice_metrics.get("agent_talk_percentage"),
            "bot_talk_pct": voice_metrics.get("bot_talk_pct"),
            "user_talk_pct": voice_metrics.get("user_talk_pct"),
            "avg_agent_latency_ms": self._round_metric(
                span_attrs.get("avg_agent_latency_ms")
            ),
            "user_wpm": self._round_metric(span_attrs.get(CallAttributes.USER_WPM)),
            "bot_wpm": self._round_metric(span_attrs.get(CallAttributes.BOT_WPM)),
            "user_interruption_count": self._round_metric(
                span_attrs.get("user_interruption_count")
            ),
            "ai_interruption_count": self._round_metric(
                span_attrs.get("ai_interruption_count")
            ),
        }
        if stored_duration is not None:
            result["duration_seconds"] = int(stored_duration)
        return self._gm.success_response(result)

    def _get_trace_id_by_index_observe_clickhouse(
        self, request, trace_id, project_id, filters, analytics
    ):
        """Return exact adjacent ids from the same bounded list order."""
        from tracer.selectors.trace_filter_reads import read_bounded_filter_neighbors
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        builder = TraceListQueryBuilderV2(
            project_id=str(project_id),
            page_number=0,
            page_size=TRACE_NAVIGATION_CANDIDATE_LIMIT,
            filters=list(filters or []),
            bounded_internal_scan=True,
            bounded_identity_only=True,
            bounded_bulk_scan=True,
            # Navigation consumes only membership and canonical root order.
            # Omitting physical filter witnesses raises the exact classifier's
            # safe batch from 20 to 100 without changing filter membership.
            bounded_include_filter_witnesses=False,
        )
        error_code = builder.bounded_filter_degraded_error_code()
        if error_code or not builder.supports_bounded_filter_scan():
            raise TraceNavigationReadUnavailable(
                error_code or "unsupported_filter_shape"
            )
        neighbors = read_bounded_filter_neighbors(
            builder=builder,
            analytics=analytics,
            filters=list(filters or []),
            key_field="trace_id",
            target_id=str(trace_id),
            scan_limit=TRACE_NAVIGATION_CANDIDATE_LIMIT,
            page_size=TRACE_NAVIGATION_SCAN_PAGE_SIZE,
            deadline_ms=TRACE_NAVIGATION_WALL_DEADLINE_MS,
            max_query_count=TRACE_NAVIGATION_MAX_QUERIES,
        )
        if not neighbors.complete or neighbors.current is None:
            code = neighbors.error_code or "read_incomplete"
            if code == "target_not_found":
                code = "trace_not_in_list"
            raise TraceNavigationReadUnavailable(code)

        newer_trace = str(neighbors.newer.get("trace_id")) if neighbors.newer else None
        older_trace = str(neighbors.older.get("trace_id")) if neighbors.older else None

        response = {
            "next_trace_id": str(older_trace) if older_trace else None,
            "previous_trace_id": str(newer_trace) if newer_trace else None,
        }
        return self._gm.success_response(response)

    @validated_request(query_serializer=TraceObserveIndexQuerySerializer)
    @action(detail=False, methods=["get"])
    def get_trace_id_by_index_observe(self, request, *args, **kwargs):
        """
        Get the previous and next trace id by index.
        """
        try:
            query = request.validated_query_data
            trace_id = str(query["trace_id"])
            project_id = str(query["project_id"])

            project = (
                _project_queryset_for_request(request).filter(id=project_id).first()
            )
            if not project or project.trace_type != "observe":
                raise Exception("Project should be of type observe")

            filters = query["filters"]

            # CH-only path post-migration. D-027: the previous PG fallback
            # body (Trace.objects + 4 ObservationSpan Subquery annotations
            # for node_type / trace_name / user_id / span_attributes +
            # per-config EvalLogger metric pivot + build_annotation_subqueries
            # + 4-stage filter combinator + by-start_time prev/next pick)
            # was deleted. CH path lives in
            # _get_trace_id_by_index_observe_clickhouse and uses the spans
            # table directly with cursor-style start_time comparisons.
            analytics = V2AnalyticsQueryService()
            return self._get_trace_id_by_index_observe_clickhouse(
                request, trace_id, project_id, filters, analytics
            )

        except TraceNavigationReadUnavailable as exc:
            # A completed bounded read that does not contain the requested
            # trace is an exact not-found result, not an availability failure.
            # Keep genuine incomplete/budget outcomes retryable below.
            if exc.code == "trace_not_in_list":
                return self._gm.bad_request("Trace not found")
            logger.warning(
                "trace_navigation_bounded_read_incomplete",
                project_id=str(request.validated_query_data.get("project_id") or ""),
                error_code=exc.code,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace navigation is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as exc:
            logger.exception("trace_navigation_failed", error_type=type(exc).__name__)
            if is_read_budget_error(exc) or is_clickhouse_query_error(exc):
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Trace navigation is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            return self._gm.bad_request("Trace navigation could not be loaded")

    @action(detail=False, methods=["get"])
    def get_trace_export_data(self, request, *args, **kwargs):
        """
        Export traces filtered by project ID with optimized queries.
        Auto-detects voice/conversation projects and exports voice-specific fields.
        """
        try:
            serializer = TraceExportQuerySerializer(data=request.query_params)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            validated_data = serializer.validated_data
            project_id = str(validated_data["project_id"])

            project = (
                _project_queryset_for_request(request).filter(id=project_id).first()
            )
            if not project:
                return self._gm.bad_request("Project not found")

            # Check if project has voice/conversation traces.
            # Wave-3 (commit 93c5c415f) added the exact reader the prior
            # CH25-TODO requested: `has_root_spans_of_type(project_id,
            # observation_type)` ANDs is_deleted=0 + parent_span_id='' +
            # observation_type on the CH side, returning a bool from a
            # SELECT … LIMIT 1. Tenant scope is preserved by the
            # workspace-scoped `_project_queryset_for_request` check
            # above; the reader call is project-scoped.
            from tracer.services.clickhouse.v2 import get_reader

            with get_reader() as reader:
                has_voice_traces = reader.has_root_spans_of_type(
                    str(project_id), "conversation"
                )

            if has_voice_traces:
                return self._export_voice_calls(request, project, project_id)

            # Regular observe export path
            response = self.list_traces_of_session(request, export=True)

            if response.status_code != 200:
                return response

            result = response.data.get("result")
            table_data = result.get("table", None)

            df = pd.DataFrame(table_data)

            # Convert to CSV buffer
            buffer = io.BytesIO()
            df.to_csv(buffer, index=False, encoding="utf-8")
            buffer.seek(0)

            # Create the response with the file
            filename = f"{project.name or 'project'}_traces.csv"
            response = FileResponse(
                buffer, as_attachment=True, filename=filename, content_type="text/csv"
            )

            return response

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in fetching the traces list of observe: {str(e)}")

    def _export_voice_calls(self, request, project, project_id):
        """
        Export voice/conversation traces as CSV with call-specific fields.
        """
        # CH25-TODO: voice-call CSV export is PG-only. Two blockers:
        #   1. Unbounded walk — no CH equivalent in
        #      VoiceCallListQueryBuilder today. The CH list endpoint
        #      always paginates; export skips pagination.
        #   2. populate_call_logs_result (L1586-1707) iterates a Django
        #      queryset and reads per-row annotations attached upstream
        #      (`span_attributes`, `provider`, `metadata`,
        #      `metric_{config.id}`, `annotation_{label.id}`, etc.).
        #      The wave-3 reader's `list_by_trace_ids` returns
        #      list[CHSpan] without these annotations; reusing
        #      `populate_call_logs_result` would require either a
        #      wrapper that fakes the queryset attribute shape or a
        #      rewritten variant that takes
        #      (CHSpan-rooted-rows, eval_outputs_map,
        #       annotation_outputs_map) and emits the same dict.
        # Migrating cleanly would need (a) a CH unbounded-walk builder
        # (or a Temporal job that streams CH rows in batches), and (b)
        # a `populate_call_logs_result_from_ch(...)` variant that does
        # not rely on Django-queryset side annotations. Staying PG-only
        # until both land or this export is moved to a Temporal job
        # that streams unbounded CH rows + assembles voice-call shape +
        # writes the CSV to S3.
        serializer = TraceExportQuerySerializer(data=request.query_params)
        if not serializer.is_valid():
            return self._gm.bad_request(serializer.errors)

        validated_data = serializer.validated_data

        # Build query (same as list_voice_calls but without pagination)
        root_span_qs = ObservationSpan.objects.filter(
            trace_id=OuterRef("id"), parent_span_id__isnull=True
        )

        base_query = (
            Trace.objects.filter(project_id=project_id)
            .annotate(
                has_conversation_root=Exists(
                    root_span_qs.filter(observation_type="conversation")
                ),
                trace_id=F("id"),
                # Fetch span_attributes from root span (fallback to eval_attributes for old data)
                span_attributes=Subquery(
                    root_span_qs.annotate(
                        _attrs=Coalesce("span_attributes", "eval_attributes")
                    ).values("_attrs")[:1]
                ),
                root_metadata=Subquery(root_span_qs.values("metadata")[:1]),
                provider=Subquery(root_span_qs.values("provider")[:1]),
                start_time=Coalesce(
                    Subquery(
                        root_span_qs.order_by("start_time").values("start_time")[:1]
                    ),
                    "created_at",
                ),
                end_time=Subquery(
                    root_span_qs.order_by("-end_time").values("end_time")[:1]
                ),
                status=Case(
                    When(
                        Exists(root_span_qs.filter(status="ERROR")),
                        then=Value("ERROR"),
                    ),
                    When(
                        Exists(root_span_qs.filter(status="OK")),
                        then=Value("OK"),
                    ),
                    default=Value("UNSET"),
                    output_field=CharField(),
                ),
            )
            .filter(has_conversation_root=True)
        )

        eval_configs, base_query = self.get_eval_configs(project_id, base_query)

        # Apply filters
        filters = validated_data.get("filters", [])
        if filters:
            system_filter_conditions = (
                FilterEngine.get_filter_conditions_for_system_metrics(filters)
            )
            if system_filter_conditions:
                base_query = base_query.filter(system_filter_conditions)

            # Apply voice system metric filters (agent latency, turn count, etc.)
            voice_metric_conditions, voice_annotations = (
                FilterEngine.get_filter_conditions_for_voice_system_metrics(filters)
            )
            if voice_annotations:
                base_query = base_query.annotate(**voice_annotations)
            if voice_metric_conditions:
                base_query = base_query.filter(voice_metric_conditions)

            # Separate annotation filters from eval filters
            def _get_col_type(f):
                fc = f.get("filter_config", {})
                return fc.get("col_type", f.get("col_type", ""))

            annotation_col_types = {"ANNOTATION"}
            annotation_column_ids = {"my_annotations", "annotator"}
            non_annotation_filters = [
                f
                for f in filters
                if _get_col_type(f) not in annotation_col_types
                and f.get("column_id") not in annotation_column_ids
            ]

            eval_filter_conditions = (
                FilterEngine.get_filter_conditions_for_non_system_metrics(
                    non_annotation_filters
                )
            )
            if eval_filter_conditions:
                base_query = base_query.filter(eval_filter_conditions)

            span_attribute_conditions = (
                FilterEngine.get_filter_conditions_for_span_attributes(filters)
            )
            if span_attribute_conditions:
                base_query = base_query.filter(span_attribute_conditions)

        base_query = base_query.order_by("-start_time", "-id")

        # Process call logs using existing method
        results = self.populate_call_logs_result(base_query, eval_configs)

        # Collect dynamic eval column names
        eval_columns = set()
        for result in results:
            if result.get("eval_outputs"):
                for config_id, eval_data in result["eval_outputs"].items():
                    eval_name = eval_data.get("name", f"Eval_{config_id}")
                    eval_columns.add(eval_name)

        # Build CSV
        fieldnames = [
            "ID",
            "Call ID",
            "Phone Number",
            "Call Type",
            "Status",
            "Started At",
            "Ended At",
            "Duration (s)",
            "Recording URL",
            "Stereo Recording URL",
            "Call Summary",
            "Overall Score",
            "Response Time (ms)",
            "Cost (cents)",
            "Ended Reason",
            "Transcript",
        ]

        sorted_eval_columns = sorted(eval_columns)
        for eval_name in sorted_eval_columns:
            fieldnames.append(eval_name)

        response = HttpResponse(content_type="text/csv")
        filename = f"{project.name or 'project'}_voice_calls.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.DictWriter(response, fieldnames=fieldnames)
        writer.writeheader()

        for result in results:
            # Format transcript as "role: content" per line
            transcript_text = ""
            if result.get("transcript"):
                lines = []
                for entry in result["transcript"]:
                    role = entry.get("role", "unknown")
                    content = entry.get("content", "")
                    lines.append(f"{role}: {content}")
                transcript_text = "\n".join(lines)

            # Build recording URL from nested recording dict
            recording = result.get("recording", {}) or {}
            mono = recording.get("mono", {}) or {}
            recording_url = result.get("recording_url") or mono.get("combinedUrl") or ""
            stereo_url = (
                result.get("stereo_recording_url") or recording.get("stereoUrl") or ""
            )

            row_data = {
                "ID": result.get("id", ""),
                "Call ID": result.get("call_id", ""),
                "Phone Number": result.get("phone_number", ""),
                "Call Type": result.get("call_type", ""),
                "Status": result.get("status", ""),
                "Started At": result.get("started_at", ""),
                "Ended At": result.get("ended_at", ""),
                "Duration (s)": result.get("duration_seconds", ""),
                "Recording URL": recording_url,
                "Stereo Recording URL": stereo_url,
                "Call Summary": result.get("call_summary", ""),
                "Overall Score": result.get("overall_score", ""),
                "Response Time (ms)": result.get("response_time_ms", ""),
                "Cost (cents)": result.get("cost_cents", ""),
                "Ended Reason": result.get("ended_reason", ""),
                "Transcript": transcript_text,
            }

            # Initialize eval columns with empty values
            for eval_name in sorted_eval_columns:
                row_data[eval_name] = ""

            # Fill in eval outputs
            if result.get("eval_outputs"):
                for config_id, eval_data in result["eval_outputs"].items():
                    eval_name = eval_data.get("name", f"Eval_{config_id}")
                    output = eval_data.get("output", "")
                    row_data[eval_name] = str(output) if output is not None else ""

            writer.writerow(row_data)

        return response

    def _list_traces_of_session_clickhouse(
        self,
        request,
        project_id,
        validated_data,
        analytics,
        org_project_ids=None,
        org=None,
    ):
        """List traces-of-session using ClickHouse backend.

        When ``org_project_ids`` is provided (cross-project user-detail
        mode), the builder is constructed with `project_ids=...` and the
        view falls back to a PG-side EvalLogger lookup scoped to those
        projects (the CH dict-lookup path requires a single project_id).

        Telemetry is direct-write-only, so this path always uses the CH25
        builder paired with the V2 query service supplied by its endpoint.
        """
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        read_deadline = ReadDeadline.start(TRACE_LIST_WALL_DEADLINE_MS)

        org_scope = bool(org_project_ids)
        filters = list(validated_data.get("filters", []) or [])
        filtered_attribute_keys = [
            str(item.get("column_id") or item.get("columnId") or "")
            for item in filters
            if (item.get("filter_config") or item.get("filterConfig") or {}).get(
                "col_type"
            )
            == "SPAN_ATTRIBUTE"
        ]
        requested_attribute_keys = tuple(
            dict.fromkeys(
                [
                    *(validated_data.get("attribute_keys", []) or []),
                    *filtered_attribute_keys,
                ]
            )
        )
        if (
            len(requested_attribute_keys) > 100
            or sum(len(key.encode("utf-8")) for key in requested_attribute_keys) > 2_048
        ):
            return self._gm.custom_error_response(
                status.HTTP_400_BAD_REQUEST,
                "Too many custom attribute keys were requested.",
                code="invalid",
            )
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
        cursor_order_token = None
        if cursor_token:
            cursor_state = decode_list_cursor(
                cursor_token,
                resource="observe_traces",
                scope=cursor_scope,
                query=cursor_query,
                page_size=page_size,
            )
            cursor_order_token = _decode_trace_list_cursor_order(
                cursor_state.order,
                org_scope=org_scope,
            )
            filters.append(frozen_window_filter(cursor_state))
            page_number = 0
        if not cursor_token and numbered_page_depth_exceeded(
            page_number=page_number,
            page_size=page_size,
        ):
            logger.info(
                "trace_list_page_depth_exceeded_preflight",
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
                page_size=page_size,
            )
            return self._gm.custom_error_response(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                PAGE_DEPTH_EXCEEDED_MESSAGE,
                code=PAGE_DEPTH_EXCEEDED_CODE,
            )
        session_id = (
            str(validated_data["session_id"])
            if validated_data.get("session_id")
            else None
        )
        if session_id:
            filters.append(
                {
                    "column_id": "trace_session_id",
                    "filter_config": {
                        "col_type": "NORMAL",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": session_id,
                    },
                }
            )

        # Eval configuration metadata is small and remains in PostgreSQL. Never
        # discover data-bearing configs through PG Trace/EvalLogger telemetry:
        # direct-write CH25 is authoritative and those rows need not exist in
        # PostgreSQL. In org mode, including a config with no page result only
        # adds an empty column; it is safer than silently losing CH-only data.
        eval_config_ids = []
        if org_scope:
            eval_configs = CustomEvalConfig.objects.filter(
                project_id__in=org_project_ids,
                deleted=False,
            ).select_related("eval_template")
            eval_config_ids = [str(c.id) for c in eval_configs]
        else:
            # Config metadata is already a finite project-scoped PG read. Do
            # not put a second, window-wide CH discovery query in front of the
            # authoritative page read: on large tenants that optional column
            # pruning phase consumed the whole endpoint deadline. Empty configs
            # merely render empty cells; page-scoped eval hydration below stays
            # finite by trace IDs + this finite config set.
            eval_configs = list(
                CustomEvalConfig.objects.filter(
                    project_id=project_id, deleted=False
                ).select_related("eval_template")
            )
            eval_config_ids = [str(c.id) for c in eval_configs]

        annotation_label_ids_by_project = None
        labels_by_project = {}
        if org_scope:
            if any(
                (item.get("column_id") or item.get("columnId")) == "has_annotation"
                for item in filters
            ):
                labels_by_project = get_annotation_labels_by_project(
                    [str(project_id) for project_id in org_project_ids],
                    organization=org,
                )
                annotation_label_ids_by_project = {
                    project_key: [str(label.id) for label in labels]
                    for project_key, labels in labels_by_project.items()
                }
            # Organization user-detail rows intentionally retain their
            # existing presentation columns; the per-project map above is the
            # authoritative metadata used by residual has_annotation filters.
            annotation_labels = []
        else:
            annotation_labels = get_annotation_labels_for_project(project_id)
        annotation_label_ids = list(
            dict.fromkeys(
                str(label.id)
                for label in [
                    *annotation_labels,
                    *(
                        label
                        for project_labels in labels_by_project.values()
                        for label in project_labels
                    ),
                ]
            )
        )
        label_types = {
            str(label.id): label.type
            for label in [
                *annotation_labels,
                *(
                    label
                    for project_labels in labels_by_project.values()
                    for label in project_labels
                ),
            ]
        }

        cursor_supported = snapshot_cursor_supported(filters, resource="observe_traces")
        if cursor_state is not None and not cursor_supported:
            raise ListCursorError(
                "cursor_unsupported",
                "Cursor pagination is unavailable for this query shape.",
            )
        cursor_enabled = cursor_requested and cursor_supported
        builder = TraceListQueryBuilderV2(
            project_id=None if org_scope else str(project_id),
            project_ids=[str(p) for p in org_project_ids] if org_scope else None,
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            eval_config_ids=eval_config_ids,
            annotation_label_ids=annotation_label_ids,
            annotation_label_ids_by_project=annotation_label_ids_by_project,
        )
        # Continuations freeze only the request window and ordered scan
        # checkpoint. ReplacingMergeTree version predicates are not snapshots:
        # background merges may remove the older row they depend on. Every
        # page therefore classifies current latest state under the same finite
        # read settings.
        page_read_settings = TRACE_LIST_READ_SETTINGS

        # Phase 1: Paginated traces (light columns only — no input/output)
        bounded_page = None
        bounded_error_code = builder.bounded_filter_degraded_error_code()
        if bounded_error_code == "unsupported_filter_shape":
            raise UnsupportedFilterShapeError(
                "Trace filter cannot be evaluated by the bounded list reader"
            )
        try:
            candidate_deadline_ms = read_deadline.remaining_ms(
                TRACE_LIST_CANDIDATE_DEADLINE_MS
            )
        except ReadDeadlineExceeded:
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        if builder.supports_bounded_filter_scan():
            from tracer.selectors.trace_filter_reads import read_bounded_filter_page
            from tracer.services.clickhouse.query_service import QueryResult

            # A first-page caller may explicitly opt in to a bounded, visibly
            # degraded result when the exact ordered prefix cannot be proven.
            # The selector publishes only latest-state-classified matches, never
            # raw candidates. Omitted/false, numbered page N, and cursor
            # continuations remain fail-closed so an incomplete working set can
            # never be mistaken for an exact continuation.
            publish_bounded_partial = bool(
                (cursor_enabled or validated_data.get("allow_sampled") is True)
                and page_number == 0
                and (cursor_state is None or cursor_enabled)
            )
            bounded_page = read_bounded_filter_page(
                builder=builder,
                analytics=analytics,
                filters=filters,
                key_field="trace_id",
                page_number=page_number,
                page_size=page_size,
                deadline_ms=candidate_deadline_ms,
                cursor_start_time=(
                    cursor_state.order[0] if cursor_state is not None else None
                ),
                cursor_order_token=(
                    cursor_order_token if cursor_state is not None else None
                ),
                read_settings=page_read_settings,
                include_incomplete_rows=publish_bounded_partial,
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
                # A strict first page may need to split a scheduled wide seed
                # after ClickHouse rejects that read budget.  The retry stays
                # exact and fail-closed: nothing from the failed slice is
                # published, and the same predicate is retried over adjacent
                # narrower windows inside the existing query/deadline caps.
                retry_wide_read_budget=page_number == 0,
            )
            if not bounded_page.complete:
                if bounded_page.error_code == PAGE_DEPTH_EXCEEDED_CODE:
                    logger.info(
                        "trace_list_page_depth_exceeded",
                        project_id=str(project_id) if project_id else None,
                        page_number=page_number,
                        page_size=page_size,
                    )
                    return self._gm.custom_error_response(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        PAGE_DEPTH_EXCEEDED_MESSAGE,
                        code=PAGE_DEPTH_EXCEEDED_CODE,
                    )
                logger.warning(
                    "trace_list_bounded_read_incomplete",
                    project_id=str(project_id) if project_id else None,
                    page_number=page_number,
                    error_code=bounded_page.error_code,
                )
                if not publish_bounded_partial:
                    return self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Filtered trace data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                if (
                    cursor_enabled
                    and not bounded_page.rows
                    and bounded_page.continuation_slice_end is None
                ):
                    return self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Filtered trace data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
            result = QueryResult(
                data=bounded_page.rows,
                row_count=len(bounded_page.rows),
                backend_used="clickhouse",
                query_time_ms=bounded_page.elapsed_ms,
            )
            total_count = bounded_page.total_rows_lower_bound
        elif bounded_error_code:
            logger.warning(
                "trace_list_filter_unsupported",
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
                error_code=bounded_error_code,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Filtered trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        else:
            if cursor_state is not None:
                raise ListCursorError(
                    "cursor_unsupported",
                    "Cursor pagination is unavailable for this query shape.",
                )
            query, params = builder.build()
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=read_deadline.remaining_ms(1_200),
                settings=page_read_settings,
            )

            # De-duplicate the common sorted prefix before slicing page N.
            result.data, _has_more = paginate_deduped(
                result.data,
                ("project_id", "trace_id") if org_scope else "trace_id",
                page_number,
                page_size,
            )

            count_query, count_params = builder.build_count_query()
            count_result = analytics.execute_ch_query(
                count_query,
                count_params,
                timeout_ms=read_deadline.remaining_ms(1_200),
                settings=page_read_settings,
            )
            total_count = (
                count_result.data[0].get("total", 0) if count_result.data else 0
            )

        query_count = bounded_page.query_count if bounded_page is not None else 2
        query_rows_returned = (
            bounded_page.rows_returned
            if bounded_page is not None
            else len(result.data) + len(count_result.data or [])
        )
        query_result_payload_bytes = (
            bounded_page.result_payload_bytes
            if bounded_page is not None
            else len(
                json.dumps(
                    [result.data, count_result.data],
                    default=str,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        )

        # Every page-scoped ClickHouse enrichment shares the request's one wall
        # deadline. They are independent once the trace page is known, so run
        # them concurrently: endpoint latency is selector + max(enrichment),
        # never selector + the serial sum of five independent queries.
        trace_ids = [str(row.get("trace_id", "")) for row in result.data]
        trace_user_identities = tuple(
            dict.fromkeys(
                (
                    str(row.get("project_id") or project_id or ""),
                    str(row.get("trace_id") or ""),
                )
                for row in result.data
                if (row.get("project_id") or project_id) and row.get("trace_id")
            )
        )
        bounded_user_resolver = getattr(
            builder, "resolve_user_ids_for_trace_identities", None
        )
        if callable(bounded_user_resolver):
            user_query, user_params = "", {}
        else:
            user_query, user_params = builder.build_user_id_query(trace_ids)
        eval_query, eval_params = builder.build_eval_replay_query(trace_ids)

        def _execute_enrichment(query, params):
            return analytics.execute_ch_query(
                query,
                params,
                timeout_ms=read_deadline.remaining_ms(TRACE_LIST_ENRICHMENT_TIMEOUT_MS),
                settings=page_read_settings,
            )

        def _fetch_span_trace_map():
            if org_scope:
                scored_span_ids_by_project = _annotation_score_span_ids_by_project(
                    annotation_label_ids_by_project,
                    trace_user_identities,
                )
                scored_span_identities = tuple(
                    (candidate_project_id, span_id)
                    for candidate_project_id, span_ids in scored_span_ids_by_project.items()
                    for span_id in span_ids
                )
                if not scored_span_identities:
                    return {}
                return analytics.get_span_trace_map(
                    trace_ids,
                    trace_identities=trace_user_identities,
                    scored_span_identities=scored_span_identities,
                    timeout_ms=read_deadline.remaining_ms(
                        TRACE_LIST_ENRICHMENT_TIMEOUT_MS
                    ),
                    settings=page_read_settings,
                )

            scored_span_ids = _annotation_score_span_ids(
                annotation_label_ids,
                str(project_id),
            )
            if not scored_span_ids:
                return {}
            return analytics.get_span_trace_map(
                trace_ids,
                project_id=str(project_id),
                start_date=builder.params.get("start_date"),
                end_date=builder.params.get("end_date"),
                timeout_ms=read_deadline.remaining_ms(TRACE_LIST_ENRICHMENT_TIMEOUT_MS),
                settings=page_read_settings,
                scored_span_ids=scored_span_ids,
            )

        tasks: dict[str, Any] = {}
        content_task_names: list[str] = []
        attribute_task_names: list[str] = []
        attribute_task_expected_rows: dict[str, int] = {}
        for chunk_index, chunk_start in enumerate(
            range(0, len(result.data), TRACE_LIST_ENRICHMENT_CHUNK_SIZE)
        ):
            chunk_rows = result.data[
                chunk_start : chunk_start + TRACE_LIST_ENRICHMENT_CHUNK_SIZE
            ]
            chunk_trace_ids = list(
                dict.fromkeys(
                    str(row.get("trace_id") or "")
                    for row in chunk_rows
                    if row.get("trace_id")
                )
            )
            chunk_trace_identities = tuple(
                dict.fromkeys(
                    (
                        str(row.get("project_id") or project_id or ""),
                        str(row.get("trace_id") or ""),
                    )
                    for row in chunk_rows
                    if (row.get("project_id") or project_id) and row.get("trace_id")
                )
            )
            chunk_root_identities = [
                (
                    str(row.get("project_id") or project_id or ""),
                    str(row.get("trace_id") or ""),
                    str(row.get("root_span_id") or ""),
                    row.get("start_time"),
                )
                for row in chunk_rows
                if row.get("trace_id")
                and row.get("root_span_id")
                and row.get("start_time") is not None
                and (row.get("project_id") or project_id)
            ]
            content_query, content_params = builder.build_content_query(
                chunk_trace_ids,
                root_identities=(
                    chunk_root_identities
                    if len(chunk_root_identities) == len(chunk_rows)
                    else None
                ),
            )
            if content_query:
                task_name = f"content:{chunk_index}"
                tasks[task_name] = (content_query, content_params)
                content_task_names.append(task_name)
            attr_query, attr_params = builder.build_span_attributes_query(
                chunk_trace_ids,
                attribute_keys=requested_attribute_keys,
                trace_identities=chunk_trace_identities,
            )
            if attr_query:
                task_name = f"attributes:{chunk_index}"
                tasks[task_name] = (attr_query, attr_params)
                attribute_task_names.append(task_name)
                attribute_task_expected_rows[task_name] = len(
                    chunk_trace_identities
                ) * len(requested_attribute_keys)
        if user_query:
            tasks["users"] = (user_query, user_params)
        if eval_query and trace_ids and eval_config_ids:
            tasks["evals"] = (eval_query, eval_params)

        enrichment_results: dict[str, Any] = {}
        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=TRACE_LIST_ENRICHMENT_MAX_WORKERS
        )
        future_names: dict[concurrent.futures.Future, str] = {}
        try:
            for task_name, (task_query, task_params) in tasks.items():
                future = pool.submit(_execute_enrichment, task_query, task_params)
                future_names[future] = task_name
            if callable(bounded_user_resolver) and trace_user_identities:
                user_future = pool.submit(
                    bounded_user_resolver,
                    trace_user_identities,
                    analytics,
                    settings=page_read_settings,
                    timeout_ms_provider=lambda: read_deadline.remaining_ms(
                        TRACE_LIST_ENRICHMENT_TIMEOUT_MS
                    ),
                )
                future_names[user_future] = "users"
            if trace_ids and annotation_label_ids:
                span_map_future = pool.submit(_fetch_span_trace_map)
                future_names[span_map_future] = "span_trace_map"

            wait_seconds = read_deadline.remaining_ms() / 1000
            enrichment_results, user_degradation = _collect_trace_enrichment_futures(
                future_names,
                timeout_seconds=wait_seconds,
            )
            if user_degradation is not None:
                logger.warning(
                    "trace_list_user_enrichment_degraded",
                    error_type=user_degradation[0],
                    clickhouse_error_code=user_degradation[1],
                    project_id=str(project_id) if project_id else None,
                    page_number=page_number,
                )
            if user_degradation != ("TimeoutError", None):
                read_deadline.remaining_ms()
        except (concurrent.futures.TimeoutError, ReadDeadlineExceeded) as exc:
            logger.warning(
                "trace_list_enrichment_deadline_exceeded",
                error_type=type(exc).__name__,
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as exc:
            failed_phase = next(
                (
                    name
                    for future, name in future_names.items()
                    if future.done() and future.exception() is exc
                ),
                "unknown",
            )
            logger.warning(
                "trace_list_enrichment_failed",
                phase=failed_phase,
                error_type=type(exc).__name__,
                read_budget_error=is_read_budget_error(exc),
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
                exc_info=True,
            )
            if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
                raise
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        if set(enrichment_results) != set(future_names.values()):
            raise AssertionError("trace enrichment futures did not all complete")

        def _chunked_enrichment_rows(task_names: list[str]) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for task_name in task_names:
                task_result = enrichment_results.get(task_name)
                if task_result is not None:
                    rows.extend(task_result.data or [])
            return rows

        query_count += len(future_names)
        resolved_users = enrichment_results.get("users")
        query_count += max(0, getattr(resolved_users, "query_count", 1) - 1)
        for task_result in enrichment_results.values():
            task_rows = (
                task_result.data if hasattr(task_result, "data") else task_result
            )
            if isinstance(task_rows, dict):
                task_rows = list(task_rows.items())
            if isinstance(task_rows, (list, tuple)):
                query_rows_returned += len(task_rows)
                query_result_payload_bytes += len(
                    json.dumps(
                        task_rows,
                        default=str,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )

        content_rows = _chunked_enrichment_rows(content_task_names)
        if not org_scope:
            for content_row in content_rows:
                content_row.setdefault("project_id", str(project_id or ""))
        expected_content_identities = tuple(trace_user_identities)
        actual_content_identities = tuple(
            (
                str(content_row.get("project_id") or ""),
                str(content_row.get("trace_id") or ""),
            )
            for content_row in content_rows
        )
        if content_task_names and (
            len(expected_content_identities) != len(result.data)
            or len(actual_content_identities) != len(expected_content_identities)
            or set(actual_content_identities) != set(expected_content_identities)
        ):
            logger.warning(
                "trace_list_content_replay_incomplete",
                returned=len(actual_content_identities),
                requested=len(expected_content_identities),
                project_id=str(project_id) if project_id else None,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        content_map = merge_content_rows(
            result.data,
            content_rows,
            id_key=("project_id", "trace_id"),
            keys=(
                "input",
                "output",
                "attrs_string",
                "attrs_number",
                "attrs_bool",
                "attributes_extra",
                "trace_tags",
            ),
        )

        # metadata needs JSON-parsing from the raw CH column
        for row in result.data:
            content = content_map.get(
                (
                    str(row.get("project_id") or project_id or ""),
                    str(row.get("trace_id", "")),
                ),
                {},
            )
            raw_meta = content.get("metadata", "{}")
            if isinstance(raw_meta, str):
                try:
                    row["metadata"] = json.loads(raw_meta)
                except (json.JSONDecodeError, TypeError):
                    row["metadata"] = {}
            else:
                row["metadata"] = raw_meta or {}

        user_result = enrichment_results.get("users")
        user_id_map: dict[tuple[str, str], str] = {}
        for user_row in user_result.data if user_result is not None else []:
            row_project_id = user_row.get("project_id")
            # A trace id is not globally unique.  Legacy single-project rows do
            # not carry project_id, so add the known request scope only when it
            # is unambiguous; organization-scoped unqualified labels fail closed.
            if not row_project_id and not org_scope:
                row_project_id = project_id
            if row_project_id and user_row.get("trace_id") and user_row.get("user_id"):
                user_id_map[(str(row_project_id), str(user_row["trace_id"]))] = str(
                    user_row["user_id"]
                )

        # Phase 2: page-scoped eval scores.
        eval_map = {}
        eval_result = enrichment_results.get("evals")
        if eval_result is not None:
            try:
                expanded_eval_rows = builder.expand_eval_replay_rows(eval_result.data)
                eval_map = builder.pivot_eval_results(
                    [(list(row.values())) for row in expanded_eval_rows],
                    (list(expanded_eval_rows[0].keys()) if expanded_eval_rows else []),
                )
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "trace_list_eval_replay_invalid",
                    error_type=type(exc).__name__,
                    project_id=str(project_id) if project_id else None,
                    page_number=page_number,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Trace data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )

        # Phase 3: Annotations — PG values, span->trace resolved via CH.
        # In org-scoped mode the page spans multiple projects, so scope the
        # map on the window only (a single project_id would drop other
        # projects' spans).
        span_trace_map = enrichment_results.get("span_trace_map", {})
        annotation_map = _build_annotation_map_from_scores(
            trace_ids,
            annotation_label_ids,
            label_types,
            span_trace_map,
            analytics=analytics,
            project_id=None if org_scope else str(project_id),
            trace_identities=trace_user_identities if org_scope else None,
            annotation_label_ids_by_project=annotation_label_ids_by_project,
        )
        try:
            read_deadline.remaining_ms()
        except ReadDeadlineExceeded as exc:
            logger.warning(
                "trace_list_annotation_hydration_deadline_exceeded",
                error_type=type(exc).__name__,
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

        # Phase 4: Aggregated span attributes for custom columns
        _SKIP_ATTR_PREFIXES = (
            "raw.",
            "llm.input_messages",
            "llm.output_messages",
            "input.value",
            "output.value",
        )
        aggregated_attrs = {}  # (project_id, trace_id) -> {attr_key -> values}
        for task_name in attribute_task_names:
            task_result = enrichment_results.get(task_name)
            task_rows = task_result.data if task_result is not None else []
            # The query returns at most one row for every exact page identity
            # and requested key. A larger replay is impossible for valid SQL
            # and therefore indicates a malformed/cross-tenant result rather
            # than a legitimate high-fanout trace.
            expected_rows = attribute_task_expected_rows.get(task_name, 0)
            if len(task_rows or ()) > expected_rows:
                logger.warning(
                    "trace_list_attribute_result_bound_exceeded",
                    returned=len(task_rows),
                    expected_rows=expected_rows,
                    requested_key_count=len(requested_attribute_keys),
                    project_id=str(project_id) if project_id else None,
                    page_number=page_number,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Trace data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )

        requested_attribute_key_set = set(requested_attribute_keys)
        try:
            for attr_row in _chunked_enrichment_rows(attribute_task_names):
                tid = str(attr_row.get("trace_id", ""))
                attr_project_id = str(
                    attr_row.get("project_id") or (project_id if not org_scope else "")
                )
                if not tid or not attr_project_id:
                    continue
                attr_identity = (attr_project_id, tid)
                aggregated_attrs.setdefault(attr_identity, {})

                # Current CH25 shape: one exact distinct value per requested
                # (project, trace, key).  Values are JSON across every typed
                # storage family, so decoding retains scalar/structured type.
                if "attribute_key" in attr_row:
                    key = str(attr_row.get("attribute_key") or "")
                    if not key or key not in requested_attribute_key_set:
                        raise ValueError(
                            "trace attribute replay returned an unrequested key"
                        )
                    value = _decode_projected_trace_attribute_value(
                        attr_row.get("attribute_value_json")
                    )
                    if key in aggregated_attrs[attr_identity]:
                        raise ValueError(
                            "trace attribute replay returned a duplicate identity"
                        )
                    aggregated_attrs[attr_identity][key] = [value]
                    continue

                # Historical expanded/packed rows remain accepted by tests and
                # rolling-version callers, but retain their legacy exclusions.
                for attrs in _iter_merged_trace_attribute_rows(attr_row):
                    for key, value in attrs.items():
                        if key.startswith(_SKIP_ATTR_PREFIXES):
                            continue
                        if isinstance(value, str) and len(value) > 500:
                            continue
                        values = aggregated_attrs[attr_identity].setdefault(key, [])
                        _append_trace_attribute_value(values, value)
        except ValueError as exc:
            logger.warning(
                "trace_list_attribute_replay_invalid",
                error_type=type(exc).__name__,
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

        # Build column config — get_default_trace_config() already includes
        # all standard columns (latency, tokens, cost, user_id, etc.)
        column_config = get_default_trace_config()
        column_config = update_column_config_based_on_eval_config(
            column_config, eval_configs
        )
        column_config = update_span_column_config_based_on_annotations(
            column_config, annotation_labels
        )

        # Format response matching PG format
        table_data = []
        for row in result.data:
            trace_id = str(row.get("trace_id", ""))
            raw_cost = row.get("cost")
            entry = {
                "trace_id": trace_id,
                "project_id": (
                    str(row.get("project_id")) if row.get("project_id") else None
                ),
                "input": row.get("input", ""),
                "output": row.get("output", ""),
                "created_at": (
                    row.get("start_time").isoformat() + "Z"
                    if row.get("start_time")
                    else None
                ),
                "node_type": row.get("observation_type", ""),
                "latency": row.get("latency_ms"),
                "total_tokens": row.get("total_tokens"),
                "prompt_tokens": row.get("prompt_tokens"),
                "completion_tokens": row.get("completion_tokens"),
                "cost": (
                    round(raw_cost, 6)
                    if isinstance(raw_cost, (int, float))
                    and not isinstance(raw_cost, bool)
                    and math.isfinite(raw_cost)
                    else 0
                ),
                "trace_name": row.get("trace_name") or row.get("span_name") or "",
                "start_time": row.get("start_time"),
                "status": row.get("status"),
                "model": row.get("model"),
                "provider": row.get("provider"),
                "tags": row.get("trace_tags") or [],
                "user_id": user_id_map.get(
                    (str(row.get("project_id") or project_id or ""), trace_id)
                ),
            }

            # Add eval metrics
            trace_evals = eval_map.get(trace_id, {})
            for config in eval_configs:
                if org_scope and str(getattr(config, "project_id", "")) != str(
                    row.get("project_id") or ""
                ):
                    continue
                config_id = str(config.id)
                if config_id not in trace_evals:
                    continue
                flatten_eval_score_into_entry(
                    entry,
                    config_id,
                    trace_evals[config_id],
                    eval_output_type_for_config(config),
                )

            # Add annotations
            trace_annotations = annotation_map.get(
                (str(row.get("project_id") or ""), trace_id) if org_scope else trace_id,
                {},
            )
            for label in annotation_labels:
                label_id = str(label.id)
                if label_id in trace_annotations:
                    entry[label_id] = trace_annotations[label_id]

            # Root-span attributes for custom columns (typed maps + attributes_extra)
            flatten_span_attributes_into_entry(entry, row)

            # Include metadata for custom columns
            metadata = row.get("metadata") or {}
            if isinstance(metadata, dict):
                for key, value in metadata.items():
                    if key not in entry:
                        if isinstance(value, str) and len(value) > 500:
                            entry[key] = value[:500] + "..."
                        else:
                            entry[key] = value

            # Requested custom columns use the deterministic latest live value
            # across every span in the trace. This intentionally supersedes an
            # older root-span value for the same requested key.
            trace_attrs = aggregated_attrs.get(
                (str(row.get("project_id") or project_id or ""), trace_id), {}
            )
            for key, values in trace_attrs.items():
                vals = sorted(values, key=_trace_attribute_value_token)
                entry[key] = vals[0] if len(vals) == 1 else vals

            table_data.append(entry)

        try:
            read_deadline.remaining_ms()
        except ReadDeadlineExceeded:
            logger.warning(
                "trace_list_response_deadline_exceeded",
                project_id=str(project_id) if project_id else None,
                page_number=page_number,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

        next_cursor = None
        cursor_seen_rows = (
            cursor_state.seen_rows
            if cursor_state is not None
            else page_number * page_size
        ) + len(result.data)
        cursor_has_more = False
        if (
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
            cursor_order = _trace_list_cursor_order_for_partial_page(
                rows=result.data,
                bounded_page=bounded_page,
                cursor_state=cursor_state,
                org_scope=org_scope,
            )
            next_cursor = encode_list_cursor(
                resource="observe_traces",
                scope=cursor_scope,
                query=cursor_query,
                page_size=page_size,
                window_start=window_start,
                window_end=window_end,
                order=cursor_order,
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

        metadata_total_rows = total_count
        if (
            cursor_enabled
            and bounded_page is not None
            and bounded_page.complete
            and not bounded_page.has_more
        ):
            # A bounded cursor read reports only this transport's matches.
            # Once the frozen cursor window is exhausted, the exact global
            # total is the previously published prefix plus this final page.
            metadata_total_rows = cursor_seen_rows

        metadata = {"total_rows": metadata_total_rows}
        if bounded_page is not None:
            published_has_more = (
                bool(bounded_page.complete and bounded_page.has_more) or cursor_has_more
            )
            total_rows_is_lower_bound = not (
                bounded_page.complete and not bounded_page.has_more
            )
            # ``bounded_page.complete`` describes whether this one transport
            # read exhausted/proved the whole requested window.  A signed
            # cursor checkpoint is a different, exact public contract: every
            # returned row was latest-state classified in canonical order and
            # the token resumes at the first unclassified position.  Reporting
            # that safe chunk as ``degraded`` made the UI show a query failure
            # even though no sampled or unproven row was exposed.  Totals stay
            # explicitly lower-bound until the cursor chain is exhausted.
            public_chunk_complete = bounded_page.complete or cursor_has_more
            metadata.update(
                {
                    "total_rows_is_lower_bound": total_rows_is_lower_bound,
                    "has_more": published_has_more,
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
        if bounded_page is None or bounded_page.complete or cursor_has_more:
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
        if (
            bounded_page is not None
            and not bounded_page.complete
            and not cursor_has_more
        ):
            # An opted-in numbered request can still publish a first classified
            # prefix, but only a signed snapshot cursor may continue it.
            metadata["has_more"] = False
            metadata["next_cursor"] = None
        if metadata.get(
            "total_rows_is_lower_bound"
        ) and exact_total_explicitly_required(
            request,
            validated_data,
            allow_exact_cursor_lower_bound=True,
        ):
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        response = {
            "metadata": metadata,
            "table": _sanitize_nonfinite_floats(table_data),
            "config": column_config,
        }

        return self._gm.success_response(response)

    def _list_voice_calls_clickhouse(
        self, request, project_id, validated_data, remove_simulation_calls, analytics
    ):
        """List voice calls using ClickHouse backend.

        Telemetry is direct-write-only, so this path always uses CH25 query
        builders paired with the V2 query service supplied by its endpoint.
        """
        from tracer.services.clickhouse.read_budget import is_read_budget_error
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )
        from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
            VoiceCallListQueryBuilderV2,
        )

        read_deadline = ReadDeadline.start(TRACE_LIST_WALL_DEADLINE_MS)

        filters = list(validated_data.get("filters", []) or [])
        page = validated_data.get("page", 1)
        page_size = validated_data.get("page_size", 30)
        page_number = page - 1  # Convert 1-based to 0-based
        cursor_token = validated_data.get("cursor")
        cursor_requested = bool(cursor_token or validated_data.get("cursor_mode"))
        cursor_scope = cursor_scope_for_request(request, project_ids=[str(project_id)])
        cursor_query = dict(validated_data)
        cursor_state = None
        cursor_order_token = None
        if cursor_token:
            cursor_state = decode_list_cursor(
                cursor_token,
                resource="voice_calls",
                scope=cursor_scope,
                query=cursor_query,
                page_size=page_size,
            )
            cursor_order_token = _decode_trace_list_cursor_order(
                cursor_state.order,
                org_scope=False,
            )
            filters.append(frozen_window_filter(cursor_state))
            page_number = 0

        sim_flag = remove_simulation_calls and str(
            remove_simulation_calls
        ).lower() not in ("false", "0", "")

        # Reject unsupported numbered-page depths before any PG or ClickHouse
        # work.  The builder's scan recommendations depend only on the request
        # shape; eval and annotation projections do not affect this bound.
        preflight_builder = VoiceCallListQueryBuilderV2(
            project_id=str(project_id),
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            eval_config_ids=[],
            remove_simulation_calls=sim_flag,
            annotation_label_ids=[],
        )
        preflight_classify_batch_size = int(
            preflight_builder.recommended_filter_classify_batch_size() or 50
        )
        preflight_seed_batch_size = int(
            preflight_builder.recommended_filter_seed_batch_size()
        )
        if not cursor_requested and bounded_numbered_page_depth_exceeded(
            page_number=page_number,
            page_size=page_size,
            classify_batch_size=preflight_classify_batch_size,
            seed_batch_size=preflight_seed_batch_size,
        ):
            logger.info(
                "voice_call_list_page_depth_exceeded_preflight",
                project_id=str(project_id),
                page_number=page_number,
                page_size=page_size,
            )
            return self._gm.custom_error_response(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                PAGE_DEPTH_EXCEEDED_MESSAGE,
                code=PAGE_DEPTH_EXCEEDED_CODE,
            )

        # Eval configs for the project, from PG (indexed) — replaces the
        # unbounded CH dictGet discovery scan.
        eval_configs, eval_config_ids = get_project_eval_configs(project_id)

        # Get annotation labels that have actual annotations/scores for this project
        annotation_labels = get_annotation_labels_for_project(project_id)
        annotation_label_ids = [str(label.id) for label in annotation_labels]
        label_types = {str(label.id): label.type for label in annotation_labels}

        # A voice-call page is a trace-root page over the same versioned spans
        # table. Reuse the trace cursor eligibility check so independently
        # mutable eval/annotation relations continue to use numbered pages
        # instead of receiving a snapshot guarantee we cannot uphold.
        cursor_supported = snapshot_cursor_supported(filters, resource="observe_traces")
        if cursor_state is not None and not cursor_supported:
            raise ListCursorError(
                "cursor_unsupported",
                "Cursor pagination is unavailable for this query shape.",
            )
        cursor_enabled = cursor_requested and cursor_supported

        builder = VoiceCallListQueryBuilderV2(
            project_id=str(project_id),
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            eval_config_ids=eval_config_ids,
            remove_simulation_calls=sim_flag,
            annotation_label_ids=annotation_label_ids,
        )
        # The signed cursor carries the immutable window and keyset progress;
        # each page resolves current latest state. A raw version ceiling cannot
        # survive ReplacingMergeTree background merges.
        page_read_settings = TRACE_LIST_READ_SETTINGS

        # Phase 1: bounded, newest-first latest-state voice roots. This is used
        # for the healthy path too: the previous raw count scanned the complete
        # project/window and dominated latency even when the first page itself
        # was cheap. The selector proves the requested prefix and exposes a
        # lower-bound count/has-more marker without a second broad aggregation.
        from tracer.selectors.trace_filter_reads import read_bounded_filter_page
        from tracer.services.clickhouse.query_service import QueryResult

        # A caller must opt in explicitly before an incomplete prefix can be
        # published. The bounded selector exposes only fully classified matches,
        # never raw candidates, and permits this contract only for page zero.
        # Omitted/false remain fail-closed so existing clients cannot silently
        # reinterpret a partial page as an exact ordered result.
        publish_bounded_partial = bool(
            (cursor_enabled or validated_data.get("allow_sampled") is True)
            and page_number == 0
            and (cursor_state is None or cursor_enabled)
        )
        bounded_page = read_bounded_filter_page(
            builder=builder,
            analytics=analytics,
            filters=filters,
            key_field="trace_id",
            page_number=page_number,
            page_size=page_size,
            deadline_ms=read_deadline.remaining_ms(TRACE_LIST_CANDIDATE_DEADLINE_MS),
            cursor_start_time=(
                cursor_state.order[0] if cursor_state is not None else None
            ),
            cursor_order_token=(
                cursor_order_token if cursor_state is not None else None
            ),
            read_settings=page_read_settings,
            include_incomplete_rows=publish_bounded_partial,
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
            retry_wide_read_budget=page_number == 0,
        )
        if not bounded_page.complete:
            if bounded_page.error_code == PAGE_DEPTH_EXCEEDED_CODE:
                logger.info(
                    "voice_call_list_page_depth_exceeded",
                    project_id=str(project_id),
                    page_number=page_number,
                    page_size=page_size,
                )
                return self._gm.custom_error_response(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    PAGE_DEPTH_EXCEEDED_MESSAGE,
                    code=PAGE_DEPTH_EXCEEDED_CODE,
                )
            logger.warning(
                "voice_call_list_bounded_read_incomplete",
                project_id=str(project_id),
                page_number=page_number,
                error_code=bounded_page.error_code,
            )
            if not publish_bounded_partial or (
                cursor_enabled
                and not bounded_page.rows
                and bounded_page.continuation_slice_end is None
            ):
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Voice call data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
        result = QueryResult(
            data=bounded_page.rows,
            row_count=len(bounded_page.rows),
            backend_used="clickhouse",
            query_time_ms=bounded_page.elapsed_ms,
        )

        # Phase 1b: hydrate only the exact physical roots selected above.
        # The builder resolves latest versions by (project, trace, id,
        # start_time), prunes by partition date, applies tombstones, and strips
        # `call_logs` before transfer. No broad FINAL scan is used.
        page_rows = result.data
        span_ids = [
            str(row.get("root_span_id") or row.get("span_id") or "")
            for row in page_rows
            if row.get("root_span_id") or row.get("span_id")
        ]
        attrs_map = {}
        if span_ids:
            root_identities = [
                (
                    str(row.get("project_id") or project_id),
                    str(row.get("trace_id") or ""),
                    str(row.get("root_span_id") or row.get("span_id") or ""),
                    row.get("start_time"),
                )
                for row in page_rows
            ]
            hydrated_rows = []
            content_batch_size = 200
            for batch_start in range(0, len(root_identities), content_batch_size):
                batch_end = batch_start + content_batch_size
                batch_identities = root_identities[batch_start:batch_end]
                batch_span_ids = span_ids[batch_start:batch_end]
                attrs_query, attrs_params = builder.build_content_query(
                    batch_span_ids,
                    root_identities=batch_identities,
                )
                try:
                    content_timeout_ms = read_deadline.remaining_ms(1_500)
                    attrs_result = analytics.execute_ch_query(
                        attrs_query,
                        attrs_params,
                        timeout_ms=content_timeout_ms,
                        settings={
                            **page_read_settings,
                            "max_result_rows": content_batch_size,
                            "result_overflow_mode": "throw",
                        },
                    )
                except Exception as exc:
                    if not is_read_budget_error(exc):
                        raise
                    logger.warning(
                        "voice_call_content_read_budget_exceeded",
                        project_id=str(project_id),
                        page_number=page_number,
                    )
                    return self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Voice call data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                if len(attrs_result.data) != len(batch_identities):
                    logger.warning(
                        "voice_call_content_replay_incomplete",
                        project_id=str(project_id),
                        page_number=page_number,
                        expected_rows=len(batch_identities),
                        actual_rows=len(attrs_result.data),
                    )
                    return self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Voice call data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                hydrated_rows.extend(attrs_result.data)

            for arow in hydrated_rows:
                sid = str(arow.get("span_id", ""))
                attr_identity = (
                    str(arow.get("project_id") or project_id),
                    str(arow.get("trace_id") or ""),
                    sid,
                    arow.get("start_time"),
                )
                raw = arow.get("span_attributes", "{}")
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except (json.JSONDecodeError, TypeError):
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {}
                # Union typed Maps over attributes_extra: voice spans split call.* scalars
                # into the Maps and overflow keys into attributes_extra, so never skip the Maps.
                for k, v in (arow.get("attrs_string") or {}).items():
                    parsed.setdefault(k, v)
                for k, v in (arow.get("attrs_number") or {}).items():
                    parsed.setdefault(k, v)
                for k, v in (arow.get("attrs_bool") or {}).items():
                    parsed.setdefault(k, bool(v))
                attrs_map[attr_identity] = {
                    "span_attributes": parsed,
                    "provider": arow.get("provider"),
                }

        cursor_seen_before = cursor_state.seen_rows if cursor_state is not None else 0
        total_count = (
            cursor_seen_before + bounded_page.total_rows_lower_bound
            if cursor_enabled
            else bounded_page.total_rows_lower_bound
        )

        trace_ids = [str(row.get("trace_id", "")) for row in page_rows]

        # Phase 2: Eval scores
        eval_map = {}
        if trace_ids and eval_config_ids:
            eval_query, eval_params = builder.build_eval_query(trace_ids)
            if eval_query:
                try:
                    eval_timeout_ms = read_deadline.remaining_ms(1_500)
                    eval_result = analytics.execute_ch_query(
                        eval_query,
                        eval_params,
                        timeout_ms=eval_timeout_ms,
                        settings={
                            "max_threads": 1,
                            "max_memory_usage": 256 * 1024 * 1024,
                            "max_bytes_to_read": 512 * 1024 * 1024,
                            "read_overflow_mode": "throw",
                            "max_result_rows": 5001,
                            "result_overflow_mode": "throw",
                        },
                    )
                except Exception as exc:
                    if not is_read_budget_error(exc):
                        raise
                    logger.warning(
                        "voice_call_eval_read_budget_exceeded",
                        project_id=str(project_id),
                        page_number=page_number,
                    )
                    return self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Voice call data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                if len(eval_result.data) > 5000:
                    logger.warning(
                        "voice_call_eval_result_limit_exceeded",
                        project_id=str(project_id),
                        page_number=page_number,
                    )
                    return self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Voice call data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                eval_map = TraceListQueryBuilderV2.pivot_eval_results(
                    [(list(row.values())) for row in eval_result.data],
                    list(eval_result.data[0].keys()) if eval_result.data else [],
                )

        # Phase 3: Annotations — fetch from PG Score (unified annotation system)
        try:
            annotation_map = _build_annotation_map_from_scores(
                trace_ids,
                annotation_label_ids,
                label_types,
                analytics=analytics,
                project_id=str(project_id),
                start_date=builder.params.get("start_date"),
                end_date=builder.params.get("end_date"),
            )
        except AnnotationScoreReadBoundExceeded:
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Voice call data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

        # Phase 4 (child spans) removed — observation_span is a detail-only field.

        # Build column config
        column_config = update_column_config_based_on_eval_config(
            [], eval_configs, is_simulator=True
        )
        column_config = update_span_column_config_based_on_annotations(
            column_config, annotation_labels
        )

        # Assemble results
        results = []
        for row in page_rows:
            trace_id = str(row.get("trace_id", ""))
            span_id = str(row.get("root_span_id") or row.get("span_id") or "")
            provider = row.get("provider") or "vapi"

            # Get span_attributes from CH CDC table (Phase 1b)
            attr_identity = (
                str(row.get("project_id") or project_id),
                trace_id,
                span_id,
                row.get("start_time"),
            )
            attr_row = attrs_map.get(attr_identity, {})
            span_attrs = attr_row.get("span_attributes") or {}
            provider = attr_row.get("provider") or provider

            # Post-filter simulator calls in Python (can't do in CH without OOM)
            if sim_flag and VoiceCallListQueryBuilderV2.is_simulator_call(
                span_attrs, provider
            ):
                continue

            raw_log = self._coerce_raw_log(span_attrs.get("raw_log"))
            voice_metrics = self._extract_voice_turn_and_talk_metrics(
                span_attrs, raw_log
            )

            # Process raw_log through existing provider-specific logic
            processed_log = ObservabilityService.process_raw_logs(
                raw_log, provider, span_attributes=span_attrs
            )
            # Collector-routed pulls carry no raw_log (OTLP); span start/end times
            # are the call start/duration.
            if not raw_log:
                if not processed_log.get("started_at"):
                    _st = row.get("start_time")
                    if _st:
                        processed_log["started_at"] = (
                            _st.isoformat() if hasattr(_st, "isoformat") else str(_st)
                        )
                if processed_log.get("duration_seconds") is None:
                    _st, _et = row.get("start_time"), row.get("end_time")
                    if _st and _et and hasattr(_st, "timestamp"):
                        processed_log["duration_seconds"] = max(
                            0, int(_et.timestamp() - _st.timestamp())
                        )
                # The list's date column binds created_at.
                if not processed_log.get("created_at"):
                    processed_log["created_at"] = processed_log.get("started_at")

            entry = {
                **processed_log,
                "id": trace_id,
                "trace_id": trace_id,
                "turn_count": voice_metrics.get("turn_count"),
                "talk_ratio": voice_metrics.get("talk_ratio"),
                "agent_talk_percentage": voice_metrics.get("agent_talk_percentage"),
                "bot_talk_pct": voice_metrics.get("bot_talk_pct"),
                "user_talk_pct": voice_metrics.get("user_talk_pct"),
                "avg_agent_latency_ms": self._round_metric(
                    span_attrs.get("avg_agent_latency_ms")
                ),
                "user_wpm": self._round_metric(span_attrs.get("call.user_wpm")),
                "bot_wpm": self._round_metric(span_attrs.get("call.bot_wpm")),
                "user_interruption_count": self._round_metric(
                    span_attrs.get("user_interruption_count")
                ),
                "ai_interruption_count": self._round_metric(
                    span_attrs.get("ai_interruption_count")
                ),
            }
            # Only override with voice_metrics if they have values —
            # otherwise keep the ones computed by process_raw_logs.
            if voice_metrics.get("turn_count") is not None:
                entry["turn_count"] = voice_metrics["turn_count"]
            if voice_metrics.get("talk_ratio") is not None:
                entry["talk_ratio"] = voice_metrics["talk_ratio"]
            if voice_metrics.get("agent_talk_percentage") is not None:
                entry["agent_talk_percentage"] = voice_metrics["agent_talk_percentage"]
            if voice_metrics.get("bot_talk_pct") is not None:
                entry["bot_talk_pct"] = voice_metrics["bot_talk_pct"]
                entry["user_talk_pct"] = voice_metrics["user_talk_pct"]
            # Backfill response_time_ms from avg_agent_latency if VAPI didn't set it
            if not entry.get("response_time_ms") and entry.get("avg_agent_latency_ms"):
                entry["response_time_ms"] = entry["avg_agent_latency_ms"]

            # Strip heavy fields from list response — these are served by
            # the voice_call_detail endpoint.
            for key in self._VOICE_CALL_HEAVY_KEYS:
                entry.pop(key, None)
            # Heavy-key strip drops observation_span, which the drawer needs to route to
            # voice; collector rows lack raw_log to fall back. Seed a stub (detail fetch replaces it).
            entry["observation_span"] = (
                [
                    {
                        "id": span_id,
                        "observation_type": "conversation",
                        "parent_span_id": None,
                    }
                ]
                if span_id
                else []
            )

            # Include span attributes for custom columns (skip heavy/nested values).
            # provider_transcript / fi.conversation.transcript / metrics_data are
            # detail-only transcript payloads — never in a list row.
            for key, value in span_attrs.items():
                if (
                    key
                    in (
                        "raw_log",
                        "call",
                        "call_logs",
                        "provider_transcript",
                        "fi.conversation.transcript",
                        "metrics_data",
                    )
                    or key in entry
                ):
                    continue
                if isinstance(value, (str, int, float, bool)):
                    entry[key] = value

            # Add eval metrics
            trace_evals = eval_map.get(trace_id, {})
            if trace_evals:
                metrics = {}
                for config in eval_configs:
                    config_id = str(config.id)
                    if config_id in trace_evals:
                        scores = trace_evals[config_id]
                        metric_name = getattr(config, "name", None) or (
                            getattr(config, "eval_template", None).name
                            if getattr(config, "eval_template", None)
                            else None
                        )
                        eval_template_config = (
                            config.eval_template.config
                            if getattr(config, "eval_template", None)
                            else {}
                        ) or {}
                        output_type = eval_template_config.get("output", "score")
                        metric_entry = {"name": metric_name, "output_type": output_type}
                        # All eval rows errored — surface error to frontend
                        if isinstance(scores, dict) and scores.get("error"):
                            metric_entry["error"] = True
                            metrics[config_id] = metric_entry
                            continue
                        # Non-terminal / skipped eval — surface the lifecycle
                        # status so the FE renders a loading/pending/skipped
                        # cell instead of a blank/0.
                        if isinstance(scores, dict) and isinstance(
                            scores.get("status"), str
                        ):
                            metric_entry["status"] = scores["status"]
                            if scores.get("skipped_reason"):
                                metric_entry["skipped_reason"] = scores[
                                    "skipped_reason"
                                ]
                            metrics[config_id] = metric_entry
                            continue
                        if isinstance(scores, dict):
                            if scores.get("per_choice"):
                                metric_entry["output"] = [
                                    k for k, v in scores["per_choice"].items() if v > 0
                                ]
                                metric_entry["output_type"] = "str_list"
                            elif "str_list" in scores and scores["str_list"]:
                                metric_entry["output"] = scores["str_list"]
                                metric_entry["output_type"] = "str_list"
                            elif "avg_score" in scores or "pass_rate" in scores:
                                # PASS_FAIL → pass_rate, else → avg_score. Both
                                # come pre-scaled (×100) from pivot_eval_results;
                                # keep 0.0 (check is-not-None, not truthiness).
                                score_val = select_eval_score(scores, output_type)
                                metric_entry["output"] = (
                                    round(score_val, 2)
                                    if isinstance(score_val, (int, float))
                                    else score_val
                                )
                        else:
                            metric_entry["output"] = scores
                        metrics[config_id] = metric_entry
                if metrics:
                    entry["eval_outputs"] = metrics

                # Flatten eval values onto the row too. CHOICES columns read the
                # flat key params.data["{config_id}**{choice}"] directly; score /
                # pass-fail columns read params.data.eval_outputs[dataKey]. Without
                # this flatten the per-choice columns stay blank in the UI.
                for eval_config in eval_configs:
                    cid = str(eval_config.id)
                    if cid not in trace_evals:
                        continue
                    flatten_eval_score_into_entry(
                        entry,
                        cid,
                        trace_evals[cid],
                        eval_output_type_for_config(eval_config),
                    )

            # Add annotation outputs — flatten onto the row for frontend grid compatibility
            # Frontend valueGetter reads params.data[labelId] directly
            trace_annotations = annotation_map.get(trace_id, {})
            if trace_annotations:
                annotation_outputs = {}
                for label in annotation_labels:
                    label_id = str(label.id)
                    if label_id in trace_annotations:
                        entry[label_id] = trace_annotations[label_id]
                        annotation_outputs[label_id] = trace_annotations[label_id]
                if annotation_outputs:
                    entry["annotation_outputs"] = annotation_outputs

            results.append(entry)

        # Return DRF-style paginated response
        import math

        total_pages = math.ceil(total_count / page_size) if page_size else 1
        response_page = (
            (cursor_seen_before // page_size) + 1 if cursor_state is not None else page
        )
        next_cursor = None
        cursor_seen_rows = cursor_seen_before + len(page_rows)
        cursor_has_more = False
        if cursor_enabled and (
            (bounded_page.complete and bounded_page.has_more)
            or (
                not bounded_page.complete
                and (
                    bounded_page.has_more
                    or bounded_page.continuation_slice_end is not None
                )
            )
        ):
            window_start, window_end = builder.parse_time_range(filters)
            next_cursor = encode_list_cursor(
                resource="voice_calls",
                scope=cursor_scope,
                query=cursor_query,
                page_size=page_size,
                window_start=window_start,
                window_end=window_end,
                order=_trace_list_cursor_order_for_partial_page(
                    rows=page_rows,
                    bounded_page=bounded_page,
                    cursor_state=cursor_state,
                    org_scope=False,
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
        # A nonterminal cursor chunk is still an exact public result: every
        # row is latest-state classified and the signed token resumes at the
        # first unclassified root. Keep totals lower-bound until exhaustion,
        # but do not expose the selector's internal finite-scan stop as a data
        # error. Numbered allow_sampled compatibility retains its degraded
        # metadata below because it has no exact continuation contract.
        public_chunk_complete = bounded_page.complete or cursor_has_more
        response_data = {
            "count": total_count,
            "count_is_lower_bound": (
                cursor_has_more or not bounded_page.complete if cursor_enabled else True
            ),
            "total_pages": total_pages,
            "current_page": response_page,
            "next": None,
            "previous": None,
            "results": results,
            "config": column_config,
            "has_more": cursor_has_more if cursor_enabled else bounded_page.has_more,
            "query_complete": public_chunk_complete,
            "query_status": (
                "complete" if public_chunk_complete else bounded_page.status
            ),
        }
        if cursor_enabled:
            response_data["next_cursor"] = next_cursor
        if bounded_page.error_code and not public_chunk_complete:
            response_data["query_error_code"] = bounded_page.error_code
        if response_data["count_is_lower_bound"] and exact_total_explicitly_required(
            request,
            validated_data,
            allow_exact_cursor_lower_bound=True,
        ):
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Voice call data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        if response_data["has_more"]:
            response_data["next"] = response_page + 1
        if response_page > 1:
            response_data["previous"] = response_page - 1

        from rest_framework.response import Response

        return Response(response_data)

    def _list_traces_clickhouse(
        self, request, project_version_id, analytics, query_params
    ):
        """List traces using ClickHouse backend.

        Telemetry is direct-write-only, so this path always uses the CH25
        builder paired with the V2 query service supplied by its endpoint.
        """
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        read_deadline = ReadDeadline.start(TRACE_LIST_WALL_DEADLINE_MS)

        filters = query_params["filters"]
        sort_params = query_params["sort_params"]
        page_number = query_params["page_number"]
        page_size = query_params["page_size"]
        if numbered_page_depth_exceeded(
            page_number=page_number,
            page_size=page_size,
        ):
            logger.info(
                "prototype_trace_list_page_depth_exceeded_preflight",
                project_version_id=str(project_version_id),
                page_number=page_number,
                page_size=page_size,
            )
            return self._gm.custom_error_response(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                PAGE_DEPTH_EXCEEDED_MESSAGE,
                code=PAGE_DEPTH_EXCEEDED_CODE,
            )

        # Get project_id from project_version
        project_version = ProjectVersion.objects.get(
            id=project_version_id,
            project__organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
        )
        project_id = str(project_version.project_id)

        # Eval configuration metadata is a finite project-scoped PG read. Do
        # not put a separate window-wide ClickHouse discovery query in front of
        # the authoritative trace page: that optional column-pruning step used
        # to carry its own 30-second timeout and could consume the request before
        # the list query started. Page-scoped eval hydration below remains
        # bounded by the selected trace IDs and this finite config set; configs
        # without page data simply render empty cells.
        project_configs = list(
            CustomEvalConfig.objects.filter(
                project_id=project_id, deleted=False
            ).select_related("eval_template")
        )
        eval_configs = project_configs
        eval_config_ids = [str(c.id) for c in eval_configs]

        # Get annotation labels that have actual annotations for this project
        annotation_labels = get_annotation_labels_for_project(
            project_version.project_id
        )
        annotation_label_ids = [str(label.id) for label in annotation_labels]
        label_types = {str(label.id): label.type for label in annotation_labels}

        builder = TraceListQueryBuilderV2(
            project_id=project_id,
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            sort_params=sort_params,
            eval_config_ids=eval_config_ids,
            annotation_label_ids=annotation_label_ids,
            project_version_id=str(project_version_id),
        )

        # Phase 1: Get paginated traces. Project-version-scoped task/eval
        # selectors use the same bounded latest-state reader as Observe; the
        # project_version_id is pushed into both seed and classifier reads.
        bounded_page = None
        bounded_error_code = builder.bounded_filter_degraded_error_code()
        if bounded_error_code == "unsupported_filter_shape":
            raise UnsupportedFilterShapeError(
                "Trace filter cannot be evaluated by the bounded list reader"
            )
        try:
            candidate_deadline_ms = read_deadline.remaining_ms(
                TRACE_LIST_CANDIDATE_DEADLINE_MS
            )
        except ReadDeadlineExceeded:
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        if builder.supports_bounded_filter_scan():
            from tracer.selectors.trace_filter_reads import read_bounded_filter_page
            from tracer.services.clickhouse.query_service import QueryResult

            bounded_page = read_bounded_filter_page(
                builder=builder,
                analytics=analytics,
                filters=filters,
                key_field="trace_id",
                page_number=page_number,
                page_size=page_size,
                deadline_ms=candidate_deadline_ms,
            )
            if not bounded_page.complete:
                if bounded_page.error_code == PAGE_DEPTH_EXCEEDED_CODE:
                    logger.info(
                        "prototype_trace_list_page_depth_exceeded",
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
                    "non_observe_trace_list_bounded_read_incomplete",
                    project_id=project_id,
                    project_version_id=str(project_version_id),
                    page_number=page_number,
                    error_code=bounded_page.error_code,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Filtered trace data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            result = QueryResult(
                data=bounded_page.rows,
                row_count=len(bounded_page.rows),
                backend_used="clickhouse",
                query_time_ms=bounded_page.elapsed_ms,
            )
            total_count = bounded_page.total_rows_lower_bound
        elif bounded_error_code:
            logger.warning(
                "non_observe_trace_list_filter_unsupported",
                project_id=project_id,
                project_version_id=str(project_version_id),
                page_number=page_number,
                error_code=bounded_error_code,
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Filtered trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        else:
            query, params = builder.build()
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=read_deadline.remaining_ms(1_200),
                settings=TRACE_LIST_READ_SETTINGS,
            )

            # Prefix-dedup pagination (Phase 1 fetches the sorted prefix
            # [0, offset + 2*page_size); dedup by trace id + slice — see
            # TraceListQueryBuilder.build and page_dedup.py).
            result.data, _has_more = paginate_deduped(
                result.data, "trace_id", page_number, page_size
            )

            count_query, count_params = builder.build_count_query()
            count_result = analytics.execute_ch_query(
                count_query,
                count_params,
                timeout_ms=read_deadline.remaining_ms(1_200),
                settings=TRACE_LIST_READ_SETTINGS,
            )
            total_count = (
                count_result.data[0].get("total", 0) if count_result.data else 0
            )

        # Every page-scoped ClickHouse enrichment shares the same request wall
        # deadline and finite read settings. Content, eval, and user reads are
        # independent after page selection, so run them concurrently instead of
        # stacking the former 10s + 30s + 10s per-query timeouts.
        trace_ids = [str(row.get("trace_id", "")) for row in result.data]
        trace_user_identities = tuple(
            dict.fromkeys(
                (str(row.get("project_id") or project_id), str(row.get("trace_id")))
                for row in result.data
                if row.get("trace_id")
            )
        )
        bounded_user_resolver = getattr(
            builder, "resolve_user_ids_for_trace_identities", None
        )
        root_identities = [
            (
                str(row.get("project_id") or project_id or ""),
                str(row.get("trace_id") or ""),
                str(row.get("root_span_id") or ""),
                row.get("start_time"),
            )
            for row in result.data
            if row.get("trace_id")
            and row.get("root_span_id")
            and row.get("start_time") is not None
            and (row.get("project_id") or project_id)
        ]
        content_query, content_params = builder.build_content_query(
            trace_ids,
            root_identities=(
                root_identities if len(root_identities) == len(result.data) else None
            ),
        )
        eval_query, eval_params = builder.build_eval_query(trace_ids)
        if callable(bounded_user_resolver):
            user_query, user_params = "", {}
        else:
            user_query, user_params = builder.build_user_id_query(trace_ids)

        def _execute_project_version_enrichment(query, params):
            return analytics.execute_ch_query(
                query,
                params,
                timeout_ms=read_deadline.remaining_ms(TRACE_LIST_ENRICHMENT_TIMEOUT_MS),
                settings=TRACE_LIST_READ_SETTINGS,
            )

        tasks: dict[str, tuple[str, dict[str, Any]]] = {}
        if content_query:
            tasks["content"] = (content_query, content_params)
        if eval_query and trace_ids and eval_config_ids:
            tasks["evals"] = (eval_query, eval_params)
        if user_query:
            tasks["users"] = (user_query, user_params)

        enrichment_results: dict[str, Any] = {}
        if tasks or (callable(bounded_user_resolver) and trace_user_identities):
            pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=min(
                    TRACE_LIST_ENRICHMENT_MAX_WORKERS,
                    len(tasks) + int(callable(bounded_user_resolver)),
                )
            )
            future_names = {
                pool.submit(_execute_project_version_enrichment, query, params): name
                for name, (query, params) in tasks.items()
            }
            if callable(bounded_user_resolver) and trace_user_identities:
                user_future = pool.submit(
                    bounded_user_resolver,
                    trace_user_identities,
                    analytics,
                    settings=TRACE_LIST_READ_SETTINGS,
                    timeout_ms_provider=lambda: read_deadline.remaining_ms(
                        TRACE_LIST_ENRICHMENT_TIMEOUT_MS
                    ),
                )
                future_names[user_future] = "users"
            try:
                wait_seconds = read_deadline.remaining_ms() / 1000
                (
                    enrichment_results,
                    user_degradation,
                ) = _collect_trace_enrichment_futures(
                    future_names,
                    timeout_seconds=wait_seconds,
                )
                if user_degradation is not None:
                    logger.warning(
                        "non_observe_trace_list_user_enrichment_degraded",
                        error_type=user_degradation[0],
                        clickhouse_error_code=user_degradation[1],
                        project_id=project_id,
                        project_version_id=str(project_version_id),
                        page_number=page_number,
                    )
                if user_degradation != ("TimeoutError", None):
                    read_deadline.remaining_ms()
            except (concurrent.futures.TimeoutError, ReadDeadlineExceeded) as exc:
                logger.warning(
                    "non_observe_trace_list_enrichment_deadline_exceeded",
                    error_type=type(exc).__name__,
                    project_id=project_id,
                    project_version_id=str(project_version_id),
                    page_number=page_number,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Trace data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            except Exception as exc:
                logger.warning(
                    "non_observe_trace_list_enrichment_failed",
                    error_type=type(exc).__name__,
                    read_budget_error=is_read_budget_error(exc),
                    project_id=project_id,
                    project_version_id=str(project_version_id),
                    page_number=page_number,
                    exc_info=True,
                )
                if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
                    raise
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Trace data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

        content_result = enrichment_results.get("content")
        content_rows = content_result.data if content_result is not None else []
        for content_row in content_rows:
            content_row.setdefault("project_id", str(project_id))
        merge_content_rows(
            result.data,
            content_rows,
            id_key=("project_id", "trace_id"),
            keys=(
                "input",
                "output",
                "trace_tags",
                "attrs_string",
                "attrs_number",
                "attrs_bool",
                "attributes_extra",
            ),
        )

        eval_map = {}
        eval_result = enrichment_results.get("evals")
        if eval_result is not None:
            eval_map = builder.pivot_eval_results(
                [(list(row.values())) for row in eval_result.data],
                list(eval_result.data[0].keys()) if eval_result.data else [],
            )

        user_result = enrichment_results.get("users")
        user_id_map = {
            (
                str(row.get("project_id") or project_id),
                str(row.get("trace_id", "")),
            ): str(row["user_id"])
            for row in (user_result.data if user_result is not None else [])
            if row.get("trace_id") and row.get("user_id")
        }

        # Phase 3: Annotations — fetch from PG Score (unified annotation system)
        try:
            annotation_map = _build_annotation_map_from_scores(
                trace_ids,
                annotation_label_ids,
                label_types,
                analytics=analytics,
                project_id=str(project_id),
                start_date=builder.params.get("start_date"),
                end_date=builder.params.get("end_date"),
            )
        except AnnotationScoreReadBoundExceeded:
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        try:
            read_deadline.remaining_ms()
        except ReadDeadlineExceeded:
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )

        # Build column config
        column_config = get_default_trace_config()
        column_config = update_column_config_based_on_eval_config(
            column_config, eval_configs
        )
        column_config = update_span_column_config_based_on_annotations(
            column_config, annotation_labels
        )

        # Format response to match existing PG format
        table_data = []
        for row in result.data:
            trace_id = str(row.get("trace_id", ""))
            entry = {
                "node_type": row.get("observation_type", ""),
                "trace_id": trace_id,
                "input": row.get("input", ""),
                "output": row.get("output", ""),
                "trace_name": row.get("trace_name") or row.get("span_name") or "",
                "start_time": row.get("start_time"),
                "status": row.get("status"),
                "latency": row.get("latency_ms"),
                "total_tokens": row.get("total_tokens"),
                "prompt_tokens": row.get("prompt_tokens"),
                "completion_tokens": row.get("completion_tokens"),
                "cost": row.get("cost"),
                "model": row.get("model"),
                "provider": row.get("provider"),
                "session_id": (
                    None
                    if str(row.get("trace_session_id", "")) == NIL_UUID
                    else row.get("trace_session_id")
                ),
                "tags": row.get("trace_tags") or [],
                "user_id": user_id_map.get(
                    (str(row.get("project_id") or project_id), trace_id)
                ),
            }

            # Add eval metrics matching PG format
            trace_evals = eval_map.get(trace_id, {})
            for config in eval_configs:
                config_id = str(config.id)
                if config_id not in trace_evals:
                    continue
                flatten_eval_score_into_entry(
                    entry,
                    config_id,
                    trace_evals[config_id],
                    eval_output_type_for_config(config),
                )

            # Add annotations
            trace_annotations = annotation_map.get(trace_id, {})
            for label in annotation_labels:
                label_id = str(label.id)
                if label_id in trace_annotations:
                    entry[label_id] = trace_annotations[label_id]

            # Root-span attributes for custom columns (typed maps + attributes_extra)
            flatten_span_attributes_into_entry(entry, row)

            table_data.append(entry)

        metadata = {"total_rows": total_count}
        if bounded_page is not None:
            total_rows_is_lower_bound = not (
                bounded_page.complete and not bounded_page.has_more
            )
            metadata.update(
                {
                    "total_rows_is_lower_bound": total_rows_is_lower_bound,
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
        ) and exact_total_explicitly_required(request, query_params):
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Trace data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        response = {
            "column_config": column_config,
            "metadata": metadata,
            "table": table_data,
        }

        return self._gm.success_response(response)

    # ------------------------------------------------------------------
    # Agent Graph — aggregate topology visualization
    # ------------------------------------------------------------------

    @validated_request(
        query_serializer=TraceAgentGraphQuerySerializer,
        responses={
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def agent_graph(self, request, *args, **kwargs):
        """Return one cached exact Agent Graph and chronological Agent Path."""
        project_id = None
        try:
            query = request.validated_query_data
            project_id = str(query["project_id"])
            project = (
                _project_queryset_for_request(request).filter(id=project_id).first()
            )
            if not project:
                return self._gm.bad_request("Project not found")

            filters = bind_request_my_annotations_principal(
                request,
                list(query.get("filters") or []),
            )
            refresh = bool(query.get("refresh", False))

            result = fetch_agent_graph_ch(
                project_id=project_id,
                filters=filters,
                refresh=refresh,
            )
            return self._gm.success_response(result)

        except (UnsupportedFilterShapeError, FilterPrincipalContextError):
            return self._gm.bad_request("Agent graph filter configuration is invalid")
        except Exception as exc:
            if is_clickhouse_api_read_unavailable_error(exc):
                logger.warning(
                    "agent_graph_query_unavailable",
                    project_id=str(project_id or ""),
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Agent graph data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "agent_graph_request_failed",
                project_id=str(project_id or ""),
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Agent graph data could not be loaded",
                code="server_error",
            )


class UsersView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=UsersQuerySerializer,
        responses={200: UsersResponseSerializer, **ERROR_RESPONSES},
        # `export=true` returns text/csv; list returns JSON.
        produces=["application/json", "text/csv"],
    )
    def get(self, request, *args, **kwargs):
        """
        List traces filtered by project ID with optimized queries.
        """
        # Thin transport layer: deserialize the request, resolve the
        # request-scoped allowed projects, then delegate all query/enrichment/
        # CSV work to UsersListManager (export=true streams CSV; else JSON).
        try:
            query_data = request.validated_query_data

            # Serializer is BooleanField(default=False), so this is already a bool.
            export = query_data.get("export", False)
            search = query_data.get("search", "")

            # The historical Users CSV and sorted-numbered-page paths must
            # aggregate every matching user before producing the first row.
            # They are not safe at large-tenant scale and cannot satisfy the
            # bounded exact-read contract. Fail closed with a stable public
            # error until a preflighted asynchronous export / bounded global
            # sort is available; never start a partial 200 CSV stream.
            if export:
                return self._gm.custom_error_response(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "User export is temporarily unavailable.",
                    code="user_export_unavailable",
                )
            if query_data.get("sort_params"):
                return self._gm.custom_error_response(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "User sorting is temporarily unavailable.",
                    code="user_sort_unavailable",
                )

            try:
                page_size = int(query_data.get("page_size", 30))
                current_page = int(query_data.get("current_page_index", 0))
            except (ValueError, TypeError):
                page_size = 10
                current_page = 0

            # Workspace isolation is request-bound, so resolve the allowed
            # projects here and pass the plain list to the manager (CH25: the
            # curated source has no workspace_id column to filter on).
            manager = UsersListManager(
                organization_id=str(request.user.organization.id),
                allowed_project_ids=[
                    str(pid)
                    for pid in _project_queryset_for_request(request).values_list(
                        "id", flat=True
                    )
                ],
                project_id=query_data.get("project_id") or None,
                search=search.strip() if search else None,
                filters=query_data.get("filters", []),
                sort_params=query_data.get("sort_params", []),
                requested_columns=query_data.get("requested_columns", []),
                attribute_keys=query_data.get("attribute_keys", []),
            )

            cursor_token = query_data.get("cursor")
            cursor_requested = bool(cursor_token or query_data.get("cursor_mode"))
            if cursor_requested and query_data.get("sort_params"):
                return self._gm.custom_error_response(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Sorted user pages require numbered pagination.",
                    code="cursor_sort_unsupported",
                )
            if cursor_requested:
                cursor_scope = cursor_scope_for_request(
                    request,
                    project_ids=manager.scoped_project_ids,
                )
                cursor_query = {
                    "project_id": str(query_data.get("project_id") or ""),
                    "search": manager.search or "",
                    "filters": manager.filters,
                    "sort_params": manager.sort_params,
                }
                cursor_state = None
                if cursor_token:
                    cursor_state = decode_list_cursor(
                        cursor_token,
                        resource="observe_users",
                        scope=cursor_scope,
                        query=cursor_query,
                        page_size=page_size,
                    )
                cursor_read = manager.list_cursor_payload(
                    page_size=page_size,
                    cursor=cursor_state,
                )
                next_cursor = None
                if cursor_read.has_more:
                    if cursor_read.checkpoint_order is None:
                        raise RuntimeError(
                            "user cursor page omitted its scan checkpoint"
                        )
                    next_cursor = encode_list_cursor(
                        resource="observe_users",
                        scope=cursor_scope,
                        query=cursor_query,
                        page_size=page_size,
                        window_start=cursor_read.window_start,
                        window_end=cursor_read.window_end,
                        order=cursor_read.checkpoint_order,
                        seen_rows=cursor_read.seen_rows,
                    )
                payload = dict(cursor_read.payload)
                payload["next_cursor"] = next_cursor
                return self._gm.success_response(payload)

            payload = manager.list_payload(
                page_size=page_size, current_page=current_page
            )
            return self._gm.success_response(payload)

        except ListCursorError as exc:
            return self._gm.custom_error_response(
                status.HTTP_400_BAD_REQUEST, str(exc), code=exc.code
            )
        except UnsupportedBoundedUserListQuery:
            # A globally sorted page over a derived metric requires evaluating
            # every matching user before LIMIT.  The bounded cursor path cannot
            # preserve that contract, so fail explicitly instead of leaking a
            # programming-style 500 or silently returning a page-local sort.
            return self._gm.custom_error_response(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "This user sort is not available for the selected filters. Clear the sort and retry.",
                code="user_sort_unsupported",
            )
        except Exception as exc:
            if is_clickhouse_api_read_unavailable_error(exc):
                logger.warning(
                    "users_list_query_unavailable",
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "User data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception("users_list_failed", error_type=type(exc).__name__)
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "User data could not be loaded",
                code="server_error",
            )


class GetUserCodeExampleView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={200: UserCodeExampleResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, *args, **kwargs):
        project_name = "New Project"
        project_id = request.GET.get("project_id")
        if project_id:
            project = get_object_or_404(Project, id=project_id)
            project_name = project.name
            project_type = project.trace_type
            if project_type != "observe":
                return self._gm.bad_request("Project type must be 'observe'.")

        code_example = f"""import openai
from fi_instrumentation import using_attributes
from traceai_openai import OpenAIInstrumentor

trace_provider = register(
    project_type=ProjectType.OBSERVE,
    project_name="{project_name}",
    session_name="new-session",
)

tracer = FITracer(trace_provider.get_tracer(__name__))
OpenAIInstrumentor().instrument(tracer_provider=trace_provider)

client = openai.OpenAI()

with using_attributes(
    session_id="new-session",
    user_id="newuser@example.com",
):
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{{"role": "user", "content": "Write a haiku."}}],
        max_tokens=20,
    )
        """
        return self._gm.success_response(code_example)
