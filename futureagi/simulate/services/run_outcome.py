"""A run's verdict and pass rate: the one rule every run and summary payload reports, so no client re-derives it."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import TypedDict

from django.db.models import CharField, Count, Q, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce

from simulate.constants.run_outcome import OutcomeStatus
from simulate.models import HostedHarnessJob, HostedHarnessReceipt, TestExecution

_TERMINAL = frozenset(
    {
        TestExecution.ExecutionStatus.COMPLETED,
        TestExecution.ExecutionStatus.FAILED,
        TestExecution.ExecutionStatus.CANCELLED,
    }
)
# A call's own outcome when it ran with no pass or fail, whatever its receipt's status.
NO_SCORE = "no_score"


class OutcomeCounts(TypedDict):
    """A run's calls by outcome. Passed is scored passes only: a call with no score is counted apart."""

    passed: int
    failed: int
    errored: int
    skipped: int
    no_score: int


class ReceiptTally(TypedDict):
    """A hosted run's receipts: by receipt status, and how many of each status are calls with no score."""

    by_status: dict[str, int]
    no_score_by_status: dict[str, int]


class RunOutcome(TypedDict):
    # None for a run with no counts: no verdict was computed.
    outcome_status: OutcomeStatus | None
    # Passed over every call that ran to a verdict (passed, failed, errored, skipped), as a whole percent; None when
    # there is none or the run has no counts.
    pass_rate: int | None
    # The pass rate's base; None for a run with no counts.
    scored: int | None


class OutcomeFields(TypedDict):
    """The outcome counts a run payload carries (None for a run without receipts) and its verdict."""

    outcome_passed: int | None
    outcome_failed: int | None
    outcome_errored: int | None
    outcome_skipped: int | None
    outcome_no_score: int | None
    outcome_status: OutcomeStatus | None
    pass_rate: int | None
    scored: int | None


class CallStatusCounts(TypedDict):
    """A run's calls by lifecycle: completed, and failed or cancelled."""

    completed: int
    failed: int


_ZERO: OutcomeCounts = {
    "passed": 0,
    "failed": 0,
    "errored": 0,
    "skipped": 0,
    "no_score": 0,
}


def pass_rate(*, passed: int, base: int) -> int | None:
    """Passed over ``base``, rounded half up to a whole percent; None when the base is empty."""
    if not base:
        return None
    return (200 * passed + base) // (2 * base)


def run_outcome(*, status: str, counts: OutcomeCounts | None) -> RunOutcome:
    """A run's verdict and pass rate from its lifecycle status and its calls' outcomes.

    The rate is passed over passed, failed, errored and skipped; ``counts`` None gives no verdict and no rate."""
    if counts is None:
        return {"outcome_status": None, "pass_rate": None, "scored": None}
    lifecycle = status.lower()
    passed = counts["passed"]
    not_passed = counts["failed"] + counts["errored"] + counts["skipped"]
    completed = lifecycle == TestExecution.ExecutionStatus.COMPLETED
    verdict: OutcomeStatus
    if lifecycle not in _TERMINAL:
        verdict = "running"
    elif lifecycle == TestExecution.ExecutionStatus.CANCELLED:
        verdict = "cancelled"
    elif completed and not_passed == 0 and not passed and counts["no_score"]:
        verdict = "no_score"
    elif completed and not_passed == 0:
        verdict = "passed"
    else:
        verdict = "failed"
    base = passed + not_passed
    return {
        "outcome_status": verdict,
        "pass_rate": pass_rate(passed=passed, base=base),
        "scored": base,
    }


def call_counts(calls: CallStatusCounts) -> OutcomeCounts:
    """The counts of a run without receipts on the runs list: its completed calls as passed and its failed or
    cancelled calls as failed."""
    return {**_ZERO, "passed": calls["completed"], "failed": calls["failed"]}


def call_outcome_counts(outcomes: dict[str, int]) -> OutcomeCounts:
    """The counts of a run from its calls' result outcomes: an errored call reads as errored, an inconclusive one as
    skipped (neither passed nor scored), and calls still to run are left to the run's lifecycle."""
    return {
        "passed": outcomes.get("passed", 0),
        "failed": outcomes.get("failed", 0),
        "errored": outcomes.get("error", 0),
        "skipped": outcomes.get("inconclusive", 0),
        "no_score": outcomes.get(NO_SCORE, 0),
    }


