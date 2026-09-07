"""Exact local BUILD quarantine: no transport, global scan, or negative proof."""

import json
from dataclasses import FrozenInstanceError, replace
from uuid import UUID, uuid4

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    native_write_journal as subject,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    NativeWriteUnresolved,
)
from tracer.tests.test_property_catalog_durable_native_writer import (
    load as load_writer_attempt,
)
from tracer.tests.test_property_catalog_durable_native_writer import (
    setup as setup_writer,
)
from tracer.tests.test_property_catalog_native_write_journal import (
    TABLE,
    TOKEN,
    ack,
    coverage,
    finish,
    options,
    path,
    publication,
    scope_for,
)
from tracer.tests.test_property_catalog_write_admission import admit

REASON = subject.NativeQuarantineReason.UNRESOLVED_NATIVE_WRITE


def index_path(directory, scoped):
    return path(directory).parent / (scoped._key + ".json")


def rewrite_index(index, document):
    document["sha256"] = subject._hash(
        {key: value for key, value in document.items() if key != "sha256"}
    )
    index.write_bytes(subject._canonical(document) + b"\n")


def no_writes(*args, **kwargs):
    pytest.fail("unexpected durable mutation")


def test_quarantine_persists_across_restart_without_changing_any_original_receipt(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    receipts = {}
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            for state in ("prepared", "sent", "acknowledged", "complete"):
                with scoped.session(TABLE, state) as session:
                    attempt = session.prepare(**options(admission))
                    if state != "prepared":
                        attempt = session.mark_sent(attempt)
                    if state in {"acknowledged", "complete"}:
                        attempt = session.mark_acknowledged(
                            attempt, evidence=ack(attempt)
                        )
                    if state == "complete":
                        attempt = session.mark_complete(
                            attempt, evidence=coverage(attempt, admission)
                        )
                    receipts[state] = attempt.encode()
            index = index_path(tmp_path, scoped)
            before = json.loads(index.read_bytes())
            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", no_writes)
                captured = scoped.capture_quarantine(REASON)
                assert scoped.quarantine_binding is None
            assert captured.scope == scope
            assert captured.admission_sha256 == before["admission_sha256"]
            assert captured.generation == scoped.generation == 4
            assert len(captured.pending) == 3
            for entry in captured.pending:
                attempt = subject.NativeWriteAttempt(
                    receipts[entry.deduplication_token]
                )
                assert entry.query_id == attempt.query_id
                assert entry.parameters_sha256 == attempt.parameters_sha256
                assert entry.intent_sha256 == subject._intent_sha256(attempt)
            atomic = subject._atomic
            writes = []

            def record_write(directory, name, raw):
                assert name == scoped._key + ".json"
                writes.append(name)
                atomic(directory, name, raw)

            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", record_write)
                frozen = scoped.quarantine(captured)
            assert frozen == captured and len(writes) == 1
            after = json.loads(index.read_bytes())
            assert after["version"] == 2
            for field in (
                "generation",
                "pending",
                "closure",
                "scope",
                "admission_sha256",
            ):
                assert after[field] == before[field]
            assert index.stat().st_mode & 0o777 == 0o600
            assert index.stat().st_nlink == 1
            with pytest.raises(FrozenInstanceError):
                frozen.generation = 123
            with pytest.raises(FrozenInstanceError):
                frozen.pending[0].intent_sha256 = "a" * 64
    with subject.NativeWriteJournal(tmp_path) as journal:
        with monkeypatch.context() as patch:
            patch.setattr(subject, "_atomic", no_writes)
            with journal.scope(scope, create=True) as scoped:
                assert scoped.quarantine_binding == frozen
                assert scoped.capture_quarantine(REASON) == frozen
                assert scoped.quarantine(frozen) == frozen
                for token, raw in receipts.items():
                    with scoped.session(TABLE, token) as session:
                        assert session.load().encode() == raw
                    assert path(tmp_path, token=token).read_bytes() == raw


@pytest.mark.parametrize("table", sorted(subject._COLUMNS))
def test_stale_prepared_is_readable_but_never_send_authorized_and_all_tables_block(
    tmp_path, monkeypatch, table
):
    admission = admit(tmp_path)
    request = options(admission, table)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal, table)
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(table, TOKEN) as session:
                prepared = session.prepare(**request)
            frozen = scoped.quarantine(scoped.capture_quarantine(REASON))
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope) as scoped,
    ):
        with monkeypatch.context() as patch:
            patch.setattr(subject, "_atomic", no_writes)
            patch.setattr(subject, "uuid4", no_writes)
            with scoped.session(table, TOKEN) as session:
                assert session.load().encode() == prepared.encode()
                assert session.prepare(**request).encode() == prepared.encode()
                with pytest.raises(
                    subject.NativeWriteJournalError, match="quarantined"
                ):
                    session.mark_sent(prepared)
                with pytest.raises(
                    subject.NativeWriteJournalError, match="no replay or proof bypass"
                ):
                    session.mark_acknowledged(prepared, evidence=ack(prepared))
                with pytest.raises(
                    subject.NativeWriteJournalError, match="no replay or proof bypass"
                ):
                    session.mark_complete(prepared, evidence={})
            with scoped.session(table, "new-build-or-repair-write") as session:
                with pytest.raises(
                    subject.NativeWriteJournalError, match="quarantined"
                ):
                    session.prepare(**request)
                assert session.load() is None
            with pytest.raises(subject.NativeWriteJournalError, match="quarantined"):
                scoped._register(prepared)
        assert scoped.quarantine_binding == frozen
        assert scoped.generation == frozen.generation == 1
        assert not path(tmp_path, table, "new-build-or-repair-write").exists()
        assert not path(
            tmp_path, table, "new-build-or-repair-write", "scope-binding"
        ).exists()
        assert path(tmp_path, table).read_bytes() == prepared.encode()
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.session(table, TOKEN) as diagnostic,
    ):
        assert diagnostic.load().encode() == prepared.encode()
        with pytest.raises(subject.NativeWriteJournalError, match="scoped session"):
            diagnostic.mark_sent(prepared)


