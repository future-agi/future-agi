"""TS-1..TS-4 RED regressions, written before the reader overlay.

Dispatch through the registered HTTP routes with real ORM/serializers. Ambient
workspace scope is deliberately empty or conflicts with the request. No
scenario/graph/agent query or serializer is mocked. R2 adds root and scenario
isolation regressions before the production fixes. R3 adds default-workspace
legacy-run compatibility and fail-closed controls before the helper correction.
R4 adds the unchanged query-guard harness and real-route projection controls
before replacing the detail endpoint's separate graph query. Only the guard
harness uses canned rows; the R4 ORM controls use real queries and serializers.
R5 adds soft-deleted mixed-tenant detail and live-only summary controls before
correcting the scenario manager used by the shared ownership helper.
Execution belongs to the integration runner, not this writer packet.
"""

import json
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models import (
    AgentDefinition,
    CallExecution,
    RunTest,
    Scenarios,
    SimulatorAgent,
)
from simulate.models.scenario_graph import ScenarioGraph
from simulate.models.test_execution import TestExecution as SimulationTestExecution
from simulate.tests.test_scenario_row_org_scope import tables as tables
from simulate.tests.test_scenario_row_org_scope import world as world
from simulate.tests.test_simulate_views_workspace_scope_r1 import (
    scope_data as scope_data,
)
from tfc.middleware.workspace_context import workspace_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenants(scope_data):
    data = scope_data
    foreign_org = Organization.objects.create(name="TS foreign organization")
    foreign_workspace, _ = Workspace.no_workspace_objects.get_or_create(
        organization=foreign_org,
        is_default=True,
        defaults={"name": "TS foreign workspace", "created_by": data.user},
    )
    return SimpleNamespace(
        data=data, organization=foreign_org, workspace=foreign_workspace
    )


def _route(data, path, method="get", payload=None, *, ambient=None, workspace=None):
    request = getattr(APIRequestFactory(), method)(
        path, data=payload or {}, format="json"
    )
    request.organization = data.organization
    request.workspace = data.w2 if workspace is None else workspace
    force_authenticate(request, user=data.user)
    match = resolve(path.split("?", 1)[0])
    with workspace_context(
        ambient,
        organization=ambient.organization if ambient is not None else None,
        user=data.user if ambient is not None else None,
    ):
        return match.func(request, *match.args, **match.kwargs)


def _run_row(response, run):
    assert response.status_code == 200, response.data
    pending = [response.data]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if str(item.get("id")) == str(run.pk) and "source_type" in item:
                return item
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    raise AssertionError("The caller's local run must remain readable")


def _loads(monkeypatch, model):
    loaded = []
    original = model.from_db

    def observe(cls, db, field_names, values):
        obj = original(db, field_names, values)
        loaded.append(obj.pk)
        return obj

    monkeypatch.setattr(model, "from_db", classmethod(observe))
    return loaded


def _owner(tenants, foreign):
    if foreign == "organization":
        return tenants.organization, tenants.workspace
    return tenants.data.organization, (
        tenants.data.w1 if foreign == "workspace" else None
    )


def _foreign_agent(tenants, foreign, model):
    organization, workspace = _owner(tenants, foreign)
    fields = (
        {"agent_name": "TS FOREIGN AGENT", "inbound": False}
        if model is AgentDefinition
        else {
            "name": "TS FOREIGN SIMULATOR",
            "prompt": "TS FOREIGN PROMPT",
            "initial_message": "TS FOREIGN INITIAL MESSAGE",
        }
    )
    agent = model.no_workspace_objects.create(
        organization=organization, workspace=workspace, **fields
    )
    # Base-model creation can supply a default workspace: retain the explicit
    # NULL control so it cannot accidentally become a same-workspace fixture.
    model.no_workspace_objects.filter(pk=agent.pk).update(workspace=workspace)
    return agent


SUMMARY_PATHS = ["/simulate/run-tests/", "/simulate/api/run-tests/"]


def _foreign_scenario(tenants, foreign):
    organization, workspace = _owner(tenants, foreign)
    scenario = Scenarios.no_workspace_objects.create(
        name="TS R2 FOREIGN SCENARIO",
        description="TS R2 FOREIGN DESCRIPTION",
        scenario_type="graph",
        organization=organization,
        workspace=workspace,
    )
    Scenarios.no_workspace_objects.filter(pk=scenario.pk).update(workspace=workspace)
    return scenario


@pytest.mark.parametrize("path", SUMMARY_PATHS)
@pytest.mark.parametrize("ambient_scope", ["empty", "workspace", "organization"])
def test_r2_summary_roots_use_request_workspace_before_pagination(
    tenants, monkeypatch, path, ambient_scope
):
    data = tenants.data
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        agent_definition=data.two.agent
    )
    other_workspace = RunTest.no_workspace_objects.create(
        name="TS R2 OTHER WORKSPACE RUN",
        organization=data.organization,
        workspace=data.w1,
        agent_definition=data.one.agent,
    )
    foreign_agent = _foreign_agent(tenants, "organization", AgentDefinition)
    other_organization = RunTest.no_workspace_objects.create(
        name="TS R2 OTHER ORGANIZATION RUN",
        organization=tenants.organization,
        workspace=tenants.workspace,
        agent_definition=foreign_agent,
    )
    ambient = {
        "empty": None,
        "workspace": data.w1,
        "organization": tenants.workspace,
    }[ambient_scope]
    loaded_runs = _loads(monkeypatch, RunTest)
    loaded_agents = _loads(monkeypatch, AgentDefinition)
    url = path + "?summary=true&limit=1"
    response = _route(data, url, ambient=ambient)
    row = _run_row(response, data.two.run)
    assert str(row["agent_definition_detail"]["id"]) == str(data.two.agent.pk)
    assert set(loaded_runs) == {data.two.run.pk}
    assert data.one.agent.pk not in loaded_agents
    assert foreign_agent.pk not in loaded_agents
    encoded = json.dumps(response.data, default=str)
    for hidden in (data.one.run, other_workspace, other_organization):
        assert str(hidden.pk) not in encoded
        assert hidden.name not in encoded
    for hidden in (data.one.agent, foreign_agent):
        assert str(hidden.pk) not in encoded
        assert hidden.agent_name not in encoded

    # The full paginated response, including totals, must be unaffected by
    # other tenants' roots, not merely contain the requested local row.
    RunTest.no_workspace_objects.filter(
        pk__in=[data.one.run.pk, other_workspace.pk, other_organization.pk]
    ).update(deleted=True)
    missing = _route(data, url, ambient=ambient)
    assert missing.status_code == response.status_code == 200
    assert response.data == missing.data


