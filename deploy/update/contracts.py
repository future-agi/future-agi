"""Shared host-update value contracts and the closed, non-authoritative JSON boundary.

Nothing here observes Docker, verifies a release, schedules work or authorizes a
mutation. AutomationGuard/LockedContext are protocols, not working adapters.
Private value objects must NEVER be serialized into public status. Public JSON
has its own schema and validators; even valid public JSON is not recovery proof.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Literal, Mapping, Optional, Protocol, Tuple, TypedDict, Union, cast, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .manifest import parse_version

__all__ = [
    "AutomationMode", "Topology", "OperationKind", "OperationState", "OperationStage", "GuardBoundary",
    "RunDisposition", "JSONValue", "ComponentName", "Freshness", "UnavailableReason", "ReasonCode", "ContractError",
    "MaintenanceWindowSpec", "AutomationPolicy", "InstallationBinding", "VerificationSnapshot", "PreparedUpdate",
    "RunOutcome", "OperationEvent", "LockedContext", "AutomationGuard", "POLICY_BOUNDS", "MAX_PUBLIC_BYTES",
    "MAX_PUBLIC_DEPTH", "ACTIVE_OPERATION_STATES", "PublicInstallation", "PublicComponent", "PublicVersions",
    "PublicPolicy", "PublicAlwaysWindow", "PublicIntervalWindow", "PublicAutomation", "PublicDiscovery",
    "PublicDownload", "PublicOperation", "PublicLastRun", "PublicSafety", "PublicStatusV1", "PublicAPIResultV1",
    "PublicAPIResponseV1", "validate_timezone", "validate_public_status", "decode_public_status",
    "validate_api_response", "decode_api_response",
    "AuthorisedTransition", "BackupSeal", "ColdHold", "RuntimeObservationV2", "RestorationResult",
    "MigrationEvidence", "RuntimeImage", "AutomaticPhase", "RecoveryDirection", "RestoreState", "ForwardResult",
    "QUALIFICATION_POLICY", "RESTORE_PROCEDURE_ID", "BACKUP_INVENTORY_VERSION", "HOST_OBSERVATION_CONTRACT",
    "RUNTIME_ACTIVATION_CONTRACT", "PERSISTENT_DOMAINS", "AUTOMATIC_POLICY_BOUNDS", "installation_binding_hash",
    "MigrationEngine", "MigrationRiskClass", "MigrationTableMeasurement", "MigrationThroughput", "MigrationMeasurement",
    "BootstrapEnvelopeTableMeasurement", "BootstrapEnvelopeMeasurement",
    "VolumeTransferMeasurement", "TransitionMeasurements", "HostTableSize", "HostSizeObservation",
    "AutomaticUpdateEstimate", "AutomaticUpdateDecision", "AUTOMATIC_VOLUMES", "pending_migration_plan_hash",
]

AutomationMode = Literal["manual", "notify", "download", "apply"]
Topology = Literal["standalone", "distributed"]
OperationKind = Literal["check", "download", "apply", "rollback", "restore"]
OperationState = Literal[
    "idle", "checking", "downloading", "backing_up", "staged", "awaiting_initialization",
    "applying", "verifying", "succeeded", "failed", "interrupted", "rolling_back",
    "rolled_back", "blocked", "unknown", "restoring", "restored", "recovery_required",
]
OperationStage = Literal[
    "planning", "backup", "image_fetch", "image_verification", "env_selection",
    "production_initialization", "rollout", "readiness", "recovery", "quiescence", "restore", "stability",
]
GuardBoundary = Literal[
    "before_recovery", "before_backup", "after_backup", "before_env_commit",
    "before_rollout", "before_fallback", "before_restore", "after_restore",
]
RunDisposition = Literal[
    "skipped", "busy", "current", "available", "downloaded", "applied", "staged",
    "rolled_back", "blocked", "recovery_required", "failed", "interrupted", "restored", "failed_available",
]
JSONValue = Union[None, bool, int, float, str, Mapping[str, "JSONValue"], Tuple["JSONValue", ...]]
MAX_PUBLIC_BYTES = 32768
MAX_PUBLIC_DEPTH = 12
ACTIVE_OPERATION_STATES = frozenset({
    "checking", "downloading", "backing_up", "applying", "verifying", "rolling_back", "restoring",
})


class ReasonCode(str, Enum):
    MANIFEST_UNAVAILABLE = "manifest_unavailable"
    SIGNATURE_REJECTED = "signature_rejected"
    CHANNEL_MISMATCH = "channel_mismatch"
    VERSION_POLICY_BLOCKED = "version_policy_blocked"
    RELEASE_INCOMPLETE = "release_incomplete"
    DISCOVERY_INCOMPLETE = "discovery_incomplete"
    IMMUTABLE_RELEASE_CHANGED = "immutable_release_changed"
    NETWORK_UNAVAILABLE = "network_unavailable"
    RATE_LIMITED = "rate_limited"
    CACHE_INVALID = "cache_invalid"
    OWNERSHIP_UNCONFIRMED = "ownership_unconfirmed"
    DEPLOYMENT_FILES_MISMATCH = "deployment_files_mismatch"
    REQUIRED_ENV_MISSING = "required_env_missing"
    UNSUPPORTED_CONTRACT = "unsupported_contract"
    UNCERTIFIED_RELEASE = "uncertified_release"
    CONFIGURATION_CHANGED = "configuration_changed"
    BACKUP_REQUIRED = "backup_required"
    BACKUP_FAILED = "backup_failed"
    BACKUP_PROOF_INVALID = "backup_proof_invalid"
    BACKUP_EXPIRED = "backup_expired"
    STALE_STAGE = "stale_stage"
    ENV_CONFLICT = "env_conflict"
    INITIALIZATION_REQUIRED = "initialization_required"
    MANUAL_STAGE_PRESENT = "manual_stage_present"
    RECOVERY_REQUIRED = "recovery_required"
    READINESS_FAILED = "readiness_failed"
    OPERATION_INTERRUPTED = "operation_interrupted"
    OPERATION_TIMEOUT = "operation_timeout"
    ROLLBACK_INELIGIBLE = "rollback_ineligible"
    ROLLBACK_FAILED = "rollback_failed"
    FAILED_TARGET_LATCHED = "failed_target_latched"
    RUNNING_VERSIONS_UNKNOWN = "running_versions_unknown"
    MIXED_RUNNING_VERSIONS = "mixed_running_versions"
    OUTSIDE_WINDOW = "outside_window"
    INSUFFICIENT_WINDOW = "insufficient_window"
    CLOCK_SKEW = "clock_skew"
    TIMEZONE_UNAVAILABLE = "timezone_unavailable"
    HOST_VERIFIER_REQUIRED = "host_verifier_required"
    IMAGE_DIGEST_MISMATCH = "image_digest_mismatch"
    DISK_SPACE_INSUFFICIENT = "disk_space_insufficient"
    STATUS_REPORTING_UNAVAILABLE = "status_reporting_unavailable"
    SCHEDULER_UNREGISTERED = "scheduler_unregistered"
    UNSUPPORTED_INSTALLATION = "unsupported_installation"
    CONTROL_INVALID = "control_invalid"
    PROCESS_FAILED = "process_failed"
    RESTORE_NOT_CERTIFIED = "restore_not_certified"
    TRANSITION_NOT_REHEARSED = "transition_not_rehearsed"
    PLATFORM_NOT_REHEARSED = "platform_not_rehearsed"
    RESTORE_PROCEDURE_UNSUPPORTED = "restore_procedure_unsupported"
    MIGRATION_INVENTORY_UNSUPPORTED = "migration_inventory_unsupported"
    MIGRATION_INVENTORY_MISMATCH = "migration_inventory_mismatch"
    BACKUP_INVENTORY_MISMATCH = "backup_inventory_mismatch"
    RESTORE_CAPACITY_UNKNOWN = "restore_capacity_unknown"
    RESTORE_CAPACITY_INSUFFICIENT = "restore_capacity_insufficient"
    RESTORE_CHILD_AMBIGUOUS = "restore_child_ambiguous"
    RESTORE_VERIFICATION_FAILED = "restore_verification_failed"
    AUTOMATIC_RECOVERY_PENDING = "automatic_recovery_pending"
    RESTORED_AFTER_UPDATE_FAILURE = "restored_after_update_failure"
    MIGRATION_EXCEEDS_WINDOW = "migration_exceeds_window"
    INSUFFICIENT_DISK_FOR_MIGRATION = "insufficient_disk_for_migration"
    BACKUP_EXCEEDS_WINDOW = "backup_exceeds_window"
    INSUFFICIENT_DISK_FOR_BACKUP = "insufficient_disk_for_backup"
    MIGRATION_STALLED = "migration_stalled"
    DISK_WATERMARK_ABORT = "disk_watermark_abort"


class ContractError(ValueError):
    """Sanitized rejection: never echo untrusted values, paths or parser errors."""

    def __init__(self, reason_code: str = "malformed") -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require(condition: bool, reason: str = "malformed") -> None:
    if not condition:
        raise ContractError(reason)


def _freeze(value: Any) -> JSONValue:
    """Defensive deep copy of private JSON values; no reference to mutable input."""
    if isinstance(value, Mapping):
        _require(all(type(key) is str for key in value))
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    _require(value is None or type(value) in (str, bool, int, float))
    if type(value) is float:
        import math
        _require(math.isfinite(value))
    return value


@dataclass(frozen=True)
class MaintenanceWindowSpec:
    """Validated configuration, not a DST evaluator (schedule.py owns that)."""

    kind: Literal["always", "interval"]
    timezone: str
    days_of_week: Tuple[int, ...] = ()
    start: Optional[str] = None
    end: Optional[str] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.days_of_week, (tuple, list)))
        _require(all(type(day) is int and 1 <= day <= 7 for day in self.days_of_week))
        object.__setattr__(self, "days_of_week", tuple(self.days_of_week))
        validate_timezone(self.timezone)
        _require(self.kind in ("always", "interval"))
        if self.kind == "always":
            _require(not self.days_of_week and self.start is None and self.end is None)
        else:
            _require(bool(self.days_of_week) and len(set(self.days_of_week)) == len(self.days_of_week))
            _require(all(type(day) is int and 1 <= day <= 7 for day in self.days_of_week))
            for endpoint in (self.start, self.end):
                _require(type(endpoint) is str and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", endpoint) is not None)
            _require(self.start != self.end)


@dataclass(frozen=True)
class AutomationPolicy:
    """Configured policy only. Durable controls override enabled and pause later."""

    mode: AutomationMode = "manual"
    enabled: bool = False
    channel: Literal["stable", "beta"] = "stable"
    version_policy: Literal["patch", "minor", "major"] = "minor"
    check_interval_seconds: int = 21600
    max_backoff_seconds: int = 86400
    timezone: str = "UTC"
    maintenance_window: Optional[MaintenanceWindowSpec] = None
    min_start_budget_seconds: int = 900
    backup_max_age_seconds: int = 3600
    stage_max_age_seconds: int = 86400
    automatic_rollback: bool = False
    probe_timeout_seconds: int = 60
    pull_timeout_seconds: int = 1800
    tick_timeout_seconds: int = 14400
    backup_timeout_seconds: int = 3600
    wait_timeout_seconds: int = 1200
    # None retains legacy/manual configuration parsing and budgets. Enrollment
    # must explicitly select the new policy; automatic_rollback is NOT an opt-out.
    qualification_policy: Optional[Literal["standalone-cold-restore-v1"]] = None
    restore_timeout_seconds: int = 3600
    recovery_startup_timeout_seconds: int = 1200
    stability_period_seconds: int = 60
    quiescence_reserve_seconds: int = 300
    metadata_reserve_seconds: int = 300
    estimate_safety_factor: int = 2
    migration_timeout_seconds: int = 1200
    migration_stall_timeout_seconds: int = 300
    disk_watermark_reserve_bytes: int = 1073741824

    def __post_init__(self) -> None:
        _require(type(self.mode) is str and self.mode in get_args(AutomationMode))
        _require(type(self.enabled) is bool and type(self.automatic_rollback) is bool)
        _require(self.channel in ("stable", "beta") and self.version_policy in ("patch", "minor", "major"))
        for name, (minimum, maximum) in POLICY_BOUNDS.items():
            value = getattr(self, name)
            _require(type(value) is int and minimum <= value <= maximum)
        _require(self.qualification_policy in (None, "standalone-cold-restore-v1"))
        for name, (minimum, maximum) in AUTOMATIC_POLICY_BOUNDS.items():
            value = getattr(self, name)
            _require(type(value) is int and minimum <= value <= maximum)
        _require(self.max_backoff_seconds >= self.check_interval_seconds)
        validate_timezone(self.timezone)
        if self.maintenance_window is not None:
            _require(isinstance(self.maintenance_window, MaintenanceWindowSpec))
            _require(self.maintenance_window.timezone == self.timezone)
        # Validate even while configured disabled: a later durable enable must
        # not activate an inconsistent policy. No window is a valid blocked
        # configuration, not implicit permission to apply at any time.
        if self.qualification_policy == "standalone-cold-restore-v1":
            _require(self.migration_stall_timeout_seconds <= self.migration_timeout_seconds)
        if self.mode == "apply":
            _require(self.tick_timeout_seconds >= self.required_tick_budget_seconds)

    @property
    def required_tick_budget_seconds(self) -> int:
        if self.qualification_policy == "standalone-cold-restore-v1":
            return self.pull_timeout_seconds + self.required_forward_budget_seconds + self.required_recovery_budget_seconds
        if self.automatic_rollback:
            return self.backup_timeout_seconds + 2 * self.pull_timeout_seconds + 4 * self.wait_timeout_seconds + 1200
        return self.backup_timeout_seconds + self.pull_timeout_seconds + 2 * self.wait_timeout_seconds + 600


    @property
    def required_forward_budget_seconds(self) -> int:
        """Cold budget; prefetch/pull completes BEFORE this clock starts."""
        migration = self.migration_timeout_seconds if self.qualification_policy == "standalone-cold-restore-v1" else 0
        return (self.backup_timeout_seconds + migration + self.wait_timeout_seconds + self.stability_period_seconds
                + self.quiescence_reserve_seconds + self.metadata_reserve_seconds)

    @property
    def required_recovery_budget_seconds(self) -> int:
        """Protected reserve, or the full budget of a fresh recovery-only tick."""
        return (self.restore_timeout_seconds + self.recovery_startup_timeout_seconds + self.stability_period_seconds
                + self.quiescence_reserve_seconds + self.metadata_reserve_seconds)

    @property
    def required_cold_budget_seconds(self) -> int:
        return self.required_forward_budget_seconds + self.required_recovery_budget_seconds


# Separate from legacy parser keys; setup-policy-status owns adding settings.
AUTOMATIC_POLICY_BOUNDS: Mapping[str, Tuple[int, int]] = MappingProxyType({
    "restore_timeout_seconds": (1, 86400),
    "recovery_startup_timeout_seconds": (60, 7200),
    "stability_period_seconds": (1, 3600),
    "quiescence_reserve_seconds": (60, 3600),
    "metadata_reserve_seconds": (60, 3600),
    "estimate_safety_factor": (2, 100),
    "migration_timeout_seconds": (1, 86400),
    "migration_stall_timeout_seconds": (1, 7200),
    "disk_watermark_reserve_bytes": (1, 9007199254740991),
})


POLICY_BOUNDS: Mapping[str, Tuple[int, int]] = MappingProxyType({
    "check_interval_seconds": (300, 86400),
    "max_backoff_seconds": (300, 86400),
    "min_start_budget_seconds": (60, 7200),
    "backup_max_age_seconds": (60, 86400),
    "stage_max_age_seconds": (300, 604800),
    "probe_timeout_seconds": (5, 300),
    "pull_timeout_seconds": (60, 7200),
    "tick_timeout_seconds": (900, 172800),
    "backup_timeout_seconds": (1, 86400),
    "wait_timeout_seconds": (60, 7200),
})


@dataclass(frozen=True)
class InstallationBinding:
    """Private registration identity; constructing it does not qualify the host.

    Paths/daemon identity/fingerprints are private. Only slot_id, topology and
    production may be copied into the separately validated public identity.
    """

    slot_id: str
    selection_hash: str
    installation_fingerprint: str
    checkout: Path
    env_alias: Path
    env_target: Path
    topology: Topology
    production: bool
    compose_project: str
    compose_files: Tuple[Path, ...]
    profiles: Tuple[str, ...]
    docker_context: str
    daemon_identity: str
    host_uid: int

    def __post_init__(self) -> None:
        _require(type(self.slot_id) is str and re.fullmatch(r"[0-9a-f]{32}", self.slot_id) is not None)
        _require(self.topology in get_args(Topology) and type(self.production) is bool)
        _require(not self.production or self.topology == "distributed")
        _require(type(self.host_uid) is int and self.host_uid >= 0)
        _require(all(isinstance(path, Path) for path in (self.checkout, self.env_alias, self.env_target)))
        _require(isinstance(self.compose_files, (tuple, list)) and all(isinstance(p, Path) for p in self.compose_files))
        _require(isinstance(self.profiles, (tuple, list)) and all(type(p) is str for p in self.profiles))
        _require(all(type(getattr(self, key)) is str for key in (
            "selection_hash", "installation_fingerprint", "compose_project", "docker_context", "daemon_identity")))
        object.__setattr__(self, "compose_files", tuple(self.compose_files))
        object.__setattr__(self, "profiles", tuple(self.profiles))


def installation_binding_hash(binding: InstallationBinding) -> str:
    """Canonical private enrollment binding; no path resolution or host I/O."""
    from .manifest import manifest_hash
    _require(isinstance(binding, InstallationBinding))
    value = {name: getattr(binding, name) for name in (
        "slot_id", "selection_hash", "installation_fingerprint", "topology", "production",
        "compose_project", "docker_context", "daemon_identity", "host_uid")}
    value.update({name: str(getattr(binding, name)) for name in ("checkout", "env_alias", "env_target")})
    value["compose_files"] = [str(path) for path in binding.compose_files]
    value["profiles"] = sorted(binding.profiles)
    return manifest_hash(value)


@dataclass(frozen=True)
class VerificationSnapshot:
    """Allowlisted immutable trust.Verification fields plus caller's aware clock.

    trust.Verification has no checked_at; the adapter supplies that observation
    time. Error/details are deliberately omitted. This is not a verifier.
    """

    verified: bool
    method: str
    signer: str
    issuer: str
    tool: str
    checked_at: datetime
    public_key_sha256: str = ""  # Exact configured key content, not its private filename.

    def __post_init__(self) -> None:
        _require(type(self.public_key_sha256) is str and (self.public_key_sha256 == "" or
                 re.fullmatch(r"[0-9a-f]{64}", self.public_key_sha256) is not None))
        _require(type(self.verified) is bool)
        _require(all(type(value) is str for value in (self.method, self.signer, self.issuer, self.tool)))
        _aware(self.checked_at)


@dataclass(frozen=True)
class PreparedUpdate:
    """Read-only prepared inputs; a fresh in-lock guard must revalidate all of them.

    manifest and plan are defensive, deeply immutable JSON copies of the existing
    validated manifest/make_plan outputs. No public wholesale serialization exists.
    Hashes identify the exact signed bytes, never reserialized JSON.
    """

    manifest: Mapping[str, JSONValue]
    raw_manifest: bytes
    raw_bundle: bytes
    manifest_sha256: str
    bundle_sha256: str
    verification: VerificationSnapshot
    binding: InstallationBinding
    config_fingerprint: str
    profile_fingerprint: str
    deployment_fingerprint: str
    trust_fingerprint: str
    plan: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        _require(isinstance(self.verification, VerificationSnapshot) and isinstance(self.binding, InstallationBinding))
        _require(all(type(value) is str for value in (
            self.config_fingerprint, self.profile_fingerprint, self.deployment_fingerprint, self.trust_fingerprint)))
        _require(type(self.raw_manifest) is bytes and type(self.raw_bundle) is bytes)
        _require(hashlib.sha256(self.raw_manifest).hexdigest() == self.manifest_sha256)
        _require(hashlib.sha256(self.raw_bundle).hexdigest() == self.bundle_sha256)
        _require(isinstance(self.manifest, Mapping) and isinstance(self.plan, Mapping))
        object.__setattr__(self, "manifest", _freeze(self.manifest))
        object.__setattr__(self, "plan", _freeze(self.plan))


# Private restore-backed interfaces. Values are immutable evidence, not authority
# by construction: callers must obtain them from authenticated in-lock adapters.
QUALIFICATION_POLICY = "standalone-cold-restore-v1"
RESTORE_PROCEDURE_ID = "standalone-inplace-cold-restore-v1"
BACKUP_INVENTORY_VERSION = 2
HOST_OBSERVATION_CONTRACT = "standalone-runtime-v2"
RUNTIME_ACTIVATION_CONTRACT = "standalone-compose-direct-v1"
PERSISTENT_DOMAINS = ("clickhouse", "object_store", "postgres_app", "shared_files", "temporal")
RecoveryDirection = Literal["forward_pending", "restore_decided", "success_decided"]
RestoreState = Literal["not_needed", "running", "verified", "failed", "recovery_required"]
ForwardResult = Literal["not_started", "running", "succeeded", "failed", "interrupted"]
AutomaticPhase = Literal[
    "attempt_reserved", "quiescing", "quiesced", "archiving", "backup_sealed",
    "env_commit_pending", "env_selected", "target_create_pending", "target_created",
    "target_starting", "target_verifying", "success_decided", "success_metadata_committed", "succeeded",
    "restore_decided", "restore_quiescing", "restore_quiesced", "restore_inputs_verified",
    "restoring_volumes", "restore_files_verified", "config_revert_pending", "config_reverted",
    "restore_data_committed", "previous_create_pending", "previous_created", "previous_start_armed",
    "previous_starting", "previous_verifying", "restore_verified", "restore_metadata_committed",
    "restored", "abort_quiescence", "failed_available",
]


def _hex(value: Any, length: int = 64) -> None:
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is not None)


def _restart_evidence(counts: Mapping[str, int], policies: Mapping[str, str]) -> None:
    _require(isinstance(counts, Mapping) and isinstance(policies, Mapping) and bool(counts))
    _require(set(counts) == set(policies))
    _require(all(type(k) is str and bool(k) and type(v) is int and v >= 0 for k, v in counts.items()))
    _require(all(type(v) is str and v in ("unless-stopped", "no") for v in policies.values()),
             "unsupported_installation")


@dataclass(frozen=True)
class MigrationEvidence:
    source_inventory_sha256: str
    target_inventory_sha256: str
    source_applied_set_sha256: str
    target_applied_set_sha256: str
    active_app_mapping_sha256: str
    target_pending_plan_sha256: str
    target_pending_plan_count: int
    migration_handling: Literal["standalone_bootstrap"] = "standalone_bootstrap"

    def __post_init__(self) -> None:
        for name in ("source_inventory_sha256", "target_inventory_sha256", "source_applied_set_sha256",
                     "target_applied_set_sha256", "active_app_mapping_sha256", "target_pending_plan_sha256"):
            _hex(getattr(self, name))
        object.__setattr__(self, "target_pending_plan_count", _wire_integer(self.target_pending_plan_count, 100000))
        _require(self.migration_handling == "standalone_bootstrap")


# A6 uses logical volume roles, never downloaded Docker names or SQL identifiers.
AUTOMATIC_VOLUMES = ("app-data", "postgres-data", "clickhouse-data")
MAX_MEASUREMENT_INTEGER = 9007199254740991
MigrationEngine = Literal["postgres", "clickhouse"]
MigrationRiskClass = Literal["metadata_only", "scan", "rewrite", "index_build", "data_backfill", "other"]


def _wire_integer(value: Any, maximum: int = MAX_MEASUREMENT_INTEGER) -> int:
    """JSON Schema integers are mathematical integers: 3 and 3.0 are equivalent.

    Normalize private typed values, NEVER the authenticated/raw wire bytes.
    Booleans, fractional floats, nonfinite numbers and unsafe sizes are rejected.
    """
    import math
    _require(type(value) is int or (type(value) is float and math.isfinite(value) and value.is_integer()))
    _require(0 <= value <= maximum)
    return int(value)


def _measurement_number(value: Any, *, minimum: float = 0, exclusive: bool = False,
                        maximum: float = 1e18) -> None:
    import math
    _require(type(value) in (int, float) and minimum <= value <= maximum)
    _require(math.isfinite(value) and (not exclusive or value > minimum))


def _table_name(value: Any) -> None:
    # Unquoted schema-qualified names only. These are data, never SQL to execute.
    _require(type(value) is str and len(value) <= 129
             and re.fullmatch(r"[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}", value) is not None)


@dataclass(frozen=True)
class MigrationTableMeasurement:
    table: str
    reference_rows: int
    reference_bytes: int

    def __post_init__(self) -> None:
        _table_name(self.table)
        for name in ("reference_rows", "reference_bytes"):
            object.__setattr__(self, name, _wire_integer(getattr(self, name)))


@dataclass(frozen=True)
class MigrationThroughput:
    unit: Literal["rows_per_second", "bytes_per_second"]
    value: float

    def __post_init__(self) -> None:
        _require(self.unit in ("rows_per_second", "bytes_per_second"))
        _measurement_number(self.value, exclusive=True)


@dataclass(frozen=True)
class MigrationMeasurement:
    app_label: str
    name: str
    engine: MigrationEngine
    tables: Tuple[MigrationTableMeasurement, ...]
    risk_class: MigrationRiskClass
    duration_seconds: float
    throughput: MigrationThroughput
    peak_disk_amplification_factor: float
    wal_bytes: int
    temp_bytes: int

    def __post_init__(self) -> None:
        _require(type(self.app_label) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", self.app_label) is not None)
        _require(type(self.name) is str and re.fullmatch(r"[A-Za-z0-9_]{1,128}", self.name) is not None)
        _require(self.engine in get_args(MigrationEngine) and self.risk_class in get_args(MigrationRiskClass))
        _require(isinstance(self.tables, (tuple, list)) and len(self.tables) <= 256)
        tables = tuple(MigrationTableMeasurement(**item) if isinstance(item, Mapping) else item for item in self.tables)
        _require(all(isinstance(item, MigrationTableMeasurement) for item in tables))
        _require(len({item.table for item in tables}) == len(tables))
        _require(bool(tables) or self.risk_class in ("metadata_only", "other"))
        throughput = MigrationThroughput(**self.throughput) if isinstance(self.throughput, Mapping) else self.throughput
        _require(isinstance(throughput, MigrationThroughput))
        _measurement_number(self.duration_seconds, exclusive=True)
        _measurement_number(self.peak_disk_amplification_factor, minimum=1, maximum=1e6)
        for name in ("wal_bytes", "temp_bytes"):
            object.__setattr__(self, name, _wire_integer(getattr(self, name)))
        object.__setattr__(self, "tables", tables)
        object.__setattr__(self, "throughput", throughput)


@dataclass(frozen=True)
class BootstrapEnvelopeTableMeasurement:
    engine: MigrationEngine
    table: str
    reference_rows: int
    reference_bytes: int

    def __post_init__(self) -> None:
        _require(self.engine in get_args(MigrationEngine))
        _table_name(self.table)
        for name in ("reference_rows", "reference_bytes"):
            object.__setattr__(self, name, _wire_integer(getattr(self, name)))


@dataclass(frozen=True)
class BootstrapEnvelopeMeasurement:
    """One measured whole-bootstrap envelope, not per-migration throughput.

    WAL/temp are observed physical peaks; sampled peaks do not bound spikes
    between observations. migration_tables is the registered reference-table
    intersection of each migration's independent full scope probe, in plan order.
    The producer must validate that raw probe against the fixed registry before
    reduction; signed type validation alone cannot prove the probe occurred.
    """

    measurement_basis: Literal["normal-bootstrap-envelope-v1"]
    pending_plan: Tuple[Mapping[str, str], ...]
    migration_tables: Tuple[Mapping[str, Any], ...]
    tables: Tuple[BootstrapEnvelopeTableMeasurement, ...]
    duration_seconds: float
    peak_disk_growth_bytes: int
    wal_bytes: int
    temp_bytes: int
    peak_memory_bytes: int

    def __post_init__(self) -> None:
        _require(self.measurement_basis == "normal-bootstrap-envelope-v1")
        _require(isinstance(self.pending_plan, (tuple, list)) and len(self.pending_plan) <= 100000)
        pending_migration_plan_hash(self.pending_plan)  # Shape, identity and uniqueness.
        plan = tuple(MappingProxyType(dict(item)) for item in self.pending_plan)
        _require(isinstance(self.tables, (tuple, list)) and len(self.tables) <= 256)
        tables = tuple(BootstrapEnvelopeTableMeasurement(**item) if isinstance(item, Mapping) else item
                       for item in self.tables)
        _require(all(isinstance(item, BootstrapEnvelopeTableMeasurement) for item in tables))
        _require(len({(item.engine, item.table) for item in tables}) == len(tables))
        _require(isinstance(self.migration_tables, (tuple, list)) and len(self.migration_tables) == len(plan))
        scopes = []
        scope_union = set()
        for identity, scope in zip(plan, self.migration_tables):
            _require(isinstance(scope, Mapping) and set(scope) == {"app_label", "name", "tables"})
            _require(scope["app_label"] == identity["app_label"] and scope["name"] == identity["name"])
            _require(isinstance(scope["tables"], (tuple, list)) and len(scope["tables"]) <= 256)
            scope_tables = []
            keys = set()
            for table in scope["tables"]:
                _require(isinstance(table, Mapping) and set(table) == {"engine", "table"})
                _require(table["engine"] in get_args(MigrationEngine))
                _table_name(table["table"])
                key = (table["engine"], table["table"])
                _require(key not in keys)
                keys.add(key)
                scope_tables.append(MappingProxyType(dict(table)))
            scope_union.update(keys)
            scopes.append(MappingProxyType({"app_label": identity["app_label"], "name": identity["name"],
                                            "tables": tuple(scope_tables)}))
        _require(scope_union == {(item.engine, item.table) for item in tables})
        _measurement_number(self.duration_seconds, exclusive=True)
        for name in ("peak_disk_growth_bytes", "wal_bytes", "temp_bytes", "peak_memory_bytes"):
            object.__setattr__(self, name, _wire_integer(getattr(self, name)))
        object.__setattr__(self, "pending_plan", plan)
        object.__setattr__(self, "migration_tables", tuple(scopes))
        object.__setattr__(self, "tables", tables)


@dataclass(frozen=True)
class VolumeTransferMeasurement:
    reference_volume_bytes: Mapping[str, int]
    throughput_bytes_per_second: float

    def __post_init__(self) -> None:
        _require(isinstance(self.reference_volume_bytes, Mapping)
                 and set(self.reference_volume_bytes) == set(AUTOMATIC_VOLUMES))
        sizes = {name: _wire_integer(value) for name, value in self.reference_volume_bytes.items()}
        _require(sum(sizes.values()) > 0)
        _measurement_number(self.throughput_bytes_per_second, exclusive=True)
        object.__setattr__(self, "reference_volume_bytes", MappingProxyType(sizes))


def pending_migration_plan_hash(identities: Any) -> str:
    """Canonical ordered plan: [{app_label, name}, ...], no SQL/engine/code."""
    from .manifest import manifest_hash
    _require(isinstance(identities, (list, tuple)))
    plan = []
    for identity in identities:
        _require(isinstance(identity, Mapping) and set(identity) == {"app_label", "name"})
        _require(type(identity["app_label"]) is str
                 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", identity["app_label"]) is not None)
        _require(type(identity["name"]) is str and re.fullmatch(r"[A-Za-z0-9_]{1,128}", identity["name"]) is not None)
        plan.append(dict(identity))
    _require(len({(item["app_label"], item["name"]) for item in plan}) == len(plan))
    return manifest_hash(plan)


@dataclass(frozen=True)
class TransitionMeasurements:
    migration_evidence: MigrationEvidence
    migration_measurements: Union[Tuple[MigrationMeasurement, ...], BootstrapEnvelopeMeasurement]
    backup_measurement: VolumeTransferMeasurement
    restore_measurement: VolumeTransferMeasurement

    def __post_init__(self) -> None:
        evidence = MigrationEvidence(**self.migration_evidence) if isinstance(self.migration_evidence, Mapping) else self.migration_evidence
        _require(isinstance(evidence, MigrationEvidence))
        measurements = self.migration_measurements
        if isinstance(measurements, Mapping):
            measurements = BootstrapEnvelopeMeasurement(**measurements)
        if isinstance(measurements, BootstrapEnvelopeMeasurement):
            plan = measurements.pending_plan
        else:
            _require(isinstance(measurements, (tuple, list)))
            measurements = tuple(MigrationMeasurement(**item) if isinstance(item, Mapping) else item
                                 for item in measurements)
            _require(all(isinstance(item, MigrationMeasurement) for item in measurements))
            plan = [{"app_label": m.app_label, "name": m.name} for m in measurements]
        _require(len(plan) == evidence.target_pending_plan_count)
        _require(pending_migration_plan_hash(plan) == evidence.target_pending_plan_sha256)
        for name in ("backup_measurement", "restore_measurement"):
            item = getattr(self, name)
            item = VolumeTransferMeasurement(**item) if isinstance(item, Mapping) else item
            _require(isinstance(item, VolumeTransferMeasurement))
            object.__setattr__(self, name, item)
        _require(dict(self.backup_measurement.reference_volume_bytes) == dict(self.restore_measurement.reference_volume_bytes))
        object.__setattr__(self, "migration_evidence", evidence)
        object.__setattr__(self, "migration_measurements", measurements)


@dataclass(frozen=True)
class HostTableSize:
    engine: MigrationEngine
    table: str
    rows: int
    bytes: int

    def __post_init__(self) -> None:
        _require(self.engine in get_args(MigrationEngine))
        _table_name(self.table)
        for name in ("rows", "bytes"):
            object.__setattr__(self, name, _wire_integer(getattr(self, name)))


@dataclass(frozen=True)
class HostSizeObservation:
    table_sizes: Tuple[HostTableSize, ...]
    volume_bytes: Mapping[str, int]
    docker_free_bytes: int
    backup_free_bytes: int
    # Unknown co-location must be treated conservatively as the SAME filesystem.
    backup_shares_docker_filesystem: bool = True

    def __post_init__(self) -> None:
        _require(isinstance(self.table_sizes, (tuple, list)))
        tables = tuple(HostTableSize(**item) if isinstance(item, Mapping) else item for item in self.table_sizes)
        _require(all(isinstance(item, HostTableSize) for item in tables))
        _require(len({(item.engine, item.table) for item in tables}) == len(tables))
        _require(isinstance(self.volume_bytes, Mapping) and set(self.volume_bytes) == set(AUTOMATIC_VOLUMES))
        object.__setattr__(self, "volume_bytes", MappingProxyType({k: _wire_integer(v) for k, v in self.volume_bytes.items()}))
        for name in ("docker_free_bytes", "backup_free_bytes"):
            object.__setattr__(self, name, _wire_integer(getattr(self, name)))
        _require(type(self.backup_shares_docker_filesystem) is bool)
        object.__setattr__(self, "table_sizes", tables)


@dataclass(frozen=True)
class AutomaticUpdateEstimate:
    backup_seconds: int
    migration_seconds: int
    restore_seconds: int
    extra_disk_bytes: int
    backup_required_free_bytes: int
    docker_required_free_bytes: int
    protected_restore_reserve_bytes: int
    forward_seconds: int
    recovery_seconds: int
    total_seconds: int
    migration_deadline_seconds: int
    migration_stall_timeout_seconds: int


@dataclass(frozen=True)
class AutomaticUpdateDecision:
    allowed: bool
    reason_codes: Tuple[ReasonCode, ...]
    estimate: Optional[AutomaticUpdateEstimate] = None


@dataclass(frozen=True)
class RuntimeImage:
    image: str
    digest: str
    image_id: str


@dataclass(frozen=True, repr=False)
class RuntimeObservationV2:
    release_version: str
    manifest_sha256: str
    observed_at: datetime
    binding_sha256: str
    platform: Literal["linux/amd64", "linux/arm64"]
    product_images: Mapping[str, Tuple[RuntimeImage, ...]]
    dependency_images: Mapping[str, Tuple[RuntimeImage, ...]]
    migration_inventory_sha256: str
    applied_migrations_sha256: str
    active_app_mapping_sha256: str
    deployment_sha256: str
    env_sha256: str
    config_sha256: str
    storage_sha256: str
    container_roster_sha256: str
    restart_counts: Mapping[str, int]
    restart_policies: Mapping[str, str]
    ready: bool
    stable: bool
    contract: Literal["standalone-runtime-v2"] = HOST_OBSERVATION_CONTRACT

    def __post_init__(self) -> None:
        parse_version(self.release_version)
        _aware(self.observed_at)
        _require(self.platform in ("linux/amd64", "linux/arm64") and self.contract == HOST_OBSERVATION_CONTRACT)
        for name in ("manifest_sha256", "binding_sha256", "migration_inventory_sha256", "applied_migrations_sha256",
                     "active_app_mapping_sha256", "deployment_sha256", "env_sha256", "config_sha256",
                     "storage_sha256", "container_roster_sha256"):
            _hex(getattr(self, name))
        _require(type(self.ready) is bool and type(self.stable) is bool)
        _restart_evidence(self.restart_counts, self.restart_policies)
        for name in ("restart_counts", "restart_policies"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        for name in ("product_images", "dependency_images"):
            images = getattr(self, name)
            _require(isinstance(images, Mapping) and bool(images))
            for service, replicas in images.items():
                _require(type(service) is str and bool(service) and isinstance(replicas, (tuple, list)) and bool(replicas))
                _require(all(isinstance(image, RuntimeImage) for image in replicas))
            object.__setattr__(self, name, MappingProxyType({s: tuple(v) for s, v in images.items()}))

    @property
    def sha256(self) -> str:
        from .manifest import manifest_hash
        value = {name: getattr(self, name) for name in (
            "release_version", "manifest_sha256", "binding_sha256", "platform", "migration_inventory_sha256",
            "applied_migrations_sha256", "active_app_mapping_sha256", "deployment_sha256", "env_sha256",
            "config_sha256", "storage_sha256", "container_roster_sha256", "ready", "stable", "contract")}
        value["observed_at"] = _aware(self.observed_at).isoformat()
        for name in ("product_images", "dependency_images"):
            value[name] = {s: [{"image": i.image, "digest": i.digest, "image_id": i.image_id}
                               for i in sorted(v, key=lambda i: (i.image, i.digest, i.image_id))]
                           for s, v in getattr(self, name).items()}
        value["restart_counts"], value["restart_policies"] = dict(self.restart_counts), dict(self.restart_policies)
        return manifest_hash(value)


@dataclass(frozen=True, repr=False)
class AuthorisedTransition:
    prepared: PreparedUpdate
    source_observation: RuntimeObservationV2
    migration_evidence: MigrationEvidence
    predecessor_raw_sha256: str
    proof_sha256: str
    execution_deployment: Mapping[str, JSONValue]
    executor_sha256: str
    row_sha256: str
    verified_at: datetime
    fingerprint: str
    qualification_policy: Literal["standalone-cold-restore-v1"] = QUALIFICATION_POLICY
    runtime_activation_contract: Literal["standalone-compose-direct-v1"] = RUNTIME_ACTIVATION_CONTRACT
    measurements: Optional[TransitionMeasurements] = None

    def __post_init__(self) -> None:
        _require(self.measurements is None or (isinstance(self.measurements, TransitionMeasurements)
                 and self.measurements.migration_evidence == self.migration_evidence))
        _require(isinstance(self.prepared, PreparedUpdate) and isinstance(self.source_observation, RuntimeObservationV2)
                 and isinstance(self.migration_evidence, MigrationEvidence))
        for name in ("predecessor_raw_sha256", "proof_sha256", "executor_sha256", "row_sha256", "fingerprint"):
            _hex(getattr(self, name))
        _aware(self.verified_at)
        _require(self.qualification_policy == QUALIFICATION_POLICY
                 and self.runtime_activation_contract == RUNTIME_ACTIVATION_CONTRACT)
        _require(isinstance(self.execution_deployment, Mapping))
        object.__setattr__(self, "execution_deployment", _freeze(self.execution_deployment))


@dataclass(frozen=True, repr=False)
class BackupSeal:
    attempt_id: str
    operation_token: str
    run_id: str
    binding_sha256: str
    target_manifest_hash: str
    source_version: str
    proof_sha256: str
    inventory_sha256: str
    source_observation_sha256: str
    quiescence_evidence_sha256: str
    snapshot_at: datetime
    authorised_at: datetime
    inventory_version: Literal[2] = BACKUP_INVENTORY_VERSION
    restore_procedure_id: Literal["standalone-inplace-cold-restore-v1"] = RESTORE_PROCEDURE_ID

    def __post_init__(self) -> None:
        _hex(self.attempt_id, 32)
        _require(type(self.operation_token) is str and bool(self.operation_token)
                 and type(self.run_id) is str and bool(self.run_id))
        parse_version(self.source_version)
        for name in ("binding_sha256", "target_manifest_hash", "proof_sha256", "inventory_sha256",
                     "source_observation_sha256", "quiescence_evidence_sha256"):
            _hex(getattr(self, name))
        _require(_aware(self.snapshot_at) <= _aware(self.authorised_at))
        _require(type(self.inventory_version) is int and self.inventory_version == BACKUP_INVENTORY_VERSION
                 and self.restore_procedure_id == RESTORE_PROCEDURE_ID)


@dataclass(frozen=True, repr=False)
class ColdHold:
    attempt_id: str
    operation_token: str
    run_id: str
    binding_sha256: str
    source_observation_sha256: str
    quiescence_evidence_sha256: str
    snapshot_at: datetime
    verified_at: datetime
    daemon_identity: str
    storage_sha256: str
    stopped_container_ids: Tuple[str, ...]
    restart_counts: Mapping[str, int]
    restart_policies: Mapping[str, str]

    def __post_init__(self) -> None:
        _hex(self.attempt_id, 32)
        _require(all(type(v) is str and bool(v) for v in (self.operation_token, self.run_id, self.daemon_identity)))
        for name in ("binding_sha256", "source_observation_sha256", "quiescence_evidence_sha256", "storage_sha256"):
            _hex(getattr(self, name))
        _require(_aware(self.snapshot_at) <= _aware(self.verified_at))
        _require(isinstance(self.stopped_container_ids, (tuple, list)) and bool(self.stopped_container_ids))
        for cid in self.stopped_container_ids:
            _hex(cid)
        _require(len(set(self.stopped_container_ids)) == len(self.stopped_container_ids))
        _restart_evidence(self.restart_counts, self.restart_policies)
        _require(set(self.restart_counts) == set(self.stopped_container_ids))
        object.__setattr__(self, "stopped_container_ids", tuple(self.stopped_container_ids))
        object.__setattr__(self, "restart_counts", MappingProxyType(dict(self.restart_counts)))
        object.__setattr__(self, "restart_policies", MappingProxyType(dict(self.restart_policies)))


@dataclass(frozen=True, repr=False)
class RestorationResult:
    attempt_id: str
    outcome: Literal["restored", "failed_available", "recovery_required"]
    source_version: str
    target_version: str
    last_verified_phase: AutomaticPhase
    backup_seal: Optional[BackupSeal] = None
    observation: Optional[RuntimeObservationV2] = None
    reason_codes: Tuple[ReasonCode, ...] = ()
    guidance_id: Literal["none", "inspect_host", "review_recovery"] = "none"

    def __post_init__(self) -> None:
        _hex(self.attempt_id, 32)
        _require(self.outcome in ("restored", "failed_available", "recovery_required"))
        parse_version(self.source_version)
        parse_version(self.target_version)
        _require(self.last_verified_phase in get_args(AutomaticPhase))
        _require(self.backup_seal is None or isinstance(self.backup_seal, BackupSeal))
        _require(self.observation is None or isinstance(self.observation, RuntimeObservationV2))
        if self.backup_seal is not None:
            _require(self.backup_seal.attempt_id == self.attempt_id and self.backup_seal.source_version == self.source_version)
        if self.outcome in ("restored", "failed_available"):
            _require(self.observation is not None and self.observation.release_version == self.source_version
                     and self.observation.ready and self.observation.stable)
        if self.outcome == "restored":
            _require(self.backup_seal is not None and self.last_verified_phase in ("restore_verified", "restore_metadata_committed", "restored"))
        _require(self.guidance_id in ("none", "inspect_host", "review_recovery"))
        object.__setattr__(self, "reason_codes", _reasons(self.reason_codes))


@dataclass(frozen=True)
class RunOutcome:
    disposition: RunDisposition
    reason_codes: Tuple[ReasonCode, ...] = ()
    operation_token: Optional[str] = None  # Private correlation, never public JSON.
    target_version: Optional[str] = None

    def __post_init__(self) -> None:
        _require(self.disposition in get_args(RunDisposition))
        _require(self.operation_token is None or type(self.operation_token) is str)
        if self.target_version is not None:
            _require(type(self.target_version) is str and self.target_version == self.target_version.strip())
            try:
                parse_version(self.target_version)
            except ValueError:
                raise ContractError() from None
        object.__setattr__(self, "reason_codes", _reasons(self.reason_codes))


@dataclass(frozen=True)
class OperationEvent:
    state: OperationState
    kind: Optional[OperationKind]
    stage: Optional[OperationStage]
    reported_at: datetime
    operation_token: str  # Private CAS identity, NOT a public status field.
    reason_codes: Tuple[ReasonCode, ...] = ()
    phase: Optional[AutomaticPhase] = None

    def __post_init__(self) -> None:
        _require(self.phase is None or self.phase in get_args(AutomaticPhase))
        _require(self.state in get_args(OperationState))
        _require(self.kind is None or self.kind in get_args(OperationKind))
        _require(self.stage is None or self.stage in get_args(OperationStage))
        _require(type(self.operation_token) is str)
        _aware(self.reported_at)
        object.__setattr__(self, "reason_codes", _reasons(self.reason_codes))


def _reasons(values: Any) -> Tuple[ReasonCode, ...]:
    _require(isinstance(values, (tuple, list)))
    try:
        return tuple(ReasonCode(value) for value in values)
    except (ValueError, TypeError):
        raise ContractError() from None


class LockedContext(Protocol):
    """Adapter-supplied fresh context, only while holding the existing update lock."""

    @property
    def binding(self) -> InstallationBinding: ...

    @property
    def prepared(self) -> PreparedUpdate: ...

    @property
    def control_generation(self) -> int: ...


class AutomationGuard(Protocol):
    """S04 implements this. Exceptions refuse; observers cannot replace this guard."""

    def validate(self, boundary: GuardBoundary, locked_context: LockedContext) -> None: ...


class PublicInstallation(TypedDict):
    slot_id: str
    topology: Topology
    production: bool


ComponentName = Literal[
    "standalone", "backend", "frontend", "fi_collector", "agentcc_gateway",
    "serving", "serving_gpu", "code_executor", "simulation_runner",
]
Freshness = Literal["fresh", "stale", "unknown"]
UnavailableReason = Literal[
    "not_configured", "not_reported", "unreadable", "malformed", "unsupported_schema",
    "untrusted_file", "clock_skew", "installation_mismatch",
]


class PublicComponent(TypedDict):
    component: ComponentName
    selected_version: Optional[str]
    observed_versions: List[str]
    observation: Literal["known", "unknown", "not_deployed"]


class PublicVersions(TypedDict):
    observed_at: Optional[str]
    runtime_state: Literal["consistent", "mixed", "unknown"]
    selected_release: Optional[str]
    running_release: Optional[str]
    components: List[PublicComponent]


class PublicPolicy(TypedDict):
    channel: Literal["stable", "beta"]
    version_policy: Literal["patch", "minor", "major"]
    check_interval_seconds: int


class PublicAlwaysWindow(TypedDict):
    kind: Literal["always"]
    timezone: str


class PublicIntervalWindow(TypedDict):
    kind: Literal["interval"]
    timezone: str
    days_of_week: List[int]
    start: str
    end: str


class PublicAutomation(TypedDict):
    mode: AutomationMode
    enabled: bool
    state: Literal["not_implemented", "disabled", "manual", "enabled", "paused", "blocked"]
    paused_until: Optional[str]
    pause_indefinite: bool
    maintenance_window: Optional[Union[PublicAlwaysWindow, PublicIntervalWindow]]
    last_tick_at: Optional[str]
    next_check_at: Optional[str]
    next_action_at: Optional[str]


class PublicDiscovery(TypedDict):
    state: Literal["not_checked", "current", "available", "blocked", "failed"]
    last_attempt_at: Optional[str]
    checked_at: Optional[str]
    candidate_version: Optional[str]
    verification: Literal["not_checked", "verified", "rejected"]
    reason_codes: List[str]  # ReasonCode.value; validator enforces the closed vocabulary.


class PublicDownload(TypedDict):
    state: Literal["not_downloaded", "downloading", "downloaded", "failed", "unknown"]
    target_version: Optional[str]
    completed_at: Optional[str]
    verified_components: List[ComponentName]
    reason_codes: List[str]  # ReasonCode.value; validator enforces the closed vocabulary.


class _PublicRestoreOperation(TypedDict, total=False):
    phase: Optional[AutomaticPhase]


class PublicOperation(_PublicRestoreOperation):
    state: OperationState
    kind: Optional[OperationKind]
    stage: Optional[OperationStage]
    started_at: Optional[str]
    finished_at: Optional[str]
    reported_at: Optional[str]
    reason_codes: List[str]  # ReasonCode.value; validator enforces the closed vocabulary.


class _PublicRestoreLastRun(TypedDict, total=False):
    forward_result: ForwardResult
    restore_result: RestoreState
    effective_version: Optional[str]
    failed_target_version: Optional[str]


class PublicLastRun(_PublicRestoreLastRun):
    kind: Literal["apply", "rollback"]
    automatic: bool
    outcome: Literal[
        "succeeded", "staged", "rolled_back", "failed_before_rollout", "failed_during_rollout",
        "interrupted_before_rollout", "interrupted_during_rollout", "blocked",
    ]
    from_version: Optional[str]
    target_version: Optional[str]
    finished_at: Optional[str]
    fallback_outcome: Literal["none", "succeeded", "failed", "ineligible"]


class _PublicRestoreSafety(TypedDict, total=False):
    fallback_strategy: Literal["restore_backup", "none"]
    restore_state: RestoreState
    snapshot_at: Optional[str]
    loss_window_start: Optional[str]
    loss_window_end: Optional[str]
    loss_window_qualification: Literal["not_applicable", "bounded", "conservative", "unknown"]
    guidance_id: Literal["none", "inspect_host", "review_recovery"]


class PublicSafety(_PublicRestoreSafety):
    backup_required: bool
    backup_state: Literal["not_evaluated", "required", "reported_complete", "failed", "explicitly_skipped", "pending", "quiescing", "sealed"]
    readiness: Literal["unknown", "pending", "passed", "failed"]
    automatic_rollback: Literal["unsupported", "disabled", "eligible", "ineligible"]
    operator_action: Literal[
        "none", "inspect_host", "prepare_installation", "initialize_then_apply", "review_recovery",
        "repair_status_reporting",
    ]


class PublicStatusV1(TypedDict):
    """Mutable wire JSON, NOT private evidence or authorization; always validate."""

    schema_version: Literal[1]
    sequence: int
    generated_at: str
    installation: PublicInstallation
    versions: PublicVersions
    policy: PublicPolicy
    automation: PublicAutomation
    discovery: PublicDiscovery
    download: PublicDownload
    operation: PublicOperation
    last_run: Optional[PublicLastRun]
    safety: PublicSafety


class PublicAPIResultV1(TypedDict):
    api_schema_version: Literal[1]
    availability: Literal["available", "unavailable", "unsupported"]
    reason_code: Optional[UnavailableReason]
    freshness: Freshness
    age_seconds: Optional[int]
    discovery_freshness: Freshness
    versions_freshness: Freshness
    progress_freshness: Freshness
    snapshot: Optional[PublicStatusV1]


class PublicAPIResponseV1(TypedDict):
    status: Literal[True]
    result: PublicAPIResultV1


def validate_timezone(value: str) -> None:
    """Validate a bounded IANA key using locally available tzdata; never install it."""
    _require(type(value) is str and len(value) <= 64)
    _require(re.fullmatch(r"[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)*", value) is not None)
    if value == "UTC":
        return
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, OSError, ValueError):
        raise ContractError("timezone_unavailable") from None


def _time(value: str) -> datetime:
    try:
        _require(type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value) is not None)
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        raise ContractError() from None


def _aware(now: datetime) -> datetime:
    _require(isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None)
    return now.astimezone(timezone.utc)


def _pairs(pairs: Any) -> Dict[str, Any]:
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ContractError()


def _depth(value: Any, depth: int = 1) -> None:
    _require(depth <= MAX_PUBLIC_DEPTH)
    if isinstance(value, dict):
        for key, child in value.items():
            _require(type(key) is str)
            _depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, depth + 1)


def _decode(raw: bytes) -> Dict[str, Any]:
    _require(type(raw) is bytes and len(raw) <= MAX_PUBLIC_BYTES)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_invalid_constant)
        _depth(value)
        _require(type(value) is dict)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ContractError() from None


@lru_cache(maxsize=2)
def _schema(name: str) -> Dict[str, Any]:
    # Deferred local-only read. Importing contracts/settings does no file I/O.
    path = Path(__file__).resolve().parents[2] / "api_contracts" / "host-update" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _shape(value: Any, schema: Mapping[str, Any], root: Mapping[str, Any]) -> None:
    """Strict stdlib validator for the exact subset used by the two frozen schemas.

    This is not a general JSON Schema implementation. Tests also use actual
    jsonschema (Draft 2020-12 plus FormatChecker) to keep the boundary in parity.
    """
    if "$ref" in schema:
        _require(schema["$ref"] == "#/$defs/PublicStatusV1")
        return _shape(value, root["$defs"]["PublicStatusV1"], root)
    for union in ("anyOf", "oneOf"):
        if union in schema:
            matches = 0
            for option in schema[union]:
                try:
                    _shape(value, option, root)
                    matches += 1
                except ContractError:
                    pass
            _require(matches == 1 if union == "oneOf" else matches > 0)
            return
    kind = schema["type"]
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None)}
    _require(type(value) is types[kind])  # bool and 1.0 are not wire integers.
    if "const" in schema:
        _require(value == schema["const"])
    if "enum" in schema:
        _require(value in schema["enum"])
    if kind == "object":
        _require(set(value) == set(schema["required"]))
        _require(set(value) <= set(schema["properties"]))
        for key, item in value.items():
            _shape(item, schema["properties"][key], root)
    elif kind == "array":
        _require(schema["minItems"] <= len(value) <= schema["maxItems"])
        if schema.get("uniqueItems"):
            _require(all(item not in value[:index] for index, item in enumerate(value)))
        for item in value:
            _shape(item, schema["items"], root)
    elif kind == "string":
        _require(schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", MAX_PUBLIC_BYTES))
        if "pattern" in schema:
            _require(re.fullmatch(schema["pattern"], value) is not None)
        if schema.get("format") == "date-time":
            _time(value)
    elif kind == "integer":
        _require(schema.get("minimum", value) <= value <= schema.get("maximum", value))


def _wire(value: Any, schema_name: str) -> None:
    try:
        _depth(value)
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        _require(len(raw) <= MAX_PUBLIC_BYTES)
        schema = _schema(schema_name)
        _shape(value, schema, schema)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ContractError() from None


def validate_public_status(snapshot: Mapping[str, Any], *,
                           expected_installation: Optional[PublicInstallation] = None,
                           now: Optional[datetime] = None,
                           previous: Optional[Mapping[str, Any]] = None) -> None:
    """Validate shape plus observable semantics, optionally identity/time/history.

    Producer must separately attest actual inspections, verified receipts and
    monotonic publication CAS. A JSON validator cannot prove those private facts.
    previous compares in-memory projections to ensure a failed discovery does
    not refresh prior success; it is never permission to read the public file
    back as host authority. Supply expected_installation/now at reader boundaries.
    """
    _wire(snapshot, "public-status.v1.schema.json")
    installation = snapshot["installation"]
    _require(not installation["production"] or installation["topology"] == "distributed")
    if expected_installation is not None:
        schema = _schema("public-status.v1.schema.json")
        _shape(expected_installation, schema["properties"]["installation"], schema)
        _require(installation == expected_installation, "installation_mismatch")
    generated = _time(snapshot["generated_at"])
    if now is not None:
        _require((generated - _aware(now)).total_seconds() <= 300, "clock_skew")
    versions = snapshot["versions"]
    components = versions["components"]
    _require(len({item["component"] for item in components}) == len(components))
    active = [item for item in components if item["observation"] != "not_deployed"]
    for item in components:
        observed = item["observed_versions"]
        _require(bool(observed) if item["observation"] == "known" else not observed)
        for version in observed + ([item["selected_version"]] if item["selected_version"] else []):
            _require(version == version.strip())
            parse_version(version)
    if versions["runtime_state"] == "consistent":
        _require(bool(active) and versions["running_release"] is not None and versions["observed_at"] is not None)
        expected = parse_version(versions["running_release"])
        _require(all(item["observation"] == "known" and len(item["observed_versions"]) == 1
                     and parse_version(item["observed_versions"][0]) == expected for item in active))
    else:
        _require(versions["running_release"] is None)

    discovery = snapshot["discovery"]
    candidate = discovery["candidate_version"]
    if candidate is not None:
        _require(discovery["verification"] == "verified" and discovery["checked_at"] is not None)
    if discovery["state"] in ("current", "available"):
        _require(candidate is not None and discovery["verification"] == "verified" and discovery["checked_at"] is not None)
    if discovery["state"] == "current":
        _require(not discovery["reason_codes"])
    failed_check = discovery["state"] == "failed" or discovery["verification"] == "rejected"
    if failed_check and discovery["checked_at"] is not None:
        _require(discovery["last_attempt_at"] is not None
                 and _time(discovery["checked_at"]) <= _time(discovery["last_attempt_at"]))
    if previous is not None and failed_check:
        validate_public_status(previous)
        _require(previous["installation"] == installation, "installation_mismatch")
        _require(discovery["checked_at"] == previous["discovery"]["checked_at"])
        _require(candidate is None or candidate == previous["discovery"]["candidate_version"])

    download = snapshot["download"]
    if download["state"] == "downloaded":
        _require(candidate is not None and download["target_version"] == candidate
                 and download["completed_at"] is not None and bool(download["verified_components"]))
    if download["state"] == "not_downloaded":
        _require(download["target_version"] is None and download["completed_at"] is None
                 and not download["verified_components"])

    automation = snapshot["automation"]
    until = automation["paused_until"]
    _require(not (automation["pause_indefinite"] and until is not None))
    paused = automation["pause_indefinite"] or (until is not None and _time(until) > generated)
    state = automation["state"]
    if state == "not_implemented":
        _require(automation["mode"] == "manual" and not automation["enabled"])
    elif state != "blocked":
        effective = ("disabled" if not automation["enabled"] else "paused" if paused
                     else "manual" if automation["mode"] == "manual" else "enabled")
        _require(state == effective)
    if state in ("not_implemented", "disabled", "manual", "paused", "blocked"):
        for key in ("next_check_at", "next_action_at"):
            _require(automation[key] is None or (state == "paused" and until is not None and automation[key] == until))
    window = automation["maintenance_window"]
    if window is not None:
        try:
            validate_timezone(window["timezone"])
        except ContractError:
            # The public reader reports malformed, not host configuration
            # errors or an untrusted timezone string.
            raise ContractError() from None
        if window["kind"] == "interval":
            _require(window["start"] != window["end"])
    operation = snapshot["operation"]
    if operation["finished_at"] is not None:
        _require(operation["started_at"] is not None and _time(operation["finished_at"]) >= _time(operation["started_at"]))
    if operation["state"] in ACTIVE_OPERATION_STATES:
        _require(operation["reported_at"] is not None)
    observed_times = [versions["observed_at"], discovery["checked_at"], discovery["last_attempt_at"],
                      download["completed_at"], automation["last_tick_at"], operation["started_at"],
                      operation["finished_at"], operation["reported_at"]]
    if snapshot["last_run"] is not None:
        observed_times.append(snapshot["last_run"]["finished_at"])
    _require(all(value is None or (_time(value) - generated).total_seconds() <= 300 for value in observed_times))


def decode_public_status(raw: bytes, *, expected_installation: Optional[PublicInstallation] = None,
                         now: Optional[datetime] = None,
                         previous: Optional[Mapping[str, Any]] = None) -> PublicStatusV1:
    snapshot = _decode(raw)
    if "schema_version" in snapshot and type(snapshot["schema_version"]) is int and snapshot["schema_version"] != 1:
        raise ContractError("unsupported_schema")
    validate_public_status(snapshot, expected_installation=expected_installation, now=now, previous=previous)
    return cast(PublicStatusV1, snapshot)


def _freshness(value: Optional[str], now: datetime, maximum_age: int) -> str:
    if value is None:
        return "unknown"
    return "stale" if (now - _time(value)).total_seconds() > maximum_age else "fresh"


def validate_api_response(response: Mapping[str, Any], *, now: datetime,
                          expected_installation: Optional[PublicInstallation] = None) -> None:
    """Validate the read-only success envelope and independent as-of labels."""
    _wire(response, "api-response.v1.schema.json")
    now = _aware(now)
    result = response["result"]
    freshness_keys = ("freshness", "discovery_freshness", "versions_freshness", "progress_freshness")
    if result["availability"] != "available":
        _require(result["snapshot"] is None and result["age_seconds"] is None)
        _require(all(result[key] == "unknown" for key in freshness_keys))
        _require(result["reason_code"] is not None)
        _require((result["availability"] == "unsupported") == (result["reason_code"] == "unsupported_schema"))
        return
    _require(result["reason_code"] is None and result["snapshot"] is not None)
    snapshot = result["snapshot"]
    validate_public_status(snapshot, expected_installation=expected_installation, now=now)
    age = max(0, int((now - _time(snapshot["generated_at"])).total_seconds()))
    _require(result["age_seconds"] == age)
    _require(result["freshness"] == _freshness(snapshot["generated_at"], now, 86400))
    _require(result["discovery_freshness"] == _freshness(
        snapshot["discovery"]["checked_at"], now, snapshot["policy"]["check_interval_seconds"] + 300))
    _require(result["versions_freshness"] == _freshness(snapshot["versions"]["observed_at"], now, 86400))
    progress = snapshot["operation"]["reported_at"] if snapshot["operation"]["state"] in ACTIVE_OPERATION_STATES else None
    _require(result["progress_freshness"] == _freshness(progress, now, 300))


def decode_api_response(raw: bytes, *, now: datetime,
                        expected_installation: Optional[PublicInstallation] = None) -> PublicAPIResponseV1:
    response = _decode(raw)
    validate_api_response(response, now=now, expected_installation=expected_installation)
    return cast(PublicAPIResponseV1, response)
