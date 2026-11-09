"""Offline direct-dispatch and explicit issuance checks, with no Django imports."""

import ast
import runpy
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

TESTS = Path(__file__).resolve().parent
SERVICES = TESTS.parent / "services"
SUPPORT = runpy.run_path(str(TESTS / "test_hard_erase_sql_flow.py"))


class DirectDispatchTests(unittest.TestCase):
    def namespace(self):
        ns, conn, auth = SUPPORT["SQLBoundaryTests"]().namespace()
        exec(SUPPORT["load"]("hard_erase_sql.py"), ns)
        ns.update(sys=sys, __name__="guard_module")
        insert_query, update_query = (
            type("InsertQuery", (), {}),
            type("UpdateQuery", (), {}),
        )
        ns.update(
            InsertQuery=insert_query,
            UpdateQuery=update_query,
            Query=type("Query", (), {"select_for_update": False}),
        )
        driver = Mock(return_value="ok")

        class SQLCompiler:
            def execute_sql(self, text):
                return ns["guard_execute"](
                    driver, text, [], False, {"connection": self.connection}
                )

        class SQLInsertCompiler(SQLCompiler):
            pass

        ns["compiler"] = SimpleNamespace(
            SQLCompiler=SQLCompiler, SQLInsertCompiler=SQLInsertCompiler
        )
        instance = SQLCompiler()
        instance.connection = conn
        return ns, conn, auth, driver, instance

    def test_ordinary_joined_update_has_no_snapshot_or_lifecycle_work(self):
        ns, conn, auth, driver, instance = self.namespace()
        conn.in_atomic_block = False
        query = ns["UpdateQuery"]()
        query.alias_map = {
            "accounts_workspacemembership": object(),
            "accounts_workspace": object(),
        }
        query.where = object()
        instance.query = query
        ns["lifecycle_lock"] = Mock(
            side_effect=AssertionError("ordinary writes must not lock")
        )
        ns["row_keys"] = Mock(
            side_effect=AssertionError("ordinary writes must not inspect bindings")
        )
        for _active in (False, True):
            self.assertEqual(
                instance.execute_sql(
                    'UPDATE "accounts_workspacemembership" SET is_active=%s'
                ),
                "ok",
            )
            self.assertIs(instance.query, query)
            self.assertIn("accounts_workspace", query.alias_map)
        self.assertIsNone(auth.get())
        ns["lifecycle_lock"].assert_not_called()
        ns["row_keys"].assert_not_called()
        self.assertEqual(driver.call_count, 2)

    def test_log_inserts_and_audit_append_do_not_consume_lifecycle_keys(self):
        ns, conn, auth, driver, instance = self.namespace()
        conn.in_atomic_block = True
        auth.set((conn, "erase"))
        for label in ("", "end-user-42"):
            query = ns["InsertQuery"]()
            query.objs = [SimpleNamespace(user_id=label)]
            instance.query = query
            self.assertEqual(
                instance.execute_sql(
                    "INSERT INTO agentcc_request_log(user_id) VALUES (%s)"
                ),
                "ok",
            )
            self.assertEqual(
                instance.execute_sql(
                    "INSERT INTO agentcc_mcp_tool_call(user_id) VALUES (%s)"
                ),
                "ok",
            )
        # Audit actor is not part of the eraser's key set; no new key is requested.
        self.assertEqual(
            instance.execute_sql(
                "INSERT INTO accounts_audit_log(actor_id) VALUES (%s)"
            ),
            "ok",
        )
        self.assertEqual(driver.call_count, 5)

    def test_orm_row_locks_work_but_raw_row_locks_refuse(self):
        ns, conn, _, driver, instance = self.namespace()
        query = ns["Query"]()
        query.select_for_update = True
        instance.query = query
        statement = "SELECT * FROM accounts_user FOR UPDATE"
        self.assertEqual(instance.execute_sql(statement), "ok")
        driver.assert_called_once()
        driver.reset_mock()
        with self.assertRaises(SUPPORT["Refused"]):
            ns["guard_execute"](driver, statement, [], False, {"connection": conn})
        instance.query = SimpleNamespace(select_for_update=True)
        with self.assertRaises(SUPPORT["Refused"]):
            instance.execute_sql(statement)
        driver.assert_not_called()

    def test_application_callback_cannot_borrow_compiler_dispatch(self):
        ns, conn, _, driver, instance = self.namespace()
        instance.query = ns["UpdateQuery"]()
        # The direct test caller is application code, not exact compiler dispatch.
        with self.assertRaises(SUPPORT["Refused"]):
            ns["guard_execute"](
                driver,
                "UPDATE accounts_user SET name='x'",
                [],
                False,
                {"connection": conn},
            )
        driver.assert_not_called()
        # A compiler query for deletion does not gain an insert/update permit.
        instance.query = object()
        with self.assertRaises(SUPPORT["Refused"]):
            instance.execute_sql("DELETE FROM accounts_user")
        driver.assert_not_called()


