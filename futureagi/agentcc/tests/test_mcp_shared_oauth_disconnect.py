# ruff: noqa: F811 -- pytest fixtures are imported from test_mcp_shared_oauth and injected by parameter name.
"""Shared Disconnect fences the entire sign-in, including consumed callbacks.

Deterministic interleavings use the discovery/exchange seams, real DB writes and
HTTP Disconnect, and the existing fake vendor/config-push/purge fixtures. No
network, sleeps or thread scheduling are needed. USER-mode stays independent.
"""

from unittest.mock import patch

import pytest
from django.db import transaction

from agentcc.models.mcp_cleanup_receipt import AgentccMCPCleanupReceipt as Receipt
from agentcc.models.mcp_connect_generation import (
    AgentccMCPConnectGeneration,
    AgentccMCPSharedConnectGeneration,
)
from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
from agentcc.services import mcp_oauth_cancellation as cancellation
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services.org_config_writer import lock_org
from agentcc.tests.mcp_upstream_oauth_helpers import ISSUER, _response, member
from agentcc.tests.test_mcp_shared_oauth import CRM, _active, _shared
from agentcc.tests.test_mcp_shared_oauth import _settings as _settings
from agentcc.tests.test_mcp_shared_oauth import config as config
from agentcc.tests.test_mcp_shared_oauth import purges as purges
from agentcc.tests.test_mcp_shared_oauth import pushes as pushes
from agentcc.tests.test_mcp_shared_oauth import vendor as vendor
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db(transaction=True)


def _start(org, user, server_id=CRM, mode="shared"):
    return connect.start(org, user, server_id, mode=mode, individual_authenticated=True)


def _finish(started, vendor):
    code, state = vendor.authorize(started.authorize_url)
    return connect.complete(
        {"code": code, "state": state, "iss": ISSUER},
        {started.cookie_name: started.cookie_value},
    )


def _disconnect(client, server_id=CRM, mode="shared"):
    return client.delete(f"/agentcc/mcp/connect/{server_id}/?mode={mode}")


@pytest.mark.parametrize(
    "server_id",
    ["s" * 65, "s" * 49, "fagi." + "s" * 49, "Invalid-server"],
    ids=["overlong-column", "overlong-id", "overlong-prefixed-id", "invalid-syntax"],
)
def test_shared_disconnect_rejects_malformed_server_id_before_service(
    organization, auth_client, config, server_id
):
    with patch.object(connect, "disconnect", wraps=connect.disconnect) as disconnect:
        response = _disconnect(auth_client, server_id=server_id)

    assert response.status_code == 404
    disconnect.assert_not_called()
    assert not AgentccMCPSharedConnectGeneration.objects.filter(
        organization=organization
    ).exists()


@pytest.mark.parametrize(
    "server_id", ["s" * 48, "fagi." + "s" * 48], ids=["max-id", "max-prefixed-id"]
)
def test_shared_disconnect_accepts_valid_unknown_server_id_and_advances_generation(
    organization, auth_client, config, server_id
):
    assert not Credential.objects.filter(
        organization=organization, server_id=server_id
    ).exists()

    for generation in (1, 2):
        assert _disconnect(auth_client, server_id=server_id).status_code == 404
        assert (
            AgentccMCPSharedConnectGeneration.objects.get(
                organization=organization, server_id=server_id
            ).generation
            == generation
        )


