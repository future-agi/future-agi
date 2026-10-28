"""Guardrail retry controls survive persistence and the gateway wire boundary."""

import copy
import uuid
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from agentcc.contracts.gateway_admin import OrgConfig as GatewayOrgConfig
from agentcc.guardrail_options import (
    GuardrailOptionsError,
    guardrail_stage,
    validate_guardrail_collection,
    validate_guardrail_options,
)
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.serializers.org_config import AgentccOrgConfigWriteSerializer
from agentcc.services.config_push import (
    OrgConfigRejected,
    _transform_guardrails,
    validate_org_config,
)


def reflection_check(**config):
    return {
        "name": "keyword-blocklist",
        "action": "block",
        "stage": "post",
        "mode": "sync",
        "config": {"on_block": "reflect", "words": ["blocked"], **config},
    }


@pytest.mark.parametrize("attempts", [1, 1.0, 2, 5, 5.0])
def test_accepts_integer_valued_retry_limits(attempts):
    validate_guardrail_options(reflection_check(reflect_max_attempts=attempts))


@pytest.mark.parametrize(
    "attempts",
    [True, False, 0, -1, 6, 1.5, "2", None, [], {}, float("nan"), float("inf")],
)
def test_rejects_invalid_retry_limits_without_echoing_values(attempts):
    with pytest.raises(GuardrailOptionsError) as rejected:
        validate_guardrail_options(reflection_check(reflect_max_attempts=attempts))
    assert (
        str(rejected.value)
        == "config.reflect_max_attempts must be an integer from 1 to 5"
    )


@pytest.mark.parametrize(
    "change, message",
    [
        ({"stage": "pre"}, "post-response"),
        ({"action": "warn"}, "action='block'"),
        ({"mode": "async"}, "synchronous"),
        ({"config": {"on_block": "retry"}}, "config.on_block"),
        ({"config": {"stage": "both"}}, "config.stage"),
        ({"config": {"reflect_debug": "true"}}, "config.reflect_debug"),
        ({"config": []}, "JSON object"),
        ({"name": []}, "name must be a string"),
    ],
)
def test_rejects_incompatible_controls(change, message):
    with pytest.raises(GuardrailOptionsError, match=message):
        validate_guardrail_options({**reflection_check(), **change})


@pytest.mark.parametrize("name", ["hallucination-detection", "data-leakage-prevention"])
def test_legacy_top_level_stage_does_not_move_response_only_checks(name):
    check = {"name": name, "stage": "pre", "config": {}}
    validate_guardrail_options(check)
    assert guardrail_stage(check) == "post"
    assert (
        "stage"
        not in _transform_guardrails({"rules": [check]})["checks"][name]["config"]
    )
    with pytest.raises(GuardrailOptionsError, match="requires config.stage='post'"):
        validate_guardrail_options({**check, "config": {"stage": "pre"}})


def test_explicit_stage_wins_and_provider_mode_is_not_reserved():
    check = reflection_check(stage="post", mode="provider-specific-mode")
    check["stage"] = "pre"
    validate_guardrail_options(check)
    assert guardrail_stage(check) == "post"
    wire = _transform_guardrails({"rules": [check]})["checks"][check["name"]]
    assert wire["config"]["mode"] == "provider-specific-mode"
    assert wire["config"]["stage"] == "post"


@pytest.mark.parametrize("shape", ["rules", "list", "map"])
def test_retry_controls_cross_existing_strict_wire_contract(shape):
    check = reflection_check(reflect_max_attempts=3, reflect_debug=True)
    source = (
        {"rules": [check]}
        if shape == "rules"
        else {"checks": [check]}
        if shape == "list"
        else {
            "checks": {check["name"]: {k: v for k, v in check.items() if k != "name"}}
        }
    )
    before = copy.deepcopy(source)
    wire = _transform_guardrails(source)
    config = wire["checks"]["keyword-blocklist"]["config"]
    assert config == {
        "stage": "post",
        "on_block": "reflect",
        "reflect_max_attempts": 3,
        "reflect_debug": True,
        "words": ["blocked"],
    }
    GatewayOrgConfig.model_validate({"guardrails": wire})
    assert source == before


@pytest.mark.parametrize("value", [{"rules": {}}, {"checks": 1}, {"checks": [None]}])
def test_invalid_collection_shapes_are_validation_errors(value):
    with pytest.raises(GuardrailOptionsError):
        validate_guardrail_collection(value)


