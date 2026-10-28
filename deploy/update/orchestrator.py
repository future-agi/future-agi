"""Small scheduled adapters around the ONLY transaction engine, Updater.

S06 owns tick scheduling/enrollment/publication. It must hold runner_lock before
calling this adapter; Updater owns update.lock. Observations are fresh private
host baseline evidence, never public JSON. Missing evidence fails closed.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Tuple

from .apply import Blocked, Updater, _digest_set
from .backup_proof import BackupExpectation, read_backup_proof
from .compatibility import (Controls, Observations, evaluate_automatic_apply, plain, topology_profile)
from .contracts import InstallationBinding, PreparedUpdate, ReasonCode, installation_binding_hash
from .envfile import EnvFileError
from .install import ComposeCommand, Installation, resolve_compose_command
from .manifest import ManifestError, hash_path, manifest_hash, parse_image_ref
from .runner_state import RunnerStateStore, TargetIdentity
from .schedule import occurrence, start_gate


class GuardRefused(Blocked):
    """No exception text, paths, env values or tool output crosses this boundary."""
    def __init__(self, reason_code: ReasonCode):
        self.reason_code = ReasonCode(reason_code)
        self.reason_codes = (self.reason_code,)
        super().__init__(self.reason_code.value)


def require(ok, reason=ReasonCode.CONFIGURATION_CHANGED):
    if not ok:
        raise GuardRefused(reason)


def profiles(installation):
    return tuple(sorted(filter(None, installation.env.get('COMPOSE_PROFILES', '').split(','))))


def inspection_installation(installation):
    """Separate pure scheduled reads from remote mutation authority.

    A timed-out info/config/image client cannot leave a late installation
    writer. Never clear the actual supervisor's uncertainty: start/create/stop
    still use it and their durable pending operations still block restoration.
    Legacy/manual callers keep their existing supervision.
    """
    from .processes import ProcessSupervisor
    if isinstance(getattr(installation, 'runner', None), ProcessSupervisor):
        from .docker_host import bounded_installation
        return bounded_installation(installation, min(30, installation.runner.policy.probe_timeout_seconds))
    return installation


def fingerprints(installation):
    """Private fingerprints only. Never return resolved Compose config to the app."""
    installation = inspection_installation(installation)
    files = {str(path): hash_path(path.parent, path.name) for path in installation._ownership_files()}
    from .settings import CONFIG_FILE
    try:
        updater_config = hashlib.sha256((installation.root / CONFIG_FILE).read_bytes()).hexdigest()
    except FileNotFoundError:
        updater_config = None
    return (manifest_hash({'config': installation.config(refresh=True), 'env': installation.env,
                           'files': files, 'environment': installation.environ, 'updater_config': updater_config}),
            manifest_hash(list(profiles(installation))), manifest_hash(files))


def check_deployment(installation, manifest, deployment_authorization=None, raw_manifest=None):
    """Reject drift before planning/printing can invoke optional manual git hints."""
    try:
        from .plan import execution_files
        files = execution_files(manifest, deployment_authorization, raw_manifest)
        require(bool(files), ReasonCode.DEPLOYMENT_FILES_MISMATCH)
        require(all(hash_path(installation.root, name) == digest for name, digest in files.items()),
                ReasonCode.DEPLOYMENT_FILES_MISMATCH)
    except (OSError, ValueError, KeyError, TypeError):
        raise GuardRefused(ReasonCode.DEPLOYMENT_FILES_MISMATCH) from None


def check_binding(installation: Installation, binding: InstallationBinding):
    installation = inspection_installation(installation)
    require(os.geteuid() == binding.host_uid, ReasonCode.OWNERSHIP_UNCONFIRMED)
    require(installation.root == binding.checkout.resolve()
            and installation.env_file.resolve() == binding.env_target
            and Path(getattr(installation, 'env_alias', installation.env_file)).absolute() == binding.env_alias
            and installation.production == binding.production
            and ('distributed' if installation.production else installation.mode) == binding.topology,
            ReasonCode.CONFIGURATION_CHANGED)
    require(tuple(installation._ownership_files()) == binding.compose_files
            and profiles(installation) == tuple(sorted(binding.profiles)), ReasonCode.CONFIGURATION_CHANGED)
    require(installation.environ.get('DOCKER_CONTEXT') == binding.docker_context,
            ReasonCode.OWNERSHIP_UNCONFIRMED)
    require(installation.project == binding.compose_project, ReasonCode.OWNERSHIP_UNCONFIRMED)
    installation.check_ownership()
    daemon = installation.docker_run('info', '--format', '{{.ID}}').stdout.strip()
    require(bool(daemon) and daemon == binding.daemon_identity, ReasonCode.OWNERSHIP_UNCONFIRMED)


def env_sha256(engine):
    """sha256 of the env bytes this engine would write (its selected alias), or a typed ENV_CONFLICT refusal."""
    try:
        return hashlib.sha256(engine._env_path().read_bytes()).hexdigest()
    except (OSError, EnvFileError):
        raise GuardRefused(ReasonCode.ENV_CONFLICT) from None


def check_dependency_images(installation, layout, certified):
    """Bind the dependency images Compose will start to the ones the facts certified; returns service -> image ids.

    The whole-stack `compose up` starts whatever each dependency's tag resolves to on this host *now* (a mutable
    tag an operator pulled or retagged would be started in place of the certified image, even with --pull never).
    So, immediately before an automatic rollout, resolve the reference Compose will start for every dependency of
    the topology, inspect it locally and require its image id AND signed digest to equal every certified running
    replica. Nothing is pulled, started or stopped here; any difference is refused before a container is touched.
    """
    installation = inspection_installation(installation)
    services = installation.config(refresh=True).get('services') or {}
    expected = {}
    for name in layout.dependencies:
        replicas = certified.get(name)
        require(isinstance(replicas, (tuple, list)) and bool(replicas), ReasonCode.RUNNING_VERSIONS_UNKNOWN)
        service = services.get(name)
        reference = service.get('image') if isinstance(service, Mapping) else None
        require(isinstance(reference, str) and bool(reference) and not service.get('build'),
                ReasonCode.CONFIGURATION_CHANGED)
        try:
            image_name = parse_image_ref(reference).name
        except ManifestError:
            raise GuardRefused(ReasonCode.CONFIGURATION_CHANGED) from None
        local = installation.image(reference)
        require(local is not None and all(
            local.get('id') == image.image_id and (image_name, image.digest) in _digest_set(local.get('repo_digests') or [])
            for image in replicas), ReasonCode.IMAGE_DIGEST_MISMATCH)
        expected[name] = frozenset(image.image_id for image in replicas)
    return expected


def registered_installation(binding, supervisor, *, status_overlay=None):
    """Resolve supported Compose on registered PATH, never fall back to another daemon."""
    import shutil
    require(supervisor.docker_context == binding.docker_context, ReasonCode.OWNERSHIP_UNCONFIRMED)
    def which(name):
        return shutil.which(name, path=supervisor.environment.get('PATH', os.defpath))

    docker = which('docker')
    require(docker is not None, ReasonCode.OWNERSHIP_UNCONFIRMED)
    # Keep the engine's supported standalone Compose-v2 fallback. Tool discovery
    # uses ONLY the registered PATH and supervised probes, never another daemon.
    compose = resolve_compose_command(supervisor, which=which)
    if os.path.basename(compose[0]) == 'docker':
        compose = ComposeCommand([docker, '--context', binding.docker_context, *compose[1:]],
                                 getattr(compose, 'version', None))
    install = Installation(binding.checkout, production=binding.production, env_file=binding.env_alias,
                           environ=supervisor.environment, runner=supervisor,
                           docker=[docker, '--context', binding.docker_context], compose=compose,
                           status_overlay=status_overlay)
    check_binding(install, binding)
    return install


def prepare_update(release, installation, binding, make_plan, history=(), *, deployment_authorization=None):
    """make_plan(fresh_installation, history) must call the ordinary build_plan."""
    fresh = installation.fresh()
    check_binding(fresh, binding)
    check_deployment(fresh, release.manifest, deployment_authorization, release.raw_manifest)
    config, profile, deployment = fingerprints(fresh)
    return PreparedUpdate(release.manifest, release.raw_manifest, release.raw_bundle,
                          release.manifest_sha256, release.bundle_sha256, release.verification, binding,
                          config, profile, deployment, release.trust_fingerprint, make_plan(fresh, history))


def stage_binding(prepared, installation):
    """Private manual stage/finish binding, invariant ONLY to approved image pins.

    The CLI adapter must supply this on BOTH calls and expose the explicit stale
    acknowledgement. Never derive backup time from this function or from finish.
    Legacy CLI callers with no release_binding keep their existing behavior.
    """
    from .install import VERSION_VARIABLES
    fresh = installation.fresh()
    check_binding(fresh, prepared.binding)
    check_deployment(fresh, prepared.manifest)
    fresh.overlay_env({key: 'stage-binding-pin' for key in VERSION_VARIABLES})
    config, profile, _ = fingerprints(fresh)
    return {'manifest_hash': manifest_hash(plain(prepared.manifest)),
            'manifest_sha256': prepared.manifest_sha256, 'bundle_sha256': prepared.bundle_sha256,
            'installation_fingerprint': prepared.binding.installation_fingerprint,
            'config_fingerprint': config, 'profile_fingerprint': profile,
            'trust_fingerprint': prepared.trust_fingerprint}


@dataclass(frozen=True, repr=False)
class EngineContext:
    binding: InstallationBinding
    prepared: PreparedUpdate
    control_generation: int
    engine: Updater
    backup_environment: Mapping[str, str] = field(default_factory=dict)
    receipt_metadata: Mapping[str, Any] = field(default_factory=dict)


class ScheduledGuard:
    """AutomationGuard implementation for one live attempt, not reusable next tick.

    facts(fresh_installation, boundary) -> (Observations, Controls) supplies S06's
    registered baseline/state/maintenance evaluation. This class independently
    rechecks ownership, daemon, controls, env/config/deployment, journal, latest
    receipt, failed target, signed exact pair and compatibility at each boundary.
    make_plan(fresh_installation, history) rebuilds the core plan. close() must run
    in finally to close the private backup directory descriptor.

    A transaction is bound to what the guard approved, not to a moment: the exact
    env bytes (approved_env_sha256, which the engine uses as the preimage of its
    recovery record and which before_rollout rechecks), the window and start budget
    re-derived at the guard's own clock sample taken after its last facts call, and,
    immediately before a rollout, the dependency images Compose will start
    (dependency_image_ids, verified again by the engine after the rollout).
    """
    def __init__(self, prepared: PreparedUpdate, *, discovery, store: RunnerStateStore,
                 facts: Callable, make_plan: Callable, control_generation: int,
                 window_occurrence: str, clock=lambda: datetime.now(timezone.utc), automatic=None):
        self.automatic = automatic
        self.recovery_required = False
        self.prepared = prepared
        self.discovery, self.store = discovery, store
        self.facts, self.make_plan = facts, make_plan
        self.control_generation = control_generation
        self.window_occurrence, self.clock = window_occurrence, clock
        self.attempt = None
        self.proof = None
        self.proof_directory = None
        self.proof_path = None
        self.first_mutation_at = None
        self.metadata = {}
        self.backup_environment = {}
        # The exact env bytes (sha256) the latest successful before_env_commit approved: the engine binds its
        # transaction to them (see Updater._approved_env_sha256), and before_rollout rechecks them.
        self.approved_env_sha256 = None
        # service -> image ids certified immediately before the latest rollout (see check_dependency_images).
        self.dependency_image_ids = None

    def context(self, engine):
        return EngineContext(self.prepared.binding, self.prepared, self.control_generation,
                             engine, self.backup_environment, self.metadata)

    def close(self):
        if self.proof_directory is not None:
            os.close(self.proof_directory)
            self.proof_directory = None

    def _refresh(self):
        result = self.discovery.refresh(self.prepared.manifest['version'],
                                        channel=self.prepared.manifest['channel'])
        require(result.complete and result.candidate is not None,
                result.reason_codes[0] if result.reason_codes else ReasonCode.MANIFEST_UNAVAILABLE)
        candidate = result.candidate
        require(candidate.manifest_sha256 == self.prepared.manifest_sha256
                and candidate.bundle_sha256 == self.prepared.bundle_sha256
                and candidate.manifest_hash == manifest_hash(plain(self.prepared.manifest)),
                ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        require(candidate.trust_fingerprint == self.prepared.trust_fingerprint, ReasonCode.SIGNATURE_REJECTED)
        self.prepared = replace(self.prepared, verification=candidate.verification)

    def _fresh(self, engine, boundary):
        fresh = engine.installation.fresh()
        check_binding(fresh, self.prepared.binding)
        check_deployment(fresh, self.prepared.manifest,
                         self.automatic.authorization if self.automatic is not None else None,
                         self.prepared.raw_manifest)
        # Normalize ONLY this engine's proven pin change for config comparison.
        # Non-pin edits, project/profile changes, drift and alias swaps still fail.
        record = engine.active_record
        if boundary == 'before_rollout' and record is not None:
            from .envfile import check_commit
            outcome = check_commit(engine._env_path(), record['env_recovery'], durable=True)
            require(outcome == 'accepted' or (outcome == 'absent' and
                    record['env_recovery']['before_sha256'] == record['env_recovery']['after_sha256']),
                    ReasonCode.ENV_CONFLICT)
            require(hashlib.sha256(engine._env_path().read_bytes()).hexdigest()
                    == record['env_recovery']['after_sha256'], ReasonCode.ENV_CONFLICT)
            # The recorded found_values describe only the bytes the guard approved: a transaction that began from
            # any other bytes (an edit adopted as its baseline) must not be normalized back into the baseline.
            require(self.approved_env_sha256 is not None
                    and record['env_recovery']['before_sha256'] == self.approved_env_sha256, ReasonCode.ENV_CONFLICT)
            fresh.overlay_env(record['found_values'])
        config, profile, deployment = fingerprints(fresh)
        require((config, profile, deployment) == (self.prepared.config_fingerprint,
                                                 self.prepared.profile_fingerprint,
                                                 self.prepared.deployment_fingerprint))
        return fresh

    def _controls_at(self, controls, now):
        """The window and start-budget facts, re-derived at `now` (the guard's own clock, sampled after its facts).

        The provider's booleans were computed before this sample, so they may only restrict. A schedule-less
        policy (injected controls) has nothing to re-evaluate. Fallback never calls this: an already-authorized
        safety fallback may finish after the window closes.
        """
        window = controls.policy.maintenance_window
        if window is None:
            return controls
        return replace(controls,
                       window_open=controls.window_open and occurrence(window, now) == self.window_occurrence,
                       start_budget_available=controls.start_budget_available
                       and start_gate(controls.policy, now) is None)

    def _expected_env(self, engine, boundary, seen):
        """The exact env sha256 this boundary approves, or None where fingerprints alone bind it.

        before_env_commit (forward): the bytes read before its final observation (`seen`). before_rollout: what
        the engine's own write left. Fallback boundaries: what the failed forward transaction left.
        """
        if boundary == 'before_rollout':
            return engine.active_record['env_recovery']['after_sha256']
        return seen if boundary == 'before_env_commit' else None

    def _pending(self, engine):
        latest = engine.state.latest_receipt()
        if latest and latest.get('status') == 'staged':
            raise GuardRefused(ReasonCode.MANUAL_STAGE_PRESENT)
        from .plan import unfinished
        if unfinished(engine._history()):
            raise GuardRefused(ReasonCode.RECOVERY_REQUIRED)
        state = self.store.read_state()
        target = TargetIdentity(self.prepared.manifest['version'], manifest_hash(plain(self.prepared.manifest)))
        require(target != state.failed_target_latch, ReasonCode.FAILED_TARGET_LATCHED)
        require(state.active_attempt_id is None or (self.attempt is not None
                and state.active_attempt_id == self.attempt.attempt_id), ReasonCode.RECOVERY_REQUIRED)

    def _certify_dependencies(self, engine, obs):
        """Automatic rollout only: the dependency images Compose will start are the ones the facts certified."""
        layout = topology_profile(self.prepared.binding.topology, self.prepared.binding.profiles)
        return check_dependency_images(engine.installation.fresh(), layout, obs.dependency_images)

    def _reserve_backup(self, engine, obs, controls):
        require(self.attempt is None, ReasonCode.RECOVERY_REQUIRED)
        target = TargetIdentity(self.prepared.manifest['version'], manifest_hash(plain(self.prepared.manifest)))
        # UTC proof timestamps have second precision; floor reservation, never
        # fabricate a later completion or reuse another attempt's filename.
        self.attempt = self.store.reserve_attempt(target, self.window_occurrence,
                                                 self.clock().replace(microsecond=0))
        layout = topology_profile(self.prepared.binding.topology, self.prepared.binding.profiles)
        from .compatibility import decode_document
        previous = decode_document(obs.predecessor_raw, 1024 * 1024)
        self.expectation = BackupExpectation(self.attempt.attempt_id,
                                            self.prepared.binding.installation_fingerprint,
                                            manifest_hash(plain(self.prepared.manifest)), previous['version'],
                                            self.attempt.reserved_at, layout.backup_domains)
        with engine.state.private_directory(create=True) as directory:
            name = 'backup-' + self.attempt.attempt_id
            os.mkdir(name, 0o700, dir_fd=directory)  # exclusive; never adopt an old proof
            self.proof_directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.fsync(directory)
        self.proof_path = engine.state.path / name / 'proof.json'
        self.backup_environment.update({
            'FUTURE_AGI_UPDATE_ATTEMPT_ID': self.attempt.attempt_id,
            'FUTURE_AGI_UPDATE_BACKUP_PROOF_FILE': str(self.proof_path),
            'FUTURE_AGI_UPDATE_INSTALLATION_FINGERPRINT': self.expectation.installation_fingerprint,
            'FUTURE_AGI_UPDATE_MANIFEST_HASH': self.expectation.target_manifest_hash,
        })
        if self.automatic is not None:
            from .automatic import BackupConfiguration
            from .recovery import RecoveryLedger
            require(engine.active_record is not None, ReasonCode.RECOVERY_REQUIRED)
            run_id = engine.active_record['run_id']
            # Retain signed bytes + source observation before the first stop.
            self.automatic.retain_attempt(self.attempt, run_id)
            configuration = BackupConfiguration.load(self.automatic.baseline.backup_state(self.automatic.head))
            ledger = RecoveryLedger.reserve(configuration.destination, self.expectation,
                operation_token=self.attempt.operation_token, run_id=run_id,
                binding_sha256=installation_binding_hash(self.prepared.binding),
                old_head=self.automatic.head.identity,
                source_observation_sha256=obs.runtime_observation.sha256)
            engine.automatic_ledger = self.automatic.ledger = ledger
            ledger.checkpoint(source_model=engine.installation.config(refresh=True), forward_record=engine.active_record)
            self.metadata['run_id'] = run_id
            self.metadata['recovery_strategy'] = 'restore_backup'
        self.metadata.update(schema_version=1, attempt_id=self.attempt.attempt_id,
                             manifest_sha256=self.prepared.manifest_sha256,
                             bundle_sha256=self.prepared.bundle_sha256,
                             manifest_hash=self.expectation.target_manifest_hash,
                             installation_fingerprint=self.expectation.installation_fingerprint,
                             config_fingerprint=self.prepared.config_fingerprint,
                             profile_fingerprint=self.prepared.profile_fingerprint,
                             trust_fingerprint=self.prepared.trust_fingerprint,
                             reserved_at=self.attempt.reserved_at.isoformat())

    @staticmethod
    def _journal(engine, boundary):
        journal = engine.state.read_journal()
        if boundary == 'before_rollout':
            record = engine.active_record or {}
            require(journal is not None and journal.get('stage') == 'pulled'
                    and all(journal.get(key) == record.get(key) for key in
                            ('run_id', 'kind', 'env_changes', 'env_recovery', 'project', 'env_file', 'production')),
                    ReasonCode.RECOVERY_REQUIRED)
        else:
            require(journal is None, ReasonCode.RECOVERY_REQUIRED)

    def validate(self, boundary, context):
        engine = context.engine
        require(context.binding == self.prepared.binding and context.control_generation == self.control_generation)
        require(not self.prepared.binding.production, ReasonCode.INITIALIZATION_REQUIRED)
        self.store.validate_binding(self.prepared.binding)
        control = self.store.read_control()
        require(control.generation == self.control_generation, ReasonCode.CONTROL_INVALID)
        if boundary == 'before_env_commit':
            self.approved_env_sha256 = None
        elif boundary == 'before_rollout':
            self.dependency_image_ids = None
        self._journal(engine, boundary)
        self._pending(engine)
        fresh = self._fresh(engine, boundary)
        obs, controls = self.facts(fresh, boundary)
        require(isinstance(obs, Observations) and isinstance(controls, Controls), ReasonCode.CONTROL_INVALID)
        require(controls.policy == engine.settings.automation_policy, ReasonCode.CONFIGURATION_CHANGED)
        require(control.effective(controls.policy, self.clock()) == 'enabled', ReasonCode.CONTROL_INVALID)
        self._refresh()
        obs = replace(obs, now=self.clock(), boundary='before_backup' if boundary == 'before_recovery' else boundary,
                      online_verified=True, owner_confirmed=True, no_pending_transaction=True,
                      installation_fingerprint=self.prepared.binding.installation_fingerprint,
                      config_fingerprint=self.prepared.config_fingerprint,
                      profile_fingerprint=self.prepared.profile_fingerprint,
                      trust_fingerprint=self.prepared.trust_fingerprint,
                      attempt_id=self.attempt.attempt_id if self.attempt else '',
                      reserved_at=self.attempt.reserved_at if self.attempt else None,
                      operation_token=self.attempt.operation_token if self.attempt else '',
                      run_id=engine.active_record['run_id'] if engine.active_record else '',
                      binding_sha256=installation_binding_hash(self.prepared.binding))
        controls = self._controls_at(controls, obs.now)
        # Rebuild from current disk/daemon, not Installation.config's old cache.
        plan = self.make_plan(inspection_installation(fresh), engine._history())
        require(plan.get('decision') == self.prepared.plan.get('decision')
                and plan.get('manifest_hash') == self.prepared.plan.get('manifest_hash')
                and not plan.get('resume') and not plan.get('reasons')
                and plan.get('env_changes') == plain(self.prepared.plan.get('env_changes')),
                ReasonCode.CONFIGURATION_CHANGED)
        proof = 'pending' if boundary in ('before_recovery', 'before_backup') else None
        if proof is None:
            require(self.proof_directory is not None, ReasonCode.BACKUP_REQUIRED)
            observed_proof = read_backup_proof(self.proof_directory, 'proof.json', self.expectation,
                                              now=obs.now, expected_uid=self.prepared.binding.host_uid,
                                              max_age_seconds=controls.policy.backup_max_age_seconds)
            require(self.proof is None or observed_proof.sha256 == self.proof.sha256,
                    ReasonCode.BACKUP_PROOF_INVALID)
            self.proof = observed_proof
            proof = self.proof.raw
            self.metadata['backup_proof'] = json.loads(proof)
        result = evaluate_automatic_apply(self.prepared, obs, controls, proof)
        require(result.allowed, result.reason_codes[0] if result.reason_codes else ReasonCode.UNCERTIFIED_RELEASE)
        # Slow online verification, plan building and proof IO cannot make an
        # earlier control/window/config observation authorize a later mutation.
        require(self.store.read_control().generation == self.control_generation, ReasonCode.CONTROL_INVALID)
        # The env bytes this final observation window starts from. Whatever the (possibly slow) facts call below
        # sees must still be exactly these bytes when it returns: the guard approves bytes, not a moment.
        try:
            seen_env = env_sha256(engine)
        except GuardRefused:
            seen_env = None  # the fresh model below reports the precise cause (retargeted alias, unreadable env)
        final_fresh = self._fresh(engine, boundary)
        final_obs, final_controls = self.facts(final_fresh, boundary)
        require(final_controls.policy == engine.settings.automation_policy, ReasonCode.CONFIGURATION_CHANGED)
        dependencies = self._certify_dependencies(engine, final_obs) if boundary == 'before_rollout' else None
        # Re-sampled AFTER the last facts call and dependency inspection; the window and start-budget facts are re-derived,
        # so no boolean computed earlier (before a slow observation) can authorize this boundary.
        final_obs = replace(final_obs, now=self.clock(), boundary=obs.boundary,
                            online_verified=True, owner_confirmed=True, no_pending_transaction=True,
                            installation_fingerprint=obs.installation_fingerprint,
                            config_fingerprint=obs.config_fingerprint, profile_fingerprint=obs.profile_fingerprint,
                            trust_fingerprint=obs.trust_fingerprint, attempt_id=obs.attempt_id,
                            reserved_at=obs.reserved_at, operation_token=obs.operation_token, run_id=obs.run_id,
                            binding_sha256=obs.binding_sha256)
        final_controls = self._controls_at(final_controls, final_obs.now)
        final_result = evaluate_automatic_apply(self.prepared, final_obs, final_controls, proof)
        require(final_result.allowed, final_result.reason_codes[0] if final_result.reason_codes
                else ReasonCode.UNCERTIFIED_RELEASE)
        self._journal(engine, boundary)
        self._pending(engine)
        require(self.store.read_control().generation == self.control_generation, ReasonCode.CONTROL_INVALID)
        expected_env = self._expected_env(engine, boundary, seen_env)
        require(expected_env is not None or boundary != 'before_env_commit', ReasonCode.ENV_CONFLICT)
        if expected_env is not None:
            require(env_sha256(engine) == expected_env, ReasonCode.ENV_CONFLICT)
        if boundary == 'before_rollout':
            self.dependency_image_ids = dependencies
        if boundary == 'before_env_commit':
            self.approved_env_sha256 = expected_env
            self.first_mutation_at = final_obs.now
            self.metadata['run_id'] = engine.active_record['run_id']
            self.metadata['first_mutation_at'] = final_obs.now.isoformat()
            if self.automatic is not None:
                self._certify_dependencies(engine, final_obs)
                self.automatic.prepare(self, engine)
                # Prediction and signature checks can be slow. They must not
                # consume a window/control budget and still authorize mutation.
                _, controls = self.facts(engine.installation.fresh(), boundary)
                controls = self._controls_at(controls, self.clock())
                require(controls.enabled and not controls.paused and controls.window_open,
                        ReasonCode.OUTSIDE_WINDOW)
                require(controls.start_budget_available, ReasonCode.INSUFFICIENT_WINDOW)
                require(self.store.read_control().generation == self.control_generation, ReasonCode.CONTROL_INVALID)
                require(env_sha256(engine) == expected_env, ReasonCode.ENV_CONFLICT)
        if boundary == 'before_backup':
            # Reserve the LAST healthy source observation, not the earlier
            # sample that the final facts call replaced. Its timestamp/digest
            # must remain identical in AttemptIntent, ledger and cold backup.
            self._reserve_backup(engine, final_obs, final_controls)

    def verify_success(self, engine):
        if self.automatic is not None:
            self.automatic.verify_success(engine)

    def complete_success(self, engine, receipt):
        if self.automatic is not None:
            self.automatic.complete_success(engine, receipt)



@dataclass(frozen=True)
class EngineResult:
    """Private runner result. Never serialize this wholesale into public status."""
    code: int
    reason_codes: Tuple[ReasonCode, ...]
    transaction_started: bool
    rollout_started: bool
    forward_result: str
    fallback_result: str = 'none'
    receipt: Optional[Path] = None
    restore_result: str = 'not_needed'
    effective_version: Optional[str] = None
    phase: Optional[str] = None


class SupervisedReadiness:
    """Bound standalone env-configured waits by the scheduled policy as well."""
    def __init__(self, installation, supervisor, out):
        self.installation, self.supervisor, self.out = installation, supervisor, out

    def _wait(self, method, **kwargs):
        import time
        from .readiness import NotReady, Readiness, http_ok
        deadline = min(self.supervisor.deadline, time.monotonic() + self.supervisor.policy.wait_timeout_seconds)

        def clock():
            now = time.monotonic()
            if now >= deadline:
                raise NotReady(ReasonCode.READINESS_FAILED.value)
            return now

        def sleep(seconds):
            time.sleep(min(seconds, max(0, deadline - clock())))
            clock()

        def probe(url):
            return http_ok(url, timeout=min(3.0, max(0.001, deadline - clock())))

        readiness = Readiness(self.installation, self.out, probe=probe, clock=clock, sleep=sleep)
        return getattr(readiness, method)(**kwargs)

    def wait_standalone(self, previous_ids=()):
        return self._wait('wait_standalone', previous_ids=previous_ids)

    def wait_api(self):
        return self._wait('wait_api')


def guarded_updater(installation, settings, state, guard, supervisor, *, readiness=None, out=None):
    """Scheduled entry point: bounded Docker AND hook runner, no ambient output.

    S06 supplies the registered ProcessSupervisor and holds runner.lock. Explicit
    manual commands continue constructing Updater without a guard as before.
    """
    import io
    from .processes import ProcessSupervisor
    require(isinstance(supervisor, ProcessSupervisor) and installation.runner is supervisor,
            ReasonCode.HOST_VERIFIER_REQUIRED)
    require(settings.automation_policy == supervisor.policy, ReasonCode.CONFIGURATION_CHANGED)
    require(supervisor.policy.mode == 'apply', ReasonCode.CONTROL_INVALID)
    from .settings import DEFAULTS, load_settings
    current = load_settings(installation.root, environ=supervisor.environment)
    require(all(current.get(key) == settings.get(key) for key in DEFAULTS), ReasonCode.CONFIGURATION_CHANGED)
    out = out if out is not None else io.StringIO()
    return Updater(installation, settings, state, out=out,
                   readiness=readiness or SupervisedReadiness(installation, supervisor, out), hook_runner=supervisor,
                   automation_guard=guard, guard_context=guard.context,
                   built_in_backup=guard.automatic.backup(installation) if guard.automatic is not None else None)


def execute_guarded(engine, guard) -> EngineResult:
    """Catch service-manager signals during backup too, not just env/rollout."""
    from .apply import signals_as_interrupt
    with signals_as_interrupt():
        return _execute_guarded(engine, guard)


def _execute_guarded(engine, guard) -> EngineResult:
    """Project the sole engine's outcome; never perform automatic recovery here.

    The enrolled transaction and its baseline callbacks run inside update.lock.
    Only the legacy non-generated adapter still finalizes its own reservation.
    Reporting is outside this function and cannot reverse a success decision.
    """
    from .apply import Interrupted, InterruptedRun
    from .receipt import StateBusy
    from .processes import ProcessError
    require(engine.automation_guard is guard, ReasonCode.CONTROL_INVALID)
    path, fallback = None, 'none'
    forward_rollout_started = False
    code, reasons, forward = 0, (), 'succeeded'
    try:
        path = engine.apply(lambda history: guard.make_plan(engine.installation, history), guard.prepared.manifest)
        if path is None:
            forward = 'current'
    except StateBusy:
        return EngineResult(0, (), False, False, 'busy')
    except (Exception, Interrupted, KeyboardInterrupt) as exc:
        code = 130 if isinstance(exc, (Interrupted, KeyboardInterrupt, InterruptedRun)) else getattr(exc, 'exit_code', 2)
        forward_rollout_started = (engine.active_record or {}).get('stage') == 'rollout'
        reason = ReasonCode.OPERATION_INTERRUPTED if code == 130 else ReasonCode.PROCESS_FAILED
        cause, seen = exc, set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            if hasattr(cause, 'reason_code'):
                try:
                    reason = ReasonCode(cause.reason_code)
                except ValueError:
                    pass
                break
            from .readiness import NotReady
            if isinstance(cause, NotReady):
                reason = ReasonCode.READINESS_FAILED
                break
            cause = cause.__cause__
        reasons = (reason,)
        forward = 'interrupted' if code == 130 else 'failed'
        if isinstance(exc, ProcessError):
            reasons = (exc.reason_code,)
    finally:
        guard.close()
    record = engine.active_record or {}
    transaction_started = guard.first_mutation_at is not None
    rollout_started = forward_rollout_started or record.get('stage') == 'rollout'
    if getattr(guard, 'automatic', None) is not None:
        # Projection only. All backup, env, runtime, restore and metadata actions
        # have already run INSIDE Updater's lock. Never mutate runner state here.
        outcome = getattr(engine, 'automatic_outcome', None)
        ledger = getattr(engine, 'automatic_ledger', None)
        phase = ledger.phase if ledger is not None else None
        receipt = getattr(engine, 'automatic_receipt', None) or path
        if outcome == 'restored':
            return EngineResult(code or 5, reasons + (ReasonCode.RESTORED_AFTER_UPDATE_FAILURE,),
                True, rollout_started, forward, 'none', receipt, 'verified',
                guard.automatic.head.release.version, phase)
        if outcome == 'succeeded':
            return EngineResult(0, (), True, True, 'succeeded', 'none', receipt, 'not_needed',
                guard.prepared.manifest['version'], phase)
        if outcome == 'failed_available':
            require(ledger is not None and ledger.phase == 'failed_available'
                    and ledger.backup_seal is None and ledger.snapshot['pending'] is None
                    and guard.store.read_state().active_attempt_id is None, ReasonCode.RECOVERY_REQUIRED)
            return EngineResult(code or 5, reasons, transaction_started, rollout_started, forward,
                'none', receipt, 'not_needed', guard.automatic.head.release.version, phase)
        if ledger is not None or guard.attempt is not None:
            # An unresolved success decision is never relabeled a forward
            # failure; it needs metadata reconciliation, not restoration.
            accepted = ledger is not None and ledger.direction == 'success_decided'
            return EngineResult(3, (ReasonCode.RECOVERY_REQUIRED,), True, rollout_started,
                'succeeded' if accepted else forward, 'none', receipt,
                'not_needed' if accepted else 'recovery_required', None, phase)
        return EngineResult(code, reasons, transaction_started, rollout_started, forward, receipt=receipt)
    if guard.attempt is not None and not (getattr(guard, 'automatic', None) is not None and code == 0):
        terminal = 'succeeded' if code == 0 else 'interrupted' if code == 130 else 'failed'
        try:
            guard.store.finish_attempt(replace(guard.attempt, outcome=terminal, finished_at=guard.clock(),
                backup_proof_identity=guard.proof.sha256 if guard.proof else None,
                receipt_identity=hashlib.sha256(path.read_bytes()).hexdigest() if path else None))
        except Exception:
            # Keep the reserved attempt as recovery evidence, don't invent a
            # terminal success or retry after state persistence failed.
            return EngineResult(2, (ReasonCode.RECOVERY_REQUIRED,), transaction_started, rollout_started,
                                forward, fallback, path)
    return EngineResult(code, reasons, transaction_started, rollout_started, forward, fallback, path)
