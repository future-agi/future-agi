from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID

import structlog
from django.conf import settings
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
    AttributeReadSelector,
    InvalidAttributeKey,
)
from tracer.services.clickhouse.client import (
    get_clickhouse_client,
    is_clickhouse_enabled,
)
from tracer.services.clickhouse.filter_value_reads import (
    SYSTEM_FILTER_VALUE_METRICS,
    read_span_system_filter_values,
)
from tracer.services.clickhouse.list_cursor import (
    ListCursorError,
    cursor_scope_for_request,
    decode_list_cursor,
    encode_list_cursor,
)
from tracer.services.clickhouse.query_builders.dashboard import (
    METRIC_UNITS,
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
from tracer.services.clickhouse.read_budget import (
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
    mark_refresh_failed,
    publish_exact_snapshot,
    read_exact_snapshot,
    read_or_schedule_exact_snapshot,
)
from tracer.utils.workspace_scope import project_queryset_for_request
from tracer.views.span_attributes import (
    is_attribute_api_read_unavailable_error,
    retained_attribute_window_start,
)

logger = structlog.get_logger(__name__)


class DashboardExactReadError(RuntimeError):
    """A dashboard refresh did not produce every requested exact metric."""


class DashboardQueryScopeError(ValueError):
    """A requested dashboard scope is outside the current workspace."""


def _materialize_dashboard_query_scope(
    query_config,
    workspace,
    *,
    trace_metrics,
    dataset_metrics,
):
    """Freeze every implicit all-resource scope into the exact cache identity.

    Empty ``project_ids``/``dataset_ids`` mean "all current workspace rows" at
    the API boundary. Keeping that sentinel in a long-lived cache key can serve
    a project after it moves out of the workspace or omit a resource added
    later. Resolve, authorize, stringify, and sort the concrete IDs before any
    cache read or refresh is scheduled.
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
        project_queryset = Project.objects.filter(workspace=workspace)
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
        from model_hub.models.develop_dataset import Dataset

        try:
            requested_dataset_ids = [
                str(UUID(str(value))) for value in scoped.get("dataset_ids") or []
            ]
        except (AttributeError, TypeError, ValueError) as exc:
            raise DashboardQueryScopeError(
                "Some dataset_ids are invalid or not in this workspace"
            ) from exc
        dataset_queryset = Dataset.objects.filter(
            workspace=workspace,
            deleted=False,
        )
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


# Exact trace dashboards may hydrate wide attribute Maps. Keep every query
# finite at the ClickHouse boundary so an exact read either completes or is
# returned as unavailable without monopolising the shared cluster. Two metric
# queries are allowed in flight below, making the aggregate request ceiling
# predictable as well.
_DASHBOARD_TRACE_READ_SETTINGS = {
    "max_threads": 2,
    "max_rows_to_read": 30_000_000,
    "max_bytes_to_read": 8 * 1024 * 1024 * 1024,
    "max_memory_usage": 512 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "max_result_rows": 250_000,
    "max_result_bytes": 64 * 1024 * 1024,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}
_DASHBOARD_TRACE_MAX_CONCURRENT_METRICS = 2


def _fetch_exact_dashboard_rows(
    *,
    analytics,
    sql,
    params,
    timeout_ms,
    settings,
):
    """Run one exact, current-state dashboard metric for the full window."""

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
    }
    if filter_item.get("output_type"):
        internal["output_type"] = filter_item["output_type"]
    if metric_type == "custom_attribute":
        internal["attribute_type"] = "number" if filter_type == "number" else "string"
        # Keep the validated canonical shape for typed span-attribute
        # compilation. The legacy flattened fields above remain for saved
        # configs and non-attribute dashboard builders.
        internal["canonical_filter"] = filter_item
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

        refresh = str(request.query_params.get("refresh", "false")).lower() in {
            "1",
            "true",
        }
        cache_identity = {
            "workspace_id": str(request.workspace.id),
            "query_config": query_config,
        }
        return self._gm.success_response(
            read_or_schedule_exact_snapshot(
                "dashboard-query",
                cache_identity,
                refresh=refresh,
                pending_payload=_pending_dashboard_payload(query_config),
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
            for pid in project_queryset_for_request(request).values_list(
                "id", flat=True
            )
        }
        if project_ids:
            project_ids = [pid for pid in project_ids if pid in workspace_project_ids]
        else:
            project_ids = list(workspace_project_ids)

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
                annotator_ids = (
                    AnnotationLabelScoresProjectPG().annotator_ids_for_projects(
                        project_ids
                    )
                )
                users = (
                    User.objects.filter(id__in=annotator_ids)
                    .values("id", "name", "email")
                    .order_by("name", "email")
                )
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
                return self._gm.success_response({"values": values})

            # Filter-value reads are backed exclusively by the direct-write
            # CH25 tables.  Using the legacy service here silently targets the
            # wrong cluster in split deployments even though the SQL names the
            # same ``spans``/``end_users`` tables.
            analytics = V2AnalyticsQueryService()

            if metric_type == "system_metric":
                enduser_string_cols = {
                    "user": "user_id",
                    "user_id": "user_id",
                    "user_id_type": "user_id_type",
                }
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
                            sql, {"project_ids": project_ids}, timeout_ms=5000
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
                    return self._gm.success_response({"values": values})

                if metric_name not in SYSTEM_FILTER_VALUE_METRICS:
                    return self._gm.success_response({"values": []})

                try:
                    value_read = read_span_system_filter_values(
                        analytics,
                        project_ids=project_ids,
                        metric_name=metric_name,
                        search=search,
                        limit=20 if search else 500,
                        lookback_days=int(
                            getattr(
                                settings,
                                "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS",
                                self.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS,
                            )
                        ),
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
                return self._gm.success_response(
                    {"values": values, **value_read.metadata()}
                )

            elif metric_type == "eval_metric":
                # Observe exposes CustomEvalConfig ids while older dashboard
                # widgets can still carry EvalTemplate ids. Resolve either id
                # through a config attached to the already-authorized project
                # set; a guessed config/template UUID from another tenant or
                # project must not reveal its output definition or choices.
                from django.core.exceptions import ValidationError
                from django.db.models import Q

                try:
                    eval_config = (
                        CustomEvalConfig.no_workspace_objects.filter(
                            project_id__in=project_ids,
                            project__workspace=request.workspace,
                            project__organization=request.workspace.organization,
                            eval_template__deleted=False,
                        )
                        .filter(Q(id=metric_name) | Q(eval_template_id=metric_name))
                        .select_related("eval_template")
                        .first()
                    )
                except (TypeError, ValueError, ValidationError):
                    eval_config = None

                if eval_config is None:
                    return self._gm.success_response({"values": []})

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
                        raw_value = choice
                        raw_label = choice
                        if isinstance(choice, dict):
                            raw_value = (
                                choice.get("value")
                                or choice.get("label")
                                or choice.get("name")
                            )
                            raw_label = (
                                choice.get("label") or choice.get("name") or raw_value
                            )
                        if raw_value in (None, ""):
                            continue
                        value = str(raw_value)
                        if value in seen_values:
                            continue
                        seen_values.add(value)
                        values.append({"value": value, "label": str(raw_label)})
                else:
                    # Score evals use numeric entry rather than a misleading
                    # categorical vocabulary.
                    values = []

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

                try:
                    label_queryset = AnnotationsLabels.no_workspace_objects.filter(
                        pk=metric_name,
                        organization=request.workspace.organization,
                        deleted=False,
                    ).filter(Q(workspace=request.workspace) | Q(workspace__isnull=True))
                    if project_ids:
                        label_queryset = label_queryset.filter(
                            Q(project_id__in=project_ids) | Q(project__isnull=True)
                        )
                    else:
                        label_queryset = label_queryset.filter(project__isnull=True)
                    label = label_queryset.first()
                except (TypeError, ValueError, ValidationError):
                    label = None
                if label is None:
                    return self._gm.success_response({"values": []})

                label_type = label.type
                label_settings = label.settings or {}

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
                    for opt in label_settings.get("options", []):
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

                    # Stored categorical choices are read from authoritative
                    # Score rows via tracer_project_id.  This avoids a cross-
                    # cluster legacy-score/direct-span subquery.
                    import json

                    from tracer.services.annotation_label_source import (
                        AnnotationLabelScoresProjectPG,
                    )

                    for (
                        payload_value
                    ) in AnnotationLabelScoresProjectPG().categorical_values_for_label(
                        label.id, project_ids
                    ):
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
                            add_value_option(values, seen_values, raw_value)
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

            elif metric_type == "custom_attribute":
                # metric_name is an exact key request. It must not depend on
                # the bounded browse inventory, where any rare key can be
                # outside the sample.
                selector = AttributeReadSelector(
                    typed_only=True,
                    json_attribute_mode="arrays",
                )
                try:
                    page_size = query_params.get("page_size")
                    cursor_token = query_params.get("cursor")
                    attribute_type = query_params.get("attribute_type")
                    if page_size is not None:
                        page_size = int(page_size)
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
                        if cursor_token and cursor_state.seen_rows != len(
                            seen_state.digests
                        ):
                            raise ListCursorError(
                                "invalid_cursor",
                                "The continuation cursor is invalid.",
                            )

                        page_read = selector.read_value_cursor_page(
                            project_ids,
                            metric_name,
                            page_size=page_size,
                            window_start=window_start,
                            window_end=window_end,
                            segment_end=segment_end,
                            before_identity=before_identity,
                            resume_identity=resume_identity,
                            resume_member_offset=resume_member_offset,
                            seen_value_digests=seen_state.digests,
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
                            appended_digests = page_read.seen_value_digests[
                                len(seen_state.digests) :
                            ]
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
                                seen_rows=len(page_read.seen_value_digests),
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
                        {
                            "values": values,
                            **metadata,
                        }
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
    ):
        """Execute a query_config against ClickHouse and return formatted results.

        Routes each metric to the appropriate builder based on source.
        """
        read_query_config = _canonicalize_persisted_dashboard_query_filters_for_read(
            query_config
        )
        serializer = DashboardQuerySerializer(data=read_query_config)
        if not serializer.is_valid():
            logger.warning(
                "dashboard_widget_query_config_invalid",
                invalid_fields=sorted(serializer.errors),
            )
            return self._gm.bad_request("Dashboard query configuration is invalid.")
        query_config = _normalize_dashboard_query_filters(serializer.validated_data)
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
            )
        except DashboardQueryScopeError as exc:
            return self._gm.bad_request(str(exc))

        cache_identity = cache_identity_override or {
            "workspace_id": str(workspace.id),
            "query_config": query_config,
        }
        previous = read_exact_snapshot("dashboard-query", cache_identity)
        if not _exact_worker:
            return self._gm.success_response(
                read_or_schedule_exact_snapshot(
                    "dashboard-query",
                    cache_identity,
                    refresh=bool(refresh),
                    pending_payload=_pending_dashboard_payload(query_config),
                )
            )

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
        query_timeout = 300_000

        if trace_metrics:
            trace_config = {
                **query_config,
                "metrics": trace_metrics,
                # Force raw latest-state spans instead of the independently
                # refreshed attribute rollup for customer-visible exact totals.
                "require_versioned_snapshot": True,
            }
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
            trace_config["organization_id"] = str(workspace.organization_id)
            trace_config["workspace_id"] = str(workspace.id)
            trace_analytics = V2AnalyticsQueryService()
            trace_builder = DashboardQueryBuilderV2(trace_config)
            trace_prepared = DashboardViewSet._prepare_metric_queries(trace_builder)

        if dataset_metrics:
            ds_config = {
                **query_config,
                "metrics": dataset_metrics,
                "workspace_id": str(workspace.id),
                "exact_snapshot_dimensions": True,
            }
            dataset_builder = DatasetQueryBuilder(ds_config)
            dataset_prepared = DashboardViewSet._prepare_metric_queries(dataset_builder)

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
                    timeout_ms=query_timeout,
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
                    timeout_ms=query_timeout,
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
                    timeout_ms=query_timeout,
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
            if previous is not None and not _exact_worker:
                return self._gm.success_response(mark_refresh_failed(previous))
            raise DashboardExactReadError(
                "one or more dashboard metrics did not complete exactly"
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
            }
        )
        if _exact_worker:
            return self._gm.success_response(formatted)
        published = publish_exact_snapshot(
            "dashboard-query",
            cache_identity,
            formatted,
        )
        return self._gm.success_response(published)

    @validated_request(
        request_serializer=DashboardSampleOptInSerializer,
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

            refresh = str(request.query_params.get("refresh", "false")).lower() in {
                "1",
                "true",
            }
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

            refresh = str(request.query_params.get("refresh", "false")).lower() in {
                "1",
                "true",
            }
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
