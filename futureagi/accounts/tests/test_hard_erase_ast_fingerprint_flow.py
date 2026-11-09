"""Source pins survive empty schema additions, never semantic mutations."""

import ast
import copy
import runpy
import unittest
from pathlib import Path

fingerprint = runpy.run_path(
    str(Path(__file__).with_name("hard_erase_ast_fingerprint.py"))
)["fingerprint"]


class AstFingerprintTests(unittest.TestCase):
    def test_empty_type_parameter_schema_is_version_independent(self):
        for source in ("def f():\n    pass", "async def f():\n    pass", "class F:\n    pass"):
            with self.subTest(source=source):
                old = ast.parse(source).body[0]
                old._fields = tuple(name for name in old._fields if name != "type_params")
                new = copy.deepcopy(old)
                new._fields += ("type_params",)
                new.type_params = []
                self.assertEqual(fingerprint(old), fingerprint(new))
                new.type_params = [ast.Name(id="T", ctx=ast.Load())]
                self.assertNotEqual(fingerprint(old), fingerprint(new))

    def test_mutation_order_scalar_type_and_unknown_fields_change_pin(self):
        original = ast.parse("def f():\n    row.save()\n    return True").body[0]
        changed = copy.deepcopy(original)
        changed.body.reverse()
        self.assertNotEqual(fingerprint(original), fingerprint(changed))
        changed = copy.deepcopy(original)
        changed.body[-1].value.value = 1
        self.assertNotEqual(fingerprint(original), fingerprint(changed))
        changed = copy.deepcopy(original)
        changed._fields += ("future_semantics",)
        changed.future_semantics = []
        self.assertNotEqual(fingerprint(original), fingerprint(changed))

    def test_source_locations_do_not_change_pin(self):
        source = "def f():\n    row.save()\n"
        self.assertEqual(fingerprint(ast.parse(source)), fingerprint(ast.parse("\n\n" + source)))


if __name__ == "__main__":
    unittest.main()
