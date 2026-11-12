"""Users latency is one measured trace duration, not one sample per span.

Native cases execute the actual generated SQL on the existing disposable
ReplacingMergeTree fixture. No service connections or candidate SQL rewrites.
"""

from datetime import timedelta

import pytest

from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.tests.test_relational_filter_native import (
    _drop_legacy_ch_spans_mvs,  # noqa: F401 -- suppress service fixtures
    _ensure_test_score_tenant_column,  # noqa: F401
    offline_only,  # noqa: F401
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_users_physical_latest_native import uid
from tracer.tests.test_users_physical_latest_native import users_engine as users_engine

pytestmark = pytest.mark.unit
PATHS = ("requested", "page", "table")
METRICS = {"avg_trace_latency", "num_llm_calls", "num_traces_with_errors"}
AT = START + timedelta(minutes=20)


def builder(builder_class=UserListQueryBuilderV2):
    return builder_class(
        organization_id=PROJECT,
        project_ids=[PROJECT, OTHER_PROJECT],
        filters=[time_filter(START, START + timedelta(hours=1))],
        end_user_id=uid(10),
        limit=25,
        offset=0,
    )


def query(target, path):
    if path == "requested":
        queries = target.build_requested_page_metric_queries([uid(10)], METRICS)
        assert len(queries) == 1
        sql, params, fields = queries[0]
        assert set(fields) == METRICS
        return sql, params
    if path == "page":
        return target.build_page_metrics_query([uid(10)])
    return target.build()


@pytest.fixture
def latency_engine(users_engine):  # noqa: F811 -- fixture injection
    execute, insert = users_engine
    execute("ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)")
    execute("""CREATE TABLE trace_session_id_remap (
        old_id UUID, new_id UUID, version DateTime64(6, 'UTC')
    ) ENGINE=ReplacingMergeTree(version) ORDER BY old_id""")
    return execute, insert


def span(insert, trace, name, latency, *, at=AT, **changes):
    insert(
        **{
            "trace_id": trace,
            "id": name,
            "start_time": at,
            "end_time": (
                (at + timedelta(milliseconds=latency)).isoformat(" ")
                if latency is not None
                else None
            ),
            "latency_ms": latency,
            "end_user_id": uid(10),
            "observation_type": "llm",
            "cost": 1,
            **changes,
        }
    )


def metrics(execute, path):
    rows = execute(*query(builder(), path))
    assert [row["end_user_id"] for row in rows] == [uid(10)]
    return rows[0]


@pytest.mark.parametrize(
    "builder_class", [UserListQueryBuilder, UserListQueryBuilderV2]
)
@pytest.mark.parametrize("path", PATHS)
def test_latency_reduces_canonical_project_trace_before_user_average(
    builder_class, path
):
    sql, params = query(builder(builder_class), path)
    compact = " ".join(sql.split())
    assert "GROUP BY end_user_id, project_id, trace_id" in compact
    assert "avg(duration_ms) AS avg_trace_latency" in compact
    assert "FROM trace_durations" in compact
    assert "argMax(tuple(latency_ms), _version).1 AS latest_latency_ms" in compact
    assert "avgIf(latency_ms" not in sql
    assert "avgIf(latest_latency_ms" not in sql
    assert "latest_is_deleted = 0" in sql
    assert params["project_ids"] == (PROJECT, OTHER_PROJECT)
    if path == "requested":
        assert "round(" not in sql


@pytest.mark.integration
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("children", [2, 8])
def test_trace_latency_does_not_change_when_short_children_are_added(
    latency_engine, path, children
):
    execute, insert = latency_engine
    span(insert, "one-trace", "root", 10000)
    assert metrics(execute, path)["avg_trace_latency"] == 10000
    for index in range(children):
        span(
            insert,
            "one-trace",
            f"child-{index}",
            1000,
            at=AT + timedelta(milliseconds=100 * index),
            parent_span_id="root",
        )
    actual = metrics(execute, path)
    assert actual["avg_trace_latency"] == 10000
    assert int(actual["num_llm_calls"]) == children + 1


@pytest.mark.integration
@pytest.mark.parametrize("path", PATHS)
def test_trace_latency_weights_traces_once_and_separates_projects(latency_engine, path):
    execute, insert = latency_engine
    span(insert, "shared-id", "root", 10000)
    span(insert, "shared-id", "child", 1000, parent_span_id="root")
    # Same trace and span IDs, another project and a distant start time.
    span(
        insert,
        "shared-id",
        "root",
        2000,
        at=AT + timedelta(minutes=10),
        project_id=OTHER_PROJECT,
    )
    span(insert, "third-trace", "root", 4000, end_user_id=uid(50))
    actual = metrics(execute, path)
    expected = 16000 / 3 if path == "requested" else 5333.33
    assert actual["avg_trace_latency"] == pytest.approx(expected)
    assert int(actual["num_llm_calls"]) == 4


@pytest.mark.integration
@pytest.mark.parametrize("path", PATHS)
def test_trace_latency_uses_time_bounds_when_no_root_is_present(latency_engine, path):
    execute, insert = latency_engine
    span(insert, "rootless", "first", 1000, parent_span_id="missing")
    span(
        insert,
        "rootless",
        "last",
        1000,
        at=AT + timedelta(seconds=9),
        parent_span_id="missing",
    )
    assert metrics(execute, path)["avg_trace_latency"] == 10000


@pytest.mark.integration
@pytest.mark.parametrize("path", PATHS)
def test_trace_latency_keeps_latest_live_remapped_and_nullable_spans(
    latency_engine, path
):
    execute, insert = latency_engine
    span(insert, "live", "root", 10000)
    span(insert, "live", "root", 5000, _version=2)
    span(
        insert,
        "live",
        "child",
        1000,
        parent_span_id="root",
        end_user_id=uid(50),
    )
    span(insert, "deleted", "root", 100000)
    span(insert, "deleted", "root", 100000, is_deleted=1, _version=2)
    span(insert, "reassigned", "root", 100000)
    span(insert, "reassigned", "root", 100000, end_user_id=uid(20), _version=2)
    span(insert, "unknown", "root", 100000)
    span(insert, "unknown", "root", None, _version=2)
    actual = metrics(execute, path)
    assert actual["avg_trace_latency"] == 5000
    assert int(actual["num_llm_calls"]) == 3
    assert int(actual["num_traces_with_errors"]) == 0


@pytest.mark.integration
def test_requested_trace_latency_keeps_unrounded_average(latency_engine):
    execute, insert = latency_engine
    for trace, latency in (("a", 1), ("b", 2), ("c", 2)):
        span(insert, trace, "root", latency)
    span(insert, "a", "child", 1, parent_span_id="root")
    assert metrics(execute, "requested")["avg_trace_latency"] == pytest.approx(5 / 3)


def test_unrequested_latency_does_not_add_duration_work():
    queries = builder().build_requested_page_metric_queries(
        [uid(10)], {"num_llm_calls"}
    )
    assert len(queries) == 1
    sql, _, fields = queries[0]
    assert fields == ("num_llm_calls",)
    assert "trace_durations" not in sql
    assert "latency_ms" not in sql
