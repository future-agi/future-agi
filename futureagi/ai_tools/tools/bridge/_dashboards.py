"""DRF bridge registrations for the dashboard query engine (Phase 2A Packet D).

DashboardViewSet / DashboardWidgetViewSet CRUD is already bridged in
_misc_viewsets.py (list/get/create/update/delete_dashboard[_widget]); this
module ADDS the custom @actions (additive registration per the A10 rule —
the registry only collides on same-name/different-class).

`execute_dashboard_query` is the headline: the same ad-hoc query DSL the
dashboard UI sends (DashboardQuerySerializer). Its body schema is authored
by hand because the serializer nests sub-serializers
(time_range/metrics/breakdowns) that auto-introspection cannot flatten.
"""

from ai_tools.drf_bridge import expose_to_mcp
from tracer.views.dashboard import DashboardViewSet, DashboardWidgetViewSet

_METRIC_SHAPE_DOC = (
    "Each metric object: {\"name\": <metric name from list_dashboard_metrics>, "
    "\"type\": one of system_metric|eval_metric|annotation_metric|"
    "custom_attribute|custom_column, \"aggregation\": one of avg|median|max|min|"
    "p25|p50|p75|p90|p95|p99|count|count_distinct|sum|pass_rate|fail_rate|"
    "pass_count|fail_count|true_rate (default avg), \"source\": traces|datasets|"
    "simulation (default traces)}. eval_metric also takes \"config_id\"; "
    "annotation_metric takes \"label_id\"; custom_attribute takes "
    "\"attribute_key\"; custom_column takes \"column_id\". 1-5 metrics."
)

expose_to_mcp(
    category="tracing",
    tools={
        # ------------------------------------------------------------------
        # The dashboard query engine — ad-hoc analytics over traces/datasets/
        # simulation data without saving a widget.
        # ------------------------------------------------------------------
        "query": {
            "name": "execute_dashboard_query",
            "description": (
                "Run an ad-hoc dashboard analytics query and get chart-ready "
                "time series back (the same engine dashboard widgets use). "
                "Answers questions like 'average latency per day over the "
                "last 7 days' or 'eval pass rate by project'. Call "
                "`list_dashboard_metrics` first to discover valid metric "
                "names. " + _METRIC_SHAPE_DOC
            ),
            "query_params": {
                "time_range": {
                    "type": dict,
                    "required": True,
                    "description": (
                        "Time window: {\"preset\": one of 30m|6h|today|"
                        "yesterday|7D|30D|3M|6M|12M} OR {\"custom_start\": "
                        "ISO-8601, \"custom_end\": ISO-8601} (both together)."
                    ),
                },
                "metrics": {
                    "type": list,
                    "required": True,
                    "description": (
                        "List of 1-5 metric objects (see tool description "
                        "for the shape)."
                    ),
                },
                "granularity": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Time bucket: minute, hour, day (default), week, or "
                        "month."
                    ),
                },
                "project_ids": {
                    "type": list,
                    "required": False,
                    "description": (
                        "Optional list of trace project UUIDs to scope trace "
                        "metrics. Omit to include every project in the "
                        "workspace. **How to get them:** call "
                        "`list_trace_projects`."
                    ),
                },
                "filters": {
                    "type": list,
                    "required": False,
                    "description": (
                        "Optional filter list; each item {\"field\", \"op\", "
                        "\"value\"} (same shape the dashboard UI sends)."
                    ),
                },
                "breakdowns": {
                    "type": list,
                    "required": False,
                    "description": (
                        "Optional group-by dimensions; each item {\"name\": "
                        "<metric/attribute name>, \"type\": system_metric|"
                        "eval_metric|annotation_metric|custom_attribute}."
                    ),
                },
            },
        },
        "metrics": {
            "name": "list_dashboard_metrics",
            "method": "GET",
            "description": (
                "List every metric available to dashboard queries across "
                "traces, datasets and simulation sources — names, categories, "
                "types, units and allowed aggregations. Call this before "
                "`execute_dashboard_query` to pick valid metric names."
            ),
            "query_params": {
                "workflow": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional legacy source selector ('dataset' returns "
                        "only dataset metrics in the old grouped format). "
                        "Omit for the unified all-sources catalog."
                    ),
                },
            },
        },
        "filter_values": {
            "name": "get_dashboard_filter_values",
            "method": "GET",
            "description": (
                "Get the distinct values of a metric/attribute for building "
                "dashboard query filters (the filter value picker). E.g. the "
                "distinct model names, eval verdicts, or attribute values."
            ),
            "query_params": {
                "metric_name": {
                    "type": str,
                    "required": True,
                    "description": (
                        "Metric or attribute name to enumerate values for "
                        "(from `list_dashboard_metrics`). For "
                        "source='dataset_column' pass the column UUID."
                    ),
                },
                "metric_type": {
                    "type": str,
                    "required": False,
                    "description": (
                        "One of system_metric (default), eval_metric, "
                        "annotation_metric, custom_attribute, custom_column."
                    ),
                },
                "source": {
                    "type": str,
                    "required": False,
                    "description": (
                        "One of traces (default), datasets, dataset_column, "
                        "simulation."
                    ),
                },
                "project_ids": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional comma-separated trace project UUIDs to "
                        "scope the value search."
                    ),
                },
                "dataset_id": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Dataset UUID (required when source='dataset_column')."
                    ),
                },
            },
        },
        "simulation_agents": {
            "name": "list_dashboard_simulation_agents",
            "method": "GET",
            "description": (
                "List simulation agents with their linked observability "
                "projects — the agents available as a 'simulation' source in "
                "dashboard queries."
            ),
        },
    },
)(DashboardViewSet)

