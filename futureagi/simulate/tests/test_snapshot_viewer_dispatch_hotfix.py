"""Cloud-only B2 dispatch coverage; real URLconf, authentication and workspace gate.

Use an ordinary DRF APIClient, NOT force_authenticate or WorkspaceAwareAPIClient.
The fixture-only cloud bootstrap supplies PostgreSQL/Redis and blocks egress.
"""

import copy
import json
import uuid

import pytest
from rest_framework.test import APIClient

from accounts.authentication import APIKeyAuthentication
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from ee.falcon_ai.models import Conversation, Message
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.run_test import RunTest
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

SECRET_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "encrypted_api_key",
        "encrypted_api_secret",
        "livekit_api_key",
        "livekit_api_secret",
        "public_api_key",
        "token",
        "password",
        "credentials",
        "credentials_legacy",
    }
)

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["Viewer", "workspace_viewer"])
def reader(request, user, organization, workspace):
    role = request.param
    User.objects.filter(pk=user.pk).update(organization_role=role)
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(role=role, level=Level.VIEWER)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER)
    user.refresh_from_db()
    credential = OrgApiKey.no_workspace_objects.create(
        name="B2 fixture reader",
        user=user,
        organization=organization,
        workspace=workspace,
        type="user",
        api_key="fixture-" + uuid.uuid4().hex,
        secret_key="fixture-" + uuid.uuid4().hex,
    )
    client = APIClient()
    client.credentials(
        HTTP_X_API_KEY=credential.api_key,
        HTTP_X_SECRET_KEY=credential.secret_key,
        HTTP_X_WORKSPACE_ID=str(workspace.pk),
    )
    yield client, user, organization, workspace
    clear_workspace_context()


@pytest.fixture
def legacy(reader):
    _, user, organization, workspace = reader
    sensitive = dict.fromkeys(SECRET_KEYS, "fixture")
    snapshot = {
        "agent_type": "voice",
        "description": "unchanged",
        **sensitive,
        "historical": [{"nested": sensitive.copy(), "keep": [True, None, 3]}],
    }
    agent = AgentDefinition.objects.create(
        agent_name="B2 fixture",
        agent_type="voice",
        inbound=True,
        provider="vapi",
        organization=organization,
        workspace=workspace,
    )
    version = AgentVersion.objects.create(
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
        version_number=1,
        version_name="fixture",
        description="fixture",
        status=AgentVersion.StatusChoices.ACTIVE,
        configuration_snapshot=copy.deepcopy(snapshot),
    )
    run = RunTest.objects.create(
        name="B2 fixture",
        agent_definition=agent,
        agent_version=version,
        organization=organization,
        workspace=workspace,
    )
    conversation = Conversation.objects.create(
        user=user, organization=organization, workspace=workspace, title="B2 fixture"
    )
    calls = [
        {
            "tool_name": "get_simulation_test",
            "result_full": json.dumps(
                {"agent_version": {"configuration_snapshot": snapshot}}
            ),
            "result_summary": "Result with 1 fields: agent_version",
        }
    ]
    message = Message.objects.create(
        conversation=conversation, role="assistant", content="fixture", tool_calls=calls
    )
    return agent, version, run, conversation, message, snapshot, calls


def check_dispatch(response, reader):
    assert response.status_code == 200, "Viewer GET did not return 200"
    request = response.renderer_context["request"]
    assert isinstance(request.successful_authenticator, APIKeyAuthentication)
    assert request.user.pk == reader[1].pk
    assert request.organization.pk == reader[2].pk
    assert request.workspace.pk == reader[3].pk
    assert request._request.resolver_match is not None


def check_clean(value):
    assert not any(key in json.dumps(value) for key in SECRET_KEYS), (
        "stored credential field survived response"
    )


def check_snapshot(value):
    display = {"livekit_api_key", "livekit_api_secret", "token", "password"}
    assert not (set(value) & (SECRET_KEYS - display)), "stored key survived"
    assert all(value.get(key) != "fixture" for key in display), "mask missing"
    check_clean(value["historical"])


def check_unchanged(legacy):
    _, version, _, _, message, snapshot, calls = legacy
    version.refresh_from_db()
    message.refresh_from_db()
    assert version.configuration_snapshot == snapshot, "stored snapshot mutated"
    assert message.tool_calls == calls, "stored tool result mutated"


