"""The gateway's just-in-time fetch of a member's upstream token (M25x,
``mcp-upstream-token-v1``): who may ask, what is answered, and what is kept.

Each test is one property. The vendor's token endpoint is ``FakeVendor``
(refresh tokens rotate and work once), answered in place of the guarded
fetcher, so no test reaches a network.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone
from jsonschema import Draft202012Validator
from structlog.testing import capture_logs

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_upstream_oauth import AgentccMCPUpstreamTokenAudit
from agentcc.services import gateway_client
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services.credential_manager import decrypt_token, encrypt_token
from agentcc.tests.mcp_upstream_oauth_helpers import (
    ADMIN_TOKEN,
    CALLBACK,
    CLIENT_METADATA,
    INTERNAL_SECRET,
    ISSUER,
    SERVER_ID,
    FakeVendor,
    member,
    move_server,
    seed_config,
)

pytestmark = pytest.mark.django_db

TOKEN_PATH = "/agentcc/internal/mcp/upstream-token/"
HEADER = "HTTP_X_AGENTCC_MCP_INTERNAL"
ORIGIN = "https://mcp.vendor.example:443"
EXAMPLES = Path(__file__).resolve().parents[3] / "api_contracts/gateway/examples"
Status = AgentccMCPCredential.Status


@pytest.fixture(autouse=True)
def _settings(settings, monkeypatch):
    settings.AGENTCC_MCP_CONNECT_CALLBACK_URL = CALLBACK
    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, INTERNAL_SECRET)
    monkeypatch.setattr(gateway_client, "AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setattr(connect.gateway_credentials, "purge", lambda *args: True)


@pytest.fixture
def vendor():
    fake = FakeVendor()
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        yield fake


@pytest.fixture
def config(organization, user):
    return seed_config(organization)


def _connected(org, user, vendor, *, expires_in=timedelta(minutes=15), refresh=True):
    """``user``'s connected row for the server, as a completed sign-in leaves it."""
    tokens = vendor.grant()
    return AgentccMCPCredential.objects.create(
        organization=org,
        server_id=SERVER_ID,
        scope="user",
        user=user,
        kind="oauth",
        encrypted_secret=encrypt_token(tokens["access_token"]),
        encrypted_refresh=encrypt_token(tokens["refresh_token"]) if refresh else None,
        token_expires_at=None if expires_in is None else timezone.now() + expires_in,
        oauth_issuer=ISSUER,
        oauth_client_id=CLIENT_METADATA,
        scopes=["mcp"],
        origin=ORIGIN,
        created_by=user,
    )


def _ask(org, user, server_id=SERVER_ID, *, secret=INTERNAL_SECRET, body=None, **extra):
    headers = {HEADER: secret} if secret is not None else {}
    payload = (
        body
        if body is not None
        else {"org_id": str(org.pk), "user_id": str(user.pk), "server_id": server_id}
    )
    return Client().post(
        TOKEN_PATH,
        json.dumps(payload),
        content_type="application/json",
        **headers,
        **extra,
    )


def _schema(definition):
    schema = json.loads((EXAMPLES / "mcp-upstream-token-v1.schema.json").read_text())
    return Draft202012Validator(
        {"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]}
    )


# --- Property: only the gateway's own secret asks --------------------------------


def test_internal_token_requires_own_secret_not_admin_token(
    organization, user, config, vendor, monkeypatch
):
    _connected(organization, user, vendor)

    for secret, extra in (
        (None, {}),
        ("wrong-" + INTERNAL_SECRET, {}),
        (ADMIN_TOKEN, {}),
        (None, {"HTTP_AUTHORIZATION": f"Bearer {ADMIN_TOKEN}"}),
        (None, {"HTTP_AUTHORIZATION": f"Bearer {INTERNAL_SECRET}"}),
    ):
        refused = _ask(organization, user, secret=secret, **extra)
        assert refused.status_code == 401, (secret, extra)
        assert refused.json() == {"error": "unauthorized"}
        assert refused["Cache-Control"] == "no-store"
    assert _ask(organization, user).status_code == 200

    # A secret set to the admin token, or too short, opens nothing.
    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, ADMIN_TOKEN)
    assert _ask(organization, user, secret=ADMIN_TOKEN).status_code == 401
    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, "short-secret")
    assert _ask(organization, user, secret="short-secret").status_code == 401
    monkeypatch.delenv(connect.INTERNAL_SECRET_ENV)
    assert _ask(organization, user, secret="").status_code == 401
    # Refused callers are never audited as fetches, and learn nothing.
    assert AgentccMCPUpstreamTokenAudit.objects.filter(outcome="ok").count() == 1
    assert AgentccMCPUpstreamTokenAudit.objects.count() == 1


