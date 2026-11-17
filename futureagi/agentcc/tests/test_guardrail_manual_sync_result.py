"""Dependency-free response regression; DB/service integration is tested separately.

Extract the actual view methods, without importing Django or opening sockets.
Run directly with Python for a red/green response check on an offline host.
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


class TestManualSyncResult(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "views" / "guardrail_policy.py"
        tree = ast.parse(path.read_text())
        view = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "AgentccGuardrailPolicyViewSet"
        )
        methods = [
            node
            for node in view.body
            if isinstance(node, ast.FunctionDef) and node.name in ("sync", "_sync")
        ]
        for method in methods:
            method.decorator_list = []
        warning = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_GATEWAY_SYNC_WARNING"
                for target in node.targets
            )
        )
        self.push = Mock()
        # PUSH-SYNC adds this exception name to the extracted methods. Bind
        # that dependency here; the real typed-refusal and transaction paths
        # remain the responsibility of the PostgreSQL integration suites.
        placement_refused = type("SyntheticPlacementRefused", (Exception,), {})
        namespace = {
            "sync_guardrail_policies": self.push,
            "logger": Mock(),
            "MCPCredentialPlacementRefused": placement_refused,
        }
        module = ast.Module(body=[warning, *methods], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
        self.warning = namespace["_GATEWAY_SYNC_WARNING"]
        self.view = SimpleNamespace(
            _gm=SimpleNamespace(
                success_response=lambda data: {"status": True, "result": data},
                bad_request=lambda message: {"status": False, "message": message},
            )
        )
        self.view._sync = lambda org: namespace["_sync"](self.view, org)
        self.sync = lambda request: namespace["sync"](self.view, request)
        self.org = SimpleNamespace(id="fixture-org")
        self.request = SimpleNamespace(organization=self.org)

    def test_synced_is_false_when_service_skips_without_an_active_config(self):
        self.push.return_value = False
        response = self.sync(self.request)
        self.assertIs(response["result"]["synced"], False)
        self.assertEqual(
            response,
            {
                "status": True,
                "result": {
                    "synced": False,
                    "gateway_synced": False,
                    "gateway_warning": self.warning,
                },
            },
        )
        self.push.assert_called_once_with(self.org, user=None)

    def test_success_still_reports_both_sync_fields_without_a_warning(self):
        self.push.return_value = True
        self.assertEqual(
            self.sync(self.request),
            {"status": True, "result": {"synced": True, "gateway_synced": True}},
        )

    def test_sync_exception_reports_false_without_changing_the_envelope(self):
        self.push.side_effect = RuntimeError("fixture failure")
        response = self.sync(self.request)
        self.assertIs(response["result"]["synced"], False)
        self.assertIs(response["result"]["gateway_synced"], False)
        self.assertIs(response["status"], True)
        self.assertEqual(response["result"]["gateway_warning"], self.warning)

    def test_missing_organization_keeps_the_existing_error_path(self):
        self.assertIs(self.sync(SimpleNamespace())["status"], False)
        self.push.assert_not_called()


if __name__ == "__main__":
    unittest.main()
