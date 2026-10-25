"""Compare two runs scenario by scenario, on pass rates over trials.

A trial passes when every sub-goal check in its receipt held. The verdict on a
scenario comes from the difference in pass rates and its Newcombe 95% interval
against a ±10-point margin; eval scores ride along as context only.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteVersion,
)
from simulate.services.harness_versioning.read import run_version_labels
from simulate.services.run_reliability_v3 import wilson
from simulate.services.run_results_v3_scoring import HARNESS_FAILED, HARNESS_PASSED

# Two-sided 95%.
Z = 1.959963984540054
# Within this many points either way, a change is not meaningful.
MARGIN = 0.10
# Absorbs float noise, so a difference of exactly the margin reads as on it.
_EPSILON = 1e-9

IMPROVED = "improved"
REGRESSED = "regressed"
NO_MEANINGFUL_CHANGE = "no_meaningful_change"
INCONCLUSIVE = "inconclusive"

PAIRED = "paired"
CRITERIA_CHANGED = "criteria_changed"
NEW = "new"
NOT_APPLICABLE = "not_applicable"

# A call that never ran to an end says nothing about the agent.
_UNRUN_OUTCOMES = ("queued", "in_progress", "error")


def wilson_interval(passed: int, trials: int) -> tuple[float, float]:
    """The Wilson score interval of one proportion, without continuity correction."""
    low, high = wilson(passed, trials, Z) or (0.0, 100.0)
    return low / 100, high / 100


def newcombe_interval(
    passed: int, trials: int, other_passed: int, other_trials: int
) -> tuple[float, float]:
    """The 95% interval of ``p1 - p2``: Newcombe's hybrid score method (10)."""
    p1, p2 = passed / trials, other_passed / other_trials
    l1, u1 = wilson_interval(passed, trials)
    l2, u2 = wilson_interval(other_passed, other_trials)
    difference = p1 - p2
    low = difference - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    high = difference + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return low, high


def verdict(
    passed: int, trials: int, other_passed: int, other_trials: int
) -> tuple[str, tuple[float, float] | None]:
    """The current run (``passed``/``trials``) against the other, and the interval."""
    if not trials or not other_trials:
        return INCONCLUSIVE, None
    difference = passed / trials - other_passed / other_trials
    low, high = newcombe_interval(passed, trials, other_passed, other_trials)
    # Equal pass rates are no change, however few the trials.
    if abs(difference) <= _EPSILON:
        return NO_MEANINGFUL_CHANGE, (low, high)
    if low >= -MARGIN - _EPSILON and high <= MARGIN + _EPSILON:
        return NO_MEANINGFUL_CHANGE, (low, high)
    if low > 0 and difference > MARGIN + _EPSILON:
        return IMPROVED, (low, high)
    if high < 0 and difference < -MARGIN - _EPSILON:
        return REGRESSED, (low, high)
    return INCONCLUSIVE, (low, high)


def trial_passed(sub_goals: Any, harness_status: Any) -> bool | None:
    """Whether a trial passed: every sub-goal held. ``None`` when nothing decided it.

    A sub-goal nothing decided (``held`` null) leaves the whole trial undecided,
    even beside a failed one: counting those as failed while dropping the ones
    beside held checks would only ever drop would-be passes. A receipt without
    sub-goals falls back to its own status.
    """
    checks = [one for one in sub_goals or [] if isinstance(one, Mapping)]
    if checks:
        held = [one.get("held") for one in checks]
        if any(value is None for value in held):
            return None
        return all(value is True for value in held)
    status = str(harness_status or "").lower()
    if status in HARNESS_PASSED:
        return True
    if status in HARNESS_FAILED:
        return False
    return None


@dataclass
class ScenarioTrials:
    label: str
    passed: int = 0
    decided: int = 0
    # Trials that ran to an end but nothing decided.
    undecided: int = 0
    eval_passed: int = 0
    eval_measured: int = 0
    sub_goals: set[str] = field(default_factory=set)

    def add(
        self, sub_goals: Any, harness_status: Any, call_outcome: Any, eval_outcome: Any
    ) -> None:
        for one in sub_goals or []:
            if isinstance(one, Mapping) and one.get("name"):
                self.sub_goals.add(str(one["name"]))
        outcome = (
            None
            if call_outcome in _UNRUN_OUTCOMES
            else trial_passed(sub_goals, harness_status)
        )
        if outcome is not None:
            self.decided += 1
            self.passed += int(outcome)
        elif call_outcome not in _UNRUN_OUTCOMES:
            self.undecided += 1
        if eval_outcome in ("passed", "failed"):
            self.eval_measured += 1
            self.eval_passed += int(eval_outcome == "passed")

    def side(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "trials": self.decided,
            "undecided": self.undecided,
            "pass_rate": _percent(self.passed, self.decided),
            "eval_pass_rate": _percent(self.eval_passed, self.eval_measured),
        }