@pytest.mark.parametrize("path", SUMMARY_PATHS)
@pytest.mark.parametrize("foreign", ["organization", "workspace", "null-workspace"])
@pytest.mark.parametrize("ambient_scope", ["empty", "workspace"])
def test_r2_summary_scenarios_are_scoped_before_hydration(
    tenants, monkeypatch, path, foreign, ambient_scope
):
    data = tenants.data
    scenario = _foreign_scenario(tenants, foreign)
    data.two.run.scenarios.add(scenario)
    loaded = _loads(monkeypatch, Scenarios)
    ambient = data.w1 if ambient_scope == "workspace" else None
    response = _route(data, path + "?summary=true", ambient=ambient)
    row = _run_row(response, data.two.run)
    assert [str(item["id"]) for item in row["scenarios_detail"]] == [
        str(data.two.scenario.pk)
    ]
    detail = row["scenarios_detail"][0]
    assert set(detail) == {
        "id",
        "name",
        "description",
        "scenario_type",
        "scenario_type_display",
        "dataset_rows",
    }
    assert detail["name"] == data.two.scenario.name
    assert detail["description"] == data.two.scenario.description
    assert detail["dataset_rows"] == 0
    assert data.two.scenario.pk in loaded
    assert scenario.pk not in loaded
    encoded = json.dumps(response.data, default=str)
    for value in (str(scenario.pk), scenario.name, scenario.description):
        assert value not in encoded
    data.two.run.scenarios.remove(scenario)
    missing = _route(data, path + "?summary=true", ambient=ambient)
    assert response.status_code == missing.status_code == 200
    assert response.data == missing.data


@pytest.mark.parametrize("path", SUMMARY_PATHS)
@pytest.mark.parametrize("foreign", ["organization", "workspace", "null-workspace"])
def test_ts1_summary_foreign_agent_is_identical_to_null(
    tenants, monkeypatch, path, foreign
):
    data = tenants.data
    agent = _foreign_agent(tenants, foreign, AgentDefinition)
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        agent_definition=agent
    )
    loaded = _loads(monkeypatch, AgentDefinition)
    response = _route(data, path + "?summary=true")
    row = _run_row(response, data.two.run)
    assert row["agent_definition_detail"] is None
    assert agent.pk not in loaded
    assert str(agent.pk) not in json.dumps(response.data, default=str)
    assert agent.agent_name not in json.dumps(response.data, default=str)
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        agent_definition=None
    )
    missing = _route(data, path + "?summary=true")
    assert response.status_code == missing.status_code == 200
    assert row == _run_row(missing, data.two.run)


@pytest.mark.parametrize("path", SUMMARY_PATHS)
def test_ts1_summary_same_owner_keeps_card_shape(tenants, path):
    data = tenants.data
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        agent_definition=data.two.agent
    )
    row = _run_row(_route(data, path + "?summary=true"), data.two.run)
    assert set(row) == {
        "id",
        "name",
        "source_type",
        "source_type_display",
        "agent_definition_detail",
        "scenarios_detail",
        "evals_detail",
        "last_run_at",
    }
    assert set(row["agent_definition_detail"]) == {
        "id",
        "agent_name",
        "agent_type",
        "provider",
        "contact_number",
    }
    assert str(row["agent_definition_detail"]["id"]) == str(data.two.agent.pk)
    assert row["agent_definition_detail"]["agent_name"] == data.two.agent.agent_name


@pytest.mark.parametrize("path", SUMMARY_PATHS)
def test_ts1_summary_search_cannot_probe_foreign_agent_name(tenants, path):
    data = tenants.data
    agent = _foreign_agent(tenants, "organization", AgentDefinition)
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        agent_definition=agent
    )
    response = _route(data, path + "?summary=true&search=TS%20FOREIGN%20AGENT")
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        agent_definition=None
    )
    missing = _route(data, path + "?summary=true&search=TS%20FOREIGN%20AGENT")
    assert response.status_code == missing.status_code == 200
    assert response.data == missing.data


def _prompt_path(data, route):
    path = f"/simulate/prompt-templates/{data.two.template.pk}/simulations/"
    return path if route == "list" else path + f"{data.two.run.pk}/"


def _prompt_response(data, route):
    return _route(
        data,
        _prompt_path(data, route),
        method="patch" if route == "patch" else "get",
        payload={"name": "TS edited local run"} if route == "patch" else None,
    )


