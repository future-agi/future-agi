"""Tenant failure policy survives serialization, persistence, and gateway push."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from agentcc.contracts.gateway_admin import GuardrailConfig
from agentcc.models import AgentccOrgConfig
from agentcc.serializers.org_config import AgentccOrgConfigWriteSerializer
from agentcc.services.config_push import _build_payload, _transform_guardrails


@pytest.mark.unit
@pytest.mark.parametrize("shape", ["rules", "checks-list", "checks-map"])
@pytest.mark.parametrize("key", ["fail_open", "failOpen"])
@pytest.mark.parametrize(
    "value,expected",
    [(False, False), (True, True), ("false", False), ("true", True), (None, False)],
)
def test_transform_preserves_explicit_boolean(shape, key, value, expected):
    check = {"name": "test-hook", "enabled": True, "action": "block", "config": {}}
    data = {key: value, "pipelineMode": "sequential"}
    if shape == "checks-map":
        data["checks"] = {
            "test-hook": {"enabled": True, "action": "block", "config": {}}
        }
    else:
        data["rules" if shape == "rules" else "checks"] = [check]
    before = deepcopy(data)
    wire = _transform_guardrails(data)
    assert wire["fail_open"] is expected
    assert wire["pipeline_mode"] == "sequential"
    assert GuardrailConfig.model_validate(wire).fail_open is expected
    assert "failOpen" not in wire and "pipelineMode" not in wire
    assert data == before


@pytest.mark.unit
@pytest.mark.parametrize(
    "guardrails",
    [
        {"fail_open": "not-a-boolean"},
        {"fail_open": []},
        {"fail_open": {}},
        {"fail_open": True, "failOpen": "false"},
        {"pipeline_mode": "unknown"},
        {"pipeline_mode": "parallel", "pipelineMode": "sequential"},
    ],
)
def test_invalid_pipeline_settings_are_refused(guardrails):
    serializer = AgentccOrgConfigWriteSerializer(data={"guardrails": guardrails})
    assert not serializer.is_valid()
    assert "guardrails" in serializer.errors


@pytest.mark.unit
def test_omitted_failure_policy_requires_opt_in():
    serializer = AgentccOrgConfigWriteSerializer(data={"guardrails": {"checks": {}}})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["guardrails"]["fail_open"] is False
    assert serializer.validated_data["guardrails"]["pipeline_mode"] == "parallel"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "value,expected", [("false", False), ("true", True), (False, False), (True, True)]
)
def test_settings_save_and_gateway_payload(auth_client, user, value, expected):
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    ) as push:
        response = auth_client.post(
            "/agentcc/org-configs/",
            {
                "guardrails": {
                    "fail_open": value,
                    "pipeline_mode": "parallel",
                    "checks": {},
                },
                "change_description": "synthetic boolean contract",
            },
            format="json",
        )
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["guardrails"]["fail_open"] is expected
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=user.organization, is_active=True, deleted=False
    )
    assert config.guardrails["fail_open"] is expected
    payload = _build_payload(str(user.organization_id), config)
    assert payload["guardrails"]["fail_open"] is expected
    push.assert_called_once()


@pytest.mark.integration
@pytest.mark.api
def test_invalid_setting_has_no_version_or_push(auth_client, user):
    before = AgentccOrgConfig.no_workspace_objects.filter(
        organization=user.organization
    ).count()
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
    ) as push:
        response = auth_client.post(
            "/agentcc/org-configs/",
            {"guardrails": {"fail_open": "unrecognized"}},
            format="json",
        )
    assert response.status_code == 400, response.json()
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=user.organization
        ).count()
        == before
    )
    push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "old_mode,new_mode", [("parallel", "sequential"), ("sequential", "parallel")]
)
def test_legacy_settings_hydrate_edit_save_and_push(
    auth_client, user, old_mode, new_mode
):
    legacy = AgentccOrgConfig.no_workspace_objects.create(
        organization=user.organization,
        version=1,
        is_active=True,
        guardrails={"failOpen": True, "pipelineMode": old_mode, "checks": {}},
    )
    before = deepcopy(legacy.guardrails)
    hydrated = auth_client.get("/agentcc/org-configs/active/")
    assert hydrated.status_code == 200
    settings = hydrated.json()["result"]["guardrails"]
    assert "failOpen" not in settings and "pipelineMode" not in settings
    assert settings["fail_open"] is True
    assert settings["pipeline_mode"] == old_mode
    settings.update(fail_open=False, pipeline_mode=new_mode)
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    ) as push:
        saved = auth_client.post(
            "/agentcc/org-configs/", {"guardrails": settings}, format="json"
        )
    assert saved.status_code == 200, saved.json()
    push.assert_called_once()
    reloaded = auth_client.get("/agentcc/org-configs/active/").json()["result"][
        "guardrails"
    ]
    assert reloaded["fail_open"] is False and reloaded["pipeline_mode"] == new_mode
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=user.organization, is_active=True, deleted=False
    )
    wire = _build_payload(str(user.organization_id), config)["guardrails"]
    assert wire["fail_open"] is False and wire["pipeline_mode"] == new_mode
    legacy.refresh_from_db()
    assert legacy.guardrails == before


@pytest.mark.unit
@pytest.mark.parametrize("empty", [{"rules": []}, {"checks": []}, {"checks": {}}])
def test_empty_checks_still_preserve_pipeline_policy(empty):
    data = {**empty, "failOpen": "false", "pipelineMode": "sequential"}
    before = deepcopy(data)
    wire = _transform_guardrails(data)
    assert wire == {"checks": {}, "fail_open": False, "pipeline_mode": "sequential"}
    assert GuardrailConfig.model_validate(wire).fail_open is False
    assert data == before


# Saved versions can hold both spellings of a setting: earlier dashboard edits
# added the canonical key next to the camelCase one. Each case is
# (saved settings, fail_open read back, pipeline_mode read back).
SAVED_WITH_BOTH_SPELLINGS = [
    ({"fail_open": True, "failOpen": False}, False, "parallel"),
    ({"fail_open": False, "failOpen": True}, False, "parallel"),
    ({"fail_open": True, "failOpen": "not-a-boolean"}, False, "parallel"),
    ({"fail_open": "true", "failOpen": True}, True, "parallel"),
    ({"pipeline_mode": "sequential", "pipelineMode": "parallel"}, False, "sequential"),
    ({"pipeline_mode": "parallel", "pipelineMode": "sequential"}, False, "parallel"),
    ({"pipeline_mode": "unknown"}, False, "parallel"),
    ({"pipeline_mode": None, "pipelineMode": "Sequential"}, False, "sequential"),
]


def _saved_version(organization, guardrails, version=1, is_active=True):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=version,
        is_active=is_active,
        guardrails={**guardrails, "checks": {}},
    )


def _assert_canonical(settings, fail_open, pipeline_mode):
    assert "failOpen" not in settings and "pipelineMode" not in settings
    assert settings["fail_open"] is fail_open
    assert settings["pipeline_mode"] == pipeline_mode


@pytest.mark.unit
@pytest.mark.parametrize("fail_open", [None, True, False, "false", "not-a-boolean"])
@pytest.mark.parametrize("fail_open_alias", [None, True, "false"])
@pytest.mark.parametrize("mode", [None, "sequential", " Sequential "])
@pytest.mark.parametrize("mode_alias", [None, "parallel", "unknown"])
def test_saved_reader_matches_write_reader_where_writes_are_accepted(
    fail_open, fail_open_alias, mode, mode_alias
):
    from agentcc.guardrail_settings import pipeline_settings, saved_pipeline_settings

    data = {
        "fail_open": fail_open,
        "failOpen": fail_open_alias,
        "pipeline_mode": mode,
        "pipelineMode": mode_alias,
    }
    saved = saved_pipeline_settings(data)
    assert saved["fail_open"] in (True, False)
    assert saved["pipeline_mode"] in ("parallel", "sequential")
    try:
        expected = pipeline_settings(data)
    except ValueError:
        return  # writes refuse this document; reads above still succeed
    assert saved == expected


@pytest.mark.unit
@pytest.mark.parametrize("shape", ["rules", "checks-list", "checks-map"])
@pytest.mark.parametrize("saved,fail_open,pipeline_mode", SAVED_WITH_BOTH_SPELLINGS)
def test_gateway_payload_reads_saved_settings_with_both_spellings(
    shape, saved, fail_open, pipeline_mode
):
    check = {"name": "test-hook", "enabled": True, "action": "block", "config": {}}
    data = dict(saved)
    if shape == "checks-map":
        data["checks"] = {
            "test-hook": {"enabled": True, "action": "block", "config": {}}
        }
    else:
        data["rules" if shape == "rules" else "checks"] = [check]
    before = deepcopy(data)
    wire = _transform_guardrails(data)
    _assert_canonical(wire, fail_open, pipeline_mode)
    assert GuardrailConfig.model_validate(wire).fail_open is fail_open
    assert data == before


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("saved,fail_open,pipeline_mode", SAVED_WITH_BOTH_SPELLINGS)
def test_active_version_with_both_spellings_loads(
    auth_client, user, saved, fail_open, pipeline_mode
):
    config = _saved_version(user.organization, saved)
    before = deepcopy(config.guardrails)

    active = auth_client.get("/agentcc/org-configs/active/")
    assert active.status_code == 200, active.json()
    _assert_canonical(active.json()["result"]["guardrails"], fail_open, pipeline_mode)

    with patch("agentcc.views.gateway.get_gateway_client"):
        gateway = auth_client.get("/agentcc/gateways/default/config/")
    assert gateway.status_code == 200, gateway.json()
    _assert_canonical(gateway.json()["result"]["guardrails"], fail_open, pipeline_mode)

    wire = _build_payload(str(user.organization_id), config)["guardrails"]
    _assert_canonical(wire, fail_open, pipeline_mode)
    config.refresh_from_db()
    assert config.guardrails == before


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("saved,fail_open,pipeline_mode", SAVED_WITH_BOTH_SPELLINGS)
def test_history_with_both_spellings_lists(
    auth_client, user, saved, fail_open, pipeline_mode
):
    _saved_version(user.organization, saved, version=1, is_active=False)
    _saved_version(user.organization, {"fail_open": True}, version=2)

    history = auth_client.get("/agentcc/org-configs/")
    assert history.status_code == 200, history.json()
    by_version = {row["version"]: row for row in history.json()["result"]}
    assert set(by_version) == {1, 2}
    _assert_canonical(by_version[1]["guardrails"], fail_open, pipeline_mode)
    _assert_canonical(by_version[2]["guardrails"], True, "parallel")


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("saved,fail_open,pipeline_mode", SAVED_WITH_BOTH_SPELLINGS)
def test_older_version_with_both_spellings_opens(
    auth_client, user, saved, fail_open, pipeline_mode
):
    old = _saved_version(user.organization, saved, version=1, is_active=False)
    _saved_version(user.organization, {"fail_open": True}, version=2)

    detail = auth_client.get(f"/agentcc/org-configs/{old.id}/")
    assert detail.status_code == 200, detail.json()
    _assert_canonical(detail.json()["result"]["guardrails"], fail_open, pipeline_mode)


@pytest.mark.integration
@pytest.mark.api
def test_older_version_with_both_spellings_can_be_activated(auth_client, user):
    old = _saved_version(
        user.organization,
        {"fail_open": True, "failOpen": False, "pipelineMode": "sequential"},
        version=1,
        is_active=False,
    )
    _saved_version(user.organization, {"fail_open": True}, version=2)
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    ) as push:
        response = auth_client.post(f"/agentcc/org-configs/{old.id}/activate/")
    assert response.status_code == 200, response.json()
    _assert_canonical(response.json()["result"]["guardrails"], False, "sequential")
    push.assert_called_once()
    old.refresh_from_db()
    assert old.is_active
    wire = _build_payload(str(user.organization_id), old)["guardrails"]
    _assert_canonical(wire, False, "sequential")


@pytest.mark.integration
@pytest.mark.api
def test_loaded_settings_with_both_spellings_save_back_canonical(auth_client, user):
    legacy = _saved_version(
        user.organization,
        {"fail_open": True, "failOpen": False, "pipelineMode": "sequential"},
    )
    before = deepcopy(legacy.guardrails)
    settings = auth_client.get("/agentcc/org-configs/active/").json()["result"][
        "guardrails"
    ]
    settings["fail_open"] = True
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    ) as push:
        saved = auth_client.post(
            "/agentcc/org-configs/", {"guardrails": settings}, format="json"
        )
    assert saved.status_code == 200, saved.json()
    push.assert_called_once()
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=user.organization, is_active=True, deleted=False
    )
    assert config.version == 2
    _assert_canonical(config.guardrails, True, "sequential")
    wire = _build_payload(str(user.organization_id), config)["guardrails"]
    _assert_canonical(wire, True, "sequential")
    legacy.refresh_from_db()
    assert legacy.guardrails == before


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
def test_other_settings_save_while_active_version_has_both_spellings(
    push, auth_client, user
):
    _saved_version(user.organization, {"fail_open": False, "failOpen": True})
    response = auth_client.post(
        "/agentcc/gateways/default/update-config/",
        {"routing": {"strategy": "round_robin"}},
        format="json",
    )
    assert response.status_code == 200, response.json()
    push.assert_called_once()
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=user.organization, is_active=True, deleted=False
    )
    assert config.version == 2
    assert config.routing == {"strategy": "round_robin"}
    wire = _build_payload(str(user.organization_id), config)["guardrails"]
    _assert_canonical(wire, False, "parallel")


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
def test_guardrail_patch_on_saved_spellings_is_stored_canonical(
    push, auth_client, user
):
    _saved_version(user.organization, {"failOpen": False, "pipelineMode": "sequential"})
    response = auth_client.post(
        "/agentcc/gateways/default/update-config/",
        {"guardrails": {"fail_open": True}},
        format="json",
    )
    assert response.status_code == 200, response.json()
    push.assert_called_once()
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=user.organization, is_active=True, deleted=False
    )
    assert config.guardrails == {
        "checks": {},
        "fail_open": True,
        "pipeline_mode": "sequential",
    }


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "saved,patch_value,fail_open,pipeline_mode",
    [
        ({}, {"failOpen": True}, True, "parallel"),
        ({}, {"pipelineMode": "sequential"}, False, "sequential"),
        ({"fail_open": False}, {"failOpen": True}, True, "parallel"),
        (
            {"pipeline_mode": "sequential"},
            {"pipelineMode": "parallel"},
            False,
            "parallel",
        ),
        ({"failOpen": True}, {"pipelineMode": "sequential"}, True, "sequential"),
    ],
)
@patch("agentcc.views.gateway.push_org_config", return_value=True)
def test_guardrail_patch_with_one_spelling_replaces_the_saved_value(
    push, auth_client, user, saved, patch_value, fail_open, pipeline_mode
):
    _saved_version(user.organization, saved)
    response = auth_client.post(
        "/agentcc/gateways/default/update-config/",
        {"guardrails": patch_value},
        format="json",
    )
    assert response.status_code == 200, response.json()
    push.assert_called_once()
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=user.organization, is_active=True, deleted=False
    )
    _assert_canonical(config.guardrails, fail_open, pipeline_mode)
    wire = _build_payload(str(user.organization_id), config)["guardrails"]
    _assert_canonical(wire, fail_open, pipeline_mode)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "guardrails",
    [
        {"fail_open": False, "failOpen": True},
        {"pipeline_mode": "parallel", "pipelineMode": "sequential"},
        {"fail_open": "not-a-boolean"},
        {"pipeline_mode": "unknown"},
    ],
)
def test_new_ambiguous_settings_are_still_refused(auth_client, user, guardrails):
    _saved_version(user.organization, {"fail_open": False})
    with (
        patch(
            "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
        ) as push,
        patch("agentcc.views.gateway.push_org_config") as patch_push,
    ):
        created = auth_client.post(
            "/agentcc/org-configs/", {"guardrails": guardrails}, format="json"
        )
        patched = auth_client.post(
            "/agentcc/gateways/default/update-config/",
            {"guardrails": guardrails},
            format="json",
        )
    assert created.status_code == 400, created.json()
    assert patched.status_code == 400, patched.json()
    push.assert_not_called()
    patch_push.assert_not_called()
    assert list(
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=user.organization
        ).values_list("version", "is_active")
    ) == [(1, True)]
