"""Policy and inventory outcomes use real transactions; all push I/O is faked."""

import copy
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models.audit_log import AuditLog
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy as Policy
from agentcc.models.mcp_tool_revision import AgentccMCPToolRevision as Revision
from agentcc.models.org_config import AgentccOrgConfig as Config
from agentcc.services import config_push, guardrail_sync
from agentcc.services import mcp_revisions as revisions
from agentcc.services.org_config_writer import (
    GATEWAY_SYNC_FAILED,
    GATEWAY_SYNC_FAILED_MESSAGE,
)
from agentcc.tests.push_sync_test_helpers import (
    FIELD,
    RAW_REASON,
    _assert_refusal,
)
from agentcc.tests.test_mcp_revisions import (
    ECHO_V1,
    REV1,
    REV2,
    REV3,
    _admin_tool,
    _record,
    _seed,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
POLICIES = "/agentcc/guardrail-policies/"
CHECK = {"name": "keyword-blocklist", "config": {"words": ["synthetic"]}}


@pytest.fixture(autouse=True)
def offline_push():
    with patch.object(config_push, "get_gateway_client") as factory:
        yield factory.return_value


def _snapshot(org):
    return {
        model.__name__: list(
            model._base_manager.filter(organization=org).order_by("pk").values()
        )
        for model in (Config, Policy, Revision, AuditLog)
    }


def _policy(org, *, scope="global"):
    return Policy.no_workspace_objects.create(
        organization=org, name="original", scope=scope, checks=[copy.deepcopy(CHECK)]
    )


def _change(client, policy, action):
    body = {"name": "changed", "checks": [CHECK], "scope": "global"}
    if action == "create":
        return client.post(POLICIES, body, format="json")
    if action == "sync":
        return client.post(POLICIES + "sync/", {}, format="json")
    url = f"{POLICIES}{policy.pk}/"
    if action == "destroy":
        return client.delete(url)
    return getattr(client, "put" if action == "update" else "patch")(
        url, body, format="json"
    )


@pytest.mark.parametrize(
    "action", ["create", "update", "partial_update", "destroy", "sync"]
)
def test_policy_refusal_rolls_back_every_write(user, auth_client, action):
    org = user.organization
    _seed(org)
    policy = _policy(org)
    before = _snapshot(org)

    def refuse(org_id, config):
        assert config.guardrails != before["AgentccOrgConfig"][0]["guardrails"]
        raise config_push.MCPCredentialPlacementRefused(FIELD, RAW_REASON)

    with patch.object(config_push, "push_org_config", side_effect=refuse) as push:
        response = _change(auth_client, policy, action)
    push.assert_called_once()
    assert _snapshot(org) == before
    _assert_refusal(response)


@pytest.mark.parametrize(
    "action", ["create", "update", "partial_update", "destroy", "sync"]
)
@pytest.mark.parametrize("outcome", ["raised", "false", "success"])
def test_policy_saved_outcome(user, auth_client, action, outcome):
    org = user.organization
    _seed(org)
    policy = _policy(org)
    with patch.object(
        config_push, "push_org_config", return_value=outcome == "success"
    ) as push:
        if outcome == "raised":
            push.side_effect = RuntimeError("synthetic late push failure")
        response = _change(auth_client, policy, action)
    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    assert result["gateway_synced"] is (outcome == "success")
    if outcome != "success":
        assert result["gateway_warning"] == GATEWAY_SYNC_FAILED_MESSAGE
    policy.refresh_from_db()
    if action == "destroy":
        assert policy.deleted
    elif action in ("update", "partial_update"):
        assert policy.name == "changed"
    elif action == "create":
        assert Policy.no_workspace_objects.filter(
            organization=org, name="changed"
        ).exists()
    active = Config.no_workspace_objects.get(organization=org, is_active=True)
    assert active.guardrails == guardrail_sync.policy_guardrails(org)[0]


@pytest.mark.parametrize("refused", [False, True])
def test_standalone_policy_sync_outcome(user, refused):
    org = user.organization
    _seed(org)
    _policy(org)
    before = _snapshot(org)
    error = (
        config_push.MCPCredentialPlacementRefused(FIELD, RAW_REASON)
        if refused
        else RuntimeError("synthetic push failure")
    )
    with patch.object(config_push, "push_org_config", side_effect=error):
        if refused:
            with pytest.raises(config_push.MCPCredentialPlacementRefused):
                guardrail_sync.sync_guardrail_policies(org)
            assert _snapshot(org) == before
        else:
            assert guardrail_sync.sync_guardrail_policies(org) is False
            assert Config.no_workspace_objects.get(
                organization=org, is_active=True
            ).guardrails["checks"]


@pytest.mark.parametrize("refused", [False, True])
def test_repair_with_sync_outcome(user, refused):
    org = user.organization
    _seed(org)
    policy = _policy(org, scope="key")
    before = _snapshot(org)
    error = (
        config_push.MCPCredentialPlacementRefused(FIELD, RAW_REASON)
        if refused
        else RuntimeError("synthetic push failure")
    )
    output = StringIO()
    with patch.object(config_push, "push_org_config", side_effect=error):
        if refused:
            with pytest.raises(
                CommandError, match="mcp_credential_placement_unsupported"
            ):
                call_command(
                    "agentcc_find_scoped_guardrail_policies",
                    "--org",
                    str(org.pk),
                    "--repair",
                    "--apply",
                    "--sync",
                    stdout=output,
                    stderr=StringIO(),
                )
            assert _snapshot(org) == before
        else:
            call_command(
                "agentcc_find_scoped_guardrail_policies",
                "--org",
                str(org.pk),
                "--repair",
                "--apply",
                "--sync",
                stdout=output,
            )
            policy.refresh_from_db()
            assert policy.scope == "global"
            assert "gateway_synced=False" in output.getvalue()
            assert GATEWAY_SYNC_FAILED_MESSAGE in output.getvalue()


def _inventory(org):
    _seed(org)
    # Exercise stale superseding, pending-row edits, new rows and remembered
    # previous definitions, not merely a single new insert.
    stale = Revision.objects.create(
        organization=org,
        server_id="github",
        tool="stale",
        revision=REV3,
        definition=ECHO_V1,
    )
    revisions._upsert(org, _record(org, definition=ECHO_V1))
    return stale, [
        _record(org, previous_definition=ECHO_V1),
        _record(org, tool="new", revision=REV3, previous=""),
    ]


@pytest.mark.parametrize("via", ["service", "endpoint"])
def test_inventory_refusal_rolls_back_all_rows(user, auth_client, via):
    org = user.organization
    _, records = _inventory(org)
    before = _snapshot(org)

    def refuse(org_id, config):
        assert revisions.pins_of(config.mcp) == {"github/echo": REV1}
        assert _snapshot(org) != before
        raise config_push.MCPCredentialPlacementRefused(FIELD, RAW_REASON)

    with (
        patch.object(
            revisions.gateway_revisions, "list_revisions", return_value=records
        ),
        patch.object(
            revisions.gateway_mcp,
            "list_tools",
            return_value=[_admin_tool("echo", REV1)],
        ),
        patch.object(config_push, "push_org_config", side_effect=refuse) as push,
    ):
        if via == "service":
            with pytest.raises(config_push.MCPCredentialPlacementRefused):
                revisions.pull(org)
        else:
            _assert_refusal(auth_client.get("/agentcc/mcp/revisions/"))
    push.assert_called_once()
    assert _snapshot(org) == before


@pytest.mark.parametrize("via", ["service", "endpoint"])
@pytest.mark.parametrize("synced", [False, True])
def test_inventory_preserves_baseline_warnings(
    user, auth_client, via, synced, offline_push
):
    org = user.organization
    stale, records = _inventory(org)
    if not synced:
        offline_push.set_org_config.side_effect = RuntimeError(
            "synthetic gateway unavailable"
        )
    with (
        patch.object(
            revisions.gateway_revisions, "list_revisions", return_value=records
        ),
        patch.object(
            revisions.gateway_mcp,
            "list_tools",
            return_value=[_admin_tool("echo", REV1)],
        ),
    ):
        if via == "service":
            result = revisions.pull(org)
            assert result["baselined"] == ["github"]
        else:
            response = auth_client.get("/agentcc/mcp/revisions/")
            assert response.status_code == 200
            result = response.json()["result"]
    assert result["gateway_synced"] is synced
    if not synced:
        assert {
            "code": GATEWAY_SYNC_FAILED,
            "message": GATEWAY_SYNC_FAILED_MESSAGE,
        } in result["warnings"]
    stale.refresh_from_db()
    assert stale.status == "superseded"
    active = Config.no_workspace_objects.get(organization=org, is_active=True)
    assert revisions.pins_of(active.mcp) == {"github/echo": REV1}
    assert Revision.objects.filter(organization=org, revision=REV2).exists()


def test_scheduler_counts_saved_unsynced_pull(user, offline_push):
    org = user.organization
    _, records = _inventory(org)
    offline_push.set_org_config.side_effect = RuntimeError(
        "synthetic gateway unavailable"
    )
    with (
        patch.object(
            revisions.gateway_revisions, "list_revisions", return_value=records
        ),
        patch.object(
            revisions.gateway_mcp,
            "list_tools",
            return_value=[_admin_tool("echo", REV1)],
        ),
    ):
        result = revisions.pull_all()
    assert result == {"pulled": 1, "failed": 0, "unsynced": 1}
