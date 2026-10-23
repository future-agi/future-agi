# ruff: noqa: F811 -- pytest fixtures are imported from the ClickHouse suite
"""Design-based standard errors and 95% intervals on persisted metric rollups.

Three layers of evidence, all against the production SQL on real ClickHouse:

* hand-computed vectors (every arithmetic step is written out beside the test);
* design capture: a real snapshot freezes the sampling design and the stratum
  population size, and a persisted run exposes the interval in the SQL view;
* a deterministic-seed Monte Carlo: repeated draws from known synthetic
  populations, each estimated by the production rollup SQL, then compared with
  an independent NumPy reference (explicit centering, zero-padded domains) and
  with the known truth (coverage, bias, variance calibration).
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from scipy import stats

from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.queries import (
    QUERY_SETTINGS,
    SCOPE_SQL,
    Scope,
    aggregate_select,
    rollup_insert,
    snapshot_select,
)
from tracer.services.derived_metrics.uncertainty import (
    CI_REASONS,
    T_975,
    t_quantile_975,
)
from tracer.tests.test_derived_metric_clickhouse import (  # noqa: F401 -- fixture
    metric_ch,
    seed_eval,
    seed_span,
)

INPUT_COLUMNS = (
    "org_id, workspace_id, project_id, snapshot_id, unit_id, session_id, user_id, "
    "cohort, day, input_values, probability, invalid_probability, source_rows, "
    "sampling_scope, sampling_stratum, sampling_design, population_size"
)
CI_COLUMNS = (
    "group_key, value, numerator, denominator, status, valid_count, std_error, "
    "ci_low, ci_high, effective_n, ci_df, ci_method, ci_status, ci_reason"
)


# --- Independent reference -------------------------------------------------------


def reference(kind, design, y, p, x=None, stratum=None, population=None) -> dict:
    """The documented estimators, written with explicit centering.

    Arrays hold the VALID units of one group. Closed strata are zero-padded to
    their full sample size ``n_h = p_h * N_h`` (domain estimation), which the SQL
    never materializes; it uses expanded second moments instead.
    """
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    x = np.ones_like(y) if x is None else np.asarray(x, dtype=float)
    w = 1 / p
    ratio = kind in {"ratio", "mean"}
    total_y, total_x = float(np.sum(w * y)), float(np.sum(w * x))
    value = total_y / total_x if ratio else total_y
    r_hat, divisor = (value, total_x) if ratio else (0.0, 1.0)
    u = y - r_hat * x
    if design == "closed":
        stratum, population = np.asarray(stratum), np.asarray(population)
        variance, labels = 0.0, np.unique(stratum)
        for label in labels:
            members = np.flatnonzero(stratum == label)
            probability = float(p[members[0]])
            size = round(probability * float(population[members[0]]))
            if probability >= 1:
                continue
            padded = np.zeros(size)
            padded[: members.size] = w[members] * u[members]
            variance += (
                (1 - probability)
                * size
                / (size - 1)
                * float(np.sum((padded - padded.mean()) ** 2))
            )
        degrees = y.size - labels.size
    elif design == "live":
        variance = float(np.sum((1 - p) * (w * u) ** 2))
        degrees = y.size - 1
    else:
        variance = y.size / (y.size - 1) * float(np.sum((u - u.mean()) ** 2))
        degrees = y.size - 1
    error = math.sqrt(variance) / abs(divisor)
    multiplier = float(stats.t.ppf(0.975, degrees))
    rate = ratio and all(
        a in (0, 1) and b in (0, 1) and a <= b for a, b in zip(y, x, strict=True)
    )
    census = design in {"closed", "live"} and bool(np.all(p >= 1))
    if rate and not census:
        size = (
            value * (1 - value) / error**2
            if error > 0
            else float(np.sum(w * x) ** 2 / np.sum((w * x) ** 2))
        )
        k = multiplier**2 / size
        center = (value + k / 2) / (1 + k)
        half = (
            multiplier
            / (1 + k)
            * math.sqrt(value * (1 - value) / size + k / (4 * size))
        )
        low, high, interval = center - half, center + half, "wilson"
    else:
        low, high = value - multiplier * error, value + multiplier * error
        interval = "wald_t"
    return {
        "value": value,
        "std_error": error,
        "ci_low": low,
        "ci_high": high,
        "interval": interval,
        "effective_n": float(np.sum(w) ** 2 / np.sum(w**2)),
        "ci_df": degrees,
    }


# --- Helpers -----------------------------------------------------------------------


def _scope():
    return Scope(uuid4(), uuid4(), uuid4())


def insert_units(client, scope, snapshot, rows):
    """Insert frozen units directly: (unit, cohort, inputs, p, stratum, design, N, invalid)."""
    values = []
    for unit, cohort, inputs, probability, stratum, design, population, invalid in rows:
        values.append(
            (
                scope.organization,
                scope.workspace,
                scope.project,
                snapshot,
                unit,
                "",
                "",
                cohort,
                datetime.now(UTC).date(),
                inputs,
                probability,
                invalid,
                1,
                "scope" if design in {"closed", "live"} else "",
                stratum,
                design,
                population,
            )
        )
    client.execute(
        f"INSERT INTO derived_metric_inputs ({INPUT_COLUMNS}) VALUES", values
    )


def aggregate(client, scope, snapshot, formula_text, aliases, rollup="overall"):
    formula = compile_formula(formula_text, aliases)
    source = (
        f"SELECT * FROM derived_metric_inputs WHERE {SCOPE_SQL} "
        "AND snapshot_id = %(snapshot_id)s"
    )
    rows, columns = client.execute(
        aggregate_select(source, formula, rollup=rollup),
        {**scope.parameters(), **formula.parameters, "snapshot_id": str(snapshot)},
        with_column_types=True,
        settings=QUERY_SETTINGS,
    )
    return [dict(zip([c[0] for c in columns], row, strict=True)) for row in rows]


def one_group(client, rows, formula_text, aliases=("y", "x")):
    scope, snapshot = _scope(), uuid4()
    insert_units(client, scope, snapshot, rows)
    result = aggregate(client, scope, snapshot, formula_text, list(aliases))
    assert len(result) == 1
    return result[0]


def unit(y, p, stratum="h1", design="closed", population=None, x=1.0, invalid=0):
    return (
        str(uuid4()),
        "",
        {"y": y, "x": x},
        p,
        stratum,
        design,
        population,
        invalid,
    )


# --- Pure unit checks (no database) ---------------------------------------------------


def test_t_multipliers_match_the_student_distribution():
    for df in range(1, 31):
        assert t_quantile_975(df) == pytest.approx(stats.t.ppf(0.975, df), rel=1e-10)
        assert T_975[df - 1] == pytest.approx(stats.t.ppf(0.975, df), rel=1e-10)
    for df in [31, 40, 75, 120, 1000, 10_000, 1_000_000]:
        assert t_quantile_975(df) == pytest.approx(stats.t.ppf(0.975, df), rel=2e-8)
    with pytest.raises(ValueError):
        t_quantile_975(0)


def test_reference_reproduces_the_hand_computed_vectors():
    # Closed, two strata, mean of a 0/1 outcome (spelled out in the SQL test).
    result = reference(
        "mean",
        "closed",
        y=[1, 0, 1, 1, 0],
        p=[0.5, 0.5, 1, 1, 1],
        stratum=["a", "a", "b", "b", "b"],
        population=[4, 4, 3, 3, 3],
    )
    assert result["value"] == pytest.approx(4 / 7)
    assert result["std_error"] == pytest.approx(math.sqrt(2) / 7)
    assert result["ci_df"] == 3 and result["effective_n"] == pytest.approx(49 / 11)


# --- Hand-computed vectors through the production SQL --------------------------------


def test_uncertainty_expression_tree_does_not_expand_recursively(metric_ch):
    formula = compile_formula("mean(y)", ["y"])
    source = "SELECT * FROM derived_metric_inputs"
    query = aggregate_select(source, formula, rollup="overall")
    # EXPLAIN emits plan metadata, not result rows. Its output budget is the
    # structural bound below; the production query still has its 101-row cap.
    tree = metric_ch.execute(
        "EXPLAIN QUERY TREE " + query,
        formula.parameters,
        settings={**QUERY_SETTINGS, "max_result_rows": 10_000},
    )
    plan = metric_ch.execute(
        "EXPLAIN PLAN " + query, formula.parameters, settings=QUERY_SETTINGS
    )
    print("uncertainty-plan", {"query_tree_lines": len(tree), "plan_steps": len(plan)})
    # The interrupted SELECT-alias version expanded to 34,955 tree lines and
    # timed out while planning even an empty input table. Bound structure, not
    # wall time on a shared host. No production resource ceiling is increased.
    assert len(tree) < 10_000
    assert len(plan) < 100


def test_closed_stratified_ratio_matches_hand_computation(metric_ch):
    # Stratum a: N=4, p=0.5 -> n=2, w=2, outcomes 1 and 0.
    # Stratum b: N=3, p=1 -> a census, w=1, outcomes 1, 1, 0 (no variance).
    # Y = 2*1 + 2*0 + 1 + 1 + 0 = 4, X = 2 + 2 + 3 = 7, R = 4/7.
    # Stratum a: w*u = 2*(1 - 4/7) = 6/7 and 2*(0 - 4/7) = -8/7, mean -1/7,
    # centered 1 and -1, sum of squares 2, scale (1 - 0.5) * 2/(2 - 1) = 1.
    # V(R) = 2 / 7^2, SE = sqrt(2)/7. df = 5 units - 2 strata = 3.
    # Kish n_eff = (2 + 2 + 1 + 1 + 1)^2 / (4 + 4 + 1 + 1 + 1) = 49/11.
    # A 0/1 rate gets a Wilson interval at n* = p(1-p)/V = (12/49)/(2/49) = 6:
    # t = 3.18244630528, k = t^2/6 = 1.687994081, centre (4/7 + k/2)/(1 + k)
    # = 0.526573188, half-width t/(1 + k) * sqrt(p(1-p)/6 + k/24) = 0.394717484.
    rows = [
        unit(1, 0.5, "a", population=4),
        unit(0, 0.5, "a", population=4),
        *[unit(y, 1.0, "b", population=3) for y in (1, 1, 0)],
    ]
    row = one_group(metric_ch, rows, "mean(y)")
    se = math.sqrt(2) / 7
    assert row["status"] == "ok" and row["value"] == pytest.approx(4 / 7)
    assert row["ci_status"] == "ok" and row["ci_reason"] == ""
    assert row["ci_method"] == "stratified_wor_fpc" and row["ci_interval"] == "wilson"
    assert row["std_error"] == pytest.approx(se, rel=1e-12)
    assert int(row["ci_df"]) == 3
    assert row["ci_low"] == pytest.approx(0.526573188 - 0.394717484, rel=1e-8)
    assert row["ci_high"] == pytest.approx(0.526573188 + 0.394717484, rel=1e-8)
    assert row["effective_n"] == pytest.approx(49 / 11, rel=1e-12)


def test_large_offset_cancellation_never_fabricates_precision(metric_ch):
    rows = [unit(1e13 + y, 0.5, population=8) for y in (1, 2, 3, 4)]
    row = one_group(metric_ch, rows, "mean(y)")
    assert row["status"] == "ok"
    assert row["ci_status"] == "not_estimable"
    assert row["ci_reason"] == "unstable_variance"
    assert row["std_error"] is None and row["ci_low"] is None


def test_more_units_than_the_design_sample_is_inconsistent(metric_ch):
    rows = [unit(y, 0.5, population=4) for y in (0, 1, 0)]
    row = one_group(metric_ch, rows, "mean(y)")
    assert row["ci_reason"] == "inconsistent_stratum_design"
    assert row["ci_low"] is None


def test_uncertainty_upgrade_is_idempotent_and_refreshes_the_view(metric_ch):
    from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements
    from tracer.tests.test_derived_metric_clickhouse import SCHEMA

    # Recreate the original view projection, as it existed before the upgrade.
    metric_ch.execute("DROP VIEW derived_metrics")
    metric_ch.execute(
        "CREATE VIEW derived_metrics AS SELECT value FROM derived_metric_results"
    )
    for _ in range(2):
        for statement in split_statements(
            (SCHEMA / "029_derived_metric_uncertainty.sql").read_text()
        ):
            metric_ch.execute(statement)
    names = {row[0] for row in metric_ch.execute("DESCRIBE TABLE derived_metrics")}
    assert {"std_error", "ci_low", "ci_high", "effective_n", "ci_status"} <= names
    assert metric_ch.execute("SELECT count() FROM derived_metrics") == [(0,)]


def test_a_census_of_certainty_units_has_no_sampling_error(metric_ch):
    # Every unit was kept with p = 1: the value is exact and so is the interval.
    rows = [unit(y, 1.0, "a", population=3) for y in (1, 1, 0)]
    row = one_group(metric_ch, rows, "mean(y)")
    assert row["ci_status"] == "ok" and row["ci_interval"] == "wald_t"
    assert row["std_error"] == 0 and row["ci_low"] == row["ci_high"] == pytest.approx(
        2 / 3
    )


def test_a_unanimous_sample_still_has_a_one_sided_interval(metric_ch):
    # Four sampled units of N=8 all pass: V = 0, but this is not a census, so the
    # Wilson interval uses the Kish size of the denominator units (4) and stays
    # below 1 instead of collapsing to the point.
    rows = [unit(1, 0.5, "a", population=8) for _ in range(4)]
    row = one_group(metric_ch, rows, "mean(y)")
    t = 3.18244630528  # df = 4 units - 1 stratum
    k = t * t / 4
    assert row["value"] == 1 and row["std_error"] == 0
    assert row["ci_interval"] == "wilson" and row["ci_high"] == pytest.approx(1)
    assert row["ci_low"] == pytest.approx(
        (1 + k / 2) / (1 + k) - t / (1 + k) * math.sqrt(k / 16)
    )
    assert row["ci_low"] < 1


def test_closed_domain_pads_unsampled_members_with_zero(metric_ch):
    # One stratum, N=10, p=0.4 -> n=4 sampled units, w=2.5. Only two of them are
    # in this window (y=1 and y=3), so the other two contribute u=0.
    # Total = 2.5 * (1 + 3) = 10. w*y padded: [2.5, 7.5, 0, 0], mean 2.5,
    # squared deviations 0 + 25 + 6.25 + 6.25 = 37.5, scale (1 - 0.4) * 4/3 = 0.8,
    # V = 30, SE = sqrt(30). df = 2 units - 1 stratum = 1, t = 12.7062047362.
    rows = [unit(1, 0.4, population=10), unit(3, 0.4, population=10)]
    row = one_group(metric_ch, rows, "sum(y)")
    assert row["value"] == pytest.approx(10) and row["denominator"] is None
    assert row["std_error"] == pytest.approx(math.sqrt(30), rel=1e-12)
    assert int(row["ci_df"]) == 1
    assert row["ci_high"] == pytest.approx(
        10 + 12.7062047362 * math.sqrt(30), rel=1e-10
    )


def test_live_policy_uses_the_poisson_design(metric_ch):
    # Independent Bernoulli keeps with p = 0.5, 0.25 and 1 (certainty), y = 1 each.
    # Total = 2 + 4 + 1 = 7. V = (1-0.5)*2^2 + (1-0.25)*4^2 + 0 = 2 + 12 = 14.
    # df = 3 - 1 = 2, t = 4.30265272975.
    rows = [unit(1, p, "live", design="live") for p in (0.5, 0.25, 1.0)]
    row = one_group(metric_ch, rows, "count_if(y == 1)")
    assert row["value"] == pytest.approx(7) and row["ci_method"] == "poisson"
    assert row["std_error"] == pytest.approx(math.sqrt(14), rel=1e-12)
    assert row["ci_low"] == pytest.approx(7 - 4.30265272975 * math.sqrt(14), rel=1e-10)


def test_unweighted_mean_is_a_labelled_model_based_interval(metric_ch):
    # y = 1, 2, 3, 6: mean 3, squared deviations 4 + 1 + 0 + 9 = 14,
    # V = 14 / (4 * 3) = 7/6, df = 3.
    rows = [unit(y, 1.0, "", design="unweighted") for y in (1, 2, 3, 6)]
    row = one_group(metric_ch, rows, "mean(y)")
    assert row["value"] == pytest.approx(3) and row["ci_method"] == "iid_units"
    assert row["std_error"] == pytest.approx(math.sqrt(7 / 6), rel=1e-12)
    assert int(row["ci_df"]) == 3 and row["effective_n"] == pytest.approx(4)


@pytest.mark.parametrize(
    "case,reason",
    [
        ("single_unit_stratum", "single_unit_stratum"),
        ("missing_population", "population_size_missing"),
        ("inconsistent_probability", "inconsistent_stratum_design"),
        ("fractional_sample_size", "inconsistent_stratum_design"),
        ("legacy_snapshot", "design_not_captured"),
        ("mixed_designs", "mixed_design"),
        ("unweighted_total", "unweighted_total"),
        ("one_unit", "fewer_than_two_units"),
        ("zero_denominator", "no_value"),
        ("invalid_probability", "no_value"),
    ],
)
def test_insufficient_design_information_is_never_an_interval(metric_ch, case, reason):
    formula = "mean(y)"
    rows = {
        # N=5, p=0.2 -> one sampled unit: no within-stratum variance exists.
        "single_unit_stratum": [
            unit(1, 0.2, "a", population=5),
            unit(0, 0.5, "b", population=4),
        ],
        "missing_population": [unit(1, 0.5), unit(0, 0.5)],
        "inconsistent_probability": [
            unit(1, 0.5, population=8),
            unit(0, 0.25, population=8),
        ],
        # 0.33 * 10 = 3.3 sampled units is not a fixed-size draw.
        "fractional_sample_size": [
            unit(1, 0.33, population=10),
            unit(0, 0.33, population=10),
        ],
        "legacy_snapshot": [unit(1, 0.5, design=""), unit(0, 0.5, design="")],
        "mixed_designs": [unit(1, 0.5, population=4), unit(0, 0.5, design="live")],
        "unweighted_total": [unit(y, 1.0, "", design="unweighted") for y in (1, 2)],
        "one_unit": [unit(1, 0.5, population=4)],
        "zero_denominator": [
            unit(1, 0.5, population=4, x=0),
            unit(0, 0.5, population=4, x=0),
        ],
        "invalid_probability": [
            unit(1, 0.5, population=4),
            unit(0, 0.5, population=4, invalid=1),
        ],
    }[case]
    if case == "unweighted_total":
        formula = "sum(y)"
    if case == "zero_denominator":
        formula = "ratio(y, x)"
    row = one_group(metric_ch, rows, formula)
    assert row["ci_status"] == "not_estimable" and row["ci_reason"] == reason
    assert reason in CI_REASONS
    assert row["std_error"] is None and row["ci_low"] is None and row["ci_high"] is None
    assert row["ci_df"] is None
    if case not in {"zero_denominator", "invalid_probability"}:
        # The point estimate is unaffected: only the interval is withheld.
        assert row["status"] == "ok" and row["value"] is not None


def test_rollup_groups_are_domains_of_one_design(metric_ch):
    # One closed stratum (N=8, p=0.5 -> n=4) split across two cohorts. Each cohort
    # is a domain: its own two units, the other two padded with zero.
    scope, snapshot = _scope(), uuid4()
    rows = []
    for cohort, outcomes in (("east", (1, 0)), ("west", (1, 1))):
        for y in outcomes:
            identity, _, inputs, p, stratum, design, population, invalid = unit(
                y, 0.5, population=8
            )
            rows.append(
                (identity, cohort, inputs, p, stratum, design, population, invalid)
            )
    insert_units(metric_ch, scope, snapshot, rows)
    grouped = {
        row["group_key"]: row
        for row in aggregate(metric_ch, scope, snapshot, "sum(y)", ["y", "x"], "cohort")
    }
    for cohort, outcomes in (("east", (1, 0)), ("west", (1, 1))):
        expected = reference(
            "sum",
            "closed",
            y=outcomes,
            p=[0.5, 0.5],
            stratum=["h1", "h1"],
            population=[8, 8],
        )
        assert grouped[cohort]["std_error"] == pytest.approx(
            expected["std_error"], rel=1e-12
        )
        assert grouped[cohort]["value"] == pytest.approx(expected["value"])


# --- Design capture through a real snapshot, persisted rollups and the view ---------


def _capture(metric_ch, *, mode, population, weighting="inverse_probability"):
    from tracer.tests.test_derived_metric_clickhouse import recipe

    scope = _scope()
    now = datetime.now(UTC).replace(microsecond=0)
    end = now - timedelta(minutes=30)
    start = end - timedelta(days=1)
    configs = [uuid4(), uuid4()]
    selection = {"membership_mode": mode, "population_size": population}
    if mode == "closed":
        selection.update(
            cohort_frozen_at=(end + timedelta(minutes=1)).isoformat(),
            window={"start": start.isoformat(), "end": end.isoformat()},
        )
    for index, (resolved, escalated) in enumerate([(1, 0), (1, 1), (1, 0)]):
        session = uuid4()
        seed_span(
            metric_ch,
            scope,
            f"unit-{index}",
            session,
            uuid4(),
            end - timedelta(hours=1),
        )
        for config, score in zip(configs, (resolved, escalated), strict=True):
            seed_eval(
                metric_ch,
                config,
                session,
                end + timedelta(minutes=2),
                score,
                0.5,
                selection_overrides=selection,
            )
    definition = recipe(configs, weighting)
    definition.cohort_key = ""
    definition.metric = SimpleNamespace(
        id=uuid4(),
        organization_id=scope.organization,
        workspace_id=scope.workspace,
        project_id=scope.project,
    )
    definition.version, definition.definition_hash = 1, "d" * 64
    run = SimpleNamespace(
        id=uuid4(),
        snapshot_id=uuid4(),
        window_start=start,
        window_end=end,
        as_of=now,
        source_run_id="",
        source_manifest={"sampling_scope": ""},
    )
    query, params = snapshot_select(definition, scope, start, end, now, [], "")
    params["snapshot_id"] = str(run.snapshot_id)
    metric_ch.execute(
        "INSERT INTO derived_metric_inputs SELECT %(org_id)s, %(workspace_id)s, "
        f"%(project_id)s, %(snapshot_id)s, frozen.* FROM ({query}) frozen",
        params,
        settings=QUERY_SETTINGS,
    )
    return scope, definition, run


@pytest.mark.parametrize(
    "mode,population,weighting,design,expected_population",
    [
        ("closed", 6, "inverse_probability", "closed", 6),
        ("live", None, "inverse_probability", "live", None),
        ("closed", 6, "none", "unweighted", None),
    ],
)
def test_snapshot_freezes_the_sampling_design(
    metric_ch, mode, population, weighting, design, expected_population
):
    scope, _, run = _capture(
        metric_ch, mode=mode, population=population, weighting=weighting
    )
    frozen = metric_ch.execute(
        "SELECT sampling_design, population_size FROM derived_metric_inputs "
        "WHERE snapshot_id = %(snapshot)s",
        {"snapshot": str(run.snapshot_id)},
    )
    assert len(frozen) == 3
    assert {(row[0], row[1]) for row in frozen} == {(design, expected_population)}


def test_persisted_rollup_and_sql_view_carry_the_interval(metric_ch):
    # Closed cohort: 3 sampled sessions of N=6 (p=0.5), escalation-free outcomes
    # 1, 0, 1 -> R = 2/3. Every unit is in one stratum and in the window.
    scope, definition, run = _capture(metric_ch, mode="closed", population=6)
    result_id = uuid4()
    for rollup in ("overall", "cohort"):
        query, params = rollup_insert(definition, run, result_id, rollup)
        metric_ch.execute(query, params, settings=QUERY_SETTINGS)
    metric_ch.execute(
        "INSERT INTO derived_metric_commits VALUES (%(org)s, %(ws)s, %(project)s, "
        "%(metric)s, %(run)s, %(result)s, now64(6), 1)",
        {
            "org": str(scope.organization),
            "ws": str(scope.workspace),
            "project": str(scope.project),
            "metric": str(definition.metric.id),
            "run": str(run.id),
            "result": str(result_id),
        },
    )
    rows, columns = metric_ch.execute(
        f"SELECT rollup, {CI_COLUMNS} FROM derived_metrics WHERE result_id = %(result)s "
        "ORDER BY rollup",
        {"result": str(result_id)},
        with_column_types=True,
    )
    by_rollup = {
        row[0]: dict(zip([c[0] for c in columns], row, strict=True)) for row in rows
    }
    expected = reference(
        "ratio",
        "closed",
        y=[1, 0, 1],
        p=[0.5] * 3,
        stratum=["s"] * 3,
        population=[6] * 3,
    )
    overall = by_rollup["overall"]
    assert overall["value"] == pytest.approx(2 / 3)
    assert overall["ci_status"] == "ok" and overall["ci_method"] == "stratified_wor_fpc"
    assert overall["std_error"] == pytest.approx(expected["std_error"], rel=1e-12)
    assert overall["ci_low"] == pytest.approx(expected["ci_low"], rel=1e-9)
    assert overall["ci_high"] == pytest.approx(expected["ci_high"], rel=1e-9)
    assert int(overall["ci_df"]) == 2 and overall["effective_n"] == pytest.approx(3)
    # The cohort rollup is the sampling stratum itself, so it matches overall.
    assert by_rollup["cohort"]["std_error"] == pytest.approx(
        overall["std_error"], rel=1e-12
    )


# --- Monte Carlo coverage ----------------------------------------------------------

REPLICATES = 2000
SEED = 20261023
DESIGN_CODES = {"closed": 0, "live": 1, "unweighted": 2}


class Batch:
    """Every replicate's valid units as numeric columns, plus its reference answer."""

    def __init__(self, kind, design):
        self.kind, self.design = kind, design
        self.parts = {
            name: [] for name in ("rep", "stratum", "y", "x", "p", "population")
        }
        self.references = []

    def add(self, rep, y, p, *, x=None, stratum=None, population=None):
        size = len(y)
        x = np.ones(size) if x is None else x
        stratum = np.zeros(size, dtype=np.uint8) if stratum is None else stratum
        sizes = np.zeros(size, dtype=np.uint64) if population is None else population
        for name, values in (
            ("rep", np.full(size, rep)),
            ("stratum", stratum),
            ("y", y),
            ("x", x),
            ("p", p),
            ("population", sizes),
        ):
            self.parts[name].append(np.asarray(values))
        self.references.append(
            reference(
                self.kind,
                self.design,
                y,
                p,
                x=x,
                stratum=stratum,
                population=population,
            )
        )

    def columns(self):
        dtypes = {"rep": np.uint32, "stratum": np.uint8, "population": np.uint64}
        columns = [
            np.concatenate(self.parts[name]).astype(dtypes.get(name, np.float64))
            for name in ("rep", "stratum", "y", "x", "p", "population")
        ]
        return columns + [np.full(columns[0].size, DESIGN_CODES[self.design], np.uint8)]


