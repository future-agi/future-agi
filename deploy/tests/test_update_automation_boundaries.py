"""Scheduled engine entry points must not recover or mutate on a refused guard."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests.test_update_apply import UpdaterCase, BACKUP  # noqa: E402
from deploy.update.apply import Updater, FailedBeforeRollout  # noqa: E402
from deploy.update.contracts import ReasonCode  # noqa: E402
from deploy.update.settings import load_settings  # noqa: E402


class GuardBoundaryTests(UpdaterCase):
    def guarded(self, reject=None):
        from deploy.update.orchestrator import GuardRefused
        self.boundaries = []
        owner = self

        class Guard:
            def validate(self, boundary, context):
                owner.boundaries.append(boundary)
                owner.assertEqual(context, 'locked')
                # A second lock cannot be taken while a guard is running.
                owner.assertIsNotNone(owner.state.lock_holder())
                if boundary == reject:
                    raise GuardRefused(ReasonCode.OUTSIDE_WINDOW)

        return Updater(self.inst, load_settings(self.root, environ={}), self.state,
                       out=self.out, readiness=self.ready, hook_runner=self.hook,
                       automation_guard=Guard(), guard_context=lambda engine: 'locked')

    def test_pre_recovery_guard_precedes_recovery_and_backup(self):
        from deploy.update.orchestrator import GuardRefused
        updater = self.guarded('before_recovery')
        with mock.patch.object(updater, '_recover_locked') as recover:
            with self.assertRaises(GuardRefused):
                self.apply(updater=updater)
        recover.assert_not_called()
        self.assertEqual(self.hook.calls, [])
        self.assertEqual(self.env_bytes(), self.original)

    def test_forward_boundaries_are_under_lock_and_ordered(self):
        self.apply(updater=self.guarded())
        self.assertEqual(self.boundaries, ['before_recovery', 'before_backup', 'after_backup',
                                          'before_env_commit', 'before_rollout'])

    def test_cancel_before_rollout_restores_exact_env(self):
        with self.assertRaises(FailedBeforeRollout):
            self.apply(updater=self.guarded('before_rollout'))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertFalse(any(call[0] == 'up' for call in self.compose_calls()))
        self.assertIsNone(self.state.read_journal())

    def test_cancel_after_backup_has_no_journal_or_env_write(self):
        from deploy.update.orchestrator import GuardRefused
        with self.assertRaises(GuardRefused):
            self.apply(updater=self.guarded('after_backup'))
        self.assertEqual(len(self.hook.calls), 1)
        self.assertEqual(self.env_write_count, 0)
        self.assertIsNone(self.state.read_journal())

    def test_automatic_production_cannot_stage_even_with_allowing_guard(self):
        from deploy.update.orchestrator import GuardRefused
        self.build(mode='production')
        updater = self.guarded()
        with self.assertRaises(GuardRefused) as caught:
            updater.apply(lambda _: self.plan(), {'version': 'v1.1.0'}, stage=True, backup_command=BACKUP)
        self.assertEqual(caught.exception.reason_code, ReasonCode.INITIALIZATION_REQUIRED)
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.hook.calls, [])

    def test_automatic_recover_refuses_journal_without_modifying_it(self):
        from deploy.update.orchestrator import GuardRefused
        self.state.write_journal({'stage': 'prepare', 'kind': 'apply'})
        raw = self.state.journal_path.read_bytes()
        with self.assertRaises(GuardRefused) as caught:
            self.guarded().recover()
        self.assertEqual(caught.exception.reason_code, ReasonCode.RECOVERY_REQUIRED)
        self.assertEqual(self.state.journal_path.read_bytes(), raw)

    def test_guard_must_have_a_context_provider(self):
        with self.assertRaises(ValueError):
            Updater(self.inst, load_settings(self.root, environ={}), self.state,
                    automation_guard=object())


class StageAndOverlayTests(UpdaterCase):
    def test_manual_default_cannot_construct_scheduled_apply_adapter(self):
        from deploy.update.orchestrator import guarded_updater, GuardRefused
        from deploy.update.processes import ProcessSupervisor
        settings = load_settings(self.root, environ={})
        supervisor = ProcessSupervisor(settings.automation_policy, docker_context='synthetic-context', environment={})
        self.inst.runner = supervisor
        with self.assertRaises(GuardRefused) as caught:
            guarded_updater(self.inst, settings, self.state, object(), supervisor)
        self.assertEqual(caught.exception.reason_code, ReasonCode.CONTROL_INVALID)
        self.assertEqual(self.log, [])

    def test_registered_installation_preserves_supported_standalone_compose(self):
        from dataclasses import replace
        from deploy.tests.test_update_compatibility import prepare, default_v2
        from deploy.update.install import ComposeCommand
        from deploy.update.orchestrator import registered_installation
        from deploy.update.processes import ProcessSupervisor
        settings = load_settings(self.root, environ={})
        supervisor = ProcessSupervisor(settings.automation_policy, docker_context='registered',
                                       environment={'PATH': '/registered'})
        binding = replace(prepare(default_v2()).binding, checkout=self.root, env_alias=self.env_path,
                          env_target=self.env_path, docker_context='registered')
        command = ComposeCommand(['/registered/docker-compose'], (2, 24, 7))
        with mock.patch('shutil.which', return_value='/registered/docker') as which, \
                mock.patch('deploy.update.orchestrator.resolve_compose_command', return_value=command), \
                mock.patch('deploy.update.orchestrator.check_binding'):
            install = registered_installation(binding, supervisor)
        which.assert_called_once_with('docker', path='/registered')
        self.assertEqual(install.compose, ['/registered/docker-compose'])
        self.assertEqual(install.compose_version, (2, 24, 7))
        self.assertEqual(install.docker, ['/registered/docker', '--context', 'registered'])
        self.assertEqual(install.environ['DOCKER_CONTEXT'], 'registered')

    def test_registered_compose_plugin_gets_the_explicit_context(self):
        from dataclasses import replace
        from deploy.tests.test_update_compatibility import prepare, default_v2
        from deploy.update.install import ComposeCommand
        from deploy.update.orchestrator import registered_installation
        from deploy.update.processes import ProcessSupervisor
        supervisor = ProcessSupervisor(load_settings(self.root, environ={}).automation_policy,
                                       docker_context='registered', environment={'PATH': '/registered'})
        binding = replace(prepare(default_v2()).binding, checkout=self.root, env_alias=self.env_path,
                          env_target=self.env_path, docker_context='registered')
        with mock.patch('shutil.which', return_value='/registered/docker'), \
                mock.patch('deploy.update.orchestrator.resolve_compose_command',
                           return_value=ComposeCommand(['docker', 'compose'], (2, 24, 7))), \
                mock.patch('deploy.update.orchestrator.check_binding'):
            install = registered_installation(binding, supervisor)
        self.assertEqual(install.compose, ['/registered/docker', '--context', 'registered', 'compose'])
        self.assertEqual(install.compose_version, (2, 24, 7))

    def test_signed_deployment_hash_drift_is_a_typed_refusal(self):
        from deploy.update.orchestrator import check_deployment, GuardRefused
        from deploy.update.manifest import hash_path
        file = self.root / 'docker-compose.yml'
        file.write_text('services: {}\n')
        manifest = {'deployment': {'files': {'docker-compose.yml': hash_path(self.root, 'docker-compose.yml')}}}
        check_deployment(self.inst, manifest)
        file.write_text('services: changed\n')
        with self.assertRaises(GuardRefused) as caught:
            check_deployment(self.inst, manifest)
        self.assertEqual(caught.exception.reason_code, ReasonCode.DEPLOYMENT_FILES_MISMATCH)

    def test_scheduled_lock_rejects_symlinks_without_chmod_or_following(self):
        from deploy.update.receipt import StateError
        target = self.root / 'unrelated'
        target.mkdir(mode=0o755)
        before = target.stat().st_mode
        self.state.path.symlink_to(target, target_is_directory=True)
        with self.assertRaises(StateError), self.state.lock(automation=True):
            self.fail('untrusted state lock was acquired')
        self.assertEqual(target.stat().st_mode, before)
        self.assertEqual(list(target.iterdir()), [])

    def test_private_evidence_rejects_symlinks_hardlinks_and_truncated_json(self):
        from deploy.update.receipt import StateError
        import os
        self.state.ensure()
        key = 'e'*64
        path = self.state.path / ('download-' + key + '.json')
        target = self.root / 'foreign'
        target.write_text('{}')
        path.symlink_to(target)
        with self.assertRaises(StateError):
            self.state.read_private('download', key)
        path.unlink()
        target.chmod(0o600)
        os.link(target, path)
        with self.assertRaises(StateError):
            self.state.read_private('download', key)
        path.unlink()
        path.write_text('{')
        path.chmod(0o600)
        with self.assertRaises(StateError):
            self.state.read_private('download', key)

    def test_production_third_file_is_identical_in_args_hint_and_ownership(self):
        from deploy.update.install import Installation
        self.build(mode='production')
        for name in ('docker-compose.distributed.yml', 'deploy/docker-compose.production.yml', 'deploy/status.yml'):
            (self.root / name).write_text('services: {}\n')
        overlay = self.root / 'deploy/status.yml'
        install = Installation(self.root, production=True, compose=['docker', 'compose'], environ={},
                               status_overlay=overlay)
        self.assertEqual(install.compose_args('config')[-3:], ['-f', str(overlay), 'config'])
        self.assertEqual(install._ownership_files()[-1], overlay)
        self.assertIn('-f deploy/status.yml', install.compose_hint())
        self.assertEqual(install.fresh().status_overlay, overlay)

    def test_new_stage_binding_requires_ack_for_absent_proof_and_never_allows_changed_pair(self):
        from deploy.update.orchestrator import GuardRefused
        binding = {key: 'a'*64 for key in ('manifest_hash', 'manifest_sha256', 'bundle_sha256',
                   'installation_fingerprint', 'config_fingerprint', 'profile_fingerprint', 'trust_fingerprint')}
        updater = self.updater()
        path = updater.apply(lambda _: self.plan(), {'version': 'v1.1.0'}, stage=True,
                             backup_command=BACKUP, release_binding=binding)
        record = self.state.load_receipt(path)
        self.assertNotIn('stage_binding', record)
        with self.assertRaises(GuardRefused) as caught:
            updater._check_stage_binding(record, binding, False)
        self.assertEqual(caught.exception.reason_code, ReasonCode.STALE_STAGE)
        updater._check_stage_binding(record, binding, True)
        with self.assertRaises(GuardRefused) as caught:
            updater._check_stage_binding(record, dict(binding, manifest_sha256='b'*64), True)
        self.assertEqual(caught.exception.reason_code, ReasonCode.IMMUTABLE_RELEASE_CHANGED)

    def test_empty_release_binding_writes_no_stage_metadata_for_an_ordinary_apply(self):
        updater = self.updater()
        path = updater.apply(lambda _: self.plan(), {'version': 'v1.1.0'}, backup_command=BACKUP, release_binding={})
        record = self.state.load_receipt(path)
        self.assertNotIn('stage_binding', record)
        self.assertIsNone(self.state.read_private('stage', record['run_id']))

    def test_non_empty_release_binding_still_records_private_stage_metadata(self):
        binding = {key: 'a'*64 for key in ('manifest_hash', 'manifest_sha256', 'bundle_sha256',
                   'installation_fingerprint', 'config_fingerprint', 'profile_fingerprint', 'trust_fingerprint')}
        updater = self.updater()
        path = updater.apply(lambda _: self.plan(), {'version': 'v1.1.0'}, stage=True,
                             backup_command=BACKUP, release_binding=binding)
        saved = self.state.read_private('stage', self.state.load_receipt(path)['run_id'])
        self.assertEqual(saved['backup_proof_state'], 'legacy_absent')
        self.assertEqual(saved['manifest_sha256'], binding['manifest_sha256'])


class ManualRunsKeepTheirBehaviourTests(UpdaterCase):
    """Review round 2: the env binding and the dependency certificate exist only for automatic runs."""

    def test_manual_engine_has_no_approved_env_and_no_dependency_certificate(self):
        updater = self.updater()
        self.assertIsNone(updater._approved_env_sha256())
        with mock.patch.object(self.inst, 'containers', side_effect=AssertionError('no inspection')):
            updater._dependencies_unchanged()

    def test_manual_apply_still_starts_whatever_a_dependency_tag_resolves_to(self):
        self.inst.local_images['postgres:16'] = 'sha256:' + '9' * 64  # moved tag: a manual run is not refused
        self.inst.metadata['sha256:' + '9' * 64] = {'repo_digests': ['postgres@sha256:' + '9' * 64], 'version': ''}
        path = self.apply()
        self.assertEqual(self.state.load_receipt(path)['status'], 'succeeded')
        self.assertTrue(any(call[0] == 'up' for call in self.compose_calls()))


class EnvDigestTests(unittest.TestCase):
    """Review round 2: the digest the guard binds a transaction to is of the bytes the engine would write."""

    def test_digest_of_the_selected_alias_and_typed_refusals_for_unreadable_or_retargeted_env(self):
        import hashlib
        import tempfile
        from types import SimpleNamespace
        from deploy.update.envfile import EnvCommitError
        from deploy.update.orchestrator import GuardRefused, env_sha256
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_bytes(b'FUTURE_AGI_VERSION=v1.0.0\n\xff')
            self.assertEqual(env_sha256(SimpleNamespace(_env_path=lambda: path)), hashlib.sha256(path.read_bytes()).hexdigest())
            def retargeted():
                raise EnvCommitError('the selected env target changed')
            for engine in (SimpleNamespace(_env_path=lambda: Path(directory) / 'missing.env'),
                           SimpleNamespace(_env_path=lambda: Path(directory)),
                           SimpleNamespace(_env_path=retargeted)):
                with self.subTest(engine=engine), self.assertRaises(GuardRefused) as caught:
                    env_sha256(engine)
                self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)


class OrchestratorHelperTests(unittest.TestCase):
    """Review round 2 (coverage): the small host helpers the scheduled engine adapter is built from."""

    def test_fingerprint_without_update_conf_differs_from_the_one_with_it(self):
        import tempfile
        from types import SimpleNamespace
        from deploy.update.orchestrator import fingerprints
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            installation = SimpleNamespace(root=root, env={'A': '1'}, environ={}, _ownership_files=lambda: [],
                                           config=lambda refresh=False: {'services': {}})
            without = fingerprints(installation)
            (root / 'update.conf').write_text('FUTURE_AGI_UPDATE_AUTOMATION_MODE=apply\n')
            with_file = fingerprints(installation)
        self.assertNotEqual(without[0], with_file[0])
        self.assertEqual(without[1:], with_file[1:])

    def test_malformed_manifest_or_missing_deployment_file_is_a_typed_mismatch(self):
        import tempfile
        from types import SimpleNamespace
        from deploy.update.orchestrator import GuardRefused, check_deployment
        with tempfile.TemporaryDirectory() as directory:
            installation = SimpleNamespace(root=Path(directory).resolve())
            for manifest in ({}, {'deployment': {}}, {'deployment': {'files': {}}}, {'deployment': {'files': None}},
                             {'deployment': {'files': {'missing.yml': 'a' * 64}}}):
                with self.subTest(manifest=manifest), self.assertRaises(GuardRefused) as caught:
                    check_deployment(installation, manifest)
                self.assertEqual(caught.exception.reason_code, ReasonCode.DEPLOYMENT_FILES_MISMATCH)


class SupervisedReadinessTests(unittest.TestCase):
    """Review round 2 (coverage): readiness waits are bounded by the supervisor's deadline and the wait budget."""

    def readiness(self, deadline_in=100.0, wait_timeout=50):
        import time
        from types import SimpleNamespace
        from deploy.update.orchestrator import SupervisedReadiness
        supervisor = SimpleNamespace(deadline=time.monotonic() + deadline_in,
                                     policy=SimpleNamespace(wait_timeout_seconds=wait_timeout))
        seen = {}

        class Recorder:
            def __init__(self, installation, out, *, probe, clock, sleep):
                seen.update(probe=probe, clock=clock, sleep=sleep)

            def wait_standalone(self, previous_ids=()):
                seen['standalone'] = list(previous_ids)
                return 'standalone-done'

            def wait_api(self):
                seen['api'] = True
                return 'api-done'

        return SupervisedReadiness('installation', supervisor, 'out'), seen, Recorder

    def test_waits_delegate_to_the_core_readiness_with_bounded_clock_sleep_and_probe(self):
        from deploy.update.readiness import NotReady
        readiness, seen, recorder = self.readiness()
        with mock.patch('deploy.update.readiness.Readiness', recorder), \
                mock.patch('deploy.update.readiness.http_ok', return_value=True) as http_ok, \
                mock.patch('time.sleep') as sleep:
            self.assertEqual(readiness.wait_standalone(previous_ids=['a', 'b']), 'standalone-done')
            self.assertEqual(readiness.wait_api(), 'api-done')
            self.assertEqual(seen['standalone'], ['a', 'b'])
            self.assertIsInstance(seen['clock'](), float)
            seen['sleep'](1000)  # never longer than what is left of the budget
            self.assertLessEqual(sleep.call_args.args[0], 50)
            self.assertTrue(seen['probe']('http://127.0.0.1:1/'))
            self.assertLessEqual(http_ok.call_args.kwargs['timeout'], 3.0)
        readiness, seen, recorder = self.readiness(deadline_in=-1)
        with mock.patch('deploy.update.readiness.Readiness', recorder):
            readiness.wait_api()
            with self.assertRaises(NotReady):
                seen['clock']()