@pytest.mark.parametrize("route", ["list", "get", "patch"])
@pytest.mark.parametrize("foreign", ["organization", "workspace", "null-workspace"])
def test_ts2_prompt_simulator_is_hidden_before_hydration(
    tenants, monkeypatch, route, foreign
):
    data = tenants.data
    agent = _foreign_agent(tenants, foreign, SimulatorAgent)
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        simulator_agent=agent
    )
    loaded = _loads(monkeypatch, SimulatorAgent)
    response = _prompt_response(data, route)
    row = _run_row(response, data.two.run)
    assert row["simulator_agent"] is None
    assert row["simulator_agent_detail"] is None
    assert agent.pk not in loaded
    encoded = json.dumps(response.data, default=str)
    for value in (str(agent.pk), agent.prompt, agent.initial_message):
        assert value not in encoded
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(simulator_agent=None)
    missing = _prompt_response(data, route)
    missing_row = _run_row(missing, data.two.run)
    # A successful PATCH advances updated_at in both controls.
    assert {k: v for k, v in row.items() if k != "updated_at"} == {
        k: v for k, v in missing_row.items() if k != "updated_at"
    }


@pytest.mark.parametrize("route", ["list", "get", "patch"])
def test_ts2_prompt_same_owner_keeps_simulator_shape(tenants, route):
    data = tenants.data
    SimulatorAgent.no_workspace_objects.filter(pk=data.two.simulator.pk).update(
        initial_message="TS local initial message"
    )
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        simulator_agent=data.two.simulator
    )
    row = _run_row(_prompt_response(data, route), data.two.run)
    assert str(row["simulator_agent"]) == str(data.two.simulator.pk)
    detail = row["simulator_agent_detail"]
    assert str(detail["id"]) == str(data.two.simulator.pk)
    assert detail["prompt"] == data.two.simulator.prompt
    assert detail["initial_message"] == "TS local initial message"


def test_ts2_patch_reload_rechecks_simulator_after_save(tenants, monkeypatch):
    data = tenants.data
    agent = _foreign_agent(tenants, "organization", SimulatorAgent)
    original_save = RunTest.save

    def save_then_mix(instance, *args, **kwargs):
        result = original_save(instance, *args, **kwargs)
        if instance.pk == data.two.run.pk:
            RunTest.no_workspace_objects.filter(pk=instance.pk).update(
                simulator_agent=agent
            )
        return result

    monkeypatch.setattr(RunTest, "save", save_then_mix)
    loaded = _loads(monkeypatch, SimulatorAgent)
    row = _run_row(_prompt_response(data, "patch"), data.two.run)
    assert row["simulator_agent"] is None
    assert row["simulator_agent_detail"] is None
    assert agent.pk not in loaded
    assert (
        RunTest.no_workspace_objects.get(pk=data.two.run.pk).simulator_agent_id
        == agent.pk
    )


@pytest.fixture
def call(tenants):
    data = tenants.data
    execution = SimulationTestExecution.objects.create(
        run_test=data.two.run,
        status=SimulationTestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
    )
    return CallExecution.objects.create(
        test_execution=execution,
        scenario=data.two.scenario,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
    )


def _graph(scenario, organization, marker):
    return ScenarioGraph.objects.create(
        scenario=scenario,
        organization=organization,
        name=marker,
        is_active=True,
        graph_config={"graph_data": {"marker": marker}},
    )


def _graph_response(data, call, route):
    if route == "detail":
        return _route(data, f"/simulate/call-executions/{call.pk}/")
    return _route(data, f"/simulate/test-executions/{call.test_execution_id}/kpis/")


def _graph_fields(response, call, route):
    assert response.status_code == 200, response.data
    if route == "detail":
        body = response.data.get("result", response.data)
        return body["scenario_graph"], body["scenario_graph_id"]
    return response.data["scenario_graphs"][str(call.scenario_id)], None


@pytest.mark.parametrize("route", ["detail", "kpis"])
def test_ts3_ts4_foreign_graph_matches_missing_graph(tenants, call, monkeypatch, route):
    data = tenants.data
    foreign = _graph(data.two.scenario, tenants.organization, "TS FOREIGN GRAPH")
    loaded = _loads(monkeypatch, ScenarioGraph)
    response = _graph_response(data, call, route)
    assert _graph_fields(response, call, route) == ({}, None)
    assert foreign.pk not in loaded
    assert "TS FOREIGN GRAPH" not in json.dumps(response.data, default=str)
    assert str(foreign.pk) not in json.dumps(response.data, default=str)
    ScenarioGraph.objects.filter(pk=foreign.pk).update(deleted=True)
    missing = _graph_response(data, call, route)
    assert _graph_fields(response, call, route) == _graph_fields(missing, call, route)
    assert response.data == missing.data


@pytest.mark.parametrize("route", ["detail", "kpis"])
def test_ts3_ts4_latest_owned_graph_wins_over_newer_foreign(
    tenants, call, monkeypatch, route
):
    data = tenants.data
    local = _graph(data.two.scenario, data.organization, "TS LOCAL GRAPH")
    foreign = _graph(data.two.scenario, tenants.organization, "TS FOREIGN GRAPH")
    loaded = _loads(monkeypatch, ScenarioGraph)
    response = _graph_response(data, call, route)
    graph_data, graph_id = _graph_fields(response, call, route)
    assert graph_data == local.graph_config["graph_data"]
    if route == "detail":
        assert graph_id == str(local.pk)
    assert foreign.pk not in loaded
    assert local.pk in loaded
    assert "TS FOREIGN GRAPH" not in json.dumps(response.data, default=str)


