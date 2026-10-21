"""Graph membership must not resurrect a superseded physical span version."""
# Pytest injects the imported, reusable engine fixture by its name.
# ruff: noqa: F811

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import exact_graph_reads as graphs
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_exact_graph_filter_predicates,
)
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    engine,  # noqa: F401 - reusable real six-key RMT fixture
)


@pytest.mark.integration
@pytest.mark.parametrize(
    "member_path", [False, True], ids=["session-roots", "session-members"]
)
@pytest.mark.parametrize(
    "replacement",
    [
        {"start_time": START + timedelta(minutes=10)},
        {"start_time": START + timedelta(minutes=40)},
        {"is_deleted": 1},
        {"trace_session_id": None},
        {"trace_session_id": "00000000-0000-0000-0000-000000000000"},
        {"parent_span_id": "parent"},
    ],
    ids=[
        "before-window",
        "after-window",
        "deleted",
        "session-null",
        "session-zero",
        "root-to-child",
    ],
)
def test_session_graph_excludes_corrected_physical_versions(
    engine, member_path, replacement
):
    run, insert = engine
    run("ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)")
    run(
        "CREATE TABLE trace_session_id_remap (old_id UUID, new_id UUID, version DateTime64(6, 'UTC')) ENGINE=ReplacingMergeTree(version) ORDER BY old_id"
    )
    run(
        "SET use_skip_indexes_if_final=0, enable_optimize_predicate_expression=1, enable_optimize_predicate_expression_to_final_subquery=1"
    )
    session = "33333333-3333-3333-3333-333333333333"
    start, end = START + timedelta(minutes=15), START + timedelta(minutes=30)
    common = {"trace_session_id": session, "latency_ms": 10, "status": "OK"}
    insert(**common, id="stable-root", trace_id="stable-trace")
    old = {
        **common,
        "id": "moved",
        "trace_id": "moved-trace",
        "parent_span_id": "parent" if member_path else None,
        "latency_ms": 90,
    }
    insert(**old)
    insert(**{**old, **replacement, "_version": 2})
    insert(**{**old, "project_id": OTHER_PROJECT, "_version": 99})
    winners = run(
        "SELECT project_id, trace_id, trace_session_id, start_time, is_deleted, parent_span_id, latency_ms FROM spans FINAL"
    )
    selected = [
        r
        for r in winners
        if r["project_id"] == PROJECT
        and not r["is_deleted"]
        and r["trace_session_id"] == session
        and start <= r["start_time"] < end
        and (member_path or not r["parent_span_id"])
    ]
    assert "stable-trace" in {r["trace_id"] for r in selected}
    kwargs = {
        "project_id": PROJECT,
        "filters": [],
        "start_date": start,
        "end_date": end,
        "candidate_trace_ids_param": "candidate_traces",
    }
    if member_path:
        sql, params = graphs._session_trace_membership_sql(**kwargs)
    else:
        sql, params = graphs._session_aggregate_source_sql(
            include_trace_ids=True, **kwargs
        )
    rows = run(sql, {**params, "candidate_traces": ("stable-trace", "moved-trace")})
    if member_path:
        assert {r["trace_id"] for r in rows} == {r["trace_id"] for r in selected}
    else:
        assert len(rows) == 1
        assert rows[0]["session_traces"] == len(selected)
        assert rows[0]["session_avg_latency"] == sum(
            r["latency_ms"] for r in selected
        ) / len(selected)


