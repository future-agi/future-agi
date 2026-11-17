"""Three data migrations declare the tables their data step reads and writes.

The update rehearsal treats a RunPython or RunSQL step as opaque: its table
scope comes only from the migration's literal UPDATE_SAFETY declaration. These
tests load the real migrations with Django's MigrationLoader and run the
rehearsal probe's own touched() and declared() (scripts/rehearse_update_compatibility.py,
PENDING_TABLE_SCOPE) on them. Reviewer, review link and large-volume rehearsal
stay the placeholders a reviewer replaces.
"""

import ast
import importlib.util
import inspect
import sys
import unittest
from pathlib import Path

from django.db.migrations.loader import MigrationLoader

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "update_rehearsal_probe", ROOT / "scripts/rehearse_update_compatibility.py"
)
probe = importlib.util.module_from_spec(SPEC)
# Registered before it runs, as dataclasses look the module up by name.
sys.modules[SPEC.name] = probe
_path = list(sys.path)
try:
    SPEC.loader.exec_module(probe)
finally:
    # The probe puts the repository root first on sys.path for its own imports.
    sys.path[:] = _path

SCHEMAS = {"postgres": "public", "clickhouse": "default"}
FIELDS = {
    "schema_version",
    "risk_classes",
    "tables",
    "mitigation",
    "review",
    "reviewer",
    "large_volume_rehearsal",
}


def scope_functions():
    """The probe's PENDING_TABLE_SCOPE definitions, without its final loader run."""
    body = []
    for node in ast.parse(probe.PENDING_TABLE_SCOPE).body:
        if any(
            isinstance(name, ast.Name) and name.id == "loader"
            for name in ast.walk(node)
        ):
            break
        body.append(node)
    # a, Path and inspect come from TARGET_LOADER_PLAN, which runs first in the image.
    env = {"a": {"database_schemas": dict(SCHEMAS)}, "Path": Path, "inspect": inspect}
    exec(
        compile(
            ast.Module(body=body, type_ignores=[]), "<pending-table-scope>", "exec"
        ),
        env,
    )
    return env


def declarations(migration):
    tree = ast.parse(Path(inspect.getsourcefile(migration.__class__)).read_text())
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "UPDATE_SAFETY"
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
    ]


class TestDataMigrationsDeclareUpdateSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scope = scope_functions()
        cls.loader = MigrationLoader(None, ignore_no_migrations=True)

    def assert_declared(self, app, name, models):
        """``models``: every model whose table the migration's data step reads or writes."""
        migration = self.loader.disk_migrations[(app, name)]
        state = self.loader.project_state((app, name), at_end=False)
        expected = {self.scope["model_table"](state, app, model) for model in models}
        scope = set()
        for operation in migration.operations:
            scope |= self.scope["touched"](operation, migration, state)
            operation.state_forwards(app, state)
        declared = self.scope["declared"](migration)
        self.assertLessEqual(expected, declared)
        self.assertLessEqual(expected, scope)

        values = declarations(migration)
        self.assertEqual(len(values), 1)
        value = ast.literal_eval(values[0])
        self.assertEqual(set(value), FIELDS)
        self.assertIs(type(value["schema_version"]), int)
        self.assertEqual(value["schema_version"], 1)
        self.assertIn("data_migration", value["risk_classes"])
        self.assertTrue(30 <= len(value["mitigation"].strip()) <= 2000)
        self.assertEqual(value["reviewer"], "<actual-github-login>")
        self.assertEqual(value["review"], "<actual-PR-review-discussion-permalink>")
        self.assertIs(value["large_volume_rehearsal"], True)

    def test_probe_scope_functions_are_present(self):
        for name in ("declared", "touched", "model_table"):
            self.assertTrue(callable(self.scope.get(name)), name)

    def test_usage_0032_ledger_opening_buckets(self):
        # _seed reads every account and each organization's entries; it updates accounts.
        self.assert_declared(
            "usage",
            "0032_ledger_opening_buckets",
            ("ManagedCreditAccount", "ManagedCreditEntry"),
        )

    def test_usage_0033_backfill_topup_payment_intent(self):
        # _fill reads and updates top-up entries only.
        self.assert_declared(
            "usage", "0033_backfill_topup_payment_intent", ("ManagedCreditEntry",)
        )

    def test_model_hub_0125_encrypt_model_hub_keys_at_rest(self):
        # KEY_COLUMNS: forwards, count() and backwards read these three; forwards rewrites them.
        self.assert_declared(
            "model_hub",
            "0125_encrypt_model_hub_keys_at_rest",
            ("ApiKey", "CustomAIModel", "SecretModel"),
        )
