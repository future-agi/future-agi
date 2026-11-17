import json
from dataclasses import asdict, replace

import pytest

from model_hub.services.experiment_analysis import RunItemRecord, compare_run_items
from model_hub.services.experiment_release_gate import (
    GateCaseIdentity,
    ReleaseGatePolicy,
    evaluate_release_gate,
)


def _compare(before=(1.0, 0.0), after=(0.0, 1.0), **options):
    return compare_run_items(
        [RunItemRecord(str(index), score=value) for index, value in enumerate(before)],
        [RunItemRecord(str(index), score=value) for index, value in enumerate(after)],
        **options,
    )


def _gate(comparison, *, expected=("0", "1"), **options):
    return evaluate_release_gate(
        comparison,
        expected_cases=[GateCaseIdentity(identity, 1) for identity in expected],
        policy=options.pop("policy", ReleaseGatePolicy()),
        **options,
    )


def test_equal_averages_do_not_hide_regression_and_allowance_boundary():
    comparison = _compare()
    assert comparison.baseline.score_average == comparison.candidate.score_average
    report = _gate(comparison)
    assert report.verdict == "fail"
    assert (
        report.expected_count,
        report.comparable_count,
        report.regression_count,
    ) == (2, 2, 1)
    assert [(b.example_id, b.reason) for b in report.blockers] == [
        ("0", "regression_limit_exceeded")
    ]
    assert (
        _gate(comparison, policy=ReleaseGatePolicy(max_regressions=1)).verdict == "pass"
    )


@pytest.mark.parametrize("side", ["baseline", "candidate", "both"])
def test_missing_required_case_on_either_or_both_sides_blocks(side):
    comparison = compare_run_items(
        [] if side in {"baseline", "both"} else [RunItemRecord("0", score=1)],
        [] if side in {"candidate", "both"} else [RunItemRecord("0", score=1)],
    )
    report = _gate(comparison, expected=("0",))
    assert report.verdict == "fail"
    assert report.comparable_count == 0
    assert report.blockers[0].reason == "missing"


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize(
    "evidence", ["unknown", "unsupported", "not_assessed", "error", "invalid"]
)
def test_non_assessed_evidence_blocks_even_with_numeric_value(side, evidence):
    valid = RunItemRecord("0", score=1)
    invalid = RunItemRecord("0", scores={"score": {"status": evidence, "value": 1}})
    comparison = compare_run_items(
        [invalid if side == "baseline" else valid],
        [invalid if side == "candidate" else valid],
    )
    report = _gate(comparison, expected=("0",))
    assert report.verdict == "fail"
    assert report.blockers[0].reason == evidence


@pytest.mark.parametrize("task_status", ["failed", "running", "skipped", "cancelled"])
def test_failed_and_unfinished_execution_never_passes(task_status):
    comparison = compare_run_items(
        [RunItemRecord("0", score=1)], [RunItemRecord("0", score=1, status=task_status)]
    )
    assert _gate(comparison, expected=("0",)).verdict == "fail"


def test_duplicate_record_and_duplicate_comparison_identity_block():
    row = RunItemRecord("0", score=1)
    comparison = compare_run_items([row], [row, row])
    assert _gate(comparison, expected=("0",)).blockers[0].reason == "duplicate"
    comparison = _compare((1,), (1,))
    comparison = replace(comparison, cases=comparison.cases * 2)
    assert _gate(comparison, expected=("0",)).blockers[0].reason == "duplicate"


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), None, "1"])
def test_malformed_numeric_evidence_does_not_pass_a_forged_success_status(value):
    comparison = _compare((1,), (1,))
    comparison = replace(
        comparison, cases=(replace(comparison.cases[0], candidate_score=value),)
    )
    assert _gate(comparison, expected=("0",)).blockers[0].reason == "invalid"


