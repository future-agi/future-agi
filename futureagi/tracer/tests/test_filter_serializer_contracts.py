import json

from model_hub.serializers.contracts import (
    EvalApiLogTableQuerySerializer,
    EvalMetricQuerySerializer,
    EvalMetricRequestSerializer,
    OptimizeDatasetListQuerySerializer,
    PromptMetricsQuerySerializer,
)
from tracer.serializers.custom_eval_config import CustomEvalConfigListQuerySerializer
from tracer.serializers.dashboard import DashboardFilterValuesQuerySerializer
from tracer.serializers.eval_task import (
    EditEvalTaskSerializer,
    EvalTaskListQuerySerializer,
)
from tracer.serializers.filters import ObserveGraphDataRequestSerializer
from tracer.serializers.monitor import FetchGraphSerializer
from tracer.serializers.observation_span import (
    ObservationAttributeListQuerySerializer,
    SpanExportQuerySerializer,
    SpanIndexQuerySerializer,
    SpanListQuerySerializer,
    SpanObserveIndexQuerySerializer,
    SpanObserveListQuerySerializer,
)
from tracer.serializers.project import (
    ProjectGraphDataQuerySerializer,
    ProjectUserGraphDataQuerySerializer,
    ProjectUserGraphDataRequestSerializer,
    ProjectUserMetricsRequestSerializer,
    ProjectUsersAggregateGraphDataRequestSerializer,
    ProjectVersionExportSerializer,
)
from tracer.serializers.project_version import ProjectVersionRunsQuerySerializer
from tracer.serializers.trace import (
    TraceAgentGraphQuerySerializer,
    TraceExportQuerySerializer,
    TraceIndexQuerySerializer,
    TraceListQuerySerializer,
    TraceObserveIndexQuerySerializer,
    TraceObserveListQuerySerializer,
    TraceVoiceCallListQuerySerializer,
    UsersQuerySerializer,
)
from tracer.serializers.trace_session import (
    TraceSessionExportQuerySerializer,
    TraceSessionFilterValuesQuerySerializer,
    TraceSessionGraphDataRequestSerializer,
    TraceSessionListQuerySerializer,
    TraceSessionRetrieveQuerySerializer,
)


