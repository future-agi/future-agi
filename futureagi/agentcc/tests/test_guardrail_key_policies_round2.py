"""Second review round of key-scoped guardrail policies."""

import uuid
from unittest.mock import patch

import pytest

from agentcc.models import AgentccAPIKey, AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.services.config_push import _build_payload
from agentcc.services.log_ingestion import _dispatch_webhook_events, _event_types

URL = "/agentcc/guardrail-policies/"


def _key(org):
    return AgentccAPIKey.no_workspace_objects.create(
        organization=org, gateway_key_id=f"gateway-{uuid.uuid4()}", name="Synthetic"
    )


def _active_config(org, **fields):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, **fields
    )


def _key_policy_body(key_id, **overrides):
    return {
        "name": "payments-rollout",
        "scope": "key",
        "mode": "monitor",
        "applied_keys": [str(key_id)],
        "checks": [{"name": "secret-detection", "enabled": True, "action": "block"}],
        **overrides,
    }


# Item 4: the shape the old policy sync stored.
LEGACY_GUARDRAILS = {"enabled": False, "checks": []}


@pytest.mark.django_db
def test_legacy_disabled_guardrails_shape_is_accepted_by_the_contract(organization):
    config = AgentccOrgConfig(organization=organization, guardrails=LEGACY_GUARDRAILS)
    with patch("agentcc.services.config_push._assemble_providers", return_value={}):
        guardrails = _build_payload(str(organization.id), config)["guardrails"]
    assert "enabled" not in guardrails
    assert guardrails["checks"] == {}


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["create", "toggle", "delete"])
def test_key_policy_saves_work_on_an_org_with_the_legacy_shape(
    auth_client, organization, change
):
    api_key = _key(organization)
    _active_config(organization, guardrails=dict(LEGACY_GUARDRAILS))
    created = auth_client.post(URL, _key_policy_body(api_key.id), format="json")
    assert created.status_code == 200, created.json()
    policy_id = created.json()["result"]["id"]
    if change == "toggle":
        response = auth_client.patch(
            f"{URL}{policy_id}/", {"is_active": False}, format="json"
        )
        assert response.status_code == 200, response.json()
    elif change == "delete":
        response = auth_client.delete(f"{URL}{policy_id}/")
        assert response.status_code == 200, response.json()


# Item 5: values other writers of the org's checks may have stored.
@pytest.mark.django_db
@pytest.mark.parametrize(
    "check",
    [
        {"enabled": True, "action": "block", "confidence_threshold": 50},
        {"enabled": True, "action": "redact"},
        {"enabled": "yes", "action": "block"},
    ],
)
@patch(
    "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
    return_value=True,
)
def test_settings_save_is_not_refused_for_a_stored_org_check(
    _push, auth_client, organization, check
):
    guardrails = {"checks": {"pii-detection": check}}
    _active_config(organization, guardrails=guardrails)
    # The settings page re-sends the whole guardrails section with any change.
    response = auth_client.post(
        "/agentcc/org-configs/",
        {"guardrails": guardrails, "routing": {"strategy": "round_robin"}},
        format="json",
    )
    assert response.status_code == 200, response.json()


@pytest.mark.django_db
def test_policy_api_still_refuses_out_of_range_thresholds(auth_client, organization):
    data = _key_policy_body(_key(organization).id)
    data["checks"][0]["threshold"] = 50
    response = auth_client.post(URL, data, format="json")
    assert response.status_code == 400
    assert "checks" in response.json()["result"]


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["threshold", "confidence_threshold"])
@patch("agentcc.views.gateway.push_org_config", return_value=True)
def test_update_guardrail_refuses_a_threshold_outside_0_to_1(
    _push, auth_client, organization, field
):
    _active_config(organization, guardrails={"checks": {}})
    response = auth_client.post(
        "/agentcc/gateways/default/update-guardrail/",
        {"name": "pii-detection", "config": {"action": "block", field: 50}},
        format="json",
    )
    assert response.status_code == 400, response.json()
    assert field in str(response.json())
    ok = auth_client.post(
        "/agentcc/gateways/default/update-guardrail/",
        {"name": "pii-detection", "config": {"action": "block", field: 0.5}},
        format="json",
    )
    assert ok.status_code == 200, ok.json()


# Item 7: scope=key rows saved before the gateway ran key policies.
@pytest.mark.django_db
def test_key_rows_saved_before_this_change_stay_dormant(auth_client, organization):
    api_key = _key(organization)
    dormant = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="dormant",
        scope="key",
        applied_keys=[str(api_key.id)],
        checks=[{"name": "secret-detection", "action": "block"}],
    )
    config = _active_config(organization, guardrails={"checks": {}})

    def key_policy_names():
        payload = _build_payload(str(organization.id), config)
        return [p["name"] for p in payload["guardrails"].get("key_policies", [])]

    assert key_policy_names() == []

    # A policy saved through the API runs at once.
    created = auth_client.post(URL, _key_policy_body(api_key.id), format="json")
    assert created.status_code == 200, created.json()
    assert key_policy_names() == ["payments-rollout"]

    # The dormant row runs once an admin saves it.
    saved = auth_client.patch(f"{URL}{dormant.id}/", {"mode": "enforce"}, format="json")
    assert saved.status_code == 200, saved.json()
    assert sorted(key_policy_names()) == ["dormant", "payments-rollout"]


