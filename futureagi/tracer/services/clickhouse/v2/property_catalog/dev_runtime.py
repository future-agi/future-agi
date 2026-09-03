"""Checked-in infrastructure for the isolated property-catalog DEV runtime.

The module deliberately separates the write-capable catalog identity from the
SELECT-only canonical-span identity.  Python fence files and Go drain proofs
must live in the same non-symlink runtime directory mounted into a Python/Go
sidecar pod; a standalone one-off command pod cannot activate a revision.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from django.conf import settings

from tracer.services.clickhouse.server_readonly import ensure_read_statement
from tracer.services.clickhouse.v2 import catalog_dev_schema

from .activation import (
    ActivationInventory,
    ActivationManifest,
    ActivationResult,
    BuildPlanSourceScope,
    BuildPlanStream,
    ManifestStream,
    ManifestStreamRole,
    PropertyCatalogActivator,
    RevisionFence,
    RevisionLease,
    StreamDrainProof,
    make_revision_fence,
)
from .codec import canonical_json, canonical_json_sha256, canonical_uuid
from .coordinator import (
    MAX_REVISION_LEASE_SECONDS,
    REVISION_LEASE_SECONDS,
    AtomicSingleTenantFenceFile,
    ClickHouseRevisionCoordinator,
    ProducerRevisionAssignment,
    PropertyCatalogCoordinatorError,
)
from .dev_rollout import (
    DEV_CONTROL_PLANE_ENVIRONMENTS,
    DEV_INITIAL_BACKFILL_MAX_WALL_MS,
    DEV_SCHEDULED_RECONCILE_MAX_WALL_MS,
    DEV_STANDARD_MAX_WALL_MS,
    DevRolloutError,
    DevRolloutRequest,
    dev_control_plane_matches_request,
    is_dev_control_plane_cloud_allowed,
)
from .durable_lifecycle import (
    ClickHouseLifecycleStateReader,
    ConfiguredSourceBounds,
    DurableWorkspaceCatalogLifecycle,
    FreshSpanLifecycleCutoffFreezer,
    LifecycleRunMode,
    PreparedLifecycleRevision,
    PriorActiveEvidence,
    ReservationStatus,
    WorkspaceCatalogScope,
)
from .models import PropertyCatalogEnvelope, SourceAdapter
from .mutation_lock import (
    CatalogMutationSerializer,
    FileCatalogMutationSerializer,
)
from .postgres_executor import (
    PostgresRevisionReconcileResult,
    reconcile_postgres_revision,
)
from .producer_retirement import (
    PRODUCER_RETIREMENT_FILE_NAME,
    AtomicProducerStateRetirementFile,
    ProducerStateRetirement,
)
from .production_rollout import (
    PRODUCTION_CLOUD_DEPLOYMENTS,
    PRODUCTION_ENVIRONMENT,
    ProductionRolloutRequest,
)
from .projection import PostgresReadBudget, PostgresSnapshotContext
from .publisher import (
    PROPERTY_CATALOG_TABLES,
    CatalogWriteLease,
    ClickHouseEnvelopePublisher,
    PropertyCatalogPublishError,
    SharedCatalogDeadline,
    require_catalog_database,
    require_dev_catalog_database,
    require_prod_catalog_database,
)
from .qualification import (
    CatalogCheckpoint,
    CheckpointStatus,
    RevisionQualification,
    qualify_revision,
)
from .reconciler import (
    PropertyCatalogReconciler,
    ReconcileMode,
    ReconcileRequest,
    ReconcileResult,
)
from .runtime_contract import (
    ProducerDrainProof,
    ProducerDrainProofError,
    parse_producer_drain_proof,
    select_producer_drain_proof,
)
from .runtime_limits import RUNTIME_LIMITS
from .source_adapters import (
    PROPERTY_SOURCE_DB_ALIAS,
    DefinitionSourceAdapter,
    SourceReadBudget,
    SourceSnapshot,
    SpanAttributeDefinitionSourceAdapter,
    SystemManifestAdapter,
)
from .span_source import (
    CANONICAL_SPAN_QUERY_TIMEOUT_MS,
    DEV_CANONICAL_SPAN_PAGE_ROWS,
    DEV_INITIAL_BACKFILL_CANONICAL_SPAN_PAGE_ROWS,
    DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS,
    MAX_CANONICAL_SPAN_PAGE_ROWS,
    SPAN_AUDIT_CUTOFF_LABEL,
    AuthoritativeSpanBuild,
    AuthoritativeSpanReconciler,
    AuthoritativeSpanResult,
    AuthoritativeSpanRole,
    CanonicalSpanSourceReader,
    FrozenSpanSource,
    RevisionPinnedSpanAttributeGroupPageLoader,
    stream_requirement,
)
from .state_store import (
    ClickHouseCatalogStateStore,
    ClickHouseCurrentBindingReader,
    PropertyCatalogStateConflict,
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_QUALIFIED_TABLE_RE = re.compile(r"^`([^`]+)`\.`([^`]+)`$")
_QUALIFIED_SOURCE_RE = re.compile(r"`([^`]+)`\.`([^`]+)`")
_STANDARD_NATIVE_READ_TIMEOUT_CEILING_MS = (
    settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
)
_CREATE_TABLE_RE = re.compile(
    r"^CREATE TABLE IF NOT EXISTS ([A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.IGNORECASE,
)
DEV_SIDECAR_ACK = "PROPERTY_CATALOG_PYTHON_GO_SIDECAR_V1"
CHECKED_IN_DEV_RUNTIME_FACTORY_PATH = (
    "tracer.services.clickhouse.v2.property_catalog.dev_runtime."
    "configured_property_catalog_dev_runtime"
)
CHECKED_IN_PRODUCTION_RUNTIME_FACTORY_PATH = (
    "tracer.services.clickhouse.v2.property_catalog.dev_runtime."
    "configured_property_catalog_production_runtime"
)
_DRAIN_PROOF_FILENAME = "producer-drain-proof-v2.json"
_MAX_DRAIN_PROOF_BYTES = RUNTIME_LIMITS.drain_proof_max_bytes
_DRAIN_POLL_INTERVAL_SECONDS = RUNTIME_LIMITS.drain_poll_interval_ms / 1_000
_DRAIN_POLL_CAP_MS = RUNTIME_LIMITS.drain_poll_cap_ms
_VISIBILITY_RETRY_CAP_MS = RUNTIME_LIMITS.visibility_retry_cap_ms
_MAX_PROJECTS = RUNTIME_LIMITS.max_projects
_MIN_INITIAL_BACKFILL_LEASE_HEADROOM_MS = (
    settings.PROPERTY_CATALOG_INITIAL_BACKFILL_LEASE_HEADROOM_MS
)
MAX_EXPECTED_CLICKHOUSE_HOSTNAMES = 16
MAX_CLICKHOUSE_GRANT_EVIDENCE_ROWS = 256
_CLICKHOUSE_PROVENANCE_SQL = """
SELECT
    hostName(),
    currentDatabase(),
    currentUser(),
    toUInt64(value),
    toUInt8(readonly)
FROM system.settings
WHERE name = 'readonly'
"""
_CLICKHOUSE_WRITER_GRANTS_SQL = "SHOW GRANTS FOR CURRENT_USER"
_DIRECT_TABLE_GRANT_RE = re.compile(
    r"^GRANT (?P<access>SELECT|INSERT)(?:, (?P<second>SELECT|INSERT))? "
    r"ON `?(?P<database>[A-Za-z_][A-Za-z0-9_]*)`?\."
    r"`?(?P<table>[A-Za-z_][A-Za-z0-9_]*)`? "
    r"TO `?(?P<user>[A-Za-z_][A-Za-z0-9_]*)`?$"
)
_POSTGRES_PROVENANCE_SQL = """
SELECT
    current_database(),
    current_user,
    session_user,
    host(inet_server_addr()),
    inet_server_port(),
    roles.rolcanlogin,
    roles.rolsuper,
    roles.rolcreaterole,
    roles.rolcreatedb,
    roles.rolreplication,
    roles.rolbypassrls,
    current_setting('default_transaction_read_only'),
    current_setting('transaction_read_only'),
    (
        SELECT count(*)
        FROM pg_catalog.pg_class AS relations
        INNER JOIN pg_catalog.pg_namespace AS namespaces
            ON namespaces.oid = relations.relnamespace
        WHERE relations.relkind IN ('r', 'p', 'f', 'v', 'm')
          AND namespaces.nspname NOT IN ('pg_catalog', 'information_schema')
          AND namespaces.nspname NOT LIKE 'pg_toast%'
          AND namespaces.nspname NOT LIKE 'pg_temp_%'
          AND (
              has_table_privilege(relations.oid, 'INSERT')
              OR has_any_column_privilege(relations.oid, 'INSERT')
              OR has_table_privilege(relations.oid, 'UPDATE')
              OR has_any_column_privilege(relations.oid, 'UPDATE')
              OR has_table_privilege(relations.oid, 'DELETE')
              OR has_table_privilege(relations.oid, 'TRUNCATE')
              OR has_table_privilege(relations.oid, 'REFERENCES')
              OR has_any_column_privilege(relations.oid, 'REFERENCES')
              OR has_table_privilege(relations.oid, 'TRIGGER')
          )
    ) AS writable_relation_count
FROM pg_catalog.pg_roles AS roles
WHERE roles.rolname = current_user
"""
# Deliberately includes soft-deleted projects/workspaces: full repair must be
# able to reconcile their tombstones.  Ownership remains exact because the
# project row, its non-null workspace FK, and that workspace's organization
# are all proved in the same read-only snapshot.
_POSTGRES_PROJECT_BINDINGS_SQL = """
SELECT
    project.id::text,
    project.organization_id::text,
    project.workspace_id::text,
    workspace.organization_id::text
FROM public.tracer_project AS project
LEFT JOIN public.accounts_workspace AS workspace
    ON workspace.id = project.workspace_id
