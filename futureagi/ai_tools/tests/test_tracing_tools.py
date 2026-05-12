import json
import uuid

import pytest

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import (
    make_annotation_label,
    make_eval_template,
    make_project,
    make_trace,
)

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

    def test_search_filter_by_project_name(self, tool_context, trace, project):
        result = run_tool(
            "search_traces", {"project_id": project.name}, tool_context
        )

        assert not result.is_error
        assert result.data["total"] == 1
        assert result.data["traces"][0]["project_id"] == str(project.id)

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

    def test_search_is_scoped_to_current_workspace(self, tool_context, trace, user):
        from accounts.models.workspace import Workspace

        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=tool_context.organization,
            is_active=True,
            created_by=user,
        )
        other_project = make_project(
            tool_context, name="Other Project", workspace=other_workspace
        )
        make_trace(tool_context, project=other_project, name="other-trace")

        result = run_tool("search_traces", {}, tool_context)

        assert result.data["total"] == 1
        assert result.data["traces"][0]["name"] == "test-trace"

    def test_search_includes_org_level_legacy_projects(self, tool_context):
        legacy_project = make_project(
            tool_context, name="Legacy Project", workspace=None
        )
        make_trace(tool_context, project=legacy_project, name="legacy-trace")

        result = run_tool("search_traces", {}, tool_context)

        assert result.data["total"] == 1
        assert result.data["traces"][0]["name"] == "legacy-trace"


class TestGetTraceAnalyticsTool:
    def test_accepts_14_day_range(self, tool_context, trace_with_spans):
        result = run_tool("get_trace_analytics", {"time_range": "14d"}, tool_context)

        assert not result.is_error
        assert result.data["time_range"] == "14d"


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

        assert not result.is_error
        assert result.data["requires_trace_id"] is True

    def test_get_shows_input_output(self, tool_context, trace):
        result = run_tool("get_trace", {"trace_id": str(trace.id)}, tool_context)

        assert "Input" in result.content
        assert "Output" in result.content
        assert "Hello" in result.content

    def test_get_invalid_uuid(self, tool_context):
        result = run_tool("get_trace", {"trace_id": "not-a-uuid"}, tool_context)

        assert not result.is_error
        assert result.data["requires_trace_id"] is True

    def test_get_trace_rejects_other_workspace_trace(self, tool_context, user):
        from accounts.models.workspace import Workspace

        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=tool_context.organization,
            is_active=True,
            created_by=user,
        )
        other_project = make_project(
            tool_context, name="Other Project", workspace=other_workspace
        )
        other_trace = make_trace(
            tool_context, project=other_project, name="other-trace"
        )

        result = run_tool("get_trace", {"trace_id": str(other_trace.id)}, tool_context)

        assert not result.is_error
        assert result.data["requires_trace_id"] is True
        assert str(other_trace.id) not in {t["id"] for t in result.data["traces"]}

    def test_get_trace_allows_org_level_legacy_project(self, tool_context):
        legacy_project = make_project(
            tool_context, name="Legacy Project", workspace=None
        )
        legacy_trace = make_trace(
            tool_context, project=legacy_project, name="legacy-trace"
        )

        result = run_tool("get_trace", {"trace_id": str(legacy_trace.id)}, tool_context)

        assert not result.is_error
        assert result.data["id"] == str(legacy_trace.id)


class TestListProjectsTool:
    def test_list_empty(self, tool_context):
        result = run_tool("list_projects", {}, tool_context)

        assert not result.is_error

    def test_list_with_project(self, tool_context, project):
        result = run_tool("list_projects", {}, tool_context)

        assert not result.is_error
        assert "Test Project" in result.content

    def test_list_is_scoped_to_current_workspace(self, tool_context, project, user):
        from accounts.models.workspace import Workspace

        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=tool_context.organization,
            is_active=True,
            created_by=user,
        )
        other_project = make_project(
            tool_context, name="Other Project", workspace=other_workspace
        )
        make_trace(tool_context, project=project)
        make_trace(tool_context, project=other_project, name="other-trace")

        result = run_tool("list_projects", {}, tool_context)

        assert result.data["total"] == 1
        assert result.data["projects"][0]["name"] == "Test Project"

    def test_list_includes_org_level_legacy_projects(self, tool_context):
        make_project(tool_context, name="Legacy Project", workspace=None)

        result = run_tool("list_projects", {}, tool_context)

        assert result.data["total"] == 1
        assert result.data["projects"][0]["name"] == "Legacy Project"


