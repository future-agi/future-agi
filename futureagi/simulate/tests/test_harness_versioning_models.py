import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessEnvironmentVersion,
    HarnessNudge,
    HarnessSuiteVersion,
    HostedHarnessJob,
)

pytestmark = pytest.mark.django_db

PIECES = [HarnessEnvironmentVersion, HarnessSuiteVersion]


@pytest.fixture
def job(organization, workspace):
    return HostedHarnessJob.objects.create(
        organization=organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        request_digest="sha256:" + "0" * 64,
        schema_version="1",
        payload={},
        seed=1,
        scenario_count=1,
        artifact_level="summary",
        max_artifact_bytes=1,
        deadline_at=timezone.now() + timedelta(hours=1),
    )


def _version(model, job, **fields):
    values = {
        "job": job,
        "organization": job.organization,
        "workspace": job.workspace,
        "status": "draft",
        "origin": "manual",
        "hash": "sha256:" + uuid.uuid4().hex * 2,
        "archive_object_key": "harness-drafts/key.tar.gz",
        "archive_sha256": "0" * 64,
        "archive_size": 1,
    }
    if model is HarnessSuiteVersion:
        values["certify_hash"] = "sha256:" + "1" * 64
    values.update(fields)
    return model.objects.create(**values)


def _refused(create):
    with pytest.raises(IntegrityError), transaction.atomic():
        create()


@pytest.mark.parametrize("model", PIECES)
class TestPieceVersionConstraints:
    def test_one_draft_per_job(self, model, job):
        _version(model, job)

        _refused(lambda: _version(model, job))

    def test_a_discarded_draft_frees_the_slot(self, model, job):
        first = _version(model, job)
        first.status = "discarded"
        first.discard_reason = "user"
        first.save()

        assert _version(model, job).status == "draft"

    def test_a_soft_deleted_draft_frees_the_slot(self, model, job):
        first = _version(model, job)
        first.deleted = True
        first.save()

        assert _version(model, job).status == "draft"

    def test_active_hash_is_unique_per_job_and_kind(self, model, job):
        _version(model, job, status="active", hash="sha256:same", version_number=1)

        _refused(
            lambda: _version(
                model, job, status="active", hash="sha256:same", version_number=2
            )
        )
        assert _version(model, job, hash="sha256:same").status == "draft"
        assert (
            _version(
                model,
                job,
                status="active",
                hash="sha256:same",
                hash_kind="legacy_key",
                version_number=3,
            ).hash_kind
            == "legacy_key"
        )

    def test_version_numbers_are_unique_once_assigned(self, model, job):
        _version(model, job, status="active", version_number=1)

        _refused(lambda: _version(model, job, status="active", version_number=1))
        _version(model, job, status="discarded")
        _version(model, job, status="discarded")


class TestNudgeConstraints:
    def _nudge(self, job, **fields):
        values = {
            "job": job,
            "organization": job.organization,
            "workspace": job.workspace,
            "target": "environment",
        }
        values.update(fields)
        return HarnessNudge.objects.create(**values)

    def test_one_pending_nudge_per_target(self, job):
        self._nudge(job)

        _refused(lambda: self._nudge(job))
        assert self._nudge(job, target="suite").state == "pending"

    def test_one_queued_or_running_nudge_per_environment(self, job):
        self._nudge(job, state="running")

        _refused(lambda: self._nudge(job, target="suite", state="queued"))
        assert self._nudge(job, state="applied").state == "applied"


class TestAgentVersionRuntime:
    @pytest.fixture
    def agent_version(self, organization, workspace):
        definition = AgentDefinition.objects.create(
            agent_name="Versioned agent",
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            description="",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        return definition.create_version(
            description="", commit_message="v1", status=AgentVersion.StatusChoices.DRAFT
        )

    def _runtime(self, agent_version, **fields):
        values = {
            "agent_version": agent_version,
            "agent_definition": agent_version.agent_definition,
            "organization": agent_version.organization,
            "hash": "sha256:" + "2" * 64,
            "source_kind": "github",
            "trigger": "creation",
        }
        values.update(fields)
        return HarnessAgentVersionRuntime.objects.create(**values)

    def test_credential_blob_is_stored_encrypted(
        self, agent_version, set_integration_encryption_key
    ):
        runtime = self._runtime(agent_version, credential_blob='{"env": {"K": "v"}}')
        runtime.refresh_from_db()

        assert runtime.credential_blob.startswith("enc::")
        assert runtime.get_credentials() == '{"env": {"K": "v"}}'

    def test_an_empty_blob_has_no_credentials(self, agent_version):
        assert self._runtime(agent_version).get_credentials() is None

    def test_hash_is_unique_per_definition_until_failed(self, agent_version):
        definition = agent_version.agent_definition
        second = definition.create_version(description="", commit_message="v2")
        third = definition.create_version(description="", commit_message="v3")
        first = self._runtime(agent_version)

        _refused(lambda: self._runtime(second))
        first.preflight_status = "failed"
        first.save()
        assert self._runtime(third).preflight_status == "pending"

    def test_a_soft_deleted_row_does_not_block_the_same_hash(self, agent_version):
        definition = agent_version.agent_definition
        second = definition.create_version(description="", commit_message="v2")
        first = self._runtime(agent_version)
        first.deleted = True
        first.save()

        assert self._runtime(second).deleted is False

    def test_legacy_rows_may_share_a_hash(self, agent_version):
        definition = agent_version.agent_definition
        second = definition.create_version(description="", commit_message="v2")
        live = definition.create_version(description="", commit_message="v3")
        self._runtime(agent_version, preflight_status="legacy")
        self._runtime(second, preflight_status="legacy")

        assert self._runtime(live).preflight_status == "pending"


def test_model_checks_pass_for_the_versioning_tables():
    from django.apps import apps
    from django.core import checks

    from simulate.models import harness_versioning

    found = checks.run_checks(
        app_configs=[apps.get_app_config("simulate")], tags=[checks.Tags.models]
    )

    assert [
        message
        for message in found
        if message.is_serious()
        and getattr(message.obj, "__module__", "") == harness_versioning.__name__
    ] == []
