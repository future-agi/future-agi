from contextlib import asynccontextmanager
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from asgiref.sync import sync_to_async
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from accounts.models.user import OrgApiKey
from mcp_server import mcp_app
from mcp_server.models.connection import MCPConnection
from mcp_server.models.tool_config import MCPToolGroupConfig
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from tfc.middleware.workspace_context import get_current_workspace
from tracer.models.dashboard import Dashboard

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def protocol_client(user, workspace, monkeypatch):
    @asynccontextmanager
    async def connect(key_workspace=None):
        credentials = await sync_to_async(OrgApiKey.objects.create)(
            name="MCP transport test",
            api_key=f"mcp-transport-{uuid4().hex}",
            secret_key=uuid4().hex,
            organization=user.organization,
            workspace=key_workspace or workspace,
            user=user,
            type="mcp",
        )
        monkeypatch.setattr(mcp_app, "_streamable_app", None)
        monkeypatch.setattr(mcp_app, "_session_manager", None)
        mcp_app.get_mcp_streamable_app()
        async with (
            mcp_app._session_manager.run(),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_app.mcp_streamable_with_auth),
                base_url="http://localhost",
                headers={
                    "X-Api-Key": credentials.api_key,
                    "X-Secret-Key": credentials.secret_key,
                },
            ) as http_client,
            streamable_http_client(
                "http://localhost/mcp", http_client=http_client
            ) as streams,
        ):
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session

    return connect


@pytest.fixture
def oauth_protocol_client(user, workspace, monkeypatch):
    """Drive the same transport with an OAuth Bearer token instead of a key.

    The Bearer branch resolves the tenant from the token payload and never has
    an ``OrgApiKey``, so it exercises a different half of the auth rewrite.
    """

    @asynccontextmanager
    async def connect(token_user=None, token_workspace=None, expires_in=3600):
        from mcp_server.oauth_utils import generate_oauth_token

        principal = token_user or user
        bound = token_workspace if token_workspace is not None else workspace
        token, _ = await sync_to_async(generate_oauth_token)(
            principal.id,
            principal.organization_id,
            bound.id if bound is not None else None,
            "test-client",
            "context datasets",
            expires_in=expires_in,
        )
        monkeypatch.setattr(mcp_app, "_streamable_app", None)
        monkeypatch.setattr(mcp_app, "_session_manager", None)
        mcp_app.get_mcp_streamable_app()
        async with (
            mcp_app._session_manager.run(),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_app.mcp_streamable_with_auth),
                base_url="http://localhost",
                headers={"Authorization": f"Bearer {token}"},
            ) as http_client,
            streamable_http_client(
                "http://localhost/mcp", http_client=http_client
            ) as streams,
        ):
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session

    return connect


async def _post_initialize(authorization):
    """POST an initialize request with a raw header, bypassing the MCP client.

    An unauthenticated request never reaches the MCP app, so the SDK client
    cannot be used to observe the 401 and its WWW-Authenticate challenge.
    """
    mcp_app.get_mcp_streamable_app()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if authorization is not None:
        headers["Authorization"] = authorization
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mcp_app.mcp_streamable_with_auth),
        base_url="http://localhost",
    ) as http_client:
        return await http_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
            },
        )


async def test_streamable_http_accepts_a_valid_bearer_token(
    oauth_protocol_client, user, workspace
):
    """A valid Bearer token must initialize, list and call like a key does."""
    async with oauth_protocol_client() as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools}

        result = await client.call_tool("whoami", {})

    assert result.isError is False
    assert result.structuredContent["id"] == str(user.id)
    assert result.structuredContent["default_workspace_id"] == str(workspace.id)


async def test_streamable_http_rejects_an_expired_bearer_token(user, workspace):
    """An expired token must 401 with the RFC 9728 challenge, not fall back."""
    from mcp_server.oauth_utils import generate_oauth_token

    token, _ = await sync_to_async(generate_oauth_token)(
        user.id,
        user.organization_id,
        workspace.id,
        "test-client",
        "context",
        expires_in=-30,
    )
    response = await _post_initialize(f"Bearer {token}")

    assert response.status_code == 401
    assert "oauth-protected-resource" in response.headers["WWW-Authenticate"]
    assert response.json()["error"] == "invalid_token"


async def test_streamable_http_rejects_a_malformed_bearer_token():
    response = await _post_initialize("Bearer not-a-real-token")

    assert response.status_code == 401
    assert "oauth-protected-resource" in response.headers["WWW-Authenticate"]


