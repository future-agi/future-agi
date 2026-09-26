import uuid

import pytest

from ai_tools.registry import registry
from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_eval_template, make_project, make_trace

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tool_context):
    return make_project(tool_context)


@pytest.fixture
def trace(tool_context, project):
    return make_trace(tool_context, project=project)


@pytest.fixture
def trace_with_spans(trace):
    from tracer.models.observation_span import ObservationSpan
    from tracer.tests._ch_seed import seed_ch_spans

    spans = []
    spans.append(
        ObservationSpan.objects.create(
            id=f"span-{uuid.uuid4().hex[:8]}",
            project=trace.project,
            trace=trace,
            name="llm-call",
            observation_type="llm",
            model="gpt-4o",
            latency_ms=500,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost=0.005,
            status="OK",
        )
    )
    spans.append(
        ObservationSpan.objects.create(
            id=f"span-{uuid.uuid4().hex[:8]}",
            project=trace.project,
            trace=trace,
            name="tool-call",
            observation_type="tool",
            latency_ms=50,
            status="OK",
        )
    )
    seed_ch_spans(spans)
    return trace, spans


# ===================================================================
# READ TOOLS
# ===================================================================


class TestSearchTracesTool:
    def test_search_empty(self, tool_context):
        result = run_tool("search_traces", {}, tool_context)

        assert not result.is_error
        assert "Traces (0)" in result.content
        assert result.data["total"] == 0

    def test_search_with_data(self, tool_context, trace):
        result = run_tool("search_traces", {}, tool_context)

        assert not result.is_error
        assert "Traces (1)" in result.content
        assert "test-trace" in result.content
        assert "Test Project" in result.content
        assert result.data["total"] == 1

    def test_search_filter_by_name(self, tool_context, trace):
        result = run_tool("search_traces", {"name": "test"}, tool_context)
        assert result.data["total"] == 1

        result = run_tool("search_traces", {"name": "nonexistent"}, tool_context)
        assert result.data["total"] == 0

    def test_search_filter_by_project(self, tool_context, trace, project):
        result = run_tool(
            "search_traces", {"project_id": str(project.id)}, tool_context
        )
        assert result.data["total"] == 1

        result = run_tool(
            "search_traces", {"project_id": str(uuid.uuid4())}, tool_context
        )
        assert result.data["total"] == 0

    def test_search_filter_by_error(self, tool_context, trace):
        result = run_tool("search_traces", {"has_error": False}, tool_context)
        assert result.data["total"] == 1

        result = run_tool("search_traces", {"has_error": True}, tool_context)
        assert result.data["total"] == 0

    def test_search_filter_by_tags(self, tool_context, trace):
        result = run_tool("search_traces", {"tags": ["test"]}, tool_context)
        assert result.data["total"] == 1

        result = run_tool("search_traces", {"tags": ["nonexistent"]}, tool_context)
        assert result.data["total"] == 0

    def test_search_pagination(self, tool_context, trace):
        result = run_tool("search_traces", {"limit": 1, "offset": 0}, tool_context)

        assert not result.is_error
        assert len(result.data["traces"]) <= 1


class TestGetTraceTool:
    def test_get_existing(self, tool_context, trace):
        result = run_tool("get_trace", {"trace_id": str(trace.id)}, tool_context)

        assert not result.is_error
        assert "test-trace" in result.content
        assert "Test Project" in result.content
        assert result.data["id"] == str(trace.id)

    def test_get_with_spans(self, tool_context, trace_with_spans):
        trace, spans = trace_with_spans
        result = run_tool("get_trace", {"trace_id": str(trace.id)}, tool_context)

        assert not result.is_error
        assert "Spans (2)" in result.content
        assert "llm-call" in result.content
        assert "tool-call" in result.content
        assert "gpt-4o" in result.content
        assert len(result.data["spans"]) == 2

    def test_get_without_spans(self, tool_context, trace):
        result = run_tool(
            "get_trace",
            {"trace_id": str(trace.id), "include_spans": False},
            tool_context,
        )

        assert not result.is_error
        assert "test-trace" in result.content
        assert result.data["spans"] == []

    def test_get_nonexistent(self, tool_context):
        result = run_tool("get_trace", {"trace_id": str(uuid.uuid4())}, tool_context)

        assert result.is_error
        assert "Not Found" in result.content

    def test_get_shows_input_output(self, tool_context, trace):
        result = run_tool("get_trace", {"trace_id": str(trace.id)}, tool_context)

        assert "Input" in result.content
        assert "Output" in result.content
        assert "Hello" in result.content

    def test_get_invalid_uuid(self, tool_context):
        result = run_tool("get_trace", {"trace_id": "not-a-uuid"}, tool_context)

        assert result.is_error


