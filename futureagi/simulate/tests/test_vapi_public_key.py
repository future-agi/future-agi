"""Saving the Vapi public key of an agent that has authentication on.

Database/API qualification; every key below is made up. The key is stored
encrypted in ProviderCredentials.api_secret (a column Vapi rows use for nothing
else), is never returned, and never enters a version snapshot.
"""

import json
from pathlib import Path

import pytest
from rest_framework import status

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from simulate.utils.snapshot_projection import scrub_snapshot_secrets

pytestmark = pytest.mark.django_db

KEY = "made-up-vapi-public-key-0001"
OTHER_KEY = "made-up-vapi-public-key-0002"
PRIVATE_KEY = "made-up-vapi-private-key-0001"
OPERATIONS = "/simulate/api/agent-definition-operations/"
AGENTS = "/simulate/agent-definitions/"
ROOT = Path(__file__).resolve().parents[1]


def resolve_vapi_public_key_for_version(version):
    # Imported where a test uses it, so this module collects on any tree.
    from simulate.services.agent_definition import (
        resolve_vapi_public_key_for_version as resolve,
    )

    return resolve(version)


def has_vapi_public_key(credentials):
    from simulate.services.agent_definition import has_vapi_public_key as has_key

    return has_key(credentials)


def _agent(organization, workspace, name="Vapi agent"):
    return AgentDefinition.no_workspace_objects.create(
        agent_name=name,
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12345678901",
        inbound=True,
        description="Vapi agent",
        provider="vapi",
        assistant_id="made-up-assistant",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


def _credentials(version):
    row = ProviderCredentials(
        agent_version=version,
        provider_type=ProviderCredentials.ProviderType.VAPI,
        assistant_id="made-up-assistant",
        api_key=PRIVATE_KEY,
    )
    row.save()
    return row


@pytest.fixture
def agent(organization, workspace):
    return _agent(organization, workspace)


@pytest.fixture
def version(agent):
    created = agent.create_version(
        description="First", commit_message="First", status="active"
    )
    _credentials(created)
    return created


def _row(version):
    return ProviderCredentials.objects.get(agent_version=version)


def _patch(client, agent, body):
    return client.patch(f"{OPERATIONS}{agent.pk}/", body, format="json")


def _new_version(client, agent, body=None):
    payload = {"commit_message": "Edit", "provider": "vapi"}
    payload.update(body or {})
    return client.post(f"{AGENTS}{agent.pk}/versions/create/", payload, format="json")


def _newest(agent):
    return (
        AgentVersion.no_workspace_objects.filter(agent_definition=agent)
        .order_by("-version_number")
        .first()
    )


def _no_secret(response, *secrets):
    body = response.content.decode()
    for secret in secrets:
        assert secret not in body
    assert '"vapi_public_key"' not in body
    assert '"api_secret"' not in body


def test_save_stores_the_key_encrypted_in_api_secret(auth_client, agent, version):
    response = _patch(auth_client, agent, {"vapi_public_key": KEY})

    assert response.status_code == status.HTTP_200_OK, response.content
    row = _row(version)
    assert row.api_secret.startswith("enc::")
    assert KEY not in row.api_secret
    assert row.get_api_secret() == KEY
    # The private key and the assistant id are untouched.
    assert row.get_api_key() == PRIVATE_KEY
    assert row.assistant_id == "made-up-assistant"
    # Saving the key says the assistant has control authentication on.
    assert row.config_json["monitorPlan"]["controlAuthenticationEnabled"] is True
    assert "public_api_key" not in row.config_json


def test_a_key_that_starts_with_the_storage_prefix_is_still_encrypted(
    auth_client, agent, version
):
    response = _patch(auth_client, agent, {"vapi_public_key": "enc::made-up"})

    assert response.status_code == status.HTTP_200_OK, response.content
    row = _row(version)
    assert row.get_api_secret() == "enc::made-up"
    assert row.api_secret != "enc::made-up"


def test_the_answer_says_a_key_is_saved_and_never_returns_it(
    auth_client, agent, version
):
    before = _patch(auth_client, agent, {"description": "Unrelated"})
    assert before.json()["has_vapi_public_key"] is False

    response = _patch(auth_client, agent, {"vapi_public_key": KEY})

    assert response.json()["has_vapi_public_key"] is True
    _no_secret(response, KEY)
    detail = auth_client.get(f"{AGENTS}{agent.pk}/")
    assert detail.status_code == status.HTTP_200_OK
    cipher = _row(version).api_secret
    assert cipher
    _no_secret(detail, KEY, cipher)
    shown = auth_client.get(f"{AGENTS}{agent.pk}/versions/{version.pk}/")
    assert shown.status_code == status.HTTP_200_OK
    _no_secret(shown, KEY, cipher)
    assert shown.json()["configuration_snapshot"]["has_vapi_public_key"] is True


def test_a_blank_or_missing_key_keeps_the_saved_key(auth_client, agent, version):
    _patch(auth_client, agent, {"vapi_public_key": KEY})
    saved = _row(version).api_secret

    for body in ({"vapi_public_key": ""}, {"description": "No key sent"}):
        response = _patch(auth_client, agent, body)
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["has_vapi_public_key"] is True
        row = _row(version)
        assert row.api_secret == saved
        assert row.get_api_secret() == KEY


def test_a_new_key_replaces_the_saved_key(auth_client, agent, version):
    _patch(auth_client, agent, {"vapi_public_key": KEY})

    _patch(auth_client, agent, {"vapi_public_key": OTHER_KEY})

    assert _row(version).get_api_secret() == OTHER_KEY


def test_null_removes_the_saved_key(auth_client, agent, version):
    _patch(auth_client, agent, {"vapi_public_key": KEY})

    response = _patch(auth_client, agent, {"vapi_public_key": None})

    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["has_vapi_public_key"] is False
    row = _row(version)
    assert row.api_secret == ""
    assert row.config_json["monitorPlan"]["controlAuthenticationEnabled"] is False
    assert row.get_api_key() == PRIVATE_KEY


def test_null_also_removes_an_older_key_in_config_json(auth_client, agent, version):
    # An older row keeps its key in config_json, and the runner reads that
    # when api_secret is empty: removing the key removes that copy too.
    row = _row(version)
    row.config_json = {"public_api_key": "made-up-older-vapi-public-key"}
    row.save()

    response = _patch(auth_client, agent, {"vapi_public_key": None})

    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["has_vapi_public_key"] is False
    row = _row(version)
    assert "public_api_key" not in row.config_json
    assert row.config_json["monitorPlan"]["controlAuthenticationEnabled"] is False


def test_a_masked_value_is_not_saved_as_the_key(auth_client, agent, version):
    _patch(auth_client, agent, {"vapi_public_key": KEY})

    _patch(auth_client, agent, {"vapi_public_key": "********"})

    assert _row(version).get_api_secret() == KEY


def test_the_edit_endpoint_saves_and_removes_the_key(auth_client, agent, version):
    saved = auth_client.put(
        f"{AGENTS}{agent.pk}/edit/", {"vapi_public_key": KEY}, format="json"
    )
    assert saved.status_code == status.HTTP_200_OK, saved.content
    _no_secret(saved, KEY)
    assert _row(version).get_api_secret() == KEY

    removed = auth_client.put(
        f"{AGENTS}{agent.pk}/edit/", {"vapi_public_key": None}, format="json"
    )
    assert removed.status_code == status.HTTP_200_OK, removed.content
    assert _row(version).api_secret == ""


def test_a_new_version_carries_the_saved_key_unless_it_is_removed(
    auth_client, agent, version
):
    _patch(auth_client, agent, {"vapi_public_key": KEY})

    carried = _new_version(auth_client, agent)
    assert carried.status_code == status.HTTP_201_CREATED, carried.content
    _no_secret(carried, KEY, _row(version).api_secret)
    newest = _newest(agent)
    assert newest.pk != version.pk
    assert resolve_vapi_public_key_for_version(newest) == KEY
    row = _row(newest)
    assert row.config_json["monitorPlan"]["controlAuthenticationEnabled"] is True

    blank = _new_version(auth_client, agent, {"vapi_public_key": ""})
    assert resolve_vapi_public_key_for_version(_newest(agent)) == KEY
    assert blank.status_code == status.HTTP_201_CREATED, blank.content

    replaced = _new_version(auth_client, agent, {"vapi_public_key": OTHER_KEY})
    assert replaced.status_code == status.HTTP_201_CREATED, replaced.content
    assert resolve_vapi_public_key_for_version(_newest(agent)) == OTHER_KEY

    removed = _new_version(auth_client, agent, {"vapi_public_key": None})
    assert removed.status_code == status.HTTP_201_CREATED, removed.content
    assert resolve_vapi_public_key_for_version(_newest(agent)) is None
    assert has_vapi_public_key(_row(_newest(agent))) is False
    # Earlier versions keep their own row.
    assert _row(version).get_api_secret() == KEY


def test_a_version_snapshot_carries_neither_the_key_nor_api_secret(
    auth_client, agent, version
):
    _patch(auth_client, agent, {"vapi_public_key": KEY})
    _new_version(auth_client, agent, {"vapi_public_key": OTHER_KEY})

    for stored in AgentVersion.no_workspace_objects.filter(agent_definition=agent):
        raw = json.dumps(stored.configuration_snapshot)
        for secret in (KEY, OTHER_KEY, PRIVATE_KEY):
            assert secret not in raw
        assert "vapi_public_key" not in raw
        assert "api_secret" not in raw
    listing = auth_client.get(f"{AGENTS}{agent.pk}/versions/")
    assert listing.status_code == status.HTTP_200_OK
    _no_secret(listing, KEY, OTHER_KEY)


def test_the_snapshot_scrub_removes_the_write_only_key_everywhere():
    scrubbed = scrub_snapshot_secrets(
        {
            "agent_name": "kept",
            "vapi_public_key": KEY,
            "nested": [{"vapi_public_key": KEY, "kept": 1}],
        }
    )

    assert scrubbed == {"agent_name": "kept", "nested": [{"kept": 1}]}


def test_another_organizations_agent_cannot_be_read_or_written(
    auth_client, agent, version
):
    stranger = Organization.objects.create(name="Other Organization")
    foreign = AgentDefinition.no_workspace_objects.create(
        agent_name="Foreign",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+13334445555",
        inbound=True,
        description="Foreign",
        provider="vapi",
        organization=stranger,
        languages=["en"],
    )
    foreign_version = foreign.create_version(
        description="Foreign", commit_message="Foreign", status="active"
    )
    _credentials(foreign_version)

    attempts = (
        _patch(auth_client, foreign, {"vapi_public_key": KEY}),
        auth_client.put(
            f"{AGENTS}{foreign.pk}/edit/", {"vapi_public_key": KEY}, format="json"
        ),
        _new_version(auth_client, foreign, {"vapi_public_key": KEY}),
        auth_client.get(f"{AGENTS}{foreign.pk}/"),
    )

    for response in attempts:
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), response.content
        _no_secret(response, KEY)
    assert _row(foreign_version).api_secret == ""
    assert not AgentVersion.no_workspace_objects.filter(
        agent_definition=foreign, version_number__gt=1
    ).exists()


