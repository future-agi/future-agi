"""Regression test for TH-5574 — Trace View selection counts off by one.

``TraceListQueryBuilder.build()`` fetches ``page_size + 1`` rows per page as a
has-more sentinel (asserted in ``test_trace_list_ch.py``). The consuming views
must trim that sentinel back to ``page_size`` before building the response,
otherwise a page returns one extra trace — the off-by-one the user saw when
"select all on this page" reported 26 selections for a 25-row page.

This pins the trim in ``TraceView._list_traces_of_session_clickhouse`` (the
``list_traces_of_session`` endpoint named in the ticket).
"""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest


@pytest.mark.unit
class TestTracesOfSessionPagination:
    def test_span_attribute_scan_routing(self):
        from tracer.services.clickhouse.query_service import (
            GUARANTEED_ROOT_SPAN_ATTRIBUTE_TYPES,
        )
        from tracer.views.trace import (
            _candidate_seed_filters,
            _has_only_time_filters,
            _requires_candidate_filter_scan,
            _requires_root_attribute_slice_scan,
        )

        for filter_type, value in (
            ("text", "completed"),
            ("number", 42),
            ("boolean", True),
        ):
            assert _requires_candidate_filter_scan(
                [
                    {
                        "column_id": f"arbitrary_{filter_type}",
                        "filter_config": {
                            "col_type": "SPAN_ATTRIBUTE",
                            "filter_type": filter_type,
                            "filter_op": "equals",
                            "filter_value": value,
                        },
                    }
                ]
            )

        for root_attribute in GUARANTEED_ROOT_SPAN_ATTRIBUTE_TYPES:
            root_filter = {
                "column_id": root_attribute,
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "completed",
                },
            }
            assert not _requires_candidate_filter_scan([root_filter])
            assert _requires_root_attribute_slice_scan([root_filter])

        assert not _requires_candidate_filter_scan(
            [
                {
                    "column_id": "latency",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 10,
                    },
                }
            ]
        )
        session_filter = {
            "column_id": "trace_session_id",
            "filter_config": {
                "col_type": "NORMAL",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": str(uuid.uuid4()),
            },
        }
        assert not _requires_candidate_filter_scan([session_filter])
        assert _candidate_seed_filters(
            [
                session_filter,
                {
                    "column_id": "arbitrary_text",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "value",
                    },
                },
            ]
        ) == [session_filter]
        assert _has_only_time_filters([])
        assert _has_only_time_filters(
            [
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": ["2026-07-24", "2026-07-31"],
                    },
                }
            ]
        )
        assert not _has_only_time_filters([session_filter])

    def test_candidate_seed_keeps_direct_root_conjuncts(self):
        from tracer.views.trace import _candidate_seed_filters

        root_filters = [
            {
                "column_id": "latency",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 100,
                },
            },
            {
                "column_id": "tag",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "production",
                },
            },
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "completed",
                },
            },
        ]
        final_status_filter = root_filters.pop()
        expensive_attr = {
            "column_id": "arbitrary.customer.field",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "contains",
                "filter_value": "needle",
            },
        }

        assert (
            _candidate_seed_filters(
                [*root_filters, final_status_filter, expensive_attr]
            )
            == root_filters
        )

        child_membership_status = {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "ERROR",
            },
        }
        assert (
            _candidate_seed_filters(
                [*root_filters, child_membership_status, expensive_attr]
            )
            == root_filters
        )

    def _make_view(self):
        from tracer.views.trace import TraceView

        view = TraceView.__new__(TraceView)
        view._gm = SimpleNamespace(
            success_response=lambda payload: ("ok", payload),
            bad_request=lambda msg: ("bad_request", msg),
        )
        return view

    def _make_request(self, *, page_size):
        org = SimpleNamespace(id=uuid.uuid4())
        return SimpleNamespace(
            query_params={"page_number": "0", "page_size": str(page_size)},
            organization=org,
            user=SimpleNamespace(organization=org),
        )

    @staticmethod
    def _candidate_filters(start, end):
        return [
            {
                "column_id": "arbitrary_customer_attribute",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "matched",
                },
            },
            {
                "column_id": "start_time",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start, end],
                },
            },
        ]

    @staticmethod
    def _trace_row(trace_id, start_time):
        return {
            "trace_id": str(trace_id),
            "project_id": str(uuid.uuid4()),
            "start_time": start_time,
            "trace_name": f"trace-{trace_id}",
            "observation_type": "llm",
            "status": "OK",
            "latency_ms": 1,
            "total_tokens": 1,
            "cost": 0,
        }

    def _routing_analytics(self, *, trace_rows, total, content_rows=None):
        """Stub ``execute_ch_query`` routing by SQL so build() runs but no CH hit.

        Phase-1 trace query returns ``trace_rows`` (page_size + 1 of them); the
        count query returns ``total``; content defaults to one row per trace.
        """

        if content_rows is None:
            content_rows = trace_rows

        def _side_effect(query, params=None, **kwargs):
            q = query
            params = params or {}
            if " AS total" in q and (
                "uniq(trace_id)" in q or "trace_count_rollup" in q
            ):
                return SimpleNamespace(data=[{"total": total}])
            if "candidate_trace_ids" in params:
                by_id = {str(row["trace_id"]): row for row in trace_rows}
                return SimpleNamespace(
                    data=[
                        by_id[str(trace_id)]
                        for trace_id in params["candidate_trace_ids"]
                        if str(trace_id) in by_id
                    ]
                )
            if "content_trace_ids" in params:
                by_id = {str(row["trace_id"]): row for row in content_rows}
                return SimpleNamespace(
                    data=[
                        by_id[str(trace_id)]
                        for trace_id in params["content_trace_ids"]
                        if str(trace_id) in by_id
                    ]
                )
            # Phase-1 paginated trace list (full row or narrow candidate seed).
            if (
                "ORDER BY start_time DESC" in q
                and "uniq(" not in q
                and "AS cid" not in q
            ):
                return SimpleNamespace(data=list(trace_rows))
            return SimpleNamespace(data=[])

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _side_effect
        return analytics

    def test_page_trimmed_to_page_size(self):
        """A page that fetched page_size + 1 rows returns exactly page_size."""
        page_size = 25
        view = self._make_view()
        request = self._make_request(page_size=page_size)

        # build() asks for page_size + 1 rows for has-more detection.
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(page_size + 1)]
        total = 40
        analytics = self._routing_analytics(trace_rows=trace_rows, total=total)

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
        ):
            # No eval configs for this project → discovery short-circuits with
            # candidate_ids == [] (no PG/CH eval round-trip). This test pins the
            # pagination trim, not eval columns.
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                # Pagination now comes from the serializer-validated query data
                # (request.validated_query_data), not request.query_params.
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": page_size,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        # The sentinel row must be trimmed — exactly page_size, not page_size + 1.
        assert len(payload["table"]) == page_size
        # A date-only request outside rollup coverage must not run the broad raw
        # count. The proven prefix is returned as an honest lower bound.
        assert payload["metadata"]["total_rows"] == page_size + 1
        assert payload["metadata"]["total_rows_is_lower_bound"] is True

    def test_task_preview_uses_two_hard_budgeted_prefix_queries(self):
        """Trace task preview seeds exact root IDs, hydrates, then skips enrichment."""
        view = self._make_view()
        request = self._make_request(page_size=50)
        now = __import__("datetime").datetime.now()
        trace_rows = [
            {
                "trace_id": str(uuid.uuid4()),
                "project_id": str(uuid.uuid4()),
                "start_time": now - timedelta(microseconds=idx),
                "trace_name": f"trace-{idx}",
                "observation_type": "llm",
                "status": "OK",
                "latency_ms": 1,
                "total_tokens": 1,
                "cost": 0,
            }
            for idx in range(12)
        ]
        analytics = self._routing_analytics(trace_rows=trace_rows, total=999)

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project"
            ) as mock_labels,
        ):
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": 50,
                    "preview": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert len(payload["table"]) == 10
        assert payload["metadata"]["total_rows_is_lower_bound"] is True
        assert analytics.execute_ch_query.call_count == 2
        seed_call, hydrate_call = analytics.execute_ch_query.call_args_list
        assert "FROM spans" in seed_call.args[0]
        assert "start_time" in seed_call.args[0]
        assert "FROM traces FINAL" not in seed_call.args[0]
        assert seed_call.kwargs["timeout_ms"] <= 750
        assert hydrate_call.kwargs["timeout_ms"] <= 750
        assert "candidate_trace_ids" in hydrate_call.args[1]
        seed_select = seed_call.args[0].split("FROM", 1)[0]
        assert "trace_id" in seed_select
        assert "start_time" in seed_select
        assert "trace_name" not in seed_select
        assert "prompt_tokens" not in seed_select
        assert seed_call.kwargs["settings"]["max_threads"] == 1
        assert seed_call.kwargs["settings"]["max_block_size"] == 8192
        assert hydrate_call.kwargs["settings"]["max_threads"] == 1
        assert hydrate_call.kwargs["settings"]["max_block_size"] == 8192
        mock_cfg.objects.filter.assert_not_called()
        mock_labels.assert_not_called()

    def test_guaranteed_root_attribute_preview_uses_bounded_root_slice(self):
        from tracer.services.clickhouse.query_service import (
            GUARANTEED_ROOT_SPAN_ATTRIBUTE_TYPES,
        )
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        view = self._make_view()
        request = self._make_request(page_size=10)
        now = datetime(2026, 7, 30, 12)
        rows = [
            self._trace_row(uuid.uuid4(), now - timedelta(seconds=index))
            for index in range(30)
        ]
        calls = []

        def _execute(query, params=None, **kwargs):
            params = dict(params or {})
            calls.append((query, params, kwargs))
            if "candidate_trace_ids" not in params:
                return SimpleNamespace(data=list(rows))
            by_id = {row["trace_id"]: row for row in rows}
            return SimpleNamespace(
                data=[
                    by_id[str(trace_id)]
                    for trace_id in params["candidate_trace_ids"]
                    if str(trace_id) in by_id
                ]
            )

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute
        root_attribute = next(iter(GUARANTEED_ROOT_SPAN_ATTRIBUTE_TYPES))

        with mock.patch(
            "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
            return_value=TraceListQueryBuilderV2,
        ):
            with mock.patch("tracer.views.trace.timezone.now", return_value=now):
                status, payload = view._list_traces_of_session_clickhouse(
                    request,
                    project_id=str(uuid.uuid4()),
                    validated_data={
                        "filters": [
                            {
                                "column_id": root_attribute,
                                "filter_config": {
                                    "col_type": "SPAN_ATTRIBUTE",
                                    "filter_type": "text",
                                    "filter_op": "equals",
                                    "filter_value": "completed",
                                },
                            },
                            {
                                "column_id": "start_time",
                                "filter_config": {
                                    "col_type": "SYSTEM_METRIC",
                                    "filter_type": "datetime",
                                    "filter_op": "between",
                                    "filter_value": [
                                        now - timedelta(days=7),
                                        now,
                                    ],
                                },
                            },
                        ],
                        "page_number": 0,
                        "page_size": 10,
                        "preview": True,
                    },
                    analytics=analytics,
                    org_project_ids=None,
                    org=request.organization,
                )

        assert status == "ok"
        assert len(payload["table"]) == 10
        assert [row["trace_id"] for row in payload["table"]] == [
            row["trace_id"] for row in rows[:10]
        ]
        assert payload["metadata"]["total_rows_is_lower_bound"] is True
        assert payload["metadata"].get("query_complete", True) is True
        assert len(calls) == 3

        seed_query, seed_params, seed_kwargs = calls[0]
        assert "candidate_trace_ids" not in seed_params
        assert seed_params["start_date"] == now - timedelta(days=7)
        assert seed_params["end_date"] == now
        assert f"mapContains(attrs_string, '{root_attribute}')" in seed_query
        assert "argMax(" not in seed_query
        assert "FINAL" not in seed_query
        assert "trace_id IN (SELECT trace_id FROM spans" not in seed_query
        assert seed_kwargs["timeout_ms"] <= 750
        assert seed_kwargs["settings"]["max_threads"] == 1
        assert seed_kwargs["settings"]["max_block_size"] == 8192

        probe_query, probe_params, probe_kwargs = calls[1]
        assert len(probe_params["candidate_trace_ids"]) == len(rows)
        assert f"mapContains(attrs_string, '{root_attribute}')" in probe_query
        assert "argMax(" in probe_query
        assert "FINAL" not in probe_query
        assert "trace_id IN (SELECT trace_id FROM spans" not in probe_query
        assert probe_kwargs["timeout_ms"] <= 750

        hydrate_query, hydrate_params, hydrate_kwargs = calls[2]
        assert (
            hydrate_params["candidate_trace_ids"] == probe_params["candidate_trace_ids"]
        )
        assert "grouped_trace_id" in hydrate_query
        assert "FINAL" not in hydrate_query
        assert hydrate_kwargs["timeout_ms"] <= 750

    def test_guaranteed_root_attribute_slice_timeout_is_honestly_degraded(self):
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        view = self._make_view()
        request = self._make_request(page_size=10)
        now = datetime(2026, 7, 30, 12)
        calls = []

        def _execute(query, params=None, **kwargs):
            calls.append((query, dict(params or {}), kwargs))
            raise TimeoutError("bounded root slice exceeded its read budget")

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute
        with (
            mock.patch(
                "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
                return_value=TraceListQueryBuilderV2,
            ),
            mock.patch("tracer.views.trace.timezone.now", return_value=now),
        ):
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": [
                        {
                            "column_id": "final_status",
                            "filter_config": {
                                "col_type": "SPAN_ATTRIBUTE",
                                "filter_type": "text",
                                "filter_op": "equals",
                                "filter_value": "completed",
                            },
                        },
                        {
                            "column_id": "start_time",
                            "filter_config": {
                                "col_type": "SYSTEM_METRIC",
                                "filter_type": "datetime",
                                "filter_op": "between",
                                "filter_value": [
                                    now - timedelta(days=7),
                                    now,
                                ],
                            },
                        },
                    ],
                    "page_number": 0,
                    "page_size": 10,
                    "preview": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert payload["table"] == []
        assert payload["metadata"]["total_rows_is_lower_bound"] is True
        assert payload["metadata"]["query_complete"] is False
        assert payload["metadata"]["query_status"] == "degraded"
        assert payload["metadata"]["query_error_code"] == "read_budget_exceeded"
        assert len(calls) == 1
        query, params, kwargs = calls[0]
        assert params["start_date"] == now - timedelta(days=7)
        assert params["end_date"] == now
        assert "mapContains(attrs_string, 'final_status')" in query
        assert "argMax(" not in query
        assert "FINAL" not in query
        assert "trace_id IN (SELECT trace_id FROM spans" not in query
        assert kwargs["timeout_ms"] <= 750

    def test_text_filter_preview_uses_bounded_trace_id_candidates(self):
        """A generic string attribute never builds a project-wide membership set."""
        view = self._make_view()
        request = self._make_request(page_size=50)
        now = __import__("datetime").datetime.now()
        candidate_rows = [
            {
                "trace_id": str(uuid.uuid4()),
                "project_id": str(uuid.uuid4()),
                "start_time": now - timedelta(microseconds=idx),
                "trace_name": f"trace-{idx}",
                "observation_type": "llm",
                "status": "OK",
                "latency_ms": 1,
                "total_tokens": 1,
                "cost": 0,
            }
            for idx in range(50)
        ]

        calls = []

        def _execute(query, params=None, **kwargs):
            calls.append((query, dict(params or {}), kwargs))
            if "candidate_trace_ids" not in (params or {}):
                assert "mapContains" not in query
                return SimpleNamespace(data=list(candidate_rows))

            ids = list(params["candidate_trace_ids"])
            if "latest_trace_name AS trace_name" not in query:
                assert len(ids) == 50
                assert query.count("candidate_trace_ids") >= 1
                assert "argMax(" in query
                assert "argMax(tuple(trace_session_id)" in query
                assert "FINAL" not in query
                return SimpleNamespace(
                    data=[{"trace_id": trace_id} for trace_id in ids[:25]]
                )
            assert len(ids) == 25
            by_id = {row["trace_id"]: row for row in candidate_rows}
            # Deliberately reverse the CH response. The view must restore the
            # authoritative newest-root candidate order before paginating.
            return SimpleNamespace(
                data=[by_id[trace_id] for trace_id in reversed(ids[:25])]
            )

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute
        session_id = uuid.uuid4()
        text_filter = {
            "column_id": "prompt_slug",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "synthetic_prompt_v2",
            },
        }

        status, payload = view._list_traces_of_session_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            validated_data={
                "filters": [text_filter],
                "page_number": 0,
                "page_size": 50,
                "preview": True,
                "session_id": session_id,
            },
            analytics=analytics,
            org_project_ids=None,
            org=request.organization,
        )

        assert status == "ok"
        assert len(payload["table"]) == 10
        assert [row["trace_id"] for row in payload["table"]] == [
            row["trace_id"] for row in candidate_rows[:10]
        ]
        assert len(calls) == 3
        # The projection seed is intentionally filter-free; mutable session
        # membership is checked by the full-window scalar latest-state probe.
        assert "trace_session_id" not in calls[0][0]
        assert "trace_session_id" in calls[1][0]
        assert str(session_id) in calls[1][1].values()
        assert calls[0][2]["settings"]["max_memory_usage"] == 268_435_456
        assert calls[1][2]["timeout_ms"] <= 750
        assert calls[2][2]["timeout_ms"] <= 750

    def test_mixed_attribute_and_trace_tag_filter_uses_candidate_classifier(self):
        """An unsupported mixed shape must not fall back to ``spans FINAL``."""
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        view = self._make_view()
        request = self._make_request(page_size=10)
        analytics = mock.MagicMock()
        filters = [
            {
                "column_id": "arbitrary_customer_attribute",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "matched",
                },
            },
            {
                "column_id": "tags",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "production",
                },
            },
        ]

        with mock.patch(
            "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
            return_value=TraceListQueryBuilderV2,
        ):
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": filters,
                    "page_number": 0,
                    "page_size": 10,
                    "preview": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert payload["table"] == []
        assert "query_error_code" not in payload["metadata"]
        analytics.execute_ch_query.assert_called()

    def test_candidate_seed_scans_adjacent_windows_in_newest_first_order(self):
        """The projection seed never turns the request into one broad scan."""
        view = self._make_view()
        request = self._make_request(page_size=50)
        end = datetime(2026, 7, 30, 12)
        start = end - timedelta(minutes=10)
        newest = [
            self._trace_row(uuid.uuid4(), end - timedelta(minutes=1)),
            self._trace_row(uuid.uuid4(), end - timedelta(minutes=2)),
        ]
        older = [
            self._trace_row(uuid.uuid4(), end - timedelta(minutes=6)),
            self._trace_row(uuid.uuid4(), end - timedelta(minutes=7)),
        ]
        calls = []

        def _execute(query, params=None, **kwargs):
            params = dict(params or {})
            calls.append((query, params, kwargs))
            if "candidate_trace_ids" in params:
                candidates = {
                    row["trace_id"]: row
                    for row in [*newest, *older]
                    if row["trace_id"] in params["candidate_trace_ids"]
                }
                # ClickHouse probe order is not authoritative.
                return SimpleNamespace(data=list(reversed(candidates.values())))
            assert params["start_date"] == start
            assert params["end_date"] == end
            return SimpleNamespace(
                data=[
                    row
                    for row in [*newest, *older]
                    if params["root_seed_slice_start"]
                    <= row["start_time"]
                    < params["root_seed_slice_end"]
                ]
            )

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute

        status, payload = view._list_traces_of_session_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            validated_data={
                "filters": self._candidate_filters(start, end),
                "page_number": 0,
                "page_size": 50,
                "preview": True,
            },
            analytics=analytics,
            org_project_ids=None,
            org=request.organization,
        )

        assert status == "ok"
        assert [row["trace_id"] for row in payload["table"]] == [
            row["trace_id"] for row in [*newest, *older]
        ]
        assert payload["metadata"].get("query_complete", True) is True
        seed_calls = [call for call in calls if "candidate_trace_ids" not in call[1]]
        assert len(seed_calls) == 2
        newest_seed, older_seed = seed_calls
        assert newest_seed[1]["root_seed_slice_start"] == end - timedelta(minutes=5)
        assert newest_seed[1]["root_seed_slice_end"] == end
        assert older_seed[1]["root_seed_slice_start"] == start
        assert older_seed[1]["root_seed_slice_end"] == end - timedelta(minutes=5)
        assert all(call[1]["root_seed_limit"] == 100 for call in seed_calls)
        point_calls = [call for call in calls if "candidate_trace_ids" in call[1]]
        assert all(call[1]["candidate_start_date"] == start for call in point_calls)
        assert all(call[1]["candidate_end_date"] == end for call in point_calls)
        assert len(calls) == 6

    def test_end_of_local_day_range_is_covered_by_adjacent_seed_slices(self):
        view = self._make_view()
        request = self._make_request(page_size=50)
        now = datetime(2026, 7, 31, 2, 50)
        requested_end = datetime(2026, 7, 31, 6, 59, 59)
        requested_start = requested_end - timedelta(days=7)
        candidates = [
            self._trace_row(
                uuid.uuid4(),
                now - timedelta(seconds=index + 1),
            )
            for index in range(20)
        ]
        calls = []

        def _execute(query, params=None, **kwargs):
            params = dict(params or {})
            calls.append((query, params, kwargs))
            if "candidate_trace_ids" in params:
                return SimpleNamespace(data=list(candidates))
            assert params["start_date"] == requested_start
            assert params["end_date"] == requested_end
            return SimpleNamespace(
                data=[
                    row
                    for row in candidates
                    if params["root_seed_slice_start"]
                    <= row["start_time"]
                    < params["root_seed_slice_end"]
                ]
            )

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute

        with mock.patch("tracer.views.trace.timezone.now", return_value=now):
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": [
                        {
                            "column_id": "created_at",
                            "filter_config": {
                                "filter_type": "datetime",
                                "filter_op": "between",
                                "filter_value": [requested_start, requested_end],
                            },
                        },
                        {
                            "column_id": "arbitrary_status",
                            "display_name": "arbitrary_status",
                            "filter_config": {
                                "col_type": "SPAN_ATTRIBUTE",
                                "filter_type": "text",
                                "filter_op": "in",
                                "filter_value": ["status_rejected"],
                            },
                        },
                    ],
                    "page_number": 0,
                    "page_size": 50,
                    "preview": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert len(payload["table"]) == 10
        seed_calls = [call for call in calls if "candidate_trace_ids" not in call[1]]
        assert len(seed_calls) == 6
        assert len(calls) == 8
        assert seed_calls[0][1]["root_seed_slice_end"] == requested_end
        assert [
            call[1]["root_seed_slice_end"] - call[1]["root_seed_slice_start"]
            for call in seed_calls
        ] == [timedelta(minutes=minutes) for minutes in (5, 10, 20, 40, 80, 160)]
        assert all(
            seed_calls[index + 1][1]["root_seed_slice_end"]
            == seed_calls[index][1]["root_seed_slice_start"]
            for index in range(len(seed_calls) - 1)
        )
        assert payload["metadata"].get("query_complete", True) is True
        assert all("future_tail_start" not in params for _, params, _ in calls)

    def test_root_attribute_no_match_proves_the_full_window_without_classifying_roots(
        self,
    ):
        """A selective root predicate traverses bounded empty slices directly."""

        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        view = self._make_view()
        request = self._make_request(page_size=50)
        end = datetime(2026, 7, 31, 8, 30)
        start = end - timedelta(days=14)
        calls = []

        def _execute(query, params=None, **kwargs):
            params = dict(params or {})
            calls.append((query, params, kwargs))
            assert "candidate_trace_ids" not in params
            return SimpleNamespace(data=[])

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start, end],
                },
            },
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "__definitely_absent__",
                },
            },
        ]

        with mock.patch(
            "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
            return_value=TraceListQueryBuilderV2,
        ):
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": filters,
                    "page_number": 0,
                    "page_size": 50,
                    "preview": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert payload["table"] == []
        assert payload["metadata"].get("query_complete", True) is True
        assert len(calls) <= 24
        assert calls[0][1]["root_seed_slice_end"] == end
        assert calls[-1][1]["root_seed_slice_start"] == start
        seed_calls = [call for call in calls if "root_seed_limit" in call[1]]
        assert seed_calls == calls
        assert all(
            "mapContains(attrs_string, 'final_status')" in call[0]
            for call in seed_calls
        )
        assert all("argMax(" not in call[0] for call in seed_calls)
        assert all(
            calls[index + 1][1]["root_seed_slice_end"]
            == calls[index][1]["root_seed_slice_start"]
            for index in range(len(calls) - 1)
        )

    def test_root_filter_value_survives_without_per_span_enrichment_rows(self):
        """The canonical hydration owns root attrs; grouped child attrs stay bounded."""

        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        view = self._make_view()
        request = self._make_request(page_size=5)
        end = datetime(2026, 7, 31, 8, 30)
        start = end - timedelta(days=7)
        trace_rows = [
            {
                **self._trace_row(uuid.uuid4(), end - timedelta(seconds=index + 1)),
                "attrs_string": {"final_status": "Rechazado"},
                "attrs_number": {},
                "attrs_bool": {},
                "attributes_extra": "{}",
            }
            for index in range(6)
        ]
        calls = []

        def _execute(query, params=None, **kwargs):
            params = dict(params or {})
            calls.append((query, params, kwargs))
            if "root_seed_limit" in params:
                return SimpleNamespace(data=list(trace_rows))
            if "candidate_trace_ids" in params:
                ids = list(params["candidate_trace_ids"])
                if "latest_trace_name AS trace_name" not in query:
                    return SimpleNamespace(
                        data=[{"trace_id": trace_id} for trace_id in ids]
                    )
                by_id = {row["trace_id"]: row for row in trace_rows}
                return SimpleNamespace(data=[by_id[trace_id] for trace_id in ids])
            if "content_trace_ids" in params:
                return SimpleNamespace(
                    data=[
                        {"trace_id": trace_id, "metadata": "{}"}
                        for trace_id in params["content_trace_ids"]
                    ]
                )
            if "attr_trace_ids" in params:
                return SimpleNamespace(
                    data=[
                        {
                            "trace_id": trace_id,
                            "root_attribute_row": (
                                "{}",
                                {
                                    "final_status": "Rechazado",
                                    "root_marker": "exact",
                                },
                                {},
                                {},
                            ),
                            "root_attribute_count": 1,
                            "attribute_rows": [
                                (
                                    "{}",
                                    {
                                        "child_only": "value",
                                        "final_status": "must-not-widen-root",
                                    },
                                    {},
                                    {},
                                )
                            ],
                            "attribute_row_count": 1,
                        }
                        for trace_id in params["attr_trace_ids"]
                    ]
                )
            return SimpleNamespace(data=[])

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start, end],
                },
            },
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["Rechazado"],
                },
            },
        ]

        with (
            mock.patch(
                "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
                return_value=TraceListQueryBuilderV2,
            ),
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": filters,
                    "page_number": 0,
                    "page_size": 5,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert len(payload["table"]) == 5
        assert payload["metadata"].get("query_complete", True) is True
        assert all(row["final_status"] == "Rechazado" for row in payload["table"])
        assert all(row["root_marker"] == "exact" for row in payload["table"])
        assert all(row["child_only"] == "value" for row in payload["table"])
        attr_calls = [call for call in calls if "attr_trace_ids" in call[1]]
        assert len(attr_calls) == 1
        assert "groupArrayIf(128)(tuple(" in attr_calls[0][0]

    def test_whale_attribute_enrichment_is_explicitly_degraded_but_keeps_root(self):
        """A capped child sample is never exposed as if it were complete."""

        view = self._make_view()
        request = self._make_request(page_size=5)
        now = datetime(2026, 7, 31, 8, 30)
        trace_row = self._trace_row(uuid.uuid4(), now - timedelta(seconds=1))
        content_row = {"trace_id": trace_row["trace_id"], "metadata": "{}"}
        analytics = self._routing_analytics(
            trace_rows=[trace_row],
            total=1,
            content_rows=[content_row],
        )
        fallback_execute = analytics.execute_ch_query.side_effect
        calls = []

        def _execute(query, params=None, **kwargs):
            params = dict(params or {})
            calls.append((query, params, kwargs))
            if "attr_trace_ids" in params:
                return SimpleNamespace(
                    data=[
                        {
                            "trace_id": trace_row["trace_id"],
                            "root_attribute_row": (
                                "{}",
                                {
                                    "final_status": "Rechazado",
                                    "root_marker": "exact",
                                },
                                {},
                                {},
                            ),
                            "root_attribute_count": 1,
                            "attribute_rows": [
                                (
                                    "{}",
                                    {"partial_child": f"value-{index}"},
                                    {},
                                    {},
                                )
                                for index in range(128)
                            ],
                            "attribute_row_count": 129,
                        }
                    ]
                )
            return fallback_execute(query, params, **kwargs)

        analytics.execute_ch_query.side_effect = _execute
        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": 5,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert len(payload["table"]) == 1
        assert payload["metadata"]["query_complete"] is False
        assert payload["metadata"]["query_status"] == "degraded"
        assert payload["metadata"]["query_error_code"] == "query_failed"
        assert payload["table"][0]["final_status"] == "Rechazado"
        assert payload["table"][0]["root_marker"] == "exact"
        assert "partial_child" not in payload["table"][0]
        attr_call = next(call for call in calls if "attr_trace_ids" in call[1])
        assert attr_call[2]["settings"]["max_result_bytes"] == 16 * 1024 * 1024

    def test_future_skewed_root_fails_closed_without_using_partial_page(self):
        view = self._make_view()
        request = self._make_request(page_size=50)
        now = datetime(2026, 7, 31, 2, 50)
        requested_end = now + timedelta(hours=4)
        requested_start = now - timedelta(days=7)
        calls = []

        def _execute(query, params=None, **kwargs):
            params = dict(params or {})
            calls.append((query, params, kwargs))
            raise TimeoutError("projection seed exceeded its read budget")

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute

        with mock.patch("tracer.views.trace.timezone.now", return_value=now):
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": self._candidate_filters(
                        requested_start,
                        requested_end,
                    ),
                    "page_number": 0,
                    "page_size": 50,
                    "preview": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert payload["table"] == []
        assert payload["metadata"]["query_complete"] is False
        assert payload["metadata"]["query_status"] == "degraded"
        assert payload["metadata"]["query_error_code"] == "read_budget_exceeded"
        assert len(calls) == 1

    def test_saturated_candidate_seed_continues_with_a_stable_keyset(self):
        view = self._make_view()
        request = self._make_request(page_size=50)
        end = datetime(2026, 7, 30, 12)
        start = end - timedelta(minutes=5)
        candidates = [
            self._trace_row(
                uuid.uuid4(),
                end - timedelta(seconds=index + 1),
            )
            for index in range(200)
        ]
        calls = []

        def _execute(query, params=None, **kwargs):
            params = dict(params or {})
            calls.append((query, params, kwargs))
            if "candidate_trace_ids" not in params:
                if "root_seed_before_trace_id" in params:
                    offset = next(
                        index + 1
                        for index, row in enumerate(candidates)
                        if row["trace_id"] == params["root_seed_before_trace_id"]
                    )
                else:
                    offset = 0
                return SimpleNamespace(
                    data=list(candidates[offset : offset + params["root_seed_limit"]])
                )

            candidate_ids = list(params["candidate_trace_ids"])
            by_id = {row["trace_id"]: row for row in candidates}
            # Ten matches per 50-candidate batch, deliberately reversed.
            return SimpleNamespace(
                data=[by_id[trace_id] for trace_id in reversed(candidate_ids[:10])]
            )

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute

        status, payload = view._list_traces_of_session_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            validated_data={
                "filters": self._candidate_filters(start, end),
                "page_number": 0,
                "page_size": 50,
                "preview": True,
            },
            analytics=analytics,
            org_project_ids=None,
            org=request.organization,
        )

        assert status == "ok"
        assert [row["trace_id"] for row in payload["table"]] == [
            row["trace_id"] for row in candidates[:10]
        ]
        seed_calls = [call for call in calls if "candidate_trace_ids" not in call[1]]
        assert [call[1]["root_seed_limit"] for call in seed_calls] == [100, 100]
        assert "root_seed_before_start_time" not in seed_calls[0][1]
        assert (
            seed_calls[1][1]["root_seed_before_start_time"]
            == candidates[99]["start_time"]
        )
        assert (
            seed_calls[1][1]["root_seed_before_trace_id"] == candidates[99]["trace_id"]
        )
        probe_calls = [call for call in calls if "candidate_trace_ids" in call[1]]
        assert len(probe_calls) == 4
        assert list(probe_calls[0][1]["candidate_trace_ids"]) == [
            row["trace_id"] for row in candidates[:100]
        ]
        assert list(probe_calls[2][1]["candidate_trace_ids"]) == [
            row["trace_id"] for row in candidates[100:200]
        ]
        assert (
            list(probe_calls[1][1]["candidate_trace_ids"])
            == [row["trace_id"] for row in candidates[:50]][:10]
        )
        assert payload["metadata"].get("query_complete", True) is True

    def test_candidate_seed_code307_fails_closed_without_wide_retry(self):
        """Code 307 permits one fast attempt, then one bounded slice only."""
        from clickhouse_driver.errors import ErrorCodes, ServerException

        view = self._make_view()
        request = self._make_request(page_size=50)
        end = datetime(2026, 7, 30, 12)
        start = end - timedelta(days=14)
        calls = []
        clock = iter((0.0, 0.0, 0.25))

        def _execute(query, params=None, **kwargs):
            calls.append((query, dict(params or {}), kwargs))
            raise ServerException(
                "Code 307 private ClickHouse stack",
                code=ErrorCodes.TOO_MANY_BYTES,
            )

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute

        with mock.patch(
            "tracer.views.trace.monotonic", side_effect=lambda: next(clock)
        ):
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": self._candidate_filters(start, end),
                    "page_number": 0,
                    "page_size": 50,
                    "preview": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert payload["table"] == []
        assert payload["metadata"]["query_complete"] is False
        assert payload["metadata"]["query_error_code"] == "read_budget_exceeded"
        assert "ClickHouse" not in str(payload)
        assert len(calls) == 1
        assert calls[0][1]["start_date"] == start
        assert calls[0][1]["end_date"] == end
        assert calls[0][2]["timeout_ms"] == 750
        assert all(
            call[2]["settings"]["read_overflow_mode"] == "throw" for call in calls
        )

    def test_candidate_seed_and_probe_share_one_1800ms_deadline(self):
        """No seed or probe starts after the shared wall-clock budget expires."""
        view = self._make_view()
        request = self._make_request(page_size=50)
        end = datetime(2026, 7, 30, 12)
        start = end - timedelta(minutes=15)
        candidate = self._trace_row(uuid.uuid4(), end - timedelta(minutes=1))
        calls = []
        # The match finishes inside the shared budget but hydration is not
        # allowed to start after it expires.
        clock = iter((0.0, 0.0, 0.2, 0.4, 1.81, 1.82))

        def _execute(query, params=None, **kwargs):
            params = dict(params or {})
            calls.append((query, params, kwargs))
            if "candidate_trace_ids" in params:
                return SimpleNamespace(data=[candidate])
            return SimpleNamespace(data=[candidate])

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute

        with mock.patch(
            "tracer.views.trace.monotonic", side_effect=lambda: next(clock)
        ):
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": self._candidate_filters(start, end),
                    "page_number": 0,
                    "page_size": 50,
                    "preview": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert len(calls) == 2
        assert calls[0][2]["timeout_ms"] == 750
        assert calls[1][2]["timeout_ms"] == 750
        assert payload["metadata"]["query_complete"] is False
        assert payload["metadata"]["query_status"] == "degraded"
        assert payload["metadata"]["total_rows"] == 0

    def test_task_preview_marks_read_budget_failure_as_degraded(self):
        """A timed-out preview must not masquerade as a true empty selection."""
        from clickhouse_driver.errors import ErrorCodes, ServerException

        view = self._make_view()
        request = self._make_request(page_size=50)
        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = ServerException(
            "private ClickHouse timeout details",
            code=ErrorCodes.TIMEOUT_EXCEEDED,
        )

        status, payload = view._list_traces_of_session_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            validated_data={
                "filters": [],
                "page_number": 0,
                "page_size": 50,
                "preview": True,
            },
            analytics=analytics,
            org_project_ids=None,
            org=request.organization,
        )

        assert status == "ok"
        assert payload["table"] == []
        assert payload["metadata"] == {
            "total_rows": 0,
            "total_rows_is_lower_bound": True,
            "has_more": False,
            "query_complete": False,
            "query_status": "degraded",
            "query_error_code": "read_budget_exceeded",
        }

    def test_incomplete_deep_candidate_scan_reports_only_proven_matches(self):
        from clickhouse_driver.errors import ErrorCodes, ServerException

        view = self._make_view()
        request = self._make_request(page_size=25)
        now = __import__("datetime").datetime.now()
        candidate_rows = [
            {
                "trace_id": str(uuid.uuid4()),
                "project_id": str(uuid.uuid4()),
                "start_time": now,
                "trace_name": f"trace-{index}",
                "observation_type": "llm",
                "status": "OK",
                "latency_ms": 1,
                "total_tokens": 1,
                "cost": 0,
            }
            for index in range(100)
        ]
        calls = 0

        def _execute(query, params=None, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SimpleNamespace(data=list(candidate_rows))
            if calls == 2:
                return SimpleNamespace(
                    data=[{"trace_id": row["trace_id"]} for row in candidate_rows[:10]]
                )
            if calls == 3:
                return SimpleNamespace(data=list(candidate_rows[:10]))
            raise ServerException(
                "private ClickHouse timeout details",
                code=ErrorCodes.TIMEOUT_EXCEEDED,
            )

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _execute
        attribute_filter = {
            "column_id": "arbitrary_number",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "number",
                "filter_op": "equals",
                "filter_value": 7,
            },
        }

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": [attribute_filter],
                    "page_number": 5,
                    "page_size": 25,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert payload["table"] == []
        assert payload["metadata"]["total_rows"] == 10
        assert payload["metadata"]["total_rows_is_lower_bound"] is True
        assert payload["metadata"]["query_complete"] is False

    def test_task_preview_does_not_hide_programming_error(self):
        """Only a recognized read-budget error qualifies for degraded output."""
        view = self._make_view()
        request = self._make_request(page_size=50)
        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = RuntimeError(
            "trace preview contract bug"
        )

        with pytest.raises(RuntimeError, match="trace preview contract bug"):
            view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": 50,
                    "preview": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

    def test_span_trace_map_skipped_without_annotation_labels(self):
        """No annotation labels -> the annotation map is a guaranteed no-op,
        so the span->trace map query must not run at all."""
        view = self._make_view()
        request = self._make_request(page_size=5)
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(3)]
        analytics = self._routing_analytics(trace_rows=trace_rows, total=3)

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, _ = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={"filters": [], "page_number": 0, "page_size": 5},
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        analytics.get_span_trace_map.assert_not_called()

    @pytest.mark.django_db
    def test_span_trace_map_runs_with_annotation_labels(self):
        """With labels present the span->trace map runs, scoped to project + window."""
        view = self._make_view()
        request = self._make_request(page_size=5)
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(3)]
        analytics = self._routing_analytics(trace_rows=trace_rows, total=3)
        label = mock.Mock()
        label.id = uuid.uuid4()
        label.type = "text"
        label.name = "Quality"
        label.settings = {}
        project_id = str(uuid.uuid4())

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[label],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, _ = view._list_traces_of_session_clickhouse(
                request,
                project_id=project_id,
                validated_data={"filters": [], "page_number": 0, "page_size": 5},
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        analytics.get_span_trace_map.assert_called_once()
        assert analytics.get_span_trace_map.call_args.kwargs["project_id"] == project_id

    def test_content_shortfall_logs_buffer_warning(self):
        """Fewer content rows than page traces -> the window-buffer warning fires."""
        view = self._make_view()
        request = self._make_request(page_size=5)
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(3)]
        analytics = self._routing_analytics(
            trace_rows=trace_rows,
            total=3,
            content_rows=[],
        )

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
            mock.patch("tracer.views.trace.logger") as mock_logger,
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={"filters": [], "page_number": 0, "page_size": 5},
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        warning_events = [
            c.args[0] for c in mock_logger.warning.call_args_list if c.args
        ]
        assert (
            "trace content enrichment returned fewer traces than requested"
            in warning_events
        )
        assert payload["metadata"]["query_complete"] is False
        assert payload["metadata"]["query_status"] == "degraded"
        assert payload["metadata"]["query_error_code"] == "query_failed"

    def test_content_timeout_preserves_rows_and_marks_safe_degradation(self):
        view = self._make_view()
        request = self._make_request(page_size=5)
        trace_rows = [self._trace_row(uuid.uuid4(), datetime.now())]
        analytics = self._routing_analytics(trace_rows=trace_rows, total=1)
        base_execute = analytics.execute_ch_query.side_effect

        def _execute(query, params=None, **kwargs):
            if "content_trace_ids" in (params or {}):
                raise TimeoutError("private ClickHouse timeout details")
            return base_execute(query, params, **kwargs)

        analytics.execute_ch_query.side_effect = _execute
        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={"filters": [], "page_number": 0, "page_size": 5},
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        assert [row["trace_id"] for row in payload["table"]] == [
            trace_rows[0]["trace_id"]
        ]
        assert payload["metadata"]["query_complete"] is False
        assert payload["metadata"]["query_status"] == "degraded"
        assert payload["metadata"]["query_error_code"] == "read_budget_exceeded"
        assert "private ClickHouse" not in repr(payload)
