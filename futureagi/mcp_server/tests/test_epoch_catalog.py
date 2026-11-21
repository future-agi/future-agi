"""Contract coverage for the epoch MCP tools.

The group is the loop from a failing run to a shipped change: Debug failures
on the run, evidence imports that become tests, then an Epoch and its
candidates. These tests pin the operations, the spend each tool admits to, the
plan entitlement that hides the Epoch tools, and the upgrade of saved
connections.
"""

import json
from importlib import import_module
from uuid import uuid4

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.http import HttpResponse
from django.urls import resolve

from mcp_server.api_executor import DjangoAPIExecutor
from mcp_server.generated_registry import GeneratedToolRegistry
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.tests.test_tool_generation import CONTRACT_PATH
from mcp_server.tool_generation import ToolGenerationError, generate_tool_manifest
from mcp_server.usage_helpers import get_enabled_tools
from tfc import ee_gating
from tfc.ee_gating import EEFeature, FeatureUnavailable

DEBUG_FAILURES = "/simulate/test-executions/{test_execution_id}/debug-analysis/"
IMPORTS = "/simulate/api/harness-environments/{id}/evidence-imports/"
IMPORT = IMPORTS + "{import_id}/"
EPOCHS = "/simulate/api/agent-fix-runs/"
CANDIDATE = EPOCHS + "{id}/candidates/{candidate_id}/"

OPEN = [
    ("get_debug_failures", "GET", DEBUG_FAILURES),
    ("run_debug_failures", "POST", DEBUG_FAILURES),
    ("list_evidence_imports", "GET", IMPORTS),
    ("create_evidence_import", "POST", IMPORTS),
    ("get_evidence_import", "GET", IMPORT),
    ("draft_evidence_import", "POST", IMPORT + "draft/"),
    ("add_evidence_import_cases", "POST", IMPORT + "add/"),
    ("discard_evidence_import", "DELETE", IMPORT),
]
EPOCH = [
    ("get_epoch_launch_options", "GET", EPOCHS + "launch-options/"),
    ("list_epochs", "GET", EPOCHS),
    ("get_epoch", "GET", EPOCHS + "{id}/"),
    ("launch_epoch", "POST", EPOCHS),
    ("stop_epoch", "POST", EPOCHS + "{id}/stop/"),
    ("retry_epoch", "POST", EPOCHS + "{id}/retry/"),
    ("get_epoch_candidate_patch", "GET", CANDIDATE + "patch/"),
    ("make_epoch_candidate_default", "POST", CANDIDATE + "make-default/"),
    ("open_epoch_candidate_pull_request", "POST", CANDIDATE + "pull-request/"),
]
OPEN_NAMES = {name for name, _, _ in OPEN}
EPOCH_NAMES = {name for name, _, _ in EPOCH}
# Tools whose call starts billed model work.
SPENDING = {
    "run_debug_failures",
    "draft_evidence_import",
    "launch_epoch",
    "retry_epoch",
}

migration = import_module("mcp_server.migrations.0005_enable_epoch_tool_group")


@pytest.fixture(scope="module")
def registry():
    return GeneratedToolRegistry.from_manifest()


def test_epoch_group_is_exactly_the_failing_run_to_shipped_change_loop(registry):
    assert {tool.name for tool in registry.list_by_groups(["epoch"])} == (
        OPEN_NAMES | EPOCH_NAMES
    )


@pytest.mark.parametrize("name,method,path", OPEN + EPOCH)
def test_tools_reuse_mounted_operations(registry, name, method, path):
    tool = registry.get(name)

    assert tool.request["method"] == method
    assert tool.request["path"] == path
    assert tool.is_available()
    concrete = path
    for field in tool.request["parameters"]["path"]:
        concrete = concrete.replace("{" + field + "}", str(uuid4()))
    view = resolve(concrete).func
    cls = getattr(view, "cls", None) or getattr(view, "view_class", None)
    assert cls is not None


@pytest.mark.parametrize("name", sorted(OPEN_NAMES | EPOCH_NAMES))
def test_descriptions_say_epoch_and_admit_their_spend(registry, name):
    tool = registry.get(name)
    description = tool.description.lower()

    assert "fix my agent" not in description and "fix-my-agent" not in description
    assert ("may incur usage charges" in description) == (name in SPENDING)
    if name in SPENDING:
        assert tool.annotations["openWorldHint"] is True


def test_epoch_tools_need_the_entitlement_their_views_check(registry):
    """Only the Epoch views check a plan feature; Debug failures and evidence
    imports are open to any signed-in member, so their tools are too."""
    for name in EPOCH_NAMES:
        assert registry.get(name).entitlement == EEFeature.FIX_MY_AGENT.value
    for name in OPEN_NAMES:
        assert registry.get(name).entitlement is None
    features = {feature.value for feature in EEFeature}
    assert {
        tool.entitlement for tool in registry.list_all() if tool.entitlement
    } <= features


