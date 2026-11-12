"""Provider defaults in shipped/rendered Helm config; no cluster or upstream calls."""

import os
from pathlib import Path
import subprocess
import unittest

import yaml

CHART = Path(__file__).resolve().parent.parent


class ProviderConcurrencyTests(unittest.TestCase):
    def assert_remote_defaults(self, provider):
        for field in ("max_concurrent", "conn_pool_size"):
            # An absent/zero value selects the gateway's remote default. An
            # explicit 1024 is also valid; never pin the old 100-slot limit.
            self.assertIn(provider.get(field, 0), (0, 1024), field)

    def render_provider(self, *args):
        result = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", "futureagi", str(CHART),
             "-f", str(CHART / "examples/bundled.yaml"),
             "--set", "secrets.allowGeneratedKeysWithoutCluster=true", *args],
            check=True, text=True, capture_output=True,
        )
        configs = [d for d in yaml.safe_load_all(result.stdout)
                   if d and d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})]
        self.assertEqual(len(configs), 1)
        return yaml.safe_load(configs[0]["data"]["config.yaml"])["providers"]["openai"]

    def test_shipped_remote_defaults(self):
        values = yaml.safe_load((CHART / "values.yaml").read_text())
        self.assert_remote_defaults(values["agentccGateway"]["config"]["providers"]["openai"])

    def test_rendered_remote_defaults(self):
        self.assert_remote_defaults(self.render_provider())

    def test_operator_overrides_are_preserved(self):
        provider = self.render_provider(
            "--set", "agentccGateway.config.providers.openai.max_concurrent=7",
            "--set", "agentccGateway.config.providers.openai.conn_pool_size=9",
            "--set", "agentccGateway.config.providers.openai.queue_timeout=250ms",
        )
        self.assertEqual(provider["max_concurrent"], 7)
        self.assertEqual(provider["conn_pool_size"], 9)
        self.assertEqual(provider["queue_timeout"], "250ms")


if __name__ == "__main__":
    unittest.main()
