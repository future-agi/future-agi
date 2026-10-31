"""Private metadata tests on the native temp filesystem; no Docker or sleeps."""
from __future__ import annotations

import dataclasses
import json
import multiprocessing
import os
import pwd
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from deploy.update.contracts import AutomationPolicy, InstallationBinding, OperationEvent, ReasonCode
from deploy.update.runner_state import (
    MAX_COUNTER, Control, EventRecord, LauncherConfig, Observation, Registration,
    RunnerStateStore, StaleGeneration, StoreBusy, StoreError, TargetIdentity,
)

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
TARGET = TargetIdentity("v1.54.0", "a" * 64)


def registration(store):
    binding = InstallationBinding("1" * 32, store.selection_hash, "2" * 64, store.checkout,
                                  store.env_target, store.env_target, store.topology, store.production,
                                  "fixture", (store.checkout / "compose.yml",), (), "dedicated-context",
                                  "private-daemon", os.geteuid())
    launcher = LauncherConfig(Path(sys.executable), store.checkout, "/usr/bin:/bin", pwd.getpwuid(os.geteuid()).pw_name)
    return Registration(1, binding, launcher, NOW)


def other_caller(root, action, pipe):
    store = RunnerStateStore(Path(root), Path(root) / ".env")
    try:
        if action == "runner":
            with store.runner_lock():
                pipe.send("acquired")
        elif action == "control":
            pipe.send(store.change_control("disable", NOW).generation)
        else:
            old = store.read_state()
            pipe.send(store.save_state(old, expected_generation=old.generation).generation)
    except StoreBusy:
        pipe.send("busy")
    finally:
        pipe.close()


def crash_control(root, after):
    store = RunnerStateStore(Path(root), Path(root) / ".env")
    original = os.replace

    def crash(*args, **kwargs):
        if after:
            original(*args, **kwargs)
        os._exit(73)

    with mock.patch("deploy.update.runner_state.os.replace", side_effect=crash):
        store.change_control("disable", NOW)


class StateFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / ".env").write_text("SECRET=not-for-public\n")
        self.store = RunnerStateStore(self.root, self.root / ".env")
        self.registration = registration(self.store)
        self.store.register(self.registration)

    def child(self, action):
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=other_caller, args=(str(self.root), action, child))
        process.start()
        child.close()
        self.addCleanup(parent.close)
        self.addCleanup(lambda: process.kill() if process.is_alive() else None)
        self.assertTrue(parent.poll(15), "child failed to respond")
        result = parent.recv()
        process.join(15)
        self.assertEqual(process.exitcode, 0)
        return result

    def corrupt(self, name, transform):
        path = self.store.path / name
        value = json.loads(path.read_text())
        path.write_text(json.dumps(transform(value)))


