"""One-shot runner with real private stores, fake Docker and injected clocks."""
from __future__ import annotations

import copy
import json
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests import test_update_public_status as pub  # noqa: E402
from deploy.tests import test_update_download as downloads  # noqa: E402
from deploy.tests.test_update_compatibility import NOW, prepare, default_v2  # noqa: E402
from deploy.update.compatibility import Observations  # noqa: E402
from deploy.update.contracts import ReasonCode  # noqa: E402
from deploy.update.discovery import DiscoveryResult  # noqa: E402
from deploy.update.orchestrator import EngineResult, GuardRefused  # noqa: E402
from deploy.update.public_status import PublicStatusPublisher  # noqa: E402
from deploy.update.runner import HostRunner, host_discovery, selected_release  # noqa: E402
from deploy.update.runner_state import StoreBusy, StoreError, TargetIdentity  # noqa: E402
from deploy.update.schedule import Clock  # noqa: E402
from deploy.update.settings import load_settings  # noqa: E402


class RunnerCase(pub.PublisherCase):
    def setUp(self):
        super().setUp()
        self.now = NOW
        self.monotonic = 0
        self.clock = Clock(lambda: self.now, lambda: self.monotonic)
        self.discoverer = mock.Mock()
        self.discoverer.discover.return_value = self.discovery
        self.discoverer.refresh.return_value = self.discovery
        self.discovery_factory = mock.Mock(return_value=self.discoverer)
        self.installation = mock.Mock()
        self.installation.identity.return_value = {}
        self.installation_factory = mock.Mock(return_value=self.installation)
        self.prepared = replace(prepare(default_v2()), binding=self.binding)
        self.observer = self.patch('deploy.update.runner.observe_status', return_value=self.observation)
        self.preparer = self.patch('deploy.update.runner.prepare_update', return_value=self.prepared)
        self.patch('deploy.update.runner.check_deployment')
        self.engine_factory = self.patch('deploy.update.runner.guarded_updater', return_value=mock.Mock())
        self.executor = self.patch('deploy.update.runner.execute_guarded', return_value=EngineResult(0, (), True, True, 'succeeded'))

    def patch(self, name, **kwargs):
        patch = mock.patch(name, **kwargs)
        value = patch.start()
        self.addCleanup(patch.stop)
        return value

    def settings(self, mode='notify', **values):
        overrides = {'FUTURE_AGI_UPDATE_AUTOMATION_MODE': mode, 'FUTURE_AGI_UPDATE_ENABLED': 'true',
                     'FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW': 'always'}
        overrides.update({'FUTURE_AGI_UPDATE_' + key: value for key, value in values.items()})
        return load_settings(self.root, overrides, environ={})

    def runner(self, mode='notify', **kwargs):
        args = dict(settings=self.settings(mode), publisher=self.publisher, clock=self.clock,
                    discovery_factory=self.discovery_factory, installation_factory=self.installation_factory)
        args.update(kwargs)
        return HostRunner(self.store, **args)

    def save(self, **changes):
        old = self.store.read_state()
        return self.store.save_state(replace(old, **changes), expected_generation=old.generation)

    def target(self):
        return TargetIdentity(self.release.version, self.release.manifest_hash)


class LocalExitTests(RunnerCase):
    def test_disabled_manual_paused_local_matrix(self):
        for mode in ('manual', 'notify', 'download', 'apply'):
            for action in ('disable', 'pause'):
                with self.subTest(mode=mode, action=action):
                    self.store.change_control('enable', self.now)
                    self.store.change_control('resume', self.now)
                    self.store.change_control(action, self.now)
                    result = self.runner(mode).tick()
                    self.assertEqual(result.code, 0)
                    self.assertEqual(result.skip_reason, 'disabled' if action == 'disable' else 'paused')
        self.store.change_control('enable', self.now)
        self.store.change_control('resume', self.now)
        self.assertEqual(self.runner('manual').tick().skip_reason, 'manual')
        self.discovery_factory.assert_not_called()
        self.installation_factory.assert_not_called()
        self.executor.assert_not_called()

    def test_unregistered_defaults_and_explicit_enable(self):
        from deploy.update.runner_state import RunnerStateStore
        other = self.root / 'another.env'
        other.write_text('FUTURE_AGI_VERSION=v1.51.0\n')
        store = RunnerStateStore(self.root, other)
        result = HostRunner(store, clock=self.clock).tick()
        self.assertEqual(result.skip_reason, 'disabled')
        result = HostRunner(store, clock=self.clock, settings=self.settings()).tick()
        self.assertEqual(result.reason_codes, (ReasonCode.SCHEDULER_UNREGISTERED,))
        self.discovery_factory.assert_not_called()

    def test_not_due_and_retry_delay_never_touch_network_or_docker(self):
        for changes in ({'next_check_at': NOW + timedelta(hours=6)},
                        {'next_check_at': NOW, 'retry_not_before': NOW + timedelta(seconds=1)}):
            self.save(**changes)
            result = self.runner().tick()
            self.assertEqual(result.skip_reason, 'not_due')
        self.discovery_factory.assert_not_called()
        self.installation_factory.assert_not_called()

    def test_pause_expires_at_exact_edge(self):
        self.store.change_control('pause', NOW, until=NOW + timedelta(seconds=10))
        self.now += timedelta(seconds=9)
        self.assertEqual(self.runner().tick().skip_reason, 'paused')
        self.now += timedelta(seconds=1)
        self.assertEqual(self.runner().tick().disposition, 'available')

    def test_busy_runner_does_not_change_public_or_state(self):
        self.publish()
        before = (self.publisher.path / 'status.json').read_bytes()
        state = self.store.read_state()
        with self.store.runner_lock():
            result = self.runner().tick()
        self.assertEqual(result.disposition, 'busy')
        self.assertEqual(self.store.read_state(), state)
        self.assertEqual((self.publisher.path / 'status.json').read_bytes(), before)
        self.discovery_factory.assert_not_called()

    def test_future_observations_and_backward_clock_fail_closed(self):
        self.save(last_tick_at=NOW + timedelta(seconds=301))
        self.assertEqual(self.runner().tick().reason_codes, (ReasonCode.CLOCK_SKEW,))
        self.save(last_tick_at=NOW)
        clock = Clock(mock.Mock(side_effect=[NOW, NOW - timedelta(seconds=301), NOW - timedelta(seconds=301)]), lambda: 0)
        self.assertEqual(self.runner(clock=clock).tick().reason_codes, (ReasonCode.CLOCK_SKEW,))
        self.discovery_factory.assert_not_called()

    def test_naive_clock_is_a_typed_local_refusal(self):
        clock = Clock(lambda: NOW.replace(tzinfo=None), lambda: 0)
        result = self.runner(clock=clock).tick()
        self.assertEqual((result.code, result.reason_codes), (2, (ReasonCode.CLOCK_SKEW,)))
        self.discovery_factory.assert_not_called()

    def test_invalid_settings_and_control_failure_are_sanitized(self):
        (self.root / 'update.conf').write_text('FUTURE_AGI_UPDATE_ENABLED=/secret/password\n')
        result = HostRunner(self.store, clock=self.clock).tick()
        self.assertEqual(result.code, 2)
        self.assertEqual(result.reason_codes, (ReasonCode.CONTROL_INVALID,))
        self.assertNotIn('password', repr(result))
        self.assertEqual(HostRunner._refusal(type('Oops', (Exception,), {'reason_code': 'bad'})()).reason_codes,
                         (ReasonCode.CONTROL_INVALID,))

    def test_pending_journal_and_active_attempt_never_recover_or_network(self):
        runner = self.runner()
        runner.engine_state.write_journal({'stage': 'prepare', 'raw': 'CANARY'})
        before = runner.engine_state.journal_path.read_bytes()
        self.assertEqual(runner.tick().disposition, 'recovery_required')
        self.assertEqual(runner.engine_state.journal_path.read_bytes(), before)
        runner.engine_state.clear_journal()
        self.store.reserve_attempt(self.target(), 'old-window', self.now)
        self.assertEqual(runner.tick().disposition, 'recovery_required')
        self.discovery_factory.assert_not_called()

    def test_failed_latch_local_exit_and_no_retry_in_new_window(self):
        target = self.target()
        attempt = self.store.reserve_attempt(target, 'previous', self.now)
        self.store.finish_attempt(replace(attempt, outcome='failed', finished_at=self.now))
        self.save(cached_candidate_identity=target, next_check_at=NOW + timedelta(hours=6))
        result = self.runner('apply').tick()
        self.assertEqual(result.reason_codes, (ReasonCode.FAILED_TARGET_LATCHED,))
        self.discovery_factory.assert_not_called()
        self.now += timedelta(hours=7)
        self.assertEqual(self.runner('apply').tick().reason_codes, (ReasonCode.FAILED_TARGET_LATCHED,))
        self.executor.assert_not_called()


