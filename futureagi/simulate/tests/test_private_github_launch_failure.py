"""An existing environment built from a private GitHub repository, on a deployment that
no longer reads private repositories through the platform GitHub App.

Its run, rerun, add-scenarios and build jobs are refused when the launch acquires the
source, before any attempt exists. The job then fails at once with the refusal's code
and the user's domain, so the run and the build say what happened, and the launch is
not tried again: the next try would be refused the same way. Every other launch
failure is recorded as before. A failed run sends its failure code only when the run
page has words for it; any other code, which a guest can set, is not sent.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from simulate.models import HostedHarnessJob, TestExecution
from simulate.models.run_test import RunTest
from simulate.services import github_app_tenancy
from simulate.services.hosted_harness import HostedHarnessError, create_hosted_job
from simulate.temporal.constants import QUEUE_RUNNER
from simulate.temporal.types.hosted_harness_gateway import (
    HostedHarnessGatewayInput,
    HostedHarnessLaunchFailureInput,
    HostedHarnessLaunchOutput,
    HostedHarnessLaunchRecoveryOutput,
)
from simulate.temporal.workflows.hosted_harness_gateway_workflow import (
    HostedHarnessGatewayWorkflow,
)

GATEWAY = "simulate.services.hosted_harness_gateway"
BASE_URL = "https://platform.example.test"
CALLS_URL = "/simulate/v3/test-executions/{}/calls/"
REFUSED = "github_private_unavailable"
ENVIRONMENT_SENTENCE = (
    "Private GitHub repositories are not available on Future AGI cloud. "
    "Upload the code to keep using this environment."
)


def _private_payload() -> dict:
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "github",
            "repository": "future-agi/reference-agent",
            "ref": "main",
            "commit_sha": "a" * 40,
            "visibility": "private",
            "installation_id": "installation-1",
        },
        "agent": {"connector": "vapi", "config": {}, "secret_refs": {}},
        "scenario_count": 1,
        "seed": 1,
        "runtime": {
            "isolation": "dedicated_vm",
            "cpu_units": 2,
            "memory_mb": 4096,
            "parallelism": 1,
            "concurrency_weight": 1,
            "max_duration_seconds": 600,
            "network_policy": "live",
        },
        "security": {
            "untrusted_source": True,
            "read_only_source": True,
            "allow_privileged": False,
            "allow_host_runtime_control": False,
            "allowed_egress_domains": ["agent.example.com"],
        },
        "retry": {
            "max_infrastructure_attempts": 2,
            "initial_backoff_seconds": 1,
            "max_backoff_seconds": 15,
            "retryable_domains": ["infrastructure", "connectivity"],
        },
        "artifacts": {
            "level": "full",
            "retention_days": 30,
            "allow_bundle_download": True,
            "max_artifact_bytes": 1024,
        },
        "metadata": {},
    }


class _NoSandbox:
    """A sandbox provider the refused launch must never reach."""

    name = "e2b"
    runtime_name = "alk-hosted-v1"
    runtime_digest = ""
    max_egress_domains = 20
    create_timeout_seconds = 60

    def create(self, *_args, **_kwargs):
        raise AssertionError("a refused launch created a sandbox")


@pytest.fixture
def activities(monkeypatch):
    """The launch activities, their database work run inline on the test's thread."""
    from simulate.temporal.activities import hosted_harness_gateway as module

    async def _inline(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(module, "_run_db", _inline)
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    monkeypatch.setattr(f"{GATEWAY}.get_sandbox_provider", lambda: _NoSandbox())
    return module


@pytest.fixture
def github(monkeypatch):
    """Records every App JWT signed and every request sent to GitHub."""
    asked: list[str] = []

    def encode(*_args, **_kwargs):
        asked.append("jwt")
        return "synthetic-app-jwt"

    def post(url, **_kwargs):
        asked.append(url)
        raise AssertionError("GitHub was asked for an installation token")

    monkeypatch.setattr(f"{GATEWAY}.jwt.encode", encode)
    monkeypatch.setattr(f"{GATEWAY}.requests.post", post)
    return asked


@pytest.fixture
def private_github_refused(settings, monkeypatch):
    """The platform App is configured and serves two organizations."""
    settings.CLOUD_DEPLOYMENT = ""
    settings.GITHUB_APP_ID = "12345"
    settings.GITHUB_APP_PRIVATE_KEY = "synthetic-app-private-key"
    monkeypatch.setattr(github_app_tenancy, "_live_org_ids", lambda: ["org-a", "org-b"])


def _launch_input(job) -> HostedHarnessGatewayInput:
    return HostedHarnessGatewayInput(
        job_id=str(job.id),
        endpoint_base_url=BASE_URL,
        max_infrastructure_attempts=2,
        initial_backoff_seconds=1,
        max_backoff_seconds=2,
    )


def _launch(activities, job):
    return asyncio.run(activities.launch_hosted_harness_job(_launch_input(job)))


def _record_exhausted(activities, job):
    return asyncio.run(
        activities.record_hosted_harness_launch_failure(
            HostedHarnessLaunchFailureInput(job_id=str(job.id))
        )
    )


def _run_of(job, organization, workspace) -> TestExecution:
    """Make ``job`` the job of a run that has not ended."""
    run_test = RunTest.no_workspace_objects.create(
        name="Private source run", organization=organization, workspace=workspace
    )
    execution = TestExecution.no_workspace_objects.create(
        run_test=run_test, status=TestExecution.ExecutionStatus.RUNNING
    )
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        run_test=run_test, test_execution=execution
    )
    return execution