@pytest.mark.parametrize("existing", [False, True], ids=["first-sign-in", "reconnect"])
@pytest.mark.parametrize("replace", [False, True], ids=["disconnected", "new-sign-in"])
def test_consumed_shared_callback_cannot_undo_disconnect(
    organization, user, auth_client, config, vendor, purges, pushes, existing, replace
):
    if existing:
        assert _finish(_start(organization, user), vendor).ok
    started = _start(organization, user)
    exchange = connect._exchange_code
    interleaved = False
    replacement = None
    after_disconnect_pushes = None

    def disconnect_during_exchange(row, code, org):
        nonlocal interleaved, replacement, after_disconnect_pushes
        if interleaved:
            return exchange(row, code, org)
        interleaved = True
        response = _disconnect(auth_client)
        assert response.status_code == (200 if existing else 404)
        if existing:
            assert response.json()["result"]["cleanup_pending"] is False
            assert response.json()["result"]["gateway_synced"] is True
        assert not AgentccMCPConnectState.objects.filter(pk=started.connect_id).exists()
        assert not _shared(org).filter(status="active").exists()
        if replace:
            assert _finish(_start(org, user), vendor).ok
            replacement = _shared(org).get(status="active")
        after_disconnect_pushes = len(pushes)
        return exchange(row, code, org)

    with patch.object(connect, "_exchange_code", disconnect_during_exchange):
        result = _finish(started, vendor)

    assert interleaved
    assert (result.ok, result.reason) == (False, "connect_cancelled")
    assert not AgentccMCPConnectState.objects.exists()
    assert len(pushes) == after_disconnect_pushes
    active = _shared(organization).filter(status="active")
    reference = _active(organization).mcp["servers"][CRM]["credential"]
    if replace:
        assert list(active.values_list("pk", flat=True)) == [replacement.pk]
        replacement.refresh_from_db()
        assert replacement.version == 1
        assert reference["credential_id"] == str(replacement.pk)
        assert reference["state"] == "active"
    else:
        assert not active.exists()
        assert reference.get("state") == ("revoked" if existing else None)
    discarded = _shared(organization).latest("created_at")
    assert discarded.status == "revoked"
    assert discarded.oauth_revoke_outcome == "confirmed"
    assert not discarded.oauth_revoke_pending and not discarded.gateway_purge_pending
    assert discarded.encrypted_refresh is None
    assert len(vendor.revoked) == 1 + int(existing)
    assert Receipt.objects.filter(
        credential=discarded,
        trigger="cancelled",
        step="vendor_revocation",
        outcome="pending",
        reason="recorded",
    ).exists()
    assert Receipt.objects.filter(
        credential=discarded, step="vendor_revocation", outcome="confirmed"
    ).exists()
    # Never-published cleanup must not evict the replacement's gateway cache.
    assert len(purges) == (2 if existing else 0) + int(replace)


def test_shared_callback_without_disconnect_succeeds_once(
    organization, user, config, vendor, purges, pushes
):
    started = _start(organization, user)
    code, state = vendor.authorize(started.authorize_url)
    params = {"code": code, "state": state, "iss": ISSUER}
    cookies = {started.cookie_name: started.cookie_value}

    result = connect.complete(params, cookies)
    replay = connect.complete(params, cookies)

    assert (result.ok, result.reason) == (True, "connected")
    assert (replay.ok, replay.reason) == (False, "invalid_state")
    row = _shared(organization).get(status="active")
    assert row.user_id is None and row.created_by_id == user.pk
    assert _active(organization).mcp["servers"][CRM]["credential"][
        "credential_id"
    ] == str(row.pk)
    assert not AgentccMCPConnectState.objects.exists()
    assert len(pushes) == len(purges) == 1
    assert vendor.revoked == []


@pytest.mark.parametrize("disconnects", [1, 2])
def test_disconnect_without_credentials_advances_generation_and_new_start_succeeds(
    organization, user, auth_client, config, vendor, purges, pushes, disconnects
):
    for _ in range(disconnects):
        assert _disconnect(auth_client).status_code == 404
    started = _start(organization, user)
    state = AgentccMCPConnectState.objects.get(pk=started.connect_id)

    assert state.cancellation_generation == disconnects
    assert _finish(started, vendor).ok
    assert _shared(organization).get().status == "active"
    assert not AgentccMCPConnectGeneration.objects.exists()


def test_disconnect_during_shared_discovery_refuses_the_older_start(
    organization, user, auth_client, config, vendor, purges, pushes
):
    assert _finish(_start(organization, user), vendor).ok
    discover = connect.discover

    def disconnect_during_discovery(url):
        assert _disconnect(auth_client).status_code == 200
        return discover(url)

    with patch.object(connect, "discover", disconnect_during_discovery):
        with pytest.raises(connect.ConnectRefused) as refused:
            _start(organization, user)

    assert refused.value.code == "connect_cancelled"
    assert not AgentccMCPConnectState.objects.exists()
    assert not _shared(organization).filter(status="active").exists()
    assert len(vendor.issued) == 1
    # Only a genuinely new start may join the post-Disconnect generation.
    assert _finish(_start(organization, user), vendor).ok


def test_generation_check_refuses_publication_even_with_a_retained_state_row(
    organization, user, config, vendor, purges, pushes
):
    started = _start(organization, user)
    exchange = connect._exchange_code

    def advance_generation_during_exchange(row, code, org):
        # Exercise the durable authority independently of state-row deletion.
        with transaction.atomic():
            lock_org(org.pk)
            cancellation.cancel_shared(org.pk, CRM)
        assert AgentccMCPConnectState.objects.filter(pk=started.connect_id).exists()
        return exchange(row, code, org)

    with patch.object(connect, "_exchange_code", advance_generation_during_exchange):
        result = _finish(started, vendor)

    assert (result.ok, result.reason) == (False, "connect_cancelled")
    assert not _shared(organization).filter(status="active").exists()
    assert pushes == purges == []
    assert _shared(organization).get().oauth_revoke_outcome == "confirmed"