@pytest.mark.integration
@pytest.mark.parametrize(
    "path", ["structured_trace", "trace_match", "span_match", "annotation_span_owner", "span_partition"]
)
@pytest.mark.parametrize(
    "replacement",
    [
        {"start_time": START + timedelta(minutes=10)},
        {"start_time": START + timedelta(minutes=40)},
        {"is_deleted": 1},
        {"attrs_number": {}, "attributes_extra": "{}"},
        {"attrs_number": {"score": 0}, "attributes_extra": '{"flags":["other"]}'},
    ],
    ids=["before-window", "after-window", "deleted", "cleared", "changed"],
)
def test_graph_membership_uses_complete_physical_winners(engine, path, replacement):
    run, insert = engine
    start, end = START + timedelta(minutes=15), START + timedelta(minutes=30)
    run(
        "SET use_skip_indexes_if_final=0, enable_optimize_predicate_expression=1, "
        "enable_optimize_predicate_expression_to_final_subquery=1"
    )
    original = {
        "id": "moved",
        "trace_id": "trace-moved",
        "attrs_number": {"score": 1},
        "attributes_extra": '{"flags":["target"]}',
    }
    insert(**original)
    # The fixture stops merges; separate INSERTs retain both competing parts.
    insert(**{**original, "_version": 2, **replacement})
    insert(**{**original, "project_id": OTHER_PROJECT, "_version": 99})

    class Reader:
        def execute_ch_query(self, sql, params, **kwargs):
            return SimpleNamespace(data=run(sql, params))

    for positive in (False, True):
        if positive:
            insert(**{**original, "id": "stable", "trace_id": "trace-stable"})
            if path == "span_partition":
                # Each high-version tombstone differs in exactly one key coordinate.
                for collision in (
                    {"project_id": OTHER_PROJECT}, {"observation_type": ""},
                    {"service_name": ""}, {"start_time": START + timedelta(hours=1, minutes=20)},
                    {"trace_id": "collision"}, {"id": "collision"},
                ):
                    insert(**{**original, "id": "stable", "trace_id": "trace-stable",
                              "_version": 99, "is_deleted": 1, **collision})
        # Independent materialization: no candidate time/value/deletion filter
        # can discard a physical version before this whole-table FINAL read.
        winners = run(
            "SELECT project_id, id, trace_id, start_time, is_deleted, "
            "attrs_number, attributes_extra FROM spans FINAL"
        )
        expected = set()
        for row in winners:
            if (
                row["project_id"] != PROJECT
                or row["is_deleted"]
                or not start <= row["start_time"] < end
            ):
                continue
            if path == "structured_trace":
                matches = "target" in json.loads(row["attributes_extra"]).get(
                    "flags", []
                )
            else:
                matches = (
                    path == "annotation_span_owner"
                    or row["attrs_number"].get("score") == 1
                )
            if matches:
                expected.add(
                    row["id" if path in {"span_match", "span_partition"} else "trace_id"]
                )
        if path == "span_partition":
            leaf = {"column_id": "score", "filter_config": {
                "col_type": "SPAN_ATTRIBUTE", "filter_type": "number",
                "filter_op": "equals", "filter_value": 1}}
            builder = TimeSeriesQueryBuilder(project_id=PROJECT, filters=[leaf], interval="day",
                observe_type="span", exact_snapshot=True, start_date=start, end_date=end)
            sql, params = builder.build_exact_span_partition(
                partition_start=START, partition_end=START + timedelta(hours=2))
            key = "project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id"
            assert f"AND ({key}) IN (" in sql and f"SELECT {key}" in sql
            assert sum(int(row["traffic_count"]) for row in run(sql, params)) == len(expected)
            continue
        common = {
            "analytics": Reader(),
            "project_id": PROJECT,
            "start_date": start,
            "end_date": end,
            "predicate": "mapContains(attrs_number, 'score') AND attrs_number['score'] = 1",
            "predicate_params": {},
            "timeout_ms": 10000,
            "settings": {},
        }
        params = {
            "project_id": PROJECT,
            "snapshot_start_date": start,
            "snapshot_end_date": end,
        }
        if path == "trace_match":
            actual = graphs._matching_trace_ids(
                trace_ids=("trace-moved", "trace-stable"), **common
            )
        elif path == "span_match":
            actual = graphs._matching_span_ids(span_ids=("moved", "stable"), **common)
        elif path == "annotation_span_owner":
            data = run(
                graphs._span_batch_trace_ids_sql(),
                {
                    **params,
                    "candidate_span_ids": ("moved", "stable"),
                },
            )
            actual = {str(next(iter(row.values()))) for row in data}
        else:
            leaf = {
                "column_id": "flags",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "array",
                    "filter_op": "contains",
                    "filter_value": ["target"],
                },
            }
            clause, bindings = compile_exact_graph_filter_predicates(
                [leaf],
                project_id=PROJECT,
                observe_type="trace",
            )
            data = run(
                "SELECT trace_id FROM values('trace_id String', "
                "('trace-moved'), ('trace-stable')) WHERE " + clause,
                {**bindings, **params},
            )
            actual = {row["trace_id"] for row in data}
        assert actual == expected


