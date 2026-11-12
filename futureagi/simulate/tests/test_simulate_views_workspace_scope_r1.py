"""Dev workspace-isolation regressions ported to IR FINAL 651ab5bb.

These tests deliberately clear ambient model-manager scope. Authorization must
follow permitted request context, not an incidental ContextVar or user fallback.
Only feature gating, prompt generation, and external dispatch are replaced;
lookup, validation, persistence, and response serialization use the real code.
IR has no prompt workspace resolver: its affected probes use existing HTTP view
entry points and real ORM observations, never a fabricated resolver or xfail.
"""

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.http import Http404
from django.http.request import HttpHeaders
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate

import tfc.middleware.workspace_context as workspace_context_module
from accounts.authentication import APIKeyAuthentication
from accounts.models import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import AgentDefinition, RunTest, Scenarios
from simulate.models.simulator_agent import SimulatorAgent
from simulate.views import prompt_simulation as prompt_views
from simulate.views import run_test as run_test_views
from simulate.views import scenarios as scenario_views
from simulate.views.prompt_simulation import PromptSimulationListCreateView
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    get_current_workspace,
    set_workspace_context,
    workspace_context,
)

pytestmark = pytest.mark.django_db
_MISSING = object()


@pytest.fixture
def scope_data(organization, workspace):
    # IR adds a denied-landing flag. Clear it as well as ambient tenant fields,
    # and restore all four ContextVars even when an assertion fails.
    with workspace_context(None, organization=None, user=None):
        set_workspace_context(require_workspace=False)
        user = get_user_model().objects.create_user(
            email=f"scope-{uuid.uuid4().hex}@example.test"
        )
        user.organization = organization
        # Deliberately disagree with W2 requests and retain a fallback for
        # missing-workspace requests. The view must never use this value.
        user.workspace = workspace
        w2 = Workspace.objects.create(
            name="Explicit second workspace",
            organization=organization,
            created_by=user,
            is_default=False,
        )

        def resources(ws, suffix):
            template = PromptTemplate.objects.create(
                name=f"Private template {suffix}",
                organization=organization,
                workspace=ws,
            )
            version = PromptVersion.objects.create(
                original_template=template,
                template_version="v1",
                prompt_config_snapshot=[{"role": "system", "content": suffix}],
            )
            simulator = SimulatorAgent.objects.create(
                name=f"Private simulator {suffix}",
                prompt=f"Private prompt {suffix}",
                organization=organization,
                workspace=ws,
            )
            scenario = Scenarios.objects.create(
                name=f"Private scenario {suffix}",
                description=f"Private description {suffix}",
                scenario_type="graph",
                organization=organization,
                workspace=ws,
                simulator_agent=simulator,
            )
            run = RunTest.objects.create(
                name=f"Private run {suffix}",
                source_type="prompt",
                prompt_template=template,
                prompt_version=version,
                organization=organization,
                workspace=ws,
            )
            run.scenarios.set([scenario])
            agent = AgentDefinition.objects.create(
                agent_name=f"Private agent {suffix}",
                inbound=False,
                organization=organization,
                workspace=ws,
            )
            dataset = Dataset.objects.create(
                name=f"Private dataset {suffix}",
                organization=organization,
                workspace=ws,
            )
            return SimpleNamespace(
                template=template,
                version=version,
                scenario=scenario,
                simulator=simulator,
                run=run,
                agent=agent,
                dataset=dataset,
            )

        yield SimpleNamespace(
            organization=organization,
            user=user,
            w1=workspace,
            w2=w2,
            one=resources(workspace, "W1"),
            two=resources(w2, "W2"),
        )


@pytest.fixture
def scenario_boundaries(monkeypatch):
    dispatch = Mock()
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *a, **k: None)
    monkeypatch.setattr(
        scenario_views, "start_create_graph_scenario_workflow_sync", dispatch
    )
    monkeypatch.setattr(
        scenario_views,
        "generate_simulator_agent_prompt",
        lambda *a, **k: "Scoped simulator prompt",
    )
    return dispatch


def _request(data, method, payload=None, workspace=_MISSING):
    request = getattr(APIRequestFactory(), method)(
        "/simulate/workspace-scope-test/", data=payload or {}, format="json"
    )
    request.organization = data.organization
    if workspace is not _MISSING:
        request.workspace = workspace
    force_authenticate(request, user=data.user)
    return request


def _prompt_body(resources):
    return {
        "name": "Scoped simulation",
        "description": "Unchanged description",
        "prompt_version_id": str(resources.version.id),
        "scenario_ids": [str(resources.scenario.id)],
    }


def _scenario_body(resources, source="prompt"):
    body = {
        "name": "Scoped scenario",
        "description": "Unchanged description",
        "kind": "graph",
        "graph": {"nodes": [], "edges": []},
        "source_type": source,
    }
    if source == "prompt":
        body.update(
            prompt_template_id=str(resources.template.id),
            prompt_version_id=str(resources.version.id),
        )
    else:
        body["agent_definition_id"] = str(resources.agent.id)
    return body


def _result(response):
    return response.data.get("result", response.data)


def _assert_no_private_content(response, resources):
    encoded = json.dumps(response.data, default=str)
    for value in (
        resources.template.name,
        resources.scenario.name,
        resources.scenario.description,
        resources.simulator.prompt,
        str(resources.template.id),
        str(resources.scenario.id),
    ):
        assert value not in encoded


@pytest.mark.parametrize("method", ["get", "post"])
def test_prompt_template_foreign_is_indistinguishable_from_missing(scope_data, method):
    data = scope_data
    responses = []
    before = RunTest.objects.count()
    for template_id in (data.one.template.id, uuid.uuid4()):
        response = PromptSimulationListCreateView.as_view()(
            _request(
                data,
                method,
                _prompt_body(data.one) if method == "post" else None,
                workspace=data.w2,
            ),
            prompt_template_id=template_id,
        )
        assert response.status_code == 404
        _assert_no_private_content(response, data.one)
        responses.append(response.data)
    assert responses[0] == responses[1]
    assert RunTest.objects.count() == before


@pytest.mark.parametrize("method", ["get", "post"])
def test_prompt_same_workspace_preserves_response_and_database(scope_data, method):
    data = scope_data
    response = PromptSimulationListCreateView.as_view()(
        _request(
            data,
            method,
            _prompt_body(data.two) if method == "post" else None,
            workspace=data.w2,
        ),
        prompt_template_id=data.two.template.id,
    )
    assert response.status_code == (200 if method == "get" else 201)
    result = _result(response)
    if method == "get":
        assert result["prompt_template"] == {
            "id": str(data.two.template.id),
            "name": data.two.template.name,
        }
        assert [str(item["id"]) for item in result["results"]] == [str(data.two.run.id)]
    else:
        created = RunTest.objects.get(id=result["id"])
        assert created.organization_id == data.organization.id
        assert created.workspace_id == data.w2.id
        assert created.prompt_template_id == data.two.template.id
        assert created.prompt_version_id == data.two.version.id
        assert created.name == "Scoped simulation"
        assert created.description == "Unchanged description"
        assert list(created.scenarios.values_list("id", flat=True)) == [
            data.two.scenario.id
        ]


def test_prompt_list_filters_run_workspace_independently_of_template(scope_data):
    data = scope_data
    foreign_run = RunTest.objects.create(
        name="Foreign run on the same template",
        source_type="prompt",
        prompt_template=data.two.template,
        prompt_version=data.two.version,
        organization=data.organization,
        workspace=data.w1,
    )
    response = PromptSimulationListCreateView.as_view()(
        _request(data, "get", workspace=data.w2),
        prompt_template_id=data.two.template.id,
    )
    assert response.status_code == 200
    result = _result(response)
    assert result["count"] == 1
    assert [str(item["id"]) for item in result["results"]] == [str(data.two.run.id)]
    assert str(foreign_run.id) not in json.dumps(response.data, default=str)


def test_prompt_create_rejects_foreign_scenario_before_any_write(scope_data):
    data = scope_data
    before = RunTest.objects.count()
    responses = []
    for scenario_id in (data.one.scenario.id, uuid.uuid4()):
        body = _prompt_body(data.two)
        body["scenario_ids"].append(str(scenario_id))
        response = PromptSimulationListCreateView.as_view()(
            _request(data, "post", body, workspace=data.w2),
            prompt_template_id=data.two.template.id,
        )
        assert response.status_code == 404
        _assert_no_private_content(response, data.one)
        responses.append(response.data)
    assert responses[0] == responses[1]
    assert RunTest.objects.count() == before


