"""Direct invariants of the shared, read-only workspace landing policy."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.workspace import WorkspaceMembership
from accounts.services.workspace_landing import resolve_user_workspace
from accounts.tests.test_workspace_landing import landing as _landing_fixture
from accounts.tests.test_workspace_landing import save_config
from accounts.views.workspace_management import WorkspaceInviteAPIView
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context

landing = _landing_fixture


@pytest.mark.parametrize("config", [None, [], "legacy", {"orgWorkspaceMap": []}])
def test_malformed_config_does_not_break_landing(landing, config):
    # JSONField is non-null; a legacy null can occur on the cached object, but
    # the persisted config remains the source of truth.
    if config is None:
        landing.user.config = None
    else:
        save_config(landing, config)
    assert resolve_user_workspace(landing.user, landing.org) == landing.default


def test_no_memberships_returns_none_without_writes(landing):
    WorkspaceMembership.no_workspace_objects.filter(user=landing.user).delete()
    assert resolve_user_workspace(landing.user, landing.org) is None
    assert not WorkspaceMembership.no_workspace_objects.filter(
        user=landing.user
    ).exists()


def test_deleted_workspace_is_not_a_candidate(landing):
    landing.default.delete()
    save_config(landing, {"defaultWorkspaceId": str(landing.default.id)})
    assert resolve_user_workspace(landing.user, landing.org) == landing.other


def test_current_context_cannot_hide_other_memberships(landing):
    set_workspace_context(workspace=landing.other, organization=landing.org)
    assert resolve_user_workspace(landing.user, landing.org) == landing.default


def test_stale_saved_preference_falls_back_to_configured_default(landing):
    save_config(
        landing,
        {
            "orgWorkspaceMap": {
                str(landing.org.id): "00000000-0000-0000-0000-000000000099"
            },
            "defaultWorkspaceId": str(landing.other.id),
        },
    )
    assert resolve_user_workspace(landing.user, landing.org) == landing.other


def test_invite_calls_shared_resolver_after_membership_creation(landing):
    request = APIRequestFactory().post(
        "/accounts/workspace/invite/",
        {
            "emails": ["resolver-invite@example.test"],
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "workspace_ids": [str(landing.other.id), str(landing.default.id)],
        },
        format="json",
    )
    request.organization = landing.org
    force_authenticate(request, user=landing.owner)
    with (
        patch(
            "accounts.views.workspace_management.resolve_user_workspace",
            wraps=resolve_user_workspace,
        ) as resolver,
        patch("accounts.views.workspace_management.email_helper"),
    ):
        response = WorkspaceInviteAPIView.as_view()(request)
    assert response.status_code == 200, response.data
    resolver.assert_called_once()
    invited, organization = resolver.call_args.args
    assert organization == landing.org
    assert (
        WorkspaceMembership.no_workspace_objects.filter(
            user=invited, is_active=True
        ).count()
        == 2
    )
