import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from tracer.models.observation_span import EndUser, ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.clickhouse.query_service import AnalyticsQueryService

pytestmark = [pytest.mark.integration, pytest.mark.api]


def _result(response):
    data = response.json()
    return data.get("result", data)


def _date_filters(start, end):
    return [
        {
            "column_id": "start_time",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
            },
        }
    ]


def _create_user_activity(organization, workspace, observe_project):
    end_user = EndUser.objects.create(
        organization=organization,
        workspace=workspace,
        project=observe_project,
        user_id=f"pg-fallback-{uuid.uuid4().hex[:8]}@example.com",
        user_id_type="email",
        user_id_hash="pg-fallback-hash",
    )
    session = TraceSession.objects.create(
        project=observe_project,
        name="PG Fallback Session",
    )
    trace = Trace.objects.create(
        project=observe_project,
        session=session,
        name="PG Fallback Trace",
        input={"message": "hello"},
        output={"message": "world"},
    )
    start = timezone.now() - timedelta(hours=2)
    span = ObservationSpan.objects.create(
        id=f"pg_fallback_span_{uuid.uuid4().hex[:16]}",
        project=observe_project,
        trace=trace,
        end_user=end_user,
        name="PG Fallback LLM Span",
        observation_type="llm",
        start_time=start,
        end_time=start + timedelta(seconds=3),
        latency_ms=300,
        prompt_tokens=11,
        completion_tokens=7,
        total_tokens=18,
        cost=0.123456,
        status="OK",
        span_attributes={"plan": "pro"},
    )
    return end_user, session, trace, span


class TestObserveUserPgFallback:
    def test_users_list_falls_back_to_pg_when_clickhouse_query_fails(
        self, auth_client, organization, workspace, observe_project
    ):
        end_user, _session, _trace, span = _create_user_activity(
            organization, workspace, observe_project
        )
        filters = _date_filters(
            span.start_time - timedelta(hours=1),
            span.start_time + timedelta(hours=1),
        )

        with (
            patch.object(
                AnalyticsQueryService,
                "should_use_clickhouse",
                return_value=True,
            ),
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                side_effect=Exception("clickhouse unavailable"),
            ) as ch_query,
        ):
            response = auth_client.get(
                "/tracer/users/",
                {
                    "project_id": str(observe_project.id),
                    "page_size": 5,
                    "current_page_index": 0,
                    "filters": json.dumps(filters),
                },
            )

        assert response.status_code == status.HTTP_200_OK
        ch_query.assert_called_once()
        rows = _result(response)["table"]
        row = next(item for item in rows if item["end_user_id"] == str(end_user.id))
        assert row["user_id"] == end_user.user_id
        assert row["num_traces"] == 1
        assert row["num_sessions"] == 1
        assert row["total_tokens"] == 18
        assert row["input_tokens"] == 11
        assert row["output_tokens"] == 7
        assert row["num_llm_calls"] == 1

    def test_user_metrics_and_graphs_fall_back_to_pg_when_clickhouse_fails(
        self, auth_client, organization, workspace, observe_project
    ):
        end_user, _session, _trace, span = _create_user_activity(
            organization, workspace, observe_project
        )
        filters = _date_filters(
            span.start_time - timedelta(hours=1),
            span.start_time + timedelta(hours=1),
        )

        with (
            patch.object(
                AnalyticsQueryService,
                "should_use_clickhouse",
                return_value=True,
            ),
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                side_effect=Exception("clickhouse unavailable"),
            ) as ch_query,
        ):
            metrics = auth_client.post(
                "/tracer/project/get_user_metrics/",
                {
                    "project_id": str(observe_project.id),
                    "end_user_id": str(end_user.id),
                    "interval": "day",
                    "filters": filters,
                },
                format="json",
            )
            aggregate_graph = auth_client.post(
                "/tracer/project/get_users_aggregate_graph_data/",
                {
                    "project_id": str(observe_project.id),
                    "interval": "day",
                    "filters": filters,
                    "property": "average",
                    "req_data_config": {
                        "type": "SYSTEM_METRIC",
                        "id": "active_users",
                    },
                },
                format="json",
            )
            detail_graph = auth_client.post(
                (
                    "/tracer/project/get_user_graph_data/"
                    f"?project_id={observe_project.id}&end_user_id={end_user.id}"
                ),
                {"interval": "day", "filters": filters},
                format="json",
            )

        assert metrics.status_code == status.HTTP_200_OK
        assert aggregate_graph.status_code == status.HTTP_200_OK
        assert detail_graph.status_code == status.HTTP_200_OK
        assert ch_query.call_count == 3

        metric_rows = _result(metrics)
        assert metric_rows[0]["user_id"] == end_user.user_id
        assert metric_rows[0]["total_tokens"] == 18
        assert metric_rows[0]["num_sessions"] == 1

        aggregate_points = _result(aggregate_graph)["data"]
        assert any(point["value"] == 1 for point in aggregate_points)
        assert any(point["primary_traffic"] == 1 for point in aggregate_points)

        detail = _result(detail_graph)
        for key in ("session", "trace", "cost", "input_tokens", "output_tokens"):
            assert isinstance(detail[key], list)
        assert any(point["session"] == 1 for point in detail["session"])
        assert any(point["trace"] == 1 for point in detail["trace"])
        assert any(point["input_tokens"] == 11 for point in detail["input_tokens"])
        assert any(point["output_tokens"] == 7 for point in detail["output_tokens"])