class DiscoveryModeTests(RunnerCase):
    def test_notify_event_dedup_and_real_public_snapshot(self):
        self.assertEqual(self.runner().tick().disposition, 'available')
        first = self.store.read_events()
        self.assertEqual(len(first), 1)
        self.save(next_check_at=self.now)
        self.assertEqual(self.runner().tick().disposition, 'available')
        self.assertEqual(self.store.read_events(), first)
        _, public = self.publisher.capture()
        self.assertEqual(public['discovery']['candidate_version'], self.release.version)
        self.assertEqual(public['operation']['state'], 'succeeded')
        self.executor.assert_not_called()

    def test_no_upward_candidate_requires_exact_current_verification(self):
        current = replace(self.release, raw_manifest=json.dumps(default_v2('v1.51.0')).encode())
        self.discoverer.discover.return_value = DiscoveryResult(checked_at=self.now)
        self.discoverer.refresh.return_value = DiscoveryResult(current, checked_at=self.now)
        result = self.runner().tick()
        self.assertEqual(result.disposition, 'current')
        self.discoverer.refresh.assert_called_once_with('v1.51.0', channel='stable')
        self.assertEqual(self.publisher.capture()[1]['discovery']['state'], 'current')

    def test_failed_discovery_preserves_observation_timestamps_and_backoff(self):
        self.runner().tick()
        old = self.publisher.capture()[1]
        self.now += timedelta(hours=7)
        self.discoverer.discover.return_value = DiscoveryResult(reason_codes=(ReasonCode.RATE_LIMITED,),
                                                                 retry_at=(self.now + timedelta(hours=1)).timestamp())
        result = self.runner().tick()
        self.assertEqual(result.reason_codes, (ReasonCode.RATE_LIMITED,))
        new = self.publisher.capture()[1]
        self.assertEqual(new['discovery']['checked_at'], old['discovery']['checked_at'])
        self.assertEqual(new['versions']['observed_at'], old['versions']['observed_at'])
        self.assertEqual(new['discovery']['state'], 'failed')
        self.assertEqual(self.store.read_state().retry_not_before, self.now + timedelta(hours=1))
        self.assertEqual(self.installation_factory.call_count, 1)

    def test_invalid_verification_and_empty_success_do_not_refresh_status(self):
        for result in (DiscoveryResult(), DiscoveryResult(self.release, checked_at=NOW)):
            if result.candidate:
                result = replace(result, candidate=replace(self.release, verification=replace(self.release.verification, verified=False)))
            self.discoverer.discover.return_value = result
            self.save(next_check_at=None, retry_not_before=None)
            outcome = self.runner().tick()
            self.assertNotEqual(outcome.code, 0)
            self.assertIsNone(self.publisher.capture()[1]['discovery']['checked_at'])
        self.installation_factory.assert_not_called()

    def test_pause_or_disable_during_discovery_stops_docker(self):
        for action in ('pause', 'disable'):
            self.store.change_control('enable', self.now)
            self.store.change_control('resume', self.now)
            self.save(next_check_at=None, retry_not_before=None)
            def discover(**kwargs):
                self.store.change_control(action, self.now)
                return self.discovery
            self.discoverer.discover.side_effect = discover
            self.assertEqual(self.runner().tick().reason_codes, (ReasonCode.CONTROL_INVALID,))
        self.installation_factory.assert_not_called()

    def test_independent_action_refresh_does_not_delay_index_discovery(self):
        deadline = NOW + timedelta(hours=6)
        self.save(next_check_at=deadline, cached_candidate_identity=self.target())
        result = self.runner('apply').tick()
        self.assertEqual(result.disposition, 'applied')
        self.discoverer.discover.assert_not_called()
        self.discoverer.refresh.assert_called_once_with(self.release.version, channel='stable')
        self.assertEqual(self.store.read_state().next_check_at, deadline)
        self.executor.assert_called_once()

    def test_action_revalidation_refuses_changed_exact_candidate(self):
        self.save(next_check_at=NOW + timedelta(hours=6), cached_candidate_identity=self.target())
        self.discoverer.refresh.return_value = DiscoveryResult(replace(self.release, manifest_hash='e'*64), checked_at=NOW)
        result = self.runner('apply').tick()
        self.assertEqual(result.reason_codes, (ReasonCode.IMMUTABLE_RELEASE_CHANGED,))
        self.assertEqual(self.store.read_state().cached_candidate_identity, self.target())
        self.assertEqual(self.publisher.capture()[1]['discovery']['state'], 'failed')
        self.executor.assert_not_called()

    def test_local_action_due_consumed_current_and_retry_edges(self):
        target = self.target()
        cases = [dict(download_identity=target), dict(retry_not_before=NOW + timedelta(seconds=1))]
        for changes in cases:
            self.save(next_check_at=NOW + timedelta(hours=6), cached_candidate_identity=target, **changes)
            self.assertEqual(self.runner('download').tick().skip_reason, 'not_due')
        self.save(retry_not_before=None, last_window_occurrence='always:' + target.key)
        self.assertEqual(self.runner('apply').tick().skip_reason, 'not_due')
        self.save(cached_candidate_identity=replace(target, version='v1.51.0'))
        self.assertEqual(self.runner('apply').tick().skip_reason, 'not_due')
        self.discovery_factory.assert_not_called()

    def test_production_apply_is_never_automatic_but_notify_and_download_allowed(self):
        # Independent selection registration is production/distributed; install
        # provider is fake, all mutation adapters remain spies.
        from deploy.update.runner_state import RunnerStateStore, Registration
        self.env.write_text('FUTURE_AGI_BACKEND_VERSION=v1.51.0\nFUTURE_AGI_FRONTEND_VERSION=v1.51.0\n'
                            'FUTURE_AGI_VERSION=v1.51.0\n')
        store = RunnerStateStore(self.root, self.env, production=True, topology='distributed')
        binding = replace(self.binding, production=True, topology='distributed', selection_hash=store.selection_hash)
        store.register(Registration(1, binding, self.store.read_registration().launcher, NOW))
        self.store = store
        self.publisher = PublicStatusPublisher(store, self.root / 'production-public')
        with mock.patch('deploy.update.runner.selected_release', return_value='v1.51.0'):
            self.assertEqual(self.runner('apply').tick().reason_codes, (ReasonCode.INITIALIZATION_REQUIRED,))
            self.save(next_check_at=None)
            self.assertEqual(self.runner().tick().disposition, 'available')
            self.save(next_check_at=None)
            with mock.patch('deploy.update.runner.Downloader') as downloader:
                receipt = mock.Mock(identity='d'*64, completed_at=pub.stamp(NOW), images=[])
                downloader.return_value.download.return_value = receipt
                self.assertEqual(self.runner('download').tick().disposition, 'downloaded')
                downloader.return_value.download.assert_called_once()
            self.save(next_check_at=NOW + timedelta(hours=6), cached_candidate_identity=self.target())
            self.assertEqual(self.runner('apply').tick().skip_reason, 'not_due')
        self.executor.assert_not_called()

    def test_history_manual_stage_and_partial_rollout_refuse_without_recovery(self):
        for status, expected in (('staged', ReasonCode.MANUAL_STAGE_PRESENT),
                                 ('failed_during_rollout', ReasonCode.RECOVERY_REQUIRED)):
            runner = self.runner()
            with mock.patch.object(runner.engine_state, 'history', return_value=[{'kind': 'apply', 'status': status}]):
                self.save(next_check_at=None)
                self.assertEqual(runner.tick().reason_codes, (expected,))
        self.executor.assert_not_called()

    def test_observer_pending_failure_refuses_after_verified_discovery(self):
        self.observer.return_value = replace(self.observation, pending_transaction=True)
        self.assertEqual(self.runner().tick().reason_codes, (ReasonCode.RECOVERY_REQUIRED,))
        self.executor.assert_not_called()


