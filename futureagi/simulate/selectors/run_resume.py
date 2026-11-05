"""What an interrupted Run left unfinished, and whether it can be resumed."""

from dataclasses import dataclass
from typing import Any

from simulate.models import (
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessReceipt,
    TestExecution,
)
from simulate.services.harness_usage import is_billable_receipt

# A Run that ended before every call finished: stopped, over its limit, or lost to a crash.
INTERRUPTED_JOB_STATES = (
    HostedHarnessJob.State.FAILED,
    HostedHarnessJob.State.CANCELED,
)
INTERRUPTED_RUN_STATUSES = (
    TestExecution.ExecutionStatus.FAILED,
    TestExecution.ExecutionStatus.CANCELLED,
)
# Where a Run's resumes are recorded, on the run itself: the job's payload goes to the sandbox.
RESUMES = "resumes"


@dataclass(frozen=True)
class ResumeState:
    resumable: bool
    finished: int | None
    unfinished: int | None
    history: list[dict[str, Any]]


def unfinished_executions(job: HostedHarnessJob) -> list[HostedHarnessExecution]:
    """The calls of a Run that did not finish: those it was not charged for. That is no
    result yet, a skipped one, a call that never started, or one the platform's own failure
    ended. Billing reads the same rule, so a resume never runs a call that was charged."""
    finished = {
        execution_id
        for execution_id, status, call, failure_domain in (
            HostedHarnessReceipt.no_workspace_objects.filter(
                job=job, execution__isnull=False
            ).values_list(
                "execution_id", "status", "body__call", "body__failure__domain"
            )
        )
        if is_billable_receipt(status, call, failure_domain)
    }
    return [
        execution
        for execution in HostedHarnessExecution.no_workspace_objects.filter(
            job=job
        ).order_by("execution_key")
        if execution.id not in finished
    ]


def resumable_job(test_execution: TestExecution) -> HostedHarnessJob | None:
    """The saved-environment Run behind this run, if it is one a user can resume. A fix's
    check run belongs to its fix, and only the fix may run it again."""
    return (
        HostedHarnessJob.no_workspace_objects.filter(
            test_execution_id=test_execution.id,
            environment__isnull=False,
            fix_run__isnull=True,
        )
        .select_related(
            "agent_version", "environment_version", "suite_version", "assembled_bundle"
        )
        .first()
    )


def resume_state(test_execution: TestExecution) -> ResumeState:
    """How far an interrupted run got, and its resumes. Counts are only read for a run that
    ended interrupted, so a finished or live run costs no queries here."""
    history = list((test_execution.execution_metadata or {}).get(RESUMES) or [])
    if test_execution.status not in INTERRUPTED_RUN_STATUSES:
        return ResumeState(False, None, None, history)
    job = resumable_job(test_execution)
    if job is None or job.state not in INTERRUPTED_JOB_STATES:
        return ResumeState(False, None, None, history)
    total = HostedHarnessExecution.no_workspace_objects.filter(job=job).count()
    unfinished = len(unfinished_executions(job))
    return ResumeState(unfinished > 0, total - unfinished, unfinished, history)
