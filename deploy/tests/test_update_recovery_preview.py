"""Read-only recovery promises must agree with actual recovery, including real kills.

Only Docker/Compose responses and signature verification are faked. Native
exchange, durable publications, CLI planning and recovery are not replaced.
Linux runs need ext4-backed temp space; overlay/virtiofs are not qualified.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]
import test_update_apply as apply_fixtures  # noqa: E402
import test_update_install_cli as fixtures  # noqa: E402
from deploy.update import __main__ as cli, envcommit  # noqa: E402
from deploy.update.apply import Blocked, preview_recovery  # noqa: E402
from deploy.update.receipt import StateDir, StateError  # noqa: E402

OLD, NEW = 'v1.0.0', 'v1.1.0'


def protected_state(root):
    """Exclude intentional lock/verification-cache bookkeeping, not recovery data."""
    result = {}
    for path in root.rglob('*'):
        if not path.is_file() or path.name == 'update.lock' or any(
                part.startswith('verify-') for part in path.relative_to(root).parts):
            continue
        info = path.stat()
        result[str(path.relative_to(root))] = (info.st_ino, info.st_mode,
                                               hashlib.sha256(path.read_bytes()).hexdigest())
    return result


class RecoveryHeaderPreviewTests(apply_fixtures.UpdaterCase):
    interrupt_apply = apply_fixtures.CrashRecoveryPreservationTests.interrupt_apply

    def assert_refused(self, journal):
        # Bypass the fault-injection fixture's stage-keyed dict (malformed stages
        # need not be hashable), not the real durable journal implementation.
        StateDir.write_journal(self.state, journal)
        before = protected_state(self.root)
        with self.assertRaises((StateError, Blocked)):
            preview_recovery(self.inst, journal, self.state.history(self.inst.identity()))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNotNone(cli._status(self.inst, self.state))
        self.assertNotIn('the next apply or rollback recovers it', out.getvalue())
        self.assertNotIn('the next apply or rollback removes it', out.getvalue())
        try:
            _, notes, blocker = cli._check_inputs(self.inst, self.state)
        except (StateError, Blocked):
            pass
        else:
            self.assertTrue(blocker)
            self.assertFalse(notes)
        self.assertEqual(protected_state(self.root), before)
        with self.assertRaises((StateError, Blocked)):
            self.updater().recover()
        self.assertEqual(protected_state(self.root), before)

    def test_unknown_and_malformed_headers_are_not_recovery_promises(self):
        self.interrupt_apply()
        valid = self.journal()
        for key, values in {'journal_version': (99, True, '2', None),
                            'kind': ('future-operation', None, []),
                            'stage': ('future-stage', None, {}),
                            'production': ('false', 0),
                            'project': ('another-project', []),
                            'env_file': ('another.env', []),
                            'mode': ('production', [])}.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assert_refused(dict(valid, **{key: value}))

    def test_matching_completed_receipt_cannot_validate_an_unknown_header(self):
        self.interrupt_apply()
        journal = dict(self.journal(), stage='rollout')
        self.state.write_receipt(dict(journal, status='succeeded', finished_at='2026-10-20T00:00:00Z'))
        for key, value in (('journal_version', 99), ('kind', 'future-operation'), ('stage', 'future-stage'),
                           ('project', 'another-project')):
            record = dict(journal, **{key: value})
            # Even a receipt matching the corrupted header must not cure it.
            self.state.write_receipt(dict(record, status='succeeded', finished_at='2026-10-21T00:00:00Z'))
            with self.subTest(key=key):
                self.assert_refused(record)

    def test_foreign_journal_cannot_match_an_identityless_completed_receipt(self):
        self.interrupt_apply()
        journal = dict(self.journal(), env_file='another.env')
        receipt = dict(journal, status='succeeded', finished_at='2026-10-20T00:00:00Z')
        for key in ('env_file', 'production', 'mode', 'project'):
            receipt.pop(key, None)
        self.state.write_receipt(receipt)
        self.assert_refused(journal)

    def test_version_two_requires_private_reference_even_with_matching_receipt(self):
        self.interrupt_apply()
        valid = self.journal()
        for recovery in (None, {}, dict(valid['env_recovery'], commit=None)):
            for completed in (False, True):
                with self.subTest(recovery=recovery, completed=completed):
                    record = dict(valid, env_recovery=recovery)
                    if completed:
                        self.state.write_receipt(dict(record, status='succeeded', finished_at='2026-10-21T00:00:00Z'))
                    self.assert_refused(record)

    def test_matching_receipt_cannot_clear_complete_but_unexchanged_inverse(self):
        self.interrupt_apply()
        real = envcommit.exchange

        def stop(a, b, c, d):
            if b == 'slot':
                raise SystemExit(137)
            return real(a, b, c, d)

        with mock.patch.object(envcommit, 'exchange', side_effect=stop):
            with self.assertRaises(SystemExit):
                self.updater().recover()
        journal = self.journal()
        recovery = journal['env_recovery']
        operation = envcommit.directory(Path(recovery['target']), recovery['commit']['restore'])
        self.assertEqual(json.loads((operation / 'manifest.json').read_text())['phase'], 'armed')
        self.state.write_receipt(dict(journal, status='failed_before_rollout', finished_at='2026-10-21T00:00:00Z'))
        before = protected_state(self.root)
        with self.assertRaisesRegex(StateError, 'unfinished inverse'):
            preview_recovery(self.inst, journal, self.state.history(self.inst.identity()))
        self.assertEqual(protected_state(self.root), before)
        self.assert_refused(journal)

    def test_malformed_retained_metadata_blocks_preview_without_exposing_private_data(self):
        self.interrupt_apply()
        journal = self.journal()
        recovery = journal['env_recovery']
        operation = envcommit.directory(Path(recovery['target']), recovery['commit']['forward'])
        manifest = operation / 'manifest.json'
        valid = json.loads(manifest.read_text())
        for body in ([{'private': 'retention-preview-secret'}], dict(valid, candidate={}),
                     dict(valid, parent=[]), dict(valid, phase='future-phase')):
            with self.subTest(body=body):
                manifest.write_text(json.dumps(body))
                self.assert_refused(journal)
                self.assertNotIn('retention-preview-secret', self.out.getvalue())
                with self.assertRaises(StateError) as caught:
                    preview_recovery(self.inst, journal, self.state.history(self.inst.identity()))
                self.assertNotIn('retention-preview-secret', str(caught.exception))
        self.assertFalse(self.receipts())

    def test_recognized_completed_receipt_control(self):
        self.interrupt_apply()
        journal = dict(self.journal(), stage='rollout')
        self.state.write_journal(journal)
        self.state.write_receipt(dict(journal, status='succeeded', finished_at='2026-10-20T00:00:00Z'))
        before = protected_state(self.root)
        record, notes = preview_recovery(self.inst, journal, self.state.history(self.inst.identity()))
        self.assertEqual(record['status'], 'succeeded')
        self.assertIn('removes it', ' '.join(notes))
        self.assertEqual(protected_state(self.root), before)
        self.assertIsNone(self.updater().recover())
        self.assertIsNone(self.journal())


def create_fixture(base):
    root = base / 'checkout'
    (root / 'deploy').mkdir(parents=True)
    compose = 'name: fagi-prod\nservices:\n' + ''.join(
        '  ' + name + ':\n    image: ' + image + '\n' for name, image in fixtures.FakeEngine.SERVICES.items())
    (root / fixtures.DISTRIBUTED_FILE).write_text(compose)
    (root / fixtures.PRODUCTION_OVERLAY).write_text('services: {}\n')
    original = (b'FUTURE_AGI_VERSION=v1.0.0\nFRONTEND_VERSION=v1.0.0\n'
                b'PG_PASSWORD=synthetic-preview-test\nPRESERVE=no-final-newline')
    (root / fixtures.PRODUCTION_ENV).write_bytes(original)
    components = {}
    for name, repo in fixtures.COMPONENT_REPOSITORIES.items():
        ref = repo + ':' + NEW + fixtures.COMPONENT_TAG_SUFFIX.get(name, '')
        components[name] = {'image': ref, 'digest': fixtures.registry_digest(ref)}
    manifest = {
        'schema_version': 1, 'product': fixtures.PRODUCT, 'channel': 'stable', 'version': NEW,
        'source_sha': 'a' * 40, 'published_at': '2026-10-19T00:00:00Z', 'minimum_version': OLD,
        'signature': {'bundle': 'future-agi-update-manifest.json.sigstore.json'}, 'components': components,
        'helm': {'chart': 'oci://ghcr.io/future-agi/charts/future-agi', 'version': NEW[1:],
                 'digest': 'sha256:' + 'b' * 64},
        'deployment': {'files': {name: fixtures.hash_path(root, name) for name in (
            fixtures.DISTRIBUTED_FILE, fixtures.PRODUCTION_OVERLAY)}},
        'migration': {'requires_bootstrap': True, 'rollback': 'manual'},
    }
    release = base / 'synthetic-manifest.json'
    release.write_text(json.dumps(manifest))
    (root / 'update.conf').write_text('FUTURE_AGI_UPDATE_MANIFEST_URL=' + str(release) + '\n')
    return root, original


def child(base, action):
    root = base / 'checkout'
    engine = fixtures.FakeEngine()
    engine.owner_labels = {
        'com.docker.compose.project.working_dir': str(root),
        'com.docker.compose.project.config_files': ','.join(str(root / name) for name in (
            fixtures.DISTRIBUTED_FILE, fixtures.PRODUCTION_OVERLAY)),
        'com.docker.compose.project.environment_file': str(root / fixtures.PRODUCTION_ENV),
    }
    for version in (OLD, NEW):
        for repo in ('futureagi/future-agi', 'futureagi/frontend'):
            engine.publish(repo + ':' + version, version)
    engine.publish('postgres:16', '')
    for name, template in engine.services.items():
        ref = template.replace('${FUTURE_AGI_VERSION}', OLD).replace('${FRONTEND_VERSION}', OLD)
        engine.pull(ref)
        engine.start(name, ref)

    def runner(argv, **kwargs):
        with (base / (action + '.calls.jsonl')).open('a') as stream:
            stream.write(json.dumps(list(argv)) + '\n')
        return engine(argv, **kwargs)

    # Prevent accidental real Docker/network activity in a daemon-free CLI test.
    def guard(event, args):
        if event == 'socket.connect':
            raise AssertionError('unexpected network access')
        if event == 'subprocess.Popen' and Path(os.fsdecode(args[0])).name != 'git':
            raise AssertionError('unexpected external process')
    sys.addaudithook(guard)
    arguments = {'seed': ['apply', '--stage', '--allow-no-backup'], 'preparing': ['rollback'],
                 'armed': ['rollback'], 'check': ['check'], 'json': ['check', '--format', 'json'],
                 'dry-run': ['apply', '--dry-run', '--stage', '--allow-no-backup'],
                 'status': ['status'], 'recover': ['rollback']}[action]
    real_json = envcommit._json

    def publication(fd, name, body):
        real_json(fd, name, body)
        direction = 'forward' if action == 'seed' else 'restore'
        phase = {'seed': 'accepted', 'preparing': 'preparing', 'armed': 'armed'}.get(action)
        if phase and body.get('phase') == phase:
            journal = StateDir(root).read_journal()
            if body.get('id') == journal['env_recovery']['commit'][direction]:
                os.kill(os.getpid(), signal.SIGKILL)  # only this test child, after a real durable publication
                raise AssertionError('SIGKILL did not terminate child')

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, fixtures.clean_environ(), clear=True))
        stack.enter_context(mock.patch.object(cli, 'Installation', side_effect=lambda root, **kwargs:
            fixtures.Installation(root, runner=runner, compose=['docker', 'compose'], **kwargs)))
        verifier = stack.enter_context(mock.patch.object(cli, 'Verifier'))
        verifier.return_value.verify.return_value = fixtures.Verification(
            True, method='keyless', signer='synthetic-test', tool='test-stub',
            issuer='https://token.actions.githubusercontent.com')
        stack.enter_context(mock.patch.object(envcommit, '_json', side_effect=publication))
        return cli.main(['--root', str(root), '--cwd', str(root), '--production', *arguments])


def invoke(base, action):
    result = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                             '--child', str(base), action], capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout + result.stderr


class RecoveryPreviewProcessTests(unittest.TestCase):
    def test_unknown_and_foreign_headers_block_real_cli_dispatch(self):
        with tempfile.TemporaryDirectory(prefix='preview-header-') as directory:
            base = Path(directory).resolve()
            root, _ = create_fixture(base)
            self.assertEqual(invoke(base, 'seed')[0], -signal.SIGKILL)
            state = StateDir(root)
            valid = state.read_journal()
            for field, value, problem in (('journal_version', 99, 'invalid or unknown'),
                                          ('kind', 'future-operation', 'invalid or unknown'),
                                          ('stage', 'future-stage', 'invalid or unknown'),
                                          ('project', 'another-project', 'original project selection')):
                state.write_journal(dict(valid, **{field: value}))
                before = protected_state(root)
                for action in ('check', 'json', 'dry-run', 'status', 'recover'):
                    with self.subTest(field=field, action=action):
                        code, output = invoke(base, action)
                        self.assertEqual(code, 3 if field == 'project' and action != 'status' else 2, output)
                        self.assertIn(problem, output)
                        self.assertNotIn('will be recovered', output)
                        self.assertNotIn('initialization jobs', output)
                        self.assertEqual(protected_state(root), before)

    def test_completed_receipt_does_not_cure_unknown_cli_header(self):
        with tempfile.TemporaryDirectory(prefix='preview-receipt-') as directory:
            base = Path(directory).resolve()
            root, _ = create_fixture(base)
            self.assertEqual(invoke(base, 'seed')[0], -signal.SIGKILL)
            state = StateDir(root)
            journal = dict(state.read_journal(), journal_version=99, stage='rollout')
            state.write_journal(journal)
            state.write_receipt(dict(journal, status='succeeded', finished_at='2026-10-21T00:00:00Z'))
            before = protected_state(root)
            for action in ('check', 'json', 'dry-run', 'status', 'recover'):
                with self.subTest(action=action):
                    code, output = invoke(base, action)
                    self.assertEqual(code, 2, output)
                    self.assertIn('invalid or unknown', output)
                    self.assertNotIn('removes it', output)
                    self.assertNotIn('initialization jobs', output)
                    self.assertEqual(protected_state(root), before)

    def exercise(self, inverse=None):
        with tempfile.TemporaryDirectory(prefix='preview-kill-') as directory:
            base = Path(directory).resolve()
            root, original = create_fixture(base)

            self.assertEqual(invoke(base, 'seed')[0], -signal.SIGKILL)
            if inverse:
                self.assertEqual(invoke(base, inverse)[0], -signal.SIGKILL)
                recovery = StateDir(root).read_journal()['env_recovery']
                op = envcommit.directory(Path(recovery['target']), recovery['commit']['restore'])
                self.assertEqual(json.loads((op / 'manifest.json').read_text())['phase'], inverse)
                self.assertEqual((op / 'candidate').exists(), inverse == 'armed')
            before = protected_state(root)
            expected = 2 if inverse == 'preparing' else 0
            for action in ('check', 'json', 'dry-run', 'status'):
                with self.subTest(action=action, inverse=inverse):
                    code, output = invoke(base, action)
                    self.assertEqual(code, expected, output)
                    if expected:
                        self.assertIn('incomplete env preparation', output)
                        self.assertNotIn('will be recovered', output)
                        self.assertNotIn('initialization jobs', output)
                    self.assertEqual(protected_state(root), before)
                    calls = [json.loads(line) for line in (base / (action + '.calls.jsonl')).read_text().splitlines()]
                    self.assertFalse(any(word in call for call in calls for word in ('pull', 'up', 'run', 'down')))
            code, output = invoke(base, 'recover')
            self.assertEqual(code, expected, output)
            if expected:
                self.assertEqual(protected_state(root), before)
                self.assertFalse(StateDir(root).receipts())
            else:
                self.assertEqual((root / fixtures.PRODUCTION_ENV).read_bytes(), original)
                self.assertIsNone(StateDir(root).read_journal())
                self.assertEqual(len(StateDir(root).receipts()), 1)

    def test_sigkill_accepted_forward_previews_and_recovers(self):
        self.exercise()

    def test_sigkill_armed_inverse_previews_and_recovers(self):
        self.exercise('armed')

    def test_sigkill_incomplete_inverse_blocks_every_preview(self):
        self.exercise('preparing')


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--child':
        sys.exit(child(Path(sys.argv[2]), sys.argv[3]))
    unittest.main()
