"""Offline migration dispatch with unchanged Django method bodies, no DB/imports.

Django must be installed (only its executor/cursor sources are read). Driver,
schema editor and migration recorder are stand-ins, not PostgreSQL qualification.
"""

import ast
import functools
import importlib.util
import runpy
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

TESTS = Path(__file__).resolve().parent
SUPPORT = runpy.run_path(str(TESTS / "test_hard_erase_sql_flow.py"))
Refused = SUPPORT["Refused"]
HISTORY = ("0026_authtoken_scope.py", "0035_gateway_retirement_fence.py")


def framework_class(relative, name, methods, namespace):
    spec = importlib.util.find_spec("django")
    if spec is None:
        raise AssertionError("installed Django source is required for dispatch tests")
    source = Path(next(iter(spec.submodule_search_locations))) / relative
    tree = ast.parse(source.read_text())
    original = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name
    )
    selected = [
        n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in methods
    ]
    assert {n.name for n in selected} == set(methods)
    cls = ast.ClassDef(
        name=name, bases=[], keywords=[], body=selected, decorator_list=[]
    )
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
            str(source),
            "exec",
        ),
        namespace,
    )
    return namespace[name]


def historical(name):
    path = TESTS.parent / "migrations" / name
    tree = ast.parse(path.read_text())
    # Keep unchanged constants/helpers and forward/reverse functions; omit only
    # framework imports and declarative Migration class requiring a real registry.
    tree.body = [
        n
        for n in tree.body
        if not isinstance(n, (ast.Import, ast.ImportFrom, ast.ClassDef))
    ]
    namespace = {}
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


def harness():
    ns, conn, auth = SUPPORT["SQLBoundaryTests"]().namespace()
    conn.in_atomic_block = False
    ns["_TABLES"].update({"accounts_auth_token": set(), "accounts_organization": set()})
    executor_class = framework_class(
        "db/migrations/executor.py",
        "MigrationExecutor",
        {"apply_migration", "unapply_migration"},
        {},
    )

    # Install through the actual guard installer, as AccountsConfig.ready does;
    # simply possessing a schema editor or a migrate-looking name is not enough.
    class Connections(dict):
        def all(self, **kwargs):
            return list(self.values())

    conn.execute_wrappers = []
    ns.update(
        _installed=False,
        wraps=functools.wraps,
        BaseDatabaseWrapper=type("BaseDatabaseWrapper", (), {}),
        connections=Connections(default=conn),
        apps=SimpleNamespace(get_models=lambda **kw: []),
        protected_models=lambda models: [],
        models=SimpleNamespace(QuerySet=type("QuerySet", (), {})),
        MigrationExecutor=executor_class,
    )
    cursor_class = framework_class(
        "db/backends/utils.py",
        "CursorWrapper",
        {"execute", "executemany", "_execute_with_wrappers", "__getattr__", "callproc"},
        {"functools": functools},
    )
    ns["CursorWrapper"] = cursor_class
    ns.update(
        {
            name: value
            for name, value in runpy.run_path(
                str(TESTS.parent / "services/hard_erase_cursor.py")
            ).items()
            if name in {"install_cursor_guard", "copy_sql"}
        }
    )
    tables = ns["_TABLES"]
    ns["install_sql_guard"]()
    ns["_TABLES"] = tables
    assert conn.execute_wrappers == [ns["guard_execute"]]
    cursor_class.__enter__ = lambda self: self
    cursor_class.__exit__ = lambda self, *args: False
    driver = Mock(return_value="driver")
    cursor = cursor_class()
    cursor.db = conn
    cursor._execute = driver
    cursor._executemany = driver
    cursor.fetchone = lambda: None
    conn.cursor = lambda: cursor

    class Editor:
        connection = conn
        deferred_sql = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            return cursor.execute(statement)

    conn.schema_editor = lambda **kwargs: Editor()
    executor = executor_class()
    executor.connection = conn
    executor.progress_callback = None
    executor.record_migration = Mock()
    executor.recorder = Mock()
    return ns, conn, auth, executor, cursor, driver


