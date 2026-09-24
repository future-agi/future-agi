"""Production handoff contracts: fake ledger, real durable files and locks."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    production_selection as subject,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ActivationControlAction,
    ActivationControlEvent,
    ActivationControlRejected,
    ActivationControlScope,
    ActivationControlTarget,
    QualifiedActivation,
    canonical_control_events,
    selected_control_target,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"
NOW = datetime(2026, 9, 8, 5, tzinfo=UTC)
SCOPE = ActivationControlScope(ORG, WORKSPACE)


def _target(revision: int = 5, **changes: Any) -> ActivationControlTarget:
    target = ActivationControlTarget(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        projection_version=3,
        catalog_revision=revision,
        build_token=str(UUID(int=1000 + revision)),
        activation_sha256=hashlib.sha256(f"activation:{revision}".encode()).hexdigest(),
    )
    return replace(target, **changes)


def _event(
    target: ActivationControlTarget,
    *,
    action: ActivationControlAction = ActivationControlAction.ACTIVATE,
    previous: ActivationControlEvent | None = None,
) -> ActivationControlEvent:
    sequence = previous.control_sequence + 1 if previous else 1
    return ActivationControlEvent.create(
        control_sequence=sequence,
        request_id=str(UUID(int=2000 + sequence)),
        action=action,
        target=target,
        previous_control_sha256=previous.control_sha256 if previous else "0" * 64,
        controlled_at=NOW - timedelta(minutes=10 - sequence),
    )


class LostAcknowledgement(RuntimeError):
    """The fake server committed, but its caller did not receive the result."""


class SimulatedCrash(BaseException):
    """Exercise durable recovery even when ordinary exception handlers cannot run."""


class _Store:
    """Only the control-store protocol exists: no DDL/source/lifecycle write API."""

    def __init__(
        self,
        targets: tuple[ActivationControlTarget, ...] | None = None,
        events: tuple[ActivationControlEvent, ...] = (),
    ) -> None:
        targets = targets if targets is not None else (_target(),)
        self.qualified = tuple(
            QualifiedActivation(target, index + 1)
            for index, target in enumerate(targets)
        )
        self.events = list(events)
        self.attempts: list[ActivationControlEvent] = []
        self.scopes: list[ActivationControlScope] = []
        self.before_append = None
        self.after_append = None

    def list_qualified_activations(self, scope: ActivationControlScope):
        self.scopes.append(scope)
        return self.qualified

    def list_control_events(self, scope: ActivationControlScope):
        self.scopes.append(scope)
        return tuple(self.events)

    def append_control_event(self, event, *, expected_head):
        self.attempts.append(event)
        head = self.events[-1].head if self.events else None
        if head != expected_head:
            raise ActivationControlRejected("control_concurrent")
        if self.before_append is not None:
            self.before_append(event)
        self.events.append(event)
        if self.after_append is not None:
            self.after_append(event)
        return event


def _publish(tmp_path: Path, store: _Store, **changes: Any):
    args = {
        "store": store,
        "scope": SCOPE,
        "catalog_epoch": 1,
        "projection_version": 3,
        "runtime_directory": str(tmp_path),
        "database": "property_catalog",
        "verify_target": lambda: _target(),
        "now": NOW,
    }
    args.update(changes)
    return subject.publish_with_store(**args)


def _intent_path(tmp_path: Path) -> Path:
    with subject.selection_lock(
        runtime_directory=str(tmp_path), database="property_catalog", scope=SCOPE
    ) as path:
        return path


def _assert_receipt(tmp_path: Path, event: ActivationControlEvent) -> bytes:
    raw = _intent_path(tmp_path).read_bytes()
    receipt = json.loads(raw)
    assert receipt["database"] == "property_catalog"
    expected_row = event.as_row()
    expected_row["controlled_at"] = event.controlled_at.isoformat()
    assert receipt["event"] == expected_row
    return raw


def test_initial_selection_appends_only_the_exact_verified_completed_target(tmp_path):
    store = _Store()
    qualified_before = store.qualified

    result = _publish(tmp_path, store)

    assert result["status"] == "published"
    assert result["catalog_revision"] == 5
    assert len(store.attempts) == len(store.events) == 1
    event = store.events[0]
    assert event.action is ActivationControlAction.ACTIVATE
    assert event.target == _target()
    assert event.control_sequence == 1
    assert event.controlled_at == NOW
    assert event.previous_control_sha256 == "0" * 64
    assert selected_control_target(store.events) == _target()
    assert store.qualified == qualified_before
    assert set(store.scopes) == {SCOPE}
    _assert_receipt(tmp_path, event)


def test_advance_uses_exact_current_head_without_modifying_qualification(tmp_path):
    previous = _event(_target(3))
    store = _Store((_target(3), _target()), (previous,))
    qualified_before = store.qualified

    result = _publish(tmp_path, store)

    assert result["status"] == "published"
    assert store.events[0] == previous
    assert store.events[-1].previous_control_sha256 == previous.control_sha256
    assert store.events[-1].control_sequence == 2
    assert store.events[-1].target == _target()
    assert store.qualified == qualified_before
    assert canonical_control_events(store.events, scope=SCOPE) == tuple(store.events)
    _assert_receipt(tmp_path, store.events[-1])


def test_already_selected_target_is_noop_and_records_observed_head(tmp_path):
    event = _event(_target())
    store = _Store(events=(event,))

    result = _publish(tmp_path, store)

    assert result["status"] == "noop"
    assert store.events == [event]
    assert not store.attempts
    _assert_receipt(tmp_path, event)


def test_no_verified_completed_target_does_not_allocate_or_publish(tmp_path):
    store = _Store(targets=())

    result = _publish(tmp_path, store, verify_target=lambda: None)

    assert result["status"] == "nothing"
    assert not store.events
    assert not store.attempts
    assert not _intent_path(tmp_path).exists()


@pytest.mark.parametrize(
    "action", (ActivationControlAction.ROLLBACK, ActivationControlAction.DISABLE)
)
def test_explicit_operator_head_is_held_even_with_a_newer_completed_target(
    tmp_path, action
):
    initial = _event(_target())
    held_target = (
        _target(3) if action is ActivationControlAction.ROLLBACK else _target()
    )
    held = _event(held_target, action=action, previous=initial)
    store = _Store((_target(3), _target(), _target(6)), (initial, held))

    result = _publish(tmp_path, store, verify_target=lambda: _target(6))

    assert result["status"] == "operator_held"
    assert store.events == [initial, held]
    assert selected_control_target(store.events) == (
        _target(3) if action is ActivationControlAction.ROLLBACK else None
    )
    assert not store.attempts
    _assert_receipt(tmp_path, held)


@pytest.mark.parametrize(
    "changes",
    (
        {"organization_id": OTHER},
        {"workspace_id": OTHER},
        {"catalog_epoch": 2},
        {"projection_version": 4},
    ),
    ids=("organization", "workspace", "epoch", "projection"),
)
def test_verified_target_cannot_cross_the_admitted_scope_or_version(tmp_path, changes):
    wrong = _target(**changes)
    store = _Store((wrong,))

    with pytest.raises((RuntimeError, ValueError)):
        _publish(tmp_path, store, verify_target=lambda: wrong)

    assert not store.events
    assert not store.attempts


@pytest.mark.parametrize(
    "mismatch", ("absent", "token", "sha", "older", "foreign_scope")
)
def test_verifier_and_qualified_ledger_must_agree_on_the_exact_latest_target(
    tmp_path, mismatch
):
    qualified = {
        "absent": (),
        "token": (_target(build_token=OTHER),),
        "sha": (_target(activation_sha256="f" * 64),),
        "older": (_target(), _target(6)),
        "foreign_scope": (_target(workspace_id=OTHER),),
    }[mismatch]
    store = _Store(qualified)

    with pytest.raises((RuntimeError, ValueError)):
        _publish(tmp_path, store)

    assert not store.events
    assert not store.attempts


def test_full_proof_failure_precedes_any_control_append(tmp_path):
    store = _Store()

    def reject_proof():
        raise ValueError("manifest/checkpoint/project coverage is not qualified")

    with pytest.raises(ValueError, match="coverage"):
        _publish(tmp_path, store, verify_target=reject_proof)

    assert not store.events
    assert not store.attempts
    assert not _intent_path(tmp_path).exists()


@pytest.mark.parametrize("failure", (LostAcknowledgement, SimulatedCrash))
def test_committed_append_with_lost_result_recovers_without_second_append(
    tmp_path, failure
):
    store = _Store()

    def lose_result(_event):
        raise failure("result was not received")

    store.after_append = lose_result
    with pytest.raises(failure):
        _publish(tmp_path, store)
    persisted = store.events[0]
    pending = _intent_path(tmp_path)
    assert pending.is_file()
    original_bytes = _assert_receipt(tmp_path, persisted)
    store.after_append = None

    result = _publish(tmp_path, store, now=NOW + timedelta(hours=1))

    assert result["status"] in {"published", "noop"}
    assert store.events == [persisted]
    assert store.attempts == [persisted]
    assert persisted.controlled_at == NOW
    assert _assert_receipt(tmp_path, persisted) == original_bytes


@pytest.mark.parametrize("failure", (LostAcknowledgement, SimulatedCrash))
def test_uncommitted_intent_replays_the_original_uuid_time_and_event_bytes(
    tmp_path, failure
):
    store = _Store()

    def crash(_event):
        raise failure("crashed before server commit")

    store.before_append = crash
    with pytest.raises(failure):
        _publish(tmp_path, store)
    first_attempt = store.attempts[0]
    pending = _intent_path(tmp_path)
    original_bytes = pending.read_bytes()
    assert not store.events

    # Another uncertainty must not replace either the intent or its event.
    with pytest.raises(failure):
        _publish(tmp_path, store, now=NOW + timedelta(minutes=1))
    assert pending.read_bytes() == original_bytes
    assert store.attempts == [first_attempt, first_attempt]

    store.before_append = None
    result = _publish(tmp_path, store, now=NOW + timedelta(hours=1))

    assert result["status"] == "published"
    assert store.events == [first_attempt]
    assert store.attempts == [first_attempt] * 3
    assert first_attempt.controlled_at == NOW
    assert _assert_receipt(tmp_path, first_attempt) == original_bytes


def test_pending_intent_does_not_silently_retarget_a_newer_build(tmp_path):
    store = _Store()

    def crash(_event):
        raise SimulatedCrash()

    store.before_append = crash
    with pytest.raises(SimulatedCrash):
        _publish(tmp_path, store)
    pending = _intent_path(tmp_path)
    original_bytes = pending.read_bytes()
    store.before_append = None
    store.qualified += (QualifiedActivation(_target(6), 2),)

    with pytest.raises((RuntimeError, ValueError)):
        _publish(
            tmp_path,
            store,
            verify_target=lambda: _target(6),
            now=NOW + timedelta(minutes=1),
        )

    assert not store.events
    assert len(store.attempts) == 1
    assert pending.read_bytes() == original_bytes


def test_manual_initial_only_never_advances_an_existing_control_head(tmp_path):
    previous = _event(_target(3))
    store = _Store((_target(3), _target()), (previous,))

    with pytest.raises((RuntimeError, ValueError)):
        _publish(tmp_path, store, initial_only=True, request_id=OTHER)

    assert store.events == [previous]
    assert not store.attempts


def test_manual_initial_request_uses_the_supplied_id(tmp_path):
    store = _Store()

    _publish(tmp_path, store, initial_only=True, request_id=OTHER)
    replay = _publish(
        tmp_path,
        store,
        initial_only=True,
        request_id=OTHER,
        now=NOW + timedelta(hours=1),
    )

    assert len(store.events) == len(store.attempts) == 1
    assert store.events[0].request_id == OTHER
    assert store.events[0].controlled_at == NOW
    assert replay["request_id"] == OTHER
    assert replay["control_sequence"] == 1
    assert replay["idempotent"] is True
    _assert_receipt(tmp_path, store.events[0])


def test_real_file_lock_serializes_manual_and_automatic_proof_and_append(tmp_path):
    store = _Store()
    first_in_proof = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_in_proof = threading.Event()

    def first_proof():
        first_in_proof.set()
        assert release_first.wait(5), "test failed to release the first writer"
        return _target()

    def second_proof():
        second_in_proof.set()
        return _target()

    def second_writer():
        second_started.set()
        return _publish(tmp_path, store, verify_target=second_proof)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            _publish,
            tmp_path,
            store,
            verify_target=first_proof,
            initial_only=True,
            request_id=OTHER,
        )
        try:
            assert first_in_proof.wait(5)
            second = pool.submit(second_writer)
            assert second_started.wait(5)
            assert not second_in_proof.wait(0.15), "second writer crossed the real lock"
            assert not store.attempts
        finally:
            release_first.set()
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert first_result["status"] == "published"
    assert second_result["status"] == "noop"
    assert len(store.events) == len(store.attempts) == 1
    assert store.events[0].request_id == OTHER


def test_lock_identity_is_shared_per_database_workspace_not_request(tmp_path):
    first = _intent_path(tmp_path)
    assert _intent_path(tmp_path) == first
    with subject.selection_lock(
        runtime_directory=str(tmp_path),
        database="property_catalog",
        scope=ActivationControlScope(ORG, OTHER),
    ) as other:
        assert other != first


def test_missing_runtime_directory_does_not_fall_back_or_create_it(tmp_path):
    missing = tmp_path / "not-mounted"
    store = _Store()

    with pytest.raises((RuntimeError, ValueError, OSError)):
        _publish(missing, store)

    assert not missing.exists()
    assert not store.attempts


def test_real_file_lock_excludes_a_separate_process(tmp_path):
    code = """
