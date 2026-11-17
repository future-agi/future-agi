"""Saved-version rollback has a new activation order, not a new spend epoch."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agentcc.services.config_push import OrgConfigRejected, _build_payload
from agentcc.services.org_config_writer import (
    activate_config_version,
    write_config_version,
)
from agentcc.tests.test_g8_cap_chokepoints import cap_sections


@pytest.mark.integration
@pytest.mark.django_db
def test_cap_revision_survives_rollback_and_deleted_versions(organization):
    first, _ = write_config_version(organization, cap_sections(), push=lambda _: True)
    second, _ = write_config_version(organization, {}, push=lambda _: True)
    revision = second.budgets.get("policy_revision", 0)
    assert revision > first.budgets.get("policy_revision", 0) > 0
    restored, _ = activate_config_version(first, push=lambda _: True)
    assert restored.version == first.version < second.version
    assert restored.budgets["policy_revision"] > revision
    assert restored.budgets["agents"] == first.budgets["agents"]
    revision = restored.budgets["policy_revision"]
    second.delete()
    forged = cap_sections()
    forged["budgets"]["policy_revision"] = 1
    with pytest.raises(OrgConfigRejected) as conflict:
        write_config_version(organization, forged, push=lambda _: True)
    assert conflict.value.code == "cap_policy_revision_conflict"
    assert conflict.value.policy_revision == revision
    forged["budgets"]["policy_revision"] = revision
    latest, _ = write_config_version(organization, forged, push=lambda _: True)
    assert latest.budgets["policy_revision"] > revision
    again, warnings = activate_config_version(latest, push=lambda _: True)
    assert again.budgets["policy_revision"] == latest.budgets["policy_revision"]
    assert not warnings


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_cap_http_activation_exports_chat_sequence(auth_client):
    payloads = []

    def capture(_org_id, config):
        payloads.append(
            _build_payload(
                str(config.organization_id), config, with_upstream_keys=False
            )
        )
        return True

    sections = cap_sections()
    sections["budgets"]["agents"]["agent-a"]["limit"] = 1.0
    sections["budgets"]["key_ids"]["key-a"]["limit"] = 1.0
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        side_effect=capture,
    ):
        first = auth_client.post("/agentcc/org-configs/", sections, format="json")
        assert first.status_code == 200, first.content
        saved = first.json()["result"]
        sections["budgets"]["policy_revision"] = saved["budgets"]["policy_revision"]
        sections["budgets"]["agents"]["agent-a"]["limit"] = 0.5
        second = auth_client.post("/agentcc/org-configs/", sections, format="json")
        assert second.status_code == 200, second.content
        restored = auth_client.post(
            f"/agentcc/org-configs/{saved['id']}/activate/", {}, format="json"
        )
        assert restored.status_code == 200, restored.content
    assert len(payloads) == 3
    revisions = [p["budgets"].get("policy_revision", 0) for p in payloads]
    assert 0 < revisions[0] < revisions[1] < revisions[2]
    assert payloads[2]["meta"]["version"] == payloads[0]["meta"]["version"]
    assert (
        len({p["budgets"]["agents"]["agent-a"]["activated_at"] for p in payloads}) == 1
    )
    if output := os.environ.get("G8_CONFIG_ACTIVATION_FIXTURE"):
        Path(output).write_text(json.dumps(payloads) + "\n")
