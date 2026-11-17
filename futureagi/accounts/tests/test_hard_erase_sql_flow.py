"""Offline execution of SQL-boundary decisions with framework-free stand-ins."""

import ast
import runpy
import unittest
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1] / "services"


class Refused(Exception):
    pass


def load(name):
    tree = ast.parse((ROOT / name).read_text())

    # Replace local app imports, not logic: these functions run without Django.
    class Imports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            if node.module and node.module.startswith(("accounts.", "django.")):
                return ast.Pass()
            return node

    tree = Imports().visit(tree)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    return compile(
        ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[])),
        name,
        "exec",
    )


class SQLBoundaryTests(unittest.TestCase):
    def namespace(self):
        connection = SimpleNamespace(in_atomic_block=True, alias="default")
        auth = ContextVar("test_auth", default=None)

        def refuse(message):
            raise Refused(message)

        ns = {
            "contextmanager": contextmanager,
            "_refuse": refuse,
            "connections": {"default": connection},
            "_SQL_AUTH": auth,
            "membership_backfill_kind": lambda sql: None,
            "exact_sql_exception": runpy.run_path(
                str(ROOT / "hard_erase_sql_exceptions.py")
            )["exact_sql_exception"],
            "_TABLES": {
                "accounts_user": {"id", "organization_id", "invited_by_id"},
                "accounts_service_grant": {"jti", "org", "user", "workspace"},
                "agentcc_api_key": {"id", "user_id", "organization_id"},
                "accounts_workspacemembership": {"id", "organization_membership_id"},
            },
            "_lex": runpy.run_path(str(ROOT / "hard_erase_sql_policy.py"))["_lex"],
            "protected_mutations": runpy.run_path(
                str(ROOT / "hard_erase_sql_policy.py")
            )["protected_mutations"],
        }
        maintenance = ROOT / "hard_erase_migrations.py"
        if maintenance.exists():
            boundary = runpy.run_path(str(maintenance))
            for name in ("install_migration_guard", "migration_sql_allowed"):
                ns[name] = boundary[name]
        exec(load("hard_erase_sql.py"), ns)
        ns["_orm_write"] = lambda connection: False
        return ns, connection, auth

    def test_raw_mutations_fail_before_driver_for_execute_and_executemany(self):
        ns, conn, _ = self.namespace()
        for sql in (
            "DELETE FROM accounts_user",
            'DELETE FROM U&"accounts\\005fuser"',
            "INSERT INTO accounts_user(id) VALUES (1) ON CONFLICT(id) DO UPDATE SET organization_id=NULL",
            "MERGE INTO accounts_user t USING others s ON t.id=s.id WHEN MATCHED THEN DELETE",
            "UPDATE accounts_user SET id=1",
            "TRUNCATE accounts_user",
            "WITH a AS (SELECT 1) DELETE FROM accounts_user",
            'UPDATE public."accounts_user" AS u SET organization_id=NULL',
            'UPDATE ONLY ("accounts_user") u SET organization_id=NULL',
            "UPDATE accounts_user SET (organization_id, is_active) = (NULL, false)",
            "TRUNCATE other_table, ONLY public.accounts_user RESTART IDENTITY",
            "WITH d AS (DELETE FROM accounts_user RETURNING id) SELECT * FROM d",
            "DO $$ BEGIN DELETE FROM accounts_user; END $$;",
            "SELECT 1; /* comment */ DELETE FROM accounts_user",
            "UPDATE accounts_workspacemembership wm SET organization_membership_id=(SELECT 1)",
        ):
            for many in (False, True):
                execute = Mock()
                with self.assertRaises(Refused):
                    ns["guard_execute"](execute, sql, [], many, {"connection": conn})
                execute.assert_not_called()

    def test_maintenance_and_non_mutating_mentions_are_unaffected(self):
        ns, conn, auth = self.namespace()
        # Independent nonce connections deliberately have no enclosing atomic block.
        conn.in_atomic_block = False
        self.assertIsNone(auth.get())
        for sql in (
            "DELETE FROM accounts_envelope_nonce WHERE exp < %s",
            "INSERT INTO accounts_envelope_nonce (aud, id, exp) VALUES (%s,%s,%s)",
            "SELECT * FROM accounts_user",
            "SELECT 'DELETE FROM accounts_user'",
            'SELECT "delete" FROM accounts_user',
            "/* DELETE FROM accounts_user */ SELECT 1",
            "SELECT jti, org FROM accounts_service_grant",
        ):
            with self.subTest(sql=sql):
                execute = Mock(return_value="ok")
                self.assertEqual(
                    ns["guard_execute"](execute, sql, [], False, {"connection": conn}),
                    "ok",
                )
                execute.assert_called_once()

    def test_permit_is_transaction_and_connection_bound(self):
        ns, conn, auth = self.namespace()
        execute = Mock(return_value="ok")
        sql = "DELETE FROM accounts_user"
        auth.set((conn, "write"))
        with self.assertRaises(Refused):
            ns["guard_execute"](execute, sql, [], False, {"connection": conn})
        auth.set((conn, "erase"))
        self.assertEqual(
            ns["guard_execute"](execute, sql, [], False, {"connection": conn}), "ok"
        )
        conn.in_atomic_block = False
        with self.assertRaises(Refused):
            ns["guard_execute"](execute, sql, [], False, {"connection": conn})
        with self.assertRaises(Refused):
            ns["guard_execute"](
                execute,
                sql,
                [],
                False,
                {"connection": SimpleNamespace(in_atomic_block=True)},
            )

    def test_raw_delete_never_calls_driver_without_checked_boundary(self):
        ns, conn, auth = self.namespace()
        raw = Mock(return_value=1)
        ns["_original_raw_delete"] = raw
        with self.assertRaises(Refused):
            ns["guarded_raw_delete"](object(), "default")
        raw.assert_not_called()
        auth.set((conn, "erase"))
        self.assertEqual(ns["guarded_raw_delete"](object(), "default"), 1)

    def test_sealing_requires_typed_atomic_boundary(self):
        ns, conn, auth = self.namespace()
        sql = "UPDATE agentcc_api_key SET secret_key='', secret_key_hash=%s WHERE id=%s"
        driver = Mock(return_value="sealed")
        with self.assertRaises(Refused):
            ns["guard_execute"](driver, sql, [], False, {"connection": conn})
        auth.set((conn, "seal"))
        self.assertEqual(
            ns["guard_execute"](driver, sql, [], False, {"connection": conn}), "sealed"
        )
        conn.in_atomic_block = False
        with self.assertRaises(Refused):
            ns["guard_execute"](driver, sql, [], False, {"connection": conn})


if __name__ == "__main__":
    unittest.main()
