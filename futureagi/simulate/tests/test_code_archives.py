import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from minio.error import S3Error
from structlog.testing import capture_logs

from simulate.models import CodeArchive
from simulate.services.code_archives import (
    store_commit_archive,
    stored_commit_archive,
)
from simulate.services.hosted_harness import create_hosted_job
from simulate.services.hosted_harness_gateway import HostedSourceAcquirer
from simulate.tests.test_harness_versioning_backfill import _RealisticStorage
from simulate.tests.test_hosted_harness_gateway import _payload

REPOSITORY = "future-agi/reference-agent"
COMMIT = "a" * 40


class _Storage(_RealisticStorage):
    def __init__(self):
        super().__init__()
        self.removed = []
        self.fail_put = False

    def put_object(self, bucket_name, object_name, data, length, content_type=None):
        if self.fail_put:
            raise S3Error(None, "InternalError", "down", object_name, "req", "host")
        super().put_object(bucket_name, object_name, data, length, content_type)

    def remove_object(self, _bucket, object_name):
        self.removed.append(object_name)
        self.objects.pop(object_name, None)


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _Storage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


@pytest.fixture
def clones(monkeypatch):
    runs = []

    def run(command, **_kwargs):
        if command[:2] == ["git", "init"]:
            runs.append(command)
            checkout = Path(command[-1])
            checkout.mkdir(parents=True)
            (checkout / ".git").mkdir()
            (checkout / "agent.py").write_text("print('agent')", encoding="utf-8")
        if "rev-parse" in command:
            return SimpleNamespace(returncode=0, stdout=COMMIT + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("simulate.services.hosted_harness_gateway.subprocess.run", run)
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.shutil.which", lambda _: "git"
    )
    return runs


def _job(organization, key):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key=key)
    return job


def _store(organization, body=b"stored-archive"):
    store_commit_archive(
        organization_id=organization.id,
        repository=REPOSITORY,
        commit_sha=COMMIT,
        body=body,
    )


@pytest.mark.django_db
def test_pinned_commit_is_served_from_the_store_without_git(
    organization, storage, monkeypatch
):
    _store(organization)
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.shutil.which", lambda _: None
    )

    archive, commit_sha = HostedSourceAcquirer().acquire(_job(organization, "stored"))

    assert (archive, commit_sha) == (b"stored-archive", COMMIT)


@pytest.mark.django_db
def test_first_clone_is_stored_and_later_acquires_reuse_it(
    organization, storage, clones
):
    first, _ = HostedSourceAcquirer().acquire(_job(organization, "first"))
    second, _ = HostedSourceAcquirer().acquire(_job(organization, "second"))

    assert first == second
    assert len(clones) == 1
    row = CodeArchive.no_workspace_objects.get()
    assert (row.repository, row.commit_sha, row.size_bytes) == (
        REPOSITORY,
        COMMIT,
        len(first),
    )
    assert row.sha256 == hashlib.sha256(first).hexdigest()


@pytest.mark.django_db
def test_store_is_scoped_to_the_organization(organization, storage):
    _store(organization)

    assert (
        stored_commit_archive(
            organization_id=uuid.uuid4(), repository=REPOSITORY, commit_sha=COMMIT
        )
        is None
    )


@pytest.mark.django_db
def test_storing_a_commit_again_replaces_the_previous_object(organization, storage):
    _store(organization, body=b"first")
    first_key = CodeArchive.no_workspace_objects.get().object_key
    _store(organization, body=b"second")

    assert CodeArchive.no_workspace_objects.count() == 1
    assert storage.removed == [first_key]
    assert list(storage.objects.values()) == [b"second"]
    assert (
        stored_commit_archive(
            organization_id=organization.id, repository=REPOSITORY, commit_sha=COMMIT
        )
        == b"second"
    )


@pytest.mark.django_db
def test_soft_deleted_row_is_revived_by_the_next_store(organization, storage):
    _store(organization, body=b"first")
    CodeArchive.no_workspace_objects.update(deleted=True)
    _store(organization, body=b"second")

    assert (
        stored_commit_archive(
            organization_id=organization.id, repository=REPOSITORY, commit_sha=COMMIT
        )
        == b"second"
    )


@pytest.mark.django_db
def test_storage_failure_on_store_keeps_the_build_going(organization, storage, clones):
    storage.fail_put = True

    with capture_logs() as logs:
        archive, commit_sha = HostedSourceAcquirer().acquire(
            _job(organization, "put-fails")
        )

    assert commit_sha == COMMIT
    assert archive
    assert not CodeArchive.no_workspace_objects.exists()
    assert "code_archive_not_stored" in [log["event"] for log in logs]


@pytest.mark.django_db
def test_missing_stored_object_is_cloned_and_healed(organization, storage, clones):
    _store(organization)
    storage.objects.clear()

    with capture_logs() as logs:
        archive, _ = HostedSourceAcquirer().acquire(_job(organization, "missing"))

    assert archive != b"stored-archive"
    assert len(clones) == 1
    assert "code_archive_unreadable" in [log["event"] for log in logs]
    assert (
        stored_commit_archive(
            organization_id=organization.id, repository=REPOSITORY, commit_sha=COMMIT
        )
        == archive
    )


@pytest.mark.django_db
def test_corrupt_stored_object_is_cloned_and_healed(organization, storage, clones):
    _store(organization)
    key = next(iter(storage.objects))
    storage.objects[key] = b"tampered"

    with capture_logs() as logs:
        archive, _ = HostedSourceAcquirer().acquire(_job(organization, "corrupt"))

    assert archive != b"tampered"
    assert len(clones) == 1
    assert "code_archive_digest_mismatch" in [log["event"] for log in logs]
    assert storage.removed == [key]
    assert (
        stored_commit_archive(
            organization_id=organization.id, repository=REPOSITORY, commit_sha=COMMIT
        )
        == archive
    )
