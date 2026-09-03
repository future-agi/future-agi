"""Production-only supervisor for the unified property-catalog lifecycle.

The controller never creates a database or table.  It verifies the existing
``property_catalog`` schema, proves read-only source identities and exact
project tenancy, then either advances an active workspace incrementally or—
only behind a separate bootstrap gate—runs the fixed initial lifecycle.
"""

from __future__ import annotations

import logging
import os
import signal
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from accounts.models.workspace import Workspace
from tracer.models.project import Project
from tracer.services.clickhouse.v2.property_catalog import dev_runtime
from tracer.services.clickhouse.v2.property_catalog.codec import (
    canonical_json,
    canonical_uuid,
)
from tracer.services.clickhouse.v2.property_catalog.dev_rollout import (
    DevRolloutError,
    run_workspace_reconcile,
)
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    DEV_SIDECAR_ACK,
    PostgresDevIdentity,
    PostgresProjectTenantBinding,
    PropertyCatalogProductionRuntimeFactory,
    require_checked_in_property_catalog_production_runtime,
)
from tracer.services.clickhouse.v2.property_catalog.production_rollout import (
    PRODUCTION_CLOUD_DEPLOYMENTS,
    PRODUCTION_ENVIRONMENT,
    PRODUCTION_LIFECYCLE_ACK,
    ProductionRolloutRequest,
    configured_production_rollout_request,
    run_configured_production_rollout,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    PropertyCatalogPublishError,
    require_prod_catalog_database,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import ReconcileMode
from tracer.services.clickhouse.v2.property_catalog.revision_fence_registry import (
    AtomicMultiTenantFenceFile,
)

logger = logging.getLogger(__name__)

_MAX_WORKSPACES = 256
_MAX_PROJECTS_PER_WORKSPACE = 256
_HEALTH_FORMAT = "futureagi.property-catalog-lifecycle-health"
_HEALTH_VERSION = 1


class ProductionLifecycleControllerError(RuntimeError):
    """Production lifecycle admission or one bounded cycle failed safely."""


@dataclass(frozen=True, slots=True)
class ControllerConfig:
    cloud_deployment: str
    source_database: str
    target_database: str
    workspace_ids: tuple[str, ...]
    poll_seconds: int
    failure_backoff_seconds: int
    scheduled_reconcile_wall_ms: int
    span_window_days: int
    health_file: str
    revision_fence_file: str
    bootstrap_enabled: bool
    repair_expired_incomplete: bool


@dataclass(frozen=True, slots=True)
class WorkspaceScope:
    organization_id: str
    workspace_id: str
    is_default: bool
    project_ids: tuple[str, ...]
    legacy_project_ids: tuple[str, ...] = ()

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
        projects = tuple(
            sorted(
                canonical_uuid(value, field="project_id") for value in self.project_ids
            )
        )
        legacy = tuple(
            sorted(
                canonical_uuid(value, field="legacy_project_id")
                for value in self.legacy_project_ids
            )
        )
        if (
            not projects
            or len(projects) > _MAX_PROJECTS_PER_WORKSPACE
            or len(set(projects)) != len(projects)
        ):
            raise ProductionLifecycleControllerError(
                "workspace scope requires 1..256 unique projects"
            )
        if len(set(legacy)) != len(legacy) or not set(legacy).issubset(projects):
            raise ProductionLifecycleControllerError(
                "legacy project scope must be a unique subset of workspace projects"
            )
        if legacy and not self.is_default:
            raise ProductionLifecycleControllerError(
                "workspace-null legacy projects require the default workspace"
            )
        object.__setattr__(self, "project_ids", projects)
        object.__setattr__(self, "legacy_project_ids", legacy)


@dataclass(frozen=True, slots=True)
class SettingsOverlay:
    base: Any
    overrides: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "overrides", MappingProxyType(dict(self.overrides)))

    def __getattr__(self, name: str) -> Any:
        try:
            return self.overrides[name]
        except KeyError:
            return getattr(self.base, name)