@pytest.mark.integration
@pytest.mark.parametrize("scalar_seed", [False, True])
@pytest.mark.parametrize("observe_type", ["trace", "span"])
@pytest.mark.parametrize("leaf_count", [2, 5, 10])
def test_nested_graph_membership_keeps_cross_type_leaf_semantics(
    engine,
    observe_type,
    leaf_count,
    scalar_seed,
):
    run, insert = engine
    specs = [
        ("array", "contains", ["target"], "attributes_extra", ["target"]),
        ("map", "contains", {"a": "b"}, "attributes_extra", {"a": "b"}),
        ("number", "greater_than", 1, "attrs_number", 2),
        ("text", "contains", "needle", "attrs_string", "a needle here"),
        ("boolean", "equals", False, "attrs_bool", 0),
    ]
    if scalar_seed:
        specs = specs[2:] + specs[:2]
    leaves, complete, span_ids = [], {}, []
    for index in range(leaf_count):
        kind, operation, value, column, stored = specs[index % len(specs)]
        key, span_id = f"property-{index}", f"split-{index}"
        leaves.append(
            {
                "column_id": key,
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": kind,
                    "filter_op": operation,
                    "filter_value": value,
                },
            }
        )
        complete.setdefault(column, {})[key] = stored
        attrs = {key: stored}
        insert(
            id=span_id,
            trace_id="split",
            **{
                column: json.dumps(attrs) if column == "attributes_extra" else attrs,
            },
        )
        span_ids.append(span_id)
    complete["attributes_extra"] = json.dumps(complete.get("attributes_extra", {}))
    insert(id="all", trace_id="single", **complete)
    insert(id="superseded", trace_id="superseded", **complete)
    insert(
        id="superseded",
        trace_id="superseded",
        _version=2,
        start_time=START + timedelta(minutes=10),
        **complete,
    )
    predicate, params = compile_exact_graph_filter_predicates(
        leaves,
        project_id=PROJECT,
        observe_type=observe_type,
    )

    class Reader:
        def execute_ch_query(self, sql, bindings, **kwargs):
            return SimpleNamespace(data=run(sql, bindings))

    common = {
        "analytics": Reader(),
        "project_id": PROJECT,
        "predicate": predicate,
        "predicate_params": params,
        "start_date": START + timedelta(minutes=15),
        "end_date": START + timedelta(minutes=30),
        "timeout_ms": 10000,
        "settings": {},
    }
    if observe_type == "trace":
        actual = graphs._matching_trace_ids(
            trace_ids=("split", "single", "superseded"),
            **common,
        )
        # Different children may satisfy each trace leaf; every span filter
        # must instead hold on the same latest physical row.
        assert actual == {"split", "single"}
    else:
        actual = graphs._matching_span_ids(
            span_ids=(*span_ids, "all", "superseded"),
            **common,
        )
        assert actual == {"all"}
    if scalar_seed and observe_type == "span":
        builder = TimeSeriesQueryBuilder(project_id=PROJECT, filters=leaves, interval="day",
            observe_type="span", exact_snapshot=True, start_date=common["start_date"],
            end_date=common["end_date"])
        assert builder._exact_span_candidate_plan() is not None
        data = run(*builder.build_exact_span_partition(
            partition_start=START, partition_end=START + timedelta(hours=1)))
        assert sum(int(row["traffic_count"]) for row in data) == 1


