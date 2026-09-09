"""Actual detail SQL against an isolated, production-shaped CH25 RMT table."""

from datetime import timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.session_detail import (
    build_session_detail_aggregate_query,
    build_session_detail_traces_query,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine

SESSION = "55555555-5555-4555-8555-555555555555"
ALIAS = "66666666-6666-4666-8666-666666666666"
OTHER_SESSION = "77777777-7777-4777-8777-777777777777"
USER = "88888888-8888-4888-8888-888888888888"


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture
def detail_engine(request):
    execute, original_insert = request.getfixturevalue("engine")
    execute("ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)")

    def insert(**kwargs):
        original_insert(
            **{
                "trace_session_id": SESSION,
                "end_user_id": USER,
                "end_time": START + timedelta(minutes=30),
                "cost": 1,
                "total_tokens": 10,
                "prompt_tokens": 4,
                "completion_tokens": 6,
                "parent_span_id": "",
                **kwargs,
            }
        )

    return execute, insert


def aggregate(execute, group=(SESSION, ALIAS)):
    return execute(*build_session_detail_aggregate_query(PROJECT, group))[0]


def page(execute, *, limit=25, offset=0):
    return execute(
        *build_session_detail_traces_query(
            PROJECT, (SESSION, ALIAS), limit=limit, offset=offset
        )
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "replacement",
    [
        {"is_deleted": 1},
        {"trace_session_id": OTHER_SESSION},
        {"trace_session_id": None},
    ],
)
def test_detail_removes_old_membership_after_replacement(detail_engine, replacement):
    execute, insert = detail_engine
    insert(id="removed", trace_id="gone", input="stale", cost=1000)
    insert(
        **{
            "id": "removed",
            "trace_id": "gone",
            "_version": 2,
            "start_time": START + timedelta(minutes=10),
            **replacement,
        }
    )
    insert(id="retained", trace_id="live", trace_session_id=ALIAS)
    result = aggregate(execute)
    assert result["total_cost"] == 1 and result["total_tokens"] == 10
    assert result["total_traces"] == 1
    assert [row["trace_id"] for row in page(execute)] == ["live"]


@pytest.mark.integration
def test_detail_latest_cost_and_same_row_root_messages_include_child_metrics(
    detail_engine,
):
    execute, insert = detail_engine
    insert(id="root", trace_id="t", input="old", output="old-out", cost=100)
    insert(
        id="root",
        trace_id="t",
        _version=2,
        input="new",
        output="new-out",
        cost=2,
        total_tokens=20,
        prompt_tokens=8,
        completion_tokens=12,
    )
    # Earlier children must not win the displayed input/output over the root.
    insert(
        id="child",
        trace_id="t",
        parent_span_id="root",
        start_time=START,
        input="child",
        output="child-out",
        cost=3,
        total_tokens=30,
        prompt_tokens=12,
        completion_tokens=18,
    )
    result = aggregate(execute)
    assert result["total_cost"] == 5 and result["total_tokens"] == 50
    assert result["end_user_id"] == USER
    row = page(execute)[0]
    assert (row["input"], row["output"]) == ("new", "new-out")
    assert row["total_cost"] == 5 and row["total_tokens"] == 50
    assert (row["input_tokens"], row["output_tokens"]) == (20, 30)
    assert row["trace_min_start_time"].startswith(START.isoformat(sep=" "))


@pytest.mark.integration
def test_detail_preserves_all_six_identity_discriminators_and_whole_history(
    detail_engine,
):
    execute, insert = detail_engine
    insert(id="same", trace_id="same")
    insert(id="same", trace_id="same", service_name="service-b")
    insert(id="same", trace_id="same", observation_type="generation")
    insert(id="same", trace_id="same", start_time=START - timedelta(days=200))
    insert(id="same", trace_id="same", project_id=OTHER_PROJECT, cost=1000)
    result = aggregate(execute)
    assert result["total_cost"] == 4 and result["total_tokens"] == 40
    assert result["total_traces"] == 1
    assert page(execute)[0]["total_cost"] == 4


@pytest.mark.integration
def test_detail_tied_time_pagination_is_disjoint_and_deterministic(detail_engine):
    execute, insert = detail_engine
    for trace_id in ("z", "a", "c", "b", "d"):
        insert(id="root", trace_id=trace_id, input=trace_id)
    first, second, third = (page(execute, limit=2, offset=n) for n in (0, 2, 4))
    assert [[r["trace_id"] for r in rows] for rows in (first, second, third)] == [
        ["a", "b"],
        ["c", "d"],
        ["z"],
    ]


@pytest.mark.integration
def test_detail_without_root_uses_one_deterministic_child(detail_engine):
    execute, insert = detail_engine
    insert(id="b", parent_span_id="absent", input="b", output="out-b")
    insert(id="a", parent_span_id="absent", input="a", output="out-a")
    row = page(execute)[0]
    assert (row["input"], row["output"]) == ("a", "out-a")


@pytest.mark.integration
def test_detail_newest_null_message_does_not_resurrect_old_content(detail_engine):
    execute, insert = detail_engine
    execute("ALTER TABLE spans MODIFY COLUMN input Nullable(String)")
    execute("ALTER TABLE spans MODIFY COLUMN output Nullable(String)")
    insert(id="root", input="stale", output="stale-out")
    insert(id="root", _version=2, input=None, output="latest-out")
    row = page(execute)[0]
    assert (row["input"], row["output"]) == (None, "latest-out")


@pytest.mark.integration
def test_detail_multiple_roots_choose_earliest_root_coherently(detail_engine):
    execute, insert = detail_engine
    insert(id="z", input="later", output="later-out")
    insert(id="a", start_time=START, input="first", output="first-out")
    insert(id="child", parent_span_id="a", start_time=START - timedelta(hours=1))
    row = page(execute)[0]
    assert (row["input"], row["output"]) == ("first", "first-out")
    assert row["total_cost"] == 3


@pytest.mark.unit
@pytest.mark.parametrize("group", [(), [], None, "id", [None], [""]])
def test_detail_cannot_drop_group_scope(group):
    with pytest.raises(ValueError):
        build_session_detail_aggregate_query(PROJECT, group)


@pytest.mark.unit
@pytest.mark.parametrize("limit,offset", [(0, 0), (1, -1), (True, 0), (2, False)])
def test_detail_pagination_validation(limit, offset):
    with pytest.raises(ValueError):
        build_session_detail_traces_query(
            PROJECT, (SESSION,), limit=limit, offset=offset
        )


@pytest.mark.unit
def test_detail_queries_replay_full_keys_without_a_population_limit():
    for sql, params in (
        build_session_detail_aggregate_query(PROJECT, (SESSION, ALIAS, SESSION)),
        build_session_detail_traces_query(
            PROJECT, (SESSION, ALIAS), limit=26, offset=25
        ),
    ):
        assert params["session_group_ids"] == (SESSION, ALIAS)
        assert sql.count("project_id = %(project_id)s") == 2
        assert (
            "FROM spans FINAL" in sql
            and "toStartOfHour(start_time), trace_id, id" in sql
        )
        assert "use_skip_indexes_if_final = 0" in sql
        assert "optimize_move_to_prewhere_if_final = 0" in sql
        before_final_where = sql.split("WHERE trace_session_id IN")[0]
        assert "is_deleted = 0" not in before_final_where
        assert "LIMIT" not in before_final_where
