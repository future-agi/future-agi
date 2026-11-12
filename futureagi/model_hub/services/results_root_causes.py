"""Observed failed-evaluation groups, never inferred causal diagnoses."""
from math import isfinite

PASS_FAIL_OUTPUTS = frozenset({"pass_fail", "boolean", "bool", "binary"})


def _external(binding):
    config = binding.get("config_snapshot")
    external = config.get("external") if isinstance(config, dict) else None
    return external if isinstance(external, dict) else {}


def _declared_direction(binding):
    """The pin's own higher_is_better (external snapshot, then legacy), else None."""
    external = _external(binding)
    if type(external.get("higher_is_better")) is bool:
        return external["higher_is_better"]
    direction = binding.get("higher_is_better")
    return direction if type(direction) is bool else None


def pinned_metric_direction(binding):
    """The one direction rule of every Results projection, or None (unknown).

    Grid and matrix verdicts (so the Experiments tab's Success), the run
    catalog, Regressed-first movement and analysis all read it. Only what the
    pin declares counts: the canonical external snapshot's higher_is_better,
    then an explicit legacy higher_is_better, then a declared pass/fail output
    (its stored 1 is the pass). A direction is never inferred from a choice
    map, a threshold or the scores, so a choice eval without a declared
    direction stays unknown.
    """
    external = _external(binding)
    direction = _declared_direction(binding)
    if direction is not None:
        return direction
    output = external.get("output_type") or binding.get("output_type_normalized")
    return True if output in PASS_FAIL_OUTPUTS else None


def pinned_score_passes(value, binding):
    """Classify an assessed score from its immutable pin, or return unknown.

    Binary outputs already encode a verdict. Numeric thresholds need a known
    direction; the threshold boundary passes in either direction. Every
    caller passes ONE try's value (results_grid.trial_record reads one run
    item's score_refs; observed_failure_group one record), never an aggregate,
    so a pass/fail output whose direction is only its declared type gives a
    verdict from its stored 0/1 alone: any other value stays unknown.
    """
    if type(value) not in (bool, int, float):
        return None
    try:
        if not isfinite(value):
            return None
        if binding.get("output_type_normalized") in PASS_FAIL_OUTPUTS and value in (0, 1):
            return bool(value)
        direction = pinned_metric_direction(binding)
        if direction is not None and _declared_direction(binding) is None:
            return None  # Pass/fail type only: one try's non-0/1 value.
        threshold = binding.get("pass_threshold")
        if direction is None or type(threshold) not in (int, float) or not isfinite(threshold):
            return None
    except OverflowError:
        return None
    return value >= threshold if direction else value <= threshold


def observed_failure_group(run_id, metric, records, binding):
    assessed, failed, examples = 0, 0, set()
    for record in records:
        score = record.scores.get(metric, {})
        if record.status != "completed" or score.get("status") != "assessed":
            continue
        passes = pinned_score_passes(score.get("value"), binding)
        if passes is None:
            continue
        assessed += 1
        if not passes:
            failed += 1
            examples.add(str(record.example_id))
    if not assessed:
        return None
    return {
        "run_id": run_id, "metric": metric, "failed_trials": failed,
        "assessed_trials": assessed, "example_ids": sorted(examples)[:50],
        "examples_total": len(examples), "examples_has_more": len(examples) > 50,
        "causal": False,
    }


def root_cause_projection(groups, *, requested):
    reason = (
        "Causal diagnosis is unavailable. No project-authorized trace topology "
        "join is included in S1; failed evaluations are observations, not causes."
    )
    observed = None
    if requested:
        observed = {
            "groups": groups[:50], "total": len(groups), "has_more": len(groups) > 50,
            "availability": {
                "state": "available" if groups else "unavailable",
                "reason": None if groups else
                "No assessed evaluations with a usable pinned pass/fail rule in this scope.",
            },
        }
    return {
        "root_causes": None, "pattern": None, "observed_failures": observed,
        "availability": {"state": "unavailable", "reason": reason},
    }
