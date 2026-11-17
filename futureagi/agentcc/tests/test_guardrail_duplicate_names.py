"""Name-based edits must not silently choose among stored duplicate checks.

There is no remove-guardrail/reorder endpoint in the pinned contract. Removing
an explicit occurrence means replacing the rules/checks array via update-config,
or PATCHing the owning policy by id with the intended checks array.
"""

import copy
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.permissions import GATEWAY_ADMIN_MESSAGE
from agentcc.services import config_push
from agentcc.services.guardrail_sync import sync_guardrail_policies
from agentcc.tests.test_agentcc_permissions import _user_with_level
from agentcc.views import gateway as gateway_views
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level

GATEWAY = "/agentcc/gateways/default"
CHECK = {"name": "keyword-blocklist", "enabled": True, "action": "block"}
WRITES = [
    ("update-guardrail", {"config": {"action": "log"}}),
    ("toggle-guardrail", {"enabled": False}),
]


@pytest.fixture
def gateway():
    client = MagicMock()
    with (
        patch.object(config_push, "get_gateway_client", return_value=client),
        patch.object(gateway_views, "get_gateway_client", return_value=client),
    ):
        yield client


def _stored(org, shape, count=2, **extra):
    checks = [{**CHECK, "threshold": i / 10} for i in range(count)]
    config = AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        guardrails={shape: checks, **extra},
    )
    return config


def _bytes(org):
    """Read the DB representation, not a Python reserialization of the JSON.

    Include version activation, timestamps and policy credentials so a rejected
    request cannot silently rewrite policy state or create a config version.
    """
    result = []
    with connection.cursor() as cursor:
        for model in (AgentccOrgConfig, AgentccGuardrailPolicy):
            table = connection.ops.quote_name(model._meta.db_table)
            cursor.execute(
                f"SELECT row_to_json(t)::text FROM {table} t "
                "WHERE organization_id = %s ORDER BY id",
                [org.id],
            )
            result.append(tuple(row[0].encode() for row in cursor.fetchall()))
    return tuple(result)


def _post(client, action, data):
    return client.post(f"{GATEWAY}/{action}/", data, format="json")


