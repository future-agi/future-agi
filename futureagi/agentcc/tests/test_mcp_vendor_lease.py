"""Expired connects recover into a new generation; late workers are fenced."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from agentcc.services import mcp_vendor_connections as lifecycle
from agentcc.services.credential_manager import decrypt_token, encrypt_token
from agentcc.tests.test_mcp_vendor_lifecycle import vendor_settings as vendor_settings
from agentcc.tests.test_mcp_vendor_round2 import fake_api, setup_connection

pytestmark = pytest.mark.django_db(transaction=True)


def test_expired_connect_recovers_and_preserves_unknown_mint_obligation():
    _, _, _, bound, row, identity = setup_connection("composio")
    row.connect_pending = True
    row.connect_expires_at = timezone.now() - timedelta(seconds=1)
    row.encrypted_session = encrypt_token("trs_abandoned_fixture")
    row.save()
    api = fake_api()
    api.connected_account_ref = "ca_new_fixture"
    api.create.return_value = (
        "trs_new_fixture",
        "https://backend.composio.dev/mcp/new",
    )
    with (
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(lifecycle, "purge_gateway", return_value=True),
    ):
        lifecycle.start(bound.pk, identity)
    row.refresh_from_db()
    assert row.consent_generation == 2 and not row.connect_pending
    old = row.cleanup_obligations.get(consent_generation=1)
    assert decrypt_token(old.encrypted_session) == "trs_abandoned_fixture"
    assert old.outcome == "pending" and old.in_flight


@pytest.mark.parametrize("late_phase", ["session", "account"])
def test_late_worker_never_overwrites_recovered_generation(late_phase):
    _, _, _, bound, row, identity = setup_connection("composio")
    api = fake_api()
    api.connected_account_ref = "ca_new_fixture"
    api.account.return_value = None
    calls = {"create": 0, "connect": 0}

    def recover():
        type(row).objects.filter(pk=row.pk).update(
            connect_expires_at=timezone.now() - timedelta(seconds=1)
        )
        lifecycle.start(bound.pk, identity)

    def create(*args):
        calls["create"] += 1
        if late_phase == "session" and calls["create"] == 1:
            recover()
            return "trs_old_fixture", "https://backend.composio.dev/mcp/old"
        return "trs_new_fixture", "https://backend.composio.dev/mcp/new"

    def connect(*args):
        calls["connect"] += 1
        if late_phase == "account" and calls["connect"] == 1:
            recover()
            api.connected_account_ref = "ca_old_fixture"
        else:
            api.connected_account_ref = "ca_new_fixture"
        return "https://connect.composio.dev/fixture"

    api.create.side_effect = create
    api.connect.side_effect = connect
    with (
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(lifecycle, "purge_gateway", return_value=True),
    ):
        with pytest.raises(lifecycle.ConnectionRefused, match="revoked"):
            lifecycle.start(bound.pk, identity)
    row.refresh_from_db()
    assert row.consent_generation == 2
    assert row.vendor_account_ref == "ca_new_fixture"
    assert decrypt_token(row.encrypted_session) == "trs_new_fixture"
    assert not row.connect_pending
    old = row.cleanup_obligations.get(consent_generation=1)
    assert not old.in_flight and old.outcome == "pending"
    if late_phase == "session":
        assert decrypt_token(old.encrypted_session) == "trs_old_fixture"
    else:
        assert old.vendor_account_ref == "ca_old_fixture"
