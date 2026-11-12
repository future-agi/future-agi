"""733 L4-B: no OrgApiKey access, local service auth and deploy alignment."""

import copy
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from agentcc.services.config_push import _inject_fi_credentials, _transform_guardrails


@pytest.mark.parametrize(
    "base", [None, "", "http://LOCALHOST:80///", "http://localhost"]
)
@pytest.mark.parametrize("enabled", [True, False])
def test_733_local_eval_strips_pair_without_reading_keys(settings, base, enabled):
    settings.BASE_URL = "http://localhost/"
    cfg = {"api_key": "stale-pair", "secret_key": "stale-secret", "eval_id": "15"}
    if base is not None:
        cfg["base_url"] = base
    checks = {"futureagi-eval": {"enabled": enabled, "config": cfg}}
    with patch("accounts.models.user.OrgApiKey.no_workspace_objects.filter") as lookup:
        _inject_fi_credentials(checks, "11111111-1111-4111-8111-111111111111")
    lookup.assert_not_called()
    assert cfg["auth"] == "gateway_service"
    assert "api_key" not in cfg and "secret_key" not in cfg
    assert cfg["base_url"] == "http://localhost"
    assert cfg["call_type"] == "protect"


@pytest.mark.parametrize("base", ["http://127.0.0.1:8001", "http://localhost/Other"])
def test_733_other_platform_keeps_operator_pair(settings, base):
    settings.BASE_URL = "http://localhost/"
    cfg = {
        "base_url": base,
        "api_key": "operator-pair",
        "secret_key": "operator-secret",
    }
    checks = {"futureagi-eval": {"enabled": True, "config": cfg}}
    with patch("accounts.models.user.OrgApiKey.no_workspace_objects.filter") as lookup:
        _inject_fi_credentials(checks, "org")
    lookup.assert_not_called()
    assert cfg["api_key"] == "operator-pair" and cfg["secret_key"] == "operator-secret"
    assert cfg.get("auth", "pair") == "pair"
    assert cfg["base_url"] == base


def test_733_changed_destination_cannot_keep_service_auth(settings):
    settings.BASE_URL = "http://localhost"
    cfg = {
        "base_url": "http://127.0.0.1:8001",
        "auth": "gateway_service",
        "api_key": "operator-pair",
        "secret_key": "operator-secret",
    }
    _inject_fi_credentials({"futureagi-eval": {"enabled": True, "config": cfg}}, "org")
    assert cfg.get("auth", "pair") == "pair"
    assert cfg["api_key"] == "operator-pair" and cfg["secret_key"] == "operator-secret"


@pytest.mark.parametrize("shape", ["checks-map", "checks-list", "rules"])
def test_733_transform_is_service_only_and_does_not_mutate_storage(settings, shape):
    settings.BASE_URL = "http://localhost"
    rule = {
        "name": "futureagi-eval",
        "enabled": True,
        "config": {"eval_id": "15", "api_key": "old", "secret_key": "old"},
    }
    section = (
        {"checks": {"futureagi-eval": rule}}
        if shape == "checks-map"
        else {"checks" if shape == "checks-list" else "rules": [rule]}
    )
    original = copy.deepcopy(section)
    with patch("agentcc.services.config_push._inject_guardrail_credentials"):
        result = _transform_guardrails(section, "org")
    assert section == original
    cfg = result["checks"]["futureagi-eval"]["config"]
    assert cfg["auth"] == "gateway_service"
    assert "api_key" not in cfg and "secret_key" not in cfg


@pytest.mark.parametrize("gateway", ["http://localhost", "HTTP://LOCALHOST:80///"])
def test_733_deploy_check_accepts_normalized_match(settings, gateway):
    settings.BASE_URL = "http://localhost/"
    out = StringIO()
    call_command("agentcc_check_eval_backend", gateway_fi_base_url=gateway, stdout=out)
    assert "aligned" in out.getvalue()


@pytest.mark.parametrize(
    "gateway",
    [
        "",
        "http://127.0.0.1:8001",
        "http://localhost/path",
        "http://localhost?x=1",
        "http://localhost#fragment",
        "http://password@localhost",
    ],
)
def test_733_deploy_check_rejects_mismatch_without_echoing_urls(settings, gateway):
    settings.BASE_URL = "http://localhost/"
    with pytest.raises(CommandError, match="BASE_URL.*FI_BASE_URL") as caught:
        call_command("agentcc_check_eval_backend", gateway_fi_base_url=gateway)
    assert "password" not in str(caught.value)
