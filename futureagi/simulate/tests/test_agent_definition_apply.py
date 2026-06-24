"""Platform-side apply: optimised config -> new AgentVersion (TH-5642)."""

import pytest

from simulate.services.agent_definition_apply import (
    apply_config_as_new_agent_version,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def agent_def(db):
    from accounts.models import Organization
    from simulate.models import AgentDefinition

    org = Organization.objects.create(name="t5642-apply-org")
    return AgentDefinition.objects.create(
        agent_name="selfhosted-livekit",
        agent_type="voice",
        provider="livekit_bridge",
        description="self-hosted agent under optimisation",
        inbound=True,
        organization=org,
    )


def test_apply_creates_new_active_version_with_config(agent_def):
    from simulate.models import AgentVersion

    # seed a v1
    AgentVersion.objects.create(
        agent_definition=agent_def,
        organization_id=agent_def.organization_id,
        status="active",
        commit_message="v1",
    )
    cfg = {
        "type": "llm",
        "name": "winner",
        "instructions": "Be concise and answer hours questions directly.",
        "model": "gpt-4o",
        "temperature": 0.2,
    }
    new_version, applied = apply_config_as_new_agent_version(agent_def, cfg)

    assert set(applied) == {"instructions", "model", "temperature"}
    # non-destructive: a brand new, higher version number
    assert new_version.version_number >= 2
    snap = new_version.configuration_snapshot
    assert snap["system_prompt"] == cfg["instructions"]
    assert snap["model"] == "gpt-4o"
    assert snap["temperature"] == 0.2
    # the whole winning config is retained, identity keys stripped
    assert snap["optimised_config"]["instructions"] == cfg["instructions"]
    assert "name" not in snap["optimised_config"]


def test_apply_works_with_no_prior_version(agent_def):
    new_version, applied = apply_config_as_new_agent_version(
        agent_def, {"instructions": "x", "model": "gpt-4o-mini"}
    )
    assert new_version.version_number >= 1
    assert new_version.configuration_snapshot["system_prompt"] == "x"
    assert set(applied) == {"instructions", "model"}
