"""Real endpoint bodies keep validation order under entry locks."""

import ast
import dataclasses
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

TESTS = Path(__file__).resolve().parent
APP = TESTS.parents[1]
SQL = runpy.run_path(str(TESTS / "test_hard_erase_sql_flow.py"))
LIFE = runpy.run_path(str(TESTS / "test_hard_erase_lifecycle_flow.py"))
ENTRY = runpy.run_path(str(TESTS / "hard_erase_entry_support.py"))


def body(path, class_name, method_name, namespace):
    """Execute unchanged production control flow, replacing only dependencies."""
    cls = next(
        node
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    method.decorator_list = []

    class Imports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            if node.module.startswith(("accounts.", "django.", "ai_tools.")):
                return ast.Pass()
            return node

    method = ast.fix_missing_locations(Imports().visit(method))
    exec(
        compile(
            "from __future__ import annotations\n" + ast.unparse(method),
            str(path),
            "exec",
        ),
        namespace,
    )
    return namespace[method_name]


def result_class():
    path = APP / "ai_tools/base.py"
    cls = next(
        node
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.ClassDef) and node.name == "ToolResult"
    )
    namespace = {"dataclass": dataclasses.dataclass}
    exec(
        compile(
            "from __future__ import annotations\n" + ast.unparse(cls), str(path), "exec"
        ),
        namespace,
    )
    return namespace["ToolResult"]


class EntryValidationTests(unittest.TestCase):
    def tool_case(self, role, workspace_missing, erased=None):
        org, actor, target, workspace = (UUID(int=n) for n in (1, 2, 3, 4))
        live = {org, actor} - ({erased} if erased else set())
        life, _, cursor = LIFE["LifecycleTests"]().namespace()
        entry = ENTRY["entry_namespace"](life, live=live)
        result = result_class()
        entry["ToolResult"] = result  # Also exercises the old early return at RED.
        keys = life["identity_keys"](organizations=[org], people=[actor, target])
        events = []

        def held(event):
            life["require_held"]("default", keys)
            events.append(event)

        class Actor:
            pk = actor

            @property
            def organization_role(self):
                held("permission")
                return role

        class Missing(Exception):
            pass

        def get_workspace(**kwargs):
            held("workspace")
            if workspace_missing:
                raise Missing()
            return SimpleNamespace(pk=workspace)

        def get_user(**kwargs):
            held("user")
            raise Missing()

        method = body(
            APP / "ai_tools/tools/users/add_workspace_member.py",
            "AddWorkspaceMemberTool",
            "execute",
            {
                "ToolResult": result,
                "OrganizationRoles": SimpleNamespace(
                    OWNER="Owner", ADMIN="Admin", MEMBER="Member"
                ),
                "Workspace": SimpleNamespace(
                    objects=SimpleNamespace(get=get_workspace), DoesNotExist=Missing
                ),
                "User": SimpleNamespace(
                    objects=SimpleNamespace(get=get_user), DoesNotExist=Missing
                ),
            },
        )
        context = SimpleNamespace(organization=SimpleNamespace(pk=org), user=Actor())
        params = SimpleNamespace(user_id=target, workspace_id=workspace)
        wrapped = entry["tool_issuance_entry"](method)
        if erased:
            with self.assertRaises(LIFE["Refused"]):
                wrapped(object(), params, context)
            self.assertEqual(events, [])
            response = None
        else:
            response = wrapped(object(), params, context)
        self.assertEqual(
            [call.args[1][0] for call in cursor.execute.call_args_list[1:]],
            [life["advisory_number"](key) for key in sorted(keys)],
        )
        self.assertIsNone(life["_ACTIVE"].get())
        self.assertIsNone(entry["_PLANNED"].get())
        return response, events

    def test_denied_viewer_with_missing_user_checks_permission_first(self):
        response, events = self.tool_case("Viewer", False)
        self.assertEqual(response.error_code, "PERMISSION_DENIED")
        self.assertEqual(events, ["permission"])

    def test_missing_workspace_and_user_reports_workspace_first(self):
        response, events = self.tool_case("Admin", True)
        self.assertEqual(response.error_code, "NOT_FOUND")
        self.assertIn("Workspace with ID", response.content)
        self.assertEqual(events, ["permission", "workspace"])

    def test_allowed_actor_and_live_workspace_reaches_original_user_not_found(self):
        response, events = self.tool_case("Admin", False)
        self.assertEqual(response.error_code, "NOT_FOUND")
        self.assertIn("User with ID", response.content)
        self.assertEqual(events, ["permission", "workspace", "user"])

    def test_erased_actor_or_org_still_refuses_before_tool_body(self):
        for erased in (UUID(int=1), UUID(int=2)):
            with self.subTest(erased=erased):
                self.tool_case("Admin", False, erased)

    def test_role_view_reaches_its_own_missing_user_branch_under_complete_lock(self):
        org, actor, target = (UUID(int=n) for n in (1, 2, 3))
        life, _, _ = LIFE["LifecycleTests"]().namespace()
        entry = ENTRY["entry_namespace"](life, live={org, actor})
        keys = life["identity_keys"](organizations=[org], people=[actor, target])
        calls = []

        class Missing(Exception):
            pass

        def get_user(**kwargs):
            life["require_held"]("default", keys)
            calls.append(kwargs)
            raise Missing()

        def not_found(message):
            life["require_held"]("default", keys)
            return 404, message

        request = SimpleNamespace(
            user=SimpleNamespace(pk=actor),
            organization=SimpleNamespace(pk=org),
            validated_data={"user_id": target, "new_role": "Member"},
        )
        method = body(
            APP / "accounts/views/workspace_management.py",
            "UserRoleUpdateAPIView",
            "post",
            {
                "resolve_org": lambda request: request.organization,
                "User": SimpleNamespace(
                    objects=SimpleNamespace(get=get_user), DoesNotExist=Missing
                ),
            },
        )
        view = SimpleNamespace(_gm=SimpleNamespace(not_found=not_found))
        self.assertEqual(
            entry["request_issuance_entry"](method)(view, request),
            (404, "User not found in organization"),
        )
        self.assertEqual(calls, [{"id": target, "organization": request.organization}])
        self.assertIsNone(life["_ACTIVE"].get())


