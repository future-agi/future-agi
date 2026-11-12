"""HOSTED-HARDEN-1: launch shape and activated money policy, cloud-only."""

import copy
import shutil
import subprocess
import tomllib

import pytest
import yaml

from test_hosted_deployment import CHART, ROOT, render, values


@pytest.mark.parametrize("single", [False, True])
def test_hosted_replica_modes_render_and_lint(tmp_path, single):
    data = values()
    data["hosted"]["singleReplica"] = single
    gateway = data["agentccGateway"]
    gateway["replicas"] = 1 if single else 2
    gateway["autoscaling"] = {"enabled": False}
    # Single-replica mode must neutralize even an explicitly blocking PDB.
    gateway["pdb"] = {"enabled": True, "maxUnavailable": 0}
    result = render(tmp_path, data)
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    deployment = next(doc for doc in docs if doc["kind"] == "Deployment" and
                      doc["metadata"]["name"].endswith("agentcc-gateway"))
    assert deployment["spec"]["replicas"] == (1 if single else 2)
    assert deployment["spec"]["strategy"] == {
        "type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    policies = [doc for doc in docs if doc["kind"] == "PodDisruptionBudget" and
                doc["metadata"]["name"].endswith("agentcc-gateway")]
    assert len(policies) == (0 if single else 1)
    assert not [doc for doc in docs if doc["kind"] == "HorizontalPodAutoscaler" and
                doc["metadata"]["name"].endswith("agentcc-gateway")]
    assert shutil.which("helm")
    lint = subprocess.run([
        "helm", "lint", str(CHART), "-f", str(CHART / "examples/bundled.yaml"),
        "-f", str(CHART / "examples/hosted-gateway.yaml"),
        "-f", str(tmp_path / "synthetic-values.yaml"),
        "--set", "secrets.allowGeneratedKeysWithoutCluster=true"], capture_output=True, text=True, timeout=30)
    assert lint.returncode == 0, lint.stderr + lint.stdout


@pytest.mark.parametrize("single,replicas,hpa,want", [
    (True, 1, True, "singleReplica refuses autoscaling"),
    (True, 0, False, "singleReplica requires exactly one"),
    (True, 2, False, "singleReplica requires exactly one"),
    (False, 1, False, "at least two"),
])
def test_hosted_replica_modes_refuse_invalid_shape(tmp_path, single, replicas, hpa, want):
    data = values()
    data["hosted"]["singleReplica"] = single
    data["agentccGateway"].update(replicas=replicas, autoscaling={"enabled": hpa})
    result = render(tmp_path, data)
    assert result.returncode != 0 and want in result.stderr


@pytest.mark.parametrize("managed,want", [
    ({"require_credits": False}, "managed.require_credits=false"),
    ({"require_redis": False}, "managed.require_redis=false"),
    ({"grant_only_credits": True}, "managed.grant_only_credits=true"),
    ({"spend_guard": {"max_usd_per_minute": 0, "max_usd_per_hour": 0,
                      "alert_usd_per_hour": 0, "new_org_daily_cap_usd": 0}}, "managed.spend_guard"),
])
def test_hosted_money_policy_matches_startup(tmp_path, managed, want):
    data = values()
    data["hosted"]["onekeyActivation"] = True
    data["agentccGateway"]["config"]["managed"] = managed
    result = render(tmp_path, data)
    assert result.returncode != 0 and want in result.stderr
    # Preparation-only behavior is unchanged.
    control = copy.deepcopy(data)
    control["hosted"]["onekeyActivation"] = False
    result = render(tmp_path, control)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("guard", [
    {"max_usd_per_minute": 0.01},
    {"max_usd_per_minute": 0.01, "max_usd_per_hour": 0, "new_org_daily_cap_usd": 0, "alert_usd_per_hour": 0},
    {"max_usd_per_minute": 0, "max_usd_per_hour": 0.01, "new_org_daily_cap_usd": 0, "alert_usd_per_hour": 0},
])
def test_hosted_money_explicit_velocity_cap_loads(tmp_path, guard):
    data = values()
    data["hosted"]["onekeyActivation"] = True
    data["agentccGateway"]["config"]["managed"] = {"spend_guard": guard}
    result = render(tmp_path, data)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("alert", [None, 0, 1, 25])
@pytest.mark.parametrize("pause", [False, True])
def test_hosted_alert_only_guard_is_refused(tmp_path, alert, pause):
    data = values()
    guard = {"max_usd_per_minute": 0, "max_usd_per_hour": 0,
             "new_org_daily_cap_usd": 0, "auto_pause": pause}
    if alert is not None:
        guard["alert_usd_per_hour"] = alert
    data["hosted"]["onekeyActivation"] = True
    data["agentccGateway"]["config"]["managed"] = {"spend_guard": guard}
    result = render(tmp_path, data)
    assert result.returncode != 0 and "managed.spend_guard" in result.stderr
    for profile in ("preparation", "self-hosted"):
        control = copy.deepcopy(data)
        if profile == "preparation":
            control["hosted"]["onekeyActivation"] = False
        else:
            control["hosted"]["enabled"] = False
        result = render(tmp_path, control)
        assert result.returncode == 0, result.stderr


def test_hosted_example_labels_multi_replica_shape():
    text = (CHART / "examples/hosted-gateway.yaml").read_text()
    data = yaml.safe_load(text)
    assert data["agentccGateway"]["replicas"] == 2
    assert "multi-replica" in text.lower()
    assert "hosted.singleReplica=true" in text
    assert "agentccGateway.replicas=1" in text
    assert "agentccGateway.autoscaling.enabled=false" in text


def test_hosted_activation_reference_distinguishes_provider_defaults():
    reference = tomllib.loads((ROOT / "deploy/env-reference.toml").read_text())
    rows = [row for part in reference["part"] for section in part["section"] for row in section.get("row", [])]
    text = next(row["text"] for row in rows if "AGENTCC_HOSTED_ONEKEY_ACTIVATION" in row["keys"])
    assert "Compose" in text and "zero providers" in text
    assert "Helm" in text and "default non-managed openai provider" in text
    compose = yaml.safe_load((ROOT / "agentcc-gateway/config.hosted.example.yaml").read_text())
    helm = yaml.safe_load((CHART / "values.yaml").read_text())
    assert not compose.get("providers")
    provider = helm["agentccGateway"]["config"]["providers"]["openai"]
    assert not provider.get("managed", False)
