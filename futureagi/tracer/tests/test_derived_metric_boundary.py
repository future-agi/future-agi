# ruff: noqa: F811 -- shared native/PG fixtures
"""Boundary inference on actual ClickHouse unit rows, not summary-moment SQL.

Each numbers() row is one observed selection unit. These are synthetic frozen
input fixtures, not collector/sampler end-to-end tests. The large-census example
also has an explicit finite-population oracle. No Monte Carlo coverage is claimed.
"""

import math
import random
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from scipy import stats

from tracer.models.derived_metric import DerivedMetricMonitorDelivery
from tracer.models.monitor import UserAlertMonitorLog
from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.integrations import (
    monitor_comparison_value,
    notify_run_monitors,
)
from tracer.services.derived_metrics.queries import (
    QUERY_SETTINGS,
    Scope,
    aggregate_select,
    rollup_insert,
)
from tracer.services.derived_metrics.service import read_results, shape_result
from tracer.tests.test_derived_metric_api import delivery_case, saved  # noqa: F401
from tracer.tests.test_derived_metric_clickhouse import metric_ch  # noqa: F401
from tracer.tests.test_derived_metric_uncertainty import INPUT_COLUMNS


def observed_source(strata, design):
    """(n, successes, probability, population, label), one row per observation."""
    parts, params = [], {"design": design}
    for i, (n, successes, probability, population, label) in enumerate(strata):
        params.update(
            {
                f"{key}{i}": value
                for key, value in (
                    ("n", n),
                    ("k", successes),
                    ("p", probability),
                    ("N", population),
                    ("label", label),
                )
            }
        )
        parts.append(f"""SELECT concat(%(label{i})s, '-{i}-', toString(number)) AS unit_id,
            '' AS session_id, '' AS user_id, 'boundary' AS cohort, today() AS day,
            map('y', toNullable(toFloat64(number < %(k{i})s)), 'x', toNullable(1.0),
                'resolved', toNullable(toFloat64(number < %(k{i})s)),
                'escalated', toNullable(0.0)) AS input_values,
            toNullable(toFloat64(%(p{i})s)) AS probability,
            toUInt8(0) AS invalid_probability, toUInt64(1) AS source_rows,
            'boundary-scope' AS sampling_scope, %(label{i})s AS sampling_stratum,
            %(design)s AS sampling_design, toNullable(toUInt64(%(N{i})s)) AS population_size
            FROM numbers(%(n{i})s)""")
    return " UNION ALL ".join(parts), params


def estimate(client, strata, design, formula_text="mean(y > 0)"):
    source, params = observed_source(strata, design)
    return estimate_source(client, source, params, formula_text)


def estimate_source(client, source, params, formula_text="mean(y > 0)"):
    formula = compile_formula(formula_text, ["y", "x"])
    rows, columns = client.execute(
        aggregate_select(source, formula, rollup="overall"),
        {**params, **formula.parameters},
        with_column_types=True,
        settings=QUERY_SETTINGS,
    )
    assert len(rows) == 1
    return dict(zip((column[0] for column in columns), rows[0], strict=True))


def assert_withheld(row, value):
    assert row["status"] == "ok" and row["value"] == pytest.approx(value)
    assert row["ci_status"] == "not_estimable"
    assert row["ci_reason"] == "unstable_variance"
    assert row["ci_interval"] == ""
    assert row["std_error"] is row["ci_low"] is row["ci_high"] is row["ci_df"] is None
    shaped = shape_result(row, include_uncertainty=True)
    for operator in ("less_than", "greater_than"):
        monitor = SimpleNamespace(alert_when="confident", threshold_operator=operator)
        assert monitor_comparison_value(monitor, shaped) is None
        monitor.alert_when = "point"
        assert monitor_comparison_value(monitor, shaped) == pytest.approx(value)


@pytest.mark.parametrize("boundary", [0, 1])
@pytest.mark.parametrize("design", ["closed", "live"])
def test_large_census_cannot_supply_boundary_evidence(metric_ch, boundary, design):
    # Population A: 100 events among 10,000; observe 50 non-events at p=.005.
    # B: 100,000 known non-events. Complement both populations for the one boundary.
    strata = [
        (50, boundary * 50, 0.005, 10_000, "sample"),
        (100_000, boundary * 100_000, 1.0, 100_000, "census"),
    ]
    row = estimate(metric_ch, strata, design)
    truth = Fraction(100, 110_000)
    miss_probability = Fraction(math.comb(9900, 50), math.comb(10_000, 50))
    kish = Fraction(110_000**2, 50 * 200**2 + 100_000)
    old_upper = stats.t.ppf(0.975, 49) ** 2 / (
        float(kish) + stats.t.ppf(0.975, 49) ** 2
    )
    assert float(miss_probability) == pytest.approx(0.6042554223567107)
    assert old_upper == pytest.approx(0.0007003871743874937)
    assert old_upper < 0.0008 < truth
    assert row["effective_n"] == pytest.approx(
        float(kish)
    )  # diagnostic, not boundary evidence
    print(
        "boundary-counterexample",
        {
            "design": design,
            "boundary": boundary,
            "row": row,
            "zero_sample_probability_closed": float(miss_probability),
            "old_upper": old_upper,
            "truth_zero_case": float(truth),
        },
    )
    assert_withheld(row, boundary)


