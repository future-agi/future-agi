"""Fail-closed production admission for the shared catalog lifecycle runtime.

The production request is a strict subtype of the reviewed rollout contract so
the reconciliation implementation stays single-sourced.  It cannot be created
with a DEV namespace, identity, acknowledgement, or cloud marker.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .dev_rollout import (
    ConfiguredDevRolloutRuntime,
    DevRolloutError,
    DevRolloutRequest,
    DevRolloutResult,
    run_configured_dev_rollout,
    validate_rollout_request_common,
)
from .publisher import PropertyCatalogPublishError, require_prod_catalog_database

PRODUCTION_ENVIRONMENT = "production"
PRODUCTION_CLOUD_DEPLOYMENTS = frozenset({"EU", "US"})
PRODUCTION_LIFECYCLE_ACK = "PROPERTY_CATALOG_PRODUCTION_LIFECYCLE_V1"
PRODUCTION_CONTROL_PLANE_IDENTITY_RE = re.compile(
    r"^prod:[a-z0-9][a-z0-9._:/-]{2,127}$"
)


class ProductionRolloutRequest(DevRolloutRequest):
    """Exact production request accepted by the shared checked-in runtime."""

    def __post_init__(self) -> None:
        validate_rollout_request_common(self)
        if self.environment != PRODUCTION_ENVIRONMENT:
            raise DevRolloutError(
                "production lifecycle requires environment='production'"
            )
        if self.cloud_deployment not in PRODUCTION_CLOUD_DEPLOYMENTS:
            raise DevRolloutError(
                "production lifecycle requires an exact supported cloud deployment"
            )
        identity = str(self.dev_identity or "")
        folded = identity.casefold()
        if PRODUCTION_CONTROL_PLANE_IDENTITY_RE.fullmatch(identity) is None or any(
            marker in folded for marker in ("dev", "local", "test")
        ):
            raise DevRolloutError(
                "production lifecycle requires a pinned production control-plane identity"
            )
        try:
            require_prod_catalog_database(self.target_database)
        except PropertyCatalogPublishError as exc:
            raise DevRolloutError(
                "production lifecycle target must match the configured "
                "production catalog database"
            ) from exc
        if self.acknowledgement != PRODUCTION_LIFECYCLE_ACK:
            raise DevRolloutError(
                "the exact production lifecycle acknowledgement is required"
            )

    @property
    def control_plane_identity(self) -> str:
        return self.dev_identity


def configured_production_rollout_request(
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
) -> ProductionRolloutRequest:
    """Build one production request from the explicit lifecycle settings."""

    values = dict(overrides or {})

    def configured(argument: str, setting: str) -> str:
        value = values.get(argument) or getattr(settings_object, setting, "")
        return str(value)

    return ProductionRolloutRequest(
        organization_id=organization_id,
        workspace_id=workspace_id,
        environment=PRODUCTION_ENVIRONMENT,
        cloud_deployment=configured("cloud_deployment", "CLOUD_DEPLOYMENT"),
        dev_identity=configured(
            "control_plane_identity",
            "PROPERTY_CATALOG_LIFECYCLE_IDENTITY",
        ),
        source_database=configured(
            "source_database",
            "PROPERTY_CATALOG_LIFECYCLE_SOURCE_DATABASE",
        ),
        target_database=configured(
            "target_database",
            "PROPERTY_CATALOG_LIFECYCLE_TARGET_DATABASE",
        ),
        acknowledgement=configured(
            "acknowledgement",
            "PROPERTY_CATALOG_LIFECYCLE_ACK",
        ),
        execute=execute,
        status=status,
        initial_backfill_wall_ms=initial_backfill_wall_ms,
        scheduled_reconcile_wall_ms=scheduled_reconcile_wall_ms,
        repair_expired_incomplete=repair_expired_incomplete,
    )


def run_configured_production_rollout(
    *,
    request: ProductionRolloutRequest,
    runtime: ConfiguredDevRolloutRuntime | None,
) -> DevRolloutResult:
    """Run the shared fixed lifecycle after production request admission."""

    if not isinstance(request, ProductionRolloutRequest):
        raise TypeError("request must be a ProductionRolloutRequest")
    return run_configured_dev_rollout(request=request, runtime=runtime)


__all__ = [
    "PRODUCTION_CLOUD_DEPLOYMENTS",
    "PRODUCTION_ENVIRONMENT",
    "PRODUCTION_LIFECYCLE_ACK",
    "ProductionRolloutRequest",
    "configured_production_rollout_request",
    "run_configured_production_rollout",
]
