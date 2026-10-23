"""Bounded comparison of two committed formula versions on the same window.

Marginal intervals alone cannot identify covariance between overlapping samples.
Never assume independence: only exact census results or the same frozen formula
get an exact delta interval. Other deltas explicitly withhold an interval.
"""

import math

from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.service import read_results


def compare_runs(baseline, candidate):
    if baseline.definition.metric_id != candidate.definition.metric_id:
        raise MetricInputError("Compare runs of the same metric.")
    for field in ("window_start", "window_end", "filters", "source_run_id"):
        if getattr(baseline, field) != getattr(candidate, field):
            raise MetricInputError(
                "Comparison requires the same window, filters and evaluation task."
            )
    if baseline.definition.selection_unit != candidate.definition.selection_unit:
        raise MetricInputError("Comparison requires the same selection unit.")
    if baseline.definition.filters != candidate.definition.filters:
        raise MetricInputError("Comparison requires the same definition filters.")
    rows = []
    for run in (baseline, candidate):
        result = read_results(run, limit=1, include_uncertainty=True)["results"]
        if len(result) != 1:
            raise MetricInputError("Both runs require a committed overall result.")
        rows.append(result[0])
    left, right = rows
    delta = (
        None
        if left["value"] is None or right["value"] is None
        else right["value"] - left["value"]
    )
    if delta is not None and not math.isfinite(delta):
        delta = None
    identical = (
        baseline.snapshot_id == candidate.snapshot_id
        and baseline.definition.definition_hash == candidate.definition.definition_hash
    )
    exact = all(
        row["uncertainty"]["status"] == "ok"
        and row["uncertainty"]["std_error"] == 0
        and row["uncertainty"]["ci_low"]
        == row["uncertainty"]["ci_high"]
        == row["value"]
        and row["uncertainty"]["method"] in ("stratified_wor_fpc", "poisson")
        for row in rows
    )
    estimable = delta is not None and (identical or exact)
    return {
        "baseline_run_id": str(baseline.id),
        "candidate_run_id": str(candidate.id),
        "baseline_result_id": str(baseline.result_id),
        "candidate_result_id": str(candidate.result_id),
        "baseline_version": baseline.definition.version,
        "candidate_version": candidate.definition.version,
        "baseline": left,
        "candidate": right,
        "delta": delta,
        "delta_uncertainty": {
            "status": "ok" if estimable else "not_estimable",
            "reason": ""
            if estimable
            else ("no_value" if delta is None else "paired_covariance_unavailable"),
            "method": "identical_frozen_formula"
            if identical
            else ("census" if exact else ""),
            "std_error": 0.0 if estimable else None,
            "ci_low": delta if estimable else None,
            "ci_high": delta if estimable else None,
        },
    }
