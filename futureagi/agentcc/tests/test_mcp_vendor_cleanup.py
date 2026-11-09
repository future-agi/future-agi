"""Durable partner cleanup obligations, including already-disconnected members."""

from unittest.mock import patch

import pytest

from agentcc.models.mcp_vendor import AgentccMCPVendorConnection as Connection
from agentcc.services import mcp_vendor_connections as lifecycle
from agentcc.services.mcp_revocation import Revocation
from agentcc.tests.mcp_oauth_helpers import member, new_org
from agentcc.tests.test_mcp_vendor_lifecycle import binding
from agentcc.tests.test_mcp_vendor_lifecycle import vendor_settings as vendor_settings

pytestmark = pytest.mark.django_db


def test_offboard_retry_keeps_external_user_deletion_obligation():
    org = new_org()
    row = binding(org)
    user, _ = member(org)
    connection = Connection.objects.create(
        binding=row,
        source="member",
        user=user,
        end_user_key="fau_fixture",
        status="revoked",
    )
    with (
        patch.object(lifecycle, "purge_gateway", return_value=True),
        patch.object(
            lifecycle,
            "revoke_vendor",
            return_value=Revocation("pending", "vendor_unavailable"),
        ) as revoke,
    ):
        lifecycle.disconnect(connection.pk, offboard=True)
        assert revoke.call_args.kwargs["offboard"] is True
    connection.refresh_from_db()
    assert connection.offboard_requested and connection.vendor_revoke_pending
    with (
        patch.object(lifecycle, "purge_gateway", return_value=True),
        patch.object(
            lifecycle, "revoke_vendor", return_value=Revocation("confirmed")
        ) as revoke,
    ):
        lifecycle.disconnect(connection.pk)
        assert revoke.call_args.kwargs["offboard"] is True
    connection.refresh_from_db()
    assert not connection.vendor_revoke_pending


def test_pending_vendor_failure_does_not_undo_local_revoke():
    org = new_org()
    row = binding(org)
    user, _ = member(org)
    connection = Connection.objects.create(
        binding=row, source="member", user=user, end_user_key="fau_fixture"
    )
    with (
        patch.object(
            lifecycle,
            "purge_gateway",
            side_effect=RuntimeError("fixture network failure"),
        ),
        patch.object(
            lifecycle,
            "revoke_vendor",
            side_effect=RuntimeError("fixture secret must not be recorded"),
        ),
    ):
        lifecycle.disconnect(connection.pk)
    connection.refresh_from_db()
    assert connection.status == "revoked"
    assert connection.gateway_purge_pending and connection.vendor_revoke_pending
    assert "fixture secret" not in str(
        list(connection.receipts.values("step", "outcome", "reason"))
    )


def test_member_cannot_revoke_someone_else_but_admin_can():
    from tfc.constants.levels import Level

    org = new_org()
    row = binding(org)
    owner, owner_client = member(org, level=Level.ADMIN)
    user, user_client = member(org)
    own = Connection.objects.create(
        binding=row, source="member", user=owner, end_user_key="fau_admin"
    )
    foreign = Connection.objects.create(
        binding=row, source="member", user=user, end_user_key="fau_member"
    )
    with (
        patch.object(lifecycle, "purge_gateway", return_value=True),
        patch.object(lifecycle, "revoke_vendor", return_value=Revocation("confirmed")),
    ):
        response = user_client.delete(
            f"/agentcc/mcp/vendor-connections/?connection_id={own.pk}"
        )
        assert response.status_code == 200 and response.json()["connections"] == []
        response = owner_client.delete(
            f"/agentcc/mcp/vendor-connections/?connection_id={foreign.pk}"
        )
        assert response.status_code == 200
    own.refresh_from_db()
    foreign.refresh_from_db()
    assert own.status == "pending" and foreign.status == "revoked"


def test_connect_rejects_non_object_and_foreign_identity():
    org = new_org()
    binding(org)
    _, client = member(org)
    path = "/agentcc/mcp/vendor-connections/partner/connect/"
    assert client.post(path, [], format="json").status_code == 400
    assert (
        client.post(path, {"asserted_id": "customer-b"}, format="json").status_code
        == 403
    )
