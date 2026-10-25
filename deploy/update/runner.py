"""One scheduled tick. Host-only orchestration, never a second update engine.

Lock order is runner.lock -> engine update.lock -> short metadata locks. The
engine owns backup, env commit, recovery, rollout and the single live fallback.
Missing enrolled facts or registered-daemon capacity refuse, never guess.
"""
from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from .apply import Interrupted
from .compatibility import Controls, Observations, topology_profile
from .contracts import OperationEvent, ReasonCode
from .discovery import DiscoveryResult, ReleaseDiscovery
from .download import Downloader
from .envfile import read_env
from .httpcache import HttpCache
from .install import COMPONENT_VARIABLES
from .manifest import is_prerelease, is_version, parse_version
from .orchestrator import (GuardRefused, ScheduledGuard, check_deployment, execute_guarded,
                           guarded_updater, prepare_update, registered_installation, require)
from .plan import build_plan, unfinished
from .processes import ProcessSupervisor
from .public_status import build_status, stamp
from .receipt import StateBusy, StateDir
from .runner_state import Observation, StoreBusy, StoreError, TargetIdentity
from .schedule import Clock, check_due, next_open, occurrence, start_gate, validate_clock
from .settings import load_settings
from .status import observe_status
from .trust import Verification, Verifier


@dataclass(frozen=True)
class TickResult:
    code: int = 0
    disposition: str = 'skipped'
    reason_codes: Tuple[ReasonCode, ...] = ()
    skip_reason: Optional[str] = None
    target_version: Optional[str] = None
    transaction_started: bool = False
    rollout_started: bool = False
    forward_result: str = 'none'
    fallback_result: str = 'none'
    next_due: Optional[datetime] = None
    warnings: Tuple[ReasonCode, ...] = ()
    receipt: Optional[Path] = field(default=None, repr=False)  # Host CLI only, never a public projection.


def selected_release(binding):
    """Local policy input, NOT a running-version observation or apply permission."""
    env = read_env(binding.env_target)
    layout = topology_profile(binding.topology, binding.profiles)
    values = {env.get(COMPONENT_VARIABLES[name], '') for name in layout.active_components}
    values = {value[:-4] if value.endswith('-gpu') else value for value in values}
    require(len(values) == 1 and all(is_version(v) for v in values), ReasonCode.RUNNING_VERSIONS_UNKNOWN)
    return next(iter(values))


def host_discovery(store, settings, registration, supervisor, clock):
    """Default S03 wiring: no ambient PATH, env, Docker helper or auth fallback."""
    verifier = Verifier(settings, runner=supervisor, probe=supervisor,
                        which=lambda name: shutil.which(name, path=registration.launcher.path),
                        environ=supervisor.environment, host_only=True)
    cache = HttpCache(store.path / 'cache', clock=lambda: clock.sample().wall.timestamp(),
                      monotonic=clock.monotonic)
    # Never silently ignore a configured fork/source. S03 supports canonical
    # repository release discovery, not arbitrary latest endpoint semantics.
    source = settings.get('FUTURE_AGI_UPDATE_MANIFEST_URL')
    import re
    match = re.fullmatch(r'https://github.com/([^/]+/[^/]+)/releases/latest/download/future-agi-update-manifest.json', source)
    require(match is not None, ReasonCode.MANIFEST_UNAVAILABLE)
    return ReleaseDiscovery(cache, verifier, repository=match[1], monotonic=clock.monotonic)


class _ReportingGuard(ScheduledGuard):
    def __init__(self, *args, report, **kwargs):
        super().__init__(*args, **kwargs)
        self.report = report

    def validate(self, boundary, context):
        # We already hold update.lock, so a busy operation cannot be replaced.
        # Report BEFORE the final safety checks: slow status fsync must not make
        # a formerly-open window/control snapshot authorize a later mutation.
        self.report(boundary, proof_complete=self.proof is not None)
        super().validate(boundary, context)