@pytest.mark.parametrize("route", ["detail", "kpis"])
def test_ts3_ts4_graph_must_be_in_caller_workspace(tenants, call, monkeypatch, route):
    data = tenants.data
    graph = _graph(data.one.scenario, data.organization, "TS OTHER WORKSPACE GRAPH")
    CallExecution.objects.filter(pk=call.pk).update(scenario=data.one.scenario)
    call.scenario_id = data.one.scenario.pk
    loaded = _loads(monkeypatch, ScenarioGraph)
    loaded_scenarios = _loads(monkeypatch, Scenarios)
    response = _graph_response(data, call, route)
    assert _graph_fields(response, call, route) == ({}, None)
    assert graph.pk not in loaded
    if route == "detail":
        encoded = json.dumps(response.data, default=str)
        assert str(data.one.scenario.pk) not in encoded
        assert data.one.scenario.name not in encoded
        assert data.one.scenario.pk not in loaded_scenarios


@pytest.mark.parametrize("foreign", ["organization", "workspace", "null-workspace"])
def test_r2_call_detail_foreign_scenario_is_nullable_without_hydration(
    tenants, call, monkeypatch, foreign
):
    data = tenants.data
    scenario = _foreign_scenario(tenants, foreign)
    graph = _graph(scenario, scenario.organization, "TS R2 FOREIGN SCENARIO GRAPH")
    CallExecution.objects.filter(pk=call.pk).update(scenario=scenario)
    loaded_scenarios = _loads(monkeypatch, Scenarios)
    loaded_graphs = _loads(monkeypatch, ScenarioGraph)
    response = _graph_response(data, call, "detail")
    assert response.status_code == 200, response.data
    body = response.data.get("result", response.data)
    assert body["scenario"] is None
    assert body["scenario_id"] is None
    assert body["scenario_graph"] == {}
    assert body["scenario_graph_id"] is None
    assert scenario.pk not in loaded_scenarios
    assert graph.pk not in loaded_graphs
    encoded = json.dumps(response.data, default=str)
    for value in (str(scenario.pk), scenario.name, scenario.description, str(graph.pk)):
        assert value not in encoded
    assert "TS R2 FOREIGN SCENARIO GRAPH" not in encoded
    assert CallExecution.objects.get(pk=call.pk).scenario_id == scenario.pk


def test_r2_call_detail_same_owner_keeps_scenario_name_and_string_id(tenants, call):
    data = tenants.data
    response = _graph_response(data, call, "detail")
    assert response.status_code == 200, response.data
    body = response.data.get("result", response.data)
    assert body["scenario"] == data.two.scenario.name
    assert body["scenario_id"] == str(data.two.scenario.pk)
    assert isinstance(body["scenario"], str)
    assert isinstance(body["scenario_id"], str)
    assert CallExecution.objects.get(pk=call.pk).scenario_id == data.two.scenario.pk


@pytest.fixture
def legacy_default_call(tenants, call):
    data = tenants.data
    workspace, _ = Workspace.no_workspace_objects.get_or_create(
        organization=data.organization,
        is_default=True,
        defaults={"name": "TS R3 default workspace", "created_by": data.user},
    )
    scenario = Scenarios.no_workspace_objects.create(
        name="TS R3 default scenario",
        scenario_type="graph",
        organization=data.organization,
        workspace=workspace,
    )
    # Use update so automatic default assignment cannot erase the legacy NULL.
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(workspace=None)
    CallExecution.objects.filter(pk=call.pk).update(scenario=scenario)
    call.scenario_id = scenario.pk
    return SimpleNamespace(call=call, scenario=scenario, workspace=workspace)


@pytest.mark.parametrize("ambient_scope", ["empty", "workspace", "organization"])
def test_r3_legacy_call_detail_default_workspace_preserves_scenario(
    tenants, legacy_default_call, monkeypatch, ambient_scope
):
    data = tenants.data
    legacy = legacy_default_call
    assert legacy.workspace.is_default
    assert RunTest.no_workspace_objects.get(pk=data.two.run.pk).workspace_id is None
    ambient = {
        "empty": None,
        "workspace": data.w2,
        "organization": tenants.workspace,
    }[ambient_scope]
    loaded = _loads(monkeypatch, Scenarios)
    path = f"/simulate/call-executions/{legacy.call.pk}/"
    response = _route(data, path, workspace=legacy.workspace, ambient=ambient)
    assert response.status_code == 200, response.data
    body = response.data.get("result", response.data)
    assert body["scenario"] == legacy.scenario.name
    assert body["scenario_id"] == str(legacy.scenario.pk)
    assert isinstance(body["scenario"], str)
    assert isinstance(body["scenario_id"], str)
    assert loaded == []
    assert (
        CallExecution.objects.get(pk=legacy.call.pk).scenario_id == legacy.scenario.pk
    )
    # The full response must match the explicitly scoped same-owner control.
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        workspace=legacy.workspace
    )
    explicit = _route(data, path, workspace=legacy.workspace, ambient=ambient)
    assert explicit.status_code == response.status_code == 200
    assert explicit.data == response.data