def test_internal_token_takes_post_only(organization, user):
    assert Client().get(TOKEN_PATH, **{HEADER: INTERNAL_SECRET}).status_code == 405


# --- Property: the answer is the contract's, and is never cached or logged ------


def test_internal_token_matches_golden(organization, user, config, vendor):
    golden = json.loads((EXAMPLES / "mcp-upstream-token-v1.json").read_text())
    row = _connected(organization, user, vendor)

    success = _ask(organization, user)
    refusal = _ask(organization, user, server_id="shared-one")
    from_golden = _ask(organization, user, body=golden["request"])

    assert success.status_code == refusal.status_code == from_golden.status_code == 200
    _schema("Request").validate(golden["request"])
    _schema("Success").validate(success.json())
    _schema("Refusal").validate(refusal.json())
    _schema("Refusal").validate(from_golden.json())
    assert success.json()["access_token"] == decrypt_token(row.encrypted_secret)
    assert success.json()["credential_version"] == row.version
    assert refusal.json() == {
        "error": "no_credential",
        "connect_hint": "https://api.example.test/agentcc/mcp/connect/shared-one/start/",
    }


@pytest.mark.parametrize(
    "body",
    [
        {"org_id": "o", "user_id": "u"},
        {"org_id": "o", "user_id": "u", "server_id": "s", "extra": "x"},
        {"org_id": "", "user_id": "u", "server_id": "s"},
        {"org_id": 1, "user_id": "u", "server_id": "s"},
        ["not", "an", "object"],
    ],
    ids=["missing", "extra", "empty", "not-a-string", "list"],
)
def test_internal_token_refuses_a_request_outside_the_contract(
    organization, user, body
):
    response = _ask(organization, user, body=body)

    assert (response.status_code, response.json()) == (
        400,
        {"error": "invalid_request"},
    )
    assert not AgentccMCPUpstreamTokenAudit.objects.exists()


def test_internal_token_no_store_not_logged_audited(organization, user, config, vendor):
    row = _connected(organization, user, vendor)
    token = decrypt_token(row.encrypted_secret)
    refresh = decrypt_token(row.encrypted_refresh)

    with capture_logs() as logs:
        response = _ask(organization, user)
        _ask(organization, user, server_id="shared-one")

    assert response["Cache-Control"] == "no-store"
    assert response["Pragma"] == "no-cache"
    assert refresh not in response.content.decode()
    assert token not in repr(logs)
    audits = list(AgentccMCPUpstreamTokenAudit.objects.order_by("created_at"))
    assert [(a.org_id, a.user_id, a.server_id, a.outcome) for a in audits] == [
        (str(organization.pk), str(user.pk), SERVER_ID, "ok"),
        (str(organization.pk), str(user.pk), "shared-one", "no_credential"),
    ]
    assert audits[0].credential_id == row.pk
    for audit in audits:
        assert token not in repr(audit.__dict__)


# --- Property: only the member's own token, for the server it was issued for ---


def test_internal_token_serves_only_the_members_own_token(
    organization, user, workspace, config, vendor
):
    other, _ = member(organization, workspace)
    _connected(organization, user, vendor)
    foreign = seed_config_org_with_same_server()

    assert _ask(organization, other).json()["error"] == "no_credential"
    assert _ask(foreign, user).json()["error"] == "no_credential"
    for body in (
        {"org_id": "not-a-uuid", "user_id": str(user.pk), "server_id": SERVER_ID},
        {"org_id": str(organization.pk), "user_id": "user_42", "server_id": SERVER_ID},
        {
            "org_id": str(organization.pk),
            "user_id": str(user.pk),
            "server_id": "BAD ID",
        },
    ):
        assert _ask(organization, user, body=body).json()["error"] == "no_credential"


