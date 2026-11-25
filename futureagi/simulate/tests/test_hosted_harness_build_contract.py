"""What a build receives and sends back: the frozen pin, subfolder's echo, and a
failure the platform set surviving the guest's own terminal event."""

from datetime import UTC, datetime

import pytest
from rest_framework.test import APIClient

from simulate.models import AgentDefinition, HostedHarnessAttempt, HostedHarnessJob
from simulate.services.hosted_harness import (
    BUILD_PIN_KEY,
    HostedHarnessError,
    _check_subfolder_echo,
    _fail_attempt_from_platform,
    create_hosted_job,
    frozen_build_pin,
    platform_set_failure,
    provision_scenarios,
    register_attempt,
)
from simulate.tests.test_hosted_harness_channels import (
    BASE,
    _headers,
    _payload,
    canonical_digest,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def definition(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="support",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


def _job(organization, key, **source):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key=key)
    if source:
        job.payload = {**job.payload, "source": {**job.payload["source"], **source}}
        job.save(update_fields=["payload", "updated_at"])
    return job


def _document(subfolder=None):
    extension = {"source": {"subfolder": subfolder}} if subfolder is not None else {}
    return {"agent": {"sdk_version": "0.2.3", "agent": {"extension": extension}}}


def test_the_first_attempt_freezes_the_pin_and_a_retry_keeps_it(
    organization, definition
):
    first = definition.create_version(description="p", commit_message="v1")
    second = definition.create_version(description="p2", commit_message="v2")
    job = _job(organization, "freeze-pin")
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(agent_version=first)

    register_attempt(job.id, endpoint_base_url="https://platform.example")
    job.refresh_from_db()
    assert job.payload["metadata"][BUILD_PIN_KEY] == str(first.id)
    assert frozen_build_pin(job) == first

    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(agent_version=second)
    register_attempt(job.id, endpoint_base_url="https://platform.example")
    job.refresh_from_db()

    assert job.current_attempt_number == 2
    assert frozen_build_pin(job) == first


def test_an_unpinned_environment_is_pinned_when_its_build_starts(
    organization, definition, settings
):
    settings.HARNESS_VERSIONING_ENABLED = True
    first = definition.create_version(
        description="p", commit_message="v1", status="active"
    )
    job = _job(organization, "pin-at-start")
    job.payload = {
        **job.payload,
        "metadata": {
            **job.payload.get("metadata", {}),
            "agent_definition_id": str(definition.id),
        },
    }
    job.save(update_fields=["payload", "updated_at"])
    assert job.agent_version_id is None

    register_attempt(job.id, endpoint_base_url="https://platform.example")
    job.refresh_from_db()

    assert job.agent_version_id == first.id
    assert frozen_build_pin(job) == first


def test_a_build_with_no_pin_is_not_frozen(organization):
    job = _job(organization, "no-pin")

    register_attempt(job.id, endpoint_base_url="https://platform.example")
    job.refresh_from_db()

    assert BUILD_PIN_KEY not in job.payload["metadata"]
    assert frozen_build_pin(job) is None


def test_a_matching_echo_passes_and_the_root_passes_without_a_subfolder(
    organization,
):
    attempt = register_attempt(
        _job(organization, "echo-ok", subfolder="agents/support").id,
        endpoint_base_url="https://platform.example",
    ).attempt
    _check_subfolder_echo(attempt, _document("agents/support"))

    root = register_attempt(
        _job(organization, "echo-root").id, endpoint_base_url="https://platform.example"
    ).attempt
    _check_subfolder_echo(root, _document())
    _check_subfolder_echo(root, {"agent": None})

    assert platform_set_failure(attempt) is None
    assert platform_set_failure(root) is None


@pytest.mark.parametrize("echoed", [None, "", "agents/billing"])
def test_a_missing_or_different_echo_fails_the_attempt(organization, echoed):
    attempt = register_attempt(
        _job(organization, f"echo-{echoed or 'none'}", subfolder="agents/support").id,
        endpoint_base_url="https://platform.example",
    ).attempt

    with pytest.raises(HostedHarnessError) as refused:
        _check_subfolder_echo(attempt, _document(echoed))

    assert refused.value.code == "subfolder_ignored"
    assert refused.value.status_code == 422
    attempt.refresh_from_db()
    failure = attempt.terminal_failure
    assert failure["domain"] == "infrastructure"
    assert failure["stage"] == "understanding_agent"
    assert failure["code"] == "subfolder_ignored"
    assert failure["retryable"] is False
    assert "agents/support" in failure["message"]
    assert platform_set_failure(attempt) == failure


def test_the_guest_terminal_event_does_not_replace_a_platform_failure(organization):
    job = _job(organization, "kept-terminal")
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    attempt = capability.attempt
    _fail_attempt_from_platform(
        attempt,
        stage="understanding_agent",
        code="subfolder_ignored",
        message="the harness read the repository root",
    )
    terminal_payload = {
        "stage": "failed",
        "reason": None,
        "failure": {
            "domain": "platform_sync",
            "stage": "registering_scenarios",
            "code": "scenario_provision_failed",
            "message": "422 from the platform",
        },
        "scenario_counts": {"passed": 0, "failed": 0, "errored": 0, "skipped": 0},
    }
    terminal = {
        "event_id": "terminal-1",
        "job_id": str(job.id),
        "attempt_id": str(attempt.id),
        "attempt_number": 1,
        "sequence": 1,
        "emitted_at": datetime.now(UTC).isoformat(),
        "stage": "failed",
        "type": "terminal",
        "payload": terminal_payload,
        "digest": canonical_digest(terminal_payload),
    }

    response = APIClient().post(
        f"{BASE}/{attempt.id}/events/",
        {"schema_version": "futureagi.harness-event.v1", "events": [terminal]},
        format="json",
        **_headers(capability),
    )

    assert response.status_code == 200, response.content
    attempt.refresh_from_db()
    assert attempt.terminal_event_received is True, response.json()
    assert attempt.state == HostedHarnessAttempt.State.FINALIZING
    assert attempt.terminal_failure["code"] == "subfolder_ignored"
    assert attempt.terminal_stage == "failed"


def test_provisioning_keeps_the_echo_failure_when_it_refuses(organization):
    attempt = register_attempt(
        _job(organization, "echo-provision", subfolder="agents/support").id,
        endpoint_base_url="https://platform.example",
    ).attempt

    with pytest.raises(HostedHarnessError) as refused:
        provision_scenarios(
            attempt,
            {"operation": "provision", "personas": [], **_document("agents/billing")},
        )

    assert refused.value.code == "subfolder_ignored"
    attempt.refresh_from_db()
    assert attempt.terminal_failure["code"] == "subfolder_ignored"
    assert platform_set_failure(attempt) is not None
