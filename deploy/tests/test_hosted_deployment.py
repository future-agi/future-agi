"""Cloud-only render contract. No cluster, credentials or vendor contact."""

import ast
import copy
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy/helm/futureagi"


def values():
    return {
        "hosted": {"releaseRevision": "a" * 40},
        "secrets": {"existingSecret": "synthetic-shared-keys"},
        "backend": {"image": {"digest": "sha256:" + "b" * 64}},
        "agentccGateway": {
            "image": {"digest": "sha256:" + "c" * 64},
            "config": {"server": {"trusted_proxies": ["192.0.2.10/32"]},
                       "managed": {"spend_guard": {"max_usd_per_minute": 5}}},
        },
        "networkPolicy": {
            "ingressFrom": [{"namespaceSelector": {"matchLabels": {"synthetic": "ingress"}}}]
        },
    }


def render(tmp_path, overrides):
    assert shutil.which("helm"), "helm required for hosted render gate"
    fixture = tmp_path / "synthetic-values.yaml"
    fixture.write_text(yaml.safe_dump(overrides))
    return subprocess.run(
        ["helm", "template", "hosted-test", str(CHART), "-f", str(CHART / "examples/bundled.yaml"),
         "-f", str(CHART / "examples/hosted-gateway.yaml"), "-f", str(fixture),
         "--set", "secrets.allowGeneratedKeysWithoutCluster=true"],
        capture_output=True, text=True, timeout=30,
    )