class ApplyAndReportingTests(RunnerCase):
    def test_next_due_respects_retry_even_when_a_window_opens_earlier(self):
        retry = NOW + timedelta(days=1)
        self.save(cached_candidate_identity=self.target(), next_check_at=NOW + timedelta(hours=6),
                  retry_not_before=retry)
        result = self.runner('apply', settings=self.settings('apply', MAINTENANCE_WINDOW='23:00-23:30')).tick()
        self.assertEqual((result.skip_reason, result.next_due), ('not_due', retry))
        self.discovery_factory.assert_not_called()

    def test_window_closed_insufficient_and_just_inside(self):
        for window, reason in (('13:00-14:00', ReasonCode.OUTSIDE_WINDOW),
                               ('11:00-12:10', ReasonCode.INSUFFICIENT_WINDOW)):
            self.save(next_check_at=None)
            result = self.runner('apply', settings=self.settings('apply', MAINTENANCE_WINDOW=window)).tick()
            self.assertEqual((result.code, result.skip_reason, result.reason_codes), (0, 'outside_window', (reason,)))
        self.executor.assert_not_called()
        self.save(next_check_at=None)
        self.now += timedelta(seconds=1)
        result = self.runner('apply', settings=self.settings('apply', MAINTENANCE_WINDOW='12:00-13:00')).tick()
        self.assertEqual(result.disposition, 'applied')
        self.executor.assert_called_once()

    def test_slow_discovery_that_exhausts_start_budget_never_starts_engine(self):
        def discover(**kwargs):
            self.now += timedelta(minutes=51)
            self.monotonic += 51*60
            return self.discovery
        self.discoverer.discover.side_effect = discover
        result = self.runner('apply', settings=self.settings('apply', MAINTENANCE_WINDOW='12:00-13:00')).tick()
        self.assertEqual(result.reason_codes, (ReasonCode.INSUFFICIENT_WINDOW,))
        self.executor.assert_not_called()

    def test_apply_current_and_always_consumed_never_invoke_engine(self):
        self.preparer.return_value = replace(self.prepared, plan={'decision': 'up_to_date'})
        self.assertEqual(self.runner('apply').tick().disposition, 'current')
        self.preparer.return_value = self.prepared
        self.save(next_check_at=None, last_window_occurrence='always:' + self.target().key)
        self.assertEqual(self.runner('apply').tick().reason_codes, (ReasonCode.FAILED_TARGET_LATCHED,))
        self.executor.assert_not_called()

    def test_engine_busy_keeps_existing_public_outcome(self):
        self.publish()
        before = (self.publisher.path / 'status.json').read_bytes()
        self.executor.return_value = EngineResult(0, (), False, False, 'busy')
        self.assertEqual(self.runner('apply').tick().disposition, 'busy')
        self.assertEqual((self.publisher.path / 'status.json').read_bytes(), before)

    def test_typed_download_lock_busy_is_noop(self):
        from deploy.update.receipt import StateBusy
        with mock.patch('deploy.update.runner.Downloader') as downloader:
            downloader.return_value.download.side_effect = StateBusy('busy')
            self.assertEqual(self.runner('download').tick().disposition, 'busy')
        self.assertFalse(self.parent.exists())

    def test_all_engine_outcomes_keep_forward_failure_even_after_fallback(self):
        for code, forward, rollout, fallback, disposition in (
            (0, 'current', False, 'none', 'current'), (130, 'interrupted', False, 'none', 'interrupted'),
            (5, 'failed', True, 'succeeded', 'failed'), (5, 'failed', True, 'ineligible', 'failed'),
            (5, 'failed', True, 'failed', 'recovery_required')):
            with self.subTest(forward=forward, fallback=fallback):
                self.save(next_check_at=None)
                reasons = (ReasonCode.RECOVERY_REQUIRED,) if fallback == 'failed' else ()
                self.executor.return_value = EngineResult(code, reasons, rollout, rollout, forward, fallback)
                result = self.runner('apply').tick()
                self.assertEqual((result.code, result.disposition, result.fallback_result), (code, disposition, fallback))
                if forward == 'current':
                    self.assertIsNone(self.publisher.capture()[1]['last_run'])
                else:
                    self.assertEqual(self.publisher.capture()[1]['last_run']['fallback_outcome'], fallback)
        self.assertEqual(self.executor.call_count, 5)

    def test_reporting_and_event_failure_cannot_change_engine_success(self):
        with mock.patch.object(self.publisher, 'publish', side_effect=OSError('/secret PASSWORD=CANARY')):
            result = self.runner('apply').tick()
        self.assertEqual(result.code, 0)
        self.assertTrue(result.transaction_started)
        self.assertEqual(result.warnings, (ReasonCode.STATUS_REPORTING_UNAVAILABLE,))
        self.save(next_check_at=None)
        with mock.patch.object(self.store, 'read_events', side_effect=OSError('CANARY')):
            result = self.runner('apply').tick()
        self.assertEqual(result.disposition, 'applied')
        self.assertEqual(result.warnings, (ReasonCode.STATUS_REPORTING_UNAVAILABLE,))
        self.assertNotIn('CANARY', repr(result))

    def test_progress_after_guard_updates_closed_states_and_does_not_retry_cas(self):
        runner = self.runner('apply')
        def execute(engine, guard):
            for boundary in ('before_recovery', 'before_backup', 'after_backup', 'before_env_commit',
                             'before_rollout', 'before_fallback'):
                guard.report(boundary)
            return EngineResult(0, (), True, True, 'succeeded')
        self.executor.side_effect = execute
        self.assertEqual(runner.tick().disposition, 'applied')
        self.assertEqual(self.publisher.capture()[1]['sequence'], 6)
        self.save(next_check_at=None)
        with mock.patch.object(self.publisher, 'publish', side_effect=pub.StaleGeneration()), \
                mock.patch.object(self.publisher, 'capture', wraps=self.publisher.capture) as capture:
            self.assertEqual(self.runner('apply').tick().code, 0)
            capture.assert_called_once()

    def test_keyboard_and_service_interrupts_before_engine_are_typed(self):
        from deploy.update.apply import Interrupted
        for interruption in (KeyboardInterrupt(), Interrupted()):
            self.save(next_check_at=None, retry_not_before=None, retry_count=0)
            self.discoverer.discover.side_effect = interruption
            result = self.runner().tick()
            self.assertEqual((result.code, result.disposition), (130, 'interrupted'))
            self.assertEqual(self.publisher.capture()[1]['operation']['state'], 'interrupted')
            self.assertEqual(self.store.read_state().retry_count, 1)
        with mock.patch.object(self.store, 'runner_lock', side_effect=Interrupted()):
            self.assertEqual(self.runner().tick().code, 130)

    def test_terminal_reporting_interrupt_preserves_success(self):
        with mock.patch.object(self.publisher, 'publish', side_effect=KeyboardInterrupt()):
            result = self.runner('apply').tick()
        self.assertEqual((result.code, result.disposition), (0, 'applied'))
        self.assertEqual(result.warnings, (ReasonCode.STATUS_REPORTING_UNAVAILABLE,))

    def test_missing_facts_default_to_refusal_and_control_window_recomputed(self):
        runner = self.runner('apply')
        def execute(engine, guard):
            facts, controls = guard.facts(self.installation, 'before_backup')
            self.assertFalse(facts.baseline_attested)
            self.assertTrue(controls.window_open)
            self.assertTrue(controls.start_budget_available)
            runner.window_occurrence = 'different'
            _, controls = guard.facts(self.installation, 'after_backup')
            self.assertFalse(controls.window_open)
            facts, controls = guard.facts(self.installation, 'before_fallback')
            self.assertIsInstance(facts, Observations)
            return EngineResult(3, (ReasonCode.UNCERTIFIED_RELEASE,), False, False, 'failed')
        self.executor.side_effect = execute
        self.assertEqual(runner.tick().reason_codes, (ReasonCode.UNCERTIFIED_RELEASE,))

    def test_make_plan_adapter_uses_existing_build_plan(self):
        def prepare_adapter(release, installation, binding, make_plan, history):
            self.assertEqual(make_plan(installation, ['history']), {'planned': True})
            return self.prepared
        self.preparer.side_effect = prepare_adapter
        with mock.patch('deploy.update.runner.build_plan', return_value={'planned': True}) as plan:
            self.assertEqual(self.runner('apply').tick().code, 0)
        self.assertEqual(plan.call_args.kwargs['history'], ['history'])

    def test_downloader_receipt_and_registered_capacity_callback(self):
        receipt = mock.Mock(identity='e'*64, completed_at=pub.stamp(NOW), images=[mock.Mock(component='standalone')])
        capacity = mock.Mock(return_value=10**12)
        with mock.patch('deploy.update.runner.Downloader') as downloader:
            downloader.return_value.download.return_value = receipt
            result = self.runner('download', space_available=capacity).tick()
            self.assertEqual(result.disposition, 'downloaded')
            self.assertEqual(downloader.call_args.kwargs['space_available'](), 10**12)
            capacity.assert_called_once_with(self.installation.fresh())
        self.assertEqual(self.store.read_state().download_identity, self.target())
        self.assertEqual(self.publisher.capture()[1]['download']['state'], 'downloaded')

    def test_host_discovery_uses_host_verifier_registered_environment_and_fork(self):
        registration = self.store.read_registration()
        supervisor = mock.Mock(environment={'PATH': '/registered', 'HOME': '/private-home'})
        settings = self.settings(MANIFEST_URL='https://github.com/example/fork/releases/latest/download/future-agi-update-manifest.json')
        with mock.patch('deploy.update.runner.shutil.which', return_value='/registered/cosign') as which:
            discovery = host_discovery(self.store, settings, registration, supervisor, self.clock)
            self.assertTrue(discovery.verifier.host_only)
            self.assertIs(discovery.verifier.runner, supervisor)
            self.assertIs(discovery.verifier.probe, supervisor)
            self.assertEqual(discovery.repository, 'example/fork')
            self.assertEqual(discovery.verifier.which('cosign'), '/registered/cosign')
            which.assert_called_once_with('cosign', path=registration.launcher.path)
            self.assertEqual(discovery.cache.clock(), NOW.timestamp())
        with self.assertRaises(GuardRefused):
            host_discovery(self.store, self.settings(MANIFEST_URL='https://example.invalid/latest'), registration,
                           supervisor, self.clock)

    def test_late_discovery_cannot_replace_newer_manual_failure(self):
        self.publish()
        newer = []
        def discover(**kwargs):
            ticket, snapshot = self.publisher.capture()
            snapshot['operation'].update(state='failed', kind='apply', started_at=pub.stamp(NOW),
                                         finished_at=pub.stamp(NOW), reported_at=pub.stamp(NOW),
                                         reason_codes=['recovery_required'])
            self.publisher.publish(snapshot, expected=ticket, operation_token='new-manual-operation')
            newer.append((self.publisher.path / 'status.json').read_bytes())
            return self.discovery
        self.discoverer.discover.side_effect = discover
        result = self.runner().tick()
        self.assertEqual(result.disposition, 'available')
        self.assertEqual(result.warnings, (ReasonCode.STATUS_REPORTING_UNAVAILABLE,))
        self.assertEqual((self.publisher.path / 'status.json').read_bytes(), newer[0])

    def test_public_capture_and_post_success_observation_failure_are_nonfatal(self):
        with mock.patch.object(self.publisher, 'capture', side_effect=OSError('CANARY')):
            result = self.runner('apply').tick()
        self.assertEqual(result.disposition, 'applied')
        self.assertEqual(result.warnings, (ReasonCode.STATUS_REPORTING_UNAVAILABLE,))
        self.save(next_check_at=None)
        self.observer.side_effect = [self.observation, OSError('CANARY')]
        result = self.runner('apply').tick()
        self.assertEqual(result.disposition, 'applied')
        self.assertEqual(result.warnings, (ReasonCode.STATUS_REPORTING_UNAVAILABLE,))

    def test_config_changed_during_discovery_refuses_download_before_docker(self):
        (self.root / 'update.conf').write_text('FUTURE_AGI_UPDATE_AUTOMATION_MODE=download\n')
        def discover(**kwargs):
            (self.root / 'update.conf').write_text('FUTURE_AGI_UPDATE_AUTOMATION_MODE=manual\n')
            return self.discovery
        self.discoverer.discover.side_effect = discover
        self.assertEqual(self.runner('download').tick().reason_codes, (ReasonCode.CONFIGURATION_CHANGED,))
        self.installation_factory.assert_not_called()

    def test_no_publisher_and_invalid_registration_finish_are_bounded(self):
        self.assertEqual(self.runner(publisher=None).tick().disposition, 'available')
        self.store.change_control('disable', self.now)
        with mock.patch.object(self.store, 'read_registration', side_effect=StoreError()):
            result = self.runner().tick()
        # A corrupt registered identity cannot hide a potentially pending safety
        # transaction behind disable. It refuses before touching the daemon.
        self.assertEqual(result.disposition, 'blocked')
        self.assertEqual(result.reason_codes, (ReasonCode.CONTROL_INVALID,))
        self.assertEqual(result.warnings, (ReasonCode.STATUS_REPORTING_UNAVAILABLE,))

    def test_local_selected_release_unknown_and_gpu_variant(self):
        self.env.write_text('FUTURE_AGI_VERSION=invalid\n')
        with self.assertRaises(GuardRefused):
            selected_release(self.binding)
        self.env.write_text('FUTURE_AGI_VERSION=v1.51.0-gpu\n')
        self.assertEqual(selected_release(self.binding), 'v1.51.0')


