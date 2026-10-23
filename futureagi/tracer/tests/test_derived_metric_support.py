# ruff: noqa: F811 -- shared native/PG fixtures
"""Pre-pooling support on frozen selection-unit rows, including persisted alerts.

These deterministic adversarial fixtures test necessary eligibility, not universal
coverage. No summary moments or mocked result rows supply the inference inputs.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest

from tracer.models.derived_metric import DerivedMetricMonitorDelivery
from tracer.models.monitor import UserAlertMonitorLog
from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.integrations import notify_run_monitors
from tracer.services.derived_metrics.queries import QUERY_SETTINGS, Scope, rollup_insert
from tracer.services.derived_metrics.service import read_results
from tracer.tests.test_derived_metric_api import ROOT, body, delivery_case  # noqa: F401
from tracer.tests.test_derived_metric_boundary import assert_withheld
from tracer.tests.test_derived_metric_clickhouse import metric_ch  # noqa: F401
from tracer.tests.test_derived_metric_uncertainty import (
    INPUT_COLUMNS,
    _scope,
    aggregate,
    one_group,
    unit,
)


def block_source(blocks, design):
    """(actual count, y, x, p, N, frozen label), expanded into distinct units."""
    parts, params = [], {"design": design}
    for i, (count, y, x, p, population, label) in enumerate(blocks):
        params.update(
            {
                f"{key}{i}": value
                for key, value in (
                    ("count", count),
                    ("y", y),
                    ("x", x),
                    ("p", p),
                    ("N", population),
                    ("h", label),
                )
            }
        )
        parts.append(f"""SELECT concat('block-{i}-', toString(number)) AS unit_id,
            'session' AS session_id, 'user' AS user_id, 'cohort' AS cohort, today() AS day,
            map('y', CAST(%(y{i})s, 'Nullable(Float64)'), 'x', CAST(%(x{i})s, 'Nullable(Float64)'),
                'resolved', CAST(%(y{i})s, 'Nullable(Float64)'), 'escalated', toNullable(0.0)) AS input_values,
            CAST(%(p{i})s, 'Nullable(Float64)') AS probability,
            toUInt8(0) AS invalid_probability, toUInt64(1) AS source_rows,
            'support-scope' AS sampling_scope, %(h{i})s AS sampling_stratum,
            %(design)s AS sampling_design, CAST(%(N{i})s, 'Nullable(UInt64)') AS population_size
            FROM numbers(%(count{i})s)""")
    return " UNION ALL ".join(parts), params


def freeze(client, scope, snapshot, blocks, design):
    source, params = block_source(blocks, design)
    client.execute(
        f"INSERT INTO derived_metric_inputs ({INPUT_COLUMNS}) "
        f"SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(snapshot)s, frozen.* FROM ({source}) frozen",
        {**params, **scope.parameters(), "snapshot": str(snapshot)},
        settings=QUERY_SETTINGS,
    )


def census_blocks(successes, tail=0):
    return [
        (50, tail, 1, 0.005, 10_000, "sample"),
        (successes, 1 - tail, 1, 1.0, 100_000, "census"),
        (100_000 - successes, tail, 1, 1.0, 100_000, "census"),
    ]


@pytest.mark.parametrize("design", ["closed", "live"])
@pytest.mark.parametrize("tail", [0, 1])
@pytest.mark.parametrize("successes", [0, 1, 50_000, 99_999, 100_000])
def test_known_census_outcomes_never_supply_sampled_tail_support(
    metric_ch, design, tail, successes
):
    scope, snapshot = _scope(), uuid4()
    freeze(metric_ch, scope, snapshot, census_blocks(successes, tail), design)
    (row,) = aggregate(metric_ch, scope, snapshot, "mean(y > 0)", ["y"])
    value = successes / 110_000
    assert_withheld(row, 1 - value if tail else value)
    assert row["valid_count"] == 100_050


@pytest.mark.parametrize("design", ["closed", "live"])
@pytest.mark.parametrize("kind", ["missing_cell", "apparent_census", "genuine_census"])
def test_raw_incomplete_rows_cannot_disappear_from_support(metric_ch, design, kind):
    scope, snapshot = _scope(), uuid4()
    sampled_p = 1.0 if kind == "genuine_census" else 0.005
    other_p = 0.2 if kind == "missing_cell" else 1.0
    freeze(
        metric_ch,
        scope,
        snapshot,
        [
            (10, None, 1, sampled_p, 10_000, "missing"),
            (5, 1, 1, other_p, 100, "observed"),
            (15, 0, 1, other_p, 100, "observed"),
        ],
        design,
    )
    (row,) = aggregate(metric_ch, scope, snapshot, "mean(y > 0)", ["y"])
    assert (
        row["unit_count"] == 30
        and row["valid_count"] == 20
        and row["missing_count"] == 10
    )
    assert row["value"] == 0.25 and row["status"] == "ok"
    if kind == "genuine_census":
        assert row["ci_status"] == "ok" and row["std_error"] == 0
        assert row["ci_low"] == row["ci_high"] == 0.25 and row["ci_df"] is None
    else:
        assert row["ci_status"] == "not_estimable"
        assert row["ci_reason"] in ("unstable_variance", "no_degrees_of_freedom")
        assert (
            row["ci_low"] is row["ci_high"] is row["std_error"] is row["ci_df"] is None
        )


@pytest.mark.parametrize("rollup", ["overall", "session", "user", "cohort", "day"])
def test_every_rollup_checks_support_before_pooling(metric_ch, rollup):
    scope, snapshot = _scope(), uuid4()
    freeze(metric_ch, scope, snapshot, census_blocks(1), "live")
    (row,) = aggregate(
        metric_ch,
        scope,
        snapshot,
        "ratio((y > 0) and (x > 0), x > 0)",
        ["y", "x"],
        rollup,
    )
    assert_withheld(row, 1 / 110_000)


@pytest.mark.parametrize("design", ["closed", "live", "unweighted"])
@pytest.mark.parametrize(
    "formula,emits",
    [
        ("ratio(y, x)", False),  # x=1 in the sample is not a structural bound
        ("ratio(y, 1)", True),
        ("mean(y)", True),
    ],
)
def test_observed_denominator_constant_is_not_formula_proof(
    metric_ch, design, formula, emits
):
    p = 1.0 if design == "unweighted" else 0.5
    row = one_group(
        metric_ch,
        [unit(y, p, design=design, population=8) for y in (1, 2, 4, 5)],
        formula,
    )
    assert row["value"] == 3 and row["status"] == "ok"
    assert (row["ci_status"] == "ok") is emits
    if not emits:
        assert_withheld(row, 3)


@pytest.mark.parametrize("design", ["closed", "live"])
def test_sample_only_proportionality_cannot_borrow_census_residual(metric_ch, design):
    rows = [unit(y, 0.5, design=design, population=8, x=y / 2) for y in (2, 4, 6, 8)]
    rows += [unit(9, 1.0, "known", design=design, population=1, x=1)]
    row = one_group(metric_ch, rows, "ratio(y, x)")
    assert_withheld(row, 49 / 21)


@pytest.mark.parametrize("design", ["closed", "live"])
def test_conditional_zero_denominator_units_are_not_failures(metric_ch, design):
    rows = [unit(1, 0.5, population=8, design=design, x=1) for _ in range(2)]
    rows += [unit(0, 0.5, population=8, design=design, x=0) for _ in range(2)]
    rows += [unit(0, 1.0, "known", population=1, design=design, x=1)]
    row = one_group(metric_ch, rows, "ratio((y > 0) and (x > 0), x > 0)")
    assert_withheld(row, 4 / 5)


def test_incomplete_probability_mixture_still_invalidates_live_cell(metric_ch):
    rows = [unit(y, 0.5, design="live") for y in (0, 1, 0, 1)]
    rows += [unit(None, 0.25, design="live")]
    assert_withheld(one_group(metric_ch, rows, "mean(y > 0)"), 0.5)


def test_unknown_incomplete_probability_cannot_prove_census(metric_ch):
    rows = [unit(y, 1.0, design="live") for y in (0, 1, 0, 1)]
    rows += [unit(None, None, "missing", design="live")]
    row = one_group(metric_ch, rows, "mean(y > 0)")
    assert row["value"] == 0.5 and row["ci_status"] == "not_estimable"
    assert row["std_error"] is row["ci_low"] is row["ci_high"] is None


@pytest.mark.parametrize("missing_design", ["", "closed"])
def test_incomplete_uncaptured_or_mixed_design_cannot_prove_census(
    metric_ch, missing_design
):
    rows = [unit(y, 1.0, design="live") for y in (0, 1, 0, 1)]
    rows += [unit(None, 1.0, "missing", design=missing_design, population=1)]
    row = one_group(metric_ch, rows, "mean(y > 0)")
    assert row["value"] == 0.5 and row["ci_status"] == "not_estimable"
    assert row["std_error"] is row["ci_low"] is row["ci_high"] is None


def test_iid_labels_do_not_create_artificial_support_cells(metric_ch):
    rows = [unit(y, 1.0, str(y), design="unweighted") for y in (0, 1, 0, 1)]
    row = one_group(metric_ch, rows, "mean(y > 0)")
    assert row["ci_status"] == "ok" and row["ci_df"] == 3


@pytest.mark.parametrize(
    "text,n,d,proportionality",
    [
        ("mean(y)", None, 1, None),
        ("ratio(y,x)", None, None, None),
        ("ratio(5 + 0*y,x)", 5, None, None),
        ("sum(y-y)", 0, 1, None),
        ("count_if(5+0*y)", 1, 1, None),
        ("ratio(y>0,y>0)", None, None, 1),
        ("ratio(0*(y>0),y>0)", 0, None, 0),
        ("mean(0*(y/y))", None, 1, None),
        ("mean((y/y)-(y/y))", None, 1, None),
        ("mean(0*y + 1/3)", None, 1, None),
    ],
)
def test_semantic_support_facts_are_narrow(text, n, d, proportionality):
    formula = compile_formula(text, ["y", "x"])
    assert formula.numerator_constant == n
    assert formula.denominator_constant == d
    assert formula.proportionality == proportionality


@pytest.fixture
def saved(auth_client, observe_project):
    data = body(observe_project)
    # The test freezes synthetic weighted rows; formula semantics are persisted,
    # not injected proof flags. The denominator is a proven indicator superset.
    data["formula"] = "ratio((resolved > 0) and (escalated >= 0), escalated >= 0)"
    response = auth_client.post(ROOT, data, format="json")
    assert response.status_code == 201, response.data
    return response.data


@pytest.mark.django_db
@pytest.mark.parametrize("tail", [0, 1])
def test_persisted_census_event_abstains_before_delivery(
    metric_ch, delivery_case, tail
):
    run, monitor = delivery_case
    run.source_manifest = {"sampling_scope": "support-scope"}
    run.save(update_fields=["source_manifest"])
    metric = run.definition.metric
    scope = Scope(metric.organization_id, metric.workspace_id, metric.project_id)
    freeze(metric_ch, scope, run.snapshot_id, census_blocks(1, tail), "live")
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
    value = 1 - 1 / 110_000 if tail else 1 / 110_000
    assert metric_ch.execute(
        "SELECT value, ci_status, ci_reason, ci_low, ci_high FROM derived_metrics WHERE result_id = %(result)s",
        {"result": str(run.result_id)},
    ) == [(pytest.approx(value), "not_estimable", "unstable_variance", None, None)]
    monitor.alert_when = "confident"
    monitor.threshold_operator = "greater_than" if tail else "less_than"
    monitor.critical_threshold_value = 0.9992 if tail else 0.0008
    monitor.save(
        update_fields=["alert_when", "threshold_operator", "critical_threshold_value"]
    )
    # Real persisted CH reads, PG locks, ledger and alert logs; only connection
    # routing and external notification transports are substituted.
    with (
        patch(
            "tracer.services.derived_metrics.service.get_clickhouse_client",
            return_value=metric_ch,
        ),
        patch("tracer.utils.monitor._send_slack_notification"),
        patch("tracer.utils.monitor._send_alert_email"),
    ):
        result = read_results(run, include_uncertainty=True)["results"][0]
        assert result["value"] == pytest.approx(value)
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
