"""What a Python eval's result looks like when it reaches the scorer: a number or
boolean comes through as it is, as a JavaScript eval's does, and an async evaluate()
or main() is an error."""

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

EXECUTOR = Path(__file__).resolve().parents[1] / "code-executor"
NUMERIC_IMPORTS = "import decimal\nimport fractions\nimport numpy\n\n\n"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_server():
    """server.py with falcon stubbed."""
    guard = load_module("egress_guard", EXECUTOR / "egress_guard.py")
    stubs = {"falcon": SimpleNamespace(App=mock.Mock), "egress_guard": guard}
    with mock.patch.dict(sys.modules, stubs):
        return load_module("executor_results_under_test", EXECUTOR / "server.py")


def evaluate_returning(expression, imports=""):
    return f"{imports}def evaluate():\n    return {expression}\n"


class PythonResultTests(unittest.TestCase):
    """Each eval runs the executor's Python script in a plain interpreter, without
    the jail, and reads what the script prints for the scorer."""

    @classmethod
    def setUpClass(cls):
        cls.server = load_server()

    def run_python_eval(self, code):
        script = self.server._build_python_script(code, {})
        run = subprocess.run(
            [sys.executable, "-I", "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_a_bare_number_reaches_the_scorer_as_a_number(self):
        for expression, number in (("0.25", 0.25), ("1", 1), ("1.5", 1.5)):
            with self.subTest(returns=expression):
                result = self.run_python_eval(evaluate_returning(expression))
                self.assertEqual(result, {"status": "success", "data": number})
                self.assertIs(type(result["data"]), type(number))

    def test_a_bare_boolean_reaches_the_scorer_as_a_boolean(self):
        for value in (True, False):
            with self.subTest(returns=value):
                result = self.run_python_eval(evaluate_returning(value))
                self.assertEqual(result["status"], "success")
                self.assertIs(result["data"], value)

    def test_numpy_and_decimal_scores_become_plain_numbers(self):
        for expression, plain in (
            ("numpy.float32(0.25)", 0.25),
            ("numpy.int64(1)", 1.0),
            ("numpy.bool_(True)", True),
            ("decimal.Decimal('0.25')", 0.25),
            ("fractions.Fraction(1, 4)", 0.25),
        ):
            for key in (None, "score", "result"):
                returns = expression if key is None else f"{{'{key}': {expression}}}"
                with self.subTest(returns=returns):
                    code = evaluate_returning(returns, NUMERIC_IMPORTS)
                    result = self.run_python_eval(code)
                    self.assertEqual(result["status"], "success")
                    data = result["data"]
                    score = data if key is None else data["result"]
                    self.assertEqual(score, plain)
                    self.assertIs(type(score), type(plain))

    def test_an_async_evaluate_is_an_error_not_a_pass(self):
        code = "async def evaluate():\n    return 1.0\n"
        result = self.run_python_eval(code)
        self.assertEqual(result["status"], "error")
        self.assertIn("Define evaluate() with def, not async def", result["data"])

    def test_an_async_generator_evaluate_is_an_error_not_a_pass(self):
        code = "async def evaluate():\n    yield 1.0\n"
        result = self.run_python_eval(code)
        self.assertEqual(result["status"], "error")
        self.assertIn("Define evaluate() with def, not async def", result["data"])

    def test_the_async_error_names_the_called_function(self):
        result = self.run_python_eval("async def main():\n    return 1.0\n")
        self.assertEqual(result["status"], "error")
        self.assertIn("call main() without awaiting it", result["data"])
        self.assertIn("Define main() with def, not async def", result["data"])

    def test_other_returns_score_as_before(self):
        for expression, data in (
            ("'0.2'", {"result": 1.0, "reason": "0.2"}),
            ("[1, 2]", {"result": 1.0, "reason": "[1, 2]"}),
        ):
            with self.subTest(returns=expression):
                result = self.run_python_eval(evaluate_returning(expression))
                self.assertEqual(result, {"status": "success", "data": data})
        result = self.run_python_eval(evaluate_returning("None"))
        self.assertEqual(result, {"status": "skip", "data": None})


if __name__ == "__main__":
    unittest.main()