def closed_batch(rng, kind, sizes, samples, passes):
    """Stratified simple random samples without replacement from a fixed population."""
    population = [
        np.r_[np.ones(k), np.zeros(n - k)] for n, k in zip(sizes, passes, strict=True)
    ]
    batch = Batch(kind, "closed")
    probability = [n / size for n, size in zip(samples, sizes, strict=True)]
    for rep in range(REPLICATES):
        picks = [
            values[rng.choice(values.size, n, replace=False)]
            for values, n in zip(population, samples, strict=True)
        ]
        batch.add(
            rep,
            np.concatenate(picks),
            np.repeat(probability, samples),
            stratum=np.repeat(np.arange(len(sizes)), samples).astype(np.uint8),
            population=np.repeat(sizes, samples).astype(np.uint64),
        )
    return batch


def stage_and_rollup(client, scope, formula, batch):
    """Persist one snapshot holding every replicate and roll it up per replicate.

    The replicate is the cohort, so the production cohort rollup INSERT writes one
    row per replicate; the rows are read back from the result table.
    """
    snapshot = uuid4()
    client.execute(
        "CREATE TABLE mc_stage (rep UInt32, stratum UInt8, y Float64, x Float64, "
        "p Float64, population UInt64, design UInt8) ENGINE = Memory"
    )
    client.execute(
        "INSERT INTO mc_stage (rep, stratum, y, x, p, population, design) VALUES",
        batch.columns(),
        columnar=True,
        # Keep seed blocks small; expanding one 900k-row Memory block to maps
        # and strings otherwise dominates the shared server's memory budget.
        settings={"use_numpy": True, "insert_block_size": 10_000},
    )
    client.execute(
        f"INSERT INTO derived_metric_inputs ({INPUT_COLUMNS}) "
        "SELECT %(org)s, %(ws)s, %(project)s, %(snapshot)s, "
        "concat('rep-', leftPad(toString(rep), 5, '0'), '-', toString(rowNumberInAllBlocks())), "
        "'', '', concat('rep-', leftPad(toString(rep), 5, '0')), today(), "
        "map('y', y, 'x', x), p, 0, 1, if(design = 2, '', 'mc-scope'), "
        "concat('h', toString(stratum)), ['closed', 'live', 'unweighted'][design + 1], "
        "if(population = 0, NULL, population) FROM mc_stage",
        {
            "org": str(scope.organization),
            "ws": str(scope.workspace),
            "project": str(scope.project),
            "snapshot": str(snapshot),
        },
        settings={
            "max_threads": 1,
            "max_insert_threads": 1,
            "min_insert_block_size_rows": 10_000,
            "min_insert_block_size_bytes": 0,
        },
    )
    client.execute("DROP TABLE mc_stage")
    definition = SimpleNamespace(
        formula=formula,
        inputs=[{"alias": "y"}, {"alias": "x"}],
        metric=SimpleNamespace(
            id=uuid4(),
            organization_id=scope.organization,
            workspace_id=scope.workspace,
            project_id=scope.project,
        ),
        version=1,
        definition_hash="m" * 64,
    )
    now = datetime.now(UTC)
    run = SimpleNamespace(
        id=uuid4(),
        snapshot_id=snapshot,
        window_start=now - timedelta(days=1),
        window_end=now,
        as_of=now,
        source_run_id="",
        source_manifest={"sampling_scope": ""},
    )
    result_id = uuid4()
    query, params = rollup_insert(definition, run, result_id, "cohort")
    client.execute(query, params, settings={**QUERY_SETTINGS, "max_threads": 1})
    rows = client.execute(
        "SELECT group_key, value, std_error, ci_low, ci_high, ci_status, ci_method, "
        "valid_count, ci_df, ci_interval FROM derived_metric_results "
        "WHERE result_id = %(result)s "
        "ORDER BY group_key",
        {"result": str(result_id)},
    )
    assert len(rows) == REPLICATES
    return rows


