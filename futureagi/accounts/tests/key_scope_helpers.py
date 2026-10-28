"""Real-authentication fixtures for API-key role contracts."""

from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models import OrgApiKey, Organization, User
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

ROLES = [Level.OWNER, Level.ADMIN, Level.MEMBER, Level.VIEWER]


def session_client(user, organization):
    token = AuthToken.objects.create(
        user=user, auth_type=AuthTokenType.ACCESS, last_used_at=timezone.now()
    )
    bearer = generate_encrypted_message({"id": str(token.id), "user_id": str(user.id)})
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {bearer}",
        HTTP_X_ORGANIZATION_ID=str(organization.id),
    )
    return client


def key_client(key, **headers):
    client = APIClient()
    client.credentials(
        HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key, **headers
    )
    return client


def make_key(org, user=None, key_type="user", **kwargs):
    clear_workspace_context()
    return OrgApiKey.no_workspace_objects.create(
        organization=org, user=user, type=key_type, **kwargs
    )


@pytest.fixture
def key_scope(db, monkeypatch):
    monkeypatch.setattr(
        "accounts.views.keys.track_mixpanel_event", lambda *a, **k: None
    )
    monkeypatch.setattr("accounts.views.keys._publish_key_revocation", lambda *a: None)
    tenants = []
    for name in ("Alpha", "Beta"):
        clear_workspace_context()
        org = Organization.objects.create(name=name)
        users = {}
        for level in ROLES:
            user = User.objects.create_user(
                email=f"keys-{name}-{level}@example.test",
                password="test-only-password",
                name=f"{name} {Level.to_org_string(level)}",
                organization=org,
                organization_role=Level.to_org_string(level),
            )
            OrganizationMembership.no_workspace_objects.update_or_create(
                user=user,
                organization=org,
                defaults={
                    "role": Level.to_org_string(level),
                    "level": level,
                    "is_active": True,
                },
            )
            users[level] = user
        workspace = Workspace.no_workspace_objects.create(
            name=f"{name} workspace",
            organization=org,
            is_default=True,
            created_by=users[Level.OWNER],
        )
        for level, user in users.items():
            WorkspaceMembership.no_workspace_objects.update_or_create(
                user=user,
                workspace=workspace,
                defaults={
                    "role": Level.to_ws_role(level),
                    "level": min(level, Level.ADMIN),
                    "is_active": True,
                    "organization_membership": user.get_membership(org),
                },
            )
        tenants.append(SimpleNamespace(org=org, users=users, workspace=workspace))
    yield tenants
    clear_workspace_context()
