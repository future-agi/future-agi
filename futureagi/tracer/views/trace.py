import csv
import io
import json
import math
import traceback
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd
import structlog
from django.contrib.postgres.aggregates import ArrayAgg
from django.db import models
from django.db.models import (
    Avg,
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
    Sum,
    TextField,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Floor, JSONObject, NullIf, Round
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

logger = structlog.get_logger(__name__)
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from model_hub.utils.SQL_queries import SQLQueryHandler
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.models.custom_eval_config import CustomEvalConfig, EvalOutputType
from tracer.models.observation_span import EndUser, EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.serializers.observation_span import (
    ObservationSpanSerializer,
    SpanExportSerializer,
)
from tracer.serializers.trace import TraceExportSerializer, TraceSerializer
from tracer.services.clickhouse.query_builders import (
    AgentGraphQueryBuilder,
    EvalMetricsQueryBuilder,
    TimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService, QueryType
from tracer.services.observability_providers import ObservabilityService
from tracer.utils.aggregates import JSONBObjectAgg
from tracer.utils.annotations import (
    build_annotation_subqueries as _build_annotation_subqueries_impl,
)
from tracer.utils.filters import FilterEngine
from tracer.utils.graphs_optimized import (
    get_annotation_graph_data,
    get_eval_graph_data,
    get_system_metric_data,
)
from tracer.utils.helper import (
    FieldConfig,
    generate_timestamps,
    get_annotation_labels_for_project,
    get_default_trace_config,
    update_column_config_based_on_eval_config,
    update_span_column_config_based_on_annotations,
)
from tracer.utils.otel import DECODER, CallAttributes, ConversationAttributes
from tracer.views.observation_span import get_observation_spans


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


def _build_annotation_map_from_scores(trace_ids, annotation_label_ids, label_types):
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

    return _build_annotation_map_from_scores_pg(
        trace_ids, annotation_label_ids, label_types
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
        "label_ids": tuple(str(l) for l in annotation_label_ids),
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
            entry = annotation_map[tid].setdefault(
                lid, {"score": None, "annotators": {}}
            )
            if uid and score_val is not None:
                entry["annotators"][uid] = {
                    "user_id": uid,
                    "user_name": user_name,
                    "score": score_val,
                }
            scores_list = [
                a["score"]
                for a in entry["annotators"].values()
                if a.get("score") is not None
            ]
            entry["score"] = (
                int(sum(scores_list) / len(scores_list)) if scores_list else None
            )

        elif ltype == "thumbs_up_down":
            thumb_val = val.get("value") if isinstance(val, dict) else val
            is_up = thumb_val in (True, "up", 1, "true")
            entry = annotation_map[tid].setdefault(
                lid, {"thumbs_up": 0, "thumbs_down": 0, "annotators": {}}
            )
            if is_up:
                entry["thumbs_up"] = entry.get("thumbs_up", 0) + 1
            else:
                entry["thumbs_down"] = entry.get("thumbs_down", 0) + 1
            if uid:
                entry["annotators"][uid] = {
                    "user_id": uid,
                    "user_name": user_name,
                    "score": 100.0 if is_up else 0.0,
                }

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
                entry["annotators"][uid] = {
                    "user_id": uid,
                    "user_name": user_name,
                    "value": selected,
                }

        elif ltype == "text":
            text_val = val.get("text", val) if isinstance(val, dict) else val
            entry = annotation_map[tid].setdefault(
                lid, {"score": text_val, "annotators": {}}
            )
            if uid:
                entry["annotators"][uid] = {
                    "user_id": uid,
                    "user_name": user_name,
                    "value": text_val,
                }
        else:
            annotation_map[tid].setdefault(lid, {"score": val, "annotators": {}})

    return annotation_map


def _build_annotation_map_from_scores_pg(trace_ids, annotation_label_ids, label_types):
    """PG fallback implementation of annotation map builder."""
    from django.db.models import Q

    from model_hub.models.score import Score

    annotation_map = {}
    # Query scores linked directly to trace OR via observation_span → trace
    scores = Score.objects.filter(
        Q(trace_id__in=trace_ids) | Q(observation_span__trace_id__in=trace_ids),
        label_id__in=annotation_label_ids,
        deleted=False,
    ).select_related("annotator", "observation_span")

    for s in scores:
        # Resolve trace_id — either directly set or via observation_span FK
        tid = (
            str(s.trace_id)
            if s.trace_id
            else (
                str(s.observation_span.trace_id)
                if s.observation_span and s.observation_span.trace_id
                else None
            )
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
            entry = annotation_map[tid].setdefault(
                lid, {"score": None, "annotators": {}}
            )
            if uid and score_val is not None:
                entry["annotators"][uid] = {
                    "user_id": uid,
                    "user_name": user_name,
                    "score": score_val,
                }
            scores_list = [
                a["score"]
                for a in entry["annotators"].values()
                if a.get("score") is not None
            ]
            entry["score"] = (
                int(sum(scores_list) / len(scores_list)) if scores_list else None
            )

        elif ltype == "thumbs_up_down":
            thumb_val = val.get("value") if isinstance(val, dict) else val
            is_up = thumb_val in (True, "up", 1, "true")
            entry = annotation_map[tid].setdefault(
                lid, {"thumbs_up": 0, "thumbs_down": 0, "annotators": {}}
            )
            if is_up:
                entry["thumbs_up"] = entry.get("thumbs_up", 0) + 1
            else:
                entry["thumbs_down"] = entry.get("thumbs_down", 0) + 1
            if uid:
                entry["annotators"][uid] = {
                    "user_id": uid,
                    "user_name": user_name,
                    "score": 100.0 if is_up else 0.0,
                }

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
                entry["annotators"][uid] = {
                    "user_id": uid,
                    "user_name": user_name,
                    "value": selected,
                }

        elif ltype == "text":
            text_val = val.get("text", val) if isinstance(val, dict) else val
            entry = annotation_map[tid].setdefault(
                lid, {"score": text_val, "annotators": {}}
            )
            if uid:
                entry["annotators"][uid] = {
                    "user_id": uid,
                    "user_name": user_name,
                    "value": text_val,
                }
        else:
            annotation_map[tid].setdefault(lid, {"score": val, "annotators": {}})

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

        turn_candidates = [
            (
                (attrs.get("metrics_data") or {}).get("turn_count")
                if isinstance(attrs.get("metrics_data"), dict)
                else None
            ),
            (
                (attrs.get("metrics_data") or {}).get("bot_message_count")
                if isinstance(attrs.get("metrics_data"), dict)
                else None
            ),
            attrs.get("call.total_turns"),
            attrs.get("call.totalTurns"),
            attrs.get("callTotalTurns"),
            attrs.get("totalTurns"),
            call_attrs.get("total_turns"),
            call_attrs.get("totalTurns"),
            perf.get("totalTurns"),
            perf.get("turnCount"),
            structured.get("totalTurns"),
            structured.get("turnCount"),
            (
                (attrs.get("metrics_data") or {}).get("message_count")
                if isinstance(attrs.get("metrics_data"), dict)
                else None
            ),
        ]

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
        if talk_ratio is not None:
            denominator = talk_ratio + 1
            if denominator > 0:
                agent_talk_percentage = round((talk_ratio / denominator) * 100, 2)

        return {
            "turn_count": turn_count,
            "talk_ratio": talk_ratio,
            "agent_talk_percentage": agent_talk_percentage,
        }

    def get_queryset(self):
        trace_id = self.kwargs.get("pk")

        # Get base queryset with automatic filtering from mixin
        query_Set = super().get_queryset()

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

    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve a trace by its ID.
        """
        try:
            trace_id = kwargs.get("pk")

            # ClickHouse dispatch for trace detail
            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.TRACE_DETAIL):
                try:
                    return self._retrieve_clickhouse(request, trace_id, analytics)
                except Exception as e:
                    logger.warning(
                        "CH trace retrieve failed, falling back to PG", error=str(e)
                    )

            trace = Trace.objects.get(
                id=trace_id,
                project__organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )
            serializer = self.get_serializer(trace)
            trace = serializer.data
            observation_spans_response = get_observation_spans(
                {
                    "project_id": trace["project"],
                    "project_version_id": trace["project_version"],
                    "trace_id": trace["id"],
                }
            )

            # Compute summary and graph from the span tree (same logic as CH path)
            summary, graph = self._compute_summary_and_graph(observation_spans_response)

            return self._gm.success_response(
                {
                    "trace": trace,
                    "observation_spans": observation_spans_response,
                    "summary": summary,
                    "graph": graph,
                }
            )
        except Exception as e:
            logger.exception(f"Error in fetching the trace: {str(e)}")
            return self._gm.bad_request(
                f"error retrieving trace {get_error_message('ERROR_GETTING_TRACE')}"
            )

    @staticmethod
    def _compute_summary_and_graph(spans_tree):
        """Compute summary metrics and graph from a span tree.

        Works with both PG and CH response formats.
        The spans_tree is a list of root span entries, each with
        'observation_span' (dict) and 'children' (list).
        """
        all_spans = []
        graph_nodes = []
        graph_edges = []

        def walk(entries, parent_id=None):
            for entry in entries:
                span = (
                    entry.get("observation_span") or entry.get("observationSpan") or {}
                )
                span_id = span.get("id", "")
                all_spans.append({"span": span, "parent_id": parent_id})
                graph_nodes.append(
                    {
                        "id": span_id,
                        "name": span.get("name", ""),
                        "type": span.get("observation_type", "unknown"),
                        "latency_ms": span.get("latency_ms")
                        or span.get("latency")
                        or 0,
                        "tokens": span.get("total_tokens") or 0,
                        "status": span.get("status"),
                    }
                )
                if parent_id:
                    graph_edges.append({"from": parent_id, "to": span_id})
                children = entry.get("children", [])
                if children:
                    walk(children, parent_id=span_id)

        walk(spans_tree)

        total_tokens = 0
        total_prompt = 0
        total_completion = 0
        total_cost = 0.0
        error_count = 0
        type_counts = {}
        root_latencies = []

        for item in all_spans:
            sp = item["span"]
            total_tokens += sp.get("total_tokens") or 0
            total_prompt += sp.get("prompt_tokens") or 0
            total_completion += sp.get("completion_tokens") or 0
            total_cost += sp.get("cost") or 0.0
            if sp.get("status") == "ERROR":
                error_count += 1
            t = sp.get("observation_type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
            if item["parent_id"] is None:
                root_latencies.append(sp.get("latency_ms") or sp.get("latency") or 0)

        summary = {
            "total_spans": len(all_spans),
            "total_duration_ms": max(root_latencies) if root_latencies else 0,
            "total_tokens": total_tokens,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_cost": round(total_cost, 6),
            "error_count": error_count,
            "span_type_counts": type_counts,
        }
        graph = {"nodes": graph_nodes, "edges": graph_edges}
        return summary, graph

    def _retrieve_clickhouse(self, request, trace_id, analytics):
        """Retrieve a trace and its spans from ClickHouse."""
        from tracer.constants.provider_logos import PROVIDER_LOGOS

        # Always fetch trace metadata from PG (small, config-like)
        trace = Trace.objects.get(
            id=trace_id,
            project__organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
        )
        serializer = self.get_serializer(trace)
        trace_data = serializer.data

        project_id = str(trace.project_id)

        # Fetch all spans for this trace from CH — use the denormalized `spans`
        # table which has renamed columns vs PG. Map them back to expected names.
        query = """
            SELECT
                id, trace_id, parent_span_id, name, observation_type,
                start_time, end_time, input, output, model,
                '' AS model_parameters, latency_ms, prompt_tokens,
                completion_tokens, total_tokens, cost, status,
                status_message, tags, span_events,
                provider, span_attributes_raw AS span_attributes,
                project_version_id, custom_eval_config_id,
                metadata_map,
                span_attr_str, span_attr_num, span_attr_bool
            FROM spans
            WHERE project_id = %(project_id)s
              AND trace_id = %(trace_id)s
              AND _peerdb_is_deleted = 0
            ORDER BY start_time
            LIMIT 1 BY id
        """
        result = analytics.execute_ch_query(
            query,
            {"project_id": project_id, "trace_id": str(trace_id)},
            timeout_ms=10000,
        )

        # Build span tree
        span_map = {}  # id -> span data
        root_spans = []
        orphan_spans = []

        import json as _json

        def _parse_json(val, default=None):
            if default is None:
                default = {}
            if not val or not isinstance(val, str):
                return val if val is not None else default
            try:
                return _json.loads(val)
            except (ValueError, TypeError):
                return default

        for row in result.data:
            span_id = str(row.get("id", ""))
            parent_id = row.get("parent_span_id")
            parent_id_str = str(parent_id) if parent_id else None

            provider = row.get("provider")

            # Build span_attributes from raw JSON or decomposed maps
            span_attrs_raw = row.get("span_attributes") or "{}"
            try:
                span_attrs = (
                    _json.loads(span_attrs_raw)
                    if isinstance(span_attrs_raw, str)
                    else span_attrs_raw
                )
            except (ValueError, TypeError):
                span_attrs = {}
            if not span_attrs:
                span_attrs = {}
                for k, v in (row.get("span_attr_str") or {}).items():
                    span_attrs[k] = v
                for k, v in (row.get("span_attr_num") or {}).items():
                    span_attrs[k] = v
                for k, v in (row.get("span_attr_bool") or {}).items():
                    span_attrs[k] = bool(v)
            # Fallback: if CH has no span_attributes, try PG
            if not span_attrs:
                try:
                    pg_span = ObservationSpan.objects.only(
                        "span_attributes", "eval_attributes"
                    ).get(id=span_id)
                    span_attrs = (
                        pg_span.span_attributes or pg_span.eval_attributes or {}
                    )
                except ObservationSpan.DoesNotExist:
                    pass

            # Build metadata from metadata_map
            metadata_map = row.get("metadata_map") or {}
            metadata = dict(metadata_map) if metadata_map else {}

            span_data = {
                "id": span_id,
                "project": project_id,
                "project_version": (
                    str(row["project_version_id"])
                    if row.get("project_version_id")
                    else None
                ),
                "trace": str(row.get("trace_id", "")),
                "parent_span_id": parent_id_str,
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
                "provider_logo": (
                    PROVIDER_LOGOS.get(provider.lower()) if provider else None
                ),
                "span_attributes": span_attrs,
                "custom_eval_config": (
                    str(row["custom_eval_config_id"])
                    if row.get("custom_eval_config_id")
                    else None
                ),
                "eval_status": None,
                "prompt_version": None,
            }

            span_map[span_id] = {
                "observation_span": span_data,
                "children": [],
                "_parent_id": parent_id_str,
            }

        # ----- Phase 8: Batch fetch eval scores from CH -----
        eval_map = {}
        try:
            eval_query = """
            SELECT
                toString(observation_span_id) AS span_id,
                toString(custom_eval_config_id) AS eval_config_id,
                output_float,
                output_bool,
                output_str,
                eval_explanation
            FROM tracer_eval_logger FINAL
            WHERE trace_id = %(trace_id)s
              AND _peerdb_is_deleted = 0
            """
            eval_result = analytics.execute_ch_query(
                eval_query, {"trace_id": str(trace_id)}, timeout_ms=30000
            )
            # Collect unique config IDs for name lookup
            config_ids_set = set()
            for row in eval_result.data:
                cid = row.get("eval_config_id", "")
                if cid:
                    config_ids_set.add(cid)
            # Lookup eval config names from PG
            config_lookup = {}
            if config_ids_set:
                configs = CustomEvalConfig.objects.filter(
                    id__in=list(config_ids_set), deleted=False
                ).select_related("eval_template")
                config_lookup = {
                    str(c.id): {
                        # Prefer the CustomEvalConfig's user-given name (e.g.
                        # "voice_sentence_count"), fall back to the template
                        # name only if unset. This keeps the drawer labels in
                        # sync with the trace list column headers.
                        "name": c.name
                        or (c.eval_template.name if c.eval_template else str(c.id)),
                        "output_type": (
                            getattr(c.eval_template, "output_type_normalized", None)
                            if c.eval_template
                            else None
                        ),
                    }
                    for c in configs
                }
            # Pivot into per-span map
            for row in eval_result.data:
                sid = row.get("span_id", "")
                if not sid:
                    continue
                if sid not in eval_map:
                    eval_map[sid] = []
                cid = row.get("eval_config_id", "")
                info = config_lookup.get(cid, {})
                # Compute score from output columns
                output_float = row.get("output_float")
                output_bool = row.get("output_bool")
                output_str = row.get("output_str")
                # Score: use float if non-zero, else bool (True=100, False=0)
                if output_float and output_float != 0:
                    score = round(output_float * 100, 2)
                elif output_bool is not None:
                    score = 100 if output_bool else 0
                else:
                    score = None

                explanation = row.get("eval_explanation", "")

                eval_map[sid].append(
                    {
                        "eval_config_id": cid,
                        "eval_name": info.get("name", cid),
                        "output_type": info.get("output_type"),
                        "score": score,
                        "result": output_str
                        or (output_bool if output_bool is not None else None),
                        "explanation": explanation if explanation else None,
                    }
                )
        except Exception as e:
            logger.warning(f"Failed to fetch trace eval scores: {e}")

        # ----- Phase 8: Batch fetch annotations from PG -----
        annotation_map = {}
        try:
            from model_hub.models.score import Score as ScoreModel

            scores = (
                ScoreModel.objects.filter(trace_id=trace_id, deleted=False)
                .select_related("label")
                .values(
                    "observation_span_id",
                    "label_id",
                    "label__name",
                    "label__type",
                    "value",
                )
            )
            for s in scores:
                sid = (
                    str(s["observation_span_id"])
                    if s.get("observation_span_id")
                    else None
                )
                if not sid:
                    continue
                if sid not in annotation_map:
                    annotation_map[sid] = []
                annotation_map[sid].append(
                    {
                        "label_id": str(s["label_id"]) if s.get("label_id") else None,
                        "label_name": s.get("label__name"),
                        "label_type": s.get("label__type"),
                        "value": s.get("value"),
                    }
                )
        except Exception as e:
            logger.warning(f"Failed to fetch trace annotations: {e}")

        # ----- Phase 8: Compute summary -----
        total_tokens = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_cost = 0.0
        error_count = 0
        type_counts = {}
        root_latencies = []

        for sid, entry in span_map.items():
            sp = entry["observation_span"]
            total_tokens += sp.get("total_tokens") or 0
            total_prompt_tokens += sp.get("prompt_tokens") or 0
            total_completion_tokens += sp.get("completion_tokens") or 0
            total_cost += sp.get("cost") or 0.0
            if sp.get("status") == "ERROR":
                error_count += 1
            obs_type = sp.get("observation_type", "unknown")
            type_counts[obs_type] = type_counts.get(obs_type, 0) + 1
            if entry.get("_parent_id") is None:
                root_latencies.append(sp.get("latency_ms") or 0)

        summary = {
            "total_spans": len(span_map),
            "total_duration_ms": max(root_latencies) if root_latencies else 0,
            "total_tokens": total_tokens,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_cost": round(total_cost, 6),
            "error_count": error_count,
            "span_type_counts": type_counts,
        }

        # ----- Phase 8: Derive agent graph -----
        graph_nodes = []
        graph_edges = []
        for sid, entry in span_map.items():
            sp = entry["observation_span"]
            graph_nodes.append(
                {
                    "id": sid,
                    "name": sp.get("name", ""),
                    "type": sp.get("observation_type", "unknown"),
                    "latency_ms": sp.get("latency_ms", 0),
                    "tokens": sp.get("total_tokens", 0),
                    "status": sp.get("status"),
                }
            )
            parent_id = entry.get("_parent_id")
            if parent_id and parent_id in span_map:
                graph_edges.append({"from": parent_id, "to": sid})

        graph = {"nodes": graph_nodes, "edges": graph_edges}

        # ----- Fetch fresh span tags from PG (CH has sync delay) -----
        if span_map:
            try:
                pg_tags = dict(
                    ObservationSpan.objects.filter(id__in=list(span_map.keys()))
                    .exclude(tags=[])
                    .values_list("id", "tags")
                )
                for sid, tags in pg_tags.items():
                    if sid in span_map:
                        span_map[sid]["observation_span"]["tags"] = tags
            except Exception as e:
                logger.warning(f"Failed to fetch span tags from PG: {e}")

        # ----- Attach evals + annotations to each span -----
        for sid, entry in span_map.items():
            entry["eval_scores"] = eval_map.get(sid, [])
            entry["annotations"] = annotation_map.get(sid, [])

        # Build tree: link children to parents
        for span_id, entry in span_map.items():
            parent_id = entry["_parent_id"]
            if parent_id is None:
                root_spans.append(entry)
            elif parent_id in span_map:
                span_map[parent_id]["children"].append(entry)
            else:
                orphan_spans.append(entry)

        # Clean up internal fields
        def _clean_entry(entry):
            del entry["_parent_id"]
            for child in entry["children"]:
                _clean_entry(child)

        for entry in root_spans:
            _clean_entry(entry)
        for entry in orphan_spans:
            _clean_entry(entry)

        observation_spans_response = root_spans + orphan_spans

        return self._gm.success_response(
            {
                "trace": trace_data,
                "observation_spans": observation_spans_response,
                "summary": summary,
                "graph": graph,
            }
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

    def populate_call_logs_result(
        self, qs, eval_configs, annotation_labels=None, *, detail_mode=False
    ):
        results = []
        for trace in qs:
            attrs = getattr(trace, "span_attributes", None) or {}
            metadata = getattr(trace, "metadata", None) or {}

            # Extract values from span_attributes (flattened keys)
            def attr(key: str):
                return attrs.get(key)  # noqa: B023

            recording = self._build_recording_dict(attrs)

            # Raw provider payload if present
            raw_log = attrs.get("raw_log") or {}
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
                "observation_span": observation_span,
                "turn_count": voice_metrics.get("turn_count"),
                "talk_ratio": voice_metrics.get("talk_ratio"),
                "agent_talk_percentage": voice_metrics.get("agent_talk_percentage"),
                "avg_agent_latency_ms": attr("avg_agent_latency_ms"),
                "user_wpm": attr(CallAttributes.USER_WPM),
                "bot_wpm": attr(CallAttributes.BOT_WPM),
                "user_interruption_count": attr("user_interruption_count"),
                "ai_interruption_count": attr("ai_interruption_count"),
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
            id__in=EvalLogger.objects.filter(trace__project_id=project_id)
            .values("custom_eval_config_id")
            .distinct(),
            deleted=False,
        ).select_related("eval_template")

        for config in eval_configs:
            choices = (
                config.eval_template.choices
                if getattr(config, "eval_template", None)
                and config.eval_template.choices
                else None
            )

            metric_subquery = (
                EvalLogger.objects.filter(
                    trace_id=OuterRef("id"), custom_eval_config_id=config.id
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
                    f"error_{config.id}": Subquery(metric_subquery.values("error")),
                }
            )
        return eval_configs, base_query

    @action(detail=True, methods=["patch"], url_path="tags")
    def update_tags(self, request, *args, **kwargs):
        """Update tags for a trace."""
        try:
            trace_id = kwargs.get("pk")
            trace = Trace.objects.get(id=trace_id)
            tags = request.data.get("tags")
            if tags is None:
                return self._gm.bad_request("tags field is required")
            if not isinstance(tags, list):
                return self._gm.bad_request("tags must be a list")
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
            project = Project.objects.get(
                id=project_id,
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )

            if not project_id or not project or project.trace_type != "observe":
                return self._gm.bad_request(
                    "Project id is required and project should be of type observe"
                )

            name = self.request.query_params.get("name", None)

            # ClickHouse dispatch: resolve which eval config IDs have data
            analytics = AnalyticsQueryService()
            eval_config_ids = None
            if analytics.should_use_clickhouse(QueryType.EVAL_METRICS):
                try:
                    eval_config_ids = analytics.get_eval_config_ids_with_data_ch(
                        str(project_id)
                    )
                except Exception as e:
                    logger.warning(
                        "CH eval config IDs failed, falling back to PG", error=str(e)
                    )
                    eval_config_ids = None

            if eval_config_ids is None:
                # PG fallback
                eval_config_ids = list(
                    EvalLogger.objects.filter(
                        trace__project_id=project_id, deleted=False
                    )
                    .values_list("custom_eval_config_id", flat=True)
                    .distinct()
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

    @action(detail=False, methods=["get"])
    def list_traces(self, request, *args, **kwargs):
        """
        List traces filtered by project ID and project version ID with optimized queries.
        """
        try:
            project_version_id = self.request.query_params.get(
                "project_version_id"
            ) or self.request.query_params.get("projectVersionId")
            if not project_version_id:
                raise Exception("Project version id is required")
            project_version = None
            try:
                project_version = ProjectVersion.objects.get(
                    id=project_version_id,
                    project__organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                )
            except ProjectVersion.DoesNotExist:
                raise Exception("Project version not found")  # noqa: B904

            # ClickHouse dispatch
            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.TRACE_LIST):
                try:
                    return self._list_traces_clickhouse(
                        request, project_version_id, analytics
                    )
                except Exception as e:
                    logger.warning(
                        "ClickHouse trace-list failed, falling back to PG", error=str(e)
                    )
                    # Fall through to existing PG code

            # Base query with annotations
            base_query = Trace.objects.filter(project_version_id=project_version_id)

            # Add trace_ids filter if provided
            trace_ids = self.request.query_params.get(
                "trace_ids"
            ) or self.request.query_params.get("traceIds")
            if trace_ids:
                trace_ids = trace_ids.split(",")
                if len(trace_ids) > 0:
                    base_query = base_query.filter(id__in=trace_ids)

            # Annotate with node_type
            base_query = base_query.annotate(
                node_type=Case(
                    When(
                        Exists(
                            ObservationSpan.objects.filter(
                                trace_id=OuterRef("id"), parent_span_id__isnull=True
                            )
                        ),
                        then=Subquery(
                            ObservationSpan.objects.filter(
                                trace_id=OuterRef("id"), parent_span_id__isnull=True
                            ).values("observation_type")[:1]
                        ),
                    ),
                    default=Value("unknown"),
                    output_field=CharField(),
                ),
                trace_name=Case(
                    When(
                        Exists(
                            ObservationSpan.objects.filter(
                                trace_id=OuterRef("id"), parent_span_id__isnull=True
                            )
                        ),
                        then=Subquery(
                            ObservationSpan.objects.filter(
                                trace_id=OuterRef("id"), parent_span_id__isnull=True
                            ).values("name")[:1]
                        ),
                    ),
                    default=Value("[ Incomplete Trace ]"),
                    output_field=CharField(),
                ),
                trace_id=F("id"),
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
                status=Case(
                    # Highest priority: any ERROR
                    When(
                        Exists(
                            ObservationSpan.objects.filter(
                                trace_id=OuterRef("id"), parent_span_id__isnull=True
                            ).filter(status="ERROR")
                        ),
                        then=Value("ERROR"),
                    ),
                    # Next: any OK
                    When(
                        Exists(
                            ObservationSpan.objects.filter(
                                trace_id=OuterRef("id"), parent_span_id__isnull=True
                            ).filter(status="OK")
                        ),
                        then=Value("OK"),
                    ),
                    # Otherwise: UNSET
                    default=Value("UNSET"),
                    output_field=CharField(),
                ),
            )

            # Get all eval configs from the project version
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    trace__project_version_id=project_version_id
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
                            default=None,
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

            # Apply filters - combine all filter conditions for better performance
            filters = self.request.query_params.get("filters", [])
            if filters:
                filters = json.loads(filters)

            if filters:
                # Combine all filter conditions into a single Q object
                combined_filter_conditions = Q()

                # Get system metric filters
                filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filters)
                )
                if filter_conditions:
                    combined_filter_conditions &= filter_conditions

                # Separate annotation filters from eval filters since
                # annotations are JSON objects
                def _get_col_type(f):
                    fc = f.get("filter_config", {})
                    return fc.get("col_type", f.get("col_type", ""))

                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filters
                    if _get_col_type(f) not in annotation_col_types
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
                ]

                # Get eval metric filters (excluding annotation filters)
                eval_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_non_system_metrics(
                        non_annotation_filters
                    )
                )
                if eval_filter_conditions:
                    combined_filter_conditions &= eval_filter_conditions

                # Get annotation filters (score, annotator, my_annotations)
                annotation_filter_conditions, extra_annotations = (
                    FilterEngine.get_filter_conditions_for_voice_call_annotations(
                        filters, user_id=request.user.id
                    )
                )
                if extra_annotations:
                    base_query = base_query.annotate(**extra_annotations)
                if annotation_filter_conditions:
                    combined_filter_conditions &= annotation_filter_conditions

                # Get span attribute filters
                span_attribute_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_conditions:
                    combined_filter_conditions &= span_attribute_conditions

                # Apply has_eval filter (only traces with evals)
                has_eval_condition = FilterEngine.get_filter_conditions_for_has_eval(
                    filters, observe_type="trace"
                )
                if has_eval_condition:
                    combined_filter_conditions &= has_eval_condition

                # Apply has_annotation filter
                has_annotation_condition = (
                    FilterEngine.get_filter_conditions_for_has_annotation(
                        filters,
                        observe_type="trace",
                        annotation_label_ids=[str(l.id) for l in annotation_labels],
                    )
                )
                if has_annotation_condition:
                    combined_filter_conditions &= has_annotation_condition

                # Apply combined filters in a single operation
                if combined_filter_conditions:
                    base_query = base_query.filter(combined_filter_conditions)

            base_query = base_query.order_by("-start_time", "-id")

            # Get total count before pagination
            total_count = base_query.count()

            # Apply pagination
            page_number = int(self.request.query_params.get("page_number", 0)) or int(
                self.request.query_params.get("pageNumber", 0)
            )
            page_size = int(self.request.query_params.get("page_size", 30)) or int(
                self.request.query_params.get("pageSize", 30)
            )
            start = page_number * page_size
            base_query = base_query[start : start + page_size]

            # Prepare column config
            column_config = get_default_trace_config()
            column_config = update_column_config_based_on_eval_config(
                column_config, eval_configs
            )
            column_config = update_span_column_config_based_on_annotations(
                column_config, annotation_labels
            )

            # Process results
            table_data = []
            for trace in base_query:
                input_val = trace.input
                output_val = trace.output

                result = {
                    "node_type": trace.node_type or "",
                    "trace_id": str(trace.id),
                    "input": input_val,
                    "output": output_val,
                    "trace_name": trace.trace_name or "",
                    "start_time": trace.start_time,
                    "status": trace.status,
                }

                # Add eval metrics from annotated fields
                for config in eval_configs:
                    data = getattr(trace, f"metric_{config.id}")
                    if data and "score" in data:
                        result[str(config.id)] = data["score"]
                    elif data:
                        for key, value in data.items():
                            result[str(config.id) + "**" + key] = value["score"]

                # Add Root Span Annotations
                for label in annotation_labels:
                    ann_data = getattr(trace, f"annotation_{label.id}", None)
                    if ann_data is not None:
                        result[str(label.id)] = ann_data

                table_data.append(result)

            response = {
                "column_config": column_config,
                "metadata": {"total_rows": total_count},
                "table": table_data,
            }

            return self._gm.success_response(response)

        except Exception as e:
            logger.exception(f"Error in fetching the traces list: {str(e)}")

            return self._gm.bad_request(
                f"error fetching the traces list {get_error_message('ERROR_GETTING_TRACE_LIST')}"
            )

    @action(detail=False, methods=["post"])
    def get_graph_methods(self, request, *args, **kwargs):
        """
        Fetch data for the observe graph with optimized queries
        """
        try:
            project_id = self.request.data.get("project_id", None)
            project = Project.objects.get(
                id=project_id,
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )

            if not project_id or not project or project.trace_type != "observe":
                return self._gm.bad_request(
                    "Project id is required and project should be of type observe"
                )

            # Get parameters
            property = self.request.data.get("property", "count")
            filters = self.request.data.get("filters", [])
            interval = self.request.data.get("interval", "hour")

            # Base query with annotations
            base_query = Trace.objects.filter(project_id=project_id).annotate(
                node_type=Subquery(
                    ObservationSpan.objects.filter(
                        trace_id=OuterRef("id"), parent_span_id__isnull=True
                    ).values("observation_type")[:1]
                ),
                trace_name=Subquery(
                    ObservationSpan.objects.filter(
                        trace_id=OuterRef("id"), parent_span_id__isnull=True
                    ).values("name")[:1]
                ),
                trace_id=F("id"),
                # Fetch span_attributes from root span (fallback to eval_attributes for old data)
                span_attributes=Subquery(
                    ObservationSpan.objects.filter(
                        trace_id=OuterRef("id"), parent_span_id__isnull=True
                    )
                    .annotate(_attrs=Coalesce("span_attributes", "eval_attributes"))
                    .values("_attrs")[:1]
                ),
                user_id=Subquery(
                    ObservationSpan.objects.filter(
                        trace_id=OuterRef("id"), parent_span_id__isnull=True
                    ).values("end_user__user_id")[:1]
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
                status=Case(
                    # Highest priority: any ERROR
                    When(
                        Exists(
                            ObservationSpan.objects.filter(
                                trace_id=OuterRef("id"), parent_span_id__isnull=True
                            ).filter(status="ERROR")
                        ),
                        then=Value("ERROR"),
                    ),
                    # Next: any OK
                    When(
                        Exists(
                            ObservationSpan.objects.filter(
                                trace_id=OuterRef("id"), parent_span_id__isnull=True
                            ).filter(status="OK")
                        ),
                        then=Value("OK"),
                    ),
                    # Otherwise: UNSET
                    default=Value("UNSET"),
                    output_field=CharField(),
                ),
            )

            # Get all eval configs for the project
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(trace__project_id=project_id)
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
                            default=None,
                            output_field=JSONField(),
                        )
                    }
                )

            # Add Span Annotations
            annotation_labels = get_annotation_labels_for_project(project_id)
            base_query = self._build_annotation_subqueries(
                base_query, annotation_labels, request.user.organization
            )

            # Apply filters
            if filters:
                # Apply system metric filters
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

                # Separate annotation filters from eval filters since
                # annotations are JSON objects
                def _get_col_type(f):
                    fc = f.get("filter_config", {})
                    return fc.get("col_type", f.get("col_type", ""))

                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filters
                    if _get_col_type(f) not in annotation_col_types
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
                ]

                # Apply eval metric filters (excluding annotation filters)
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

                # Apply span attribute filters
                span_attribute_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_conditions:
                    base_query = base_query.filter(span_attribute_conditions)

                # Apply has_eval filter (only traces with evals)
                has_eval_condition = FilterEngine.get_filter_conditions_for_has_eval(
                    filters, observe_type="trace"
                )
                if has_eval_condition:
                    base_query = base_query.filter(has_eval_condition)

                # Apply has_annotation filter
                has_annotation_condition = (
                    FilterEngine.get_filter_conditions_for_has_annotation(
                        filters,
                        observe_type="trace",
                        annotation_label_ids=[str(l.id) for l in annotation_labels],
                    )
                )
                if has_annotation_condition:
                    base_query = base_query.filter(has_annotation_condition)

            filtered_trace_queryset = base_query
            req_data_config = self.request.data.get("req_data_config", None)

            if not req_data_config:
                return self._gm.bad_request("Req data config property is required")

            type = req_data_config.get("type", None)
            if type not in ["EVAL", "ANNOTATION", "SYSTEM_METRIC"]:
                return self._gm.bad_request("Filter property type is not valid")

            # ClickHouse dispatch for system metrics
            analytics = AnalyticsQueryService()

            if type == "SYSTEM_METRIC" and analytics.should_use_clickhouse(
                QueryType.TIME_SERIES
            ):
                try:
                    metric_id = req_data_config.get("id", "latency")
                    builder = TimeSeriesQueryBuilder(
                        project_id=str(project_id),
                        filters=filters,
                        interval=interval,
                    )
                    query, params = builder.build()
                    result = analytics.execute_ch_query(query, params, timeout_ms=30000)
                    ch_data = builder.format_result(result.data, result.columns or [])
                    # CH now returns all metric keys directly
                    metric_key = metric_id if metric_id in ch_data else "latency"
                    metric_points = ch_data.get(metric_key, [])
                    traffic_points = ch_data.get("traffic", [])
                    # Build traffic lookup by timestamp
                    traffic_by_ts = {
                        t.get("timestamp"): t.get("traffic", 0) for t in traffic_points
                    }
                    graph_data = {
                        "metric_name": metric_id,
                        "data": [
                            {
                                "timestamp": p.get("timestamp"),
                                "value": p.get("value", 0),
                                "primary_traffic": traffic_by_ts.get(
                                    p.get("timestamp"), 0
                                ),
                            }
                            for p in metric_points
                        ],
                    }
                    return self._gm.success_response(graph_data)
                except Exception as e:
                    logger.warning(
                        "ClickHouse time-series failed, falling back to PG",
                        error=str(e),
                    )
                    # Fall through to existing PG code below

            if type == "EVAL" and analytics.should_use_clickhouse(
                QueryType.EVAL_METRICS
            ):
                try:
                    eval_config_id = req_data_config.get("id")
                    eval_output_type = req_data_config.get("eval_output_type", "SCORE")
                    choices = req_data_config.get("choices", [])
                    builder = EvalMetricsQueryBuilder(
                        project_id=str(project_id),
                        custom_eval_config_id=str(eval_config_id),
                        filters=filters,
                        interval=interval,
                        eval_output_type=eval_output_type,
                        choices=choices,
                    )
                    query, params = builder.build()
                    result = analytics.execute_ch_query(query, params, timeout_ms=30000)
                    graph_data = builder.format_result(
                        result.data, result.columns or []
                    )
                    return self._gm.success_response(graph_data)
                except Exception as e:
                    logger.warning(
                        "ClickHouse eval-metrics failed, falling back to PG",
                        error=str(e),
                    )
                    # Fall through to existing PG code below

            if type == "EVAL":
                graph_data = get_eval_graph_data(
                    interval=interval,
                    filters=filters,
                    property=property,
                    observe_type="trace",
                    req_data_config=req_data_config,
                    eval_logger_filters={"trace_ids_queryset": filtered_trace_queryset},
                )

            elif type == "ANNOTATION":
                graph_data = get_annotation_graph_data(
                    interval=interval,
                    filters=filters,
                    property=property,
                    observe_type="trace",
                    req_data_config=req_data_config,
                    annotation_logger_filters={
                        "trace_ids_queryset": filtered_trace_queryset
                    },
                )

            elif type == "SYSTEM_METRIC":
                graph_data = get_system_metric_data(
                    interval=interval,
                    filters=filters,
                    property=property,
                    req_data_config=req_data_config,
                    system_metric_filters={
                        "trace_ids_queryset": filtered_trace_queryset
                    },
                    observe_type="trace",
                )

            if not graph_data:
                # Add debug information
                logger.info(
                    f"""
                    Graph data empty with params:
                    - Project ID: {project_id}
                    - Property: {property}
                    - Interval: {interval}
                """
                )

            data_fetched = graph_data.get("data", [])
            if len(data_fetched) == 0:
                # Find the filter with columnId == "created_at"
                created_at_filter = None
                for f in filters:
                    if f.get("column_id") == "created_at":
                        created_at_filter = f
                        break

                if not created_at_filter:
                    return self._gm.success_response(graph_data)

                filter_config = created_at_filter.get("filter_config", {})
                filter_value = filter_config.get("filter_value", [])
                if not (isinstance(filter_value, list) and len(filter_value) == 2):
                    return self._gm.success_response(graph_data)

                start = datetime.strptime(filter_value[0], "%Y-%m-%dT%H:%M:%S.%fZ")
                end = datetime.strptime(filter_value[1], "%Y-%m-%dT%H:%M:%S.%fZ")

                timestamps = generate_timestamps(interval, start, end)
                graph_data["data"] = timestamps

            return self._gm.success_response(graph_data)

        except Exception as e:
            logger.exception(f"Error in get_graph_methods: {str(e)}")
            return self._gm.bad_request(f"Error fetching graph data: {str(e)}")

    @action(detail=False, methods=["post"])
    def bulk_create(self, request, *args, **kwargs):
        try:
            traces_data = self.request.data.get("traces", [])
            for trace in traces_data:
                trace["project"] = Project.objects.get(
                    id=trace["project"],
                    organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                )
                trace["project_version"] = ProjectVersion.objects.get(
                    id=trace["project_version"],
                    project__organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                )
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
        try:
            project_version_ids = self.request.data.get("project_version_ids", [])
            index = self.request.data.get("index", 0)

            if not project_version_ids:
                return self._gm.success_response(
                    {"trace_comparison": {}, "total_traces": 0, "index": 0}
                )

            # First verify all project versions exist in the database
            existing_versions = ProjectVersion.objects.filter(
                id__in=project_version_ids
            )
            if len(existing_versions) != len(project_version_ids):
                existing_ids = {str(v.id) for v in existing_versions}
                missing_ids = {str(id) for id in project_version_ids} - existing_ids
                return self._gm.success_response(
                    {
                        "trace_comparison": {},
                        "total_traces": 0,
                        "index": 0,
                        "message": f"Some project versions not found: {', '.join(missing_ids)}",
                    }
                )

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
                    trace__project_version_id__in=project_version_ids
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
                                    trace__project_version_id__in=project_version_ids,
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

    @action(detail=False, methods=["get"])
    def get_trace_id_by_index(self, request, *args, **kwargs):
        """
        Get the previous and next trace id by index using efficient database queries.
        """
        try:
            trace_id = self.request.query_params.get(
                "trace_id"
            ) or self.request.query_params.get("traceId")
            project_version_id = self.request.query_params.get(
                "project_version_id"
            ) or self.request.query_params.get("projectVersionId")

            if not trace_id:
                raise Exception("Trace id is required")
            if not project_version_id:
                raise Exception("Project version id is required")

            # Base query with annotations
            base_query = Trace.objects.filter(
                project_version_id=project_version_id
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
                    trace__project_version_id=project_version_id
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
            try:
                project_version = ProjectVersion.objects.get(id=project_version_id)
            except ProjectVersion.DoesNotExist:
                raise Exception("Project version not found")  # noqa: B904

            # Add Root Span Annotations
            annotation_labels = get_annotation_labels_for_project(
                project_version.project.id
            )
            base_query = self._build_annotation_subqueries(
                base_query, annotation_labels, request.user.organization
            )

            # Apply filters from request
            filters = self.request.query_params.get("filters", [])
            if filters:
                try:
                    filters = json.loads(filters)
                except json.JSONDecodeError as e:
                    return self._gm.bad_request(
                        f"Invalid JSON format in filters parameter: {str(e)}"
                    )

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
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
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

    @action(detail=False, methods=["get"])
    def list_traces_of_session(self, request, *args, **kwargs):
        """
        List traces filtered by project ID with optimized queries.
        """
        try:
            export = kwargs.get("export", False) if kwargs else False

            # Get data from query parameters instead of request body
            query_data = {"filters": request.query_params.get("filters", "[]")}

            # Parse JSON fields from query parameters
            try:
                if query_data["filters"]:
                    query_data["filters"] = json.loads(query_data["filters"])
            except json.JSONDecodeError as e:
                return self._gm.bad_request(
                    f"Invalid JSON format in filters parameter: {str(e)}"
                )

            serializer = TraceExportSerializer(data=query_data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
            project_id = request.query_params.get(
                "project_id"
            ) or request.query_params.get("projectId")

            org = (
                getattr(self.request, "organization", None)
                or self.request.user.organization
            )

            # Org-scoped mode: when no project_id is supplied the caller wants
            # traces from every project in the org (e.g. the cross-project
            # user detail page at /dashboard/users/:userId).
            org_scope = not project_id
            if org_scope:
                org_project_ids = list(
                    Project.objects.filter(
                        organization=org,
                        deleted=False,
                        trace_type__in=("observe", "experiment"),
                    ).values_list("id", flat=True)
                )
            else:
                project = Project.objects.get(id=project_id, organization=org)
                if project.trace_type not in ("observe", "experiment"):
                    raise Exception("Project should be of type observe or experiment")
                org_project_ids = None

            # ClickHouse dispatch
            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.TRACE_OF_SESSION_LIST):
                try:
                    return self._list_traces_of_session_clickhouse(
                        request,
                        project_id,
                        validated_data,
                        analytics,
                        org_project_ids=org_project_ids,
                        org=org,
                    )
                except Exception as e:
                    logger.warning(
                        "CH traces-of-session failed, falling back to PG", error=str(e)
                    )

            # Get pagination parameters
            page_number = int(self.request.query_params.get("page_number", 0))
            page_size = int(self.request.query_params.get("page_size", 30))

            base_query = (
                Trace.objects.filter(project_id__in=org_project_ids)
                if org_scope
                else Trace.objects.filter(project_id=project_id)
            )

            # Optional project_version_id filter (for experiment/prototype projects)
            project_version_id = request.query_params.get(
                "project_version_id"
            ) or request.query_params.get("projectVersionId")
            # Common annotation expressions (used with and without project_version_id)
            _root_span_qs = ObservationSpan.objects.filter(
                trace_id=OuterRef("id"), parent_span_id__isnull=True
            )
            _all_span_qs = ObservationSpan.objects.filter(trace_id=OuterRef("id"))
            _end_user_span_qs = ObservationSpan.objects.filter(
                trace_id=OuterRef("id"), end_user__isnull=False
            ).order_by("start_time")

            _common_annotations = dict(
                node_type=Case(
                    When(
                        Exists(_root_span_qs),
                        then=Subquery(_root_span_qs.values("observation_type")[:1]),
                    ),
                    default=Value("unknown"),
                    output_field=CharField(),
                ),
                trace_name=Case(
                    When(
                        Exists(_root_span_qs),
                        then=Subquery(_root_span_qs.values("name")[:1]),
                    ),
                    default=Value("[ Incomplete Trace ]"),
                    output_field=CharField(),
                ),
                latency=Case(
                    When(
                        Exists(_root_span_qs),
                        then=Subquery(_root_span_qs.values("latency_ms")[:1]),
                    ),
                    output_field=IntegerField(),
                ),
                total_tokens=Coalesce(
                    Subquery(
                        _all_span_qs.values("trace_id")
                        .annotate(total=Sum("total_tokens"))
                        .values("total")[:1]
                    ),
                    0,
                    output_field=IntegerField(),
                ),
                total_cost=Coalesce(
                    Subquery(
                        _all_span_qs.values("trace_id")
                        .annotate(total=Sum("cost"))
                        .values("total")[:1]
                    ),
                    0,
                    output_field=FloatField(),
                ),
                trace_id=F("id"),
                # Fetch span_attributes from root span (fallback to eval_attributes for old data)
                span_attributes=Subquery(
                    ObservationSpan.objects.filter(
                        trace_id=OuterRef("id"), parent_span_id__isnull=True
                    )
                    .annotate(_attrs=Coalesce("span_attributes", "eval_attributes"))
                    .values("_attrs")[:1]
                ),
                user_id=Subquery(
                    ObservationSpan.objects.filter(
                        trace_id=OuterRef("id"), end_user__isnull=False
                    )
                    .order_by("start_time")
                    .values("end_user__user_id")[:1]
                ),
                user_id_type=Subquery(
                    _end_user_span_qs.values("end_user__user_id_type")[:1]
                ),
                user_id_hash=Subquery(
                    _end_user_span_qs.values("end_user__user_id_hash")[:1]
                ),
                start_time=Coalesce(
                    Subquery(
                        _root_span_qs.order_by("start_time").values("start_time")[:1]
                    ),
                    "created_at",
                ),
                status=Case(
                    When(
                        Exists(_root_span_qs.filter(status="ERROR")),
                        then=Value("ERROR"),
                    ),
                    When(
                        Exists(_root_span_qs.filter(status="OK")),
                        then=Value("OK"),
                    ),
                    default=Value("UNSET"),
                    output_field=CharField(),
                ),
            )

            if project_version_id:
                base_query = (
                    base_query.filter(
                        observation_spans__project_version_id=project_version_id,
                        observation_spans__parent_span_id__isnull=True,
                    )
                    .distinct()
                    .annotate(**_common_annotations)
                )
            else:
                base_query = base_query.annotate(**_common_annotations)

            # Get all eval configs for the project (or all org projects in
            # org-scoped mode)
            _eval_logger_qs = (
                EvalLogger.objects.filter(trace__project_id__in=org_project_ids)
                if org_scope
                else EvalLogger.objects.filter(trace__project_id=project_id)
            )
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=_eval_logger_qs.values("custom_eval_config_id").distinct(),
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
                            default=None,
                            output_field=JSONField(),
                        ),
                    }
                )

            # Add Span Annotations. In org-scoped mode we skip annotation
            # labels (deferred enhancement — the helper only supports a
            # single project).
            annotation_labels = (
                [] if org_scope else get_annotation_labels_for_project(project_id)
            )
            base_query = self._build_annotation_subqueries(
                base_query, annotation_labels, request.user.organization
            )

            # Apply filters
            filters = validated_data.get("filters", [])
            if filters:
                # Apply system metric filters
                system_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filters)
                )
                if system_filter_conditions:
                    base_query = base_query.filter(system_filter_conditions)

                # Separate annotation filters from eval filters since
                # annotations are JSON objects
                def _get_col_type(f):
                    fc = f.get("filter_config", {})
                    return fc.get("col_type", f.get("col_type", ""))

                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filters
                    if _get_col_type(f) not in annotation_col_types
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
                ]

                # Apply eval metric filters (excluding annotation filters)
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

                # Apply span attribute filters
                span_attribute_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_conditions:
                    base_query = base_query.filter(span_attribute_conditions)

                # Apply has_eval filter (only traces with evals)
                has_eval_condition = FilterEngine.get_filter_conditions_for_has_eval(
                    filters, observe_type="trace"
                )
                if has_eval_condition:
                    base_query = base_query.filter(has_eval_condition)

                # Apply has_annotation filter
                has_annotation_condition = (
                    FilterEngine.get_filter_conditions_for_has_annotation(
                        filters,
                        observe_type="trace",
                        annotation_label_ids=[str(l.id) for l in annotation_labels],
                    )
                )
                if has_annotation_condition:
                    base_query = base_query.filter(has_annotation_condition)

            base_query = base_query.order_by("-start_time", "-id")

            # Get total count before pagination
            total_count = base_query.count()

            # Apply pagination
            start = page_number * page_size
            base_query = base_query if export else base_query[start : start + page_size]

            # Prepare column config — get_default_trace_config() already includes
            # all standard columns (latency, tokens, cost, user_id, etc.)
            column_config = get_default_trace_config()
            column_config = update_column_config_based_on_eval_config(
                column_config, eval_configs
            )
            column_config = update_span_column_config_based_on_annotations(
                column_config, annotation_labels
            )

            # Process results
            table_data = []
            for trace in base_query:
                input_val = trace.input
                output_val = trace.output

                result = {
                    "trace_id": str(trace.id),
                    "project_id": str(trace.project_id),
                    "input": input_val,
                    "output": output_val,
                    "created_at": trace.created_at.isoformat() + "Z",
                    "node_type": trace.node_type or "",
                    "latency": trace.latency,
                    "total_tokens": trace.total_tokens,
                    "total_cost": round(trace.total_cost, 6) if trace.total_cost else 0,
                    "user_id": trace.user_id,
                    "user_id_type": trace.user_id_type,
                    "user_id_hash": trace.user_id_hash,
                    "trace_name": trace.trace_name or "",
                    "start_time": trace.start_time,
                    "status": trace.status,
                }

                # Add eval metrics from annotated fields
                for config in eval_configs:
                    data = getattr(trace, f"metric_{config.id}")
                    if data and "score" in data:
                        score = data["score"]
                        result[str(config.id)] = round(score, 2) if score is not None else None
                    elif data:
                        for key, value in data.items():
                            score = value["score"] if isinstance(value, dict) and "score" in value else None
                            result[str(config.id) + "**" + key] = round(score, 2) if score is not None else None

                # Add Root Span Annotations
                for label in annotation_labels:
                    ann_data = getattr(trace, f"annotation_{label.id}", None)
                    if ann_data is not None:
                        result[str(label.id)] = ann_data

                # Include trace metadata as flat keys for custom columns
                if trace.metadata and isinstance(trace.metadata, dict):
                    for key, value in trace.metadata.items():
                        if key not in result:
                            if isinstance(value, str) and len(value) > 500:
                                result[key] = value[:500] + "..."
                            else:
                                result[key] = value

                table_data.append(result)

            response = {
                "metadata": {"total_rows": total_count},
                "table": table_data,
                "config": column_config,
            }

            return self._gm.success_response(response)

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
            project_id = request.query_params.get(
                "project_id"
            ) or request.query_params.get("projectId")
            remove_simulation_calls = request.query_params.get(
                "remove_simulation_calls"
            )
            if not project_id:
                raise Exception("Project id is required")

            query_data = {"filters": request.query_params.get("filters", "[]")}

            # Parse JSON fields from query parameters
            try:
                if query_data["filters"]:
                    query_data["filters"] = json.loads(query_data["filters"])
            except json.JSONDecodeError as e:
                return self._gm.bad_request(
                    f"Invalid JSON format in filters parameter: {str(e)}"
                )

            serializer = SpanExportSerializer(data=query_data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data

            # Validate project exists
            Project.objects.get(
                id=project_id,
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )

            # ClickHouse for pagination + PG for span_attributes (hybrid)
            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.VOICE_CALL_LIST):
                try:
                    return self._list_voice_calls_clickhouse(
                        request,
                        project_id,
                        validated_data,
                        remove_simulation_calls,
                        analytics,
                    )
                except Exception as e:
                    logger.warning(
                        "CH voice-call-list failed, falling back to PG",
                        error=str(e),
                    )

            # Build optimized base query: only traces whose root span is a conversation
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

            # Add Span Annotations
            annotation_labels = get_annotation_labels_for_project(project_id)

            base_query = self._build_annotation_subqueries(
                base_query, annotation_labels, request.user.organization
            )

            filters = validated_data.get("filters", [])
            if filters:
                # Apply system metric filters
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

                # Separate annotation filters from eval filters since voice call
                # annotations are JSON objects (not scalars like in list_traces)
                def _get_col_type(f):
                    fc = f.get("filter_config", {})
                    return fc.get("col_type", f.get("col_type", ""))

                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filters
                    if _get_col_type(f) not in annotation_col_types
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
                ]

                # Apply eval metric filters (excluding annotation filters)
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

                # Apply span attribute filters
                span_attribute_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_conditions:
                    base_query = base_query.filter(span_attribute_conditions)

                # Apply has_eval filter (only traces with evals)
                has_eval_condition = FilterEngine.get_filter_conditions_for_has_eval(
                    filters, observe_type="trace"
                )
                if has_eval_condition:
                    base_query = base_query.filter(has_eval_condition)

                # Apply has_annotation filter
                has_annotation_condition = (
                    FilterEngine.get_filter_conditions_for_has_annotation(
                        filters,
                        observe_type="trace",
                        annotation_label_ids=[str(l.id) for l in annotation_labels],
                    )
                )
                if has_annotation_condition:
                    base_query = base_query.filter(has_annotation_condition)

                # Hide FutureAGI simulator calls
                remove_simulator_calls_conditions = (
                    FilterEngine.get_filter_conditions_for_simulation_calls(
                        remove_simulation_calls=remove_simulation_calls
                    )
                )
                if remove_simulator_calls_conditions:
                    base_query = base_query.exclude(remove_simulator_calls_conditions)

            base_query = base_query.order_by("-start_time", "-id")

            # Build column config for voice observability (simulator) projects
            column_config = update_column_config_based_on_eval_config(
                [], eval_configs, skip_choices=True, is_simulator=True
            )
            column_config = update_span_column_config_based_on_annotations(
                column_config, annotation_labels
            )

            # Use ExtendedPageNumberPagination
            paginator = ExtendedPageNumberPagination()
            # Respect page_size if provided; fallback to 'limit' which paginator supports
            requested_page_size = request.query_params.get("page_size")
            if requested_page_size:
                try:
                    paginator.page_size = int(requested_page_size)
                except (TypeError, ValueError):
                    pass
            page_qs = paginator.paginate_queryset(base_query, request, view=self)

            results = self.populate_call_logs_result(
                page_qs, eval_configs, annotation_labels=annotation_labels
            )

            # Get paginated response and add column config
            response = paginator.get_paginated_response(results)
            response.data["config"] = column_config
            return response

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

            # Validate ownership via a single PG query before any dispatch
            trace = (
                Trace.objects.select_related("project")
                .filter(
                    id=trace_id,
                    project__organization_id=request.user.organization_id,
                )
                .first()
            )
            if not trace:
                return self._gm.not_found("trace_id not found")

            # ClickHouse dispatch
            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.VOICE_CALL_DETAIL):
                try:
                    return self._voice_call_detail_clickhouse(
                        request, trace_id, analytics, str(trace.project_id)
                    )
                except Exception as e:
                    logger.warning(
                        "CH voice-call-detail failed, falling back to PG",
                        error=str(e),
                    )

            # --- PG path ---
            project = trace.project

            # Get root span (conversation type, no parent)
            root_span = ObservationSpan.objects.filter(
                trace_id=trace_id,
                parent_span_id__isnull=True,
                observation_type="conversation",
            ).first()
            if root_span is None:
                return self._gm.not_found("No conversation root span found")

            attrs = root_span.span_attributes or root_span.eval_attributes or {}
            metadata = root_span.metadata or {}
            raw_log = attrs.get("raw_log") or {}
            provider = root_span.provider or "vapi"

            processed_log = ObservabilityService.process_raw_logs(
                raw_log, provider, span_attributes=attrs
            )
            voice_metrics = self._extract_voice_turn_and_talk_metrics(attrs, raw_log)

            recording = self._build_recording_dict(attrs)

            # Serialize ALL observation spans (the expensive part)
            observation_span = [
                ObservationSpanSerializer(span).data
                for span in trace.observation_spans.all()
            ]

            # Include ALL non-deleted eval configs for the project so the
            # drawer always shows the same set of evals as the list columns.
            # Entries without a log record get an empty `output` so the UI
            # can render a placeholder.
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(trace__project_id=project.id)
                .values("custom_eval_config_id")
                .distinct(),
                deleted=False,
            ).select_related("eval_template")

            eval_logs = (
                EvalLogger.objects.filter(
                    trace_id=trace_id,
                    custom_eval_config_id__in=[c.id for c in eval_configs],
                )
                .order_by("custom_eval_config_id", "-created_at")
                .distinct("custom_eval_config_id")
            )
            eval_log_map = {str(e.custom_eval_config_id): e for e in eval_logs}

            eval_outputs = {}
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

                log = eval_log_map.get(config_id)
                if log is None:
                    eval_outputs[config_id] = {
                        "name": metric_name,
                        "output_type": output_type,
                        "output": None,
                        "reason": None,
                        "error": None,
                    }
                    continue

                metric_entry = {
                    "name": metric_name,
                    "output_type": output_type,
                    "reason": log.eval_explanation,
                    "error": log.error,
                }
                if log.output_str_list:
                    metric_entry["output"] = log.output_str_list
                elif output_type == "Pass/Fail" and log.output_bool is not None:
                    metric_entry["output"] = "Pass" if log.output_bool else "Fail"
                elif log.output_float is not None:
                    metric_entry["output"] = round(log.output_float * 100, 2)
                else:
                    metric_entry["output"] = None
                eval_outputs[config_id] = metric_entry

            # Use the stored call.duration from eval_attributes as the single
            # source of truth so the API response always matches the metric.
            stored_duration = attrs.get(CallAttributes.DURATION)
            if stored_duration is not None:
                stored_duration = int(stored_duration)

            # NOTE: we intentionally do NOT set `customer_latency_metrics` or
            # `customer_cost_breakdown` here. The correct values live on
            # CallExecution and are already merged into the drawer data from
            # the call-logs list endpoint (simulate path). For pure observe
            # traffic with no CallExecution, the frontend falls back to the
            # provider-reported metrics in `raw_log.artifact.performanceMetrics`
            # and `raw_log.costBreakdown`. Writing anything here would clobber
            # those with an unrelated span-aggregate of our own spans.
            result = {
                **processed_log,
                "id": str(trace.id),
                "trace_id": str(trace.id),
                "project_id": str(trace.project_id),
                "provider_call_id": processed_log.get("call_id"),
                "recording": recording,
                "call_metadata": metadata,
                "observation_span": observation_span,
                "eval_outputs": eval_outputs,
                "turn_count": voice_metrics.get("turn_count"),
                "talk_ratio": voice_metrics.get("talk_ratio"),
                "agent_talk_percentage": voice_metrics.get("agent_talk_percentage"),
                "avg_agent_latency_ms": attrs.get("avg_agent_latency_ms"),
                "user_wpm": attrs.get(CallAttributes.USER_WPM),
                "bot_wpm": attrs.get(CallAttributes.BOT_WPM),
                "user_interruption_count": attrs.get("user_interruption_count"),
                "ai_interruption_count": attrs.get("ai_interruption_count"),
            }
            if stored_duration is not None:
                result["duration_seconds"] = stored_duration
            return self._gm.success_response(result)

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
            span_attributes_raw,
            span_attr_str,
            span_attr_num,
            metadata_map
        FROM spans
        WHERE project_id = toUUID(%(project_id)s)
          AND trace_id = %(trace_id)s
          AND _peerdb_is_deleted = 0
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

        # Parse span_attributes_raw to get raw_log
        span_attrs_raw = row.get("span_attributes_raw", "{}")
        try:
            span_attrs = (
                json.loads(span_attrs_raw)
                if isinstance(span_attrs_raw, str)
                else (span_attrs_raw or {})
            )
        except (json.JSONDecodeError, TypeError):
            span_attrs = {}

        raw_log = span_attrs.get("raw_log") or {}
        metadata = {}
        metadata_map = row.get("metadata_map")
        if isinstance(metadata_map, dict):
            metadata = metadata_map

        processed_log = ObservabilityService.process_raw_logs(
            raw_log, provider, span_attributes=span_attrs
        )
        voice_metrics = self._extract_voice_turn_and_talk_metrics(span_attrs, raw_log)

        attr_str = row.get("span_attr_str") or {}
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
            span_attributes_raw,
            span_attr_str,
            span_attr_num,
            span_attr_bool,
            metadata_map,
            status_message,
            tags
        FROM spans
        WHERE project_id = toUUID(%(project_id)s)
          AND trace_id = %(trace_id)s
          AND _peerdb_is_deleted = 0
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
            child_attrs_raw = child.get("span_attributes_raw", "{}")
            try:
                child_span_attrs = (
                    json.loads(child_attrs_raw)
                    if isinstance(child_attrs_raw, str)
                    else (child_attrs_raw or {})
                )
            except (json.JSONDecodeError, TypeError):
                child_span_attrs = {}

            child_attr_str = child.get("span_attr_str") or {}
            child_attr_num = child.get("span_attr_num") or {}
            child_attr_bool = child.get("span_attr_bool") or {}
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
                    "metadata": child.get("metadata_map") or {},
                    "tags": child.get("tags") or [],
                }
            )

        # Fetch ALL non-deleted eval configs for the project so the drawer
        # renders the same set of evals as the list columns. Missing scores
        # become placeholder entries with `output=None`.
        eval_configs = CustomEvalConfig.objects.filter(
            id__in=EvalLogger.objects.filter(trace__project_id=project_id)
            .values("custom_eval_config_id")
            .distinct(),
            deleted=False,
        ).select_related("eval_template")
        eval_config_ids = [str(c.id) for c in eval_configs]

        eval_outputs = {}
        trace_evals: Dict[str, Any] = {}
        if eval_config_ids:
            eval_query = f"""
            SELECT
                trace_id,
                toString(custom_eval_config_id) AS eval_config_id,
                avg(output_float) AS avg_score,
                avg(CASE WHEN output_bool = 1 THEN 100.0 ELSE 0.0 END) AS pass_rate,
                count() AS eval_count,
                any(output_str_list) AS output_str_list
            FROM tracer_eval_logger FINAL
            WHERE _peerdb_is_deleted = 0
              AND trace_id = %(trace_id)s
              AND custom_eval_config_id IN %(eval_config_ids)s
            GROUP BY trace_id, custom_eval_config_id
            """
            eval_result = analytics.execute_ch_query(
                eval_query,
                {
                    "trace_id": str(trace_id),
                    "eval_config_ids": tuple(eval_config_ids),
                },
                timeout_ms=30000,
            )
            eval_map = TraceListQueryBuilder.pivot_eval_results(
                [(list(r.values())) for r in eval_result.data],
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
        span_attr_num = row.get("span_attr_num") or {}
        stored_duration = span_attr_num.get(CallAttributes.DURATION)

        # See PG path for rationale — do not set customer_latency_metrics /
        # customer_cost_breakdown; they flow in via the list merge or fall
        # back to raw_log provider metrics on the frontend.
        result = {
            **processed_log,
            "id": str(trace_id),
            "trace_id": str(trace_id),
            "project_id": str(project_id),
            "provider_call_id": processed_log.get("call_id"),
            "recording": recording,
            "call_metadata": metadata,
            "observation_span": observation_span,
            "eval_outputs": eval_outputs,
            "turn_count": voice_metrics.get("turn_count"),
            "talk_ratio": voice_metrics.get("talk_ratio"),
            "agent_talk_percentage": voice_metrics.get("agent_talk_percentage"),
            "avg_agent_latency_ms": span_attrs.get("avg_agent_latency_ms")
            or span_attr_num.get("avg_agent_latency_ms"),
            "user_wpm": span_attrs.get(CallAttributes.USER_WPM)
            or span_attr_num.get(CallAttributes.USER_WPM),
            "bot_wpm": span_attrs.get(CallAttributes.BOT_WPM)
            or span_attr_num.get(CallAttributes.BOT_WPM),
            "user_interruption_count": span_attrs.get("user_interruption_count")
            or span_attr_num.get("user_interruption_count"),
            "ai_interruption_count": span_attrs.get("ai_interruption_count")
            or span_attr_num.get("ai_interruption_count"),
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
            (f.get("column_id") or f.get("columnId")) in ("created_at", "start_time")
            for f in filters
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
          AND _peerdb_is_deleted = 0
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
          AND _peerdb_is_deleted = 0
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
          AND _peerdb_is_deleted = 0
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

    @action(detail=False, methods=["get"])
    def get_trace_id_by_index_observe(self, request, *args, **kwargs):
        """
        Get the previous and next trace id by index.
        """
        try:
            trace_id = request.query_params.get("trace_id") or request.query_params.get(
                "traceId"
            )
            if not trace_id:
                raise Exception("Trace id is required")

            project_id = request.query_params.get(
                "project_id"
            ) or request.query_params.get("projectId")
            if not project_id:
                raise Exception("Project id is required")

            project = Project.objects.get(
                id=project_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            if project.trace_type != "observe":
                raise Exception("Project should be of type observe")

            # Parse filters for CH path
            filters = request.query_params.get("filters", "[]")
            try:
                if filters:
                    filters = json.loads(filters)
            except json.JSONDecodeError:
                filters = []

            # ClickHouse dispatch — simple prev/next by start_time
            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.TRACE_OF_SESSION_LIST):
                try:
                    return self._get_trace_id_by_index_observe_clickhouse(
                        request, trace_id, project_id, filters, analytics
                    )
                except Exception as e:
                    logger.warning(
                        "CH get_trace_id_by_index_observe failed, falling back to PG",
                        error=str(e),
                    )

            # PG fallback — base query with annotations
            base_query = Trace.objects.filter(project_id=project_id).annotate(
                node_type=Subquery(
                    ObservationSpan.objects.filter(
                        trace_id=OuterRef("id"), parent_span_id__isnull=True
                    ).values("observation_type")[:1]
                ),
                trace_id=F("id"),
                trace_name=Subquery(
                    ObservationSpan.objects.filter(
                        trace_id=OuterRef("id"), parent_span_id__isnull=True
                    ).values("name")[:1]
                ),
                user_id=Subquery(
                    ObservationSpan.objects.filter(
                        trace_id=OuterRef("id"), parent_span_id__isnull=True
                    ).values("end_user__user_id")[:1]
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

            # Get all eval configs for the project
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(trace__project_id=project_id)
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

            # Add Span Annotations
            annotation_labels = get_annotation_labels_for_project(project_id)
            base_query = self._build_annotation_subqueries(
                base_query, annotation_labels, request.user.organization
            )

            # Apply filters
            filters = request.query_params.get("filters", "[]")
            try:
                if filters:
                    filters = json.loads(filters)
            except json.JSONDecodeError as e:
                return self._gm.bad_request(
                    f"Invalid JSON format in filters parameter: {str(e)}"
                )

            if filters:
                # Apply system metric filters
                # Override span_id: base_query is on Trace, so span_id
                # must resolve through the ObservationSpan relation.
                filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(
                        filters,
                        field_map={
                            **FilterEngine.DEFAULT_FIELD_MAP,
                            "span_id": "observation_spans__id",
                        },
                    )
                )
                if filter_conditions:
                    base_query = base_query.filter(filter_conditions)

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
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
                ]

                # Apply eval metric filters (excluding annotation filters)
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

                # Apply span attribute filters
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
            }  # Its reverse coz by next we mean next in line which is ideally the immediate previous one

            return self._gm.success_response(response)

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
            project_id = request.query_params.get(
                "project_id"
            ) or request.query_params.get("projectId")
            if not project_id:
                return self._gm.bad_request("Project id is required")

            project = Project.no_workspace_objects.get(
                id=project_id, organization=request.user.organization
            )

            # Check if project has voice/conversation traces
            has_voice_traces = ObservationSpan.objects.filter(
                trace__project_id=project_id,
                parent_span_id__isnull=True,
                observation_type="conversation",
            ).exists()

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
        query_data = {"filters": request.query_params.get("filters", "[]")}
        try:
            if query_data["filters"]:
                query_data["filters"] = json.loads(query_data["filters"])
        except json.JSONDecodeError:
            query_data["filters"] = []

        serializer = SpanExportSerializer(data=query_data)
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
                and (f.get("column_id") or f.get("columnId"))
                not in annotation_column_ids
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
        """
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        org_scope = bool(org_project_ids)
        filters = list(validated_data.get("filters", []) or [])
        page_number = int(request.query_params.get("page_number", 0))
        page_size = int(request.query_params.get("page_size", 30))

        # Resolve user_id filter values (strings) to end_user_id UUIDs.
        # `spans` only populates `end_user_id` on the user-facing child span,
        # not the root span the trace list queries. A direct equality on the
        # root-span `end_user_id` would miss most traces, so we rewrite the
        # filter to a `trace_id IN (subquery)` condition using a dedicated
        # col_type that the CH filter builder handles specially.
        if org is None:
            org = getattr(request, "organization", None) or request.user.organization
        _resolved: List[Dict] = []
        for _f in filters:
            _col = _f.get("column_id") or _f.get("columnId")
            _cfg = _f.get("filter_config") or _f.get("filterConfig") or {}
            _col_type = _cfg.get("col_type") or _cfg.get("colType") or "NORMAL"
            if _col == "user_id" and _col_type == "NORMAL":
                _val = _cfg.get("filter_value", _cfg.get("filterValue"))
                _vals = _val if isinstance(_val, list) else [_val]
                _vals = [v for v in _vals if v]
                if not _vals:
                    _resolved.append(_f)
                    continue
                _eu_qs = EndUser.objects.filter(
                    user_id__in=_vals,
                    organization=org,
                    deleted=False,
                )
                if not org_scope and project_id:
                    _eu_qs = _eu_qs.filter(project_id=project_id)
                _ids = [str(u) for u in _eu_qs.values_list("id", flat=True)]
                if not _ids:
                    _ids = ["00000000-0000-0000-0000-000000000000"]
                _resolved.append(
                    {
                        "column_id": "end_user_id",
                        "filter_config": {
                            "col_type": "TRACE_END_USER",
                            "filter_type": "text",
                            "filter_op": "in",
                            "filter_value": _ids,
                        },
                    }
                )
                continue
            _resolved.append(_f)
        filters = _resolved

        # Get eval config IDs. Project mode uses a CH dict-lookup (fast);
        # org mode uses a PG scan because the CH dict-lookup takes a single
        # project_id — multi-project CH variant not implemented yet.
        eval_config_ids = []
        if org_scope:
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    trace__project_id__in=org_project_ids
                )
                .values("custom_eval_config_id")
                .distinct(),
                deleted=False,
            ).select_related("eval_template")
            eval_config_ids = [str(c.id) for c in eval_configs]
        else:
            ch_result = analytics.execute_ch_query(
                "SELECT DISTINCT toString(custom_eval_config_id) AS cid "
                "FROM tracer_eval_logger FINAL "
                "WHERE _peerdb_is_deleted = 0 "
                "AND dictGet('trace_dict', 'project_id', "
                "trace_id) = toUUID(%(pid)s)",
                {"pid": str(project_id)},
                timeout_ms=30000,
            )
            ch_ids = [r.get("cid", "") for r in ch_result.data if r.get("cid")]
            if ch_ids:
                eval_configs = CustomEvalConfig.objects.filter(
                    id__in=ch_ids, deleted=False
                ).select_related("eval_template")
                eval_config_ids = [str(c.id) for c in eval_configs]
            else:
                eval_configs = []

        # Annotation labels — skip in org-scoped mode (deferred enhancement)
        if org_scope:
            annotation_labels = []
        else:
            annotation_labels = get_annotation_labels_for_project(project_id)
        annotation_label_ids = [str(l.id) for l in annotation_labels]
        label_types = {str(l.id): l.type for l in annotation_labels}

        builder = TraceListQueryBuilder(
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

        # Count
        count_query, count_params = builder.build_count_query()
        count_result = analytics.execute_ch_query(
            count_query, count_params, timeout_ms=30000
        )
        total_count = count_result.data[0].get("total", 0) if count_result.data else 0

        # Phase 1b: Fetch heavy columns (input/output/attrs) for the page
        trace_ids = [str(row.get("trace_id", "")) for row in result.data]
        content_map = {}
        if trace_ids:
            content_query, content_params = builder.build_content_query(trace_ids)
            if content_query:
                content_result = analytics.execute_ch_query(
                    content_query, content_params, timeout_ms=10000
                )
                for crow in content_result.data:
                    content_map[str(crow.get("trace_id", ""))] = crow

        # Merge content into Phase 1 results
        for row in result.data:
            tid = str(row.get("trace_id", ""))
            content = content_map.get(tid, {})
            row["input"] = content.get("input", "")
            row["output"] = content.get("output", "")
            row["span_attr_str"] = content.get("span_attr_str", {})
            row["span_attr_num"] = content.get("span_attr_num", {})
            row["metadata_map"] = content.get("metadata_map", {})
            row["trace_tags"] = content.get("trace_tags", [])

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

        # Phase 3: Annotations — fetch from PG Score (unified annotation system)
        annotation_map = _build_annotation_map_from_scores(
            trace_ids, annotation_label_ids, label_types
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
                        raw = attr_row.get("span_attributes_raw", "{}")
                        try:
                            attrs = (
                                json.loads(raw) if isinstance(raw, str) else (raw or {})
                            )
                        except (json.JSONDecodeError, TypeError):
                            attrs = {}
                        # Fallback: merge from typed Map columns when raw is empty
                        if not attrs:
                            str_map = attr_row.get("span_attr_str") or {}
                            num_map = attr_row.get("span_attr_num") or {}
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
            }

            # Add eval metrics
            trace_evals = eval_map.get(trace_id, {})
            for config in eval_configs:
                config_id = str(config.id)
                if config_id not in trace_evals:
                    continue
                scores = trace_evals[config_id]
                # CHOICES eval: spread per-choice percentages into
                # separate columns keyed ``{config_id}**{choice}``.
                if isinstance(scores, dict) and scores.get("per_choice"):
                    for choice, pct in scores["per_choice"].items():
                        entry[f"{config_id}**{choice}"] = pct
                elif isinstance(scores, dict) and "avg_score" in scores:
                    # Prefer ``avg_score`` when it's present. A plain
                    # ``avg_score or pass_rate`` drops a legitimate 0.0
                    # (Fail) because ``0.0`` is falsy — use an explicit
                    # ``None`` check so Fail doesn't silently fall
                    # through to ``pass_rate``.
                    avg_val = scores.get("avg_score")
                    entry[config_id] = (
                        avg_val if avg_val is not None else scores.get("pass_rate")
                    )
                else:
                    entry[config_id] = scores

            # Add annotations
            trace_annotations = annotation_map.get(trace_id, {})
            for label in annotation_labels:
                label_id = str(label.id)
                if label_id in trace_annotations:
                    entry[label_id] = trace_annotations[label_id]

            # Include metadata for custom columns
            metadata = row.get("metadata_map") or {}
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
        """List voice calls using ClickHouse backend."""
        from tracer.services.clickhouse.query_builders import VoiceCallListQueryBuilder
        from tracer.services.clickhouse.query_builders.trace_list import (
            TraceListQueryBuilder,
        )

        filters = validated_data.get("filters", [])
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 30))
        page_number = page - 1  # Convert 1-based to 0-based

        # Get eval config IDs from CH (fast) instead of PG EvalLogger scan
        eval_config_ids = []
        ch_result = analytics.execute_ch_query(
            "SELECT DISTINCT toString(custom_eval_config_id) AS cid "
            "FROM tracer_eval_logger FINAL "
            "WHERE _peerdb_is_deleted = 0 "
            "AND dictGet('trace_dict', 'project_id', "
            "trace_id) = toUUID(%(pid)s)",
            {"pid": str(project_id)},
            timeout_ms=30000,
        )
        ch_ids = [r.get("cid", "") for r in ch_result.data if r.get("cid")]
        if ch_ids:
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=ch_ids, deleted=False
            ).select_related("eval_template")
            eval_config_ids = [str(c.id) for c in eval_configs]
        else:
            eval_configs = []

        # Get annotation labels that have actual annotations/scores for this project
        annotation_labels = get_annotation_labels_for_project(project_id)
        annotation_label_ids = [str(l.id) for l in annotation_labels]
        label_types = {str(l.id): l.type for l in annotation_labels}

        sim_flag = remove_simulation_calls and str(
            remove_simulation_calls
        ).lower() not in ("false", "0", "")

        builder = VoiceCallListQueryBuilder(
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

        # Phase 1b: Fetch span_attributes from the CH CDC table for the
        # paginated spans.  The denormalized `spans` table has empty
        # span_attributes_raw (MV wasn't in place during initial sync),
        # but the CDC source `tracer_observation_span` has full data.
        span_ids = [
            str(row.get("span_id", "")) for row in result.data if row.get("span_id")
        ]
        attrs_map = {}
        if span_ids:
            attrs_result = analytics.execute_ch_query(
                "SELECT id, span_attributes, provider "
                "FROM tracer_observation_span FINAL "
                "PREWHERE id IN %(span_ids)s "
                "WHERE _peerdb_is_deleted = 0",
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

        trace_ids = [str(row.get("trace_id", "")) for row in result.data]

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
        for row in result.data:
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

            raw_log = span_attrs.get("raw_log") or {}
            voice_metrics = self._extract_voice_turn_and_talk_metrics(
                span_attrs, raw_log
            )

            # Process raw_log through existing provider-specific logic
            processed_log = ObservabilityService.process_raw_logs(
                raw_log, provider, span_attributes=span_attrs
            )

            entry = {
                **processed_log,
                "id": trace_id,
                "trace_id": trace_id,
                "turn_count": voice_metrics.get("turn_count"),
                "talk_ratio": voice_metrics.get("talk_ratio"),
                "agent_talk_percentage": voice_metrics.get("agent_talk_percentage"),
                "avg_agent_latency_ms": span_attrs.get("avg_agent_latency_ms"),
                "user_wpm": span_attrs.get("call.user_wpm"),
                "bot_wpm": span_attrs.get("call.bot_wpm"),
                "user_interruption_count": span_attrs.get("user_interruption_count"),
                "ai_interruption_count": span_attrs.get("ai_interruption_count"),
            }
            # Only override with voice_metrics if they have values —
            # otherwise keep the ones computed by process_raw_logs.
            if voice_metrics.get("turn_count") is not None:
                entry["turn_count"] = voice_metrics["turn_count"]
            if voice_metrics.get("talk_ratio") is not None:
                entry["talk_ratio"] = voice_metrics["talk_ratio"]
            if voice_metrics.get("agent_talk_percentage") is not None:
                entry["agent_talk_percentage"] = voice_metrics["agent_talk_percentage"]
            # Backfill response_time_ms from avg_agent_latency if VAPI didn't set it
            if not entry.get("response_time_ms") and entry.get("avg_agent_latency_ms"):
                entry["response_time_ms"] = entry["avg_agent_latency_ms"]

            # Strip heavy fields from list response — these are served by
            # the voice_call_detail endpoint.
            for key in self._VOICE_CALL_HEAVY_KEYS:
                entry.pop(key, None)
            entry.setdefault("observation_span", [])

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
                        if isinstance(scores, dict):
                            if scores.get("per_choice"):
                                metric_entry["output"] = [
                                    k
                                    for k, v in scores["per_choice"].items()
                                    if v > 0
                                ]
                                metric_entry["output_type"] = "str_list"
                            elif "str_list" in scores and scores["str_list"]:
                                metric_entry["output"] = scores["str_list"]
                                metric_entry["output_type"] = "str_list"
                            elif "avg_score" in scores:
                                score_val = scores.get("avg_score") or scores.get(
                                    "pass_rate"
                                )
                                if output_type == "Pass/Fail":
                                    metric_entry["output"] = (
                                        "Pass"
                                        if score_val and score_val > 0
                                        else "Fail"
                                    )
                                else:
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

    def _list_traces_clickhouse(self, request, project_version_id, analytics):
        """List traces using ClickHouse backend."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        filters = request.query_params.get("filters", "[]")
        sort_params = request.query_params.get(
            "sort_params", "[]"
        ) or request.query_params.get("sortParams", "[]")

        import json

        filters = json.loads(filters) if isinstance(filters, str) else filters
        sort_params = (
            json.loads(sort_params) if isinstance(sort_params, str) else sort_params
        )

        page_number = int(request.query_params.get("page_number", 0))
        page_size = int(request.query_params.get("page_size", 30))

        # Get project_id from project_version
        project_version = ProjectVersion.objects.get(
            id=project_version_id,
            project__organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
        )
        project_id = str(project_version.project_id)

        # Get eval config IDs from CH (fast) instead of PG EvalLogger scan
        eval_config_ids = []
        ch_result = analytics.execute_ch_query(
            "SELECT DISTINCT toString(custom_eval_config_id) AS cid "
            "FROM tracer_eval_logger FINAL "
            "WHERE _peerdb_is_deleted = 0 "
            "AND dictGet('trace_dict', 'project_id', "
            "trace_id) = toUUID(%(pid)s)",
            {"pid": project_id},
            timeout_ms=30000,
        )
        ch_ids = [r.get("cid", "") for r in ch_result.data if r.get("cid")]
        if ch_ids:
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=ch_ids, deleted=False
            ).select_related("eval_template")
            eval_config_ids = [str(c.id) for c in eval_configs]
        else:
            eval_configs = []

        # Get annotation labels that have actual annotations for this project
        annotation_labels = get_annotation_labels_for_project(
            project_version.project_id
        )
        annotation_label_ids = [str(l.id) for l in annotation_labels]
        label_types = {str(l.id): l.type for l in annotation_labels}

        builder = TraceListQueryBuilder(
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
                "session_id": row.get("trace_session_id"),
                "tags": row.get("trace_tags") or [],
            }

            # Add eval metrics matching PG format
            trace_evals = eval_map.get(trace_id, {})
            for config in eval_configs:
                config_id = str(config.id)
                if config_id in trace_evals:
                    scores = trace_evals[config_id]
                    if isinstance(scores, dict) and "avg_score" in scores:
                        entry[config_id] = scores.get("avg_score") or scores.get(
                            "pass_rate"
                        )
                    else:
                        entry[config_id] = scores

            # Add annotations
            trace_annotations = annotation_map.get(trace_id, {})
            for label in annotation_labels:
                label_id = str(label.id)
                if label_id in trace_annotations:
                    entry[label_id] = trace_annotations[label_id]

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

    @action(detail=False, methods=["get"])
    def agent_graph(self, request, *args, **kwargs):
        """Return the aggregate agent graph for a project.

        Computes nodes (distinct span types/names) and edges (parent→child
        transitions) across all traces in the given time window.
        """
        try:
            project_id = request.query_params.get(
                "project_id"
            ) or request.query_params.get("projectId")
            if not project_id:
                return self._gm.bad_request("Project id is required")

            filters_raw = request.query_params.get("filters", "[]")
            try:
                filters = json.loads(filters_raw)
            except json.JSONDecodeError:
                filters = []

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

    def get(self, request, *args, **kwargs):
        """
        List traces filtered by project ID with optimized queries.
        """
        try:
            project_id = request.query_params.get("project_id") or None
            search = request.query_params.get("search", "")
            page_size = int(request.query_params.get("page_size", 30))
            current_page = int(request.query_params.get("current_page_index", 0))
            search_name = search.strip() if search else None
            organization_id = request.user.organization.id
            limit = page_size
            offset = current_page * page_size
            sort_params = request.query_params.get("sort_params", [])
            filters = request.query_params.get("filters", [])

            # Convert string parameters to appropriate types
            try:
                page_size = int(page_size)
                current_page = int(current_page)
            except (ValueError, TypeError):
                page_size = 10
                current_page = 0

            # Parse sort_params and filters if they're strings
            if isinstance(sort_params, str):
                try:
                    sort_params = json.loads(sort_params)
                except (ValueError, TypeError):
                    sort_params = []

            if isinstance(filters, str):
                try:
                    filters = json.loads(filters)
                except (ValueError, TypeError):
                    filters = []

            column_mapping = {
                "user_id": "user_id",
                "activated_at": "created_at",
                "avg_trace_latency": "avg_latency_trace",
                "total_cost": "total_cost",
                "total_tokens": "total_tokens",
                "input_tokens": "input_tokens",
                "output_tokens": "output_tokens",
                "num_traces": "num_traces",
                # NOTE: num_sessions is computed in the SQL as COALESCE(fo.num_sessions, 0).
                # Filtering with "equals 0" must match NULL session aggregates (no sessions),
                # so we coalesce in the filter expression as well.
                "num_sessions": "COALESCE(fo.num_sessions, 0)",
                "avg_session_duration": "avg_session_duration_seconds",
                "num_llm_calls": "num_llm_calls",
                "num_guardrails_triggered": "num_guardrails_triggered",
                "last_active": "la.last_active",
                "num_active_days": "num_active_days",
                "num_traces_with_errors": "num_traces_with_errors",
                "bool_eval_pass_rate": "bool_eval_pass_rate",
                "avg_output_float": "avg_output_float",
                "user_id_hash": "user_id_hash",
                "user_id_type": "user_id_type",
            }

            column = None
            sort_order = None
            if isinstance(sort_params, dict) and sort_params:
                column = sort_params.get("column_id")
                column = column_mapping.get(column, None)
                direction = sort_params.get("direction", "asc")
                if direction == "desc":
                    sort_order = "DESC"
                else:
                    sort_order = "ASC"

            query_params = {
                "org_id": organization_id,
                "project_id": project_id,
                "search_name": search_name,
                "limit": limit,
                "offset": offset,
                "filters": filters,
                "column_map": column_mapping,
                "workspace_id": request.workspace.id,
            }

            if column and sort_order:
                query_params["sort_by"] = column
                query_params["sort_order"] = sort_order

            results = SQLQueryHandler.get_spans_by_end_users(**query_params)
            output = []
            count = 0
            for result in results:
                output.append(
                    {
                        "user_id": result[0],
                        "total_cost": round(result[1], 6) if result[1] else 0,
                        "total_tokens": result[2],
                        "input_tokens": result[3],
                        "output_tokens": result[4],
                        "num_traces": result[5],
                        "num_sessions": result[6],
                        "avg_session_duration": result[7],
                        "avg_trace_latency": result[8],
                        "num_llm_calls": result[9],
                        "num_guardrails_triggered": result[10],
                        "activated_at": result[11],
                        "last_active": result[12],
                        "num_active_days": result[13],
                        "num_traces_with_errors": result[14],
                        "bool_eval_pass_rate": result[15],
                        "avg_output_float": result[16],
                        "project_id": result[17],
                        "user_id_type": result[19],
                        "user_id_hash": result[20],
                        "end_user_id": result[21],
                    }
                )
                count = result[18]

            # Enrich with aggregated span attributes from ClickHouse
            end_user_ids = [
                r.get("end_user_id") for r in output if r.get("end_user_id")
            ]
            if end_user_ids:
                try:
                    analytics = AnalyticsQueryService()
                    _SKIP_ATTR_PREFIXES = (
                        "raw.",
                        "llm.input_messages",
                        "llm.output_messages",
                        "input.value",
                        "output.value",
                    )
                    attr_params = {"eu_ids": tuple(str(e) for e in end_user_ids)}
                    project_clause = ""
                    if project_id:
                        attr_params["attr_pid"] = str(project_id)
                        project_clause = "AND project_id = toUUID(%(attr_pid)s)"

                    attr_query = f"""
                    SELECT
                        end_user_id,
                        span_attributes_raw,
                        span_attr_str,
                        span_attr_num
                    FROM spans
                    PREWHERE end_user_id IN %(eu_ids)s
                    WHERE _peerdb_is_deleted = 0
                      {project_clause}
                      AND (
                        (span_attributes_raw != '{{}}' AND span_attributes_raw != '')
                        OR length(mapKeys(span_attr_str)) > 0
                        OR length(mapKeys(span_attr_num)) > 0
                      )
                    """
                    attr_result = analytics.execute_ch_query(
                        attr_query, attr_params, timeout_ms=30000
                    )
                    # Aggregate per user
                    user_attrs: dict = {}
                    for attr_row in attr_result.data:
                        uid = str(attr_row.get("end_user_id", ""))
                        raw = attr_row.get("span_attributes_raw", "{}")
                        try:
                            attrs = (
                                json.loads(raw) if isinstance(raw, str) else (raw or {})
                            )
                        except (json.JSONDecodeError, TypeError):
                            attrs = {}
                        # Fallback: merge from typed Map columns when raw is empty
                        if not attrs:
                            str_map = attr_row.get("span_attr_str") or {}
                            num_map = attr_row.get("span_attr_num") or {}
                            if isinstance(str_map, dict):
                                attrs.update(str_map)
                            if isinstance(num_map, dict):
                                for k, v in num_map.items():
                                    if k not in attrs:
                                        attrs[k] = v
                        if uid not in user_attrs:
                            user_attrs[uid] = {}
                        for key, value in attrs.items():
                            if key.startswith(_SKIP_ATTR_PREFIXES):
                                continue
                            if isinstance(value, str) and len(value) > 500:
                                continue
                            if key not in user_attrs[uid]:
                                user_attrs[uid][key] = (
                                    set()
                                    if isinstance(value, (str, int, float, bool))
                                    else []
                                )
                            if isinstance(value, (str, int, float, bool)):
                                user_attrs[uid][key].add(
                                    value
                                    if not isinstance(value, bool)
                                    else str(value).lower()
                                )
                    # Merge into output rows
                    for entry in output:
                        euid = str(entry.get("end_user_id", ""))
                        for key, values in user_attrs.get(euid, {}).items():
                            if key not in entry:
                                if isinstance(values, set):
                                    vals = sorted(values, key=str)
                                    entry[key] = vals[0] if len(vals) == 1 else vals
                                else:
                                    entry[key] = values
                except Exception as e:
                    logger.warning(f"User span attribute enrichment failed: {e}")

            final_output = {
                "table": output,
                "total_count": count,
                "total_pages": (count // page_size)
                + (1 if count % page_size > 0 else 0),
            }

            return self._gm.success_response(final_output)

        except Exception as e:
            logger.exception(f"ERROR {e}")
            return self._gm.bad_request(f"error fetching users: {str(e)}")


class GetUserCodeExampleView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

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
