# ruff: noqa: F811 -- fixtures shared with the native uncertainty suite
"""Semantic-rate and certainty-stratum regressions on the production estimator."""

import math
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from scipy import stats

from tracer.services.derived_metrics.integrations import monitor_comparison_value
from tracer.services.derived_metrics.queries import (
    QUERY_SETTINGS,
    rollup_insert,
    snapshot_select,
)
from tracer.services.derived_metrics.service import shape_result
from tracer.tests.test_derived_metric_uncertainty import (
    REPLICATES,
    SEED,
    Batch,
    _capture,
    _scope,
    metric_ch,  # noqa: F401
    native_ch,  # noqa: F401
    one_group,
    stage_and_rollup,
    unit,
)


@pytest.mark.parametrize("formula", ["mean(y)", "ratio(y, x)", "ratio(y, 1)"])
@pytest.mark.parametrize("design", ["closed", "live", "unweighted"])
@pytest.mark.parametrize("observed", [0.0, 1.0])
def test_numeric_unanimity_is_not_boolean_provenance(
    metric_ch, formula, design, observed
):
    p = 1.0 if design == "unweighted" else 0.005
    rows = [unit(observed, p, design=design, population=10_000) for _ in range(50)]
    row = one_group(metric_ch, rows, formula)
    assert row["status"] == "ok" and row["value"] == observed
    assert row["ci_status"] == "not_estimable"
    assert row["ci_reason"] == "unstable_variance"
    assert row["std_error"] is row["ci_low"] is row["ci_high"] is None
    shaped = shape_result(row, include_uncertainty=True)
    for operator in ("less_than", "greater_than"):
        monitor = SimpleNamespace(alert_when="confident", threshold_operator=operator)
        assert monitor_comparison_value(monitor, shaped) is None
    point = SimpleNamespace(alert_when="point", threshold_operator="less_than")
    assert monitor_comparison_value(point, shaped) == observed


@pytest.mark.parametrize("design", ["closed", "live", "unweighted"])
def test_numeric_zero_one_sample_uses_generic_mean_interval(metric_ch, design):
    p = 1.0 if design == "unweighted" else 0.5
    row = one_group(
        metric_ch,
        [unit(y, p, design=design, population=8) for y in (0, 1, 0, 1)],
        "mean(y)",
    )
    assert row["status"] == row["ci_status"] == "ok"
    assert row["value"] == 0.5 and row["ci_interval"] == "wald_t"
    # The outcome's observed range does not bound the population's numeric mean.
    assert row["ci_low"] < 0 and row["ci_high"] > 1


@pytest.mark.parametrize(
    "formula",
    [
        "mean(y > 0)",
        "mean(not y)",
        "ratio(y > 0, 1)",
        "ratio((y > 0) and (x > 0), x > 0)",
        "ratio((y > 0) * (x > 0), x > 0)",
    ],
)
def test_proven_predicates_keep_boundary_intervals(metric_ch, formula):
    rows = [unit(0, 0.005, population=10_000) for _ in range(50)]
    row = one_group(metric_ch, rows, formula)
    assert row["ci_status"] == "ok" and row["ci_interval"] == "wilson"
    assert 0 <= row["ci_low"] < row["ci_high"] <= 1
    assert row["ci_high"] - row["ci_low"] > 0.07


def test_unrelated_predicates_do_not_establish_a_bounded_ratio(metric_ch):
    # Observed numerator <= denominator does not prove this for unseen units.
    rows = [unit(0, 0.005, population=10_000) for _ in range(50)]
    row = one_group(metric_ch, rows, "ratio(y > 0, x > 0)")
    assert row["value"] == 0
    assert (
        row["ci_status"] == "not_estimable" and row["ci_reason"] == "unstable_variance"
    )


@pytest.mark.parametrize("source", ["eval", "span"])
def test_rate_proof_comes_from_frozen_definition_not_observed_binding(
    metric_ch, source
):
    scope, definition, run = _capture(metric_ch, mode="closed", population=6)
    if source == "span":
        # Same observed all-one numeric values, read from attributes instead of
        # evaluation outputs. Neither source establishes a population bound.
        definition.weighting = "none"
        definition.inputs = [
            {
                "alias": alias,
                "source": "span",
                "field": "attribute_number",
                "attribute_key": alias,
                "reducer": "latest",
            }
            for alias in ("resolved", "escalated")
        ]
        run.snapshot_id = uuid4()
        query, params = snapshot_select(
            definition, scope, run.window_start, run.window_end, run.as_of, [], ""
        )
        metric_ch.execute(
            "INSERT INTO derived_metric_inputs SELECT %(org_id)s, %(workspace_id)s, "
            f"%(project_id)s, %(snapshot_id)s, frozen.* FROM ({query}) frozen",
            {**params, "snapshot_id": str(run.snapshot_id)},
            settings=QUERY_SETTINGS,
        )
    # No live source remains: each persisted version is compiled against the same
    # frozen map. Its saved formula is the only semantic provenance needed.
    metric_ch.execute("TRUNCATE TABLE spans")
    metric_ch.execute("TRUNCATE TABLE tracer_eval_logger_v2")
    for version, formula in enumerate(("mean(resolved)", "mean(resolved > 0)"), 1):
        definition.version, definition.formula = version, formula
        result_id = uuid4()
        query, params = rollup_insert(definition, run, result_id, "overall")
        metric_ch.execute(query, params, settings=QUERY_SETTINGS)
        result = metric_ch.execute(
            "SELECT value, ci_status, ci_interval, ci_low, ci_high FROM derived_metric_results "
            "WHERE result_id = %(id)s",
            {"id": str(result_id)},
        )
        assert len(result) == 1 and result[0][0] == 1
        if version == 1:
            assert result[0][1:] == ("not_estimable", "", None, None)
        else:
            assert result[0][1:3] == ("ok", "wilson")
            assert 0 <= result[0][3] < result[0][4] <= 1


