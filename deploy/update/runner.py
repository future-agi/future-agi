"""One scheduled tick. Host-only orchestration, never a second update engine.

Lock order is runner.lock -> engine update.lock -> short metadata locks. The
engine owns backup, env commit, rollout and mandatory cold-snapshot restoration.
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
from .contracts import AutomaticUpdateDecision, OperationEvent, ReasonCode, RestorationResult
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
from .public_status import build_status, restoration_projection, retry_busy, stamp
from .receipt import StateBusy, StateDir
from .runner_state import Observation, StaleGeneration, StoreBusy, StoreError, TargetIdentity
from .schedule import Clock, check_due, next_open, occurrence, start_gate, validate_clock
from .settings import SettingsError, load_settings
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
    preflight_decision: Optional[AutomaticUpdateDecision] = field(default=None, repr=False)
    restore_result: str = 'not_needed'
    effective_version: Optional[str] = None


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
                 facts=None, space_available=None, status_overlay=None, automatic_factory=None,
                 recovery_factory=None):
        self.store, self.settings, self.publisher = store, settings, publisher
        self._provided_settings = settings
        self.clock = clock or Clock(lambda: datetime.now(timezone.utc), time.monotonic)
        self.discovery_factory, self.installation_factory = discovery_factory, installation_factory
        self.facts_provider, self.space_available, self.status_overlay = facts, space_available, status_overlay
        self.engine_state = StateDir(store.checkout)
        self.automatic_factory = automatic_factory
        self.recovery_factory = recovery_factory

    def _supervisor(self):
        launcher = self.registration.launcher
        return ProcessSupervisor(self.policy, docker_context=self.registration.binding.docker_context,
            environment={'HOME': str(launcher.home), 'PATH': launcher.path,
                         'USER': launcher.user, 'LOGNAME': launcher.user})

    def _automatic(self, discovery):
        from .automatic import AutomaticLifecycle
        return (self.automatic_factory or AutomaticLifecycle)(self.store, discovery, self.registration, self._now)

    def _recovery_lifecycle(self, supervisor):
        """Retained trust only: no discovery object, endpoint refresh or image pull."""
        if self.recovery_factory is not None:
            return self.recovery_factory(self.store, self.settings, self.registration, supervisor, self.clock)
        from types import SimpleNamespace
        from .enrollment import read_setup_configuration
        config = read_setup_configuration(self.store)
        settings = config.as_settings() if config is not None else self.settings
        verifier = Verifier(settings, runner=supervisor, probe=supervisor,
            which=lambda name: shutil.which(name, path=self.registration.launcher.path),
            environ=supervisor.environment, host_only=True)
        return self._automatic(SimpleNamespace(verifier=verifier))

    def _recover_pending(self, state):
        if state.baseline_head is None:
            return TickResult(3, 'recovery_required', (ReasonCode.RECOVERY_REQUIRED,))
        from .enrollment import read_setup_configuration
        config = read_setup_configuration(self.store)
        recovery_policy = config.policy if config is not None else self.policy
        recovery_settings = config.as_settings() if config is not None else self.settings
        supervisor = self._supervisor()
        supervisor.policy = recovery_policy
        # A NEW supervisor is a fresh RECOVERY-ONLY budget, not evidence that a
        # prior remote mutator ended. The ledger/engine must reconcile ambiguity.
        supervisor.deadline = time.monotonic() + recovery_policy.required_recovery_budget_seconds
        with self.engine_state.lock(automation=True):
            self.automatic = self._recovery_lifecycle(supervisor)
            def installation():
                return self.installation_factory(self.registration.binding, supervisor,
                                                 status_overlay=self.status_overlay)
            try:
                # The lifecycle authenticates retained direction first. A durable
                # accepted success needs only receipt/head/ledger replay, even
                # when Docker is unavailable; restoration still resolves and
                # checks the live registered installation before any mutation.
                result = self.automatic.resume(installation, self.engine_state, recovery_settings,
                                               supervisor=supervisor)
            except (Exception, Interrupted, KeyboardInterrupt):
                if getattr(self.automatic, 'success_decided', False) is True:
                    # This is metadata/availability reconciliation, NOT forward
                    # failure and never permission to restore the accepted target.
                    return TickResult(3, 'recovery_required', (ReasonCode.AUTOMATIC_RECOVERY_PENDING,),
                        target_version=self.automatic.target.version, forward_result='succeeded')
                raise
        if isinstance(result, RestorationResult):
            self.target = self.store.read_attempt(result.attempt_id).target
            require(result.outcome == 'recovery_required' or self.store.read_state().active_attempt_id is None,
                    ReasonCode.RECOVERY_REQUIRED)
            projection = restoration_projection(result, now=self._now(), started_at=self.started_at,
                                                forward_result='interrupted')
            self.operation, self.last_run, self.safety = (projection[k] for k in ('operation', 'last_run', 'safety'))
            self.restoration = result
            if result.outcome == 'recovery_required':
                return TickResult(3, 'recovery_required', result.reason_codes or (ReasonCode.RECOVERY_REQUIRED,),
                                  target_version=result.target_version, forward_result='interrupted')
            return TickResult(5, result.outcome, result.reason_codes, target_version=result.target_version,
                              transaction_started=True, forward_result='interrupted',
                              restore_result='verified' if result.outcome == 'restored' else 'not_needed',
                              effective_version=result.source_version)
        # Exact success metadata reconciliation returns the retained receipt.
        require(type(result) is tuple and len(result) == 2, ReasonCode.RECOVERY_REQUIRED)
        receipt, version = result
        require(self.store.read_state().active_attempt_id is None and isinstance(receipt, Path)
                and type(version) is str and is_version(version), ReasonCode.RECOVERY_REQUIRED)
        try:
            self._project_recovered_success(receipt, version)
        except (Exception, Interrupted, KeyboardInterrupt):
            # Receipt/head completion is already durable; reporting cannot
            # turn it into a failed forward attempt or authorize restoration.
            self.warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)
        return TickResult(disposition='applied', target_version=version, transaction_started=True,
                          rollout_started=True, forward_result='succeeded', receipt=receipt,
                          effective_version=version)

    def _project_recovered_success(self, receipt, version):
        """Project the exact committed transaction, not stale pre-recovery status.

        Metadata replay never probes B again or claims a fresh availability
        observation. Its as-of timestamp is the retained successful receipt.
        """
        from .baseline import BaselineStore, SuccessSeal, identity, sha
        from .compatibility import decode_document
        from .status import StatusObservation
        baseline = BaselineStore(self.store)
        head = baseline.current()
        intent = baseline.intent(head.attempt_id)
        seal = baseline._record('transitions', head.attempt_id + '-success.json', SuccessSeal)
        require(head.release.version == version and seal.successor_head == head.identity
                and seal.intent_sha256 == identity(intent) and seal.receipt_name == receipt.name
                and self.store.read_attempt(head.attempt_id) == seal.terminal, ReasonCode.RECOVERY_REQUIRED)
        with self.engine_state.private_directory() as directory:
            raw = self.store._read_raw(directory, receipt.name, 256 * 1024)
        require(sha(raw) == seal.terminal.receipt_identity, ReasonCode.RECOVERY_REQUIRED)
        record = decode_document(raw)
        require(record.get('status') == 'succeeded' and record.get('kind') == 'apply'
                and record.get('run_id') == intent.run_id and record.get('target_version') == version,
                ReasonCode.RECOVERY_REQUIRED)
        self.target = intent.attempt.target
        self.last_run = dict(kind='apply', automatic=True, outcome='succeeded',
            from_version=baseline._head(intent.old_head).release.version, target_version=version,
            finished_at=record['finished_at'], fallback_outcome='none', forward_result='succeeded',
            restore_result='not_needed', effective_version=version, failed_target_version=None)
        self.observation = StatusObservation(dict(observed_at=record['finished_at'], runtime_state='consistent',
            selected_release=version, running_release=version, components=[dict(component='standalone',
            selected_version=version, observed_versions=[version], observation='known')]), False, self.last_run, ())
        self.safety = dict(backup_required=True, backup_state='sealed', readiness='passed',
            automatic_rollback='ineligible', operator_action='none', fallback_strategy='restore_backup',
            restore_state='not_needed', guidance_id='none')
        self.operation = dict(state='succeeded', kind='apply', stage=None, phase='succeeded',
            started_at=stamp(intent.attempt.reserved_at), finished_at=record['finished_at'],
            reported_at=stamp(self._now()), reason_codes=[])

    def _pending_success(self, state):
        """Head CAS may have cleared active intent before ledger finalization.

        Inspect retained local authority under update.lock, ahead of controls;
        a committed head never becomes permission to restore or start B again.
        """
        if state.baseline_head is None:
            return False
        from .automatic import BackupConfiguration, pending_restore_attempt
        if pending_restore_attempt(self.store) is not None:
            return True
        from .baseline import BaselineStore, SuccessSeal
        from .backup_proof import BackupExpectation
        from .contracts import installation_binding_hash
        from .recovery import RecoveryLedger
        baseline = BaselineStore(self.store)
        head = baseline.current()
        if head.attempt_id is None:
            return False
        seal = baseline._record('transitions', head.attempt_id+'-success.json', SuccessSeal)
        require(seal.successor_head == head.identity, ReasonCode.RECOVERY_REQUIRED)
        retained = baseline.automatic_attempt(head.attempt_id)
        old = baseline._head(retained.old_head)
        raw = baseline.backup_state(old)
        if raw is None:
            return False  # Core-only metadata tests have no automatic backend.
        config = BackupConfiguration.load(raw)
        attempt = retained.attempt
        expected = BackupExpectation(attempt.attempt_id, old.installation_fingerprint,
            attempt.target.manifest_hash, old.release.version, attempt.reserved_at,
            topology_profile(attempt.binding.topology, attempt.binding.profiles).backup_domains)
        ledger = RecoveryLedger.open(config.destination/attempt.attempt_id, expected=expected,
            operation_token=attempt.operation_token, run_id=retained.run_id,
            binding_sha256=installation_binding_hash(attempt.binding))
        require(ledger.direction == 'success_decided', ReasonCode.RECOVERY_REQUIRED)
        return ledger.phase != 'succeeded'

    def _capacity(self, installation):
        if self.space_available is not None:
            return self.space_available(installation)
        if self.automatic is not None:
            from .docker_host import observe_daemon_storage
            return observe_daemon_storage(installation, self.registration)
        return None

    def _now(self):
        sample = self.clock.sample()
        validate_clock(sample, self.sample)
        self.sample = sample
        return sample.wall

    def _save(self, **changes):
        old = self.store.read_state()
        try:
            return self.store.save_state(replace(old, **changes), expected_generation=old.generation)
        except StaleGeneration:
            # runner.lock owns these fields; merge once over a concurrent retry
            # or event append, preserving every unrelated field and counter.
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
        # The provider is slow (verifier, several daemon reads): it runs FIRST, and the clock, the controls and
        # every window/start-budget fact below are sampled only after it returns. A boundary then authorizes
        # against a time no earlier than its own observations, so a slow final observation can consume the
        # required start budget (it refuses) instead of leaving a stale "budget available" fact behind.
        provider = self.automatic.facts if getattr(self, 'automatic', None) is not None else self.facts_provider
        facts = None if provider is None else provider(installation, boundary)
        now = self._now()
        control = self.store.read_control()
        state = self.store.read_state()
        # Never consume our own already-reserved occurrence at a later boundary.
        gate = start_gate(self.policy, now)
        window = occurrence(self.policy.maintenance_window, now)
        # Express the occurrence as a forward eligibility fact, not an exception:
        # fallback guards also call us at before_recovery/env_commit/rollout and
        # must finish an already-authorized safety action after window closure.
        facts = Observations(now) if facts is None else facts
        require(isinstance(facts, Observations), ReasonCode.CONTROL_INVALID)
        enabled = self.policy.enabled if control.enabled_override is None else control.enabled_override
        controls = Controls(policy=self.policy, enabled=enabled,
                            paused=control.effective(self.policy, now) == 'paused', registered=True,
                            window_open=window is not None and window == self.window_occurrence,
                            start_budget_available=gate is None,
                            trusted_identity=self.settings.get('FUTURE_AGI_UPDATE_TRUSTED_IDENTITY'),
                            trusted_issuer=self.settings.get('FUTURE_AGI_UPDATE_TRUSTED_ISSUER'),
                            public_key_sha256=getattr(self, 'public_key_sha256', ''),
                            repository=getattr(self, 'repository', 'future-agi/future-agi'))
        return replace(facts, now=now, no_failed_target=state.failed_target_latch != self.target), controls

    def _current_release(self):
        selected = selected_release(self.registration.binding)
        if self.store.read_state().baseline_head is not None:
            from .baseline import BaselineStore
            version = BaselineStore(self.store).current().release.version
            require(selected == version, ReasonCode.CONFIGURATION_CHANGED)
            return version
        return selected

    def _action_due(self, state, now):
        target = state.cached_candidate_identity
        if target is None or self.policy.mode == 'notify' or self.registration.binding.production and self.policy.mode == 'apply':
            return False
        if state.retry_not_before is not None and now < state.retry_not_before:
            return False
        if self.policy.mode == 'apply' and state.failed_target_latch == target:
            return False
        if parse_version(target.version) <= parse_version(self._current_release()):
            return False
        if self.policy.mode == 'download':
            return state.download_identity != target
        key = occurrence(self.policy.maintenance_window, now)
        consumed = 'always:' + target.key if key == 'always' else key
        return (state.failed_target_latch != target and key is not None
                and consumed != state.last_window_occurrence
                and start_gate(self.policy, now, consumed=state.last_window_occurrence) is None)

    def tick(self) -> TickResult:
        """One attempt or exact success-metadata finalization; never replay a rollout."""
        self.observation = self.discovered = self.download = self.target = None
        self.ticket = self.previous = None
        self.warnings = []
        self.token = uuid.uuid4().hex
        self.operation = self.last_run = self.safety = self.restoration = None
        self.reporting_failed = False
        self.registration = self.control = self.policy = self.automatic = None
        self.public_key_sha256, self.repository = '', 'future-agi/future-agi'
        try:
            self.sample = self.clock.sample()
            self.started_at = self.sample.wall
            self.config_identity = self._config_identity()
            with self.store.runner_lock():
                try:
                    try:
                        self.settings = self._provided_settings or load_settings(self.store.checkout, environ={})
                        self.policy = self.settings.automation_policy
                    except SettingsError as invalid:
                        # A malformed NEW forward configuration cannot strand a
                        # cold transaction whose original policy/trust is retained.
                        from .enrollment import read_setup_configuration
                        state = self.store.read_state()
                        with self.engine_state.lock(automation=True):
                            pending = (state.active_attempt_id is not None
                                or self.engine_state.read_journal() is not None or self._pending_success(state))
                        if state.baseline_head is None or not pending:
                            raise invalid
                        config = read_setup_configuration(self.store)
                        require(config is not None, ReasonCode.AUTOMATIC_RECOVERY_PENDING)
                        self.settings, self.policy = config.as_settings(), config.policy
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
            reason = ReasonCode(getattr(exc, 'reason_code', getattr(exc, 'code',
                ReasonCode.RECOVERY_REQUIRED if getattr(exc, 'recovery_required', False) else ReasonCode.CONTROL_INVALID)))
        except ValueError:
            reason = ReasonCode.RECOVERY_REQUIRED if getattr(exc, 'recovery_required', False) else ReasonCode.CONTROL_INVALID
        disposition = 'recovery_required' if reason in (ReasonCode.RECOVERY_REQUIRED, ReasonCode.AUTOMATIC_RECOVERY_PENDING) \
            or getattr(exc, 'recovery_required', False) else 'blocked'
        return TickResult(3 if isinstance(exc, GuardRefused) else 2, disposition, (reason,))

    def _tick_locked(self):
        # A manual command owns update.lock, not runner.lock. Its live journal
        # is progress, not crash evidence; do not save state or publish over it.
        if self.engine_state.lock_holder(automation=True) is not None:
            return TickResult(disposition='busy')
        self.control = self.store.read_control()
        effective = self.control.effective(self.policy, self._now())
        # Registered safety recovery precedes EVERY forward gate, including
        # disabled/paused/manual. An unregistered default remains a local no-op.
        try:
            self.registration = self.store.read_registration()
        except StoreError as exc:
            if effective != 'enabled' and exc.reason_code == ReasonCode.SCHEDULER_UNREGISTERED:
                return TickResult(skip_reason=effective)
            raise
        state = self.store.read_state()
        validate_clock(self.sample, observed=(state.last_tick_at, state.last_attempt_at,
                                              state.last_successful_check_at,
                                              *(o.observed_at for o in state.last_good_observations)))
        # A probe alone has a journal-creation race. Take the nonblocking engine
        # lock for this local read, then release it BEFORE discovery/network I/O.
        with self.engine_state.lock(automation=True):
            retained_state = self.store.read_state()
            pending = (retained_state.active_attempt_id is not None or self.engine_state.read_journal() is not None
                       or self._pending_success(retained_state))
        if pending:
            # Reporting cannot postpone required recovery. No public-lock CAS is
            # held across engine work; capture failure remains only a warning.
            self._capture_publication()
            return self._recover_pending(state)
        if effective != 'enabled':
            return TickResult(skip_reason=effective)
        self._capture_publication()  # Fence slow forward checks against later reports.
        if self.publisher is not None and self.ticket is None and not self.reporting_failed:
            return TickResult(disposition='busy')
        state = self._save(last_tick_at=self._now())
        due = check_due(self._now(), state.next_check_at, state.retry_not_before)
        action_due = self._action_due(state, self._now())
        if not due and not action_due:
            if state.failed_target_latch is not None and self.policy.mode == 'apply':
                return TickResult(3, 'blocked', (ReasonCode.FAILED_TARGET_LATCHED,))
            return TickResult(skip_reason='not_due', next_due=state.retry_not_before or state.next_check_at)
        supervisor = self._supervisor()
        discovery = self.discovery_factory(self.store, self.settings, self.registration, supervisor, self.clock)
        current = self._current_release()
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
        # A manual command may have started during discovery. A failed lock
        # acquisition returns busy without events/publication; completed discovery
        # accounting stays recorded. Hold the lock through the consistent journal
        # and history observation so a live journal cannot appear between reads.
        with self.engine_state.lock(automation=True):
            self.observation = observe_status(installation, self.engine_state, now=self._now())
            require(not self.observation.pending_transaction, ReasonCode.RECOVERY_REQUIRED)
            history = self.engine_state.history(installation.identity())
            latest = history[-1] if history else None
            require(not latest or latest.get('status') != 'staged', ReasonCode.MANUAL_STAGE_PRESENT)
            require(not unfinished(history), ReasonCode.RECOVERY_REQUIRED)
        if self.policy.mode == 'notify':
            return TickResult(disposition='available' if parse_version(release.version) > parse_version(current) else 'current',
                              target_version=release.version)
        authorization = None
        if state.baseline_head is not None:
            require(not self.registration.binding.production, ReasonCode.INITIALIZATION_REQUIRED)
            with self.engine_state.lock(automation=True):
                require(self.engine_state.read_journal() is None and self.store.read_state().active_attempt_id is None,
                        ReasonCode.RECOVERY_REQUIRED)
                self.automatic = self._automatic(discovery)
                if parse_version(release.version) <= parse_version(current):
                    self.automatic.verify_current(installation)
                    return TickResult(disposition='current', target_version=release.version)
                release = self.automatic.select(installation, release)
                authorization = self.automatic.authorization
            # Trust comes from host configuration, not the candidate's snapshot.
            self.public_key_sha256 = discovery.verifier.public_key_sha256()
            self.repository = discovery.repository
        check_deployment(installation, release.manifest, authorization, release.raw_manifest)
        verification = Verification(True, release.verification.method, release.verification.signer,
                                    release.verification.issuer, release.verification.tool)
        automatic_plan = {} if authorization is None else {
            'deployment_authorization': authorization, 'raw_manifest': release.raw_manifest}

        def make_plan(fresh, history):
            return build_plan(release.manifest, verification, fresh, self.settings, history=history, **automatic_plan)

        prepared = prepare_update(release, installation, self.registration.binding, make_plan, history,
                                  **({'deployment_authorization': authorization} if authorization is not None else {}))
        if self.policy.mode == 'download':
            self._controls()
            capacity = lambda: self._capacity(installation.fresh())
            self.download = Downloader(installation, self.engine_state, space_available=capacity, clock=self._now,
                **({'deployment_authorization': authorization} if authorization is not None else {})).download(prepared)
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
                                window_occurrence=self.window_occurrence, clock=self._now, report=self._progress,
                                automatic=self.automatic)
        try:
            engine = guarded_updater(installation, self.settings, self.engine_state, guard, supervisor)
            if self.automatic is not None:
                with self.engine_state.lock(automation=True):
                    guard.validate('before_recovery', guard.context(engine))
                self.download = Downloader(installation, self.engine_state,
                    space_available=lambda: self._capacity(installation.fresh()), clock=self._now,
                    deployment_authorization=authorization).download(prepared)
                self._save(download_identity=self.target)
                self._controls()
                with self.engine_state.lock(automation=True):
                    decision = self.automatic.preflight(installation)
                if not decision.allowed:
                    return TickResult(disposition='available', reason_codes=decision.reason_codes,
                        skip_reason='notify_only', target_version=release.version, preflight_decision=decision)
                # The engine revalidates proofs/state/window under its own lock
                # after prefetch, before reservation, backup, or any env write.
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
        if self.automatic is not None and result.restore_result in ('verified', 'recovery_required'):
            observation = self.automatic.latest_restore_observation
            restored = result.restore_result == 'verified'
            runtime = observation.facts.runtime_observation if observation is not None else None
            require(not restored or runtime is not None and self.store.read_state().active_attempt_id is None,
                    ReasonCode.RECOVERY_REQUIRED)
            restoration = RestorationResult(guard.attempt.attempt_id,
                'restored' if restored else 'recovery_required', current, release.version,
                result.phase or ('restored' if restored else 'restore_decided'),
                self.automatic.backup_seal, runtime, result.reason_codes,
                'none' if restored else 'review_recovery')
            projection = restoration_projection(restoration, now=self._now(), started_at=self.started_at,
                forward_result='interrupted' if result.forward_result == 'interrupted' else 'failed')
            self.operation, self.last_run, self.safety = (projection[k] for k in ('operation', 'last_run', 'safety'))
            self.restoration = restoration
            return TickResult(result.code, 'restored' if restored else 'recovery_required', result.reason_codes,
                target_version=release.version, transaction_started=result.transaction_started,
                rollout_started=result.rollout_started, forward_result=result.forward_result, receipt=result.receipt,
                restore_result=result.restore_result, effective_version=result.effective_version)
        self.safety = {'backup_required': True,
                       'backup_state': 'reported_complete' if guard.proof is not None else 'not_evaluated',
                       'readiness': 'passed' if result.code == 0 and result.forward_result == 'succeeded'
                       or result.fallback_result == 'succeeded' else 'failed' if result.rollout_started else 'unknown',
                       'automatic_rollback': 'ineligible' if self.policy.automatic_rollback else 'disabled',
                       'operator_action': 'none' if result.code == 0 else 'review_recovery'
                       if result.transaction_started or ReasonCode.RECOVERY_REQUIRED in result.reason_codes else 'inspect_host'}
        outcome = ('succeeded' if result.forward_result == 'succeeded' else
                   ('interrupted_' if result.forward_result == 'interrupted' else 'failed_') +
                   ('during_rollout' if result.rollout_started else 'before_rollout'))
        self.last_run = {'kind': 'apply', 'automatic': True, 'outcome': outcome, 'from_version': current,
                         'target_version': release.version, 'finished_at': stamp(self.sample.wall),
                         'fallback_outcome': result.fallback_result}
        if self.automatic is not None:
            self.safety.update(fallback_strategy='restore_backup', restore_state=result.restore_result,
                backup_state='sealed' if guard.proof is not None else 'pending', automatic_rollback='ineligible',
                guidance_id='review_recovery' if disposition == 'recovery_required' else 'none')
            self.last_run.update(forward_result=result.forward_result, restore_result=result.restore_result,
                effective_version=result.effective_version,
                failed_target_version=release.version if result.forward_result != 'succeeded' else None)
        return TickResult(result.code, disposition, result.reason_codes, target_version=release.version,
                          transaction_started=result.transaction_started, rollout_started=result.rollout_started,
                          forward_result=result.forward_result, fallback_result=result.fallback_result, receipt=result.receipt,
                          restore_result=result.restore_result, effective_version=result.effective_version)

    def _progress(self, boundary, *, proof_complete=False, phase=None):
        states = {'before_backup': ('backing_up', 'backup'), 'after_backup': ('verifying', 'image_verification'),
                  'before_env_commit': ('applying', 'env_selection'), 'before_rollout': ('applying', 'rollout'),
                  'before_fallback': ('rolling_back', 'recovery'),
                  'before_restore': ('restoring', 'restore'), 'after_restore': ('restored', 'restore')}
        if boundary in states:
            state, stage = states[boundary]
            restoring = boundary in ('before_restore', 'after_restore')
            self.operation = {'state': state, 'kind': 'restore' if restoring else 'rollback' if state == 'rolling_back' else 'apply',
                              'stage': stage, 'started_at': stamp(self.started_at), 'finished_at': None,
                              'reported_at': stamp(self.sample.wall), 'reason_codes': []}
            self.safety = {'backup_required': True, 'backup_state': 'reported_complete' if proof_complete else 'required',
                           'readiness': 'pending',
                           'automatic_rollback': 'ineligible' if self.policy.automatic_rollback else 'disabled',
                           'operator_action': 'none'}
            if self.policy.qualification_policy == 'standalone-cold-restore-v1':
                phases = {'before_backup': 'quiescing', 'after_backup': 'backup_sealed',
                    'before_env_commit': 'env_commit_pending', 'before_rollout': 'target_starting',
                    'before_restore': 'restore_decided', 'after_restore': 'restore_verified'}
                self.operation['phase'] = phase or phases.get(boundary)
                self.safety.update(fallback_strategy='restore_backup',
                    backup_state='sealed' if proof_complete else 'quiescing' if boundary == 'before_backup' else 'pending',
                    restore_state='verified' if boundary == 'after_restore' else 'running' if restoring else 'not_needed',
                    automatic_rollback='ineligible', guidance_id='none')
            self._publish(TickResult())

    def _capture_publication(self):
        if self.publisher is not None and self.ticket is None and not self.reporting_failed:
            try:
                self.ticket, self.previous = self.publisher.capture()
            except StoreBusy:
                self.warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)
            except Exception:
                self.reporting_failed = True
                self.warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)

    def _publish(self, result, *, terminal=False):
        if self.publisher is None or self.reporting_failed:
            return
        try:
            if result.skip_reason is not None and self.discovered is None and self.observation is None:
                self.publisher.publish_controls(policy=self.policy, now=self.sample.wall,
                                                last_tick_at=self.sample.wall)
                return
            self._capture_publication()
            if self.ticket is None or self.reporting_failed:
                return
            registration = self.registration or self.store.read_registration()
            state = self.store.read_state()
            control = self.store.read_control()
            snapshot = build_status(registration.binding, self.policy, control, state, now=self.sample.wall,
                                    previous=self.previous, observation=self.observation, discovery=self.discovered,
                                    download=self.download, operation=self.operation, last_run=self.last_run,
                                    reason_codes=result.reason_codes, safety=self.safety,
                                    automatic_decision=result.preflight_decision)
            def publish():
                return self.publisher.publish(snapshot, expected=self.ticket, operation_token=self.token)
            # Progress can skip once; a terminal report has no later callback.
            # StoreBusy consumes nothing, so retry only this write with the SAME
            # ticket. Never recapture on conflict or replay engine mutation.
            self.ticket = retry_busy(publish) if terminal else publish()
            snapshot['sequence'] = self.ticket.generation
            self.previous = snapshot
        except StoreBusy:
            # No private intent was written. A skipped progress report or an
            # exhausted terminal retry is a warning, never an engine failure.
            self.warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)
        except Exception:
            # A newer OPERATION report wins. Never recapture to defeat its CAS.
            self.reporting_failed = True
            self.warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)

    def _finish(self, result):
        decision = getattr(self.automatic, 'preflight_decision', None)
        if result.preflight_decision is None and isinstance(decision, AutomaticUpdateDecision):
            result = replace(result, preflight_decision=decision)
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
                              'recovery_required': 'recovery_required', 'restored': 'restored',
                              'failed_available': 'failed'}.get(result.disposition, 'blocked')
                kind = 'restore' if result.disposition in ('restored', 'failed_available') else (
                    'apply' if result.forward_result != 'none' else 'download' if self.policy.mode == 'download' else 'check')
                if self.restoration is None and not (result.disposition == 'applied'
                        and self.operation is not None and self.operation.get('phase') == 'succeeded'):
                    self.operation = {'state': state_name, 'kind': kind, 'stage': None,
                                      'started_at': stamp(self.started_at), 'finished_at': stamp(now),
                                      'reported_at': stamp(now), 'reason_codes': [r.value for r in result.reason_codes]}
                else:
                    state_name, kind = self.operation['state'], self.operation['kind']
                event = OperationEvent(state_name, kind, self.operation.get('stage'), now, self.token,
                                       result.reason_codes, phase=self.operation.get('phase'))
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
            self._publish(result, terminal=True)
        except (Interrupted, KeyboardInterrupt):
            # A terminal engine outcome is already durable. An interruption of
            # reporting cannot turn success into failure (nor retry a failure).
            self.warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)
        return replace(result, warnings=tuple(dict.fromkeys(self.warnings)))
