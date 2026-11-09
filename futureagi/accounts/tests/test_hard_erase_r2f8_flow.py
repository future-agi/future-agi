"""Ruling 584: bare protected EXPLAIN and PostgreSQL string/COPY compatibility.

Actual policy/guard and installed CursorWrapper with driver doubles, not a
PostgreSQL grammar or database qualification. The dataset builder is unchanged.
"""

import ast
import runpy
import unittest
from pathlib import Path
from unittest.mock import Mock

TESTS = Path(__file__).resolve().parent
SQL = runpy.run_path(str(TESTS / "test_hard_erase_sql_flow.py"))
CURSOR = runpy.run_path(str(TESTS / "test_hard_erase_r2f5_flow.py"))
LEX = runpy.run_path(str(TESTS.parent / "services/hard_erase_sql_lex.py"))


class StatementCompatibilityTests(unittest.TestCase):
    def dispatch(self, text, allowed):
        ns, conn, _ = SQL["SQLBoundaryTests"]().namespace()
        conn.in_atomic_block = False
        for many in (False, True):
            with self.subTest(sql=text, many=many):
                driver = Mock(return_value="ok")
                if allowed:
                    self.assertEqual(
                        ns["guard_execute"](
                            driver, text, [], many, {"connection": conn}
                        ),
                        "ok",
                    )
                    driver.assert_called_once_with(text, [], many, {"connection": conn})
                else:
                    with self.assertRaises(SQL["Refused"]):
                        ns["guard_execute"](
                            driver, text, [], many, {"connection": conn}
                        )
                    driver.assert_not_called()

    def test_protected_explain_refuses_all_option_prefixes(self):
        prefixes = [
            "(FORMAT JSON)",
            '("format" JSON, COSTS TRUE)',
            "('analyze' TRUE)",
            "()",
        ]
        for option in ("analyze", "analyse", "verbose"):
            for spelling in (option, option.upper(), option.title()):
                for name in (spelling, f'"{spelling}"'):
                    prefixes.extend(
                        (name, f"({name})", f"({name} TRUE)", f"({name} FALSE)")
                    )
        for prefix in prefixes:
            for table in ("accounts_user", 'public."accounts_user"'):
                self.dispatch(
                    f"eXpLaIn /* prefix */ {prefix} SELECT * FROM {table}", False
                )
            # The name-based policy does not change ordinary EXPLAIN behavior;
            # syntax validity (including synthetic quoted keywords) is driver-owned.
            self.dispatch(f"EXPLAIN {prefix} SELECT * FROM ordinary_table", True)

    def test_bare_protected_explain_all_read_starts_and_opaque_data(self):
        for text in (
            "EXPLAIN SELECT * FROM accounts_user",
            "EXPLAIN WITH u AS (SELECT * FROM accounts_user) SELECT * FROM u",
            "EXPLAIN VALUES ((SELECT id FROM accounts_user LIMIT 1))",
            'EXPLAIN TABLE public."accounts_user"',
            "/* outer /* inner */ tail */ EXPLAIN /* no options */ SELECT * FROM accounts_user;--end",
            'EXPLAIN SELECT "analyze", "analyse", "verbose" FROM accounts_user',
            "EXPLAIN SELECT '(ANALYZE TRUE)' FROM accounts_user",
        ):
            self.dispatch(text, True)
        for text in (
            'EXPLAIN "select" * FROM accounts_user',
            "EXPLAIN EXPLAIN SELECT * FROM accounts_user",
            "EXPLAIN SELECT * FROM accounts_user FOR UPDATE",
            "EXPLAIN WITH d AS (DELETE FROM accounts_user RETURNING id) SELECT * FROM d",
            "EXPLAIN SELECT * FROM accounts_user; CALL p()",
            "EXPLAIN (FORMAT JSON) EXECUTE p",
        ):
            self.dispatch(text, False)

    def test_standard_strings_are_literal_backslashes_and_doubled_quotes(self):
        self.assertIs(LEX["ASSUMED_STANDARD_CONFORMING_STRINGS"], True)
        for literal, value in (
            (r"'\'", "\\"),
            (r"'\\'", "\\\\"),
            (r"'\'';CALL p()'", "\\';CALL p()"),
            ("'it''s /* not SQL */'", "it's /* not SQL */"),
        ):
            with self.subTest(literal=literal):
                clean, opaque = LEX["statements"]("SELECT " + literal)
                self.assertEqual(len(clean), 1)
                self.assertEqual(list(opaque.values()), [value])
                self.dispatch("SELECT " + literal + " FROM accounts_user", True)
                self.dispatch("SELECT " + literal + "; CALL p()", False)
        self.dispatch(r"SELECT '\'; DELETE FROM accounts_user", False)
        self.dispatch(r"SELECT '\'; /* outer /* inner */ tail */ EXECUTE p", False)
        self.dispatch(r"SELECT '\' ; CALL p() --'", False)

    def test_escape_strings_keep_backslash_escapes_and_whole_input_validation(self):
        for literal in (
            r"E'escaped \' ; CALL p() /* data */'",
            r"e'paired \\'",
            r"E'it''s \' quoted'",
        ):
            self.dispatch("SELECT " + literal + " FROM accounts_user", True)
            self.dispatch("SELECT " + literal + "; CALL p()", False)
        for suffix in (
            "'unterminated",
            r"E'escaped \'",
            '"unterminated',
            "$tag$unterminated",
            "/* outer /* closed inner */",
            "(",
        ):
            self.dispatch("SELECT 1; ;--empty\n" + suffix, False)

    def test_empty_segments_do_not_count_as_executable_statements(self):
        for text in ("", " ; ; ", "--comment", "; /* outer /* inner */ tail */ ;"):
            with self.subTest(sql=text):
                self.assertEqual(LEX["statements"](text)[0], [])
                self.assertEqual(list(LEX["_lex"](text)), [])
        for suffix in (";", ";--comment", "; /* comment */ ; --tail"):
            self.dispatch("COPY ordinary_table FROM STDIN" + suffix, True)
            self.dispatch("COPY accounts_user FROM STDIN" + suffix, False)
            self.dispatch("SELECT 1" + suffix + "\n; DELETE FROM accounts_user", False)

    def test_actual_dataset_search_builder_sends_both_statements(self):
        path = TESTS.parents[1] / "model_hub/utils/SQL_queries.py"
        cls = next(
            n
            for n in ast.parse(path.read_text()).body
            if isinstance(n, ast.ClassDef) and n.name == "SQLQueryHandler"
        )
        cls.body = [
            n
            for n in cls.body
            if isinstance(n, ast.FunctionDef)
            and n.name in {"search_cells_by_text", "build_ilike_pattern"}
        ]
        env = {}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), "exec"), env)
        handler = env["SQLQueryHandler"]
        for many in (False, True):
            for search_key in ("literal%_substring", "back\\slash'quote", "; CALL p()"):
                with self.subTest(many=many, search_key=search_key):
                    ns, conn, _ = SQL["SQLBoundaryTests"]().namespace()
                    conn.in_atomic_block = False
                    rows = [("cell", True, [[0, 3]])]
                    driver = Mock(return_value=rows)

                    def execute_query(
                        query,
                        params=None,
                        fetch_results=True,
                        *,
                        ns=ns,
                        driver=driver,
                        many=many,
                        conn=conn,
                    ):
                        return ns["guard_execute"](
                            driver, query, params, many, {"connection": conn}
                        )

                    handler.execute_query = staticmethod(execute_query)
                    self.assertEqual(
                        handler.search_cells_by_text(search_key, "dataset"), rows
                    )
                    self.assertEqual(driver.call_count, 2)
                    create, select = driver.call_args_list
                    self.assertIn("CREATE OR REPLACE FUNCTION", create.args[0])
                    self.assertIn("$$ LANGUAGE plpgsql IMMUTABLE;", create.args[0])
                    self.assertIn("model_hub_cell", select.args[0])
                    self.assertIn("ESCAPE '\\'", select.args[0])
                    self.assertEqual(
                        select.args[1],
                        (
                            search_key,
                            search_key,
                            handler.build_ilike_pattern(search_key),
                            "dataset",
                        ),
                    )
                    self.assertEqual(create.args[2], many)
                    self.assertEqual(select.args[2], many)


