from unittest.mock import patch

import pytest
from rest_framework import status

from simulate.models import AgentDefinition, AgentVersion, HostedHarnessJob
from simulate.tests.test_agent_overview_api import _environment
from simulate.tests.test_code_archives import storage as storage  # noqa: F401
from simulate.tests.test_harness_agent_versions import _preflight
from simulate.tests.test_harness_connected_versions import _connect

pytestmark = pytest.mark.django_db

AGENTS = "/simulate/agent-definitions/"
ENVIRONMENTS = "/simulate/api/harness-environments/"
NUDGE = "simulate.services.harness_versioning.nudges.schedule_environment_nudge"


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


@pytest.fixture
def agent(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="support",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def production(agent):
    version = _connect(agent)
    with patch(NUDGE):
        _preflight(version)
    version.refresh_from_db()
    return version


@pytest.fixture
def tested(agent, production):
    environment = _environment(agent, 1)
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        agent_version=production
    )
    return environment


def test_agent_detail_versions_carry_their_check_and_environments(
    auth_client, agent, production, tested
):
    response = auth_client.get(f"{AGENTS}{agent.id}/")

    assert response.status_code == status.HTTP_200_OK, response.content
    body = response.json()
    listed = {row["id"]: row for row in body["versions"]}
    assert listed[str(production.id)]["environments_count"] == 1
    assert listed[str(production.id)]["preflight_status"] == "passed"
    assert body["active_version"]["environments_count"] == 1


@pytest.mark.parametrize(
    "request_delete",
    [
        lambda client, agent: client.delete(
            AGENTS, {"agent_ids": [str(agent.id)]}, format="json"
        ),
        lambda client, agent: client.delete(f"{AGENTS}{agent.id}/delete/"),
        lambda client, agent: client.delete(
            f"/simulate/api/agent-definition-operations/{agent.id}/"
        ),
    ],
    ids=["bulk", "single", "viewset"],
)
def test_an_agent_an_environment_tests_is_kept_with_a_conflict(
    auth_client, agent, tested, request_delete
):
    response = request_delete(auth_client, agent)

    assert response.status_code == status.HTTP_409_CONFLICT, response.content
    assert response.json()["code"] == "agent_in_use"
    agent.refresh_from_db()
    assert agent.deleted is False
    assert AgentVersion.objects.filter(agent_definition=agent).exists()


def test_an_untested_agent_and_its_versions_are_deleted(auth_client, agent, production):
    response = auth_client.delete(f"{AGENTS}{agent.id}/delete/")

    assert response.status_code == status.HTTP_200_OK, response.content
    assert not AgentDefinition.objects.filter(id=agent.id).exists()
    assert not AgentVersion.objects.filter(agent_definition=agent).exists()


def test_compatibility_requires_the_environment_and_rejects_unknown_params(
    auth_client, agent, production, tested
):
    url = f"{AGENTS}{agent.id}/versions/{production.id}/compatibility/"

    missing = auth_client.get(url)
    unknown = auth_client.get(url, {"environment_id": str(tested.id), "extra": "1"})

    assert missing.status_code == status.HTTP_400_BAD_REQUEST, missing.content
    assert unknown.status_code == status.HTTP_400_BAD_REQUEST, unknown.content


def test_build_preflight_rejects_unknown_params(auth_client, agent, production):
    response = auth_client.get(f"{AGENTS}{agent.id}/build-preflight/", {"foo": "1"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content


def test_agent_version_errors_use_the_common_envelope(auth_client, agent, production):
    response = auth_client.get(
        f"{AGENTS}{agent.id}/versions/{agent.id}/compatibility/",
        {"environment_id": str(agent.id)},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND, response.content
    body = response.json()
    assert body["status"] is False
    assert body["code"] == "version_not_found"
    assert body["detail"] == "Agent version not found"
    assert body["retryable"] is False


def test_a_follow_policy_cannot_name_a_version(auth_client, production, tested):
    response = auth_client.post(
        f"{ENVIRONMENTS}{tested.id}/agent-policy/",
        {"policy": "follow", "version_id": str(production.id)},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
