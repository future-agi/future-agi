import csv
import io
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status

from tracer.services.clickhouse.query_service import AnalyticsQueryService

pytestmark = [pytest.mark.integration, pytest.mark.api]


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


def _ch_stub(rows):
    return MagicMock(data=rows)


def _row(
    *,
    user_id,
    user_id_type="email",
    user_id_hash="hash",
    activated_at=None,
    last_active=None,
    num_traces=1,
    num_sessions=1,
    avg_session_duration=3.0,
    total_tokens=18,
    total_cost=0.123456,
    avg_trace_latency=300.0,
    num_llm_calls=1,
    num_guardrails_triggered=0,
    bool_eval_pass_rate=0.0,
    input_tokens=11,
    output_tokens=7,
    project_id=None,
    end_user_id=None,
    total_count=1,
):
    return {
        "user_id": user_id,
        "user_id_type": user_id_type,
        "user_id_hash": user_id_hash,
        "activated_at": activated_at or datetime.utcnow(),
        "last_active": last_active,
        "num_traces": num_traces,
        "num_sessions": num_sessions,
        "avg_session_duration": avg_session_duration,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "avg_trace_latency": avg_trace_latency,
        "num_llm_calls": num_llm_calls,
        "num_guardrails_triggered": num_guardrails_triggered,
        "bool_eval_pass_rate": bool_eval_pass_rate,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "project_id": project_id or uuid.uuid4(),
        "end_user_id": end_user_id or uuid.uuid4(),
        "num_active_days": 1,
        "num_traces_with_errors": 0,
        "avg_output_float": 0.0,
        "total_count": total_count,
    }


# Header order is the frontend contract; if the view drifts, these tests catch it.
_EXPECTED_HEADER = [
    "User ID",
    "User ID Type",
    "User ID Hash",
    "First Active",
    "Last Active",
    "No. of Traces",
    "No. of Sessions",
    "Avg Session Duration (s)",
    "Total Tokens",
    "Total Cost ($)",
    "Avg Latency / Trace (ms)",
    "No. of LLM Calls",
    "Guardrails Triggered",
    "Evals Pass Rate (%)",
    "Input Tokens",
    "Output Tokens",
]


def _parse_csv(response):
    body = b"".join(response.streaming_content).decode("utf-8")
    return list(csv.reader(io.StringIO(body)))


