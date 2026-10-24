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
    assert result["result"] == payload


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
