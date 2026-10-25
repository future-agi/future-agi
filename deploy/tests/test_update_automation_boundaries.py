"""Scheduled engine entry points must not recover or mutate on a refused guard."""
from __future__ import annotations

import sys
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

    def prepare_failed_rollout(self):
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
