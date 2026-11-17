"""Additional W11 tenant, invalid-response and stale-snapshot controls."""

from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from accounts.models import Organization
from agentcc.models import AgentccMCPApproval
from agentcc.services.gateway_client import GatewayClient
from agentcc.tests.test_mcp_approvals_bridge import invoke, record

pytestmark = pytest.mark.django_db


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_gateway_foreign_rows_are_not_disclosed_or_written(user, organization):
    foreign = Organization.objects.create(name="Other tenant")
    with patch.object(
        GatewayClient,
        "_request",
        return_value={"next_cursor": "", "approvals": [record(foreign)]},
    ) as transport:
        response = invoke(user, organization)
    assert response.status_code == 503
    assert str(foreign.pk) not in str(response.data)
    assert not AgentccMCPApproval.objects.exists()
    transport.assert_called_once()


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_nonmember_cannot_read_even_with_attached_organization(user):
    foreign = Organization.objects.create(name="Other tenant")
    with patch.object(GatewayClient, "_request") as transport:
        response = invoke(user, foreign)
    assert response.status_code == 403
    transport.assert_not_called()


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_stale_gateway_snapshot_does_not_reopen_decided_ui(user, organization):
    from agentcc.services.mcp_approval_audit import project_approval

    pending = record(organization)
    approved = {
        **pending,
        "status": "approved",
        "decided_by": str(user.pk),
        "decided_at": timezone.now().isoformat(),
    }
    project_approval(organization, approved)
    with patch.object(
        GatewayClient,
        "_request",
        return_value={"next_cursor": "", "approvals": [pending]},
    ):
        response = invoke(user, organization)
    assert response.status_code == 200
    assert response.data["result"][0]["status"] == "approved"
    assert response.data["result"][0]["decided_by"] == str(user.pk)


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_malformed_actor_fails_closed(user, organization):
    bad = record(
        organization,
        status="approved",
        decided_by="not-an-actor",
        decided_at=timezone.now().isoformat(),
    )
    with patch.object(
        GatewayClient, "_request", return_value={"next_cursor": "", "approvals": [bad]}
    ):
        response = invoke(user, organization)
    assert response.status_code == 503
    assert not AgentccMCPApproval.objects.exists()


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_utf8_note_bound_matches_gateway_bytes(user, organization):
    with patch.object(GatewayClient, "_request") as transport:
        response = invoke(
            user,
            organization,
            "post",
            "sealed/decision/",
            {"decision": "deny", "note": "界" * 400},
        )
    assert response.status_code == 400
    transport.assert_not_called()


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=False)
def test_background_is_active_without_legacy_switch():
    from agentcc.approval_tasks import reconcile_mcp_approvals

    with patch.object(GatewayClient, "_request") as transport:
        assert reconcile_mcp_approvals() == {"enabled": True, "organizations": 0}
    transport.assert_not_called()
