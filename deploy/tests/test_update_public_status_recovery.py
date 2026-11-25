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
from deploy.tests.test_update_runner import RunnerCase
from deploy.update import __main__ as cli
from deploy.update.contracts import AutomaticUpdateDecision, OperationEvent, ReasonCode, RestorationResult
from deploy.update.orchestrator import EngineResult
from deploy.update.public_status import operation_projection, restoration_projection, stamp
from deploy.update.runner_state import StoreError


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

    def test_notify_only_and_stale_success_receipts_do_not_clear_recovery(self):
        stale_success = dict(kind='apply', automatic=True, outcome='succeeded', from_version='v1.50.0',
            target_version='v1.51.0', finished_at=stamp(NOW - timedelta(days=1)), fallback_outcome='none',
            forward_result='succeeded', restore_result='not_needed', effective_version='v1.51.0',
            failed_target_version=None)
        for state in ('restoring', 'recovery_required'):
            previous = self.unresolved(state)
            for reason in (ReasonCode.RESTORE_NOT_CERTIFIED, ReasonCode.MIGRATION_EXCEEDS_WINDOW):
                with self.subTest(state=state, reason=reason):
                    operation = dict(state='blocked', kind='check', stage=None, started_at=stamp(NOW),
                        finished_at=stamp(NOW), reported_at=stamp(NOW), reason_codes=[reason.value])
                    current = self.snapshot(previous=previous, operation=operation,
                        observation=replace(self.observation, last_run=stale_success),
                        automatic_decision=AutomaticUpdateDecision(False, (reason,), None))
                    self.assert_unresolved(current, previous)
                    self.assertEqual(current['discovery']['state'], 'blocked')
                    self.assertEqual(current['discovery']['reason_codes'], [reason.value])
            for kind in ('apply', 'check'):
                for explicit in (False, True):
                    with self.subTest(state=state, kind=kind, explicit_stale_result=explicit):
                        operation = dict(state='succeeded', kind=kind, stage=None, started_at=stamp(NOW),
                            finished_at=stamp(NOW), reported_at=stamp(NOW), reason_codes=[])
                        current = self.snapshot(previous=previous, operation=operation,
                            observation=replace(self.observation, last_run=stale_success),
                            last_run=stale_success if explicit else None)
                        self.assert_unresolved(current, previous)

    def test_phase_less_success_requires_a_successful_automatic_forward_result(self):
        previous = self.unresolved()
        operation = dict(state='succeeded', kind='apply', stage=None, started_at=stamp(NOW),
            finished_at=stamp(NOW), reported_at=stamp(NOW), reason_codes=[])
        run = dict(kind='apply', automatic=True, outcome='succeeded', from_version='v1.51.0',
            target_version=self.release.version, finished_at=stamp(NOW), fallback_outcome='none',
            forward_result='succeeded', restore_result='not_needed', effective_version=self.release.version,
            failed_target_version=None)
        for change in (dict(automatic=False), dict(forward_result='failed'),
                       dict(restore_result='recovery_required'), dict(outcome='blocked')):
            with self.subTest(change=change):
                current = self.snapshot(previous=previous, operation=operation, last_run=dict(run, **change))
                self.assert_unresolved(current, previous)

    def test_old_accepted_success_does_not_exempt_a_new_unresolved_recovery(self):
        run = dict(kind='apply', automatic=True, outcome='succeeded', from_version='v1.51.0',
            target_version=self.release.version, finished_at=stamp(NOW), fallback_outcome='none',
            forward_result='succeeded', restore_result='not_needed', effective_version=None, failed_target_version=None)
        accepted = dict(state='recovery_required', kind='apply', stage=None, started_at=stamp(NOW),
            finished_at=stamp(NOW), reported_at=stamp(NOW), reason_codes=['automatic_recovery_pending'])
        previous = self.snapshot(operation=accepted, last_run=run, reason_codes=(ReasonCode.AUTOMATIC_RECOVERY_PENDING,))
        for kind, phase in (('check', None), ('restore', None), ('restore', 'restore_quiescing'),
                            ('apply', 'target_starting')):
            with self.subTest(kind=kind, phase=phase):
                operation = dict(accepted, kind=kind, phase=phase)
                current = self.snapshot(previous=previous, observation=None, operation=operation,
                    reason_codes=(ReasonCode.AUTOMATIC_RECOVERY_PENDING,))
                self.assertEqual(current['safety']['restore_state'], 'recovery_required')
                self.assertEqual(current['safety']['guidance_id'], 'review_recovery')
                self.assertIsNone(current['versions']['running_release'])

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


