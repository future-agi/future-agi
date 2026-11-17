"""Restoring a config version never changes whether daily caps are in force
without the person choosing it, and is never refused for daily caps.

Daily caps can't be turned on in this release. Restoring a version with caps
on over one with caps off saves a new version, a copy of it with caps off; the
stored version keeps its value. Restoring over caps on succeeds as it is. The
decision uses the two versions only: the gateway is never asked for a ledger.
"""

from unittest.mock import patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.contracts import CapActivationConflictSerializer
from agentcc.services import cap_ledger_status
from agentcc.services.cap_ledger_status import AdmissionCapLedgerUnavailable

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.django_db]

CREATE_URL = "/agentcc/org-configs/"
VERSION_PUSH = (
    "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
)
OFF_SENTENCE = (
    "Daily caps can't be turned on in this release. "
    "Budgets in Gateway > Budgets still limit spend."
)


def sections(enabled, *, alias=False):
    budgets = {
        "enabled": True,
        "admission_caps_enabled": enabled,
        "fail_mode": "closed",
        "no_max_tokens": "clamp",
        "agents": {"agent-a": {"limit": 0.05, "period": "daily"}},
        "key_ids": {"key-a": {"limit": 0.05, "period": "daily"}},
    }
    if alias:
        budgets["admissionCapsEnabled"] = budgets.pop("admission_caps_enabled")
    return {
        "budgets": budgets,
        "routing": {"agents": {"key_bindings": {"key-a": "agent-a"}}},
    }


def saved(org, *, enabled, active, version, alias=False):
    config = AgentccOrgConfig(
        organization=org,
        version=version,
        is_active=active,
        **sections(enabled, alias=alias),
    )
    config.disable_auto_workspace_assignment = True
    config.save()
    return config


def versions(org):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=org)
        .order_by("version")
        .values_list("version", "is_active")
    )


def caps_on(config):
    return cap_ledger_status.caps_enabled(config)


@pytest.fixture(autouse=True)
def no_ledger_probe(monkeypatch):
    """A restore decides from the two versions: a fresh probe, which a decision
    on the ledger would need, fails the test. Reads for display stay cached."""

    def status(_org_id, *, fresh=False, require_autocommit=False):
        if fresh:
            raise AssertionError("a restore must not ask the gateway for its ledger")
        return "absent"

    monkeypatch.setattr(cap_ledger_status, "get_ledger_status", status)


def restore(client, target):
    with patch(VERSION_PUSH, return_value=True) as push:
        response = client.post(f"{CREATE_URL}{target.pk}/activate/", {}, format="json")
    return response, push


@pytest.mark.parametrize("alias", [False, True], ids=["canonical", "stored-alias"])
def test_caps_on_version_over_caps_off_restores_as_new_version_with_caps_off(
    auth_client, organization, alias
):
    saved(organization, enabled=False, active=True, version=1)
    target = saved(organization, enabled=True, active=False, version=2, alias=alias)
    stored_budgets = dict(target.budgets)

    response, push = restore(auth_client, target)

    assert response.status_code == 200, response.content
    assert versions(organization) == [(1, False), (2, False), (3, True)]
    restored = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    assert not caps_on(restored)
    assert restored.budgets["admission_caps_enabled"] is False
    assert "admissionCapsEnabled" not in restored.budgets
    assert restored.change_description == "Restored from version 2 with daily caps off"
    # Everything else is the restored version's.
    assert restored.budgets["agents"]["agent-a"]["limit"] == 0.05
    assert restored.routing == target.routing
    # The stored version keeps its value.
    target.refresh_from_db()
    assert target.budgets == stored_budgets
    assert caps_on(target)
    push.assert_called_once()


def test_caps_on_version_with_no_active_version_restores_with_caps_off(
    auth_client, organization
):
    target = saved(organization, enabled=True, active=False, version=1)

    response, push = restore(auth_client, target)

    assert response.status_code == 200, response.content
    assert versions(organization) == [(1, False), (2, True)]
    restored = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    assert not caps_on(restored)
    assert restored.change_description == "Restored from version 1 with daily caps off"
    push.assert_called_once()


def test_caps_on_version_over_caps_on_restores_as_it_is(auth_client, organization):
    saved(organization, enabled=True, active=True, version=1)
    target = saved(organization, enabled=True, active=False, version=2)

    response, push = restore(auth_client, target)

    assert response.status_code == 200, response.content
    assert versions(organization) == [(1, False), (2, True)]
    target.refresh_from_db()
    assert target.is_active
    assert caps_on(target)
    push.assert_called_once()


def test_caps_off_version_over_caps_on_restores_and_stops_caps(
    auth_client, organization
):
    saved(organization, enabled=True, active=True, version=1)
    target = saved(organization, enabled=False, active=False, version=2)

    response, push = restore(auth_client, target)

    assert response.status_code == 200, response.content
    assert versions(organization) == [(1, False), (2, True)]
    target.refresh_from_db()
    assert target.is_active
    assert not caps_on(target)
    push.assert_called_once()


@pytest.mark.parametrize(
    "active_on,target_on",
    [(False, True), (True, True), (True, False), (False, False)],
    ids=["off-to-on", "on-to-on", "on-to-off", "off-to-off"],
)
def test_no_restore_answers_409_or_mentions_redis(
    auth_client, organization, active_on, target_on
):
    saved(organization, enabled=active_on, active=True, version=1)
    target = saved(organization, enabled=target_on, active=False, version=2)

    response, _ = restore(auth_client, target)

    assert response.status_code == 200, response.content
    assert b"admission_cap_ledger_unavailable" not in response.content
    assert b"redis" not in response.content.lower()


@pytest.mark.unit
@pytest.mark.parametrize("status", ["absent", "unavailable", "unknown"])
def test_save_refusal_says_caps_cant_be_turned_on(status):
    refusal = AdmissionCapLedgerUnavailable(status)
    body = refusal.response().data
    assert body["message"] == OFF_SENTENCE
    assert str(refusal) == OFF_SENTENCE
    assert "redis" not in body["message"].lower()
    assert body["code"] == "admission_cap_ledger_unavailable"
    assert body["ledger_status"] == status


@pytest.mark.unit
def test_refusal_contract_lists_only_the_sentence():
    choices = CapActivationConflictSerializer().fields["message"].choices
    assert list(choices) == [OFF_SENTENCE]
