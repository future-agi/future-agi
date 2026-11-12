"""Try a tool (test-mcp-tool) refused by the organization's MCP access default
says why, in a sentence Django holds; every other failure stays the generic
"Gateway request failed.", and no gateway or upstream text is passed on."""

import json

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.tests.test_mcp_admin_api import V1_STATUS, faked_gateway

SENTENCE = (
    "This organization's MCP access default is Deny, so tools can't run here. "
    "Add this server to a virtual server, or change mcp.access.default in the "
    "organization config."
)
GENERIC = {"error": {"message": "Gateway request failed.", "code": "gateway_error"}}
PLANTED = "PLANTED-GATEWAY-TEXT-7f3a19"


@pytest.fixture
def gateway_id():
    return "default"


@pytest.fixture
def fake_gateway():
    with faked_gateway() as gateway:
        yield gateway


def _try_tool(fake_gateway, auth_client, gateway_id, user, gateway_result):
    AgentccOrgConfig.no_workspace_objects.create(
        organization=user.organization,
        version=1,
        is_active=True,
        mcp={"servers": {"server-a": {"url": "http://mcp"}}},
    )
    fake_gateway.on("GET", "/-/mcp/status", body=V1_STATUS)
    fake_gateway.on(
        "GET",
        "/-/mcp/tools",
        body=[{"name": "server-a_tool-1", "server_id": "server-a"}],
    )
    fake_gateway.on("POST", "/-/mcp/test", body=gateway_result)
    response = auth_client.post(
        f"/agentcc/gateways/{gateway_id}/test-mcp-tool/",
        {"name": "server-a_tool-1", "arguments": {}},
        format="json",
    )
    assert response.status_code == 200, response.json()
    return response.json()["result"]


def _refused(rule=None, error=PLANTED):
    result = {
        "is_error": False,
        "duration_ms": 0,
        "guardrail_pre": "skipped",
        "guardrail_post": "skipped",
        "error": error + " [tool_denied]",
        "server": "server-a",
        "server_id": "server-a",
        "code": "tool_denied",
    }
    if rule is not None:
        result["rule"] = rule
    return result


@pytest.mark.integration
@pytest.mark.api
class TestTryToolDenyReason:
    def test_access_default_refusal_names_the_setting(
        self, fake_gateway, auth_client, gateway_id, user
    ):
        result = _try_tool(
            fake_gateway, auth_client, gateway_id, user, _refused("access.default")
        )

        assert result == {
            "error": {"message": SENTENCE, "code": "mcp_access_default_deny"}
        }
        assert PLANTED not in json.dumps(result)

    @pytest.mark.parametrize(
        "gateway_result",
        [
            _refused("access.rules[0]"),
            _refused("header"),
            _refused("policy:no-echo"),
            _refused(None),
            {**_refused("access.default"), "code": "tool_quarantined"},
            {"code": "upstream_error", "is_error": True, "error": PLANTED},
            {"is_error": True, "content": [{"type": "text", "text": PLANTED}]},
        ],
        ids=[
            "another rule",
            "the allow-tools header",
            "a tool policy",
            "no rule (an older gateway)",
            "access.default on another code",
            "an upstream error",
            "a tool that answered an error",
        ],
    )
    def test_every_other_failure_stays_generic(
        self, fake_gateway, auth_client, gateway_id, user, gateway_result
    ):
        result = _try_tool(fake_gateway, auth_client, gateway_id, user, gateway_result)

        assert result == GENERIC
        assert PLANTED not in json.dumps(result)

    def test_the_gateway_text_is_never_shown(
        self, fake_gateway, auth_client, gateway_id, user
    ):
        # Even a gateway that sent the very sentence, or text of its own,
        # is answered with Django's sentence, not what it sent.
        result = _try_tool(
            fake_gateway,
            auth_client,
            gateway_id,
            user,
            _refused("access.default", error=PLANTED + " " + SENTENCE),
        )

        assert result["error"]["message"] == SENTENCE
        assert PLANTED not in json.dumps(result)
