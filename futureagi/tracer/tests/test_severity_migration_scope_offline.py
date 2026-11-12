"""DB-free proof of the severity roundtrip's migration authority boundary.

Use the real Django executor bodies and hard_erase_migrations scope, with only
schema IO and migration operations doubled. The PostgreSQL roundtrip stays in
its original integration test; this does not certify DDL.
"""

import ast
import runpy
import unittest
from contextvars import copy_context
from inspect import unwrap
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from django.db.migrations.executor import MigrationExecutor

ROOT = Path(__file__).resolve().parents[2]


class SeverityMigrationScopeTests(unittest.TestCase):
    def harness(self):
        scope = runpy.run_path(str(ROOT / "accounts/services/hard_erase_migrations.py"))

        # The host may already be guarded with a different module's ContextVar.
        # Wrap the original Django bodies with this isolated scope instead.
        class Executor:
            apply_migration = unwrap(MigrationExecutor.apply_migration)
            unapply_migration = unwrap(MigrationExecutor.unapply_migration)

        scope["install_migration_guard"](Executor)
        tree = ast.parse((ROOT / "accounts/tests/hard_erase_fixture.py").read_text())
        fixture = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "FixtureExecutor"
        )
        namespace = {"MigrationExecutor": Executor, "SimpleNamespace": NS}
        exec(
            compile(
                ast.Module(body=[fixture], type_ignores=[]),
                "<fixture executor>",
                "exec",
            ),
            namespace,
        )
        allowed = scope["migration_sql_allowed"]
        connection = NS()
        history = []
        captured = []
        outer = self

        class Editor:
            deferred_sql = []

            def __enter__(self):
                outer.assertTrue(allowed(connection))
                return self

            def __exit__(self, *args):
                # Deferred SQL must still be in scope through editor exit.
                outer.assertTrue(allowed(connection))
                history.append("editor_exit")

        connection.schema_editor = lambda **kwargs: Editor()

        def operation(direction, state, editor):
            self.assertTrue(allowed(connection))
            self.assertFalse(allowed(NS()))
            captured.append(copy_context())
            history.append(direction)
            return state

        migration = NS(
            atomic=True,
            replaces=[],
            app_label="tracer",
            name="offline",
            apply=lambda state, editor: operation("apply", state, editor),
            unapply=lambda state, editor: operation("unapply", state, editor),
        )
        return (
            namespace["FixtureExecutor"](connection),
            connection,
            allowed,
            history,
            captured,
            migration,
        )

    def test_real_executor_scopes_both_directions_and_revokes_copied_context(self):
        executor, connection, allowed, history, captured, migration = self.harness()
        state = object()
        self.assertFalse(allowed(connection))
        self.assertIs(executor.unapply_migration(state, migration), state)
        self.assertFalse(allowed(connection))
        self.assertIs(executor.apply_migration(state, migration), state)
        self.assertFalse(allowed(connection))
        self.assertEqual(history, ["unapply", "editor_exit", "apply", "editor_exit"])
        for context in captured:
            self.assertFalse(context.run(allowed, connection))

    def test_executor_failure_revokes_authority(self):
        executor, connection, allowed, history, captured, migration = self.harness()
        migration.apply = Mock(side_effect=RuntimeError("synthetic migration failure"))
        with self.assertRaisesRegex(RuntimeError, "synthetic migration failure"):
            executor.apply_migration(object(), migration)
        self.assertFalse(allowed(connection))
        self.assertEqual(history, ["editor_exit"])


if __name__ == "__main__":
    unittest.main()