@pytest.mark.parametrize("state", ["sent", "acknowledged"])
@pytest.mark.parametrize("ack_kind", ["native_end_of_stream", "query_log_finish"])
def test_same_binding_remains_idempotent_after_proof_only_settlement(
    tmp_path, monkeypatch, state, ack_kind
):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(TABLE, TOKEN) as session:
                attempt = session.mark_sent(session.prepare(**options(admission)))
                if state == "acknowledged":
                    attempt = session.mark_acknowledged(
                        attempt, evidence=ack(attempt, ack_kind)
                    )
            frozen = scoped.quarantine(scoped.capture_quarantine(REASON))
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope) as scoped,
    ):
        with scoped.session(TABLE, TOKEN) as session:
            attempt = session.load()
            if state == "sent":
                with pytest.raises(subject.NativeWriteJournalError):
                    session.mark_acknowledged(
                        attempt, evidence={**ack(attempt), "query_id": str(uuid4())}
                    )
                attempt = session.mark_acknowledged(
                    attempt, evidence=ack(attempt, ack_kind)
                )
            with pytest.raises(subject.NativeWriteJournalError):
                session.mark_complete(attempt, evidence={})
            complete = session.mark_complete(
                attempt, evidence=coverage(attempt, admission)
            )
        assert scoped.pending() == ()
        assert len(frozen.pending) == 1
        assert scoped.generation == frozen.generation == 1
        with monkeypatch.context() as patch:
            patch.setattr(subject, "_atomic", no_writes)
            assert scoped.quarantine(frozen) == frozen
            assert scoped.capture_quarantine(REASON) == frozen
            assert scoped.quarantine_binding == frozen
            with pytest.raises(subject.NativeWriteJournalError, match="immutable"):
                scoped.quarantine(replace(frozen, pending=()))
            with pytest.raises(subject.NativeWriteJournalError, match="immutable"):
                scoped.quarantine(
                    replace(
                        frozen, reason=subject.NativeQuarantineReason.BUILD_SUPERSEDED
                    )
                )
            with pytest.raises(subject.NativeWriteJournalError, match="immutable"):
                scoped.capture_quarantine(
                    subject.NativeQuarantineReason.PUBLICATION_BLOCKED
                )
        assert path(tmp_path).read_bytes() == complete.encode()
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope) as scoped,
    ):
        assert scoped.quarantine(frozen) == frozen
        assert scoped.pending() == ()


