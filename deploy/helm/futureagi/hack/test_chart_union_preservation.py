"""Synthetic preservation controls at the c2.5/custody/KS4 merge seams."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("union_controls", Path(__file__).with_name("test_chart_union.py"))
controls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controls)


class UnionPreservationTests(unittest.TestCase):
    def setUp(self):
        self.control = controls.ChartUnionTests()

    def custody(self, changes=None):
        settings = {
            "agentccGateway.providerCredentials.gatewayOnly": True,
            "agentccGateway.providerCredentials.existingSecret": "synthetic-providers",
            "agentccGateway.providerCredentials.keys": ["OPENAI_API_KEY"],
            "agentccGateway.serviceAccount.create": True,
        }
        settings.update(changes or {})
        return settings

    def refused(self, settings, phrase, hosted=False):
        result = self.control.render(settings, hosted=hosted)
        self.assertNotEqual(result.returncode, 0, "synthetic forbidden composition rendered")
        self.assertTrue(phrase in result.stderr, "expected fixed refusal was absent")

    def test_gateway_only_reference_stays_exclusive(self):
        docs = self.control.documents(self.control.render(self.custody()))
        for name in ("backend", "fi-collector", "agentcc-gateway"):
            env = self.control.container(docs, name).get("env", [])
            entries = [entry for entry in env if entry["name"] == "OPENAI_API_KEY"]
            if name == "agentcc-gateway":
                self.assertEqual(len(entries), 1)
                ref = entries[0]["valueFrom"]["secretKeyRef"]
                self.assertEqual(ref["name"], "synthetic-providers")
                self.assertFalse(ref.get("optional", False))
            else:
                self.assertEqual(entries, [])

    def test_hosted_requires_gateway_only(self):
        self.refused({"agentccGateway.providerCredentials.gatewayOnly": False},
                     "hosted.enabled requires agentccGateway.providerCredentials.gatewayOnly=true", hosted=True)

    def test_hosted_provider_names_cannot_erase_runtime_pin(self):
        self.refused({"agentccGateway.providerCredentials.keys": ["OPENAI_API_KEY", "AGENTCC_DEPLOYMENT_PROFILE"]},
                     "hosted refuses safety-setting or test-endpoint providerCredentials.keys", hosted=True)

    def test_shared_provider_secret_is_refused(self):
        self.refused(self.custody({"agentccGateway.providerCredentials.existingSecret": "synthetic-app"}),
                     "must name a Secret no other workload reads")

    def test_default_gateway_account_is_refused(self):
        self.refused(self.custody({"agentccGateway.serviceAccount": {"create": False, "name": "default"}}),
                     "dedicated non-default ServiceAccount")

    def test_webhook_reference_survives_custody_union(self):
        docs = self.control.documents(self.control.render(self.custody()))
        env = self.control.container(docs, "backend")["env"]
        entries = [e for e in env if e["name"] == "WEBHOOKS_ENCRYPTION_KEYS"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["valueFrom"]["secretKeyRef"],
                         {"name": "synthetic-app", "key": "WEBHOOKS_ENCRYPTION_KEYS", "optional": True})

    def test_inline_webhook_key_is_refused(self):
        self.refused({"config.extraEnv.WEBHOOKS_ENCRYPTION_KEYS": "synthetic-denied"},
                     "WEBHOOKS_ENCRYPTION_KEYS must come only from secrets.existingSecret")

    def test_hosted_backend_health_and_readiness_remain_distinct(self):
        docs = self.control.documents(self.control.render(hosted=True))
        backend = self.control.container(docs, "backend")
        self.assertEqual(backend["readinessProbe"]["httpGet"]["path"], "/readyz/")
        self.assertEqual(backend["startupProbe"]["httpGet"]["path"], "/health/")
        self.assertEqual(backend["livenessProbe"]["httpGet"]["path"], "/health/")

    def test_self_hosted_backend_retains_candidate_readiness(self):
        docs = self.control.documents(self.control.render())
        self.assertEqual(self.control.container(docs, "backend")["readinessProbe"]["httpGet"]["path"], "/health/")

    def gateway_documents(self, hosted):
        from unittest.mock import patch
        settings = {
            "gatewayApi.enabled": True,
            "gatewayApi.parentRefs": [{"name": "synthetic-gateway"}],
            "gatewayApi.app.host": "app.example.com",
            "gatewayApi.api.host": "api.example.com",
            "gatewayApi.llmGateway.enabled": True,
            "gatewayApi.llmGateway.host": "llm.example.com",
            "gatewayApi.otlp.enabled": False,
            "gatewayApi.otlpGrpc.enabled": False,
            "gatewayApi.gke.healthChecks.enabled": True,
        }
        real_run = controls.subprocess.run
        def with_policy_output(command, **kwargs):
            # Extend the output selector, not Helm behavior or its result.
            return real_run([*command, "--show-only", "templates/gateway-api.yaml"], **kwargs)
        with patch.object(controls.subprocess, "run", with_policy_output):
            return self.control.documents(self.control.render(settings, hosted=hosted))

    def test_gke_policy_matches_hosted_readiness_ternary(self):
        for hosted, expected in ((False, "/health/"), (True, "/readyz/")):
            with self.subTest(hosted=hosted):
                policies = [d for d in self.gateway_documents(hosted)
                            if d["kind"] == "HealthCheckPolicy" and d["spec"]["targetRef"]["name"].endswith("-backend")]
                self.assertEqual(len(policies), 1)
                check = policies[0]["spec"]["default"]["config"]["httpHealthCheck"]
                self.assertEqual(check["requestPath"], expected)
                self.assertEqual(check["host"], "localhost")

    def test_hosted_gateway_public_route_keeps_prefix_allowlist(self):
        routes = [d for d in self.gateway_documents(True) if d["kind"] == "HTTPRoute"
                  and d["spec"].get("hostnames") == ["llm.example.com"]]
        self.assertEqual(len(routes), 1)
        prefixes = [m["path"]["value"] for rule in routes[0]["spec"]["rules"] for m in rule["matches"]]
        self.assertEqual(prefixes, ["/v1", "/v1beta", "/a2a", "/deepgram", "/cartesia"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
