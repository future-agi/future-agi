"""W11 customer routes and durable audit, with the real gateway client seam."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization_membership import OrganizationMembership
from agentcc.models import AgentccMCPApproval
from agentcc.services.gateway_client import GatewayClient, GatewayClientError

pytestmark = pytest.mark.django_db
ROOT = "/agentcc/mcp/approvals/"


def record(organization, **changes):
    now = timezone.now()
    return {
        "approval_id": "sealed_" + "a" * 300,
        "org_id": str(organization.id),
        "virtual_server_id": "review",
        "tool": "mock/delete_file",
        "args_hash": "a" * 64,
        "agent_run_id": "run-person",
        "principal": {"kind": "key", "key_id": "person-key", "user_id": "person"},
        "status": "pending",
        "requested_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        **changes,
    }


def invoke(user, organization, method="get", suffix="", body=None, level=8):
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=level)
    route = ROOT + suffix
    request = getattr(APIRequestFactory(), method)(route, body or {}, format="json")
    request.organization = organization
    force_authenticate(request, user=user)
    match = resolve(route)
    return match.func(request, **match.kwargs)


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=False)
def test_historical_false_setting_does_not_cut_approvals(user, organization):
    row = record(organization)
    with patch.object(
        GatewayClient, "_request", return_value={"next_cursor": "", "approvals": [row]}
    ) as transport:
        response = invoke(user, organization)
    assert response.status_code == 200
    transport.assert_called_once()


@pytest.mark.parametrize("level", [1, 3, 8, 15])
@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_members_read_real_route(user, organization, level):
    row = record(organization)
    with patch.object(
        GatewayClient, "_request", return_value={"next_cursor": "", "approvals": [row]}
    ) as transport:
        response = invoke(user, organization, level=level)
    assert response.status_code == 200
    assert response.data["result"][0]["approval_id"] == row["approval_id"]
    transport.assert_called_once_with(
        "GET", "/-/mcp/approvals", params={"org_id": str(organization.id)}
    )
    assert AgentccMCPApproval.objects.get().status == "pending"


@pytest.mark.parametrize("level", [1, 3])
@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_member_viewer_cannot_decide(user, organization, level):
    with patch.object(GatewayClient, "_request") as transport:
        response = invoke(
            user,
            organization,
            "post",
            "sealed/decision/",
            {"decision": "approve"},
            level,
        )
    assert response.status_code == 403
    transport.assert_not_called()


@pytest.mark.parametrize(
    "level,decision,status", [(8, "approve", "approved"), (15, "deny", "denied")]
)
@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_actor_and_org_server_derived(user, organization, level, decision, status):
    row = record(
        organization,
        status=status,
        decided_by=str(user.pk),
        decided_at=timezone.now().isoformat(),
    )
    with patch.object(GatewayClient, "_request", return_value=row) as transport:
        response = invoke(
            user,
            organization,
            "post",
            row["approval_id"] + "/decision/",
            {"decision": decision},
            level,
        )
    assert response.status_code == 200
    transport.assert_called_once_with(
        "POST",
        f"/-/mcp/approvals/{row['approval_id']}/decision",
        json_body={
            "org_id": str(organization.id),
            "decision": decision,
            "actor": str(user.pk),
            "note": "",
        },
    )
    audit = AgentccMCPApproval.objects.get()
    assert audit.decided_by_id == user.pk
    assert audit.decided_at is not None
    assert audit.api_key_id == "person-key"
    assert audit.status == status


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_spoofed_actor_rejected(user, organization):
    with patch.object(GatewayClient, "_request") as transport:
        response = invoke(
            user,
            organization,
            "post",
            "sealed/decision/",
            {"decision": "approve", "actor": "someone-else", "org_id": "foreign"},
        )
    assert response.status_code == 400
    transport.assert_not_called()


@pytest.mark.parametrize("status", [404, 409, 503])
@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_refusals_are_preserved_and_sanitized(user, organization, status):
    with patch.object(
        GatewayClient,
        "_request",
        side_effect=GatewayClientError("private transport detail", status_code=status),
    ) as transport:
        response = invoke(
            user, organization, "post", "sealed/decision/", {"decision": "approve"}
        )
    assert response.status_code == status
    assert "private" not in str(response.data)
    transport.assert_called_once()


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_foreign_poll_is_404(user, organization):
    with patch.object(
        GatewayClient,
        "_request",
        side_effect=GatewayClientError("missing", status_code=404),
    ) as transport:
        response = invoke(user, organization, suffix="foreign/")
    assert response.status_code == 404
    transport.assert_called_once()


def test_projection_does_not_regress_or_rebind(organization, user):
    from agentcc.services.mcp_approval_audit import project_approval

    pending = record(organization)
    project_approval(organization, pending)
    approved = {
        **pending,
        "status": "approved",
        "decided_by": str(user.pk),
        "decided_at": timezone.now().isoformat(),
    }
    project_approval(organization, approved)
    used = {**approved, "status": "used"}
    project_approval(organization, used)
    project_approval(organization, pending)
    audit = AgentccMCPApproval.objects.get()
    assert audit.status == "used"
    assert audit.decided_by_id == user.pk
    bad = deepcopy(used)
    bad["args_hash"] = "b" * 64
    with pytest.raises(ValueError):
        project_approval(organization, bad)
    audit.refresh_from_db()
    assert audit.args_hash == "a" * 64


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_background_projection_and_logical_expiry_without_browser(organization):
    from agentcc.approval_tasks import reconcile_mcp_approvals
    from agentcc.models import AgentccOrgConfig

    AgentccOrgConfig.objects.create(organization=organization, mcp={"enabled": True})
    row = record(organization)
    with patch.object(
        GatewayClient, "_request", return_value={"next_cursor": "", "approvals": [row]}
    ) as transport:
        reconcile_mcp_approvals()
    transport.assert_called_once()
    assert AgentccMCPApproval.objects.get().status == "pending"
    row["status"] = "used"
    with patch.object(
        GatewayClient, "_request", return_value={"next_cursor": "", "approvals": [row]}
    ):
        reconcile_mcp_approvals()
    assert AgentccMCPApproval.objects.get().status == "used"
    row["expires_at"] = (timezone.now() - timedelta(seconds=1)).isoformat()
    # The immutable expiry came from the original request, so use a different id.
    row["approval_id"] += "expired"
    row["status"] = "pending"
    with patch.object(
        GatewayClient, "_request", return_value={"next_cursor": "", "approvals": [row]}
    ):
        reconcile_mcp_approvals()
    assert (
        AgentccMCPApproval.objects.get(approval_id=row["approval_id"]).status
        == "expired"
    )
