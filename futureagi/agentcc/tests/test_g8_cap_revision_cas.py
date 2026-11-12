"""The cap revision is a transaction precondition, including disabled policies."""

import threading
from copy import deepcopy
from unittest.mock import patch

import pytest
from django.db import connection

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.tests.test_g8_cap_chokepoints import cap_sections
from agentcc.tests.test_org_config_writer_concurrency import (
    JOIN_TIMEOUT,
    _start,
    _wait_until_a_backend_waits_on_this_one,
)

URL = "/agentcc/gateways/default/update-config/"
PUSH = "agentcc.views.gateway.AgentccGatewayViewSet._push_current_config"


def active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def assert_conflict(response, revision):
    assert response.status_code == 409, response.content
    assert response.json() == {
        "code": "cap_policy_revision_conflict",
        "policy_revision": revision,
    }


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestCapRevisionCAS:
    @pytest.mark.parametrize("initial", [None, 0])
    def test_initial_write_and_stale_write(self, auth_client, organization, initial):
        sections = cap_sections()
        if initial is not None:
            sections["budgets"]["policy_revision"] = initial
        with patch(PUSH, return_value=True) as push:
            first = auth_client.post(URL, sections, format="json")
            assert first.status_code == 200, first.content
            saved = active(organization)
            revision = saved.budgets["policy_revision"]
            assert revision > 0
            changed = deepcopy(sections)
            changed["budgets"]["agents"]["agent-a"]["limit"] = 0.01
            assert_conflict(auth_client.post(URL, changed, format="json"), revision)
            assert active(organization).pk == saved.pk
            assert push.call_count == 1
            changed["budgets"]["policy_revision"] = revision
            accepted = auth_client.post(URL, changed, format="json")
            assert accepted.status_code == 200, accepted.content
            assert active(organization).budgets["policy_revision"] > revision

    def test_nonzero_first_revision_is_a_conflict(self, auth_client, organization):
        sections = cap_sections()
        sections["budgets"]["policy_revision"] = 123
        with patch(PUSH, return_value=True) as push:
            assert_conflict(auth_client.post(URL, sections, format="json"), 0)
            push.assert_not_called()
        assert not AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization
        ).exists()

    def test_disabled_revision_and_enable_prerequisites(
        self, auth_client, organization
    ):
        with patch(PUSH, return_value=True):
            disabled = {"budgets": {"admission_caps_enabled": False}}
            response = auth_client.post(URL, disabled, format="json")
            assert response.status_code == 200, response.content
            before = active(organization)
            revision = before.budgets["policy_revision"]
            assert revision > 0
            # Stale enabling is a conflict even if prerequisites are missing.
            enable = {"budgets": {"admission_caps_enabled": True, "policy_revision": 0}}
            assert_conflict(auth_client.post(URL, enable, format="json"), revision)
            enable["budgets"]["policy_revision"] = revision
            invalid = auth_client.post(URL, enable, format="json")
            assert invalid.status_code == 400, invalid.content
            assert active(organization).pk == before.pk
            enable = cap_sections()
            enable["budgets"]["policy_revision"] = revision
            response = auth_client.post(URL, enable, format="json")
            assert response.status_code == 200, response.content
            enabled = active(organization).budgets["policy_revision"]
            assert enabled > revision
            disabled["budgets"]["policy_revision"] = enabled
            response = auth_client.post(URL, disabled, format="json")
            assert response.status_code == 200, response.content
            latest = active(organization).budgets["policy_revision"]
            assert latest > enabled
            assert_conflict(auth_client.post(URL, enable, format="json"), latest)

    @pytest.mark.parametrize("revision", [True, "1", 1.0, -1, None])
    def test_revision_is_a_strict_nonnegative_integer(self, auth_client, revision):
        sections = cap_sections()
        sections["budgets"]["policy_revision"] = revision
        with patch(PUSH, return_value=True) as push:
            response = auth_client.post(URL, sections, format="json")
        assert response.status_code == 400, response.content
        push.assert_not_called()

    def test_alias_and_clear_cannot_bypass_cas(self, auth_client, organization):
        with patch(PUSH, return_value=True):
            response = auth_client.post(URL, cap_sections(), format="json")
            assert response.status_code == 200, response.content
            revision = active(organization).budgets["policy_revision"]
            for budgets in ({"agents": None}, {"admissionCapsEnabled": False}):
                assert_conflict(
                    auth_client.post(URL, {"budgets": budgets}, format="json"), revision
                )
            response = auth_client.post(
                URL,
                {
                    "budgets": {
                        "policyRevision": revision,
                        "admissionCapsEnabled": False,
                    }
                },
                format="json",
            )
            assert response.status_code == 200, response.content
            assert active(organization).budgets["admission_caps_enabled"] is False

    def test_replacement_clear_retains_disabled_revision(
        self, auth_client, organization
    ):
        with patch(PUSH, return_value=True):
            first = auth_client.post(URL, cap_sections(), format="json")
        assert first.status_code == 200, first.content
        revision = active(organization).budgets["policy_revision"]
        with patch(
            "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
            return_value=True,
        ) as push:
            for budgets in (None, {}):
                assert_conflict(
                    auth_client.post(
                        "/agentcc/org-configs/", {"budgets": budgets}, format="json"
                    ),
                    revision,
                )
            push.assert_not_called()
            response = auth_client.post(
                "/agentcc/org-configs/",
                {"budgets": {"policy_revision": revision}},
                format="json",
            )
        assert response.status_code == 200, response.content
        budgets = active(organization).budgets
        assert budgets["policy_revision"] > revision
        assert not budgets.get("admission_caps_enabled", False)
        assert "agents" not in budgets

    def test_unrelated_writes_need_no_revision(self, auth_client, organization):
        with patch(PUSH, return_value=True):
            for payload in (
                {"budgets": {"enabled": True}},
                cap_sections(),
                {"cache": {"enabled": False}},
                {"budgets": {"org_limit": 10}},
            ):
                response = auth_client.post(URL, payload, format="json")
                assert response.status_code == 200, response.content
            assert active(organization).budgets["org_limit"] == 10


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "policy,scope",
    [
        ("absent", "budgets"),
        ("enabled", "budgets"),
        ("disabled", "budgets"),
        ("enabled", "routing"),
        ("disabled", "routing"),
    ],
)
def test_concurrent_same_revision_has_one_winner(
    auth_client, organization, policy, scope
):
    # Independent real Postgres connections, with pg_locks proving overlap.
    assert connection.vendor == "postgresql"
    revision = 0
    if policy != "absent":
        initial = cap_sections()
        initial["budgets"]["admission_caps_enabled"] = policy == "enabled"
        with patch(PUSH, return_value=True):
            response = auth_client.post(URL, initial, format="json")
        assert response.status_code == 200, response.content
        revision = active(organization).budgets["policy_revision"]
    before = AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization
    ).count()
    first_in_push = threading.Event()
    results = {}

    def hold_winner(*args, **kwargs):
        if first_in_push.is_set():
            return True
        first_in_push.set()
        _wait_until_a_backend_waits_on_this_one()
        return True

    def write(limit, wait=False):
        if wait:
            assert first_in_push.wait(JOIN_TIMEOUT)
        payload = cap_sections()
        payload["budgets"]["policy_revision"] = revision
        payload["budgets"]["agents"]["agent-a"]["limit"] = limit
        if scope == "routing":
            payload = {
                "budgets": {"policy_revision": revision},
                "routing": {"agents": {"max_run_usd": limit}},
            }
        response = auth_client.post(URL, payload, format="json")
        return response.status_code, response.json()

    with patch(PUSH, side_effect=hold_winner) as push:
        threads = [
            _start(lambda: write(0.03), results, "winner"),
            _start(lambda: write(0.04, wait=True), results, "loser"),
        ]
        for thread in threads:
            thread.join(JOIN_TIMEOUT + 5)
        assert all(not thread.is_alive() for thread in threads), results
    assert results["winner"][0] == "ok", results
    assert results["winner"][1][0] == 200, results
    saved = active(organization)
    assert (
        results["winner"][1][1]["result"]["policy_revision"]
        == saved.budgets["policy_revision"]
    )
    assert results["loser"] == (
        "ok",
        (
            409,
            {
                "code": "cap_policy_revision_conflict",
                "policy_revision": saved.budgets["policy_revision"],
            },
        ),
    ), results
    assert saved.budgets["policy_revision"] > revision
    if scope == "routing":
        assert saved.routing["agents"]["max_run_usd"] == 0.03
    else:
        assert saved.budgets["agents"]["agent-a"]["limit"] == 0.03
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization).count()
        == before + 1
    )
    push.assert_called_once()
