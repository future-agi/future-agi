"""Exercise generated SQL tools through the real Django API executor."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from mcp_server.api_executor import DjangoAPIExecutor, MCPRequestContext
from mcp_server.generated_registry import GeneratedToolRegistry
from tracer.sql_workbench.tests.test_permissions import create_job
from tracer.sql_workbench.tests.test_permissions import tenants as _tenant_fixture


@pytest.fixture
def tenants(monkeypatch):
    return _tenant_fixture.__wrapped__(monkeypatch)


pytestmark = pytest.mark.django_db


def context_for(tenant):
    actor, project, user = tenant
    return MCPRequestContext(
        user,
        Organization.objects.get(id=actor.organization_id),
        Workspace.no_workspace_objects.get(id=actor.workspace_id),
    )


@pytest.mark.parametrize(
    "state", ["queued", "running", "succeeded", "failed", "cancelled"]
)
def test_get_sql_result_preserves_the_raw_lifecycle_envelope(tenants, state):
    actor, project, _ = tenants[0]
    payload = (
        {
            "columns": [{"name": "n", "type": "UInt64"}],
            "rows": [[3]],
            "row_count": 1,
            "elapsed_ms": 2,
            "truncated": False,
        }
        if state == "succeeded"
        else {}
    )
    job = create_job(
        actor,
        project,
        state=state,
        result=payload,
        error="Query reached a bound." if state == "failed" else "",
    )
    tool = GeneratedToolRegistry.from_manifest().get("get_sql_result")
    result = DjangoAPIExecutor().execute_sync(
        tool,
        {"id": str(job.id), "project_id": str(project.id)},
        context_for(tenants[0]),
    )
    assert tool.response["unwrap"] is None
    assert result["id"] == str(job.id)
    assert result["project_id"] == str(project.id)
    assert result["state"] == state
    assert result["error"] == job.error
    assert all(result["result"][key] == value for key, value in payload.items())
    if payload:
        assert not result["result"]["has_more"] and not result["result"]["previewed"]
    else:
        assert result["result"] == {}


def test_run_cancel_and_catalog_tools_keep_job_and_scope_metadata(tenants):
    actor, project, _ = tenants[0]
    registry = GeneratedToolRegistry.from_manifest()
    executor = DjangoAPIExecutor()
    context = context_for(tenants[0])
    now = timezone.now()
    with patch("tracer.sql_workbench.service.connection_config"):
        submitted = executor.execute_sync(
            registry.get("run_sql"),
            {
                "project_id": str(project.id),
                "sql": "SELECT count() AS n FROM spans",
                "start": (now - timedelta(hours=1)).isoformat(),
                "end": now.isoformat(),
            },
            context,
        )
    assert submitted["state"] == "queued" and submitted["id"]
    assert submitted["result"] == {}
    cancelled = executor.execute_sync(
        registry.get("cancel_sql"),
        {"id": submitted["id"], "project_id": str(project.id)},
        context,
    )
    assert cancelled["id"] == submitted["id"] and cancelled["state"] == "cancelled"
    catalog = executor.execute_sync(
        registry.get("get_sql_catalog"), {"project_id": str(project.id)}, context
    )
    assert catalog["version"] == 1 and catalog["datasets"]
    assert all(
        registry.get(name).response["unwrap"] is None
        for name in ["run_sql", "get_sql_result", "cancel_sql", "get_sql_catalog"]
    )


def test_viewer_can_run_readonly_sql_through_mcp(tenants):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership

    actor, project, _ = tenants[0]
    OrganizationMembership.no_workspace_objects.filter(user_id=actor.user_id).update(
        level=1
    )
    WorkspaceMembership.no_workspace_objects.filter(user_id=actor.user_id).update(
        level=1
    )
    now = timezone.now()
    with patch("tracer.sql_workbench.service.connection_config"):
        result = DjangoAPIExecutor().execute_sync(
            GeneratedToolRegistry.from_manifest().get("run_sql"),
            {
                "project_id": str(project.id),
                "sql": "SELECT count() AS n FROM spans",
                "start": (now - timedelta(hours=1)).isoformat(),
                "end": now.isoformat(),
            },
            context_for(tenants[0]),
        )
    assert result["id"] and result["state"] == "queued"


@pytest.mark.parametrize("tool_name", ["get_sql_result", "cancel_sql"])
@pytest.mark.parametrize(
    "bad_id", ["a" * 36, "------------------------------------", "abc", "not-a-uuid"]
)
def test_mcp_tools_answer_not_found_for_a_malformed_execution_id(
    tenants, tool_name, bad_id
):
    """A malformed id from an agent is a 404, never a server error."""
    from mcp_server.api_executor import APIExecutionError

    actor, project, _ = tenants[0]
    with pytest.raises(APIExecutionError) as failure:
        DjangoAPIExecutor().execute_sync(
            GeneratedToolRegistry.from_manifest().get(tool_name),
            {"id": bad_id, "project_id": str(project.id)},
            context_for(tenants[0]),
        )
    assert failure.value.status_code == 404, failure.value


def test_sql_result_pages_fit_mcp_bound_without_gaps(tenants):
    import json

    from mcp_server.api_executor import APIExecutionError
    from mcp_server.response_limits import MAX_RESPONSE_BYTES

    actor, project, _ = tenants[0]
    rows = [[str(i)] + ["abcdef"] * 39 for i in range(300)]
    payload = {
        "columns": [{"name": f"c{i}", "type": "String"} for i in range(40)],
        "rows": rows,
        "row_count": len(rows),
    }
    job = create_job(actor, project, state="succeeded", result=payload)
    tool = GeneratedToolRegistry.from_manifest().get("get_sql_result")
    executor = DjangoAPIExecutor()
    with pytest.raises(APIExecutionError) as oversized:
        executor.execute_sync(
            tool,
            {"id": str(job.id), "project_id": str(project.id), "limit": 101},
            context_for(tenants[0]),
        )
    assert oversized.value.status_code == 400
    received, offset = [], 0
    while True:
        page = executor.execute_sync(
            tool,
            {"id": str(job.id), "project_id": str(project.id), "offset": offset},
            context_for(tenants[0]),
        )
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= MAX_RESPONSE_BYTES
        assert page["id"] == str(job.id) and page["state"] == "succeeded"
        assert not page["result"]["previewed"]
        received += page["result"]["rows"]
        if not page["result"]["has_more"]:
            break
        offset = page["result"]["next_offset"]
    assert received == rows
    with pytest.raises(APIExecutionError) as denied:
        executor.execute_sync(
            tool,
            {"id": str(job.id), "project_id": str(project.id), "offset": 100},
            context_for(tenants[1]),
        )
    assert denied.value.status_code == 403


def test_oversize_row_is_explicit_preview_and_still_advances(tenants):
    import json

    from mcp_server.response_limits import MAX_RESPONSE_BYTES

    actor, project, _ = tenants[0]
    job = create_job(
        actor,
        project,
        state="succeeded",
        result={
            "columns": [{"name": "text", "type": "String"}],
            "rows": [["界" * 100000], ["next"]],
            "row_count": 2,
        },
    )
    tool = GeneratedToolRegistry.from_manifest().get("get_sql_result")
    page = DjangoAPIExecutor().execute_sync(
        tool,
        {"id": str(job.id), "project_id": str(project.id), "limit": 1},
        context_for(tenants[0]),
    )
    assert len(json.dumps(page, ensure_ascii=False).encode()) < MAX_RESPONSE_BYTES
    assert page["result"]["previewed"] and page["result"]["retrieval_note"]
    assert page["result"]["next_offset"] == 1


def test_execution_dates_match_rest_and_mcp(tenants):
    from tracer.sql_workbench.tests.test_permissions import api_request
    from tracer.views.sql_query import SQLQueryViewSet

    actor, project, user = tenants[0]
    job = create_job(actor, project, state="succeeded", finished_at=timezone.now())
    rest = SQLQueryViewSet.as_view({"get": "retrieve"})(
        api_request(actor, user, "get", "/result", {"project_id": str(project.id)}),
        pk=str(job.id),
    ).data
    mcp = DjangoAPIExecutor().execute_sync(
        GeneratedToolRegistry.from_manifest().get("get_sql_result"),
        {"id": str(job.id), "project_id": str(project.id)},
        context_for(tenants[0]),
    )
    for field in ("created_at", "expires_at", "finished_at"):
        assert rest[field] == mcp[field]
        assert "T" in mcp[field] and mcp[field].endswith("Z")
