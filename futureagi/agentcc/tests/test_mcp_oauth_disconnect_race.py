"""Deterministic consumed-state/Disconnect interleavings; no vendor sockets.

The exchange seam runs Disconnect after complete() has committed state deletion,
then resumes the real exchange against FakeVendor. No sleep or scheduling luck
is needed, and the token store, cleanup, DB locks and HTTP DELETE remain real.
"""

from unittest.mock import patch

import pytest

from agentcc.models.mcp_cleanup_receipt import AgentccMCPCleanupReceipt as Receipt
from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_upstream_tokens
from agentcc.services.credential_manager import decrypt_token
from agentcc.tests.mcp_upstream_oauth_helpers import (
    ISSUER,
    SERVER_ID,
    _response,
    member,
)
from agentcc.tests.test_mcp_upstream_oauth import _settings as _settings
from agentcc.tests.test_mcp_upstream_oauth import config as config
from agentcc.tests.test_mcp_upstream_oauth import purges as purges
from agentcc.tests.test_mcp_upstream_oauth import vendor as vendor

pytestmark = pytest.mark.django_db(transaction=True)


def _start(org, user):
    return connect.start(org, user, SERVER_ID, individual_authenticated=True)


def _finish(started, vendor):
    code, state = vendor.authorize(started.authorize_url)
    return connect.complete(
        {"code": code, "state": state, "iss": ISSUER},
        {started.cookie_name: started.cookie_value},
    )


def _accounts(org, user):
    return Credential.objects.filter(
        organization=org, user=user, server_id=SERVER_ID, scope="user", kind="oauth"
    )


@pytest.mark.parametrize("existing", [False, True], ids=["first-sign-in", "reconnect"])
@pytest.mark.parametrize(
    "replace", [False, True], ids=["stay-disconnected", "new-sign-in"]
)
def test_consumed_callback_cannot_undo_disconnect(
    organization, user, auth_client, config, vendor, purges, existing, replace
):
    if existing:
        assert _finish(_start(organization, user), vendor).ok
    started = _start(organization, user)
    exchange = connect._exchange_code
    interleaved = False
    replacement = None

    def disconnect_during_exchange(row, code, org):
        nonlocal interleaved, replacement
        if interleaved:
            return exchange(row, code, org)
        interleaved = True
        assert not AgentccMCPConnectState.objects.filter(
            state_hash=row.state_hash
        ).exists(), "callback must already have consumed its state"
        response = auth_client.delete(f"/agentcc/mcp/connect/{SERVER_ID}/")
        assert response.status_code == (200 if existing else 404)
        if existing:
            assert response.json()["result"]["cleanup_pending"] is False
            assert response.json()["result"]["gateway_synced"] is True
        assert not _accounts(org, user).filter(status="active").exists()
        if replace:
            assert _finish(_start(org, user), vendor).ok
            replacement = _accounts(org, user).get(status="active")
        return exchange(row, code, org)

    with patch.object(connect, "_exchange_code", disconnect_during_exchange):
        result = _finish(started, vendor)

    assert interleaved
    assert not result.ok, (
        "a callback consumed before Disconnect reconnected the account"
    )
    assert result.reason == "connect_cancelled"
    assert not AgentccMCPConnectState.objects.exists()
    active = _accounts(organization, user).filter(status="active")
    if replace:
        assert list(active.values_list("pk", flat=True)) == [replacement.pk]
        replacement.refresh_from_db()
        assert (
            decrypt_token(replacement.encrypted_secret)
            == vendor.issued[-2]["access_token"]
        )
        assert replacement.version == 1
    else:
        assert not active.exists()
        token = mcp_upstream_tokens.upstream_token(
            str(organization.pk), str(user.pk), SERVER_ID
        )
        assert token.outcome == "revoked"
    # The discarded grant is revoked, never published, and never evicts the
    # replacement. Its terminal cleanup erases both tokens and leaves a receipt.
    discarded = _accounts(organization, user).order_by("-created_at").first()
    assert discarded.status == "revoked"
    assert discarded.oauth_revoke_outcome == "confirmed"
    assert not discarded.oauth_revoke_pending and not discarded.gateway_purge_pending
    assert discarded.encrypted_refresh is None
    assert decrypt_token(discarded.encrypted_secret) == "revoked"
    assert vendor.revoked[-1]["token"] == vendor.issued[-1]["refresh_token"]
    assert Receipt.objects.filter(
        credential=discarded,
        trigger="cancelled",
        step="vendor_revocation",
        outcome="confirmed",
    ).exists()
    assert len(purges) == (2 if existing else 0) + int(replace)


def test_cancelled_grant_retains_retryable_vendor_cleanup(
    organization, user, auth_client, config, vendor, purges
):
    assert _finish(_start(organization, user), vendor).ok
    started = _start(organization, user)
    exchange = connect._exchange_code

    def disconnect_during_exchange(row, code, org):
        assert (
            auth_client.delete(f"/agentcc/mcp/connect/{SERVER_ID}/").status_code == 200
        )
        return exchange(row, code, org)

    def fetch(url, **kwargs):
        if url.endswith("/revoke") and len(vendor.issued) == 2:
            return _response(503)
        return vendor.fetch(url, **kwargs)

    with (
        patch.object(connect, "_exchange_code", disconnect_during_exchange),
        patch.object(connect.mcp_safe_fetch, "fetch", fetch),
    ):
        result = _finish(started, vendor)

    assert not result.ok
    discarded = _accounts(organization, user).get(oauth_revoke_pending=True)
    assert discarded.status == "revoked" and not discarded.gateway_purge_pending
    assert (
        decrypt_token(discarded.encrypted_refresh) == vendor.issued[-1]["refresh_token"]
    )
    assert Receipt.objects.filter(
        credential=discarded, trigger="cancelled", outcome="pending"
    ).exists()
    assert auth_client.delete(f"/agentcc/mcp/connect/{SERVER_ID}/").status_code == 200
    discarded.refresh_from_db()
    assert not discarded.oauth_revoke_pending
    assert discarded.encrypted_refresh is None


@pytest.mark.parametrize("other_identity", ["user", "server"])
def test_disconnect_cancels_only_the_matching_identity(
    organization, user, workspace, config, vendor, purges, other_identity
):
    other, _ = member(organization, workspace)
    started = _start(organization, user)
    exchange = connect._exchange_code

    def disconnect_during_exchange(row, code, org):
        connect.disconnect(
            org,
            other if other_identity == "user" else user,
            SERVER_ID if other_identity == "user" else "another-server",
        )
        return exchange(row, code, org)

    with patch.object(connect, "_exchange_code", disconnect_during_exchange):
        result = _finish(started, vendor)

    assert result.ok
    assert _accounts(organization, user).get().status == "active"
    assert vendor.revoked == []
