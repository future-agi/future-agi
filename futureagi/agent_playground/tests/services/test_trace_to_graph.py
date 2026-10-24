"""
Tests for agent_playground.services.trace_to_graph

Tests the conversion of Trace + ObservationSpans into an Agent Playground Graph.
"""

import json
import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from agent_playground.models.choices import GraphVersionStatus, PortDirection
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.port import Port
from agent_playground.services.trace_to_graph import (
    _compute_positions,
    _extract_assistant_response,
    _extract_messages,
    _generate_unique_names,
    _normalize_content,
    _parse_template_variables,
    _templatize_text,
    convert_trace_to_graph,
)
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(db, organization, workspace):
    from model_hub.models.ai_model import AIModel

    return Project.objects.create(
        name="Test Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def trace(db, project):
    return Trace.no_workspace_objects.create(
        project=project,
        name="Test Agent Trace",
        input={"prompt": "Hello"},
        output={"response": "World"},
    )


def _make_llm_span(
    trace,
    project,
    name="LLM Call",
    parent_span_id=None,
    messages=None,
    output=None,
    model="gpt-4",
    model_parameters=None,
    span_attributes=None,
    start_offset_s=0,
):
    """Helper to create an LLM ObservationSpan."""
    now = timezone.now()
    return ObservationSpan.no_workspace_objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        parent_span_id=parent_span_id,
        name=name,
        observation_type="llm",
        start_time=now + timedelta(seconds=start_offset_s),
        end_time=now + timedelta(seconds=start_offset_s + 1),
        input=messages or {"messages": [{"role": "user", "content": "Hello"}]},
        output=output
        or {"choices": [{"message": {"content": "Hi there", "role": "assistant"}}]},
        model=model,
        model_parameters=model_parameters or {"temperature": 0.7, "max_tokens": 1024},
        latency_ms=500,
        status="OK",
        span_attributes=span_attributes or {},
    )


def _make_chain_span(
    trace, project, name="Chain", parent_span_id=None, start_offset_s=0
):
    """Helper to create a chain/agent span (non-LLM)."""
    now = timezone.now()
    return ObservationSpan.no_workspace_objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        parent_span_id=parent_span_id,
        name=name,
        observation_type="chain",
        start_time=now + timedelta(seconds=start_offset_s),
        end_time=now + timedelta(seconds=start_offset_s + 1),
        input={"action": "run"},
        output={"status": "ok"},
        latency_ms=100,
        status="OK",
        span_attributes={},
    )


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestGenerateUniqueNames:
    def test_unique_names(self):
        assert _generate_unique_names(["A", "B", "C"]) == ["A", "B", "C"]

    def test_duplicate_names(self):
        result = _generate_unique_names(["chat", "chat", "chat"])
        assert result == ["chat", "chat_2", "chat_3"]

    def test_name_collision_with_suffix(self):
        """If raw name 'chat_2' exists and 'chat' duplicates, no collision."""
        result = _generate_unique_names(["chat_2", "chat", "chat"])
        assert len(result) == len(set(result)), f"Duplicate names: {result}"
        assert result[0] == "chat_2"

    def test_reserved_chars_sanitized(self):
        result = _generate_unique_names(["node.name[0]"])
        assert "." not in result[0]
        assert "[" not in result[0]
        assert "]" not in result[0]

    def test_empty_name_fallback(self):
        result = _generate_unique_names(["", None])
        assert all(name for name in result)
        assert len(result) == len(set(result))


class TestNormalizeContent:
    def test_string_content(self):
        assert _normalize_content("hello") == [{"type": "text", "text": "hello"}]

    def test_list_content_blocks(self):
        blocks = [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": "http://..."},
        ]
        result = _normalize_content(blocks)
        assert result == blocks

    def test_dict_content(self):
        result = _normalize_content({"key": "value"})
        assert result[0]["type"] == "text"
        assert "key" in result[0]["text"]


class TestExtractMessages:
    def test_openai_format(self):
        span_input = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ]
        }
        messages = _extract_messages(span_input, None)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"][0]["text"] == "You are helpful."
        assert messages[1]["role"] == "user"

    def test_empty_input(self):
        messages = _extract_messages(None, None)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_non_messages_dict(self):
        """Dict without 'messages' key falls back to stringify."""
        messages = _extract_messages({"prompt": "hello"}, None)
        assert len(messages) == 1
        assert "prompt" in messages[0]["content"][0]["text"]

    def test_templatization(self):
        span_input = {
            "messages": [
                {"role": "user", "content": "Summarize: The weather is sunny today"},
            ]
        }
        template_vars = {"text": "The weather is sunny today"}
        messages = _extract_messages(span_input, template_vars)
        assert "{{text}}" in messages[0]["content"][0]["text"]
        assert "The weather is sunny today" not in messages[0]["content"][0]["text"]