@dataclass(frozen=True, slots=True)
class CycleResult:
    processed: tuple[str, ...]
    skipped: tuple[str, ...]
    failures: Mapping[str, str]
    stopped: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "failed": dict(sorted(self.failures.items())),
            "processed": list(self.processed),
            "skipped": list(self.skipped),
            "stopped": self.stopped,
        }


class Command(BaseCommand):
    help = (
        "Continuously advance the existing production unified property catalog "
        "for an exact workspace allowlist."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one bounded discovery/controller cycle and exit.",
        )
        parser.add_argument(
            "--status-only",
            action="store_true",
            help="Verify schema, identities, tenancy, and active state without writes.",
        )

    def handle(self, *args: Any, **options: Any) -> str | None:
        once = bool(options.get("once"))
        status_only = bool(options.get("status_only"))
        stop = threading.Event()
        previous_handlers = _install_signal_handlers(stop)
        try:
            config = controller_config(settings_object=settings)
            while not stop.is_set():
                observed_at = datetime.now(UTC)
                try:
                    scopes, skipped = discover_workspace_scopes(config.workspace_ids)
                    result = run_cycle(
                        scopes=scopes,
                        skipped=skipped,
                        settings_object=settings,
                        config=config,
                        now=observed_at,
                        status_only=status_only,
                        stop=stop,
                        on_error=lambda workspace_id, exc: self.stderr.write(
                            self.style.ERROR(
                                f"workspace {workspace_id} failed safely: {exc}"
                            )
                        ),
                    )
                except Exception as exc:
                    _write_health(
                        config.health_file,
                        healthy=False,
                        observed_at=observed_at,
                        detail={"cycle_error": str(exc)},
                    )
                    if once:
                        raise CommandError(str(exc)) from exc
                    logger.exception(
                        "production property-catalog lifecycle cycle failed safely"
                    )
                    stop.wait(config.failure_backoff_seconds)
                    continue

                healthy = not result.failures
                _write_health(
                    config.health_file,
                    healthy=healthy,
                    observed_at=observed_at,
                    detail=result.as_dict(),
                )
                if once:
                    if result.failures:
                        failed = ", ".join(sorted(result.failures))
                        raise CommandError(
                            f"production lifecycle failed for workspaces: {failed}"
                        )
                    return canonical_json(result.as_dict(), max_bytes=256 * 1024)
                stop.wait(config.poll_seconds)
        except (ProductionLifecycleControllerError, DevRolloutError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        finally:
            _restore_signal_handlers(previous_handlers)
        return None


def controller_config(*, settings_object: Any) -> ControllerConfig:
    environment = str(getattr(settings_object, "ENV_TYPE", "")).strip().lower()
    cloud = str(getattr(settings_object, "CLOUD_DEPLOYMENT", "")).strip()
    if environment not in {"prod", PRODUCTION_ENVIRONMENT}:
        raise ProductionLifecycleControllerError(
            "production lifecycle controller requires ENV_TYPE=production"
        )
    if cloud not in PRODUCTION_CLOUD_DEPLOYMENTS:
        raise ProductionLifecycleControllerError(
            "production lifecycle controller requires an exact supported cloud"
        )
    if (
        getattr(settings_object, "PROPERTY_CATALOG_LIFECYCLE_ENABLED", False)
        is not True
    ):
        raise ProductionLifecycleControllerError(
            "PROPERTY_CATALOG_LIFECYCLE_ENABLED must be true"
        )
    if (
        getattr(settings_object, "PROPERTY_CATALOG_LIFECYCLE_ACK", "")
        != PRODUCTION_LIFECYCLE_ACK
    ):
        raise ProductionLifecycleControllerError(
            "the exact production lifecycle acknowledgement is required"
        )
    source = str(
        getattr(settings_object, "PROPERTY_CATALOG_LIFECYCLE_SOURCE_DATABASE", "")
    ).strip()
    target = str(
        getattr(settings_object, "PROPERTY_CATALOG_LIFECYCLE_TARGET_DATABASE", "")
    ).strip()
    try:
        require_prod_catalog_database(target)
    except PropertyCatalogPublishError as exc:
        raise ProductionLifecycleControllerError(str(exc)) from exc
    if not source or source == target:
        raise ProductionLifecycleControllerError(
            "source and production catalog databases must be distinct"
        )
    workspaces = tuple(
        sorted(
            canonical_uuid(value, field="workspace_id")
            for value in getattr(
                settings_object,
                "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST",
                (),
            )
        )
    )
    if (
        not workspaces
        or len(workspaces) > _MAX_WORKSPACES
        or len(set(workspaces)) != len(workspaces)
    ):
        raise ProductionLifecycleControllerError(
            "production lifecycle requires 1..256 unique allowlisted workspaces"
        )
    runtime_directory = _existing_directory(
        getattr(settings_object, "PROPERTY_CATALOG_LIFECYCLE_RUNTIME_DIRECTORY", ""),
        "runtime directory",
    )
    health_file = _runtime_file(
        getattr(settings_object, "PROPERTY_CATALOG_LIFECYCLE_HEALTH_FILE", ""),
        runtime_directory,
        "health file",
    )
    revision_fence_file = _runtime_file(
        getattr(
            settings_object,
            "PROPERTY_CATALOG_LIFECYCLE_REVISION_FENCE_FILE",
            "",
        ),
        runtime_directory,
        "revision fence file",
    )
    return ControllerConfig(
        cloud_deployment=cloud,
        source_database=source,
        target_database=target,
        workspace_ids=workspaces,
        poll_seconds=_bounded_int_setting(
            settings_object, "PROPERTY_CATALOG_LIFECYCLE_POLL_SECONDS", 5, 3_600
        ),
        failure_backoff_seconds=_bounded_int_setting(
            settings_object,
            "PROPERTY_CATALOG_LIFECYCLE_FAILURE_BACKOFF_SECONDS",
            5,
            3_600,
        ),
        scheduled_reconcile_wall_ms=_positive_int_setting(
            settings_object,
            "PROPERTY_CATALOG_LIFECYCLE_SCHEDULED_RECONCILE_WALL_MS",
        ),
        span_window_days=_bounded_int_setting(
            settings_object,
            "PROPERTY_CATALOG_LIFECYCLE_SPAN_WINDOW_DAYS",
            1,
            366,
        ),
        health_file=health_file,
        revision_fence_file=revision_fence_file,
        bootstrap_enabled=(
            getattr(
                settings_object,
                "PROPERTY_CATALOG_LIFECYCLE_BOOTSTRAP_ENABLED",
                False,
            )
            is True
        ),
        repair_expired_incomplete=(
            getattr(
                settings_object,
                "PROPERTY_CATALOG_LIFECYCLE_REPAIR_EXPIRED_INCOMPLETE",
                False,
            )
            is True
        ),
    )


def discover_workspace_scopes(
    workspace_ids: tuple[str, ...],
) -> tuple[tuple[WorkspaceScope, ...], tuple[str, ...]]:
    rows = list(
        Workspace.no_workspace_objects.filter(id__in=workspace_ids, is_active=True)
        .order_by("organization_id", "id")
        .values_list("id", "organization_id", "is_default")
    )
    observed = {canonical_uuid(row[0], field="workspace_id") for row in rows}
    missing = tuple(sorted(set(workspace_ids) - observed))
    if missing:
        raise ProductionLifecycleControllerError(
            "allowlisted production workspaces are missing or inactive: "
            + ", ".join(missing)
        )
    scopes: list[WorkspaceScope] = []
    skipped: list[str] = []
    for workspace_raw, organization_raw, is_default_raw in rows:
        workspace_id = canonical_uuid(workspace_raw, field="workspace_id")
        organization_id = canonical_uuid(organization_raw, field="organization_id")
        project_filter = Q(
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        if bool(is_default_raw):
            project_filter |= Q(
                organization_id=organization_id,
                workspace_id__isnull=True,
            )
        project_rows = list(
            Project.no_workspace_objects.filter(project_filter)
            .order_by("id")
            .values_list("id", "workspace_id")[: _MAX_PROJECTS_PER_WORKSPACE + 1]
        )
        if len(project_rows) > _MAX_PROJECTS_PER_WORKSPACE:
            raise ProductionLifecycleControllerError(
                f"workspace {workspace_id} exceeds the 256-project bound"
            )
        if not project_rows:
            skipped.append(workspace_id)
            continue
        scopes.append(
            WorkspaceScope(
                organization_id=organization_id,
                workspace_id=workspace_id,
                is_default=bool(is_default_raw),
                project_ids=tuple(str(row[0]) for row in project_rows),
                legacy_project_ids=tuple(
                    str(project_id)
                    for project_id, bound_workspace_id in project_rows
                    if bound_workspace_id is None
                ),
            )
        )
    return tuple(scopes), tuple(sorted(skipped))


def run_cycle(
    *,
    scopes: tuple[WorkspaceScope, ...],
    skipped: tuple[str, ...],
    settings_object: Any,
    config: ControllerConfig,
    now: datetime,
    status_only: bool,
    stop: threading.Event,
    on_error: Callable[[str, Exception], None],
) -> CycleResult:
    authorized_workspaces = tuple(
        sorted((*skipped, *(scope.workspace_id for scope in scopes)))
    )
    if authorized_workspaces != config.workspace_ids:
        raise ProductionLifecycleControllerError(
            "cycle workspace scope inventory does not match the exact allowlist"
        )
    if not status_only and not stop.is_set():
        AtomicMultiTenantFenceFile(
            config.revision_fence_file,
            now=lambda: now,
        ).reconcile_authorized_workspaces(authorized_workspaces)
    processed: list[str] = []
    failures: dict[str, str] = {}
    for scope in scopes:
        if stop.is_set():
            break
        try:
            run_workspace(
                scope=scope,
                settings_object=settings_object,
                config=config,
                now=now,
                status_only=status_only,
                stop=stop,
            )
        except Exception as exc:
            failures[scope.workspace_id] = str(exc)
            on_error(scope.workspace_id, exc)
            continue
        processed.append(scope.workspace_id)
    return CycleResult(
        processed=tuple(processed),
        skipped=skipped,
        failures=MappingProxyType(failures),
        stopped=stop.is_set(),
    )


def run_workspace(
    *,
    scope: WorkspaceScope,
    settings_object: Any,
    config: ControllerConfig,
    now: datetime,
    status_only: bool,
    stop: threading.Event | None = None,
) -> Mapping[str, Any]:
    cancellation_probe = stop.is_set if stop is not None else lambda: False
    proxy = workspace_settings_overlay(
        settings_object=settings_object,
        config=config,
        scope=scope,
        now=now,
    )
    status_request = rollout_request(
        scope=scope,
        proxy=proxy,
        config=config,
        status=True,
    )
    with managed_runtime(
        request=status_request,
        proxy=proxy,
        scope=scope,
        cancellation_probe=cancellation_probe,
    ) as status_runtime:
        status_result = run_configured_production_rollout(
            request=status_request,
            runtime=status_runtime,
        )
    evidence = dict(status_result.evidence[0].evidence)
    if evidence.get("schema_ready") is not True:
        raise ProductionLifecycleControllerError(
            "production catalog schema is absent or drifted; controller is verify-only"
        )
    if status_only:
        return evidence
    if evidence.get("active") is True:
        request = rollout_request(
            scope=scope,
            proxy=proxy,
            config=config,
            scheduled_reconcile_wall_ms=config.scheduled_reconcile_wall_ms,
        )
        with managed_runtime(
            request=request,
            proxy=proxy,
            scope=scope,
            cancellation_probe=cancellation_probe,
        ) as runtime:
            return run_workspace_reconcile(
                request=request,
                runtime=runtime,
                mode=ReconcileMode.INCREMENTAL,
            )
    if not config.bootstrap_enabled:
        raise ProductionLifecycleControllerError(
            "workspace has no active catalog revision and production bootstrap is disabled"
        )
    request = rollout_request(scope=scope, proxy=proxy, config=config)
    with managed_runtime(
        request=request,
        proxy=proxy,
        scope=scope,
        cancellation_probe=cancellation_probe,
    ) as runtime:
        result = run_configured_production_rollout(request=request, runtime=runtime)
    return result.as_dict()


@contextmanager
def managed_runtime(
    *,
    request: ProductionRolloutRequest,
    proxy: SettingsOverlay,
    scope: WorkspaceScope,
    cancellation_probe: Callable[[], bool] = lambda: False,
) -> Iterator[Any]:
    runtime = PropertyCatalogProductionRuntimeFactory(
        settings_object=proxy,
        project_tenant_binding_probe=legacy_aware_project_probe(scope),
        cancellation_probe=cancellation_probe,
    )(request)
    runtime = require_checked_in_property_catalog_production_runtime(runtime)
    try:
        yield runtime
    finally:
        runtime.close()


def legacy_aware_project_probe(
    scope: WorkspaceScope,
) -> Callable[
    [tuple[str, ...], PostgresDevIdentity],
    tuple[PostgresProjectTenantBinding, ...],
]:
    legacy = frozenset(scope.legacy_project_ids)

    def probe(
        project_ids: tuple[str, ...],
        expected_postgres_identity: PostgresDevIdentity,
    ) -> tuple[PostgresProjectTenantBinding, ...]:
        bindings = tuple(
            dev_runtime._postgres_project_tenant_bindings(  # noqa: SLF001
                project_ids,
                expected_postgres_identity,
            )
        )
        mapped: list[PostgresProjectTenantBinding] = []
        for binding in bindings:
            if (
                binding.project_id in legacy
                and binding.workspace_id is None
                and binding.workspace_organization_id is None
                and binding.organization_id == scope.organization_id
                and scope.is_default
            ):
                binding = replace(
                    binding,
                    workspace_id=scope.workspace_id,
                    workspace_organization_id=scope.organization_id,
                )
            mapped.append(binding)
        return tuple(mapped)

    return probe


def workspace_settings_overlay(
    *,
    settings_object: Any,
    config: ControllerConfig,
    scope: WorkspaceScope,
    now: datetime,
) -> SettingsOverlay:
    span_until = utc_hour(now)
    span_since = span_until - timedelta(days=config.span_window_days)
    lifecycle = "PROPERTY_CATALOG_LIFECYCLE_"

    def configured(suffix: str, default: Any = "") -> Any:
        return getattr(settings_object, lifecycle + suffix, default)

    overrides = {
        "PROPERTY_CATALOG_DEV_WRITE_CH_HOST": configured("WRITE_CH_HOST"),
        "PROPERTY_CATALOG_DEV_WRITE_CH_PORT": configured("WRITE_CH_PORT"),
        "PROPERTY_CATALOG_DEV_WRITE_CH_USER": configured("WRITE_CH_USER"),
        "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD": configured("WRITE_CH_PASSWORD"),
        "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE": config.target_database,
        "PROPERTY_CATALOG_DEV_CATALOG_EPOCH": configured("CATALOG_EPOCH"),
        "PROPERTY_CATALOG_DEV_PROJECTION_VERSION": configured("PROJECTION_VERSION"),
        "PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST": scope.project_ids,
        "PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID": configured("PRODUCER_STREAM_ID"),
        "PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY": configured("RUNTIME_DIRECTORY"),
        "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE": configured("REVISION_FENCE_FILE"),
        "PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE": configured("DRAIN_PROOF_FILE"),
        "PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE": configured(
            "PRODUCER_RETIREMENT_FILE"
        ),
        "PROPERTY_CATALOG_DEV_SPAN_SINCE": iso_z(span_since),
        "PROPERTY_CATALOG_DEV_SPAN_UNTIL": iso_z(span_until),
        "PROPERTY_CATALOG_DEV_SIDECAR_ACK": DEV_SIDECAR_ACK,
        "PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME": configured(
            "EXPECTED_WRITE_CH_HOSTNAME"
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAMES": configured(
            "EXPECTED_WRITE_CH_HOSTNAMES", ()
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME": configured(
            "EXPECTED_SOURCE_CH_HOSTNAME"
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAMES": configured(
            "EXPECTED_SOURCE_CH_HOSTNAMES", ()
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_DATABASE": configured("EXPECTED_PG_DATABASE"),
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_USER": configured("EXPECTED_PG_USER"),
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS": configured(
            "EXPECTED_PG_SERVER_ADDRESS"
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT": configured(
            "EXPECTED_PG_SERVER_PORT"
        ),
        "PROPERTY_CATALOG_DEV_MAX_WALL_MS": configured("MAX_WALL_MS"),
    }
    return SettingsOverlay(base=settings_object, overrides=overrides)


def rollout_request(
    *,
    scope: WorkspaceScope,
    proxy: SettingsOverlay,
    config: ControllerConfig,
    status: bool = False,
    scheduled_reconcile_wall_ms: int | None = None,
) -> ProductionRolloutRequest:
    return configured_production_rollout_request(
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        settings_object=proxy,
        execute=not status,
        status=status,
        scheduled_reconcile_wall_ms=scheduled_reconcile_wall_ms,
        repair_expired_incomplete=(
            config.repair_expired_incomplete if not status else False
        ),
    )


def _write_health(
    path: str,
    *,
    healthy: bool,
    observed_at: datetime,
    detail: Mapping[str, Any],
) -> None:
    raw = (
        canonical_json(
            {
                "detail": dict(detail),
                "format": _HEALTH_FORMAT,
                "healthy": healthy,
                "observed_at": iso_z(observed_at),
                "version": _HEALTH_VERSION,
            },
            max_bytes=256 * 1024,
        ).encode("utf-8")
        + b"\n"
    )
    target = Path(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".property-catalog-lifecycle-health-",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    keep = True
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ProductionLifecycleControllerError(
                    "production lifecycle health write was incomplete"
                )
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, target)
        keep = False
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if keep:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _install_signal_handlers(
    stop: threading.Event,
) -> dict[signal.Signals, Any]:
    previous: dict[signal.Signals, Any] = {}

    def handle(_signum: int, _frame: Any) -> None:
        stop.set()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, handle)
    return previous


def _restore_signal_handlers(previous: Mapping[signal.Signals, Any]) -> None:
    if threading.current_thread() is threading.main_thread():
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def utc_hour(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ProductionLifecycleControllerError("controller clock must be UTC-aware")
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _existing_directory(value: Any, name: str) -> Path:
    path = Path(str(value or ""))
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise ProductionLifecycleControllerError(
            f"{name} must be an existing absolute non-symlink directory"
        )
    return path.resolve(strict=True)


def _runtime_file(value: Any, parent: Path, name: str) -> str:
    path = Path(str(value or ""))
    if (
        not path.is_absolute()
        or path.parent.resolve(strict=True) != parent
        or path.is_symlink()
        or (path.exists() and not path.is_file())
    ):
        raise ProductionLifecycleControllerError(
            f"{name} must be a regular file inside the exact runtime directory"
        )
    return str(path)


def _positive_int_setting(source: Any, name: str) -> int:
    value = getattr(source, name, None)
    if type(value) is not int or value <= 0:
        raise ProductionLifecycleControllerError(f"{name} must be positive")
    return value


def _bounded_int_setting(source: Any, name: str, minimum: int, maximum: int) -> int:
    value = getattr(source, name, None)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProductionLifecycleControllerError(
            f"{name} must be in [{minimum}, {maximum}]"
        )
    return value


__all__ = [
    "Command",
    "ControllerConfig",
    "CycleResult",
    "ProductionLifecycleControllerError",
    "SettingsOverlay",
    "WorkspaceScope",
    "controller_config",
    "discover_workspace_scopes",
    "run_cycle",
    "run_workspace",
]
