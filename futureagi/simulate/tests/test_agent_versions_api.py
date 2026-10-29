"""The Agents page's version endpoints: a change by kind, production, retry and
discard, the richer list, and an environment's follow-or-pin policy."""

from unittest.mock import patch

import pytest
from rest_framework import status

from simulate.models import AgentDefinition, AgentVersion, HostedHarnessJob
from simulate.services.harness_agent_changes import HeadResult
from simulate.services.harness_agent_versions import POLICY_KEY
from simulate.tests.test_agent_overview_api import _environment
from simulate.tests.test_code_archives import storage as storage  # noqa: F401
from simulate.tests.test_harness_agent_versions import _preflight
from simulate.tests.test_harness_connected_versions import _completed, _connect

pytestmark = pytest.mark.django_db

AGENTS = "/simulate/agent-definitions/"
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


def _change(client, agent, **body):
    return client.post(f"{AGENTS}{agent.id}/versions/change/", body, format="json")


def test_sync_at_the_head_is_up_to_date(auth_client, agent, production):
    with patch(
        "simulate.services.harness_agent_changes.github_head",
        return_value=HeadResult("ok", "a" * 40),
    ):
        response = _change(auth_client, agent, kind="sync")

    assert response.status_code == status.HTTP_200_OK, response.content
    body = response.json()
    assert body["up_to_date"] is True
    assert body["version"]["id"] == str(production.id)
    assert AgentVersion.objects.filter(agent_definition=agent).count() == 1


def test_sync_at_a_new_head_makes_a_draft_from_production(
    auth_client, agent, production
):
    with patch(
        "simulate.services.harness_agent_changes.github_head",
        return_value=HeadResult("ok", "b" * 40),
    ) as head:
        response = _change(auth_client, agent, kind="sync")

    assert response.status_code == status.HTTP_202_ACCEPTED, response.content
    body = response.json()
    assert body["created"] is True
    assert body["version"]["status"] == "draft"
    assert body["version"]["source"]["commit_sha"] == "b" * 40
    assert head.call_args.args[:2] == ("acme/agent", "main")
    production.refresh_from_db()
    assert production.status == "active"


def test_sync_refuses_an_unreachable_branch(auth_client, agent, production):
    with patch(
        "simulate.services.harness_agent_changes.github_head",
        return_value=HeadResult("rate_limited"),
    ):
        response = _change(auth_client, agent, kind="sync")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["error"] == "source_unreachable"


def test_a_change_needs_the_fields_of_its_kind(auth_client, agent, production):
    assert (
        _change(auth_client, agent, kind="upload").status_code
        == status.HTTP_400_BAD_REQUEST
    )
    assert (
        _change(auth_client, agent, kind="sync", prompt="x").status_code
        == status.HTTP_400_BAD_REQUEST
    )
    assert (
        _change(auth_client, agent, kind="credentials", secret_values={}).status_code
        == status.HTTP_400_BAD_REQUEST
    )


def test_a_prompt_edit_is_refused_for_a_code_agent(auth_client, agent, production):
    response = _change(auth_client, agent, kind="manual", prompt="Be brief.")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["error"] == "prompt_in_source"


def test_a_credentials_change_makes_a_version_that_reuses_the_source(
    auth_client, agent, production
):
    response = _change(
        auth_client, agent, kind="credentials", secret_values={"DB_URL": "pg://x"}
    )

    assert response.status_code == status.HTTP_202_ACCEPTED, response.content
    body = response.json()
    assert body["created"] is True
    assert body["version"]["source"]["commit_sha"] == "a" * 40
    assert body["version"]["version_number"] == 2


def test_the_list_carries_origin_check_production_and_environments(
    auth_client, agent, production
):
    environment = _completed(_environment(agent, 1), production)
    with patch(
        "simulate.services.harness_agent_changes.github_head",
        return_value=HeadResult("ok", "b" * 40),
    ):
        _change(auth_client, agent, kind="sync")

    response = auth_client.get(f"{AGENTS}{agent.id}/versions/")

    assert response.status_code == status.HTTP_200_OK
    rows = {row["version_number"]: row for row in response.json()["results"]}
    assert rows[1]["origin"] == "connect"
    assert rows[1]["preflight_status"] == "passed"
    assert rows[1]["in_production"] is True
    assert rows[1]["environments_count"] == 1
    assert rows[1]["source"] == {
        "kind": "github",
        "repository": "acme/agent",
        "ref": "main",
        "commit_sha": "a" * 40,
    }
    assert rows[2]["origin"] == "sync"
    assert rows[2]["preflight_status"] == "pending"
    assert rows[2]["in_production"] is False
    assert rows[2]["environments_count"] == 0
    assert environment.agent_version_id == production.id


