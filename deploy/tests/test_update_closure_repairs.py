"""Bounded regressions for lock probes, provenance and publication ownership."""
from __future__ import annotations

import contextlib
import copy
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock
from datetime import timedelta

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests.test_update_public_status import PublisherCase  # noqa: E402
from deploy.tests.test_update_runner import RunnerCase, NOW  # noqa: E402
from deploy.update import __main__ as cli  # noqa: E402
from deploy.update.orchestrator import EngineResult  # noqa: E402
from deploy.update.public_status import PublicationConflict, PublicationError, stamp  # noqa: E402
from deploy.update.receipt import StateBusy, StateDir, StateError  # noqa: E402
from deploy.update.runner_state import StoreBusy  # noqa: E402


def _lock_child(mode, entry):
    """Runs ONLY in a bounded child so the original FIFO bug cannot hang unittest."""
    case = RunnerCase()
    case.setUp()
    try:
        runner = case.runner('manual' if mode == 'manual' else 'notify')
        if mode in ('disabled', 'paused'):
            case.store.change_control('disable' if mode == 'disabled' else 'pause', NOW)
        state = runner.engine_state
        state.ensure()
        if entry == 'fifo':
            os.mkfifo(state.lock_path, 0o600)
        else:
            fifo = case.root / 'foreign-fifo'
            os.mkfifo(fifo, 0o600)
            state.lock_path.symlink_to(fifo)
        result = runner.tick()
        assert result.code != 0 and result.disposition == 'blocked', result
        case.discovery_factory.assert_not_called()
        case.installation_factory.assert_not_called()
        # The refusal released runner.lock too.
        with case.store.runner_lock():
            pass
    finally:
        case.doCleanups()