def receipt_counts(tally: ReceiptTally) -> OutcomeCounts:
    """The counts a hosted run is judged by: each receipt by its status, except that a passed receipt whose call has
    no score is counted as no score rather than passed."""
    by_status = tally["by_status"]
    no_score = tally["no_score_by_status"]
    return {
        "passed": by_status.get("passed", 0) - no_score.get("passed", 0),
        "failed": by_status.get("failed", 0),
        "errored": by_status.get("errored", 0),
        "skipped": by_status.get("skipped", 0),
        "no_score": sum(no_score.values()),
    }


def outcome_fields(
    *,
    status: str,
    receipts: ReceiptTally | None,
    calls: CallStatusCounts | None,
) -> OutcomeFields:
    """A run payload's outcome counts and verdict.

    Hosted runs count ``receipts`` (``outcome_failed`` includes errored); others are judged by ``calls``, if given."""
    if receipts is not None:
        counts: OutcomeCounts | None = receipt_counts(receipts)
    elif calls is not None:
        counts = call_counts(calls)
    else:
        counts = None
    by_status = receipts["by_status"] if receipts is not None else None
    return {
        "outcome_passed": by_status.get("passed", 0) if by_status is not None else None,
        "outcome_failed": (
            by_status.get("failed", 0) + by_status.get("errored", 0)
            if by_status is not None
            else None
        ),
        "outcome_errored": (
            by_status.get("errored", 0) if by_status is not None else None
        ),
        "outcome_skipped": (
            by_status.get("skipped", 0) if by_status is not None else None
        ),
        "outcome_no_score": (
            sum(receipts["no_score_by_status"].values())
            if receipts is not None
            else None
        ),
        **run_outcome(status=status, counts=counts),
    }


def receipt_outcome_counts(
    job_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, ReceiptTally]:
    """Each job's receipts counted by status, and how many of each belong to a call with no score.

    Soft-deleted calls are not counted. ``job_ids`` must already be scoped to the caller."""
    job_ids = list(job_ids)
    tallies: dict[uuid.UUID, ReceiptTally] = {}
    if not job_ids:
        return tallies
    receipt_of_deleted_call = Q(execution__call_execution__deleted=True) | Q(
        execution__isnull=True, scenario__call_execution__deleted=True
    )
    for row in (
        HostedHarnessReceipt.no_workspace_objects.filter(job_id__in=job_ids)
        .exclude(receipt_of_deleted_call)
        .annotate(
            call_outcome=Coalesce(
                KeyTextTransform(
                    "harness_outcome_status", "execution__call_execution__call_metadata"
                ),
                Value(""),
                output_field=CharField(),
            )
        )
        .values("job_id", "status", "call_outcome")
        .annotate(count=Count("id"))
        .order_by()
    ):
        tally = tallies.setdefault(
            row["job_id"], {"by_status": {}, "no_score_by_status": {}}
        )
        status = row["status"]
        tally["by_status"][status] = tally["by_status"].get(status, 0) + row["count"]
        if row["call_outcome"] == NO_SCORE:
            tally["no_score_by_status"][status] = (
                tally["no_score_by_status"].get(status, 0) + row["count"]
            )
    return tallies


def hosted_receipt_outcomes(
    execution_ids: Iterable[uuid.UUID], *, organization_id: uuid.UUID
) -> dict[uuid.UUID, ReceiptTally]:
    """Each hosted run's receipt tally by its execution id; a run with no hosted job of the caller's organisation is
    absent. ``execution_ids`` must already be scoped to the caller's workspace."""
    jobs = dict(
        HostedHarnessJob.no_workspace_objects.filter(
            test_execution_id__in=list(execution_ids), organization_id=organization_id
        ).values_list("test_execution_id", "id")
    )
    tallies = receipt_outcome_counts(jobs.values())
    empty: ReceiptTally = {"by_status": {}, "no_score_by_status": {}}
    return {
        execution_id: tallies.get(job_id, empty)
        for execution_id, job_id in jobs.items()
    }
