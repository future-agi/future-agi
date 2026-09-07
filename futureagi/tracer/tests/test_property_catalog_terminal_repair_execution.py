"""Real coordinator, frozen intent, native journals; only remote I/O is simulated."""

from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    NativeWriteUnresolved,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NATIVE_WRITE_ATTEMPT_DIRECTORY,
    NativeQuarantineReason,
    NativeWriteScope,
)
from tracer.services.clickhouse.v2.property_catalog.terminal_repair_execution import (
    NativeTerminalRepairExecutor,
)
from tracer.services.clickhouse.v2.property_catalog.terminal_repair_intent import (
    FrozenTerminalRepairIntent,
)
from tracer.tests.test_property_catalog_native_supersession_receipt import (
    TABLE,
    fixture_setup,
)


def prepare_case(tmp_path, monkeypatch, *, native_uuid=False):
    writer, proof, transport, harness, options = fixture_setup(tmp_path, monkeypatch)
    if native_uuid:
        row = dict(options["rows"][0])
        for field in (
            "organization_id",
            "workspace_id",
            "build_token",
            "producer_stream_id",
        ):
            row[field] = UUID(str(row[field]))
        options = {**options, "rows": (row,)}

    def lose_response(*args, **kwargs):
        kwargs["before_send"]()
        raise TimeoutError("original response lost")

    transport.side_effect = lose_response
    with pytest.raises(TimeoutError, match="original response lost"):
        writer.insert(**options)
    scope = NativeWriteScope.from_rows(TABLE, options["rows"], writer.proof.identity)
    with writer._journal() as journal, journal.scope(scope) as scoped:
        quarantine = scoped.quarantine(
            scoped.capture_quarantine(NativeQuarantineReason.UNRESOLVED_NATIVE_WRITE)
        )
        with scoped.session(TABLE, options["deduplication_token"]) as session:
            original = session.load().encode()
    intent = FrozenTerminalRepairIntent.from_evidence(
        database=writer.database,
        quarantine=quarantine,
        lease=harness.build.lease,
        reservation=options["rows"][0],
        publication_document=None,
        repaired_at=harness.clock.current,
    )
    transport.reset_mock()
    proof.cover.reset_mock()
    proof.settled.reset_mock()
    return writer, proof, transport, harness, intent, original, options


def execution_state(harness, intent):
    key = harness.coordinator._revision_key_for_lease(intent.lease)
    return harness.coordinator._recovery_journal.load_record(
        f"{key}:terminal-repair:{intent.lease.build_lease_sha256}"
    )


def inspect_original(writer, intent, options):
    with (
        writer._journal() as journal,
        journal.scope(intent.binding.quarantine.scope) as scoped,
    ):
        assert scoped.quarantine_binding == intent.binding.quarantine
        with scoped.session(TABLE, options["deduplication_token"]) as session:
            return session.load().encode()


def acknowledge(harness, intent):
    def send(*args, **kwargs):
        key = harness.coordinator._revision_key_for_lease(intent.lease)
        assert harness.coordinator._recovery_journal.is_revoked(
            key, intent.lease.build_lease_sha256
        )
        assert execution_state(harness, intent)["complete"] is False
        kwargs["before_send"]()
        return len(kwargs["values"])

    return send


def test_terminal_execution_revokes_before_sql_and_restarts_without_original_resend(
    tmp_path, monkeypatch
):
    writer, proof, transport, h, intent, original, options = prepare_case(
        tmp_path, monkeypatch
    )
    transport.side_effect = acknowledge(h, intent)
    receipt = NativeTerminalRepairExecutor(writer, h.coordinator).apply(
        intent, timeout_ms=5000
    )
    assert transport.call_count == 1
    assert len(receipt.attempt_record_sha256s) == 1
    assert execution_state(h, intent)["complete"] is True
    assert inspect_original(writer, intent, options) == original
    proof.settled.assert_not_called()
    assert proof.cover.call_count == 2
    assert all(
        call.args[1][0]["status"] == "failed" for call in proof.cover.call_args_list
    )
    h.restart()
    transport.reset_mock()
    same = NativeTerminalRepairExecutor(writer, h.coordinator).apply(
        intent, timeout_ms=5000
    )
    assert same == receipt
    transport.assert_not_called()
    assert inspect_original(writer, intent, options) == original
    assert (
        NativeTerminalRepairExecutor(writer, h.coordinator).confirm(
            intent, timeout_ms=5000
        )
        == receipt
    )
    transport.assert_not_called()