class LockProbeRepairs(RunnerCase):
    def test_fifo_and_symlink_fifo_are_bounded_in_every_control_mode(self):
        for mode in ('disabled', 'manual', 'paused', 'enabled'):
            for entry in ('fifo', 'symlink'):
                with self.subTest(mode=mode, entry=entry):
                    code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                            'from deploy.tests.test_update_closure_repairs import _lock_child; '
                            '_lock_child(sys.argv[2], sys.argv[3])')
                    try:
                        child = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT), mode, entry],
                                               capture_output=True, text=True, timeout=5)
                    except subprocess.TimeoutExpired:
                        self.fail('lock probe exceeded child deadline: ' + mode + '/' + entry)
                    self.assertEqual(child.returncode, 0, child.stdout + child.stderr)

    def test_unsafe_regular_directory_symlink_and_hardlink_evidence_refuses(self):
        state = StateDir(self.root)
        state.ensure()
        for kind in ('directory', 'symlink', 'hardlink', 'mode'):
            with self.subTest(kind=kind):
                foreign = self.root / ('foreign-' + kind)
                foreign.write_text('private')
                foreign.chmod(0o600)
                if kind == 'directory':
                    state.lock_path.mkdir()
                elif kind == 'symlink':
                    state.lock_path.symlink_to(foreign)
                elif kind == 'hardlink':
                    os.link(foreign, state.lock_path)
                else:
                    state.lock_path.write_text('private')
                    state.lock_path.chmod(0o666)
                try:
                    with self.assertRaises(StateError):
                        state.lock_holder()
                    result = self.runner().tick()
                    self.assertEqual((result.code, result.disposition), (2, 'blocked'))
                    self.discovery_factory.assert_not_called()
                    self.assertEqual(foreign.read_text(), 'private')
                finally:
                    if kind == 'directory':
                        state.lock_path.rmdir()
                    else:
                        state.lock_path.unlink()

    def test_scheduled_probe_refuses_unsafe_state_directory_without_repair(self):
        root = self.root / 'separate'
        root.mkdir()
        state = StateDir(root)
        state.ensure()
        state.path.chmod(0o755)
        with self.assertRaises(StateError):
            state.lock_holder(automation=True)
        self.assertEqual(state.path.stat().st_mode & 0o777, 0o755)
        state.path.chmod(0o700)
        with mock.patch('os.geteuid', return_value=os.geteuid() + 1), self.assertRaises(StateError):
            state.lock_holder(automation=True)
        foreign = root / 'foreign'
        state.path.rename(foreign)
        state.path.symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(StateError):
            state.lock_holder(automation=True)
        self.assertEqual(list(foreign.iterdir()), [])
        state.path.unlink()
        state.path.write_text('not a directory')
        with self.assertRaises(StateError):
            state.lock_holder(automation=True)

    def test_probe_owner_validation_and_all_descriptors_close(self):
        state = StateDir(self.root)
        with state.lock():
            pass
        opened = []
        original_open, original_stat = os.open, os.fstat
        def opened_fd(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            opened.append(fd)
            return fd
        def wrong_owner(fd):
            info = original_stat(fd)
            fields = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
            fields['st_uid'] += 1
            return SimpleNamespace(**fields)
        with mock.patch('os.open', side_effect=opened_fd), mock.patch('os.fstat', side_effect=wrong_owner):
            with self.assertRaises(StateError):
                state.lock_holder()
        self.assertTrue(opened)
        for fd in opened:
            with self.assertRaises(OSError):
                original_stat(fd)
        opened.clear()
        with mock.patch('os.open', side_effect=opened_fd):
            self.assertIsNone(state.lock_holder(automation=True))
        self.assertEqual(len(opened), 2)
        for fd in opened:
            with self.assertRaises(OSError):
                original_stat(fd)

    def test_regular_held_and_missing_locks_across_control_modes(self):
        state = StateDir(self.root)
        self.assertIsNone(state.lock_holder(automation=True))
        self.assertFalse(state.lock_path.exists())
        self.publish()
        for mode in ('disabled', 'manual', 'paused', 'enabled'):
            self.store.change_control('enable', NOW)
            self.store.change_control('resume', NOW)
            if mode in ('disabled', 'paused'):
                self.store.change_control('disable' if mode == 'disabled' else 'pause', NOW)
            before = (self.publisher.path / 'status.json').read_bytes()
            old_state = self.store.read_state()
            with state.lock():
                self.assertIsNotNone(state.lock_holder(automation=True))
                result = self.runner('manual' if mode == 'manual' else 'notify').tick()
                self.assertEqual((result.code, result.disposition), (0, 'busy'))
                self.assertEqual((self.publisher.path / 'status.json').read_bytes(), before)
                self.assertEqual(self.store.read_state(), old_state)
            self.assertIsNone(state.lock_holder(automation=True))
            self.assertEqual(self.runner('manual' if mode == 'manual' else 'notify').tick().code, 0)

    def test_probe_io_errors_are_not_unlocked_and_legacy_safe_mode_is_retained(self):
        import fcntl
        state = StateDir(self.root)
        with state.lock():
            pass
        state.lock_path.chmod(0o644)
        self.assertIsNone(state.lock_holder())
        with self.assertRaises(StateError):
            state.lock_holder(automation=True)
        with mock.patch('fcntl.flock', side_effect=OSError(errno.ENOLCK, 'no locking')):
            with self.assertRaises(StateError):
                state.lock_holder()
        # Keep the import referenced for F checks while asserting POSIX operation availability.
        self.assertTrue(fcntl.LOCK_NB)


class DirectoryProvenanceRepairs(PublisherCase):
    def test_process_death_before_mkdir_cannot_adopt_later_directory(self):
        code = '''import sys, os, json
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])
from deploy.update.runner_state import RunnerStateStore
from deploy.update.public_status import PublicStatusPublisher
root=Path(sys.argv[2]); store=RunnerStateStore(root, root/'.env')
pub=PublicStatusPublisher(store, root/'public'); mkdir=os.mkdir
def die(name, *args, **kwargs):
    if name == 'public': os._exit(93)
    return mkdir(name, *args, **kwargs)
with mock.patch('os.mkdir', side_effect=die):
    pub.publish(json.loads(sys.argv[3]), expected=pub.capture()[0])
'''
        child = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT), str(self.root),
                                json.dumps(self.snapshot())], capture_output=True, text=True, timeout=5)
        self.assertEqual(child.returncode, 93, child.stdout + child.stderr)
        self.assertFalse(self.parent.exists())
        self.parent.mkdir(mode=0o700)
        with self.assertRaises(PublicationError):
            self.publish()
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o700)

    def test_legacy_unbound_intent_is_not_creation_proof(self):
        import hashlib
        self.parent.mkdir(mode=0o700)
        parent = self.parent.parent.stat()
        key = hashlib.sha256(('public-directory:' + str(self.parent)).encode()).hexdigest()
        self.publisher.state.write_private('automation', key,
                                           {'parent': [parent.st_dev, parent.st_ino], 'directory': None})
        with self.assertRaises(PublicationError):
            self.publish()
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o700)

    def test_bound_creation_recovers_but_replacement_inode_does_not(self):
        chmod = os.fchmod
        def interrupt(fd, mode):
            if mode == 0o755:
                raise KeyboardInterrupt()
            return chmod(fd, mode)
        with mock.patch('os.fchmod', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.publish()
        old_inode = self.parent.stat().st_ino
        saved = self.root / 'saved-private-directory'
        self.parent.rename(saved)
        self.parent.mkdir(mode=0o700)
        self.assertNotEqual(self.parent.stat().st_ino, old_inode)
        with self.assertRaises(PublicationError):
            self.publish()
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o700)
        self.parent.rmdir()  # Only this test's known empty replacement.
        saved.rename(self.parent)
        self.publish()
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o755)