expose_to_mcp(
    category="tracing",
    tools={
        "execute_query": {
            "name": "execute_widget_query",
            "description": (
                "Execute a saved dashboard widget's stored query_config "
                "against ClickHouse and return its chart data. Use "
                "`list_dashboard_widgets` (with the dashboard) to find "
                "widget ids; use `preview_widget_query` for ad-hoc configs."
            ),
            "pk_field": "widget_id",
            "id_source": "list_dashboard_widgets",
            "path_kwargs": {
                "dashboard_id": {
                    "description": "UUID of the dashboard the widget belongs to.",
                    "id_source": "list_dashboards",
                },
            },
        },
        "preview_query": {
            "name": "preview_widget_query",
            "description": (
                "Execute an ad-hoc widget query_config WITHOUT saving a "
                "widget — live preview of dashboard chart data. query_config "
                "uses the same shape as `execute_dashboard_query` "
                "(time_range, metrics, granularity, project_ids, filters, "
                "breakdowns). " + _METRIC_SHAPE_DOC
            ),
            "query_params": {
                "query_config": {
                    "type": dict,
                    "required": True,
                    "description": (
                        "The widget query config object: {\"time_range\": "
                        "{...}, \"metrics\": [...], \"granularity\": ..., "
                        "\"project_ids\": [...], \"filters\": [...], "
                        "\"breakdowns\": [...]}."
                    ),
                },
            },
        },
        "reorder": {
            "name": "reorder_dashboard_widgets",
            "description": (
                "Reorder (and optionally resize) a dashboard's widgets in "
                "one batch. `order` is the full list of widget ids in the "
                "desired position order; items may also be objects "
                "{\"id\": <widget_id>, \"width\": 1-12} to set widths."
            ),
            "path_kwargs": {
                "dashboard_id": {
                    "description": "UUID of the dashboard whose widgets to reorder.",
                    "id_source": "list_dashboards",
                },
            },
            "query_params": {
                "order": {
                    "type": list,
                    "required": True,
                    "description": (
                        "Widget ids in the desired order; plain UUID strings "
                        "or {\"id\", \"width\"} objects."
                    ),
                },
            },
        },
        "duplicate_widget": {
            "name": "duplicate_dashboard_widget",
            "description": (
                "Duplicate a dashboard widget — creates '<name> (Copy)' next "
                "to the original with the same query and chart config."
            ),
            "pk_field": "widget_id",
            "id_source": "list_dashboard_widgets",
            "path_kwargs": {
                "dashboard_id": {
                    "description": "UUID of the dashboard the widget belongs to.",
                    "id_source": "list_dashboards",
                },
            },
            # Deliberately body-less detail POST (the documented A2 escape
            # hatch): everything the action needs is in the URL kwargs.
            "query_params": {},
        },
    },
)(DashboardWidgetViewSet)
