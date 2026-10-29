"""Observability asked for when an Environment is created, applied by its first provision."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import QuerySet
from rest_framework.test import APIClient
from structlog.testing import capture_logs

from accounts.models.workspace import Workspace
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HostedHarnessJob,
    HostedHarnessScenario,
    TestExecution,
)
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services import hosted_harness
from simulate.services.hosted_harness import (
    create_hosted_job,
    create_selected_harness_run,
    provision_scenarios,
    register_attempt,
)
from simulate.tests.test_agent_setup_service import source_agent
from simulate.tests.test_harness_provider import _v1_payload
from simulate.tests.test_hosted_harness_channels import _headers, _payload
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.observability_provider import ObservabilityProvider
from tracer.models.project import Project
from tracer.serializers.observability_provider import ObservabilityProviderSerializer
from tracer.services import observability_switch

BASE = "/simulate/api/harness/attempts"
GITHUB = {
    "kind": "github",
    "repository": "acme/agent",
    "ref": "main",
    "visibility": "public",
}
NOT_ENABLED = "harness_observability_not_enabled"
NOT_SAVED = "harness_observability_refusal_not_saved"


def _job(organization, workspace, key, *, metadata=None, drop_metadata=False):
    payload = _payload(
        source=GITHUB,
        metadata=metadata or {},
        agent={"connector": "auto", "config": {}, "secret_refs": {}},
    )
    if drop_metadata:
        del payload["metadata"]
    job, _ = create_hosted_job(
        organization, payload, idempotency_key=key, workspace=workspace
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    return job, capability


def _provision(capability, *, agent=None, **extra):
    body = {
        "operation": "provision",
        "name": "Connected agent",
        "modality": "text",
        "personas": [
            {
                "scenario_key": "refund-request",
                "name": "Customer",
                "situation": "Asks for a refund",
                "outcome": "Agent follows policy",
            }
        ],
    }
    if agent is not None:
        body["agent"] = {"sdk_version": "0.2.3", "agent": agent}
    body.update(extra)
    return APIClient().post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        body,
        format="json",
        **_headers(capability),
    )


def _definition(job):
    job.refresh_from_db()
    return AgentDefinition.objects.get(id=job.run_test.agent_definition_id)


def _stored_refusal(job):
    metadata = HostedHarnessJob.objects.get(id=job.id).payload.get("metadata") or {}
    return metadata.get("observability_refusal")


def _events(logs, name):
    return [entry for entry in logs if entry["event"] == name]


def _database_error_name():
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT 1/0")
    except DatabaseError as exc:
        return type(exc).__name__
    raise AssertionError("the database accepted a division by zero")


def _divide_by_zero(*args, **kwargs):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1/0")


def _spy():
    return patch.object(
        hosted_harness,
        "set_agent_observability",
        wraps=hosted_harness.set_agent_observability,
    )


def _complete(environment):
    environment.refresh_from_db()
    payload = dict(environment.payload)
    metadata = dict(payload.get("metadata") or {})
    metadata["authoring_object_key"] = "harness/environments/authored.tar.gz"
    payload["metadata"] = metadata
    environment.payload = payload
    environment.state = environment.State.COMPLETED
    environment.current_stage = "completed"
    environment.save(update_fields=["payload", "state", "current_stage", "updated_at"])


@pytest.mark.django_db
def test_runs_of_one_environment_share_its_agent(user, workspace):
    environment, _ = create_hosted_job(
        user.organization,
        _v1_payload(scenario_count=2),
        idempotency_key="shared-agent-environment",
        workspace=workspace,
    )
    attempt = register_attempt(
        environment.id, endpoint_base_url="https://harness.example.test"
    ).attempt
    provision_scenarios(
        attempt,
        {
            "operation": "provision",
            "name": "Shared agent suite",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": key,
                    "name": key,
                    "role": "customer",
                    "situation": "A situation",
                    "outcome": "An outcome",
                    "persona": {"name": key},
                }
                for key in ("scenario-a", "scenario-b")
            ],
        },
    )
    _complete(environment)
    agents = AgentDefinition.objects.count()

    first, _ = create_selected_harness_run(
        environment,
        scenario_keys=["scenario-a"],
        trials=1,
        idempotency_key="shared-run-one",
    )
    second, _ = create_selected_harness_run(
        environment,
        scenario_keys=["scenario-b"],
        trials=1,
        idempotency_key="shared-run-two",
    )

    environment.refresh_from_db()
    expected = environment.run_test.agent_definition_id
    assert expected is not None
    for run in (first, second):
        run.refresh_from_db()
        assert run.run_test_id == environment.run_test_id
        assert (
            TestExecution.objects.get(id=run.test_execution_id).agent_definition_id
            == expected
        )
    assert AgentDefinition.objects.count() == agents


@pytest.mark.django_db
def test_environment_create_request_turns_observability_on_after_the_store(
    organization, workspace, auth_client
):
    """The key asked for on the create screen reaches the stored agent's own switch."""
    prompt = "You are a refunds agent. Be brief."
    job, capability = _job(
        organization,
        workspace,
        "obs-on",
        metadata={"observability_enabled": True},
    )

    with capture_logs() as logs:
        response = _provision(
            capability,
            agent=source_agent(instrumentation="opentelemetry"),
            agent_prompt=prompt,
        )

    assert response.status_code == 200, response.content
    assert _events(logs, NOT_ENABLED) == []
    assert _stored_refusal(job) is None
    agent = _definition(job)
    row = ObservabilityProvider.no_workspace_objects.get(
        id=agent.observability_provider_id
    )
    assert row.enabled is True
    assert row.provider == "others"
    assert row.project.user is None
    assert job.workspace_id == workspace.id
    assert row.organization_id == job.organization_id == organization.id
    assert row.workspace_id == job.workspace_id
    assert row.project.organization_id == job.organization_id
    assert row.project.workspace_id == job.workspace_id
    version = AgentVersion.objects.get(agent_definition=agent)
    assert version.configuration_snapshot["observability_enabled"] is True
    assert version.configuration_snapshot["description"] == prompt
    assert version.description == prompt

    # The agent page's save for a code agent keeps the switch on.
    saved = auth_client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        {
            "commit_message": "observability",
            "observability_enabled": True,
            "description": "A changed prompt.",
        },
        format="json",
    )
    assert saved.status_code == 201, saved.content
    assert ObservabilityProvider.no_workspace_objects.get(id=row.id).enabled is True


