"""toggle-guardrail and update-guardrail on both stored guardrail shapes."""

import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig

POLICY_ID = str(uuid.uuid4())


@pytest.fixture
def gateway_id():
    return "default"


def _stored_check():
    # A checks{} entry as the Settings editor save stores it: credentials
    # replaced by the sentinel and the owning policy recorded in _policy.
    return {
        "enabled": True,
        "action": "block",
        "confidence_threshold": 0.8,
        "config": {
            "provider": "lakera",
            "api_key": "__encrypted__",
            "categories": ["pii"],
        },
        "_policy": POLICY_ID,
    }


def _stored_rule():
    return {
        "name": "lakera-guard",
        "enabled": True,
        "action": "block",
        "threshold": 0.8,
        "stage": "pre",
        "mode": "sync",
        "config": {"api_key": "__encrypted__"},
        "_policy": POLICY_ID,
    }


def _make_config(organization, guardrails):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, guardrails=guardrails
    )


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailActionsOnChecksMap:
    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_toggle_updates_the_check_in_place(
        self, mock_push, auth_client, gateway_id, organization
    ):
        _make_config(organization, {"checks": {"lakera-guard": _stored_check()}})

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/toggle-guardrail/",
            {"name": "lakera-guard", "enabled": False},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        active = _active(organization)
        assert active.version == 2
        assert active.guardrails == {
            "checks": {"lakera-guard": {**_stored_check(), "enabled": False}}
        }
        mock_push.assert_called_once()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_toggle_adds_an_absent_check_with_the_rule_defaults(
        self, mock_push, auth_client, gateway_id, organization
    ):
        _make_config(organization, {"checks": {"lakera-guard": _stored_check()}})

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/toggle-guardrail/",
            {"name": "pii-detection", "enabled": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails == {
            "checks": {
                "lakera-guard": _stored_check(),
                "pii-detection": {
                    "enabled": True,
                    "action": "block",
                    "confidence_threshold": 0.8,
                    "config": {},
                },
            }
        }
        mock_push.assert_called_once()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_applies_the_edit_dialog_payload(
        self, mock_push, auth_client, gateway_id, organization
    ):
        _make_config(organization, {"checks": {"lakera-guard": _stored_check()}})

        # EditGuardrailDialog echoes the listed entry and adds the rule fields.
        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-guardrail/",
            {
                "name": "lakera-guard",
                "config": {
                    "name": "lakera-guard",
                    **_stored_check(),
                    "action": "warn",
                    "stage": "post",
                    "mode": "sync",
                    "threshold": 0.42,
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails == {
            "checks": {
                "lakera-guard": {
                    **_stored_check(),
                    "action": "warn",
                    "confidence_threshold": 0.42,
                }
            }
        }
        mock_push.assert_called_once()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_keeps_fields_and_metadata_the_payload_does_not_set(
        self, mock_push, auth_client, gateway_id, organization
    ):
        _make_config(organization, {"checks": {"lakera-guard": _stored_check()}})

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-guardrail/",
            {
                "name": "lakera-guard",
                "config": {"action": "log", "_policy": str(uuid.uuid4())},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails == {
            "checks": {"lakera-guard": {**_stored_check(), "action": "log"}}
        }
        mock_push.assert_called_once()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_adds_an_absent_check(
        self, mock_push, auth_client, gateway_id, organization
    ):
        _make_config(organization, {"checks": {"lakera-guard": _stored_check()}})

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-guardrail/",
            {
                "name": "pii-detection",
                "config": {
                    "enabled": True,
                    "action": "flag",
                    "threshold": 0.42,
                    "stage": "pre",
                    "mode": "sync",
                    "config": {"source": "api-test"},
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails == {
            "checks": {
                "lakera-guard": _stored_check(),
                "pii-detection": {
                    "enabled": True,
                    "action": "flag",
                    "confidence_threshold": 0.42,
                    "config": {"source": "api-test"},
                },
            }
        }
        mock_push.assert_called_once()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_toggle_by_rule_name_updates_the_registry_named_check(
        self, mock_push, auth_client, gateway_id, organization
    ):
        check = {**_stored_check(), "config": {"entities": ["email"]}}
        _make_config(organization, {"checks": {"pii-detection": check}})

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/toggle-guardrail/",
            {"name": "pii-detector", "enabled": False},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails == {
            "checks": {"pii-detection": {**check, "enabled": False}}
        }

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_adds_an_absent_check_enabled_by_default(
        self, mock_push, auth_client, gateway_id, organization
    ):
        _make_config(organization, {"checks": {"lakera-guard": _stored_check()}})

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-guardrail/",
            {"name": "topic-restriction", "config": {"action": "warn"}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails["checks"]["topic-restriction"] == {
            "enabled": True,
            "action": "warn",
        }


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailActionsOnRulesList:
    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_toggle_updates_the_rule_in_place(
        self, mock_push, auth_client, gateway_id, organization
    ):
        _make_config(organization, {"rules": [_stored_rule()]})

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/toggle-guardrail/",
            {"name": "lakera-guard", "enabled": False},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails == {
            "rules": [{**_stored_rule(), "enabled": False}],
            "checks": {},
        }
        mock_push.assert_called_once()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_toggle_appends_an_absent_rule(
        self, mock_push, auth_client, gateway_id, organization
    ):
        _make_config(organization, {"rules": [_stored_rule()]})

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/toggle-guardrail/",
            {"name": "pii-detector", "enabled": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails["rules"] == [
            _stored_rule(),
            {
                "name": "pii-detector",
                "enabled": True,
                "action": "block",
                "threshold": 0.8,
                "stage": "pre",
                "mode": "sync",
                "config": {},
            },
        ]
        mock_push.assert_called_once()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_replaces_the_rule(
        self, mock_push, auth_client, gateway_id, organization
    ):
        _make_config(organization, {"rules": [_stored_rule()]})
        edited = {**_stored_rule(), "action": "warn", "threshold": 0.42}

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-guardrail/",
            {"name": "lakera-guard", "config": edited},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails == {"rules": [edited]}
        mock_push.assert_called_once()
