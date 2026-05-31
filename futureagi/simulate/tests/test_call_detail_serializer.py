"""Integration tests for CallExecutionDetailSerializer's persona_profile and
scenario_metadata fields.

These fields drive the eval-mapping dropdown on the frontend — the dropdown
auto-derives options from the serialized call-detail payload, so the shape
asserted here is the contract with SimulationTestMode.jsx /
CreateSimulationPreviewMode.jsx.
"""

import pytest

from simulate.serializers.test_execution import CallExecutionDetailSerializer
from simulate.tests.factories import (
    make_agent_definition,
    make_call_execution,
    make_persona,
    make_run_test,
    make_scenario,
    make_simulator_agent,
    make_test_execution,
)

pytestmark = pytest.mark.integration


def _make_call(
    organization,
    workspace,
    *,
    persona=None,
    scenario_metadata=None,
    simulator=None,
):
    """Bootstrap a CallExecution with optional persona / scenario.metadata wiring.

    Composes primitives from `simulate.tests.factories` — kept here (not in
    the shared factories module) because the persona-resolution wiring is
    specific to this suite's contract assertions.
    """
    agent_def = make_agent_definition(organization, workspace)
    simulator = simulator or make_simulator_agent(organization, workspace)

    metadata = scenario_metadata or {}
    if persona is not None and "persona_ids" not in metadata:
        metadata["persona_ids"] = [str(persona.id)]

    scenario = make_scenario(
        organization, workspace, agent_def, metadata=metadata
    )
    rt = make_run_test(
        organization,
        workspace,
        agent_def,
        simulator_agent=simulator,
        scenarios=[scenario],
    )
    te = make_test_execution(
        rt, agent_definition=agent_def, simulator_agent=simulator
    )

    row_data = {"persona": str(persona.id)} if persona is not None else None
    return make_call_execution(te, scenario, row_data=row_data)


@pytest.mark.django_db
def test_persona_profile_exposes_persona_model_attributes(
    organization, workspace
):
    persona = make_persona(
        organization,
        workspace,
        name="Alice",
        gender=["female"],
        age_group=["28-35"],
        occupation=["Software Engineer"],
        tone="casual",
        verbosity="brief",
    )
    call = _make_call(organization, workspace, persona=persona)

    data = CallExecutionDetailSerializer(call).data

    profile = data["persona_profile"]
    assert profile["name"] == "Alice"
    assert profile["gender"] == "female"
    assert profile["age_group"] == "28-35"
    assert profile["occupation"] == "Software Engineer"
    assert profile["tone"] == "casual"
    assert profile["verbosity"] == "brief"


@pytest.mark.django_db
def test_persona_profile_excludes_simulator_fields(organization, workspace):
    """SimulatorAgent fields must NOT show up in the user-facing persona profile,
    even when a SimulatorAgent is bound. Legacy persona.<sim_field> mappings
    still resolve at runtime via the eval-context resolver — but the
    dropdown source must never list them.
    """
    persona = make_persona(organization, workspace, name="Alice")
    simulator = make_simulator_agent(
        organization, workspace, voice_name="alloy", prompt="You are helpful"
    )
    call = _make_call(
        organization, workspace, persona=persona, simulator=simulator
    )

    data = CallExecutionDetailSerializer(call).data

    profile = data["persona_profile"]
    assert profile["name"] == "Alice"
    assert "voice_name" not in profile
    assert "prompt" not in profile
    assert "initial_message" not in profile


@pytest.mark.django_db
def test_persona_profile_empty_when_no_persona_bound(organization, workspace):
    """If the scenario has no persona_ids and no row_data.persona, the
    profile is an empty dict (no SimulatorAgent stand-in)."""
    simulator = make_simulator_agent(organization, workspace, voice_name="alloy")
    call = _make_call(organization, workspace, simulator=simulator)

    data = CallExecutionDetailSerializer(call).data

    assert data["persona_profile"] == {}


@pytest.mark.django_db
def test_persona_profile_nests_metadata_under_metadata_key(
    organization, workspace
):
    """Persona.metadata sub-keys should land under persona_profile["metadata"]
    so the frontend flattener can emit flat.persona.metadata.<key>.
    """
    persona = make_persona(
        organization,
        workspace,
        metadata={"region": "EMEA", "vip_tier": "gold"},
    )
    call = _make_call(organization, workspace, persona=persona)

    data = CallExecutionDetailSerializer(call).data

    assert data["persona_profile"]["metadata"]["region"] == "EMEA"
    assert data["persona_profile"]["metadata"]["vip_tier"] == "gold"


@pytest.mark.django_db
def test_persona_profile_multi_value_list_joined(organization, workspace):
    persona = make_persona(
        organization,
        workspace,
        languages=["English", "Hindi", "Marathi"],
    )
    call = _make_call(organization, workspace, persona=persona)

    data = CallExecutionDetailSerializer(call).data

    assert data["persona_profile"]["languages"] == "English, Hindi, Marathi"


@pytest.mark.django_db
def test_scenario_metadata_exposes_user_keys(organization, workspace):
    call = _make_call(
        organization,
        workspace,
        scenario_metadata={
            "custom_instruction": "Be empathetic.",
            "campaign_id": "Q2-launch",
        },
    )

    data = CallExecutionDetailSerializer(call).data

    metadata = data["scenario_metadata"]
    assert metadata["custom_instruction"] == "Be empathetic."
    assert metadata["campaign_id"] == "Q2-launch"


@pytest.mark.django_db
def test_scenario_metadata_filters_internal_reference_keys(
    organization, workspace
):
    """persona_ids and agent_definition_version_id must NOT leak via the
    serializer — same denylist as the eval context map.
    """
    call = _make_call(
        organization,
        workspace,
        scenario_metadata={
            "custom_instruction": "..",
            "persona_ids": ["aaa-bbb"],
            "agent_definition_version_id": "ccc-ddd",
        },
    )

    data = CallExecutionDetailSerializer(call).data

    assert "persona_ids" not in data["scenario_metadata"]
    assert "agent_definition_version_id" not in data["scenario_metadata"]
    assert data["scenario_metadata"]["custom_instruction"] == ".."


@pytest.mark.django_db
def test_scenario_metadata_jsonfield_values_flattened(organization, workspace):
    call = _make_call(
        organization,
        workspace,
        scenario_metadata={
            "tags": ["urgent", "billing"],
            "single_tag": ["solo"],
        },
    )

    data = CallExecutionDetailSerializer(call).data

    assert data["scenario_metadata"]["tags"] == "urgent, billing"
    assert data["scenario_metadata"]["single_tag"] == "solo"
