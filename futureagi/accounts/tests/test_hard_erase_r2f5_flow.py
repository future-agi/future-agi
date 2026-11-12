"""Regression: actual policy/planners/CursorWrapper, offline doubles."""

import ast
import functools
import runpy
import unittest
import warnings
from contextlib import ContextDecorator, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import UUID

TESTS = Path(__file__).resolve().parent
SERVICES = TESTS.parent / "services"
SQL = runpy.run_path(str(TESTS / "test_hard_erase_sql_flow.py"))
META = runpy.run_path(str(TESTS / "test_hard_erase_r2f_flow.py"))


def cursor_namespace():
    # Locate installed framework source without importing Django/settings.
    import importlib.util

    spec = importlib.util.find_spec("django")
    path = Path(next(iter(spec.submodule_search_locations))) / "db/backends/utils.py"
    node = next(
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == "CursorWrapper"
    )
    framework = {
        "functools": functools,
        "apps": SimpleNamespace(ready=True),
        "warnings": warnings,
        "NotSupportedError": RuntimeError,
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), framework
    )
    ns, conn, auth = SQL["SQLBoundaryTests"]().namespace()
    conn.in_atomic_block = False
    conn.execute_wrappers = []
    conn.validate_no_broken_transaction = lambda: None

    class Errors(nullcontext, ContextDecorator):
        pass

    conn.wrap_database_errors = Errors()
    cursor = framework["CursorWrapper"]
    ns.update(
        {
            "_installed": False,
            "wraps": functools.wraps,
            "BaseDatabaseWrapper": type("BaseDatabaseWrapper", (), {}),
            "apps": SimpleNamespace(get_models=lambda **kw: []),
            "protected_models": lambda models: [],
            "connections": SimpleNamespace(all=lambda **kw: [conn]),
            "models": SimpleNamespace(QuerySet=type("QuerySet", (), {})),
            "install_migration_guard": lambda executor: None,
            "MigrationExecutor": object,
            "CursorWrapper": cursor,
        }
    )
    helper = SERVICES / "hard_erase_cursor.py"
    if helper.exists():
        ns.update(runpy.run_path(str(helper)))
    ns["install_sql_guard"]()
    ns["_TABLES"] = {"accounts_user"}
    driver = MagicMock()
    return ns, conn, auth, cursor(driver, conn), driver


class CredentialStorageTests(unittest.TestCase):
    def test_manually_encrypted_real_models_are_protected(self):
        policy = runpy.run_path(str(SERVICES / "hard_erase_sql_policy.py"))
        for module, name, field_name, table in (
            (
                "simulate.models.hosted_harness",
                "HostedHarnessSecret",
                "encrypted_value",
                "simulate_hosted_harness_secret",
            ),
            (
                "agentcc.models.email_alert",
                "AgentccEmailAlert",
                "encrypted_config",
                "agentcc_email_alert",
            ),
        ):
            path = TESTS.parents[1] / (module.replace(".", "/") + ".py")
            cls = next(
                n
                for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.ClassDef) and n.name == name
            )
            assignment = next(
                n
                for n in cls.body
                if isinstance(n, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == field_name for t in n.targets
                )
            )
            field = META["field"](field_name, kind=ast.unparse(assignment.value.func))
            model = META["fake_model"](module, name, [field], table)
            with self.subTest(model=name):
                self.assertIn(model, policy["protected_models"]([model]))
                ns, conn, _ = SQL["SQLBoundaryTests"]().namespace()
                ns["_TABLES"] = {
                    m._meta.db_table for m in policy["protected_models"]([model])
                }
                for many in (False, True):
                    driver = Mock()
                    with self.assertRaises(SQL["Refused"]):
                        ns["guard_execute"](
                            driver,
                            f"DELETE FROM {table}",
                            [],
                            many,
                            {"connection": conn},
                        )
                    driver.assert_not_called()

    def test_encrypt_in_name_or_column_overrides_field_exclusion(self):
        policy = runpy.run_path(str(SERVICES / "hard_erase_sql_policy.py"))
        for name, column in (
            ("encrypted_value", None),
            ("blob", "encrypted_payload"),
            ("session_id", "encrypt_blob"),
        ):
            field = META["field"](name, kind="BinaryField")
            field.db_column = column
            model = META["fake_model"](
                "new.models", "Plain", [field], "agentcc_session"
            )
            with self.subTest(name=name, column=column):
                self.assertIn(model, policy["protected_models"]([model]))


class EffectiveStatementTests(unittest.TestCase):
    def test_unprotected_wrapped_upserts_work_protected_ones_refuse(self):
        ns, conn, _ = SQL["SQLBoundaryTests"]().namespace()
        for prefix in (
            "EXPLAIN ",
            "EXPLAIN (FORMAT JSON) ",
            "PREPARE p AS ",
            "PREPARE p(int) AS ",
        ):
            for suffix in ("DO NOTHING", "DO UPDATE SET id=excluded.id"):
                for table in ("ordinary_table", "accounts_user"):
                    text = f"{prefix}INSERT INTO {table}(id) VALUES(1) ON CONFLICT(id) {suffix}"
                    with self.subTest(text=text):
                        driver = Mock(return_value="ok")
                        if table == "accounts_user":
                            with self.assertRaises(SQL["Refused"]):
                                ns["guard_execute"](
                                    driver, text, [], False, {"connection": conn}
                                )
                            driver.assert_not_called()
                        else:
                            self.assertEqual(
                                ns["guard_execute"](
                                    driver, text, [], False, {"connection": conn}
                                ),
                                "ok",
                            )


