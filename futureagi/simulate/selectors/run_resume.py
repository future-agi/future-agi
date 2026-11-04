"""What an interrupted Run left unfinished, and whether it can be resumed."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simulate.models import (
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessReceipt,
    TestExecution,
)
from simulate.pydantic_schemas.agent_fix import FailureOwner
from simulate.selectors.agent_fix import check_failure

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


def _cut_short_by_us(failure: Any) -> bool:
    """A result the platform's own failure ended, such as a timeout or an infrastructure
    error. A result the agent or the user's environment broke is finished."""
    if not isinstance(failure, Mapping) or failure.get("domain") == "agent":
        return False
    found = check_failure(failure)
    return found is not None and found.owner == FailureOwner.OURS


def unfinished_executions(job: HostedHarnessJob) -> list[HostedHarnessExecution]:
    """The calls of a Run that did not finish: no result yet, or one the platform cut short."""
    results = dict(
        HostedHarnessReceipt.no_workspace_objects.filter(
            job=job, execution__isnull=False
        ).values_list("execution_id", "body")
    )
    return [
        execution
        for execution in HostedHarnessExecution.no_workspace_objects.filter(
            job=job
        ).order_by("execution_key")
        if execution.id not in results
        or _cut_short_by_us((results[execution.id] or {}).get("failure"))
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
