# ruff: noqa: F811 -- shared isolated ClickHouse fixtures
"""Batched KPI periods must equal independent complete statistical reads."""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest

from tracer.services.derived_metrics import reads
from tracer.tests.metric_read_fixtures import (  # noqa: F401
    ninety_day_source_policy,
    retained_sql_case,
)
from tracer.tests.test_derived_metric_parts import assert_same
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
from tracer.tests.test_derived_metric_uncertainty import reference, unit

PREVIOUS = {
    **WINDOW,
    "window_start": AT - timedelta(hours=1),
    "window_end": AT,
}


def assert_periods(store, metric, definition, manifest):
    expected = [
        reads._aggregate(store, metric, definition, manifest, window)
        for window in (WINDOW, PREVIOUS)
    ]
    with patch.object(reads, "_rows", wraps=reads._rows) as query:
        actual = reads._aggregate_periods(
            store, metric, definition, manifest, WINDOW, PREVIOUS
        )
    assert query.call_count == 1
    for row, expected_row in zip(actual, expected, strict=True):
        assert_same(row, expected_row)
        actual_point = reads.point(row, definition)
        expected_point = reads.point(expected_row, definition)
        # Use the established CI-equivalence tolerance at both wire levels:
        # aggregation order can change the last floating-point bit.
        assert_same(actual_point.pop("uncertainty"), expected_point.pop("uncertainty"))
        assert_same(actual_point, expected_point)
    return actual


@pytest.mark.parametrize("text", ["sum(y)", "ratio(y, x)", "mean(y > 0)"])
@pytest.mark.parametrize("design", ["closed", "live", "unweighted"])
def test_periods_keep_independent_scopes_and_all_ci_fields(
    publication_store, text, design
):
    scope, metric, definition, attempt, manifest = case(text)
    for period, start in enumerate([AT, PREVIOUS["window_start"]]):
        stage_parts(
            publication_store,
            scope,
            metric,
            definition,
            attempt,
            [
                unit(
                    value + period,
                    1 if design == "unweighted" else 0.5,
                    design=design,
                    population=24 if design == "closed" else None,
                    x=index + 1,
                )
                for index, value in enumerate([0, 0, 0, 1, 1, 1, 2, 2, 3, 4, 0, 1])
            ],
            start=start,
            scope_key=f"period-{period}",
        )
    rows = assert_periods(publication_store, metric, definition, manifest)
    assert all(row["status"] == "ok" for row in rows)


@pytest.mark.django_db
@pytest.mark.parametrize("text", ["sum(y)", "ratio(y, x)", "mean(y > 0)"])
@pytest.mark.parametrize("design", ["closed", "live", "unweighted"])
def test_stored_cohorts_keep_the_oracle_and_backfill_invariant_ci(
    publication_store, text, design, retained_sql_case
):
    scope, metric, definition, attempt, manifest = retained_sql_case(text)
    definition.cohort_key = "region"
    values = [0, 0, 0, 1, 1, 1, 2, 2, 3, 4, 0, 1]
    probability = 1 if design == "unweighted" else 0.5
    for period, start in enumerate([AT, PREVIOUS["window_start"]]):
        for quarter in range(2):
            units = []
            for cohort in ["east", "west"]:
                for index in range(quarter * 6, (quarter + 1) * 6):
                    row = unit(
                        values[index] + period,
                        probability,
                        design=design,
                        population=48 if design == "closed" else None,
                        x=index + 1,
                    )
                    units.append((row[0], cohort, *row[2:]))
            stage_parts(
                publication_store,
                scope,
                metric,
                definition,
                attempt,
                units,
                start=start + timedelta(minutes=quarter * 15),
                scope_key=f"period-{period}",
                rollup="cohort",
            )
    current, _ = assert_periods(publication_store, metric, definition, manifest)
    # Independent centering uses the ACTUAL sampling stratum shared by east/west,
    # not metric dimensions as synthetic independent strata. This is an overall
    # read of all 24 sampled units (n_h=.5*48=24), with no outside-domain zeros.
    # Grouped reads instead retain their requested domain and its zero padding.
    expected = reference(
        "sum" if text == "sum(y)" else "ratio",
        design,
        y=([int(value > 0) for value in values] if "> 0" in text else values) * 2,
        p=[probability] * 24,
        x=list(range(1, 13)) * 2 if text == "ratio(y, x)" else None,
        stratum=["h1"] * 24,
        population=[48] * 24,
        rate=text == "mean(y > 0)",
    )
    assert current["value"] == pytest.approx(expected["value"])
    assert current["unit_count"] == current["valid_count"] == 24
    if design == "unweighted" and text == "sum(y)":
        assert current["ci_reason"] == "unweighted_total"
    else:
        assert current["ci_status"] == "ok"
        for name in ("std_error", "ci_low", "ci_high", "effective_n", "ci_df"):
            assert current[name] == pytest.approx(expected[name], rel=1e-9, abs=1e-12)
        assert current["ci_interval"] == expected["interval"]

    @contextmanager
    def pin(*args, **kwargs):
        yield publication_store, manifest, definition

    with (
        patch.object(reads, "metric_read", pin),
        patch.object(reads, "provenance", return_value={}),
    ):
        definition.backfill_cursor = {"start": AT.isoformat()}
        without_previous = reads.read_kpi(metric, WINDOW)
        definition.backfill_cursor = {"start": PREVIOUS["window_start"].isoformat()}
        with_previous = reads.read_kpi(metric, WINDOW)
    assert without_previous["previous"] is None
    assert with_previous["previous"]["point_status"] == "ok"
    before, after = without_previous["current"], with_previous["current"]
    assert_same(before.pop("uncertainty"), after.pop("uncertainty"))
    assert_same(before, after)


