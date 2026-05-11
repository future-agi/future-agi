import pytest

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_eval_template, make_project, make_trace


class TestEvalTemplateWorkflow:
    """Create → inspect → update → delete."""

    def test_full_lifecycle(self, tool_context):
        # 1. Create template
        create = run_tool(
            "create_eval_template",
            {
                "name": "wf-eval-template",
                "description": "Workflow test eval",
                "criteria": "Evaluate {{response}} for clarity",
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

        # 5. Delete template
        delete = run_tool(
            "delete_eval_template", {"eval_template_id": tmpl_id}, tool_context
        )
        assert not delete.is_error

        # 6. Verify gone
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

        # 4. Search for it
        search = run_tool("search_traces", {"name": "wf-trace"}, tool_context)
        assert not search.is_error
        assert search.data["total"] >= 1

        # 5. Get trace details
        get = run_tool("get_trace", {"trace_id": str(trace.id)}, tool_context)
        assert not get.is_error
        assert "wf-trace" in get.content

        # 6. Tag the trace
        tag = run_tool(
            "add_trace_tags",
            {"trace_id": str(trace.id), "tags": ["workflow", "automated"]},
            tool_context,
        )
        assert not tag.is_error
        assert "workflow" in tag.data["added"]
        assert "automated" in tag.data["added"]

        # 7. Search by tag
        tag_search = run_tool("search_traces", {"tags": ["workflow"]}, tool_context)
        assert not tag_search.is_error
        assert tag_search.data["total"] >= 1

        # 8. Delete project
        delete = run_tool("delete_project", {"project_id": proj_id}, tool_context)
        assert not delete.is_error