def test_terminal_lost_ack_stays_pending_then_uses_original_query_proof(
    tmp_path, monkeypatch
):
    writer, proof, transport, h, intent, original, options = prepare_case(
        tmp_path, monkeypatch
    )

    def lose(*args, **kwargs):
        acknowledge(h, intent)(*args, **kwargs)
        raise TimeoutError("terminal response lost")

    transport.side_effect = lose
    executor = NativeTerminalRepairExecutor(writer, h.coordinator)
    with pytest.raises(TimeoutError, match="terminal response lost"):
        executor.apply(intent, timeout_ms=5000)
    assert not execution_state(h, intent)["complete"]
    assert transport.call_count == 1
    with pytest.raises(NativeWriteUnresolved, match="outcome is still unresolved"):
        executor.apply(intent, timeout_ms=5000)
    assert transport.call_count == 1
    assert not execution_state(h, intent)["complete"]
    proof.settled.return_value = True
    executor.confirm(intent, timeout_ms=5000)
    assert execution_state(h, intent)["complete"]
    assert transport.call_count == 1
    assert inspect_original(writer, intent, options) == original


def test_cover_failure_never_becomes_terminal_completion(tmp_path, monkeypatch):
    writer, proof, transport, h, intent, original, options = prepare_case(
        tmp_path, monkeypatch
    )
    transport.side_effect = acknowledge(h, intent)
    proof.cover.side_effect = TimeoutError("one member missing")
    executor = NativeTerminalRepairExecutor(writer, h.coordinator)
    with pytest.raises(TimeoutError, match="one member missing"):
        executor.apply(intent, timeout_ms=5000)
    assert not execution_state(h, intent)["complete"]
    assert transport.call_count == 1
    proof.cover.side_effect = None
    executor.confirm(intent, timeout_ms=5000)
    assert execution_state(h, intent)["complete"]
    assert transport.call_count == 1
    assert inspect_original(writer, intent, options) == original


