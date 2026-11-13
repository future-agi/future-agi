"""Regression coverage for graph activities retained for v1 workflow histories."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.requires_ee


@pytest.fixture
def legacy_graph(monkeypatch):
    from ee.agenthub.scenario_graph import enhanced_scenarios_agent, graph_generator
    from ee.usage.services import emitter, metering
    from simulate.models import AgentDefinition, Scenarios
    from simulate.models.scenario_graph import ScenarioGraph
    from simulate.views import scenarios
    from tfc.temporal.simulate import activities

    scenario = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        name="Legacy graph scenario",
        description="Test scenario",
        organization=SimpleNamespace(id="00000000-0000-0000-0000-000000000002"),
        workspace=SimpleNamespace(id="workspace-id"),
        prompt_template=None,
        prompt_version=None,
        metadata={},
        save=MagicMock(),
    )
    agent = SimpleNamespace(id="agent-id", description="Agent instructions")
    dataset = SimpleNamespace(id="dataset-id")
    graph = SimpleNamespace(id="graph-id")
    lookup = MagicMock(return_value=agent)
    generator = MagicMock()
    generator.return_value.generate_graph.return_value = graph
    generator.return_value.get_branches.return_value = []
    create_graph = MagicMock(return_value=graph)
    enhanced = MagicMock()
    enhanced.return_value.run.return_value = (scenario, dataset)
    enhanced.return_value.llm.cost = {"total_cost": 0}
    enhanced.return_value.llm.token_usage = {}
    llm = MagicMock()
    llm.return_value._get_completion_content.return_value = "Ask about a booking"

    monkeypatch.setattr(Scenarios.objects, "get", MagicMock(return_value=scenario))
    monkeypatch.setattr(AgentDefinition.no_workspace_objects, "get", lookup)
    monkeypatch.setattr(ScenarioGraph.objects, "create", create_graph)
    monkeypatch.setattr(graph_generator, "ConversationGraphGenerator", generator)
    monkeypatch.setattr(enhanced_scenarios_agent, "EnhancedScenariosAgent", enhanced)
    monkeypatch.setattr(
        metering, "check_usage", MagicMock(return_value=SimpleNamespace(allowed=True))
    )
    monkeypatch.setattr(emitter, "emit", MagicMock())
    monkeypatch.setattr(
        scenarios, "convert_personas_to_property_list", MagicMock(return_value=[])
    )
    monkeypatch.setattr("agentic_eval.core.llm.llm.LLM", llm)

    return SimpleNamespace(
        run=activities._create_graph_scenario_sync,
        scenario=scenario,
        agent=agent,
        dataset=dataset,
        lookup=lookup,
        generator=generator,
        create_graph=create_graph,
        enhanced=enhanced,
        llm=llm,
    )


@pytest.mark.parametrize("transcript_option", ["omitted", "empty-list", "empty-dict"])
def test_legacy_supplied_graph_without_transcripts(
    legacy_graph, monkeypatch, transcript_option
):
    """Supplied graphs still propose intents when legacy payloads lack transcripts."""
    from model_hub.models.choices import StatusType
    from tfc.temporal.simulate import activities

    context = legacy_graph
    graph_data = {
        "nodes": [{"id": "start", "isStart": True}, {"id": "end"}],
        "edges": [{"from": "start", "to": "end"}],
    }
    payload = {
        "agent_definition_id": context.agent.id,
        "graph": graph_data,
        "generate_graph": False,
    }
    if transcript_option != "omitted":
        payload["transcripts"] = [] if transcript_option == "empty-list" else {}
    branches = [{"start_node": "start", "end_node": "end"}]
    context.generator.return_value.get_branches.return_value = branches
    propose_intents = MagicMock(
        return_value=[{"use_case_id": "booking", "user_intent": "Ask about a booking"}]
    )
    monkeypatch.setattr(
        activities, "propose_use_cases_covering_workflows", propose_intents
    )

    result = context.run(context.scenario.id, payload)

    assert result == {
        "scenario_id": context.scenario.id,
        "dataset_id": context.dataset.id,
        "status": "COMPLETED",
    }
    assert context.scenario.status == StatusType.COMPLETED.value
    assert context.scenario.dataset is context.dataset
    context.create_graph.assert_called_once_with(
        scenario=context.scenario,
        name=f"{context.scenario.name} - Graph",
        description=f"Graph for {context.scenario.name}",
        organization=context.scenario.organization,
        graph_config={"graph_data": graph_data, "source": "user_provided"},
    )
    context.generator.assert_called_once_with(
        scenario=context.scenario, agent_definition=context.agent
    )
    context.generator.return_value.generate_graph.assert_not_called()
    context.generator.return_value.get_branches.assert_called_once_with(
        graph_id="graph-id"
    )
    propose_intents.assert_called_once_with(
        agent_definition=context.agent.description,
        branches=branches,
        llm=context.llm.return_value,
        skip_coverage_patch=True,
    )
    run_kwargs = context.enhanced.return_value.run.call_args.kwargs
    assert run_kwargs["graph_id"] == "graph-id"
    assert run_kwargs["intent_dict"] == {"booking": "Ask about a booking"}


def test_legacy_supplied_graph_with_transcripts(legacy_graph, monkeypatch):
    """Transcripts remain the intent source without initializing a graph generator."""
    from tfc.temporal.simulate import activities

    context = legacy_graph
    propose_intents = MagicMock()
    monkeypatch.setattr(
        activities, "propose_use_cases_covering_workflows", propose_intents
    )
    payload = {
        "agent_definition_id": context.agent.id,
        "graph": {"nodes": [{"id": "start", "isStart": True}], "edges": []},
        "generate_graph": False,
        "transcripts": {"call-1": {"transcript": "I have a booking question"}},
    }

    result = context.run(context.scenario.id, payload)

    assert result["status"] == "COMPLETED", result
    context.generator.assert_not_called()
    propose_intents.assert_not_called()
    context.llm.return_value._get_completion_content.assert_called_once()
    run_kwargs = context.enhanced.return_value.run.call_args.kwargs
    assert run_kwargs["graph_id"] == "graph-id"
    assert run_kwargs["intent_dict"] == {"call-1": "Ask about a booking"}


@pytest.mark.parametrize("graph_source", ["generated", "provided", "none"])
def test_legacy_agent_graph_inputs(legacy_graph, graph_source):
    """Old activity payloads bind the agent ID and both graph options."""
    context = legacy_graph
    payload = {"agent_definition_id": context.agent.id}
    if graph_source == "generated":
        payload["generate_graph"] = True
    elif graph_source == "provided":
        payload["graph"] = {"nodes": [], "edges": []}
        payload["transcripts"] = {"call-1": {"transcript": "I have a booking question"}}

    result = context.run(context.scenario.id, payload)

    assert result == {
        "scenario_id": context.scenario.id,
        "dataset_id": context.dataset.id,
        "status": "COMPLETED",
    }
    assert context.scenario.dataset is context.dataset
    assert context.scenario.source == "Graph-based scenario"
    context.lookup.assert_called_once_with(id=context.agent.id)
    context.enhanced.assert_called_once_with(
        no_of_rows=20, custom_columns=[], agent_definition=context.agent
    )
    if graph_source == "generated":
        context.generator.assert_called_once_with(
            scenario=context.scenario, agent_definition=context.agent
        )
        context.generator.return_value.generate_graph.assert_called_once_with(
            save_to_db=True
        )
        context.create_graph.assert_not_called()
    elif graph_source == "provided":
        assert context.create_graph.call_args.kwargs["graph_config"] == {
            "graph_data": payload["graph"],
            "source": "user_provided",
        }
        context.generator.assert_not_called()
    else:
        context.generator.assert_not_called()
        context.create_graph.assert_not_called()
    assert context.enhanced.return_value.run.call_args.kwargs["graph_id"] == (
        None if graph_source == "none" else "graph-id"
    )


def test_legacy_prompt_source_without_graph(legacy_graph):
    """Prompt sources use their prompt snapshot, without an agent DB lookup."""
    context = legacy_graph
    context.scenario.prompt_template = SimpleNamespace(
        id="prompt-id", name="Booking prompt", description="Fallback description"
    )
    context.scenario.prompt_version = SimpleNamespace(
        prompt_config_snapshot={
            "messages": [{"role": "system", "content": "Help with bookings"}]
        }
    )

    result = context.run(context.scenario.id, {"source_type": "prompt"})

    assert result["status"] == "COMPLETED", result
    assert result["dataset_id"] == context.dataset.id
    context.lookup.assert_not_called()
    context.generator.assert_not_called()
    context.create_graph.assert_not_called()
    adapter = context.enhanced.call_args.kwargs["agent_definition"]
    assert adapter.id == "prompt-id"
    assert adapter.description == "[system]: Help with bookings"
    assert context.enhanced.return_value.run.call_args.kwargs["graph_id"] is None


def test_legacy_usage_denial_still_stops_generation(legacy_graph, monkeypatch):
    from ee.usage.services import metering

    monkeypatch.setattr(
        metering,
        "check_usage",
        MagicMock(
            return_value=SimpleNamespace(allowed=False, reason="Usage limit exceeded")
        ),
    )

    result = legacy_graph.run(legacy_graph.scenario.id, {})

    assert result["status"] == "FAILED"
    assert "Usage limit exceeded" in result["error"]
    legacy_graph.lookup.assert_not_called()
    legacy_graph.generator.assert_not_called()
    legacy_graph.enhanced.assert_not_called()
