"""Public writer security, atomicity, freshness and private CAS, without Docker."""
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests.test_update_compatibility import NOW, default_v2, prepare  # noqa: E402
from deploy.tests.test_update_public_contract import needs_jsonschema, VALIDATORS  # noqa: E402
from deploy.update.contracts import AutomationPolicy, ContractError, MaintenanceWindowSpec, ReasonCode  # noqa: E402
from deploy.update.discovery import DiscoveredRelease, DiscoveryResult  # noqa: E402
from deploy.update.download import DownloadImage, DownloadReceipt  # noqa: E402
from deploy.update.public_status import (PublicationError, PublicationTicket, PublicStatusPublisher,  # noqa: E402
                                         build_status, stamp)
from deploy.update.runner_state import (Control, LauncherConfig, Registration, RunnerStateStore,  # noqa: E402
                                        StaleGeneration)
from deploy.update.status import StatusObservation  # noqa: E402


class PublisherCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.env = self.root / '.env'
        self.env.write_text('FUTURE_AGI_VERSION=v1.51.0\nSECRET=do-not-export\n')
        self.compose = self.root / 'docker-compose.yml'
        self.compose.write_text('services: {}\n')
        self.store = RunnerStateStore(self.root, self.env)
        prepared = prepare(default_v2())
        self.binding = replace(prepared.binding, checkout=self.root, env_alias=self.env, env_target=self.env,
                               host_uid=os.geteuid(), compose_files=(self.compose,), selection_hash=self.store.selection_hash)
        self.store.register(Registration(1, self.binding, LauncherConfig(Path(sys.executable), Path.home(),
                                                                        os.defpath, 'test'), NOW))
        self.parent = self.root / 'public'
        self.publisher = PublicStatusPublisher(self.store, self.parent)
        self.policy = AutomationPolicy(mode='notify', enabled=True)
        self.state = replace(self.store.read_state(), last_attempt_at=NOW, last_tick_at=NOW)
        self.release = DiscoveredRelease(prepared.raw_manifest, prepared.raw_bundle,
                                         prepared.plan['manifest_hash'], prepared.trust_fingerprint, prepared.verification)
        self.discovery = DiscoveryResult(self.release, checked_at=NOW)
        self.observation = StatusObservation({
            'observed_at': stamp(NOW), 'runtime_state': 'consistent', 'selected_release': 'v1.51.0',
            'running_release': 'v1.51.0', 'components': [{'component': 'standalone', 'selected_version': 'v1.51.0',
            'observed_versions': ['v1.51.0'], 'observation': 'known'}]}, False, None, ())

    def snapshot(self, **kwargs):
        args = dict(now=NOW, observation=self.observation, discovery=self.discovery)
        args.update(kwargs)
        return build_status(self.binding, self.policy, Control(), self.state, **args)

    def publish(self, snapshot=None):
        ticket, _ = self.publisher.capture()
        return self.publisher.publish(snapshot or self.snapshot(), expected=ticket, operation_token='private-operation')


