"""CN22c: the EE agent key is a scoped key, not an org-wide one.

AM-C25's 22:50 addendum (binding): the key CN22's mint makes for Omega EE
carries scopes ``mcp,models`` and a spend budget, both from records no tenant
can write; its models are the org's own (no list on the key); the org has a
cap on live agent keys; each key expires after 90 days and EE rotates it with
ensure; and every mint, rotation, scope refusal and budget refusal is audited
with the workspace and the actor. The gateway half (key gate, budget,
refusal reporter) is ``agentcc-gateway/internal/keyscope``; this is Django's
half. The gateway is faked at its admin client.
"""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models.audit_log import AuditLog
from agentcc.models import AgentccAPIKey, AgentccRequestLog
from agentcc.models.ee_agent_key_policy import AgentccEEWorkspacePolicy
from agentcc.models.mcp_service_provisioning import AgentccEEAgentKey
from agentcc.services import auth_bridge
from agentcc.services import ee_agent_key_policy as policy
from agentcc.services import mcp_service_provisioning as provisioning
from agentcc.tests.test_mcp_service_provisioning import (
    CN22_OFF_733,
    FakeGateway,
    _bearer,
    _ee,
    _ensure,
    _fixture,
    _member,
    _mint,
    _org,
    _seed,
    _valid,
    _workspace,
    uuid7,
)

urlpatterns = [
    path("agentcc/", include("agentcc.urls_mcp_service_provisioning")),
    path("", include("tfc.urls")),
]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db,
    pytest.mark.urls(__name__),
]

ADMIN_TOKEN = "cn22c-admin-token-" + "0123456789abcdef" * 2
REFUSALS = "/agentcc/mcp/service/agent-key-refusals/"


def _policy_url(workspace):
    return f"/agentcc/mcp/service/workspaces/{workspace.id}/agent-key-policy/"


def _keys_url(workspace):
    return f"/agentcc/mcp/service/workspaces/{workspace.id}/agent-keys/"


class RecordingGateway(FakeGateway):
    """FakeGateway that also keeps the metadata each key was created with."""

    def create_key(self, name, owner="", models=None, providers=None, metadata=None):
        made = super().create_key(name, owner, models, providers, metadata)
        self.created[-1]["metadata"] = dict(metadata or {})
        self.created[-1]["id"] = made["id"]
        return made


