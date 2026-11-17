"""T1/T2/T7/T9 tenancy regressions using real views, serializers and ORM.

Written before the production overlay. External sandbox transport alone is
mocked. Django execution is intentionally not part of this writer packet.
The submit/read lifecycle probe also covers missing sandbox ownership storage;
it must not be replaced by a manufactured ownership row to claim T9 closed.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import (
    AgentDefinition,
    CallExecution,
    HostedHarnessJob,
    RunTest,
    SandboxHarnessJob,
    Scenarios,
)
from simulate.models.agent_version import AgentVersion
from simulate.models.scenario_graph import ScenarioGraph
from simulate.models.test_execution import TestExecution as SimulationTestExecution
from simulate.serializers.run_test import RunTestSerializer
from simulate.services.branch_deviation_analyzer import BranchDeviationAnalyzer
from simulate.services.harness_provider import SandboxHarnessProvider
from simulate.tests.test_harness_provider import _v1_payload
from simulate.tests.test_simulate_views_workspace_scope_r1 import (
    _r3_grant_workspace_access,
    _request,
    _result,
)
from simulate.tests.test_simulate_views_workspace_scope_r1 import (
    scope_data as scope_data,
)
from simulate.views import prompt_simulation as prompt_views
from simulate.views import run_test as run_views
from simulate.views import scenarios as scenario_views
from simulate.views.harness_job import HarnessJobViewSet
from tfc.middleware.workspace_context import workspace_context
from tracer.models.project import Project
from tracer.models.replay_session import ReplaySession, ReplaySessionStep

pytestmark = pytest.mark.django_db


def _loads(monkeypatch, model):
    loaded = []
    original = model.from_db

    def observe(cls, db, field_names, values):
        obj = original(db, field_names, values)
        loaded.append(obj.pk)
        return obj

    monkeypatch.setattr(model, "from_db", classmethod(observe))
    return loaded


@pytest.fixture
def tenants(scope_data):
    data = scope_data
    foreign_org = Organization.objects.create(name="T129 foreign organization")
    foreign_workspace, _ = Workspace.no_workspace_objects.get_or_create(
        organization=foreign_org,
        is_default=True,
        defaults={"name": "T129 foreign workspace", "created_by": data.user},
    )
    template = PromptTemplate.no_workspace_objects.create(
        name="T129 foreign template",
        organization=foreign_org,
        workspace=foreign_workspace,
    )
    version = PromptVersion.objects.create(
        original_template=template,
        template_version="T129 FOREIGN VERSION",
        commit_message="T129 FOREIGN COMMIT",
        prompt_config_snapshot=[{"role": "system", "content": "T129 FOREIGN PROMPT"}],
    )
    scenario = Scenarios.no_workspace_objects.create(
        name="T129 foreign scenario",
        source="Synthetic T129 scenario source",
        organization=foreign_org,
        workspace=foreign_workspace,
        scenario_type="graph",
    )
    run = RunTest.no_workspace_objects.create(
        name="T129 foreign run",
        organization=foreign_org,
        workspace=foreign_workspace,
        source_type="prompt",
        prompt_template=template,
        prompt_version=version,
    )
    run.scenarios.add(scenario)
    return SimpleNamespace(
        data=data,
        organization=foreign_org,
        workspace=foreign_workspace,
        template=template,
        version=version,
        scenario=scenario,
        run=run,
    )


def _detail(data, method, run_id):
    payload = {"name": "T129 edited local run"} if method == "patch" else None
    return run_views.RunTestDetailView.as_view()(
        _request(data, method, payload, workspace=data.w2), run_test_id=run_id
    )


@pytest.mark.parametrize("method", ["get", "patch"])
@pytest.mark.parametrize("foreign", ["organization", "workspace", "mixed-version"])
def test_t1_foreign_and_missing_run_are_identical(
    tenants, monkeypatch, method, foreign
):
    data = tenants.data
    if foreign == "organization":
        run = tenants.run
    elif foreign == "workspace":
        run = data.one.run
    else:
        run = data.two.run
        RunTest.no_workspace_objects.filter(pk=run.pk).update(
            prompt_version=tenants.version
        )
    before = RunTest.no_workspace_objects.get(pk=run.pk).name
    loaded = _loads(monkeypatch, PromptVersion)
    response = _detail(data, method, run.pk)
    missing = _detail(data, method, uuid.uuid4())
    assert response.status_code == missing.status_code == 404
    assert response.data == missing.data
    assert tenants.version.pk not in loaded
    assert str(tenants.version.pk) not in json.dumps(response.data, default=str)
    assert RunTest.no_workspace_objects.get(pk=run.pk).name == before


@pytest.mark.parametrize("method", ["get", "patch"])
def test_t1_local_get_and_patch_keep_prompt_details(tenants, method):
    data = tenants.data
    response = _detail(data, method, data.two.run.pk)
    assert response.status_code == 200, response.data
    result = _result(response)
    assert str(result["prompt_version"]) == str(data.two.version.pk)
    assert result["prompt_version_detail"]["id"] == str(data.two.version.pk)
    data.two.run.refresh_from_db()
    if method == "patch":
        assert data.two.run.name == "T129 edited local run"


def test_t1_patch_reload_rechecks_version_after_write(tenants, monkeypatch):
    data = tenants.data
    original_save = RunTest.save

    def save_then_mix(instance, *args, **kwargs):
        result = original_save(instance, *args, **kwargs)
        if instance.pk == data.two.run.pk:
            RunTest.no_workspace_objects.filter(pk=instance.pk).update(
                prompt_version=tenants.version
            )
        return result

    monkeypatch.setattr(RunTest, "save", save_then_mix)
    loaded = _loads(monkeypatch, PromptVersion)
    response = _detail(data, "patch", data.two.run.pk)
    missing = _detail(data, "patch", uuid.uuid4())
    assert response.status_code == missing.status_code == 404
    assert response.data == missing.data
    assert tenants.version.pk not in loaded
    persisted = RunTest.no_workspace_objects.get(pk=data.two.run.pk)
    assert persisted.name == data.two.run.name
    assert persisted.prompt_version_id == data.two.version.pk


def test_t1_serializer_scopes_uncached_version_without_hydrating_foreign(
    tenants, monkeypatch
):
    data = tenants.data
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        prompt_version=tenants.version
    )
    run = RunTest.no_workspace_objects.get(pk=data.two.run.pk)
    loaded = _loads(monkeypatch, PromptVersion)
    result = RunTestSerializer(run).data
    assert result["prompt_version_detail"] is None
    assert result["prompt_version"] is None
    assert tenants.version.pk not in loaded
    assert tenants.version.commit_message not in json.dumps(result, default=str)


def test_t1_scoped_prefetch_keeps_version_details_query_free(
    tenants, django_assert_num_queries
):
    from simulate.views.prompt_simulation import _prompt_simulation_read_queryset

    data = tenants.data
    run = _prompt_simulation_read_queryset(
        RunTest.no_workspace_objects, data.organization, data.w2
    ).get(pk=data.two.run.pk)
    with django_assert_num_queries(0):
        assert RunTestSerializer().get_prompt_version_detail(run)["id"] == str(
            data.two.version.pk
        )


@pytest.mark.parametrize("method", ["get", "patch"])
@pytest.mark.parametrize("context_kind", ["header", "middleware"])
def test_t1_authorized_session_workspace_is_reused(tenants, method, context_kind):
    data = tenants.data
    _r3_grant_workspace_access(data)
    request = _request(data, method, {"name": "Authorized T129 edit"})
    if context_kind == "header":
        request.META["HTTP_X_WORKSPACE_ID"] = str(data.w2.pk)
    ambient = data.w2 if context_kind == "middleware" else None
    with workspace_context(ambient, organization=data.organization, user=data.user):
        response = run_views.RunTestDetailView.as_view()(
            request, run_test_id=data.two.run.pk
        )
    assert response.status_code == 200, response.data
    assert _result(response)["prompt_version_detail"]["id"] == str(data.two.version.pk)
    if method == "patch":
        data.two.run.refresh_from_db()
        assert data.two.run.name == "Authorized T129 edit"


@pytest.mark.parametrize("selection", ["agent_definition_id", "scenario_ids"])
@pytest.mark.parametrize("foreign", ["organization", "workspace", "deleted"])
def test_t1_patch_references_are_exactly_scoped_and_atomic(tenants, selection, foreign):
    data = tenants.data
    if selection == "agent_definition_id":
        model, field, local = AgentDefinition, "agent_definition_id", data.two.agent
        foreign_row = (
            AgentDefinition.no_workspace_objects.create(
                agent_name="Foreign T129 agent",
                organization=tenants.organization,
                workspace=tenants.workspace,
                inbound=False,
                description="Synthetic T129 agent",
            )
            if foreign == "organization"
            else data.one.agent
        )
    else:
        model, field, local = Scenarios, None, data.two.scenario
        foreign_row = (
            tenants.scenario if foreign == "organization" else data.one.scenario
        )
    if foreign == "deleted":
        foreign_row = local
        model.no_workspace_objects.filter(pk=local.pk).update(deleted=True)
    before = RunTest.no_workspace_objects.get(pk=data.two.run.pk)
    scenario_ids = set(before.scenarios.values_list("pk", flat=True))
    responses = []
    with workspace_context(None, organization=None, user=None):
        for pk in (foreign_row.pk, uuid.uuid4()):
            value = [str(data.two.scenario.pk), str(pk)] if field is None else str(pk)
            response = run_views.RunTestDetailView.as_view()(
                _request(
                    data,
                    "patch",
                    {"name": "Must roll back", selection: value},
                    workspace=data.w2,
                ),
                run_test_id=data.two.run.pk,
            )
            assert response.status_code == 404, response.data
            responses.append(response.data)
    assert responses[0] == responses[1]
    after = RunTest.no_workspace_objects.get(pk=before.pk)
    assert after.name == before.name
    assert after.agent_definition_id == before.agent_definition_id
    assert after.simulator_agent_id == before.simulator_agent_id
    assert set(after.scenarios.values_list("pk", flat=True)) == scenario_ids


def test_t1_patch_local_components_remain_successful(tenants):
    data = tenants.data
    with workspace_context(None, organization=None, user=None):
        response = run_views.RunTestDetailView.as_view()(
            _request(
                data,
                "patch",
                {
                    "agent_definition_id": str(data.two.agent.pk),
                    "scenario_ids": [str(data.two.scenario.pk)],
                },
                workspace=data.w2,
            ),
            run_test_id=data.two.run.pk,
        )
    assert response.status_code == 200, response.data
    data.two.run.refresh_from_db()
    assert data.two.run.agent_definition_id == data.two.agent.pk
    assert list(data.two.run.scenarios.values_list("pk", flat=True)) == [
        data.two.scenario.pk
    ]


@pytest.mark.parametrize("method", ["get", "patch"])
def test_t1_root_foreign_agent_is_not_hydrated(tenants, monkeypatch, method):
    data = tenants.data
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        agent_definition=data.one.agent
    )
    loaded = _loads(monkeypatch, AgentDefinition)
    with workspace_context(None, organization=None, user=None):
        response = _detail(data, method, data.two.run.pk)
        missing = _detail(data, method, uuid.uuid4())
    assert response.status_code == missing.status_code == 404
    assert response.data == missing.data
    assert data.one.agent.pk not in loaded


def _agent_version(agent, organization, workspace, number, name, status="draft"):
    # Explicit snapshot: AgentVersion.save() only builds one when it is empty.
    return AgentVersion.no_workspace_objects.create(
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
        version_number=number,
        status=status,
        version_name=f"T129 {name} version name",
        description=f"T129 {name} version",
        commit_message=f"T129 {name} commit",
        configuration_snapshot={"marker": f"T129 {name.upper()} SNAPSHOT"},
    )


def _version_owner(tenants, kind):
    data = tenants.data
    return {
        "foreign-organization": (tenants.organization, tenants.workspace),
        "foreign-workspace": (data.organization, data.w1),
        "local-control": (data.organization, data.w2),
    }[kind]


def _assert_version_hidden(version, loaded, response_data):
    encoded = json.dumps(response_data, default=str)
    assert version.pk not in loaded
    assert str(version.pk) not in encoded
    assert version.version_name not in encoded
    assert version.configuration_snapshot["marker"] not in encoded


def _components(data, run_id, payload):
    return run_views.RunTestComponentsUpdateView.as_view()(
        _request(data, "patch", payload, workspace=data.w2),
        run_test_id=run_id,
    )


@pytest.mark.parametrize("route", ["get", "patch", "components", "components-select"])
@pytest.mark.parametrize(
    "highest", ["foreign-organization", "foreign-workspace", "local-control"]
)
def test_t1_unpinned_local_agent_never_loads_foreign_versions(
    tenants, monkeypatch, route, highest
):
    # A mixed-FK child: the highest-numbered version under the LOCAL agent may
    # belong to another organization or workspace. The definition FK alone is
    # never ownership, so neither the batched reload, the nested agent detail's
    # active/latest pick, nor the automatic components selection may load it.
    data = tenants.data
    run = data.two.run
    RunTest.no_workspace_objects.filter(pk=run.pk).update(
        agent_definition=data.two.agent,
        agent_version=None,
    )
    local = _agent_version(data.two.agent, data.organization, data.w2, 1, "local")
    organization, workspace = _version_owner(tenants, highest)
    top = _agent_version(
        data.two.agent,
        organization,
        workspace,
        2,
        highest,
        status="active",
    )
    expected = top if highest == "local-control" else local
    loaded = _loads(monkeypatch, AgentVersion)
    with workspace_context(None, organization=None, user=None):
        if route in {"get", "patch"}:
            response = _detail(data, route, run.pk)
        else:
            payload = (
                {"agent_definition_id": str(data.two.agent.pk)}
                if route == "components-select"
                else {}
            )
            response = _components(data, run.pk, payload)
    assert response.status_code == 200, response.data
    result = _result(response)
    assert str(result["agent_version"]["id"]) == str(expected.pk)
    assert str(result["agent_definition_detail"]["id"]) == str(data.two.agent.pk)
    assert expected.pk in loaded
    if highest != "local-control":
        _assert_version_hidden(top, loaded, response.data)
    persisted = RunTest.no_workspace_objects.get(pk=run.pk)
    assert persisted.agent_definition_id == data.two.agent.pk
    if route == "components-select":
        # The automatic latest-version pin uses the same exact tenant scope.
        assert persisted.agent_version_id == expected.pk
    else:
        assert persisted.agent_version_id is None


def test_t1_version_prefetch_is_one_exact_tenant_query_per_page(tenants, monkeypatch):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    data = tenants.data
    local, foreign = [], []
    for index in range(3):
        agent = AgentDefinition.no_workspace_objects.create(
            agent_name=f"T129 page agent {index}",
            organization=data.organization,
            workspace=data.w2,
            inbound=False,
            description="Synthetic T129 agent",
        )
        RunTest.no_workspace_objects.create(
            name=f"T129 page run {index}",
            organization=data.organization,
            workspace=data.w2,
            source_type=RunTest.SourceTypes.AGENT_DEFINITION,
            agent_definition=agent,
        )
        local.append(
            _agent_version(
                agent,
                data.organization,
                data.w2,
                1,
                f"page local {index}",
            )
        )
        foreign.append(
            _agent_version(
                agent,
                tenants.organization,
                tenants.workspace,
                2,
                f"page foreign {index}",
                status="active",
            )
        )
    loaded = _loads(monkeypatch, AgentVersion)
    with (
        workspace_context(None, organization=None, user=None),
        CaptureQueriesContext(connection) as queries,
    ):
        response = run_views.RunTestListView.as_view()(
            _request(data, "get", workspace=data.w2)
        )
    assert response.status_code == 200, response.data
    encoded = json.dumps(response.data, default=str)
    for index, row in enumerate(local):
        assert f"T129 page run {index}" in encoded
        assert row.pk in loaded
        assert str(row.pk) in encoded
    for row in foreign:
        _assert_version_hidden(row, loaded, response.data)
    # The root join also names the table; count only queries reading versions.
    version_queries = [
        q for q in queries if 'FROM "simulate_agent_version"' in q["sql"]
    ]
    assert len(version_queries) == 1


@pytest.mark.parametrize("highest", ["foreign-organization", "foreign-workspace"])
def test_t1_serializer_version_fallbacks_stay_inside_the_run_tenant(
    tenants, monkeypatch, highest
):
    # No read helper and no request context: the serializer's own latest-version
    # fallback and the nested agent detail's active/latest fallback must still
    # use the run's exact organization and workspace.
    data = tenants.data
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        agent_definition=data.two.agent,
        agent_version=None,
    )
    local = _agent_version(data.two.agent, data.organization, data.w2, 1, "local")
    organization, workspace = _version_owner(tenants, highest)
    top = _agent_version(
        data.two.agent,
        organization,
        workspace,
        2,
        highest,
        status="active",
    )
    run = RunTest.no_workspace_objects.get(pk=data.two.run.pk)
    loaded = _loads(monkeypatch, AgentVersion)
    with workspace_context(None, organization=None, user=None):
        result = RunTestSerializer(run).data
    assert str(result["agent_version"]["id"]) == str(local.pk)
    _assert_version_hidden(top, loaded, result)


def test_t1_shared_read_helper_never_hydrates_versions_of_another_tenant(
    tenants, monkeypatch
):
    # The internal helper is also reached through prompt_simulation's own reload.
    # Its version Prefetch is tied to the parent definition's tenant as a floor.
    from simulate.views.prompt_simulation import _prompt_simulation_read_queryset

    data = tenants.data
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        agent_definition=data.two.agent,
        agent_version=None,
    )
    local = _agent_version(data.two.agent, data.organization, data.w2, 1, "local")
    top = _agent_version(
        data.two.agent,
        tenants.organization,
        tenants.workspace,
        2,
        "foreign-organization",
        status="active",
    )
    loaded = _loads(monkeypatch, AgentVersion)
    with workspace_context(None, organization=None, user=None):
        run = _prompt_simulation_read_queryset(
            RunTest.no_workspace_objects, data.organization, data.w2
        ).get(pk=data.two.run.pk)
        # Read the SQL result before the serializer re-filters the cache.
        prefetched = [
            version.pk for version in run.agent_definition._prefetched_versions
        ]
        result = RunTestSerializer(run).data
    assert prefetched == [local.pk]
    assert str(result["agent_version"]["id"]) == str(local.pk)
    _assert_version_hidden(top, loaded, result)


def _run_row(payload, run_id):
    """Find one run representation in a detail or paginated list payload."""
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if str(item.get("id")) == str(run_id) and "agent_definition" in item:
                return item
            stack.extend(item.values())
        elif isinstance(item, list | tuple):
            stack.extend(item)
    raise AssertionError(f"run {run_id} is missing from the response")


@pytest.mark.parametrize("route", ["list", "get", "patch", "components-replace"])
@pytest.mark.parametrize("deleted", ["agent", "pinned-version"])
def test_t1_soft_deleted_local_components_keep_the_run_readable(
    tenants, route, deleted
):
    # Same-tenant historical reads: deleting the run's own agent or its pinned
    # version (soft_delete_agent_definition_and_versions leaves RunTest intact)
    # must not hide the run or block replacing the deleted component.
    data = tenants.data
    run = data.two.run
    agent = data.two.agent
    pinned = _agent_version(agent, data.organization, data.w2, 1, "pinned")
    RunTest.no_workspace_objects.filter(pk=run.pk).update(
        agent_definition=agent,
        agent_version=pinned,
    )
    if deleted == "agent":
        AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(deleted=True)
    AgentVersion.no_workspace_objects.filter(agent_definition=agent).update(
        deleted=True
    )
    replacement = replacement_version = None
    with workspace_context(None, organization=None, user=None):
        if route == "list":
            response = run_views.RunTestListView.as_view()(
                _request(data, "get", workspace=data.w2)
            )
        elif route in {"get", "patch"}:
            response = _detail(data, route, run.pk)
        else:
            replacement = AgentDefinition.no_workspace_objects.create(
                agent_name="T129 replacement agent",
                organization=data.organization,
                workspace=data.w2,
                inbound=False,
                description="Synthetic T129 agent",
            )
            replacement_version = _agent_version(
                replacement,
                data.organization,
                data.w2,
                1,
                "replacement",
            )
            response = _components(
                data,
                run.pk,
                {"agent_definition_id": str(replacement.pk)},
            )
    assert response.status_code == 200, response.data
    row = _run_row(response.data, run.pk)
    persisted = RunTest.no_workspace_objects.get(pk=run.pk)
    if route == "components-replace":
        assert persisted.agent_definition_id == replacement.pk
        assert persisted.agent_version_id == replacement_version.pk
        assert str(row["agent_version"]["id"]) == str(replacement_version.pk)
        return
    assert persisted.agent_definition_id == agent.pk
    assert persisted.agent_version_id == pinned.pk
    if route == "patch":
        assert persisted.name == "T129 edited local run"
    if deleted == "agent":
        # The carried serializer's deleted-agent contract.
        assert row["agent_definition"] is None
        assert row["agent_definition_detail"] is None
        assert row["agent_version"] is None
    else:
        assert str(row["agent_definition"]) == str(agent.pk)
        assert str(row["agent_definition_detail"]["id"]) == str(agent.pk)


def _prompt_run_response(data, route, run_id=None, payload=None):
    method = "patch" if route == "patch" else "get"
    if payload is None and method == "patch":
        payload = {"name": "T129 prompt edit", "description": "T129 prompt description"}
    request = _request(data, method, payload, workspace=data.w2)
    if route == "list":
        return prompt_views.PromptSimulationListCreateView.as_view()(
            request,
            prompt_template_id=data.two.template.pk,
        )
    return prompt_views.PromptSimulationDetailView.as_view()(
        request,
        prompt_template_id=data.two.template.pk,
        run_test_id=run_id,
    )


@pytest.mark.parametrize("route", ["list", "get", "patch"])
@pytest.mark.parametrize("relation", ["agent_version", "agent_definition"])
@pytest.mark.parametrize("foreign", ["organization", "workspace", "null-workspace"])
def test_t1_prompt_routes_reject_foreign_agent_roots_before_hydration(
    tenants, monkeypatch, route, relation, foreign
):
    # A local prompt run/template is not authority for either independent root
    # agent FK. Clear ambient scope and use the real prompt views, not the run
    # detail wrapper that already guarded these references in the previous round.
    data = tenants.data
    run = data.two.run
    organization, workspace = (
        (tenants.organization, tenants.workspace)
        if foreign == "organization"
        else (data.organization, data.w1 if foreign == "workspace" else None)
    )
    agent = data.two.agent
    if relation == "agent_definition":
        agent = AgentDefinition.no_workspace_objects.create(
            agent_name="T129 foreign prompt root",
            organization=organization,
            workspace=workspace,
            inbound=False,
            description="Synthetic T129 agent",
        )
        AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(
            workspace=workspace,
        )
        # Isolate the definition predicate: the independently linked pin is local.
        version = _agent_version(
            data.two.agent, data.organization, data.w2, 1, "local pin"
        )
    else:
        version = _agent_version(
            agent, organization, workspace, 1, "foreign prompt pin"
        )
        AgentVersion.no_workspace_objects.filter(pk=version.pk).update(
            workspace=workspace
        )
    RunTest.no_workspace_objects.filter(pk=run.pk).update(
        agent_definition=agent,
        agent_version=version,
    )
    before = RunTest.no_workspace_objects.get(pk=run.pk)
    loaded_versions = _loads(monkeypatch, AgentVersion)
    loaded_agents = _loads(monkeypatch, AgentDefinition)
    with workspace_context(None, organization=None, user=None):
        response = _prompt_run_response(data, route, run.pk)
        persisted = RunTest.no_workspace_objects.get(pk=run.pk)
        # A rejected PATCH must not edit the row before the scoped response reload.
        assert persisted.name == before.name
        assert persisted.description == before.description
        assert persisted.agent_version_id == version.pk
        assert persisted.agent_definition_id == agent.pk
        if route == "list":
            assert response.status_code == 200, response.data
            assert _result(response)["results"] == []
            assert _result(response)["count"] == 0
            # The same visible list as if the rejected run did not exist.
            RunTest.no_workspace_objects.filter(pk=run.pk).update(deleted=True)
            missing = _prompt_run_response(data, route)
        else:
            missing = _prompt_run_response(data, route, uuid.uuid4())
            assert response.status_code == missing.status_code == 404
    assert response.status_code == missing.status_code
    assert response.data == missing.data
    assert loaded_versions == []
    assert loaded_agents == []
    assert str(run.pk) not in json.dumps(response.data, default=str)
    if relation == "agent_version":
        _assert_version_hidden(version, loaded_versions, response.data)
    else:
        assert str(agent.pk) not in json.dumps(response.data, default=str)
        assert agent.agent_name not in json.dumps(response.data, default=str)


@pytest.mark.parametrize("route", ["list", "get", "patch"])
@pytest.mark.parametrize(
    "component_state",
    ["live", "deleted-version", "deleted-agent", "null-pin", "null-roots"],
)
def test_t1_prompt_routes_preserve_local_pins_and_historical_components(
    tenants, route, component_state
):
    data = tenants.data
    run = data.two.run
    agent = data.two.agent
    pinned = _agent_version(agent, data.organization, data.w2, 1, "local prompt pin")
    # A pin must keep its historical version, not silently select the latest one.
    latest = _agent_version(agent, data.organization, data.w2, 2, "local prompt latest")
    RunTest.no_workspace_objects.filter(pk=run.pk).update(
        agent_definition=None if component_state == "null-roots" else agent,
        agent_version=None if component_state in {"null-pin", "null-roots"} else pinned,
    )
    if component_state == "deleted-agent":
        AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(deleted=True)
        AgentVersion.no_workspace_objects.filter(agent_definition=agent).update(
            deleted=True
        )
    elif component_state == "deleted-version":
        AgentVersion.no_workspace_objects.filter(pk=pinned.pk).update(deleted=True)
    with workspace_context(None, organization=None, user=None):
        response = _prompt_run_response(data, route, run.pk)
    assert response.status_code == 200, response.data
    row = _run_row(response.data, run.pk)
    assert str(row["prompt_version"]) == str(data.two.version.pk)
    assert row["prompt_version_detail"]["id"] == str(data.two.version.pk)
    if component_state in {"deleted-agent", "null-roots"}:
        assert row["agent_definition"] is None
        assert row["agent_definition_detail"] is None
        assert row["agent_version"] is None
    else:
        expected = latest if component_state == "null-pin" else pinned
        assert str(row["agent_definition"]) == str(agent.pk)
        assert str(row["agent_version"]["id"]) == str(expected.pk)
        assert row["agent_version"]["name"] == expected.version_name
        assert isinstance(row["agent_version"]["configuration_snapshot"], dict)
    persisted = RunTest.no_workspace_objects.get(pk=run.pk)
    expected_pin = None if component_state in {"null-pin", "null-roots"} else pinned.pk
    assert persisted.agent_version_id == expected_pin
    if route == "patch":
        assert persisted.name == "T129 prompt edit"
        assert persisted.description == "T129 prompt description"


@pytest.mark.parametrize("foreign", ["foreign-organization", "foreign-workspace"])
def test_t1_prompt_patch_reload_rechecks_the_direct_pin(tenants, monkeypatch, foreign):
    data = tenants.data
    organization, workspace = _version_owner(tenants, foreign)
    foreign_version = _agent_version(
        data.two.agent, organization, workspace, 1, foreign
    )
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        agent_definition=data.two.agent,
        agent_version=None,
    )
    original_save = RunTest.save

    def save_then_mix(instance, *args, **kwargs):
        result = original_save(instance, *args, **kwargs)
        if instance.pk == data.two.run.pk:
            RunTest.no_workspace_objects.filter(pk=instance.pk).update(
                agent_version=foreign_version,
            )
        return result

    monkeypatch.setattr(RunTest, "save", save_then_mix)
    loaded = _loads(monkeypatch, AgentVersion)
    with workspace_context(None, organization=None, user=None):
        response = _prompt_run_response(data, "patch", data.two.run.pk)
        missing = _prompt_run_response(data, "patch", uuid.uuid4())
    assert response.status_code == missing.status_code == 404
    assert response.data == missing.data
    _assert_version_hidden(foreign_version, loaded, response.data)


@pytest.mark.parametrize("page_size", [1, 3])
def test_t1_prompt_pinned_page_keeps_one_version_prefetch(
    tenants, monkeypatch, page_size
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    data = tenants.data
    runs, versions, foreign_versions = [], [], []
    for index in range(page_size):
        agent = AgentDefinition.no_workspace_objects.create(
            agent_name=f"T129 prompt page agent {index}",
            organization=data.organization,
            workspace=data.w2,
            inbound=False,
            description="Synthetic T129 agent",
        )
        version = _agent_version(
            agent, data.organization, data.w2, 1, f"prompt page {index}"
        )
        foreign_versions.append(
            _agent_version(
                agent,
                tenants.organization,
                tenants.workspace,
                2,
                f"foreign prompt page {index}",
                status="active",
            )
        )
        runs.append(
            RunTest.no_workspace_objects.create(
                name=f"T129 prompt page run {index}",
                organization=data.organization,
                workspace=data.w2,
                source_type="prompt",
                prompt_template=data.two.template,
                prompt_version=data.two.version,
                agent_definition=agent,
                agent_version=version,
            )
        )
        versions.append(version)
    loaded = _loads(monkeypatch, AgentVersion)
    with (
        workspace_context(None, organization=None, user=None),
        CaptureQueriesContext(connection) as queries,
    ):
        response = _prompt_run_response(data, "list", payload={"limit": 10})
    assert response.status_code == 200, response.data
    assert _result(response)["count"] == page_size + 1
    for run, version in zip(runs, versions, strict=True):
        row = _run_row(response.data, run.pk)
        assert str(row["agent_version"]["id"]) == str(version.pk)
        assert version.pk in loaded
    for version in foreign_versions:
        _assert_version_hidden(version, loaded, response.data)
    version_queries = [
        q for q in queries if 'FROM "simulate_agent_version"' in q["sql"]
    ]
    assert len(version_queries) == 1


@pytest.mark.parametrize("pin_workspace", ["null", "different"])
def test_t1_shared_root_guard_preserves_exact_null_workspace(
    tenants, monkeypatch, pin_workspace
):
    # Prompt routes require a workspace, but the shared helper's nullable equality
    # must keep legacy NULL/NULL components without admitting NULL/non-NULL pairs.
    data = tenants.data
    agent = data.two.agent
    version = _agent_version(agent, data.organization, data.w2, 1, "null workspace pin")
    AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(workspace=None)
    AgentVersion.no_workspace_objects.filter(pk=version.pk).update(
        workspace=None if pin_workspace == "null" else data.w1,
    )
    RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
        workspace=None,
        agent_definition=agent,
        agent_version=version,
    )
    loaded = _loads(monkeypatch, AgentVersion)
    with workspace_context(None, organization=None, user=None):
        rows = list(
            run_views._run_test_read_queryset(
                RunTest.no_workspace_objects.filter(pk=data.two.run.pk)
            )
        )
    if pin_workspace == "null":
        assert len(rows) == 1
        assert rows[0].agent_version.pk == version.pk
        assert version.pk in loaded
    else:
        assert rows == []
        assert loaded == []


@pytest.fixture
def graph_rows(tenants):
    data = tenants.data
    rows = {}
    for index, (name, scenario, organization) in enumerate(
        [
            ("local", data.two.scenario, data.organization),
            ("foreign-organization", data.two.scenario, tenants.organization),
            ("foreign-workspace", data.one.scenario, data.organization),
            ("foreign-root", tenants.scenario, tenants.organization),
        ]
    ):
        graph = ScenarioGraph.objects.create(
            scenario=scenario,
            organization=organization,
            name=name,
            graph_config={"graph_data": {"marker": name}},
            is_active=True,
        )
        ScenarioGraph.objects.filter(pk=graph.pk).update(
            created_at=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=index)
        )
        rows[name] = graph
    return rows


@pytest.mark.parametrize("reader", ["scenario-cache", "run-prefetch", "analyzer"])
@pytest.mark.parametrize(
    "local", [True, False], ids=["local-control", "no-local-graph"]
)
def test_t2_three_exact_graph_readers_exclude_foreign(
    tenants, graph_rows, monkeypatch, reader, local
):
    data = tenants.data
    if not local:
        ScenarioGraph.objects.filter(pk=graph_rows["local"].pk).update(is_active=False)
    loaded = _loads(monkeypatch, ScenarioGraph)
    expected = {"marker": "local"} if local else {}
    if reader == "scenario-cache":
        response = scenario_views.ScenariosListView.as_view()(
            _request(data, "get", workspace=data.w2)
        )
        assert response.status_code == 200, response.data
        result = _result(response)
        assert result["results"][0]["graph"] == expected
    elif reader == "run-prefetch":
        data.two.run.scenarios.add(data.one.scenario, tenants.scenario)
        response = _detail(data, "get", data.two.run.pk)
        assert response.status_code == 200, response.data
        result = _result(response)
        assert len(result["scenarios_detail"]) == 1
        assert result["scenarios_detail"][0]["graph"] == expected
    else:
        execution = SimulationTestExecution.objects.create(run_test=data.two.run)
        call = CallExecution.objects.create(
            test_execution=execution, scenario=data.two.scenario
        )
        graph = BranchDeviationAnalyzer()._get_scenario_graph(call)
        assert (graph.graph_config["graph_data"] if graph else {}) == expected
    for name in ("foreign-organization", "foreign-workspace", "foreign-root"):
        assert graph_rows[name].pk not in loaded
    if local:
        assert graph_rows["local"].pk in loaded


@pytest.mark.parametrize("foreign", ["organization", "workspace"])
def test_t2_analyzer_rejects_mixed_foreign_scenario(
    tenants, graph_rows, monkeypatch, foreign
):
    data = tenants.data
    execution = SimulationTestExecution.objects.create(run_test=data.two.run)
    scenario = tenants.scenario if foreign == "organization" else data.one.scenario
    call = CallExecution.objects.create(test_execution=execution, scenario=scenario)
    loaded = _loads(monkeypatch, ScenarioGraph)
    assert BranchDeviationAnalyzer()._get_scenario_graph(call) is None
    assert loaded == []


@pytest.mark.parametrize("foreign", ["organization", "workspace"])
def test_t2_scenario_cache_route_foreign_root_is_missing(tenants, foreign):
    data = tenants.data
    scenario = tenants.scenario if foreign == "organization" else data.one.scenario
    responses = []
    for pk in (scenario.pk, uuid.uuid4()):
        response = scenario_views.EditScenarioView.as_view()(
            _request(data, "put", {"description": "local edit"}, workspace=data.w2),
            scenario_id=pk,
        )
        assert response.status_code == 404
        responses.append(response.data)
    assert responses[0] == responses[1]


def test_t2_edit_cache_excludes_foreign_graph(tenants, graph_rows, monkeypatch):
    data = tenants.data
    loaded = _loads(monkeypatch, ScenarioGraph)
    response = scenario_views.EditScenarioView.as_view()(
        _request(data, "put", {"description": "local edit"}, workspace=data.w2),
        scenario_id=data.two.scenario.pk,
    )
    assert response.status_code == 200, response.data
    assert _result(response)["scenario"]["graph"] == {"marker": "local"}
    assert graph_rows["foreign-organization"].pk not in loaded


def test_t2_scenario_list_graphs_use_one_query(tenants, graph_rows):
    # Inspect the actual view's graph loads rather than a replacement queryset.
    # A page with several scenarios must still issue one graph query.
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    data = tenants.data
    for index in range(4):
        scenario = Scenarios.no_workspace_objects.create(
            name=f"T129 local scenario {index}",
            source="Synthetic T129 scenario source",
            organization=data.organization,
            workspace=data.w2,
            scenario_type="graph",
        )
        ScenarioGraph.objects.create(
            scenario=scenario,
            organization=data.organization,
            name="local extra",
            graph_config={"graph_data": {"marker": "local"}},
        )
    with CaptureQueriesContext(connection) as queries:
        response = scenario_views.ScenariosListView.as_view()(
            _request(data, "get", workspace=data.w2)
        )
    assert response.status_code == 200, response.data
    graph_queries = [q for q in queries if '"simulate_scenario_graph"' in q["sql"]]
    assert len(graph_queries) == 1


@pytest.mark.parametrize("mixed", [False, True], ids=["local-control", "mixed-m2m"])
def test_t2_components_patch_scopes_current_run_not_any_related_run(
    tenants,
    graph_rows,
    monkeypatch,
    mixed,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    data = tenants.data
    assert tenants.run.scenarios.filter(pk=tenants.scenario.pk).exists()
    if mixed:
        data.two.run.scenarios.add(tenants.scenario, data.one.scenario)
    for index in range(3):
        scenario = Scenarios.no_workspace_objects.create(
            name=f"T129 components local {index}",
            source="Synthetic T129 scenario source",
            organization=data.organization,
            workspace=data.w2,
            scenario_type="graph",
        )
        data.two.run.scenarios.add(scenario)
        ScenarioGraph.objects.create(
            scenario=scenario,
            organization=data.organization,
            name="local extra",
            graph_config={"graph_data": {"marker": "local"}},
        )
    loaded_scenarios = _loads(monkeypatch, Scenarios)
    loaded_graphs = _loads(monkeypatch, ScenarioGraph)
    with (
        workspace_context(None, organization=None, user=None),
        CaptureQueriesContext(connection) as queries,
    ):
        response = run_views.RunTestComponentsUpdateView.as_view()(
            _request(data, "patch", {}, workspace=data.w2),
            run_test_id=data.two.run.pk,
        )
    assert response.status_code == 200, response.data
    result = _result(response)
    assert len(result["scenarios_detail"]) == 4
    assert all(
        row["graph"] == {"marker": "local"} for row in result["scenarios_detail"]
    )
    assert tenants.scenario.pk not in loaded_scenarios
    assert data.one.scenario.pk not in loaded_scenarios
    for name in ("foreign-organization", "foreign-workspace", "foreign-root"):
        assert graph_rows[name].pk not in loaded_graphs
    graph_queries = [q for q in queries if '"simulate_scenario_graph"' in q["sql"]]
    assert len(graph_queries) == 1


def _harness(data, action, pk=None, payload=None):
    method = "get" if action in {"list", "retrieve"} else "post"
    kwargs = {"pk": str(pk)} if pk is not None else {}
    return HarnessJobViewSet.as_view({method: action})(
        _request(data, method, payload, workspace=data.w2), **kwargs
    )


@pytest.fixture
def sandbox(tenants, monkeypatch, settings):
    settings.HARNESS_PROVIDER = "sandbox"
    client = Mock()
    factory = Mock(return_value=client)
    monkeypatch.setattr(SandboxHarnessProvider, "_client", factory)
    return SimpleNamespace(client=client, factory=factory)


@pytest.fixture
def job_rows(tenants):
    data = tenants.data
    rows = {}
    for name, organization, workspace in [
        ("local", data.organization, data.w2),
        ("foreign-org", tenants.organization, tenants.workspace),
        ("foreign-workspace", data.organization, data.w1),
        ("null-workspace", data.organization, None),
    ]:
        job = SandboxHarnessJob.objects.create(
            job_id=f"job-{name}",
            organization=organization,
            workspace=workspace,
        )
        # Keep the negative NULL fixture exact despite global save signals.
        SandboxHarnessJob.objects.filter(pk=job.pk).update(workspace=workspace)
        job.refresh_from_db()
        assert job.workspace_id == (workspace.pk if workspace is not None else None)
        rows[name] = job
    return rows


@pytest.mark.parametrize("action", ["retrieve", "cancel", "adjust"])
@pytest.mark.parametrize(
    "foreign", ["foreign-org", "foreign-workspace", "null-workspace"]
)
def test_t9_foreign_and_missing_never_construct_client(
    tenants, sandbox, job_rows, action, foreign
):
    payload = {"instruction": "Keep this local"} if action == "adjust" else {}
    responses = []
    for pk in (job_rows[foreign].pk, uuid.uuid4(), "not-a-job-id"):
        response = _harness(tenants.data, action, pk, payload)
        assert response.status_code == 404, response.data
        responses.append(response.data)
    assert responses[0] == responses[1] == responses[2]
    sandbox.factory.assert_not_called()
    assert sandbox.client.mock_calls == []


@pytest.mark.parametrize("action", ["retrieve", "cancel", "adjust"])
def test_t9_registered_same_tenant_controls(tenants, sandbox, job_rows, action):
    job = job_rows["local"]
    payload = {"instruction": "Keep this local"} if action == "adjust" else {}
    method = "get" if action == "retrieve" else action
    expected = {"job": {"job_id": str(job.pk)}, "status": {"state": "queued"}}
    getattr(sandbox.client, method).return_value = expected
    response = _harness(tenants.data, action, job.pk, payload)
    assert response.status_code == 200, response.data
    assert response.data == expected
    args = (str(job.pk), payload) if action == "adjust" else (str(job.pk),)
    getattr(sandbox.client, method).assert_called_once_with(*args)


def test_t9_list_filters_installation_results_after_scoped_lookup(
    tenants, sandbox, job_rows
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    documents = [
        {"job": {"job_id": str(job.pk)}, "status": {"marker": name}}
        for name, job in job_rows.items()
    ]
    documents.append(
        {"job": {"job_id": str(uuid.uuid4())}, "status": {"marker": "unowned"}}
    )
    with CaptureQueriesContext(connection) as queries:

        def list_jobs():
            # Even constructing/using the shared transport must follow ownership.
            ownership_reads = [
                q for q in queries if '"simulate_sandbox_harness_job"' in q["sql"]
            ]
            assert len(ownership_reads) == 1
            return documents

        sandbox.client.list_jobs.side_effect = list_jobs
        response = _harness(tenants.data, "list")
    assert response.status_code == 200, response.data
    assert response.data == documents[:1]
    sandbox.client.list_jobs.assert_called_once_with()
    for name in ("foreign-org", "foreign-workspace", "null-workspace", "unowned"):
        assert name not in json.dumps(response.data, default=str)


def test_t9_empty_scope_never_constructs_installation_client(
    tenants, sandbox, job_rows
):
    SandboxHarnessJob.objects.filter(pk=job_rows["local"].pk).update(deleted=True)
    response = _harness(tenants.data, "list")
    assert response.status_code == 200
    assert response.data == []
    sandbox.factory.assert_not_called()


@pytest.mark.parametrize("identifier", ["opaque", "uuid"])
def test_t9_real_sandbox_submission_remains_readable_by_its_owner(
    tenants, sandbox, identifier
):
    # No pre-seeded binding: the actual create route must persist ownership.
    job_id = "job-1" if identifier == "opaque" else str(uuid.uuid4())
    document = {"job": {"job_id": job_id}, "status": {"stage": "queued"}}
    sandbox.client.submit.return_value = document
    sandbox.client.list_jobs.return_value = [document]
    hosted_before = HostedHarnessJob.no_workspace_objects.count()
    with workspace_context(None, organization=None, user=None):
        created = _harness(
            tenants.data,
            "create",
            payload=_v1_payload(
                metadata={
                    "organization_id": str(tenants.organization.pk),
                    "workspace_id": str(tenants.workspace.pk),
                }
            ),
        )
    assert created.status_code == 202, created.data
    assert created.data == document
    binding = SandboxHarnessJob.objects.get(pk=job_id)
    assert binding.organization_id == tenants.data.organization.pk
    assert binding.workspace_id == tenants.data.w2.pk
    assert HostedHarnessJob.no_workspace_objects.count() == hosted_before
    assert _harness(tenants.data, "list").data == [document]
    for action in ("retrieve", "cancel", "adjust"):
        method = "get" if action == "retrieve" else action
        getattr(sandbox.client, method).return_value = document
        payload = (
            {
                "instruction": "Add 10 more scenarios covering payment failures",
                "client_request_id": "browser-1",
            }
            if action == "adjust"
            else {}
        )
        response = _harness(tenants.data, action, job_id, payload)
        assert response.status_code == 200, response.data
        assert response.data == document
        args = (job_id, payload) if action == "adjust" else (job_id,)
        getattr(sandbox.client, method).assert_called_once_with(*args)


def test_t9_null_workspace_submission_is_not_defaulted(tenants, sandbox):
    data = tenants.data
    document = {"job": {"job_id": "job-null"}, "status": {"stage": "queued"}}
    sandbox.client.submit.return_value = document
    sandbox.client.get.return_value = document
    sandbox.client.list_jobs.return_value = [document]
    with workspace_context(data.w2, organization=data.organization, user=data.user):
        response = HarnessJobViewSet.as_view({"post": "create"})(
            _request(data, "post", _v1_payload(), workspace=None),
        )
    assert response.status_code == 202, response.data
    binding = SandboxHarnessJob.objects.get(pk="job-null")
    assert binding.organization_id == data.organization.pk
    assert binding.workspace_id is None
    for action in ("list", "retrieve"):
        kwargs = {"pk": "job-null"} if action == "retrieve" else {}
        response = HarnessJobViewSet.as_view({"get": action})(
            _request(data, "get", workspace=None),
            **kwargs,
        )
        assert response.status_code == 200, response.data
        assert response.data == (document if kwargs else [document])
    sandbox.factory.reset_mock()
    assert _harness(data, "retrieve", "job-null").status_code == 404
    assert _harness(data, "list").data == []
    sandbox.factory.assert_not_called()


def test_t9_repeated_same_owner_submission_preserves_one_binding(tenants, sandbox):
    document = {"job": {"job_id": "job-1"}}
    sandbox.client.submit.return_value = document
    for _ in range(2):
        response = _harness(tenants.data, "create", payload=_v1_payload())
        assert response.status_code == 202, response.data
        assert response.data == document
    assert SandboxHarnessJob.objects.count() == 1


@pytest.mark.parametrize(
    "foreign", ["foreign-org", "foreign-workspace", "null-workspace"]
)
def test_t9_submission_cannot_reassign_existing_binding(
    tenants, sandbox, job_rows, foreign
):
    binding = job_rows[foreign]
    owner_before = (binding.organization_id, binding.workspace_id)
    sandbox.client.submit.return_value = {"job": {"job_id": binding.pk}}
    response = _harness(tenants.data, "create", payload=_v1_payload())
    assert response.status_code == 503, response.data
    assert str(binding.pk) not in json.dumps(response.data)
    binding.refresh_from_db()
    assert (binding.organization_id, binding.workspace_id) == owner_before
    assert _harness(tenants.data, "retrieve", binding.pk).status_code == 404
    sandbox.client.get.assert_not_called()


@pytest.mark.parametrize("document", [{}, {"job": {}}, {"job": {"job_id": ""}}])
def test_t9_submission_without_identifier_never_claims_success(
    tenants, sandbox, document
):
    sandbox.client.submit.return_value = document
    response = _harness(tenants.data, "create", payload=_v1_payload())
    assert response.status_code == 503, response.data
    assert not SandboxHarnessJob.objects.exists()


@pytest.mark.parametrize(
    "foreign", ["foreign-org", "foreign-workspace", "null-workspace"]
)
def test_t9_rerun_foreign_and_missing_refuse_before_transport(
    tenants, sandbox, job_rows, foreign
):
    from simulate.services.harness_sandbox import HarnessSandboxRejected

    data = tenants.data
    for job_id in (job_rows[foreign].pk, "job-missing"):
        with pytest.raises(HarnessSandboxRejected) as error:
            SandboxHarnessProvider().rerun_saved(
                job_id,
                organization=data.organization,
                workspace=data.w2,
                environment_values={},
            )
        assert error.value.status_code == 404
        assert str(error.value) == "Job not found"
    sandbox.factory.assert_not_called()


def test_t9_rerun_records_the_new_opaque_identifier(tenants, sandbox):
    data = tenants.data
    document = {"job": {"job_id": "job-1", "secret_refs": {}, "metadata": {}}}
    sandbox.client.submit.return_value = document
    sandbox.client.get.return_value = document
    sandbox.client.rerun.return_value = {"job": {"job_id": "job-2"}}
    created = _harness(data, "create", payload=_v1_payload())
    assert created.status_code == 202, created.data
    # Reruns read the saved environment profile; store an empty one for this job.
    from simulate.services.harness_credentials import save_environment_credentials

    save_environment_credentials(
        "job-1",
        environment_values={},
        secret_refs={},
        organization=data.organization,
        workspace=data.w2,
    )
    result = SandboxHarnessProvider().rerun_saved(
        "job-1",
        organization=data.organization,
        workspace=data.w2,
        environment_values={},
    )
    assert result == {"job": {"job_id": "job-2"}}
    assert SandboxHarnessJob.objects.filter(
        pk="job-2",
        organization=data.organization,
        workspace=data.w2,
    ).exists()
    sandbox.client.rerun.assert_called_once_with(
        "job-1",
        {"environment_values": {}, "secret_refs": {}, "only": []},
    )
    sandbox.client.get.return_value = result
    assert _harness(data, "retrieve", "job-2").data == result


@pytest.fixture
def replay_rows(tenants):
    data = tenants.data
    rows = {}
    for name, organization, workspace in [
        ("local", data.organization, data.w2),
        ("foreign-org", tenants.organization, tenants.workspace),
        ("foreign-workspace", data.organization, data.w1),
    ]:
        project = Project.no_workspace_objects.create(
            name=f"T129 project {name}",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        rows[name] = ReplaySession.no_workspace_objects.create(
            project=project, replay_type="trace", ids=[], select_all=False
        )
    return rows


def _create_with_replay(data, session_id):
    data.two.agent.agent_type = "text"
    data.two.agent.save(update_fields=["agent_type"])
    return run_views.CreateRunTestView.as_view()(
        _request(
            data,
            "post",
            {
                "name": "T129 replay run",
                "agent_definition_id": str(data.two.agent.pk),
                "scenario_ids": [str(data.two.scenario.pk)],
                "replay_session_id": str(session_id),
            },
            workspace=data.w2,
        )
    )


@pytest.mark.parametrize("foreign", ["foreign-org", "foreign-workspace"])
def test_t7_replay_rebind_foreign_and_missing_are_identical(
    tenants, replay_rows, foreign
):
    data = tenants.data
    session = replay_rows[foreign]
    before = RunTest.no_workspace_objects.count()
    responses = []
    # Clear ambient manager scope: the explicit request workspace must be enough.
    with workspace_context(None, organization=None, user=None):
        for pk in (session.pk, uuid.uuid4()):
            response = _create_with_replay(data, pk)
            assert response.status_code == 404, response.data
            responses.append(response.data)
    assert responses[0] == responses[1]
    assert RunTest.no_workspace_objects.count() == before
    session.refresh_from_db()
    assert session.run_test_id is None
    assert session.current_step != ReplaySessionStep.COMPLETED


def test_t7_local_replay_rebind_preserves_created_contract(tenants, replay_rows):
    data = tenants.data
    session = replay_rows["local"]
    with workspace_context(None, organization=None, user=None):
        response = _create_with_replay(data, session.pk)
    # This route creates a run: its existing success contract is 201, not 200.
    assert response.status_code == 201, response.data
    session.refresh_from_db()
    assert str(session.run_test_id) == str(_result(response)["id"])
    assert session.current_step == ReplaySessionStep.COMPLETED
