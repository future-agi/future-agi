"""Tests for OAuth bearer token authentication in mcp_app._authenticate_via_oauth.

Covers the security fix for org_id claim validation (issue #1164-related bearer auth fix):
- org mismatch after user moves organizations
- inactive/deleted workspace fails closed
- cross-org workspace rebinding blocked
"""

import uuid
from datetime import timedelta

import pytest

from mcp_server.oauth_utils import generate_oauth_token


def _make_token(user_id, org_id, workspace_id=None, expires_in=3600):
    token, _ = generate_oauth_token(
        user_id=user_id,
        org_id=org_id,
        workspace_id=workspace_id,
        client_id="test-client",
        scope=["evaluations"],
        expires_in=expires_in,
    )
    return token


@pytest.mark.django_db
class TestOAuthBearerAuth:

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_valid_token_authenticates(self, user, workspace):
        from mcp_server.mcp_app import _authenticate_via_oauth

        token = _make_token(user.id, user.organization.id, workspace.id)
        ctx = _authenticate_via_oauth(token)

        assert ctx is not None
        assert ctx.user.id == user.id
        assert ctx.organization.id == user.organization.id
        assert ctx.workspace.id == workspace.id

    def test_no_workspace_claim_falls_back_to_default(self, user, workspace):
        """Token with no workspace_id uses the org's default workspace."""
        from mcp_server.mcp_app import _authenticate_via_oauth

        token = _make_token(user.id, user.organization.id, workspace_id=None)
        ctx = _authenticate_via_oauth(token)

        assert ctx is not None
        assert ctx.workspace.id == workspace.id

    # ------------------------------------------------------------------
    # Security: org mismatch
    # ------------------------------------------------------------------

    def test_user_moved_to_different_org_rejects_token(self, user, workspace):
        """Core attack scenario: token minted for org_a is rejected after user moves to org_b."""
        from accounts.models.organization import Organization
        from mcp_server.mcp_app import _authenticate_via_oauth

        token = _make_token(user.id, user.organization.id, workspace.id)

        org_b = Organization.objects.create(name="Org B")
        user.organization = org_b
        user.save()

        ctx = _authenticate_via_oauth(token)
        assert ctx is None

    def test_token_org_id_does_not_match_user_org_rejects(self, user, workspace):
        """Token carrying a different org_id than user's current org is rejected."""
        from accounts.models.organization import Organization
        from mcp_server.mcp_app import _authenticate_via_oauth

        org_b = Organization.objects.create(name="Org B")
        token = _make_token(user.id, org_b.id, workspace.id)

        ctx = _authenticate_via_oauth(token)
        assert ctx is None

    # ------------------------------------------------------------------
    # Security: workspace fail-closed
    # ------------------------------------------------------------------

    def test_inactive_workspace_rejects_token(self, user, workspace):
        """Inactive workspace is rejected — no silent fallback to default."""
        from mcp_server.mcp_app import _authenticate_via_oauth

        token = _make_token(user.id, user.organization.id, workspace.id)

        workspace.is_active = False
        workspace.save()

        ctx = _authenticate_via_oauth(token)
        assert ctx is None

    def test_deleted_workspace_rejects_token(self, user, workspace):
        """Deleted workspace is rejected — no silent fallback to default."""
        from mcp_server.mcp_app import _authenticate_via_oauth

        token = _make_token(user.id, user.organization.id, workspace.id)
        workspace.delete()

        ctx = _authenticate_via_oauth(token)
        assert ctx is None

    def test_workspace_belonging_to_different_org_rejects(self, user, workspace):
        """Workspace from a different org in the token is rejected."""
        from accounts.models.organization import Organization
        from accounts.models.workspace import Workspace
        from mcp_server.mcp_app import _authenticate_via_oauth

        org_b = Organization.objects.create(name="Org B")
        other_ws = Workspace.objects.create(
            organization=org_b,
            name="WS B",
            is_default=True,
            is_active=True,
        )

        # Token claims user's real org but a workspace that belongs to org_b
        token = _make_token(user.id, user.organization.id, other_ws.id)
        ctx = _authenticate_via_oauth(token)
        assert ctx is None

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_nonexistent_user_rejects_token(self, user, workspace):
        from mcp_server.mcp_app import _authenticate_via_oauth

        token = _make_token(uuid.uuid4(), user.organization.id, workspace.id)
        ctx = _authenticate_via_oauth(token)
        assert ctx is None

    def test_expired_token_rejects(self, user, workspace):
        from mcp_server.mcp_app import _authenticate_via_oauth

        token = _make_token(user.id, user.organization.id, workspace.id, expires_in=-1)
        ctx = _authenticate_via_oauth(token)
        assert ctx is None

    def test_tampered_token_rejects(self):
        from mcp_server.mcp_app import _authenticate_via_oauth

        ctx = _authenticate_via_oauth("not-a-real-token")
        assert ctx is None

    def test_empty_token_rejects(self):
        from mcp_server.mcp_app import _authenticate_via_oauth

        ctx = _authenticate_via_oauth("")
        assert ctx is None