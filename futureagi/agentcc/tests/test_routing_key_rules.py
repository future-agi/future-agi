"""Org routing keys the gateway stores but does not run.

routing.scheduled, routing.access_groups and routing.mirror: a new or changed
value is refused with 400 unsupported_routing_key (param routing.<key>); a value
the active version already holds is saved unchanged, with a warning.
routing.conditional_routes and routing.default_model are stored with a warning
(ROUTING_KEY_NOT_ENFORCED). Every config write path applies the
rule (update-config, POST org-configs/, the single-section actions through
write_config_version_with), and activation carries the warnings without
refusing.
"""

import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.org_config_writer import write_config_version_with
from agentcc.services.routing_key_rules import (
    ROUTING_KEY_IGNORED,
    ROUTING_KEY_NOT_ENFORCED,
    UNSUPPORTED_ROUTING_KEY,
    UnsupportedRoutingKey,
    check_routing_keys,
    routing_key_warnings,
)

SCHEDULED = {"enabled": True, "max_pending_jobs": 10}
ACCESS_GROUPS = {"basic": {"models": ["openai/gpt-4o-mini"]}}
MIRROR = {"enabled": True, "rules": []}


def _cfg(**routing):
    return AgentccOrgConfig(organization_id=uuid.uuid4(), routing=routing)


def _make_config(organization, version=1, is_active=True, **fields):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=version, is_active=is_active, **fields
    )


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _params(warnings):
    return {(w["code"], w["param"]) for w in warnings if "param" in w}


class TestRule:
    @pytest.mark.parametrize(
        "key,value",
        [
            ("scheduled", SCHEDULED),
            ("access_groups", ACCESS_GROUPS),
            ("mirror", MIRROR),
        ],
    )
    def test_new_value_refused(self, key, value):
        with pytest.raises(UnsupportedRoutingKey) as refused:
            check_routing_keys(_cfg(strategy="round_robin"), _cfg(**{key: value}))
        assert refused.value.code == UNSUPPORTED_ROUTING_KEY
        assert refused.value.param == f"routing.{key}"

    def test_new_value_refused_without_an_active_version(self):
        with pytest.raises(UnsupportedRoutingKey):
            check_routing_keys(None, _cfg(mirror=MIRROR))

    def test_changed_value_refused(self):
        changed = {**SCHEDULED, "max_pending_jobs": 99}
        with pytest.raises(UnsupportedRoutingKey) as refused:
            check_routing_keys(_cfg(scheduled=SCHEDULED), _cfg(scheduled=changed))
        assert refused.value.param == "routing.scheduled"

    def test_held_value_kept_with_a_warning(self):
        held = _cfg(scheduled=SCHEDULED, access_groups=ACCESS_GROUPS, mirror=MIRROR)
        warnings = check_routing_keys(held, _cfg(**held.routing))
        assert _params(warnings) == {
            (ROUTING_KEY_IGNORED, "routing.scheduled"),
            (ROUTING_KEY_IGNORED, "routing.access_groups"),
            (ROUTING_KEY_IGNORED, "routing.mirror"),
        }
        assert all(w["message"] == "stored, not supported; ignored" for w in warnings)

    @pytest.mark.parametrize("cleared", [None, {}, []])
    def test_removing_or_clearing_is_never_refused(self, cleared):
        routing = {"mirror": cleared} if cleared is not None else {}
        assert check_routing_keys(_cfg(mirror=MIRROR), _cfg(**routing)) == []

    def test_the_five_warnings(self):
        config = _cfg(
            scheduled=SCHEDULED,
            access_groups=ACCESS_GROUPS,
            mirror=MIRROR,
            conditional_routes=[
                {"field": "model", "operator": "eq", "value": "x", "target": "y"}
            ],
            default_model="openai/gpt-4o-mini",
        )
        warnings = routing_key_warnings(config)
        assert [w["param"] for w in warnings] == [
            "routing.scheduled",
            "routing.access_groups",
            "routing.mirror",
            "routing.conditional_routes",
            "routing.default_model",
        ]
        assert [w["code"] for w in warnings[3:]] == [ROUTING_KEY_NOT_ENFORCED] * 2
        assert all(w["message"] == "stored, not enforced" for w in warnings[3:])

    def test_unset_keys_warn_nothing(self):
        assert (
            routing_key_warnings(_cfg(strategy="round_robin", default_model="")) == []
        )


@pytest.mark.integration
@pytest.mark.api
class TestEveryWritePathAppliesTheRule:
    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_config_refuses_a_new_value(self, _push, auth_client, organization):
        _make_config(organization, routing={"strategy": "round_robin"})

        response = auth_client.post(
            "/agentcc/gateways/default/update-config/",
            {"routing": {"scheduled": SCHEDULED}},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["code"] == UNSUPPORTED_ROUTING_KEY
        assert body["param"] == "routing.scheduled"
        assert _active(organization).version == 1

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_config_keeps_a_held_value_and_warns(
        self, _push, auth_client, organization
    ):
        _make_config(
            organization, routing={"strategy": "round_robin", "mirror": MIRROR}
        )

        response = auth_client.post(
            "/agentcc/gateways/default/update-config/",
            {"routing": {"default_model": "openai/gpt-4o-mini"}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        result = response.json()["result"]
        assert _params(result["warnings"]) == {
            (ROUTING_KEY_IGNORED, "routing.mirror"),
            (ROUTING_KEY_NOT_ENFORCED, "routing.default_model"),
        }
        active = _active(organization)
        assert active.version == 2 and active.routing["mirror"] == MIRROR

    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_create_refuses_a_changed_value(self, _push, auth_client, organization):
        _make_config(organization, routing={"access_groups": ACCESS_GROUPS})

        response = auth_client.post(
            "/agentcc/org-configs/",
            {"routing": {"access_groups": {"pro": {"models": ["*"]}}}},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == UNSUPPORTED_ROUTING_KEY
        assert response.json()["param"] == "routing.access_groups"
        assert _active(organization).version == 1

    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_create_keeps_a_held_value_and_warns(
        self, _push, auth_client, organization
    ):
        _make_config(organization, routing={"access_groups": ACCESS_GROUPS})

        response = auth_client.post(
            "/agentcc/org-configs/",
            {"routing": {"access_groups": ACCESS_GROUPS, "strategy": "round_robin"}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _params(response.json()["result"]["warnings"]) == {
            (ROUTING_KEY_IGNORED, "routing.access_groups")
        }

    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_activate_is_never_refused_and_warns(
        self, _push, auth_client, organization
    ):
        older = _make_config(
            organization,
            version=1,
            is_active=False,
            routing={"scheduled": SCHEDULED, "default_model": "openai/gpt-4o-mini"},
        )
        _make_config(organization, version=2, is_active=True)

        response = auth_client.post(f"/agentcc/org-configs/{older.id}/activate/")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _params(response.json()["result"]["warnings"]) == {
            (ROUTING_KEY_IGNORED, "routing.scheduled"),
            (ROUTING_KEY_NOT_ENFORCED, "routing.default_model"),
        }
        assert _active(organization).version == 1

    def test_single_section_writer_applies_the_refusal(self, organization):
        # The seven _update_org_config actions go through
        # write_config_version_with; it refuses as update-config does.
        _make_config(organization, routing={"strategy": "round_robin"})

        def updater(cfg):
            cfg.routing = {**(cfg.routing or {}), "mirror": MIRROR}

        with pytest.raises(UnsupportedRoutingKey):
            write_config_version_with(organization, updater, push=lambda _c: True)
        assert _active(organization).version == 1
