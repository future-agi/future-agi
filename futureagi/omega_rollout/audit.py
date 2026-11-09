"""Audit for every rollout change (READINESS RC-10): the actor, before and after, and the reason.

Each change writes:
- one `RolloutAuditEvent` (the complete record, global changes included, with kill counts);
- one `accounts.AuditLog` row under the actor's own organization, their oldest active membership
  (`accounts_audit_log` needs an org; omega_rollout/authority.py refuses an operator with none);
- for a change that names an org (allow, deny, request, invitation), one more `AuditLog` row under
  that org, so its admins see who changed its access and why.

Called inside the change's transaction: a change that cannot be audited does not happen. A change
with an actor and no organization to record it under raises, so its transaction rolls back.
"""

from __future__ import annotations

import uuid

from omega_rollout.authority import home_organization
from omega_rollout.models import RolloutAuditEvent

ACTION_PREFIX = "omega_rollout."


def record(
    *,
    action: str,
    product: str,
    actor,
    before: dict,
    after: dict,
    reason: str,
    epoch: int,
    organization=None,
    workspace_id=None,
    counts: dict | None = None,
    actor_organization=None,
) -> RolloutAuditEvent:
    from accounts.models.audit_log import AuditAction, AuditLog, AuditScope

    if f"{ACTION_PREFIX}{action}" not in AuditAction.ROLLOUT_ACTIONS:
        raise ValueError(f"undeclared rollout audit action {action!r}")
    home = actor_organization or home_organization(actor)
    if actor is not None and home is None:
        raise ValueError(
            "a rollout change by a person needs their organization for the audit"
        )

    event = RolloutAuditEvent.objects.create(
        action=action,
        product=product,
        actor=actor,
        organization_id=getattr(organization, "pk", None),
        workspace_id=workspace_id,
        before=before,
        after=after,
        reason=reason,
        counts=counts or {},
        epoch=epoch,
    )
    changes = {"product": product, "before": before, "after": after}
    metadata = {"reason": reason, "rollout_event_id": str(event.pk), "epoch": epoch}
    if counts:
        metadata["counts"] = counts
    targets = []
    if home is not None:
        targets.append(home)
    if organization is not None and getattr(organization, "pk", None) != getattr(
        home, "pk", None
    ):
        targets.append(organization)
    for org in targets:
        AuditLog.objects.create(
            organization=org,
            actor=actor,
            action=f"{ACTION_PREFIX}{action}",
            scope=AuditScope.WORKSPACE if workspace_id else AuditScope.ORGANIZATION,
            target_id=_target_id(organization, workspace_id, product),
            changes=changes,
            metadata=metadata,
        )
    return event


def _target_id(organization, workspace_id, product) -> uuid.UUID:
    if workspace_id:
        return uuid.UUID(str(workspace_id))
    if organization is not None:
        return organization.pk
    # A product-wide change has no row with a UUID; a stable name-based id stands for the product.
    return uuid.uuid5(uuid.NAMESPACE_URL, f"omega-rollout:{product}")
