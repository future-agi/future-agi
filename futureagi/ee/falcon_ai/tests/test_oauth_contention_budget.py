"""Unit-test the real fixture methods without importing Django or opening a DB.

Django runs unittest cleanups before its transactional fixture teardown. The SQL
budget must end at that boundary, not later at pytest's fixture finalization.
This is setup proof only, never an OAuth or PostgreSQL behavioral control.
"""

import ast
import inspect
import unittest
from contextlib import contextmanager
from pathlib import Path


class ContentionBudgetTests(unittest.TestCase):
    def test_scenario_budget_is_restored_before_django_flush(self):
        source = Path(__file__).with_name("test_oauth_writer_contention.py")
        tree = ast.parse(source.read_text())
        definition = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "WriterContentionTests"
        )
        methods = [
            node
            for node in definition.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"fixture_rows", "setUp"}
        ]
        for method in methods:
            method.decorator_list = []
        definition.body = methods
        definition.decorator_list = []
        definition.bases = [ast.Name(id="TestCase", ctx=ast.Load())]
        isolated = ast.fix_missing_locations(
            ast.Module(body=[definition], type_ignores=[])
        )
        active = []

        @contextmanager
        def db_budget():
            active.append(True)
            try:
                yield
            finally:
                active.pop()

        scope = {"TestCase": unittest.TestCase, "db_budget": db_budget}
        # Execute only these repository-owned fixture methods, never app imports.
        exec(compile(isolated, str(source), "exec"), scope)  # noqa: S102
        case = scope["WriterContentionTests"]()
        fixture = case.fixture_rows(object(), object())
        if inspect.isgenerator(fixture):
            next(fixture)
        try:
            case.setUp()
            self.assertEqual(len(active), 1, "scenario lost its SQL timeout budget")
            case.tearDown()
            self.assertTrue(case.doCleanups())
            self.assertEqual(active, [], "scenario timeout leaked into Django flush")
        finally:
            case.doCleanups()
            if inspect.isgenerator(fixture):
                fixture.close()


if __name__ == "__main__":
    unittest.main()
