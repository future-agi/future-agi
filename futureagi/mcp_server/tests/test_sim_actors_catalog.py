"""Contract coverage for the simulation environment tools: environments, their
actors and drafts, the run plan, a run with its seed, and one trial's World
changes. The tests pin the operations each tool reuses, the spend each admits
to, and the renamed environment parameter the actor writes take.
"""

import json
from uuid import uuid4

import pytest
from django.urls import resolve

from mcp_server.generated_registry import GeneratedToolRegistry
from mcp_server.tests.test_tool_generation import CONTRACT_PATH
from mcp_server.tool_generation import ToolGenerationError, generate_tool_manifest
from simulate.services.harness_versioning import read as version_reads
from simulate.views.harness_environment import HarnessEnvironmentViewSet

ENVIRONMENTS = "/simulate/api/harness-environments/"
ENVIRONMENT = ENVIRONMENTS + "{id}/"
RENAMED = ENVIRONMENTS + "{environment_id}/"

TOOLS = [
    ("list_harness_environments", "GET", ENVIRONMENTS),
    ("get_harness_environment", "GET", ENVIRONMENT),
    ("list_environment_actors", "GET", ENVIRONMENT + "actors/"),
    ("get_environment_actor", "GET", ENVIRONMENT + "actors/{actor_id}/"),
    ("validate_environment_actor", "POST", RENAMED + "actor-validation/"),
    ("create_environment_actor", "POST", RENAMED + "actors/"),
    ("update_environment_actor", "PUT", RENAMED + "actors/{actor_id}/"),
    ("list_environment_drafts", "GET", ENVIRONMENT + "drafts/"),
    ("save_environment_drafts", "POST", ENVIRONMENT + "drafts/save/"),
    ("get_environment_run_plan", "GET", ENVIRONMENT + "run-plan/"),
    ("run_environment", "POST", ENVIRONMENT + "run/"),
    ("get_call_execution", "GET", "/simulate/v3/call-executions/{call_execution_id}/"),
]
NAMES = {name for name, _, _ in TOOLS}
# Tools whose call starts billed model work.
SPENDING = {"run_environment"}
# Tools that write nothing.
READS = {
    "list_harness_environments",
    "get_harness_environment",
    "list_environment_actors",
    "get_environment_actor",
    "validate_environment_actor",
    "list_environment_drafts",
    "get_environment_run_plan",
    "get_call_execution",
}


@pytest.fixture(scope="module")
def registry():
    return GeneratedToolRegistry.from_manifest()


def test_tools_are_in_the_simulation_group(registry):
    assert NAMES <= {tool.name for tool in registry.list_by_groups(["simulation"])}


@pytest.mark.parametrize("name,method,path", TOOLS)
def test_tools_reuse_mounted_operations(registry, name, method, path):
    tool = registry.get(name)

    assert tool.request["method"] == method
    assert tool.request["path"] == path
    assert tool.is_available()
    concrete = path
    for field in tool.request["parameters"]["path"]:
        concrete = concrete.replace("{" + field + "}", str(uuid4()))
    view = resolve(concrete).func
    assert (getattr(view, "cls", None) or getattr(view, "view_class", None)) is not None


@pytest.mark.parametrize("name", sorted(NAMES))
def test_access_and_spend(registry, name):
    tool = registry.get(name)

    assert tool.annotations["readOnlyHint"] is (name in READS)
    assert tool.annotations["destructiveHint"] is False
    assert tool.annotations["openWorldHint"] is (name in SPENDING)
    assert ("may incur usage charges" in tool.description) is (name in SPENDING)


def test_a_run_takes_its_seed_and_versions(registry):
    body = registry.get("run_environment").request["parameters"]["body"]

    assert {"seed", "trials", "scenario_ids", "agent_version_id"} <= set(body)


@pytest.mark.parametrize(
    "name",
    [
        "validate_environment_actor",
        "create_environment_actor",
        "update_environment_actor",
    ],
)
def test_actor_writes_take_the_environment_as_environment_id(registry, name):
    """The actor body has its own `id`, so the environment's `{id}` is renamed."""
    tool = registry.get(name)
    parameters = tool.request["parameters"]

    assert "environment_id" in parameters["path"]
    assert "id" in parameters["body"]
    assert "id" not in parameters["path"]
    assert "environment_id" in tool.input_schema["required"]


@pytest.mark.django_db
def test_a_renamed_parameter_reaches_its_view_as_the_environment(
    auth_client, monkeypatch
):
    """environment_id fills the route's environment segment, so the view looks
    up exactly that environment."""
    looked_up = []

    def job(self, request, pk):
        looked_up.append(pk)
        return None

    monkeypatch.setattr(HarnessEnvironmentViewSet, "_job", job)
    monkeypatch.setattr(version_reads, "require_versioning", lambda: None)
    monkeypatch.setattr(version_reads, "require_actors", lambda: None)
    environment_id = str(uuid4())

    response = auth_client.post(
        "/mcp/internal/tool-call/",
        {
            "tool_name": "validate_environment_actor",
            "params": {
                "environment_id": environment_id,
                "op": "delete",
                "actor_id": "caller",
            },
        },
        format="json",
    )

    assert response.status_code == 404
    assert looked_up == [environment_id]


def _catalog(tmp_path, path_names):
    catalog = {
        "expected_tool_count": 1,
        "tools": [
            {
                "name": "create_environment_actor",
                "group": "simulation",
                "description": "Add an actor",
                "operation": {"method": "POST", "path": ENVIRONMENT + "actors/"},
                "access": "write",
                "path_names": path_names,
            }
        ],
    }
    path = tmp_path / "tools.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


def test_without_a_rename_the_body_id_conflicts(tmp_path):
    with pytest.raises(ToolGenerationError, match="Conflicting request field: id"):
        generate_tool_manifest(CONTRACT_PATH, _catalog(tmp_path, {}))


@pytest.mark.parametrize(
    "path_names,message",
    [
        ({"environment": "environment_id"}, "Unknown path parameter"),
        ({"id": "environment id"}, "Invalid path name"),
        ({"id": 5}, "Invalid path name"),
    ],
)
def test_generator_rejects_a_bad_rename(tmp_path, path_names, message):
    with pytest.raises(ToolGenerationError, match=message):
        generate_tool_manifest(CONTRACT_PATH, _catalog(tmp_path, path_names))
