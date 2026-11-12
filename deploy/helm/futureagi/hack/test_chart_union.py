"""C26 security-union regressions: offline Helm, synthetic CLI inputs only.

No values files are opened by this test. Helm evaluates chart defaults internally.
No manifests or Helm diagnostics are printed. HELM selects the executable.
"""
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

import yaml

CHART = Path(__file__).resolve().parent.parent


class ChartUnionTests(unittest.TestCase):
    def render(self, changes=None, hosted=False):
        settings = {
            "secrets.existingSecret": "synthetic-app",
            "postgres.mode": "bundled", "clickhouse.mode": "bundled",
            "redis.mode": "bundled", "temporal.mode": "bundled",
            "objectStorage.mode": "bundled",
            # The assembly lacks optional namespaces. Supply synthetic disabled
            # structures explicitly, without inspecting its protected defaults.
            "hub": {"enabled": False, "checker": {"extraEnv": {}}},
            "omega": {"enabled": False, "gateway": {"extraEnv": {}}},
            "hosted": {"enabled": False, "migration": {"apply": False, "phase": ""}},
            "mcpFeatures": {"upstreamOAuth": False, "clientOAuth": False, "k2Apps": False},
        }
        if hosted:
            settings.update({
                "hosted.enabled": True, "hosted.releaseRevision": "a" * 40,
                "hosted.singleReplica": False, "hosted.onekeyActivation": True,
                "billing.enforcement": True, "config.envType": "production",
                "backend.image.digest": "sha256:" + "b" * 64,
                "agentccGateway.image.digest": "sha256:" + "c" * 64,
                "agentccGateway.replicas": 2, "agentccGateway.controlPlaneSync": True,
                "agentccGateway.allowPrivateProviderURLs": False,
                "agentccGateway.redis.enabled": True,
                "agentccGateway.service.type": "ClusterIP",
                "agentccGateway.serviceAccount.create": True,
                "agentccGateway.providerCredentials.gatewayOnly": True,
                "agentccGateway.providerCredentials.existingSecret": "synthetic-providers",
                "agentccGateway.providerCredentials.keys": ["OPENAI_API_KEY"],
                "agentccGateway.config": {
                    "server": {"port": 8000, "trusted_proxies": ["192.0.2.10/32"]},
                    "auth": {"enabled": True}, "guardrails": {"enabled": True},
                    "managed": {"spend_guard": {"max_usd_per_minute": 5}},
                    "providers": {"openai": {"api_key": "${OPENAI_API_KEY}"}},
                },
                "networkPolicy.enabled": True,
                "networkPolicy.ingressFrom": [{"namespaceSelector": {"matchLabels": {"synthetic": "ingress"}}}],
            })
        settings.update(changes or {})
        temporary = None
        render_chart = CHART
        if os.environ.get("CHART_UNION_SKIP_SCHEMA") == "1":
            # RED-only isolation of the independently lost schema. Helm 3.15
            # lacks --skip-schema-validation, so omit only the schema in a
            # disposable copy; template bytes remain the assembly's bytes.
            temporary = tempfile.TemporaryDirectory(prefix="chart-union-red-")
            render_chart = Path(temporary.name) / "chart"
            shutil.copytree(CHART, render_chart)
            (render_chart / "values.schema.json").unlink()
        command = [os.environ.get("HELM", "helm"), "template", "union-control", str(render_chart)]
        for key, value in settings.items():
            command.extend(["--set-json", key + "=" + json.dumps(value)])
        for template in ("backend", "worker", "fi-collector", "agentcc-gateway"):
            command.extend(["--show-only", "templates/" + template + "/deployment.yaml"])
        try:
            return subprocess.run(command, text=True, capture_output=True, timeout=30)
        finally:
            if temporary is not None:
                temporary.cleanup()

    def documents(self, result):
        self.assertEqual(result.returncode, 0, "synthetic union chart render failed")
        try:
            return [d for d in yaml.safe_load_all(result.stdout) if d]
        except yaml.YAMLError:
            self.fail("synthetic union render was not YAML")

    def container(self, documents, name):
        matches = [c for d in documents for c in d.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []) if c["name"] == name]
        self.assertEqual(len(matches), 1, "expected one synthetic target container")
        return matches[0]

    def env(self, documents, name):
        entries = self.container(documents, name).get("env", [])
        names = [e["name"] for e in entries]
        self.assertEqual(len(names), len(set(names)), "duplicate environment names")
        return {e["name"]: e.get("value") for e in entries}

    def test_collector_billing_is_derived(self):
        docs = self.documents(self.render({"backend.extraEnv.CLOUD_DEPLOYMENT": "US"}))
        self.assertEqual(self.env(docs, "fi-collector").get("FI_BILLING_ENFORCEMENT_ENABLED"), "true")

    def test_collector_billing_pinned_after_override(self):
        docs = self.documents(self.render({"billing.enforcement": True, "fiCollector.extraEnv.FI_BILLING_ENFORCEMENT_ENABLED": "false"}))
        self.assertEqual(self.env(docs, "fi-collector").get("FI_BILLING_ENFORCEMENT_ENABLED"), "true")

    def test_hosted_runtime_profiles_and_pins(self):
        docs = self.documents(self.render(hosted=True))
        gateway = self.env(docs, "agentcc-gateway")
        backend = self.env(docs, "backend")
        self.assertEqual(gateway.get("AGENTCC_DEPLOYMENT_PROFILE"), "hosted")
        self.assertEqual(backend.get("AGENTCC_DEPLOYMENT_PROFILE"), "hosted")
        self.assertEqual(gateway.get("AGENTCC_AUTH_ENABLED"), "true")
        self.assertEqual(gateway.get("AGENTCC_HOSTED_ONEKEY_ACTIVATION"), "true")
        self.assertEqual(gateway.get("AGENTCC_SYNC_INTERVAL"), "60s")
        self.assertEqual(backend.get("REGISTER_TEMPORAL_SCHEDULES"), "false")
        self.assertEqual(backend.get("AGENTCC_HOSTED_RELEASE_REVISION"), "a" * 40)

    def test_hosted_pins_cannot_be_overridden(self):
        # The padded spelling reaches the pod as the same variable when names
        # are emitted unquoted; the reserved-name check must normalize it.
        refusals = ("hosted refuses", "whitespace around it", "environment names must be plain variable names")
        for scope in ("config", "backend", "worker", "agentccGateway"):
            for name in ("AGENTCC_DEPLOYMENT_PROFILE", " AGENTCC_DEPLOYMENT_PROFILE"):
                with self.subTest(scope=scope, padded=name != name.strip()):
                    result = self.render({scope + ".extraEnv": {name: "self-hosted"}}, hosted=True)
                    self.assertNotEqual(result.returncode, 0, "hosted profile override accepted")
                    expected = refusals if name != name.strip() else refusals[:1]
                    self.assertTrue(any(p in result.stderr for p in expected), "expected fixed refusal was absent")

    def test_omega_reserved_channel_is_rejected(self):
        result = self.render({"worker.extraEnv.OMEGA_HOST_EVENTS_URL": "https://synthetic.invalid/host/events"})
        self.assertNotEqual(result.returncode, 0, "reserved revocation-channel override accepted")
        self.assertIn("OMEGA_HOST_EVENTS_URL", result.stderr)

    def test_omega_untrusted_sender_label_is_rejected(self):
        result = self.render({"commonLabels": {"omega.futureagi.ai/host-events-sender": "true"}})
        self.assertNotEqual(result.returncode, 0, "untrusted sender label accepted")
        self.assertIn("omega.futureagi.ai/host-events-sender", result.stderr)

    def test_self_hosted_positive_control(self):
        self.documents(self.render())


if __name__ == "__main__":
    unittest.main(verbosity=2)
