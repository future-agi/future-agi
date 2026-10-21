"""A narrowed Session candidate page publishes only a count it has proved.

A raised floor makes the discovery statement's ``count() OVER()`` a count of
CANDIDATES, not of matches: a predicate computed from a session's root set can
admit a session whose truncated set passes it and whose full set does not. The
reader drops those rows after the full-window verifier rejects them, and these
tests pin that the lower bound published beside the page drops them too.
"""

from __future__ import annotations

import re
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest

from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests.test_session_candidate_slice_page import (
    END,
    PROJECT,
    START,
    _builder,
    _cte,
    _filters,
    _floor_of,
    _read,
    _root_set_filters,
    _Server,
    _sessions_having,
    _sid,
)

MATCHING = 25
FALSE_CANDIDATES = 100


def _traces_count_equals_one_filters():
    filters = _root_set_filters("traces_count")
    filters[-1]["filter_config"].update(filter_op="equals", filter_value=1)
    return filters


class _AggregateEqualsServer(_Server):
    """``traces_count == 1`` evaluated on whatever roots the slice can see.

    ``MATCHING`` sessions have one root, newest in the window. Another
    ``FALSE_CANDIDATES`` have one root above any slice floor the width search
    picks and a second at the very start of the request window, so a slice sees
    one root for each of them and admits all ``MATCHING + FALSE_CANDIDATES``,
    while full state sees two and rejects the false ones.
    """

    def __init__(self):
        self.roots = {
            **{_sid(i): [END - timedelta(hours=100 + i)] for i in range(MATCHING)},
            **{
                _sid(MATCHING + i): [
                    END - timedelta(hours=150 + i / 2),
                    START + timedelta(hours=1),
                ]
                for i in range(FALSE_CANDIDATES)
            },
        }
        self.sql = ""
        super().__init__(
            rows=[],
            full_state={
                sid: min(times) for sid, times in self.roots.items() if len(times) == 1
            },
        )

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "AS remaining_count" in query:
            self.sql = query
            having = _sessions_having(query)
            parameter = re.fullmatch(
                r"HAVING traces_count = %\((\w+)\)s", having or ""
            ).group(1)
            assert params[parameter] == 1
        return super().execute_ch_query(
            query, params, timeout_ms=timeout_ms, settings=settings
        )

    def _slice(self, params):
        floor = _floor_of(_cte(self.sql, "candidate_root_identities"), params)
        found = []
        for sid, times in self.roots.items():
            live = [moment for moment in times if moment >= floor]
            if len(live) == 1:
                found.append({"session_id": sid, "session_start": live[0]})
        found.sort(key=lambda row: row["session_start"], reverse=True)
        return SimpleNamespace(
            data=[
                {**row, "remaining_count": len(found)}
                for row in found[: params["limit"]]
            ],
            columns=["session_id", "session_start", "remaining_count"],
        )


def _aggregate_builder():
    return SessionListQueryBuilderV2(
        project_id=PROJECT,
        filters=_traces_count_equals_one_filters(),
        page_size=MATCHING,
        page_number=0,
        bounded_internal_scan=True,
    )


def _select(server, filters, page_size):
    from tracer.tests.test_session_list_bounded_view import _view_and_request
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    return TraceSessionView._select_session_page(
        view,
        request,
        project_id=PROJECT,
        project=None,
        analytics=server,
        validated_data={
            "filters": filters,
            "sort_params": [],
            "page_number": 0,
            "page_size": page_size,
            "cursor_mode": True,
        },
    )


def _three_rows():
    return [
        {"session_id": _sid(0), "session_start": END - timedelta(hours=100)},
        {"session_id": _sid(1), "session_start": END - timedelta(hours=120)},
        {"session_id": _sid(2), "session_start": END - timedelta(hours=140)},
    ]


# --------------------------------------------------------------------------
# The counterexample
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_the_unsliced_statement_counts_only_the_true_matches():
    """The fixture itself: over the whole window only the 25 match."""
    builder = _aggregate_builder()
    assert builder.prefers_bounded_filter_page() is False
    assert builder.supports_candidate_cursor_page() is True
    server = _AggregateEqualsServer()
    query, params = builder.build_candidate_cursor_page_query()
    rows = server.execute_ch_query(query, params, timeout_ms=30_000, settings={}).data

    assert [row["session_id"] for row in rows] == [_sid(i) for i in range(MATCHING)]
    assert rows[0]["remaining_count"] == MATCHING


@pytest.mark.unit
def test_a_narrowed_page_counts_only_candidates_the_full_window_proved():
    server = _AggregateEqualsServer()
    page = _read(_aggregate_builder(), server)

    assert page.slice_start is not None, "the counterexample must be a sliced read"
    assert [row["session_id"] for row in page.rows] == [
        _sid(i) for i in range(MATCHING)
    ]
    # Discovery admitted all 125; full state rejected the 100. The bound may
    # not exceed the 25 that exist, and here it is exactly what was proved.
    assert page.remaining_count == MATCHING
    assert page.remaining_count <= len(server.full_state)


