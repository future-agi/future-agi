"""Scheduled guards refuse forward mutation; sealed v2 attempts must restore.

Legacy/manual assertions retain their original behavior. Synthetic Docker and
local test-key release fixtures here are component coverage, not qualification.
"""
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


class RuntimeCrashCut(BaseException):
    """Simulated process death; deliberately bypasses exception recovery."""


class RuntimeComponentWorld:
    """Real engine/ledger/envcommit with fake Docker/storage, NEVER qualification."""
    def __init__(self, owner, *, fail_target=False, cut=None):
        import hashlib
        import json
        import time
        from dataclasses import dataclass
        from datetime import datetime, timezone
        from types import SimpleNamespace as NS
        from deploy.update.automatic_runtime import AutomaticRuntime
        from deploy.update.backup_proof import BackupExpectation
        from deploy.update.contracts import AutomationPolicy, AutomaticUpdateEstimate, BackupSeal
        from deploy.update.install import CommandResult
        from deploy.update.recovery import RecoveryLedger
        from deploy.update.standalone_backup import StandaloneBackup, BackupPolicy, _identity
        self.case = c = UpdaterCase('runTest')
        c.setUp()
        self.patches = []
        self.owner, self.events, self.cut = owner, [], cut
        self.generation, self.extractions, self.starts, self.creates = 'source', 0, [], []
        self.fail_target = fail_target
        self.time = 0.0
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.policy = policy = AutomationPolicy(mode='apply', enabled=True,
            qualification_policy='standalone-cold-restore-v1', probe_timeout_seconds=90,
            tick_timeout_seconds=20000, stability_period_seconds=1,
            migration_timeout_seconds=1, migration_stall_timeout_seconds=1,
            disk_watermark_reserve_bytes=1)
        self.estimate = AutomaticUpdateEstimate(1, 0, 1, 0, 100, 100, 10, 100, 100, 200, 0, 1)
        c.inst.third_party['clickhouse'] = 'clickhouse/clickhouse-server:25.3'
        c.inst.local_images['clickhouse/clickhouse-server:25.3'] = 'sha256:' + '8' * 64
        c.inst.metadata['sha256:' + '8' * 64] = {'repo_digests': ['clickhouse/clickhouse-server@sha256:' + '8' * 64], 'version': ''}
        target = c.inst.registry['futureagi/standalone:v1.1.0']
        c.inst.local_images['futureagi/standalone:v1.1.0'] = target
        self.source_images = {'app': c.inst.local_images['futureagi/standalone:v1.0.0'],
                             'postgres': 'sha256:' + '7' * 64, 'clickhouse': 'sha256:' + '8' * 64}
        self.rows, self.serial = {}, 0
        raw_config = c.inst.config
        def config(refresh=False):
            model = raw_config(refresh=refresh)
            for service, row in model['services'].items():
                row['restart'] = 'unless-stopped'
                row['ports'] = [{'target': 8000, 'published': '8000'}] if service == 'app' else []
            return model
        c.inst.config = config
        def create_rows():
            self.serial += 1
            model = config()
            self.rows = {}
            for service in ('app', 'postgres', 'clickhouse'):
                cid = hashlib.sha256((service + str(self.serial)).encode()).hexdigest()
                image = c.inst.local_images[model['services'][service]['image']]
                role = {'app': 'app-data', 'postgres': 'postgres-data', 'clickhouse': 'clickhouse-data'}[service]
                self.rows[cid] = {'Id': cid, 'Image': image, 'Created': 'fixed',
                    'Config': {'Image': model['services'][service]['image'], 'Labels': {
                        'com.docker.compose.project': c.inst.project, 'com.docker.compose.service': service}},
                    'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}},
                    'Mounts': [{'Type': 'volume', 'Name': role, 'Destination': '/data'}],
                    'State': {'Running': False, 'Status': 'created', 'ExitCode': 0, 'StartedAt': '', 'Health': {'Status': 'healthy'}},
                    'RestartCount': 0}
        create_rows()
        for row in self.rows.values():
            row['State'].update(Running=True, Status='running', StartedAt='source')
        def containers():
            return [dict(id=cid, service=row['Config']['Labels']['com.docker.compose.service'],
                image_id=row['Image'], image=row['Config']['Image'], status=row['State']['Status'],
                exit_code=0, restart_count=row['RestartCount'], health='healthy', started_at=row['State']['StartedAt'])
                for cid, row in self.rows.items()]
        c.inst.containers = containers
        def docker(*args, check=True):
            self.event('docker_before_' + args[0])
            if args[0] == 'info':
                value = 'daemon'
            elif args[:2] == ('volume', 'inspect'):
                value = json.dumps([self.backup.b['volumes'][name] for name in args[2:]])
            elif args[0] == 'inspect':
                value = json.dumps([self.rows[args[1]]])
            elif args[0] == 'start':
                row = self.rows[args[1]]
                if row['Config']['Labels']['com.docker.compose.service'] == 'app':
                    self.generation = 'source_running' if row['Image'] == self.source_images['app'] else 'candidate'
                row['State'].update(Running=True, Status='running', StartedAt='started-' + str(self.time))
                self.starts.append((row['Image'], self.generation))
                value = args[1]
            else:
                raise AssertionError(args)
            self.event('docker_after_' + args[0])
            return CommandResult(['docker', *args], 0, value, '')
        c.inst.docker_run = docker
        def compose(*args, **kwargs):
            if args == ('config', '--quiet'):
                return CommandResult(['docker', 'compose', *args], 0, '', '')
            owner.assertEqual(args, ('create', '--no-build', '--pull', 'never', '--force-recreate'))
            self.event('compose_before_create')
            create_rows()
            self.creates.append(c.inst.env['FUTURE_AGI_VERSION'])
            self.event('compose_after_create')
            return CommandResult(['docker', 'compose', *args], 0, '', '')
        c.inst.compose_run = compose
        c.inst.runner = NS(policy=policy, deadline=time.monotonic() + 20000, uncertain=False)
        settings = mock.Mock(wraps=load_settings(c.root, environ={}))
        settings.automation_policy = policy
        self.settings = settings
        world = self
        destination = c.root / 'backups'
        destination.mkdir(mode=0o700)
        self.expected = BackupExpectation('a' * 32, 'b' * 64, 'f' * 64, 'v1.0.0', self.now,
            ('clickhouse', 'object_store', 'postgres_app', 'shared_files', 'temporal'))
        self.ledger = None
        self.guard = NS(automatic=None, metadata={}, attempt=None, proof=None, recovery_required=False,
            approved_env_sha256=None, dependency_image_ids=None, first_mutation_at=None,
            prepared=NS(manifest={'version': 'v1.1.0'}))
        class Backup(StandaloneBackup):
            def __init__(self):
                self.destination, self.binding, self.policy = destination, NS(raw=b'{}'), BackupPolicy()
                self.b = {'project': c.inst.project, 'daemon': 'daemon', 'images': world.source_images,
                    'volumes': {role: {'Name': role} for role in ('app-data', 'postgres-data', 'clickhouse-data')},
                    'volume_root_ids': {role: '1:1' for role in ('app-data', 'postgres-data', 'clickhouse-data')}}
            def run_quiesced(self, expected, ledger, observation, **kwargs):
                world.event('backup_before')
                ledger.checkpoint('quiescing', containers={c['Config']['Labels']['com.docker.compose.service']: _identity(c)
                                                          for c in world.rows.values()},
                    start_times={c['Config']['Labels']['com.docker.compose.service']: c['State']['StartedAt'] for c in world.rows.values()},
                    restart_counts={c['Config']['Labels']['com.docker.compose.service']: c['RestartCount'] for c in world.rows.values()})
                for row in world.rows.values():
                    row['State'].update(Running=False, Status='exited')
                ledger.checkpoint('quiesced')
                from deploy.update.standalone_backup import _directory, _atomic
                # A synthetically measured, schema-v2 restore proof. The fake
                # storage does not qualify any product; do not reuse a v1 hook
                # attestation to exercise an automatic transaction.
                from deploy.update.manifest import canonical_json
                proof_raw = canonical_json(dict(schema_version=2, attempt_id=expected.attempt_id,
                    installation_fingerprint=expected.installation_fingerprint,
                    target_manifest_hash=expected.target_manifest_hash, source_version=expected.source_version,
                    backup_id='component-snapshot', started_at=world.now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    completed_at=world.now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    snapshot_at=world.now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    covered_domains=list(expected.covered_domains), verification='passed',
                    restore_procedure_id='standalone-inplace-cold-restore-v1', inventory_version=2,
                    binding_sha256='b' * 64, inventory_sha256='d' * 64,
                    source_observation_sha256='e' * 64, quiescence_evidence_sha256='f' * 64))
                proof_hash = hashlib.sha256(proof_raw).hexdigest()
                with _directory(ledger.directory) as directory:
                    _atomic(directory, 'proof.json', proof_raw)
                seal = BackupSeal(expected.attempt_id, 'token', world.engine.active_record['run_id'], 'b' * 64,
                    expected.target_manifest_hash, 'v1.0.0', proof_hash, 'd' * 64, 'e' * 64, 'f' * 64, world.now, world.now)
                ledger.seal_backup(seal)
                artifact = NS(proof=NS(raw=proof_raw, sha256=proof_hash), seal=seal, hold=NS())
                world.event('backup_after')
                return artifact
            def retire_helpers(self, ledger):
                world.event('retire_helpers')
            def abort_quiescence(self, expected, ledger):
                owner.assertIsNone(ledger.backup_seal)
                owner.assertIsNone(ledger.snapshot['pending'])
                for row in world.rows.values():
                    row['State'].update(Running=True, Status='running')
                ledger.checkpoint('failed_available')
                return NS()
        self.backup = Backup()
        @dataclass(frozen=True)
        class Intent:
            target: str = 'target'
        from dataclasses import replace
        from deploy.tests.test_update_compatibility import restore_context
        runtime_observation = replace(restore_context()[1].runtime_observation, release_version='v1.0.0')
        self.lifecycle = life = NS(registration=NS(), source_observation=NS(facts=NS(runtime_observation=runtime_observation)),
            head=NS(release=NS(version='v1.0.0')), intent=Intent(), target=NS(version='v1.1.0'),
            preflight_decision=NS(allowed=True, estimate=self.estimate), clock=lambda: world.now,
            backup_raw=None, ledger=None, backup_seal=None,
            backup=lambda installation: world.backup)
        def retain(artifact, ledger):
            life.backup_seal, life.ledger, life.backup_raw = artifact.seal, ledger, artifact.proof.raw
        life.retain_cold_hold = retain
        def finalize(*args):
            world.event('metadata_before')
            owner.assertIsNotNone(c.state.lock_holder())
            world.event('metadata_after')
        life.prepare_restore = lambda engine: world.event('prepare_restore')
        life.complete_restore = life.complete_success = finalize
        life._verification_window = lambda installation: world.event('retained_evidence_window')
        life.baseline = NS(evidence=lambda target: target, validate_success_observation=lambda *args: world.event('success_validate'))
        # Same semantics as AutomaticLifecycle: renew, then (re)verify. Looked
        # up dynamically so per-test replacements of the parts still apply.
        life.renew_verification = lambda installation: life._verification_window(installation)
        def target_evidence(installation):
            life.renew_verification(installation)
            return life.baseline.evidence(life.intent.target)
        def validate_success(installation, observation):
            life.renew_verification(installation)
            life.baseline.validate_success_observation(life.intent, observation)
        life.target_evidence, life.validate_success = target_evidence, validate_success
        life.retain_success = mock.Mock()
        life._observation = lambda *args: NS(facts=NS(now=world.now, runtime_observation=runtime_observation))
        life.verify_current = lambda *args: NS(facts=NS(runtime_observation=runtime_observation))
        self.guard.automatic = life
        self.guard.close = lambda: None
        def validate(boundary, context):
            world.event('guard_' + boundary)
            owner.assertIsNotNone(c.state.lock_holder())
            if boundary == 'before_backup':
                world.guard.attempt = NS(attempt_id=world.expected.attempt_id)
                ledger = RecoveryLedger.reserve(destination, world.expected, operation_token='token',
                    run_id=world.engine.active_record['run_id'], binding_sha256='b' * 64,
                    old_head='a' * 64, source_observation_sha256='e' * 64)
                world.ledger = world.engine.automatic_ledger = life.ledger = ledger
                ledger.checkpoint(source_model=config(), forward_record=world.engine.active_record)
            if boundary == 'before_env_commit':
                world.guard.first_mutation_at = world.now
                world.guard.approved_env_sha256 = hashlib.sha256(c.env_path.read_bytes()).hexdigest()
        self.guard.validate = validate
        self.guard.context = lambda engine: NS(backup_environment={}, receipt_metadata={})
        self.guard.expectation, self.guard.proof_directory = self.expected, None
        self.engine = Updater(c.inst, settings, c.state, out=c.out, automation_guard=self.guard,
            guard_context=self.guard.context, built_in_backup=self.backup)
        class Observer:
            def free_bytes(self):
                return 1000
            def progress(self, roster):
                if world.fail_target:
                    raise __import__('deploy.update.readiness', fromlist=['NotReady']).NotReady('fixture target failure')
                return 'ready', '', 'pg'
        self.runtime = lambda installation, ledger, b: AutomaticRuntime(installation, ledger, b,
            observer=Observer(), clock=lambda: world.time, sleep=lambda seconds: setattr(world, 'time', world.time + seconds))
        self.engine._automatic_runtime = lambda lifecycle, ledger, backup: world.runtime(c.inst, ledger, backup.b)
        class Restore:
            def __init__(self, *args, **kwargs):
                pass
            def resume(self, expected, ledger, **kwargs):
                owner.assertIsNotNone(c.state.lock_holder())
                owner.assertFalse(ledger.snapshot['previous_start_armed'])
                owner.assertIsNone(ledger.snapshot['pending'])
                owner.assertFalse(getattr(c.inst.runner, 'uncertain', False))
                from deploy.update import envfile
                if kwargs.get('env_recovery'):
                    envfile.recovery_text(c.env_path, kwargs['env_recovery'])
                world.event('restore_before')
                for row in world.rows.values():
                    row['State'].update(Running=False, Status='exited')
                for role in ('app-data', 'postgres-data', 'clickhouse-data'):
                    if ledger.snapshot['volumes'].get(role) == 'verified':
                        continue
                    if ledger.snapshot['volumes'].get(role) is not None:
                        ledger.volume_phase(role, 'clear_intent', restart=True)
                        phases = ('empty', 'extract_intent', 'extracted', 'verified')
                    else:
                        phases = ('clear_intent', 'empty', 'extract_intent', 'extracted', 'verified')
                    for phase in phases:
                        ledger.volume_phase(role, phase)
                world.extractions += 1
                world.generation = 'source'
                from deploy.update import envfile
                if kwargs.get('env_recovery'):
                    envfile.restore_changes(c.env_path, kwargs['env_recovery'])
                ledger.checkpoint('restore_data_committed')
                world.event('restore_after')
            def close(self):
                pass
        self.restore_patch = mock.patch('deploy.update.standalone_restore.StandaloneRestore', Restore)
        self.restore_patch.start()
        self.patches.append(self.restore_patch)
        # Durable protocol edges as well as the independent Docker boundaries.
        for name in ('checkpoint', 'begin', 'complete', 'seal_backup', 'decide_restore',
                     'decide_success', 'arm_previous_start', 'volume_phase'):
            original_method = getattr(RecoveryLedger, name)
            def edge(ledger, *args, _name=name, _method=original_method, **kwargs):
                suffix = str(args[0]) if args and isinstance(args[0], (str, int)) else ''
                world.event(_name + '_before_' + suffix)
                value = _method(ledger, *args, **kwargs)
                world.event(_name + '_after_' + suffix)
                return value
            patch = mock.patch.object(RecoveryLedger, name, edge)
            patch.start()
            self.patches.append(patch)
        original_write = c.state.write_journal
        def journal(record):
            world.event('journal_before_' + record.get('stage', ''))
            value = original_write(record)
            world.event('journal_after_' + record.get('stage', ''))
            return value
        c.state.write_journal = journal

    def close(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.patches.clear()
        self.case.doCleanups()

    def event(self, label):
        self.events.append(label)
        if self.cut == len(self.events) - 1:
            raise RuntimeCrashCut(label)

    def run(self):
        return self.engine.apply(lambda history: self.case.plan(), {'version': 'v1.1.0'})

    def resume(self):
        from deploy.update.apply import Updater
        engine = Updater(self.case.inst, self.settings, self.case.state, out=self.case.out)
        engine._automatic_runtime = lambda lifecycle, ledger, backup: self.runtime(self.case.inst, ledger, backup.b)
        with self.case.state.lock(automation=True):
            return engine.resume_automatic_locked(lifecycle=self.lifecycle, ledger=self.ledger)


class AutomaticRuntimeComponentTests(unittest.TestCase):
    """Shipping engine plus real on-disk ledger/inverse; fake Docker is explicit."""
    def setUp(self):
        self._component_world = None
        self.addCleanup(lambda: self._component_world.close() if self._component_world is not None else None)

    def world(self, **kwargs):
        previous = self._component_world
        if previous is not None:
            previous.close()
        self._component_world = RuntimeComponentWorld(self, **kwargs)
        return self._component_world

    def test_restarted_original_after_last_guard_is_not_recreated_as_target(self):
        from deploy.update.automatic_runtime import RuntimeRefused
        w = self.world()
        validate = w.guard.validate
        def revive(boundary, context):
            validate(boundary, context)
            if boundary == 'before_rollout':
                for row in w.rows.values():
                    row['State'].update(Running=True, Status='running', StartedAt='unexpected-revival')
        w.guard.validate = revive
        with self.assertRaises(RuntimeRefused):
            w.run()
        self.assertEqual(w.engine.automatic_outcome, 'restored')
        self.assertEqual(w.creates, ['v1.0.0'])  # B never erased the source roster
        self.assertEqual(w.extractions, 1)
        self.assertEqual(w.case.env_bytes(), w.case.original)

    def test_capacity_drop_during_last_inspection_forces_restore(self):
        from deploy.update.automatic_runtime import RuntimeRefused
        w = self.world()
        state = {'retained': False, 'free': 1000}
        w.lifecycle.retain_success = lambda *args: state.update(retained=True)
        runtime = w.runtime
        def factory(*args):
            result = runtime(*args)
            inspect = result._exact
            def slow(identity):
                row = inspect(identity)
                if state['retained'] and w.ledger.direction == 'forward_pending':
                    state['free'] = 0
                    w.time += 5
                return row
            result._exact = slow
            result.observer.free_bytes = lambda: state['free'] if result.ledger.direction == 'forward_pending' else 1000
            return result
        w.runtime = factory
        with self.assertRaises(RuntimeRefused) as caught:
            w.run()
        self.assertEqual(caught.exception.reason_code, ReasonCode.DISK_WATERMARK_ABORT)
        self.assertEqual(w.engine.automatic_outcome, 'restored')
        self.assertEqual(w.extractions, 1)
        self.assertEqual(w.engine.automatic_ledger.direction, 'restore_decided')

    def test_direct_start_retains_ports_policies_and_never_pulls_or_trials(self):
        w = self.world()
        w.run()
        self.assertEqual(w.engine.automatic_outcome, 'succeeded')
        self.assertEqual(w.creates, ['v1.1.0'])
        self.assertEqual(w.extractions, 0)
        self.assertEqual(w.ledger.direction, 'success_decided')
        self.assertIsNone(w.case.state.read_journal())
        self.assertTrue(all(row['HostConfig']['RestartPolicy']['Name'] == 'unless-stopped' for row in w.rows.values()))
        self.assertEqual(w.ledger.snapshot['target_model']['services']['app']['ports'],
                         [{'target': 8000, 'published': '8000'}])

    def test_postbackup_failure_restores_inside_the_original_lock(self):
        from deploy.update.readiness import NotReady
        w = self.world(fail_target=True)
        with self.assertRaises(NotReady):
            w.run()
        self.assertEqual(w.engine.automatic_outcome, 'restored')
        self.assertEqual(w.engine.automatic_ledger.direction, 'restore_decided')
        self.assertEqual(w.creates, ['v1.1.0', 'v1.0.0'])
        self.assertEqual(w.case.env_bytes(), w.case.original)
        self.assertEqual(w.extractions, 1)
        self.assertTrue(w.engine.automatic_ledger.snapshot['previous_start_armed'])
        self.assertIsNone(w.case.state.read_journal())
        receipts = w.case.state.history(w.case.inst.identity())
        self.assertEqual([(r['kind'], r['status']) for r in receipts],
                         [('apply', 'failed_during_rollout'), ('restore', 'restored')])

    def test_pre_rollout_guards_and_env_failures_also_choose_snapshot_restore(self):
        from deploy.update.orchestrator import GuardRefused
        for boundary in ('after_backup', 'before_env_commit', 'before_rollout'):
            with self.subTest(boundary=boundary):
                w = self.world()
                validate = w.guard.validate
                def refuse(at, context):
                    validate(at, context)
                    if at == boundary:
                        raise GuardRefused(ReasonCode.CONTROL_INVALID)
                w.guard.validate = refuse
                with self.assertRaises(GuardRefused):
                    w.run()
                self.assertEqual(w.engine.automatic_outcome, 'restored')
                self.assertEqual(w.extractions, 1)
                self.assertNotIn('v1.1.0', w.creates)
                self.assertEqual(w.case.env_bytes(), w.case.original)

    def test_metadata_error_after_success_decision_never_restores(self):
        w = self.world()
        w.lifecycle.complete_success = mock.Mock(side_effect=OSError('metadata fixture failure'))
        with self.assertRaises(Exception):
            w.run()
        self.assertEqual(w.engine.automatic_ledger.direction, 'success_decided')
        self.assertEqual(w.extractions, 0)
        w.lifecycle.complete_success = lambda *args: None
        w.resume()
        self.assertEqual(w.extractions, 0)
        self.assertEqual(w.creates, ['v1.1.0'])
        self.assertEqual(len(w.case.state.receipts()), 1)

    def test_resume_after_source_start_never_reextracts_or_erases_new_writes(self):
        w = self.world(fail_target=True)
        w.lifecycle.complete_restore = mock.Mock(side_effect=OSError('CAS fixture failure'))
        with self.assertRaises(Exception):
            w.run()
        self.assertEqual(w.extractions, 1)
        self.assertTrue(w.engine.automatic_ledger.snapshot['previous_start_armed'])
        w.generation = 'new-source-write'
        w.lifecycle.complete_restore = lambda *args: None
        w.resume()
        self.assertEqual(w.extractions, 1)
        self.assertEqual(w.generation, 'new-source-write')
        self.assertEqual(w.creates, ['v1.1.0', 'v1.0.0'])

    def test_manual_apply_stage_rollback_and_recover_refuse_automatic_journal(self):
        from deploy.update.orchestrator import GuardRefused
        w = self.world()
        w.case.state.write_journal({'automatic_transaction': True, 'kind': 'apply', 'stage': 'prepare'})
        updater = w.case.updater()
        for call in (lambda: updater.apply(lambda history: w.case.plan(), {'version': 'v1.1.0'}, allow_no_backup=True),
                     lambda: updater.apply(lambda history: w.case.plan(), {'version': 'v1.1.0'}, stage=True, allow_no_backup=True),
                     lambda: updater.rollback(None), lambda: updater.recover()):
            with self.subTest(call=call), self.assertRaises(GuardRefused) as caught:
                call()
            self.assertEqual(caught.exception.reason_code, ReasonCode.AUTOMATIC_RECOVERY_PENDING)

    def test_sealed_backup_publication_failure_still_loads_original_proof_and_restores(self):
        w = self.world()
        backup = w.backup.run_quiesced
        def fail_publication(*args, **kwargs):
            backup(*args, **kwargs)
            w.lifecycle.backup_raw = None
            raise OSError('fixture proof-leaf publication failure')
        w.backup.run_quiesced = fail_publication
        with self.assertRaises(OSError):
            w.run()
        self.assertEqual(w.engine.automatic_outcome, 'restored')
        self.assertEqual(w.extractions, 1)
        self.assertEqual(w.creates, ['v1.0.0'])
        import json
        self.assertEqual(json.loads(w.lifecycle.backup_raw)['schema_version'], 2)
        self.assertEqual(w.lifecycle.backup_raw, (w.ledger.directory / 'proof.json').read_bytes())

    def test_migration_stall_and_hard_deadline_failures_restore_the_original_snapshot(self):
        from dataclasses import replace
        from deploy.update.readiness import AutomaticNotReady
        for stalled in (True, False):
            with self.subTest(stalled=stalled):
                w = self.world()
                policy = replace(w.policy, migration_timeout_seconds=60, migration_stall_timeout_seconds=10)
                w.settings.automation_policy = w.case.inst.runner.policy = policy
                w.lifecycle.preflight_decision.estimate = replace(w.estimate, migration_seconds=30,
                    migration_deadline_seconds=30, migration_stall_timeout_seconds=10)
                compose = w.case.inst.compose_run
                def starting(*args, **kwargs):
                    result = compose(*args, **kwargs)
                    if args[0] == 'create' and w.case.inst.env['FUTURE_AGI_VERSION'] == 'v1.1.0':
                        for row in w.rows.values():
                            if row['Config']['Labels']['com.docker.compose.service'] == 'app':
                                row['State']['Health']['Status'] = 'starting'
                    return result
                w.case.inst.compose_run = starting
                factory = w.runtime
                def runtime(*args):
                    adapter = factory(*args)
                    adapter.observer.progress = lambda roster: ('migrating', 'tfc.0002', 'fixed' if stalled else str(w.time))
                    return adapter
                w.runtime = runtime
                with self.assertRaises(AutomaticNotReady) as caught:
                    w.run()
                self.assertEqual(caught.exception.reason_code,
                                 ReasonCode.MIGRATION_STALLED if stalled else ReasonCode.MIGRATION_EXCEEDS_WINDOW)
                self.assertEqual(w.engine.automatic_outcome, 'restored')
                self.assertEqual(w.extractions, 1)

    def test_disk_watermark_failure_takes_the_same_snapshot_restore_path(self):
        from deploy.update.readiness import AutomaticNotReady
        w = self.world()
        factory = w.runtime
        def runtime(*args):
            adapter = factory(*args)
            adapter.observer.free_bytes = lambda: 0 if w.generation == 'candidate' else 1000
            return adapter
        w.runtime = runtime
        with self.assertRaises(AutomaticNotReady) as caught:
            w.run()
        self.assertEqual(caught.exception.reason_code, ReasonCode.DISK_WATERMARK_ABORT)
        self.assertEqual(w.engine.automatic_outcome, 'restored')
        self.assertEqual(w.extractions, 1)

    def test_model_change_after_guard_never_starts_an_unapproved_command(self):
        from deploy.update.orchestrator import GuardRefused
        w = self.world()
        validate, config = w.guard.validate, w.case.inst.config
        def changed(boundary, context):
            validate(boundary, context)
            if boundary == 'before_rollout':
                def drift(refresh=False):
                    model = config(refresh=refresh)
                    model['services']['app']['command'] = ['operator-edit']
                    return model
                w.case.inst.config = drift
        w.guard.validate = changed
        with self.assertRaises(GuardRefused) as caught:
            w.run()
        self.assertEqual(caught.exception.reason_code, ReasonCode.RECOVERY_REQUIRED)
        self.assertEqual(w.creates, [])
        self.assertEqual(w.starts, [])
        self.assertIn('command', w.case.inst.config()['services']['app'])

    def test_manual_commands_refuse_the_prejournal_enrolled_attempt_gap(self):
        from dataclasses import replace
        from deploy.tests.test_update_runner_state import registration
        from deploy.update.orchestrator import GuardRefused
        from deploy.update.runner_state import RunnerStateStore, RunnerState
        w = self.world()
        alias = w.case.root / 'selected.env'
        alias.symlink_to(w.case.env_path.name)
        alternate = w.case.root / 'identical.env'
        alternate.write_bytes(w.case.env_bytes())
        store = RunnerStateStore(w.case.root, alias)
        reg = registration(store)
        reg = replace(reg, binding=replace(reg.binding, env_alias=alias))
        store.register(reg)
        with store._lock('state.lock') as directory:
            store._write(directory, 'state.json', RunnerState(
                installation_fingerprint=reg.binding.installation_fingerprint,
                active_attempt_id='a' * 32, active_operation_token='token', baseline_head='b' * 64))
        for target in (w.case.env_path, alternate, w.case.root / 'missing.env'):
            with self.subTest(target=target):
                alias.unlink()
                alias.symlink_to(target.name)
                # A fresh manual invocation may have resolved the new target or
                # fail discovery. Neither makes original authority disappear.
                w.case.inst.env_file = target
                self.assertTrue(w.case.state.automatic_pending(w.case.inst))
                for call in (lambda: w.case.updater().recover(), lambda: w.case.updater().rollback(None),
                             lambda: w.case.updater().apply(lambda h: w.case.plan(), {'version': 'v1.1.0'}, allow_no_backup=True)):
                    with self.assertRaises(GuardRefused) as caught:
                        call()
                    self.assertEqual(caught.exception.reason_code, ReasonCode.AUTOMATIC_RECOVERY_PENDING)
        self.assertIsNone(w.case.state.read_journal())
        self.assertEqual(w.starts, [])

    def test_dependency_startup_watermark_aborts_before_any_target_app_start(self):
        from deploy.update.automatic_runtime import RuntimeRefused
        w = self.world()
        factory, compose = w.runtime, w.case.inst.compose_run
        def starting(*args, **kwargs):
            result = compose(*args, **kwargs)
            if w.case.inst.env['FUTURE_AGI_VERSION'] == 'v1.1.0':
                for row in w.rows.values():
                    row['State']['Health']['Status'] = 'starting'
            return result
        w.case.inst.compose_run = starting
        reads = []
        def runtime(*args):
            adapter = factory(*args)
            def free():
                reads.append(w.time)
                return 0 if w.case.inst.env['FUTURE_AGI_VERSION'] == 'v1.1.0' and w.time >= 2 else 1000
            adapter.observer.free_bytes = free
            return adapter
        w.runtime = runtime
        with self.assertRaises(RuntimeRefused) as caught:
            w.run()
        self.assertEqual(caught.exception.reason_code, ReasonCode.DISK_WATERMARK_ABORT)
        self.assertIn(2, reads)
        self.assertFalse(any(image == w.case.inst.registry['futureagi/standalone:v1.1.0'] for image, _ in w.starts))
        self.assertEqual(w.engine.automatic_outcome, 'restored')
        self.assertEqual(w.extractions, 1)

    def test_readonly_inspection_timeout_restores_without_resetting_mutation_authority(self):
        from deploy.update.automatic_runtime import AutomaticRuntime
        from deploy.update.docker_host import ObservationFailure
        from deploy.update.processes import ProcessSupervisor
        w = self.world()
        supervisor = ProcessSupervisor(w.policy, docker_context='colima-fagi-update', environment={}, grace_seconds=.05)
        w.case.inst.runner = supervisor
        original = AutomaticRuntime._read_installation
        triggered = []
        def read(adapter):
            inst = original(adapter)
            docker = inst.docker_run
            def inspect(*args, **kwargs):
                if args[0] == 'inspect' and w.generation == 'candidate' and not triggered and adapter.ledger.phase == 'target_verifying':
                    triggered.append(adapter.ledger.snapshot['pending'])
                    return inst.runner([sys.executable, '-I', '-c', 'import time; time.sleep(3)'], timeout=.02)
                return docker(*args, **kwargs)
            inst.docker_run = inspect
            return inst
        with mock.patch.object(AutomaticRuntime, '_read_installation', read), self.assertRaises(ObservationFailure):
            w.run()
        self.assertEqual(triggered, [None])  # Every preceding mutation was acknowledged.
        self.assertFalse(supervisor.uncertain)
        self.assertEqual(w.engine.automatic_outcome, 'restored')
        self.assertEqual(w.extractions, 1)
        self.assertEqual(w.generation, 'source_running')

    def test_real_guard_fresh_timeout_after_seal_cannot_poison_restore_authority(self):
        from dataclasses import replace
        from deploy.tests.test_update_compatibility import restore_context
        from deploy.update.orchestrator import ScheduledGuard, fingerprints
        from deploy.update.docker_host import bounded_installation
        from deploy.update.orchestrator import GuardRefused
        from deploy.update.processes import ProcessSupervisor
        from deploy.update.install import Installation
        w = self.world()
        supervisor = ProcessSupervisor(w.policy, docker_context='colima-fagi-update', environment={}, grace_seconds=.05)
        w.case.inst.runner = supervisor
        w.case.inst.environ = dict(supervisor.environment)
        prepared = restore_context()[0]
        (w.case.root / 'docker-compose.yml').write_text('services: {}\n')
        binding = replace(prepared.binding, checkout=w.case.root, env_alias=w.case.env_path,
            env_target=w.case.env_path, compose_files=tuple(w.case.inst._ownership_files()),
            compose_project=w.case.inst.project, daemon_identity='daemon', docker_context='colima-fagi-update',
            host_uid=__import__('os').geteuid())
        prepared = replace(prepared, binding=binding,
            config_fingerprint=fingerprints(w.case.inst)[0], profile_fingerprint=fingerprints(w.case.inst)[1],
            deployment_fingerprint=fingerprints(w.case.inst)[2])
        guard = ScheduledGuard(prepared, discovery=None, store=None, facts=None, make_plan=None,
            control_generation=0, window_occurrence='always-test', automatic=w.lifecycle)
        real_validate = w.guard.validate
        fresh = w.case.inst.fresh
        triggered = []
        def independent():
            import copy
            inst = copy.copy(w.case.inst)
            # Bind this method to the independent installation, so its actual
            # runner (not the fixture's closure) executes the read-only client.
            inst.docker = [sys.executable, '-I', '-c', 'import time; time.sleep(3)']
            inst.docker_run = Installation.docker_run.__get__(inst)
            inst.run = Installation.run.__get__(inst)
            return inst
        def validate(boundary, context):
            real_validate(boundary, context)
            if boundary == 'after_backup':
                w.case.inst.fresh = independent
                triggered.append(w.ledger.snapshot['pending'])
                supervisor.deadline = __import__('time').monotonic() + .02
                guard._fresh(w.engine, boundary)
        def bounded(inst, timeout=60, **kwargs):
            clone = bounded_installation(inst, timeout, **kwargs)
            clone.runner.deadline = __import__('time').monotonic() + .02
            # copy.copy retains a fixture MethodType's receiver; the production
            # Installation method is rebound to the actual bounded clone.
            if getattr(inst.docker_run, '__func__', None) is Installation.docker_run:
                clone.docker_run = Installation.docker_run.__get__(clone)
                clone.run = Installation.run.__get__(clone)
            return clone
        w.guard.validate = validate
        with mock.patch('deploy.update.docker_host.bounded_installation', bounded), \
                mock.patch('deploy.update.orchestrator.check_deployment'), self.assertRaises(GuardRefused):
            w.run()
        w.case.inst.fresh = fresh
        self.assertEqual(triggered, [None])
        self.assertFalse(supervisor.uncertain)
        self.assertEqual(w.engine.automatic_outcome, 'restored')
        self.assertEqual(w.extractions, 1)
        self.assertEqual(w.generation, 'source_running')

    def test_watermark_remains_active_during_full_observation_and_success_retention(self):
        from deploy.update.automatic_runtime import RuntimeRefused
        for boundary in ('observation', 'retention', 'validation'):
            with self.subTest(boundary=boundary):
                w = self.world()
                free = [1000]
                runtime = w.runtime
                def guarded(*args):
                    adapter = runtime(*args)
                    adapter.observer.free_bytes = lambda: free[0]
                    return adapter
                w.runtime = guarded
                observe = w.lifecycle._observation
                def observed(*args):
                    value = observe(*args)
                    if args[-1] == 'success':
                        w.time += 5
                        free[0] = 0
                    else:
                        free[0] = 1000
                    return value
                if boundary == 'observation':
                    w.lifecycle._observation = observed
                elif boundary == 'retention':
                    w.lifecycle.retain_success.side_effect = lambda *args: free.__setitem__(0, 0)
                else:
                    w.lifecycle.baseline.validate_success_observation = lambda *args: free.__setitem__(0, 0)
                # Source recovery has its independently admitted reserve.
                original_prepare = w.lifecycle.prepare_restore
                def prepare(*args):
                    free[0] = 1000
                    return original_prepare(*args)
                w.lifecycle.prepare_restore = prepare
                with self.assertRaises(RuntimeRefused) as caught:
                    w.run()
                self.assertEqual(caught.exception.reason_code, ReasonCode.DISK_WATERMARK_ABORT)
                self.assertEqual(w.engine.automatic_outcome, 'restored')
                self.assertEqual(w.engine.automatic_ledger.direction, 'restore_decided')
                self.assertEqual(w.extractions, 1)
                self.assertFalse(any(w.case.state.load_receipt(path)['status'] == 'succeeded'
                                     for path in w.case.state.receipts()))

    def test_real_mutation_timeout_still_blocks_restore_with_pending_start(self):
        from deploy.update.processes import ProcessSupervisor
        from deploy.update.orchestrator import GuardRefused
        w = self.world()
        supervisor = ProcessSupervisor(w.policy, docker_context='colima-fagi-update', environment={}, grace_seconds=.05)
        w.case.inst.runner = supervisor
        docker = w.case.inst.docker_run
        def start(*args, **kwargs):
            if args[0] == 'start':
                return supervisor([sys.executable, '-I', '-c', 'import time; time.sleep(3)'], timeout=.02)
            return docker(*args, **kwargs)
        w.case.inst.docker_run = start
        with self.assertRaises(GuardRefused):
            w.run()
        self.assertTrue(supervisor.uncertain)
        self.assertEqual(w.engine.automatic_outcome, 'recovery_required')
        self.assertEqual(w.engine.automatic_ledger.snapshot['pending']['kind'], 'start')
        self.assertEqual(w.extractions, 0)

    def test_manual_check_of_automatic_journal_never_projects_env_only_recovery(self):
        from deploy.update.__main__ import _check_inputs
        from deploy.update.orchestrator import GuardRefused
        w = self.world()
        validate = w.guard.validate
        def cut(boundary, context):
            if boundary == 'before_rollout':
                raise RuntimeCrashCut(boundary)
            validate(boundary, context)
        w.guard.validate = cut
        with self.assertRaises(RuntimeCrashCut):
            w.run()
        before, journal = w.case.env_bytes(), w.case.state.read_journal()
        with self.assertRaises(GuardRefused) as caught:
            _check_inputs(w.case.inst, w.case.state)
        self.assertEqual(caught.exception.reason_code, ReasonCode.AUTOMATIC_RECOVERY_PENDING)
        self.assertEqual(w.case.inst.env['FUTURE_AGI_VERSION'], 'v1.1.0')
        self.assertEqual(w.case.env_bytes(), before)
        self.assertEqual(w.case.state.read_journal(), journal)

    def test_public_success_finalization_needs_no_existing_receipt_or_runtime_mutation(self):
        from deploy.update.apply import Updater
        w = self.world()
        write = w.case.state.write_receipt
        w.case.state.write_receipt = mock.Mock(side_effect=OSError('receipt unavailable'))
        with self.assertRaises(Exception):
            w.run()
        self.assertEqual(w.engine.automatic_ledger.direction, 'success_decided')
        self.assertEqual(w.case.state.receipts(), [])
        w.case.state.write_receipt = write
        engine = Updater(w.case.inst, w.settings, w.case.state, out=w.case.out)
        creates, starts = list(w.creates), list(w.starts)
        with w.case.state.lock(automation=True):
            path = engine.finalize_automatic_success_locked(lifecycle=w.lifecycle, ledger=w.engine.automatic_ledger)
            again = engine.finalize_automatic_success_locked(lifecycle=w.lifecycle, ledger=engine.automatic_ledger)
        self.assertIsInstance(path, Path)
        self.assertEqual(path, again)
        self.assertEqual(w.case.state.load_receipt(path)['status'], 'succeeded')
        self.assertEqual(len(w.case.state.receipts()), 1)
        self.assertEqual(w.creates, creates)
        self.assertEqual(w.starts, starts)
        self.assertEqual(w.extractions, 0)

    def test_success_evidence_is_retained_before_decision_and_a_cut_still_restores(self):
        w = self.world()
        retained = []
        def retain(installation, observation):
            self.assertEqual(w.ledger.direction, 'forward_pending')
            self.assertIsNone(w.ledger.snapshot['pending'])
            retained.append(observation.facts.runtime_observation.sha256)
            raise RuntimeCrashCut('after_success_evidence_before_decision')
        w.lifecycle.retain_success.side_effect = retain
        with self.assertRaises(RuntimeCrashCut):
            w.run()
        self.assertEqual(len(retained), 1)
        self.assertEqual(w.ledger.direction, 'forward_pending')
        self.assertEqual(w.case.state.receipts(), [])
        w.resume()
        self.assertEqual(w.extractions, 1)
        self.assertEqual(w.creates, ['v1.1.0', 'v1.0.0'])
        self.assertEqual(w.generation, 'source_running')
        self.assertEqual(w.engine._open_automatic_ledger(w.ledger).direction, 'restore_decided')

    def test_failed_target_logs_are_bounded_private_immutable_and_optional(self):
        import json
        import stat
        from deploy.update.automatic_runtime import AutomaticRuntime
        from deploy.update.readiness import NotReady
        from deploy.update.docker_host import bounded_installation
        w = self.world(fail_target=True)
        real = bounded_installation
        def bounded(installation, timeout, **kwargs):
            inst = real(installation, timeout, **kwargs)
            docker = inst.docker_run
            def logs(*args, **kw):
                if args[0] == 'logs':
                    self.assertEqual(timeout, 5)
                    self.assertEqual(inst.runner.max_output, 16384)
                    from deploy.update.install import CommandResult
                    return CommandResult([], 0, 'private candidate diagnostic', '')
                return docker(*args, **kw)
            inst.docker_run = logs
            return inst
        with mock.patch('deploy.update.docker_host.bounded_installation', bounded), self.assertRaises(NotReady):
            w.run()
        leaf = w.engine.automatic_ledger.directory / 'failed-target-logs.json'
        raw = leaf.read_bytes()
        self.assertLessEqual(len(raw), 128 * 1024)
        self.assertEqual(stat.S_IMODE(leaf.stat().st_mode), 0o600)
        self.assertEqual(set(json.loads(raw)['services']), {'app', 'postgres', 'clickhouse'})
        self.assertTrue(all(row['status'] == 'retained' for row in json.loads(raw)['services'].values()))
        self.assertEqual(w.engine.automatic_outcome, 'restored')
        AutomaticRuntime(w.case.inst, w.engine.automatic_ledger, w.backup.b).retain_failed_logs()
        self.assertEqual(leaf.read_bytes(), raw)
        for receipt in w.case.state.receipts():
            self.assertNotIn('private candidate diagnostic', receipt.read_text())

    def test_crash_cuts_reopen_same_direction_without_target_retry(self):
        # Every fake-Docker/guard/backup/restore/metadata boundary on both paths.
        # Unacknowledged create/start is intentionally recovery_required, never
        # guessed safe because the local client disappeared.
        from deploy.update.orchestrator import GuardRefused
        from deploy.update.readiness import NotReady
        from deploy.update.recovery import RecoveryLedger
        total = 0
        for failure in (False, True):
            baseline = self.world(fail_target=failure)
            try:
                baseline.run()
            except NotReady:
                pass
            events = list(baseline.events)
            for cut, label in enumerate(events):
                with self.subTest(failure=failure, cut=cut, label=label):
                    w = self.world(fail_target=failure, cut=cut)
                    with self.assertRaises(RuntimeCrashCut):
                        w.run()
                    w.cut = None
                    if w.ledger is None:
                        self.assertEqual(w.creates, [])
                        continue
                    before_creates = w.creates.count('v1.1.0')
                    try:
                        w.resume()
                    except GuardRefused as exc:
                        self.assertEqual(exc.reason_code, ReasonCode.RECOVERY_REQUIRED)
                        blocked = RecoveryLedger.open(w.ledger.directory)
                        self.assertIsNotNone(blocked.snapshot['pending'],
                            'A classified cut with no unresolved operation must finish recovery')
                    current = RecoveryLedger.open(w.ledger.directory)
                    self.assertLessEqual(w.creates.count('v1.1.0'), before_creates)
                    if current.direction == 'success_decided':
                        self.assertEqual(w.extractions, 0)
                    if current.snapshot['previous_start_armed']:
                        extracted = w.extractions
                        w.generation = 'post-start-write'
                        try:
                            w.resume()
                        except GuardRefused:
                            pass
                        self.assertEqual(w.extractions, extracted)
                        self.assertEqual(w.generation, 'post-start-write')
                    total += 1
        self.assertGreater(total, 100)
        print('runtime_component_crash_cuts=%d' % total)


class FullObserverBudgetTests(unittest.TestCase):
    """Real readiness + full observer, serial fake-clock Docker data only."""
    def fixture(self, *, previous=False, fault=None, release_version=None):
        import tempfile
        from types import SimpleNamespace as NS
        from deploy.tests.test_update_standalone_state import RuntimeFixture, DOCUMENT, NOW
        from deploy.update import standalone_state as state, docker_host as host
        from deploy.update.automatic_runtime import AutomaticRuntime
        from deploy.update.discovery import DiscoveredRelease
        from deploy.update.contracts import AutomationPolicy, AutomaticUpdateEstimate, VerificationSnapshot
        from deploy.update.manifest import canonical_json, manifest_hash
        from deploy.update.readiness import RuntimeSample
        from deploy.update.baseline import normalise_observation
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        inst = RuntimeFixture(Path(temp.name).resolve())
        if release_version is not None:
            from deploy.tests.test_update_manifest import valid_manifest
            inst.manifest = valid_manifest(release_version)
            inst.manifest['minimum_version'] = 'v1.0.0'
            component = inst.manifest['components']['standalone']
            inst.model['services']['app']['image'] = component['image']
            inst.img_app['repo_digests'] = [component['image'].split(':')[0] + '@' + component['digest']]
            inst.env_file.write_text('FUTURE_AGI_VERSION=' + release_version + '\n')
            inst.env['FUTURE_AGI_VERSION'] = release_version
        ticks, free = [0.0], [1000]
        policy = AutomationPolicy(qualification_policy='standalone-cold-restore-v1', probe_timeout_seconds=90,
            wait_timeout_seconds=60, recovery_startup_timeout_seconds=60, stability_period_seconds=600,
            migration_timeout_seconds=1, migration_stall_timeout_seconds=1, disk_watermark_reserve_bytes=10,
            backup_timeout_seconds=1, restore_timeout_seconds=1,
            quiescence_reserve_seconds=60, metadata_reserve_seconds=60)
        estimate = AutomaticUpdateEstimate(1, 0, 1, 0, 100, 100, 10, 782, 781, 1563, 0, 1)
        inst.runner = NS(policy=policy, deadline=781 if previous else 782)
        verified = VerificationSnapshot(True, 'synthetic', 'fixture', 'fixture', 'unit-test-only', NOW)
        release = DiscoveredRelease(canonical_json(inst.manifest), b'fixture', manifest_hash(inst.manifest), 'd' * 64, verified)
        execution = {'files': {'docker-compose.yml': state._sha(inst.compose_file.read_bytes())}}
        def sleep(seconds):
            ticks[0] += seconds
            if fault is not None:
                fault(inst, ticks[0], free)
        observer = state.StandaloneStateObserver(NS(binding=inst.binding), timeout=180,
            clock=lambda: NOW, monotonic=lambda: ticks[0], sleeper=sleep)
        life = NS(observer=observer, registration=NS(binding=inst.binding), clock=lambda: NOW)
        life._observation = lambda installation, evidence, boundary: normalise_observation(life.observer.observe(
            installation, current_release=evidence, execution_deployment=execution,
            boundary='reconcile' if boundary == 'restored' else boundary))
        ledger = NS(direction='restore_decided' if previous else 'forward_pending', snapshot={'run_id': 'a' * 32, 'pending': None})
        runtime = AutomaticRuntime(inst, ledger, {}, observer=NS(free_bytes=lambda: free[0]),
            clock=lambda: ticks[0], sleep=sleep)
        runtime._safe = mock.Mock()
        runtime._stores = mock.Mock()
        def sample(roster, **kwargs):
            return RuntimeSample('ready', '', '', free[0], {cid: (r['State']['Running'],
                r['State']['Health']['Status'] == 'healthy', r['RestartCount'], r['State']['StartedAt'])
                for cid, r in inst.records.items()})
        runtime.sample = sample
        roster = {row['service']: {'Id': row['id']} for row in inst.conts}
        storage = host.VolumeStorageObservation('available', tuple(host.VolumeFilesystem(role, 'fixture-' + role,
            '2049:' + str(i), '8:1', 'ext4', 'e' * 64, 1000, 99999, None)
            for i, role in enumerate(host.VOLUME_ROLES, 1)))
        patches = [mock.patch.object(state, 'bounded_installation', side_effect=lambda obj, *args: obj),
            mock.patch.object(state, 'check_binding'), mock.patch.object(state, 'inspect_volume_storage', return_value=storage),
            mock.patch.object(state.StandaloneStateObserver, '_probe', return_value=canonical_json(DOCUMENT))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return NS(inst=inst, runtime=runtime, life=life, release=release, policy=policy, estimate=estimate,
            ticks=ticks, free=free, roster=roster, probe=state.StandaloneStateObserver._probe, previous=previous,
            execution=execution)

    def wait(self, f, session):
        from deploy.update.readiness import AutomaticReadiness
        return AutomaticReadiness(clock=lambda: f.ticks[0], sleep=f.runtime.sleep).wait(
            lambda: f.runtime.sample(f.roster), policy=f.policy, estimate=f.estimate,
            deadline=f.inst.runner.deadline, previous=f.previous,
            expected_ids={r['Id'] for r in f.roster.values()}, ready_callback=lambda: session.ready(f.roster))

    def test_one_genuine_600_second_period_fits_forward_and_restore_frozen_budgets(self):
        for previous in (False, True):
            with self.subTest(previous=previous):
                f = self.fixture(previous=previous)
                with f.runtime.observation_session(f.life, f.release, 'restored' if previous else 'success',
                        f.policy, f.estimate, previous=previous) as session:
                    self.wait(f, session)
                    first = session.result
                    again = f.life._observation(f.inst, f.release, 'restored' if previous else 'success')
                    self.assertTrue(first.facts.runtime_observation.ready)
                    self.assertTrue(first.facts.runtime_observation.stable)
                    self.assertEqual(first.facts.runtime_observation.sha256, again.facts.runtime_observation.sha256)
                    self.assertEqual(f.ticks[0], 600)
                    self.assertLess(f.ticks[0], f.inst.runner.deadline)
                    self.assertEqual(f.probe.call_count, 2)  # first + final migration/readiness probe, no duplicate
                self.assertIsNot(f.life.observer, session)
                self.assertIsNone(session.cached)

    def test_shipping_engine_shares_the_period_and_restore_metadata_reuses_its_evidence(self):
        from deploy.update.baseline import normalise_observation
        from deploy.update.readiness import NotReady
        for previous in (False, True):
            with self.subTest(previous=previous):
                f = self.fixture(previous=previous, release_version='v1.0.0' if previous else 'v1.1.0')
                w = RuntimeComponentWorld(self, fail_target=previous)
                try:
                    w.settings.automation_policy = w.case.inst.runner.policy = f.policy
                    w.case.inst.runner.deadline = f.policy.required_cold_budget_seconds + 5
                    f.life.observer.monotonic = lambda: w.time
                    f.life.observer.sleeper = lambda seconds: setattr(w, 'time', w.time + seconds)
                    w.lifecycle.observer = f.life.observer
                    calls = []
                    def observe(installation, evidence, boundary):
                        calls.append((boundary, w.time))
                        return normalise_observation(w.lifecycle.observer.observe(f.inst, current_release=f.release,
                            execution_deployment=f.execution, boundary='reconcile' if boundary == 'restored' else boundary))
                    w.lifecycle._observation = observe
                    # The actual lifecycle calls _observation again when sealing
                    # restored metadata. This must consume the SAME checked full
                    # observation, never a third serial 600-second health wait.
                    w.lifecycle.complete_restore = lambda *args: observe(w.case.inst, f.release, 'restored')
                    with mock.patch('time.monotonic', side_effect=lambda: w.time):
                        if previous:
                            with self.assertRaises(NotReady):
                                w.run()
                        else:
                            w.run()
                    self.assertEqual(w.engine.automatic_outcome, 'restored' if previous else 'succeeded')
                    self.assertEqual(w.extractions, 1 if previous else 0)
                    self.assertEqual(w.time, 600)
                    self.assertEqual(f.policy.required_forward_budget_seconds, 782)
                    self.assertEqual(f.policy.required_recovery_budget_seconds, 781)
                    self.assertEqual(f.probe.call_count, 2)
                    self.assertEqual(len(calls), 2 if previous else 1)
                    self.assertEqual(w.creates, ['v1.1.0', 'v1.0.0'] if previous else ['v1.1.0'])
                finally:
                    w.close()

    def test_full_observer_interval_still_guards_watermark_health_and_restarts(self):
        from deploy.update.orchestrator import GuardRefused
        for fault_name in ('disk', 'health', 'restart', 'start'):
            def fault(inst, now, free):
                if now >= 301:
                    if fault_name == 'disk': free[0] = 0
                    elif fault_name == 'health': inst.records['1' * 64]['State']['Health']['Status'] = 'unhealthy'
                    elif fault_name == 'restart': inst.records['1' * 64]['RestartCount'] = 1
                    else: inst.records['1' * 64]['State']['StartedAt'] = 'restarted'
            with self.subTest(fault=fault_name):
                f = self.fixture(fault=fault)
                with f.runtime.observation_session(f.life, f.release, 'success', f.policy, f.estimate) as session, \
                        self.assertRaises(GuardRefused) as caught:
                    self.wait(f, session)
                self.assertEqual(caught.exception.reason_code,
                    ReasonCode.DISK_WATERMARK_ABORT if fault_name == 'disk' else ReasonCode.READINESS_FAILED)
                self.assertEqual(f.ticks[0], 301)

    def test_a_cached_period_is_not_authority_for_a_new_attempt_or_a_different_policy(self):
        from deploy.update.automatic_runtime import RuntimeRefused
        f = self.fixture(previous=True)
        with f.runtime.observation_session(f.life, f.release, 'restored', f.policy, f.estimate, previous=True) as session:
            self.wait(f, session)
            f.runtime.ledger.snapshot['run_id'] = 'new-run'
            with self.assertRaises(RuntimeRefused) as caught:
                f.life._observation(f.inst, f.release, 'restored')
            self.assertEqual(caught.exception.reason_code, ReasonCode.RESTORE_CHILD_AMBIGUOUS)
        f = self.fixture()
        f.life.observer.stability_period_seconds = 601
        with f.runtime.observation_session(f.life, f.release, 'success', f.policy, f.estimate) as session, \
                self.assertRaises(RuntimeRefused) as caught:
            self.wait(f, session)
        self.assertEqual(caught.exception.reason_code, ReasonCode.READINESS_FAILED)
        f.probe.assert_not_called()

    def test_completed_observation_cannot_cross_an_attempt_roster_or_configuration(self):
        from deploy.update.automatic_runtime import RuntimeRefused
        f = self.fixture(previous=True)
        with f.runtime.observation_session(f.life, f.release, 'restored', f.policy, f.estimate, previous=True) as session:
            self.wait(f, session)
            f.inst.env_file.write_text('FUTURE_AGI_VERSION=operator\n')
            f.inst.env['FUTURE_AGI_VERSION'] = 'operator'
            with self.assertRaises(RuntimeRefused) as caught:
                f.life._observation(f.inst, f.release, 'restored')
            self.assertEqual(caught.exception.reason_code, ReasonCode.CONFIGURATION_CHANGED)


class ColdScheduledGuardTests(unittest.TestCase):
    """Actual v2 admission/guard, synthetic signed-input envelopes only."""
    def setUp(self):
        import tempfile
        from types import SimpleNamespace as NS
        from deploy.tests.test_update_compatibility import restore_context, NOW
        from deploy.update.orchestrator import ScheduledGuard
        from deploy.update.compatibility import plain
        self.prepared, self.obs, self.controls, self.proof = restore_context()
        # Rebind the synthetic envelope to the real fixture file owner. The
        # production proof reader must not waive uid checks for test fixtures.
        import hashlib
        import json
        import os
        from dataclasses import replace
        from deploy.update.contracts import installation_binding_hash
        from deploy.update.manifest import canonical_json
        self.prepared = replace(self.prepared, binding=replace(self.prepared.binding, host_uid=os.geteuid()))
        binding_hash = installation_binding_hash(self.prepared.binding)
        runtime = replace(self.obs.runtime_observation, binding_sha256=binding_hash)
        hold = replace(self.obs.cold_hold, binding_sha256=binding_hash, source_observation_sha256=runtime.sha256)
        document = json.loads(self.proof)
        document.update(binding_sha256=binding_hash, source_observation_sha256=runtime.sha256)
        self.proof = canonical_json(document)
        seal = replace(self.obs.backup_seal, binding_sha256=binding_hash,
            source_observation_sha256=runtime.sha256, proof_sha256=hashlib.sha256(self.proof).hexdigest())
        self.obs = replace(self.obs, runtime_observation=runtime, cold_hold=hold, backup_seal=seal,
                           binding_sha256=binding_hash)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        env = self.directory / '.env'
        env.write_bytes(b'FUTURE_AGI_VERSION=v1.51.0\n')
        store = mock.Mock()
        store.read_control.return_value = NS(generation=0, effective=lambda *args: 'enabled')
        store.read_state.return_value = NS(failed_target_latch=None, active_attempt_id=None)
        self.engine = NS(settings=NS(automation_policy=self.controls.policy), active_record={'run_id': self.obs.run_id},
            state=NS(read_journal=lambda: None, latest_receipt=lambda: None), _history=lambda: [], _env_path=lambda: env)
        self.guard = ScheduledGuard(self.prepared, discovery=mock.Mock(), store=store,
            facts=lambda fresh, boundary: (self.obs, self.controls),
            make_plan=lambda *args: plain(self.prepared.plan), control_generation=0,
            window_occurrence='always-test', clock=lambda: NOW)
        self.guard._fresh = mock.Mock(return_value=NS())
        self.guard._refresh = mock.Mock()
        self.guard._reserve_backup = mock.Mock()
        self.context = NS(engine=self.engine, binding=self.prepared.binding, control_generation=0)
        self.addCleanup(self.guard.close)

    def test_reservation_binds_the_last_source_observation_not_a_replaced_earlier_sample(self):
        from dataclasses import replace
        from datetime import timedelta
        newer = replace(self.obs, runtime_observation=replace(self.obs.runtime_observation,
            observed_at=self.obs.runtime_observation.observed_at + timedelta(seconds=1)))
        self.guard.facts = mock.Mock(side_effect=[(self.obs, self.controls), (newer, self.controls)])
        self.guard.validate('before_backup', self.context)
        self.guard._reserve_backup.assert_called_once()
        self.assertEqual(self.guard._reserve_backup.call_args.args[1].runtime_observation.sha256,
                         newer.runtime_observation.sha256)

    def test_postbackup_guard_consumes_retained_source_hold_and_exact_original_proof(self):
        import os
        from types import SimpleNamespace as NS
        from deploy.update.backup_proof import BackupExpectation
        proof_file = self.directory / 'proof.json'
        proof_file.write_bytes(self.proof)
        proof_file.chmod(0o600)
        self.guard.proof_directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        self.guard.expectation = BackupExpectation(self.obs.attempt_id, self.obs.installation_fingerprint,
            self.prepared.plan['manifest_hash'], self.obs.runtime_observation.release_version,
            self.obs.reserved_at, ('clickhouse', 'object_store', 'postgres_app', 'shared_files', 'temporal'))
        self.guard.attempt = NS(attempt_id=self.obs.attempt_id, operation_token=self.obs.operation_token,
                               reserved_at=self.obs.reserved_at)
        self.guard.facts = mock.Mock(return_value=(self.obs, self.controls))
        self.guard.validate('after_backup', self.context)
        self.assertEqual(self.guard.facts.call_count, 2)
        self.assertTrue(all(call.args[1] == 'after_backup' for call in self.guard.facts.call_args_list))
        self.guard._reserve_backup.assert_not_called()
        self.assertEqual(self.guard.proof.raw, self.proof)
        proof_file.write_bytes(self.proof.replace(b'"verification":"passed"', b'"verification":"failed"'))
        with self.assertRaises(Exception):
            self.guard.validate('after_backup', self.context)

    def test_legacy_proofs_do_not_gain_new_forward_permission(self):
        from dataclasses import replace
        from deploy.update.orchestrator import GuardRefused
        self.obs = replace(self.obs, release_proof=b'{}')
        with self.assertRaises(GuardRefused):
            self.guard.validate('before_backup', self.context)
        self.guard._reserve_backup.assert_not_called()


class HostRuntimeProgressTests(unittest.TestCase):
    def observer(self):
        from types import SimpleNamespace as NS
        from deploy.update.automatic_runtime import HostRuntimeObserver
        self.inst = mock.Mock()
        binding = {'images': {'app': 'sha256:' + '1' * 64},
            'volumes': {role: {'Name': role} for role in ('app-data', 'postgres-data', 'clickhouse-data')},
            'volume_root_ids': {role: '1:1' for role in ('app-data', 'postgres-data', 'clickhouse-data')}}
        return HostRuntimeObserver(self.inst, NS(binding=NS()), binding)

    def test_fixed_pg_queries_and_product_phase_and_migration_are_the_only_progress(self):
        import json
        from types import SimpleNamespace as NS
        from deploy.update.automatic_runtime import PROGRESS_SQL
        observer = self.observer()
        postgres = {'index': [{'blocks_done': 1}, {'blocks_done': 2}], 'cluster': [],
                    'vacuum': [], 'analyze': [], 'copy': [], 'activity': []}
        def answer(*args, **kwargs):
            if args[0] == 'logs':
                return NS(returncode=0, stdout='Applying tfc.0020_test...')
            if args[-1] == '/run/futureagi/status.json':
                return NS(returncode=0, stdout=json.dumps({'phase': 'migrating'}))
            self.assertEqual(args[-1], PROGRESS_SQL)
            self.assertIn('default_transaction_read_only=on', args[2])
            return NS(returncode=0, stdout=json.dumps(postgres))
        readonly = mock.Mock()
        readonly.docker_run.side_effect = answer
        with mock.patch('deploy.update.docker_host.bounded_installation', return_value=readonly):
            one = observer.progress({'app': {'Id': 'a' * 64}, 'postgres': {'Id': 'b' * 64}})
            postgres['index'].reverse()
            two = observer.progress({'app': {'Id': 'a' * 64}, 'postgres': {'Id': 'b' * 64}})
        self.assertEqual(one, two)  # changing enumeration order is not progress
        self.assertEqual(one[:2], ('migrating', 'tfc.0020_test'))
        self.assertEqual(len(one[2]), 64)
        for view in ('create_index', 'cluster', 'vacuum', 'analyze', 'copy'):
            self.assertIn('pg_stat_progress_' + view, PROGRESS_SQL)
        self.assertIn('pg_stat_activity', PROGRESS_SQL)
        self.assertNotIn('clock_timestamp', PROGRESS_SQL)

    def test_reused_volume_names_with_new_object_metadata_never_authorize_startup(self):
        import json
        from types import SimpleNamespace as NS
        from deploy.update.automatic_runtime import AutomaticRuntime, RuntimeRefused
        observer = self.observer()
        expected = {key: dict(row, CreatedAt='original') for key, row in observer.binding['volumes'].items()}
        observer.binding['volumes'] = expected
        rows = [dict(row) for row in expected.values()]
        installation = mock.Mock()
        installation.docker_run.side_effect = lambda *args: NS(stdout=json.dumps(rows))
        runtime = AutomaticRuntime(installation, NS(), observer.binding, observer=observer)
        runtime._read_installation = lambda: installation
        runtime._stores()
        rows[0]['CreatedAt'] = 'replacement'
        with self.assertRaises(RuntimeRefused) as caught:
            runtime._stores()
        self.assertEqual(caught.exception.reason_code, ReasonCode.RESTORE_VERIFICATION_FAILED)
        self.assertTrue(all(call.args[:2] == ('volume', 'inspect') for call in installation.docker_run.call_args_list))

    def test_watermark_uses_exact_daemon_volumes_not_checkout_or_image_free_space(self):
        from types import SimpleNamespace as NS
        from deploy.update.automatic_runtime import RuntimeRefused
        observer = self.observer()
        rows = [NS(role=role, name=role, root_identity='1:1', free_bytes=100 + i)
                for i, role in enumerate(('app-data', 'postgres-data', 'clickhouse-data'))]
        with mock.patch('deploy.update.docker_host.inspect_volume_storage',
                        return_value=NS(status='available', volumes=rows)) as inspect:
            self.assertEqual(observer.free_bytes(), 100)
            self.assertEqual(inspect.call_args.kwargs['helper_image_id'], 'sha256:' + '1' * 64)
            rows[0].root_identity = '2:2'
            with self.assertRaises(RuntimeRefused):
                observer.free_bytes()
        with mock.patch('deploy.update.docker_host.inspect_volume_storage', return_value=NS(status='unknown', volumes=[])):
            with self.assertRaises(RuntimeRefused) as caught:
                observer.free_bytes()
            self.assertEqual(caught.exception.reason_code, ReasonCode.RESTORE_CAPACITY_UNKNOWN)


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
        from deploy.update.orchestrator import GuardRefused, ScheduledGuard
        from deploy.tests.test_update_compatibility import restore_context
        # Test the real pending-history guard without constructing another
        # TestCase outside unittest's class-fixture lifecycle.
        guard = ScheduledGuard(restore_context()[0], discovery=mock.Mock(), store=mock.Mock(),
            facts=mock.Mock(), make_plan=mock.Mock(), control_generation=0, window_occurrence='always')
        self.addCleanup(guard.close)
        self.craft_apply(status='failed_during_rollout', target='v1.52.0')
        with self.assertRaises(GuardRefused) as caught:
            guard._pending(self.updater())
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


class ScheduledGuardTests(unittest.TestCase):
    """Real v2 guard, signatures, baseline, cold backup/restore and recorded Docker.

    Shares the signed component publisher, not a release qualification. Faults
    enter the shipping guard/engine; no v1 hook or image-only fallback remains.
    """
    @classmethod
    def setUpClass(cls):
        from deploy.tests.test_update_automatic_integration import AutomaticIntegrationTests
        AutomaticIntegrationTests.setUpClass.__func__(cls)

    def worker(self, operation='tick', fault='', expected=0):
        from deploy.tests.test_update_automatic_integration import process_tick
        self.assertEqual(expected, 0)
        context = process_tick(self.root, self.artifacts, operation, fault)
        if operation != 'enroll':
            return context.result

    def setUp(self):
        from deploy.tests.test_update_automatic_integration import AutomaticIntegrationTests
        AutomaticIntegrationTests.setUp(self)
        self.env_path = self.root / '.env'
        self.original = self.env_path.read_bytes()
        import json
        self.source_daemon = json.loads((self.root/'recorded-daemon.json').read_bytes())
        self.configure = lambda engine: None
        self.hooks = {}
        self.engine = self.guard = None

    def run_guarded(self, fault=''):
        from deploy.tests.test_update_automatic_integration import process_tick
        def configure(engine):
            self.engine, self.guard = engine, engine.automation_guard
            self.configure(engine)
        self.context = process_tick(self.root, self.artifacts, fault=fault,
            hooks=dict(self.hooks, engine=configure))
        return self.context.result

    def ledger(self):
        from deploy.update.recovery import RecoveryLedger
        paths = list((self.root / 'backups').glob('*/recovery.json'))
        self.assertEqual(len(paths), 1)
        return RecoveryLedger.open(paths[0].parent)

    def assert_reason(self, result, reason):
        # Once recovery owns the outcome, the original refusal is retained in
        # the ledger, while the public reason describes restore/recovery state.
        if list((self.root / 'backups').glob('*/recovery.json')) and self.ledger().backup_seal:
            self.assertEqual(self.ledger().snapshot['forward_failure']['reason_code'], reason.value)
        else:
            self.assertIn(reason.value, result['reasons'], result)
        self.assertEqual(self.store.read_state().baseline_head, self.initial)

    def starts(self):
        return [event for event in self.context.daemon.events if event[0] in ('up', 'start')]

    def assert_no_target_start(self):
        self.assertFalse([e for e in self.starts() if e[1] == 'v1.52.0'])

    def at_boundary(self, boundary, action, *, after=False):
        previous = self.configure
        def configure(engine):
            previous(engine)
            guard = engine.automation_guard
            validate = guard.validate
            def visit(name, context):
                if name == boundary and not after:
                    action(engine)
                value = validate(name, context)
                if name == boundary and after:
                    action(engine)
                return value
            guard.validate = visit
        self.configure = configure

    def after_backup(self, action):
        self.hooks['backup'] = action

    def final_facts(self, boundary, action, call=2):
        previous = self.configure
        def configure(engine):
            previous(engine)
            guard = engine.automation_guard
            facts, calls = guard.facts, []
            def observe(inst, name):
                value = facts(inst, name)
                if name == boundary:
                    calls.append(name)
                    if len(calls) == call:
                        action(engine)
                return value
            guard.facts = observe
        self.configure = configure

    def edit(self, raw=b'\nFUTURE_AGI_VERSION=v1.53.0\n'):
        with self.env_path.open('ab') as out:
            out.write(raw)

    def test_factory_uses_settings_property_and_supervises_both_docker_and_backup(self):
        result = self.run_guarded()
        self.assertEqual(result['disposition'], 'applied', result)
        self.assertIs(self.engine.installation.runner, self.context.daemon)
        self.assertIs(self.engine.hook_runner, self.context.daemon)
        self.assertIs(self.engine.built_in_backup.installation.runner, self.context.daemon)
        self.assertIs(self.engine.automation_guard, self.guard)
        from deploy.update.orchestrator import guarded_updater, SupervisedReadiness
        from deploy.update.processes import ProcessSupervisor
        supervisor = ProcessSupervisor(self.engine.settings.automation_policy,
            docker_context='test-backup', environment={})
        self.context.inst.runner = supervisor
        engine = guarded_updater(self.context.inst, self.engine.settings, self.engine.state, self.guard, supervisor)
        self.assertIs(engine.hook_runner, supervisor)
        self.assertIs(engine.installation.runner, supervisor)
        self.assertIsInstance(engine.readiness, SupervisedReadiness)
        self.assertIs(engine.automation_guard, self.guard)
        self.assertIs(engine.built_in_backup.installation.runner, supervisor)

    def test_certified_attempt_has_private_bound_proof_and_refreshes_each_boundary(self):
        self.seen = []
        self.hooks['before_facts'] = lambda life, boundary: self.seen.append(boundary)
        result = self.run_guarded()
        self.assertEqual(result['disposition'], 'applied', result)
        import json
        proof = json.loads(self.guard.proof.raw)
        self.assertEqual(proof['schema_version'], 2)
        self.assertEqual(proof['attempt_id'], self.guard.attempt.attempt_id)
        self.assertEqual(proof['target_manifest_hash'], self.guard.prepared.plan['manifest_hash'])
        self.assertEqual(self.ledger().phase, 'succeeded')
        for boundary in ('before_recovery', 'before_backup', 'after_backup', 'before_env_commit', 'before_rollout'):
            self.assertGreaterEqual(self.seen.count(boundary), 2)

    def test_successful_runner_finalizes_attempt_without_a_failed_target_latch(self):
        result = self.run_guarded()
        self.assertEqual(result['code'], 0, result)
        self.assertEqual(result['forward'], 'succeeded')
        self.assertIsNotNone(self.guard.first_mutation_at)
        self.assertEqual(self.engine.active_record['stage'], 'rollout')
        self.assertIsNone(self.store.read_state().active_attempt_id)
        self.assertIsNone(self.store.read_state().failed_target_latch)
        self.assertEqual(self.ledger().phase, 'succeeded')

    def test_foreign_manual_stage_is_never_adopted(self):
        from deploy.update.receipt import StateDir
        state = StateDir(self.root)
        state.write_receipt(dict(kind='apply', status='staged', run_id='d'*32, project='test-backup',
            env_file='.env', mode='standalone', production=False, target_version='v1.52.0'))
        result = self.run_guarded()
        self.assertIn('manual_stage_present', result['reasons'], result)
        self.assertFalse(list((self.root/'backups').glob('*/recovery.json')))
        self.assertEqual(self.env_path.read_bytes(), self.original)
        self.assertEqual(len(state.receipts()), 1)

    def test_changed_bundle_refuses_before_backup(self):
        from dataclasses import replace
        from deploy.update.discovery import DiscoveryResult
        def configure(engine):
            discovery = engine.automation_guard.discovery
            refresh = discovery.refresh
            def changed(*args, **kwargs):
                result = refresh(*args, **kwargs)
                return replace(result, candidate=replace(result.candidate, raw_bundle=b'changed'))
            discovery.refresh = changed
        self.configure = configure
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        self.assertFalse(list((self.root/'backups').glob('*/recovery.json')))

    def test_exact_pair_is_reverified_again_at_first_mutation_boundary(self):
        from dataclasses import replace
        def configure(engine):
            discovery = engine.automation_guard.discovery
            refresh, calls = discovery.refresh, []
            def changed(*args, **kwargs):
                result = refresh(*args, **kwargs)
                calls.append(1)
                if len(calls) == 4:
                    return replace(result, candidate=replace(result.candidate, raw_bundle=b'changed-at-mutation'))
                return result
            discovery.refresh = changed
        self.configure = configure
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        self.assert_no_target_start()
        self.assertEqual(self.env_path.read_bytes(), self.original)
        self.assertEqual(self.ledger().phase, 'restored')

    def test_update_configuration_change_during_backup_refuses(self):
        self.after_backup(lambda engine: (self.root/'update.conf').write_text('FUTURE_AGI_UPDATE_AUTOMATION_MODE=manual\n'))
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.CONFIGURATION_CHANGED)
        self.assert_no_target_start()
        self.assertEqual(self.env_path.read_bytes(), self.original)

    def test_paused_during_backup_blocks_before_any_env_mutation(self):
        from deploy.tests.test_update_automatic_integration import now
        self.after_backup(lambda engine: self.store.change_control('pause', now()))
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.CONTROL_INVALID)
        self.assert_no_target_start()
        self.assertEqual(self.env_path.read_bytes(), self.original)
        self.assertEqual(self.ledger().phase, 'restored')

    def test_pause_arriving_during_refresh_is_rechecked_before_backup(self):
        from deploy.tests.test_update_automatic_integration import now
        def configure(engine):
            discovery = engine.automation_guard.discovery
            refresh = discovery.refresh
            def paused(*args, **kwargs):
                value = refresh(*args, **kwargs)
                self.store.change_control('pause', now())
                return value
            discovery.refresh = paused
        self.configure = configure
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.CONTROL_INVALID)
        self.assertFalse(list((self.root/'backups').glob('*/recovery.json')))

    def test_backup_edits_configuration_refuse_without_overwriting_operator(self):
        self.after_backup(lambda engine: self.edit(b'\nOPERATOR_EDIT=preserve\n'))
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.CONFIGURATION_CHANGED)
        self.assertEqual(self.env_path.read_bytes(), self.original+b'\nOPERATOR_EDIT=preserve\n')
        self.assert_no_target_start()
        self.assertEqual(self.ledger().direction, 'restore_decided')

    def test_foreign_journal_arriving_during_backup_is_kept_without_recovery(self):
        foreign = dict(stage='prepare', kind='apply', run_id='e'*32)
        self.after_backup(lambda engine: engine.state.write_journal(foreign))
        result = self.run_guarded()
        self.assertEqual(result['disposition'], 'recovery_required', result)
        self.assertEqual(self.engine.state.read_journal(), foreign)
        self.assertEqual(self.engine.state.receipts(), [])
        self.assert_no_target_start()
        self.assertEqual(self.env_path.read_bytes(), self.original)

    def test_foreign_journal_at_rollout_boundary_is_not_cleared_or_recovered(self):
        foreign = dict(stage='prepare', kind='rollback', run_id='e'*32)
        self.at_boundary('before_rollout', lambda engine: engine.state.write_journal(foreign))
        result = self.run_guarded()
        self.assertEqual(result['disposition'], 'recovery_required', result)
        self.assertEqual(self.engine.state.read_journal(), foreign)
        self.assertEqual(self.engine.state.receipts(), [])
        self.assert_no_target_start()
        self.assertIn(b'v1.52.0', self.env_path.read_bytes())

    def test_backup_proof_cannot_be_replaced_with_a_newer_attestation(self):
        import json
        def change(engine):
            path = engine.automation_guard.proof_path
            value = json.loads(path.read_bytes())
            value['backup_id'] = 'different-backup'
            path.write_text(json.dumps(value))
        self.at_boundary('before_env_commit', change)
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.BACKUP_PROOF_INVALID)
        self.assert_no_target_start()
        self.assertEqual(self.env_path.read_bytes(), self.original)

    def test_missing_backup_proof_fails_closed(self):
        self.after_backup(lambda engine: engine.automation_guard.proof_path.unlink())
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.BACKUP_PROOF_INVALID)
        self.assert_no_target_start()
        self.assertEqual(self.env_path.read_bytes(), self.original)
        self.assertEqual(self.ledger().direction, 'restore_decided')

    def test_missing_proof_runner_latches_target_and_records_typed_reason(self):
        self.test_missing_backup_proof_fails_closed()
        self.assertEqual(self.ledger().phase, 'restored')
        self.assertIsNone(self.store.read_state().active_attempt_id)
        self.assertEqual(self.store.read_state().failed_target_latch.version, 'v1.52.0')

    def test_expired_backup_at_mutation_boundary_refuses(self):
        from deploy.tests import test_update_automatic_integration as fixture
        def expire(engine):
            fixture.CLOCK_SHIFT += 3601
        self.at_boundary('before_env_commit', expire)
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.BACKUP_EXPIRED)
        self.assert_no_target_start()
        self.assertEqual(self.env_path.read_bytes(), self.original)

    def test_incomplete_attempt_is_never_retried_by_another_guard(self):
        result = self.run_guarded('backup_uncertain')
        self.assertEqual(result['disposition'], 'recovery_required', result)
        attempt = self.store.read_state().active_attempt_id
        before = list(self.context.daemon.events)
        result = self.run_guarded()
        self.assertEqual(result['disposition'], 'recovery_required', result)
        self.assertEqual(self.store.read_state().active_attempt_id, attempt)
        self.assertEqual(self.context.daemon.events, [])
        self.assertTrue(before)

    def test_failed_target_latch_refuses_before_backup(self):
        self.assertEqual(self.run_guarded('readiness_failed')['disposition'], 'restored')
        latch = self.store.read_state().failed_target_latch
        result = self.run_guarded()
        self.assertIn('failed_target_latched', result['reasons'], result)
        self.assertEqual(self.store.read_state().failed_target_latch, latch)
        self.assertFalse(self.context.daemon.events)

    def test_uncertified_release_does_not_take_backup(self):
        result = self.run_guarded('missing_proof')
        self.assertEqual(result['disposition'], 'blocked', result)
        self.assertFalse(list((self.root/'backups').glob('*/recovery.json')))
        self.assertFalse(self.context.daemon.events)

    def test_clock_regression_refuses_before_backup(self):
        from deploy.tests import test_update_automatic_integration as fixture
        self.at_boundary('before_backup', lambda engine: setattr(fixture, 'CLOCK_SHIFT', -100))
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.CLOCK_SKEW)
        self.assertFalse(list((self.root/'backups').glob('*/recovery.json')))

    def test_closed_window_after_slow_refresh_refuses(self):
        from dataclasses import replace
        def configure(engine):
            guard = engine.automation_guard
            facts = guard.facts
            guard.facts = lambda *args: (facts(*args)[0], replace(facts(*args)[1], window_open=False))
        self.configure = configure
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.OUTSIDE_WINDOW)
        self.assertEqual(self.env_path.read_bytes(), self.original)

    def test_typed_busy_preserves_running_operation(self):
        from deploy.update.receipt import StateDir
        with StateDir(self.root).lock():
            result = self.run_guarded()
        self.assertEqual(result['disposition'], 'busy', result)
        self.assertFalse(self.context.daemon.events)

    def test_public_observation_contains_no_raw_error_path_or_env_value(self):
        from dataclasses import asdict
        import json
        from deploy.update.status import observe_status
        self.run_guarded()
        self.context.inst.fresh = mock.Mock(side_effect=RuntimeError('/private/path PASSWORD=very-secret'))
        before = self.env_path.read_bytes()
        observation = observe_status(self.context.inst, self.engine.state)
        self.assertEqual(self.env_path.read_bytes(), before)
        text = json.dumps(asdict(observation))
        self.assertNotIn('private/path', text)
        self.assertNotIn('very-secret', text)
        self.assertIsNone(observation.versions['observed_at'])
        self.assertEqual(observation.reason_codes, (ReasonCode.RUNNING_VERSIONS_UNKNOWN,))

    def test_status_observes_running_and_selected_versions_separately(self):
        from deploy.update.status import observe_status
        self.run_guarded()
        before = self.env_path.read_bytes()
        observation = observe_status(self.context.inst, self.engine.state)
        self.assertEqual(self.env_path.read_bytes(), before)
        self.assertEqual(observation.versions['selected_release'], 'v1.52.0')
        self.assertEqual(observation.versions['running_release'], 'v1.52.0')
        self.assertFalse(observation.pending_transaction)

    def test_mutation_timeout_retains_journal_and_cannot_fallback(self):
        from deploy.update.processes import ProcessError
        def configure(engine):
            docker = engine.installation.docker_run
            def timeout(*args, **kwargs):
                if args[0] == 'start':
                    engine.installation.runner.uncertain = True
                    raise ProcessError(ReasonCode.OPERATION_TIMEOUT, uncertain=True)
                return docker(*args, **kwargs)
            engine.installation.docker_run = timeout
        self.configure = configure
        result = self.run_guarded()
        self.assertEqual(result['disposition'], 'recovery_required', result)
        self.assertIsNotNone(self.engine.state.read_journal())
        self.assertEqual(self.ledger().direction, 'restore_decided')
        self.assertIsNotNone(self.ledger().snapshot['pending'])
        self.assertIn(b'v1.52.0', self.env_path.read_bytes())
        self.assertNotEqual(self.ledger().phase, 'restored')

    def test_pin_edit_during_the_final_facts_call_is_refused_and_survives(self):
        self.final_facts('before_env_commit', lambda engine: self.edit())
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.ENV_CONFLICT)
        self.assertEqual(self.env_path.read_bytes(), self.original+b'\nFUTURE_AGI_VERSION=v1.53.0\n')
        self.assert_no_target_start()
        self.assertIsNone(self.guard.approved_env_sha256)

    def test_any_env_edit_during_the_final_facts_call_is_refused_not_only_pins(self):
        raw = b'\n# a comment the operator added\n'
        self.final_facts('before_env_commit', lambda engine: self.edit(raw))
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.ENV_CONFLICT)
        self.assertEqual(self.env_path.read_bytes(), self.original+raw)
        self.assert_no_target_start()

    def test_pin_edit_after_the_guard_returned_is_refused_by_the_transaction_itself(self):
        self.at_boundary('before_env_commit', lambda engine: self.edit(), after=True)
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.ENV_CONFLICT)
        self.assertEqual(self.env_path.read_bytes(), self.original+b'\nFUTURE_AGI_VERSION=v1.53.0\n')
        self.assert_no_target_start()

    def test_pin_edit_before_the_final_observation_is_still_a_configuration_change(self):
        self.final_facts('before_env_commit', lambda engine: self.edit(), call=1)
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.CONFIGURATION_CHANGED)
        self.assertEqual(self.env_path.read_bytes(), self.original+b'\nFUTURE_AGI_VERSION=v1.53.0\n')
        self.assert_no_target_start()

    def test_env_that_cannot_be_read_when_the_final_observation_starts_fails_closed(self):
        from deploy.update.envfile import EnvCommitError
        def configure(engine):
            read = engine._env_path
            def unreadable():
                raise EnvCommitError('selected env target changed')
            engine._env_path = unreadable
        self.at_boundary('before_env_commit', configure)
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.ENV_CONFLICT)
        self.assertEqual(result['disposition'], 'restored', result)
        self.assertEqual(self.ledger().phase, 'restored')
        self.assertIsNone(self.guard.approved_env_sha256)
        self.assertEqual(self.env_path.read_bytes(), self.original)
        self.assert_no_target_start()

    def test_without_an_edit_the_transaction_is_bound_to_the_approved_bytes_and_succeeds(self):
        import hashlib
        result = self.run_guarded()
        self.assertEqual(result['disposition'], 'applied', result)
        self.assertEqual(self.guard.approved_env_sha256, hashlib.sha256(self.original).hexdigest())
        self.assertEqual(self.engine.active_record['env_recovery']['before_sha256'], self.guard.approved_env_sha256)
        self.assertIn(b'v1.52.0', self.env_path.read_bytes())

    def test_rollout_boundary_rechecks_the_approved_preimage(self):
        self.at_boundary('before_rollout', lambda engine: setattr(engine.automation_guard, 'approved_env_sha256', 'f'*64))
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.ENV_CONFLICT)
        self.assert_no_target_start()
        # The independent snapshot authority can restore despite corrupt guard
        # memory. This replaces the removed image-fallback recovery expectation.
        self.assertEqual(self.ledger().phase, 'restored')
        self.assertEqual(self.env_path.read_bytes(), self.original)

    def test_final_rollout_observation_preserves_forward_operator_edits(self):
        self.final_facts('before_rollout', lambda engine: self.edit())
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.ENV_CONFLICT)
        self.assertTrue(self.env_path.read_bytes().endswith(b'\nFUTURE_AGI_VERSION=v1.53.0\n'))
        self.assert_no_target_start()
        self.assertIsNotNone(self.engine.state.read_journal())

    def retag(self, engine, service='postgres', *, remove=False, strip_digest=False):
        inst = engine.installation
        reference = inst.config()['services'][service]['image']
        image = inst.daemon.images[service]
        if remove:
            inst.daemon.local_images.pop(reference)
        elif strip_digest:
            # All fresh inspection clones must see the same local image store.
            from deploy.tests.test_update_automatic_integration import RecordedInstallation
            original = RecordedInstallation.image
            def inspect(reader, ref):
                value = original(reader, ref)
                if value and value['id'] == image:
                    value = dict(value, repo_digests=[])
                return value
            patch = mock.patch.object(RecordedInstallation, 'image', inspect)
            patch.start()
            self.addCleanup(patch.stop)
        else:
            inst.daemon.local_images[reference] = 'sha256:'+'9'*64

    def test_retagged_dependency_is_refused_before_create_and_nothing_is_touched(self):
        self.at_boundary('before_backup', lambda engine: self.retag(engine))
        result = self.run_guarded()
        self.assertIn('image_digest_mismatch', result['reasons'], result)
        self.assertFalse(list((self.root/'backups').glob('*/recovery.json')))
        self.assertEqual(self.env_path.read_bytes(), self.original)
        self.assertEqual(self.context.daemon.containers, self.source_daemon['containers'])
        self.assert_no_target_start()

    def test_retagged_dependency_is_a_typed_pre_rollout_outcome_and_latches_the_target(self):
        self.at_boundary('before_env_commit', lambda engine: self.retag(engine))
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.IMAGE_DIGEST_MISMATCH)
        self.assert_no_target_start()
        # Immutable previous IDs are retained, but a changed selected model is
        # not normalized or pulled to manufacture restore permission.
        self.assertEqual(self.ledger().direction, 'restore_decided')
        self.assertEqual(self.store.read_state().baseline_head, self.initial)

    def test_same_image_id_without_the_certified_digest_is_refused_before_create(self):
        self.at_boundary('before_env_commit', lambda engine: self.retag(engine, strip_digest=True))
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.IMAGE_DIGEST_MISMATCH)
        self.assert_no_target_start()

    def test_dependency_tag_removed_cannot_be_pulled_cold_to_bypass_certification(self):
        self.at_boundary('before_env_commit', lambda engine: self.retag(engine, remove=True))
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.IMAGE_DIGEST_MISMATCH)
        self.assert_no_target_start()
        # The normal target prefetch is before quiescence. No dependency pull
        # (nor any other pull) is allowed after the cold snapshot.
        commands = self.context.daemon.calls
        first_stop = next(i for i, row in enumerate(commands) if 'stop' in row)
        self.assertFalse([row for row in commands[first_stop:] if 'pull' in row])

    def test_unchanged_dependencies_succeed_and_are_certified_once(self):
        result = self.run_guarded()
        self.assertEqual(result['disposition'], 'applied', result)
        for service in ('postgres', 'clickhouse'):
            before = self.source_daemon['images'][service]
            self.assertEqual(self.context.daemon.images[service], before)
            self.assertEqual(self.guard.dependency_image_ids[service], frozenset({before}))
        self.assertEqual([e for e in self.context.daemon.events if e[0]=='up'], [['up', 'v1.52.0']])

    def test_dependency_replaced_during_rollout_never_yields_a_succeeded_receipt(self):
        def configure(engine):
            runtime = engine._automatic_runtime
            def replaced(*args):
                value = runtime(*args)
                start = value.start_and_wait
                def changed(roster, *a, **kw):
                    result = start(roster, *a, **kw)
                    if not kw.get('previous'):
                        daemon = engine.installation.daemon
                        daemon.find('postgres')['Image'] = daemon.images['postgres'] = 'sha256:'+'9'*64
                    return result
                value.start_and_wait = changed
                return value
            engine._automatic_runtime = replaced
        self.configure = configure
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.IMAGE_DIGEST_MISMATCH)
        self.assertEqual(result['disposition'], 'recovery_required', result)
        self.assertNotIn('succeeded', [r['status'] for r in self.engine.state.history(self.context.inst.identity())])
        self.assertEqual(self.ledger().direction, 'restore_decided')

    def test_missing_dependency_after_rollout_never_yields_a_succeeded_receipt(self):
        def configure(engine):
            factory = engine._automatic_runtime
            def runtime(*args):
                value = factory(*args)
                start = value.start_and_wait
                def missing(roster, *a, **kw):
                    result = start(roster, *a, **kw)
                    if not kw.get('previous'):
                        daemon = engine.installation.daemon
                        del daemon.containers[daemon.find('postgres')['Id']]
                    return result
                value.start_and_wait = missing
                return value
            engine._automatic_runtime = runtime
        self.configure = configure
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.IMAGE_DIGEST_MISMATCH)
        self.assertEqual(result['disposition'], 'recovery_required', result)
        self.assertNotIn('succeeded', [r['status'] for r in self.engine.state.history(self.context.inst.identity())])
        self.assertIsNotNone(self.store.read_state().active_attempt_id)

    def test_newer_unrelated_receipt_never_reselects_snapshot_restore_authority(self):
        def receipt(life, ledger, daemon):
            self.engine.state.write_receipt(dict(kind='apply', status='succeeded', run_id='f'*32,
                project='unrelated', env_file='unrelated.env', mode='standalone', production=False,
                target_version='v1.53.0'))
        self.hooks['readiness'] = receipt
        result = self.run_guarded('readiness_failed')
        self.assertEqual(result['disposition'], 'restored', result)
        self.assertEqual(self.ledger().backup_seal.source_version, 'v1.51.0')
        self.assertEqual(self.env_path.read_bytes(), self.original)
        self.assertEqual(len(self.engine.state.receipts()), 3)
        self.assertEqual(self.store.read_state().failed_target_latch.version, 'v1.52.0')

    def test_restore_env_edit_before_inverse_is_never_normalized_or_overwritten(self):
        for index, raw in enumerate((b'\nFUTURE_AGI_VERSION=v1.53.0\n',
                                     b'\nSOME_OPERATOR_SETTING=1\n',
                                     b'\n# operator comment\n')):
            with self.subTest(edit=raw):
                if index:
                    self.setUp()
                self.hooks['readiness'] = lambda *args: self.edit(raw)
                result = self.run_guarded('readiness_failed')
                self.assertEqual(result['disposition'], 'recovery_required', result)
                self.assertTrue(self.env_path.read_bytes().endswith(raw))
                self.assertFalse(self.ledger().snapshot['previous_start_armed'])
                self.assertIsNotNone(self.engine.state.read_journal())
                self.assertIsNotNone(self.store.read_state().active_attempt_id)

    def windowed(self, remaining=960, min_start=900):
        from dataclasses import replace
        from datetime import timedelta
        from deploy.tests.test_update_automatic_integration import now
        from deploy.update.schedule import occurrence
        from deploy.update.settings import load_settings
        def configure(engine):
            at = now()
            window = (at-timedelta(minutes=1)).strftime('%H:%M')+'-'+(at+timedelta(seconds=remaining)).strftime('%H:%M')
            settings = load_settings(self.root, {'FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW': window,
                'FUTURE_AGI_UPDATE_MIN_START_BUDGET_SECONDS': str(min_start),
                # Isolate schedule admission from the separately tested proof
                # expiry guard when simulated facts consume hours of wall time.
                'FUTURE_AGI_UPDATE_BACKUP_MAX_AGE_SECONDS': '86400'}, environ={})
            engine.settings = settings
            engine.installation.runner.policy = settings.automation_policy
            guard = engine.automation_guard
            guard.window_occurrence = occurrence(settings.automation_policy.maintenance_window, at)
            original = guard.facts
            def facts(*args):
                obs, controls = original(*args)
                return obs, replace(controls, policy=settings.automation_policy)
            guard.facts = facts
        self.configure = configure

    def test_slow_final_facts_that_cross_the_start_budget_refuse_before_create(self):
        from deploy.tests import test_update_automatic_integration as fixture
        # Preserve the original start-budget assertion, independently of window
        # closure and backup expiry. Large budgets allow the genuine A6 check.
        self.windowed(18000, min_start=7200)
        self.final_facts('before_rollout', lambda engine: setattr(fixture, 'CLOCK_SHIFT', 10900))
        result = self.run_guarded()
        self.assert_reason(result, ReasonCode.INSUFFICIENT_WINDOW)
        self.assert_no_target_start()
        self.assertEqual(self.ledger().phase, 'restored')

    def test_slow_final_facts_that_stay_inside_the_budget_proceed(self):
        from deploy.tests import test_update_automatic_integration as fixture
        self.windowed(18000, min_start=7200)
        self.final_facts('before_rollout', lambda engine: setattr(fixture, 'CLOCK_SHIFT', 1))
        result = self.run_guarded()
        self.assertEqual(result['disposition'], 'applied', result)
        self.assertEqual(self.ledger().phase, 'succeeded')

    def test_slow_final_facts_that_close_the_window_refuse_at_every_forward_boundary(self):
        from deploy.tests import test_update_automatic_integration as fixture
        for boundary in ('before_backup', 'after_backup', 'before_env_commit'):
            with self.subTest(boundary=boundary):
                if boundary != 'before_backup': self.setUp()
                self.windowed(18000)
                self.final_facts(boundary, lambda engine: setattr(fixture, 'CLOCK_SHIFT', 18001))
                result = self.run_guarded()
                self.assert_reason(result, ReasonCode.OUTSIDE_WINDOW)
                self.assert_no_target_start()
                self.assertEqual(self.env_path.read_bytes(), self.original)

    def test_snapshot_restore_survives_pause_closed_window_and_never_retries_target(self):
        from deploy.tests import test_update_automatic_integration as fixture
        def fail(life, ledger, daemon):
            self.store.change_control('pause', fixture.now())
            fixture.CLOCK_SHIFT += 7200
        self.hooks['readiness'] = fail
        result = self.run_guarded('readiness_failed')
        self.assertEqual(result['disposition'], 'restored', result)
        self.assertEqual(self.env_path.read_bytes(), self.original)
        self.assertEqual(self.ledger().phase, 'restored')
        self.assertEqual(self.store.read_state().failed_target_latch.version, 'v1.52.0')
        self.assertEqual([e[1] for e in self.starts() if e[0]=='start'], ['v1.52.0', 'v1.51.0'])
        # Keep wall time monotonic across this second in-process scheduler tick.
        self.store.change_control('disable', fixture.now())
        from datetime import timedelta
        with mock.patch.object(fixture, 'datetime', wraps=fixture.datetime) as clock:
            clock.now.return_value = fixture.now()+timedelta(seconds=1)
            result = self.run_guarded()
        self.assertEqual(result['disposition'], 'skipped', result)
        self.assertFalse(self.context.daemon.events)

    def test_restore_rechecks_env_after_previous_image_inspection_and_preserves_operator_edit(self):
        def configure(engine):
            image = engine.installation.image
            def inspect(ref):
                value = image(ref)
                if engine.automatic_ledger is not None and engine.automatic_ledger.direction == 'restore_decided':
                    if b'v1.53.0' not in self.env_path.read_bytes(): self.edit()
                return value
            engine.installation.image = inspect
        self.configure = configure
        result = self.run_guarded('readiness_failed')
        self.assertEqual(result['disposition'], 'recovery_required', result)
        self.assertTrue(self.env_path.read_bytes().endswith(b'\nFUTURE_AGI_VERSION=v1.53.0\n'))
        self.assertFalse([e for e in self.starts() if e == ['start', 'v1.51.0']])
        self.assertEqual(self.ledger().direction, 'restore_decided')

    def test_previous_image_missing_cannot_authorize_restore_start(self):
        # V2 restores the sealed immutable ID, not the removed image-fallback
        # digest eligibility check. Losing that exact local image still refuses.
        def fail(life, ledger, daemon):
            self.retag(self.engine, service='postgres', remove=True)
        self.hooks['readiness'] = fail
        result = self.run_guarded('readiness_failed')
        self.assertEqual(result['disposition'], 'recovery_required', result)
        self.assertFalse(self.ledger().snapshot['previous_start_armed'])
        self.assertEqual(self.ledger().direction, 'restore_decided')

    def test_restore_renews_real_signature_window_after_io_without_extending_recovery_deadline(self):
        import time
        deadlines = []
        def configure(engine):
            life = engine.automation_guard.automatic
            window = life._verification_window
            def bounded(installation):
                value = window(installation)
                deadline = life.baseline.verifier.deadline
                self.assertLessEqual(deadline, installation.runner.deadline)
                self.assertLessEqual(deadline, time.monotonic()+engine.settings.automation_policy.probe_timeout_seconds)
                deadlines.append(deadline)
                return value
            life._verification_window = bounded
        self.configure = configure
        def expire(life, ledger, daemon):
            life.baseline.verifier.deadline = time.monotonic()-1
        self.hooks['readiness'] = expire
        result = self.run_guarded('readiness_failed')
        self.assertEqual(result['disposition'], 'restored', result)
        self.assertEqual(self.ledger().phase, 'restored')
        self.assertTrue(self.ledger().snapshot['previous_start_armed'])
        self.assertEqual(self.env_path.read_bytes(), self.original)
        self.assertGreater(len(deadlines), 1)


