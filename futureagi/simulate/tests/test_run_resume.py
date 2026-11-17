"""Resuming an interrupted harness Run: only the calls that did not finish run again, as a
new attempt of the same Run, through ``POST /simulate/test-executions/{id}/rerun-calls/``
with ``unfinished_only``.

Each test starts from a Run of two calls (scenario-a, scenario-b). Call A has a result and
call B has none, unless a test changes that.
"""

import pytest
from django.utils import timezone

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    CallExecution,
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessReceipt,
    TestExecution,
)
from simulate.services.hosted_harness import (
    RERUN_EXECUTION_KEYS,
    project_terminal_job_onto_run,
)
from simulate.services.hosted_harness_ingestion import _backfill_missing_receipts
from simulate.services.scoring_status import settle_run

# The in-run re-run's fixtures: a finished two-call Run, its calls, and the workflow mocks.
from simulate.tests.test_rerun_calls_in_run import (  # noqa: F401
    calls,
    finished_run,
    workflow,
)

# The summary ALK reports for a call that ran.
CALL = {
    "started_at": "2026-11-04T09:00:00.000Z",
    "ended_at": "2026-11-04T09:00:15.000Z",
    "duration_ms": 15000,
    "turns": 4,
    "transcript_artifact": None,
    "recording_artifacts": [],
}


def _result(run, call, *, status="passed", body=None):
    """A result receipt as ALK sends it; by default a call that ran and passed."""
    execution = HostedHarnessExecution.no_workspace_objects.select_related(
        "source_scenario"
    ).get(job=run, call_execution=call)
    return HostedHarnessReceipt.no_workspace_objects.create(
        job=run,
        attempt=run.attempts.order_by("attempt_number").first(),
        scenario=execution.source_scenario,
        execution=execution,
        attempt_number=1,
        digest="sha256:" + "0" * 64,
        status=status,
        body={"status": status, "call": CALL, "failure": None, **(body or {})},
    )


def _failure(domain, code):
    return {"domain": domain, "stage": "running", "code": code, "message": code}


@pytest.fixture
def interrupted_run(finished_run, calls):  # noqa: F811
    """The Run stopped before call B finished: A has its result, B has none."""
    first, _ = calls
    _result(finished_run, first)
    HostedHarnessJob.no_workspace_objects.filter(id=finished_run.id).update(
        state=HostedHarnessJob.State.FAILED, current_stage="failed"
    )
    TestExecution.no_workspace_objects.filter(id=finished_run.test_execution_id).update(
        status=TestExecution.ExecutionStatus.FAILED
    )
    finished_run.refresh_from_db()
    return finished_run


def _resume(auth_client, run, **extra):
    return auth_client.post(
        f"/simulate/test-executions/{run.test_execution_id}/rerun-calls/",
        {"rerun_type": "call_and_eval", "unfinished_only": True, **extra},
        format="json",
    )


def _read(auth_client, run):
    response = auth_client.get(
        f"/simulate/v3/test-executions/{run.test_execution_id}/calls/"
    )
    assert response.status_code == 200, response.content
    body = response.json()
    return (body.get("result") or body)["execution"]["resume"]


def _status(call):
    return CallExecution.no_workspace_objects.values_list("status", flat=True).get(
        id=call.id
    )


@pytest.mark.django_db
def test_resume_runs_only_the_calls_that_did_not_finish(
    auth_client,
    interrupted_run,
    calls,  # noqa: F811
    workflow,  # noqa: F811
):
    finished, unfinished = calls
    _, start = workflow

    response = _resume(auth_client, interrupted_run)

    assert response.status_code == 202, response.content
    assert response.json()["successful_reruns"] == [str(unfinished.id)]
    assert _status(finished) == CallExecution.CallStatus.COMPLETED
    assert _status(unfinished) == CallExecution.CallStatus.PENDING
    interrupted_run.refresh_from_db()
    # The same Run, queued again, scoped to the one unfinished call.
    assert interrupted_run.state == HostedHarnessJob.State.QUEUED
    keys = interrupted_run.payload["metadata"][RERUN_EXECUTION_KEYS]
    assert keys == [
        HostedHarnessExecution.no_workspace_objects.get(
            job=interrupted_run, call_execution=unfinished
        ).execution_key
    ]
    start.assert_called_once()
    # The finished call's result is untouched.
    assert HostedHarnessReceipt.no_workspace_objects.filter(
        job=interrupted_run, execution__call_execution=finished
    ).exists()


