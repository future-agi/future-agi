"""Journey: a user stops a running Run mid-call, and every read of it says Cancelled.

Walks the transitions in order, through the same entry points the product uses: a Run of two cases is under way,
the harness skips one case, the user clicks Stop (``test-executions/{id}/cancel/``), the guest seals the call it
was on with ``user_canceled``, the job ends cancelled, and the calls list, its status chips, the analytics donut and
the runs list all read the stopped call as Cancelled while the skipped case stays an Error.
"""

from unittest.mock import patch

import pytest
from django.utils import timezone

from simulate.models import (
    CallExecution,
    HostedHarnessExecution,
    HostedHarnessJob,
    TestExecution,
)
from simulate.services.hosted_harness import (
    canonical_digest,
    create_hosted_job,
    create_selected_harness_run,
    project_terminal_job_onto_run,
    provision_scenarios,
    register_attempt,
)
from simulate.services.hosted_harness_ingestion import ingest_result_receipt
from simulate.tests.test_harness_provider import _v1_payload
from simulate.tests.test_rerun_calls_in_run import BASE_URL, _persona

STOPPED, SKIPPED = "case-stopped", "case-skipped"


@pytest.fixture
def running_run(user, workspace, settings, monkeypatch):
    """A Run of two cases under way: its attempt is live and the first case's call has started."""
    from tfc.settings import settings as app_settings

    settings.HARNESS_PUBLIC_BASE_URL = BASE_URL
    # Stop goes through Temporal, as deployed; the view reads the settings module directly.
    monkeypatch.setattr(app_settings, "TEMPORAL_TEST_EXECUTION_ENABLED", True)
    environment, _ = create_hosted_job(
        user.organization,
        _v1_payload(scenario_count=2),
        idempotency_key="environment",
        workspace=workspace,
    )
    authoring = register_attempt(environment.id, endpoint_base_url=BASE_URL).attempt
    provision_scenarios(
        authoring,
        {
            "operation": "provision",
            "name": "Stop journey suite",
            "modality": "text",
            "personas": [_persona(STOPPED), _persona(SKIPPED)],
        },
    )
    environment.refresh_from_db()
    environment.payload = {
        **environment.payload,
        "metadata": {
            **(environment.payload.get("metadata") or {}),
            "authoring_object_key": "harness/environments/authored.tar.gz",
        },
    }
    environment.state = HostedHarnessJob.State.COMPLETED
    environment.current_stage = "completed"
    environment.save(update_fields=["payload", "state", "current_stage", "updated_at"])

    run, _ = create_selected_harness_run(
        environment, scenario_keys=[STOPPED, SKIPPED], trials=1, idempotency_key="run"
    )
    attempt = register_attempt(run.id, endpoint_base_url=BASE_URL).attempt
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
        state=HostedHarnessJob.State.RUNNING, current_stage="running"
    )
    TestExecution.no_workspace_objects.filter(id=run.test_execution_id).update(
        status=TestExecution.ExecutionStatus.RUNNING
    )
    CallExecution.no_workspace_objects.filter(id=_call(run, STOPPED).id).update(
        status=CallExecution.CallStatus.ONGOING, started_at=timezone.now()
    )
    run.refresh_from_db()
    return run, attempt


def _execution(run, key):
    return HostedHarnessExecution.no_workspace_objects.select_related(
        "source_scenario", "call_execution"
    ).get(job=run, call_execution__call_metadata__harness_scenario_key=key)


def _call(run, key):
    return CallExecution.no_workspace_objects.get(
        id=_execution(run, key).call_execution_id
    )


def _seal(attempt, run, key, *, status, failure=None, call=None):
    """A result receipt as the guest signs and sends it."""
    execution = _execution(run, key)
    body = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(attempt.job_id),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "scenario_key": execution.execution_key,
        "scenario_id": str(execution.source_scenario.scenario_id),
        "scenario_attempt": 1,
        "world_index": None,
        "status": status,
        "sub_goals": [],
        "evaluations": [],
        "call": call,
        "failure": failure,
    }
    body["digest"] = canonical_digest(body)
    with (
        patch("simulate.services.harness_usage.replay_harness_usage"),
        patch("simulate.services.hosted_harness_ingestion.dispatch_worker_read"),
    ):
        ingest_result_receipt(attempt, body, replay_usage=False)


def _outcome_rows(auth_client, run):
    response = auth_client.get(
        f"/simulate/v3/test-executions/{run.test_execution_id}/calls/"
    )
    assert response.status_code == 200
    page = response.json()
    rows = {row["id"]: row["outcome"] for row in page["results"]}
    return rows, page["status_counts"]


