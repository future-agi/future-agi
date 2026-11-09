"""Offline contract checks; database proofs live in test_hard_erase_db.py.

Run directly with Python (no Django initialization or provider/network access).
These are structural tripwires, not a substitute for PostgreSQL T1-T8 execution.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source(path):
    return (ROOT / path).read_text()


class HardEraseContract(unittest.TestCase):
    def test_t4_common_boundary_precedes_every_receiver(self):
        text = source("accounts/services/hard_erase.py")
        tree = ast.parse(text)
        functions = {
            n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
        }
        body = ast.unparse(functions["guarded_delete"])
        self.assertLess(body.index("locked_preflight"), body.index("_original_delete"))
        self.assertIn("Collector.delete = guarded_delete", text)
        self.assertIn("install_hard_erase_guard()", source("accounts/apps.py"))

    def test_t2_t3_unfiltered_closure_and_updates(self):
        text = source("accounts/services/hard_erase.py")
        self.assertIn("QuerySet(model=", text)
        self.assertIn("field_updates", text)
        self.assertIn("fast_deletes", text)
        self.assertIn("Collector.related_objects = unfiltered_related_objects", text)
        self.assertNotIn(".objects.", text)

    def test_t6_per_key_lock_before_recollection_and_receiver(self):
        text = source("accounts/services/hard_erase.py")
        self.assertNotIn("LOCK TABLE", text)
        self.assertIn(
            "read committed", source("accounts/services/hard_erase_lifecycle.py")
        )
        tree = ast.parse(text)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "locked_preflight"
        )
        body = ast.unparse(fn)
        locked = body[body.index("with lifecycle_lock") :]
        self.assertLess(locked.index("_collect_groups"), locked.index("require_held"))
        self.assertLess(locked.index("require_held"), locked.index("_check_closure"))
        self.assertIn("keys=keys", locked)
        self.assertLess(body.index("_check_closure"), body.index("yield"))

    def test_t1_admin_preserves_framework_permission_checks(self):
        text = source("accounts/admin.py")
        self.assertIn(
            "class OrganizationAdmin(HardEraseAdminMixin, admin.ModelAdmin)", text
        )
        self.assertIn("class CustomUserAdmin(HardEraseAdminMixin, UserAdmin)", text)
        mixin = source("accounts/services/hard_erase_admin.py")
        self.assertIn("super().delete_view", mixin)
        self.assertIn("super().changelist_view", mixin)
        self.assertNotIn("def has_delete_permission", mixin)

    def test_t5_collection_has_no_external_callback(self):
        text = source("omega_hub/models/deletion.py")
        self.assertNotIn("erasure.forget_avatars(sub_objs, using)", text)
        text = source("omega_hub/services/erasure.py")
        self.assertIn("transaction.on_commit(cache_keys.invalidate_listing", text)

    def test_t8_command_preflight_before_clickhouse(self):
        tree = ast.parse(source("model_hub/management/commands/gt_roundtrip_test.py"))
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_teardown"
        )
        body = ast.unparse(fn)
        self.assertLess(
            body.index("locked_preflight"), body.index("ClickHouseVectorDB()")
        )
        self.assertIn("authorized_scope", body)
        self.assertIn("cannot undo", ast.get_docstring(fn) or "")

    def test_t8_keep_and_fresh_fixture_interface_remain_narrow(self):
        tree = ast.parse(source("model_hub/management/commands/gt_roundtrip_test.py"))
        functions = {
            n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
        }
        arguments = ast.unparse(functions["add_arguments"])
        self.assertNotIn("--user", arguments)
        self.assertNotIn("--organization", arguments)
        handle = ast.unparse(functions["handle"])
        self.assertIn("if fixture and (not keep):", handle)
        self.assertIn("fixture = self._build_fixture()", handle)
        self.assertIn("create_user", ast.unparse(functions["_build_fixture"]))

    def test_t7_tenant_offboarding_never_uses_erase_authority(self):
        self.assertNotIn("authorized_scope", source("accounts/services/offboarding.py"))


if __name__ == "__main__":
    unittest.main()