@pytest.mark.parametrize("phase", ["before", "closing", "sent", "complete", "closed"])
def test_quarantine_blocks_begin_finish_require_before_and_after_publication_closure(
    tmp_path, monkeypatch, phase
):
    admission = admit(tmp_path)
    request, binding = publication(admission)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope_for(journal), create=True) as scoped,
    ):
        closure = subject.NativeScopeClosure(binding, 0)
        active = None
        if phase != "before":
            closure = scoped.begin_closure(binding)
        if phase in {"sent", "complete", "closed"}:
            with scoped.session(
                subject._ACTIVE_TABLE, binding.deduplication_token
            ) as session:
                active = session.mark_sent(session.prepare(**request))
                if phase in {"complete", "closed"}:
                    active = finish(session, active, admission)
            if phase == "closed":
                closure = scoped.finish_closure(closure, active)
                scoped.require_closed(closure)
        index = index_path(tmp_path, scoped)
        before_closure = json.loads(index.read_bytes())["closure"]
        scoped.quarantine(scoped.capture_quarantine(REASON))
        preserved = index.read_bytes()
        with monkeypatch.context() as patch:
            patch.setattr(subject, "_atomic", no_writes)
            for action in (
                lambda: scoped.begin_closure(binding),
                lambda: scoped.finish_closure(closure, active),
                lambda: scoped.require_closed(closure),
            ):
                with pytest.raises(
                    subject.NativeWriteJournalError, match="quarantined"
                ):
                    action()
        assert index.read_bytes() == preserved
        assert json.loads(preserved)["closure"] == before_closure


@pytest.mark.parametrize(
    "change",
    [
        "scope",
        "admission",
        "generation",
        "pending",
        "query_id",
        "parameters_sha256",
        "intent_sha256",
    ],
)
def test_exact_capture_rejects_conflicting_binding_without_mutation(tmp_path, change):
    admission = admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope_for(journal), create=True) as scoped,
    ):
        with scoped.session(TABLE, TOKEN) as session:
            session.prepare(**options(admission))
        captured = scoped.capture_quarantine(REASON)
        if change == "scope":
            changed = replace(
                captured, scope=replace(captured.scope, workspace_id=str(UUID(int=77)))
            )
        elif change == "admission":
            changed = replace(captured, admission_sha256="f" * 64)
        elif change == "generation":
            changed = replace(captured, generation=captured.generation + 1)
        elif change == "pending":
            changed = replace(captured, pending=())
        else:
            value = str(uuid4()) if change == "query_id" else "f" * 64
            changed = replace(
                captured, pending=(replace(captured.pending[0], **{change: value}),)
            )
        index = index_path(tmp_path, scoped)
        preserved = index.read_bytes()
        with pytest.raises(
            subject.NativeWriteJournalError, match="binding differs|compare-and-swap"
        ):
            scoped.quarantine(changed)
        assert index.read_bytes() == preserved
        assert scoped.quarantine_binding is None