def coverage_report(rows, truth, references):
    assert {row[5] for row in rows} == {"ok"}
    estimates = np.array([row[1] for row in rows])
    errors = np.array([row[2] for row in rows])
    low, high = np.array([row[3] for row in rows]), np.array([row[4] for row in rows])
    for row, expected in zip(rows, references, strict=True):
        assert row[1] == pytest.approx(expected["value"], rel=1e-9)
        assert row[2] == pytest.approx(expected["std_error"], rel=1e-9)
        assert row[3] == pytest.approx(expected["ci_low"], rel=1e-9, abs=1e-12)
        assert row[4] == pytest.approx(expected["ci_high"], rel=1e-9, abs=1e-12)
        assert int(row[8]) == expected["ci_df"] and row[9] == expected["interval"]
    # The symmetric value +/- t * SE interval on the same draws, for comparison.
    multipliers = np.array([stats.t.ppf(0.975, int(row[8])) for row in rows])
    symmetric = np.abs(estimates - truth) <= multipliers * errors
    return {
        "symmetric_coverage": float(np.mean(symmetric)),
        "coverage": float(np.mean((low <= truth) & (truth <= high))),
        "bias": float(estimates.mean() - truth),
        "bias_mcse": float(estimates.std(ddof=1) / math.sqrt(len(rows))),
        # Mean estimated variance over the empirical variance of the estimates.
        "variance_ratio": float(np.mean(errors**2) / estimates.var(ddof=1)),
        "mean_valid_units": float(np.mean([int(row[7]) for row in rows])),
    }


