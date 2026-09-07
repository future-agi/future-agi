"""Crash recovery with real runtime helper, journals, native barrier and capture."""

import fcntl
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog import dev_runtime
from tracer.services.clickhouse.v2.property_catalog.codec import (
    canonical_json,
    canonical_json_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    ClickHouseLifecycleStateReader,
    DurableLifecycleError,
    LifecycleRunMode,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    NativeWriteUnresolved,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeWriteJournal,
    NativeWriteJournalError,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    SharedCatalogDeadline,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_runtime import (
    LifecycleSourceCapture,
    SourceCaptureNeedsReplacement,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import _activation_row
from tracer.tests.test_property_catalog_native_publication import ACTIVE, harness
from tracer.tests.test_property_catalog_source_capture_runtime import (
    PARTS,
    SERVER,
    SOURCE,
    TABLE_UUID,
    Backend,
)


@pytest.fixture
def case(tmp_path, monkeypatch):
    h, writer, proof, native, scope, options, _ = harness(tmp_path, monkeypatch)
    backend = Backend()
    reads, events = [], []

    def metadata():
        reads.append("metadata")
        return {
            "server_uuid": SERVER,
            "table_uuid": TABLE_UUID,
            "create_table_query": SOURCE,
        }

    def bridge():
        return LifecycleSourceCapture(
            directory=str(h.base.directory),
            installation_id=writer.proof.identity.producer_stream_id,
            source_database="default",
            catalog_database=writer.database,
            metadata_loader=metadata,
            live_reader=object(),
            backend_factory=lambda spec, schema: backend,
            reader_factory=lambda spec: object(),
            can_retire=lambda spec: False,
        )

    captured = bridge().bind(h.prepared, started_parts=PARTS)
    record = h.activate().record  # Process dies here, before retiring captured source.
    reader = ClickHouseLifecycleStateReader(
        h.base.client,
        database=writer.database,
        checkpoint_store=SimpleNamespace(
            load_checkpoint_write=lambda **_: pytest.fail("source/checkpoint scan")
        ),
    )

    def runtime():
        current = SimpleNamespace(
            _source_capture=bridge(),
            catalog_client=SimpleNamespace(_durable_writer=writer),
            coordinator=h.base.coordinator,
            state_store=h.store,
            lifecycle_state=reader,
            deadline=SharedCatalogDeadline(wall_ms=30_000),
            bound_request=SimpleNamespace(
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                execute=True,
                status=False,
            ),
            config=SimpleNamespace(
                catalog_epoch=record.catalog_epoch,
                projection_version=record.projection_version,
                project_ids=h.prepared.scope.project_ids,
            ),
            _require_workspace_publication_authorization=lambda: events.append(
                "authorize"
            ),
        )
        current._recover_completed_capture_retirement = MethodType(
            dev_runtime.CheckedInPropertyCatalogDevRuntime._recover_completed_capture_retirement,
            current,
        )
        return current

    return SimpleNamespace(
        h=h,
        writer=writer,
        proof=proof,
        native=native,
        scope=scope,
        options=options,
        record=record,
        captured=captured,
        backend=backend,
        reads=reads,
        events=events,
        runtime=runtime,
        bridge=bridge,
        recover=lambda: runtime()._recover_completed_capture_retirement(
            h.prepared.scope
        ),
    )


def test_completed_capture_reclaimed_on_restart_without_source_read_or_insert(
    case, monkeypatch
):
    c = case
    for name in ("glob", "rglob", "iterdir"):
        monkeypatch.setattr(Path, name, lambda *_: pytest.fail("journal scan"))
    c.proof.cover.reset_mock()
    assert c.recover() == (c.record.build_token,)
    assert c.backend.events.count("drop") == 1
    assert c.proof.cover.call_count == 1
    assert len(c.native.dispatch) == 1
    assert c.reads == ["metadata"]
    c.proof.cover.reset_mock()
    assert c.recover() == ()
    assert not c.proof.cover.called  # Already retired needs no new native proof.


@pytest.mark.parametrize("failure", ["prepared", "sent", "coverage", "receipt"])
def test_unproven_native_evidence_never_retires_capture(case, failure):
    c = case
    before = len(c.native.dispatch)
    if failure == "prepared":
        c.proof.attest.side_effect = RuntimeError("before dispatch")
        with pytest.raises(RuntimeError):
            c.writer.insert(**c.options)
        c.proof.attest.side_effect = None
    elif failure == "sent":
        c.native.loss = True
        with pytest.raises(TimeoutError):
            c.writer.insert(**c.options)
        before += 1
    elif failure == "coverage":
        c.proof.cover.side_effect = RuntimeError("coverage lost")
    else:
        token = f"property-catalog-activation-v1:{c.record.build_token}:{c.record.activation_sha256}"
        with (
            NativeWriteJournal(c.writer.directory) as journal,
            journal.scope(c.scope) as scoped,
        ):
            with scoped.session(ACTIVE, token) as session:
                os.unlink(session._key + ".json", dir_fd=session._directory)
    with pytest.raises((NativeWriteUnresolved, NativeWriteJournalError, RuntimeError)):
        c.recover()
    assert "drop" not in c.backend.events
    assert len(c.native.dispatch) == before
    if failure in {"sent", "coverage"}:
        c.proof.cover.side_effect = None
        c.proof.settled.return_value = True
        assert c.recover() == (c.record.build_token,)
        assert len(c.native.dispatch) == before


@pytest.mark.parametrize(
    "change", ["status", "lease_sha", "binding_scope", "active_payload"]
)
def test_full_record_fenced_lease_and_binding_must_agree(case, change):
    c = case
    if change == "status":
        c.h.base.client.stream_rows[-1]["status"] = "open"
    elif change == "lease_sha":
        c.h.base.client.stream_rows[-1]["build_lease_sha256"] = "f" * 64
    elif change == "active_payload":
        c.h.rows[-1] = _activation_row(
            replace(c.record, value_rows=c.record.value_rows + 1)
        )
    else:
        bridge = c.bridge()
        key = bridge._key(bridge._identity(c.h.prepared))
        binding = bridge._journal.load_record(key)
        binding["identity"]["project_ids"] = []
        bridge._journal.save_record(key, binding)
    with pytest.raises(
        (
            DurableLifecycleError,
            ValueError,
            NativeWriteUnresolved,
            SourceCaptureNeedsReplacement,
        )
    ):
        c.recover()
    assert "drop" not in c.backend.events
    assert len(c.native.dispatch) == 1


def test_original_project_scope_survives_current_allowlist_change(case):
    runtime = case.runtime()
    runtime.config.project_ids = ()
    scope = replace(case.h.prepared.scope, project_ids=())
    assert runtime._recover_completed_capture_retirement(scope) == (
        case.record.build_token,
    )


def test_older_completed_capture_is_not_hidden_by_a_newer_active(case):
    c = case
    manifest = json.loads(c.record.source_manifest_json)
    manifest["lineage_anchor_revision"] = c.record.catalog_revision + 1
    text = canonical_json(manifest)
    newer = replace(
        c.record,
        build_token="ffffffff-ffff-4fff-8fff-ffffffffffff",
        catalog_revision=c.record.catalog_revision + 1,
        activation_sequence=c.record.activation_sequence + 1,
        lineage_anchor_revision=c.record.catalog_revision + 1,
        source_manifest_json=text,
        source_manifest_sha256=canonical_json_sha256(text),
    )
    c.h.rows.append(_activation_row(newer))
    assert c.recover() == (c.record.build_token,)
    assert len(c.native.dispatch) == 1


def test_absent_binding_does_not_consult_capture_index_or_backend(case, monkeypatch):
    c = case
    bridge = c.bridge()
    key = bridge._key(bridge._identity(c.h.prepared))
    bridge._journal._path(key).unlink()
    from tracer.services.clickhouse.v2.property_catalog.source_capture import (
        DurableSourceCapture,
    )

    monkeypatch.setattr(
        DurableSourceCapture,
        "reservation_snapshot",
        lambda *_: pytest.fail("guessed index"),
    )
    c.proof.cover.reset_mock()
    assert c.recover() == ()
    assert "drop" not in c.backend.events and not c.proof.cover.called


def test_legacy_epochless_binding_uses_exact_persisted_lease(case):
    c = case
    bridge = c.bridge()
    key = bridge._key(bridge._identity(c.h.prepared))
    binding = bridge._journal.load_record(key)
    del binding["identity"]["catalog_epoch"]
    bridge._journal.save_record(key, binding)
    assert c.recover() == (c.record.build_token,)


def test_drop_failure_retries_original_retirement_without_recapture(case, monkeypatch):
    c = case
    drop = c.backend.drop_owned
    monkeypatch.setattr(
        c.backend,
        "drop_owned",
        lambda *_: (_ for _ in ()).throw(TimeoutError("drop ACK")),
    )
    with pytest.raises(TimeoutError):
        c.recover()
    monkeypatch.setattr(c.backend, "drop_owned", drop)
    assert c.recover() == (c.record.build_token,)
    assert c.backend.events.count("create") == c.backend.events.count("attach") == 1


def test_drop_runs_under_workspace_build_and_exact_completion_proof(case, monkeypatch):
    c = case
    key = c.h.base.coordinator._revision_key_for_lease(c.h.lease)
    path = c.h.base.directory / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
    original = LifecycleSourceCapture.retire_completed
    observed = []

    def retire(bridge, lease, *, completion_proof):
        fd = os.open(path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        assert completion_proof.require_capture(c.captured.spec) is True
        assert not completion_proof._scoped.pending()
        observed.append(completion_proof)
        original(bridge, lease, completion_proof=completion_proof)

    monkeypatch.setattr(LifecycleSourceCapture, "retire_completed", retire)
    assert c.recover() == (c.record.build_token,)
    with pytest.raises(NativeWriteUnresolved):
        observed[0].require_capture(c.captured.spec)


@pytest.mark.parametrize("coverage_ok", [True, False])
def test_runtime_recovers_after_reader_reconcile_before_new_prepare(
    case, monkeypatch, coverage_ok
):
    c = case
    runtime = c.runtime()
    runtime._execution = None
    runtime._recover_native_writes = lambda _: ()
    runtime._recover_fenced_scope_drift = lambda _: False
    runtime._refresh_project_tenant_authorization = lambda: object()
    runtime.reconcile_reader_selection = lambda: c.events.append("reader")
    runtime._source_repair_scope = lambda: {}
    runtime._notice_preplanning_source_part_changes = lambda _: (
        c.events.append("preplanning") or False
    )
    runtime.config.drain_proof_file = c.h.base.directory / "unused.json"
    runtime.config.span_since = c.h.prepared.cutoffs.span_window.since
    runtime.config.span_until = c.h.prepared.cutoffs.span_window.until
    runtime.now = c.h.base.clock
    monkeypatch.setattr(dev_runtime, "pending_candidate_repair", lambda **_: None)
    monkeypatch.setattr(dev_runtime, "pending_source_repair", lambda **_: None)

    def prepare(**kwargs):
        c.events.append("prepare")
        assert "drop" in c.backend.events
        raise LookupError("stop before new allocation")

    runtime.lifecycle = SimpleNamespace(prepare=prepare)
    if not coverage_ok:
        c.proof.cover.side_effect = RuntimeError("coverage lost")
    with pytest.raises(LookupError if coverage_ok else RuntimeError):
        dev_runtime.CheckedInPropertyCatalogDevRuntime._prepare_revision(
            runtime, LifecycleRunMode.AUTO
        )
    assert c.events[0] == "reader"
    assert ("prepare" in c.events) is coverage_ok
    assert ("preplanning" in c.events) is coverage_ok
    if coverage_ok:
        assert c.events.index("preplanning") < c.events.index("prepare")
