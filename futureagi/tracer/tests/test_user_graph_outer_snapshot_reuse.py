"""Offline contracts for opt-in shared outer user snapshot/remap context."""

import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import exact_graph_reads as graph

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
NIL = "00000000-0000-0000-0000-000000000000"
START = datetime(2026, 9, 1, 12, 15, 0, 123456)
END = datetime(2026, 9, 2, 14, 30, 0, 654321)


def _filter(key, value=None, *, kind="text", op="equals", source="SPAN_ATTRIBUTE"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": source,
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        },
    }


def _dates():
    return _filter(
        "created_at",
        [START.isoformat(), END.isoformat()],
        kind="datetime",
        op="between",
        source="SYSTEM_METRIC",
    )


def _source(*, filters=None, **kwargs):
    return graph._user_aggregate_source_sql(
        project_id=PROJECT,
        filters=filters or [],
        start_date=START,
        end_date=END,
        include_trace_ids=False,
        **kwargs,
    )


def _cte(sql, name):
    start = re.search(r"\b" + re.escape(name) + r" AS \(", sql).end()
    depth = 1
    for end in range(start, len(sql)):
        depth += (sql[end] == "(") - (sql[end] == ")")
        if depth == 0:
            return " ".join(sql[start:end].split())
    raise AssertionError("unclosed CTE")


@pytest.mark.parametrize("with_session_filter", [False, True])
def test_reused_source_has_no_shadowing_population_or_user_remap(with_session_filter):
    filters = [_filter("company_id", "company")]
    if with_session_filter:
        filters.append(
            _filter(
                "num_sessions",
                2,
                kind="number",
                op="greater_than",
                source="SYSTEM_METRIC",
            )
        )
    sql, params, needs_eval = _source(
        filters=filters, all_snapshot_users=True, reuse_outer_snapshot=True
    )
    assert "FROM latest_spans" in _cte(sql, "candidate_user_spans")
    assert "FROM spans" not in sql
    assert "FROM end_user_id_remap" not in sql
    assert not re.search(r"\b(?:latest_spans|eu_survivor_map) AS \(", sql)
    assert "candidate_physical_end_user_ids AS" not in sql
    assert "candidate_physical_users AS" not in sql
    assert "candidate_users AS" not in sql
    assert "candidate_end_user_remap" not in sql
    assert "LEFT JOIN eu_survivor_map AS span_eu_remap" in sql
    assert "LEFT JOIN eu_survivor_map AS eu_remap" in sql
    assert "candidate_user_session_ids AS" in sql
    assert "FROM trace_session_id_remap FINAL" in sql
    assert "AS num_sessions" in _cte(sql, "user_span_metrics")
    assert "GROUP BY end_user_id HAVING" in _cte(sql, "user_span_metrics")
    assert "SUM" not in _cte(sql, "candidate_user_spans").upper()
    assert not needs_eval and params["project_id"] == PROJECT


@pytest.mark.parametrize(
    "scope",
    [
        {},
        {"candidate_trace_ids_param": "trace_ids"},
        {"candidate_trace_ids_sql": "SELECT 'trace'"},
        {"all_snapshot_users": True, "candidate_trace_ids_param": "trace_ids"},
    ],
)
def test_reuse_requires_full_snapshot_and_rejects_partition_context(scope):
    with pytest.raises(ValueError):
        _source(reuse_outer_snapshot=True, **scope)


def test_trace_array_callers_cannot_opt_into_outer_system_graph_context():
    with pytest.raises(ValueError):
        graph._user_aggregate_source_sql(
            project_id=PROJECT,
            filters=[],
            start_date=START,
            end_date=END,
            include_trace_ids=True,
            all_snapshot_users=True,
            reuse_outer_snapshot=True,
        )


@pytest.mark.parametrize("full", [False, True])
def test_standalone_main_branch_keeps_full_hour_replay_and_candidate_contract(full):
    scope = (
        {"all_snapshot_users": True}
        if full
        else {"candidate_trace_ids_param": "trace_ids"}
    )
    sql, params, _ = _source(**scope)
    assert sql.count("SELECT * FROM spans FINAL") == 2
    snapshot = _cte(sql, "candidate_user_spans")
    physical, fenced = snapshot.split(") AS physical", 1)
    physical = physical.split("FROM spans FINAL PREWHERE", 1)[1]
    assert "toStartOfHour(start_time) >= %(user_membership_scan_start)s" in physical
    assert "toStartOfHour(start_time) < %(user_membership_scan_end)s" in physical
    assert "is_deleted" not in physical and "end_user_id" not in physical
    assert "user_membership_start_us" not in physical
    assert (
        "ARRAY JOIN [tuple(physical.start_time, physical.is_deleted, physical.end_user_id, physical.trace_session_id)] AS latest_membership"
        in fenced
    )
    assert "FROM latest_spans" not in sql
    assert "eu_survivor_map AS" in sql
    assert "FROM end_user_id_remap FINAL" in sql
    assert params["user_membership_scan_start"] == datetime(2026, 9, 1, 12)
    assert params["user_membership_scan_end"] == datetime(2026, 9, 2, 15)
    assert "fromUnixTimestamp64Micro(%(user_membership_start_us)s, 'UTC')" in sql
    assert ("candidate_physical_users AS" in sql) is not full
    assert ("IN (SELECT end_user_id FROM candidate_users)" in sql) is not full


