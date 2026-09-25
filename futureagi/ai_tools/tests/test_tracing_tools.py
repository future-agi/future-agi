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


class TestGetEvalTaskLogsTool:
    ENGINE_FAILURE = (
        "Error during evaluation: No input received for any of "
        "'input', 'output'. Please check your inputs."
    )
    INLINE_FAILURE = "Response contradicts the provided context."

    def _errored_entry(self, tool_context, project, **fields):
        import uuid as _uuid

        from tracer.models.observation_span import (
            EvalEntryStatus,
            ObservationSpan,
        )
        from tracer.tests.eval_task_factories import make_config, make_row
        from tracer.tests.eval_task_factories import make_task, make_template

        trace = make_trace(tool_context, project=project)
        span = ObservationSpan.objects.create(
            id=f"span-{_uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name="llm-call",
            observation_type="llm",
        )
        template = make_template(
            organization=tool_context.organization,
            workspace=tool_context.workspace,
        )
        config = make_config(project=project, template=template, name="Eval")
        task = make_task(project=project)
        make_row(
            span=span,
            cfg=config,
            task=task,
            status=EvalEntryStatus.ERRORED,
            error=True,
            **fields,
        )
        return task

    def test_engine_failure_text_reaches_the_caller(self, tool_context, project):
        task = self._errored_entry(
            tool_context,
            project,
            eval_explanation="",
            error_message=self.ENGINE_FAILURE,
        )

        result = run_tool(
            "get_eval_task_logs", {"eval_task_id": str(task.id)}, tool_context
        )

        assert not result.is_error
        assert result.data["errors_count"] == 1
        assert result.data["errors_message"] == [self.ENGINE_FAILURE]
        assert "### Error Messages" in result.content
        assert self.ENGINE_FAILURE in result.content

    def test_inline_eval_reason_is_not_displaced_by_a_null_error_message(
        self, tool_context, project
    ):
        task = self._errored_entry(
            tool_context,
            project,
            eval_explanation=self.INLINE_FAILURE,
            error_message=None,
        )

        result = run_tool(
            "get_eval_task_logs", {"eval_task_id": str(task.id)}, tool_context
        )

        assert not result.is_error
        assert result.data["errors_message"] == [self.INLINE_FAILURE]
        assert self.INLINE_FAILURE in result.content
