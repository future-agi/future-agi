"""Per-scenario reliability across trials, and a pass-rate range that respects them."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
from django.db.models import QuerySet

from simulate.services.run_results_v3 import OUTCOME_LABELS

# Fewer scenarios than this give a range too wide to act on; show counts only.
MIN_INTERVAL_SCENARIOS = 10
_RESAMPLES = 2000
# A fixed seed keeps a run's range stable across page loads.
_SEED = 0
# Bounds the (resamples x scenarios) index matrix drawn at once.
_MAX_DRAW_CELLS = 1_000_000


def scenario_bootstrap_interval(
    clusters: list[tuple[int, int]],
) -> dict[str, Any] | None:
    """95% range for pass^1 by resampling scenarios with their trials nested inside.

    Trials of one scenario share its script, persona and world, so they are not
    independent draws: the bootstrap resamples whole scenarios, never single
    trials. ``clusters`` holds (passed, evaluated) per scenario; scenarios with no
    conclusive trial are left out.
    """
    clusters = [(passed, evaluated) for passed, evaluated in clusters if evaluated]
    scenarios = len(clusters)
    if scenarios < MIN_INTERVAL_SCENARIOS:
        return None
    rates = np.array([passed / evaluated for passed, evaluated in clusters])
    rng = np.random.default_rng(_SEED)
    chunk = max(1, _MAX_DRAW_CELLS // scenarios)
    means = np.concatenate(
        [
            rates[rng.integers(0, scenarios, size=(min(chunk, left), scenarios))].mean(
                axis=1
            )
            for left in range(_RESAMPLES, 0, -chunk)
        ]
    )
    low, high = np.percentile(means, [2.5, 97.5]) * 100
    return {
        "low": round(float(low), 2),
        "high": round(float(high), 2),
        "scenarios": scenarios,
        "evaluated": sum(evaluated for _, evaluated in clusters),
        "resamples": _RESAMPLES,
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
            round(sum(row["passed"] == k for row in complete) * 100 / len(complete), 2)
            if complete
            else None
        ),
        "pass_hat_k_scenarios": len(complete),
        "pass_rate_interval": scenario_bootstrap_interval(
            [(row["passed"], row["evaluated"]) for row in rows]
        ),
        "rows": rows,
    }