async def test_streamable_http_requires_credentials():
    response = await _post_initialize(None)

    assert response.status_code == 401
    assert response.json()["error_description"] == "Authentication required"


async def test_bearer_transport_fails_closed_when_org_access_is_revoked(
    oauth_protocol_client, user, workspace
):
    """The Bearer path carries no OrgApiKey, so the executor's tenant guards
    are the only thing preventing a fallback into another tenant."""
    from accounts.models.organization_membership import OrganizationMembership
    from model_hub.models.develop_dataset import Dataset

    membership = await sync_to_async(
        lambda: OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=user.organization
        ).first()
    )()
    assert membership is not None, "expected an organization membership to revoke"

    async with oauth_protocol_client() as client:
        before = await client.call_tool(
            "create_dataset", {"new_dataset_name": "oauth before revoke"}
        )
        assert before.isError is False, before

        membership.is_active = False
        await sync_to_async(membership.save)(update_fields=["is_active"])

        after = await client.call_tool(
            "create_dataset", {"new_dataset_name": "oauth after revoke"}
        )
        listed = await client.call_tool("list_datasets", {})

    assert after.isError is True
    assert after.structuredContent["error"]["code"] == "HTTP_403"
    assert listed.isError is True
    assert listed.structuredContent["error"]["code"] == "HTTP_403"
    assert not await sync_to_async(
        Dataset.no_workspace_objects.filter(name="oauth after revoke").exists
    )()


async def test_streamable_http_lists_and_executes_generated_tools(
    protocol_client, user, workspace
):
    async with protocol_client() as client:
        tools = await client.list_tools()
        expected = await sync_to_async(
            lambda: {
                tool.name for tool in mcp_app.registry.list_all() if tool.is_available()
            }
        )()
        assert {tool.name for tool in tools.tools} == expected
        result = await client.call_tool("whoami", {})
        assert result.isError is False
        assert result.structuredContent["id"] == str(user.id)
        assert result.structuredContent["default_workspace_id"] == str(workspace.id)


async def test_streamable_http_hands_the_api_key_to_the_executor(
    protocol_client, user, workspace
):
    """The credential that authenticated the request must reach the executor so
    Django resolves the tenant from the key, exactly as the REST API does."""
    seen = []
    original = mcp_app.executor.execute_sync

    def capture(tool, arguments, context):
        seen.append(context)
        return original(tool, arguments, context)

    with patch.object(mcp_app.executor, "execute_sync", side_effect=capture):
        async with protocol_client() as client:
            result = await client.call_tool("whoami", {})

    assert result.isError is False
    (context,) = seen
    assert context.api_key is not None
    assert context.api_key.type == "mcp"
    assert context.api_key.workspace_id == workspace.id
    assert context.organization.id == user.organization.id
    assert context.workspace.id == workspace.id


