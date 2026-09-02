from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import stat
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    revision_fence_registry as registry_module,
)
from tracer.services.clickhouse.v2.property_catalog.coordinator import (
    ProducerRevisionAssignment,
)
from tracer.services.clickhouse.v2.property_catalog.revision_fence_registry import (
    MAX_REVISION_FENCE_BYTES,
    MAX_REVISION_FENCE_ENTRIES,
    AtomicMultiTenantFenceFile,
    RevisionFenceRegistryError,
    decode_revision_fence_registry,
    encode_revision_fence_registry,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
BUILD = "55555555-5555-4555-8555-555555555555"
NOW = datetime(2026, 8, 14, 12, 1, tzinfo=UTC)

GO_V2_FIXTURE = (
    b'{"format":"futureagi.property-catalog-revision-fence","version":2,'
    b'"fences":[{"organization_id":"11111111-1111-4111-8111-111111111111",'
    b'"workspace_id":"22222222-2222-4222-8222-222222222222",'
    b'"catalog_epoch":1,"catalog_revision":2,"projection_version":1,'
    b'"build_lease_sha256":"74b3c71a4e280b332debb5c25b7f8e50d7d1513ecf4c08f624a0a7be7f0da0c6",'
    b'"build_token":"55555555-5555-4555-8555-555555555555",'
    b'"project_ids":["33333333-3333-4333-8333-333333333333",'
    b'"77777777-7777-4777-8777-777777777777"],'
    b'"span_since_us":1786708800000000,"span_until_us":1786712400000000,'
    b'"issued_at":"2026-08-14 12:00:00.000000",'
    b'"expires_at":"2026-08-14 12:10:00.000000","drain_deadline":"",'
    b'"fenced_sequence":0,"status":"building",'
    b'"fence_sha256":"9883a4f8a4f4032931ab9e4b31d73ec41e3e9f8587ea2c06c4d4db35c6174d0e"}]}'
    b"\n"
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _uuid(value: int) -> str:
    return str(uuid.UUID(int=value))


def _assignment(
    index: int = 1,
    *,
    project_ids: tuple[str, ...] = (PROJECT,),
    issued_at: datetime = NOW - timedelta(minutes=1),
    expires_at: datetime = NOW + timedelta(minutes=9),
    drain_deadline: datetime | None = None,
    fenced_sequence: int = 0,
    status: str = "building",
) -> ProducerRevisionAssignment:
    return ProducerRevisionAssignment(
        organization_id=ORG,
        workspace_id=_uuid(10_000 + index),
        catalog_epoch=1,
        catalog_revision=index,
        projection_version=1,
        build_lease_sha256=_sha(f"lease-{index}"),
        build_token=_uuid(20_000 + index),
        project_ids=project_ids,
        span_since_us=1_786_708_800_000_000,
        span_until_us=1_786_712_400_000_000,
        issued_at=issued_at,
        expires_at=expires_at,
        drain_deadline=drain_deadline,
        fenced_sequence=fenced_sequence,
        status=status,
    )


def _go_fixture_assignment() -> ProducerRevisionAssignment:
    return ProducerRevisionAssignment(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=2,
        projection_version=1,
        build_lease_sha256=(
            "74b3c71a4e280b332debb5c25b7f8e50d7d1513ecf4c08f624a0a7be7f0da0c6"
        ),
        build_token=BUILD,
        project_ids=(PROJECT, "77777777-7777-4777-8777-777777777777"),
        span_since_us=1_786_708_800_000_000,
        span_until_us=1_786_712_400_000_000,
        issued_at=datetime(2026, 8, 14, 12, tzinfo=UTC),
        expires_at=datetime(2026, 8, 14, 12, 10, tzinfo=UTC),
        drain_deadline=None,
        fenced_sequence=0,
        status="building",
    )


def _publish_in_process(path: str, index: int, start: object) -> None:
    start.wait(30)  # type: ignore[attr-defined]
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    registry.publish(_assignment(index))


def _reconcile_in_process(
    path: str,
    workspace_ids: tuple[str, ...],
    start: object,
) -> None:
    start.wait(30)  # type: ignore[attr-defined]
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    registry.reconcile_authorized_workspaces(workspace_ids)


def test_python_registry_bytes_match_go_file_revision_provider_fixture() -> None:
    assignment = _go_fixture_assignment()
    raw = encode_revision_fence_registry((assignment,), now=NOW)

    assert raw == GO_V2_FIXTURE
    assert decode_revision_fence_registry(raw, now=NOW) == (assignment.document,)


def test_registry_preserves_other_tenants_sorts_and_replaces_only_target(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revision-fence.json"
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    later = _assignment(2)
    earlier = _assignment(1)
    registry.publish(later)
    registry.publish(earlier)

    before = decode_revision_fence_registry(path.read_bytes(), now=NOW)
    assert [(row["organization_id"], row["workspace_id"]) for row in before] == [
        (ORG, earlier.workspace_id),
        (ORG, later.workspace_id),
    ]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(registry.lock_path.stat().st_mode) == 0o600

    replacement = replace(
        earlier,
        catalog_revision=101,
        fence_sha256="0" * 64,
    )
    registry.publish(replacement)
    after = decode_revision_fence_registry(path.read_bytes(), now=NOW)

    assert len(after) == 2
    assert (
        next(row for row in after if row["workspace_id"] == earlier.workspace_id)[
            "catalog_revision"
        ]
        == 101
    )
    assert (
        next(row for row in after if row["workspace_id"] == later.workspace_id)
        == later.document
    )


def test_reconcile_removes_deauthorized_workspaces_and_unlinks_empty_registry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revision-fence.json"
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    retained = _assignment(1)
    removed = _assignment(2)
    registry.publish(retained)
    registry.publish(removed)

    assert registry.reconcile_authorized_workspaces((retained.workspace_id,)) == 1
    assert decode_revision_fence_registry(path.read_bytes(), now=NOW) == (
        retained.document,
    )

    assert registry.reconcile_authorized_workspaces((_uuid(99_999),)) == 1
    assert not path.exists()


def test_reconcile_retains_authorized_live_and_terminal_fences(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revision-fence.json"
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    live = _assignment(1)
    terminal = _assignment(
        2,
        status="fenced",
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW - timedelta(minutes=1),
        drain_deadline=NOW - timedelta(seconds=1),
        fenced_sequence=9,
    )
    deauthorized_terminal = _assignment(
        3,
        status="fenced",
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW - timedelta(minutes=1),
        drain_deadline=NOW - timedelta(seconds=1),
        fenced_sequence=11,
    )
    registry.publish(live)
    registry.publish(terminal)
    registry.publish(deauthorized_terminal)

    assert (
        registry.reconcile_authorized_workspaces(
            (live.workspace_id, terminal.workspace_id)
        )
        == 1
    )
    documents = decode_revision_fence_registry(path.read_bytes(), now=NOW)
    assert [(value["workspace_id"], value["status"]) for value in documents] == [
        (live.workspace_id, "building"),
        (terminal.workspace_id, "fenced"),
    ]


def test_corruption_fails_closed_without_overwriting_existing_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revision-fence.json"
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    registry.publish(_assignment(1))
    corrupt = path.read_bytes().replace(
        b'"catalog_revision":1',
        b'"catalog_revision":99',
    )
    path.write_bytes(corrupt)
    path.chmod(0o600)

    with pytest.raises(RevisionFenceRegistryError, match="digest"):
        registry.publish(_assignment(2))

    assert path.read_bytes() == corrupt
    assert not tuple(tmp_path.glob(".property-catalog-fence-registry-*"))


def test_atomic_replace_failure_preserves_old_file_and_cleans_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "revision-fence.json"
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    registry.publish(_assignment(1))
    original = path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        _ = source, destination
        raise OSError("injected replace failure")

    monkeypatch.setattr(registry_module.os, "replace", fail_replace)
    with pytest.raises(RevisionFenceRegistryError, match="update failed"):
        registry.publish(_assignment(2))

    assert path.read_bytes() == original
    assert not tuple(tmp_path.glob(".property-catalog-fence-registry-*"))


def test_publish_fsyncs_file_then_containing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "revision-fence.json"
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    original_fsync = registry_module.os.fsync
    fsync_targets: list[str] = []

    def recording_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        fsync_targets.append("directory" if stat.S_ISDIR(mode) else "file")
        original_fsync(descriptor)

    monkeypatch.setattr(registry_module.os, "fsync", recording_fsync)
    registry.publish(_assignment(1))

    assert fsync_targets == ["file", "directory"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("workspace_id", "not-a-uuid", "workspace_id"),
        ("catalog_revision", 0, "positive UInt64"),
        ("status", "active", "status"),
        ("fence_sha256", "a" * 64, "digest"),
    ),
)
def test_decoder_rejects_invalid_workspace_revision_status_and_hash(
    field: str,
    value: object,
    message: str,
) -> None:
    document = json.loads(encode_revision_fence_registry((_assignment(),), now=NOW))
    document["fences"][0][field] = value
    raw = json.dumps(document, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(RevisionFenceRegistryError, match=message):
        decode_revision_fence_registry(raw, now=NOW)


def test_registry_accepts_protocol_maximum_and_rejects_overwide_live_fences(
    tmp_path: Path,
) -> None:
    registry = AtomicMultiTenantFenceFile(
        tmp_path / "revision-fence.json",
        now=lambda: NOW,
    )
    expired = _assignment(
        issued_at=NOW - timedelta(minutes=3),
        expires_at=NOW - timedelta(minutes=1),
    )
    with pytest.raises(RevisionFenceRegistryError, match="expired"):
        registry.publish(expired)

    expired_drain = _assignment(
        status="draining",
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(minutes=5),
        drain_deadline=NOW - timedelta(seconds=1),
    )
    with pytest.raises(RevisionFenceRegistryError, match="expired"):
        registry.publish(expired_drain)

    maximum = _assignment(
        status="draining",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=59),
        drain_deadline=NOW + timedelta(minutes=59),
    )
    registry.publish(maximum)
    assert decode_revision_fence_registry(
        registry.path.read_bytes(),
        now=NOW,
    ) == (maximum.document,)

    overwide = _assignment(
        status="draining",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=59, microseconds=1),
        drain_deadline=NOW + timedelta(minutes=59, microseconds=1),
    )
    with pytest.raises(RevisionFenceRegistryError, match="too wide"):
        registry.publish(overwide)


def test_publish_recovers_only_proven_expired_live_entries(tmp_path: Path) -> None:
    path = tmp_path / "revision-fence.json"
    expired_building = _assignment(
        1,
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW - timedelta(minutes=1),
    )
    expired_draining = _assignment(
        2,
        status="draining",
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW - timedelta(minutes=1),
        drain_deadline=NOW - timedelta(seconds=1),
        fenced_sequence=7,
    )
    live_fenced = _assignment(
        3,
        status="fenced",
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW - timedelta(minutes=1),
        drain_deadline=NOW - timedelta(seconds=1),
        fenced_sequence=9,
    )
    path.write_bytes(
        encode_revision_fence_registry(
            (expired_building, expired_draining, live_fenced),
            now=NOW - timedelta(minutes=2),
        )
    )
    path.chmod(0o600)

    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    incoming = _assignment(4)
    registry.publish(incoming)

    documents = decode_revision_fence_registry(path.read_bytes(), now=NOW)
    assert [document["workspace_id"] for document in documents] == sorted(
        (live_fenced.workspace_id, incoming.workspace_id)
    )


def test_stale_registry_recovery_still_rejects_corruption(tmp_path: Path) -> None:
    path = tmp_path / "revision-fence.json"
    stale = _assignment(
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW - timedelta(minutes=1),
    )
    raw = encode_revision_fence_registry(
        (stale,),
        now=NOW - timedelta(minutes=2),
    ).replace(b'"catalog_revision":1', b'"catalog_revision":99')
    path.write_bytes(raw)
    path.chmod(0o600)

    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    with pytest.raises(RevisionFenceRegistryError, match="digest"):
        registry.publish(_assignment(2))

    assert path.read_bytes() == raw


def test_scope_reconciliation_does_not_overwrite_corrupt_registry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revision-fence.json"
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    registry.publish(_assignment(1))
    corrupt = path.read_bytes().replace(b'"status":"building"', b'"status":"active"')
    path.write_bytes(corrupt)
    path.chmod(0o600)

    with pytest.raises(RevisionFenceRegistryError, match="status"):
        registry.reconcile_authorized_workspaces(())

    assert path.read_bytes() == corrupt


def test_scope_reconciliation_accepts_authorization_inventory_larger_than_registry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revision-fence.json"
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    retained = _assignment(1)
    deauthorized = _assignment(2)
    registry.publish(retained)
    registry.publish(deauthorized)
    authorized = (
        retained.workspace_id,
        *(
            _uuid(50_000 + index)
            for index in range(MAX_REVISION_FENCE_ENTRIES + 1)
        ),
    )

    assert len(authorized) > MAX_REVISION_FENCE_ENTRIES
    assert registry.reconcile_authorized_workspaces(authorized) == 1
    assert decode_revision_fence_registry(path.read_bytes(), now=NOW) == (
        retained.document,
    )


def test_entry_and_byte_limits_fail_without_replacing_registry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revision-fence.json"
    assignments = tuple(
        _assignment(index) for index in range(1, MAX_REVISION_FENCE_ENTRIES + 1)
    )
    original = encode_revision_fence_registry(assignments, now=NOW)
    path.write_bytes(original)
    path.chmod(0o600)
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)

    with pytest.raises(RevisionFenceRegistryError, match="1..256"):
        registry.publish(_assignment(MAX_REVISION_FENCE_ENTRIES + 1))
    assert path.read_bytes() == original

    assert (
        registry.reconcile_authorized_workspaces((assignments[0].workspace_id,))
        == MAX_REVISION_FENCE_ENTRIES - 1
    )
    reclaimed = _assignment(MAX_REVISION_FENCE_ENTRIES + 1)
    registry.publish(reclaimed)
    assert len(decode_revision_fence_registry(path.read_bytes(), now=NOW)) == 2

    projects = tuple(_uuid(100_000 + index) for index in range(256))
    oversized = tuple(
        _assignment(index, project_ids=projects) for index in range(1, 111)
    )
    with pytest.raises(RevisionFenceRegistryError, match="1MiB"):
        encode_revision_fence_registry(oversized, now=NOW)

    oversized_bytes = b"x" * (MAX_REVISION_FENCE_BYTES + 1)
    path.write_bytes(oversized_bytes)
    path.chmod(0o600)
    with pytest.raises(RevisionFenceRegistryError, match="size"):
        registry.publish(_assignment(1))
    assert path.read_bytes() == oversized_bytes


def test_concurrent_processes_preserve_every_tenant_update(tmp_path: Path) -> None:
    path = tmp_path / "revision-fence.json"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    processes = [
        context.Process(
            target=_publish_in_process,
            args=(str(path), index, start),
        )
        for index in range(1, 9)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(45)

    assert [process.exitcode for process in processes] == [0] * len(processes)
    documents = decode_revision_fence_registry(path.read_bytes(), now=NOW)
    assert len(documents) == len(processes)
    assert [document["workspace_id"] for document in documents] == sorted(
        _assignment(index).workspace_id for index in range(1, 9)
    )
    assert os.stat(path).st_size <= MAX_REVISION_FENCE_BYTES


def test_concurrent_publish_and_scope_reconcile_share_process_lock(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revision-fence.json"
    registry = AtomicMultiTenantFenceFile(path, now=lambda: NOW)
    retained = _assignment(1)
    removed = _assignment(2)
    incoming = _assignment(3)
    registry.publish(retained)
    registry.publish(removed)

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    processes = (
        context.Process(
            target=_publish_in_process,
            args=(str(path), 3, start),
        ),
        context.Process(
            target=_reconcile_in_process,
            args=(
                str(path),
                (retained.workspace_id, incoming.workspace_id),
                start,
            ),
        ),
    )
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(45)

    assert [process.exitcode for process in processes] == [0, 0]
    documents = decode_revision_fence_registry(path.read_bytes(), now=NOW)
    assert [document["workspace_id"] for document in documents] == [
        retained.workspace_id,
        incoming.workspace_id,
    ]