WHERE project.id = ANY(%s::uuid[])
ORDER BY project.id
"""
_PROJECT_TENANT_AUTHORITY = object()
_RUNTIME_FACTORY_AUTHORITY = object()
_LOCKED_RUNTIME_SCOPE_FIELDS = frozenset(
    {
        "bound_request",
        "config",
        "project_tenant_binding_probe",
        "provenance",
        "_factory_authority",
        "_execution",
        "_revision_project_tenant_authorization",
        "_authorized_build_binding_sha256",
        "_authorized_revision_proof",
    }
)


class PropertyCatalogDevRuntimeError(DevRolloutError):
    """The checked-in DEV runtime cannot prove its configured boundary."""


class PropertyCatalogHotDrainHandshakeUnavailable(PropertyCatalogDevRuntimeError):
    """The colocated Go producer did not complete its bounded drain handshake."""


class HotDrainProofSource(Protocol):
    """Wait for one exact prepared/ready proof from the colocated Go sidecar."""

    def wait_for(
        self,
        *,
        assignment: ProducerRevisionAssignment,
        producer_stream_id: str,
        phase: str,
    ) -> ProducerDrainProof: ...


@dataclass(slots=True)
class SharedVolumeHotDrainProofSource:
    """Read only the canonical v2 proof file under the shared shrinking wall."""

    path: str
    deadline: SharedCatalogDeadline
    poll_interval_seconds: float = _DRAIN_POLL_INTERVAL_SECONDS
    sleeper: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        _safe_runtime_file(self.path, "drain proof file")
        if Path(self.path).name != _DRAIN_PROOF_FILENAME:
            raise PropertyCatalogDevRuntimeError(
                "drain proof source does not use the fixed Go v2 filename"
            )
        if (
            type(self.poll_interval_seconds) not in {int, float}
            or isinstance(self.poll_interval_seconds, bool)
            or not 0 < self.poll_interval_seconds <= 1
        ):
            raise ValueError("drain proof poll interval must be in (0, 1] seconds")

    def wait_for(
        self,
        *,
        assignment: ProducerRevisionAssignment,
        producer_stream_id: str,
        phase: str,
    ) -> ProducerDrainProof:
        producer_stream_id = canonical_uuid(
            producer_stream_id, field="producer_stream_id"
        )
        if phase not in {"prepared", "ready"}:
            raise ValueError("hot drain phase must be prepared or ready")
        while True:
            self.deadline.remaining_ms(cap_ms=_DRAIN_POLL_CAP_MS)
            raw = self._read_once()
            if raw is not None:
                proofs = parse_producer_drain_proof(raw)
                same_workspace = tuple(
                    proof
                    for proof in proofs
                    if proof.organization_id == assignment.organization_id
                    and proof.workspace_id == assignment.workspace_id
                )
                if same_workspace:
                    if len(same_workspace) != 1:
                        raise PropertyCatalogHotDrainHandshakeUnavailable(
                            "drain proof has conflicting workspace evidence"
                        )
                    current = same_workspace[0]
                    if (
                        current.catalog_epoch != assignment.catalog_epoch
                        or current.catalog_revision != assignment.catalog_revision
                        or current.build_token != assignment.build_token
                        or current.projection_version != assignment.projection_version
                        or current.build_lease_sha256 != assignment.build_lease_sha256
                        or current.producer_stream_id != producer_stream_id
                    ):
                        raise PropertyCatalogHotDrainHandshakeUnavailable(
                            "drain proof contains stale workspace build evidence"
                        )
                    if current.poisoned or current.phase == "poisoned":
                        raise PropertyCatalogHotDrainHandshakeUnavailable(
                            "Go hot producer reported a poisoned drain"
                        )
                    waiting_phases = (
                        {"building", "preparing"}
                        if phase == "prepared"
                        else {"prepared", "bound"}
                    )
                    if current.phase == phase:
                        return select_producer_drain_proof(
                            proofs,
                            assignment=assignment,
                            producer_stream_id=producer_stream_id,
                            phase=phase,
                        )
                    if current.phase not in waiting_phases:
                        raise PropertyCatalogHotDrainHandshakeUnavailable(
                            "Go drain proof skipped or regressed its expected phase"
                        )
            remaining = self.deadline.remaining_ms(cap_ms=_DRAIN_POLL_CAP_MS)
            self.sleeper(min(self.poll_interval_seconds, remaining / 1_000))

    def _read_once(self) -> bytes | None:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise PropertyCatalogHotDrainHandshakeUnavailable(
                "drain proof file is not a safe regular file"
            ) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size < 2
                or before.st_size > _MAX_DRAIN_PROOF_BYTES
            ):
                raise PropertyCatalogHotDrainHandshakeUnavailable(
                    "drain proof file is not one bounded regular file"
                )
            chunks: list[bytes] = []
            remaining = before.st_size + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            raw = b"".join(chunks)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or len(raw) != before.st_size
            ):
                return None
            return raw
        finally:
            os.close(descriptor)


class NativeClickHouseDriver(Protocol):
    """Small surface implemented by ``tracer.services.clickhouse.ClickHouseClient``."""

    database: str
    server_enforced_readonly: bool

    def execute_read(
        self,
        query: str,
        params: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
        settings: Mapping[str, Any] | None = None,
    ) -> tuple[Sequence[Sequence[Any]], Sequence[Any], float]: ...

    def execute(
        self,
        query: str,
        params: Any = None,
        with_column_types: bool = False,
        settings: Mapping[str, Any] | None = None,
    ) -> Any: ...


def _close_native_drivers(
    drivers: Sequence[NativeClickHouseDriver],
    *,
    raise_on_error: bool,
) -> None:
    """Close every distinct native client once, including partial factories."""

    first_error: Exception | None = None
    seen: set[int] = set()
    for driver in reversed(drivers):
        identity = id(driver)
        if identity in seen:
            continue
        seen.add(identity)
        close = getattr(driver, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except Exception as exc:  # pragma: no cover - defensive client boundary
            if first_error is None:
                first_error = exc
    if first_error is not None and raise_on_error:
        raise PropertyCatalogDevRuntimeError(
            "failed to close one or more native ClickHouse clients"
        ) from first_error


@dataclass(frozen=True, slots=True)
class NativeConnectionConfig:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    database: str
    server_enforced_readonly: bool
    read_timeout_ceiling_ms: int = _STANDARD_NATIVE_READ_TIMEOUT_CEILING_MS

    def __post_init__(self) -> None:
        if (
            not isinstance(self.host, str)
            or not self.host.strip()
            or self.host != self.host.strip()
            or any(character in self.host for character in "\r\n")
        ):
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse host must be one non-empty unpadded value"
            )
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse native port must be in [1, 65535]"
            )
        if (
            not isinstance(self.user, str)
            or not self.user.strip()
            or self.user != self.user.strip()
            or any(character in self.user for character in "\r\n")
        ):
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse user must be one non-empty unpadded value"
            )
        if not isinstance(self.password, str) or any(
            character in self.password for character in "\r\n"
        ):
            raise PropertyCatalogDevRuntimeError("ClickHouse password is invalid")
        if (
            not isinstance(self.database, str)
            or _IDENTIFIER_RE.fullmatch(self.database) is None
        ):
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse database must be one safe identifier"
            )
        if type(self.server_enforced_readonly) is not bool:
            raise PropertyCatalogDevRuntimeError(
                "server_enforced_readonly must be a bool"
            )
        if self.read_timeout_ceiling_ms not in {
            _STANDARD_NATIVE_READ_TIMEOUT_CEILING_MS,
            DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS,
        }:
            allowed_ceilings = sorted(
                {
                    _STANDARD_NATIVE_READ_TIMEOUT_CEILING_MS,
                    DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS,
                }
            )
            raise PropertyCatalogDevRuntimeError(
                "native read timeout ceiling must be one of the reviewed "
                f"values {allowed_ceilings!r} ms"
            )
        if (
            self.read_timeout_ceiling_ms > _STANDARD_NATIVE_READ_TIMEOUT_CEILING_MS
            and not self.server_enforced_readonly
        ):
            raise PropertyCatalogDevRuntimeError(
                "extended native read timeout requires a server-enforced read-only identity"
            )


@dataclass(frozen=True, slots=True)
class DevProvenanceExpectation:
    """Operator-approved identities that an authoritative DEV probe must match."""

    writer_clickhouse_hostname: str
    source_clickhouse_hostname: str
    postgres_database: str
    postgres_user: str
    postgres_server_address: str
    postgres_server_port: int
    writer_clickhouse_hostnames: tuple[str, ...] | list[str] = ()
    source_clickhouse_hostnames: tuple[str, ...] | list[str] = ()

    def __post_init__(self) -> None:
        for field_name in ("postgres_database", "postgres_user"):
            _provenance_text(getattr(self, field_name), field_name)
        writer_hostnames = _clickhouse_hostname_allowlist(
            singular=self.writer_clickhouse_hostname,
            plural=self.writer_clickhouse_hostnames,
            field_name="writer_clickhouse_hostnames",
        )
        source_hostnames = _clickhouse_hostname_allowlist(
            singular=self.source_clickhouse_hostname,
            plural=self.source_clickhouse_hostnames,
            field_name="source_clickhouse_hostnames",
        )
        object.__setattr__(self, "writer_clickhouse_hostnames", writer_hostnames)
        object.__setattr__(self, "source_clickhouse_hostnames", source_hostnames)
        if len(writer_hostnames) == 1:
            object.__setattr__(
                self,
                "writer_clickhouse_hostname",
                writer_hostnames[0],
            )
        if len(source_hostnames) == 1:
            object.__setattr__(
                self,
                "source_clickhouse_hostname",
                source_hostnames[0],
            )
        object.__setattr__(
            self,
            "postgres_server_address",
            _canonical_ip(self.postgres_server_address, "postgres_server_address"),
        )
        if (
            type(self.postgres_server_port) is not int
            or not 1 <= self.postgres_server_port <= 65_535
        ):
            raise PropertyCatalogDevRuntimeError(
                "postgres_server_port must be in [1, 65535]"
            )


@dataclass(frozen=True, slots=True)
class ClickHouseDevIdentity:
    hostname: str
    database: str
    user: str
    readonly_value: int
    readonly_locked: bool

    def __post_init__(self) -> None:
        for field_name in ("hostname", "database", "user"):
            _provenance_text(getattr(self, field_name), field_name)
        if type(self.readonly_value) is not int or not 0 <= self.readonly_value <= 2:
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse readonly evidence must be 0, 1, or 2"
            )
        if type(self.readonly_locked) is not bool:
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse readonly lock evidence must be a bool"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "hostname": self.hostname,
            "readonly_locked": self.readonly_locked,
            "readonly_value": self.readonly_value,
            "user": self.user,
        }


@dataclass(frozen=True, slots=True)
class PostgresDevIdentity:
    database: str
    user: str
    session_user: str
    server_address: str
    server_port: int
    can_login: bool
    is_superuser: bool
    can_create_role: bool
    can_create_database: bool
    can_replicate: bool
    can_bypass_rls: bool
    default_transaction_read_only: bool
    transaction_read_only: bool
    writable_relation_count: int

    def __post_init__(self) -> None:
        _provenance_text(self.database, "postgres database")
        _provenance_text(self.user, "postgres user")
        _provenance_text(self.session_user, "postgres session_user")
        object.__setattr__(
            self,
            "server_address",
            _canonical_ip(self.server_address, "postgres server_address"),
        )
        if type(self.server_port) is not int or not 1 <= self.server_port <= 65_535:
            raise PropertyCatalogDevRuntimeError(
                "PostgreSQL server_port must be in [1, 65535]"
            )
        for field_name in (
            "can_login",
            "is_superuser",
            "can_create_role",
            "can_create_database",
            "can_replicate",
            "can_bypass_rls",
            "default_transaction_read_only",
            "transaction_read_only",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise PropertyCatalogDevRuntimeError(
                    f"PostgreSQL {field_name} evidence must be a bool"
                )
        if type(
            self.writable_relation_count
        ) is not int or not 0 <= self.writable_relation_count < (1 << 64):
            raise PropertyCatalogDevRuntimeError(
                "PostgreSQL writable_relation_count must be a UInt64"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "can_bypass_rls": self.can_bypass_rls,
            "can_create_database": self.can_create_database,
            "can_create_role": self.can_create_role,
            "can_login": self.can_login,
            "can_replicate": self.can_replicate,
            "database": self.database,
            "default_transaction_read_only": self.default_transaction_read_only,
            "is_superuser": self.is_superuser,
            "server_address": self.server_address,
            "server_port": self.server_port,
            "session_user": self.session_user,
            "transaction_read_only": self.transaction_read_only,
            "user": self.user,
            "writable_relation_count": self.writable_relation_count,
        }


@dataclass(frozen=True, slots=True)
class DevProvenanceObservation:
    writer_clickhouse: ClickHouseDevIdentity
    source_clickhouse: ClickHouseDevIdentity
    postgres: PostgresDevIdentity
    writer_clickhouse_grants: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.writer_clickhouse, ClickHouseDevIdentity):
            raise TypeError("writer_clickhouse must be ClickHouseDevIdentity")
        if not isinstance(self.source_clickhouse, ClickHouseDevIdentity):
            raise TypeError("source_clickhouse must be ClickHouseDevIdentity")
        if not isinstance(self.postgres, PostgresDevIdentity):
            raise TypeError("postgres must be PostgresDevIdentity")
        grants = tuple(
            sorted(
                _provenance_text(value, "ClickHouse writer grant")
                for value in self.writer_clickhouse_grants
            )
        )
        if len(grants) > MAX_CLICKHOUSE_GRANT_EVIDENCE_ROWS:
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse writer grant evidence exceeds the bounded row limit"
            )
        if len(set(grants)) != len(grants):
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse writer grant evidence contains duplicates"
            )
        object.__setattr__(self, "writer_clickhouse_grants", grants)


@dataclass(frozen=True, slots=True)
class DevProvenanceEvidence:
    observation: DevProvenanceObservation
    attested_at: datetime
    project_tenant_authorization: ProjectTenantAuthorization | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.observation, DevProvenanceObservation):
            raise TypeError("observation must be DevProvenanceObservation")
        _require_utc_runtime(self.attested_at, "DEV provenance attested_at")
        if self.project_tenant_authorization is not None and not isinstance(
            self.project_tenant_authorization,
            ProjectTenantAuthorization,
        ):
            raise TypeError(
                "project_tenant_authorization must be ProjectTenantAuthorization"
            )

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attested_at": self.attested_at.isoformat(timespec="microseconds"),
            "postgres": self.observation.postgres.as_dict(),
            "source_clickhouse": self.observation.source_clickhouse.as_dict(),
            "writer_clickhouse": self.observation.writer_clickhouse.as_dict(),
            "writer_clickhouse_grants": list(self.observation.writer_clickhouse_grants),
        }
        if self.project_tenant_authorization is not None:
            payload["project_tenant_authorization"] = (
                self.project_tenant_authorization.as_dict()
            )
        encoded = canonical_json(payload, max_bytes=64 * 1024)
        return {
            **payload,
            "attestation_sha256": canonical_json_sha256(encoded),
            "development_only": True,
        }


@dataclass(frozen=True, slots=True)
class PostgresProjectTenantBinding:
    """Canonical PostgreSQL owner tuple for one allowlisted project."""

    project_id: str = field(repr=False)
    organization_id: str = field(repr=False)
    workspace_id: str | None = field(repr=False)
    workspace_organization_id: str | None = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_id",
            canonical_uuid(self.project_id, field="project_id"),
        )
        object.__setattr__(
            self,
            "organization_id",
            canonical_uuid(self.organization_id, field="organization_id"),
        )
        if self.workspace_id is not None:
            object.__setattr__(
                self,
                "workspace_id",
                canonical_uuid(self.workspace_id, field="workspace_id"),
            )
        if self.workspace_organization_id is not None:
            object.__setattr__(
                self,
                "workspace_organization_id",
                canonical_uuid(
                    self.workspace_organization_id,
                    field="workspace_organization_id",
                ),
            )


@dataclass(frozen=True, slots=True)
class ProjectTenantAuthorization:
    """Exact allowlist-to-rollout-tenant proof frozen before any target access."""

    organization_id: str = field(repr=False)
    workspace_id: str = field(repr=False)
    project_ids: tuple[str, ...] = field(repr=False)
    authorization_contract_sha256: str
    authorized_at: datetime
    _authority: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "organization_id",
            canonical_uuid(self.organization_id, field="organization_id"),
        )
        object.__setattr__(
            self,
            "workspace_id",
            canonical_uuid(self.workspace_id, field="workspace_id"),
        )
        if not isinstance(self.project_ids, tuple):
            raise TypeError("project_ids must be a tuple")
        projects = tuple(
            sorted(
                canonical_uuid(project_id, field="project_id")
                for project_id in self.project_ids
            )
        )
        if (
            not projects
            or len(projects) > _MAX_PROJECTS
            or len(set(projects)) != len(projects)
        ):
            raise PropertyCatalogDevRuntimeError(
                "project tenant authorization requires 1.."
                f"{_MAX_PROJECTS} unique project IDs"
            )
        object.__setattr__(self, "project_ids", projects)
        if (
            not isinstance(self.authorization_contract_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.authorization_contract_sha256) is None
        ):
            raise PropertyCatalogDevRuntimeError(
                "project tenant authorization contract must be one SHA-256 digest"
            )
        _require_utc_runtime(self.authorized_at, "project tenant authorized_at")
        if self._authority is not _PROJECT_TENANT_AUTHORITY:
            raise PropertyCatalogDevRuntimeError(
                "project tenant authorization was not issued by the runtime factory"
            )

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "authorization_contract_sha256": self.authorization_contract_sha256,
            "authorized_at": self.authorized_at.isoformat(timespec="microseconds"),
        }
        return {
            "authorization_contract_sha256": self.authorization_contract_sha256,
            "authorization_sha256": canonical_json_sha256(
                canonical_json(payload, max_bytes=64 * 1024)
            ),
            "authorized_at": payload["authorized_at"],
            "project_count": len(self.project_ids),
        }


@dataclass(frozen=True, slots=True)
class DevRuntimeConfig:
    """All settings required before constructing any network client."""

    catalog: NativeConnectionConfig
    source: NativeConnectionConfig
    catalog_epoch: int
    projection_version: int
    project_ids: tuple[str, ...]
    hot_producer_stream_id: str
    mutation_lock_directory: str
    revision_fence_file: str
    drain_proof_file: str
    producer_retirement_file: str
    span_since: datetime
    span_until: datetime
    sidecar_acknowledgement: str
    provenance_expectation: DevProvenanceExpectation
    deployment: str = "dev"
    span_page_rows: int = DEV_CANONICAL_SPAN_PAGE_ROWS
    rollout_wall_ms: int = DEV_STANDARD_MAX_WALL_MS
    catalog_control_database: str = "default"
    explicit_initial_backfill_wall: bool = False
    explicit_scheduled_reconcile_wall: bool = False

    @property
    def extended_rollout_wall(self) -> bool:
        """Whether the aggregate wall also needs a lease with headroom."""

        return (
            self.explicit_initial_backfill_wall
            or self.explicit_scheduled_reconcile_wall
        )

    @property
    def span_query_timeout_ms(self) -> int:
        """Select the canonical-span cap from the validated runtime mode."""

        if self.explicit_initial_backfill_wall:
            return DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS
        return CANONICAL_SPAN_QUERY_TIMEOUT_MS

    def __post_init__(self) -> None:
        if not isinstance(self.provenance_expectation, DevProvenanceExpectation):
            raise TypeError("provenance_expectation must be DevProvenanceExpectation")
        if self.deployment == "dev":
            require_dev_catalog_database(self.catalog.database)
        elif self.deployment == "prod":
            require_prod_catalog_database(self.catalog.database)
        else:
            raise PropertyCatalogDevRuntimeError(
                "catalog runtime deployment must be dev or prod"
            )
        if self.catalog.server_enforced_readonly:
            raise PropertyCatalogDevRuntimeError(
                "catalog writes require an explicitly write-capable identity"
            )
        if not self.source.server_enforced_readonly:
            raise PropertyCatalogDevRuntimeError(
                "canonical source reads require a server-enforced read-only identity"
            )
        if self.catalog.user == self.source.user:
            raise PropertyCatalogDevRuntimeError(
                "catalog writer and canonical source reader must use distinct identities"
            )
        if self.catalog.database == self.source.database:
            raise PropertyCatalogDevRuntimeError(
                "catalog target and canonical source databases must differ"
            )
        if (
            _IDENTIFIER_RE.fullmatch(self.catalog_control_database) is None
            or self.catalog_control_database == self.catalog.database
        ):
            raise PropertyCatalogDevRuntimeError(
                "catalog control database must be one safe existing non-target database"
            )
        for field_name, value, bits in (
            ("catalog_epoch", self.catalog_epoch, 16),
            ("projection_version", self.projection_version, 16),
        ):
            if type(value) is not int or not 1 <= value < (1 << bits):
                raise PropertyCatalogDevRuntimeError(
                    f"{field_name} must be a positive UInt{bits}"
                )
        projects = tuple(
            sorted(
                {
                    canonical_uuid(project_id, field="project_id")
                    for project_id in self.project_ids
                }
            )
        )
        if (
            not projects
            or len(projects) > _MAX_PROJECTS
            or len(projects) != len(self.project_ids)
        ):
            raise PropertyCatalogDevRuntimeError(
                "project allowlist must contain 1.."
                f"{_MAX_PROJECTS} unique canonical UUIDs"
            )
        object.__setattr__(self, "project_ids", projects)
        object.__setattr__(
            self,
            "hot_producer_stream_id",
            canonical_uuid(
                self.hot_producer_stream_id,
                field="hot_producer_stream_id",
            ),
        )
        _existing_directory(self.mutation_lock_directory, "mutation lock directory")
        _safe_runtime_file(self.revision_fence_file, "revision fence file")
        _safe_runtime_file(self.drain_proof_file, "drain proof file")
        _safe_runtime_file(
            self.producer_retirement_file,
            "producer retirement file",
        )
        runtime_files = {
            Path(self.revision_fence_file),
            Path(self.drain_proof_file),
            Path(self.producer_retirement_file),
        }
        if len(runtime_files) != 3:
            raise PropertyCatalogDevRuntimeError(
                "revision fence, drain proof, and retirement files must be distinct"
            )
        shared_directory = Path(self.mutation_lock_directory).resolve(strict=True)
        for value, name in (
            (self.revision_fence_file, "revision fence file"),
            (self.drain_proof_file, "drain proof file"),
            (self.producer_retirement_file, "producer retirement file"),
        ):
            if Path(value).parent.resolve(strict=True) != shared_directory:
                raise PropertyCatalogDevRuntimeError(
                    f"{name} must be inside the exact shared Python/Go sidecar "
                    "runtime directory"
                )
        if Path(self.drain_proof_file).name != _DRAIN_PROOF_FILENAME:
            raise PropertyCatalogDevRuntimeError(
                "drain proof file must use the Go runtime's fixed v2 filename"
            )
        if Path(self.producer_retirement_file).name != PRODUCER_RETIREMENT_FILE_NAME:
            raise PropertyCatalogDevRuntimeError(
                "producer retirement file must use the Go runtime's fixed v1 filename"
            )
        if self.sidecar_acknowledgement != DEV_SIDECAR_ACK:
            raise PropertyCatalogDevRuntimeError(
                "the exact Python/Go shared-sidecar acknowledgement is required"
            )
        _utc_hour(self.span_since, "span_since")
        _utc_hour(self.span_until, "span_until")
        if self.span_since >= self.span_until:
            raise PropertyCatalogDevRuntimeError("span_since must precede span_until")
        hours = int((self.span_until - self.span_since).total_seconds() // 3600)
        if not 1 <= hours <= 366 * 24:
            raise PropertyCatalogDevRuntimeError(
                "canonical span window must contain 1 hour to 366 days"
            )
        if (
            type(self.span_page_rows) is not int
            or not 1 <= self.span_page_rows <= MAX_CANONICAL_SPAN_PAGE_ROWS
        ):
            raise PropertyCatalogDevRuntimeError(
                "canonical span page rows must be in [1, 1024]"
            )
        if type(self.explicit_initial_backfill_wall) is not bool:
            raise PropertyCatalogDevRuntimeError(
                "explicit_initial_backfill_wall must be a bool"
            )
        if type(self.explicit_scheduled_reconcile_wall) is not bool:
            raise PropertyCatalogDevRuntimeError(
                "explicit_scheduled_reconcile_wall must be a bool"
            )
        if (
            self.explicit_initial_backfill_wall
            and self.explicit_scheduled_reconcile_wall
        ):
            raise PropertyCatalogDevRuntimeError(
                "initial backfill and scheduled reconcile walls are mutually exclusive"
            )
        if type(self.rollout_wall_ms) is not int:
            raise PropertyCatalogDevRuntimeError("rollout wall must be an integer")
        if self.extended_rollout_wall:
            if not (
                DEV_STANDARD_MAX_WALL_MS
                < self.rollout_wall_ms
                <= min(
                    DEV_INITIAL_BACKFILL_MAX_WALL_MS,
                    DEV_SCHEDULED_RECONCILE_MAX_WALL_MS,
                )
            ):
                raise PropertyCatalogDevRuntimeError(
                    "explicit extended rollout wall must be in "
                    f"[{DEV_STANDARD_MAX_WALL_MS + 1}, "
                    f"{min(DEV_INITIAL_BACKFILL_MAX_WALL_MS, DEV_SCHEDULED_RECONCILE_MAX_WALL_MS)}] ms"
                )
            lease_headroom_ms = (
                MAX_REVISION_LEASE_SECONDS * 1_000 - self.rollout_wall_ms
            )
            if lease_headroom_ms < _MIN_INITIAL_BACKFILL_LEASE_HEADROOM_MS:
                raise PropertyCatalogDevRuntimeError(
                    "explicit extended rollout wall must leave at least "
                    f"{_MIN_INITIAL_BACKFILL_LEASE_HEADROOM_MS} ms of revision "
                    "lease headroom"
                )
        elif not 1 <= self.rollout_wall_ms <= DEV_STANDARD_MAX_WALL_MS:
            raise PropertyCatalogDevRuntimeError(
                f"rollout wall must be in [1, {DEV_STANDARD_MAX_WALL_MS}] ms"
            )

    @classmethod
    def from_settings(
        cls,
        request: DevRolloutRequest,
        settings_object: Any,
        *,
        now: datetime | None = None,
    ) -> DevRuntimeConfig:
        """Load only explicit DEV writer settings plus the CH25 source route."""

        if not isinstance(request, DevRolloutRequest):
            raise TypeError("request must satisfy the checked-in rollout contract")
        production = isinstance(request, ProductionRolloutRequest)
        environment = str(getattr(settings_object, "ENV_TYPE", "")).strip().lower()
        cloud_deployment = str(getattr(settings_object, "CLOUD_DEPLOYMENT", "")).strip()
        if production:
            if (
                environment not in {"prod", PRODUCTION_ENVIRONMENT}
                or cloud_deployment not in PRODUCTION_CLOUD_DEPLOYMENTS
                or request.environment != PRODUCTION_ENVIRONMENT
                or request.cloud_deployment != cloud_deployment
            ):
                raise PropertyCatalogDevRuntimeError(
                    "production runtime ENV_TYPE/CLOUD_DEPLOYMENT differs from the "
                    "validated production request"
                )
        else:
            if environment not in DEV_CONTROL_PLANE_ENVIRONMENTS:
                raise PropertyCatalogDevRuntimeError(
                    "runtime refuses non-DEV ENV_TYPE before constructing a client"
                )
            if not is_dev_control_plane_cloud_allowed(
                environment=environment,
                cloud_deployment=cloud_deployment,
            ):
                raise PropertyCatalogDevRuntimeError(
                    "runtime requires CLOUD_DEPLOYMENT=DEV, or an unset "
                    "CLOUD_DEPLOYMENT only when ENV_TYPE=development"
                )
            if not dev_control_plane_matches_request(
                environment=environment,
                cloud_deployment=cloud_deployment,
                request_environment=request.environment,
                request_cloud_deployment=request.cloud_deployment,
            ):
                raise PropertyCatalogDevRuntimeError(
                    "runtime ENV_TYPE/CLOUD_DEPLOYMENT differs from the validated request"
                )
        legacy = _mapping_setting(settings_object, "CLICKHOUSE")
        v2 = _mapping_setting(settings_object, "CLICKHOUSE_V2")

        def source_value(key: str, legacy_key: str, default: Any = "") -> Any:
            value = v2.get(key)
            return (
                legacy.get(legacy_key, default)
                if value is None or value == ""
                else value
            )

        source_database = source_value(
            "CH25_DATABASE", "CH_DATABASE", request.source_database
        )
        if source_database != request.source_database:
            raise PropertyCatalogDevRuntimeError(
                "configured CH25 source database differs from the validated request"
            )
        source_readonly = _strict_bool(
            source_value(
                "CH25_SERVER_ENFORCED_READONLY",
                "CH_SERVER_ENFORCED_READONLY",
                False,
            ),
            "CH25_SERVER_ENFORCED_READONLY",
        )
        explicit_initial_wall_ms = request.initial_backfill_wall_ms
        explicit_scheduled_wall_ms = request.scheduled_reconcile_wall_ms
        source = NativeConnectionConfig(
            host=source_value("CH25_HOST", "CH_HOST"),
            port=_strict_port(
                source_value("CH25_TCP_PORT", "CH_PORT", 9000),
                "CH25_TCP_PORT",
            ),
            user=source_value("CH25_USER", "CH_USERNAME", "default"),
            password=source_value("CH25_PASSWORD", "CH_PASSWORD", ""),
            database=source_database,
            server_enforced_readonly=source_readonly,
            read_timeout_ceiling_ms=(
                DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS
                if explicit_initial_wall_ms is not None
                else _STANDARD_NATIVE_READ_TIMEOUT_CEILING_MS
            ),
        )

        target_database = _required_text(
            settings_object,
            "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE",
        )
        if target_database != request.target_database:
            raise PropertyCatalogDevRuntimeError(
                "configured catalog write database differs from the validated request"
            )
        catalog = NativeConnectionConfig(
            host=_required_text(
                settings_object,
                "PROPERTY_CATALOG_DEV_WRITE_CH_HOST",
            ),
            port=_strict_port(
                getattr(settings_object, "PROPERTY_CATALOG_DEV_WRITE_CH_PORT", None),
                "PROPERTY_CATALOG_DEV_WRITE_CH_PORT",
            ),
            user=_required_text(
                settings_object,
                "PROPERTY_CATALOG_DEV_WRITE_CH_USER",
            ),
            password=_password_setting(
                settings_object,
                "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD",
            ),
            database=target_database,
            server_enforced_readonly=False,
        )
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None or observed_at.utcoffset() != UTC.utcoffset(
            observed_at
        ):
            raise PropertyCatalogDevRuntimeError("runtime clock must be UTC-aware")
        configured_wall_ms = _strict_positive_int_setting(
            settings_object,
            "PROPERTY_CATALOG_DEV_MAX_WALL_MS",
            default=DEV_STANDARD_MAX_WALL_MS,
        )
        if configured_wall_ms > DEV_STANDARD_MAX_WALL_MS:
            raise PropertyCatalogDevRuntimeError(
                "PROPERTY_CATALOG_DEV_MAX_WALL_MS must remain in "
                f"[1, {DEV_STANDARD_MAX_WALL_MS}] ms; "
                "only --initial-backfill-wall-ms may extend an explicit backfill"
            )
        writer_clickhouse_hostnames = _expected_clickhouse_hostnames_setting(
            settings_object,
            plural_name="PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAMES",
            singular_name="PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME",
        )
        source_clickhouse_hostnames = _expected_clickhouse_hostnames_setting(
            settings_object,
            plural_name="PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAMES",
            singular_name="PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME",
        )
        return cls(
            catalog=catalog,
            source=source,
            deployment="prod" if production else "dev",
            catalog_epoch=_strict_positive_int_setting(
                settings_object,
                "PROPERTY_CATALOG_DEV_CATALOG_EPOCH",
            ),
            projection_version=_strict_positive_int_setting(
                settings_object,
                "PROPERTY_CATALOG_DEV_PROJECTION_VERSION",
            ),
            project_ids=_project_allowlist_setting(settings_object),
            hot_producer_stream_id=_required_text(
                settings_object,
                "PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID",
            ),
            mutation_lock_directory=_required_text(
                settings_object,
                "PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY",
            ),
            revision_fence_file=_required_text(
                settings_object,
                "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE",
            ),
            drain_proof_file=_required_text(
                settings_object,
                "PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE",
            ),
            producer_retirement_file=_required_text(
                settings_object,
                "PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE",
            ),
            span_since=_datetime_setting(
                settings_object,
                "PROPERTY_CATALOG_DEV_SPAN_SINCE",
            ),
            span_until=_datetime_setting(
                settings_object,
                "PROPERTY_CATALOG_DEV_SPAN_UNTIL",
            ),
            sidecar_acknowledgement=_required_text(
                settings_object,
                "PROPERTY_CATALOG_DEV_SIDECAR_ACK",
            ),
            provenance_expectation=DevProvenanceExpectation(
                writer_clickhouse_hostname=(
                    writer_clickhouse_hostnames[0]
                    if len(writer_clickhouse_hostnames) == 1
                    else ""
                ),
                source_clickhouse_hostname=(
                    source_clickhouse_hostnames[0]
                    if len(source_clickhouse_hostnames) == 1
                    else ""
                ),
                postgres_database=_required_text(
                    settings_object,
                    "PROPERTY_CATALOG_DEV_EXPECTED_PG_DATABASE",
                ),
                postgres_user=_required_text(
                    settings_object,
                    "PROPERTY_CATALOG_DEV_EXPECTED_PG_USER",
                ),
                postgres_server_address=_required_text(
                    settings_object,
                    "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS",
                ),
                postgres_server_port=_strict_port(
                    getattr(
                        settings_object,
                        "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT",
                        None,
                    ),
                    "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT",
                ),
                writer_clickhouse_hostnames=writer_clickhouse_hostnames,
                source_clickhouse_hostnames=source_clickhouse_hostnames,
            ),
            span_page_rows=_strict_positive_int_setting(
                settings_object,
                "PROPERTY_CATALOG_DEV_SPAN_PAGE_ROWS",
                default=(
                    DEV_INITIAL_BACKFILL_CANONICAL_SPAN_PAGE_ROWS
                    if explicit_initial_wall_ms is not None
                    else DEV_CANONICAL_SPAN_PAGE_ROWS
                ),
            ),
            rollout_wall_ms=(
                explicit_initial_wall_ms
                if explicit_initial_wall_ms is not None
                else (
                    explicit_scheduled_wall_ms
                    if explicit_scheduled_wall_ms is not None
                    else configured_wall_ms
                )
            ),
            explicit_initial_backfill_wall=explicit_initial_wall_ms is not None,
            explicit_scheduled_reconcile_wall=(explicit_scheduled_wall_ms is not None),
        )


DevProvenanceProbe = Callable[
    [DevRuntimeConfig, NativeClickHouseDriver, NativeClickHouseDriver],
    DevProvenanceObservation,
]


def _default_dev_provenance_probe(
    config: DevRuntimeConfig,
    writer_driver: NativeClickHouseDriver,
    source_driver: NativeClickHouseDriver,
) -> DevProvenanceObservation:
    """Read authoritative identities without touching application/source rows."""

    if not isinstance(config, DevRuntimeConfig):
        raise TypeError("config must be DevRuntimeConfig")
    return DevProvenanceObservation(
        writer_clickhouse=_clickhouse_dev_identity(
            writer_driver,
            require_server_readonly=False,
        ),
        source_clickhouse=_clickhouse_dev_identity(
            source_driver,
            require_server_readonly=True,
        ),
        postgres=_postgres_dev_identity(),
        writer_clickhouse_grants=(
            _clickhouse_writer_grants(writer_driver)
            if config.deployment == "prod"
            else ()
        ),
    )


def _clickhouse_dev_identity(
    driver: NativeClickHouseDriver,
    *,
    require_server_readonly: bool,
) -> ClickHouseDevIdentity:
    ensure_read_statement(_CLICKHOUSE_PROVENANCE_SQL)
    configured_readonly = getattr(driver, "server_enforced_readonly", None)
    if configured_readonly is not require_server_readonly:
        raise PropertyCatalogDevRuntimeError(
            "ClickHouse provenance driver has the wrong readonly identity"
        )
    if require_server_readonly:
        rows, _columns, _elapsed = driver.execute_read(
            _CLICKHOUSE_PROVENANCE_SQL,
            {},
            timeout_ms=RUNTIME_LIMITS.canonical_span_query_timeout_ms,
            settings={},
        )
    else:
        # execute_read deliberately injects readonly=2 for ordinary clients,
        # which would mask the writer profile. This exact constant is a SELECT;
        # use the raw driver surface so the server's session default is observed.
        rows = driver.execute(_CLICKHOUSE_PROVENANCE_SQL)
    if len(rows) != 1 or len(rows[0]) != 5:
        raise PropertyCatalogDevRuntimeError(
            "ClickHouse DEV provenance query did not return one complete row"
        )
    row = rows[0]
    return ClickHouseDevIdentity(
        hostname=_observed_text(row[0], "ClickHouse hostname"),
        database=_observed_text(row[1], "ClickHouse database"),
        user=_observed_text(row[2], "ClickHouse user"),
        readonly_value=_observed_uint(row[3], "ClickHouse readonly value", maximum=2),
        readonly_locked=_observed_bool(row[4], "ClickHouse readonly lock"),
    )


def _clickhouse_writer_grants(
    driver: NativeClickHouseDriver,
) -> tuple[str, ...]:
    """Read the authenticated writer's own grants without SHOW ACCESS."""

    rows = driver.execute(_CLICKHOUSE_WRITER_GRANTS_SQL)
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise PropertyCatalogDevRuntimeError(
            "ClickHouse writer grant query returned an invalid result"
        )
    grants: list[str] = []
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse writer grant query returned an invalid row"
            )
        if len(row) != 1:
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse writer grant query returned an incomplete row"
            )
        grants.append(_observed_text(row[0], "ClickHouse writer grant"))
    return tuple(grants)