@pytest.mark.parametrize("change", ["registration", "settlement", "same_key_intent"])
def test_capture_rejects_changes_between_scope_lock_sessions(tmp_path, change):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(TABLE, TOKEN) as session:
                attempt = session.mark_sent(session.prepare(**options(admission)))
            captured = scoped.capture_quarantine(REASON)
        with journal.scope(scope) as scoped:
            if change == "registration":
                with scoped.session(TABLE, "racing-registration") as session:
                    session.prepare(**options(admission))
            elif change == "settlement":
                with scoped.session(TABLE, TOKEN) as session:
                    finish(session, attempt, admission)
            else:
                # Same table/token/query/payload, different full frozen intent.
                index = index_path(tmp_path, scoped)
                document = json.loads(index.read_bytes())
                document["pending"][subject._key(TABLE, TOKEN)]["intent_sha256"] = (
                    "e" * 64
                )
                rewrite_index(index, document)
        with journal.scope(scope) as scoped:
            before = index_path(tmp_path, scoped).read_bytes()
            with pytest.raises(
                subject.NativeWriteJournalError, match="compare-and-swap"
            ):
                scoped.quarantine(captured)
            assert index_path(tmp_path, scoped).read_bytes() == before


def test_exact_scope_lock_required_and_other_build_and_control_remain_independent(
    tmp_path,
):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            captured = scoped.capture_quarantine(REASON)
            with scoped.session(TABLE, TOKEN):
                with pytest.raises(subject.NativeWriteJournalError, match="nested"):
                    scoped.quarantine(captured)
                with pytest.raises(subject.NativeWriteJournalError, match="nested"):
                    scoped.capture_quarantine(REASON)
            with pytest.raises(subject.NativeWriteJournalBusy):
                with journal.scope(scope):
                    pytest.fail("scope lock bypass")
            scoped.quarantine(captured)
        with pytest.raises(subject.NativeWriteJournalError, match="live owning"):
            scoped.quarantine(captured)
        other_scope = replace(scope, catalog_revision=3, build_token=str(UUID(int=88)))
        with journal.scope(other_scope, create=True) as other:
            with pytest.raises(
                subject.NativeWriteJournalError, match="binding differs"
            ):
                other.quarantine(captured)
            request = options(admission)
            request["rows"][0].update(
                target_catalog_revision=3, target_build_token=str(UUID(int=88))
            )
            with other.session(TABLE, "other-build") as session:
                assert session.mark_sent(session.prepare(**request)).state == "sent"
        control = replace(
            scope, kind="control", catalog_revision=None, build_token=None
        )
        with journal.scope(control, create=True) as scoped:
            with pytest.raises(subject.NativeWriteJournalError, match="BUILD"):
                scoped.capture_quarantine(REASON)
            with pytest.raises(
                subject.NativeWriteJournalError, match="binding differs"
            ):
                scoped.quarantine(captured)
            request = options(admission)
            request["rows"][0].update(
                action="disable", target_catalog_revision=0, target_build_token=""
            )
            with scoped.session(TABLE, "control-disable") as session:
                assert session.mark_sent(session.prepare(**request)).state == "sent"
            assert scoped.quarantine_binding is None
        with pytest.raises(subject.NativeWriteJournalError, match="BUILD"):
            replace(captured, scope=control)