def assert_calibrated(report):
    print("monte-carlo", report)
    assert 0.92 <= report["coverage"] <= 0.98, report
    assert abs(report["bias"]) <= 4 * report["bias_mcse"], report
    assert 0.85 <= report["variance_ratio"] <= 1.15, report


@pytest.fixture
def native_ch(metric_ch):
    if not hasattr(metric_ch, "connection"):
        pytest.fail(
            "The Monte Carlo coverage proof requires the native ClickHouse test sidecar."
        )
    return metric_ch


def test_closed_stratified_rate_interval_coverage_and_bias(native_ch):
    """Fixed 3-stratum design: 90/9/1% of N=10,000, p = 0.02/0.2/1, pass rates
    0.95/0.75/0.40, so the true rate is exactly 0.9265 and n = 460 per draw."""
    started = time.monotonic()
    rng = np.random.default_rng(SEED)
    batch = closed_batch(
        rng, "mean", (9000, 900, 100), (180, 180, 100), (8550, 675, 40)
    )
    truth = 9265 / 10000
    unweighted = [float(np.mean(part)) for part in batch.parts["y"]]
    rows = stage_and_rollup(native_ch, _scope(), "mean(y)", batch)
    report = coverage_report(rows, truth, batch.references)
    report["unweighted_bias"] = float(np.mean(unweighted) - truth)
    report["seconds"] = round(time.monotonic() - started, 1)
    assert_calibrated(report)
    assert rows[0][6] == "stratified_wor_fpc" and report["mean_valid_units"] == 460
    assert rows[0][9] == "wilson"
    # Weighting removes the selection bias that the unweighted mean of the same
    # draws carries (346/460 = 0.7522 expected on this design, 0.1743 below truth).
    assert abs(report["bias"]) <= 0.002
    assert abs(report["unweighted_bias"]) >= 10 * abs(report["bias"])