class TestListProjectsTool:
    def test_list_empty(self, tool_context):
        result = run_tool("list_projects", {}, tool_context)

        assert not result.is_error

    def test_list_with_project(self, tool_context, project):
        result = run_tool("list_projects", {}, tool_context)

        assert not result.is_error
        assert "Test Project" in result.content


# ===================================================================
# WRITE TOOLS
# ===================================================================


class TestCreateProjectTool:
    def test_create_basic(self, tool_context):
        result = run_tool(
            "create_project",
            {"name": "New Project", "model_type": "GenerativeLLM"},
            tool_context,
        )

        assert not result.is_error
        assert "Project Created" in result.content
        assert result.data["name"] == "New Project"
        assert result.data["trace_type"] == "observe"

    def test_create_experiment_type(self, tool_context):
        result = run_tool(
            "create_project",
            {
                "name": "Exp Project",
                "trace_type": "experiment",
                "model_type": "GenerativeLLM",
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["trace_type"] == "experiment"

    def test_create_invalid_type(self, tool_context):
        result = run_tool(
            "create_project",
            {
                "name": "Bad Type",
                "trace_type": "invalid",
                "model_type": "GenerativeLLM",
            },
            tool_context,
        )

        assert result.is_error

    def test_create_duplicate_name(self, tool_context):
        run_tool(
            "create_project",
            {"name": "Dup Proj", "model_type": "GenerativeLLM"},
            tool_context,
        )
        result = run_tool(
            "create_project",
            {"name": "Dup Proj", "model_type": "GenerativeLLM"},
            tool_context,
        )

        assert result.is_error
        assert "already exists" in result.content

    def test_create_with_description(self, tool_context):
        result = run_tool(
            "create_project",
            {
                "name": "Described Proj",
                "description": "A test project",
                "model_type": "GenerativeLLM",
            },
            tool_context,
        )

        assert not result.is_error


class TestAddTraceTagsTool:
    def test_add_tags(self, tool_context, trace):
        result = run_tool(
            "add_trace_tags",
            {"trace_id": str(trace.id), "tags": ["new-tag", "another-tag"]},
            tool_context,
        )

        assert not result.is_error
        assert "new-tag" in result.data["added"]
        assert "another-tag" in result.data["added"]

    def test_add_duplicate_tags(self, tool_context, trace):
        """Tags already on the trace should be reported as already present."""
        result = run_tool(
            "add_trace_tags",
            {"trace_id": str(trace.id), "tags": ["test"]},  # 'test' is already on trace
            tool_context,
        )

        assert not result.is_error
        assert "test" in result.data["already_present"]
        assert len(result.data["added"]) == 0

    def test_add_tags_nonexistent_trace(self, tool_context):
        result = run_tool(
            "add_trace_tags",
            {"trace_id": str(uuid.uuid4()), "tags": ["tag1"]},
            tool_context,
        )

        assert result.is_error

    def test_add_mixed_new_and_existing(self, tool_context, trace):
        result = run_tool(
            "add_trace_tags",
            {"trace_id": str(trace.id), "tags": ["test", "brand-new"]},
            tool_context,
        )

        assert not result.is_error
        assert "brand-new" in result.data["added"]
        assert "test" in result.data["already_present"]


class TestDeleteProjectTool:
    def test_delete_existing(self, tool_context, project):
        result = run_tool(
            "delete_project",
            {"project_id": str(project.id)},
            tool_context,
        )

        assert not result.is_error

    def test_delete_nonexistent(self, tool_context):
        result = run_tool(
            "delete_project",
            {"project_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error


# ---------------------------------------------------------------------------
# create_eval_task: the mapping has to resolve on the spans the task will read
# ---------------------------------------------------------------------------


class TestEvalTaskMappingGate:
    """The project attribute list is a union over every span, so a path that
    only ever appears on one span type passes config creation and then reads
    empty on the type the task is scoped to."""

    @pytest.fixture
    def project(self, tool_context):
        return make_project(tool_context, name="gate-project")

    @pytest.fixture
    def spans(self, tool_context, project):
        from tracer.models.observation_span import ObservationSpan

        trace = make_trace(tool_context, project=project)
        for i in range(3):
            ObservationSpan.objects.create(
                id=f"span-agent-{i}-{uuid.uuid4().hex[:6]}",
                project=project,
                trace=trace,
                name="support.conversation",
                observation_type="agent",
                input={"value": "where is my booking"},
                output={"value": "it departs at 09:40"},
                span_attributes={},
            )
            ObservationSpan.objects.create(
                id=f"span-llm-{i}-{uuid.uuid4().hex[:6]}",
                project=project,
                trace=trace,
                name="ChatCompletion",
                observation_type="llm",
                span_attributes={"raw.input": "[...]", "raw.output": "{...}"},
            )
        return project

    def _config(self, tool_context, project, name, mapping):
        from tracer.models.custom_eval_config import CustomEvalConfig

        template = make_eval_template(
            tool_context,
            name=f"tmpl-{name}",
            config={"required_keys": list(mapping), "custom_eval": True},
        )
        return CustomEvalConfig.objects.create(
            eval_template=template,
            name=name,
            project=project,
            model="turing_large",
            mapping=mapping,
        )

    def test_a_mapping_that_resolves_is_allowed(self, tool_context, spans):
        cfg = self._config(
            tool_context, spans, "body-config", {"input": "input", "output": "output"}
        )
        result = run_tool(
            "create_eval_task",
            {
                "project_id": str(spans.id),
                "name": "good-task",
                "eval_config_ids": [str(cfg.id)],
            },
            tool_context,
        )
        assert not result.is_error, result.content

    def test_a_mapping_that_resolves_on_nothing_is_refused(self, tool_context, spans):
        """`raw.input` is a real key on this project, on the llm spans. It is
        not on the agent spans, and this task is scoped to those."""
        cfg = self._config(
            tool_context,
            spans,
            "raw-config",
            {"input": "raw.input", "output": "raw.output"},
        )
        result = run_tool(
            "create_eval_task",
            {
                "project_id": str(spans.id),
                "name": "blocked-task",
                "eval_config_ids": [str(cfg.id)],
                "filters": {"observation_type": ["agent"]},
            },
            tool_context,
        )
        assert result.is_error
        assert "raw-config" in result.content
        assert "raw.input" in result.content

    def test_the_same_mapping_is_fine_when_scoped_to_the_spans_that_carry_it(
        self, tool_context, spans
    ):
        cfg = self._config(
            tool_context,
            spans,
            "raw-config-llm",
            {"input": "raw.input", "output": "raw.output"},
        )
        result = run_tool(
            "create_eval_task",
            {
                "project_id": str(spans.id),
                "name": "llm-task",
                "eval_config_ids": [str(cfg.id)],
                "filters": {"observation_type": ["llm"]},
            },
            tool_context,
        )
        assert not result.is_error, result.content

    def test_no_spans_to_sample_means_no_opinion(self, tool_context):
        empty = make_project(tool_context, name="empty-project")
        cfg = self._config(
            tool_context, empty, "empty-config", {"input": "anything.at.all"}
        )
        result = run_tool(
            "create_eval_task",
            {
                "project_id": str(empty.id),
                "name": "empty-task",
                "eval_config_ids": [str(cfg.id)],
            },
            tool_context,
        )
        assert not result.is_error, result.content
