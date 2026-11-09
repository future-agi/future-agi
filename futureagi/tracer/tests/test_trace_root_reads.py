"""A page of traces' root spans in two bounded queries (the run page, Ruling 95).

The ClickHouse client is replaced by a small in-memory ``spans`` table that
answers the two statements by their parameters, with the table's own rules (a
span's latest version wins, a tombstone stays dead). The SQL itself runs on a
real ClickHouse in the DSX stack rows (SDK-TR-01, UI-TR-01).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver.errors import ErrorCodes, ServerException

from tracer.services.clickhouse.v2 import trace_root_reads
from tracer.services.clickhouse.v2.trace_detail_reads import TraceDetailReadUnavailable
from tracer.services.clickhouse.v2.trace_root_reads import (
    build_root_content_query,
    build_summary_query,
    read_trace_roots,
)

P1 = "11111111-1111-1111-1111-111111111111"
P2 = "22222222-2222-2222-2222-222222222222"
OUTSIDE = "33333333-3333-3333-3333-333333333333"
T0 = datetime(2026, 10, 24, 4, 0, tzinfo=UTC)


def _us(value: datetime) -> int:
    return int((value - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1_000_000)


def span(
    trace_id,
    span_id,
    *,
    project=P1,
    parent="",
    at=0,
    version=1,
    deleted=0,
    run=None,
    name="experiment.trial",
):
    return {
        "project_id": project,
        "trace_id": trace_id,
        "id": span_id,
        "start_time": T0 + timedelta(seconds=at),
        "parent_span_id": parent,
        "name": name,
        "attrs_string": {"fi.experiment.run_id": run} if run else {},
        "attrs_number": {},
        "attrs_bool": {},
        "attributes_extra": "{}",
        "is_deleted": deleted,
        "_version": version,
    }


class SpanTable:
    """Answers the summary and root-content statements over in-memory rows."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def _latest(self, rows):
        groups = {}
        for row in rows:
            key = (row["project_id"], row["trace_id"], row["id"], row["start_time"])
            groups.setdefault(key, []).append(row)
        return {
            key: max(group, key=lambda r: r["_version"])
            | {"max_version": max(r["_version"] for r in group)}
            for key, group in groups.items()
        }

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append(
            SimpleNamespace(
                query=query, params=params, timeout_ms=timeout_ms, settings=settings
            )
        )
        if "run_page_trace_ids" in params:
            return SimpleNamespace(data=self._summary(params))
        return SimpleNamespace(data=self._content(params))

    def _summary(self, params):
        roots = set(params["run_page_roots"])
        rows = [
            r
            for r in self.rows
            if r["project_id"] in params["run_page_project_ids"]
            and r["trace_id"] in params["run_page_trace_ids"]
        ]
        copies = {}
        for (project, trace, span_id, start), row in self._latest(rows).items():
            copies.setdefault((project, trace), []).append((span_id, start, row))
        out = []
        for (project, trace), spans in sorted(
            copies.items(), key=lambda kv: (kv[0][1], kv[0][0])
        ):
            live = [(s, t, r) for s, t, r in spans if not r["is_deleted"]]
            if not live:
                continue
            recorded = sorted((t, s) for s, t, r in live if (trace, s) in roots)
            parentless = sorted((t, s) for s, t, r in live if not r["parent_span_id"])
            out.append(
                {
                    "project_id": project,
                    "trace_id": trace,
                    "span_count": len(live),
                    "latest_version": max(r["max_version"] for _, _, r in live),
                    "recorded_roots": len(recorded),
                    "recorded_root": (recorded[0][1], _us(recorded[0][0]))
                    if recorded
                    else ("", 0),
                    "parentless_roots": len(parentless),
                    "first_parentless": (parentless[0][1], _us(parentless[0][0]))
                    if parentless
                    else ("", 0),
                }
            )
        return out[: params["run_page_summary_limit"]]

    def _content(self, params):
        wanted = set(params["run_root_identities"])
        rows = [
            r
            for r in self.rows
            if (r["project_id"], r["trace_id"], r["id"], _us(r["start_time"])) in wanted
        ]
        out = []
        for (project, trace, span_id, start), row in self._latest(rows).items():
            if row["is_deleted"]:
                continue
            out.append(
                {
                    "project_id": project,
                    "trace_id": trace,
                    "id": span_id,
                    "start_time": start,
                    "parent_span_id": row["parent_span_id"],
                    "name": row["name"],
                    "span_attributes": row["attributes_extra"],
                    "attrs_string": row["attrs_string"],
                    "attrs_number": row["attrs_number"],
                    "attrs_bool": row["attrs_bool"],
                }
            )
        return sorted(out, key=lambda r: r["trace_id"])


def read(table, wanted, scope=(P1, P2)):
    return read_trace_roots(analytics=table, project_ids=list(scope), wanted=wanted)