class ExecuteGuardedStubTests(unittest.TestCase):
    """Review round 2 (coverage): the adapter's outcome mapping, with a scripted engine and guard."""

    def run_with(self, apply, *, attempt=True, finish=None, uncertain=False):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from deploy.update.orchestrator import _execute_guarded
        store = SimpleNamespace(finish_attempt=finish or mock.Mock())
        guard = SimpleNamespace(attempt=SimpleNamespace(attempt_id='a' * 32, outcome='', finished_at=None) if attempt else None,
                                proof=None, first_mutation_at=None, fallback_reserved=False, store=store,
                                clock=lambda: datetime(2026, 10, 24, tzinfo=timezone.utc), make_plan=mock.Mock(),
                                prepared=SimpleNamespace(manifest={}), close=mock.Mock())
        engine = SimpleNamespace(automation_guard=guard, automatic_fallback=False, active_record={},
                                 installation=SimpleNamespace(runner=SimpleNamespace(uncertain=uncertain)),
                                 apply=apply)
        with mock.patch('deploy.update.orchestrator.replace', side_effect=lambda value, **changes: value):
            return _execute_guarded(engine, guard), guard

    def test_nothing_to_do_busy_and_unclassified_causes(self):
        from deploy.update.receipt import StateBusy
        result, guard = self.run_with(lambda *args, **kwargs: None, attempt=False)
        self.assertEqual((result.code, result.forward_result), (0, 'current'))
        guard.close.assert_called_once()
        result, _ = self.run_with(mock.Mock(side_effect=StateBusy('busy')), attempt=False)
        self.assertEqual((result.code, result.forward_result, result.transaction_started), (0, 'busy', False))

        class Odd(Exception):
            reason_code = 'not-a-reason'

        result, _ = self.run_with(mock.Mock(side_effect=Odd()), attempt=False)
        self.assertEqual((result.code, result.reason_codes), (2, (ReasonCode.PROCESS_FAILED,)))

    def test_process_errors_keep_their_typed_reason(self):
        from deploy.update.processes import ProcessError
        error = ProcessError(ReasonCode.OPERATION_TIMEOUT, uncertain=True)
        result, _ = self.run_with(mock.Mock(side_effect=error), attempt=False)
        self.assertEqual((result.code, result.forward_result, result.reason_codes),
                         (2, 'failed', (ReasonCode.OPERATION_TIMEOUT,)))
        result, _ = self.run_with(mock.Mock(side_effect=ProcessError(ReasonCode.PROCESS_FAILED)), attempt=False)
        self.assertEqual(result.reason_codes, (ReasonCode.PROCESS_FAILED,))

    def test_a_terminal_state_that_cannot_be_persisted_leaves_recovery_required(self):
        result, _ = self.run_with(lambda *args, **kwargs: None, finish=mock.Mock(side_effect=OSError('/private PASSWORD=x')))
        self.assertEqual((result.code, result.reason_codes, result.forward_result),
                         (2, (ReasonCode.RECOVERY_REQUIRED,), 'current'))
        self.assertNotIn('PASSWORD', repr(result))
        finish = mock.Mock()
        result, guard = self.run_with(lambda *args, **kwargs: None, finish=finish)
        finish.assert_called_once()
        self.assertEqual((result.code, result.reason_codes), (0, ()))


