"""Recovery presentation survives non-mutating reports until explicit completion."""
from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from deploy.tests.test_update_compatibility import NOW, restore_context
from deploy.tests.test_update_public_status import PublisherCase, ROOT
from deploy.update import __main__ as cli
from deploy.update.contracts import OperationEvent, ReasonCode, RestorationResult
from deploy.update.public_status import operation_projection, restoration_projection, stamp


class RecoveryReportingTests(PublisherCase):
    def setUp(self):
        super().setUp()
        self.policy = replace(self.policy, mode='apply', qualification_policy='standalone-cold-restore-v1')
        # Load the shipped stdlib-only application reader, not a test decoder.
        spec = importlib.util.spec_from_file_location('recovery_status_reader',
            ROOT / 'futureagi/tfc/host_updates/status.py')
        self.reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.reader)

    def unresolved(self, state='recovery_required'):
        prepared, obs, _, _ = restore_context()
        result = RestorationResult(obs.attempt_id, 'recovery_required', obs.runtime_observation.release_version,
            prepared.manifest['version'], 'previous_verifying', obs.backup_seal, None,
            (ReasonCode.AUTOMATIC_RECOVERY_PENDING,), 'review_recovery')
        projection = restoration_projection(result, now=NOW, started_at=NOW - timedelta(seconds=100))
        projection['operation'].update(state=state, finished_at=None)
        if state == 'restoring':
            projection['safety'].update(restore_state='running', guidance_id='none', operator_action='none')
            projection['operation']['reason_codes'] = []
        return self.snapshot(**projection)

    def assert_unresolved(self, current, previous):
        self.assertEqual(current['operation'], previous['operation'])
        self.assertEqual(current['safety'], previous['safety'])
        self.assertEqual(current['last_run'], previous['last_run'])
        self.assertEqual(current['versions']['runtime_state'], 'unknown')
        self.assertIsNone(current['versions']['running_release'])
        self.assertIsNone(current['last_run']['effective_version'])

    def test_refused_manual_commands_publish_without_erasing_recovery(self):
        # Exercise the actual CLI reporting boundary, private CAS publisher and
        # shipped reader. A label inspection (even without an env journal) is
        # not evidence that a cold automatic transaction finished.
        for state in ('restoring', 'recovery_required'):
            for pending in (False, True):
                for kind in ('apply', 'rollback'):
                    with self.subTest(state=state, pending=pending, kind=kind):
                        previous = self.unresolved(state)
                        self.publish(previous)
                        observation = replace(self.observation, pending_transaction=pending,
                            reason_codes=(ReasonCode.RECOVERY_REQUIRED,) if pending else ())
                        now = NOW + timedelta(seconds=10)
                        with mock.patch.object(cli, '_now', return_value=now), \
                                mock.patch.object(cli, '_reporting', return_value=self.publisher), \
                                mock.patch.object(cli, 'observe_status', return_value=observation):
                            publication = cli._Publication(self.store,
                                SimpleNamespace(automation_policy=self.policy), required=True)
                            publication.finish(installation=object(), kind=kind, code=3)
                        raw = (self.publisher.path / 'status.json').read_bytes()
                        current = self.reader.decode_public_status(raw,
                            expected_installation=previous['installation'], now=now)
                        self.assert_unresolved(current, previous)
                        for private in (str(self.root), 'SECRET', 'proof_sha256', 'inventory_sha256',
                                        'attempt_id', publication.token):
                            self.assertNotIn(private, raw.decode())

    def test_unrelated_reports_cannot_clear_recovery_or_its_last_run(self):
        manual_run = dict(kind='apply', automatic=False, outcome='blocked', from_version='v1.51.0',
            target_version=self.release.version, finished_at=stamp(NOW), fallback_outcome='none')
        for state in ('restoring', 'recovery_required'):
            previous = self.unresolved(state)
            for kind in ('apply', 'rollback', 'check', 'download'):
                for outcome in ('blocked', 'failed', 'interrupted', 'succeeded'):
                    with self.subTest(state=state, kind=kind, outcome=outcome):
                        operation = dict(state=outcome, kind=kind, stage=None, started_at=stamp(NOW),
                            finished_at=stamp(NOW), reported_at=stamp(NOW), reason_codes=[])
                        current = self.snapshot(previous=previous, operation=operation,
                            observation=replace(self.observation, last_run=manual_run), last_run=manual_run)
                        self.assert_unresolved(current, previous)
            with self.subTest(state=state, operation=None):
                current = self.snapshot(previous=previous, observation=replace(self.observation, last_run=manual_run))
                self.assert_unresolved(current, previous)

    def test_phase_less_recovery_required_derives_safety_without_inventing_backup(self):
        for kind in ('check', 'apply', 'restore'):
            for include_phase in (False, True):
                with self.subTest(kind=kind, include_phase=include_phase):
                    event = OperationEvent('recovery_required', kind, None, NOW, 'private-event-token',
                        (ReasonCode.AUTOMATIC_RECOVERY_PENDING,))
                    operation = operation_projection(event, started_at=NOW, finished_at=NOW)
                    if not include_phase:
                        operation.pop('phase')
                    snapshot = self.snapshot(operation=operation, reason_codes=event.reason_codes)
                    self.assertEqual(snapshot['safety']['restore_state'], 'recovery_required')
                    self.assertEqual(snapshot['safety']['guidance_id'], 'review_recovery')
                    self.assertEqual(snapshot['safety']['operator_action'], 'review_recovery')
                    self.assertEqual(snapshot['safety']['backup_state'], 'not_evaluated')
                    self.assertIsNone(snapshot['safety']['snapshot_at'])
                    self.assertIsNone(snapshot['versions']['running_release'])
                    self.reader.decode_public_status(json.dumps(snapshot).encode(),
                        expected_installation=snapshot['installation'], now=NOW)
                    self.assertNotIn(event.operation_token, json.dumps(snapshot))

    def test_phase_less_restoring_derives_running_state(self):
        event = OperationEvent('restoring', 'restore', 'restore', NOW, 'private-event-token')
        snapshot = self.snapshot(operation=operation_projection(event, started_at=NOW))
        self.assertEqual(snapshot['safety']['restore_state'], 'running')
        self.assertIsNone(snapshot['versions']['running_release'])

    def test_phase_less_recovery_update_keeps_last_verified_phase_and_snapshot(self):
        previous = self.unresolved('restoring')
        operation = dict(state='recovery_required', kind='check', stage=None, started_at=stamp(NOW),
            finished_at=stamp(NOW), reported_at=stamp(NOW), reason_codes=['automatic_recovery_pending'])
        current = self.snapshot(previous=previous, operation=operation,
            reason_codes=(ReasonCode.AUTOMATIC_RECOVERY_PENDING,))
        self.assertEqual(current['operation']['state'], 'recovery_required')
        self.assertEqual(current['operation']['phase'], previous['operation']['phase'])
        self.assertEqual(current['safety']['snapshot_at'], previous['safety']['snapshot_at'])
        self.assertEqual(current['safety']['backup_state'], 'sealed')
        self.assertEqual(current['last_run'], previous['last_run'])
        self.assertEqual(current['safety']['restore_state'], 'recovery_required')
        self.assertEqual(current['safety']['guidance_id'], 'review_recovery')

    def test_explicit_recovery_completion_replaces_unresolved_status(self):
        prepared, obs, _, _ = restore_context()
        for outcome in ('restored', 'failed_available'):
            with self.subTest(outcome=outcome):
                previous = self.unresolved()
                result = RestorationResult(obs.attempt_id, outcome, obs.runtime_observation.release_version,
                    prepared.manifest['version'], outcome, obs.backup_seal if outcome == 'restored' else None,
                    obs.runtime_observation)
                current = self.snapshot(previous=previous, **restoration_projection(result, now=NOW, started_at=NOW))
                self.assertEqual(current['operation']['phase'], outcome)
                self.assertEqual(current['operation']['state'], 'restored' if outcome == 'restored' else 'failed')
                self.assertEqual(current['safety']['restore_state'], 'verified' if outcome == 'restored' else 'not_needed')
                self.assertEqual(current['safety']['operator_action'], 'none')
                self.assertEqual(current['last_run']['effective_version'], obs.runtime_observation.release_version)
                self.reader.decode_public_status(json.dumps(current).encode(),
                    expected_installation=current['installation'], now=NOW)

    def test_terminal_event_does_not_inherit_unresolved_safety(self):
        previous = self.unresolved()
        for state, kind, phase, restore_result in (
                ('restored', 'restore', 'restored', 'verified'),
                ('failed', 'apply', 'failed_available', 'not_needed'),
                ('succeeded', 'apply', 'succeeded', 'not_needed')):
            with self.subTest(phase=phase):
                event = OperationEvent(state, kind, None, NOW, 'private-event-token', phase=phase)
                run = dict(previous['last_run'], restore_result=restore_result)
                if state == 'succeeded':
                    run.update(outcome='succeeded', forward_result='succeeded', failed_target_version=None)
                snapshot = self.snapshot(previous=previous, observation=None,
                    operation=operation_projection(event, started_at=NOW, finished_at=NOW), last_run=run)
                self.assertEqual(snapshot['operation']['state'], state)
                self.assertEqual(snapshot['safety']['restore_state'], restore_result)
                self.assertEqual(snapshot['safety']['operator_action'], 'none')
                self.assertEqual(snapshot['safety']['guidance_id'], 'none')
                self.assertIsNone(snapshot['versions']['running_release'])

    def test_explicit_accepted_success_completion_replaces_unresolved_status(self):
        previous = self.unresolved()
        operation = dict(state='succeeded', kind='apply', stage=None, phase='succeeded', started_at=stamp(NOW),
            finished_at=stamp(NOW), reported_at=stamp(NOW), reason_codes=[])
        run = dict(kind='apply', automatic=True, outcome='succeeded', from_version='v1.51.0',
            target_version=self.release.version, finished_at=stamp(NOW), fallback_outcome='none',
            forward_result='succeeded', restore_result='not_needed', effective_version=self.release.version,
            failed_target_version=None)
        safety = dict(backup_required=True, backup_state='sealed', readiness='passed', automatic_rollback='ineligible',
            operator_action='none', fallback_strategy='restore_backup', restore_state='not_needed', guidance_id='none')
        current = self.snapshot(previous=previous, operation=operation, last_run=run, safety=safety)
        self.assertEqual(current['operation'], operation)
        self.assertEqual(current['last_run'], run)
        self.assertEqual(current['safety']['restore_state'], 'not_needed')
        self.assertEqual(current['safety']['operator_action'], 'none')

    def test_accepted_success_metadata_error_does_not_request_restoration(self):
        run = dict(kind='apply', automatic=True, outcome='succeeded', from_version='v1.51.0',
            target_version=self.release.version, finished_at=stamp(NOW), fallback_outcome='none',
            forward_result='succeeded', restore_result='not_needed', effective_version=None, failed_target_version=None)
        for phase in (None, 'success_decided'):
            with self.subTest(phase=phase):
                operation = dict(state='recovery_required', kind='apply', stage=None, phase=phase,
                    started_at=stamp(NOW), finished_at=stamp(NOW), reported_at=stamp(NOW),
                    reason_codes=['automatic_recovery_pending'])
                snapshot = self.snapshot(operation=operation, last_run=run,
                    reason_codes=(ReasonCode.AUTOMATIC_RECOVERY_PENDING,))
                self.assertEqual(snapshot['safety']['restore_state'], 'not_needed')
                self.assertEqual(snapshot['last_run']['forward_result'], 'succeeded')
                self.assertEqual(snapshot['safety']['guidance_id'], 'review_recovery')

    def test_unresolved_recovery_survives_controls_heartbeat_and_read_only_export(self):
        previous = self.unresolved()
        self.publish(previous)
        self.store.change_control('disable', NOW)
        self.publisher.publish_controls(policy=self.policy, now=NOW)
        self.publisher.heartbeat(now=NOW)
        with mock.patch.object(cli, '_now', return_value=NOW), \
                mock.patch.object(cli, '_reporting', return_value=self.publisher), \
                mock.patch.object(cli, 'observe_status', return_value=self.observation):
            publication = cli._Publication(self.store, SimpleNamespace(automation_policy=self.policy), required=True)
            publication.finish(installation=object())
        current = self.reader.decode_public_status((self.publisher.path / 'status.json').read_bytes(),
            expected_installation=previous['installation'], now=NOW)
        self.assert_unresolved(current, previous)
        self.assertEqual(current['automation']['state'], 'disabled')