def _percent(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 2) if whole else None


def _points(value: float) -> float:
    return round(value * 100, 2)


def changed_sub_goals(
    names: Iterable[str],
    digests: Mapping[str, Any] | None,
    other_digests: Mapping[str, Any] | None,
) -> list[str]:
    """The named sub-goals defined differently in the two environment versions."""
    if not isinstance(digests, Mapping) or not isinstance(other_digests, Mapping):
        return []
    return sorted(
        name for name in names if digests.get(name) != other_digests.get(name)
    )


def scenario_pairs(
    current: Mapping[str, ScenarioTrials],
    against: Mapping[str, ScenarioTrials],
    *,
    digests: Mapping[str, Any] | None = None,
    against_digests: Mapping[str, Any] | None = None,
    removed: Mapping[str, Mapping[str, Any]] | None = None,
    not_run: Collection[str] = (),
    against_is_newer: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Each scenario of either run as a pair, and the counts over them.

    A key only the newer run has is new; one only the older run has is not
    applicable, with the suite change that removed it from ``removed``:
    ``{"reason", "version"}``. A key in ``not_run`` was in the other run's suite
    but left out of that run, so it is not applicable either, with no reason.
    """
    removed = removed or {}
    pairs = []
    for key in sorted(current.keys() | against.keys()):
        now, then = current.get(key), against.get(key)
        pair: dict[str, Any] = {
            "scenario_key": key,
            "scenario": now.label if now else then.label if then else key,
            "band": PAIRED,
            "verdict": None,
            "difference": None,
            "interval": None,
            "current": now.side() if now else None,
            "against": then.side() if then else None,
            "changed_sub_goals": [],
            "reason": None,
            "removed_in": None,
        }
        if now is None or then is None:
            only_in_newer = (now is None) == against_is_newer
            if only_in_newer and key not in not_run:
                pair["band"] = NEW
            else:
                gone = removed.get(key) or {}
                pair.update(
                    band=NOT_APPLICABLE,
                    reason=gone.get("reason"),
                    removed_in=gone.get("version"),
                )
        else:
            changed = changed_sub_goals(
                now.sub_goals | then.sub_goals, digests, against_digests
            )
            outcome, interval = verdict(
                now.passed, now.decided, then.passed, then.decided
            )
            pair.update(
                band=CRITERIA_CHANGED if changed else PAIRED,
                verdict=outcome,
                changed_sub_goals=changed,
            )
            if now.decided and then.decided:
                pair["difference"] = _points(
                    now.passed / now.decided - then.passed / then.decided
                )
            if interval is not None:
                pair["interval"] = {
                    "low": _points(interval[0]),
                    "high": _points(interval[1]),
                }
        pairs.append(pair)
    return pairs, _summary(pairs, current, against)


def _summary(
    pairs: list[dict[str, Any]],
    current: Mapping[str, ScenarioTrials],
    against: Mapping[str, ScenarioTrials],
) -> dict[str, Any]:
    def bands(band: str) -> int:
        return sum(pair["band"] == band for pair in pairs)

    def verdicts(outcome: str) -> int:
        # A pair judged on changed criteria is set apart, not counted as a change.
        return sum(
            pair["band"] == PAIRED and pair["verdict"] == outcome for pair in pairs
        )

    def rate(trials: Mapping[str, ScenarioTrials]) -> float | None:
        return _percent(
            sum(one.passed for one in trials.values()),
            sum(one.decided for one in trials.values()),
        )

    return {
        "paired": bands(PAIRED),
        "improved": verdicts(IMPROVED),
        "regressed": verdicts(REGRESSED),
        "no_meaningful_change": verdicts(NO_MEANINGFUL_CHANGE),
        "inconclusive": verdicts(INCONCLUSIVE),
        "criteria_changed": bands(CRITERIA_CHANGED),
        "new": bands(NEW),
        "not_applicable": bands(NOT_APPLICABLE),
        "current_pass_rate": rate(current),
        "against_pass_rate": rate(against),
        "margin": _points(MARGIN),
    }


def run_versions(execution_ids: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Each run's pinned ``{agent, environment, suite}``; an unpinned run's are null."""
    labels = run_version_labels(execution_ids)
    return {
        str(one): {
            piece: (labels.get(str(one)) or {}).get(f"{piece}_version")
            for piece in ("agent", "environment", "suite")
        }
        for one in execution_ids
        if one
    }


def sub_goal_digests(environment_version_ids: Iterable[Any]) -> dict[str, Any]:
    """Each environment version's ``documents.sub_goal_digests``, by id."""
    ids = [one for one in environment_version_ids if one]
    if not ids:
        return {}
    rows = HarnessEnvironmentVersion.no_workspace_objects.filter(
        id__in=ids
    ).values_list("id", "documents__sub_goal_digests")
    return {str(version_id): digests for version_id, digests in rows}


def suite_members(suite_id: Any, keys: Iterable[str]) -> set[str]:
    """Which of ``keys`` are scenarios of this suite version."""
    keys = set(keys)
    if not suite_id or not keys:
        return set()
    return set(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=suite_id, scenario_key__in=keys
        ).values_list("scenario_key", flat=True)
    )


