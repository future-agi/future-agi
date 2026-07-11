"""Shared org / workspace binding for MCP entry points (Phase 7A, seams S1-S5).

Single source of truth mirroring ``accounts/authentication.py::
_resolve_organization`` semantics on the MCP surface:

* **Org binding comes from a verified ACTIVE ``OrganizationMembership``** —
  never from the legacy ``user.organization`` FK, except for truly-legacy
  accounts that have *zero* membership rows (the HTTP path's step-5 rule).
  A user whose membership was revoked (``is_active=False``) keeps their FK
  but loses resolution — removal revokes effective access at the next
  request, without waiting for token expiry (seams S1/S2).
* **Token-bound org is the authority on the OAuth path** (S1):
  ``oauth_utils.generate_oauth_token`` encrypts ``org_id`` into the access
  token at approval; callers pass it here and membership is re-verified per
  request. Mismatch or revoked membership ⇒ ``None`` ⇒ clean MCP auth error.
* **Workspace binding is verified, never silently defaulted** (S3): every
  fallback branch applies ``user.can_access_workspace`` — HTTP parity with
  ``accounts/authentication.py:193`` (``PermissionDenied`` for an explicitly
  requested workspace the user cannot access). A workspace-restricted user
  falls back to their own first accessible workspace instead of silently
  reading the org default; if nothing is resolvable and the user lacks
  global workspace access, ``WorkspaceAccessDenied`` is raised.
* **System API keys execute as a deliberate principal** (S4): the longest-
  standing ACTIVE owner-level member of the key's org (falling back to the
  longest-standing active member) — not an arbitrary ``User`` row whose
  membership may have been revoked.

Used by ``mcp_server/mcp_app.py`` (OAuth + API-key auth), ``mcp_server/
views/transport.py`` (stdio-proxy tool-call/list views) and ``ee/falcon_ai/
consumers.py`` (WS org/workspace fallbacks). Negative probes:
``ai_tools/tests/test_authz_negative.py`` (Layer 4) and
``ee/falcon_ai/tests/test_org_scoping_ws.py``.
"""

import structlog

logger = structlog.get_logger(__name__)


class WorkspaceAccessDenied(Exception):
    """Workspace binding failed verification (seam S3) — callers must reject
    the request rather than fall back to an unverified workspace."""


def resolve_membership_org(user, org_id=None):
    """Resolve the organization a request executes as. Returns Organization or None.

    ``org_id`` given (token/key-bound): return that org IFF the user holds an
    ACTIVE membership in it; legacy-FK parity only when the FK matches AND the
    user has zero membership rows (truly-legacy account). ``None`` otherwise.

    ``org_id`` absent: deterministic first active membership (``joined_at``
    order — seam S5); legacy FK only when zero membership rows exist
    (``_resolve_organization`` steps 4-5).
    """
    from accounts.models.organization_membership import OrganizationMembership

    if user is None or not getattr(user, "is_authenticated", False):
        return None

    memberships = OrganizationMembership.no_workspace_objects.filter(user=user)

    if org_id is not None:
        try:
            membership = (
                memberships.filter(organization_id=org_id, is_active=True)
                .select_related("organization")
                .first()
            )
        except Exception:
            # Malformed org_id (bad UUID) — reject, never fall back.
            logger.warning("mcp_org_binding_invalid_org_id", org_id=str(org_id))
            return None
        if membership:
            return membership.organization
        # Legacy-FK parity: FK matches the bound org AND the user has ZERO
        # membership rows (a revoked member has rows ⇒ no fallback).
        if (
            getattr(user, "organization_id", None)
            and str(user.organization_id) == str(org_id)
            and not memberships.exists()
        ):
            return user.organization
        logger.warning(
            "mcp_org_binding_rejected",
            user_id=str(user.id),
            org_id=str(org_id),
            reason="no_active_membership",
        )
        return None

    # No bound org: deterministic first active membership.
    membership = (
        memberships.filter(is_active=True)
        .select_related("organization")
        .order_by("joined_at", "id")
        .first()
    )
    if membership:
        return membership.organization
    if getattr(user, "organization_id", None) and not memberships.exists():
        return user.organization
    return None


def resolve_accessible_workspace(user, organization, workspace_id=None):
    """Resolve and verify the workspace binding (seam S3 — HTTP parity).

    Returns a Workspace the user can access, or ``None`` (meaning org-wide
    context for a user with global workspace access). Raises
    ``WorkspaceAccessDenied`` when an explicitly bound workspace exists but
    the user cannot access it, or when nothing accessible is resolvable for
    a workspace-restricted user.

    Chain: explicit ``workspace_id`` (in-org + active + access-verified;
    not-found falls through like the HTTP path) → org default workspace IF
    accessible → user's first active workspace membership in the org →
    ``None`` only for users with global workspace access.
    """
    from accounts.models.workspace import Workspace, WorkspaceMembership

    if organization is None:
        return None

    if workspace_id:
        try:
            workspace = (
                Workspace.objects.all()
                .filter(id=workspace_id, organization=organization, is_active=True)
                .first()
            )
        except Exception:
            workspace = None  # malformed id — treat as not found, fall through
        if workspace is not None:
            if user.can_access_workspace(workspace):
                return workspace
            # Explicitly bound but inaccessible — deny, never silently
            # fall back (authentication.py:193 parity).
            logger.warning(
                "mcp_workspace_binding_denied",
                user_id=str(user.id),
                workspace_id=str(workspace_id),
            )
            raise WorkspaceAccessDenied(
                f"Access denied to workspace {workspace_id}"
            )
        # Not found / wrong org / inactive — fall through to fallbacks
        # (mirrors accounts/authentication.py _get_requested_workspace).

    default_ws = (
        Workspace.objects.all()
        .filter(organization=organization, is_default=True, is_active=True)
        .first()
    )
    if default_ws and user.can_access_workspace(default_ws):
        return default_ws

    # Workspace-restricted user: their own first accessible workspace
    # (mirrors authentication.py _get_user_default_workspace's membership
    # fallback) instead of the org default they cannot access.
    ws_membership = (
        WorkspaceMembership.no_workspace_objects.filter(
            user=user,
            workspace__organization=organization,
            workspace__is_active=True,
            is_active=True,
        )
        .select_related("workspace")
        .order_by("created_at", "id")
        .first()
    )
    if ws_membership and user.can_access_workspace(ws_membership.workspace):
        return ws_membership.workspace

    # Nothing resolvable: org-wide (workspace=None) context is acceptable
    # only for users whose role already grants global workspace access.
    if user.has_global_workspace_access(organization):
        return None
    raise WorkspaceAccessDenied(
        "No accessible workspace could be resolved for this user"
    )


def resolve_system_key_principal(organization):
    """Seam S4: the principal a system-type API key executes as.

    Deliberate choice (documented, not an accident of ``.first()``): the
    longest-standing ACTIVE owner-level member of the org — i.e. the org
    owner — falling back to the longest-standing active member. The user
    must be active AND hold an active membership; a revoked or deactivated
    account can never be impersonated by a system key.
    """
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level

    memberships = (
        OrganizationMembership.no_workspace_objects.filter(
            organization=organization,
            is_active=True,
            user__is_active=True,
        )
        .select_related("user")
        .order_by("joined_at", "id")
    )
    membership = memberships.filter(level=Level.OWNER).first() or memberships.first()
    return membership.user if membership else None
