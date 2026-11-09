"""Offline Helm render controls: stdlib only, no cluster or datastore access.

Run with python3 -I. helm template uses only this chart and synthetic values.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import unittest

CHART = Path(__file__).resolve().parent.parent


class CollectorBillingRenderTests(unittest.TestCase):
    def render(self, example="cloud/aks.yaml", *settings, hosted=False):
        cmd = [
            os.environ.get("HELM", "helm"), "template", "billing-control", str(CHART),
            "-f", str(CHART / "examples" / example),
            "--show-only", "templates/fi-collector/deployment.yaml",
            "--set", "secrets.allowGeneratedKeysWithoutCluster=true",
        ]
        fixture = None
        if hosted:
            # Satisfy the existing hosted contract with synthetic identities, so
            # a refusal tests billing rather than an unrelated prerequisite.
            cmd.extend(["-f", str(CHART / "examples/hosted-gateway.yaml"), "-f", "-"])
            fixture = json.dumps({
                "hosted": {"releaseRevision": "a" * 40},
                "secrets": {"existingSecret": "synthetic-shared-keys"},
                "backend": {"image": {"digest": "sha256:" + "b" * 64}},
                "agentccGateway": {
                    "image": {"digest": "sha256:" + "c" * 64},
                    "config": {
                        "server": {"trusted_proxies": ["192.0.2.10/32"]},
                        "managed": {"spend_guard": {"max_usd_per_minute": 5}},
                    },
                },
                "networkPolicy": {
                    "ingressFrom": [{"namespaceSelector": {"matchLabels": {"synthetic": "ingress"}}}],
                },
            })
        for setting in settings:
            cmd.extend(["--set", setting])
        result = subprocess.run(cmd, input=fixture, check=True, capture_output=True, text=True, timeout=30)
        # Inspect only the collector's env list; Helm emits each plain value as
        # a JSON-compatible quoted scalar. Secret references remain separate.
        env = result.stdout.split("          env:\n", 1)[1].split("          readinessProbe:", 1)[0]
        values = {
            name: json.loads(value)
            for name, value in re.findall(r"- name: ([A-Z0-9_]+)\n\s+value: (\"[^\n]*\")", env)
        }
        return values, env

    def test_aks_tls_keeps_address_password_and_verified_tls(self):
        values, env = self.render()
        self.assertEqual(values.get("FI_AUTH_REDIS_ADDR"), "futureagi-example.redis.cache.windows.net:6380")
        self.assertEqual(values.get("FI_AUTH_REDIS_TLS"), "true")
        self.assertIn("- name: FI_AUTH_REDIS_PASSWORD\n", env)
        self.assertRegex(env, r"name: FI_AUTH_REDIS_PASSWORD\n\s+valueFrom:\n\s+secretKeyRef:\n\s+name: \"?futureagi-redis\"?")
        self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), "false")

    def test_plain_mode_keeps_address_and_password(self):
        values, env = self.render("cloud/aks.yaml", "redis.external.tls=false")
        self.assertEqual(values.get("FI_AUTH_REDIS_ADDR"), "futureagi-example.redis.cache.windows.net:6380")
        self.assertEqual(values.get("FI_AUTH_REDIS_TLS"), "false")
        self.assertIn("- name: FI_AUTH_REDIS_PASSWORD\n", env)

    def test_bundled_redis_stays_plain(self):
        values, env = self.render("bundled.yaml")
        self.assertTrue(values.get("FI_AUTH_REDIS_ADDR", "").endswith(":6379"))
        self.assertEqual(values.get("FI_AUTH_REDIS_TLS"), "false")
        self.assertIn("- name: FI_AUTH_REDIS_PASSWORD\n", env)

    def test_license_configures_billing_without_copying_license(self):
        values, env = self.render("cloud/aks.yaml", "license.existingSecret=synthetic-license")
        self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), "true")
        self.assertNotIn("- name: EE_LICENSE_KEY", env)

    def test_cloud_intent_enforces_even_with_usage_events_off(self):
        values, _ = self.render("cloud/aks.yaml", "config.extraEnv.CLOUD_DEPLOYMENT= eu ")
        self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), "true")
        self.assertEqual(values["USAGE_EVENTS_ENABLED"], "false")

    def test_emission_alone_is_not_billing(self):
        values, _ = self.render("cloud/aks.yaml", "config.usageEvents=true")
        self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), "false")

    def test_backend_cloud_signal_cannot_be_disabled_on_collector(self):
        values, _ = self.render(
            "cloud/aks.yaml", "backend.extraEnv.CLOUD_DEPLOYMENT=US",
            "fiCollector.extraEnv.FI_BILLING_ENFORCEMENT_ENABLED=false",
        )
        self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), "true")

    def test_backend_license_override_is_a_billing_signal(self):
        values, env = self.render(
            "cloud/aks.yaml", "config.extraEnv.EE_LICENSE_KEY=synthetic-license",
        )
        self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), "true")
        self.assertNotIn("synthetic-license", env)

    def test_secret_cloud_signal_enforces_without_copying_signals(self):
        values, env = self.render("cloud/aks.yaml", "secrets.extra.CLOUD_DEPLOYMENT=US")
        self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), "true")
        self.assertNotIn("- name: CLOUD_DEPLOYMENT", env)

    def test_license_sources_all_enforce(self):
        for setting in (
            "backend.extraEnv.EE_LICENSE_KEY=synthetic-license",
            "secrets.extra.EE_LICENSE_KEY=synthetic-license",
            "license.key=synthetic-license",
            "secrets.eeLicenseKey=synthetic-license",
        ):
            with self.subTest(setting=setting):
                values, env = self.render("cloud/aks.yaml", setting)
                self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), "true")
                self.assertNotIn("synthetic-license", env)

    def test_plain_self_hosted_auto_is_nonbillable(self):
        values, _ = self.render("bundled.yaml", "hosted.enabled=false", "billing.enforcement=auto")
        self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), "false")

    def test_hosted_auto_is_rejected_even_with_a_cloud_signal(self):
        for signal in ("", "US"):
            with self.subTest(signal=signal):
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    self.render(
                        "bundled.yaml", "billing.enforcement=auto",
                        f"secrets.extra.CLOUD_DEPLOYMENT={signal}", hosted=True,
                    )
                self.assertIn("hosted requires billing.enforcement=true", caught.exception.stderr)

    def test_hosted_false_is_rejected(self):
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.render("bundled.yaml", "billing.enforcement=false", hosted=True)
        self.assertIn("hosted requires billing.enforcement=true", caught.exception.stderr)

    def test_hosted_explicit_true_renders_billable_collector(self):
        values, _ = self.render("bundled.yaml", "billing.enforcement=true", hosted=True)
        self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), "true")

    def test_explicit_enforcement_overrides_both_ways(self):
        for setting, signal, collector, expected in (
            ("true", "", "false", "true"),
            ("false", "US", "true", "false"),
        ):
            with self.subTest(setting=setting):
                values, _ = self.render(
                    "cloud/aks.yaml", f"billing.enforcement={setting}",
                    f"secrets.extra.CLOUD_DEPLOYMENT={signal}",
                    f"fiCollector.extraEnv.FI_BILLING_ENFORCEMENT_ENABLED={collector}",
                )
                self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), expected)

    def test_opaque_envfrom_can_explicitly_enable_enforcement(self):
        values, _ = self.render(
            "cloud/aks.yaml", "config.extraEnvFrom[0].secretRef.name=synthetic-billing-env",
            "billing.enforcement=true",
        )
        self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), "true")

    def test_cloud_precedence_matches_backend_explicit_env(self):
        for settings, expected in (
            (("secrets.extra.CLOUD_DEPLOYMENT=US", "config.extraEnv.CLOUD_DEPLOYMENT=false"), "false"),
            (("config.extraEnv.CLOUD_DEPLOYMENT=US", "backend.extraEnv.CLOUD_DEPLOYMENT=false"), "false"),
            (("config.extraEnv.CLOUD_DEPLOYMENT=false", "backend.extraEnv.CLOUD_DEPLOYMENT=EU"), "true"),
        ):
            with self.subTest(settings=settings):
                values, _ = self.render("cloud/aks.yaml", *settings)
                self.assertEqual(values.get("FI_BILLING_ENFORCEMENT_ENABLED"), expected)

    def test_invalid_enforcement_is_rejected(self):
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.render("cloud/aks.yaml", "billing.enforcement=tru")
        self.assertIn("enforcement", caught.exception.stderr)

    def test_hosted_overlay_explicitly_enforces(self):
        # The hosted preparation overlay deliberately refuses to render without
        # release-only prerequisites; inspect its declarative billing contract.
        text = (CHART / "examples/hosted-gateway.yaml").read_text()
        self.assertRegex(text, r"(?m)^billing:\n  enforcement: true$")

    def test_tls_operator_overrides_are_preserved(self):
        values, _ = self.render(
            "cloud/aks.yaml", "fiCollector.extraEnv.FI_AUTH_REDIS_ADDR=proxy.invalid:6380",
            "fiCollector.extraEnv.FI_AUTH_REDIS_TLS_SERVER_NAME=cache.invalid",
        )
        self.assertEqual(values.get("FI_AUTH_REDIS_ADDR"), "proxy.invalid:6380")
        self.assertEqual(values.get("FI_AUTH_REDIS_TLS"), "true")
        self.assertEqual(values.get("FI_AUTH_REDIS_TLS_SERVER_NAME"), "cache.invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
