"""Exact production exception dispatch, using the unchanged SAML call ASTs."""

import ast
import copy
import runpy
import unittest
from functools import partial
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

TESTS = Path(__file__).resolve().parent
SERVICES = TESTS.parent / "services"
ROOT = TESTS.parents[1]
SUPPORT = runpy.run_path(str(TESTS / "test_hard_erase_sql_flow.py"))
EXCEPTIONS = runpy.run_path(str(SERVICES / "hard_erase_sql_exceptions.py"))[
    "SQL_EXCEPTIONS"
]


class ExactSQLExceptionsTests(unittest.TestCase):
    def test_actual_callsite_statements_and_only_those_reach_driver(self):
        self.assertEqual(len(EXCEPTIONS), 2)
        for row in EXCEPTIONS:
            path = ROOT / row["file"]
            tree = ast.parse(path.read_text())
            original = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == row["function"]
            )
            call = next(
                node
                for node in ast.walk(original)
                if isinstance(node, ast.Call) and node.lineno == row["line"]
            )
            self.assertEqual(ast.unparse(call.func), "cursor.execute")
            self.assertEqual(
                sha256(call.args[0].value.encode()).hexdigest(), row["fingerprint"]
            )
            for variant in (
                "approved",
                "raw",
                "wrong_file",
                "wrong_function",
                "wrong_line",
                "changed_sql",
                "non_atomic",
                "other_connection",
                "many",
                "callback",
            ):
                with self.subTest(site=row["line"], variant=variant):
                    guard, connection, _ = SUPPORT["SQLBoundaryTests"]().namespace()
                    guard["_TABLES"]["accounts_organization_membership"] = set()
                    # Preserve the real guard filename used to distinguish its
                    # own frame from the first application caller.
                    guard_path = SERVICES / "hard_erase_sql.py"
                    guard_fn = next(
                        node
                        for node in ast.parse(guard_path.read_text()).body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "guard_execute"
                    )
                    guard_fn.body = [
                        node
                        for node in guard_fn.body
                        if not isinstance(node, ast.ImportFrom)
                    ]
                    exec(
                        compile(
                            ast.Module(body=[guard_fn], type_ignores=[]),
                            str(guard_path),
                            "exec",
                        ),
                        guard,
                    )
                    driver = Mock(return_value="ok")
                    dispatch = partial(
                        guard["guard_execute"],
                        driver,
                        many=variant == "many",
                        context={"connection": connection},
                    )
                    connection.in_atomic_block = variant != "non_atomic"
                    connection.alias = (
                        "other" if variant == "other_connection" else "default"
                    )
                    namespace = {
                        "__name__": "saml2_auth.services",
                        "cursor": SimpleNamespace(execute=dispatch),
                        "user": SimpleNamespace(id="user-fixture"),
                        "attempt": SimpleNamespace(organization_id="org-fixture"),
                    }
                    modified = copy.deepcopy(call)
                    if variant == "changed_sql":
                        modified.args[0].value += " /* different bytes */"
                    fn = ast.FunctionDef(
                        name="wrong_function"
                        if variant == "wrong_function"
                        else row["function"],
                        args=ast.arguments(
                            posonlyargs=[],
                            args=[],
                            kwonlyargs=[],
                            kw_defaults=[],
                            defaults=[],
                        ),
                        body=[ast.copy_location(ast.Expr(value=modified), modified)],
                        decorator_list=[],
                    )
                    ast.copy_location(fn, original)
                    module = ast.fix_missing_locations(
                        ast.Module(body=[fn], type_ignores=[])
                    )
                    if variant == "wrong_line":
                        ast.increment_lineno(module, 1)
                    filename = (
                        str(path.with_name("other.py"))
                        if variant == "wrong_file"
                        else str(path)
                    )
                    exec(compile(module, filename, "exec"), namespace)
                    if variant == "callback":

                        def callback(sql, params, dispatch=dispatch):
                            return dispatch(sql, params)

                        namespace["cursor"] = SimpleNamespace(execute=callback)
                    if variant == "raw":
                        action = partial(dispatch, call.args[0].value, [])
                    else:
                        action = namespace[fn.name]
                    if variant == "approved":
                        action()
                        driver.assert_called_once()
                    else:
                        with self.assertRaises(SUPPORT["Refused"]):
                            action()
                        driver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