@pytest.mark.integration
@pytest.mark.api
class TestDuplicateNames:
    @pytest.mark.parametrize("shape", ["rules", "checks"])
    @pytest.mark.parametrize("count", [2, 3])
    @pytest.mark.parametrize("action,fields", WRITES)
    def test_named_write_rejected_without_changing_stored_bytes(
        self,
        auth_client,
        organization,
        gateway,
        shape,
        count,
        action,
        fields,
    ):
        _stored(organization, shape, count)
        before = _bytes(organization)
        response = _post(auth_client, action, {"name": CHECK["name"], **fields})
        assert response.status_code == 400, response.json()
        assert response.json()["code"] == "guardrail_name_ambiguous"
        assert str(count) in response.json()["message"]
        assert CHECK["name"] in response.json()["message"]
        assert _bytes(organization) == before
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("action,fields", WRITES)
    def test_duplicate_in_policy_is_rejected_even_when_sync_deduplicates_list(
        self,
        auth_client,
        organization,
        gateway,
        action,
        fields,
    ):
        _stored(organization, "checks", 0)
        AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="duplicate-policy",
            is_active=True,
            scope="global",
            checks=[dict(CHECK), {**CHECK, "action": "log"}],
        )
        assert sync_guardrail_policies(organization)
        before = _bytes(organization)
        gateway.reset_mock()
        response = _post(auth_client, action, {"name": CHECK["name"], **fields})
        assert response.status_code == 400, response.json()
        assert response.json()["code"] == "guardrail_name_ambiguous"
        assert "2" in response.json()["message"]
        assert _bytes(organization) == before
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("shape", ["rules", "checks"])
    @pytest.mark.parametrize("action,fields", WRITES)
    @pytest.mark.parametrize("count", [0, 1])
    def test_unique_and_new_names_still_work(
        self,
        auth_client,
        organization,
        gateway,
        shape,
        action,
        fields,
        count,
    ):
        _stored(organization, shape, count)
        response = _post(auth_client, action, {"name": CHECK["name"], **fields})
        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization,
            is_active=True,
            deleted=False,
        )
        # An empty checks list uses the existing rules upsert path.
        entries = active.guardrails.get("rules") or active.guardrails["checks"]
        assert len(entries) == 1
        if action == "update-guardrail":
            assert entries[0]["action"] == "log"
        else:
            assert entries[0]["enabled"] is False

    @pytest.mark.parametrize("shape", ["rules", "checks"])
    def test_other_duplicate_names_do_not_block_a_unique_check(
        self,
        auth_client,
        organization,
        gateway,
        shape,
    ):
        config = _stored(organization, shape)
        unrelated = copy.deepcopy(config.guardrails[shape])
        config.guardrails[shape].append({"name": "pii-detector", "enabled": True})
        config.save(update_fields=["guardrails"])
        response = _post(
            auth_client,
            "toggle-guardrail",
            {
                "name": "pii-detector",
                "enabled": False,
            },
        )
        assert response.status_code == 200, response.json()
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization,
            is_active=True,
            deleted=False,
        )
        assert active.guardrails[shape][:2] == unrelated
        assert active.guardrails[shape][2]["enabled"] is False

    def test_checks_aliases_cannot_silently_select_one_occurrence(
        self,
        auth_client,
        organization,
        gateway,
    ):
        config = _stored(organization, "checks")
        config.guardrails["checks"] = [
            {"name": "pii-detector", "enabled": True},
            {"name": "pii-detection", "enabled": False},
        ]
        config.save(update_fields=["guardrails"])
        before = _bytes(organization)
        response = _post(
            auth_client,
            "toggle-guardrail",
            {
                "name": "pii-detector",
                "enabled": False,
            },
        )
        assert response.status_code == 400, response.json()
        assert response.json()["code"] == "guardrail_name_ambiguous"
        assert _bytes(organization) == before
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("shape", ["rules", "checks"])
    @pytest.mark.parametrize("keep_index", [0, 1])
    def test_remove_explicit_duplicate_by_replacing_array(
        self,
        auth_client,
        organization,
        gateway,
        shape,
        keep_index,
    ):
        config = _stored(organization, shape)
        kept = copy.deepcopy(config.guardrails[shape][keep_index])
        response = _post(auth_client, "update-config", {"guardrails": {shape: [kept]}})
        assert response.status_code == 200, response.json()
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization,
            is_active=True,
            deleted=False,
        )
        assert active.guardrails[shape] == [kept]
        response = _post(
            auth_client,
            "toggle-guardrail",
            {
                "name": CHECK["name"],
                "enabled": False,
            },
        )
        assert response.status_code == 200, response.json()

    def test_policy_duplicates_can_be_removed_by_id_scoped_array_replacement(
        self,
        auth_client,
        organization,
        gateway,
    ):
        _stored(organization, "checks", 0)
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="repair-policy",
            is_active=True,
            scope="global",
            checks=[dict(CHECK), {**CHECK, "action": "log"}],
        )
        assert sync_guardrail_policies(organization)
        response = auth_client.patch(
            f"/agentcc/guardrail-policies/{policy.id}/",
            {"checks": [policy.checks[0]]},
            format="json",
        )
        assert response.status_code == 200, response.json()
        policy.refresh_from_db()
        assert policy.checks == [CHECK]
        response = _post(
            auth_client,
            "toggle-guardrail",
            {
                "name": CHECK["name"],
                "enabled": False,
            },
        )
        assert response.status_code == 200, response.json()

    @pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
    @pytest.mark.parametrize(
        "action,fields",
        [
            *WRITES,
            (
                "update-config",
                {"guardrails": {"rules": [CHECK]}},
            ),
        ],
    )
    def test_non_admin_cannot_edit_or_remove_duplicates(
        self,
        user,
        workspace,
        organization,
        gateway,
        level,
        action,
        fields,
    ):
        _stored(organization, "rules")
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=_user_with_level(organization, level))
        client.set_workspace(workspace)
        before = _bytes(organization)
        try:
            data = (
                fields
                if action == "update-config"
                else {"name": CHECK["name"], **fields}
            )
            response = _post(client, action, data)
        finally:
            client.stop_workspace_injection()
        assert response.status_code == 403, response.json()
        assert GATEWAY_ADMIN_MESSAGE in str(response.json())
        assert _bytes(organization) == before
        gateway.set_org_config.assert_not_called()
