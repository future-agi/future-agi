import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parent


class LocalRuntimeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load((ROOT / "compose.yaml").read_text())
        cls.services = cls.config["services"]

    def test_existing_collector_forwards_opt_in_notifications(self):
        application = yaml.safe_load(
            (ROOT.parents[1] / "docker-compose.yml").read_text()
        )
        env = application["services"]["fi-collector"]["environment"]
        self.assertEqual(
            env["FI_ERROR_FEED_ENABLED"], "${FI_ERROR_FEED_ENABLED:-false}"
        )
        self.assertEqual(
            env["FI_ERROR_FEED_KAFKA_BROKERS"],
            "${FI_ERROR_FEED_KAFKA_BROKERS:-property-catalog-kafka:9092}",
        )
        self.assertEqual(
            env["FI_ERROR_FEED_KAFKA_TOPIC"],
            "${FI_ERROR_FEED_KAFKA_TOPIC:-error-feed.trace-available.v1}",
        )

    def test_runtime_never_builds_pulls_or_exposes_ports(self):
        self.assertEqual(
            set(self.services), {"omega", "grouping", "reconciliation", "usage"}
        )
        for service in self.services.values():
            self.assertEqual(service["pull_policy"], "never")
            self.assertNotIn("build", service)
            self.assertNotIn("ports", service)
            self.assertTrue(service["read_only"])
            self.assertTrue(service["init"])
            self.assertEqual(service["profiles"], ["omega"])
        self.assertTrue(self.config["networks"]["application"]["external"])

    def test_evidence_and_reports_are_separate_disk_volumes(self):
        worker = self.services["omega"]
        self.assertIn("reports:/var/lib/omega/reports", worker["volumes"])
        self.assertIn("scratch:/var/lib/omega/scratch", worker["volumes"])
        self.assertFalse(any("/var/lib/omega" in mount for mount in worker["tmpfs"]))
        secret_mount = worker["volumes"][2]
        self.assertEqual(secret_mount["target"], "/run/omega-secrets")
        self.assertTrue(secret_mount["read_only"])
        self.assertFalse(secret_mount["bind"]["create_host_path"])
        self.assertNotEqual(worker["env_file"], self.services["grouping"]["env_file"])

    def test_independent_bounded_periodic_jobs(self):
        for name, command in {
            "reconciliation": "reconcile_omega_investigations --project-limit 100 --page-size 100 --max-pages-per-project 2",
            "grouping": "publish_omega_investigation_groups --report-limit 25",
            "usage": "emit_omega_investigation_usage --receipt-limit 100",
        }.items():
            job = self.services[name]
            self.assertIn("python manage.py " + command, job["command"][0])
            self.assertIn("sleep ", job["command"][0])
            self.assertNotIn("temporal", job["command"][0])
            self.assertEqual(job["entrypoint"], ["/bin/sh", "-ec"])
            self.assertEqual(job["environment"]["SERVICE_TYPE"], "bootstrap")
            self.assertEqual(job["environment"]["STARTUP_DB_MUTATION_MODE"], "operator")

    @unittest.skipUnless(shutil.which("docker-compose"), "Compose CLI unavailable")
    def test_compose_renders_without_reading_runtime_secrets(self):
        env = {
            **os.environ,
            "OMEGA_BACKEND_IMAGE": "futureagi/future-agi:dev",
            "OMEGA_WORKER_ENV_FILE": "/tmp/unused-omega-worker.env",
            "OMEGA_BACKEND_ENV_FILE": "/tmp/unused-omega-backend.env",
            "OMEGA_WORKER_SECRETS_DIR": "/tmp/unused-omega-secrets",
            "OMEGA_APPLICATION_NETWORK": "futureagi_default",
            "OMEGA_BACKEND_SOURCE": "/tmp/unused-backend-source",
            "ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED": "false",
        }
        rendered = subprocess.check_output(
            [
                "docker-compose",
                "-f",
                str(ROOT / "compose.yaml"),
                "-f",
                str(ROOT / "compose.source.yaml"),
                "--profile",
                "omega",
                "config",
                "--no-env-resolution",
                "--format",
                "json",
            ],
            env=env,
            text=True,
        )
        services = json.loads(rendered)["services"]
        self.assertEqual(len(services), 4)
        self.assertEqual(
            services["usage"]["environment"]["ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED"],
            "false",
        )
        for name in ("usage", "grouping", "reconciliation"):
            self.assertEqual(services[name]["environment"]["SERVICE_TYPE"], "bootstrap")
            self.assertTrue(
                any(
                    mount.get("target") == "/app/backend" and mount["read_only"]
                    for mount in services[name]["volumes"]
                )
            )
        self.assertEqual(services["omega"]["pull_policy"], "never")
        self.assertFalse(
            any(
                mount.get("target") == "/app/backend"
                for mount in services["omega"]["volumes"]
            )
        )


if __name__ == "__main__":
    unittest.main()