@pytest.mark.parametrize("revocation", ["retry", "unsupported"])
def test_cancelled_shared_grant_keeps_a_durable_vendor_cleanup_record(
    organization, user, auth_client, config, vendor, purges, pushes, revocation
):
    started = _start(organization, user)
    exchange = connect._exchange_code
    if revocation == "unsupported":
        vendor.revocation = False

    def disconnect_during_exchange(row, code, org):
        assert _disconnect(auth_client).status_code == 404
        return exchange(row, code, org)

    def fetch(url, **kwargs):
        if url.endswith("/revoke"):
            return _response(503)
        return vendor.fetch(url, **kwargs)

    with (
        patch.object(connect, "_exchange_code", disconnect_during_exchange),
        patch.object(connect.mcp_safe_fetch, "fetch", fetch),
    ):
        result = _finish(started, vendor)

    assert (result.ok, result.reason) == (False, "connect_cancelled")
    discarded = _shared(organization).get()
    assert discarded.status == "revoked" and not discarded.gateway_purge_pending
    assert discarded.oauth_revoke_pending == (revocation == "retry")
    assert discarded.oauth_revoke_outcome == (
        "pending" if revocation == "retry" else "unconfirmed"
    )
    assert (discarded.encrypted_refresh is not None) == (revocation == "retry")
    assert Receipt.objects.filter(
        credential=discarded,
        trigger="cancelled",
        step="vendor_revocation",
        outcome="pending",
        reason="recorded",
    ).exists()
    assert Receipt.objects.filter(
        credential=discarded,
        step="vendor_revocation",
        outcome=discarded.oauth_revoke_outcome,
    ).exists()
    assert pushes == purges == []
    if revocation == "retry":
        assert _disconnect(auth_client).status_code == 200
        discarded.refresh_from_db()
        assert not discarded.oauth_revoke_pending
        assert discarded.encrypted_refresh is None
        assert discarded.oauth_revoke_outcome == "confirmed"


def test_another_admins_disconnect_cancels_the_shared_start(
    organization, user, workspace, config, vendor, purges, pushes
):
    _, other_client = member(organization, workspace, level=Level.ADMIN)
    assert _finish(_start(organization, user), vendor).ok
    started = _start(organization, user)
    exchange = connect._exchange_code

    def disconnect_during_exchange(row, code, org):
        assert _disconnect(other_client).status_code == 200
        return exchange(row, code, org)

    with patch.object(connect, "_exchange_code", disconnect_during_exchange):
        result = _finish(started, vendor)

    assert (result.ok, result.reason) == (False, "connect_cancelled")
    assert not _shared(organization).filter(status="active").exists()


@pytest.mark.parametrize("other_identity", ["organization", "server", "user-mode"])
def test_shared_disconnect_only_cancels_the_matching_shared_identity(
    organization, user, config, vendor, purges, pushes, other_identity
):
    from accounts.models import Organization

    other_org = Organization.objects.create(name="shared-disconnect-other")
    started = _start(organization, user)
    exchange = connect._exchange_code

    def disconnect_during_exchange(row, code, org):
        connect.disconnect(
            other_org if other_identity == "organization" else org,
            user,
            "another-server" if other_identity == "server" else CRM,
            mode="user" if other_identity == "user-mode" else "shared",
        )
        return exchange(row, code, org)

    with patch.object(connect, "_exchange_code", disconnect_during_exchange):
        result = _finish(started, vendor)

    assert result.ok
    assert _shared(organization).get().status == "active"
    assert vendor.revoked == []


@pytest.mark.parametrize("disconnect_mode", [None, "shared", "user"])
def test_user_callback_keeps_its_existing_cancellation_behavior(
    organization, user, config, vendor, purges, pushes, disconnect_mode
):
    started = _start(organization, user, server_id="issues", mode="user")
    exchange = connect._exchange_code

    def disconnect_during_exchange(row, code, org):
        assert not AgentccMCPConnectState.objects.filter(pk=started.connect_id).exists()
        if disconnect_mode is not None:
            connect.disconnect(org, user, "issues", mode=disconnect_mode)
        return exchange(row, code, org)

    with patch.object(connect, "_exchange_code", disconnect_during_exchange):
        result = _finish(started, vendor)

    cancelled = disconnect_mode == "user"
    assert (result.ok, result.reason) == (
        (False, "connect_cancelled") if cancelled else (True, "connected")
    )
    accounts = Credential.objects.filter(
        organization=organization, user=user, server_id="issues", scope="user"
    )
    assert accounts.filter(status="active").exists() == (not cancelled)
    assert not Credential.objects.filter(scope="shared").exists()
    assert pushes == []
    if cancelled:
        assert _finish(
            _start(organization, user, server_id="issues", mode="user"), vendor
        ).ok
