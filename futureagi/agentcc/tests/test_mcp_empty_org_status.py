"""A fresh organization can browse/connect when the deployment serves MCP.

The real gateway distinguishes a missing MCP route (not_found) from an org
whose first configuration has not been pushed (org_not_found). Exercise the
actual HTTP adapter, not a fabricated exception that loses that distinction.
No database or upstream server is needed.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from agentcc.services import gateway_client, mcp_admin


class TestEmptyOrgMCPStatus(unittest.TestCase):
    def status(self, code, servers=(), http_status=404):
        response = httpx.Response(
            http_status,
            json={"error": {"code": code, "message": "fixture gateway refusal"}},
        )
        with (
            patch.object(mcp_admin, "org_server_ids", return_value=frozenset(servers)),
            patch.object(gateway_client.httpx, "Client") as client,
        ):
            client.return_value.__enter__.return_value.request.return_value = response
            return mcp_admin.org_status(SimpleNamespace(id="e2e-new-org"))

    def test_new_org_without_config_can_reach_the_first_connector(self):
        self.assertEqual(
            self.status("org_not_found"),
            {
                "enabled": False,
                "sessions": 0,
                "tools": 0,
                "resources": 0,
                "prompts": 0,
                "servers": [],
            },
        )

    def test_missing_config_with_saved_servers_is_not_reported_as_mcp_off(self):
        self.assertEqual(
            self.status("org_not_found", ["alpha"])["reason"],
            "gateway_unreachable",
        )

    def test_an_unregistered_route_still_reports_deployment_mcp_off(self):
        self.assertEqual(self.status("not_found")["reason"], "gateway_mcp_disabled")

    def test_other_not_found_codes_are_errors_not_deployment_mcp_off(self):
        self.assertEqual(
            self.status("virtual_server_not_found")["reason"], "gateway_unreachable"
        )

    def test_an_org_not_found_code_on_a_server_error_is_not_an_empty_org(self):
        self.assertEqual(
            self.status("org_not_found", http_status=503)["reason"],
            "gateway_unreachable",
        )

    def test_http_adapter_retains_only_a_string_error_code(self):
        for body, expected in [
            ({"error": {"code": "org_not_found"}}, "org_not_found"),
            ({"error": "not an object"}, None),
            ({"error": {"code": {"unexpected": "object"}}}, None),
            (["not an object"], None),
        ]:
            with (
                self.subTest(body=body),
                patch.object(gateway_client.httpx, "Client") as client,
            ):
                client.return_value.__enter__.return_value.request.return_value = (
                    httpx.Response(404, json=body)
                )
                with self.assertRaises(gateway_client.GatewayClientError) as caught:
                    gateway_client.GatewayClient("http://localhost:8080")._request(
                        "GET", "/-/mcp/status"
                    )
                self.assertEqual(
                    getattr(caught.exception, "error_code", None), expected
                )


if __name__ == "__main__":
    unittest.main()
