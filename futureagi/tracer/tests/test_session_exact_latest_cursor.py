"""Exact session storage-key/window/cursor regressions; no server or DDL.

Optional chdb tests execute generated queries over inline VALUES relations.
Remap FINAL is removed (fixtures already supply latest remaps), and PREWHERE
is lowered to equivalent WHERE conjunctions because VALUES has no PREWHERE.
They do not establish production planner behavior, retention, or latency.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from uuid import UUID

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

PROJECT = str(UUID(int=1))
SESSION = str(UUID(int=100))
METHODS = ("page", "cursor", "count", "match", "metrics", "content", "attributes")


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    """Override server-bootstrap DDL: this module uses only inline relations."""
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    """No external Score schema is needed by these isolated/mocked tests."""
    yield


def _filters(start, end):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
            },
        }
    ]


def _query(builder, method):
    if method == "match":
        return builder.build_filter_match_query([SESSION])
    if method in {"metrics", "content", "attributes"}:
        name = {
            "metrics": "build_page_metrics_query",
            "content": "build_content_query",
            "attributes": "build_span_attributes_query",
        }[method]
        return getattr(builder, name)([SESSION])
    return getattr(
        builder,
        {
            "page": "build_candidate_page_query",
            "cursor": "build_candidate_cursor_page_query",
            "count": "build_candidate_count_query",
        }[method],
    )()


@pytest.mark.unit
@pytest.mark.parametrize("cls", [SessionListQueryBuilder, SessionListQueryBuilderV2])
@pytest.mark.parametrize("method", ["page", "cursor"])
@pytest.mark.parametrize("aggregate", [False, True])
def test_session_candidate_params_preserve_statement_lookahead_after_builder_reuse(
    cls, method, aggregate
):
    start = datetime(2026, 8, 1, 10, 30, 0, 123456)
    filters = _filters(start, start + timedelta(days=7))
    if aggregate:
        filters += [
            {
                "column_id": "session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [SESSION],
                },
            },
            {
                "column_id": "total_tokens",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 7,
                },
            },
        ]
    builder = cls(project_id=PROJECT, filters=filters, page_size=24, page_number=3)
    # The legacy builder legitimately caches its own limit/offset. A new
    # candidate statement must own its page_size+1 lookahead, not inherit 25.
    builder.build()
    assert builder.params["limit"] == 25
    assert builder.params["offset"] == 72
    builder.page_size = 25
    if aggregate:
        builder.filters[-1]["filter_config"]["filter_value"] = 11
    before = dict(builder.params)
    sql, params = _query(builder, method)
    assert params["limit"] == 26
    if method == "page":
        assert params["offset"] == 75
    if aggregate:
        assert "HAVING total_tokens >" in sql
        assert params["having_901"] == 11
    assert builder.params == before, (
        "statement-local HAVING must not mutate cached builder state"
    )


@pytest.mark.unit
@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("cls", [SessionListQueryBuilder, SessionListQueryBuilderV2])
def test_session_replay_uses_schema_key_and_post_collapse_microsecond_window(
    cls, method, days
):
    start = datetime(2026, 8, 1, 10, 30, 0, 123456)
    builder = cls(
        project_id=PROJECT,
        filters=_filters(start, start + timedelta(days=days)),
        bounded_internal_scan=True,
    )
    sql, params = _query(builder, method)
    compact = " ".join(sql.split())
    key = "project_id, trace_id, id, start_time"
    if cls is SessionListQueryBuilderV2:
        key = "project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        assert "toStartOfHour(fromUnixTimestamp64Micro(%(start_date_us)s" in sql
        assert "%(end_date_us)s - 1" in sql
        assert "toStartOfHour(start_time) AS start_hour" in sql
    else:
        assert "toStartOfHour" not in sql
    assert f"GROUP BY {key}" in compact
    assert "AS latest_start_time" in sql
    assert (
        "latest_start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')" in sql
    )
    assert "latest_start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')" in sql
    assert params["start_date_us"] % 1_000_000 == 123456
    assert "spans_per_session" not in sql
    assert not re.search(r"FROM\s+spans(?:\s+AS\s+\w+)?\s+FINAL\b", sql)


@pytest.mark.unit
def test_negative_native_date_is_checked_after_latest_collapse():
    start = datetime(2026, 8, 1, 10, 30, 0, 123456)
    filters = _filters(start, start + timedelta(days=7)) + [
        {
            "column_id": "start_time",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "not_equals",
                "filter_value": start.isoformat(),
            },
        }
    ]
    builder = SessionListQueryBuilderV2(
        project_id=PROJECT, filters=filters, bounded_internal_scan=True
    )
    for method in METHODS:
        sql, _params = _query(builder, method)
        latest = sql.split("latest_roots AS (", 1)[1].split("GROUP BY", 1)[0]
        assert "!=" not in latest
        assert " OR latest_start_time >= fromUnixTimestamp64Micro" in sql


def _values_where(sql):
    """Keep all predicates/parentheses; lower only the storage-specific clause."""
    depth = 0
    prewhere = {}

    def token(match):
        nonlocal depth
        word = match.group()
        if word == "(":
            depth += 1
        elif word == ")":
            prewhere.pop(depth, None)
            depth -= 1
        elif word == "SELECT":
            prewhere[depth] = False
        elif word == "PREWHERE":
            prewhere[depth] = True
            return "WHERE"
        elif word == "WHERE" and prewhere.get(depth):
            return "AND"
        return word

    return re.sub(r"'(?:\\.|[^'\\])*'|\b(?:SELECT|PREWHERE|WHERE)\b|[()]", token, sql)


def _inline_execute(chdb, sql, params, rows, remaps):
    context = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))
    rendered = sql % escape_params(params, context)
    # Inline remaps are already latest state and VALUES has no FINAL operator.
    rendered = re.sub(
        r"(trace_session_id_remap(?:\s+AS\s+\w+)?)\s+FINAL\b", r"\1", rendered
    )
    # Match schema/002_spans_v2.sql exactly. An unqualified DateTime64 uses
    # chdb's process-initialized timezone; importing it before Django's UTC
    # setup otherwise shifts rows/cursors by the host timezone offset.
    columns = """project_id UUID, observation_type String, service_name String,
        start_time DateTime64(6, 'UTC'), trace_id String, id String,
        parent_span_id Nullable(String), trace_session_id Nullable(UUID),
        _version UInt64, is_deleted UInt8, cost Float64, input String,
        end_user_id Nullable(UUID)"""
    literals = escape_params(
        {"rows": tuple(rows), "remaps": tuple(remaps), "columns": columns}, context
    )
    spans = f"""SELECT *, start_time + INTERVAL 1 SECOND AS end_time, toInt64(1) AS total_tokens,
        map('state', input) AS attrs_string, map('amount', cost) AS attrs_number,
        map('ok', true) AS attrs_bool, '{{}}' AS attributes_extra
        FROM values({literals["columns"]}, {literals["rows"][1:-1]})"""
    maps = (
        f"SELECT * FROM values('old_id UUID, new_id UUID', {literals['remaps'][1:-1]})"
    )
    assert rendered.lstrip().startswith("WITH")
    rendered = f"WITH spans AS ({spans}), trace_session_id_remap AS ({maps}), {rendered.lstrip()[4:]}"
    result = str(chdb.query(_values_where(rendered), "JSONEachRow"))
    return [json.loads(line) for line in result.splitlines() if line]


def _adversarial_rows(start, end):
    rows = []

    def add(
        sid,
        span,
        when,
        *,
        version=1,
        deleted=0,
        cost=1,
        service="a",
        kind="SPAN",
        parent=None,
    ):
        rows.append(
            (
                PROJECT,
                kind,
                service,
                when.isoformat(sep=" "),
                span,
                span,
                parent,
                str(UUID(int=sid)),
                version,
                deleted,
                cost,
                f"v{version}-{span}",
                None,
            )
        )

    # A deleted early root must neither omit nor rank this newest live session late.
    add(100, "early", start - timedelta(minutes=10))
    add(100, "early", start - timedelta(minutes=10), version=2, deleted=1)
    add(100, "new", end - timedelta(minutes=1), cost=5)
    # More than the former 101-seed page, all newer than A's stale rollup min.
    for i in range(105):
        add(1000 + i, f"other-{i}", start + timedelta(minutes=2, microseconds=i))
    # Same-hour replacements: one surviving timestamp/cost/input, never two.
    add(101, "moved", start + timedelta(minutes=5), cost=90)
    add(101, "moved", start + timedelta(minutes=6), version=2, cost=7)
    # Physical service/type collisions must stay distinct.
    for service, kind, cost in [("a", "SPAN", 2), ("b", "SPAN", 3), ("a", "LLM", 4)]:
        add(
            102,
            "collision",
            start + timedelta(minutes=7),
            service=service,
            kind=kind,
            cost=cost,
        )
    # Latest winners outside precise boundaries must not resurrect older rows.
    add(103, "out-start", start + timedelta(minutes=4))
    add(103, "out-start", start - timedelta(minutes=1), version=2)
    add(104, "out-end", end - timedelta(minutes=2))
    add(104, "out-end", end + timedelta(minutes=1), version=2)
    add(105, "child", start + timedelta(minutes=3))
    add(105, "child", start + timedelta(minutes=4), version=2, parent="parent")
    add(106, "reassigned", start + timedelta(minutes=8))
    add(107, "reassigned", start + timedelta(minutes=9), version=2)
    add(108, "tombstone", start + timedelta(minutes=4))
    add(108, "tombstone", start - timedelta(minutes=2), version=2, deleted=1)
    # Identical IDs in distinct replacement hours are distinct physical rows.
    add(109, "two-hours", start + timedelta(minutes=5), cost=4)
    add(109, "two-hours", start + timedelta(minutes=65), version=2, cost=6)
    add(110, "into-window", start - timedelta(minutes=1))
    add(110, "into-window", start + timedelta(minutes=1), version=2)
    # All aliases contribute to one canonical minimum and aggregate, once.
    add(200, "alias-a", start + timedelta(minutes=10), cost=2)
    add(201, "alias-b", start + timedelta(minutes=11), cost=3)
    add(300, "alias-new", start + timedelta(minutes=12), cost=4)
    return rows, [
        (str(UUID(int=200)), str(UUID(int=300))),
        (str(UUID(int=201)), str(UUID(int=300))),
    ]


@pytest.mark.unit
@pytest.mark.parametrize("days", [7, 30, 365])
def test_inline_exact_session_pages_and_hydration_over_adversarial_versions(days):
    chdb = pytest.importorskip(
        "chdb", reason="optional isolated ClickHouse execution engine"
    )
    start = datetime(2025, 8, 1, 10, 30, 0, 123456)
    end = start + timedelta(days=days)
    rows, remaps = _adversarial_rows(start, end)
    builder = SessionListQueryBuilderV2(
        project_id=PROJECT, filters=_filters(start, end), page_size=25
    )
    collected = []
    boundary = {}
    while True:
        sql, params = builder.build_candidate_cursor_page_query(**boundary)
        assert params["limit"] == 26, (params, builder.params)
        page = _inline_execute(chdb, sql, params, rows, remaps)
        collected.extend(page[:25])
        if len(page) <= 25:
            break
        last = page[24]
        boundary = {
            "before_start_time": datetime.fromisoformat(last["session_start"]),
            "before_session_id": last["session_id"],
        }
        assert len(collected) <= 125, "cursor did not advance"
    ids = [item["session_id"] for item in collected]
    assert len(ids) == len(set(ids)) == 112
    assert ids[0] == SESSION
    assert not {str(UUID(int=n)) for n in (103, 104, 105, 106, 108, 201, 300)} & set(
        ids
    )
    expected_costs = {100: 5, 101: 7, 102: 9, 107: 1, 109: 10, 110: 1, 200: 9}
    for method in (
        "build_page_metrics_query",
        "build_content_query",
        "build_span_attributes_query",
    ):
        sql, params = getattr(builder, method)(
            [str(UUID(int=n)) for n in expected_costs]
        )
        result = _inline_execute(chdb, sql, params, rows, remaps)
        assert {r["session_id"] for r in result} == {
            str(UUID(int=n)) for n in expected_costs
        }
        if method == "build_page_metrics_query":
            assert {
                int(UUID(r["session_id"])): r["total_cost"] for r in result
            } == expected_costs
            moved = next(r for r in result if int(UUID(r["session_id"])) == 101)
            assert datetime.fromisoformat(moved["session_start"]) == start + timedelta(
                minutes=6
            )
        if method == "build_content_query":
            moved = next(r for r in result if int(UUID(r["session_id"])) == 101)
            assert moved["first_message"] == moved["last_message"] == "v2-moved"
        if method == "build_span_attributes_query":
            moved = [r for r in result if int(UUID(r["session_id"])) == 101]
            assert len(moved) == 1 and moved[0]["attrs_number"]["amount"] == 7
    # An excluded latest timestamp must not expose its older allowed version.
    excluded = {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "not_equals",
            "filter_value": (start + timedelta(minutes=6)).isoformat(),
        },
    }
    builder = SessionListQueryBuilderV2(
        project_id=PROJECT, filters=[*_filters(start, end), excluded], page_size=200
    )
    sql, params = builder.build_candidate_cursor_page_query()
    excluded_rows = _inline_execute(chdb, sql, params, rows, remaps)
    assert len(excluded_rows) == 111
    assert str(UUID(int=101)) not in {row["session_id"] for row in excluded_rows}
    # Finite classifiers must not publish an unrelated reassignment target
    # from a partial root set, but must expand all aliases of a requested group.
    builder = SessionListQueryBuilderV2(
        project_id=PROJECT, filters=_filters(start, end), bounded_internal_scan=True
    )
    sql, params = builder.build_filter_match_query([str(UUID(int=106))])
    assert _inline_execute(chdb, sql, params, rows, remaps) == []
    sql, params = builder.build_filter_match_query([str(UUID(int=201))])
    alias_result = _inline_execute(chdb, sql, params, rows, remaps)
    assert len(alias_result) == 1
    assert alias_result[0]["session_id"] == str(UUID(int=200))
    assert datetime.fromisoformat(alias_result[0]["start_time"]) == start + timedelta(
        minutes=10
    )


@pytest.mark.unit
@pytest.mark.parametrize("date_only", [False, True])
@pytest.mark.parametrize("org_scope", [False, True])
def test_public_default_session_dispatch_uses_real_exact_builder_and_signed_canonical_cursor(
    date_only, org_scope
):
    from tracer.services.clickhouse.list_cursor import (
        ListCursorError,
        cursor_scope_for_request,
        encode_list_cursor,
    )
    from tracer.tests.test_session_list_bounded_view import _view_and_request
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    start = datetime(2026, 8, 1, 10, 30, 0, 123456)
    end = start + timedelta(days=7)
    native_filters = _filters(start, end) if date_only else []
    data = {
        "filters": native_filters,
        "sort_params": [],
        "page_number": 0,
        "page_size": 1,
        "cursor_mode": True,
    }
    sid2 = str(UUID(int=101))
    newest, oldest = end - timedelta(minutes=1), end - timedelta(minutes=2)
    calls = []

    def execute(sql, params, **kwargs):
        calls.append((sql, params))
        assert "spans_per_session" not in sql and "_seed_order" not in sql
        assert (
            "GROUP BY project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id"
            in sql
        )
        if "AS remaining_count" in sql:
            first = "cursor_before_start_us" not in params
            records = [(SESSION, newest), (sid2, oldest)] if first else [(sid2, oldest)]
            return SimpleNamespace(
                data=[
                    {
                        "session_id": sid,
                        "session_start": when,
                        "remaining_count": len(records),
                        "project_id": PROJECT,
                        "project_count": 1,
                        "max_project_count": 1,
                    }
                    for sid, when in records
                ]
            )
        if "sum(cost) AS total_cost" in sql:
            return SimpleNamespace(
                data=[
                    {
                        "session_id": sid,
                        "session_start": newest if sid == SESSION else oldest,
                        "session_end": end,
                        "duration": 60,
                        "total_cost": 1,
                        "total_tokens": 1,
                        "traces_count": 1,
                    }
                    for sid in params["candidate_session_ids"]
                ]
            )
        return SimpleNamespace(data=[])

    analytics = SimpleNamespace(execute_ch_query=execute)
    view._fetch_session_names = mock.Mock(return_value={})
    view._fetch_end_user_info = mock.Mock(return_value={})
    args = {
        "project_id": None if org_scope else PROJECT,
        "project": None,
        "org_project_ids": [PROJECT] if org_scope else None,
        "analytics": analytics,
    }
    with (
        mock.patch(
            "tracer.services.clickhouse.query_builders.base.BaseQueryBuilder.parse_time_range",
            return_value=(start, end),
        ),
        mock.patch(
            "tracer.views.trace_session.AnnotationsLabels.objects.filter",
            return_value=[],
        ),
        mock.patch("tracer.views.trace_session.read_bounded_filter_page") as bounded,
    ):
        status, first = TraceSessionView._list_sessions_clickhouse(
            view, request, validated_data=data, **args
        )
        assert status == "ok"
        token = first["metadata"]["next_cursor"]
        assert token and first["metadata"]["query_complete"] is True
        assert first["metadata"]["total_rows_is_lower_bound"] is False
        status, second = TraceSessionView._list_sessions_clickhouse(
            view, request, validated_data={**data, "cursor": token}, **args
        )
        assert status == "ok" and second["metadata"]["next_cursor"] is None
        assert [row["session_id"] for row in first["table"]] == [SESSION]
        assert [row["session_id"] for row in second["table"]] == [sid2]
        bounded.assert_not_called()
        cursor_calls = [p for sql, p in calls if "AS remaining_count" in sql]
        assert cursor_calls[1]["cursor_before_session_id"] == SESSION
        assert cursor_calls[1]["cursor_before_start_us"] % 1_000_000 == 123456
        # A validly signed token from the historical ordering contract must
        # fail before constructing/executing any new candidate statement.
        old = encode_list_cursor(
            resource="observe_sessions",
            scope=cursor_scope_for_request(request, project_ids=[PROJECT]),
            query=data,
            page_size=1,
            window_start=start,
            window_end=end,
            order=(start, sid2),
            seen_rows=1,
        )
        before = len(calls)
        with pytest.raises(ListCursorError):
            TraceSessionView._list_sessions_clickhouse(
                view, request, validated_data={**data, "cursor": old}, **args
            )
        assert len(calls) == before
