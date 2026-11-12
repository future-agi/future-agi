"""Canonical host CLI: real private state and publication, no daemon or sleeps."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
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


# The options bin/update had before the automation commands: (commands taking it, flag, value or None, namespace
# attribute). A prefix that named exactly one of these (and, before the command, --root/--cwd) must keep naming it.
LEGACY_OPTIONS = (
    (cli.COMMANDS, '--production', None, 'production'),
    (cli.COMMANDS, '--env-file', 'p.env', 'env_file'),
    (('check', 'apply'), '--manifest', 'm', 'manifest'),
    (('check', 'apply'), '--bundle', 'b', 'bundle'),
    (('check', 'apply'), '--policy', 'minor', 'policy'),
    (('check', 'apply'), '--channel', 'beta', 'channel'),
    (('check',), '--format', 'json', 'format'),
    (('apply',), '--backup-command', '/bin/backup', 'backup_command'),
    (('apply',), '--allow-no-backup', None, 'allow_no_backup'),
    (('apply',), '--dry-run', None, 'dry_run'),
    (('apply',), '--stage', None, 'stage'),
    (('rollback',), '--receipt', 'r', 'receipt'),
    (('rollback',), '--yes', None, 'yes'),
)


class AbbreviationTests(unittest.TestCase):
    """Review round 2: the automation options must not take abbreviations away from the older ones."""

    @classmethod
    def setUpClass(cls):
        cls.parser = cli.build_parser()

    def parse(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                return 0, self.parser.parse_args(args), err.getvalue()
            except SystemExit as caught:
                return caught.code, None, err.getvalue()

    def test_reported_prefixes_work_before_and_after_the_command(self):
        for words, attribute, value in (
                (['check', '--man', 'm'], 'manifest', 'm'), (['--man', 'm', 'check'], 'manifest', 'm'),
                (['apply', '--sta'], 'stage', True), (['--sta', 'apply'], 'stage', True),
                (['apply', '--ba', '/bin/backup'], 'backup_command', '/bin/backup'),
                (['--ba', '/bin/backup', 'apply'], 'backup_command', '/bin/backup'),
                (['check', '--man=m'], 'manifest', 'm'), (['--ba=/bin/backup', 'apply'], 'backup_command', '/bin/backup')):
            with self.subTest(words=words):
                code, args, err = self.parse(*words)
                self.assertEqual(code, 0, err)
                self.assertEqual(getattr(args, attribute), value)

    def test_other_legacy_prefixes_keep_naming_the_older_option(self):
        table = (
            (['apply', '--a'], 'allow_no_backup', True), (['--a', 'apply'], 'allow_no_backup', True),
            (['apply', '--d'], 'dry_run', True), (['--d', 'apply'], 'dry_run', True),
            (['apply', '--s'], 'stage', True), (['apply', '--st'], 'stage', True),
            (['status', '--e', 'x.env'], 'env_file', 'x.env'), (['--e', 'x.env', 'status'], 'env_file', 'x.env'),
            (['check', '--m', 'm'], 'manifest', 'm'), (['--m', 'm', 'check'], 'manifest', 'm'),
            (['check', '--manif', 'm'], 'manifest', 'm'), (['check', '--manifest', 'm'], 'manifest', 'm'),
            (['check', '--bu', 'b'], 'bundle', 'b'), (['check', '--po', 'major'], 'policy', 'major'),
            (['check', '--ch', 'beta'], 'channel', 'beta'), (['check', '--f', 'json'], 'format', 'json'),
            (['rollback', '--y'], 'yes', True), (['rollback', '--re', 'r'], 'receipt', 'r'),
            (['rollback', '--pr'], 'production', True),
            (['apply', '--al'], 'allow_no_backup', True), (['apply', '--dr'], 'dry_run', True),
            (['apply', '--back', '/bin/backup'], 'backup_command', '/bin/backup'))
        for words, attribute, value in table:
            with self.subTest(words=words):
                code, args, err = self.parse(*words)
                self.assertEqual(code, 0, err)
                self.assertEqual(getattr(args, attribute), value)

    def test_every_prefix_that_named_exactly_one_legacy_option_still_does(self):
        names = [flag for _, flag, _, _ in LEGACY_OPTIONS]
        compared = 0
        for takers, flag, value, attribute in LEGACY_OPTIONS:
            for length in range(3, len(flag) + 1):  # '--x' up to the full spelling
                prefix = flag[:length]
                # The hidden top-level --root/--cwd took part in prefix matching too (before the command always,
                # and after it on Pythons that classify every token at the top level).
                matching = [name for name in names + ['--help', '--root', '--cwd'] if name.startswith(prefix)]
                if len(matching) != 1 and prefix != flag:
                    continue  # ambiguous among the older options before the automation commands too
                for command in takers:
                    words = [prefix] + ([value] if value else [])
                    for argv in ([*words, command], [command, *words], [command, prefix + '=' + value] if value else [command, prefix]):
                        with self.subTest(argv=argv):
                            code, args, err = self.parse(*argv)
                            self.assertEqual(code, 0, err)
                            self.assertEqual(getattr(args, attribute), value or True)
                            compared += 1
        self.assertGreater(compared, 200)

    def test_prefixes_that_were_ambiguous_between_older_options_stay_ambiguous(self):
        # Both candidates are options the command takes (or, before the command, older options): no guess is made.
        for words in (['apply', '--p', 'minor'], ['--p', 'minor', 'apply'], ['apply', '--b', 'x'],
                      ['--b', 'x', 'apply'], ['--c', 'beta', 'check'], ['--r', 'x', 'rollback']):
            with self.subTest(words=words):
                code, _, err = self.parse(*words)
                self.assertEqual(code, 2)
                self.assertIn('ambiguous option', err)

    def test_options_a_command_does_not_take_do_not_take_part_in_its_prefix_matching(self):
        for words, attribute, value in (
                (['retry', '--man', 'a' * 64, '--target', 'v1.2.3', '--yes'], 'manifest_hash', 'a' * 64),
                (['retry', '--manifest-h', 'a' * 64, '--target', 'v1.2.3', '--yes'], 'manifest_hash', 'a' * 64),
                (['schedule', 'render', '--ba', 'cron'], 'backend', 'cron'),
                (['schedule', 'init', '--d', 'ctx'], 'docker_context', 'ctx'),
                (['reporting', 'init', '--d', '/srv/public'], 'directory', '/srv/public'),
                (['pause', '--u', '2099-01-01T00:00:00Z'], 'until', '2099-01-01T00:00:00Z'),
                (['pause', '--i'], 'indefinite', True), (['status', '--exp'], 'export_public', True),
                (['--prod', 'run'], 'production', True)):
            with self.subTest(words=words):
                code, args, err = self.parse(*words)
                self.assertEqual(code, 0, err)
                self.assertEqual(getattr(args, attribute), value)

    def test_command_aware_prefixes_before_command_and_command_named_values(self):
        cases = ((['--man', 'apply', '--target', 'v1.2.3', '--yes', 'retry'], 'manifest_hash', 'apply'),
                 (['--ba', 'cron', 'schedule', 'render'], 'backend', 'cron'),
                 (['--d', 'check', 'schedule', 'init'], 'docker_context', 'check'),
                 (['--d', 'apply', 'reporting', 'init'], 'directory', 'apply'),
                 (['--b', 'apply', 'check'], 'bundle', 'apply'),
                 (['--manifest', 'rollback', '--ba', 'backup', 'apply'], 'backup_command', 'backup'),
                 (['--root', 'check', '--d', 'apply'], 'dry_run', True))
        for words, attribute, value in cases:
            with self.subTest(words=words):
                code, args, err = self.parse(*words)
                self.assertEqual(code, 0, err)
                self.assertEqual(getattr(args, attribute), value)
        for words in (['--unknown', 'check'], ['-', 'check'], ['--man'],
                      ['check', '--unknown'], ['--ba', 'apply']):
            with self.subTest(words=words):
                self.assertEqual(self.parse(*words)[0], 2)

    def test_forbidden_full_flags_and_their_abbreviations_are_still_usage_errors(self):
        values = {'--env-file': 'p.env', '--manifest': 'm', '--bundle': 'b', '--policy': 'minor', '--channel': 'stable',
                  '--format': 'json', '--backup-command': '/bin/backup', '--receipt': 'r', '--until': '2099-01-01T00:00:00Z',
                  '--target': 'v1.2.3', '--manifest-hash': 'a' * 64, '--docker-context': 'fake', '--backend': 'cron',
                  '--directory': '/srv/public'}
        bases = {command: [command] for command in cli.ALL_COMMANDS}
        bases.update(pause=['pause', '--indefinite'], retry=['retry', '--yes', '--target', 'v1.2.3', '--manifest-hash', 'a' * 64],
                     schedule=['schedule', 'init', '--docker-context', 'fake'], reporting=['reporting', 'init', '--directory', '/srv/public'])
        rejected = 0
        for takers, _, flag, _ in cli.OPTIONS:
            for command in cli.ALL_COMMANDS:
                if command in takers:
                    continue
                flagged = [flag] + ([values[flag]] if flag in values else [])
                for words in (flagged + bases[command], bases[command] + flagged, flagged + bases[command] + flagged):
                    with self.subTest(words=words):
                        self.assertEqual(self.parse(*words)[0], 2)
                        rejected += 1
        self.assertGreater(rejected, 500)
        for words in (['check', '--stag'], ['check', '--st'], ['run', '--status'], ['check', '--manifest-h', 'x'],
                      ['apply', '--backe', 'cron'], ['run', '--y'], ['download', '--stag'], ['run', '--force'],
                      ['apply', '--yes=false'], ['apply', '--stage=false']):
            with self.subTest(words=words):
                self.assertEqual(self.parse(*words)[0], 2)

    def test_full_flag_diagnostics_are_unchanged(self):
        code, _, err = self.parse('check', '--stage')
        self.assertEqual(code, 2)
        self.assertIn('argument --stage: not allowed with check (only apply takes it)', err)

    def test_help_and_its_abbreviation_still_work(self):
        for words in (['--help'], ['--he'], ['apply', '--he']):
            with self.subTest(words=words), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.parse(*words)[0], 0)


class WrapperRegressionTests(unittest.TestCase):
    """Review round 2: the real bin/update wrapper, a real interpreter, and a fake docker that must never run."""

    def setUp(self):
        if shutil.which('bash') is None:
            self.skipTest('bin/update needs bash')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.checkout = self.base / 'checkout'
        (self.checkout / 'bin').mkdir(parents=True)
        shutil.copy2(ROOT / 'bin' / 'update', self.checkout / 'bin' / 'update')
        shutil.copytree(ROOT / 'deploy' / 'update', self.checkout / 'deploy' / 'update',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (self.checkout / '.env').write_text('FUTURE_AGI_VERSION=v1.51.0\n')
        fakes = self.base / 'fakes'
        fakes.mkdir()
        self.docker_log = self.base / 'docker.log'
        (fakes / 'docker').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "' + str(self.docker_log) + '"\nexit 91\n')
        (fakes / 'docker').chmod(0o755)
        self.env = installs.clean_environ()
        self.env.update(PYTHON=sys.executable, PATH=str(fakes) + ':/usr/bin:/bin', FUTURE_AGI_UPDATE_E2E='0')

    def run_update(self, *words):
        return subprocess.run([str(self.checkout / 'bin' / 'update'), *words], cwd=str(self.base), env=self.env,
                              capture_output=True, text=True, timeout=120)

    def assert_typed(self, result, code, message):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stdout + result.stderr)
        self.assertTrue(result.stderr.endswith('bin/update: ' + message + '\n'), result.stderr)
        self.assertFalse(self.docker_log.exists(), 'Docker was invoked')

    def test_empty_docker_context_is_a_documented_refusal_not_a_traceback(self):
        self.assert_typed(self.run_update('schedule', 'init', '--docker-context', ''), 3, 'ownership_unconfirmed')
        self.assertFalse((self.checkout / '.futureagi-updates' / 'registration.json').exists())

    def test_whitespace_docker_context_is_refused_the_same_way(self):
        self.assert_typed(self.run_update('schedule', 'init', '--docker-context', 'two words'), 3, 'ownership_unconfirmed')

    def test_pause_timestamps_that_overflow_utc_normalization_are_control_invalid(self):
        for value in ('9999-12-31T23:59:59-23:59', '0001-01-01T00:00:00+23:59'):
            with self.subTest(value=value):
                self.assert_typed(self.run_update('pause', '--until', value), 3, 'control_invalid')

    def test_legacy_prefixes_reach_the_command_through_the_wrapper(self):
        # `--ba` names --backup-command before the command and `--sta` is --stage after it. The fake docker then
        # cannot provide Compose, which is a tool error from the command itself, never an argparse usage error.
        for words in (('--ba', '/bin/true', 'apply', '--dry-run'), ('apply', '--sta', '--dry-run'),
                      ('--man', str(self.base / 'missing.json'), 'check')):
            with self.subTest(words=words):
                result = self.run_update(*words)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn('usage:', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertTrue(result.stderr.startswith('bin/update: '), result.stderr)


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

    def test_pause_timestamps_whose_utc_form_overflows_are_a_typed_refusal(self):
        before = self.store.read_control()
        for value in ('9999-12-31T23:59:59-23:59', '0001-01-01T00:00:00+23:59', '9999-12-31T23:59:59.999999-00:01',
                      '0001-01-01T00:00:00.000000+00:01'):
            with self.subTest(value=value):
                code, out, err = self.call('pause', '--until', value)
                self.assertEqual(code, 3, err)
                self.assertIn('bin/update: control_invalid\n', err)
                self.assertNotIn('Traceback', err + out)
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
        with mock.patch.object(cli.PublicStatusPublisher, 'publish_controls', side_effect=OSError('private secret')):
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
        newer = cli._Publication(self.store, load_settings(self.root, environ={}))
        newer.finish(kind='check')
        before = (self.publisher.path/'status.json').read_bytes()
        with contextlib.redirect_stderr(io.StringIO()) as error:
            publication.finish(kind='check')
        self.assertEqual((self.publisher.path/'status.json').read_bytes(), before)
        self.assertIn('status_reporting_unavailable; newer operation report retained', error.getvalue())
        self.assertNotIn('registration', error.getvalue())

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


class PublicationRegressionTests(CliCase):
    def test_controls_and_heartbeats_do_not_fence_manual_terminal_report(self):
        self.init_reporting()
        publication = cli._Publication(self.store, load_settings(self.root, environ={}))
        initial, _ = self.publisher.capture()
        for words in (('enable', '--yes'), ('pause', '--indefinite'), ('resume',), ('disable',)):
            self.assertEqual(self.call(*words)[0], 0)
        # The real disabled HostRunner performs a heartbeat, not an operation.
        tick = HostRunner(self.store, publisher=self.publisher, clock=Clock(lambda: pub.NOW, lambda: 0)).tick()
        self.assertEqual(tick.skip_reason, 'disabled')
        with mock.patch.object(cli, 'observe_status', return_value=self.observation), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            publication.finish(installation=object(), kind='check')
        ticket, snapshot = self.publisher.capture()
        self.assertEqual(error.getvalue(), '')
        self.assertEqual(snapshot['operation']['kind'], 'check')
        self.assertEqual(snapshot['operation']['state'], 'succeeded')
        self.assertEqual(snapshot['versions'], self.observation.versions)
        self.assertFalse(snapshot['automation']['enabled'])
        self.assertGreater(ticket.generation, initial.generation + 1)

    def test_controls_preserve_live_operation_and_its_next_progress_report(self):
        self.init_reporting()
        ticket, _ = self.publisher.capture()
        snapshot = self.snapshot(operation={'kind': 'apply', 'state': 'backing_up', 'stage': 'backup',
            'started_at': cli.stamp(pub.NOW), 'finished_at': None, 'reported_at': cli.stamp(pub.NOW), 'reason_codes': []})
        owner = self.publisher.publish(snapshot, expected=ticket, operation_token='owner')
        before = self.publisher.capture()[1]
        for words in (('pause', '--indefinite'), ('disable',), ('enable', '--yes'), ('resume',)):
            self.assertEqual(self.call(*words)[0], 0)
            after = self.publisher.capture()[1]
            for key in ('operation', 'versions', 'discovery', 'download', 'last_run', 'safety'):
                self.assertEqual(after[key], before[key], key)
        snapshot['operation'].update(state='succeeded', stage=None, finished_at=cli.stamp(pub.NOW))
        self.publisher.publish(snapshot, expected=owner, operation_token='owner')
        self.assertEqual(self.publisher.capture()[1]['operation']['state'], 'succeeded')

    def test_busy_report_retries_original_ticket_and_later_finish_still_works(self):
        self.init_reporting()
        publication = cli._Publication(self.store, load_settings(self.root, environ={}))
        before = (self.publisher.path / 'status.json').read_bytes()
        with self.store._lock('public.lock'), mock.patch('time.sleep') as sleep, \
                contextlib.redirect_stderr(io.StringIO()) as error:
            publication.finish(kind='check')
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual((self.publisher.path / 'status.json').read_bytes(), before)
        self.assertIn('public report busy', error.getvalue())
        self.assertNotIn('registration', error.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as error:
            publication.finish(kind='check')
            publication.finish(kind='check')  # Retain the returned operation ticket, not the first ticket.
        self.assertEqual(error.getvalue(), '')
        self.assertEqual(self.publisher.capture()[1]['operation']['state'], 'succeeded')

    def test_capture_busy_is_nonfatal_to_manual_command_but_not_misdiagnosed(self):
        self.init_reporting()
        with self.store._lock('public.lock'), mock.patch('time.sleep'), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            publication = cli._Publication(self.store, load_settings(self.root, environ={}))
        self.assertIsNone(publication.publisher)
        self.assertIn('public report busy', error.getvalue())
        self.assertNotIn('registration', error.getvalue())

    def test_explicit_export_publish_failure_and_conflict_are_nonzero_once(self):
        self.init_reporting()
        install = mock.Mock(root=self.root, env_file=self.env, production=False, env={})
        for failure, code, message in ((OSError('private secret'), 2, 'private secret'),
                                      (cli.PublicationConflict(), 2, 'newer operation report retained'),
                                      (StoreBusy(), 3, 'busy; nothing recorded')):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch.object(cli, 'Installation', return_value=install), \
                    mock.patch.object(cli, 'observe_status', return_value=self.observation), \
                    mock.patch.object(cli.PublicStatusPublisher, 'publish', side_effect=failure) as publish, \
                    mock.patch('time.sleep'):
                result, out, err = self.call('status', '--export-public')
                self.assertEqual(result, code)
                self.assertIn(message, err)
                self.assertEqual(publish.call_count, 5 if isinstance(failure, StoreBusy) else 1)
                self.assertNotIn('Traceback', out + err)
                if isinstance(failure, cli.PublicationConflict):
                    self.assertNotIn('registration', err)

    def test_failed_registration_write_leaves_no_published_slot_and_retries(self):
        write = self.store._write
        def fail_registration(directory, name, value):
            if name == 'reporting.json':
                raise OSError(28, 'private ENOSPC detail')
            return write(directory, name, value)
        with mock.patch.object(type(self.store), '_write', staticmethod(fail_registration)):
            code, out, err = self.call('reporting', 'init', '--directory', str(self.parent))
        self.assertNotEqual(code, 0)
        self.assertIn('retry with the same directory', err)
        self.assertFalse((self.publisher.path / 'status.json').exists())
        self.init_reporting()
        self.assertIsNotNone(cli._reporting(self.store))

    def test_interruption_before_or_after_initial_publication_is_retryable(self):
        publish = cli.PublicStatusPublisher._replace
        for after in (False, True):
            with self.subTest(after=after):
                # The first case creates the registration; the second is an idempotent re-init.
                def interrupt(publisher, snapshot):
                    if after:
                        publish(publisher, snapshot)
                    raise KeyboardInterrupt()
                with mock.patch.object(cli.PublicStatusPublisher, '_replace', interrupt):
                    code, out, err = self.call('reporting', 'init', '--directory', str(self.parent))
                self.assertEqual(code, 130)
                self.assertIn('retry with the same directory', err)
                self.assertIsNotNone(cli._reporting(self.store))
                self.init_reporting()
                validate_public_status(self.publisher.capture()[1])
                other = self.parent.with_name(self.parent.name + '-other')
                self.assertEqual(self.call('reporting', 'init', '--directory', str(other))[0], 3)
                self.assertFalse(other.exists())

    def test_unregistered_foreign_status_or_symlink_is_never_adopted(self):
        self.parent.mkdir(mode=0o755)
        self.publisher.path.mkdir(mode=0o755)
        status = self.publisher.path / 'status.json'
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if symlink:
                    status.symlink_to(self.env)
                else:
                    status.write_text('{}')
                before = self.env.read_bytes()
                self.assertEqual(self.call('reporting', 'init', '--directory', str(self.parent))[0], 3)
                self.assertIsNone(cli._reporting(self.store))
                self.assertEqual(self.env.read_bytes(), before)
                status.unlink()


class RunnerControlPublicationTests(runners.RunnerCase):
    def test_pause_cli_during_automatic_backup_keeps_progress_and_terminal_owner(self):
        from deploy.update.orchestrator import EngineResult
        with self.store._lock('reporting.lock') as directory:
            self.store._write(directory, 'reporting.json', cli.ReportingRegistration(
                1, self.parent, self.binding.installation_fingerprint))
        settings = self.settings('apply')
        def execute(engine, guard):
            guard.report('before_backup')
            before = self.publisher.capture()[1]
            with mock.patch.object(cli, 'load_settings', return_value=settings), \
                    mock.patch.object(cli, '_now', side_effect=lambda: self.now), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(cli.main(['--root', str(self.root), 'pause', '--indefinite']), 0)
            self.assertEqual(error.getvalue(), '')
            after = self.publisher.capture()[1]
            self.assertEqual(after['operation'], before['operation'])
            self.assertEqual(after['automation']['state'], 'paused')
            guard.report('after_backup')
            guard.report('before_env_commit')
            self.assertEqual(self.publisher.capture()[1]['operation']['stage'], 'env_selection')
            return EngineResult(0, (), True, True, 'succeeded')
        self.executor.side_effect = execute
        result = self.runner('apply', settings=settings).tick()
        self.assertEqual((result.code, result.disposition, result.warnings), (0, 'applied', ()))
        for _ in range(3):
            self.now += timedelta(minutes=5)
            self.monotonic += 300
            self.assertEqual(self.runner('apply', settings=settings).tick().skip_reason, 'paused')
        final = self.publisher.capture()[1]
        self.assertEqual(final['operation']['state'], 'succeeded')
        self.assertEqual(final['last_run']['outcome'], 'succeeded')


class ShortLockTests(CliCase):
    def setUp(self):
        super().setUp()
        from functools import partial
        self.sleep = mock.Mock()
        self.retry_clock = mock.Mock(return_value=0)
        # Exercise the attempt bound independently of filesystem latency. The
        # deadline path has its own advancing-clock test below; production's
        # 200ms/five-attempt budget is unchanged.
        patcher = mock.patch.object(cli, '_retry_busy', partial(cli._retry_busy,
                                    monotonic=self.retry_clock, sleep=self.sleep))
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, *words):
        # Each CLI call owns its observations, even when a preceding subtest
        # assertion failed before reaching assert_busy's end.
        self.sleep.reset_mock()
        return super().call(*words)

    def assert_busy(self, result):
        code, out, err = result
        self.assertEqual(code, 3, out + err)
        self.assertIn('busy', err)
        self.assertIn('nothing recorded', err)
        self.assertNotIn('Traceback', out + err)
        self.assertEqual(self.sleep.call_args_list, [mock.call(0.05)] * 4)

    def test_control_lock_never_silently_loses_an_operator_command(self):
        before = self.store.read_control()
        with self.store._lock('control.lock'):
            for words in (('disable',), ('pause', '--indefinite'),
                          ('pause', '--until', (pub.NOW + timedelta(hours=1)).isoformat()),
                          ('resume',), ('enable', '--yes')):
                with self.subTest(words=words):
                    self.assert_busy(self.call(*words))
                    self.assertEqual(self.store.read_control(), before)

    def test_retry_state_lock_keeps_latch_and_is_nonzero(self):
        target = TargetIdentity('v1.2.3', 'a' * 64)
        attempt = self.store.reserve_attempt(target, 'window', pub.NOW)
        self.store.finish_attempt(replace(attempt, outcome='failed', finished_at=pub.NOW))
        before = self.store.read_state()
        with self.store._lock('state.lock'):
            self.assert_busy(self.call('retry', '--target', target.version, '--manifest-hash', target.manifest_hash, '--yes'))
        self.assertEqual(self.store.read_state(), before)

    def test_registration_and_export_locks_are_nonzero_without_effect(self):
        for name in ('reporting.lock', 'public.lock'):
            with self.subTest(name=name), self.store._lock(name):
                self.assert_busy(self.call('reporting', 'init', '--directory', str(self.parent)))
            self.assertIsNone(cli._reporting(self.store))
            self.assertFalse(self.publisher.path.exists())
        self.init_reporting()
        before = (self.publisher.path / 'status.json').read_bytes()
        with self.store._lock('public.lock'), mock.patch.object(cli, 'Installation') as installation:
            self.assert_busy(self.call('status', '--export-public'))
            installation.assert_not_called()
        self.assertEqual((self.publisher.path / 'status.json').read_bytes(), before)

    def test_schedule_enrollment_state_and_control_locks_are_retried_without_reprobing_docker(self):
        settings = load_settings(self.root, environ={})
        binding = self.store.read_registration().binding
        with mock.patch.object(cli, 'Installation') as installation, \
                mock.patch.object(cli, 'installation_binding', return_value=binding), \
                mock.patch('deploy.update.install.resolve_compose_command', return_value=['docker', 'compose']), \
                mock.patch('shutil.which', return_value='/usr/bin/docker'):
            for name in ('state.lock', 'control.lock'):
                with self.subTest(name=name), self.store._lock(name):
                    installation.reset_mock()
                    self.assert_busy(self.call('schedule', 'init', '--docker-context', binding.docker_context))
                    installation.assert_called_once()
        self.assertEqual(self.store.read_registration().binding, binding)
        self.assertIsNone(self.store.read_control().enabled_override)
        self.assertEqual(settings.automation_policy.mode, 'manual')

    def test_schedule_lock_deadline_stops_before_five_attempts_without_reprobing_docker(self):
        binding = self.store.read_registration().binding
        before = self.store.read_registration(), self.store.read_control()
        # Three failed acquisitions use the real locks; the injected clock
        # models slow I/O and caps the final sleep to the remaining 10ms.
        self.retry_clock.side_effect = [0, 0.10, 0.19, 0.20]
        with self.store._lock('state.lock'), \
                mock.patch.object(cli, 'Installation') as installation, \
                mock.patch.object(cli, 'installation_binding', return_value=binding), \
                mock.patch('deploy.update.install.resolve_compose_command', return_value=['docker', 'compose']), \
                mock.patch('shutil.which', return_value='/usr/bin/docker'):
            code, out, err = self.call('schedule', 'init', '--docker-context', binding.docker_context)
        self.assertEqual((code, out), (3, ''))
        self.assertIn('busy; nothing recorded', err)
        self.assertNotIn('Traceback', err)
        installation.assert_called_once()
        self.assertEqual(self.retry_clock.call_count, 4)  # start, then three busy attempts
        self.assertEqual(self.sleep.call_count, 2)
        self.assertAlmostEqual(self.sleep.call_args_list[0].args[0], 0.05)
        self.assertAlmostEqual(self.sleep.call_args_list[1].args[0], 0.01)
        self.assertEqual((self.store.read_registration(), self.store.read_control()), before)

    def test_transient_control_lock_retries_once_and_records_once(self):
        lock = self.store._lock('control.lock')
        lock.__enter__()
        self.addCleanup(lambda: lock.__exit__(None, None, None))
        self.sleep.side_effect = lambda delay: lock.__exit__(None, None, None)
        before = self.store.read_control()
        self.assertEqual(self.call('disable')[0], 0)
        self.assertEqual(self.sleep.call_count, 1)
        self.assertEqual(self.store.read_control().generation, before.generation + 1)
        self.assertFalse(self.store.read_control().enabled_override)

    def test_public_busy_after_control_commit_reports_partial_effect_honestly(self):
        self.init_reporting()
        before = (self.publisher.path / 'status.json').read_bytes()
        with self.store._lock('public.lock'):
            result = self.call('disable')
        self.assert_busy(result)
        self.assertIn('disable: recorded', result[1])
        self.assertIn('control recorded; public report busy', result[2])
        self.assertFalse(self.store.read_control().enabled_override)
        self.assertEqual((self.publisher.path / 'status.json').read_bytes(), before)

    def test_public_busy_after_registration_commit_is_explicit_and_retryable(self):
        with mock.patch.object(cli.PublicStatusPublisher, 'publish_controls', side_effect=StoreBusy()):
            result = self.call('reporting', 'init', '--directory', str(self.parent))
        self.assert_busy(result)
        self.assertIn('reporting registration recorded', result[2])
        self.assertIsNotNone(cli._reporting(self.store))
        self.init_reporting()

    def test_retry_budget_is_injected_bounded_and_only_for_busy(self):
        call = mock.Mock(side_effect=StoreBusy())
        sleep = mock.Mock()
        with self.assertRaises(StoreBusy):
            cli._retry_busy(call, monotonic=lambda: 0, sleep=sleep)
        self.assertEqual(call.call_count, 5)
        self.assertEqual(sleep.call_count, 4)
        call.reset_mock()
        sleep.reset_mock()
        with self.assertRaises(StoreBusy):
            cli._retry_busy(call, monotonic=mock.Mock(side_effect=[0, 1]), sleep=sleep)
        self.assertEqual(call.call_count, 1)
        sleep.assert_not_called()
        call.side_effect = OSError('not contention')
        with self.assertRaises(OSError):
            cli._retry_busy(call, monotonic=lambda: 0, sleep=sleep)
        sleep.assert_not_called()
        with self.assertRaises(StoreBusy), cli._short_lock(self.store, 'reporting.lock'):
            raise StoreBusy()  # Never repeat an already-entered transaction body.
        self.assertEqual(self.sleep.call_count, 0)


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
        # The contract's quiet no-op is for scheduled work, not a lost disable.
        with mock.patch.object(cli, '_automation', side_effect=StoreBusy()):
            self.assertEqual(self.call('disable'), (3, '', 'bin/update: busy; nothing recorded; retry the command\n'))
            self.assertEqual(self.call('run'), (0, '', ''))


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

    def test_unusable_docker_context_is_a_typed_refusal_before_any_supervisor_or_docker(self):
        before = self.store.read_registration(), self.store.read_control()
        contexts = ('', ' ', 'a b', 'tab\tname', 'new\nline', 'nul\x00', '\x7f', 'c1\x85', 'nbsp\xa0', 'x\u2003')
        with mock.patch.object(cli, 'ProcessSupervisor', side_effect=AssertionError('supervisor built')), \
                mock.patch.object(cli, 'Installation', side_effect=AssertionError('Docker forbidden')), \
                mock.patch.object(cli, 'safe_path', side_effect=lambda path, **kwargs: Path(path)):
            for context in contexts:
                with self.subTest(context=context):
                    self.assertEqual(self.call('schedule', 'init', '--docker-context', context),
                                     (3, '', 'bin/update: ownership_unconfirmed\n'))
        self.assertEqual((self.store.read_registration(), self.store.read_control()), before)

    def test_enrollment_helper_refuses_an_unusable_context_itself(self):
        # Defense in depth: _enroll builds the supervisor, so it must not rely on its caller having checked.
        from deploy.update.orchestrator import GuardRefused
        with mock.patch.object(cli, 'ProcessSupervisor', side_effect=AssertionError('supervisor built')):
            with self.assertRaises(GuardRefused) as caught:
                cli._enroll(self.store, '', load_settings(self.root, environ={}, verify=False))
        self.assertEqual(caught.exception.reason_code, cli.ReasonCode.OWNERSHIP_UNCONFIRMED)

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

    def test_production_stage_reports_awaiting_initialization_until_completed(self):
        clock = mock.patch.object(installs.apply_module, '_now', return_value=cli.stamp(pub.NOW))
        clock.start()
        self.addCleanup(clock.stop)
        self.enroll()
        parent = self.root.parent / 'public'
        self.assertEqual(self.call('reporting', 'init', '--directory', str(parent))[0], 0)
        public = parent / self.store.read_registration().binding.slot_id / 'status.json'
        for words, outcome in ((('apply', '--dry-run', '--stage'), 'succeeded'),
                               (('apply', '--stage'), 'awaiting_initialization'),
                               (('apply', '--acknowledge-stale-stage'), 'succeeded'),
                               (('rollback', '--yes'), 'rolled_back')):
            with self.subTest(words=words):
                code, out, err = self.call(*words)
                self.assertEqual(code, 0, out + err)
                snapshot = json.loads(public.read_bytes())
                self.assertEqual(snapshot['operation']['state'], outcome)
                self.assertEqual(snapshot['operation']['kind'], 'check' if '--dry-run' in words else words[0])
                if outcome == 'awaiting_initialization':
                    self.assertEqual(snapshot['operation']['stage'], 'production_initialization')
                    self.assertEqual(snapshot['safety']['operator_action'], 'initialize_then_apply')
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


class ManualOutcomeTests(unittest.TestCase):
    """Exercise the real Updater return/receipt contract; only Docker/signatures/readiness are synthetic."""
    fixture = runners.RealLayoutTests.fixture

    def setUp(self):
        self.layout, self.store = self.fixture()
        self.parent = self.layout.root.parent / (self.layout.root.name + '-public')
        self.addCleanup(shutil.rmtree, self.parent, ignore_errors=True)
        with contextlib.redirect_stdout(io.StringIO()):
            cli._reporting_init(self.store, str(self.parent), load_settings(self.layout.root, environ={}))
        self.public = self.parent / self.store.read_registration().binding.slot_id / 'status.json'
        self.manifest = self.layout.root / 'release.json'
        self.manifest.write_bytes(self.layout.prepared.raw_manifest)
        self.bundle = self.layout.root / 'release.bundle'
        self.bundle.write_bytes(self.layout.prepared.raw_bundle)
        self.decision = 'update_available'
        verifier = mock.Mock()
        verifier.verify.return_value = self.layout.prepared.verification
        updater = cli.Updater
        for target, name, kwargs in (
                (cli, 'Installation', {'return_value': self.layout.inst}),
                (cli, 'build_plan', {'side_effect': lambda *a, **kw: self.layout.plan(decision=self.decision)}),
                (cli, 'Verifier', {'return_value': verifier}),
                (cli, '_manual_stage_binding', {'return_value': {}}),
                (cli, 'Updater', {'side_effect': lambda *a, **kw: updater(*a, **kw, readiness=self.layout.ready,
                                                                         hook_runner=self.layout.hook)})):
            patcher = mock.patch.object(target, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, *flags):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(['--root', str(self.layout.root), 'apply', '--manifest', str(self.manifest),
                             '--bundle', str(self.bundle), '--allow-no-backup', *flags])
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        self.assertNotIn('status_reporting_unavailable', err.getvalue())
        result = json.loads(self.public.read_bytes())
        validate_public_status(result)
        return result

    def test_dry_run_is_a_check_without_a_transaction(self):
        for flags in (('--dry-run',), ('--dry-run', '--stage')):
            snapshot = self.call(*flags)
            self.assertEqual((snapshot['operation']['kind'], snapshot['operation']['state']), ('check', 'succeeded'))
            self.assertEqual(self.layout.env_bytes(), self.layout.original)
            self.assertEqual(self.layout.state.receipts(), [])
            self.assertIsNone(self.layout.state.read_journal())

    def test_up_to_date_apply_is_a_check_without_a_transaction(self):
        self.decision = 'up_to_date'
        snapshot = self.call()
        self.assertEqual((snapshot['operation']['kind'], snapshot['operation']['state']), ('check', 'succeeded'))
        self.assertEqual(self.layout.env_bytes(), self.layout.original)
        self.assertEqual(self.layout.state.receipts(), [])

    def test_stage_reports_the_returned_staged_receipt_not_success(self):
        before = dict(self.layout.inst.running)
        snapshot = self.call('--stage')
        self.assertEqual((snapshot['operation']['kind'], snapshot['operation']['state']), ('apply', 'staged'))
        self.assertEqual(self.layout.inst.running, before)
        receipt = self.layout.state.load_receipt(self.layout.state.receipts()[-1])
        self.assertEqual(receipt['status'], 'staged')

    def test_only_completed_transaction_reports_apply_succeeded(self):
        snapshot = self.call()
        self.assertEqual((snapshot['operation']['kind'], snapshot['operation']['state']), ('apply', 'succeeded'))
        receipt = self.layout.state.load_receipt(self.layout.state.receipts()[-1])
        self.assertEqual(receipt['status'], 'succeeded')
        self.assertIsNone(self.layout.state.read_journal())
        self.assertNotEqual(self.layout.env_bytes(), self.layout.original)


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