class TestGetProjectEvalAttributesTool:
    def test_uses_clickhouse_fast_path(self, tool_context, project, monkeypatch):
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        monkeypatch.setattr(
            AnalyticsQueryService, "should_use_clickhouse", lambda self, query_type: True
        )
        monkeypatch.setattr(
            AnalyticsQueryService,
            "get_span_attribute_keys_ch",
            lambda self, project_id: [{"key": "llm.input", "type": "text"}],
        )

        result = run_tool(
            "get_project_eval_attributes",
            {"project_id": str(project.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["attributes"] == ["llm.input"]
        assert result.data["backend"] == "clickhouse"


class TestGetSpanTool:
    def test_get_nonexistent_returns_candidates(self, tool_context):
        result = run_tool("get_span", {"span_id": "missing-span"}, tool_context)

        assert not result.is_error
        assert result.data["requires_span_id"] is True


class TestCreateDashboardTool:
    def test_create_dashboard_shell(self, tool_context):
        result = run_tool(
            "create_dashboard",
            {"name": "Falcon Ops", "description": "Created by Falcon"},
            tool_context,
        )

        assert not result.is_error
        assert "Dashboard Created" in result.content
        assert result.data["name"] == "Falcon Ops"
        assert result.data["widget_count"] == 0

    def test_create_dashboard_with_widget(self, tool_context):
        result = run_tool(
            "create_dashboard",
            {
                "name": "Trace Health",
                "widgets": [
                    {
                        "name": "Trace count",
                        "chart_type": "metric",
                        "width": 4,
                        "height": 3,
                    }
                ],
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["widget_count"] == 1
        assert result.data["widgets"][0]["name"] == "Trace count"


class TestRenderWidgetTool:
    def test_summary_stats_flat_payload_maps_to_key_value(self, tool_context):
        result = run_tool(
            "render_widget",
            {
                "type": "summary_stats",
                "title": "API Key Health",
                "data": json.dumps(
                    {
                        "stats": [
                            {"label": "Total Keys", "value": 12},
                            {"label": "Active Keys", "value": 10},
                        ]
                    }
                ),
            },
            tool_context,
        )

        assert not result.is_error
        payload = json.loads(result.content)
        assert payload["widget"]["type"] == "key_value"
        assert payload["widget"]["config"]["items"] == [
            {"key": "Total Keys", "value": "12"},
            {"key": "Active Keys", "value": "10"},
        ]


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

        assert not result.is_error
        assert result.data["requires_trace_id"] is True

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
    def test_delete_existing_defaults_to_preview(self, tool_context, project):
        result = run_tool(
            "delete_project",
            {"project_id": str(project.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["dry_run"] is True
        assert result.data["requires_confirm_delete"] is True

    def test_delete_nonexistent(self, tool_context):
        result = run_tool(
            "delete_project",
            {"project_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_project_id"] is True


class TestCreateEvalTaskTool:
    def test_missing_project_returns_candidates(self, tool_context, project):
        result = run_tool("create_eval_task", {}, tool_context)

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_project_id"] is True
        assert any(item["id"] == str(project.id) for item in result.data["projects"])

    def test_ambiguous_eval_configs_returns_candidates(self, tool_context, project):
        from tracer.models.custom_eval_config import CustomEvalConfig

        template_a = make_eval_template(tool_context, name="Trace Quality A")
        template_b = make_eval_template(tool_context, name="Trace Quality B")
        config_a = CustomEvalConfig.objects.create(
            project=project,
            eval_template=template_a,
            name="quality-a",
        )
        config_b = CustomEvalConfig.objects.create(
            project=project,
            eval_template=template_b,
            name="quality-b",
        )

        result = run_tool(
            "create_eval_task",
            {"project_id": str(project.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_eval_config_ids"] is True
        returned_ids = {item["id"] for item in result.data["eval_configs"]}
        assert {str(config_a.id), str(config_b.id)} <= returned_ids

    def test_create_with_project_and_config_names(self, tool_context, project):
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.eval_task import EvalTask

        template = make_eval_template(tool_context, name="Trace Correctness")
        config = CustomEvalConfig.objects.create(
            project=project,
            eval_template=template,
            name="correctness-config",
        )

        result = run_tool(
            "create_eval_task",
            {
                "project_id": project.name,
                "eval_config_ids": [config.name],
                "run_type": "historical",
            },
            tool_context,
        )

        assert not result.is_error
        assert "Eval Task Created" in result.content
        assert result.data["project_id"] == str(project.id)
        assert result.data["eval_config_ids"] == [str(config.id)]
        task = EvalTask.objects.get(id=result.data["id"])
        assert task.name.startswith(f"{project.name} eval task")
        assert task.evals.filter(id=config.id).exists()


class TestDeleteEvalTasksTool:
    def test_missing_ids_returns_candidates(self, tool_context, project):
        from tracer.models.eval_task import EvalTask, EvalTaskStatus

        task = EvalTask.objects.create(
            project=project,
            name="candidate-task",
            status=EvalTaskStatus.PAUSED,
        )

        result = run_tool("delete_eval_tasks", {}, tool_context)

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_eval_task_ids"] is True
        assert any(item["id"] == str(task.id) for item in result.data["tasks"])

    def test_delete_defaults_to_preview(self, tool_context, project):
        from tracer.models.eval_task import EvalTask, EvalTaskStatus

        task = EvalTask.objects.create(
            project=project,
            name="preview-task",
            status=EvalTaskStatus.PAUSED,
        )

        result = run_tool(
            "delete_eval_tasks",
            {"eval_task_ids": [str(task.id)]},
            tool_context,
        )

        assert not result.is_error
        assert result.data["dry_run"] is True
        assert result.data["requires_confirm_delete"] is True


class TestCreateScoreTool:
    def test_missing_trace_returns_candidates(self, tool_context, trace):
        result = run_tool("create_score", {}, tool_context)

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_trace_id"] is True
        assert any(item["id"] == str(trace.id) for item in result.data["traces"])

    def test_missing_value_returns_needs_input(self, tool_context, trace):
        label = make_annotation_label(
            tool_context,
            name="score-label",
            label_type="numeric",
            project=trace.project,
        )

        result = run_tool(
            "create_score",
            {"trace_id": str(trace.id), "annotation_label_id": str(label.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_score_value"] is True
        assert result.data["label_type"] == "numeric"