@pytest.mark.unit
@pytest.mark.parametrize("size", [1, 50, 100])
def test_a_page_is_two_queries_whatever_its_size(size):
    rows = []
    for n in range(size):
        rows += [
            span(f"t{n}", f"r{n}"),
            span(f"t{n}", f"c{n}", parent=f"r{n}", at=1, name="llm"),
        ]
    table = SpanTable(rows)
    found = read(table, [(f"t{n}", f"r{n}") for n in range(size)])
    assert len(table.calls) == 2
    assert set(found) == {f"t{n}" for n in range(size)}
    assert all(
        f.span_count == 2 and f.root["name"] == "experiment.trial"
        for f in found.values()
    )
    for call in table.calls:
        assert (
            call.settings["max_threads"] == 1
            and call.settings["read_overflow_mode"] == "throw"
        )
        assert call.settings["max_result_rows"] >= 1 and call.timeout_ms <= 9_500


@pytest.mark.unit
def test_the_recorded_root_wins_over_an_earlier_parentless_span():
    rows = [
        span("t", "stray", at=0, run="other"),
        span("t", "root", parent="remote-parent", at=5, run="mine"),
        span("t", "child", parent="root", at=6),
    ]
    found = read(SpanTable(rows), [("t", "root")])
    assert found["t"].root["id"] == "root" and found["t"].span_count == 3
    # with no recorded root (or one the store does not hold): the earliest parentless span
    assert read(SpanTable(rows), [("t", None)])["t"].root["id"] == "stray"
    assert read(SpanTable(rows), [("t", "missing")])["t"].root["id"] == "stray"


@pytest.mark.unit
def test_latest_versions_win_and_tombstones_stay_dead():
    rows = [
        span("t", "old-root", at=0, version=1),
        span(
            "t", "old-root", at=0, version=2, deleted=1
        ),  # its latest version is a tombstone
        span("t", "moved", at=1, version=1),
        span(
            "t", "moved", parent="new-parent", at=1, version=2
        ),  # no longer parentless
        span("t", "root", at=2),
    ]
    found = read(SpanTable(rows), [("t", "old-root")])
    assert found["t"].root["id"] == "root" and found["t"].span_count == 2
    gone = [span("g", "only", version=1), span("g", "only", version=2, deleted=1)]
    assert read(SpanTable(gone), [("g", "only")]) == {}
    no_root = [span("n", "child", parent="elsewhere")]
    assert read(SpanTable(no_root), [("n", None)]) == {}


@pytest.mark.unit
def test_a_trace_in_two_projects_is_served_from_its_newest_copy():
    rows = [
        span("t", "root", project=P1, version=5),
        span("t", "root", project=P2, version=9),
        span("t", "child", project=P2, parent="root", at=1, version=9),
    ]
    found = read(SpanTable(rows), [("t", "root")])
    assert found["t"].project_id == P2 and found["t"].span_count == 2


@pytest.mark.unit
def test_only_the_callers_projects_are_read():
    rows = [span("t", "root", project=OUTSIDE, run="theirs")]
    table = SpanTable(rows)
    assert read(table, [("t", "root")], scope=(P1,)) == {}
    assert table.calls[0].params["run_page_project_ids"] == (P1,)
    empty = SpanTable(rows)
    assert read(empty, [("t", "root")], scope=()) == {} and empty.calls == []
    assert read(empty, [], scope=(P1,)) == {} and empty.calls == []
    too_many = [f"{n:08d}-0000-0000-0000-000000000000" for n in range(4097)]
    with pytest.raises(TraceDetailReadUnavailable) as raised:
        read(empty, [("t", "root")], scope=too_many)
    assert raised.value.code == "project_scope_too_large" and empty.calls == []


@pytest.mark.unit
def test_failures_and_the_deadline_fail_closed(monkeypatch):
    class Failing(SpanTable):
        def execute_ch_query(self, query, params, **kwargs):
            raise ServerException("memory", code=ErrorCodes.MEMORY_LIMIT_EXCEEDED)

    with pytest.raises(TraceDetailReadUnavailable) as raised:
        read(Failing([]), [("t", "root")])
    assert raised.value.code == "read_budget_exceeded"

    clock = iter(
        [0.0, 0.0, 100.0]
    )  # the summary in time, the content read after the wall deadline
    monkeypatch.setattr(trace_root_reads, "monotonic", lambda: next(clock))
    table = SpanTable([span("t", "root")])
    with pytest.raises(TraceDetailReadUnavailable) as raised:
        read(table, [("t", "root")])
    assert raised.value.code == "deadline_exceeded" and len(table.calls) == 1


@pytest.mark.unit
def test_the_statements_are_scoped_and_exact():
    query, params = build_summary_query(
        project_ids=(P1,), trace_ids=("t1", "t2"), roots=()
    )
    assert "PREWHERE project_id IN %(run_page_project_ids)s" in query
    assert "AND trace_id IN %(run_page_trace_ids)s" in query
    assert "FINAL" not in query and params["run_page_roots"] == (("", ""),)
    assert params["run_page_summary_limit"] == 2 * 16 + 1
    from tracer.services.clickhouse.v2.trace_detail_reads import PhysicalSpanIdentity

    identity = PhysicalSpanIdentity(
        project_id=P1, trace_id="t1", span_id="r1", start_time=T0
    )
    query, params = build_root_content_query([identity])
    assert (
        "toUnixTimestamp64Micro(start_time)" in query
        and "WHERE latest_is_deleted = 0" in query
    )
    assert params["run_root_identities"] == ((P1, "t1", "r1", _us(T0)),)
    assert params["run_root_dates"] == (T0.date(),) and params["run_root_limit"] == 2