def test_production_needs_a_passed_check_then_moves_following_environments(
    auth_client, agent, production
):
    from simulate.services.agent_setup import store_agent
    from simulate.services.hosted_harness import BUILD_PIN_KEY
    from simulate.tests.test_agent_setup_service import source_agent

    following = _completed(_environment(agent, 1), production)
    assert store_agent(
        agent_version_id=production.id, agent=source_agent(), sdk_version="0.2.3"
    )
    HostedHarnessJob.no_workspace_objects.filter(id=following.id).update(
        payload={
            **following.payload,
            "metadata": {
                **following.payload.get("metadata", {}),
                BUILD_PIN_KEY: str(production.id),
            },
        }
    )
    with patch(
        "simulate.services.harness_agent_changes.github_head",
        return_value=HeadResult("ok", "b" * 40),
    ):
        second_id = _change(auth_client, agent, kind="sync").json()["version"]["id"]
    second = AgentVersion.objects.get(id=second_id)

    refused = auth_client.post(f"{AGENTS}{agent.id}/versions/{second.id}/production/")
    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["error"] == "version_not_ready"

    assert store_agent(
        agent_version_id=second.id, agent=source_agent(), sdk_version="0.2.3"
    )
    with patch(NUDGE):
        _preflight(second, patches=None)
    with patch(NUDGE):
        response = auth_client.post(
            f"{AGENTS}{agent.id}/versions/{second.id}/production/"
        )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["version"]["is_active"] is True
    second.refresh_from_db()
    production.refresh_from_db()
    following.refresh_from_db()
    assert second.status == "active"
    assert production.status == "archived"
    assert following.agent_version_id == second.id


def test_discard_refuses_production_and_retry_requeues_a_check(
    auth_client, agent, production, django_capture_on_commit_callbacks
):
    refused = auth_client.post(f"{AGENTS}{agent.id}/versions/{production.id}/discard/")
    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["error"] == "version_in_production"

    with patch(
        "simulate.services.harness_agent_changes.github_head",
        return_value=HeadResult("ok", "b" * 40),
    ):
        draft_id = _change(auth_client, agent, kind="sync").json()["version"]["id"]

    with (
        patch("simulate.services.harness_agent_versions._schedule_preflight") as queued,
        django_capture_on_commit_callbacks(execute=True),
    ):
        retried = auth_client.post(f"{AGENTS}{agent.id}/versions/{draft_id}/retry/")
    assert retried.status_code == status.HTTP_202_ACCEPTED, retried.content
    assert queued.called

    discarded = auth_client.post(f"{AGENTS}{agent.id}/versions/{draft_id}/discard/")
    assert discarded.status_code == status.HTTP_200_OK, discarded.content
    assert AgentVersion.objects.filter(id=draft_id, deleted=False).exists()
    rows = {
        row["id"]: row
        for row in auth_client.get(f"{AGENTS}{agent.id}/versions/").json()["results"]
    }
    assert rows[draft_id]["preflight_status"] == "discarded"


def test_an_environment_can_pin_to_a_version_and_follow_production_again(
    auth_client, agent, production
):
    environment = _completed(_environment(agent, 1), production)
    with patch(
        "simulate.services.harness_agent_changes.github_head",
        return_value=HeadResult("ok", "b" * 40),
    ):
        second_id = _change(auth_client, agent, kind="sync").json()["version"]["id"]
    second = AgentVersion.objects.get(id=second_id)
    with patch(NUDGE):
        _preflight(second)
    url = f"/simulate/api/harness-environments/{environment.id}/agent-policy/"

    pinned = auth_client.post(
        url, {"policy": "pinned", "version_id": str(production.id)}, format="json"
    )
    assert pinned.status_code == status.HTTP_200_OK, pinned.content
    assert pinned.json()["policy"] == "pinned"
    environment.refresh_from_db()
    assert environment.payload["metadata"][POLICY_KEY] == "pinned"
    assert environment.agent_version_id == production.id

    with patch(NUDGE):
        auth_client.post(f"{AGENTS}{agent.id}/versions/{second.id}/production/")
    environment.refresh_from_db()
    assert environment.agent_version_id == production.id

    followed = auth_client.post(url, {"policy": "follow"}, format="json")
    assert followed.status_code == status.HTTP_200_OK, followed.content
    environment.refresh_from_db()
    assert environment.payload["metadata"][POLICY_KEY] == "follow"
    assert environment.agent_version_id == second.id
    assert followed.json()["agent_version"]["id"] == str(second.id)


