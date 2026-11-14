"""Regressions found in review of key-scoped guardrail policies."""

import uuid
from unittest.mock import patch

import pytest

from agentcc.models import AgentccAPIKey, AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.services import org_config_service
from agentcc.services.config_push import _build_payload


def _key(org):
    return AgentccAPIKey.no_workspace_objects.create(
        organization=org, gateway_key_id=f"gateway-{uuid.uuid4()}", name="Synthetic"
    )


@pytest.mark.django_db
def test_org_config_editor_keeps_saving_mask_actions(organization):
    """Settings -> Guardrails still offers Mask; its hidden policy row must save."""
    hidden = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization, name="__ui_guardrails__", scope="global"
    )
    serializer = AgentccGuardrailPolicySerializer(
        hidden,
        data={"checks": [{"name": "pii-detection", "action": "mask"}]},
        partial=True,
    )
    assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
def test_public_policies_still_refuse_mask(organization):
    serializer = AgentccGuardrailPolicySerializer(
        data={
            "name": "public",
            "checks": [{"name": "pii-detection", "action": "mask"}],
        },
        context={"organization": organization},
    )
    assert not serializer.is_valid()
    assert "checks" in serializer.errors


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad_checks",
    [
        [{"name": "pii-detection", "config": "redact"}],
        [{"name": "pii-detection", "threshold": "high"}],
        ["pii-detection"],
        "not-a-list",
    ],
)
def test_malformed_legacy_key_policy_does_not_break_the_org_payload(
    organization, bad_checks
):
    """Rows saved before validation existed are skipped, never fail the org."""
    api_key = _key(organization)
    AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="legacy",
        scope="key",
        runs_on_gateway=True,
        applied_keys=[str(api_key.id)],
        checks=bad_checks,
    )
    AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="good",
        scope="key",
        runs_on_gateway=True,
        applied_keys=[str(api_key.id)],
        checks=[{"name": "secret-detection", "action": "block"}],
    )
    payload = _build_payload(
        str(organization.id), AgentccOrgConfig(organization=organization)
    )
    names = [p["name"] for p in payload["guardrails"]["key_policies"]]
    assert "good" in names
    assert (
        payload["guardrails"]["key_policies"][names.index("good")]["checks"][
            "secret-detection"
        ]["action"]
        == "block"
    )


@pytest.mark.django_db
def test_concurrent_first_config_creation_returns_the_winner(organization):
    """A writer that loses the insert race gets the winner's config, not a 400."""
    # The winner's active config exists, but this writer's first locked read ran
    # before it was committed, so it did not see it and tries to insert its own.
    winner = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True
    )
    manager = AgentccOrgConfig.no_workspace_objects
    real_select = manager.select_for_update
    calls = {"n": 0}

    def select_for_update(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return manager.none()
        return real_select(*args, **kwargs)

    with patch.object(manager, "select_for_update", side_effect=select_for_update):
        config = org_config_service.get_or_create_org_config(organization)
    assert config.pk == winner.pk
