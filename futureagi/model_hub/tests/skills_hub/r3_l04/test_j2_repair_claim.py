"""J2: an application repair must recheck the original failure, not just text."""

from dataclasses import replace
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


@pytest.mark.parametrize("family", ["tools", "delivery", "selection"])
def test_historical_failure_must_be_checked_on_candidate(family):
    """One-sided proof: removing the candidate assertion cannot verify a repair."""
    case_id = str(uuid4())
    matrix = planned_matrix(case_ids=[case_id], baselines=["reference"], repetitions=1)
    checks = {
        case_id: [
            {
                "check_id": "answer",
                "family": "answer",
                "variants": ["candidate", "reference"],
            },
            {
                "check_id": "original-failure",
                "family": family,
                "variants": ["reference"],
            },
        ]
    }
    trials = [
        NativeTrial(
            case_id,
            "candidate",
            0,
            "completed",
            (NativeCheck("answer", "answer", "pass"),),
        ),
        NativeTrial(
            case_id,
            "reference",
            0,
            "completed",
            (
                NativeCheck("answer", "answer", "pass"),
                NativeCheck("original-failure", family, "fail"),
            ),
        ),
    ]
    # An ordinary asymmetric comparison is legitimate; a repair claim is not.
    assert (
        reduce_native_results(matrix=matrix, required_checks=checks, trials=trials)[
            "repair_verified"
        ]
        is False
    )
    with pytest.raises(SkillsHubError, match="candidate and reference") as error:
        reduce_native_results(
            matrix=matrix,
            required_checks=checks,
            trials=trials,
            reproduction_checks={case_id: "original-failure"},
        )
    assert error.value.code == "invalid_test_config"


@pytest.mark.parametrize("reproduction", [[], ["case"], "case", True, {"case": []}])
def test_malformed_reproduction_contract_is_a_domain_error(reproduction):
    case_id = str(uuid4())
    with pytest.raises(SkillsHubError) as error:
        reduce_native_results(
            matrix=planned_matrix(
                case_ids=[case_id], baselines=["reference"], repetitions=1
            ),
            required_checks={
                case_id: [
                    {
                        "check_id": "answer",
                        "family": "answer",
                        "variants": ["candidate", "reference"],
                    },
                ]
            },
            trials=[],
            reproduction_checks=reproduction,
        )
    assert error.value.code == "invalid_test_config"


@pytest.mark.parametrize(
    "candidate_tool", ["pass", "fail", "unverifiable", "not_graded", None]
)
def test_text_success_never_substitutes_for_repaired_tool(candidate_tool):
    case_id = str(uuid4())
    checks = {
        case_id: [
            {
                "check_id": "answer",
                "family": "answer",
                "variants": ["candidate", "reference"],
            },
            {
                "check_id": "tool",
                "family": "tools",
                "variants": ["candidate", "reference"],
            },
        ]
    }
    candidate = [NativeCheck("answer", "answer", "pass")]
    if candidate_tool is not None:
        candidate.append(NativeCheck("tool", "tools", candidate_tool))
    reduced = reduce_native_results(
        matrix=planned_matrix(
            case_ids=[case_id], baselines=["reference"], repetitions=1
        ),
        required_checks=checks,
        trials=[
            NativeTrial(case_id, "candidate", 0, "completed", tuple(candidate)),
            NativeTrial(
                case_id,
                "reference",
                0,
                "completed",
                (
                    NativeCheck("answer", "answer", "pass"),
                    NativeCheck("tool", "tools", "fail"),
                ),
            ),
        ],
        reproduction_checks={case_id: "tool"},
    )
    assert reduced["reproduction"] == "reproduced"
    assert reduced["repair_verified"] is (candidate_tool == "pass")
    assert reduced["cells"][0]["answer"] == "pass"
    if candidate_tool == "fail":
        assert reduced["state"] == "needs_changes"


@pytest.fixture
def selection():
    catalog = [
        {
            "skill_id": str(uuid4()),
            "version_id": str(uuid4()),
            "digest": "sha256:" + "a" * 64,
            "mount": f"skills/child-{index}",
        }
        for index in range(15)
    ]
    expected = validate_selection(
        {
            "catalog_digest": catalog_identity(catalog),
            "invocation_mode": "natural",
            "child_selection": [
                {"skill_id": catalog[0]["skill_id"], "expectation": "required"},
                {"skill_id": catalog[1]["skill_id"], "expectation": "forbidden"},
            ],
        },
        links=catalog,
    )
    observed = SelectionObservation(
        catalog_identity(catalog),
        "natural",
        True,
        True,
        tuple(
            ChildObservation(row["skill_id"], index == 0, index == 0)
            for index, row in enumerate(catalog)
        ),
    )
    return expected, observed


@pytest.mark.parametrize("field", ["complete", "instrumented"])
@pytest.mark.parametrize("value", [1, "false", [], None])
def test_selection_receipt_requires_boolean_observability(selection, field, value):
    expected, observed = selection
    with pytest.raises(SkillsHubError) as error:
        evaluate_child_selection(
            selection=expected, observation=replace(observed, **{field: value})
        )
    assert error.value.code == "invalid_receipt"


def test_full_catalog_near_miss_and_forced_invocation_are_not_success(selection):
    expected, observed = selection
    assert {
        row["verdict"]
        for row in evaluate_child_selection(selection=expected, observation=observed)
    } == {"pass"}
    near_miss = replace(
        observed,
        children=tuple(
            replace(row, requested=True, root_delivered=True) if index == 1 else row
            for index, row in enumerate(observed.children)
        ),
    )
    assert "fail" in {
        row["verdict"]
        for row in evaluate_child_selection(selection=expected, observation=near_miss)
    }
    forced = replace(observed, invocation_mode="forced")
    assert {
        row["verdict"]
        for row in evaluate_child_selection(selection=expected, observation=forced)
    } == {"unverifiable"}
