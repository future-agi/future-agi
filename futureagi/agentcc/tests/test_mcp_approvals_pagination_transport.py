"""W11 R3 pagination and actual HTTPX exception boundary regressions."""

from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from django.test import override_settings
from django.utils import timezone

from agentcc.models import AgentccMCPApproval
from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.services.mcp_approval_client import MCPApprovalClient
from agentcc.tests.test_mcp_approvals_bridge import invoke, record

pytestmark = pytest.mark.django_db


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_exhaustive_mixed_ttl_reconciliation_and_out_of_window_detail(
    user, organization
):
    from agentcc.services.mcp_approval_audit import reconcile_organization

    rows = [
        record(
            organization,
            approval_id=f"retained-{i}",
            expires_at=(
                timezone.now() + timedelta(minutes=10 if i < 1000 else 2)
            ).isoformat(),
        )
        for i in range(1002)
    ]

    def transport(method, path, params=None, **kwargs):
        assert method == "GET"
        assert params["org_id"] == str(organization.pk)
        if path == "/-/mcp/approvals":
            return {
                "approvals": rows[1000:] if params.get("cursor") else rows[:1000],
                "next_cursor": "" if params.get("cursor") else "second-page",
            }
        assert path == "/-/mcp/approvals/retained-1000"
        return rows[1000]

    with patch.object(GatewayClient, "_request", side_effect=transport) as request:
        assert len(reconcile_organization(organization, MCPApprovalClient())) == 1002
        assert request.call_count == 2
        rows[1001]["status"] = "used"
        reconcile_organization(organization, MCPApprovalClient())
        assert request.call_count == 4
        assert (
            AgentccMCPApproval.objects.get(approval_id="retained-1001").status == "used"
        )
        request.reset_mock()
        response = invoke(user, organization, suffix="retained-1000/")
        assert response.status_code == 200
        assert response.data["result"]["status"] == "pending"
        request.assert_called_once_with(
            "GET",
            "/-/mcp/approvals/retained-1000",
            params={"org_id": str(organization.pk)},
        )
    assert AgentccMCPApproval.objects.count() == 1002


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_foreign_detail_is_not_projected_or_disclosed(user, organization):
    with patch.object(
        GatewayClient,
        "_request",
        return_value=record(organization, org_id="foreign", approval_id="foreign"),
    ) as request:
        response = invoke(user, organization, suffix="foreign/")
    assert response.status_code == 503
    assert "foreign" not in str(response.data)
    assert not AgentccMCPApproval.objects.exists()
    request.assert_called_once_with(
        "GET", "/-/mcp/approvals/foreign", params={"org_id": str(organization.pk)}
    )


@pytest.mark.parametrize("error", [httpx.ReadError, httpx.RemoteProtocolError])
@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_unknown_decision_outcome_is_sanitized_not_replayed_and_refreshed(
    user, organization, error
):
    row = record(
        organization,
        status="approved",
        decided_by=str(user.pk),
        decided_at=timezone.now().isoformat(),
    )
    calls = []

    def transport(self, method, url, **kwargs):
        calls.append(method)
        if method == "POST":
            raise error("private transport detail and secret " + "x" * 8000)
        return httpx.Response(200, json=row)

    with patch.object(
        httpx.Client, "request", autospec=True, side_effect=transport
    ) as request:
        response = invoke(
            user,
            organization,
            "post",
            row["approval_id"] + "/decision/",
            {"decision": "approve"},
        )
        assert response.status_code == 503
        assert "private" not in str(response.data) and "secret" not in str(
            response.data
        )
        assert len(str(response.data)) < 256
        assert calls == ["POST"]
        assert not AgentccMCPApproval.objects.exists()
        refreshed = invoke(user, organization, suffix=row["approval_id"] + "/")
        assert refreshed.status_code == 200
        assert refreshed.data["result"]["status"] == "approved"
        assert calls == ["POST", "GET"]
        assert request.call_count == 2
    assert AgentccMCPApproval.objects.get().status == "approved"


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadError,
        httpx.RemoteProtocolError,
        httpx.WriteError,
        httpx.CloseError,
        httpx.ProxyError,
        httpx.UnsupportedProtocol,
        httpx.DecodingError,
        httpx.TooManyRedirects,
    ],
)
def test_all_request_errors_are_bounded_at_the_adapter(organization, error):
    with patch.object(
        httpx.Client, "request", side_effect=error("private " + "x" * 8000)
    ) as request:
        with pytest.raises(GatewayClientError) as exc:
            MCPApprovalClient(GatewayClient("http://127.0.0.1:9")).list(organization)
    assert str(exc.value) == "Approval gateway transport failed."
    assert exc.value.__cause__ is None
    assert exc.value.status_code == 503
    request.assert_called_once()


def test_cursor_cycles_fail_closed_instead_of_looping(organization):
    from agentcc.services.mcp_approval_audit import reconcile_organization

    with patch.object(
        GatewayClient,
        "_request",
        return_value={"approvals": [], "next_cursor": "repeated"},
    ) as request:
        with pytest.raises(ValueError):
            reconcile_organization(organization, MCPApprovalClient())
    assert request.call_count == 2


@pytest.mark.parametrize(
    "payload", [{"approvals": []}, {"approvals": [], "next_cursor": None}]
)
def test_legacy_capped_list_is_not_evidence_of_completion(organization, payload):
    from agentcc.services.mcp_approval_audit import reconcile_organization

    with patch.object(GatewayClient, "_request", return_value=payload) as request:
        with pytest.raises(ValueError):
            reconcile_organization(organization, MCPApprovalClient())
    request.assert_called_once()


def test_foreign_second_page_rolls_back_entire_projection(organization):
    from agentcc.services.mcp_approval_audit import reconcile_organization

    pages = [
        {"approvals": [record(organization)], "next_cursor": "page-2"},
        {"approvals": [record(organization, org_id="foreign")], "next_cursor": ""},
    ]
    with patch.object(GatewayClient, "_request", side_effect=pages) as request:
        with pytest.raises(ValueError):
            reconcile_organization(organization, MCPApprovalClient())
    assert request.call_count == 2
    assert not AgentccMCPApproval.objects.exists()
