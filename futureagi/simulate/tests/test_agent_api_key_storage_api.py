"""Database/API qualification for the final M3 schema; NOT_RUN locally."""

import pytest
from django.db import connections

from agentcc.services.credential_manager import mask_key
from integrations.services.credentials import CredentialManager
from simulate.models import AgentDefinition

pytestmark = pytest.mark.django_db


@pytest.fixture
def encrypted_agent(organization, workspace):
    agent = AgentDefinition(
        agent_name="Encrypted legacy agent",
        agent_type="text",
        inbound=True,
        description="No versions",
        organization=organization,
        workspace=workspace,
    )
    agent.set_api_key("api-patch-synthetic-marker")
    agent.save()
    return agent


def test_no_version_operations_patch_masks_and_preserves_storage(
    auth_client, encrypted_agent
):
    agent = encrypted_agent
    assert not agent.versions.exists()
    original = agent.encrypted_api_key
    response = auth_client.patch(
        f"/simulate/api/agent-definition-operations/{agent.pk}/",
        {"description": "Metadata only"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["api_key"] == mask_key("api-patch-synthetic-marker")
    assert "api-patch-synthetic-marker" not in response.content.decode()
    assert original not in response.content.decode()
    assert "encrypted_api_key" not in response.json()
    agent.refresh_from_db()
    assert agent.get_api_key() == "api-patch-synthetic-marker"
    assert agent.encrypted_api_key == original


def test_raw_cursor_storage_and_deferred_partial_save(encrypted_agent):
    agent = encrypted_agent
    connection = connections[agent._state.db]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT encrypted_api_key FROM simulate_agent_definition WHERE id = %s",
            [agent.pk],
        )
        stored = cursor.fetchone()[0]
        columns = connection.introspection.get_table_description(
            cursor, "simulate_agent_definition"
        )
    assert "api_key" not in {column.name for column in columns}
    assert "api-patch-synthetic-marker" not in stored
    assert (
        CredentialManager.decrypt(stored[5:].encode())["v"]
        == "api-patch-synthetic-marker"
    )
    deferred = AgentDefinition.objects.only("id", "description").get(pk=agent.pk)
    assert deferred.get_api_key() == "api-patch-synthetic-marker"
    deferred.description = "Metadata preserved"
    deferred.save(update_fields=["description"])
    deferred.set_api_key("enc::literal-client-marker")
    deferred.save(update_fields=["encrypted_api_key"])
    agent.refresh_from_db()
    assert agent.get_api_key() == "enc::literal-client-marker"
    assert agent.description == "Metadata preserved"


def test_rejected_storage_field_injection_and_masked_update(
    auth_client, encrypted_agent
):
    agent = encrypted_agent
    url = f"/simulate/api/agent-definition-operations/{agent.pk}/"
    response = auth_client.patch(
        url, {"encrypted_api_key": "injected-synthetic"}, format="json"
    )
    assert response.status_code == 400
    original = agent.encrypted_api_key
    response = auth_client.patch(
        url, {"api_key": mask_key(agent.get_api_key())}, format="json"
    )
    assert response.status_code == 200
    agent.refresh_from_db()
    assert agent.encrypted_api_key == original


def test_prefix_input_is_encrypted_in_both_stores(auth_client, encrypted_agent):
    agent = encrypted_agent
    version = agent.create_version(
        description="", commit_message="Credential input", status="active"
    )
    response = auth_client.patch(
        f"/simulate/api/agent-definition-operations/{agent.pk}/",
        {"provider": "vapi", "api_key": "enc::raw-client-marker"},
        format="json",
    )
    assert response.status_code == 200
    agent.refresh_from_db()
    version.refresh_from_db()
    assert agent.get_api_key() == "enc::raw-client-marker"
    assert version.credentials.get_api_key() == "enc::raw-client-marker"
    assert agent.encrypted_api_key != "enc::raw-client-marker"
    assert version.credentials.api_key != "enc::raw-client-marker"