@pytest.mark.parametrize("text", ["sum(y)", "ratio(y, x)", "mean(y > 0)"])
def test_periods_keep_heterogeneous_weights_and_missing_counts(publication_store, text):
    scope, metric, definition, attempt, manifest = case(text)
    for period, start in enumerate([AT, PREVIOUS["window_start"]]):
        stage_parts(
            publication_store,
            scope,
            metric,
            definition,
            attempt,
            [
                unit(1 + period, 0.25, design="live", x=7),
                unit(3, 0.5, design="live", x=2),
                unit(None, 0.5, design="live", x=9),
            ],
            start=start,
            scope_key=f"period-{period}",
        )
    rows = assert_periods(publication_store, metric, definition, manifest)
    assert all(row["missing_count"] == 1 and row["unit_count"] == 3 for row in rows)


@pytest.mark.parametrize("mixed_period", [0, 1])
def test_mixed_scope_rejection_does_not_leak_between_periods(
    publication_store, mixed_period
):
    scope, metric, definition, attempt, manifest = case("ratio(y, x)")
    for period, start in enumerate([AT, PREVIOUS["window_start"]]):
        for quarter in range(2):
            stage_parts(
                publication_store,
                scope,
                metric,
                definition,
                attempt,
                [unit(1, 0.5, design="live", x=7), unit(None, 0.5, design="live", x=2)],
                start=start + timedelta(minutes=15 * quarter),
                scope_key=f"scope-{quarter if period == mixed_period else 0}",
            )
    rows = assert_periods(publication_store, metric, definition, manifest)
    assert rows[mixed_period]["status"] == "invalid_probability"
    assert rows[1 - mixed_period]["status"] == "ok"


@pytest.mark.parametrize("populated", [(), (0,), (1,)])
def test_absent_period_is_no_data_with_the_same_uncertainty(
    publication_store, populated
):
    scope, metric, definition, attempt, manifest = case("mean(y)")
    for period in populated:
        stage_parts(
            publication_store,
            scope,
            metric,
            definition,
            attempt,
            [unit(4, 1, design="live")],
            start=[AT, PREVIOUS["window_start"]][period],
        )
    rows = assert_periods(publication_store, metric, definition, manifest)
    for period in {0, 1} - set(populated):
        assert rows[period]["status"] == "no_data"
        assert rows[period]["value"] is None