class ProjectionTests(PublisherCase):
    @needs_jsonschema
    def test_actual_published_json_matches_frozen_schema(self):
        self.publish()
        snapshot = json.loads((self.publisher.path / 'status.json').read_bytes())
        VALIDATORS['status'].validate(snapshot)
        self.assertEqual(snapshot['sequence'], 1)

    def test_closed_projection_drops_extraneous_private_fields(self):
        self.observation.versions['secret'] = 'CANARY-password'
        self.observation.versions['components'][0]['command'] = 'curl https://secret.invalid/path'
        operation = dict(state='succeeded', kind='check', stage=None, started_at=stamp(NOW),
                         finished_at=stamp(NOW), reported_at=stamp(NOW), reason_codes=[], raw_error='/secret/path')
        snapshot = self.snapshot(operation=operation)
        raw = json.dumps(snapshot)
        for value in ('CANARY', '/secret', 'curl', 'https:', str(self.root), 'private-operation', 'SECRET'):
            self.assertNotIn(value, raw)
        self.assertEqual(snapshot['versions']['running_release'], 'v1.51.0')

    def test_bad_allowlisted_values_refuse_before_any_output(self):
        self.observation.versions['components'][0]['selected_version'] = '/secret/path'
        with self.assertRaises(ContractError):
            self.snapshot()
        self.assertFalse(self.parent.exists())

    def test_failed_discovery_preserves_old_success_and_versions(self):
        old = self.snapshot()
        self.state = replace(self.state, last_attempt_at=NOW + timedelta(hours=8))
        failed = DiscoveryResult(reason_codes=(ReasonCode.NETWORK_UNAVAILABLE,))
        unknown = StatusObservation({'observed_at': None}, True, None, ())
        new = self.snapshot(previous=old, now=NOW + timedelta(hours=8), discovery=failed, observation=unknown)
        self.assertEqual(new['discovery']['checked_at'], old['discovery']['checked_at'])
        self.assertEqual(new['discovery']['candidate_version'], old['discovery']['candidate_version'])
        self.assertEqual(new['versions'], old['versions'])
        self.assertEqual(new['discovery']['state'], 'failed')

    def test_no_observation_is_unknown_and_current_requires_observed_version(self):
        new = self.snapshot(discovery=None, observation=None)
        self.assertEqual(new['versions']['runtime_state'], 'unknown')
        self.assertEqual(new['discovery']['state'], 'not_checked')
        self.observation.versions.update(running_release=self.release.version)
        self.observation.versions['components'][0]['observed_versions'] = [self.release.version]
        self.assertEqual(self.snapshot()['discovery']['state'], 'current')

    def test_download_requires_receipt_components_and_candidate(self):
        receipt = DownloadReceipt(1, 'a'*64, 'b'*64, 'c'*64, 'd'*64, 'e'*64, 'f'*64, 'a'*64, 'b'*64,
                                  stamp(NOW), (DownloadImage('standalone', 'SECRET', 'SECRET', 'SECRET'),), ())
        downloaded = self.snapshot(download=receipt)
        self.assertEqual(downloaded['download']['state'], 'downloaded')
        self.assertNotIn('SECRET', json.dumps(downloaded))
        with self.assertRaises(ContractError):
            self.snapshot(download=replace(receipt, images=()))
        self.assertEqual(self.snapshot(previous=downloaded)['download']['state'], 'unknown')
        self.assertEqual(self.snapshot(previous=downloaded, discovery=None)['download']['state'], 'downloaded')
        for reason in (ReasonCode.CONFIGURATION_CHANGED, ReasonCode.SIGNATURE_REJECTED):
            self.assertEqual(self.snapshot(previous=downloaded, reason_codes=(reason,))['download']['state'], 'unknown')
        newer = replace(self.release, raw_manifest=json.dumps(default_v2('v1.53.0')).encode())
        changed = self.snapshot(previous=downloaded, discovery=DiscoveryResult(newer, checked_at=NOW))
        self.assertEqual(changed['download']['state'], 'not_downloaded')

    def test_controls_precedence_pause_expiry_and_windows(self):
        interval = MaintenanceWindowSpec('interval', 'UTC', (1, 2), '02:00', '04:00')
        for policy, control, expected in (
            (AutomationPolicy(), Control(), 'disabled'),
            (AutomationPolicy(enabled=True), Control(), 'manual'),
            (self.policy, Control(pause_indefinite=True), 'paused'),
            (self.policy, Control(pause_until=NOW + timedelta(hours=1)), 'paused'),
            (self.policy, Control(pause_until=NOW), 'enabled'),
            (self.policy, Control(enabled_override=False), 'disabled'),
        ):
            with self.subTest(expected=expected):
                snap = build_status(self.binding, replace(policy, maintenance_window=interval), control, self.state, now=NOW)
                self.assertEqual(snap['automation']['state'], expected)
                self.assertEqual(snap['automation']['maintenance_window']['days_of_week'], [1, 2])
                if expected == 'paused':
                    self.assertEqual(snap['automation']['next_check_at'], stamp(control.pause_until))
        self.policy = replace(self.policy, maintenance_window=MaintenanceWindowSpec('always', 'UTC'))
        self.assertEqual(self.snapshot()['automation']['maintenance_window'], {'kind': 'always', 'timezone': 'UTC'})

    def test_subsecond_pause_rounds_conservatively_without_changing_control(self):
        control = Control(pause_until=NOW + timedelta(microseconds=500000))
        active = build_status(self.binding, self.policy, control, self.state, now=NOW)
        self.assertEqual(active['automation']['state'], 'paused')
        self.assertEqual(active['automation']['paused_until'], stamp(NOW + timedelta(seconds=1)))
        self.assertEqual(active['automation']['next_check_at'], active['automation']['paused_until'])
        expired = build_status(self.binding, self.policy, control, self.state, now=NOW + timedelta(microseconds=500000))
        self.assertEqual(expired['automation']['state'], 'enabled')
        self.assertEqual(control.pause_until, NOW + timedelta(microseconds=500000))

    def test_safety_recovery_production_and_last_run_allowlist(self):
        run = {'kind': 'apply', 'automatic': True, 'outcome': 'failed_during_rollout', 'from_version': 'v1.51.0',
               'target_version': self.release.version, 'finished_at': stamp(NOW), 'fallback_outcome': 'succeeded',
               'receipt': '/secret/receipt'}
        obs = replace(self.observation, last_run=run)
        snap = self.snapshot(observation=obs, reason_codes=(ReasonCode.RECOVERY_REQUIRED,))
        self.assertEqual(snap['safety']['operator_action'], 'review_recovery')
        self.assertNotIn('receipt', snap['last_run'])
        self.assertEqual(self.snapshot(last_run=run)['last_run']['fallback_outcome'], 'succeeded')
        self.binding = replace(self.binding, production=True, topology='distributed')
        self.policy = AutomationPolicy(mode='apply', enabled=True, automatic_rollback=True)
        self.assertEqual(self.snapshot()['safety']['operator_action'], 'initialize_then_apply')
        self.assertEqual(self.snapshot()['safety']['automatic_rollback'], 'ineligible')

    def test_check_never_replaces_active_or_failed_apply_progress(self):
        for state in ('applying', 'failed', 'interrupted'):
            operation = dict(state=state, kind='apply', stage='rollout', started_at=stamp(NOW),
                             finished_at=None, reported_at=stamp(NOW), reason_codes=[])
            previous = self.snapshot(operation=operation)
            check = dict(operation, state='succeeded', kind='check', stage=None, finished_at=stamp(NOW))
            current = self.snapshot(previous=previous, operation=check)
            self.assertEqual(current['operation'], previous['operation'])
            self.assertEqual(current['discovery']['state'], 'available')

    def test_window_noop_is_not_blocked_but_safety_refusal_is(self):
        self.assertEqual(self.snapshot(reason_codes=(ReasonCode.OUTSIDE_WINDOW,))['automation']['state'], 'enabled')
        snap = self.snapshot(reason_codes=(ReasonCode.BACKUP_REQUIRED,))
        self.assertEqual(snap['automation']['state'], 'blocked')
        self.assertEqual(snap['safety']['operator_action'], 'inspect_host')