def test_shared_source_matches_outer_physical_domain_and_rejects_canonical_nil():
    sql, _, _ = _source(all_snapshot_users=True, reuse_outer_snapshot=True)
    population = _cte(sql, "candidate_user_spans")
    assert "isNotNull(end_user_id)" in population
    # Outer graph excludes NULL before grouping, but can remap a raw NIL via
    # another live alias's touched group. Do not drop those rows only here.
    assert "end_user_id !=" not in population
    assert f"AND usm.end_user_id != toUUID('{NIL}')" in _cte(sql, "user_rows")
    dimension = _cte(sql, "user_dimensions_raw")
    assert "eu.project_id = toUUID(%(project_id)s)" in dimension
    assert "eu.is_deleted = 0" in dimension and "notEmpty(eu.user_id)" in dimension
    assert "tuple(physical_end_user_id = end_user_id, version)" in _cte(
        sql, "user_dimensions"
    )
    assert "INNER JOIN user_dimensions" in _cte(sql, "user_rows")


def test_id_wrapper_forwards_the_opt_in_without_defining_shared_names():
    sql, _, _ = graph._user_id_membership_sql(
        project_id=PROJECT,
        filters=[],
        start_date=START,
        end_date=END,
        all_snapshot_users=True,
        reuse_outer_snapshot=True,
    )
    assert sql.startswith("SELECT end_user_id FROM (")
    assert "FROM latest_spans" in sql and "FROM spans" not in sql


class _RecordingAnalytics:
    def __init__(self):
        self.calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, dict(params), dict(settings)))
        return SimpleNamespace(
            data=[], columns=["time_bucket", "value", "primary_traffic"]
        )


@pytest.mark.parametrize("explicit_window", [False, True])
def test_full_system_reader_uses_one_outer_definition_and_frozen_bounds(
    monkeypatch, explicit_window
):
    filters = [_filter("company_id", "company")]
    if explicit_window:
        filters.insert(0, _dates())
    monkeypatch.setattr(graph, "_snapshot_window", lambda _filters: (START, END, False))
    analytics = _RecordingAnalytics()
    result = graph.read_exact_user_system_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=filters,
        interval="hour",
        metric_id="active_users",
    )
    assert len(analytics.calls) == 1
    sql, params, settings = analytics.calls[0]
    assert len(re.findall(r"\blatest_spans AS \(", sql)) == 1
    assert len(re.findall(r"\beu_survivor_map AS \(", sql)) == 1
    assert sql.count("FROM spans FINAL") == 1
    assert "candidate_trace_ids AS" not in sql
    assert "candidate_end_user_remap" not in sql
    assert params["start_date"] == params["snapshot_start_date"] == START
    assert params["end_date"] == params["snapshot_end_date"] == END
    assert "WHERE snapshot_spans.is_deleted = 0" in sql
    assert settings["optimize_move_to_prewhere_if_final"] == 0
    assert settings["use_skip_indexes_if_final"] == 0
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys()
    assert result["query_complete"] and not result["query_sampled"]


def test_eval_membership_keeps_internal_trace_array_and_eval_reduction(monkeypatch):
    owned_config_ids = ("22222222-2222-4222-8222-222222222222",)
    ownership_lookups = []

    def owned_configs(**lookup):
        assert lookup == {"project_id": PROJECT, "deleted": False}
        ownership_lookups.append(lookup)

        def values_list(*fields, flat):
            assert fields == ("id",) and flat is True
            return owned_config_ids

        return SimpleNamespace(values_list=values_list)

    monkeypatch.setattr(
        graph.CustomEvalConfig.no_workspace_objects, "filter", owned_configs
    )
    monkeypatch.setattr(
        graph,
        "_user_filter_clauses",
        lambda *_args, **_kwargs: ("1 = 1", "avg_output_float > 1", {}, True),
    )
    monkeypatch.setattr(
        graph, "_user_membership_having", lambda *_args, **_kwargs: ((), "1 = 1", {})
    )
    sql, params, needs_eval = _source(
        all_snapshot_users=True, reuse_outer_snapshot=True, started=graph.monotonic()
    )
    assert needs_eval
    assert "groupUniqArray(trace_id) AS user_trace_ids" in _cte(
        sql, "user_span_metrics"
    )
    assert "arrayJoin(user_trace_ids) AS trace_id" in _cte(sql, "user_eval_metrics")
    assert ownership_lookups == [{"project_id": PROJECT, "deleted": False}]
    assert "eval_scan.custom_eval_config_id IN %(user_eval_config_ids)s" in _cte(
        sql, "user_eval_metrics"
    )
    assert params["user_eval_config_ids"] == owned_config_ids
    assert params["project_id"] == PROJECT
    assert "LEFT JOIN user_eval_metrics AS ue" in _cte(sql, "user_rows")
    assert "avg_output_float > 1" in sql


