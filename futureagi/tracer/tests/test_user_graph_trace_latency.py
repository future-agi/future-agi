"""Users graph latency membership uses one measured duration per trace.

The Users list averages one time envelope per canonical user/trace
(``user_list._user_trace_latency_ctes``). A latency-filtered Users graph must
select the same users, so contained child spans never dilute a trace.
"""

# ruff: noqa: F811
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import exact_graph_reads as graph
from tracer.tests.test_span_physical_identity_latest import (
    PROJECT,
    START,
    engine,  # noqa: F401 - pytest fixture
    time_filter,
)

pytestmark = pytest.mark.unit
UA, UB = (f"{n:08d}-4444-4444-4444-444444444444" for n in (1, 2))
LO, HI = START + timedelta(minutes=15), START + timedelta(minutes=30)
AT = START + timedelta(minutes=20)
LATENCY_ALIASES = ("avg_trace_latency", "avg_latency", "latency", "latency_ms")


def latency_filter(op="less_than", value=5000, column="avg_trace_latency"):
    return {
        "column_id": column,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": op,
            "filter_value": value,
        },
    }


def num_traces_filter():
    return {
        "column_id": "num_traces",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": 0,
        },
    }


def source(filters, **kwargs):
    return graph._user_aggregate_source_sql(
        project_id=PROJECT,
        filters=filters,
        start_date=LO,
        end_date=HI,
        include_trace_ids=False,
        all_snapshot_users=True,
        **kwargs,
    )


def cte(sql, name):
    start = re.search(r"\b" + re.escape(name) + r" AS \(", sql).end()
    depth = 1
    for end in range(start, len(sql)):
        depth += (sql[end] == "(") - (sql[end] == ")")
        if depth == 0:
            return " ".join(sql[start:end].split())
    raise AssertionError("unclosed CTE")


@pytest.mark.parametrize("alias", LATENCY_ALIASES)
@pytest.mark.parametrize("reuse_outer_snapshot", [False, True])
def test_latency_filter_reduces_each_trace_before_the_user_average(
    alias, reuse_outer_snapshot
):
    sql, params, needs_eval = source(
        [time_filter(LO, HI), latency_filter(column=alias)],
        reuse_outer_snapshot=reuse_outer_snapshot,
    )
    durations = cte(sql, "user_trace_durations")
    assert "FROM resolved_spans" in durations
    assert "GROUP BY end_user_id, trace_id" in durations
    assert "max(toUnixTimestamp64Micro(start_time) + latency_ms * 1000)" in durations
    latency = cte(sql, "user_trace_latency")
    assert "avg(duration_ms) AS avg_trace_latency" in latency
    assert "FROM user_trace_durations" in latency
    # The span-grain average is gone from the per-user aggregate.
    assert "avgIf(latency_ms" not in sql
    assert "latency_ms" not in cte(sql, "user_span_metrics")
    rows = cte(sql, "user_rows")
    assert "coalesce(round(utl.avg_trace_latency, 2), 0) AS avg_trace_latency" in rows
    assert "LEFT JOIN user_trace_latency AS utl" in rows
    assert "avg_trace_latency < %(user_filter_1)s" in sql
    assert params["user_filter_1"] == 5000 and not needs_eval


def test_other_entity_filters_skip_the_trace_latency_reduction():
    sql, _, _ = source([time_filter(LO, HI), num_traces_filter()])
    assert "user_trace_durations" not in sql
    assert "user_trace_latency" not in sql
    assert "avgIf(latency_ms" not in sql
    assert "toFloat64(0) AS avg_trace_latency" in cte(sql, "user_rows")


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ([latency_filter()], True),
        ([latency_filter(column="latency")], True),
        ([time_filter(LO, HI), num_traces_filter()], False),
        # A raw span attribute that happens to share a list alias name.
        (
            [
                {
                    "column_id": "latency_ms",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "number",
                        "filter_op": "less_than",
                        "filter_value": 5000,
                    },
                }
            ],
            False,
        ),
        ([], False),
    ],
)
def test_only_output_latency_filters_need_the_reduction(filters, expected):
    assert graph._user_filters_need_trace_latency(filters) is expected


def populate(run, insert):
    run("ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)")
    run(
        "SET max_threads=1, max_memory_usage=536870912, max_execution_time=10, "
        "use_skip_indexes_if_final=0, enable_optimize_predicate_expression=1, "
        "enable_optimize_predicate_expression_to_final_subquery=1"
    )
    run("""CREATE TABLE end_users (project_id UUID, end_user_id UUID, user_id String,
        user_id_type String, user_id_hash String, first_seen DateTime64(6,'UTC'),
        version UInt64, is_deleted UInt8) ENGINE=ReplacingMergeTree(version)
        ORDER BY (project_id,end_user_id)""")
    for table in ("end_user_id_remap", "trace_session_id_remap"):
        run(
            f"CREATE TABLE {table} (old_id UUID,new_id UUID,version UInt64) "
            "ENGINE=ReplacingMergeTree(version) ORDER BY old_id"
        )
    for uid in (UA, UB):
        run(
            "INSERT INTO end_users VALUES (%(p)s,%(u)s,%(u)s,'id','hash',%(t)s,1,0)",
            {"p": PROJECT, "u": uid, "t": START},
        )
    # UA: one 10 s trace whose two 1 s children run inside the root. The
    # span-grain mean is 4 s; the trace latency (and the Users list) is 10 s.
    insert(trace_id="slow", id="root", end_user_id=UA, start_time=AT, latency_ms=10000)
    for index in range(2):
        insert(
            trace_id="slow",
            id=f"child-{index}",
            parent_span_id="root",
            end_user_id=UA,
            start_time=AT + timedelta(milliseconds=100 * (index + 1)),
            latency_ms=1000,
        )
    # UB: one 1 s trace.
    insert(trace_id="fast", id="root", end_user_id=UB, start_time=AT, latency_ms=1000)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("op", "selected", "latency"),
    [("less_than", UB, 1000), ("greater_than", UA, 10000)],
)
@pytest.mark.parametrize("path", ["aggregate", "full_snapshot"])
def test_latency_filtered_users_match_the_list_trace_latency(
    engine, path, op, selected, latency
):
    run, insert = engine
    populate(run, insert)
    filters = [time_filter(LO, HI), latency_filter(op)]
    if path == "aggregate":
        sql, params, _ = graph._user_aggregate_source_sql(
            project_id=PROJECT,
            filters=filters,
            start_date=LO,
            end_date=HI,
            include_trace_ids=False,
            candidate_trace_ids_param="candidate_traces",
        )
        rows = run(sql, {**params, "candidate_traces": ("fast", "slow")})
        assert [row["end_user_id"] for row in rows] == [selected]
        assert rows[0]["avg_trace_latency"] == latency
        return

    def execute(sql, params, **_):
        rows = run(sql, params)
        return SimpleNamespace(data=rows, columns=list(rows[0]) if rows else [])

    payload = graph.read_exact_user_system_graph(
        analytics=SimpleNamespace(execute_ch_query=execute),
        project_id=PROJECT,
        filters=filters,
        interval="day",
        metric_id="active_users",
    )
    # Exactly one of the two same-day users is selected, never both or none.
    assert sum(point["value"] for point in payload["data"]) == 1
