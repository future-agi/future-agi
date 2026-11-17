"""Offline executable regressions for r2. No Django setup, DB, or network."""

import ast
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
SUPPORT = runpy.run_path(str(Path(__file__).with_name("test_hard_erase_flow.py")))
load = SUPPORT["load_functions"]


class EveryRootTests(unittest.TestCase):
    def test_nonidentity_closure_is_sensitive(self):
        member = type(
            "Member",
            (),
            {"_meta": SimpleNamespace(label_lower="accounts.workspacemembership")},
        )
        c = SimpleNamespace(
            data={member: [member()]}, fast_deletes=[], field_updates={}
        )
        self.assertTrue(load()["_sensitive"](c))

    def test_any_root_is_frozen(self):
        root = SimpleNamespace(_meta=SimpleNamespace(label_lower="example.root"))
        c = SimpleNamespace(origin=root)
        load(_original_collect=Mock())["remember_roots"](c, [root])
        self.assertEqual(c._hard_erase_roots, [root])

    def test_user_legacy_pointer_is_not_owned_effect(self):
        user = SimpleNamespace(
            pk="u",
            _meta=SimpleNamespace(label_lower="accounts.user", concrete_fields=[]),
        )
        # A forward pointer on the removed user is not a mutation of its target.
        field = SimpleNamespace(
            is_relation=True,
            many_to_one=True,
            one_to_one=False,
            attname="organization_id",
            target_field=SimpleNamespace(attname="pk"),
            remote_field=SimpleNamespace(
                model=type(
                    "Org",
                    (),
                    {"_meta": SimpleNamespace(label_lower="accounts.organization")},
                )
            ),
        )
        user._meta.concrete_fields = [field]
        user.organization_id = "survives"
        queries = Mock(side_effect=AssertionError("must not read surviving legacy org"))
        ns = load(models=SimpleNamespace(QuerySet=queries))
        self.assertEqual(ns["_organization_scopes"](user, "default"), set())

    def test_raw_delete_and_connection_wrapper_are_installed(self):
        text = (ROOT / "accounts/services/hard_erase.py").read_text()
        self.assertIn("install_sql_guard", text)
        self.assertIn("guarded_raw_delete", text)

    def test_issuer_locks_before_live_person(self):
        tree = ast.parse((ROOT / "accounts/services/service_grants.py").read_text())
        fn = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "issue_person_grant"
        )
        body = ast.unparse(fn)
        self.assertLess(body.index("lifecycle_lock"), body.index("live_person"))

    def test_shared_graph_batches_ten_thousand_rows(self):
        # Import only the dependency-free graph algorithm with a fake reader.
        path = ROOT / "accounts/services/hard_erase_graph.py"
        ns = runpy.run_path(str(path))
        field = SimpleNamespace(
            name="organization",
            attname="organization_id",
            is_relation=True,
            many_to_one=True,
            one_to_one=False,
            target_field=SimpleNamespace(attname="pk"),
        )
        org = type(
            "Org",
            (),
            {
                "_meta": SimpleNamespace(
                    label_lower="accounts.organization", concrete_fields=[]
                )
            },
        )
        field.remote_field = SimpleNamespace(model=org)
        cell = type(
            "Cell",
            (),
            {
                "_meta": SimpleNamespace(
                    label_lower="model_hub.cell", concrete_fields=[field]
                )
            },
        )
        rows = []
        for i in range(10000):
            row = cell()
            row.pk = i
            row.organization_id = "one"
            rows.append(row)
        target = org()
        target.pk = "one"
        reader = Mock(return_value=[target])
        graph = ns["OwnershipGraph"](rows, reader)
        for row in rows:
            self.assertEqual(graph.scopes(row), {"one"})
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(graph.nodes_read, 10001)


if __name__ == "__main__":
    unittest.main()