class ExplainTableTests(unittest.TestCase):
    def dispatch(self, text, allowed):
        ns, conn, _ = SQL["SQLBoundaryTests"]().namespace()
        for many in (False, True):
            with self.subTest(sql=text, many=many):
                driver = Mock(return_value="ok")
                if allowed:
                    self.assertEqual(
                        ns["guard_execute"](
                            driver, text, [], many, {"connection": conn}
                        ),
                        "ok",
                    )
                    driver.assert_called_once()
                else:
                    with self.assertRaises(SQL["Refused"]):
                        ns["guard_execute"](
                            driver, text, [], many, {"connection": conn}
                        )
                    driver.assert_not_called()

    def test_table_reads_obey_pure_read_rule(self):
        for table in ("ordinary_business_table", "accounts_user"):
            for prefix in (
                "",
                "EXPLAIN ",
                "EXPLAIN (FORMAT JSON) ",
                "EXPLAIN VERBOSE ",
            ):
                # This supersedes protected option-prefix positives.
                self.dispatch(
                    f"{prefix}TABLE {table}",
                    table != "accounts_user" or prefix in ("", "EXPLAIN "),
                )
            for prefix in (
                "EXPLAIN ANALYZE ",
                "EXPLAIN ANALYSE ",
                "EXPLAIN ANALYSE VERBOSE ",
                "EXPLAIN ANALYZE VERBOSE ",
                "EXPLAIN (ANALYZE, FORMAT JSON) ",
                "EXPLAIN (ANALYSE, FORMAT JSON) ",
            ):
                self.dispatch(f"{prefix}TABLE {table}", table != "accounts_user")

    def test_explain_prefix_stops_before_unknown_wrapped_statement(self):
        policy = runpy.run_path(str(TESTS.parent / "services/hard_erase_sql_policy.py"))
        for prefix in ("EXPLAIN ", "EXPLAIN (FORMAT JSON) ", "EXPLAIN VERBOSE "):
            # Unknown syntax is the driver's concern on ordinary tables. It must
            # never become a protected read by searching ahead for SELECT.
            for tail in (
                "ordinary_business_table",
                "accounts_user",
                "accounts_user SELECT 1",
            ):
                text = prefix + "UNKNOWN_COMMAND " + tail
                words = next(policy["_lex"](text))
                with self.subTest(sql=text):
                    self.assertEqual(
                        policy["effective_statement"](words), "unknown_command"
                    )
                    self.dispatch(text, "accounts_user" not in tail)

    def test_executing_and_mutating_forms_still_refuse(self):
        for prefix in (
            "",
            "EXPLAIN ",
            "EXPLAIN (FORMAT JSON) ",
            "EXPLAIN ANALYZE VERBOSE ",
            "EXPLAIN ANALYSE VERBOSE ",
            "EXPLAIN (ANALYSE, FORMAT JSON) ",
            "PREPARE p AS ",
        ):
            for statement in ("EXECUTE p", "CALL proc()", "DO $$ BEGIN NULL; END $$"):
                self.dispatch(prefix + statement, False)
        self.dispatch("EXPLAIN TABLE accounts_user; DELETE FROM accounts_user", False)
        self.dispatch("EXPLAIN (FORMAT JSON) TABLE accounts_user FOR UPDATE", False)
        for text in ("EXPLAIN", "EXPLAIN (FORMAT JSON)", "EXPLAIN (FORMAT JSON"):
            self.dispatch(text, False)


if __name__ == "__main__":
    unittest.main()
