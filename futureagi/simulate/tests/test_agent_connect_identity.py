"""Which agent a connect stands for: each entry file picked from one folder or
upload is its own agent, an upload carries no GitHub fields, and a private
repository is known by the id GitHub gives it."""

import uuid
from unittest.mock import patch

import pytest

from integrations.services.source_hosts import Repository, RepositoryPage
from simulate.models import AgentDefinition, AgentScan, HarnessAgentVersionRuntime
from simulate.services.agent_connect import connect_agent, start_scan
from simulate.services.harness_github import GitHubResponse
from simulate.services.hosted_harness import HostedHarnessError

pytestmark = pytest.mark.django_db

SCAN = "/simulate/agents/scan/"
CONNECT = "/simulate/agents/connect/"
COMMIT = "a" * 40
ARCHIVE_ID = str(uuid.uuid4())
ARCHIVE_EXISTS = "simulate.services.harness_versioning.archives.archive_exists"
LOOKUP = "simulate.services.harness_agent_changes.github_repository"
CONNECTED = "integrations.services.github_app.installation_connected"
LISTED = "integrations.services.github_app.list_repositories"
ARCHIVE = {"kind": "archive", "archive_artifact_id": ARCHIVE_ID}
PUBLIC = {
    "kind": "github",
    "repository": "acme/agent",
    "ref": "main",
    "commit_sha": COMMIT,
}
PRIVATE = {
    **PUBLIC,
    "visibility": "private",
    "installation_id": "4242",
    "repository_id": 999,
}


@pytest.fixture(autouse=True)
def reading_on(settings):
    settings.HARNESS_AGENT_READING_ENABLED = True
    settings.HARNESS_SOURCE_SUBFOLDER_ENABLED = False


@pytest.fixture
def sources_found():
    public = GitHubResponse("ok", {"id": 7, "private": False})
    with (
        patch(ARCHIVE_EXISTS, return_value=True),
        patch(LOOKUP, return_value=public),
    ):
        yield


def _page(*names):
    return RepositoryPage(
        repositories=[
            Repository(id=77, full_name=name, default_branch="main", private=True)
            for name in names
        ],
        next_page=None,
    )


class TestEntryIdentity:
    @pytest.mark.usefixtures("sources_found")
    @pytest.mark.parametrize("source", [ARCHIVE, PUBLIC], ids=["archive", "github"])
    def test_each_entry_in_one_source_is_its_own_agent(self, auth_client, source):
        def connect(entry):
            return auth_client.post(
                CONNECT, {**source, "entry": entry, "name": "Harbor"}, format="json"
            )

        first = connect("support/agent.py")
        second = connect("billing/agent.py")
        again = connect("support/agent.py")

        assert first.status_code == 201, first.content
        assert second.status_code == 201, second.content
        assert first.json()["agent_id"] != second.json()["agent_id"]
        assert again.status_code == 200, again.content
        assert again.json()["exists"] is True
        assert again.json()["agent_id"] == first.json()["agent_id"]

    @pytest.mark.usefixtures("sources_found")
    def test_a_source_without_an_entry_is_not_the_agent_of_one_entry(
        self, organization, workspace, user
    ):
        picked = connect_agent(
            organization=organization,
            workspace=workspace,
            user=user,
            source={**ARCHIVE, "entry": "support/agent.py"},
            name="Harbor",
        )
        whole = connect_agent(
            organization=organization,
            workspace=workspace,
            user=user,
            source=dict(ARCHIVE),
            name="Harbor",
        )

        assert whole.created is True
        assert whole.definition.id != picked.definition.id

    @pytest.mark.parametrize("entry", ["/etc/passwd", "../agent.py", "a/../b.py"])
    def test_an_entry_outside_the_source_is_refused(self, auth_client, entry):
        agents = AgentDefinition.objects.count()

        response = auth_client.post(
            CONNECT, {**ARCHIVE, "entry": entry, "name": "Harbor"}, format="json"
        )

        assert response.status_code == 400
        assert AgentDefinition.objects.count() == agents


class TestUploadFields:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("repository", "acme/agent"),
            ("ref", "main"),
            ("visibility", "private"),
            ("installation_id", "4242"),
            ("repository_id", 7),
        ],
    )
    @pytest.mark.parametrize("url", [SCAN, CONNECT], ids=["scan", "connect"])
    def test_an_upload_carries_no_github_field(self, auth_client, url, field, value):
        agents = AgentDefinition.objects.count()
        body = {**ARCHIVE, field: value, "name": "Harbor"}
        if url == SCAN:
            body.pop("name")

        with (
            patch(ARCHIVE_EXISTS, return_value=True),
            patch("simulate.tasks.agent_scans.schedule_agent_scan"),
        ):
            response = auth_client.post(url, body, format="json")

        assert response.status_code == 400, response.content
        assert field in response.content.decode()
        assert not AgentScan.objects.exists()
        assert AgentDefinition.objects.count() == agents


class TestPrivateRepositoryId:
    def test_a_scan_keeps_githubs_repository_id_not_the_clients(
        self, organization, workspace, user
    ):
        with (
            patch(CONNECTED, return_value=True),
            patch(LISTED, return_value=_page("acme/agent-two", "Acme/Agent")) as listed,
            patch("simulate.tasks.agent_scans.schedule_agent_scan"),
        ):
            start_scan(
                organization=organization,
                workspace=workspace,
                user=user,
                source=dict(PRIVATE),
            )

        assert listed.call_args.args[0] == "4242"
        [scan] = AgentScan.objects.all()
        assert scan.source["repository_id"] == 77

    def test_a_connect_keeps_githubs_repository_id_not_the_clients(
        self, organization, workspace, user
    ):
        with (
            patch(CONNECTED, return_value=True),
            patch(LISTED, return_value=_page("acme/agent")),
        ):
            connected = connect_agent(
                organization=organization,
                workspace=workspace,
                user=user,
                source=dict(PRIVATE),
                name="Harbor",
            )

        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version_id=connected.version.id
        )
        assert runtime.agent_payload["source_access"]["repository_id"] == 77

    def test_a_repository_the_installation_cannot_read_is_refused(
        self, organization, workspace, user
    ):
        with (
            patch(CONNECTED, return_value=True),
            patch(LISTED, return_value=_page("acme/other")),
        ):
            with pytest.raises(HostedHarnessError) as refused:
                start_scan(
                    organization=organization,
                    workspace=workspace,
                    user=user,
                    source=dict(PRIVATE),
                )

        assert refused.value.code == "github_repository_not_found"
        assert refused.value.status_code == 422
        assert not AgentScan.objects.exists()