@pytest.mark.parametrize("receipt_exists", [False, True])
def test_registering_prepared_load_stays_read_only_after_quarantine(
    tmp_path, monkeypatch, receipt_exists
):
    admission = admit(tmp_path)
    atomic = subject._atomic
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:

            def interrupt(directory, name, raw):
                if name == subject._key(TABLE, TOKEN) + ".json":
                    if receipt_exists:
                        atomic(directory, name, raw)
                    raise OSError("interrupted before READY")
                atomic(directory, name, raw)

            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", interrupt)
                with pytest.raises(OSError, match="before READY"):
                    with scoped.session(TABLE, TOKEN) as session:
                        session.prepare(**options(admission))
            original = json.loads(index_path(tmp_path, scoped).read_bytes())["pending"][
                subject._key(TABLE, TOKEN)
            ]["prepared"]
            prepared = subject.NativeWriteAttempt(subject._canonical(original) + b"\n")
            captured = scoped.capture_quarantine(REASON)
            scoped.quarantine(captured)
            preserved = index_path(tmp_path, scoped).read_bytes()
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope) as scoped,
    ):
        with monkeypatch.context() as patch:
            patch.setattr(subject, "_atomic", no_writes)
            assert len(scoped.pending()) == 1
            with scoped.session(TABLE, TOKEN) as session:
                assert session.load().encode() == prepared.encode()
                assert (
                    session.prepare(**options(admission)).encode() == prepared.encode()
                )
                with pytest.raises(
                    subject.NativeWriteJournalError, match="quarantined"
                ):
                    session.mark_sent(prepared)
            assert scoped.quarantine(captured) == captured
        assert index_path(tmp_path, scoped).read_bytes() == preserved
        assert path(tmp_path).exists() is receipt_exists
        if receipt_exists:
            assert path(tmp_path).read_bytes() == prepared.encode()


@pytest.mark.parametrize("fault", ["before", "after"])
def test_atomic_quarantine_failure_recovers_only_old_or_exact_new_state(
    tmp_path, monkeypatch, fault
):
    admit(tmp_path)
    atomic = subject._atomic
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            captured = scoped.capture_quarantine(REASON)

            def interrupt(directory, name, raw):
                if fault == "after":
                    atomic(directory, name, raw)
                raise OSError("interrupted quarantine")

            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", interrupt)
                with pytest.raises(OSError, match="interrupted quarantine"):
                    scoped.quarantine(captured)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope) as scoped,
    ):
        assert scoped.quarantine_binding == (captured if fault == "after" else None)
        assert scoped.quarantine(captured) == captured


def test_complete_receipt_with_stranded_reference_is_captured_without_settlement(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    atomic = subject._atomic
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(TABLE, TOKEN) as session:
                attempt = session.mark_sent(session.prepare(**options(admission)))
                attempt = session.mark_acknowledged(attempt, evidence=ack(attempt))

                def interrupt(directory, name, raw):
                    if name == scoped._key + ".json":
                        raise OSError("interrupted reference removal")
                    atomic(directory, name, raw)

                with monkeypatch.context() as patch:
                    patch.setattr(subject, "_atomic", interrupt)
                    with pytest.raises(OSError, match="reference removal"):
                        session.mark_complete(
                            attempt, evidence=coverage(attempt, admission)
                        )
            complete = path(tmp_path).read_bytes()
            assert subject.NativeWriteAttempt(complete).state == "complete"
            captured = scoped.capture_quarantine(REASON)
            assert len(captured.pending) == 1
            before = json.loads(index_path(tmp_path, scoped).read_bytes())["pending"]
            scoped.quarantine(captured)
            assert (
                json.loads(index_path(tmp_path, scoped).read_bytes())["pending"]
                == before
            )
            assert path(tmp_path).read_bytes() == complete
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope) as scoped,
    ):
        assert scoped.quarantine_binding == captured
        assert [entry.deduplication_token for entry in scoped.pending()] == [TOKEN]
        assert scoped.quarantine(captured) == captured
        assert path(tmp_path).read_bytes() == complete


