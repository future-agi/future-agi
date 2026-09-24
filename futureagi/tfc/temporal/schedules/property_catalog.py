"""Default-off Temporal reconciliation for the isolated DEV property catalog."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string
from temporalio.client import ScheduleOverlapPolicy

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.property_catalog_queue import PROPERTY_CATALOG_TASK_QUEUE
from tfc.temporal.schedules.config import ScheduleConfig
from tracer.services.clickhouse.v2.property_catalog.dev_rollout import (
    DEV_CONTROL_PLANE_ENVIRONMENTS,
    DEV_SCHEDULED_RECONCILE_MAX_WALL_MS,
    DEV_STANDARD_MAX_WALL_MS,
    DevRolloutError,
    DevRolloutRequest,
    configured_dev_rollout_request,
    dev_control_plane_matches_request,
    is_dev_control_plane_cloud_allowed,
    run_workspace_reconcile,
)
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    CHECKED_IN_DEV_RUNTIME_FACTORY_PATH,
    PropertyCatalogDevRuntimeError,
    require_checked_in_property_catalog_dev_runtime,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import ReconcileMode

PROPERTY_CATALOG_RECONCILE_ACTIVITY = "reconcile_unified_property_catalog_dev"
PROPERTY_CATALOG_RECONCILE_SCHEDULE_ID = "unified-property-catalog-dev"
PROPERTY_CATALOG_RECONCILE_INTERVAL_SECONDS = (
    settings.PROPERTY_CATALOG_RECONCILE_INTERVAL_SECONDS
)
PROPERTY_CATALOG_RECONCILE_MAX_WORKSPACES = (
    settings.PROPERTY_CATALOG_RECONCILE_MAX_WORKSPACES
)
PROPERTY_CATALOG_RECONCILE_MAX_WALL_MS = DEV_STANDARD_MAX_WALL_MS
PROPERTY_CATALOG_RECONCILE_DEFAULT_EXTENDED_WALL_MS = (
    settings.PROPERTY_CATALOG_RECONCILE_DEFAULT_EXTENDED_WALL_MS
)
PROPERTY_CATALOG_RECONCILE_MAX_EXTENDED_WALL_MS = DEV_SCHEDULED_RECONCILE_MAX_WALL_MS
PROPERTY_CATALOG_RECONCILE_ACTIVITY_TIME_LIMIT_SECONDS = (
    settings.PROPERTY_CATALOG_RECONCILE_ACTIVITY_TIME_LIMIT_SECONDS
)


class PropertyCatalogScheduleError(RuntimeError):
    """Scheduled reconciliation cannot prove its DEV-only configuration."""


@dataclass(frozen=True, slots=True)
class PropertyCatalogScheduleConfiguration:
    enabled: bool
    requests: tuple[DevRolloutRequest, ...] = ()
    runtime_factory_path: str = ""


def property_catalog_schedule_configuration(
    settings_object: Any,
) -> PropertyCatalogScheduleConfiguration:
    """Validate every safety gate without importing a runtime or client."""

    enabled = getattr(
        settings_object,
        "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED",
        False,
    )
    if type(enabled) is not bool:
        raise PropertyCatalogScheduleError(
            "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED must be a bool"
        )
    if not enabled:
        return PropertyCatalogScheduleConfiguration(enabled=False)

    environment = str(getattr(settings_object, "ENV_TYPE", "")).strip().lower()
    cloud_deployment = str(getattr(settings_object, "CLOUD_DEPLOYMENT", "")).strip()
    if environment not in DEV_CONTROL_PLANE_ENVIRONMENTS:
        raise PropertyCatalogScheduleError(
            "property catalog schedule refuses non-DEV ENV_TYPE"
        )
    if not is_dev_control_plane_cloud_allowed(
        environment=environment,
        cloud_deployment=cloud_deployment,
    ):
        raise PropertyCatalogScheduleError(
            "property catalog schedule requires CLOUD_DEPLOYMENT=DEV, or an unset "
            "CLOUD_DEPLOYMENT only when ENV_TYPE=development"
        )
    max_wall_ms = getattr(
        settings_object,
        "PROPERTY_CATALOG_DEV_MAX_WALL_MS",
        PROPERTY_CATALOG_RECONCILE_MAX_WALL_MS,
    )
    if (
        type(max_wall_ms) is not int
        or not 1 <= max_wall_ms <= PROPERTY_CATALOG_RECONCILE_MAX_WALL_MS
    ):
        raise PropertyCatalogScheduleError(
            "property catalog schedule wall must be in "
            f"[1, {PROPERTY_CATALOG_RECONCILE_MAX_WALL_MS}] ms"
        )
    extended_wall_ms = getattr(
        settings_object,
        "PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS",
        PROPERTY_CATALOG_RECONCILE_DEFAULT_EXTENDED_WALL_MS,
    )
    if (
        type(extended_wall_ms) is not int
        or not PROPERTY_CATALOG_RECONCILE_MAX_WALL_MS
        < extended_wall_ms
        <= PROPERTY_CATALOG_RECONCILE_MAX_EXTENDED_WALL_MS
    ):
        raise PropertyCatalogScheduleError(
            "property catalog scheduled reconcile wall must be in "
            f"[{PROPERTY_CATALOG_RECONCILE_MAX_WALL_MS + 1}, "
            f"{PROPERTY_CATALOG_RECONCILE_MAX_EXTENDED_WALL_MS}] ms"
        )

    organization_id = str(
        getattr(settings_object, "PROPERTY_CATALOG_DEV_ORGANIZATION_ID", "")
    )
    raw_workspaces = getattr(
        settings_object,
        "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST",
        (),
    )
    if isinstance(raw_workspaces, str) or not isinstance(raw_workspaces, Sequence):
        raise PropertyCatalogScheduleError(
            "property catalog schedule requires an explicit workspace allowlist"
        )
    workspaces = tuple(str(value) for value in raw_workspaces)
    if len(workspaces) != PROPERTY_CATALOG_RECONCILE_MAX_WORKSPACES:
        workspace_label = (
            "workspace ID"
            if PROPERTY_CATALOG_RECONCILE_MAX_WORKSPACES == 1
            else "workspace IDs"
        )
        raise PropertyCatalogScheduleError(
            "property catalog sidecar workspace allowlist must contain exactly "
            f"{PROPERTY_CATALOG_RECONCILE_MAX_WORKSPACES} {workspace_label}"
        )
    if len(set(workspaces)) != len(workspaces):
        raise PropertyCatalogScheduleError(
            "property catalog schedule workspace allowlist contains duplicates"
        )

    try:
        requests = tuple(
            configured_dev_rollout_request(
                organization_id=organization_id,
                workspace_id=workspace_id,
                settings_object=settings_object,
                execute=True,
                scheduled_reconcile_wall_ms=extended_wall_ms,
            )
            for workspace_id in workspaces
        )
    except (DevRolloutError, TypeError, ValueError) as exc:
        raise PropertyCatalogScheduleError(str(exc)) from exc
    if any(
        not dev_control_plane_matches_request(
            environment=environment,
            cloud_deployment=cloud_deployment,
            request_environment=request.environment,
            request_cloud_deployment=request.cloud_deployment,
        )
        for request in requests
    ):
        raise PropertyCatalogScheduleError(
            "property catalog schedule ENV_TYPE/CLOUD_DEPLOYMENT differs from "
            "the validated request"
        )
    if any(request.initial_backfill_wall_ms is not None for request in requests):
        raise PropertyCatalogScheduleError(
            "property catalog schedule refuses an initial backfill wall"
        )

    runtime_factory_path = getattr(
        settings_object,
        "PROPERTY_CATALOG_DEV_RUNTIME_FACTORY",
        "",
    )
    if not isinstance(runtime_factory_path, str) or not runtime_factory_path.strip():
        raise PropertyCatalogScheduleError(
            "PROPERTY_CATALOG_DEV_RUNTIME_FACTORY must name the reviewed runtime"
        )
    if runtime_factory_path.strip() != CHECKED_IN_DEV_RUNTIME_FACTORY_PATH:
        raise PropertyCatalogScheduleError(
            "PROPERTY_CATALOG_DEV_RUNTIME_FACTORY must equal the reviewed "
            "checked-in runtime factory"
        )

    return PropertyCatalogScheduleConfiguration(
        enabled=True,
        requests=requests,
        runtime_factory_path=runtime_factory_path.strip(),
    )


def configured_property_catalog_schedules(
    settings_object: Any,
) -> tuple[ScheduleConfig, ...]:
    """Return exactly one serialized reconciliation schedule for this sidecar.

    Each Temporal activity owns exactly one workspace revision.  Schema creation
    and initial backfill remain explicit management-command operations and are
    never repeated by these schedules.  The durable runtime promotes the
    requested incremental run to a full repair when the persisted lineage
    anchor is due, so a second schedule cannot race and starve that repair.  The
    Python/Go sidecar shares one atomic fence file and is therefore admitted for
    exactly one workspace; additional workspaces require separate sidecars,
    queues, and shared volumes.
    """

    configuration = property_catalog_schedule_configuration(settings_object)
    if not configuration.enabled:
        return ()
    return tuple(
        ScheduleConfig(
            schedule_id=(
                f"{PROPERTY_CATALOG_RECONCILE_SCHEDULE_ID}-{request.workspace_id}"
            ),
            activity_name=PROPERTY_CATALOG_RECONCILE_ACTIVITY,
            interval_seconds=PROPERTY_CATALOG_RECONCILE_INTERVAL_SECONDS,
            catchup_window_seconds=PROPERTY_CATALOG_RECONCILE_INTERVAL_SECONDS,
            queue=PROPERTY_CATALOG_TASK_QUEUE,
            overlap_policy=ScheduleOverlapPolicy.SKIP,
            description=(
                "Reconcile one isolated DEV property catalog workspace, with "
                "automatic persisted-state full repair "
                f"({request.workspace_id})"
            ),
            activity_kwargs={
                "mode": ReconcileMode.INCREMENTAL.value,
                "workspace_id": request.workspace_id,
            },
        )
        for request in configuration.requests
    )


def run_property_catalog_dev_reconcile(
    *,
    settings_object: Any,
    workspace_id: str,
    mode: str,
    runtime_factory_loader: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Run one bounded revision after proving workspace and DEV admission."""

    configuration = property_catalog_schedule_configuration(settings_object)
    if not configuration.enabled:
        return {
            "mode": mode,
            "status": "disabled",
            "workspace_id": workspace_id,
        }

    try:
        reconcile_mode = ReconcileMode(mode)
    except ValueError as exc:
        raise PropertyCatalogScheduleError(
            "property catalog schedule mode is unsupported"
        ) from exc
    if reconcile_mode not in {
        ReconcileMode.INCREMENTAL,
        ReconcileMode.FULL_REPAIR,
    }:
        raise PropertyCatalogScheduleError(
            "property catalog schedule mode is unsupported"
        )

    requests_by_workspace = {
        request.workspace_id: request for request in configuration.requests
    }
    request = requests_by_workspace.get(workspace_id)
    if request is None:
        raise PropertyCatalogScheduleError(
            "property catalog schedule workspace is not allowlisted"
        )

    loader = runtime_factory_loader or import_string
    runtime_factory = loader(configuration.runtime_factory_path)
    if not callable(runtime_factory):
        raise PropertyCatalogScheduleError(
            "configured property catalog runtime factory is not callable"
        )

    try:
        runtime = require_checked_in_property_catalog_dev_runtime(
            runtime_factory(request)
        )
    except PropertyCatalogDevRuntimeError as exc:
        raise PropertyCatalogScheduleError(str(exc)) from exc
    result = run_workspace_reconcile(
        request=request,
        runtime=runtime,
        mode=reconcile_mode,
    )
    return {
        "evidence": dict(result),
        "mode": reconcile_mode.value,
        "status": "completed",
        "workspace_id": request.workspace_id,
    }