def test_period_boundary_and_replacements_remain_half_open(publication_store):
    scope, metric, definition, attempt, manifest = case("sum(y)")
    for start, value in [
        (PREVIOUS["window_start"] - timedelta(minutes=15), 1000),
        (PREVIOUS["window_start"], 1),
        (AT - timedelta(minutes=15), 2),
        (AT, 4),
        (WINDOW["window_end"] - timedelta(minutes=15), 8),
        (WINDOW["window_end"], 2000),
    ]:
        stage_parts(
            publication_store,
            scope,
            metric,
            definition,
            attempt,
            [unit(value, 1, design="live")],
            start=start,
        )
    pending = uuid4()
    stage_parts(
        publication_store,
        scope,
        metric,
        definition,
        pending,
        [unit(32, 1, design="live")],
        start=AT,
        generation=2,
        table="metric_bucket_totals",
    )
    rows = assert_periods(publication_store, metric, definition, manifest)
    assert [row["value"] for row in rows] == [12, 3]
    manifest.allowed_attempts = lambda: [(1, attempt), (2, pending)]
    rows = assert_periods(publication_store, metric, definition, manifest)
    assert [row["value"] for row in rows] == [40, 3]
    publication_store.execute(
        "INSERT INTO metric_bucket_totals SELECT * REPLACE(toUInt8(1) AS is_deleted, toUInt64(3) AS version) FROM metric_bucket_totals"
    )
    rows = assert_periods(publication_store, metric, definition, manifest)
    assert [row["value"] for row in rows] == [8, 3]


@pytest.mark.parametrize("mixed", [False, True])
def test_unscoped_and_scoped_strata_do_not_merge(publication_store, mixed):
    scope, metric, definition, attempt, manifest = case("mean(y)")
    for period, start in enumerate([AT, PREVIOUS["window_start"]]):
        for quarter, scope_key in enumerate(["", "A", "B" if mixed else "A"]):
            stage_parts(
                publication_store,
                scope,
                metric,
                definition,
                attempt,
                [
                    unit(value + period, 0.5, design="closed", population=100)
                    for value in [0, 0, 0, 1, 1, 1, 2, 2, 3, 4, 0, 1]
                ],
                start=start + timedelta(minutes=15 * quarter),
                scope_key=scope_key,
            )
    rows = assert_periods(publication_store, metric, definition, manifest)
    assert all(
        row["status"] == ("invalid_probability" if mixed else "ok") for row in rows
    )


@pytest.mark.parametrize("kind", ["unique_count", "percentile"])
@pytest.mark.parametrize("populated", [(), (0,), (1,), (0, 1)])
def test_sketch_periods_keep_real_states_and_empty_metadata(
    publication_store, kind, populated
):
    from tracer.services.derived_metrics.buckets import KEYS, STATS, live_parts_select
    from tracer.tests.test_derived_metric_publication_clickhouse import field_query

    scope, metric, definition, attempt, manifest = case("mean(y)", kind=kind)
    definition.field_spec = {"value_alias": "y", "percentile": 0.5}
    columns = [
        "org_id",
        "workspace_id",
        "project_id",
        "metric_id",
        "metric_version",
        "generation",
        "attempt_id",
        "version",
        *KEYS,
        *STATS,
    ]
    for period in populated:
        field, params = field_query(
            metric=metric.id, start=[AT, PREVIOUS["window_start"]][period], n=4 + period
        )
        field = f"SELECT * REPLACE(%(period_scope)s AS sampling_scope) FROM ({field})"
        params["period_scope"] = f"period-{period}"
        parts = live_parts_select(definition, field)
        params.update(
            org=scope.organization,
            workspace=scope.workspace,
            project=scope.project,
            metric=metric.id,
            attempt=attempt,
        )
        publication_store.execute(
            f"INSERT INTO metric_bucket_totals_checkpoint ({', '.join(columns)}) SELECT %(org)s, %(workspace)s, %(project)s, %(metric)s, 1, 1, %(attempt)s, 1, {', '.join(KEYS + STATS)} FROM ({parts})",
            params,
        )
    rows = assert_periods(publication_store, metric, definition, manifest)
    for period in populated:
        assert rows[period]["value"] == (
            1.0 if kind == "unique_count" else 4.0 + period
        )
