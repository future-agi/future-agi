"""The hard-erase AST pins hash the same text on every supported Python."""

import ast
import runpy
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
# The text Python 3.13's ast.dump prints by default: None and empty-list fields
# are left out, a constant None is kept.
SAMPLES = {
    "f()": "Module(body=[Expr(value=Call(func=Name(id='f', ctx=Load())))])",
    "import os": "Module(body=[Import(names=[alias(name='os')])])",
    "None": "Module(body=[Expr(value=Constant(value=None))])",
}


class PinTextTests(unittest.TestCase):
    def test_pin_text_drops_empty_and_none_fields_and_keeps_a_constant_none(self):
        for name in (
            "hard_erase_callgraph_dispatch.py",
            "hard_erase_callgraph_classifications.py",
        ):
            canonical = runpy.run_path(str(TESTS / name))["_canonical"]
            for source, text in SAMPLES.items():
                with self.subTest(module=name, source=source):
                    self.assertEqual(canonical(ast.parse(source)), text)


if __name__ == "__main__":
    unittest.main()