@pytest.mark.parametrize("design", ["closed", "live"])
def test_census_zeros_cannot_narrow_an_unrelated_sampled_total(metric_ch, design):
    uncertain = [
        unit(y, 0.0002, "sampled", design=design, population=10_000) for y in (1, 2)
    ]
    census = [
        unit(0, 1.0, "known", design=design, population=1000) for _ in range(1000)
    ]
    before = one_group(metric_ch, uncertain, "sum(y)")
    after = one_group(metric_ch, uncertain + census, "sum(y)")
    for field in ("value", "std_error", "ci_df", "ci_low", "ci_high"):
        assert after[field] == pytest.approx(before[field], rel=1e-12), field
    assert after["ci_status"] == "ok" and after["ci_df"] == 1
    expected_se = math.sqrt(24_995_000 if design == "closed" else 124_975_000)
    assert after["value"] == 15_000
    assert after["std_error"] == pytest.approx(expected_se, rel=1e-12)
    assert after["ci_low"] == pytest.approx(
        15_000 - stats.t.ppf(0.975, 1) * expected_se
    )
    monitor = SimpleNamespace(alert_when="confident", threshold_operator="greater_than")
    bound = monitor_comparison_value(
        monitor, shape_result(after, include_uncertainty=True)
    )
    assert bound < 5000


@pytest.mark.parametrize("design", ["closed", "live"])
def test_certainty_units_cannot_supply_a_missing_sample_degree(metric_ch, design):
    rows = [unit(2, 0.5, "sampled", design=design, population=4)]
    rows += [unit(0, 1.0, "known", design=design, population=10) for _ in range(10)]
    row = one_group(metric_ch, rows, "sum(y)")
    assert row["value"] == 4 and row["status"] == "ok"
    assert (
        row["ci_status"] == "not_estimable"
        and row["ci_reason"] == "no_degrees_of_freedom"
    )
    assert row["ci_df"] is row["ci_low"] is row["ci_high"] is None


def test_large_noncertainty_stratum_cannot_pool_away_a_small_stratum(metric_ch):
    rows = [unit(y, 0.0002, "small", population=10_000) for y in (1, 2)]
    rows += [unit(0, 0.5, "large", population=100) for _ in range(50)]
    row = one_group(metric_ch, rows, "sum(y)")
    assert row["ci_status"] == "ok" and row["ci_df"] == 1
    assert row["ci_low"] == pytest.approx(
        15_000 - stats.t.ppf(0.975, 1) * math.sqrt(24_995_000)
    )


@pytest.mark.parametrize("design", ["closed", "live"])
def test_all_census_strata_have_exact_intervals_without_sample_degrees(
    metric_ch, design
):
    rows = [
        unit(y, 1.0, f"known-{i}", design=design, population=1)
        for i, y in enumerate((1, 2, 3))
    ]
    row = one_group(metric_ch, rows, "sum(y)")
    assert row["ci_status"] == "ok" and row["ci_df"] is None
    assert row["value"] == row["ci_low"] == row["ci_high"] == 6
    assert row["std_error"] == 0


@pytest.mark.parametrize("design", ["closed", "live"])
def test_one_certainty_unit_is_exact(metric_ch, design):
    row = one_group(metric_ch, [unit(7, 1.0, design=design, population=1)], "mean(y)")
    assert row["ci_status"] == "ok" and row["ci_df"] is None
    assert row["value"] == row["ci_low"] == row["ci_high"] == 7
    assert row["std_error"] == 0


@pytest.mark.parametrize("design", ["closed", "live"])
def test_census_interval_matches_the_published_api_contract(metric_ch, design):
    from tracer.serializers.derived_metric import DerivedMetricUncertaintySerializer

    row = one_group(
        metric_ch,
        [unit(y, 1.0, design=design, population=3) for y in (1, 2, 3)],
        "mean(y)",
    )
    serializer = DerivedMetricUncertaintySerializer(
        data=shape_result(row, include_uncertainty=True)["uncertainty"]
    )
    assert serializer.is_valid(), serializer.errors
    # There is no estimated sampling variance in a census, hence no estimated df.
    # Keep the existing public contract: null, or a positive number of degrees.
    assert serializer.validated_data["degrees_of_freedom"] is None
    assert (
        serializer.validated_data["ci_low"] == serializer.validated_data["ci_high"] == 2
    )


