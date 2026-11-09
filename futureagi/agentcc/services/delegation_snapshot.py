"""Primary-backed eligibility for each delegated batch item (733 addendum 4).

Wire contract: api_contracts/delegation/v1/bulk_snapshot.json pins the
org_id/user_id/workspace_id tags, with '-' for org-wide membership.
fetched_at is diagnostic metadata; the gateway's freshness bound starts
before its HTTP fetch, not at receipt.
"""

from django.apps import apps
from django.db.models import Exists, OuterRef
from django.utils import timezone

from accounts.models.workspace import WorkspaceMembership
from agentcc.services.gateway_membership import live_gateway_memberships


def revoked_batch_grants(fetched_at):
    """Read L1b's claims-only accounts.ServiceGrant registry on the primary.

    The schema is owned by L1b (accounts/models/service_grant.py, migration 0032
    on feat/okeys-733-env). Do not duplicate it here. An older composed app with
    no registered model cannot attest to revocation: None makes the snapshot
    incomplete. Database errors propagate, never become an empty deny list.
    exp is inclusive in fe1, so a grant expiring in this second is still listed.
    """
    try:
        model = apps.get_model("accounts", "ServiceGrant")
    except LookupError:
        return None
    return list(
        model._base_manager.using("default")
        .filter(
            kind="batch",
            aud="fa-eval-grant",
            revoked_at__isnull=False,
            exp__gte=int(fetched_at.timestamp()),
        )
        .order_by("jti")
        .values_list("jti", flat=True)
    )


def delegation_snapshot():
    fetched_at = timezone.now()
    revoked = revoked_batch_grants(fetched_at)
    snapshot = {
        "complete": revoked is not None,
        "fetched_at": fetched_at.isoformat(),
        "members": [],
        "revoked_grants": revoked if revoked is not None else [],
    }
    if revoked is None:
        return snapshot

    members = {
        (str(org), str(user), "-")
        for org, user in live_gateway_memberships().values_list(
            "organization_id", "user_id"
        )
    }
    # A workspace row cannot confer org access. Cross-check its actual org and
    # user rather than trusting the nullable legacy organization_membership FK.
    org_membership = live_gateway_memberships().filter(
        organization_id=OuterRef("workspace__organization_id"),
        user_id=OuterRef("user_id"),
    )
    workspaces = (
        WorkspaceMembership._base_manager.using("default")
        .filter(
            is_active=True,
            deleted=False,
            workspace__is_active=True,
            workspace__deleted=False,
        )
        .filter(Exists(org_membership))
        .values_list("workspace__organization_id", "user_id", "workspace_id")
    )
    members.update(
        (str(org), str(user), str(workspace)) for org, user, workspace in workspaces
    )
    snapshot["members"] = [
        {"org_id": org, "user_id": user, "workspace_id": workspace}
        for org, user, workspace in sorted(members)
    ]
    return snapshot
