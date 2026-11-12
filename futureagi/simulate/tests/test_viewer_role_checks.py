import uuid
from types import SimpleNamespace

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework import status
from rest_framework.permissions import AllowAny

from accounts.tests.test_rbac_regressions import _member_client, _viewer_client
from agentcc.services.credential_manager import mask_key
from simulate.models import AgentDefinition, AgentVersion, RunTest
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
    ("post", f"{AGENT}versions/change/", "create an agent version"),
    ("post", f"{AGENT}versions/{OTHER}/production/", "change the production version"),
    ("post", f"{AGENT}versions/{OTHER}/retry/", "retry an agent version check"),
    ("post", f"{AGENT}versions/{OTHER}/discard/", "discard an agent version"),
    ("post", f"{AGENT}found-check/", "confirm what a reading found"),
    ("post", "/simulate/agents/scan/", "scan a source for agents"),
    ("post", "/simulate/agents/connect/", "connect an agent"),
    (
        "post",
        f"{ENVIRONMENT}agent-policy/",
        "change whether an environment follows production",
    ),
]

LABELS_WITHOUT_A_ROUTE_CASE = {
    "add evaluations",
    "add evaluations to a run",
    "change a build",
    "change an environment's default version",
    "change scenarios",
    "change the tool call evaluation",
    "check an environment setup",
    "create a test run",
    "delete agents",
    "delete an agent",
    "discard environment drafts",
    "dismiss a rebuild prompt",
    "extend a build",
    "restore an agent version",
    "run evaluations",
    "save environment drafts",
    "upload agent code",
}


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


def _declared_responses(handler, method):
    schema = getattr(handler, "_swagger_auto_schema", None) or {}
    return schema.get(method, schema).get("responses", {})


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


def test_a_write_outside_simulation_keeps_its_original_refusal():
    from accounts.authentication import ViewerReadOnly, workspace_write_denied

    refusal = workspace_write_denied(SimpleNamespace(path="/model-hub/datasets/"))

    assert not isinstance(refusal, ViewerReadOnly)
    assert refusal.detail == "Write access denied to this workspace"
    assert refusal.detail.code == "permission_denied"


def test_every_labelled_simulation_write_declares_the_viewer_refusal():
    from tfc.utils.api_serializers import ViewerReadOnlyErrorSerializer

    undeclared = [
        f"{method.upper()} {route}"
        for route, method, _view_cls, handler in _simulation_write_handlers()
        if getattr(handler, "_viewer_denied_action", None)
        and _declared_responses(handler, method).get(403)
        is not ViewerReadOnlyErrorSerializer
    ]

    assert undeclared == []


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


def test_no_simulation_write_is_exempt_by_its_path():
    from accounts.authentication import WORKSPACE_WRITE_EXEMPT_PATHS

    exempt = [
        f"{method.upper()} {route}"
        for route, method, view_cls, _handler in _simulation_write_handlers()
        if not _signs_in_without_a_workspace(view_cls)
        and any(path in f"/{route}" for path in WORKSPACE_WRITE_EXEMPT_PATHS)
    ]

    assert exempt == []


def test_every_viewer_label_reads_as_a_sentence():
    labels = {
        getattr(handler, "_viewer_denied_action", None)
        for *_, handler in _simulation_write_handlers()
    } - {None}

    assert (
        labels
        == {action for *_, action in VIEWER_REFUSALS} | LABELS_WITHOUT_A_ROUTE_CASE
    )
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


@pytest.fixture(params=["pinned", "latest"])
def keyed_run_test(request, organization, workspace, keyed_agent):
    agent, version = keyed_agent
    run_test = RunTest.objects.create(
        name="Keyed run",
        agent_definition=agent,
        agent_version=version if request.param == "pinned" else None,
        organization=organization,
        workspace=workspace,
    )
    return run_test, version


@pytest.mark.django_db
def test_a_viewer_reads_a_run_test_without_its_agent_key(
    organization, workspace, keyed_run_test
):
    run_test, version = keyed_run_test
    viewer = _viewer_client(organization, workspace)

    response = viewer.get(f"/simulate/run-tests/{run_test.id}/")

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["agent_version"]["id"] == version.id
    assert "api_key" not in response.data["agent_version"]
    assert RAW_KEY[:4] not in str(response.data)


@pytest.mark.django_db
def test_a_member_reads_a_run_test_with_its_masked_agent_key(
    organization, workspace, keyed_run_test
):
    run_test, version = keyed_run_test
    member = _member_client(organization, workspace)

    response = member.get(f"/simulate/run-tests/{run_test.id}/")

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["agent_version"]["id"] == version.id
    assert response.data["agent_version"]["api_key"] == mask_key(RAW_KEY)
    assert RAW_KEY not in str(response.data)