def _postgres_dev_identity() -> PostgresDevIdentity:
    from django.db import connections

    connection = connections[PROPERTY_SOURCE_DB_ALIAS]
    if connection.vendor != "postgresql":
        raise PropertyCatalogDevRuntimeError(
            "PostgreSQL DEV provenance requires the default PostgreSQL connection"
        )
    if connection.in_atomic_block:
        raise PropertyCatalogDevRuntimeError(
            "PostgreSQL DEV provenance cannot inherit an existing transaction"
        )
    with connection.cursor() as cursor:
        cursor.execute(_POSTGRES_PROVENANCE_SQL)
        row = cursor.fetchone()
        extra = cursor.fetchone()
    if row is None or extra is not None or len(row) != 14:
        raise PropertyCatalogDevRuntimeError(
            "PostgreSQL DEV provenance query did not return one complete role row"
        )
    return _postgres_dev_identity_from_row(row)


def _postgres_dev_identity_from_row(row: Sequence[Any]) -> PostgresDevIdentity:
    if len(row) != 14:
        raise PropertyCatalogDevRuntimeError(
            "PostgreSQL DEV provenance row is incomplete"
        )
    return PostgresDevIdentity(
        database=_observed_text(row[0], "PostgreSQL database"),
        user=_observed_text(row[1], "PostgreSQL user"),
        session_user=_observed_text(row[2], "PostgreSQL session user"),
        server_address=_observed_text(row[3], "PostgreSQL server address"),
        server_port=_observed_uint(
            row[4], "PostgreSQL server port", maximum=65_535, minimum=1
        ),
        can_login=_observed_bool(row[5], "PostgreSQL rolcanlogin"),
        is_superuser=_observed_bool(row[6], "PostgreSQL rolsuper"),
        can_create_role=_observed_bool(row[7], "PostgreSQL rolcreaterole"),
        can_create_database=_observed_bool(row[8], "PostgreSQL rolcreatedb"),
        can_replicate=_observed_bool(row[9], "PostgreSQL rolreplication"),
        can_bypass_rls=_observed_bool(row[10], "PostgreSQL rolbypassrls"),
        default_transaction_read_only=_observed_on_off(
            row[11], "PostgreSQL default_transaction_read_only"
        ),
        transaction_read_only=_observed_on_off(
            row[12], "PostgreSQL transaction_read_only"
        ),
        writable_relation_count=_observed_uint(
            row[13], "PostgreSQL writable relation count"
        ),
    )


def _postgres_project_tenant_bindings(
    project_ids: tuple[str, ...],
    expected_postgres_identity: PostgresDevIdentity,
) -> tuple[PostgresProjectTenantBinding, ...]:
    """Re-prove PG identity and read canonical project tenants in one snapshot."""

    if not isinstance(project_ids, tuple):
        raise TypeError("project_ids must be a tuple")
    projects = tuple(
        sorted(
            canonical_uuid(project_id, field="project_id") for project_id in project_ids
        )
    )
    if (
        not projects
        or len(projects) > _MAX_PROJECTS
        or len(set(projects)) != len(projects)
    ):
        raise PropertyCatalogDevRuntimeError(
            f"project ownership probe requires 1..{_MAX_PROJECTS} unique project IDs"
        )
    if not isinstance(expected_postgres_identity, PostgresDevIdentity):
        raise TypeError("expected_postgres_identity must be PostgresDevIdentity")

    from django.db import connections, transaction

    connection = connections[PROPERTY_SOURCE_DB_ALIAS]
    if connection.vendor != "postgresql":
        raise PropertyCatalogDevRuntimeError(
            "project ownership authorization requires PostgreSQL"
        )
    if connection.in_atomic_block:
        raise PropertyCatalogDevRuntimeError(
            "project ownership authorization cannot inherit a transaction"
        )
    try:
        with transaction.atomic(using=PROPERTY_SOURCE_DB_ALIAS):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                cursor.execute("SET LOCAL statement_timeout = '8000ms'")
                cursor.execute(_POSTGRES_PROVENANCE_SQL)
                identity_row = cursor.fetchone()
                identity_extra = cursor.fetchone()
                if identity_row is None or identity_extra is not None:
                    raise PropertyCatalogDevRuntimeError(
                        "project ownership snapshot has incomplete PostgreSQL identity"
                    )
                if (
                    _postgres_dev_identity_from_row(identity_row)
                    != expected_postgres_identity
                ):
                    raise PropertyCatalogDevRuntimeError(
                        "project ownership snapshot changed PostgreSQL identity"
                    )
                cursor.execute(_POSTGRES_PROJECT_BINDINGS_SQL, (list(projects),))
                rows = cursor.fetchall()
    except PropertyCatalogDevRuntimeError:
        raise
    except Exception as exc:
        raise PropertyCatalogDevRuntimeError(
            "canonical PostgreSQL project ownership probe failed"
        ) from exc

    return _parse_postgres_project_tenant_rows(rows)


def _postgres_project_tenant_bindings_in_current_snapshot(
    project_ids: tuple[str, ...],
    expected_postgres_identity: PostgresDevIdentity,
) -> tuple[PostgresProjectTenantBinding, ...]:
    """Run the ownership proof inside the active revision-wide PG snapshot."""

    projects = tuple(
        sorted(
            canonical_uuid(project_id, field="project_id") for project_id in project_ids
        )
    )
    if (
        not projects
        or len(projects) > _MAX_PROJECTS
        or len(set(projects)) != len(projects)
    ):
        raise PropertyCatalogDevRuntimeError(
            f"project ownership probe requires 1..{_MAX_PROJECTS} unique project IDs"
        )
    if not isinstance(expected_postgres_identity, PostgresDevIdentity):
        raise TypeError("expected_postgres_identity must be PostgresDevIdentity")

    from django.db import connections

    connection = connections[PROPERTY_SOURCE_DB_ALIAS]
    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        raise PropertyCatalogDevRuntimeError(
            "revision project ownership proof requires the active PostgreSQL snapshot"
        )
    try:
        with connection.cursor() as cursor:
            cursor.execute(_POSTGRES_PROVENANCE_SQL)
            identity_row = cursor.fetchone()
            identity_extra = cursor.fetchone()
            if identity_row is None or identity_extra is not None:
                raise PropertyCatalogDevRuntimeError(
                    "revision project ownership snapshot has incomplete PostgreSQL identity"
                )
            if (
                _postgres_dev_identity_from_row(identity_row)
                != expected_postgres_identity
            ):
                raise PropertyCatalogDevRuntimeError(
                    "revision project ownership snapshot changed PostgreSQL identity"
                )
            cursor.execute(_POSTGRES_PROJECT_BINDINGS_SQL, (list(projects),))
            rows = cursor.fetchall()
    except PropertyCatalogDevRuntimeError:
        raise
    except Exception as exc:
        raise PropertyCatalogDevRuntimeError(
            "revision PostgreSQL project ownership proof failed"
        ) from exc
    return _parse_postgres_project_tenant_rows(rows)


def _parse_postgres_project_tenant_rows(
    rows: Sequence[Sequence[Any]],
) -> tuple[PostgresProjectTenantBinding, ...]:
    bindings: list[PostgresProjectTenantBinding] = []
    try:
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 4:
                raise ValueError("incomplete project binding row")
            bindings.append(
                PostgresProjectTenantBinding(
                    project_id=str(row[0]),
                    organization_id=str(row[1]),
                    workspace_id=None if row[2] is None else str(row[2]),
                    workspace_organization_id=(None if row[3] is None else str(row[3])),
                )
            )
    except (TypeError, ValueError) as exc:
        raise PropertyCatalogDevRuntimeError(
            "canonical PostgreSQL project ownership probe returned invalid rows"
        ) from exc
    return tuple(bindings)


def _authorize_project_tenant_bindings(
    *,
    request: DevRolloutRequest,
    config: Any,
    observation: DevProvenanceObservation,
    bindings: Sequence[PostgresProjectTenantBinding],
    authorized_at: datetime,
) -> ProjectTenantAuthorization:
    """Require a complete one-to-one owner match for the rollout allowlist."""

    if not isinstance(request, DevRolloutRequest):
        raise TypeError("request must be a DevRolloutRequest")
    if not isinstance(observation, DevProvenanceObservation):
        raise TypeError("observation must be a DevProvenanceObservation")
    organization_id = request.organization_id
    workspace_id = request.workspace_id
    expected = tuple(
        sorted(
            canonical_uuid(project_id, field="project_id")
            for project_id in config.project_ids
        )
    )
    if (
        not expected
        or len(expected) > _MAX_PROJECTS
        or len(set(expected)) != len(expected)
    ):
        raise PropertyCatalogDevRuntimeError(
            "project tenant authorization requires 1.."
            f"{_MAX_PROJECTS} unique project IDs"
        )
    if any(
        not isinstance(binding, PostgresProjectTenantBinding) for binding in bindings
    ):
        raise TypeError("project binding probe returned an invalid binding")
    observed_ids = tuple(binding.project_id for binding in bindings)
    if (
        len(observed_ids) != len(set(observed_ids))
        or tuple(sorted(observed_ids)) != expected
    ):
        raise PropertyCatalogDevRuntimeError(
            "project allowlist is missing exact canonical PostgreSQL ownership"
        )
    if any(
        binding.organization_id != organization_id
        or binding.workspace_id != workspace_id
        or binding.workspace_organization_id != organization_id
        for binding in bindings
    ):
        raise PropertyCatalogDevRuntimeError(
            "project allowlist is not owned by the exact rollout organization/workspace"
        )
    return ProjectTenantAuthorization(
        organization_id=organization_id,
        workspace_id=workspace_id,
        project_ids=expected,
        authorization_contract_sha256=_project_tenant_authorization_contract_sha256(
            request=request,
            config=config,
            observation=observation,
        ),
        authorized_at=authorized_at,
        _authority=_PROJECT_TENANT_AUTHORITY,
    )


def _project_tenant_authorization_contract_sha256(
    *,
    request: DevRolloutRequest,
    config: Any,
    observation: DevProvenanceObservation,
) -> str:
    """Bind authorization to every immutable request/config/identity input."""

    def connection_payload(value: NativeConnectionConfig) -> dict[str, Any]:
        return {
            "database": value.database,
            "host": value.host,
            "port": value.port,
            "read_timeout_ceiling_ms": value.read_timeout_ceiling_ms,
            "server_enforced_readonly": value.server_enforced_readonly,
            "user": value.user,
        }

    payload = {
        "config": {
            "catalog": connection_payload(config.catalog),
            "catalog_control_database": config.catalog_control_database,
            "catalog_epoch": config.catalog_epoch,
            "drain_proof_file": config.drain_proof_file,
            "explicit_initial_backfill_wall": config.explicit_initial_backfill_wall,
            "explicit_scheduled_reconcile_wall": (
                config.explicit_scheduled_reconcile_wall
            ),
            "hot_producer_stream_id": config.hot_producer_stream_id,
            "mutation_lock_directory": config.mutation_lock_directory,
            "producer_retirement_file": config.producer_retirement_file,
            "project_ids": list(config.project_ids),
            "projection_version": config.projection_version,
            "provenance_expectation": {
                "postgres_database": config.provenance_expectation.postgres_database,
                "postgres_server_address": (
                    config.provenance_expectation.postgres_server_address
                ),
                "postgres_server_port": (
                    config.provenance_expectation.postgres_server_port
                ),
                "postgres_user": config.provenance_expectation.postgres_user,
                "source_clickhouse_hostnames": list(
                    config.provenance_expectation.source_clickhouse_hostnames
                ),
                "writer_clickhouse_hostnames": list(
                    config.provenance_expectation.writer_clickhouse_hostnames
                ),
            },
            "revision_fence_file": config.revision_fence_file,
            "rollout_wall_ms": config.rollout_wall_ms,
            "sidecar_acknowledgement": config.sidecar_acknowledgement,
            "source": connection_payload(config.source),
            "span_page_rows": config.span_page_rows,
            "span_since": config.span_since.isoformat(timespec="microseconds"),
            "span_until": config.span_until.isoformat(timespec="microseconds"),
        },
        "remote_identity": {
            "postgres": observation.postgres.as_dict(),
            "source_clickhouse": observation.source_clickhouse.as_dict(),
            "writer_clickhouse": observation.writer_clickhouse.as_dict(),
            "writer_clickhouse_grants": list(observation.writer_clickhouse_grants),
        },
        "request": {
            "acknowledgement": request.acknowledgement,
            "cloud_deployment": request.cloud_deployment,
            "dev_identity": request.dev_identity,
            "environment": request.environment,
            "execute": request.execute,
            "initial_backfill_wall_ms": request.initial_backfill_wall_ms,
            "scheduled_reconcile_wall_ms": request.scheduled_reconcile_wall_ms,
            "organization_id": request.organization_id,
            "source_database": request.source_database,
            "status": request.status,
            "target_database": request.target_database,
            "workspace_id": request.workspace_id,
        },
        "v": 1,
    }
    return canonical_json_sha256(canonical_json(payload, max_bytes=64 * 1024))


def _authorized_build_binding_sha256(
    *,
    authorization: ProjectTenantAuthorization,
    build_lease_sha256: str,
) -> str:
    if not isinstance(authorization, ProjectTenantAuthorization):
        raise TypeError("authorization must be ProjectTenantAuthorization")
    if re.fullmatch(r"[0-9a-f]{64}", build_lease_sha256) is None:
        raise PropertyCatalogDevRuntimeError(
            "authorized build lease must be one SHA-256 digest"
        )
    return canonical_json_sha256(
        canonical_json(
            {
                "authorization_contract_sha256": (
                    authorization.authorization_contract_sha256
                ),
                "build_lease_sha256": build_lease_sha256,
                "v": 1,
            },
            max_bytes=64 * 1024,
        )
    )


def _validate_dev_provenance(
    *,
    config: DevRuntimeConfig,
    observation: DevProvenanceObservation,
    attested_at: datetime,
) -> DevProvenanceEvidence:
    if not isinstance(observation, DevProvenanceObservation):
        raise TypeError("DEV provenance probe must return DevProvenanceObservation")
    expected = config.provenance_expectation
    writer = observation.writer_clickhouse
    source = observation.source_clickhouse
    postgres = observation.postgres
    checks = (
        (
            writer.hostname in expected.writer_clickhouse_hostnames,
            "writer_clickhouse_hostname",
        ),
        (
            writer.database == config.catalog_control_database,
            "writer_clickhouse_database",
        ),
        (writer.user == config.catalog.user, "writer_clickhouse_user"),
        (writer.readonly_value == 0, "writer_clickhouse_readonly"),
        (
            source.hostname in expected.source_clickhouse_hostnames,
            "source_clickhouse_hostname",
        ),
        (source.database == config.source.database, "source_clickhouse_database"),
        (source.user == config.source.user, "source_clickhouse_user"),
        (source.readonly_value == 1, "source_clickhouse_readonly"),
        (source.readonly_locked, "source_clickhouse_readonly_lock"),
        (postgres.database == expected.postgres_database, "postgres_database"),
        (postgres.user == expected.postgres_user, "postgres_user"),
        (postgres.session_user == expected.postgres_user, "postgres_session_user"),
        (
            postgres.server_address == expected.postgres_server_address,
            "postgres_server_address",
        ),
        (postgres.server_port == expected.postgres_server_port, "postgres_server_port"),
        (postgres.can_login, "postgres_login"),
        (not postgres.is_superuser, "postgres_superuser"),
        (not postgres.can_create_role, "postgres_create_role"),
        (not postgres.can_create_database, "postgres_create_database"),
        (not postgres.can_replicate, "postgres_replication"),
        (not postgres.can_bypass_rls, "postgres_bypass_rls"),
        (
            postgres.default_transaction_read_only,
            "postgres_default_transaction_read_only",
        ),
        (postgres.transaction_read_only, "postgres_transaction_read_only"),
        (postgres.writable_relation_count == 0, "postgres_dml_privileges"),
    )
    mismatches = tuple(label for valid, label in checks if not valid)
    if mismatches:
        raise PropertyCatalogDevRuntimeError(
            "remote DEV provenance mismatch: " + ", ".join(mismatches)
        )
    if config.deployment == "prod":
        _validate_production_writer_grants(
            observation.writer_clickhouse_grants,
            database=config.catalog.database,
            user=config.catalog.user,
        )
    return DevProvenanceEvidence(observation=observation, attested_at=attested_at)


