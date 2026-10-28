"""Per-scenario reliability across trials, and a pass-rate range that respects them."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from django.db.models import QuerySet

from simulate.services.run_results_v3 import OUTCOME_LABELS

# Fewer scenarios than this give a range too wide to act on; show counts only.
MIN_INTERVAL_SCENARIOS = 10
_Z = 1.96


def _wilson(p: float, n: float) -> tuple[float, float]:
    denominator = 1 + _Z * _Z / n
    centre = (p + _Z * _Z / (2 * n)) / denominator
    half = _Z * math.sqrt(p * (1 - p) / n + _Z * _Z / (4 * n * n)) / denominator
    return max(0.0, centre - half) * 100, min(1.0, centre + half) * 100


def scenario_interval(clusters: list[tuple[int, int]]) -> dict[str, Any] | None:
    """95% range for pass^1 with scenarios, not trials, as the sampling unit.

    Trials of one scenario share its script, persona and world, so they are not
    independent draws. The spread of per-scenario rates (the variance a resample
    of whole scenarios would show) sets the effective sample size, and a Wilson
    interval on that keeps its width at 0% and 100%, where a percentile bootstrap
    collapses to a point. ``clusters`` holds (passed, evaluated) per scenario;
    scenarios with no conclusive trial are left out.
    """
    clusters = [(passed, evaluated) for passed, evaluated in clusters if evaluated]
    scenarios = len(clusters)
    if scenarios < MIN_INTERVAL_SCENARIOS:
        return None
    evaluated = sum(n for _, n in clusters)
    rates = [passed / n for passed, n in clusters]
    p = sum(rates) / scenarios
    variance = sum((rate - p) ** 2 for rate in rates) / (scenarios - 1) / scenarios
    # Identical scenario rates carry no spread to measure, so fall back to one
    # independent verdict per scenario. The tolerance absorbs float residue
    # (e.g. ten 2/3 rates), which would otherwise read as near-infinite n.
    effective_n = float(scenarios)
    if variance > 1e-12 and 0 < p < 1:
        effective_n = min(float(evaluated), max(1.0, p * (1 - p) / variance))
    low, high = _wilson(p, effective_n)
    return {
        "low": round(low, 2),
        "high": round(high, 2),
        "effective_n": round(effective_n, 2),
        "scenarios": scenarios,
        "evaluated": evaluated,
    }


def build_reliability(queryset: QuerySet, trials: int) -> dict[str, Any]:
    """Group trial verdicts by the scenario they repeated."""
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: dict.fromkeys(OUTCOME_LABELS, 0)
    )
    labels: dict[str, str] = {}
    call_ids: dict[str, list[str]] = defaultdict(list)
    for call_id, scenario_key, scenario_label, outcome in (
        queryset.order_by()
        .values_list("id", "result_scenario_key", "result_scenario", "result_outcome")
        .iterator(chunk_size=2000)
    ):
        key = str(scenario_key)
        labels[key] = str(scenario_label or key)
        counts[key][outcome] += 1
        call_ids[key].append(str(call_id))

    rows = []
    for scenario_key, outcome_counts in counts.items():
        passed, failed = outcome_counts["passed"], outcome_counts["failed"]
        ran = sum(outcome_counts.values())
        evaluated = passed + failed
        if not evaluated:
            verdict = "not_evaluated"
        elif passed and failed:
            verdict = "flaky"
        else:
            verdict = "passed" if passed else "failed"
        rows.append(
            {
                "scenario": labels[scenario_key],
                "scenario_key": scenario_key,
                "runs": ran,
                **outcome_counts,
                "evaluated": evaluated,
                "pass_rate": round(passed * 100 / evaluated, 2) if evaluated else None,
                "verdict": verdict,
                "call_ids": call_ids[scenario_key],
            }
        )
    order = {"flaky": 0, "failed": 1, "not_evaluated": 2, "passed": 3}
    rows.sort(key=lambda row: (order[row["verdict"]], row["scenario"]))
    repeated = [row for row in rows if row["evaluated"] >= 2]
    flaky = sum(1 for row in repeated if row["verdict"] == "flaky")
    conclusive = [row for row in rows if row["evaluated"]]
    # pass^k needs one k shared by every scenario; at k = 1 it is pass^1 again.
    run_counts = {row["runs"] for row in rows}
    k = run_counts.pop() if len(run_counts) == 1 else None
    pass_hat_k_shown = bool(k and k > 1)
    # A scenario with an inconclusive trial cannot show k of k passing.
    complete = [row for row in rows if pass_hat_k_shown and row["evaluated"] == k]
    all_passed = sum(row["passed"] == k for row in complete)
    return {
        "trials": trials,
        "scenarios": len(rows),
        # Strict reliability: every trial of the scenario ran and passed.
        "consistent_pass": sum(
            1 for row in rows if row["runs"] and row["passed"] == row["runs"]
        ),
        "passed_at_least_once": sum(1 for row in rows if row["passed"]),
        "repeated": len(repeated),
        "flaky": flaky,
        "flip_rate": round(flaky * 100 / len(repeated), 2) if repeated else None,
        "k": k,
        # Infra, simulator and evidence failures say nothing about the agent, so
        # they sit outside every denominator below.
        "inconclusive": sum(row["error"] + row["inconclusive"] for row in rows),
        "pass_hat_1": (
            round(
                sum(row["passed"] / row["evaluated"] for row in conclusive)
                * 100
                / len(conclusive),
                2,
            )
            if conclusive
            else None
        ),
        "pass_hat_k": (
            round(all_passed * 100 / len(complete), 2) if complete else None
        ),
        "pass_hat_k_scenarios": len(complete),
        "pass_hat_k_passed": all_passed,
        # Scenarios pass^k could not judge, so the header can say what it covers.
        "pass_hat_k_excluded": len(rows) - len(complete) if pass_hat_k_shown else 0,
        # Pooled counts for runs too small for rates, so the header need not sum rows.
        "trials_passed": sum(row["passed"] for row in conclusive),
        "trials_evaluated": sum(row["evaluated"] for row in conclusive),
        "pass_rate_interval": scenario_interval(
            [(row["passed"], row["evaluated"]) for row in rows]
        ),
        "rows": rows,
    }