@pytest.mark.parametrize(
    "request_workspace_value", [_MISSING, None], ids=["missing", "none"]
)
@pytest.mark.parametrize("route", ["prompt-get", "prompt-post", "scenario-post"])
def test_missing_workspace_refuses_before_validation(
    scope_data, route, request_workspace_value, monkeypatch, django_assert_num_queries
):
    data = scope_data
    before = (
        RunTest.objects.count(),
        Scenarios.objects.count(),
        SimulatorAgent.objects.count(),
    )
    method = "get" if route == "prompt-get" else "post"
    request = _request(data, method, workspace=request_workspace_value)
    validation = Mock(side_effect=AssertionError("Business validation must not run"))
    monkeypatch.setattr(
        prompt_views.CreatePromptSimulationRequestSerializer, "is_valid", validation
    )
    monkeypatch.setattr(
        scenario_views.ScenarioCreateRequestSerializer, "is_valid", validation
    )
    # Pure pagination parsing may precede the guard; database-backed validation may not.
    with django_assert_num_queries(0):
        if route == "scenario-post":
            response = scenario_views.CreateScenarioView.as_view()(request)
        else:
            response = PromptSimulationListCreateView.as_view()(
                request, prompt_template_id=data.one.template.id
            )
    validation.assert_not_called()
    assert response.status_code == 403
    response.render()
    assert response["Content-Type"].startswith("application/json")
    encoded = json.dumps(response.data, default=str)
    assert "workspace_required" in encoded
    _assert_no_private_content(response, data.one)
    assert str(data.w1.id) not in encoded
    assert str(data.organization.id) not in encoded
    assert before == (
        RunTest.objects.count(),
        Scenarios.objects.count(),
        SimulatorAgent.objects.count(),
    )


@pytest.mark.parametrize(
    "field,resource",
    [
        ("prompt_template_id", "template"),
        ("prompt_version_id", "version"),
        ("agent_definition_id", "agent"),
        ("dataset_id", "dataset"),
    ],
)
def test_scenario_foreign_reference_is_missing_before_validation(
    scope_data, scenario_boundaries, field, resource
):
    data = scope_data
    before = (Scenarios.objects.count(), SimulatorAgent.objects.count())
    responses = []
    for object_id in (getattr(data.one, resource).id, uuid.uuid4()):
        source = "agent_definition" if field == "agent_definition_id" else "prompt"
        body = _scenario_body(data.two, source)
        body[field] = str(object_id)
        response = scenario_views.CreateScenarioView.as_view()(
            _request(data, "post", body, workspace=data.w2)
        )
        # Preserve dev's field-validation contract for dataset/agent references.
        assert response.status_code == (
            400 if field in {"dataset_id", "agent_definition_id"} else 404
        )
        if response.status_code == 400:
            assert field in response.data["details"]
        _assert_no_private_content(response, data.one)
        assert str(object_id) not in json.dumps(response.data, default=str)
        responses.append(response.data)
    assert responses[0] == responses[1]
    assert before == (Scenarios.objects.count(), SimulatorAgent.objects.count())
    scenario_boundaries.assert_not_called()


@pytest.mark.parametrize("source", ["prompt", "agent_definition"])
def test_scenario_same_workspace_create_is_unchanged(
    scope_data, scenario_boundaries, source
):
    data = scope_data
    body = _scenario_body(data.two, source)
    body["dataset_id"] = str(data.two.dataset.id)
    response = scenario_views.CreateScenarioView.as_view()(
        _request(data, "post", body, workspace=data.w2)
    )
    assert response.status_code == 202
    created = Scenarios.objects.get(name="Scoped scenario", workspace=data.w2)
    assert created.organization_id == data.organization.id
    assert created.description == "Unchanged description"
    assert created.simulator_agent.workspace_id == data.w2.id
    if source == "prompt":
        assert created.prompt_template_id == data.two.template.id
        assert created.prompt_version_id == data.two.version.id
    else:
        assert created.agent_definition_id == data.two.agent.id
    scenario_boundaries.assert_called_once()
    assert scenario_boundaries.call_args.kwargs["scenario_id"] == str(created.id)
    assert created.name in json.dumps(response.data, default=str)


def _helper_request(data, workspace=_MISSING):
    request = SimpleNamespace(
        user=data.user, organization=data.organization, headers={}
    )
    if workspace is not _MISSING:
        request.workspace = workspace
    return request


def test_temp_scenario_helper_scopes_prompt_template(scope_data, scenario_boundaries):
    data = scope_data
    view = scenario_views.CreateScenarioView()
    before = (Scenarios.objects.count(), SimulatorAgent.objects.count())
    with pytest.raises(Http404):
        view._create_temp_scenario(
            _helper_request(data, data.w2), _scenario_body(data.one)
        )
    assert before == (Scenarios.objects.count(), SimulatorAgent.objects.count())


@pytest.mark.parametrize("helper", ["_create_temp_scenario", "_create_simulator_agent"])
def test_scenario_agent_sibling_reads_are_scoped(
    scope_data, scenario_boundaries, monkeypatch, helper
):
    data = scope_data
    view = scenario_views.CreateScenarioView()
    if helper == "_create_temp_scenario":
        # Reach the second AgentDefinition read independently of the first one.
        monkeypatch.setattr(
            view, "_create_simulator_agent", lambda *a: data.two.simulator
        )
    before = (Scenarios.objects.count(), SimulatorAgent.objects.count())
    with pytest.raises(Http404):
        getattr(view, helper)(
            _helper_request(data, data.w2), _scenario_body(data.one, "agent_definition")
        )
    assert before == (Scenarios.objects.count(), SimulatorAgent.objects.count())


@pytest.mark.parametrize(
    "request_workspace_value", [_MISSING, None], ids=["missing", "none"]
)
@pytest.mark.parametrize("helper", ["_create_temp_scenario", "_create_simulator_agent"])
def test_scenario_helpers_refuse_user_workspace_fallback(
    scope_data, helper, request_workspace_value, django_assert_num_queries
):
    data = scope_data
    with django_assert_num_queries(0), pytest.raises(PermissionDenied) as caught:
        getattr(scenario_views.CreateScenarioView(), helper)(
            _helper_request(data, request_workspace_value), _scenario_body(data.one)
        )
    assert "workspace_required" in json.dumps(caught.value.detail, default=str)


def test_scenario_agent_helper_same_workspace_control(scope_data, scenario_boundaries):
    data = scope_data
    simulator = scenario_views.CreateScenarioView()._create_simulator_agent(
        _helper_request(data, data.w2), _scenario_body(data.two, "agent_definition")
    )
    simulator.refresh_from_db()
    assert simulator.organization_id == data.organization.id
    assert simulator.workspace_id == data.w2.id
    assert simulator.name == "Scoped scenario"
    assert simulator.prompt == "Scoped simulator prompt"


# R2 isolation coverage, retained alongside the R3 request-context repairs.
def _r2_prompt_response(data, route):
    from simulate.views.prompt_simulation import PromptSimulationDetailView

    assert get_current_workspace() is None
    if route == "list":
        return PromptSimulationListCreateView.as_view()(
            _request(data, "get", workspace=data.w2),
            prompt_template_id=data.two.template.id,
        )
    method = "patch" if route == "patch-response" else "get"
    return PromptSimulationDetailView.as_view()(
        _request(data, method, {"name": data.two.run.name}, workspace=data.w2),
        prompt_template_id=data.two.template.id,
        run_test_id=data.two.run.id,
    )


def _r2_link_same_workspace_references(data):
    scenario = data.two.scenario
    scenario.dataset = data.two.dataset
    scenario.agent_definition = data.two.agent
    scenario.prompt_template = data.two.template
    scenario.prompt_version = data.two.version
    scenario.save(
        update_fields=[
            "dataset",
            "agent_definition",
            "prompt_template",
            "prompt_version",
        ]
    )


def _r2_assert_only_local_scenario(response, data, route, omitted):
    assert response.status_code == 200
    result = _result(response)
    if route == "list":
        assert result["count"] == 1
        assert len(result["results"]) == 1
        result = result["results"][0]
    assert str(result["id"]) == str(data.two.run.id)
    assert [str(item) for item in result["scenarios"]] == [str(data.two.scenario.id)]
    assert [str(item["id"]) for item in result["scenarios_detail"]] == [
        str(data.two.scenario.id)
    ]
    detail = result["scenarios_detail"][0]
    assert detail["name"] == data.two.scenario.name
    assert detail["description"] == data.two.scenario.description
    assert detail["agent"]["prompt"] == data.two.simulator.prompt
    assert str(detail["dataset"]) == str(data.two.dataset.id)
    assert str(detail["prompt_template"]) == str(data.two.template.id)
    assert str(detail["prompt_version"]) == str(data.two.version.id)
    encoded = json.dumps(response.data, default=str)
    for value in omitted:
        assert value not in encoded
    _assert_no_private_content(response, data.one)


@pytest.mark.parametrize("route", ["list", "detail", "patch-response"])
def test_r2_prompt_response_omits_linked_foreign_scenario(scope_data, route):
    data = scope_data
    _r2_link_same_workspace_references(data)
    data.one.simulator.initial_message = "W1-private-initial-message-r2"
    data.one.simulator.save(update_fields=["initial_message"])
    data.two.run.scenarios.add(data.one.scenario)
    before = set(data.two.run.scenarios.values_list("id", flat=True))
    assert before == {data.one.scenario.id, data.two.scenario.id}

    response = _r2_prompt_response(data, route)

    _r2_assert_only_local_scenario(
        response,
        data,
        route,
        [
            str(data.one.scenario.id),
            data.one.scenario.name,
            data.one.scenario.description,
            str(data.one.simulator.id),
            data.one.simulator.name,
            data.one.simulator.prompt,
            data.one.simulator.initial_message,
        ],
    )
    # Read-time filtering must not silently repair or delete persisted links.
    assert set(data.two.run.scenarios.values_list("id", flat=True)) == before