def _suite_parents(suite_id: Any) -> dict[str, str | None]:
    """Each version of this suite's job, by id, with its ``parent``."""
    return {
        str(version_id): str(parent_id) if parent_id else None
        for version_id, parent_id in HarnessSuiteVersion.no_workspace_objects.filter(
            job_id__in=HarnessSuiteVersion.no_workspace_objects.filter(
                id=suite_id
            ).values("job_id")
        ).values_list("id", "parent_id")
    }


def _lineage(
    parents: Mapping[str, str | None], start: str, ancestor: str
) -> list[str] | None:
    """The versions from ``start`` up its parents to ``ancestor``, which is left
    out; ``None`` when the way never gets there."""
    chain: list[str] = []
    at: str | None = start
    while at != ancestor:
        if at is None or at not in parents or len(chain) >= len(parents):
            return None
        chain.append(at)
        at = parents[at]
    return chain


def suite_is_newer(suite_id: Any, other_suite_id: Any) -> bool | None:
    """Whether ``suite_id`` descends from ``other_suite_id`` along ``parent``.

    ``None`` when neither descends from the other, they are the same, or one is
    missing: their lineage can't order them.
    """
    if not suite_id or not other_suite_id or str(suite_id) == str(other_suite_id):
        return None
    parents = _suite_parents(suite_id)
    if _lineage(parents, str(suite_id), str(other_suite_id)):
        return True
    if _lineage(parents, str(other_suite_id), str(suite_id)):
        return False
    return None


def removed_keys(
    older_suite_id: Any, newer_suite_id: Any, keys: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Why each key left the suite: the version on the way whose diff removed it.

    The way is the newer suite's ``parent`` lineage back to the older suite; when
    it never gets there (a roll back branched off, a parent was deleted) nothing
    is named. A key still in the newer suite was left out of a run, not removed.
    """
    keys = set(keys) - suite_members(newer_suite_id, keys)
    if not keys or not older_suite_id or not newer_suite_id:
        return {}
    chain = _lineage(
        _suite_parents(newer_suite_id), str(newer_suite_id), str(older_suite_id)
    )
    if not chain:
        return {}
    diffs = {
        str(version_id): (number, diff)
        for version_id, number, diff in HarnessSuiteVersion.no_workspace_objects.filter(
            id__in=chain
        ).values_list("id", "version_number", "diff")
    }
    found: dict[str, dict[str, Any]] = {}
    # Oldest first, so a key removed, added back and removed again names the last.
    for version_id in reversed(chain):
        number, diff = diffs[version_id]
        removed = diff.get("removed") if isinstance(diff, Mapping) else None
        for item in removed or []:
            key = item.get("key") if isinstance(item, Mapping) else None
            if key in keys:
                found[key] = {
                    "reason": item.get("reason") or None,
                    "version": {"id": version_id, "label": f"v{number}"},
                }
    return found
