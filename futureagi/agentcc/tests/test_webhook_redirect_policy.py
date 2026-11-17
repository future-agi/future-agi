"""Dependency-free call-site checks; delivery outcomes are tested separately.

Run this file directly with Python when the Django test stack is unavailable.
"""

import ast
import unittest
from pathlib import Path

AGENTCC = Path(__file__).resolve().parents[1]


class TestWebhookRedirectPolicy(unittest.TestCase):
    def assert_no_redirects(self, relative_path, function_name):
        tree = ast.parse((AGENTCC / relative_path).read_text())
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        ]
        self.assertEqual(len(functions), 1)
        posts = [
            node
            for node in ast.walk(functions[0])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "http"
            and node.func.attr == "post"
        ]
        self.assertEqual(len(posts), 1)
        options = {keyword.arg: keyword.value for keyword in posts[0].keywords}
        self.assertIn("allow_redirects", options)
        self.assertIs(ast.literal_eval(options["allow_redirects"]), False)

    def test_delivery_post_disables_redirects(self):
        self.assert_no_redirects("services/webhook_delivery.py", "_deliver_one")

    def test_test_send_post_disables_redirects(self):
        self.assert_no_redirects("views/webhook_outbound.py", "test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
