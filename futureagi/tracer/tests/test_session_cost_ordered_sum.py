"""Session and trace cost sums render one ordered, compensated reducer.

Offline SQL-text checks only; test_session_cost_ordered_sum_ch25.py runs the
same SQL on ClickHouse. Every expected fragment is a literal, never a call to
the helper under test, so a change to the helper text is caught here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Final

import pytest

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.query_builders.trace_usage import (
    deterministic_sum_sql,
    ordered_float_sum_sql,
    usage_aggregate_sql,
)
from tracer.services.clickhouse.v2.query_builders.session_detail import (
    build_session_detail_aggregate_query,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.eval_tasks.sampling.policy import validate_policy
from tracer.services.eval_tasks.sampling.query import SamplingQuery

pytestmark = pytest.mark.unit

PROJECT: Final[str] = "00000000-0000-4000-8000-0000000000d7"
SESSION: Final[str] = "00000000-0000-4000-8000-0000000000d8"
START: Final[datetime] = datetime(2026, 10, 20, 12, tzinfo=UTC)
NOW: Final[datetime] = datetime(2026, 10, 21, tzinfo=UTC)

ORDERED_SUM_OF_X: Final[str] = (
    "arrayMap(ordered_cost_values -> arrayMap(compensated_cost -> "
    "if(isFinite(compensated_cost), compensated_cost, "
    "arraySum(ordered_cost_values)), "
    "[arrayReduce('sumKahan', ordered_cost_values)])[1], "
    "[arraySort(x)])[1]"
)
DETERMINISTIC_SUM_OF_USAGE_COST: Final[str] = (
    "if(count(usage_cost) = 0, sum(usage_cost), "
    "arrayMap(ordered_cost_values -> arrayMap(compensated_cost -> "
    "if(isFinite(compensated_cost), compensated_cost, "
    "arraySum(ordered_cost_values)), "
    "[arrayReduce('sumKahan', ordered_cost_values)])[1], "
    "[arraySort(groupArray(usage_cost))])[1])"
)
V2_COST_AGGREGATE: Final[str] = (
    "if(countIf(attrs_string['fi.cost.kind'] = 'unknown' AND "
    "attrs_string['fi.usage.contribution'] != 'none') > 0, "
    "CAST(NULL, 'Nullable(Float64)'), "
    "if(count(cost) = 0, sum(cost), "
    "arrayMap(ordered_cost_values -> arrayMap(compensated_cost -> "
    "if(isFinite(compensated_cost), compensated_cost, "
    "arraySum(ordered_cost_values)), "
    "[arrayReduce('sumKahan', ordered_cost_values)])[1], "
    "[arraySort(groupArray(cost))])[1]))"
)
# Unchanged: the session list legacy aggregate takes a max over copies of one
# per-session value, so it needs no reducer of its own.
AGGREGATE_SELECT: Final[str] = (
    "min(start_time) AS session_start, "
    "max(end_time) AS session_end, "
    "dateDiff('second', min(start_time), max(end_time)) AS duration, "
    "max(session_usage_cost) AS total_cost, "
    "max(session_usage_tokens) AS total_tokens, "
    "uniqExact(trace_id) AS traces_count"
)
PLAIN_PER_TRACE_COST: Final[str] = "arraySum(s -> s.5, billable_usage_spans)"
ORDERED_PER_TRACE_COST: Final[str] = (
    "arrayMap(ordered_cost_values -> arrayMap(compensated_cost -> "
    "if(isFinite(compensated_cost), compensated_cost, "
    "arraySum(ordered_cost_values)), "
    "[arrayReduce('sumKahan', ordered_cost_values)])[1], "
    "[arraySort(arrayMap(s -> s.5, billable_usage_spans))])[1]"
)
# The user list's known-cost reducer, rendered on the base commit. It must stay
# byte-identical: user totals keep their own definition.
KNOWN_COST_REDUCER: Final[str] = "\n".join(
    (
        "",
        "            arrayMap(usage_spans ->",
        "                arrayMap(repeated_usage_ids ->",
        "                    arrayMap(billable_usage_spans -> tuple(",
        "                        arraySum(s -> if(tupleElement(s.9, 2), 0, s.5), billable_usage_spans),",
        "                        arraySum(s -> toInt64(s.10",
        "                            AND NOT tupleElement(s.9, 1) AND NOT tupleElement(s.9, 2)",
        "                            AND NOT (s.4 != 'llm'",
        "                                AND has(repeated_usage_ids, s.1))), usage_spans),",
        "                        arrayCount(s -> tupleElement(s.9, 2), billable_usage_spans),",
        "                        arraySum(s -> s.6, billable_usage_spans),",
        "                        arraySum(s -> s.7, billable_usage_spans),",
        "                        arraySum(s -> s.8, billable_usage_spans)",
        "                    ), [arrayFilter(s -> NOT tupleElement(s.9, 1)",
        "                        AND NOT (s.4 != 'llm' AND has(repeated_usage_ids, s.1)), usage_spans)])[1],",
        "                    [arrayConcat(arrayMap(s -> s.1, arrayFilter(s -> arrayExists(c -> c.2 = s.1 AND (1), usage_spans) AND abs(s.5 - arraySum(c -> c.5, arrayFilter(c -> c.2 = s.1 AND (1), usage_spans))) <= greatest(abs(s.5) * 1e-9, 1e-18) AND s.6 = arraySum(c -> c.6, arrayFilter(c -> c.2 = s.1 AND (1), usage_spans)) AND s.7 = arraySum(c -> c.7, arrayFilter(c -> c.2 = s.1 AND (1), usage_spans)) AND s.8 = arraySum(c -> c.8, arrayFilter(c -> c.2 = s.1 AND (1), usage_spans)), arrayFilter(s -> s.4 != 'llm' AND s.3 = 'pydantic_ai.agent.run', usage_spans))), ",
        "        arrayMap(summary_proofs ->",
        "            arrayMap(proof -> proof.1, arrayFilter(proof -> proof.2",
        "                AND arrayCount(p -> p.1 = proof.1 AND p.2, summary_proofs)",
        "                    = arrayCount(c -> c.1 = proof.1 AND c.4 != 'llm'",
        "                        AND NOT tupleElement(c.9, 1), usage_spans), summary_proofs)),",
        "            [arrayMap(s -> tuple(s.1,",
        "                arrayMap(proof_children -> notEmpty(proof_children) AND abs(s.5 - arraySum(c -> c.5, proof_children)) <= greatest(abs(s.5) * 1e-9, 1e-18) AND s.6 = arraySum(c -> c.6, proof_children) AND s.7 = arraySum(c -> c.7, proof_children) AND s.8 = arraySum(c -> c.8, proof_children),",
        "                    [arrayMap(reachable_ids -> ",
        "        arrayFilter(c -> c.4 = 'llm' AND c.1 != s.1 AND (1)",
        "            AND has(reachable_ids, c.2), usage_spans)",
        "    , [",
        "        arrayMap(usage_branches -> arrayFold((state, hop) ->",
        "            arrayMap(next_ids -> tuple(",
        "                if(empty(next_ids), state.1,",
        "                    arrayConcat(if(empty(next_ids), [], state.1), next_ids)), next_ids),",
        "                [arrayMap(c -> c.1, arrayFilter(c -> has(state.2, c.2)",
        "                    AND NOT has(state.1, c.1),",
        "                    if(empty(state.2), [], usage_branches)))])[1],",
        "            range(length(usage_branches)), tuple([s.1], [s.1])).1,",
        "            [",
        "        arrayMap(parent_ids -> arrayFilter(c -> has(parent_ids, c.1), usage_spans),",
        "            [arrayDistinct(arrayMap(c -> c.2, usage_spans))])[1]",
        "    ])[1]",
        "    ])[1]])[1]),",
        "                arrayFilter(s -> s.4 != 'llm' AND s.3 != 'pydantic_ai.agent.run' AND NOT tupleElement(s.9, 1) AND tupleElement(tupleConcat(s.9, tuple(0)), 3), usage_spans))])[1]",
        "    )]",
        "                )[1],",
        "                [groupArray(tuple(latest_id, latest_parent_span_id, latest_name, latest_observation_type, latest_cost, latest_total_tokens, latest_prompt_tokens, latest_completion_tokens, latest_coverage, latest_known_cost_fact))]",
        "            ) AS trace_usage_metrics",
        "        ",
    )
)


def _filter(
    column: str, value: Any, *, kind: str = "number", operation: str = "greater_than"
) -> dict[str, Any]:
    return {
        "column_id": column,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _time_filter() -> dict[str, Any]:
    return _filter(
        "created_at",
        [START.isoformat(), (START + timedelta(days=1)).isoformat()],
        kind="datetime",
        operation="between",
    )


def _assert_every_cost_sum_is_ordered(sql: str, *, columns: tuple[str, ...]) -> None:
    """Each named column is summed at least once, and only through the helper.

    A bare sum(column) left anywhere in the statement fails: every occurrence
    must be the empty-input branch of the helper itself.
    """
    for column in ("usage_cost", "cost"):
        bare = sql.count(f"sum({column})")
        ordered = sql.count(f"if(count({column}) = 0, sum({column}), ")
        sorted_input = sql.count(f"[arraySort(groupArray({column}))])[1])")
        assert bare == ordered == sorted_input, (column, bare, ordered, sorted_input)
        if column in columns:
            assert ordered >= 1, column


def test_helper_text_is_pinned() -> None:
    assert ordered_float_sum_sql("x") == ORDERED_SUM_OF_X
    assert deterministic_sum_sql("usage_cost") == DETERMINISTIC_SUM_OF_USAGE_COST


def test_session_detail_total_uses_the_helper_inside_the_unknown_cost_guard() -> None:
    sql, _ = build_session_detail_aggregate_query(PROJECT, (SESSION,))
    _assert_every_cost_sum_is_ordered(sql, columns=("usage_cost",))
    flat = " ".join(sql.split())
    assert (
        "if(countIf(isNull(usage_cost)) > 0, CAST(NULL, 'Nullable(Float64)'), "
        f"{DETERMINISTIC_SUM_OF_USAGE_COST}) AS total_cost," in flat
    )


@pytest.mark.parametrize("column", ["total_cost", "total_tokens"])
def test_candidate_usage_rollup_uses_the_helper_for_cost_and_token_requests(
    column: str,
) -> None:
    # The rollup is emitted for a cost OR a token sort/filter, so a token-only
    # request carries the same cost reducer.
    builder = SessionListQueryBuilderV2(
        project_id=PROJECT,
        filters=[_time_filter(), _filter(column, 0)],
        sort_params=[{"column_id": column, "direction": "desc"}],
    )
    assert builder.supports_candidate_first_page()
    for sql, _ in (
        builder.build_candidate_page_query(),
        builder.build_candidate_count_query(),
    ):
        _assert_every_cost_sum_is_ordered(sql, columns=("usage_cost",))
        flat = " ".join(sql.split())
        assert f"{DETERMINISTIC_SUM_OF_USAGE_COST}) AS member_total_cost" in flat


@pytest.mark.parametrize("column", ["total_cost", "total_tokens"])
def test_legacy_aggregate_having_uses_the_helper(column: str) -> None:
    builder = SessionListQueryBuilderV2(
        project_id=PROJECT, filters=[_time_filter(), _filter(column, 0)]
    )
    sql, _ = builder.build()
    _assert_every_cost_sum_is_ordered(sql, columns=("usage_cost",))
    flat = " ".join(sql.split())
    assert f"{DETERMINISTIC_SUM_OF_USAGE_COST}) AS session_usage_cost" in flat
    assert "HAVING" in flat


@pytest.mark.parametrize("method", ["build", "build_id_query"])
def test_page_and_id_statements_use_the_helper_without_a_usage_filter(
    method: str,
) -> None:
    # Outside the sampling source, build() and build_id_query() join the
    # member usage roll-up whatever the filters, so a time window alone
    # already carries the cost reducer.
    builder = SessionListQueryBuilderV2(project_id=PROJECT, filters=[_time_filter()])
    sql, _ = getattr(builder, method)()
    _assert_every_cost_sum_is_ordered(sql, columns=("usage_cost",))
    flat = " ".join(sql.split())
    assert f"{DETERMINISTIC_SUM_OF_USAGE_COST}) AS session_usage_cost" in flat
    assert "max(session_usage_cost) AS total_cost" in flat


def test_aggregated_count_uses_the_helper_for_a_duration_only_filter() -> None:
    # Any aggregate filter takes the aggregated count, which joins the same
    # member usage roll-up as build().
    builder = SessionListQueryBuilderV2(
        project_id=PROJECT, filters=[_time_filter(), _filter("duration", 10)]
    )
    assert builder.has_having_filters()
    sql, _ = builder.build_count_query()
    _assert_every_cost_sum_is_ordered(sql, columns=("usage_cost",))
    flat = " ".join(sql.split())
    assert f"{DETERMINISTIC_SUM_OF_USAGE_COST}) AS session_usage_cost" in flat
    assert "SELECT count() AS total FROM (" in flat
    assert "HAVING" in flat


def test_simple_count_without_an_aggregate_filter_has_no_cost_sum() -> None:
    builder = SessionListQueryBuilderV2(project_id=PROJECT, filters=[_time_filter()])
    assert not builder.has_having_filters()
    sql, _ = builder.build_count_query()
    assert "count(DISTINCT trace_session_id) AS total" in sql
    assert "usage_cost" not in sql
    assert "sumKahan" not in sql
    assert "sum(cost)" not in sql


@pytest.mark.parametrize("column", ["total_cost", "total_tokens"])
def test_sampling_snapshot_rollup_uses_the_helper(column: str) -> None:
    clean = validate_policy(
        {
            "version": 1,
            "sampling_unit": "session",
            "mode": "closed",
            "window": {
                "start": "2026-10-19T00:00:00Z",
                "end": "2026-10-20T00:00:00Z",
            },
            "default": {"rate": 100},
        },
        now=NOW,
    )
    # The same task shape the sampling policy tests use for session usage.
    task = SimpleNamespace(
        project_id=PROJECT,
        row_type="sessions",
        filters={
            "filters": [
                {
                    "column_id": column,
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 10,
                    },
                }
            ]
        },
    )
    sql, _ = SamplingQuery(task, clean, "v", NOW).members()
    _assert_every_cost_sum_is_ordered(sql, columns=("usage_cost",))
    flat = " ".join(sql.split())
    assert f"{DETERMINISTIC_SUM_OF_USAGE_COST}) AS session_usage_cost" in flat


@pytest.mark.parametrize(
    "builder_class", [SessionListQueryBuilder, SessionListQueryBuilderV2]
)
def test_page_hydration_member_rollup_and_root_fallback_use_the_helper(
    builder_class: type[SessionListQueryBuilder],
) -> None:
    builder = builder_class(project_id=PROJECT, filters=[_time_filter()])
    sql, _ = builder.build_page_hydration_query([SESSION])
    _assert_every_cost_sum_is_ordered(sql, columns=("usage_cost", "cost"))
    flat = " ".join(sql.split())
    assert f"{DETERMINISTIC_SUM_OF_USAGE_COST}) AS member_total_cost" in flat
    assert f"{builder_class._COST_AGGREGATE} AS root_total_cost" in flat


def test_root_fallback_and_aggregate_select_text() -> None:
    assert SessionListQueryBuilderV2._COST_AGGREGATE == V2_COST_AGGREGATE
    assert "span_attr_str" not in SessionListQueryBuilderV2._COST_AGGREGATE
    assert SessionListQueryBuilder._COST_AGGREGATE == V2_COST_AGGREGATE.replace(
        "attrs_string", "span_attr_str"
    )
    assert SessionListQueryBuilder._AGGREGATE_SELECT == AGGREGATE_SELECT
    assert SessionListQueryBuilderV2._AGGREGATE_SELECT == AGGREGATE_SELECT


def test_user_list_known_cost_reducer_is_unchanged() -> None:
    assert (
        usage_aggregate_sql(
            prefix="latest_",
            coverage="latest_coverage",
            known_cost_fact="latest_known_cost_fact",
        )
        == KNOWN_COST_REDUCER
    )


def test_trace_typed_cost_filter_reads_the_root_span_cost() -> None:
    # Trace-typed eval tasks and trace bulk selection filter on the root
    # span's own cost column; neither the per-trace reducer nor the session
    # helper takes part, so neither change moves their thresholds.
    def id_query(filters: list[dict[str, Any]]) -> str:
        sql, _ = TraceListQueryBuilderV2(
            project_id=PROJECT, filters=filters
        ).build_id_query()
        return sql

    unfiltered = id_query([_time_filter()])
    filtered = id_query(
        [_time_filter(), _filter("cost", 0.6, operation="greater_than_or_equal")]
    )
    # The cost filter is compiled into the statement ...
    assert filtered != unfiltered
    # ... over the root span column, not over a per-trace reduction.
    for sql in (filtered, unfiltered):
        assert PLAIN_PER_TRACE_COST not in sql
        assert "billable_usage_spans" not in sql
        assert "sumKahan" not in sql


# Per-trace cost. These checks belong with the per-trace reducer change.


@pytest.mark.parametrize(
    "options",
    [
        pytest.param(
            {
                "prefix": "latest_",
                "coverage": "latest_usage_coverage",
                "source_summary": "latest_source_summary_present",
                "materialized": True,
            },
            id="materialized",
        ),
        pytest.param(
            {
                "prefix": "live_session_spans.",
                "coverage": "live_session_spans.usage_coverage",
                "materialized": True,
            },
            id="materialized-without-summary",
        ),
        pytest.param({"coverage": "usage_coverage"}, id="projection"),
        pytest.param(
            {"prefix": "live.", "coverage": "live.usage_coverage"},
            id="projection-prefixed",
        ),
    ],
)
def test_per_trace_cost_uses_the_ordered_sum_in_both_reducer_forms(
    options: dict[str, Any],
) -> None:
    sql = usage_aggregate_sql(**options)
    assert sql.count(ORDERED_PER_TRACE_COST) == 1
    assert PLAIN_PER_TRACE_COST not in sql
    # Token sums are integers and stay plain.
    for index in (6, 7, 8):
        assert f"arraySum(s -> s.{index}, billable_usage_spans)" in sql
