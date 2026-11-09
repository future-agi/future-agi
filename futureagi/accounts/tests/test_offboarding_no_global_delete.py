"""DB-free safety tripwire; behavioral coverage is in test_offboarding_cross_org.

Run directly with Python when the approved Django DB runner is unavailable.
"""

import ast
import unittest
from pathlib import Path


class TenantOffboardingSourceTests(unittest.TestCase):
    def test_tenant_entrypoints_do_not_delete_identity_or_clear_global_sessions(self):
        views = Path(__file__).resolve().parents[1] / "views"
        targets = {
            "signup.py": [(None, "delete_users")],
            "workspace_management.py": [
                ("DeleteUserAPIView", "post"),
                ("ManageTeamView", "delete"),
                ("DeactivateUserAPIView", "post"),
            ],
            "rbac_views.py": [("MemberRemoveAPIView", "delete")],
        }
        violations = []
        for filename, functions in targets.items():
            tree = ast.parse((views / filename).read_text())
            for class_name, function_name in functions:
                parent = tree
                if class_name:
                    parent = next(
                        node
                        for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == class_name
                    )
                function = next(
                    node
                    for node in parent.body
                    if isinstance(node, ast.FunctionDef) and node.name == function_name
                )
                for node in ast.walk(function):
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if ast.unparse(target) in {
                                "user.is_active",
                                "target_user.is_active",
                                "member.is_active",
                            }:
                                violations.append(
                                    f"{filename}:{node.lineno} global identity write"
                                )
                    if not isinstance(node, ast.Call):
                        continue
                    called = ast.unparse(node.func)
                    if called in {
                        "user.delete",
                        "target_user.delete",
                        "member.delete",
                        "clear_user_redis_cache",
                    }:
                        violations.append(f"{filename}:{node.lineno} {called}")
        self.assertEqual(
            violations, [], "Tenant offboarding cannot mutate the global identity"
        )


if __name__ == "__main__":
    unittest.main()