class ControlsTests(StateFixture):
    def test_defaults_and_registration_do_not_enable(self):
        control = self.store.read_control()
        self.assertEqual(control.generation, 0)
        self.assertIsNone(control.enabled_override)
        self.assertEqual(control.effective(AutomationPolicy(), NOW), "disabled")
        self.store.register(self.registration)
        self.assertEqual(self.store.read_control(), control)

    def test_manual_disabled_paused_are_local_only_and_inert(self):
        for mode in ("manual", "notify", "download", "apply"):
            for configured in (False, True):
                for override in (None, False, True):
                    control = Control(enabled_override=override)
                    policy = AutomationPolicy(mode=mode, enabled=configured)
                    expected = "disabled" if not (configured if override is None else override) else (
                        "manual" if mode == "manual" else "enabled")
                    self.assertEqual(control.effective(policy, NOW), expected)
                    if expected != "disabled":
                        self.assertEqual(dataclasses.replace(control, pause_indefinite=True).effective(policy, NOW), "paused")

    def test_disable_survives_environment_policy_changes_and_resume(self):
        disabled = self.store.change_control("disable", NOW)
        self.store.change_control("pause", NOW)
        resumed = self.store.change_control("resume", NOW)
        self.assertGreater(resumed.generation, disabled.generation)
        self.assertFalse(resumed.enabled_override)
        self.assertEqual(resumed.effective(AutomationPolicy(mode="apply", enabled=True), NOW), "disabled")

    def test_pause_offsets_expiry_and_boundary(self):
        until = NOW + timedelta(hours=1)
        control = self.store.change_control("pause", NOW, until=until.astimezone(timezone(timedelta(hours=5, minutes=30))))
        policy = AutomationPolicy(mode="notify", enabled=True)
        self.assertEqual(control.effective(policy, until - timedelta(microseconds=1)), "paused")
        self.assertEqual(control.effective(policy, until), "enabled")
        self.assertEqual(self.store.read_control().pause_until, until)
        for bad in (NOW, NOW - timedelta(seconds=1), NOW.replace(tzinfo=None)):
            with self.assertRaises((StoreError, ValueError)):
                self.store.change_control("pause", NOW, until=bad)

    def test_enable_does_not_resume(self):
        self.store.change_control("pause", NOW)
        control = self.store.change_control("enable", NOW)
        self.assertTrue(control.pause_indefinite)
        self.assertEqual(control.effective(AutomationPolicy(mode="notify"), NOW), "paused")

    def test_generation_cas_and_overflow(self):
        self.store.change_control("disable", NOW, expected_generation=0)
        with self.assertRaises(StaleGeneration):
            self.store.change_control("enable", NOW, expected_generation=0)
        self.corrupt("control.json", lambda v: dict(v, generation=MAX_COUNTER))
        before = (self.store.path / "control.json").read_bytes()
        with self.assertRaises(StoreError):
            self.store.change_control("enable", NOW)
        self.assertEqual((self.store.path / "control.json").read_bytes(), before)

    def test_controls_can_be_set_before_registration(self):
        env = self.root / "other.env"
        env.touch()
        store = RunnerStateStore(self.root, env)
        store.change_control("disable", NOW)
        self.assertFalse(store.read_control().enabled_override)
        with self.assertRaises(StoreError) as raised:
            store.read_registration()
        self.assertEqual(raised.exception.reason_code, ReasonCode.SCHEDULER_UNREGISTERED)

    def test_control_command_during_runner_lock_and_busy_state(self):
        with self.store.runner_lock():
            self.assertEqual(self.child("runner"), "busy")
            self.assertEqual(self.child("control"), 1)
        self.assertEqual(self.child("runner"), "acquired")
        with self.store._lock("control.lock"):
            self.assertEqual(self.child("control"), "busy")
        with self.store._lock("state.lock"):
            self.assertEqual(self.child("state"), "busy")

    def test_checkout_lock_serializes_different_selections(self):
        env = self.root / "second.env"
        env.touch()
        second = RunnerStateStore(self.root, env)
        self.assertNotEqual(second.selection_hash, self.store.selection_hash)
        with self.store.runner_lock():
            with self.assertRaises(StoreBusy):
                with second.runner_lock():
                    self.fail("overlap")
        self.assertFalse((self.root / ".futureagi-updates" / "update.lock").exists())

    def test_real_process_crash_before_and_after_rename(self):
        for after in (False, True):
            self.store.change_control("enable", NOW)
            before = self.store.read_control()
            process = multiprocessing.get_context("spawn").Process(target=crash_control, args=(str(self.root), after))
            process.start()
            process.join(15)
            self.addCleanup(lambda: process.kill() if process.is_alive() else None)
            self.assertEqual(process.exitcode, 73)
            actual = self.store.read_control()
            self.assertEqual(actual.generation, before.generation + int(after))
            self.assertEqual(actual.enabled_override, not after)
            # Process death releases locks, even when its private temp remains.
            self.store.change_control("resume", NOW)