class RestoreProjectionTests(PublisherCase):
    def setUp(self):
        super().setUp()
        self.policy = replace(self.policy, mode='apply', qualification_policy='standalone-cold-restore-v1')

    def operation(self, state='restoring', phase='restoring_volumes'):
        return dict(state=state, kind='restore', stage='restore', phase=phase, started_at=stamp(NOW),
                    finished_at=None, reported_at=stamp(NOW), reason_codes=[])

    def test_restore_progress_hides_stale_live_version_and_preserves_phase(self):
        old = self.snapshot()
        current = self.snapshot(previous=old, operation=self.operation())
        self.assertEqual(current['versions']['runtime_state'], 'unknown')
        self.assertIsNone(current['versions']['running_release'])
        self.assertEqual(current['operation']['phase'], 'restoring_volumes')
        self.assertEqual(current['safety']['fallback_strategy'], 'restore_backup')
        check = self.operation(state='succeeded', phase=None)
        check['kind'] = 'check'
        self.assertEqual(self.snapshot(previous=current, operation=check)['operation'], current['operation'])

    def test_forward_and_restore_outcomes_remain_separate_and_private_data_drops(self):
        safety = dict(backup_required=True, backup_state='sealed', readiness='passed', automatic_rollback='disabled',
                      operator_action='none', fallback_strategy='restore_backup', restore_state='verified',
                      snapshot_at=stamp(NOW), loss_window_start=stamp(NOW), loss_window_end=stamp(NOW),
                      loss_window_qualification='bounded', guidance_id='none', archive_path='/secret')
        run = dict(kind='apply', automatic=True, outcome='failed_during_rollout', from_version='v1.51.0',
                   target_version='v1.52.0', finished_at=stamp(NOW), fallback_outcome='none',
                   forward_result='failed', restore_result='verified', effective_version='v1.51.0',
                   failed_target_version='v1.52.0', proof_sha256='secret')
        snapshot = self.snapshot(operation=self.operation('restored', 'restored'), safety=safety, last_run=run)
        self.assertEqual(snapshot['last_run']['forward_result'], 'failed')
        self.assertEqual(snapshot['last_run']['restore_result'], 'verified')
        self.assertEqual(snapshot['last_run']['effective_version'], 'v1.51.0')
        self.assertNotIn('secret', json.dumps(snapshot))
        VALIDATORS['status'].validate(snapshot)
        legacy_forward = {key: value for key, value in run.items() if key not in
                          ('forward_result', 'restore_result', 'effective_version', 'failed_target_version', 'proof_sha256')}
        later = self.snapshot(previous=snapshot, observation=replace(self.observation, last_run=legacy_forward))
        self.assertEqual(later['last_run']['restore_result'], 'verified')

    def test_preflight_absent_admission_refusal_outranks_completed_restore(self):
        from deploy.tests.test_update_compatibility import restore_context
        from deploy.update.contracts import RestorationResult
        from deploy.update.public_status import restoration_projection
        prepared, obs, _, _ = restore_context()
        result = RestorationResult(obs.attempt_id, 'restored', obs.runtime_observation.release_version,
            prepared.manifest['version'], 'restored', obs.backup_seal, obs.runtime_observation)
        previous = self.snapshot(**restoration_projection(result, now=NOW, started_at=NOW))
        for reason in (ReasonCode.RESTORE_NOT_CERTIFIED, ReasonCode.TRANSITION_NOT_REHEARSED):
            operation = dict(state='blocked', kind='check', stage=None, started_at=stamp(NOW),
                finished_at=stamp(NOW), reported_at=stamp(NOW), reason_codes=[reason.value])
            current = self.snapshot(previous=previous, operation=operation, reason_codes=(reason,))
            self.assertEqual(current['operation']['state'], 'blocked')
            self.assertEqual(current['operation']['reason_codes'], [reason.value])
            self.assertEqual(current['discovery']['state'], 'blocked')
            self.assertEqual(current['discovery']['reason_codes'], [reason.value])
            self.assertEqual(current['last_run']['restore_result'], 'verified')
            VALIDATORS['status'].validate(current)

    def test_recovery_required_is_unknown_even_with_previous_healthy_observation(self):
        current = self.snapshot(operation=self.operation('recovery_required'), reason_codes=(ReasonCode.RECOVERY_REQUIRED,))
        self.assertIsNone(current['versions']['running_release'])
        self.assertEqual(current['safety']['guidance_id'], 'review_recovery')

    def test_typed_estimate_projection_carries_only_closed_scalars(self):
        from deploy.update.contracts import AutomaticUpdateDecision, AutomaticUpdateEstimate
        from deploy.update.public_status import ESTIMATE_FIELDS, project_estimate
        estimate = AutomaticUpdateEstimate(**{name: 10 for name in ESTIMATE_FIELDS})
        decision = AutomaticUpdateDecision(False, (ReasonCode.MIGRATION_EXCEEDS_WINDOW,), estimate)
        snapshot = self.snapshot(automatic_decision=decision)
        self.assertEqual(snapshot['safety']['estimates']['migration_seconds'], 10)
        self.assertEqual(snapshot['automation']['state'], 'blocked')
        VALIDATORS['status'].validate(snapshot)
        with self.assertRaises(ValueError):
            project_estimate({'estimate': {'secret': '/private'}})

    def test_success_decision_not_rewritten_by_same_operation_reporting_error(self):
        old = self.operation('succeeded', 'success_decided')
        old['kind'] = 'apply'
        run = dict(kind='apply', automatic=True, outcome='succeeded', from_version='v1.51.0',
                   target_version='v1.52.0', finished_at=stamp(NOW), fallback_outcome='none',
                   forward_result='succeeded', restore_result='not_needed', effective_version='v1.52.0', failed_target_version=None)
        snapshot = self.snapshot(operation=old, last_run=run)
        failure = dict(old, state='failed')
        later = self.snapshot(previous=snapshot, operation=failure,
                              last_run=dict(run, forward_result='failed', restore_result='running'))
        self.assertEqual(later['operation'], snapshot['operation'])
        self.assertEqual(later['last_run']['forward_result'], 'succeeded')
        self.assertEqual(later['safety']['restore_state'], 'not_needed')

    def test_typed_runner_projection_never_serializes_evidence_or_tokens(self):
        from deploy.tests.test_update_compatibility import restore_context
        from deploy.update.contracts import OperationEvent, RestorationResult
        from deploy.update.public_status import operation_projection, restoration_projection
        prepared, obs, _, _ = restore_context()
        result = RestorationResult(obs.attempt_id, 'restored', obs.runtime_observation.release_version,
            prepared.manifest['version'], 'restored', obs.backup_seal, obs.runtime_observation)
        kwargs = restoration_projection(result, now=NOW, started_at=NOW - timedelta(seconds=100))
        snapshot = self.snapshot(observation=None, **kwargs)
        self.assertEqual(snapshot['versions']['running_release'], obs.runtime_observation.release_version)
        self.assertEqual(snapshot['last_run']['forward_result'], 'failed')
        self.assertEqual(snapshot['last_run']['restore_result'], 'verified')
        event = OperationEvent('restoring', 'restore', 'restore', NOW, 'PRIVATE-TOKEN', phase='restoring_volumes')
        projected = operation_projection(event, started_at=NOW)
        self.assertNotIn('PRIVATE-TOKEN', json.dumps(projected))
        for private in ('proof_sha256', 'inventory_sha256', 'attempt_id', 'operation_token', 'storage_sha256'):
            self.assertNotIn(private, json.dumps(snapshot))
        with self.assertRaises(ValueError):
            restoration_projection({'outcome': 'restored'}, now=NOW)

    def test_phase_and_new_reason_vocab_matches_shared_types(self):
        from typing import get_args
        from deploy.update.contracts import AutomaticPhase
        phases = VALIDATORS['status'].schema['properties']['operation']['properties']['phase']['anyOf'][0]['enum']
        self.assertEqual(phases, list(get_args(AutomaticPhase)))
        for reason in (ReasonCode.MIGRATION_EXCEEDS_WINDOW, ReasonCode.INSUFFICIENT_DISK_FOR_MIGRATION,
                       ReasonCode.BACKUP_EXCEEDS_WINDOW, ReasonCode.INSUFFICIENT_DISK_FOR_BACKUP,
                       ReasonCode.MIGRATION_STALLED, ReasonCode.DISK_WATERMARK_ABORT):
            snapshot = self.snapshot(reason_codes=(reason,))
            self.assertEqual(snapshot['automation']['state'], 'blocked')


