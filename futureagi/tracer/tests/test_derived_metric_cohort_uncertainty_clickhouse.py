# ruff: noqa: F811 -- shared isolated ClickHouse fixtures
"""Metric dimensions are domains, not independent sampling strata."""

import json
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest

from tracer.services.derived_metrics import reads
from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.queries import aggregate_select
from tracer.tests.metric_read_fixtures import (  # noqa: F401
    ninety_day_source_policy,
    retained_sql_case,
)
from tracer.tests.test_derived_metric_parts import assert_same, by_group, rows_as_dicts
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_reads_clickhouse import (
    AT,
    WINDOW,
    case,
    stage_parts,
)
from tracer.tests.test_derived_metric_uncertainty import insert_units, reference, unit


@pytest.mark.django_db
@pytest.mark.parametrize(
    "text", ["mean(y)", "sum(y)", "ratio(y, x)", "mean(y > 0)", "count_if(y > 0)"]
)
@pytest.mark.parametrize("separate_strata", [False, True])
def test_requested_domain_matches_direct_units_and_centered_oracle(
    publication_store, text, separate_strata, retained_sql_case
):
    scope, metric, definition, attempt, manifest = retained_sql_case(text)
    definition.cohort_key = "region"
    units = []
    values = [0, 1, 0, 1] if "> 0" in text else [0, 1, 2, 3]
    for index, value in enumerate(values):
        cohort = "east" if index < 2 else "west"
        row = unit(
            value,
            0.5,
            stratum=cohort if separate_strata else "shared",
            population=8,
            x=[1, 3, 2, 4][index],
        )
        units.append((row[0], cohort, *row[2:]))
    # Each bucket holds both cohorts; the full stratum spans buckets AND dimensions.
    for quarter in range(2):
        stage_parts(
            publication_store,
            scope,
            metric,
            definition,
            attempt,
            units[quarter::2],
            start=AT + timedelta(minutes=15 * quarter),
            rollup="cohort",
        )
    snapshot = uuid4()
    insert_units(publication_store.client, scope, snapshot, units)
    formula = compile_formula(text, ["y", "x"])
    source = "SELECT * FROM derived_metric_inputs WHERE snapshot_id = %(snapshot)s"
    params = {"snapshot": str(snapshot), **formula.parameters}
    direct = {
        rollup: by_group(
            rows_as_dicts(
                publication_store.client,
                aggregate_select(source, formula, rollup=rollup),
                params,
            )
        )
        for rollup in ("overall", "cohort")
    }
    overall = reads._aggregate(publication_store, metric, definition, manifest, WINDOW)
    previous = {**WINDOW, "window_start": AT - timedelta(hours=1), "window_end": AT}
    current, absent = reads._aggregate_periods(
        publication_store, metric, definition, manifest, WINDOW, previous
    )

    @contextmanager
    def pin(*args, **kwargs):
        yield publication_store, manifest, definition

    with (
        patch.object(reads, "metric_read", pin),
        patch.object(reads, "provenance", return_value={}),
    ):
        trend = reads.read_trend(metric, {**WINDOW, "points": 1})["results"]
        grouped = reads.read_trend(
            metric, {**WINDOW, "points": 1, "group_by": "region"}
        )["results"]
    print(
        "COHORT_UNCERTAINTY "
        + json.dumps(
            {
                "formula": text,
                "separate_strata": separate_strata,
                "overall": overall,
                "batched_current": current,
                "direct": direct,
                "trend": trend,
                "grouped": grouped,
            },
            default=str,
        )
    )
    # Check each grouped domain FIRST, with full n_h=4 including outside-domain zeros.
    # For mean(y): east R=.5, west R=2.5, both have z=(-1,1,0,0),
    # V = (2/3)*2/4^2 = 1/12, df=1. Neither cohort may be lost by period batching.
    for row in grouped:
        expected = reads.point(direct["cohort"][row["group_key"]], definition)
        assert_same({key: row[key] for key in expected}, expected)
        if text == "mean(y)":
            assert row["uncertainty"]["std_error"] ** 2 == pytest.approx(1 / 12)
            assert row["uncertainty"]["degrees_of_freedom"] == 1
    expected = direct["overall"][""]
    transformed = [float(value > 0) if "> 0" in text else value for value in values]
    oracle = reference(
        formula.kind,
        "closed",
        y=transformed,
        p=[0.5] * 4,
        x=[1, 3, 2, 4] if text == "ratio(y, x)" else None,
        stratum=[row[4] for row in units],
        population=[8] * 4,
        rate=text == "mean(y > 0)",
    )
    for key in ("value", "std_error", "ci_low", "ci_high", "effective_n", "ci_df"):
        assert expected[key] == pytest.approx(oracle[key], rel=1e-9, abs=1e-12)
    # Shared-stratum mean: R=1.5, X=8, z=(-3,-1,1,3), sum(z)=0.
    # V = (2/3)*20/8^2 = 5/24, df=3, NOT the sum of cohort variances (1/8).
    if text == "mean(y)" and not separate_strata:
        assert expected["std_error"] ** 2 == pytest.approx(5 / 24)
        assert expected["ci_df"] == 3
    assert_same(overall, expected)
    assert_same(current, expected)
    assert absent["status"] == "no_data"
    expected_point = reads.point(expected, definition)
    assert_same({key: trend[0][key] for key in expected_point}, expected_point)


