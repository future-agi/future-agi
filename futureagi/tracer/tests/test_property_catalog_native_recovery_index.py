"""Bounded workspace pointers; operation state remains in the real scoped journal."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    native_write_journal as subject,
)
from tracer.tests.test_property_catalog_native_terminal_repair import (
    complete_plan,
    plan,
)
from tracer.tests.test_property_catalog_native_write_journal import (
    ack,
    finish,
    options,
    path,
    scope_for,
)
from tracer.tests.test_property_catalog_write_admission import Probe, admit

SOURCE = "property_catalog_source_streams"


def workspace(scope):
    return {
        field: getattr(scope, field)
        for field in (
            "organization_id",
            "workspace_id",
            "catalog_epoch",
            "projection_version",
        )
    }


def build(journal, number=0):
    scope = scope_for(journal, SOURCE)
    return replace(
        scope,
        catalog_revision=scope.catalog_revision + number,
        build_token=str(UUID(int=3 + number)),
    )


def request(admission, scope):
    result = options(admission, SOURCE)
    result["rows"][0].update(
        {field: value for field, value in asdict(scope).items() if field != "kind"}
    )
    return result


def pointer_path(directory, scope, suffix="index"):
    return path(directory, SOURCE).parent / (
        subject._hash(workspace(scope))
        + ".recovery"
        + ("-index" if suffix == "index" else "." + suffix)
    )


def no_mutation(*args, **kwargs):
    pytest.fail("unexpected receipt mutation or global enumeration")


def rewrite(filename, document):
    document["sha256"] = subject._hash(
        {key: value for key, value in document.items() if key != "sha256"}
    )
    filename.write_bytes(subject._canonical(document) + b"\n")


def test_tracks_multiple_builds_before_prepare_and_reload_contains_no_copied_state(
    tmp_path,
):
    admission = admit(tmp_path)
    receipts = []
    with subject.NativeWriteJournal(tmp_path) as journal:
        scopes = tuple(build(journal, number) for number in range(3))
        for scope in scopes:
            with journal.scope(scope, create=True) as scoped:
                with scoped.session(SOURCE, scope.build_token) as session:
                    journal.track_recovery_scope(scoped)
                    before = pointer_path(tmp_path, scope).read_bytes()
                    attempt = session.mark_sent(
                        session.prepare(**request(admission, scope))
                    )
                    receipts.append(attempt)
                assert pointer_path(tmp_path, scope).read_bytes() == before
                journal.track_recovery_scope(scoped)
        document = json.loads(pointer_path(tmp_path, scopes[0]).read_bytes())
        assert set(document) == {
            "format",
            "version",
            "workspace",
            "admission_sha256",
            "scopes",
            "sha256",
        }
        assert {subject._canonical(row) for row in document["scopes"]} == {
            subject._canonical(asdict(scope)) for scope in scopes
        }
        assert all(
            attempt.query_id.encode()
            not in pointer_path(tmp_path, scopes[0]).read_bytes()
            for attempt in receipts
        )
    with subject.NativeWriteJournal(tmp_path) as journal:
        found = journal.recovery_scopes(**workspace(scopes[0]))
        assert set(found) == set(scopes)
        for scope in found:
            # Returned snapshots must not retain the workspace-index lock.
            with journal.scope(scope) as scoped:
                journal.track_recovery_scope(scoped)
                assert len(scoped.pending()) == 1
        for attempt in receipts:
            assert (
                path(tmp_path, SOURCE, attempt["deduplication_token"]).read_bytes()
                == attempt.encode()
            )


@pytest.mark.parametrize(
    "phase", ["before_registering", "registering", "prepared", "sent"]
)
def test_pointer_survives_crash_gaps_and_recovery_reads_only_the_known_scope(
    tmp_path, monkeypatch, phase
):
    admission = admit(tmp_path)
    original_atomic = subject._atomic
    frozen = []

    def crash(directory, name, raw):
        original_atomic(directory, name, raw)
        document = json.loads(raw)
        entries = document.get("pending", {})
        if entries and next(iter(entries.values()))["stage"] == "registering":
            frozen.append(next(iter(entries.values()))["prepared"])
            raise OSError("crash after REGISTERING")

    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(SOURCE, "crash-gap") as session:
                journal.track_recovery_scope(scoped)
                if phase == "registering":
                    with monkeypatch.context() as patch:
                        patch.setattr(subject, "_atomic", crash)
                        with pytest.raises(OSError, match="REGISTERING"):
                            session.prepare(**request(admission, scope))
                elif phase != "before_registering":
                    attempt = session.prepare(**request(admission, scope))
                    if phase == "sent":
                        attempt = session.mark_sent(attempt)
                    frozen.append(json.loads(attempt.encode()))
    with subject.NativeWriteJournal(tmp_path) as journal:
        with monkeypatch.context() as patch:
            patch.setattr(subject, "_atomic", no_mutation)
            assert journal.recovery_scopes(**workspace(scope)) == (scope,)
        with journal.scope(scope) as scoped:
            with scoped.session(SOURCE, "crash-gap") as session:
                observed = session.load()
                if phase == "before_registering":
                    assert observed is None
                else:
                    assert observed.query_id == frozen[0]["query_id"]
                    assert observed.state == ("sent" if phase == "sent" else "prepared")


def test_no_scan_or_legacy_receipt_adoption_and_workspace_isolation(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        legacy = build(journal)
        with journal.scope(legacy, create=True) as scoped:
            with monkeypatch.context() as patch:
                patch.setattr(journal, "track_recovery_scope", lambda _: None)
                with scoped.session(SOURCE, "legacy-unindexed") as session:
                    attempt = session.prepare(**request(admission, legacy))
        foreign = replace(legacy, workspace_id=str(UUID(int=91)))
        with journal.scope(foreign, create=True) as scoped:
            journal.track_recovery_scope(scoped)
        with monkeypatch.context() as patch:
            for name in ("listdir", "scandir", "walk"):
                patch.setattr(subject.os, name, no_mutation)
            for name in ("glob", "rglob", "iterdir"):
                patch.setattr(Path, name, no_mutation)
            assert journal.recovery_scopes(**workspace(legacy)) == ()
            assert journal.recovery_scopes(**workspace(foreign)) == (foreign,)
            with journal.scope(legacy) as scoped:
                journal.track_recovery_scope(scoped)
                assert journal.recovery_scopes(**workspace(legacy)) == (legacy,)
        assert (
            path(tmp_path, SOURCE, "legacy-unindexed").read_bytes() == attempt.encode()
        )


def test_bound_overflow_cannot_evict_existing_scopes(tmp_path, monkeypatch):
    admit(tmp_path)
    monkeypatch.setattr(subject.NativeWriteJournal, "MAX_RECOVERY_SCOPES", 2)
    with subject.NativeWriteJournal(tmp_path) as journal:
        first, second, third = (build(journal, number) for number in range(3))
        for scope in (first, second):
            with journal.scope(scope, create=True) as scoped:
                journal.track_recovery_scope(scoped)
        before = pointer_path(tmp_path, first).read_bytes()
        with journal.scope(third, create=True) as scoped:
            with pytest.raises(ValueError, match="scope bound"):
                journal.track_recovery_scope(scoped)
            assert scoped.pending() == ()
        assert pointer_path(tmp_path, first).read_bytes() == before
        assert set(journal.recovery_scopes(**workspace(first))) == {first, second}


@pytest.mark.parametrize("state", ["prepared", "sent", "acknowledged"])
def test_unresolved_scope_cannot_be_cleared_without_terminal_receipt(tmp_path, state):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            journal.track_recovery_scope(scoped)
            with scoped.session(SOURCE, "unresolved") as session:
                attempt = session.prepare(**request(admission, scope))
                if state != "prepared":
                    attempt = session.mark_sent(attempt)
                if state == "acknowledged":
                    attempt = session.mark_acknowledged(attempt, evidence=ack(attempt))
            before = pointer_path(tmp_path, scope).read_bytes()
            assert not journal.clear_recovery_scope(
                scoped, expected_generation=scoped.generation
            )
            assert pointer_path(tmp_path, scope).read_bytes() == before
            assert path(tmp_path, SOURCE, "unresolved").read_bytes() == attempt.encode()


def test_stale_generation_cannot_clear_newer_even_empty_scope_and_other_build_is_preserved(
    tmp_path,
):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope, other = build(journal), build(journal, 1)
        with journal.scope(other, create=True) as scoped:
            journal.track_recovery_scope(scoped)
        with journal.scope(scope, create=True) as scoped:
            journal.track_recovery_scope(scoped)
            previous = scoped.generation
            for token in ("first", "second"):
                with scoped.session(SOURCE, token) as session:
                    finish(
                        session, session.prepare(**request(admission, scope)), admission
                    )
            assert scoped.pending() == ()
            assert not journal.clear_recovery_scope(
                scoped, expected_generation=previous
            )
            assert set(journal.recovery_scopes(**workspace(scope))) == {scope, other}
            assert journal.clear_recovery_scope(
                scoped, expected_generation=scoped.generation
            )
            assert not journal.clear_recovery_scope(
                scoped, expected_generation=scoped.generation
            )
            assert journal.recovery_scopes(**workspace(scope)) == (other,)


def test_clear_does_not_reconcile_complete_pending_reference_itself(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            journal.track_recovery_scope(scoped)
            with scoped.session(SOURCE, "pending-complete") as session:
                prepared = session.prepare(**request(admission, scope))

                def crash(*args):
                    raise OSError("pending reference removal crashed")

                with monkeypatch.context() as patch:
                    patch.setattr(scoped, "_forget_completed", crash)
                    with pytest.raises(OSError, match="removal crashed"):
                        finish(session, prepared, admission)
                completed = session.load()
                assert completed.state == "complete"
            assert not journal.clear_recovery_scope(
                scoped, expected_generation=scoped.generation
            )
            assert len(scoped.pending()) == 1
            assert not journal.clear_recovery_scope(
                scoped, expected_generation=scoped.generation
            )
            assert (
                path(tmp_path, SOURCE, "pending-complete").read_bytes()
                == completed.encode()
            )


@pytest.fixture
def terminal(tmp_path):
    admission = admit(tmp_path, Probe(2))
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            journal.track_recovery_scope(scoped)
            with scoped.session(SOURCE, "original-sent") as session:
                original = session.mark_sent(
                    session.prepare(**request(admission, scope))
                )
            quarantine = scoped.quarantine(
                scoped.capture_quarantine(
                    subject.NativeQuarantineReason.UNRESOLVED_NATIVE_WRITE
                )
            )
            case = SimpleNamespace(
                directory=tmp_path,
                admission=admission,
                scope=scope,
                quarantine=quarantine,
            )
            binding, requests = plan(case, activation=True)
            scoped.begin_terminal_repair(binding)
            completed = complete_plan(scoped, binding, requests, admission)
            receipt = scoped.finish_terminal_repair(binding, completed)
    return case, original, completed, receipt


def test_exact_completed_terminal_receipt_clears_only_pointer_and_keeps_original_unresolved(
    terminal,
):
    case, original, completed, receipt = terminal
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            generation = scoped.generation
            assert not journal.clear_recovery_scope(
                scoped, expected_generation=generation
            )
            assert journal.clear_recovery_scope(
                scoped, expected_generation=generation, terminal_receipt=receipt
            )
            assert scoped.generation == generation
            assert scoped.quarantine_binding == case.quarantine
            assert [entry.query_id for entry in scoped.pending()] == [original.query_id]
        assert journal.recovery_scopes(**workspace(case.scope)) == ()
    for attempt in (original, *completed):
        assert (
            path(
                case.directory, attempt["table"], attempt["deduplication_token"]
            ).read_bytes()
            == attempt.encode()
        )


@pytest.mark.parametrize(
    "damage",
    ["wrong_receipt", "wrong_binding", "missing", "corrupt", "partial_members"],
)
def test_invalid_terminal_completion_never_clears_pointer(terminal, damage):
    case, original, completed, receipt = terminal
    filename = path(
        case.directory, completed[0]["table"], completed[0]["deduplication_token"]
    )
    if damage == "wrong_receipt":
        receipt = replace(
            receipt,
            attempt_record_sha256s=("0" * 64, receipt.attempt_record_sha256s[1]),
        )
    elif damage == "wrong_binding":
        receipt = replace(
            receipt, binding=replace(receipt.binding, build_lease_sha256="d" * 64)
        )
    elif damage == "missing":
        filename.unlink()
    elif damage == "corrupt":
        filename.write_bytes(b"corrupt\n")
    else:
        document = json.loads(completed[0].encode())
        document["completion"]["members"] = [case.admission.members[0].name]
        filename.write_bytes(subject._record(document).encode())
    before = pointer_path(case.directory, case.scope).read_bytes()
    with subject.NativeWriteJournal(case.directory) as journal:
        with journal.scope(case.scope) as scoped:
            with pytest.raises(ValueError):
                journal.clear_recovery_scope(
                    scoped,
                    expected_generation=scoped.generation,
                    terminal_receipt=receipt,
                )
        assert journal.recovery_scopes(**workspace(case.scope)) == (case.scope,)
    assert pointer_path(case.directory, case.scope).read_bytes() == before
    assert (
        path(case.directory, SOURCE, "original-sent").read_bytes() == original.encode()
    )


@pytest.mark.parametrize(
    "damage",
    [
        "checksum",
        "version",
        "admission",
        "workspace",
        "duplicate",
        "control",
        "foreign_scope",
        "missing_kind",
        "extra_state",
        "oversized",
        "truncated",
    ],
)
def test_corruption_or_cross_binding_rejects_instead_of_empty_lookup(tmp_path, damage):
    admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            journal.track_recovery_scope(scoped)
    filename = pointer_path(tmp_path, scope)
    document = json.loads(filename.read_bytes())
    if damage == "checksum":
        document["sha256"] = "0" * 64
        filename.write_bytes(subject._canonical(document) + b"\n")
    elif damage == "oversized":
        filename.write_bytes(
            b"x" * (subject.NativeWriteJournal._MAX_RECOVERY_BYTES + 1)
        )
    elif damage == "truncated":
        filename.write_bytes(b"{\n")
    else:
        if damage == "version":
            document["version"] = True
        elif damage == "admission":
            document["admission_sha256"] = "d" * 64
        elif damage == "workspace":
            document["workspace"]["workspace_id"] = str(UUID(int=91))
        elif damage == "duplicate":
            document["scopes"] *= 2
        elif damage == "control":
            document["scopes"] = [
                asdict(
                    replace(
                        scope, kind="control", catalog_revision=None, build_token=None
                    )
                )
            ]
        elif damage == "foreign_scope":
            document["scopes"][0]["workspace_id"] = str(UUID(int=91))
        elif damage == "missing_kind":
            del document["scopes"][0]["kind"]
        else:
            document["pending"] = []
        rewrite(filename, document)
    with subject.NativeWriteJournal(tmp_path) as journal:
        with pytest.raises(ValueError):
            journal.recovery_scopes(**workspace(scope))
        with journal.scope(scope) as scoped:
            with pytest.raises(ValueError):
                journal.track_recovery_scope(scoped)


@pytest.mark.parametrize("extension", ["index", "lock"])
@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "permissions"])
def test_index_and_lock_paths_reject_unsafe_files_without_overwriting_targets(
    tmp_path, extension, unsafe
):
    admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            journal.track_recovery_scope(scoped)
        filename = pointer_path(tmp_path, scope, extension)
        original = filename.read_bytes()
        target = tmp_path / "protected-local-fixture"
        target.write_bytes(original)
        target.chmod(0o600)
        if unsafe == "permissions":
            filename.chmod(0o644)
        else:
            filename.unlink()
            if unsafe == "symlink":
                filename.symlink_to(target)
            else:
                os.link(target, filename)
        with pytest.raises((OSError, ValueError)):
            journal.recovery_scopes(**workspace(scope))
        with journal.scope(scope) as scoped:
            with pytest.raises((OSError, ValueError)):
                journal.track_recovery_scope(scoped)
        assert target.read_bytes() == original


def test_deleted_initialized_index_cannot_be_mistaken_for_empty_legacy_workspace(
    tmp_path,
):
    admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            journal.track_recovery_scope(scoped)
        assert pointer_path(tmp_path, scope, "lock").read_bytes() == b"1"
        pointer_path(tmp_path, scope).unlink()
    with subject.NativeWriteJournal(tmp_path) as journal:
        with pytest.raises(ValueError, match="missing initialized"):
            journal.recovery_scopes(**workspace(scope))
        with journal.scope(scope) as scoped:
            with pytest.raises(ValueError, match="missing initialized"):
                journal.track_recovery_scope(scoped)
        assert not pointer_path(tmp_path, scope).exists()


def test_recovery_index_is_not_named_as_an_attempt_json(tmp_path):
    admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            journal.track_recovery_scope(scoped)
        filename = pointer_path(tmp_path, scope)
        assert filename.suffix == ".recovery-index" and filename.exists()
        assert filename.stat().st_mode & 0o777 == 0o600


def test_reader_snapshot_does_not_classify_busy_build_or_hold_index_lock(tmp_path):
    admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        subject.NativeWriteJournal(tmp_path) as other,
    ):
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            journal.track_recovery_scope(scoped)
            assert other.recovery_scopes(**workspace(scope)) == (scope,)
            with pytest.raises(subject.NativeWriteJournalBusy):
                with other.scope(scope):
                    pytest.fail("ongoing BUILD was acquired")
            journal.track_recovery_scope(scoped)
        with journal._locked(subject._hash(workspace(scope)) + ".recovery"):
            with pytest.raises(subject.NativeWriteJournalBusy):
                other.recovery_scopes(**workspace(scope))
            foreign = replace(scope, workspace_id=str(UUID(int=91)))
            assert other.recovery_scopes(**workspace(foreign)) == ()


def test_mutations_require_own_live_build_and_clear_requires_idle_scope(tmp_path):
    admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        subject.NativeWriteJournal(tmp_path) as other,
    ):
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            with pytest.raises(ValueError):
                other.track_recovery_scope(scoped)
            with scoped.session(SOURCE, "active-session"):
                journal.track_recovery_scope(scoped)
                with pytest.raises(ValueError, match="nested locks"):
                    journal.clear_recovery_scope(
                        scoped, expected_generation=scoped.generation
                    )
        with pytest.raises(ValueError):
            journal.track_recovery_scope(scoped)
        control = replace(
            scope, kind="control", catalog_revision=None, build_token=None
        )
        with journal.scope(control, create=True) as scoped:
            with pytest.raises(ValueError, match="BUILD"):
                journal.track_recovery_scope(scoped)
        with pytest.raises(ValueError):
            journal.track_recovery_scope(scope)


@pytest.mark.parametrize(
    "change",
    [
        {"organization_id": "../outside"},
        {"workspace_id": str(UUID(int=0))},
        {"catalog_epoch": True},
        {"catalog_epoch": 2},
        {"projection_version": 2},
    ],
)
def test_workspace_lookup_requires_exact_installed_tenant_key(tmp_path, change):
    admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        with pytest.raises(ValueError):
            journal.recovery_scopes(**{**workspace(build(journal)), **change})


@pytest.mark.parametrize("fault", ["file_fsync", "directory_fsync"])
def test_track_fsync_failure_prevents_caller_registration_and_retry_is_durable(
    tmp_path, monkeypatch, fault
):
    admit(tmp_path)
    real_fsync = os.fsync
    fired = False

    def fsync(fd):
        nonlocal fired
        if not fired and stat.S_ISDIR(os.fstat(fd).st_mode) == (
            fault == "directory_fsync"
        ):
            fired = True
            raise OSError("pointer fsync failed")
        return real_fsync(fd)

    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            reached_register = []
            with monkeypatch.context() as patch:
                patch.setattr(subject.os, "fsync", fsync)
                with pytest.raises(OSError, match="pointer fsync failed"):
                    journal.track_recovery_scope(scoped)
                    reached_register.append(True)
            assert fired and not reached_register and scoped.pending() == ()
    with subject.NativeWriteJournal(tmp_path) as journal:
        expected = (scope,) if fault == "directory_fsync" else ()
        assert journal.recovery_scopes(**workspace(scope)) == expected
        with journal.scope(scope) as scoped:
            synced = []

            def record_sync(fd):
                synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
                return real_fsync(fd)

            with monkeypatch.context() as patch:
                patch.setattr(subject.os, "fsync", record_sync)
                journal.track_recovery_scope(scoped)
            assert True in synced
            assert journal.recovery_scopes(**workspace(scope)) == (scope,)


@pytest.mark.parametrize("table", tuple(subject._COLUMNS))
def test_direct_prepare_tracks_only_noncontrol_build_writes(tmp_path, table):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal, table)
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(table, "direct-prepare") as session:
                session.prepare(**options(admission, table))
        expected = (
            () if table == "property_catalog_activation_control_events" else (scope,)
        )
        assert journal.recovery_scopes(**workspace(scope)) == expected


def test_targetless_control_and_explicit_terminal_repair_never_autotrack(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    table = "property_catalog_activation_control_events"
    control_request = options(admission, table)
    control_request["rows"][0].update(
        action="disable", target_catalog_revision=0, target_build_token=""
    )
    with subject.NativeWriteJournal(tmp_path) as journal:
        control = subject.NativeWriteScope.from_rows(
            table, control_request["rows"], journal.identity
        )
        with monkeypatch.context() as patch:
            patch.setattr(journal, "track_recovery_scope", no_mutation)
            with journal.scope(control, create=True) as scoped:
                with scoped.session(table, "targetless-control") as session:
                    session.prepare(**control_request)
            scope = build(journal)
            with journal.scope(scope, create=True) as scoped:
                quarantine = scoped.quarantine(
                    scoped.capture_quarantine(
                        subject.NativeQuarantineReason.UNRESOLVED_NATIVE_WRITE
                    )
                )
                case = SimpleNamespace(
                    scope=scope, quarantine=quarantine, admission=admission
                )
                binding, requests = plan(case, activation=True)
                scoped.begin_terminal_repair(binding)
                completed = complete_plan(scoped, binding, requests, admission)
                scoped.finish_terminal_repair(binding, completed)
        assert journal.recovery_scopes(**workspace(scope)) == ()


def test_register_hook_fsyncs_pointer_before_original_seed_and_registering(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            initial = request(admission, scope)
            initial["rows"][0].update(
                source_adapter="system_manifest",
                producer_stream_id=scope.build_token,
                envelope_version=0,
                status="open",
                _version=1,
                build_lease_sha256="a" * 64,
            )
            retain_seed = scoped._retain_reservation_seed
            observed = []

            def check_before_seed(attempt):
                assert journal.recovery_scopes(**workspace(scope)) == (scope,)
                assert pointer_path(tmp_path, scope, "lock").read_bytes() == b"1"
                assert scoped._read_index()["pending"] == {}
                observed.append("tracked-before-seed")
                retain_seed(attempt)

            monkeypatch.setattr(scoped, "_retain_reservation_seed", check_before_seed)
            with scoped.session(SOURCE, "initial-source") as session:
                session.prepare(**initial)
            assert observed == ["tracked-before-seed"]
            assert len(scoped.pending()) == 1


def test_register_hook_pointer_failure_cannot_reach_seed_or_registering(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    atomic = subject._atomic

    def fail_pointer(directory, name, raw):
        if name.endswith(".recovery-index"):
            raise OSError("workspace pointer unavailable")
        return atomic(directory, name, raw)

    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = build(journal)
        with journal.scope(scope, create=True) as scoped:
            original = scoped._read_index()
            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", fail_pointer)
                patch.setattr(scoped, "_retain_reservation_seed", no_mutation)
                with scoped.session(SOURCE, "unregistered") as session:
                    with pytest.raises(OSError, match="pointer unavailable"):
                        session.prepare(**request(admission, scope))
                    assert session.load() is None
            assert scoped._read_index() == original
            assert not path(tmp_path, SOURCE, "unregistered").exists()
