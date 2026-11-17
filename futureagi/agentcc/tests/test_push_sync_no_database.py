"""Small executable service-unit rows without Django setup or database access.

Load the actual function bodies, not copied implementations. PostgreSQL rows in
sibling files separately prove transaction/endpoint behavior. Run this file with
python3 -I -B; all dependencies here are in-process fakes.
"""

import ast
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SERVICES = Path(__file__).resolve().parents[1] / "services"
WARNING = {
    "code": "gateway_sync_failed",
    "operation": "shared_credential_revocation",
    "message": "The gateway has not confirmed it revoked the stored key yet.",
}


def load_function(filename, name, namespace):
    tree = ast.parse((SERVICES / filename).read_text())
    node = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name
    )
    exec(
        compile(
            ast.Module(body=[node], type_ignores=[]), str(SERVICES / filename), "exec"
        ),
        namespace,
    )
    return namespace[name]


class TestEvictionCallback(unittest.TestCase):
    def run_callback(self, outcome):
        callbacks = []
        logger = Mock()
        retry = Mock(side_effect=outcome)
        credential = Mock()
        credential.Scope.SHARED = "shared"
        namespace = {
            "Credential": credential,
            "STATIC_KINDS": ("bearer", "api_key"),
            "transaction": SimpleNamespace(on_commit=callbacks.append),
            "_retry_org_locked": retry,
            "current_sync_warnings": lambda: [],
            "logger": logger,
            "GATEWAY_SYNC_FAILED": WARNING["code"],
            "SHARED_CREDENTIAL_REVOCATION": WARNING["operation"],
            "SHARED_CREDENTIAL_REVOCATION_MESSAGE": WARNING["message"],
        }
        evict = load_function(
            "mcp_shared_credential_lifecycle.py", "evict_after_commit", namespace
        )
        rows = [
            SimpleNamespace(
                pk=i,
                organization_id=f"org-{i}",
                server_id="server",
                scope="shared",
                kind="bearer",
            )
            for i in (1, 2)
        ]
        warnings = evict(rows)
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        return warnings, retry, logger

    def test_exception_is_logged_contained_and_other_org_is_attempted(self):
        warnings, retry, logger = self.run_callback(
            [RuntimeError("synthetic failure"), True]
        )
        self.assertEqual(retry.call_count, 2)
        logger.exception.assert_called_once_with(
            "mcp_shared_eviction_after_commit_failed", org_id="org-1"
        )
        self.assertIn(WARNING, warnings)

    def test_false_result_retains_warning(self):
        warnings, retry, _ = self.run_callback([False, True])
        self.assertEqual(retry.call_count, 2)
        self.assertIn(WARNING, warnings)

    def test_confirmed_eviction_removes_pending_warning(self):
        warnings, _, _ = self.run_callback([True, True])
        self.assertEqual(warnings, [])


class TestDCRPreflight(unittest.TestCase):
    def configure(self, refused):
        class PlacementRefused(ValueError):
            pass

        org = SimpleNamespace(pk="org")
        plan = SimpleNamespace(
            modes=["user"],
            transport="http",
            era="modern",
            catalog_ref="fixture/vendor",
            card={"id": "mcp"},
            timeout_ms=None,
            forward_headers=[],
            tools=[],
        )
        app = SimpleNamespace(card={})
        config = SimpleNamespace(mcp={"enabled": True}, version=2)
        catalog = Mock()
        catalog._servers.return_value = {}
        catalog._virtual_servers.return_value = {}
        catalog._virtual_server.return_value = {}
        # The fixture provider takes dynamic registration, not only the
        # organization's own OAuth app, so the real helper says no.
        catalog.oauth2_by_org_app.return_value = False
        oauth = Mock()
        found = SimpleNamespace(
            registration_endpoint="https://as.example.com/register",
            auth_methods=["none"],
        )
        oauth.discover.return_value = found
        push = Mock()
        push.push_org_config.return_value = True
        if refused:
            push._mcp_payload.side_effect = PlacementRefused("fixture refusal")

        def writer(org, update, **kwargs):
            update(config)
            kwargs["push"](config)
            return config, []

        namespace = {
            "_target": lambda *args: (app, plan, "https://mcp.example.com/mcp", {}),
            "_can_add": Mock(),
            # The fixture app is not a Microsoft app, so the Microsoft gate
            # lets it through; the gate itself has its own tests.
            "microsoft": SimpleNamespace(
                require_microsoft_connect_enabled=lambda *args, **kwargs: None
            ),
            "catalog": catalog,
            "copy": copy,
            "oauth": oauth,
            "config_push": push,
            "vault": Mock(),
            "auth_method": Mock(return_value="none"),
            "write_config_version_with": writer,
            "configuration": lambda *args: {},
            "is_gateway_synced": lambda warnings: not warnings,
        }
        load_function("connector_mcp_oauth_app.py", "_org_app_record", namespace)
        configure = load_function("connector_mcp_oauth_app.py", "configure", namespace)
        with (
            patch("socket.socket", side_effect=AssertionError("network forbidden")),
            patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")),
        ):
            if refused:
                with self.assertRaises(PlacementRefused):
                    configure(
                        org, "app", {"mode": "per_user", "registration": "dcr"}, None
                    )
                oauth.discover.assert_not_called()
                oauth._registered_client.assert_not_called()
                push.push_org_config.assert_not_called()
            else:
                self.assertTrue(
                    configure(
                        org, "app", {"mode": "per_user", "registration": "dcr"}, None
                    )["gateway_synced"]
                )
                oauth._registered_client.assert_called_once_with(found)
                push.push_org_config.assert_called_once_with("org", config)
        push._mcp_payload.assert_called_once_with(
            "org", config, with_upstream_keys=True, refuse_unapplied_placements=True
        )

    def test_refusal_precedes_every_external_call(self):
        self.configure(True)

    def test_allowed_placement_still_registers(self):
        self.configure(False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