@pytest.mark.django_db
def test_dimension_projection_combines_only_unrequested_dimensions(
    publication_store, retained_sql_case
):
    scope, metric, definition, attempt, manifest = retained_sql_case("mean(y)")
    definition.group_dims = ["region", "tier"]
    units = []
    for index in range(8):
        region = "east" if index < 4 else "west"
        tier = "gold" if index % 4 < 2 else "silver"
        cohort = json.dumps(
            [json.dumps(["value", region]), json.dumps(["value", tier])]
        )
        row = unit(index, 0.5, population=16)
        units.append((row[0], cohort, *row[2:]))
    stage_parts(
        publication_store, scope, metric, definition, attempt, units, rollup="cohort"
    )

    @contextmanager
    def pin(*args, **kwargs):
        yield publication_store, manifest, definition

    with (
        patch.object(reads, "metric_read", pin),
        patch.object(reads, "provenance", return_value={}),
    ):
        grouped = reads.read_trend(
            metric, {**WINDOW, "points": 1, "group_by": "region"}
        )["results"]
    assert len(grouped) == 2
    for row in grouped:
        region = json.loads(row["group_key"])[1]
        assert row["value"] == {"east": 1.5, "west": 5.5}[region]
        assert row["eligible_count"] == row["evaluated_count"] == 4
        # n_h=8, four actual units per requested region, four outside-domain zeros.
        # z=(-3,-1,1,3,0,0,0,0), X=8, V=(4/7)*20/64=5/28.
        assert row["uncertainty"]["status"] == "ok"
        assert row["uncertainty"]["std_error"] ** 2 == pytest.approx(5 / 28)
        assert row["uncertainty"]["degrees_of_freedom"] == 3
    overall = reads._aggregate(publication_store, metric, definition, manifest, WINDOW)
    assert overall["value"] == 3.5
    assert overall["std_error"] ** 2 == pytest.approx(3 / 8)
    assert overall["ci_df"] == 7


def test_support_is_checked_for_requested_domain_not_storage_cohort(publication_store):
    scope, metric, definition, attempt, manifest = case("mean(y)")
    units = []
    for value in (0, 0, 1, 1):
        row = unit(value, 0.5, population=8)
        units.append((row[0], "east" if value == 0 else "west", *row[2:]))
    stage_parts(
        publication_store, scope, metric, definition, attempt, units, rollup="cohort"
    )
    overall = reads._aggregate(publication_store, metric, definition, manifest, WINDOW)
    assert overall["ci_status"] == "ok"
    assert overall["std_error"] ** 2 == pytest.approx(1 / 24)
    assert overall["ci_df"] == 3
    sql, params = reads.bucket_select(metric, definition, manifest, WINDOW)
    grouped = reads._rows(
        publication_store,
        reads._aggregate_select(sql, definition, params, grouped=True),
        params,
    )
    assert len(grouped) == 2
    assert {row["group_key"] for row in grouped} == {"east", "west"}
    for row in grouped:
        assert row["ci_status"] == "not_estimable"
        assert row["ci_reason"] == "unstable_variance"
        assert row["std_error"] is None


def test_replacement_collapse_keeps_cohorts_before_overall_centering(publication_store):
    scope, metric, definition, base, manifest = case("mean(y)")

    def rows(values, cohort):
        return [
            (row[0], cohort, *row[2:])
            for row in (unit(value, 0.5, population=8) for value in values)
        ]

    stage_parts(
        publication_store,
        scope,
        metric,
        definition,
        base,
        rows([0, 1], "east") + rows([2, 3], "west"),
        rollup="cohort",
    )
    pending = uuid4()
    stage_parts(
        publication_store,
        scope,
        metric,
        definition,
        pending,
        rows([10, 11], "east"),
        generation=2,
        table="metric_bucket_totals",
        rollup="cohort",
    )
    assert (
        reads._aggregate(publication_store, metric, definition, manifest, WINDOW)[
            "value"
        ]
        == 1.5
    )
    manifest.allowed_attempts = lambda: [(1, base), (2, pending)]
    replaced = reads._aggregate(publication_store, metric, definition, manifest, WINDOW)
    assert replaced["value"] == 6.5
    assert replaced["unit_count"] == replaced["valid_count"] == 4
    assert replaced["std_error"] ** 2 == pytest.approx(65 / 24)
    publication_store.execute(
        "INSERT INTO metric_bucket_totals SELECT * REPLACE("
        "toUInt8(1) AS is_deleted, toUInt64(3) AS version) "
        "FROM metric_bucket_totals WHERE group_key = 'east'"
    )
    remaining = reads._aggregate(
        publication_store, metric, definition, manifest, WINDOW
    )
    assert remaining["value"] == 2.5
    assert remaining["unit_count"] == remaining["valid_count"] == 2
    assert remaining["std_error"] ** 2 == pytest.approx(1 / 12)
    assert remaining["ci_df"] == 1
