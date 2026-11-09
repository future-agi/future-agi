"""Offline backfill dispatch and grant-identity assertion regressions.

The SQL guard, shape matcher and lifecycle checks are real source bodies. Only
framework imports, transactions and the DB driver are stand-ins. PostgreSQL
locking and persistence still require test_hard_erase_r2_db in the E2B venue.
"""

import ast
import runpy
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

TESTS = Path(__file__).resolve().parent
SERVICES = TESTS.parent / "services"
SUPPORT = runpy.run_path(str(TESTS / "test_hard_erase_sql_flow.py"))
Refused = SUPPORT["Refused"]
ROW = tuple(uuid.UUID(int=value) for value in (1, 2, 3))


def load_boundary(name, namespace):
    """Replace imports only, not application decisions or function bodies."""
    path = SERVICES / name

    class Imports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            if node.module and node.module.startswith(("accounts.", "django.", "tfc.")):
                return ast.Pass()
            return node

    tree = ast.fix_missing_locations(Imports().visit(ast.parse(path.read_text())))
    namespace["__file__"] = str(path)
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


class BackfillHarness:
    def __init__(self, rows=(ROW,), rechecked=None):
        self.connection = SimpleNamespace(
            in_atomic_block=False, vendor="postgresql", alias="default"
        )
        self.authority = ContextVar("backfill_test_authority", default=None)
        lex = runpy.run_path(str(SERVICES / "hard_erase_sql_lex.py"))["_lex"]
        policy = load_boundary("hard_erase_sql_policy.py", {"_lex": lex})

        def refuse(message):
            raise Refused(message)

        self.guard = {
            "_refuse": refuse, "_SQL_AUTH": self.authority,
            "connections": {"default": self.connection},
            "_TABLES": {"accounts_user", "accounts_workspacemembership"},
            "_lex": lex, "protected_mutations": policy["protected_mutations"],
            "exact_sql_exception": runpy.run_path(
                str(SERVICES / "hard_erase_sql_exceptions.py")
            )["exact_sql_exception"],
            "migration_sql_allowed": runpy.run_path(
                str(SERVICES / "hard_erase_migrations.py")
            )["migration_sql_allowed"],
            "contextmanager": contextmanager,
        }
        exec(SUPPORT["load"]("hard_erase_sql.py"), self.guard)
        self.guard["_orm_write"] = lambda connection: False
        self.rows = list(rows)
        self.rechecked = self.rows if rechecked is None else list(rechecked)
        self.calls = []
        self.before_execute = lambda sql: None

        @contextmanager
        def atomic(using):
            assert using == "default"
            previous = self.connection.in_atomic_block
            self.connection.in_atomic_block = True
            try:
                yield
            finally:
                self.connection.in_atomic_block = previous

        framework = {
            "connections": {"default": self.connection},
            "transaction": SimpleNamespace(atomic=atomic),
            "_refuse": self.guard["_refuse"],
        }
        self.lifecycle = load_boundary("hard_erase_lifecycle.py", dict(framework))
        self.backfill = load_boundary(
            "hard_erase_backfill.py",
            dict(
                framework,
                _lex=self.guard["_lex"],
                MAX_NODES=2,
                force_primary=nullcontext,
                **{name: self.lifecycle[name] for name in
                   ("identity_keys", "lifecycle_lock", "require_held")},
            ),
        )
        for name in ("membership_backfill_kind", "execute_membership_backfill"):
            self.guard[name] = self.backfill[name]
        self.connection.cursor = lambda: self.Cursor(self)
        self.cursor = self.connection.cursor()

    class Cursor:
        def __init__(self, harness):
            self.harness = harness
            self.db = harness.connection
            self.result = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            return self.harness.guard["guard_execute"](
                self.downstream, sql, params, False,
                {"connection": self.db, "cursor": self},
            )

        def downstream(self, sql, params, many, context):
            h = self.harness
            assert context["cursor"] is self and context["connection"] is self.db
            assert not many
            assert h.authority.get() is None
            h.before_execute(sql)
            h.calls.append((sql, params, self))
            if sql.startswith("SELECT wm.id"):
                if "FOR UPDATE" in sql:
                    assert self.db.in_atomic_block
                    h.lifecycle["require_held"](
                        "default", h.lifecycle["identity_keys"](
                            people=[row[1] for row in h.rows],
                            organizations=[row[2] for row in h.rows],
                        ),
                    )
                    self.result = h.rechecked
                else:
                    self.result = h.rows
            elif sql.startswith("SHOW"):
                self.result = [("read committed",)]
            elif sql.lstrip().startswith("UPDATE"):
                assert self.db.in_atomic_block
                assert h.lifecycle["_ACTIVE"].get() is not None
                return "updated"
            else:
                self.result = []

        def fetchall(self):
            return self.result

        def fetchone(self):
            return self.result[0]

    def run(self, batched=True):
        sql = self.backfill["_BATCHED" if batched else "_INITIAL"]
        params = [[str(row[0]) for row in self.rows]] if batched else []
        return self.cursor.execute(sql, params)

    def updates(self):
        return [call for call in self.calls if call[0].lstrip().startswith("UPDATE")]


