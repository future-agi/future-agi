import structlog
from concurrent.futures import ThreadPoolExecutor
from django.http import Http404
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from tfc.routers import uses_db
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiErrorResponseSerializer,
    EmptyRequestSerializer,
)
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tracer.db_routing import DATABASE_FOR_DASHBOARD_LIST
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.models.project import Project, ProjectSourceChoices
from tracer.serializers.dashboard import (
    DashboardCreateUpdateSerializer,
    DashboardDetailSerializer,
    DashboardFilterValuesQuerySerializer,
    DashboardMetricsCatalogResponseSerializer,
    DashboardPreviewQuerySerializer,
    DashboardQueryApiResponseSerializer,
    DashboardQuerySerializer,
    DashboardSerializer,
    DashboardWidgetSerializer,
)
from tracer.services.clickhouse.client import (
    get_clickhouse_client,
    is_clickhouse_enabled,
)
from tracer.services.clickhouse.query_builders.dashboard import (
    METRIC_UNITS,
    DashboardQueryBuilder,
    InvalidMetricCombinationError,
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
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)
from tracer.utils.sql_queries import SQL_query_handler

logger = structlog.get_logger(__name__)

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


def _customer_attribute_metric_aliases():
    from tracer.utils.filters import FilterEngine

    aliases = {}
    for metric_id, definition in FilterEngine.VOICE_METRIC_DEFINITIONS.items():
        json_keys = definition.get("json_keys") or []
        if len(json_keys) == 1:
            aliases[json_keys[0]] = metric_id
    return aliases


def _suppress_customer_attribute_metric_aliases(metric_entries):
    aliases = _customer_attribute_metric_aliases()
    exposed_metric_names = {
        metric.get("name")
        for metric in metric_entries
        if metric.get("category") != "custom_attribute"
    }
    return [
        metric
        for metric in metric_entries
        if not (
            metric.get("category") == "custom_attribute"
            and aliases.get(metric.get("name")) in exposed_metric_names
        )
    ]


def _normalize_eval_output_type(template_config):
    """Normalize EvalTemplate config.output to the filter output type enum."""
    if not isinstance(template_config, dict):
        return "SCORE"
    output_type = (
        (template_config.get("output") or "")
        .upper()
        .replace("/", "_")
        .replace(" ", "_")
    )
    return (
        output_type
        if output_type in ("PASS_FAIL", "CHOICE", "CHOICES", "SCORE")
        else "SCORE"
    )


def build_eval_metric_entries(eval_templates, project_ids, workspace, per_eval_config):
    """Build eval metric entries per template or per configured eval."""
    entries = []

    if per_eval_config:
        eval_cfg_qs = CustomEvalConfig.objects.filter(deleted=False).select_related(
            "eval_template"
        )
        if project_ids:
            eval_cfg_qs = eval_cfg_qs.filter(project_id__in=project_ids)
        else:
            eval_cfg_qs = eval_cfg_qs.filter(project__workspace=workspace)

        for cfg in eval_cfg_qs:
            tmpl = cfg.eval_template
            if not tmpl or getattr(tmpl, "deleted", False):
                continue
            output_type = _normalize_eval_output_type(tmpl.config or {})
            entry = {
                "name": str(cfg.id),
                "display_name": cfg.name or tmpl.name,
                "category": "eval_metric",
                "source": "all",
                "sources": ["all"],
                "output_type": output_type,
                "eval_template_id": str(tmpl.id),
            }
            if output_type in ("CHOICE", "CHOICES") and tmpl.choices:
                entry["choices"] = tmpl.choices
            elif output_type == "PASS_FAIL":
                entry["choices"] = ["Passed", "Failed"]
            entries.append(entry)
        return entries

    for et in eval_templates:
        output_type = _normalize_eval_output_type(et["config"] or {})
        entry = {
            "name": str(et["id"]),
            "display_name": et["name"],
            "category": "eval_metric",
            "source": "all",
            "sources": ["all"],
            "output_type": output_type,
        }
        choices = et.get("choices") or []
        if output_type in ("CHOICE", "CHOICES") and choices:
            entry["choices"] = choices
        elif output_type == "PASS_FAIL":
            entry["choices"] = ["Passed", "Failed"]
        entries.append(entry)
    return entries


