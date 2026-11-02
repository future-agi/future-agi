import uuid

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework import status
from rest_framework.permissions import AllowAny

from accounts.tests.test_rbac_regressions import _member_client, _viewer_client
from agentcc.services.credential_manager import mask_key
from simulate.models import AgentDefinition, AgentVersion
from simulate.services.agent_definition import sync_provider_credentials
from simulate.services.types.agent_definition import ProviderCredentialsInput

ID = uuid.uuid4()
OTHER = uuid.uuid4()
ENVIRONMENT = f"/simulate/api/harness-environments/{ID}/"
JOBS = "/simulate/api/harness-jobs/"
AGENT = f"/simulate/agent-definitions/{ID}/"
WRITE_METHODS = ("post", "put", "patch", "delete")
RAW_KEY = "sk-viewer-role-check-123456"

VIEWER_REFUSALS = [
    ("post", f"{ENVIRONMENT}run/", "start a paid run"),
    ("post", f"{JOBS}{ID}/runs/", "start a paid run"),
    ("post", f"/simulate/run-tests/{ID}/execute/", "start a paid run"),
    ("post", f"/simulate/test-executions/{ID}/cancel/", "cancel a run"),
    ("post", f"{JOBS}secret-values/", "add or change secrets"),
    ("post", f"{JOBS}secret-files/", "add or change secrets"),
    ("post", JOBS, "create an environment"),
    ("post", f"{JOBS}{ID}/cancel/", "cancel a build"),
    ("post", f"{JOBS}{ID}/conversation/messages/", "send messages to the builder"),
    ("patch", ENVIRONMENT, "edit an environment"),
    ("delete", ENVIRONMENT, "delete an environment"),
    ("post", f"{ENVIRONMENT}versions/agent/", "create an agent version"),
    ("post", f"{ENVIRONMENT}nudges/{OTHER}/answer/", "answer a rebuild prompt"),
    ("delete", f"{ENVIRONMENT}evaluations/{OTHER}/", "remove evaluations"),
    ("patch", f"{ENVIRONMENT}evaluations/{OTHER}/", "edit evaluations"),
    ("post", "/simulate/agent-definitions/create/", "create an agent"),
    ("put", f"{AGENT}edit/", "edit an agent"),
    ("post", f"{AGENT}versions/create/", "create an agent version"),
    ("post", f"{AGENT}versions/{OTHER}/activate/", "change the production version"),
]


def _walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def _simulation_write_handlers():
    for route, callback in _walk(get_resolver().url_patterns):
        view_cls = getattr(callback, "cls", None)
        if not route.startswith("simulate/") or view_cls is None:
            continue
        actions = getattr(callback, "actions", None)
        if isinstance(actions, dict):
            handlers = actions.items()
        else:
            handlers = [(m, m) for m in WRITE_METHODS if hasattr(view_cls, m)]
        for method, name in handlers:
            if method in WRITE_METHODS:
                yield route, method, view_cls, getattr(view_cls, name)


def _signs_in_without_a_workspace(view_cls):
    return view_cls.authentication_classes == [] or any(
        permission is AllowAny for permission in view_cls.permission_classes
    )


@pytest.mark.django_db
@pytest.mark.parametrize("method,url,action", VIEWER_REFUSALS)
def test_a_viewer_is_told_which_action_needs_the_member_role(
    organization, workspace, method, url, action
):
    viewer = _viewer_client(organization, workspace)

    response = getattr(viewer, method)(url, {}, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
    assert response.data["code"] == "viewer_read_only"
    assert response.data["action"] == action
    assert response.data["detail"] == (
        f"Viewers can't {action}. Ask a workspace admin for the Member role."
    )


@pytest.mark.django_db
def test_an_unlabelled_write_still_gets_the_viewer_message(organization, workspace):
    viewer = _viewer_client(organization, workspace)

    response = viewer.post("/simulate/scenarios/create/", {}, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
    assert response.data["code"] == "viewer_read_only"
    assert "action" not in response.data
    assert response.data["detail"] == (
        "Viewers can't make changes in this workspace. "
        "Ask a workspace admin for the Member role."
    )


def test_no_simulation_write_skips_the_viewer_check():
    skipped = [
        f"{method.upper()} {route}"
        for route, method, view_cls, handler in _simulation_write_handlers()
        if not _signs_in_without_a_workspace(view_cls)
        and (
            getattr(view_cls, "workspace_write_exempt", False)
            or getattr(handler, "_read_query_post", False)
        )
    ]

    assert skipped == []


def test_every_viewer_label_reads_as_a_sentence():
    labels = {
        getattr(handler, "_viewer_denied_action", None)
        for *_, handler in _simulation_write_handlers()
    } - {None}

    assert labels >= {action for *_, action in VIEWER_REFUSALS}
    for label in labels:
        assert label == label.strip() and label[0].islower(), label
        assert not label.endswith("."), label


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/accounts/keys/", "/accounts/key/get_secret_keys/"])
def test_a_viewer_cannot_see_api_keys(organization, workspace, url):
    viewer = _viewer_client(organization, workspace)

    response = viewer.get(url)

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
    assert response.data["code"] == "viewer_read_only"
    assert response.data["detail"] == (
        "Viewers can't see API keys. Ask a workspace admin for the Member role."
    )


@pytest.mark.django_db
def test_a_member_still_reads_the_system_key(organization, workspace):
    member = _member_client(organization, workspace)

    response = member.get("/accounts/keys/")

    assert response.status_code == status.HTTP_200_OK, response.data
    assert len(response.data["data"]["secret_key"]) == 32
    assert "*" not in response.data["data"]["secret_key"]


@pytest.fixture
def keyed_agent(organization, workspace):
    agent = AgentDefinition.objects.create(
        agent_name="Keyed agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12345678901",
        inbound=True,
        provider="vapi",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    version = agent.create_version(
        description="v1",
        commit_message="v1",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    sync_provider_credentials(
        version,
        ProviderCredentialsInput(
            provider="vapi",
            api_key=RAW_KEY,
            assistant_id="asst_viewer_role_check",
            provider_was_provided=True,
        ),
    )
    return agent, version


@pytest.mark.django_db
def test_a_viewer_reads_an_agent_without_its_masked_key(
    organization, workspace, keyed_agent
):
    agent, version = keyed_agent
    viewer = _viewer_client(organization, workspace)

    agent_response = viewer.get(f"/simulate/agent-definitions/{agent.id}/")
    version_response = viewer.get(
        f"/simulate/agent-definitions/{agent.id}/versions/{version.id}/"
    )

    assert agent_response.status_code == status.HTTP_200_OK, agent_response.data
    assert agent_response.data["api_key"] == ""
    assert version_response.status_code == status.HTTP_200_OK
    assert version_response.data["api_key"] is None
    assert RAW_KEY[:4] not in str(agent_response.data)
    assert RAW_KEY[:4] not in str(version_response.data)


@pytest.mark.django_db
def test_a_member_reads_an_agent_with_its_masked_key(
    organization, workspace, keyed_agent
):
    agent, version = keyed_agent
    member = _member_client(organization, workspace)

    agent_response = member.get(f"/simulate/agent-definitions/{agent.id}/")
    version_response = member.get(
        f"/simulate/agent-definitions/{agent.id}/versions/{version.id}/"
    )

    assert agent_response.data["api_key"] == mask_key(RAW_KEY)
    assert version_response.data["api_key"] == mask_key(RAW_KEY)
    assert RAW_KEY not in str(agent_response.data)
    assert RAW_KEY not in str(version_response.data)
