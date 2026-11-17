"""Who may do what with webhooks (DESIGN.md section 9.1).

Levels are FA's (``tfc.constants.levels``): Viewer 1, Member 3, Admin 8, Owner 15; an org Admin or Owner is a
Workspace Admin everywhere. Every lookup is scoped by the caller's organization, and an id in another tenant, or in a
workspace the caller cannot open, answers 404 (never 403), so ids do not leak existence. A caller who can open the
workspace but lacks the level gets 403.

API-key callers get the role of the key's user (a system key acts as the org's first user, as everywhere in FA). A key
pinned to a workspace is confined to it: it sees org-wide endpoints read-only and manages nothing outside it.

Memberships and policies are read with explicit organization and workspace ids, never through the thread-local
workspace context, so the same rules serve the offboarding check, which runs outside any request.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tfc.constants.levels import Level
from webhooks.api_errors import WebhookApiError
from webhooks.models import Endpoint, Policy

VIEW = "view"
VIEW_URL = "view_url"
VIEW_LOG = "view_log"
VIEW_FULL_BODY = "view_full_body"
MANAGE = "manage"
ADMIN = "admin"
ACTIONS = (VIEW, VIEW_URL, VIEW_LOG, VIEW_FULL_BODY, MANAGE, ADMIN)


def org_level_of(user_id, organization_id) -> int:
    """The active org membership's level, 0 when there is none."""
    from accounts.models.organization_membership import OrganizationMembership

    m = OrganizationMembership.no_workspace_objects.filter(
        user_id=user_id, organization_id=organization_id, is_active=True
    ).first()
    return 0 if m is None else int(m.level_or_legacy or 0)


def workspace_level_of(user_id, organization_id, workspace_id, org_level: int) -> int:
    """max(org level, workspace level); an org Admin+ is a Workspace Admin everywhere; 0 = cannot open it."""
    from accounts.models.workspace import Workspace, WorkspaceMembership

    if org_level <= 0:
        return 0
    if not Workspace.no_workspace_objects.filter(
        id=workspace_id, organization_id=organization_id, is_active=True
    ).exists():
        return 0
    if org_level >= Level.ADMIN:
        return max(org_level, Level.WORKSPACE_ADMIN)
    m = WorkspaceMembership.no_workspace_objects.filter(
        user_id=user_id, workspace_id=workspace_id, is_active=True
    ).first()
    if m is None:
        return 0
    return max(org_level, int(m.level_or_legacy or 0))


def policies(organization_id, workspace_id) -> tuple[Policy | None, Policy | None]:
    org = Policy.objects.filter(
        organization_id=organization_id, workspace__isnull=True
    ).first()
    ws = (
        None
        if workspace_id is None
        else Policy.objects.filter(
            organization_id=organization_id, workspace_id=workspace_id
        ).first()
    )
    return org, ws


def members_can_manage(organization_id, workspace_id) -> bool:
    """The org turns it on; a workspace can turn it off (section 4.9)."""
    org, ws = policies(organization_id, workspace_id)
    if org is None or not org.members_can_manage:
        return False
    return ws is None or ws.members_can_manage


def allowed_host_lists(organization_id, workspace_id) -> dict:
    """The policy lists in the shape ``core.addresses.url_decision`` takes (empty = any public host)."""
    org, ws = policies(organization_id, workspace_id)
    out = {}
    if org is not None and org.allowed_hosts:
        out["org_allowed_hosts"] = list(org.allowed_hosts)
    if ws is not None and ws.allowed_hosts:
        out["workspace_allowed_hosts"] = list(ws.allowed_hosts)
    return out


def full_payload_allowed(organization_id) -> bool:
    org, _ = policies(organization_id, None)
    return bool(org and org.full_payload_allowed)


def allows(
    action: str, level: int, *, org_wide: bool, org_level: int, pinned: bool, mcm
) -> bool:
    """The section 9.1 table. ``level`` is the caller's level where the endpoint lives (the org level for an
    org-wide endpoint); ``mcm`` is called only when a Member's write depends on ``members_can_manage``."""
    if action not in ACTIONS:
        raise ValueError(action)
    if org_wide:
        if action == VIEW:
            return level >= Level.MEMBER
        return org_level >= Level.ADMIN and not pinned
    if action in (VIEW, VIEW_LOG):
        return level >= Level.MEMBER
    if action in (VIEW_URL, VIEW_FULL_BODY, ADMIN):
        return level >= Level.WORKSPACE_ADMIN
    # MANAGE: create, edit, delete, turn on or off, rotate, test, redeliver one.
    return level >= Level.WORKSPACE_ADMIN or (level >= Level.MEMBER and bool(mcm()))