class OrchestratorHistoryTests(UpdaterCase):
    def test_an_unfinished_partial_rollout_receipt_refuses_a_new_scheduled_start(self):
        from deploy.update.orchestrator import GuardRefused
        guard_case = ScheduledGuardTests('test_exact_pair_is_reverified_again_at_first_mutation_boundary')
        guard_case.setUp()
        self.addCleanup(guard_case.doCleanups)
        self.addCleanup(guard_case.tearDown)
        guard_case.craft_apply(status='failed_during_rollout', target='v1.52.0')
        with self.assertRaises(GuardRefused) as caught:
            guard_case.guard._pending(guard_case.engine)
        self.assertEqual(caught.exception.reason_code, ReasonCode.RECOVERY_REQUIRED)


class DependencyImageCheckTests(unittest.TestCase):
    """Review round 2: check_dependency_images against a stand-in for the Compose model and the local image store."""

    LAYOUT = None

    def setUp(self):
        from deploy.update.compatibility import RuntimeImage, topology_profile
        self.layout = topology_profile('standalone', ())
        self.digest = {'postgres': 'sha256:' + 'a' * 64, 'clickhouse': 'sha256:' + 'b' * 64}
        self.ids = {'postgres': 'sha256:' + '1' * 64, 'clickhouse': 'sha256:' + '2' * 64}
        self.references = {'postgres': 'postgres:16', 'clickhouse': 'clickhouse/clickhouse-server:24.8'}
        self.certified = {name: (RuntimeImage(self.references[name], self.digest[name], self.ids[name]),)
                          for name in self.references}
        self.services = {name: {'image': ref} for name, ref in self.references.items()}
        self.local = {ref: {'id': self.ids[name], 'repo_digests': [
            ref.rsplit(':', 1)[0] + '@' + self.digest[name]]} for name, ref in self.references.items()}
        self.installation = mock.Mock()
        self.installation.config.side_effect = lambda refresh=False: {'services': self.services}
        self.installation.image.side_effect = lambda ref: self.local.get(ref)

    def check(self):
        from deploy.update.orchestrator import check_dependency_images
        return check_dependency_images(self.installation, self.layout, self.certified)

    def refused(self, reason):
        from deploy.update.orchestrator import GuardRefused
        with self.assertRaises(GuardRefused) as caught:
            self.check()
        self.assertEqual(caught.exception.reason_code, reason)

    def test_matching_images_are_certified_per_service_and_the_model_is_refreshed(self):
        self.assertEqual(self.check(), {name: frozenset({self.ids[name]}) for name in self.ids})
        self.installation.config.assert_called_once_with(refresh=True)

    def test_every_mismatch_is_a_typed_refusal(self):
        from dataclasses import replace
        cases = {
            'moved tag (other id)': (lambda: self.local['postgres:16'].update(id='sha256:' + '9' * 64),
                                     ReasonCode.IMAGE_DIGEST_MISMATCH),
            'other digest': (lambda: self.local['postgres:16'].update(repo_digests=['postgres@sha256:' + '9' * 64]),
                             ReasonCode.IMAGE_DIGEST_MISMATCH),
            'no digests': (lambda: self.local['postgres:16'].update(repo_digests=[]), ReasonCode.IMAGE_DIGEST_MISMATCH),
            'digest of another repository': (lambda: self.local['postgres:16'].update(
                repo_digests=['other/postgres@' + self.digest['postgres']]), ReasonCode.IMAGE_DIGEST_MISMATCH),
            'not on this host': (lambda: self.local.pop('clickhouse/clickhouse-server:24.8'),
                                 ReasonCode.IMAGE_DIGEST_MISMATCH),
            'one of two replicas differs': (lambda: self.certified.update(postgres=(
                self.certified['postgres'][0], replace(self.certified['postgres'][0], image_id='sha256:' + '8' * 64))),
                ReasonCode.IMAGE_DIGEST_MISMATCH),
            'service absent from the model': (lambda: self.services.pop('postgres'), ReasonCode.CONFIGURATION_CHANGED),
            'service is built locally': (lambda: self.services['postgres'].update(build='.'),
                                         ReasonCode.CONFIGURATION_CHANGED),
            'service has no image': (lambda: self.services['postgres'].pop('image'), ReasonCode.CONFIGURATION_CHANGED),
            'service entry is not a mapping': (lambda: self.services.update(postgres='postgres:16'),
                                               ReasonCode.CONFIGURATION_CHANGED),
            'unparseable reference': (lambda: self.services['postgres'].update(image='Not A Reference'),
                                      ReasonCode.CONFIGURATION_CHANGED),
            'no certified replicas': (lambda: self.certified.pop('postgres'), ReasonCode.RUNNING_VERSIONS_UNKNOWN),
            'empty certified replicas': (lambda: self.certified.update(postgres=()),
                                         ReasonCode.RUNNING_VERSIONS_UNKNOWN),
        }
        for label, (change, reason) in cases.items():
            with self.subTest(label):
                self.setUp()
                change()
                self.refused(reason)

    def test_the_check_never_pulls_starts_or_stops_anything(self):
        self.check()
        self.assertEqual([call[0] for call in self.installation.method_calls], ['config', 'image', 'image'])