def _span_attr_filter(filter_op="equals", filter_value="alpha"):
    return {
        "column_id": "customer_tier",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


class TestFilterSerializerContracts:
    def test_users_query_serializer_decodes_strict_filter_query_param(self):
        serializer = UsersQuerySerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert serializer.is_valid(), serializer.errors
        filters = serializer.validated_data["filters"]
        assert filters[0]["filter_config"]["filter_op"] == "equals"

    def test_users_query_serializer_rejects_camel_case_filter_config(self):
        payload = _span_attr_filter()
        payload["filterConfig"] = payload.pop("filter_config")
        serializer = UsersQuerySerializer(data={"filters": json.dumps([payload])})

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_users_query_serializer_accepts_canonical_sort_params(self):
        serializer = UsersQuerySerializer(
            data={
                "sort_params": json.dumps(
                    [{"column_id": "last_active", "direction": "desc"}]
                )
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["sort_params"] == [
            {"column_id": "last_active", "direction": "desc"}
        ]

    def test_users_query_serializer_rejects_legacy_aliases(self):
        serializer = UsersQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "pageNumber": "1",
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors
        assert "pageNumber" in serializer.errors

    def test_users_query_serializer_rejects_legacy_sort_aliases(self):
        serializer = UsersQuerySerializer(
            data={
                "sort_params": json.dumps(
                    [{"columnId": "last_active", "sort": "desc"}]
                ),
            }
        )

        assert not serializer.is_valid()
        assert "sort_params" in serializer.errors

    def test_eval_task_filters_validate_span_attribute_contract(self):
        serializer = EditEvalTaskSerializer(
            data={
                "edit_type": "edit_rerun",
                "filters": {
                    "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                    "date_range": [
                        "2026-01-01T00:00:00Z",
                        "2026-01-31T23:59:59Z",
                    ],
                    "observation_type": ["llm", "tool"],
                    "span_attributes_filters": [_span_attr_filter()],
                },
            }
        )

        assert serializer.is_valid(), serializer.errors
        filters = serializer.validated_data["filters"]
        assert filters["observation_type"] == ["llm", "tool"]

    def test_eval_task_filters_reject_frontend_field_id_drift(self):
        serializer = EditEvalTaskSerializer(
            data={
                "edit_type": "edit_rerun",
                "filters": {
                    "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                    "span_kind": ["llm"],
                },
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_eval_task_filters_reject_legacy_span_attribute_operator(self):
        serializer = EditEvalTaskSerializer(
            data={
                "edit_type": "edit_rerun",
                "filters": {
                    "span_attributes_filters": [
                        _span_attr_filter("not_in_between", ["a", "b"])
                    ],
                },
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_eval_task_filters_reject_malformed_date_range(self):
        serializer = EditEvalTaskSerializer(
            data={
                "edit_type": "edit_rerun",
                "filters": {"date_range": ["2026-01-01T00:00:00Z"]},
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_eval_task_filters_coerce_scalar_observation_type_to_list(self):
        """Scalar observation_type is coerced to a list for convenience."""
        serializer = EditEvalTaskSerializer(
            data={
                "edit_type": "edit_rerun",
                "filters": {"observation_type": "llm"},
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["filters"]["observation_type"] == ["llm"]

    def test_dashboard_filter_values_query_requires_explicit_source_choices(self):
        serializer = DashboardFilterValuesQuerySerializer(
            data={
                "metric_name": "latency_ms",
                "metric_type": "system_metric",
                "source": "workflow",
            }
        )

        assert not serializer.is_valid()
        assert "source" in serializer.errors

    def test_dashboard_filter_values_query_parses_project_ids(self):
        serializer = DashboardFilterValuesQuerySerializer(
            data={
                "metric_name": "latency_ms",
                "metric_type": "system_metric",
                "source": "traces",
                "project_ids": "project-a, project-b,,",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["project_ids"] == [
            "project-a",
            "project-b",
        ]

    def test_dashboard_filter_values_query_accepts_sessions_source(self):
        serializer = DashboardFilterValuesQuerySerializer(
            data={
                "metric_name": "model",
                "metric_type": "system_metric",
                "source": "sessions",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["source"] == "sessions"

    def test_session_filter_values_query_accepts_canonical_columns_only(self):
        serializer = TraceSessionFilterValuesQuerySerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "column": "session_id",
            }
        )

        assert serializer.is_valid(), serializer.errors

    def test_session_filter_values_query_rejects_camel_case_columns(self):
        serializer = TraceSessionFilterValuesQuerySerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "column": "sessionId",
            }
        )

        assert not serializer.is_valid()
        assert "column" in serializer.errors

    def test_session_list_query_accepts_canonical_filters_and_sort(self):
        serializer = TraceSessionListQuerySerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "user_id": "customer-1",
                "filters": json.dumps([_span_attr_filter()]),
                "sort_params": json.dumps(
                    [{"column_id": "start_time", "direction": "desc"}]
                ),
                "page_number": "1",
                "page_size": "75",
                "bookmarked": "true",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["filters"][0]["column_id"] == "customer_tier"
        assert serializer.validated_data["sort_params"] == [
            {"column_id": "start_time", "direction": "desc"}
        ]
        assert serializer.validated_data["page_size"] == 75
        assert serializer.validated_data["bookmarked"] is True

    def test_session_list_query_rejects_legacy_query_and_filter_aliases(self):
        serializer = TraceSessionListQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "userId": "customer-1",
                "sortParams": json.dumps(
                    [{"column_id": "start_time", "direction": "desc"}]
                ),
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors
        assert "userId" in serializer.errors
        assert "sortParams" in serializer.errors

    def test_session_list_query_rejects_legacy_filter_shape(self):
        payload = _span_attr_filter()
        payload["filterConfig"] = payload.pop("filter_config")
        serializer = TraceSessionListQuerySerializer(
            data={"filters": json.dumps([payload])}
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_session_retrieve_query_rejects_legacy_aliases(self):
        serializer = TraceSessionRetrieveQuerySerializer(
            data={
                "userId": "customer-1",
                "sortParams": json.dumps(
                    [{"column_id": "start_time", "direction": "desc"}]
                ),
                "pageNumber": "1",
                "pageSize": "75",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "userId" in serializer.errors
        assert "sortParams" in serializer.errors
        assert "pageNumber" in serializer.errors
        assert "pageSize" in serializer.errors

    def test_eval_task_list_query_accepts_canonical_filters_and_sort(self):
        serializer = EvalTaskListQuerySerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
                "sort_params": json.dumps(
                    [{"column_id": "created_at", "direction": "desc"}]
                ),
                "page_number": "1",
                "page_size": "50",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["filters"][0]["column_id"] == "customer_tier"
        assert serializer.validated_data["sort_params"] == [
            {"column_id": "created_at", "direction": "desc"}
        ]

    def test_eval_task_list_query_rejects_legacy_aliases(self):
        serializer = EvalTaskListQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "sortParams": json.dumps(
                    [{"column_id": "created_at", "direction": "desc"}]
                ),
                "pageNumber": "1",
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors
        assert "sortParams" in serializer.errors
        assert "pageNumber" in serializer.errors

    def test_session_graph_request_accepts_canonical_filters(self):
        serializer = TraceSessionGraphDataRequestSerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "interval": "day",
                "property": "average",
                "req_data_config": {"id": "session_count", "type": "SYSTEM_METRIC"},
                "filters": [_span_attr_filter()],
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["req_data_config"]["id"] == "session_count"

    def test_session_graph_request_rejects_legacy_filter_shape(self):
        serializer = TraceSessionGraphDataRequestSerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "req_data_config": {"id": "session_count", "type": "SYSTEM_METRIC"},
                "filters": [
                    {"column": "duration", "operator": "greater_than", "value": 1}
                ],
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_observe_graph_request_rejects_camel_case_project_alias(self):
        serializer = ObserveGraphDataRequestSerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "interval": "day",
                "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
                "filters": [_span_attr_filter()],
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors

    def test_observe_graph_request_requires_metric_config(self):
        serializer = ObserveGraphDataRequestSerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": [_span_attr_filter()],
            }
        )

        assert not serializer.is_valid()
        assert "req_data_config" in serializer.errors

    def test_prompt_metrics_query_accepts_canonical_filters(self):
        serializer = PromptMetricsQuerySerializer(
            data={
                "prompt_template_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
                "search_term": "response",
                "page_number": "1",
                "page_size": "25",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["filters"][0]["column_id"] == "customer_tier"
        assert serializer.validated_data["page_number"] == 1

    def test_prompt_metrics_query_rejects_camel_case_query_and_filters(self):
        payload = _span_attr_filter()
        payload["filterConfig"] = payload.pop("filter_config")
        serializer = PromptMetricsQuerySerializer(
            data={
                "promptTemplateId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([payload]),
                "pageNumber": "1",
            }
        )

        assert not serializer.is_valid()
        assert "prompt_template_id" in serializer.errors
        assert "filters" in serializer.errors

    def test_trace_list_query_accepts_canonical_filters_and_sort(self):
        serializer = TraceListQuerySerializer(
            data={
                "project_version_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "trace_ids": "trace-a, trace-b",
                "filters": json.dumps([_span_attr_filter()]),
                "sort_params": json.dumps(
                    [{"column_id": "start_time", "direction": "asc"}]
                ),
                "page_number": "2",
                "page_size": "50",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["trace_ids"] == ["trace-a", "trace-b"]
        assert serializer.validated_data["filters"][0]["column_id"] == "customer_tier"
        assert serializer.validated_data["sort_params"] == [
            {"column_id": "start_time", "direction": "asc"}
        ]

    def test_trace_list_query_rejects_camel_case_contract(self):
        serializer = TraceListQuerySerializer(
            data={
                "projectVersionId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "sort_params": json.dumps(
                    [{"column_id": "start_time", "direction": "asc"}]
                ),
            }
        )

        assert not serializer.is_valid()
        assert "projectVersionId" in serializer.errors

    def test_trace_list_query_rejects_legacy_sort_contract(self):
        serializer = TraceListQuerySerializer(
            data={
                "project_version_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "sort_params": json.dumps([{"columnId": "start_time", "sort": "asc"}]),
            }
        )

        assert not serializer.is_valid()
        assert "sort_params" in serializer.errors

    def test_trace_observe_list_query_accepts_canonical_filters(self):
        serializer = TraceObserveListQuerySerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "project_version_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
                "page_number": "1",
                "page_size": "50",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["filters"][0]["column_id"] == "customer_tier"

    def test_trace_observe_list_query_rejects_camel_case_aliases(self):
        serializer = TraceObserveListQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "projectVersionId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "pageNumber": "1",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors
        assert "projectVersionId" in serializer.errors
        assert "pageNumber" in serializer.errors

    def test_trace_export_query_rejects_camel_case_aliases(self):
        serializer = TraceExportQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors

    def test_trace_voice_call_query_accepts_canonical_pagination(self):
        serializer = TraceVoiceCallListQuerySerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
                "page": "2",
                "page_size": "25",
                "remove_simulation_calls": "true",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["page"] == 2
        assert serializer.validated_data["remove_simulation_calls"] is True

    def test_trace_index_queries_reject_camel_case_aliases(self):
        trace_index = TraceIndexQuerySerializer(
            data={
                "traceId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "projectVersionId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
            }
        )
        observe_index = TraceObserveIndexQuerySerializer(
            data={
                "traceId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
            }
        )

        assert not trace_index.is_valid()
        assert not observe_index.is_valid()
        assert "traceId" in trace_index.errors
        assert "projectVersionId" in trace_index.errors
        assert "traceId" in observe_index.errors
        assert "projectId" in observe_index.errors

    def test_optimize_dataset_list_query_accepts_canonical_filters(self):
        serializer = OptimizeDatasetListQuerySerializer(
            data={
                "filters": json.dumps(
                    [
                        {
                            "key": "status",
                            "operator": "equals",
                            "value": ["completed"],
                            "data_type": "string",
                        },
                        {
                            "key": "start_date",
                            "operator": "between",
                            "value": [
                                "2026-01-01T00:00:00Z",
                                "2026-01-31T23:59:59Z",
                            ],
                            "data_type": "datetime",
                        },
                    ]
                ),
            }
        )

        assert serializer.is_valid(), serializer.errors
        filters = serializer.validated_data["filters"]
        assert filters[0]["operator"] == "equals"
        assert filters[1]["value"][1] == "2026-01-31T23:59:59Z"

    def test_optimize_dataset_list_query_rejects_legacy_filter_shape(self):
        serializer = OptimizeDatasetListQuerySerializer(
            data={
                "filters": json.dumps(
                    [
                        {
                            "key": "status",
                            "operator": "equal",
                            "value": ["completed"],
                            "dataType": "string",
                        }
                    ]
                ),
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_project_graph_query_accepts_canonical_filter_query_param(self):
        serializer = ProjectGraphDataQuerySerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "interval": "day",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["filters"][0]["column_id"] == "customer_tier"

    def test_project_graph_query_rejects_camel_case_project_id(self):
        serializer = ProjectGraphDataQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors

    def test_project_version_runs_query_rejects_legacy_aliases(self):
        serializer = ProjectVersionRunsQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "sortParams": json.dumps(
                    [{"column_id": "avg_latency", "direction": "desc"}]
                ),
                "pageNumber": "1",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors
        assert "sortParams" in serializer.errors
        assert "pageNumber" in serializer.errors

    def test_project_version_export_request_rejects_legacy_sort_shape(self):
        serializer = ProjectVersionExportSerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "runs_ids": [],
                "sort_params": [{"columnId": "avg_latency", "sort": "desc"}],
            }
        )

        assert not serializer.is_valid()
        assert "sort_params" in serializer.errors

    def test_project_version_export_request_defaults_omitted_run_ids(self):
        serializer = ProjectVersionExportSerializer(
            data={"project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f"}
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["runs_ids"] == []

    def test_project_user_metrics_request_rejects_legacy_filters(self):
        serializer = ProjectUserMetricsRequestSerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "end_user_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": [{"column": "total_cost", "operator": "gt", "value": 10}],
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_project_users_aggregate_graph_request_accepts_canonical_filters(self):
        serializer = ProjectUsersAggregateGraphDataRequestSerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "interval": "day",
                "filters": [_span_attr_filter()],
                "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["req_data_config"]["id"] == "latency"

    def test_project_users_aggregate_graph_request_rejects_bad_metric_config(self):
        serializer = ProjectUsersAggregateGraphDataRequestSerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "interval": "day",
                "filters": [_span_attr_filter()],
                "req_data_config": {
                    "id": "latency",
                    "type": "SYSTEM_METRIC",
                    "metricType": "legacy",
                },
            }
        )

        assert not serializer.is_valid()
        assert "req_data_config" in serializer.errors

    def test_project_user_graph_query_and_request_reject_legacy_aliases(self):
        query_serializer = ProjectUserGraphDataQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "endUserId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
            }
        )
        body_serializer = ProjectUserGraphDataRequestSerializer(
            data={
                "filterConfig": {},
                "filters": [_span_attr_filter()],
            }
        )

        assert not query_serializer.is_valid()
        assert not body_serializer.is_valid()
        assert "projectId" in query_serializer.errors
        assert "endUserId" in query_serializer.errors
        assert "filterConfig" in body_serializer.errors

    def test_fetch_graph_query_rejects_legacy_aliases(self):
        serializer = FetchGraphSerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "reqDataConfig": json.dumps({"id": "latency", "type": "SYSTEM_METRIC"}),
                "interval": "day",
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors
        assert "reqDataConfig" in serializer.errors

    def test_custom_eval_config_list_query_rejects_legacy_aliases(self):
        serializer = CustomEvalConfigListQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "taskId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps({}),
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors
        assert "taskId" in serializer.errors

    def test_custom_eval_config_list_query_rejects_ignored_filters(self):
        serializer = CustomEvalConfigListQuerySerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps({}),
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_observation_attribute_query_requires_project_filter_object(self):
        serializer = ObservationAttributeListQuerySerializer(
            data={
                "filters": json.dumps(
                    {"project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f"}
                ),
                "row_type": "traces",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert (
            serializer.validated_data["filters"]["project_id"]
            == "1372e742-a10b-4d98-9ca4-31ef4d67115f"
        )

    def test_observation_attribute_query_rejects_extra_filter_keys(self):
        serializer = ObservationAttributeListQuerySerializer(
            data={
                "filters": json.dumps(
                    {
                        "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                        "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                    }
                )
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_span_observe_list_query_accepts_canonical_filters(self):
        serializer = SpanObserveListQuerySerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "user_id": "customer-1",
                "filters": json.dumps([_span_attr_filter()]),
                "page_number": "1",
                "page_size": "50",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["filters"][0]["column_id"] == "customer_tier"

    def test_span_list_query_rejects_camel_case_aliases(self):
        serializer = SpanListQuerySerializer(
            data={
                "projectVersionId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "pageNumber": "1",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "projectVersionId" in serializer.errors
        assert "pageNumber" in serializer.errors

    def test_span_observe_list_query_rejects_camel_case_aliases(self):
        serializer = SpanObserveListQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "userId": "customer-1",
                "pageNumber": "1",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors
        assert "userId" in serializer.errors
        assert "pageNumber" in serializer.errors

    def test_span_export_query_rejects_camel_case_project_alias(self):
        serializer = SpanExportQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors

    def test_span_index_queries_reject_camel_case_aliases(self):
        span_index = SpanIndexQuerySerializer(
            data={
                "spanId": "span-1",
                "projectVersionId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
            }
        )
        observe_index = SpanObserveIndexQuerySerializer(
            data={
                "spanId": "span-1",
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "userId": "customer-1",
            }
        )

        assert not span_index.is_valid()
        assert not observe_index.is_valid()
        assert "spanId" in span_index.errors
        assert "projectVersionId" in span_index.errors
        assert "spanId" in observe_index.errors
        assert "projectId" in observe_index.errors
        assert "userId" in observe_index.errors

    def test_eval_api_log_table_query_accepts_canonical_filters(self):
        serializer = EvalApiLogTableQuerySerializer(
            data={
                "eval_template_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "page_size": "25",
                "current_page_index": "2",
                "source": "eval_playground",
                "filters": json.dumps([_span_attr_filter()]),
                "sort": json.dumps([{"column_id": "created_at", "type": "descending"}]),
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["current_page_index"] == 2
        assert serializer.validated_data["filters"][0]["column_id"] == "customer_tier"
        assert serializer.validated_data["sort"][0]["type"] == "descending"

    def test_eval_api_log_table_query_rejects_legacy_query_aliases(self):
        serializer = EvalApiLogTableQuerySerializer(
            data={
                "evalTemplateId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "currentPageIndex": "2",
            }
        )

        assert not serializer.is_valid()
        assert "evalTemplateId" in serializer.errors
        assert "currentPageIndex" in serializer.errors

    def test_eval_api_log_table_query_rejects_legacy_filter_aliases(self):
        payload = _span_attr_filter()
        payload["filterConfig"] = payload.pop("filter_config")
        serializer = EvalApiLogTableQuerySerializer(
            data={
                "eval_template_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([payload]),
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_eval_metric_query_and_request_use_canonical_filters(self):
        query_serializer = EvalMetricQuerySerializer(
            data={
                "eval_template_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )
        body_serializer = EvalMetricRequestSerializer(
            data={
                "eval_template_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": [_span_attr_filter()],
            }
        )

        assert query_serializer.is_valid(), query_serializer.errors
        assert body_serializer.is_valid(), body_serializer.errors

    def test_eval_metric_query_and_request_reject_legacy_aliases(self):
        query_serializer = EvalMetricQuerySerializer(
            data={
                "evalTemplateId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
            }
        )
        body_serializer = EvalMetricRequestSerializer(
            data={
                "evalTemplateId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": [_span_attr_filter()],
            }
        )

        assert not query_serializer.is_valid()
        assert not body_serializer.is_valid()
        assert "evalTemplateId" in query_serializer.errors
        assert "evalTemplateId" in body_serializer.errors

    def test_trace_agent_graph_query_rejects_camel_case_project_id(self):
        serializer = TraceAgentGraphQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors

    def test_session_export_query_requires_canonical_project_id(self):
        serializer = TraceSessionExportQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "projectId" in serializer.errors