class PublisherTests(PublisherCase):
    def test_atomic_replacement_and_modes(self):
        first = self.publish()
        path = self.publisher.path / 'status.json'
        before = path.stat()
        fd = os.open(str(path), os.O_RDONLY)
        self.addCleanup(os.close, fd)
        second = self.publish()
        old = json.loads(os.read(fd, 32768))
        new = json.loads(path.read_bytes())
        self.assertEqual((old['sequence'], new['sequence']), (1, 2))
        self.assertNotEqual(before.st_ino, path.stat().st_ino)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.publisher.path.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(self.publisher.path.iterdir()), [path])
        self.assertEqual((first.generation, second.generation), (1, 2))

    def test_scheduler_private_umask_still_creates_public_modes(self):
        old = os.umask(0o077)
        try:
            self.publish()
        finally:
            os.umask(old)
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.publisher.path.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.publisher.path / 'status.json').stat().st_mode & 0o777, 0o644)

    def test_cas_generation_and_operation_token(self):
        old, _ = self.publisher.capture()
        new = self.publish()
        path = self.publisher.path / 'status.json'
        before = path.read_bytes()
        for ticket in (old, PublicationTicket(new.generation, 'wrong-operation')):
            with self.assertRaises(StaleGeneration):
                self.publisher.publish(self.snapshot(), expected=ticket)
            self.assertEqual(path.read_bytes(), before)

    def test_heartbeat_retains_stale_discovery_versions_and_active_progress(self):
        op = dict(state='applying', kind='apply', stage='rollout', started_at=stamp(NOW), finished_at=None,
                  reported_at=stamp(NOW), reason_codes=[])
        ticket = self.publish(self.snapshot(operation=op))
        now = NOW + timedelta(days=2)
        self.publisher.heartbeat(expected=ticket, now=now, last_tick_at=now)
        _, new = self.publisher.capture()
        self.assertEqual(new['generated_at'], stamp(now))
        self.assertEqual(new['operation']['reported_at'], stamp(NOW))
        self.assertEqual(new['versions']['observed_at'], stamp(NOW))
        self.assertEqual(new['discovery']['checked_at'], stamp(NOW))
        self.assertEqual(new['automation']['last_tick_at'], stamp(now))
        # Heartbeats merge into the newest snapshot; they never own/fence an operation.
        newer = self.publisher.heartbeat(expected=ticket, now=now)
        self.assertEqual(newer.generation, ticket.generation + 2)
        self.assertEqual(newer.epoch, ticket.epoch)

    def test_empty_heartbeat_and_invalid_payload_write_nothing(self):
        with self.assertRaises(PublicationError):
            self.publisher.heartbeat(expected=PublicationTicket(), now=NOW)
        invalid = self.snapshot()
        invalid['password'] = 'CANARY'
        with self.assertRaises(ContractError):
            self.publish(invalid)
        self.assertFalse(self.parent.exists())

    def test_private_presentation_history_not_public_authority(self):
        self.publish()
        path = self.publisher.path / 'status.json'
        path.write_text('{"sequence":9999,"token":"password"}')
        self.assertEqual(self.publisher.capture()[0].generation, 1)
        self.publish()
        self.assertEqual(json.loads(path.read_bytes())['sequence'], 2)

    def test_failure_before_replace_preserves_old_file_and_consumes_private_sequence(self):
        self.publish()
        path = self.publisher.path / 'status.json'
        old = path.read_bytes()
        original = os.replace
        def fail_public(source, destination, **kwargs):
            if destination == 'status.json':
                raise OSError('CANARY-password /secret/file')
            return original(source, destination, **kwargs)
        with mock.patch('os.replace', side_effect=fail_public):
            with self.assertRaises(PublicationError) as caught:
                self.publish()
        self.assertEqual(str(caught.exception), 'status_reporting_unavailable')
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(self.publisher.capture()[0].generation, 2)
        self.assertEqual(list(self.publisher.path.iterdir()), [path])

    def test_fsync_failure_never_leaves_sensitive_or_partial_public_temp(self):
        self.publish()
        path = self.publisher.path / 'status.json'
        old = path.read_bytes()
        from deploy.update.envcommit import sync_file
        def fail_public_sync(fd):
            if os.fstat(fd).st_mode & 0o777 == 0o644:
                raise OSError('CANARY')
            return sync_file(fd)
        with mock.patch('deploy.update.envcommit.sync_file', side_effect=fail_public_sync):
            with self.assertRaises(PublicationError):
                self.publish()
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(list(self.publisher.path.iterdir()), [path])
        self.assertEqual(self.publisher.capture()[0].generation, 2)

    def test_parent_slot_and_file_symlinks_are_not_followed(self):
        foreign = self.root / 'foreign'
        foreign.mkdir(mode=0o755)
        for location in ('parent', 'slot', 'file'):
            with self.subTest(location=location):
                parent = self.root / ('public-' + location)
                pub = PublicStatusPublisher(self.store, parent)
                if location == 'parent':
                    parent.symlink_to(foreign, target_is_directory=True)
                else:
                    parent.mkdir(mode=0o755)
                    if location == 'slot':
                        pub.path.symlink_to(foreign, target_is_directory=True)
                    else:
                        pub.path.mkdir(mode=0o755)
                        (pub.path / 'status.json').symlink_to(foreign / 'secret')
                with self.assertRaises(PublicationError):
                    pub.publish(self.snapshot(), expected=pub.capture()[0])
                self.assertEqual(list(foreign.iterdir()), [])

    def test_hardlink_and_fifo_and_bad_permissions_refuse(self):
        self.publish()
        path = self.publisher.path / 'status.json'
        for kind in ('hardlink', 'fifo', 'mode'):
            with self.subTest(kind=kind):
                path.unlink()
                if kind == 'hardlink':
                    other = self.root / 'other'
                    other.write_text('CANARY')
                    os.link(other, path)
                elif kind == 'fifo':
                    os.mkfifo(path, 0o644)
                else:
                    path.write_text('{}')
                    path.chmod(0o600)
                with self.assertRaises(PublicationError):
                    self.publish()

    def test_untrusted_parent_and_ancestor_rejected_without_chmod(self):
        self.parent.mkdir(mode=0o700)
        with self.assertRaises(PublicationError):
            self.publish()
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o700)
        self.parent.chmod(0o755)
        ticket = self.publisher.capture()[0]
        self.root.chmod(0o777)
        with self.assertRaises(PublicationError):
            self.publisher.publish(self.snapshot(), expected=ticket)
        self.root.chmod(0o700)

    def test_private_tree_relative_and_traversal_paths_rejected(self):
        for parent in (Path('relative'), self.root / '..' / 'public', self.store.path / 'public'):
            with self.assertRaises(PublicationError):
                PublicStatusPublisher(self.store, parent)

    def test_corrupt_private_metadata_and_counter_exhaustion_fail_closed(self):
        for value in ({'unexpected': 'secret'}, {'generation': True, 'operation_token': '', 'snapshot': self.snapshot()},
                      {'generation': 3, 'operation_token': '', 'snapshot': self.snapshot()}):
            self.publisher.state.write_private('automation', self.publisher.key, value)
            with self.assertRaises(PublicationError):
                self.publisher.capture()
        value = self.snapshot()
        value['sequence'] = 2**53 - 1
        self.publisher.state.write_private('automation', self.publisher.key,
                                          {'generation': value['sequence'], 'operation_token': '', 'snapshot': value})
        with self.assertRaises(PublicationError):
            self.publisher.publish(value, expected=PublicationTicket(value['sequence']))

    def test_failed_discovery_cannot_advance_success_during_cas_publish(self):
        self.publish()
        old = self.publisher.capture()[1]
        self.state = replace(self.state, last_attempt_at=NOW + timedelta(hours=1))
        snapshot = self.snapshot(previous=old, discovery=DiscoveryResult(reason_codes=(ReasonCode.NETWORK_UNAVAILABLE,)),
                                 now=NOW + timedelta(hours=1))
        snapshot['discovery']['checked_at'] = stamp(NOW + timedelta(minutes=1))
        with self.assertRaises(PublicationError):
            self.publish(snapshot)
        self.assertEqual(self.publisher.capture()[0].generation, 1)

    def test_heartbeat_without_tick_keeps_all_observations(self):
        ticket = self.publish()
        _, old = self.publisher.capture()
        self.publisher.heartbeat(expected=ticket, now=NOW + timedelta(minutes=10))
        _, new = self.publisher.capture()
        for name in ('versions', 'discovery', 'download', 'operation', 'automation'):
            self.assertEqual(old[name], new[name])

    def test_wrong_public_identity_and_invalid_ticket_are_rejected(self):
        snapshot = self.snapshot()
        snapshot['installation']['slot_id'] = 'f'*32
        with self.assertRaises(ContractError):
            self.publish(snapshot)
        for ticket in (None, PublicationTicket(False), PublicationTicket(-1), PublicationTicket(0, None)):
            with self.assertRaises(PublicationError):
                self.publisher.publish(self.snapshot(), expected=ticket)


