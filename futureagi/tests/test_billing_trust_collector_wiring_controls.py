"""Stdlib wiring controls complement the collector's pure admission/TLS tests."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CollectorWiringControls(unittest.TestCase):
    def test_collector_startup_uses_verified_redis_options(self):
        source = (ROOT / "fi-collector/cmd/fi-collector/main.go").read_text()
        self.assertIn("redis.NewClient(cfg.Auth.RedisOptions())", source)
        self.assertIn("c.Auth.ApplyRedisEnv(os.Getenv)", source)

    def test_distributed_collector_receives_backend_deployment_signals(self):
        source = (ROOT / "docker-compose.distributed.yml").read_text()
        backend = source.split("x-backend-env:", 1)[1].split("\nx-", 1)[0]
        collector = source.split("\n  fi-collector:\n", 1)[1].split("\n  fi-", 1)[0]
        for env in ("EE_LICENSE_KEY", "CLOUD_DEPLOYMENT"):
            expected = f"{env}: ${{{env}:-}}"
            self.assertIn(expected, backend)
            self.assertIn(expected, collector)


if __name__ == "__main__":
    unittest.main(verbosity=2)
