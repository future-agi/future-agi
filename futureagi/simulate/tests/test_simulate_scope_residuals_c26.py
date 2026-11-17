"""c2.6 residual boundaries, layered on the frozen r3 database fixtures.

Run with r3 and the r2 ownership-test amendment installed. These tests use real
ORM filtering, validation, writes and serializers; only the r3 external-work
boundaries are replaced. No test execution is claimed by the writer packet.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from accounts.models import Organization
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import Persona, RunTest, Scenarios, SimulateEvalConfig
from simulate.models.scenario_graph import ScenarioGraph
from simulate.models.simulator_agent import SimulatorAgent
from simulate.serializers.response.scenarios import ScenarioResponseSerializer
from simulate.tests.test_simulate_views_workspace_scope_r1 import (
    _prompt_body,
    _r4_call,
    _request,
    _result,
    _scenario_body,
)
from simulate.tests.test_simulate_views_workspace_scope_r1 import (
    r4_boundaries as r4_boundaries,
)
from simulate.tests.test_simulate_views_workspace_scope_r1 import (
    scenario_boundaries as scenario_boundaries,
)
from simulate.tests.test_simulate_views_workspace_scope_r1 import (
    scope_data as scope_data,
)
from simulate.utils.scenario_ownership import SCENARIOS_NOT_IN_RUN_TEST
from simulate.views import prompt_simulation as prompt_views
from simulate.views import scenarios as scenario_views
from tfc.middleware.workspace_context import workspace_context

pytestmark = pytest.mark.django_db


def _observe_loads(monkeypatch, model):
    loaded = []
    original = model.from_db

    def observe(cls, db, field_names, values):
        obj = original(db, field_names, values)
        loaded.append(obj.pk)
        return obj

    monkeypatch.setattr(model, "from_db", classmethod(observe))
    return loaded


@pytest.fixture
def graph_rows(scope_data):
    data = scope_data
    foreign_org = Organization.objects.create(name="C26 graph organization")
    specs = [
        ("older-local", data.two.scenario, data.organization, True),
        ("newest-local", data.two.scenario, data.organization, True),
        ("other-workspace", data.one.scenario, data.organization, True),
        ("other-organization", data.two.scenario, foreign_org, True),
        ("inactive-local", data.two.scenario, data.organization, False),
    ]
    rows = {}
    for index, (name, scenario, org, active) in enumerate(specs):
        graph = ScenarioGraph.objects.create(
            scenario=scenario,
            organization=org,
            name=name,
            is_active=active,
            graph_config={"graph_data": {"marker": name}},
        )
        timestamp = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=index)
        assert (
            ScenarioGraph.objects.filter(pk=graph.pk).update(created_at=timestamp) == 1
        )
        graph.refresh_from_db()
        rows[name] = graph
    assert rows["other-organization"].created_at > rows["newest-local"].created_at
    assert rows["other-organization"].scenario_id == data.two.scenario.pk
    assert rows["other-organization"].organization_id != data.organization.pk
    return rows


def _response_graph(response, route):
    data = _result(response)
    if route == "scenario-list":
        return data["results"][0]["graph"]
    if route == "multi":
        return data["column_configs"][0]["graph"]
    if route == "prompt-list":
        data = data["results"][0]
    return data["scenarios_detail"][0]["graph"]


@pytest.mark.parametrize(
    "route",
    [
        "scenario-list",
        "multi",
        "prompt-list",
        "prompt-detail",
        "prompt-create",
        "prompt-patch",
    ],
)
@pytest.mark.parametrize(
    "local_graphs", [True, False], ids=["local-control", "empty-local"]
)
def test_graph_responses_never_load_foreign_rows(
    scope_data, graph_rows, monkeypatch, route, local_graphs
):
    if not local_graphs:
        ScenarioGraph.objects.filter(
            pk__in=[graph_rows["older-local"].pk, graph_rows["newest-local"].pk]
        ).update(is_active=False)
    loaded = _observe_loads(monkeypatch, ScenarioGraph)
    response = _r4_call(scope_data, route, request_workspace_value=scope_data.w2)
    assert response.status_code == (201 if route == "prompt-create" else 200), (
        response.data
    )
    expected = {"marker": "newest-local"} if local_graphs else {}
    assert _response_graph(response, route) == expected
    for name in ("other-workspace", "other-organization", "inactive-local"):
        assert graph_rows[name].pk not in loaded
        assert name not in json.dumps(response.data, default=str)
    if local_graphs:
        assert graph_rows["newest-local"].pk in loaded


def test_graph_serializer_fallback_and_empty_cache(
    scope_data, graph_rows, monkeypatch, django_assert_num_queries
):
    scenario = Scenarios.no_workspace_objects.get(pk=scope_data.two.scenario.pk)
    loaded = _observe_loads(monkeypatch, ScenarioGraph)
    serializer = ScenarioResponseSerializer()
    assert serializer.get_graph(scenario) == {"marker": "newest-local"}
    assert graph_rows["other-organization"].pk not in loaded
    scenario._active_graphs = []
    with django_assert_num_queries(0):
        assert serializer.get_graph(scenario) == {}


@pytest.mark.parametrize(
    "ambient", [False, True], ids=["unbound-manager", "default-manager"]
)
@pytest.mark.parametrize(
    "root", ["workspace", "organization", "mixed-run", "null-workspace"]
)
@pytest.mark.parametrize(
    "attached", [False, True], ids=["unknown-selection", "attached-selection"]
)
def test_execute_foreign_root_is_missing_before_ownership_validation(
    scope_data, r4_boundaries, root, attached, ambient
):
    data = scope_data
    data.w1.is_default = False
    data.w2.is_default = True
    data.w1.save(update_fields=["is_default"])
    data.w2.save(update_fields=["is_default"])
    if root == "organization":
        other_org = Organization.objects.create(name="C26 execution organization")
        type(data.one.template).no_workspace_objects.filter(
            pk=data.one.template.pk
        ).update(organization=other_org)
        RunTest.no_workspace_objects.filter(pk=data.one.run.pk).update(
            organization=other_org
        )
    elif root == "mixed-run":
        # A local template does not authorize an independently foreign run.
        RunTest.no_workspace_objects.filter(pk=data.one.run.pk).update(
            prompt_template=data.two.template
        )
        data.one.template = data.two.template
    elif root == "null-workspace":
        type(data.one.template).no_workspace_objects.filter(
            pk=data.one.template.pk
        ).update(workspace=None)
        RunTest.no_workspace_objects.filter(pk=data.one.run.pk).update(workspace=None)
    selected = data.one.scenario.pk if attached else uuid.uuid4()
    with workspace_context(
        data.w2 if ambient else None, organization=data.organization, user=data.user
    ):
        response = _r4_call(
            data,
            "prompt-execute",
            resources=data.one,
            payload={"scenario_ids": [str(selected)]},
            request_workspace_value=data.w2,
        )
        missing = _r4_call(
            data,
            "prompt-execute",
            resources=data.one,
            object_id=uuid.uuid4(),
            payload={"scenario_ids": [str(selected)]},
            request_workspace_value=data.w2,
        )
    assert response.status_code == missing.status_code == 404
    assert response.data == missing.data
    r4_boundaries.execute.assert_not_called()


def test_execute_unbound_selection_retains_fixed_400_without_queries(
    scope_data, r4_boundaries, django_assert_num_queries
):
    responses = []
    for run_id in (scope_data.two.run.pk, uuid.uuid4()):
        with django_assert_num_queries(0):
            response = _r4_call(
                scope_data,
                "prompt-execute",
                object_id=run_id,
                payload={"scenario_ids": [str(uuid.uuid4())]},
            )
        assert response.status_code == 400
        assert response.data["details"] == {"scenario_ids": [SCENARIOS_NOT_IN_RUN_TEST]}
        responses.append(response.data)
    assert responses[0] == responses[1]
    r4_boundaries.execute.assert_not_called()


@pytest.mark.parametrize(
    "payload", [{}, {"select_all": True}, {"scenario_ids": ["bad-uuid"]}]
)
def test_execute_no_context_and_pure_validation_order(
    scope_data, r4_boundaries, payload, django_assert_num_queries
):
    with django_assert_num_queries(0):
        response = _r4_call(scope_data, "prompt-execute", payload=payload)
    assert response.status_code == (400 if "scenario_ids" in payload else 403)
    r4_boundaries.execute.assert_not_called()


def test_execute_rejected_header_is_not_legacy_unbound_400(scope_data, r4_boundaries):
    response = _r4_call(
        scope_data,
        "prompt-execute",
        header=uuid.uuid4(),
        payload={"scenario_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 403
    r4_boundaries.execute.assert_not_called()


@pytest.mark.parametrize("exclude", [False, True])
def test_execute_local_selection_and_exclusion_controls(
    scope_data, r4_boundaries, exclude
):
    data = scope_data
    keep = data.two.scenario
    drop = Scenarios.objects.create(
        name="C26 other local scenario",
        organization=data.organization,
        workspace=data.w2,
        status=scenario_views.StatusType.COMPLETED.value,
    )
    data.two.run.scenarios.add(drop)
    payload = {
        "scenario_ids": [str(drop.pk if exclude else keep.pk)],
        "select_all": exclude,
    }
    response = _r4_call(
        data, "prompt-execute", payload=payload, request_workspace_value=data.w2
    )
    assert response.status_code == 200, response.data
    r4_boundaries.execute.assert_called_once()
    assert r4_boundaries.execute.call_args.kwargs["scenario_ids"] == [str(keep.pk)]


@pytest.mark.parametrize("scope", ["workspace", "organization", "deleted"])
def test_create_personas_refused_before_business_validation_or_writes(
    scope_data, scenario_boundaries, monkeypatch, scope
):
    data = scope_data
    org = (
        Organization.objects.create(name="C26 persona organization")
        if scope == "organization"
        else data.organization
    )
    persona = Persona.no_workspace_objects.create(
        name="C26 inaccessible persona",
        persona_type=Persona.PersonaType.WORKSPACE,
        organization=org,
        workspace=data.w1 if scope == "workspace" else data.w2,
    )
    if scope == "deleted":
        Persona.no_workspace_objects.filter(pk=persona.pk).update(deleted=True)
    own = Persona.no_workspace_objects.create(
        name="C26 local persona",
        persona_type=Persona.PersonaType.WORKSPACE,
        organization=data.organization,
        workspace=data.w2,
    )
    original = (
        scenario_views.ScenarioCreateRequestSerializer.validate_prompt_template_id
    )
    validated = []

    def observe(self, value):
        validated.append(value)
        return original(self, value)

    monkeypatch.setattr(
        scenario_views.ScenarioCreateRequestSerializer,
        "validate_prompt_template_id",
        observe,
    )
    loaded = _observe_loads(monkeypatch, Persona)
    before = (Scenarios.objects.count(), SimulatorAgent.objects.count())
    responses = []
    for persona_id in (persona.pk, uuid.uuid4()):
        payload = _scenario_body(data.two)
        payload["personas"] = [str(own.pk), str(persona_id)]
        response = scenario_views.CreateScenarioView.as_view()(
            _request(data, "post", payload, workspace=data.w2)
        )
        assert response.status_code == 400, response.data
        assert response.data["details"] == {
            "personas": ["Persona not found or not accessible."]
        }
        assert str(persona_id) not in json.dumps(response.data, default=str)
        responses.append(response.data)
    assert responses[0] == responses[1]
    assert validated == []
    assert persona.pk not in loaded
    assert before == (Scenarios.objects.count(), SimulatorAgent.objects.count())
    scenario_boundaries.assert_not_called()


@pytest.mark.parametrize("system", [False, True], ids=["local", "system"])
def test_create_persona_controls_are_persisted_and_forwarded(
    scope_data, scenario_boundaries, system
):
    data = scope_data
    persona = Persona.no_workspace_objects.create(
        name="C26 allowed persona",
        persona_type=Persona.PersonaType.SYSTEM
        if system
        else Persona.PersonaType.WORKSPACE,
        organization=None if system else data.organization,
        workspace=None if system else data.w2,
    )
    payload = _scenario_body(data.two)
    payload["personas"] = [str(persona.pk), str(persona.pk)]
    response = scenario_views.CreateScenarioView.as_view()(
        _request(data, "post", payload, workspace=data.w2)
    )
    assert response.status_code == 202, response.data
    scenario_boundaries.assert_called_once()
    forwarded = scenario_boundaries.call_args.kwargs
    scenario = Scenarios.no_workspace_objects.get(pk=forwarded["scenario_id"])
    assert scenario.metadata["persona_ids"] == payload["personas"]
    assert [str(pk) for pk in forwarded["validated_data"]["personas"]] == payload[
        "personas"
    ]
    assert scenario.workspace_id == data.w2.pk
    assert scenario.organization_id == data.organization.pk


@pytest.mark.parametrize(
    "scope", ["workspace", "organization", "null-workspace", "deleted"]
)
def test_prompt_create_ignores_inaccessible_eval_templates_like_missing(
    scope_data, monkeypatch, scope
):
    data = scope_data
    # Default-workspace visibility also admits organization-owned NULL rows.
    # Keep one default (the schema requires it); prompt creates stay exact.
    data.w1.is_default = False
    data.w2.is_default = True
    data.w1.save(update_fields=["is_default"])
    data.w2.save(update_fields=["is_default"])
    org = (
        Organization.objects.create(name="C26 eval organization")
        if scope == "organization"
        else data.organization
    )
    ws = (
        None
        if scope == "null-workspace"
        else (data.w1 if scope == "workspace" else data.w2)
    )
    template = EvalTemplate.no_workspace_objects.create(
        name="c26_inaccessible_eval",
        organization=org,
        workspace=ws,
        owner=OwnerChoices.USER.value,
        config={"required_keys": []},
    )
    # BaseModel.save may assign a default workspace; persist the intended shape.
    if scope == "null-workspace":
        EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(workspace=None)
    if scope == "deleted":
        EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(deleted=True)
    loaded = _observe_loads(monkeypatch, EvalTemplate)
    projections = []
    for template_id in (template.pk, uuid.uuid4()):
        payload = _prompt_body(data.two)
        payload["evaluations_config"] = [{"template_id": str(template_id)}]
        response = prompt_views.PromptSimulationListCreateView.as_view()(
            _request(data, "post", payload, workspace=data.w2),
            prompt_template_id=data.two.template.pk,
        )
        assert response.status_code == 201, response.data
        run_id = _result(response)["id"]
        assert not SimulateEvalConfig.no_workspace_objects.filter(
            run_test_id=run_id
        ).exists()
        assert str(template_id) not in json.dumps(response.data, default=str)
        projections.append(_result(response)["evals_detail"])
    assert projections == [[], []]
    assert template.pk not in loaded


@pytest.mark.parametrize("system", [False, True], ids=["local", "system"])
def test_prompt_create_eval_template_controls(scope_data, system):
    data = scope_data
    template = EvalTemplate.no_workspace_objects.create(
        name="c26_allowed_eval",
        organization=None if system else data.organization,
        workspace=None if system else data.w2,
        owner=OwnerChoices.USER.value,
        config={"required_keys": []},
    )
    if system:
        EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(
            organization=None, workspace=None
        )
    payload = _prompt_body(data.two)
    payload["evaluations_config"] = [{"template_id": str(template.pk)}]
    response = prompt_views.PromptSimulationListCreateView.as_view()(
        _request(data, "post", payload, workspace=data.w2),
        prompt_template_id=data.two.template.pk,
    )
    assert response.status_code == 201, response.data
    result = _result(response)
    config = SimulateEvalConfig.no_workspace_objects.get(run_test_id=result["id"])
    assert config.eval_template_id == template.pk
    assert [str(item["template_id"]) for item in result["evals_detail"]] == [
        str(template.pk)
    ]
