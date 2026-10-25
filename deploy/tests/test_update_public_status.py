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
        with self.assertRaises(StaleGeneration):
            self.publisher.heartbeat(expected=ticket, now=now)

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


if __name__ == '__main__':
    unittest.main()
