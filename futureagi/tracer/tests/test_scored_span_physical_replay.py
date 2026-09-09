"""Scored-span mappings and eval anchors against constant CH25 rows; no DDL."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.query_service import (
    AnalyticsQueryService,
    SpanTraceMapIntegrityError,
)

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
AT = datetime(2026, 8, 1, 12)
CONTEXT = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))


def row(**overrides):
    return {
        "project_id": PROJECT,
        "observation_type": "SPAN",
        "service_name": "svc",
        "trace_id": "trace-a",
        "id": "scored",
        "start_time": AT,
        "_version": 1,
        "is_deleted": 0,
    } | overrides


@pytest.fixture(scope="module")
def engine():
    return pytest.importorskip("chdb", reason="optional constant-fixture engine")


def execute_fixture(engine, rows, query, params):
    # Substitute constant rows without changing scope or physical replay.
    assert query.count("FROM spans") == 1
    query = query.replace("FROM spans", "FROM fixture_spans")
    if "PREWHERE" in query:
        # VALUES lacks PREWHERE. Preserve the anchor's conjunction exactly.
        assert query.count("PREWHERE") == query.count("WHERE id =") == 1
        query = query.replace("PREWHERE", "WHERE").replace("WHERE id =", "AND id =")
    rendered = query % escape_params(params, CONTEXT)
    schema = (
        "project_id UUID, observation_type String, service_name String, "
        "trace_id String, id String, start_time DateTime64(6, 'UTC'), "
        "_version UInt64, is_deleted UInt8"
    )
    values = [
        tuple(r[k].isoformat(sep=" ") if k == "start_time" else r[k] for k in row())
        for r in rows
    ]
    escaped = escape_params({"schema": schema, "rows": tuple(values)}, CONTEXT)
    sql = (
        f"WITH fixture_spans AS (SELECT * FROM values({escaped['schema']}, "
        f"{escaped['rows'][1:-1]})) {rendered} "
        "SETTINGS max_threads=1, max_execution_time=5, "
        "max_memory_usage=134217728, max_result_rows=100, "
        "max_result_bytes=1048576, result_overflow_mode='throw'"
    )
    return SimpleNamespace(
        data=[
            json.loads(line)
            for line in str(engine.query(sql, "JSONEachRow")).splitlines()
            if line
        ]
    )


def mapping(engine, rows, *, pairs=False, finite=True, start=None, end=None):
    service = AnalyticsQueryService()

    def execute(query, params=None, **_kwargs):
        return execute_fixture(engine, rows, query, params)

    service.execute_ch_query = execute
    traces = ["trace-a", "trace-b"]
    options = {"start_date": start, "end_date": end}
    if pairs:
        options.update(
            trace_identities=[(PROJECT, trace) for trace in traces],
            scored_span_identities=[(PROJECT, "scored")],
        )
    else:
        options.update(
            project_id=PROJECT, scored_span_ids=["scored"] if finite else None
        )
    return service.get_span_trace_map(traces, **options)


def latest_spans(rows):
    latest = {}
    for r in rows:
        key = (
            r["project_id"],
            r["observation_type"],
            r["service_name"],
            r["start_time"].replace(minute=0, second=0, microsecond=0),
            r["trace_id"],
            r["id"],
        )
        if key not in latest or latest[key]["_version"] < r["_version"]:
            latest[key] = r
    return latest.values()


def truth(rows, *, pairs=False):
    matches = {
        r["trace_id"]
        for r in latest_spans(rows)
        if r["project_id"] == PROJECT
        and r["id"] == "scored"
        and not r["is_deleted"]
        and r["trace_id"] in {"trace-a", "trace-b"}
    }
    if len(matches) > 1:
        raise ValueError("ambiguous fixture")
    if not matches:
        return {}
    trace = matches.pop()
    return {(PROJECT, "scored"): (PROJECT, trace)} if pairs else {"scored": trace}


@pytest.mark.parametrize("pairs", [False, True])
@pytest.mark.parametrize("minutes", [(-20, -10), (10, 20), (20, 10)])
def test_timestamp_corrected_tombstone_does_not_resurrect_annotation(
    engine, pairs, minutes
):
    rows = [
        row(start_time=AT + timedelta(minutes=minutes[0])),
        row(start_time=AT + timedelta(minutes=minutes[1]), _version=2, is_deleted=1),
    ]
    assert mapping(engine, rows, pairs=pairs) == truth(rows, pairs=pairs) == {}


@pytest.mark.parametrize("pairs", [False, True])
@pytest.mark.parametrize(
    "other_identity",
    [
        {"service_name": "other-service"},
        {"observation_type": "LLM"},
        {"start_time": AT + timedelta(hours=1)},
        {"project_id": OTHER},
    ],
)
def test_tombstone_cannot_delete_another_physical_identity(
    engine, pairs, other_identity
):
    rows = [row(), row(**other_identity, _version=2, is_deleted=1)]
    assert mapping(engine, rows, pairs=pairs) == truth(rows, pairs=pairs)


@pytest.mark.parametrize("pairs", [False, True])
def test_same_span_id_in_two_live_traces_is_ambiguous(engine, pairs):
    rows = [row(), row(trace_id="trace-b", _version=2)]
    with pytest.raises(ValueError, match="ambiguous fixture"):
        truth(rows, pairs=pairs)
    with pytest.raises(SpanTraceMapIntegrityError, match="ambiguous live traces"):
        mapping(engine, rows, pairs=pairs)


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("pairs", [False, True])
def test_finite_scored_identity_keeps_children_outside_root_window(engine, days, pairs):
    rows = [
        row(start_time=AT + timedelta(days=days + 5)),
        row(project_id=OTHER, trace_id="trace-b", _version=3),
    ]
    assert mapping(
        engine, rows, pairs=pairs, start=AT, end=AT + timedelta(days=days)
    ) == truth(rows, pairs=pairs)


@pytest.mark.parametrize(
    "edge,old_minute,new_minute,present",
    [
        ("start", 10, 20, True),
        ("start", 20, 10, False),
        ("start", 10, 15, True),
        ("start", 15, 10, False),
        ("end", 10, 20, False),
        ("end", 20, 10, True),
        ("end", 10, 15, False),
        ("end", 15, 10, True),
    ],
)
def test_generic_window_applies_after_timestamp_correction(
    engine, edge, old_minute, new_minute, present
):
    rows = [
        row(start_time=AT + timedelta(minutes=old_minute)),
        row(start_time=AT + timedelta(minutes=new_minute), _version=2),
    ]
    # The generic compatibility lane widens by one day, unlike scored IDs.
    if edge == "start":
        start = AT + timedelta(days=1, minutes=15)
        end = start + timedelta(days=7)
    else:
        end = AT + timedelta(days=-1, minutes=15)
        start = end - timedelta(days=7)
    expected = {"scored": "trace-a"} if present else {}
    assert mapping(engine, rows, finite=False, start=start, end=end) == expected


@pytest.mark.parametrize(
    "other_identity",
    [
        {"service_name": "other-service"},
        {"observation_type": "LLM"},
        {"start_time": AT + timedelta(hours=1)},
        {"trace_id": "trace-b"},
        {"project_id": OTHER},
        {"start_time": AT + timedelta(minutes=10)},
    ],
)
@pytest.mark.parametrize("deleted", [0, 1])
@pytest.mark.parametrize("reverse", [False, True])
def test_eval_anchor_requires_one_live_physical_span(
    engine, other_identity, deleted, reverse
):
    rows = [row(), row(**other_identity, _version=2, is_deleted=deleted)]
    if reverse:
        rows.reverse()
    assert_eval_anchor(engine, rows)


@pytest.mark.parametrize(
    "absent",
    [
        {"project_id": OTHER},
        {"id": "unrelated-span"},
        {"is_deleted": 1},
    ],
)
def test_eval_anchor_does_not_read_eval_without_authorized_live_span(engine, absent):
    assert_eval_anchor(engine, [row(**absent)])


def assert_eval_anchor(engine, rows):
    live = [
        r
        for r in latest_spans(rows)
        if r["project_id"] == PROJECT and r["id"] == "scored" and not r["is_deleted"]
    ]
    eval_calls = []
    service = AnalyticsQueryService()

    def execute(query, params=None, **_kwargs):
        if "FROM spans" in query:
            return execute_fixture(engine, rows, query, params)
        # The eval payload is mocked: these tests qualify native span anchoring
        # and binding only, not eval-table or HTTP/UI behavior.
        assert len(live) == 1, "eval lookup must not follow an ambiguous/deleted anchor"
        assert params == {
            "span_id": "scored",
            "config_id": "config",
            "trace_id": live[0]["trace_id"],
        }
        assert "eval_scan.trace_id = toUUID(%(trace_id)s)" in query
        assert "eval_scan.observation_span_id = %(span_id)s" in query
        assert "eval_scan.custom_eval_config_id = %(config_id)s" in query
        eval_calls.append(params)
        return SimpleNamespace(data=[{"output_bool": True}])

    service.execute_ch_query = execute
    result = service.get_eval_detail_ch(
        "scored",
        "config",
        project_id=PROJECT,
        eval_logger_table="tracer_eval_logger_v2",
    )
    assert result == ({"output_bool": True} if len(live) == 1 else None)
    assert len(eval_calls) == int(len(live) == 1)