class PublicationRepairCase(RunnerCase):
    def setUp(self):
        super().setUp()
        self.patch('deploy.update.__main__._now', side_effect=lambda: self.now)
        self.installation.env = {}
        self.installation.root = self.root
        self.installation.env_file = self.env
        self.installation.production = False
        with self.store._lock('reporting.lock') as directory:
            self.store._write(directory, 'reporting.json', cli.ReportingRegistration(
                1, self.parent, self.binding.installation_fingerprint))

    def export(self):
        with mock.patch.object(cli, 'load_settings', return_value=self.settings('apply')), \
                mock.patch.object(cli, 'Installation', return_value=self.installation), \
                mock.patch.object(cli, 'observe_status', return_value=self.observation), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(['--root', str(self.root), 'status', '--export-public'])

    def execute_progress(self, engine, guard):
        guard.report('before_backup')
        guard.report('before_rollout')
        return EngineResult(0, (), True, True, 'succeeded')


class ExportRepairs(PublicationRepairCase):
    def test_export_during_backup_preserves_owner_operation_and_safety(self):
        runner = self.runner('apply')
        exports = []
        def execute(engine, guard):
            with runner.engine_state.lock():
                runner.engine_state.write_journal({'stage': 'prepare', 'kind': 'apply'})
                guard.report('before_backup')
                owner, before = self.publisher.capture()
                code = self.export()
                after_ticket, after = self.publisher.capture()
                exports.append((code, owner, before, after_ticket, after))
                guard.report('after_backup')
                guard.report('before_rollout')
                runner.engine_state.clear_journal()
            return EngineResult(0, (), True, True, 'succeeded')
        self.executor.side_effect = execute
        result = runner.tick()
        self.assertEqual((result.code, result.disposition), (0, 'applied'))
        self.assertEqual(len(exports), 1)
        code, owner, before, after_ticket, after = exports[0]
        self.assertEqual(code, 0)
        self.assertEqual((after_ticket.epoch, after_ticket.operation_token), (owner.epoch, owner.operation_token))
        for field in ('operation', 'safety'):
            self.assertEqual(after[field], before[field])
        self.assertEqual(result.warnings, ())
        self.assertEqual(self.publisher.capture()[1]['last_run']['outcome'], 'succeeded')

    def test_export_after_terminal_and_between_controls_and_heartbeat_preserves_epoch(self):
        self.executor.side_effect = self.execute_progress
        self.runner('apply').tick()
        owner, before = self.publisher.capture()
        self.store.change_control('pause', NOW)
        self.publisher.publish_controls(policy=self.settings('apply').automation_policy, now=NOW)
        self.assertEqual(self.export(), 0)
        self.publisher.heartbeat(now=NOW)
        after_ticket, after = self.publisher.capture()
        self.assertEqual(after_ticket.epoch, owner.epoch)
        self.assertEqual(after['automation']['state'], 'paused')
        for field in ('operation', 'safety', 'last_run'):
            self.assertEqual(after[field], before[field])

    def test_report_during_export_observation_refuses_without_overwriting_newer_data(self):
        self.publish()
        for actor in ('operation', 'export', 'control', 'heartbeat'):
            with self.subTest(actor=actor):
                publication = cli._Publication(self.store, self.settings(), required=True)
                newest = []
                def observe(*args, **kwargs):
                    if actor == 'operation':
                        self.publish()
                    elif actor == 'export':
                        self.assertEqual(self.export(), 0)
                    elif actor == 'control':
                        self.store.change_control('pause', NOW)
                        self.publisher.publish_controls(policy=self.settings().automation_policy, now=NOW)
                    else:
                        self.publisher.heartbeat(now=NOW)
                    newest.append((self.publisher.path / 'status.json').read_bytes())
                    return self.observation
                with mock.patch.object(cli, 'observe_status', side_effect=observe), self.assertRaises(PublicationConflict):
                    publication.finish(installation=self.installation)
                self.assertEqual((self.publisher.path / 'status.json').read_bytes(), newest[0])

    def test_real_cli_export_conflict_is_nonzero(self):
        self.publish()
        original = cli.PublicStatusPublisher.publish
        def publish(publisher, snapshot, **kwargs):
            publisher.heartbeat(now=NOW)
            return original(publisher, snapshot, **kwargs)
        with mock.patch.object(cli.PublicStatusPublisher, 'publish', new=publish):
            self.assertEqual(self.export(), 2)


