"""Credential identities resolve independently and order their own transitions.

Provider and gateway traffic are synthetic; events delimit pending lifecycle I/O.
"""

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import timedelta
from threading import Event
from unittest.mock import patch

import pytest
from django.db import close_old_connections
from django.utils import timezone

from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_upstream_tokens as tokens
from agentcc.services.gateway_admin import mcp_credentials as gateway
from agentcc.tests.mcp_upstream_oauth_helpers import SERVER_ID, member
from agentcc.tests.test_mcp_internal_tokens import _connected
from agentcc.tests.test_mcp_internal_tokens import _settings as _settings
from agentcc.tests.test_mcp_internal_tokens import config as config
from agentcc.tests.test_mcp_internal_tokens import vendor as vendor


def _in_connection(fn, *args):
    close_old_connections()
    try:
        return fn(*args)
    finally:
        close_old_connections()


def _replacement():
    return connect._Tokens(
        access=connect.Sealed("synthetic-refreshed"),
        refresh=None,
        expires_at=timezone.now() + timedelta(hours=1),
        scopes=("mcp",),
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("unrelated", ["member", "server"])
@pytest.mark.parametrize("pending_io", ["refresh", "eviction"])
def test_unrelated_grant_resolves_within_token_channel_timeout(
    organization, user, workspace, config, vendor, unrelated, pending_io
):
    _connected(organization, user, vendor, expires_in=timedelta(seconds=-1))
    other, server_id = user, "another-server"
    if unrelated == "member":
        other, _ = member(organization, workspace)
        server_id = SERVER_ID
    else:
        config.mcp["servers"][server_id] = dict(config.mcp["servers"][SERVER_ID])
        config.save(update_fields=["mcp"])
    # Create with a distinct identity even when it belongs to the same member.
    if unrelated == "server":
        from agentcc.models.mcp_credential import AgentccMCPCredential

        original = AgentccMCPCredential.objects.get(
            organization=organization, user=user
        )
        original.server_id = server_id
        original.save(update_fields=["server_id"])
        original.token_expires_at = timezone.now() + timedelta(hours=1)
        original.save(update_fields=["token_expires_at"])
        _connected(organization, user, vendor, expires_in=timedelta(seconds=-1))
    else:
        _connected(organization, other, vendor)
    started, release = Event(), Event()

    def block():
        started.set()
        assert release.wait(15), "synthetic lifecycle I/O was not released"

    def synthetic_refresh(row, url):
        if pending_io == "refresh":
            block()
        return _replacement()

    def synthetic_evict(*args):
        if pending_io == "eviction":
            block()
        return True

    def answer(member_id, server):
        return tokens.upstream_token(
            str(organization.pk), str(member_id), server
        ).outcome

    with (
        patch.object(tokens, "_refresh", side_effect=synthetic_refresh),
        patch.object(gateway, "purge", side_effect=synthetic_evict),
        ThreadPoolExecutor(max_workers=2) as workers,
    ):
        refreshing = workers.submit(_in_connection, answer, user.pk, SERVER_ID)
        try:
            assert started.wait(5), "first credential did not reach pending I/O"
            unaffected = workers.submit(_in_connection, answer, other.pk, server_id)
            done, _ = wait([unaffected], timeout=5.5)
            completed_before_release = unaffected in done
        finally:
            release.set()
        assert refreshing.result(timeout=10) == "refreshed"
        assert unaffected.result(timeout=10) == "ok"
    assert completed_before_release, "unrelated valid grant waited behind lifecycle I/O"


@pytest.mark.django_db(transaction=True)
def test_disconnect_orders_after_refresh_without_restoring_the_grant(
    organization, user, config, vendor
):
    row = _connected(organization, user, vendor, expires_in=timedelta(seconds=-1))
    started, release, disconnect_started = Event(), Event(), Event()

    def synthetic_refresh(*args):
        started.set()
        assert release.wait(15)
        return _replacement()

    def disconnect():
        disconnect_started.set()
        return connect.disconnect(organization, user, SERVER_ID)

    with (
        patch.object(tokens, "_refresh", side_effect=synthetic_refresh),
        patch.object(connect, "_revoke_upstream", return_value=True),
        ThreadPoolExecutor(max_workers=2) as workers,
    ):
        refreshing = workers.submit(
            _in_connection,
            tokens.upstream_token,
            str(organization.pk),
            str(user.pk),
            SERVER_ID,
        )
        try:
            assert started.wait(5)
            revoking = workers.submit(_in_connection, disconnect)
            assert disconnect_started.wait(5)
            done, _ = wait([revoking], timeout=0.1)
            assert not done, "same-identity disconnect did not wait for refresh"
        finally:
            release.set()
        assert refreshing.result(timeout=10).outcome == "refreshed"
        assert revoking.result(timeout=10) == (True, True, False)
    row.refresh_from_db()
    assert row.status == "revoked"
    assert not row.gateway_purge_pending and not row.oauth_revoke_pending
    assert (
        tokens.upstream_token(str(organization.pk), str(user.pk), SERVER_ID).outcome
        == "revoked"
    )
