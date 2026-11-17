"""Fixture-only routed regressions for historical snapshot output.

Run on the cloud fixture stack, never against a deployed database/provider.
Uses real URL routing and serializers; only the version-create snapshot producer
is patched to model a historical writer. No test prints credential values.
"""

import copy
import json
import uuid
from unittest.mock import patch

import pytest
from django.urls import resolve

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.run_test import RunTest
from simulate.serializers.agent_definition import (
    AgentDefinitionOperationSerializer,
    AgentDefinitionUpdateSerializer,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

SECRET_KEYS = (
    "api_key",
    "api_secret",
    "encrypted_api_key",
    "encrypted_api_secret",
    "livekit_api_key",
    "livekit_api_secret",
    "token",
    "password",
    "credentials",
    "credentials_legacy",
    "public_api_key",
)
DISPLAY_KEYS = {"livekit_api_key", "livekit_api_secret", "token", "password"}
pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]


@pytest.fixture
def historical(organization, workspace):
    agent = AgentDefinition.objects.create(
        agent_name="Snapshot fixture",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        provider="vapi",
        inbound=True,
        contact_number="+15555550100",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    marker = "fixture-" + uuid.uuid4().hex
    secrets = dict.fromkeys(SECRET_KEYS, marker)
    snapshot = {
        "agent_name": agent.agent_name,
        "agent_type": agent.agent_type,
        "provider": "vapi",
        "inbound": True,
        "observability_enabled": False,
        "description": "unchanged",
        **secrets,
        "historical_nested": [{**secrets, "safe": [1, False, None]}],
    }
    version = AgentVersion.objects.create(
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
        status=AgentVersion.StatusChoices.ACTIVE,
        description="fixture",
        configuration_snapshot=copy.deepcopy(snapshot),
    )
    return agent, version, snapshot, marker


@pytest.fixture
def viewer_client(auth_client, user, workspace):
    # Change both integer and legacy roles; leaving an org Owner membership
    # would silently make this an owner test, not a workspace Viewer test.
    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        role=OrganizationRoles.MEMBER_VIEW_ONLY, level=Level.VIEWER
    )
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER)
    user.organization_role = OrganizationRoles.MEMBER_VIEW_ONLY
    user.save(update_fields=["organization_role"])
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=user, workspace=workspace
        ).level
        == Level.WORKSPACE_VIEWER
    )
    assert (
        OrganizationMembership.no_workspace_objects.get(
            user=user, organization=workspace.organization
        ).level
        == Level.VIEWER
    )
    return auth_client


def require(condition, message):
    # No pytest assertion introspection of a response, stored object or marker.
    if not condition:
        pytest.fail(message, pytrace=False)


def safe_snapshot(actual, original, marker):
    require(
        marker not in json.dumps(actual), "stored snapshot value present in response"
    )
    require(
        not (set(actual) & (set(SECRET_KEYS) - DISPLAY_KEYS)), "removed field present"
    )
    require(
        actual.get("description") == original["description"],
        "non-secret output changed",
    )
    nested = actual["historical_nested"][0]
    require(not (set(nested) & set(SECRET_KEYS)), "nested credential field present")
    require(nested["safe"] == [1, False, None], "nested non-secret output changed")


def unchanged(version, snapshot):
    version.refresh_from_db()
    require(version.configuration_snapshot == snapshot, "stored snapshot mutated")


def version_url(agent, version, action=""):
    return f"/simulate/agent-definitions/{agent.id}/versions/{version.id}/{action}"


def test_viewer_version_detail(viewer_client, historical):
    agent, version, snapshot, marker = historical
    url = version_url(agent, version)
    require(resolve(url).url_name == "agent-versions-detail", "wrong URL resolution")
    response = viewer_client.get(url)
    require(response.status_code == 200, "Viewer version GET did not return 200")
    safe_snapshot(response.json()["configuration_snapshot"], snapshot, marker)
    require(marker not in response.content.decode(), "stored value elsewhere in body")
    unchanged(version, snapshot)


@pytest.mark.parametrize("explicit", [True, False], ids=["explicit", "latest"])
def test_viewer_run_detail(
    viewer_client, historical, organization, workspace, explicit
):
    agent, version, snapshot, marker = historical
    run = RunTest.objects.create(
        name="Snapshot run",
        agent_definition=agent,
        agent_version=version if explicit else None,
        organization=organization,
        workspace=workspace,
    )
    url = f"/simulate/run-tests/{run.id}/"
    require(resolve(url).url_name == "run-tests-detail", "wrong URL resolution")
    response = viewer_client.get(url)
    require(response.status_code == 200, "Viewer run GET did not return 200")
    safe_snapshot(
        response.json()["agent_version"]["configuration_snapshot"], snapshot, marker
    )
    require(marker not in response.content.decode(), "stored value elsewhere in body")
    unchanged(version, snapshot)


@pytest.mark.parametrize("route", ["/simulate/run-tests/", "/simulate/api/run-tests/"])
def test_viewer_full_lists(viewer_client, historical, organization, workspace, route):
    agent, version, snapshot, marker = historical
    RunTest.objects.create(
        name="Snapshot list",
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
    )
    response = viewer_client.get(route)
    require(response.status_code == 200, "Viewer list did not return 200")
    require(marker not in response.content.decode(), "stored value in full list body")
    unchanged(version, snapshot)


@pytest.mark.parametrize("action", ["activate/", "restore/"])
def test_version_mutation_output(auth_client, historical, action):
    agent, version, snapshot, marker = historical
    response = auth_client.post(version_url(agent, version, action), {}, format="json")
    require(response.status_code == 200, "version mutation did not return 200")
    safe_snapshot(
        response.json()["version"]["configuration_snapshot"], snapshot, marker
    )
    require(marker not in response.content.decode(), "stored value elsewhere in body")
    unchanged(version, snapshot)


def test_create_output_with_historical_writer(auth_client, historical):
    agent, version, snapshot, marker = historical
    # Current producers omit secrets. Inject the legacy writer shape only;
    # the route, view, persistence and response serializer remain real.
    with patch.object(
        AgentVersion,
        "create_snapshot",
        side_effect=lambda **kw: copy.deepcopy(snapshot),
    ):
        response = auth_client.post(
            f"/simulate/agent-definitions/{agent.id}/versions/create/",
            {"commit_message": "fixture"},
            format="json",
        )
    require(response.status_code == 201, "version create did not return 201")
    data = response.json()["version"]
    safe_snapshot(data["configuration_snapshot"], snapshot, marker)
    require(marker not in response.content.decode(), "stored value elsewhere in body")
    created = AgentVersion.objects.get(id=data["id"])
    unchanged(created, snapshot)
    unchanged(version, snapshot)


def test_write_only_fields_not_echoed(historical):
    agent, _, _, _ = historical
    operation = AgentDefinitionOperationSerializer(
        data={"api_key": "fixture-input", "assistant_id": "fixture"}
    )
    require(operation.is_valid(), "operation input rejected")
    require("api_key" not in operation.data, "operation input echoed")
    serializer = AgentDefinitionUpdateSerializer(agent)
    require(serializer.fields["api_key"].write_only, "update field readable")
    require(
        "api_key" not in serializer.data, "update output reinserted write-only field"
    )
