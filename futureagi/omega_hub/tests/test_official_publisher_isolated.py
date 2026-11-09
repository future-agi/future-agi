"""Dependency-free command control-flow checks; DB/atomicity coverage lives beside this file.

Run with python3 -I <this file>. Only the handle method is executed, with explicit ORM doubles.
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


class CommandError(Exception):
    pass


class OfficialPublisherCommand(unittest.TestCase):
    def setUp(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "management"
            / "commands"
            / "omega_hub_ensure_official_publisher.py"
        )
        tree = ast.parse(path.read_text(), filename=str(path))
        command = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Command"
        )
        handle = next(
            node
            for node in command.body
            if isinstance(node, ast.FunctionDef) and node.name == "handle"
        )
        # PostgreSQL qualification verifies the real atomic wrapper, locking and rollback.
        handle.decorator_list = []
        # Organization is a plain Django model, not BaseModel: no soft-deletion field exists.
        self.owner = SimpleNamespace(
            pk="person-id", is_staff=True, is_active=True, organization_id="org-id"
        )
        self.users = Mock()
        self.users.select_for_update.return_value.filter.return_value.first.return_value = self.owner
        self.publisher = SimpleNamespace(
            pk="publisher-id",
            kind="organization",
            tier="official",
            owner_org_id="org-id",
            owner_user_id=None,
            deleted=False,
            is_erased=False,
            is_suspended=False,
            save=Mock(),
        )
        self.publishers = Mock()
        self.publishers.select_for_update.return_value.get_or_create.return_value = (
            self.publisher,
            True,
        )
        self.audit = Mock()
        namespace = {
            "get_user_model": lambda: SimpleNamespace(objects=self.users),
            "CommandError": CommandError,
            "conf": SimpleNamespace(maintainer_org_id=lambda: None),
            "Publisher": SimpleNamespace(
                all_objects=self.publishers,
                Kind=SimpleNamespace(ORGANIZATION="organization"),
                Tier=SimpleNamespace(OFFICIAL="official"),
            ),
            "audit": self.audit,
        }
        exec(
            compile(ast.Module(body=[handle], type_ignores=[]), str(path), "exec"),
            namespace,
        )
        self.handle = namespace["handle"]
        self.command = SimpleNamespace(stdout=SimpleNamespace(write=Mock()))

    def test_staff_org_bootstrap_uses_only_real_model_attributes_and_is_audited(self):
        self.handle(self.command, owner_email="staff@example.test")
        create = self.publishers.select_for_update.return_value.get_or_create
        self.assertEqual(create.call_args.kwargs["handle"], "future-agi")
        self.assertEqual(create.call_args.kwargs["defaults"]["owner_org_id"], "org-id")
        self.assertEqual(
            self.audit.record.call_args.args[0], "publisher.official_ensured"
        )
        self.assertIs(self.audit.record.call_args.kwargs["actor"], self.owner)
        self.users.create_user.assert_not_called()
        self.publisher.save.assert_not_called()

    def test_nonstaff_is_refused_before_any_publisher_mutation(self):
        self.owner.is_staff = False
        with self.assertRaises(CommandError):
            self.handle(self.command, owner_email="staff@example.test")
        self.publishers.select_for_update.assert_not_called()
        self.audit.record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
