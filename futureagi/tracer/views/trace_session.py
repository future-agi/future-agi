import io
import json
import traceback
from collections import defaultdict
from dataclasses import asdict
from typing import Dict, List, Optional

try:
    import orjson

    _json_loads = orjson.loads
except ImportError:
    _json_loads = json.loads

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)
from django.db import models
from django.db.models import (
    Avg,
    Case,
    Count,
    DurationField,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
    When,
)
from django.db.models.functions import (
    Coalesce,
    Round,
)
from django.http import FileResponse
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import (
    EndUser,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.project import Project
from tracer.models.trace import Trace

session_logger = structlog.get_logger(__name__)
from tracer.models.trace_session import TraceSession
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.serializers.eval_task import PaginationQuerySerializer
from tracer.serializers.trace_session import (
    TraceSessionExportSerializer,
    TraceSessionSerializer,
)
from tracer.utils.filters import FilterEngine, apply_created_at_filters
from tracer.utils.helper import (
    FieldConfig,
    format_datetime_fields_to_iso,
    format_datetime_to_iso,
    get_default_project_session_config,
)
from tracer.utils.session import get_session_navigation


class TraceSessionView(BaseModelViewSetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = TraceSessionSerializer

    def get_queryset(self):
        trace_session_id = self.kwargs.get("pk")
        # Get base queryset with automatic filtering from mixin
        queryset = super().get_queryset()

        if trace_session_id:
            queryset = queryset.filter(id=trace_session_id)

        project_id = self.request.query_params.get("project_id")
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        return queryset

    def retrieve(self, request, *args, **kwargs):
        try:
            trace_session_id = self.kwargs.get("pk")
            trace_session = TraceSession.objects.get(
                id=trace_session_id,
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            project_id = trace_session.project.id

            # ClickHouse dispatch for session detail
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
                QueryType,
            )

            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.TRACE_DETAIL):
                try:
                    return self._retrieve_clickhouse(
                        request, trace_session_id, trace_session, project_id, analytics
                    )
                except Exception as e:
                    logger.warning(
                        "CH session retrieve failed, falling back to PG", error=str(e)
                    )

            serializer = self.get_serializer(trace_session)
            trace_session = serializer.data

            page_number = int(self.request.query_params.get("page_number", 0))
            page_size = int(self.request.query_params.get("page_size", 30))
            page_start = page_number * page_size
            page_end = page_start + page_size + 1

            trace_qs = Trace.objects.filter(session_id=trace_session_id)
            total_traces = trace_qs.count()
            trace_id_subquery = trace_qs.values("id")

            session_aggregates = ObservationSpan.objects.filter(
                trace_id__in=trace_id_subquery
            ).aggregate(
                start_time=Min("start_time"),
                end_time=Max("end_time"),
                total_cost=Coalesce(
                    Sum("cost", output_field=models.FloatField()),
                    0.0,
                ),
                total_tokens=Coalesce(
                    Sum("total_tokens", output_field=models.IntegerField()),
                    0,
                ),
            )

            session_start_time = session_aggregates["start_time"]
            session_end_time = session_aggregates["end_time"]
            duration = (
                (session_end_time - session_start_time).total_seconds()
                if session_end_time and session_start_time
                else 0
            )

            session_metadata = {
                "session_id": str(trace_session_id),
                "duration": duration,
                "total_cost": (
                    round(session_aggregates["total_cost"], 6)
                    if session_aggregates["total_cost"]
                    and session_aggregates["total_cost"] > 0
                    else 0
                ),
                "total_traces": total_traces,
                "start_time": format_datetime_to_iso(session_start_time),
                "end_time": format_datetime_to_iso(session_end_time),
                "total_tokens": session_aggregates["total_tokens"],
            }

            traces = list(
                Trace.objects.filter(session_id=trace_session_id).order_by(
                    "created_at"
                )[page_start:page_end]
            )
            has_next = len(traces) > page_size
            traces = traces[:page_size]

            if not traces:
                next_session_id, previous_session_id = get_session_navigation(
                    request, project_id, trace_session_id
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

            paginated_trace_ids = [t.id for t in traces]

            span_aggs = (
                ObservationSpan.objects.filter(trace_id__in=paginated_trace_ids)
                .values("trace_id")
                .annotate(
                    root_start_time=Min(
                        Case(When(parent_span_id__isnull=True, then="start_time"))
                    ),
                    root_latency_ms=Min(
                        Case(When(parent_span_id__isnull=True, then="latency_ms"))
                    ),
                    total_cost=Coalesce(
                        Round(Sum("cost", output_field=FloatField()), 6), 0.0
                    ),
                    total_tokens=Coalesce(
                        Sum("total_tokens", output_field=IntegerField()), 0
                    ),
                    input_tokens=Coalesce(
                        Sum("prompt_tokens", output_field=IntegerField()), 0
                    ),
                    output_tokens=Coalesce(
                        Sum("completion_tokens", output_field=IntegerField()), 0
                    ),
                )
            )
            span_agg_map = {str(row["trace_id"]): row for row in span_aggs}

            eval_configs = list(
                CustomEvalConfig.objects.filter(
                    id__in=EvalLogger.objects.filter(
                        trace_id__in=trace_id_subquery
                    ).values("custom_eval_config_id"),
                    deleted=False,
                ).select_related("eval_template")
            )

            eval_config_ids = [c.id for c in eval_configs]
            eval_map = {}  # (trace_id_str, config_id_str) -> score data
            explanation_map = {}  # config_id_str -> explanation

            if eval_config_ids:
                eval_aggs = (
                    EvalLogger.objects.filter(
                        trace_id__in=paginated_trace_ids,
                        custom_eval_config_id__in=eval_config_ids,
                    )
                    .exclude(Q(output_str="ERROR") | Q(error=True))
                    .values("trace_id", "custom_eval_config_id")
                    .annotate(
                        float_score=Round(Avg("output_float") * 100, 2),
                        bool_score=Round(
                            Avg(
                                Case(
                                    When(output_bool=True, then=100.0),
                                    When(output_bool=False, then=0.0),
                                    default=None,
                                    output_field=FloatField(),
                                )
                            ),
                            2,
                        ),
                        float_count=Count("output_float"),
                        bool_count=Count("output_bool"),
                        str_list_count=Count("output_str_list"),
                    )
                )

                for row in eval_aggs:
                    key = (
                        str(row["trace_id"]),
                        str(row["custom_eval_config_id"]),
                    )
                    if row["float_count"] > 0:
                        eval_map[key] = {
                            "score": row["float_score"],
                            "type": "float",
                        }
                    elif row["bool_count"] > 0:
                        eval_map[key] = {
                            "score": row["bool_score"],
                            "type": "bool",
                        }
                    elif row["str_list_count"] > 0:
                        eval_map[key] = {
                            "type": "str_list",
                            "str_list_data": {},
                        }

                str_list_choices = {
                    str(c.id): c.eval_template.choices
                    for c in eval_configs
                    if c.eval_template and c.eval_template.choices
                }
                str_list_keys = {
                    k for k, v in eval_map.items() if v.get("type") == "str_list"
                }

                if str_list_keys and str_list_choices:
                    str_list_logs = (
                        EvalLogger.objects.filter(
                            trace_id__in=paginated_trace_ids,
                            custom_eval_config_id__in=[
                                c.id
                                for c in eval_configs
                                if str(c.id) in str_list_choices
                            ],
                            output_str_list__isnull=False,
                        )
                        .exclude(Q(output_str="ERROR") | Q(error=True))
                        .values(
                            "trace_id",
                            "custom_eval_config_id",
                            "output_str_list",
                        )
                    )

                    grouped = defaultdict(list)
                    for log in str_list_logs:
                        key = (
                            str(log["trace_id"]),
                            str(log["custom_eval_config_id"]),
                        )
                        if log["output_str_list"]:
                            grouped[key].append(log["output_str_list"])

                    for key, str_lists in grouped.items():
                        if key not in str_list_keys:
                            continue
                        choices = str_list_choices.get(key[1], [])
                        total = len(str_lists)
                        breakdown = {}
                        for choice in choices:
                            count = sum(1 for sl in str_lists if choice in sl)
                            breakdown[choice] = {
                                "score": (
                                    round(100.0 * count / total, 2) if total > 0 else 0
                                )
                            }
                        eval_map[key]["str_list_data"] = breakdown

                # Pick the most recent explanation per eval config via
                # Subquery (ordered by -created_at) instead of Min() which
                # is non-deterministic on text fields.
                for row in (
                    EvalLogger.objects.filter(
                        trace_id__in=paginated_trace_ids,
                        custom_eval_config_id__in=eval_config_ids,
                        eval_explanation__isnull=False,
                    )
                    .values("custom_eval_config_id")
                    .annotate(
                        explanation=Subquery(
                            EvalLogger.objects.filter(
                                custom_eval_config_id=OuterRef("custom_eval_config_id"),
                                trace_id__in=paginated_trace_ids,
                                eval_explanation__isnull=False,
                            )
                            .order_by("-created_at")
                            .values("eval_explanation")[:1]
                        )
                    )
                ):
                    explanation_map[str(row["custom_eval_config_id"])] = row[
                        "explanation"
                    ]

            response = []
            for trace in traces:
                trace_id_str = str(trace.id)
                agg = span_agg_map.get(trace_id_str, {})

                result = {
                    "trace_id": trace_id_str,
                    "input": trace.input,
                    "output": trace.output,
                    "system_metrics": {
                        "total_latency_ms": agg.get("root_latency_ms", 0),
                        "total_cost": agg.get("total_cost", 0),
                        "start_time": format_datetime_to_iso(trace.created_at),
                        "total_tokens": agg.get("total_tokens", 0),
                        "input_tokens": agg.get("input_tokens", 0),
                        "output_tokens": agg.get("output_tokens", 0),
                    },
                }

                eval_metrics = {}
                for config in eval_configs:
                    config_id_str = str(config.id)
                    key = (trace_id_str, config_id_str)
                    data = eval_map.get(key)
                    explanation = explanation_map.get(config_id_str)

                    if data:
                        if data["type"] in ("float", "bool"):
                            eval_metrics[config_id_str] = {
                                "score": data["score"],
                                "name": config.name,
                                "explanation": explanation,
                            }
                        elif data["type"] == "str_list":
                            str_data = data.get("str_list_data", {})
                            for choice_key, value in str_data.items():
                                eval_metrics[config_id_str + "**" + choice_key] = {
                                    "score": value["score"],
                                    "name": config.name + " - " + choice_key,
                                    "explanation": explanation,
                                }

                result["evals_metrics"] = eval_metrics
                response.append(result)

            next_session_id, previous_session_id = get_session_navigation(
                request, project_id, trace_session_id
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
        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(f"Error retrieving trace session: {str(e)}")

    def _retrieve_clickhouse(
        self, request, trace_session_id, trace_session_obj, project_id, analytics
    ):
        """Retrieve session detail from ClickHouse."""
        serializer = self.get_serializer(trace_session_obj)
        trace_session = serializer.data

        page_number = int(self.request.query_params.get("page_number", 0))
        page_size = int(self.request.query_params.get("page_size", 30))
        page_start = page_number * page_size

        # Get session-level aggregates from CH
        agg_query = """
            SELECT
                min(start_time) AS start_time,
                max(end_time) AS end_time,
                round(sum(cost), 6) AS total_cost,
                sum(total_tokens) AS total_tokens,
                count(DISTINCT trace_id) AS total_traces
            FROM spans
            WHERE project_id = %(project_id)s
              AND trace_session_id = %(session_id)s
              AND _peerdb_is_deleted = 0
        """
        agg_result = analytics.execute_ch_query(
            agg_query,
            {"project_id": str(project_id), "session_id": str(trace_session_id)},
            timeout_ms=5000,
        )

        agg = agg_result.data[0] if agg_result.data else {}
        session_start = agg.get("start_time")
        session_end = agg.get("end_time")
        duration = 0
        if session_start and session_end:
            try:
                duration = (session_end - session_start).total_seconds()
            except (TypeError, AttributeError):
                duration = 0

        session_metadata = {
            "session_id": str(trace_session_id),
            "duration": duration,
            "total_cost": agg.get("total_cost", 0) or 0,
            "total_traces": agg.get("total_traces", 0),
            "start_time": format_datetime_to_iso(session_start),
            "end_time": format_datetime_to_iso(session_end),
            "total_tokens": agg.get("total_tokens", 0),
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
              AND trace_session_id = %(session_id)s
              AND _peerdb_is_deleted = 0
            GROUP BY trace_id
            ORDER BY trace_min_start_time ASC
            LIMIT %(limit)s
            OFFSET %(offset)s
        """
        trace_result = analytics.execute_ch_query(
            trace_query,
            {
                "project_id": str(project_id),
                "session_id": str(trace_session_id),
                "limit": page_size + 1,
                "offset": page_start,
            },
            timeout_ms=10000,
        )

        has_next = len(trace_result.data) > page_size
        traces_data = trace_result.data[:page_size]

        if not traces_data:
            next_session_id, previous_session_id = get_session_navigation(
                request, project_id, trace_session_id
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

        # Get eval metrics from CH
        trace_ids = [r["trace_id"] for r in traces_data]
        eval_configs = list(
            CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(trace_id__in=trace_ids).values(
                    "custom_eval_config_id"
                ),
                deleted=False,
            ).select_related("eval_template")
        )

        eval_map = {}
        if eval_configs and trace_ids:
            config_ids = [str(c.id) for c in eval_configs]
            eval_query = """
                SELECT
                    toString(trace_id) AS trace_id,
                    toString(custom_eval_config_id) AS config_id,
                    round(avg(output_float) * 100, 2) AS float_score,
                    round(avg(CASE WHEN output_bool = 1 THEN 100.0
                                   WHEN output_bool = 0 THEN 0.0
                                   ELSE NULL END), 2) AS bool_score,
                    count(output_float) AS float_count,
                    count(output_bool) AS bool_count
                FROM tracer_eval_logger FINAL
                WHERE trace_id IN %(trace_ids)s
                  AND custom_eval_config_id IN %(config_ids)s
                  AND _peerdb_is_deleted = 0
                  AND output_str != 'ERROR'
                  AND (error = 0 OR error IS NULL)
                GROUP BY trace_id, custom_eval_config_id
            """
            eval_result = analytics.execute_ch_query(
                eval_query,
                {"trace_ids": trace_ids, "config_ids": config_ids},
                timeout_ms=5000,
            )
            for row in eval_result.data:
                key = (row["trace_id"], row["config_id"])
                if row.get("float_count", 0) > 0:
                    eval_map[key] = {"score": row["float_score"], "type": "float"}
                elif row.get("bool_count", 0) > 0:
                    eval_map[key] = {"score": row["bool_score"], "type": "bool"}

        response = []
        for trace_row in traces_data:
            trace_id_str = trace_row["trace_id"]
            result = {
                "trace_id": trace_id_str,
                "input": trace_row.get("input"),
                "output": trace_row.get("output"),
                "system_metrics": {
                    "total_latency_ms": trace_row.get("root_latency_ms", 0),
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

            result["evals_metrics"] = eval_metrics
            response.append(result)

        next_session_id, previous_session_id = get_session_navigation(
            request, project_id, trace_session_id
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

    @action(detail=False, methods=["get"])
    def get_session_filter_values(self, request, *args, **kwargs):
        """
        Return distinct values for a session-level column.
        Used by the filter panel's value picker for session-specific fields
        (session_id, user_id, first_message, etc.).

        Query params:
            project_id: required
            column: the session column name (camelCase, e.g. "sessionId")
            search: optional search substring
            page: page number (0-based), default 0
            page_size: default 50
        """
        try:
            project_id = request.query_params.get("project_id")
            column = request.query_params.get("column", "")
            search = request.query_params.get("search", "")
            page = int(request.query_params.get("page", 0))
            page_size = int(request.query_params.get("page_size", 50))

            if not project_id or not column:
                return self._gm.bad_request("project_id and column are required")

            # Map frontend column names to ClickHouse expressions
            COLUMN_MAP = {
                "session_id": "trace_session_id",
                "user_id": "end_user_id",
                "first_message": "first_message",
                "last_message": "last_message",
                # Legacy camelCase support
                "sessionId": "trace_session_id",
                "userId": "end_user_id",
                "firstMessage": "first_message",
                "lastMessage": "last_message",
            }

            ch_column = COLUMN_MAP.get(column)
            if not ch_column:
                return self._gm.success_response({"values": []})

            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
                QueryType,
            )

            analytics = AnalyticsQueryService()
            if not analytics.should_use_clickhouse(QueryType.SESSION_LIST):
                return self._gm.success_response({"values": []})

            # For firstMessage/lastMessage we need argMin/argMax from root spans
            if ch_column in ("first_message", "last_message"):
                agg_expr = (
                    "argMin(input, start_time)"
                    if ch_column == "first_message"
                    else "argMax(input, start_time)"
                )
                search_clause = f"AND val ILIKE %(search)s" if search else ""
                query = f"""
                SELECT DISTINCT val FROM (
                    SELECT {agg_expr} AS val
                    FROM spans
                    WHERE project_id = %(project_id)s
                      AND _peerdb_is_deleted = 0
                      AND trace_session_id IS NOT NULL
                      AND (parent_span_id IS NULL OR parent_span_id = '')
                    GROUP BY trace_session_id
                )
                WHERE val != '' AND val IS NOT NULL
                {search_clause}
                ORDER BY val
                LIMIT %(limit)s OFFSET %(offset)s
                """
            else:
                # Simple distinct on the column (session_id, user_id)
                is_uuid = ch_column in ("trace_session_id", "end_user_id")
                select_expr = f"toString({ch_column})" if is_uuid else ch_column
                search_clause = (
                    f"AND toString({ch_column}) ILIKE %(search)s" if search else ""
                )
                query = f"""
                SELECT DISTINCT {select_expr} AS val
                FROM spans
                WHERE project_id = %(project_id)s
                  AND _peerdb_is_deleted = 0
                  AND {ch_column} IS NOT NULL
                  AND (parent_span_id IS NULL OR parent_span_id = '')
                  {search_clause}
                ORDER BY val
                LIMIT %(limit)s OFFSET %(offset)s
                """

            params = {
                "project_id": project_id,
                "limit": page_size,
                "offset": page * page_size,
            }
            if search:
                params["search"] = f"%{search}%"

            try:
                result = analytics.execute_ch_query(query, params, timeout_ms=5000)
                values = [
                    str(row.get("val", "") if isinstance(row, dict) else row[0])
                    for row in result.data
                    if (row.get("val") if isinstance(row, dict) else row[0])
                ]
                return self._gm.success_response({"values": values})
            except Exception as e:
                session_logger.warning("CH session filter values failed", error=str(e))
                return self._gm.success_response({"values": []})

        except Exception as e:
            session_logger.exception(f"Error in get_session_filter_values: {e}")
            return self._gm.bad_request(str(e))

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
        try:
            project_id = request.data.get("project_id")
            project = Project.objects.get(
                id=project_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )

            if not project_id or not project:
                return self._gm.bad_request("project_id is required")

            filters = request.data.get("filters", [])
            interval = request.data.get("interval", "day")
            req_data_config = request.data.get("req_data_config", {})
            metric_type = req_data_config.get("type", "SYSTEM_METRIC")
            metric_id = req_data_config.get("id", "session_count")

            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
                QueryType,
            )

            analytics = AnalyticsQueryService()

            # --- SYSTEM_METRIC: session-level aggregation via ClickHouse ---
            if metric_type == "SYSTEM_METRIC":
                if analytics.should_use_clickhouse(QueryType.TIME_SERIES):
                    try:
                        from tracer.services.clickhouse.query_builders.session_time_series import (
                            SessionTimeSeriesQueryBuilder,
                        )

                        builder = SessionTimeSeriesQueryBuilder(
                            project_id=str(project_id),
                            filters=filters,
                            interval=interval,
                        )
                        query, params = builder.build()
                        result = analytics.execute_ch_query(
                            query, params, timeout_ms=10000
                        )
                        ch_data = builder.format_result(
                            result.data, result.columns or []
                        )

                        metric_key = (
                            metric_id if metric_id in ch_data else "session_count"
                        )
                        metric_points = ch_data.get(metric_key, [])
                        traffic_points = ch_data.get("traffic", [])
                        traffic_by_ts = {
                            t.get("timestamp"): t.get("traffic", 0)
                            for t in traffic_points
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
                        session_logger.warning(
                            "CH session time-series failed",
                            error=str(e),
                        )

            # --- EVAL / ANNOTATION: delegate to shared helpers ---
            # Filter traces to only those belonging to sessions
            elif metric_type in ("EVAL", "ANNOTATION"):
                from tracer.utils.graphs_optimized import (
                    get_annotation_graph_data,
                    get_eval_graph_data,
                )

                session_trace_qs = Trace.objects.filter(
                    project_id=project_id,
                    session__isnull=False,
                )

                if metric_type == "EVAL":
                    graph_data = get_eval_graph_data(
                        interval=interval,
                        filters=filters,
                        property=request.data.get("property", "average"),
                        observe_type="trace",
                        req_data_config=req_data_config,
                        eval_logger_filters={"trace_ids_queryset": session_trace_qs},
                    )
                else:
                    graph_data = get_annotation_graph_data(
                        interval=interval,
                        filters=filters,
                        property=request.data.get("property", "average"),
                        observe_type="trace",
                        req_data_config=req_data_config,
                        annotation_logger_filters={
                            "trace_ids_queryset": session_trace_qs
                        },
                    )

                return self._gm.success_response(
                    graph_data or {"metric_name": metric_id, "data": []}
                )

            # Fallback: empty
            return self._gm.success_response({"metric_name": metric_id, "data": []})
        except Project.DoesNotExist:
            return self._gm.bad_request("Project not found")
        except Exception as e:
            session_logger.exception(f"Error in get_session_graph_data: {str(e)}")
            return self._gm.bad_request(f"Error fetching session graph data: {str(e)}")

    @action(detail=False, methods=["get"])
    def list_sessions(self, request, *args, **kwargs):
        """
        List traces filtered by project ID and project version ID with optimized queries.
        """
        try:
            query_data = {
                "filters": request.query_params.get("filters", "[]"),
                "sort_params": request.query_params.get("sort_params", "[]")
                or request.query_params.get("sortParams", "[]"),
            }
            if query_data["filters"]:
                query_data["filters"] = json.loads(query_data["filters"])
            if query_data["sort_params"]:
                query_data["sort_params"] = json.loads(query_data["sort_params"])
            serializer = TraceSessionExportSerializer(data=query_data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
            export = kwargs.get("export", False) if kwargs else False
            project_id = self.request.query_params.get(
                "project_id"
            ) or self.request.query_params.get("projectId")

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
                    Project.objects.filter(
                        organization=org,
                        deleted=False,
                        trace_type__in=("observe", "experiment"),
                    ).values_list("id", flat=True)
                )
                project = None
            else:
                project = Project.objects.get(id=project_id, organization=org)
                org_project_ids = None

            # ClickHouse dispatch
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
                QueryType,
            )

            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.SESSION_LIST):
                try:
                    return self._list_sessions_clickhouse(
                        request,
                        project_id,
                        project,
                        analytics,
                        validated_data,
                        org_project_ids=org_project_ids,
                    )
                except Exception as e:
                    logger.warning(
                        "ClickHouse session-list failed, falling back to PG",
                        error=str(e),
                    )

            filters = validated_data.get("filters", [])
            sort_params = validated_data.get("sort_params", [])

            trace_sessions_qs = (
                TraceSession.objects.filter(project_id__in=org_project_ids)
                if org_scope
                else TraceSession.objects.filter(project_id=project_id)
            )
            trace_sessions_qs, remaining_filters = apply_created_at_filters(
                trace_sessions_qs, filters
            )

            if not trace_sessions_qs.exists():
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
                        "config": (
                            (project.session_config if project else None)
                            or get_default_project_session_config()
                        ),
                    }
                )

            session_ids = trace_sessions_qs.values("id")

            user_id = self.request.query_params.get(
                "user_id"
            ) or self.request.query_params.get("userId")

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
                set(
                    str(l.id)
                    for l in AnnotationsLabels.objects.filter(
                        project_id=project_id, deleted=False
                    )
                )
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

            page_number = int(request.query_params.get("page_number", 0))
            page_size = int(request.query_params.get("page_size", 30))
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
            end_user_map = self._fetch_end_user_info(
                paginated_session_ids,
                end_user_filter,
                getattr(request, "organization", None) or request.user.organization,
            )

            # Map UUID -> user-defined session name (TraceSession.name)
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
            config = (
                project.session_config if project else None
            ) or default_session_config

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

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(f"Error fetching the traces list: {str(e)}")

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
    def _fetch_end_user_info(session_ids, end_user_filter, organization):
        """Fetch end user info for a small set of session IDs using DISTINCT ON."""
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

    def _list_sessions_clickhouse(
        self,
        request,
        project_id,
        project,
        analytics,
        validated_data,
        org_project_ids=None,
    ):
        """List sessions using ClickHouse backend.

        When ``org_project_ids`` is provided the builder is constructed
        with `project_ids=...` and the session list spans all projects in
        the org.
        """
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        org_scope = bool(org_project_ids)
        filters = list(validated_data.get("filters", []) or [])
        sort_params = validated_data.get("sort_params", [])
        page_number = int(request.query_params.get("page_number", 0))
        page_size = int(request.query_params.get("page_size", 30))
        org = getattr(request, "organization", None) or request.user.organization
        user_id_qp = request.query_params.get("user_id") or request.query_params.get(
            "userId"
        )

        # Support user_id injected as a structural filter (the cross-project
        # user detail page prepends one). Extract the raw user_id string from
        # either query_params or filters, strip it from the filter list, then
        # resolve it to a set of EndUser UUIDs and pass an end_user_id IN(...)
        # synthetic filter instead (the CH `spans` table keys users via the
        # UUID column `end_user_id`, not the string `user_id`).
        user_id_raw: Optional[str] = user_id_qp or None
        _remaining: List[Dict] = []
        for _f in filters:
            _col = _f.get("column_id")
            _cfg = _f.get("filter_config") or {}
            _col_type = _cfg.get("col_type") or "NORMAL"
            if _col == "user_id" and _col_type == "NORMAL":
                _val = _cfg.get("filter_value")
                if isinstance(_val, list):
                    _val = _val[0] if _val else None
                if _val and not user_id_raw:
                    user_id_raw = _val
                continue
            _remaining.append(_f)
        filters = _remaining

        # Resolve the raw user_id to a list of end_user UUIDs and inject as
        # a synthetic end_user_id IN(...) filter. Scope by org (and project
        # if we're in project mode).
        if user_id_raw:
            _eu_qs = EndUser.objects.filter(
                user_id=user_id_raw,
                organization=org,
                deleted=False,
            )
            if not org_scope and project_id:
                _eu_qs = _eu_qs.filter(project_id=project_id)
            _ids = [str(u) for u in _eu_qs.values_list("id", flat=True)]
            if not _ids:
                _ids = ["00000000-0000-0000-0000-000000000000"]
            filters.append(
                {
                    "column_id": "end_user_id",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": _ids,
                    },
                }
            )

        builder = SessionListQueryBuilder(
            project_id=None if org_scope else str(project_id),
            project_ids=[str(p) for p in org_project_ids] if org_scope else None,
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            sort_params=sort_params,
            user_id=None,  # user_id handled via end_user_id IN(...) synthetic filter
        )

        # Phase 1: Light aggregation (no input column)
        query, params = builder.build()
        result = analytics.execute_ch_query(query, params, timeout_ms=10000)

        # Trim the +1 sentinel row used for has_more detection
        has_more = len(result.data) > page_size
        actual_data = result.data[:page_size]

        # Phase 1b: Fetch first/last messages for the page
        session_ids_page = [str(row.get("session_id", "")) for row in actual_data]
        content_map = {}
        if session_ids_page:
            cq, cp = builder.build_content_query(session_ids_page)
            if cq:
                cr = analytics.execute_ch_query(cq, cp, timeout_ms=10000)
                content_map = {str(r.get("session_id", "")): r for r in cr.data}
        for row in actual_data:
            sid = str(row.get("session_id", ""))
            c = content_map.get(sid, {})
            row["first_message"] = c.get("first_message", "")
            row["last_message"] = c.get("last_message", "")

        # Get total count — skip the expensive count query when we can infer
        # the total from the Phase 1 result size.
        if not has_more and page_number == 0:
            total_count = len(actual_data)
        elif not has_more:
            total_count = (page_number * page_size) + len(actual_data)
        else:
            count_query, count_params = builder.build_count_query()
            count_result = analytics.execute_ch_query(
                count_query, count_params, timeout_ms=5000
            )
            total_count = (
                count_result.data[0].get("total", 0) if count_result.data else 0
            )

        formatted = builder.format_sessions(
            [(list(row.values())) for row in actual_data],
            list(actual_data[0].keys()) if actual_data else [],
        )

        # Inject user-defined session_name (from TraceSession.name) — spans'
        # trace_session_id is the UUID, but users identify sessions by the
        # string they passed in the OTel ``session.id`` attribute, which
        # ingestion stores on TraceSession.name.
        if session_ids_page:
            _name_map = dict(
                TraceSession.objects.filter(
                    id__in=session_ids_page,
                    project_id__in=(org_project_ids or [project_id]),
                ).values_list("id", "name")
            )
            for entry in formatted:
                sid = entry.get("session_id", "")
                try:
                    from uuid import UUID as _UUID

                    entry["session_name"] = _name_map.get(_UUID(sid))
                except (ValueError, TypeError):
                    entry["session_name"] = None

        # Phase 2: Aggregated span attributes for custom columns
        _SKIP_ATTR_PREFIXES = (
            "raw.",
            "llm.input_messages",
            "llm.output_messages",
            "input.value",
            "output.value",
        )
        _MAX_ATTR_KEYS_PER_SESSION = 50
        if session_ids_page:
            try:
                attr_query, attr_params = builder.build_span_attributes_query(
                    session_ids_page
                )
                if attr_query:
                    attr_result = analytics.execute_ch_query(
                        attr_query, attr_params, timeout_ms=5000
                    )
                    # Aggregate per session: session_id -> {attr_key -> set(values)}
                    aggregated_attrs: Dict[str, Dict] = {}
                    for attr_row in attr_result.data:
                        sid = str(attr_row.get("session_id", ""))
                        # Skip if this session already has max keys
                        if (
                            sid in aggregated_attrs
                            and len(aggregated_attrs[sid]) >= _MAX_ATTR_KEYS_PER_SESSION
                        ):
                            continue
                        # Primary: parse raw JSON blob (using orjson if available)
                        raw = attr_row.get("span_attributes_raw", "{}")
                        try:
                            attrs = (
                                _json_loads(raw)
                                if isinstance(raw, str) and raw
                                else (raw or {})
                            )
                        except (json.JSONDecodeError, ValueError, TypeError):
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
                                    set()
                                    if isinstance(value, (str, int, float, bool))
                                    else []
                                )
                            if isinstance(value, (str, int, float, bool)):
                                aggregated_attrs[sid][key].add(
                                    value
                                    if not isinstance(value, bool)
                                    else str(value).lower()
                                )
                    # Merge into formatted rows
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
            except Exception as e:
                logger.warning(f"Session span attribute aggregation failed: {e}")

        return self._gm.success_response(
            {
                "metadata": {"total_rows": total_count},
                "table": formatted,
                "config": (
                    (project.session_config if project else None)
                    or get_default_project_session_config()
                ),
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
        # Batch-fetch distinct annotators for all labels from Score
        label_ids = [label.id for label in annotation_labels]
        score_filter = {
            "label_id__in": label_ids,
            "trace_session__isnull": False,
            "deleted": False,
        }
        if project_id:
            score_filter["trace_session__project_id"] = project_id
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

            configs.append(
                asdict(
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
            )
        return configs

    @action(detail=False, methods=["get"])
    def get_trace_session_export_data(self, request, *args, **kwargs):
        """
        Export traces filtered by project ID and project version ID with optimized queries.
        """
        try:
            response = self.list_sessions(request, export=True)

            if response.status_code != 200:
                return response

            project_id = self.request.query_params.get(
                "project_id"
            ) or self.request.query_params.get("projectId")
            project = Project.objects.get(
                id=project_id,
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )

            result = response.data.get("result").get("table")
            df = pd.DataFrame(result) if result else pd.DataFrame(columns=result)

            # Convert to CSV buffer
            buffer = io.BytesIO()
            df.to_csv(buffer, index=False, encoding="utf-8")
            buffer.seek(0)

            # Create the response with the file
            filename = f"{project.name or 'project'}_sessions.csv"
            response = FileResponse(
                buffer, as_attachment=True, filename=filename, content_type="text/csv"
            )

            return response

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(f"Error fetching the traces list: {str(e)}")

    @action(detail=True, methods=["get"])
    def eval_logs(self, request, *args, **kwargs):
        """Session-scoped eval log feed for TracesDrawer's "Evals" tab.

        Session-level eval results are walled off from span/trace surfaces
        by ``target_type='session'`` — this endpoint is the only place
        they appear.

        Query params:
            page (int, 1-indexed, default 1)
            page_size (int, default 25, max 100)

        Returns:
            Paginated DRF response: {count, next, previous, results,
            total_pages, current_page}. Each ``results`` item carries the
            same fields ``EvalTaskView.get_usage`` exposes, minus
            span/trace-only fields (NULL on session rows per the
            ``eval_logger_target_type_fks`` check constraint).
        """
        try:
            # get_object() applies the org-scoped queryset filter and
            # raises 404 if the caller can't access the session.
            session = self.get_object()

            qp = PaginationQuerySerializer(data=request.query_params)
            qp.is_valid(raise_exception=True)
            page_size = qp.validated_data["page_size"]

            logs_qs = (
                EvalLogger.objects.filter(
                    trace_session_id=session.id,
                    target_type=EvalTargetType.SESSION,
                )
                .select_related(
                    "custom_eval_config",
                    "custom_eval_config__eval_template",
                )
                .order_by("-created_at")
            )

            paginator = ExtendedPageNumberPagination()
            paginator.page_size = page_size
            logs_page = paginator.paginate_queryset(logs_qs, request, view=self)

            items = []
            for log in logs_page:
                # Same Pass/Fail derivation as EvalTaskView.get_usage so
                # the two surfaces render identically.
                if log.error:
                    result_label = "Error"
                    score = None
                    status = "error"
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
                elif log.output_str:
                    result_label = log.output_str[:50]
                    score = None
                    status = "success"
                else:
                    result_label = ""
                    score = None
                    status = "success"

                config = log.custom_eval_config
                reason = log.eval_explanation or log.error_message or ""

                items.append(
                    {
                        "id": str(log.id),
                        "input": (session.name or "")[:200],
                        "result": result_label,
                        "score": score,
                        "reason": reason,
                        "status": status,
                        "source": "eval_task",
                        "created_at": (
                            log.created_at.isoformat() if log.created_at else ""
                        ),
                        "session_id": str(session.id),
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
                            "target_type": log.target_type,
                            "session_id": str(session.id),
                            "session_name": session.name,
                            "output_bool": log.output_bool,
                            "output_float": log.output_float,
                            "output_str": log.output_str,
                            "results_explanation": log.results_explanation,
                            "error_message": log.error_message,
                        },
                    }
                )

            # ExtendedPageNumberPagination response shape:
            # {count, next, previous, results, total_pages, current_page}
            paginated = paginator.get_paginated_response(items)
            return self._gm.success_response(paginated.data)
        except Exception as e:
            logger.exception(f"Error in fetching session eval logs: {str(e)}")
            return self._gm.bad_request(f"Error fetching session eval logs: {str(e)}")
