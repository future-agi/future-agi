"""Both E2E stacks tell Request Logs retention their gateway YAML has no budgets.

Retention keeps the current calendar month and week (UTC) unless
AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS is set, because the gateway seeds its YAML
budgets from these logs. gateway.e2e.yaml defines none, and GW-E2E-008 purges
rows 3 days old in whichever container runs Django: the Distributed backend
and worker, and the Standalone app. Compose rendering only; no container is
started.
"""

from __future__ import annotations

import unittest

import yaml

try:
    from .test_e2e_telemetry_env import ROOT, STACKS, render
except ImportError:
    from test_e2e_telemetry_env import ROOT, STACKS, render

FLAG = "AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS"
RETENTION_SERVICES = {
    "distributed": ("backend", "worker"),
    "standalone": ("app",),
}


class E2ERequestLogRetentionEnvironmentTests(unittest.TestCase):
    def test_the_e2e_gateway_config_defines_no_budgets(self):
        config = yaml.safe_load((ROOT / "e2e/stack/gateway.e2e.yaml").read_text())
        self.assertNotIn("budgets", config or {})

    def test_every_stack_running_retention_sets_the_no_yaml_budgets_flag(self):
        for stack, (env_file, files) in STACKS.items():
            services = render(env_file, files)["services"]
            for name in RETENTION_SERVICES[stack]:
                with self.subTest(stack=stack, service=name):
                    environment = services[name].get("environment") or {}
                    self.assertEqual(environment.get(FLAG), "true")


if __name__ == "__main__":
    unittest.main()
