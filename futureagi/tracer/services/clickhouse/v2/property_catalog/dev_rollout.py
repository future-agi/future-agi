"""Executable, fail-closed control plane for the isolated unified DEV catalog.

Dry-run is a pure plan and never constructs a runtime. Status is read-only.
Execute runs the fixed schema/backfill/reconcile/qualify/activate sequence; its
PostgreSQL stage always goes through the revision-wide snapshot executor.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from django.conf import settings

from .codec import canonical_uuid
from .postgres_executor import (
    PostgresRevisionReconciler,
    PostgresRevisionReconcileResult,
    ReconcileRequestFactory,
    reconcile_postgres_revision,
)
from .publisher import (
    PROPERTY_CATALOG_TABLES,
    PropertyCatalogPublishError,
    require_dev_catalog_database,
)
from .reconciler import ReconcileMode
from .source_adapters import DefinitionSourceAdapter

DEV_ROLLOUT_ACK = "PROPERTY_CATALOG_DEV_ROLLOUT"
DEV_ENVIRONMENT = "development"
DEV_CLOUD_DEPLOYMENT = "DEV"
DEV_CONTROL_PLANE_ENVIRONMENTS = frozenset({"dev", DEV_ENVIRONMENT, "staging"})
DEV_STANDARD_MAX_WALL_MS = settings.PROPERTY_CATALOG_DEV_STANDARD_MAX_WALL_MS
DEV_INITIAL_BACKFILL_MAX_WALL_MS = (
    settings.PROPERTY_CATALOG_DEV_INITIAL_BACKFILL_MAX_WALL_MS
)
DEV_SCHEDULED_RECONCILE_MAX_WALL_MS = (
    settings.PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_MAX_WALL_MS
)
_DEV_IDENTITY_RE = re.compile(r"^dev:[a-z0-9][a-z0-9._:/-]{2,127}$")
_SOURCE_DATABASE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_dev_control_plane_cloud_allowed(
    *,
    environment: str,
    cloud_deployment: str,
) -> bool:
    """Admit the existing DEV marker or an exact OSS-local development scope."""

    if environment not in DEV_CONTROL_PLANE_ENVIRONMENTS:
        return False
    return cloud_deployment == DEV_CLOUD_DEPLOYMENT or (
        cloud_deployment == "" and environment == DEV_ENVIRONMENT
    )


def dev_control_plane_matches_request(
    *,
    environment: str,
    cloud_deployment: str,
    request_environment: str,
    request_cloud_deployment: str,
) -> bool:
    """Require runtime and request scopes to agree without narrowing DEV aliases."""

    if not is_dev_control_plane_cloud_allowed(
        environment=environment,
        cloud_deployment=cloud_deployment,
    ):
        return False
    if request_environment != DEV_ENVIRONMENT or not is_dev_control_plane_cloud_allowed(
        environment=request_environment,
        cloud_deployment=request_cloud_deployment,
    ):
        return False
    if cloud_deployment != request_cloud_deployment:
        return False
    return cloud_deployment == DEV_CLOUD_DEPLOYMENT or (
        environment == request_environment == DEV_ENVIRONMENT
    )


class DevRolloutStage(StrEnum):
    SCHEMA = "schema"
    BACKFILL = "backfill"
    RECONCILE = "reconcile"
    QUALIFY = "qualify"
    ACTIVATE = "activate"


class DevRolloutMode(StrEnum):
    DRY_RUN = "dry_run"
    STATUS = "status"
    EXECUTE = "execute"


class DevRolloutError(RuntimeError):
    """The configured lifecycle cannot prove its isolated DEV boundary."""


def validate_rollout_request_common(request: Any) -> None:
    """Validate deployment-independent rollout fields before any client exists."""

    object.__setattr__(
        request,
        "organization_id",
        canonical_uuid(request.organization_id, field="organization_id"),
    )
    object.__setattr__(
        request,
        "workspace_id",
        canonical_uuid(request.workspace_id, field="workspace_id"),
    )
    if (
        not isinstance(request.source_database, str)
        or _SOURCE_DATABASE_RE.fullmatch(request.source_database) is None
    ):
        raise DevRolloutError("source_database must be one safe identifier")
    if request.source_database == request.target_database:
        raise DevRolloutError("source and isolated catalog databases must differ")
    if (
        type(request.execute) is not bool
        or type(request.status) is not bool
        or type(request.repair_expired_incomplete) is not bool
    ):
        raise DevRolloutError(
            "execute, status, and repair_expired_incomplete must be bools"
        )
    if request.execute and request.status:
        raise DevRolloutError("execute and status are mutually exclusive")
    if request.repair_expired_incomplete and (not request.execute or request.status):
        raise DevRolloutError("expired incomplete repair requires explicit --execute")
    if request.initial_backfill_wall_ms is not None:
        if (
            type(request.initial_backfill_wall_ms) is not int
            or not DEV_STANDARD_MAX_WALL_MS
            < request.initial_backfill_wall_ms
            <= DEV_INITIAL_BACKFILL_MAX_WALL_MS
        ):
            raise DevRolloutError(
                "explicit initial backfill wall must be in "
                f"[{DEV_STANDARD_MAX_WALL_MS + 1}, "
                f"{DEV_INITIAL_BACKFILL_MAX_WALL_MS}] ms"
            )
        if not request.execute or request.status:
            raise DevRolloutError("explicit initial backfill wall requires --execute")
    if request.scheduled_reconcile_wall_ms is not None:
        if (
            type(request.scheduled_reconcile_wall_ms) is not int
            or not DEV_STANDARD_MAX_WALL_MS
            < request.scheduled_reconcile_wall_ms
            <= DEV_SCHEDULED_RECONCILE_MAX_WALL_MS
        ):
            raise DevRolloutError(
                "explicit scheduled reconcile wall must be in "
                f"[{DEV_STANDARD_MAX_WALL_MS + 1}, "
                f"{DEV_SCHEDULED_RECONCILE_MAX_WALL_MS}] ms"
            )
        if not request.execute or request.status:
            raise DevRolloutError(
                "explicit scheduled reconcile wall requires execute mode"
            )
        if request.initial_backfill_wall_ms is not None:
            raise DevRolloutError(
                "initial backfill and scheduled reconcile walls are mutually exclusive"
            )


@dataclass(frozen=True, slots=True)
class DevRolloutRequest:
    organization_id: str
    workspace_id: str
    environment: str
    cloud_deployment: str
    dev_identity: str
    source_database: str
    target_database: str
    acknowledgement: str
    execute: bool = False
    status: bool = False
    initial_backfill_wall_ms: int | None = None
    scheduled_reconcile_wall_ms: int | None = None
    repair_expired_incomplete: bool = False

    def __post_init__(self) -> None:
        validate_rollout_request_common(self)
        if self.environment != DEV_ENVIRONMENT:
            raise DevRolloutError("unified catalog rollout is development-only")
        if not is_dev_control_plane_cloud_allowed(
            environment=self.environment,
            cloud_deployment=self.cloud_deployment,
        ):
            raise DevRolloutError(
                "unified catalog rollout requires CLOUD_DEPLOYMENT=DEV, or an "
                "unset CLOUD_DEPLOYMENT only for OSS development"
            )
        identity = str(self.dev_identity or "")
        folded_identity = identity.casefold()
        if (
            _DEV_IDENTITY_RE.fullmatch(identity) is None
            or "prod" in folded_identity
            or "live" in folded_identity
        ):
            raise DevRolloutError(
                "unified catalog rollout requires a pinned non-production dev identity"
            )
        try:
            require_dev_catalog_database(self.target_database)
        except PropertyCatalogPublishError as exc:
            raise DevRolloutError(
                "target_database must be an exact lowercase isolated DEV identifier"
            ) from exc
        if self.acknowledgement != DEV_ROLLOUT_ACK:
            raise DevRolloutError(
                "the exact unified DEV rollout acknowledgement is required"
            )

    @property
    def mode(self) -> DevRolloutMode:
        if self.status:
            return DevRolloutMode.STATUS
        return DevRolloutMode.EXECUTE if self.execute else DevRolloutMode.DRY_RUN


@dataclass(frozen=True, slots=True)
class DevRolloutPlan:
    source_database: str
    target_database: str
    mode: DevRolloutMode
    stages: tuple[DevRolloutStage, ...]
    write_allowlist: tuple[str, ...]
    legacy_source_access: str = "select_only"
    zero_io: bool = False
    initial_backfill_wall_ms: int | None = None
    scheduled_reconcile_wall_ms: int | None = None
    repair_expired_incomplete: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "legacy_source_access": self.legacy_source_access,
            "initial_backfill_wall_ms": self.initial_backfill_wall_ms,
            "scheduled_reconcile_wall_ms": self.scheduled_reconcile_wall_ms,
            "mode": self.mode,
            "repair_expired_incomplete": self.repair_expired_incomplete,
            "source_database": self.source_database,
            "stages": list(self.stages),
            "target_database": self.target_database,
            "write_allowlist": list(self.write_allowlist),
            "zero_io": self.zero_io,
        }


@dataclass(frozen=True, slots=True)
class DevRolloutEvidence:
    stage: DevRolloutStage
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DevRolloutResult:
    plan: DevRolloutPlan
    completed: tuple[DevRolloutStage, ...]
    evidence: tuple[DevRolloutEvidence, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "completed": list(self.completed),
            "evidence": [
                {"evidence": dict(item.evidence), "stage": item.stage}
                for item in self.evidence
            ],
            "plan": self.plan.as_dict(),
        }


class ConfiguredDevRolloutRuntime(Protocol):
    """Runtime factory result; construction happens only after DEV validation."""

    def status(self, request: DevRolloutRequest) -> Mapping[str, Any]: ...

    def verify_schema(self, request: DevRolloutRequest) -> Mapping[str, Any]: ...

    def reconcile_workspace(
        self,
        request: DevRolloutRequest,
        *,
        mode: ReconcileMode,
    ) -> Mapping[str, Any]: ...

    def apply_schema(self, request: DevRolloutRequest) -> Mapping[str, Any]: ...

    def backfill(self, request: DevRolloutRequest) -> Mapping[str, Any]: ...

    def postgres_reconciler(
        self, request: DevRolloutRequest
    ) -> PostgresRevisionReconciler: ...

    def postgres_request_factory(
        self, request: DevRolloutRequest
    ) -> ReconcileRequestFactory: ...

    def postgres_snapshot_guard(
        self, request: DevRolloutRequest
    ) -> Callable[[], None]: ...

    def postgres_adapters(
        self, request: DevRolloutRequest
    ) -> Sequence[DefinitionSourceAdapter] | None: ...

    def reconcile_non_postgres(
        self,
        request: DevRolloutRequest,
        postgres: PostgresRevisionReconcileResult,
    ) -> Mapping[str, Any]: ...

    def qualify(self, request: DevRolloutRequest) -> Mapping[str, Any]: ...

    def activate(self, request: DevRolloutRequest) -> Mapping[str, Any]: ...


class UnifiedDevRollout:
    """Run the clean catalog lifecycle in one fixed, non-skippable order."""

    _STAGES = (
        DevRolloutStage.SCHEMA,
        DevRolloutStage.BACKFILL,
        DevRolloutStage.RECONCILE,
        DevRolloutStage.QUALIFY,
        DevRolloutStage.ACTIVATE,
    )

    @classmethod
    def plan(cls, request: DevRolloutRequest) -> DevRolloutPlan:
        return DevRolloutPlan(
            source_database=request.source_database,
            target_database=request.target_database,
            mode=request.mode,
            stages=cls._STAGES,
            write_allowlist=tuple(sorted(PROPERTY_CATALOG_TABLES)),
            zero_io=request.mode is DevRolloutMode.DRY_RUN,
            initial_backfill_wall_ms=request.initial_backfill_wall_ms,
            scheduled_reconcile_wall_ms=request.scheduled_reconcile_wall_ms,
            repair_expired_incomplete=request.repair_expired_incomplete,
        )

    def run(
        self,
        request: DevRolloutRequest,
        *,
        runtime: ConfiguredDevRolloutRuntime | None,
    ) -> DevRolloutResult:
        plan = self.plan(request)
        if request.mode is DevRolloutMode.DRY_RUN:
            if runtime is not None:
                raise DevRolloutError("dry-run must not construct a runtime")
            return DevRolloutResult(plan=plan, completed=(), evidence=())
        if runtime is None:
            raise DevRolloutError("status/execute requires a configured DEV runtime")
        if request.mode is DevRolloutMode.STATUS:
            evidence = _evidence(DevRolloutStage.QUALIFY, runtime.status(request))
            return DevRolloutResult(plan=plan, completed=(), evidence=(evidence,))

        evidence: list[DevRolloutEvidence] = []
        evidence.append(
            _evidence(DevRolloutStage.SCHEMA, runtime.apply_schema(request))
        )
        backfill = dict(runtime.backfill(request))
        evidence.append(_evidence(DevRolloutStage.BACKFILL, backfill))
        fenced_recovery = backfill.get("fenced_recovery", False)
        if type(fenced_recovery) is not bool:
            raise DevRolloutError("backfill fenced_recovery evidence must be a bool")
        if fenced_recovery:
            # A fenced crash recovery already has ten terminal, physically
            # audited streams.  Reopening source snapshots or publishing any
            # definitions here would violate the immutable fence.
            reconcile_evidence = {
                "fenced_recovery": True,
                "source_reconciliation_skipped": True,
            }
        else:
            postgres = reconcile_postgres_revision(
                reconciler=runtime.postgres_reconciler(request),
                request_factory=runtime.postgres_request_factory(request),
                adapters=runtime.postgres_adapters(request),
                snapshot_guard=runtime.postgres_snapshot_guard(request),
            )
            reconcile_evidence = dict(runtime.reconcile_non_postgres(request, postgres))
            reconcile_evidence.update(
                {
                    "postgres_adapter_count": len(postgres.adapter_results),
                    "postgres_snapshot_opened": postgres.postgres_snapshot_opened,
                }
            )
        evidence.append(_evidence(DevRolloutStage.RECONCILE, reconcile_evidence))
        evidence.append(_evidence(DevRolloutStage.QUALIFY, runtime.qualify(request)))
        evidence.append(_evidence(DevRolloutStage.ACTIVATE, runtime.activate(request)))
        return DevRolloutResult(
            plan=plan,
            completed=self._STAGES,
            evidence=tuple(evidence),
        )


def run_configured_dev_rollout(
    *,
    request: DevRolloutRequest,
    runtime: ConfiguredDevRolloutRuntime | None,
) -> DevRolloutResult:
    """Stable service entrypoint shared by the command and DEV activity."""

    return UnifiedDevRollout().run(request, runtime=runtime)


def configured_dev_rollout_request(
    *,
    organization_id: str,
    workspace_id: str,
    settings_object: Any,
    execute: bool,
    status: bool = False,
    initial_backfill_wall_ms: int | None = None,
    scheduled_reconcile_wall_ms: int | None = None,
    repair_expired_incomplete: bool = False,
    overrides: Mapping[str, str | None] | None = None,
) -> DevRolloutRequest:
    """Build one allowlisted request from explicit Django settings."""

    values = dict(overrides or {})

    def configured(argument: str, setting: str) -> str:
        value = values.get(argument) or getattr(settings_object, setting, "")
        return str(value)

    return DevRolloutRequest(
        organization_id=organization_id,
        workspace_id=workspace_id,
        environment=configured("environment", "PROPERTY_CATALOG_DEV_ENVIRONMENT"),
        cloud_deployment=configured(
            "cloud_deployment", "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT"
        ),
        dev_identity=configured("dev_identity", "PROPERTY_CATALOG_DEV_IDENTITY"),
        source_database=configured(
            "source_database", "PROPERTY_CATALOG_DEV_SOURCE_DATABASE"
        ),
        target_database=configured(
            "target_database", "PROPERTY_CATALOG_DEV_TARGET_DATABASE"
        ),
        acknowledgement=configured(
            "acknowledgement", "PROPERTY_CATALOG_DEV_ACKNOWLEDGEMENT"
        ),
        execute=execute,
        status=status,
        initial_backfill_wall_ms=initial_backfill_wall_ms,
        scheduled_reconcile_wall_ms=scheduled_reconcile_wall_ms,
        repair_expired_incomplete=repair_expired_incomplete,
    )


def run_scheduled_dev_rollout(
    *,
    organization_id: str,
    workspace_id: str,
    settings_object: Any,
    runtime_factory: Callable[[DevRolloutRequest], ConfiguredDevRolloutRuntime],
) -> DevRolloutResult:
    """No-CLI scheduled entrypoint; validation precedes runtime construction."""

    request = configured_dev_rollout_request(
        organization_id=organization_id,
        workspace_id=workspace_id,
        settings_object=settings_object,
        execute=True,
    )
    runtime = runtime_factory(request)
    return run_configured_dev_rollout(request=request, runtime=runtime)


def run_workspace_reconcile(
    *,
    request: DevRolloutRequest,
    runtime: ConfiguredDevRolloutRuntime,
    mode: ReconcileMode,
) -> Mapping[str, Any]:
    """Run one bounded scheduled revision without schema DDL or initial backfill.

    The exact-schema check is read-only.  Initial schema creation remains an
    explicit management-command operation and is never repeated by a tick.
    """

    if request.mode is not DevRolloutMode.EXECUTE:
        raise DevRolloutError("workspace reconciliation requires execute mode")
    if request.initial_backfill_wall_ms is not None:
        raise DevRolloutError(
            "scheduled workspace reconciliation refuses an initial backfill wall"
        )
    if request.scheduled_reconcile_wall_ms is None:
        raise DevRolloutError(
            "scheduled workspace reconciliation requires an explicit extended wall"
        )
    if mode not in {ReconcileMode.INCREMENTAL, ReconcileMode.FULL_REPAIR}:
        raise DevRolloutError("workspace reconciliation mode is unsupported")
    schema = _evidence(DevRolloutStage.SCHEMA, runtime.verify_schema(request))
    result = _evidence(
        DevRolloutStage.RECONCILE,
        runtime.reconcile_workspace(request, mode=mode),
    )
    return {
        "mode": mode,
        "schema": dict(schema.evidence),
        "reconcile": dict(result.evidence),
        "workspace_id": request.workspace_id,
    }


def _evidence(stage: DevRolloutStage, value: Mapping[str, Any]) -> DevRolloutEvidence:
    if not isinstance(value, Mapping):
        raise DevRolloutError(f"{stage} did not return mapping evidence")
    return DevRolloutEvidence(stage=stage, evidence=dict(value))


__all__ = [
    "ConfiguredDevRolloutRuntime",
    "DEV_CLOUD_DEPLOYMENT",
    "DEV_CONTROL_PLANE_ENVIRONMENTS",
    "DEV_ENVIRONMENT",
    "DEV_ROLLOUT_ACK",
    "DEV_INITIAL_BACKFILL_MAX_WALL_MS",
    "DEV_SCHEDULED_RECONCILE_MAX_WALL_MS",
    "DEV_STANDARD_MAX_WALL_MS",
    "DevRolloutError",
    "DevRolloutEvidence",
    "DevRolloutMode",
    "DevRolloutPlan",
    "DevRolloutRequest",
    "DevRolloutResult",
    "DevRolloutStage",
    "UnifiedDevRollout",
    "configured_dev_rollout_request",
    "dev_control_plane_matches_request",
    "is_dev_control_plane_cloud_allowed",
    "run_configured_dev_rollout",
    "run_scheduled_dev_rollout",
    "run_workspace_reconcile",
    "validate_rollout_request_common",
]