def may_still_manage(user_id, ep: Endpoint) -> tuple[bool, int]:
    """Whether an endpoint's owner may still manage it (G1, section 9.1): an active account with the level the table
    asks where the endpoint lives. Returns the verdict and the level it was judged at (0 when the account is gone or
    inactive)."""
    from accounts.models.user import User

    if user_id is None or not User.objects.filter(id=user_id, is_active=True).exists():
        return False, 0
    org_level = org_level_of(user_id, ep.organization_id)
    if ep.workspace_id is None:
        return org_level >= Level.ADMIN, org_level
    level = workspace_level_of(user_id, ep.organization_id, ep.workspace_id, org_level)
    ok = allows(
        MANAGE,
        level,
        org_wide=False,
        org_level=org_level,
        pinned=False,
        mcm=lambda: members_can_manage(ep.organization_id, ep.workspace_id),
    )
    return ok, level


@dataclass
class Caller:
    user: object
    organization: object
    org_level: int
    pinned_workspace_id: str | None = None
    request_workspace_id: str | None = None
    _ws: dict = field(default_factory=dict, repr=False)

    @property
    def user_id(self):
        return getattr(self.user, "id", None)

    @property
    def organization_id(self):
        return self.organization.id

    def ws_level(self, workspace_id) -> int:
        """0 when the caller cannot open the workspace (or a pinned key points elsewhere)."""
        if workspace_id is None:
            return 0
        key = str(workspace_id)
        if self.pinned_workspace_id and key != self.pinned_workspace_id:
            return 0
        if key not in self._ws:
            self._ws[key] = workspace_level_of(
                self.user_id, self.organization_id, workspace_id, self.org_level
            )
        return self._ws[key]

    def org_wide_level(self) -> int:
        """Who sees org-wide rows (read-only in the workspace list): Member+ in the org, or in the request's
        workspace."""
        return max(self.org_level, self.ws_level(self.request_workspace_id))

    def level_at(self, workspace_id) -> int:
        return (
            self.org_wide_level()
            if workspace_id is None
            else self.ws_level(workspace_id)
        )

    def can(self, action: str, workspace_id) -> bool:
        level = self.level_at(workspace_id)
        return allows(
            action,
            level,
            org_wide=workspace_id is None,
            org_level=self.org_level,
            pinned=bool(self.pinned_workspace_id),
            mcm=lambda: members_can_manage(self.organization_id, workspace_id),
        )

    def require(self, action: str, workspace_id) -> None:
        """404 when the caller cannot open the scope at all; 403 when it can but lacks the level."""
        if self.level_at(workspace_id) <= 0:
            raise WebhookApiError("WEBHOOK_NOT_FOUND")
        if not self.can(action, workspace_id):
            raise WebhookApiError("WEBHOOK_FORBIDDEN")

    def require_endpoint(self, endpoint_id: str, action: str) -> Endpoint:
        ep = (
            Endpoint.objects.filter(
                id=endpoint_id, organization_id=self.organization_id, deleted=False
            )
            .exclude(kind="cli_listen")
            .first()
        )
        if ep is None:
            raise WebhookApiError("WEBHOOK_NOT_FOUND")
        self.require(action, ep.workspace_id)
        return ep

    def visible_workspaces(self, action: str = VIEW) -> list[str] | None:
        """Workspace ids where ``action`` is allowed; None means every workspace of the org (an org Admin+ caller
        with no pinned key)."""
        from accounts.models.workspace import WorkspaceMembership

        if self.org_level >= Level.ADMIN and not self.pinned_workspace_id:
            return None
        if self.pinned_workspace_id:
            candidates = [self.pinned_workspace_id]
        else:
            # Below org Admin, a person opens only the workspaces they are a member of: one query, not one per
            # workspace of the org.
            candidates = WorkspaceMembership.no_workspace_objects.filter(
                user_id=self.user_id,
                workspace__organization_id=self.organization_id,
                workspace__is_active=True,
                is_active=True,
            ).values_list("workspace_id", flat=True)
        return sorted({str(w) for w in candidates if self.can(action, w)})

    def sees_org_wide(self, action: str = VIEW) -> bool:
        return self.can(action, None)


def caller_of(request) -> Caller:
    from accounts.utils import get_request_organization

    org = get_request_organization(request)
    user = request.user
    if org is None or user is None or not getattr(user, "is_authenticated", False):
        raise WebhookApiError("WEBHOOK_FORBIDDEN")
    level = org_level_of(user.id, org.id)
    if level <= 0:
        raise WebhookApiError("WEBHOOK_FORBIDDEN")
    key = getattr(request, "org_api_key", None)
    pinned = str(key.workspace_id) if key is not None and key.workspace_id else None
    ws = getattr(request, "workspace", None)
    return Caller(
        user=user,
        organization=org,
        org_level=level,
        pinned_workspace_id=pinned,
        request_workspace_id=pinned or (str(ws.id) if ws is not None else None),
    )