class RunnerConcurrencyTests(RunnerCase):
    def test_control_between_tick_progress_and_terminal_preserves_failed_fallback_report(self):
        sequences = []
        def execute(engine, guard):
            guard.report('before_backup')
            sequences.append(self.publisher.capture()[0].generation)
            for action, boundary in (('pause', 'after_backup'), ('disable', 'before_env_commit'),
                                     ('enable', 'before_rollout'), ('resume', 'before_fallback')):
                before_ticket, before = self.publisher.capture()
                self.store.change_control(action, self.now)
                control_ticket = self.publisher.publish_controls(policy=self.settings('apply').automation_policy,
                                                                 now=self.now)
                _, after = self.publisher.capture()
                self.assertEqual((control_ticket.epoch, control_ticket.operation_token),
                                 (before_ticket.epoch, before_ticket.operation_token))
                for field in ('operation', 'safety', 'versions', 'discovery', 'download', 'last_run'):
                    self.assertEqual(after[field], before[field])
                sequences.append(control_ticket.generation)
                guard.report(boundary)
                sequences.append(self.publisher.capture()[0].generation)
            return EngineResult(5, (), True, True, 'failed', 'succeeded')
        self.executor.side_effect = execute
        result = self.runner('apply').tick()
        self.assertEqual((result.code, result.disposition, result.warnings), (5, 'failed', ()))
        ticket, final = self.publisher.capture()
        sequences.append(ticket.generation)
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
        self.assertEqual(final['operation']['state'], 'failed')
        self.assertEqual(final['last_run']['outcome'], 'failed_during_rollout')
        self.assertEqual(final['last_run']['fallback_outcome'], 'succeeded')

    def test_idle_ticks_cannot_fence_manual_apply_check_or_download(self):
        self.publish()
        for mode, action in (('notify', 'disable'), ('notify', 'pause'), ('manual', 'resume'), ('notify', 'resume')):
            self.store.change_control('enable', NOW)
            self.store.change_control('resume', NOW)
            self.store.change_control(action, NOW)
            self.save(next_check_at=NOW + timedelta(days=1))
            for kind in ('apply', 'check', 'download'):
                with self.subTest(mode=mode, action=action, kind=kind):
                    owner, previous = self.publisher.capture()
                    self.now += timedelta(minutes=5)
                    self.monotonic += 300
                    result = self.runner(mode).tick()
                    self.assertIsNotNone(result.skip_reason)
                    self.assertEqual(result.warnings, ())
                    mid = self.publisher.capture()[1]
                    for field in ('operation', 'safety', 'versions', 'discovery', 'download', 'last_run'):
                        self.assertEqual(mid[field], previous[field])
                    operation = dict(state='succeeded', kind=kind, stage=None, started_at=pub.stamp(NOW),
                                     finished_at=pub.stamp(self.now), reported_at=pub.stamp(self.now), reason_codes=[])
                    final = pub.build_status(self.binding, self.settings(mode).automation_policy,
                                             self.store.read_control(), self.store.read_state(), now=self.now,
                                             previous=previous, operation=operation)
                    self.publisher.publish(final, expected=owner, operation_token='manual-' + kind)
                    self.assertEqual(self.publisher.capture()[1]['operation']['kind'], kind)
        self.discovery_factory.assert_not_called()
        self.installation_factory.assert_not_called()

    def test_live_manual_journal_is_busy_with_no_state_event_or_public_change(self):
        runner = self.runner()
        self.publish()
        owner, previous = self.publisher.capture()
        before = (self.publisher.path / 'status.json').read_bytes()
        state = self.store.read_state()
        events = self.store.read_events()
        with runner.engine_state.lock():
            runner.engine_state.write_journal({'stage': 'prepare', 'kind': 'apply'})
            result = self.runner().tick()
            self.assertEqual((result.code, result.disposition), (0, 'busy'))
            self.assertEqual(self.store.read_state(), state)
            self.assertEqual(self.store.read_events(), events)
            self.assertEqual((self.publisher.path / 'status.json').read_bytes(), before)
            runner.engine_state.clear_journal()
        terminal = dict(state='succeeded', kind='apply', stage=None, started_at=pub.stamp(NOW),
                        finished_at=pub.stamp(NOW), reported_at=pub.stamp(NOW), reason_codes=[])
        self.publisher.publish(self.snapshot(previous=previous, operation=terminal), expected=owner,
                               operation_token='manual')
        self.save(next_check_at=NOW + timedelta(days=1))
        for _ in range(3):
            self.now += timedelta(minutes=5)
            self.monotonic += 300
            self.assertEqual(self.runner().tick().skip_reason, 'not_due')
            final = self.publisher.capture()[1]
            self.assertEqual(final['safety']['operator_action'], 'none')
            self.assertEqual(final['operation']['state'], 'succeeded')
        self.discovery_factory.assert_not_called()

    def test_manual_lock_acquired_after_probe_before_journal_read_is_also_busy(self):
        runner = self.runner()
        state = self.store.read_state()
        # Real contention at the read lock, after the entry probe observed free.
        with runner.engine_state.lock(), \
                mock.patch.object(runner.engine_state, 'lock_holder', return_value=None), \
                mock.patch.object(runner.engine_state, 'read_journal') as journal:
            self.assertEqual(runner.tick().disposition, 'busy')
            journal.assert_not_called()
        self.assertEqual(self.store.read_state(), state)
        self.assertFalse(self.parent.exists())

    def test_retry_state_write_between_read_and_save_is_merged_once(self):
        from deploy.update.contracts import OperationEvent
        original = self.store.save_state
        calls = []
        def race(state, *, expected_generation):
            calls.append(expected_generation)
            if len(calls) == 1:
                self.store.append_event(OperationEvent('succeeded', 'check', None, NOW, 'concurrent', ()))
            return original(state, expected_generation=expected_generation)
        with mock.patch.object(self.store, 'save_state', side_effect=race):
            result = self.runner().tick()
        self.assertEqual((result.code, result.disposition, result.warnings), (0, 'available', ()))
        self.assertEqual(calls[1], calls[0] + 1)
        self.assertEqual(self.store.read_events()[0].event.operation_token, 'concurrent')
        self.assertEqual(self.publisher.capture()[1]['safety']['operator_action'], 'none')

    def test_retry_target_race_preserves_cleared_latch_and_window(self):
        target = self.target()
        self.save(failed_target_latch=target, last_window_occurrence='always:' + target.key)
        original = self.store.save_state
        raced = []
        def retry(state, *, expected_generation):
            if not raced:
                raced.append(True)
                self.store.retry_target(target)
            return original(state, expected_generation=expected_generation)
        with mock.patch.object(self.store, 'save_state', side_effect=retry):
            self.runner()._save(last_tick_at=NOW)
        state = self.store.read_state()
        self.assertIsNone(state.failed_target_latch)
        self.assertIsNone(state.last_window_occurrence)
        self.assertEqual(state.last_tick_at, NOW)

    def test_busy_progress_publication_skips_only_that_report(self):
        runner = self.runner('apply')
        def execute(engine, guard):
            guard.report('before_backup')
            owner = self.publisher.capture()[0]
            with self.store._lock('public.lock'):
                guard.report('after_backup')
            self.assertFalse(runner.reporting_failed)
            self.assertEqual(runner.ticket, owner)
            guard.report('before_env_commit')
            self.assertEqual(self.publisher.capture()[1]['operation']['stage'], 'env_selection')
            return EngineResult(0, (), True, True, 'succeeded')
        self.executor.side_effect = execute
        result = runner.tick()
        self.assertEqual((result.code, result.disposition), (0, 'applied'))
        self.assertEqual(result.warnings, (ReasonCode.STATUS_REPORTING_UNAVAILABLE,))
        self.assertEqual(self.publisher.capture()[1]['operation']['state'], 'succeeded')
        self.assertEqual(self.publisher.capture()[1]['last_run']['outcome'], 'succeeded')

    def test_busy_initial_capture_is_noop_and_idle_heartbeat_failure_is_not_sticky(self):
        runner = self.runner()
        state = self.store.read_state()
        with self.store._lock('public.lock'):
            self.assertEqual(runner.tick().disposition, 'busy')
        self.assertFalse(runner.reporting_failed)
        self.assertEqual(self.store.read_state(), state)
        self.discovery_factory.assert_not_called()
        self.store.change_control('disable', NOW)
        with self.store._lock('public.lock'):
            result = runner.tick()
        self.assertEqual(result.skip_reason, 'disabled')
        self.assertFalse(runner.reporting_failed)
        self.assertEqual(runner.tick().warnings, ())
        self.assertEqual(self.publisher.capture()[1]['automation']['state'], 'disabled')

    def test_slow_facts_clock_timestamp_window_and_budget_are_sampled_after_provider(self):
        for elapsed, open_window, budget in ((60, True, True), (61, True, False), (961, False, False)):
            self.now, self.monotonic = NOW, 0
            self.save(next_check_at=None, last_tick_at=None)
            def facts(installation, boundary):
                self.now += timedelta(seconds=elapsed)
                self.monotonic += elapsed
                return Observations(NOW)
            runner = self.runner('apply', settings=self.settings('apply', MAINTENANCE_WINDOW='12:00-12:16'), facts=facts)
            def execute(engine, guard):
                observed, controls = guard.facts(self.installation, 'before_rollout')
                self.assertEqual(observed.now, self.now)
                self.assertEqual(controls.window_open, open_window)
                self.assertEqual(controls.start_budget_available, budget)
                return EngineResult(3, (ReasonCode.INSUFFICIENT_WINDOW,), False, False, 'failed')
            self.executor.side_effect = execute
            self.assertEqual(runner.tick().reason_codes, (ReasonCode.INSUFFICIENT_WINDOW,))