async def test_streamable_http_denies_tools_once_the_key_workspace_is_deactivated(
    protocol_client, user, workspace
):
    """A key scoped to a workspace that gets deactivated must fail with 403
    instead of silently executing in the organization's default workspace."""
    from accounts.models.workspace import Workspace
    from model_hub.models.develop_dataset import Dataset

    other = await sync_to_async(Workspace.objects.create)(
        name="Key workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    async with protocol_client(key_workspace=other) as client:
        before = await client.call_tool(
            "create_dataset", {"new_dataset_name": "keyed before deactivation"}
        )
        assert before.isError is False, before
        created = await sync_to_async(Dataset.no_workspace_objects.get)(
            name="keyed before deactivation"
        )
        assert created.workspace_id == other.id

        other.is_active = False
        await sync_to_async(other.save)(update_fields=["is_active"])

        after = await client.call_tool(
            "create_dataset", {"new_dataset_name": "keyed after deactivation"}
        )
        listed = await client.call_tool("list_datasets", {})

    assert after.isError is True
    assert after.structuredContent["error"]["code"] == "HTTP_403"
    assert listed.isError is True
    assert listed.structuredContent["error"]["code"] == "HTTP_403"
    assert not await sync_to_async(
        Dataset.no_workspace_objects.filter(name="keyed after deactivation").exists
    )()
    assert not await sync_to_async(
        Dataset.no_workspace_objects.filter(workspace=workspace).exists
    )()


async def test_protocol_creates_updates_and_reads_prompt(
    protocol_client, user, workspace
):
    async with protocol_client() as client:
        name = f"mcp-prompt-{uuid4().hex}"
        created = await client.call_tool(
            "create_prompt_template", {"name": name, "description": None}
        )
        assert not created.isError, created
        template_id = created.structuredContent["id"]
        updated = await client.call_tool(
            "update_prompt_template",
            {
                "id": template_id,
                "description": "updated through MCP",
                "prompt_folder": None,
            },
        )
        assert not updated.isError, updated
        template = await sync_to_async(PromptTemplate.no_workspace_objects.get)(
            pk=template_id
        )
        assert template.name == name
        assert template.description == "updated through MCP"
        assert template.organization_id == user.organization_id
        assert template.workspace_id == workspace.id
        assert template.prompt_folder_id is None
        read = await client.call_tool("get_prompt_template", {"id": template_id})
        assert not read.isError, read
        assert read.structuredContent["name"] == name


async def test_protocol_passes_workbench_execution_arguments(
    protocol_client, user, workspace
):
    async with protocol_client() as client:
        template = await sync_to_async(PromptTemplate.objects.create)(
            name=f"mcp-run-{uuid4().hex}",
            organization=user.organization,
            workspace=workspace,
            created_by=user,
        )
        version = await sync_to_async(PromptVersion.objects.create)(
            original_template=template,
            template_version="v1",
            is_draft=False,
            prompt_config_snapshot={
                "messages": [{"role": "user", "content": "hello"}],
                "configuration": {"model": "test-model"},
            },
        )
        # Only background scheduling is replaced. Auth, schema validation,
        # request mapping, permissions, the Django view and its writes all run.
        with patch("model_hub.views.prompt_template.submit_with_retry") as submit:
            result = await client.call_tool(
                "run_prompt",
                {
                    "id": str(template.id),
                    "version": "v1",
                    "is_run": "prompt",
                    "variable_names": {"name": ["Ada"]},
                },
            )
        assert not result.isError, result
        assert result.structuredContent["template_id"] == str(template.id)
        assert submit.call_count == 1
        assert submit.call_args.args[5:7] == ("v1", "prompt")
        await sync_to_async(version.refresh_from_db)()
        assert version.variable_names == {"name": ["Ada"]}


async def test_view_modules_load_before_the_request_workspace_is_bound(
    protocol_client, monkeypatch
):
    # A cold worker whose first request is an MCP call must not import the
    # URLconf with a tenant's workspace in scope; class-level querysets in the
    # view modules would otherwise be pinned to that tenant for every caller.
    seen = []
    real_loader = mcp_app.ensure_urlconf_loaded

    def spying_loader():
        seen.append(get_current_workspace())
        real_loader()

    monkeypatch.setattr(mcp_app, "ensure_urlconf_loaded", spying_loader)
    async with protocol_client() as client:
        listing = await client.list_tools()
    assert listing.tools
    assert seen and set(seen) == {None}


async def test_protocol_builds_and_reads_a_dashboard(protocol_client, user, workspace):
    async with protocol_client() as client:
        name = f"mcp-dashboard-{uuid4().hex}"
        created = await client.call_tool(
            "create_dashboard", {"name": name, "description": "Built over MCP"}
        )
        assert not created.isError, created
        dashboard_id = created.structuredContent["id"]
        dashboard = await sync_to_async(Dashboard.objects.get)(pk=dashboard_id)
        assert dashboard.workspace_id == workspace.id
        assert dashboard.created_by_id == user.id

        query_config = {
            "time_range": {"preset": "7D"},
            "metrics": [
                {"name": "latency", "type": "system_metric", "aggregation": "p95"}
            ],
        }
        widget = await client.call_tool(
            "create_dashboard_widget",
            {
                "dashboard_pk": dashboard_id,
                "name": "p95 latency",
                "query_config": query_config,
                "chart_config": {"chart_type": "line"},
            },
        )
        assert not widget.isError, widget
        widget_id = widget.structuredContent["id"]
        assert widget.structuredContent["query_config"] == query_config

        renamed = await client.call_tool(
            "update_dashboard_widget",
            {"dashboard_pk": dashboard_id, "id": widget_id, "width": 6},
        )
        assert not renamed.isError, renamed
        assert renamed.structuredContent["width"] == 6

        detail = await client.call_tool("get_dashboard", {"id": dashboard_id})
        assert not detail.isError, detail
        assert detail.structuredContent["name"] == name
        assert [w["id"] for w in detail.structuredContent["widgets"]] == [widget_id]

        widgets = await client.call_tool(
            "list_dashboard_widgets", {"dashboard_pk": dashboard_id}
        )
        assert not widgets.isError, widgets
        assert [w["id"] for w in widgets.structuredContent["results"]] == [widget_id]

        listing = await client.call_tool("list_dashboards", {})
        assert not listing.isError, listing
        assert dashboard_id in {d["id"] for d in listing.structuredContent["result"]}


async def test_protocol_applies_tool_group_changes(protocol_client, user, workspace):
    async with protocol_client() as client:
        first = await client.call_tool("list_datasets", {})
        assert not first.isError, first

        def disable_datasets():
            connection = MCPConnection.no_workspace_objects.get(
                user=user, workspace=workspace, deleted=False
            )
            config = MCPToolGroupConfig.no_workspace_objects.get(connection=connection)
            config.enabled_groups = [
                group for group in config.enabled_groups if group != "datasets"
            ]
            config.save(update_fields=["enabled_groups"])

        await sync_to_async(disable_datasets)()
        listing = await client.list_tools()
        assert "list_datasets" not in {tool.name for tool in listing.tools}
        denied = await client.call_tool("list_datasets", {})
        assert denied.isError
        assert "disabled" in denied.content[0].text.lower()


async def test_protocol_dataset_columns_and_evaluation_setup(
    protocol_client, user, workspace
):
    from model_hub.models.choices import StatusType
    from model_hub.models.develop_dataset import Column, Dataset
    from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric

    async with protocol_client() as client:
        created = await client.call_tool(
            "create_dataset", {"new_dataset_name": "MCP dataset workflow"}
        )
        assert not created.isError, created
        dataset = await sync_to_async(Dataset.no_workspace_objects.get)(
            name="MCP dataset workflow", workspace=workspace
        )
        dataset_id = str(dataset.id)
        added = await client.call_tool(
            "create_dataset_column",
            {
                "dataset_id": dataset_id,
                "new_column_name": "question",
                "column_type": "text",
            },
        )
        assert not added.isError, added
        column = await sync_to_async(Column.objects.get)(
            dataset=dataset, name="question"
        )
        renamed = await client.call_tool(
            "rename_dataset_column",
            {
                "dataset_id": dataset_id,
                "column_id": str(column.id),
                "new_column_name": "input",
            },
        )
        assert not renamed.isError, renamed
        rows = await client.call_tool(
            "add_dataset_rows",
            {
                "dataset_id": dataset_id,
                "rows": [{"cells": [{"column_name": "input", "value": "Hello"}]}],
            },
        )
        assert not rows.isError, rows
        template = await sync_to_async(EvalTemplate.objects.create)(
            name="mcp_word_count",
            organization=user.organization,
            workspace=workspace,
            owner="user",
            visible_ui=True,
            config={"required_keys": ["text"], "output": "Pass/Fail", "config": {}},
        )
        invalid = await client.call_tool(
            "configure_dataset_evaluation",
            {
                "dataset_id": dataset_id,
                "name": "missing_mapping",
                "template_id": str(template.id),
                "config": {"mapping": {}},
                "run": False,
            },
        )
        assert invalid.isError
        assert "mapping" in invalid.content[0].text.lower()
        attached = await client.call_tool(
            "configure_dataset_evaluation",
            {
                "dataset_id": dataset_id,
                "name": "word_count",
                "template_id": str(template.id),
                "config": {"mapping": {"text": "input"}},
                "run": False,
            },
        )
        assert not attached.isError, attached
        listing = await client.call_tool(
            "list_dataset_evaluations", {"dataset_id": dataset_id, "eval_type": "user"}
        )
        assert not listing.isError, listing
        metric = await sync_to_async(UserEvalMetric.objects.get)(
            dataset=dataset, name="word_count"
        )
        assert str(metric.id) in {
            item["id"] for item in listing.structuredContent["evals"]
        }
        queued = await client.call_tool(
            "run_dataset_evals",
            {"dataset_id": dataset_id, "user_eval_ids": [str(metric.id)]},
        )
        assert not queued.isError, queued
        await sync_to_async(metric.refresh_from_db)()
        assert metric.status == StatusType.NOT_STARTED.value
        assert await sync_to_async(
            Column.objects.filter(dataset=dataset, source_id=str(metric.id)).exists
        )()


async def test_protocol_dataset_prompt_setup_and_row_selection(
    protocol_client, user, workspace
):
    from model_hub.models.develop_dataset import Column, Dataset, Row
    from model_hub.models.run_prompt import RunPrompter

    dataset = await sync_to_async(Dataset.objects.create)(
        name="MCP prompt dataset",
        organization=user.organization,
        workspace=workspace,
        user=user,
    )
    row_a = await sync_to_async(Row.objects.create)(dataset=dataset, order=0)
    row_b = await sync_to_async(Row.objects.create)(dataset=dataset, order=1)
    async with protocol_client() as client:
        config = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Say hello"}]}
            ],
            "output_format": "string",
            "run_prompt_config": {"temperature": None},
        }
        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as initial:
            result = await client.call_tool(
                "add_dataset_prompt",
                {
                    "dataset_id": str(dataset.id),
                    "name": "answer",
                    "config": config,
                },
            )
        assert not result.isError, result
        initial.assert_called_once()
        prompt = await sync_to_async(RunPrompter.objects.get)(
            dataset=dataset, name="answer"
        )
        column = await sync_to_async(Column.objects.get)(dataset=dataset, name="answer")
        with patch("model_hub.tasks.run_prompt.process_prompts_single.apply_async"):
            updated = await client.call_tool(
                "update_dataset_prompt",
                {
                    "dataset_id": str(dataset.id),
                    "column_id": str(column.id),
                    "name": "response",
                    "config": config,
                },
            )
        assert not updated.isError, updated
        with patch(
            "model_hub.views.run_prompt.run_all_prompts_task.apply_async"
        ) as submit:
            selected = await client.call_tool(
                "run_dataset_prompts",
                {
                    "run_prompt_ids": [str(prompt.id)],
                    "row_ids": [str(row_a.id)],
                    "selected_all_rows": True,
                },
            )
        assert not selected.isError, selected
        assert submit.call_count == 1
        selected_ids = submit.call_args.kwargs["args"][1]
        assert {str(value) for value in selected_ids} == {str(row_b.id)}
        stats = await client.call_tool(
            "get_dataset_prompt_stats", {"dataset_id": str(dataset.id)}
        )
        assert not stats.isError, stats
        assert str(prompt.id) in str(stats.structuredContent)


