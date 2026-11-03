import io
import json
import tarfile
import uuid
from types import SimpleNamespace

import pytest

from simulate.models import AgentDefinition
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services import hosted_harness
from simulate.services.agent_contracts import (
    store_build_contract,
    stored_version_contract,
)
from simulate.services.agent_setup import stored_agent
from simulate.tests.test_code_archives import storage as storage  # noqa: F401
from simulate.tests.test_hosted_harness_authored_agent import _sent

pytestmark = pytest.mark.django_db

CONTRACT = {
    "agent": {"name": "support"},
    "base_environment": {"orders": [{"id": 1}, {"id": 2}]},
    "runtime": {"env": {"OPENAI_API_KEY": "sk-raw-value"}},
}
ARCHIVE_KEY = "authoring/frozen.tar.gz"


@pytest.fixture
def objects(storage):  # noqa: F811
    return storage


@pytest.fixture
def definition(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="support",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def version(definition):
    return definition.create_version(
        description="v1", commit_message="v1", status="active"
    )


def _archive(contract):
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode="w:gz") as tar:
        data = json.dumps(contract).encode()
        member = tarfile.TarInfo("contract.json")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    return body.getvalue()


def _job(organization, *, frozen=False):
    metadata = {"authoring_object_key": ARCHIVE_KEY} if frozen else {}
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization.id,
        payload={"source": {"kind": "github"}, "metadata": metadata},
        agent_version=None,
        agent_version_id=None,
        environment_id=None,
        environment_version_id=None,
    )


def _freeze(objects, contract=CONTRACT):
    body = _archive(contract)
    objects.put_object("bucket", ARCHIVE_KEY, io.BytesIO(body), len(body))
    return body


def test_an_archive_saved_first_is_stored_when_the_agent_arrives(
    objects, organization, definition, version
):
    _freeze(objects)

    hosted_harness._store_authored_agent(
        _job(organization, frozen=True), definition, _sent()
    )

    assert stored_agent(version.id) is not None
    assert stored_version_contract(version.id) == CONTRACT


def test_an_agent_stored_first_gets_the_contract_when_the_archive_is_saved(
    objects, organization, definition, version
):
    job = _job(organization)
    hosted_harness._store_authored_agent(job, definition, _sent())
    assert stored_version_contract(version.id) is None

    stored = store_build_contract(
        job, agent_version_id=version.id, archive=_archive(CONTRACT)
    )

    assert stored is True
    assert stored_version_contract(version.id) == CONTRACT


def test_no_contract_is_kept_for_a_version_without_a_stored_agent(
    objects, organization, version
):
    stored = store_build_contract(
        _job(organization), agent_version_id=version.id, archive=_archive(CONTRACT)
    )

    assert stored is False
    assert not AgentVersionSetup.no_workspace_objects.filter(
        agent_version_id=version.id
    ).exists()


def test_the_first_contract_stays_when_a_rebuild_sends_another(
    objects, organization, definition, version
):
    job = _job(organization)
    hosted_harness._store_authored_agent(job, definition, _sent())
    store_build_contract(job, agent_version_id=version.id, archive=_archive(CONTRACT))

    again = store_build_contract(
        job,
        agent_version_id=version.id,
        archive=_archive({**CONTRACT, "agent": {"name": "changed"}}),
    )

    assert again is False
    assert stored_version_contract(version.id) == CONTRACT


def test_a_storage_outage_keeps_the_agent_and_stores_no_contract(
    objects, organization, definition, version
):
    _freeze(objects)
    objects.fail_put = True

    hosted_harness._store_authored_agent(
        _job(organization, frozen=True), definition, _sent()
    )

    assert stored_agent(version.id) is not None
    assert stored_version_contract(version.id) is None


def test_a_contract_is_filed_under_its_agent_and_version(
    objects, organization, definition, version
):
    job = _job(organization)
    hosted_harness._store_authored_agent(job, definition, _sent())

    store_build_contract(job, agent_version_id=version.id, archive=_archive(CONTRACT))

    key = AgentVersionSetup.no_workspace_objects.get(
        agent_version_id=version.id
    ).contract_object_key
    assert key.startswith(
        f"agent-contracts/{organization.id}/{definition.id}/{version.id}/"
    )
    assert key in objects.objects