@pytest.mark.parametrize(
    "serializer_class",
    [AgentccGuardrailPolicySerializer, AgentccOrgConfigWriteSerializer],
)
def test_both_save_boundaries_reject_invalid_retry_limits(serializer_class):
    checks = [reflection_check(reflect_max_attempts=True)]
    data = (
        {"checks": checks}
        if serializer_class is AgentccGuardrailPolicySerializer
        else {"guardrails": {"rules": checks}}
    )
    serializer = serializer_class(data=data, partial=True)
    assert not serializer.is_valid()
    assert "integer from 1 to 5" in str(serializer.errors)


@patch("agentcc.services.config_push._assemble_providers", return_value={})
def test_activation_validates_retry_options_even_for_existing_rows(_providers):
    config = AgentccOrgConfig(
        organization_id=uuid.uuid4(),
        guardrails={"rules": [reflection_check(reflect_max_attempts=6)]},
    )
    with pytest.raises(OrgConfigRejected, match="integer from 1 to 5"):
        validate_org_config(config)


@pytest.mark.parametrize(
    "name",
    [
        "prompt-injection",
        "injection-detector",
        "topic-restriction",
        "language-detection",
        "system-prompt-protection",
    ],
)
@pytest.mark.parametrize("config", [{"stage": "post"}, {"on_block": "reflect"}])
def test_request_only_checks_cannot_claim_response_validation(name, config):
    check = {"name": name, "stage": "post", "action": "block", "config": config}
    with pytest.raises(GuardrailOptionsError, match="request stage"):
        validate_guardrail_options(check)


def test_reflect_map_supplies_explicit_default_block_action():
    check = reflection_check()
    del check["action"]
    name = check.pop("name")
    wire = _transform_guardrails({"checks": {name: check}})
    assert wire["checks"][name]["action"] == "block"
    assert wire["checks"][name]["config"]["stage"] == "post"
    legacy = _transform_guardrails(
        {"checks": {name: {"config": {"words": ["blocked"]}}}}
    )
    assert "action" not in legacy["checks"][name]


@pytest.mark.parametrize(
    "name, provider",
    [
        ("tool-permissions", "tool_permissions"),
        ("custom-tools", "tool_permissions"),
        ("custom-tools", "tool_permission"),
    ],
)
def test_tool_permission_request_scope_cannot_claim_response_validation(name, provider):
    check = reflection_check(stage="post", provider=provider, apply_to="request")
    check["name"] = name
    with pytest.raises(GuardrailOptionsError, match="request stage"):
        validate_guardrail_options(check)
    check["config"]["apply_to"] = "both"
    validate_guardrail_options(check)


@pytest.mark.integration
@pytest.mark.api
class TestRetryConfigPersistence:
    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_invalid_save_keeps_previous_active_version(
        self, push, auth_client, organization
    ):
        current = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        response = auth_client.post(
            "/agentcc/org-configs/",
            {"guardrails": {"rules": [reflection_check(reflect_max_attempts=0)]}},
            format="json",
        )
        assert response.status_code == 400
        current.refresh_from_db()
        assert current.is_active
        assert (
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=organization
            ).count()
            == 1
        )
        push.assert_not_called()

    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_save_and_read_preserve_retry_controls_and_encrypted_credentials(
        self, push, auth_client, organization, settings
    ):
        settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
        check = reflection_check(
            stage="post", reflect_max_attempts=2, reflect_debug=True
        )
        check["config"]["api_key"] = "synthetic-guardrail-credential"
        response = auth_client.post(
            "/agentcc/org-configs/", {"guardrails": {"rules": [check]}}, format="json"
        )
        assert response.status_code == 200
        saved = response.json()["result"]["guardrails"]["rules"][0]
        assert saved["config"]["api_key"] == "__encrypted__"
        assert saved["config"]["reflect_max_attempts"] == 2
        assert saved["config"]["on_block"] == "reflect"
        assert saved["config"]["stage"] == "post"
        push.assert_called_once()
        config = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True
        )
        wire = _transform_guardrails(config.guardrails)
        assert (
            wire["checks"]["keyword-blocklist"]["config"]["api_key"]
            == "synthetic-guardrail-credential"
        )
        assert config.guardrails["rules"][0]["config"]["api_key"] == "__encrypted__"
        active = auth_client.get("/agentcc/org-configs/active/")
        assert active.status_code == 200
        assert (
            active.json()["result"]["guardrails"]["rules"][0]["config"]
            == saved["config"]
        )
