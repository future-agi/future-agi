"""Reconnect is a new consent, never an overwrite of an existing account."""

from unittest.mock import patch

import pytest

from agentcc.services import mcp_vendor_connections as lifecycle
from agentcc.services.credential_manager import encrypt_token
from agentcc.services.mcp_revocation import Revocation
from agentcc.tests.test_mcp_vendor_lifecycle import vendor_settings as vendor_settings
from agentcc.tests.test_mcp_vendor_round2 import fake_api, setup_connection

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("completed", [True, False], ids=["completed", "abandoned"])
@pytest.mark.parametrize("via_view", [False, True], ids=["service", "view"])
def test_reconnect_supersedes_old_account_before_mint(completed, via_view):
    _, _, client, bound, row, identity = setup_connection("composio")
    row.status = "active" if completed else "pending"
    row.vendor_account_ref = "ca_old_fixture"
    row.encrypted_session = encrypt_token("trs_old_fixture")
    row.save()
    api = fake_api()
    api.connected_account_ref = "ca_new_fixture"
    events = []
    api.disconnect.side_effect = lambda account: (
        events.append(("revoke_delete", account)) or Revocation("confirmed")
    )
    api.offboard.side_effect = lambda session: (
        events.append(("session_delete", session)) or Revocation("confirmed")
    )
    api.create.side_effect = lambda subject, *args: (
        events.append(("mint", subject))
        or ("trs_new_fixture", "https://backend.composio.dev/mcp/fixture")
    )
    api.connect.return_value = "https://connect.composio.dev/fixture"
    with (
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(lifecycle, "purge_gateway", return_value=True),
    ):
        if via_view:
            response = client.post(
                "/agentcc/mcp/vendor-connections/partner/connect/", {}, format="json"
            )
            assert response.status_code == 200
        else:
            lifecycle.start(bound.pk, identity)
        row.refresh_from_db()
        assert events[:2] == [
            ("revoke_delete", "ca_old_fixture"),
            ("session_delete", "trs_old_fixture"),
        ]
        assert events[2] == ("mint", row.end_user_key + "_v2")
        assert row.consent_generation == 2
        assert row.vendor_account_ref == "ca_new_fixture"
        assert row.receipts.filter(
            step="vendor_revocation", outcome="confirmed"
        ).exists()
        if completed:
            row.status = "active"
            row.save()
        else:
            api.disconnect.side_effect = lambda account: (
                events.append(("revoke_delete", account))
                or Revocation("unconfirmed", "http_409")
            )
        lifecycle.disconnect(row.pk)
        assert ("revoke_delete", "ca_new_fixture") in events


def test_reconnect_keeps_pending_old_account_cleanup_obligation():
    _, _, _, bound, row, identity = setup_connection("composio")
    row.status = "active"
    row.vendor_account_ref = "ca_old_fixture"
    row.encrypted_session = encrypt_token("trs_old_fixture")
    row.save()
    api = fake_api()
    api.connected_account_ref = "ca_new_fixture"
    api.create.return_value = (
        "trs_new_fixture",
        "https://backend.composio.dev/mcp/fixture",
    )
    api.disconnect.return_value = Revocation("pending", "vendor_unavailable")
    with (
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(lifecycle, "purge_gateway", return_value=True),
    ):
        lifecycle.start(bound.pk, identity)
        assert api.disconnect.call_args_list[0].args == ("ca_old_fixture",)
        row.refresh_from_db()
        assert row.consent_generation == 2
        # The old handle must remain durable and retryable, not just a summary receipt.
        old = row.cleanup_obligations.get(consent_generation=1)
        assert old.vendor_account_ref == "ca_old_fixture"
        assert old.outcome == "pending"
        api.disconnect.reset_mock()
        api.disconnect.return_value = Revocation("confirmed")
        lifecycle.disconnect(row.pk)
        assert {call.args[0] for call in api.disconnect.call_args_list} == {
            "ca_old_fixture",
            "ca_new_fixture",
        }
