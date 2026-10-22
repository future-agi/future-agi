import uuid
from types import SimpleNamespace

import pytest
from django.db import transaction
from rest_framework.test import APIClient
from structlog.testing import capture_logs

from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services import hosted_harness
from simulate.services.agent_setup import stored_agent
from simulate.services.hosted_harness import create_hosted_job, register_attempt
from simulate.tests.test_agent_setup_service import source_agent
from simulate.tests.test_hosted_harness_channels import _headers, _payload


@pytest.fixture
def definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="alk-sdk-agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="SDK-provisioned text agent (ALK ingestion).",
        organization=organization,
        workspace=workspace,
    )


def _job(kind):
    return SimpleNamespace(id=uuid.uuid4(), payload={"source": {"kind": kind}})


def _sent(agent=None):
    return {"agent": {"sdk_version": "0.2.3", "agent": agent or source_agent()}}


def _not_stored(logs):
    return [entry for entry in logs if entry["event"] == "harness_agent_not_stored"]


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["github", "archive"])
def test_a_repository_run_stores_the_agent_on_the_version_the_prompt_created(definition, kind):
    version = definition.create_version(description="You help.", commit_message="prompt", status="active")

    with capture_logs() as logs:
        hosted_harness._store_authored_agent(_job(kind), definition, _sent())

    assert stored_agent(version.id)["kind"] == "source"
    assert AgentVersion.objects.filter(agent_definition=definition).count() == 1
    assert _not_stored(logs) == []


@pytest.mark.django_db
def test_a_run_with_no_prompt_gets_one_active_version_with_an_empty_description(definition):
    hosted_harness._store_authored_agent(_job("github"), definition, _sent())

    version = AgentVersion.objects.get(agent_definition=definition)
    assert version.status == "active"
    assert version.description == ""
    assert version.configuration_snapshot["description"] == ""
    assert stored_agent(version.id)["name"] == "harbor_hotels"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("remote", _sent()),
        ("provider", _sent()),
        ("github", {}),
        ("github", {**_sent(), "agent_definition_id": str(uuid.uuid4())}),
    ],
    ids=["remote_source", "provider_source", "no_agent_sent", "run_bound_to_an_agent"],
)
def test_nothing_is_stored_or_logged_outside_a_repository_run_with_an_agent(definition, kind, payload):
    with capture_logs() as logs:
        hosted_harness._store_authored_agent(_job(kind), definition, payload)

    assert AgentVersion.objects.filter(agent_definition=definition).count() == 0
    assert AgentVersionSetup.all_objects.count() == 0
    assert _not_stored(logs) == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [{"agent": []}, {"agent": {"sdk_version": "0.2.3", "agent": "x"}}, {"agent": {"agent": source_agent()}}],
    ids=["agent_a_list", "agent_not_an_object", "sdk_version_missing"],
)
def test_a_malformed_agent_field_is_logged_and_leaves_no_version(definition, payload):
    with capture_logs() as logs:
        hosted_harness._store_authored_agent(_job("github"), definition, payload)

    assert [entry["code"] for entry in _not_stored(logs)] == ["agent_invalid"]
    assert AgentVersion.objects.filter(agent_definition=definition).count() == 0


@pytest.mark.django_db
def test_a_refused_agent_is_logged_by_code(definition):
    agent = source_agent(type="email")

    with capture_logs() as logs:
        hosted_harness._store_authored_agent(_job("archive"), definition, _sent(agent))

    assert [entry["code"] for entry in _not_stored(logs)] == ["agent_invalid"]
    assert AgentVersionSetup.all_objects.count() == 0


@pytest.mark.django_db
def test_any_other_error_is_logged_by_class_and_the_outer_transaction_survives(definition, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(hosted_harness, "store_agent", broken)

    with transaction.atomic():
        with capture_logs() as logs:
            hosted_harness._store_authored_agent(_job("github"), definition, _sent())
        assert AgentDefinition.objects.filter(id=definition.id).exists()

    assert [entry["code"] for entry in _not_stored(logs)] == ["RuntimeError"]
    assert AgentVersion.objects.filter(agent_definition=definition).count() == 0
    assert all("database went away" not in str(entry) for entry in logs)


GITHUB = {
    "kind": "github",
    "repository": "acme/agent",
    "ref": "main",
    "visibility": "public",
}


@pytest.mark.django_db
def test_a_provision_posted_with_an_agent_stores_it_through_the_url(organization, workspace):
    job, _ = create_hosted_job(
        organization,
        _payload(source=GITHUB, agent={"connector": "auto", "config": {}, "secret_refs": {}}),
        idempotency_key="authored-url",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")

    response = APIClient().post(
        f"/simulate/api/harness/attempts/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Authored",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "refund-request",
                    "name": "Customer",
                    "situation": "Asks for a refund",
                    "outcome": "Agent follows policy",
                }
            ],
            "agent": {"sdk_version": "0.2.3", "agent": source_agent()},
        },
        format="json",
        **_headers(capability),
    )

    assert response.status_code == 200, response.content
    job.refresh_from_db()
    agent = AgentDefinition.objects.get(id=job.run_test.agent_definition_id)
    version = AgentVersion.objects.get(agent_definition=agent)
    assert stored_agent(version.id)["name"] == "harbor_hotels"