@pytest.mark.django_db
def test_observability_switch_is_pinned_to_the_jobs_non_default_workspace(
    user, organization, workspace
):
    # A missing workspace falls back to the default one, so only a second workspace tells them apart.
    other = Workspace.objects.create(
        name="Second workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    assert other.id != workspace.id
    job, capability = _job(
        organization,
        other,
        "obs-on-other-workspace",
        metadata={"observability_enabled": True},
    )
    # A real provision request runs with no workspace context.
    clear_workspace_context()

    response = _provision(
        capability,
        agent=source_agent(instrumentation="opentelemetry"),
        agent_prompt="You are a refunds agent. Be brief.",
    )

    assert response.status_code == 200, response.content
    job.refresh_from_db()
    assert job.workspace_id == other.id
    # The default manager hides rows outside the default workspace, so read through the unfiltered one.
    definition = AgentDefinition.no_workspace_objects.get(
        id=job.run_test.agent_definition_id
    )
    row = ObservabilityProvider.no_workspace_objects.get(
        id=definition.observability_provider_id
    )
    assert row.enabled is True
    assert row.workspace_id == job.workspace_id == other.id
    assert row.project.workspace_id == other.id
    assert row.workspace_id != workspace.id
    assert row.project.workspace_id != workspace.id


@pytest.mark.django_db
def test_environment_create_request_with_no_prompt_keeps_the_empty_description(
    organization, workspace
):
    job, capability = _job(
        organization,
        workspace,
        "obs-no-prompt",
        metadata={"observability_enabled": True},
    )

    response = _provision(capability, agent=source_agent(instrumentation="traceai"))

    assert response.status_code == 200, response.content
    version = AgentVersion.objects.get(agent_definition=_definition(job))
    assert version.description == ""
    assert version.configuration_snapshot["description"] == ""
    assert version.configuration_snapshot["observability_enabled"] is True


@pytest.mark.django_db
def test_a_code_without_instrumentation_leaves_observability_off_and_says_why(
    organization, workspace
):
    job, capability = _job(
        organization, workspace, "obs-none", metadata={"observability_enabled": True}
    )

    with capture_logs() as logs:
        response = _provision(capability, agent=source_agent(instrumentation="none"))

    assert response.status_code == 200, response.content
    agent = _definition(job)
    assert _events(logs, NOT_ENABLED) == [
        {
            "event": NOT_ENABLED,
            "log_level": "warning",
            "job_id": str(job.id),
            "agent_definition_id": str(agent.id),
            "code": "instrumentation_missing",
        }
    ]
    assert agent.observability_provider_id is None
    assert ObservabilityProvider.no_workspace_objects.count() == 0
    assert AgentVersionSetup.all_objects.count() == 1
    assert _stored_refusal(job) == {
        "code": "instrumentation_missing",
        "details": {
            "instrumentation": [
                observability_switch.PREREQUISITES["instrumentation"].message
            ]
        },
    }


@pytest.mark.django_db
def test_a_run_made_from_an_environment_with_a_refusal_does_not_carry_it(
    organization, workspace
):
    job, capability = _job(
        organization, workspace, "obs-run", metadata={"observability_enabled": True}
    )
    response = _provision(capability, agent=source_agent(instrumentation="none"))
    assert response.status_code == 200, response.content
    _complete(job)
    assert _stored_refusal(job) is not None

    run, _ = create_selected_harness_run(
        job,
        scenario_keys=["refund-request"],
        trials=1,
        idempotency_key="obs-run-one",
    )

    assert "observability_refusal" not in run.payload["metadata"]
    assert _stored_refusal(job) is not None


@pytest.mark.django_db
def test_a_database_error_while_creating_the_provider_leaves_nothing_behind(
    organization, workspace
):
    job, capability = _job(
        organization,
        workspace,
        "obs-db-error",
        metadata={"observability_enabled": True},
    )

    with (
        patch.object(ObservabilityProviderSerializer, "save", _divide_by_zero),
        capture_logs() as logs,
    ):
        response = _provision(capability, agent=source_agent(instrumentation="traceai"))

    assert response.status_code == 200, response.content
    agent = _definition(job)
    assert [entry["code"] for entry in _events(logs, NOT_ENABLED)] == [
        "ObservabilityProjectError"
    ]
    assert _stored_refusal(job) == {"code": "ObservabilityProjectError", "details": {}}
    assert not Project.no_workspace_objects.filter(name=agent.agent_name).exists()
    assert ObservabilityProvider.no_workspace_objects.count() == 0
    assert agent.observability_provider_id is None
    assert AgentVersionSetup.all_objects.count() == 1
    assert HostedHarnessScenario.no_workspace_objects.filter(job=job).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("failing_write", ["definition_save", "snapshot_update"])
def test_a_database_error_outside_the_provider_insert_leaves_nothing_behind(
    organization, workspace, failing_write
):
    job, capability = _job(
        organization,
        workspace,
        "obs-db-error-write",
        metadata={"observability_enabled": True},
    )
    real_save = AgentDefinition.save
    real_update = QuerySet.update

    def save(self, *args, **kwargs):
        if self.observability_provider_id is not None:
            _divide_by_zero()
        return real_save(self, *args, **kwargs)

    def update(self, **kwargs):
        if (
            self.model is AgentVersion
            and (kwargs.get("configuration_snapshot") or {}).get(
                "observability_enabled"
            )
            is True
        ):
            _divide_by_zero()
        return real_update(self, **kwargs)

    target = (
        patch.object(AgentDefinition, "save", save)
        if failing_write == "definition_save"
        else patch.object(QuerySet, "update", update)
    )
    with target, capture_logs() as logs:
        response = _provision(capability, agent=source_agent(instrumentation="traceai"))

    assert response.status_code == 200, response.content
    agent = _definition(job)
    name = _database_error_name()
    assert [entry["code"] for entry in _events(logs, NOT_ENABLED)] == [name]
    assert _stored_refusal(job) == {"code": name, "details": {}}
    assert not Project.no_workspace_objects.filter(name=agent.agent_name).exists()
    assert ObservabilityProvider.no_workspace_objects.count() == 0
    assert agent.observability_provider_id is None
    snapshot = AgentVersion.objects.get(agent_definition=agent).configuration_snapshot
    assert snapshot.get("observability_enabled") is not True
    assert AgentVersionSetup.all_objects.count() == 1
    assert HostedHarnessScenario.no_workspace_objects.filter(job=job).count() == 1


@pytest.mark.django_db
def test_an_agent_the_store_refused_never_reaches_the_switch(organization, workspace):
    job, capability = _job(
        organization,
        workspace,
        "obs-unstored",
        metadata={"observability_enabled": True},
    )

    with _spy() as spy, capture_logs() as logs:
        response = _provision(capability, agent=source_agent(type="email"))

    assert response.status_code == 200, response.content
    spy.assert_not_called()
    assert [entry["code"] for entry in _events(logs, NOT_ENABLED)] == [
        "agent_not_stored"
    ]
    assert _stored_refusal(job) == {"code": "agent_not_stored", "details": {}}


@pytest.mark.django_db
def test_a_provision_that_sends_no_agent_never_reaches_the_switch(
    organization, workspace
):
    job, capability = _job(
        organization,
        workspace,
        "obs-no-agent",
        metadata={"observability_enabled": True},
    )

    with _spy() as spy, capture_logs() as logs:
        response = _provision(capability)

    assert response.status_code == 200, response.content
    spy.assert_not_called()
    agent = _definition(job)
    assert _events(logs, NOT_ENABLED) == [
        {
            "event": NOT_ENABLED,
            "log_level": "warning",
            "job_id": str(job.id),
            "agent_definition_id": str(agent.id),
            "code": "agent_not_stored",
        }
    ]
    assert _stored_refusal(job) == {"code": "agent_not_stored", "details": {}}
    assert agent.observability_provider_id is None
    assert ObservabilityProvider.no_workspace_objects.count() == 0
    assert HostedHarnessScenario.no_workspace_objects.filter(job=job).count() == 1


@pytest.mark.django_db
def test_an_unexpected_error_in_the_switch_never_fails_the_provision(
    organization, workspace
):
    job, capability = _job(
        organization,
        workspace,
        "obs-unexpected",
        metadata={"observability_enabled": True},
    )

    with (
        patch.object(
            hosted_harness, "set_agent_observability", side_effect=RuntimeError("boom")
        ),
        capture_logs() as logs,
    ):
        response = _provision(capability, agent=source_agent(instrumentation="traceai"))

    assert response.status_code == 200, response.content
    agent = _definition(job)
    events = _events(logs, NOT_ENABLED)
    assert len(events) == 1
    assert isinstance(events[0].pop("exc_info"), RuntimeError)
    assert events == [
        {
            "event": NOT_ENABLED,
            "log_level": "warning",
            "job_id": str(job.id),
            "agent_definition_id": str(agent.id),
            "code": "RuntimeError",
        }
    ]
    assert _stored_refusal(job) == {"code": "RuntimeError", "details": {}}
    assert agent.observability_provider_id is None
    assert ObservabilityProvider.no_workspace_objects.count() == 0
    assert HostedHarnessScenario.no_workspace_objects.filter(job=job).count() == 1


@pytest.mark.django_db
def test_a_run_bound_to_an_existing_agent_never_reaches_the_switch(
    organization, workspace
):
    existing = AgentDefinition.objects.create(
        agent_name="existing-agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="An agent made on the agent page.",
        organization=organization,
        workspace=workspace,
    )
    job, capability = _job(
        organization, workspace, "obs-bound", metadata={"observability_enabled": True}
    )

    with _spy() as spy, capture_logs() as logs:
        response = _provision(
            capability,
            agent=source_agent(instrumentation="traceai"),
            agent_definition_id=str(existing.id),
        )

    assert response.status_code == 200, response.content
    spy.assert_not_called()
    assert [entry["code"] for entry in _events(logs, NOT_ENABLED)] == ["bound"]
    assert _stored_refusal(job) == {"code": "bound", "details": {}}


@pytest.mark.django_db
def test_a_refusal_that_cannot_be_saved_never_fails_the_provision(
    organization, workspace
):
    job, capability = _job(
        organization, workspace, "obs-unsaved", metadata={"observability_enabled": True}
    )
    real_save = HostedHarnessJob.save

    def save(self, *args, **kwargs):
        if kwargs.get("update_fields") == ["payload", "updated_at"] and (
            "observability_refusal" in (self.payload.get("metadata") or {})
        ):
            _divide_by_zero()
        return real_save(self, *args, **kwargs)

    with (
        patch.object(HostedHarnessJob, "save", save),
        capture_logs() as logs,
    ):
        response = _provision(capability, agent=source_agent(instrumentation="none"))

    assert response.status_code == 200, response.content
    assert _events(logs, NOT_SAVED) == [
        {
            "event": NOT_SAVED,
            "log_level": "error",
            "job_id": str(job.id),
            "code": _database_error_name(),
            "exc_info": True,
        }
    ]
    assert _stored_refusal(job) is None
    assert HostedHarnessScenario.no_workspace_objects.filter(job=job).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "metadata",
    [{}, {"observability_enabled": "yes"}, {"observability_enabled": 1}],
    ids=["absent", "yes", "one"],
)
def test_only_the_boolean_true_asks_for_observability(
    organization, workspace, metadata
):
    job, capability = _job(organization, workspace, "obs-not-asked", metadata=metadata)

    with _spy() as spy, capture_logs() as logs:
        response = _provision(capability, agent=source_agent(instrumentation="none"))

    assert response.status_code == 200, response.content
    spy.assert_not_called()
    assert _events(logs, NOT_ENABLED) == []
    assert _stored_refusal(job) is None
    assert _definition(job).observability_provider_id is None


