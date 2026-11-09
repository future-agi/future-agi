"""The job documents a reading and a scan hand the sandbox carry every field
agent-learning-kit's job model requires, so the sandbox never refuses them."""

from unittest.mock import patch

import pytest

from simulate.models import AgentDefinition, AgentScan
from simulate.services import agent_reading_sandbox
from simulate.services.agent_reading_sandbox import (
    reading_job_document,
    scan_job_document,
    scan_source,
)
from simulate.tests.test_harness_connected_versions import _connect, _runtime

pytestmark = pytest.mark.django_db

ALK_REQUIRED_JOB_FIELDS = {"job_id", "run_id", "execution", "source", "agent"}
ALK_ARTIFACT_LEVELS = {
    "metadata-only",
    "traces",
    "traces-and-recordings",
    "full",
    "local-only",
}


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


def _assert_alk_accepts(document):
    assert ALK_REQUIRED_JOB_FIELDS <= document.keys()
    assert document["execution"] == "hosted"
    level = (document.get("artifacts") or {}).get("level", "traces")
    assert level in ALK_ARTIFACT_LEVELS


def test_a_reading_job_carries_what_the_sandbox_requires(agent):
    version = _connect(agent)
    runtime = _runtime(version)

    document = reading_job_document(agent, runtime)

    _assert_alk_accepts(document)
    assert document["job_id"] == str(runtime.agent_version_id)
    assert document["metadata"]["agent_name"] == "support"
    assert document["source"]["commit_sha"] == "a" * 40


def test_each_reading_gets_its_own_run(agent):
    version = _connect(agent)
    runtime = _runtime(version)

    first = reading_job_document(agent, runtime)
    second = reading_job_document(agent, runtime)

    assert first["run_id"] != second["run_id"]


def test_a_scan_job_carries_what_the_sandbox_requires(organization, workspace):
    scan = AgentScan.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        source={"kind": "github", "repository": "acme/agent", "commit_sha": "b" * 40},
    )

    document = scan_job_document(scan)

    _assert_alk_accepts(document)
    assert document["job_id"] == str(scan.id)
    assert document["metadata"]["agent_name"] == "acme/agent"


def test_a_scan_job_carries_the_commit_resolved_for_it(organization, workspace):
    scan = AgentScan.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        source={"kind": "github", "repository": "acme/agent", "ref": "main"},
        status=AgentScan.Status.PENDING,
    )
    sent = {}

    def run_reading(source_archive, job_document, **_):
        sent.update(job_document)
        return None, {"status": "none", "agents": []}, None

    with (
        patch.object(agent_reading_sandbox, "require_harness_authoring"),
        patch(
            "simulate.services.agent_versions.branch_head_of",
            return_value="c" * 40,
        ),
        patch.object(
            agent_reading_sandbox,
            "acquire_source_archive",
            return_value=(b"archive", "c" * 40),
        ),
        patch.object(agent_reading_sandbox, "run_reading", side_effect=run_reading),
    ):
        scan_source(scan.id)

    assert sent["source"]["commit_sha"] == "c" * 40
    scan.refresh_from_db()
    assert scan.status == AgentScan.Status.NONE
