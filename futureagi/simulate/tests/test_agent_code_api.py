"""The code of a version: tree, file and changes over its stored archive; the host
is never contacted, paths outside the archive are refused, big and binary files
answer their kind and no content."""

import io
import tarfile

import pytest
from rest_framework import status

from simulate.models import AgentDefinition, HarnessAgentVersionRuntime
from simulate.services.agent_code import MAX_FILE_BYTES
from simulate.services.code_archives import store_commit_archive
from simulate.services.harness_agent_versions import create_agent_version
from simulate.tests.test_code_archives import storage as storage  # noqa: F401
from simulate.tests.test_harness_connected_versions import _source

pytestmark = pytest.mark.django_db

Trigger = HarnessAgentVersionRuntime.Trigger


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture
def agent(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="support",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


def _archive(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, data in files.items():
            body = data if isinstance(data, bytes) else data.encode("utf-8")
            info = tarfile.TarInfo(f"source/{path}")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


FIRST = {
    "README.md": "# Agent\n",
    "agents/support/agent.py": "def run():\n    return 'hi'\n",
    "agents/support/prompts/system.md": "Be kind.\n",
    "agents/billing/agent.py": "print('billing')\n",
    "agents/support/model.bin": b"\x00\x01\x02binary",
    "agents/support/big.txt": "x" * (MAX_FILE_BYTES + 1),
}
SECOND = {
    **FIRST,
    "agents/support/agent.py": "def run():\n    return 'hello'\n",
    "agents/support/tools.py": "TOOLS = []\n",
}
del SECOND["agents/support/prompts/system.md"]


def _stored(organization, commit, files):
    store_commit_archive(
        organization_id=organization.id,
        repository="acme/agent",
        commit_sha=commit * 40,
        body=_archive(files),
    )


def _version(agent, *, commit, parent=None, subfolder="agents/support"):
    return create_agent_version(
        agent,
        parent=parent,
        env=None,
        source={**_source(commit), "subfolder": subfolder},
        prompt="You help.",
        agent_payload={"connector": "auto", "config": {}},
        trigger=Trigger.CONNECT if parent is None else Trigger.SYNC,
    )


def _url(agent, version, leaf):
    return f"/simulate/agent-definitions/{agent.id}/versions/{version.id}/code/{leaf}/"


def test_tree_lists_the_folder_the_agent_lives_in(
    auth_client, organization, storage, agent  # noqa: F811
):
    _stored(organization, "a", FIRST)
    version = _version(agent, commit="a")

    response = auth_client.get(_url(agent, version, "tree"))

    assert response.status_code == status.HTTP_200_OK, response.content
    body = response.json()
    assert body["subfolder"] == "agents/support"
    assert body["truncated"] is False
    assert {one["path"]: one["kind"] for one in body["files"]} == {
        "agents/support/agent.py": "text",
        "agents/support/big.txt": "too_large",
        "agents/support/model.bin": "binary",
        "agents/support/prompts/system.md": "text",
    }
    assert all(one["size"] > 0 for one in body["files"])


def test_tree_covers_the_whole_repository_without_a_subfolder(
    auth_client, organization, storage, agent  # noqa: F811
):
    _stored(organization, "a", FIRST)
    version = _version(agent, commit="a", subfolder="")

    body = auth_client.get(_url(agent, version, "tree")).json()

    assert body["subfolder"] == ""
    assert [one["path"] for one in body["files"]][:2] == [
        "README.md",
        "agents/billing/agent.py",
    ]


def test_file_answers_text_with_its_language_and_kinds_without_content(
    auth_client, organization, storage, agent  # noqa: F811
):
    _stored(organization, "a", FIRST)
    version = _version(agent, commit="a")

    text = auth_client.get(
        _url(agent, version, "file"), {"path": "agents/support/agent.py"}
    ).json()
    assert text == {
        "path": "agents/support/agent.py",
        "size": len(FIRST["agents/support/agent.py"]),
        "kind": "text",
        "language": "python",
        "content": FIRST["agents/support/agent.py"],
    }

    binary = auth_client.get(
        _url(agent, version, "file"), {"path": "agents/support/model.bin"}
    ).json()
    assert binary["kind"] == "binary"
    assert "content" not in binary

    big = auth_client.get(
        _url(agent, version, "file"), {"path": "agents/support/big.txt"}
    ).json()
    assert big["kind"] == "too_large"
    assert "content" not in big


@pytest.mark.parametrize(
    "path",
    ["../etc/passwd", "/agents/support/agent.py", "agents/../README.md", "", "a\\b"],
)
def test_a_path_outside_the_archive_is_refused(
    auth_client, organization, storage, agent, path  # noqa: F811
):
    _stored(organization, "a", FIRST)
    version = _version(agent, commit="a")

    response = auth_client.get(_url(agent, version, "file"), {"path": path})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"] == "path_outside_archive"


def test_a_missing_file_and_a_version_without_code_answer_404(
    auth_client, organization, storage, agent  # noqa: F811
):
    _stored(organization, "a", FIRST)
    version = _version(agent, commit="a")
    assert (
        auth_client.get(_url(agent, version, "file"), {"path": "nope.py"}).status_code
        == status.HTTP_404_NOT_FOUND
    )

    bare = agent.create_version(description="p", commit_message="v0")
    response = auth_client.get(_url(agent, bare, "tree"))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"] == "no_code"


def test_a_stored_archive_that_cannot_be_read_is_unavailable(
    auth_client, organization, storage, agent  # noqa: F811
):
    _stored(organization, "a", FIRST)
    version = _version(agent, commit="a")
    storage.objects.clear()

    response = auth_client.get(_url(agent, version, "tree"))

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["error"] == "archive_unavailable"


def test_changes_compare_with_the_previous_version_within_the_folder(
    auth_client, organization, storage, agent  # noqa: F811
):
    _stored(organization, "a", FIRST)
    _stored(organization, "b", SECOND)
    first = _version(agent, commit="a")
    second = _version(agent, commit="b", parent=first)

    body = auth_client.get(_url(agent, second, "changes")).json()

    assert body["against"] == str(first.id)
    assert body["files"] == [
        {"path": "agents/support/agent.py", "status": "modified"},
        {"path": "agents/support/prompts/system.md", "status": "removed"},
        {"path": "agents/support/tools.py", "status": "added"},
    ]
    explicit = auth_client.get(
        _url(agent, first, "changes"), {"against": str(second.id)}
    ).json()
    assert {one["path"]: one["status"] for one in explicit["files"]}[
        "agents/support/tools.py"
    ] == "removed"
    assert auth_client.get(_url(agent, first, "changes")).json() == {
        "against": None,
        "files": [],
        "unavailable": "no_previous_version",
    }


def test_another_organization_cannot_read_the_code(
    api_client, organization, storage, agent, user  # noqa: F811
):
    _stored(organization, "a", FIRST)
    version = _version(agent, commit="a")

    response = api_client.get(_url(agent, version, "tree"))

    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    )
