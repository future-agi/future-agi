"""Contract checks for the bounded, route-specific DEV Insights Ask timeout."""

import re
import unittest
from pathlib import Path


TEMPLATE = Path(__file__).parents[1] / "dev-api-proxy" / "default.conf.template"
RUN = "d7965e3a-85f9-48f5-badd-221f540baf54"


class InsightsProxyTests(unittest.TestCase):
    def setUp(self):
        self.template = TEMPLATE.read_text()
        self.ask = re.search(
            r'location ~ "(?P<route>\^/tracer/insights/[^"\n]+)" \{(?P<body>.*?)\n    \}',
            self.template,
            re.DOTALL,
        )
        self.assertIsNotNone(self.ask)
        self.route = re.compile(self.ask["route"])

    def test_only_the_exact_uuid_ask_route_receives_the_longer_read_timeout(self):
        self.assertIsNotNone(self.route.fullmatch(f"/tracer/insights/runs/{RUN}/ask/"))
        for path in (
            "/accounts/token/",
            f"/tracer/insights/runs/{RUN}/",
            f"/tracer/insights/runs/{RUN}/stop/",
            f"/tracer/insights/runs/{RUN}/ask/extra",
            "/tracer/insights/runs/not-a-uuid/ask/",
            "/tracer/insights/runs/",
            "/v1/traces",
        ):
            with self.subTest(path=path):
                self.assertIsNone(self.route.fullmatch(path))
        self.assertIn("proxy_read_timeout 210s;", self.ask["body"])
        self.assertEqual(self.template.count("proxy_read_timeout 210s;"), 1)
        self.assertEqual(
            self.template.count("proxy_read_timeout ${DEV_API_PROXY_READ_TIMEOUT};"),
            2,
        )

    def test_preserves_other_deadlines_and_never_replays_paid_post(self):
        body = self.ask["body"]
        self.assertIn("proxy_pass http://dev_backend;", body)
        self.assertIn("proxy_connect_timeout ${DEV_API_PROXY_CONNECT_TIMEOUT};", body)
        self.assertIn("proxy_send_timeout ${DEV_API_PROXY_SEND_TIMEOUT};", body)
        self.assertIn("proxy_next_upstream off;", body)
        self.assertIn("proxy_set_header Host $host;", body)
        self.assertNotIn("proxy_set_header Authorization", body)


if __name__ == "__main__":
    unittest.main()
