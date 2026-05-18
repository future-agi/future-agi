import json

from model_hub.serializers.contracts import (
    EvalApiLogTableQuerySerializer,
    EvalMetricQuerySerializer,
    EvalMetricRequestSerializer,
    OptimizeDatasetListQuerySerializer,
    PromptMetricsQuerySerializer,
)
from tracer.serializers.eval_task import EditEvalTaskSerializer
from tracer.serializers.dashboard import DashboardFilterValuesQuerySerializer
from tracer.serializers.observation_span import ObservationAttributeListQuerySerializer
from tracer.serializers.project import (
    ProjectGraphDataQuerySerializer,
    ProjectUserMetricsRequestSerializer,
    ProjectUsersAggregateGraphDataRequestSerializer,
)
from tracer.serializers.trace import (
    TraceAgentGraphQuerySerializer,
    TraceListQuerySerializer,
    UsersQuerySerializer,
)
from tracer.serializers.trace_session import TraceSessionFilterValuesQuerySerializer


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

    def test_eval_task_filters_reject_scalar_observation_type(self):
        serializer = EditEvalTaskSerializer(
            data={
                "edit_type": "edit_rerun",
                "filters": {"observation_type": "llm"},
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

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
                "sort_params": json.dumps([{"columnId": "start_time", "sort": "asc"}]),
            }
        )

        assert not serializer.is_valid()
        assert "project_version_id" in serializer.errors
        assert "sort_params" in serializer.errors

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
        assert "project_id" in serializer.errors

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

    def test_eval_api_log_table_query_accepts_canonical_filters(self):
        serializer = EvalApiLogTableQuerySerializer(
            data={
                "eval_template_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "page_size": "25",
                "current_page_index": "2",
                "source": "eval_playground",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["current_page_index"] == 2
        assert serializer.validated_data["filters"][0]["column_id"] == "customer_tier"

    def test_eval_api_log_table_query_rejects_legacy_filter_and_query_aliases(self):
        payload = _span_attr_filter()
        payload["filterConfig"] = payload.pop("filter_config")
        serializer = EvalApiLogTableQuerySerializer(
            data={
                "evalTemplateId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "currentPageIndex": "2",
                "filters": json.dumps([payload]),
            }
        )

        assert not serializer.is_valid()
        assert "eval_template_id" in serializer.errors
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

    def test_trace_agent_graph_query_rejects_camel_case_project_id(self):
        serializer = TraceAgentGraphQuerySerializer(
            data={
                "projectId": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert not serializer.is_valid()
        assert "project_id" in serializer.errors
