"""Alert thresholds are numbers; auto_pause is a boolean, never coerced."""

import pytest
import yaml

from test_hosted_caps import activated
from test_hosted_deployment import ROOT, render


@pytest.mark.parametrize("value", ["25", True, "NaN", [], {}, "Infinity", False])
def test_hosted_alert_wrong_kind(tmp_path, value):
    result = render(tmp_path, activated({"max_usd_per_minute": 5, "alert_usd_per_hour": value}))
    assert result.returncode != 0
    assert "managed.spend_guard.alert_usd_per_hour" in result.stderr
    assert "must be a number" in result.stderr


@pytest.mark.parametrize("value", ["true", "false", 1, 0, [], {}, "private-sentinel"])
def test_hosted_auto_pause_wrong_kind(tmp_path, value):
    result = render(tmp_path, activated({"max_usd_per_minute": 5, "auto_pause": value}))
    assert result.returncode != 0
    assert "managed.spend_guard.auto_pause" in result.stderr
    assert "must be a boolean" in result.stderr
    assert "private-sentinel" not in result.stderr


@pytest.mark.parametrize("alert", [0, 25, 0.5])
@pytest.mark.parametrize("pause", [True, False])
def test_hosted_alert_valid_kinds(tmp_path, alert, pause):
    result = render(tmp_path, activated({"max_usd_per_minute": 5, "alert_usd_per_hour": alert, "auto_pause": pause}))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("database,override", [(4, False), (9, False), (9, True)])
def test_hosted_chart_aligns_settlement_database(tmp_path, database, override):
    data = activated({"max_usd_per_minute": 5})
    if override:
        data["agentccGateway"]["extraEnv"] = {"AGENTCC_REDIS_DB": str(database)}
    else:
        data["agentccGateway"]["redis"] = {"db": database}
    result = render(tmp_path, data)
    assert result.returncode == 0, result.stderr
    checked = set()
    for doc in yaml.safe_load_all(result.stdout):
        if not doc or doc["kind"] != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            env = {item["name"]: item.get("value") for item in container.get("env", [])}
            if env.get("AGENTCC_DEPLOYMENT_PROFILE") != "hosted":
                continue
            assert env["AGENTCC_REDIS_DB"] == str(database)
            if container["name"] != "agentcc-gateway":
                assert env["REDIS_URL"].endswith("/" + str(database))
            checked.add(container["name"])
    assert {"backend", "agentcc-gateway"} <= checked


def test_hosted_backfill_readiness_contract_is_documented():
    text = (ROOT / "deploy/helm/futureagi/README.md").read_text()
    assert "Gateway /readyz may succeed during the approved compatible-runtime backfill" in text


def test_hosted_compose_declares_billing_database_and_warning():
    text = (ROOT / "deploy/docker-compose.hosted.yml").read_text()
    assert 'AGENTCC_REDIS_DB: "4"' in text
    assert "settlement drain" in text and "database 4" in text
    text = (ROOT / "deploy/env-reference.toml").read_text()
    assert "settlement drain" in text and "AGENTCC_REDIS_DB" in text
