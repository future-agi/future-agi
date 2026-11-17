"""Content search uses full stored fields and the bounded physical-winner path."""

from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_span_filter_plans,
    compile_trace_filter_plans,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_input_output_search_contract import search_filter

PROJECT = "11111111-1111-1111-1111-111111111111"
START = datetime(2026, 8, 8, 12)


def window():
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [
                START.isoformat(),
                (START + timedelta(hours=1)).isoformat(),
            ],
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "compiler,scope",
    [(compile_span_filter_plans, "span"), (compile_trace_filter_plans, "any")],
)
@pytest.mark.parametrize("needle", ["Needle", "É文_%'\\", "x" * 1024])
def test_search_compiles_one_or_leaf_over_latest_input_and_output(
    compiler, scope, needle
):
    plans = compiler([search_filter(needle)])
    assert len(plans) == 1
    plan = plans[0]
    assert plan.scope == scope
    assert len(plan.aggregates) == 2
    assert "argMax(input, _peerdb_version)" in " ".join(plan.aggregates)
    assert "argMax(output, _peerdb_version)" in " ".join(plan.aggregates)
    assert set(plan.params.values()) == {needle}
    for sql in (plan.predicate, plan.seed_predicate):
        assert sql.startswith("(") and sql.endswith(")")
        assert " OR " in sql
        assert sql.count("positionUTF8(") == 2
        assert sql.count("lowerUTF8(") == 4
        assert "concat" not in sql and "LIKE" not in sql
        assert needle not in sql
    assert "toString(input)" in plan.seed_predicate
    assert "toString(output)" in plan.seed_predicate
    assert plan.raw_witness_predicate == plan.seed_predicate
    assert plan.source_metric == "input_output"
    assert "countIf(" in plan.grouped_match_predicate()


@pytest.mark.unit
def test_search_keeps_sibling_filters_and_parameter_names_separate():
    plans = compile_span_filter_plans(
        [
            search_filter("first"),
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "model-a",
                },
            },
            search_filter("second"),
        ]
    )
    assert len(plans) == 3
    assert set(plans[0].params).isdisjoint(plans[2].params)
    assert "argMax(tuple(model), _peerdb_version).1" in plans[1].aggregates[0]


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["trace", "span"])
def test_direct_compiler_never_falls_back_to_attribute_named_input_output(mode):
    needle = "É_%'\\literal"
    builder = ClickHouseFilterBuilder(
        query_mode=mode, project_id=PROJECT, span_date_scope=True
    )
    sql, params = builder.translate([search_filter(needle)])
    assert "toString(input)" in sql and "toString(output)" in sql
    assert " OR " in sql and "span_attr" not in sql
    assert needle not in sql and needle in params.values()
    if mode == "trace":
        assert "trace_id IN" in sql and "%(project_id)s" in sql


@pytest.mark.unit
def test_span_seed_and_classifier_pack_full_content_and_keep_physical_scope():
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[window(), search_filter()],
        bounded_internal_scan=True,
    )
    row = {
        "project_id": PROJECT,
        "trace_id": "trace",
        "id": "span",
        "observation_type": "span",
        "service_name": "service-a",
        "start_time": START,
        "_version": 1,
    }
    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=START + timedelta(hours=1),
        limit=26,
    )
    match_sql, match_params = builder.build_filter_match_query_from_seed_rows([row])
    for sql in (seed_sql, match_sql):
        assert "_physical_winner" in sql
        assert "service_name" in sql and "toStartOfHour(start_time)" in sql
        assert "is_deleted" in sql and "%(project_id)s" in sql
        assert "substring(input" not in sql and "substring(output" not in sql

    # Without an indexed sibling, content search must not scan wide strings
    # before LIMIT. The seed is a bounded identity superset, not a content match.
    assert "input" not in seed_sql and "output" not in seed_sql
    assert "positionUTF8" not in seed_sql
    assert "needle" not in seed_params.values()
    assert "LIMIT %(filter_seed_limit)s" in seed_sql
    assert seed_params["filter_seed_limit"] == 26

    # Replay the complete predicate only for the seeded physical identities.
    assert "input" in match_sql and "output" in match_sql
    assert "positionUTF8" in match_sql
    assert "needle" in match_params.values()
    assert "IN %(candidate_span_identities)s" in match_sql
    assert len(match_params["candidate_span_identities"]) == 1
    identity = match_params["candidate_span_identities"][0]
    assert identity[:3] == (PROJECT, "trace", "span")
    assert identity[4:] == ("span", "service-a")


@pytest.mark.unit
def test_trace_content_search_uses_coordinate_pruned_replay():
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=[window(), search_filter()]
    )
    assert builder._uses_attribute_coordinate_replay()
    sql, params = builder.build_filter_match_query(["trace"])
    assert "_physical_winner" in sql
    assert "countIf(" in sql and " OR " in sql
    assert "input" in sql and "output" in sql
    assert "needle" in params.values()