@pytest.mark.unit
def test_the_view_publishes_the_proved_count_as_its_lower_bound():
    """``candidate_total_count`` is the response's ``metadata.total_rows``."""
    server = _AggregateEqualsServer()
    selected = _select(server, _traces_count_equals_one_filters(), MATCHING)

    assert selected.candidate_cursor is True
    assert [row["session_id"] for row in selected.page_candidates] == [
        _sid(i) for i in range(MATCHING)
    ]
    assert selected.candidate_total_is_lower_bound is True
    assert selected.candidate_total_count == MATCHING


class _HydratingAggregateEqualsServer(_AggregateEqualsServer):
    """The counterexample, also answering the page's hydration statement."""

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "sum(cost) AS total_cost" in query:
            self.calls.append(("hydrate", params))
            return SimpleNamespace(
                data=[
                    {
                        "session_id": sid,
                        "session_start": min(self.roots[sid]),
                        "session_end": min(self.roots[sid]),
                        "duration": 0,
                        "total_cost": 1,
                        "total_tokens": 1,
                        "traces_count": 1,
                    }
                    for sid in params["candidate_session_ids"]
                ]
            )
        return super().execute_ch_query(
            query, params, timeout_ms=timeout_ms, settings=settings
        )


@pytest.mark.unit
def test_the_response_metadata_publishes_the_proved_lower_bound():
    from tracer.tests.test_session_list_bounded_view import _view_and_request
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    view._fetch_session_names = mock.Mock(return_value={})
    view._fetch_end_user_info = mock.Mock(return_value={})
    server = _HydratingAggregateEqualsServer()
    with mock.patch(
        "tracer.views.trace_session.AnnotationsLabels.objects.filter",
        return_value=[],
    ):
        status, payload = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=PROJECT,
            project=None,
            analytics=server,
            validated_data={
                "filters": _traces_count_equals_one_filters(),
                "sort_params": [],
                "page_number": 0,
                "page_size": MATCHING,
                "cursor_mode": True,
            },
        )

    assert status == "ok"
    assert "hydrate" in server.kinds
    assert [row["session_id"] for row in payload["table"]] == [
        _sid(i) for i in range(MATCHING)
    ]
    metadata = payload["metadata"]
    assert metadata["total_rows"] == MATCHING
    assert metadata["total_rows_is_lower_bound"] is True
    assert "total_rows_exact" not in metadata


# --------------------------------------------------------------------------
# Controls
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_a_narrowed_page_rejecting_nothing_counts_every_verified_candidate():
    """Nothing rejected: the bound is the whole verified prefix, not the page."""
    rows = _three_rows()
    server = _Server(
        rows=rows,
        full_state={row["session_id"]: row["session_start"] for row in rows},
    )
    page = _read(_builder(page_size=2), server)

    assert page.slice_start is not None
    assert [row["session_id"] for row in page.rows] == [_sid(0), _sid(1)]
    assert page.has_more is True
    assert page.remaining_count == 3

    selected = _select(
        _Server(
            rows=rows,
            full_state={row["session_id"]: row["session_start"] for row in rows},
        ),
        _filters(),
        2,
    )
    assert selected.candidate_total_is_lower_bound is True
    assert selected.candidate_total_count == 3


@pytest.mark.unit
def test_an_unsliced_page_keeps_the_count_it_established():
    """No readable estimate, no slice: the statement's count is exact."""
    rows = _three_rows()
    full_state = {row["session_id"]: row["session_start"] for row in rows}
    page = _read(
        _builder(page_size=2),
        _Server(rows=rows, full_state=full_state, estimate_columns=None),
    )

    assert page.slice_start is None
    assert page.remaining_count == 3

    selected = _select(
        _Server(rows=rows, full_state=full_state, estimate_columns=None),
        _filters(),
        2,
    )
    assert selected.candidate_total_is_lower_bound is False
    assert selected.candidate_total_count == 3


@pytest.mark.unit
def test_a_page_that_widens_to_the_whole_window_keeps_its_exact_count():
    """Too few survivors: the read ends unsliced, and that count is exact."""
    kept = END - timedelta(hours=120)
    server = _Server(
        rows=[
            {"session_id": _sid(0), "session_start": END - timedelta(hours=100)},
            {"session_id": _sid(1), "session_start": kept},
            {"session_id": _sid(2), "session_start": END - timedelta(hours=140)},
        ],
        full_state={
            _sid(0): START + timedelta(hours=4),
            _sid(1): kept,
            _sid(2): START + timedelta(hours=6),
        },
    )
    page = _read(_builder(page_size=2), server)

    assert server.kinds.count("slice") > 1
    assert page.slice_start is None
    assert page.remaining_count == 3
