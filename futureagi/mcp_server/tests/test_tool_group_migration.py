"""Upgrade saved MCP settings without broadening access or losing selections."""

from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from mcp_server.models.tool_config import MCPToolGroupConfig

migration = import_module("mcp_server.migrations.0004_remove_retired_tool_groups")


@pytest.fixture
def migrate_groups():
    historical_apps = (
        MigrationLoader(connection)
        .project_state([("mcp_server", "0003_streamable_http_transport")])
        .apps
    )

    def run():
        with connection.schema_editor(atomic=False) as editor:
            migration.remove_retired_tool_groups(historical_apps, editor)

    return run


@pytest.mark.django_db
@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize(
    "before,after",
    [
        (
            ["context", "users", "datasets", "docs", "observability"],
            ["context", "datasets", "observability"],
        ),
        (["docs", "users", "docs"], []),
        ([], []),
        (["prompts", "context"], ["prompts", "context"]),
        (["gateway", "docs", "dashboards"], ["gateway", "dashboards"]),
        (["future_group", "users"], ["future_group"]),
    ],
)
def test_upgrade_preserves_selections(
    mcp_connection, migrate_groups, before, after, deleted
):
    configs = MCPToolGroupConfig._base_manager.filter(connection=mcp_connection)
    configs.update(
        enabled_groups=before, disabled_tools=["list_projects"], deleted=deleted
    )

    migrate_groups()
    migrate_groups()  # Safe when a non-atomic migration is resumed.

    config = configs.get()
    assert config.enabled_groups == after
    assert config.disabled_tools == ["list_projects"]
    assert config.deleted == deleted


@pytest.mark.django_db
def test_upgraded_selection_can_be_saved_and_enforced(
    mcp_connection, auth_client, migrate_groups
):
    MCPToolGroupConfig._base_manager.filter(connection=mcp_connection).update(
        enabled_groups=["context", "users", "datasets", "docs", "observability"]
    )
    migrate_groups()

    current = auth_client.get("/mcp/config/tool-groups/")
    assert current.status_code == 200
    assert current.data["result"]["enabled_groups"] == [
        "context",
        "datasets",
        "observability",
    ]
    saved = ["context", "observability"]
    response = auth_client.put(
        "/mcp/config/tool-groups/", {"enabled_groups": saved}, format="json"
    )
    assert response.status_code == 200
    assert response.data["result"]["enabled_groups"] == saved
    assert (
        MCPToolGroupConfig._base_manager.get(connection=mcp_connection).enabled_groups
        == saved
    )

    listed = auth_client.get("/mcp/internal/tools/")
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.data["result"]["tools"]}
    assert "whoami" in names
    assert "list_projects" in names
    assert "list_datasets" not in names
    for name, status in [
        ("whoami", 200),
        ("list_projects", 200),
        ("list_datasets", 403),
    ]:
        result = auth_client.post(
            "/mcp/internal/tool-call/", {"tool_name": name, "params": {}}, format="json"
        )
        assert result.status_code == status