@pytest.fixture
def gateway(monkeypatch):
    fake = RecordingGateway()
    for target in (
        "agentcc.services.auth_bridge.get_gateway_client",
        "agentcc.services.config_push.get_gateway_client",
        "agentcc.services.mcp_service_provisioning.get_gateway_client",
    ):
        monkeypatch.setattr(target, lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _env(monkeypatch, settings):
    # This file exercises the explicitly enabled compatibility implementation.
    settings.EE_SERVICE_PROVISIONING_ENABLED = True
    monkeypatch.setenv(provisioning.PUBLIC_URL_ENV, "https://gateway.example.com")
    monkeypatch.delenv(provisioning.FALLBACK_URL_ENV, raising=False)
    monkeypatch.delenv(provisioning.MCP_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(policy.CAP_ENV, raising=False)
    monkeypatch.delenv(policy.DEFAULT_BUDGET_ENV, raising=False)
    monkeypatch.setattr("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN)
    cache.clear()


def _audits(org, action):
    return list(
        AuditLog.objects.filter(organization=org, action=action).order_by("created_at")
    )


def _payload(gateway_key_id):
    key = auth_bridge.gateway_loadable_keys().get(gateway_key_id=gateway_key_id)
    return auth_bridge.gateway_key_payloads([key])[0]


# --- Rule 1 and the mint: a scoped key, with its budget and a 90-day expiry --


@CN22_OFF_733
def test_mint_makes_a_scoped_key_with_a_budget_and_a_90_day_expiry(gateway):
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    before = timezone.now()
    minted = _mint(ee, workspace)

    made = gateway.created[-1]
    assert made["metadata"] == {
        "org_id": str(org.id),
        "scopes": "mcp,models",
        "budget_limit_usd": "50.00",
        "budget_period": "monthly",
    }
    # Rule 2: no model or provider list of its own; the org's config decides.
    assert made["models"] is None and made["providers"] is None
    row = AgentccAPIKey.no_workspace_objects.get(gateway_key_id=minted["agent_key_id"])
    assert row.metadata == {"org_id": str(org.id)}  # server-owned entries never stored
    assert before + timedelta(days=90) <= row.expires_at
    assert row.expires_at <= timezone.now() + timedelta(days=90)
    assert minted["expires_at"] == row.expires_at.isoformat().replace("+00:00", "Z")

    audit = _audits(org, provisioning.AUDIT_AGENT_KEY_MINTED)[-1]
    assert audit.actor is None and audit.metadata["actor"] == "omega-ee"
    assert audit.metadata["workspace"] == str(workspace.id)
    assert audit.metadata["scopes"] == "mcp,models"
    assert audit.metadata["expires_at"] == row.expires_at.isoformat()


@CN22_OFF_733
def test_the_key_sync_carries_the_scope_and_budget_from_the_records(gateway):
    """The gateway rebuilds a synced key from the bulk payload, so the scope
    and budget ride there, from the binding and the workspace policy, never
    from the key's stored metadata. A tenant's stored "scopes" never reaches
    the gateway, and a policy change reaches it with no new key."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key_id = _mint(ee, workspace)["agent_key_id"]

    payload = _payload(key_id)
    assert payload["metadata"]["scopes"] == "mcp,models"
    assert payload["metadata"]["budget_limit_usd"] == "50.00"
    assert payload["metadata"]["budget_period"] == "monthly"
    assert payload["models"] == [] and payload["providers"] == []
    assert payload["expires_at"] is not None

    response = _bearer(owner).put(
        _policy_url(workspace),
        {"budget_usd": "5.25", "budget_period": "weekly"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert _payload(key_id)["metadata"]["budget_limit_usd"] == "5.25"
    assert _payload(key_id)["metadata"]["budget_period"] == "weekly"

    # A console key whose stored metadata claims a scope or a budget (written
    # before they were reserved) is sent without them.
    console, _ = auth_bridge.provision_key(name="console", user=owner, organization=org)
    AgentccAPIKey.no_workspace_objects.filter(pk=console.pk).update(
        metadata={"scopes": "mcp", "budget_limit_usd": "1", "env": "prod"}
    )
    sent = _payload(console.gateway_key_id)["metadata"]
    assert sent == {"org_id": str(org.id), "env": "prod"}


@CN22_OFF_733
def test_the_bulk_route_sends_every_ee_key_with_its_scope(gateway, settings):
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    keys = [_mint(ee, workspace)["agent_key_id"] for _ in range(3)]
    settings.AGENTCC_ADMIN_TOKEN = ADMIN_TOKEN
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
    response = client.get("/agentcc/api-keys/bulk/")
    assert response.status_code == 200, response.content[:400]
    sent = {k["id"]: k for k in response.json()["result"]}
    for key_id in keys:
        assert sent[key_id]["metadata"]["scopes"] == "mcp,models"


@CN22_OFF_733
def test_a_failed_binding_leaves_no_key_behind(gateway, monkeypatch):
    """The key row and its binding commit together: a key sync can never list
    an EE key without the binding that scopes it, and a mint that fails after
    the gateway made the key revokes it there."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)

    def boom(*args, **kwargs):
        raise RuntimeError("binding insert failed")

    monkeypatch.setattr(AgentccEEAgentKey.objects, "create", boom)
    with pytest.raises(RuntimeError):
        ee.post(
            "/agentcc/mcp/service/agent-keys/",
            {"workspace": str(workspace.id)},
            format="json",
            HTTP_IDEMPOTENCY_KEY=uuid7(),
        )
    assert not AgentccAPIKey.no_workspace_objects.filter(owner="omega-ee").exists()
    assert gateway.revoked == [gateway.created[-1]["id"]]


# --- Rule 4: the per-org cap on live agent keys ------------------------------


@CN22_OFF_733
def test_the_org_cap_on_live_agent_keys(gateway, monkeypatch):
    monkeypatch.setenv(policy.CAP_ENV, "2")
    org, owner, workspace = _org()
    other_ws = _workspace(org, owner, "Support")
    _seed(org)
    ee, _ = _ee(org, owner)
    first = _mint(ee, workspace)["agent_key_id"]
    _mint(ee, other_ws)
    created = len(gateway.created)

    refused = _mint(ee, workspace, expect=409)
    assert refused["code"] == "agent_key_cap_reached"
    assert len(gateway.created) == created  # nothing was made at the gateway
    document = {
        "http": {"status": 409, "headers": {"content-type": "application/json"}},
        "body": refused,
    }
    _valid("django_error_response", document)
    assert document == _fixture("provision/error-agent-key-cap.response.json")
    audit = _audits(org, policy.AUDIT_AGENT_KEY_CAP_REFUSED)[-1]
    assert audit.actor is None and audit.metadata["actor"] == "omega-ee"
    assert audit.metadata["workspace"] == str(workspace.id)
    assert audit.metadata["live_agent_keys"] == 2 and audit.metadata["cap"] == 2

    # A revoked key and an expired key leave room.
    assert (
        ee.post(f"/agentcc/mcp/service/agent-keys/{first}/revoke/").status_code == 200
    )
    third = _mint(ee, workspace)["agent_key_id"]
    AgentccAPIKey.no_workspace_objects.filter(gateway_key_id=third).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    _mint(ee, workspace)

    # Another org's keys never count.
    org2, owner2, ws2 = _org("Other org")
    _seed(org2)
    ee2, _ = _ee(org2, owner2)
    _mint(ee2, ws2)


# --- Rule 4: expiry, and rotation by ensure ---------------------------------


@CN22_OFF_733
def test_ensure_rotates_the_key_and_answers_its_expiry(gateway):
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    old = _mint(ee, workspace)
    first = _ensure(ee, workspace, "conn-1", ["github"], old["agent_key_id"])["result"]
    assert first["agent_key_expires_at"] == old["expires_at"]
    assert not _audits(org, policy.AUDIT_AGENT_KEY_ROTATED)

    new = _mint(ee, workspace)
    again = _ensure(ee, workspace, "conn-1", ["github"], new["agent_key_id"])["result"]
    assert again["vs_id"] == first["vs_id"] and again["created"] is False
    assert again["agent_key_expires_at"] == new["expires_at"]
    _valid("provision_ensure_response", _document_of(again))
    rotated = _audits(org, policy.AUDIT_AGENT_KEY_ROTATED)
    assert len(rotated) == 1
    row = rotated[0]
    assert row.actor is None and row.metadata["actor"] == "omega-ee"
    assert row.metadata["workspace"] == str(workspace.id)
    assert row.metadata["from_agent_key_id"] == old["agent_key_id"]
    assert row.metadata["to_agent_key_id"] == new["agent_key_id"]
    assert row.metadata["vs_id"] == first["vs_id"]

    # An expired key is refused (rule 3) and leaves the gateway's key set.
    AgentccAPIKey.no_workspace_objects.filter(
        gateway_key_id=old["agent_key_id"]
    ).update(expires_at=timezone.now() - timedelta(seconds=1))
    refused = _ensure(
        ee, workspace, "conn-2", ["github"], old["agent_key_id"], expect=400
    )
    assert refused["code"] == "agent_key_invalid"
    loadable = set(
        auth_bridge.gateway_loadable_keys().values_list("gateway_key_id", flat=True)
    )
    assert old["agent_key_id"] not in loadable and new["agent_key_id"] in loadable


def _document_of(result):
    return {
        "http": {"status": 200, "headers": {"content-type": "application/json"}},
        "body": {"status": True, "result": result},
    }


# --- Rule 3: the workspace budget an Owner or Admin sets, and usage per key -


@CN22_OFF_733
def test_an_owner_or_admin_sets_the_workspace_agent_key_budget(gateway):
    org, owner, workspace = _org()
    _seed(org)
    admin = _bearer(owner)
    got = admin.get(_policy_url(workspace)).json()["result"]
    assert got == {
        "workspace": str(workspace.id),
        "budget_usd": "50.00",
        "budget_period": "monthly",
        "default": True,
        "scopes": "mcp,models",
        "lifetime_days": 90,
        "org_cap": policy.DEFAULT_CAP,
        "live_agent_keys": 0,
    }
    response = admin.put(
        _policy_url(workspace),
        {"budget_usd": "12.50", "budget_period": "daily"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["budget_usd"] == "12.50"
    assert response.json()["result"]["default"] is False
    row = AgentccEEWorkspacePolicy.objects.get(workspace=workspace)
    assert row.agent_key_budget_usd == Decimal("12.50") and row.updated_by == owner
    audit = _audits(org, policy.AUDIT_WORKSPACE_POLICY_UPDATED)[-1]
    assert audit.actor == owner and audit.metadata["workspace"] == str(workspace.id)
    assert audit.changes == {
        "budget_usd": {"old": "50.00", "new": "12.50"},
        "budget_period": {"old": "monthly", "new": "daily"},
    }

    for bad in (
        {"budget_usd": "-1", "budget_period": "daily"},
        {"budget_usd": "1.001", "budget_period": "daily"},
        {"budget_usd": "5", "budget_period": "yearly"},
        {"budget_usd": "5"},
        {"budget_usd": "5", "budget_period": "daily", "scopes": "admin"},
    ):
        assert (
            admin.put(_policy_url(workspace), bad, format="json").status_code == 400
        ), bad

    member = _bearer(_member(org, workspace))
    assert member.get(_policy_url(workspace)).status_code == 403
    assert (
        member.put(
            _policy_url(workspace),
            {"budget_usd": "999", "budget_period": "daily"},
            format="json",
        ).status_code
        == 403
    )
    ee, _ = _ee(org, owner)
    assert ee.put(
        _policy_url(workspace),
        {"budget_usd": "999", "budget_period": "daily"},
        format="json",
    ).status_code in (401, 403)

    org2, owner2, ws2 = _org("Other org")
    assert admin.get(_policy_url(ws2)).status_code == 404


@CN22_OFF_733
def test_usage_shows_each_agent_keys_budget_and_spend(gateway):
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    a = _mint(ee, workspace)["agent_key_id"]
    b = _mint(ee, workspace)["agent_key_id"]
    now = timezone.now()
    for key_id, cost in ((a, "0.40"), (a, "0.35"), (b, "0.10")):
        AgentccRequestLog.no_workspace_objects.create(
            organization=org,
            request_id=f"r-{key_id}-{cost}",
            model="gpt-4o",
            provider="openai",
            api_key_id=key_id,
            cost=Decimal(cost),
            status_code=200,
        )
    old = AgentccRequestLog.no_workspace_objects.create(
        organization=org,
        request_id="r-old",
        model="gpt-4o",
        provider="openai",
        api_key_id=a,
        cost=Decimal("9"),
        status_code=200,
    )
    AgentccRequestLog.no_workspace_objects.filter(pk=old.pk).update(
        created_at=policy.period_start("monthly", now) - timedelta(seconds=1)
    )
    listed = _bearer(owner).get(_keys_url(workspace)).json()["result"]
    by_id = {k["agent_key_id"]: k for k in listed["keys"]}
    assert by_id[a]["period_spend_usd"] == "0.750000"
    assert by_id[b]["period_spend_usd"] == "0.100000"
    assert by_id[a]["budget_usd"] == "50.00" and by_id[a]["budget_period"] == "monthly"
    assert by_id[a]["status"] == "active" and by_id[a]["expires_at"]
    assert by_id[a]["requests"] == 2
    assert _bearer(_member(org, workspace)).get(_keys_url(workspace)).status_code == 403


# --- Rule 5: refusals at the gateway and in Django, audited -----------------


@CN22_OFF_733
def test_gateway_refusals_are_audited_with_workspace_and_actor(gateway, settings):
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key_id = _mint(ee, workspace)["agent_key_id"]
    events = [
        {
            "agent_key_id": key_id,
            "code": "key_scope_denied",
            "method": "GET",
            "path": "/v1/models",
            "count": 3,
            "first_at": "2026-10-21T23:00:00Z",
            "last_at": "2026-10-21T23:00:05Z",
        },
        {
            "agent_key_id": key_id,
            "code": "agent_key_budget_exhausted",
            "method": "PIPELINE",
            "path": "chat endpoint (model gpt-4o)",
            "count": 1,
            "first_at": "2026-10-21T23:00:01Z",
            "last_at": "2026-10-21T23:00:01Z",
        },
        {
            "agent_key_id": "key_" + "0" * 24,
            "code": "key_scope_denied",
            "method": "GET",
            "path": "/x",
            "count": 1,
            "first_at": "2026-10-21T23:00:01Z",
            "last_at": "2026-10-21T23:00:01Z",
        },
    ]
    gw = APIClient()
    gw.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
    response = gw.post(REFUSALS, {"events": events}, format="json")
    assert response.status_code == 200, response.content[:400]
    assert response.json()["result"] == {"audited": 2, "skipped": 1, "invalid": 0}

    scope = _audits(org, policy.AUDIT_AGENT_KEY_SCOPE_REFUSED)[-1]
    budget = _audits(org, policy.AUDIT_AGENT_KEY_BUDGET_REFUSED)[-1]
    for row in (scope, budget):
        assert row.actor is None and row.metadata["actor"] == "omega-ee"
        assert row.metadata["workspace"] == str(workspace.id)
        assert row.metadata["agent_key_id"] == key_id
        assert row.metadata["surface"] == "gateway"
    assert scope.metadata["count"] == 3 and scope.metadata["path"] == "/v1/models"

    assert (
        APIClient().post(REFUSALS, {"events": events}, format="json").status_code == 403
    )
    # An event that cannot be read is counted, not audited; it never fails
    # the batch (fix round 1, R-CN22c-2).
    for bad in ({"agent_key_id": key_id}, events[0] | {"code": "other"}):
        response = gw.post(REFUSALS, {"events": [bad]}, format="json")
        assert response.status_code == 200, response.content[:300]
        assert response.json()["result"] == {"audited": 0, "skipped": 0, "invalid": 1}


@CN22_OFF_733
def test_django_routes_answer_an_agent_key_403_key_scope_denied(gateway):
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    minted = _mint(ee, workspace)
    raw = minted["key"]

    for headers in ({"HTTP_AUTHORIZATION": f"Bearer {raw}"}, {"HTTP_X_API_KEY": raw}):
        client = APIClient()
        client.credentials(**headers)
        for method, route in (
            ("get", "/agentcc/api-keys/"),
            ("post", "/agentcc/mcp/service/agent-keys/"),
            ("get", _policy_url(workspace)),
        ):
            response = (
                getattr(client, method)(route, {}, format="json")
                if method == "post"
                else client.get(route)
            )
            assert response.status_code == 403, (route, response.content[:300])
            body = response.json()
            assert (
                body
                == _fixture("provision/error-agent-key-out-of-scope.response.json")[
                    "body"
                ]
            )
            _valid(
                "django_error_response",
                {
                    "http": {
                        "status": 403,
                        "headers": {"content-type": "application/json"},
                    },
                    "body": body,
                },
            )
    # One audit row per key and route in a window, however many requests.
    rows = _audits(org, policy.AUDIT_AGENT_KEY_SCOPE_REFUSED)
    assert {r.metadata["path"] for r in rows} == {
        "/agentcc/api-keys/",
        "/agentcc/mcp/service/agent-keys/",
        _policy_url(workspace),
    }
    assert all(
        r.metadata["surface"] == "django"
        and r.metadata["workspace"] == str(workspace.id)
        and r.metadata["agent_key_id"] == minted["agent_key_id"]
        and r.actor is None
        for r in rows
    )

    # A revoked agent key, and a key that is not EE's, are the route's own
    # business (no named 403).
    console, console_raw = auth_bridge.provision_key(
        name="console", user=owner, organization=org
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {console_raw}")
    assert client.get("/agentcc/api-keys/").json().get("code") != "key_scope_denied"
    ee.post(f"/agentcc/mcp/service/agent-keys/{minted['agent_key_id']}/revoke/")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert client.get("/agentcc/api-keys/").json().get("code") != "key_scope_denied"
    assert hashlib.sha256(raw.encode()).hexdigest() not in json.dumps(
        [r.metadata for r in AuditLog.objects.filter(organization=org)]
    )


@CN22_OFF_733
def test_the_console_cannot_remove_or_extend_an_agent_keys_expiry(gateway):
    """Rule 4: an Owner or Admin may revoke an agent key or bring its expiry
    forward in the console, but not remove it or move it later. Other keys
    are untouched."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    minted = _mint(ee, workspace)
    row = AgentccAPIKey.no_workspace_objects.get(gateway_key_id=minted["agent_key_id"])
    admin = _bearer(owner)
    url = f"/agentcc/api-keys/{row.id}/"
    later = (row.expires_at + timedelta(days=30)).isoformat()
    sooner = (row.expires_at - timedelta(days=30)).isoformat()

    for expires_at in (None, later):
        response = admin.patch(url, {"expires_at": expires_at}, format="json")
        assert response.status_code == 400, response.content[:300]
        assert policy.EXPIRY_FIXED in json.dumps(response.json())
    row.refresh_from_db()
    assert row.expires_at.isoformat() == minted["expires_at"].replace("Z", "+00:00")

    assert admin.patch(url, {"expires_at": sooner}, format="json").status_code == 200
    row.refresh_from_db()
    assert row.expires_at.isoformat() == sooner

    console, _ = auth_bridge.provision_key(name="console", user=owner, organization=org)
    response = admin.patch(
        f"/agentcc/api-keys/{console.id}/", {"expires_at": later}, format="json"
    )
    assert response.status_code == 200, response.content[:300]


# --- Fix round 1 (review R-CN22c-2, R-CN22c-6) -------------------------------


def _refusal(key_id, **over):
    return {
        "agent_key_id": key_id,
        "code": "agent_key_budget_exhausted",
        "method": "PIPELINE",
        "path": "responses endpoint (model gpt-4o)",
        "count": 7,
        "first_at": "2026-10-21T23:00:01Z",
        "last_at": "2026-10-21T23:00:09Z",
        **over,
    }


@CN22_OFF_733
@pytest.mark.parametrize(
    "hostile, audited_as",
    [
        (
            {"code": "key_scope_denied", "method": "X" * 40, "path": "/v1/keys"},
            {"method": "OTHER", "path": "/v1/keys"},
        ),
        (
            {"code": "key_scope_denied", "method": "GET", "path": "/v1/models\u0000x"},
            {"method": "GET", "path": "/v1/modelsx"},
        ),
        (
            {"code": "key_scope_denied", "method": "GET", "path": "/v1/" + "a" * 400},
            {"method": "GET", "path": ("/v1/" + "a" * 400)[:300]},
        ),
        (
            {"code": "key_scope_denied", "method": "GET", "path": "\u0000\r\n"},
            {"method": "GET", "path": "-"},
        ),
        ({"code": "other"}, None),
        ({"count": 0}, None),
        ("not an event", None),
    ],
    ids=[
        "long-method",
        "nul-in-path",
        "long-path",
        "control-only-path",
        "unknown-code",
        "zero-count",
        "not-an-object",
    ],
)
def test_one_malformed_refusal_never_keeps_the_batch_out_of_the_audit_log(
    gateway, hostile, audited_as
):
    """A refused request with a hostile method or path (a leaked agent key's
    request, from an older gateway that sends it as it came) is audited with
    the method and path cleaned; an event that cannot be read is counted as
    invalid. Neither keeps the batch's other refusals out of the audit log."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key_id = _mint(ee, workspace)["agent_key_id"]
    bad = _refusal(key_id, **hostile) if isinstance(hostile, dict) else hostile
    gw = APIClient()
    gw.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")

    response = gw.post(REFUSALS, {"events": [_refusal(key_id), bad]}, format="json")

    assert response.status_code == 200, response.content[:400]
    budget = _audits(org, policy.AUDIT_AGENT_KEY_BUDGET_REFUSED)
    assert len(budget) == 1 and budget[0].metadata["count"] == 7
    scope = _audits(org, policy.AUDIT_AGENT_KEY_SCOPE_REFUSED)
    if audited_as is None:
        assert response.json()["result"] == {"audited": 1, "skipped": 0, "invalid": 1}
        assert scope == []
    else:
        assert response.json()["result"] == {"audited": 2, "skipped": 0, "invalid": 0}
        assert len(scope) == 1
        assert scope[0].metadata["method"] == audited_as["method"]
        assert scope[0].metadata["path"] == audited_as["path"]


def test_a_malformed_refusals_envelope_is_still_400(gateway):
    gw = APIClient()
    gw.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
    too_many = [_refusal("key_1")] * 1001
    for body in ({}, {"events": "x"}, {"events": too_many}):
        assert gw.post(REFUSALS, body, format="json").status_code == 400, body


@CN22_OFF_733
def test_migration_gives_cn22_agent_keys_their_90_day_expiry(gateway):
    """Agent keys CN22 minted before CN22c have no expiry: the data migration
    sets created_at + 90 days on each, and leaves other keys alone."""
    import importlib

    from django.apps import apps

    backfill = importlib.import_module(
        "agentcc.migrations.0054_ee_agent_key_expiry_backfill"
    ).backfill_agent_key_expiry
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    minted = _mint(ee, workspace)
    console, _ = auth_bridge.provision_key(name="console", user=owner, organization=org)
    keys = AgentccAPIKey.no_workspace_objects
    keys.filter(pk__in=[console.pk]).update(expires_at=None)
    agent = keys.get(gateway_key_id=minted["agent_key_id"])
    keys.filter(pk=agent.pk).update(expires_at=None)
    sooner = timezone.now() + timedelta(days=3)
    other = keys.get(gateway_key_id=_mint(ee, workspace)["agent_key_id"])
    keys.filter(pk=other.pk).update(expires_at=sooner)

    backfill(apps, None)

    agent.refresh_from_db()
    assert agent.expires_at == agent.created_at + timedelta(days=90)
    other.refresh_from_db()
    assert other.expires_at == sooner  # a key with an expiry keeps it
    console.refresh_from_db()
    assert console.expires_at is None  # not an agent key