# Queries under asyncio.run() use their own connection, which sees only committed rows.
@pytest.mark.django_db(transaction=True)
def test_a_refused_private_repository_fails_the_launch_at_once_with_its_reason(
    activities, github, private_github_refused, organization
):
    job, _ = create_hosted_job(
        organization, _private_payload(), idempotency_key="private-refused-launch"
    )

    with pytest.raises(ApplicationError) as raised:
        _launch(activities, job)

    # Not retried: another try would be refused the same way.
    assert raised.value.non_retryable is True
    assert raised.value.type == REFUSED
    assert github == []
    job.refresh_from_db()
    assert job.state == HostedHarnessJob.State.FAILED
    assert job.current_stage == "failed"
    assert job.terminal_at is not None
    assert job.failure == {
        "domain": "environment",
        "stage": "acquiring_source",
        "code": REFUSED,
        "message": ENVIRONMENT_SENTENCE,
    }
    assert not job.attempts.exists()

    # The workflow then records the launch failure; the reason stays.
    recovery = _record_exhausted(activities, job)

    assert recovery.state == HostedHarnessJob.State.FAILED
    assert recovery.attempt_id is None
    job.refresh_from_db()
    assert job.failure["code"] == REFUSED
    assert job.failure["domain"] == "environment"


@pytest.mark.django_db(transaction=True)
def test_a_refused_private_repository_run_says_why_it_failed(
    activities,
    github,
    private_github_refused,
    organization,
    workspace,
    auth_client,
):
    job, _ = create_hosted_job(
        organization,
        _private_payload(),
        idempotency_key="private-refused-run",
        workspace=workspace,
    )
    execution = _run_of(job, organization, workspace)

    with pytest.raises(ApplicationError):
        _launch(activities, job)

    execution.refresh_from_db()
    assert execution.status == TestExecution.ExecutionStatus.FAILED
    assert execution.error_reason == ENVIRONMENT_SENTENCE
    response = auth_client.get(CALLS_URL.format(execution.id))
    assert response.status_code == 200, response.content
    reported = response.json()["execution"]
    assert reported["status"] == TestExecution.ExecutionStatus.FAILED
    # The typed reason only; a failure message can end with a guest's output.
    assert reported["failure"] == {"domain": "environment", "code": REFUSED}


@pytest.mark.django_db(transaction=True)
def test_another_launch_failure_is_still_retried_and_recorded_as_before(
    activities, monkeypatch, organization
):
    job, _ = create_hosted_job(
        organization, _private_payload(), idempotency_key="clone-failed-launch"
    )

    def clone_failed(_self, _job):
        raise HostedHarnessError(
            "github_clone_failed",
            "fatal: unable to access the repository",
            status_code=502,
            retryable=True,
        )

    monkeypatch.setattr(f"{GATEWAY}.HostedSourceAcquirer.acquire", clone_failed)

    # Raised as it was, so the activity's retry policy still applies.
    with pytest.raises(HostedHarnessError) as raised:
        _launch(activities, job)

    assert raised.value.code == "github_clone_failed"
    job.refresh_from_db()
    assert job.state not in {
        HostedHarnessJob.State.FAILED,
        HostedHarnessJob.State.COMPLETED,
        HostedHarnessJob.State.CANCELED,
    }

    recovery = _record_exhausted(activities, job)

    assert recovery.state == HostedHarnessJob.State.FAILED
    job.refresh_from_db()
    assert job.failure == {
        "domain": "infrastructure",
        "stage": "acquiring_source",
        "code": "hosted_launch_failed",
        "message": "The hosted run could not launch after its bounded retries; no call was attempted.",
    }