class HostRunner:
    """S07 calls HostRunner(store, ...).tick() once and exits with result.code.

    Optional adapters are private host inputs, not app/RPC hooks:
      facts(fresh_installation, boundary) -> compatibility.Observations
      space_available(fresh_installation) -> free bytes on REGISTERED daemon
    Facts must come from an enrolled, unchanged-state baseline plus fresh
    observations. Default missing facts fail closed. No generic schema probe,
    automatic baseline attestation, package install or Docker VM SSH is invented.
    Tests inject discovery/installation factories; production uses the defaults.
    """
    def __init__(self, store, *, settings=None, publisher=None, clock=None,
                 discovery_factory=host_discovery, installation_factory=registered_installation,
                 facts=None, space_available=None, status_overlay=None):
        self.store, self.settings, self.publisher = store, settings, publisher
        self._provided_settings = settings
        self.clock = clock or Clock(lambda: datetime.now(timezone.utc), time.monotonic)
        self.discovery_factory, self.installation_factory = discovery_factory, installation_factory
        self.facts_provider, self.space_available, self.status_overlay = facts, space_available, status_overlay
        self.engine_state = StateDir(store.checkout)

    def _now(self):
        sample = self.clock.sample()
        validate_clock(sample, self.sample)
        self.sample = sample
        return sample.wall

    def _save(self, **changes):
        old = self.store.read_state()
        return self.store.save_state(replace(old, **changes), expected_generation=old.generation)

    def _config_identity(self):
        try:
            return hashlib.sha256((self.store.checkout / 'update.conf').read_bytes()).digest()
        except FileNotFoundError:
            return None

    def _controls(self):
        require(self._config_identity() == self.config_identity, ReasonCode.CONFIGURATION_CHANGED)
        control = self.store.read_control()
        require(control.generation == self.control.generation, ReasonCode.CONTROL_INVALID)
        require(control.effective(self.policy, self._now()) == 'enabled', ReasonCode.CONTROL_INVALID)

    def _facts(self, installation, boundary):
        now = self._now()
        control = self.store.read_control()
        state = self.store.read_state()
        # Never consume our own already-reserved occurrence at a later boundary.
        gate = start_gate(self.policy, now)
        window = occurrence(self.policy.maintenance_window, now)
        # Express the occurrence as a forward eligibility fact, not an exception:
        # fallback guards also call us at before_recovery/env_commit/rollout and
        # must finish an already-authorized safety action after window closure.
        facts = (Observations(now) if self.facts_provider is None
                 else self.facts_provider(installation, boundary))
        require(isinstance(facts, Observations), ReasonCode.CONTROL_INVALID)
        enabled = self.policy.enabled if control.enabled_override is None else control.enabled_override
        controls = Controls(policy=self.policy, enabled=enabled,
                            paused=control.effective(self.policy, now) == 'paused', registered=True,
                            window_open=window is not None and window == self.window_occurrence,
                            start_budget_available=gate is None,
                            trusted_identity=self.settings.get('FUTURE_AGI_UPDATE_TRUSTED_IDENTITY'),
                            trusted_issuer=self.settings.get('FUTURE_AGI_UPDATE_TRUSTED_ISSUER'))
        return replace(facts, now=now, no_failed_target=state.failed_target_latch != self.target), controls

    def _action_due(self, state, now):
        target = state.cached_candidate_identity
        if target is None or self.policy.mode == 'notify' or self.registration.binding.production and self.policy.mode == 'apply':
            return False
        if state.retry_not_before is not None and now < state.retry_not_before:
            return False
        if parse_version(target.version) <= parse_version(selected_release(self.registration.binding)):
            return False
        if self.policy.mode == 'download':
            return state.download_identity != target
        key = occurrence(self.policy.maintenance_window, now)
        consumed = 'always:' + target.key if key == 'always' else key
        return (state.failed_target_latch != target and key is not None
                and consumed != state.last_window_occurrence
                and start_gate(self.policy, now, consumed=state.last_window_occurrence) is None)

    def tick(self) -> TickResult:
        """No sleeps, no catch-up queue, no recovery/resume of an old attempt."""
        self.observation = self.discovered = self.download = self.target = None
        self.ticket = self.previous = None
        self.warnings = []
        self.token = uuid.uuid4().hex
        self.operation = self.last_run = self.safety = None
        self.reporting_failed = False
        self.registration = self.control = self.policy = None
        try:
            self.sample = self.clock.sample()
            self.started_at = self.sample.wall
            self.config_identity = self._config_identity()
            self.settings = self._provided_settings or load_settings(self.store.checkout, environ={})
            self.policy = self.settings.automation_policy
            with self.store.runner_lock():
                try:
                    result = self._tick_locked()
                except (StoreBusy, StateBusy):
                    return TickResult(disposition='busy')  # Never replace active reports.
                except (Interrupted, KeyboardInterrupt):
                    result = TickResult(130, 'interrupted', (ReasonCode.OPERATION_INTERRUPTED,))
                except Exception as exc:
                    result = self._refusal(exc)
                if result.disposition != 'busy':
                    result = self._finish(result)
                return result
        except (StoreBusy, StateBusy):
            return TickResult(disposition='busy')
        except (Interrupted, KeyboardInterrupt):
            return TickResult(130, 'interrupted', (ReasonCode.OPERATION_INTERRUPTED,))
        except Exception as exc:
            return self._refusal(exc)

    @staticmethod
    def _refusal(exc):
        try:
            reason = ReasonCode(getattr(exc, 'reason_code', ReasonCode.CONTROL_INVALID))
        except ValueError:
            reason = ReasonCode.CONTROL_INVALID
        disposition = 'recovery_required' if reason == ReasonCode.RECOVERY_REQUIRED else 'blocked'
        return TickResult(3 if isinstance(exc, GuardRefused) else 2, disposition, (reason,))

    def _tick_locked(self):
        self.control = self.store.read_control()
        effective = self.control.effective(self.policy, self._now())
        # No registration, Installation, verifier, cache, daemon or hook required
        # for these local no-ops. Registered installs still get a heartbeat.
        if effective != 'enabled':
            return TickResult(skip_reason=effective)
        self.registration = self.store.read_registration()
        self._capture_publication()  # Fence slow checks against later manual/control reports.
        state = self.store.read_state()
        validate_clock(self.sample, observed=(state.last_tick_at, state.last_attempt_at,
                                              state.last_successful_check_at,
                                              *(o.observed_at for o in state.last_good_observations)))
        state = self._save(last_tick_at=self._now())
        if state.active_attempt_id is not None or self.engine_state.read_journal() is not None:
            return TickResult(3, 'recovery_required', (ReasonCode.RECOVERY_REQUIRED,))
        due = check_due(self._now(), state.next_check_at, state.retry_not_before)
        action_due = self._action_due(state, self._now())
        if not due and not action_due:
            if state.failed_target_latch is not None and self.policy.mode == 'apply':
                return TickResult(3, 'blocked', (ReasonCode.FAILED_TARGET_LATCHED,))
            return TickResult(skip_reason='not_due', next_due=state.retry_not_before or state.next_check_at)
        launcher = self.registration.launcher
        supervisor = ProcessSupervisor(self.policy, docker_context=self.registration.binding.docker_context,
                                       environment={'HOME': str(launcher.home), 'PATH': launcher.path,
                                                    'USER': launcher.user, 'LOGNAME': launcher.user})
        discovery = self.discovery_factory(self.store, self.settings, self.registration, supervisor, self.clock)
        current = selected_release(self.registration.binding)
        self.store.record_check_attempt(self.policy, self._now())
        try:
            if due:
                self.discovered = discovery.discover(current=current, channel=self.policy.channel, policy=self.policy.version_policy)
                if self.discovered.complete and self.discovered.candidate is None:
                    # S03's no-upgrade result has no signed candidate. The public
                    # schema requires a verified current version: verify that pair
                    # rather than manufacture evidence from selected .env pins.
                    self.discovered = discovery.refresh(current, channel='beta' if is_prerelease(current) else 'stable')
            else:
                self.discovered = discovery.refresh(state.cached_candidate_identity.version, channel=self.policy.channel)
            now = self._now()
            require(self.discovered.complete and self.discovered.candidate is not None,
                    self.discovered.reason_codes[0] if self.discovered.reason_codes else ReasonCode.MANIFEST_UNAVAILABLE)
            release = self.discovered.candidate
            require(release.verification.verified, ReasonCode.SIGNATURE_REJECTED)
            validate_clock(self.sample, observed=(self.discovered.checked_at, release.verification.checked_at))
            self.target = TargetIdentity(release.version, release.manifest_hash)
            if not due:
                require(self.target == state.cached_candidate_identity, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
            if due:
                self.store.record_check_success(self.policy, now, self.target)
            else:
                self._save(last_successful_check_at=now, retry_count=0, retry_not_before=None,
                           cached_candidate_identity=self.target)
        except Exception as exc:
            retry = getattr(self.discovered, 'retry_at', None)
            self.discovered = DiscoveryResult(reason_codes=self._refusal(exc).reason_codes, retry_at=retry)
            self.store.record_check_failure(self.policy, self._now(),
                datetime.fromtimestamp(retry, timezone.utc) if retry is not None else None)
            raise
        self._controls()
        installation = self.installation_factory(self.registration.binding, supervisor, status_overlay=self.status_overlay)
        self.observation = observe_status(installation, self.engine_state, now=self._now())
        require(not self.observation.pending_transaction, ReasonCode.RECOVERY_REQUIRED)
        history = self.engine_state.history(installation.identity())
        latest = history[-1] if history else None
        require(not latest or latest.get('status') != 'staged', ReasonCode.MANUAL_STAGE_PRESENT)
        require(not unfinished(history), ReasonCode.RECOVERY_REQUIRED)
        if self.policy.mode == 'notify':
            return TickResult(disposition='available' if parse_version(release.version) > parse_version(current) else 'current',
                              target_version=release.version)
        check_deployment(installation, release.manifest)  # No plan's git drift hints before this refusal.
        verification = Verification(True, release.verification.method, release.verification.signer,
                                    release.verification.issuer, release.verification.tool)

        def make_plan(fresh, history):
            return build_plan(release.manifest, verification, fresh, self.settings, history=history)

        prepared = prepare_update(release, installation, self.registration.binding, make_plan, history)
        if self.policy.mode == 'download':
            self._controls()
            capacity = None if self.space_available is None else lambda: self.space_available(installation.fresh())
            self.download = Downloader(installation, self.engine_state, space_available=capacity, clock=self._now).download(prepared)
            self._save(download_identity=self.target)
            return TickResult(disposition='downloaded', target_version=release.version)
        require(not self.registration.binding.production, ReasonCode.INITIALIZATION_REQUIRED)
        if prepared.plan.get('decision') == 'up_to_date':
            return TickResult(disposition='current', target_version=release.version)
        state = self.store.read_state()
        require(state.failed_target_latch != self.target, ReasonCode.FAILED_TARGET_LATCHED)
        reason = start_gate(self.policy, self._now(), consumed=state.last_window_occurrence)
        if reason is not None:
            return TickResult(reason_codes=(reason,), skip_reason='outside_window', target_version=release.version)
        self.window_occurrence = occurrence(self.policy.maintenance_window, self._now())
        if self.window_occurrence == 'always':
            require(state.last_window_occurrence != 'always:' + self.target.key, ReasonCode.FAILED_TARGET_LATCHED)
        guard = _ReportingGuard(prepared, discovery=discovery, store=self.store, facts=self._facts,
                                make_plan=make_plan, control_generation=self.control.generation,
                                window_occurrence=self.window_occurrence, clock=self._now, report=self._progress)
        try:
            engine = guarded_updater(installation, self.settings, self.engine_state, guard, supervisor)
            result = execute_guarded(engine, guard)  # Exactly one forward engine invocation; owns fallback/latch.
        finally:
            guard.close()
        if result.forward_result == 'busy':
            return TickResult(disposition='busy')
        if result.forward_result == 'current':
            # No transaction occurred; do not fabricate a last apply receipt or
            # replace a previous failure with an automatic-apply success.
            return TickResult(disposition='current', target_version=release.version)
        disposition = {'succeeded': 'applied', 'interrupted': 'interrupted'}.get(result.forward_result, 'failed')
        if ReasonCode.RECOVERY_REQUIRED in result.reason_codes:
            disposition = 'recovery_required'
        # No post-success reporting or reinspection failure can change this result.
        try:
            self.observation = observe_status(installation, self.engine_state, now=self._now())
        except (Exception, Interrupted, KeyboardInterrupt):
            self.warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)
        self.safety = {'backup_required': True,
                       'backup_state': 'reported_complete' if guard.proof is not None else 'not_evaluated',
                       'readiness': 'passed' if result.code == 0 and result.forward_result == 'succeeded'
                       or result.fallback_result == 'succeeded' else 'failed' if result.rollout_started else 'unknown',
                       'automatic_rollback': 'ineligible' if self.policy.automatic_rollback else 'disabled',
                       'operator_action': 'none' if result.code == 0 else 'review_recovery'
                       if result.transaction_started or ReasonCode.RECOVERY_REQUIRED in result.reason_codes else 'inspect_host'}
        outcome = ('succeeded' if result.code == 0 else
                   ('interrupted_' if result.forward_result == 'interrupted' else 'failed_') +
                   ('during_rollout' if result.rollout_started else 'before_rollout'))
        self.last_run = {'kind': 'apply', 'automatic': True, 'outcome': outcome, 'from_version': current,
                         'target_version': release.version, 'finished_at': stamp(self.sample.wall),
                         'fallback_outcome': result.fallback_result}
        return TickResult(result.code, disposition, result.reason_codes, target_version=release.version,
                          transaction_started=result.transaction_started, rollout_started=result.rollout_started,
                          forward_result=result.forward_result, fallback_result=result.fallback_result, receipt=result.receipt)

    def _progress(self, boundary, *, proof_complete=False):
        states = {'before_backup': ('backing_up', 'backup'), 'after_backup': ('verifying', 'image_verification'),
                  'before_env_commit': ('applying', 'env_selection'), 'before_rollout': ('applying', 'rollout'),
                  'before_fallback': ('rolling_back', 'recovery')}
        if boundary in states:
            state, stage = states[boundary]
            self.operation = {'state': state, 'kind': 'rollback' if state == 'rolling_back' else 'apply',
                              'stage': stage, 'started_at': stamp(self.started_at), 'finished_at': None,
                              'reported_at': stamp(self.sample.wall), 'reason_codes': []}
            self.safety = {'backup_required': True, 'backup_state': 'reported_complete' if proof_complete else 'required',
                           'readiness': 'pending',
                           'automatic_rollback': 'ineligible' if self.policy.automatic_rollback else 'disabled',
                           'operator_action': 'none'}
            self._publish(TickResult())

    def _capture_publication(self):
        if self.publisher is not None and self.ticket is None and not self.reporting_failed:
            try:
                self.ticket, self.previous = self.publisher.capture()
            except Exception:
                self.reporting_failed = True
                self.warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)

    def _publish(self, result):
        self._capture_publication()
        if self.publisher is None or self.reporting_failed:
            return
        try:
            registration = self.registration or self.store.read_registration()
            state = self.store.read_state()
            control = self.store.read_control()
            snapshot = build_status(registration.binding, self.policy, control, state, now=self.sample.wall,
                                    previous=self.previous, observation=self.observation, discovery=self.discovered,
                                    download=self.download, operation=self.operation, last_run=self.last_run,
                                    reason_codes=result.reason_codes, safety=self.safety)
            self.ticket = self.publisher.publish(snapshot, expected=self.ticket, operation_token=self.token)
            snapshot['sequence'] = self.ticket.generation
            self.previous = snapshot
        except Exception:
            # In particular, do NOT reload/retry on CAS conflict: a newer manual
            # operation/control report wins, even if our tick subsequently ends.
            self.reporting_failed = True
            self.warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)

    def _finish(self, result):
        try:
            if self.registration is None:
                try:
                    self.registration = self.store.read_registration()
                except StoreError as exc:
                    if exc.reason_code == ReasonCode.SCHEDULER_UNREGISTERED and result.skip_reason is not None:
                        return result
                    raise
            now = self._now()
            state = self.store.read_state()
            self._save(last_tick_at=now)
            if result.skip_reason is None:
                state_name = {'available': 'succeeded', 'current': 'succeeded', 'downloaded': 'succeeded',
                              'applied': 'succeeded', 'interrupted': 'interrupted', 'failed': 'failed',
                              'recovery_required': 'blocked'}.get(result.disposition, 'blocked')
                kind = 'apply' if result.forward_result != 'none' else 'download' if self.policy.mode == 'download' else 'check'
                self.operation = {'state': state_name, 'kind': kind, 'stage': None,
                                  'started_at': stamp(self.started_at), 'finished_at': stamp(now),
                                  'reported_at': stamp(now), 'reason_codes': [r.value for r in result.reason_codes]}
                event = OperationEvent(state_name, kind, None, now, self.token, result.reason_codes)
                events = self.store.read_events()
                if not events or (events[-1].target, events[-1].event.state, events[-1].event.kind,
                                  events[-1].event.reason_codes) != (self.target, state_name, kind, result.reason_codes):
                    self.store.append_event(event, self.target)
            observations = {o.kind: o for o in state.last_good_observations}
            if self.discovered is not None and self.discovered.complete and self.target is not None:
                observations['discovery'] = Observation('discovery', self.discovered.checked_at, self.target.manifest_hash)
            if self.download is not None:
                observations['download'] = Observation('download', now, self.download.identity)
            self._save(last_good_observations=tuple(observations.values()))
            state = self.store.read_state()
            next_due = state.retry_not_before or state.next_check_at
            if (self.policy.mode == 'apply' and not self.registration.binding.production
                    and state.cached_candidate_identity is not None
                    and state.cached_candidate_identity != state.failed_target_latch):
                earliest = max(now, state.retry_not_before) if state.retry_not_before is not None else now
                opening = next_open(self.policy.maintenance_window, earliest)
                if opening is not None and opening > now:
                    next_due = min(opening, next_due) if next_due is not None else opening
            current_control = self.store.read_control()
            effective = current_control.effective(self.policy, now)
            if effective != 'enabled':
                next_due = current_control.pause_until if effective == 'paused' else None
            result = replace(result, next_due=next_due)
        except (Exception, Interrupted, KeyboardInterrupt):
            self.warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)
        try:
            self._publish(result)
        except (Interrupted, KeyboardInterrupt):
            # A terminal engine outcome is already durable. An interruption of
            # reporting cannot turn success into failure (nor retry a failure).
            self.warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)
        return replace(result, warnings=tuple(dict.fromkeys(self.warnings)))
