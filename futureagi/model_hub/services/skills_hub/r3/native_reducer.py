"""Skills-native evidence reduction over the frozen matrix, not Simulation uplift."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal, NoReturn
from uuid import UUID

from model_hub.services.skills_hub.errors import SkillsHubError

Verdict = Literal["pass", "fail", "not_applicable", "not_graded", "unverifiable"]
TrialStatus = Literal["completed", "error", "incomplete", "cancelled", "unknown"]
FAMILIES = ("answer", "delivery", "selection", "tools")
ARMS = {"candidate", "production", "reference", "without_target"}
VERDICTS = {"pass", "fail", "not_applicable", "not_graded", "unverifiable"}


@dataclass(frozen=True)
class NativeCheck:
    check_id: str
    family: Literal["answer", "delivery", "selection", "tools"]
    verdict: Verdict


@dataclass(frozen=True)
class NativeTrial:
    case_id: str
    variant: str
    repeat_index: int
    status: TrialStatus
    checks: tuple[NativeCheck, ...]


def _invalid(message: str) -> NoReturn:
    raise SkillsHubError("invalid_test_config", message, status=400)


def planned_matrix(
    *, case_ids: list[str], baselines: list[str], repetitions: int
) -> list[dict]:
    if (
        not isinstance(case_ids, list)
        or not case_ids
        or len(case_ids) > 20
        or any(not isinstance(value, str) for value in case_ids)
        or len(set(case_ids)) != len(case_ids)
    ):
        _invalid("Choose 1–20 distinct reviewed cases.")
    try:
        ids = [str(UUID(str(value))) for value in case_ids]
    except (ValueError, TypeError, AttributeError) as exc:
        raise SkillsHubError(
            "invalid_test_config", "Invalid case identity.", status=400
        ) from exc
    if len(set(ids)) != len(ids):
        _invalid("Choose distinct case identities.")
    if type(repetitions) is not int or repetitions not in {1, 3}:
        _invalid("Choose one or three independent repetitions.")
    if (
        not isinstance(baselines, list)
        or not 1 <= len(baselines) <= 2
        or any(not isinstance(value, str) for value in baselines)
        or len(set(baselines)) != len(baselines)
        or not set(baselines) <= ARMS - {"candidate"}
    ):
        _invalid("Choose one or two distinct baseline arms.")
    return [
        {"case_id": case_id, "variant": variant, "repeat_index": repeat}
        for case_id in ids
        for variant in ["candidate", *baselines]
        for repeat in range(repetitions)
    ]


def validate_matrix(matrix: list[dict]) -> list[dict]:
    if not isinstance(matrix, list) or not matrix or len(matrix) > 180:
        _invalid("A native plan must contain 1–180 matrix cells.")
    for cell in matrix:
        if not isinstance(cell, dict) or set(cell) != {
            "case_id",
            "variant",
            "repeat_index",
        }:
            _invalid("Use exact native matrix cell fields.")
        if not isinstance(cell["case_id"], str) or not isinstance(cell["variant"], str):
            _invalid("Use string case and arm identities.")
        if type(cell["repeat_index"]) is not int or not 0 <= cell["repeat_index"] <= 2:
            _invalid("Invalid trial repetition.")
    case_ids = list(dict.fromkeys(cell["case_id"] for cell in matrix))
    baselines = sorted({cell["variant"] for cell in matrix} - {"candidate"})
    expected = planned_matrix(
        case_ids=case_ids,
        baselines=baselines,
        repetitions=max(cell["repeat_index"] for cell in matrix) + 1,
    )
    keys = [_key(cell) for cell in matrix]
    if len(keys) != len(set(keys)) or set(keys) != {_key(cell) for cell in expected}:
        _invalid("The immutable case × arm × repeat matrix must be complete.")
    return expected


def _key(cell: dict) -> tuple[str, str, int]:
    return cell["case_id"], cell["variant"], cell["repeat_index"]


def _family(trial: NativeTrial | None, family: str) -> str:
    if trial is None:
        return "unverifiable"
    values = [check.verdict for check in trial.checks if check.family == family]
    # Retain demonstrated failures even alongside interrupted/unverifiable work.
    if "fail" in values:
        return "fail"
    if trial.status != "completed" or "unverifiable" in values:
        return "unverifiable"
    if not values or all(value == "not_applicable" for value in values):
        return "not_applicable"
    if "not_graded" in values:
        return "not_graded"
    return "pass"


def reduce_native_results(
    *,
    matrix: list[dict],
    trials: list[NativeTrial],
    required_checks: dict[str, list[dict]],
    reproduction_checks: dict[str, str] | None = None,
) -> dict:
    """Missing cells/checks retain their denominators; both-arms-fail never passes.

    Required checks are frozen per case, with check_id/family and applicable arms.
    They are evaluator-side data and must never be sent in task messages.
    """
    planned = validate_matrix(matrix)
    keys = {_key(cell) for cell in planned}
    cases = list(dict.fromkeys(cell["case_id"] for cell in planned))
    variants = list(dict.fromkeys(cell["variant"] for cell in planned))
    if not isinstance(required_checks, dict) or set(required_checks) != set(cases):
        _invalid("Freeze check expectations for every planned case.")
    check_index = {}
    for case_id, checks in required_checks.items():
        if not isinstance(checks, list) or not checks or len(checks) > 50:
            _invalid("Every native case requires bounded assertions.")
        indexed = {}
        for check in checks:
            if not isinstance(check, dict) or set(check) != {
                "check_id",
                "family",
                "variants",
            }:
                _invalid("Use the frozen native check contract.")
            if (
                not isinstance(check["check_id"], str)
                or not check["check_id"]
                or check["check_id"] in indexed
                or check["family"] not in FAMILIES
                or not isinstance(check["variants"], list)
                or not check["variants"]
                or any(not isinstance(value, str) for value in check["variants"])
                or len(set(check["variants"])) != len(check["variants"])
                or not set(check["variants"]) <= set(variants)
            ):
                _invalid("Check IDs, families and applicable arms must be valid.")
            if check["family"] == "answer" and set(check["variants"]) != set(variants):
                _invalid("Answer assertions must be identical across every arm.")
            indexed[check["check_id"]] = check
        if not any("candidate" in check["variants"] for check in checks):
            _invalid("The candidate must have at least one assertion.")
        check_index[case_id] = indexed
    observed = {}
    for trial in trials:
        if (
            not isinstance(trial.case_id, str)
            or not isinstance(trial.variant, str)
            or type(trial.repeat_index) is not int
            or not isinstance(trial.status, str)
        ):
            _invalid("Invalid native trial identity or status.")
        key = (trial.case_id, trial.variant, trial.repeat_index)
        if (
            key not in keys
            or key in observed
            or trial.status
            not in {"completed", "error", "incomplete", "cancelled", "unknown"}
        ):
            _invalid("Unexpected, duplicate or invalid native result.")
        seen = set()
        for result in trial.checks:
            if not isinstance(result.check_id, str) or not isinstance(
                result.verdict, str
            ):
                _invalid("Invalid native check result.")
            expected = check_index[trial.case_id].get(result.check_id)
            if (
                result.check_id in seen
                or expected is None
                or result.family != expected["family"]
                or result.verdict not in VERDICTS
            ):
                _invalid("Native check results must match their frozen identities.")
            if (
                trial.variant not in expected["variants"]
                and result.verdict != "not_applicable"
            ):
                _invalid("An inapplicable check cannot contribute to a verdict.")
            if (
                trial.variant in expected["variants"]
                and result.verdict == "not_applicable"
            ):
                _invalid("A required check cannot be omitted as inapplicable.")
            seen.add(result.check_id)
        missing = tuple(
            NativeCheck(check_id, expected["family"], "unverifiable")
            for check_id, expected in check_index[trial.case_id].items()
            if trial.variant in expected["variants"] and check_id not in seen
        )
        observed[key] = NativeTrial(
            trial.case_id,
            trial.variant,
            trial.repeat_index,
            trial.status,
            (*trial.checks, *missing),
        )
    cells = []
    counts = Counter(
        planned=len(planned), finished=0, missing=0, errors=0, incomplete=0
    )
    for cell in planned:
        cell_trial = observed.get(_key(cell))
        families = {family: _family(cell_trial, family) for family in FAMILIES}
        applicable = [value for value in families.values() if value != "not_applicable"]
        failure = "fail" in applicable
        incomplete = (
            cell_trial is None
            or cell_trial.status != "completed"
            or any(value in {"unverifiable", "not_graded"} for value in applicable)
        )
        counts["finished"] += cell_trial is not None and cell_trial.status in {
            "completed",
            "error",
            "cancelled",
        }
        counts["missing"] += cell_trial is None
        counts["errors"] += cell_trial is not None and cell_trial.status == "error"
        counts["incomplete"] += incomplete
        cells.append(
            {
                **cell,
                "status": cell_trial.status if cell_trial else "not_started",
                **families,
                "failure": failure,
                "incomplete": incomplete,
            }
        )
    candidate = [cell for cell in cells if cell["variant"] == "candidate"]
    candidate_failures = sum(cell["failure"] for cell in candidate)
    candidate_incomplete = sum(cell["incomplete"] for cell in candidate)
    comparisons = []
    for case_id in cases:
        deltas = []
        for baseline in variants:
            if baseline == "candidate":
                continue
            left = [cell for cell in candidate if cell["case_id"] == case_id]
            right = [
                cell
                for cell in cells
                if cell["case_id"] == case_id and cell["variant"] == baseline
            ]
            matched = all(
                cell["status"] == "completed" and cell["answer"] in {"pass", "fail"}
                for cell in [*left, *right]
            )
            delta = (
                (
                    sum(cell["answer"] == "pass" for cell in left)
                    - sum(cell["answer"] == "pass" for cell in right)
                )
                / len(left)
                if matched
                else None
            )
            deltas.append(delta)
            comparisons.append(
                {
                    "case_id": case_id,
                    "baseline": baseline,
                    "matched": matched,
                    "answer_delta": delta,
                }
            )
    reproduction = "not_requested"
    if reproduction_checks is not None and (
        not isinstance(reproduction_checks, dict)
        or any(
            not isinstance(case_id, str) or not isinstance(check_id, str)
            for case_id, check_id in reproduction_checks.items()
        )
    ):
        _invalid("Use a mapping of historical cases to frozen check identities.")
    if reproduction_checks:
        if "reference" not in variants or not set(reproduction_checks) <= set(cases):
            _invalid(
                "Historical reproduction needs the recorded reference arm and case."
            )
        reproduced = []
        for case_id, check_id in reproduction_checks.items():
            expected = check_index[case_id].get(check_id)
            # Reproducing a reference-only tool/delivery/selection failure says
            # nothing about its repair. The very same assertion must run on the
            # candidate; a passing answer check cannot stand in for it.
            if expected is None or not {"candidate", "reference"} <= set(
                expected["variants"]
            ):
                _invalid(
                    "Reproduction must name the same frozen check on candidate and reference."
                )
            reference = [
                observed.get(_key(cell))
                for cell in planned
                if cell["case_id"] == case_id and cell["variant"] == "reference"
            ]
            reproduced.append(
                all(
                    trial is not None
                    and trial.status == "completed"
                    and any(
                        check.check_id == check_id and check.verdict == "fail"
                        for check in trial.checks
                    )
                    for trial in reference
                )
            )
        reproduction = "reproduced" if all(reproduced) else "not_reproduced"
    state = (
        "needs_changes"
        if candidate_failures
        else "inconclusive" if counts["incomplete"] else "works_on_examples"
    )
    if reproduction == "not_reproduced" and not candidate_failures:
        state = "inconclusive"
    return {
        "state": state,
        "counts": dict(counts),
        "candidate_failures": candidate_failures,
        "candidate_incomplete": candidate_incomplete,
        "reproduction": reproduction,
        "repair_verified": state == "works_on_examples"
        and reproduction == "reproduced",
        "root_routing": "not_applicable",
        "cells": cells,
        "answer_comparisons": comparisons,
        "regressed_cases": len(
            {
                row["case_id"]
                for row in comparisons
                if row["answer_delta"] is not None and row["answer_delta"] < 0
            }
        ),
    }
