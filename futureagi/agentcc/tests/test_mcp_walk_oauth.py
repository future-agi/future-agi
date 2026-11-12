"""WALK-OAUTH regressions. Database tests; all vendor/gateway I/O is faked."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_upstream_tokens as tokens
from agentcc.services.credential_manager import decrypt_token
from agentcc.tests.mcp_upstream_oauth_helpers import ISSUER, SERVER_URL, _response
from agentcc.tests.test_mcp_shared_oauth import (  # fixture reuse, no live I/O
    CRM, _active, _settings, config, purges, pushes, vendor,
)

pytestmark = pytest.mark.django_db


def start(client, vendor, mode):
    server = CRM if mode == "shared" else "issues"
    response = client.post(
        f"/agentcc/mcp/connect/{server}/start/", {"mode": mode}, format="json"
    )
    assert response.status_code == 200, response.content
    code, state = vendor.authorize(response.json()["result"]["authorize_url"])
    name = connect.cookie_name(state)
    return {"code": code, "state": state, "iss": ISSUER}, {
        name: response.cookies[name].value
    }


def connected(client, vendor, mode):
    params, cookies = start(client, vendor, mode)
    assert connect.complete(params, cookies).ok
    return Credential.objects.get(scope=mode, status="active", kind="oauth")


def ask(org, user, mode):
    if mode == "shared":
        return tokens.shared_token(str(org.pk), CRM)
    return tokens.upstream_token(str(org.pk), str(user.pk), "issues")


@pytest.mark.parametrize("mode", ["user", "shared"])
def test_root_resource_survives_first_refresh(
    auth_client, organization, user, config, vendor, purges, pushes, mode
):
    """REST-01: a strict provider binds refresh to the original root grant."""
    resource = "https://mcp.vendor.example"
    original = vendor.fetch

    def strict(url, **kwargs):
        if url == resource + "/.well-known/oauth-protected-resource/mcp":
            return _response(404)
        if url == resource + "/.well-known/oauth-protected-resource":
            return _response(200, {"resource": resource, "authorization_servers": [ISSUER]})
        form = kwargs.get("data") or {}
        if form.get("grant_type") == "refresh_token":
            if form.get("resource") != resource:
                return _response(400, {"error": "invalid_target"})
        return original(url, **kwargs)

    with patch.object(connect.mcp_safe_fetch, "fetch", strict):
        row = connected(auth_client, vendor, mode)
        row.token_expires_at = timezone.now() + timedelta(seconds=90)
        row.save(update_fields=["token_expires_at"])
        answer = ask(organization, user, mode)
    assert answer.outcome == "refreshed"
    assert vendor.refreshes[-1]["resource"] == resource
    row.refresh_from_db()
    assert row.status == "active"


@pytest.mark.parametrize("mode", ["user", "shared"])
@pytest.mark.parametrize("remaining", [90, -1])
@pytest.mark.parametrize("failure", ["timeout", 429, 503, "malformed"])
def test_transient_refresh_recovers_without_new_consent(
    auth_client, organization, user, config, vendor, purges, pushes,
    mode, remaining, failure,
):
    """REST-02: availability failures never revoke a still-valid grant."""
    row = connected(auth_client, vendor, mode)
    access, refresh = row.encrypted_secret, row.encrypted_refresh
    row.token_expires_at = timezone.now() + timedelta(seconds=remaining)
    row.save(update_fields=["token_expires_at"])
    purges.clear()
    if failure == "timeout":
        response = None
    elif failure == "malformed":
        response = _response(200, raw=b"not-json")
    else:
        response = _response(failure, {"error": "temporarily_unavailable"})
    with patch.object(connect.connect_clients, "post", return_value=response):
        answer = ask(organization, user, mode)
    row.refresh_from_db()
    assert row.status == "active"
    assert (row.encrypted_secret, row.encrypted_refresh, row.version) == (access, refresh, 1)
    assert purges == []
    if remaining > 0:
        assert answer.body()["access_token"] == decrypt_token(access)
    else:
        assert answer.body() == {"error": "unavailable"}
    recovered = ask(organization, user, mode)
    assert recovered.outcome == "refreshed"
    assert recovered.body()["credential_version"] == 2


@pytest.mark.parametrize("mode", ["user", "shared"])
def test_authoritative_invalid_grant_still_requires_reauthentication(
    auth_client, organization, user, config, vendor, purges, pushes, mode
):
    row = connected(auth_client, vendor, mode)
    row.token_expires_at = timezone.now() + timedelta(seconds=90)
    row.save(update_fields=["token_expires_at"])
    vendor.refresh_fails = True
    assert ask(organization, user, mode).outcome == "needs_reauth"
    row.refresh_from_db()
    assert row.status == "needs_reauth"


def test_shared_callback_cannot_undo_acknowledged_disconnect(
    auth_client, organization, user, config, vendor, purges, pushes
):
    """REST-04: deterministically disconnect inside the provider exchange."""
    old = connected(auth_client, vendor, "shared")
    params, cookies = start(auth_client, vendor, "shared")
    exchange = connect._exchange_code

    def disconnect_during_exchange(state, code, org):
        # The public callback is already single-use even while exchange is pending.
        assert not AgentccMCPConnectState.objects.filter(state_hash=connect._hash(params["state"])).exists()
        response = auth_client.delete(f"/agentcc/mcp/connect/{CRM}/?mode=shared")
        assert response.status_code == 200, response.content
        old.refresh_from_db()
        assert old.status == "revoked"
        return exchange(state, code, org)

    with patch.object(connect, "_exchange_code", side_effect=disconnect_during_exchange):
        with patch.object(connect, "_revoke_upstream", return_value=False):
            result = connect.complete(params, cookies)
    assert not result.ok and result.reason == "connect_cancelled"
    assert not Credential.objects.filter(scope="shared", status="active").exists()
    assert _active(organization).mcp["servers"][CRM]["credential"]["state"] == "revoked"
    discarded = Credential.objects.filter(scope="shared").exclude(pk=old.pk).get()
    assert discarded.status == "revoked"
    assert discarded.oauth_revoke_pending
    assert not discarded.gateway_purge_pending
    assert decrypt_token(discarded.encrypted_refresh) == vendor.issued[-1]["refresh_token"]
    assert not AgentccMCPConnectState.objects.exists()
    assert not connect.complete(params, cookies).ok
    # A later explicit start still succeeds; a discarded grant never occupies its slot.
    assert connected(auth_client, vendor, "shared").status == "active"


def test_shared_callback_remains_single_use_during_exchange(
    auth_client, organization, user, config, vendor, purges, pushes
):
    params, cookies = start(auth_client, vendor, "shared")
    exchange = connect._exchange_code

    def replay(state, code, org):
        assert not connect.complete(params, cookies).ok
        return exchange(state, code, org)

    with patch.object(connect, "_exchange_code", side_effect=replay):
        assert connect.complete(params, cookies).ok
    assert Credential.objects.filter(scope="shared", status="active").count() == 1
    assert not AgentccMCPConnectState.objects.exists()