def _run_of_ended_job(organization, workspace, *, status, job_state, failure=None):
    """A run with ``status`` whose job ended in ``job_state``, recording ``failure``."""
    run_test = RunTest.no_workspace_objects.create(
        name="Ended run", organization=organization, workspace=workspace
    )
    execution = TestExecution.no_workspace_objects.create(
        run_test=run_test, status=status
    )
    HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest="digest",
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=1,
        payload={},
        run_test=run_test,
        test_execution=execution,
        state=job_state,
        failure=failure,
    )
    return execution


@pytest.mark.django_db
def test_a_finished_run_reports_no_failure_reason(auth_client, organization, workspace):
    execution = _run_of_ended_job(
        organization,
        workspace,
        status=TestExecution.ExecutionStatus.COMPLETED,
        job_state=HostedHarnessJob.State.COMPLETED,
    )

    response = auth_client.get(CALLS_URL.format(execution.id))

    assert response.status_code == 200, response.content
    assert response.json()["execution"].get("failure") is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "code",
    ["__proto__", "constructor", "agent_said_so", ["github_private_unavailable"]],
)
def test_a_failure_code_the_run_page_does_not_name_is_not_sent(
    code, auth_client, organization, workspace
):
    execution = _run_of_ended_job(
        organization,
        workspace,
        status=TestExecution.ExecutionStatus.FAILED,
        job_state=HostedHarnessJob.State.FAILED,
        # As a guest's terminal event records it: a code and words of its own.
        failure={
            "domain": "agent",
            "stage": "running",
            "code": code,
            "message": "agent_said_so: the guest's own words",
        },
    )

    response = auth_client.get(CALLS_URL.format(execution.id))

    assert response.status_code == 200, response.content
    reported = response.json()["execution"]
    assert reported["status"] == TestExecution.ExecutionStatus.FAILED
    assert reported.get("failure") is None
    assert "agent_said_so" not in response.content.decode()
    assert "the guest's own words" not in response.content.decode()


class _RefusedLaunchActivities:
    def __init__(self) -> None:
        self.launch_calls = 0
        self.recorded_job_id = None

    @activity.defn(name="launch_hosted_harness_job")
    async def launch(
        self, _input: HostedHarnessGatewayInput
    ) -> HostedHarnessLaunchOutput:
        self.launch_calls += 1
        raise ApplicationError(ENVIRONMENT_SENTENCE, type=REFUSED, non_retryable=True)

    @activity.defn(name="record_hosted_harness_launch_failure")
    async def record(
        self, input: HostedHarnessLaunchFailureInput
    ) -> HostedHarnessLaunchRecoveryOutput:
        self.recorded_job_id = input.job_id
        return HostedHarnessLaunchRecoveryOutput(state="failed")


@pytest.mark.asyncio
async def test_a_launch_refused_for_good_is_tried_once_then_recorded(
    temporal_test_server_options,
):
    activities = _RefusedLaunchActivities()
    async with await WorkflowEnvironment.start_time_skipping(
        **temporal_test_server_options
    ) as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_RUNNER,
            workflows=[HostedHarnessGatewayWorkflow],
            activities=[activities.launch, activities.record],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                HostedHarnessGatewayWorkflow.run,
                HostedHarnessGatewayInput(
                    job_id="job-private-refused",
                    endpoint_base_url="https://example.test",
                    max_infrastructure_attempts=3,
                    initial_backoff_seconds=1,
                    max_backoff_seconds=2,
                ),
                id=f"hosted-private-refused-{uuid.uuid4()}",
                task_queue=QUEUE_RUNNER,
            )

    assert result.state == "failed"
    assert activities.launch_calls == 1
    assert activities.recorded_job_id == "job-private-refused"
