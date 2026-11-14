"""Shared active org-config creation for gateway and policy mutations."""

from django.db import IntegrityError, transaction
from django.db.models import Max

from agentcc.models import AgentccOrgConfig
from agentcc.org_config_defaults import (
    default_cost_tracking_config,
    normalize_cost_tracking_config,
)


def get_or_create_org_config(org):
    with transaction.atomic():
        config = (
            AgentccOrgConfig.no_workspace_objects.select_for_update()
            .filter(organization=org, is_active=True, deleted=False)
            .first()
        )
        if config is None:
            latest = AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, deleted=False
            ).aggregate(Max("version"))["version__max"]
            try:
                # A savepoint, so losing the race leaves the caller's
                # transaction usable.
                with transaction.atomic():
                    config = AgentccOrgConfig.no_workspace_objects.create(
                        organization=org,
                        version=(latest or 0) + 1,
                        is_active=True,
                        cost_tracking=default_cost_tracking_config(),
                    )
            except IntegrityError:
                # A concurrent request created the active config first.
                config = (
                    AgentccOrgConfig.no_workspace_objects.select_for_update()
                    .filter(organization=org, is_active=True, deleted=False)
                    .first()
                )
                if config is None:
                    raise
        normalized = normalize_cost_tracking_config(config.cost_tracking)
        if normalized != config.cost_tracking:
            config.cost_tracking = normalized
            config.save(update_fields=["cost_tracking", "updated_at"])
        return config