@pytest.mark.integration
@pytest.mark.parametrize("operation,values,types,expected", [
    ("equals", 0, None, {"zero", "to-zero"}),
    ("in", [0, 2], ["number", "number"], {"zero", "two", "to-zero"}),
    ("in", [0, 2, False, "ALPHA"], ["number", "number", "boolean", "string"],
     {"zero", "two", "to-zero", "false", "text", "conflict", "type-change"}),
    ("not_in", [0, 2, False, "ALPHA"], ["number", "number", "boolean", "string"],
     {"string-zero", "true", "other", "changed"}),
])
def test_span_graph_typed_defaults_match_independent_latest_domain(engine, operation, values, types, expected):
    run, insert = engine
    start, end = START + timedelta(minutes=15, microseconds=123456), START + timedelta(minutes=30, microseconds=123456)
    states = [
        ("absent", {}, {}), ("zero", {"attrs_number": {"score": 0}}, {}),
        ("two", {"attrs_number": {"score": 2}}, {}),
        ("string-zero", {"attrs_string": {"score": "0"}}, {}),
        ("false", {"attrs_bool": {"score": 0}}, {}), ("true", {"attrs_bool": {"score": 1}}, {}),
        ("text", {"attrs_string": {"score": "alpha"}}, {}),
        ("other", {"attrs_number": {"score": 9}}, {}),
        ("conflict", {"attrs_number": {"score": 9}, "attrs_bool": {"score": 0}}, {}),
        ("cleared", {"attrs_number": {"score": 0}}, {"attrs_number": {}}),
        ("deleted", {"attrs_number": {"score": 0}}, {"is_deleted": 1}),
        ("type-change", {"attrs_number": {"score": 0}}, {"attrs_number": {}, "attrs_string": {"score": "alpha"}}),
        ("to-zero", {"attrs_number": {"score": 9}}, {"attrs_number": {"score": 0}}),
        ("changed", {"attrs_number": {"score": 0}}, {"attrs_number": {"score": 9}}),
        ("outside", {"attrs_number": {"score": 0}}, {"start_time": START + timedelta(minutes=40, microseconds=123456)}),
        ("foreign", {"attrs_number": {"score": 0}, "project_id": OTHER_PROJECT}, {}),
    ]
    for index, (name, initial, correction) in enumerate(states):
        weight = 2 ** index  # Independent identity-weighted metrics expose wrong same-trace members.
        row = {"id": name, "attrs_number": {}, "attrs_string": {}, "attrs_bool": {},
               "latency_ms": weight, "total_tokens": 2 * weight, "cost": weight / 8,
               "prompt_tokens": weight, "completion_tokens": weight, "status": "ERROR", **initial}
        insert(**row)
        if correction:
            insert(**{**row, **correction, "_version": 2})
    assert int(run("SELECT count() AS n FROM spans")[0]["n"]) == len(states) + sum(bool(s[2]) for s in states)
    # Complete FINAL first; no candidate/value/date filter decides oracle membership.
    winners = run("SELECT * FROM spans FINAL")
    domains = {"number": "attrs_number", "boolean": "attrs_bool", "string": "attrs_string"}
    choices = list(zip(types, values, strict=True)) if types else [("number", values)]
    truth = []
    for row in winners:
        if row["project_id"] != PROJECT or row["is_deleted"] or not start <= row["start_time"] < end:
            continue
        present = any("score" in row[domains[t]] for t, _ in choices)
        matches = any("score" in row[domains[t]] and
                      (row[domains[t]]["score"].lower() == v.lower() if t == "string"
                       else row[domains[t]]["score"] == v) for t, v in choices)
        if (present and not matches) if operation == "not_in" else matches:
            truth.append(row)
    assert {row["id"] for row in truth} == expected
    config = {"col_type": "SPAN_ATTRIBUTE", "filter_type": "text" if types else "number",
              "filter_op": operation, "filter_value": values}
    if types:
        config["attribute_value_types"] = types
    builder = TimeSeriesQueryBuilder(project_id=PROJECT, filters=[{"column_id": "score", "filter_config": config}],
        interval="day", observe_type="span", exact_snapshot=True, start_date=start, end_date=end)
    assert (builder._exact_span_candidate_plan() is not None) == (operation == "in")
    data = run(*builder.build_exact_span_partition(partition_start=START, partition_end=START + timedelta(hours=1)))
    fields = ("latency_sum", "total_tokens", "cost_sum", "traffic_count", "prompt_tokens", "completion_tokens", "error_count")
    weight = sum(row["latency_ms"] for row in truth)
    assert tuple(sum(float(row[k]) for row in data) for k in fields) == (
        weight, 2 * weight, weight / 8, len(truth), weight, weight, len(truth))