@pytest.mark.parametrize(
    "change",
    [
        "checksum",
        "missing",
        "null",
        "reason",
        "scope",
        "admission",
        "generation",
        "query_id",
        "intent_sha256",
        "unknown",
        "oversized",
        "duplicate",
        "pending_new",
        "pending_intent",
        "legacy_with_quarantine",
        "missing_anchor",
    ],
)
def test_corrupt_quarantine_fails_closed_even_with_recomputed_index_checksum(
    tmp_path, change
):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(TABLE, TOKEN) as session:
                session.prepare(**options(admission))
            scoped.quarantine(scoped.capture_quarantine(REASON))
            index = index_path(tmp_path, scoped)
            anchor = index.with_suffix(".anchor")
        document = json.loads(index.read_bytes())
        quarantine = document["quarantine"]
        if change == "missing":
            del document["quarantine"]
        elif change == "null":
            document["quarantine"] = None
        elif change == "reason":
            quarantine["reason"] = "x" * 4096
        elif change == "scope":
            quarantine["scope"]["workspace_id"] = str(UUID(int=991))
        elif change == "admission":
            quarantine["admission_sha256"] = "f" * 64
        elif change == "generation":
            document["generation"] += 1
        elif change in {"query_id", "intent_sha256"}:
            quarantine["pending"][0][change] = (
                str(uuid4()) if change == "query_id" else "f" * 64
            )
        elif change == "unknown":
            quarantine["repair_allowed"] = True
        elif change == "oversized":
            quarantine["pending"] *= 129
        elif change == "duplicate":
            quarantine["pending"] *= 2
            quarantine["generation"] = document["generation"] = 2
        elif change == "pending_new":
            entry = dict(next(iter(document["pending"].values())))
            entry["deduplication_token"] = "new-intent"
            document["pending"][subject._key(TABLE, "new-intent")] = entry
            quarantine["generation"] = document["generation"] = 2
        elif change == "pending_intent":
            next(iter(document["pending"].values()))["intent_sha256"] = "f" * 64
        elif change == "legacy_with_quarantine":
            document["version"] = 1
        elif change == "missing_anchor":
            anchor.unlink()
        rewrite_index(index, document)
        if change == "checksum":
            document["sha256"] = "f" * 64
            index.write_bytes(subject._canonical(document) + b"\n")
        preserved = index.read_bytes()
        with pytest.raises(subject.NativeWriteJournalError):
            with journal.scope(scope, create=True):
                pytest.fail("corrupt quarantine was ignored")
        assert index.read_bytes() == preserved


def test_bounded_scope_capture_never_enumerates_or_recovers_receipts(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope_for(journal), create=True) as scoped,
    ):
        for number in range(subject.MAX_SCOPE_PENDING):
            with scoped.session(TABLE, f"intent-{number}") as session:
                session.prepare(**options(admission))
        with monkeypatch.context() as patch:
            patch.setattr(subject.os, "listdir", no_writes)
            patch.setattr(subject.os, "scandir", no_writes)
            patch.setattr(subject.NativeWriteScopeSession, "pending", no_writes)
            patch.setattr(subject.NativeWriteScopeSession, "session", no_writes)
            patch.setattr(subject.NativeWriteSession, "_load_file", no_writes)
            captured = scoped.capture_quarantine(REASON)
            assert len(captured.pending) == subject.MAX_SCOPE_PENDING
            assert scoped.quarantine(captured) == captured
            assert scoped.quarantine_binding == captured
        with pytest.raises(subject.NativeWriteJournalError, match="bounded"):
            replace(
                captured,
                generation=129,
                pending=captured.pending + captured.pending[:1],
            )


@pytest.mark.parametrize(
    "reason", [None, "unresolved_native_write", "x" * 4096, True, {}, []]
)
def test_reason_must_be_typed_and_bounded(tmp_path, reason):
    admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope_for(journal), create=True) as scoped,
    ):
        before = index_path(tmp_path, scoped).read_bytes()
        with pytest.raises(subject.NativeWriteJournalError, match="typed"):
            scoped.capture_quarantine(reason)
        assert index_path(tmp_path, scoped).read_bytes() == before