@pytest.mark.parametrize("route", ["list", "detail", "patch-response"])
@pytest.mark.parametrize(
    "field,resource",
    [
        ("simulator_agent", "simulator"),
        ("dataset", "dataset"),
        ("agent_definition", "agent"),
        ("prompt_template", "template"),
        ("prompt_version", "version"),
    ],
)
def test_r2_prompt_response_omits_scenario_with_foreign_nested_reference(
    scope_data, route, field, resource
):
    data = scope_data
    _r2_link_same_workspace_references(data)
    data.one.version.commit_message = "W1-private-version-message-r2"
    data.one.version.save(update_fields=["commit_message"])
    references = {
        "simulator_agent": data.two.simulator,
        "dataset": data.two.dataset,
        "agent_definition": data.two.agent,
        "prompt_template": data.two.template,
        "prompt_version": data.two.version,
    }
    foreign = getattr(data.one, resource)
    references[field] = foreign
    mixed = Scenarios.objects.create(
        name=f"Rejected mixed scenario {field}",
        description=f"Rejected mixed description {field}",
        scenario_type="graph",
        organization=data.organization,
        workspace=data.w2,
        **references,
    )
    data.two.run.scenarios.add(mixed)
    assert mixed.workspace_id == data.w2.id
    assert getattr(mixed, f"{field}_id") == foreign.id

    response = _r2_prompt_response(data, route)

    _r2_assert_only_local_scenario(
        response,
        data,
        route,
        [
            str(mixed.id),
            mixed.name,
            mixed.description,
            str(foreign.id),
            data.one.simulator.name,
            data.one.simulator.prompt,
            data.one.dataset.name,
            data.one.template.name,
            data.one.version.commit_message,
        ],
    )
    assert set(data.two.run.scenarios.values_list("id", flat=True)) == {
        mixed.id,
        data.two.scenario.id,
    }


@pytest.mark.parametrize("route", ["list", "detail"])
def test_r2_prompt_response_keeps_deleted_scenario_hidden(scope_data, route):
    data = scope_data
    _r2_link_same_workspace_references(data)
    deleted = Scenarios.objects.create(
        name="Deleted scenario r2",
        description="Deleted description r2",
        scenario_type="graph",
        organization=data.organization,
        workspace=data.w2,
        simulator_agent=data.two.simulator,
    )
    data.two.run.scenarios.add(deleted)
    Scenarios.objects.filter(id=deleted.id).update(deleted=True)

    response = _r2_prompt_response(data, route)

    _r2_assert_only_local_scenario(
        response,
        data,
        route,
        [
            str(deleted.id),
            deleted.name,
            deleted.description,
        ],
    )


@pytest.mark.parametrize("invalid_kind", ["foreign", "deleted", "missing"])
def test_r2_patch_rejects_entire_invalid_scenario_set(scope_data, invalid_kind):
    from simulate.views.prompt_simulation import PromptSimulationDetailView

    data = scope_data
    replacement = Scenarios.objects.create(
        name="Valid replacement r2",
        scenario_type="graph",
        organization=data.organization,
        workspace=data.w2,
        simulator_agent=data.two.simulator,
    )
    if invalid_kind == "foreign":
        invalid_id = data.one.scenario.id
    elif invalid_kind == "deleted":
        deleted = Scenarios.objects.create(
            name="Deleted attachment r2",
            scenario_type="graph",
            organization=data.organization,
            workspace=data.w2,
        )
        Scenarios.objects.filter(id=deleted.id).update(deleted=True)
        invalid_id = deleted.id
    else:
        invalid_id = uuid.uuid4()
    before_ids = set(data.two.run.scenarios.values_list("id", flat=True))
    before_name = data.two.run.name
    responses = []
    for rejected_id in (invalid_id, uuid.uuid4()):
        response = PromptSimulationDetailView.as_view()(
            _request(
                data,
                "patch",
                {
                    "scenario_ids": [str(replacement.id), str(rejected_id)],
                    "name": "Must not be persisted",
                },
                workspace=data.w2,
            ),
            prompt_template_id=data.two.template.id,
            run_test_id=data.two.run.id,
        )
        assert response.status_code == 404
        _assert_no_private_content(response, data.one)
        assert str(rejected_id) not in json.dumps(response.data, default=str)
        responses.append(response.data)
        data.two.run.refresh_from_db()
        assert data.two.run.name == before_name
        assert set(data.two.run.scenarios.values_list("id", flat=True)) == before_ids
    assert responses[0] == responses[1]


@pytest.mark.parametrize("selection", ["replace", "duplicate", "clear"])
def test_r2_patch_same_workspace_attachment_controls(scope_data, selection):
    from simulate.views.prompt_simulation import PromptSimulationDetailView

    data = scope_data
    replacement = Scenarios.objects.create(
        name="Allowed replacement r2",
        scenario_type="graph",
        organization=data.organization,
        workspace=data.w2,
        simulator_agent=data.two.simulator,
    )
    ids = [] if selection == "clear" else [str(replacement.id)]
    if selection == "duplicate":
        ids += ids
    response = PromptSimulationDetailView.as_view()(
        _request(data, "patch", {"scenario_ids": ids}, workspace=data.w2),
        prompt_template_id=data.two.template.id,
        run_test_id=data.two.run.id,
    )
    assert response.status_code == 200
    expected = set() if selection == "clear" else {replacement.id}
    assert set(data.two.run.scenarios.values_list("id", flat=True)) == expected
    result = _result(response)
    assert {str(item) for item in result["scenarios"]} == {str(pk) for pk in expected}
    assert {str(item["id"]) for item in result["scenarios_detail"]} == {
        str(pk) for pk in expected
    }


@pytest.mark.parametrize("method", ["get", "patch"])
@pytest.mark.parametrize(
    "request_workspace_value", [_MISSING, None], ids=["missing", "none"]
)
def test_r2_prompt_detail_requires_request_workspace(
    scope_data, method, request_workspace_value, monkeypatch, django_assert_num_queries
):
    from simulate.views.prompt_simulation import PromptSimulationDetailView

    data = scope_data
    before = set(data.two.run.scenarios.values_list("id", flat=True))
    validation = Mock(side_effect=AssertionError("Update validation must not run"))
    monkeypatch.setattr(
        prompt_views.PromptSimulationUpdateRequestSerializer, "is_valid", validation
    )
    with django_assert_num_queries(0):
        response = PromptSimulationDetailView.as_view()(
            _request(
                data, method, {"scenario_ids": []}, workspace=request_workspace_value
            ),
            prompt_template_id=data.two.template.id,
            run_test_id=data.two.run.id,
        )
    validation.assert_not_called()
    assert response.status_code == 403
    response.render()
    assert response["Content-Type"].startswith("application/json")
    encoded = json.dumps(response.data, default=str)
    assert "workspace_required" in encoded
    for resource in (data.one, data.two):
        _assert_no_private_content(response, resource)
    assert str(data.w2.id) not in encoded
    assert str(data.organization.id) not in encoded
    assert set(data.two.run.scenarios.values_list("id", flat=True)) == before


@pytest.mark.parametrize("method", ["get", "patch"])
@pytest.mark.parametrize("root", ["template", "run"])
def test_r2_prompt_detail_foreign_root_is_missing(scope_data, method, root):
    from simulate.views.prompt_simulation import PromptSimulationDetailView

    data = scope_data
    if root == "template":
        template = data.one.template
        foreign_run = data.one.run
    else:
        template = data.two.template
        foreign_run = RunTest.objects.create(
            name="Foreign detail run r2",
            source_type="prompt",
            prompt_template=template,
            prompt_version=data.two.version,
            organization=data.organization,
            workspace=data.w1,
        )
        foreign_run.scenarios.set([data.one.scenario])
    before = set(foreign_run.scenarios.values_list("id", flat=True))
    responses = []
    for run_id in (foreign_run.id, uuid.uuid4()):
        response = PromptSimulationDetailView.as_view()(
            _request(data, method, {"scenario_ids": []}, workspace=data.w2),
            prompt_template_id=template.id,
            run_test_id=run_id,
        )
        assert response.status_code == 404
        _assert_no_private_content(response, data.one)
        assert str(foreign_run.id) not in json.dumps(response.data, default=str)
        responses.append(response.data)
        assert set(foreign_run.scenarios.values_list("id", flat=True)) == before
    assert responses[0] == responses[1]


def test_r2_prompt_create_rejects_deleted_scenario_before_any_write(scope_data):
    data = scope_data
    deleted = Scenarios.objects.create(
        name="Deleted create attachment r2",
        scenario_type="graph",
        organization=data.organization,
        workspace=data.w2,
    )
    Scenarios.objects.filter(id=deleted.id).update(deleted=True)
    before = RunTest.objects.count()
    body = _prompt_body(data.two)
    body["scenario_ids"].append(str(deleted.id))
    response = PromptSimulationListCreateView.as_view()(
        _request(data, "post", body, workspace=data.w2),
        prompt_template_id=data.two.template.id,
    )
    assert response.status_code == 404
    assert str(deleted.id) not in json.dumps(response.data, default=str)
    assert RunTest.objects.count() == before


def _r3_grant_workspace_access(data):
    membership = OrganizationMembership.no_workspace_objects.create(
        user=data.user,
        organization=data.organization,
        role=OrganizationRoles.OWNER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        workspace=data.w2,
        user=data.user,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        organization_membership=membership,
        is_active=True,
    )


