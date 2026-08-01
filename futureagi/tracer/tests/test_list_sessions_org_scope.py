"""
Regression tests for the org-scope + user_id code path through
``TraceSessionView._list_sessions_clickhouse``.

Previously the method referenced ``org`` before it was defined — the
identifier was only assigned later in the formatted-result decoration
block. Whenever a ``user_id`` query parameter was set the method
NameError'd on the EndUser lookup, the outer ``try/except`` swallowed
the exception, and the request silently fell through to the PG path
(which then timed out — TH-5092).

These tests pin:
  1. The method completes without raising when ``user_id`` is set in
     org-scope mode (the bug case).
  2. The synthetic ``end_user_id IN (...)`` filter is appended to the
     filter list passed into ``SessionListQueryBuilder``.
  3. End-user display fields are stitched onto the formatted output
     from a single EndUser lookup (no second round-trip).
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest


@pytest.mark.unit
class TestListSessionsClickHouseOrgScope:
    """Direct unit tests for ``_list_sessions_clickhouse``."""

    def _make_request(self, *, user_id=None, query_params=None):
        params = dict(query_params or {})
        if user_id:
            params["user_id"] = user_id
        return SimpleNamespace(
            query_params=params,
            organization=SimpleNamespace(id=uuid.uuid4()),
            user=SimpleNamespace(organization=SimpleNamespace(id=uuid.uuid4())),
        )

    def _make_view(self):
        """Construct a TraceSessionView without invoking ModelViewSet.__init__.

        The view's only attributes we need are ``_gm.success_response`` and
        the methods we're testing. Building one through DRF requires a full
        DB stack, so we fabricate just enough surface area here.
        """
        from tracer.views.trace_session import TraceSessionView

        view = TraceSessionView.__new__(TraceSessionView)
        view._gm = SimpleNamespace(
            success_response=lambda payload: ("ok", payload),
            bad_request=lambda msg: ("bad_request", msg),
        )
        return view

    def _make_validated_data(self, filters=None, sort_params=None):
        return {
            "filters": filters or [],
            "sort_params": sort_params or [],
        }

    def _final_status_preview_data(
        self,
        *,
        start: datetime,
        end: datetime,
        page_number: int = 0,
    ):
        return {
            "filters": [
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [start.isoformat(), end.isoformat()],
                    },
                },
                {
                    "column_id": "final_status",
                    "display_name": "final_status",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["Rechazado"],
                        "col_type": "SPAN_ATTRIBUTE",
                    },
                },
            ],
            "sort_params": [],
            "page_number": page_number,
            "page_size": 10,
            "preview": True,
        }

    @staticmethod
    def _session_row(session_id, when, *, reversed_keys=False):
        items = [
            ("session_id", str(session_id)),
            ("session_start", when),
            ("session_end", when + timedelta(seconds=1)),
            ("duration", 1),
            ("total_cost", 0.25),
            ("total_tokens", 7),
            ("traces_count", 1),
        ]
        return dict(reversed(items) if reversed_keys else items)

    def _patch_endusers(self, ids, *, with_display=True):
        """Patch ``EndUser.objects.filter`` chain so we don't touch the DB."""
        rows = []
        for _id in ids:
            row = {"id": _id}
            if with_display:
                row.update(
                    {
                        "user_id": "user-eve",
                        "user_id_type": "DEVELOPER_IDENTIFIER",
                        "user_id_hash": "deadbeef",
                    }
                )
            rows.append(row)

        chain = mock.MagicMock()
        chain.filter.return_value = chain
        chain.values.return_value = rows
        chain.values_list.return_value = rows
        return mock.patch(
            "tracer.views.trace_session.EndUser.objects.filter",
            return_value=chain,
        )

    def _patch_analytics(self):
        """Stub ``analytics.execute_ch_query`` so build() runs but no CH hit."""
        analytics = mock.MagicMock()
        analytics.execute_ch_query.return_value = SimpleNamespace(data=[])
        return analytics

    def _patch_session_name_lookup(self):
        """Patch CH-backed session name lookup so these unit tests stay local."""
        return mock.patch(
            "tracer.views.trace_session.TraceSessionView._fetch_session_names",
            return_value={},
        )

    def _patch_annotation_labels(self):
        return mock.patch(
            "tracer.views.trace_session.AnnotationsLabels.objects.filter",
            return_value=[],
        )

    def test_runs_without_nameerror_when_user_id_set_org_scope(self):
        """Repro of TH-5092: ``org`` was undefined when ``user_id`` was set.

        The previous code raised ``NameError: name 'org' is not defined``
        at the EndUser lookup, the wrapping ``try/except`` swallowed it,
        and the request silently fell through to the PG path. After the
        fix, the call completes and returns a (mocked) success response.
        """
        view = self._make_view()
        request = self._make_request(user_id="user-eve")
        analytics = self._patch_analytics()

        eu_ids = [str(uuid.uuid4())]
        with self._patch_endusers(eu_ids), self._patch_session_name_lookup():
            status, payload = view._list_sessions_clickhouse(
                request,
                project_id=None,
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(),
                org_project_ids=[str(uuid.uuid4())],
            )

        assert status == "ok"
        # Phase 1 build() and count fast path produce one execute call;
        # absent data the count is inferred without a second CH call.
        assert analytics.execute_ch_query.call_count >= 1

    def test_task_preview_is_one_hard_budgeted_base_query(self):
        """Session preview omits content, exact count, and enrichments."""
        view = self._make_view()
        request = self._make_request()
        analytics = mock.MagicMock()
        now = __import__("datetime").datetime.now()
        analytics.execute_ch_query.return_value = SimpleNamespace(
            data=[
                {
                    "session_id": str(uuid.uuid4()),
                    "session_start": now,
                    "session_end": now,
                    "duration": 0,
                    "total_cost": 0,
                    "total_tokens": 1,
                    "traces_count": 1,
                }
            ]
        )
        project = SimpleNamespace(session_config=None)

        status, payload = view._list_sessions_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            project=project,
            analytics=analytics,
            validated_data={
                "filters": [],
                "sort_params": [],
                "page_number": 0,
                "page_size": 50,
                "preview": True,
            },
            org_project_ids=None,
        )

        assert status == "ok"
        assert len(payload["table"]) == 1
        assert payload["metadata"]["total_rows_is_lower_bound"] is True
        assert analytics.execute_ch_query.call_count == 1
        assert analytics.execute_ch_query.call_args.kwargs["timeout_ms"] == 750
        assert (
            analytics.execute_ch_query.call_args.kwargs["settings"]["max_threads"] == 2
        )

    def test_unfiltered_project_list_uses_bounded_root_frontier(self):
        """Ordinary browsing must not fall back to a whole-window GROUP BY."""

        view = self._make_view()
        request = self._make_request()
        analytics = mock.MagicMock()
        analytics.execute_ch_query.return_value = SimpleNamespace(data=[])
        project = SimpleNamespace(session_config=None)
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(minutes=5)

        with (
            self._patch_session_name_lookup(),
            self._patch_annotation_labels(),
        ):
            status, payload = view._list_sessions_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                project=project,
                analytics=analytics,
                validated_data={
                    "filters": [
                        {
                            "column_id": "created_at",
                            "filter_config": {
                                "filter_type": "datetime",
                                "filter_op": "between",
                                "filter_value": [start.isoformat(), end.isoformat()],
                            },
                        }
                    ],
                    "sort_params": [],
                    "page_number": 0,
                    "page_size": 25,
                },
            )

        assert status == "ok"
        assert payload["table"] == []
        assert any(
            "root_seed_limit" in call.args[1]
            for call in analytics.execute_ch_query.call_args_list
        )
        assert not any(
            "GROUP BY trace_session_id" in call.args[0]
            for call in analytics.execute_ch_query.call_args_list
        )

    def test_final_status_preview_uses_exact_candidates_and_remap_aliases(self):
        """The reported task-selector filter avoids the broad session scan.

        Candidate roots are rechecked at latest state, hydrated, expanded to
        every session-id alias, and only then aggregated. Mapping rows with a
        deliberately different insertion order also pin column-safe formatting.
        """
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2,
        )
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        view = self._make_view()
        request = self._make_request()
        project = SimpleNamespace(session_config=None)
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(hours=1)
        trace_ids = [str(uuid.uuid4()) for _ in range(11)]
        old_ids = [str(uuid.uuid4()) for _ in range(11)]
        new_ids = [str(uuid.uuid4()) for _ in range(11)]
        seed_rows = [
            {"trace_id": trace_id, "start_time": end - timedelta(seconds=i + 1)}
            for i, trace_id in enumerate(trace_ids)
        ]
        session_rows = [
            self._session_row(
                old_id,
                end - timedelta(seconds=i + 1),
                reversed_keys=bool(i % 2),
            )
            for i, old_id in enumerate(old_ids)
        ]
        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = [
            SimpleNamespace(data=seed_rows),
            SimpleNamespace(data=[{"trace_id": trace_id} for trace_id in trace_ids]),
            SimpleNamespace(
                data=[
                    {"trace_id": trace_id, "trace_session_id": new_id}
                    for trace_id, new_id in zip(trace_ids, new_ids, strict=True)
                ]
            ),
            SimpleNamespace(
                data=[
                    {"any_id": alias, "survivor_id": old_id}
                    for old_id, new_id in zip(old_ids, new_ids, strict=True)
                    for alias in (old_id, new_id)
                ]
            ),
            SimpleNamespace(data=session_rows),
        ]

        with mock.patch(
            "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
            side_effect=lambda query_type: (
                SessionListQueryBuilderV2
                if query_type == "SESSION_LIST"
                else TraceListQueryBuilderV2
            ),
        ):
            status, payload = view._list_sessions_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                project=project,
                analytics=analytics,
                validated_data=self._final_status_preview_data(start=start, end=end),
            )

        assert status == "ok"
        assert len(payload["table"]) == 10
        assert payload["metadata"] == {
            "total_rows": 11,
            "total_rows_is_lower_bound": True,
        }
        assert payload["table"][1]["session_id"] == old_ids[1]
        assert payload["table"][1]["total_cost"] == 0.25
        assert payload["table"][1]["total_tokens"] == 7
        candidate_call = analytics.execute_ch_query.call_args_list[4]
        candidate_query = candidate_call.args[0]
        candidate_params = candidate_call.args[1]
        assert set(candidate_params["candidate_session_ids"]) == set(old_ids + new_ids)
        assert "FROM spans FINAL" in candidate_query
        assert "use_skip_indexes_if_final = 0" in candidate_query
        assert candidate_call.kwargs["settings"]["use_skip_indexes_if_final"] == 0
        assert all(
            call.kwargs["timeout_ms"] <= 750
            for call in analytics.execute_ch_query.call_args_list
        )

    def test_final_status_preview_missing_candidate_row_fails_closed(self):
        view = self._make_view()
        request = self._make_request()
        project = SimpleNamespace(session_config=None)
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(minutes=10)
        trace_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = [
            SimpleNamespace(
                data=[{"trace_id": trace_id, "start_time": end - timedelta(seconds=1)}]
            ),
            SimpleNamespace(data=[{"trace_id": trace_id}]),
            SimpleNamespace(
                data=[{"trace_id": trace_id, "trace_session_id": session_id}]
            ),
            SimpleNamespace(data=[]),
            # The exact scoped aggregate omitted a requested canonical id.
            SimpleNamespace(data=[]),
        ]

        status, payload = view._list_sessions_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            project=project,
            analytics=analytics,
            validated_data=self._final_status_preview_data(start=start, end=end),
        )

        assert status == "ok"
        assert payload["table"] == []
        assert payload["metadata"]["query_complete"] is False
        assert payload["metadata"]["query_error_code"] == "read_budget_exceeded"

    def test_final_status_preview_missing_hydration_row_fails_closed(self):
        view = self._make_view()
        request = self._make_request()
        project = SimpleNamespace(session_config=None)
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(minutes=10)
        trace_id = str(uuid.uuid4())
        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = [
            SimpleNamespace(
                data=[{"trace_id": trace_id, "start_time": end - timedelta(seconds=1)}]
            ),
            SimpleNamespace(data=[{"trace_id": trace_id}]),
            # The latest-state matcher returned the trace, but hydration did
            # not. Treat that race/truncation as incomplete, never as empty.
            SimpleNamespace(data=[]),
        ]

        status, payload = view._list_sessions_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            project=project,
            analytics=analytics,
            validated_data=self._final_status_preview_data(start=start, end=end),
        )

        assert status == "ok"
        assert payload["table"] == []
        assert payload["metadata"]["query_complete"] is False
        assert payload["metadata"]["query_error_code"] == "read_budget_exceeded"

    def test_final_status_preview_timeout_is_sanitized_and_never_partial(self):
        view = self._make_view()
        request = self._make_request()
        project = SimpleNamespace(session_config=None)
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(minutes=10)
        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = TimeoutError(
            "Code 159 private ClickHouse stack and query text"
        )

        status, payload = view._list_sessions_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            project=project,
            analytics=analytics,
            validated_data=self._final_status_preview_data(start=start, end=end),
        )

        assert status == "ok"
        assert payload["table"] == []
        assert payload["metadata"]["query_complete"] is False
        assert payload["metadata"]["query_error_code"] == "read_budget_exceeded"
        assert "ClickHouse" not in str(payload)
        assert "Code 159" not in str(payload)

    def test_final_status_preview_rejects_stale_only_seed_match(self):
        """A physical stale hit cannot resurrect a non-matching latest trace."""
        view = self._make_view()
        request = self._make_request()
        project = SimpleNamespace(session_config=None)
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(minutes=10)
        trace_id = str(uuid.uuid4())
        seed_calls = 0

        def _execute(_query, params, **_kwargs):
            nonlocal seed_calls
            if "root_seed_limit" in params:
                seed_calls += 1
                if seed_calls == 1:
                    return SimpleNamespace(
                        data=[
                            {
                                "trace_id": trace_id,
                                "start_time": end - timedelta(seconds=1),
                            }
                        ]
                    )
                return SimpleNamespace(data=[])
            if "candidate_trace_ids" in params:
                return SimpleNamespace(data=[])
            raise AssertionError(f"unexpected query params: {params}")

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute
        status, payload = view._list_sessions_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            project=project,
            analytics=analytics,
            validated_data=self._final_status_preview_data(start=start, end=end),
        )

        assert status == "ok"
        assert payload["table"] == []
        assert seed_calls >= 2

    def test_final_status_preview_requires_strict_lead_at_saturated_frontier(self):
        """Equal timestamps cannot prove a prefix until the keyset tie is read."""
        view = self._make_view()
        request = self._make_request()
        project = SimpleNamespace(session_config=None)
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(minutes=10)
        frontier = end - timedelta(seconds=30)
        trace_ids = [str(uuid.uuid4()) for _ in range(100)]
        session_ids = [str(uuid.uuid4()) for _ in range(11)]
        seed_calls = 0

        def _execute(query, params, **_kwargs):
            nonlocal seed_calls
            if "root_seed_limit" in params:
                seed_calls += 1
                if seed_calls == 1:
                    return SimpleNamespace(
                        data=[
                            {"trace_id": trace_id, "start_time": frontier}
                            for trace_id in trace_ids
                        ]
                    )
                return SimpleNamespace(data=[])
            if "session_ids" in params:
                return SimpleNamespace(data=[])
            if "candidate_session_ids" in params:
                return SimpleNamespace(
                    data=[
                        self._session_row(session_id, frontier)
                        for session_id in session_ids
                    ]
                )
            if "candidate_trace_ids" in params and "latest_trace_session_id" in query:
                return SimpleNamespace(
                    data=[
                        {
                            "trace_id": trace_id,
                            "trace_session_id": session_ids[i % len(session_ids)],
                        }
                        for i, trace_id in enumerate(trace_ids)
                    ]
                )
            if "candidate_trace_ids" in params:
                return SimpleNamespace(
                    data=[{"trace_id": trace_id} for trace_id in trace_ids]
                )
            raise AssertionError(f"unexpected query params: {params}")

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute
        status, payload = view._list_sessions_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            project=project,
            analytics=analytics,
            validated_data=self._final_status_preview_data(start=start, end=end),
        )

        assert status == "ok"
        assert len(payload["table"]) == 10
        assert seed_calls == 2

    def test_final_status_preview_older_full_session_start_scans_to_window_start(self):
        """Full-window aggregation can move ordering behind the root frontier."""
        view = self._make_view()
        request = self._make_request()
        project = SimpleNamespace(session_config=None)
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(minutes=20)
        trace_ids = [str(uuid.uuid4()) for _ in range(11)]
        session_ids = [str(uuid.uuid4()) for _ in range(11)]
        seed_calls = 0

        def _execute(query, params, **_kwargs):
            nonlocal seed_calls
            if "root_seed_limit" in params:
                seed_calls += 1
                if seed_calls == 1:
                    return SimpleNamespace(
                        data=[
                            {
                                "trace_id": trace_id,
                                "start_time": end - timedelta(seconds=i + 1),
                            }
                            for i, trace_id in enumerate(trace_ids)
                        ]
                    )
                return SimpleNamespace(data=[])
            if "session_ids" in params:
                return SimpleNamespace(data=[])
            if "candidate_session_ids" in params:
                return SimpleNamespace(
                    data=[
                        self._session_row(session_id, start)
                        for session_id in session_ids
                    ]
                )
            if "candidate_trace_ids" in params and "latest_trace_session_id" in query:
                return SimpleNamespace(
                    data=[
                        {"trace_id": trace_id, "trace_session_id": session_id}
                        for trace_id, session_id in zip(
                            trace_ids, session_ids, strict=True
                        )
                    ]
                )
            if "candidate_trace_ids" in params:
                return SimpleNamespace(
                    data=[{"trace_id": trace_id} for trace_id in trace_ids]
                )
            raise AssertionError(f"unexpected query params: {params}")

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute
        status, payload = view._list_sessions_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            project=project,
            analytics=analytics,
            validated_data=self._final_status_preview_data(start=start, end=end),
        )

        assert status == "ok"
        assert len(payload["table"]) == 10
        assert seed_calls >= 3

    def test_ordinary_any_span_filter_uses_exact_bounded_deep_page(self):
        """Non-preview pages avoid the wide session/Map scan too.

        The unfiltered root stream establishes ordering, while the candidate
        session query keeps any-span semantics inside the bounded old/new
        session alias set. Page one is sliced only after a three-row exact
        prefix has been proven.
        """
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2,
        )
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        view = self._make_view()
        request = self._make_request()
        project = SimpleNamespace(session_config=None)
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(hours=1)
        trace_ids = [str(uuid.uuid4()) for _ in range(3)]
        session_ids = [str(uuid.uuid4()) for _ in range(3)]
        candidate_query = ""

        def _execute(query, params, **_kwargs):
            nonlocal candidate_query
            if "root_seed_limit" in params:
                # An arbitrary attribute is not a canonical-root prefilter.
                assert "final_status" not in query
                return SimpleNamespace(
                    data=[
                        {
                            "trace_id": trace_id,
                            "start_time": end - timedelta(seconds=index + 1),
                        }
                        for index, trace_id in enumerate(trace_ids)
                    ]
                )
            if "candidate_trace_ids" in params:
                return SimpleNamespace(
                    data=[
                        {"trace_id": trace_id, "trace_session_id": session_id}
                        for trace_id, session_id in zip(
                            trace_ids, session_ids, strict=True
                        )
                    ]
                )
            if "session_ids" in params:
                return SimpleNamespace(data=[])
            if "candidate_session_ids" in params:
                candidate_query = query
                return SimpleNamespace(
                    data=[
                        self._session_row(
                            session_id,
                            end - timedelta(seconds=index + 1),
                        )
                        for index, session_id in enumerate(session_ids)
                    ]
                )
            if "content_session_ids" in params:
                return SimpleNamespace(
                    data=[
                        {
                            "session_id": params["content_session_ids"][0],
                            "first_message": "hello",
                            "last_message": "world",
                        }
                    ]
                )
            if "attr_session_ids" in params:
                return SimpleNamespace(data=[])
            raise AssertionError(f"unexpected query params: {params}")

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start.isoformat(), end.isoformat()],
                },
            },
            {
                "column_id": "customer.segment",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "enterprise",
                },
            },
        ]

        with (
            mock.patch(
                "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
                side_effect=lambda query_type: (
                    SessionListQueryBuilderV2
                    if query_type == "SESSION_LIST"
                    else TraceListQueryBuilderV2
                ),
            ),
            mock.patch.object(view, "_fetch_session_names", return_value={}),
            mock.patch.object(view, "_fetch_end_user_info", return_value={}),
            self._patch_annotation_labels(),
        ):
            status, payload = view._list_sessions_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                project=project,
                analytics=analytics,
                validated_data={
                    "filters": filters,
                    "sort_params": [],
                    "page_number": 1,
                    "page_size": 1,
                },
            )

        assert status == "ok"
        assert [row["session_id"] for row in payload["table"]] == [session_ids[1]]
        assert payload["metadata"] == {
            "total_rows": 3,
            "total_rows_is_lower_bound": True,
        }
        compact_candidate = " ".join(candidate_query.split())
        assert "FROM spans FINAL" in compact_candidate
        assert "trace_id IN (SELECT trace_id FROM spans FINAL" in compact_candidate
        assert "trace_session_id IN %(candidate_session_ids)s" in compact_candidate
        assert "attrs_string['customer.segment']" in compact_candidate
        assert "use_skip_indexes_if_final = 0" in compact_candidate
        assert not any(
            "count(" in call.args[0].lower()
            for call in analytics.execute_ch_query.call_args_list
        )

    @pytest.mark.parametrize(
        ("filter_item", "expected_sql"),
        [
            (
                {
                    "column_id": "total_tokens",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 100,
                    },
                },
                "HAVING total_tokens >",
            ),
            (
                {
                    "column_id": "first_message",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": "literal%_\\needle",
                    },
                },
                "argMin(input, start_time) AS first_message",
            ),
        ],
    )
    def test_candidate_session_query_keeps_aggregate_and_message_filters(
        self, filter_item, expected_sql
    ):
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2,
        )

        candidate_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        builder = SessionListQueryBuilderV2(
            project_id=str(uuid.uuid4()),
            filters=[filter_item],
            candidate_session_ids=candidate_ids,
            page_size=2,
        )
        sql, params = builder.build()
        compact = " ".join(sql.split())

        assert expected_sql in compact
        assert "FROM spans FINAL" in compact
        assert params["candidate_session_ids"] == tuple(candidate_ids)
        assert "ORDER BY session_start DESC, session_id DESC" in compact
        assert "use_skip_indexes_if_final = 0" in compact

    def test_preview_page_after_zero_does_not_use_bounded_prefix_optimization(self):
        view = self._make_view()
        request = self._make_request()
        project = SimpleNamespace(session_config=None)
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(minutes=10)
        session_id = str(uuid.uuid4())
        analytics = mock.MagicMock()
        analytics.execute_ch_query.return_value = SimpleNamespace(
            data=[self._session_row(session_id, end - timedelta(seconds=1))]
        )

        status, payload = view._list_sessions_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            project=project,
            analytics=analytics,
            validated_data=self._final_status_preview_data(
                start=start,
                end=end,
                page_number=1,
            ),
        )

        assert status == "ok"
        assert len(payload["table"]) == 1
        assert analytics.execute_ch_query.call_count == 1
        assert "root_seed_limit" not in analytics.execute_ch_query.call_args.args[1]

    def test_count_timeout_marks_total_as_lower_bound(self):
        view = self._make_view()
        request = self._make_request()
        analytics = mock.MagicMock()
        now = __import__("datetime").datetime.now()
        rows = [
            {
                "session_id": str(uuid.uuid4()),
                "session_start": now,
                "session_end": now,
                "duration": 0,
                "total_cost": 0,
                "total_tokens": 1,
                "traces_count": 1,
            }
            for _ in range(2)
        ]

        def _execute(query, *_args, **_kwargs):
            compact = " ".join(query.split())
            if compact.startswith("SELECT count("):
                raise TimeoutError("Code 159 private ClickHouse details")
            if "LIMIT %(limit)s OFFSET %(offset)s" in compact:
                return SimpleNamespace(data=rows)
            if "argMin(input, start_time) AS first_message" in compact:
                return SimpleNamespace(
                    data=[
                        {
                            "session_id": str(rows[0]["session_id"]),
                            "first_message": "",
                            "last_message": "",
                        }
                    ]
                )
            return SimpleNamespace(data=[])

        analytics.execute_ch_query.side_effect = _execute
        project = SimpleNamespace(session_config=None)

        with (
            mock.patch.object(view, "_fetch_session_names", return_value={}),
            mock.patch.object(view, "_fetch_end_user_info", return_value={}),
            self._patch_annotation_labels(),
        ):
            response_status, payload = view._list_sessions_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                project=project,
                analytics=analytics,
                validated_data={
                    "filters": [],
                    # Custom aggregate sorts intentionally retain the exact
                    # list + count path; ordinary session-start browsing now
                    # uses the bounded root frontier and never issues this
                    # wide count query.
                    "sort_params": [{"column_id": "duration", "direction": "desc"}],
                    "page_number": 0,
                    "page_size": 1,
                },
                org_project_ids=None,
            )

        assert response_status == "ok"
        assert payload["metadata"] == {
            "total_rows": 2,
            "total_rows_is_lower_bound": True,
        }
        assert len(payload["table"]) == 1

    def test_synthetic_end_user_id_filter_is_injected(self):
        """The user_id query param must surface as a synthetic
        ``end_user_id IN (...)`` filter on the builder."""
        # Resolve the real builder class BEFORE patching, so the
        # side_effect can construct a real instance instead of
        # re-entering the mocked symbol (which would recurse forever).
        from tracer.services.clickhouse.query_builders import (
            SessionListQueryBuilder as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request(user_id="user-eve")
        analytics = self._patch_analytics()
        eu_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        captured = {}

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        with (
            self._patch_endusers(eu_ids),
            self._patch_session_name_lookup(),
            mock.patch(
                "tracer.services.clickhouse.query_builders.SessionListQueryBuilder",
                side_effect=_capture_builder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=None,
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(),
                org_project_ids=[str(uuid.uuid4())],
            )

        synthetic = [
            f for f in captured["filters"] if f.get("column_id") == "end_user_id"
        ]
        assert len(synthetic) == 1, (
            f"expected one synthetic end_user_id filter, got: {captured['filters']}"
        )
        cfg = synthetic[0]["filter_config"]
        assert cfg["filter_op"] == "in"
        assert set(cfg["filter_value"]) == {str(_id) for _id in eu_ids}

    def test_user_id_filter_preserves_multi_value_list(self):
        from tracer.services.clickhouse.query_builders import (
            SessionListQueryBuilder as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request()
        analytics = self._patch_analytics()
        captured = {}
        alice_id = str(uuid.uuid4())
        bob_id = str(uuid.uuid4())

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        filters = [
            {
                "column_id": "user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["alice", "bob"],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        with (
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_id",
                side_effect=[([alice_id], None), ([bob_id], None)],
            ) as resolve_mock,
            self._patch_session_name_lookup(),
            self._patch_annotation_labels(),
            mock.patch(
                "tracer.services.clickhouse.query_builders.SessionListQueryBuilder",
                side_effect=_capture_builder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=uuid.uuid4(),
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(filters=filters),
            )

        assert [c.args[0] for c in resolve_mock.call_args_list] == ["alice", "bob"]
        synthetic = [
            f for f in captured["filters"] if f.get("column_id") == "end_user_id"
        ]
        assert len(synthetic) == 1
        cfg = synthetic[0]["filter_config"]
        assert cfg["filter_op"] == "in"
        assert cfg["filter_value"] == [alice_id, bob_id]

    def test_user_id_filter_preserves_negated_operator(self):
        from tracer.services.clickhouse.query_builders import (
            SessionListQueryBuilder as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request()
        analytics = self._patch_analytics()
        captured = {}
        alice_id = str(uuid.uuid4())

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        filters = [
            {
                "column_id": "user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_equals",
                    "filter_value": "alice",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        with (
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_id",
                return_value=([alice_id], None),
            ),
            self._patch_session_name_lookup(),
            self._patch_annotation_labels(),
            mock.patch(
                "tracer.services.clickhouse.query_builders.SessionListQueryBuilder",
                side_effect=_capture_builder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=uuid.uuid4(),
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(filters=filters),
            )

        synthetic = [
            f for f in captured["filters"] if f.get("column_id") == "end_user_id"
        ]
        assert len(synthetic) == 1
        assert synthetic[0]["filter_config"]["filter_op"] == "not_in"
        assert synthetic[0]["filter_config"]["filter_value"] == [alice_id]

    def test_user_id_null_filter_preserves_null_operator_without_resolution(self):
        from tracer.services.clickhouse.query_builders import (
            SessionListQueryBuilder as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request()
        analytics = self._patch_analytics()
        captured = {}

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        filters = [
            {
                "column_id": "user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "is_null",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        with (
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_id"
            ) as resolve_mock,
            self._patch_session_name_lookup(),
            self._patch_annotation_labels(),
            mock.patch(
                "tracer.services.clickhouse.query_builders.SessionListQueryBuilder",
                side_effect=_capture_builder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=uuid.uuid4(),
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(filters=filters),
            )

        resolve_mock.assert_not_called()
        synthetic = [
            f for f in captured["filters"] if f.get("column_id") == "end_user_id"
        ]
        assert len(synthetic) == 1
        cfg = synthetic[0]["filter_config"]
        assert cfg["filter_op"] == "is_null"
        assert "filter_value" not in cfg

    def test_end_user_display_injected_without_extra_db_call(self):
        """When ``user_id`` resolves, the EndUser display fields should be
        injected onto the formatted rows from the SAME query that built
        the synthetic filter — no second EndUser.objects.filter call."""
        view = self._make_view()
        request = self._make_request(user_id="user-eve")
        # Return one synthetic session row from the CH stub so the
        # injection branch actually runs.
        analytics = mock.MagicMock()
        session_row = {
            "session_id": uuid.uuid4(),
            "session_start": None,
            "session_end": None,
            "duration": 0,
            "total_cost": 0,
            "total_tokens": 0,
            "traces_count": 0,
        }
        analytics.execute_ch_query.return_value = SimpleNamespace(data=[session_row])

        eu_ids = [str(uuid.uuid4())]
        with (
            self._patch_endusers(eu_ids) as filter_mock,
            self._patch_session_name_lookup(),
        ):
            status, payload = view._list_sessions_clickhouse(
                request,
                project_id=None,
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(),
                org_project_ids=[str(uuid.uuid4())],
            )

        # Exactly one EndUser query (the consolidated one), not two.
        assert filter_mock.call_count == 1, (
            f"expected 1 EndUser.objects.filter call, got {filter_mock.call_count} — "
            "user-info decoration should reuse the resolved EndUser, "
            "not issue a second query"
        )

        assert status == "ok"
        rows = payload["table"]
        assert rows, "expected at least one formatted row"
        assert rows[0]["user_id"] == "user-eve"
        assert rows[0]["user_id_type"] == "DEVELOPER_IDENTIFIER"
        assert rows[0]["user_id_hash"] == "deadbeef"

    def test_no_user_id_skips_enduser_lookup(self):
        """Absent ``user_id`` must NOT trigger any EndUser query."""
        view = self._make_view()
        request = self._make_request()  # no user_id
        analytics = self._patch_analytics()

        with self._patch_endusers([]) as filter_mock, self._patch_session_name_lookup():
            view._list_sessions_clickhouse(
                request,
                project_id=None,
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(),
                org_project_ids=[str(uuid.uuid4())],
            )

        assert filter_mock.call_count == 0


def test_resolve_session_fields_scopes_multi_project_lookup(monkeypatch):
    from tracer.services.clickhouse.v2 import trace_session_dict_reader

    project_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    fake_client = mock.Mock()
    fake_client.query.return_value = SimpleNamespace(result_rows=[])
    monkeypatch.setattr(
        trace_session_dict_reader,
        "_get_client",
        lambda: fake_client,
    )

    assert (
        trace_session_dict_reader.resolve_session_fields(
            [str(uuid.uuid4())],
            project_ids=project_ids,
        )
        == {}
    )

    query = fake_client.query.call_args.args[0]
    params = fake_client.query.call_args.kwargs["parameters"]
    assert "ts.project_id IN %(pids)s" in query
    assert set(params["pids"]) == set(project_ids)