def test_legacy_scope_remains_byte_identical_until_quarantine_then_survives_settlement(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(TABLE, TOKEN) as session:
                attempt = session.mark_sent(session.prepare(**options(admission)))
            index = index_path(tmp_path, scoped)
            legacy = index.read_bytes()
            anchor = index.with_suffix(".anchor").read_bytes()
            assert json.loads(legacy)["version"] == 1
            assert "quarantine" not in json.loads(legacy)
        with monkeypatch.context() as patch:
            patch.setattr(subject, "_atomic", no_writes)
            with journal.scope(scope, create=True) as scoped:
                assert scoped.quarantine_binding is None
                captured = scoped.capture_quarantine(REASON)
                assert index.read_bytes() == legacy
        with journal.scope(scope) as scoped:
            scoped.quarantine(captured)
            with scoped.session(TABLE, TOKEN) as session:
                finish(session, attempt, admission)
            assert scoped.quarantine_binding == captured
            assert json.loads(index.read_bytes())["version"] == 2
            assert index.with_suffix(".anchor").read_bytes() == anchor


def _quarantine_writer(tmp_path, writer, request):
    table = request["table"].split("`.")[-1].strip("`")
    scope = subject.NativeWriteScope.from_rows(
        table, request["rows"], writer.proof.identity
    )
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope) as scoped,
    ):
        return scope, scoped.quarantine(scoped.capture_quarantine(REASON))


def test_actual_writer_cannot_dispatch_original_prepared_after_quarantine(
    tmp_path, monkeypatch
):
    writer, proof, transport, steps, request = setup_writer(tmp_path, monkeypatch)
    proof.attest.side_effect = TimeoutError("admission observation unavailable")
    with pytest.raises(TimeoutError):
        writer.insert(**request)
    prepared = load_writer_attempt(tmp_path)
    assert prepared.state == "prepared"
    transport.assert_not_called()
    _quarantine_writer(tmp_path, writer, request)
    proof.attest.side_effect = None
    with pytest.raises(subject.NativeWriteJournalError, match="quarantined"):
        writer.insert(**request)
    # The transport callback rejects before the simulated network dispatch.
    assert "send-after-sent-fsync" not in steps
    assert load_writer_attempt(tmp_path).encode() == prepared.encode()
    proof.cover.assert_not_called()


def test_actual_writer_recovers_sent_by_proof_but_never_unquarantines(
    tmp_path, monkeypatch
):
    writer, proof, transport, _, request = setup_writer(tmp_path, monkeypatch)

    def lose_ack(*args, **kwargs):
        kwargs["before_send"]()
        raise TimeoutError("ack lost after dispatch")

    transport.side_effect = lose_ack
    with pytest.raises(TimeoutError):
        writer.insert(**request)
    sent = load_writer_attempt(tmp_path)
    assert sent.state == "sent"
    scope, frozen = _quarantine_writer(tmp_path, writer, request)
    transport.reset_mock()
    with pytest.raises(NativeWriteUnresolved):
        writer.recover_scope(scope, timeout_ms=5_000)
    assert load_writer_attempt(tmp_path).encode() == sent.encode()
    proof.settled.return_value = True
    assert writer.recover_scope(scope, timeout_ms=5_000) == 1
    assert load_writer_attempt(tmp_path).state == "complete"
    transport.assert_not_called()
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope) as scoped,
    ):
        assert scoped.quarantine_binding == frozen
        assert scoped.pending() == ()
    with pytest.raises(subject.NativeWriteJournalError, match="quarantined"):
        writer.insert(**{**request, "deduplication_token": "new-after-settlement"})
    transport.assert_not_called()


def test_completed_receipt_reproof_does_not_dispatch_or_clear_quarantine(
    tmp_path, monkeypatch
):
    writer, _, transport, _, request = setup_writer(tmp_path, monkeypatch)
    writer.insert(**request)
    completed = load_writer_attempt(tmp_path)
    scope, frozen = _quarantine_writer(tmp_path, writer, request)
    transport.reset_mock()
    writer.insert(**request)
    transport.assert_not_called()
    assert load_writer_attempt(tmp_path).encode() == completed.encode()
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope) as scoped,
    ):
        assert scoped.quarantine_binding == frozen