def build_simulation_eval_metric_entries(agent_definition_id, workspace):
    """Build simulation eval filter entries scoped to an agent definition."""
    if not agent_definition_id:
        return []

    from simulate.models import SimulateEvalConfig

    entries = []
    eval_configs = (
        SimulateEvalConfig.objects.filter(
            run_test__agent_definition_id=agent_definition_id,
            run_test__organization=workspace.organization,
            run_test__workspace=workspace,
            run_test__deleted=False,
            deleted=False,
        )
        .select_related("eval_template")
        .order_by("name", "eval_template__name", "id")
        .distinct()
    )

    for cfg in eval_configs:
        tmpl = cfg.eval_template
        if not tmpl or getattr(tmpl, "deleted", False):
            continue
        output_type = _normalize_eval_output_type(tmpl.config or {})
        entry = {
            "name": str(cfg.id),
            "display_name": cfg.name or tmpl.name,
            "category": "eval_metric",
            "source": "simulation",
            "sources": ["simulation"],
            "output_type": output_type,
            "eval_template_id": str(tmpl.id),
        }
        if output_type in ("CHOICE", "CHOICES") and tmpl.choices:
            entry["choices"] = tmpl.choices
        elif output_type == "PASS_FAIL":
            entry["choices"] = ["Passed", "Failed"]
        entries.append(entry)
    return entries


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
        """Use a longer timeout for high-cardinality or wide trace queries."""
        has_eval_metrics = any(
            m.get("type") == "eval_metric" for m in trace_config.get("metrics", [])
        )
        has_project_breakdown = any(
            bd.get("name") == "project"
            for bd in trace_config.get("breakdowns", [])
            if bd.get("source", "traces") in ("traces", "both", "all", "")
        )
        return 30000 if has_eval_metrics or has_project_breakdown else 10000

    @staticmethod
    def _run_metric_queries(builder, source, fetch_rows):
        """Build + execute each metric in parallel; return [(metric_info, rows)].

        Only ``InvalidMetricCombinationError`` is caught per-metric (the metric
        is non-sensical and a user-facing message is attached). All other
        exceptions (connection, timeout, programming bugs) propagate so they
        surface as real errors instead of being silently masked as per-widget
        "could not be computed" text.
        """
        metrics = builder.metrics
        if not metrics:
            return []

        def _exec_one(metric):
            metric_info = builder.metric_info(metric)
            metric_info["source"] = source
            try:
                sql, params = builder.build_metric_query(metric)
                return (metric_info, fetch_rows(sql, params))
            except InvalidMetricCombinationError as e:
                metric_info["error"] = str(e)
                return (metric_info, [])
            except Exception as e:
                logger.warning("metric_query_failed", metric=metric_info.get("name"), error=str(e)[:200])
                return (metric_info, [])

        if len(metrics) == 1:
            return [_exec_one(metrics[0])]

        with ThreadPoolExecutor(max_workers=min(len(metrics), 4)) as pool:
            futures = [pool.submit(_exec_one, m) for m in metrics]
        return [f.result() for f in futures]

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
        request_serializer=DashboardQuerySerializer,
        responses={
            200: DashboardQueryApiResponseSerializer,
            400: ApiErrorResponseSerializer,
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
        simulation_metrics = [
            m for m in query_config["metrics"] if m.get("source") == "simulation"
        ]

        try:
            analytics = AnalyticsQueryService()
            all_metric_results = []
            project_name_map = {}

            # --- Trace metrics via DashboardQueryBuilder ---
            if trace_metrics:
                trace_config = {**query_config, "metrics": trace_metrics}

                # Resolve project_ids
                project_ids = trace_config.get("project_ids", [])
                if not project_ids:
                    project_ids = list(
                        Project.objects.filter(
                            workspace=request.workspace,
                        ).values_list("id", flat=True)
                    )
                    trace_config["project_ids"] = [str(pid) for pid in project_ids]
                else:
                    # Validate project_ids belong to this workspace
                    valid_count = Project.objects.filter(
                        id__in=project_ids,
                        workspace=request.workspace,
                    ).count()
                    if valid_count != len(project_ids):
                        return self._gm.bad_request(
                            "One or more project_ids do not belong to this workspace"
                        )

                # Build project name map from current workspace projects
                project_name_map = dict(
                    Project.objects.filter(
                        id__in=trace_config["project_ids"],
                        workspace=request.workspace,
                    ).values_list("id", "name")
                )
                project_name_map = {str(k): v for k, v in project_name_map.items()}

                # For eval metrics: extend with ALL org projects so
                # cross-workspace project breakdowns resolve correctly.
                has_eval_metrics = any(
                    m.get("type") == "eval_metric" for m in trace_config["metrics"]
                )
                if has_eval_metrics:
                    org_projects = dict(
                        Project.objects.filter(
                            workspace__organization=request.workspace.organization,
                        ).values_list("id", "name")
                    )
                    for k, v in org_projects.items():
                        project_name_map.setdefault(str(k), v)

                # Pass workspace + org IDs for eval metrics
                trace_config["organization_id"] = str(request.workspace.organization_id)
                trace_config["workspace_id"] = str(request.workspace.id)

                # v1↔v2 dispatch — flips with CH25_QUERY_TYPES_V2_PRIMARY=DASHBOARD
                from tracer.services.clickhouse.v2.dispatch import (
                    get_query_builder_class,
                )

                _DashCls = get_query_builder_class("DASHBOARD")
                builder = _DashCls(trace_config)
                query_timeout = self._get_trace_query_timeout_ms(trace_config)
                all_metric_results.extend(
                    self._run_metric_queries(
                        builder,
                        "traces",
                        lambda sql, params: analytics.execute_ch_query(
                            sql, params, timeout_ms=query_timeout
                        ).data,
                    )
                )

            # --- Dataset metrics via DatasetQueryBuilder ---
            if dataset_metrics:
                ds_config = {**query_config, "metrics": dataset_metrics}
                ds_config["workspace_id"] = str(request.workspace.id)

                # Validate dataset_ids if provided
                dataset_ids = ds_config.get("dataset_ids", [])
                if dataset_ids:
                    from model_hub.models.develop_dataset import Dataset

                    valid_count = Dataset.objects.filter(
                        id__in=dataset_ids,
                        workspace=request.workspace,
                        deleted=False,
                    ).count()
                    if valid_count != len(dataset_ids):
                        return self._gm.bad_request(
                            "Some dataset_ids are invalid or not in this workspace"
                        )

                builder = DatasetQueryBuilder(ds_config)
                all_metric_results.extend(
                    self._run_metric_queries(
                        builder,
                        "datasets",
                        lambda sql, params: analytics.execute_ch_query(
                            sql, params, timeout_ms=10000
                        ).data,
                    )
                )

            # --- Simulation metrics via SimulationQueryBuilder ---
            if simulation_metrics:
                sim_config = {**query_config, "metrics": simulation_metrics}
                sim_config["workspace_id"] = str(request.workspace.id)
                all_metric_results.extend(
                    self._run_simulation_analytics_queries(analytics, sim_config)
                )

            # --- Resolve project UUIDs to names in breakdown values ---
            has_project_breakdown = any(
                bd.get("name") == "project" for bd in query_config.get("breakdowns", [])
            )
            if has_project_breakdown and project_name_map:
                for _metric_info, rows in all_metric_results:
                    for row in rows:
                        bv = row.get("breakdown_value")
                        if bv:
                            bv_str = str(bv)
                            if " / " in bv_str:
                                # Multi-breakdown: resolve each segment independently
                                parts = bv_str.split(" / ")
                                parts = [project_name_map.get(p, p) for p in parts]
                                row["breakdown_value"] = " / ".join(parts)
                            elif bv_str in project_name_map:
                                row["breakdown_value"] = project_name_map[bv_str]

            # --- Resolve dataset UUIDs to names in breakdown values ---
            has_dataset_breakdown = any(
                bd.get("name") == "dataset" for bd in query_config.get("breakdowns", [])
            )
            if has_dataset_breakdown:
                # Collect all dataset UUIDs from breakdown values
                import uuid as _uuid

                ds_uuids = set()
                for _metric_info, rows in all_metric_results:
                    for row in rows:
                        bv = row.get("breakdown_value", "")
                        try:
                            _uuid.UUID(bv)
                            ds_uuids.add(bv)
                        except (ValueError, AttributeError):
                            pass

                if ds_uuids:
                    from model_hub.models.develop_dataset import Dataset

                    ds_name_map = dict(
                        Dataset.objects.filter(
                            id__in=list(ds_uuids),
                        ).values_list("id", "name")
                    )
                    ds_name_map = {str(k): v for k, v in ds_name_map.items()}
                    if ds_name_map:
                        for _metric_info, rows in all_metric_results:
                            for row in rows:
                                bv = row.get("breakdown_value", "")
                                if bv in ds_name_map:
                                    row["breakdown_value"] = ds_name_map[bv]

            # --- Format merged results ---
            # Use DatasetQueryBuilder for time-bucket generation (same logic in both)
            merged_config = {**query_config, "metrics": query_config["metrics"]}
            if dataset_metrics:
                merged_config["workspace_id"] = str(request.workspace.id)
            response = self._format_merged_metric_results(
                merged_config,
                all_metric_results,
            )
            return self._gm.success_response(response)

        except Exception as e:
            logger.error(
                "query_execution_failed", error=str(e), query_config=query_config
            )
            return self._gm.bad_request(
                "Query execution failed. Please check your query configuration."
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
            metrics = []

            # 1. Trace system metrics
            metrics.extend(
                [
                    {
                        "name": "project",
                        "display_name": "Project",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "latency",
                        "display_name": "Latency",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "ms",
                    },
                    {
                        "name": "error_rate",
                        "display_name": "Error Rate",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "%",
                    },
                    {
                        "name": "tokens",
                        "display_name": "Tokens",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "tokens",
                    },
                    {
                        "name": "input_tokens",
                        "display_name": "Input Tokens",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "tokens",
                    },
                    {
                        "name": "output_tokens",
                        "display_name": "Output Tokens",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "tokens",
                    },
                    {
                        "name": "time_to_first_token",
                        "display_name": "Time to First Token",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "ms",
                    },
                    {
                        "name": "cost",
                        "display_name": "Cost",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "$",
                    },
                    # Trace numeric: session & user counts
                    {
                        "name": "session_count",
                        "display_name": "Session Count",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "",
                    },
                    {
                        "name": "user_count",
                        "display_name": "User Count",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "",
                    },
                    {
                        "name": "trace_count",
                        "display_name": "Trace Count",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "",
                    },
                    {
                        "name": "span_count",
                        "display_name": "Span Count",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "",
                    },
                    # Trace string dimensions for breakdown/filter
                    {
                        "name": "model",
                        "display_name": "Model",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "status",
                        "display_name": "Status",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "service_name",
                        "display_name": "Service Name",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "span_kind",
                        "display_name": "Span Kind",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "provider",
                        "display_name": "Provider",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "session",
                        "display_name": "Session",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "user",
                        "display_name": "User",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "user_id_type",
                        "display_name": "User ID Type",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    # Prompt dimensions
                    {
                        "name": "prompt_name",
                        "display_name": "Prompt Name",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "prompt_version",
                        "display_name": "Prompt Version",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "prompt_label",
                        "display_name": "Prompt Label",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "tag",
                        "display_name": "Tag",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "string",
                        "unit": "",
                    },
                ]
            )


            # Eval-specific dimensions (available across all sources)
            metrics.extend(
                [
                    {
                        "name": "dataset",
                        "display_name": "Dataset",
                        "category": "system_metric",
                        "source": "all",
                        "sources": ["all"],
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "eval_source",
                        "display_name": "Eval Source",
                        "category": "system_metric",
                        "source": "all",
                        "sources": ["all"],
                        "type": "string",
                        "unit": "",
                    },
                ]
            )

            # 2. Dataset system metrics
            metrics.extend(
                [
                    {
                        "name": "row_count",
                        "display_name": "Row Count",
                        "category": "system_metric",
                        "source": "datasets",
                        "type": "number",
                        "unit": "",
                    },
                    {
                        "name": "prompt_tokens",
                        "display_name": "Prompt Tokens",
                        "category": "system_metric",
                        "source": "datasets",
                        "type": "number",
                        "unit": "tokens",
                    },
                    {
                        "name": "completion_tokens",
                        "display_name": "Completion Tokens",
                        "category": "system_metric",
                        "source": "datasets",
                        "type": "number",
                        "unit": "tokens",
                    },
                    {
                        "name": "total_tokens",
                        "display_name": "Total Tokens",
                        "category": "system_metric",
                        "source": "datasets",
                        "type": "number",
                        "unit": "tokens",
                    },
                    {
                        "name": "response_time",
                        "display_name": "Response Time",
                        "category": "system_metric",
                        "source": "datasets",
                        "type": "number",
                        "unit": "ms",
                    },
                    {
                        "name": "cell_error_rate",
                        "display_name": "Cell Error Rate",
                        "category": "system_metric",
                        "source": "datasets",
                        "type": "number",
                        "unit": "%",
                    },
                ]
            )

            # 2b. Dataset breakdown/filter dimensions (string)
            metrics.extend(
                [
                    {
                        "name": "dataset",
                        "display_name": "Dataset",
                        "category": "system_metric",
                        "source": "datasets",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "eval_template",
                        "display_name": "Eval Template",
                        "category": "system_metric",
                        "source": "datasets",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "column_name",
                        "display_name": "Column Name",
                        "category": "system_metric",
                        "source": "datasets",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "column_source",
                        "display_name": "Column Source",
                        "category": "system_metric",
                        "source": "datasets",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "cell_status",
                        "display_name": "Cell Status",
                        "category": "system_metric",
                        "source": "datasets",
                        "type": "string",
                        "unit": "",
                    },
                ]
            )

            # Project IDs for trace-scoped metrics (custom attrs, evals, annotations)
            # If caller passes project_ids, scope to those; otherwise all workspace projects.
            req_project_ids_str = request.query_params.get("project_ids", "")
            req_project_ids = [
                pid.strip() for pid in req_project_ids_str.split(",") if pid.strip()
            ]

            workspace_project_ids = {
                str(pid)
                for pid in Project.objects.filter(workspace=workspace).values_list(
                    "id", flat=True
                )
            }
            if req_project_ids:
                project_ids = [
                    pid for pid in req_project_ids if pid in workspace_project_ids
                ]
            else:
                project_ids = list(workspace_project_ids)

            filter_by_project = bool(req_project_ids and project_ids)

            if (
                filter_by_project
                and not Project.objects.filter(
                    id__in=project_ids,
                )
                .exclude(
                    source=ProjectSourceChoices.SIMULATOR.value,
                )
                .exists()
            ):
                metrics.append(
                    {
                        "name": "agent_talk_percentage",
                        "display_name": "Agent Talk %",
                        "category": "system_metric",
                        "source": "traces",
                        "type": "number",
                        "unit": "%",
                    }
                )

            # 3-6: Eval metrics, annotations, and span attributes are independent.
            # Span attribute discovery (CH) runs concurrently with the PG lookups.
            def _discover_span_attributes():
                attrs = []
                try:
                    if is_clickhouse_enabled() and project_ids:
                        analytics = AnalyticsQueryService()
                        rows = analytics.get_span_attribute_keys_ch_for_projects(
                            project_ids,
                            recent_days=None,
                            timeout_ms=15000,
                            outer_limit=2000,
                        )
                        for r in rows:
                            k = r.get("key", "")
                            t = r.get("type", "string")
                            if k:
                                attrs.append({"key": k, "type": t})
                    elif project_ids:
                        for pid in project_ids:
                            keys = SQL_query_handler.get_span_attributes_for_project(pid)
                            for key in keys:
                                k = key if isinstance(key, str) else str(key)
                                if k not in [
                                    a.get("key") if isinstance(a, dict) else a
                                    for a in attrs
                                ]:
                                    attrs.append({"key": k, "type": "string"})
                except Exception as exc:
                    logger.warning(
                        "dashboard_span_attribute_discovery_failed",
                        error=str(exc)[:200],
                    )
                return attrs

            with ThreadPoolExecutor(max_workers=1) as pool:
                f_attrs = pool.submit(_discover_span_attributes)

            # 3. Eval metrics (PG-heavy chain, runs in main thread)
            try:
                from model_hub.models.evals_metric import EvalTemplate

                used_template_ids = []
                if is_clickhouse_enabled():
                    from tracer.services.clickhouse.client import (
                        get_clickhouse_client,
                    )

                    ch = get_clickhouse_client()

                    if filter_by_project:
                        result = ch.execute_read(
                            "SELECT DISTINCT toString(custom_eval_config_id) AS tid "
                            "FROM tracer_eval_logger "
                            "WHERE _peerdb_is_deleted = 0 AND deleted = 0 "
                            "AND custom_eval_config_id != toUUID('00000000-0000-0000-0000-000000000000') "
                            "AND created_at >= now() - INTERVAL 90 DAY "
                            "AND dictGet('trace_dict', 'project_id', trace_id) IN %(project_ids)s",
                            {"project_ids": project_ids},
                            timeout_ms=5000,
                        )
                    else:
                        ws_id = str(workspace.id)
                        result = ch.execute_read(
                            "SELECT DISTINCT source_id FROM usage_apicalllog "
                            "WHERE workspace_id = toUUID(%(ws_id)s) "
                            "AND status = 'success' AND length(source_id) > 0 "
                            "AND _peerdb_is_deleted = 0",
                            {"ws_id": ws_id},
                            timeout_ms=5000,
                        )

                    raw_rows = result[0] if isinstance(result, tuple) else result
                    used_template_ids = [
                        (
                            r[0]
                            if isinstance(r, (list, tuple))
                            else r.get("tid", r.get("source_id", ""))
                        )
                        for r in raw_rows
                    ]

                if not used_template_ids and filter_by_project:
                    used_template_ids = list(
                        CustomEvalConfig.objects.filter(
                            project_id__in=project_ids,
                            deleted=False,
                        )
                        .values_list("eval_template_id", flat=True)
                        .distinct()
                    )
                elif used_template_ids and filter_by_project:
                    used_template_ids = list(
                        CustomEvalConfig.objects.filter(
                            id__in=used_template_ids,
                        ).values_list("eval_template_id", flat=True)
                    )

                if used_template_ids:
                    eval_templates = EvalTemplate.no_workspace_objects.filter(
                        id__in=used_template_ids,
                        deleted=False,
                    ).values("id", "name", "config", "choices")
                elif filter_by_project:
                    eval_templates = EvalTemplate.objects.none().values(
                        "id", "name", "config", "choices"
                    )
                else:
                    eval_templates = EvalTemplate.objects.filter(
                        organization=workspace.organization,
                        deleted=False,
                    ).values("id", "name", "config", "choices")

                per_eval_config = request.query_params.get("per_eval_config") == "true"
                metrics.extend(
                    build_eval_metric_entries(
                        eval_templates=eval_templates,
                        project_ids=project_ids if filter_by_project else [],
                        workspace=workspace,
                        per_eval_config=per_eval_config,
                    )
                )
            except (ImportError, Exception) as e:
                logger.warning(f"Failed to load eval templates: {e}")

            # 3b. Simulation eval metrics
            agent_definition_id = request.query_params.get("agent_definition_id")
            if agent_definition_id:
                try:
                    metrics.extend(
                        build_simulation_eval_metric_entries(
                            agent_definition_id,
                            workspace,
                        )
                    )
                except (ImportError, Exception) as e:
                    logger.warning(f"Failed to load simulation eval configs: {e}")

            # 4. Annotation metrics
            try:
                from django.db.models import Q

                from model_hub.models.develop_annotations import AnnotationsLabels

                if filter_by_project:
                    from model_hub.models.score import Score

                    used_label_ids = list(
                        Score.objects.filter(
                            Q(project_id__in=project_ids)
                            | Q(trace__project_id__in=project_ids)
                            | Q(observation_span__project_id__in=project_ids)
                            | Q(trace_session__project_id__in=project_ids),
                            deleted=False,
                        )
                        .values_list("label_id", flat=True)
                        .distinct()
                    )
                    if used_label_ids:
                        annotation_labels = (
                            AnnotationsLabels.no_workspace_objects.filter(
                                id__in=used_label_ids,
                            ).values("id", "name", "type", "settings")
                        )
                    else:
                        annotation_labels = (
                            AnnotationsLabels.no_workspace_objects.none().values(
                                "id", "name", "type", "settings"
                            )
                        )
                else:
                    annotation_labels = AnnotationsLabels.no_workspace_objects.filter(
                        Q(organization=workspace.organization),
                        Q(workspace__isnull=True) | Q(workspace=workspace),
                    ).values("id", "name", "type", "settings")

                for al in annotation_labels:
                    label_type = al.get("type", "numeric")
                    settings = al.get("settings") or {}

                    metric_entry = {
                        "name": str(al["id"]),
                        "display_name": al["name"],
                        "category": "annotation_metric",
                        "source": "both",
                        "sources": ["datasets", "traces"],
                        "output_type": label_type,
                    }

                    if label_type == "categorical":
                        options = settings.get("options", [])
                        metric_entry["choices"] = [
                            opt.get("label", "")
                            for opt in options
                            if isinstance(opt, dict) and opt.get("label")
                        ]
                    elif label_type == "thumbs_up_down":
                        metric_entry["choices"] = ["Thumbs Up", "Thumbs Down"]

                    metrics.append(metric_entry)
            except Exception:
                logger.exception("annotation_metrics_failed")

            # Collect span attributes from background thread
            custom_attributes = f_attrs.result()
            for attr in custom_attributes:
                k = attr["key"] if isinstance(attr, dict) else attr
                t = attr.get("type", "string") if isinstance(attr, dict) else "string"
                metrics.append(
                    {
                        "name": k,
                        "display_name": k,
                        "category": "custom_attribute",
                        "source": "traces",
                        "type": t,
                    }
                )

            # 7. Custom columns (datasets only)
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
                    metrics.append(
                        {
                            "name": str(col["id"]),
                            "display_name": col["name"],
                            "category": "custom_column",
                            "source": "datasets",
                            "type": (
                                "number" if col["data_type"] != "boolean" else "boolean"
                            ),
                            "data_type": col["data_type"],
                        }
                    )
            except (ImportError, Exception):
                pass

            # 8. Simulation system metrics (numeric — for aggregation)
            metrics.extend(
                [
                    {
                        "name": "call_count",
                        "display_name": "Call Count",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "",
                    },
                    {
                        "name": "success_rate",
                        "display_name": "Success Rate",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "%",
                    },
                    {
                        "name": "failure_rate",
                        "display_name": "Failure Rate",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "%",
                    },
                    {
                        "name": "duration",
                        "display_name": "Duration",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "s",
                    },
                    {
                        "name": "response_time",
                        "display_name": "Response Time",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "ms",
                    },
                    {
                        "name": "agent_latency",
                        "display_name": "Agent Latency",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "ms",
                    },
                    {
                        "name": "stt_latency",
                        "display_name": "STT Latency",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "ms",
                    },
                    {
                        "name": "tts_latency",
                        "display_name": "TTS Latency",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "ms",
                    },
                    {
                        "name": "llm_latency",
                        "display_name": "LLM Latency",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "ms",
                    },
                    {
                        "name": "total_cost",
                        "display_name": "Total Cost",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "cents",
                    },
                    {
                        "name": "stt_cost",
                        "display_name": "STT Cost",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "cents",
                    },
                    {
                        "name": "tts_cost",
                        "display_name": "TTS Cost",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "cents",
                    },
                    {
                        "name": "llm_cost",
                        "display_name": "LLM Cost",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "cents",
                    },
                    {
                        "name": "customer_cost",
                        "display_name": "Customer Cost",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "cents",
                    },
                    {
                        "name": "overall_score",
                        "display_name": "Overall Score",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "",
                    },
                    {
                        "name": "message_count",
                        "display_name": "Message Count",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "",
                    },
                    {
                        "name": "user_interruptions",
                        "display_name": "User Interruptions",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "",
                    },
                    {
                        "name": "user_interruption_rate",
                        "display_name": "User Interruption Rate",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "/min",
                    },
                    {
                        "name": "ai_interruptions",
                        "display_name": "AI Interruptions",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "",
                    },
                    {
                        "name": "ai_interruption_rate",
                        "display_name": "AI Interruption Rate",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "/min",
                    },
                    {
                        "name": "stop_time_after_interruption",
                        "display_name": "Stop Time After Interruption",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "ms",
                    },
                    {
                        "name": "user_wpm",
                        "display_name": "User WPM",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "wpm",
                    },
                    {
                        "name": "bot_wpm",
                        "display_name": "Bot WPM",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "wpm",
                    },
                    {
                        "name": "talk_ratio",
                        "display_name": "Talk Ratio",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "number",
                        "unit": "%",
                    },
                ]
            )

            # 8b. Simulation breakdown/filter dimensions (string — for grouping & filtering)
            metrics.extend(
                [
                    {
                        "name": "simulation",
                        "display_name": "Simulation",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "scenario",
                        "display_name": "Scenario",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "agent_definition",
                        "display_name": "Agent",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "agent_version",
                        "display_name": "Agent Version",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "persona",
                        "display_name": "Persona",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "call_type",
                        "display_name": "Call Type",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "status",
                        "display_name": "Status",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "scenario_type",
                        "display_name": "Scenario Type",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "ended_reason",
                        "display_name": "Ended Reason",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "run_test",
                        "display_name": "Test",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "test_execution",
                        "display_name": "Test Run",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    # Persona attributes for breakdown/filtering
                    {
                        "name": "persona_gender",
                        "display_name": "Persona Gender",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "persona_age_group",
                        "display_name": "Persona Age Group",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "persona_location",
                        "display_name": "Persona Location",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "persona_profession",
                        "display_name": "Persona Profession",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "persona_personality",
                        "display_name": "Persona Personality",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "persona_communication_style",
                        "display_name": "Persona Communication Style",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "persona_accent",
                        "display_name": "Persona Accent",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "persona_language",
                        "display_name": "Persona Language",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                    {
                        "name": "persona_conversation_speed",
                        "display_name": "Persona Conversation Speed",
                        "category": "system_metric",
                        "source": "simulation",
                        "type": "string",
                        "unit": "",
                    },
                ]
            )

            for metric in metrics:
                if (
                    metric.get("source") in ("simulation", "datasets")
                    and metric.get("type") == "string"
                ):
                    metric["allowed_aggregations"] = ["count", "count_distinct"]

            metrics = _suppress_customer_attribute_metric_aliases(metrics)

            # (Simulation eval metrics are now covered by the central
            #  EvalTemplate listing in section 3 above.)

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
        analytics = AnalyticsQueryService() if is_clickhouse_enabled() else None
        for pid in project_ids:
            if analytics is not None:
                keys = analytics.get_span_attribute_keys_ch(pid)
            else:
                keys = SQL_query_handler.get_span_attributes_for_project(pid)
            for key in keys:
                attr = {"name": key, "display_name": key, "type": "string"}
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

    @action(detail=False, methods=["get"])
    def filter_values(self, request):
        """Return distinct values for a given metric/attribute, for filter value picker."""
        query_serializer = DashboardFilterValuesQuerySerializer(
            data=request.query_params
        )
        if not query_serializer.is_valid():
            return self._gm.bad_request(query_serializer.errors)

        query_params = query_serializer.validated_data
        metric_name = query_params["metric_name"]
        metric_type = query_params["metric_type"]
        source = query_params["source"]
        project_ids = query_params.get("project_ids", [])
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

        # Traces source (default)
        # Validate project_ids belong to this workspace
        workspace_project_ids = {
            str(pid)
            for pid in Project.objects.filter(
                workspace=request.workspace,
            ).values_list("id", flat=True)
        }
        if project_ids:
            project_ids = [pid for pid in project_ids if pid in workspace_project_ids]
        else:
            project_ids = list(workspace_project_ids)

        try:
            if metric_type == "annotation_metric" and metric_name == "annotator":
                from django.db.models import Q

                from model_hub.models.score import Score

                rows = (
                    Score.objects.filter(
                        deleted=False,
                        annotator_id__isnull=False,
                    )
                    .filter(
                        Q(project_id__in=project_ids)
                        | Q(trace__project_id__in=project_ids)
                        | Q(observation_span__project_id__in=project_ids)
                        | Q(trace_session__project_id__in=project_ids)
                    )
                    .values(
                        "annotator_id",
                        "annotator__name",
                        "annotator__email",
                    )
                    .distinct()
                    .order_by("annotator__name", "annotator__email")
                )
                values = []
                seen = set()
                for row in rows:
                    user_id = str(row["annotator_id"])
                    if user_id in seen:
                        continue
                    seen.add(user_id)
                    name = (row.get("annotator__name") or "").strip()
                    email = (row.get("annotator__email") or "").strip()
                    label = name or email or user_id
                    option = {"value": user_id, "label": label}
                    if name:
                        option["name"] = name
                    if email:
                        option["email"] = email
                    if name and email and email != name:
                        option["description"] = email
                    values.append(option)
                return self._gm.success_response({"values": values})

            if not is_clickhouse_enabled() and metric_type not in (
                "annotation_metric",
                "eval_metric",
            ):
                return self._gm.success_response({"values": []})

            analytics = AnalyticsQueryService()

            if metric_type == "system_metric":
                col_map = {
                    "trace_id": "trace_id",
                    "span_id": "id",
                    "project": "toString(project_id)",
                    "model": "model",
                    "status": "status",
                    "provider": "provider",
                    "observation_type": "observation_type",
                    "span_kind": "observation_type",  # span_kind maps to observation_type in CH
                    "service_name": "service_name",  # OTel service.name; matches _STRING_FILTER_COL
                    "name": "name",
                    "span_name": "name",
                    "session": "toString(trace_session_id)",
                    "tag": "arrayJoin(trace_tags)",
                    "prompt_name": "dictGet('prompt_dict', 'prompt_name', prompt_version_id)",
                    "prompt_version": "dictGet('prompt_dict', 'template_version', prompt_version_id)",
                    "prompt_label": "dictGet('prompt_label_dict', 'name', prompt_label_id)",
                }
                enduser_string_cols = {
                    "user": "user_id",
                    "user_id": "user_id",
                    "user_id_type": "user_id_type",
                }
                if metric_name in enduser_string_cols:
                    enduser_col = enduser_string_cols[metric_name]
                    try:
                        # P3b step2 precondition — the user/user_id_type filter-
                        # value list is cut off the legacy CDC `tracer_enduser`
                        # onto the v2 `end_users` RMT (017): `_peerdb_is_deleted`
                        # → `is_deleted`; `user_id`/`user_id_type` columns are
                        # identical. The legacy table stops getting new users
                        # once step2 drops the PG get_or_create → PG→CDC chain,
                        # so a newly-active user's `user_id` would be MISSING from
                        # this dropdown; `end_users` is kept fresh by the P3a-ii
                        # ingest dual-write. Both are OLD-keyed pre-flip with the
                        # same rows → byte-identical value list (gate B).
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
                            sql, {"project_ids": project_ids}, timeout_ms=5000
                        )
                        values = [
                            {"value": row["val"], "label": row["val"]}
                            for row in result.data
                        ]
                    except Exception as e:
                        logger.warning(
                            "filter_values_ch_query_failed",
                            metric_name=metric_name,
                            error=str(e)[:200],
                        )
                        values = []
                    return self._gm.success_response({"values": values})

                col_expr = col_map.get(metric_name)
                if not col_expr:
                    return self._gm.success_response({"values": []})

                try:
                    # Exclude empty strings and the zero UUID. PeerDB CDC
                    # replicates PostgreSQL NULL UUIDs as ClickHouse's
                    # default UUID value (all zeroes) because CH's UUID
                    # type is non-nullable by default — the Nullable
                    # wrapper isn't always preserved through the MV chain.
                    null_uuid = "00000000-0000-0000-0000-000000000000"
                    # Trace Name = root span name; restrict to root spans.
                    root_only_clause = (
                        "AND (parent_span_id IS NULL OR parent_span_id = '') "
                        if metric_name == "name"
                        else ""
                    )

                    ch_params: dict = {"project_ids": project_ids}
                    if metric_name == "session":
                        ts_remap_join = remap_left_join(
                            "sp.trace_session_id",
                            "trace_session_id_remap",
                            "ts_remap",
                        )
                        ts_resolved = resolved_id_expr(
                            "sp.trace_session_id", "ts_remap"
                        )
                        col_expr = f"toString({ts_resolved})"
                        limit = 20 if search else 500
                        if search:
                            ch_params["search_pattern"] = f"%{search}%"
                        search_clause = (
                            f"AND {col_expr} ILIKE %(search_pattern)s "
                            if search
                            else ""
                        )
                        sql = (
                            f"SELECT DISTINCT {col_expr} AS val "
                            f"FROM spans AS sp "
                            f"{ts_remap_join} "
                            f"WHERE sp.project_id IN %(project_ids)s "
                            f"AND sp.is_deleted = 0 "
                            f"AND {col_expr} NOT IN ('', '{null_uuid}') "
                            f"{search_clause}"
                            f"ORDER BY val "
                            f"LIMIT {limit}"
                        )
                    else:
                        limit = 20 if search else 500
                        if search:
                            ch_params["search_pattern"] = f"%{search}%"
                        search_clause = (
                            f"AND toString({col_expr}) ILIKE %(search_pattern)s "
                            if search
                            else ""
                        )
                        sql = (
                            f"SELECT DISTINCT {col_expr} AS val "
                            f"FROM spans "
                            f"WHERE project_id IN %(project_ids)s "
                            f"AND is_deleted = 0 "
                            f"AND {col_expr} NOT IN ('', '{null_uuid}') "
                            f"{root_only_clause}"
                            f"{search_clause}"
                            f"ORDER BY val "
                            f"LIMIT {limit}"
                        )
                    result = analytics.execute_ch_query(sql, ch_params, timeout_ms=5000)
                    values = [row["val"] for row in result.data]
                except Exception as e:
                    logger.warning(
                        "filter_values_ch_query_failed",
                        metric_name=metric_name,
                        error=str(e)[:200],
                    )
                    values = []

                if metric_name == "session" and source == "sessions":
                    from tracer.services.clickhouse.v2.trace_session_dict_reader import (
                        resolve_session_fields,
                    )

                    session_fields = resolve_session_fields(values)
                    values = [
                        {
                            "value": value,
                            "label": str(
                                session_fields.get(value, {}).get("display_name")
                                or session_fields.get(value, {}).get(
                                    "external_session_id"
                                )
                                or value
                            ),
                        }
                        for value in values
                    ]
                elif metric_name == "project":
                    name_map = dict(
                        Project.objects.filter(
                            id__in=project_ids,
                            workspace=request.workspace,
                        ).values_list("id", "name")
                    )
                    name_map = {str(k): v for k, v in name_map.items()}
                    values = [{"value": v, "label": name_map.get(v, v)} for v in values]
                else:
                    values = [{"value": v, "label": v} for v in values]

            elif metric_type in ("annotation_metric", "eval_metric"):
                # Annotation / eval filter values are derived from the label
                # definition (settings) and, for categorical annotations, from
                # stored scores. Older imported/backfilled labels can have
                # real choices in Score.value without settings.options; relying
                # only on settings makes the value dropdown empty even though
                # the annotation metric itself is available.
                from model_hub.models.develop_annotations import AnnotationsLabels

                try:
                    label = AnnotationsLabels.no_workspace_objects.get(
                        pk=metric_name, deleted=False
                    )
                except AnnotationsLabels.DoesNotExist:
                    return self._gm.success_response({"values": []})

                label_type = label.type
                settings = label.settings or {}

                def add_value_option(options, seen, raw_value, raw_label=None):
                    if raw_value in (None, ""):
                        return
                    value = str(raw_value)
                    if not value or value in seen:
                        return
                    seen.add(value)
                    options.append(
                        {
                            "value": value,
                            "label": str(raw_label or raw_value),
                        }
                    )

                if label_type == "categorical":
                    values = []
                    seen_values = set()
                    for opt in settings.get("options", []):
                        if isinstance(opt, dict):
                            option_value = (
                                opt.get("value") or opt.get("label") or opt.get("name")
                            )
                            option_label = (
                                opt.get("label") or opt.get("name") or option_value
                            )
                            add_value_option(
                                values, seen_values, option_value, option_label
                            )
                        else:
                            add_value_option(values, seen_values, opt)

                    # Include actual stored categorical choices as a fallback
                    # and as protection against stale label settings.
                    from django.db.models import Q

                    from model_hub.models.score import Score

                    score_qs = Score.objects.filter(
                        label_id=label.id,
                        deleted=False,
                    )
                    if project_ids:
                        score_qs = score_qs.filter(
                            Q(project_id__in=project_ids)
                            | Q(trace__project_id__in=project_ids)
                            | Q(observation_span__project_id__in=project_ids)
                            | Q(trace_session__project_id__in=project_ids)
                        )

                    for payload in score_qs.values_list("value", flat=True).order_by(
                        "-updated_at"
                    )[:5000]:
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
                            add_value_option(values, seen_values, raw_value)
                elif label_type == "star":
                    no_of_stars = settings.get("no_of_stars", 5)
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

            elif metric_type == "custom_attribute":
                # Use mapContains() so the `idx_attrs_string_keys` bloom
                # filter index prunes granules that don't have the key.
                # Without this, wide-attribute projects can blow past
                # ClickHouse's `max_bytes_to_read` limit (code 307) and
                # the endpoint returns 400 — see the failure on
                # conversation.recording.mono.assistant / ended_reason for
                # heavy voice projects.
                sql = (
                    "SELECT DISTINCT attrs_string[%(attr_key)s] AS val "
                    "FROM spans "
                    "WHERE project_id IN %(project_ids)s "
                    "AND is_deleted = 0 "
                    "AND mapContains(attrs_string, %(attr_key)s) "
                    "AND attrs_string[%(attr_key)s] != '' "
                    "ORDER BY val "
                    "LIMIT 500"
                )
                result = analytics.execute_ch_query(
                    sql,
                    {"project_ids": project_ids, "attr_key": metric_name},
                    timeout_ms=15000,
                )
                values = [
                    {"value": row["val"], "label": row["val"]} for row in result.data
                ]
            else:
                values = []

            return self._gm.success_response({"values": values})
        except Exception as e:
            logger.error("fetch_filter_values_failed", error=str(e))
            return self._gm.bad_request(
                "Failed to fetch filter values. Please try again later."
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

    def _execute_ch_query_config(self, query_config, workspace):
        """Execute a query_config against ClickHouse and return formatted results.

        Routes each metric to the appropriate builder based on source.
        """
        serializer = DashboardQuerySerializer(data=query_config)
        if not serializer.is_valid():
            return self._gm.bad_request(f"Invalid query config: {serializer.errors}")
        query_config = _normalize_dashboard_query_filters(serializer.validated_data)

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

        ch_client = get_clickhouse_client()
        metric_results = []

        if trace_metrics:
            trace_config = {**query_config, "metrics": trace_metrics}
            project_ids = trace_config.get("project_ids", [])
            if not project_ids:
                project_ids = list(
                    Project.objects.filter(
                        workspace=workspace,
                    ).values_list("id", flat=True)
                )
                trace_config["project_ids"] = [str(pid) for pid in project_ids]
                query_config["project_ids"] = trace_config["project_ids"]
            else:
                valid_count = Project.objects.filter(
                    id__in=project_ids,
                    workspace=workspace,
                ).count()
                if valid_count != len(project_ids):
                    return self._gm.bad_request(
                        "Some project_ids are invalid or not in this workspace"
                    )
            # v1↔v2 dispatch — flips with CH25_QUERY_TYPES_V2_PRIMARY=DASHBOARD
            from tracer.services.clickhouse.v2.dispatch import (
                get_query_builder_class,
            )

            _DashCls = get_query_builder_class("DASHBOARD")
            builder = _DashCls(trace_config)
            query_timeout = self._get_trace_query_timeout_ms(trace_config)

            def _fetch_trace_rows(sql, params):
                rows, column_types, _ = ch_client.execute_read(
                    sql, params, timeout_ms=query_timeout
                )
                col_names = [ct[0] for ct in column_types]
                return [dict(zip(col_names, row, strict=True)) for row in rows]

            metric_results.extend(
                DashboardViewSet._run_metric_queries(builder, "traces", _fetch_trace_rows)
            )

        if dataset_metrics:
            ds_config = {**query_config, "metrics": dataset_metrics}
            ds_config["workspace_id"] = str(workspace.id)
            builder = DatasetQueryBuilder(ds_config)

            def _fetch_ds_rows(sql, params):
                rows, column_types, _ = ch_client.execute_read(sql, params)
                col_names = [ct[0] for ct in column_types]
                return [dict(zip(col_names, row, strict=True)) for row in rows]

            metric_results.extend(
                DashboardViewSet._run_metric_queries(builder, "datasets", _fetch_ds_rows)
            )

        if simulation_metrics:
            sim_config = {**query_config, "metrics": simulation_metrics}
            sim_config["workspace_id"] = str(workspace.id)
            metric_results.extend(
                self._run_simulation_clickhouse_queries(ch_client, sim_config)
            )

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
            formatted = DashboardQueryBuilder(query_config).format_results(
                metric_results, project_name_map=project_name_map
            )
        else:
            formatted = formatter.format_results(metric_results)

        return self._gm.success_response(formatted)

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={
            200: DashboardQueryApiResponseSerializer,
            400: ApiErrorResponseSerializer,
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
            query_config = widget.query_config
            if not query_config or not query_config.get("metrics"):
                return self._gm.bad_request(
                    "Widget has no query configuration or metrics defined."
                )

            return self._execute_ch_query_config(query_config, request.workspace)
        except Exception as e:
            logger.error("widget_query_execution_failed", error=str(e), exc_info=True)
            return self._gm.bad_request(f"Query execution failed: {str(e)}")

    @validated_request(
        request_serializer=DashboardPreviewQuerySerializer,
        responses={
            200: DashboardQueryApiResponseSerializer,
            400: ApiErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"], url_path="preview")
    def preview_query(self, request, *args, **kwargs):
        """Execute an ad-hoc query_config without saving, for live preview."""
        try:
            if not is_clickhouse_enabled():
                return self._gm.bad_request("ClickHouse is not enabled.")

            query_config = request.validated_data["query_config"]

            return self._execute_ch_query_config(query_config, request.workspace)
        except Exception as e:
            logger.error("query_preview_failed", error=str(e), exc_info=True)
            return self._gm.bad_request(f"Query preview failed: {str(e)}")
