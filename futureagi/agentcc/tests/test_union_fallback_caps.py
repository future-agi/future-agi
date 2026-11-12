"""Union seams: locked fallback writes and durable cap revision/activation."""

import threading
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.org_config_writer import activate_config_version
from agentcc.tests.test_g8_cap_chokepoints import cap_sections
from agentcc.tests.test_g8_cap_revision_cas import PUSH, URL, active, assert_conflict
from agentcc.tests.test_model_fallback_bounds import MODEL, routing_with_chain
from agentcc.tests.test_org_config_writer_concurrency import (
    JOIN_TIMEOUT,
    _start,
    _wait_until_a_backend_waits_on_this_one,
)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
@pytest.mark.parametrize("spelling", ["model_fallbacks", "modelFallbacks"])
@pytest.mark.parametrize("candidate", [False, True])
def test_union_legacy_chain_and_enabled_cap_cas(
    auth_client, organization, spelling, candidate
):
    routing = routing_with_chain(20, spelling, candidate)
    sections = cap_sections()
    if candidate:
        # Rollout routing replaces the root section at runtime, so its stored
        # envelope must repeat the authoritative bindings before caps turn on.
        routing["rollout"]["routing"]["agents"] = deepcopy(
            sections["routing"]["agents"]
        )
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, routing=routing
    )
    with patch(PUSH, return_value=True) as push:
        first = auth_client.post(URL, sections, format="json")
        assert first.status_code == 200, first.content
        saved = active(organization)
        section = saved.routing["rollout"]["routing"] if candidate else saved.routing
        assert len(section[spelling][MODEL]) == 20
        assert saved.routing["agents"] == sections["routing"]["agents"]
        if candidate:
            assert section["agents"] == saved.routing["agents"]
        revision = saved.budgets["policy_revision"]
        assert revision > 0
        activation = deepcopy(saved.budgets["agents"]["agent-a"]["activated_at"])
        patch_body = {
            "budgets": {
                "policy_revision": revision,
                "agents": {"agent-a": {"limit": 0.03}},
            }
        }
        accepted = auth_client.post(URL, patch_body, format="json")
        assert accepted.status_code == 200, accepted.content
        latest = active(organization)
        assert latest.budgets["policy_revision"] > revision
        assert latest.budgets["agents"]["agent-a"]["activated_at"] == activation
        assert latest.routing == saved.routing
        calls = push.call_count
        count = AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization
        ).count()
        stale = deepcopy(patch_body)
        stale["routing"] = routing_with_chain(2, spelling, candidate)
        assert_conflict(
            auth_client.post(URL, stale, format="json"),
            latest.budgets["policy_revision"],
        )
        assert active(organization).pk == latest.pk
        assert active(organization).routing == latest.routing
        assert (
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=organization
            ).count()
            == count
        )
        assert push.call_count == calls


@pytest.mark.integration
@pytest.mark.django_db
def test_union_same_active_invalid_caps_with_legacy_chain(organization):
    sections = cap_sections()
    sections["budgets"]["fail_mode"] = "open"
    sections["routing"].update(routing_with_chain(20))
    stored = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, **sections
    )
    before = deepcopy((stored.budgets, stored.routing))
    push = MagicMock(return_value=True)
    with pytest.raises(OrgConfigRejected):
        activate_config_version(stored, push=push)
    stored.refresh_from_db()
    assert stored.is_active
    assert (stored.budgets, stored.routing) == before
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization).count()
        == 1
    )
    push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_union_fallback_write_racing_stale_policy_revision(auth_client, organization):
    assert connection.vendor == "postgresql"
    with patch(PUSH, return_value=True):
        response = auth_client.post(URL, cap_sections(), format="json")
    assert response.status_code == 200, response.content
    revision = active(organization).budgets["policy_revision"]
    before = AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization
    ).count()
    first_in_push = threading.Event()
    results = {}

    def hold_winner(*args, **kwargs):
        if not first_in_push.is_set():
            first_in_push.set()
            _wait_until_a_backend_waits_on_this_one()
        return True

    def write(wait=False):
        if wait:
            assert first_in_push.wait(JOIN_TIMEOUT)
        payload = {
            "budgets": {
                "policy_revision": revision,
                "agents": {"agent-a": {"limit": 0.03 if wait else 0.04}},
            },
            "routing": routing_with_chain(3 if wait else 2),
        }
        answer = auth_client.post(URL, payload, format="json")
        return answer.status_code, answer.json()

    with patch(PUSH, side_effect=hold_winner) as push:
        threads = [
            _start(write, results, "winner"),
            _start(lambda: write(True), results, "loser"),
        ]
        for thread in threads:
            thread.join(JOIN_TIMEOUT + 5)
        assert all(not thread.is_alive() for thread in threads), results
    assert results["winner"][0] == "ok", results
    assert results["winner"][1][0] == 200, results
    assert results["loser"][0] == "ok", results
    saved = active(organization)
    assert results["loser"][1] == (
        409,
        {
            "code": "cap_policy_revision_conflict",
            "policy_revision": saved.budgets["policy_revision"],
        },
    )
    assert saved.budgets["policy_revision"] > revision
    assert (
        saved.routing["model_fallbacks"][MODEL]
        == routing_with_chain(2)["model_fallbacks"][MODEL]
    )
    assert saved.budgets["agents"]["agent-a"]["limit"] == 0.04
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization).count()
        == before + 1
    )
    assert push.call_count == 1
