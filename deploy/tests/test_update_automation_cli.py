"""Canonical host CLI: real private state and publication, no daemon or sleeps."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests import test_update_public_status as pub  # noqa: E402
from deploy.tests import test_update_runner as runners  # noqa: E402
from deploy.tests import test_update_install_cli as installs  # noqa: E402
from deploy.update import host_facts  # noqa: E402
from deploy.update import __main__ as cli  # noqa: E402
from deploy.update.contracts import validate_public_status  # noqa: E402
from deploy.update.discovery import DiscoveredRelease, DiscoveryResult  # noqa: E402
from deploy.update.runner import HostRunner, TickResult  # noqa: E402
from deploy.update.runner_state import RunnerStateStore, StoreBusy, TargetIdentity  # noqa: E402
from deploy.update.schedule import Clock  # noqa: E402
from deploy.update.settings import load_settings  # noqa: E402


class GrammarTests(unittest.TestCase):
    def parse(self, *args):
        with contextlib.redirect_stderr(io.StringIO()):
            return cli.build_parser().parse_args(args)

    def test_command_specific_flags_in_both_positions(self):
        cases = {'run': [], 'download': ['--manifest', 'm', '--channel', 'beta'],
                 'enable': ['--yes'], 'disable': [], 'pause': ['--indefinite'], 'resume': [],
                 'retry': ['--target', 'v1.2.3', '--manifest-hash', 'a'*64, '--yes'],
                 'status': ['--format', 'json'], 'apply': ['--acknowledge-stale-stage']}
        for command, flags in cases.items():
            for words in ([command, *flags], [*flags, command]):
                with self.subTest(words=words):
                    self.assertEqual(self.parse(*words).command, command)
        self.assertEqual(self.parse('schedule', 'init', '--docker-context', 'test').action, 'init')
        self.assertEqual(self.parse('--backend', 'cron', 'schedule', 'render').backend, 'cron')
        self.assertEqual(self.parse('reporting', 'init', '--directory', '/srv/public').directory, '/srv/public')
        for words in (['--production', '--status-overlay', 'schedule', 'init', '--docker-context', 'test'],
                      ['schedule', 'init', '--docker-context', 'test', '--production', '--status-overlay']):
            self.assertTrue(self.parse(*words).status_overlay)

    def test_invalid_flag_matrix(self):
        cases = [('run', '--yes'), ('run', '--allow-no-backup'), ('run', '--stage'), ('run', '--force'),
                 ('run', '--acknowledge-stale-stage'), ('download', '--allow-no-backup'),
                 ('download', '--stage'), ('download', '--yes'), ('disable', '--yes'),
                 ('resume', '--until', 'later'), ('pause',), ('pause', '--indefinite', '--until', 'later'),
                 ('retry', '--target', 'v1.2.3'), ('schedule', 'init'),
                 ('schedule', 'render', '--backend', 'cron', '--docker-context', 'x'),
                 ('reporting', 'init'), ('status', '--export-public', '--format', 'json'),
                 ('apply', '--acknowledge-stale-stage', '--stage'),
                 ('apply', '--acknowledge-stale-stage', '--dry-run'),
                 ('schedule', 'init', '--docker-context', 'test', '--status-overlay'),
                 ('--production', 'schedule', 'render', '--backend', 'cron', '--status-overlay'),
                 ('--production', 'schedule', 'init', '--docker-context', 'test', '--status-overlay', '/custom.yml'),
                 *[(command, '--production', '--status-overlay') for command in cli.COMMANDS + ('run', 'download')]]
        for words in cases:
            with self.subTest(words=words), self.assertRaises(SystemExit) as caught:
                self.parse(*words)
            self.assertEqual(caught.exception.code, 2)


class CliCase(pub.PublisherCase):
    def setUp(self):
        super().setUp()
        self.parent = self.root.parent / (self.root.name + '-public')
        self.addCleanup(shutil.rmtree, self.parent, ignore_errors=True)
        self.publisher = cli.PublicStatusPublisher(self.store, self.parent)
        clock = mock.patch.object(cli, '_now', return_value=pub.NOW)
        clock.start()
        self.addCleanup(clock.stop)
        (self.root / 'bin').mkdir()
        (self.root / 'bin/update').write_text('#!/bin/sh\nexit 0\n')
        (self.root / 'bin/update').chmod(0o755)
        registration = self.store.read_registration()
        with self.store._lock('state.lock') as directory:
            self.store._write(directory, 'registration.json', replace(registration,
                launcher=replace(registration.launcher, user=cli.pwd.getpwuid(os.geteuid()).pw_name)))

    def call(self, *words):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['--root', str(self.root), *words])
        return code, out.getvalue(), err.getvalue()

    def init_reporting(self):
        code, out, err = self.call('reporting', 'init', '--directory', str(self.parent))
        self.assertEqual(code, 0, err)
        self.assertIn(str(self.publisher.path), out)
        return self.publisher.capture()[1]


class ControlTests(CliCase):
    def test_controls_persist_without_docker_or_enabling_on_resume(self):
        with mock.patch.object(cli, 'Installation', side_effect=AssertionError('Docker forbidden')):
            self.assertEqual(self.call('enable')[0], 3)
            self.assertIsNone(self.store.read_control().enabled_override)
            for words in [('enable', '--yes'), ('pause', '--indefinite'), ('disable',), ('resume',)]:
                self.assertEqual(self.call(*words)[0], 0)
        control = self.store.read_control()
        self.assertFalse(control.enabled_override)
        self.assertFalse(control.pause_indefinite)
        self.assertEqual(control.generation, 4)

    def test_pause_offset_and_rejected_timestamps(self):
        self.assertEqual(self.call('pause', '--until', (pub.NOW + timedelta(hours=1)).isoformat())[0], 0)
        self.assertEqual(self.store.read_control().pause_until, pub.NOW + timedelta(hours=1))
        before = self.store.read_control()
        for value in (pub.NOW.isoformat(), '2026-01-01T00:00:00', 'not-a-time', '2026-99-99T00:00:00Z'):
            self.assertEqual(self.call('pause', '--until', value)[0], 3)
            self.assertEqual(self.store.read_control(), before)

    def test_retry_exact_identity_and_confirmation_preserve_failure_evidence(self):
        target = TargetIdentity('v1.2.3', 'a'*64)
        attempt = self.store.reserve_attempt(target, 'window', pub.NOW)
        self.store.finish_attempt(replace(attempt, outcome='failed', finished_at=pub.NOW))
        for suffix in ([], ['--yes', '--manifest-hash', 'b'*64], ['--yes', '--manifest-hash', 'not-hex']):
            self.assertIn(self.call('retry', '--target', target.version, '--manifest-hash', target.manifest_hash, *suffix)[0], (2, 3))
            self.assertEqual(self.store.read_state().failed_target_latch, target)
        self.assertEqual(self.call('retry', '--target', target.version, '--manifest-hash', target.manifest_hash, '--yes')[0], 0)
        self.assertIsNone(self.store.read_state().failed_target_latch)
        self.assertEqual(self.store.read_state().last_window_occurrence, 'window')
        self.assertEqual(self.store.read_attempt(attempt.attempt_id).outcome, 'failed')

    def test_control_publication_preserves_freshness_and_failure_is_nonfatal(self):
        self.init_reporting()
        self.assertEqual(self.call('enable', '--yes')[0], 0)
        public = self.publisher.capture()[1]
        self.assertTrue(public['automation']['enabled'])
        self.assertIsNone(public['versions']['observed_at'])
        with mock.patch.object(cli.PublicStatusPublisher, 'publish', side_effect=OSError('private secret')):
            code, out, err = self.call('disable')
        self.assertEqual(code, 0)
        self.assertNotIn('private secret', out + err)
        self.assertFalse(self.store.read_control().enabled_override)


class ReportingTests(CliCase):
    def test_manual_discovery_never_promotes_rejected_or_incomplete_bytes(self):
        settings = load_settings(self.root, environ={})
        fetched = SimpleNamespace(newer_release=None, bundle_path=None)
        rejected = cli._manual_discovery(fetched, replace(self.release.verification, verified=False), settings)
        self.assertFalse(rejected.complete)
        self.assertEqual(rejected.reason_codes[0].value, 'signature_rejected')
        incomplete = cli._manual_discovery(fetched, self.release.verification, settings)
        self.assertFalse(incomplete.complete)
        fetched.newer_release = 'v9.0.0'
        self.assertFalse(cli._manual_discovery(fetched, self.release.verification, settings).complete)

    def test_prepared_empty_slot_is_accepted_but_foreign_content_is_not(self):
        self.parent.mkdir(mode=0o755)
        self.publisher.path.mkdir(mode=0o755)
        self.init_reporting()
        self.assertTrue((self.publisher.path/'status.json').is_file())

    def test_unknown_initial_snapshot_idempotent_same_slot_no_env_change(self):
        before = self.env.read_bytes()
        initial = self.init_reporting()
        validate_public_status(initial)
        self.assertEqual(initial['versions']['runtime_state'], 'unknown')
        self.assertEqual(initial['discovery']['state'], 'not_checked')
        self.init_reporting()
        self.assertEqual(self.env.read_bytes(), before)
        self.assertEqual((self.parent.stat().st_mode & 0o777, (self.publisher.path/'status.json').stat().st_mode & 0o777), (0o755, 0o644))
        self.assertEqual(self.call('reporting', 'init', '--directory', str(self.root/'other'))[0], 3)

    def test_refuses_unsafe_relative_symlink_overlap_and_preexisting_files(self):
        unsafe = self.root/'unsafe'
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        link = self.root/'link'
        link.symlink_to(unsafe, target_is_directory=True)
        occupied = self.root/'occupied'
        occupied.mkdir()
        (occupied/'foreign').write_text('keep')
        for path in ('relative', str(self.root), str(self.root.parent), str(unsafe), str(link),
                     str(occupied), str(self.store.path/'public')):
            with self.subTest(path=path):
                self.assertIn(self.call('reporting', 'init', '--directory', path)[0], (2, 3))
        self.assertEqual((occupied/'foreign').read_text(), 'keep')
        self.assertEqual(unsafe.stat().st_mode & 0o777, 0o777)

    def test_capture_precedes_slow_work_and_cas_conflict_is_not_retried(self):
        self.init_reporting()
        publication = cli._Publication(self.store, load_settings(self.root, environ={}))
        self.assertEqual(self.call('pause', '--indefinite')[0], 0)
        before = (self.publisher.path/'status.json').read_bytes()
        publication.finish(kind='check')
        self.assertEqual((self.publisher.path/'status.json').read_bytes(), before)

    def test_status_text_json_export_are_distinct_and_read_only_by_default(self):
        self.init_reporting()
        install = mock.Mock(root=self.root, env_file=self.env, production=False, env={})
        with mock.patch.object(cli, 'Installation', return_value=install), \
                mock.patch.object(cli, '_status', return_value=None) as text, \
                mock.patch.object(cli, 'observe_status', return_value=self.observation):
            before = (self.publisher.path/'status.json').read_bytes()
            self.assertEqual(self.call('status')[0], 0)
            self.assertEqual(self.call('status', '--format', 'text')[0], 0)
            code, out, err = self.call('status', '--format', 'json')
            self.assertEqual(code, 0, err)
            host = json.loads(out)
            self.assertEqual(host['host_schema_version'], 1)
            self.assertIn('checkout', host)
            self.assertNotIn('schema_version', host)
            self.assertEqual((self.publisher.path/'status.json').read_bytes(), before)
            self.assertEqual(self.call('status', '--export-public')[0], 0)
            self.assertEqual(text.call_count, 2)
        self.assertNotIn(str(self.root), (self.publisher.path/'status.json').read_text())
        self.assertEqual(self.publisher.capture()[1]['versions']['running_release'], 'v1.51.0')

    def test_export_requires_prior_binding(self):
        with mock.patch.object(cli, 'Installation') as install:
            self.assertEqual(self.call('status', '--export-public')[0], 3)
            install.assert_not_called()


class RunnerWiringTests(CliCase):
    def test_real_default_run_is_local_only(self):
        with mock.patch.object(cli, 'Installation', side_effect=AssertionError('no Docker')):
            code, out, err = self.call('run')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['skip_reason'], 'disabled')

    def test_exit_and_accounting_are_not_reclassified_after_fallback(self):
        for code, started, rollout, fallback in ((0, False, False, 'none'), (3, False, False, 'none'),
                (4, True, False, 'none'), (5, True, True, 'succeeded'), (130, True, True, 'failed')):
            result = TickResult(code, 'failed', transaction_started=started, rollout_started=rollout,
                                forward_result='failed', fallback_result=fallback)
            with mock.patch.object(cli, 'HostRunner') as runner:
                runner.return_value.tick.return_value = result
                actual, out, err = self.call('run')
            self.assertEqual(actual, code, err)
            value = json.loads(out)
            self.assertEqual((value['transaction_started'], value['rollout_started'], value['fallback_result']),
                             (started, rollout, fallback))
            self.assertEqual(value['forward_result'], 'failed')
            self.assertNotIn('receipt', value)
            runner.return_value.tick.assert_called_once_with()

    def test_reporting_setup_failure_warns_without_changing_tick_outcome(self):
        with mock.patch.object(cli, '_reporting', side_effect=OSError('secret')):
            code, out, err = self.call('run')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['warnings'], ['status_reporting_unavailable'])
        self.assertNotIn('secret', out + err)
        with mock.patch.object(cli.sys, 'stderr') as error:
            error.write.side_effect = OSError('unwritable output')
            cli._Publication.warning()

    def test_busy_runner_is_zero_and_publishes_nothing(self):
        self.init_reporting()
        before = (self.publisher.path/'status.json').read_bytes()
        with self.store.runner_lock():
            code, out, err = self.call('run')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['disposition'], 'busy')
        self.assertEqual((self.publisher.path/'status.json').read_bytes(), before)

    def test_stale_ack_without_actual_stage_refuses_before_engine_or_fetch(self):
        installation = mock.Mock(root=self.root, env_file=self.env, env={})
        installation.identity.return_value = {}
        with mock.patch.object(cli, 'Installation', return_value=installation), \
                mock.patch.object(cli, 'fetch_manifest') as fetch:
            code, out, err = self.call('apply', '--acknowledge-stale-stage')
        self.assertEqual((code, err), (3, 'bin/update: stale_stage\n'))
        fetch.assert_not_called()
        self.assertEqual(self.env.read_text(), 'FUTURE_AGI_VERSION=v1.51.0\nSECRET=do-not-export\n')

    def test_typed_store_busy_never_publishes(self):
        with mock.patch.object(cli, '_automation', side_effect=StoreBusy()):
            self.assertEqual(self.call('disable')[0], 0)


class ScheduleTests(CliCase):
    def test_init_dispatch_and_path_refusals(self):
        with mock.patch.object(cli, '_enroll') as enroll:
            self.assertEqual(self.call('schedule', 'init', '--docker-context', 'registered')[0], 0)
            enroll.assert_called_once()
        link = self.root/'env-link'
        link.symlink_to(self.env)
        for env in (str(link), '.env'):
            with mock.patch.object(cli, '_enroll') as enroll:
                self.assertEqual(self.call('schedule', 'init', '--docker-context', 'registered', '--env-file', env)[0], 3)
                enroll.assert_not_called()

    def test_render_only_and_backend_requirements(self):
        before = self.store.read_control()
        for backend in ('cron', 'systemd'):
            code, out, err = self.call('schedule', 'render', '--backend', backend)
            self.assertEqual(code, 0, err)
            self.assertIn('run', out)
        self.assertEqual(self.store.read_control(), before)
        self.assertFalse((self.root/'futureagi-compose-update.service').exists())

    def test_enrollment_readonly_docker_identity_and_reinit(self):
        other = self.root/'new'
        other.mkdir()
        (other/'.env').write_bytes(self.env.read_bytes())
        (other/'docker-compose.yml').write_text('services: {}\n')
        (other/'bin').mkdir()
        (other/'bin/update').write_text('#!/bin/sh\nexit 0\n')
        (other/'bin/update').chmod(0o755)
        store = RunnerStateStore(other, other/'.env')
        installation = mock.Mock(root=other, env_file=other/'.env', env_alias=other/'.env', project='test', env={})
        installation._ownership_files.return_value = [other/'docker-compose.yml']
        installation.docker_run.return_value.stdout = 'daemon-id'
        with mock.patch.object(cli, 'Installation', return_value=installation), \
                mock.patch('deploy.update.install.resolve_compose_command', return_value=['docker', 'compose']), \
                mock.patch('shutil.which', return_value='/usr/bin/docker'):
            cli._enroll(store, 'registered', load_settings(other, environ={}))
            cli._enroll(store, 'registered', load_settings(other, environ={}))
        self.assertEqual(store.read_registration().binding.docker_context, 'registered')
        self.assertNotEqual(store.read_registration().binding.slot_id, store.selection_hash[:32])
        self.assertRegex(store.read_registration().binding.slot_id, r'^[0-9a-f]{32}$')
        self.assertIsNone(store.read_control().enabled_override)
        self.assertEqual(installation.docker_run.call_args.args, ('info', '--format', '{{.ID}}'))
        installation.compose_run.assert_not_called()
        self.assertFalse((store.path/'facts.json').exists(), 'identity registration must not attest state')


class ProductionOverlayTests(unittest.TestCase):
    """Real CLI, registration, ownership, plans and downloader; synthetic Docker/signatures."""
    def setUp(self):
        self.fixture = installs.FullStackProductionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.engine = self.fixture.root, self.fixture.engine
        env = self.fixture.env_file.read_text()
        self.fixture.env_file.write_text(env + ''.join(name+'='+self.fixture.OLD+'\n'
            for name in installs.VERSION_VARIABLES if name+'=' not in env))
        self.overlay = self.root / host_facts.STATUS_OVERLAY
        self.overlay.write_bytes((ROOT / host_facts.STATUS_OVERLAY).read_bytes())
        inventory = self.root / 'deploy/update/deployment-files.txt'
        inventory.parent.mkdir()
        inventory.write_text(host_facts.STATUS_OVERLAY + '\n')
        (self.root / 'bin').mkdir()
        (self.root / 'bin/update').write_text('#!/bin/sh\nexit 0\n')
        (self.root / 'bin/update').chmod(0o755)
        self.store = host_facts.selected_store(self.root, production=True)
        self.files = tuple(self.root / name for name in
            (installs.DISTRIBUTED_FILE, installs.PRODUCTION_OVERLAY, host_facts.STATUS_OVERLAY))
        self.select_labels(self.files)
        self.constructed = []
        for target, name, kwargs in (
                (cli, 'Installation', {'side_effect': self.installation}),
                (cli, 'ProcessSupervisor', {'side_effect': self.supervisor}),
                (cli, '_now', {'return_value': pub.NOW}),
                (shutil, 'which', {'return_value': '/fake/docker'}),
                (cli, 'daemon_space_available', {'return_value': 10**12})):
            patcher = mock.patch.object(target, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        for path in self.fixture.manifests.values():
            manifest = json.loads(path.read_bytes())
            manifest['deployment']['files'][host_facts.STATUS_OVERLAY] = installs.hash_path(self.root, host_facts.STATUS_OVERLAY)
            path.write_text(json.dumps(manifest))
            path.with_name(manifest['signature']['bundle']).write_bytes(b'synthetic-signature')

    def select_labels(self, files):
        value = ','.join(str(p) for p in files)
        self.engine.owner_labels['com.docker.compose.project.config_files'] = value
        for record in self.engine.records:
            record['Config']['Labels']['com.docker.compose.project.config_files'] = value

    def docker(self, args, **kwargs):
        args = list(args)
        args[0] = 'docker'
        if args[1:2] == ['--context']:
            del args[1:3]
        if args[1:2] == ['info']:
            return installs.completed(stdout='test-daemon')
        return self.engine(args, **kwargs)

    def supervisor(self, policy, *, docker_context, environment):
        owner = self
        class Supervisor:
            def __call__(self, args, **kwargs):
                return owner.docker(args, **kwargs)
        result = Supervisor()
        result.docker_context = docker_context
        result.environment = dict(environment, DOCKER_CONTEXT=docker_context)
        return result

    def installation(self, root, **kwargs):
        kwargs.setdefault('runner', self.docker)
        kwargs.setdefault('compose', ['docker', 'compose'])
        result = host_facts.HostInstallation(root, **kwargs)
        self.constructed.append(result)
        return result

    def call(self, *words):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['--root', str(self.root), '--production', *words])
        return code, out.getvalue(), err.getvalue()

    def enroll(self, overlay=True):
        code, out, err = self.call('schedule', 'init', '--docker-context', 'test-context',
                                   *(['--status-overlay'] if overlay else []))
        self.assertEqual(code, 0, out + err)
        return self.store.read_registration()

    def test_three_file_enrollment_round_trip_idempotent_and_readonly(self):
        before = self.fixture.env_file.read_bytes(), self.engine.container_ids()
        registration = self.enroll()
        self.assertEqual(registration.binding.compose_files, self.files)
        self.assertEqual(host_facts.registered_status_overlay(self.store), self.overlay)
        self.assertEqual(self.enroll(), registration)
        self.assertEqual(before, (self.fixture.env_file.read_bytes(), self.engine.container_ids()))
        self.assertFalse(self.engine.up_calls())
        self.assertFalse((self.store.path / 'facts.json').exists())
        self.assertIsNone(self.store.read_control().enabled_override)

    def test_two_file_production_enrollment_and_unenrolled_status_unchanged(self):
        self.select_labels(self.files[:2])
        self.assertEqual(self.call('status')[0], 0)
        registration = self.enroll(False)
        self.assertEqual(registration.binding.compose_files, self.files[:2])
        self.assertIsNone(host_facts.registered_status_overlay(self.store))
        self.assertEqual(self.call('check')[0], 0)
        self.assertIsNone(self.constructed[-1].status_overlay)

    def test_overlay_without_registration_refuses_with_enrollment_pointer(self):
        for words in (('status',), ('check',), ('apply', '--stage'), ('rollback', '--yes')):
            code, out, err = self.call(*words)
            self.assertIn(code, (2, 3), out + err)
            self.assertIn('schedule init --docker-context NAME --status-overlay', out + err)
        self.assertFalse(self.engine.up_calls())
        self.assertFalse((self.store.path / 'registration.json').exists())

    def test_changed_selection_cannot_reinitialize_existing_registration(self):
        registration = self.enroll()
        self.select_labels(self.files[:2])
        self.assertIn(self.call('schedule', 'init', '--docker-context', 'test-context')[0], (2, 3))
        self.assertEqual(self.store.read_registration(), registration)
        self.assertNotEqual(host_facts.installation_binding(
            self.installation(self.root, production=True), self.store, 'test-context').installation_fingerprint,
            registration.binding.installation_fingerprint)

    def test_all_manual_commands_and_download_preserve_exact_file_set(self):
        self.enroll()
        before = self.fixture.env_file.read_bytes(), self.engine.container_ids()
        for words in (('status',), ('check',), ('apply', '--dry-run', '--stage'), ('download',)):
            with self.subTest(words=words):
                code, out, err = self.call(*words)
                self.assertEqual(code, 0, out + err)
        # The existing status observer calls exited initialization jobs unknown;
        # overlay selection must neither conceal that nor fabricate healthy status.
        code, out, err = self.call('status', '--format', 'json')
        self.assertEqual(code, 2, err)
        self.assertEqual(json.loads(out)['reason_codes'], ['running_versions_unknown'])
        self.assertEqual(before, (self.fixture.env_file.read_bytes(), self.engine.container_ids()))
        self.assertFalse(self.engine.up_calls())
        # Exercise real stage, staged resume and rollback through their ownership checks.
        for words in (('apply', '--stage'), ('apply', '--acknowledge-stale-stage'), ('rollback', '--yes')):
            code, out, err = self.call(*words)
            self.assertEqual(code, 0, out + err)
        for instance in self.constructed:
            self.assertEqual(tuple(instance._ownership_files()), self.files)
        for args in self.engine.calls:
            if args[:2] == ['docker', 'compose'] and '-f' in args:
                self.assertEqual(tuple(Path(args[i+1]) for i, word in enumerate(args) if word == '-f'), self.files)

    def test_json_status_and_public_export_with_inactive_initialization_profile(self):
        # An explicitly inactive init profile is not a required running replica.
        self.engine.profiles['postgres-schema-bootstrap'] = ['init']
        self.engine.records = [r for r in self.engine.records
                               if self.engine.service(r) != 'postgres-schema-bootstrap']
        self.enroll()
        parent = self.root.parent / 'public'
        code, out, err = self.call('reporting', 'init', '--directory', str(parent))
        self.assertEqual(code, 0, err)
        path = parent / self.store.read_registration().binding.slot_id / 'status.json'
        before = path.read_bytes()
        code, out, err = self.call('status', '--format', 'json')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['versions']['runtime_state'], 'consistent')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.call('status', '--export-public')[0], 0)
        validate_public_status(json.loads(path.read_bytes()))
        self.assertFalse(self.engine.up_calls())

    def test_overlay_never_promotes_unverified_manifest(self):
        self.enroll()
        cli.Verifier.return_value.verify.return_value = installs.Verification(False)
        code, out, err = self.call('check')
        self.assertEqual((code, err), (3, 'bin/update: signature_rejected\n'))

    def test_runner_receives_only_registered_overlay(self):
        self.enroll()
        with mock.patch.object(cli, 'HostRunner') as runner:
            runner.return_value.tick.return_value = TickResult()
            self.assertEqual(self.call('run')[0], 0)
            self.assertEqual(runner.call_args.kwargs['status_overlay'], self.overlay)
            self.assertIs(runner.call_args.kwargs['discovery_factory'], host_facts.overlay_discovery)
            runner.return_value.tick.assert_called_once_with()

    def test_real_notify_and_download_ticks_use_registered_overlay(self):
        self.enroll()
        manifest = json.loads(self.fixture.manifests[self.fixture.NEW].read_bytes())
        raw = json.dumps(manifest).encode()
        from deploy.update.manifest import manifest_hash
        from deploy.update.contracts import VerificationSnapshot
        release = DiscoveredRelease(raw, b'synthetic-signature', manifest_hash(manifest), 'a'*64,
            VerificationSnapshot(True, 'keyless', 'signer', 'issuer', 'cosign', pub.NOW))
        discovery = mock.Mock()
        discovery.discover.return_value = DiscoveryResult(release, checked_at=pub.NOW)
        discovery.refresh.return_value = discovery.discover.return_value
        def runner(store, **kwargs):
            return HostRunner(store, clock=Clock(lambda: pub.NOW, lambda: 0), **kwargs)
        for mode in ('notify', 'download'):
            (self.root / 'update.conf').write_text('FUTURE_AGI_UPDATE_ENABLED=true\nFUTURE_AGI_UPDATE_AUTOMATION_MODE='+mode+'\n')
            with mock.patch.object(cli, 'HostRunner', side_effect=runner), \
                    mock.patch('deploy.update.runner.ProcessSupervisor', side_effect=self.supervisor), \
                    mock.patch('deploy.update.runner.host_discovery', return_value=discovery):
                code, out, err = self.call('run')
            self.assertEqual(code, 0, out + err)
            self.assertEqual(json.loads(out)['disposition'], 'available' if mode == 'notify' else 'downloaded')
        self.assertFalse(self.engine.up_calls())

    def test_tampered_reordered_extra_or_short_registration_refuses_every_command(self):
        self.enroll()
        path = self.store.path / 'registration.json'
        original = path.read_bytes()
        value = json.loads(original)
        cases = [list(reversed(value['binding']['compose_files'])), value['binding']['compose_files'] + [str(self.overlay)],
                 value['binding']['compose_files'][:2], [str(self.root / 'other.yml')], []]
        for files in cases:
            value['binding']['compose_files'] = files
            path.write_text(json.dumps(value))
            for words in (('status',), ('check',), ('apply',), ('rollback', '--yes'), ('download',), ('run',)):
                with self.subTest(files=files, words=words), mock.patch.object(cli, 'HostRunner') as runner:
                    code, out, err = self.call(*words)
                    self.assertIn(code, (2, 3), out + err)
                    self.assertIn('configuration_changed' if files else 'control_invalid', err)
                    runner.assert_not_called()
        path.write_bytes(original)

    def test_inventory_and_signed_deployment_bytes_fail_closed(self):
        self.enroll()
        manifest_path = self.fixture.manifests[self.fixture.NEW]
        manifest = json.loads(manifest_path.read_bytes())
        original = self.overlay.read_bytes()
        self.overlay.write_bytes(original + b'\n# changed\n')
        for command in ('check', 'download', 'apply'):
            code, out, err = self.call(command)
            self.assertEqual((code, err), (3, 'bin/update: deployment_files_mismatch\n'))
        self.overlay.write_bytes(original)
        del manifest['deployment']['files'][host_facts.STATUS_OVERLAY]
        manifest_path.write_text(json.dumps(manifest))
        self.assertEqual(self.call('check')[0], 3)
        (self.root / 'deploy/update/deployment-files.txt').write_text('docker-compose.distributed.yml\n')
        self.assertEqual(self.call('status')[0], 3)
        self.assertEqual(self.call('run')[0], 3)

    def test_registered_ownership_mismatch_fails_closed(self):
        self.enroll()
        self.select_labels(self.files[:2])
        for words in (('status',), ('check',), ('apply', '--stage'), ('rollback', '--yes'), ('download',)):
            code, out, err = self.call(*words)
            self.assertIn(code, (2, 3), out + err)
        self.assertFalse(self.engine.up_calls())
        self.assertEqual(self.fixture.backups(), 0)


class CliIntegrationTests(unittest.TestCase):
    fixture = runners.RealLayoutTests.fixture

    def test_explicit_check_publishes_verified_candidate_without_changing_schedule(self):
        fixture, store = self.fixture()
        manifest = fixture.root/'release.json'
        manifest.write_bytes(fixture.prepared.raw_manifest)
        bundle = fixture.root/'release.bundle'
        bundle.write_bytes(fixture.prepared.raw_bundle)
        parent = fixture.root.parent / (fixture.root.name + '-public')
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        with contextlib.redirect_stdout(io.StringIO()):
            cli._reporting_init(store, str(parent), load_settings(fixture.root, environ={}))
        state = store.read_state()
        public = parent/store.read_registration().binding.slot_id/'status.json'
        verifier = mock.Mock()
        verifier.verify.return_value = fixture.prepared.verification
        words = ['--root', str(fixture.root), 'check', '--format', 'json', '--manifest', str(manifest), '--bundle', str(bundle)]
        with mock.patch.object(cli, 'Installation', return_value=fixture.inst), \
                mock.patch.object(cli, 'build_plan', side_effect=lambda *a, **kw: fixture.plan()), \
                mock.patch.object(cli, 'Verifier', return_value=verifier), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(words), 0)
            snapshot = json.loads(public.read_bytes())
            self.assertEqual(snapshot['discovery']['candidate_version'], fixture.prepared.manifest['version'])
            self.assertEqual(snapshot['discovery']['verification'], 'verified')
            self.assertIsNotNone(snapshot['discovery']['last_attempt_at'])
            with mock.patch.object(cli, '_manual_discovery', side_effect=OSError('private secret')):
                self.assertEqual(cli.main(words), 0)
        self.assertEqual(store.read_state(), state)
        self.assertEqual(fixture.env_bytes(), fixture.original)

    def test_manual_download_works_disabled_and_busy_never_publishes(self):
        fixture, store = self.fixture()
        manifest = fixture.root/'release.json'
        manifest.write_bytes(fixture.prepared.raw_manifest)
        bundle = fixture.root/'release.bundle'
        bundle.write_bytes(fixture.prepared.raw_bundle)
        parent = fixture.root.parent / (fixture.root.name + '-public')
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        with contextlib.redirect_stdout(io.StringIO()):
            cli._reporting_init(store, str(parent), load_settings(fixture.root, environ={}))
        public = parent/store.read_registration().binding.slot_id/'status.json'
        verifier = mock.Mock()
        verifier.verify.return_value = fixture.prepared.verification
        verifier.trust_fingerprint.return_value = fixture.prepared.trust_fingerprint
        words = ['--root', str(fixture.root), 'download', '--manifest', str(manifest), '--bundle', str(bundle)]
        with mock.patch.object(cli, 'registered_installation', return_value=fixture.inst), \
                mock.patch.object(cli, 'prepare_update', return_value=fixture.prepared) as prepare, \
                mock.patch.object(cli, 'Verifier', return_value=verifier) as verify, \
                mock.patch.object(cli, 'daemon_space_available', return_value=10**12), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            before = public.read_bytes()
            with fixture.state.lock(automation=True):
                self.assertEqual(cli.main(words), 0, err.getvalue())
            self.assertEqual(public.read_bytes(), before)
            self.assertEqual(cli.main(words), 0, err.getvalue())
        self.assertTrue(verify.call_args.kwargs['host_only'])
        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(json.loads(public.read_bytes())['download']['state'], 'downloaded')
        self.assertIn('Downloaded', out.getvalue())
        self.assertEqual(fixture.env_bytes(), fixture.original)
        self.assertIsNone(fixture.state.read_journal())
        self.assertEqual(fixture.state.receipts(), [])
        self.assertIsNone(store.read_control().enabled_override)
    def test_notify_download_cli_and_app_file(self):
        fixture, store = self.fixture()
        # This integration begins unenrolled and exercises the canonical CLI,
        # rather than treating the runner fixture's registration as enrollment.
        shutil.rmtree(store.path)
        (fixture.root/'bin').mkdir()
        (fixture.root/'bin/update').write_text('#!/bin/sh\nexit 0\n')
        (fixture.root/'bin/update').chmod(0o755)
        with mock.patch.object(cli, 'Installation', return_value=fixture.inst), \
                mock.patch.object(fixture.inst, 'docker_run', return_value=mock.Mock(stdout='test-daemon')), \
                mock.patch('deploy.update.install.resolve_compose_command', return_value=['docker', 'compose']), \
                mock.patch('shutil.which', return_value='/usr/bin/docker'), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            code = cli.main(['--root', str(fixture.root), 'schedule', 'init', '--docker-context', 'test-context'])
        self.assertEqual(code, 0, error.getvalue())
        fixture.prepared = replace(fixture.prepared, binding=store.read_registration().binding)
        release = DiscoveredRelease(fixture.prepared.raw_manifest, fixture.prepared.raw_bundle,
            fixture.prepared.plan['manifest_hash'], fixture.prepared.trust_fingerprint, fixture.prepared.verification)
        discovery = mock.Mock()
        discovery.discover.return_value = DiscoveryResult(release, checked_at=pub.NOW)
        discovery.refresh.return_value = discovery.discover.return_value
        parent = fixture.root.parent / (fixture.root.name + '-public')
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        with mock.patch.object(cli, '_now', return_value=pub.NOW), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            code = cli.main(['--root', str(fixture.root), 'reporting', 'init', '--directory', str(parent)])
        self.assertEqual(code, 0, error.getvalue())
        def runner(selected, **kwargs):
            return HostRunner(selected, publisher=kwargs['publisher'], clock=Clock(lambda: pub.NOW, lambda: 0),
                discovery_factory=lambda *args: discovery, installation_factory=lambda *args, **kwargs: fixture.inst,
                space_available=lambda install: 10**12)
        for mode in ('notify', 'download'):
            (fixture.root/'update.conf').write_text('FUTURE_AGI_UPDATE_AUTOMATION_MODE='+mode+'\nFUTURE_AGI_UPDATE_ENABLED=true\n')
            with mock.patch.object(cli, 'HostRunner', side_effect=runner), \
                    mock.patch('deploy.update.runner.prepare_update', return_value=fixture.prepared), \
                    mock.patch('deploy.update.runner.check_deployment'), contextlib.redirect_stdout(io.StringIO()) as out:
                code = cli.main(['--root', str(fixture.root), 'run'])
            self.assertEqual(code, 0, out.getvalue())
            self.assertEqual(json.loads(out.getvalue())['disposition'], 'available' if mode == 'notify' else 'downloaded')
        public = json.loads((parent/store.read_registration().binding.slot_id/'status.json').read_bytes())
        validate_public_status(public)
        self.assertEqual(public['download']['state'], 'downloaded')
        self.assertEqual(fixture.env_bytes(), fixture.original)
        self.assertIsNone(fixture.state.read_journal())
        self.assertEqual(fixture.state.receipts(), [])