@pytest.mark.parametrize("boundary", [0, 1])
def test_fixed_raw_population_draw_withholds_mixed_boundary(metric_ch, boundary):
    # A finite population, not supplied group moments: ids 0..99 are events,
    # 100..9999 are non-events, and 10000..109999 are known census non-events.
    # This fixed draw is an adversarial regression, NOT a coverage campaign.
    chosen = tuple(random.Random(42).sample(range(10_000), 50))
    assert len(set(chosen)) == 50 and min(chosen) >= 100
    event = "number < 100" if boundary == 0 else "number >= 100"
    source = f"""SELECT map('y', toNullable(toFloat64({event}))) AS input_values,
        toNullable(if(number < 10000, 0.005, 1.0)) AS probability,
        toUInt8(0) AS invalid_probability,
        if(number < 10000, 'sample', 'census') AS sampling_stratum,
        'closed' AS sampling_design,
        toNullable(toUInt64(if(number < 10000, 10000, 100000))) AS population_size
        FROM numbers(110000) WHERE number IN %(chosen)s OR number >= 10000"""
    row = estimate_source(metric_ch, source, {"chosen": chosen})
    assert row["valid_count"] == row["unit_count"] == 100_050
    assert_withheld(row, boundary)


@pytest.mark.parametrize("design", ["closed", "live"])
@pytest.mark.parametrize("boundary", [0, 1])
@pytest.mark.parametrize("second", [(20, 0.2, 100), (80, 0.005, 16_000), (5, 1.0, 5)])
def test_multiple_strata_withhold_degenerate_rates(metric_ch, design, boundary, second):
    # Unequal fractions, equal fractions but distinct strata/sizes, and certainty.
    n, p, population = second
    row = estimate(
        metric_ch,
        [
            (50, boundary * 50, 0.005, 10_000, "a"),
            (n, boundary * n, p, population, "b"),
        ],
        design,
    )
    assert_withheld(row, boundary)


@pytest.mark.parametrize("boundary", [0, 1])
def test_live_heterogeneous_weights_within_one_label_are_not_homogeneous(
    metric_ch, boundary
):
    for p in (0.25, 1.0):
        row = estimate(
            metric_ch,
            [
                (50, boundary * 50, 0.005, 10_000, "same"),
                (20, boundary * 20, p, 80, "same"),
            ],
            "live",
        )
        assert_withheld(row, boundary)


@pytest.mark.parametrize("design", ["closed", "live", "unweighted"])
@pytest.mark.parametrize("boundary", [0, 1])
def test_single_uniform_rate_retains_boundary_interval(metric_ch, design, boundary):
    for n, population in ((50, 10_000), (40, 200)):
        p = 1.0 if design == "unweighted" else n / population
        row = estimate(metric_ch, [(n, boundary * n, p, population, "one")], design)
        k = stats.t.ppf(0.975, n - 1) ** 2 / n
        width = k / (1 + k)
        assert row["ci_status"] == "ok" and row["ci_interval"] == "wilson"
        assert row["value"] == boundary and row["ci_df"] == n - 1
        assert row["ci_low"] == pytest.approx(
            0 if boundary == 0 else 1 - width, abs=1e-10
        )
        assert row["ci_high"] == pytest.approx(width if boundary == 0 else 1, abs=1e-10)


@pytest.mark.parametrize("design", ["closed", "live"])
@pytest.mark.parametrize("successes", [(0, 0), (3, 7), (3, 0)])
def test_all_census_rate_is_exact_across_strata(metric_ch, design, successes):
    a, b = successes
    row = estimate(metric_ch, [(3, a, 1.0, 3, "a"), (7, b, 1.0, 7, "b")], design)
    assert row["ci_status"] == "ok" and row["ci_interval"] == "wald_t"
    assert row["value"] == row["ci_low"] == row["ci_high"] == (a + b) / 10
    assert row["std_error"] == 0 and row["ci_df"] is None


def test_degenerate_interior_stratified_rate_is_also_withheld(metric_ch):
    # Every stratum is internally unanimous, but they disagree. V=0 at R=.25
    # is not a homogeneous binomial boundary and needs its own design-aware bound.
    row = estimate(
        metric_ch, [(10, 10, 0.1, 100, "yes"), (30, 0, 0.1, 300, "no")], "closed"
    )
    assert_withheld(row, 0.25)


