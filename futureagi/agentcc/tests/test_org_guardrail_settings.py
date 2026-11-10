"""Tenant failure policy survives serialization, persistence, and gateway push."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from agentcc.contracts.gateway_admin import GuardrailConfig
from agentcc.models import AgentccOrgConfig
from agentcc.serializers.org_config import AgentccOrgConfigWriteSerializer
from agentcc.services.config_push import _build_payload, _transform_guardrails


@pytest.mark.unit
@pytest.mark.parametrize("shape", ["rules", "checks-list", "checks-map"])
@pytest.mark.parametrize("key", ["fail_open", "failOpen"])
@pytest.mark.parametrize(
    "value,expected",
    [(False, False), (True, True), ("false", False), ("true", True), (None, False)],
)
def test_transform_preserves_explicit_boolean(shape, key, value, expected):
    check = {"name": "test-hook", "enabled": True, "action": "block", "config": {}}
    data = {key: value, "pipelineMode": "sequential"}
    if shape == "checks-map":
        data["checks"] = {
            "test-hook": {"enabled": True, "action": "block", "config": {}}
        }
    else:
        data["rules" if shape == "rules" else "checks"] = [check]
    before = deepcopy(data)
    wire = _transform_guardrails(data)
    assert wire["fail_open"] is expected
    assert wire["pipeline_mode"] == "sequential"
    assert GuardrailConfig.model_validate(wire).fail_open is expected
    assert "failOpen" not in wire and "pipelineMode" not in wire
    assert data == before


@pytest.mark.unit
@pytest.mark.parametrize(
    "guardrails",
    [
        {"fail_open": "not-a-boolean"},
        {"fail_open": []},
        {"fail_open": {}},
        {"fail_open": True, "failOpen": "false"},
        {"pipeline_mode": "unknown"},
        {"pipeline_mode": "parallel", "pipelineMode": "sequential"},
    ],
)
def test_invalid_pipeline_settings_are_refused(guardrails):
    serializer = AgentccOrgConfigWriteSerializer(data={"guardrails": guardrails})
    assert not serializer.is_valid()
    assert "guardrails" in serializer.errors


@pytest.mark.unit
def test_omitted_failure_policy_requires_opt_in():
    serializer = AgentccOrgConfigWriteSerializer(data={"guardrails": {"checks": {}}})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["guardrails"]["fail_open"] is False
    assert serializer.validated_data["guardrails"]["pipeline_mode"] == "parallel"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "value,expected", [("false", False), ("true", True), (False, False), (True, True)]
)
def test_settings_save_and_gateway_payload(auth_client, user, value, expected):
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    ) as push:
        response = auth_client.post(
            "/agentcc/org-configs/",
            {
                "guardrails": {
                    "fail_open": value,
                    "pipeline_mode": "parallel",
                    "checks": {},
                },
                "change_description": "synthetic boolean contract",
            },
            format="json",
        )
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["guardrails"]["fail_open"] is expected
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=user.organization, is_active=True, deleted=False
    )
    assert config.guardrails["fail_open"] is expected
    payload = _build_payload(str(user.organization_id), config)
    assert payload["guardrails"]["fail_open"] is expected
    push.assert_called_once()


@pytest.mark.integration
@pytest.mark.api
def test_invalid_setting_has_no_version_or_push(auth_client, user):
    before = AgentccOrgConfig.no_workspace_objects.filter(
        organization=user.organization
    ).count()
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
    ) as push:
        response = auth_client.post(
            "/agentcc/org-configs/",
            {"guardrails": {"fail_open": "unrecognized"}},
            format="json",
        )
    assert response.status_code == 400, response.json()
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=user.organization
        ).count()
        == before
    )
    push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "old_mode,new_mode", [("parallel", "sequential"), ("sequential", "parallel")]
)
def test_legacy_settings_hydrate_edit_save_and_push(
    auth_client, user, old_mode, new_mode
):
    legacy = AgentccOrgConfig.no_workspace_objects.create(
        organization=user.organization,
        version=1,
        is_active=True,
        guardrails={"failOpen": True, "pipelineMode": old_mode, "checks": {}},
    )
    before = deepcopy(legacy.guardrails)
    hydrated = auth_client.get("/agentcc/org-configs/active/")
    assert hydrated.status_code == 200
    settings = hydrated.json()["result"]["guardrails"]
    assert "failOpen" not in settings and "pipelineMode" not in settings
    assert settings["fail_open"] is True
    assert settings["pipeline_mode"] == old_mode
    settings.update(fail_open=False, pipeline_mode=new_mode)
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    ) as push:
        saved = auth_client.post(
            "/agentcc/org-configs/", {"guardrails": settings}, format="json"
        )
    assert saved.status_code == 200, saved.json()
    push.assert_called_once()
    reloaded = auth_client.get("/agentcc/org-configs/active/").json()["result"][
        "guardrails"
    ]
    assert reloaded["fail_open"] is False and reloaded["pipeline_mode"] == new_mode
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=user.organization, is_active=True, deleted=False
    )
    wire = _build_payload(str(user.organization_id), config)["guardrails"]
    assert wire["fail_open"] is False and wire["pipeline_mode"] == new_mode
    legacy.refresh_from_db()
    assert legacy.guardrails == before


@pytest.mark.unit
@pytest.mark.parametrize("empty", [{"rules": []}, {"checks": []}, {"checks": {}}])
def test_empty_checks_still_preserve_pipeline_policy(empty):
    data = {**empty, "failOpen": "false", "pipelineMode": "sequential"}
    before = deepcopy(data)
    wire = _transform_guardrails(data)
    assert wire == {"checks": {}, "fail_open": False, "pipeline_mode": "sequential"}
    assert GuardrailConfig.model_validate(wire).fail_open is False
    assert data == before