class TestExtractAssistantResponse:
    def test_openai_format(self):
        output = {"choices": [{"message": {"content": "Hello!", "role": "assistant"}}]}
        assert _extract_assistant_response(output) == "Hello!"

    def test_direct_content(self):
        output = {"content": "Direct response"}
        assert _extract_assistant_response(output) == "Direct response"

    def test_anthropic_format(self):
        output = {
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ]
        }
        assert _extract_assistant_response(output) == "Part 1\nPart 2"

    def test_string_output(self):
        assert _extract_assistant_response("plain text") == "plain text"

    def test_none_output(self):
        assert _extract_assistant_response(None) is None

    def test_empty_output(self):
        assert _extract_assistant_response({}) is None
        assert _extract_assistant_response("  ") is None


class TestParseTemplateVariables:
    def test_json_string(self):
        attrs = {
            "gen_ai.prompt.template.variables": '{"city": "Paris", "lang": "French"}'
        }
        result = _parse_template_variables(attrs)
        assert result == {"city": "Paris", "lang": "French"}

    def test_dict_value(self):
        attrs = {"llm.prompt_template.variables": {"name": "Alice"}}
        result = _parse_template_variables(attrs)
        assert result == {"name": "Alice"}

    def test_no_variables(self):
        assert _parse_template_variables({}) is None
        assert _parse_template_variables(None) is None

    def test_invalid_json(self):
        attrs = {"gen_ai.prompt.template.variables": "not json"}
        assert _parse_template_variables(attrs) is None


class TestTemplatizeText:
    def test_simple_replacement(self):
        result = _templatize_text("Hello Paris", {"city": "Paris"})
        assert result == "Hello {{city}}"

    def test_longest_first(self):
        """Longer values replaced first to avoid partial matches."""
        result = _templatize_text(
            "The sun is bright", {"weather": "The sun is bright", "adj": "bright"}
        )
        assert result == "{{weather}}"

    def test_no_variables(self):
        assert _templatize_text("hello", {}) == "hello"
        assert _templatize_text("hello", None) == "hello"


class TestComputePositions:
    def test_single_node(self):
        spans = [{"id": "a"}]
        parent_map = {"a": None}
        positions = _compute_positions(spans, parent_map)
        assert positions["a"] == {"x": 0, "y": 0}

    def test_linear_chain(self):
        spans = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        parent_map = {"a": None, "b": "a", "c": "b"}
        positions = _compute_positions(spans, parent_map)
        assert positions["a"]["x"] < positions["b"]["x"] < positions["c"]["x"]

    def test_parallel_nodes(self):
        spans = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        parent_map = {"a": None, "b": "a", "c": "a"}
        positions = _compute_positions(spans, parent_map)
        # b and c same x level, different y
        assert positions["b"]["x"] == positions["c"]["x"]
        assert positions["b"]["y"] != positions["c"]["y"]