class BackfillBoundaryTests(unittest.TestCase):
    def test_both_shapes_lock_and_update_on_the_qualified_cursor(self):
        for batched in (False, True):
            with self.subTest(batched=batched):
                h = BackfillHarness()
                self.assertEqual(h.run(batched), "updated")
                locking = [call for call in h.calls if "FOR UPDATE" in call[0]]
                self.assertEqual(len(locking), 1)
                self.assertIs(locking[0][2], h.cursor)
                self.assertEqual(locking[0][1][-1], [ROW[0]])
                self.assertEqual(h.updates()[0][1][-1], [ROW[0]])
                self.assertTrue(h.updates()[0][0].endswith(" AND wm.id = ANY(%s::uuid[])"))
                self.assertFalse(h.connection.in_atomic_block)
                self.assertIsNone(h.lifecycle["_ACTIVE"].get())
                self.assertIsNone(h.authority.get())

    def test_empty_selection_stays_bounded(self):
        h = BackfillHarness(rows=[])
        self.assertEqual(h.run(), "updated")
        self.assertEqual(h.updates()[0][1][-1], [])

    def test_too_many_selected_rows_refuses_before_locks_or_update(self):
        h = BackfillHarness(rows=[ROW] * 3)
        with self.assertRaisesRegex(Refused, "bounded selection"):
            h.run()
        self.assertEqual(h.updates(), [])
        self.assertEqual(len(h.calls), 1)
        self.assertFalse(h.connection.in_atomic_block)

    def test_changed_lifecycle_identity_refuses_before_update(self):
        h = BackfillHarness(rechecked=[(ROW[0], uuid.UUID(int=9), ROW[2])])
        with self.assertRaisesRegex(Refused, "lifecycle key set changed"):
            h.run()
        self.assertEqual(h.updates(), [])
        self.assertIsNone(h.lifecycle["_ACTIVE"].get())
        self.assertFalse(h.connection.in_atomic_block)

    def test_removed_selection_does_not_expand_frozen_ids(self):
        h = BackfillHarness(rechecked=[])
        self.assertEqual(h.run(), "updated")
        self.assertEqual(h.updates()[0][1][-1], [ROW[0]])

    def test_unknown_shape_many_wrong_connection_and_param_count_refuse(self):
        for change in ("shape", "many", "connection", "params"):
            with self.subTest(change=change):
                h = BackfillHarness()
                sql, params, many = h.backfill["_BATCHED"], [[str(ROW[0])]], False
                context = {"connection": h.connection, "cursor": h.cursor}
                if change == "shape":
                    sql = sql.replace("om.user_id = wm.user_id", "om.user_id <> wm.user_id")
                elif change == "many":
                    many = True
                elif change == "connection":
                    context["connection"] = SimpleNamespace(in_atomic_block=True)
                else:
                    params = []
                driver = Mock()
                with self.assertRaises(Refused):
                    h.guard["guard_execute"](driver, sql, params, many, context)
                driver.assert_not_called()
                self.assertEqual(h.calls, [])

    def test_foreign_cursor_refuses_before_selection(self):
        h = BackfillHarness()
        foreign = SimpleNamespace(db=SimpleNamespace(in_atomic_block=True))
        driver = Mock()
        with self.assertRaises(Refused):
            h.guard["guard_execute"](
                driver, h.backfill["_INITIAL"], [], False,
                {"connection": h.connection, "cursor": foreign},
            )
        driver.assert_not_called()
        self.assertEqual(h.calls, [])

    def test_callback_cannot_borrow_locking_read_or_dml_authority(self):
        h = BackfillHarness()
        denied = []

        def callback(sql):
            if "FOR UPDATE" not in sql:
                return
            for raw in (sql, "DELETE FROM accounts_user", "UPDATE accounts_user SET id=1"):
                with self.assertRaises(Refused):
                    h.connection.cursor().execute(raw, [[ROW[0]]])
                denied.append(raw)

        h.before_execute = callback
        self.assertEqual(h.run(), "updated")
        self.assertEqual(len(denied), 3)
        with self.assertRaises(Refused):
            h.cursor.execute("SELECT * FROM accounts_workspacemembership FOR UPDATE")

    def test_driver_failure_cleans_up_without_granting_authority(self):
        h = BackfillHarness()

        def fail(sql):
            if "FOR UPDATE" in sql:
                raise RuntimeError("synthetic locked-read failure")

        h.before_execute = fail
        with self.assertRaisesRegex(RuntimeError, "synthetic locked-read failure"):
            h.run()
        self.assertEqual(h.updates(), [])
        self.assertIsNone(h.lifecycle["_ACTIVE"].get())
        self.assertIsNone(h.authority.get())
        self.assertFalse(h.connection.in_atomic_block)
        with self.assertRaises(Refused):
            h.cursor.execute("DELETE FROM accounts_user")