@pytest.mark.parametrize("foreign", ["organization", "workspace", "null-workspace"])
def test_r3_legacy_call_detail_default_workspace_hides_foreign_scenario(
    tenants, legacy_default_call, monkeypatch, foreign
):
    data = tenants.data
    legacy = legacy_default_call
    scenario = (
        data.two.scenario
        if foreign == "workspace"
        else _foreign_scenario(tenants, foreign)
    )
    if foreign == "organization":
        # Same workspace ID isolates the organization predicate from workspace.
        Scenarios.no_workspace_objects.filter(pk=scenario.pk).update(
            workspace=legacy.workspace
        )
    graph = _graph(scenario, scenario.organization, "TS R3 FOREIGN GRAPH")
    CallExecution.objects.filter(pk=legacy.call.pk).update(scenario=scenario)
    loaded_scenarios = _loads(monkeypatch, Scenarios)
    loaded_graphs = _loads(monkeypatch, ScenarioGraph)
    response = _route(
        data,
        f"/simulate/call-executions/{legacy.call.pk}/",
        workspace=legacy.workspace,
    )
    assert response.status_code == 200, response.data
    body = response.data.get("result", response.data)
    assert body["scenario"] is None
    assert body["scenario_id"] is None
    assert body["scenario_graph"] == {}
    assert body["scenario_graph_id"] is None
    assert scenario.pk not in loaded_scenarios
    assert graph.pk not in loaded_graphs
    encoded = json.dumps(response.data, default=str)
    for value in (str(scenario.pk), scenario.name, str(graph.pk), graph.name):
        assert value not in encoded
    assert CallExecution.objects.get(pk=legacy.call.pk).scenario_id == scenario.pk


@pytest.mark.parametrize("scenario_workspace", ["default", "non-default"])
def test_r3_legacy_call_detail_non_default_request_cannot_admit_root(
    tenants, legacy_default_call, monkeypatch, scenario_workspace
):
    data = tenants.data
    legacy = legacy_default_call
    assert not data.w2.is_default
    if scenario_workspace == "non-default":
        CallExecution.objects.filter(pk=legacy.call.pk).update(
            scenario=data.two.scenario
        )
    loaded = _loads(monkeypatch, Scenarios)
    response = _route(data, f"/simulate/call-executions/{legacy.call.pk}/")
    assert response.status_code == 404, response.data
    assert loaded == []
    encoded = json.dumps(response.data, default=str)
    for scenario in (legacy.scenario, data.two.scenario):
        assert str(scenario.pk) not in encoded
        assert scenario.name not in encoded


@pytest.mark.parametrize("foreign_run", [False, True])
def test_r3_legacy_call_projection_keeps_run_organization_floor(
    tenants, legacy_default_call, foreign_run, django_assert_num_queries
):
    from simulate.utils.owned_relations import owned_call_scenario_map

    data = tenants.data
    legacy = legacy_default_call
    if foreign_run:
        RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
            organization=tenants.organization
        )
    with django_assert_num_queries(1):
        projection = owned_call_scenario_map(
            [legacy.call.pk],
            caller_tenant=(data.organization.pk, legacy.workspace.pk),
        )
    assert projection == (
        {}
        if foreign_run
        else {
            str(legacy.call.pk): {
                "id": str(legacy.scenario.pk),
                "name": legacy.scenario.name,
            }
        }
    )
    if foreign_run:
        response = _route(
            data,
            f"/simulate/call-executions/{legacy.call.pk}/",
            workspace=legacy.workspace,
        )
        assert response.status_code == 404, response.data


@pytest.mark.parametrize("scenario_workspace", ["default", "non-default", "null"])
def test_r3_legacy_call_standalone_projection_requires_exact_null_workspace(
    tenants,
    legacy_default_call,
    scenario_workspace,
    monkeypatch,
    django_assert_num_queries,
):
    from simulate.serializers.test_execution import CallExecutionDetailSerializer

    data = tenants.data
    legacy = legacy_default_call
    workspace = {
        "default": legacy.workspace,
        "non-default": data.w2,
        "null": None,
    }[scenario_workspace]
    Scenarios.no_workspace_objects.filter(pk=legacy.scenario.pk).update(
        workspace=workspace
    )
    loaded = _loads(monkeypatch, Scenarios)
    serializer = CallExecutionDetailSerializer(legacy.call)
    with django_assert_num_queries(1):
        assert serializer.get_scenario(legacy.call) == (
            legacy.scenario.name if workspace is None else None
        )
        assert serializer.get_scenario_id(legacy.call) == (
            str(legacy.scenario.pk) if workspace is None else None
        )
    assert loaded == []


@pytest.mark.parametrize("scenario_has_workspace", [True, False])
def test_r3_legacy_call_missing_caller_scope_never_wildcards(
    tenants, legacy_default_call, scenario_has_workspace, django_assert_num_queries
):
    from simulate.utils.owned_relations import owned_call_scenario_map

    data = tenants.data
    legacy = legacy_default_call
    if not scenario_has_workspace:
        Scenarios.no_workspace_objects.filter(pk=legacy.scenario.pk).update(
            workspace=None
        )
    with django_assert_num_queries(0):
        assert (
            owned_call_scenario_map(
                [legacy.call.pk], caller_tenant=(None, legacy.workspace.pk)
            )
            == {}
        )
        assert (
            owned_call_scenario_map([legacy.call.pk], caller_tenant=(None, None)) == {}
        )
    with django_assert_num_queries(1):
        projection = owned_call_scenario_map(
            [legacy.call.pk], caller_tenant=(data.organization.pk, None)
        )
    assert projection == (
        {}
        if scenario_has_workspace
        else {
            str(legacy.call.pk): {
                "id": str(legacy.scenario.pk),
                "name": legacy.scenario.name,
            }
        }
    )


