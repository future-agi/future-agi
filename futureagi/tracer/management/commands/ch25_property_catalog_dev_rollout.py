"""Run or inspect the isolated unified property catalog in DEV only."""

from __future__ import annotations

import os
from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from tracer.services.clickhouse.v2.property_catalog.codec import canonical_json
from tracer.services.clickhouse.v2.property_catalog.dev_rollout import (
    ConfiguredDevRolloutRuntime,
    DevRolloutError,
    DevRolloutRequest,
    configured_dev_rollout_request,
    run_configured_dev_rollout,
    run_workspace_reconcile,
)
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    CHECKED_IN_DEV_RUNTIME_FACTORY_PATH,
    require_checked_in_property_catalog_dev_runtime,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import ReconcileMode

_RUNTIME_FACTORY_SETTING = "PROPERTY_CATALOG_DEV_RUNTIME_FACTORY"
_RUNTIME_UID_SETTING = "PROPERTY_CATALOG_RUNTIME_UID"
_DEFAULT_RUNTIME_UID = 65_532


class Command(BaseCommand):
    help = (
        "Plan, inspect, or execute the clean six-table unified property catalog "
        "inside one exact isolated DEV database."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--organization-id")
        parser.add_argument("--workspace-id")
        parser.add_argument("--environment")
        parser.add_argument("--cloud-deployment")
        parser.add_argument("--dev-identity")
        parser.add_argument("--source-database")
        parser.add_argument("--target-database")
        parser.add_argument("--ack", dest="acknowledgement")
        parser.add_argument("--initial-backfill-wall-ms", type=int)
        parser.add_argument("--scheduled-reconcile-wall-ms", type=int)
        parser.add_argument(
            "--repair-expired-incomplete",
            action="store_true",
            help=(
                "Explicitly supersede one expired incomplete revision with a "
                "fresh revision; never extends or mutates the expired lease."
            ),
        )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--status", action="store_true")
        mode.add_argument("--execute", action="store_true")
        mode.add_argument(
            "--scheduled-reconcile",
            choices=(
                ReconcileMode.INCREMENTAL.value,
                ReconcileMode.FULL_REPAIR.value,
            ),
            help=(
                "Run only one bounded scheduled revision; schema DDL and initial "
                "backfill remain disabled."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> str:
        try:
            _require_mutating_runtime_identity(options)
            request = _request(options)
            runtime = _runtime(request) if request.execute or request.status else None
            scheduled_mode = options.get("scheduled_reconcile")
            if scheduled_mode:
                assert runtime is not None
                result: Any = run_workspace_reconcile(
                    request=request,
                    runtime=runtime,
                    mode=ReconcileMode(str(scheduled_mode)),
                )
            else:
                result = run_configured_dev_rollout(request=request, runtime=runtime)
        except (DevRolloutError, TypeError, ValueError, ImportError) as exc:
            raise CommandError(str(exc)) from exc
        payload = result if isinstance(result, dict) else result.as_dict()
        output = canonical_json(payload, max_bytes=4 * 1024 * 1024)
        return output


def _require_mutating_runtime_identity(options: dict[str, Any]) -> None:
    """Keep every spool mutation under the producer/control runtime identity."""

    if not (options.get("execute") or options.get("scheduled_reconcile")):
        return
    expected_uid = getattr(settings, _RUNTIME_UID_SETTING, _DEFAULT_RUNTIME_UID)
    if isinstance(expected_uid, bool) or not isinstance(expected_uid, int):
        raise DevRolloutError(f"{_RUNTIME_UID_SETTING} must be a positive integer")
    if expected_uid <= 0:
        raise DevRolloutError(f"{_RUNTIME_UID_SETTING} must be a positive integer")
    actual_uid = os.geteuid()
    if actual_uid != expected_uid:
        raise DevRolloutError(
            "property-catalog mutations require runtime uid "
            f"{expected_uid}; refusing uid {actual_uid} before runtime I/O"
        )


def _request(options: dict[str, Any]) -> DevRolloutRequest:
    organization_id = options.get("organization_id") or getattr(
        settings, "PROPERTY_CATALOG_DEV_ORGANIZATION_ID", ""
    )
    workspace_id = options.get("workspace_id") or getattr(
        settings, "PROPERTY_CATALOG_DEV_WORKSPACE_ID", ""
    )
    scheduled_mode = options.get("scheduled_reconcile")
    scheduled_wall_ms = options.get("scheduled_reconcile_wall_ms")
    if scheduled_mode and scheduled_wall_ms is None:
        scheduled_wall_ms = getattr(
            settings,
            "PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS",
            1_200_000,
        )
    return configured_dev_rollout_request(
        organization_id=str(organization_id),
        workspace_id=str(workspace_id),
        settings_object=settings,
        execute=bool(options.get("execute") or scheduled_mode),
        status=bool(options.get("status")),
        initial_backfill_wall_ms=options.get("initial_backfill_wall_ms"),
        scheduled_reconcile_wall_ms=(
            int(scheduled_wall_ms) if scheduled_mode else None
        ),
        repair_expired_incomplete=bool(options.get("repair_expired_incomplete")),
        overrides={
            "acknowledgement": options.get("acknowledgement"),
            "cloud_deployment": options.get("cloud_deployment"),
            "dev_identity": options.get("dev_identity"),
            "environment": options.get("environment"),
            "source_database": options.get("source_database"),
            "target_database": options.get("target_database"),
        },
    )


def _runtime(request: DevRolloutRequest) -> ConfiguredDevRolloutRuntime:
    dotted_path = getattr(settings, _RUNTIME_FACTORY_SETTING, "")
    if not isinstance(dotted_path, str) or not dotted_path:
        raise DevRolloutError(
            f"{_RUNTIME_FACTORY_SETTING} must name the reviewed DEV runtime factory"
        )
    if dotted_path != CHECKED_IN_DEV_RUNTIME_FACTORY_PATH:
        raise DevRolloutError(
            f"{_RUNTIME_FACTORY_SETTING} must equal the reviewed checked-in factory"
        )
    factory = import_string(dotted_path)
    if not callable(factory):
        raise DevRolloutError("configured DEV runtime factory is not callable")
    runtime = factory(request)
    require_checked_in_property_catalog_dev_runtime(runtime)
    required = (
        "activate",
        "apply_schema",
        "backfill",
        "postgres_adapters",
        "postgres_reconciler",
        "postgres_request_factory",
        "postgres_snapshot_guard",
        "qualify",
        "reconcile_workspace",
        "reconcile_non_postgres",
        "status",
        "verify_schema",
    )
    if any(not callable(getattr(runtime, name, None)) for name in required):
        raise DevRolloutError("configured DEV runtime is missing a required stage")
    return runtime