@pytest.mark.parametrize("context_kind", ["header", "middleware"])
@pytest.mark.parametrize(
    "route",
    ["prompt-get", "prompt-post", "detail-get", "detail-patch", "scenario-post"],
)
def test_r3_authorized_context_preserves_views(
    scope_data, scenario_boundaries, context_kind, route
):
    data = scope_data
    _r3_grant_workspace_access(data)
    method = route.split("-")[1]
    body = (
        _scenario_body(data.two) if route == "scenario-post" else _prompt_body(data.two)
    )
    if method == "patch":
        body = {"name": "Authorized update"}
    elif method == "get":
        body = None
    request = _request(data, method, body)
    if context_kind == "header":
        request.META["HTTP_X_WORKSPACE_ID"] = str(data.w2.id)
    else:
        set_workspace_context(
            workspace=data.w2, organization=data.organization, user=data.user
        )
    try:
        if route == "scenario-post":
            response = scenario_views.CreateScenarioView.as_view()(request)
        elif route.startswith("detail-"):
            response = prompt_views.PromptSimulationDetailView.as_view()(
                request,
                prompt_template_id=data.two.template.id,
                run_test_id=data.two.run.id,
            )
        else:
            response = PromptSimulationListCreateView.as_view()(
                request, prompt_template_id=data.two.template.id
            )
    finally:
        set_workspace_context(workspace=None, organization=None, user=None)
    expected = (
        202 if route == "scenario-post" else 201 if route == "prompt-post" else 200
    )
    assert response.status_code == expected
    _assert_no_private_content(response, data.one)
    result = _result(response)
    if route == "prompt-post":
        assert RunTest.objects.get(id=result["id"]).workspace_id == data.w2.id
    elif route == "scenario-post":
        assert Scenarios.objects.get(name="Scoped scenario").workspace_id == data.w2.id
        scenario_boundaries.assert_called_once()
    elif route == "prompt-get":
        assert [str(row["id"]) for row in result["results"]] == [str(data.two.run.id)]
    else:
        assert str(result["id"]) == str(data.two.run.id)


_WORKSPACE_HEADER = "x-workspace-id"
_WORKSPACE_META_KEY = "HTTP_X_WORKSPACE_ID"