class ScheduledManualSemanticsTests(UpdaterCase):
    """Manual/legacy assertions remain unchanged; no automatic v1 fixture."""
    def test_manual_stage_binding_ignores_only_image_pins(self):
        from dataclasses import replace
        from deploy.tests.test_update_compatibility import synthetic_context
        from deploy.update.orchestrator import stage_binding
        prepared = synthetic_context()[0]
        self.build(current='v1.51.0')
        compose = self.root/'docker-compose.yml'
        compose.write_text('services: {}\n')
        prepared = replace(prepared, binding=replace(prepared.binding, checkout=self.root,
            env_alias=self.env_path, env_target=self.env_path, compose_files=(compose,)))
        import copy
        from deploy.update.envfile import read_env
        def fresh():
            clone = copy.copy(self.inst)
            clone.env, clone.env_overlay = read_env(self.env_path), {}
            clone.resolved_images = lambda: {
                service: f'{repo}:{clone.env.get(variable) or default}'
                for service, repo, variable, default in clone.specs}
            return clone
        self.inst.fresh = fresh
        with mock.patch('deploy.update.orchestrator.check_binding'), mock.patch('deploy.update.orchestrator.check_deployment'):
            before = stage_binding(prepared, self.inst)
            self.env_path.write_bytes(self.env_bytes().replace(b'v1.51.0', b'v1.52.0'))
            after = stage_binding(prepared, self.inst)
            self.assertEqual(before, after)
            with self.env_path.open('a') as handle:
                handle.write('\nOPERATOR_EDIT=changed\n')
            self.assertNotEqual(before['config_fingerprint'], stage_binding(prepared, self.inst)['config_fingerprint'])

    def test_manual_runs_keep_the_unbound_recovery_record_call(self):
        from deploy.update import envfile
        with mock.patch.object(envfile, 'recovery_record', wraps=envfile.recovery_record) as record:
            self.apply(updater=self.updater())
        self.assertEqual(record.call_count, 1)
        self.assertEqual(len(record.call_args.args), 2)
        self.assertEqual(record.call_args.kwargs, {})