class PublicationConcurrencyTests(PublisherCase):
    def operation(self, state='applying', stage='rollout'):
        return dict(state=state, kind='apply', stage=stage, started_at=stamp(NOW),
                    finished_at=None if state == 'applying' else stamp(NOW),
                    reported_at=stamp(NOW), reason_codes=[])

    def test_manual_terminal_report_survives_heartbeat_without_refreshing_observations(self):
        owner = self.publish(self.snapshot(operation=self.operation()))
        previous = self.publisher.capture()[1]
        heartbeat = self.publisher.heartbeat(expected=owner, now=NOW + timedelta(minutes=5))
        mid = self.publisher.capture()[1]
        for field in ('operation', 'safety', 'versions', 'discovery', 'download', 'last_run'):
            self.assertEqual(mid[field], previous[field])
        terminal = self.snapshot(previous=previous, now=NOW + timedelta(minutes=6),
                                 operation=self.operation('succeeded', None))
        result = self.publisher.publish(terminal, expected=owner, operation_token=owner.operation_token)
        self.assertEqual(result.generation, heartbeat.generation + 1)
        self.assertEqual(self.publisher.capture()[1]['operation']['state'], 'succeeded')

    def test_controls_keep_manual_ticket_and_newest_progress_safety_and_observations(self):
        owner = self.publish(self.snapshot(operation=self.operation()))
        before = self.publisher.capture()[1]
        sequence = owner.generation
        for action in ('pause', 'disable', 'enable', 'resume'):
            with self.subTest(action=action):
                self.store.change_control(action, NOW)
                control = self.publisher.publish_controls(policy=self.policy, now=NOW)
                newest = self.publisher.capture()[1]
                self.assertEqual((control.epoch, control.operation_token), (owner.epoch, owner.operation_token))
                self.assertEqual(control.generation, sequence + 1)
                sequence = control.generation
                for field in ('operation', 'safety', 'versions', 'discovery', 'download', 'last_run'):
                    self.assertEqual(newest[field], before[field])
        final = self.snapshot(previous=before, operation=self.operation('failed', None))
        owner = self.publisher.publish(final, expected=owner, operation_token=owner.operation_token)
        self.assertEqual(owner.generation, sequence + 1)
        self.assertEqual(self.publisher.capture()[1]['operation']['state'], 'failed')

    def test_operation_rebases_intervening_controls_not_its_old_automation_snapshot(self):
        owner = self.publish()
        stale = self.snapshot(operation=self.operation('succeeded', None))
        self.store.change_control('pause', NOW)
        self.publisher.publish_controls(policy=self.policy, now=NOW)
        self.publisher.publish(stale, expected=owner, operation_token='manual')
        newest = self.publisher.capture()[1]
        self.assertEqual(newest['automation']['state'], 'paused')
        self.assertEqual(newest['operation']['state'], 'succeeded')

    def test_pause_expiry_during_manual_operation_is_recomputed_when_rebasing(self):
        owner = self.publish()
        self.store.change_control('pause', NOW, until=NOW + timedelta(minutes=1))
        self.publisher.publish_controls(policy=self.policy, now=NOW)
        final = self.snapshot(now=NOW + timedelta(minutes=2), operation=self.operation('succeeded', None))
        self.publisher.publish(final, expected=owner, operation_token='manual')
        self.assertEqual(self.publisher.capture()[1]['automation']['state'], 'enabled')
        self.assertIsNone(self.publisher.capture()[1]['automation']['next_action_at'])

    def test_controls_and_heartbeat_merge_newer_operation_instead_of_stale_capture(self):
        old = self.publish()
        newer = self.publish(self.snapshot(operation=self.operation('failed', None)))
        before = self.publisher.capture()[1]
        self.store.change_control('disable', NOW)
        controls = self.publisher.publish_controls(policy=self.policy, now=NOW)
        beat = self.publisher.heartbeat(expected=old, now=NOW + timedelta(minutes=5))
        current, snapshot = self.publisher.capture()
        self.assertEqual(current, beat)
        self.assertEqual((newer.generation, controls.generation, beat.generation), (2, 3, 4))
        self.assertEqual(snapshot['operation'], before['operation'])
        self.assertEqual(snapshot['safety'], before['safety'])
        self.assertEqual(snapshot['automation']['state'], 'disabled')
        self.assertEqual(beat.epoch, newer.epoch)
        with self.assertRaises(StaleGeneration) as caught:
            self.publisher.publish(self.snapshot(), expected=old, operation_token='late-check')
        self.assertEqual(caught.exception.reason_code, ReasonCode.STATUS_REPORTING_UNAVAILABLE)
        self.assertEqual(self.publisher.capture()[0], beat)

    def test_first_control_report_does_not_fence_a_manual_command_already_started(self):
        owner, _ = self.publisher.capture()
        control = self.publisher.publish_controls(policy=self.policy, now=NOW)
        result = self.publisher.publish(self.snapshot(), expected=owner, operation_token='manual')
        self.assertEqual((control.generation, result.generation), (1, 2))
        self.assertEqual((control.epoch, result.epoch), (0, 1))

    def test_busy_public_lock_is_typed_and_does_not_consume_owner_ticket(self):
        from deploy.update.runner_state import StoreBusy
        owner = self.publish()
        for write in (lambda: self.publisher.publish(self.snapshot(), expected=owner),
                      lambda: self.publisher.publish_controls(policy=self.policy, now=NOW),
                      lambda: self.publisher.heartbeat(now=NOW)):
            with self.store._lock('public.lock'), self.assertRaises(StoreBusy):
                write()
            self.assertEqual(self.publisher.capture()[0], owner)
        final = self.publisher.publish(self.snapshot(), expected=owner, operation_token='manual')
        self.assertEqual(final.generation, owner.generation + 1)

    def test_resolved_recovery_drops_block_and_action_but_retains_failed_apply_history(self):
        op = dict(self.operation('blocked', None), kind='check', reason_codes=['recovery_required'])
        previous = self.snapshot(operation=op, reason_codes=(ReasonCode.RECOVERY_REQUIRED,))
        for _ in range(3):
            previous = self.snapshot(previous=previous, observation=None, discovery=None)
            self.assertEqual(previous['operation']['state'], 'idle')
            self.assertEqual(previous['safety']['operator_action'], 'none')
        failed = self.snapshot(operation=self.operation('failed', None))
        self.assertEqual(self.snapshot(previous=failed, discovery=None, observation=None)['operation'], failed['operation'])

    def test_failed_operation_rebased_over_heartbeat_remains_blocked(self):
        owner = self.publish()
        self.publisher.heartbeat(now=NOW)
        final = self.snapshot(reason_codes=(ReasonCode.BACKUP_REQUIRED,))
        self.publisher.publish(final, expected=owner)
        self.assertEqual(self.publisher.capture()[1]['automation']['state'], 'blocked')

    def test_legacy_metadata_upgrades_and_invalid_epochs_are_rejected(self):
        value = self.snapshot()
        value['sequence'] = 7
        legacy = {'generation': 7, 'operation_token': 'legacy', 'snapshot': value}
        self.publisher.state.write_private('automation', self.publisher.key, legacy)
        ticket, _ = self.publisher.capture()
        self.assertEqual(ticket.epoch, 7)
        result = self.publisher.publish(value, expected=PublicationTicket(7, 'legacy'), operation_token='new')
        self.assertEqual((result.generation, result.epoch), (8, 8))
        for epoch in (-1, True, 8, '7'):
            self.publisher.state.write_private('automation', self.publisher.key, dict(legacy, operation_epoch=epoch))
            with self.assertRaises(PublicationError):
                self.publisher.capture()

    def test_control_and_heartbeat_io_failures_are_sanitized(self):
        self.publish()
        with mock.patch.object(self.publisher, '_replace', side_effect=OSError('/secret CANARY')):
            for write in (lambda: self.publisher.heartbeat(now=NOW),
                          lambda: self.publisher.publish_controls(policy=self.policy, now=NOW)):
                with self.assertRaises(PublicationError) as caught:
                    write()
                self.assertEqual(str(caught.exception), 'status_reporting_unavailable')


