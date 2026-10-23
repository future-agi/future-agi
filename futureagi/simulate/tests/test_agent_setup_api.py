import uuid

import pytest
from rest_framework import status

from accounts.models import Organization
from simulate.models import AgentDefinition
from simulate.serializers.response import agent_version as version_serializers
from simulate.services.agent_setup import store_agent, store_setup_failure
from simulate.tests.test_agent_setup_service import source_agent


@pytest.fixture
def definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Harbor Hotels",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="SDK-provisioned text agent",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def version(definition):
    return definition.create_version(description="", commit_message="first", status="active")


def _agent_url(agent_id, version_id):
    return f"/simulate/agent-definitions/{agent_id}/versions/{version_id}/agent/"


def _detail_url(agent_id, version_id):
    return f"/simulate/agent-definitions/{agent_id}/versions/{version_id}/"


@pytest.mark.django_db
def test_get_agent_answers_the_stored_agent_without_its_raw_setup(auth_client, definition, version):
    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")

    response = auth_client.get(_agent_url(definition.id, version.id))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert sorted(body) == ["agent", "sdk_version", "setup_stale", "stored_at"]
    expected = {key: value for key, value in source_agent().items() if key != "extension"}
    assert body["agent"] == expected
    assert (body["sdk_version"], body["setup_stale"]) == ("0.2.3", False)
    assert body["stored_at"]


@pytest.mark.django_db
def test_get_agent_404s(auth_client, definition, version):
    other = definition.create_version(description="", commit_message="second", status="active")
    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")
    failed = definition.create_version(description="", commit_message="third", status="draft")
    store_setup_failure(agent_version_id=failed.id, code="invalid_key", message="x")

    cases = {
        _agent_url(uuid.uuid4(), version.id): "Agent definition not found",
        _agent_url(definition.id, uuid.uuid4()): "Agent version not found",
        _agent_url(definition.id, other.id): "Agent not stored for this version",
        _agent_url(definition.id, failed.id): "Agent not stored for this version",
    }
    for url, message in cases.items():
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND, url
        assert message in str(response.json()), url


@pytest.mark.django_db
def test_get_agent_of_another_organization_404s(auth_client, workspace):
    stranger = Organization.objects.create(name="Other Organization")
    foreign = AgentDefinition.no_workspace_objects.create(
        agent_name="Foreign",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="x",
        organization=stranger,
    )
    foreign_version = foreign.create_version(description="", commit_message="x", status="active")
    store_agent(agent_version_id=foreign_version.id, agent=source_agent(), sdk_version="0.2.3")

    response = auth_client.get(_agent_url(foreign.id, foreign_version.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_other_organization_404(auth_client, workspace):
    stranger = Organization.objects.create(name="Other Organization")
    # Filed under the caller's workspace so the workspace-scoped manager still returns it:
    # only the organization check can refuse it.
    foreign = AgentDefinition.no_workspace_objects.create(
        agent_name="Foreign",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="x",
        organization=stranger,
        workspace=workspace,
    )
    foreign_version = foreign.create_version(description="", commit_message="x", status="active")
    store_agent(agent_version_id=foreign_version.id, agent=source_agent(), sdk_version="0.2.3")

    response = auth_client.get(_agent_url(foreign.id, foreign_version.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "Agent definition not found" in str(response.json())


@pytest.mark.django_db
def test_version_detail_carries_agent_kind_from_the_summary(auth_client, definition, version):
    assert auth_client.get(_detail_url(definition.id, version.id)).json()["agent_kind"] is None

    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")
    newer = definition.create_version(description="", commit_message="second", status="active")

    assert auth_client.get(_detail_url(definition.id, version.id)).json()["agent_kind"] == "source"
    assert auth_client.get(_detail_url(definition.id, newer.id)).json()["agent_kind"] == "source"


@pytest.mark.django_db
def test_version_detail_answers_null_agent_kind_when_the_summary_raises(
    auth_client, definition, version, monkeypatch
):
    def broken(*args, **kwargs):
        raise RuntimeError("summary down")

    monkeypatch.setattr(version_serializers, "agent_summary", broken)

    response = auth_client.get(_detail_url(definition.id, version.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["agent_kind"] is None
    assert response.json()["id"] == str(version.id)
