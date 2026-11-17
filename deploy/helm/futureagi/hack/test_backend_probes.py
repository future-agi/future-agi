"""Offline backend probe controls using synthetic --set overrides only.

HELM may select the Helm binary. No values files are opened by this test or
passed with -f; Helm evaluates its own chart defaults internally. Only the
non-Secret templates needed by the probe/policy oracle are returned by Helm.
"""

import importlib.util
import os
from pathlib import Path
import subprocess
import unittest

import yaml

CHART = Path(__file__).resolve().parent.parent


def policy_checks():
    spec = importlib.util.spec_from_file_location(
        "rendered_checks", CHART / "hack/rendered_checks.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackendProbeRenderTests(unittest.TestCase):
    def render(self, *, gateway=False):
        command = [
            os.environ.get("HELM", "helm"), "template", "probe-test", str(CHART),
            "--namespace", "probe-test",
        ]
        settings = [
            "secrets.existingSecret=synthetic-probe-secret",
            "postgres.mode=bundled",
            "clickhouse.mode=bundled",
            "redis.mode=bundled",
            "temporal.mode=bundled",
            "objectStorage.mode=bundled",
        ]
        templates = ["backend/deployment.yaml"]
        if gateway:
            settings.extend([
                "gatewayApi.enabled=true",
                "gatewayApi.parentRefs[0].name=synthetic-gateway",
                "gatewayApi.app.host=app.example.com",
                "gatewayApi.api.host=api.example.com",
                "gatewayApi.otlp.enabled=false",
                "gatewayApi.otlpGrpc.enabled=false",
                "gatewayApi.llmGateway.enabled=false",
                "gatewayApi.gke.healthChecks.enabled=true",
            ])
            templates.extend([
                "backend/service.yaml", "frontend/deployment.yaml",
                "frontend/service.yaml", "gateway-api.yaml",
            ])
        for setting in settings:
            command.extend(["--set", setting])
        for template in templates:
            command.extend(["--show-only", f"templates/{template}"])
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        # Never include rendered manifests or error output in a failure message.
        self.assertEqual(result.returncode, 0, "synthetic probe chart render failed")
        try:
            return [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        except yaml.YAMLError:
            self.fail("synthetic probe chart render is not valid YAML")

    def backend(self, documents):
        backends = [
            doc for doc in documents
            if doc["kind"] == "Deployment"
            and doc["metadata"]["labels"]["app.kubernetes.io/component"] == "backend"
        ]
        self.assertEqual(len(backends), 1)
        return backends[0]["spec"]["template"]["spec"]["containers"][0]

    # The c2.5 chart keeps the hosted readiness ternary: /readyz/ only with
    # hosted.enabled (hack/test_chart_union_preservation.py), /health/ here.
    def test_self_hosted_readiness_uses_health(self):
        self.assertEqual(self.backend(self.render())["readinessProbe"]["httpGet"]["path"], "/health/")

    def test_startup_and_liveness_remain_health(self):
        backend = self.backend(self.render())
        for name in ("startupProbe", "livenessProbe"):
            with self.subTest(probe=name):
                self.assertEqual(backend[name]["httpGet"]["path"], "/health/")

    def test_probe_hosts_and_ports_are_unchanged(self):
        backend = self.backend(self.render())
        for name in ("startupProbe", "readinessProbe", "livenessProbe"):
            with self.subTest(probe=name):
                probe = backend[name]["httpGet"]
                self.assertEqual(probe["port"], "http")
                self.assertEqual(probe["httpHeaders"], [{"name": "Host", "value": "localhost"}])

    def test_gke_backend_policy_uses_readiness(self):
        policies = [
            doc for doc in self.render(gateway=True)
            if doc["kind"] == "HealthCheckPolicy"
            and doc["spec"]["targetRef"]["name"] == "probe-test-futureagi-backend"
        ]
        self.assertEqual(len(policies), 1)
        check = policies[0]["spec"]["default"]["config"]["httpHealthCheck"]
        self.assertEqual(check["requestPath"], "/health/")
        self.assertEqual(check["host"], "localhost")
        self.assertEqual(check["portSpecification"], "USE_SERVING_PORT")

    def test_rendered_policy_consistency_oracle_passes(self):
        documents = self.render(gateway=True)
        self.assertEqual(
            policy_checks().check_health_check_policies("backend-probes", documents), []
        )


if __name__ == "__main__":
    unittest.main()
