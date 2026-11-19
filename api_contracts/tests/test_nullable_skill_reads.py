"""Required JSON keys may carry null without making their object types nullable."""

import json
import unittest
from pathlib import Path


class NullableSkillReadTests(unittest.TestCase):
    def test_nullability_belongs_to_the_property(self):
        swagger = Path(__file__).resolve().parents[1] / "openapi/swagger.json"
        defs = json.loads(swagger.read_bytes())["definitions"]
        for parent, key, target in (
            ("SkillsContextDetail", "own_draft", "SkillsContextOwnDraft"),
            ("LatestLoadResult", "load", "LatestLoad"),
            ("LatestLoadResult", "fetch", "LatestFetch"),
        ):
            with self.subTest(parent=parent, key=key):
                self.assertIn(key, defs[parent]["required"])
                self.assertEqual(defs[parent]["properties"][key], {
                    "allOf": [{"$ref": f"#/definitions/{target}"}], "x-nullable": True,
                })
                self.assertNotIn("x-nullable", defs[target])
                self.assertEqual(defs[target]["type"], "object")
        detail = defs["SkillsContextDetail"]
        self.assertIn("latest", detail["required"])
        self.assertIs(detail["properties"]["latest"]["x-nullable"], True)
        self.assertEqual(detail["properties"]["latest"]["type"], "object")
