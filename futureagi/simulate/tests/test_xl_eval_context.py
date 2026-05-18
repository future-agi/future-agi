"""Tests for the eval context map and mapping resolver.

Covers:
- resolve_persona_for_call: picks the Persona used for a given CallExecution.
- flatten_persona / flatten_persona_for_resolver: build persona.* namespace.
- _build_simulation_context_map: full context map assembly (xl.py).
- _translate_mapping_value: resolver branches including scenario.<column_name>.
"""

import pytest

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Persona, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test helpers — module-scope factories. Kept in this file (not conftest) so
# the persona/simulator coverage doesn't bleed into unrelated suites.
# ---------------------------------------------------------------------------


def _make_persona(organization, workspace, **overrides):
    """Create a Persona row with sane defaults; overrides win."""
    defaults = dict(
        name="Test Persona",
        organization=organization,
        workspace=workspace,
        persona_type=Persona.PersonaType.WORKSPACE,
    )
    defaults.update(overrides)
    return Persona.objects.create(**defaults)


def _make_agent_definition(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        description="Test agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


def _make_dataset_with_columns(organization, workspace, user, column_specs):
    """Create a Dataset with the named columns and a single Row.

    `column_specs` is a list of (column_name, cell_value) tuples. Returns
    (dataset, row, columns_by_name).
    """
    dataset = Dataset.no_workspace_objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    columns_by_name = {}
    column_order = []
    for col_name, _ in column_specs:
        col = Column.objects.create(
            dataset=dataset,
            name=col_name,
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        columns_by_name[col_name] = col
        column_order.append(str(col.id))
    dataset.column_order = column_order
    dataset.save()

    row = Row.objects.create(dataset=dataset, order=0)
    for col_name, cell_value in column_specs:
        Cell.objects.create(
            dataset=dataset,
            column=columns_by_name[col_name],
            row=row,
            value=cell_value,
        )
    return dataset, row, columns_by_name


def _make_scenario(
    organization, workspace, agent_definition, dataset=None, metadata=None
):
    return Scenarios.objects.create(
        name="Test Scenario",
        description="Test scenario description",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
        metadata=metadata or {},
    )


def _make_run_test(organization, workspace, agent_definition, simulator_agent, scenario):
    rt = RunTest.objects.create(
        name="Test Run",
        description="Test run description",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


def _make_call_execution(test_execution, scenario, row_id=None, row_data=None):
    call_metadata = {}
    if row_id is not None:
        call_metadata["row_id"] = str(row_id)
    if row_data is not None:
        call_metadata["row_data"] = row_data
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        call_metadata=call_metadata,
    )


# ---------------------------------------------------------------------------
# Task 1.1 — _resolve_persona_for_call
# ---------------------------------------------------------------------------


@pytest.fixture
def persona_setup(db, organization, workspace, user):
    """Bootstraps the dependency graph: AgentDefinition → Scenario → RunTest
    → TestExecution. Returns a closure that builds a CallExecution with
    arbitrary scenario_metadata / row_data.
    """
    agent_def = _make_agent_definition(organization, workspace)
    simulator = SimulatorAgent.objects.create(
        name="Sim",
        prompt="You are a simulator",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )

    def _build(scenario_metadata=None, row_data=None):
        scenario = _make_scenario(
            organization,
            workspace,
            agent_def,
            metadata=scenario_metadata or {},
        )
        rt = _make_run_test(organization, workspace, agent_def, simulator, scenario)
        te = TestExecution.objects.create(
            run_test=rt,
            status=TestExecution.ExecutionStatus.COMPLETED,
            simulator_agent=simulator,
            agent_definition=agent_def,
        )
        return _make_call_execution(te, scenario, row_data=row_data)

    return _build


@pytest.mark.django_db
def test_resolve_persona_prefers_row_data_persona_id(
    persona_setup, organization, workspace
):
    from simulate.utils.eval_context import resolve_persona_for_call

    persona_a = _make_persona(organization, workspace, name="A")
    persona_b = _make_persona(organization, workspace, name="B")

    call = persona_setup(
        scenario_metadata={"persona_ids": [str(persona_a.id), str(persona_b.id)]},
        row_data={"persona": str(persona_b.id)},
    )

    resolved = resolve_persona_for_call(call)

    assert resolved is not None
    assert resolved.id == persona_b.id


@pytest.mark.django_db
def test_resolve_persona_falls_back_to_first_in_metadata(
    persona_setup, organization, workspace
):
    from simulate.utils.eval_context import resolve_persona_for_call

    persona_a = _make_persona(organization, workspace, name="A")
    persona_b = _make_persona(organization, workspace, name="B")

    call = persona_setup(
        scenario_metadata={"persona_ids": [str(persona_a.id), str(persona_b.id)]},
        row_data={},
    )

    resolved = resolve_persona_for_call(call)

    assert resolved is not None
    assert resolved.id == persona_a.id


@pytest.mark.django_db
def test_resolve_persona_returns_none_when_no_persona_ids(persona_setup):
    from simulate.utils.eval_context import resolve_persona_for_call

    call = persona_setup(scenario_metadata={}, row_data={})

    assert resolve_persona_for_call(call) is None


# ---------------------------------------------------------------------------
# Task 1.2 — _flatten_persona / _flatten_persona_for_resolver
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_user_facing_flatten_persona_excludes_simulator_fields(
    organization, workspace
):
    """User-facing variant — Persona model only. SimulatorAgent fields are
    deliberately absent so the eval-mapping dropdown never offers them.
    """
    from simulate.utils.eval_context import flatten_persona

    persona = _make_persona(
        organization,
        workspace,
        name="Alice",
        gender=["female"],
        age_group=["28-35"],
        tone="casual",
    )

    flat = flatten_persona(persona)

    assert flat["persona.name"] == "Alice"
    assert flat["persona.gender"] == "female"
    assert flat["persona.age_group"] == "28-35"
    assert flat["persona.tone"] == "casual"
    # SimulatorAgent-only fields must NOT appear in the user-facing variant.
    assert "persona.voice_name" not in flat
    assert "persona.prompt" not in flat
    assert "persona.initial_message" not in flat


@pytest.mark.django_db
def test_flatten_persona_multi_value_list_joins_with_comma(organization, workspace):
    from simulate.utils.eval_context import flatten_persona

    persona = _make_persona(
        organization,
        workspace,
        languages=["English", "Hindi", "Marathi"],
    )

    flat = flatten_persona(persona)

    assert flat["persona.languages"] == "English, Hindi, Marathi"


@pytest.mark.django_db
def test_flatten_persona_metadata_surfaces_dynamically(organization, workspace):
    """Persona.metadata is a free-form dict — each key must surface under
    persona.metadata.<key>, with the same flattening rules.
    """
    from simulate.utils.eval_context import flatten_persona

    persona = _make_persona(
        organization,
        workspace,
        metadata={"region": "EMEA", "vip_tier": "gold", "tags": ["A", "B"]},
    )

    flat = flatten_persona(persona)

    assert flat["persona.metadata.region"] == "EMEA"
    assert flat["persona.metadata.vip_tier"] == "gold"
    assert flat["persona.metadata.tags"] == "A, B"


@pytest.mark.django_db
def test_flatten_persona_none_returns_empty_strings(organization, workspace):
    """When no Persona is bound, every persona.<scalar> key is "" and no
    JSONField/metadata keys are present.
    """
    from simulate.utils.eval_context import flatten_persona

    flat = flatten_persona(None)

    assert flat["persona.name"] == ""
    assert flat["persona.gender"] == ""
    assert flat["persona.tone"] == ""
    assert "persona.metadata.region" not in flat


@pytest.mark.django_db
def test_resolver_flatten_persona_falls_back_to_simulator(
    organization, workspace
):
    """The resolver variant adds a SimulatorAgent fallback for legacy
    persona.<sim_field> mappings. Persona model still wins on conflicts.
    """
    from simulate.utils.eval_context import flatten_persona_for_resolver

    persona = _make_persona(organization, workspace, name="Alice")
    simulator = SimulatorAgent.objects.create(
        name="SimulatorBot",  # Persona.name should still win.
        prompt="You are helpful",
        voice_provider="elevenlabs",
        voice_name="alloy",
        model="gpt-4",
        initial_message="Hello",
        organization=organization,
        workspace=workspace,
    )

    flat = flatten_persona_for_resolver(persona, simulator)

    # Persona wins the name collision.
    assert flat["persona.name"] == "Alice"
    # SimulatorAgent fills in the rest under persona.*.
    assert flat["persona.prompt"] == "You are helpful"
    assert flat["persona.voice_name"] == "alloy"
    assert flat["persona.initial_message"] == "Hello"


@pytest.mark.django_db
def test_resolver_flatten_persona_no_persona_uses_simulator(
    organization, workspace
):
    """If no Persona is bound, the resolver variant returns SimulatorAgent
    fields under persona.* — keeping legacy eval-config mappings alive.
    """
    from simulate.utils.eval_context import flatten_persona_for_resolver

    simulator = SimulatorAgent.objects.create(
        name="Bot",
        prompt="You are helpful",
        voice_provider="elevenlabs",
        voice_name="alloy",
        model="gpt-4",
        initial_message="Hi",
        organization=organization,
        workspace=workspace,
    )

    flat = flatten_persona_for_resolver(None, simulator)

    assert flat["persona.name"] == "Bot"
    assert flat["persona.prompt"] == "You are helpful"
    assert flat["persona.voice_name"] == "alloy"


# ---------------------------------------------------------------------------
# Task 1.2 — integration: _build_simulation_context_map surfaces persona.*
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_context_map_surfaces_persona_and_legacy_simulator_fields(
    persona_setup, organization, workspace
):
    """End-to-end: Persona attributes resolve under persona.*, and legacy
    persona.<sim_field> keys (prompt, voice_name, initial_message) keep
    resolving via the SimulatorAgent fallback in the resolver variant.
    """
    from simulate.temporal.activities.xl import _build_simulation_context_map

    persona = _make_persona(
        organization,
        workspace,
        name="Alice",
        gender=["female"],
        age_group=["28-35"],
    )
    call = persona_setup(
        scenario_metadata={"persona_ids": [str(persona.id)]},
        row_data={"persona": str(persona.id)},
    )

    ctx = _build_simulation_context_map(call, agent_version=None)

    # Persona model fields.
    assert ctx["persona.name"] == "Alice"
    assert ctx["persona.gender"] == "female"
    assert ctx["persona.age_group"] == "28-35"
    # SimulatorAgent fallback fields under the same namespace (legacy compat).
    assert ctx["persona.voice_name"] == "marissa"
    assert ctx["persona.prompt"] == "You are a simulator"


# ---------------------------------------------------------------------------
# Task 1.3 — scenario.info.* + scenario.metadata.<key>
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_context_map_scenario_metadata_moves_to_info_dot_namespace(persona_setup):
    """Scenarios-row attributes shift from scenario.{name,description,type,source}
    to scenario.info.{name,description,type,source} so bare scenario.<X> is
    reserved for dataset-column lookups in Task 1.4.
    """
    from simulate.temporal.activities.xl import _build_simulation_context_map

    call = persona_setup(scenario_metadata={}, row_data={})
    scenario = call.scenario
    scenario.name = "Customer support"
    scenario.description = "Help with refunds"
    scenario.scenario_type = Scenarios.ScenarioTypes.DATASET
    scenario.source = "manual entry"
    scenario.save()

    ctx = _build_simulation_context_map(call, agent_version=None)

    assert ctx["scenario.info.name"] == "Customer support"
    assert ctx["scenario.info.description"] == "Help with refunds"
    assert ctx["scenario.info.type"] == Scenarios.ScenarioTypes.DATASET
    assert ctx["scenario.info.source"] == "manual entry"


@pytest.mark.django_db
def test_context_map_preserves_scenario_info_underscore_aliases(persona_setup):
    """Pre-2026-04-13 saved configs referenced scenario_info_name etc.
    directly. Those underscore aliases keep resolving.
    """
    from simulate.temporal.activities.xl import _build_simulation_context_map

    call = persona_setup(scenario_metadata={}, row_data={})
    scenario = call.scenario
    scenario.name = "Help with refunds"
    scenario.save()

    ctx = _build_simulation_context_map(call, agent_version=None)

    assert ctx["scenario_info_name"] == "Help with refunds"


@pytest.mark.django_db
def test_context_map_scenario_metadata_keys_surface_dynamically(persona_setup):
    """Scenarios.metadata is free-form. Every user-supplied key surfaces under
    scenario.metadata.<key>. Internal references (persona_ids, agent_definition_version_id)
    are filtered out so they don't leak into eval prompts.
    """
    from simulate.temporal.activities.xl import _build_simulation_context_map

    call = persona_setup(
        scenario_metadata={
            "custom_instruction": "Be empathetic.",
            "campaign_id": "Q2-launch",
            "persona_ids": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
            "agent_definition_version_id": "aaaaaaaa-bbbb-cccc-dddd-ffffffffffff",
        },
        row_data={},
    )

    ctx = _build_simulation_context_map(call, agent_version=None)

    assert ctx["scenario.metadata.custom_instruction"] == "Be empathetic."
    assert ctx["scenario.metadata.campaign_id"] == "Q2-launch"
    # Internal-reference keys filtered out.
    assert "scenario.metadata.persona_ids" not in ctx
    assert "scenario.metadata.agent_definition_version_id" not in ctx


@pytest.mark.django_db
def test_context_map_scenario_metadata_jsonfield_flattening(persona_setup):
    """scenario.metadata.<key> values run through flatten_jsonfield_value,
    so lists/dicts get the same normalization as Persona attributes.
    """
    from simulate.temporal.activities.xl import _build_simulation_context_map

    call = persona_setup(
        scenario_metadata={
            "tags": ["urgent", "billing"],
            "single_tag": ["solo"],
        },
        row_data={},
    )

    ctx = _build_simulation_context_map(call, agent_version=None)

    assert ctx["scenario.metadata.tags"] == "urgent, billing"
    assert ctx["scenario.metadata.single_tag"] == "solo"


# ---------------------------------------------------------------------------
# Task 1.4 — resolve_scenario_column_by_name + scenario.<col> resolver branch
# ---------------------------------------------------------------------------


@pytest.fixture
def call_with_dataset_columns(db, organization, workspace, user):
    """Bootstrap a CallExecution backed by a Dataset with named columns.

    Returns a closure that takes a {column_name: cell_value} dict and yields
    (call_execution, columns_by_name).
    """
    agent_def = _make_agent_definition(organization, workspace)
    simulator = SimulatorAgent.objects.create(
        name="Sim",
        prompt="You are a simulator",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )

    def _build(columns):
        column_specs = list(columns.items())
        dataset, row, columns_by_name = _make_dataset_with_columns(
            organization, workspace, user, column_specs
        )
        scenario = _make_scenario(
            organization, workspace, agent_def, dataset=dataset
        )
        rt = _make_run_test(organization, workspace, agent_def, simulator, scenario)
        te = TestExecution.objects.create(
            run_test=rt,
            status=TestExecution.ExecutionStatus.COMPLETED,
            simulator_agent=simulator,
            agent_definition=agent_def,
        )
        call = _make_call_execution(te, scenario, row_id=row.id)
        return call, columns_by_name

    return _build


@pytest.mark.django_db
def test_resolve_scenario_column_by_name_returns_cell_value(call_with_dataset_columns):
    from simulate.utils.eval_context import resolve_scenario_column_by_name

    call, _ = call_with_dataset_columns({"outcome": "resolved successfully"})

    assert resolve_scenario_column_by_name(call, "outcome") == "resolved successfully"


@pytest.mark.django_db
def test_resolve_scenario_column_by_name_returns_empty_for_unknown_column(
    call_with_dataset_columns,
):
    from simulate.utils.eval_context import resolve_scenario_column_by_name

    call, _ = call_with_dataset_columns({"outcome": "..."})

    assert resolve_scenario_column_by_name(call, "does_not_exist") == ""


@pytest.mark.django_db
def test_resolve_scenario_column_by_name_returns_empty_when_no_row_id(persona_setup):
    from simulate.utils.eval_context import resolve_scenario_column_by_name

    call = persona_setup(scenario_metadata={}, row_data={})

    assert resolve_scenario_column_by_name(call, "outcome") == ""


@pytest.mark.django_db
def test_resolve_scenario_column_first_by_created_at_on_name_collision(
    db, organization, workspace, user, call_with_dataset_columns
):
    """Two columns with the same name in one dataset — first by created_at
    wins (rest are logged but ignored).
    """
    from simulate.utils.eval_context import resolve_scenario_column_by_name

    call, columns_by_name = call_with_dataset_columns({"outcome": "first value"})
    first_col = columns_by_name["outcome"]

    # Create a second column with the same name on the same dataset.
    Column.objects.create(
        dataset=first_col.dataset,
        name="outcome",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )

    # first_col was created earlier — should win.
    assert resolve_scenario_column_by_name(call, "outcome") == "first value"


@pytest.mark.django_db(transaction=True)
def test_run_single_evaluation_resolves_scenario_dotted_column_name(
    call_with_dataset_columns, organization, workspace, user
):
    """End-to-end: an eval mapped to scenario.<col> resolves the cell value
    via the new branch in _run_single_evaluation. run_eval_func is mocked so
    we observe what `mappings` the underlying eval received.
    """
    from unittest.mock import patch

    from model_hub.models.evals_metric import EvalTemplate
    from simulate.models.eval_config import SimulateEvalConfig
    from simulate.temporal.activities.xl import _run_single_evaluation

    call, _ = call_with_dataset_columns({"outcome": "resolved successfully"})

    eval_template = EvalTemplate.objects.create(
        name="test template",
        config={"prompt": "evaluate this"},
        organization=organization,
    )
    eval_config = SimulateEvalConfig.objects.create(
        eval_template=eval_template,
        name="test eval",
        run_test=call.test_execution.run_test,
        mapping={"target": "scenario.outcome"},
        config={},
    )

    with patch(
        "model_hub.views.utils.evals.run_eval_func",
        return_value={"output": True, "reason": "", "output_type": "boolean"},
    ) as mock_run:
        _run_single_evaluation(eval_config, call, {"transcript": "..."})

    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["mappings"]["target"] == "resolved successfully"
