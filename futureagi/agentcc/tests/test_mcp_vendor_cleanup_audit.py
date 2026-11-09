"""Generation-wide offboarding and retained cleanup audit, synthetic vendors."""

from unittest.mock import patch

import pytest

from agentcc.models.mcp_vendor import AgentccMCPVendorReceipt as Receipt
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import mcp_vendor_connections as lifecycle
from agentcc.services.mcp_revocation import Revocation
from agentcc.tests.mcp_oauth_helpers import member
from agentcc.tests.test_mcp_vendor_lifecycle import vendor_settings as vendor_settings
from agentcc.tests.test_mcp_vendor_round2 import fake_api, setup_connection
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("delete", [False, True], ids=["offboard", "binding-delete"])
def test_pipedream_offboard_deletes_every_generation_subject(delete):
    org, user, _, bound, row, _ = setup_connection()
    row.consent_generation = 3
    row.status = "active"
    row.save()
    api = fake_api()
    api.offboard.return_value = Revocation("unconfirmed", "not_documented")
    with (
        patch.object(lifecycle, "vendor_client", return_value=api),
        patch.object(lifecycle, "purge_gateway", return_value=True),
    ):
        if delete:
            lifecycle.disable_binding(bound.pk, delete=True)
        else:
            lifecycle.offboard_member(org.pk, user.pk)
    assert {c.args[0] for c in api.offboard.call_args_list} == {
        row.end_user_key,
        row.end_user_key + "_v2",
        row.end_user_key + "_v3",
    }


def test_binding_delete_keeps_org_scoped_cleanup_receipts():
    org, _, _, bound, row, _ = setup_connection()
    with (
        patch.object(lifecycle, "purge_gateway", return_value=True),
        patch.object(
            lifecycle,
            "revoke_vendor",
            return_value=Revocation("unconfirmed", "not_documented"),
        ),
    ):
        lifecycle.disconnect(row.pk)
        receipt_ids = list(row.receipts.values_list("pk", flat=True))
        assert receipt_ids
        lifecycle.disable_binding(bound.pk, delete=True)
    receipts = list(Receipt.objects.filter(pk__in=receipt_ids))
    assert len(receipts) == len(receipt_ids)
    assert all(
        r.connection_id is None
        and r.organization_id == org.pk
        and r.server_id == "partner"
        and r.connection_ref == row.pk
        for r in receipts
    )


def test_binding_created_for_matching_vendor_end_user_server():
    org, _, _, bound, _, _ = setup_connection()
    _, admin = member(org, level=Level.ADMIN)
    vault_id = bound.credential_id
    bound.delete()
    AgentccOrgConfig.no_workspace_objects.filter(
        organization=org, is_active=True
    ).update(is_active=False)
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=AgentccOrgConfig.all_objects.filter(organization=org).count() + 1,
        mcp={
            "servers": {
                "partner": {
                    "url": "https://remote.mcp.pipedream.net/v3",
                    "credential": {"mode": "vendor_end_user", "vendor": "pipedream"},
                }
            }
        },
    )
    response = admin.post(
        "/agentcc/mcp/vendor-bindings/",
        {
            "server_id": "partner",
            "vendor": "pipedream",
            "credential_id": str(vault_id),
            "project_id": "proj_fixture",
            "app_slug": "gmail",
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["vendor"] == "pipedream"
    assert response.json()["enabled"] is False
