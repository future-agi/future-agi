"""N05: a permitted discovery snapshot cannot authorize a later dispatch."""

from unittest.mock import patch

import pytest

from ee.falcon_ai import tool_scope
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests import test_mcp_workspace_scope as fixtures
from ee.falcon_ai.tests.test_mcp_workspace_scope import SCHEMA, grant
from ee.falcon_ai.tests.test_wsl_merge_nested_mcp import call, orchestrator
from tfc.middleware.workspace_context import set_workspace_context, workspace_context

scope_state = fixtures.scope_state
connector = fixtures.connector
proxy_boundary = fixtures.proxy_boundary
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "change", ["move", "disabled", "unverified", "enabled-name", "discovered-name"]
)
def test_cached_connector_snapshot_cannot_replace_live_dispatch_lookup(
    scope_state, connector, proxy_boundary, change
):
    state = scope_state
    grant(state)
    instance = orchestrator(state)
    with workspace_context(state.private, state.org, state.member):
        set_workspace_context(require_workspace=True)
        assert call(instance)["result"]["is_error"] is False
    proxy_boundary.sync_proxy.assert_called_once()
    proxy_boundary.sync_proxy.reset_mock()
    instance._resources_used.clear()
    discover = tool_scope.accessible_connectors
    snapshots = []

    def discover_then_change(context):
        found = list(discover(context))
        assert [row.pk for row in found] == [connector.pk]
        snapshots.extend(found)
        update = {
            "move": {"workspace": state.default},
            "disabled": {"is_active": False},
            "unverified": {"is_verified": False},
            "enabled-name": {"enabled_tool_names": ["different_tool"]},
            "discovered-name": {"discovered_tools": []},
        }[change]
        assert MCPConnector._base_manager.filter(pk=connector.pk).update(**update) == 1
        return found

    with workspace_context(state.private, state.org, state.member):
        set_workspace_context(require_workspace=True)
        with (
            patch.object(
                tool_scope, "accessible_connectors", side_effect=discover_then_change
            ),
            patch.object(
                tool_scope,
                "connector_for_execution",
                wraps=tool_scope.connector_for_execution,
            ) as recheck,
        ):
            result = call(instance)
        # Verify the snapshot itself would still pass all cached-field checks.
        # Only the persisted connector has changed; membership remains valid.
        assert len(snapshots) == 1
        cached = snapshots[0]
        assert tool_scope.connector_access_allowed(
            cached, tool_scope.current_tool_context()
        )
        assert (
            not cached.enabled_tool_names or SCHEMA["name"] in cached.enabled_tool_names
        )
        assert SCHEMA["name"] in [schema["name"] for schema in cached.discovered_tools]
    # Outcome first: a missing lookup spy alone is not a security-red witness.
    assert result == {"error": "Connector is not available in this workspace."}
    assert instance._resources_used == []
    proxy_boundary.sync_proxy.assert_not_called()
    proxy_boundary.async_proxy.assert_not_called()
    recheck.assert_called_once()
    assert recheck.call_args.args[:2] == (connector.pk, SCHEMA["name"])
