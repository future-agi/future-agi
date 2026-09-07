"""Bounded OSS-local reconciler for the unified property catalog.

The command discovers tenant scope through a read-only PostgreSQL identity,
then delegates every catalog lifecycle transition to the checked-in DEV
runtime. Ordinary cycles initialize eligible inactive workspaces and then
incrementally reconcile active catalogs. Bootstrap attempts use the runtime's
bounded, resumable lifecycle. The explicit ``--once --initial-backfill`` mode
is bootstrap-only and skips already-active catalogs. An exact isolated catalog
schema must be prepared before either mode starts.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
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
from tracer.services.clickhouse.v2.property_catalog.controller_health import (
    ControllerHealth,
    write_health_record,
)
from tracer.services.clickhouse.v2.property_catalog.dev_rollout import (
    DEV_ENVIRONMENT,
    DEV_ROLLOUT_ACK,
    DevRolloutError,
    DevRolloutRequest,
    configured_dev_rollout_request,
    run_configured_dev_rollout,
    run_workspace_reconcile,
)
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    DEV_SIDECAR_ACK,
    DevProvenanceExpectation,
    DevProvenanceObservation,
    DevRuntimeConfig,
    PostgresDevIdentity,
    PropertyCatalogDevRuntimeFactory,
    require_checked_in_property_catalog_dev_runtime,
)
from tracer.services.clickhouse.v2.property_catalog.installation_bootstrap import (
    require_legacy_identity,
    resolve_installation,
)
from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
    IDENTITY_FILENAME,
    load_identity,
)
from tracer.services.clickhouse.v2.property_catalog.oss_write_startup import (
    prepare_oss_write_admission,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    PropertyCatalogPublishError,
    require_dev_catalog_database,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import ReconcileMode
from tracer.services.clickhouse.v2.property_catalog.revision_fence_registry import (
    AtomicMultiTenantFenceFile,
)

logger = logging.getLogger(__name__)

OSS_SUPERVISOR_ACK_ENV = "PROPERTY_CATALOG_OSS_SUPERVISOR_ACK"
OSS_SUPERVISOR_ACK = "PROPERTY_CATALOG_OSS_SUPERVISOR_V1"
OSS_SUPERVISOR_POLL_SECONDS_ENV = "PROPERTY_CATALOG_OSS_SUPERVISOR_POLL_SECONDS"
OSS_SUPERVISOR_WORKSPACE_BATCH_SIZE_ENV = (
    "PROPERTY_CATALOG_OSS_SUPERVISOR_WORKSPACE_BATCH_SIZE"
)
OSS_SUPERVISOR_PROJECT_BATCH_SIZE_ENV = (
    "PROPERTY_CATALOG_OSS_SUPERVISOR_PROJECT_BATCH_SIZE"
)
OSS_CATALOG_EPOCH_ENV = "PROPERTY_CATALOG_DEV_CATALOG_EPOCH"
OSS_PROJECTION_VERSION_ENV = "PROPERTY_CATALOG_DEV_PROJECTION_VERSION"
OSS_PRODUCER_STREAM_ID_ENV = "PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID"

_DEFAULT_POLL_SECONDS = 60
_MIN_POLL_SECONDS = 5
_MAX_POLL_SECONDS = 3_600
_DEFAULT_WORKSPACE_BATCH_SIZE = 512
_DEFAULT_PROJECT_BATCH_SIZE = 512
_MAX_QUERY_BATCH_SIZE = 16_384
_SPAN_WINDOW_DAYS = 366
_OSS_DEV_IDENTITY = "dev:oss-property-catalog-supervisor"
_PROBE_ORGANIZATION_ID = "00000000-0000-4000-8000-000000000001"
_PROBE_WORKSPACE_ID = "00000000-0000-4000-8000-000000000002"
_PROBE_PROJECT_ID = "00000000-0000-4000-8000-000000000003"


class OssPropertyCatalogSupervisorError(RuntimeError):
    """The OSS supervisor cannot prove or execute its bounded local scope."""


@dataclass(frozen=True, slots=True)
class OssSupervisorConfig:
    source_database: str
    target_database: str
    catalog_epoch: int
    projection_version: int
    producer_stream_id: str
    revision_fence_file: str
    poll_seconds: int
    workspace_batch_size: int
    project_batch_size: int
    scheduled_reconcile_wall_ms: int
    managed_identity: bool = False


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
        if not isinstance(self.project_ids, tuple) or not isinstance(
            self.legacy_project_ids, tuple
        ):
            raise OssPropertyCatalogSupervisorError(
                "workspace scope requires explicit project tuples"
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
        if len(projects) > 256 or len(set(projects)) != len(projects):
            raise OssPropertyCatalogSupervisorError(
                "workspace scope requires 0..256 unique projects"
            )
        if len(set(legacy)) != len(legacy) or not set(legacy).issubset(projects):
            raise OssPropertyCatalogSupervisorError(
                "legacy project scope must be a unique subset of workspace projects"
            )
        if legacy and not self.is_default:
            raise OssPropertyCatalogSupervisorError(
                "workspace-null legacy projects require the organization's default workspace"
            )
        object.__setattr__(self, "project_ids", projects)
        object.__setattr__(self, "legacy_project_ids", legacy)


@dataclass(frozen=True, slots=True)
class _SettingsProxy:
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
class _CycleResult:
    processed: tuple[str, ...]
    skipped: tuple[str, ...]
    failures: Mapping[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "failed": dict(sorted(self.failures.items())),
            "processed": list(self.processed),
            "skipped": list(self.skipped),
        }


class Command(BaseCommand):
    help = (
        "Initialize and incrementally reconcile isolated OSS workspace catalogs, "
        "or run one explicit bootstrap-only cycle."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--health-file",
            help="Publish live process and dependency health to an existing writable directory.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one bounded discovery/bootstrap/reconcile cycle and exit.",
        )
        parser.add_argument(
            "--initial-backfill",
            action="store_true",
            help=(
                "Only initialize inactive workspace catalogs; skip active builds. "
                "This historical write requires --once."
            ),
        )
        parser.add_argument(
            "--initial-backfill-wall-ms",
            type=int,
            help=(
                "Per-workspace wall allowance for explicit initial backfill. "
                "The reviewed runtime bounds and validates this value."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> str | None:
        once = bool(options.get("once"))
        initial_backfill = bool(options.get("initial_backfill"))
        initial_backfill_wall_ms = options.get("initial_backfill_wall_ms")
        if initial_backfill and not once:
            raise CommandError("--initial-backfill requires --once")
        if initial_backfill_wall_ms is not None and not initial_backfill:
            raise CommandError("--initial-backfill-wall-ms requires --initial-backfill")
        try:
            config = _supervisor_config(settings_object=settings, environ=os.environ)
        except (OssPropertyCatalogSupervisorError, DevRolloutError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        health_file = options.get("health_file")
        if health_file:
            path = Path(health_file)
            if not path.is_absolute() or not path.parent.is_dir() or path.is_symlink():
                raise CommandError(
                    "health file requires an absolute writable directory"
                )
        health = ControllerHealth(
            lambda **snapshot: (
                write_health_record(health_file, **snapshot) if health_file else None
            )
        )
        with ExitStack() as stack:
            if health_file:
                stack.enter_context(health)
            config = _resolve_supervisor_installation(
                settings_object=settings, config=config
            )
            if config.managed_identity:
                health.progress("admitting_writes", timeout_seconds=60, ready=False)
                try:
                    prepare_oss_write_admission(settings, environ=os.environ)
                except Exception as exc:
                    raise CommandError(
                        "OSS write admission failed before lifecycle work"
                    ) from exc
            return self._serve(
                config=config,
                health=health,
                once=once,
                initial_backfill=initial_backfill,
                initial_backfill_wall_ms=initial_backfill_wall_ms,
            )

    def _serve(
        self,
        *,
        config: OssSupervisorConfig,
        health: ControllerHealth,
        once: bool,
        initial_backfill: bool,
        initial_backfill_wall_ms: int | None,
    ) -> str | None:
        while True:
            try:
                health.progress("discovering", timeout_seconds=120, ready=False)
                cycle_now = _utc_now()
                observation = _probe_remote_identities(
                    settings_object=settings,
                    config=config,
                    now=cycle_now,
                )
                scopes, skipped = _discover_workspace_scopes(
                    workspace_batch_size=config.workspace_batch_size,
                    project_batch_size=config.project_batch_size,
                )
                result = _run_cycle(
                    scopes=scopes,
                    skipped=skipped,
                    settings_object=settings,
                    config=config,
                    observation=observation,
                    now=cycle_now,
                    bootstrap_only=initial_backfill,
                    initial_backfill_wall_ms=initial_backfill_wall_ms,
                    clock=_utc_now,
                    on_workspace=lambda workspace_id: health.progress(
                        "reconciling",
                        ready=True,
                        timeout_seconds=max(
                            config.scheduled_reconcile_wall_ms,
                            initial_backfill_wall_ms or 0,
                        )
                        / 1000
                        + 120,
                        detail={"workspace_id": workspace_id},
                    ),
                    on_error=lambda workspace_id, exc: self.stderr.write(
                        self.style.ERROR(
                            f"workspace {workspace_id} failed safely: {exc}"
                        )
                    ),
                )
            except Exception as exc:
                if once:
                    raise CommandError(str(exc)) from exc
                logger.exception("OSS property-catalog supervisor cycle failed safely")
                self.stderr.write(
                    self.style.ERROR(
                        "OSS property-catalog discovery cycle failed safely; retrying"
                    )
                )
                health.progress(
                    "retrying",
                    timeout_seconds=config.poll_seconds + 120,
                    ready=False,
                    detail={"cycle_error": type(exc).__name__},
                )
            else:
                health.progress(
                    "idle",
                    timeout_seconds=config.poll_seconds + 120,
                    ready=True,
                    detail={
                        "failed_count": len(result.failures),
                        "processed_count": len(result.processed),
                        "skipped_count": len(result.skipped),
                    },
                )
                if once:
                    if result.failures:
                        failed = ", ".join(sorted(result.failures))
                        raise CommandError(
                            f"OSS property-catalog cycle failed for workspaces: {failed}"
                        )
                    return canonical_json(result.as_dict(), max_bytes=256 * 1024)
            health.publish()
            time.sleep(config.poll_seconds)


def _supervisor_config(
    *,
    settings_object: Any,
    environ: Mapping[str, str],
) -> OssSupervisorConfig:
    environment = str(getattr(settings_object, "ENV_TYPE", "")).strip().lower()
    cloud_deployment = str(getattr(settings_object, "CLOUD_DEPLOYMENT", "")).strip()
    if environment != DEV_ENVIRONMENT:
        raise OssPropertyCatalogSupervisorError(
            "OSS property-catalog supervisor requires ENV_TYPE=development"
        )
    if cloud_deployment:
        raise OssPropertyCatalogSupervisorError(
            "OSS property-catalog supervisor requires CLOUD_DEPLOYMENT to be unset"
        )
    if str(environ.get(OSS_SUPERVISOR_ACK_ENV, "")).strip() != OSS_SUPERVISOR_ACK:
        raise OssPropertyCatalogSupervisorError(
            f"{OSS_SUPERVISOR_ACK_ENV} must equal the exact OSS supervisor acknowledgement"
        )

    source_database = str(
        getattr(settings_object, "PROPERTY_CATALOG_DEV_SOURCE_DATABASE", "")
    ).strip()
    target_database = str(
        getattr(settings_object, "PROPERTY_CATALOG_DEV_TARGET_DATABASE", "")
    ).strip()
    try:
        require_dev_catalog_database(target_database)
    except PropertyCatalogPublishError as exc:
        raise OssPropertyCatalogSupervisorError(
            "OSS target must be a safe isolated development catalog database"
        ) from exc
    if not source_database or source_database == target_database:
        raise OssPropertyCatalogSupervisorError(
            "canonical source and isolated catalog target databases must differ"
        )
    write_database = str(
        getattr(settings_object, "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE", "")
    ).strip()
    if write_database != target_database:
        raise OssPropertyCatalogSupervisorError(
            "catalog writer database must equal the exact isolated target database"
        )

    # No version variables is the normal managed installation. Partial legacy
    # configuration is still an error, not a request to invent missing values.
    legacy = (
        OSS_CATALOG_EPOCH_ENV,
        OSS_PROJECTION_VERSION_ENV,
        OSS_PRODUCER_STREAM_ID_ENV,
    )
    epoch, projection, producer_stream_id = 0, 0, ""
    if any(name in environ for name in legacy):
        epoch = _explicit_positive_uint16(environ, OSS_CATALOG_EPOCH_ENV)
        projection = _explicit_positive_uint16(environ, OSS_PROJECTION_VERSION_ENV)
        try:
            producer_stream_id = canonical_uuid(
                _required_environment(environ, OSS_PRODUCER_STREAM_ID_ENV),
                field="producer_stream_id",
            )
        except ValueError as exc:
            raise OssPropertyCatalogSupervisorError(str(exc)) from exc
    revision_fence_file = str(
        getattr(settings_object, "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE", "")
    ).strip()
    if not revision_fence_file:
        raise OssPropertyCatalogSupervisorError(
            "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE must be set explicitly"
        )
    poll_seconds = _bounded_environment_int(
        environ,
        OSS_SUPERVISOR_POLL_SECONDS_ENV,
        default=_DEFAULT_POLL_SECONDS,
        minimum=_MIN_POLL_SECONDS,
        maximum=_MAX_POLL_SECONDS,
    )
    workspace_batch_size = _bounded_environment_int(
        environ,
        OSS_SUPERVISOR_WORKSPACE_BATCH_SIZE_ENV,
        default=_DEFAULT_WORKSPACE_BATCH_SIZE,
        minimum=1,
        maximum=_MAX_QUERY_BATCH_SIZE,
    )
    project_batch_size = _bounded_environment_int(
        environ,
        OSS_SUPERVISOR_PROJECT_BATCH_SIZE_ENV,
        default=_DEFAULT_PROJECT_BATCH_SIZE,
        minimum=1,
        maximum=_MAX_QUERY_BATCH_SIZE,
    )
    scheduled_wall_ms = getattr(
        settings_object,
        "PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS",
        None,
    )
    if type(scheduled_wall_ms) is not int or scheduled_wall_ms <= 0:
        raise OssPropertyCatalogSupervisorError(
            "PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS must be positive"
        )
    return OssSupervisorConfig(
        source_database=source_database,
        target_database=target_database,
        catalog_epoch=epoch,
        projection_version=projection,
        producer_stream_id=producer_stream_id,
        revision_fence_file=revision_fence_file,
        poll_seconds=poll_seconds,
        workspace_batch_size=workspace_batch_size,
        project_batch_size=project_batch_size,
        scheduled_reconcile_wall_ms=scheduled_wall_ms,
        managed_identity=not any(name in environ for name in legacy),
    )


def _resolve_supervisor_installation(
    *,
    settings_object: Any,
    config: OssSupervisorConfig,
) -> OssSupervisorConfig:
    candidate_topic = getattr(
        settings_object,
        "PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC",
        "futureagi.oss.property-catalog.candidates.v1",
    )
    ordered_topic = getattr(
        settings_object,
        "PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC",
        "futureagi.oss.property-catalog.ordered.v1",
    )
    path = Path(config.revision_fence_file).parent / IDENTITY_FILENAME
    if config.producer_stream_id:
        if path.exists() or path.is_symlink():
            persisted = load_identity(path)
            persisted.require_destination(
                environment="development",
                target_database=config.target_database,
                candidate_topic=candidate_topic,
                ordered_topic=ordered_topic,
            )
            require_legacy_identity(
                persisted,
                epoch=config.catalog_epoch,
                projection=config.projection_version,
                producer=config.producer_stream_id,
            )
        return config
    identity = resolve_installation(
        settings_object=settings_object,
        prefix="PROPERTY_CATALOG_DEV_",
        environment="development",
        target_database=config.target_database,
        revision_fence_file=config.revision_fence_file,
        candidate_topic=candidate_topic,
        ordered_topic=ordered_topic,
    )
    return replace(
        config,
        catalog_epoch=identity.catalog_epoch,
        projection_version=identity.projection_version,
        producer_stream_id=identity.producer_stream_id,
    )


def _discover_workspace_scopes(
    *,
    workspace_batch_size: int,
    project_batch_size: int,
) -> tuple[tuple[WorkspaceScope, ...], tuple[str, ...]]:
    workspace_rows = (
        Workspace.no_workspace_objects.filter(is_active=True)
        .order_by("organization_id", "id")
        .values_list("id", "organization_id", "is_default")
        .iterator(chunk_size=workspace_batch_size)
    )

    scopes: list[WorkspaceScope] = []
    skipped: list[str] = []
    for workspace_id_raw, organization_id_raw, is_default_raw in workspace_rows:
        workspace_id = canonical_uuid(workspace_id_raw, field="workspace_id")
        organization_id = canonical_uuid(
            organization_id_raw,
            field="organization_id",
        )
        is_default = bool(is_default_raw)
        project_filter = Q(
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        if is_default:
            project_filter |= Q(
                organization_id=organization_id,
                workspace_id__isnull=True,
            )
        project_rows = list(
            Project.no_workspace_objects.filter(project_filter, trace_type="observe")
            .order_by("id")
            .values_list("id", "workspace_id")
            .iterator(chunk_size=project_batch_size)
        )
        projects = tuple(str(project_id) for project_id, _ in project_rows)
        legacy = tuple(
            str(project_id)
            for project_id, bound_workspace_id in project_rows
            if bound_workspace_id is None
        )
        scopes.append(
            WorkspaceScope(
                organization_id=organization_id,
                workspace_id=workspace_id,
                is_default=is_default,
                project_ids=projects,
                legacy_project_ids=legacy,
            )
        )
    return tuple(scopes), tuple(skipped)


def _run_cycle(
    *,
    scopes: tuple[WorkspaceScope, ...],
    skipped: tuple[str, ...],
    settings_object: Any,
    config: OssSupervisorConfig,
    observation: DevProvenanceObservation,
    now: datetime,
    on_error: Callable[[str, Exception], None],
    bootstrap_only: bool = False,
    initial_backfill_wall_ms: int | None = None,
    clock: Callable[[], datetime] | None = None,
    on_workspace: Callable[[str], None] = lambda _: None,
) -> _CycleResult:
    authorized_workspaces = tuple(
        sorted((*skipped, *(scope.workspace_id for scope in scopes)))
    )
    AtomicMultiTenantFenceFile(
        config.revision_fence_file,
        now=lambda: now,
    ).reconcile_authorized_workspaces(authorized_workspaces)
    processed: list[str] = []
    skipped_workspaces = list(skipped)
    failures: dict[str, str] = {}
    for scope in scopes:
        on_workspace(scope.workspace_id)
        try:
            workspace_processed = _run_workspace(
                scope=scope,
                settings_object=settings_object,
                config=config,
                observation=observation,
                now=clock() if clock is not None else now,
                bootstrap_only=bootstrap_only,
                initial_backfill_wall_ms=initial_backfill_wall_ms,
            )
        except Exception as exc:
            failures[scope.workspace_id] = str(exc)
            on_error(scope.workspace_id, exc)
            continue
        if workspace_processed:
            processed.append(scope.workspace_id)
        else:
            skipped_workspaces.append(scope.workspace_id)
    return _CycleResult(
        processed=tuple(processed),
        skipped=tuple(sorted(set(skipped_workspaces))),
        failures=MappingProxyType(failures),
    )


def _run_workspace(
    *,
    scope: WorkspaceScope,
    settings_object: Any,
    config: OssSupervisorConfig,
    observation: DevProvenanceObservation,
    now: datetime,
    bootstrap_only: bool = False,
    initial_backfill_wall_ms: int | None = None,
) -> bool:
    proxy = _workspace_settings_proxy(
        settings_object=settings_object,
        config=config,
        scope=scope,
        observation=observation,
        now=now,
    )
    status_request = _rollout_request(scope=scope, proxy=proxy, status=True)
    with _managed_runtime(
        request=status_request,
        proxy=proxy,
        scope=scope,
    ) as status_runtime:
        status_result = run_configured_dev_rollout(
            request=status_request,
            runtime=status_runtime,
        )
    status_evidence = dict(status_result.evidence[0].evidence)
    if status_evidence.get("schema_ready") is not True:
        raise OssPropertyCatalogSupervisorError(
            "isolated catalog schema is not prepared; supervisor will not create it"
        )

    active = status_evidence.get("active")
    if type(active) is not bool:
        raise OssPropertyCatalogSupervisorError(
            "catalog status must explicitly prove whether the workspace is active"
        )
    if active:
        if bootstrap_only:
            return False
        request = _rollout_request(
            scope=scope,
            proxy=proxy,
            scheduled_reconcile_wall_ms=config.scheduled_reconcile_wall_ms,
        )
        with _managed_runtime(request=request, proxy=proxy, scope=scope) as runtime:
            run_workspace_reconcile(
                request=request,
                runtime=runtime,
                mode=ReconcileMode.INCREMENTAL,
            )
        return True

    # Each cycle re-enters the checked-in lifecycle: persisted reservations,
    # cutoffs, and checkpoints govern retries. Keep its standard wall/query/lease
    # bounds unless the operator supplies an explicit bootstrap-only allowance.
    # Expired incomplete builds use the same journaled automatic recovery guard.
    request = _rollout_request(
        scope=scope,
        proxy=proxy,
        initial_backfill_wall_ms=initial_backfill_wall_ms,
    )
    with _managed_runtime(request=request, proxy=proxy, scope=scope) as runtime:
        run_configured_dev_rollout(request=request, runtime=runtime)
    return True


@contextmanager
def _managed_runtime(
    *,
    request: DevRolloutRequest,
    proxy: _SettingsProxy,
    scope: WorkspaceScope,
) -> Iterator[Any]:
    runtime = _runtime(request=request, proxy=proxy, scope=scope)
    try:
        yield runtime
    finally:
        close = getattr(runtime, "close", None)
        if callable(close):
            close()


def _runtime(
    *,
    request: DevRolloutRequest,
    proxy: _SettingsProxy,
    scope: WorkspaceScope,
) -> Any:
    runtime = PropertyCatalogDevRuntimeFactory(
        settings_object=proxy,
        fence_sink_factory=AtomicMultiTenantFenceFile,
        project_tenant_binding_probe=_legacy_aware_project_probe(scope),
    )(request)
    return require_checked_in_property_catalog_dev_runtime(runtime)


def _legacy_aware_project_probe(
    scope: WorkspaceScope,
) -> Callable[
    [tuple[str, ...], PostgresDevIdentity],
    dev_runtime.PostgresWorkspaceProjectInventory,
]:
    """Re-prove default workspace semantics and exact inventory in PostgreSQL."""

    def probe(
        project_ids: tuple[str, ...],
        expected_postgres_identity: PostgresDevIdentity,
    ) -> dev_runtime.PostgresWorkspaceProjectInventory:
        return dev_runtime._postgres_workspace_project_inventory(  # noqa: SLF001
            project_ids,
            expected_postgres_identity,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
        )

    return probe


def _probe_remote_identities(
    *,
    settings_object: Any,
    config: OssSupervisorConfig,
    now: datetime,
) -> DevProvenanceObservation:
    """Read and validate remote identities before any ORM source discovery.

    The checked-in runtime has no public pre-construction hostname/IP probe.
    These private calls are therefore deliberately limited to its existing
    read-only provenance path; every workspace factory repeats the full proof.
    """

    probe_scope = WorkspaceScope(
        organization_id=_PROBE_ORGANIZATION_ID,
        workspace_id=_PROBE_WORKSPACE_ID,
        is_default=False,
        project_ids=(_PROBE_PROJECT_ID,),
    )
    placeholder = DevProvenanceExpectation(
        writer_clickhouse_hostname="pending-writer-host",
        source_clickhouse_hostname="pending-source-host",
        postgres_database="pending_postgres_database",
        postgres_user="pending_postgres_user",
        postgres_server_address="127.0.0.1",
        postgres_server_port=5432,
    )
    proxy = _workspace_settings_proxy(
        settings_object=settings_object,
        config=config,
        scope=probe_scope,
        observation=None,
        expectation=placeholder,
        now=now,
    )
    request = _rollout_request(scope=probe_scope, proxy=proxy, status=True)
    runtime_config = DevRuntimeConfig.from_settings(request, proxy, now=now)
    writer = dev_runtime._default_native_client(  # noqa: SLF001
        replace(
            runtime_config.catalog,
            database=runtime_config.catalog_control_database,
        )
    )
    source = dev_runtime._default_native_client(runtime_config.source)  # noqa: SLF001
    try:
        observation = dev_runtime._default_dev_provenance_probe(  # noqa: SLF001
            runtime_config,
            writer,
            source,
        )
    finally:
        for client in (writer, source):
            close = getattr(client, "close", None)
            if callable(close):
                close()
    expectation = _provenance_expectation(observation)
    validated_config = replace(
        runtime_config,
        provenance_expectation=expectation,
    )
    dev_runtime._validate_dev_provenance(  # noqa: SLF001
        config=validated_config,
        observation=observation,
        attested_at=now,
    )
    return observation


def _workspace_settings_proxy(
    *,
    settings_object: Any,
    config: OssSupervisorConfig,
    scope: WorkspaceScope,
    now: datetime,
    observation: DevProvenanceObservation | None,
    expectation: DevProvenanceExpectation | None = None,
) -> _SettingsProxy:
    span_until = _utc_hour(now)
    span_since = span_until - timedelta(days=_SPAN_WINDOW_DAYS)
    effective_expectation = expectation or _provenance_expectation(observation)
    overrides = {
        "_PROPERTY_CATALOG_MANAGED_INSTALLATION": config.managed_identity,
        "ENV_TYPE": DEV_ENVIRONMENT,
        "CLOUD_DEPLOYMENT": "",
        "PROPERTY_CATALOG_DEV_ENVIRONMENT": DEV_ENVIRONMENT,
        "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT": "",
        "PROPERTY_CATALOG_DEV_IDENTITY": _OSS_DEV_IDENTITY,
        "PROPERTY_CATALOG_DEV_SOURCE_DATABASE": config.source_database,
        "PROPERTY_CATALOG_DEV_TARGET_DATABASE": config.target_database,
        "PROPERTY_CATALOG_DEV_ACKNOWLEDGEMENT": DEV_ROLLOUT_ACK,
        "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE": config.target_database,
        "PROPERTY_CATALOG_DEV_CATALOG_EPOCH": config.catalog_epoch,
        "PROPERTY_CATALOG_DEV_PROJECTION_VERSION": config.projection_version,
        "PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST": scope.project_ids,
        "PROPERTY_CATALOG_DEV_SPAN_SINCE": _iso_z(span_since),
        "PROPERTY_CATALOG_DEV_SPAN_UNTIL": _iso_z(span_until),
        "PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID": config.producer_stream_id,
        "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE": config.revision_fence_file,
        "PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME": (
            effective_expectation.writer_clickhouse_hostname
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME": (
            effective_expectation.source_clickhouse_hostname
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_DATABASE": (
            effective_expectation.postgres_database
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_USER": effective_expectation.postgres_user,
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS": (
            effective_expectation.postgres_server_address
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT": (
            effective_expectation.postgres_server_port
        ),
        "PROPERTY_CATALOG_DEV_SIDECAR_ACK": DEV_SIDECAR_ACK,
    }
    return _SettingsProxy(base=settings_object, overrides=overrides)


def _rollout_request(
    *,
    scope: WorkspaceScope,
    proxy: _SettingsProxy,
    status: bool = False,
    initial_backfill_wall_ms: int | None = None,
    scheduled_reconcile_wall_ms: int | None = None,
) -> DevRolloutRequest:
    return configured_dev_rollout_request(
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        settings_object=proxy,
        execute=not status,
        status=status,
        initial_backfill_wall_ms=initial_backfill_wall_ms,
        scheduled_reconcile_wall_ms=scheduled_reconcile_wall_ms,
    )


def _provenance_expectation(
    observation: DevProvenanceObservation | None,
) -> DevProvenanceExpectation:
    if observation is None:
        raise OssPropertyCatalogSupervisorError(
            "remote provenance observation is required"
        )
    return DevProvenanceExpectation(
        writer_clickhouse_hostname=observation.writer_clickhouse.hostname,
        source_clickhouse_hostname=observation.source_clickhouse.hostname,
        postgres_database=observation.postgres.database,
        postgres_user=observation.postgres.user,
        postgres_server_address=observation.postgres.server_address,
        postgres_server_port=observation.postgres.server_port,
    )


def _utc_hour(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise OssPropertyCatalogSupervisorError("supervisor clock must be UTC-aware")
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_z(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _required_environment(environ: Mapping[str, str], name: str) -> str:
    value = str(environ.get(name, "")).strip()
    if not value:
        raise OssPropertyCatalogSupervisorError(f"{name} must be set explicitly")
    return value


def _explicit_positive_uint16(environ: Mapping[str, str], name: str) -> int:
    raw = _required_environment(environ, name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise OssPropertyCatalogSupervisorError(
            f"{name} must be a positive UInt16"
        ) from exc
    if not 1 <= value < (1 << 16):
        raise OssPropertyCatalogSupervisorError(f"{name} must be a positive UInt16")
    return value


def _bounded_environment_int(
    environ: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = str(environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise OssPropertyCatalogSupervisorError(
            f"{name} must be an integer in [{minimum}, {maximum}]"
        ) from exc
    if not minimum <= value <= maximum:
        raise OssPropertyCatalogSupervisorError(
            f"{name} must be in [{minimum}, {maximum}]"
        )
    return value


__all__ = [
    "Command",
    "OSS_SUPERVISOR_ACK",
    "OSS_SUPERVISOR_ACK_ENV",
    "OssPropertyCatalogSupervisorError",
]
