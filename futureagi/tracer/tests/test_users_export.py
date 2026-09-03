import csv
import io
import json
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from django.http import StreamingHttpResponse
from rest_framework import status

from tracer.serializers.trace import UsersTableRowSerializer
from tracer.services.clickhouse.query_builders.user_list import (
    UnsupportedBoundedUserListQuery,
    UserListQueryBuilder,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.users_list_manager import (
    USER_EXPORT_PAGE_SIZE,
    USER_LIST_QUERY_TIMEOUT_MS,
    USER_LIST_WALL_DEADLINE_MS,
    USERS_EXPORT_COLUMNS,
    UserCursorRead,
    UsersListManager,
)

pytestmark = [pytest.mark.integration, pytest.mark.api]


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
    def test_export_preserves_the_bounded_streaming_csv_contract(
        self, auth_client, organization, workspace, observe_project
    ):
        cursor_read = MagicMock(spec=UserCursorRead)
        cursor_read.payload = {"table": [{"user_id": "user-1"}]}
        with patch.object(
            UsersListManager,
            "iter_export_csv",
            return_value=iter(["User ID\r\n", "user-1\r\n"]),
        ) as export_csv, patch.object(
            UsersListManager,
            "list_cursor_payload",
            return_value=cursor_read,
        ) as cursor_page:
            response = auth_client.get(
                "/tracer/users/",
                {
                    "project_id": str(observe_project.id),
                    "export": "true",
                },
            )
            body = b"".join(response.streaming_content).decode("utf-8")

        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response, StreamingHttpResponse)
        assert response["Content-Type"].startswith("text/csv")
        assert response["Content-Disposition"] == "attachment"
        assert body == "User ID\r\nuser-1\r\n"
        cursor_page.assert_called_once_with(
            page_size=USER_EXPORT_PAGE_SIZE,
            cursor=None,
        )
        export_csv.assert_called_once_with(cursor_read=cursor_read)

    def test_export_read_failure_is_a_typed_503_before_csv_starts(
        self, auth_client, organization, workspace, observe_project
    ):
        with patch.object(
            UsersListManager,
            "list_cursor_payload",
            side_effect=ReadDeadlineExceeded("read deadline exceeded"),
        ), patch.object(UsersListManager, "iter_export_csv") as export_csv:
            response = auth_client.get(
                "/tracer/users/",
                {
                    "project_id": str(observe_project.id),
                    "export": "true",
                },
            )

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response["Content-Type"].startswith("application/json")
        assert response.json()["code"] == "service_unavailable"
        export_csv.assert_not_called()

    def test_sorted_export_is_rejected_before_cursor_read(
        self, auth_client, organization, workspace, observe_project
    ):
        sort_params = [{"column_id": "num_traces", "direction": "desc"}]
        with patch.object(
            UsersListManager,
            "list_cursor_payload",
        ) as cursor_page:
            response = auth_client.get(
                "/tracer/users/",
                {
                    "project_id": str(observe_project.id),
                    "export": "true",
                    "sort_params": json.dumps(sort_params),
                },
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json()["code"] == "cursor_sort_unsupported"
        cursor_page.assert_not_called()

    def test_export_requires_authentication(self, api_client, observe_project):
        response = api_client.get(
            "/tracer/users/",
            {"project_id": str(observe_project.id), "export": "true"},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_supported_sort_uses_the_bounded_numbered_list_path(
        self, auth_client, organization, workspace, observe_project
    ):
        payload = {"table": [], "total_count": 0, "total_pages": 0}
        sort_params = [{"column_id": "num_traces", "direction": "desc"}]
        with patch("tracer.views.trace.UsersListManager") as manager_cls:
            manager_cls.return_value.list_payload.return_value = payload
            response = auth_client.get(
                "/tracer/users/",
                {
                    "project_id": str(observe_project.id),
                    "sort_params": json.dumps(sort_params),
                },
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == payload
        assert manager_cls.call_args.kwargs["sort_params"] == sort_params
        manager_cls.return_value.list_payload.assert_called_once_with(
            page_size=30, current_page=0
        )

    def test_derived_sort_remains_a_typed_422(
        self, auth_client, organization, workspace, observe_project
    ):
        sort_params = [{"column_id": "num_sessions", "direction": "desc"}]
        builder = UserListQueryBuilder(
            organization_id=str(organization.id),
            project_ids=[str(observe_project.id)],
            sort_params=sort_params,
        )
        assert builder.supports_candidate_first_page() is False

        with patch.object(
            UsersListManager,
            "list_payload",
            side_effect=UnsupportedBoundedUserListQuery("bounded sort unavailable"),
        ):
            response = auth_client.get(
                "/tracer/users/",
                {
                    "project_id": str(observe_project.id),
                    "sort_params": json.dumps(sort_params),
                },
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json()["code"] == "user_sort_unsupported"

    def test_cursor_sort_is_rejected_before_any_clickhouse_read(
        self, auth_client, organization, workspace, observe_project
    ):
        with patch.object(AnalyticsQueryService, "execute_ch_query") as execute_query:
            response = auth_client.get(
                "/tracer/users/",
                {
                    "project_id": str(observe_project.id),
                    "cursor_mode": "true",
                    "sort_params": json.dumps(
                        [{"column_id": "num_traces", "direction": "desc"}]
                    ),
                },
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json()["code"] == "cursor_sort_unsupported"
        execute_query.assert_not_called()

    def test_users_endpoint_documents_bounded_failures(self):
        from tracer.views.trace import UsersView

        responses = UsersView.get._swagger_auto_schema["responses"]
        assert {422, 503} <= responses.keys()


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

    def test_explicit_scan_cap_limits_without_window_count(self):
        from tracer.services.clickhouse.query_builders.user_list import (
            UserListQueryBuilder,
        )

        builder = UserListQueryBuilder(
            organization_id=str(uuid.uuid4()),
            project_ids=[str(uuid.uuid4())],
            limit=None,
            offset=None,
            max_rows=10_000,
        )
        query, params = builder.build()
        # A caller-owned scan cap applies LIMIT without materializing a window
        # count. The synchronous Users export no longer uses this broad path.
        assert "LIMIT %(max_rows)s" in query
        assert "count() OVER()" not in query
        assert params["max_rows"] == 10_000


class TestUsersExportStreaming:
    """Manager-level serialization behaviour (no HTTP / no ClickHouse)."""

    @staticmethod
    def _manager(*, requested_columns=None, attribute_keys=None):
        pid = str(uuid.uuid4())
        return UsersListManager(
            organization_id=str(uuid.uuid4()),
            allowed_project_ids=[pid],
            project_id=pid,
            requested_columns=requested_columns or [],
            attribute_keys=attribute_keys or [],
        )

    @staticmethod
    def _cursor_read(*, rows, **metadata):
        payload = {
            "table": rows,
            "has_more": False,
            "count_is_lower_bound": False,
            "query_complete": True,
            "query_exact": True,
            "ordering_exact": True,
            "approximate_fields": [],
            **metadata,
        }
        return UserCursorRead(
            payload=payload,
            window_start=datetime.utcnow(),
            window_end=datetime.utcnow(),
            checkpoint_order=None,
            seen_rows=len(rows),
            has_more=bool(payload["has_more"]),
            unseen_row_proven=False,
        )

    def test_export_serializes_only_the_pre_materialized_cursor_page(self):
        manager = self._manager()
        cursor_read = self._cursor_read(rows=[{"user_id": "user-1"}])
        with (
            patch.object(UsersListManager, "_fetch_rows") as broad_fetch,
            patch.object(UsersListManager, "list_cursor_payload") as cursor_fetch,
        ):
            body = "".join(manager.iter_export_csv(cursor_read=cursor_read))

        rows = [row for row in csv.reader(io.StringIO(body)) if row]
        assert rows[0] == [header for header, _ in USERS_EXPORT_COLUMNS]
        assert rows[1][0] == "user-1"
        assert len(rows) == 2
        broad_fetch.assert_not_called()
        cursor_fetch.assert_not_called()

    def test_export_marks_bounded_inexact_and_approximate_page(self):
        manager = self._manager()
        page_rows = [
            {"user_id": f"u{i}", "end_user_id": uuid.uuid4()}
            for i in range(USER_EXPORT_PAGE_SIZE)
        ]
        cursor_read = self._cursor_read(
            rows=page_rows,
            has_more=True,
            count_is_lower_bound=True,
            query_exact=False,
            ordering_exact=False,
            approximate_fields=["num_sessions"],
        )
        body = "".join(manager.iter_export_csv(cursor_read=cursor_read))

        rows = [r for r in csv.reader(io.StringIO(body)) if r]
        data_rows = rows[1:]  # drop header
        marker = data_rows[-1]
        assert marker == [
            f"# export truncated after {USER_EXPORT_PAGE_SIZE} rows; "
            "refine filters to export a complete bounded page; "
            "candidate membership or ordering is inexact; "
            "approximate fields: num_sessions"
        ]
        assert len(data_rows[:-1]) == USER_EXPORT_PAGE_SIZE

    def test_export_keeps_formula_guard_and_fixed_columns(self):
        manager = self._manager()
        cursor_read = self._cursor_read(
            rows=[{"user_id": "=HYPERLINK(\"https://invalid\")"}]
        )

        body = "".join(manager.iter_export_csv(cursor_read=cursor_read))
        rows = list(csv.reader(io.StringIO(body)))

        assert rows[0] == [header for header, _ in USERS_EXPORT_COLUMNS]
        assert rows[1][0] == "'=HYPERLINK(\"https://invalid\")"
        assert len(rows[1]) == len(USERS_EXPORT_COLUMNS)

    def test_list_enrichment_programming_defect_is_not_hidden(self):
        # Arbitrary runtime defects must reach the sanitized HTTP boundary,
        # never masquerade as a successful partially enriched user page.
        manager = self._manager(attribute_keys=["final_status"])
        base_rows = [{"user_id": "u1", "end_user_id": uuid.uuid4()}]
        with (
            patch.object(
                UsersListManager,
                "_fetch_rows",
                return_value=(base_rows, 1, MagicMock()),
            ),
            patch.object(UsersListManager, "_read_page_metrics", return_value={}),
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                side_effect=RuntimeError("attr query down"),
            ),
        ):
            with pytest.raises(RuntimeError, match="attr query down"):
                manager.list_payload(page_size=30, current_page=0)

    def test_list_clickhouse_reads_share_deadline_and_have_hard_caps(self):
        manager = self._manager()
        base_row = _row(user_id="u1", end_user_id=uuid.uuid4(), total_count=1)

        def execute(query, _params, *, timeout_ms, settings):
            if "attributes_extra AS attributes_extra" in query:
                return _ch_stub([])
            if "bool_eval_pass_rate" in query and "tracer_eval_logger" in query:
                return _ch_stub([])
            return _ch_stub([base_row])

        with patch.object(
            AnalyticsQueryService,
            "execute_ch_query",
            side_effect=execute,
        ) as execute_mock:
            payload = manager.list_payload(page_size=30, current_page=0)

        assert payload["total_count"] == 1
        assert payload["table"][0]["user_id"] == "u1"
        # With no optional projection only the conservative physical-span
        # presence proof and exact base page run. Optional metric/attribute/eval
        # reads are demand-driven and must not consume the shared deadline.
        assert execute_mock.call_count == 2
        for call in execute_mock.call_args_list:
            assert 0 < call.kwargs["timeout_ms"] <= USER_LIST_QUERY_TIMEOUT_MS
            assert call.kwargs["timeout_ms"] <= USER_LIST_WALL_DEADLINE_MS
            settings = call.kwargs["settings"]
            assert "max_rows_to_read" not in settings
            assert settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
            assert settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
            assert settings["max_result_rows"] > 0
            assert settings["max_result_bytes"] == 32 * 1024 * 1024
            assert settings["result_overflow_mode"] == "throw"

    def test_export_columns_match_serializer_fields(self):
        # The CSV columns must stay a subset of the JSON contract's serializer
        # fields, so the export can't silently drift from the list response.
        serializer_fields = set(UsersTableRowSerializer().fields.keys())
        export_fields = {field for _, field in USERS_EXPORT_COLUMNS}
        missing = export_fields - serializer_fields
        assert not missing, f"export columns not on serializer: {missing}"