@pytest.mark.django_db
def test_a_stopped_call_reads_cancelled_in_every_view_and_a_skipped_case_stays_an_error(
    auth_client, running_run
):
    run, attempt = running_run
    stopped, skipped = _call(run, STOPPED), _call(run, SKIPPED)

    # 1. The harness skips one case before its call starts: an Error, never Cancelled.
    _seal(attempt, run, SKIPPED, status="skipped")
    rows, counts = _outcome_rows(auth_client, run)
    assert (rows[str(stopped.id)], rows[str(skipped.id)]) == ("in_progress", "error")

    # 2. The user clicks Stop while the other call is under way.
    with patch(
        "simulate.temporal.client.cancel_hosted_harness_gateway_workflow"
    ) as signal:
        response = auth_client.post(
            f"/simulate/test-executions/{run.test_execution_id}/cancel/"
        )
    assert response.status_code == 200, response.content
    signal.assert_called_once_with(str(run.id))
    run.refresh_from_db()
    assert (run.cancel_reason, run.state) == (
        "user_canceled",
        HostedHarnessJob.State.CLEANING_UP,
    )

    # 3. The guest seals the call it was on: the call ran, then the user stopped it.
    _seal(
        attempt,
        run,
        STOPPED,
        status="errored",
        failure={
            "domain": "platform_sync",
            "stage": "running",
            "code": "user_canceled",
            "message": "stopped",
        },
        call={
            "started_at": "2026-11-16T09:00:00.000Z",
            "ended_at": "2026-11-16T09:00:20.000Z",
            "duration_ms": 20000,
            "turns": 2,
            "transcript_artifact": None,
        },
    )
    stopped.refresh_from_db()
    assert stopped.status == CallExecution.CallStatus.CANCELLED

    # 4. The job ends cancelled and the Run with it.
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
        state=HostedHarnessJob.State.CANCELED, terminal_at=timezone.now()
    )
    run.refresh_from_db()
    project_terminal_job_onto_run(run, timezone.now())
    TestExecution.no_workspace_objects.filter(id=run.test_execution_id).update(
        status=TestExecution.ExecutionStatus.CANCELLED
    )

    # 5. The calls list and its chips: Cancelled 1, Error 1 (the skipped case).
    rows, counts = _outcome_rows(auth_client, run)
    assert (rows[str(stopped.id)], rows[str(skipped.id)]) == ("cancelled", "error")
    assert (counts["cancelled"], counts["error"]) == (1, 1)

    # 6. The analytics donut has a Cancelled segment of one call.
    dashboard = auth_client.get(
        f"/simulate/v3/test-executions/{run.test_execution_id}/analytics/"
    ).json()["dashboard"]
    donut = next(
        item for item in dashboard["breakdowns"] if item["key"] == "goal_outcome"
    )
    segments = {segment["label"]: segment["count"] for segment in donut["segments"]}
    assert (segments["cancelled"], segments["error"]) == (1, 1)

    # 7. The runs list: the stopped call is counted Cancelled, not errored; the skipped case as skipped.
    listing = auth_client.get(
        f"/simulate/run-tests/{run.run_test_id}/executions/"
    ).json()
    row = next(
        item for item in listing["results"] if item["id"] == str(run.test_execution_id)
    )
    assert (
        row["status"],
        row["outcome_cancelled"],
        row["outcome_errored"],
        row["outcome_skipped"],
    ) == ("Cancelled", 1, 0, 1)


@pytest.mark.django_db
def test_the_cancel_seal_waits_on_a_call_in_flight_until_its_receipt_lands(
    running_run, monkeypatch, settings
):
    from simulate.services import hosted_harness_gateway as gateway_module
    from simulate.services.hosted_harness_gateway import HostedHarnessGateway

    run, attempt = running_run
    settings.HARNESS_CANCEL_SEAL_SECONDS = 10
    now = {"t": 0.0}
    monkeypatch.setattr(gateway_module.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(
        gateway_module.time,
        "sleep",
        lambda seconds: now.__setitem__("t", now["t"] + seconds),
    )
    gateway = object.__new__(HostedHarnessGateway)

    # One call under way and one still pending, neither sealed: the wait runs to its bound.
    gateway._await_guest_seal(attempt)
    assert now["t"] >= 10

    # Once both receipts land, a later stop has nothing to wait for.
    _seal(attempt, run, SKIPPED, status="skipped")
    _seal(
        attempt,
        run,
        STOPPED,
        status="errored",
        failure={
            "domain": "platform_sync",
            "stage": "running",
            "code": "user_canceled",
            "message": "stopped",
        },
    )
    from django.core.cache import cache

    cache.delete(f"hosted-cancel-seal:{attempt.id}")
    now["t"] = 0.0
    gateway._await_guest_seal(attempt)
    assert now["t"] == 0.0