def test_another_workspaces_agent_cannot_be_written(
    auth_client, organization, user, workspace
):
    other_workspace = Workspace.no_workspace_objects.create(
        name="Other Vapi Key Workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    hidden = _agent(organization, other_workspace, "Hidden Vapi agent")
    hidden_version = hidden.create_version(
        description="Hidden", commit_message="Hidden", status="active"
    )
    _credentials(hidden_version)

    response = _patch(auth_client, hidden, {"vapi_public_key": KEY})

    assert response.status_code in (
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    ), response.content
    assert _row(hidden_version).api_secret == ""


def test_a_provider_other_than_vapi_saves_no_public_key(auth_client, agent, version):
    response = _patch(
        auth_client, agent, {"provider": "retell", "vapi_public_key": KEY}
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    row = _row(version)
    assert row.provider_type == ProviderCredentials.ProviderType.RETELL
    assert row.api_secret == ""
    assert response.json()["has_vapi_public_key"] is False


def test_the_runner_names_what_to_do_when_no_key_is_saved():
    activity = (ROOT / "temporal/activities/hosted_runner.py").read_text()

    assert "coming soon" not in activity
    # Both refusal sites (the pre-spawn check and the credential resolver) say
    # the same sentence, split across two adjacent literals.
    assert activity.count("This Vapi assistant has authentication on.") == 2
    assert activity.count("agent's Vapi connection to test it here.") == 2
    assert activity.count("No call was started.") == 2
