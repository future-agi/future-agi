"""The shared admission boundary for personal API keys, across transports."""

from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from accounts.api_key_constants import PERSONAL_KEY_CREATE_URL, RETIRED_KEY_TYPES
from accounts.api_key_lookup import find_api_key
from accounts.api_secrets import MAX_SECRET_LENGTH, verify_secret
from accounts.models import OrgApiKey, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.routers import primary_required


class APIKeyRetired(AuthenticationFailed):
    default_code = "api_key_retired"
    default_detail = "Organization keys were retired. Create a personal key."
    create_url = PERSONAL_KEY_CREATE_URL


def is_retired_key(row):
    return (
        row.type in RETIRED_KEY_TYPES
        or row.retired_at is not None
        or bool(row.retired_reason)
    )


@primary_required
def live_membership(user_id, organization_id):
    return OrganizationMembership._base_manager.filter(
        user_id=user_id,
        organization_id=organization_id,
        is_active=True,
        deleted=False,
        user__is_active=True,
    ).first()


def membership_role(membership):
    if membership.level is None:
        return membership.role
    return {
        Level.OWNER: OrganizationRoles.OWNER,
        Level.ADMIN: OrganizationRoles.ADMIN,
        Level.MEMBER: OrganizationRoles.MEMBER,
        Level.VIEWER: OrganizationRoles.MEMBER_VIEW_ONLY,
    }.get(membership.level, OrganizationRoles.MEMBER_VIEW_ONLY)


@primary_required
def admit_api_key(row, *, workspace_unavailable_on_write=False):
    """Require a live person, org membership and, when pinned, workspace membership.

    This predicate deliberately has no home-organization fallback and never
    creates membership. It cannot reveal retirement without a proven secret.
    """
    if (
        row is None
        or row.type != "user"
        or not row.user_id
        or row.deleted
        or not row.enabled
        or is_retired_key(row)
    ):
        raise AuthenticationFailed("Invalid API key or secret key")
    membership = live_membership(row.user_id, row.organization_id)
    if membership is None:
        raise AuthenticationFailed("Invalid API key or secret key")
    # Re-read the person before disclosing a destination-specific denial.
    user = User._base_manager.filter(pk=row.user_id, is_active=True).first()
    if user is None:
        raise AuthenticationFailed("Invalid API key or secret key")
    if (
        row.workspace_id
        and not WorkspaceMembership._base_manager.filter(
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            is_active=True,
            deleted=False,
            workspace__is_active=True,
            workspace__deleted=False,
            workspace__organization_id=row.organization_id,
        ).exists()
    ):
        # HTTP writes must distinguish an unavailable destination from omission.
        # The secret and live person/org membership were already verified; reads
        # and non-HTTP callers retain their existing authentication failure.
        if workspace_unavailable_on_write:
            raise PermissionDenied(
                "The selected workspace is unavailable. Select an accessible workspace and retry.",
                code="workspace_unavailable",
            )
        raise AuthenticationFailed("Invalid API key or secret key")
    user.organization = row.organization
    user.organization_role = membership_role(membership)
    row.user = user
    return row


@primary_required
def authenticate_api_key(
    api_key, presented_secret, *, workspace_unavailable_on_write=False
):
    """Only a stored hash proving possession permits the retirement message."""
    if (
        not api_key
        or not presented_secret
        or len(api_key) > MAX_SECRET_LENGTH
        or len(presented_secret) > MAX_SECRET_LENGTH
    ):
        raise AuthenticationFailed("Invalid API key or secret key")
    rows = OrgApiKey._base_manager.select_related("organization", "workspace", "user")
    row = rows.filter(api_key=api_key).first()
    if row is not None and is_retired_key(row):
        verdict = verify_secret(row.secret_key_hash, "", presented_secret)
        if row.secret_key_hash and verdict.ok:
            raise APIKeyRetired()
        raise AuthenticationFailed("Invalid API key or secret key")
    row = find_api_key(rows, api_key, presented_secret)
    return admit_api_key(
        row, workspace_unavailable_on_write=workspace_unavailable_on_write
    )


def require_personal_key_session(request):
    """Proven browser access sessions only; never infer provenance from a user."""
    from rest_framework.authentication import SessionAuthentication

    session = getattr(request, "credential_provenance", None) == "interactive_session"
    if isinstance(
        getattr(request, "successful_authenticator", None), SessionAuthentication
    ):
        session = True
    if (
        not session
        or getattr(request, "org_api_key", None) is not None
        or getattr(request, "auth_scope", None) is not None
    ):
        raise PermissionDenied(
            "Sign in interactively to create your personal API key.",
            code="interactive_session_required",
        )
    org = getattr(request, "organization", None)
    if org is None or live_membership(request.user.pk, org.pk) is None:
        raise PermissionDenied("Active organization membership is required.")


def can_manage_member_keys(request):
    org = getattr(request, "organization", None) or request.user.organization
    membership = live_membership(request.user.pk, org.pk) if org else None
    return membership is not None and membership_role(membership) in {
        OrganizationRoles.OWNER,
        OrganizationRoles.ADMIN,
    }