class ValidationTests(StateFixture):
    def test_exact_fields_types_schema_duplicates_and_bounded_read(self):
        path = self.store.path / "control.json"
        original = path.read_bytes()
        cases = [b"{}", b"null", b"[]", original.replace(b'"schema_version":1', b'"schema_version":2'),
                 original.replace(b'"generation":0', b'"generation":true'),
                 original.replace(b'"generation":0', b'"generation":0.0'),
                 original.replace(b'"generation":0', b'"generation":-1'),
                 original.replace(b'"generation":0', b'"generation":0,"generation":1'),
                 original.replace(b'"enabled_override":null', b'"enabled_override":"false"'),
                 original[:-2] + b',"secret":"canary"}', b"{" + b" " * 65536 + b"}",
                 b"[" * 1000, b"\xff", original.replace(b'"pause_until":null', b'"pause_until":NaN')]
        for raw in cases:
            with self.subTest(raw=raw[:100]):
                path.write_bytes(raw)
                with self.assertRaises(StoreError):
                    self.store.read_control()
        path.write_bytes(original)

    def test_bad_time_or_conflicting_pause(self):
        for field, value in (("pause_until", "2026-10-24T13:00:00"), ("changed_at", "2026-13-99T12:00:00Z"),
                             ("schema_version", True)):
            with self.subTest(field=field):
                original = (self.store.path / "control.json").read_bytes()
                self.corrupt("control.json", lambda v: dict(v, **{field: value}))
                with self.assertRaises(StoreError):
                    self.store.read_control()
                (self.store.path / "control.json").write_bytes(original)
        self.corrupt("control.json", lambda v: dict(v, pause_until="2026-10-24T13:00:00Z", pause_indefinite=True))
        with self.assertRaises(StoreError):
            self.store.read_control()

    def test_missing_control_after_init_blocks(self):
        (self.store.path / "control.json").unlink()
        with self.assertRaises(StoreError):
            self.store.read_control()
        with self.assertRaises(StoreError):
            self.store.change_control("enable", NOW)

    def test_private_modes_and_no_permission_repair(self):
        for directory in (self.store.path, self.store.path.parent, self.store.path.parent.parent):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for file in self.store.path.iterdir():
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        for mode in (0o644, 0o660, 0o400, 0o700):
            file = self.store.path / "control.json"
            file.chmod(mode)
            with self.assertRaises(StoreError):
                self.store.read_control()
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), mode)
        file.chmod(0o600)
        self.store.path.chmod(0o755)
        with self.assertRaises(StoreError):
            self.store.change_control("disable", NOW)
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o755)
        self.store.path.chmod(0o700)

    def test_no_follow_regular_file_and_single_link(self):
        path = self.store.path / "control.json"
        raw = path.read_bytes()
        path.unlink()
        foreign = self.root / "foreign"
        foreign.write_bytes(raw)
        foreign.chmod(0o600)
        for kind in ("symlink", "hardlink", "fifo", "directory"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    path.symlink_to(foreign)
                elif kind == "hardlink":
                    os.link(foreign, path)
                elif kind == "fifo":
                    os.mkfifo(path, 0o600)
                else:
                    path.mkdir()
                with self.assertRaises(StoreError):
                    self.store.read_control()
                path.rmdir() if kind == "directory" else path.unlink()
                self.assertEqual(foreign.read_bytes(), raw)

    def test_symlink_directory_and_lock_refused(self):
        real = self.store.path.with_name("renamed")
        self.store.path.rename(real)
        self.store.path.symlink_to(real)
        with self.assertRaises(StoreError):
            self.store.read_state()
        self.store.path.unlink()
        real.rename(self.store.path)
        lock = self.store.path / "control.lock"
        lock.unlink()
        lock.symlink_to(self.root / ".env")
        with self.assertRaises(StoreError):
            self.store.change_control("disable", NOW)
        self.assertEqual((self.root / ".env").read_text(), "SECRET=not-for-public\n")

    def test_foreign_owner_is_rejected_without_chown(self):
        # No chown authority needed: fstat ownership check is independently unit
        # tested; native permissions/link/locking/crash tests above are real.
        with mock.patch("deploy.update.runner_state.os.geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(StoreError):
                self.store.read_control()

    def test_registration_drift_not_migrated(self):
        before = (self.store.path / "registration.json").read_bytes()
        for key, value in (("daemon_identity", "changed"), ("host_uid", os.geteuid() + 1),
                           ("profiles", ("gpu",)), ("compose_project", "different")):
            changed = dataclasses.replace(self.registration, binding=dataclasses.replace(self.registration.binding, **{key: value}))
            with self.assertRaises(StoreError):
                self.store.register(changed)
            self.assertEqual((self.store.path / "registration.json").read_bytes(), before)

    def test_environment_alias_retarget_and_fresh_daemon_drift_block(self):
        alias = self.root / "alias.env"
        alias.symlink_to(self.root / ".env")
        self.assertEqual(RunnerStateStore(self.root, alias).selection_hash, self.store.selection_hash)
        with self.assertRaises(StoreError):
            self.store.validate_binding(dataclasses.replace(self.registration.binding, daemon_identity="new-daemon"))
        # Use a new selection so its explicit registration can bind an alias.
        target = self.root / "target.env"
        target.touch()
        alias.unlink()
        alias.symlink_to(target)
        other = RunnerStateStore(self.root, alias)
        reg = registration(other)
        reg = dataclasses.replace(reg, binding=dataclasses.replace(reg.binding, env_alias=alias))
        other.register(reg)
        alias.unlink()
        alias.symlink_to(self.root / ".env")
        with self.assertRaises(StoreError):
            other.read_registration()

    def test_boolean_cas_generation_rejected_even_when_equal_to_integer(self):
        self.store.change_control("enable", NOW)
        with self.assertRaises(StoreError):
            self.store.change_control("disable", NOW, expected_generation=True)
        state = self.store.save_state(self.store.read_state(), expected_generation=0)
        with self.assertRaises(StoreError):
            self.store.save_state(state, expected_generation=True)

    def test_state_closed_nested_objects_and_safe_counter(self):
        path = self.store.path / "state.json"
        raw = path.read_bytes()
        for patch in ({"last_good_observations": {"secret": "value"}}, {"retry_count": True},
                      {"active_operation_token": "token"}, {"next_event_sequence": MAX_COUNTER + 1},
                      {"cached_candidate_identity": {"version": "v1.54.0", "manifest_hash": "a" * 64, "extra": 1}},
                      {"installation_fingerprint": "3" * 64}):
            self.corrupt("state.json", lambda v: dict(v, **patch))
            with self.assertRaises(StoreError):
                self.store.read_state()
            path.write_bytes(raw)

    def test_state_cas_observation_roundtrip_and_stale_writer(self):
        old = self.store.read_state()
        new = self.store.save_state(dataclasses.replace(old, last_tick_at=NOW,
                                   last_good_observations=(Observation("versions", NOW, "f" * 64),)), expected_generation=0)
        self.assertEqual(self.store.read_state(), new)
        with self.assertRaises(StaleGeneration):
            self.store.save_state(old, expected_generation=0)
        with self.assertRaises(StoreError):
            self.store.save_state(dataclasses.replace(new, generation=MAX_COUNTER), expected_generation=new.generation)


class DiscoveryStateTests(StateFixture):
    def test_crashed_checks_keep_exponential_deadline_across_restarts(self):
        policy = AutomationPolicy()
        first = self.store.record_check_attempt(policy, NOW)
        self.assertEqual(first.retry_count, 1)
        self.assertGreaterEqual(first.retry_not_before, NOW + timedelta(seconds=300))
        fresh = RunnerStateStore(self.root, self.root / ".env")
        self.assertEqual(fresh.read_state(), first)
        next_time = first.retry_not_before
        second = fresh.record_check_attempt(policy, next_time)
        self.assertEqual(second.retry_count, 2)
        self.assertGreaterEqual(second.retry_not_before, next_time + timedelta(seconds=600))
        self.assertIsNone(second.last_successful_check_at)

    def test_failure_preserves_freshness_and_success_resets_backoff_not_latch(self):
        policy = AutomationPolicy()
        self.store.record_check_attempt(policy, NOW)
        success = self.store.record_check_success(policy, NOW, TARGET)
        state = self.store.save_state(dataclasses.replace(success, failed_target_latch=TARGET),
                                      expected_generation=success.generation)
        attempt_time = NOW + timedelta(hours=7)
        self.store.record_check_attempt(policy, attempt_time)
        failed = self.store.record_check_failure(policy, attempt_time, attempt_time + timedelta(days=2))
        self.assertEqual(failed.last_successful_check_at, NOW)
        self.assertEqual(failed.cached_candidate_identity, TARGET)
        self.assertEqual(failed.next_check_at, state.next_check_at)
        self.assertEqual(failed.retry_not_before, attempt_time + timedelta(days=1))
        self.assertEqual(failed.retry_count, 1)
        self.store.record_check_attempt(policy, failed.retry_not_before)
        success = self.store.record_check_success(policy, failed.retry_not_before, TARGET)
        self.assertEqual(success.retry_count, 0)
        self.assertIsNone(success.retry_not_before)
        self.assertEqual(success.failed_target_latch, TARGET)
        self.assertGreater(success.next_check_at, failed.retry_not_before + timedelta(hours=6) - timedelta(seconds=1))

    def test_no_success_or_failure_without_reserved_check(self):
        with self.assertRaises(StoreError):
            self.store.record_check_success(AutomationPolicy(), NOW, TARGET)
        with self.assertRaises(StoreError):
            self.store.record_check_failure(AutomationPolicy(), NOW)


class AttemptTests(StateFixture):
    def test_reservation_is_durable_before_backup_and_blocks_restart(self):
        attempt = self.store.reserve_attempt(TARGET, "window-one", NOW)
        self.assertEqual(self.store.read_attempt(attempt.attempt_id), attempt)
        fresh = RunnerStateStore(self.root, self.root / ".env")
        with self.assertRaises(StoreError) as error:
            fresh.reserve_attempt(TARGET, "window-two", NOW)
        self.assertEqual(error.exception.reason_code, ReasonCode.RECOVERY_REQUIRED)

    def test_crash_between_intent_and_attempt_record_fails_closed(self):
        original = self.store._write

        def fail(directory, name, value):
            if name != "state.json":
                raise OSError("fault")
            original(directory, name, value)

        with mock.patch.object(self.store, "_write", side_effect=fail), self.assertRaises(StoreError):
            self.store.reserve_attempt(TARGET, "window-one", NOW)
        self.assertIsNotNone(self.store.read_state().active_attempt_id)
        with self.assertRaises(StoreError):
            self.store.reserve_attempt(TARGET, "window-two", NOW)

    def test_failure_latch_retry_preserves_archive_and_consumed_window(self):
        attempt = self.store.reserve_attempt(TARGET, "window-one", NOW)
        terminal = dataclasses.replace(attempt, outcome="rolled_back", finished_at=NOW + timedelta(seconds=1),
                                       backup_proof_identity="c" * 64, receipt_identity="d" * 64)
        state = self.store.finish_attempt(terminal)
        self.assertEqual(state.failed_target_latch, TARGET)
        for window in ("window-one", "window-two"):
            with self.assertRaises(StoreError) as error:
                self.store.reserve_attempt(TARGET, window, NOW)
            self.assertEqual(error.exception.reason_code, ReasonCode.FAILED_TARGET_LATCHED)
        with self.assertRaises(StoreError):
            self.store.retry_target(TargetIdentity(TARGET.version, "b" * 64))
        self.store.retry_target(TARGET)
        with self.assertRaises(StoreError):
            self.store.reserve_attempt(TARGET, "window-one", NOW)
        self.assertEqual(self.store.read_attempt(attempt.attempt_id), terminal)
        self.store.reserve_attempt(TARGET, "window-two", NOW)

    def test_always_one_target_attempt_until_retry_or_new_target(self):
        attempt = self.store.reserve_attempt(TARGET, "always", NOW)
        self.store.finish_attempt(dataclasses.replace(attempt, outcome="succeeded", finished_at=NOW))
        with self.assertRaises(StoreError):
            self.store.reserve_attempt(TARGET, "always", NOW)
        other = TargetIdentity("v1.55.0", "b" * 64)
        failed = self.store.reserve_attempt(other, "always", NOW)
        self.store.finish_attempt(dataclasses.replace(failed, outcome="failed", finished_at=NOW))
        self.store.retry_target(other)
        self.store.reserve_attempt(other, "always", NOW)

    def test_terminal_evidence_precedes_active_clear(self):
        attempt = self.store.reserve_attempt(TARGET, "one", NOW)
        terminal = dataclasses.replace(attempt, outcome="failed", finished_at=NOW)
        original = self.store._write

        def fail(directory, name, value):
            if name == "state.json":
                raise OSError("fault")
            original(directory, name, value)

        with mock.patch.object(self.store, "_write", side_effect=fail), self.assertRaises(StoreError):
            self.store.finish_attempt(terminal)
        self.assertEqual(self.store.read_attempt(attempt.attempt_id), terminal)
        self.assertEqual(self.store.read_state().active_attempt_id, attempt.attempt_id)
        with self.assertRaises(StoreError):
            self.store.retry_target(TARGET)

    def test_exact_terminal_replay_after_record_before_state_is_idempotent(self):
        attempt = self.store.reserve_attempt(TARGET, 'one', NOW)
        terminal = dataclasses.replace(attempt, outcome='failed', finished_at=NOW,
            backup_proof_identity='c' * 64, receipt_identity='d' * 64)
        original = self.store._write
        def crash(directory, name, value):
            if name == 'state.json':
                raise OSError('crash after terminal publication')
            original(directory, name, value)
        with mock.patch.object(self.store, '_write', side_effect=crash), self.assertRaises(StoreError):
            self.store.finish_attempt(terminal)
        fresh = RunnerStateStore(self.root, self.root / '.env')
        state = fresh.finish_attempt(terminal)
        self.assertIsNone(state.active_attempt_id)
        self.assertEqual(state.failed_target_latch, TARGET)
        self.assertEqual(fresh.finish_attempt(terminal), state)
        for changed in (dataclasses.replace(terminal, outcome='interrupted'),
                        dataclasses.replace(terminal, receipt_identity='e' * 64),
                        dataclasses.replace(terminal, backup_proof_identity='e' * 64),
                        dataclasses.replace(terminal, finished_at=NOW + timedelta(seconds=1)),
                        dataclasses.replace(terminal, operation_token='another-token')):
            with self.subTest(changed=changed), self.assertRaises(StoreError):
                fresh.finish_attempt(changed)
        fresh.retry_target(TARGET)
        after_retry = fresh.read_state()
        self.assertEqual(fresh.finish_attempt(terminal), after_retry)
        self.assertIsNone(fresh.read_state().failed_target_latch)
        newer = fresh.reserve_attempt(TargetIdentity('v1.55.0', 'e' * 64), 'two', NOW)
        with self.assertRaises(StoreError):
            fresh.finish_attempt(terminal)
        self.assertEqual(fresh.read_state().active_attempt_id, newer.attempt_id)

    def test_foreign_or_late_terminal_cannot_clear_active_operation(self):
        attempt = self.store.reserve_attempt(TARGET, "one", NOW)
        for patch in ({"operation_token": "wrong"}, {"target": TargetIdentity("v1.55.0", "b" * 64)}):
            terminal = dataclasses.replace(attempt, outcome="failed", finished_at=NOW, **patch)
            with self.assertRaises(StoreError):
                self.store.finish_attempt(terminal)
        self.assertEqual(self.store.read_state().active_attempt_id, attempt.attempt_id)


class EventsTests(StateFixture):
    def event(self, token="check"):
        return OperationEvent("succeeded", "check", None, NOW, token, ())

    def test_event_sequence_gap_after_crash_is_never_reused(self):
        original = self.store._write

        def fail(directory, name, value):
            if name.endswith("00000.json"):
                raise OSError("fault")
            original(directory, name, value)

        with mock.patch.object(self.store, "_write", side_effect=fail), self.assertRaises(StoreError):
            self.store.append_event(self.event())
        self.assertEqual(self.store.append_event(self.event()).sequence, 1)
        self.assertFalse((self.store.path / "events" / "00000000000000000000.json").exists())

    def test_retention_keeps_active_failed_and_foreign_evidence(self):
        with mock.patch("deploy.update.runner_state.MAX_EVENTS", 3):
            self.store.append_event(self.event("failed"), TARGET)
            old = self.store.read_state()
            self.store.save_state(dataclasses.replace(old, failed_target_latch=TARGET), expected_generation=old.generation)
            self.store.append_event(self.event("old"))
            self.store.append_event(self.event("new"))
            foreign = self.store.path / "events" / "foreign.json"
            foreign.write_text("do not prune")
            self.store.append_event(self.event("newest"))
            names = {p.name for p in foreign.parent.iterdir()}
            self.assertIn("00000000000000000000.json", names)
            self.assertNotIn("00000000000000000001.json", names)
            self.assertEqual(foreign.read_text(), "do not prune")
            self.assertEqual(len(names), 4)

    def test_real_thousand_record_bound_and_active_token_pin(self):
        attempt = self.store.reserve_attempt(TARGET, "window-one", NOW)
        self.store.append_event(self.event(attempt.operation_token))
        # Seed historical records using the same durable writer; real retention
        # limit, modes and schemas, without a thousand separate lock acquisitions.
        with self.store._lock("state.lock") as directory:
            state = self.store.read_state()
            with self.store._directory(create=True, child="events") as events:
                for sequence in range(1, 1000):
                    self.store._write(events, f"{sequence:020d}.json", EventRecord(1, sequence, self.event(), None))
            self.store._write(directory, "state.json", dataclasses.replace(state, next_event_sequence=1000,
                                                                          generation=state.generation + 1))
        self.store.append_event(self.event("new"))
        records = self.store.read_events()
        self.assertEqual(len(records), 1000)
        self.assertEqual(records[0].event.operation_token, attempt.operation_token)
        self.assertNotIn(1, [record.sequence for record in records])
        self.assertEqual(records[-1].sequence, 1000)
        self.assertEqual(self.store.read_attempt(attempt.attempt_id), attempt)

    def test_counter_exhaustion_does_not_prune_events(self):
        self.store.append_event(self.event())
        state = self.store.read_state()
        self.store.save_state(dataclasses.replace(state, next_event_sequence=MAX_COUNTER), expected_generation=state.generation)
        with mock.patch("deploy.update.runner_state.MAX_EVENTS", 1):
            with self.assertRaises(StoreError):
                self.store.append_event(self.event())
        self.assertEqual(len(self.store.read_events()), 1)

    def test_all_pinned_refuses_instead_of_destroying_evidence(self):
        with mock.patch("deploy.update.runner_state.MAX_EVENTS", 2):
            old = self.store.read_state()
            self.store.save_state(dataclasses.replace(old, failed_target_latch=TARGET), expected_generation=old.generation)
            for _ in range(2):
                self.store.append_event(self.event(), TARGET)
            before = self.store.read_state()
            with self.assertRaises(StoreError) as error:
                self.store.append_event(self.event(), TARGET)
            self.assertEqual(error.exception.reason_code, ReasonCode.DISK_SPACE_INSUFFICIENT)
            self.assertEqual(self.store.read_state(), before)

    def test_event_contains_closed_codes_not_raw_error_or_secret(self):
        event = self.store.append_event(self.event(), TARGET)
        self.assertIsInstance(event, EventRecord)
        raw = next((self.store.path / "events").glob("*.json")).read_bytes()
        self.assertNotIn(b"SECRET", raw)
        self.assertNotIn(b"not-for-public", raw)
        self.assertNotIn(b"private-daemon", raw)
        with self.assertRaises(StoreError):
            self.store.append_event(self.event("secret\ncommand"))


if __name__ == "__main__":
    unittest.main()
