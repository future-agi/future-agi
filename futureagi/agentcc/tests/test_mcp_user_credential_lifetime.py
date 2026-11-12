"""Personal vault transitions use the existing token/purge/Disconnect seams.

All token and gateway traffic is synthetic. Assertions name the state left by
an event, not a sequence for misusing a credential.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import close_old_connections
from django.utils import timezone

from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.services import mcp_credentials as vault
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_upstream_tokens as tokens
from agentcc.services.gateway_admin import mcp_credentials as gateway
from agentcc.tests.mcp_upstream_oauth_helpers import (
    CLIENT_METADATA,
    ISSUER,
    SERVER_ID,
    seed_config,
)
from agentcc.tests.test_mcp_internal_tokens import _connected
from agentcc.tests.test_mcp_internal_tokens import _settings as _settings
from agentcc.tests.test_mcp_internal_tokens import vendor as vendor

pytestmark = pytest.mark.django_db


@pytest.fixture
def config(organization):
    return seed_config(organization)


@pytest.mark.parametrize("state", ["expired", "needs_reauth"])
def test_personal_lapse_evicts_only_its_user(
    organization, user, config, vendor, state, django_capture_on_commit_callbacks
):
    row = _connected(organization, user, vendor)
    with patch.object(gateway, "purge", return_value=True) as purge:
        with django_capture_on_commit_callbacks(execute=True):
            vault.mark_lapsed(row, state, user)
    row.refresh_from_db()
    assert row.status == state
    assert purge.call_args.args == (str(organization.pk), SERVER_ID, str(user.pk))
    assert not row.gateway_purge_pending
    assert (
        tokens.upstream_token(str(organization.pk), str(user.pk), SERVER_ID).outcome
        == state
    )


def test_personal_expiry_records_eviction_obligation(
    organization, user, config, vendor, django_capture_on_commit_callbacks
):
    row = _connected(
        organization, user, vendor, expires_in=timedelta(seconds=-1), refresh=False
    )
    with patch.object(gateway, "purge", return_value=False) as purge:
        with django_capture_on_commit_callbacks(execute=True):
            answer = tokens.upstream_token(
                str(organization.pk), str(user.pk), SERVER_ID
            )
    row.refresh_from_db()
    assert answer.outcome == "expired"
    assert row.status == "expired"
    assert row.gateway_purge_pending
    assert purge.call_args.args == (str(organization.pk), SERVER_ID, str(user.pk))


def _reconnect_state(org, user):
    return SimpleNamespace(
        organization_id=org.pk,
        user_id=user.pk,
        server_id=SERVER_ID,
        issuer=ISSUER,
        client_id=CLIENT_METADATA,
        # Match the connect-state model's default for this non-K2 fixture.
        org_only=False,
        cancellation_generation=0,
        scopes=["mcp"],
        origin="https://mcp.vendor.example:443",
    )


def _replacement():
    return connect._Tokens(
        access=connect.Sealed("synthetic-replacement"),
        refresh=None,
        expires_at=timezone.now() + timedelta(hours=1),
        scopes=("mcp",),
    )


def test_personal_replacement_evicts_before_it_is_usable(
    organization, user, config, vendor, django_capture_on_commit_callbacks
):
    old = _connected(organization, user, vendor)
    with patch.object(gateway, "purge", return_value=True) as purge:
        with django_capture_on_commit_callbacks(execute=True):
            fresh = connect._store_tokens(
                _reconnect_state(organization, user), _replacement()
            )
    fresh.refresh_from_db()
    assert fresh.pk == old.pk
    assert fresh.version == old.version + 1
    assert not fresh.gateway_purge_pending
    assert purge.call_args.args == (str(organization.pk), SERVER_ID, str(user.pk))


def test_personal_failed_eviction_stays_closed_until_disconnect_cleanup(
    organization, user, config, vendor, django_capture_on_commit_callbacks
):
    _connected(organization, user, vendor)
    with patch.object(gateway, "purge", return_value=False):
        with django_capture_on_commit_callbacks(execute=True):
            fresh = connect._store_tokens(
                _reconnect_state(organization, user), _replacement()
            )
    fresh.refresh_from_db()
    assert fresh.gateway_purge_pending
    assert (
        tokens.upstream_token(str(organization.pk), str(user.pk), SERVER_ID).outcome
        == "needs_reauth"
    )
    with (
        patch.object(gateway, "purge", return_value=True),
        patch.object(connect, "_revoke_upstream", return_value=True),
    ):
        found, synced, pending = connect.disconnect(organization, user, SERVER_ID)
    assert found and synced and not pending
    fresh.refresh_from_db()
    assert fresh.status == Credential.Status.REVOKED
    assert not fresh.gateway_purge_pending


@pytest.mark.django_db(transaction=True)
def test_personal_concurrent_reconnects_leave_one_current_version(
    organization, user, config, vendor
):
    old = _connected(organization, user, vendor)
    state = _reconnect_state(organization, user)

    def reconnect(_):
        close_old_connections()
        try:
            return connect._store_tokens(state, _replacement()).version
        finally:
            close_old_connections()

    with patch.object(gateway, "purge", return_value=True) as purge:
        with ThreadPoolExecutor(max_workers=2) as workers:
            versions = list(workers.map(reconnect, range(2)))
    assert sorted(versions) == [2, 3]
    old.refresh_from_db()
    assert old.version == 3 and not old.gateway_purge_pending
    assert (
        Credential.objects.filter(
            organization=organization,
            user=user,
            scope="user",
            server_id=SERVER_ID,
            status="active",
        ).count()
        == 1
    )
    assert purge.call_count == 2
    assert all(
        call.args == (str(organization.pk), SERVER_ID, str(user.pk))
        for call in purge.call_args_list
    )


def test_personal_static_revoke_uses_the_same_eviction_seam(
    organization, user, config, django_capture_on_commit_callbacks
):
    with patch.object(gateway, "purge", return_value=True):
        with django_capture_on_commit_callbacks(execute=True):
            row = vault.create(
                organization,
                SERVER_ID,
                kind="bearer",
                value="synthetic-personal",
                scope="user",
                user=user,
                actor=user,
            ).credential
    with patch.object(gateway, "purge", return_value=True) as purge:
        with django_capture_on_commit_callbacks(execute=True):
            vault.revoke(row, user)
    row.refresh_from_db()
    assert row.status == "revoked"
    assert purge.call_args.args == (str(organization.pk), SERVER_ID, str(user.pk))
