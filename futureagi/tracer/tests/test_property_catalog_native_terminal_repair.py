"""Local terminal-repair accounting: synthetic receipts, never SQL or services."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    native_write_journal as subject,
)
from tracer.tests.test_property_catalog_native_write_journal import (
    ack,
    coverage,
    finish,
    options,
    path,
    publication,
    scope_for,
)
from tracer.tests.test_property_catalog_write_admission import Probe, admit

SOURCE = "property_catalog_source_streams"
ACTIVE = "property_catalog_activations"
TERMINAL_VERSION = (1 << 64) - 1
LEASE = "c" * 64
PUBLICATION = "b" * 64
REASON = subject.NativeQuarantineReason.UNRESOLVED_NATIVE_WRITE


def token(scope, table):
    return f"property-catalog-terminal-repair-v1:{scope.build_token}:{table}"


def terminal_request(admission, scope, table):
    request = options(admission, table)
    row = request["rows"][0]
    row.update(_version=TERMINAL_VERSION)
    if table == SOURCE:
        row.update(
            status="failed",
            source_adapter="system_manifest",
            envelope_version=0,
            producer_stream_id=scope.build_token,
            build_lease_sha256=LEASE,
        )
    else:
        row.update(status="disabled", revision_fence_sha256="a" * 64)
    return request


def plan(case, *, activation=False, requests=None):
    if requests is None:
        requests = {
            table: terminal_request(case.admission, case.scope, table)
            for table in ((ACTIVE, SOURCE) if activation else (SOURCE,))
        }
    binding = subject.NativeTerminalRepairBinding(
        case.quarantine,
        LEASE,
        PUBLICATION if activation else None,
        tuple(
            subject.NativeTerminalWriteIntent(
                table,
                token(case.scope, table),
                subject.native_parameters_sha256(request["columns"], request["rows"]),
            )
            for table, request in sorted(requests.items())
        ),
    )
    return binding, requests


@pytest.fixture
def quarantined(tmp_path):
    admission = admit(tmp_path, Probe(2))
    originals = []
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal, SOURCE)
        with journal.scope(scope, create=True) as scoped:
            for state in ("prepared", "sent"):
                with scoped.session(SOURCE, f"original-{state}") as session:
                    attempt = session.prepare(**options(admission, SOURCE))
                    if state == "sent":
                        attempt = session.mark_sent(attempt)
                    originals.append(attempt)
            quarantine = scoped.quarantine(scoped.capture_quarantine(REASON))
    return SimpleNamespace(
        directory=tmp_path,
        admission=admission,
        scope=scope,
        quarantine=quarantine,
        originals=tuple(originals),
    )


def assert_originals_unchanged(case):
    for attempt in case.originals:
        assert (
            path(
                case.directory, attempt["table"], attempt["deduplication_token"]
            ).read_bytes()
            == attempt.encode()
        )


def index_path(case, scoped):
    return path(case.directory, SOURCE).parent / (scoped._key + ".json")


def no_mutation(*args, **kwargs):
    pytest.fail("unexpected durable mutation or global enumeration")


def complete_plan(scoped, binding, requests, admission):
    completed = []
    for write in binding.writes:
        with scoped.session(
            write.table, write.deduplication_token, terminal_repair=binding
        ) as session:
            prepared = session.prepare(**requests[write.table])
            assert prepared.state == "prepared"
            completed.append(finish(session, prepared, admission))
    return tuple(completed)


@pytest.mark.parametrize("activation", [False, True])
def test_plan_and_completion_survive_restart_without_settling_originals(
    quarantined, monkeypatch, activation
):
    case = quarantined
    binding, requests = plan(case, activation=activation)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            before = scoped.pending()
            anchor_file = index_path(case, scoped).with_suffix(".anchor")
            anchor_before = anchor_file.read_bytes()
            assert scoped.begin_terminal_repair(binding) == binding
            assert anchor_file.read_bytes() == anchor_before
            assert json.loads(anchor_before)["version"] == 1
            assert json.loads(index_path(case, scoped).read_bytes())["version"] == 3
            assert scoped.pending() == before
            assert scoped.generation == case.quarantine.generation
            assert_originals_unchanged(case)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", no_mutation)
                assert scoped.begin_terminal_repair(binding) == binding
            completed = complete_plan(scoped, binding, requests, case.admission)
            receipt = scoped.finish_terminal_repair(binding, completed)
            assert receipt == subject.NativeTerminalRepairReceipt(
                binding, tuple(attempt["record_sha256"] for attempt in completed)
            )
            assert scoped.generation == case.quarantine.generation + len(binding.writes)
            assert scoped.pending() == before
            assert_originals_unchanged(case)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", no_mutation)
                assert scoped.begin_terminal_repair(binding) == binding
                assert scoped.finish_terminal_repair(binding, completed) == receipt
                assert scoped.quarantine(case.quarantine) == case.quarantine
                for write, attempt in zip(binding.writes, completed, strict=True):
                    with scoped.session(
                        write.table, write.deduplication_token, terminal_repair=binding
                    ) as session:
                        assert (
                            session.prepare(**requests[write.table]).encode()
                            == attempt.encode()
                        )
                        with pytest.raises(ValueError, match="no replay"):
                            session.mark_sent(attempt)
            assert_originals_unchanged(case)


@pytest.mark.parametrize("activation", [False, True])
def test_original_positive_settlement_after_repair_preserves_frozen_binding(
    quarantined, activation
):
    case = quarantined
    binding, requests = plan(case, activation=activation)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            completed = complete_plan(scoped, binding, requests, case.admission)
            receipt = scoped.finish_terminal_repair(binding, completed)
            prepared, sent = case.originals
            with scoped.session(SOURCE, sent["deduplication_token"]) as session:
                observed = session.load()
                assert observed.encode() == sent.encode()
                acknowledged = session.mark_acknowledged(
                    observed, evidence=ack(observed, "query_log_finish")
                )
                settled = session.mark_complete(
                    acknowledged, evidence=coverage(acknowledged, case.admission)
                )
                assert settled.state == "complete"
            with scoped.session(SOURCE, prepared["deduplication_token"]) as session:
                assert session.load().encode() == prepared.encode()
                with pytest.raises(ValueError, match="quarantin"):
                    session.mark_sent(prepared)
            assert [entry.query_id for entry in scoped.pending()] == [prepared.query_id]
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            assert scoped.begin_terminal_repair(binding) == binding
            assert scoped.finish_terminal_repair(binding, completed) == receipt
            assert scoped.quarantine_binding == case.quarantine
            assert len(binding.quarantine.pending) == 2


@pytest.mark.parametrize("table", [SOURCE, ACTIVE])
def test_sent_restart_is_proof_only_and_requires_exact_ack_and_member_coverage(
    quarantined, table
):
    case = quarantined
    binding, requests = plan(case, activation=table == ACTIVE)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            with scoped.session(
                table, token(case.scope, table), terminal_repair=binding
            ) as session:
                prepared = session.prepare(**requests[table])
                sent = session.mark_sent(prepared)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            with scoped.session(
                table, token(case.scope, table), terminal_repair=binding
            ) as session:
                assert session.prepare(**requests[table]).encode() == sent.encode()
                for previous in (prepared, sent):
                    with pytest.raises(ValueError):
                        session.mark_sent(previous)
                with pytest.raises(ValueError):
                    session.mark_complete(sent, evidence={})
                with pytest.raises(ValueError):
                    session.mark_acknowledged(
                        sent, evidence={**ack(sent), "query_id": str(UUID(int=9))}
                    )
                assert session.load().encode() == sent.encode()
                acknowledged = session.mark_acknowledged(sent, evidence=ack(sent))
                with pytest.raises(ValueError):
                    session.mark_complete(
                        acknowledged,
                        evidence={
                            **coverage(acknowledged, case.admission),
                            "members": [],
                        },
                    )
                completed = session.mark_complete(
                    acknowledged, evidence=coverage(acknowledged, case.admission)
                )
                assert completed.state == "complete"
            assert_originals_unchanged(case)


@pytest.mark.parametrize("phase", ["before_begin", "after_begin", "after_complete"])
def test_no_normal_or_root_session_bypass_even_for_exact_terminal_rows(
    quarantined, phase
):
    case = quarantined
    binding, requests = plan(case)
    write = binding.writes[0]
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            if phase != "before_begin":
                scoped.begin_terminal_repair(binding)
            if phase == "after_complete":
                completed = complete_plan(scoped, binding, requests, case.admission)
                scoped.finish_terminal_repair(binding, completed)
            elif phase == "before_begin":
                with pytest.raises(ValueError):
                    with scoped.session(
                        SOURCE, write.deduplication_token, terminal_repair=binding
                    ):
                        pytest.fail("unfrozen repair session was authorized")
            else:
                with scoped.session(
                    SOURCE, write.deduplication_token, terminal_repair=binding
                ) as session:
                    terminal_prepared = session.prepare(**requests[SOURCE])
                with scoped.session(SOURCE, write.deduplication_token) as session:
                    assert session.load().encode() == terminal_prepared.encode()
                    with pytest.raises(ValueError, match="quarantin"):
                        session.mark_sent(terminal_prepared)
            for table in subject._COLUMNS:
                with scoped.session(table, "generic-repair-bypass") as session:
                    with pytest.raises(ValueError):
                        session.prepare(**options(case.admission, table))
                    assert session.load() is None
                with pytest.raises(ValueError):
                    with scoped.session(
                        table, "generic-repair-bypass", terminal_repair=binding
                    ):
                        pytest.fail("non-plan table/token authorized")
            original = case.originals[0]
            with scoped.session(SOURCE, original["deduplication_token"]) as session:
                assert (
                    session.prepare(**options(case.admission, SOURCE)).encode()
                    == original.encode()
                )
                with pytest.raises(ValueError):
                    session.mark_sent(original)
            with journal.session(SOURCE, write.deduplication_token) as session:
                with pytest.raises(ValueError, match="read-only"):
                    session.prepare(**requests[SOURCE])
            assert_originals_unchanged(case)


@pytest.mark.parametrize(
    "table,change",
    [
        (SOURCE, {"status": "reserved"}),
        (SOURCE, {"status": "complete"}),
        (SOURCE, {"source_adapter": "otel"}),
        (SOURCE, {"envelope_version": 1}),
        (SOURCE, {"envelope_version": "0"}),
        (SOURCE, {"producer_stream_id": str(UUID(int=91))}),
        (SOURCE, {"build_lease_sha256": "d" * 64}),
        (ACTIVE, {"status": "active"}),
        *[
            (table, {"_version": value})
            for table in (SOURCE, ACTIVE)
            for value in (0, TERMINAL_VERSION - 1, str(TERMINAL_VERSION))
        ],
        *[
            (table, {column: value})
            for table in (SOURCE, ACTIVE)
            for column, value in (
                ("organization_id", UUID(int=91)),
                ("workspace_id", UUID(int=92)),
                ("catalog_epoch", 2),
                ("projection_version", 2),
                ("catalog_revision", 3),
                ("build_token", str(UUID(int=93))),
            )
        ],
    ],
)
def test_self_consistently_digest_bound_but_nonterminal_rows_reject_before_write(
    quarantined, monkeypatch, table, change
):
    case = quarantined
    _, requests = plan(case, activation=table == ACTIVE)
    requests[table]["rows"][0].update(change)
    # Recompute the plan digest: this tests row semantics, not just a hash mismatch.
    binding, requests = plan(case, activation=table == ACTIVE, requests=requests)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            before = index_path(case, scoped).read_bytes()
            with scoped.session(
                table, token(case.scope, table), terminal_repair=binding
            ) as session:
                with monkeypatch.context() as patch:
                    patch.setattr(subject, "_atomic", no_mutation)
                    with pytest.raises(ValueError):
                        session.prepare(**requests[table])
                assert session.load() is None
            assert index_path(case, scoped).read_bytes() == before
            assert not path(case.directory, table, token(case.scope, table)).exists()
            assert_originals_unchanged(case)


@pytest.mark.parametrize("table", [SOURCE, ACTIVE])
@pytest.mark.parametrize("mutation", ["digest", "multiple_rows", "admission"])
def test_exact_payload_count_and_admission_are_required(quarantined, table, mutation):
    case = quarantined
    binding, requests = plan(case, activation=table == ACTIVE)
    if mutation == "digest":
        requests[table]["rows"][0]["updated_at"] = "different frozen parameter"
    elif mutation == "multiple_rows":
        requests[table]["rows"] *= 2
        binding, requests = plan(case, activation=table == ACTIVE, requests=requests)
    else:
        requests[table]["admission_sha256"] = "d" * 64
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            with scoped.session(
                table, token(case.scope, table), terminal_repair=binding
            ) as session:
                with pytest.raises(ValueError):
                    session.prepare(**requests[table])
                assert session.load() is None
            assert scoped.generation == case.quarantine.generation


@pytest.mark.parametrize("settled", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        "lease",
        "publication",
        "parameters",
        "generation",
        "admission",
        "scope",
        "intent",
    ],
)
def test_frozen_plan_replacement_rejected_after_restart(quarantined, change, settled):
    case = quarantined
    binding, requests = plan(case, activation=True)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            if settled:
                completed = complete_plan(scoped, binding, requests, case.admission)
                scoped.finish_terminal_repair(binding, completed)
    if change == "lease":
        replacement = replace(binding, build_lease_sha256="d" * 64)
    elif change == "publication":
        replacement = replace(binding, publication_sha256="d" * 64)
    elif change == "parameters":
        replacement = replace(
            binding,
            writes=(
                replace(binding.writes[0], parameters_sha256="d" * 64),
                binding.writes[1],
            ),
        )
    else:
        quarantine = binding.quarantine
        if change == "generation":
            quarantine = replace(quarantine, generation=quarantine.generation + 1)
        elif change == "admission":
            quarantine = replace(quarantine, admission_sha256="d" * 64)
        elif change == "scope":
            quarantine = replace(
                quarantine, scope=replace(case.scope, workspace_id=str(UUID(int=91)))
            )
        else:
            quarantine = replace(
                quarantine,
                pending=(
                    replace(quarantine.pending[0], intent_sha256="d" * 64),
                    *quarantine.pending[1:],
                ),
            )
        replacement = replace(binding, quarantine=quarantine)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            before = index_path(case, scoped).read_bytes()
            with pytest.raises(ValueError):
                scoped.begin_terminal_repair(replacement)
            with pytest.raises(ValueError):
                with scoped.session(
                    SOURCE, token(case.scope, SOURCE), terminal_repair=replacement
                ):
                    pytest.fail("replacement plan authorized")
            assert scoped.begin_terminal_repair(binding) == binding
            assert index_path(case, scoped).read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        "missing_source",
        "missing_activation",
        "unbound_activation",
        "reordered",
        "duplicate",
        "list",
        "wrong_token",
        "wrong_build_token",
        "wrong_table",
        "bad_digest",
        "bad_lease",
        "bad_publication",
    ],
)
def test_plan_constructor_requires_exact_canonical_terminal_intents(
    quarantined, change
):
    binding, _ = plan(quarantined, activation=True)
    with pytest.raises(ValueError):
        if change == "missing_source":
            replace(binding, writes=binding.writes[:1])
        elif change == "missing_activation":
            replace(binding, writes=binding.writes[1:])
        elif change == "unbound_activation":
            replace(binding, publication_sha256=None)
        elif change == "reordered":
            replace(binding, writes=tuple(reversed(binding.writes)))
        elif change == "duplicate":
            replace(
                binding,
                writes=(binding.writes[0], binding.writes[1], binding.writes[1]),
            )
        elif change == "list":
            replace(binding, writes=list(binding.writes))
        elif change in {"wrong_token", "wrong_build_token"}:
            bad_token = (
                "generic-terminal-token"
                if change == "wrong_token"
                else token(
                    replace(quarantined.scope, build_token=str(UUID(int=91))), ACTIVE
                )
            )
            replace(
                binding,
                writes=(
                    replace(binding.writes[0], deduplication_token=bad_token),
                    binding.writes[1],
                ),
            )
        elif change == "wrong_table":
            replace(binding.writes[0], table="property_catalog_checkpoints")
        elif change == "bad_digest":
            replace(binding.writes[0], parameters_sha256="not-a-sha")
        elif change == "bad_lease":
            replace(binding, build_lease_sha256="not-a-sha")
        else:
            replace(binding, publication_sha256="not-a-sha")


@pytest.mark.parametrize("state", ["prepared", "sent", "acknowledged"])
def test_finish_never_turns_unresolved_receipt_into_complete(quarantined, state):
    case = quarantined
    binding, requests = plan(case)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            with scoped.session(
                SOURCE, token(case.scope, SOURCE), terminal_repair=binding
            ) as session:
                attempt = session.prepare(**requests[SOURCE])
                if state != "prepared":
                    attempt = session.mark_sent(attempt)
                if state == "acknowledged":
                    attempt = session.mark_acknowledged(attempt, evidence=ack(attempt))
            before = index_path(case, scoped).read_bytes()
            with pytest.raises(ValueError):
                scoped.finish_terminal_repair(binding, (attempt,))
            assert (
                path(case.directory, SOURCE, token(case.scope, SOURCE)).read_bytes()
                == attempt.encode()
            )
            assert index_path(case, scoped).read_bytes() == before
            assert_originals_unchanged(case)


@pytest.mark.parametrize(
    "change", ["missing", "extra", "reordered", "duplicate", "list", "forged_complete"]
)
def test_finish_requires_all_exact_ordered_durable_completed_receipts(
    quarantined, change
):
    case = quarantined
    binding, requests = plan(case, activation=True)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            completed = complete_plan(scoped, binding, requests, case.admission)
            if change == "missing":
                supplied = completed[:1]
            elif change == "extra":
                supplied = (*completed, completed[1])
            elif change == "reordered":
                supplied = tuple(reversed(completed))
            elif change == "duplicate":
                supplied = (completed[0], completed[0])
            elif change == "list":
                supplied = list(completed)
            else:
                document = json.loads(completed[0].encode())
                document["acknowledgement"]["kind"] = "query_log_finish"
                document["completion"]["settlement"] = "query_log_finish"
                supplied = (subject._record(document), completed[1])
            before = index_path(case, scoped).read_bytes()
            with pytest.raises(ValueError):
                scoped.finish_terminal_repair(binding, supplied)
            assert index_path(case, scoped).read_bytes() == before
            assert scoped.finish_terminal_repair(
                binding, completed
            ).attempt_record_sha256s == tuple(a["record_sha256"] for a in completed)


@pytest.mark.parametrize("stage", ["registering", "attempt", "ready"])
@pytest.mark.parametrize("fault", ["file_fsync", "directory_fsync"])
def test_registration_fsync_crashes_recover_only_exact_durable_terminal_intent(
    quarantined, monkeypatch, stage, fault
):
    case = quarantined
    binding, requests = plan(case)
    key = subject._key(SOURCE, token(case.scope, SOURCE))
    real_atomic, real_fsync = subject._atomic, os.fsync
    saved = []
    fired = False

    def atomic(directory, name, raw):
        nonlocal fired
        document = json.loads(raw)
        entry = document.get("pending", {}).get(key)
        phase = "other"
        if entry:
            phase = entry["stage"]
            if phase == "registering":
                saved.append(
                    subject.NativeWriteAttempt(
                        subject._canonical(entry["prepared"]) + b"\n"
                    )
                )
        elif name == key + ".json" and document.get("format") == subject._FORMAT:
            phase = "attempt"

        def fsync(fd):
            nonlocal fired
            directory_sync = stat.S_ISDIR(os.fstat(fd).st_mode)
            if not fired and directory_sync == (fault == "directory_fsync"):
                fired = True
                raise OSError("injected terminal registration fsync crash")
            return real_fsync(fd)

        with monkeypatch.context() as patch:
            if not fired and phase == stage:
                patch.setattr(subject.os, "fsync", fsync)
            real_atomic(directory, name, raw)

    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", atomic)
                with scoped.session(
                    SOURCE, token(case.scope, SOURCE), terminal_repair=binding
                ) as session:
                    with pytest.raises(OSError, match="fsync crash"):
                        session.prepare(**requests[SOURCE])
            assert fired and saved
            assert_originals_unchanged(case)
    durable_registration = not (stage == "registering" and fault == "file_fsync")
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            assert scoped.begin_terminal_repair(binding) == binding
            # Diagnostics must not materialize even a terminal REGISTERING receipt.
            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", no_mutation)
                with scoped.session(SOURCE, token(case.scope, SOURCE)) as session:
                    observed = session.load()
                    assert (observed is not None) == durable_registration
                    if observed is not None:
                        assert observed.encode() == saved[0].encode()
                        with pytest.raises(ValueError, match="quarantin"):
                            session.mark_sent(observed)
            with scoped.session(
                SOURCE, token(case.scope, SOURCE), terminal_repair=binding
            ) as session:
                recovered = session.prepare(**requests[SOURCE])
                if durable_registration:
                    assert recovered.encode() == saved[0].encode()
                assert recovered.state == "prepared"
                sent = session.mark_sent(recovered)
                with pytest.raises(ValueError):
                    session.mark_sent(sent)
            assert scoped.generation == case.quarantine.generation + 1
            assert_originals_unchanged(case)


@pytest.mark.parametrize("fault", ["file_fsync", "directory_fsync"])
def test_plan_fsync_failure_has_no_receipt_effect_and_restart_is_idempotent(
    quarantined, monkeypatch, fault
):
    case = quarantined
    binding, _ = plan(case)
    real_fsync = os.fsync
    fired = False

    def fsync(fd):
        nonlocal fired
        if not fired and stat.S_ISDIR(os.fstat(fd).st_mode) == (
            fault == "directory_fsync"
        ):
            fired = True
            raise OSError("injected plan fsync crash")
        return real_fsync(fd)

    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            with monkeypatch.context() as patch:
                patch.setattr(subject.os, "fsync", fsync)
                with pytest.raises(OSError, match="plan fsync crash"):
                    scoped.begin_terminal_repair(binding)
            assert fired
            document = json.loads(index_path(case, scoped).read_bytes())
            assert ("terminal_repair" in document) == (fault == "directory_fsync")
            assert_originals_unchanged(case)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            assert scoped.begin_terminal_repair(binding) == binding
            assert scoped.generation == case.quarantine.generation
            assert_originals_unchanged(case)


@pytest.mark.parametrize("fault", ["file_fsync", "directory_fsync"])
def test_sent_fsync_failure_never_returns_dispatch_authorization(
    quarantined, monkeypatch, fault
):
    case = quarantined
    binding, requests = plan(case)
    real_fsync = os.fsync
    dispatched = []
    fired = False

    def fsync(fd):
        nonlocal fired
        if not fired and stat.S_ISDIR(os.fstat(fd).st_mode) == (
            fault == "directory_fsync"
        ):
            fired = True
            raise OSError("injected Sent fsync crash")
        return real_fsync(fd)

    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            with scoped.session(
                SOURCE, token(case.scope, SOURCE), terminal_repair=binding
            ) as session:
                prepared = session.prepare(**requests[SOURCE])
                with monkeypatch.context() as patch:
                    patch.setattr(subject.os, "fsync", fsync)
                    with pytest.raises(OSError, match="Sent fsync crash"):
                        session.mark_sent(prepared)
                        dispatched.append("would dispatch")
            assert fired and not dispatched
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            with scoped.session(
                SOURCE, token(case.scope, SOURCE), terminal_repair=binding
            ) as session:
                observed = session.load()
                assert observed.query_id == prepared.query_id
                assert observed.state == (
                    "sent" if fault == "directory_fsync" else "prepared"
                )
                if observed.state == "sent":
                    with pytest.raises(ValueError, match="no replay"):
                        session.mark_sent(observed)
                else:
                    assert session.mark_sent(observed).state == "sent"
            assert_originals_unchanged(case)


def test_original_registering_can_remain_read_only_alongside_complete_repair(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    real_atomic = subject._atomic
    original_token = "original-registering"
    original_key = subject._key(SOURCE, original_token)
    saved = []

    def atomic(directory, name, raw):
        real_atomic(directory, name, raw)
        entry = json.loads(raw).get("pending", {}).get(original_key)
        if entry and entry["stage"] == "registering":
            saved.append(
                subject.NativeWriteAttempt(
                    subject._canonical(entry["prepared"]) + b"\n"
                )
            )
            raise OSError("original registration interrupted")

    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal, SOURCE)
        with journal.scope(scope, create=True) as scoped:
            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", atomic)
                with scoped.session(SOURCE, original_token) as session:
                    with pytest.raises(OSError, match="interrupted"):
                        session.prepare(**options(admission, SOURCE))
            quarantine = scoped.quarantine(scoped.capture_quarantine(REASON))
            case = SimpleNamespace(
                admission=admission, scope=scope, quarantine=quarantine
            )
            binding, requests = plan(case, activation=True)
            scoped.begin_terminal_repair(binding)
            completed = complete_plan(scoped, binding, requests, admission)
            receipt = scoped.finish_terminal_repair(binding, completed)
    with subject.NativeWriteJournal(tmp_path) as journal:
        with journal.scope(scope) as scoped:
            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", no_mutation)
                assert scoped.finish_terminal_repair(binding, completed) == receipt
                with scoped.session(SOURCE, original_token) as session:
                    assert session.load().encode() == saved[0].encode()
                    with pytest.raises(ValueError):
                        session.mark_sent(saved[0])
            assert not path(tmp_path, SOURCE, original_token).exists()
            assert scoped.quarantine_binding == quarantine


@pytest.mark.parametrize("closure_state", ["reserved", "complete"])
def test_terminal_repair_never_reopens_preexisting_publication_closure(
    tmp_path, closure_state
):
    admission = admit(tmp_path)
    request, active_binding = publication(admission)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal, ACTIVE)
        with journal.scope(scope, create=True) as scoped:
            closure = scoped.begin_closure(active_binding)
            with scoped.session(ACTIVE, active_binding.deduplication_token) as session:
                active = finish(session, session.prepare(**request), admission)
            if closure_state == "complete":
                closure = scoped.finish_closure(closure, active)
                scoped.require_closed(closure)
            quarantine = scoped.quarantine(scoped.capture_quarantine(REASON))
            case = SimpleNamespace(
                admission=admission, scope=scope, quarantine=quarantine
            )
            binding, requests = plan(case, activation=True)
            for phase in ("before", "after"):
                for operation in (
                    lambda: scoped.begin_closure(active_binding),
                    lambda: scoped.finish_closure(closure, active),
                    lambda: scoped.require_closed(closure),
                ):
                    with pytest.raises(ValueError, match="quarantin"):
                        operation()
                if phase == "before":
                    scoped.begin_terminal_repair(binding)
                    completed = complete_plan(scoped, binding, requests, admission)
                    scoped.finish_terminal_repair(binding, completed)
    with subject.NativeWriteJournal(tmp_path) as journal:
        with journal.scope(scope) as scoped:
            with pytest.raises(ValueError, match="quarantin"):
                scoped.require_closed(closure)
            assert scoped.quarantine_binding == quarantine


def test_repair_requires_exact_live_build_scope_and_already_persisted_quarantine(
    quarantined,
):
    case = quarantined
    binding, _ = plan(case)
    with subject.NativeWriteJournal(case.directory) as journal:
        foreign = replace(case.scope, workspace_id=str(UUID(int=91)))
        control = replace(
            case.scope, kind="control", catalog_revision=None, build_token=None
        )
        for scope in (foreign, control):
            with journal.scope(scope, create=True) as scoped:
                with pytest.raises(ValueError):
                    scoped.begin_terminal_repair(binding)
                if scope == foreign:
                    own_capture = scoped.capture_quarantine(REASON)
                    with pytest.raises(ValueError):
                        scoped.begin_terminal_repair(
                            replace(binding, quarantine=own_capture)
                        )
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
        with pytest.raises(ValueError):
            scoped.begin_terminal_repair(binding)
        with pytest.raises(ValueError):
            scoped.finish_terminal_repair(binding, ())


@pytest.mark.parametrize("state", ["prepared", "complete"])
def test_preexisting_receipt_at_terminal_token_is_never_adopted(tmp_path, state):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal, SOURCE)
        with journal.scope(scope, create=True) as scoped:
            request = terminal_request(admission, scope, SOURCE)
            with scoped.session(SOURCE, token(scope, SOURCE)) as session:
                attempt = session.prepare(**request)
                if state == "complete":
                    attempt = finish(session, attempt, admission)
            quarantine = scoped.quarantine(scoped.capture_quarantine(REASON))
            case = SimpleNamespace(
                admission=admission, scope=scope, quarantine=quarantine
            )
            binding, _ = plan(case)
            with pytest.raises(ValueError):
                scoped.begin_terminal_repair(binding)
            assert (
                path(tmp_path, SOURCE, token(scope, SOURCE)).read_bytes()
                == attempt.encode()
            )


@pytest.mark.parametrize(
    "change",
    [
        "checksum",
        "scope",
        "admission",
        "generation",
        "intent",
        "unknown_field",
        "drop_plan",
    ],
)
def test_corrupt_or_cross_bound_persisted_plan_is_not_silently_dropped(
    quarantined, change
):
    case = quarantined
    binding, requests = plan(case)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            with scoped.session(
                SOURCE, token(case.scope, SOURCE), terminal_repair=binding
            ) as session:
                session.prepare(**requests[SOURCE])
            filename = index_path(case, scoped)
    document = json.loads(filename.read_bytes())
    terminal = document["terminal_repair"]
    if change == "checksum":
        document["sha256"] = "0" * 64
    elif change == "scope":
        terminal["binding"]["quarantine"]["scope"]["workspace_id"] = str(UUID(int=91))
    elif change == "admission":
        terminal["binding"]["quarantine"]["admission_sha256"] = "d" * 64
    elif change == "generation":
        document["generation"] += 1
    elif change == "intent":
        next(iter(terminal["attempts"].values()))["parameters_sha256"] = "d" * 64
    elif change == "unknown_field":
        terminal["allow_arbitrary_writes"] = True
    else:
        del document["terminal_repair"]
    if change != "checksum":
        document["sha256"] = subject._hash(
            {key: value for key, value in document.items() if key != "sha256"}
        )
    filename.write_bytes(subject._canonical(document) + b"\n")
    with subject.NativeWriteJournal(case.directory) as journal:
        with pytest.raises(ValueError, match="scope index"):
            with journal.scope(case.scope):
                pytest.fail("corrupt terminal scope accepted")
    assert_originals_unchanged(case)


def test_terminal_plan_and_receipts_use_only_exact_scope_no_global_enumeration(
    quarantined, monkeypatch
):
    case = quarantined
    binding, requests = plan(case, activation=True)
    with monkeypatch.context() as patch:
        for name in ("listdir", "scandir", "walk"):
            patch.setattr(subject.os, name, no_mutation)
        for name in ("iterdir", "glob", "rglob"):
            patch.setattr(Path, name, no_mutation)
        with subject.NativeWriteJournal(case.directory) as journal:
            with journal.scope(case.scope) as scoped:
                scoped.begin_terminal_repair(binding)
                completed = complete_plan(scoped, binding, requests, case.admission)
                receipt = scoped.finish_terminal_repair(binding, completed)
                assert receipt.binding == binding
                assert len(scoped.pending()) == len(case.originals)
                assert_originals_unchanged(case)


@pytest.mark.parametrize(
    "activation_state", ["absent", "prepared", "sent", "acknowledged"]
)
def test_source_cannot_register_before_exact_activation_is_complete(
    quarantined, monkeypatch, activation_state
):
    case = quarantined
    binding, requests = plan(case, activation=True)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            if activation_state != "absent":
                with scoped.session(
                    ACTIVE, token(case.scope, ACTIVE), terminal_repair=binding
                ) as session:
                    attempt = session.prepare(**requests[ACTIVE])
                    if activation_state != "prepared":
                        attempt = session.mark_sent(attempt)
                    if activation_state == "acknowledged":
                        session.mark_acknowledged(attempt, evidence=ack(attempt))
            before = index_path(case, scoped).read_bytes()
            with scoped.session(
                SOURCE, token(case.scope, SOURCE), terminal_repair=binding
            ) as session:
                with monkeypatch.context() as patch:
                    patch.setattr(subject, "_atomic", no_mutation)
                    with pytest.raises(ValueError, match="activation must complete"):
                        session.prepare(**requests[SOURCE])
                assert session.load() is None
            assert index_path(case, scoped).read_bytes() == before
            assert_originals_unchanged(case)


@pytest.mark.parametrize("operation", ["prepare", "send"])
@pytest.mark.parametrize(
    "corruption",
    ["missing", "checksum", "partial_members", "unregistered", "different_intent"],
)
def test_source_rechecks_durable_activation_proof_before_registration_and_send(
    quarantined, monkeypatch, operation, corruption
):
    case = quarantined
    binding, requests = plan(case, activation=True)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            with scoped.session(
                ACTIVE, token(case.scope, ACTIVE), terminal_repair=binding
            ) as session:
                active = finish(
                    session, session.prepare(**requests[ACTIVE]), case.admission
                )
            if operation == "send":
                with scoped.session(
                    SOURCE, token(case.scope, SOURCE), terminal_repair=binding
                ) as session:
                    prepared = session.prepare(**requests[SOURCE])
            active_file = path(case.directory, ACTIVE, token(case.scope, ACTIVE))
            if corruption == "missing":
                active_file.unlink()
            elif corruption == "unregistered":
                filename = index_path(case, scoped)
                document = json.loads(filename.read_bytes())
                del document["terminal_repair"]["attempts"][
                    subject._key(ACTIVE, token(case.scope, ACTIVE))
                ]
                document["generation"] -= 1
                document["sha256"] = subject._hash(
                    {key: value for key, value in document.items() if key != "sha256"}
                )
                filename.write_bytes(subject._canonical(document) + b"\n")
            else:
                document = json.loads(active.encode())
                if corruption == "checksum":
                    document["record_sha256"] = "0" * 64
                    raw = subject._canonical(document) + b"\n"
                else:
                    if corruption == "partial_members":
                        document["completion"]["members"] = [
                            case.admission.members[0].name
                        ]
                    else:
                        replacement_query_id = "00000000-0000-4000-8000-000000000091"
                        document["query_id"] = replacement_query_id
                        document["acknowledgement"]["query_id"] = replacement_query_id
                        document["completion"]["query_id"] = replacement_query_id
                    raw = subject._record(document).encode()
                active_file.write_bytes(raw)
    # Fresh journal defeats any accidental reliance on in-memory Complete objects.
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            with scoped.session(
                SOURCE, token(case.scope, SOURCE), terminal_repair=binding
            ) as session:
                with monkeypatch.context() as patch:
                    patch.setattr(subject, "_atomic", no_mutation)
                    with pytest.raises(ValueError):
                        if operation == "prepare":
                            session.prepare(**requests[SOURCE])
                        else:
                            session.mark_sent(prepared)
                if operation == "send":
                    assert session.load().encode() == prepared.encode()
                else:
                    assert session.load() is None
            assert_originals_unchanged(case)


@pytest.mark.parametrize("activation", [False, True])
def test_finish_recovers_complete_fsync_before_pending_removal_without_original_settlement(
    quarantined, monkeypatch, activation
):
    case = quarantined
    binding, requests = plan(case, activation=activation)
    completed = []

    def lost_reference_removal(*args, **kwargs):
        raise OSError("crash after durable Complete before pending removal")

    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            scoped.begin_terminal_repair(binding)
            for write in binding.writes:
                with scoped.session(
                    write.table, write.deduplication_token, terminal_repair=binding
                ) as session:
                    sent = session.mark_sent(session.prepare(**requests[write.table]))
                    acknowledged = session.mark_acknowledged(sent, evidence=ack(sent))
                    with monkeypatch.context() as patch:
                        patch.setattr(
                            scoped, "_forget_completed", lost_reference_removal
                        )
                        with pytest.raises(OSError, match="pending removal"):
                            session.mark_complete(
                                acknowledged,
                                evidence=coverage(acknowledged, case.admission),
                            )
                    completed.append(session.load())
                    assert completed[-1].state == "complete"
            document = json.loads(index_path(case, scoped).read_bytes())
            assert len(document["pending"]) == len(case.originals) + len(binding.writes)
            assert document["terminal_repair"]["completion"] is None
            assert_originals_unchanged(case)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            receipt = scoped.finish_terminal_repair(binding, tuple(completed))
            document = json.loads(index_path(case, scoped).read_bytes())
            assert len(document["pending"]) == len(case.originals)
            assert receipt.attempt_record_sha256s == tuple(
                a["record_sha256"] for a in completed
            )
            assert document["terminal_repair"]["completion"] == list(
                receipt.attempt_record_sha256s
            )
            assert scoped.quarantine_binding == case.quarantine
            assert_originals_unchanged(case)


def test_full_original_pending_budget_still_allows_only_bounded_terminal_plan(
    quarantined, monkeypatch
):
    case = quarantined
    # Exercise the exact boundary without manufacturing 128 receipt files.
    monkeypatch.setattr(subject, "MAX_SCOPE_PENDING", len(case.originals))
    binding, requests = plan(case, activation=True)
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            assert len(scoped.pending(limit=2)) == 2
            scoped.begin_terminal_repair(binding)
            completed = complete_plan(scoped, binding, requests, case.admission)
            scoped.finish_terminal_repair(binding, completed)
            assert scoped.generation == case.quarantine.generation + 2
            assert len(scoped.pending(limit=2)) == 2
            with scoped.session(SOURCE, "third-terminal-attempt") as session:
                with pytest.raises(ValueError, match="quarantin"):
                    session.prepare(**requests[SOURCE])
            assert_originals_unchanged(case)
