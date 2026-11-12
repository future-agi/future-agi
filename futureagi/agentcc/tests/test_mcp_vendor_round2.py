"""Regression proofs for optimistic partner lifecycle; synthetic vendor I/O only."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch

import pytest
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from agentcc.models.mcp_vendor import AgentccMCPVendorConnection as Connection
from agentcc.services import mcp_vendor_connections as lifecycle
from agentcc.services import mcp_vendor_credentials as credentials
from agentcc.services.credential_manager import decrypt_token, encrypt_token
from agentcc.services.mcp_revocation import Revocation
from agentcc.services.mcp_vendor_http import VendorUnavailable
from agentcc.tests.mcp_oauth_helpers import member, new_org
from agentcc.tests.test_mcp_vendor_lifecycle import binding, request_body
from agentcc.tests.test_mcp_vendor_lifecycle import vendor_settings as vendor_settings

pytestmark = pytest.mark.django_db(transaction=True)


def setup_connection(vendor="pipedream"):
    org = new_org()
    user, client = member(org)
    bound = binding(org, vendor)
    if vendor == "composio":
        bound.toolkits = ["gmail"]
        bound.tools = {"gmail": ["GMAIL_FETCH_EMAILS"]}
        bound.save()
    identity = {"source": "member", "id": str(user.pk)}
    row = Connection.objects.create(
        binding=bound,
        user=user,
        source="member",
        end_user_key=credentials.subject_key(org.pk, "member", str(user.pk)),
    )
    return org, user, client, bound, row, identity


def fake_api():
    api = Mock()
    api.connect.return_value = "https://connect.pipedream.com/?fixture=synthetic"
    api.account.return_value = "ac_fixture"
    api.bearer.return_value = "fixture-bearer-not-real"
    api.expires_at = timezone.now()
    api.disconnect.return_value = Revocation("confirmed")
    api.offboard.return_value = Revocation("confirmed")
    return api


@pytest.mark.parametrize("operation", ["start", "answer", "disconnect"])
def test_slow_vendor_does_not_lock_org_vault_or_connection(operation):
    org, user, _, bound, row, identity = setup_connection()
    entered, release = Event(), Event()
    api = fake_api()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10), "test did not release synthetic vendor"
        return "ac_fixture" if operation == "answer" else api.connect.return_value

    def run():
        close_old_connections()
        try:
            if operation == "start":
                return lifecycle.start(bound.pk, identity)
            if operation == "answer":
                return credentials.answer_request(request_body(org, user))
            return lifecycle.disconnect(row.pk)
        finally:
            close_old_connections()

    api.account.side_effect = slow if operation == "answer" else None
    if operation == "start":
        api.connect.side_effect = slow
    with (
        patch.object(credentials, "vendor_client", return_value=api),
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(lifecycle, "purge_gateway", side_effect=slow),
        patch.object(lifecycle, "revoke_vendor", return_value=Revocation("confirmed")),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        work = pool.submit(run)
        try:
            assert entered.wait(10), "worker did not reach vendor I/O"
            # A different request in this org must proceed while vendor I/O waits.
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '750ms'")
                type(org)._base_manager.select_for_update(no_key=True).get(pk=org.pk)
                type(bound.credential).objects.select_for_update(nowait=True).get(
                    pk=bound.credential_id
                )
                Connection.objects.select_for_update(nowait=True).get(pk=row.pk)
                type(bound.credential).objects.create(
                    organization=org,
                    server_id="unrelated",
                    scope="shared",
                    kind="oauth_client",
                    origin="https://fixture.invalid",
                    encrypted_secret=encrypt_token("fixture-secret-not-real"),
                )
        finally:
            release.set()
        work.result(timeout=10)


def test_composio_unknown_account_never_claims_confirmed():
    _, _, _, _, row, _ = setup_connection("composio")
    api = fake_api()
    api.account.return_value = None
    with patch.object(lifecycle, "vendor_client", return_value=api):
        result = lifecycle.revoke_vendor(row)
    assert result.outcome == "unconfirmed"
    assert result.reason in ("no_account", "account_unknown")


@pytest.mark.parametrize("vendor", ["pipedream", "composio"])
def test_disconnect_discovers_account_before_first_execution(vendor):
    _, _, _, _, row, _ = setup_connection(vendor)
    row.encrypted_session = encrypt_token("trs_fixture")
    row.save()
    api = fake_api()
    with (
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(lifecycle, "purge_gateway", return_value=True),
    ):
        lifecycle.disconnect(row.pk)
    api.account.assert_called_once()
    api.disconnect.assert_called_once_with("ac_fixture")


def test_asserted_link_failure_keeps_minted_session():
    org = new_org()
    bound = binding(org, "composio")
    bound.identity_policy = "member_or_asserted"
    bound.toolkits = ["gmail"]
    bound.tools = {"gmail": ["GMAIL_FETCH_EMAILS"]}
    bound.save()
    api = fake_api()
    api.create.return_value = (
        "trs_fixture",
        "https://backend.composio.dev/mcp/fixture",
    )
    api.connect.side_effect = VendorUnavailable()
    body = {
        "org_id": str(org.pk),
        "server_id": "partner",
        "vendor": "composio",
        "end_user": {"source": "asserted", "id": "customer-fixture"},
    }
    with patch.object(lifecycle, "vendor_client", return_value=api):
        with pytest.raises(VendorUnavailable):
            credentials.answer_request(body)
    row = Connection.objects.get(binding=bound)
    assert decrypt_token(row.encrypted_session) == "trs_fixture"


def test_reconnect_supersedes_pending_vendor_not_purge():
    _, _, _, _, row, identity = setup_connection()
    row.status = "revoked"
    row.vendor_revoke_pending = True
    row.save()
    api = fake_api()
    api.account.return_value = None
    with (
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(
            lifecycle,
            "revoke_vendor",
            return_value=Revocation("pending", "vendor_unavailable"),
        ),
    ):
        lifecycle.start(row.binding_id, identity)
    row.refresh_from_db()
    assert not row.vendor_revoke_pending
    assert row.receipts.filter(step="vendor_revocation", outcome="unconfirmed").exists()
    row.gateway_purge_pending = True
    row.save()
    with pytest.raises(lifecycle.ConnectionRefused, match="cleanup_pending"):
        lifecycle.start(row.binding_id, identity)


def test_concurrent_revoke_cannot_resurrect_issued_grant():
    org, user, _, _, row, _ = setup_connection()
    api = fake_api()

    def account(*args):
        with (
            patch.object(lifecycle, "purge_gateway", return_value=True),
            patch.object(
                lifecycle, "revoke_vendor", return_value=Revocation("confirmed")
            ),
        ):
            lifecycle.disconnect(row.pk)
        return "ac_fixture"

    api.account.side_effect = account
    with patch.object(credentials, "vendor_client", return_value=api):
        answer = credentials.answer_request(request_body(org, user))
    row.refresh_from_db()
    assert answer.get("error") == "revoked"
    assert row.status == "revoked"


def test_org_cascade_not_blocked_by_vendor_rows():
    # Exercise the actual org -> vault/binding/connection cascade, without the
    # unrelated membership post_delete audit writer (which inserts a new org FK).
    org = new_org()
    bound = binding(org)
    Connection.objects.create(
        binding=bound, source="asserted", end_user_key="fau_fixture"
    )
    org.delete()
    assert not Connection.objects.exists()


def test_membership_cleanup_runs_only_after_commit():
    from accounts.models.organization_membership import OrganizationMembership

    org, user, _, _, _, _ = setup_connection()
    membership = OrganizationMembership.objects.get(organization=org, user=user)
    with patch.object(lifecycle, "offboard_member") as offboard:
        with transaction.atomic():
            membership.is_active = False
            membership.save()
            offboard.assert_not_called()
        offboard.assert_called_once_with(org.pk, user.pk)


def test_admin_can_disable_then_delete_binding_with_cleanup():
    from tfc.constants.levels import Level

    org, _, client, bound, row, _ = setup_connection()
    _, admin = member(org, level=Level.ADMIN)
    path = f"/agentcc/mcp/vendor-bindings/{bound.pk}/"
    assert client.patch(path, {"enabled": False}, format="json").status_code == 403
    with (
        patch.object(lifecycle, "purge_gateway", return_value=True),
        patch.object(lifecycle, "revoke_vendor", return_value=Revocation("confirmed")),
    ):
        response = admin.patch(path, {"enabled": False}, format="json")
        assert response.status_code == 200
        row.refresh_from_db()
        assert row.status == "revoked"
        assert admin.delete(path).status_code == 204
    assert not type(bound).objects.filter(pk=bound.pk).exists()
