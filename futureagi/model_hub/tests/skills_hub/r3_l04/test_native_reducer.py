"""Planned denominators and independent evidence families, including 15 children."""

from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.child_selection import (
    ChildObservation,
    SelectionObservation,
    catalog_identity,
    evaluate_child_selection,
    validate_selection,
)
from model_hub.services.skills_hub.r3.native_reducer import (
    NativeCheck,
    NativeTrial,
    planned_matrix,
    reduce_native_results,
)


@pytest.fixture
def plan():
    case_id, check_id = str(uuid4()), str(uuid4())
    matrix = planned_matrix(case_ids=[case_id], baselines=["reference"], repetitions=1)
    checks = {
        case_id: [
            {
                "check_id": check_id,
                "family": "answer",
                "variants": ["candidate", "reference"],
            }
        ]
    }

    def result(variant, verdict="pass", status="completed"):
        return NativeTrial(
            case_id, variant, 0, status, (NativeCheck(check_id, "answer", verdict),)
        )

    return matrix, checks, result


def test_both_arms_fail_is_candidate_failure_not_relative_success(plan):
    matrix, checks, result = plan
    reduced = reduce_native_results(
        matrix=matrix,
        required_checks=checks,
        trials=[result("candidate", "fail"), result("reference", "fail")],
    )
    assert reduced["state"] == "needs_changes"
    assert reduced["candidate_failures"] == 1
    assert reduced["answer_comparisons"][0]["answer_delta"] == 0
    assert reduced["root_routing"] == "not_applicable"


def test_missing_baseline_keeps_full_denominator(plan):
    matrix, checks, result = plan
    reduced = reduce_native_results(
        matrix=matrix, required_checks=checks, trials=[result("candidate")]
    )
    assert reduced["counts"] == {
        "planned": 2,
        "finished": 1,
        "missing": 1,
        "errors": 0,
        "incomplete": 1,
    }
    assert reduced["state"] == "inconclusive"
    assert reduced["answer_comparisons"][0]["answer_delta"] is None


def test_failure_survives_incomplete_work(plan):
    matrix, checks, result = plan
    reduced = reduce_native_results(
        matrix=matrix, required_checks=checks, trials=[result("candidate", "fail")]
    )
    assert reduced["state"] == "needs_changes"
    assert reduced["counts"]["incomplete"] == 1


@pytest.mark.parametrize("status", ["error", "unknown", "incomplete", "cancelled"])
def test_startup_tool_parser_and_unknown_cannot_pass(plan, status):
    matrix, checks, result = plan
    reduced = reduce_native_results(
        matrix=matrix,
        required_checks=checks,
        trials=[result("candidate", status=status), result("reference")],
    )
    assert reduced["state"] == "inconclusive"
    assert reduced["candidate_incomplete"] == 1


def test_missing_check_cannot_become_green(plan):
    matrix, checks, result = plan
    candidate = replace(result("candidate"), checks=())
    reduced = reduce_native_results(
        matrix=matrix, required_checks=checks, trials=[candidate, result("reference")]
    )
    assert reduced["state"] == "inconclusive"


def test_actual_failure_signature_must_reproduce(plan):
    matrix, checks, result = plan
    case_id = matrix[0]["case_id"]
    reproduction = {case_id: checks[case_id][0]["check_id"]}
    good = reduce_native_results(
        matrix=matrix,
        required_checks=checks,
        trials=[result("candidate"), result("reference", "fail")],
        reproduction_checks=reproduction,
    )
    assert good["reproduction"] == "reproduced"
    assert good["repair_verified"] is True
    for reference in (result("reference"), result("reference", "fail", "error")):
        reduced = reduce_native_results(
            matrix=matrix,
            required_checks=checks,
            trials=[result("candidate"), reference],
            reproduction_checks=reproduction,
        )
        assert reduced["reproduction"] == "not_reproduced"
        assert reduced["repair_verified"] is False
        assert reduced["state"] == "inconclusive"


def test_duplicate_or_unplanned_trial_rejected(plan):
    matrix, checks, result = plan
    for trials in (
        [result("candidate"), result("candidate")],
        [replace(result("candidate"), case_id=str(uuid4()))],
    ):
        with pytest.raises(SkillsHubError, match="Unexpected"):
            reduce_native_results(matrix=matrix, required_checks=checks, trials=trials)


def test_missing_planned_cell_is_not_an_accepted_plan(plan):
    matrix, checks, result = plan
    with pytest.raises(SkillsHubError):
        reduce_native_results(matrix=matrix[:1], required_checks=checks, trials=[])


def test_three_repeats_one_pass_plus_unrun_is_incomplete(plan):
    _, checks, result = plan
    matrix = planned_matrix(
        case_ids=list(checks), baselines=["reference"], repetitions=3
    )
    reduced = reduce_native_results(
        matrix=matrix,
        required_checks=checks,
        trials=[result("candidate"), result("reference")],
    )
    assert reduced["counts"]["planned"] == 6
    assert reduced["counts"]["missing"] == 4
    assert reduced["state"] == "inconclusive"


def test_answer_grades_equal_with_delivery_applicable_only_to_candidate(plan):
    matrix, checks, result = plan
    case_id = matrix[0]["case_id"]
    delivery = str(uuid4())
    checks[case_id].append(
        {"check_id": delivery, "family": "delivery", "variants": ["candidate"]}
    )
    candidate = replace(
        result("candidate"),
        checks=(*result("candidate").checks, NativeCheck(delivery, "delivery", "pass")),
    )
    reduced = reduce_native_results(
        matrix=matrix, required_checks=checks, trials=[candidate, result("reference")]
    )
    assert reduced["answer_comparisons"][0]["answer_delta"] == 0
    assert reduced["state"] == "works_on_examples"