def test_r2_call_scenario_projection_is_batched_and_never_hydrates_scenarios(
    tenants, call, monkeypatch, django_assert_num_queries
):
    from simulate.serializers.test_execution import CallExecutionDetailSerializer

    data = tenants.data
    calls = [call]
    for _ in range(3):
        calls.append(
            CallExecution.objects.create(
                test_execution=call.test_execution,
                scenario=data.two.scenario,
                status=CallExecution.CallStatus.COMPLETED,
                simulation_call_type=CallExecution.SimulationCallType.TEXT,
            )
        )
    foreign = _foreign_scenario(tenants, "workspace")
    CallExecution.objects.filter(pk=calls[-1].pk).update(scenario=foreign)
    calls[-1].scenario_id = foreign.pk
    loaded = _loads(monkeypatch, Scenarios)
    request = SimpleNamespace(organization=data.organization, workspace=data.w2)
    serializer = CallExecutionDetailSerializer(
        calls, many=True, context={"request": request}
    )
    with django_assert_num_queries(1):
        for item in calls[:-1]:
            assert serializer.child.get_scenario(item) == data.two.scenario.name
            assert serializer.child.get_scenario_id(item) == str(data.two.scenario.pk)
        assert serializer.child.get_scenario(calls[-1]) is None
        assert serializer.child.get_scenario_id(calls[-1]) is None
    assert loaded == []
    with django_assert_num_queries(0):
        assert serializer.child.get_scenario(calls[-1]) is None
        assert serializer.child.get_scenario_id(calls[-1]) is None


def test_r2_call_scenario_projection_reuses_single_reader_without_stale_absence(
    tenants, call, django_assert_num_queries
):
    from simulate.serializers.test_execution import CallExecutionDetailSerializer

    data = tenants.data
    other = CallExecution.objects.create(
        test_execution=call.test_execution,
        scenario=data.two.scenario,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
    )
    request = SimpleNamespace(organization=data.organization, workspace=data.w2)
    serializer = CallExecutionDetailSerializer(context={"request": request})
    for item in (call, other):
        with django_assert_num_queries(1):
            assert serializer.get_scenario(item) == data.two.scenario.name
            assert serializer.get_scenario_id(item) == str(data.two.scenario.pk)
    empty_page = CallExecutionDetailSerializer(
        context={"request": request, "owned_call_scenarios": {}}
    )
    with django_assert_num_queries(0):
        assert empty_page.get_scenario(call) is None
        assert empty_page.get_scenario_id(call) is None


def test_ts1_summary_agent_join_is_batched_and_serialization_query_free(
    tenants, django_assert_num_queries
):
    from simulate.serializers.run_test import RunTestListSummarySerializer
    from simulate.views.run_test import _run_test_summary_queryset

    data = tenants.data
    for index in range(4):
        RunTest.no_workspace_objects.create(
            name=f"TS batch {index}",
            organization=data.organization,
            workspace=data.w2,
            agent_definition=data.two.agent,
        )
    with CaptureQueriesContext(connection) as queries:
        rows = list(
            _run_test_summary_queryset(
                RunTest.no_workspace_objects.filter(
                    organization=data.organization, workspace=data.w2
                ),
                organization_id=data.organization.pk,
                workspace_id=data.w2.pk,
            )
        )
    agent_queries = [
        query
        for query in queries.captured_queries
        if AgentDefinition._meta.db_table in query["sql"]
    ]
    assert len(agent_queries) == 1
    with django_assert_num_queries(0):
        assert len(RunTestListSummarySerializer(rows, many=True).data) == len(rows)


def test_ts2_simulator_join_is_batched_and_keeps_null_workspace_runs(
    tenants, django_assert_num_queries
):
    from simulate.utils.owned_relations import (
        owned_run_for_serialization,
        with_owned_run_relation,
    )

    data = tenants.data
    run = RunTest.no_workspace_objects.create(
        name="TS legacy run",
        organization=data.organization,
        workspace=data.w2,
        simulator_agent=data.two.simulator,
    )
    RunTest.no_workspace_objects.filter(pk=run.pk).update(workspace=None)
    with django_assert_num_queries(1):
        rows = list(
            with_owned_run_relation(
                RunTest.no_workspace_objects.filter(pk=run.pk), "simulator_agent"
            )
        )
    with django_assert_num_queries(0):
        represented = owned_run_for_serialization(rows[0], "simulator_agent")
        assert represented.simulator_agent_id == data.two.simulator.pk
        assert represented.simulator_agent.prompt == data.two.simulator.prompt
    assert rows[0].simulator_agent_id == data.two.simulator.pk


def test_ts3_graph_fields_share_one_scoped_query(
    tenants, call, django_assert_num_queries
):
    from simulate.serializers.test_execution import CallExecutionDetailSerializer

    data = tenants.data
    local = _graph(data.two.scenario, data.organization, "TS LOCAL GRAPH")
    request = SimpleNamespace(organization=data.organization, workspace=data.w2)
    serializer = CallExecutionDetailSerializer(context={"request": request})
    with django_assert_num_queries(1):
        assert serializer.get_scenario_graph(call) == local.graph_config["graph_data"]
        assert serializer.get_scenario_graph_id(call) == str(local.pk)


