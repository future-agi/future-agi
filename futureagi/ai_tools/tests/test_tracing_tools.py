import uuid

import pytest

from ai_tools.registry import registry
from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_project, make_trace

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
# Eval config + eval task creation
# ---------------------------------------------------------------------------


@pytest.fixture
def span_attributes(monkeypatch):
    """Pin the project's attribute union so mapping behaviour is deterministic."""
    from tracer.utils.sql_queries import SQL_query_handler

    attrs = ["llm.input_messages", "llm.output_messages"]
    monkeypatch.setattr(
        SQL_query_handler,
        "get_span_attributes_for_project",
        staticmethod(lambda project_id: attrs),
    )
    return attrs


class TestCreateCustomEvalConfigMapping:
    def _template(self, tool_context, required):
        from ai_tools.tests.fixtures import make_eval_template

        return make_eval_template(
            tool_context,
            name=f"tmpl-{uuid.uuid4().hex[:6]}",
            config={"required_keys": required},
        )

    def test_unmatchable_required_key_is_not_silently_guessed(
        self, tool_context, project, span_attributes
    ):
        """`grading_rubric` has no counterpart, so nothing may be created."""
        from tracer.models.custom_eval_config import CustomEvalConfig

        template = self._template(tool_context, ["grading_rubric"])

        result = run_tool(
            "create_custom_eval_config",
            {
                "project_id": str(project.id),
                "eval_template_id": str(template.id),
                "name": "rubric-eval",
            },
            tool_context,
        )

        assert result.is_error
        assert "grading_rubric" in result.content
        assert not CustomEvalConfig.objects.filter(
            project=project, name="rubric-eval"
        ).exists()

    def test_supplied_mapping_must_cover_every_required_key(
        self, tool_context, project, span_attributes
    ):
        from tracer.models.custom_eval_config import CustomEvalConfig

        template = self._template(tool_context, ["input", "output"])

        result = run_tool(
            "create_custom_eval_config",
            {
                "project_id": str(project.id),
                "eval_template_id": str(template.id),
                "name": "half-mapped",
                "mapping": {"input": "llm.input_messages"},
            },
            tool_context,
        )

        assert result.is_error
        assert "output" in result.content
        assert not CustomEvalConfig.objects.filter(
            project=project, name="half-mapped"
        ).exists()

    def test_complete_mapping_creates_the_config(
        self, tool_context, project, span_attributes
    ):
        template = self._template(tool_context, ["input", "output"])

        result = run_tool(
            "create_custom_eval_config",
            {
                "project_id": str(project.id),
                "eval_template_id": str(template.id),
                "name": "fully-mapped",
                "mapping": {
                    "input": "llm.input_messages",
                    "output": "llm.output_messages",
                },
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["mapping"] == {
            "input": "llm.input_messages",
            "output": "llm.output_messages",
        }


class TestCreateEvalTaskFilters:
    @pytest.fixture
    def eval_config(self, tool_context, project):
        from ai_tools.tests.fixtures import make_eval_template
        from tracer.models.custom_eval_config import CustomEvalConfig

        return CustomEvalConfig.objects.create(
            eval_template=make_eval_template(
                tool_context, name=f"tmpl-{uuid.uuid4().hex[:6]}"
            ),
            name="cfg",
            project=project,
            model="turing_large",
            mapping={"input": "llm.input_messages"},
            config={},
        )

    def test_key_the_dispatcher_cannot_read_is_rejected(
        self, tool_context, project, eval_config
    ):
        """`span_type` is not a dispatcher key; it must not reach the task."""
        from tracer.models.eval_task import EvalTask

        result = run_tool(
            "create_eval_task",
            {
                "project_id": str(project.id),
                "name": "wrongly-scoped",
                "eval_config_ids": [str(eval_config.id)],
                "filters": {"span_type": "llm"},
            },
            tool_context,
        )

        assert result.is_error
        assert not EvalTask.objects.filter(
            project=project, name="wrongly-scoped"
        ).exists()

    def test_observation_type_filter_reaches_the_task(
        self, tool_context, project, eval_config
    ):
        from tracer.models.eval_task import EvalTask

        result = run_tool(
            "create_eval_task",
            {
                "project_id": str(project.id),
                "name": "llm-only",
                "eval_config_ids": [str(eval_config.id)],
                "filters": {"observation_type": ["llm"]},
            },
            tool_context,
        )

        assert not result.is_error
        task = EvalTask.objects.get(id=result.data["id"])
        assert task.filters["observation_type"] == ["llm"]
        assert task.filters["project_id"] == str(project.id)
