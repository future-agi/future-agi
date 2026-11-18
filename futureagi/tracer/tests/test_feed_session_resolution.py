"""Regression tests for CH-only session member resolution in the feed.

Pins the bug where session-eval cluster members resolved zero traces on a
ClickHouse-only deployment: membership and newest-first ordering come from the
spans table via ``CHSpanReader`` (keyed by the threaded ``project_id``), NOT
from a PG ``TraceSession`` / ``Trace.session`` FK that is ``None`` for net-new
CH-only sessions post session-cutover.

DB-free by design (mocks the CH reader), so it runs without Postgres/ClickHouse
and fails fast if the resolution ever reverts to a PG-FK walk.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from tracer.queries import feed


def _mock_reader(trace_ids, start_times=None):
    reader = MagicMock()
    reader.session_trace_ids.return_value = trace_ids
    reader.per_trace_root_span_start_times.return_value = start_times or {}
    cm = MagicMock()
    cm.__enter__.return_value = reader
    cm.__exit__.return_value = False
    return cm, reader


def test_session_traces_map_resolves_via_ch_with_threaded_project_id():
    cm, reader = _mock_reader(["t2", "t1"])
    with patch.object(feed, "get_reader", return_value=cm):
        out = feed._session_traces_map(["sess-A"], "proj-1")

    # Members come from CH, pinned to the project the caller threaded in.
    reader.session_trace_ids.assert_called_once_with("proj-1", "sess-A")
    assert set(out["sess-A"]) == {"t1", "t2"}


def test_session_traces_map_orders_newest_first_from_ch_start_times():
    cm, _ = _mock_reader(
        ["t_old", "t_new"],
        start_times={
            "t_old": datetime(2026, 6, 1, tzinfo=UTC),
            "t_new": datetime(2026, 6, 23, tzinfo=UTC),
        },
    )
    with patch.object(feed, "get_reader", return_value=cm):
        out = feed._session_traces_map(["sess-A"], "proj-1")

    # Ordered by CH root-span start_time, latest turn first.
    assert out["sess-A"] == ["t_new", "t_old"]


def test_session_rep_trace_map_returns_latest_trace_per_session():
    cm, _ = _mock_reader(
        ["t_old", "t_new"],
        start_times={
            "t_old": datetime(2026, 6, 1, tzinfo=UTC),
            "t_new": datetime(2026, 6, 23, tzinfo=UTC),
        },
    )
    with patch.object(feed, "get_reader", return_value=cm):
        rep = feed._session_rep_trace_map(["sess-A"], "proj-1")

    # Representative trace is the newest turn ([0] of the ordered set).
    assert rep == {"sess-A": "t_new"}


def test_session_traces_map_empty_when_ch_has_no_members():
    cm, reader = _mock_reader([])
    with patch.object(feed, "get_reader", return_value=cm):
        out = feed._session_traces_map(["sess-A"], "proj-1")

    reader.session_trace_ids.assert_called_once_with("proj-1", "sess-A")
    assert out == {}


# ---------------------------------------------------------------------------
# Regression: trace-row hydration must read CH for post-cutover CH-only traces
# (no PG ``Trace`` row). ``_resolve_member_traces`` is CH-only: it hydrates from
# the root span and omits members with no CH root — reading PG would silently
# drop collector traces from the Traces tab / Overview.
# ---------------------------------------------------------------------------


class _FakeRoot:
    def __init__(self):
        self.start_time = "2026-06-23T00:00:00Z"
        self.input = "track my package"
        self.output = "delivered yesterday"


def test_resolve_member_traces_uses_ch_shim():
    tid = "11111111-1111-1111-1111-111111111111"
    with patch.object(feed, "_get_root_spans_batch", return_value={tid: _FakeRoot()}):
        out = feed._resolve_member_traces([tid])

    assert tid in out, "CH-only trace must resolve via the spans table, not be dropped"
    shim = out[tid]
    assert str(shim.id) == tid
    assert shim.input == "track my package"
    assert shim.output == "delivered yesterday"


def test_resolve_member_traces_omits_when_no_ch_root():
    tid = "22222222-2222-2222-2222-222222222222"
    with patch.object(feed, "_get_root_spans_batch", return_value={}):
        out = feed._resolve_member_traces([tid])

    # No CH root span -> member is omitted (same as an unresolvable trace).
    assert out == {}
