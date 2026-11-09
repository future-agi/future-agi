"""Ruling 545: PostgreSQL lexing and conservative scanner effects, offline."""

import ast
import runpy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

TESTS = Path(__file__).resolve().parent
SQL = runpy.run_path(str(TESTS / "test_hard_erase_sql_flow.py"))
GRAPH = runpy.run_path(str(TESTS / "hard_erase_callgraph.py"))
SOURCES = runpy.run_path(str(TESTS / "hard_erase_source_files.py"))
SOURCE = "futureagi/accounts/services/scanner_regression.py"
KEY = "accounts.services.scanner_regression.unsafe_entry"


def scan(source):
    """Inject source at ingestion, keeping actual issuance roots, never fake edges."""
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == GRAPH["ROOT"] / SOURCE:
            return source
        return original(path, *args, **kwargs)

    files = [SOURCE, "futureagi/accounts/services/identity_issuance.py"]
    with patch.object(Path, "read_text", read):
        return GRAPH["graph"](files=files)


class PostgreSQLLexicalTests(unittest.TestCase):
    def dispatch(self, text, refused):
        ns, conn, _ = SQL["SQLBoundaryTests"]().namespace()
        conn.in_atomic_block = False
        for many in (False, True):
            with self.subTest(sql=text, many=many):
                driver = Mock(return_value="ok")
                if refused:
                    with self.assertRaises(SQL["Refused"]):
                        ns["guard_execute"](
                            driver, text, [], many, {"connection": conn}
                        )
                    driver.assert_not_called()
                else:
                    self.assertEqual(
                        ns["guard_execute"](
                            driver, text, [], many, {"connection": conn}
                        ),
                        "ok",
                    )
                    driver.assert_called_once_with(text, [], many, {"connection": conn})

    def test_nested_comments_cannot_hide_execution(self):
        for prefix in (
            "/* outer /* inner */ tail */ ",
            "/*" * 64 + " nested " + "*/" * 64,
            "-- line\r\n/* ' $tag$ /* inner */ \" */ ",
        ):
            for statement in (
                "DO $$ BEGIN DELETE FROM accounts_user; END; $$;",
                "EXECUTE destructive_plan",
                "CALL destructive_procedure()",
                "EXPLAIN EXECUTE destructive_plan",
                "PREPARE p AS CALL destructive_procedure()",
            ):
                self.dispatch(prefix + statement, True)
                self.dispatch("SELECT 1; " + prefix + statement, True)

    def test_nested_comments_preserve_pure_reads_and_upserts(self):
        prefix = "/* outer /* inner */ tail */ "
        for text in (
            "SELECT * FROM accounts_user",
            "WITH u AS (SELECT * FROM accounts_user) SELECT * FROM u",
            "VALUES ('accounts_user')",
            "TABLE accounts_user",
            "EXPLAIN TABLE accounts_user",  # Ruling 584: protected EXPLAIN is bare.
            "SELECT 1; TABLE accounts_user",
            "EXPLAIN INSERT INTO ordinary_table(id) VALUES(1) ON CONFLICT(id) DO NOTHING",
            "PREPARE p AS INSERT INTO ordinary_table(id) VALUES(1) ON CONFLICT(id) DO UPDATE SET id=2",
        ):
            self.dispatch(prefix + text, False)

    def test_quoting_is_opaque_but_real_following_statements_refuse(self):
        for literal in (
            "'/* outer /* inner */ tail */ ; DO ''quoted'''",
            "E'escaped \\' ; DO /* not a comment */'",
            r"'^[0-9]+(\.[0-9]+)?$'",  # Existing annotation timing query.
            r"'paired \\'",
            "$$ /* ; ' DO DELETE FROM accounts_user */ $$",
            "$body$ /* ; $$ ' CALL p() $body$",
        ):
            self.dispatch("SELECT " + literal + " FROM accounts_user", False)
            self.dispatch("SELECT " + literal + "; CALL p()", True)
        self.dispatch('SELECT "/*;DO" FROM accounts_user', False)
        self.dispatch('SELECT "a"";CALL" FROM accounts_user', False)
        self.dispatch("DELETE FROM U&\"accounts!005fuser\" UESCAPE '!'", True)

    def test_unterminated_or_unsupported_constructs_fail_closed(self):
        for text in (
            "SELECT 1 /* outer /* inner */",
            "SELECT 'unterminated",
            "SELECT E'escaped \\'",
            r"SELECT 'ambiguous \' ; CALL p() --'",
            'SELECT "unterminated',
            "SELECT $tag$ unterminated $$",
            "SELECT 1; /*",
            "SELECT 1 */",
            "SELECT `unsupported`",
            "SELECT U&'unsupported unicode literal'",
            "SELECT 1\x00",
        ):
            self.dispatch(text, True)