class PublicationCrashTests(PublisherCase):
    def test_killed_writer_temp_is_reclaimed_without_touching_any_other_entry(self):
        self.publish()
        pid = os.fork()
        if pid == 0:
            original = os.replace
            def die(source, destination, **kwargs):
                if destination == 'status.json':
                    os._exit(9)
                return original(source, destination, **kwargs)
            with mock.patch('os.replace', side_effect=die):
                self.publish()
            os._exit(10)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.WEXITSTATUS(status), 9)
        leftovers = list(self.publisher.path.glob('.status-*.tmp'))
        self.assertEqual(len(leftovers), 1)
        self.assertEqual(leftovers[0].stat().st_mode & 0o777, 0o644)
        target = self.root / 'target'
        target.write_text('CANARY')
        link = self.publisher.path / ('.status-' + 'a' * 32 + '.tmp')
        link.symlink_to(target)
        directory = self.publisher.path / ('.status-' + 'b' * 32 + '.tmp')
        directory.mkdir()
        unrelated = self.publisher.path / '.status-unrelated.tmp'
        unrelated.write_text('keep')
        for _ in range(3):
            self.publish()
        self.assertFalse(leftovers[0].exists())
        self.assertTrue(link.is_symlink())
        self.assertTrue(directory.is_dir())
        self.assertEqual(target.read_text(), 'CANARY')
        self.assertEqual(unrelated.read_text(), 'keep')
        self.assertEqual(self.publisher.capture()[0].generation,
                         json.loads((self.publisher.path / 'status.json').read_bytes())['sequence'])

    def test_unbound_parent_and_slot_mkdir_crashes_require_manual_remediation(self):
        original = os.mkdir
        for part in ('public', self.binding.slot_id):
            with self.subTest(part=part):
                if part == self.binding.slot_id:
                    # Explicit test-owner remediation created the slot; remove only that slot.
                    (self.publisher.path / 'status.json').unlink()
                    self.publisher.path.rmdir()
                def crash(name, *args, **kwargs):
                    result = original(name, *args, **kwargs)
                    if name == part:
                        raise KeyboardInterrupt()
                    return result
                with mock.patch('os.mkdir', side_effect=crash), self.assertRaises(KeyboardInterrupt):
                    self.publish()
                damaged = self.parent if part == 'public' else self.publisher.path
                self.assertEqual(damaged.stat().st_mode & 0o777, 0o700)
                # No inode binding was committed before the interruption. Even
                # empty residue cannot be distinguished from a foreign creation.
                self.publisher = PublicStatusPublisher(self.store, self.parent)
                with self.assertRaises(PublicationError):
                    self.publish()
                self.assertEqual(damaged.stat().st_mode & 0o777, 0o700)
                # Only this fixture owner knows this empty directory is ours.
                damaged.chmod(0o755)
                self.publish()
        alien = self.root / 'operator-private'
        alien.mkdir(mode=0o700)
        other = PublicStatusPublisher(self.store, alien)
        with self.assertRaises(PublicationError):
            other.publish(self.snapshot(), expected=other.capture()[0])
        self.assertEqual(alien.stat().st_mode & 0o777, 0o700)

    def test_crash_before_chmod_recovers_only_matching_owned_inode(self):
        original = os.fchmod
        def crash(fd, mode):
            if mode == 0o755:
                raise KeyboardInterrupt()
            return original(fd, mode)
        with mock.patch('os.fchmod', side_effect=crash), self.assertRaises(KeyboardInterrupt):
            self.publish()
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o700)
        self.publish()
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o755)

    def test_creation_race_never_adopts_another_creators_directory(self):
        original = os.mkdir
        def race(name, *args, **kwargs):
            if name == 'public':
                original(name, *args, **kwargs)
                raise FileExistsError()
            return original(name, *args, **kwargs)
        with mock.patch('os.mkdir', side_effect=race), self.assertRaises(PublicationError):
            self.publish()
        with self.assertRaises(PublicationError):
            self.publish()
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