def test_r4_detail_scoped_scenario_stays_inside_existing_query_guard(
    world, tables, monkeypatch
):
    from simulate.serializers.test_execution import CallExecutionDetailSerializer
    from simulate.tests import test_scenario_row_org_scope as row_scope

    class ScenarioProjectionSerializer(CallExecutionDetailSerializer):
        class Meta(CallExecutionDetailSerializer.Meta):
            fields = [
                "id",
                "scenario",
                "scenario_id",
                "scenario_graph",
                "scenario_graph_id",
            ]

    # Reuse the pre-existing guard without permitting any additional model.
    # Its canned scenario represents the scalar annotations of the scenario
    # query; the real-ORM controls below verify those annotations against storage.
    scenario = world.graph_scenario
    scenario.workspace = world.workspace
    scenario._owned_graph_id = scenario.pk
    scenario._owned_graph_config = {"graph_data": {"marker": "TS R4 LOCAL GRAPH"}}
    world.execution.scenario_ids = [str(scenario.pk)]
    tables.canned[Scenarios] = [scenario]
    allowed_models = set(tables.canned) | set(tables.records) | {CallExecution}
    assert ScenarioGraph not in allowed_models
    queried_models = []
    original_answer = tables.answer

    def answer(queryset):
        queried_models.append(queryset.model)
        return original_answer(queryset)

    monkeypatch.setattr(tables, "answer", answer)
    monkeypatch.setattr(
        row_scope, "ScenarioFieldsSerializer", ScenarioProjectionSerializer
    )
    call = world.make_call(scenario=scenario)
    response = row_scope._get_test_execution_response(world, tables, [call])
    assert response["results"] == [
        {
            "id": str(call.pk),
            "scenario": scenario.name,
            "scenario_id": str(scenario.pk),
            "scenario_graph": {"marker": "TS R4 LOCAL GRAPH"},
            "scenario_graph_id": str(scenario.pk),
        }
    ]
    assert Scenarios in queried_models
    assert set(queried_models) <= allowed_models


def _forbid_separate_graph_queries(monkeypatch):
    from django.db.models.query import QuerySet

    original = QuerySet._fetch_all

    def fetch_all(queryset):
        assert queryset.model is not ScenarioGraph, "unexpected query on ScenarioGraph"
        return original(queryset)

    monkeypatch.setattr(QuerySet, "_fetch_all", fetch_all)


@pytest.mark.parametrize("scenario_state", ["snapshot", "call-only", "historical"])
def test_r4_detail_projects_owned_graph_in_scenario_query(
    tenants, call, monkeypatch, scenario_state
):
    data = tenants.data
    local = _graph(data.two.scenario, data.organization, "TS R4 LOCAL GRAPH")
    foreign = _graph(data.two.scenario, tenants.organization, "TS R4 FOREIGN GRAPH")
    scenario_ids = [] if scenario_state == "call-only" else [str(data.two.scenario.pk)]
    SimulationTestExecution.objects.filter(pk=call.test_execution_id).update(
        scenario_ids=scenario_ids
    )
    if scenario_state == "historical":
        Scenarios.no_workspace_objects.filter(pk=data.two.scenario.pk).update(
            deleted=True
        )
    for _ in range(2):
        CallExecution.objects.create(
            test_execution_id=call.test_execution_id,
            scenario=data.two.scenario,
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
        )
    loaded_scenarios = _loads(monkeypatch, Scenarios)
    loaded_graphs = _loads(monkeypatch, ScenarioGraph)
    _forbid_separate_graph_queries(monkeypatch)
    response = _route(
        data, f"/simulate/test-executions/{call.test_execution_id}/", ambient=data.w1
    )
    assert response.status_code == 200, response.data
    assert len(response.data["results"]) == 3
    for row in response.data["results"]:
        assert row["scenario"] == data.two.scenario.name
        assert row["scenario_id"] == str(data.two.scenario.pk)
        assert row["scenario_graph"] == local.graph_config["graph_data"]
        assert row["scenario_graph_id"] == str(local.pk)
    assert loaded_scenarios == [data.two.scenario.pk]
    assert loaded_graphs == []
    assert str(foreign.pk) not in json.dumps(response.data, default=str)
    assert "TS R4 FOREIGN GRAPH" not in json.dumps(response.data, default=str)


@pytest.mark.parametrize("foreign", ["organization", "workspace", "null-workspace"])
def test_r4_detail_foreign_scenario_is_null_without_graph_query(
    tenants, call, monkeypatch, foreign
):
    data = tenants.data
    scenario = _foreign_scenario(tenants, foreign)
    graph = _graph(scenario, scenario.organization, "TS R4 FOREIGN SCENARIO GRAPH")
    CallExecution.objects.filter(pk=call.pk).update(scenario=scenario)
    SimulationTestExecution.objects.filter(pk=call.test_execution_id).update(
        scenario_ids=[str(scenario.pk)]
    )
    loaded_scenarios = _loads(monkeypatch, Scenarios)
    _forbid_separate_graph_queries(monkeypatch)
    response = _route(data, f"/simulate/test-executions/{call.test_execution_id}/")
    assert response.status_code == 200, response.data
    (row,) = response.data["results"]
    assert row["scenario"] is None
    assert row["scenario_id"] is None
    assert row["scenario_graph"] == {}
    assert row["scenario_graph_id"] is None
    assert scenario.pk not in loaded_scenarios
    encoded = json.dumps(response.data, default=str)
    for value in (str(scenario.pk), scenario.name, str(graph.pk), graph.name):
        assert value not in encoded