def seed_config_org_with_same_server():
    from accounts.models import Organization

    org = Organization.objects.create(name="M25x Other Org")
    seed_config(org)
    return org


def test_a_server_moved_to_another_origin_gets_no_token(
    organization, user, config, vendor
):
    _connected(organization, user, vendor)
    move_server(organization, "https://mcp.elsewhere.example/mcp")

    answer = _ask(organization, user).json()

    assert answer["error"] == "needs_reauth"
    assert "access_token" not in answer


def test_a_server_no_longer_per_user_gets_no_token(organization, user, config, vendor):
    _connected(organization, user, vendor)
    from agentcc.models.org_config import AgentccOrgConfig

    active = AgentccOrgConfig.no_workspace_objects.get(organization=organization)
    active.mcp["servers"][SERVER_ID]["credential"] = {"mode": "shared"}
    active.save(update_fields=["mcp"])

    assert _ask(organization, user).json()["error"] == "no_credential"


def test_a_member_who_left_the_org_gets_no_token(
    organization, user, workspace, config, vendor
):
    other, _ = member(organization, workspace)
    _connected(organization, other, vendor)
    other.organization_memberships.update(is_active=False)

    assert _ask(organization, other).json() == {"error": "revoked"}


def test_a_disconnected_account_answers_revoked(organization, user, config, vendor):
    row = _connected(organization, user, vendor)
    row.status = Status.REVOKED
    row.save(update_fields=["status"])

    assert _ask(organization, user).json()["error"] == "revoked"


# --- Property: refresh before expiry, once; a refused refresh needs a reconnect --


def test_a_token_near_expiry_is_refreshed_first(organization, user, config, vendor):
    row = _connected(organization, user, vendor, expires_in=timedelta(seconds=90))
    old_refresh = decrypt_token(row.encrypted_refresh)

    answer = _ask(organization, user).json()

    row.refresh_from_db()
    new = vendor.issued[-1]
    assert answer["access_token"] == new["access_token"]
    assert answer["credential_version"] == row.version == 2
    assert decrypt_token(row.encrypted_refresh) == new["refresh_token"]
    assert new["refresh_token"] not in json.dumps(answer)
    assert vendor.refreshes == [
        {
            "grant_type": "refresh_token",
            "refresh_token": old_refresh,
            "client_id": CLIENT_METADATA,
            "resource": "https://mcp.vendor.example/mcp",
        }
    ]
    # A second fetch serves the refreshed token without refreshing again.
    assert _ask(organization, user).json()["access_token"] == new["access_token"]
    assert len(vendor.refreshes) == 1


def test_failed_refresh_marks_needs_reauth(organization, user, config, vendor):
    row = _connected(organization, user, vendor, expires_in=timedelta(seconds=30))
    vendor.refresh_fails = True

    first = _ask(organization, user).json()
    second = _ask(organization, user).json()

    row.refresh_from_db()
    assert first["error"] == second["error"] == "needs_reauth"
    assert first["connect_hint"].endswith(f"/agentcc/mcp/connect/{SERVER_ID}/start/")
    assert row.status == Status.NEEDS_REAUTH
    assert row.encrypted_refresh is not None  # the vault keeps the entry
    assert len(vendor.refreshes) == 1


def test_an_expired_token_without_a_refresh_token_answers_expired(
    organization, user, config, vendor
):
    row = _connected(
        organization, user, vendor, expires_in=-timedelta(seconds=1), refresh=False
    )

    assert _ask(organization, user).json()["error"] == "expired"
    row.refresh_from_db()
    assert row.status == Status.EXPIRED
    assert vendor.refreshes == []


def test_a_token_without_an_expiry_is_answered_with_a_bounded_one(
    organization, user, config, vendor
):
    _connected(organization, user, vendor, expires_in=None)

    answer = _ask(organization, user).json()

    expires = datetime.fromisoformat(answer["expires_at"].replace("Z", "+00:00"))
    assert timezone.now() < expires <= timezone.now() + timedelta(minutes=15)
