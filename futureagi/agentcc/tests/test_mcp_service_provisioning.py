"""CN22a: the virtual-server provisioning API for Omega EE, agent mode.

Hosted Omega EE attaches a connection to a gateway virtual server: it mints a
gateway key for a workspace, ensures one virtual server per connection, and
detaches it when a person disconnects (``omega-ee-attach/v1``,
``docs/gateway/connectors/omega-ee-attach.md`` section 3). Only EE's own
service credential may call it (AM-C18 Q1). The answers are checked against
the contract's golden fixtures and schema.

The routes are not in ``agentcc/urls.py`` yet (an integrator include), so
these tests mount them under ``/agentcc/`` ahead of the app's own routes. The
gateway is faked at its admin client. Every key, secret and token here is
generated at run time.
"""

import copy
import hashlib
import json
import secrets
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.urls import include, path, resolve
from django.utils import timezone
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from structlog.testing import capture_logs

from accounts.authentication import generate_encrypted_message
from accounts.models import Organization, OrgApiKey, User
from accounts.models.audit_log import AuditLog
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models import AgentccAPIKey, AgentccOrgConfig
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_service_provisioning import (
    AgentccEEAgentKey,
    AgentccEEServiceCredential,
    AgentccEEVirtualServer,
)
from agentcc.services import auth_bridge
from agentcc.services import mcp_service_provisioning as provisioning
from agentcc.services.credential_manager import encrypt_token
from agentcc.services.gateway_client import GatewayClientError
from agentcc.services.org_config_writer import write_config_version
from agentcc.views import mcp_service_provisioning as views
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

urlpatterns = [
    path("agentcc/", include("agentcc.urls_mcp_service_provisioning")),
    path("", include("tfc.urls")),
]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db,
    pytest.mark.urls(__name__),
]

# 733 addendum 4: CN22, the EE agent-mode broker, is OFF at launch
# (KI-733-EE-RELAY), and the ownerless agent keys it mints cannot be issued
# under F2's person-owned gateway keys. EE kinds are out of L4's scope. The OFF
# refusal itself is pinned by test_gateway_retirement_command_733; the broker's
# own tests wait for its post-launch, person-delegated successor.
CN22_OFF_733 = pytest.mark.skip(
    reason="733 add.4: CN22 is OFF at launch (KI-733-EE-RELAY); "
    "its OFF refusal is pinned by test_gateway_retirement_command_733"
)

FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "agentcc-gateway/scripts/scenarios/fixtures/omega-ee-attach"
)
SCHEMA = json.loads((FIXTURES / "schema.json").read_text())
SPEC = (
    Path(__file__).resolve().parents[3] / "docs/gateway/connectors/omega-ee-attach.md"
)
REGISTRY = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))

ENSURE = "/agentcc/mcp/service/virtual-servers/ensure/"
KEYS = "/agentcc/mcp/service/agent-keys/"
CREDENTIAL = "/agentcc/mcp/service/credential/"
ROTATE = CREDENTIAL + "rotate/"
REVOKE_CREDENTIAL = CREDENTIAL + "revoke/"
PUBLIC_GATEWAY = "https://gateway.example.com"

SERVERS = {
    "github": {"url": "https://mcp.github.example.com/mcp", "transport": "http"},
    "sentry": {
        "url": "https://mcp.sentry.example.com/mcp",
        "transport": "http",
        "credential": {"mode": "shared"},
    },
    "linear": {
        "url": "https://mcp.linear.example.com/mcp",
        "transport": "http",
        "credential": {"mode": "per_user"},
    },
    "notion": {
        "url": "https://mcp.notion.example.com/mcp",
        "transport": "http",
        "enabled": False,
    },
}


def _detach_url(vs_id):
    return f"/agentcc/mcp/service/virtual-servers/{vs_id}/"


def _revoke_url(agent_key_id):
    return f"{KEYS}{agent_key_id}/revoke/"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _validator(definition):
    return Draft202012Validator(
        {"$ref": f"{SCHEMA['$id']}#/$defs/{definition}"}, registry=REGISTRY
    )


def _valid(definition, document):
    errors = sorted(_validator(definition).iter_errors(document), key=str)
    assert not errors, [e.message for e in errors]


def _document(response, **substitute):
    """The answer as the contract's fixtures write one: http and body."""
    headers = {"content-type": response["Content-Type"]}
    if response.has_header("Cache-Control"):
        headers["cache-control"] = response["Cache-Control"]
    body = copy.deepcopy(response.json())
    for name, value in substitute.items():
        body["result"][name] = value
    return {"http": {"status": response.status_code, "headers": headers}, "body": body}


class FakeGateway:
    """The gateway's admin API: keys, revocations and org-config pushes."""

    def __init__(self):
        self.keys = {}
        self.created = []
        self.revoked = []
        self.pushes = []
        self.down = False
        self.push_fails = False
        self.revoke_status = None

    def create_key(self, name, owner="", models=None, providers=None, metadata=None):
        if self.down:
            raise GatewayClientError("connection refused")
        key_id = "key_" + secrets.token_hex(12)
        raw = "sk-agentcc-" + secrets.token_urlsafe(24)
        self.keys[key_id] = raw
        self.created.append(
            {"name": name, "owner": owner, "models": models, "providers": providers}
        )
        return {"id": key_id, "key": raw, "key_prefix": raw[:14]}

    def revoke_key(self, key_id):
        self.revoked.append(key_id)
        if self.revoke_status is not None:
            raise GatewayClientError("revoke failed", status_code=self.revoke_status)
        return {}

    def set_org_config(self, org_id, payload):
        if self.push_fails:
            raise GatewayClientError("connection refused", status_code=503)
        self.pushes.append((org_id, copy.deepcopy(payload)))
        return {}

    def pushed_virtual_servers(self):
        return self.pushes[-1][1]["mcp"].get("virtual_servers") or {}