@pytest.mark.parametrize("default_request", [True, False])
def test_r4_detail_legacy_default_workspace_keeps_projection_and_root_guard(
    tenants, legacy_default_call, monkeypatch, default_request
):
    data = tenants.data
    legacy = legacy_default_call
    local = _graph(legacy.scenario, data.organization, "TS R4 LEGACY GRAPH")
    SimulationTestExecution.objects.filter(pk=legacy.call.test_execution_id).update(
        scenario_ids=[str(legacy.scenario.pk)]
    )
    _forbid_separate_graph_queries(monkeypatch)
    response = _route(
        data,
        f"/simulate/test-executions/{legacy.call.test_execution_id}/",
        workspace=legacy.workspace if default_request else data.w2,
        ambient=tenants.workspace,
    )
    assert response.status_code == (200 if default_request else 404), response.data
    if default_request:
        (row,) = response.data["results"]
        assert row["scenario"] == legacy.scenario.name
        assert row["scenario_id"] == str(legacy.scenario.pk)
        assert row["scenario_graph"] == local.graph_config["graph_data"]
        assert row["scenario_graph_id"] == str(local.pk)
    else:
        assert str(legacy.scenario.pk) not in json.dumps(response.data, default=str)


@pytest.mark.parametrize("scenario_source", ["snapshot", "call-only"])
@pytest.mark.parametrize("ambient_scope", ["empty", "workspace", "organization"])
def test_r5_historical_detail_scopes_scenarios_before_hydration(
    tenants, call, monkeypatch, scenario_source, ambient_scope
):
    data = tenants.data
    local = _graph(data.two.scenario, data.organization, "TS R5 LOCAL GRAPH")
    newer_foreign = _graph(
        data.two.scenario, tenants.organization, "TS R5 FOREIGN GRAPH"
    )
    hidden_scenarios = []
    hidden_graphs = [newer_foreign]
    for foreign in ("organization", "workspace", "null-workspace"):
        scenario = _foreign_scenario(tenants, foreign)
        if foreign == "organization":
            # Isolate the organization predicate from the workspace predicate.
            Scenarios.no_workspace_objects.filter(pk=scenario.pk).update(
                workspace=data.w2
            )
        hidden_scenarios.append(scenario)
        hidden_graphs.append(
            _graph(scenario, scenario.organization, "TS R5 FOREIGN SCENARIO GRAPH")
        )
        CallExecution.objects.create(
            test_execution_id=call.test_execution_id,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
        )
    scenario_ids = [str(data.two.scenario.pk)] + [
        str(scenario.pk) for scenario in hidden_scenarios
    ]
    SimulationTestExecution.objects.filter(pk=call.test_execution_id).update(
        scenario_ids=scenario_ids if scenario_source == "snapshot" else []
    )
    Scenarios.no_workspace_objects.filter(pk__in=scenario_ids).update(deleted=True)
    loaded_scenarios = _loads(monkeypatch, Scenarios)
    loaded_graphs = _loads(monkeypatch, ScenarioGraph)
    _forbid_separate_graph_queries(monkeypatch)
    ambient = {
        "empty": None,
        "workspace": data.w1,
        "organization": tenants.workspace,
    }[ambient_scope]
    response = _route(
        data,
        f"/simulate/test-executions/{call.test_execution_id}/",
        ambient=ambient,
    )
    assert response.status_code == 200, response.data
    assert len(response.data["results"]) == 4
    assert str(call.pk) in {str(row["id"]) for row in response.data["results"]}
    for row in response.data["results"]:
        if str(row["id"]) == str(call.pk):
            assert row["scenario"] == data.two.scenario.name
            assert row["scenario_id"] == str(data.two.scenario.pk)
            assert row["scenario_graph"] == local.graph_config["graph_data"]
            assert row["scenario_graph_id"] == str(local.pk)
        else:
            assert row["scenario"] is None
            assert row["scenario_id"] is None
            assert row["scenario_graph"] == {}
            assert row["scenario_graph_id"] is None
    assert loaded_scenarios == [data.two.scenario.pk]
    assert loaded_graphs == []
    encoded = json.dumps(response.data, default=str)
    for scenario in hidden_scenarios:
        for value in (str(scenario.pk), scenario.name, scenario.description):
            assert value not in encoded
    for graph in hidden_graphs:
        assert str(graph.pk) not in encoded
        assert graph.name not in encoded


@pytest.mark.parametrize("path", SUMMARY_PATHS)
def test_r5_summary_keeps_soft_deleted_scenarios_hidden(tenants, monkeypatch, path):
    data = tenants.data
    Scenarios.no_workspace_objects.filter(pk=data.two.scenario.pk).update(deleted=True)
    loaded_scenarios = _loads(monkeypatch, Scenarios)
    response = _route(data, path + "?summary=true", ambient=tenants.workspace)
    row = _run_row(response, data.two.run)
    assert row["scenarios_detail"] == []
    assert data.two.scenario.pk not in loaded_scenarios
    encoded = json.dumps(response.data, default=str)
    assert str(data.two.scenario.pk) not in encoded
    assert data.two.scenario.name not in encoded
    data.two.run.scenarios.remove(data.two.scenario)
    missing = _route(data, path + "?summary=true", ambient=tenants.workspace)
    assert response.status_code == missing.status_code == 200
    assert response.data == missing.data


def test_p2_graph_helper_preserves_missing_semantics(tenants):
    from simulate.utils.owned_relations import owned_scenario_graphs

    data = tenants.data
    foreign = _graph(data.two.scenario, tenants.organization, "TS FOREIGN TASK GRAPH")
    graphs = owned_scenario_graphs(
        organization_id=data.organization.pk, workspace_id=data.w2.pk
    )
    with pytest.raises(ScenarioGraph.DoesNotExist):
        graphs.get(scenario=data.two.scenario)
    assert not graphs.filter(pk=foreign.pk).exists()
    local = _graph(data.two.scenario, data.organization, "TS LOCAL TASK GRAPH")
    assert graphs.get(scenario=data.two.scenario).pk == local.pk