import sys
from django.conf import settings
settings.configure(ENV_TYPE='test', CLOUD_DEPLOYMENT='US')
from tracer.services.clickhouse.v2.property_catalog.production_selection import selection_lock
from tracer.services.clickhouse.v2.property_catalog.activation_control import ActivationControlScope
print('ready', flush=True)
with selection_lock(runtime_directory=sys.argv[1], database='property_catalog',
                    scope=ActivationControlScope(sys.argv[2], sys.argv[3])):
    print('acquired', flush=True)
"""
    child = None
    try:
        with subject.selection_lock(
            runtime_directory=str(tmp_path), database="property_catalog", scope=SCOPE
        ):
            child = subprocess.Popen(
                [sys.executable, "-B", "-c", code, str(tmp_path), ORG, WORKSPACE],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                ready = pool.submit(child.stdout.readline)
                assert ready.result(timeout=10) == "ready\n"
            with pytest.raises(subprocess.TimeoutExpired):
                child.communicate(timeout=0.15)
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr
        assert stdout.strip() == "acquired"
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_intent_is_file_and_directory_fsynced_before_append(tmp_path, monkeypatch):
    store = _Store()
    pending = _intent_path(tmp_path)
    synced = []
    real_fsync = os.fsync

    def fsync(fd):
        synced.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    def before_append(event):
        assert synced[:2] == ["file", "directory"]
        intent = json.loads(pending.read_text())
        assert intent["version"] == 1
        assert intent["database"] == "property_catalog"
        assert intent["expected_head"] is None
        assert intent["event"]["request_id"] == event.request_id
        assert intent["event"]["controlled_at"] == NOW.isoformat()
        assert intent["event"]["control_sha256"] == event.control_sha256
        assert stat.S_IMODE(pending.stat().st_mode) == 0o600

    monkeypatch.setattr(subject.os, "fsync", fsync)
    store.before_append = before_append

    _publish(tmp_path, store)

    assert len(store.events) == 1


@pytest.mark.parametrize(
    "action", (ActivationControlAction.DISABLE, ActivationControlAction.ROLLBACK)
)
def test_resolved_lost_ack_never_undoes_a_later_operator_hold(tmp_path, action):
    store = _Store((_target(3), _target()))

    def lose_result(_event):
        raise LostAcknowledgement()

    store.after_append = lose_result
    with pytest.raises(LostAcknowledgement):
        _publish(tmp_path, store)
    promoted = store.events[0]
    original_receipt = _assert_receipt(tmp_path, promoted)
    held = _event(
        _target(3) if action is ActivationControlAction.ROLLBACK else _target(),
        action=action,
        previous=promoted,
    )
    store.events.append(held)
    store.after_append = None

    result = _publish(tmp_path, store, now=NOW + timedelta(hours=1))

    assert result["status"] == "operator_held"
    assert store.events == [promoted, held]
    assert store.attempts == [promoted]
    assert _assert_receipt(tmp_path, held) != original_receipt


def test_changed_head_blocks_uncommitted_intent_without_overwriting_it(tmp_path):
    store = _Store()

    def crash(_event):
        raise SimulatedCrash()

    store.before_append = crash
    with pytest.raises(SimulatedCrash):
        _publish(tmp_path, store)
    pending = _intent_path(tmp_path)
    original = pending.read_bytes()
    competing = _event(_target())
    store.events.append(competing)
    store.before_append = None

    with pytest.raises(ActivationControlRejected, match="control_stale"):
        _publish(tmp_path, store)

    assert store.events == [competing]
    assert len(store.attempts) == 1
    assert pending.read_bytes() == original


def test_invalid_durable_intent_is_not_replaced_or_ignored(tmp_path):
    store = _Store()
    pending = _intent_path(tmp_path)
    pending.write_text('{"version":1,"database":"wrong"}')
    pending.chmod(0o600)
    original = pending.read_bytes()

    with pytest.raises(ActivationControlRejected, match="selection_intent_invalid"):
        _publish(tmp_path, store)

    assert not store.attempts
    assert pending.read_bytes() == original


def test_busy_real_lock_times_out_without_starting_an_intent(tmp_path):
    with subject.selection_lock(
        runtime_directory=str(tmp_path), database="property_catalog", scope=SCOPE
    ) as intent:
        with pytest.raises(ActivationControlRejected, match="selection_lock_busy"):
            with subject.selection_lock(
                runtime_directory=str(tmp_path),
                database="property_catalog",
                scope=SCOPE,
                timeout_seconds=0.01,
            ):
                pytest.fail("a competing writer acquired the held lock")
        assert not intent.exists()


def test_symlink_runtime_directory_does_not_alias_the_writer_domain(tmp_path):
    alias = tmp_path / "spool-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)

    with pytest.raises(ValueError, match="resolved absolute"):
        with subject.selection_lock(
            runtime_directory=str(alias), database="property_catalog", scope=SCOPE
        ):
            pytest.fail("symlinked runtime directory was admitted")


@pytest.mark.parametrize("unsafe", ("permissions", "hardlink", "symlink"))
def test_unsafe_lock_file_is_rejected_without_replacement(tmp_path, unsafe):
    lock = _intent_path(tmp_path).with_suffix(".lock")
    lock.write_bytes(b"preserve this existing file")
    if unsafe == "permissions":
        lock.chmod(0o640)
    elif unsafe == "hardlink":
        os.link(lock, tmp_path / "second-link")
    else:
        original = tmp_path / "original-lock"
        lock.rename(original)
        lock.symlink_to(original)
    before = lock.read_bytes()

    with pytest.raises((ValueError, OSError)):
        with subject.selection_lock(
            runtime_directory=str(tmp_path), database="property_catalog", scope=SCOPE
        ):
            pytest.fail("unsafe lock file was admitted")

    assert lock.read_bytes() == before


@pytest.mark.parametrize("prior_revision", (None, 3), ids=("initial", "advance"))
def test_acknowledged_receipt_blocks_stale_replica_fork_then_allows_fresh_advance(
    tmp_path, prior_revision
):
    previous = (_event(_target(prior_revision)),) if prior_revision else ()
    initial_targets = (
        (_target(prior_revision), _target()) if prior_revision else (_target(),)
    )
    writer = _Store(initial_targets, previous)
    _publish(tmp_path, writer)
    acknowledged = writer.events[-1]
    receipt_bytes = _assert_receipt(tmp_path, acknowledged)
    acknowledged_history = tuple(writer.events)

    # A new runtime/connection sees newer qualification but a control replica
    # still at the pre-publish head. The real PVC receipt must fence this view.
    replica = _Store((*initial_targets, _target(6)), previous)
    with pytest.raises(ActivationControlRejected):
        _publish(
            tmp_path,
            replica,
            verify_target=lambda: _target(6),
            now=NOW + timedelta(minutes=1),
        )

    assert not replica.attempts, "stale replica allocated a conflicting successor"
    assert tuple(replica.events) == previous
    assert tuple(writer.events) == acknowledged_history
    assert _assert_receipt(tmp_path, acknowledged) == receipt_bytes

    # Replication catches up: recovery and the next advance must work in one
    # invocation, without losing the previous event or inventing its identity.
    replica.events = list(acknowledged_history)
    result = _publish(
        tmp_path,
        replica,
        verify_target=lambda: _target(6),
        now=NOW + timedelta(minutes=2),
    )

    assert result["status"] == "published"
    assert result["catalog_revision"] == 6
    assert len(replica.attempts) == 1
    advanced = replica.events[-1]
    assert tuple(replica.events[:-1]) == acknowledged_history
    assert advanced.target == _target(6)
    assert advanced.control_sequence == acknowledged.control_sequence + 1
    assert advanced.previous_control_sha256 == acknowledged.control_sha256
    assert advanced.request_id != acknowledged.request_id
    assert acknowledged.controlled_at == NOW
    assert advanced.controlled_at == NOW + timedelta(minutes=2)
    replacement = _assert_receipt(tmp_path, advanced)
    assert replacement != receipt_bytes
    assert (
        json.loads(replacement)["expected_head"]["control_sha256"]
        == acknowledged.control_sha256
    )
    assert canonical_control_events(replica.events, scope=SCOPE) == tuple(
        replica.events
    )


@pytest.mark.parametrize(
    "action", (ActivationControlAction.DISABLE, ActivationControlAction.ROLLBACK)
)
def test_acknowledged_receipt_preserves_later_operator_hold(tmp_path, action):
    store = _Store((_target(3), _target()))
    _publish(tmp_path, store)
    acknowledged = store.events[-1]
    receipt_bytes = _assert_receipt(tmp_path, acknowledged)
    held = _event(
        _target(3) if action is ActivationControlAction.ROLLBACK else _target(),
        action=action,
        previous=acknowledged,
    )
    store.events.append(held)
    store.qualified += (QualifiedActivation(_target(6), 3),)

    result = _publish(
        tmp_path,
        store,
        verify_target=lambda: _target(6),
        now=NOW + timedelta(minutes=1),
    )

    assert result["status"] == "operator_held"
    assert store.events == [acknowledged, held]
    assert store.attempts == [acknowledged]
    assert selected_control_target(store.events) == (
        _target(3) if action is ActivationControlAction.ROLLBACK else None
    )
    assert _assert_receipt(tmp_path, held) != receipt_bytes


def test_acknowledged_same_target_noop_retains_exact_receipt_identity(tmp_path):
    writer = _Store()
    _publish(tmp_path, writer)
    acknowledged = writer.events[-1]
    receipt_bytes = _assert_receipt(tmp_path, acknowledged)
    restarted = _Store(events=(acknowledged,))

    result = _publish(tmp_path, restarted, now=NOW + timedelta(hours=1))

    assert result["status"] == "noop"
    assert result["catalog_revision"] == 5
    assert result["request_id"] == acknowledged.request_id
    assert result["control_sequence"] == acknowledged.control_sequence
    assert result["idempotent"] is True
    assert not restarted.attempts
    assert restarted.events == [acknowledged]
    assert _assert_receipt(tmp_path, acknowledged) == receipt_bytes


@pytest.mark.parametrize("action", tuple(ActivationControlAction))
def test_observed_head_receipt_blocks_stale_view_from_undoing_selection(
    tmp_path, action
):
    initial = _event(_target())
    if action is ActivationControlAction.ACTIVATE:
        observed = initial
        history = (initial,)
        expected_status = "noop"
    else:
        observed = _event(
            _target(3) if action is ActivationControlAction.ROLLBACK else _target(),
            action=action,
            previous=initial,
        )
        history = (initial, observed)
        expected_status = "operator_held"
    observer = _Store((_target(3), _target()), history)

    result = _publish(tmp_path, observer)

    assert result["status"] == expected_status
    assert not observer.attempts
    receipt_bytes = _assert_receipt(tmp_path, observed)
    stale = _Store((_target(3), _target(), _target(6)), history[:-1])
    with pytest.raises(ActivationControlRejected) as rejected:
        _publish(
            tmp_path,
            stale,
            verify_target=lambda: _target(6),
            now=NOW + timedelta(minutes=1),
        )

    if action is not ActivationControlAction.ACTIVATE:
        assert rejected.value.reason == "selection_receipt_not_visible"
    assert not stale.attempts, (
        "a stale read must not replay an operator action or replace its receipt"
    )
    assert tuple(stale.events) == history[:-1]
    assert _assert_receipt(tmp_path, observed) == receipt_bytes

    # The same durable receipt is decoded after another restart. A fresh view
    # preserves the selected target/hold without another control-ledger insert.
    refreshed = _Store((_target(3), _target()), history)
    result = _publish(tmp_path, refreshed, now=NOW + timedelta(minutes=2))
    assert result["status"] == expected_status
    assert not refreshed.attempts
    assert tuple(refreshed.events) == history
    assert _assert_receipt(tmp_path, observed) == receipt_bytes