@pytest.mark.django_db
def test_resume_is_recorded_on_the_run_and_shown_on_its_read(
    auth_client,
    user,
    interrupted_run,
    calls,  # noqa: F811
    workflow,  # noqa: F811
):
    before = _read(auth_client, interrupted_run)

    assert _resume(auth_client, interrupted_run).status_code == 202

    assert before == {
        "resumable": True,
        "finished": 1,
        "unfinished": 1,
        "history": [],
    }
    [entry] = _read(auth_client, interrupted_run)["history"]
    assert (entry["by"], entry["calls"]) == (str(user.id), 1)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "body", "resumed"),
    [
        # A call is finished only when it was charged. The plan's limit refused this one
        # before it started, so it runs again once the plan is raised.
        (
            "errored",
            {"call": None, "failure": _failure("platform_sync", "usage_exhausted")},
            2,
        ),
        # The platform's own failure ended the call, so it was not charged.
        ("errored", {"failure": _failure("infrastructure", "call_failed")}, 2),
        # The simulator's setup broke before the call started: a billable domain, but no
        # call, so nothing was charged.
        (
            "errored",
            {"call": None, "failure": _failure("simulator", "setup_crashed")},
            2,
        ),
        # A call that ran and was charged is finished, whoever broke it.
        ("errored", {"failure": _failure("simulator", "evidence_missing")}, 1),
        ("errored", {"failure": _failure("agent", "target_agent_failed")}, 1),
        ("passed", {}, 1),
    ],
    ids=[
        "usage-limit",
        "platform-failure",
        "never-started",
        "simulator-failure",
        "agent-failure",
        "passed",
    ],
)
def test_which_results_count_as_finished(
    auth_client,
    finished_run,  # noqa: F811
    calls,  # noqa: F811
    workflow,  # noqa: F811
    status,
    body,
    resumed,
):
    first, _ = calls
    _result(finished_run, first, status=status, body=body)
    HostedHarnessJob.no_workspace_objects.filter(id=finished_run.id).update(
        state=HostedHarnessJob.State.CANCELED
    )
    TestExecution.no_workspace_objects.filter(id=finished_run.test_execution_id).update(
        status=TestExecution.ExecutionStatus.CANCELLED
    )
    assert _read(auth_client, finished_run)["unfinished"] == resumed

    response = _resume(auth_client, finished_run)

    assert response.status_code == 202, response.content
    assert response.json()["success_count"] == resumed


@pytest.mark.django_db
def test_a_call_the_manifest_backfill_marked_skipped_did_not_finish(
    auth_client,
    interrupted_run,
    calls,  # noqa: F811
    workflow,  # noqa: F811
):
    # The attempt's manifest landed before call B ran, so B got a skipped result.
    _, unfinished = calls
    _backfill_missing_receipts(
        interrupted_run.attempts.order_by("attempt_number").first()
    )
    assert (
        HostedHarnessReceipt.no_workspace_objects.get(
            job=interrupted_run, execution__call_execution=unfinished
        ).status
        == "skipped"
    )

    response = _resume(auth_client, interrupted_run)

    assert response.status_code == 202, response.content
    assert response.json()["successful_reruns"] == [str(unfinished.id)]


