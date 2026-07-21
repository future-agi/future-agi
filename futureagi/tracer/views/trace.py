import csv
import io
import json
import math
import traceback
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
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers
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
from tracer.serializers.filters import (
    ObserveGraphDataRequestSerializer,
    ObserveGraphDataResponseSerializer,
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
    UserCodeExampleResponseSerializer,
    UsersQuerySerializer,
    UsersResponseSerializer,
)
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.graph_dispatch import (
    fetch_annotation_graph_ch,
    fetch_eval_graph_ch,
    fetch_system_metric_graph_ch,
)
from tracer.services.clickhouse.page_dedup import paginate_deduped
from tracer.services.clickhouse.query_builders import (
    AgentGraphQueryBuilder,
)
from tracer.services.clickhouse.query_builders.base import NIL_UUID
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.span_selectors import (
    flatten_span_attributes_into_entry,
    merge_content_rows,
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
        return AgentGraphQueryBuilder._make_node_id(
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


def _build_annotation_map_from_scores(
    trace_ids, annotation_label_ids, label_types, span_trace_map=None
):
    """Fetch annotation values from PG Score table and build annotation_map.

    Always reads from PG to guarantee read-after-write consistency —
    annotations are written to PG first and CDC replication to ClickHouse
    may lag, causing newly created annotations to be invisible.

    Returns:
        Dict mapping trace_id -> label_id -> structured annotation data
        matching the format produced by build_annotation_subqueries (PG ORM path).
    """
    if not trace_ids or not annotation_label_ids:
        return {}
    if span_trace_map is None:
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        span_trace_map = AnalyticsQueryService().get_span_trace_map(trace_ids)
    return _build_annotation_map_from_scores_pg(
        trace_ids, annotation_label_ids, label_types, span_trace_map
    )


def _build_annotation_map_from_scores_ch(trace_ids, annotation_label_ids, label_types):
    """ClickHouse implementation of annotation map builder."""
    import json

    from accounts.models.user import User
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

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
    trace_ids, annotation_label_ids, label_types, span_trace_map=None
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
    span_ids = list(span_trace_map.keys())
    annotation_map = {}
    # Trace- or span-linked scores by column id (no dropped-table JOIN).
    scores = Score.objects.filter(
        Q(trace_id__in=trace_ids) | Q(observation_span_id__in=span_ids),
        label_id__in=annotation_label_ids,
        deleted=False,
    ).select_related("annotator")

    for s in scores:
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
        responses={200: TraceDetailResponseSerializer, **ERROR_RESPONSES},
    )
    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve a trace by its ID.
        """
        try:
            trace_id = kwargs.get("pk")
            from tracer.services.clickhouse.v2.dispatch import (
                get_query_builder_class,
            )

            HandlerCls = get_query_builder_class("TRACE_DETAIL")
            handler = HandlerCls(
                view=self,
                request=request,
                pk=trace_id,
                analytics=AnalyticsQueryService(),
            )
            return self._gm.success_response(handler.fetch())
        except Exception as e:
            logger.exception(f"Error in fetching the trace: {str(e)}")
            return self._gm.bad_request(
                f"error retrieving trace {get_error_message('ERROR_GETTING_TRACE')}"
            )

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

        except Exception as e:
            return self._gm.bad_request(f"Failed to fetch properties: {str(e)}")

    @action(detail=False, methods=["get"])
    def get_eval_names(self, request, *args, **kwargs):
        """
        Fetch all evaluation template names.
        """
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
            analytics = AnalyticsQueryService()
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

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(f"Failed to fetch evaluation names: {str(e)}")

    @validated_request(query_serializer=TraceListQuerySerializer)
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
                raise Exception("Project version not found")  # noqa: B904

            # CH-only path post-migration. D-027: the previous PG fallback
            # body (Trace.objects.filter + 6 ObservationSpan Subquery
            # annotations for node_type / trace_name / span_attributes /
            # start_time / status + per-config EvalLogger metric pivot +
            # build_annotation_subqueries + 4-stage filter combinator +
            # Python pivot) was deleted. CH path lives in
            # _list_traces_clickhouse via TraceListQueryBuilder. If
            # TRACE_LIST isn't routed to CH that's a config error —
            # surface it as 400.
            analytics = AnalyticsQueryService()
            return self._list_traces_clickhouse(
                request, project_version_id, analytics, query_params
            )

        except Exception as e:
            logger.exception(f"Error in fetching the traces list: {str(e)}")

            return self._gm.bad_request(
                f"error fetching the traces list {get_error_message('ERROR_GETTING_TRACE_LIST')}"
            )

    @validated_request(
        request_serializer=ObserveGraphDataRequestSerializer,
        responses={200: ObserveGraphDataResponseSerializer},
    )
    @action(detail=False, methods=["post"])
    def get_graph_methods(self, request, *args, **kwargs):
        """
        Fetch data for the observe graph with optimized queries
        """
        try:
            body = request.validated_data
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
            filters = body["filters"]
            interval = body["interval"]
            req_data_config = body["req_data_config"]

            type = req_data_config.get("type", None)
            if type not in ["EVAL", "ANNOTATION", "SYSTEM_METRIC"]:
                return self._gm.bad_request("Filter property type is not valid")

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
            analytics = AnalyticsQueryService()
            if type == "SYSTEM_METRIC":
                return self._gm.success_response(
                    fetch_system_metric_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        metric_id=req_data_config.get("id", "latency"),
                    )
                )
            elif type == "EVAL":
                return self._gm.success_response(
                    fetch_eval_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        req_data_config=req_data_config,
                    )
                )
            elif type == "ANNOTATION":
                return self._gm.success_response(
                    fetch_annotation_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        req_data_config=req_data_config,
                        observe_type="trace",
                    )
                )
            return self._gm.bad_request("Filter property type is not valid")

        except Exception as e:
            logger.exception(f"Error in get_graph_methods: {str(e)}")
            return self._gm.bad_request(f"Error fetching graph data: {str(e)}")

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
                                ] = (
                                    metric["custom_eval_config__name"] + " - " + choice
                                )
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

        except Exception as e:
            logger.exception(f"Error in fetching the trace id by index: {str(e)}")

            return self._gm.bad_request(
                f"error fetching the trace id by index {str(e)}"
            )

    @validated_request(
        query_serializer=TraceObserveListQuerySerializer,
        responses={200: TraceObserveListResponseSerializer, **ERROR_RESPONSES},
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

            validated_data = request.validated_query_data
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
                    raise Exception("Project should be of type observe or experiment")
                org_project_ids = None

            # CH-only path post-migration. D-027: the previous PG fallback
            # body (Trace.objects + _root_span_qs / _all_span_qs /
            # _end_user_span_qs Subquery annotations + per-config EvalLogger
            # metric pivot + build_annotation_subqueries + 4-stage filter
            # combinator + Python pivot) was deleted. CH path lives in
            # _list_traces_of_session_clickhouse via TraceListQueryBuilder.
            # If TRACE_OF_SESSION_LIST isn't routed to CH that's a config
            # error — surface as 400. (NOTE: the legacy PG path supported
            # export=True by skipping pagination; the CH path always
            # paginates. Export of traces-of-session beyond the first page
            # is unsupported post-migration — feature parity tracked as a
            # follow-up if needed.)
            analytics = AnalyticsQueryService()
            return self._list_traces_of_session_clickhouse(
                request,
                project_id,
                validated_data,
                analytics,
                org_project_ids=org_project_ids,
                org=org,
            )

        except Exception as e:
            logger.exception(f"Error in fetching the traces list of observe: {str(e)}")

            return self._gm.bad_request(
                f"error fetching the traces list of observe {str(e)}"
            )

    @action(detail=False, methods=["get"])
    def list_voice_calls(self, request, *args, **kwargs):
        """
        List voice/conversation traces for a project in an optimized way and
        return a response similar to the provided call object schema.

        Query params:
        - project_id (required)
        - page (1-based, optional, default 1)
        - page_size (optional, default 30)
        """
        try:
            serializer = TraceVoiceCallListQuerySerializer(data=request.query_params)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
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
            analytics = AnalyticsQueryService()
            return self._list_voice_calls_clickhouse(
                request,
                project_id,
                validated_data,
                remove_simulation_calls,
                analytics,
            )

        except NotFound:
            raise
        except ValueError as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception(f"Error in fetching voice calls list: {str(e)}")
            return self._gm.bad_request("Failed to fetch voice calls")

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

            # Resolve the trace's project from CH and validate ownership. PG
            # `tracer_trace` is dropped on CH25, so the project comes from the CH
            # `traces` row and ownership is checked against the still-present
            # `tracer_project`.
            analytics = AnalyticsQueryService()
            proj_result = analytics.execute_ch_query(
                "SELECT toString(project_id) AS project_id FROM traces "
                "WHERE id = toUUID(%(trace_id)s) AND is_deleted = 0 LIMIT 1",
                {"trace_id": str(trace_id)},
                timeout_ms=10000,
            )
            if not proj_result.data:
                return self._gm.not_found("trace_id not found")
            project_id = proj_result.data[0]["project_id"]
            if not Project.objects.filter(
                id=project_id,
                organization_id=request.user.organization_id,
            ).exists():
                return self._gm.not_found("trace_id not found")

            # ClickHouse-only path: span data lives in CH, not PG (PLAN_V2_NO_CDC).
            return self._voice_call_detail_clickhouse(
                request, trace_id, analytics, project_id
            )
        except Exception as e:
            logger.exception("voice_call_detail_error", error=str(e))
            return self._gm.bad_request("error fetching voice call detail")

    def _voice_call_detail_clickhouse(self, request, trace_id, analytics, project_id):
        """Return heavy voice-call detail fields from ClickHouse."""
        from tracer.services.clickhouse.query_builders.trace_list import (
            TraceListQueryBuilder,
        )

        # 1. Fetch root conversation span for this trace
        root_query = """
        SELECT
            id AS span_id,
            project_id,
            trace_id,
            observation_type,
            status,
            start_time,
            end_time,
            latency_ms,
            provider,
            attributes_extra,
            -- `eval_attributes` lives on the LANDING table `tracer_observation_span`,
            -- not on the denormalized `spans` table the dashboard reads. The
            -- original query referenced it and CH errored with
            -- `Unknown expression identifier eval_attributes`. The actual eval
            -- output comes from the EvalLogger rows we already load further
            -- down — eval_attrs here was only used by simulation_context as a
            -- fallback, and that fallback resolves to {} on this path.
            attrs_string,
            attrs_number,
            attrs_bool,
            toJSONString(metadata) AS metadata_json
        FROM spans
        WHERE project_id = toUUID(%(project_id)s)
          AND trace_id = %(trace_id)s
          AND is_deleted = 0
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND observation_type = 'conversation'
        LIMIT 1
        """
        root_result = analytics.execute_ch_query(
            root_query,
            {"trace_id": str(trace_id), "project_id": project_id},
            timeout_ms=10000,
        )
        if not root_result.data:
            return self._gm.not_found("No conversation root span found in CH")

        row = root_result.data[0]
        provider = row.get("provider") or "vapi"

        # Parse attributes_extra to get raw_log
        span_attrs_raw = row.get("attributes_extra", "{}")
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
            organization_id=request.user.organization_id,
            span_attributes=span_attrs,
            eval_attributes=eval_attrs,
            raw_log=raw_log,
            metadata=metadata,
            processed_log=processed_log,
        )
        voice_metrics = self._extract_voice_turn_and_talk_metrics(span_attrs, raw_log)

        attr_str = row.get("attrs_string") or {}
        recording = self._build_recording_dict(attr_str)

        # 2. Fetch child spans
        child_query = """
        SELECT
            id,
            trace_id,
            name,
            observation_type,
            status,
            start_time,
            end_time,
            latency_ms,
            model,
            provider,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            cost,
            input,
            output,
            parent_span_id,
            attributes_extra,
            attrs_string,
            attrs_number,
            attrs_bool,
            toJSONString(metadata) AS metadata_json,
            status_message,
            tags
        FROM spans
        WHERE project_id = toUUID(%(project_id)s)
          AND trace_id = %(trace_id)s
          AND is_deleted = 0
          AND parent_span_id IS NOT NULL
        ORDER BY start_time ASC
        LIMIT 1 BY id
        """
        child_result = analytics.execute_ch_query(
            child_query,
            {"trace_id": str(trace_id), "project_id": project_id},
            timeout_ms=10000,
        )

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

        for child in child_result.data:
            child_attrs_raw = child.get("attributes_extra", "{}")
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

        # All non-deleted eval configs for the project so the drawer renders
        # the same set of evals as the list columns; missing scores become
        # placeholder entries with `output=None`. Read from PG (indexed) —
        # replaces the unbounded CH dictGet discovery scan.
        eval_configs, eval_config_ids = get_project_eval_configs(project_id)

        eval_outputs = {}
        trace_evals: dict[str, Any] = {}
        if eval_config_ids:
            # Reuse the list builder's eval query so the detail view stays in
            # parity with the voice-call list (same completed-only aggregation
            # + per-status counts) instead of a drifting parallel query. The
            # builder gates aggregates on ``status = 'completed'`` so pending /
            # running / skipped rows no longer contaminate the average, and the
            # shared pivot emits a ``{"status": ...}`` marker for them.
            eval_builder = TraceListQueryBuilder(
                project_id=str(project_id),
                eval_config_ids=eval_config_ids,
            )
            eval_query, eval_params = eval_builder.build_eval_query([str(trace_id)])
            if eval_query:
                eval_result = analytics.execute_ch_query(
                    eval_query, eval_params, timeout_ms=30000
                )
                eval_map = TraceListQueryBuilder.pivot_eval_results(
                    [list(r.values()) for r in eval_result.data],
                    list(eval_result.data[0].keys()) if eval_result.data else [],
                )
                trace_evals = eval_map.get(str(trace_id), {}) or {}

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
                elif "avg_score" in scores:
                    score_val = scores.get("avg_score") or scores.get("pass_rate")
                    if output_type == "Pass/Fail":
                        metric_entry["output"] = (
                            "Pass" if score_val and score_val > 0 else "Fail"
                        )
                    else:
                        metric_entry["output"] = (
                            round(score_val * 100, 2)
                            if isinstance(score_val, (int, float))
                            else score_val
                        )
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
        """CH path: get prev/next trace IDs using the spans table."""
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        fb = ClickHouseFilterBuilder(table="spans")
        extra_where, extra_params = fb.translate(filters)

        # Parse date range from filters.  The drawer does not forward the
        # date-range picker value, so we fall back to 1 year to avoid
        # excluding the current trace.
        from datetime import datetime, timedelta

        start_date, end_date = BaseQueryBuilder.parse_time_range(filters)
        has_explicit_date = any(
            f.get("column_id") in ("created_at", "start_time") for f in filters
        )
        if not has_explicit_date:
            start_date = datetime.utcnow() - timedelta(days=365)
            end_date = datetime.utcnow()

        params = {
            "project_id": str(project_id),
            "trace_id": str(trace_id),
            "start_date": start_date,
            "end_date": end_date,
        }
        params.update(extra_params)

        time_filter = "AND start_time >= %(start_date)s AND start_time < %(end_date)s"
        filter_fragment = f"AND {extra_where}" if extra_where else ""

        # Get current trace's start_time
        current_query = f"""
        SELECT start_time
        FROM spans
        WHERE project_id = toUUID(%(project_id)s)
          AND is_deleted = 0
          AND trace_id = %(trace_id)s
          AND (parent_span_id IS NULL OR parent_span_id = '')
          {time_filter}
          {filter_fragment}
        ORDER BY start_time DESC
        LIMIT 1
        """
        current_result = analytics.execute_ch_query(
            current_query, params, timeout_ms=30000
        )
        if not current_result.data:
            return self._gm.bad_request("Trace not found")

        current_start_time = current_result.data[0]["start_time"]
        params["current_start_time"] = current_start_time

        # Previous trace (newer by time — "next in line")
        prev_query = f"""
        SELECT trace_id
        FROM spans
        WHERE project_id = toUUID(%(project_id)s)
          AND is_deleted = 0
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND trace_id != %(trace_id)s
          AND start_time <= %(current_start_time)s
          {time_filter}
          {filter_fragment}
        ORDER BY start_time DESC
        LIMIT 1 BY trace_id
        LIMIT 1
        """
        prev_result = analytics.execute_ch_query(prev_query, params, timeout_ms=30000)
        previous_trace = prev_result.data[0]["trace_id"] if prev_result.data else None

        # Next trace (older by time)
        next_query = f"""
        SELECT trace_id
        FROM spans
        WHERE project_id = toUUID(%(project_id)s)
          AND is_deleted = 0
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND trace_id != %(trace_id)s
          AND start_time >= %(current_start_time)s
          {time_filter}
          {filter_fragment}
        ORDER BY start_time ASC
        LIMIT 1 BY trace_id
        LIMIT 1
        """
        next_result = analytics.execute_ch_query(next_query, params, timeout_ms=30000)
        next_trace = next_result.data[0]["trace_id"] if next_result.data else None

        response = {
            "next_trace_id": str(previous_trace) if previous_trace else None,
            "previous_trace_id": str(next_trace) if next_trace else None,
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
            analytics = AnalyticsQueryService()
            return self._get_trace_id_by_index_observe_clickhouse(
                request, trace_id, project_id, filters, analytics
            )

        except Exception as e:
            return self._gm.bad_request(
                f"error fetching the trace id by index {str(e)}"
            )

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

        Builder class resolved via v1↔v2 dispatch — set
        CH25_QUERY_TYPES_V2_PRIMARY=TRACE_LIST to flip to CH 25.3.
        """
        from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

        BuilderCls = get_query_builder_class("TRACE_LIST")  # noqa: N806

        org_scope = bool(org_project_ids)
        filters = list(validated_data.get("filters", []) or [])
        page_number = validated_data["page_number"]
        page_size = validated_data["page_size"]
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

        # Get eval config IDs. Project mode uses a CH dict-lookup (fast);
        # org mode uses a PG scan because the CH dict-lookup takes a single
        # project_id — multi-project CH variant not implemented yet.
        eval_config_ids = []
        if org_scope:
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    trace_id__in=Trace.objects.filter(
                        project_id__in=org_project_ids
                    ).values("id")
                )
                .values("custom_eval_config_id")
                .distinct(),
                deleted=False,
            ).select_related("eval_template")
            eval_config_ids = [str(c.id) for c in eval_configs]
        else:
            # PERF: resolve this project's configs from PG first (indexed by
            # the project FK), then ask CH which of them have recent data via
            # a ``custom_eval_config_id IN (…)`` scope — the eval table's
            # leading sort key, so CH prunes to just those configs. The old
            # inline query ran ``FINAL`` over the ENTIRE eval table plus a
            # per-row ``dictGet('trace_dict', 'project_id', …)`` call — a
            # full-table merge + dictionary lookup per eval row that
            # OOM-crashed the server at tens of millions of eval rows. See
            # AnalyticsQueryService.get_eval_config_ids_with_data_ch.
            project_configs = list(
                CustomEvalConfig.objects.filter(
                    project_id=project_id, deleted=False
                ).select_related("eval_template")
            )
            candidate_ids = [str(c.id) for c in project_configs]
            # Discover eval columns over the requested window (cover
            # [start, now]), not a fixed 30 days — so configs with data anywhere
            # in the viewed range keep their columns. Bounded by candidate ids.
            window_days = BuilderCls.window_days_covering(filters)
            ids_with_data = (
                set(
                    analytics.get_eval_config_ids_with_data_ch(
                        str(project_id),
                        timeout_ms=30000,
                        candidate_config_ids=candidate_ids,
                        window_days=window_days,
                    )
                )
                if candidate_ids
                else set()
            )
            eval_configs = [c for c in project_configs if str(c.id) in ids_with_data]
            eval_config_ids = [str(c.id) for c in eval_configs]

        # Annotation labels — skip in org-scoped mode (deferred enhancement)
        if org_scope:
            annotation_labels = []
        else:
            annotation_labels = get_annotation_labels_for_project(project_id)
        annotation_label_ids = [str(label.id) for label in annotation_labels]
        label_types = {str(label.id): label.type for label in annotation_labels}

        builder = BuilderCls(
            project_id=None if org_scope else str(project_id),
            project_ids=[str(p) for p in org_project_ids] if org_scope else None,
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            eval_config_ids=eval_config_ids,
            annotation_label_ids=annotation_label_ids,
        )

        # Phase 1: Paginated traces (light columns only — no input/output)
        query, params = builder.build()
        result = analytics.execute_ch_query(query, params, timeout_ms=10000)

        # Prefix-dedup pagination: Phase 1 dropped `LIMIT 1 BY trace_id` (its
        # O(roots-in-window) full sort OOM-crashed CH — see
        # TraceListQueryBuilder.build) and instead fetched the sorted prefix
        # [0, offset + 2*page_size). De-dup the prefix by trace id and slice
        # the page — every page is a disjoint slice of the same globally
        # de-duplicated stream, so a trace (even a multi-root one whose roots
        # sort pages apart) can never appear on two pages and none is
        # skipped. See page_dedup.py.
        result.data, _has_more = paginate_deduped(
            result.data, "trace_id", page_number, page_size
        )

        # Count
        count_query, count_params = builder.build_count_query()
        count_result = analytics.execute_ch_query(
            count_query, count_params, timeout_ms=30000
        )
        total_count = count_result.data[0].get("total", 0) if count_result.data else 0

        # Phase 1b: Fetch heavy columns (input/output/attrs) for the page
        trace_ids = [str(row.get("trace_id", "")) for row in result.data]
        content_rows = []
        if trace_ids:
            content_query, content_params = builder.build_content_query(trace_ids)
            if content_query:
                content_result = analytics.execute_ch_query(
                    content_query, content_params, timeout_ms=10000
                )
                content_rows = content_result.data
        content_map = merge_content_rows(
            result.data,
            content_rows,
            id_key="trace_id",
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
            content = content_map.get(str(row.get("trace_id", "")), {})
            raw_meta = content.get("metadata", "{}")
            if isinstance(raw_meta, str):
                try:
                    row["metadata"] = json.loads(raw_meta)
                except (json.JSONDecodeError, TypeError):
                    row["metadata"] = {}
            else:
                row["metadata"] = raw_meta or {}

        user_id_map = builder.resolve_user_ids(trace_ids, analytics)

        # Phase 2: Eval scores
        eval_map = {}
        if trace_ids and eval_config_ids:
            eval_query, eval_params = builder.build_eval_query(trace_ids)
            if eval_query:
                eval_result = analytics.execute_ch_query(
                    eval_query, eval_params, timeout_ms=30000
                )
                eval_map = builder.pivot_eval_results(
                    [(list(row.values())) for row in eval_result.data],
                    list(eval_result.data[0].keys()) if eval_result.data else [],
                )

        # Phase 3: Annotations — PG values, span->trace resolved via CH.
        span_trace_map = analytics.get_span_trace_map(trace_ids) if trace_ids else {}
        annotation_map = _build_annotation_map_from_scores(
            trace_ids, annotation_label_ids, label_types, span_trace_map
        )

        # Phase 4: Aggregated span attributes for custom columns
        _SKIP_ATTR_PREFIXES = (
            "raw.",
            "llm.input_messages",
            "llm.output_messages",
            "input.value",
            "output.value",
        )
        aggregated_attrs = {}  # trace_id -> {attr_key -> [unique_values]}
        if trace_ids:
            try:
                attr_query, attr_params = builder.build_span_attributes_query(trace_ids)
                if attr_query:
                    attr_result = analytics.execute_ch_query(
                        attr_query, attr_params, timeout_ms=30000
                    )
                    for attr_row in attr_result.data:
                        tid = str(attr_row.get("trace_id", ""))
                        raw = attr_row.get("attributes_extra", "{}")
                        try:
                            attrs = (
                                json.loads(raw) if isinstance(raw, str) else (raw or {})
                            )
                        except (json.JSONDecodeError, TypeError):
                            attrs = {}
                        # Fallback: merge from typed Map columns when raw is empty
                        if not attrs:
                            str_map = attr_row.get("attrs_string") or {}
                            num_map = attr_row.get("attrs_number") or {}
                            if isinstance(str_map, dict):
                                attrs.update(str_map)
                            if isinstance(num_map, dict):
                                for k, v in num_map.items():
                                    if k not in attrs:
                                        attrs[k] = v
                        if tid not in aggregated_attrs:
                            aggregated_attrs[tid] = {}
                        for key, value in attrs.items():
                            if key.startswith(_SKIP_ATTR_PREFIXES):
                                continue
                            if isinstance(value, str) and len(value) > 500:
                                continue
                            if key not in aggregated_attrs[tid]:
                                aggregated_attrs[tid][key] = (
                                    set()
                                    if isinstance(value, (str, int, float, bool))
                                    else []
                                )
                            if isinstance(value, (str, int, float, bool)):
                                aggregated_attrs[tid][key].add(
                                    value
                                    if not isinstance(value, bool)
                                    else str(value).lower()
                                )
                            elif isinstance(value, (list, dict)):
                                pass  # skip complex values for aggregation
            except Exception as e:
                logger.warning(f"Span attribute aggregation failed: {e}")

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
                "user_id": user_id_map.get(trace_id),
            }

            # Add eval metrics
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

            # Include metadata for custom columns
            metadata = row.get("metadata") or {}
            if isinstance(metadata, dict):
                for key, value in metadata.items():
                    if key not in entry:
                        if isinstance(value, str) and len(value) > 500:
                            entry[key] = value[:500] + "..."
                        else:
                            entry[key] = value

            # Include aggregated span attributes — single value or array of unique values
            trace_attrs = aggregated_attrs.get(trace_id, {})
            for key, values in trace_attrs.items():
                if key not in entry:
                    if isinstance(values, set):
                        vals = sorted(values, key=str)
                        entry[key] = vals[0] if len(vals) == 1 else vals
                    else:
                        entry[key] = values

            table_data.append(entry)

        response = {
            "metadata": {"total_rows": total_count},
            "table": _sanitize_nonfinite_floats(table_data),
            "config": column_config,
        }

        return self._gm.success_response(response)

    def _list_voice_calls_clickhouse(
        self, request, project_id, validated_data, remove_simulation_calls, analytics
    ):
        """List voice calls using ClickHouse backend.

        Builder classes resolved via v1↔v2 dispatch — flip with
        CH25_QUERY_TYPES_V2_PRIMARY=VOICE_CALL_LIST,TRACE_LIST.
        """
        from tracer.services.clickhouse.query_builders import VoiceCallListQueryBuilder
        from tracer.services.clickhouse.query_builders.trace_list import (
            TraceListQueryBuilder,
        )
        from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

        VoiceBuilderCls = get_query_builder_class("VOICE_CALL_LIST")  # noqa: N806

        filters = validated_data.get("filters", [])
        page = validated_data.get("page", 1)
        page_size = validated_data.get("page_size", 30)
        page_number = page - 1  # Convert 1-based to 0-based

        # Eval configs for the project, from PG (indexed) — replaces the
        # unbounded CH dictGet discovery scan.
        eval_configs, eval_config_ids = get_project_eval_configs(project_id)

        # Get annotation labels that have actual annotations/scores for this project
        annotation_labels = get_annotation_labels_for_project(project_id)
        annotation_label_ids = [str(label.id) for label in annotation_labels]
        label_types = {str(label.id): label.type for label in annotation_labels}

        sim_flag = remove_simulation_calls and str(
            remove_simulation_calls
        ).lower() not in ("false", "0", "")

        builder = VoiceBuilderCls(
            project_id=str(project_id),
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            eval_config_ids=eval_config_ids,
            remove_simulation_calls=sim_flag,
            annotation_label_ids=annotation_label_ids,
        )

        # Phase 1: Paginated root conversation spans (light columns only)
        query, params = builder.build()
        result = analytics.execute_ch_query(query, params, timeout_ms=10000)
        result.data = result.data[:page_size]

        # Phase 1b: Fetch span_attributes + provider for the paginated spans
        # from the v2 `spans` table (CH25 close-out: was `tracer_observation_span`
        # CDC mirror). fi-collector populates three sources, in priority order:
        #   1. `attributes_extra` JSON — overflow keys that didn't match the
        #      typed-Map classifier.
        #   2. `attrs_string` / `attrs_number` / `attrs_bool` Maps — the
        #      common-case typed attributes (gen_ai.* keys for LLM spans).
        # We SELECT all three and reconstruct the flat dict on the Python
        # side, matching the pattern used by the trace-tree fetch above
        # (~line 1195). `FINAL` collapses ReplacingMergeTree duplicates;
        # the `idx_id` bloom filter keeps the PREWHERE scan cheap.
        page_rows = result.data[:page_size]
        span_ids = [
            str(row.get("span_id", "")) for row in page_rows if row.get("span_id")
        ]
        attrs_map = {}
        if span_ids:
            attrs_result = analytics.execute_ch_query(
                "SELECT id, provider, "
                "attributes_extra AS span_attributes, "
                "attrs_string, attrs_number, attrs_bool "
                "FROM spans FINAL "
                "PREWHERE id IN %(span_ids)s "
                "WHERE is_deleted = 0",
                {"span_ids": tuple(span_ids)},
                timeout_ms=10000,
            )
            for arow in attrs_result.data:
                sid = str(arow.get("id", ""))
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
                attrs_map[sid] = {
                    "span_attributes": parsed,
                    "provider": arow.get("provider"),
                }

        # Count
        count_query, count_params = builder.build_count_query()
        count_result = analytics.execute_ch_query(
            count_query, count_params, timeout_ms=30000
        )
        total_count = count_result.data[0].get("total", 0) if count_result.data else 0

        trace_ids = [str(row.get("trace_id", "")) for row in page_rows]

        # Phase 2: Eval scores
        eval_map = {}
        if trace_ids and eval_config_ids:
            eval_query, eval_params = builder.build_eval_query(trace_ids)
            if eval_query:
                eval_result = analytics.execute_ch_query(
                    eval_query, eval_params, timeout_ms=30000
                )
                eval_map = TraceListQueryBuilder.pivot_eval_results(
                    [(list(row.values())) for row in eval_result.data],
                    list(eval_result.data[0].keys()) if eval_result.data else [],
                )

        # Phase 3: Annotations — fetch from PG Score (unified annotation system)
        annotation_map = _build_annotation_map_from_scores(
            trace_ids, annotation_label_ids, label_types
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
            span_id = str(row.get("span_id", ""))
            provider = row.get("provider") or "vapi"

            # Get span_attributes from CH CDC table (Phase 1b)
            attr_row = attrs_map.get(span_id, {})
            span_attrs = attr_row.get("span_attributes") or {}
            provider = attr_row.get("provider") or provider

            # Post-filter simulator calls in Python (can't do in CH without OOM)
            if sim_flag and VoiceCallListQueryBuilder.is_simulator_call(
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

            # Include span attributes for custom columns (skip heavy/nested values)
            for key, value in span_attrs.items():
                if key in ("raw_log", "call") or key in entry:
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
        response_data = {
            "count": total_count,
            "total_pages": total_pages,
            "current_page": page,
            "next": None,
            "previous": None,
            "results": results,
            "config": column_config,
        }
        if page < total_pages:
            response_data["next"] = page + 1
        if page > 1:
            response_data["previous"] = page - 1

        from rest_framework.response import Response

        return Response(response_data)

    def _list_traces_clickhouse(
        self, request, project_version_id, analytics, query_params
    ):
        """List traces using ClickHouse backend.

        v1↔v2 dispatch — flips with CH25_QUERY_TYPES_V2_PRIMARY=TRACE_LIST.
        """
        from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

        BuilderCls = get_query_builder_class("TRACE_LIST")  # noqa: N806

        filters = query_params["filters"]
        sort_params = query_params["sort_params"]
        page_number = query_params["page_number"]
        page_size = query_params["page_size"]

        # Get project_id from project_version
        project_version = ProjectVersion.objects.get(
            id=project_version_id,
            project__organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
        )
        project_id = str(project_version.project_id)

        # PERF: resolve this project's configs from PG first (indexed by the
        # project FK), then ask CH which of them have recent data via a
        # ``custom_eval_config_id IN (…)`` scope — the eval table's leading
        # sort key. The old inline query ran ``FINAL`` over the ENTIRE eval
        # table plus a per-row ``dictGet('trace_dict', …)`` — a full-table
        # merge that OOM-crashed the server at tens of millions of eval rows.
        # See AnalyticsQueryService.get_eval_config_ids_with_data_ch.
        project_configs = list(
            CustomEvalConfig.objects.filter(
                project_id=project_id, deleted=False
            ).select_related("eval_template")
        )
        candidate_ids = [str(c.id) for c in project_configs]
        # Discover eval columns over the requested window (cover [start, now]),
        # not a fixed 30 days — so configs with data anywhere in the viewed range
        # keep their columns. Bounded by candidate ids.
        window_days = BuilderCls.window_days_covering(filters)
        ids_with_data = (
            set(
                analytics.get_eval_config_ids_with_data_ch(
                    str(project_id),
                    timeout_ms=30000,
                    candidate_config_ids=candidate_ids,
                    window_days=window_days,
                )
            )
            if candidate_ids
            else set()
        )
        eval_configs = [c for c in project_configs if str(c.id) in ids_with_data]
        eval_config_ids = [str(c.id) for c in eval_configs]

        # Get annotation labels that have actual annotations for this project
        annotation_labels = get_annotation_labels_for_project(
            project_version.project_id
        )
        annotation_label_ids = [str(label.id) for label in annotation_labels]
        label_types = {str(label.id): label.type for label in annotation_labels}

        builder = BuilderCls(
            project_id=project_id,
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            sort_params=sort_params,
            eval_config_ids=eval_config_ids,
            project_version_id=str(project_version_id),
        )

        # Phase 1: Get paginated traces
        query, params = builder.build()
        result = analytics.execute_ch_query(query, params, timeout_ms=10000)

        # Prefix-dedup pagination (Phase 1 fetches the sorted prefix
        # [0, offset + 2*page_size); dedup by trace id + slice — see
        # TraceListQueryBuilder.build and page_dedup.py).
        result.data, _has_more = paginate_deduped(
            result.data, "trace_id", page_number, page_size
        )

        # Phase 1b: heavy columns (input/output + root-span attrs) for the page.
        # Root-span attrs feed custom columns; without this merge they render "-".
        page_trace_ids = [str(row.get("trace_id", "")) for row in result.data]
        if page_trace_ids:
            content_query, content_params = builder.build_content_query(page_trace_ids)
            if content_query:
                content_result = analytics.execute_ch_query(
                    content_query, content_params, timeout_ms=10000
                )
                merge_content_rows(
                    result.data,
                    content_result.data,
                    id_key="trace_id",
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

        # Get count
        count_query, count_params = builder.build_count_query()
        count_result = analytics.execute_ch_query(
            count_query, count_params, timeout_ms=30000
        )
        total_count = count_result.data[0].get("total", 0) if count_result.data else 0

        # Phase 2: Get eval scores for this page
        trace_ids = [str(row.get("trace_id", "")) for row in result.data]
        eval_map = {}
        if trace_ids and eval_config_ids:
            eval_query, eval_params = builder.build_eval_query(trace_ids)
            if eval_query:
                eval_result = analytics.execute_ch_query(
                    eval_query, eval_params, timeout_ms=30000
                )
                eval_map = builder.pivot_eval_results(
                    [(list(row.values())) for row in eval_result.data],
                    list(eval_result.data[0].keys()) if eval_result.data else [],
                )

        # Phase 3: Annotations — fetch from PG Score (unified annotation system)
        annotation_map = _build_annotation_map_from_scores(
            trace_ids, annotation_label_ids, label_types
        )

        user_id_map = builder.resolve_user_ids(trace_ids, analytics)

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
                "user_id": user_id_map.get(trace_id),
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

        response = {
            "column_config": column_config,
            "metadata": {"total_rows": total_count},
            "table": table_data,
        }

        return self._gm.success_response(response)

    # ------------------------------------------------------------------
    # Agent Graph — aggregate topology visualization
    # ------------------------------------------------------------------

    @validated_request(query_serializer=TraceAgentGraphQuerySerializer)
    @action(detail=False, methods=["get"])
    def agent_graph(self, request, *args, **kwargs):
        """Return the aggregate agent graph for a project.

        Computes nodes (distinct span types/names) and edges (parent→child
        transitions) across all traces in the given time window.
        """
        project_id = None
        filters = []
        builder = None
        try:
            query = request.validated_query_data
            project_id = str(query["project_id"])
            filters = query["filters"]

            project = (
                _project_queryset_for_request(request).filter(id=project_id).first()
            )
            if not project:
                return self._gm.bad_request("Project not found")

            builder = AgentGraphQueryBuilder(
                project_id=project_id,
                filters=filters,
            )

            analytics = AnalyticsQueryService()

            # Edge query
            edge_query, edge_params = builder.build()
            edge_result = analytics.execute_ch_query(
                edge_query, edge_params, timeout_ms=15000
            )

            # Node metrics query
            node_query, node_params = builder.build_node_metrics()
            node_result = analytics.execute_ch_query(
                node_query, node_params, timeout_ms=15000
            )

            # CH-only path. The "agent graph returned no nodes → fall back
            # to PG" branch and the outer-except PG fallback were both
            # removed: an empty CH result means the project genuinely has no
            # spans (or the data-pipeline is broken — that's an alert, not
            # a fallback opportunity). PG was the legacy source of truth;
            # post-migration it's incomplete.
            result = builder.format_result(
                edge_result.data,
                edge_result.columns or [],
                node_result.data,
                node_result.columns or [],
            )
            return self._gm.success_response(result)

        except Exception as e:
            logger.exception("agent_graph failed", error=str(e))
            return self._gm.bad_request("Failed to compute agent graph")


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
            )

            if export:
                response = StreamingHttpResponse(
                    manager.iter_export_csv(),
                    content_type="text/csv",
                )
                response["Content-Disposition"] = "attachment"
                return response

            payload = manager.list_payload(
                page_size=page_size, current_page=current_page
            )
            return self._gm.success_response(payload)

        except Exception as e:
            logger.exception(f"ERROR {e}")
            return self._gm.bad_request(f"error fetching users: {str(e)}")


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
