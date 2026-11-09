"""Ownership regressions use synthetic rows and a completely mocked gateway."""

import ipaddress
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge


@pytest.fixture(autouse=True)
def ownership_network_guard(monkeypatch):
    """Fail closed before DNS/connect; only local test datastores may connect."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_resolve = socket.getaddrinfo

    def check(host):
        if host == "localhost":
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise AssertionError("ownership tests forbid non-loopback network access")

    def connect(sock, address):
        if sock.family != socket.AF_UNIX:
            check(address[0])
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family != socket.AF_UNIX:
            check(address[0])
        return original_connect_ex(sock, address)

    def resolve(host, *args, **kwargs):
        check(host)
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)


@pytest.fixture
def gateway(monkeypatch):
    client = MagicMock()
    client.create_key.return_value = {
        "id": "ownership-created",
        "key": "sk-agentcc-" + "a" * 48,
        "models": [],
        "providers": [],
    }
    client.list_keys.return_value = {"data": []}
    monkeypatch.setattr(auth_bridge, "get_gateway_client", lambda: client)
    return client


@pytest.fixture
def ownership_case(user, workspace, gateway):
    other_org = Organization.objects.create(name="Ownership other org")
    other_user = User.objects.create_user(
        email=f"owner-{uuid4().hex}@example.invalid",
        password=None,
        name="Other person",
        organization=other_org,
    )
    other_workspace = Workspace.no_workspace_objects.create(
        organization=other_org, name="Other workspace", created_by=other_user
    )
    key = AgentccAPIKey.no_workspace_objects.create(
        user=user,
        organization=user.organization,
        workspace=workspace,
        gateway_key_id="ownership-existing",
        name="Original",
        key_hash="b" * 64,
    )
    snapshot = (key.user_id, key.organization_id, key.workspace_id, key.gateway_key_id)

    def unchanged():
        key.refresh_from_db()
        assert (
            key.user_id,
            key.organization_id,
            key.workspace_id,
            key.gateway_key_id,
        ) == snapshot
        assert key.name == "Original"

    return SimpleNamespace(
        key=key,
        user=user,
        other={
            "user": other_user,
            "organization": other_org,
            "workspace": other_workspace,
        },
        unchanged=unchanged,
    )
