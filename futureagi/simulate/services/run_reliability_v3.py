"""Per-scenario reliability across trials, and a pass-rate range that respects them."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from django.db.models import QuerySet

_Z = 1.96


def _wilson(passed: float, n: float) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = passed / n
    denominator = 1 + _Z * _Z / n
    centre = (p + _Z * _Z / (2 * n)) / denominator
    half = _Z * math.sqrt(p * (1 - p) / n + _Z * _Z / (4 * n * n)) / denominator
    return max(0.0, centre - half) * 100, min(1.0, centre + half) * 100


def scenario_clustered_interval(
    clusters: list[tuple[int, int]],
) -> dict[str, Any] | None:
    """95% range for passed/evaluated when verdicts come in correlated clusters.

    Trials of one scenario share its script, persona and world, so they are not
    independent draws. The cluster-robust variance of the ratio estimate gives the
    effective number of independent verdicts, which then feeds a Wilson interval.
    ``clusters`` holds (passed, evaluated) per scenario.
    """
    clusters = [(passed, evaluated) for passed, evaluated in clusters if evaluated]
    evaluated = sum(n for _, n in clusters)
    if not evaluated:
        return None
    passed = sum(k for k, _ in clusters)
    p = passed / evaluated
    effective_n = float(min(evaluated, len(clusters)))
    if len(clusters) > 1 and 0 < p < 1:
        m = len(clusters)
        variance = (
            m
            / (m - 1)
            * sum((k - p * n) ** 2 for k, n in clusters)
            / (evaluated * evaluated)
        )
        if variance > 0:
            effective_n = min(float(evaluated), max(1.0, p * (1 - p) / variance))
    low, high = _wilson(p * effective_n, effective_n)
    return {
        "low": round(low, 2),
        "high": round(high, 2),
        "effective_n": round(effective_n, 2),
        "evaluated": evaluated,
        "clusters": len(clusters),
    }


def build_reliability(queryset: QuerySet, trials: int) -> dict[str, Any]:
    """Group trial verdicts by the scenario they repeated."""
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"passed": 0, "failed": 0, "error": 0, "inconclusive": 0}
    )
    labels: dict[str, str] = {}
    for scenario_key, scenario_label, outcome in (
        queryset.order_by()
        .values_list("result_scenario_key", "result_scenario", "result_outcome")
        .iterator(chunk_size=2000)
    ):
        key = str(scenario_key)
        labels[key] = str(scenario_label or key)
        counts[key][outcome] += 1

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
            }
        )
    order = {"flaky": 0, "failed": 1, "not_evaluated": 2, "passed": 3}
    rows.sort(key=lambda row: (order[row["verdict"]], row["scenario"]))
    repeated = [row for row in rows if row["evaluated"] >= 2]
    flaky = sum(1 for row in repeated if row["verdict"] == "flaky")
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
        "pass_rate_interval": scenario_clustered_interval(
            [(row["passed"], row["evaluated"]) for row in rows]
        ),
        "rows": rows,
    }