class DiscoveryOverlapRepairs(PublicationRepairCase):
    def test_manual_start_during_discovery_preserves_terminal_and_later_idle_ticks(self):
        self.publish()
        before = (self.publisher.path / 'status.json').read_bytes()
        events = self.store.read_events()
        runner = self.runner()
        with contextlib.ExitStack() as held:
            manual = []
            def discover(**kwargs):
                manual.append(cli._Publication(self.store, self.settings()))
                held.enter_context(runner.engine_state.lock())
                runner.engine_state.write_journal({'stage': 'prepare', 'kind': 'apply'})
                from deploy.update.status import observe_status
                self.observer.side_effect = observe_status
                return self.discovery
            self.discoverer.discover.side_effect = discover
            result = runner.tick()
            self.assertEqual((result.code, result.disposition), (0, 'busy'))
            self.assertEqual((self.publisher.path / 'status.json').read_bytes(), before)
            self.assertEqual(self.store.read_events(), events)
            self.assertEqual(self.store.read_state().cached_candidate_identity, self.target())
            runner.engine_state.clear_journal()
        with contextlib.redirect_stderr(io.StringIO()) as error:
            manual[0].finish(kind='check')
        self.assertEqual(error.getvalue(), '')
        terminal = self.publisher.capture()[1]['operation']
        self.assertEqual(terminal['state'], 'succeeded')
        for _ in range(3):
            self.now += timedelta(minutes=5)
            self.monotonic += 300
            self.assertEqual(self.runner().tick().skip_reason, 'not_due')
            final = self.publisher.capture()[1]
            self.assertEqual(final['operation'], terminal)
            self.assertEqual(final['safety']['operator_action'], 'none')

    def test_manual_cannot_acquire_between_journal_and_history_read(self):
        runner = self.runner()
        original = runner.engine_state.read_journal
        calls = []
        def read():
            calls.append(True)
            with self.assertRaises(StateBusy), StateDir(self.root).lock(automation=True):
                pass
            return original()
        def observe(installation, state, **kwargs):
            state.read_journal()
            return self.observation
        self.observer.side_effect = observe
        with mock.patch.object(runner.engine_state, 'read_journal', side_effect=read):
            result = runner.tick()
        self.assertEqual((result.code, result.disposition), (0, 'available'))
        self.assertGreaterEqual(len(calls), 2)

    def test_abandoned_journal_appearing_during_discovery_still_requires_recovery(self):
        runner = self.runner()
        def discover(**kwargs):
            with runner.engine_state.lock():
                runner.engine_state.write_journal({'stage': 'prepare', 'kind': 'apply'})
            from deploy.update.status import observe_status
            self.observer.side_effect = observe_status
            return self.discovery
        self.discoverer.discover.side_effect = discover
        self.assertEqual(runner.tick().disposition, 'recovery_required')
        self.assertEqual(self.publisher.capture()[1]['safety']['operator_action'], 'review_recovery')


