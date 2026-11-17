"""Real-auth requests and side-effect observations for workspace write tests."""

import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.apps import apps
from django.conf import settings
from django.test import override_settings
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.middleware.workspace_context import clear_workspace_context


@pytest.fixture
def scope_no_egress(monkeypatch):
    """Only the configured test DB and loopback may open sockets; no providers."""
    allowed = {"localhost", "127.0.0.1", "::1"}
    allowed.update(
        db.get("HOST") for db in settings.DATABASES.values() if db.get("HOST")
    )
    original_lookup = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    addresses = set(allowed)
    for host in allowed:
        if str(host).startswith("/"):
            continue
        for row in original_lookup(host, None):
            addresses.add(row[4][0])

    def lookup(host, *args, **kwargs):
        if host not in allowed:
            raise AssertionError("Outbound DNS forbidden in scope regression tests")
        return original_lookup(host, *args, **kwargs)

    def connect(sock, address):
        if sock.family != socket.AF_UNIX and address[0] not in addresses:
            raise AssertionError("Outbound sockets forbidden in scope regression tests")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family != socket.AF_UNIX and address[0] not in addresses:
            raise AssertionError("Outbound sockets forbidden in scope regression tests")
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture
def write_effects(scope_no_egress, monkeypatch):
    """Permit quota in RED without contacting billing; observe every attempted call."""
    from model_hub.views import develop_dataset
    from model_hub.views.datasets.create import blank_grid

    quota = Mock(return_value=SimpleNamespace(status="success", save=Mock()))
    create = Mock(wraps=blank_grid.create_blank_grid)
    monkeypatch.setattr(
        develop_dataset, "log_and_deduct_cost_for_resource_request", quota
    )
    monkeypatch.setattr(blank_grid, "create_blank_grid", create)
    with override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    ):
        yield SimpleNamespace(quota=quota, create=create)
    clear_workspace_context()


def credential_headers(user, organization, credential="session"):
    clear_workspace_context()
    if credential == "key":
        key = OrgApiKey.no_workspace_objects.create(
            name="Scope regression", user=user, organization=organization, type="user"
        )
        return {"HTTP_X_API_KEY": key.api_key, "HTTP_X_SECRET_KEY": key.secret_key}
    token = encrypted_token(user, AuthTokenType.ACCESS.value)
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def encrypted_token(user, auth_type):
    row = AuthToken.objects.create(
        user=user, auth_type=auth_type, is_active=True, last_used_at=timezone.now()
    )
    return generate_encrypted_message({"user_id": str(user.pk), "id": str(row.pk)})


def request_view(path, headers, data=None, *, method="post"):
    """Resolve the real handler/markers and authenticate; never force_authenticate."""
    clear_workspace_context()
    request = getattr(APIRequestFactory(), method)(
        path, data or {}, format="json", HTTP_HOST="localhost", **headers
    )
    match = resolve(path)
    request.resolver_match = match
    try:
        return match.func(request, *match.args, **match.kwargs)
    finally:
        clear_workspace_context()


def membership_snapshot():
    return {
        model.__name__: list(model._base_manager.order_by("pk").values())
        for model in (OrganizationMembership, WorkspaceMembership)
    }


def write_snapshot():
    # Raw base managers include NULL-workspace and soft-deleted rows. Never use
    # an empty scoped queryset as evidence that an INSERT did not happen.
    models = [Dataset, Column, Row, Cell]
    models.extend(
        model
        for model in apps.get_models()
        if model.__module__.startswith("ee.usage.models")
    )
    return {
        model._meta.label: list(model._base_manager.order_by("pk").values())
        for model in models
    }
