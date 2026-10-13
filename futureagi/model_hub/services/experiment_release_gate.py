"""Deterministic release decisions over a complete, compatible comparison."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from model_hub.services.experiment_analysis import (
    ASSESSED,
    DUPLICATE,
    ERROR,
    IMPROVED,
    INVALID,
    MISSING,
    NOT_ASSESSED,
    REGRESSED,
    TIE,
    UNKNOWN,
    UNSUPPORTED,
    ComparisonSummary,
    _finite_number,
    _validate_options,
)


@dataclass(frozen=True, order=True)
class GateCaseIdentity:
    example_id: str
    trial_number: int


@dataclass(frozen=True)
class ReleaseGatePolicy:
    max_regressions: int = 0
    critical_cases: tuple[GateCaseIdentity, ...] = ()


@dataclass(frozen=True)
class ReleaseGateBlocker:
    example_id: str | None
    trial_number: int | None
    reason: Literal[
        "missing",
        "duplicate",
        "invalid",
        "error",
        "unknown",
        "unsupported",
        "not_assessed",
        "critical_regression",
        "critical_threshold",
        "regression_limit_exceeded",
        "empty_plan",
    ]


@dataclass(frozen=True)
class ReleaseGateReport:
    verdict: Literal["pass", "fail"]
    expected_count: int
    comparable_count: int
    regression_count: int
    policy: ReleaseGatePolicy
    passing_score: float | None
    higher_is_better: bool
    blockers: tuple[ReleaseGateBlocker, ...]


def _identities(values: Iterable[GateCaseIdentity]) -> set[GateCaseIdentity]:
    result = set()
    for identity in values:
        if (
            not isinstance(identity, GateCaseIdentity)
            or not isinstance(identity.example_id, str)
            or not identity.example_id.strip()
            or type(identity.trial_number) is not int
            or identity.trial_number < 1
        ):
            raise ValueError("case identity requires an example ID and positive trial")
        if identity in result:
            raise ValueError("case identities must be unique")
        result.add(identity)
    return result


def evaluate_release_gate(
    comparison: ComparisonSummary,
    *,
    expected_cases: Iterable[GateCaseIdentity],
    policy: ReleaseGatePolicy,
    passing_score: float | None = None,
    higher_is_better: bool = True,
) -> ReleaseGateReport:
    """Require finite assessed evidence on both sides of every planned trial.

    The authorized caller establishes dataset/plan/selected-binding compatibility
    and supplies the full plan, including identities absent on both sides. This
    function never trusts aggregate averages as a release decision.
    """
    _validate_options(passing_score, higher_is_better)
    if type(policy.max_regressions) is not int or policy.max_regressions < 0:
        raise ValueError("max_regressions must be a nonnegative integer")
    expected = _identities(expected_cases)
    critical = _identities(policy.critical_cases)
    if not critical <= expected:
        raise ValueError("critical cases must belong to the pinned run plan")
    groups = defaultdict(list)
    for case in comparison.cases:
        identity = GateCaseIdentity(case.example_id, case.trial_number)
        _identities((identity,))
        if identity not in expected:
            raise ValueError("comparison case is outside the pinned run plan")
        groups[identity].append(case)

    blockers = []
    regressions = []
    comparable = 0
    evidence_failures = {
        MISSING,
        DUPLICATE,
        INVALID,
        ERROR,
        UNKNOWN,
        UNSUPPORTED,
        NOT_ASSESSED,
    }
    for identity in sorted(expected):
        cases = groups[identity]
        if len(cases) != 1:
            reason = MISSING if not cases else DUPLICATE
            blockers.append(
                ReleaseGateBlocker(identity.example_id, identity.trial_number, reason)
            )
            continue
        case = cases[0]
        if not isinstance(case.status, str) or case.status not in {
            IMPROVED,
            REGRESSED,
            TIE,
        }:
            reason = (
                case.status
                if isinstance(case.status, str) and case.status in evidence_failures
                else INVALID
            )
            blockers.append(
                ReleaseGateBlocker(identity.example_id, identity.trial_number, reason)
            )
            continue
        before, after = (
            _finite_number(case.baseline_score),
            _finite_number(case.candidate_score),
        )
        delta = after - before if before is not None and after is not None else None
        expected_status = (
            TIE
            if delta == 0
            else IMPROVED
            if delta is not None and (delta > 0) == higher_is_better
            else REGRESSED
        )
        if (
            case.baseline_status != ASSESSED
            or case.candidate_status != ASSESSED
            or before is None
            or after is None
            or _finite_number(delta) is None
            or _finite_number(case.score_delta) is None
            or case.status != expected_status
            or case.score_delta != delta
        ):
            blockers.append(
                ReleaseGateBlocker(identity.example_id, identity.trial_number, INVALID)
            )
            continue
        comparable += 1
        if case.status == REGRESSED:
            regressions.append(identity)
        if identity in critical:
            if case.status == REGRESSED:
                blockers.append(
                    ReleaseGateBlocker(
                        identity.example_id,
                        identity.trial_number,
                        "critical_regression",
                    )
                )
            if passing_score is not None and not (
                after >= passing_score if higher_is_better else after <= passing_score
            ):
                blockers.append(
                    ReleaseGateBlocker(
                        identity.example_id, identity.trial_number, "critical_threshold"
                    )
                )
    if len(regressions) > policy.max_regressions:
        blockers.extend(
            ReleaseGateBlocker(
                identity.example_id, identity.trial_number, "regression_limit_exceeded"
            )
            for identity in regressions
        )
    if not expected:
        blockers.append(ReleaseGateBlocker(None, None, "empty_plan"))
    return ReleaseGateReport(
        verdict="fail" if blockers else "pass",
        expected_count=len(expected),
        comparable_count=comparable,
        regression_count=len(regressions),
        policy=ReleaseGatePolicy(policy.max_regressions, tuple(sorted(critical))),
        passing_score=passing_score,
        higher_is_better=higher_is_better,
        blockers=tuple(
            sorted(
                blockers,
                key=lambda item: (
                    item.example_id or "",
                    item.trial_number or 0,
                    item.reason,
                ),
            )
        ),
    )