@pytest.mark.django_db
def test_a_job_with_no_metadata_provisions_as_before(organization, workspace):
    job, capability = _job(
        organization, workspace, "obs-no-metadata", drop_metadata=True
    )
    assert "metadata" not in job.payload

    with _spy() as spy, capture_logs() as logs:
        response = _provision(capability, agent=source_agent(instrumentation="none"))

    assert response.status_code == 200, response.content
    spy.assert_not_called()
    assert _events(logs, NOT_ENABLED) == []
    job.refresh_from_db()
    assert "metadata" not in job.payload
    assert job.run_test_id is not None


def _stored_agent_without_a_prompt(organization, workspace, key):
    job, capability = _job(
        organization,
        workspace,
        key,
        metadata={"observability_enabled": True},
    )
    response = _provision(
        capability, agent=source_agent(instrumentation="opentelemetry")
    )
    assert response.status_code == 200, response.content
    return _definition(job)


@pytest.mark.django_db
def test_a_code_agent_saved_from_the_agent_page_keeps_its_empty_description(
    organization, workspace, auth_client
):
    agent = _stored_agent_without_a_prompt(organization, workspace, "obs-page-save")
    row_id = agent.observability_provider_id

    # The body the agent page sends for a code agent that has no prompt.
    saved = auth_client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        {
            "commit_message": "Turn observability on",
            "observability_enabled": True,
            "description": "",
        },
        format="json",
    )

    assert saved.status_code == 201, saved.content
    versions = AgentVersion.objects.filter(agent_definition=agent)
    newest = versions.order_by("-version_number").first()
    assert versions.count() == 2
    assert newest.commit_message == "Turn observability on"
    assert newest.configuration_snapshot["description"] == ""
    assert newest.description == ""
    assert ObservabilityProvider.no_workspace_objects.get(id=row_id).enabled is True


