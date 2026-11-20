"""Explicit binding/value fixtures, not authorization or native-storage proof."""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from django.utils import timezone
from pytest import MonkeyPatch

from tracer.models.attachment import AttachmentOutbox, AttachmentUpload
from tracer.models.attachment_storage import (
    AttachmentStorageBinding,
    binding_identity_sha256,
)
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import (
    EraseResult,
    ObjectIdentity,
    ObjectLocator,
    PutPermit,
    StorageOperation,
    VersionPage,
)

BINDING_ID = UUID("8f82ab45-65d1-4cd5-9db1-111600000001")


def wait_for_db(deadline):
    """Wait for the database clock without changing process time or stored evidence."""
    import time

    from django.db import connection

    stop = time.monotonic() + 95
    while True:
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            remaining = (deadline - cursor.fetchone()[0]).total_seconds()
        if remaining <= 0:
            return
        if remaining > stop - time.monotonic():
            raise RuntimeError("fixture deadline exceeds bounded database wait")
        time.sleep(min(remaining, 2))


def binding_values() -> dict[str, object]:
    """A synthetic immutable resource tuple; never a claim about real buckets."""
    values = {
        "provider": "s3",
        "endpoint": "https://s3.us-east-1.amazonaws.com",
        "region": "us-east-1",
        "account_identity": "owner",
    }
    for purpose in ("quarantine", "serving"):
        bucket = f"private-{purpose}"
        values.update(
            {
                f"{purpose}_bucket": bucket,
                f"{purpose}_resource_identity": f"arn:aws:s3:::{bucket}",
                f"{purpose}_owner_identity": "owner",
                f"{purpose}_created_at": datetime(2025, 1, 1, tzinfo=UTC),
                f"{purpose}_encryption_mode": "AES256",
                f"{purpose}_encryption_key_identity": "",
            }
        )
    return values


def storage_binding() -> AttachmentStorageBinding:
    values = binding_values()
    row, _ = AttachmentStorageBinding.objects.get_or_create(
        id=BINDING_ID,
        defaults={**values, "identity_sha256": binding_identity_sha256(values)},
    )
    assert all(getattr(row, key) == value for key, value in values.items())
    return row


def configure_component_storage(
    monkeypatch: MonkeyPatch, settings: object, tmp_path: Path, store: MemoryObjectStore
) -> None:
    """Replace physical provider/self-test boundaries only; all gates stay real.

    Parser/sandbox execution is still selected explicitly by each test. The
    sandbox module's own qualification tests do not use this component fixture.
    """
    from tracer.services.attachments import sandbox, storage
    from tracer.views import attachment_media

    binding = storage_binding()
    assert store.binding_id == binding.id
    work = tmp_path / "attachment-work"
    work.mkdir(exist_ok=True)
    settings.FI_ATTACHMENTS_WORK_DIR = str(work)
    monkeypatch.setattr(sandbox, "self_test", lambda: None)
    monkeypatch.setattr(storage, "bind_for_admission", lambda *, settings: binding.id)

    def for_binding(binding_id: UUID, *, operation: StorageOperation, settings=None):
        assert binding_id == store.binding_id
        store.verify_identity(operation=operation)
        return store

    monkeypatch.setattr(storage, "for_binding", for_binding)
    monkeypatch.setattr(attachment_media, "for_binding", for_binding)