def test_live_poisson_ratio_interval_coverage_and_bias(native_ch):
    """Live policy: independent keeps with p = 0.05/0.2/0.5 over a fixed population
    of 3,000 units; a ratio of two continuous measures (cost per token)."""
    started = time.monotonic()
    rng = np.random.default_rng(SEED + 1)
    groups = np.repeat([0, 1, 2], [1800, 900, 300]).astype(np.uint8)
    probability = np.array([0.05, 0.2, 0.5])[groups]
    tokens = rng.lognormal(6.0, 0.8, groups.size)
    cost = (
        0.002
        * tokens
        * np.array([1.0, 1.5, 3.0])[groups]
        * rng.gamma(4.0, 0.25, groups.size)
    )
    truth = float(cost.sum() / tokens.sum())
    batch = Batch("ratio", "live")
    for rep in range(REPLICATES):
        kept = np.flatnonzero(rng.random(groups.size) < probability)
        batch.add(
            rep, cost[kept], probability[kept], x=tokens[kept], stratum=groups[kept]
        )
    rows = stage_and_rollup(native_ch, _scope(), "ratio(y, x)", batch)
    report = coverage_report(rows, truth, batch.references)
    report["seconds"] = round(time.monotonic() - started, 1)
    assert_calibrated(report)
    assert rows[0][6] == "poisson"


