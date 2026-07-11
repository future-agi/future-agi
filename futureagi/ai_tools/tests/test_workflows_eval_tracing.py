import pytest

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_eval_template, make_project, make_trace


class TestEvalTemplateWorkflow:
    """Create → inspect → update → group → delete."""

    def test_full_lifecycle(self, tool_context):
        # 1. Create template
        create = run_tool(
            "create_eval_template",
            {
                "name": "wf-eval-template",
                "description": "Workflow test eval",
                "criteria": "Evaluate {{response}}",
                "required_keys": ["response"],
            },
            tool_context,
        )
        assert not create.is_error
        tmpl_id = create.data["id"]

        # 2. Verify in list
        listing = run_tool("list_eval_templates", {}, tool_context)
        assert not listing.is_error
        assert any(t["id"] == tmpl_id for t in listing.data["templates"])

        # 3. Get details
        get = run_tool("get_eval_template", {"eval_template_id": tmpl_id}, tool_context)
        assert not get.is_error
        assert get.data["name"] == "wf-eval-template"

        # 4. Update name
        update = run_tool(
            "update_eval_template",
            {"eval_template_id": tmpl_id, "name": "wf-eval-renamed"},
            tool_context,
        )
        assert not update.is_error
        assert update.data["name"] == "wf-eval-renamed"

        # 5. Create group with this template
        group = run_tool(
            "create_eval_group",
            {"name": "Workflow Group", "eval_template_ids": [tmpl_id]},
            tool_context,
        )
        assert not group.is_error
        assert group.data["template_count"] == 1

        # 6. Delete template
        delete = run_tool(
            "delete_eval_template", {"eval_template_id": tmpl_id}, tool_context
        )
        assert not delete.is_error

        # 7. Verify gone
        get2 = run_tool(
            "get_eval_template", {"eval_template_id": tmpl_id}, tool_context
        )
        assert get2.is_error


class TestTracingWorkflow:
    """Create project → create trace (via fixture) → search → tag → inspect."""

    def test_project_and_trace_lifecycle(self, tool_context):
        # 1. Create project
        proj = run_tool(
            "create_project",
            {"name": "Workflow Project", "model_type": "GenerativeLLM"},
            tool_context,
        )
        assert not proj.is_error
        proj_id = proj.data["project_id"]

        # 2. Verify in list
        listing = run_tool("list_projects", {}, tool_context)
        assert not listing.is_error
        assert any(p["id"] == proj_id for p in listing.data["projects"])

        # 3. Create a trace via fixture for this project
        from tracer.models.project import Project

        project_obj = Project.objects.get(id=proj_id)
        trace = make_trace(tool_context, project=project_obj, name="wf-trace")

        # 4. Get trace details
        # (search_traces was retired in Phase 2A Packet D — trace listing
        # goes through the CH-backed list_traces/list_session_traces bridges,
        # covered by the live sweeps.)
        get = run_tool("get_trace", {"trace_id": str(trace.id)}, tool_context)
        assert not get.is_error
        assert "wf-trace" in get.content

        # 5. Tag the trace (update_trace_tags replaces the full tag list —
        # Packet D bridge replacing the retired add/remove/list_trace_tags)
        tag = run_tool(
            "update_trace_tags",
            {"trace_id": str(trace.id), "tags": ["workflow", "automated"]},
            tool_context,
        )
        assert not tag.is_error
        trace.refresh_from_db()
        assert "workflow" in trace.tags
        assert "automated" in trace.tags

        # 6. Delete project
        delete = run_tool("delete_project", {"project_id": proj_id}, tool_context)
        assert not delete.is_error


class TestEvalGroupMultiTemplate:
    """Create multiple templates → group them → verify count."""

    def test_group_multiple_templates(self, tool_context):
        # Create 3 templates
        ids = []
        for i in range(3):
            result = run_tool(
                "create_eval_template",
                {
                    "name": f"group-tmpl-{i}",
                    "description": f"Template {i}",
                    "criteria": "Evaluate {{response}}",
                    "required_keys": ["response"],
                },
                tool_context,
            )
            assert not result.is_error
            ids.append(result.data["id"])

        # Group them
        group = run_tool(
            "create_eval_group",
            {"name": "Multi Template Group", "eval_template_ids": ids},
            tool_context,
        )
        assert not group.is_error
        assert group.data["template_count"] == 3