@pytest.fixture
def gateway(monkeypatch, settings):
    settings.EE_SERVICE_PROVISIONING_ENABLED = True
    fake = FakeGateway()
    for target in (
        "agentcc.services.auth_bridge.get_gateway_client",
        "agentcc.services.config_push.get_gateway_client",
        "agentcc.services.mcp_service_provisioning.get_gateway_client",
    ):
        monkeypatch.setattr(target, lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _gateway_url(monkeypatch, settings):
    # Exercise the dormant compatibility contract independently of default-OFF tests.
    settings.EE_SERVICE_PROVISIONING_ENABLED = True
    monkeypatch.setenv(provisioning.PUBLIC_URL_ENV, PUBLIC_GATEWAY)
    monkeypatch.delenv(provisioning.FALLBACK_URL_ENV, raising=False)
    monkeypatch.delenv(provisioning.MCP_ENDPOINT_ENV, raising=False)


def _person(org, workspace, level, role, ws_level, ws_role):
    person = User.objects.create_user(
        email=f"cn22-{secrets.token_hex(4)}@futureagi.com",
        password=secrets.token_urlsafe(16),
        name="CN22 Person",
        organization=org,
        organization_role=role,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=person, organization=org, role=role, level=level, is_active=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=person,
        workspace=workspace,
        role=ws_role,
        level=ws_level,
        organization_membership=membership,
        is_active=True,
    )
    return person


def _org(name="CN22 Org"):
    """An org, its owner and its default workspace."""
    org = Organization.objects.create(name=name)
    owner = User.objects.create_user(
        email=f"cn22-owner-{secrets.token_hex(4)}@futureagi.com",
        password=secrets.token_urlsafe(16),
        name="CN22 Owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=owner,
        organization=org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    workspace = Workspace.no_workspace_objects.create(
        name="Engineering",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=owner,
        workspace=workspace,
        role="Workspace Owner",
        level=Level.OWNER,
        organization_membership=membership,
        is_active=True,
    )
    return org, owner, workspace


def _workspace(org, owner, name):
    return Workspace.no_workspace_objects.create(
        name=name, organization=org, is_active=True, created_by=owner
    )


def _member(org, workspace):
    return _person(
        org,
        workspace,
        Level.MEMBER,
        OrganizationRoles.MEMBER,
        Level.WORKSPACE_MEMBER,
        OrganizationRoles.WORKSPACE_MEMBER,
    )


def _seed(org, *, enabled=True, servers=None):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        mcp={"enabled": enabled, "servers": copy.deepcopy(servers or SERVERS)},
    )


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _virtual_servers(org):
    return _active(org).mcp.get("virtual_servers") or {}


def _by_id(org):
    return {entry["id"]: (slug, entry) for slug, entry in _virtual_servers(org).items()}


def _audited(org, action):
    """The audit rows of ``action``, oldest first: each written as EE, never
    as a person, with the fields a reader needs."""
    rows = AuditLog.objects.filter(organization=org, action=action).order_by(
        "created_at"
    )
    out = []
    for row in rows:
        assert row.actor is None and row.metadata["actor"] == "omega-ee"
        out.append(
            {
                k: row.metadata[k]
                for k in ("created", "members", "agent_key_id")
                if k in row.metadata
            }
        )
    return out


def _versions(org):
    return AgentccOrgConfig.no_workspace_objects.filter(organization=org).count()


def _client(api_key, secret, **extra):
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=api_key, HTTP_X_SECRET_KEY=secret, **extra)
    return client


def _ee(org, owner):
    """EE's client, with a service credential an owner issued."""
    issued = provisioning.issue_credential(org, owner)
    return _client(issued.credential.api_key, issued.secret), issued