@pytest.mark.parametrize("higher_is_better", [True, False])
def test_critical_regression_overrides_allowance_and_threshold_is_direction_aware(
    higher_is_better,
):
    before, after = (1.0, 0.0) if higher_is_better else (0.0, 1.0)
    comparison = _compare((before,), (after,), higher_is_better=higher_is_better)
    policy = ReleaseGatePolicy(100, (GateCaseIdentity("0", 1),))
    report = _gate(
        comparison,
        expected=("0",),
        policy=policy,
        passing_score=0.5,
        higher_is_better=higher_is_better,
    )
    assert report.verdict == "fail"
    assert {b.reason for b in report.blockers} == {
        "critical_regression",
        "critical_threshold",
    }
    tied = _compare((0.5,), (0.5,), higher_is_better=higher_is_better)
    assert (
        _gate(
            tied,
            expected=("0",),
            policy=policy,
            passing_score=0.5,
            higher_is_better=higher_is_better,
        ).verdict
        == "pass"
    )


def test_critical_tie_does_not_invent_threshold():
    comparison = _compare((0,), (0,))
    policy = ReleaseGatePolicy(critical_cases=(GateCaseIdentity("0", 1),))
    assert _gate(comparison, expected=("0",), policy=policy).verdict == "pass"
    assert (
        _gate(comparison, expected=("0",), policy=policy, passing_score=1).verdict
        == "fail"
    )


def test_trial_identity_and_full_plan_are_preserved():
    rows = [RunItemRecord("same", trial_number=1, score=1)]
    comparison = compare_run_items(rows, rows)
    report = evaluate_release_gate(
        comparison,
        expected_cases=[GateCaseIdentity("same", 1), GateCaseIdentity("same", 2)],
        policy=ReleaseGatePolicy(),
    )
    assert report.expected_count == 2
    assert [(b.example_id, b.trial_number, b.reason) for b in report.blockers] == [
        ("same", 2, "missing")
    ]


def test_empty_plan_fails_closed():
    report = _gate(_compare((), ()), expected=())
    assert report.verdict == "fail"
    assert asdict(report.blockers[0]) == {
        "example_id": None,
        "trial_number": None,
        "reason": "empty_plan",
    }


@pytest.mark.parametrize("allowance", [-1, 0.5, True, "1", float("inf")])
def test_policy_requires_nonnegative_integer(allowance):
    with pytest.raises(ValueError, match="max_regressions"):
        _gate(_compare(), policy=ReleaseGatePolicy(allowance))


@pytest.mark.parametrize(
    "identities",
    [
        (GateCaseIdentity("0", 1),) * 2,
        (GateCaseIdentity("other", 1),),
        (GateCaseIdentity("0", 0),),
        (GateCaseIdentity("", 1),),
    ],
)
def test_bad_critical_identities_are_rejected(identities):
    with pytest.raises(ValueError):
        _gate(_compare(), policy=ReleaseGatePolicy(critical_cases=identities))


def test_duplicate_expected_and_out_of_plan_comparison_identities_rejected():
    with pytest.raises(ValueError, match="unique"):
        _gate(_compare(), expected=("0", "0"))
    with pytest.raises(ValueError, match="outside"):
        _gate(_compare(), expected=("0",))


def test_report_is_deterministic_across_case_plan_and_policy_order():
    comparison = _compare()
    first = _gate(
        comparison,
        policy=ReleaseGatePolicy(
            critical_cases=(GateCaseIdentity("0", 1), GateCaseIdentity("1", 1))
        ),
    )
    second = _gate(
        replace(comparison, cases=tuple(reversed(comparison.cases))),
        expected=("1", "0"),
        policy=ReleaseGatePolicy(
            critical_cases=(GateCaseIdentity("1", 1), GateCaseIdentity("0", 1))
        ),
    )
    assert json.dumps(asdict(first), allow_nan=False) == json.dumps(
        asdict(second), allow_nan=False
    )


@pytest.mark.parametrize("change", ["direction", "status", "delta"])
def test_inconsistent_direction_status_or_delta_cannot_pass(change):
    comparison = _compare((0,), (1,))
    options = {}
    if change == "direction":
        options["higher_is_better"] = False
    elif change == "status":
        comparison = replace(
            comparison, cases=(replace(comparison.cases[0], status="tie"),)
        )
    else:
        comparison = replace(
            comparison, cases=(replace(comparison.cases[0], score_delta=0),)
        )
    report = _gate(comparison, expected=("0",), **options)
    assert report.verdict == "fail"
    assert report.blockers[0].reason == "invalid"