class _ObservedMeta(dict):
    """Request environ that records every X-Workspace-Id lookup, then delegates."""

    def __init__(self, environ, on_read):
        super().__init__(environ)
        self._on_read = on_read

    def _observe(self, key):
        if key == _WORKSPACE_META_KEY:
            self._on_read("header")

    def __getitem__(self, key):
        self._observe(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._observe(key)
        return super().get(key, default)

    def __contains__(self, key):
        self._observe(key)
        return super().__contains__(key)


class _ObservedHeaders(HttpHeaders):
    """request.headers view that records every X-Workspace-Id lookup.

    Mapping.get and ``in`` route through __getitem__; construction iterates
    items() and is deliberately not recorded.
    """

    def __init__(self, environ, on_read):
        super().__init__(environ)
        self._on_read = on_read

    def __getitem__(self, key):
        if isinstance(key, str) and key.replace("_", "-").lower() == _WORKSPACE_HEADER:
            self._on_read("header")
        return super().__getitem__(key)


class _ObservedContextVar:
    """Delegating workspace ContextVar that records every read."""

    def __init__(self, variable, on_read):
        self._variable = variable
        self._on_read = on_read

    def get(self, *default):
        self._on_read("context")
        return self._variable.get(*default)

    def set(self, value):
        return self._variable.set(value)

    def reset(self, token):
        return self._variable.reset(token)


def _observe_workspace_resolution(monkeypatch, request, on_resolution, context=False):
    """Observe IR's actual workspace resolution seams for one prompt request.

    IR has no prompt workspace helper. It resolves request.workspace in
    APIKeyAuthentication._set_workspace_context (accounts/authentication.py:408);
    its header resolvers read X-Workspace-Id (accounts/authentication.py:647,689;
    simulate/services/harness_provider.py:123) and check membership through
    User.can_access_workspace (accounts/models/user.py:217). Every observer
    records first and then delegates to the real code; none supplies a workspace.
    ``context`` adds the middleware ContextVars (workspace_context.py:14,17); it
    is not used where ORM managers legitimately read them.
    """
    original_resolver = APIKeyAuthentication._set_workspace_context
    user_model = get_user_model()
    original_membership = user_model.can_access_workspace

    def observe_resolver(self, resolver_request, user):
        on_resolution("resolver")
        return original_resolver(self, resolver_request, user)

    def observe_membership(user, workspace):
        on_resolution("membership")
        return original_membership(user, workspace)

    monkeypatch.setattr(
        APIKeyAuthentication, "_set_workspace_context", observe_resolver
    )
    monkeypatch.setattr(user_model, "can_access_workspace", observe_membership)
    # WSGIRequest keeps environ and META as one dict; observe both names.
    environ = _ObservedMeta(request.META, on_resolution)
    request.environ = request.META = environ
    request.headers = _ObservedHeaders(environ, on_resolution)
    if context:
        for name in ("_current_workspace", "_workspace_scope_required"):
            monkeypatch.setattr(
                workspace_context_module,
                name,
                _ObservedContextVar(
                    getattr(workspace_context_module, name), on_resolution
                ),
            )


@pytest.mark.parametrize("field", ["page", "limit"])
@pytest.mark.parametrize("value", [0, 101, "not-an-integer"])
def test_r3_invalid_pagination_precedes_workspace_resolution(
    field, value, monkeypatch, django_assert_num_queries
):
    # IR has no prompt workspace helper; observe the real resolution seams
    # instead. Any resolver, header, membership or workspace-context
    # consultation before pagination validation fails, even with zero queries.
    consulted = []

    def fail_resolution(kind):
        consulted.append(kind)
        raise AssertionError("Workspace lookup must not run")

    resolve = Mock(side_effect=AssertionError("Tenant lookup must not run"))
    monkeypatch.setattr(prompt_views, "get_request_organization", resolve)
    request = APIRequestFactory().get("/simulate/workspace-scope-test/", {field: value})
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    _observe_workspace_resolution(monkeypatch, request, fail_resolution, context=True)
    with django_assert_num_queries(0):
        response = PromptSimulationListCreateView.as_view()(
            request, prompt_template_id=uuid.uuid4()
        )
    assert response.status_code == 400
    assert field in response.data["details"]
    resolve.assert_not_called()
    assert consulted == []


@pytest.mark.parametrize("resolver_name", ["prompt", "scenario"])
@pytest.mark.parametrize(
    "denial", ["foreign-organization", "inactive", "missing", "membership"]
)
def test_r3_header_workspace_requires_organization_active_membership(
    scope_data, resolver_name, denial
):
    data = scope_data
    if denial != "membership":
        _r3_grant_workspace_access(data)
    target = data.w2
    if denial == "foreign-organization":
        foreign_org = Organization.objects.create(name="Foreign header organization")
        target = Workspace.no_workspace_objects.create(
            name="Foreign header workspace",
            organization=foreign_org,
            created_by=data.user,
        )
    elif denial == "inactive":
        Workspace.no_workspace_objects.filter(id=target.id).update(is_active=False)
    workspace_id = uuid.uuid4() if denial == "missing" else target.id
    before = _r4_state(data)
    request = _helper_request(data)
    request.headers["X-Workspace-Id"] = str(workspace_id)
    # An explicit rejected header must never fall back to otherwise valid middleware.
    set_workspace_context(
        workspace=data.w1, organization=data.organization, user=data.user
    )
    try:
        if resolver_name == "prompt":
            # Target W1 deliberately: accepting the ambient fallback would expose
            # a real row, rather than merely failing to find a W2 template.
            response = _r4_call(
                data, "prompt-list", resources=data.one, header=workspace_id
            )
        else:
            with pytest.raises(PermissionDenied) as caught:
                scenario_views._request_workspace(request)
    finally:
        set_workspace_context(workspace=None, organization=None, user=None)
    if resolver_name == "prompt":
        assert response.status_code == 403
        response.render()
        assert response["Content-Type"].startswith("application/json")
        encoded = json.dumps(response.data, default=str)
        _assert_no_private_content(response, data.one)
        _assert_no_private_content(response, data.two)
    else:
        encoded = json.dumps(caught.value.detail, default=str)
    assert str(workspace_id) not in encoded
    assert _r4_state(data) == before


@pytest.mark.parametrize("resolver_name", ["prompt", "scenario"])
@pytest.mark.parametrize(
    "context_kind", ["unbound", "different-user", "different-organization"]
)
def test_r3_unbound_middleware_context_is_not_authority(
    scope_data, resolver_name, context_kind, django_assert_num_queries
):
    data = scope_data
    context_user = None if context_kind == "unbound" else data.user
    context_org = data.organization
    if context_kind == "different-user":
        context_user = get_user_model().objects.create_user(
            email=f"other-context-{uuid.uuid4().hex}@example.test"
        )
    elif context_kind == "different-organization":
        context_org = Organization.objects.create(name="Other context organization")
    before = _r4_state(data)
    set_workspace_context(
        workspace=data.w2, organization=context_org, user=context_user
    )
    try:
        with django_assert_num_queries(0):
            if resolver_name == "prompt":
                response = _r4_call(data, "prompt-list")
            else:
                with pytest.raises(PermissionDenied) as caught:
                    scenario_views._request_workspace(_helper_request(data))
    finally:
        set_workspace_context(workspace=None, organization=None, user=None)
    if resolver_name == "prompt":
        assert response.status_code == 403
        response.render()
        assert response["Content-Type"].startswith("application/json")
        encoded = json.dumps(response.data, default=str)
        _assert_no_private_content(response, data.one)
        _assert_no_private_content(response, data.two)
    else:
        encoded = json.dumps(caught.value.detail, default=str)
    assert "workspace_required" in encoded
    assert _r4_state(data) == before


# R4: all exposed handlers, including the custom dispatch action, use real ORM
# reads/validation. Only external execution, workflow and entitlement boundaries
# are replaced. from_db spies observe both manager queries and select_related
# hydration, so an output-only filter cannot satisfy the no-foreign-load oracle.
_R4_NEW_ROUTES = [
    "prompt-delete",
    "prompt-execute",
    "picker",
    "scenario-list",
    "multi",
    "scenario-detail",
    "scenario-delete",
    "scenario-edit",
    "scenario-prompts",
    "scenario-rows",
    "scenario-columns",
]
_R4_ALL_ROUTES = _R4_NEW_ROUTES + [
    "prompt-list",
    "prompt-create",
    "prompt-detail",
    "prompt-patch",
    "scenario-create",
]


@pytest.fixture
def r4_boundaries(scope_data, scenario_boundaries, monkeypatch):
    data = scope_data
    for resources in (data.one, data.two):
        resources.scenario.dataset = resources.dataset
        resources.scenario.status = scenario_views.StatusType.COMPLETED.value
        resources.scenario.save(update_fields=["dataset", "status"])
        Column.objects.create(
            dataset=resources.dataset,
            name="input",
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        Row.objects.create(dataset=resources.dataset, order=0)
    rows = Mock()
    columns = Mock()
    monkeypatch.setattr(scenario_views, "start_add_scenario_rows_workflow_sync", rows)
    monkeypatch.setattr(scenario_views, "start_add_columns_workflow_sync", columns)
    try:
        from ee.usage.services.entitlements import Entitlements
    except ImportError:
        Entitlements = None
    if Entitlements is not None:
        monkeypatch.setattr(
            Entitlements,
            "check_feature",
            Mock(return_value=SimpleNamespace(allowed=True)),
        )
    execute = Mock(
        return_value={
            "success": True,
            "execution_id": str(uuid.uuid4()),
            "run_test_id": str(data.two.run.id),
            "status": "pending",
            "total_scenarios": 1,
            "total_calls": 1,
        }
    )
    monkeypatch.setattr(
        prompt_views,
        "TestExecutor",
        Mock(return_value=SimpleNamespace(execute_test=execute)),
    )
    return SimpleNamespace(rows=rows, columns=columns, execute=execute)


def _r4_route(data, route, resources=None, object_id=None, payload=None):
    resources = resources or data.two
    scenario_id = resources.scenario.id if object_id is None else object_id
    run_id = resources.run.id if object_id is None else object_id
    prompt_kwargs = {"prompt_template_id": resources.template.id}
    detail_kwargs = {**prompt_kwargs, "run_test_id": run_id}
    scenario_kwargs = {"scenario_id": scenario_id}
    routes = {
        "prompt-list": (PromptSimulationListCreateView, "get", {}, prompt_kwargs),
        "prompt-create": (
            PromptSimulationListCreateView,
            "post",
            _prompt_body(resources),
            prompt_kwargs,
        ),
        "prompt-detail": (
            prompt_views.PromptSimulationDetailView,
            "get",
            {},
            detail_kwargs,
        ),
        "prompt-patch": (
            prompt_views.PromptSimulationDetailView,
            "patch",
            {"name": "Updated local run"},
            detail_kwargs,
        ),
        "prompt-delete": (
            prompt_views.PromptSimulationDetailView,
            "delete",
            {},
            detail_kwargs,
        ),
        "prompt-execute": (
            prompt_views.ExecutePromptSimulationView,
            "post",
            {},
            detail_kwargs,
        ),
        "picker": (prompt_views.PromptSimulationScenariosView, "get", {}, {}),
        "scenario-list": (scenario_views.ScenariosListView, "get", {}, {}),
        "multi": (
            scenario_views.ScenariosListView,
            "get",
            {"scenarios": json.dumps([str(scenario_id)])},
            {"action": "multi-dataset"},
        ),
        "scenario-create": (
            scenario_views.CreateScenarioView,
            "post",
            _scenario_body(resources),
            {},
        ),
        "scenario-detail": (
            scenario_views.ScenarioDetailView,
            "get",
            {},
            scenario_kwargs,
        ),
        "scenario-delete": (
            scenario_views.DeleteScenarioView,
            "delete",
            {},
            scenario_kwargs,
        ),
        "scenario-edit": (
            scenario_views.EditScenarioView,
            "put",
            {"name": "Updated local scenario", "prompt": "Updated local prompt"},
            scenario_kwargs,
        ),
        "scenario-prompts": (
            scenario_views.EditScenarioPromptsView,
            "put",
            {"prompts": "Updated local prompt"},
            scenario_kwargs,
        ),
        "scenario-rows": (
            scenario_views.AddScenarioRowsView,
            "post",
            {"num_rows": 10},
            scenario_kwargs,
        ),
        "scenario-columns": (
            scenario_views.AddScenarioColumnsView,
            "post",
            {
                "columns": [
                    {
                        "name": "new_column",
                        "data_type": "text",
                        "description": "Local column",
                    }
                ]
            },
            scenario_kwargs,
        ),
    }
    view, method, body, kwargs = routes[route]
    return view, method, body if payload is None else payload, kwargs


def _r4_call(
    data,
    route,
    resources=None,
    object_id=None,
    payload=None,
    request_workspace_value=_MISSING,
    header=None,
):
    view, method, body, kwargs = _r4_route(data, route, resources, object_id, payload)
    request = _request(data, method, body, workspace=request_workspace_value)
    if header is not None:
        request.META["HTTP_X_WORKSPACE_ID"] = str(header)
    return view.as_view()(request, **kwargs)


def _r4_state(data):
    """Persisted-state oracle, called outside each instrumented request window."""
    result = []
    for resources in (data.one, data.two):
        for obj in (
            resources.run,
            resources.scenario,
            resources.simulator,
            resources.dataset,
        ):
            result.append(list(type(obj).objects.filter(pk=obj.pk).values()))
        result.append(set(resources.run.scenarios.values_list("id", flat=True)))
        for model in (Row, Column, Cell):
            result.append(
                list(model.objects.filter(dataset=resources.dataset).values())
            )
    return result


@pytest.fixture
def r4_loads(monkeypatch):
    loaded = {}
    for model in (
        Scenarios,
        Dataset,
        SimulatorAgent,
        AgentDefinition,
        PromptTemplate,
        PromptVersion,
        RunTest,
    ):
        rows = loaded[model] = []
        original = model.from_db

        def observe(cls, db, field_names, values, _original=original, _rows=rows):
            obj = _original(db, field_names, values)
            _rows.append(obj.pk)
            return obj

        monkeypatch.setattr(model, "from_db", classmethod(observe))
    return loaded


def _r4_assert_no_foreign_loads(loaded, data):
    for resource in vars(data.one).values():
        assert resource.pk not in loaded[type(resource)]


@pytest.mark.parametrize("route", _R4_ALL_ROUTES)
@pytest.mark.parametrize(
    "request_workspace_value", [_MISSING, None], ids=["missing", "none"]
)
def test_r4_every_route_refuses_missing_context_before_queries(
    scope_data, r4_boundaries, route, request_workspace_value, django_assert_num_queries
):
    data = scope_data
    before = _r4_state(data)
    with django_assert_num_queries(0):
        response = _r4_call(
            data, route, request_workspace_value=request_workspace_value
        )
    assert response.status_code == 403
    response.render()
    assert response["Content-Type"].startswith("application/json")
    assert "workspace_required" in json.dumps(response.data, default=str)
    _assert_no_private_content(response, data.one)
    assert _r4_state(data) == before
    for boundary in vars(r4_boundaries).values():
        boundary.assert_not_called()


@pytest.mark.parametrize("route", _R4_ALL_ROUTES)
def test_r4_every_route_refuses_foreign_header_before_tenant_managers(
    scope_data, r4_boundaries, route, monkeypatch
):
    data = scope_data
    foreign_org = Organization.objects.create(name="R4 foreign organization")
    foreign_ws = Workspace.no_workspace_objects.create(
        name="R4 foreign workspace", organization=foreign_org, created_by=data.user
    )
    before = _r4_state(data)
    forbidden = Mock(
        side_effect=AssertionError("Tenant manager reached before refusal")
    )
    with monkeypatch.context() as context:
        for model in (RunTest, Scenarios, PromptTemplate, Dataset, SimulatorAgent):
            for manager_name in ("objects", "no_workspace_objects"):
                context.setattr(getattr(model, manager_name), "get_queryset", forbidden)
        response = _r4_call(data, route, header=foreign_ws.id)
    forbidden.assert_not_called()
    assert response.status_code == 403
    response.render()
    assert response["Content-Type"].startswith("application/json")
    assert str(foreign_ws.id) not in json.dumps(response.data, default=str)
    assert _r4_state(data) == before


@pytest.mark.parametrize(
    "route",
    [
        "prompt-delete",
        "prompt-execute",
        "scenario-detail",
        "scenario-delete",
        "scenario-edit",
        "scenario-prompts",
        "scenario-rows",
        "scenario-columns",
    ],
)
def test_r4_foreign_detail_and_mutation_roots_are_missing(
    scope_data, r4_boundaries, r4_loads, route
):
    data = scope_data
    before = _r4_state(data)
    for rows in r4_loads.values():
        rows.clear()
    responses = []
    for object_id in (None, uuid.uuid4()):
        response = _r4_call(
            data,
            route,
            resources=data.one,
            object_id=object_id,
            request_workspace_value=data.w2,
        )
        assert response.status_code == 404
        _assert_no_private_content(response, data.one)
        responses.append(response.data)
    assert responses[0] == responses[1]
    _r4_assert_no_foreign_loads(r4_loads, data)
    assert _r4_state(data) == before
    for boundary in vars(r4_boundaries).values():
        boundary.assert_not_called()


@pytest.mark.parametrize("route", ["prompt-delete", "prompt-execute"])
def test_r4_prompt_run_scope_independent_of_local_template(
    scope_data, r4_boundaries, route
):
    data = scope_data
    foreign_run = RunTest.objects.create(
        name="Foreign run under local template R4",
        source_type="prompt",
        prompt_template=data.two.template,
        prompt_version=data.two.version,
        organization=data.organization,
        workspace=data.w1,
    )
    response = _r4_call(
        data, route, object_id=foreign_run.id, request_workspace_value=data.w2
    )
    assert response.status_code == 404
    foreign_run.refresh_from_db()
    assert foreign_run.deleted is False
    r4_boundaries.execute.assert_not_called()


@pytest.mark.parametrize("route", ["picker", "scenario-list", "multi"])
def test_r4_collections_exclude_foreign_workspace_rows(
    scope_data, r4_boundaries, r4_loads, route
):
    data = scope_data
    before = _r4_state(data)
    for rows in r4_loads.values():
        rows.clear()
    response = _r4_call(
        data, route, resources=data.one, request_workspace_value=data.w2
    )
    assert response.status_code == 200
    _assert_no_private_content(response, data.one)
    _r4_assert_no_foreign_loads(r4_loads, data)
    if route == "multi":
        assert response.data["column_configs"] == []
    else:
        result = _result(response)
        assert result["count"] == 1
        assert [str(row["id"]) for row in result["results"]] == [
            str(data.two.scenario.id)
        ]
    assert _r4_state(data) == before


@pytest.mark.parametrize("context_kind", ["request", "header", "middleware"])
@pytest.mark.parametrize("route", _R4_NEW_ROUTES)
def test_r4_authorized_routes_keep_same_workspace_behavior(
    scope_data, r4_boundaries, context_kind, route
):
    data = scope_data
    _r3_grant_workspace_access(data)
    before_foreign = _r4_state(data)[:8]
    if context_kind == "middleware":
        set_workspace_context(
            workspace=data.w2, organization=data.organization, user=data.user
        )
    try:
        response = _r4_call(
            data,
            route,
            request_workspace_value=data.w2 if context_kind == "request" else _MISSING,
            header=data.w2.id if context_kind == "header" else None,
        )
    finally:
        set_workspace_context(workspace=None, organization=None, user=None)
    assert response.status_code == (
        202 if route in {"scenario-rows", "scenario-columns"} else 200
    )
    _assert_no_private_content(response, data.one)
    assert _r4_state(data)[:8] == before_foreign
    if route == "prompt-execute":
        r4_boundaries.execute.assert_called_once()
        assert r4_boundaries.execute.call_args.kwargs["scenario_ids"] == [
            str(data.two.scenario.id)
        ]
    elif route == "scenario-rows":
        r4_boundaries.rows.assert_called_once()
    elif route == "scenario-columns":
        r4_boundaries.columns.assert_called_once()
    elif route == "prompt-delete":
        data.two.run.refresh_from_db()
        assert data.two.run.deleted is True
    elif route == "scenario-delete":
        data.two.scenario.refresh_from_db()
        assert data.two.scenario.deleted is True
    elif route in {"scenario-edit", "scenario-prompts"}:
        data.two.simulator.refresh_from_db()
        assert data.two.simulator.prompt == "Updated local prompt"


@pytest.mark.parametrize("route", ["list", "detail", "patch-response"])
@pytest.mark.parametrize(
    "field,resource",
    [
        ("dataset", "dataset"),
        ("simulator_agent", "simulator"),
        ("agent_definition", "agent"),
        ("prompt_template", "template"),
        ("prompt_version", "version"),
    ],
)
def test_r4_prompt_prefetch_never_loads_foreign_scenarios_or_relations(
    scope_data, r4_loads, route, field, resource
):
    data = scope_data
    _r2_link_same_workspace_references(data)
    mixed = Scenarios.objects.create(
        name="R4 mixed reference",
        scenario_type="graph",
        organization=data.organization,
        workspace=data.w2,
        **{field: getattr(data.one, resource)},
    )
    data.two.run.scenarios.add(data.one.scenario, mixed)
    for rows in r4_loads.values():
        rows.clear()
    response = _r2_prompt_response(data, route)
    _r4_assert_no_foreign_loads(r4_loads, data)
    assert mixed.id not in r4_loads[Scenarios]
    assert data.two.scenario.id in r4_loads[Scenarios]
    assert data.two.dataset.id in r4_loads[Dataset]
    assert data.two.simulator.id in r4_loads[SimulatorAgent]
    _r2_assert_only_local_scenario(response, data, route, [str(mixed.id), mixed.name])
    assert set(data.two.run.scenarios.values_list("id", flat=True)) == {
        data.two.scenario.id,
        data.one.scenario.id,
        mixed.id,
    }


@pytest.mark.parametrize(
    "route",
    [
        "scenario-detail",
        "scenario-delete",
        "scenario-edit",
        "scenario-prompts",
        "scenario-rows",
        "scenario-columns",
        "scenario-list",
        "multi",
        "picker",
    ],
)
@pytest.mark.parametrize(
    "field,resource",
    [
        ("dataset", "dataset"),
        ("simulator_agent", "simulator"),
        ("agent_definition", "agent"),
        ("prompt_template", "template"),
        ("prompt_version", "version"),
    ],
)
def test_r4_scenario_routes_reject_mixed_references_before_loading(
    scope_data, r4_boundaries, r4_loads, route, field, resource
):
    data = scope_data
    mixed = Scenarios.objects.create(
        name="R4 forbidden mixed scenario",
        scenario_type="graph",
        organization=data.organization,
        workspace=data.w2,
        **{field: getattr(data.one, resource)},
    )
    before = _r4_state(data)
    for rows in r4_loads.values():
        rows.clear()
    response = _r4_call(
        data, route, object_id=mixed.id, request_workspace_value=data.w2
    )
    collection = route in {"scenario-list", "multi", "picker"}
    assert response.status_code == (200 if collection else 404)
    _r4_assert_no_foreign_loads(r4_loads, data)
    assert mixed.id not in r4_loads[Scenarios]
    assert str(mixed.id) not in json.dumps(response.data, default=str)
    assert _r4_state(data) == before
    mixed.refresh_from_db()
    assert mixed.deleted is False
    assert getattr(mixed, f"{field}_id") == getattr(data.one, resource).id
    for boundary in vars(r4_boundaries).values():
        boundary.assert_not_called()


def test_r4_multi_dataset_ids_require_organization_even_with_same_workspace(
    scope_data, r4_boundaries, r4_loads
):
    data = scope_data
    foreign_org = Organization.objects.create(name="R4 other dataset organization")
    foreign_dataset = Dataset.objects.create(
        name="R4 other organization dataset",
        organization=foreign_org,
        workspace=data.w2,
    )
    foreign_scenario = Scenarios.objects.create(
        name="R4 other organization scenario",
        scenario_type="dataset",
        organization=foreign_org,
        workspace=data.w2,
        dataset=foreign_dataset,
    )
    for rows in r4_loads.values():
        rows.clear()
    response = _r4_call(
        data, "multi", object_id=foreign_scenario.id, request_workspace_value=data.w2
    )
    assert response.status_code == 200
    assert response.data["column_configs"] == []
    assert foreign_scenario.id not in r4_loads[Scenarios]
    assert foreign_dataset.id not in r4_loads[Dataset]


@pytest.mark.parametrize("selection", ["explicit", "all", "exclude"])
def test_r4_execute_only_dispatches_scoped_linked_scenarios(
    scope_data, r4_boundaries, r4_loads, selection
):
    data = scope_data
    data.two.run.scenarios.add(data.one.scenario)
    payload = {"scenario_ids": [str(data.one.scenario.id)]}
    if selection == "all":
        payload = {"select_all": True}
    elif selection == "exclude":
        payload["select_all"] = True
    response = _r4_call(
        data, "prompt-execute", payload=payload, request_workspace_value=data.w2
    )
    _r4_assert_no_foreign_loads(r4_loads, data)
    if selection == "all":
        assert response.status_code == 200
        r4_boundaries.execute.assert_called_once()
        assert r4_boundaries.execute.call_args.kwargs["scenario_ids"] == [
            str(data.two.scenario.id)
        ]
    else:
        assert response.status_code == 404
        r4_boundaries.execute.assert_not_called()
    assert set(data.two.run.scenarios.values_list("id", flat=True)) == {
        data.one.scenario.id,
        data.two.scenario.id,
    }


@pytest.mark.parametrize(
    "route,payload",
    [
        ("prompt-execute", {"scenario_ids": ["not-a-uuid"]}),
        ("scenario-list", {"agent_definition_id": "not-a-uuid"}),
        ("multi", {"scenarios": "not-json"}),
        ("scenario-edit", {"name": ""}),
        ("scenario-prompts", {}),
        ("scenario-rows", {"num_rows": 0}),
    ],
)
def test_r4_pure_input_validation_still_precedes_workspace_queries(
    scope_data, route, payload, django_assert_num_queries
):
    with django_assert_num_queries(0):
        response = _r4_call(scope_data, route, payload=payload)
    assert response.status_code == 400


@pytest.mark.parametrize(
    "route", ["scenario-detail", "scenario-edit", "scenario-delete"]
)
def test_r4_graph_rows_have_independent_tenant_scope(
    scope_data, r4_boundaries, monkeypatch, route
):
    data = scope_data
    graph_model = scenario_views.ScenarioGraph
    # Graphs inherit workspace through their scenario; there is no graph
    # workspace field. Keep both the same-org W1 and independent-org controls.
    local_graph = graph_model.objects.create(
        scenario=data.two.scenario,
        name="Local graph",
        organization=data.organization,
        is_active=True,
        graph_config={"graph_data": {"nodes": [], "edges": []}, "source": "local"},
    )
    foreign_graph = graph_model.objects.create(
        scenario=data.one.scenario,
        name="Foreign workspace graph",
        organization=data.organization,
        is_active=True,
        graph_config={"graph_data": {"private": "W1 graph content"}},
    )
    foreign_org = Organization.objects.create(name="R5 foreign graph organization")
    mixed_graph = graph_model.objects.create(
        scenario=data.two.scenario,
        name="Foreign organization graph under local scenario",
        organization=foreign_org,
        is_active=True,
        graph_config={"graph_data": {"private": "Foreign organization graph content"}},
    )
    # Persist distinct timestamps; insertion order and the wall clock are not
    # evidence that an organization-blind first() would select the hostile row.
    local_created_at = datetime(2020, 1, 1, tzinfo=UTC)
    mixed_created_at = local_created_at + timedelta(days=1)
    assert (
        graph_model.objects.filter(pk=local_graph.pk).update(
            created_at=local_created_at
        )
        == 1
    )
    assert (
        graph_model.objects.filter(pk=mixed_graph.pk).update(
            created_at=mixed_created_at
        )
        == 1
    )
    local_graph.refresh_from_db()
    mixed_graph.refresh_from_db()
    assert local_graph.created_at == local_created_at
    assert mixed_graph.created_at == mixed_created_at
    assert mixed_graph.created_at > local_graph.created_at
    assert local_graph.scenario_id == mixed_graph.scenario_id == data.two.scenario.id
    assert local_graph.organization_id == data.organization.id
    assert mixed_graph.organization_id == foreign_org.id != data.organization.id
    assert local_graph.is_active is True and mixed_graph.is_active is True
    assert local_graph.deleted is False and mixed_graph.deleted is False
    # Match the detail/edit selection with only organization_id omitted, before
    # installing the request-only hydration spy.
    organization_blind_graph = (
        graph_model.objects.filter(
            scenario=data.two.scenario,
            scenario__workspace_id=data.w2.id,
            is_active=True,
        )
        .order_by("-created_at")
        .first()
    )
    assert organization_blind_graph.pk == mixed_graph.pk
    loaded = []
    original = graph_model.from_db

    def observe(cls, db, field_names, values):
        obj = original(db, field_names, values)
        loaded.append(obj.pk)
        return obj

    monkeypatch.setattr(graph_model, "from_db", classmethod(observe))
    edited_graph_data = {"nodes": [{"id": "local-node"}], "edges": []}
    payload = {"graph": edited_graph_data} if route == "scenario-edit" else None
    response = _r4_call(data, route, payload=payload, request_workspace_value=data.w2)
    assert response.status_code == 200
    assert foreign_graph.id not in loaded
    assert mixed_graph.id not in loaded
    assert "W1 graph content" not in json.dumps(response.data, default=str)
    assert "Foreign organization graph content" not in json.dumps(
        response.data, default=str
    )
    if route != "scenario-delete":
        assert local_graph.id in loaded
    foreign_graph.refresh_from_db()
    assert foreign_graph.deleted is False
    assert foreign_graph.deleted_at is None
    assert foreign_graph.graph_config == {"graph_data": {"private": "W1 graph content"}}
    mixed_graph.refresh_from_db()
    assert mixed_graph.deleted is False
    assert mixed_graph.deleted_at is None
    assert mixed_graph.graph_config == {
        "graph_data": {"private": "Foreign organization graph content"}
    }
    local_graph.refresh_from_db()
    if route == "scenario-delete":
        assert local_graph.deleted is True
        assert local_graph.deleted_at is not None
    elif route == "scenario-edit":
        assert local_graph.graph_config == {
            "graph_data": edited_graph_data,
            "source": "local",
        }
    else:
        assert response.data["graph"] == {"nodes": [], "edges": []}


@pytest.mark.parametrize("selection", ["no-local", "mixed-dataset", "foreign-version"])
def test_r4_execute_cannot_fall_back_to_foreign_resources(
    scope_data, r4_boundaries, r4_loads, selection
):
    data = scope_data
    if selection == "foreign-version":
        data.two.run.prompt_version = data.one.version
        data.two.run.save(update_fields=["prompt_version"])
    elif selection == "mixed-dataset":
        data.two.scenario.dataset = data.one.dataset
        data.two.scenario.save(update_fields=["dataset"])
    else:
        data.two.run.scenarios.set([data.one.scenario])
    for rows in r4_loads.values():
        rows.clear()
    response = _r4_call(data, "prompt-execute", request_workspace_value=data.w2)
    assert response.status_code == 400
    _r4_assert_no_foreign_loads(r4_loads, data)
    r4_boundaries.execute.assert_not_called()


@pytest.mark.parametrize("route", ["prompt-create", "prompt-patch"])
@pytest.mark.parametrize(
    "field,resource", [("dataset", "dataset"), ("simulator_agent", "simulator")]
)
def test_r4_prompt_writes_refuse_mixed_scenario_attachments(
    scope_data, r4_boundaries, r4_loads, route, field, resource
):
    data = scope_data
    mixed = Scenarios.objects.create(
        name="R4 mixed attachment",
        scenario_type="graph",
        organization=data.organization,
        workspace=data.w2,
        **{field: getattr(data.one, resource)},
    )
    payload = _prompt_body(data.two) if route == "prompt-create" else {}
    payload["scenario_ids"] = [str(data.two.scenario.id), str(mixed.id)]
    before = _r4_state(data)
    runs_before = RunTest.objects.count()
    for rows in r4_loads.values():
        rows.clear()
    response = _r4_call(data, route, payload=payload, request_workspace_value=data.w2)
    assert response.status_code == 404
    _r4_assert_no_foreign_loads(r4_loads, data)
    assert mixed.id not in r4_loads[Scenarios]
    assert RunTest.objects.count() == runs_before
    assert _r4_state(data) == before


# R5: fixture validation and graph/deadline compatibility regressions. These
# controls exercise real model fields and view wrappers, not mocked querysets.
def test_r5_fixture_columns_have_valid_sources(scope_data, r4_boundaries):
    for resources in (scope_data.one, scope_data.two):
        columns = list(Column.objects.filter(dataset=resources.dataset))
        assert len(columns) == 1
        assert columns[0].source == SourceChoices.OTHERS.value
        columns[0].full_clean()


@pytest.mark.parametrize("existing_graph", [False, True])
def test_r5_scenario_graph_edits_create_and_update_scoped_graph(
    scope_data, existing_graph
):
    data = scope_data
    graph_model = scenario_views.ScenarioGraph
    graph = None
    if existing_graph:
        graph = graph_model.objects.create(
            scenario=data.two.scenario,
            name="Existing local graph",
            organization=data.organization,
            is_active=True,
            graph_config={"graph_data": {"nodes": []}, "source": "retained"},
        )
    graph_data = {"nodes": [], "edges": []}
    response = _r4_call(
        data,
        "scenario-edit",
        payload={"graph": graph_data},
        request_workspace_value=data.w2,
    )
    assert response.status_code == 200
    persisted = graph_model.objects.get(scenario=data.two.scenario, deleted=False)
    assert persisted.organization_id == data.organization.id
    assert persisted.scenario.workspace_id == data.w2.id
    assert persisted.graph_config["graph_data"] == graph_data
    if existing_graph:
        assert persisted.pk == graph.pk
        assert persisted.graph_config["source"] == "retained"
    else:
        assert persisted.graph_config["source"] == "user_provided"
    data.two.scenario.refresh_from_db()
    assert data.two.scenario.deleted is False
    assert not graph_model.objects.filter(scenario=data.one.scenario).exists()


@pytest.mark.parametrize("with_graph", [False, True])
def test_r5_scenario_delete_soft_deletes_local_scenario_and_graph(
    scope_data, with_graph
):
    data = scope_data
    graph = None
    if with_graph:
        graph = scenario_views.ScenarioGraph.objects.create(
            scenario=data.two.scenario,
            name="Local graph to delete",
            organization=data.organization,
            graph_config={"graph_data": {"nodes": []}},
        )
    response = _r4_call(data, "scenario-delete", request_workspace_value=data.w2)
    assert response.status_code == 200
    assert response.data["message"] == "Scenario deleted successfully"
    data.two.scenario.refresh_from_db()
    assert data.two.scenario.deleted is True
    assert data.two.scenario.deleted_at is not None
    data.one.scenario.refresh_from_db()
    assert data.one.scenario.deleted is False
    if with_graph:
        graph.refresh_from_db()
        assert graph.deleted is True
        assert graph.deleted_at is not None


@pytest.mark.parametrize(
    "view_name", ["PromptSimulationListCreateView", "PromptSimulationDetailView"]
)
def test_r5_prompt_read_deadline_is_outermost(view_name):
    wrapper_code = run_test_views._bounded_run_test_list_read(
        lambda *_args: None
    ).__code__
    assert getattr(prompt_views, view_name).get.__code__ is wrapper_code


@pytest.mark.parametrize("route", ["prompt-list", "prompt-detail"])
def test_r5_prompt_read_deadline_encloses_workspace_resolution(
    scope_data, monkeypatch, route
):
    data = scope_data
    _r3_grant_workspace_access(data)
    original_transaction = run_test_views._bounded_run_test_list_transaction
    original_organization = prompt_views.get_request_organization
    original_template_load = PromptTemplate.from_db
    observed_requests = []
    events = []
    active = False

    @contextmanager
    def observe_transaction(deadline):
        nonlocal active
        with original_transaction(deadline):
            assert active is False
            active = True
            events.append("enter")
            try:
                yield
            finally:
                active = False
                events.append("exit")

    def observe_resolution(kind):
        # The actual header/membership/resolver access is the ordering event
        # (dev: the workspace helper spy). Record before asserting so a handler
        # that swallows this AssertionError still fails the outer event order.
        events.append("workspace")
        assert active is True, kind

    def observe_organization(request):
        assert active is True
        observed_requests.append(request)
        events.append("organization")
        return original_organization(request)

    def observe_template(cls, db, field_names, values):
        # The header must have become authorized request scope before the first
        # tenant row is hydrated, and that read must remain inside the deadline.
        # Observe the real load; never supply or repair request.workspace here.
        assert active is True
        assert "workspace" in events
        assert observed_requests
        assert getattr(observed_requests[-1], "workspace", None) == data.w2
        events.append("template")
        return original_template_load(db, field_names, values)

    monkeypatch.setattr(
        run_test_views, "_bounded_run_test_list_transaction", observe_transaction
    )
    monkeypatch.setattr(prompt_views, "get_request_organization", observe_organization)
    monkeypatch.setattr(PromptTemplate, "from_db", classmethod(observe_template))
    # As _r4_call(data, route, header=data.w2.id), with the resolution seams
    # installed between request construction and dispatch.
    view, method, body, kwargs = _r4_route(data, route)
    request = _request(data, method, body)
    request.META[_WORKSPACE_META_KEY] = str(data.w2.id)
    _observe_workspace_resolution(monkeypatch, request, observe_resolution)
    response = view.as_view()(request, **kwargs)
    assert response.status_code == 200
    assert events[0] == "enter"
    assert events[-1] == "exit"
    assert events.count("enter") == events.count("exit") == 1
    assert "workspace" in events
    assert active is False
    _assert_no_private_content(response, data.one)


# R6: a local run/template does not authorize its independent direct version.
@pytest.mark.parametrize("route", ["prompt-list", "prompt-detail", "prompt-patch"])
@pytest.mark.parametrize(
    "parent_scope",
    [
        "foreign-workspace",
        "foreign-organization",
        "null-workspace",
        "null-organization",
        "null-parent",
    ],
)
def test_r6_prompt_reads_reject_out_of_scope_direct_version(
    scope_data, r4_loads, route, parent_scope
):
    data = scope_data
    if parent_scope == "foreign-organization":
        foreign_org = Organization.objects.create(
            name="R6 foreign version organization"
        )
        assert (
            PromptTemplate.no_workspace_objects.filter(pk=data.one.template.pk).update(
                organization=foreign_org, workspace=data.w2
            )
            == 1
        )
    elif parent_scope == "null-workspace":
        assert (
            PromptTemplate.no_workspace_objects.filter(pk=data.one.template.pk).update(
                workspace=None
            )
            == 1
        )
    elif parent_scope == "null-organization":
        assert (
            PromptTemplate.no_workspace_objects.filter(pk=data.one.template.pk).update(
                organization=None, workspace=data.w2
            )
            == 1
        )
    elif parent_scope == "null-parent":
        assert (
            PromptVersion.no_workspace_objects.filter(pk=data.one.version.pk).update(
                original_template=None
            )
            == 1
        )

    # Reuse the mixed-FK state of the existing foreign-version execute control.
    data.two.run.prompt_version = data.one.version
    data.two.run.save(update_fields=["prompt_version"])
    assert RunTest.no_workspace_objects.filter(pk=data.two.run.pk).values(
        "organization_id", "workspace_id", "prompt_template_id", "prompt_version_id"
    ).get() == {
        "organization_id": data.organization.pk,
        "workspace_id": data.w2.pk,
        "prompt_template_id": data.two.template.pk,
        "prompt_version_id": data.one.version.pk,
    }
    assert not PromptVersion.no_workspace_objects.filter(
        pk=data.one.version.pk,
        original_template__organization=data.organization,
        original_template__workspace=data.w2,
    ).exists()
    version_before = (
        PromptVersion.no_workspace_objects.filter(pk=data.one.version.pk).values().get()
    )
    links_before = set(data.two.run.scenarios.values_list("id", flat=True))
    # A nonempty positive list oracle prevents an exclude-everything repair.
    allowed_run = RunTest.objects.create(
        name="R6 allowed sibling run",
        source_type="prompt",
        prompt_template=data.two.template,
        prompt_version=data.two.version,
        organization=data.organization,
        workspace=data.w2,
    )
    allowed_run.scenarios.set([data.two.scenario])
    for rows in r4_loads.values():
        rows.clear()

    response = _r4_call(
        data,
        route,
        payload={"name": data.two.run.name} if route == "prompt-patch" else None,
        request_workspace_value=data.w2,
    )
    _r4_assert_no_foreign_loads(r4_loads, data)
    assert data.one.version.pk not in r4_loads[PromptVersion]
    assert data.two.template.pk in r4_loads[PromptTemplate]
    _assert_no_private_content(response, data.one)
    encoded = json.dumps(response.data, default=str)
    assert str(data.one.version.pk) not in encoded
    assert str(data.two.run.pk) not in encoded
    if route == "prompt-list":
        assert response.status_code == 200
        result = _result(response)
        assert result["count"] == 1
        assert [str(row["id"]) for row in result["results"]] == [str(allowed_run.pk)]
        assert allowed_run.pk in r4_loads[RunTest]
        assert data.two.version.pk in r4_loads[PromptVersion]
        assert data.two.run.pk not in r4_loads[RunTest]
    else:
        assert response.status_code == 404
        if route == "prompt-patch":
            # The local write root is loaded; the subsequent response reload
            # must exclude it before select_related can hydrate its version.
            assert data.two.run.pk in r4_loads[RunTest]
        else:
            assert data.two.run.pk not in r4_loads[RunTest]
        missing = _r4_call(
            data, route, object_id=uuid.uuid4(), request_workspace_value=data.w2
        )
        assert missing.status_code == 404
        assert response.data == missing.data
        _r4_assert_no_foreign_loads(r4_loads, data)

    # Filtering is read-time authorization, not a repair of the mixed FK.
    assert (
        RunTest.no_workspace_objects.filter(pk=data.two.run.pk)
        .values_list("prompt_version_id", flat=True)
        .get()
        == data.one.version.pk
    )
    assert set(data.two.run.scenarios.values_list("id", flat=True)) == links_before
    assert (
        PromptVersion.no_workspace_objects.filter(pk=data.one.version.pk).values().get()
        == version_before
    )


@pytest.mark.parametrize("route", ["prompt-list", "prompt-detail", "prompt-patch"])
@pytest.mark.parametrize("version_kind", ["local", "null"])
def test_r6_prompt_reads_keep_local_and_null_direct_versions(
    scope_data, r4_loads, route, version_kind
):
    data = scope_data
    version_id = data.two.version.pk if version_kind == "local" else None
    assert (
        RunTest.no_workspace_objects.filter(pk=data.two.run.pk).update(
            prompt_version_id=version_id
        )
        == 1
    )
    for rows in r4_loads.values():
        rows.clear()
    response = _r4_call(data, route, request_workspace_value=data.w2)
    assert response.status_code == 200
    _r4_assert_no_foreign_loads(r4_loads, data)
    assert data.two.run.pk in r4_loads[RunTest]
    result = _result(response)
    if route == "prompt-list":
        assert result["count"] == 1
        assert len(result["results"]) == 1
        result = result["results"][0]
    assert str(result["id"]) == str(data.two.run.pk)
    if version_kind == "local":
        assert str(result["prompt_version"]) == str(version_id)
        assert version_id in r4_loads[PromptVersion]
    else:
        assert result["prompt_version"] is None
        assert r4_loads[PromptVersion] == []
    persisted = (
        RunTest.no_workspace_objects.filter(pk=data.two.run.pk)
        .values("prompt_version_id", "name")
        .get()
    )
    assert persisted["prompt_version_id"] == version_id
    if route == "prompt-patch":
        assert persisted["name"] == "Updated local run"