class MemoryObjectStore:
    """Exact binding/purpose/key/version double with observable bytes and cleanup.

    Deliberately no admission, authority, publication, lease or accounting logic.
    Tests still call the real services and retain their independent expected bytes.
    """

    def __init__(
        self,
        binding_id: UUID = BINDING_ID,
        *,
        quarantine_version: str = "version-1",
        serving_version: str = "immutable-v1",
    ) -> None:
        self.binding_id = binding_id
        self.quarantine_version = quarantine_version
        self.serving_version = serving_version
        self.objects: dict[str, bytes] = {}
        self.identities: dict[str, ObjectIdentity] = {}
        self.promotions: list[bytes] = []

    def verify_identity(self, *, operation: StorageOperation) -> None:
        assert operation in {"admit", "write", "read", "erase"}

    def _locator(self, locator: ObjectLocator | ObjectIdentity) -> None:
        if locator.binding_id != self.binding_id:
            raise AttachmentError("storage_unverified", 503)
        assert locator.purpose in {"quarantine", "serving"}

    def _read(self, identity: ObjectIdentity, expected_length: int) -> bytes:
        self._locator(identity)
        if self.identities.get(identity.key) != identity:
            raise AttachmentError("media_unavailable", 503)
        data = self.objects[identity.key]
        assert len(data) == expected_length
        return data

    def _put(self, permit: PutPermit, data: bytes, version: str) -> ObjectIdentity:
        self._locator(permit.locator)
        if permit.locator.key in self.objects:
            raise AttachmentError("already_exists", 409)
        identity = ObjectIdentity(
            self.binding_id, permit.locator.purpose, permit.locator.key, version
        )
        self.objects[identity.key] = data
        self.identities[identity.key] = identity
        return identity

    def put_quarantine(
        self, permit: PutPermit, stream: BinaryIO, length: int
    ) -> ObjectIdentity:
        assert permit.locator.purpose == "quarantine"
        data = stream.read()
        assert len(data) == length
        return self._put(permit, data, self.quarantine_version)

    def snapshot(
        self, identity: ObjectIdentity, output: BinaryIO, *, expected_length: int
    ) -> str:
        data = self._read(identity, expected_length)
        output.write(data)
        return hashlib.sha256(data).hexdigest()

    def promote(
        self,
        snapshot: BinaryIO,
        *,
        permit: PutPermit,
        expected_length: int,
        expected_sha256: str,
    ) -> ObjectIdentity:
        assert permit.locator.purpose == "serving"
        snapshot.seek(0)
        data = snapshot.read()
        assert len(data) == expected_length
        assert hashlib.sha256(data).hexdigest() == expected_sha256
        result = self._put(permit, data, self.serving_version)
        self.promotions.append(data)
        return result

    def open_range(
        self, identity: ObjectIdentity, *, start: int, end: int, expected_length: int
    ) -> BinaryIO:
        data = self._read(identity, expected_length)
        assert 0 <= start <= end < expected_length
        return io.BytesIO(data[start : end + 1])

    def list_versions(
        self, locator: ObjectLocator, *, cursor: str | None = None, limit: int = 100
    ) -> VersionPage:
        self._locator(locator)
        assert cursor is None and 1 <= limit <= 100
        identity = self.identities.get(locator.key)
        versions = (
            (identity,)
            if identity is not None and identity.purpose == locator.purpose
            else ()
        )
        return VersionPage(versions, (), None)

    def erase_version(self, identity: ObjectIdentity) -> None:
        self._locator(identity)
        if self.identities.get(identity.key) == identity:
            self.identities.pop(identity.key)
            self.objects.pop(identity.key, None)

    def erase_all_versions(self, locator: ObjectLocator) -> EraseResult:
        self._locator(locator)
        for identity in self.list_versions(locator).versions:
            self.erase_version(identity)
        return EraseResult(True, False, None, timezone.now())


def erase_terminal_upload(
    upload: AttachmentUpload, store: MemoryObjectStore, monkeypatch: MonkeyPatch
) -> None:
    """Drive the real terminal eraser after the accepted-write horizon.

    Requires a transaction=True test: no trigger, ledger or timestamp bypass.
    Empty storage before this horizon is not proof of completed cleanup.
    """
    from datetime import timedelta

    from tracer.services.attachments import retention, validation

    event = AttachmentOutbox.objects.get(
        attachment_id=upload.attachment_id, event="erase"
    )
    upload.refresh_from_db()
    wait_for_db(upload.cleanup_not_before)
    # The DB proof clock is real. Advance the application clock for the later
    # tombstone horizon and scheduled validation/erase retries.
    clock = timezone.now() + timedelta(hours=2)
    monkeypatch.setattr(timezone, "now", lambda: clock)
    for _ in range(3):
        for pending in AttachmentOutbox.objects.filter(
            upload=upload,
            event="validate",
            processed_at__isnull=True,
            available_at__lte=clock,
        ):
            validation.consume_validation(pending.pk, storage=store)
        if retention.erase(event.pk, storage=store):
            break
        clock += timedelta(minutes=6)
    event.refresh_from_db()
    assert event.processed_at is not None
    upload.refresh_from_db()
    assert upload.reservation_settled_at is not None
