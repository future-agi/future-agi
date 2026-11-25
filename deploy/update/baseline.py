"""Machine-owned automatic baseline and recoverable, METADATA-ONLY succession.

Caller holds runner.lock -> StateDir.lock(automation=True) throughout observation,
prepare, rollout and commit/reconcile. Only short writes take existing state.lock;
verification/Compose/probes must never run inside that metadata lock. locked() is
provided for enrollment/tests; an engine already holding those locks calls the
methods directly, exactly as for RunnerStateStore.reserve_attempt.

This core does NOT activate a scheduler, probe databases, mutate env, back up,
pull or recreate. Injected typed observations are results of a fixed host adapter,
not user consent. prepare_success runs BEFORE mutation, after the real backup;
commit/reconcile require the engine's retained success receipt and fresh probes.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional, Tuple

from .backup_proof import BackupExpectation, validate_backup_proof
from .compatibility import (Observations, _images, authorize_execution, decode_document,
                            timestamp, topology_profile, validate_release_proof)
from .contracts import (BackupSeal, ColdHold, RuntimeObservationV2, ReasonCode, VerificationSnapshot,
                        installation_binding_hash, _freeze)
from .discovery import DiscoveredRelease
from .envfile import render_changes
from .envcommit import CommitError
from .install import COMPONENT_VARIABLES
from .manifest import canonical_json, manifest_hash, parse_version, validate_manifest
from .receipt import StateDir
from .runner_state import (AttemptRecord, RunnerStateStore, StaleGeneration, StoreError, TargetIdentity,
                           _counter, _hash, _increment, _require, _token, _wire)
from .trust import FetchedManifest

MAX_EVIDENCE_BYTES = 4 * 1024 * 1024


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require(ok: bool, reason=ReasonCode.RECOVERY_REQUIRED) -> None:
    if not ok:
        raise StoreError(reason)


def identity(value) -> str:
    return manifest_hash(_wire(value))


@dataclass(frozen=True)
class ReleaseReference:
    version: str
    manifest_sha256: str
    bundle_sha256: str
    proof_sha256: Optional[str]
    manifest_hash: str
    trust_fingerprint: str
    repository: str
    source_identity_sha256: str

    def __post_init__(self):
        parse_version(self.version)
        for value in (self.manifest_sha256, self.bundle_sha256, self.manifest_hash,
                      self.trust_fingerprint, self.source_identity_sha256):
            _hash(value)
        if self.proof_sha256 is not None:
            _hash(self.proof_sha256)
        _require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) is not None)


@dataclass(frozen=True)
class BaselineHead:
    schema_version: int
    generation: int
    installation_fingerprint: str
    release: ReleaseReference
    execution_deployment: str  # Immutable JSON blob {files: exact physical hashes}.
    config_fingerprint: str
    profile_fingerprint: str
    env_sha256: str
    structure_sha256: str
    previous_head: Optional[str]
    attempt_id: Optional[str]
    run_id: Optional[str]

    def __post_init__(self):
        _require(type(self.schema_version) is int and self.schema_version == 1)
        _counter(self.generation)
        _require(isinstance(self.release, ReleaseReference))
        for value in (self.installation_fingerprint, self.execution_deployment, self.config_fingerprint,
                      self.profile_fingerprint, self.env_sha256, self.structure_sha256):
            _hash(value)
        if self.previous_head is not None:
            _hash(self.previous_head)
        for value in (self.attempt_id, self.run_id):
            if value is not None:
                _token(value)
        _require((self.generation == 0) == (self.previous_head is None))
        _require((self.previous_head is None) == (self.attempt_id is None) == (self.run_id is None))

    @property
    def identity(self):
        return identity(self)


@dataclass(frozen=True)
class _BackupLink:
    """Immutable generation sidecar, written BEFORE publishing its head."""
    head_identity: str
    state_sha256: str

    def __post_init__(self):
        _hash(self.head_identity)
        _hash(self.state_sha256)


@dataclass(frozen=True)
class Pin:
    name: str
    value: str

    def __post_init__(self):
        _require(self.name in COMPONENT_VARIABLES.values())
        parse_version(self.value)


@dataclass(frozen=True)
class SuccessIntent:
    schema_version: int
    old_head: str
    attempt: AttemptRecord
    run_id: str
    target: ReleaseReference
    pins: Tuple[Pin, ...]
    expected_env_sha256: str
    expected_config_fingerprint: str
    backup_sha256: str
    prepared_at: datetime
    source_state_sha256: Optional[str] = None
    backup_seal_sha256: Optional[str] = None

    def __post_init__(self):
        _require(type(self.schema_version) is int and self.schema_version == 1)
        for value in (self.old_head, self.expected_env_sha256, self.expected_config_fingerprint, self.backup_sha256):
            _hash(value)
        _require(isinstance(self.attempt, AttemptRecord) and self.attempt.outcome == "reserved")
        _token(self.run_id)
        _require(isinstance(self.target, ReleaseReference) and self.target.proof_sha256 is not None)
        _require(self.attempt.target == TargetIdentity(self.target.version, self.target.manifest_hash))
        _require(type(self.pins) is tuple and bool(self.pins) and all(isinstance(p, Pin) for p in self.pins))
        _require(len({p.name for p in self.pins}) == len(self.pins) and all(p.value == self.target.version for p in self.pins))
        _require(self.prepared_at.tzinfo is not None and self.prepared_at >= self.attempt.reserved_at)
        for value in (self.source_state_sha256, self.backup_seal_sha256):
            if value is not None:
                _hash(value)


@dataclass(frozen=True)
class AutomaticAttemptIntent:
    old_head: str
    attempt: AttemptRecord
    run_id: str
    target: ReleaseReference
    observation_sha256: str
    env_sha256: str

    def __post_init__(self):
        for value in (self.old_head, self.observation_sha256, self.env_sha256):
            _hash(value)
        _token(self.run_id)
        _require(isinstance(self.attempt, AttemptRecord) and self.attempt.outcome == 'reserved')
        _require(isinstance(self.target, ReleaseReference)
                 and self.attempt.target == TargetIdentity(self.target.version, self.target.manifest_hash))


@dataclass(frozen=True)
class RestoreIntent:
    old_head: str
    attempt: AttemptRecord
    run_id: str
    backup_sha256: str
    backup_seal_sha256: str
    source_state_sha256: str
    env_inverse_sha256: str

    def __post_init__(self):
        for value in (self.old_head, self.backup_sha256, self.backup_seal_sha256,
                      self.source_state_sha256, self.env_inverse_sha256):
            _hash(value)
        _require(isinstance(self.attempt, AttemptRecord) and self.attempt.outcome == 'reserved')
        _token(self.run_id)


@dataclass(frozen=True)
class RestoreSeal:
    intent_sha256: str
    source_head: str
    receipt_name: str
    terminal: AttemptRecord
    observation_sha256: str

    def __post_init__(self):
        for value in (self.intent_sha256, self.source_head, self.observation_sha256):
            _hash(value)
        _token(self.receipt_name)
        _require(isinstance(self.terminal, AttemptRecord) and self.terminal.outcome == 'restored'
                 and self.terminal.backup_proof_identity is not None and self.terminal.receipt_identity is not None)


@dataclass(frozen=True)
class FailedAvailableSeal:
    intent_sha256: str
    observation_sha256: str
    env_sha256: str
    terminal: AttemptRecord

    def __post_init__(self):
        for value in (self.intent_sha256, self.observation_sha256, self.env_sha256):
            _hash(value)
        _require(isinstance(self.terminal, AttemptRecord) and self.terminal.outcome == 'failed_available')


@dataclass(frozen=True)
class AcceptedSuccess:
    """Immutable pre-decision observation and successor sidecar, never live IO."""
    intent_sha256: str
    observation_sha256: str
    env_sha256: str
    backup_state_sha256: Optional[str]

    def __post_init__(self):
        for value in (self.intent_sha256, self.observation_sha256, self.env_sha256):
            _hash(value)
        if self.backup_state_sha256 is not None:
            _hash(self.backup_state_sha256)


@dataclass(frozen=True)
class SuccessSeal:
    intent_sha256: str
    successor_head: str
    receipt_name: str
    terminal: AttemptRecord

    def __post_init__(self):
        _hash(self.intent_sha256)
        _hash(self.successor_head)
        _token(self.receipt_name)
        _require(isinstance(self.terminal, AttemptRecord) and self.terminal.outcome == "succeeded")


@dataclass(frozen=True, repr=False)
class StructuralObservation:
    """Fixed host observations; no state approval inferred from enrollment.

    Runtime-v2 separates physical installation identity from release-specific
    migration inventory/applied-set state. Business rows, observation timestamps
    and recreated container IDs are not baseline identity. Legacy formats/epochs
    remain readable only for existing structural-v1 callers, never v2 admission.
    The fixed host StandaloneObservation envelope normalises to this value.
    """
    facts: Observations
    env_bytes: bytes
    formats: Mapping[str, str]
    volumes: Mapping[str, str]
    probe_contract: str
    ready: bool

    def __post_init__(self):
        _require(isinstance(self.facts, Observations) and type(self.env_bytes) is bytes)
        _require(len(self.env_bytes) <= 1024 * 1024 and type(self.ready) is bool)
        _require(self.probe_contract in ('standalone-structure-v1', 'standalone-runtime-v2'))
        _require(self.probe_contract != 'standalone-runtime-v2'
                 or isinstance(self.facts.runtime_observation, RuntimeObservationV2))
        for mapping in (self.formats, self.volumes):
            _require(isinstance(mapping, Mapping) and (bool(mapping) or mapping is self.formats
                                                     and self.probe_contract == 'standalone-runtime-v2'))
            for key, value in mapping.items():
                _token(key)
                _hash(value)
        object.__setattr__(self, "formats", _freeze(self.formats))
        object.__setattr__(self, "volumes", _freeze(self.volumes))
        values = {name: _freeze(getattr(self.facts, name)) for name in (
            "state_epochs", "persistence", "deployment_files", "env")}
        # RuntimeImage values are frozen, but the maps/lists that hold them aren't.
        from types import MappingProxyType
        for name in ("product_images", "dependency_images"):
            values[name] = MappingProxyType({s: tuple(v) for s, v in getattr(self.facts, name).items()})
        object.__setattr__(self, "facts", replace(self.facts, **values))

    @property
    def structure_sha256(self):
        obs = self.facts
        dependencies = {s: sorted([dict(image=i.image, digest=i.digest, image_id=i.image_id) for i in replicas],
                                   key=lambda i: (i["image"], i["digest"], i["image_id"]))
                        for s, replicas in obs.dependency_images.items()}
        if self.probe_contract == 'standalone-runtime-v2':
            runtime = obs.runtime_observation
            return manifest_hash({'installation': self.installation_sha256,
                'migration_inventory': runtime.migration_inventory_sha256,
                'applied_migrations': runtime.applied_migrations_sha256,
                'active_app_mapping': runtime.active_app_mapping_sha256})
        return manifest_hash({"probe": self.probe_contract, "platform": obs.platform,
                              "formats": dict(self.formats), "volumes": dict(self.volumes),
                              "epochs": dict(obs.state_epochs), "persistence": dict(obs.persistence),
                              "dependencies": dependencies})

    @property
    def installation_sha256(self):
        """Cross-release invariants, deliberately excluding migration/runtime state."""
        obs = self.facts
        dependencies = {s: sorted([dict(image=i.image, digest=i.digest, image_id=i.image_id) for i in replicas],
                                   key=lambda i: (i['image'], i['digest'], i['image_id']))
                        for s, replicas in obs.dependency_images.items()}
        runtime = obs.runtime_observation
        return manifest_hash({'probe': self.probe_contract, 'platform': obs.platform,
            'volumes': dict(self.volumes), 'persistence': dict(obs.persistence), 'dependencies': dependencies,
            'storage': runtime.storage_sha256 if runtime is not None else None})

    @property
    def retained_state(self):
        return canonical_json({'installation_sha256': self.installation_sha256,
            'structure_sha256': self.structure_sha256,
            'runtime': _wire(self.facts.runtime_observation), 'env_sha256': sha(self.env_bytes)})


def normalise_observation(observation):
    """Adapt only the fixed host envelope, never arbitrary duck-typed facts."""
    if isinstance(observation, StructuralObservation):
        return observation
    from .standalone_state import StandaloneObservation
    require(isinstance(observation, StandaloneObservation), ReasonCode.INITIALIZATION_REQUIRED)
    return StructuralObservation(observation.facts, observation.env_bytes, {}, observation.volumes,
                                 observation.probe_contract, observation.ready)


class BaselineStore:
    def __init__(self, store: RunnerStateStore, *, verifier=None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.store, self.verifier, self.clock = store, verifier, clock
        self.engine_state = StateDir(store.checkout)
        from .host_facts import safe_path
        safe_path(store.checkout, directory=True)

    @contextmanager
    def locked(self):
        with self.store.runner_lock(), self.engine_state.lock(automation=True):
            yield self

    def _record(self, child, name, kind):
        try:
            with self.store._directory(child=child) as directory:
                return self.store._read(directory, name, kind)
        except FileNotFoundError:
            raise StoreError(ReasonCode.RECOVERY_REQUIRED) from None

    def _immutable(self, child, name, value):
        # All callers hold state.lock. Exact replay is idempotent; conflict refuses.
        with self.store._directory(create=True, child=child) as directory:
            try:
                old = self.store._read(directory, name, type(value))
            except FileNotFoundError:
                self.store._write(directory, name, value)
            else:
                require(old == value, ReasonCode.IMMUTABLE_RELEASE_CHANGED)

    def _blob(self, raw):
        key = sha(raw)
        with self.store._directory(create=True, child="evidence") as directory:
            try:
                old = self.store._read_raw(directory, key, MAX_EVIDENCE_BYTES)
            except FileNotFoundError:
                self.store._write_raw(directory, key, raw)
            else:
                require(old == raw, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        return key

    def blob(self, key: str) -> bytes:
        _hash(key)
        try:
            with self.store._directory(child="evidence") as directory:
                raw = self.store._read_raw(directory, key, MAX_EVIDENCE_BYTES)
            require(sha(raw) == key, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
            return raw
        except FileNotFoundError:
            raise StoreError(ReasonCode.RECOVERY_REQUIRED) from None

    def _head(self, key):
        _hash(key)
        head = self._record("baselines", key + ".json", BaselineHead)
        require(head.identity == key and head.installation_fingerprint ==
                self.store.read_registration().binding.installation_fingerprint)
        return head

    def current(self) -> BaselineHead:
        """Read closed private metadata, NOT a signature-verification assertion."""
        key = self.store.read_state().baseline_head
        require(key is not None, ReasonCode.INITIALIZATION_REQUIRED)
        return self._head(key)

    def backup_state(self, head=None) -> Optional[bytes]:
        """Read a generation's immutable backup configuration, never a mutable latest file."""
        head = head or self.current()
        with self.store._directory(child="baselines") as directory:
            try:
                link = self.store._read(directory, head.identity + "-backup.json", _BackupLink)
            except FileNotFoundError:
                return None  # Older core-only enrollments cannot run the automatic backend.
        require(link.head_identity == head.identity)
        return self.blob(link.state_sha256)

    def _link_backup(self, head, raw):
        require(type(raw) is bytes and 0 < len(raw) <= MAX_EVIDENCE_BYTES)
        self._immutable("baselines", head.identity + "-backup.json", _BackupLink(head.identity, self._blob(raw)))

    def _verify(self, release: DiscoveredRelease) -> DiscoveredRelease:
        require(self.verifier is not None and self.verifier.host_only is True, ReasonCode.HOST_VERIFIER_REQUIRED)
        require(isinstance(release, DiscoveredRelease), ReasonCode.SIGNATURE_REJECTED)
        manifest = validate_manifest(decode_document(release.raw_manifest, 1024 * 1024))
        require(manifest_hash(manifest) == release.manifest_hash, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        fingerprint = self.verifier.trust_fingerprint()
        require(fingerprint == release.trust_fingerprint, ReasonCode.SIGNATURE_REJECTED)
        # Verify a new immutable private copy. Never let a previous trusted-key.pub
        # or public status substitute for the currently configured verifier anchor.
        with self.store._directory():
            pass  # Qualify private ancestry before creating a verifier workspace.
        with tempfile.TemporaryDirectory(prefix="verify-baseline-", dir=str(self.store.path)) as tmp:
            directory = Path(tmp)
            m, b = directory / "manifest.json", directory / "bundle.json"
            for path, raw in ((m, release.raw_manifest), (b, release.raw_bundle)):
                require(len(raw) <= MAX_EVIDENCE_BYTES, ReasonCode.SIGNATURE_REJECTED)
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as output:
                    output.write(raw)
            fetched = FetchedManifest("retained-private-evidence", directory, m, release.raw_manifest, manifest,
                                      bundle_path=b)
            verified = self.verifier.verify(fetched)
            require(verified.verified and m.read_bytes() == release.raw_manifest and b.read_bytes() == release.raw_bundle
                    and self.verifier.trust_fingerprint(fetched) == fingerprint
                    and self.verifier.trust_fingerprint() == fingerprint, ReasonCode.SIGNATURE_REJECTED)
        if release.release_proof:
            validate_release_proof(release.release_proof, manifest, repository=release.repository)
        return replace(release, verification=VerificationSnapshot(
            True, verified.method, verified.signer, verified.issuer, verified.tool, self.clock(), verified.public_key_sha256))

    def evidence(self, reference: ReleaseReference) -> DiscoveredRelease:
        """Reverify retained raw pair under the current explicit host anchor."""
        release = DiscoveredRelease(self.blob(reference.manifest_sha256), self.blob(reference.bundle_sha256),
            reference.manifest_hash, reference.trust_fingerprint,
            VerificationSnapshot(False, "", "", "", "", self.clock()),
            b"" if reference.proof_sha256 is None else self.blob(reference.proof_sha256), (),
            reference.repository, reference.source_identity_sha256)
        require(release.version == reference.version, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        return self._verify(release)

    def _retain(self, release):
        return ReleaseReference(release.version, self._blob(release.raw_manifest), self._blob(release.raw_bundle),
                                self._blob(release.release_proof) if release.release_proof else None,
                                release.manifest_hash, release.trust_fingerprint, release.repository,
                                release.source_identity_sha256)

    def _env_bytes(self) -> bytes:
        """Observe actual selected bytes without following a replaced leaf or FIFO."""
        binding = self.store.read_registration().binding
        from .host_facts import safe_path
        safe_path(binding.env_target)
        try:
            fd = os.open(str(binding.env_target), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            with os.fdopen(fd, "rb") as handle:
                before = os.fstat(handle.fileno())
                from .envcommit import _known_links
                require(stat.S_ISREG(before.st_mode) and before.st_uid == binding.host_uid
                        and _known_links(binding.env_target, before)
                        and not before.st_mode & 0o022 and before.st_size <= 1024 * 1024, ReasonCode.ENV_CONFLICT)
                raw = handle.read(1024 * 1024 + 1)
                after = os.fstat(handle.fileno())
                fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_nlink")
                require(len(raw) <= 1024 * 1024 and all(getattr(before, k) == getattr(after, k) for k in fields),
                        ReasonCode.ENV_CONFLICT)
                from .envcommit import qualify_retained_env
                if before.st_nlink > 1:
                    qualify_retained_env(binding.env_target, handle.fileno(), before, raw, maximum=1024 * 1024)
            return raw
        except (OSError, CommitError):
            raise StoreError(ReasonCode.ENV_CONFLICT) from None

    def _observe(self, observation: StructuralObservation, release: DiscoveredRelease, *, cold_hold=None,
                 accepted=False):
        observation = normalise_observation(observation)
        binding = self.store.read_registration().binding
        obs = observation.facts
        if cold_hold is not None:
            self.validate_hold(observation, cold_hold)
        elif not accepted:
            require(self._env_bytes() == observation.env_bytes, ReasonCode.CONFIGURATION_CHANGED)
        require(binding.topology == "standalone" and not binding.production, ReasonCode.UNSUPPORTED_INSTALLATION)
        layout = topology_profile(binding.topology, binding.profiles)
        age = (self.clock() - obs.now).total_seconds()
        require(0 <= age and (accepted or cold_hold is not None or age <= 300), ReasonCode.CLOCK_SKEW)
        require(obs.owner_confirmed is True and obs.installation_fingerprint == binding.installation_fingerprint,
                ReasonCode.OWNERSHIP_UNCONFIRMED)
        require(obs.topology_qualified is True and obs.no_source_build is True, ReasonCode.UNSUPPORTED_INSTALLATION)
        require(obs.runtime_complete is True and observation.ready, ReasonCode.RUNNING_VERSIONS_UNKNOWN)
        require(set(observation.volumes) == set(layout.backup_domains), ReasonCode.UNSUPPORTED_INSTALLATION)
        if observation.probe_contract == 'standalone-runtime-v2':
            runtime = obs.runtime_observation
            require(runtime.release_version == release.version and runtime.manifest_sha256 == release.manifest_sha256
                    and runtime.binding_sha256 == installation_binding_hash(binding)
                    and runtime.platform == obs.platform and runtime.ready and runtime.stable
                    and runtime.observed_at == obs.now
                    and dict(runtime.product_images) == dict(obs.product_images)
                    and dict(runtime.dependency_images) == dict(obs.dependency_images)
                    and runtime.env_sha256 == sha(observation.env_bytes)
                    and runtime.config_sha256 == obs.config_fingerprint
                    and runtime.deployment_sha256 == manifest_hash({'files': dict(obs.deployment_files)}),
                    ReasonCode.MIGRATION_INVENTORY_MISMATCH)
            require(all(p in ('unless-stopped', 'no') for p in runtime.restart_policies.values()),
                    ReasonCode.UNSUPPORTED_INSTALLATION)
        else:
            require(obs.no_pending_migrations is True and set(observation.formats) == set(layout.domains)
                    and set(obs.state_epochs) == set(layout.domains)
                    and all(type(v) is int and 0 <= v <= 2147483647 for v in obs.state_epochs.values()),
                    ReasonCode.INITIALIZATION_REQUIRED)
        require(dict(obs.persistence) == dict(layout.persistence) and all(type(v) is bool for v in obs.persistence.values()),
                ReasonCode.UNSUPPORTED_INSTALLATION)
        require(obs.platform in ("linux/amd64", "linux/arm64") and all(
            obs.platform in release.manifest["components"][component].get("platforms", ())
            for component in layout.active_components), ReasonCode.UNSUPPORTED_INSTALLATION)
        require(obs.profile_fingerprint == manifest_hash(sorted(binding.profiles)), ReasonCode.CONFIGURATION_CHANGED)
        for value in (obs.config_fingerprint, obs.profile_fingerprint):
            _hash(value)
        require(obs.trust_fingerprint == release.trust_fingerprint, ReasonCode.SIGNATURE_REJECTED)
        _images(obs.product_images, {service: release.manifest["components"][component] for service, component in layout.products})
        require(set(obs.dependency_images) == set(layout.dependencies), ReasonCode.IMAGE_DIGEST_MISMATCH)
        # Initial dependency identity is OBSERVED, not certified by enrollment.
        # A target proof must subsequently name these exact dependencies.
        _images(obs.dependency_images, {s: {"image": replicas[0].image, "digest": replicas[0].digest}
                                       for s, replicas in obs.dependency_images.items() if replicas})
        return binding, layout

    def enroll(self, current_release: DiscoveredRelease, observation: StructuralObservation, *,
               backup_state: Optional[bytes] = None) -> BaselineHead:
        """One-time fixed observations + verified current release, never consent facts."""
        observation = normalise_observation(observation)
        current_release = self._verify(current_release)
        binding, _ = self._observe(observation, current_release)
        require(observation.facts.no_pending_transaction is True)
        with self.engine_state.private_directory() as directory:
            require("journal.json" not in os.listdir(directory))
        require(dict(observation.facts.deployment_files) == current_release.manifest["deployment"]["files"],
                ReasonCode.DEPLOYMENT_FILES_MISMATCH)
        with self.store._lock("state.lock") as directory:
            state = self.store.read_state()
            require(state.active_attempt_id is None)
            reference = self._retain(current_release)
            execution = self._blob(canonical_json({"files": dict(observation.facts.deployment_files)}))
            head = BaselineHead(1, 0, binding.installation_fingerprint, reference, execution,
                                observation.facts.config_fingerprint, observation.facts.profile_fingerprint,
                                sha(observation.env_bytes), observation.structure_sha256, None, None, None)
            if state.baseline_head is not None:
                require(self.current() == head and self.backup_state(head) == backup_state,
                        ReasonCode.CONFIGURATION_CHANGED)
                return head
            if backup_state is not None:
                self._link_backup(head, backup_state)
            self._immutable("baselines", head.identity + ".json", head)
            self.store._write(directory, "state.json", replace(state, generation=_increment(state.generation),
                                                              baseline_head=head.identity))
            return head

    def validate_hold(self, observation, hold):
        """Validate linkage; the fixed backup adapter must independently verify stopped writers."""
        require(isinstance(hold, ColdHold) and isinstance(observation.facts.runtime_observation, RuntimeObservationV2),
                ReasonCode.BACKUP_PROOF_INVALID)
        runtime = observation.facts.runtime_observation
        binding = self.store.read_registration().binding
        require(hold.binding_sha256 == installation_binding_hash(binding)
                and hold.source_observation_sha256 == runtime.sha256
                and hold.daemon_identity == binding.daemon_identity and hold.storage_sha256 == runtime.storage_sha256
                and dict(hold.restart_counts) == dict(runtime.restart_counts)
                and dict(hold.restart_policies) == dict(runtime.restart_policies)
                and set(hold.stopped_container_ids) == set(runtime.restart_counts)
                and runtime.observed_at.replace(microsecond=0) <= hold.snapshot_at <= hold.verified_at <= self.clock()
                and (self.clock() - hold.verified_at).total_seconds() <= 300,
                ReasonCode.BACKUP_PROOF_INVALID)
        state = self.store.read_state()
        require((state.active_attempt_id, state.active_operation_token) == (hold.attempt_id, hold.operation_token))

    def observe(self, observation: StructuralObservation, target: DiscoveredRelease, *,
                intent: Optional[SuccessIntent] = None, cold_hold=None, backup_seal=None) -> Observations:
        """Evidence-backed adapter; does not assert online freshness or controls."""
        observation = normalise_observation(observation)
        head = self.current()
        previous = self.evidence(head.release)
        self._observe(observation, previous, cold_hold=cold_hold)
        obs = observation.facts
        config, env_hash = head.config_fingerprint, head.env_sha256
        if intent is not None and cold_hold is None:
            # The guard first proves the exact live journal/preimage. Only that
            # retained intent's pin postimage may differ before Compose up.
            require(self.intent(intent.attempt.attempt_id) == intent and intent.old_head == head.identity
                    and intent.target.manifest_sha256 == target.manifest_sha256
                    and self.store.read_state().active_attempt_id == intent.attempt.attempt_id)
            config, env_hash = intent.expected_config_fingerprint, intent.expected_env_sha256
        require((obs.config_fingerprint, obs.profile_fingerprint, sha(observation.env_bytes)) ==
                (config, head.profile_fingerprint, env_hash), ReasonCode.CONFIGURATION_CHANGED)
        require({"files": dict(obs.deployment_files)} == decode_document(self.blob(head.execution_deployment)),
                ReasonCode.DEPLOYMENT_FILES_MISMATCH)
        require((target.repository, target.source_identity_sha256, target.trust_fingerprint) ==
                (head.release.repository, head.release.source_identity_sha256, head.release.trust_fingerprint),
                ReasonCode.SIGNATURE_REJECTED)
        target = self._verify(target)
        require(bool(target.release_proof) and not target.proof_reason_codes, ReasonCode.UNCERTIFIED_RELEASE)
        authorization = authorize_execution(target.raw_manifest, previous.raw_manifest, target.release_proof,
            repository=target.repository, mode=self.store.topology, profiles=self.store.read_registration().binding.profiles,
            platform=obs.platform, deployment_files=obs.deployment_files)
        _images(obs.dependency_images, {i["service"]: i for i in authorization.row["dependencies"]})
        compatibility = target.manifest['compatibility']
        if compatibility['contract_version'] == 2:
            require(observation.probe_contract == 'standalone-runtime-v2', ReasonCode.MIGRATION_INVENTORY_UNSUPPORTED)
            migration = authorization.row['migration_evidence']
            runtime = obs.runtime_observation
            require((runtime.migration_inventory_sha256, runtime.applied_migrations_sha256,
                     runtime.active_app_mapping_sha256) == (migration['source_inventory_sha256'],
                     migration['source_applied_set_sha256'], migration['active_app_mapping_sha256']),
                    ReasonCode.MIGRATION_INVENTORY_MISMATCH)
        else:
            epochs = {r['domain']: r['from_epoch'] for r in compatibility['state_domains']
                      if r['domain'] in obs.state_epochs}
            require(epochs == dict(obs.state_epochs), ReasonCode.INITIALIZATION_REQUIRED)
            require(not target.manifest['migration']['requires_bootstrap'], ReasonCode.UNCERTIFIED_RELEASE)
        require(observation.structure_sha256 == head.structure_sha256, ReasonCode.CONFIGURATION_CHANGED)
        require(compatibility['automatic_apply'] is True and not compatibility['requires_operator_initialization'],
                ReasonCode.UNCERTIFIED_RELEASE)
        return replace(obs, predecessor_raw=previous.raw_manifest, predecessor_verification=previous.verification,
                       release_proof=target.release_proof, baseline_attested=True, state_unchanged=True,
                       cold_hold=cold_hold, backup_seal=backup_seal,
                       binding_sha256=installation_binding_hash(self.store.read_registration().binding),
                       operation_token=cold_hold.operation_token if cold_hold is not None else obs.operation_token,
                       run_id=cold_hold.run_id if cold_hold is not None else obs.run_id,
                       graph_unchanged=True, env_unchanged=True)

    def prepare_success(self, attempt: AttemptRecord, *, expected_head: str, run_id: str,
                        target: DiscoveredRelease, observation: StructuralObservation, backup_raw: bytes,
                        render_config: Callable[[bytes], str], backup_max_age_seconds: int = 3600,
                        cold_hold=None, backup_seal=None) -> SuccessIntent:
        """Before env mutation: retain intent, exact backup and predicted pin-only postimage.

        render_config must be the fixed host adapter's read-only Compose evaluation
        of the supplied bytes with identical files/environment/updater settings. It
        must not read/adopt whatever env happens to exist after rollout. Its returned
        fingerprint uses orchestrator.fingerprints' exact inputs. No default exists.
        The engine MUST compare its actual env_changes to returned intent.pins.
        """
        observation = normalise_observation(observation)
        head = self.current()
        if head.identity != expected_head:
            raise StaleGeneration()
        if target.manifest.get('compatibility', {}).get('contract_version') == 2:
            require(isinstance(cold_hold, ColdHold) and isinstance(backup_seal, BackupSeal),
                    ReasonCode.BACKUP_PROOF_INVALID)
            require((cold_hold.attempt_id, cold_hold.operation_token, cold_hold.run_id) ==
                    (attempt.attempt_id, attempt.operation_token, run_id), ReasonCode.BACKUP_PROOF_INVALID)
            self._validate_backup_seal(attempt, head, run_id, observation, backup_raw, backup_seal)
        obs = self.observe(observation, target, cold_hold=cold_hold, backup_seal=backup_seal)
        # Engine receipts and backup proofs have second precision, as does the
        # existing attempt reservation. Do not reject a fast same-second success.
        now = self.clock().replace(microsecond=0)
        require(attempt.outcome == "reserved" and self.store.read_attempt(attempt.attempt_id) == attempt)
        require(attempt.target == TargetIdentity(target.version, target.manifest_hash))
        layout = topology_profile(attempt.binding.topology, attempt.binding.profiles)
        expected = BackupExpectation(attempt.attempt_id, head.installation_fingerprint, target.manifest_hash,
                                     head.release.version, attempt.reserved_at, layout.backup_domains)
        validate_backup_proof(backup_raw, expected, now=now, max_age_seconds=backup_max_age_seconds)
        pins = tuple(Pin(name, target.version) for name in sorted({COMPONENT_VARIABLES[c] for c in layout.active_components}))
        post = render_changes(observation.env_bytes.decode("utf-8", "surrogateescape"),
                              {p.name: p.value for p in pins}).encode("utf-8", "surrogateescape")
        require(render_config(observation.env_bytes) == head.config_fingerprint, ReasonCode.CONFIGURATION_CHANGED)
        predicted_config = render_config(post)
        _hash(predicted_config)
        with self.store._lock("state.lock"):
            require(self._env_bytes() == observation.env_bytes, ReasonCode.CONFIGURATION_CHANGED)
            state = self.store.read_state()
            require(state.baseline_head == expected_head and (state.active_attempt_id, state.active_operation_token) ==
                    (attempt.attempt_id, attempt.operation_token))
            require(obs.config_fingerprint == head.config_fingerprint)
            intent = SuccessIntent(1, expected_head, attempt, run_id, self._retain(target), pins, sha(post),
                                   predicted_config, self._blob(backup_raw), now,
                                   self._blob(observation.retained_state),
                                   self._blob(canonical_json(_wire(backup_seal))) if backup_seal is not None else None)
            self._immutable("transitions", attempt.attempt_id + ".json", intent)
            return intent

    def intent(self, attempt_id: str) -> SuccessIntent:
        _token(attempt_id)
        result = self._record("transitions", attempt_id + ".json", SuccessIntent)
        require(result.attempt.attempt_id == attempt_id)
        return result

    def validate_success_observation(self, intent, observation, *, _accepted=False):
        """Fixed post-state checks before the engine declares runtime success."""
        observation = normalise_observation(observation)
        old = self._head(intent.old_head)
        target = self.evidence(intent.target)
        self._observe(observation, target, accepted=_accepted)
        require((observation.facts.config_fingerprint, sha(observation.env_bytes),
                 observation.facts.profile_fingerprint) ==
                (intent.expected_config_fingerprint, intent.expected_env_sha256,
                 old.profile_fingerprint), ReasonCode.CONFIGURATION_CHANGED)
        require({'files': dict(observation.facts.deployment_files)} ==
                decode_document(self.blob(old.execution_deployment)), ReasonCode.DEPLOYMENT_FILES_MISMATCH)
        if target.manifest.get('compatibility', {}).get('contract_version') == 2:
            require(intent.source_state_sha256 is not None and intent.backup_seal_sha256 is not None)
            source = decode_document(self.blob(intent.source_state_sha256))
            require(observation.installation_sha256 == source['installation_sha256'], ReasonCode.CONFIGURATION_CHANGED)
            previous = self.evidence(old.release)
            authorization = authorize_execution(target.raw_manifest, previous.raw_manifest, target.release_proof,
                repository=target.repository, mode=self.store.topology, profiles=intent.attempt.binding.profiles,
                platform=observation.facts.platform, deployment_files=observation.facts.deployment_files)
            migration = authorization.row['migration_evidence']
            runtime = observation.facts.runtime_observation
            require(isinstance(runtime, RuntimeObservationV2) and
                    (runtime.migration_inventory_sha256, runtime.applied_migrations_sha256,
                     runtime.active_app_mapping_sha256) == (migration['target_inventory_sha256'],
                     migration['target_applied_set_sha256'], migration['active_app_mapping_sha256']),
                    ReasonCode.MIGRATION_INVENTORY_MISMATCH)
        else:
            require(observation.structure_sha256 == old.structure_sha256, ReasonCode.CONFIGURATION_CHANGED)

    def _success(self, intent, receipt_name, observation, *, accepted=False):
        require(self.intent(intent.attempt.attempt_id) == intent)
        old = self._head(intent.old_head)
        target = self.evidence(intent.target)
        previous = self.evidence(old.release)
        self.validate_success_observation(intent, observation, _accepted=accepted)
        obs = observation.facts
        execution = decode_document(self.blob(old.execution_deployment))
        require(dict(obs.deployment_files) == execution["files"], ReasonCode.DEPLOYMENT_FILES_MISMATCH)
        authorization = authorize_execution(target.raw_manifest, previous.raw_manifest, target.release_proof,
            repository=target.repository, mode=self.store.topology, profiles=intent.attempt.binding.profiles,
            platform=obs.platform, deployment_files=obs.deployment_files)
        _images(obs.dependency_images, {i["service"]: i for i in authorization.row["dependencies"]})
        require(re.fullmatch(r"receipt-[0-9]{8}T[0-9]{6}Z-[0-9]+\.json", receipt_name) is not None)
        # Engine owns journal reconciliation; this module never clears or replays it.
        with self.engine_state.private_directory() as directory:
            require("journal.json" not in os.listdir(directory))
            raw = self.store._read_raw(directory, receipt_name, 256 * 1024)
            metadata = decode_document(self.store._read_raw(directory, "automation-" + intent.run_id + ".json", 256 * 1024))
        receipt = decode_document(raw)
        binding = intent.attempt.binding
        try:
            receipt_env = str(binding.env_target.relative_to(binding.checkout))
        except ValueError:
            receipt_env = str(binding.env_target)
        require(receipt.get("status") == "succeeded" and receipt.get("kind") == "apply"
                and receipt.get("run_id") == intent.run_id and receipt.get("target_version") == target.version
                and (receipt.get("from_versions") == [old.release.version] or
                     "from_versions" not in receipt and receipt.get("from_version") == old.release.version)
                and receipt.get("manifest_hash") == target.manifest_hash
                and receipt.get("project") == binding.compose_project and receipt.get("mode") == binding.topology
                and receipt.get("production") is binding.production and receipt.get("env_file") == receipt_env
                and receipt.get("env_changes") == {p.name: p.value for p in intent.pins})
        require(all(metadata.get(k) == v for k, v in {
            "attempt_id": intent.attempt.attempt_id, "run_id": intent.run_id,
            "manifest_sha256": intent.target.manifest_sha256, "bundle_sha256": intent.target.bundle_sha256,
            "manifest_hash": intent.target.manifest_hash, "installation_fingerprint": old.installation_fingerprint,
            "config_fingerprint": old.config_fingerprint, "profile_fingerprint": old.profile_fingerprint,
            "trust_fingerprint": intent.target.trust_fingerprint}.items()))
        require(canonical_json(metadata.get("backup_proof")) == canonical_json(decode_document(self.blob(intent.backup_sha256))))
        ids = receipt.get("target_image_ids")
        require(isinstance(ids, dict) and set(ids) == set(obs.product_images)
                and all(all(i.image_id == ids[s] for i in replicas) for s, replicas in obs.product_images.items()),
                ReasonCode.IMAGE_DIGEST_MISMATCH)
        finished = timestamp(receipt.get("finished_at"))
        require(intent.prepared_at <= finished <= self.clock(), ReasonCode.CLOCK_SKEW)
        # The accepted observation precedes the engine's success record. Receipt
        # timestamps are second-precision; replay never refreshes observation time.
        if accepted:
            require(obs.now.replace(microsecond=0) <= finished, ReasonCode.CLOCK_SKEW)
        else:
            require(finished <= obs.now, ReasonCode.CLOCK_SKEW)
        head = BaselineHead(1, _increment(old.generation), old.installation_fingerprint, intent.target,
                            old.execution_deployment, intent.expected_config_fingerprint, old.profile_fingerprint,
                            intent.expected_env_sha256, observation.structure_sha256, intent.old_head,
                            intent.attempt.attempt_id, intent.run_id)
        terminal = replace(intent.attempt, outcome="succeeded", finished_at=finished,
                           backup_proof_identity=intent.backup_sha256, receipt_identity=sha(raw))
        return head, terminal, raw

    def commit(self, intent: SuccessIntent, *, receipt_name: str, observation: StructuralObservation,
               backup_state: Optional[bytes] = None) -> BaselineHead:
        """Durable success only. Evidence -> immutable successor -> terminal -> one CAS.

        The final state rename advances the head AND clears active attempt together.
        Crash before it is recoverable via the exact same retained receipt. Control,
        backoff and all existing failed-target latches are preserved. No rollout IO.
        """
        return self._commit_success(intent, receipt_name=receipt_name, observation=observation,
                                    backup_state=backup_state)

    def _commit_success(self, intent, *, receipt_name, observation, backup_state, accepted=False):
        observation = normalise_observation(observation)
        with self.store._directory(child='transitions') as transitions:
            require(not any(name in os.listdir(transitions) for name in (
                intent.attempt.attempt_id + '-restore-intent.json', intent.attempt.attempt_id + '-restore.json')))
        head, terminal, receipt_raw = self._success(intent, receipt_name, observation, accepted=accepted)
        self._validate_successor_backup(intent, observation, backup_state)
        with self.store._lock("state.lock") as directory:
            if not accepted:
                require(sha(self._env_bytes()) == intent.expected_env_sha256, ReasonCode.CONFIGURATION_CHANGED)
            state = self.store.read_state()
            saved = self.store.read_attempt(intent.attempt.attempt_id)
            seal = SuccessSeal(identity(intent), head.identity, receipt_name, terminal)
            if state.baseline_head == head.identity:
                require(saved == terminal and state.active_attempt_id is None and self.backup_state(head) == backup_state)
                require(self._record("transitions", intent.attempt.attempt_id + "-success.json", SuccessSeal) == seal)
                return self.current()
            if state.baseline_head != intent.old_head:
                raise StaleGeneration()
            require((state.active_attempt_id, state.active_operation_token) ==
                    (intent.attempt.attempt_id, intent.attempt.operation_token) and saved in (intent.attempt, terminal))
            self._blob(receipt_raw)
            if backup_state is not None:
                self._link_backup(head, backup_state)
            self._immutable("transitions", intent.attempt.attempt_id + "-success.json", seal)
            self._immutable("baselines", head.identity + ".json", head)
            with self.store._directory(child="attempts") as attempts:
                if saved != terminal:
                    self.store._write(attempts, terminal.attempt_id + ".json", terminal)
            self.store._write(directory, "state.json", replace(state, generation=_increment(state.generation),
                baseline_head=head.identity, active_attempt_id=None, active_operation_token=None))
            return head

    def retain_attempt(self, attempt, *, run_id, target, observation):
        """Evidence-first pre-stop intent; engine reserves the ledger only after this."""
        observation = normalise_observation(observation)
        head = self.current()
        self.observe(observation, target)
        require(self.store.read_attempt(attempt.attempt_id) == attempt)
        require((self.store.read_state().active_attempt_id, self.store.read_state().active_operation_token) ==
                (attempt.attempt_id, attempt.operation_token))
        require(isinstance(observation.facts.runtime_observation, RuntimeObservationV2),
                ReasonCode.MIGRATION_INVENTORY_UNSUPPORTED)
        with self.store._lock('state.lock'):
            observation_key, env_key = self._retain_observation(observation)
            intent = AutomaticAttemptIntent(head.identity, attempt, run_id, self._retain(target),
                observation_key, env_key)
            self._immutable('transitions', attempt.attempt_id + '-attempt.json', intent)
            return intent

    def automatic_attempt(self, attempt_id):
        _token(attempt_id)
        intent = self._record('transitions', attempt_id + '-attempt.json', AutomaticAttemptIntent)
        require(intent.attempt.attempt_id == attempt_id)
        return intent

    def _retain_observation(self, observation):
        retained_facts = {f.name: _wire(getattr(observation.facts, f.name)) for f in fields(Observations)
                          if f.name not in ('predecessor_raw', 'release_proof')}
        raw = canonical_json({'facts': retained_facts, 'formats': dict(observation.formats),
            'volumes': dict(observation.volumes), 'probe_contract': observation.probe_contract, 'ready': observation.ready})
        return self._blob(raw), self._blob(observation.env_bytes)

    def _validate_successor_backup(self, intent, observation, backup_state):
        old_backup = self.backup_state(self._head(intent.old_head))
        require((old_backup is None) == (backup_state is None))
        if old_backup is None:
            return
        # No unrelated destination/store or stale A images may enter B's head.
        before, after = decode_document(old_backup), decode_document(backup_state)
        require(set(before) == set(after) == {'schema_version', 'destination', 'policy', 'binding'})
        require(all(before[k] == after[k] for k in ('schema_version', 'destination', 'policy')))
        old_binding, new_binding = before['binding'], after['binding']
        stable = ('schema_version', 'installation_fingerprint', 'root', 'context', 'daemon', 'project',
                  'profiles', 'volumes', 'volume_root_ids')
        require(set(old_binding) == set(new_binding) and all(old_binding[k] == new_binding[k] for k in stable))
        require({k: v for k, v in old_binding['files'].items() if k != '.env'} ==
                {k: v for k, v in new_binding['files'].items() if k != '.env'}
                and new_binding['files']['.env'] == intent.expected_env_sha256)
        images = {**observation.facts.product_images, **observation.facts.dependency_images}
        require(set(new_binding['images']) == set(images) and all(
            {i.image_id for i in replicas} == {new_binding['images'][service]}
            for service, replicas in images.items()), ReasonCode.IMAGE_DIGEST_MISMATCH)

    def retain_success(self, intent, observation, backup_state):
        """Before success_decided: validate live B and freeze its complete successor evidence."""
        observation = normalise_observation(observation)
        require(self.intent(intent.attempt.attempt_id) == intent)
        self.validate_success_observation(intent, observation)
        self._validate_successor_backup(intent, observation, backup_state)
        with self.store._lock('state.lock'):
            state = self.store.read_state()
            require(state.baseline_head == intent.old_head and
                (state.active_attempt_id, state.active_operation_token) ==
                (intent.attempt.attempt_id, intent.attempt.operation_token))
            observation_key, env_key = self._retain_observation(observation)
            seal = AcceptedSuccess(identity(intent), observation_key, env_key,
                self._blob(backup_state) if backup_state is not None else None)
            self._immutable('transitions', intent.attempt.attempt_id + '-accepted.json', seal)
        return seal

    def accepted_success(self, intent, decision):
        """Authenticate pre-decision data against the irreversible engine seal."""
        seal = self._record('transitions', intent.attempt.attempt_id + '-accepted.json', AcceptedSuccess)
        require(seal.intent_sha256 == identity(intent) and self.intent(intent.attempt.attempt_id) == intent)
        observation = self._load_observation(seal.observation_sha256, seal.env_sha256)
        runtime = observation.facts.runtime_observation
        require(isinstance(runtime, RuntimeObservationV2) and type(decision) is dict)
        expected = {'run_id': intent.run_id, 'successor_intent_sha256': identity(intent),
            'observation_sha256': runtime.sha256, 'env_sha256': runtime.env_sha256,
            'config_sha256': runtime.config_sha256, 'storage_sha256': runtime.storage_sha256}
        require(all(decision.get(k) == v for k, v in expected.items()))
        backup = self.blob(seal.backup_state_sha256) if seal.backup_state_sha256 is not None else None
        return observation, backup

    def commit_decided_success(self, intent, *, receipt_name, decision):
        observation, backup = self.accepted_success(intent, decision)
        return self._commit_success(intent, receipt_name=receipt_name, observation=observation,
                                    backup_state=backup, accepted=True)

    def retained_observation(self, intent):
        return self._load_observation(intent.observation_sha256, intent.env_sha256)

    def _load_observation(self, observation_key, env_key):
        """Read retained fixed data, never import probe/release code or refresh time."""
        from .contracts import RuntimeImage
        from .runner_state import _typed
        doc = decode_document(self.blob(observation_key), MAX_EVIDENCE_BYTES)
        require(set(doc) == {'facts', 'formats', 'volumes', 'probe_contract', 'ready'})
        facts = dict(doc['facts'])
        for name in ('now', 'reserved_at'):
            if facts.get(name) is not None:
                facts[name] = _typed(datetime, facts[name])
        for name in ('product_images', 'dependency_images'):
            facts[name] = {s: tuple(RuntimeImage(**i) for i in replicas) for s, replicas in facts[name].items()}
        for name in ('predecessor_verification',):
            if facts.get(name) is not None:
                value = dict(facts[name])
                value['checked_at'] = _typed(datetime, value['checked_at'])
                facts[name] = VerificationSnapshot(**value)
        runtime = dict(facts['runtime_observation'])
        runtime['observed_at'] = _typed(datetime, runtime['observed_at'])
        for name in ('product_images', 'dependency_images'):
            runtime[name] = {s: tuple(RuntimeImage(**i) for i in replicas) for s, replicas in runtime[name].items()}
        facts['runtime_observation'] = RuntimeObservationV2(**runtime)
        # Source is captured before any backup, so it cannot contain a hold/seal.
        require(facts['cold_hold'] is None and facts['backup_seal'] is None)
        return StructuralObservation(Observations(**facts), self.blob(env_key), doc['formats'],
            doc['volumes'], doc['probe_contract'], doc['ready'])

    def finish_failed_available(self, retained, observation):
        """Verified pre-seal abort: evidence first, then terminal + clear/latch CAS."""
        observation = normalise_observation(observation)
        require(self.automatic_attempt(retained.attempt.attempt_id) == retained)
        old = self._head(retained.old_head)
        self._observe(observation, self.evidence(old.release))
        source = self.retained_observation(retained)
        require((observation.structure_sha256, observation.installation_sha256, sha(observation.env_bytes),
                 observation.facts.config_fingerprint, observation.facts.profile_fingerprint) ==
                (old.structure_sha256, source.installation_sha256, old.env_sha256,
                 old.config_fingerprint, old.profile_fingerprint), ReasonCode.RESTORE_VERIFICATION_FAILED)
        require(_wire(observation.facts.runtime_observation.product_images) ==
                _wire(source.facts.runtime_observation.product_images), ReasonCode.IMAGE_DIGEST_MISMATCH)
        name = retained.attempt.attempt_id + '-failed-available.json'
        with self.store._lock('state.lock'):
            require(self.store.read_state().baseline_head == retained.old_head)
            with self.store._directory(child='transitions') as directory:
                names = os.listdir(directory)
                require(not any(retained.attempt.attempt_id + suffix in names for suffix in
                    ('-accepted.json', '-success.json', '-restore-intent.json', '-restore.json')))
            if name in names:
                seal = self._record('transitions', name, FailedAvailableSeal)
                require(seal.intent_sha256 == identity(retained)
                    and replace(seal.terminal, outcome='reserved', finished_at=None,
                        receipt_identity=None, backup_proof_identity=None) == retained.attempt)
                self._load_observation(seal.observation_sha256, seal.env_sha256)
            else:
                observation_key, env_key = self._retain_observation(observation)
                terminal = replace(retained.attempt, outcome='failed_available', finished_at=observation.facts.now)
                seal = FailedAvailableSeal(identity(retained), observation_key, env_key, terminal)
                self._immutable('transitions', name, seal)
        # finish_attempt owns its short state lock and exact terminal replay.
        return self.store.finish_attempt(seal.terminal)

    def _validate_backup_seal(self, attempt, head, run_id, source, backup_raw, seal):
        """Original snapshot identity/order, deliberately NO recovery-time expiry."""
        require(isinstance(seal, BackupSeal) and isinstance(source.facts.runtime_observation, RuntimeObservationV2),
                ReasonCode.BACKUP_PROOF_INVALID)
        runtime = source.facts.runtime_observation
        proof = decode_document(backup_raw)
        require((seal.attempt_id, seal.operation_token, seal.run_id, seal.binding_sha256,
                 seal.target_manifest_hash, seal.source_version, seal.proof_sha256, seal.source_observation_sha256) ==
                (attempt.attempt_id, attempt.operation_token, run_id, installation_binding_hash(attempt.binding),
                 attempt.target.manifest_hash, head.release.version, sha(backup_raw), runtime.sha256),
                ReasonCode.BACKUP_INVENTORY_MISMATCH)
        expected = BackupExpectation(attempt.attempt_id, head.installation_fingerprint, attempt.target.manifest_hash,
            head.release.version, attempt.reserved_at, topology_profile(attempt.binding.topology, attempt.binding.profiles).backup_domains)
        # Verify freshness AT the original authorization, not at this recovery.
        validate_backup_proof(backup_raw, expected, now=self.clock(), seal=seal, for_recovery=True)
        require(proof.get('schema_version') == 2 and proof.get('inventory_version') == 2
                and proof.get('binding_sha256') == seal.binding_sha256
                and proof.get('inventory_sha256') == seal.inventory_sha256
                and proof.get('source_observation_sha256') == seal.source_observation_sha256
                and proof.get('quiescence_evidence_sha256') == seal.quiescence_evidence_sha256
                and proof.get('restore_procedure_id') == seal.restore_procedure_id
                and timestamp(proof.get('snapshot_at')) == seal.snapshot_at
                and runtime.observed_at.replace(microsecond=0) <= seal.snapshot_at
                and attempt.reserved_at <= seal.snapshot_at <= timestamp(proof.get('completed_at'))
                <= seal.authorised_at <= self.clock(), ReasonCode.BACKUP_INVENTORY_MISMATCH)

    def prepare_restore(self, attempt: AttemptRecord, *, expected_head: str, run_id: str,
                        source_observation: StructuralObservation, backup_raw: bytes, backup_seal: BackupSeal,
                        env_inverse_raw: bytes) -> RestoreIntent:
        """Retain restore correlation before mutation; never refresh storage identities.

        source_observation is the retained pre-stop observation. The engine owns
        original-seal authentication, direction and storage restore. This method
        is metadata-only and permits the exact forward env postimage to be live.
        env_inverse_raw is the original native env recovery record, or JSON null
        only if no env mutation was ever prepared.
        """
        source_observation = normalise_observation(source_observation)
        head = self._head(expected_head)
        state = self.store.read_state()
        require(state.baseline_head == expected_head and
                (state.active_attempt_id, state.active_operation_token) == (attempt.attempt_id, attempt.operation_token))
        require(self.store.read_attempt(attempt.attempt_id) == attempt and attempt.outcome == 'reserved')
        require(source_observation.structure_sha256 == head.structure_sha256
                and sha(source_observation.env_bytes) == head.env_sha256
                and source_observation.facts.config_fingerprint == head.config_fingerprint)
        self._validate_backup_seal(attempt, head, run_id, source_observation, backup_raw, backup_seal)
        # JSON null is the only no-env-write representation; do not accept a
        # fabricated inverse or an unrelated preimage. _check_recovery is read-only.
        require(type(env_inverse_raw) is bytes and len(env_inverse_raw) <= MAX_EVIDENCE_BYTES)
        inverse = None if env_inverse_raw == b'null' else decode_document(env_inverse_raw, MAX_EVIDENCE_BYTES)
        if inverse is not None:
            from .envfile import _check_recovery
            _check_recovery(attempt.binding.env_target, inverse)
            require(inverse['before_sha256'] == head.env_sha256, ReasonCode.ENV_CONFLICT)
        else:
            require(sha(self._env_bytes()) == head.env_sha256, ReasonCode.ENV_CONFLICT)
        with self.store._lock('state.lock'):
            require(self.store.read_state() == state)
            with self.store._directory(child='transitions') as transitions:
                require(attempt.attempt_id + '-success.json' not in os.listdir(transitions))
            intent = RestoreIntent(expected_head, attempt, run_id, self._blob(backup_raw),
                self._blob(canonical_json(_wire(backup_seal))), self._blob(source_observation.retained_state),
                self._blob(env_inverse_raw))
            self._immutable('transitions', attempt.attempt_id + '-restore-intent.json', intent)
            return intent

    def restore_intent(self, attempt_id):
        _token(attempt_id)
        result = self._record('transitions', attempt_id + '-restore-intent.json', RestoreIntent)
        require(result.attempt.attempt_id == attempt_id)
        return result

    def validate_restore_observation(self, intent, observation):
        observation = normalise_observation(observation)
        old = self._head(intent.old_head)
        self._observe(observation, self.evidence(old.release))
        source = decode_document(self.blob(intent.source_state_sha256))
        require((observation.structure_sha256, observation.installation_sha256,
                 sha(observation.env_bytes), observation.facts.config_fingerprint,
                 observation.facts.profile_fingerprint) ==
                (old.structure_sha256, source['installation_sha256'], old.env_sha256,
                 old.config_fingerprint, old.profile_fingerprint), ReasonCode.RESTORE_VERIFICATION_FAILED)
        require({'files': dict(observation.facts.deployment_files)} ==
                decode_document(self.blob(old.execution_deployment)), ReasonCode.DEPLOYMENT_FILES_MISMATCH)
        # Source product digest and EXACT local image IDs must be restored, not
        # merely a new image claiming A's version. Container IDs may be recreated.
        runtime = observation.facts.runtime_observation
        require(isinstance(runtime, RuntimeObservationV2)
                and _wire(runtime.product_images) == source['runtime']['product_images']
                and _wire(runtime.dependency_images) == source['runtime']['dependency_images'],
                ReasonCode.IMAGE_DIGEST_MISMATCH)

    def commit_restore(self, intent: RestoreIntent, *, receipt_name: str,
                       observation: StructuralObservation) -> BaselineHead:
        """Evidence -> RestoreSeal -> terminal -> atomic clear + failed-target latch.

        A is never a successor of failed B. No service, env or volume mutation is
        possible here, including after A has accepted post-restore writes.
        """
        observation = normalise_observation(observation)
        require(self.restore_intent(intent.attempt.attempt_id) == intent)
        self.validate_restore_observation(intent, observation)
        old = self._head(intent.old_head)
        require(re.fullmatch(r'receipt-[0-9]{8}T[0-9]{6}Z-[0-9]+\.json', receipt_name) is not None)
        with self.engine_state.private_directory() as directory:
            require('journal.json' not in os.listdir(directory))
            raw = self.store._read_raw(directory, receipt_name, 256 * 1024)
        receipt = decode_document(raw)
        binding = intent.attempt.binding
        original_seal = decode_document(self.blob(intent.backup_seal_sha256))
        require(original_seal['proof_sha256'] == sha(self.blob(intent.backup_sha256)))
        self.blob(intent.env_inverse_sha256)
        # Existing receipts are deliberately PUBLIC/allowlisted. Attempt, token,
        # snapshot and inverse stay private in RestoreIntent/RestoreSeal, which
        # bind this exact receipt hash. Never extend public receipts with them.
        expected = {'kind': 'restore', 'status': 'restored', 'run_id': intent.run_id,
            'from_version': old.release.version, 'target_version': intent.attempt.target.version,
            'manifest_hash': intent.attempt.target.manifest_hash,
            'project': binding.compose_project, 'mode': binding.topology, 'production': binding.production,
            'target_image_ids': {s: replicas[0].image_id for s, replicas in observation.facts.product_images.items()}}
        require(all(receipt.get(k) == v for k, v in expected.items()), ReasonCode.RESTORE_VERIFICATION_FAILED)
        finished = timestamp(receipt.get('finished_at'))
        require(intent.attempt.reserved_at <= finished <= observation.facts.now, ReasonCode.CLOCK_SKEW)
        terminal = replace(intent.attempt, outcome='restored', finished_at=finished,
            backup_proof_identity=intent.backup_sha256, receipt_identity=sha(raw))
        seal = RestoreSeal(identity(intent), intent.old_head, receipt_name, terminal, observation.structure_sha256)
        with self.store._lock('state.lock') as directory:
            state = self.store.read_state()
            require(state.baseline_head == intent.old_head)
            saved = self.store.read_attempt(intent.attempt.attempt_id)
            require(saved in (intent.attempt, terminal))
            require(sha(self._env_bytes()) == old.env_sha256, ReasonCode.ENV_CONFLICT)
            if state.active_attempt_id is None:
                require(saved == terminal and self._record('transitions', intent.attempt.attempt_id + '-restore.json', RestoreSeal) == seal)
                return old  # Exact replay after retry_target must not re-latch B.
            require((state.active_attempt_id, state.active_operation_token) ==
                    (intent.attempt.attempt_id, intent.attempt.operation_token))
            # Success and restoration decisions can never silently supersede one another.
            with self.store._directory(child='transitions') as transitions:
                require(intent.attempt.attempt_id + '-success.json' not in os.listdir(transitions))
            self._blob(raw)
            self._immutable('transitions', intent.attempt.attempt_id + '-restore.json', seal)
            if saved != terminal:
                with self.store._directory(child='attempts') as attempts:
                    self.store._write(attempts, terminal.attempt_id + '.json', terminal)
            self.store._write(directory, 'state.json', replace(state, generation=_increment(state.generation),
                active_attempt_id=None, active_operation_token=None, failed_target_latch=terminal.target))
            return old

    def reconcile_restore(self, attempt_id, *, receipt_name, observation):
        return self.commit_restore(self.restore_intent(attempt_id), receipt_name=receipt_name, observation=observation)

    def commit_success(self, intent: SuccessIntent, *, receipt_name: str, observation: StructuralObservation,
                       backup_state: Optional[bytes] = None) -> BaselineHead:
        """Explicit engine callback name; commit remains the compatible entrypoint."""
        return self.commit(intent, receipt_name=receipt_name, observation=observation, backup_state=backup_state)

    def reconcile(self, attempt_id: str, *, receipt_name: str, observation: StructuralObservation,
                  backup_state: Optional[bytes] = None) -> BaselineHead:
        """Exact proven success, METADATA ONLY. Missing/ambiguous evidence refuses.

        Caller first uses the engine's existing specific-journal finalization (if
        necessary), then repeats fresh runtime/readiness/structural observations
        under the same locks. This function has no backup/pull/recreate callbacks.
        """
        return self.commit(self.intent(attempt_id), receipt_name=receipt_name, observation=observation,
                           backup_state=backup_state)