@pytest.mark.django_db
def test_a_resume_that_was_itself_cut_short_can_resume_again(
    auth_client,
    interrupted_run,
    calls,  # noqa: F811
    workflow,  # noqa: F811
):
    _, unfinished = calls
    assert _resume(auth_client, interrupted_run).status_code == 202
    # The resume's own attempt fails before call B runs, as the gateway ends it. The job
    # keeps the re-run's keys, so the run settles from its calls instead of failing.
    job = HostedHarnessJob.no_workspace_objects.get(id=interrupted_run.id)
    job.state = HostedHarnessJob.State.FAILED
    job.terminal_at = timezone.now()
    job.failure = _failure("infrastructure", "sandbox_lost")
    job.save(update_fields=["state", "terminal_at", "failure", "updated_at"])
    project_terminal_job_onto_run(job, job.terminal_at)
    run = TestExecution.no_workspace_objects.get(id=interrupted_run.test_execution_id)

    # While it grades, it is neither offered nor taken.
    assert run.status == TestExecution.ExecutionStatus.EVALUATING
    assert _read(auth_client, interrupted_run)["resumable"] is False
    grading = _resume(auth_client, interrupted_run)
    assert grading.status_code == 409, grading.content
    assert grading.json()["error"] == "rerun_in_progress"

    settle_run(run.id)
    run.refresh_from_db()
    assert run.status == TestExecution.ExecutionStatus.COMPLETED
    assert _read(auth_client, interrupted_run)["unfinished"] == 1

    response = _resume(auth_client, interrupted_run)

    assert response.status_code == 202, response.content
    assert response.json()["successful_reruns"] == [str(unfinished.id)]


@pytest.mark.django_db
def test_a_run_that_completed_cannot_be_resumed(
    auth_client,
    finished_run,  # noqa: F811
    calls,  # noqa: F811
    workflow,  # noqa: F811
):
    _, start = workflow

    response = _resume(auth_client, finished_run)

    assert response.status_code == 409, response.content
    assert response.json()["error"] == "run_not_interrupted"
    start.assert_not_called()
    assert _read(auth_client, finished_run)["resumable"] is False


@pytest.mark.django_db
def test_a_run_whose_calls_all_finished_has_nothing_to_resume(
    auth_client,
    interrupted_run,
    calls,  # noqa: F811
    workflow,  # noqa: F811
):
    _, second = calls
    _result(interrupted_run, second)

    response = _resume(auth_client, interrupted_run)

    assert response.status_code == 409, response.content
    assert response.json()["error"] == "nothing_to_resume"
    assert _read(auth_client, interrupted_run)["resumable"] is False


@pytest.mark.django_db
def test_a_run_whose_pinned_version_is_gone_is_refused_with_the_reason(
    auth_client,
    organization,
    workspace,
    interrupted_run,
    calls,  # noqa: F811
    workflow,  # noqa: F811
):
    _, start = workflow
    agent = AgentDefinition.objects.create(
        agent_name="Resume agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    pinned = agent.create_version(
        description="Pinned",
        commit_message="Pinned",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    HostedHarnessJob.no_workspace_objects.filter(id=interrupted_run.id).update(
        agent_version=pinned
    )
    # The version the run used was deleted after the run stopped.
    AgentVersion.all_objects.filter(id=pinned.id).update(deleted=True)

    response = _resume(auth_client, interrupted_run)

    assert response.status_code == 409, response.content
    assert response.json()["error"] == "resume_version_missing"
    assert "agent version" in response.json()["message"]
    start.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "extra",
    [
        {"select_all": True},
        {"rerun_type": "eval_only"},
        # A resume re-runs calls inside the run, which takes no fresh environment.
        {"environment_values": {"API_TOKEN": "fresh"}},
    ],
    ids=["with-select-all", "eval-only", "with-environment-values"],
)
def test_unfinished_only_takes_no_other_selection(
    auth_client,
    interrupted_run,
    calls,  # noqa: F811
    workflow,  # noqa: F811
    extra,
):
    response = _resume(auth_client, interrupted_run, **extra)

    assert response.status_code == 400, response.content