class TestUsersExport:
    def test_export_streams_csv_with_correct_headers(
        self, auth_client, organization, workspace, observe_project
    ):
        user_id = "export-happy@example.com"
        now = timezone.now()
        activated = now - timedelta(days=1)
        filters = _date_filters(now - timedelta(hours=1), now + timedelta(hours=1))
        rows = [
            _row(
                user_id=user_id,
                activated_at=activated,
                last_active=now,
                total_tokens=18,
                input_tokens=11,
                output_tokens=7,
                num_traces=1,
                project_id=observe_project.id,
            )
        ]

        with (
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                return_value=_ch_stub(rows),
            ),
        ):
            response = auth_client.get(
                "/tracer/users/",
                {
                    "project_id": str(observe_project.id),
                    "filters": json.dumps(filters),
                    "export": "true",
                },
            )

        assert response.status_code == status.HTTP_200_OK
        # Guards the 60s LB-timeout fix — buffered HttpResponse would regress this.
        assert isinstance(response, StreamingHttpResponse)
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment;" in response["Content-Disposition"]
        assert f"users_{observe_project.id}_" in response["Content-Disposition"]

        csv_rows = _parse_csv(response)
        assert csv_rows[0] == _EXPECTED_HEADER
        data_rows = [r for r in csv_rows[1:] if r]
        target = next(r for r in data_rows if r[0] == user_id)
        assert target[_EXPECTED_HEADER.index("User ID Type")] == "email"
        assert target[_EXPECTED_HEADER.index("Total Tokens")] == "18"
        assert target[_EXPECTED_HEADER.index("Input Tokens")] == "11"
        assert target[_EXPECTED_HEADER.index("Output Tokens")] == "7"
        assert target[_EXPECTED_HEADER.index("No. of Traces")] == "1"
        # Datetimes go through _format_users_export_cell → isoformat().
        assert target[_EXPECTED_HEADER.index("First Active")] == activated.isoformat()
        assert target[_EXPECTED_HEADER.index("Last Active")] == now.isoformat()

    def test_export_requires_authentication(self, api_client, observe_project):
        response = api_client.get(
            "/tracer/users/",
            {"project_id": str(observe_project.id), "export": "true"},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_export_ignores_pagination_params(
        self, auth_client, organization, workspace, observe_project
    ):
        now = timezone.now()
        filters = _date_filters(now - timedelta(hours=1), now + timedelta(hours=1))
        user_ids = [f"export-page-{i}@example.com" for i in range(3)]
        rows = [_row(user_id=uid, project_id=observe_project.id) for uid in user_ids]

        with (
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                return_value=_ch_stub(rows),
            ),
        ):
            response = auth_client.get(
                "/tracer/users/",
                {
                    "project_id": str(observe_project.id),
                    "filters": json.dumps(filters),
                    "export": "true",
                    "page_size": 1,
                    "current_page_index": 2,
                },
            )

        assert response.status_code == status.HTTP_200_OK
        csv_rows = _parse_csv(response)
        emitted = {r[0] for r in csv_rows[1:] if r}
        for uid in user_ids:
            assert uid in emitted

    def test_export_skips_pagination_in_builder_kwargs(
        self, auth_client, organization, workspace, observe_project
    ):
        now = timezone.now()
        filters = _date_filters(now - timedelta(hours=1), now + timedelta(hours=1))

        with (
            patch(
                "tracer.views.trace.UserListQueryBuilder",
                wraps=__import__(
                    "tracer.services.clickhouse.query_builders.user_list",
                    fromlist=["UserListQueryBuilder"],
                ).UserListQueryBuilder,
            ) as builder_cls,
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                return_value=_ch_stub([]),
            ),
        ):
            response = auth_client.get(
                "/tracer/users/",
                {
                    "project_id": str(observe_project.id),
                    "filters": json.dumps(filters),
                    "export": "true",
                    "page_size": 5,
                    "current_page_index": 3,
                },
            )

        assert response.status_code == status.HTTP_200_OK
        builder_cls.assert_called_once()
        kwargs = builder_cls.call_args.kwargs
        assert kwargs["limit"] is None
        assert kwargs["offset"] is None
        assert kwargs["filters"] == filters
        # Post-CH25: workspace isolation rides on project_ids + empty_scope.
        # Project requested IS in the workspace, so empty_scope must be False
        # and project_ids must contain the requested project.
        assert kwargs["project_ids"] == [str(observe_project.id)]
        assert kwargs["empty_scope"] is False

    def test_export_formats_none_cells_as_empty(
        self, auth_client, organization, workspace, observe_project
    ):
        now = timezone.now()
        filters = _date_filters(now - timedelta(hours=1), now + timedelta(hours=1))
        rows = [
            _row(
                user_id="export-idle@example.com",
                last_active=None,
                project_id=observe_project.id,
            )
        ]

        with (
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                return_value=_ch_stub(rows),
            ),
        ):
            response = auth_client.get(
                "/tracer/users/",
                {
                    "project_id": str(observe_project.id),
                    "filters": json.dumps(filters),
                    "export": "true",
                },
            )

        csv_rows = _parse_csv(response)
        data_row = next(r for r in csv_rows[1:] if r)
        assert data_row[_EXPECTED_HEADER.index("Last Active")] == ""

    def test_export_filename_defaults_to_all_when_no_project(
        self, auth_client, organization, workspace, observe_project
    ):
        with (
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                return_value=_ch_stub([]),
            ),
        ):
            response = auth_client.get(
                "/tracer/users/",
                {"export": "true"},
            )

        assert response.status_code == status.HTTP_200_OK
        assert "users_all_" in response["Content-Disposition"]

    def test_export_returns_only_header_when_project_out_of_scope(
        self, auth_client, organization, workspace, observe_project
    ):
        # Random UUID NOT in this user's workspace.
        foreign_project_id = str(uuid.uuid4())

        with (
            patch(
                "tracer.views.trace.UserListQueryBuilder",
                wraps=__import__(
                    "tracer.services.clickhouse.query_builders.user_list",
                    fromlist=["UserListQueryBuilder"],
                ).UserListQueryBuilder,
            ) as builder_cls,
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                return_value=_ch_stub([]),
            ),
        ):
            response = auth_client.get(
                "/tracer/users/",
                {"project_id": foreign_project_id, "export": "true"},
            )

        assert response.status_code == status.HTTP_200_OK
        # Builder must be told the scope is empty so the SQL contracts to "no rows"
        # rather than falling through to an org-wide scan.
        kwargs = builder_cls.call_args.kwargs
        assert kwargs["empty_scope"] is True
        assert kwargs["project_ids"] == []

        # Response is still a valid streamed CSV with just the header row.
        csv_rows = _parse_csv(response)
        assert csv_rows[0] == _EXPECTED_HEADER
        assert [r for r in csv_rows[1:] if r] == []


class TestUserListQueryBuilderUnpaginated:
    def test_unpaginated_query_omits_window_count(self):
        from tracer.services.clickhouse.query_builders.user_list import (
            UserListQueryBuilder,
        )

        builder = UserListQueryBuilder(
            organization_id=str(uuid.uuid4()),
            project_ids=[str(uuid.uuid4())],
            limit=None,
            offset=None,
        )
        query, _ = builder.build()
        assert "count() OVER()" not in query
        assert "LIMIT %(limit)s" not in query
        assert "0 AS total_count" in query

    def test_paginated_query_keeps_window_count(self):
        from tracer.services.clickhouse.query_builders.user_list import (
            UserListQueryBuilder,
        )

        builder = UserListQueryBuilder(
            organization_id=str(uuid.uuid4()),
            project_ids=[str(uuid.uuid4())],
            limit=30,
            offset=0,
        )
        query, _ = builder.build()
        assert "count() OVER() AS total_count" in query
        assert "LIMIT %(limit)s OFFSET %(offset)s" in query