class ScheduledGuardTests(UpdaterCase):
    """Real guard/store/proof/engine with synthetic certification and fake Docker."""
    def setUp(self):
        super().setUp()
        import copy
        import os
        from dataclasses import replace
        from deploy.tests.test_update_compatibility import synthetic_context, NOW
        from deploy.update.discovery import DiscoveredRelease, DiscoveryResult
        from deploy.update.orchestrator import ScheduledGuard, fingerprints
        from deploy.update.runner_state import RunnerStateStore, Registration, LauncherConfig
        from deploy.update.envfile import read_env
        self.now = NOW
        prepared, self.obs, self.controls, _ = synthetic_context()
        self.build(current='v1.51.0')
        self.model_dependencies()
        (self.root / 'update.conf').write_text('FUTURE_AGI_UPDATE_AUTOMATION_MODE=apply\n'
                                               'FUTURE_AGI_UPDATE_ENABLED=true\n'
                                               'FUTURE_AGI_UPDATE_AUTOMATIC_ROLLBACK=true\n')
        self.inst.environ = {'DOCKER_CONTEXT': 'synthetic-context'}
        compose = self.root / 'docker-compose.yml'
        compose.write_text('services: {}\n')
        original = self.inst
        def fresh():
            clone = copy.copy(original)
            clone.env = read_env(self.env_path)
            clone.env_overlay = {}
            clone.resolved_images = lambda: {
                service: f'{repo}:{clone.env.get(variable) or default}'
                for service, repo, variable, default in clone.specs}
            return clone
        self.inst.fresh = fresh
        self.store = RunnerStateStore(self.root, self.env_path)
        binding = replace(prepared.binding, checkout=self.root, env_alias=self.env_path,
                          env_target=self.env_path, host_uid=os.geteuid(), compose_project=self.inst.project,
                          compose_files=(compose,), selection_hash=self.store.selection_hash)
        self.store.register(Registration(1, binding, LauncherConfig(Path(sys.executable), Path.home(),
                                                                   os.defpath, 'test'), NOW))
        plan = self.plan(target='v1.52.0')
        plan['manifest_hash'] = prepared.plan['manifest_hash']
        signed = prepared.manifest['components']['standalone']
        self.publish('futureagi/standalone', 'v1.52.0', 'v1.52.0', digest=signed['digest'])
        plan['components'][0]['target_digest'] = signed['digest']
        config, profile, deployment = fingerprints(self.inst)
        prepared = replace(prepared, binding=binding, plan=plan, config_fingerprint=config,
                           profile_fingerprint=profile, deployment_fingerprint=deployment)
        self.prepared, self.plan_value = prepared, plan
        self.release = DiscoveredRelease(prepared.raw_manifest, prepared.raw_bundle, plan['manifest_hash'],
                                         prepared.trust_fingerprint, prepared.verification)
        self.discovery = mock.Mock()
        self.discovery.refresh.return_value = DiscoveryResult(self.release, checked_at=NOW)
        self.guard = ScheduledGuard(prepared, discovery=self.discovery, store=self.store,
                                    facts=lambda installation, boundary: (self.obs, self.controls),
                                    make_plan=lambda installation, history: copy.deepcopy(plan),
                                    control_generation=0, window_occurrence='always-test', clock=lambda: self.now)
        self.addCleanup(self.guard.close)
        # Unit Docker provenance is intentionally synthetic, NOT an E2E pass.
        for name in ('check_binding', 'check_deployment'):
            patch = mock.patch('deploy.update.orchestrator.' + name)
            patch.start()
            self.addCleanup(patch.stop)
        def backup(argv, **kwargs):
            import json
            result = self.hook(argv, **kwargs)
            expectation = self.guard.expectation
            value = {'schema_version': 1, 'attempt_id': expectation.attempt_id,
                     'installation_fingerprint': expectation.installation_fingerprint,
                     'target_manifest_hash': expectation.target_manifest_hash,
                     'source_version': expectation.source_version, 'backup_id': 'test-backup',
                     'started_at': self.now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                     'completed_at': self.now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                     'covered_domains': list(expectation.covered_domains), 'verification': 'passed',
                     'restore_procedure_id': 'test-restore'}
            self.guard.proof_path.write_text(json.dumps(value))
            self.guard.proof_path.chmod(0o600)
            return result
        self.engine = Updater(self.inst, load_settings(self.root, environ={}), self.state,
                              out=self.out, readiness=self.ready, hook_runner=backup,
                              automation_guard=self.guard, guard_context=self.guard.context)

    def model_dependencies(self):
        """The fake Docker holds the certified dependency images, as the enrolled facts observe them on a real host.

        Each dependency of the layout is tagged locally, has one distinct image id and runs it, so the running
        replicas the facts report are exactly the images the Compose configuration resolves to.
        """
        import hashlib
        from dataclasses import replace
        from deploy.update.compatibility import RuntimeImage
        dependencies = {}
        self.inst.third_party = {}
        for service, images in sorted(self.obs.dependency_images.items()):
            certified = images[0]
            image_id = 'sha256:' + hashlib.sha256(('dependency ' + service).encode()).hexdigest()
            self.inst.third_party[service] = certified.image
            self.inst.local_images[certified.image] = image_id
            self.inst.metadata[image_id] = {'repo_digests': [certified.image.rsplit(':', 1)[0] + '@' + certified.digest],
                                            'version': ''}
            self.inst.running[service] = {'image': certified.image, 'image_id': image_id}
            dependencies[service] = (RuntimeImage(certified.image, certified.digest, image_id),)
        self.obs = replace(self.obs, dependency_images=dependencies)

    def run_guarded(self):
        import copy
        return self.engine.apply(lambda history: copy.deepcopy(self.plan_value), self.prepared.manifest,
                                 backup_command=BACKUP)

    def test_factory_uses_settings_property_and_supervises_both_docker_and_backup(self):
        from deploy.update.orchestrator import guarded_updater, SupervisedReadiness
        from deploy.update.processes import ProcessSupervisor
        policy = self.engine.settings.automation_policy
        supervisor = ProcessSupervisor(policy, docker_context='synthetic-context', environment={})
        self.inst.runner = supervisor
        engine = guarded_updater(self.inst, self.engine.settings, self.state, self.guard, supervisor)
        self.assertIs(engine.hook_runner, supervisor)
        self.assertIs(engine.installation.runner, supervisor)
        self.assertIsInstance(engine.readiness, SupervisedReadiness)
        self.assertIs(engine.automation_guard, self.guard)

    def test_update_configuration_change_during_backup_refuses(self):
        from deploy.update.orchestrator import GuardRefused
        backup = self.engine.hook_runner
        def change(argv, **kwargs):
            result = backup(argv, **kwargs)
            (self.root / 'update.conf').write_text('FUTURE_AGI_UPDATE_AUTOMATION_MODE=manual\n')
            return result
        self.engine.hook_runner = change
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.CONFIGURATION_CHANGED)
        self.assertEqual(self.env_write_count, 0)

    def test_manual_stage_binding_ignores_only_image_pins(self):
        from deploy.update.orchestrator import stage_binding
        before = stage_binding(self.prepared, self.inst)
        self.env_path.write_bytes(self.env_bytes().replace(b'v1.51.0', b'v1.52.0'))
        after = stage_binding(self.prepared, self.inst)
        self.assertEqual(before, after)
        with self.env_path.open('a') as handle:
            handle.write('\nOPERATOR_EDIT=changed\n')
        self.assertNotEqual(before['config_fingerprint'], stage_binding(self.prepared, self.inst)['config_fingerprint'])

    def test_certified_attempt_has_private_bound_proof_and_refreshes_each_boundary(self):
        path = self.run_guarded()
        receipt = self.state.load_receipt(path)
        self.assertEqual(receipt['status'], 'succeeded')
        self.assertNotIn('automation', receipt)
        private = self.state.read_private('automation', receipt['run_id'])
        self.assertEqual(private['attempt_id'], self.guard.attempt.attempt_id)
        self.assertEqual(private['backup_proof']['target_manifest_hash'], self.plan_value['manifest_hash'])
        self.assertEqual(self.discovery.refresh.call_count, 5)

    def test_foreign_manual_stage_is_never_adopted(self):
        from deploy.update.orchestrator import GuardRefused
        self.state.write_receipt({'kind': 'apply', 'status': 'staged', 'run_id': 'd'*32,
                                  'project': 'foreign', 'env_file': 'foreign.env', 'mode': 'production',
                                  'production': True, 'target_version': 'v1.52.0'})
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.MANUAL_STAGE_PRESENT)
        self.assertEqual(self.hook.calls, [])
        self.assertEqual(self.env_write_count, 0)
        self.assertEqual(len(self.state.receipts()), 1)

    def test_exact_pair_is_reverified_again_at_first_mutation_boundary(self):
        from dataclasses import replace
        from deploy.update.orchestrator import GuardRefused
        from deploy.update.discovery import DiscoveryResult
        valid = self.discovery.refresh.return_value
        changed = DiscoveryResult(replace(self.release, raw_bundle=b'changed-at-mutation'), checked_at=self.now)
        self.discovery.refresh.side_effect = [valid, valid, valid, changed]
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        self.assertEqual(len(self.hook.calls), 1)
        self.assertEqual(self.env_write_count, 0)
        self.assertIsNone(self.state.read_journal())

    def test_changed_bundle_refuses_before_backup(self):
        from dataclasses import replace
        from deploy.update.orchestrator import GuardRefused
        from deploy.update.discovery import DiscoveryResult
        self.discovery.refresh.return_value = DiscoveryResult(replace(self.release, raw_bundle=b'changed'),
                                                              checked_at=self.now)
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        self.assertEqual(self.hook.calls, [])

    def test_paused_during_backup_blocks_before_any_env_mutation(self):
        from deploy.update.orchestrator import GuardRefused
        backup = self.engine.hook_runner
        def pause(argv, **kwargs):
            result = backup(argv, **kwargs)
            self.store.change_control('pause', self.now)
            return result
        self.engine.hook_runner = pause
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.CONTROL_INVALID)
        self.assertEqual(self.env_write_count, 0)
        self.assertIsNone(self.state.read_journal())

    def test_backup_edits_configuration_refuse_without_overwriting_operator(self):
        from deploy.update.orchestrator import GuardRefused
        backup = self.engine.hook_runner
        def edit(argv, **kwargs):
            result = backup(argv, **kwargs)
            with self.env_path.open('a') as handle:
                handle.write('\nOPERATOR_EDIT=preserve\n')
            return result
        self.engine.hook_runner = edit
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.CONFIGURATION_CHANGED)
        self.assertIn(b'OPERATOR_EDIT=preserve', self.env_bytes())
        self.assertEqual(self.env_write_count, 0)

    def test_pause_arriving_during_refresh_is_rechecked_before_backup(self):
        from deploy.update.orchestrator import GuardRefused
        refreshed = self.discovery.refresh.return_value
        def pause(*args, **kwargs):
            self.store.change_control('pause', self.now)
            return refreshed
        self.discovery.refresh.side_effect = pause
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.CONTROL_INVALID)
        self.assertEqual(self.hook.calls, [])

    def test_foreign_journal_arriving_during_backup_is_kept_without_recovery(self):
        from deploy.update.orchestrator import GuardRefused
        backup = self.engine.hook_runner
        foreign = {'stage': 'prepare', 'kind': 'apply', 'run_id': 'e'*32}
        def inject(argv, **kwargs):
            result = backup(argv, **kwargs)
            self.state.write_journal(foreign)
            return result
        self.engine.hook_runner = inject
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.RECOVERY_REQUIRED)
        self.assertEqual(self.state.read_journal(), foreign)
        self.assertEqual(self.env_write_count, 0)

    def test_foreign_journal_at_rollout_boundary_is_not_cleared_or_recovered(self):
        from deploy.update.apply import UpdateError
        validate = self.guard.validate
        foreign = {'stage': 'prepare', 'kind': 'rollback', 'run_id': 'e'*32}
        def inject(boundary, context):
            if boundary == 'before_rollout':
                self.state.write_journal(foreign)
            validate(boundary, context)
        self.guard.validate = inject
        with self.assertRaises(UpdateError):
            self.run_guarded()
        self.assertEqual(self.state.read_journal(), foreign)
        self.assertFalse(any(call[0] == 'up' for call in self.compose_calls()))
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.52.0')
        self.assertEqual(self.state.receipts(), [])

    def test_backup_proof_cannot_be_replaced_with_a_newer_attestation(self):
        import json
        from deploy.update.orchestrator import GuardRefused
        validate = self.guard.validate
        def change(boundary, context):
            if boundary == 'before_env_commit':
                value = json.loads(self.guard.proof_path.read_text())
                value['backup_id'] = 'different-backup'
                self.guard.proof_path.write_text(json.dumps(value))
            validate(boundary, context)
        self.guard.validate = change
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.BACKUP_PROOF_INVALID)
        self.assertEqual(self.env_write_count, 0)

    def test_successful_runner_finalizes_attempt_without_a_failed_target_latch(self):
        from deploy.update.orchestrator import execute_guarded
        self.engine.settings = load_settings(self.root, {'FUTURE_AGI_UPDATE_BACKUP_COMMAND': BACKUP}, environ={})
        outcome = execute_guarded(self.engine, self.guard)
        self.assertEqual(outcome.code, 0)
        self.assertEqual(outcome.forward_result, 'succeeded')
        self.assertTrue(outcome.transaction_started)
        self.assertTrue(outcome.rollout_started)
        self.assertIsNone(self.store.read_state().active_attempt_id)
        self.assertIsNone(self.store.read_state().failed_target_latch)

    def test_missing_proof_runner_latches_target_and_records_typed_reason(self):
        from deploy.update.orchestrator import execute_guarded
        self.engine.settings = load_settings(self.root, {'FUTURE_AGI_UPDATE_BACKUP_COMMAND': BACKUP}, environ={})
        self.engine.hook_runner = self.hook
        outcome = execute_guarded(self.engine, self.guard)
        self.assertEqual(outcome.code, 3)
        self.assertEqual(outcome.reason_codes, (ReasonCode.BACKUP_PROOF_INVALID,))
        self.assertFalse(outcome.transaction_started)
        self.assertEqual(self.store.read_state().failed_target_latch.version, 'v1.52.0')
        self.assertIsNone(self.store.read_state().active_attempt_id)

    def test_missing_backup_proof_fails_closed(self):
        from deploy.update.orchestrator import GuardRefused
        self.engine.hook_runner = self.hook
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.BACKUP_PROOF_INVALID)
        self.assertEqual(self.env_bytes(), self.original)

    def test_expired_backup_at_mutation_boundary_refuses(self):
        from datetime import timedelta
        from deploy.update.orchestrator import GuardRefused
        original = self.guard.validate
        def validate(boundary, context):
            if boundary == 'before_env_commit':
                self.now += timedelta(seconds=3601)
                # A fresh online verification must not conceal an expired proof.
                from dataclasses import replace
                from deploy.update.discovery import DiscoveryResult
                release = replace(self.release, verification=replace(self.release.verification, checked_at=self.now))
                self.discovery.refresh.return_value = DiscoveryResult(release, checked_at=self.now)
            original(boundary, context)
        self.guard.validate = validate
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.BACKUP_EXPIRED)
        self.assertEqual(self.env_write_count, 0)

    def test_incomplete_attempt_is_never_retried_by_another_guard(self):
        from deploy.update.orchestrator import GuardRefused
        from deploy.update.runner_state import TargetIdentity
        target = TargetIdentity(self.prepared.manifest['version'], self.plan_value['manifest_hash'])
        self.store.reserve_attempt(target, 'earlier-window', self.now)
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.RECOVERY_REQUIRED)
        self.assertEqual(self.hook.calls, [])

    def test_failed_target_latch_refuses_before_backup(self):
        from dataclasses import replace
        from deploy.update.orchestrator import GuardRefused
        from deploy.update.runner_state import TargetIdentity
        target = TargetIdentity(self.prepared.manifest['version'], self.plan_value['manifest_hash'])
        attempt = self.store.reserve_attempt(target, 'earlier-window', self.now)
        self.store.finish_attempt(replace(attempt, outcome='failed', finished_at=self.now))
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.FAILED_TARGET_LATCHED)

    def test_uncertified_release_does_not_take_backup(self):
        from dataclasses import replace
        from deploy.update.orchestrator import GuardRefused
        self.obs = replace(self.obs, release_proof=b'not-certified')
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.UNCERTIFIED_RELEASE)
        self.assertEqual(self.hook.calls, [])

    def prepare_failed_rollout(self, before_authorize=None):
        from dataclasses import replace
        import subprocess
        from deploy.update.compatibility import RuntimeImage
        from deploy.update.apply import FailedDuringRollout
        from deploy.update.readiness import NotReady
        before = self.obs.product_images['app'][0]
        image_id = self.inst.running['app']['image_id']
        self.obs = replace(self.obs, product_images={'app': (RuntimeImage(before.image, before.digest, image_id),)})
        self.inst.metadata[image_id]['repo_digests'] = ['futureagi/standalone@' + before.digest]
        self.inst.runner = mock.Mock(return_value=subprocess.CompletedProcess([], 1, b'', b''))
        self.inst.runner.uncertain = False
        self.ready.failure = NotReady('synthetic readiness failure')
        with self.assertRaises(FailedDuringRollout):
            self.run_guarded()
        self.ready.failure = None
        receipt = self.state.latest_receipt()
        target = self.prepared.manifest['components']['standalone']
        target_id = self.inst.running['app']['image_id']
        self.obs = replace(self.obs, product_images={'app': (RuntimeImage(target['image'], target['digest'], target_id),)})
        if before_authorize is not None:
            before_authorize()
        self.guard.authorize_fallback(self.engine, Path(receipt['_path']),
                                      prior_images=self.guard.prior_images, child_state_known=True)
        return receipt

    def test_one_exact_certified_fallback_survives_pause_but_never_retries(self):
        from deploy.update.orchestrator import GuardRefused
        receipt = self.prepare_failed_rollout()
        self.store.change_control('pause', self.now)
        path = self.guard.rollback_once(self.engine)
        restored = self.state.load_receipt(path)
        self.assertEqual(restored['status'], 'rolled_back')
        self.assertEqual(restored['rolls_back'], receipt['_path'])
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.51.0')
        self.assertEqual(len(self.hook.calls), 1)  # fallback never takes/restores a database backup
        from deploy.update.status import observe_status
        observation = observe_status(self.inst, self.state, now=self.now)
        self.assertTrue(observation.last_run['automatic'])
        self.assertEqual(observation.last_run['outcome'], 'failed_during_rollout')
        self.assertEqual(observation.last_run['fallback_outcome'], 'succeeded')
        with self.assertRaises(GuardRefused):
            self.guard.rollback_once(self.engine)

    def baseline_only_facts(self):
        """Like the enrolled adapter: facts exist only for the unchanged pre-update graph."""
        from deploy.update.orchestrator import GuardRefused, fingerprints
        seen = []

        def facts(installation, boundary):
            config, profile, deployment = fingerprints(installation)
            seen.append(boundary)
            if (config, profile, deployment) != (self.prepared.config_fingerprint, self.prepared.profile_fingerprint,
                                                 self.prepared.deployment_fingerprint):
                raise GuardRefused(ReasonCode.CONFIGURATION_CHANGED)
            return self.obs, self.controls

        self.guard.facts = facts
        return seen

    def test_fallback_predecessor_facts_are_read_against_the_unchanged_graph(self):
        seen = self.baseline_only_facts()
        self.prepare_failed_rollout()
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.52.0')  # the forward pins are still live
        self.assertEqual(seen.count('before_fallback'), 1)
        path = self.guard.rollback_once(self.engine)
        self.assertEqual(self.state.load_receipt(path)['status'], 'rolled_back')
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.51.0')

    def test_operator_pin_edit_before_fallback_authorization_is_refused_not_normalized(self):
        from deploy.update.orchestrator import GuardRefused
        self.baseline_only_facts()

        def edit():
            with self.env_path.open('a') as handle:
                handle.write('\nFUTURE_AGI_VERSION=v1.53.0\n')

        with self.assertRaises(GuardRefused) as caught:
            self.prepare_failed_rollout(before_authorize=edit)
        self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)
        self.assertIsNone(self.guard.fallback)
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.53.0')

    def test_non_pin_env_edit_before_fallback_authorization_is_refused(self):
        from deploy.update.orchestrator import GuardRefused
        self.baseline_only_facts()

        def edit():
            with self.env_path.open('a') as handle:
                handle.write('\nSOME_OPERATOR_SETTING=1\n')

        with self.assertRaises(GuardRefused) as caught:
            self.prepare_failed_rollout(before_authorize=edit)
        self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)
        self.assertIsNone(self.guard.fallback)

    def test_prior_image_lost_signed_digest_cannot_authorize_fallback(self):
        from deploy.update.orchestrator import GuardRefused
        self.prepare_failed_rollout()
        # Revoke the prior image's inspectable signed digest before evaluating
        # another fresh eligibility boundary. Merely having an image ID is not proof.
        self.guard.fallback = None
        prior = self.guard.prior_images['app'][0]
        self.inst.metadata[prior.image_id]['repo_digests'] = []
        latest = self.state.latest_receipt()
        with self.assertRaises(GuardRefused) as caught:
            self.guard.authorize_fallback(self.engine, Path(latest['_path']),
                                          prior_images=self.guard.prior_images, child_state_known=True)
        self.assertEqual(caught.exception.reason_code, ReasonCode.ROLLBACK_INELIGIBLE)
        self.assertFalse(self.guard.fallback_reserved)

    def test_newer_receipt_invalidates_exact_fallback_selection(self):
        from deploy.update.orchestrator import GuardRefused
        receipt = self.prepare_failed_rollout()
        newer = dict(receipt, run_id='f'*32)
        newer.pop('_path')
        self.state.write_receipt(newer)
        with self.assertRaises(GuardRefused) as caught:
            self.guard.rollback_once(self.engine)
        self.assertEqual(caught.exception.reason_code, ReasonCode.ROLLBACK_INELIGIBLE)
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.52.0')

    def test_operator_pin_edit_invalidates_fallback(self):
        from deploy.update.orchestrator import GuardRefused
        self.prepare_failed_rollout()
        with self.env_path.open('a') as handle:
            handle.write('\nFUTURE_AGI_VERSION=v1.53.0\n')
        with self.assertRaises(GuardRefused) as caught:
            self.guard.rollback_once(self.engine)
        self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.53.0')

    def test_clock_regression_refuses_before_backup(self):
        from datetime import timedelta
        from deploy.update.orchestrator import GuardRefused
        self.now -= timedelta(seconds=1)
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.CLOCK_SKEW)
        self.assertEqual(self.hook.calls, [])

    def test_closed_window_after_slow_refresh_refuses(self):
        from dataclasses import replace
        from deploy.update.orchestrator import GuardRefused
        refreshed = self.discovery.refresh.return_value
        def close(*args, **kwargs):
            self.controls = replace(self.controls, window_open=False)
            return refreshed
        self.discovery.refresh.side_effect = close
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.OUTSIDE_WINDOW)
        self.assertEqual(self.env_write_count, 0)

    def test_typed_busy_preserves_running_operation(self):
        from deploy.update.receipt import StateBusy
        with self.state.lock(), self.assertRaises(StateBusy):
            self.run_guarded()
        self.assertEqual(self.hook.calls, [])

    def test_public_observation_contains_no_raw_error_path_or_env_value(self):
        from dataclasses import asdict
        import json
        from deploy.update.status import observe_status
        self.inst.fresh = mock.Mock(side_effect=RuntimeError('/private/path PASSWORD=very-secret'))
        observation = observe_status(self.inst, self.state, now=self.now)
        text = json.dumps(asdict(observation))
        self.assertNotIn('private/path', text)
        self.assertNotIn('very-secret', text)
        self.assertIsNone(observation.versions['observed_at'])
        self.assertEqual(observation.reason_codes, (ReasonCode.RUNNING_VERSIONS_UNKNOWN,))
        self.assertEqual(self.env_write_count, 0)

    def test_status_observes_running_and_selected_versions_separately(self):
        from deploy.update.status import observe_status
        observation = observe_status(self.inst, self.state, now=self.now)
        self.assertEqual(observation.versions['selected_release'], 'v1.51.0')
        self.assertEqual(observation.versions['running_release'], 'v1.51.0')
        self.assertFalse(observation.pending_transaction)
        self.assertEqual(self.env_write_count, 0)

    def test_mutation_timeout_retains_journal_and_cannot_fallback(self):
        from deploy.update.processes import ProcessError
        from deploy.update.apply import UpdateError
        self.inst.failures['up'] = ProcessError(ReasonCode.OPERATION_TIMEOUT, uncertain=True)
        with self.assertRaises(UpdateError):
            self.run_guarded()
        self.assertIsNotNone(self.state.read_journal())
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.52.0')
        self.assertEqual(self.state.receipts(), [])

    # ------------------------------------------------------------------------------------------------------------
    # Review round 2 (F1): an automatic transaction is bound to the exact env bytes the guard approved.

    PIN_EDIT = '\nFUTURE_AGI_VERSION=v1.53.0\n'

    def edit_after_facts(self, boundary, call, *, edit=PIN_EDIT):
        """Make an operator pin edit land right after the `call`-th facts call of `boundary` returns."""
        from collections import Counter
        calls = Counter()
        original = self.guard.facts

        def facts(installation, name):
            result = original(installation, name)
            calls[name] += 1
            if name == boundary and calls[name] == call:
                with self.env_path.open('a') as handle:
                    handle.write(edit)
            return result

        self.guard.facts = facts

    def assert_operator_edit_survives(self, expected_selection='v1.53.0'):
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), expected_selection)
        self.assertTrue(self.env_bytes().endswith(self.PIN_EDIT.encode()))
        self.assertIsNone(self.state.read_journal())

    def test_pin_edit_during_the_final_facts_call_is_refused_and_survives(self):
        from deploy.update.orchestrator import GuardRefused
        self.edit_after_facts('before_env_commit', 2)  # the second call is the final observation
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)
        self.assert_operator_edit_survives()
        self.assertEqual(self.env_bytes(), self.original + self.PIN_EDIT.encode())
        self.assertEqual(self.env_write_count, 0)
        self.assertFalse(any(call[0] == 'up' for call in self.compose_calls()))
        self.assertEqual(self.state.receipts(), [])
        self.assertIsNone(self.guard.approved_env_sha256)

    def test_any_env_edit_during_the_final_facts_call_is_refused_not_only_pins(self):
        from deploy.update.orchestrator import GuardRefused
        self.edit_after_facts('before_env_commit', 2, edit='\n# a comment the operator added\n')
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)
        self.assertTrue(self.env_bytes().endswith(b'# a comment the operator added\n'))
        self.assertEqual(self.env_write_count, 0)

    def test_pin_edit_after_the_guard_returned_is_refused_by_the_transaction_itself(self):
        # The window between the guard and the journal (ownership is rechecked there): the preimage the
        # transaction is built from must be the one the guard approved, not whatever is on disk by then.
        from deploy.update.orchestrator import GuardRefused
        validate = self.guard.validate
        state = {'approved': False, 'edited': False}

        def watch(boundary, context):
            validate(boundary, context)
            state['approved'] = state['approved'] or boundary == 'before_env_commit'

        def ownership():
            if state['approved'] and not state['edited']:
                state['edited'] = True
                with self.env_path.open('a') as handle:
                    handle.write(self.PIN_EDIT)

        self.guard.validate = watch
        self.inst.check_ownership = ownership
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertTrue(state['edited'])
        self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)
        self.assert_operator_edit_survives()
        self.assertEqual(self.env_write_count, 0)
        self.assertFalse(any(call[0] == 'up' for call in self.compose_calls()))
        self.assertEqual(self.state.receipts(), [])

    def test_pin_edit_before_the_final_observation_is_still_a_configuration_change(self):
        from deploy.update.orchestrator import GuardRefused
        self.edit_after_facts('before_env_commit', 1)  # lands before the final observation starts
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.reason_code, ReasonCode.CONFIGURATION_CHANGED)
        self.assert_operator_edit_survives()
        self.assertEqual(self.env_write_count, 0)

    def test_env_that_cannot_be_read_when_the_final_observation_starts_fails_closed(self):
        from deploy.update.envfile import EnvCommitError
        from deploy.update.orchestrator import GuardRefused
        validate = self.guard.validate

        def flaky_first_read(boundary, context):
            if boundary == 'before_env_commit':
                original = self.engine._env_path
                reads = []

                def env_path():
                    if not reads:
                        reads.append(1)
                        raise EnvCommitError('the selected env target changed')
                    return original()

                self.engine._env_path = env_path
            validate(boundary, context)

        self.guard.validate = flaky_first_read
        with self.assertRaises(GuardRefused) as caught:
            self.run_guarded()
        # Nothing may be approved on bytes that were never read: the guard refuses instead of skipping the binding.
        self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)
        self.assertEqual(self.env_write_count, 0)
        self.assertIsNone(self.guard.approved_env_sha256)

    def test_without_an_edit_the_transaction_is_bound_to_the_approved_bytes_and_succeeds(self):
        import hashlib
        path = self.run_guarded()
        approved = hashlib.sha256(self.original).hexdigest()
        self.assertEqual(self.guard.approved_env_sha256, approved)
        self.assertEqual(self.state.load_receipt(path)['status'], 'succeeded')
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.52.0')
        self.assertEqual(self.env_write_count, 1)

    def test_rollout_boundary_rechecks_the_approved_preimage(self):
        from deploy.update.apply import UpdateError
        validate = self.guard.validate

        def tamper(boundary, context):
            if boundary == 'before_rollout':
                self.guard.approved_env_sha256 = 'f' * 64  # the transaction began from other bytes
            validate(boundary, context)

        self.guard.validate = tamper
        with self.assertRaises(UpdateError) as caught:
            self.run_guarded()
        self.assertEqual(str(caught.exception), ReasonCode.RECOVERY_REQUIRED.value)
        self.assertFalse(any(call[0] == 'up' for call in self.compose_calls()))
        self.assertIsNotNone(self.state.read_journal())  # kept for the operator, never normalized away

    def test_manual_runs_keep_the_unbound_recovery_record_call(self):
        from deploy.update import envfile
        with mock.patch.object(envfile, 'recovery_record', wraps=envfile.recovery_record) as record:
            self.apply(updater=self.updater())
        self.assertEqual(record.call_count, 1)
        self.assertEqual(len(record.call_args.args), 2)
        self.assertEqual(record.call_args.kwargs, {})

    def test_fallback_pin_edit_during_the_final_facts_call_is_refused_and_survives(self):
        from deploy.update.orchestrator import GuardRefused
        self.prepare_failed_rollout()
        ups = sum(call[0] == 'up' for call in self.compose_calls())
        self.edit_after_facts('before_env_commit', 1)
        with self.assertRaises(GuardRefused) as caught:
            self.guard.rollback_once(self.engine)
        self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)
        self.assert_operator_edit_survives()
        self.assertEqual(sum(call[0] == 'up' for call in self.compose_calls()), ups)
        self.assertEqual([r['status'] for r in self.receipts()], ['failed_during_rollout'])

    def test_fallback_pin_edit_after_the_guard_returned_is_refused_by_the_transaction_itself(self):
        from deploy.update.orchestrator import GuardRefused
        self.prepare_failed_rollout()
        validate = self.guard.validate
        state = {'approved': False, 'edited': False}

        def watch(boundary, context):
            validate(boundary, context)
            state['approved'] = state['approved'] or boundary == 'before_env_commit'

        def ownership():
            if state['approved'] and not state['edited']:
                state['edited'] = True
                with self.env_path.open('a') as handle:
                    handle.write(self.PIN_EDIT)

        self.guard.validate = watch
        self.inst.check_ownership = ownership
        with self.assertRaises(GuardRefused) as caught:
            self.guard.rollback_once(self.engine)
        self.assertTrue(state['edited'])
        self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)
        self.assert_operator_edit_survives()

    def test_fallback_rollout_boundary_rechecks_the_forward_transactions_hash(self):
        from deploy.update.apply import UpdateError
        self.prepare_failed_rollout()
        validate = self.guard.validate
        ups = sum(call[0] == 'up' for call in self.compose_calls())

        def tamper(boundary, context):
            if boundary == 'before_rollout':
                self.guard.approved_env_sha256 = 'f' * 64
            validate(boundary, context)

        self.guard.validate = tamper
        with self.assertRaises(UpdateError):
            self.guard.rollback_once(self.engine)
        self.assertEqual(sum(call[0] == 'up' for call in self.compose_calls()), ups)

    def test_fallback_authorization_rechecks_env_after_predecessor_facts(self):
        from deploy.update.orchestrator import GuardRefused
        self.edit_after_facts('before_fallback', 1)
        with self.assertRaises(GuardRefused) as caught:
            self.prepare_failed_rollout()
        self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)
        self.assertIsNone(self.guard.fallback)
        self.assert_operator_edit_survives()

    def test_fallback_authorization_rechecks_env_after_prior_image_inspection(self):
        from deploy.update.orchestrator import GuardRefused

        def arm():
            image = self.inst.image
            prior_id = self.guard.prior_images['app'][0].image_id

            def inspect(reference):
                result = image(reference)
                if reference == prior_id:
                    with self.env_path.open('a') as handle:
                        handle.write(self.PIN_EDIT)
                return result

            self.inst.image = inspect

        with self.assertRaises(GuardRefused) as caught:
            self.prepare_failed_rollout(before_authorize=arm)
        self.assertEqual(caught.exception.reason_code, ReasonCode.ENV_CONFLICT)
        self.assertIsNone(self.guard.fallback)
        self.assert_operator_edit_survives()

    def test_final_rollout_observation_preserves_forward_and_fallback_operator_edits(self):
        from deploy.update.apply import UpdateError
        from deploy.update.orchestrator import GuardRefused
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                self.setUp()
                if fallback:
                    self.prepare_failed_rollout()
                ups = sum(call[0] == 'up' for call in self.compose_calls())
                self.edit_after_facts('before_rollout', 1 if fallback else 2)
                with self.assertRaises(UpdateError) as caught:
                    if fallback:
                        self.guard.rollback_once(self.engine)
                    else:
                        self.run_guarded()
                self.assertIsInstance(caught.exception.__cause__, GuardRefused)
                self.assertEqual(caught.exception.__cause__.reason_code, ReasonCode.ENV_CONFLICT)
                self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.53.0')
                self.assertTrue(self.env_bytes().endswith(self.PIN_EDIT.encode()))
                self.assertEqual(sum(call[0] == 'up' for call in self.compose_calls()), ups)
                self.assertIsNotNone(self.state.read_journal())

    def test_fallback_without_an_edit_is_bound_to_the_forward_hash_and_succeeds(self):
        self.prepare_failed_rollout()
        path = self.guard.rollback_once(self.engine)
        self.assertEqual(self.state.load_receipt(path)['status'], 'rolled_back')
        self.assertEqual(self.guard.approved_env_sha256, self.guard.fallback_env_sha256)
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.51.0')

    # ------------------------------------------------------------------------------------------------------------
    # Review round 2 (F2): the dependency images Compose starts are the ones the facts certified.

    def whole_stack_up(self, on_up=None):
        """Model Compose's whole-stack `up`: every service runs the image its tag resolves to on this host NOW."""
        original = self.inst.compose_run
        self.up_calls = 0

        def compose(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] == 'up':
                self.up_calls += 1
                for service, image in self.inst.third_party.items():
                    self.inst.running[service] = {'image': image, 'image_id': self.inst.local_images[image]}
                if on_up is not None:
                    on_up(self.up_calls)
            return result

        self.inst.compose_run = compose

    def move_tag(self, service='postgres', character='9'):
        """A newer upstream build was pulled/retagged locally under the dependency's tag; returns its image id."""
        reference = self.inst.third_party[service]
        image_id = 'sha256:' + character * 64
        self.inst.local_images[reference] = image_id
        self.inst.metadata[image_id] = {'repo_digests': [reference.rsplit(':', 1)[0] + '@sha256:' + character * 64],
                                        'version': 'a different upstream build'}
        return image_id

    def live_dependency_facts(self):
        """Facts as the enrolled adapter reports them: what the dependency containers run right now."""
        from dataclasses import replace
        from deploy.update.compatibility import RuntimeImage

        def facts(installation, boundary):
            images = {}
            for service in self.obs.dependency_images:
                running = self.inst.running[service]
                digest = self.inst.metadata[running['image_id']]['repo_digests'][0].split('@', 1)[1]
                images[service] = (RuntimeImage(running['image'], digest, running['image_id']),)
            return replace(self.obs, dependency_images=images), self.controls

        self.guard.facts = facts

    def running_snapshot(self):
        import copy
        return copy.deepcopy(self.inst.running)

    def test_retagged_dependency_is_refused_before_up_and_nothing_is_touched(self):
        from deploy.update.apply import FailedBeforeRollout
        from deploy.update.orchestrator import GuardRefused
        self.whole_stack_up()
        self.move_tag('postgres')
        running = self.running_snapshot()
        with self.assertRaises(FailedBeforeRollout) as caught:
            self.run_guarded()
        cause = caught.exception.__cause__
        self.assertIsInstance(cause, GuardRefused)
        self.assertEqual(cause.reason_code, ReasonCode.IMAGE_DIGEST_MISMATCH)
        self.assertEqual(self.up_calls, 0)
        self.assertFalse(any(call[0] == 'up' for call in self.compose_calls()))
        self.assertEqual(self.running_snapshot(), running)
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual([r['status'] for r in self.receipts()], ['failed_before_rollout'])
        self.assertIsNone(self.state.read_journal())

    def test_retagged_dependency_is_a_typed_pre_rollout_outcome_and_latches_the_target(self):
        from deploy.update.orchestrator import execute_guarded
        self.engine.settings = load_settings(self.root, {'FUTURE_AGI_UPDATE_BACKUP_COMMAND': BACKUP}, environ={})
        self.whole_stack_up()
        self.move_tag('clickhouse', '8')
        outcome = execute_guarded(self.engine, self.guard)
        self.assertEqual((outcome.code, outcome.reason_codes), (4, (ReasonCode.IMAGE_DIGEST_MISMATCH,)))
        self.assertEqual((outcome.forward_result, outcome.fallback_result), ('failed', 'none'))
        self.assertFalse(outcome.rollout_started)
        self.assertEqual(self.up_calls, 0)
        self.assertIsNotNone(self.store.read_state().failed_target_latch)

    def test_same_image_id_without_the_certified_digest_is_refused_before_up(self):
        from deploy.update.apply import FailedBeforeRollout
        self.whole_stack_up()
        certified = self.obs.dependency_images['postgres'][0]
        self.inst.metadata[certified.image_id]['repo_digests'] = [certified.image.rsplit(':', 1)[0] + '@sha256:' + '4' * 64]
        with self.assertRaises(FailedBeforeRollout) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.__cause__.reason_code, ReasonCode.IMAGE_DIGEST_MISMATCH)
        self.assertEqual(self.up_calls, 0)

    def test_dependency_tag_removed_and_pulled_again_cannot_bypass_certification(self):
        # The engine pulls a third-party image that is not on the host; whatever the registry serves now is a
        # new build, so the pull may not stand in for the certified image.
        from deploy.update.apply import FailedBeforeRollout
        self.whole_stack_up()
        reference = self.inst.third_party['postgres']
        self.inst.local_images.pop(reference)
        self.inst.registry[reference] = self.move_tag('postgres', '6')
        self.inst.local_images.pop(reference)  # move_tag put it back; only the registry may offer it
        with self.assertRaises(FailedBeforeRollout) as caught:
            self.run_guarded()
        self.assertTrue(any(call[0] == 'pull' for call in self.compose_calls()))
        self.assertEqual(caught.exception.__cause__.reason_code, ReasonCode.IMAGE_DIGEST_MISMATCH)
        self.assertEqual(self.up_calls, 0)

    def test_unchanged_dependencies_succeed_and_are_certified_once(self):
        self.whole_stack_up()
        running = self.running_snapshot()
        path = self.run_guarded()
        self.assertEqual(self.state.load_receipt(path)['status'], 'succeeded')
        self.assertEqual(self.up_calls, 1)
        for service in ('clickhouse', 'postgres'):
            self.assertEqual(self.inst.running[service], running[service])
            self.assertEqual(self.guard.dependency_image_ids[service], frozenset({running[service]['image_id']}))

    def test_dependency_replaced_during_rollout_never_yields_a_succeeded_receipt(self):
        from deploy.update.apply import FailedDuringRollout
        self.live_dependency_facts()
        replaced = {}

        def replace_while_starting(call):
            # Compose resolved the dependency's tag to another build between the pre-rollout check and `up`.
            replaced['id'] = self.move_tag('postgres')
            reference = self.inst.third_party['postgres']
            self.inst.running['postgres'] = {'image': reference, 'image_id': replaced['id']}

        self.whole_stack_up(replace_while_starting)
        with self.assertRaises(FailedDuringRollout) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.__cause__.reason_code, ReasonCode.IMAGE_DIGEST_MISMATCH)
        self.assertEqual([r['status'] for r in self.receipts()], ['failed_during_rollout'])
        self.assertEqual(self.inst.running['postgres']['image_id'], replaced['id'])

    def test_missing_dependency_after_rollout_never_yields_a_succeeded_receipt(self):
        from deploy.update.apply import FailedDuringRollout
        self.whole_stack_up(lambda call: self.inst.running.pop('postgres'))
        with self.assertRaises(FailedDuringRollout) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.__cause__.reason_code, ReasonCode.IMAGE_DIGEST_MISMATCH)
        self.assertEqual([r['status'] for r in self.receipts()], ['failed_during_rollout'])

    def test_dependency_replaced_during_rollout_is_a_failed_rollout_with_no_automatic_fallback(self):
        from deploy.update.orchestrator import execute_guarded
        self.engine.settings = load_settings(self.root, {'FUTURE_AGI_UPDATE_BACKUP_COMMAND': BACKUP}, environ={})
        self.live_dependency_facts()

        def replace_while_starting(call):
            image_id = self.move_tag('postgres')
            self.inst.running['postgres'] = {'image': self.inst.third_party['postgres'], 'image_id': image_id}

        self.whole_stack_up(replace_while_starting)
        outcome = execute_guarded(self.engine, self.guard)
        self.assertEqual((outcome.code, outcome.forward_result), (5, 'failed'))
        self.assertEqual(outcome.reason_codes, (ReasonCode.IMAGE_DIGEST_MISMATCH,))
        self.assertTrue(outcome.rollout_started)
        # The fallback's own eligibility facts see the uncertified dependency: it is not attempted.
        self.assertEqual(outcome.fallback_result, 'ineligible')
        self.assertEqual(self.up_calls, 1)
        self.assertNotIn('succeeded', [r['status'] for r in self.receipts()])
        self.assertIsNotNone(self.store.read_state().failed_target_latch)

    def test_retagged_dependency_is_refused_before_the_fallback_up(self):
        from deploy.update.apply import FailedBeforeRollout
        self.whole_stack_up()
        self.prepare_failed_rollout()
        ups = self.up_calls
        self.move_tag('postgres')
        with self.assertRaises(FailedBeforeRollout) as caught:
            self.guard.rollback_once(self.engine)
        self.assertEqual(caught.exception.__cause__.reason_code, ReasonCode.IMAGE_DIGEST_MISMATCH)
        self.assertEqual(self.up_calls, ups)
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.52.0')  # the failed forward pins, untouched

    def test_dependency_replaced_during_the_fallback_rollout_is_not_a_rolled_back_receipt(self):
        from deploy.update.apply import FailedDuringRollout
        self.whole_stack_up()
        self.prepare_failed_rollout()

        def replace_while_starting(call):
            image_id = self.move_tag('clickhouse')
            self.inst.running['clickhouse'] = {'image': self.inst.third_party['clickhouse'], 'image_id': image_id}

        self.whole_stack_up(replace_while_starting)
        with self.assertRaises(FailedDuringRollout):
            self.guard.rollback_once(self.engine)
        self.assertNotIn('rolled_back', [r['status'] for r in self.receipts()])

    # ------------------------------------------------------------------------------------------------------------
    # Review round 2 (F3): the guard authorizes against a clock sampled after its own last facts call.

    def windowed(self, window='12:00-12:16'):
        from dataclasses import replace
        from deploy.update.schedule import occurrence
        settings = load_settings(self.root, {'FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW': window}, environ={})
        self.engine.settings = settings
        self.controls = replace(self.controls, policy=settings.automation_policy)
        self.guard.window_occurrence = occurrence(settings.automation_policy.maintenance_window, self.now)

    def slow_final_facts(self, seconds, boundary='before_rollout'):
        """The provider returns stale 'window open / budget available' booleans after taking `seconds`."""
        from collections import Counter
        from datetime import timedelta
        calls = Counter()

        def facts(installation, name):
            calls[name] += 1
            if name == boundary and calls[name] == 2:
                self.now += timedelta(seconds=seconds)
            return self.obs, self.controls

        self.guard.facts = facts

    def test_slow_final_facts_that_cross_the_start_budget_refuse_before_up(self):
        from deploy.update.apply import FailedBeforeRollout
        self.windowed()
        self.whole_stack_up()
        self.slow_final_facts(61)  # 960s left at 12:00:00; 899 after the final observation: under the 900 required
        with self.assertRaises(FailedBeforeRollout) as caught:
            self.run_guarded()
        self.assertEqual(caught.exception.__cause__.reason_code, ReasonCode.INSUFFICIENT_WINDOW)
        self.assertEqual(self.up_calls, 0)
        self.assertEqual(self.env_bytes(), self.original)

    def test_slow_final_facts_that_stay_inside_the_budget_proceed(self):
        self.windowed()
        self.whole_stack_up()
        self.slow_final_facts(60)  # exactly 900s remain: the minimum is met
        path = self.run_guarded()
        self.assertEqual(self.state.load_receipt(path)['status'], 'succeeded')
        self.assertEqual(self.up_calls, 1)

    def test_slow_final_facts_that_close_the_window_refuse_at_every_forward_boundary(self):
        from deploy.update.orchestrator import GuardRefused
        for boundary in ('before_backup', 'after_backup', 'before_env_commit'):
            with self.subTest(boundary=boundary):
                self.setUp()
                self.windowed()
                self.slow_final_facts(1000, boundary)
                with self.assertRaises(GuardRefused) as caught:
                    self.run_guarded()
                self.assertIn(caught.exception.reason_code, (ReasonCode.INSUFFICIENT_WINDOW, ReasonCode.OUTSIDE_WINDOW))
                self.assertEqual(self.env_write_count, 0)

    def test_authorized_fallback_ignores_window_and_budget_booleans(self):
        from dataclasses import replace
        self.prepare_failed_rollout()
        self.controls = replace(self.controls, window_open=False, start_budget_available=False)
        path = self.guard.rollback_once(self.engine)
        self.assertEqual(self.state.load_receipt(path)['status'], 'rolled_back')

    def test_authorized_fallback_finishes_after_its_own_slow_facts_close_the_window(self):
        self.windowed('12:00-13:00')
        self.prepare_failed_rollout()
        from datetime import timedelta
        original = self.guard.facts

        def facts(installation, boundary):
            self.now += timedelta(hours=2)  # the window closed while the fallback was observing
            return original(installation, boundary)

        self.guard.facts = facts
        path = self.guard.rollback_once(self.engine)
        self.assertEqual(self.state.load_receipt(path)['status'], 'rolled_back')
        self.assertEqual(self.env_value('FUTURE_AGI_VERSION'), 'v1.51.0')