def test_closed_small_sample_total_interval_coverage(native_ch):
    """A count (Horvitz-Thompson total) from n = 60 + 60 sampled units: N = 600/200,
    p = 0.1/0.3, outcome rates 0.3/0.6, so the true total is exactly 300."""
    started = time.monotonic()
    rng = np.random.default_rng(SEED + 2)
    batch = closed_batch(rng, "count_if", (600, 200), (60, 60), (180, 120))
    rows = stage_and_rollup(native_ch, _scope(), "count_if(y == 1)", batch)
    report = coverage_report(rows, 300.0, batch.references)
    report["seconds"] = round(time.monotonic() - started, 1)
    assert_calibrated(report)
    assert report["mean_valid_units"] == 120


def test_unweighted_mean_interval_coverage_of_the_process_mean(native_ch):
    """60 independent skewed (exponential) unit values per window; the model-based
    interval targets the process mean 1.0."""
    started = time.monotonic()
    rng = np.random.default_rng(SEED + 3)
    batch = Batch("mean", "unweighted")
    for rep in range(REPLICATES):
        batch.add(rep, rng.exponential(1.0, 60), np.ones(60))
    rows = stage_and_rollup(native_ch, _scope(), "mean(y)", batch)
    report = coverage_report(rows, 1.0, batch.references)
    report["seconds"] = round(time.monotonic() - started, 1)
    assert_calibrated(report)
    assert rows[0][6] == "iid_units"


def test_extreme_rate_interval_stays_calibrated_near_the_boundary(native_ch):
    """A 0.98 pass rate from n = 100 + 50 sampled units (N = 2000/500, p = 0.05/0.1).
    A symmetric interval undercovers here; the Wilson interval must not."""
    started = time.monotonic()
    rng = np.random.default_rng(SEED + 4)
    batch = closed_batch(rng, "mean", (2000, 500), (100, 50), (1960, 490))
    rows = stage_and_rollup(native_ch, _scope(), "mean(y)", batch)
    report = coverage_report(rows, 0.98, batch.references)
    report["seconds"] = round(time.monotonic() - started, 1)
    assert_calibrated(report)
    assert {row[9] for row in rows} == {"wilson"}