def test_hosted_render_routes_readiness_and_removes_bootstrap_hook(tmp_path):
    result = render(tmp_path, values())
    assert result.returncode == 0, result.stderr
    docs = [row for row in yaml.safe_load_all(result.stdout) if row]
    assert not [row for row in docs if row["kind"] == "Job"]
    workloads = [row for row in docs if row["kind"] == "Deployment"]
    containers = {container["name"]: container for row in workloads for container in row["spec"]["template"]["spec"]["containers"]}
    gateway = containers["agentcc-gateway"]
    backend = containers["backend"]
    assert gateway["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert gateway["startupProbe"]["httpGet"]["path"] == "/livez"
    assert backend["readinessProbe"]["httpGet"]["path"] == "/readyz/"
    assert backend["livenessProbe"]["httpGet"]["path"] == "/health/"
    for container in (gateway, backend):
        env = {row["name"]: row.get("value") for row in container["env"]}
        assert env["AGENTCC_DEPLOYMENT_PROFILE"] == "hosted"
        assert "@sha256:" in container["image"]


@pytest.mark.parametrize("path,value", [
    (("secrets", "existingSecret"), ""),
    (("agentccGateway", "replicas"), 1),
    (("agentccGateway", "controlPlaneSync"), False),
    (("agentccGateway", "allowPrivateProviderURLs"), True),
    (("agentccGateway", "config", "auth", "enabled"), False),
    (("agentccGateway", "config", "guardrails", "enabled"), False),
    (("agentccGateway", "config", "mcp", "enabled"), True),
    (("agentccGateway", "config", "tools", "enabled"), True),
    (("agentccGateway", "extraEnv", "AGENTCC_DEPLOYMENT_PROFILE"), "self-hosted"),
    (("agentccGateway", "extraEnv", "AGENTCC_TEST_ENDPOINT"), "http://loopback.invalid"),
    (("agentccGateway", "existingConfigMap"), "opaque-map"),
    (("networkPolicy", "enabled"), False),
])
def test_hosted_render_refuses_unsafe_shape(tmp_path, path, value):
    data = copy.deepcopy(values())
    node = data
    for name in path[:-1]:
        node = node.setdefault(name, {})
    node[path[-1]] = value
    result = render(tmp_path, data)
    assert result.returncode != 0
    assert "hosted" in result.stderr.lower()


def test_existing_config_map_still_gets_profile_and_checksum(tmp_path):
    data = values()
    data["agentccGateway"]["existingConfigMap"] = "operator-reviewed-map"
    data["hosted"]["existingConfigChecksum"] = "d" * 64
    result = render(tmp_path, data)
    assert result.returncode == 0, result.stderr
    assert "AGENTCC_DEPLOYMENT_PROFILE" in result.stdout
    assert "checksum/config:" in result.stdout


@pytest.mark.parametrize("active", [False, True])
def test_hosted_onekey_activation_is_explicit_and_gateway_only(tmp_path, active):
    data = values()
    if active:
        data["hosted"]["onekeyActivation"] = True
        data["agentccGateway"]["config"]["providers"] = {
            "synthetic": {"type": "openrouter", "api_format": "openai",
                          "base_url": "https://upstream.synthetic.invalid", "managed": True}
        }
    result = render(tmp_path, data)
    assert result.returncode == 0, result.stderr
    for doc in yaml.safe_load_all(result.stdout):
        if not doc or doc["kind"] != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            env = {row["name"]: row.get("value") for row in container.get("env", [])}
            if container["name"] == "agentcc-gateway":
                assert env["AGENTCC_HOSTED_ONEKEY_ACTIVATION"] == str(active).lower()
            else:
                assert "AGENTCC_HOSTED_ONEKEY_ACTIVATION" not in env


@pytest.mark.parametrize("active,provider,want", [
    (False, {"managed": True}, "refuses credits-backed"),
    (True, {"managed": True, "api_format": "anthropic"}, "api_format=openai"),
    (True, {"managed": True, "type": "ollama", "api_format": "openai"}, "provider kind"),
    (True, {"managed": True, "api_format": "openai", "skip_tls": True}, "insecure/local"),
])
def test_hosted_onekey_chart_refuses_unqualified_providers(tmp_path, active, provider, want):
    data = values()
    data["hosted"]["onekeyActivation"] = active
    data["agentccGateway"]["config"]["providers"] = {"synthetic": provider}
    result = render(tmp_path, data)
    assert result.returncode != 0 and want in result.stderr


def test_hosted_onekey_chart_refuses_extra_env_bypass_and_mixed_chain(tmp_path):
    data = values()
    data["agentccGateway"]["extraEnv"] = {"AGENTCC_HOSTED_ONEKEY_ACTIVATION": "true"}
    result = render(tmp_path, data)
    assert result.returncode != 0 and "extraEnv" in result.stderr
    del data["agentccGateway"]["extraEnv"]
    data["hosted"]["onekeyActivation"] = True
    data["agentccGateway"]["config"]["routing"] = {"model_fallbacks": {"catalogue/model": ["token/model"]}}
    result = render(tmp_path, data)
    assert result.returncode != 0 and "fallback" in result.stderr
    del data["agentccGateway"]["config"]["routing"]
    data["agentccGateway"]["config"]["managed"] = {"mixed_billing_chains": True}
    result = render(tmp_path, data)
    assert result.returncode != 0 and "mixed_billing_chains" in result.stderr


def source_constant(relative, name):
    """A module-level integer constant, read without importing Django."""
    for node in ast.parse((ROOT / relative).read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a literal in {relative}")


def operator_budget_seconds():
    """The operator's own schema deadline plus the process grace the rehearsal
    allows it: Kubernetes must never stop the Job before the operator's
    graceful 'exceeded deadline; preserve applied-state metadata' refusal."""
    return source_constant(
        "futureagi/agentcc/hosted_migration_progress.py", "DEFAULT_SCHEMA_DEADLINE_SECONDS"
    ) + source_constant("futureagi/agentcc/tests/hosted_rehearsal_process.py", "SCHEMA_PROCESS_GRACE_SECONDS")


def chart_default_deadline():
    chart_values = yaml.safe_load((CHART / "values.yaml").read_text())
    return chart_values["hosted"]["migration"].get("activeDeadlineSeconds")


def operator_jobs(result):
    assert result.returncode == 0, result.stderr
    return [row for row in yaml.safe_load_all(result.stdout) if row and row["kind"] == "Job"]


def test_phased_operator_job_is_explicit_and_not_a_hook(tmp_path):
    data = values()
    data["hosted"]["migration"] = {"phase": "schema"}
    result = render(tmp_path, data)
    assert result.returncode == 0, result.stderr
    jobs = [row for row in yaml.safe_load_all(result.stdout) if row and row["kind"] == "Job"]
    assert len(jobs) == 1
    assert "helm.sh/hook" not in jobs[0]["metadata"].get("annotations", {})
    assert jobs[0]["spec"]["activeDeadlineSeconds"] == chart_default_deadline()
    assert jobs[0]["spec"]["activeDeadlineSeconds"] >= operator_budget_seconds()
    container = jobs[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["command"][-1] == "hosted_gateway_migrate"
    assert container["args"] == ["schema"]
    env = {row["name"]: row.get("value") for row in container["env"]}
    assert env["PG_DIRECT_HOST"]
    assert env["REGISTER_TEMPORAL_SCHEDULES"] == "false"


def test_operator_job_deadline_default_covers_the_schema_budget():
    budget = operator_budget_seconds()
    assert budget == 930
    assert chart_default_deadline() == 960 >= budget
    schema = json.loads((CHART / "values.schema.json").read_text())
    field = schema["properties"]["hosted"]["properties"]["migration"]["properties"]["activeDeadlineSeconds"]
    assert field["type"] == "integer" and field["minimum"] == budget


@pytest.mark.parametrize("phase", ["schema", "backfill", "check"])
@pytest.mark.parametrize("deadline", [930, 1800])
def test_operator_job_deadline_is_a_chart_value(tmp_path, phase, deadline):
    data = values()
    data["hosted"]["migration"] = {"phase": phase, "activeDeadlineSeconds": deadline}
    jobs = operator_jobs(render(tmp_path, data))
    assert len(jobs) == 1
    assert jobs[0]["spec"]["activeDeadlineSeconds"] == deadline


@pytest.mark.parametrize("deadline", [600, 929, 0, "960"])
def test_operator_job_deadline_below_the_schema_budget_is_refused(tmp_path, deadline):
    data = values()
    data["hosted"]["migration"] = {"phase": "schema", "activeDeadlineSeconds": deadline}
    result = render(tmp_path, data)
    assert result.returncode != 0
    assert "activeDeadlineSeconds" in result.stderr


def test_hosted_compose_requires_values_and_uses_shell_free_probe():
    path = ROOT / "deploy/docker-compose.hosted.yml"
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
    command = ["docker", "compose", "--env-file", "/dev/null", "-f", str(path), "config", "--format", "json"]
    refused = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert refused.returncode != 0
    # Every required input comes from this file; fixture values exist only in
    # the subprocess environment and are never written to a .env or receipt.
    import re
    for name in set(re.findall(r"\$\{([A-Z_]+):\?", path.read_text())):
        env[name] = secrets.token_hex(32)
    env.update(BACKEND_IMAGE="synthetic/backend", GATEWAY_IMAGE="synthetic/gateway",
               BACKEND_DIGEST="sha256:" + "a" * 64, GATEWAY_DIGEST="sha256:" + "b" * 64,
               PG_PORT="5432", CH_PORT="9000", REDIS_URL="redis://redis:6379/4",
               INTEGRATION_ENCRYPTION_KEY=Fernet.generate_key().decode(),
               BASE_URL="https://api.synthetic.invalid", FRONTEND_URL="https://app.synthetic.invalid",
               AGENTCC_REDIS_ADDRESS="redis:6379", AGENTCC_TRUSTED_PROXY_CIDR="192.0.2.10/32")
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    gateway = services["agentcc-gateway"]
    assert gateway["healthcheck"]["test"] == ["CMD", "/usr/local/bin/healthcheck", "http://127.0.0.1:8080/readyz"]
    assert gateway["deploy"]["replicas"] == 1
    assert not gateway.get("ports")
    assert services["backend"]["environment"]["NO_STARTUP_DB_MUTATIONS"] == "true"

    from django.test import override_settings
    from agentcc.hosted import validate_hosted_configuration

    backend_env = services["backend"]["environment"]
    with override_settings(
        DEBUG=False,
        INTEGRATION_ENCRYPTION_KEY=backend_env["INTEGRATION_ENCRYPTION_KEY"],
        INTEGRATION_ENCRYPTION_KEY_EPHEMERAL=False,
        REDIS_URL=backend_env["REDIS_URL"],
        CACHES={"default": {"BACKEND": "django_redis.cache.RedisCache", "LOCATION": backend_env["REDIS_URL"]}},
    ):
        validate_hosted_configuration(backend_env)


def test_hosted_gateway_api_keeps_admin_private_and_checks_backend_readiness(tmp_path):
    data = values()
    data["gatewayApi"] = {
        "enabled": True,
        "parentRefs": [{"name": "synthetic-controller"}],
        "app": {"host": "app.synthetic.invalid"},
        "api": {"host": "api.synthetic.invalid"},
        "llmGateway": {"enabled": True, "host": "gateway.synthetic.invalid"},
        "gke": {"healthChecks": {"enabled": True}},
    }
    result = render(tmp_path, data)
    assert result.returncode == 0, result.stderr
    documents = [row for row in yaml.safe_load_all(result.stdout) if row]
    route = next(row for row in documents if row["kind"] == "HTTPRoute" and row["metadata"]["name"].endswith("llm-gateway"))
    paths = {match["path"]["value"] for rule in route["spec"]["rules"] for match in rule["matches"]}
    assert paths == {"/v1", "/v1beta", "/a2a", "/deepgram", "/cartesia"}
    assert "/" not in paths and "/-" not in paths
    policy = next(row for row in documents if row["kind"] == "HealthCheckPolicy" and row["metadata"]["name"].endswith("backend"))
    assert policy["spec"]["default"]["config"]["httpHealthCheck"]["requestPath"] == "/readyz/"