class ConservativeScannerTests(unittest.TestCase):
    def rejected(self, source):
        try:
            functions, edges, reached = scan(source)
        except ValueError as exc:
            self.assertIn("unresolved", str(exc))
        else:
            self.assertIn(KEY, reached)
            self.assertTrue(
                any(
                    key == KEY
                    for key, _ in GRAPH["violations"](functions, edges, reached)
                ),
                source,
            )

    def test_invoked_and_callback_lambdas_cannot_hide_issuance(self):
        for body in (
            "issue = lambda: issue_membership(manager, 'create')\n    row.save()\n    issue()",
            "row.save()\n    dispatch(lambda: issue_membership(manager, 'create'))",
            "issue = lambda: issue_membership(manager, 'create')\n    row.save()\n    dispatch(issue)",
            "issue = lambda: (lambda: issue_membership(manager, 'create'))()\n    row.save()\n    issue()",
        ):
            self.rejected(
                "from .identity_issuance import issue_membership\n"
                "@transaction.atomic\ndef unsafe_entry():\n    " + body + "\n"
            )

    def test_default_lambdas_are_registered_and_invocations_checked(self):
        self.rejected(
            "from .identity_issuance import issue_membership\n"
            "@transaction.atomic\ndef unsafe_entry(issue=lambda: issue_membership(manager, 'create')):\n"
            "    row.save()\n    issue()\n"
        )

    def test_aliases_and_transitive_helper_writes_precede_issuance(self):
        for helpers, body in (
            ("", "persist = row.save\n    persist()"),
            ("", "self.persist = row.save\n    self.persist()"),
            ("", "persist = row.save\n    again = persist\n    again()"),
            ("", "lock = rows.select_for_update\n    lock()"),
            ("", "persist = cursor.execute\n    persist(statement)"),
            (
                "",
                "persist = rows.filter(active=True).update\n    persist(active=False)",
            ),
            ("", "persist = connection.cursor().execute\n    persist(statement)"),
            ("", "persist = rows.bulk_custom_write\n    persist(items)"),
            ("", "persist = row.save\n    persist = harmless\n    persist()"),
            ("def persist():\n    row.save()\n", "persist()"),
            (
                "def lock():\n    rows.select_for_update()\ndef persist():\n    lock()\n",
                "persist()",
            ),
            (
                "class Helper:\n    def persist(self):\n        row.save()\n",
                "helper = Helper()\n    helper.persist()",
            ),
            ("", "persist = lambda: row.save()\n    persist()"),
            ("", "dispatch(lambda: row.save())"),
        ):
            self.rejected(
                "from .identity_issuance import issue_membership\n"
                + helpers
                + "@transaction.atomic\ndef unsafe_entry():\n    "
                + body
                + "\n    issue_membership(manager, 'create')\n"
            )

    def test_known_callback_and_original_func_shim_propagate_writes(self):
        for body in (
            "transaction.on_commit(persist)",
            "original = getattr(persist, '_original_func', None)\n    if callable(original):\n        original()",
        ):
            self.rejected(
                "from django.db import transaction\n"
                "from .identity_issuance import issue_membership\n"
                "def persist():\n    row.save()\n"
                "@transaction.atomic\ndef unsafe_entry():\n    "
                + body
                + "\n    issue_membership(manager, 'create')\n"
            )

    def test_unresolved_write_references_fail_closed(self):
        for body in (
            "callbacks = [row.save]\n    callbacks[0]()",
            "dispatch(row.save)",
            "persist = row.save\n    callbacks = {'write': persist}\n    callbacks['write']()",
        ):
            self.rejected(
                "from .identity_issuance import issue_membership\n"
                "@transaction.atomic\ndef unsafe_entry():\n    "
                + body
                + "\n    issue_membership(manager, 'create')\n"
            )

        self.rejected(
            "from .identity_issuance import issue_membership\n"
            "@transaction.atomic\ndef unsafe_entry(callable):\n"
            "    callable(row.save)\n    issue_membership(manager, 'create')\n"
        )
        self.rejected(
            "from .identity_issuance import issue_membership\n"
            "def outer(callable):\n"
            "    @transaction.atomic\n    def unsafe_entry():\n"
            "        callable(row.save)\n        issue_membership(manager, 'create')\n"
            "    unsafe_entry()\n"
        )

    def test_exact_non_row_phase_classifications_fail_closed_on_source_change(self):
        ns = runpy.run_path(str(TESTS / "hard_erase_callgraph_classifications.py"))
        all_rows = ns["EFFECT_CLASSIFICATIONS"]
        rows = SOURCES["available_effect_rows"](GRAPH["ROOT"], all_rows)
        self.assertTrue(rows, "the OSS classification controls must still execute")
        if not SOURCES["ee_available"](GRAPH["ROOT"]):
            self.assertEqual(set(rows), {key for key in all_rows if not key.startswith("ee.")})
        else:
            self.assertEqual(set(rows), set(all_rows))
        functions = {}
        for key in {
            *rows,
            *(row["callee"] for row in rows.values() if "callee" in row),
        }:
            module = (
                key.split(".FalconAIConsumer.")[0]
                if ".FalconAIConsumer." in key
                else key.rsplit(".", 1)[0]
            )
            path = GRAPH["ROOT"] / "futureagi" / (module.replace(".", "/") + ".py")
            node = next(
                n
                for n in ast.walk(ast.parse(path.read_text()))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == key.rsplit(".", 1)[-1]
            )
            functions[key] = (str(path), node, module, "")
        evaluated = set()
        for key in rows:
            function = functions[key][1]
            ns["qualify"](key, function, functions, ast.walk(function))
            self.assertTrue(
                any(
                    getattr(node, "issuance_non_row_effect", False)
                    for node in ast.walk(function)
                ),
                key,
            )
            changed = ast.parse(ast.unparse(function)).body[0]
            changed.body.insert(0, ast.parse("row.save()").body[0])
            ns["qualify"](key, changed, functions, ast.walk(changed))
            self.assertFalse(
                any(
                    getattr(node, "issuance_non_row_effect", False)
                    for node in ast.walk(changed)
                ),
                key,
            )
            if "callee" in rows[key]:
                callee_key = rows[key]["callee"]
                source, callee, module, owner = functions[callee_key]
                mutated = ast.parse(ast.unparse(callee)).body[0]
                mutated.body.insert(0, ast.parse("row.save()").body[0])
                changed_functions = {**functions, callee_key: (source, mutated, module, owner)}
                fresh = ast.parse(ast.unparse(function)).body[0]
                ns["qualify"](key, fresh, changed_functions, ast.walk(fresh))
                self.assertFalse(any(getattr(n, "issuance_non_row_effect", False) for n in ast.walk(fresh)), callee_key)
            if key == "accounts.gcp_marketplace_utils.process_signup":
                marked = [n for n in ast.walk(function) if getattr(n, "issuance_non_row_effect", False)]
                self.assertEqual([ast.unparse(n) for n in marked], [rows[key]["call"]] * 2)
                writes = [n for n in ast.walk(function) if isinstance(n, ast.Call) and ast.unparse(n.func) in {"User.objects.create_user", "organization.save", "issue_membership"}]
                self.assertEqual(len(writes), 3)
                self.assertTrue(all(GRAPH["dominated"](n, GRAPH["entry_scopes"](function)) for n in writes))
                self.assertFalse(any(getattr(n, "issuance_non_row_effect", False) for n in writes))
                # Moving finishing under the entry changes the transaction
                # boundary and must invalidate the separate-phase summary.
                fresh = ast.parse(ast.unparse(function)).body[0]
                scope = GRAPH["entry_scopes"](fresh)[0]
                finish = next(n for n in fresh.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and ast.unparse(n.value) == rows[key]["call"])
                fresh.body.remove(finish)
                scope.body.append(finish)
                ns["qualify"](key, fresh, functions, ast.walk(fresh))
                self.assertFalse(any(getattr(n, "issuance_non_row_effect", False) for n in ast.walk(fresh)))
            evaluated.add(key)
        self.assertEqual(evaluated, set(rows))

    def test_entry_scope_dominates_alias_helper_and_lambda_effects(self):
        functions, edges, reached = scan(
            "from .identity_issuance import issue_membership\n"
            "def persist():\n    row.save()\n"
            "@transaction.atomic\ndef unsafe_entry():\n"
            "    with issuance_entry(people=[user.pk]):\n"
            "        alias = row.save\n        alias()\n        persist()\n"
            "        issue = lambda: issue_membership(manager, 'create')\n        issue()\n"
        )
        self.assertIn(KEY, reached)
        self.assertEqual(GRAPH["violations"](functions, edges, reached), [])


if __name__ == "__main__":
    unittest.main()
