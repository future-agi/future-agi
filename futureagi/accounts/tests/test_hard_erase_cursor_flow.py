"""Product COPY path and delayed driver authority regressions, without a DB."""

import ast
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

TESTS = Path(__file__).resolve().parent
SUPPORT = runpy.run_path(str(TESTS / "test_hard_erase_r2f5_flow.py"))
SQL = SUPPORT["SQL"]


class ProductCopyTests(unittest.TestCase):
    def test_actual_ingestion_models_work_and_protected_model_refuses(self):
        scanner = runpy.run_path(str(TESTS / "test_hard_erase_sql_source.py"))
        _, protected, model_names = scanner["model_census"](
            scanner["tracked_sources"]()
        )
        source = TESTS.parents[1] / "tracer/utils/trace_ingestion.py"
        function = next(
            n
            for n in ast.parse(source.read_text()).body
            if isinstance(n, ast.FunctionDef) and n.name == "_bulk_create_with_copy"
        )
        for model_name in ("Trace", "ObservationSpan", "ProtectedNegative"):
            ns, conn, _, cursor, driver = SUPPORT["cursor_namespace"]()
            table = (
                model_names["tracer", model_name]
                if model_name != "ProtectedNegative"
                else "accounts_user"
            )
            if model_name != "ProtectedNegative":
                self.assertNotIn(table, protected)
            ns["_TABLES"] = set(protected) | {"accounts_user"}
            conn.cursor = lambda cursor=cursor: cursor
            cursor.close = Mock()
            model = SimpleNamespace(
                _meta=SimpleNamespace(
                    db_table=table,
                    concrete_fields=[SimpleNamespace(column="id", attname="id")],
                )
            )
            env = {
                "models": SimpleNamespace(
                    Model=object, JSONField=type("JSONField", (), {})
                ),
                "connection": conn,
            }
            exec(
                compile(
                    ast.Module(body=[function], type_ignores=[]), str(source), "exec"
                ),
                env,
            )
            with self.subTest(model=model_name):
                if model_name == "ProtectedNegative":
                    with self.assertRaises(SQL["Refused"]):
                        env["_bulk_create_with_copy"](model, [SimpleNamespace(id=1)])
                    driver.copy.assert_not_called()
                else:
                    env["_bulk_create_with_copy"](model, [SimpleNamespace(id=1)])
                    driver.copy.assert_called_once()
                    driver.copy.return_value.__enter__.return_value.write_row.assert_called_once_with(
                        (1,)
                    )

    def test_deferred_copy_checks_entry_and_writes_after_expiry(self):
        _, conn, auth, cursor, driver = SUPPORT["cursor_namespace"]()
        conn.in_atomic_block = True
        auth.set((conn, "erase"))
        statement = "COPY accounts_user FROM STDIN"
        deferred = cursor.copy(statement)
        auth.set(None)
        with self.assertRaises(SQL["Refused"]):
            deferred.__enter__()
        driver.copy.return_value.__enter__.assert_not_called()
        auth.set((conn, "erase"))
        with cursor.copy(statement) as copy:
            auth.set(None)
            with self.assertRaises(SQL["Refused"]):
                copy.write_row((1,))
            auth.set((conn, "erase"))
        driver.copy.return_value.__enter__.return_value.write_row.assert_not_called()


if __name__ == "__main__":
    unittest.main()