class CursorEntryTests(unittest.TestCase):
    def test_nonexecute_driver_apis_refuse_before_driver(self):
        for method, args in (
            ("copy", ('COPY public."accounts_user" (id) FROM STDIN',)),
            ("copy_expert", ("COPY accounts_user TO STDOUT", object())),
            ("copy_from", (object(), "accounts_user")),
            ("copy_from", (object(), "public.accounts_user")),
            ("copy_to", (object(), "accounts_user")),
            ("callproc", ("unqualified_proc",)),
            ("stream", ("DELETE FROM accounts_user RETURNING id",)),
            ("send_query", ("DELETE FROM accounts_user",)),
        ):
            with self.subTest(method=method):
                _, _, _, cursor, driver = cursor_namespace()
                with self.assertRaises(SQL["Refused"]):
                    getattr(cursor, method)(*args)
                self.assertEqual(driver.mock_calls, [])

    def test_unprotected_copy_and_fetch_work_but_unparseable_copy_refuses(self):
        _, _, _, cursor, driver = cursor_namespace()
        with cursor.copy('COPY public."ordinary_table" (id) FROM STDIN') as copy:
            copy.write_row((1,))
        driver.copy.assert_called_once()
        cursor.fetchone()
        driver.fetchone.assert_called_once()
        for sql in (
            "not copy",
            object(),
            "COPY (SELECT 1) TO STDOUT",
            "COPY ordinary_table FROM STDIN; DELETE FROM accounts_user",
        ):
            driver.reset_mock()
            with self.subTest(sql=str(sql)), self.assertRaises(SQL["Refused"]):
                cursor.copy(sql)
            driver.copy.assert_not_called()

    def test_copy_requires_connection_bound_atomic_erase_or_migration(self):
        ns, conn, auth, cursor, driver = cursor_namespace()
        auth.set((conn, "erase"))
        with self.assertRaises(SQL["Refused"]):
            cursor.copy("COPY accounts_user FROM STDIN")
        conn.in_atomic_block = True
        cursor.copy("COPY accounts_user FROM STDIN")
        driver.copy.assert_called_once()
        auth.set((object(), "erase"))
        with self.assertRaises(SQL["Refused"]):
            cursor.copy("COPY accounts_user FROM STDIN")
        auth.set(None)
        conn.in_atomic_block = False
        ns["migration_sql_allowed"] = lambda connection: connection is conn
        cursor.copy("COPY accounts_user FROM STDIN")
        self.assertEqual(driver.copy.call_count, 2)


class MissingTargetTests(unittest.TestCase):
    def entry(self, live):
        life = runpy.run_path(str(TESTS / "test_hard_erase_lifecycle_flow.py"))[
            "LifecycleTests"
        ]().namespace()[0]
        ns = runpy.run_path(str(TESTS / "hard_erase_entry_support.py"))[
            "entry_namespace"
        ](life, live=live)
        ns["_target_exists"] = lambda value, using: value in live
        return life, ns

    def test_role_missing_target_preserves_404_and_actor_org_refuse(self):
        org, actor, target = (UUID(int=n) for n in (1, 2, 3))
        for live in ({org, actor}, {org}, {actor}):
            life, ns = self.entry(live)
            request = SimpleNamespace(
                user=SimpleNamespace(pk=actor),
                organization=SimpleNamespace(pk=org),
                validated_data={"user_id": target},
            )
            view = SimpleNamespace(
                _gm=SimpleNamespace(not_found=lambda message: (404, message))
            )
            keys = life["identity_keys"](organizations=[org], people=[actor, target])

            def missing_user(view, request, life=life, keys=keys):
                life["require_held"]("default", keys)
                return view._gm.not_found("User not found in organization")

            method = Mock(side_effect=missing_user)
            wrapped = ns["request_issuance_entry"](method)
            with self.subTest(live=live):
                if org in live and actor in live:
                    self.assertEqual(
                        wrapped(view, request), (404, "User not found in organization")
                    )
                    method.assert_called_once_with(view, request)
                else:
                    with self.assertRaises(life["_refuse"].__globals__["Refused"]):
                        wrapped(view, request)
                    method.assert_not_called()
                self.assertIsNone(life["_ACTIVE"].get())

    def test_tool_missing_target_preserves_not_found_under_complete_lock(self):
        org, actor, target = (UUID(int=n) for n in (1, 2, 3))
        life, ns = self.entry({org, actor})
        keys = life["identity_keys"](organizations=[org], people=[actor, target])

        def not_found(kind, value):
            life["require_held"]("default", keys)
            return ("NOT_FOUND", kind, value)

        # The body owns target translation; the entry wrapper owns locking.
        # r2f6 additionally executes the real body to verify check precedence.
        method = Mock(side_effect=lambda *args: not_found("User", str(target)))
        context = SimpleNamespace(
            user=SimpleNamespace(pk=actor), organization=SimpleNamespace(pk=org)
        )
        params = SimpleNamespace(user_id=target)
        self.assertEqual(
            ns["tool_issuance_entry"](method)(object(), params, context),
            ("NOT_FOUND", "User", str(target)),
        )
        method.assert_called_once()
        self.assertIsNone(life["_ACTIVE"].get())


if __name__ == "__main__":
    unittest.main()
