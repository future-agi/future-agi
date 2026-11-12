"""Offline lock protocol checks; PostgreSQL scheduling is DB-REQUIRED."""

import ast
import unittest
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import blake2b
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

SOURCE = Path(__file__).resolve().parents[1] / "services" / "hard_erase_lifecycle.py"


class Refused(Exception):
    pass


class LifecycleTests(unittest.TestCase):
    def namespace(self):
        class Imports(ast.NodeTransformer):
            def visit_ImportFrom(self, node):
                if node.module.startswith(("accounts.", "django.")):
                    return ast.Pass()
                return node

        tree = ast.fix_missing_locations(Imports().visit(ast.parse(SOURCE.read_text())))
        connection = MagicMock(vendor="postgresql", in_atomic_block=True)
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("read committed",)

        @contextmanager
        def atomic(**kwargs):
            yield

        def refuse(message):
            raise Refused(message)

        ns = {
            "contextmanager": contextmanager,
            "ContextVar": ContextVar,
            "blake2b": blake2b,
            "UUID": UUID,
            "_refuse": refuse,
            "connections": {"default": connection},
            "transaction": SimpleNamespace(atomic=atomic),
        }
        exec(compile(tree, str(SOURCE), "exec"), ns)
        return ns, connection, cursor

    def test_keys_are_canonical_domain_separated_and_ordered(self):
        ns, _, cursor = self.namespace()
        a, b = UUID(int=1), UUID(int=2)
        keys = ns["identity_keys"](
            organizations=[str(b), a, str(a).upper()], people=[a]
        )
        self.assertEqual(len(keys), 3)
        with ns["lifecycle_lock"](keys=sorted(keys, reverse=True)):
            ns["require_held"]("default", keys)
        self.assertEqual(
            [call.args[1][0] for call in cursor.execute.call_args_list[1:]],
            [ns["advisory_number"](key) for key in sorted(keys)],
        )
        self.assertNotEqual(
            ns["advisory_number"](("person", str(a))),
            ns["advisory_number"](("organization", str(a))),
        )
        self.assertIsNone(ns["_ACTIVE"].get())

    def test_nested_scope_is_reuse_only_and_connection_bound(self):
        ns, conn, cursor = self.namespace()
        keys = ns["identity_keys"](people=[UUID(int=1)])
        other = ns["identity_keys"](people=[UUID(int=2)])
        with ns["lifecycle_lock"](keys=keys):
            with ns["lifecycle_lock"](keys=keys):
                pass
            with self.assertRaises(Refused):
                with ns["lifecycle_lock"](keys=other):
                    self.fail("expanded held scope")
            conn.in_atomic_block = False
            with self.assertRaises(Refused):
                ns["require_held"]("default", keys)
        self.assertEqual(cursor.execute.call_count, 2)

    def test_unrelated_keys_share_no_advisory_resource(self):
        ns, _, _ = self.namespace()
        left = ns["identity_keys"](organizations=[UUID(int=1)], people=[UUID(int=2)])
        right = ns["identity_keys"](organizations=[UUID(int=3)], people=[UUID(int=4)])
        self.assertTrue(
            {ns["advisory_number"](key) for key in left}.isdisjoint(
                ns["advisory_number"](key) for key in right
            )
        )
        text = SOURCE.read_text()
        self.assertNotIn("LOCK_RESOURCE", text)
        self.assertNotIn("pg_advisory_xact_lock(%s, %s)", text)

    def test_unqualified_isolation_refuses_before_advisory_acquisition(self):
        ns, _, cursor = self.namespace()
        cursor.fetchone.return_value = ("repeatable read",)
        with self.assertRaises(Refused):
            with ns["lifecycle_lock"](keys=ns["identity_keys"](people=[UUID(int=1)])):
                self.fail("yielded")
        cursor.execute.assert_called_once_with("SHOW transaction_isolation")


if __name__ == "__main__":
    unittest.main()
