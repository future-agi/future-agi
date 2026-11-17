"""A Time to First Token filter reads the key spans store it under.

The Observe filter panel offers "Time to First Token" by its name,
time_to_first_token. traceAI and the gateway store the value, in ms, as
gen_ai.server.time_to_first_token. The trace and span lists, the graphs and the
dashboard read that key, as stored, so a filter matches the value the dashboard
shows.
"""

import pytest

from tracer.services.clickhouse.query_builders.dashboard import SYSTEM_METRICS
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_span_filter_plans,
    compile_trace_filter_plans,
)

pytestmark = pytest.mark.unit

STORED_KEY = "gen_ai.server.time_to_first_token"
STORED_VALUE = f"span_attr_num['{STORED_KEY}']"


def _ttft_filter(op="greater_than", value=500):
    return {
        "column_id": "time_to_first_token",
        "property_id": "system_attribute:traces:time_to_first_token",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": op,
            "filter_value": value,
        },
    }


def _plan_sql(plan):
    return " ".join([*plan.aggregates, plan.predicate, plan.seed_predicate])


@pytest.mark.parametrize(
    "compile_plans", [compile_trace_filter_plans, compile_span_filter_plans]
)
def test_the_list_filter_reads_the_stored_key(compile_plans):
    (plan,) = compile_plans([_ttft_filter()])

    assert STORED_VALUE in _plan_sql(plan)
    assert f"mapContains(span_attr_num, '{STORED_KEY}')" in _plan_sql(plan)
    assert "time_to_first_token" not in plan.params.values()
    assert 500 in plan.params.values()


@pytest.mark.parametrize("op", ["greater_than", "less_than"])
def test_the_graph_filter_reads_the_stored_key(op):
    where, params = ClickHouseFilterBuilder().translate([_ttft_filter(op)])

    assert f"mapContains(span_attr_num, '{STORED_KEY}')" in where
    assert STORED_VALUE in where
    assert "time_to_first_token" not in params.values()
    assert 500 in params.values()


def test_the_dashboard_reads_the_same_stored_milliseconds():
    from tracer.services.clickhouse.query_builders.ttft import (
        TTFT_FILTER_EXPRESSION,
        TTFT_VALUE,
    )

    assert SYSTEM_METRICS["time_to_first_token"] == ("spans", TTFT_VALUE)
    assert TTFT_VALUE in TTFT_FILTER_EXPRESSION
    assert STORED_VALUE in TTFT_VALUE


UNIT_RULE = (
    "if(span_attr_str['traceai.timing.server_ttft.unit'] = 's', "
    f"{STORED_VALUE} * 1000, {STORED_VALUE})"
)


@pytest.mark.parametrize(
    "compile_plans", [compile_trace_filter_plans, compile_span_filter_plans]
)
def test_the_list_filter_reads_seconds_only_when_traceai_marks_the_unit(
    compile_plans,
):
    (plan,) = compile_plans([_ttft_filter()])

    assert UNIT_RULE in _plan_sql(plan)


def test_the_graph_filter_reads_seconds_only_when_traceai_marks_the_unit():
    where, _ = ClickHouseFilterBuilder().translate([_ttft_filter()])

    assert UNIT_RULE in where


def test_the_dashboard_reads_seconds_only_when_traceai_marks_the_unit():
    assert SYSTEM_METRICS["time_to_first_token"] == ("spans", UNIT_RULE)
