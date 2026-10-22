"""
Guardrail Sync Service — merges guardrail policies into the org config
and pushes the result to the gateway.
"""

import structlog
from django.db import transaction

from agentcc.models import AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy

logger = structlog.get_logger(__name__)


def sync_guardrail_policies(org, user=None):
    """
    Collect all active guardrail policies for the org, merge their checks
    into a single guardrail config, create a new org config version, and
    push to the gateway.

    Returns:
        True on successful gateway push, False on failure or skip.
    """
    # Gather only SCOPE_GLOBAL active policies ordered by priority
    policies = list(
        AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=org,
            is_active=True,
            deleted=False,
            scope=AgentccGuardrailPolicy.SCOPE_GLOBAL,
        ).order_by("priority", "name")
    )

    # Merge checks — last policy wins per check name
    merged_checks = {}
    for policy in policies:
        for check in policy.checks:
            name = check.get("name")
            if name:
                merged_checks[name] = {
                    **check,
                    "_policy": str(policy.id),
                    "_policy_name": policy.name,
                    "_mode": policy.mode,
                    "_scope": policy.scope,
                }

    # With no check left in any active global policy, the section is an empty
    # checks list, which the push sends as an empty section, so the gateway
    # stops running the checks it had.
    merged_guardrails = {"checks": list(merged_checks.values())}
    if merged_checks:
        merged_guardrails = {"enabled": True, **merged_guardrails}

    with transaction.atomic():
        # Get current active org config with lock
        active_config = (
            AgentccOrgConfig.no_workspace_objects.select_for_update()
            .filter(
                organization=org,
                is_active=True,
                deleted=False,
            )
            .first()
        )

        if not active_config:
            logger.info(
                "guardrail_sync_skipped",
                reason="no active org config",
                org_id=str(org.id),
            )
            return False

        # Update guardrails in-place on the active config instead of creating
        # a new version. This prevents unbounded version accumulation — only
        # explicit user saves from the org config UI should create new versions.
        active_config.guardrails = merged_guardrails
        active_config.save(update_fields=["guardrails", "updated_at"])

    # Push to gateway
    from agentcc.services.config_push import push_org_config

    synced = push_org_config(str(org.id), active_config)
    if synced:
        logger.info(
            "guardrail_sync_success",
            org_id=str(org.id),
            version=active_config.version,
            policy_count=len(policies),
        )
    return synced
