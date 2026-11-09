"""Exercise the real bundle handler without importing Django or touching DB/storage/network.

This supplements, not replaces, test_web_download.py's request/DB qualification tests.
Run with python3 -I <this file>.
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


class InvalidSource(Exception):
    pass


class BundleSource(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "api" / "public_views.py"
        tree = ast.parse(path.read_text(), filename=str(path))
        view = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "VersionBundleView"
        )
        method = next(
            node
            for node in view.body
            if isinstance(node, ast.FunctionDef) and node.name == "get"
        )
        module = ast.Module(body=[method], type_ignores=[])
        self.version = SimpleNamespace(
            bundle_key="canonical/test",
            digest="sha256:digest",
            bundle_sha256="bundle-sha",
            entry=SimpleNamespace(name="first-agent"),
            version="1.0.0",
            status="approved",
        )
        self.read = Mock(return_value=b"canonical bundle bytes")
        self.count = Mock()
        namespace = {
            "public_version": Mock(return_value=self.version),
            "HubError": InvalidSource,
            "storage": SimpleNamespace(read_bytes=self.read),
            "counts": SimpleNamespace(count_download=self.count),
            "viewer": lambda request: None,
            "is_hub_token": lambda request: False,
            "Version": SimpleNamespace(Status=SimpleNamespace(YANKED="yanked")),
            "bytes_response": lambda data, mime, headers: (data, mime, headers),
        }
        exec(compile(module, str(path), "exec"), namespace)
        self.get = namespace["get"]

    def request(self, query):
        return self.get(
            None,
            SimpleNamespace(query_params=query),
            "future-agi",
            "first-agent",
            "1.0.0",
        )

    def test_web_delivers_the_same_bytes_and_headers_but_never_calls_counts(self):
        expected = self.request({})
        self.count.reset_mock()
        self.assertEqual(self.request({"source": "web"}), expected)
        self.count.assert_not_called()

    def test_web_keeps_the_yanked_header(self):
        self.version.status = "yanked"
        self.version.yanked_reason = "New version\navailable"
        data, mime, headers = self.request({"source": "web"})
        self.assertEqual(data, b"canonical bundle bytes")
        self.assertEqual(mime, "application/gzip")
        self.assertEqual(headers["X-Omega-Hub-Yanked"], "New version available")
        self.count.assert_not_called()

    def test_default_source_still_counts_cli(self):
        self.request({})
        self.assertEqual(self.count.call_args.args[2], "cli")

    def test_other_unknown_sources_still_fail_before_reading_or_counting(self):
        for source in ("bogus", "", "WEB"):
            with self.subTest(source=source):
                with self.assertRaises(InvalidSource) as failure:
                    self.request({"source": source})
                self.assertEqual(failure.exception.args[:2], ("OMEGA_HUB_INVALID", 400))
        self.read.assert_not_called()
        self.count.assert_not_called()


if __name__ == "__main__":
    unittest.main()
