"""The background approval reconcile when the gateway runs with MCP off.

With MCP off (the hosted profile requires it) the gateway registers no
/-/mcp/* route, so its router answers the approval list with its own
unknown-endpoint 404 (error code not_found). That is nothing to reconcile,
not a failure of every organization. Any other failure still counts and
still fails the run.
"""

from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from django.utils import timezone

from accounts.models import Organization
from agentcc.models import AgentccMCPApproval, AgentccOrgConfig
from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.tests.test_mcp_approvals_bridge import invoke, record

pytestmark = pytest.mark.django_db

EMPTY = {"next_cursor": "", "approvals": []}
FAILED_ONE = "Approval audit reconciliation failed for 1 organizations"
ROUTER_404_BODY = {
    "error": {
        "message": "Unknown endpoint: /-/mcp/approvals",
        "type": "invalid_request_error",
        "code": "not_found",
    }
}


def router_404():
    """What the shared client raises for the router's unknown-endpoint 404."""
    return GatewayClientError(
        "Gateway returned 404.",
        status_code=404,
        error_code="not_found",
        payload=ROUTER_404_BODY,
    )


def organizations_with_config(organization, total):
    organizations = [organization] + [
        Organization.objects.create(name=f"Tenant {index}") for index in range(1, total)
    ]
    for each in organizations:
        AgentccOrgConfig.objects.create(organization=each, mcp={"enabled": True})
    return organizations


def reconcile():
    from agentcc.approval_tasks import reconcile_mcp_approvals

    return reconcile_mcp_approvals()


def test_mcp_off_for_every_organization_is_nothing_to_reconcile(organization):
    organizations_with_config(organization, 3)
    with (
        patch.object(GatewayClient, "_request", side_effect=router_404()) as gateway,
        patch("agentcc.approval_tasks.logger") as log,
    ):
        result = reconcile()
    assert result == {
        "enabled": False,
        "reason": "gateway_mcp_disabled",
        "organizations": 0,
        "skipped": 3,
    }
    # The gateway is shared: its first MCP-off answer stops the run.
    gateway.assert_called_once()
    log.warning.assert_not_called()
    assert log.info.call_count <= 1


def test_mcp_off_still_expires_local_audit_rows(organization):
    from agentcc.services.mcp_approval_audit import project_approval

    project_approval(organization, record(organization))
    AgentccMCPApproval.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with patch.object(GatewayClient, "_request", side_effect=router_404()):
        result = reconcile()
    assert result["enabled"] is False
    assert AgentccMCPApproval.objects.get().status == "expired"


def test_router_404_body_through_the_real_client_is_mcp_off(organization):
    organizations_with_config(organization, 2)
    answer = httpx.Response(
        404,
        json=ROUTER_404_BODY,
        request=httpx.Request("GET", "http://gateway.test/-/mcp/approvals"),
    )
    with patch.object(httpx.Client, "request", return_value=answer) as sent:
        result = reconcile()
    assert result == {
        "enabled": False,
        "reason": "gateway_mcp_disabled",
        "organizations": 0,
        "skipped": 2,
    }
    sent.assert_called_once()


def test_client_types_the_router_404_and_keeps_its_status(organization):
    from agentcc.services.mcp_approval_client import (
        MCPApprovalClient,
        MCPApprovalsDisabled,
    )

    with patch.object(GatewayClient, "_request", side_effect=router_404()):
        with pytest.raises(MCPApprovalsDisabled) as raised:
            MCPApprovalClient().list(organization)
    assert isinstance(raised.value, GatewayClientError)
    assert raised.value.status_code == 404
    assert raised.value.reason == "gateway_mcp_disabled"


def test_mcp_on_real_failure_still_counts_and_raises(organization):
    organizations = organizations_with_config(organization, 2)
    failing = str(organizations[1].pk)

    def answer(method, path, params=None, **kwargs):
        if params["org_id"] == failing:
            raise GatewayClientError("Gateway returned 503.", status_code=503)
        return EMPTY

    with (
        patch.object(GatewayClient, "_request", side_effect=answer) as gateway,
        patch("agentcc.approval_tasks.logger") as log,
    ):
        with pytest.raises(RuntimeError) as raised:
            reconcile()
    assert str(raised.value) == FAILED_ONE
    assert gateway.call_count == 2
    log.warning.assert_called_once()


def test_an_approval_404_without_the_router_code_is_still_a_failure(organization):
    organizations_with_config(organization, 1)
    untyped = GatewayClientError("Gateway returned 404.", status_code=404)
    with patch.object(GatewayClient, "_request", side_effect=untyped):
        with pytest.raises(RuntimeError) as raised:
            reconcile()
    assert str(raised.value) == FAILED_ONE


def test_mixed_mcp_off_answers_are_skipped_not_failed(organization):
    organizations_with_config(organization, 3)
    calls = []

    def answer(method, path, params=None, **kwargs):
        calls.append(params["org_id"])
        if len(calls) == 1:
            raise GatewayClientError("Gateway returned 503.", status_code=503)
        raise router_404()

    with patch.object(GatewayClient, "_request", side_effect=answer):
        with pytest.raises(RuntimeError) as raised:
            reconcile()
    assert str(raised.value) == FAILED_ONE
    assert len(calls) == 2


def test_reconciled_before_mcp_off_keeps_its_count(organization):
    organizations_with_config(organization, 3)
    calls = []

    def answer(method, path, params=None, **kwargs):
        calls.append(params["org_id"])
        if len(calls) == 1:
            return EMPTY
        raise router_404()

    with patch.object(GatewayClient, "_request", side_effect=answer):
        result = reconcile()
    assert result == {
        "enabled": False,
        "reason": "gateway_mcp_disabled",
        "organizations": 1,
        "skipped": 2,
    }
    assert len(calls) == 2


def test_list_view_answer_is_unchanged_when_mcp_is_off(user, organization):
    with patch.object(GatewayClient, "_request", side_effect=router_404()):
        response = invoke(user, organization)
    assert response.status_code == 404
    assert response.data == {"message": "Approval not found."}
