"""Regression tests for OAuth bearer tenant-claim validation (issue #1139).

A bearer token authenticates as the organization and workspace named in the
token, not as whatever organization the user currently belongs to.
"""

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from mcp_server.oauth_utils import generate_oauth_token


def _token(user, organization, workspace=None, expires_in=3600):
    token, _ = generate_oauth_token(
        user_id=user.id,
        org_id=organization.id,
        workspace_id=workspace.id if workspace else None,
        client_id="test-client",
        scope=["evaluations"],
        expires_in=expires_in,
    )
    return token


def _authenticate(token):
    from mcp_server.mcp_app import _authenticate_via_oauth

    return _authenticate_via_oauth(token)


@pytest.mark.django_db
class TestOAuthBearerTenantClaims:
    def test_token_for_current_org_and_workspace_authenticates(self, user, workspace):
        ctx = _authenticate(_token(user, user.organization, workspace))

        assert ctx is not None
        assert ctx.user.id == user.id
        assert ctx.organization.id == user.organization_id
        assert ctx.workspace.id == workspace.id

    def test_token_without_workspace_claim_uses_default(self, user, workspace):
        ctx = _authenticate(_token(user, user.organization))

        assert ctx is not None
        assert ctx.organization.id == user.organization_id
        assert ctx.workspace.id == workspace.id

    def test_token_stays_in_its_org_after_user_moves(self, user, workspace):
        """The token must not follow ``user.organization`` to a new tenant."""
        token = _token(user, user.organization, workspace)

        other = Organization.objects.create(name="Other Organization")
        user.organization = other
        user.save(update_fields=["organization"])

        ctx = _authenticate(token)

        assert ctx is not None
        assert ctx.organization.id != other.id
        assert ctx.workspace.id == workspace.id

    def test_token_is_rejected_after_membership_is_revoked(self, user, workspace):
        token = _token(user, user.organization, workspace)

        membership = OrganizationMembership.no_workspace_objects.get(
            user=user, organization=user.organization
        )
        membership.is_active = False
        membership.save(update_fields=["is_active"])

        assert _authenticate(token) is None

    def test_token_for_org_without_membership_is_rejected(self, user, workspace):
        other = Organization.objects.create(name="Other Organization")

        assert _authenticate(_token(user, other)) is None

    def test_token_for_invited_org_authenticates(self, user, workspace):
        other = Organization.objects.create(name="Invited Organization")
        OrganizationMembership.no_workspace_objects.create(
            user=user, organization=other, is_active=True
        )
        other_workspace = Workspace.objects.create(
            name="Invited Workspace",
            organization=other,
            is_default=True,
            is_active=True,
            created_by=user,
        )

        ctx = _authenticate(_token(user, other, other_workspace))

        assert ctx is not None
        assert ctx.organization.id == other.id
        assert ctx.workspace.id == other_workspace.id

    def test_inactive_workspace_claim_is_rejected(self, user, workspace):
        token = _token(user, user.organization, workspace)

        workspace.is_active = False
        workspace.save(update_fields=["is_active"])

        assert _authenticate(token) is None

    def test_deleted_workspace_claim_is_rejected(self, user, workspace):
        token = _token(user, user.organization, workspace)

        workspace.delete()

        assert _authenticate(token) is None

    def test_workspace_from_another_org_is_rejected(self, user, workspace):
        other = Organization.objects.create(name="Other Organization")
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=other,
            is_default=True,
            is_active=True,
            created_by=user,
        )

        assert _authenticate(_token(user, user.organization, other_workspace)) is None
