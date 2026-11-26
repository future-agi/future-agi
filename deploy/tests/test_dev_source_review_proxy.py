"""Keep the development proxy open while a source write waits for the sandbox."""

import re
import unittest
from pathlib import Path


class SourceReviewProxyTests(unittest.TestCase):
    def test_only_source_preparation_endpoints_receive_the_sandbox_timeout(self):
        template = (Path(__file__).parents[1] / "dev-api-proxy/default.conf.template").read_text()
        match = re.search(r'location ~ "(\^/simulate/api/[^"]+)" \{(.*?)\n    \}', template, re.S)
        self.assertIsNotNone(match)
        route = re.compile(match[1])
        base = "/simulate/api/"
        review = "harness-source-reviews/d7965e3a-85f9-48f5-badd-221f540baf54/"
        for path in ("harness-jobs/", "harness-environment-designs/", "harness-source-reviews/", review + "accept/", review + "reprepare/"):
            with self.subTest(path=path):
                self.assertIsNotNone(route.fullmatch(base + path))
        for path in (review, review + "cancel/", review + "scenario-generation/", "harness-environment-designs/documents/"):
            with self.subTest(path=path):
                self.assertIsNone(route.fullmatch(base + path))
        self.assertIn("proxy_read_timeout 330s;", match[2])
        self.assertIn("proxy_next_upstream off;", match[2])