class IssuanceTests(unittest.TestCase):
    def namespace(self):
        ns = {}
        tree = ast.parse((SERVICES / "identity_issuance.py").read_text())

        class Imports(ast.NodeTransformer):
            def visit_ImportFrom(self, node):
                return ast.Pass()

        tree = ast.fix_missing_locations(Imports().visit(tree))
        exec(compile(tree, "identity_issuance.py", "exec"), ns)
        events = []

        @contextmanager
        def lock(using, *, keys):
            events.append(("lock", keys))
            yield
            events.append(("unlock", keys))

        def refuse(message):
            raise SUPPORT["Refused"](message)

        ns.update(
            lifecycle_lock=lock,
            identity_keys=lambda **kw: kw,
            _refuse=refuse,
            planned_person=lambda user: user,
        )
        return ns, events

    def test_new_person_insert_locks_before_rechecking_and_saving(self):
        ns, events = self.namespace()
        ns.update(Organization=object(), User=object())
        ns["_exists"] = lambda *args: events.append(("check", args[1]))
        user = SimpleNamespace(
            pk="new",
            organization_id="org",
            invited_by_id="inviter",
            save=lambda **kw: events.append(("save", kw)),
        )
        self.assertIs(ns["save_new_person"](user), user)
        self.assertEqual(
            [e[0] for e in events], ["lock", "check", "check", "save", "unlock"]
        )
        self.assertTrue(events[-2][1]["force_insert"])

    def test_membership_issuance_checks_liveness_inside_shared_key_lock(self):
        ns, events = self.namespace()
        organization_membership, workspace_membership = object(), object()
        ns.update(
            OrganizationMembership=organization_membership,
            WorkspaceMembership=workspace_membership,
            Organization=object(),
            User=object(),
            Workspace=object(),
        )
        ns["_exists"] = lambda *args: events.append(("check", args[1]))
        manager = SimpleNamespace(
            model=organization_membership,
            _db="default",
            create=lambda **kw: events.append(("insert", kw)),
        )
        manager.db_manager = lambda using: manager
        ns["issue_membership"](manager, "create", user_id="u", organization_id="o")
        self.assertEqual(events[0][0], "lock")
        self.assertEqual(events[-2][0], "insert")
        self.assertEqual(events[-1][0], "unlock")
        self.assertEqual(events[0][1]["organizations"], ["o"])
        self.assertIn("u", events[0][1]["people"])

    def test_member_defaults_cannot_change_the_locked_identity(self):
        ns, events = self.namespace()
        model = object()
        ns.update(
            OrganizationMembership=model,
            WorkspaceMembership=object(),
            Organization=object(),
            User=object(),
            Workspace=object(),
        )
        manager = SimpleNamespace(model=model, _db="default", create=Mock())
        manager.db_manager = lambda using: manager
        with self.assertRaises(SUPPORT["Refused"]):
            ns["issue_membership"](
                manager,
                "create",
                user_id="u",
                organization_id="o",
                defaults={"user_id": "other"},
            )
        manager.create.assert_not_called()
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
