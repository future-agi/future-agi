"""Single-leaf session graph witness and fail-closed fallback contracts."""

import re
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver.errors import ErrorCodes, ServerException

from tracer.services.clickhouse import exact_graph_reads as graph

pytestmark = pytest.mark.unit
PROJECT = "22222222-2222-4222-8222-222222222222"
START = datetime(2026, 8, 28, 22, 36, 52)
END = datetime(2026, 9, 5, 6, 59, 59)


def _leaf(op="in", value=None, kind="text", key="company_id"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": op,
            "filter_value": ["10000001", "10000002"] if value is None else value,
        },
    }


def _filters(*leaves):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START, END],
            },
        },
        *(leaves or [_leaf()]),
    ]


def _source(filters=None, enabled=True):
    return graph._session_aggregate_source_sql(
        project_id=PROJECT,
        filters=filters or _filters(),
        start_date=START,
        end_date=END,
        include_trace_ids=False,
        anchor_by_session_start=True,
        use_scalar_witness=enabled,
    )


def test_witness_only_prunes_complete_physical_identity_replay():
    sql, params = _source()
    witness = sql.split("session_scalar_witness_ids AS (", 1)[1].split(
        "latest_session_filter_spans AS (", 1
    )[0]
    replay = sql.split("latest_session_filter_spans AS (", 1)[1].split(
        "resolved_session_filter_spans AS (", 1
    )[0]
    assert "FROM spans" in witness and "FINAL" not in witness
    assert "LIMIT" not in witness and "SAMPLE" not in sql
    assert "trace_session_id" not in witness and "is_deleted" not in witness
    assert "lowerUTF8(toString(attrs_string[" in witness
    witness_params = set(re.findall(r"%\(([^)]+)\)s", witness))
    assert witness_params <= params.keys()
    assert any(
        name.startswith("session_scalar_latest_filter_param_")
        and params[name] == ("10000001", "10000002")
        for name in witness_params
    )
    for field in ("project_id", "observation_type", "service_name", "trace_id", "id"):
        assert field in witness and field in replay
    assert "toStartOfHour(start_time) AS physical_hour" in witness
    assert "physical_hour, trace_id, id" in replay
    assert "FROM session_scalar_witness_ids" in replay
    assert "lowerUTF8" not in replay
    assert "argMax(is_deleted, _version)" in replay
    assert "argMax(tuple(trace_session_id), _version).1" in replay
    assert "argMax(start_time, _version)" in replay
    assert params["snapshot_scan_start_date"] == START.replace(minute=0, second=0)
    assert params["snapshot_scan_end_date"] == END.replace(
        minute=0, second=0
    ) + timedelta(hours=1)
    assert "latest_start_time >= fromUnixTimestamp64Micro(%(snapshot_start_date_us)s)" in sql
    assert "latest_start_time < fromUnixTimestamp64Micro(%(snapshot_end_date_us)s)" in sql

    original, _ = _source(enabled=False)
    # Neither root discovery, complete alias groups, nor all-root metrics is
    # pruned by a physical attribute value.
    assert (
        sql.split("session_scalar_witness_ids AS (", 1)[0]
        == original.split("latest_session_filter_spans AS (", 1)[0]
    )
    assert (
        sql.split("resolved_session_filter_spans AS (", 1)[1]
        == original.split("resolved_session_filter_spans AS (", 1)[1]
    )


@pytest.mark.parametrize(
    "leaf",
    [
        _leaf("not_in"),
        _leaf("not_equals", "10000001"),
        _leaf("is_null"),
        _leaf("is_not_null"),
        _leaf("contains", ["10000001"], "ARRAY"),
        _leaf("not_contains", ["10000001"], "ARRAY"),
        _leaf("contains", {"company": "10000001"}, "MAP"),
    ],
)
def test_non_positive_scalar_contracts_do_not_use_witness(leaf):
    sql, _ = _source(_filters(leaf))
    assert "session_scalar_witness_ids" not in sql


def test_multiple_leaves_keep_sibling_span_intersection_unpruned():
    sql, _ = _source(_filters(_leaf(), _leaf(key="prompt_slug")))
    assert "session_scalar_witness_ids" not in sql
    assert sql.count("countIf(") == 2
    assert "GROUP BY session_id" in sql


