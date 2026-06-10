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


# NOTE (Phase 2A Packet D): the hand-written ``search_traces`` tool was
# retired — trace listing now goes through the ``list_traces`` /
# ``list_session_traces`` DRF bridges (ClickHouse-backed list path, covered
# by the live sweeps, not by these PG-fixture unit tests).


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


class TestUpdateTraceTagsTool:
    """Phase 2A Packet D: ``update_trace_tags`` (DRF bridge onto
    TraceView.update_tags, PATCH /traces/<pk>/tags/) replaced the retired
    hand-written add/remove/list_trace_tags trio. The tool REPLACES the
    full tag list (read current tags via ``get_trace``, edit, resubmit)."""

    def test_replace_tags(self, tool_context, trace):
        result = run_tool(
            "update_trace_tags",
            {"trace_id": str(trace.id), "tags": ["new-tag", "another-tag"]},
            tool_context,
        )

        assert not result.is_error
        trace.refresh_from_db()
        assert sorted(trace.tags) == ["another-tag", "new-tag"]

    def test_clear_tags_with_empty_list(self, tool_context, trace):
        result = run_tool(
            "update_trace_tags",
            {"trace_id": str(trace.id), "tags": []},
            tool_context,
        )

        assert not result.is_error
        trace.refresh_from_db()
        assert trace.tags == []

    def test_update_tags_nonexistent_trace(self, tool_context):
        result = run_tool(
            "update_trace_tags",
            {"trace_id": str(uuid.uuid4()), "tags": ["tag1"]},
            tool_context,
        )

        assert result.is_error


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