async def test_protocol_reads_requested_prompt_version(
    protocol_client, user, workspace
):
    template = await sync_to_async(PromptTemplate.objects.create)(
        name="MCP version polling",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    for label, output, default in [("v1", "first", True), ("v2", "second", False)]:
        await sync_to_async(PromptVersion.objects.create)(
            original_template=template,
            template_version=label,
            output=[output],
            is_default=default,
            prompt_config_snapshot={},
        )
    async with protocol_client() as client:
        result = await client.call_tool(
            "get_prompt_run", {"id": str(template.id), "template_version": "v2"}
        )
        assert not result.isError, result
        data = result.structuredContent
        assert data["executions_result"]["template_version"] == "v2"
        assert data["executions_result"]["output"] == ["second"]
        missing = await client.call_tool(
            "get_prompt_run", {"id": str(template.id), "template_version": "missing"}
        )
        assert missing.isError
        assert missing.structuredContent["error"]["code"] == "HTTP_404"


async def test_protocol_agent_scenario_and_simulation_handoff(
    protocol_client, user, workspace, settings
):
    from model_hub.models.choices import StatusType
    from simulate.models import AgentDefinition, RunTest, Scenarios, TestExecution

    settings.HOSTED_RUNNER_ENABLED = False
    settings.TEMPORAL_TEST_EXECUTION_ENABLED = False
    async with protocol_client() as client:
        created = await client.call_tool(
            "create_agent",
            {
                "agent_name": "MCP support agent",
                "agent_type": "text",
                "commit_message": "Initial",
            },
        )
        assert not created.isError, created
        agent_id = created.structuredContent["agent"]["id"]
        updated = await client.call_tool(
            "update_agent",
            {"agent_id": agent_id, "description": "Updated configuration"},
        )
        assert not updated.isError, updated
        versioned = await client.call_tool(
            "create_agent_version",
            {"agent_id": agent_id, "commit_message": "Simulation version"},
        )
        assert not versioned.isError, versioned
        agent = await sync_to_async(AgentDefinition.objects.get)(pk=agent_id)
        version = await sync_to_async(lambda: agent.active_version)()
        assert version.description == "Updated configuration"

        with (
            patch("tfc.ee_gating.check_ee_feature", return_value=None),
            patch(
                "simulate.views.scenarios.start_create_script_scenario_workflow_sync"
            ) as generate,
        ):
            scenario_result = await client.call_tool(
                "create_scenario",
                {
                    "name": "Support scenario",
                    "kind": "script",
                    "agent_definition_id": agent_id,
                    "script_url": "https://example.com/script.txt",
                    "no_of_rows": 10,
                },
            )
        assert not scenario_result.isError, scenario_result
        generate.assert_called_once()
        scenario_id = scenario_result.structuredContent["scenario"]["id"]
        edited = await client.call_tool(
            "update_scenario", {"scenario_id": scenario_id, "name": "Support checks"}
        )
        assert not edited.isError, edited
        saved = await client.call_tool(
            "create_simulation_test",
            {
                "name": "MCP simulation",
                "agent_definition_id": agent_id,
                "agent_version": str(version.id),
                "scenario_ids": [scenario_id],
            },
        )
        assert not saved.isError, saved
        run_test_id = saved.structuredContent["id"]
        test = await sync_to_async(RunTest.objects.get)(pk=run_test_id)
        assert test.workspace_id == workspace.id
        assert test.agent_version_id == version.id
        detail = await client.call_tool(
            "get_simulation_test", {"run_test_id": run_test_id}
        )
        assert not detail.isError, detail
        listing = await client.call_tool("list_simulation_tests", {})
        assert not listing.isError, listing
        assert run_test_id in str(listing.structuredContent)

        # An asynchronous scenario is not executable until generation completes.
        with patch("simulate.views.run_test.TestExecutor.execute_test") as executor:
            blocked = await client.call_tool(
                "run_simulation", {"run_test_id": run_test_id}
            )
        assert blocked.isError
        executor.assert_not_called()

        # Seed the background worker's completed scenario, then verify dispatch
        # and the real result-read API. No paid LLM or provider call is made.
        await sync_to_async(Scenarios.objects.filter(pk=scenario_id).update)(
            status=StatusType.COMPLETED.value
        )
        execution = await sync_to_async(TestExecution.objects.create)(
            run_test=test,
            total_scenarios=1,
            status="pending",
        )
        receipt = {
            "success": True,
            "execution_id": str(execution.id),
            "run_test_id": run_test_id,
            "status": "pending",
            "total_scenarios": 1,
            "total_calls": 0,
        }
        with patch(
            "simulate.views.run_test.TestExecutor.execute_test", return_value=receipt
        ) as execute:
            started = await client.call_tool(
                "run_simulation", {"run_test_id": run_test_id}
            )
        assert not started.isError, started
        assert started.structuredContent["execution_id"] == str(execution.id)
        assert execute.call_args.kwargs["scenario_ids"] == [scenario_id]
        read = await client.call_tool(
            "get_test_execution", {"test_execution_id": str(execution.id)}
        )
        assert not read.isError, read
        assert read.structuredContent["status"] == "pending"


async def test_protocol_online_eval_pause_edit_resume(protocol_client, user, workspace):
    from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
    from tracer.models.project import Project

    project = await sync_to_async(Project.objects.create)(
        name="MCP eval project",
        organization=user.organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    task = await sync_to_async(EvalTask.objects.create)(
        project=project,
        name="MCP eval task",
        filters={},
        sampling_rate=100,
        spans_limit=100,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.RUNNING,
    )
    args = {"eval_task_id": str(task.id)}
    async with protocol_client() as client:
        with patch("tracer.views.eval_task.signal_pause_eval_task_workflow") as signal:
            paused = await client.call_tool("pause_eval_task", args)
        assert not paused.isError, paused
        signal.assert_called_once()
        with patch("tracer.views.eval_task.start_eval_task_workflow_sync") as start:
            updated = await client.call_tool(
                "update_eval_task",
                {**args, "edit_type": "edit_rerun", "name": "Updated through MCP"},
            )
        assert not updated.isError, updated
        start.assert_called_once()
        await sync_to_async(task.refresh_from_db)()
        assert task.name == "Updated through MCP"
        assert task.status == EvalTaskStatus.PENDING
        await sync_to_async(EvalTask.objects.filter(pk=task.id).update)(
            status=EvalTaskStatus.PAUSED
        )
        with patch("tracer.views.eval_task.start_eval_task_workflow_sync") as resume:
            resumed = await client.call_tool("resume_eval_task", args)
        assert not resumed.isError, resumed
        resume.assert_called_once()