class RunnerRecoveryReportingTests(RunnerCase):
    """Real runner reporting/CAS/reader boundaries; engine outcomes are fixtures.

    No Docker or product qualification is implied by these finite outcomes.
    In particular, _finish deliberately replaces ordinary apply phases with a
    phase-less terminal report, unlike an explicit recovered-success projection.
    """
    def setUp(self):
        super().setUp()
        self.policy = replace(self.policy, mode='apply', qualification_policy='standalone-cold-restore-v1')
        spec = importlib.util.spec_from_file_location('runner_recovery_status_reader',
            ROOT / 'futureagi/tfc/host_updates/status.py')
        self.reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.reader)

    def automatic_runner(self, mode='apply', **kwargs):
        return self.runner(mode, settings=self.settings(mode, QUALIFICATION_POLICY=self.policy.qualification_policy),
                           **kwargs)

    def read_public(self):
        return self.reader.decode_public_status((self.publisher.path / 'status.json').read_bytes(),
            expected_installation=self.snapshot()['installation'], now=self.now)

    def pending(self):
        attempt = self.store.reserve_attempt(self.target(),
            'previous-window-' + str(self.store.read_state().generation), self.now)
        with self.store._lock('state.lock') as directory:
            self.store._write(directory, 'state.json', replace(self.store.read_state(), baseline_head='b' * 64))
        return attempt

    def accepted_engine(self, runner, *, pending=False):
        def execute(engine, guard):
            # Match orchestrator._execute_guarded's accepted-success outcome.
            runner.automatic = SimpleNamespace(preflight_decision=None)
            guard.report('after_backup', proof_complete=True)
            return EngineResult(3 if pending else 0, (ReasonCode.RECOVERY_REQUIRED,) if pending else (),
                True, True, 'succeeded', 'none', None, 'not_needed',
                None if pending else self.release.version, 'success_decided' if pending else 'succeeded')
        self.executor.side_effect = execute

    def test_later_automatic_success_clears_a_missed_recovery_completion(self):
        for prior_state in ('restoring', 'recovery_required'):
            with self.subTest(prior_state=prior_state):
                previous = RecoveryReportingTests.unresolved(self, prior_state)
                self.publish(previous)  # The prior restore finished but its report was lost.
                self.now += timedelta(seconds=10)
                runner = self.automatic_runner()
                self.accepted_engine(runner)
                result = runner.tick()
                self.assertEqual((result.code, result.disposition, result.forward_result), (0, 'applied', 'succeeded'))
                self.assertFalse(result.warnings)
                self.assertNotIn('phase', runner.operation)
                current = self.read_public()
                self.assertEqual(current['operation']['state'], 'succeeded')
                self.assertEqual(current['operation']['kind'], 'apply')
                self.assertEqual(current['last_run']['forward_result'], 'succeeded')
                self.assertEqual(current['last_run']['restore_result'], 'not_needed')
                self.assertEqual(current['last_run']['effective_version'], self.release.version)
                self.assertEqual(current['safety']['restore_state'], 'not_needed')
                self.assertEqual(current['safety']['guidance_id'], 'none')
                self.assertEqual(current['safety']['operator_action'], 'none')
                self.assertIsNone(self.store.read_state().active_attempt_id)
                self.save(next_check_at=None, cached_candidate_identity=None)

    def test_refused_manual_commands_preserve_phase_less_accepted_success(self):
        runner = self.automatic_runner()
        self.accepted_engine(runner, pending=True)
        result = runner.tick()
        self.assertEqual((result.disposition, result.forward_result), ('recovery_required', 'succeeded'))
        self.assertFalse(result.warnings)
        accepted = self.read_public()
        self.assertNotIn('phase', accepted['operation'])
        self.assertEqual(accepted['operation']['state'], 'recovery_required')
        self.assertEqual(accepted['last_run']['forward_result'], 'succeeded')
        self.assertEqual(accepted['safety']['restore_state'], 'not_needed')

        # A refused CLI transaction preserves this report, not a new recovery
        # event. Re-inferring safety from its banner would incorrectly request
        # restoration of a forward result already durably accepted by the runner.
        for kind in ('apply', 'rollback'):
            for pending in (False, True):
                with self.subTest(kind=kind, pending=pending):
                    self.now += timedelta(seconds=10)
                    observation = replace(self.observation, pending_transaction=pending,
                        reason_codes=(ReasonCode.RECOVERY_REQUIRED,) if pending else ())
                    sequence = self.read_public()['sequence']
                    with mock.patch.object(cli, '_now', return_value=self.now), \
                            mock.patch.object(cli, '_reporting', return_value=self.publisher), \
                            mock.patch.object(cli, 'observe_status', return_value=observation):
                        publication = cli._Publication(self.store,
                            SimpleNamespace(automation_policy=self.policy), required=True)
                        publication.finish(installation=object(), kind=kind, code=3)
                    current = self.read_public()
                    self.assertEqual(current['sequence'], sequence + 1)
                    RecoveryReportingTests.assert_unresolved(self, current, accepted)

    def test_phase_less_metadata_recovery_error_retains_accepted_forward_result(self):
        runner = self.automatic_runner()
        self.accepted_engine(runner, pending=True)
        first = runner.tick()
        self.assertEqual((first.disposition, first.forward_result), ('recovery_required', 'succeeded'))
        accepted = self.read_public()
        self.assertNotIn('phase', accepted['operation'])
        self.assertEqual(accepted['safety']['restore_state'], 'not_needed')
        self.pending()
        life = mock.Mock(success_decided=True, target=self.release)
        life.resume.side_effect = StoreError(ReasonCode.CONFIGURATION_CHANGED)
        for prior_restore_state in ('not_needed', 'recovery_required'):
            if prior_restore_state == 'recovery_required':
                # Repair a report misclassified by the previous producer, too.
                misclassified = self.publisher.capture()[1]
                misclassified['safety']['restore_state'] = prior_restore_state
                self.publish(misclassified)
            self.now += timedelta(seconds=10)
            recovery = self.automatic_runner('manual', recovery_factory=lambda *args: life)
            result = recovery.tick()
            self.assertEqual((result.disposition, result.forward_result), ('recovery_required', 'succeeded'))
            self.assertFalse(result.warnings)
            self.assertIsNone(recovery.last_run)  # Real _recover_pending error has no new projection.
            self.assertIsNone(recovery.safety)
            self.assertNotIn('phase', recovery.operation)
            current = self.read_public()
            self.assertEqual(current['operation']['state'], 'recovery_required')
            self.assertEqual(current['last_run'], accepted['last_run'])
            self.assertEqual(current['safety']['restore_state'], 'not_needed')
            self.assertEqual(current['safety']['guidance_id'], 'review_recovery')
            self.assertEqual(current['safety']['operator_action'], 'review_recovery')
            self.assertIsNone(current['versions']['running_release'])
            self.assertIsNotNone(self.store.read_state().active_attempt_id)

    def test_conclusive_runner_recovery_terminals_clear_unresolved_status(self):
        prepared, obs, _, _ = restore_context()
        for outcome in ('restored', 'failed_available'):
            with self.subTest(outcome=outcome):
                self.publish(RecoveryReportingTests.unresolved(self))
                attempt = self.pending()
                result = RestorationResult(attempt.attempt_id, outcome, obs.runtime_observation.release_version,
                    prepared.manifest['version'], outcome,
                    replace(obs.backup_seal, attempt_id=attempt.attempt_id) if outcome == 'restored' else None,
                    obs.runtime_observation)
                def resume(*args, **kwargs):
                    # The engine's conclusive result is emitted after terminal CAS.
                    self.save(active_attempt_id=None, active_operation_token=None)
                    return result
                life = SimpleNamespace(resume=resume)
                runner = self.automatic_runner('manual', recovery_factory=lambda *args: life)
                tick = runner.tick()
                self.assertEqual(tick.disposition, outcome)
                self.assertFalse(tick.warnings)
                current = self.read_public()
                self.assertEqual(current['operation']['phase'], outcome)
                self.assertEqual(current['safety']['restore_state'], 'verified' if outcome == 'restored' else 'not_needed')
                self.assertEqual(current['safety']['operator_action'], 'none')
                self.assertEqual(current['last_run']['effective_version'], obs.runtime_observation.release_version)
