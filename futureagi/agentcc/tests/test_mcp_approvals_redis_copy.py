"""Approval routes say why approvals are unavailable, and what the person who
runs the deployment does, in two cases: the gateway serves no approval route,
or it serves them with no working Redis connection. Every other gateway
failure keeps today's message."""

from unittest.mock import patch

import httpx
import pytest
from django.test import override_settings

from agentcc.models import AgentccMCPApproval
from agentcc.tests.test_mcp_approvals_bridge import invoke

pytestmark = pytest.mark.django_db

SERVES_NONE = (
    "Approvals aren't available here: MCP is off on this deployment's gateway, "
    "or the gateway is older than approvals. If you run this deployment, turn "
    "MCP on in the gateway's configuration file (mcp.enabled: true, with its "
    "state keys), update the gateway if it is older, and restart it. "
    "Approval state could not be refreshed. Decisions are disabled until "
    "Refresh succeeds."
)
NEEDS_REDIS = (
    "Approvals need Redis, and this deployment's gateway has no working Redis "
    "connection. If you run this deployment, give the gateway process its "
    "Redis connection (AGENTCC_REDIS_ADDRESS, and AGENTCC_REDIS_PASSWORD if "
    "your Redis needs one), then restart the gateway. "
    "Approval state could not be refreshed. Decisions are disabled until "
    "Refresh succeeds."
)
NOT_FOUND = "Approval not found."
UNCONFIRMED = (
    "Approval state could not be confirmed. Refresh before retrying a decision."
)

# The gateway router's own answer for a path it does not serve. With MCP off,
# or on a gateway older than approvals, every approval path gets it.
ROUTER_404_BODY = {
    "error": {
        "message": "Unknown endpoint: /-/mcp/approvals",
        "type": "invalid_request_error",
        "code": "not_found",
    }
}

# Each approval route, as the console calls it.
ROUTES = {
    "list": ("get", "", None),
    "detail": ("get", "sealed/", None),
    "decision": ("post", "sealed/decision/", {"decision": "approve"}),
}


def call(user, organization, route, answer):
    """Send one approval route through the real gateway client, with the
    gateway's HTTP answer (or transport failure) supplied by ``answer``."""
    method, suffix, body = ROUTES[route]
    calls = []

    def transport(self, verb, url, **kwargs):
        calls.append(verb)
        return answer()

    with patch.object(httpx.Client, "request", autospec=True, side_effect=transport):
        response = invoke(user, organization, method, suffix, body)
    assert calls == [method.upper()]
    return response


def serve_no_approval_route():
    return httpx.Response(404, json=ROUTER_404_BODY)


@pytest.mark.parametrize("route", sorted(ROUTES))
@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_gateway_serving_no_approval_route_says_why_and_the_remedy(
    user, organization, route
):
    response = call(user, organization, route, serve_no_approval_route)
    # The router's 404 status is kept, as before; the words say why.
    assert response.status_code == 404
    assert response.data == {"message": SERVES_NONE}
    assert not AgentccMCPApproval.objects.exists()


@pytest.mark.parametrize("route", sorted(ROUTES))
@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_gateway_without_readable_approval_state_names_redis_and_the_remedy(
    user, organization, route
):
    response = call(
        user,
        organization,
        route,
        lambda: httpx.Response(503, json={"error": "approval_unavailable"}),
    )
    assert response.status_code == 503
    assert response.data == {"message": NEEDS_REDIS}
    assert not AgentccMCPApproval.objects.exists()


def miss_the_approval():
    return httpx.Response(404, json={"error": "approval_not_found"})


def miss_without_body():
    return httpx.Response(404, text="")


def miss_the_organization():
    return httpx.Response(
        404, json={"error": {"code": "org_not_found", "message": "private"}}
    )


@pytest.mark.parametrize(
    "answer",
    [miss_the_approval, miss_without_body, miss_the_organization],
    ids=lambda answer: answer.__name__,
)
@pytest.mark.parametrize("route", sorted(ROUTES))
@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_every_other_gateway_404_keeps_the_not_found_message(
    user, organization, route, answer
):
    response = call(user, organization, route, answer)
    assert response.status_code == 404
    assert response.data == {"message": NOT_FOUND}
    assert "private" not in str(response.data)
    assert not AgentccMCPApproval.objects.exists()


def refuse_with_other_code():
    return httpx.Response(503, json={"error": "approval_state_unknown"})


def refuse_without_body():
    return httpx.Response(503, text="upstream unavailable")


def fail_with_approval_code_and_other_status():
    return httpx.Response(500, json={"error": "approval_unavailable"})


def refuse_with_the_router_body():
    return httpx.Response(503, json=ROUTER_404_BODY)


def refuse_with_structured_error():
    return httpx.Response(
        503, json={"error": {"code": "gateway_unavailable", "message": "private"}}
    )


def lose_the_connection():
    raise httpx.ConnectError("private transport detail")


@pytest.mark.parametrize(
    "answer",
    [
        refuse_with_other_code,
        refuse_without_body,
        fail_with_approval_code_and_other_status,
        refuse_with_the_router_body,
        refuse_with_structured_error,
        lose_the_connection,
    ],
    ids=lambda answer: answer.__name__,
)
@pytest.mark.parametrize("route", sorted(ROUTES))
@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
def test_every_other_gateway_failure_keeps_the_unconfirmed_message(
    user, organization, route, answer
):
    response = call(user, organization, route, answer)
    assert response.status_code == 503
    assert response.data == {"message": UNCONFIRMED}
    assert "private" not in str(response.data)
    assert not AgentccMCPApproval.objects.exists()
