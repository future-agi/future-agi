"""RED-first, DB-free algorithm tests; real codec, fake historical ORM only."""

import ast
import importlib
import unittest
from pathlib import Path

from django.db import models

ROOT = Path(__file__).resolve().parents[1]
NAMES = [
    "0075_webhook_credential_columns",
    "0076_encrypt_webhook_credentials",
    "0077_remove_webhook_plaintext",
]


class SchemaTests(unittest.TestCase):
    def test_model_does_not_persist_plaintext(self):
        tree = ast.parse((ROOT / "models/webhook.py").read_text())
        node = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "AgentccWebhook"
        )
        fields = {
            n.targets[0].id: n.value
            for n in node.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
        }
        self.assertNotIn(
            "headers", fields, "ordinary JSONField persists header values at rest"
        )
        self.assertNotIn(
            "secret", fields, "ordinary CharField persists signing secret at rest"
        )
        for name in [
            "encrypted_headers",
            "encrypted_secret",
            "credential_storage_version",
        ]:
            field = eval(
                compile(ast.Expression(fields[name]), "<model field>", "eval"),
                {"models": models},
            )
            self.assertFalse(field.null)
            self.assertFalse(field.blank)
            self.assertFalse(field.editable)
            self.assertFalse(field.has_default())

    def test_three_stage_clear_last_graph(self):
        from django.db.migrations import AddField, AlterField, RemoveField, RunPython

        for name in NAMES:
            self.assertTrue(
                (ROOT / "migrations" / (name + ".py")).exists(),
                "required fenced migration missing",
            )
        m1, m2, m3 = [
            importlib.import_module("agentcc.migrations." + name).Migration
            for name in NAMES
        ]
        self.assertEqual(m1.dependencies, [("prism", "0074_api_key_provisioning_state")])
        self.assertTrue(all(isinstance(op, AddField) for op in m1.operations[:3]))
        self.assertIsInstance(m1.operations[-1], RunPython)
        self.assertFalse(m2.atomic)
        self.assertFalse(m2.operations[0].atomic)
        self.assertIsInstance(m3.operations[0], RunPython)
        self.assertEqual(
            [op.name for op in m3.operations if isinstance(op, RemoveField)],
            ["headers", "secret"],
        )
        self.assertEqual(
            len([op for op in m3.operations if isinstance(op, AlterField)]), 3
        )
        self.assertIsInstance(m3.operations[-1], RunPython)
        self.assertTrue(m3.atomic)

    def test_native_historical_state_has_unfiltered_manager_and_final_constraints(self):
        import django
        from django.db.migrations.state import ModelState, ProjectState

        django.setup()
        state = ProjectState()
        state.add_model(
            ModelState(
                "prism",
                "AgentccWebhook",
                [
                    ("id", models.UUIDField(primary_key=True)),
                    ("headers", models.JSONField(default=dict)),
                    ("secret", models.CharField(max_length=255, default="")),
                    ("deleted", models.BooleanField(default=False)),
                    ("is_active", models.BooleanField(default=True)),
                ],
            )
        )
        for index, name in enumerate(NAMES):
            migration = importlib.import_module("agentcc.migrations." + name).Migration(
                name, "prism"
            )
            state = migration.mutate_state(state)
            model = state.apps.get_model("prism", "AgentccWebhook")
            self.assertFalse(model._base_manager.all().query.where.children)
            self.assertEqual(
                model._base_manager.using("default_direct").db, "default_direct"
            )
            fields = {field.name: field for field in model._meta.fields}
            if index < 2:
                self.assertIn("headers", fields)
                self.assertIn("secret", fields)
                self.assertTrue(fields["encrypted_headers"].null)
            else:
                self.assertNotIn("headers", fields)
                self.assertNotIn("secret", fields)
                for field in (
                    "encrypted_headers",
                    "encrypted_secret",
                    "credential_storage_version",
                ):
                    self.assertFalse(fields[field].null)
                    self.assertFalse(fields[field].has_default())
                self.assertEqual(
                    {constraint.name for constraint in model._meta.constraints},
                    {"webhook_credentials_v1", "webhook_credentials_nonempty"},
                )

    def test_rotation_registered_raw_binary(self):
        tree = ast.parse((ROOT / "services/gateway_key_inventory.py").read_text())
        stores = ast.literal_eval(
            next(
                n.value
                for n in tree.body
                if isinstance(n, ast.Assign) and n.targets[0].id == "_STORES"
            )
        )
        self.assertIn(
            ("AgentccWebhook", ("encrypted_headers", "encrypted_secret"), False), stores
        )