def test_viewer_version_detail(reader, legacy):
    agent, version, *_ = legacy
    response = reader[0].get(
        f"/simulate/agent-definitions/{agent.pk}/versions/{version.pk}/"
    )
    check_dispatch(response, reader)
    check_snapshot(response.data["configuration_snapshot"])
    check_unchanged(legacy)


def test_viewer_run_detail(reader, legacy):
    response = reader[0].get(f"/simulate/run-tests/{legacy[2].pk}/")
    check_dispatch(response, reader)
    check_snapshot(response.data["agent_version"]["configuration_snapshot"])
    check_unchanged(legacy)


@pytest.mark.parametrize("method", ["get", "patch"])
def test_historical_conversation(reader, legacy, method):
    if method == "patch":
        # Unlike v1.56.1, 2.0 correctly denies workspace Viewer writes. Exercise
        # successful PATCH with a real Member, never bypass the authenticator.
        User.objects.filter(pk=reader[1].pk).update(
            organization_role=OrganizationRoles.MEMBER
        )
        OrganizationMembership.no_workspace_objects.filter(
            user=reader[1], organization=reader[2]
        ).update(role=OrganizationRoles.MEMBER, level=Level.MEMBER)
        WorkspaceMembership.no_workspace_objects.filter(
            user=reader[1], workspace=reader[3]
        ).update(role=OrganizationRoles.WORKSPACE_MEMBER, level=Level.WORKSPACE_MEMBER)
    url = f"/falcon-ai/conversations/{legacy[3].pk}/"
    response = (
        reader[0].get(url)
        if method == "get"
        else reader[0].patch(url, {"title": "edited"}, format="json")
    )
    check_dispatch(response, reader)
    check_clean(response.data["result"]["messages"][0]["tool_calls"])
    check_unchanged(legacy)


def test_viewer_cannot_patch_conversation(reader, legacy):
    response = reader[0].patch(
        f"/falcon-ai/conversations/{legacy[3].pk}/",
        {"title": "must not save"},
        format="json",
    )
    assert response.status_code == 403, "Viewer write unexpectedly admitted"
    legacy[3].refresh_from_db()
    assert legacy[3].title == "B2 fixture"
    check_unchanged(legacy)


@pytest.mark.parametrize("route", ["version", "run", "conversation"])
def test_authentication_is_required(reader, legacy, route):
    agent, version, run, conversation, *_ = legacy
    urls = {
        "version": f"/simulate/agent-definitions/{agent.pk}/versions/{version.pk}/",
        "run": f"/simulate/run-tests/{run.pk}/",
        "conversation": f"/falcon-ai/conversations/{conversation.pk}/",
    }
    response = APIClient().get(urls[route])
    assert response.status_code in (401, 403), "unauthenticated request admitted"


@pytest.mark.parametrize("route", ["version", "run", "conversation"])
def test_workspace_gate_is_not_bypassed(reader, legacy, route):
    agent, version, run, conversation, *_ = legacy
    # Keep an active membership elsewhere: this head intentionally auto-assigns
    # users with zero active memberships to a default workspace. Exercise the
    # explicit denied-workspace branch, not that unrelated bootstrap fallback.
    other = Workspace.objects.create(
        name="Other B2 fixture", organization=reader[2], created_by=reader[1]
    )
    membership = OrganizationMembership.no_workspace_objects.get(
        user=reader[1], organization=reader[2]
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=reader[1],
        workspace=other,
        organization_membership=membership,
        role=OrganizationRoles.WORKSPACE_VIEWER,
        is_active=True,
    )
    # Revoke the requested workspace. 2.0 key lookup may reject a pinned key
    # before workspace permission dispatch (401); explicit permission denial is
    # also valid (403). Neither case may return the protected resource.
    WorkspaceMembership.no_workspace_objects.filter(
        user=reader[1], workspace=reader[3]
    ).update(is_active=False)
    urls = {
        "version": f"/simulate/agent-definitions/{agent.pk}/versions/{version.pk}/",
        "run": f"/simulate/run-tests/{run.pk}/",
        "conversation": f"/falcon-ai/conversations/{conversation.pk}/",
    }
    response = reader[0].get(urls[route])
    assert response.status_code in (401, 403), "workspace membership gate bypassed"