def _validate_production_writer_grants(
    grants: Sequence[str],
    *,
    database: str,
    user: str,
) -> None:
    """Require direct table-exact DML grants for the isolated catalog only."""

    require_prod_catalog_database(database)
    if _IDENTIFIER_RE.fullmatch(user) is None:
        raise PropertyCatalogDevRuntimeError(
            "production ClickHouse writer user must be one safe identifier"
        )
    observed: set[tuple[str, str]] = set()
    for grant in grants:
        match = _DIRECT_TABLE_GRANT_RE.fullmatch(grant)
        if match is None:
            raise PropertyCatalogDevRuntimeError(
                "production ClickHouse writer has a non-catalog or delegated grant"
            )
        if match.group("database") != database or match.group("user") != user:
            raise PropertyCatalogDevRuntimeError(
                "production ClickHouse writer grant identity is outside the catalog"
            )
        table = match.group("table")
        if table not in PROPERTY_CATALOG_TABLES:
            raise PropertyCatalogDevRuntimeError(
                "production ClickHouse writer grant targets a non-catalog table"
            )
        access = {match.group("access")}
        if match.group("second") is not None:
            access.add(match.group("second"))
        if access != {"SELECT", "INSERT"}:
            raise PropertyCatalogDevRuntimeError(
                "production ClickHouse writer requires SELECT and INSERT together"
            )
        observed.add((database, table))
    expected = {(database, table) for table in PROPERTY_CATALOG_TABLES}
    if observed != expected or len(grants) != len(expected):
        raise PropertyCatalogDevRuntimeError(
            "production ClickHouse writer grants must cover every catalog table exactly"
        )


class NativeCatalogClient:
    """Qualified six-table writer/reader over one dedicated native client."""

    def __init__(self, driver: NativeClickHouseDriver, *, database: str) -> None:
        self.catalog_database = require_catalog_database(database)
        self._driver = driver
        self._validate_identity()

    def query(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Sequence[Mapping[str, Any]]:
        self._validate_identity()
        _bounded_catalog_timeout(timeout_ms)
        ensure_read_statement(sql)
        qualified = set(_QUALIFIED_SOURCE_RE.findall(sql))
        if not qualified or any(
            database != self.catalog_database or table not in PROPERTY_CATALOG_TABLES
            for database, table in qualified
        ):
            raise PropertyCatalogDevRuntimeError(
                "catalog reads may reference only the exact six qualified DEV tables"
            )
        rows, columns, _ = self._driver.execute_read(
            sql,
            params,
            timeout_ms=timeout_ms,
            settings={"readonly": 2},
        )
        names = tuple(
            str(column[0]) if isinstance(column, tuple) else str(column)
            for column in columns
        )
        if len(names) != len(set(names)):
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse returned duplicate catalog column names"
            )
        return tuple(dict(zip(names, row, strict=True)) for row in rows)

    def insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None:
        self._validate_identity()
        _bounded_catalog_timeout(timeout_ms)
        match = _QUALIFIED_TABLE_RE.fullmatch(table)
        if (
            match is None
            or match.group(1) != self.catalog_database
            or match.group(2) not in PROPERTY_CATALOG_TABLES
        ):
            raise PropertyCatalogDevRuntimeError(
                "catalog insert target is outside the exact six-table allowlist"
            )
        ordered_columns = tuple(columns)
        if (
            not ordered_columns
            or len(set(ordered_columns)) != len(ordered_columns)
            or any(_COLUMN_RE.fullmatch(column) is None for column in ordered_columns)
        ):
            raise PropertyCatalogDevRuntimeError("catalog insert columns are invalid")
        if not isinstance(deduplication_token, str) or not (
            1 <= len(deduplication_token.encode("utf-8")) <= 1024
        ):
            raise PropertyCatalogDevRuntimeError(
                "catalog insert deduplication token is invalid"
            )
        encoded_rows: list[tuple[Any, ...]] = []
        for row in rows:
            if set(row) != set(ordered_columns):
                raise PropertyCatalogDevRuntimeError(
                    "catalog insert row does not exactly match its columns"
                )
            encoded_rows.append(tuple(row[column] for column in ordered_columns))
        if not encoded_rows:
            return
        column_sql = ", ".join(ordered_columns)
        self._driver.execute(
            f"INSERT INTO {table} ({column_sql}) VALUES",
            encoded_rows,
            settings={
                "insert_deduplication_token": deduplication_token,
                "max_execution_time": timeout_ms / 1000,
            },
        )

    def _validate_identity(self) -> None:
        require_catalog_database(self.catalog_database)
        if getattr(self._driver, "database", None) != self.catalog_database:
            raise PropertyCatalogDevRuntimeError(
                "native catalog client identity changed databases"
            )
        if getattr(self._driver, "server_enforced_readonly", None) is not False:
            raise PropertyCatalogDevRuntimeError(
                "native catalog client is not the explicit write-capable identity"
            )


class NativeSourceClient:
    """SELECT-only native adapter closed over one canonical ``spans`` table."""

    def __init__(
        self,
        driver: NativeClickHouseDriver,
        *,
        source_database: str,
        catalog_database: str,
        explicit_initial_backfill: bool = False,
    ) -> None:
        if _IDENTIFIER_RE.fullmatch(source_database) is None:
            raise PropertyCatalogDevRuntimeError("source database is invalid")
        self.source_database = source_database
        self._catalog_database = require_catalog_database(catalog_database)
        if source_database == catalog_database:
            raise PropertyCatalogDevRuntimeError(
                "source and catalog databases must differ"
            )
        if type(explicit_initial_backfill) is not bool:
            raise PropertyCatalogDevRuntimeError(
                "explicit initial-backfill source mode must be a bool"
            )
        self._explicit_initial_backfill = explicit_initial_backfill
        self._driver = driver
        self._validate_identity()

    def query(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        timeout_ms: int,
        settings: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]:
        self._validate_identity()
        _bounded_canonical_span_timeout(
            timeout_ms,
            explicit_initial_backfill=self._explicit_initial_backfill,
        )
        ensure_read_statement(sql)
        qualified = set(_QUALIFIED_SOURCE_RE.findall(sql))
        if qualified != {(self.source_database, "spans")}:
            raise PropertyCatalogDevRuntimeError(
                "canonical source reads may reference only the exact CH25 spans table"
            )
        if f"`{self._catalog_database}`" in sql:
            raise PropertyCatalogDevRuntimeError(
                "canonical source client cannot read catalog tables"
            )
        query_settings = dict(settings)
        query_settings["readonly"] = 2
        rows, columns, _ = self._driver.execute_read(
            sql,
            params,
            timeout_ms=timeout_ms,
            settings=query_settings,
        )
        names = tuple(
            str(column[0]) if isinstance(column, tuple) else str(column)
            for column in columns
        )
        if len(names) != len(set(names)):
            raise PropertyCatalogDevRuntimeError(
                "ClickHouse returned duplicate source column names"
            )
        return tuple(dict(zip(names, row, strict=True)) for row in rows)

    def _validate_identity(self) -> None:
        if getattr(self._driver, "database", None) != self.source_database:
            raise PropertyCatalogDevRuntimeError(
                "native source client identity changed databases"
            )
        if getattr(self._driver, "server_enforced_readonly", None) is not True:
            raise PropertyCatalogDevRuntimeError(
                "native source client is not server-enforced read-only"
            )


class NativeSchemaClient:
    """Native adapter for the pinned create-or-verify schema boundary."""

    def __init__(
        self,
        *,
        target_database: str,
        control_database: str,
        client_for_database: Callable[[str], NativeClickHouseDriver],
        deployment: str,
    ) -> None:
        if deployment not in {"dev", "prod"}:
            raise PropertyCatalogDevRuntimeError(
                "schema client deployment must be dev or prod"
            )
        self._target_database = (
            require_dev_catalog_database(target_database)
            if deployment == "dev"
            else require_prod_catalog_database(target_database)
        )
        self._deployment = deployment
        if (
            _IDENTIFIER_RE.fullmatch(control_database) is None
            or control_database == target_database
        ):
            raise PropertyCatalogDevRuntimeError("schema control database is invalid")
        self._control_database = control_database
        self._client_for_database = client_for_database

    def query_rows(
        self,
        sql: str,
        *,
        database: str | None = None,
    ) -> Sequence[Sequence[object]]:
        selected = self._selected_database(database)
        ensure_read_statement(sql)
        driver = self._driver(selected)
        rows, _, _ = driver.execute_read(
            sql,
            {},
            timeout_ms=RUNTIME_LIMITS.state_store_timeout_ms,
            settings={"readonly": 2},
        )
        return rows

    def command(self, sql: str, *, database: str | None = None) -> None:
        if self._deployment == "prod":
            raise PropertyCatalogDevRuntimeError(
                "production lifecycle schema client is verify-only"
            )
        selected = self._selected_database(database)
        normalized = " ".join(sql.strip().rstrip(";").split())
        if database is None:
            expected = f"CREATE DATABASE IF NOT EXISTS {self._target_database}"
            if normalized != expected:
                raise PropertyCatalogDevRuntimeError(
                    "schema control context may create only the exact DEV database"
                )
        else:
            table_match = _CREATE_TABLE_RE.match(normalized)
            if (
                ";" in normalized
                or table_match is None
                or table_match.group(1) not in PROPERTY_CATALOG_TABLES
            ):
                raise PropertyCatalogDevRuntimeError(
                    "target schema context accepts only the six pinned CREATE TABLE "
                    "statements"
                )
        self._driver(selected).execute(sql)

    def _selected_database(self, database: str | None) -> str:
        if database is None:
            return self._control_database
        if database != self._target_database:
            raise PropertyCatalogDevRuntimeError(
                "schema client was asked to cross the isolated DEV target"
            )
        return self._target_database

    def _driver(self, database: str) -> NativeClickHouseDriver:
        driver = self._client_for_database(database)
        if getattr(driver, "database", None) != database:
            raise PropertyCatalogDevRuntimeError(
                "native schema client identity changed databases"
            )
        if getattr(driver, "server_enforced_readonly", None) is not False:
            raise PropertyCatalogDevRuntimeError(
                "native schema client is not the explicit write-capable identity"
            )
        return driver


def verify_dev_catalog_schema(
    client: catalog_dev_schema.CatalogDevClickHouseClient,
    *,
    target_database: str,
) -> Mapping[str, Any]:
    """Read-only proof of the pinned bytes and exact six-table DEV topology."""

    require_dev_catalog_database(target_database)
    return _schema_evidence(
        catalog_dev_schema.verify_catalog_dev_schema(
            client,
            target_database=target_database,
        )
    )


def verify_runtime_catalog_schema(
    client: catalog_dev_schema.CatalogDevClickHouseClient,
    *,
    target_database: str,
    deployment: str,
) -> Mapping[str, Any]:
    """Read-only schema proof for an admitted DEV or production runtime."""

    if deployment == "dev":
        require_dev_catalog_database(target_database)
    elif deployment == "prod":
        require_prod_catalog_database(target_database)
    else:
        raise PropertyCatalogDevRuntimeError(
            "catalog schema deployment must be dev or prod"
        )
    return _schema_evidence(
        catalog_dev_schema.verify_catalog_schema(
            client,
            target_database=target_database,
            deployment=deployment,
        )
    )


def ensure_dev_catalog_schema(
    client: catalog_dev_schema.CatalogDevClickHouseClient,
    *,
    target_database: str,
) -> Mapping[str, Any]:
    """Create an absent/empty target once, otherwise perform read-only verify."""

    require_dev_catalog_database(target_database)
    return _schema_evidence(
        catalog_dev_schema.ensure_catalog_dev_schema(
            client,
            target_database=target_database,
            development_sentinel=catalog_dev_schema.DEVELOPMENT_SENTINEL,
        )
    )


@dataclass(frozen=True, slots=True)
class _DefinitionPublisherRouter:
    publishers: Mapping[SourceAdapter, ClickHouseEnvelopePublisher]

    def publish(self, envelope: PropertyCatalogEnvelope) -> str:
        publisher = self.publishers.get(envelope.source_adapter)
        if publisher is None:
            raise PropertyCatalogDevRuntimeError(
                "definition envelope has no exact planned publisher"
            )
        return publisher.publish(envelope)


@dataclass(slots=True)
class _RevisionExecution:
    prepared: PreparedLifecycleRevision
    mode: ReconcileMode
    frozen: FrozenSpanSource
    lease: RevisionLease
    context: PostgresSnapshotContext
    planned_by_role: Mapping[tuple[SourceAdapter, ManifestStreamRole], BuildPlanStream]
    publishers_by_role: Mapping[
        tuple[SourceAdapter, ManifestStreamRole], ClickHouseEnvelopePublisher
    ]
    reconciler: PropertyCatalogReconciler
    emitted_at: datetime
    source_budget: SourceReadBudget | None = None
    authoritative: AuthoritativeSpanResult | None = None
    postgres: PostgresRevisionReconcileResult | None = None
    definition_results: dict[SourceAdapter, ReconcileResult] = field(
        default_factory=dict
    )
    checkpoints: dict[tuple[SourceAdapter, str], CatalogCheckpoint] = field(
        default_factory=dict
    )
    manifest: ActivationManifest | None = None
    qualification: RevisionQualification | None = None
    fence: RevisionFence | None = None
    activation: ActivationResult | None = None

    def stream(
        self, source_adapter: SourceAdapter, role: ManifestStreamRole
    ) -> BuildPlanStream:
        try:
            return self.planned_by_role[(source_adapter, role)]
        except KeyError as exc:
            raise PropertyCatalogDevRuntimeError(
                f"build plan has no {source_adapter}/{role} stream"
            ) from exc

    def publisher(
        self, source_adapter: SourceAdapter, role: ManifestStreamRole
    ) -> ClickHouseEnvelopePublisher:
        try:
            return self.publishers_by_role[(source_adapter, role)]
        except KeyError as exc:
            raise PropertyCatalogDevRuntimeError(
                f"build plan has no Python publisher for {source_adapter}/{role}"
            ) from exc


@dataclass(frozen=True, slots=True)
class _AuthorizedRevisionProof:
    """Immutable authorization binding for every executable revision field."""

    authorization_contract_sha256: str
    authorized_build_binding_sha256: str
    build_lease_sha256: str
    build_plan_sha256: str
    span_since_us: int
    span_until_us: int
    snapshot_cutoff_us: int
    reconcile_mode: ReconcileMode
    emitted_at_us: int
    planned_roles: tuple[tuple[str, str, str, str, int], ...]

    @property
    def sha256(self) -> str:
        return canonical_json_sha256(
            canonical_json(
                {
                    "authorization_contract_sha256": (
                        self.authorization_contract_sha256
                    ),
                    "authorized_build_binding_sha256": (
                        self.authorized_build_binding_sha256
                    ),
                    "build_lease_sha256": self.build_lease_sha256,
                    "build_plan_sha256": self.build_plan_sha256,
                    "emitted_at_us": self.emitted_at_us,
                    "planned_roles": [list(value) for value in self.planned_roles],
                    "reconcile_mode": self.reconcile_mode.value,
                    "snapshot_cutoff_us": self.snapshot_cutoff_us,
                    "span_since_us": self.span_since_us,
                    "span_until_us": self.span_until_us,
                    "v": 1,
                },
                max_bytes=64 * 1024,
            )
        )


def _make_authorized_revision_proof(
    *,
    authorization: ProjectTenantAuthorization,
    lease: RevisionLease,
    frozen: FrozenSpanSource,
    context: PostgresSnapshotContext,
    mode: ReconcileMode,
    emitted_at: datetime,
    planned_by_role: Mapping[tuple[SourceAdapter, ManifestStreamRole], BuildPlanStream],
) -> _AuthorizedRevisionProof:
    planned_roles = tuple(
        sorted(
            (
                source_adapter.value,
                role.value,
                stream.producer_stream_id,
                stream.source_cutoff_label,
                stream.source_version_fence,
            )
            for (source_adapter, role), stream in planned_by_role.items()
        )
    )
    return _AuthorizedRevisionProof(
        authorization_contract_sha256=(authorization.authorization_contract_sha256),
        authorized_build_binding_sha256=_authorized_build_binding_sha256(
            authorization=authorization,
            build_lease_sha256=lease.build_lease_sha256,
        ),
        build_lease_sha256=lease.build_lease_sha256,
        build_plan_sha256=lease.build_plan.sha256,
        span_since_us=_unix_microseconds(frozen.since),
        span_until_us=_unix_microseconds(frozen.until),
        snapshot_cutoff_us=_unix_microseconds(context.snapshot_cutoff),
        reconcile_mode=mode,
        emitted_at_us=_unix_microseconds(emitted_at),
        planned_roles=planned_roles,
    )


