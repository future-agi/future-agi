"""Landing selection follows can_access_workspace, not random UUID order."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.authentication import APIKeyAuthentication
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.views.organization_selection import SwitchOrganizationView
from accounts.views.user import get_user_info
from accounts.views.workspace_management import WorkspaceInviteAPIView
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context


def org_member(org, email, role, level, *, is_active=True):
    """A user whose only link to ``org`` is one OrganizationMembership row."""
    user = User.objects.create_user(
        email=email,
        password="test-password",
        name=str(role),
        organization=org,
        organization_role=role,
        config={},
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=role,
        level=level,
        is_active=is_active,
    )
    return user, membership


@pytest.fixture
def landing(db):
    clear_workspace_context()
    org = Organization.objects.create(name="Landing organization")
    # The owner invites; the resolving user is an org Member, whose workspace
    # access comes only from WorkspaceMembership rows.
    owner, _ = org_member(
        org, "landing-owner@example.test", OrganizationRoles.OWNER, Level.OWNER
    )
    user, om = org_member(
        org, "landing@example.test", OrganizationRoles.MEMBER, Level.MEMBER
    )
    # Both membership and workspace UUID order deliberately disagree with default.
    other = Workspace.objects.create(
        id=UUID(int=1),
        name="Other",
        organization=org,
        created_by=owner,
    )
    default = Workspace.objects.create(
        id=UUID(int=9),
        name="Default",
        organization=org,
        created_by=owner,
        is_default=True,
    )
    other_mem = WorkspaceMembership.no_workspace_objects.create(
        id=UUID(int=1),
        user=user,
        workspace=other,
        organization_membership=om,
        role=OrganizationRoles.WORKSPACE_ADMIN,
    )
    default_mem = WorkspaceMembership.no_workspace_objects.create(
        id=UUID(int=9),
        user=user,
        workspace=default,
        organization_membership=om,
        role=OrganizationRoles.WORKSPACE_ADMIN,
    )
    return SimpleNamespace(
        org=org,
        owner=owner,
        user=user,
        default=default,
        other=other,
        default_mem=default_mem,
        other_mem=other_mem,
    )


def save_config(landing, config, user=None):
    user = user or landing.user
    user.config = config
    user.save(update_fields=["config"])


def resolve(landing, entry, user=None):
    user = user or landing.user
    if entry == "resolver":
        from accounts.services.workspace_landing import resolve_user_workspace

        return resolve_user_workspace(user, landing.org)
    if entry == "auth":
        return APIKeyAuthentication()._get_user_default_workspace(user, landing.org)
    if entry == "auth_context":
        request = SimpleNamespace(
            headers={}, GET={}, method="GET", path="/accounts/user/"
        )
        APIKeyAuthentication()._set_workspace_context(request, user)
        assert request.organization == landing.org
        return request.workspace
    if entry == "saml":
        return APIKeyAuthentication()._get_scoped_default_workspace(user, landing.org)
    return SwitchOrganizationView()._resolve_workspace_for_org(user, landing.org)


@pytest.mark.parametrize("entry", ["auth", "saml", "switch"])
def test_default_beats_random_membership_uuid(landing, entry):
    assert resolve(landing, entry) == landing.default


@pytest.mark.parametrize("entry", ["auth", "saml", "switch"])
def test_configured_default_is_used_including_saml(landing, entry):
    save_config(landing, {"defaultWorkspaceId": str(landing.other.id)})
    assert resolve(landing, entry) == landing.other


@pytest.mark.parametrize("entry", ["auth", "saml", "switch"])
def test_saved_preference_beats_configured_default(landing, entry):
    save_config(
        landing,
        {
            "orgWorkspaceMap": {str(landing.org.id): str(landing.other.id)},
            "defaultWorkspaceId": str(landing.default.id),
        },
    )
    assert resolve(landing, entry) == landing.other


@pytest.mark.parametrize("entry", ["auth", "saml", "switch"])
@pytest.mark.parametrize(
    "state", ["inactive", "deleted", "removed", "foreign", "malformed"]
)
def test_stale_or_foreign_preference_falls_through(landing, entry, state):
    if state == "inactive":
        landing.other_mem.is_active = False
        landing.other_mem.save(update_fields=["is_active"])
    elif state == "deleted":
        landing.other_mem.delete()
    elif state == "removed":
        WorkspaceMembership.no_workspace_objects.filter(
            pk=landing.other_mem.pk
        ).delete()
    elif state == "foreign":
        other_org = Organization.objects.create(name="Foreign organization")
        landing.other.organization = other_org
        landing.other.save(update_fields=["organization"])
    preference = "not-a-uuid" if state == "malformed" else str(landing.other.id)
    save_config(
        landing,
        {
            "orgWorkspaceMap": {str(landing.org.id): preference},
            "currentWorkspaceId": preference,
            "defaultWorkspaceId": preference,
        },
    )
    assert resolve(landing, entry) == landing.default


@pytest.mark.parametrize("entry", ["auth", "saml", "switch"])
def test_unjoined_default_is_not_chosen_for_member(landing, entry):
    landing.default_mem.is_active = False
    landing.default_mem.save(update_fields=["is_active"])
    save_config(landing, {"defaultWorkspaceId": str(landing.default.id)})
    assert resolve(landing, entry) == landing.other


@pytest.mark.parametrize("entry", ["auth", "saml", "switch"])
def test_no_default_uses_earliest_membership_then_workspace_id(landing, entry):
    landing.default.is_default = False
    landing.default.save(update_fields=["is_default"])
    earlier = timezone.now() - timedelta(days=2)
    WorkspaceMembership.no_workspace_objects.filter(pk=landing.default_mem.pk).update(
        created_at=earlier
    )
    assert resolve(landing, entry) == landing.default
    WorkspaceMembership.no_workspace_objects.filter(pk=landing.other_mem.pk).update(
        created_at=earlier
    )
    assert resolve(landing, entry) == landing.other
    # Equal membership times: workspace UUID wins even when it was created later.
    Workspace.no_workspace_objects.filter(pk=landing.default.pk).update(
        created_at=earlier
    )
    assert resolve(landing, entry) == landing.other
    # Workspace creation time is not an additional ordering key.
    Workspace.no_workspace_objects.filter(pk=landing.other.pk).update(
        created_at=earlier
    )
    assert resolve(landing, entry) == landing.other


@pytest.mark.parametrize("entry", ["auth", "saml", "switch"])
def test_deactivated_workspace_is_not_chosen(landing, entry):
    landing.default.is_active = False
    landing.default.save(update_fields=["is_active"])
    save_config(landing, {"defaultWorkspaceId": str(landing.default.id)})
    assert resolve(landing, entry) == landing.other


@pytest.mark.parametrize("entry", ["auth", "saml", "switch"])
def test_no_active_memberships_returns_none_without_reactivating(landing, entry):
    WorkspaceMembership.no_workspace_objects.filter(user=landing.user).update(
        is_active=False
    )
    assert resolve(landing, entry) is None
    assert not WorkspaceMembership.no_workspace_objects.filter(
        user=landing.user, is_active=True
    ).exists()


@pytest.mark.parametrize("entry", ["resolver", "auth", "saml", "switch"])
@pytest.mark.parametrize(
    "role,level",
    [
        (OrganizationRoles.OWNER, Level.OWNER),
        (OrganizationRoles.ADMIN, Level.ADMIN),
    ],
)
def test_owner_or_admin_without_membership_lands_on_default(
    landing, entry, role, level
):
    user, _ = org_member(landing.org, "global@example.test", role, level)
    assert user.can_access_workspace(landing.default)
    assert resolve(landing, entry, user) == landing.default
    # Landing is a read: no WorkspaceMembership is provisioned for global access.
    assert not WorkspaceMembership._base_manager.filter(user=user).exists()


@pytest.mark.parametrize(
    "entry", ["resolver", "auth", "auth_context", "saml", "switch"]
)
def test_member_without_membership_gets_none(landing, entry):
    user, _ = org_member(
        landing.org,
        "bare-member@example.test",
        OrganizationRoles.MEMBER,
        Level.MEMBER,
    )
    assert not user.can_access_workspace(landing.default)
    assert resolve(landing, entry, user) is None
    assert not WorkspaceMembership._base_manager.filter(user=user).exists()


def test_every_choice_satisfies_can_access_workspace(landing):
    from accounts.services.workspace_landing import resolve_user_workspace

    org = landing.org
    extra = Workspace.objects.create(
        name="Extra", organization=org, created_by=landing.owner
    )
    retired = Workspace.objects.create(
        name="Retired", organization=org, created_by=landing.owner, is_active=False
    )
    foreign_org = Organization.objects.create(name="Foreign organization")
    foreign = Workspace.objects.create(
        name="Foreign", organization=foreign_org, created_by=landing.owner
    )
    owner, _ = org_member(
        org, "table-owner@example.test", OrganizationRoles.OWNER, Level.OWNER
    )
    admin, _ = org_member(
        org, "table-admin@example.test", OrganizationRoles.ADMIN, Level.ADMIN
    )
    bare, _ = org_member(
        org, "table-member@example.test", OrganizationRoles.MEMBER, Level.MEMBER
    )
    viewer, viewer_om = org_member(
        org,
        "table-viewer@example.test",
        OrganizationRoles.MEMBER_VIEW_ONLY,
        Level.VIEWER,
    )
    removed, removed_om = org_member(
        org,
        "table-removed@example.test",
        OrganizationRoles.MEMBER,
        Level.MEMBER,
        is_active=False,
    )
    for member, om, workspace in (
        (viewer, viewer_om, extra),
        (removed, removed_om, landing.other),
    ):
        # A removed org member can keep stale active workspace rows.
        WorkspaceMembership.no_workspace_objects.create(
            user=member,
            workspace=workspace,
            organization_membership=om,
            role=OrganizationRoles.WORKSPACE_MEMBER,
        )
    users = [landing.user, owner, admin, bare, viewer, removed]
    workspaces = [landing.default, landing.other, extra, retired, foreign]
    eligible_in_org = [landing.default, landing.other, extra]
    rows = []
    for user in users:
        for preference in [None, *workspaces]:
            config = (
                {}
                if preference is None
                else {"orgWorkspaceMap": {str(org.id): str(preference.id)}}
            )
            User.objects.filter(pk=user.pk).update(config=config)
            choice = resolve_user_workspace(user, org)
            accessible = [w for w in eligible_in_org if user.can_access_workspace(w)]
            rows.append((user.email, getattr(preference, "name", None), choice))
            if choice is None:
                assert not accessible, rows[-1]
                continue
            assert choice.organization_id == org.id, rows[-1]
            assert choice.is_active and not choice.deleted, rows[-1]
            assert user.can_access_workspace(choice), rows[-1]
            if preference in accessible:
                assert choice == preference, rows[-1]
    landed = {email: choice for email, preference, choice in rows if not preference}
    assert landed[owner.email] == landing.default
    assert landed[admin.email] == landing.default
    assert landed[bare.email] is None
    assert landed[viewer.email] == extra
    assert landed[removed.email] is None


def test_saml_context_honors_configured_default_not_membership_uuid(landing):
    configured = Workspace.objects.create(
        id=UUID(int=20),
        name="SAML configured",
        organization=landing.org,
        created_by=landing.owner,
    )
    WorkspaceMembership.no_workspace_objects.create(
        id=UUID(int=20),
        user=landing.user,
        workspace=configured,
        role=OrganizationRoles.WORKSPACE_ADMIN,
    )
    save_config(landing, {"defaultWorkspaceId": str(configured.id)})
    request = SimpleNamespace(
        headers={},
        GET={},
        method="GET",
        path="/accounts/user/",
        auth_scope=SimpleNamespace(org_id=landing.org.id),
    )
    APIKeyAuthentication()._set_workspace_context(request, landing.user)
    assert request.organization == landing.org
    assert request.workspace == configured


def test_auth_reads_persisted_preferences_not_cached_user(landing):
    User.objects.filter(pk=landing.user.pk).update(
        config={"currentWorkspaceId": str(landing.default.id)}
    )
    landing.user.config = {"currentWorkspaceId": str(landing.other.id)}
    assert resolve(landing, "auth") == landing.default


@pytest.mark.parametrize("stale", [False, True])
def test_user_response_uses_access_checked_landing(landing, stale):
    if stale:
        landing.other_mem.is_active = False
        landing.other_mem.save(update_fields=["is_active"])
        save_config(landing, {"currentWorkspaceId": str(landing.other.id)})
    request = APIRequestFactory().get("/accounts/user/")
    request.organization = landing.org
    force_authenticate(request, user=landing.user)
    with patch("accounts.views.user.get_user_checks", return_value={}):
        response = get_user_info(request)
    assert response.status_code == 200
    assert response.data["default_workspace_id"] == str(landing.default.id)


def test_invite_seeds_default_after_memberships_exist(landing):
    request = APIRequestFactory().post(
        "/accounts/workspace/invite/",
        {
            "emails": ["invitee@example.test"],
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "workspace_ids": [str(landing.other.id), str(landing.default.id)],
        },
        format="json",
    )
    request.organization = landing.org
    force_authenticate(request, user=landing.owner)
    with patch("accounts.views.workspace_management.email_helper"):
        response = WorkspaceInviteAPIView.as_view()(request)
    assert response.status_code == 200, response.data
    invited = User.objects.get(email="invitee@example.test")
    assert invited.config["defaultWorkspaceId"] == str(landing.default.id)
    assert (
        WorkspaceMembership.no_workspace_objects.filter(
            user=invited, is_active=True
        ).count()
        == 2
    )


def context_workspace(landing, entry):
    if entry == "falcon":
        from ee.falcon_ai.consumers import FalconAIConsumer

        consumer = FalconAIConsumer()
        consumer.user = landing.user
        consumer.organization = landing.org
        consumer.organization_id = landing.org.id
        consumer.workspace_id = None
        return FalconAIConsumer.__dict__["_get_workspace"].func(consumer)
    if entry == "mcp_key":
        from accounts.models.user import OrgApiKey
        from mcp_server.mcp_app import _authenticate_and_set_context

        key = OrgApiKey.no_workspace_objects.create(
            user=landing.user,
            organization=landing.org,
            type="user",
            name="Landing",
        )
        return _authenticate_and_set_context(key.api_key, key.secret_key).workspace
    from mcp_server.mcp_app import _authenticate_via_oauth

    with patch(
        "mcp_server.oauth_utils.decrypt_oauth_token",
        return_value={
            "type": "mcp_oauth",
            "user_id": str(landing.user.id),
        },
    ):
        return _authenticate_via_oauth("synthetic").workspace


@pytest.mark.parametrize(
    "entry",
    [pytest.param("falcon", marks=pytest.mark.requires_ee), "mcp_key", "mcp_oauth"],
)
def test_other_user_contexts_use_saved_landing(landing, entry):
    save_config(landing, {"currentWorkspaceId": str(landing.other.id)})
    assert context_workspace(landing, entry) == landing.other


@pytest.mark.parametrize(
    "entry",
    [pytest.param("falcon", marks=pytest.mark.requires_ee), "mcp_key", "mcp_oauth"],
)
def test_other_user_contexts_reject_unjoined_default(landing, entry):
    landing.default_mem.is_active = False
    landing.default_mem.save(update_fields=["is_active"])
    save_config(landing, {"defaultWorkspaceId": str(landing.default.id)})
    # The saved-preference test sets its own config; this check exercises the
    # no-preference fallback through the same public context entry points.
    assert context_workspace(landing, entry) == landing.other
