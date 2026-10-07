"""
Per-check guardrail stage in the org config pushed to the gateway (#3162).

The control plane forwards a ``stage`` of "post" or "both" for every shape the
org config stores guardrails in, and drops anything else so the gateway runs
the guardrail at its own stage. "pre" is dropped too: it is the default, and a
gateway that predates per-check stages rejects a check carrying ``stage``.

Settings > Org Config saves a checks dict that also carries the editor's own
keys, which the gateway contract forbids; only the contract's fields of it are
pushed. A config with no checks pushes only its pipeline settings.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from accounts.models.user import OrgApiKey
from agentcc.contracts.gateway_admin import OrgConfig as GatewayOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import _build_payload, _transform_guardrails
from agentcc.services.guardrail_sync import sync_guardrail_policies

_MISSING = object()

STAGE_CASES = [
    pytest.param("post", "post", id="post"),
    pytest.param("both", "both", id="both"),
    # The dashboard stores "pre" on every rule by default; pushing it would
    # break config pushes to older gateways for no change in behaviour.
    pytest.param("pre", None, id="pre-is-the-default"),
    pytest.param(" PRE ", None, id="pre-any-case"),
    pytest.param(" Post ", "post", id="mixed-case-padded"),
    pytest.param("BOTH", "both", id="upper-case"),
    pytest.param("during", None, id="unknown"),
    pytest.param("", None, id="empty"),
    pytest.param(None, None, id="null"),
    pytest.param(1, None, id="not-a-string"),
    pytest.param(_MISSING, None, id="missing"),
]


def _check_fields(stage):
    fields = {
        "enabled": True,
        "action": "block",
        "config": {"provider": "lakera", "api_key": "lk-test"},
    }
    if stage is not _MISSING:
        fields["stage"] = stage
    return fields


def _rules_shape(stage):
    # Dashboard edits and toggles (views/gateway.py) store a rules list.
    rule = {"name": "lakera-guard", "threshold": 0.5, **_check_fields(stage)}
    return {"rules": [rule]}


def _policy_list_shape(stage):
    # guardrail_sync stores the merged policy checks as a list.
    check = {"name": "lakera-guard", "threshold": 0.5, **_check_fields(stage)}
    return {"checks": [check]}


def _checks_dict_shape(stage):
    # Org config saves may store checks keyed by name.
    check = {"confidence_threshold": 0.5, **_check_fields(stage)}
    return {"checks": {"lakera-guard": check}}


def _settings_editor_shape(stage):
    # Settings > Org Config saves the checks it built from the stored rules next
    # to those rules, with its own keys on each check and a model check's
    # provider beside its config.
    check = {
        **_check_fields(stage),
        "confidence_threshold": 0.5,
        "mode": "sync",
        "provider": "lakera",
        "config": {"api_key": "lk-test"},
        "_originalName": "lakera-guard",
    }
    return {**_rules_shape(stage), "checks": {"lakera-guard": check}}


SHAPES = [
    pytest.param(_rules_shape, id="rules"),
    pytest.param(_policy_list_shape, id="policy-list"),
    pytest.param(_checks_dict_shape, id="checks-dict"),
    pytest.param(_settings_editor_shape, id="settings-editor"),
]

NO_CHECKS = [
    # Guardrails page > Config tab saves an org without checks as an empty
    # rules list.
    pytest.param(
        {"pipeline_mode": "sequential", "rules": []},
        {"checks": {}, "fail_open": False, "pipeline_mode": "sequential"},
        id="empty-rules",
    ),
    # Policy sync once the org's last global policy is removed.
    pytest.param(
        {"enabled": False, "checks": []},
        {"checks": {}, "fail_open": False, "pipeline_mode": "parallel"},
        id="no-policies",
    ),
]


@pytest.mark.unit
class TestTransformGuardrailsStage:
    @pytest.mark.parametrize("build", SHAPES)
    @pytest.mark.parametrize("raw_stage,expected", STAGE_CASES)
    def test_forwards_only_valid_stage(self, build, raw_stage, expected):
        check = _transform_guardrails(build(raw_stage))["checks"]["lakera-guard"]

        if expected is None:
            assert "stage" not in check
        else:
            assert check["stage"] == expected

    @pytest.mark.parametrize("build", SHAPES)
    def test_leaves_the_rest_of_the_check_unchanged(self, build):
        check = _transform_guardrails(build("post"))["checks"]["lakera-guard"]

        assert check == {
            "enabled": True,
            "action": "block",
            "confidence_threshold": 0.5,
            "config": {"provider": "lakera", "api_key": "lk-test"},
            "stage": "post",
        }

    def test_does_not_mutate_the_stored_config(self):
        guardrails = _checks_dict_shape(" Post ")

        _transform_guardrails(guardrails)

        assert guardrails["checks"]["lakera-guard"]["stage"] == " Post "


@pytest.mark.unit
class TestGuardrailStagePayloadContract:
    @pytest.mark.parametrize("build", SHAPES)
    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_payload_with_stage_validates_against_gateway_contract(
        self, _mock_providers, build
    ):
        payload = _build_payload("org-123", AgentccOrgConfig(guardrails=build("Both")))

        contract = GatewayOrgConfig.model_validate(payload)
        assert contract.guardrails.checks["lakera-guard"].stage == "both"
        assert payload["guardrails"]["checks"]["lakera-guard"]["stage"] == "both"

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_non_string_stage_in_checks_dict_does_not_break_the_push(
        self, _mock_providers
    ):
        # The contract types stage as a string, so passing this through would
        # make the whole org payload invalid.
        payload = _build_payload(
            "org-123", AgentccOrgConfig(guardrails=_checks_dict_shape(["post"]))
        )

        GatewayOrgConfig.model_validate(payload)
        assert "stage" not in payload["guardrails"]["checks"]["lakera-guard"]


@pytest.mark.unit
class TestSettingsEditorChecks:
    def test_pushes_only_the_contract_keys(self):
        guardrails = {
            **_settings_editor_shape("post"),
            "enabled": True,
            "pipeline_mode": "sequential",
            "fail_open": True,
            "timeout_ms": 3000,
        }
        guardrails["checks"]["lakera-guard"]["phase"] = "pre"

        assert _transform_guardrails(guardrails) == {
            "checks": {
                "lakera-guard": {
                    "enabled": True,
                    "action": "block",
                    "confidence_threshold": 0.5,
                    "config": {"provider": "lakera", "api_key": "lk-test"},
                    "stage": "post",
                }
            },
            "pipeline_mode": "sequential",
            "fail_open": True,
            "timeout_ms": 3000,
        }

    @pytest.mark.parametrize(
        "check,expected_config",
        [
            # A check switched on in the editor without being configured.
            pytest.param(
                {"provider": "lakera"}, {"provider": "lakera"}, id="no-config"
            ),
            pytest.param(
                {"provider": "lakera", "config": {"api_key": "lk-test"}},
                {"provider": "lakera", "api_key": "lk-test"},
                id="config-without-provider",
            ),
            pytest.param(
                {"provider": "lakera", "config": {"provider": "custom"}},
                {"provider": "custom"},
                id="config-provider-wins",
            ),
        ],
    )
    def test_moves_the_provider_into_config(self, check, expected_config):
        guardrails = {"checks": {"lakera-guard": {"enabled": True, **check}}}

        pushed = _transform_guardrails(guardrails)["checks"]["lakera-guard"]

        assert pushed == {"enabled": True, "config": expected_config}

    @pytest.mark.parametrize(
        "thresholds,expected",
        [
            # A check the editor converted from a policy's checks list.
            pytest.param({"threshold": 0.9}, 0.9, id="policy-threshold"),
            # Saving the check's dialog writes confidence_threshold.
            pytest.param(
                {"threshold": 0.9, "confidence_threshold": 0.5},
                0.5,
                id="dialog-threshold-wins",
            ),
            pytest.param(
                {"threshold": 0.9, "confidenceThreshold": 0.3},
                0.3,
                id="camel-case-wins",
            ),
        ],
    )
    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_keeps_the_threshold_of_a_converted_policy_check(
        self, _mock_providers, thresholds, expected
    ):
        guardrails = {
            "checks": {
                "lakera-guard": {
                    "name": "lakera-guard",
                    "enabled": True,
                    "action": "block",
                    "config": {"provider": "lakera"},
                    "_originalName": "lakera-guard",
                    **thresholds,
                }
            }
        }

        payload = _build_payload("org-123", AgentccOrgConfig(guardrails=guardrails))

        check = payload["guardrails"]["checks"]["lakera-guard"]
        assert check["confidence_threshold"] == expected

    def test_reads_the_pipeline_settings_as_the_contract_does(self):
        # The contract accepts either spelling and prefers snake_case.
        guardrails = {
            **_settings_editor_shape("post"),
            "pipelineMode": "sequential",
            "timeoutMs": 3000,
            "fail_open": True,
            "failOpen": False,
        }

        result = _transform_guardrails(guardrails)

        assert {k: v for k, v in result.items() if k != "checks"} == {
            "pipeline_mode": "sequential",
            "timeout_ms": 3000,
            "fail_open": True,
        }


@pytest.mark.unit
class TestGuardrailsWithoutChecks:
    @pytest.mark.parametrize("guardrails,expected", NO_CHECKS)
    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_pushes_only_the_pipeline_settings(
        self, _mock_providers, guardrails, expected
    ):
        payload = _build_payload("org-123", AgentccOrgConfig(guardrails=guardrails))

        assert payload["guardrails"] == expected


@pytest.fixture
def gateway_client():
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


@pytest.fixture
def admin_client():
    token = "agentcc-admin-secret"
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", token):
        yield client


def _pushed_checks(gateway_client):
    _, payload = gateway_client.set_org_config.call_args.args
    GatewayOrgConfig.model_validate(payload)
    return payload["guardrails"]["checks"]


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailStagePush:
    def test_update_guardrail_pushes_stage(self, auth_client, gateway_client):
        response = auth_client.post(
            "/agentcc/gateways/default/update-guardrail/",
            {
                "name": "lakera-guard",
                "config": {
                    "enabled": True,
                    "action": "block",
                    "stage": "post",
                    "threshold": 0.5,
                    "mode": "sync",
                    "config": {"api_key": "lk-test"},
                },
            },
            format="json",
        )

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        check = _pushed_checks(gateway_client)["lakera-guard"]
        assert check["stage"] == "post"
        assert check["config"]["provider"] == "lakera"

    def test_toggle_with_default_stage_pushes_no_stage(
        self, auth_client, gateway_client
    ):
        # The toggle stores a new rule with the dashboard default, "pre".
        response = auth_client.post(
            "/agentcc/gateways/default/toggle-guardrail/",
            {"name": "lakera-guard", "enabled": True},
            format="json",
        )

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert "stage" not in _pushed_checks(gateway_client)["lakera-guard"]

    def test_org_config_save_pushes_stage(self, auth_client, gateway_client):
        # The Rules tab saves a new version; its checks round-trip through the
        # guardrail policy serializer, which encrypts the credentials.
        response = auth_client.post(
            "/agentcc/org-configs/",
            {"guardrails": _rules_shape("both")},
            format="json",
        )

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        check = _pushed_checks(gateway_client)["lakera-guard"]
        assert check["stage"] == "both"
        assert check["config"]["api_key"] == "lk-test"

    def test_policy_sync_pushes_stage(self, user, gateway_client):
        org = user.organization
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org, version=1, is_active=True
        )
        AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=org,
            name="response-checks",
            scope=AgentccGuardrailPolicy.SCOPE_GLOBAL,
            is_active=True,
            checks=[
                {
                    "name": "lakera-guard",
                    "action": "block",
                    "stage": "Both",
                    "config": {"api_key": "lk-test"},
                },
                {"name": "pii-detector", "action": "block", "stage": "later"},
            ],
        )

        assert sync_guardrail_policies(org) is True

        checks = _pushed_checks(gateway_client)
        assert checks["lakera-guard"]["stage"] == "both"
        assert "stage" not in checks["pii-detection"]

    def test_bulk_sync_returns_stage(self, admin_client, organization):
        # The gateway loads every active org config from here on startup.
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails=_rules_shape("Post"),
        )

        response = admin_client.get("/agentcc/org-configs/bulk/")

        assert response.status_code == 200, response.json()
        payload = response.json()["result"][str(organization.id)]
        GatewayOrgConfig.model_validate(payload)
        assert payload["guardrails"]["checks"]["lakera-guard"]["stage"] == "post"


@pytest.mark.integration
@pytest.mark.api
class TestSettingsEditorGuardrailsPush:
    def test_save_syncs_to_the_gateway(self, auth_client, gateway_client):
        response = auth_client.post(
            "/agentcc/org-configs/",
            {"guardrails": _settings_editor_shape("post")},
            format="json",
        )

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        # The api key round-trips through the policy's encrypted credentials.
        assert _pushed_checks(gateway_client) == {
            "lakera-guard": {
                "enabled": True,
                "action": "block",
                "confidence_threshold": 0.5,
                "config": {"provider": "lakera", "api_key": "lk-test"},
                "stage": "post",
            }
        }

    def test_bulk_sync_serves_the_org(self, admin_client, organization):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails=_settings_editor_shape("post"),
        )

        response = admin_client.get("/agentcc/org-configs/bulk/")

        assert response.status_code == 200, response.json()
        payload = response.json()["result"][str(organization.id)]
        GatewayOrgConfig.model_validate(payload)
        check = payload["guardrails"]["checks"]["lakera-guard"]
        assert check["config"] == {"provider": "lakera", "api_key": "lk-test"}
        assert check["stage"] == "post"

    def test_future_agi_eval_check_gets_its_provider_and_the_org_key(
        self, organization
    ):
        key = OrgApiKey.no_workspace_objects.create(
            organization=organization, type="system"
        )
        guardrails = {
            "checks": {
                "futureagi-eval": {
                    "enabled": True,
                    "action": "block",
                    "mode": "sync",
                    "provider": "futureagi",
                    "config": {"eval_ids": [76, "15"]},
                    "_originalName": "futureagi-eval",
                }
            }
        }

        payload = _build_payload(
            str(organization.id), AgentccOrgConfig(guardrails=guardrails)
        )

        assert payload["guardrails"]["checks"]["futureagi-eval"] == {
            "enabled": True,
            "action": "block",
            "config": {
                "provider": "futureagi",
                "eval_ids": ["76", "15"],
                "eval_id": "76",
                "call_type": "protect",
                "base_url": settings.BASE_URL,
                "api_key": key.api_key,
                "secret_key": key.secret_key,
            },
        }


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailsWithoutChecksPush:
    def test_config_tab_save_without_checks_syncs(self, auth_client, gateway_client):
        response = auth_client.post(
            "/agentcc/org-configs/",
            {"guardrails": {"pipeline_mode": "sequential", "rules": []}},
            format="json",
        )

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert _pushed_checks(gateway_client) == {}

    def test_removing_the_last_policy_clears_the_org_checks(self, user, gateway_client):
        org = user.organization
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org, version=1, is_active=True
        )
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=org,
            name="request-checks",
            scope=AgentccGuardrailPolicy.SCOPE_GLOBAL,
            is_active=True,
            checks=[{"name": "pii-detector", "action": "block"}],
        )
        assert sync_guardrail_policies(org) is True
        assert list(_pushed_checks(gateway_client)) == ["pii-detection"]

        policy.is_active = False
        policy.save()

        assert sync_guardrail_policies(org) is True
        assert _pushed_checks(gateway_client) == {}

    @pytest.mark.parametrize("guardrails,expected", NO_CHECKS)
    def test_bulk_sync_serves_the_org(
        self, admin_client, organization, guardrails, expected
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails=guardrails,
        )

        response = admin_client.get("/agentcc/org-configs/bulk/")

        assert response.status_code == 200, response.json()
        payload = response.json()["result"][str(organization.id)]
        assert payload["guardrails"] == expected