class PendingRecoveryOrderingTests(RunnerCase):
    def pending(self):
        attempt = self.store.reserve_attempt(self.target(), 'previous-window', NOW)
        with self.store._lock('state.lock') as directory:
            state = self.store.read_state()
            self.store._write(directory, 'state.json', replace(state, baseline_head='b' * 64,
                next_check_at=NOW + timedelta(days=2), retry_not_before=NOW + timedelta(days=1)))
        return attempt

    def test_pending_recovery_precedes_all_controls_modes_windows_and_current_pin(self):
        import time
        from deploy.update.contracts import RestorationResult
        from deploy.update.receipt import StateDir
        attempt = self.pending()
        self.env.write_text('FUTURE_AGI_VERSION=v99.0.0\n')  # Not the source and never a discovery input.
        life = mock.Mock()
        life.resume.return_value = RestorationResult(attempt.attempt_id, 'recovery_required', 'v1.51.0',
            self.target().version, 'previous_start_armed', reason_codes=(ReasonCode.RESTORE_CHILD_AMBIGUOUS,))
        budgets = []
        def recovery(store, settings, registration, supervisor, clock):
            self.assertIsNotNone(StateDir(store.checkout).lock_holder(automation=True))
            with self.assertRaises(StoreBusy), store.runner_lock():
                self.fail('recovery escaped runner.lock')
            budgets.append(supervisor.deadline - time.monotonic())
            self.assertGreater(budgets[-1], 0)
            self.assertLessEqual(budgets[-1], settings.automation_policy.required_recovery_budget_seconds)
            return life
        for mode in ('manual', 'notify', 'download', 'apply'):
            for action in ('disable', 'pause', 'resume'):
                with self.subTest(mode=mode, action=action):
                    self.store.change_control('enable', NOW)
                    self.store.change_control(action, NOW)
                    settings = self.settings(mode, MAINTENANCE_WINDOW='23:00-23:30')
                    result = self.runner(mode, settings=settings, recovery_factory=recovery).tick()
                    self.assertEqual(result.disposition, 'recovery_required', result)
                    self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CHILD_AMBIGUOUS,))
                    self.assertEqual(self.store.read_state().active_attempt_id, attempt.attempt_id)
                    self.assertEqual(self.store.read_state().baseline_head, 'b' * 64)
                    self.assertIsNone(self.store.read_state().failed_target_latch)
        self.assertEqual(life.resume.call_count, 12)
        self.discovery_factory.assert_not_called()
        self.preparer.assert_not_called()
        self.executor.assert_not_called()
        self.assertEqual(self.publisher.capture()[1]['operation']['phase'], 'previous_start_armed')
        self.assertIsNone(self.publisher.capture()[1]['last_run']['effective_version'])

    def test_immediate_engine_restore_preserves_forward_failure_and_projects_verified_A(self):
        from types import SimpleNamespace
        from deploy.tests.test_update_compatibility import restore_context
        _, evidence, _, _ = restore_context()
        runner = self.runner('apply')
        def execute(engine, guard):
            attempt = self.store.reserve_attempt(self.target(), 'restore-window', NOW)
            guard.attempt = attempt
            self.store.finish_attempt(replace(attempt, outcome='failed', finished_at=NOW))
            seal = replace(evidence.backup_seal, attempt_id=attempt.attempt_id,
                operation_token=attempt.operation_token, run_id=attempt.operation_token)
            runner.automatic = SimpleNamespace(latest_restore_observation=SimpleNamespace(
                facts=SimpleNamespace(runtime_observation=evidence.runtime_observation)), backup_seal=seal,
                preflight_decision=None)
            return EngineResult(5, (ReasonCode.RESTORED_AFTER_UPDATE_FAILURE,), True, True, 'failed',
                restore_result='verified', effective_version='v1.51.0', phase='restored')
        self.executor.side_effect = execute
        result = runner.tick()
        self.assertEqual(result.disposition, 'restored', result)
        self.assertEqual(result.forward_result, 'failed')
        self.assertEqual(result.restore_result, 'verified')
        self.assertEqual(result.effective_version, 'v1.51.0')
        self.assertEqual(result.fallback_result, 'none')
        public = self.publisher.capture()[1]
        self.assertEqual(public['operation']['state'], 'restored')
        self.assertEqual(public['last_run']['forward_result'], 'failed')
        self.assertEqual(public['last_run']['restore_result'], 'verified')
        self.assertEqual(public['last_run']['effective_version'], 'v1.51.0')
        self.assertEqual(public['safety']['fallback_strategy'], 'restore_backup')

    def test_malformed_forward_settings_cannot_strand_recovery_with_retained_configuration(self):
        from types import SimpleNamespace
        from deploy.update.contracts import RestorationResult
        attempt = self.pending()
        retained = self.settings('apply', QUALIFICATION_POLICY='standalone-cold-restore-v1')
        config = SimpleNamespace(policy=retained.automation_policy, as_settings=lambda: retained)
        (self.root / 'update.conf').write_text('FUTURE_AGI_UPDATE_ENABLED=not-a-boolean\n')
        life = mock.Mock()
        life.resume.return_value = RestorationResult(attempt.attempt_id, 'recovery_required', 'v1.51.0',
            self.target().version, 'previous_start_armed', reason_codes=(ReasonCode.RESTORE_CHILD_AMBIGUOUS,))
        runner = self.runner(settings=None, recovery_factory=lambda *args: life)
        with mock.patch('deploy.update.enrollment.read_setup_configuration', return_value=config):
            result = runner.tick()
        self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CHILD_AMBIGUOUS,))
        self.assertEqual(result.disposition, 'recovery_required')
        self.assertEqual(life.resume.call_args.args[2].automation_policy, config.policy)
        self.discovery_factory.assert_not_called()
        self.executor.assert_not_called()

    def test_restore_progress_uses_restore_states_and_never_claims_image_only_fallback(self):
        from deploy.update.contracts import QUALIFICATION_POLICY
        runner = self.runner('apply', settings=self.settings('apply', QUALIFICATION_POLICY=QUALIFICATION_POLICY))
        def execute(engine, guard):
            guard.report('before_restore', proof_complete=True, phase='restore_quiescing')
            public = self.publisher.capture()[1]
            self.assertEqual(public['operation']['state'], 'restoring')
            self.assertEqual(public['operation']['kind'], 'restore')
            self.assertEqual(public['operation']['phase'], 'restore_quiescing')
            self.assertEqual(public['safety']['fallback_strategy'], 'restore_backup')
            self.assertEqual(public['safety']['restore_state'], 'running')
            self.assertEqual(public['safety']['backup_state'], 'sealed')
            return EngineResult(3, (ReasonCode.RECOVERY_REQUIRED,), True, True, 'failed')
        self.executor.side_effect = execute
        self.assertEqual(runner.tick().disposition, 'recovery_required')

    def test_success_decision_metadata_error_keeps_forward_success_and_never_restores(self):
        self.pending()
        life = mock.Mock(success_decided=True, target=self.release)
        life.resume.side_effect = StoreError(ReasonCode.CONFIGURATION_CHANGED)
        result = self.runner('manual', recovery_factory=lambda *args: life).tick()
        self.assertEqual(result.disposition, 'recovery_required')
        self.assertEqual(result.forward_result, 'succeeded')
        self.assertEqual(result.reason_codes, (ReasonCode.AUTOMATIC_RECOVERY_PENDING,))
        self.assertIsNotNone(self.store.read_state().active_attempt_id)
        self.executor.assert_not_called()
        self.discovery_factory.assert_not_called()

    def test_busy_publication_cannot_cancel_safety_recovery(self):
        from deploy.update.contracts import RestorationResult
        attempt = self.pending()
        life = mock.Mock()
        life.resume.return_value = RestorationResult(attempt.attempt_id, 'recovery_required', 'v1.51.0',
            self.target().version, 'restore_quiescing', reason_codes=(ReasonCode.RECOVERY_REQUIRED,))
        runner = self.runner('manual', recovery_factory=lambda *args: life)
        with self.store._lock('public.lock'):
            result = runner.tick()
        self.assertEqual(result.disposition, 'recovery_required')
        life.resume.assert_called_once()
        self.discovery_factory.assert_not_called()
        self.assertIn(ReasonCode.STATUS_REPORTING_UNAVAILABLE, result.warnings)

    def test_A6_notify_only_projects_sanitized_estimates_without_an_attempt(self):
        from deploy.update.contracts import AutomaticUpdateDecision, AutomaticUpdateEstimate
        estimate = AutomaticUpdateEstimate(10, 100, 10, 300, 400, 500, 600, 200, 200, 400, 100, 60)
        denied = AutomaticUpdateDecision(False, (ReasonCode.MIGRATION_EXCEEDS_WINDOW,), estimate)
        runner = self.runner('apply')
        def execute(engine, guard):
            # This fixture exercises public projection only. No apply/backup
            # authority is implied by an externally constructed estimate.
            runner.automatic = mock.Mock(preflight_decision=denied)
            return EngineResult(3, denied.reason_codes, False, False, 'failed')
        self.executor.side_effect = execute
        result = runner.tick()
        self.assertEqual(result.preflight_decision, denied)
        public = self.publisher.capture()[1]
        self.assertEqual(public['safety']['estimates']['migration_seconds'], 100)
        self.assertIn('migration_exceeds_window', public['discovery']['reason_codes'])
        self.assertIsNone(self.store.read_state().active_attempt_id)


