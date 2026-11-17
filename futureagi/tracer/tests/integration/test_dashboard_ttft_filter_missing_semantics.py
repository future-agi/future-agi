"""Execute the dashboard's active TTFT predicate, not the shared constant alone."""

import pytest

from tracer.services.clickhouse.query_builders.dashboard import DashboardQueryBuilder
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_v1_sql_to_v2

pytestmark = [pytest.mark.integration, pytest.mark.slow]
STORED_KEY = "gen_ai.server.time_to_first_token"
UNIT_KEY = "traceai.timing.server_ttft.unit"

# Every case contains missing AND measured zero, both sides of 500, the exact
# boundary, seconds-marked values, and four unrecognized unit markers.
ROWS = [
    ("missing", None, None),
    ("zero", 0, None),
    ("ms200", 200, None),
    ("ms500", 500, None),
    ("ms800", 800, None),
    ("s200", 0.2, "s"),
    ("s800", 0.8, "s"),
    ("upper_s800", 800, "S"),
    ("ms_unit800", 800, "ms"),
    ("space_s800", 800, " s"),
    ("empty_unit800", 800, ""),
]
LOW = ["zero", "ms200", "s200"]
HIGH = ["ms800", "s800", "upper_s800", "ms_unit800", "space_s800", "empty_unit800"]


def _dashboard_predicate(builder_type, site, operator, value):
    ttft_filter = {
        "metric_type": "system_metric",
        "metric_name": "time_to_first_token",
        "operator": operator,
        "value": value,
        "source": "traces",
    }
    metric = {"name": "latency", "type": "system_metric", "aggregation": "avg"}
    if site == "metric":
        metric["filters"] = [ttft_filter]
    builder = builder_type(
        {
            "project_ids": ["11111111-1111-4111-8111-111111111111"],
            "time_range": {
                "custom_start": "2026-11-01T00:00:00+00:00",
                "custom_end": "2026-11-03T00:00:00+00:00",
            },
            "granularity": "day",
            "metrics": [metric],
            "filters": [ttft_filter] if site == "global" else [],
            "breakdowns": [],
        }
    )
    clauses, params = builder._build_where_clauses(
        "spans", "start_time", metric.get("filters", []), {}
    )
    (predicate,) = [clause for clause in clauses if STORED_KEY in clause]
    if builder_type is DashboardQueryBuilderV2:
        predicate = rewrite_v1_sql_to_v2(predicate)

    # Prove that the fragment being executed really is an active filter in the
    # public latency query (TTFT aggregation's own presence guard cannot help).
    sql, public_params = builder.build_metric_query(metric)
    active_where = sql.split("\nWHERE ", 1)[1].split("\nGROUP BY ", 1)[0]
    assert predicate in active_where
    assert "avg(latency_ms) AS value" in sql
    assert params and all(
        isinstance(number, (int, float)) for number in params.values()
    )
    assert {key: public_params[key] for key in params} == params
    # ch_client.command's adapter does not forward bound parameters. These are
    # only the numeric parameters emitted for the fixed test bounds above.
    return predicate % params


def _inline_rows(builder_type):
    numbers, strings = (
        ("attrs_number", "attrs_string")
        if builder_type is DashboardQueryBuilderV2
        else ("span_attr_num", "span_attr_str")
    )
    selects = []
    for label, stored, unit in ROWS:
        numeric_map = (
            "map()" if stored is None else f"map('{STORED_KEY}', {float(stored)})"
        )
        string_map = "map()" if unit is None else f"map('{UNIT_KEY}', '{unit}')"
        selects.append(
            f"SELECT '{label}' AS label, "
            f"CAST({numeric_map}, 'Map(String, Float64)') AS {numbers}, "
            f"CAST({string_map}, 'Map(String, String)') AS {strings}"
        )
    return " UNION ALL ".join(selects)


@pytest.mark.parametrize(
    "builder_type", [DashboardQueryBuilder, DashboardQueryBuilderV2]
)
@pytest.mark.parametrize("site", ["global", "metric"])
@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        ("less_than", 500, LOW),
        ("greater_than", 500, HIGH),
        ("equal_to", 0, ["zero"]),
        ("not_equal_to", 500, LOW + HIGH),
        ("greater_than_or_equal", 200, ["ms200", "ms500", "s200"] + HIGH),
        ("less_than_or_equal", 500, LOW + ["ms500"]),
        ("between", [0, 500], LOW + ["ms500"]),
        ("not_between", [500, 800], LOW),
    ],
    ids=[
        "lt500",
        "gt500-control",
        "eq0",
        "ne500",
        "ge200-control",
        "le500",
        "range0to500",
        "outside500to800",
    ],
)
def test_dashboard_numeric_ttft_bounds_exclude_missing_but_keep_measurements(
    ch_client, builder_type, site, operator, value, expected
):
    predicate = _dashboard_predicate(builder_type, site, operator, value)
    matched = ch_client.command(
        "SELECT arraySort(groupArray(label)) "
        f"FROM ({_inline_rows(builder_type)}) WHERE {predicate}"
    )
    assert matched == sorted(expected)