@pytest.mark.django_db
def test_a_save_without_a_description_copies_the_placeholder_into_the_new_version(
    organization, workspace, auth_client
):
    """Pins why the page must send an empty description: leaving it out keeps the placeholder."""
    agent = _stored_agent_without_a_prompt(organization, workspace, "obs-page-omit")
    placeholder = agent.description
    assert placeholder.startswith("SDK-provisioned")

    saved = auth_client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        {"commit_message": "Turn observability on", "observability_enabled": True},
        format="json",
    )

    assert saved.status_code == 201, saved.content
    newest = (
        AgentVersion.objects.filter(agent_definition=agent)
        .order_by("-version_number")
        .first()
    )
    assert newest.configuration_snapshot["description"] == placeholder


def _credentials_of(version):
    from simulate.models.agent_definition import ProviderCredentials

    return ProviderCredentials.objects.filter(agent_version=version)


def _livekit_page_body(**overrides):
    # What the agent page sends for a LiveKit agent when nothing is filled in.
    body = {
        "commit_message": "Save from the agent page",
        "description": "",
        "provider": "livekit",
        "livekit_url": "",
        "livekit_api_key": "",
        "livekit_api_secret": "",
        "livekit_agent_name": "",
        "livekit_config_json": {},
        # The page loads the version's value, which is the default with no row.
        "livekit_max_concurrency": settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY,
    }
    body.update(overrides)
    return body


