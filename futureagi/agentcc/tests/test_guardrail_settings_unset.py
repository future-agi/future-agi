"""Fail Open and the pipeline mode are pushed only when an organization saved them.

v1.58.0's gateway ran every organization's guardrail checks with the node's own
fail_open (on by default) and per-rule modes, whatever Django pushed. The merged
gateway reads the organization's values, so an organization that never touched
the switch must not be pushed an explicit false: it would be refused whenever a
check does not finish. Release-lead's ruling (11-14): the default for a
never-saved organization stays what v1.58.0 did. Django leaves such a setting
out of what it stores and pushes, and the gateway falls back to its node. A
saved value is stored and pushed as saved, and an ambiguous or unknown value is
still refused (400).
"""

from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import _transform_guardrails

URL = "/agentcc/gateways/default/update-config/"
CHECKS = {"keyword-blocklist": {"enabled": True, "action": "block"}}
SETTINGS = ("fail_open", "pipeline_mode")


def _make_config(organization, **fields):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, **fields
    )


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _unset(section):
    return [key for key in SETTINGS if key in section]


@pytest.mark.unit
def test_a_section_with_neither_setting_pushes_neither():
    for section in ({"checks": dict(CHECKS)}, {"checks": {}}, {"rules": []}):
        assert _unset(_transform_guardrails(section)) == [], section


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
class TestGuardrailSettingsUnset:
    def test_an_unrelated_guardrails_save_leaves_both_settings_unset(
        self, _push, auth_client, organization
    ):
        _make_config(organization, guardrails={"checks": dict(CHECKS)})

        response = auth_client.post(
            URL,
            {"guardrails": {"checks": {"keyword-blocklist": {"enabled": False}}}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _active(organization).guardrails
        assert _unset(stored) == []
        assert _unset(_transform_guardrails(stored)) == []

    @pytest.mark.parametrize("value", [True, False])
    def test_a_saved_fail_open_is_stored_and_pushed_as_saved(
        self, _push, auth_client, organization, value
    ):
        _make_config(organization, guardrails={"checks": dict(CHECKS)})

        response = auth_client.post(
            URL, {"guardrails": {"fail_open": value}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _active(organization).guardrails
        pushed = _transform_guardrails(stored)
        assert stored["fail_open"] is value and pushed["fail_open"] is value
        assert "pipeline_mode" not in stored and "pipeline_mode" not in pushed

    def test_a_saved_pipeline_mode_is_stored_and_pushed_as_saved(
        self, _push, auth_client, organization
    ):
        _make_config(organization, guardrails={"checks": dict(CHECKS)})

        response = auth_client.post(
            URL, {"guardrails": {"pipeline_mode": "sequential"}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _transform_guardrails(_active(organization).guardrails)
        assert pushed["pipeline_mode"] == "sequential"
        assert "fail_open" not in pushed

    def test_a_saved_false_survives_an_unrelated_save(
        self, _push, auth_client, organization
    ):
        _make_config(
            organization, guardrails={"checks": dict(CHECKS), "fail_open": False}
        )

        response = auth_client.post(
            URL,
            {"guardrails": {"checks": {"keyword-blocklist": {"enabled": False}}}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _active(organization).guardrails
        assert stored["fail_open"] is False
        assert _transform_guardrails(stored)["fail_open"] is False

    def test_an_ambiguous_fail_open_is_still_refused(
        self, _push, auth_client, organization
    ):
        _make_config(organization, guardrails={"checks": dict(CHECKS)})

        response = auth_client.post(
            URL, {"guardrails": {"fail_open": True, "failOpen": False}}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "conflicting fail_open aliases" in response.json()["message"]
        assert _active(organization).guardrails == {"checks": CHECKS}
