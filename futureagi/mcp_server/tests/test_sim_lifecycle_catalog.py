"""Contract coverage for the scenario lifecycle and run readiness tools: the
scenario list with its lifecycle, validation, archive and restore, revisions,
versions and source changes, and the readiness of a version pairing. The tests
pin the operations each tool reuses and the spend each admits to.
"""

from uuid import uuid4

import pytest
from django.urls import resolve

from mcp_server.generated_registry import GeneratedToolRegistry
from simulate.services import harness_scenario_actions
from simulate.services.harness_versioning import read as version_reads
from simulate.views import harness_job
from simulate.views.harness_environment import HarnessEnvironmentViewSet

H = "/simulate/api/harness-environments/{id}/"

TOOLS = [
    ("list_harness_scenarios", "GET", "/simulate/api/harness-jobs/{id}/scenarios/"),
    ("list_environment_versions", "GET", H + "versions/"),
    (
        "get_environment_source_changes",
        "GET",
        H + "versions/environment/{version_id}/source-changes/",
    ),
    ("validate_environment_scenarios", "POST", H + "scenarios/validate/"),
    ("get_scenario_validation", "GET", H + "scenarios/validate/"),
    ("get_running_scenario_validation", "GET", H + "scenarios/validate/running/"),
    ("archive_environment_scenario", "POST", H + "scenarios/{key}/archive/"),
    ("restore_environment_scenario", "POST", H + "scenarios/{key}/restore/"),
    ("list_scenario_revisions", "GET", H + "scenarios/{key}/revisions/"),
    ("get_scenario_records", "GET", H + "scenarios/{key}/records/"),
    ("get_environment_readiness_for_scenarios", "POST", H + "capabilities/"),
    ("list_environment_readiness_cases", "GET", H + "capabilities/cases/"),
    ("get_environment_readiness", "GET", H + "capabilities/"),
    ("recheck_environment_readiness", "POST", H + "capabilities/recheck/"),
    ("list_certified_pairings", "GET", H + "capabilities/history/"),
]
NAMES = {name for name, _, _ in TOOLS}
# Both run a build sandbox.
SPENDING = {"validate_environment_scenarios", "recheck_environment_readiness"}
WRITES = SPENDING | {"archive_environment_scenario", "restore_environment_scenario"}


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

    assert tool.annotations["readOnlyHint"] is (name not in WRITES)
    # Archive keeps the scenario's identity, runs and evidence; restore undoes it.
    assert tool.annotations["destructiveHint"] is False
    assert tool.annotations["openWorldHint"] is (name in SPENDING)
    assert ("may incur usage charges" in tool.description) is (name in SPENDING)


def test_validation_status_needs_its_job(registry):
    tool = registry.get("get_scenario_validation")

    assert tool.request["parameters"]["query"] == ["job_id"]
    assert "job_id" in tool.input_schema["required"]


def test_readiness_takes_the_pairing_and_selection(registry):
    query = set(
        registry.get("get_environment_readiness").request["parameters"]["query"]
    )

    assert query == {
        "agent_version_id",
        "environment_version_id",
        "suite_version_id",
        "keys",
        "trials",
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "tool_name,service",
    [
        ("archive_environment_scenario", "archive_scenario"),
        ("restore_environment_scenario", "restore_scenario"),
    ],
)
def test_archive_and_restore_reach_their_view_with_the_key(
    auth_client, monkeypatch, tool_name, service
):
    environment = object()
    calls = []
    monkeypatch.setattr(version_reads, "require_versioning", lambda: None)
    monkeypatch.setattr(
        HarnessEnvironmentViewSet, "_job", lambda self, request, pk: environment
    )

    def act(job, key, **kwargs):
        calls.append((job, key, kwargs.get("reason")))
        return {"scenario_key": key, "lifecycle": None, "live": True}

    monkeypatch.setattr(harness_scenario_actions, service, act)
    params = {"id": str(uuid4()), "key": "refund-flow"}
    if service == "archive_scenario":
        params["reason"] = "duplicate"

    response = auth_client.post(
        "/mcp/internal/tool-call/",
        {"tool_name": tool_name, "params": params},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert calls == [(environment, "refund-flow", params.get("reason"))]


def test_scenario_list_takes_its_page_and_filters(registry):
    tool = registry.get("list_harness_scenarios")
    query = set(tool.request["parameters"]["query"])

    assert {"page", "limit", "search", "ordering", "group_by", "lifecycle"} <= query
    assert {"agent_version_id", "environment_version_id", "suite_version_id"} <= query
    assert tool.input_schema["properties"]["limit"]["maximum"] == 100


def test_readiness_for_many_scenarios_and_its_cases(registry):
    selection = registry.get("get_environment_readiness_for_scenarios")
    cases = registry.get("list_environment_readiness_cases")
    records = registry.get("get_scenario_records")

    assert "scenario_ids" in selection.input_schema["required"]
    assert {"bucket", "row", "cursor", "keys"} <= set(
        cases.request["parameters"]["query"]
    )
    assert cases.input_schema["properties"]["bucket"]["enum"] == [
        "eligible",
        "blocked",
        "stale",
        "untested",
    ]
    assert set(records.input_schema["required"]) == {"id", "key", "table"}


@pytest.mark.django_db
def test_scenario_list_filters_reach_the_view(auth_client, monkeypatch):
    seen = []

    class Provider:
        def list_scenarios(self, request, pk):
            from rest_framework.response import Response

            seen.append((pk, dict(request.query_params.items())))
            return Response({"count": 0, "next": None, "previous": None, "results": []})

    monkeypatch.setattr(harness_job, "get_harness_provider", lambda: Provider())
    environment = str(uuid4())

    response = auth_client.post(
        "/mcp/internal/tool-call/",
        {
            "tool_name": "list_harness_scenarios",
            "params": {
                "id": environment,
                "lifecycle": "archived",
                "page": 2,
                "limit": 25,
                "group_by": "",
            },
        },
        format="json",
    )

    assert response.status_code == 200, response.content
    assert seen == [
        (
            environment,
            {"lifecycle": "archived", "page": "2", "limit": "25", "group_by": ""},
        )
    ]