def test_mixed_baseline_regression_not_hidden():
    case_id, check_id = str(uuid4()), str(uuid4())
    variants = ["candidate", "production", "without_target"]
    matrix = planned_matrix(case_ids=[case_id], baselines=variants[1:], repetitions=3)
    checks = {
        case_id: [{"check_id": check_id, "family": "answer", "variants": variants}]
    }
    trials = [
        NativeTrial(
            case_id,
            variant,
            repeat,
            "completed",
            (
                NativeCheck(
                    check_id,
                    "answer",
                    (
                        "pass"
                        if variant == "production"
                        or variant == "candidate"
                        and repeat < 2
                        else "fail"
                    ),
                ),
            ),
        )
        for variant in variants
        for repeat in range(3)
    ]
    reduced = reduce_native_results(
        matrix=matrix, required_checks=checks, trials=trials
    )
    assert reduced["regressed_cases"] == 1
    assert reduced["state"] == "needs_changes"


@pytest.mark.parametrize(
    "field,value", [("case_id", []), ("variant", {}), ("repeat_index", True)]
)
def test_malformed_matrix_is_a_domain_error(plan, field, value):
    matrix, checks, _ = plan
    matrix[0][field] = value
    with pytest.raises(SkillsHubError) as error:
        reduce_native_results(matrix=matrix, required_checks=checks, trials=[])
    assert error.value.code == "invalid_test_config"


def test_boolean_repeat_and_inapplicable_required_check_are_rejected(plan):
    matrix, checks, result = plan
    for trial in (
        replace(result("candidate"), repeat_index=False),
        result("candidate", "not_applicable"),
    ):
        with pytest.raises(SkillsHubError):
            reduce_native_results(matrix=matrix, required_checks=checks, trials=[trial])


@pytest.fixture
def catalog():
    return [
        {
            "skill_id": str(uuid4()),
            "version_id": str(uuid4()),
            "digest": "sha256:" + "a" * 64,
            "mount": f"skills/child-{index}/",
        }
        for index in range(15)
    ]


def test_fifteen_child_refund_chargeback_near_miss(catalog):
    selection = validate_selection(
        {
            "catalog_digest": catalog_identity(catalog),
            "invocation_mode": "natural",
            "child_selection": [
                {
                    "skill_id": row["skill_id"],
                    "expectation": (
                        "required" if i == 0 else "forbidden" if i == 1 else "optional"
                    ),
                }
                for i, row in enumerate(catalog)
            ],
        },
        links=catalog,
    )
    observed = SelectionObservation(
        selection["catalog_digest"],
        "natural",
        True,
        True,
        tuple(
            ChildObservation(row["skill_id"], i in {0, 1}, i == 0)
            for i, row in enumerate(catalog)
        ),
    )
    outcomes = {
        item["skill_id"]: item
        for item in evaluate_child_selection(selection=selection, observation=observed)
    }
    assert outcomes[catalog[0]["skill_id"]]["verdict"] == "pass"
    assert outcomes[catalog[1]["skill_id"]]["verdict"] == "fail"
    assert len(outcomes) == 15
    assert sum(row["verdict"] == "not_applicable" for row in outcomes.values()) == 13


@pytest.mark.parametrize(
    "forced,instrumented,complete,missing",
    [
        (True, True, True, False),
        (False, False, True, False),
        (False, True, False, False),
        (False, True, True, True),
    ],
)
def test_forced_and_missing_observation_never_prove_nonselection(
    catalog, forced, instrumented, complete, missing
):
    selection: dict[str, Any] = {
        "catalog_digest": catalog_identity(catalog),
        "invocation_mode": "forced" if forced else "natural",
        "child_selection": [
            {"skill_id": catalog[0]["skill_id"], "expectation": "forbidden"}
        ],
    }
    observed = SelectionObservation(
        selection["catalog_digest"],
        selection["invocation_mode"],
        complete,
        instrumented,
        () if missing else (ChildObservation(catalog[0]["skill_id"], False, False),),
    )
    assert (
        evaluate_child_selection(selection=selection, observation=observed)[0][
            "verdict"
        ]
        == "unverifiable"
    )


def test_uninstrumented_positive_selection_is_not_certified(catalog):
    selection: dict[str, Any] = {
        "catalog_digest": catalog_identity(catalog),
        "invocation_mode": "natural",
        "child_selection": [
            {"skill_id": catalog[0]["skill_id"], "expectation": "required"}
        ],
    }
    observed = SelectionObservation(
        selection["catalog_digest"],
        "natural",
        True,
        False,
        (ChildObservation(catalog[0]["skill_id"], True, True),),
    )
    assert (
        evaluate_child_selection(selection=selection, observation=observed)[0][
            "verdict"
        ]
        == "unverifiable"
    )


def test_catalog_and_child_identity_are_bound(catalog):
    selection: dict[str, Any] = {
        "catalog_digest": catalog_identity(catalog),
        "invocation_mode": "natural",
        "child_selection": [{"skill_id": str(uuid4()), "expectation": "required"}],
    }
    with pytest.raises(SkillsHubError):
        validate_selection(selection, links=catalog)
    catalog[-1]["version_id"] = str(uuid4())
    with pytest.raises(SkillsHubError) as error:
        validate_selection(selection, links=catalog)
    assert error.value.code == "test_plan_changed"
