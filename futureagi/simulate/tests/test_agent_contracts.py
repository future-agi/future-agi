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
    SEED_MAX_BYTES,
    contract_bytes,
    seed_was_used,
    store_build_contract,
    stored_version_contract,
)
from simulate.services.agent_setup import stored_agent
from simulate.services.hosted_harness_gateway import authored_contract
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
        member = tarfile.TarInfo("./contract.json")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    return body.getvalue()


def _archive_with(entries: dict[str, bytes]) -> bytes:
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode="w:gz") as tar:
        for name, data in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return body.getvalue()


@pytest.mark.parametrize("name", ["./contract.json", "contract.json"])
def test_the_authored_contract_is_read_however_the_sandbox_named_it(name):
    body = _archive_with({name: json.dumps({"modality": "voice"}).encode()})

    assert authored_contract(body) == {"modality": "voice"}


def test_a_nested_or_missing_contract_reads_as_empty():
    nested = _archive_with(
        {"./scenarios/one/contract.json": json.dumps({"modality": "voice"}).encode()}
    )
    other = _archive_with({"./environment.json": b"{}"})

    assert authored_contract(nested) == {}
    assert authored_contract(other) == {}
    assert authored_contract(b"not a tar") == {}


def _job(organization, *, frozen=False, seeded=False):
    metadata = {"authoring_object_key": ARCHIVE_KEY} if frozen else {}
    if seeded:
        metadata["seeded_contract"] = True
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
    objects, organization, definition, version, django_capture_on_commit_callbacks
):
    _freeze(objects)

    with django_capture_on_commit_callbacks(execute=True):
        hosted_harness._store_authored_agent(
            _job(organization, frozen=True), definition, _sent()
        )

    assert stored_agent(version.id) is not None
    assert stored_version_contract(version.id) == CONTRACT


def test_an_agent_stored_first_gets_the_contract_when_the_archive_is_saved(
    objects, organization, definition, version, django_capture_on_commit_callbacks
):
    job = _job(organization)
    with django_capture_on_commit_callbacks(execute=True):
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
    objects, organization, definition, version, django_capture_on_commit_callbacks
):
    job = _job(organization)
    with django_capture_on_commit_callbacks(execute=True):
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
    objects, organization, definition, version, django_capture_on_commit_callbacks
):
    _freeze(objects)
    objects.fail_put = True

    with django_capture_on_commit_callbacks(execute=True):
        hosted_harness._store_authored_agent(
            _job(organization, frozen=True), definition, _sent()
        )

    assert stored_agent(version.id) is not None
    assert stored_version_contract(version.id) is None


def test_a_contract_is_filed_under_its_agent_and_version(
    objects, organization, definition, version, django_capture_on_commit_callbacks
):
    job = _job(organization)
    with django_capture_on_commit_callbacks(execute=True):
        hosted_harness._store_authored_agent(job, definition, _sent())

    store_build_contract(job, agent_version_id=version.id, archive=_archive(CONTRACT))

    key = AgentVersionSetup.no_workspace_objects.get(
        agent_version_id=version.id
    ).contract_object_key
    assert key.startswith(
        f"agent-contracts/{organization.id}/{definition.id}/{version.id}/"
    )
    assert key in objects.objects


def test_a_seed_is_held_to_the_size_a_contract_is_stored_at():
    assert SEED_MAX_BYTES == 5 * 1_048_576


def test_a_contract_is_kept_as_utf8_and_not_escaped():
    body = contract_bytes({"agent": {"name": "café"}, "greeting": "こんにちは"})

    assert body == '{"agent":{"name":"café"},"greeting":"こんにちは"}'.encode()
    assert json.loads(body) == {"agent": {"name": "café"}, "greeting": "こんにちは"}


@pytest.mark.parametrize(
    "contract, used",
    [
        ({**CONTRACT, "reading_inputs": {"commit_sha": "a" * 40}}, True),
        ({**CONTRACT, "reading_inputs": {}}, True),
        ({**CONTRACT, "reading_inputs": "a" * 40}, False),
        (CONTRACT, False),
        (None, False),
    ],
)
def test_a_build_used_its_seed_only_when_the_reading_record_survives(
    contract, used, django_capture_on_commit_callbacks
):
    assert seed_was_used(contract) is used


def _stored_first(objects, organization, definition, version, capture, *, seeded):
    job = _job(organization, seeded=seeded)
    with capture(execute=True):
        hosted_harness._store_authored_agent(job, definition, _sent())
    store_build_contract(job, agent_version_id=version.id, archive=_archive(CONTRACT))
    return job


def test_a_build_that_used_its_seed_replaces_the_reading(
    objects, organization, definition, version, django_capture_on_commit_callbacks
):
    job = _stored_first(
        objects,
        organization,
        definition,
        version,
        django_capture_on_commit_callbacks,
        seeded=True,
    )
    finished = {
        **CONTRACT,
        "agent": {"name": "finished"},
        "reading_inputs": {"commit_sha": "a" * 40},
    }

    replaced = store_build_contract(
        job, agent_version_id=version.id, archive=_archive(finished)
    )

    assert replaced is True
    assert stored_version_contract(version.id) == finished


@pytest.mark.parametrize("seeded", [True, False])
def test_a_build_that_read_the_agent_again_keeps_the_reading(
    objects,
    organization,
    definition,
    version,
    seeded,
    django_capture_on_commit_callbacks,
):
    job = _stored_first(
        objects,
        organization,
        definition,
        version,
        django_capture_on_commit_callbacks,
        seeded=seeded,
    )
    reread = {**CONTRACT, "agent": {"name": "read again"}}
    if not seeded:
        reread["reading_inputs"] = {"commit_sha": "a" * 40}

    replaced = store_build_contract(
        job, agent_version_id=version.id, archive=_archive(reread)
    )

    assert replaced is False
    assert stored_version_contract(version.id) == CONTRACT