def test_discarding_an_import_is_destructive(registry):
    assert registry.get("discard_evidence_import").annotations["destructiveHint"]


@pytest.mark.parametrize("entitlement", ["", 5])
def test_generator_rejects_an_invalid_entitlement(tmp_path, entitlement):
    catalog = {
        "expected_tool_count": 1,
        "tools": [
            {
                "name": "whoami",
                "group": "context",
                "description": "Current user",
                "operation": {"method": "GET", "path": "/accounts/user-info/"},
                "entitlement": entitlement,
            }
        ],
    }
    catalog_path = tmp_path / "tools.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(ToolGenerationError, match="Invalid entitlement"):
        generate_tool_manifest(CONTRACT_PATH, catalog_path)


def _deny(*_, **__):
    raise FeatureUnavailable("fix_my_agent")


@pytest.mark.django_db
@pytest.mark.parametrize("entitled", [True, False])
def test_listing_hides_epoch_tools_without_the_entitlement(
    mcp_connection, monkeypatch, entitled
):
    checked = []

    def check(feature, *, org_id=None, **_):
        checked.append((feature, org_id))
        if not entitled:
            _deny()

    monkeypatch.setattr("mcp_server.usage_helpers.check_ee_feature", check)
    enabled = get_enabled_tools(mcp_connection)

    assert OPEN_NAMES <= enabled
    assert (EPOCH_NAMES <= enabled) is entitled
    assert bool(EPOCH_NAMES & enabled) is entitled
    # One check per listing, for the connection's organization.
    assert checked == [("fix_my_agent", str(mcp_connection.organization_id))]


@pytest.mark.django_db
def test_listing_follows_check_ee_feature_off_cloud(mcp_connection, monkeypatch):
    """The listing asks the real check_ee_feature, unstubbed, on its off-Cloud
    path: it shows the Epoch tools exactly when that check passes, as
    AgentFixRunViewSet serves their routes."""
    monkeypatch.setattr(ee_gating, "_try_capability_service", lambda *_, **__: False)
    monkeypatch.setattr(ee_gating, "is_oss", lambda: True)
    org_id = str(mcp_connection.organization_id)
    try:
        ee_gating.check_ee_feature(EEFeature.FIX_MY_AGENT, org_id=org_id)
        allowed = True
    except FeatureUnavailable:
        allowed = False

    enabled = get_enabled_tools(mcp_connection)

    assert (EPOCH_NAMES <= enabled) is allowed
    assert bool(EPOCH_NAMES & enabled) is allowed
    assert OPEN_NAMES <= enabled


@pytest.mark.django_db
def test_an_unlisted_epoch_tool_cannot_be_called(auth_client, monkeypatch):
    monkeypatch.setattr("mcp_server.usage_helpers.check_ee_feature", _deny)

    response = auth_client.post(
        "/mcp/internal/tool-call/",
        {"tool_name": "get_epoch", "params": {"id": str(uuid4())}},
        format="json",
    )

    assert response.status_code == 403


def test_a_text_body_is_returned_as_text(registry):
    """A candidate's git patch is text/x-diff, not JSON."""
    tool = registry.get("get_epoch_candidate_patch")
    patch = "diff --git a/agent.py b/agent.py\n+    retries = 2\n"

    result = DjangoAPIExecutor._normalize_response(
        tool, HttpResponse(patch.encode(), content_type="text/x-diff")
    )

    assert result == patch
    # A binary body is still not decoded.
    archive = DjangoAPIExecutor._normalize_response(
        tool, HttpResponse(b"\x1f\x8b\x08", content_type="application/gzip")
    )
    assert archive == b"\x1f\x8b\x08"


@pytest.fixture
def migrate_groups():
    historical_apps = (
        MigrationLoader(connection)
        .project_state([("mcp_server", "0004_remove_retired_tool_groups")])
        .apps
    )

    def run():
        with connection.schema_editor(atomic=False) as editor:
            migration.enable_epoch_tool_group(historical_apps, editor)

    return run


@pytest.mark.django_db
@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize(
    "before,after",
    [
        (["context", "simulation"], ["context", "simulation", "epoch"]),
        (["simulation", "epoch"], ["simulation", "epoch"]),
        # Simulation turned off: no tool that spends credits appears.
        (["context", "agents", "datasets"], ["context", "agents", "datasets"]),
        ([], []),
    ],
)
def test_upgrade_adds_epoch_only_where_simulation_is_on(
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
def test_a_new_connection_gets_the_epoch_group(mcp_connection):
    config = MCPToolGroupConfig._base_manager.get(connection=mcp_connection)
    assert "epoch" in config.enabled_groups
