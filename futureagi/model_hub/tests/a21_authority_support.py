"""Synthetic routed authority fixtures. PostgreSQL VM ONLY; never local collection.

The authorized VM runner must install its native socket/DNS egress guard BEFORE
Python/Django imports (libpq bypasses Python sockets), and supply synthetic-only
settings, including encryption keys. The Python guard is defense in depth, not
a substitute for that preimport guard. No credentials are minted during import.
"""

import secrets
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection
from rest_framework.test import APIClient

from accounts.authentication import APIKeyAuthentication, generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    get_current_user,
    get_current_workspace,
    workspace_scope_is_empty,
)


@pytest.fixture
def authority_environment(settings, monkeypatch, transactional_db):
    assert getattr(settings, "A21_NATIVE_EGRESS_GUARD", False) is True
    assert settings.ROOT_URLCONF == "tfc.urls"
    assert connection.vendor == "postgresql"
    assert str(connection.settings_dict["NAME"]).startswith("test_")
    assert settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] == (
        "accounts.authentication.APIKeyAuthentication",
    )
    settings.SECRET_KEY = secrets.token_urlsafe(48)
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    chain = list(settings.MIDDLEWARE)
    for name in (
        "accounts.authentication.AuthMonitoringMiddleware",
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
    ):
        if name not in chain:
            chain.append(name)
    settings.MIDDLEWARE = chain
    settings.ALLOWED_HOSTS = ["testserver", "localhost"]
    allowed = set()
    for config in settings.DATABASES.values():
        assert config["ENGINE"] == "django.db.backends.postgresql"
        host = config.get("HOST") or "localhost"
        assert host in {"localhost", "127.0.0.1", "::1"} or host.startswith("/")
        port = int(config.get("PORT") or 5432)
        allowed.update((h, port) for h in (host, "localhost", "127.0.0.1", "::1"))
    attempts = []
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_lookup = socket.getaddrinfo

    def check(sock, address):
        if sock.family == socket.AF_UNIX:
            permitted = any(
                isinstance(address, str) and address.endswith(f"/.s.PGSQL.{port}")
                for _, port in allowed
            )
        else:
            permitted = tuple(address[:2]) in allowed
        if not permitted:
            attempts.append("blocked socket")
            raise AssertionError("Only the isolated PostgreSQL socket is permitted")

    def connect(sock, address):
        check(sock, address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check(sock, address)
        return original_connect_ex(sock, address)

    def lookup(host, *args, **kwargs):
        if host not in {"localhost", "127.0.0.1", "::1"}:
            attempts.append("blocked DNS")
            raise AssertionError("External DNS is forbidden")
        return original_lookup(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    clear_workspace_context()
    yield
    clear_workspace_context()
    assert not attempts, "An outward call was attempted, even if caught"


def make_actor(org, label):
    user = User.objects.create_user(
        email=f"{label}-{uuid4().hex}@example.invalid",
        name=f"Synthetic {label}",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
        is_active=True,
        config={},
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    return user, membership


@pytest.fixture
def authority_scope(authority_environment, transactional_db):
    clear_workspace_context()
    org = Organization.objects.create(name="Synthetic authority org")
    foreign_org = Organization.objects.create(name="Synthetic foreign authority org")
    user, membership = make_actor(org, "member")
    creator, _ = make_actor(org, "creator")
    spaces = {
        name: Workspace.no_workspace_objects.create(
            name=f"Synthetic {name}",
            organization=foreign_org if name == "foreign" else org,
            created_by=creator,
            is_default=name == "default",
            is_active=True,
        )
        for name in ("a", "b", "default", "foreign")
    }
    assert not WorkspaceMembership._base_manager.filter(user=user).exists()
    return SimpleNamespace(
        org=org,
        foreign_org=foreign_org,
        user=user,
        membership=membership,
        creator=creator,
        **spaces,
    )


def grant(data, workspace, *, viewer=False):
    return WorkspaceMembership.no_workspace_objects.create(
        user=data.user,
        workspace=workspace,
        organization_membership=data.membership,
        role=OrganizationRoles.WORKSPACE_VIEWER
        if viewer
        else OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_VIEWER if viewer else Level.WORKSPACE_MEMBER,
        is_active=True,
    )


def session_client(data):
    from django.utils import timezone

    token = AuthToken.objects.create(
        user=data.user,
        auth_type=AuthTokenType.ACCESS,
        is_active=True,
        last_used_at=timezone.now(),
    )
    bearer = generate_encrypted_message(
        {"user_id": str(data.user.pk), "id": str(token.pk)}
    )
    client = APIClient(raise_request_exception=False)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {bearer}")
    return client


def post(client, path, body, workspace):
    response = client.post(
        path,
        body,
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.pk),
    )
    assert get_current_workspace() is None
    assert get_current_organization() is None
    assert get_current_user() is None
    assert not workspace_scope_is_empty()
    return response


def assert_admitted(response, data, workspace, view_name):
    request = response.renderer_context["request"]
    assert type(request.successful_authenticator) is APIKeyAuthentication
    assert request.credential_provenance == "interactive_session"
    assert request.user.pk == data.user.pk
    assert request.organization.pk == data.org.pk
    assert request.workspace.pk == workspace.pk
    assert request.resolver_match.func.cls.__name__ == view_name
    assert request._request is response.wsgi_request


def snapshot(*models):
    """Full native rows, including timestamps, soft-deleted rows and through rows."""
    return {
        model._meta.label: list(model._base_manager.order_by("pk").values())
        for model in models
    }
