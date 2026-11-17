"""Consent generations and connect-time account receipts, using synthetic APIs."""

import json
from unittest.mock import patch

import pytest

from agentcc.services import mcp_vendor_connections as lifecycle
from agentcc.services import mcp_vendor_credentials as credentials
from agentcc.services.mcp_revocation import Revocation
from agentcc.services.mcp_safe_fetch import FetchResponse
from agentcc.services.mcp_vendor_composio import Composio
from agentcc.tests.mcp_oauth_helpers import member
from agentcc.tests.test_mcp_vendor_lifecycle import request_body
from agentcc.tests.test_mcp_vendor_lifecycle import vendor_settings as vendor_settings
from agentcc.tests.test_mcp_vendor_round2 import fake_api, setup_connection

pytestmark = pytest.mark.django_db(transaction=True)


def test_pipedream_reconnect_cannot_readopt_surviving_account():
    org, user, _, bound, row, identity = setup_connection()
    original_subject = row.end_user_key
    api = fake_api()
    api.account.side_effect = lambda subject, *args: (
        "ac_survivor" if subject == original_subject else None
    )
    with (
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(credentials, "vendor_client", return_value=api),
        patch.object(lifecycle, "purge_gateway", return_value=True),
        patch.object(
            lifecycle,
            "revoke_vendor",
            return_value=Revocation("pending", "vendor_unavailable"),
        ),
    ):
        lifecycle.disconnect(row.pk)
        lifecycle.start(bound.pk, identity)
        answer = credentials.answer_request(request_body(org, user))
    row.refresh_from_db()
    assert row.vendor_subject != original_subject
    api.connect.assert_called_once_with(row.vendor_subject)
    assert answer == {"error": "not_connected"}


def test_composio_link_account_saved_before_first_execution():
    _, _, _, bound, row, identity = setup_connection("composio")
    api = Composio("fixture-project-not-real")

    def fetch(url, **kwargs):
        assert url.endswith("/link")
        assert not lifecycle.transaction.get_connection().in_atomic_block
        return FetchResponse(
            201,
            {},
            json.dumps(
                {
                    "redirect_url": "https://connect.composio.dev/fixture",
                    "connected_account_id": "ca_consent_fixture",
                }
            ).encode(),
            url,
        )

    with (
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(
            api,
            "create",
            return_value=("trs_fixture", "https://backend.composio.dev/mcp/fixture"),
        ),
        patch("agentcc.services.mcp_safe_fetch.fetch", fetch),
    ):
        lifecycle.start(bound.pk, identity)
    row.refresh_from_db()
    assert row.vendor_account_ref == "ca_consent_fixture"
    with (
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(lifecycle, "purge_gateway", return_value=True),
        patch.object(api, "disconnect", return_value=Revocation("confirmed")) as revoke,
        patch.object(api, "offboard", return_value=Revocation("confirmed")),
    ):
        lifecycle.disconnect(row.pk)
    revoke.assert_called_once_with("ca_consent_fixture")


def test_binding_delete_retains_pending_cleanup_and_stays_disabled():
    _, _, _, bound, row, _ = setup_connection()
    with (
        patch.object(lifecycle, "purge_gateway", return_value=False),
        patch.object(
            lifecycle,
            "revoke_vendor",
            return_value=Revocation("pending", "vendor_unavailable"),
        ),
    ):
        with pytest.raises(lifecycle.ConnectionRefused, match="cleanup_pending"):
            lifecycle.disable_binding(bound.pk, delete=True)
    bound.refresh_from_db()
    row.refresh_from_db()
    assert not bound.enabled and row.status == "revoked"
    assert row.gateway_purge_pending and row.vendor_revoke_pending


def test_inactive_vault_cleanup_terminal_not_permanently_pending():
    _, _, _, bound, row, _ = setup_connection()
    bound.credential.status = "revoked"
    bound.credential.save(update_fields=["status"])
    with patch.object(lifecycle, "purge_gateway", return_value=True):
        saved = lifecycle.disconnect(row.pk)
    assert not saved.vendor_revoke_pending
    assert saved.vendor_revoke_outcome == "unconfirmed"
    assert saved.receipts.filter(reason="credential_inactive").exists()


def test_binding_creation_requires_matching_vendor_config():
    from tfc.constants.levels import Level

    org, _, _, bound, _, _ = setup_connection()
    _, admin = member(org, level=Level.ADMIN)
    data = {
        "server_id": "partner",
        "vendor": "pipedream",
        "credential_id": str(bound.credential_id),
        "project_id": "proj_fixture",
        "app_slug": "gmail",
    }
    response = admin.post("/agentcc/mcp/vendor-bindings/", data, format="json")
    assert response.status_code == 409
    assert response.json()["error"] == "vendor_server_required"


def test_composio_connect_refuses_link_without_cleanup_handle():
    from agentcc.services.mcp_vendor_http import VendorUnavailable

    api = Composio("fixture-project-not-real")
    response = FetchResponse(
        201,
        {},
        b'{"redirect_url":"https://connect.composio.dev/fixture"}',
        "https://backend.composio.dev/fixture",
    )
    with patch("agentcc.services.mcp_safe_fetch.fetch", return_value=response):
        with pytest.raises(VendorUnavailable):
            api.connect("trs_fixture", "gmail")


def test_disconnect_during_link_keeps_new_account_for_cleanup():
    _, _, _, bound, row, identity = setup_connection("composio")
    api = fake_api()
    api.create.return_value = (
        "trs_fixture",
        "https://backend.composio.dev/mcp/fixture",
    )
    api.connected_account_ref = "ca_inflight_fixture"

    def link(*args):
        lifecycle.disconnect(row.pk)
        return "https://connect.composio.dev/fixture"

    api.connect.side_effect = link
    with (
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(lifecycle, "purge_gateway", return_value=True),
    ):
        with pytest.raises(lifecycle.ConnectionRefused, match="revoked"):
            lifecycle.start(bound.pk, identity)
        row.refresh_from_db()
        assert row.vendor_account_ref == "ca_inflight_fixture"
        assert row.vendor_revoke_pending and not row.connect_pending
        lifecycle.disconnect(row.pk)
    api.disconnect.assert_called_once_with("ca_inflight_fixture")