def test_confirm_cannot_start_a_repair_or_dispatch_prepared(tmp_path, monkeypatch):
    writer, proof, transport, h, intent, _, _ = prepare_case(tmp_path, monkeypatch)
    executor = NativeTerminalRepairExecutor(writer, h.coordinator)
    with pytest.raises(NativeWriteUnresolved, match="no frozen execution"):
        executor.confirm(intent, timeout_ms=5000)
    transport.assert_not_called()
    assert execution_state(h, intent) is None
    original_attest = proof.attest.side_effect
    calls = 0

    def fail_second(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError("failed after Prepared")
        return original_attest(**kwargs)

    proof.attest.side_effect = fail_second
    with pytest.raises(TimeoutError, match="failed after Prepared"):
        executor.apply(intent, timeout_ms=5000)
    proof.attest.side_effect = original_attest
    with pytest.raises(NativeWriteUnresolved, match="Prepared native attempt"):
        executor.confirm(intent, timeout_ms=5000)
    assert not execution_state(h, intent)["complete"]
    transport.assert_not_called()
    transport.side_effect = acknowledge(h, intent)
    executor.apply(intent, timeout_ms=5000)
    assert transport.call_count == 1


def test_completion_archive_failure_recovers_exact_receipt_without_sql(
    tmp_path, monkeypatch
):
    writer, _, transport, h, intent, _, _ = prepare_case(tmp_path, monkeypatch)
    transport.side_effect = acknowledge(h, intent)
    store = h.coordinator._recovery_journal
    save = store.save_record

    def fail_completion(key, document):
        if document.get("complete") is True:
            raise OSError("archive fsync interrupted")
        save(key, document)

    monkeypatch.setattr(store, "save_record", fail_completion)
    with pytest.raises(OSError, match="archive fsync interrupted"):
        NativeTerminalRepairExecutor(writer, h.coordinator).apply(
            intent, timeout_ms=5000
        )
    assert not execution_state(h, intent)["complete"]
    assert transport.call_count == 1
    h.restart()
    NativeTerminalRepairExecutor(writer, h.coordinator).confirm(intent, timeout_ms=5000)
    assert execution_state(h, intent)["complete"]
    assert transport.call_count == 1


def test_new_activation_marker_without_publication_does_not_authorize_repair(
    tmp_path, monkeypatch
):
    writer, _, transport, h, intent, _, _ = prepare_case(tmp_path, monkeypatch)
    key = h.coordinator._revision_key_for_lease(intent.lease)
    h.coordinator._recovery_journal.save_record(
        key + ":activation",
        {
            "catalog_revision": intent.lease.catalog_revision,
            "build_token": intent.lease.build_token,
            "build_lease_sha256": intent.lease.build_lease_sha256,
        },
    )
    with pytest.raises(
        NativeWriteUnresolved, match="lacks frozen publication evidence"
    ):
        NativeTerminalRepairExecutor(writer, h.coordinator).apply(
            intent, timeout_ms=5000
        )
    transport.assert_not_called()
    assert execution_state(h, intent) is None


def test_frozen_repair_mutation_on_restart_fails_before_io(tmp_path, monkeypatch):
    writer, proof, transport, h, intent, _, _ = prepare_case(tmp_path, monkeypatch)
    transport.side_effect = acknowledge(h, intent)
    executor = NativeTerminalRepairExecutor(writer, h.coordinator)
    executor.apply(intent, timeout_ms=5000)
    state = execution_state(h, intent)
    state["intent_sha256"] = "a" * 64
    key = h.coordinator._revision_key_for_lease(intent.lease)
    h.coordinator._recovery_journal.save_record(
        f"{key}:terminal-repair:{intent.lease.build_lease_sha256}", state
    )
    transport.reset_mock()
    proof.attest.reset_mock()
    with pytest.raises(NativeWriteUnresolved, match="intent or receipt conflicts"):
        executor.apply(intent, timeout_ms=5000)
    transport.assert_not_called()
    proof.attest.assert_not_called()


def test_source_only_repair_stays_provable_after_successor_publication(
    tmp_path, monkeypatch
):
    writer, _, transport, h, intent, _, _ = prepare_case(tmp_path, monkeypatch)
    transport.side_effect = acknowledge(h, intent)
    executor = NativeTerminalRepairExecutor(writer, h.coordinator)
    receipt = executor.apply(intent, timeout_ms=5000)
    key = h.coordinator._revision_key_for_lease(intent.lease)
    marker = {
        "catalog_revision": intent.lease.catalog_revision + 1,
        "build_token": "99999999-9999-4999-8999-999999999999",
        "build_lease_sha256": "9" * 64,
    }
    h.coordinator._recovery_journal.save_record(key + ":activation", marker)
    transport.reset_mock()
    assert executor.confirm(intent, timeout_ms=5000) == receipt
    transport.assert_not_called()
    assert h.coordinator._recovery_journal.load_record(key + ":activation") == marker


@pytest.mark.parametrize("already_prepared", [False, True])
def test_missing_original_publication_cannot_adopt_same_revision_marker(
    tmp_path, monkeypatch, already_prepared
):
    writer, _, transport, h, intent, _, _ = prepare_case(tmp_path, monkeypatch)
    executor = NativeTerminalRepairExecutor(writer, h.coordinator)
    if already_prepared:
        transport.side_effect = acknowledge(h, intent)
        executor.apply(intent, timeout_ms=5000)
    key = h.coordinator._revision_key_for_lease(intent.lease)
    h.coordinator._recovery_journal.save_record(
        key + ":activation",
        {
            "catalog_revision": intent.lease.catalog_revision,
            "build_token": intent.lease.build_token,
            "build_lease_sha256": intent.lease.build_lease_sha256,
        },
    )
    transport.reset_mock()
    with pytest.raises(
        NativeWriteUnresolved, match="lacks frozen publication evidence"
    ):
        executor.apply(intent, timeout_ms=5000)
    transport.assert_not_called()


def test_fresh_source_only_repair_cannot_infer_old_publication_from_newer_marker(
    tmp_path, monkeypatch
):
    writer, _, transport, h, intent, _, _ = prepare_case(tmp_path, monkeypatch)
    key = h.coordinator._revision_key_for_lease(intent.lease)
    h.coordinator._recovery_journal.save_record(
        key + ":activation",
        {
            "catalog_revision": intent.lease.catalog_revision + 1,
            "build_token": "99999999-9999-4999-8999-999999999999",
            "build_lease_sha256": "9" * 64,
        },
    )
    with pytest.raises(
        NativeWriteUnresolved, match="lacks frozen publication evidence"
    ):
        NativeTerminalRepairExecutor(writer, h.coordinator).apply(
            intent, timeout_ms=5000
        )
    transport.assert_not_called()
    assert execution_state(h, intent) is None


def test_native_uuid_reservation_keeps_exact_typed_bytes_through_repair(
    tmp_path, monkeypatch
):
    writer, proof, transport, h, intent, original, options = prepare_case(
        tmp_path, monkeypatch, native_uuid=True
    )
    assert isinstance(intent.rows[0]["producer_stream_id"], UUID)
    original_bytes = intent.encode()
    transport.side_effect = acknowledge(h, intent)
    executor = NativeTerminalRepairExecutor(writer, h.coordinator)
    receipt = executor.apply(intent, timeout_ms=5000)
    assert execution_state(h, intent)["complete"]
    assert intent.encode() == original_bytes
    assert inspect_original(writer, intent, options) == original
    assert all(
        isinstance(call.args[1][0]["producer_stream_id"], UUID)
        for call in proof.cover.call_args_list
    )
    assert executor.confirm(intent, timeout_ms=5000) == receipt
    assert transport.call_count == 1


@pytest.mark.parametrize("operation", ["apply", "confirm"])
def test_missing_native_journal_is_not_initialized_as_empty_repair(
    tmp_path, monkeypatch, operation
):
    writer, _, transport, h, intent, _, _ = prepare_case(tmp_path, monkeypatch)
    transport.side_effect = acknowledge(h, intent)
    executor = NativeTerminalRepairExecutor(writer, h.coordinator)
    executor.apply(intent, timeout_ms=5000)
    native = writer.directory / NATIVE_WRITE_ATTEMPT_DIRECTORY
    retained = writer.directory / "retained-native-journal"
    native.rename(retained)
    transport.reset_mock()
    with pytest.raises(NativeWriteUnresolved, match="native journal is missing"):
        getattr(executor, operation)(intent, timeout_ms=5000)
    assert not native.exists()
    assert retained.is_dir()
    transport.assert_not_called()