@temporal_activity(
    name=PROPERTY_CATALOG_RECONCILE_ACTIVITY,
    time_limit=PROPERTY_CATALOG_RECONCILE_ACTIVITY_TIME_LIMIT_SECONDS,
    queue=PROPERTY_CATALOG_TASK_QUEUE,
    max_retries=0,
)
def reconcile_unified_property_catalog_dev(
    *,
    workspace_id: str,
    mode: str,
) -> dict[str, Any]:
    """Temporal activity wrapper around the safe in-process service entrypoint."""

    return run_property_catalog_dev_reconcile(
        settings_object=settings,
        workspace_id=workspace_id,
        mode=mode,
    )


PROPERTY_CATALOG_SCHEDULES = list(configured_property_catalog_schedules(settings))


__all__ = [
    "PROPERTY_CATALOG_RECONCILE_ACTIVITY",
    "PROPERTY_CATALOG_RECONCILE_ACTIVITY_TIME_LIMIT_SECONDS",
    "PROPERTY_CATALOG_RECONCILE_DEFAULT_EXTENDED_WALL_MS",
    "PROPERTY_CATALOG_RECONCILE_INTERVAL_SECONDS",
    "PROPERTY_CATALOG_RECONCILE_MAX_EXTENDED_WALL_MS",
    "PROPERTY_CATALOG_RECONCILE_MAX_WORKSPACES",
    "PROPERTY_CATALOG_RECONCILE_MAX_WALL_MS",
    "PROPERTY_CATALOG_RECONCILE_SCHEDULE_ID",
    "PROPERTY_CATALOG_TASK_QUEUE",
    "PROPERTY_CATALOG_SCHEDULES",
    "PropertyCatalogScheduleConfiguration",
    "PropertyCatalogScheduleError",
    "configured_property_catalog_schedules",
    "property_catalog_schedule_configuration",
    "reconcile_unified_property_catalog_dev",
    "run_property_catalog_dev_reconcile",
]