@pytest.mark.parametrize(
    "state",
    [
        "live",
        "tombstone",
        "moves-out",
        "reassigned",
        "missing-dimension",
        "deleted-dimension",
        "unmapped",
        "null",
        "nil",
        "mapped-nil",
    ],
)
def test_fixture_latest_alias_membership_preserves_split_span_witnesses_and_all_metrics(
    state,
):
    """Reference fixture, not a SQL interpreter; SQL order is asserted above."""
    sql, _, _ = _source(
        filters=[_filter("a", "x"), _filter("b", "y")],
        all_snapshot_users=True,
        reuse_outer_snapshot=True,
    )
    assert "countIf" in _cte(sql, "user_span_metrics")
    assert "latest_spans" in _cte(sql, "candidate_user_spans")
    remap = {"old-b": "old-a", "new": "old-a", "old-a": "old-a"}
    original = {
        "id": "span-a",
        "trace": "trace-a",
        "version": 1,
        "user": "old-b",
        "start": START + timedelta(minutes=1),
        "deleted": False,
        "a": True,
        "b": False,
        "cost": 2,
    }
    second = {
        **original,
        "id": "span-b",
        "trace": "trace-b",
        "user": "new",
        "a": False,
        "b": True,
        "cost": 3,
    }
    updated = {**second, "version": 2}
    if state == "tombstone":
        updated["deleted"] = True
    elif state == "moves-out":
        updated["start"] = START - timedelta(minutes=1)
    elif state == "reassigned":
        updated["user"] = "other-user"
    elif state in {"unmapped", "null", "nil", "mapped-nil"}:
        uid = {"unmapped": "solo", "null": None, "nil": NIL, "mapped-nil": NIL}[state]
        original["user"] = updated["user"] = uid
        if state == "mapped-nil":
            # The outer map can include NIL only via a group touched by
            # another valid alias; both consumers must count it identically.
            remap[NIL] = "old-a"
    rows = [original, second, updated]
    latest = {}
    for row in rows:
        identity = (
            row["trace"],
            row["id"],
            row["start"].replace(minute=0, second=0, microsecond=0),
        )
        if identity not in latest or row["version"] > latest[identity]["version"]:
            latest[identity] = row
    dims = (
        set()
        if state in {"missing-dimension", "deleted-dimension"}
        else {"old-a", "solo", NIL}
    )
    groups = {}
    for row in latest.values():
        if row["deleted"] or row["user"] is None or not START <= row["start"] < END:
            continue
        canonical = remap.get(row["user"], row["user"])
        groups.setdefault(canonical, []).append(row)
    selected = {
        uid: sum(row["cost"] for row in values)
        for uid, values in groups.items()
        if uid != NIL
        and uid in dims
        and all(any(row[leaf] for row in values) for leaf in ("a", "b"))
    }
    expected = {
        "unmapped": {"solo": 5},
        "live": {"old-a": 5},
        "mapped-nil": {"old-a": 5},
    }.get(state, {})
    assert selected == expected


def test_trace_membership_fences_mutable_candidate_fields_after_final():
    sql, params, _ = graph._user_trace_membership_sql(
        project_id=PROJECT,
        filters=[],
        start_date=START,
        end_date=END,
        candidate_trace_ids_param="trace_ids",
    )
    candidate = _cte(sql, "candidate_members")
    physical, fenced = candidate.split(") AS physical", 1)
    physical = physical.split("FROM spans FINAL PREWHERE", 1)[1]
    assert "toStartOfHour(start_time) >= %(user_membership_scan_start)s" in physical
    assert "toStartOfHour(start_time) < %(user_membership_scan_end)s" in physical
    assert "snapshot_start_date" not in physical and "snapshot_end_date" not in physical
    assert "is_deleted" not in physical and "end_user_id" not in physical
    assert (
        "ARRAY JOIN [tuple(physical.start_time, physical.is_deleted, physical.end_user_id, physical.trace_session_id)] AS latest_membership"
        in fenced
    )
    assert "candidate_member.start_time >= %(snapshot_start_date)s" in fenced
    assert "candidate_member.start_time < %(snapshot_end_date)s" in fenced
    assert "candidate_member.is_deleted = 0" in fenced
    assert "isNotNull(candidate_member.end_user_id)" in fenced
    assert params["snapshot_start_date"] == START and params["snapshot_end_date"] == END