class TerminalRetryRepairs(PublicationRepairCase):
    def terminal_contended(self, *, between=None, permanent=False, error=None):
        runner = self.runner('apply')
        original = self.publisher.publish
        attempts = []
        self.executor.side_effect = self.execute_progress
        def publish(snapshot, **kwargs):
            if snapshot['operation']['state'] == 'succeeded':
                attempts.append(kwargs['expected'])
                if error is not None:
                    raise error
                if permanent:
                    if len(attempts) == 1:
                        held.enter_context(self.store._lock('public.lock'))
                    return original(snapshot, **kwargs)
                if len(attempts) == 1:
                    with self.store._lock('public.lock'):
                        return original(snapshot, **kwargs)
            return original(snapshot, **kwargs)
        with contextlib.ExitStack() as held, mock.patch.object(self.publisher, 'publish', side_effect=publish), \
                mock.patch('time.sleep', side_effect=between), mock.patch('time.monotonic', return_value=0):
            result = runner.tick()
        self.executor.assert_called_once()
        self.assertEqual((result.code, result.disposition), (0, 'applied'))
        self.assertTrue(all(ticket is attempts[0] for ticket in attempts))
        return runner, result, attempts

    def test_one_transient_terminal_lock_retries_same_ticket_and_survives_idle_ticks(self):
        _, result, attempts = self.terminal_contended()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(result.warnings, ())
        self.store.change_control('pause', NOW)
        for _ in range(3):
            self.now += timedelta(minutes=5)
            self.monotonic += 300
            self.assertEqual(self.runner('apply').tick().skip_reason, 'paused')
            final = self.publisher.capture()[1]
            self.assertEqual(final['operation']['state'], 'succeeded')
            self.assertEqual(final['last_run']['outcome'], 'succeeded')

    def test_permanent_lock_exhaustion_is_bounded_and_warns_without_engine_retry(self):
        _, result, attempts = self.terminal_contended(permanent=True)
        self.assertEqual(len(attempts), 5)
        self.assertEqual([r.value for r in result.warnings], ['status_reporting_unavailable'])
        self.assertEqual(self.publisher.capture()[1]['operation']['state'], 'applying')

    def test_new_operation_between_attempts_fences_old_terminal_without_recapture(self):
        original = self.publisher.publish
        newer = []
        def between(seconds):
            ticket, snapshot = self.publisher.capture()
            snapshot = copy.deepcopy(snapshot)
            snapshot['operation'].update(state='failed', finished_at=stamp(NOW))
            original(snapshot, expected=ticket, operation_token='newer-operation')
            newer.append((self.publisher.path / 'status.json').read_bytes())
        with mock.patch.object(self.publisher, 'capture', wraps=self.publisher.capture) as capture:
            _, result, attempts = self.terminal_contended(between=between)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(capture.call_count, 2)  # Initial runner capture plus the NEW actor, never a retry capture.
        self.assertEqual((self.publisher.path / 'status.json').read_bytes(), newer[0])
        self.assertTrue(result.warnings)

    def test_control_and_heartbeat_between_attempts_do_not_fence_terminal(self):
        def between(seconds):
            self.store.change_control('pause', NOW)
            self.publisher.publish_controls(policy=self.settings('apply').automation_policy, now=NOW)
            self.publisher.heartbeat(now=NOW)
        _, result, attempts = self.terminal_contended(between=between)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(result.warnings, ())
        final = self.publisher.capture()[1]
        self.assertEqual(final['automation']['state'], 'paused')
        self.assertEqual(final['operation']['state'], 'succeeded')

    def test_interruption_between_attempts_is_nonfatal_and_not_retried(self):
        def interrupt(seconds):
            raise KeyboardInterrupt()
        _, result, attempts = self.terminal_contended(between=interrupt)
        self.assertEqual(len(attempts), 1)
        self.assertTrue(result.warnings)

    def test_disk_error_is_never_retried(self):
        _, result, attempts = self.terminal_contended(error=OSError('disk unavailable'))
        self.assertEqual(len(attempts), 1)
        self.assertTrue(result.warnings)

    def test_terminal_retry_obeys_deadline_as_well_as_attempt_limit(self):
        # Exercise the shared retry helper through the runner, not just its CLI alias.
        runner = self.runner('apply')
        self.executor.side_effect = self.execute_progress
        original = self.publisher.publish
        attempts = []
        def publish(snapshot, **kwargs):
            if snapshot['operation']['state'] == 'succeeded':
                attempts.append(kwargs['expected'])
                raise StoreBusy()
            return original(snapshot, **kwargs)
        # Supervisor initialization also samples monotonic; expire only once terminal starts.
        clock = mock.Mock(side_effect=lambda: 1 if attempts else 0)
        with mock.patch.object(self.publisher, 'publish', side_effect=publish), \
                mock.patch('time.monotonic', clock), mock.patch('time.sleep') as sleep:
            result = runner.tick()
        self.assertEqual(len(attempts), 1)
        sleep.assert_not_called()
        self.assertEqual(result.code, 0)
        self.assertTrue(result.warnings)


if __name__ == '__main__':
    unittest.main()
