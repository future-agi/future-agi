"""Real workspace locks, native journals/writer and terminal executor; offline I/O."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import native_recovery as subject
from tracer.services.clickhouse.v2.property_catalog import (
    publication_journal as publication,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    WorkspaceCatalogScope,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    NativeWriteUnresolved,
)
from tracer.services.clickhouse.v2.property_catalog.native_publication import (
    NativePublicationBarrier,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeQuarantineReason,
    NativeWriteJournalError,
    NativeWriteScope,
    NativeWriteScopeSession,
    _key,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    _COLUMNS,
    NativeWriteProofError,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import _activation_row
from tracer.services.clickhouse.v2.property_catalog.terminal_repair_intent import (
    TerminalRepairIntentError,
)
from tracer.tests.test_property_catalog_native_publication import (
    harness as publication_harness,
)
from tracer.tests.test_property_catalog_native_supersession_receipt import (
    fixture_setup,
    options_for,
)
from tracer.tests.test_property_catalog_native_write_proof import row_for
from tracer.tests.test_property_catalog_terminal_repair_intent import (
    _rehash_publication,
    publication_evidence,
)

SOURCE = "property_catalog_source_streams"
ACTIVE = "property_catalog_activations"
DEFINITION = "property_definition_catalog"
CONTROL = "property_catalog_activation_control_events"


def send(*args, **kwargs):
    kwargs["before_send"]()
    return len(kwargs["values"])


def lose(*args, **kwargs):
    kwargs["before_send"]()
    raise TimeoutError("original ACK lost")


def workspace(lease):
    return WorkspaceCatalogScope(
        lease.organization_id,
        lease.workspace_id,
        lease.catalog_epoch,
        lease.projection_version,
        lease.build_plan.source_scope.project_ids,
    )


@pytest.fixture
def case(tmp_path, monkeypatch):
    writer, proof, transport, h, options = fixture_setup(tmp_path, monkeypatch)
    transport.side_effect = send
    return SimpleNamespace(
        writer=writer,
        proof=proof,
        transport=transport,
        h=h,
        options=options,
        scope=workspace(h.build.lease),
        lease=h.build.lease,
        now=h.build.lease.expires_at,
    )


def recover(c, *, now=None):
    return subject.recover_workspace(
        c.writer, c.h.coordinator, c.scope, c.now if now is None else now, 5000
    )


def native_scope(c):
    return NativeWriteScope.from_rows(
        SOURCE, c.options["rows"], c.writer.proof.identity
    )


def candidates(c):
    with c.writer._journal() as journal:
        return journal.recovery_scopes(
            **{name: getattr(c.scope, name) for name in subject._WORKSPACE}
        )


def attempt(c, table=SOURCE, token=None):
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        with scoped.session(
            table, token or c.options["deduplication_token"]
        ) as session:
            return session.load()


def request(c, table, token):
    row = row_for(table)
    for name in subject._WORKSPACE:
        if name in row:
            row[name] = getattr(c.scope, name)
    if table == CONTROL:
        row.update(
            target_catalog_revision=c.lease.catalog_revision,
            target_build_token=c.lease.build_token,
        )
    else:
        row.update(
            catalog_revision=c.lease.catalog_revision, build_token=c.lease.build_token
        )
    return {
        **c.options,
        "table": f"`{c.writer.database}`.`{table}`",
        "rows": (row,),
        "columns": _COLUMNS[table],
        "deduplication_token": token,
    }


def pending(c, options=None, *, prepared=False):
    options = c.options if options is None else options
    if prepared:
        original = c.proof.attest.side_effect
        c.proof.attest.side_effect = TimeoutError("before send")
        with pytest.raises(TimeoutError, match="before send"):
            c.writer.insert(**options)
        c.proof.attest.side_effect = original
    else:
        c.transport.side_effect = lose
        with pytest.raises(TimeoutError, match="ACK lost"):
            c.writer.insert(**options)
        c.transport.side_effect = send


def archive(c, lease=None):
    lease = c.lease if lease is None else lease
    key = c.h.coordinator._revision_key_for_lease(lease)
    return c.h.coordinator._recovery_journal.load_record(
        f"{key}:terminal-repair:{lease.build_lease_sha256}"
    )


def test_fresh_workspace_does_not_initialize_native_journal(case):
    path = case.writer.directory / "native-write-attempts"
    assert not path.exists()
    assert recover(case) == ()
    assert not path.exists()
    case.transport.assert_not_called()


@pytest.mark.parametrize("native_uuid", [False, True])
def test_initial_source_lost_ack_before_execution_uses_frozen_seed(
    case, monkeypatch, native_uuid
):
    c = case
    if native_uuid:
        row = dict(c.options["rows"][0])
        for field in (
            "organization_id",
            "workspace_id",
            "build_token",
            "producer_stream_id",
        ):
            row[field] = UUID(row[field])
        c.options = {**c.options, "rows": (row,)}
    pending(c)
    original = attempt(c).encode()
    c.h.client.stream_rows.clear()
    monkeypatch.setattr(
        c.h.coordinator,
        "_read_stream",
        lambda **_: pytest.fail("visible-head reconstruction"),
    )
    c.transport.reset_mock()
    ((intent, receipt),) = recover(c)
    assert intent.lease == c.lease
    assert intent.binding == receipt.binding
    assert intent.publication_document is None
    assert intent.reservation["status"] == "open"
    assert intent.rows[0]["status"] == "failed"
    assert attempt(c).encode() == original
    assert c.transport.call_count == 1
    assert archive(c)["complete"] is True
    c.h.restart()
    c.transport.reset_mock()
    c.proof.settled.reset_mock()
    assert recover(c, now=c.now + timedelta(days=1)) == ((intent, receipt),)
    c.transport.assert_not_called()
    c.proof.settled.assert_not_called()
    assert attempt(c).encode() == original


def test_positive_original_proof_prunes_without_repair_or_resend(case):
    c = case
    pending(c)
    c.proof.settled.return_value = True
    c.transport.reset_mock()
    assert recover(c) == ()
    assert attempt(c).state == "complete"
    assert candidates(c) == ()
    assert archive(c) is None
    c.transport.assert_not_called()


def test_live_prepared_initial_blocks_new_allocation_then_expiry_repairs(case):
    c = case
    c.now = c.lease.issued_at + timedelta(seconds=1)
    pending(c, prepared=True)
    original = attempt(c).encode()
    with pytest.raises(NativeWriteUnresolved, match="Prepared initial reservation"):
        recover(c)
    assert archive(c) is None
    assert attempt(c).encode() == original
    c.transport.assert_not_called()
    ((intent, _),) = recover(c, now=c.lease.expires_at)
    assert intent.binding.quarantine.reason is NativeQuarantineReason.BUILD_SUPERSEDED
    assert attempt(c).encode() == original
    assert c.transport.call_count == 1


def test_live_prepared_definition_returns_to_original_lifecycle(case):
    c = case
    c.now = c.lease.issued_at + timedelta(seconds=1)
    c.writer.insert(**c.options)
    pending(c, request(c, DEFINITION, "definition-prepared"), prepared=True)
    c.transport.reset_mock()
    assert recover(c) == ()
    assert archive(c) is None
    assert candidates(c) == (native_scope(c),)
    assert attempt(c, DEFINITION, "definition-prepared").state == "prepared"
    c.transport.assert_not_called()


def test_completed_initial_seed_survives_pending_cleanup_and_definition_failure(case):
    c = case
    c.writer.insert(**c.options)
    assert attempt(c).state == "complete"
    pending(c, request(c, DEFINITION, "definition-sent"))
    original = attempt(c, DEFINITION, "definition-sent").encode()
    ((intent, _),) = recover(c)
    assert intent.lease == c.lease
    assert [entry.table for entry in intent.binding.quarantine.pending] == [DEFINITION]
    assert attempt(c, DEFINITION, "definition-sent").encode() == original


@pytest.mark.parametrize("initial", [False, True])
def test_live_unresolved_sent_blocks_until_lease_expiry(case, initial):
    c = case
    c.now = c.lease.expires_at - timedelta(microseconds=1)
    options = c.options
    if not initial:
        c.writer.insert(**options)
        options = request(c, DEFINITION, "definition-sent")
    pending(c, options)
    table = SOURCE if initial else DEFINITION
    original = attempt(c, table, options["deduplication_token"]).encode()
    c.transport.reset_mock()
    with pytest.raises(NativeWriteUnresolved, match="unexpired native build"):
        recover(c)
    assert archive(c) is None
    assert candidates(c) == (native_scope(c),)
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert scoped.quarantine_binding is None
    c.transport.assert_not_called()
    ((intent, _),) = recover(c, now=c.lease.expires_at)
    assert (
        intent.binding.quarantine.reason
        is NativeQuarantineReason.UNRESOLVED_NATIVE_WRITE
    )
    assert attempt(c, table, options["deduplication_token"]).encode() == original


def test_live_prepared_cannot_hide_unresolved_sent_sibling(case):
    c = case
    c.now = c.lease.issued_at + timedelta(seconds=1)
    c.writer.insert(**c.options)
    pending(c, request(c, DEFINITION, "definition-prepared"), prepared=True)
    pending(c, request(c, DEFINITION, "definition-sent"))
    c.transport.reset_mock()
    with pytest.raises(NativeWriteUnresolved, match="unexpired native build"):
        recover(c)
    assert c.proof.settled.call_count == 1
    assert archive(c) is None
    assert attempt(c, DEFINITION, "definition-prepared").state == "prepared"
    assert attempt(c, DEFINITION, "definition-sent").state == "sent"
    c.transport.assert_not_called()
    ((intent, _),) = recover(c, now=c.lease.expires_at)
    assert len(intent.binding.quarantine.pending) == 2


def test_positive_pass_continues_after_first_unresolved_attempt(case):
    c = case
    c.writer.insert(**c.options)
    tokens = sorted(
        ("definition-a", "definition-b"), key=lambda token: _key(DEFINITION, token)
    )
    for token in tokens:
        pending(c, request(c, DEFINITION, token))
    c.proof.settled.side_effect = lambda original, **_: (
        original["deduplication_token"] == tokens[1]
    )
    ((intent, _),) = recover(c)
    assert c.proof.settled.call_count == 2
    assert attempt(c, DEFINITION, tokens[1]).state == "complete"
    assert [
        entry.deduplication_token for entry in intent.binding.quarantine.pending
    ] == [tokens[0]]


def test_prepared_entry_does_not_prevent_positive_proof_of_later_sent(case):
    c = case
    c.now = c.lease.issued_at + timedelta(seconds=1)
    c.writer.insert(**c.options)
    tokens = sorted(
        ("definition-a", "definition-b"), key=lambda token: _key(DEFINITION, token)
    )
    pending(c, request(c, DEFINITION, tokens[0]), prepared=True)
    pending(c, request(c, DEFINITION, tokens[1]))
    c.proof.settled.return_value = True
    assert recover(c) == ()
    assert attempt(c, DEFINITION, tokens[1]).state == "complete"
    assert attempt(c, DEFINITION, tokens[0]).state == "prepared"
    assert archive(c) is None


def test_reader_control_attempt_vetoes_automatic_build_termination(case):
    c = case
    c.writer.insert(**c.options)
    pending(c, request(c, DEFINITION, "definition-sent"))
    pending(c, request(c, CONTROL, "manual-rollback"))
    c.transport.reset_mock()
    with pytest.raises(NativeWriteUnresolved, match="reader-control"):
        recover(c)
    assert c.proof.settled.call_count == 2
    assert archive(c) is None
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert scoped.quarantine_binding is None
    c.transport.assert_not_called()


def test_archive_before_quarantine_crash_reuses_original_repaired_at(case, monkeypatch):
    c = case
    pending(c)
    quarantine = NativeWriteScopeSession.quarantine
    with monkeypatch.context() as patch:
        patch.setattr(
            NativeWriteScopeSession,
            "quarantine",
            lambda *a: (_ for _ in ()).throw(OSError("quarantine fsync")),
        )
        with pytest.raises(OSError, match="quarantine fsync"):
            recover(c)
    frozen = subject.FrozenTerminalRepairIntent.decode(
        subject._canonical(archive(c)["intent"]) + b"\n"
    )
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert scoped.quarantine_binding is None
    assert NativeWriteScopeSession.quarantine is quarantine
    ((restored, _),) = recover(c, now=c.now + timedelta(days=1))
    assert restored.encode() == frozen.encode()


def test_terminal_ack_loss_resumes_exact_terminal_before_original_classification(case):
    c = case
    pending(c)
    original = attempt(c).encode()
    c.transport.side_effect = lose
    with pytest.raises(TimeoutError, match="ACK lost"):
        recover(c)
    frozen = archive(c)["intent"]
    c.proof.settled.side_effect = lambda original, **_: original[
        "deduplication_token"
    ].startswith("property-catalog-terminal-repair-v1:")
    c.transport.reset_mock()
    ((intent, _),) = recover(c, now=c.now + timedelta(hours=1))
    assert subject.json.loads(intent.encode()) == frozen
    c.transport.assert_not_called()
    assert attempt(c).encode() == original


@pytest.mark.parametrize("newer", [False, True])
def test_fresh_missing_publication_guard_precedes_archive(case, newer):
    c = case
    pending(c)
    key = c.h.coordinator._revision_key_for_lease(c.lease)
    c.h.coordinator._recovery_journal.save_record(
        key + ":activation",
        {
            "catalog_revision": c.lease.catalog_revision + int(newer),
            "build_token": c.lease.build_token,
            "build_lease_sha256": c.lease.build_lease_sha256,
        },
    )
    c.transport.reset_mock()
    with pytest.raises(NativeWriteUnresolved, match="publication/marker"):
        recover(c)
    assert archive(c) is None
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert scoped.quarantine_binding is None
    c.transport.assert_not_called()


def test_missing_seed_blocks_repair_instead_of_reading_current_head(case):
    c = case
    pending(c)
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        (
            c.writer.directory / "native-write-attempts" / f"{scoped._key}.reservation"
        ).unlink()
    with pytest.raises(NativeWriteUnresolved, match="original reservation"):
        recover(c)
    assert archive(c) is None


def test_missing_receipt_is_not_classified_as_failed_remote_write(case):
    c = case
    pending(c)
    (
        c.writer.directory
        / "native-write-attempts"
        / (_key(SOURCE, c.options["deduplication_token"]) + ".json")
    ).unlink()
    with pytest.raises(NativeWriteJournalError):
        recover(c)
    assert archive(c) is None


def test_admission_failure_cannot_become_terminal_authorization(case):
    c = case
    pending(c)
    c.proof.attest.side_effect = NativeWriteProofError("admission changed")
    with pytest.raises(NativeWriteProofError, match="admission changed"):
        recover(c)
    assert archive(c) is None


def test_workspace_index_cannot_select_other_workspace(case):
    c = case
    pending(c)
    other = replace(c.scope, workspace_id=str(UUID(int=99)))
    assert (
        subject.recover_workspace(c.writer, c.h.coordinator, other, c.now, 5000) == ()
    )
    assert attempt(c).state == "sent"
    assert archive(c) is None


def test_original_active_becomes_exact_disabled_then_failed(case, tmp_path):
    c = case
    directory = tmp_path / "publication"
    directory.mkdir()
    h, evidence = publication_evidence(directory)
    document = evidence["publication_document"]
    document["database"] = c.writer.database
    _rehash_publication(document)
    h.base.client.catalog_database = c.writer.database
    h.base.restart()
    h.base.coordinator._recovery_journal.save_record(h.key(), document)
    row = next(
        row
        for batch in h.base.client.inserts
        for row in batch
        if row["build_token"] == h.lease.build_token
        and row["envelope_version"] == 0
        and row["status"] == "open"
    )
    c.h, c.lease, c.scope = h.base, h.lease, workspace(h.lease)
    c.now = h.lease.expires_at
    c.options = {
        **c.options,
        "rows": (row,),
        "deduplication_token": "original-reservation",
    }
    c.writer.insert(**c.options)
    record = publication._record(document["publication"]["record"])
    token = f"property-catalog-activation-v1:{record.build_token}:{record.activation_sha256}"
    options = {
        **c.options,
        "table": f"`{c.writer.database}`.`{ACTIVE}`",
        "rows": (_activation_row(record),),
        "columns": _COLUMNS[ACTIVE],
        "deduplication_token": token,
    }
    pending(c, options)
    original = attempt(c, ACTIVE, token).encode()
    c.transport.reset_mock()
    ((intent, receipt),) = recover(c)
    assert [write.table for write in receipt.binding.writes] == [ACTIVE, SOURCE]
    assert intent.publication_document == document
    assert intent.rows[0]["activation_sequence"] == record.activation_sequence
    assert intent.rows[0]["_version"] == (1 << 64) - 1
    assert c.transport.call_count == 2
    assert attempt(c, ACTIVE, token).encode() == original
    assert c.h.coordinator._recovery_journal.load_record(h.key()) == document


def closure_case(tmp_path, monkeypatch, *, state="complete"):
    h, writer, proof, native, _, _, _ = publication_harness(tmp_path, monkeypatch)
    row = next(
        row
        for batch in h.base.client.inserts
        for row in batch
        if row["build_token"] == h.lease.build_token
        and row["envelope_version"] == 0
        and row["status"] == "open"
    )
    options = options_for(writer, row)
    writer.insert(**options)
    if state == "unsent":
        monkeypatch.setattr(
            h.client,
            "insert",
            lambda *a, **k: (_ for _ in ()).throw(TimeoutError("before prepare")),
        )
    native.loss = state == "sent"
    if state == "complete":
        h.activate()
    else:
        with pytest.raises(TimeoutError):
            h.activate()
    native.loss = False
    record = publication._record(h.document()["publication"]["record"])
    c = SimpleNamespace(
        writer=writer,
        proof=proof,
        h=h.base,
        lease=h.lease,
        scope=workspace(h.lease),
        now=h.lease.expires_at,
        options=options,
        record=record,
        token=f"property-catalog-activation-v1:{record.build_token}:{record.activation_sha256}",
    )
    return c, h, native


def lose_original_active(table, rows, **kwargs):
    if table == ACTIVE and rows[0]["status"] == "active":
        raise NativeWriteProofError("original ACTIVE no longer covered")


def test_empty_pending_complete_active_reproved_before_pointer_removal(
    tmp_path, monkeypatch
):
    c, h, native = closure_case(tmp_path, monkeypatch)
    original = attempt(c, ACTIVE, c.token).encode()
    c.proof.cover.reset_mock()
    c.proof.settled.reset_mock()
    sent = tuple(native.dispatch)
    assert recover(c) == ()
    assert candidates(c) == ()
    assert c.proof.cover.call_count == 1
    assert c.proof.cover.call_args.args == (ACTIVE, attempt(c, ACTIVE, c.token).rows)
    c.proof.settled.assert_not_called()
    assert tuple(native.dispatch) == sent
    assert attempt(c, ACTIVE, c.token).encode() == original
    assert archive(c) is None


def test_completion_proof_loss_retracks_empty_scope_and_blocks_until_expiry(
    tmp_path, monkeypatch
):
    c, h, native = closure_case(tmp_path, monkeypatch)
    original = attempt(c, ACTIVE, c.token).encode()
    document = h.document()
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert scoped.pending() == ()
        closure = scoped._read_index()["closure"]
        assert journal.clear_recovery_scope(
            scoped, expected_generation=scoped.generation
        )
    c.proof.cover.side_effect = lose_original_active
    sent = tuple(native.dispatch)
    with pytest.raises(NativeWriteProofError, match="no longer covered"):
        NativePublicationBarrier(c.writer).confirm(c.record)
    assert candidates(c) == (native_scope(c),)
    with pytest.raises(NativeWriteUnresolved, match="unexpired native build"):
        recover(c, now=c.lease.expires_at - timedelta(microseconds=1))
    assert archive(c) is None
    assert tuple(native.dispatch) == sent
    assert attempt(c, ACTIVE, c.token).encode() == original
    h.base.restart()
    ((intent, receipt),) = recover(c)
    assert intent.binding == receipt.binding
    assert [
        entry.deduplication_token for entry in intent.binding.quarantine.pending
    ] == [c.token]
    assert [row["status"] for row in intent.rows] == ["disabled", "failed"]
    assert len(native.dispatch) == len(sent) + 2
    assert attempt(c, ACTIVE, c.token).encode() == original
    assert h.document() == document
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert scoped._read_index()["closure"] == closure
    assert recover(c, now=c.now + timedelta(days=1)) == ((intent, receipt),)
    assert len(native.dispatch) == len(sent) + 2


def test_open_closure_sent_active_has_only_one_positive_proof(tmp_path, monkeypatch):
    c, h, native = closure_case(tmp_path, monkeypatch, state="sent")
    c.proof.settled.return_value = True
    c.proof.cover.reset_mock()
    sent = tuple(native.dispatch)
    assert recover(c) == ()
    assert c.proof.settled.call_count == 1
    assert c.proof.cover.call_count == 1
    assert attempt(c, ACTIVE, c.token).state == "complete"
    assert candidates(c) == ()
    assert tuple(native.dispatch) == sent


def test_open_closure_before_active_prepare_remains_frozen_until_expiry(
    tmp_path, monkeypatch
):
    c, h, native = closure_case(tmp_path, monkeypatch, state="unsent")
    assert attempt(c, ACTIVE, c.token) is None
    sent = tuple(native.dispatch)
    assert recover(c, now=c.lease.issued_at + timedelta(seconds=1)) == ()
    assert candidates(c) == (native_scope(c),)
    assert archive(c) is None
    assert tuple(native.dispatch) == sent
    ((intent, _),) = recover(c)
    assert [row["status"] for row in intent.rows] == ["disabled", "failed"]
    assert intent.publication_document == h.document()
    assert attempt(c, ACTIVE, c.token) is None


@pytest.mark.parametrize("missing", [False, True])
def test_closed_active_missing_or_changed_receipt_blocks_without_repair(
    tmp_path, monkeypatch, missing
):
    c, _, native = closure_case(tmp_path, monkeypatch)
    if missing:
        (
            c.writer.directory
            / "native-write-attempts"
            / (_key(ACTIVE, c.token) + ".json")
        ).unlink()
    else:
        with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
            document = scoped._read_index()
            document["closure"]["attempt_record_sha256"] = "f" * 64
            scoped._save(document)
    sent = tuple(native.dispatch)
    with pytest.raises(NativeWriteUnresolved, match="closure ACTIVE receipt"):
        recover(c)
    assert candidates(c) == (native_scope(c),)
    assert archive(c) is None
    assert tuple(native.dispatch) == sent


@pytest.mark.parametrize("missing", [False, True])
def test_expired_complete_loss_requires_closure_exact_publication(
    tmp_path, monkeypatch, missing
):
    c, h, native = closure_case(tmp_path, monkeypatch)
    c.proof.cover.side_effect = lose_original_active
    document = h.document()
    if missing:
        storage = c.h.coordinator._recovery_journal
        load = storage.load_record
        monkeypatch.setattr(
            storage, "load_record", lambda key: None if key == h.key() else load(key)
        )
    else:
        document["publication"]["record"]["value_rows"] += 1
        _rehash_publication(document)
        c.h.coordinator._recovery_journal.save_record(h.key(), document)
    sent = tuple(native.dispatch)
    with pytest.raises(
        (NativeWriteUnresolved, TerminalRepairIntentError), match="publication"
    ):
        recover(c)
    assert candidates(c) == (native_scope(c),)
    assert archive(c) is None
    assert tuple(native.dispatch) == sent