def migration(forward, reverse, atomic=False):
    def dispatch(callback, state, editor):
        callback(None, editor)
        return state

    return SimpleNamespace(
        atomic=atomic,
        app_label="accounts",
        name="offline",
        replaces=[],
        apply=lambda state, editor: dispatch(forward, state, editor),
        unapply=lambda state, editor: dispatch(reverse, state, editor),
    )


def history_dispatch():
    """Qualify the exact historical routes by executing both directions + guard."""
    ns, conn, auth, executor, cursor, driver = harness()
    expected = []
    for name in HISTORY:
        data = historical(name)
        if "forwards" in data:
            forward, reverse = data["forwards"], data["backwards"]
            statements = data["FORWARD"] + data["BACKWARD"]
        else:

            def forward(apps, editor, data=data):
                editor.execute(data["FORWARD"])

            def reverse(apps, editor, data=data):
                editor.execute(data["REVERSE"])

            statements = [data["FORWARD"], data["REVERSE"]]
        item = migration(forward, reverse)
        for method in (executor.apply_migration, executor.unapply_migration):
            state = object()
            assert method(state, item) is state
        expected.extend(statements)
    seen = [call.args[0] for call in driver.call_args_list]
    assert all(statement in seen for statement in expected)
    protected = [s for s in expected if ns["protected_mutations"](s, ns["_TABLES"])]
    # Six 0026 sites (FK helper twice), plus both 0035 DO directions.
    assert len(protected) == 9
    for statement in protected:
        for many in (False, True):
            driver.reset_mock()
            try:
                (cursor.executemany if many else cursor.execute)(statement, [])
            except Refused:
                pass
            else:
                raise AssertionError("migration SQL escaped its dispatch boundary")
            driver.assert_not_called()
    assert auth.get() is None
    return {"futureagi/accounts/migrations/" + name for name in HISTORY}


def additional_history_dispatch():
    """Actual non-atomic tracer backfill also runs only via the executor."""
    path = TESTS.parents[1] / "tracer/migrations/0102_reconcile_report_flags.py"
    tree = ast.parse(path.read_text())
    body = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "backfill_has_issues"
    ]
    namespace = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), namespace)
    ns, conn, _, executor, cursor, driver = harness()
    ns["_TABLES"].update({"tracer_trace_scan_result": set()})
    callback = namespace["backfill_has_issues"]
    executor.apply_migration(object(), migration(callback, lambda *args: None))
    assert driver.call_count == 2
    guarded = driver.call_args_list[0].args[0]
    for many in (False, True):
        driver.reset_mock()
        try:
            (cursor.executemany if many else cursor.execute)(guarded, [])
        except Refused:
            pass
        else:
            raise AssertionError("tracer migration escaped executor boundary")
        driver.assert_not_called()
    return {"futureagi/tracer/migrations/0102_reconcile_report_flags.py"}