def test_group_exclusion_rejected_even_if_compiler_exposes_witness(monkeypatch):
    real = graph.partition_span_filter_plans

    def compile_with_exclusion(filters, **kwargs):
        plans, residual = real(filters, **kwargs)
        return [replace(plan, exclude_group_matches=True) for plan in plans], residual

    monkeypatch.setattr(graph, "partition_span_filter_plans", compile_with_exclusion)
    assert "session_scalar_witness_ids" not in _source()[0]


@pytest.mark.parametrize("kind,value", [("number", 0), ("boolean", False)])
def test_missing_map_defaults_never_get_unsafe_value_witness(kind, value):
    plan = graph._session_membership_plan(
        project_id=PROJECT, filters=[_leaf("equals", value, kind)]
    )
    assert plan.scalar_witness_predicate is None


class _Analytics:
    def __init__(self, errors=(), empty=False):
        self.errors = list(errors)
        self.calls = []
        self.empty = empty

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, dict(params), timeout_ms, dict(settings)))
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        return SimpleNamespace(
            data=[]
            if self.empty
            else [
                {
                    "time_bucket": START.replace(hour=0, minute=0, second=0),
                    "value": 17,
                    "primary_traffic": 2,
                }
            ],
            columns=["time_bucket", "value", "primary_traffic"],
        )


def _read(analytics, filters=None):
    return graph.read_exact_session_system_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=filters or _filters(),
        interval="day",
        metric_id="latency",
    )


def test_success_is_one_complete_statement_and_caps_only_tighten(monkeypatch):
    original = dict(graph.EXACT_GRAPH_READ_SETTINGS)
    monkeypatch.setitem(graph.EXACT_GRAPH_READ_SETTINGS, "max_bytes_to_read", 1024)
    monkeypatch.setitem(graph.EXACT_GRAPH_READ_SETTINGS, "max_rows_in_set", 20)
    analytics = _Analytics()
    result = _read(analytics)
    assert result["query_count"] == 1
    assert result["query_complete"] is True and result["query_sampled"] is False
    assert len(analytics.calls) == 1
    _, _, timeout, settings = analytics.calls[0]
    assert 0 < timeout <= graph._SESSION_SCALAR_WITNESS_TIMEOUT_MS
    assert settings["max_bytes_to_read"] == 1024
    assert settings["max_rows_in_set"] == 20
    assert settings["set_overflow_mode"] == "throw"
    assert settings["result_overflow_mode"] == "throw"
    assert settings["max_memory_usage"] == original["max_memory_usage"]
    assert settings["max_threads"] == original["max_threads"]


@pytest.mark.parametrize(
    "code",
    [
        ErrorCodes.TIMEOUT_EXCEEDED,
        ErrorCodes.TOO_MANY_BYTES,
        ErrorCodes.SET_SIZE_LIMIT_EXCEEDED,
        ErrorCodes.MEMORY_LIMIT_EXCEEDED,
    ],
)
def test_budget_failure_discards_witness_and_runs_original_exact_query(code):
    analytics = _Analytics([ServerException("bounded witness", code=code)])
    result = _read(analytics)
    assert len(analytics.calls) == 2 and result["query_count"] == 2
    assert "session_scalar_witness_ids" in analytics.calls[0][0]
    fallback, params, _, settings = analytics.calls[1]
    original_source, _ = _source(enabled=False)
    assert original_source in fallback
    assert "session_scalar_witness_ids" not in fallback
    assert params["snapshot_start_date"] == START
    assert params["snapshot_end_date"] == END
    assert settings == graph.EXACT_GRAPH_READ_SETTINGS
    assert result["query_complete"] is True
    assert any(point["value"] == 17 for point in result["data"])