def _newest_version(agent):
    return (
        AgentVersion.objects.filter(agent_definition=agent)
        .order_by("-version_number")
        .first()
    )


@pytest.mark.django_db
def test_a_code_agent_saved_with_every_credential_blank_gets_no_credentials_row(
    organization, workspace, auth_client
):
    agent = _stored_agent_without_a_prompt(organization, workspace, "creds-blank")

    saved = auth_client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        _livekit_page_body(),
        format="json",
    )

    assert saved.status_code == 201, saved.content
    newest = _newest_version(agent)
    assert newest.version_number == 2
    assert not _credentials_of(newest).exists()


@pytest.mark.django_db
def test_a_code_agent_saved_with_a_livekit_url_and_key_gets_a_credentials_row(
    organization, workspace, auth_client
):
    agent = _stored_agent_without_a_prompt(organization, workspace, "creds-filled")

    saved = auth_client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        _livekit_page_body(
            livekit_url="wss://agent.example.com", livekit_api_key="lk-key"
        ),
        format="json",
    )

    assert saved.status_code == 201, saved.content
    row = _credentials_of(_newest_version(agent)).get()
    assert row.server_url == "wss://agent.example.com"
    assert row.get_api_key() == "lk-key"


@pytest.mark.django_db
def test_a_platform_agent_saved_with_every_credential_blank_still_gets_a_credentials_row(
    organization, workspace, auth_client
):
    agent = AgentDefinition.objects.create(
        agent_name="Support line",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        description="Support",
        provider="livekit",
        organization=organization,
        workspace=workspace,
    )
    agent.create_version(description="", commit_message="connect", status="active")

    saved = auth_client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        _livekit_page_body(),
        format="json",
    )

    assert saved.status_code == 201, saved.content
    assert _credentials_of(_newest_version(agent)).exists()


