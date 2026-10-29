"""Building an environment from a connected agent: the environment binds to the
agent's chosen version, takes its source and credential names, writes nothing to
the agent, and follows production unless pinned."""

from unittest.mock import patch

import pytest
from rest_framework import status

from simulate.models import AgentDefinition, AgentVersion, HostedHarnessJob
from simulate.services.harness_agent_versions import POLICY_KEY
from simulate.tests.test_harness_agent_creation import (
    _body,
    _job,
    _post,
)
from simulate.tests.test_harness_agent_creation import (
    client as client,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_agent_creation import (
    gates as gates,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_agent_versions import _preflight
from simulate.tests.test_harness_connected_versions import _connect
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
)

pytestmark = pytest.mark.django_db

NUDGE = "simulate.services.harness_versioning.nudges.schedule_environment_nudge"


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    settings.HARNESS_PROVIDER = "hosted"


@pytest.fixture
def agent(user, workspace):
    return AgentDefinition.objects.create(
        agent_name="support",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=user.organization,
        workspace=workspace,
    )


@pytest.fixture
def production(agent):
    version = _connect(agent)
    with patch(NUDGE):
        _preflight(version)
    version.refresh_from_db()
    return version


def _creation(agent, **metadata):
    return _body(metadata={"agent_definition_id": str(agent.id), **metadata})


def test_an_environment_binds_to_the_agents_production_version(
    client, user, workspace, gates, agent, production  # noqa: F811
):
    job = _job(_post(client, workspace, _creation(agent)))

    assert job.agent_version_id == production.id
    metadata = job.payload["metadata"]
    assert metadata["agent_definition_owner"] == "connected"
    assert metadata["agent_definition_id"] == str(agent.id)
    assert metadata["build_agent_version_id"] == str(production.id)
    assert metadata[POLICY_KEY] == "follow"
    assert job.payload["source"]["commit_sha"] == "a" * 40
    assert job.payload["source"]["repository"] == "acme/agent"
    assert AgentDefinition.objects.filter(organization=user.organization).count() == 1
    assert AgentVersion.objects.filter(agent_definition=agent).count() == 1
    assert job.payload["agent"]["secret_refs"] == {}


def test_a_creation_can_pin_a_specific_ready_version(
    client, workspace, gates, agent, production  # noqa: F811
):
    second = _connect(agent, parent=production, commit="b")
    with patch(NUDGE):
        _preflight(second)
    with patch(NUDGE):
        third = _connect(agent, parent=second, commit="c")

    pinned = _job(
        _post(
            client,
            workspace,
            _creation(
                agent, agent_version_id=str(production.id), agent_policy="pinned"
            ),
        )
    )
    assert pinned.agent_version_id == production.id
    assert pinned.payload["metadata"][POLICY_KEY] == "pinned"

    not_ready = _post(
        client, workspace, _creation(agent, agent_version_id=str(third.id))
    )
    assert not_ready.status_code == status.HTTP_409_CONFLICT, not_ready.content
    assert not_ready.json()["error"] == "version_not_ready"
    assert HostedHarnessJob.no_workspace_objects.count() == 1


def test_an_agent_without_a_version_cannot_be_built_from(
    client, workspace, gates, agent  # noqa: F811
):
    response = _post(client, workspace, _creation(agent))

    assert response.status_code == status.HTTP_409_CONFLICT, response.content
    assert response.json()["error"] == "agent_has_no_version"


def test_build_preflight_names_the_version_its_check_and_its_code(
    auth_client, agent, production
):
    url = f"/simulate/agent-definitions/{agent.id}/build-preflight/"

    response = auth_client.get(url)

    assert response.status_code == status.HTTP_200_OK, response.content
    body = response.json()
    assert body["ok"] is True
    assert body["version_id"] == str(production.id)
    assert [one["name"] for one in body["checks"]] == ["version", "code", "credentials"]

    draft = _connect(agent, parent=production, commit="b")
    blocked = auth_client.get(url, {"version_id": str(draft.id)}).json()

    assert blocked["ok"] is False
    assert blocked["checks"][0] == {
        "name": "version",
        "status": "blocked",
        "message": "v2 is pending",
    }


def test_build_preflight_on_an_agent_with_no_version(auth_client, agent):
    body = auth_client.get(
        f"/simulate/agent-definitions/{agent.id}/build-preflight/"
    ).json()

    assert body == {
        "ok": False,
        "version_id": None,
        "checks": [
            {
                "name": "version",
                "status": "blocked",
                "message": "This agent has no version to build from yet",
            }
        ],
    }