@pytest.mark.django_db
def test_apply_with_key_ids_makes_the_policy_run(auth_client, organization):
    api_key = _key(organization)
    policy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="applied",
        scope="global",
        checks=[{"name": "secret-detection", "action": "block"}],
    )
    config = _active_config(organization, guardrails={"checks": {}})
    response = auth_client.post(
        f"{URL}{policy.id}/apply/", {"key_ids": [str(api_key.id)]}, format="json"
    )
    assert response.status_code == 200, response.json()
    payload = _build_payload(str(organization.id), config)
    assert [p["name"] for p in payload["guardrails"]["key_policies"]] == ["applied"]


# P3: a change that leaves scope and keys alone is not refused for a pairing
# an older version saved (apply with project_ids kept applied_keys).
@pytest.mark.django_db
def test_toggle_is_not_refused_for_a_legacy_scope_and_keys_pairing(
    auth_client, organization
):
    api_key = _key(organization)
    legacy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="legacy-project",
        scope="project",
        applied_keys=[str(api_key.id)],
        checks=[{"name": "secret-detection", "action": "log"}],
    )
    _active_config(organization, guardrails={"checks": {}})
    response = auth_client.patch(
        f"{URL}{legacy.id}/", {"is_active": False}, format="json"
    )
    assert response.status_code == 200, response.json()
    # Changing the scope or keys still applies the rule.
    response = auth_client.patch(
        f"{URL}{legacy.id}/", {"scope": "global"}, format="json"
    )
    assert response.status_code == 400


# Item 8: a Monitor-only match is recorded but is not a guardrail event.
MONITOR_RESULT = {
    "name": "secret-detection",
    "mode": "monitor",
    "outcome": "would_block",
}
ENFORCE_RESULT = {"name": "pii-detection", "mode": "enforce", "outcome": "blocked"}
ORG_RESULT = {"name": "pii-detection", "action": "block"}


@pytest.mark.parametrize(
    ("results", "raised"),
    [
        ([MONITOR_RESULT], False),
        ([MONITOR_RESULT, MONITOR_RESULT], False),
        ([MONITOR_RESULT, ENFORCE_RESULT], True),
        ([ORG_RESULT], True),
        (None, True),
        ([], True),
    ],
)
def test_guardrail_event_only_when_a_guardrail_acted(results, raised):
    entry = {"status_code": 200, "guardrail_triggered": True}
    if results is not None:
        entry["guardrail_results"] = results
    assert ("guardrail.triggered" in _event_types(entry)) is raised


def test_untriggered_entry_raises_no_guardrail_event():
    entry = {"status_code": 200, "guardrail_results": [ENFORCE_RESULT]}
    assert "guardrail.triggered" not in _event_types(entry)


@pytest.mark.django_db
def test_webhook_payload_tells_a_monitor_match_from_an_enforcement(organization):
    from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent

    AgentccWebhook.no_workspace_objects.create(
        organization=organization,
        name="synthetic",
        url="https://hooks.example.invalid/agentcc",
        events=["request.completed", "guardrail.triggered"],
        is_active=True,
    )
    logs = [
        {
            "request_id": "monitored",
            "status_code": 200,
            "guardrail_triggered": True,
            "guardrail_results": [MONITOR_RESULT],
        },
        {
            "request_id": "blocked",
            "status_code": 403,
            "guardrail_triggered": True,
            "guardrail_results": [ENFORCE_RESULT],
        },
    ]
    with patch("agentcc.services.webhook_delivery.deliver_webhook_events"):
        _dispatch_webhook_events(organization, logs)
    events = {
        (e.payload["request_id"], e.event_type): e.payload
        for e in AgentccWebhookEvent.no_workspace_objects.filter(
            organization=organization
        )
    }
    assert ("monitored", "guardrail.triggered") not in events
    assert ("blocked", "guardrail.triggered") in events
    monitored = events[("monitored", "request.completed")]
    # Round 3: the payload's flag matches the stored request log (and its API,
    # exports and analytics); guardrail_monitored tells the two apart.
    assert monitored["guardrail_triggered"] is True
    assert monitored["guardrail_monitored"] is True
    blocked = events[("blocked", "request.completed")]
    assert blocked["guardrail_triggered"] is True
    assert blocked["guardrail_monitored"] is False
