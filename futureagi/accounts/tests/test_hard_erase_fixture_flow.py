"""Fixture routes use real executor/cursor method bodies; sockets remain blocked."""

import ast
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace

TESTS = Path(__file__).resolve().parent
MIGRATION = runpy.run_path(str(TESTS / "test_hard_erase_migrations_flow.py"))


class MigrationFixtureTests(unittest.TestCase):
    def test_fixture_sql_uses_executor_and_authority_does_not_escape(self):
        guard, connection, _, executor, cursor, driver = MIGRATION["harness"]()

        class Migration:
            atomic = True
            replaces = []

            def __init__(self, name, app_label):
                self.name, self.app_label = name, app_label

            def apply(self, state, schema_editor):
                for operation in self.operations:
                    operation(None, schema_editor)
                return state

        path = TESTS / "hard_erase_fixture.py"
        tree = ast.parse(path.read_text())
        tree.body = [
            node
            for node in tree.body
            if not (
                isinstance(node, ast.ImportFrom) and node.module.startswith("django.")
            )
        ]
        namespace = {
            "MigrationExecutor": type(executor),
            "ProjectState": object,
            "migrations": SimpleNamespace(
                Migration=Migration, RunPython=lambda callback: callback
            ),
        }
        exec(compile(tree, str(path), "exec"), namespace)
        sql = "INSERT INTO accounts_user(id) VALUES (%s)"
        with namespace["migration_fixture_cursor"](connection) as fixture:
            self.assertEqual(fixture.execute(sql, [1]), "driver")
            self.assertIsNone(fixture.fetchone())
        driver.assert_called_once()
        driver.reset_mock()
        with self.assertRaises(MIGRATION["Refused"]):
            cursor.execute(sql, [1])
        driver.assert_not_called()
        self.assertFalse(guard["migration_sql_allowed"](connection))


if __name__ == "__main__":
    unittest.main()