# ---------------------------------------------------------------------------
# Integration tests: convert_trace_to_graph
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConvertTraceToGraph:
    def test_single_llm_span(
        self, trace, project, organization, workspace, user, llm_node_template
    ):
        span = _make_llm_span(trace, project, name="Summarize")

        graph, version = convert_trace_to_graph(trace, user, organization, workspace)

        assert isinstance(graph, Graph)
        assert "Iterate" in graph.name
        assert graph.organization == organization
        assert graph.created_by == user

        assert isinstance(version, GraphVersion)
        assert version.status == GraphVersionStatus.DRAFT

        nodes = list(Node.no_workspace_objects.filter(graph_version=version))
        assert len(nodes) == 1
        assert nodes[0].name == "Summarize"
        assert nodes[0].config.get("source_span_id") == str(span.id)
        assert nodes[0].config.get("source_trace_id") == str(trace.id)
        assert "source_parent_span_id" not in nodes[0].config

    def test_multiple_llm_spans_with_hierarchy(
        self, trace, project, organization, workspace, user, llm_node_template
    ):
        """Chain → LLM1 → LLM2 should produce 2 nodes connected."""
        chain = _make_chain_span(trace, project, name="Agent", start_offset_s=0)
        llm1 = _make_llm_span(
            trace, project, name="First LLM", parent_span_id=chain.id, start_offset_s=1
        )
        llm2 = _make_llm_span(
            trace, project, name="Second LLM", parent_span_id=llm1.id, start_offset_s=2
        )

        graph, version = convert_trace_to_graph(trace, user, organization, workspace)

        nodes = list(
            Node.no_workspace_objects.filter(graph_version=version).order_by("position")
        )
        assert len(nodes) == 2

        node_map = {n.name: n for n in nodes}
        assert node_map["First LLM"].config.get("source_span_id") == str(llm1.id)
        assert node_map["First LLM"].config.get("source_trace_id") == str(trace.id)
        assert "source_parent_span_id" not in node_map["First LLM"].config

        assert node_map["Second LLM"].config.get("source_span_id") == str(llm2.id)
        assert node_map["Second LLM"].config.get("source_trace_id") == str(trace.id)
        assert node_map["Second LLM"].config.get("source_parent_span_id") == str(llm1.id)

        connections = list(
            NodeConnection.no_workspace_objects.filter(graph_version=version)
        )
        assert len(connections) == 1
        assert connections[0].source_node.name == "First LLM"
        assert connections[0].target_node.name == "Second LLM"

    def test_source_span_provenance_persisted(
        self, trace, project, organization, workspace, user, llm_node_template
    ):
        """Verify that trace-to-graph conversion faithfully preserves span provenance metadata."""
        span1 = _make_llm_span(trace, project, name="Generator", start_offset_s=0)
        span2 = _make_llm_span(
            trace, project, name="Reviewer", parent_span_id=span1.id, start_offset_s=1
        )

        _, version = convert_trace_to_graph(trace, user, organization, workspace)
        nodes = {n.name: n for n in Node.no_workspace_objects.filter(graph_version=version)}

        assert nodes["Generator"].config["source_span_id"] == str(span1.id)
        assert nodes["Generator"].config["source_trace_id"] == str(trace.id)
        assert "source_parent_span_id" not in nodes["Generator"].config

        assert nodes["Reviewer"].config["source_span_id"] == str(span2.id)
        assert nodes["Reviewer"].config["source_trace_id"] == str(trace.id)
        assert nodes["Reviewer"].config["source_parent_span_id"] == str(span1.id)

    def test_no_llm_spans_raises(self, trace, project, organization, workspace, user):
        _make_chain_span(trace, project, name="Only Chain")

        with pytest.raises(ValidationError, match="no LLM spans"):
            convert_trace_to_graph(trace, user, organization, workspace)

    def test_messages_extracted(
        self, trace, project, organization, workspace, user, llm_node_template
    ):
        """Verify prompt messages are correctly transferred to the node."""
        _make_llm_span(
            trace,
            project,
            name="Chat",
            messages={
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "What is 2+2?"},
                ]
            },
            output={"choices": [{"message": {"content": "4", "role": "assistant"}}]},
        )

        graph, version = convert_trace_to_graph(trace, user, organization, workspace)
        node = Node.no_workspace_objects.filter(graph_version=version).first()

        # Check PromptTemplateNode was created with messages
        ptn = node.prompt_template_node
        snapshot = ptn.prompt_version.prompt_config_snapshot
        messages = snapshot.get("messages", [])

        # Should have system + user + assistant (from output)
        roles = [m["role"] for m in messages]
        assert "system" in roles
        assert "user" in roles
        assert "assistant" in roles

    def test_assistant_response_included(
        self, trace, project, organization, workspace, user, llm_node_template
    ):
        _make_llm_span(
            trace,
            project,
            name="Chat",
            output={
                "choices": [
                    {"message": {"content": "The answer is 42", "role": "assistant"}}
                ]
            },
        )

        graph, version = convert_trace_to_graph(trace, user, organization, workspace)
        node = Node.no_workspace_objects.filter(graph_version=version).first()
        snapshot = node.prompt_template_node.prompt_version.prompt_config_snapshot
        messages = snapshot["messages"]

        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_msgs) >= 1
        assert "42" in assistant_msgs[-1]["content"][0]["text"]

    def test_template_variables_create_input_ports(
        self, trace, project, organization, workspace, user, llm_node_template
    ):
        """When span has template variables, they should become {{var}} placeholders
        and auto-create input ports on the node."""
        _make_llm_span(
            trace,
            project,
            name="Translate",
            messages={
                "messages": [
                    {"role": "user", "content": "Translate to French: Hello world"},
                ]
            },
            span_attributes={
                "gen_ai.prompt.template.variables": json.dumps({"text": "Hello world"})
            },
        )

        graph, version = convert_trace_to_graph(trace, user, organization, workspace)
        node = Node.no_workspace_objects.filter(graph_version=version).first()

        # The message should have {{text}} placeholder
        snapshot = node.prompt_template_node.prompt_version.prompt_config_snapshot
        user_msg = [m for m in snapshot["messages"] if m["role"] == "user"][0]
        assert "{{text}}" in user_msg["content"][0]["text"]
        assert "Hello world" not in user_msg["content"][0]["text"]

        # Should have an input port for "text"
        input_ports = list(
            Port.no_workspace_objects.filter(node=node, direction=PortDirection.INPUT)
        )
        port_names = [p.display_name for p in input_ports]
        assert "text" in port_names

    def test_model_parameters_preserved(
        self, trace, project, organization, workspace, user, llm_node_template
    ):
        _make_llm_span(
            trace,
            project,
            name="Chat",
            model="claude-3-5-sonnet",
            model_parameters={"temperature": 0.3, "max_tokens": 2048},
        )

        graph, version = convert_trace_to_graph(trace, user, organization, workspace)
        node = Node.no_workspace_objects.filter(graph_version=version).first()
        snapshot = node.prompt_template_node.prompt_version.prompt_config_snapshot
        config = snapshot.get("configuration", {})

        assert (
            config.get("model") == "claude-3-5-sonnet"
            or snapshot.get("model") == "claude-3-5-sonnet"
        )

    def test_duplicate_span_names(
        self, trace, project, organization, workspace, user, llm_node_template
    ):
        _make_llm_span(trace, project, name="Chat", start_offset_s=0)
        _make_llm_span(trace, project, name="Chat", start_offset_s=1)
        _make_llm_span(trace, project, name="Chat", start_offset_s=2)

        graph, version = convert_trace_to_graph(trace, user, organization, workspace)
        nodes = list(Node.no_workspace_objects.filter(graph_version=version))
        names = [n.name for n in nodes]

        assert len(names) == 3
        assert len(set(names)) == 3, f"Duplicate node names: {names}"

    def test_hierarchy_flattening(
        self, trace, project, organization, workspace, user, llm_node_template
    ):
        """chain → agent → llm1 → tool → llm2 should flatten to llm1 → llm2."""
        chain = _make_chain_span(trace, project, name="Chain", start_offset_s=0)
        agent = _make_chain_span(
            trace, project, name="Agent", parent_span_id=chain.id, start_offset_s=1
        )
        llm1 = _make_llm_span(
            trace, project, name="LLM1", parent_span_id=agent.id, start_offset_s=2
        )
        tool = ObservationSpan.no_workspace_objects.create(
            id=f"span_{uuid.uuid4().hex[:16]}",
            project=project,
            trace=trace,
            parent_span_id=llm1.id,
            name="Tool",
            observation_type="tool",
            start_time=timezone.now() + timedelta(seconds=3),
            end_time=timezone.now() + timedelta(seconds=4),
            input={},
            output={},
            status="OK",
            span_attributes={},
        )
        _make_llm_span(
            trace, project, name="LLM2", parent_span_id=tool.id, start_offset_s=5
        )

        graph, version = convert_trace_to_graph(trace, user, organization, workspace)

        nodes = list(Node.no_workspace_objects.filter(graph_version=version))
        assert len(nodes) == 2

        connections = list(
            NodeConnection.no_workspace_objects.filter(graph_version=version)
        )
        assert len(connections) == 1
        assert connections[0].source_node.name == "LLM1"
        assert connections[0].target_node.name == "LLM2"

    def test_dataset_created(
        self, trace, project, organization, workspace, user, llm_node_template
    ):
        _make_llm_span(trace, project)

        graph, version = convert_trace_to_graph(trace, user, organization, workspace)

        from agent_playground.models.graph_dataset import GraphDataset

        assert GraphDataset.no_workspace_objects.filter(graph=graph).exists()
