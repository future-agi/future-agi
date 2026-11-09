"""N04/N05: the nested evaluator uses the real discovery and live recheck."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace, WorkspaceMembership
from ee.agenthub.eval_orchestrator.orchestrator import EvalOrchestrator
from ee.falcon_ai import tool_scope
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests import test_mcp_workspace_scope as fixtures
from ee.falcon_ai.tests.test_mcp_workspace_scope import SCHEMA, grant
from tfc.middleware.workspace_context import set_workspace_context, workspace_context

scope_state = fixtures.scope_state
connector = fixtures.connector
proxy_boundary = fixtures.proxy_boundary
pytestmark = pytest.mark.django_db(transaction=True)


def orchestrator(state):
    instance = EvalOrchestrator.__new__(EvalOrchestrator)
    instance.eval_config = SimpleNamespace(
        organization_id=state.org.pk, available_resources={"mcp_tools": [SCHEMA]}
    )
    instance._resources_used = []
    return instance


def call(instance):
    return instance._tool_call_mcp(SCHEMA["name"], {})


def test_nested_discovery_excludes_duplicate_names_before_live_recheck(
    scope_state, connector, proxy_boundary
):
    state = scope_state
    grant(state)
    foreign = Organization.objects.create(name="Foreign duplicate connector")
    foreign_workspace = Workspace.objects.create(
        name="Foreign duplicate workspace",
        organization=foreign,
        created_by=state.owner,
    )
    decoys = []
    for org, workspace in (
        (state.org, state.default),
        (state.org, None),
        (foreign, foreign_workspace),
    ):
        decoys.append(
            MCPConnector.no_workspace_objects.create(
                organization=org,
                workspace=workspace,
                name="Duplicate connector",
                server_url="http://127.0.0.1:9/mcp",
                auth_type="none",
                is_active=True,
                is_verified=True,
                discovered_tools=[SCHEMA],
            )
        )
    assert len({row.pk for row in [connector, *decoys]}) == 4
    discover = tool_scope.accessible_connectors
    observed = []

    def observe(context):
        found = list(discover(context))
        observed.append([row.pk for row in found])
        return found

    instance = orchestrator(state)
    with workspace_context(state.private, state.org, state.member):
        set_workspace_context(require_workspace=True)
        with patch.object(
            tool_scope, "accessible_connectors", side_effect=observe
        ) as discovery:
            result = call(instance)
    # This assertion is outside the product's catch-all. An organization-only
    # discovery mutant cannot be masked by the final execution recheck.
    discovery.assert_called_once()
    assert observed == [[connector.pk]]
    assert result["result"]["is_error"] is False
    proxy_boundary.sync_proxy.assert_called_once()
    assert proxy_boundary.sync_proxy.call_args.args[0].pk == connector.pk
    proxy_boundary.async_proxy.assert_not_called()


@pytest.mark.parametrize(
    "state_change",
    [
        "no-user",
        "no-org",
        "no-workspace",
        "wrong-org",
        "different-workspace",
        "null-connector",
        "revoked",
    ],
)
def test_nested_mcp_requires_same_org_exact_workspace_and_actor(
    scope_state, connector, proxy_boundary, state_change
):
    state = scope_state
    membership = grant(state)
    instance = orchestrator(state)
    with workspace_context(state.private, state.org, state.member):
        set_workspace_context(require_workspace=True)
        assert call(instance)["result"]["is_error"] is False
    proxy_boundary.sync_proxy.assert_called_once()
    assert proxy_boundary.sync_proxy.call_args.args[0].pk == connector.pk
    proxy_boundary.sync_proxy.reset_mock()
    instance._resources_used.clear()
    user, org, workspace = state.member, state.org, state.private
    if state_change == "no-user":
        user = None
    elif state_change == "no-org":
        org = None
    elif state_change == "no-workspace":
        workspace = None
    elif state_change == "wrong-org":
        # Context and connector remain genuinely accessible, only the evaluator org differs.
        instance.eval_config.organization_id = uuid4()
    elif state_change == "different-workspace":
        user, workspace = state.owner, state.default
    elif state_change == "null-connector":
        user, workspace = state.owner, state.default
        MCPConnector._base_manager.filter(pk=connector.pk).update(workspace=None)
    else:
        WorkspaceMembership._base_manager.filter(pk=membership.pk).update(
            is_active=False
        )
    with workspace_context(workspace, org, user):
        set_workspace_context(require_workspace=True)
        result = call(instance)
    assert "error" in result
    assert "result" not in result
    assert instance._resources_used == []
    proxy_boundary.sync_proxy.assert_not_called()
    proxy_boundary.async_proxy.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "membership",
        "organization",
        "workspace",
        "move",
        "disabled",
        "unverified",
        "enabled-name",
        "discovered-name",
    ],
)
def test_nested_mcp_rechecks_between_discovery_and_proxy(
    scope_state, connector, proxy_boundary, change
):
    state = scope_state
    membership = grant(state)
    instance = orchestrator(state)
    with workspace_context(state.private, state.org, state.member):
        set_workspace_context(require_workspace=True)
        assert call(instance)["result"]["is_error"] is False
    proxy_boundary.sync_proxy.assert_called_once()
    proxy_boundary.sync_proxy.reset_mock()
    instance._resources_used.clear()
    discover = tool_scope.accessible_connectors

    def discover_then_revoke(context):
        found = list(discover(context))
        assert [c.pk for c in found] == [connector.pk]
        if change == "membership":
            type(membership)._base_manager.filter(pk=membership.pk).update(
                is_active=False
            )
        elif change == "organization":
            type(state.membership)._base_manager.filter(pk=state.membership.pk).update(
                is_active=False
            )
        elif change == "workspace":
            type(state.private)._base_manager.filter(pk=state.private.pk).update(
                is_active=False
            )
        else:
            update = {
                "move": {"workspace": state.default},
                "disabled": {"is_active": False},
                "unverified": {"is_verified": False},
                "enabled-name": {"enabled_tool_names": ["different_tool"]},
                "discovered-name": {"discovered_tools": []},
            }[change]
            MCPConnector._base_manager.filter(pk=connector.pk).update(**update)
        return found

    with workspace_context(state.private, state.org, state.member):
        set_workspace_context(require_workspace=True)
        with (
            patch.object(
                tool_scope, "accessible_connectors", side_effect=discover_then_revoke
            ),
            patch.object(
                tool_scope,
                "connector_for_execution",
                wraps=tool_scope.connector_for_execution,
            ) as recheck,
        ):
            result = call(instance)
    recheck.assert_called_once()
    assert recheck.call_args.args[:2] == (connector.pk, SCHEMA["name"])
    assert "error" in result
    assert instance._resources_used == []
    proxy_boundary.sync_proxy.assert_not_called()
    proxy_boundary.async_proxy.assert_not_called()