class CopyStatementCountTests(unittest.TestCase):
    def test_cursor_copy_allows_empty_segments_but_no_second_statement(self):
        for prefix in ("", "; /* empty */ ; "):
            for suffix in (";", ";--comment", "; /* outer /* inner */ tail */ ;"):
                for table in ("ordinary_table", "accounts_user"):
                    text = f"{prefix}COPY {table} FROM STDIN{suffix}"
                    with self.subTest(sql=text):
                        _, _, _, cursor, driver = CURSOR["cursor_namespace"]()
                        if table == "ordinary_table":
                            with cursor.copy(text) as copy:
                                copy.write_row((1,))
                            driver.copy.assert_called_once_with(text)
                            driver.copy.return_value.__enter__.return_value.write_row.assert_called_once_with(
                                (1,)
                            )
                        else:
                            with self.assertRaises(CURSOR["SQL"]["Refused"]):
                                cursor.copy(text)
                            self.assertEqual(driver.mock_calls, [])
        for suffix in (
            "; SELECT 1",
            "; COPY ordinary_table FROM STDIN",
            "; DELETE FROM accounts_user",
            ";--empty\n; SELECT 1;",
            "; /* unterminated",
            "; 'unterminated",
            "; E'escaped \\'",
            "; $tag$unterminated",
            '; "unterminated',
        ):
            with self.subTest(suffix=suffix):
                _, _, _, cursor, driver = CURSOR["cursor_namespace"]()
                with self.assertRaises(CURSOR["SQL"]["Refused"]):
                    cursor.copy("COPY ordinary_table FROM STDIN" + suffix)
                self.assertEqual(driver.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