def test_fallback_does_not_receive_a_new_wall(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(graph, "monotonic", lambda: clock[0])

    class SlowWitness(_Analytics):
        def execute_ch_query(self, *args, **kwargs):
            if not self.calls:
                clock[0] += 9
            return super().execute_ch_query(*args, **kwargs)

    analytics = SlowWitness([ServerException("timeout", code=159)])
    _read(analytics)
    assert analytics.calls[1][2] == graph.EXACT_GRAPH_QUERY_TIMEOUT_MS - 9_000


def test_programming_error_is_not_swallowed_or_retried():
    analytics = _Analytics([ServerException("unknown identifier", code=47)])
    with pytest.raises(ServerException, match="unknown identifier"):
        _read(analytics)
    assert len(analytics.calls) == 1


def test_failed_fallback_cannot_publish_empty_success():
    analytics = _Analytics([ServerException("timeout", code=159)] * 2)
    with pytest.raises(ServerException):
        _read(analytics)
    assert len(analytics.calls) == 2


def test_exhausted_empty_witness_is_complete_not_a_budget_failure():
    analytics = _Analytics(empty=True)
    result = _read(analytics)
    assert len(analytics.calls) == 1
    assert result["query_complete"] is True
    assert all(point["primary_traffic"] == 0 for point in result["data"])


@pytest.fixture
def physical_session_rows():
    """Reference fixtures paired with the six-key generated-SQL contract above."""

    def row(**changes):
        return {
            "project": PROJECT,
            "type": "span",
            "service": "agent",
            "trace": "child-trace",
            "id": "child",
            "start": START + timedelta(seconds=10),
            "version": 1,
            "deleted": False,
            "session": "new-ab",
            "company": "10000001",
            "root": False,
            "latency": 0,
            **changes,
        }

    roots = [
        row(
            id="root-a", trace="a", session="old-a", company=None, root=True, latency=10
        ),
        row(
            id="root-b", trace="b", session="old-b", company=None, root=True, latency=30
        ),
        row(
            id="root-c",
            trace="c",
            session="other-c",
            company=None,
            root=True,
            latency=50,
        ),
    ]
    return row, roots


@pytest.mark.parametrize(
    "change,expected",
    [
        ({}, {"old-a": 20}),
        ({"company": "unselected"}, {}),
        ({"deleted": True, "company": None}, {}),
        ({"session": "other-c"}, {"other-c": 50}),
        ({"session": None}, {}),
        ({"session": "00000000-0000-0000-0000-000000000000"}, {}),
        ({"start": START - timedelta(seconds=1)}, {}),
        ({"start": START + timedelta(minutes=1)}, {"old-a": 20}),
        ({"service": "other", "deleted": True}, {"old-a": 20}),
        ({"type": "log", "deleted": True}, {"old-a": 20}),
        ({"project": "other-tenant", "deleted": True}, {"old-a": 20}),
        ({"start": START + timedelta(hours=1), "deleted": True}, {"old-a": 20}),
    ],
)
def test_physical_witness_fixture_matches_unpruned_membership_and_all_roots(
    physical_session_rows, change, expected
):
    row, roots = physical_session_rows
    rows = [*roots, row(), row(version=2, **change)]

    def identity(item):
        return (
            item["project"],
            item["type"],
            item["service"],
            item["start"].replace(minute=0, second=0, microsecond=0),
            item["trace"],
            item["id"],
        )

    def company_matches(item):
        return item["company"] in {"10000001", "10000002"}

    scan_start = START.replace(minute=0, second=0, microsecond=0)
    scan_end = END.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    physical = [
        item
        for item in rows
        if item["project"] == PROJECT and scan_start <= item["start"] < scan_end
    ]
    witness_ids = {identity(item) for item in physical if company_matches(item)}

    def latest(source):
        winners = {}
        for item in source:
            key = identity(item)
            if key not in winners or winners[key]["version"] < item["version"]:
                winners[key] = item
        return [
            item
            for item in winners.values()
            if not item["deleted"]
            and START <= item["start"] < END
            and item["session"] not in {None, "00000000-0000-0000-0000-000000000000"}
        ]

    aliases = dict.fromkeys(("old-a", "old-b", "new-ab"), "old-a")

    def canonical(item):
        return aliases.get(item["session"], item["session"])

    all_live_roots = [item for item in latest(physical) if item["root"]]

    def graph_values(source):
        selected = {canonical(item) for item in latest(source) if company_matches(item)}
        return {
            session: sum(
                item["latency"] for item in all_live_roots if canonical(item) == session
            )
            / len([item for item in all_live_roots if canonical(item) == session])
            for session in selected & {canonical(item) for item in all_live_roots}
        }

    assert graph_values(physical) == expected
    assert (
        graph_values([item for item in physical if identity(item) in witness_ids])
        == expected
    )