@pytest.mark.django_db
def test_a_vapi_code_agent_saved_with_a_key_and_assistant_id_gets_a_credentials_row(
    organization, workspace, auth_client
):
    agent = _stored_agent_without_a_prompt(organization, workspace, "creds-vapi")

    saved = auth_client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        {
            "commit_message": "Save from the agent page",
            "description": "",
            "provider": "vapi",
            "api_key": "vapi-key",
            "assistant_id": "asst-1",
        },
        format="json",
    )

    assert saved.status_code == 201, saved.content
    row = _credentials_of(_newest_version(agent)).get()
    assert row.get_api_key() == "vapi-key"
    assert row.assistant_id == "asst-1"


@pytest.mark.django_db
def test_a_livekit_code_agent_with_an_assistant_id_but_blank_livekit_fields_gets_no_row(
    organization, workspace, auth_client
):
    agent = _stored_agent_without_a_prompt(organization, workspace, "creds-lk-asst")

    saved = auth_client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        _livekit_page_body(api_key="stale-key", assistant_id="asst-1"),
        format="json",
    )

    assert saved.status_code == 201, saved.content
    assert not _credentials_of(_newest_version(agent)).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "provider", ["livekit", "vapi"], ids=["livekit", "vapi"]
)
def test_a_code_agent_saved_with_a_changed_max_concurrency_keeps_it(
    organization, workspace, auth_client, provider
):
    agent = _stored_agent_without_a_prompt(
        organization, workspace, f"creds-conc-{provider}"
    )

    saved = auth_client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        _livekit_page_body(provider=provider, livekit_max_concurrency=10),
        format="json",
    )

    assert saved.status_code == 201, saved.content
    newest = _newest_version(agent)
    assert newest.configuration_snapshot["livekit_max_concurrency"] == 10
    assert _credentials_of(newest).get().max_concurrency == 10


@pytest.mark.django_db
def test_a_code_agent_saved_with_a_room_config_keeps_it(
    organization, workspace, auth_client
):
    agent = _stored_agent_without_a_prompt(organization, workspace, "creds-room")

    saved = auth_client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        _livekit_page_body(livekit_config_json={"room": "support"}),
        format="json",
    )

    assert saved.status_code == 201, saved.content
    newest = _newest_version(agent)
    assert newest.configuration_snapshot["livekit_config_json"] == {"room": "support"}
    assert _credentials_of(newest).get().config_json == {"room": "support"}