@pytest.mark.parametrize("design", ["closed", "live"])
@pytest.mark.parametrize("second", [(100, 0.25, 400), (100_000, 1.0, 100_000)])
def test_nondegenerate_heterogeneous_rate_retains_reference_interval(
    metric_ch, design, second
):
    n, p, population = second
    strata = [(50, 1, 0.005, 10_000, "a"), (n, 0, p, population, "b")]
    row = estimate(metric_ch, strata, design)
    # Independent centered arithmetic, no production expanded moments or Kish fallback.
    total_x = sum(
        Fraction(count) / Fraction(str(prob)) for count, _, prob, _, _ in strata
    )
    total_y = sum(Fraction(k) / Fraction(str(prob)) for _, k, prob, _, _ in strata)
    value = total_y / total_x
    variance = Fraction(0)
    for count, k, prob, _, _ in strata:
        probability = Fraction(str(prob))
        residual_one = (1 - value) / probability
        residual_zero = -value / probability
        squares = k * residual_one**2 + (count - k) * residual_zero**2
        if design == "closed":
            residual_sum = k * residual_one + (count - k) * residual_zero
            squares = (squares - residual_sum**2 / count) * Fraction(count, count - 1)
        variance += (1 - probability) * squares
    se = math.sqrt(float(variance)) / float(total_x)
    df = 49 if design == "closed" or p == 1 else 50 + n - 1
    t = stats.t.ppf(0.975, df)
    neff = float(value * (1 - value)) / se**2
    k = t * t / neff
    center = (float(value) + k / 2) / (1 + k)
    half = t / (1 + k) * math.sqrt(float(value * (1 - value)) / neff + k / (4 * neff))
    assert row["ci_status"] == "ok" and row["ci_interval"] == "wilson"
    assert row["ci_df"] == df
    assert row["value"] == pytest.approx(float(value), rel=1e-12)
    assert row["std_error"] == pytest.approx(se, rel=1e-10)
    assert row["ci_low"] == pytest.approx(center - half, abs=1e-10)
    assert row["ci_high"] == pytest.approx(center + half, abs=1e-10)


@pytest.mark.django_db
@pytest.mark.parametrize("boundary", [0, 1])
def test_persisted_boundary_cannot_notify_confident_monitor(
    metric_ch, delivery_case, boundary
):
    # Inject synthetic frozen unit rows, not a mocked result or interval. The saved
    # fixture's formula is a proven indicator ratio with an all-one denominator.
    run, monitor = delivery_case
    run.source_manifest = {"sampling_scope": "boundary-scope"}
    run.save(update_fields=["source_manifest"])
    metric = run.definition.metric
    scope = Scope(metric.organization_id, metric.workspace_id, metric.project_id)
    source, params = observed_source(
        [
            (50, boundary * 50, 0.005, 10_000, "sample"),
            (100_000, boundary * 100_000, 1.0, 100_000, "census"),
        ],
        "closed",
    )
    metric_ch.execute(
        f"INSERT INTO derived_metric_inputs ({INPUT_COLUMNS}) "
        f"SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(snapshot)s, frozen.* FROM ({source}) frozen",
        {**params, **scope.parameters(), "snapshot": str(run.snapshot_id)},
        settings=QUERY_SETTINGS,
    )
    query, params = rollup_insert(run.definition, run, run.result_id, "overall")
    metric_ch.execute(query, params, settings=QUERY_SETTINGS)
    metric_ch.execute(
        "INSERT INTO derived_metric_commits VALUES (%(org_id)s, %(workspace_id)s, %(project_id)s, "
        "%(metric)s, %(run)s, %(result)s, now64(6), 1)",
        {
            **scope.parameters(),
            "metric": str(metric.id),
            "run": str(run.id),
            "result": str(run.result_id),
        },
    )
    assert metric_ch.execute(
        "SELECT value, ci_status, ci_reason, ci_low, ci_high FROM derived_metrics WHERE result_id = %(result)s",
        {"result": str(run.result_id)},
    ) == [(float(boundary), "not_estimable", "unstable_variance", None, None)]
    monitor.alert_when = "confident"
    monitor.threshold_operator = "less_than" if boundary == 0 else "greater_than"
    monitor.critical_threshold_value = 0.0008 if boundary == 0 else 0.9992
    monitor.save(
        update_fields=["alert_when", "threshold_operator", "critical_threshold_value"]
    )
    # Only client routing and external notification transport are substituted.
    # Real persisted-result reading, gating, PG locking, ledger and alert log run.
    with (
        patch(
            "tracer.services.derived_metrics.service.get_clickhouse_client",
            return_value=metric_ch,
        ),
        patch("tracer.utils.monitor._send_slack_notification"),
        patch("tracer.utils.monitor._send_alert_email"),
    ):
        result = read_results(run, include_uncertainty=True)["results"][0]
        assert (
            result["value"] == boundary
            and result["uncertainty"]["status"] == "not_estimable"
        )
        assert notify_run_monitors(run) == 0
        assert not DerivedMetricMonitorDelivery.objects.filter(
            monitor=monitor, run=run
        ).exists()
        assert not UserAlertMonitorLog.objects.filter(alert=monitor).exists()
        monitor.alert_when = "point"
        monitor.save(update_fields=["alert_when"])
        assert notify_run_monitors(run) == 1
        assert (
            DerivedMetricMonitorDelivery.objects.filter(
                monitor=monitor, run=run
            ).count()
            == 1
        )
        assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == 1
