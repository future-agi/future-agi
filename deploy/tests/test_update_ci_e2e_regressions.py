"""CI E2E regressions: real filesystem/CLI guards, daemon-free Docker setup checks."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests import test_update_apply as applies  # noqa: E402
from deploy.tests import test_update_install_cli as installs  # noqa: E402
from deploy.tests import test_update_runner as runners  # noqa: E402
from deploy.tests.update_e2e import harness as e2e  # noqa: E402
from deploy.update.receipt import StateDir, StateError  # noqa: E402
from deploy.update.apply import Updater  # noqa: E402
from deploy.update.orchestrator import GuardRefused, ScheduledGuard  # noqa: E402


# This file is selected by update-ci's test_update_*.py discovery on Python
# 3.9 and 3.12. These regressions need neither the E2E opt-in nor a Docker daemon.
class HarnessPortFactsTests(unittest.TestCase):
    def assert_allocation_refused(self):
        taken = {23456}
        with mock.patch.object(e2e.socket, 'socket') as probe, \
                mock.patch.object(e2e.socket, 'create_connection') as connect:
            with self.assertRaisesRegex(RuntimeError, 'cannot determine local ephemeral TCP port ranges'):
                e2e.free_port(taken)
        probe.assert_not_called()
        connect.assert_not_called()
        self.assertEqual(taken, {23456})

    def test_linux_reads_actual_range_including_custom_endpoints(self):
        for raw, expected in [('32768\t60999\n', [(32768, 60999)]),
                              ('15000 25000\n', [(15000, 25000)]), ('20000 20000', [(20000, 20000)])]:
            with self.subTest(raw=raw), mock.patch.object(e2e.sys, 'platform', 'linux'), \
                    mock.patch.object(e2e.Path, 'read_text', autospec=True, return_value=raw) as read:
                self.assertEqual(e2e._ephemeral_port_ranges(), expected)
                read.assert_called_once_with(Path('/proc/sys/net/ipv4/ip_local_port_range'), encoding='ascii')

    def test_darwin_reads_both_ranges_and_normalizes_descending_endpoints(self):
        for raw, expected in [('49152\n65535\n49152\n65535\n', [(49152, 65535), (49152, 65535)]),
                              ('25000\n21000\n39000\n35000\n', [(21000, 25000), (35000, 39000)]),
                              ('20000\n20000\n30000\n30000\n', [(20000, 20000), (30000, 30000)])]:
            with self.subTest(raw=raw), mock.patch.object(e2e.sys, 'platform', 'darwin'), \
                    mock.patch.object(e2e, 'run', return_value=e2e.Result([], 0, raw, '')) as run:
                self.assertEqual(e2e._ephemeral_port_ranges(), expected)
                run.assert_called_once_with(['/usr/sbin/sysctl', '-n', 'net.inet.ip.portrange.first',
                    'net.inet.ip.portrange.last', 'net.inet.ip.portrange.hifirst',
                    'net.inet.ip.portrange.hilast'], check=False, timeout=5)

    def test_linux_missing_or_invalid_facts_fail_before_any_socket_probe(self):
        bad = ['', '32768', '32768 60999 65535', 'first=32768 60999', '-1 60999',
               '0 60999', '32768 65536', '60999 32768', '+20000 30000', '２０000 30000']
        for raw in bad:
            with self.subTest(raw=raw), mock.patch.object(e2e.sys, 'platform', 'linux'), \
                    mock.patch.object(e2e.Path, 'read_text', return_value=raw):
                self.assert_allocation_refused()
        for error in (FileNotFoundError('missing procfs'), PermissionError('unreadable procfs'),
                      UnicodeDecodeError('ascii', b'\xff', 0, 1, 'invalid byte')):
            with self.subTest(error=error), mock.patch.object(e2e.sys, 'platform', 'linux'), \
                    mock.patch.object(e2e.Path, 'read_text', side_effect=error):
                self.assert_allocation_refused()

    def test_darwin_missing_or_invalid_facts_do_not_fall_back_to_defaults(self):
        for raw in ('', '49152\n65535\n', '49152 65535 49152 65535 9',
                    'first: 49152 65535 49152 65535', '49152 65535 0 65535',
                    '49152 65535 49152 65536', '49152 65535 nope 65535'):
            with self.subTest(raw=raw), mock.patch.object(e2e.sys, 'platform', 'darwin'), \
                    mock.patch.object(e2e, 'run', return_value=e2e.Result([], 0, raw, '')):
                self.assert_allocation_refused()
        # run() reports a timeout as -999; even valid-looking stdout from a
        # failed/partial query must not authorize allocating any port.
        for code in (1, -999):
            with self.subTest(code=code), mock.patch.object(e2e.sys, 'platform', 'darwin'), \
                    mock.patch.object(e2e, 'run', return_value=e2e.Result([], code,
                        '49152 65535 49152 65535', 'query failed')):
                self.assert_allocation_refused()
        with mock.patch.object(e2e.sys, 'platform', 'darwin'), \
                mock.patch.object(e2e, 'run', side_effect=FileNotFoundError('missing sysctl')):
            self.assert_allocation_refused()

    def test_unsupported_platform_fails_explicitly_without_guessing(self):
        with mock.patch.object(e2e.sys, 'platform', 'unsupported'), \
                mock.patch.object(e2e.Path, 'read_text') as read, mock.patch.object(e2e, 'run') as run:
            self.assert_allocation_refused()
        read.assert_not_called()
        run.assert_not_called()


class HarnessPortPolicyTests(unittest.TestCase):
    def patch(self, obj, name, **kwargs):
        patcher = mock.patch.object(obj, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def setUp(self):
        self.facts = self.patch(e2e, '_ephemeral_port_ranges', return_value=[(32768, 60999)])
        self.socket = self.patch(e2e.socket, 'socket')
        self.probe = self.socket.return_value.__enter__.return_value
        self.connect = self.patch(e2e.socket, 'create_connection', side_effect=ConnectionRefusedError)
        self.sample = self.patch(e2e.random, 'sample', side_effect=lambda ports, count: ports[:count])

    def test_default_linux_policy_excludes_the_failed_ci_port(self):
        taken = set()
        self.assertEqual(e2e.free_port(taken), 20000)
        candidates, count = self.sample.call_args.args
        self.assertEqual(candidates, list(range(20000, 32768)))
        self.assertNotIn(39192, candidates)
        self.assertEqual(count, 500)
        self.assertEqual(taken, {20000})

    def test_custom_ranges_exclude_both_endpoints_and_keep_all_safe_fragments(self):
        cases = [([(20000, 20000)], [20001, 20002, 20003, 20004, 20005]),
                 ([(20005, 20005)], [20000, 20001, 20002, 20003, 20004]),
                 ([(20002, 20003)], [20000, 20001, 20004, 20005]),
                 ([(19999, 20001), (20004, 50000)], [20002, 20003]),
                 ([(1, 19999), (40000, 65535)], list(range(20000, 20006)))]
        with mock.patch.object(e2e, '_PORT_RANGE', range(20000, 20006)):
            for ranges, expected in cases:
                with self.subTest(ranges=ranges):
                    self.facts.return_value = ranges
                    self.assertEqual(e2e.free_port(set()), expected[0])
                    self.assertEqual(self.sample.call_args, mock.call(expected, len(expected)))

    def test_excluded_or_taken_pool_is_exhausted_without_probing(self):
        for ranges, taken in [([(20000, 20002)], set()), ([(32768, 60999)], {20000, 20001, 20002}),
                              ([(20001, 65535)], {20000})]:
            with self.subTest(ranges=ranges, taken=taken), \
                    mock.patch.object(e2e, '_PORT_RANGE', range(20000, 20003)):
                self.facts.return_value = ranges
                before = set(taken)
                with self.assertRaisesRegex(RuntimeError, 'no eligible TCP port'):
                    e2e.free_port(taken)
                self.assertEqual(taken, before)
        self.sample.assert_not_called()
        self.socket.assert_not_called()
        self.connect.assert_not_called()

    def test_taken_ports_do_not_spend_the_probe_budget(self):
        taken = set(range(20000, 20600))
        with mock.patch.object(e2e, '_PORT_RANGE', range(20000, 20601)):
            self.assertEqual(e2e.free_port(taken), 20600)
        self.sample.assert_called_once_with([20600], 1)
        self.assertEqual(taken, set(range(20000, 20601)))

    def test_busy_small_pool_is_searched_once_and_does_not_claim_any_port(self):
        self.probe.bind.side_effect = OSError('busy')
        taken = {20003}
        with mock.patch.object(e2e, '_PORT_RANGE', range(20000, 20004)), \
                self.assertRaisesRegex(RuntimeError, 'after 3 distinct probes'):
            e2e.free_port(taken)
        self.assertEqual(self.probe.bind.call_args_list,
                         [mock.call(('0.0.0.0', port)) for port in range(20000, 20003)])
        self.assertEqual(self.socket.return_value.__exit__.call_count, 3)
        self.connect.assert_not_called()
        self.assertEqual(taken, {20003})

    def test_probe_budget_still_bounds_large_busy_pools_without_replacement(self):
        self.sample.side_effect = e2e.random.Random(0).sample
        self.probe.bind.side_effect = OSError('busy')
        taken = {20000}
        with mock.patch.object(e2e, '_PORT_RANGE', range(20000, 20601)), \
                self.assertRaisesRegex(RuntimeError, 'after 500 distinct probes'):
            e2e.free_port(taken)
        attempted = [call.args[0][1] for call in self.probe.bind.call_args_list]
        self.assertEqual(len(attempted), 500)
        self.assertEqual(len(set(attempted)), 500)
        self.assertNotIn(20000, attempted)
        self.assertEqual(taken, {20000})
        self.connect.assert_not_called()

    def test_parallel_callers_share_locked_taken_set_until_exhaustion(self):
        def refused(*args, **kwargs):
            self.assertTrue(e2e._PORT_LOCK.locked())
            raise ConnectionRefusedError
        self.connect.side_effect = refused
        taken = set()
        with mock.patch.object(e2e, '_PORT_RANGE', range(20000, 20032)):
            with ThreadPoolExecutor(max_workers=4) as pool:
                ports = list(pool.map(lambda _: e2e.free_port(taken), range(32)))
            with self.assertRaisesRegex(RuntimeError, 'no eligible TCP port'):
                e2e.free_port(taken)
        self.assertEqual(len(set(ports)), 32)
        self.assertEqual(taken, set(range(20000, 20032)))

    def test_wildcard_loopback_and_answering_forwarder_guards_remain_independent(self):
        for obstruction in ('0.0.0.0', '127.0.0.1', 'forwarder'):
            with self.subTest(obstruction=obstruction), \
                    mock.patch.object(e2e, '_PORT_RANGE', range(20000, 20002)):
                self.socket.reset_mock()
                self.connect.reset_mock()
                forwarder = mock.Mock()
                def bind(address):
                    if address == (obstruction, 20000):
                        raise OSError('busy')
                def connect(address, timeout):
                    self.assertEqual(timeout, 0.2)
                    if obstruction == 'forwarder' and address[1] == 20000:
                        return forwarder
                    raise ConnectionRefusedError
                self.probe.bind.side_effect = bind
                self.connect.side_effect = connect
                taken = set()
                self.assertEqual(e2e.free_port(taken), 20001)
                self.assertEqual(taken, {20001})
                expected = [mock.call(('0.0.0.0', 20000))]
                if obstruction != '0.0.0.0':
                    expected.append(mock.call(('127.0.0.1', 20000)))
                expected += [mock.call(('0.0.0.0', 20001)), mock.call(('127.0.0.1', 20001))]
                self.assertEqual(self.probe.bind.call_args_list, expected)
                self.assertEqual(self.socket.return_value.__exit__.call_count, len(expected))
                self.probe.setsockopt.assert_not_called()
                connections = [mock.call(('127.0.0.1', 20001), timeout=0.2)]
                if obstruction == 'forwarder':
                    connections.insert(0, mock.call(('127.0.0.1', 20000), timeout=0.2))
                    forwarder.close.assert_called_once_with()
                self.assertEqual(self.connect.call_args_list, connections)


class HarnessPortBindingTests(unittest.TestCase):
    def test_real_bound_ports_are_rejected_and_probes_release_for_static_rebinding(self):
        # Real sockets and real platform facts, no daemon/listening service.
        # Use the allocator to seed a non-ephemeral port rather than hard-code
        # a port or use bind(0), which would choose inside the ephemeral range.
        for host in ('0.0.0.0', '127.0.0.1'):
            with self.subTest(host=host):
                port = e2e.free_port(set())
                taken = set()
                with mock.patch.object(e2e, '_PORT_RANGE', (port,)):
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as owner:
                        owner.bind((host, port))
                        with self.assertRaisesRegex(RuntimeError, 'no free TCP port'):
                            e2e.free_port(taken)
                        self.assertEqual(owner.getsockname(), (host, port))
                        self.assertEqual(taken, set())
                    self.assertEqual(e2e.free_port(taken), port)
                    self.assertEqual(taken, {port})
                    # The returned integer is suitable for a retained static
                    # .env port: no probe socket or reuse option is required.
                    for target in ('0.0.0.0', '127.0.0.1'):
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as successor:
                            successor.bind((target, port))


class FaultFixtureTests(unittest.TestCase):
    def test_production_matrices_do_not_share_removable_image_tags(self):
        from deploy.tests import test_update_e2e_faults as matrix
        self.assertNotEqual(matrix.PROD_STAGE, matrix.PROD_FINISH)
        case = matrix.DistributedMatrixE2E()
        co = mock.Mock()
        co.env_bytes.return_value = b'original env'
        case.dist_checkout = mock.Mock(return_value=co)
        h = mock.Mock()
        h.dist_release.side_effect = lambda version: mock.Mock(manifest=Path('/release')/version)
        with mock.patch.object(matrix, 'H', h), mock.patch.object(case, 'remove_images') as remove:
            stage, _, _ = case.production_spec('stage', matrix.PROD_STAGE)
            _, finish, _ = case.production_spec('finish', matrix.PROD_FINISH)
            stage.prepare()
        remove.assert_called_once_with(matrix.PROD_STAGE)
        self.assertNotEqual(stage.target, finish.target)
        self.assertNotIn('--acknowledge-stale-stage', stage.command)
        self.assertIn('--acknowledge-stale-stage', finish.command)
        self.assertNotIn('--acknowledge-stale-stage', finish.forward[0])

    def test_stable_environment_really_injects_faults_without_changing_stage_identity(self):
        from deploy.tests.update_e2e.faults import FaultDocker
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        real = root/'real-docker'
        real.write_text('#!/bin/sh\nprintf "real ran\\n"\n')
        real.chmod(0o700)
        fd = FaultDocker(root/'faults', docker=str(real), compose=str(real), stable_environment=True)
        baseline = fd.env()
        command = [str(fd.bin/'docker'), 'image', 'inspect', 'test:fixture']
        for action in ('run', 'fail', 'fail-after'):
            chosen = fd.env() if action == 'run' else fd.env(at=1, match='image inspect *', action=action,
                                                           stderr="can't inspect 'fixture'", exit_code=17)
            self.assertEqual(chosen, baseline, 'fault selection changed the CLI stage-binding environment')
            result = subprocess.run(command, env={**os.environ, **chosen}, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0 if action == 'run' else 17, result.stderr)
            self.assertEqual(fd.calls()[0].action, action)
            self.assertEqual(result.stdout, '' if action == 'fail' else 'real ran\n')
            if action != 'run':
                self.assertEqual(result.stderr, "can't inspect 'fixture'\n")


class StageRefusalHelperTests(unittest.TestCase):
    def test_pasted_finish_checks_the_same_invocation_environment_as_its_stage(self):
        from deploy.tests.update_e2e.harness import assert_stage_acknowledgment_required, Result
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        co = mock.Mock(env_path=root/'.env', state_dir=root/'.futureagi-updates')
        co.env_path.write_bytes(b'FUTURE_AGI_VERSION=v1.2.3\n')
        co.state_dir.mkdir()
        co.snapshot.return_value = {'unchanged': True}
        paste = mock.Mock(return_value=Result(['sh', '-c', './bin/update apply'], 3, '', 'bin/update: stale_stage\n'))
        assert_stage_acknowledgment_required(self, co, 'apply', invoke=paste)
        paste.assert_called_once_with()
        co.bin_update.assert_not_called()


class StageBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = StateDir(self.root)
        self.updater = Updater.__new__(Updater)
        self.updater.state = self.state
        self.binding = {key: 'a'*64 for key in ('manifest_hash', 'manifest_sha256', 'bundle_sha256',
            'installation_fingerprint', 'config_fingerprint', 'profile_fingerprint', 'trust_fingerprint')}
        self.staged = {'run_id': 'b'*64}

    def save(self, binding):
        self.state.write_private('stage', self.staged['run_id'], dict(binding,
            created_at='2000-01-01T00:00:00Z', backup_proof_state='legacy_absent'))

    def test_acknowledgment_excuses_only_age_and_absent_proof(self):
        self.save(self.binding)
        with self.assertRaisesRegex(GuardRefused, 'stale_stage'):
            self.updater._check_stage_binding(self.staged, self.binding, False)
        self.updater._check_stage_binding(self.staged, self.binding, True)

    def test_missing_wrong_hash_identity_config_env_profile_and_trust_still_refuse(self):
        for key in self.binding:
            for value in (None, 'c'*64):
                saved = dict(self.binding)
                if value is None:
                    del saved[key]
                else:
                    saved[key] = value
                self.save(saved)
                with self.subTest(key=key, value=value), self.assertRaisesRegex(GuardRefused, 'immutable_release_changed'):
                    self.updater._check_stage_binding(self.staged, self.binding, True)

    def test_corrupt_private_stage_cannot_be_acknowledged(self):
        self.save(self.binding)
        path = self.state.path/('stage-' + self.staged['run_id'] + '.json')
        path.write_text('{')
        for stage_only in (False, True):
            with self.subTest(stage_only=stage_only), self.assertRaises(StateError):
                self.updater._check_stage_binding(self.staged, self.binding, True, stage_only=stage_only)

    def test_restage_missing_private_binding_is_not_an_acknowledgment(self):
        for staged in (self.staged, {}):
            for acknowledge in (False, True):
                with self.subTest(staged=staged, acknowledge=acknowledge), self.assertRaisesRegex(GuardRefused, 'stale_stage'):
                    self.updater._check_stage_binding(staged, self.binding, acknowledge, stage_only=True)
        # The existing explicit legacy-finish path is distinct from restaging.
        self.updater._check_stage_binding(self.staged, self.binding, True)
        self.assertFalse(self.state.path.exists())


class RepeatedStageTests(applies.UpdaterCase):
    stage = applies.StagedUpdateTests.stage
    finish_plan = applies.StagedUpdateTests.finish_plan

    def setUp(self):
        super().setUp()
        self.binding = {key: 'a'*64 for key in ('manifest_hash', 'manifest_sha256', 'bundle_sha256',
            'installation_fingerprint', 'config_fingerprint', 'profile_fingerprint', 'trust_fingerprint')}
        self.binding['manifest_hash'] = self.plan()['manifest_hash']

    @staticmethod
    def witness(path):
        info = path.stat()
        return (None if path.is_dir() else path.read_bytes(), info.st_ino, info.st_mode,
                info.st_mtime_ns, info.st_ctime_ns)

    def restage(self, **kwargs):
        return self.updater().apply(lambda _: self.finish_plan(), {'version': 'v1.1.0'},
                                    stage=True, release_binding=self.binding, **kwargs)

    def check_restage(self, *, old_proof):
        first = self.stage(release_binding=self.binding)
        record = self.state.load_receipt(first)
        saved = self.state.read_private('stage', record['run_id'])
        if old_proof:
            # Fixture evidence is already stale before restaging; restaging must
            # preserve it, not authorize it or generate replacement proof.
            saved.update(created_at='2000-01-01T00:00:00Z', backup_completed_at='2000-01-01T00:00:01Z',
                         backup_proof_state='verified')
            self.state.write_private('stage', record['run_id'], saved)
        private = self.state.path / ('stage-' + record['run_id'] + '.json')
        first_witness, private_witness = self.witness(first), self.witness(private)
        selected, running = self.env_bytes(), dict(self.inst.running)
        self.assertEqual(len(self.hook.calls), 1)
        second = self.restage(backup_command=applies.BACKUP)
        later = self.state.load_receipt(second)
        self.assertEqual(later['resumes'], str(first))
        self.assertEqual(later['status'], 'staged')
        self.assertEqual(later['backup'], {'ran': False, 'staged': str(first)})
        self.assertEqual(later['previous_values'], record['previous_values'])
        self.assertEqual(later['previous_images'], record['previous_images'])
        self.assertEqual(self.state.read_private('stage', later['run_id']), saved)
        self.assertEqual((self.witness(first), self.witness(private)), (first_witness, private_witness))
        self.assertEqual((self.env_bytes(), self.inst.running), (selected, running))
        self.assertEqual(len(self.hook.calls), 1)
        self.assertFalse(any(call[0] == 'up' for call in self.compose_calls()))
        before = {p.name: self.witness(p) for p in self.state.path.iterdir() if p.name != 'update.lock'}
        # The real scheduled pending-state guard must reject these actual
        # repeated-stage receipts before needing any automatic authorization.
        with self.assertRaisesRegex(GuardRefused, 'manual_stage_present'):
            ScheduledGuard.__new__(ScheduledGuard)._pending(self.updater())
        with self.assertRaisesRegex(GuardRefused, 'stale_stage'):
            self.updater().apply(lambda _: self.finish_plan(), {'version': 'v1.1.0'}, release_binding=self.binding)
        self.assertEqual({p.name: self.witness(p) for p in self.state.path.iterdir() if p.name != 'update.lock'}, before)
        self.assertEqual((self.env_bytes(), self.inst.running), (selected, running))
        self.assertEqual(len(self.hook.calls), 1)
        self.assertFalse(any(call[0] == 'up' for call in self.compose_calls()))
        # Finishing still needs the operator's explicit acknowledgment; it
        # reuses the original backup and does not refresh the stored evidence.
        finished = self.updater().apply(lambda _: self.finish_plan(), {'version': 'v1.1.0'},
                                       release_binding=self.binding, acknowledge_stale_stage=True)
        final = self.state.load_receipt(finished)
        self.assertEqual((final['status'], final['resumes']), ('succeeded', str(second)))
        self.assertEqual(self.state.read_private('stage', final['run_id']), saved)
        self.assertEqual((self.witness(first), self.witness(private)), (first_witness, private_witness))
        self.assertEqual(len(self.hook.calls), 1)

    def test_restage_keeps_absent_proof_and_default_finish_refuses(self):
        self.check_restage(old_proof=False)

    def test_restage_keeps_old_backup_proof_and_default_finish_refuses(self):
        self.check_restage(old_proof=True)

    def test_restage_cannot_adopt_a_stage_with_missing_identity(self):
        first = self.stage()  # A legacy stage has no private binding to compare.
        before = self.witness(first), self.env_bytes(), dict(self.inst.running)
        with self.assertRaisesRegex(GuardRefused, 'stale_stage'):
            self.restage()
        self.assertEqual((self.witness(first), self.env_bytes(), self.inst.running), before)
        self.assertEqual(self.state.receipts(), [first])
        self.assertEqual(list(self.state.path.glob('stage-*.json')), [])
        self.assertEqual(len(self.hook.calls), 1)
        self.assertFalse(any(call[0] == 'up' for call in self.compose_calls()))

    def test_restage_rejects_every_missing_or_changed_binding_before_effects(self):
        first = self.stage(release_binding=self.binding)
        record = self.state.load_receipt(first)
        saved = self.state.read_private('stage', record['run_id'])
        for key in self.binding:
            for missing in (False, True):
                with self.subTest(key=key, missing=missing):
                    damaged = dict(saved)
                    if missing:
                        del damaged[key]
                    else:
                        damaged[key] = 'c'*64
                    self.state.write_private('stage', record['run_id'], damaged)
                    before = self.env_bytes(), dict(self.inst.running)
                    with self.assertRaisesRegex(GuardRefused, 'immutable_release_changed'):
                        self.restage()
                    self.assertEqual((self.env_bytes(), self.inst.running), before)
                    self.assertEqual(self.state.receipts(), [first])
                    self.assertEqual(self.state.read_private('stage', record['run_id']), damaged)
                    self.assertEqual(len(self.hook.calls), 1)
                    self.assertFalse(any(call[0] == 'up' for call in self.compose_calls()))


class OrdinaryStatusTests(unittest.TestCase):
    setUp = installs.MainTests.setUp
    patch = installs.MainTests.patch
    main = installs.MainTests.main

    def test_regular_non_directory_state_keeps_read_only_status_useful(self):
        state = StateDir(self.root)
        state.path.write_bytes(b'operator-owned file, not update state\n')
        before = state.path.stat()
        code, out, err = self.main('status')
        self.assertEqual((code, err), (0, ''))
        self.assertIn('Installation  ', out)
        self.assertIn('Last run      none', out)
        self.assertEqual(state.path.read_bytes(), b'operator-owned file, not update state\n')
        after = state.path.stat()
        self.assertEqual((before.st_ino, before.st_mode, before.st_mtime_ns),
                         (after.st_ino, after.st_mode, after.st_mtime_ns))


class RegularStateProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = StateDir(self.root)
        self.state.path.write_text('not a directory')
        self.state.path.chmod(0o600)

    def test_only_ordinary_probe_accepts_owned_regular_state_file(self):
        self.assertIsNone(self.state.lock_holder())
        with self.assertRaises(StateError):
            self.state.lock_holder(automation=True)
        for automation in (False, True):
            with self.subTest(automation=automation), self.assertRaises(StateError):
                with self.state.lock(automation=automation):
                    self.fail('an unusable state must never be writable')

    def test_fallback_uses_nonblocking_nofollow_and_closes_validated_fd(self):
        opened = []
        original = os.open
        def tracked(path, flags, *args, **kwargs):
            self.assertTrue(flags & os.O_NONBLOCK)
            self.assertTrue(flags & os.O_NOFOLLOW)
            fd = original(path, flags, *args, **kwargs)
            opened.append(fd)
            return fd
        with mock.patch('os.open', side_effect=tracked):
            self.assertIsNone(self.state.lock_holder())
        self.assertEqual(len(opened), 1)
        for fd in opened:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_foreign_shared_hardlinked_or_symlink_state_is_not_unheld(self):
        with mock.patch('os.geteuid', return_value=os.geteuid() + 1), self.assertRaises(StateError):
            self.state.lock_holder()
        self.state.path.chmod(0o666)
        with self.assertRaises(StateError):
            self.state.lock_holder()
        self.state.path.chmod(0o600)
        foreign = self.root/'foreign'
        os.link(self.state.path, foreign)
        with self.assertRaises(StateError):
            self.state.lock_holder()
        self.state.path.unlink()
        self.state.path.symlink_to(foreign)
        with self.assertRaises(StateError):
            self.state.lock_holder()

    def test_fifo_state_and_lock_probes_are_bounded(self):
        script = '''import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from deploy.update.receipt import StateDir, StateError
state = StateDir(Path(sys.argv[2]))
if sys.argv[3] == 'lock':
    state.ensure()
    os.mkfifo(state.lock_path, 0o600)
else:
    os.mkfifo(state.path, 0o600)
for automation in (False, True):
    try: state.lock_holder(automation=automation)
    except StateError: pass
    else: raise AssertionError('FIFO was classified as unheld')
'''
        for entry in ('state', 'lock'):
            root = self.root/entry
            root.mkdir()
            child = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(ROOT), str(root), entry],
                                   capture_output=True, text=True, timeout=5)
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)


class ScheduledStateTests(runners.RunnerCase):
    def test_regular_state_file_blocks_runner_before_discovery_or_installation(self):
        runner = self.runner()
        state = runner.engine_state
        # Keep the real registration intact for cleanup, but replace its actual
        # core-state path, not a mocked probe or a different engine-state root.
        saved = self.root/'saved-engine-state'
        state.path.rename(saved)
        try:
            state.path.write_text('not a directory')
            result = runner.tick()
            self.assertEqual((result.code, result.disposition), (2, 'blocked'))
            self.discovery_factory.assert_not_called()
            self.installation_factory.assert_not_called()
            self.assertEqual(state.path.read_text(), 'not a directory')
        finally:
            state.path.unlink()
            saved.rename(state.path)