class MigrationBoundaryTests(unittest.TestCase):
    def test_nonatomic_tracer_backfill_uses_executor_boundary(self):
        self.assertEqual(len(additional_history_dispatch()), 1)

    def test_forward_and_reverse_historical_dispatch_and_runtime_refusal(self):
        self.assertEqual(len(history_dispatch()), 2)

    def test_source_gate_requires_nine_working_migration_routes(self):
        source = runpy.run_path(str(TESTS / "test_hard_erase_sql_source.py"))
        rows = [
            r
            for r in source["classifications"]()
            if any(name in r["site"] for name in HISTORY)
        ]
        protected = [r for r in rows if r["status"] != "OUT_OF_SCOPE"]
        self.assertEqual(len(protected), 9)
        for row in protected:
            self.assertEqual(row["status"], "ROUTED", row)
            self.assertIn("migration boundary", row["reason"])

    def test_other_commands_and_direct_schema_editor_have_no_exemption(self):
        ns, conn, _, executor, cursor, driver = harness()
        sql = historical(HISTORY[0])["FORWARD"][1]
        # An arbitrary command handle, even named migrate, cannot set authority.
        for name in ("flush", "shell", "backfill", "migrate"):
            command = type(
                name, (), {"handle": lambda self: conn.schema_editor().execute(sql)}
            )()
            with self.subTest(command=name), self.assertRaises(Refused):
                command.handle()
        with self.assertRaises(Refused):
            historical(HISTORY[0])["forwards"](None, conn.schema_editor())
        self.assertNotIn(sql, [call.args[0] for call in driver.call_args_list])

    def test_exception_nested_dispatch_and_copied_context_cleanup(self):
        ns, conn, _, executor, cursor, driver = harness()
        sql = historical(HISTORY[0])["FORWARD"][1]
        saved = []

        def fail(apps, editor):
            editor.execute(sql)
            saved.append(copy_context())
            raise RuntimeError("migration failed")

        def outer(apps, editor):
            for method in (executor.apply_migration, executor.unapply_migration):
                with self.assertRaisesRegex(RuntimeError, "migration failed"):
                    method(None, migration(fail, fail))
                editor.execute(sql)  # Nested failure restores the outer permit.

        executor.apply_migration(None, migration(outer, outer))
        self.assertEqual(driver.call_count, 4)
        for context in saved:
            with self.assertRaises(Refused):
                context.run(cursor.execute, sql)
        for method in (executor.apply_migration, executor.unapply_migration):
            with self.assertRaisesRegex(RuntimeError, "migration failed"):
                method(None, migration(fail, fail))
            with self.assertRaises(Refused):
                cursor.execute(sql)
        with self.assertRaises(Refused):
            ns["guarded_raw_delete"](object(), "default")

    def test_connection_thread_and_raw_delete_are_not_borrowed(self):
        ns, conn, _, executor, cursor, driver = harness()
        sql = historical(HISTORY[0])["FORWARD"][1]
        other = SimpleNamespace(in_atomic_block=False)

        def forward(apps, editor):
            editor.execute(sql)
            foreign_driver = Mock()
            with self.assertRaises(Refused):
                ns["guard_execute"](
                    foreign_driver, sql, [], False, {"connection": other}
                )
            foreign_driver.assert_not_called()
            with ThreadPoolExecutor(max_workers=1) as pool:
                for context in (None, copy_context()):
                    call = (
                        (lambda: cursor.execute(sql))
                        if context is None
                        else functools.partial(context.run, cursor.execute, sql)
                    )
                    with self.assertRaises(Refused):
                        pool.submit(call).result()
            with self.assertRaises(Refused):
                ns["guarded_raw_delete"](object(), "default")

        executor.apply_migration(None, migration(forward, forward))
        self.assertEqual(driver.call_count, 1)

    def test_non_atomic_deferred_sql_and_installation_are_scoped(self):
        ns, conn, _, executor, cursor, driver = harness()
        installed = type(executor).apply_migration
        ns["_installed"] = False
        ns["install_sql_guard"]()
        self.assertIs(type(executor).apply_migration, installed)
        ns["_TABLES"] = {"accounts_auth_token"}
        sql = historical(HISTORY[0])["FORWARD"][1]
        original_editor = type(conn.schema_editor())

        class DeferredEditor(original_editor):
            def __exit__(self, *args):
                self.execute(sql)
                return False

        conn.schema_editor = lambda **kw: DeferredEditor()

        def no_op(apps, editor):
            pass

        for method in (executor.apply_migration, executor.unapply_migration):
            method(None, migration(no_op, no_op))
            with self.assertRaises(Refused):
                cursor.execute(sql)
        self.assertEqual(driver.call_count, 2)


if __name__ == "__main__":
    unittest.main()
