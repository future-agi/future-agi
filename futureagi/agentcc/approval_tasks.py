"""Scheduled audit reconciliation; does not depend on a browser opening a list.

The main-owned scheduler runs every 15s, within the one-hour Redis retention.
No legacy deployment switch disables this audit lifecycle.
A missed retention window cannot be reconstructed; Redis remains authority.
"""

import structlog
from celery import shared_task

from agentcc.models import AgentccMCPApproval, AgentccOrgConfig
from agentcc.services.mcp_approval_audit import expire_audit, reconcile_organization
from agentcc.services.mcp_approval_client import (
    MCPApprovalClient,
    MCPApprovalsDisabled,
)

logger = structlog.get_logger(__name__)


@shared_task(name="agentcc.reconcile_mcp_approvals")
def reconcile_mcp_approvals():
    from accounts.models import Organization

    # Include retired configs with live audit rows so a config change cannot
    # prevent consumption/expiry reconciliation. No person/key is fabricated.
    ids = set(
        AgentccOrgConfig.objects.filter(is_active=True, deleted=False).values_list(
            "organization_id", flat=True
        )
    )
    ids.update(
        AgentccMCPApproval.objects.exclude(status="expired").values_list(
            "organization_id", flat=True
        )
    )
    count, failures, skipped = 0, 0, 0
    disabled = False
    client = MCPApprovalClient()
    for organization in Organization.objects.filter(pk__in=ids).iterator():
        if disabled:
            # Every organization shares one gateway: once it answers that it
            # serves no approval route, none can be reconciled this run.
            skipped += 1
            continue
        try:
            reconcile_organization(organization, client)
            count += 1
        except MCPApprovalsDisabled:
            # MCP is off on the gateway: nothing to reconcile, not a failure.
            disabled = True
            skipped += 1
        except Exception:
            # Do not log HTTP error bodies, opaque ids or principal material.
            failures += 1
            logger.warning(
                "mcp_approval_reconciliation_failed",
                organization_id=str(organization.pk),
            )
    # Local logical expiry needs no gateway, so it runs with MCP off too.
    expire_audit()
    if disabled:
        logger.info(
            "mcp_approval_reconciliation_skipped",
            reason=MCPApprovalsDisabled.reason,
            organizations=skipped,
        )
    if failures:
        raise RuntimeError(
            f"Approval audit reconciliation failed for {failures} organizations"
        )
    if disabled:
        return {
            "enabled": False,
            "reason": MCPApprovalsDisabled.reason,
            "organizations": count,
            "skipped": skipped,
        }
    return {"enabled": True, "organizations": count}
