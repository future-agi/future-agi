"""Generated-baseline lifecycle around the existing engine; no second mutator.

Setup supplies BackupConfiguration.raw to BaselineStore.enroll(backup_state=...).
Each later generation references an immutable backup sidecar written before its
atomic head publication. Observation/render/capture injections are host adapters,
not release-controlled scripts. Defaults import the fixed adapters lazily.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import time

from .baseline import BaselineStore, normalise_observation, sha
from .compatibility import authorize_execution, decode_document, estimate_automatic_update, transition_measurements, topology_profile
from .contracts import (AutomaticUpdateDecision, BackupSeal, ColdHold, InstallationBinding, ReasonCode,
                        installation_binding_hash)
from .schedule import remaining_seconds
from .host_facts import EvidenceFacts, executor_implementation_sha256
from .manifest import canonical_json
from .orchestrator import GuardRefused, check_binding, fingerprints, require
from .standalone_backup import BackupBinding, BackupPolicy, StandaloneBackup, capture_binding


@dataclass(frozen=True)
class BackupConfiguration:
    binding: BackupBinding
    destination: Path
    policy: BackupPolicy = BackupPolicy()

    def __post_init__(self):
        require(isinstance(self.binding, BackupBinding) and isinstance(self.policy, BackupPolicy),
                ReasonCode.BACKUP_REQUIRED)
        require(isinstance(self.destination, Path) and self.destination.is_absolute(), ReasonCode.BACKUP_REQUIRED)

    @property
    def raw(self):
        return canonical_json({'schema_version': 1, 'binding': decode_document(self.binding.raw),
                               'destination': str(self.destination), 'policy': asdict(self.policy)})

    @classmethod
    def load(cls, raw):
        require(type(raw) is bytes, ReasonCode.BACKUP_REQUIRED)
        doc = decode_document(raw)
        require(set(doc) == {'schema_version', 'binding', 'destination', 'policy'}
                and type(doc['schema_version']) is int and doc['schema_version'] == 1
                and type(doc['destination']) is str and type(doc['policy']) is dict
                and set(doc['policy']) == set(asdict(BackupPolicy())), ReasonCode.BACKUP_REQUIRED)
        return cls(BackupBinding(canonical_json(doc['binding'])), Path(doc['destination']), BackupPolicy(**doc['policy']))


def default_observer(registration, policy, clock):
    from .standalone_state import StandaloneStateObserver
    return StandaloneStateObserver(registration, timeout=policy.probe_timeout_seconds,
        stability_period_seconds=policy.stability_period_seconds, clock=clock)


def default_size_observer(registration, policy, clock):
    """Fixed host-observation API, loaded lazily; no unknown-as-zero fallback."""
    from .standalone_state import StandaloneStateObserver
    return StandaloneStateObserver(registration, timeout=policy.probe_timeout_seconds, clock=clock)


def default_renderer(installation, raw):
    from .standalone_state import fingerprint_for_env
    return fingerprint_for_env(installation, raw)


@dataclass(frozen=True)
class _SuccessMetadataInstallation:
    """Retained receipt identity only; deliberately has no Docker/Compose API.

    A success decision authorizes metadata replay, not a fresh runtime adoption.
    Do not resolve the current env alias or inspect the currently running stack.
    Only pass this context to the engine's success-finalization API.
    """
    binding: InstallationBinding
    runner: object

    def identity(self):
        try:
            env = str(self.binding.env_target.relative_to(self.binding.checkout))
        except ValueError:
            env = str(self.binding.env_target)
        return dict(mode=self.binding.topology, production=self.binding.production,
                    env_file=env, project=self.binding.compose_project)


def pending_restore_attempt(store):
    """Find a restore CAS whose engine ledger still needs terminal replay.

    Restore leaves the predecessor head intact, so head.attempt_id cannot index
    this crash cut. Authenticate its immutable RestoreSeal and exact ledger.
    Caller holds runner.lock -> update.lock; this performs no runtime mutation.
    """
    import os
    import re
    from .baseline import RestoreSeal
    from .backup_proof import BackupExpectation
    from .recovery import RecoveryLedger
    baseline = BaselineStore(store)
    state = store.read_state()
    if state.baseline_head is None or state.active_attempt_id is not None:
        return None
    try:
        with store._directory(child='transitions') as directory:
            names = os.listdir(directory)
    except FileNotFoundError:
        return None
    pending = []
    for name in sorted(names):
        if re.fullmatch(r'[0-9a-f]{32}-restore.json', name) is None:
            continue
        seal = baseline._record('transitions', name, RestoreSeal)
        if seal.source_head != state.baseline_head:
            continue
        attempt = seal.terminal
        require(store.read_attempt(attempt.attempt_id) == attempt, ReasonCode.RECOVERY_REQUIRED)
        retained = baseline.automatic_attempt(attempt.attempt_id)
        old = baseline._head(retained.old_head)
        config = BackupConfiguration.load(baseline.backup_state(old))
        expected = BackupExpectation(attempt.attempt_id, old.installation_fingerprint,
            attempt.target.manifest_hash, old.release.version, attempt.reserved_at,
            topology_profile(attempt.binding.topology, attempt.binding.profiles).backup_domains)
        ledger = RecoveryLedger.open(config.destination / attempt.attempt_id, expected=expected,
            operation_token=attempt.operation_token, run_id=retained.run_id,
            binding_sha256=installation_binding_hash(attempt.binding))
        require(ledger.direction == 'restore_decided', ReasonCode.RECOVERY_REQUIRED)
        if ledger.phase != 'restored':
            pending.append(attempt.attempt_id)
    require(len(pending) <= 1, ReasonCode.RECOVERY_REQUIRED)
    return pending[0] if pending else None


class AutomaticLifecycle:
    """One tick's authenticated baseline plus fixed host adapters.

    Call all methods that observe/prepare/complete under runner.lock -> update.lock.
    Terminal reconciliation is metadata-only. Pending direction dispatch delegates
    safety mutations to Updater.resume_automatic_locked, never to a new apply,
    legacy image rollback, discovery or image pull.
    """
    def __init__(self, store, discovery, registration, clock, *, observer=None,
                 render_config=default_renderer, capture=capture_binding,
                 size_observer_factory=default_size_observer, executor_digest=executor_implementation_sha256):
        self.store, self.discovery, self.registration, self.clock = store, discovery, registration, clock
        self.baseline = BaselineStore(store, verifier=discovery.verifier, clock=clock)
        self.observer = observer
        self.render_config, self.capture = render_config, capture
        self.executor_digest = executor_digest
        self.head = self.baseline.current()
        self.execution = decode_document(self.baseline.blob(self.head.execution_deployment))
        self.target = self.authorization = self.intent = None
        self.latest_observation = None
        self.preimage_pins = None
        self.source_observation = None
        self.cold_hold = self.backup_seal = self.ledger = None
        self.size_observer_factory, self.size_observer = size_observer_factory, None
        self.measurements = self.preflight_decision = None
        self.restore_intent = None
        self.backup_raw = None
        self.success_decided = False
        self.latest_restore_observation = None
        self.attempt_intent = None

    def _verification_window(self, installation):
        # A backup may outlast discovery's earlier short budget. Start a new
        # bounded retained-evidence verification, never extend the tick budget.
        runner = installation.runner
        self.baseline.verifier.begin_discovery(min(runner.deadline,
            time.monotonic() + runner.policy.probe_timeout_seconds))

    def _observation(self, installation, release, boundary):
        if self.observer is None:
            self.observer = default_observer(self.registration, installation.runner.policy, self.clock)
        fresh = installation.fresh()  # Never observe the guard's normalized plan overlay.
        check_binding(fresh, self.registration.binding)
        require(boundary not in ('after_backup', 'before_env_commit', 'before_rollout'),
                ReasonCode.AUTOMATIC_RECOVERY_PENDING)
        observation = normalise_observation(self.observer.observe(fresh, current_release=release,
            execution_deployment=self.execution, boundary='reconcile' if boundary == 'restored' else boundary))
        require(fingerprints(fresh)[:2] == (observation.facts.config_fingerprint,
                                           observation.facts.profile_fingerprint), ReasonCode.CONFIGURATION_CHANGED)
        observation = replace(observation, facts=replace(observation.facts,
            executor_sha256=self.executor_digest(installation.root)))
        return observation

    def verify_current(self, installation):
        self._verification_window(installation)
        from .baseline import sha
        previous = self.baseline.evidence(self.head.release)
        observation = self._observation(installation, previous, 'before_recovery')
        self.baseline._observe(observation, previous)
        require((observation.facts.config_fingerprint, observation.facts.profile_fingerprint,
                 sha(observation.env_bytes), observation.structure_sha256) ==
                (self.head.config_fingerprint, self.head.profile_fingerprint, self.head.env_sha256,
                 self.head.structure_sha256), ReasonCode.CONFIGURATION_CHANGED)
        require(dict(observation.facts.deployment_files) == self.execution['files'], ReasonCode.DEPLOYMENT_FILES_MISMATCH)
        self.latest_observation = observation
        if self.ledger is not None and self.ledger.backup_seal is None and self.ledger.phase == 'failed_available':
            self.complete_failed_available(observation)
        return observation

    def complete_failed_available(self, observation):
        require(self.ledger is not None and self.ledger.direction == 'forward_pending'
                and self.ledger.backup_seal is None and self.ledger.phase == 'failed_available'
                and self.ledger.snapshot['pending'] is None and self.attempt_intent is not None,
                ReasonCode.RECOVERY_REQUIRED)
        return self.baseline.finish_failed_available(self.attempt_intent, observation)

    def select(self, installation, candidate):
        self.target = self.discovery.evidence(candidate)
        require(not self.target.proof_reason_codes and bool(self.target.release_proof),
                self.target.proof_reason_codes[0] if self.target.proof_reason_codes else ReasonCode.UNCERTIFIED_RELEASE)
        self._verification_window(installation)
        previous = self.baseline.evidence(self.head.release)
        observation = self._observation(installation, previous, 'before_recovery')
        # The observation alone spans the full stability period, which may
        # exceed probe_timeout. Re-verify retained evidence in a fresh window.
        self._verification_window(installation)
        obs = self.baseline.observe(observation, self.target)
        self.authorization = authorize_execution(self.target.raw_manifest, obs.predecessor_raw, obs.release_proof,
            repository=self.target.repository, mode=self.registration.binding.topology,
            profiles=self.registration.binding.profiles, platform=obs.platform, deployment_files=obs.deployment_files)
        self.source_observation = observation
        self.measurements = transition_measurements(self.authorization.row)
        return self.target

    def preflight(self, installation):
        """A6 while A is healthy, AFTER prefetch and BEFORE any stop/reservation.

        The runner calls once after download; facts(before_backup) calls again at
        the final slow-observation boundary. Measurements come only from the
        authenticated exact predecessor row selected above. Missing host data is
        never an estimate of zero or permission to fit.
        """
        policy = installation.runner.policy
        if self.measurements is None:
            self.preflight_decision = AutomaticUpdateDecision(False, (ReasonCode.UNCERTIFIED_RELEASE,))
            return self.preflight_decision
        try:
            config = BackupConfiguration.load(self.baseline.backup_state(self.head))
            if self.size_observer is None:
                self.size_observer = self.size_observer_factory(self.registration, policy, self.clock)
            sizes = self.size_observer.measure_update_sizes(installation.fresh(),
                measurements=self.measurements, backup_destination=config.destination)
        except Exception as exc:
            # Preserve the fixed observer's measured byte/inode shortage, but
            # never expose arbitrary exception text or unknown-as-zero sizes.
            reason = (ReasonCode.RESTORE_CAPACITY_INSUFFICIENT if isinstance(exc, GuardRefused)
                and exc.reason_code == ReasonCode.RESTORE_CAPACITY_INSUFFICIENT else ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            self.preflight_decision = AutomaticUpdateDecision(False, (reason,))
            return self.preflight_decision
        tick = max(0, installation.runner.deadline - time.monotonic())
        # An always-open window is infinite, but the pure estimator deliberately
        # requires finite real budgets. The tick is the tighter bound in that case.
        window = min(tick, remaining_seconds(policy.maintenance_window, self.clock()))
        self.preflight_decision = estimate_automatic_update(self.measurements, sizes, policy,
            window_remaining_seconds=window, tick_remaining_seconds=tick)
        return self.preflight_decision

    def retain_cold_hold(self, artifact, ledger):
        """Called by the engine with its independently verified sealed backup."""
        require(isinstance(artifact.hold, ColdHold) and isinstance(artifact.seal, BackupSeal),
                ReasonCode.BACKUP_PROOF_INVALID)
        require(self.source_observation is not None, ReasonCode.RECOVERY_REQUIRED)
        self.baseline.validate_hold(self.source_observation, artifact.hold)
        self.cold_hold, self.backup_seal, self.ledger = artifact.hold, artifact.seal, ledger
        self.backup_raw = artifact.proof.raw

    def _held_observation(self, installation):
        require(self.source_observation is not None and self.ledger is not None, ReasonCode.RECOVERY_REQUIRED)
        journal = self.baseline.engine_state.read_journal()
        inverse = journal.get('env_recovery') if journal else None
        hold = self.backup(installation).verify_hold(self.ledger,
            self.source_observation.facts.runtime_observation, env_recovery=inverse)
        self.baseline.validate_hold(self.source_observation, hold)
        self.cold_hold = hold
        return self.source_observation

    def facts(self, installation, boundary):
        self._verification_window(installation)
        held = boundary in ('after_backup', 'before_env_commit', 'before_rollout')
        def probe(fresh, at):
            if held:
                self.latest_observation = self._held_observation(fresh)
            else:
                previous = self.baseline.evidence(self.head.release)
                self.latest_observation = self._observation(fresh, previous, at)
                if self.attempt_intent is None:
                    self.source_observation = self.latest_observation
            # EvidenceFacts re-verifies retained evidence after this slow probe.
            self._verification_window(installation)
            return self.latest_observation
        provider = EvidenceFacts(self.baseline, self.target, probe=probe,
            intent=lambda at: self.intent if at == 'before_rollout' else None,
            cold_hold=lambda: self.cold_hold if held else None,
            backup_seal=lambda: self.backup_seal if held else None)
        result = provider(installation.fresh(), boundary)
        if boundary == 'before_backup':
            decision = self.preflight(installation)
            require(decision.allowed, decision.reason_codes[0] if decision.reason_codes else ReasonCode.UNCERTIFIED_RELEASE)
        return replace(result, executor_sha256=self.executor_digest(installation.root))

    def backup(self, installation):
        # The legacy 60-second probe default is deliberately NOT raised silently.
        # Setup must explicitly select >=70 seconds (90 is the qualified policy).
        require(installation.runner.policy.probe_timeout_seconds >= 70, ReasonCode.BACKUP_REQUIRED)
        config = BackupConfiguration.load(self.baseline.backup_state(self.head))
        require(config.policy.timeout_seconds <= installation.runner.policy.backup_timeout_seconds
                and config.policy.readiness_seconds <= installation.runner.policy.wait_timeout_seconds,
                ReasonCode.BACKUP_REQUIRED)
        return StandaloneBackup(installation, config.binding, config.destination, policy=config.policy, clock=self.clock)

    def retain_attempt(self, attempt, run_id):
        require(self.source_observation is not None and self.target is not None, ReasonCode.RECOVERY_REQUIRED)
        self.attempt_intent = self.baseline.retain_attempt(attempt, run_id=run_id, target=self.target,
                                                         observation=self.source_observation)
        return self.attempt_intent

    def prepare_restore(self, engine):
        require(self.ledger is not None and self.ledger.direction == 'restore_decided'
                and self.backup_seal is not None and self.source_observation is not None,
                ReasonCode.RECOVERY_REQUIRED)
        attempt = self.store.read_attempt(self.backup_seal.attempt_id)
        with self.store._directory(child='transitions') as transitions:
            import os
            retained = attempt.attempt_id + '-restore-intent.json' in os.listdir(transitions)
        if retained:
            self.restore_intent = self.baseline.restore_intent(attempt.attempt_id)
            require(self.restore_intent.backup_sha256 == self.backup_seal.proof_sha256
                    and self.restore_intent.run_id == self.backup_seal.run_id
                    and self.restore_intent.attempt.operation_token == attempt.operation_token,
                    ReasonCode.RECOVERY_REQUIRED)
            return self.restore_intent  # Never invent a new inverse after restored A has started.
        journal = engine.active_record or engine.state.read_journal()
        inverse = canonical_json(journal['env_recovery']) if journal and journal.get('env_recovery') else b'null'
        self.restore_intent = self.baseline.prepare_restore(attempt, expected_head=self.head.identity,
            run_id=self.backup_seal.run_id, source_observation=self.source_observation,
            backup_raw=self.backup_raw, backup_seal=self.backup_seal, env_inverse_raw=inverse)
        return self.restore_intent

    def complete_restore(self, engine, receipt):
        self._verification_window(engine.installation)
        require(self.ledger is not None and self.ledger.direction == 'restore_decided', ReasonCode.RECOVERY_REQUIRED)
        intent = self.restore_intent or self.baseline.restore_intent(self.backup_seal.attempt_id)
        observation = self._observation(engine.installation, self.baseline.evidence(self.head.release), 'restored')
        result = self.baseline.commit_restore(intent, receipt_name=receipt.name, observation=observation)
        self.latest_restore_observation = observation
        return result

    def resume(self, installation_factory, state, settings, *, supervisor):
        """Dispatch retained direction BEFORE constructing a live installation.

        Caller holds runner.lock -> update.lock. installation_factory() performs
        registered live binding checks, but is called only for safety recovery,
        never for an authenticated success_decided metadata replay. Other
        directions go to the shipping engine's resume_automatic_locked, NEVER
        apply/rollback/discover/pull. Missing authority or API fails closed.
        """
        from .apply import Updater
        from .backup_proof import BackupExpectation
        from .recovery import RecoveryLedger
        current = self.store.read_state()
        attempt_id = current.active_attempt_id or pending_restore_attempt(self.store) or self.head.attempt_id
        require(attempt_id is not None, ReasonCode.RECOVERY_REQUIRED)
        retained = self.baseline.automatic_attempt(attempt_id)
        self.attempt_intent = retained
        attempt = self.store.read_attempt(attempt_id)
        require(attempt.operation_token == retained.attempt.operation_token
                and (current.active_attempt_id is None or attempt.operation_token == current.active_operation_token),
                ReasonCode.RECOVERY_REQUIRED)
        self.head = self.baseline._head(retained.old_head)
        self.execution = decode_document(self.baseline.blob(self.head.execution_deployment))
        metadata = _SuccessMetadataInstallation(attempt.binding, supervisor)
        self._verification_window(metadata)
        self.target = self.baseline.evidence(retained.target)
        self.source_observation = self.baseline.retained_observation(retained)
        config = BackupConfiguration.load(self.baseline.backup_state(self.head))
        layout = topology_profile(attempt.binding.topology, attempt.binding.profiles)
        expected = BackupExpectation(attempt.attempt_id, self.head.installation_fingerprint,
            attempt.target.manifest_hash, self.head.release.version, attempt.reserved_at, layout.backup_domains)
        self.ledger = RecoveryLedger.open(config.destination / attempt.attempt_id, expected=expected,
            operation_token=attempt.operation_token, run_id=retained.run_id,
            binding_sha256=installation_binding_hash(attempt.binding))
        require(self.ledger.snapshot['old_head'] == retained.old_head
                and self.ledger.snapshot['source_observation_sha256'] == self.source_observation.facts.runtime_observation.sha256,
                ReasonCode.RECOVERY_REQUIRED)
        self.backup_seal = self.ledger.backup_seal
        if self.backup_seal is not None:
            from .standalone_backup import _directory, _read
            with _directory(self.ledger.directory) as directory:
                self.backup_raw = _read(directory, 'proof.json')
            require(sha(self.backup_raw) == self.backup_seal.proof_sha256,
                    ReasonCode.BACKUP_INVENTORY_MISMATCH)
        if current.baseline_head != retained.old_head:
            from .baseline import SuccessSeal
            seal = self.baseline._record('transitions', attempt_id + '-success.json', SuccessSeal)
            require(seal.successor_head == current.baseline_head and current.active_attempt_id is None
                    and self.ledger.direction != 'restore_decided', ReasonCode.RECOVERY_REQUIRED)
            self.success_decided = True  # A committed successor also forbids restoration.
            require(self.ledger.direction == 'success_decided', ReasonCode.RECOVERY_REQUIRED)
        if self.ledger.direction == 'success_decided':
            self.success_decided = True
            self.intent = self.baseline.intent(attempt_id)
            # The engine owns completion from its immutable decision record even
            # if the receipt was not yet written. Do not require that receipt as
            # a precondition or execute another target startup.
        require(self.success_decided or current.baseline_head == retained.old_head, ReasonCode.RECOVERY_REQUIRED)
        if self.success_decided:
            # Authenticate the pre-decision observation and successor sidecar
            # before any receipt/journal/head replay. No Docker binary, Compose
            # model, env selection, ownership or health observation is needed.
            self.baseline.accepted_success(self.intent, self.ledger.snapshot['success_seal'])
            engine = Updater(metadata, settings, state, hook_runner=supervisor)
            finalize = getattr(engine, 'finalize_automatic_success_locked', None)
            require(callable(finalize), ReasonCode.RECOVERY_REQUIRED)
            receipt = finalize(lifecycle=self, ledger=self.ledger)
            require(isinstance(receipt, Path) and self.store.read_state().active_attempt_id is None,
                    ReasonCode.RECOVERY_REQUIRED)
            return receipt, self.target.version
        installation = installation_factory()  # Restoration retains all live registered binding checks.
        with self.store._directory(child='transitions') as transitions:
            import os
            completed_restore = attempt.attempt_id + '-restore.json' in os.listdir(transitions)
        if completed_restore:
            require(self.ledger.direction == 'restore_decided', ReasonCode.RECOVERY_REQUIRED)
            from .baseline import RestoreSeal
            seal = self.baseline._record('transitions', attempt.attempt_id + '-restore.json', RestoreSeal)
            observation = self._observation(installation, self.baseline.evidence(self.head.release), 'restored')
            self.baseline.reconcile_restore(attempt.attempt_id, receipt_name=seal.receipt_name, observation=observation)
            self.ledger.checkpoint('restore_metadata_committed')
            self.ledger.checkpoint('restored')
            from .contracts import RestorationResult
            return RestorationResult(attempt.attempt_id, 'restored', self.head.release.version, attempt.target.version,
                'restore_metadata_committed', self.backup_seal, observation.facts.runtime_observation,
                (ReasonCode.RESTORED_AFTER_UPDATE_FAILURE,))
        engine = Updater(installation, settings, state, hook_runner=installation.runner)
        resume = getattr(engine, 'resume_automatic_locked', None)
        require(callable(resume), ReasonCode.RECOVERY_REQUIRED)
        return resume(lifecycle=self, ledger=self.ledger)

    def prepare(self, guard, engine):
        self._verification_window(engine.installation)
        require(self.intent is None and guard.proof is not None, ReasonCode.RECOVERY_REQUIRED)
        observation = self._held_observation(engine.installation)
        intent = self.baseline.prepare_success(guard.attempt, expected_head=self.head.identity,
            run_id=engine.active_record['run_id'], target=self.target, observation=observation,
            backup_raw=guard.proof.raw, render_config=lambda raw: self.render_config(engine.installation, raw),
            backup_max_age_seconds=engine.settings.automation_policy.backup_max_age_seconds,
            cold_hold=self.cold_hold, backup_seal=self.backup_seal)
        require({p.name: p.value for p in intent.pins} == engine.active_record['env_changes'],
                ReasonCode.CONFIGURATION_CHANGED)
        self.intent = intent
        self.preimage_pins = {p.name: observation.facts.env.get(p.name) for p in intent.pins}

    def verify_success(self, engine):
        self._verification_window(engine.installation)
        require(self.intent is not None, ReasonCode.RECOVERY_REQUIRED)
        observation = self._observation(engine.installation, self.baseline.evidence(self.intent.target), 'success')
        self.baseline.validate_success_observation(self.intent, observation)

    def _successor_backup(self, installation, intent, observation):
        old = self.baseline._head(intent.old_head)
        config = BackupConfiguration.load(self.baseline.backup_state(old))
        images = {}
        for service, replicas in {**observation.facts.product_images, **observation.facts.dependency_images}.items():
            ids = {i.image_id for i in replicas}
            require(len(ids) == 1, ReasonCode.IMAGE_DIGEST_MISMATCH)
            images[service] = next(iter(ids))
        binding = self.registration.binding
        from .docker_host import bounded_installation
        reader = bounded_installation(installation.fresh(), installation.runner.policy.probe_timeout_seconds)
        successor = self.capture(reader, installation_fingerprint=binding.installation_fingerprint,
            docker_context=binding.docker_context, daemon_id=binding.daemon_identity, project=binding.compose_project,
            profiles=binding.profiles, verified_images=images, deployment_hashes=self.execution['files'],
            previous=config.binding)
        return replace(config, binding=successor).raw

    def retain_success(self, installation, observation):
        require(self.intent is not None and self.ledger is not None
                and self.ledger.direction == 'forward_pending', ReasonCode.RECOVERY_REQUIRED)
        self.baseline.validate_success_observation(self.intent, observation)
        backup = self._successor_backup(installation, self.intent, observation)
        self.baseline.retain_success(self.intent, observation, backup)
        self.latest_observation = observation

    def complete_success(self, engine, receipt):
        require(self.ledger is not None and self.ledger.direction == 'success_decided', ReasonCode.RECOVERY_REQUIRED)
        self.success_decided = True
        self._verification_window(engine.installation)
        decision = self.ledger.snapshot['success_seal']
        observation, _ = self.baseline.accepted_success(self.intent, decision)
        self.baseline.commit_decided_success(self.intent, receipt_name=receipt.name, decision=decision)
        self.latest_observation = observation

    def reconcile(self, installation, state, *, attempt_id=None):
        """Only an exact succeeded receipt + intent. Caller already holds both locks."""
        self._verification_window(installation)
        require(self.ledger is not None and self.ledger.direction == 'success_decided',
                ReasonCode.AUTOMATIC_RECOVERY_PENDING)
        current = self.store.read_state()
        attempt_id = attempt_id or current.active_attempt_id or self.head.attempt_id
        require(attempt_id is not None, ReasonCode.RECOVERY_REQUIRED)
        intent = self.baseline.intent(attempt_id)
        old = self.baseline._head(intent.old_head)
        self.execution = decode_document(self.baseline.blob(old.execution_deployment))
        history = state.history(installation.identity())
        matches = [r for r in history if r.get('run_id') == intent.run_id]
        require(len(matches) == 1 and history[-1] == matches[0] and matches[0].get('status') == 'succeeded'
                and matches[0].get('kind') == 'apply', ReasonCode.RECOVERY_REQUIRED)
        receipt = Path(matches[0]['_path'])
        latest = state.latest_receipt()
        require(latest is not None and Path(latest['_path']) == receipt, ReasonCode.RECOVERY_REQUIRED)
        # Journal/receipt materialization belongs to the engine finalization
        # API. This compatibility entrypoint handles only an already-finalized
        # engine receipt, from the immutable pre-decision observation.
        require(state.read_journal() is None, ReasonCode.AUTOMATIC_RECOVERY_PENDING)
        decision = self.ledger.snapshot['success_seal']
        observation, _ = self.baseline.accepted_success(intent, decision)
        self.baseline.commit_decided_success(intent, receipt_name=receipt.name, decision=decision)
        self.latest_observation = observation
        self.success_decided = True
        return receipt, intent.target.version
