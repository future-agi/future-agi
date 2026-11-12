"""No-DB checks for the webhook pair composed after the existing prism 0074.

Run this file with python3 -I -B. These are structural regression tests, not
PostgreSQL, full Django migration-loader, deployment-fence or backup proof.
"""

import ast
from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[1]
MIGRATIONS = APP / "migrations"
PREDECESSOR = "0074_api_key_provisioning_state"
CHAIN = (
    "0075_webhook_credential_columns",
    "0076_encrypt_webhook_credentials",
    "0077_remove_webhook_plaintext",
)


def prism_dependencies(path):
    tree = ast.parse(path.read_text())
    migration = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Migration"
    )
    assignment = next(
        node for node in migration.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "dependencies"
                for target in node.targets)
    )
    return [
        entry.elts[1].value
        for entry in assignment.value.elts
        if isinstance(entry, (ast.Tuple, ast.List)) and len(entry.elts) == 2
        and isinstance(entry.elts[0], ast.Constant)
        and entry.elts[0].value == "prism"
        and isinstance(entry.elts[1], ast.Constant)
    ]


class WebhookComposedMigrationChain(unittest.TestCase):
    def test_existing_provisioning_dependency_is_preserved(self):
        self.assertEqual(
            prism_dependencies(MIGRATIONS / f"{PREDECESSOR}.py"),
            ["0073_merge_708j_round3"],
        )

    def test_webhook_migrations_follow_the_existing_provisioning_leaf(self):
        predecessor = PREDECESSOR
        for name in CHAIN:
            path = MIGRATIONS / f"{name}.py"
            self.assertTrue(path.is_file(), f"Missing composed migration {name}")
            self.assertEqual(prism_dependencies(path), [predecessor])
            predecessor = name

    def test_prism_has_one_composed_leaf_without_post_0073_number_collisions(self):
        # The id is kept for pinned runlists. Distinct migrations may share a
        # numeric prefix (the joined 0075 branches); a migration NAME may not repeat.
        paths = sorted(MIGRATIONS.glob("[0-9]*.py"))
        names = [path.stem for path in paths]
        self.assertEqual(len(names), len(set(names)), "Duplicate prism migration names")
        dependencies = {path.stem: prism_dependencies(path) for path in paths}
        referenced = {dep for deps in dependencies.values() for dep in deps}
        leaves = set(names) - referenced
        self.assertEqual(len(leaves), 1, f"Expected one prism leaf, found {sorted(leaves)}")
        self.assertEqual(leaves, {"0086_merge_dispatch_claim_webhook_at_rest"})
        ancestry, pending = set(), list(leaves)
        while pending:
            for dep in dependencies.get(pending.pop(), []):
                if dep not in ancestry:
                    ancestry.add(dep)
                    pending.append(dep)
        # Both branches must reach the single leaf: dispatch claim and webhook at rest.
        self.assertIn("0085_dispatch_claim", ancestry)
        self.assertIn(CHAIN[-1], ancestry)

    def test_migration_and_application_pair_are_both_present(self):
        required = (
            "migrations/_webhook_credentials_v1.py",
            "services/webhook_credential_storage.py",
            "services/webhook_credential_access.py",
            "services/webhook_credential_updates.py",
            "tests/test_webhook_migration_postgres.py",
            "tests/test_webhook_old_worker_timing.py",
            "tests/test_webhook_encrypted_api.py",
            "tests/test_webhook_encrypted_delivery.py",
            "tests/test_webhook_encrypted_admin.py",
            "tests/test_webhook_encrypted_concurrency.py",
            "tests/test_webhook_event_admin.py",
        )
        missing = [name for name in required if not (APP / name).is_file()]
        self.assertEqual(missing, [], "The migration must not ship without the paired application")


if __name__ == "__main__":
    unittest.main()