def test_rare_large_numeric_mean_abstains_on_unseen_tail(native_ch):
    # Mean is exactly 10, not a rate: 9,900 zeroes and 100 values of 1,000.
    # The production persisted rollup, not scalar SQL emulation, sees each draw.
    rng = np.random.default_rng(SEED + 6)
    population = np.r_[np.zeros(9900), np.full(100, 1000.0)]
    batch = Batch("mean", "closed")
    draws = []
    for rep in range(REPLICATES):
        y = rng.choice(population, 50, replace=False)
        draws.append(y)
        batch.add(rep, y, np.full(50, 0.005), population=np.full(50, 10_000))
    rows = stage_and_rollup(native_ch, _scope(), "mean(y)", batch)
    monitor = SimpleNamespace(alert_when="confident", threshold_operator="less_than")
    withheld = issued = covered = alerts = 0
    for row, draw in zip(rows, draws, strict=True):
        _, value, error, low, high, status, *_ = row
        assert value == pytest.approx(draw.mean())
        bound = monitor_comparison_value(
            monitor,
            {
                "value": value,
                "uncertainty": {"status": status, "ci_low": low, "ci_high": high},
            },
        )
        alerts += bound is not None and bound < 5
        if not np.any(draw):
            assert status == "not_estimable" and error is low is high is None
            withheld += 1
        else:
            assert status == "ok" and row[9] == "wald_t"
            # Independent centered SRS mean variance with the finite correction.
            expected_se = math.sqrt((1 - 0.005) * draw.var(ddof=1) / 50)
            assert error == pytest.approx(expected_se, rel=1e-10)
            assert low == pytest.approx(
                draw.mean() - stats.t.ppf(0.975, 49) * expected_se
            )
            assert high == pytest.approx(
                draw.mean() + stats.t.ppf(0.975, 49) * expected_se
            )
            issued += 1
            covered += low <= 10 <= high
    zero_probability = float(stats.hypergeom.pmf(0, 10_000, 100, 50))
    assert abs(withheld / REPLICATES - zero_probability) < 0.04
    assert alerts == 0 and withheld + issued == REPLICATES
    print(
        "rare-large-numeric-mean",
        {
            "draws": REPLICATES,
            "withheld": withheld,
            "issued": issued,
            "covered_issued": covered,
            "conditional_coverage": covered / issued,
            "false_confident_alerts": alerts,
            "zero_sample_probability": zero_probability,
        },
    )


def test_imbalanced_certainty_stratum_total_calibration(native_ch):
    # Two SRS units supply ALL variance information. An additional 100 known-zero
    # units must not manufacture df=100. Quantiles define a fixed centered normal
    # finite population; draws are without replacement, and truth is exactly zero.
    population = stats.norm.ppf((np.arange(10_000) + 0.5) / 10_000)
    population -= population.mean()
    rng = np.random.default_rng(SEED + 7)
    batch = Batch("sum", "closed")
    draws = []
    for rep in range(REPLICATES):
        y = rng.choice(population, 2, replace=False)
        draws.append(y)
        batch.add(
            rep,
            np.r_[y, np.zeros(100)],
            np.r_[np.full(2, 0.0002), np.ones(100)],
            stratum=np.r_[np.zeros(2), np.ones(100)].astype(np.uint8),
            population=np.r_[np.full(2, 10_000), np.full(100, 100)].astype(np.uint64),
        )
    rows = stage_and_rollup(native_ch, _scope(), "sum(y)", batch)
    covered = old_df_covered = 0
    for row, draw in zip(rows, draws, strict=True):
        _, value, error, low, high, status, _, count, df, interval = row
        expected_value = 10_000 * draw.mean()
        expected_se = 10_000 * math.sqrt((1 - 0.0002) * draw.var(ddof=1) / 2)
        assert status == "ok" and interval == "wald_t" and count == 102
        assert df == 1
        assert value == pytest.approx(expected_value, rel=1e-10)
        assert error == pytest.approx(expected_se, rel=1e-8)
        assert low == pytest.approx(
            expected_value - stats.t.ppf(0.975, 1) * expected_se, rel=1e-8
        )
        assert high == pytest.approx(
            expected_value + stats.t.ppf(0.975, 1) * expected_se, rel=1e-8
        )
        covered += low <= 0 <= high
        old_df_covered += abs(value) <= stats.t.ppf(0.975, 100) * error
    coverage = covered / REPLICATES
    print(
        "certainty-stratum-calibration",
        {
            "draws": REPLICATES,
            "census_units": 100,
            "sampled_units": 2,
            "coverage": coverage,
            "counterfactual_pooled_df_coverage": old_df_covered / REPLICATES,
        },
    )
    assert 0.92 <= coverage <= 0.98
    assert old_df_covered / REPLICATES < 0.8