@dataclass(slots=True)
class CheckedInPropertyCatalogDevRuntime:
    """Concrete DEV-only six-table lifecycle with a physical hot-drain proof."""

    config: DevRuntimeConfig = field(repr=False)
    bound_request: DevRolloutRequest = field(repr=False)
    provenance: DevProvenanceEvidence = field(repr=False)
    schema_client: NativeSchemaClient
    catalog_client: NativeCatalogClient
    source_client: NativeSourceClient
    serializer: CatalogMutationSerializer
    deadline: SharedCatalogDeadline
    state_store: ClickHouseCatalogStateStore
    coordinator: ClickHouseRevisionCoordinator
    lifecycle: DurableWorkspaceCatalogLifecycle
    span_reader: CanonicalSpanSourceReader
    hot_proof_source: HotDrainProofSource
    now: Callable[[], datetime]
    new_build_token: Callable[[], str]
    project_tenant_binding_probe: ProjectTenantBindingProbe | None = field(
        default=None,
        repr=False,
    )
    _factory_authority: object | None = field(default=None, repr=False)
    lifecycle_state: ClickHouseLifecycleStateReader | None = None
    producer_retirement_sink: AtomicProducerStateRetirementFile | None = None
    _execution: _RevisionExecution | None = None
    _revision_project_tenant_authorization: ProjectTenantAuthorization | None = field(
        default=None,
        repr=False,
    )
    _authorized_build_binding_sha256: str | None = field(default=None, repr=False)
    _authorized_revision_proof: _AuthorizedRevisionProof | None = field(
        default=None,
        repr=False,
    )
    _native_drivers: tuple[NativeClickHouseDriver, ...] = field(
        default=(),
        repr=False,
    )
    _scope_locked: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self._factory_authority is not _RUNTIME_FACTORY_AUTHORITY:
            raise PropertyCatalogDevRuntimeError(
                "checked-in runtime must be constructed by its reviewed factory"
            )
        if self.bound_request.execute and not callable(
            self.project_tenant_binding_probe
        ):
            raise PropertyCatalogDevRuntimeError(
                "execute runtime requires its factory-owned PostgreSQL project probe"
            )
        if self.bound_request.execute:
            authorization = self._require_project_tenant_authorization()
            if self._execution is not None:
                self._validate_execution_authorization(
                    self._execution,
                    authorization,
                )
        object.__setattr__(self, "_scope_locked", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _LOCKED_RUNTIME_SCOPE_FIELDS and getattr(
            self, "_scope_locked", False
        ):
            raise AttributeError(f"runtime scope field {name} is immutable")
        object.__setattr__(self, name, value)

    def close(self) -> None:
        """Release factory-owned native clients; safe to call more than once."""

        drivers = self._native_drivers
        object.__setattr__(self, "_native_drivers", ())
        _close_native_drivers(drivers, raise_on_error=True)

    def status(self, request: DevRolloutRequest) -> Mapping[str, Any]:
        self._validate_request(request)
        if not request.status or request.execute:
            raise PropertyCatalogDevRuntimeError(
                "status requires the bound status-mode request"
            )
        self._refresh_project_tenant_authorization()
        target_tables: list[dict[str, str]] = []
        schema_ready = False
        schema_issue: str | None = None
        try:
            schema = verify_runtime_catalog_schema(
                self.schema_client,
                target_database=request.target_database,
                deployment=self.config.deployment,
            )
            target_tables = _status_target_tables(schema)
            schema_ready = True
        except catalog_dev_schema.CatalogDevSchemaError as exc:
            schema_issue = str(exc)
        result: dict[str, Any] = {
            "active": False,
            "schema_ready": schema_ready,
            "target_database": request.target_database,
            "target_tables": target_tables,
            "remote_dev_provenance": self.provenance.as_dict(),
            "write_capable_runtime": True,
            "hot_drain_contract_configured": True,
            "hot_drain_protocol_version": 2,
            "hot_drain_handshake_state": "not_started",
            "hot_drain_handshake_ready": False,
        }
        if schema_issue is not None:
            result["schema_issue"] = schema_issue
        if not schema_ready:
            return result
        rows = self.catalog_client.query(
            "SELECT count() AS activation_rows, "
            "countIf(status='active') AS active_rows, "
            "maxIf(catalog_revision, status='active') AS latest_active_revision "
            f"FROM `{request.target_database}`.`property_catalog_activations` "
            "WHERE organization_id=%(organization_id)s "
            "AND workspace_id=%(workspace_id)s",
            {
                "organization_id": request.organization_id,
                "workspace_id": request.workspace_id,
            },
            timeout_ms=RUNTIME_LIMITS.state_store_timeout_ms,
        )
        if len(rows) != 1:
            raise PropertyCatalogDevRuntimeError(
                "activation status query did not return one row"
            )
        activation_rows = _nonnegative_int(
            rows[0].get("activation_rows"), "activation_rows"
        )
        active_rows = _nonnegative_int(rows[0].get("active_rows"), "active_rows")
        latest = _nonnegative_int(
            rows[0].get("latest_active_revision"),
            "latest_active_revision",
        )
        result.update(
            {
                "activation_rows": activation_rows,
                "active": active_rows > 0,
                "active_rows": active_rows,
                "latest_active_revision": latest or None,
            }
        )
        return result

    def verify_schema(self, request: DevRolloutRequest) -> Mapping[str, Any]:
        self._validate_request(request)
        if not request.execute or request.status:
            raise PropertyCatalogDevRuntimeError(
                "schema verification requires the bound execute-mode request"
            )
        self._refresh_project_tenant_authorization()
        return _evidence_with_provenance(
            verify_runtime_catalog_schema(
                self.schema_client,
                target_database=request.target_database,
                deployment=self.config.deployment,
            ),
            self.provenance,
        )

    def apply_schema(self, request: DevRolloutRequest) -> Mapping[str, Any]:
        self._validate_mutation_request(request, "apply_schema")
        self._refresh_project_tenant_authorization()
        schema = (
            ensure_dev_catalog_schema(
                self.schema_client,
                target_database=request.target_database,
            )
            if self.config.deployment == "dev"
            else verify_runtime_catalog_schema(
                self.schema_client,
                target_database=request.target_database,
                deployment="prod",
            )
        )
        return _evidence_with_provenance(schema, self.provenance)

    def backfill(self, request: DevRolloutRequest) -> Mapping[str, Any]:
        self._validate_mutation_request(request, "backfill")
        self._refresh_project_tenant_authorization()
        execution = self._prepare_revision(LifecycleRunMode.INITIAL_BACKFILL)
        authorization = self._revision_project_tenant_authorization
        if authorization is None:
            raise PropertyCatalogDevRuntimeError(
                "revision has no live PostgreSQL project tenant authorization"
            )
        if execution.prepared.reservation_status is ReservationStatus.FENCED:
            values = execution.checkpoints[
                execution.stream(
                    SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.VALUES
                ).key
            ]
            audit = execution.checkpoints[
                execution.stream(
                    SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.SOURCE_AUDIT
                ).key
            ]
            return {
                "audit_generation": execution.frozen.audit_generation,
                "authorized_build_binding_sha256": (
                    self._authorized_build_binding_sha256 or ""
                ),
                "authoritative_source_count": values.source_count,
                "authoritative_value_rows": values.value_count,
                "build_lease_sha256": execution.lease.build_lease_sha256,
                "build_token": execution.lease.build_token,
                "catalog_revision": execution.lease.catalog_revision,
                "fenced_recovery": True,
                "project_tenant_authorization_sha256": authorization.as_dict()[
                    "authorization_sha256"
                ],
                "project_tenant_contract_sha256": (
                    authorization.authorization_contract_sha256
                ),
                "source_audit_digest": audit.source_digest,
                "terminal_streams": 2,
            }
        # The canonical span table carries only project_id. Re-prove the exact
        # tenant binding immediately before any span row can be stamped with
        # rollout organization/workspace labels and published.
        self._refresh_project_tenant_authorization()
        result = self._run_authoritative_span(execution)
        return {
            "audit_generation": execution.frozen.audit_generation,
            "authorized_build_binding_sha256": (
                self._authorized_build_binding_sha256 or ""
            ),
            "authoritative_source_count": result.values.source_count,
            "authoritative_value_rows": result.values.value_count,
            "build_lease_sha256": execution.lease.build_lease_sha256,
            "build_token": execution.lease.build_token,
            "catalog_revision": execution.lease.catalog_revision,
            "fenced_recovery": False,
            "project_tenant_authorization_sha256": authorization.as_dict()[
                "authorization_sha256"
            ],
            "project_tenant_contract_sha256": (
                authorization.authorization_contract_sha256
            ),
            "source_audit_digest": result.source_audit.source_digest,
            "terminal_streams": 2,
        }

    def reconcile_workspace(
        self,
        request: DevRolloutRequest,
        *,
        mode: ReconcileMode,
    ) -> Mapping[str, Any]:
        self._validate_mutation_request(request, "reconcile_workspace")
        self._refresh_project_tenant_authorization()
        if not isinstance(mode, ReconcileMode):
            raise TypeError("mode must be a ReconcileMode")
        verify_runtime_catalog_schema(
            self.schema_client,
            target_database=request.target_database,
            deployment=self.config.deployment,
        )
        lifecycle_mode = (
            LifecycleRunMode.AUTO
            if mode is ReconcileMode.INCREMENTAL
            else LifecycleRunMode.FULL_REPAIR
        )
        execution = self._prepare_revision(lifecycle_mode)
        if execution.prepared.reservation_status is ReservationStatus.FENCED:
            qualification = execution.qualification
            assert qualification is not None and qualification.qualified
            activation = self.activate(request)
            return {
                **activation,
                "build_lease_sha256": execution.lease.build_lease_sha256,
                "lifecycle_mode": str(execution.prepared.lifecycle_mode),
                "lineage_anchor_revision": execution.prepared.lineage_anchor_revision,
                "qualified": True,
                "resumed": True,
            }
        # The canonical span table carries only project_id. Keep the live PG
        # ownership proof adjacent to the first source read/publish boundary.
        self._refresh_project_tenant_authorization()
        authoritative = self._run_authoritative_span(execution)
        # Re-prove ownership after the potentially long span pass and
        # immediately before the revision-wide PostgreSQL snapshot can publish.
        self._refresh_project_tenant_authorization()
        postgres = reconcile_postgres_revision(
            reconciler=execution.reconciler,
            request_factory=lambda adapter: self._definition_request(
                execution, adapter.source_adapter
            ),
            snapshot_guard=self.postgres_snapshot_guard(request),
        )
        self.reconcile_non_postgres(request, postgres)
        # The scheduled path calls the internal fence helper directly; keep
        # the same live ownership gate as the public qualify stage.
        self._refresh_project_tenant_authorization()
        qualification = self._qualify_and_fence(execution)
        activation = self.activate(request)
        return {
            **activation,
            "authoritative_source_count": authoritative.values.source_count,
            "build_lease_sha256": execution.lease.build_lease_sha256,
            "lifecycle_mode": str(execution.prepared.lifecycle_mode),
            "lineage_anchor_revision": execution.prepared.lineage_anchor_revision,
            "qualified": qualification.qualified,
            "resumed": execution.prepared.resumed,
        }

    def postgres_reconciler(
        self, request: DevRolloutRequest
    ) -> PropertyCatalogReconciler:
        self._validate_mutation_request(request, "postgres_reconciler")
        self._refresh_project_tenant_authorization()
        return self._require_execution().reconciler

    def postgres_snapshot_guard(
        self,
        request: DevRolloutRequest,
    ) -> Callable[[], None]:
        """Return the owner/identity proof run inside the PG source snapshot."""

        self._validate_mutation_request(request, "postgres_snapshot_guard")
        execution = self._require_execution()

        def guard() -> None:
            frozen = self._require_project_tenant_authorization()
            current = _authorize_project_tenant_bindings(
                request=self.bound_request,
                config=self.config,
                observation=self.provenance.observation,
                bindings=_postgres_project_tenant_bindings_in_current_snapshot(
                    self.config.project_ids,
                    self.provenance.observation.postgres,
                ),
                authorized_at=self.now(),
            )
            if (
                current.authorization_contract_sha256
                != frozen.authorization_contract_sha256
            ):
                raise PropertyCatalogDevRuntimeError(
                    "revision snapshot changed its project authorization contract"
                )
            self._validate_execution_authorization(execution, current)

        return guard

    def postgres_request_factory(
        self, request: DevRolloutRequest
    ) -> Callable[[DefinitionSourceAdapter], ReconcileRequest]:
        self._validate_mutation_request(request, "postgres_request_factory")
        self._require_project_tenant_authorization()
        execution = self._require_execution()

        def build(adapter: DefinitionSourceAdapter) -> ReconcileRequest:
            return self._definition_request(execution, adapter.source_adapter)

        return build

    def postgres_adapters(
        self, request: DevRolloutRequest
    ) -> Sequence[DefinitionSourceAdapter] | None:
        self._validate_mutation_request(request, "postgres_adapters")
        self._require_project_tenant_authorization()
        self._require_execution()
        # ``None`` is intentional: postgres_executor owns construction of the
        # five reviewed read-only adapters and their one revision-wide snapshot.
        return None

    def reconcile_non_postgres(
        self,
        request: DevRolloutRequest,
        postgres: PostgresRevisionReconcileResult,
    ) -> Mapping[str, Any]:
        self._validate_mutation_request(request, "reconcile_non_postgres")
        self._refresh_project_tenant_authorization()
        execution = self._require_execution()
        self._record_postgres_result(execution, postgres)
        system = self._run_definition_adapter(execution, SystemManifestAdapter())
        span = self._run_definition_adapter(
            execution,
            SpanAttributeDefinitionSourceAdapter(
                group_page_loader=RevisionPinnedSpanAttributeGroupPageLoader(
                    self.catalog_client,
                    context=execution.context,
                    build_token=execution.lease.build_token,
                    deadline=self.deadline,
                    lineage_anchor_revision=(
                        execution.prepared.lineage_anchor_revision
                    ),
                    prior_active_revision=(
                        execution.prepared.prior_active.catalog_revision
                        if (
                            execution.prepared.mode is LifecycleRunMode.INCREMENTAL
                            and execution.prepared.prior_active is not None
                        )
                        else None
                    ),
                )
            ),
        )
        final_audit = self.span_reader.audit(execution.frozen)
        authoritative = self._require_authoritative(execution)
        if (
            final_audit.state_conflict_count
            or final_audit.count != authoritative.values.source_count
            or final_audit.digest != authoritative.values.source_digest
            or final_audit.count != authoritative.source_audit.source_count
            or final_audit.digest != authoritative.source_audit.source_digest
        ):
            raise PropertyCatalogDevRuntimeError(
                "canonical span source changed between authoritative values, "
                "definition projection, and the final independent audit"
            )
        return {
            "definition_streams": 7,
            "final_span_audit_count": final_audit.count,
            "final_span_audit_digest": final_audit.digest,
            "postgres_terminal_streams": len(postgres.adapter_results),
            "span_definition_rows": span.checkpoint_write.checkpoint.definition_count,
            "system_definition_rows": system.checkpoint_write.checkpoint.definition_count,
        }

    def qualify(self, request: DevRolloutRequest) -> Mapping[str, Any]:
        self._validate_mutation_request(request, "qualify")
        authorization = self._refresh_project_tenant_authorization()
        execution = self._require_execution()
        qualification = self._qualify_and_fence(execution)
        assert qualification.activation_sha256 is not None
        assert execution.fence is not None
        return {
            "activation_sha256": qualification.activation_sha256,
            "authorized_build_binding_sha256": (
                self._authorized_build_binding_sha256 or ""
            ),
            "build_lease_sha256": execution.lease.build_lease_sha256,
            "manifest_sha256": execution.manifest.sha256
            if execution.manifest is not None
            else "",
            "qualified": qualification.qualified,
            "project_tenant_contract_sha256": (
                authorization.authorization_contract_sha256
            ),
            "revision_fence_sha256": execution.fence.fence_sha256,
            "stream_count": len(execution.checkpoints),
        }

    def activate(self, request: DevRolloutRequest) -> Mapping[str, Any]:
        self._validate_mutation_request(request, "activate")
        authorization = self._refresh_project_tenant_authorization()
        execution = self._require_execution()
        if execution.activation is None:
            if execution.manifest is None or execution.fence is None:
                raise PropertyCatalogDevRuntimeError(
                    "activation requires a qualified and physically fenced revision"
                )
            inventory = self._activation_inventory(execution)
            execution.activation = PropertyCatalogActivator(self.state_store).activate(
                manifest=execution.manifest,
                fence=execution.fence,
                inventory=inventory,
                now=self.now(),
            )
        result = execution.activation
        active = self._load_latest_active_retirement(execution.prepared.scope)
        if (
            active.catalog_revision != result.record.catalog_revision
            or active.build_token != result.record.build_token
            or active.projection_version != result.record.projection_version
            or active.lifecycle_mode is not result.record.lifecycle_mode
            or active.activation_sequence != result.record.activation_sequence
            or active.activation_sha256 != result.record.activation_sha256
            or active.source_manifest_sha256 != result.record.source_manifest_sha256
            or active.build_plan.sha256 != execution.lease.build_lease_sha256
        ):
            raise PropertyCatalogDevRuntimeError(
                "durably reread active evidence differs from activation result"
            )
        self._publish_producer_retirement(active, execution.prepared.scope)
        return {
            "activated": True,
            "activation_sequence": result.record.activation_sequence,
            "authorized_build_binding_sha256": (
                self._authorized_build_binding_sha256 or ""
            ),
            "catalog_revision": result.record.catalog_revision,
            "idempotent": result.idempotent,
            "live_definition_rows": result.record.live_definition_rows,
            "project_tenant_contract_sha256": (
                authorization.authorization_contract_sha256
            ),
            "tombstone_rows": result.record.tombstone_rows,
            "value_rows": result.record.value_rows,
        }

    def _prepare_revision(self, mode: LifecycleRunMode) -> _RevisionExecution:
        if not self.bound_request.execute or self.bound_request.status:
            raise PropertyCatalogDevRuntimeError(
                "revision preparation requires an execute-mode request"
            )
        authorization = self._refresh_project_tenant_authorization()
        object.__setattr__(
            self,
            "_revision_project_tenant_authorization",
            authorization,
        )
        if self._execution is not None:
            if (
                mode is not LifecycleRunMode.AUTO
                and self._execution.prepared.mode is not mode
            ):
                raise PropertyCatalogDevRuntimeError(
                    "runtime cannot change reconcile mode inside one revision"
                )
            self._validate_execution_authorization(
                self._execution,
                authorization,
            )
            return self._execution
        scope = WorkspaceCatalogScope(
            organization_id=self.bound_request.organization_id,
            workspace_id=self.bound_request.workspace_id,
            catalog_epoch=self.config.catalog_epoch,
            projection_version=self.config.projection_version,
            project_ids=self.config.project_ids,
        )
        prepared = self.lifecycle.prepare(
            scope=scope,
            mode=mode,
            configured_bounds=ConfiguredSourceBounds(
                origin=self.config.span_since,
                initial_until=self.config.span_until,
            ),
            allow_expired_repair=self.bound_request.repair_expired_incomplete,
        )
        self._validate_prepared_authorization(prepared, authorization)
        build_binding_sha256 = _authorized_build_binding_sha256(
            authorization=authorization,
            build_lease_sha256=prepared.lease.build_lease_sha256,
        )
        if self._authorized_build_binding_sha256 is None:
            object.__setattr__(
                self,
                "_authorized_build_binding_sha256",
                build_binding_sha256,
            )
        elif self._authorized_build_binding_sha256 != build_binding_sha256:
            raise PropertyCatalogDevRuntimeError(
                "revision changed its authorized build binding"
            )
        lease = prepared.lease
        emitted_at = lease.issued_at
        frozen = FrozenSpanSource(
            project_ids=prepared.scope.project_ids,
            since=prepared.cutoffs.span_window.since,
            until=prepared.cutoffs.span_window.until,
            audit_generation=prepared.cutoffs.span_audit_generation,
        )
        expected_scope = BuildPlanSourceScope(
            project_ids=frozen.project_ids,
            span_since_us=_unix_microseconds(frozen.since),
            span_until_us=_unix_microseconds(frozen.until),
        )
        if lease.build_plan.source_scope != expected_scope:
            raise PropertyCatalogDevRuntimeError(
                "reserved build source scope differs from the frozen DEV scan"
            )
        planned_by_role = {
            (stream.source_adapter, stream.role): stream
            for stream in lease.build_plan.streams
        }
        context = PostgresSnapshotContext(
            organization_id=self.bound_request.organization_id,
            workspace_id=self.bound_request.workspace_id,
            project_ids=self.config.project_ids,
            catalog_epoch=self.config.catalog_epoch,
            catalog_revision=lease.catalog_revision,
            projection_version=self.config.projection_version,
            snapshot_cutoff=prepared.cutoffs.snapshot_upper,
        )
        revision_proof = _make_authorized_revision_proof(
            authorization=authorization,
            lease=lease,
            frozen=frozen,
            context=context,
            mode=prepared.reconcile_mode,
            emitted_at=emitted_at,
            planned_by_role=planned_by_role,
        )
        if self._authorized_revision_proof is None:
            object.__setattr__(
                self,
                "_authorized_revision_proof",
                revision_proof,
            )
        elif self._authorized_revision_proof != revision_proof:
            raise PropertyCatalogDevRuntimeError(
                "revision changed its immutable authorization proof"
            )
        if prepared.prior_active is not None:
            # The prior active high-water must be durable before Python exposes
            # a newer mutable fence to Go. This also repairs a crash after the
            # activation INSERT but before its retirement-file rename.
            self._publish_producer_retirement(
                prepared.prior_active,
                prepared.scope,
            )
        publishers_by_role: dict[
            tuple[SourceAdapter, ManifestStreamRole], ClickHouseEnvelopePublisher
        ] = {}
        if prepared.reservation_status is not ReservationStatus.FENCED:
            for stream in lease.build_plan.streams:
                write_lease = (
                    self.coordinator.open_stream(
                        lease=lease,
                        source_adapter=stream.source_adapter,
                        producer_stream_id=stream.producer_stream_id,
                    )
                    if prepared.reservation_status is ReservationStatus.OPEN
                    else CatalogWriteLease(
                        organization_id=lease.organization_id,
                        workspace_id=lease.workspace_id,
                        catalog_epoch=lease.catalog_epoch,
                        catalog_revision=lease.catalog_revision,
                        build_token=lease.build_token,
                        projection_version=lease.projection_version,
                        source_adapter=stream.source_adapter,
                        producer_stream_id=stream.producer_stream_id,
                        build_plan_json=lease.build_plan_json,
                        build_lease_sha256=lease.build_lease_sha256,
                        expires_at=lease.expires_at,
                    )
                )
                if stream.role is ManifestStreamRole.HOT_VALUES:
                    continue
                publishers_by_role[(stream.source_adapter, stream.role)] = (
                    ClickHouseEnvelopePublisher(
                        self.catalog_client,
                        database=self.config.catalog.database,
                        lease=write_lease,
                        deadline=self.deadline,
                        now=self.now,
                    )
                )
        if prepared.reservation_status is ReservationStatus.OPEN:
            self.coordinator.publish_building_assignment(lease=lease)
        definitions = (
            {}
            if prepared.reservation_status is ReservationStatus.FENCED
            else {
                adapter: publishers_by_role[(adapter, ManifestStreamRole.DEFINITIONS)]
                for adapter in SourceAdapter
            }
        )
        current = ClickHouseCurrentBindingReader(
            self.catalog_client,
            database=self.config.catalog.database,
            deadline=self.deadline,
        )
        reconciler = PropertyCatalogReconciler(
            publisher=_DefinitionPublisherRouter(definitions),
            checkpoint_writer=self.state_store,
            current_bindings=current,
        )
        object.__setattr__(
            self,
            "_execution",
            _RevisionExecution(
                prepared=prepared,
                mode=prepared.reconcile_mode,
                frozen=frozen,
                lease=lease,
                context=context,
                planned_by_role=planned_by_role,
                publishers_by_role=publishers_by_role,
                reconciler=reconciler,
                emitted_at=emitted_at,
            ),
        )
        self._validate_execution_authorization(self._execution, authorization)
        if prepared.reservation_status is ReservationStatus.FENCED:
            self._restore_fenced_execution(self._execution)
        return self._execution

    def _load_latest_active_retirement(
        self,
        scope: WorkspaceCatalogScope,
    ) -> PriorActiveEvidence:
        if self.lifecycle_state is None:
            raise PropertyCatalogDevRuntimeError(
                "runtime has no conflict-visible active-state reader"
            )
        active = self.lifecycle_state.load_latest_active(scope)
        if active is None:
            raise PropertyCatalogDevRuntimeError(
                "activated revision was not durably reread"
            )
        return active

    def _publish_producer_retirement(
        self,
        active: PriorActiveEvidence,
        scope: WorkspaceCatalogScope,
    ) -> None:
        if self.producer_retirement_sink is None:
            raise PropertyCatalogDevRuntimeError(
                "runtime has no producer retirement proof sink"
            )
        self.producer_retirement_sink.publish(
            ProducerStateRetirement.from_active(
                active,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                catalog_epoch=scope.catalog_epoch,
                emitted_at=self.now(),
            )
        )

    def _restore_fenced_execution(self, execution: _RevisionExecution) -> None:
        """Reconstruct activation evidence after a post-fence process crash."""

        if execution.prepared.reservation_status is not ReservationStatus.FENCED:
            raise PropertyCatalogDevRuntimeError(
                "fenced recovery requires a persisted fenced reservation"
            )
        for start in execution.prepared.streams:
            if start.resume is None:
                raise PropertyCatalogDevRuntimeError(
                    "fenced recovery is missing a persisted checkpoint"
                )
            self._remember_checkpoint(execution, start.resume.checkpoint)
        manifest = _activation_manifest(execution)
        loaded = tuple(self.state_store.load_checkpoints(manifest.revision_requirement))
        loaded_manifest = _activation_manifest(execution, checkpoints=loaded)
        if loaded_manifest.sha256 != manifest.sha256:
            raise PropertyCatalogDevRuntimeError(
                "fenced recovery checkpoint manifest changed"
            )
        qualification = qualify_revision(
            loaded_manifest.revision_requirement,
            loaded,
        )
        if not qualification.qualified or qualification.activation_sha256 is None:
            raise PropertyCatalogDevRuntimeError(
                "fenced recovery no longer qualifies: " + ",".join(qualification.issues)
            )
        fence = make_revision_fence(
            manifest=loaded_manifest,
            build_plan=execution.lease.build_plan,
            checkpoints=loaded,
            drain_deadline=execution.lease.expires_at,
            fenced_at=execution.lease.expires_at,
        )
        self.state_store.audit_build_plan(
            build_plan=execution.lease.build_plan,
            manifest=loaded_manifest,
        )
        execution.checkpoints = {checkpoint.key: checkpoint for checkpoint in loaded}
        execution.manifest = loaded_manifest
        execution.qualification = qualification
        execution.fence = fence

    def _run_authoritative_span(
        self, execution: _RevisionExecution
    ) -> AuthoritativeSpanResult:
        if execution.authoritative is not None:
            return execution.authoritative
        values = execution.stream(
            SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.VALUES
        )
        audit = execution.stream(
            SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.SOURCE_AUDIT
        )
        reconciler = AuthoritativeSpanReconciler(
            reader=self.span_reader,
            publishers={
                AuthoritativeSpanRole.VALUES: execution.publisher(
                    SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.VALUES
                ),
                AuthoritativeSpanRole.SOURCE_AUDIT: execution.publisher(
                    SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.SOURCE_AUDIT
                ),
            },
            checkpoint_store=self.state_store,
        )
        result = reconciler.run(
            frozen=execution.frozen,
            build=AuthoritativeSpanBuild(
                organization_id=execution.context.organization_id,
                workspace_id=execution.context.workspace_id,
                catalog_epoch=execution.context.catalog_epoch,
                catalog_revision=execution.context.catalog_revision,
                build_token=execution.lease.build_token,
                projection_version=execution.context.projection_version,
                emitted_at=execution.emitted_at,
                values_producer_stream_id=values.producer_stream_id,
                audit_producer_stream_id=audit.producer_stream_id,
            ),
        )
        execution.authoritative = result
        self._remember_checkpoint(execution, result.values)
        self._remember_checkpoint(execution, result.source_audit)
        return result

    def _definition_request(
        self,
        execution: _RevisionExecution,
        source_adapter: SourceAdapter,
    ) -> ReconcileRequest:
        stream = execution.stream(source_adapter, ManifestStreamRole.DEFINITIONS)
        start = execution.prepared.stream(
            source_adapter, ManifestStreamRole.DEFINITIONS
        )
        if execution.source_budget is None:
            execution.source_budget = self._source_budget()
        return ReconcileRequest(
            context=execution.context,
            build_token=execution.lease.build_token,
            producer_stream_id=stream.producer_stream_id,
            emitted_at=execution.emitted_at,
            mode=execution.mode,
            source_version=stream.source_version_fence,
            # Span definitions are rebuilt from the complete revision-pinned
            # workspace key/type union.  A prior revision's timestamp cursor
            # is not meaningful in that snapshot and conflicts with its exact
            # revision fence.  Same-revision crash continuation still uses
            # ``resume.source_cursor`` below.
            lower_watermark=(
                ""
                if source_adapter is SourceAdapter.SPAN_ATTRIBUTE
                else start.lower_watermark
            ),
            resume=start.resume,
            source_budget=execution.source_budget,
        )

    def _source_budget(self) -> SourceReadBudget:
        remaining = self.deadline.remaining_ms(cap_ms=self.config.rollout_wall_ms)
        if remaining < 100:
            raise PropertyCatalogDevRuntimeError(
                "insufficient shared wall for a source snapshot"
            )
        if self.config.explicit_initial_backfill_wall:
            source_wall_seconds = (
                RUNTIME_LIMITS.initial_backfill_source_adapter_wall_seconds
            )
        elif self.config.explicit_scheduled_reconcile_wall:
            source_wall_seconds = (
                RUNTIME_LIMITS.scheduled_reconcile_source_adapter_wall_seconds
            )
        else:
            source_wall_seconds = RUNTIME_LIMITS.source_adapter_wall_seconds
        postgres_wall_cap_ms = int(source_wall_seconds * 1_000)
        postgres_remaining = min(postgres_wall_cap_ms, remaining)
        return SourceReadBudget(
            postgres=PostgresReadBudget(
                statement_timeout_ms=min(
                    RUNTIME_LIMITS.postgres_statement_timeout_ms,
                    postgres_remaining - 1,
                ),
                wall_timeout_seconds=postgres_remaining / 1_000,
                initial_backfill=self.config.explicit_initial_backfill_wall,
                scheduled_reconcile=self.config.explicit_scheduled_reconcile_wall,
            ),
            adapter_wall_timeout_seconds=min(
                source_wall_seconds,
                remaining / 1_000,
            ),
            shared_deadline=self.deadline,
        )

    def _record_postgres_result(
        self,
        execution: _RevisionExecution,
        postgres: PostgresRevisionReconcileResult,
    ) -> None:
        if not isinstance(postgres, PostgresRevisionReconcileResult):
            raise TypeError("postgres must be PostgresRevisionReconcileResult")
        if (
            postgres.context != execution.context
            or postgres.build_token != execution.lease.build_token
            or len(postgres.adapter_results) != 5
        ):
            raise PropertyCatalogDevRuntimeError(
                "PostgreSQL revision result differs from its exact build"
            )
        for adapter_result in postgres.adapter_results:
            final = adapter_result.final_result
            if not final.complete or final.error is not None:
                raise PropertyCatalogDevRuntimeError(
                    "PostgreSQL definition stream is not terminal and complete"
                )
            self._remember_checkpoint(execution, final.checkpoint_write.checkpoint)
            execution.definition_results[adapter_result.source_adapter] = final
        execution.postgres = postgres

    def _run_definition_adapter(
        self,
        execution: _RevisionExecution,
        adapter: DefinitionSourceAdapter,
    ) -> ReconcileResult:
        existing = execution.definition_results.get(adapter.source_adapter)
        if existing is not None:
            return existing
        request = self._definition_request(execution, adapter.source_adapter)
        rehydrated = _rehydrate_completed_definition_resume(
            adapter=adapter,
            request=request,
        )
        if rehydrated is not None:
            execution.definition_results[adapter.source_adapter] = rehydrated
            self._remember_checkpoint(
                execution,
                rehydrated.checkpoint_write.checkpoint,
            )
            return rehydrated
        seen: set[str] = set()
        while True:
            result = execution.reconciler.reconcile(adapter, request)
            if result.error is not None:
                raise PropertyCatalogDevRuntimeError(
                    f"{adapter.source_adapter} reconciliation failed: {result.error}"
                )
            if result.complete:
                execution.definition_results[adapter.source_adapter] = result
                self._remember_checkpoint(execution, result.checkpoint_write.checkpoint)
                return result
            resume = result.checkpoint_write
            if (
                not resume.source_cursor
                or resume.source_cursor in seen
                or (
                    request.resume is not None
                    and resume.processed_rows <= request.resume.processed_rows
                )
            ):
                raise PropertyCatalogDevRuntimeError(
                    f"{adapter.source_adapter} continuation made no progress"
                )
            seen.add(resume.source_cursor)
            request = replace(request, lower_watermark="", resume=resume)

    def _qualify_and_fence(
        self, execution: _RevisionExecution
    ) -> RevisionQualification:
        if execution.qualification is not None:
            return execution.qualification
        expected_scope = BuildPlanSourceScope(
            project_ids=execution.frozen.project_ids,
            span_since_us=_unix_microseconds(execution.frozen.since),
            span_until_us=_unix_microseconds(execution.frozen.until),
        )
        if execution.lease.build_plan.source_scope != expected_scope:
            raise PropertyCatalogDevRuntimeError(
                "build lease no longer matches the authoritative source scope"
            )
        if execution.postgres is None or len(execution.definition_results) != 7:
            raise PropertyCatalogDevRuntimeError(
                "qualification requires all seven terminal definition streams"
            )
        if len(execution.checkpoints) != 9:
            raise PropertyCatalogDevRuntimeError(
                "qualification requires nine complete non-hot checkpoints"
            )
        non_hot = tuple(
            _checkpoint_stream_proof(checkpoint)
            for checkpoint in sorted(
                execution.checkpoints.values(), key=lambda item: item.key
            )
        )
        intent = self.coordinator.begin_drain_intent(
            lease=execution.lease,
            completed_stream_proofs=non_hot,
            drain_deadline=execution.lease.expires_at,
            now=self.now(),
        )
        hot = execution.stream(
            SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.HOT_VALUES
        )
        if intent.fenced_sequence == 0:
            prepared = self.hot_proof_source.wait_for(
                assignment=intent,
                producer_stream_id=hot.producer_stream_id,
                phase="prepared",
            )
            bound = self._retry_physical_visibility(
                lambda: self.coordinator.bind_hot_drain_boundary(
                    lease=execution.lease,
                    prepared_proof=prepared,
                    drain_deadline=execution.lease.expires_at,
                    now=self.now(),
                ),
                stage="prepared hot boundary",
            )
        else:
            bound = intent
        ready = self.hot_proof_source.wait_for(
            assignment=bound,
            producer_stream_id=hot.producer_stream_id,
            phase="ready",
        )
        hot_checkpoint = self._retry_physical_visibility(
            lambda: self.state_store.append_hot_checkpoint_from_proof(
                lease=execution.lease,
                assignment=bound,
                proof=ready,
            ),
            stage="ready hot checkpoint",
        )
        self._remember_checkpoint(execution, hot_checkpoint)
        hot_proof = ready.to_stream_proof(
            assignment=bound,
            checkpoint=hot_checkpoint,
        )
        manifest = _activation_manifest(execution)
        loaded = tuple(self.state_store.load_checkpoints(manifest.revision_requirement))
        loaded_manifest = _activation_manifest(execution, checkpoints=loaded)
        if loaded_manifest.sha256 != manifest.sha256:
            raise PropertyCatalogDevRuntimeError(
                "physical checkpoints changed the final activation manifest"
            )
        qualification = qualify_revision(
            loaded_manifest.revision_requirement,
            loaded,
        )
        if not qualification.qualified or qualification.activation_sha256 is None:
            raise PropertyCatalogDevRuntimeError(
                "revision qualification failed: " + ",".join(qualification.issues)
            )
        proofs_by_key = {proof.key: proof for proof in non_hot}
        proofs_by_key[hot_proof.key] = hot_proof
        fence = self.coordinator.fence(
            lease=execution.lease,
            stream_proofs=tuple(proofs_by_key[key] for key in sorted(proofs_by_key)),
            checkpoint_state_sha256s=tuple(
                sorted(checkpoint.state_sha256 for checkpoint in loaded)
            ),
            final_manifest_sha256=loaded_manifest.sha256,
            drain_deadline=execution.lease.expires_at,
            now=self.now(),
        )
        self.state_store.audit_build_plan(
            build_plan=execution.lease.build_plan,
            manifest=loaded_manifest,
        )
        execution.checkpoints = {checkpoint.key: checkpoint for checkpoint in loaded}
        execution.manifest = loaded_manifest
        execution.qualification = qualification
        execution.fence = fence
        return qualification

    def _retry_physical_visibility(
        self,
        operation: Callable[[], Any],
        *,
        stage: str,
    ) -> Any:
        """Retry Kafka-ACK-to-ClickHouse visibility lag under the shared wall."""

        last: Exception | None = None
        while True:
            try:
                return operation()
            except (
                ProducerDrainProofError,
                PropertyCatalogStateConflict,
                PropertyCatalogCoordinatorError,
            ) as exc:
                last = exc
                try:
                    remaining = self.deadline.remaining_ms(
                        cap_ms=_VISIBILITY_RETRY_CAP_MS
                    )
                except Exception as deadline_exc:
                    raise PropertyCatalogHotDrainHandshakeUnavailable(
                        f"{stage} was not physically visible before the shared deadline"
                    ) from (last or deadline_exc)
                time.sleep(min(_DRAIN_POLL_INTERVAL_SECONDS, remaining / 1_000))

    def _activation_inventory(
        self, execution: _RevisionExecution
    ) -> ActivationInventory:
        manifest = execution.manifest
        if manifest is None or (
            manifest.catalog_revision != execution.context.catalog_revision
            or manifest.build_token != execution.lease.build_token
            or manifest.projection_version != execution.context.projection_version
            or manifest.lifecycle_mode != execution.prepared.lifecycle_mode
            or manifest.lineage_anchor_revision
            != execution.prepared.lineage_anchor_revision
        ):
            raise PropertyCatalogDevRuntimeError(
                "activation inventory requires the exact prepared manifest lineage"
            )
        current = ClickHouseCurrentBindingReader(
            self.catalog_client,
            database=self.config.catalog.database,
            deadline=self.deadline,
        )
        rows = tuple(
            row
            for adapter in SourceAdapter
            for row in current.read_current(
                context=execution.context,
                source_adapter=adapter,
                at_revision=execution.context.catalog_revision,
                build_token=execution.lease.build_token,
            )
        )
        if len({row.binding_id for row in rows}) != len(rows):
            raise PropertyCatalogDevRuntimeError(
                "activation inventory contains duplicate definition bindings"
            )
        prior = execution.prepared.prior_active
        if execution.prepared.mode is LifecycleRunMode.INCREMENTAL:
            if prior is None:
                raise PropertyCatalogDevRuntimeError(
                    "incremental activation inventory has no prior active lineage"
                )
            expected_lineage_rows = prior.lineage_anchor.active_revisions_since + 1
            expected_prior_matches = 1
            prior_revision = prior.catalog_revision
            prior_build_token = prior.build_token
            prior_activation_sequence = prior.activation_sequence
        else:
            # Snapshot modes self-anchor.  All older active rows are outside
            # the selected lineage and the current building token is joined
            # explicitly below.
            expected_lineage_rows = 0
            expected_prior_matches = 0
            prior_revision = 0
            prior_build_token = ""
            prior_activation_sequence = 0
        values = self.catalog_client.query(
            _active_value_inventory_sql(self.config.catalog.database),
            {
                "organization_id": execution.context.organization_id,
                "workspace_id": execution.context.workspace_id,
                "catalog_epoch": execution.context.catalog_epoch,
                "catalog_revision": execution.context.catalog_revision,
                "build_token": execution.lease.build_token,
                "projection_version": execution.context.projection_version,
                "lineage_anchor_revision": manifest.lineage_anchor_revision,
                "prior_revision": prior_revision,
                "prior_build_token": prior_build_token,
            },
            timeout_ms=self.deadline.remaining_ms(
                cap_ms=RUNTIME_LIMITS.state_store_timeout_ms
            ),
        )
        if len(values) != 1:
            raise PropertyCatalogDevRuntimeError(
                "activation value inventory did not return one proof row"
            )
        proof = values[0]
        conflict_fields = (
            "activation_state_conflicts",
            "activation_lineage_conflicts",
            "activation_sequence_conflicts",
            "activation_anchor_conflicts",
            "value_state_conflicts",
        )
        if any(
            _nonnegative_int(proof.get(field), field) != 0 for field in conflict_fields
        ):
            raise PropertyCatalogDevRuntimeError(
                "activation value inventory contains conflicting lineage or value states"
            )
        observed_anchor = _nonnegative_int(
            proof.get("observed_lineage_anchor_revision"),
            "observed_lineage_anchor_revision",
        )
        active_lineage_rows = _nonnegative_int(
            proof.get("active_lineage_rows"), "active_lineage_rows"
        )
        latest_active_revision = _nonnegative_int(
            proof.get("latest_active_revision"), "latest_active_revision"
        )
        latest_active_sequence = _nonnegative_int(
            proof.get("latest_active_sequence"), "latest_active_sequence"
        )
        latest_active_build_token = str(proof.get("latest_active_build_token") or "")
        prior_active_matches = _nonnegative_int(
            proof.get("prior_active_matches"), "prior_active_matches"
        )
        if (
            observed_anchor != manifest.lineage_anchor_revision
            or active_lineage_rows != expected_lineage_rows
            or latest_active_revision != prior_revision
            or latest_active_sequence != prior_activation_sequence
            or latest_active_build_token != prior_build_token
            or prior_active_matches != expected_prior_matches
        ):
            raise PropertyCatalogDevRuntimeError(
                "activation value inventory does not match the prepared active lineage"
            )
        return ActivationInventory(
            live_definition_rows=sum(not row.is_deleted for row in rows),
            tombstone_rows=sum(row.is_deleted for row in rows),
            value_rows=_nonnegative_int(proof.get("value_rows"), "value_rows"),
        )

    @staticmethod
    def _remember_checkpoint(
        execution: _RevisionExecution, checkpoint: CatalogCheckpoint
    ) -> None:
        existing = execution.checkpoints.get(checkpoint.key)
        if existing is not None and existing != checkpoint:
            raise PropertyCatalogDevRuntimeError(
                "terminal checkpoint changed inside one runtime revision"
            )
        execution.checkpoints[checkpoint.key] = checkpoint

    @staticmethod
    def _require_authoritative(
        execution: _RevisionExecution,
    ) -> AuthoritativeSpanResult:
        if execution.authoritative is None:
            raise PropertyCatalogDevRuntimeError(
                "authoritative span reconciliation has not completed"
            )
        return execution.authoritative

    def _validate_prepared_authorization(
        self,
        prepared: PreparedLifecycleRevision,
        authorization: ProjectTenantAuthorization,
    ) -> None:
        lease = prepared.lease
        plan = lease.build_plan
        if (
            prepared.scope.organization_id != authorization.organization_id
            or prepared.scope.workspace_id != authorization.workspace_id
            or prepared.scope.project_ids != authorization.project_ids
            or prepared.scope.catalog_epoch != self.config.catalog_epoch
            or prepared.scope.projection_version != self.config.projection_version
            or lease.organization_id != authorization.organization_id
            or lease.workspace_id != authorization.workspace_id
            or lease.catalog_epoch != self.config.catalog_epoch
            or lease.projection_version != self.config.projection_version
            or plan.organization_id != authorization.organization_id
            or plan.workspace_id != authorization.workspace_id
            or plan.catalog_epoch != self.config.catalog_epoch
            or plan.projection_version != self.config.projection_version
            or plan.catalog_revision != lease.catalog_revision
            or plan.build_token != lease.build_token
            or plan.source_scope.project_ids != authorization.project_ids
            or lease.build_plan_json != plan.canonical_json
            or lease.build_lease_sha256 != plan.sha256
        ):
            raise PropertyCatalogDevRuntimeError(
                "revision build plan differs from its PostgreSQL project tenant authorization"
            )

    def _validate_execution_authorization(
        self,
        execution: _RevisionExecution,
        authorization: ProjectTenantAuthorization,
    ) -> None:
        self._validate_prepared_authorization(execution.prepared, authorization)
        lease = execution.lease
        plan = lease.build_plan
        context = execution.context
        frozen = execution.frozen
        span_since_us = _unix_microseconds(frozen.since)
        span_until_us = _unix_microseconds(frozen.until)
        snapshot_cutoff_us = _unix_microseconds(context.snapshot_cutoff)
        expected_build_binding = _authorized_build_binding_sha256(
            authorization=authorization,
            build_lease_sha256=lease.build_lease_sha256,
        )
        expected_source_scope = BuildPlanSourceScope(
            project_ids=frozen.project_ids,
            span_since_us=span_since_us,
            span_until_us=span_until_us,
        )
        expected_planned_by_role = {
            (stream.source_adapter, stream.role): stream for stream in plan.streams
        }
        relational_streams = tuple(
            stream
            for stream in plan.streams
            if stream.source_adapter
            not in {SourceAdapter.SYSTEM_MANIFEST, SourceAdapter.SPAN_ATTRIBUTE}
        )
        stored_revision_proof = self._authorized_revision_proof
        current_revision_proof = _make_authorized_revision_proof(
            authorization=authorization,
            lease=lease,
            frozen=frozen,
            context=context,
            mode=execution.mode,
            emitted_at=execution.emitted_at,
            planned_by_role=execution.planned_by_role,
        )
        if (
            self._authorized_build_binding_sha256 != expected_build_binding
            or stored_revision_proof is None
            or stored_revision_proof != current_revision_proof
            or stored_revision_proof.authorized_build_binding_sha256
            != expected_build_binding
            or stored_revision_proof.build_lease_sha256 != lease.build_lease_sha256
            or stored_revision_proof.build_plan_sha256 != plan.sha256
            or execution.prepared.lease != lease
            or context.organization_id != authorization.organization_id
            or context.workspace_id != authorization.workspace_id
            or context.project_ids != authorization.project_ids
            or context.catalog_epoch != self.config.catalog_epoch
            or context.catalog_revision != lease.catalog_revision
            or context.projection_version != self.config.projection_version
            or frozen.project_ids != authorization.project_ids
            or plan.source_scope != expected_source_scope
            or execution.prepared.cutoffs.span_window.since != frozen.since
            or execution.prepared.cutoffs.span_window.until != frozen.until
            or execution.prepared.cutoffs.span_audit_generation
            != frozen.audit_generation
            or context.snapshot_cutoff != execution.prepared.cutoffs.snapshot_upper
            or context.snapshot_cutoff != frozen.until
            or snapshot_cutoff_us != span_until_us
            or execution.mode is not execution.prepared.reconcile_mode
            or execution.emitted_at != lease.issued_at
            or dict(execution.planned_by_role) != expected_planned_by_role
            or len(execution.planned_by_role) != len(plan.streams)
            or not relational_streams
            or any(
                stream.source_version_fence != snapshot_cutoff_us
                for stream in relational_streams
            )
        ):
            raise PropertyCatalogDevRuntimeError(
                "live revision execution differs from its authorized build plan"
            )

    def _require_execution(self) -> _RevisionExecution:
        if self._execution is None:
            raise PropertyCatalogDevRuntimeError(
                "revision backfill must prepare the exact build before reconciliation"
            )
        self._validate_execution_authorization(
            self._execution,
            self._require_project_tenant_authorization(),
        )
        return self._execution

    def _validate_request(self, request: DevRolloutRequest) -> None:
        if not isinstance(request, DevRolloutRequest):
            raise TypeError("request must be a DevRolloutRequest")
        if request != self.bound_request:
            raise PropertyCatalogDevRuntimeError(
                "runtime request changed after factory validation"
            )
        if not isinstance(self.provenance, DevProvenanceEvidence):
            raise PropertyCatalogDevRuntimeError(
                "runtime has no frozen remote DEV provenance evidence"
            )

    def _validate_mutation_request(
        self,
        request: DevRolloutRequest,
        stage: str,
    ) -> None:
        self._validate_request(request)
        if not request.execute or request.status:
            raise PropertyCatalogDevRuntimeError(
                f"{stage} requires the bound execute-mode request"
            )

    def _require_project_tenant_authorization(self) -> ProjectTenantAuthorization:
        if self._factory_authority is not _RUNTIME_FACTORY_AUTHORITY:
            raise PropertyCatalogDevRuntimeError(
                "runtime project authorization lacks factory authority"
            )
        authorization = self.provenance.project_tenant_authorization
        if authorization is None:
            raise PropertyCatalogDevRuntimeError(
                "mutation runtime has no PostgreSQL project tenant authorization"
            )
        configured_projects = getattr(self.config, "project_ids", ())
        expected_contract = _project_tenant_authorization_contract_sha256(
            request=self.bound_request,
            config=self.config,
            observation=self.provenance.observation,
        )
        if (
            authorization.organization_id != self.bound_request.organization_id
            or authorization.workspace_id != self.bound_request.workspace_id
            or authorization.project_ids != configured_projects
            or authorization.authorization_contract_sha256 != expected_contract
        ):
            raise PropertyCatalogDevRuntimeError(
                "PostgreSQL project tenant authorization differs from runtime scope"
            )
        return authorization

    def _refresh_project_tenant_authorization(self) -> ProjectTenantAuthorization:
        frozen = self._require_project_tenant_authorization()
        probe = self.project_tenant_binding_probe
        if not callable(probe):
            raise PropertyCatalogDevRuntimeError(
                "mutation runtime has no factory-owned PostgreSQL project probe"
            )
        current = _authorize_project_tenant_bindings(
            request=self.bound_request,
            config=self.config,
            observation=self.provenance.observation,
            bindings=tuple(
                probe(
                    self.config.project_ids,
                    self.provenance.observation.postgres,
                )
            ),
            authorized_at=self.now(),
        )
        if (
            current.authorization_contract_sha256
            != frozen.authorization_contract_sha256
        ):
            raise PropertyCatalogDevRuntimeError(
                "live PostgreSQL project authorization changed its frozen contract"
            )
        execution = getattr(self, "_execution", None)
        if execution is not None:
            self._validate_execution_authorization(execution, current)
        return current


NativeClientFactory = Callable[[NativeConnectionConfig], NativeClickHouseDriver]
SerializerFactory = Callable[[str], CatalogMutationSerializer]
HotProofSourceFactory = Callable[[str, SharedCatalogDeadline], HotDrainProofSource]
ProducerRetirementSinkFactory = Callable[[str], AtomicProducerStateRetirementFile]
ProjectTenantBindingProbe = Callable[
    [tuple[str, ...], PostgresDevIdentity],
    Sequence[PostgresProjectTenantBinding],
]
CancellationProbe = Callable[[], bool]


class PropertyCatalogDevRuntimeFactory:
    """Injectable checked-in factory used by the command and Temporal activity."""

    def __init__(
        self,
        *,
        settings_object: Any | None = None,
        native_client_factory: NativeClientFactory | None = None,
        serializer_factory: SerializerFactory = FileCatalogMutationSerializer,
        fence_sink_factory: Callable[[str], Any] = AtomicSingleTenantFenceFile,
        hot_proof_source_factory: HotProofSourceFactory | None = None,
        producer_retirement_sink_factory: ProducerRetirementSinkFactory = (
            AtomicProducerStateRetirementFile
        ),
        provenance_probe: DevProvenanceProbe | None = None,
        project_tenant_binding_probe: ProjectTenantBindingProbe | None = None,
        cancellation_probe: CancellationProbe = lambda: False,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_build_token: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        if settings_object is None:
            from django.conf import settings

            settings_object = settings
        self._settings = settings_object
        self._native_client_factory = native_client_factory or _default_native_client
        self._serializer_factory = serializer_factory
        self._fence_sink_factory = fence_sink_factory
        self._hot_proof_source_factory = hot_proof_source_factory or (
            lambda path, deadline: SharedVolumeHotDrainProofSource(path, deadline)
        )
        self._producer_retirement_sink_factory = producer_retirement_sink_factory
        self._provenance_probe = provenance_probe or _default_dev_provenance_probe
        self._project_tenant_binding_probe = (
            project_tenant_binding_probe or _postgres_project_tenant_bindings
        )
        if not callable(cancellation_probe):
            raise TypeError("cancellation_probe must be callable")
        self._cancellation_probe = cancellation_probe
        self._now = now
        self._new_build_token = new_build_token

    def __call__(
        self,
        request: DevRolloutRequest,
    ) -> CheckedInPropertyCatalogDevRuntime:
        if isinstance(request, ProductionRolloutRequest):
            raise TypeError(
                "DEV runtime factory does not accept ProductionRolloutRequest"
            )
        if not isinstance(request, DevRolloutRequest):
            raise TypeError("request must be a DevRolloutRequest")
        if not request.execute and not request.status:
            raise PropertyCatalogDevRuntimeError(
                "dry-run must not construct a checked-in runtime"
            )
        config = DevRuntimeConfig.from_settings(
            request,
            self._settings,
            now=self._now(),
        )
        return self._build_runtime(request=request, config=config)

    def _build_runtime(
        self,
        *,
        request: DevRolloutRequest,
        config: DevRuntimeConfig,
    ) -> CheckedInPropertyCatalogDevRuntime:
        """Construct the one reviewed runtime after deployment-specific admission."""

        drivers: dict[str, NativeClickHouseDriver] = {}
        owned_drivers: list[NativeClickHouseDriver] = []

        def write_driver(database: str) -> NativeClickHouseDriver:
            driver = drivers.get(database)
            if driver is None:
                driver = self._native_client_factory(
                    replace(config.catalog, database=database)
                )
                drivers[database] = driver
                owned_drivers.append(driver)
            return driver

        try:
            writer_control_driver = write_driver(config.catalog_control_database)
            source_driver = self._native_client_factory(config.source)
            owned_drivers.append(source_driver)
            provenance = _validate_dev_provenance(
                config=config,
                observation=self._provenance_probe(
                    config,
                    writer_control_driver,
                    source_driver,
                ),
                attested_at=self._now(),
            )
            bindings = tuple(
                self._project_tenant_binding_probe(
                    config.project_ids,
                    provenance.observation.postgres,
                )
            )
            authorization = _authorize_project_tenant_bindings(
                request=request,
                config=config,
                observation=provenance.observation,
                bindings=bindings,
                authorized_at=self._now(),
            )
            provenance = replace(
                provenance,
                project_tenant_authorization=authorization,
            )
            # No target client, schema inspection, source snapshot, lease, hot
            # assignment, or write is constructed until remote identity and every
            # allowlisted project's canonical tenant pass attestation.
            target_driver = write_driver(config.catalog.database)
            schema_client = NativeSchemaClient(
                target_database=config.catalog.database,
                control_database=config.catalog_control_database,
                client_for_database=write_driver,
                deployment=config.deployment,
            )
            catalog_client = NativeCatalogClient(
                target_driver,
                database=config.catalog.database,
            )
            source_client = NativeSourceClient(
                source_driver,
                source_database=config.source.database,
                catalog_database=config.catalog.database,
                explicit_initial_backfill=config.explicit_initial_backfill_wall,
            )
            serializer = self._serializer_factory(config.mutation_lock_directory)
            deadline = SharedCatalogDeadline(
                wall_ms=config.rollout_wall_ms,
                cancelled=self._cancellation_probe,
            )
            state_store = ClickHouseCatalogStateStore(
                catalog_client,
                database=config.catalog.database,
                serializer=serializer,
                deadline=deadline,
            )
            coordinator = ClickHouseRevisionCoordinator(
                catalog_client,
                database=config.catalog.database,
                serializer=serializer,
                producer_fence_sink=self._fence_sink_factory(
                    config.revision_fence_file
                ),
                hot_producer_stream_id=config.hot_producer_stream_id,
                deadline=deadline,
                lease_seconds=(
                    (
                        config.rollout_wall_ms
                        + _MIN_INITIAL_BACKFILL_LEASE_HEADROOM_MS
                        + 999
                    )
                    // 1_000
                    if config.extended_rollout_wall
                    else REVISION_LEASE_SECONDS
                ),
                now=self._now,
            )
            span_reader = CanonicalSpanSourceReader(
                source_client,
                source_database=config.source.database,
                catalog_database=config.catalog.database,
                deadline=deadline,
                timeout_ms=config.span_query_timeout_ms,
                explicit_initial_backfill=config.explicit_initial_backfill_wall,
                page_rows=config.span_page_rows,
            )
            lifecycle_state = ClickHouseLifecycleStateReader(
                catalog_client,
                database=config.catalog.database,
                checkpoint_store=state_store,
                deadline=deadline,
            )
            lifecycle = DurableWorkspaceCatalogLifecycle(
                state_reader=lifecycle_state,
                coordinator=coordinator,
                cutoff_freezer=FreshSpanLifecycleCutoffFreezer(
                    span_reader,
                    now=self._now,
                ),
                hot_producer_stream_id=config.hot_producer_stream_id,
                now=self._now,
                new_build_token=self._new_build_token,
            )
            hot_proof_source = self._hot_proof_source_factory(
                config.drain_proof_file,
                deadline,
            )
            producer_retirement_sink = self._producer_retirement_sink_factory(
                config.producer_retirement_file
            )
            return CheckedInPropertyCatalogDevRuntime(
                config=config,
                bound_request=request,
                provenance=provenance,
                schema_client=schema_client,
                catalog_client=catalog_client,
                source_client=source_client,
                serializer=serializer,
                deadline=deadline,
                state_store=state_store,
                coordinator=coordinator,
                lifecycle=lifecycle,
                span_reader=span_reader,
                hot_proof_source=hot_proof_source,
                now=self._now,
                new_build_token=self._new_build_token,
                project_tenant_binding_probe=self._project_tenant_binding_probe,
                _factory_authority=_RUNTIME_FACTORY_AUTHORITY,
                lifecycle_state=lifecycle_state,
                producer_retirement_sink=producer_retirement_sink,
                _native_drivers=tuple(owned_drivers),
            )
        except BaseException:
            _close_native_drivers(owned_drivers, raise_on_error=False)
            raise


class PropertyCatalogProductionRuntimeFactory(PropertyCatalogDevRuntimeFactory):
    """Production admission over the same reviewed lifecycle implementation."""

    def __init__(
        self,
        *,
        settings_object: Any | None = None,
        native_client_factory: NativeClientFactory | None = None,
        serializer_factory: SerializerFactory = FileCatalogMutationSerializer,
        fence_sink_factory: Callable[[str], Any] | None = None,
        hot_proof_source_factory: HotProofSourceFactory | None = None,
        producer_retirement_sink_factory: ProducerRetirementSinkFactory = (
            AtomicProducerStateRetirementFile
        ),
        provenance_probe: DevProvenanceProbe | None = None,
        project_tenant_binding_probe: ProjectTenantBindingProbe | None = None,
        cancellation_probe: CancellationProbe = lambda: False,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_build_token: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        if fence_sink_factory is None:
            from .revision_fence_registry import AtomicMultiTenantFenceFile

            fence_sink_factory = AtomicMultiTenantFenceFile
        super().__init__(
            settings_object=settings_object,
            native_client_factory=native_client_factory,
            serializer_factory=serializer_factory,
            fence_sink_factory=fence_sink_factory,
            hot_proof_source_factory=hot_proof_source_factory,
            producer_retirement_sink_factory=producer_retirement_sink_factory,
            provenance_probe=provenance_probe,
            project_tenant_binding_probe=project_tenant_binding_probe,
            cancellation_probe=cancellation_probe,
            now=now,
            new_build_token=new_build_token,
        )

    def __call__(
        self,
        request: DevRolloutRequest,
    ) -> CheckedInPropertyCatalogDevRuntime:
        if not isinstance(request, ProductionRolloutRequest):
            raise TypeError("request must be a ProductionRolloutRequest")
        if not request.execute and not request.status:
            raise PropertyCatalogDevRuntimeError(
                "dry-run must not construct a checked-in runtime"
            )
        config = DevRuntimeConfig.from_settings(
            request,
            self._settings,
            now=self._now(),
        )
        if config.deployment != "prod":
            raise PropertyCatalogDevRuntimeError(
                "production factory did not resolve a production runtime"
            )
        return self._build_runtime(request=request, config=config)


def configured_property_catalog_dev_runtime(
    request: DevRolloutRequest,
) -> CheckedInPropertyCatalogDevRuntime:
    """Default dotted-path entrypoint for the reviewed checked-in factory."""

    return PropertyCatalogDevRuntimeFactory()(request)


def configured_property_catalog_production_runtime(
    request: ProductionRolloutRequest,
) -> CheckedInPropertyCatalogDevRuntime:
    """Default dotted-path entrypoint for the reviewed production factory."""

    return PropertyCatalogProductionRuntimeFactory()(request)


def require_checked_in_property_catalog_dev_runtime(
    runtime: Any,
) -> CheckedInPropertyCatalogDevRuntime:
    """Reject configured factories that bypass the reviewed tenant gate."""

    if (
        not isinstance(runtime, CheckedInPropertyCatalogDevRuntime)
        or runtime._factory_authority is not _RUNTIME_FACTORY_AUTHORITY
        or not runtime._scope_locked
        or isinstance(runtime.bound_request, ProductionRolloutRequest)
        or runtime.config.deployment != "dev"
    ):
        raise PropertyCatalogDevRuntimeError(
            "configured runtime did not return the reviewed checked-in DEV runtime"
        )
    try:
        require_dev_catalog_database(runtime.config.catalog.database)
    except PropertyCatalogPublishError as exc:
        raise PropertyCatalogDevRuntimeError(
            "configured runtime did not return the reviewed checked-in DEV runtime"
        ) from exc
    return runtime


def require_checked_in_property_catalog_production_runtime(
    runtime: Any,
) -> CheckedInPropertyCatalogDevRuntime:
    """Reject a production factory result that crossed a DEV/runtime boundary."""

    if (
        not isinstance(runtime, CheckedInPropertyCatalogDevRuntime)
        or runtime._factory_authority is not _RUNTIME_FACTORY_AUTHORITY
        or not runtime._scope_locked
        or not isinstance(runtime.bound_request, ProductionRolloutRequest)
        or runtime.config.deployment != "prod"
    ):
        raise PropertyCatalogDevRuntimeError(
            "configured runtime did not return the reviewed production runtime"
        )
    try:
        require_prod_catalog_database(runtime.config.catalog.database)
    except PropertyCatalogPublishError as exc:
        raise PropertyCatalogDevRuntimeError(
            "configured runtime did not return the reviewed production runtime"
        ) from exc
    return runtime


def _default_native_client(config: NativeConnectionConfig) -> NativeClickHouseDriver:
    from tracer.services.clickhouse.client import ClickHouseClient

    return ClickHouseClient(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        server_enforced_readonly=config.server_enforced_readonly,
        connect_timeout=5,
        send_timeout=30,
        receive_timeout=30,
        pool_size=2,
        read_timeout_ceiling_ms=config.read_timeout_ceiling_ms,
    )


def _schema_evidence(raw: str) -> Mapping[str, Any]:
    try:
        evidence = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PropertyCatalogDevRuntimeError(
            "schema boundary returned invalid evidence"
        ) from exc
    if not isinstance(evidence, dict):
        raise PropertyCatalogDevRuntimeError(
            "schema boundary returned non-object evidence"
        )
    return evidence


def _evidence_with_provenance(
    evidence: Mapping[str, Any],
    provenance: DevProvenanceEvidence,
) -> Mapping[str, Any]:
    if not isinstance(evidence, Mapping):
        raise TypeError("schema evidence must be a mapping")
    if not isinstance(provenance, DevProvenanceEvidence):
        raise TypeError("provenance must be DevProvenanceEvidence")
    if "remote_dev_provenance" in evidence:
        raise PropertyCatalogDevRuntimeError(
            "schema evidence attempted to replace frozen DEV provenance"
        )
    return {**evidence, "remote_dev_provenance": provenance.as_dict()}


def _status_target_tables(evidence: Mapping[str, Any]) -> list[dict[str, str]]:
    raw_tables = evidence.get("target_tables")
    if not isinstance(raw_tables, list) or len(raw_tables) != len(
        PROPERTY_CATALOG_TABLES
    ):
        raise PropertyCatalogDevRuntimeError(
            "schema evidence does not contain the exact six target tables"
        )
    result: list[dict[str, str]] = []
    for raw_table in raw_tables:
        if not isinstance(raw_table, Mapping):
            raise PropertyCatalogDevRuntimeError(
                "schema evidence contains an invalid target table"
            )
        name = raw_table.get("name")
        engine = raw_table.get("engine")
        if name not in PROPERTY_CATALOG_TABLES or not isinstance(engine, str):
            raise PropertyCatalogDevRuntimeError(
                "schema evidence contains an unexpected target table"
            )
        result.append({"engine": engine, "name": name})
    if {table["name"] for table in result} != PROPERTY_CATALOG_TABLES:
        raise PropertyCatalogDevRuntimeError(
            "schema evidence contains duplicate target tables"
        )
    return sorted(result, key=lambda table: table["name"])


def _planned_streams(
    *,
    build_token: str,
    hot_producer_stream_id: str,
    postgres_source_fence: int,
    span_audit_generation: int,
) -> tuple[BuildPlanStream, ...]:
    namespace = uuid.UUID(canonical_uuid(build_token, field="build_token"))
    hot_producer_stream_id = canonical_uuid(
        hot_producer_stream_id, field="hot_producer_stream_id"
    )
    streams: list[BuildPlanStream] = []
    for adapter in SourceAdapter:
        roles = (
            (
                ManifestStreamRole.DEFINITIONS,
                ManifestStreamRole.VALUES,
                ManifestStreamRole.HOT_VALUES,
                ManifestStreamRole.SOURCE_AUDIT,
            )
            if adapter is SourceAdapter.SPAN_ATTRIBUTE
            else (ManifestStreamRole.DEFINITIONS,)
        )
        for role in roles:
            stream_id = (
                hot_producer_stream_id
                if role is ManifestStreamRole.HOT_VALUES
                else str(
                    uuid.uuid5(
                        namespace,
                        f"futureagi.property-catalog:{adapter}:{role}",
                    )
                )
            )
            span = adapter is SourceAdapter.SPAN_ATTRIBUTE
            streams.append(
                BuildPlanStream(
                    source_adapter=adapter,
                    role=role,
                    producer_stream_id=stream_id,
                    source_cutoff_label=(
                        SPAN_AUDIT_CUTOFF_LABEL
                        if span
                        else (
                            "system_manifest_snapshot_cutoff"
                            if adapter is SourceAdapter.SYSTEM_MANIFEST
                            else "postgres_snapshot_upper_cutoff"
                        )
                    ),
                    source_version_fence=(
                        span_audit_generation if span else postgres_source_fence
                    ),
                )
            )
    result = tuple(streams)
    if len(result) != 10 or len({stream.key for stream in result}) != 10:
        raise PropertyCatalogDevRuntimeError(
            "runtime did not construct the exact ten-stream build plan"
        )
    return result


def _rehydrate_completed_definition_resume(
    *,
    adapter: DefinitionSourceAdapter,
    request: ReconcileRequest,
) -> ReconcileResult | None:
    """Carry exact terminal non-Postgres evidence without reopening its source."""

    resume = request.resume
    if resume is None or resume.checkpoint.status is not CheckpointStatus.COMPLETE:
        return None
    source_adapter = adapter.source_adapter
    if source_adapter not in {
        SourceAdapter.SYSTEM_MANIFEST,
        SourceAdapter.SPAN_ATTRIBUTE,
    }:
        raise PropertyCatalogDevRuntimeError(
            "completed definition resume is not a non-Postgres stream"
        )
    checkpoint = resume.checkpoint
    context = request.context
    if (
        checkpoint.organization_id != context.organization_id
        or checkpoint.workspace_id != context.workspace_id
        or checkpoint.catalog_epoch != context.catalog_epoch
        or checkpoint.catalog_revision != context.catalog_revision
        or checkpoint.build_token != request.build_token
        or checkpoint.projection_version != context.projection_version
        or checkpoint.source_adapter is not source_adapter
        or checkpoint.producer_stream_id != request.producer_stream_id
        or checkpoint.source_version_fence != request.source_version
        or resume.source_version_fence != request.source_version
    ):
        raise PropertyCatalogDevRuntimeError(
            "completed non-Postgres definition resume changed scope"
        )
    try:
        _checkpoint_stream_proof(checkpoint)
    except PropertyCatalogDevRuntimeError as exc:
        raise PropertyCatalogDevRuntimeError(
            "completed non-Postgres definition resume has unsafe terminal evidence"
        ) from exc
    if (
        checkpoint.value_count != 0
        or checkpoint.tombstone_count > checkpoint.definition_count
        or checkpoint.delivery_count != checkpoint.last_sequence
        or resume.source_cursor
        or resume.source_fingerprint != checkpoint.source_digest
        or resume.previous_payload_sha256 != checkpoint.terminal_payload_sha256
        or resume.processed_rows != checkpoint.source_count
        or resume.gap_reasons
    ):
        raise PropertyCatalogDevRuntimeError(
            "completed non-Postgres definition resume has inconsistent evidence"
        )
    return ReconcileResult(
        # Recovery performs no source read. This explicit zero-page snapshot keeps
        # the persisted source digest attached to the exact terminal checkpoint.
        snapshot=SourceSnapshot(
            source_adapter=source_adapter,
            records=(),
            next_cursor=None,
            terminal=True,
            source_count=0,
            source_bytes=0,
            source_digest=checkpoint.source_digest,
            page_count=0,
        ),
        envelopes=(),
        payload_sha256s=(),
        checkpoint_write=resume,
    )


def _checkpoint_stream_proof(checkpoint: CatalogCheckpoint) -> StreamDrainProof:
    if (
        checkpoint.status is not CheckpointStatus.COMPLETE
        or not checkpoint.terminal
        or checkpoint.gap_count
        or checkpoint.poison_count
        or checkpoint.conflict_count
        or checkpoint.first_sequence != 1
        or checkpoint.last_sequence is None
        or checkpoint.last_sequence < 1
        or checkpoint.last_issued_sequence != checkpoint.last_sequence
        or checkpoint.fenced_sequence != checkpoint.last_sequence
    ):
        raise PropertyCatalogDevRuntimeError(
            "non-hot stream is not terminal and fenced at one contiguous tail"
        )
    return StreamDrainProof(
        source_adapter=checkpoint.source_adapter,
        producer_stream_id=checkpoint.producer_stream_id,
        last_issued_sequence=checkpoint.last_sequence,
        fenced_sequence=checkpoint.last_sequence,
        terminal_sequence=checkpoint.last_sequence,
        terminal_payload_sha256=checkpoint.terminal_payload_sha256,
    )


def _activation_manifest(
    execution: _RevisionExecution,
    *,
    checkpoints: Sequence[CatalogCheckpoint] | None = None,
) -> ActivationManifest:
    selected = tuple(
        execution.checkpoints.values() if checkpoints is None else checkpoints
    )
    plan_by_key = {stream.key: stream for stream in execution.lease.build_plan.streams}
    if (
        len(selected) != 10
        or len({checkpoint.key for checkpoint in selected}) != 10
        or {checkpoint.key for checkpoint in selected} != set(plan_by_key)
    ):
        raise PropertyCatalogDevRuntimeError(
            "activation manifest requires the exact ten planned checkpoints"
        )
    return ActivationManifest(
        organization_id=execution.context.organization_id,
        workspace_id=execution.context.workspace_id,
        catalog_epoch=execution.context.catalog_epoch,
        catalog_revision=execution.context.catalog_revision,
        build_token=execution.lease.build_token,
        projection_version=execution.context.projection_version,
        lifecycle_mode=execution.prepared.lifecycle_mode,
        lineage_anchor_revision=execution.prepared.lineage_anchor_revision,
        streams=tuple(
            ManifestStream(
                requirement=stream_requirement(checkpoint),
                role=plan_by_key[checkpoint.key].role,
            )
            for checkpoint in sorted(selected, key=lambda item: item.key)
        ),
    )


def _active_value_inventory_sql(database: str) -> str:
    """Count one logical value snapshot over the exact activation lineage.

    The current revision is fenced but not active yet, so it is added as one
    explicit lineage row.  Every earlier revision is admitted only through a
    conflict-checked active activation whose persisted anchor and projection
    match the manifest being activated.
    """

    require_catalog_database(database)
    return f"""
WITH activation_versioned AS
(
    SELECT
        *,
        max(_version) OVER (
            PARTITION BY organization_id, workspace_id,
                         catalog_epoch, catalog_revision, build_token
        ) AS latest_version
    FROM `{database}`.`property_catalog_activations`
    PREWHERE organization_id = %(organization_id)s
      AND workspace_id = %(workspace_id)s
      AND catalog_epoch = %(catalog_epoch)s
      AND catalog_revision >= %(lineage_anchor_revision)s
      AND catalog_revision < %(catalog_revision)s
), activation_states AS
(
    SELECT
        versioned.catalog_epoch,
        versioned.catalog_revision,
        versioned.build_token,
        argMax(versioned.projection_version, versioned._version) AS projection_version,
        argMax(versioned.lifecycle_mode, versioned._version) AS lifecycle_mode,
        argMax(versioned.lineage_anchor_revision, versioned._version)
            AS lineage_anchor_revision,
        argMax(versioned.activation_sequence, versioned._version)
            AS activation_sequence,
        argMax(versioned.source_manifest_sha256, versioned._version)
            AS source_manifest_sha256,
        argMax(versioned.activation_sha256, versioned._version)
            AS activation_sha256,
        argMax(versioned.status, versioned._version) AS status,
        argMax(versioned.qualified_at, versioned._version) AS qualified_at,
        uniqExactIf(
            tuple(
                versioned.projection_version,
                versioned.lifecycle_mode,
                versioned.lineage_anchor_revision,
                versioned.activation_sequence,
                versioned.source_manifest_json,
                versioned.source_manifest_sha256,
                versioned.revision_fence_sha256,
                versioned.activation_sha256,
                versioned.status,
                versioned.live_definition_rows,
                versioned.tombstone_rows,
                versioned.value_rows,
                versioned.qualified_at,
                versioned.updated_at
            ),
            versioned._version = versioned.latest_version
        ) AS latest_state_variants
    FROM activation_versioned AS versioned
    GROUP BY
        versioned.catalog_epoch,
        versioned.catalog_revision,
        versioned.build_token
), active_candidates AS
(
    SELECT *
    FROM activation_states
    WHERE latest_state_variants = 1
      AND status = 'active'
      AND qualified_at IS NOT NULL
), active_lineage AS
(
    SELECT
        candidate.catalog_epoch,
        candidate.catalog_revision,
        any(candidate.build_token) AS build_token,
        any(candidate.projection_version) AS projection_version,
        any(candidate.lineage_anchor_revision) AS lineage_anchor_revision,
        any(candidate.activation_sequence) AS activation_sequence,
        count() AS active_builds
    FROM active_candidates AS candidate
    GROUP BY candidate.catalog_epoch, candidate.catalog_revision
    HAVING active_builds = 1
), admitted_lineage AS
(
    SELECT
        catalog_epoch,
        catalog_revision,
        build_token,
        projection_version
    FROM active_lineage
    UNION ALL
    SELECT
        toUInt16(%(catalog_epoch)s) AS catalog_epoch,
        toUInt64(%(catalog_revision)s) AS catalog_revision,
        toUUID(%(build_token)s) AS build_token,
        toUInt16(%(projection_version)s) AS projection_version
), source_values AS
(
    SELECT catalog_value.*
    FROM `{database}`.`span_attribute_value_catalog` AS catalog_value
    INNER JOIN admitted_lineage AS lineage
        ON catalog_value.catalog_epoch = lineage.catalog_epoch
       AND catalog_value.catalog_revision = lineage.catalog_revision
       AND catalog_value.build_token = lineage.build_token
    PREWHERE catalog_value.organization_id = %(organization_id)s
      AND catalog_value.workspace_id = %(workspace_id)s
      AND catalog_value.catalog_epoch = %(catalog_epoch)s
      AND catalog_value.catalog_revision >= %(lineage_anchor_revision)s
      AND catalog_value.catalog_revision <= %(catalog_revision)s
), resolved_values AS
(
    SELECT
        source_value.project_id,
        source_value.source_kind,
        source_value.attribute_key,
        source_value.attribute_type,
        source_value.value_fingerprint,
        uniqExact(tuple(
            source_value.value_json,
            source_value.value_search_text_folded
        )) AS state_variants,
        min(source_value.first_seen) AS first_seen,
        max(source_value.last_seen) AS last_seen
    FROM source_values AS source_value
    GROUP BY
        source_value.project_id,
        source_value.source_kind,
        source_value.attribute_key,
        source_value.attribute_type,
        source_value.value_fingerprint
)
SELECT
    count() AS value_rows,
    countIf(state_variants != 1 OR first_seen > last_seen) AS value_state_conflicts,
    (
        SELECT count()
        FROM activation_states
        WHERE latest_state_variants != 1
    ) AS activation_state_conflicts,
    (
        SELECT count()
        FROM
        (
            SELECT catalog_epoch, catalog_revision
            FROM active_candidates
            GROUP BY catalog_epoch, catalog_revision
            HAVING count() != 1
        )
    ) AS activation_lineage_conflicts,
    (
        SELECT count()
        FROM
        (
            SELECT activation_sequence
            FROM active_candidates
            GROUP BY activation_sequence
            HAVING count() != 1
        )
    ) AS activation_sequence_conflicts,
    (
        SELECT count()
        FROM active_candidates
        WHERE projection_version != %(projection_version)s
           OR lineage_anchor_revision != %(lineage_anchor_revision)s
           OR lineage_anchor_revision > catalog_revision
           OR (
               catalog_revision = lineage_anchor_revision
               AND lifecycle_mode NOT IN ('initial_backfill', 'full_repair')
           )
           OR (
               catalog_revision > lineage_anchor_revision
               AND lifecycle_mode != 'incremental'
           )
    ) AS activation_anchor_conflicts,
    (
        SELECT count()
        FROM active_lineage
    ) AS active_lineage_rows,
    (
        SELECT if(count() = 0, toUInt64(0), max(catalog_revision))
        FROM active_lineage
    ) AS latest_active_revision,
    (
        SELECT if(count() = 0, toUInt64(0), max(activation_sequence))
        FROM active_lineage
    ) AS latest_active_sequence,
    (
        SELECT if(
            count() = 0,
            '',
            argMax(toString(build_token), tuple(catalog_revision, activation_sequence))
        )
        FROM active_lineage
    ) AS latest_active_build_token,
    (
        SELECT if(
            count() = 0,
            toUInt64(%(lineage_anchor_revision)s),
            min(lineage_anchor_revision)
        )
        FROM active_lineage
    ) AS observed_lineage_anchor_revision,
    (
        SELECT countIf(
            catalog_revision = %(prior_revision)s
            AND toString(build_token) = %(prior_build_token)s
        )
        FROM active_lineage
    ) AS prior_active_matches
FROM resolved_values
"""


def _unix_microseconds(value: datetime) -> int:
    _require_utc_runtime(value, "source snapshot cutoff")
    result = int(value.timestamp() * 1_000_000)
    if not 1 <= result < (1 << 64):
        raise PropertyCatalogDevRuntimeError(
            "source snapshot cutoff is outside positive UInt64 microseconds"
        )
    return result


def _require_utc_runtime(value: datetime, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise PropertyCatalogDevRuntimeError(f"{field_name} must be UTC-aware")


def _mapping_setting(settings_object: Any, name: str) -> Mapping[str, Any]:
    value = getattr(settings_object, name, {}) or {}
    if not isinstance(value, Mapping):
        raise PropertyCatalogDevRuntimeError(f"{name} must be a mapping")
    return value


def _provenance_text(value: Any, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > 512
        or any(character in value for character in "\r\n\x00")
    ):
        raise PropertyCatalogDevRuntimeError(
            f"{field_name} must be one bounded exact identity"
        )
    return value


def _clickhouse_hostname_allowlist(
    *,
    singular: Any,
    plural: Any,
    field_name: str,
) -> tuple[str, ...]:
    if not isinstance(plural, (tuple, list)):
        raise PropertyCatalogDevRuntimeError(
            f"{field_name} must be an exact tuple or list"
        )
    values = tuple(plural)
    if singular:
        singular_hostname = _provenance_text(
            singular,
            field_name.removesuffix("s"),
        )
        if values and singular_hostname not in values:
            raise PropertyCatalogDevRuntimeError(
                f"{field_name} conflicts with its singular compatibility setting"
            )
        if not values:
            values = (singular_hostname,)
    if (
        not values
        or len(values) > MAX_EXPECTED_CLICKHOUSE_HOSTNAMES
        or any(not isinstance(value, str) for value in values)
    ):
        raise PropertyCatalogDevRuntimeError(
            f"{field_name} must contain 1..{MAX_EXPECTED_CLICKHOUSE_HOSTNAMES} "
            "exact hostnames"
        )
    canonical = tuple(sorted(_provenance_text(value, field_name) for value in values))
    if len(set(canonical)) != len(canonical):
        raise PropertyCatalogDevRuntimeError(
            f"{field_name} must contain unique exact hostnames"
        )
    return canonical


def _expected_clickhouse_hostnames_setting(
    settings_object: Any,
    *,
    plural_name: str,
    singular_name: str,
) -> tuple[str, ...]:
    plural = getattr(settings_object, plural_name, ())
    singular = getattr(settings_object, singular_name, "")
    return _clickhouse_hostname_allowlist(
        singular=singular,
        plural=plural,
        field_name=plural_name,
    )


def _observed_text(value: Any, field_name: str) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PropertyCatalogDevRuntimeError(
                f"{field_name} is not valid UTF-8"
            ) from exc
    return _provenance_text(value, field_name)


def _observed_uint(
    value: Any,
    field_name: str,
    *,
    maximum: int = (1 << 64) - 1,
    minimum: int = 0,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise PropertyCatalogDevRuntimeError(f"{field_name} is outside its UInt bound")
    return value


def _observed_bool(value: Any, field_name: str) -> bool:
    if type(value) is bool:
        return value
    if type(value) is int and value in {0, 1}:
        return bool(value)
    raise PropertyCatalogDevRuntimeError(f"{field_name} is not an exact boolean")


def _observed_on_off(value: Any, field_name: str) -> bool:
    text = _observed_text(value, field_name).casefold()
    if text not in {"on", "off"}:
        raise PropertyCatalogDevRuntimeError(f"{field_name} is not on or off")
    return text == "on"


def _canonical_ip(value: Any, field_name: str) -> str:
    text = _provenance_text(value, field_name)
    try:
        return str(ipaddress.ip_address(text))
    except ValueError as exc:
        raise PropertyCatalogDevRuntimeError(
            f"{field_name} must be one literal IPv4 or IPv6 address"
        ) from exc


def _required_text(settings_object: Any, name: str) -> str:
    value = getattr(settings_object, name, None)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character in value for character in "\r\n")
    ):
        raise PropertyCatalogDevRuntimeError(f"{name} must be configured exactly")
    return value


def _password_setting(settings_object: Any, name: str) -> str:
    value = getattr(settings_object, name, None)
    if not isinstance(value, str) or any(character in value for character in "\r\n"):
        raise PropertyCatalogDevRuntimeError(f"{name} must be configured")
    return value


def _strict_port(value: Any, name: str) -> int:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if type(value) is not int or not 1 <= value <= 65_535:
        raise PropertyCatalogDevRuntimeError(f"{name} must be a valid native port")
    return value


def _strict_bool(value: Any, name: str) -> bool:
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.strip().lower() in {
        "true",
        "false",
        "1",
        "0",
    }:
        return value.strip().lower() in {"true", "1"}
    raise PropertyCatalogDevRuntimeError(f"{name} must be a bool")


def _strict_positive_int_setting(
    settings_object: Any,
    name: str,
    *,
    default: int | None = None,
) -> int:
    value = getattr(settings_object, name, default)
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if type(value) is not int or value < 1:
        raise PropertyCatalogDevRuntimeError(f"{name} must be a positive integer")
    return value


def _project_allowlist_setting(settings_object: Any) -> tuple[str, ...]:
    name = "PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST"
    value = getattr(settings_object, name, ())
    if isinstance(value, str):
        values = tuple(item.strip() for item in value.split(",") if item.strip())
    elif isinstance(value, Sequence):
        values = tuple(value)
    else:
        raise PropertyCatalogDevRuntimeError(f"{name} must be a sequence")
    if any(not isinstance(item, str) for item in values):
        raise PropertyCatalogDevRuntimeError(f"{name} must contain only UUID text")
    return values


def _datetime_setting(settings_object: Any, name: str) -> datetime:
    return _datetime_value(getattr(settings_object, name, None), name)


def _datetime_value(value: Any, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PropertyCatalogDevRuntimeError(
                f"{name} must be an ISO-8601 UTC hour"
            ) from exc
    if not isinstance(value, datetime):
        raise PropertyCatalogDevRuntimeError(f"{name} must be an ISO-8601 UTC hour")
    if value.tzinfo is None:
        raise PropertyCatalogDevRuntimeError(f"{name} must be an ISO-8601 UTC hour")
    return value.astimezone(UTC)


def _utc_hour(value: datetime, name: str) -> None:
    if (
        value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
        or any((value.minute, value.second, value.microsecond))
    ):
        raise PropertyCatalogDevRuntimeError(f"{name} must be aligned to a UTC hour")


def _existing_directory(value: str, name: str) -> None:
    path = Path(value)
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise PropertyCatalogDevRuntimeError(
            f"{name} must be an existing absolute non-symlink directory"
        )


def _safe_runtime_file(value: str, name: str) -> None:
    path = Path(value)
    if not path.is_absolute() or not path.parent.is_dir() or path.parent.is_symlink():
        raise PropertyCatalogDevRuntimeError(
            f"{name} must have an existing absolute non-symlink parent"
        )
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise PropertyCatalogDevRuntimeError(
            f"{name} must be absent or a regular non-symlink file"
        )


def _bounded_catalog_timeout(value: int) -> None:
    maximum_ms = RUNTIME_LIMITS.state_store_timeout_ms
    if type(value) is not int or not 1 <= value <= maximum_ms:
        raise PropertyCatalogDevRuntimeError(
            f"catalog query timeout must be in [1, {maximum_ms}] ms"
        )


def _bounded_canonical_span_timeout(
    value: int,
    *,
    explicit_initial_backfill: bool,
) -> None:
    cap_ms = (
        DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS
        if explicit_initial_backfill
        else CANONICAL_SPAN_QUERY_TIMEOUT_MS
    )
    if type(value) is not int or not 1 <= value <= cap_ms:
        raise PropertyCatalogDevRuntimeError(
            "canonical-span query timeout must be in "
            f"[1, {cap_ms}] ms for this runtime mode"
        )


def _nonnegative_int(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value < (1 << 64):
        raise PropertyCatalogDevRuntimeError(f"{name} is not a UInt64")
    return value


__all__ = [
    "CheckedInPropertyCatalogDevRuntime",
    "CHECKED_IN_DEV_RUNTIME_FACTORY_PATH",
    "CHECKED_IN_PRODUCTION_RUNTIME_FACTORY_PATH",
    "ClickHouseDevIdentity",
    "DEV_SIDECAR_ACK",
    "DevProvenanceEvidence",
    "DevProvenanceExpectation",
    "DevProvenanceObservation",
    "DevProvenanceProbe",
    "DevRuntimeConfig",
    "HotDrainProofSource",
    "NativeCatalogClient",
    "NativeClickHouseDriver",
    "NativeConnectionConfig",
    "NativeSchemaClient",
    "NativeSourceClient",
    "PostgresDevIdentity",
    "PostgresProjectTenantBinding",
    "ProjectTenantAuthorization",
    "ProjectTenantBindingProbe",
    "PropertyCatalogDevRuntimeError",
    "PropertyCatalogDevRuntimeFactory",
    "PropertyCatalogProductionRuntimeFactory",
    "PropertyCatalogHotDrainHandshakeUnavailable",
    "SharedVolumeHotDrainProofSource",
    "configured_property_catalog_dev_runtime",
    "configured_property_catalog_production_runtime",
    "ensure_dev_catalog_schema",
    "require_checked_in_property_catalog_dev_runtime",
    "require_checked_in_property_catalog_production_runtime",
    "verify_dev_catalog_schema",
    "verify_runtime_catalog_schema",
]