def test_a_version_of_another_agent_cannot_be_pinned(auth_client, agent, production):
    environment = _completed(_environment(agent, 1), production)
    other = AgentDefinition.objects.create(
        agent_name="other",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=agent.organization,
        workspace=agent.workspace,
    )
    foreign = other.create_version(description="p", commit_message="v1")

    response = auth_client.post(
        f"/simulate/api/harness-environments/{environment.id}/agent-policy/",
        {"policy": "pinned", "version_id": str(foreign.id)},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert HostedHarnessJob.objects.get(id=environment.id).agent_version_id == (
        production.id
    )


def test_sync_is_refused_for_an_agent_without_a_github_source(auth_client, agent):
    agent.create_version(description="p", commit_message="v1", status="active")

    response = _change(auth_client, agent, kind="sync")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["error"] == "sync_unsupported"


def test_retry_needs_a_checked_version(auth_client, agent):
    bare = agent.create_version(description="p", commit_message="v1", status="active")

    response = auth_client.post(f"{AGENTS}{agent.id}/versions/{bare.id}/retry/")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error"] == "version_not_checked"


def test_a_prompt_edit_makes_a_version_for_a_built_in_agent_once(auth_client, agent):
    agent.create_version(
        description="Old prompt.", commit_message="v1", status="active"
    )

    created = _change(auth_client, agent, kind="manual", prompt="New prompt.")
    assert created.status_code == status.HTTP_202_ACCEPTED, created.content
    assert created.json()["version"]["description"] == "New prompt."

    same = _change(auth_client, agent, kind="manual", prompt="New prompt.")
    assert same.status_code == status.HTTP_200_OK
    assert same.json()["up_to_date"] is True


@pytest.mark.usefixtures("storage")
def test_upload_refuses_a_missing_archive_and_keeps_the_same_one(
    auth_client, agent, production
):
    import uuid

    missing = _change(
        auth_client, agent, kind="upload", archive_artifact_id=str(uuid.uuid4())
    )

    assert missing.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert missing.json()["error"] == "archive_source_not_found"


def test_compatibility_needs_a_known_environment(auth_client, agent, production):
    import uuid

    url = f"{AGENTS}{agent.id}/versions/{production.id}/compatibility/"

    assert (
        auth_client.get(url, {"environment_id": "not-a-uuid"}).status_code
        == status.HTTP_400_BAD_REQUEST
    )
    missing = auth_client.get(url, {"environment_id": str(uuid.uuid4())})
    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert missing.json()["error"] == "environment_not_found"


def test_another_organization_sees_none_of_the_version_endpoints(
    agent, production, organization
):
    from rest_framework.test import APIClient

    from accounts.models import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from tfc.constants.roles import OrganizationRoles

    other_org = Organization.objects.create(name="Other Org")
    other = User.objects.create_user(
        email="other-org@futureagi.com",
        password="testpassword123",
        name="Other",
        organization=other_org,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=other, organization=other_org, defaults={"role": OrganizationRoles.OWNER}
    )
    theirs = APIClient()
    theirs.force_authenticate(user=other)
    base = f"{AGENTS}{agent.id}/versions/{production.id}/"

    assert (
        theirs.post(
            f"{AGENTS}{agent.id}/versions/change/", {"kind": "sync"}, format="json"
        ).status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert theirs.post(f"{base}production/").status_code == status.HTTP_404_NOT_FOUND
    assert theirs.post(f"{base}retry/").status_code == status.HTTP_404_NOT_FOUND
    assert theirs.post(f"{base}discard/").status_code == status.HTTP_404_NOT_FOUND
    assert theirs.get(f"{base}code/tree/").status_code == status.HTTP_404_NOT_FOUND
    assert (
        theirs.get(f"{AGENTS}{agent.id}/build-preflight/").status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert agent.versions.count() == 1
