import structlog
from django.db import models

from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from tfc.constants.levels import Level

logger = structlog.get_logger(__name__)


_ALLOWED_SORT_FIELDS = {
    "name",
    "email",
    "ws_level",
    "status",
    "type",
    "date_joined",
    "created_at",
}


def _member_row(
    *,
    row_id,
    name,
    email,
    ws_level,
    status,
    created_at,
    member_type,
    org_level=None,
    auto_access=False,
):
    """Single source of the member-row shape (was hand-built in 3 places).

    Derives the ws/org role strings from the integer levels so the columns
    can't drift between the explicit-member, auto-access-admin and pending-
    invite sources. Shape is contracted by
    ``accounts.serializers.rbac.WorkspaceMemberRowSerializer``.
    """
    return {
        "id": str(row_id),
        "name": name or "",
        "email": email,
        "ws_level": ws_level,
        "ws_role": Level.to_ws_string(ws_level) if ws_level is not None else None,
        "org_level": org_level,
        "org_role": Level.to_org_string(org_level) if org_level is not None else None,
        "status": status,
        "created_at": created_at or "",
        "type": member_type,
        "auto_access": auto_access,
    }


def list_workspace_members(
    *,
    workspace,
    organization,
    search="",
    filter_status=None,
    filter_role=None,
    sort="-created_at",
    page=1,
    limit=20,
):
    rows, explicit_user_ids = _explicit_members(workspace, organization)
    rows.extend(_auto_access_admins(organization, explicit_user_ids))
    rows = _merge_pending_invites(rows, organization, workspace)
    rows = _apply_filters(rows, search, filter_status, filter_role)
    rows = _apply_sort(rows, sort)

    total = len(rows)
    start = (page - 1) * limit
    return {
        "results": rows[start : start + limit],
        "total": total,
        "page": page,
        "limit": limit,
    }


def _explicit_members(workspace, organization):
    ws_memberships = (
        WorkspaceMembership.objects.filter(
            workspace=workspace, is_active=True, user__is_active=True
        )
        .exclude(organization_membership__is_active=False)
        .select_related("user", "organization_membership")
    )
    org_membership_by_user = {
        om.user_id: om
        for om in OrganizationMembership.objects.filter(
            organization=organization, is_active=True
        )
    }

    rows = []
    explicit_user_ids = set()
    fallback_user_ids = []
    for ws_mem in ws_memberships:
        user = ws_mem.user
        explicit_user_ids.add(user.id)
        org_mem = ws_mem.organization_membership
        if org_mem is None:
            org_mem = org_membership_by_user.get(user.id)
            fallback_user_ids.append(str(user.id))
        rows.append(
            _member_row(
                row_id=user.id,
                name=user.name,
                email=user.email,
                ws_level=ws_mem.level_or_legacy,
                org_level=org_mem.level_or_legacy if org_mem else None,
                status="Active",
                created_at=(
                    ws_mem.created_at.isoformat()
                    if getattr(ws_mem, "created_at", None)
                    else ""
                ),
                member_type="member",
            )
        )

    if fallback_user_ids:
        # Surfaces the data drift the 0022 backfill is meant to heal; this log
        # should stop firing once prod NULL count holds at 0. TODO(TH-5928): remove.
        logger.warning(
            "workspace_member_list_null_org_fk",
            workspace_id=str(workspace.id),
            user_ids=fallback_user_ids,
        )
    return rows, explicit_user_ids


def _auto_access_admins(organization, explicit_user_ids):
    org_admins = (
        OrganizationMembership.objects.filter(
            organization=organization, is_active=True, user__is_active=True
        )
        .filter(
            models.Q(level__gte=Level.ADMIN)
            | models.Q(level__isnull=True, role__in=["Admin", "Owner"])
        )
        .select_related("user")
    )
    rows = []
    for org_mem in org_admins:
        if org_mem.user_id in explicit_user_ids:
            continue
        user = org_mem.user
        rows.append(
            _member_row(
                row_id=user.id,
                name=user.name,
                email=user.email,
                ws_level=Level.WORKSPACE_ADMIN,
                org_level=org_mem.level_or_legacy,
                status="Active",
                created_at=(org_mem.joined_at.isoformat() if org_mem.joined_at else ""),
                member_type="member",
                auto_access=True,
            )
        )
    return rows


def _merge_pending_invites(member_rows, organization, workspace):
    invites = _pending_invites(organization, workspace)
    invited_emails = {inv["email"] for inv in invites}
    deduped = [r for r in member_rows if r["email"] not in invited_emails]
    deduped.extend(invites)
    return deduped


def _pending_invites(organization, workspace):
    qs = OrganizationInvite.objects.filter(
        organization=organization, status=InviteStatus.PENDING
    )
    results = []
    for inv in qs:
        ws_match = None
        if inv.workspace_access:
            for entry in inv.workspace_access:
                if str(entry.get("workspace_id")) == str(workspace.id):
                    ws_match = entry
                    break
        # Admin+ invites auto-access every workspace, so include them even
        # without a workspace_access entry for this workspace.
        if ws_match is None and inv.level < Level.ADMIN:
            continue
        ws_level = (
            ws_match.get("level", Level.WORKSPACE_ADMIN)
            if ws_match
            else Level.WORKSPACE_ADMIN
        )
        results.append(
            _member_row(
                row_id=inv.id,
                name=inv.target_email.split("@")[0],
                email=inv.target_email,
                ws_level=ws_level,
                org_level=inv.level,
                status=inv.effective_status,
                created_at=inv.created_at.isoformat() if inv.created_at else "",
                member_type="invite",
            )
        )
    return results


def _apply_filters(rows, search, filter_status, filter_role):
    if search:
        s = search.lower()
        rows = [
            r
            for r in rows
            if s in r.get("name", "").lower() or s in r.get("email", "").lower()
        ]
    if filter_status:
        rows = [r for r in rows if r["status"] in filter_status]
    if filter_role:
        ws_levels = set()
        for val in filter_role:
            val = str(val)
            if val.startswith("ws_"):
                ws_levels.add(int(val[3:]))
            else:
                try:
                    ws_levels.add(int(val))
                except ValueError:
                    pass
        if ws_levels:
            rows = [r for r in rows if r.get("ws_level") in ws_levels]
    return rows


def _apply_sort(rows, sort):
    reverse = sort.startswith("-")
    key = sort.lstrip("-")
    if key not in _ALLOWED_SORT_FIELDS:
        key = "name"
    rows.sort(
        key=lambda r: (r.get(key) is None, r.get(key) or ""),
        reverse=reverse,
    )
    return rows