def _bearer(person):
    """A client signed in as ``person``, with a real access token."""
    access = AuthToken.objects.create(
        user=person,
        auth_type=AuthTokenType.ACCESS.value,
        is_active=True,
        last_used_at=timezone.now(),
    )
    token = generate_encrypted_message(
        {"user_id": str(person.id), "id": str(access.id)}
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def uuid7() -> str:
    """A UUIDv7 (RFC 9562), as EE makes the Idempotency-Key it sends with a
    mint (CN22d): 48 bits of Unix milliseconds, version 7, the RFC variant,
    and 74 random bits."""
    raw = bytearray(
        int(time.time() * 1000).to_bytes(6, "big") + secrets.token_bytes(10)
    )
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def _mint(client, workspace, expect=201, idempotency_key=None):
    """EE's mint, with a new Idempotency-Key unless one is given (CN22d)."""
    response = client.post(
        KEYS,
        {"workspace": str(workspace.id)},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idempotency_key or uuid7(),
    )
    assert response.status_code == expect, response.json()
    return response.json()["result"] if expect == 201 else response.json()


def _ensure(client, workspace, connection_id, servers, agent_key_id, expect=200):
    body = {
        "workspace": str(workspace.id),
        "connection_id": connection_id,
        "servers": servers,
        "mode": "agent",
        "agent_key_id": agent_key_id,
    }
    response = client.post(ENSURE, body, format="json")
    assert response.status_code == expect, response.json()
    return response.json()


def _error(status, code, message, attr=None):
    """Django's envelope, as the contract's fixtures spell it."""
    body = {
        "status": False,
        "type": {
            400: "validation_error",
            401: "authentication_error",
            403: "permission_error",
            404: "not_found",
            503: "service_unavailable",
        }[status],
        "code": code,
        "detail": message,
        "message": message,
        "error": message,
        "result": message,
    }
    if attr:
        body["attr"] = attr
        body["details"] = {attr: [message]}
    return body


NOT_FOUND = _fixture("provision/error-not-found.response.json")["body"]
NOT_EE = _fixture("provision/error-not-ee-credential.response.json")["body"]
AGENT_KEY_INVALID = _fixture("provision/error-agent-key.response.json")["body"]
OTHER_WORKSPACE = _fixture("provision/error-agent-key-other-workspace.response.json")[
    "body"
]


# --- The five acceptance tests ----------------------------------------------


@CN22_OFF_733
def test_ensure_is_idempotent(gateway):
    """Rule 1: one virtual server per (org, workspace, connection); a repeat
    keeps its id and url, pushes again, and changes only members and
    access.keys, so a key rotation keeps what an admin set."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key = _mint(ee, workspace)["agent_key_id"]

    first = _ensure(ee, workspace, "int_4f2a9c1e7b3d", ["github"], key)["result"]
    vs_id = first["vs_id"]
    assert provisioning.VS_ID.fullmatch(vs_id)
    assert first == {
        "vs_id": vs_id,
        "url": f"{PUBLIC_GATEWAY}/mcp/v/{vs_id}",
        "mode": "agent",
        "workspace": str(workspace.id),
        "connection_id": "int_4f2a9c1e7b3d",
        "created": True,
        # CN22c: the bound key's expiry, so EE rotates before it.
        "agent_key_expires_at": first["agent_key_expires_at"],
    }
    assert first["agent_key_expires_at"] is not None
    slug, entry = _by_id(org)[vs_id]
    assert entry["members"] == ["github"]
    assert entry["access"] == {"keys": [key]}
    assert entry["naming"] == "prefixed"
    assert entry["approval"] == {"require": "none"}
    assert "overrides" not in entry
    # The gateway is handed the virtual server exactly as stored.
    assert gateway.pushed_virtual_servers()[slug] == entry

    versions, pushes = _versions(org), len(gateway.pushes)
    repeat = _ensure(ee, workspace, "int_4f2a9c1e7b3d", ["github"], key)["result"]
    assert repeat == {**first, "created": False}
    assert _versions(org) == versions  # nothing changed, so no new version
    assert _audited(org, provisioning.AUDIT_VS_ENSURED) == [
        {"created": True, "members": ["github"], "agent_key_id": key}
    ]
    assert len(gateway.pushes) == pushes + 1  # but the gateway got it again
    assert gateway.pushed_virtual_servers()[slug]["id"] == vs_id

    # An org admin narrows EE's calls on this virtual server.
    rules = [{"match": {"key_ids": [key]}, "deny": ["github__delete_*"]}]
    write_config_version(
        org,
        {
            "mcp": {
                "virtual_servers": {
                    slug: {
                        "access": {"rules": rules},
                        "approval": {"require": "destructive"},
                    }
                }
            }
        },
        user=owner,
    )

    # EE rotates its key and adds a member: same virtual server, same url.
    new_key = _mint(ee, workspace)["agent_key_id"]
    rotated = _ensure(ee, workspace, "int_4f2a9c1e7b3d", ["github", "sentry"], new_key)[
        "result"
    ]
    # The answer names the new key's expiry (CN22c): the rotation moved it.
    assert rotated == {
        **first,
        "created": False,
        "agent_key_expires_at": rotated["agent_key_expires_at"],
    }
    assert rotated["agent_key_expires_at"] > first["agent_key_expires_at"]
    slug_after, entry = _by_id(org)[vs_id]
    assert slug_after == slug
    assert entry["members"] == ["github", "sentry"]
    assert entry["access"] == {"keys": [new_key], "rules": rules}
    assert entry["approval"] == {"require": "destructive"}
    assert len(_virtual_servers(org)) == 1
    assert gateway.pushed_virtual_servers()[slug] == entry
    assert _audited(org, provisioning.AUDIT_VS_ENSURED)[1] == {
        "created": False,
        "members": ["github", "sentry"],
        "agent_key_id": new_key,
    }
    row = AgentccEEVirtualServer.objects.get(organization=org)
    assert (row.vs_id, row.slug, row.agent_key.api_key.gateway_key_id) == (
        vs_id,
        slug,
        new_key,
    )


@CN22_OFF_733
def test_two_workspaces_cannot_see_each_other(gateway):
    """Two workspaces of one org, and another org, stay apart: a key serves
    one workspace (rule 3), each virtual server admits only its own key
    (rule 6), and nothing of another org is found."""
    org, owner, research = _org()
    support = _workspace(org, owner, "Support")
    _seed(org)
    ee, _ = _ee(org, owner)
    research_key = _mint(ee, research)["agent_key_id"]
    support_key = _mint(ee, support)["agent_key_id"]

    assert (
        _ensure(ee, research, "int_shared", ["github"], support_key, expect=400)
        == OTHER_WORKSPACE
    )
    assert not AgentccEEVirtualServer.objects.exists()

    a = _ensure(ee, research, "int_shared", ["github"], research_key)["result"]
    b = _ensure(ee, support, "int_shared", ["github"], support_key)["result"]
    assert a["created"] and b["created"]
    assert a["vs_id"] != b["vs_id"] and a["url"] != b["url"]
    servers = _by_id(org)
    assert servers[a["vs_id"]][1]["access"] == {"keys": [research_key]}
    assert servers[b["vs_id"]][1]["access"] == {"keys": [support_key]}
    for _, entry in servers.values():
        assert entry["access"].get("default_for_org") is None

    # Another org's EE finds none of it, and its keys bind nothing here.
    other, other_owner, other_workspace = _org("CN22 Other Org")
    _seed(other)
    other_ee, _ = _ee(other, other_owner)
    other_key = _mint(other_ee, other_workspace)["agent_key_id"]
    response = other_ee.delete(_detach_url(a["vs_id"]))
    assert (response.status_code, response.json()) == (404, NOT_FOUND)
    response = other_ee.post(_revoke_url(research_key))
    assert (response.status_code, response.json()) == (404, NOT_FOUND)
    assert _mint(other_ee, research, expect=404) == NOT_FOUND
    assert (
        _ensure(other_ee, research, "int_x", ["github"], other_key, expect=404)
        == NOT_FOUND
    )
    assert (
        _ensure(
            other_ee, other_workspace, "int_x", ["github"], research_key, expect=400
        )
        == AGENT_KEY_INVALID
    )
    assert AgentccAPIKey.no_workspace_objects.get(
        gateway_key_id=research_key
    ).status == (AgentccAPIKey.ACTIVE)
    assert set(_by_id(org)) == {a["vs_id"], b["vs_id"]}


@CN22_OFF_733
def test_member_key_refused(gateway):
    """Only EE's service credential calls the API: a person's session (a
    member's or the owner's), a person's or the org's system API key and the
    gateway admin token get 403; no or a wrong pair 401. A gateway key a
    member made in the console, or a legacy key id, is no agent key. And EE's
    pair is accepted nowhere else in the app."""
    org, owner, workspace = _org()
    _seed(org)
    ee, issued = _ee(org, owner)
    member = _member(org, workspace)
    key = _mint(ee, workspace)["agent_key_id"]
    body = {
        "workspace": str(workspace.id),
        "connection_id": "int_4f2a9c1e7b3d",
        "servers": ["github"],
        "mode": "agent",
        "agent_key_id": key,
    }

    from accounts.tests.personal_keys import personal_key_for

    member_key = personal_key_for(member, org, workspace=workspace, name="m")
    system_key = OrgApiKey.no_workspace_objects.create(organization=org, type="system")
    admin_token = secrets.token_hex(32)
    refused = {
        "member session": _bearer(member),
        "owner session": _bearer(owner),
        "member API key": _client(member_key.api_key, member_key.secret_key),
        "admin token": _client(
            issued.credential.api_key,
            issued.secret,
            HTTP_AUTHORIZATION=f"Bearer {admin_token}",
        ),
    }
    for who, client in refused.items():
        for method, url, data in (
            ("post", ENSURE, body),
            ("post", KEYS, {"workspace": str(workspace.id)}),
            ("post", _revoke_url(key), None),
            ("delete", _detach_url("vs_" + "0" * 26), None),
        ):
            response = getattr(client, method)(url, data, format="json")
            assert (response.status_code, response.json()) == (403, NOT_EE), (
                who,
                url,
            )

    # A retired platform pair is no longer a valid non-EE principal. Keep the
    # same endpoint census, now requiring refusal before the service operation.
    retired_client = _client(system_key.api_key, system_key.secret_key)
    for method, url, data in (
        ("post", ENSURE, body),
        ("post", KEYS, {"workspace": str(workspace.id)}),
        ("post", _revoke_url(key), None),
        ("delete", _detach_url("vs_" + "0" * 26), None),
    ):
        response = getattr(retired_client, method)(url, data, format="json")
        assert response.status_code == 401, url
        assert response.json()["code"] == "api_key_retired"
        assert response.json()["create_url"] == "/dashboard/keys"
        assert response["WWW-Authenticate"] == 'ApiKey realm="agentcc-mcp-service"'

    # A platform key that was deleted or disabled is no credential at all:
    # 401, as APIKeyAuthentication would answer, not 403.
    deleted_key = OrgApiKey.no_workspace_objects.create(
        organization=org, type="user", user=member, workspace=workspace, name="d"
    )
    deleted_key.deleted = True
    deleted_key.save(update_fields=["deleted"])
    disabled_key = OrgApiKey.no_workspace_objects.create(
        organization=org, type="user", user=member, workspace=workspace, name="x"
    )
    disabled_key.enabled = False
    disabled_key.save(update_fields=["enabled"])
    unauthenticated = {
        "no credential": APIClient(),
        "wrong secret": _client(issued.credential.api_key, "x" + issued.secret),
        "unknown key": _client("ees_" + secrets.token_hex(16), issued.secret),
        "wrong retired secret": _client(system_key.api_key, "wrong-synthetic-secret"),
        "deleted API key": _client(deleted_key.api_key, deleted_key.secret_key),
        "disabled API key": _client(disabled_key.api_key, disabled_key.secret_key),
    }
    for who, client in unauthenticated.items():
        response = client.post(ENSURE, body, format="json")
        assert response.status_code == 401, who
        assert response["WWW-Authenticate"] == 'ApiKey realm="agentcc-mcp-service"'
        assert response.json()["code"] == "not_authenticated"
        assert "create_url" not in response.json()
        _valid("django_error_response", _document(response))

    # A gateway key a member made in the console is not an agent key, and
    # neither is a legacy id (rule 3).
    console_key, _ = auth_bridge.provision_key(
        name="member's key", user=member, organization=org
    )
    for key_id in (console_key.gateway_key_id, "key_7"):
        refusal = _ensure(ee, workspace, "int_4f2a9c1e7b3d", ["github"], key_id, 400)
        assert refusal == AGENT_KEY_INVALID
    assert not AgentccEEVirtualServer.objects.exists()

    # EE's pair opens nothing else in the app.
    response = ee.get("/agentcc/api-keys/")
    assert response.status_code == 401
    assert key not in json.dumps(response.json())

    assert _ensure(ee, workspace, "int_4f2a9c1e7b3d", ["github"], key)["status"]


@CN22_OFF_733
def test_matches_omega_ee_attach_fixtures(gateway):
    """Every answer CN22a gives EE validates against the contract's schema,
    EE's exact request bodies are accepted, and each error is the golden
    fixture's body byte for byte."""
    org, owner, workspace = _org()
    other_workspace = _workspace(org, owner, "Support")
    _seed(org)
    ee, issued = _ee(org, owner)

    # Mint, with EE's request body.
    request = _fixture("provision/agent-key-mint.request.json")
    assert {"x-api-key", "x-secret-key"} <= set(request["http"]["headers"])
    assert "idempotency-key" in request["http"]["headers"]  # CN22d
    response = ee.post(
        KEYS,
        {**request["body"], "workspace": str(workspace.id)},
        format="json",
        HTTP_IDEMPOTENCY_KEY=uuid7(),
    )
    raw_key = response.json()["result"]["key"]
    assert raw_key == gateway.keys[response.json()["result"]["agent_key_id"]]
    document = _document(response, key="{{ORG_VIRTUAL_KEY}}")
    _valid("provision_agent_key_mint_response", document)
    assert document["http"]["headers"]["cache-control"] == "no-store"
    key = response.json()["result"]["agent_key_id"]

    # Ensure, then a repeat, with EE's request body.
    request = _fixture("provision/ensure-agent.request.json")
    _valid("provision_ensure_request", request)
    body = {**request["body"], "workspace": str(workspace.id), "agent_key_id": key}
    first = ee.post(ENSURE, body, format="json")
    _valid("provision_ensure_response", _document(first))
    golden = _fixture("provision/ensure-agent.response.json")["body"]["result"]
    assert set(first.json()["result"]) == set(golden)
    assert first.json()["result"]["connection_id"] == golden["connection_id"]
    repeat = ee.post(ENSURE, body, format="json")
    _valid("provision_ensure_response", _document(repeat))
    assert repeat.json()["result"]["created"] is False
    for answer in (first, repeat):
        assert raw_key not in answer.content.decode()
        assert not _validator("provision_ensure_response").is_valid(
            {
                **_document(answer),
                "body": {
                    **answer.json(),
                    "result": {**answer.json()["result"], "key": "{{ORG_VIRTUAL_KEY}}"},
                },
            }
        )

    # Each refusal is the golden body. A valid personal key, not a retired
    # system pair, exercises the authenticated-but-not-EE refusal.
    from accounts.tests.personal_keys import personal_key_for

    other_key = _mint(ee, other_workspace)["agent_key_id"]
    personal_key = personal_key_for(owner, org, workspace=workspace)
    cases = [
        (
            "provision/error-mode-mismatch.response.json",
            ee.post(ENSURE, {**body, "servers": ["linear"]}, format="json"),
        ),
        (
            "provision/error-unknown-server.response.json",
            ee.post(ENSURE, {**body, "servers": ["jira"]}, format="json"),
        ),
        (
            "provision/error-agent-key.response.json",
            ee.post(
                ENSURE,
                {
                    **_fixture("forbidden/ensure.request-legacy-key-id.json")["body"],
                    "workspace": str(workspace.id),
                },
                format="json",
            ),
        ),
        (
            "provision/error-agent-key-other-workspace.response.json",
            ee.post(ENSURE, {**body, "agent_key_id": other_key}, format="json"),
        ),
        (
            "provision/error-not-ee-credential.response.json",
            _client(personal_key.api_key, personal_key.secret_key).post(
                ENSURE, body, format="json"
            ),
        ),
        (
            "provision/error-not-found.response.json",
            ee.delete(_detach_url("vs_01K7CN5EEAGENTM0DE00000009")),
        ),
    ]
    gateway.push_fails = True
    cases.append(
        (
            "provision/error-sync-failed.response.json",
            ee.post(
                ENSURE, {**body, "connection_id": "int_8d1b6e3a5c90"}, format="json"
            ),
        )
    )
    gateway.push_fails = False
    gateway.down = True
    cases.append(
        (
            "provision/error-agent-key-gateway-unreachable.response.json",
            ee.post(
                KEYS,
                {"workspace": str(workspace.id)},
                format="json",
                HTTP_IDEMPOTENCY_KEY=uuid7(),
            ),
        )
    )
    gateway.down = False
    for name, response in cases:
        golden = _fixture(name)
        assert response.status_code == golden["http"]["status"], name
        assert response.json() == golden["body"], name
        _valid("django_error_response", _document(response))

    # 401: no pair, or one that is not the org's current credential.
    for client in (
        APIClient(),
        _client(issued.credential.api_key, "x" + issued.secret),
    ):
        response = client.post(ENSURE, body, format="json")
        assert response.status_code == 401
        assert (response.json()["type"], response.json()["code"]) == (
            "authentication_error",
            "not_authenticated",
        )
        _valid("django_error_response", _document(response))

    # EE's forbidden request shapes are refused for their stated reasons.
    without_key = _fixture("forbidden/ensure.request-agent-without-key.json")["body"]
    response = ee.post(
        ENSURE, {**without_key, "workspace": str(workspace.id)}, format="json"
    )
    assert response.status_code == 400
    assert (response.json()["code"], response.json()["attr"]) == (
        "invalid",
        "agent_key_id",
    )
    _valid("django_error_response", _document(response))
    admin_token = _fixture("forbidden/ensure.request-admin-token.json")
    assert admin_token["http"]["headers"]["authorization"].startswith("Bearer ")
    response = _client(
        issued.credential.api_key,
        issued.secret,
        HTTP_AUTHORIZATION="Bearer " + secrets.token_hex(32),
    ).post(ENSURE, body, format="json")
    assert (response.status_code, response.json()) == (403, NOT_EE)

    # User mode is CN22b's (the ensure-user and mode-change fixtures): until
    # then EE's user-mode request gets a plain 400 naming the field.
    user_request = _fixture("provision/ensure-user.request.json")
    response = ee.post(
        ENSURE, {**user_request["body"], "workspace": str(workspace.id)}, format="json"
    )
    assert response.json() == _error(
        400, "invalid", provisioning.USER_MODE_REFUSED, "mode"
    )
    _valid("django_error_response", _document(response))

    # Revoke and detach, with EE's request shapes.
    vs_id = first.json()["result"]["vs_id"]
    detach = _fixture("provision/detach.request.json")
    assert detach["http"]["method"] == "DELETE" and detach["body"] is None
    response = ee.delete(_detach_url(vs_id))
    _valid("provision_detach_response", _document(response))
    assert response.json()["result"] == {
        "vs_id": vs_id,
        "connection_id": golden_connection(),
        "removed": True,
    }
    revoke = _fixture("provision/agent-key-revoke.request.json")
    assert revoke["http"]["method"] == "POST" and revoke["body"] is None
    response = ee.post(_revoke_url(key))
    _valid("provision_agent_key_revoke_response", _document(response))
    assert response.json()["result"] == {"agent_key_id": key, "revoked": True}
    assert raw_key not in response.content.decode()


def golden_connection():
    return _fixture("provision/ensure-agent.request.json")["body"]["connection_id"]


@CN22_OFF_733
def test_delete_detaches_only_this_connection(gateway):
    """Detach removes that connection's virtual server and nothing else: not
    the members, not other connections' or an admin's virtual servers, not
    the vault or the keys. A repeat is 404. When the gateway does not accept
    the removal, it stands and detaching again pushes again."""
    org, owner, workspace = _org()
    support = _workspace(org, owner, "Support")
    _seed(org)
    vault_row = AgentccMCPCredential.objects.create(
        organization=org,
        server_id="sentry",
        kind=AgentccMCPCredential.Kind.BEARER,
        encrypted_secret=encrypt_token("fake-" + secrets.token_hex(8)),
    )
    ee, _ = _ee(org, owner)
    key = _mint(ee, workspace)["agent_key_id"]
    support_key = _mint(ee, support)["agent_key_id"]
    one = _ensure(ee, workspace, "int_one", ["github"], key)["result"]
    two = _ensure(ee, workspace, "int_two", ["github", "sentry"], key)["result"]
    elsewhere = _ensure(ee, support, "int_one", ["github"], support_key)["result"]
    write_config_version(
        org,
        {"mcp": {"virtual_servers": {"team-tools": {"members": ["github"]}}}},
        user=owner,
    )
    admin_vs_id = _virtual_servers(org)["team-tools"]["id"]
    before = _virtual_servers(org)
    servers_before = _active(org).mcp["servers"]
    slug_one = _by_id(org)[one["vs_id"]][0]

    response = ee.delete(_detach_url(one["vs_id"]))
    assert response.status_code == 200
    assert response.json() == {
        "status": True,
        "result": {"vs_id": one["vs_id"], "connection_id": "int_one", "removed": True},
    }
    after = _virtual_servers(org)
    assert after == {slug: entry for slug, entry in before.items() if slug != slug_one}
    assert _active(org).mcp["servers"] == servers_before
    assert slug_one not in gateway.pushed_virtual_servers()
    vault_row.refresh_from_db()
    assert vault_row.status == AgentccMCPCredential.Status.ACTIVE
    assert AgentccAPIKey.no_workspace_objects.get(gateway_key_id=key).status == (
        AgentccAPIKey.ACTIVE
    )
    assert not gateway.revoked
    detached = AuditLog.objects.get(action=provisioning.AUDIT_VS_DETACHED)
    assert detached.actor is None
    assert (detached.metadata["vs_id"], detached.metadata["connection_id"]) == (
        one["vs_id"],
        "int_one",
    )

    for vs_id in (one["vs_id"], admin_vs_id):
        response = ee.delete(_detach_url(vs_id))
        assert (response.status_code, response.json()) == (404, NOT_FOUND)
    assert "team-tools" in _virtual_servers(org)

    # The gateway does not accept the removal: it stands, and is pushed again.
    gateway.push_fails = True
    response = ee.delete(_detach_url(two["vs_id"]))
    assert response.status_code == 503
    assert response.json() == _error(
        503, "gateway_sync_failed", provisioning.DETACH_NOT_SYNCED
    )
    assert two["vs_id"] not in _by_id(org)
    assert AgentccEEVirtualServer.objects.get(vs_id=two["vs_id"]).detached_at
    gateway.push_fails = False
    pushes = len(gateway.pushes)
    response = ee.delete(_detach_url(two["vs_id"]))
    assert response.json()["result"] == {
        "vs_id": two["vs_id"],
        "connection_id": "int_two",
        "removed": True,
    }
    assert len(gateway.pushes) == pushes + 1
    assert two["vs_id"] not in {
        e["id"] for e in gateway.pushed_virtual_servers().values()
    }
    assert ee.delete(_detach_url(two["vs_id"])).status_code == 404

    assert set(_by_id(org)) == {elsewhere["vs_id"], admin_vs_id}
    assert list(
        AgentccEEVirtualServer.objects.filter(organization=org).values_list(
            "vs_id", flat=True
        )
    ) == [elsewhere["vs_id"]]


# --- Supporting tests -------------------------------------------------------


@CN22_OFF_733
def test_user_mode_is_refused_until_cn22b(gateway):
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    versions = _versions(org)
    response = ee.post(
        ENSURE,
        {
            "workspace": str(workspace.id),
            "connection_id": "int_8d1b6e3a5c90",
            "servers": ["linear"],
            "mode": "user",
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json() == _error(
        400, "invalid", provisioning.USER_MODE_REFUSED, "mode"
    )
    assert "soon" not in provisioning.USER_MODE_REFUSED.lower()
    assert _versions(org) == versions
    assert not AgentccEEVirtualServer.objects.exists()


@CN22_OFF_733
def test_ensure_refuses_what_the_gateway_would_not_serve(gateway):
    """A 200 never hands EE a virtual server that cannot work: MCP off, a
    disabled or unknown member, a per-user member in agent mode, or a key
    that is revoked, expired or deleted. Nothing is written."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key = _mint(ee, workspace)["agent_key_id"]
    versions = _versions(org)

    assert _ensure(ee, workspace, "c1", ["notion"], key, 400) == _error(
        400, "invalid", "Server notion is turned off in this organization.", "servers"
    )
    assert _ensure(ee, workspace, "c1", ["github", "linear"], key, 400)["code"] == (
        "mode_mismatch"
    )
    malformed = [
        ({"servers": []}, "servers"),
        ({"servers": [f"s{i}" for i in range(9)]}, "servers"),
        ({"servers": ["github", "github"]}, "servers"),
        ({"connection_id": "-bad"}, "connection_id"),
        ({"connection_id": "x" * 129}, "connection_id"),
        ({"workspace": "not-a-uuid"}, "workspace"),
        ({"mode": "both"}, "mode"),
        ({"key": "anything"}, "key"),
        ({"connection_id": "int_x\n"}, "connection_id"),
        ({"workspace": str(workspace.id) + "\n"}, "workspace"),
        ({"servers": [1]}, "servers"),
        ({"servers": ["github", ""]}, "servers"),
        ({"servers": "github"}, "servers"),
        ({"agent_key_id": 7}, "agent_key_id"),
    ]
    base = {
        "workspace": str(workspace.id),
        "connection_id": "c1",
        "servers": ["github"],
        "mode": "agent",
        "agent_key_id": key,
    }
    for change, attr in malformed:
        response = ee.post(ENSURE, {**base, **change}, format="json")
        assert response.status_code == 400, change
        assert (response.json()["code"], response.json()["attr"]) == ("invalid", attr)
        _valid("django_error_response", _document(response))
        assert "ErrorDetail" not in response.content.decode(), change

    # A list member's error names its place, in plain text.
    response = ee.post(ENSURE, {**base, "servers": ["github", 1]}, format="json")
    place = "servers[1]: Not a valid string."
    assert response.json() == {
        **_error(400, "invalid", place),
        "attr": "servers",
        "details": {"servers": [place]},
    }
    # A body that is not a JSON object has no field at fault.
    response = ee.post(ENSURE, "[1, 2]", content_type="application/json")
    assert response.status_code == 400 and "attr" not in response.json()
    _valid("django_error_response", _document(response))

    api_key = AgentccAPIKey.no_workspace_objects.get(gateway_key_id=key)
    api_key.expires_at = timezone.now() - timedelta(minutes=1)
    api_key.save(update_fields=["expires_at"])
    assert _ensure(ee, workspace, "c1", ["github"], key, 400) == AGENT_KEY_INVALID
    api_key.expires_at = None
    api_key.deleted = True
    api_key.save(update_fields=["expires_at", "deleted"])
    assert _ensure(ee, workspace, "c1", ["github"], key, 400) == AGENT_KEY_INVALID
    revoked = _mint(ee, workspace)["agent_key_id"]
    assert ee.post(_revoke_url(revoked)).status_code == 200
    assert _ensure(ee, workspace, "c1", ["github"], revoked, 400) == AGENT_KEY_INVALID

    off, off_owner, off_workspace = _org("CN22 MCP off")
    _seed(off, enabled=False)
    off_ee, _ = _ee(off, off_owner)
    off_key = _mint(off_ee, off_workspace)["agent_key_id"]
    refusal = _ensure(off_ee, off_workspace, "c1", ["github"], off_key, 400)
    assert refusal["code"] == "invalid" and "turned off" in refusal["message"]

    assert _versions(org) == versions
    assert not AgentccEEVirtualServer.objects.exists()


@CN22_OFF_733
def test_ensure_answers_503_until_the_gateway_has_it(gateway):
    """Rule 9: a 200 only once the gateway accepted the virtual server. What
    was saved stands, and the repeat pushes it again."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key = _mint(ee, workspace)["agent_key_id"]
    gateway.push_fails = True
    response = ee.post(
        ENSURE,
        {
            "workspace": str(workspace.id),
            "connection_id": "int_retry",
            "servers": ["github"],
            "mode": "agent",
            "agent_key_id": key,
        },
        format="json",
    )
    assert (response.status_code, response.json()) == (
        503,
        _fixture("provision/error-sync-failed.response.json")["body"],
    )
    row = AgentccEEVirtualServer.objects.get(organization=org)
    assert row.vs_id in _by_id(org)
    gateway.push_fails = False
    answer = _ensure(ee, workspace, "int_retry", ["github"], key)["result"]
    assert (answer["vs_id"], answer["created"]) == (row.vs_id, False)
    assert row.vs_id in {e["id"] for e in gateway.pushed_virtual_servers().values()}


@CN22_OFF_733
@pytest.mark.parametrize(
    "public_url",
    [
        "",
        "http://gateway.example.com",
        "http://localhost:8090",
        "https://localhost",
        "https://10.0.0.1",
        "https://gateway.internal",
        "https://10.0.0.1.nip.io",
        "https://gateway.example.com/a/../b",
        "https://gateway.example.com?x=1",
        "https://user@gateway.example.com",
    ],
)
def test_url_must_be_public_https(gateway, monkeypatch, public_url):
    """Rule 8: the url is public HTTPS with no query, user, IP literal or
    private name, or ensure answers 503 before writing anything."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key = _mint(ee, workspace)["agent_key_id"]
    monkeypatch.setenv(provisioning.PUBLIC_URL_ENV, public_url)
    versions = _versions(org)
    refusal = _ensure(ee, workspace, "c1", ["github"], key, 503)
    assert refusal == _error(503, "gateway_url_not_public", provisioning.URL_NOT_PUBLIC)
    assert _versions(org) == versions
    assert not AgentccEEVirtualServer.objects.exists()


@CN22_OFF_733
def test_url_not_public_is_a_contract_answer(gateway, monkeypatch):
    """CN22 R3: the 503 gateway_url_not_public answer is in CN5's v1 contract
    (an additive code, spec section 3.5 and section 11), so EE, which pins the
    contract by set digest, never sees a code outside it."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key = _mint(ee, workspace)["agent_key_id"]
    monkeypatch.setenv(provisioning.PUBLIC_URL_ENV, "http://gateway.example.com")
    body = {
        "workspace": str(workspace.id),
        "connection_id": "c1",
        "servers": ["github"],
        "mode": "agent",
        "agent_key_id": key,
    }
    response = ee.post(ENSURE, body, format="json")
    assert response.status_code == 503
    _valid("django_error_response", _document(response))
    spec = SPEC.read_text()
    assert "| 503  | `gateway_url_not_public`" in spec


@CN22_OFF_733
def test_url_follows_the_gateways_mcp_endpoint(gateway, monkeypatch):
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key = _mint(ee, workspace)["agent_key_id"]
    monkeypatch.delenv(provisioning.PUBLIC_URL_ENV)
    monkeypatch.setenv(provisioning.FALLBACK_URL_ENV, "https://gw.example.com/")
    monkeypatch.setenv(provisioning.MCP_ENDPOINT_ENV, "/tools/mcp/")
    answer = _ensure(ee, workspace, "c1", ["github"], key)["result"]
    assert answer["url"] == f"https://gw.example.com/tools/mcp/v/{answer['vs_id']}"


def test_the_site_mounts_the_provisioning_routes(settings):
    """CN22 R2: the site's own URLconf serves every route of the provisioning
    API (mounted after R3, R4 and the live run through the gateway binary)."""
    settings.ROOT_URLCONF = "tfc.urls"
    routes = {
        ENSURE: views.EnsureVirtualServerView,
        "/agentcc/mcp/service/virtual-servers/vs_01M5GZ6QJ1YCR0B45VRK2847NT/": views.DetachVirtualServerView,
        KEYS: views.MintAgentKeyView,
        f"{KEYS}key_{'0' * 24}/revoke/": views.RevokeAgentKeyView,
        CREDENTIAL: views.ServiceCredentialView,
        ROTATE: views.RotateServiceCredentialView,
        REVOKE_CREDENTIAL: views.RevokeServiceCredentialView,
    }
    for route, view in routes.items():
        assert resolve(route).func.view_class is view, route


def test_declared_mode_is_agent_only_until_cn22b():
    """CN22 R2 (the reviewer's P3): the API declares mode agent only, in the
    request and the answer, until CN22b builds user mode; a user-mode request
    still gets the same 400 invalid naming mode (test_user_mode_is_refused_until_cn22b)."""
    for serializer in (
        views.EEEnsureRequestSerializer(),
        views.EEEnsureResultSerializer(),
    ):
        assert list(serializer.fields["mode"].choices) == [provisioning.AGENT]
    field = views.EEEnsureRequestSerializer().fields["mode"]
    assert (
        field.run_validation(provisioning.USER) == provisioning.USER
    )  # refused by ensure, as before
    with pytest.raises(ValidationError):
        field.run_validation("other")


def test_request_and_url_rules_are_the_contracts():
    """The patterns CN22 enforces are the contract schema's own."""
    defs = SCHEMA["$defs"]
    assert provisioning.VS_URL.pattern == defs["vs_url"]["pattern"]
    assert provisioning.VS_URL_MAX_LENGTH == defs["vs_url"]["maxLength"]
    assert provisioning.NON_PUBLIC_URL.pattern == defs["non_public_url"]["pattern"]
    assert provisioning.AGENT_KEY_ID.pattern == defs["key_id"]["pattern"]
    assert provisioning.CONNECTION_ID.pattern == defs["connection_id"]["pattern"]
    assert provisioning.VS_ID.pattern == defs["vs_id"]["pattern"]
    assert provisioning.WORKSPACE_ID.pattern == defs["uuid"]["pattern"]
    assert provisioning.MAX_SERVERS == defs["servers"]["maxItems"]


@CN22_OFF_733
def test_agent_key_mint_and_revoke(gateway):
    """Mint makes a gateway key bound to the workspace, not personal and with
    no limits, and returns it once; nothing is made when the gateway is
    down. Revoke is safe to repeat, and survives a gateway outage."""
    org, owner, workspace = _org()
    _seed(org)
    ee, issued = _ee(org, owner)
    minted = _mint(ee, workspace)
    key_id, raw_key = minted["agent_key_id"], minted["key"]
    assert minted["workspace"] == str(workspace.id)
    api_key = AgentccAPIKey.no_workspace_objects.get(gateway_key_id=key_id)
    assert api_key.user is None and api_key.owner == "omega-ee"
    assert api_key.name == "Omega EE agent key: Engineering"
    assert api_key.key_hash == hashlib.sha256(raw_key.encode()).hexdigest()
    # CN22c: the key expires 90 days after the mint (test_ee_agent_key_policy).
    assert api_key.expires_at is not None and api_key.status == AgentccAPIKey.ACTIVE
    assert api_key.metadata == {"org_id": str(org.id)}
    assert (
        gateway.created[-1]["models"] is None
        and gateway.created[-1]["providers"] is None
    )
    binding = AgentccEEAgentKey.objects.get(api_key=api_key)
    assert (binding.workspace, binding.minted_by) == (workspace, issued.credential)
    audit = AuditLog.objects.get(action=provisioning.AUDIT_AGENT_KEY_MINTED)
    assert audit.actor is None and audit.metadata["actor"] == "omega-ee"
    assert raw_key not in json.dumps(audit.metadata) + json.dumps(audit.changes)

    gateway.down = True
    keys = AgentccAPIKey.no_workspace_objects.count()
    assert _mint(ee, workspace, expect=503)["code"] == "gateway_sync_failed"
    assert AgentccAPIKey.no_workspace_objects.count() == keys
    assert AgentccEEAgentKey.objects.count() == 1
    gateway.down = False

    response = ee.post(_revoke_url(key_id))
    assert response.json() == {
        "status": True,
        "result": {"agent_key_id": key_id, "revoked": True},
    }
    api_key.refresh_from_db()
    assert api_key.status == AgentccAPIKey.REVOKED
    assert ee.post(_revoke_url(key_id)).status_code == 200
    assert gateway.revoked == [key_id, key_id]

    second = _mint(ee, workspace)["agent_key_id"]
    gateway.revoke_status = 500
    response = ee.post(_revoke_url(second))
    assert response.json() == _error(
        503, "gateway_sync_failed", provisioning.REVOKE_NOT_SYNCED
    )
    assert AgentccAPIKey.no_workspace_objects.get(gateway_key_id=second).status == (
        AgentccAPIKey.REVOKED
    )
    gateway.revoke_status = 404  # the gateway dropped it at its key sync
    assert ee.post(_revoke_url(second)).status_code == 200

    console_key, _ = auth_bridge.provision_key(
        name="console", user=owner, organization=org
    )
    for key in (console_key.gateway_key_id, "key_7", "key_" + "0" * 24):
        response = ee.post(_revoke_url(key))
        assert (response.status_code, response.json()) == (404, NOT_FOUND)
    console_key.refresh_from_db()
    assert console_key.status == AgentccAPIKey.ACTIVE


@CN22_OFF_733
def test_service_credential_lifecycle(gateway):
    """An Owner or Admin, signed in, issues EE's credential (the secret once),
    rotates it (the old pair stops at once) and revokes it. A member, an API
    key or EE's own pair cannot manage it."""
    org, owner, workspace = _org()
    _seed(org)
    admin = _bearer(owner)
    assert admin.get(CREDENTIAL).json() == {"status": True, "result": None}

    response = admin.post(CREDENTIAL)
    assert response.status_code == 201 and response["Cache-Control"] == "no-store"
    issued = response.json()["result"]
    assert issued["api_key"].startswith("ees_") and issued["status"] == "active"
    row = AgentccEEServiceCredential.objects.get(pk=issued["id"])
    assert row.secret_hash == hashlib.sha256(issued["secret_key"].encode()).hexdigest()
    assert row.created_by == owner
    shown = admin.get(CREDENTIAL).json()["result"]
    assert shown["api_key"] == issued["api_key"] and "secret_key" not in shown
    assert admin.post(CREDENTIAL).status_code == 409

    ee = _client(issued["api_key"], issued["secret_key"])
    assert _mint(ee, workspace)["workspace"] == str(workspace.id)
    assert AgentccEEServiceCredential.objects.get(pk=issued["id"]).last_used_at

    response = admin.post(ROTATE)
    assert response.status_code == 201 and response["Cache-Control"] == "no-store"
    rotated = response.json()["result"]
    assert rotated["api_key"] != issued["api_key"]
    assert _mint(ee, workspace, expect=401)["code"] == "not_authenticated"
    ee = _client(rotated["api_key"], rotated["secret_key"])
    assert _mint(ee, workspace)["workspace"] == str(workspace.id)

    response = admin.post(REVOKE_CREDENTIAL)
    assert response.json()["result"]["status"] == "revoked"
    assert _mint(ee, workspace, expect=401)["code"] == "not_authenticated"
    assert admin.post(ROTATE).status_code == 404
    assert admin.post(CREDENTIAL).status_code == 201

    assert [
        a.action
        for a in AuditLog.objects.filter(organization=org).order_by("created_at")
        if a.action.startswith("agentcc.ee_service")
    ] == [
        provisioning.AUDIT_CREDENTIAL_ISSUED,
        provisioning.AUDIT_CREDENTIAL_ROTATED,
        provisioning.AUDIT_CREDENTIAL_REVOKED,
        provisioning.AUDIT_CREDENTIAL_ISSUED,
    ]
    assert all(
        a.actor == owner
        for a in AuditLog.objects.filter(action__startswith="agentcc.ee_service")
    )

    member = _member(org, workspace)
    system_key = OrgApiKey.no_workspace_objects.create(organization=org, type="system")
    current = provisioning.current_credential(org)
    for client, expected in (
        (_bearer(member), 403),
        (_client(system_key.api_key, system_key.secret_key), 401),
    ):
        for method, url in (
            ("get", CREDENTIAL),
            ("post", CREDENTIAL),
            ("post", ROTATE),
            ("post", REVOKE_CREDENTIAL),
        ):
            response = getattr(client, method)(url)
            assert response.status_code == expected, url
            if expected == 401:
                assert response.json()["code"] == "api_key_retired"
                assert response.json()["create_url"] == "/dashboard/keys"
    assert provisioning.current_credential(org) == current


def _status(agent_key_id):
    return AgentccAPIKey.no_workspace_objects.get(gateway_key_id=agent_key_id).status


@CN22_OFF_733
def test_revoking_the_credential_revokes_every_agent_key(gateway):
    """Spec 3.7: revoking EE's service credential keeps EE out for good. Each
    live agent key EE holds in the org, minted under this credential or one
    it rotated out, in any workspace, is revoked with it and the gateway is
    told. Rotation revokes none; a console key and another org's keys stay."""
    org, owner, workspace = _org()
    support = _workspace(org, owner, "Support")
    _seed(org)
    admin = _bearer(owner)
    issued = admin.post(CREDENTIAL).json()["result"]
    ee = _client(issued["api_key"], issued["secret_key"])
    first = _mint(ee, workspace)["agent_key_id"]
    _ensure(ee, workspace, "c1", ["github"], first)
    rotated = admin.post(ROTATE).json()["result"]
    assert _status(first) == AgentccAPIKey.ACTIVE  # rotation revokes no key
    ee = _client(rotated["api_key"], rotated["secret_key"])
    second = _mint(ee, support)["agent_key_id"]
    already = _mint(ee, support)["agent_key_id"]
    assert ee.post(_revoke_url(already)).status_code == 200
    console_key, _ = auth_bridge.provision_key(
        name="console", user=owner, organization=org
    )
    elsewhere, elsewhere_owner, elsewhere_ws = _org("CN22 Elsewhere")
    _seed(elsewhere)
    elsewhere_ee, _ = _ee(elsewhere, elsewhere_owner)
    elsewhere_key = _mint(elsewhere_ee, elsewhere_ws)["agent_key_id"]
    gateway.revoked.clear()

    response = admin.post(REVOKE_CREDENTIAL)
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["status"] == "revoked" and result["gateway_synced"] is True
    assert result["agent_keys_revoked"] == sorted([first, second])
    assert sorted(gateway.revoked) == sorted([first, second])
    assert {k: _status(k) for k in (first, second, already, elsewhere_key)} == {
        first: AgentccAPIKey.REVOKED,
        second: AgentccAPIKey.REVOKED,
        already: AgentccAPIKey.REVOKED,
        elsewhere_key: AgentccAPIKey.ACTIVE,
    }
    console_key.refresh_from_db()
    assert console_key.status == AgentccAPIKey.ACTIVE
    audit = AuditLog.objects.get(
        organization=org, action=provisioning.AUDIT_CREDENTIAL_REVOKED
    )
    assert audit.actor == owner
    assert audit.metadata["agent_keys_revoked"] == sorted([first, second])
    # The gateway's key sync leaves them out, and a new credential does not
    # bring them back.
    loadable = auth_bridge.gateway_loadable_keys(org).values_list(
        "gateway_key_id", flat=True
    )
    assert not {first, second} & set(loadable)
    renewed = admin.post(CREDENTIAL).json()["result"]
    ee = _client(renewed["api_key"], renewed["secret_key"])
    assert _ensure(ee, workspace, "c1", ["github"], first, 400) == AGENT_KEY_INVALID

    # The gateway cannot be told: the keys are revoked here all the same.
    third = _mint(ee, workspace)["agent_key_id"]
    gateway.revoke_status = 500
    response = admin.post(REVOKE_CREDENTIAL)
    assert response.status_code == 200
    result = response.json()["result"]
    assert (result["agent_keys_revoked"], result["gateway_synced"]) == ([third], False)
    assert _status(third) == AgentccAPIKey.REVOKED
    assert _status(elsewhere_key) == AgentccAPIKey.ACTIVE


@CN22_OFF_733
def test_a_mint_racing_a_revoke_leaves_no_live_key(gateway):
    """A mint that authenticated just before an admin revoked (or rotated)
    the credential makes no key the revoke's sweep missed. Since CN22c the
    whole mint runs under the org lock, so the credential is checked before
    the gateway makes anything: no key at all."""
    org, owner, workspace = _org()
    _seed(org)
    for retire in (provisioning.revoke_credential, provisioning.rotate_credential):
        if provisioning.current_credential(org) is None:
            provisioning.issue_credential(org, owner)
        issued = provisioning.rotate_credential(org, owner)
        credential = provisioning.authenticate(issued.credential.api_key, issued.secret)
        retire(org, owner)
        gateway.revoked.clear()
        created = len(gateway.created)
        with pytest.raises(provisioning.ProvisioningError) as refused:
            provisioning.mint_agent_key(credential, str(workspace.id), uuid7())
        assert (refused.value.status, refused.value.code) == (401, "not_authenticated")
        assert len(gateway.created) == created and gateway.revoked == []
        assert not AgentccAPIKey.no_workspace_objects.filter(owner="omega-ee").exists()
        assert not AgentccEEAgentKey.objects.exists()

    answer = views._error(refused.value)
    assert answer["WWW-Authenticate"] == 'ApiKey realm="agentcc-mcp-service"'
    assert answer.data == _error(401, "not_authenticated", refused.value.message)


@CN22_OFF_733
def test_answers_match_their_declared_serializers(gateway, settings):
    """With DEBUG on, validated_request checks each answer against the
    serializer the route declares (its OpenAPI contract): none may drift."""
    settings.DEBUG = True
    org, owner, workspace = _org()
    _seed(org)
    admin = _bearer(owner)
    with capture_logs() as logs:
        assert admin.get(CREDENTIAL).status_code == 200
        issued = admin.post(CREDENTIAL).json()["result"]
        assert admin.get(CREDENTIAL).status_code == 200
        ee = _client(issued["api_key"], issued["secret_key"])
        key = _mint(ee, workspace)["agent_key_id"]
        vs = _ensure(ee, workspace, "c1", ["github"], key)["result"]
        _ensure(ee, workspace, "c1", ["github"], key)
        _ensure(ee, workspace, "c2", ["jira"], key, 400)
        _ensure(ee, workspace, "c2", ["github"], "key_7", 400)
        _mint(ee, _workspace(_org("CN22 Elsewhere")[0], owner, "X"), expect=404)
        gateway.push_fails = True
        _ensure(ee, workspace, "c3", ["github"], key, 503)
        gateway.push_fails = False
        assert ee.delete(_detach_url(vs["vs_id"])).status_code == 200
        assert ee.post(_revoke_url(key)).status_code == 200
        assert admin.post(ROTATE).status_code == 201
        assert admin.post(CREDENTIAL).status_code == 409
        assert admin.post(REVOKE_CREDENTIAL).status_code == 200
        assert admin.post(ROTATE).status_code == 404
    drift = [
        event
        for event in logs
        if event.get("event")
        in (
            "API response does not match declared serializer.",
            "API response status is not declared.",
            "API request does not match declared serializer.",
        )
    ]
    assert not drift, drift


@CN22_OFF_733
def test_no_secret_is_logged_audited_or_stored_in_clear(gateway):
    org, owner, workspace = _org()
    _seed(org)
    with capture_logs() as logs:
        ee, issued = _ee(org, owner)
        minted = _mint(ee, workspace)
        vs = _ensure(ee, workspace, "c1", ["github"], minted["agent_key_id"])["result"]
        ee.delete(_detach_url(vs["vs_id"]))
        ee.post(_revoke_url(minted["agent_key_id"]))
        gateway.down = True
        _mint(ee, workspace, expect=503)
    secrets_seen = (issued.secret, minted["key"])
    logged = json.dumps(logs, default=str)
    audited = json.dumps(
        [[a.metadata, a.changes] for a in AuditLog.objects.filter(organization=org)],
        default=str,
    )
    configs = json.dumps(
        list(
            AgentccOrgConfig.no_workspace_objects.filter(organization=org).values_list(
                "mcp", flat=True
            )
        )
    )
    stored = json.dumps(
        list(AgentccEEServiceCredential.objects.values()), default=str
    ) + json.dumps(list(AgentccAPIKey.no_workspace_objects.values()), default=str)
    for secret in secrets_seen:
        assert secret not in logged
        assert secret not in audited
        assert secret not in configs
        assert secret not in stored
    assert "IssuedCredential(credential=" in repr(issued) and issued.secret not in repr(
        issued
    )