class RealLayoutTests(unittest.TestCase):
    """Real env, private state, receipts and public files; only Docker is fake.

    Synthetic signed fixtures are not a real release/host certification.
    """
    def fixture(self):
        fixture = downloads.DownloadTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        fixture.setup_download()
        manifest = json.loads(fixture.prepared.raw_manifest)
        manifest['minimum_version'] = 'v1.0.0'
        fixture.prepared = replace(prepare(manifest), plan=fixture.prepared.plan)
        from deploy.update.runner_state import LauncherConfig, Registration, RunnerStateStore
        store = RunnerStateStore(fixture.root, fixture.env_path)
        compose = fixture.root / 'docker-compose.yml'
        compose.write_text('services: {}\n')
        binding = replace(fixture.prepared.binding, checkout=fixture.root, env_alias=fixture.env_path,
                          env_target=fixture.env_path, host_uid=os.geteuid(), selection_hash=store.selection_hash,
                          compose_files=(compose,))
        store.register(Registration(1, binding, LauncherConfig(Path(sys.executable), Path.home(), os.defpath, 'test'), NOW))
        fixture.prepared = replace(fixture.prepared, binding=binding)
        return fixture, store

    def test_whole_notify_then_download_tick_preserves_running_install(self):
        fixture, store = self.fixture()
        from deploy.update.discovery import DiscoveredRelease
        release = DiscoveredRelease(fixture.prepared.raw_manifest, fixture.prepared.raw_bundle,
                                    fixture.prepared.plan['manifest_hash'], fixture.prepared.trust_fingerprint,
                                    fixture.prepared.verification)
        discovery = mock.Mock()
        discovery.discover.return_value = DiscoveryResult(release, checked_at=NOW)
        discovery.refresh.return_value = discovery.discover.return_value
        publisher = PublicStatusPublisher(store, fixture.root / 'public')
        before = fixture.env_path.stat()
        running = copy.deepcopy(fixture.inst.running)
        for mode in ('notify', 'download'):
            settings = load_settings(fixture.root, {'FUTURE_AGI_UPDATE_AUTOMATION_MODE': mode,
                                                    'FUTURE_AGI_UPDATE_ENABLED': 'true'}, environ={})
            runner = HostRunner(store, settings=settings, publisher=publisher, clock=Clock(lambda: NOW, lambda: 0),
                                discovery_factory=lambda *args: discovery,
                                installation_factory=lambda *args, **kwargs: fixture.inst,
                                space_available=lambda install: 10**12)
            with mock.patch('deploy.update.runner.prepare_update', return_value=fixture.prepared), \
                    mock.patch('deploy.update.runner.check_deployment'):
                result = runner.tick()
            self.assertEqual(result.disposition, 'available' if mode == 'notify' else 'downloaded', result)
            self.assertFalse(result.warnings)
        self.assertEqual(fixture.env_bytes(), fixture.original)
        self.assertEqual((before.st_ino, before.st_mtime_ns), (fixture.env_path.stat().st_ino, fixture.env_path.stat().st_mtime_ns))
        self.assertEqual(fixture.inst.running, running)
        self.assertEqual(fixture.hook.calls, [])
        self.assertIsNone(fixture.state.read_journal())
        self.assertEqual(fixture.state.receipts(), [])
        self.assertTrue(all(call[0] == 'pull' for call in fixture.compose_calls()))
        public = json.loads((publisher.path / 'status.json').read_bytes())
        self.assertEqual(public['download']['state'], 'downloaded')
        self.assertEqual(public['versions']['running_release'], 'v1.0.0')
        self.assertEqual(len(store.read_events()), 2)

    @classmethod
    def setUpClass(cls):
        from deploy.tests.test_update_automatic_integration import AutomaticIntegrationTests
        AutomaticIntegrationTests.setUpClass.__func__(cls)

    def run_certified(self, *, fault='', facts=None, before_facts=None, backup=None, readiness=None,
                      report=None, window='always'):
        """Real shipping v2 engine, signatures, cold archives and in-place restore.

        Only Docker/storage/runtime reads are finite synthetic models. Unlike
        the old image-fallback fixture, no affirmative v1 admission is invented.
        """
        from deploy.tests import test_update_automatic_integration as component
        prior_clock = (component.FIXTURE_WALL, component.FIXTURE_MONOTONIC, component.CLOCK_SHIFT)
        def restore_clock():
            component.FIXTURE_WALL, component.FIXTURE_MONOTONIC, component.CLOCK_SHIFT = prior_clock
        self.addCleanup(restore_clock)
        stack = component.AutomaticIntegrationTests()
        stack.artifacts = self.artifacts
        stack.setUp(window=window)
        self.addCleanup(stack.doCleanups)
        self.fixture_in_use = stack
        hooks = {}
        def locked_facts(life, boundary, value):
            from deploy.update.receipt import StateBusy, StateDir
            with self.assertRaises(StateBusy), StateDir(stack.root).lock(automation=True):
                self.fail('facts escaped update.lock')
            with self.assertRaises(StoreBusy), life.store.runner_lock():
                self.fail('facts escaped runner.lock')
            return facts(life, boundary, value) if facts else value
        hooks['facts'] = locked_facts
        if before_facts: hooks['before_facts'] = before_facts
        if backup: hooks['backup'] = backup
        if readiness: hooks['readiness'] = readiness
        def configure(runner):
            if report:
                original = runner.publisher.publish
                def publish(snapshot, **kwargs):
                    result = original(snapshot, **kwargs)
                    report(runner, snapshot)
                    return result
                runner.publisher.publish = publish
        hooks['runner'] = configure
        context = component.process_tick(stack.root, self.artifacts, fault=fault, hooks=hooks)
        return stack, context

    @staticmethod
    def shift(seconds):
        from deploy.tests import test_update_automatic_integration as component
        component.CLOCK_SHIFT += seconds

    @staticmethod
    def window():
        # Component publication advances seven hours. Leave ample admission
        # budget; individual boundary tests consume it AFTER preflight.
        from datetime import datetime, timezone
        at = datetime.now(timezone.utc) + timedelta(hours=7)
        return (at-timedelta(minutes=1)).strftime('%H:%M')+'-'+(at+timedelta(minutes=70)).strftime('%H:%M')

    def assert_restored(self, stack, context):
        result = context.result
        self.assertEqual(result['disposition'], 'restored', result)
        self.assertIn(result['forward'], ('failed','interrupted'))
        self.assertEqual(context.inst.env['FUTURE_AGI_VERSION'], 'v1.51.0')
        state = context.store.read_state()
        self.assertEqual(state.baseline_head, stack.initial)
        self.assertIsNone(state.active_attempt_id)
        self.assertEqual(state.failed_target_latch.version, 'v1.52.0')
        self.assertEqual(len(list((stack.root/'backups').glob('*/inventory.json'))),1)
        public = context.publisher.capture()[1]
        self.assertEqual(public['last_run']['forward_result'], result['forward'])
        self.assertEqual(public['last_run']['restore_result'],'verified')
        self.assertEqual(public['last_run']['effective_version'],'v1.51.0')
        self.assertEqual(public['safety']['fallback_strategy'],'restore_backup')
        self.assertEqual(public['safety']['restore_state'],'verified')
        self.assertNotIn('CANARY',(context.publisher.path/'status.json').read_text())

    def test_whole_certified_apply_uses_real_guard_engine_and_lock_order(self):
        stack, context = self.run_certified()
        self.assertEqual(context.result['disposition'],'applied',context.result)
        self.assertEqual(context.inst.env['FUTURE_AGI_VERSION'],'v1.52.0')
        self.assertIsNone(context.store.read_state().active_attempt_id)
        self.assertEqual(len(list((stack.root/'backups').glob('*/inventory.json'))),1)
        self.assertEqual(len(list((stack.root/'backups').glob('*/*.tar'))),3)
        public = context.publisher.capture()[1]
        self.assertEqual(public['last_run']['outcome'],'succeeded')
        self.assertEqual(public['safety']['backup_state'],'sealed')
        self.assertEqual(public['safety']['readiness'],'passed')
        helpers = [c for c in context.daemon.calls if c[0] == 'create']
        self.assertTrue(helpers)
        self.assertTrue(all('--pull' in c and 'never' in c and '--name' in c for c in helpers))

    def test_one_certified_restore_keeps_forward_failure_and_latches_target(self):
        stack, context = self.run_certified(fault='readiness_failed')
        self.assert_restored(stack,context)
        self.assertEqual(context.result['forward'],'failed')
        self.assertEqual(context.runner.tick().reason_codes,(ReasonCode.FAILED_TARGET_LATCHED,))

    def test_authorized_restore_finishes_after_pause_and_window_close(self):
        def close(life,ledger,daemon):
            self.shift(3*3600)
            life.store.change_control('pause',life.clock())
            from deploy.update.readiness import NotReady
            raise NotReady('CANARY /private/path')
        stack,context=self.run_certified(readiness=close,window=self.window())
        self.assert_restored(stack,context)
        self.assertEqual(context.publisher.capture()[1]['automation']['state'],'paused')

    def test_uncertain_interrupted_and_failed_restore_never_retry(self):
        from deploy.update.readiness import NotReady
        for failure in ('uncertain','interrupt','restore'):
            with self.subTest(failure=failure):
                def fail(life,ledger,daemon):
                    if failure == 'uncertain': daemon.uncertain=True
                    if failure == 'interrupt': raise KeyboardInterrupt()
                    if failure == 'restore':
                        # A replaced volume is not adopted or cleared.
                        daemon.volumes['test-backup_app-data']['CreatedAt']='foreign-object'
                    raise NotReady('CANARY /private/path')
                stack,context=self.run_certified(readiness=fail)
                if failure == 'interrupt':
                    self.assert_restored(stack,context)
                    self.assertEqual(context.result['forward'],'interrupted')
                else:
                    self.assertEqual(context.result['disposition'],'recovery_required',context.result)
                    self.assertIsNotNone(context.store.read_state().active_attempt_id)
                    self.assertIsNone(context.store.read_state().failed_target_latch)
                    self.assertFalse(any(e[:2]==['helper','clear'] for e in context.daemon.events))
                self.assertNotIn('CANARY',(context.publisher.path/'status.json').read_text())
                before=[e for e in context.daemon.events if e[0]=='up']
                context.runner.tick()
                self.assertEqual([e for e in context.daemon.events if e[0]=='up'],before)

    def test_backup_pause_disable_and_window_close_never_rollout_target(self):
        for action in ('pause','disable','window'):
            with self.subTest(action=action):
                def close(engine):
                    if action == 'window': self.shift(3*3600)
                    else: engine.automation_guard.store.change_control(action,engine.automation_guard.clock())
                stack,context=self.run_certified(backup=close,window=self.window())
                self.assert_restored(stack,context)
                self.assertFalse(any(e==['up','v1.52.0'] for e in context.daemon.events))
                self.assertIsNone(context.runner.engine_state.read_journal())
                self.assertEqual((stack.root/'.env').read_text(),'FUTURE_AGI_VERSION=v1.51.0\nSECRET=unchanged\n')

    def test_slow_progress_publication_cannot_authorize_late_backup(self):
        def slow(runner,snapshot):
            if snapshot['operation']['state']=='backing_up': self.shift(3*3600)
        stack,context=self.run_certified(report=slow,window=self.window())
        self.assertEqual(context.result['reasons'],['backup_exceeds_window'],context.result)
        self.assertFalse(list((stack.root/'backups').glob('*/inventory.json')))
        self.assertFalse(any(e[0]=='up' for e in context.daemon.events))
        self.assertEqual(context.inst.env['FUTURE_AGI_VERSION'],'v1.51.0')

    def test_expired_backup_with_new_online_verification_never_mutates_target(self):
        stack,context=self.run_certified(backup=lambda engine:self.shift(3601))
        self.assert_restored(stack,context)
        self.assertFalse(any(e==['up','v1.52.0'] for e in context.daemon.events))
        self.assertIsNone(context.runner.engine_state.read_journal())
        self.assertEqual(context.inst.env['FUTURE_AGI_VERSION'],'v1.51.0')

    def slow_final_observation(self, remaining, boundary='before_rollout'):
        from deploy.update.schedule import remaining_seconds
        counts={}
        def before(life,name):
            counts[name]=counts.get(name,0)+1
            if name==boundary and counts[name]==1:
                # Spend the earlier outage time BEFORE collecting fresh trust
                # facts; the final slow observation itself stays within trust's
                # independent freshness bound, exactly as in the old 60/61s test.
                runner_policy=load_settings(life.store.checkout,environ={}).automation_policy
                self.shift(remaining_seconds(runner_policy.maintenance_window,life.clock())-961)
        def slow(life,name,value):
            if name==boundary and counts[name]==2: self.shift(961-remaining)
            return value
        return self.run_certified(facts=slow,before_facts=before,window=self.window())

    def test_slow_final_facts_that_cross_the_start_budget_refuse_before_compose_create(self):
        stack,context=self.slow_final_observation(899)
        self.assert_restored(stack,context)
        self.assertFalse(any(e==['up','v1.52.0'] for e in context.daemon.events))
        self.assertIsNone(context.runner.engine_state.read_journal())

    def test_fast_final_facts_inside_the_start_budget_proceed(self):
        # Leave 30 seconds for real offline signature verification/fsync after
        # the slow sample; the 899-second case still pins strict refusal.
        stack,context=self.slow_final_observation(930)
        self.assertEqual(context.result['disposition'],'applied',context.result)
        self.assertEqual(sum(e==['up','v1.52.0'] for e in context.daemon.events),1)
        self.assertEqual(context.inst.env['FUTURE_AGI_VERSION'],'v1.52.0')

    def test_slow_facts_at_every_earlier_forward_boundary_refuse_without_target_mutation(self):
        for boundary in ('before_backup','after_backup','before_env_commit'):
            with self.subTest(boundary=boundary):
                stack,context=self.slow_final_observation(899,boundary)
                self.assertFalse(any(e==['up','v1.52.0'] for e in context.daemon.events))
                self.assertEqual(context.inst.env['FUTURE_AGI_VERSION'],'v1.51.0')
                if list((stack.root/'backups').glob('*/inventory.json')):
                    self.assert_restored(stack,context)
                else:
                    self.assertEqual(context.result['reasons'],['backup_exceeds_window'],context.result)
                    self.assertIsNone(context.store.read_state().active_attempt_id)

    def test_restore_whose_own_observations_outlast_the_window_still_proceeds(self):
        def outlast(life,ledger,daemon):
            self.shift(3*3600)
            raise NotReady('CANARY /private/path')
        from deploy.update.readiness import NotReady
        stack,context=self.run_certified(readiness=outlast,window=self.window())
        self.assert_restored(stack,context)

    def test_uncertified_baseline_does_not_take_backup(self):
        stack,context=self.run_certified(facts=lambda life,boundary,value:replace(value,baseline_attested=False))
        self.assertEqual(context.result['reasons'],['ownership_unconfirmed'],context.result)
        self.assertFalse(list((stack.root/'backups').glob('*/inventory.json')))
        self.assertFalse(any(e[0]=='up' for e in context.daemon.events))
        self.assertIsNone(context.store.read_state().active_attempt_id)


if __name__ == '__main__':
    unittest.main()