class GrantIdentityAssertionTests(unittest.TestCase):
    def exercise(self, claim_user, persisted_user=None):
        """Run the unchanged DB test method with UUID/text driver stand-ins."""
        path = TESTS / "test_hard_erase_r2_db.py"
        cls = next(node for node in ast.parse(path.read_text()).body
                   if isinstance(node, ast.ClassDef) and node.name == "EveryRootDBTests")
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                      and node.name == "test_unrelated_tenant_issuance_does_not_wait_for_erasure")
        user, org, grant_pk = uuid.UUID(int=2), uuid.UUID(int=3), uuid.UUID(int=4)
        held = []

        @contextmanager
        def preflight(roots, using):
            self.assertEqual(using, "default")
            held.append(roots)
            try:
                yield
            finally:
                held.pop()

        grant = SimpleNamespace(user=claim_user, org=str(org), pk=grant_pk)
        persisted = SimpleNamespace(user=user if persisted_user is None else persisted_user, org=org)
        manager = SimpleNamespace(get=Mock(return_value=persisted))
        namespace = {
            "uuid": uuid, "ThreadPoolExecutor": ThreadPoolExecutor,
            "locked_preflight": preflight,
            "OrganizationMembership": SimpleNamespace(objects=SimpleNamespace(create=Mock())),
            "ServiceGrant": SimpleNamespace(objects=manager),
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
        actor = unittest.TestCase()
        actor.u, actor.v, actor.o2 = object(), SimpleNamespace(pk=user), SimpleNamespace(pk=org)

        def issue(person):
            self.assertTrue(held, "issuance must finish inside the held erase boundary")
            self.assertIs(person, actor.v)
            return "synthetic", {}, grant

        actor.issue, actor.connected = issue, lambda fn: fn()
        namespace[method.name](actor)
        self.assertFalse(held)

    def test_text_claim_and_uuid_model_identity_are_equal(self):
        self.exercise(str(uuid.UUID(int=2)))

    def test_uuid_model_identity_is_also_accepted(self):
        self.exercise(uuid.UUID(int=2))

    def test_different_claim_identity_is_still_rejected(self):
        with self.assertRaises(AssertionError):
            self.exercise(str(uuid.UUID(int=99)))

    def test_different_persisted_identity_is_still_rejected(self):
        with self.assertRaises(AssertionError):
            self.exercise(uuid.UUID(int=2), persisted_user=uuid.UUID(int=99))


if __name__ == "__main__":
    unittest.main()
