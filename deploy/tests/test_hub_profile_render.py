"""Docker Compose render rows; opt in with FA_HUB_PROFILE_RENDER_TESTS=1.

No services are started. The shared compose helper supplies --env-file /dev/null
and synthetic inputs. Offline source equivalents live in test_hub_profile_repairs.
"""

import os
import unittest

from test_observed_catalog_compose import PRODUCTION_HUB, PRODUCTION_PINS, compose


@unittest.skipUnless(os.environ.get("FA_HUB_PROFILE_RENDER_TESTS") == "1", "Docker Compose render row")
class HubProfileRenderTests(unittest.TestCase):
    def test_production_hub_profiles_keep_external_checker_without_local_runner(self):
        env = {key: "synthetic-production-contract" for key in (
            "SECRET_KEY", "FI_API_SECRET_PEPPER", "INTEGRATION_ENCRYPTION_KEY",
            "AGENTCC_INTERNAL_API_KEY", "AGENTCC_ADMIN_TOKEN", "PG_PASSWORD",
            "MINIO_ROOT_PASSWORD", "PROPERTY_CATALOG_API_PASSWORD", "PROPERTY_CATALOG_CONSUMER_PASSWORD",
        )}
        env.update(PRODUCTION_HUB)
        env.update(PRODUCTION_PINS)
        env.update(FRONTEND_URL="https://app.example.com", VITE_HOST_API="https://api.example.com")
        for profiles in ("hub", "hub,full", "full"):
            with self.subTest(profiles=profiles):
                model = compose("deploy/docker-compose.production.yml", COMPOSE_PROFILES=profiles, **env)["services"]
                self.assertNotIn("hub-runner", model)
                self.assertIn("hub-checker", model)
                self.assertEqual(model["hub-checker"]["environment"]["DOCKER_HOST"], PRODUCTION_HUB["OMEGA_HUB_RUNNER_DOCKER_HOST"])
                for name, svc in model.items():
                    if name != "hub-checker" and "OMEGA_HUB_CHECKER_TOKEN" in svc.get("environment", {}):
                        self.assertIn(svc["environment"]["OMEGA_HUB_COMPOSE_PROFILES"], ("hub", "*"), name)

    def test_default_distributed_read_only_set_without_hub_profile(self):
        model = compose(COMPOSE_PROFILES="")["services"]
        self.assertNotIn("hub-runner", model)
        self.assertNotIn("hub-checker", model)
        self.assertEqual({name for name, svc in model.items() if svc.get("read_only")},
                         {"fi-collector", "fi-property-catalog-consumer", "code-executor"})


if __name__ == "__main__":
    unittest.main()
