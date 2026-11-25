import copy
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models import AgentVersionSetup, HostedHarnessJob
from simulate.services.agent_contracts import (
    contract_bytes,
    store_build_contract,
    store_version_contract,
    version_contract_key,
)
from simulate.services.harness_design_review import (
    APPROVED,
    approve,
    create_design_review,
    design_document,
    stored_review,
)
from simulate.services.hosted_harness import HostedHarnessError, create_hosted_job
from simulate.services.hosted_harness_gateway import _seed_contract_for
from simulate.tests.test_harness_design_review import CONTRACT
from simulate.tests.test_harness_provider import _v1_payload
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
    _definition,
    _RealisticStorage,
)

pytestmark = pytest.mark.django_db

START = "simulate.temporal.client.start_hosted_harness_gateway_workflow"
AUTHORED = "simulate.services.hosted_harness_gateway.authored_contract"
BASE_URL = "https://platform.example.com"
READ = {**copy.deepcopy(CONTRACT), "reading_inputs": {"commit": "a" * 40}}


class _Storage(_RealisticStorage):
    def remove_object(self, _bucket, object_name):
        self.objects.pop(object_name, None)


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _Storage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


@pytest.fixture(autouse=True)
def seeding_on(settings):
    settings.HARNESS_SEED_CONTRACT_ENABLED = True


@pytest.fixture
def version(user, workspace, storage):
    version = _agent_version(_definition(user, workspace))
    AgentVersionSetup.no_workspace_objects.create(
        agent_version=version, status=AgentVersionSetup.Status.STORED
    )
    assert store_version_contract(
        organization_id=user.organization_id,
        agent_version_id=version.id,
        contract=READ,
    )
    return version


def _reviewed(user, workspace, version):
    job, _ = create_hosted_job(
        user.organization,
        _v1_payload(scenario_count=6),
        idempotency_key=uuid.uuid4().hex,
        workspace=workspace,
        state=HostedHarnessJob.State.RECEIVED,
    )
    create_design_review(job, SimpleNamespace(agent_version_id=version.id), READ)
    return job


def _approve(job, user):
    return approve(
        job,
        user=user,
        base_url=BASE_URL,
        revision=design_document(job)["revision"],
    )


def _build_finishes(job, version, contract):
    job.refresh_from_db()
    job.payload["metadata"]["seeded_contract"] = True
    with patch(AUTHORED, return_value=contract):
        assert store_build_contract(job, agent_version_id=version.id, archive=b"x")


class TestTwoEnvironmentsOnOneVersion:
    def test_a_sibling_build_leaves_the_other_design_approvable_and_seedable(
        self, user, workspace, storage, version
    ):
        first = _reviewed(user, workspace, version)
        second = _reviewed(user, workspace, version)
        read_key = version_contract_key(version.id)
        with patch(START):
            _approve(first, user)
        built = {**copy.deepcopy(READ), "tools": READ["tools"][:2]}

        _build_finishes(first, version, built)

        assert version_contract_key(version.id) != read_key
        assert read_key not in storage.objects
        assert design_document(second)["agent_changed"] is False
        with patch(START):
            _approve(second, user)
        second.refresh_from_db()
        first.refresh_from_db()
        assert stored_review(second)["status"] == APPROVED
        assert _seed_contract_for(second) == contract_bytes(READ)
        assert _seed_contract_for(first) == contract_bytes(READ)

    def test_a_new_reading_of_the_version_still_refuses_the_old_design(
        self, user, workspace, storage, version
    ):
        waiting = _reviewed(user, workspace, version)
        reread = {**copy.deepcopy(READ), "reading_inputs": {"commit": "b" * 40}}
        assert store_version_contract(
            organization_id=user.organization_id,
            agent_version_id=version.id,
            contract=reread,
            replace=True,
        )

        assert design_document(waiting)["agent_changed"] is True
        with patch(START) as start, pytest.raises(HostedHarnessError) as refused:
            _approve(waiting, user)

        assert (refused.value.code, refused.value.status_code) == (
            "design_agent_changed",
            409,
        )
        start.assert_not_called()
