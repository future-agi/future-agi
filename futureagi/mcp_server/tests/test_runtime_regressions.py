from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from django.db import connections
from rest_framework.exceptions import PermissionDenied

from mcp_server.api_executor import (
    APIExecutionError,
    MCPRequestContext,
    ensure_urlconf_loaded,
    executor,
)
from mcp_server.generated_registry import registry
from mcp_server.models.connection import MCPConnection
from mcp_server.models.session import MCPSession
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.usage_helpers import (
    get_enabled_tools,
    get_or_create_connection,
    update_session_counters,
)
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.run_prompt import PromptTemplate
from model_hub.serializers.contracts import CreateEmptyDatasetRequestSerializer
from model_hub.serializers.prompt_requests import PromptRunRequestSerializer
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    workspace_context,
)


def test_workbench_save_accepts_existing_nullable_provider_settings():
    serializer = PromptRunRequestSerializer(
        data={
            "is_run": None,
            "name": "Saved prompt",
            "variable_names": {},
            "placeholders": [],
            "evaluation_configs": [],
            "source": "dataset",
            "prompt_config": [
                {
                    "messages": [{"role": "user", "content": "Hello"}],
                    "configuration": {
                        "model": "gpt-4o-mini",
                        "temperature": None,
                        "max_tokens": None,
                        "response_format": None,
                    },
                }
            ],
        }
    )
    assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
def test_usage_never_persists_nested_provider_credentials(mcp_session):
    from mcp_server.usage_helpers import record_usage

    params = {
        "agent_name": "Support",
        "api_key": "secret-provider-key",
        "config": {"livekit_api_secret": "nested-secret"},
        "providers": [{"websocket_headers": {"X-Custom-Auth": "private-header"}}],
    }
    record_usage(
        mcp_session,
        "create_agent",
        "simulation",
        params,
        "error",
        "Invalid secret-provider-key",
        10,
    )
    record = mcp_session.usage_records.get()
    assert record.request_params["agent_name"] == "Support"
    assert record.request_params["api_key"] == "[Filtered]"
    assert record.request_params["config"]["livekit_api_secret"] == "[Filtered]"
    assert record.request_params["providers"][0]["websocket_headers"] == "[Filtered]"
    assert "secret-provider-key" not in record.error_message
    assert params["api_key"] == "secret-provider-key"


def test_minimal_dataset_request_has_a_valid_model_type():
    serializer = CreateEmptyDatasetRequestSerializer(
        data={"new_dataset_name": "MCP dataset"}
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["model_type"] == "GenerativeLLM"


@pytest.mark.django_db
def test_unknown_dataset_columns_fail_before_creating_blank_rows(user, workspace):
    dataset = Dataset.objects.create(
        name="MCP columns",
        organization=user.organization,
        workspace=workspace,
        user=user,
    )
    with pytest.raises(APIExecutionError) as error:
        executor.execute_sync(
            registry.get("add_dataset_rows"),
            {
                "dataset_id": str(dataset.id),
                "rows": [
                    {"cells": [{"column_name": "missing", "value": "must survive"}]}
                ],
            },
            MCPRequestContext(user, user.organization, workspace),
        )
    assert error.value.status_code == 400
    assert "Unknown dataset columns" in str(error.value)
    assert not Row.objects.filter(dataset=dataset).exists()


@pytest.mark.django_db
def test_urlconf_loading_refuses_to_run_inside_a_bound_workspace(user, workspace):
    # Importing view modules evaluates class-level querysets such as
    # ``queryset = Model.objects.all()``; a bound workspace would be pinned.
    clear_workspace_context()
    ensure_urlconf_loaded()
    with workspace_context(workspace, organization=user.organization, user=user):
        with pytest.raises(RuntimeError, match="before a workspace is bound"):
            ensure_urlconf_loaded()


@pytest.mark.django_db
def test_workspace_denial_preserves_forbidden_status(user, workspace):
    context = MCPRequestContext(user, user.organization, workspace)
    with patch(
        "mcp_server.api_executor.APIKeyAuthentication._set_workspace_context",
        side_effect=PermissionDenied("Read-only workspace"),
    ):
        with pytest.raises(APIExecutionError) as error:
            executor.execute_sync(
                registry.get("create_prompt_template"),
                {"name": "Must not be created"},
                context,
            )
    assert error.value.status_code == 403
    assert not PromptTemplate.objects.filter(name="Must not be created").exists()


@pytest.mark.django_db
def test_workbench_get_requires_write_access_before_creating_a_draft(user, workspace):
    from model_hub.models.run_prompt import PromptVersion

    template = PromptTemplate.objects.create(
        name="Read-only workbench",
        organization=user.organization,
        workspace=workspace,
    )
    with patch(
        "mcp_server.api_executor.APIKeyAuthentication._can_write_to_workspace",
        return_value=False,
    ):
        with pytest.raises(APIExecutionError) as error:
            executor.execute_sync(
                registry.get("get_prompt_template"),
                {"id": str(template.id)},
                MCPRequestContext(user, user.organization, workspace),
            )
    assert error.value.status_code == 403
    assert not PromptVersion.objects.filter(original_template=template).exists()


@pytest.mark.django_db
def test_oversized_write_response_retains_success_and_created_id(user, workspace):
    result = executor.execute_sync(
        registry.get("create_prompt_template"),
        {"name": "Large MCP prompt", "description": "x" * 70000},
        MCPRequestContext(user, user.organization, workspace),
    )
    assert result["_mcp"]["truncated"] is True
    assert "succeeded" in result["_mcp"]["message"]
    saved = PromptTemplate.objects.get(pk=result["id"])
    assert saved.description == "x" * 70000


@pytest.mark.django_db
def test_oversized_read_post_remains_a_size_error(user, workspace):
    # List-eval-templates is a read even though its filters use a POST body.
    with patch.object(
        executor, "_normalize_response", return_value={"items": ["x" * 100000]}
    ):
        with pytest.raises(APIExecutionError) as error:
            executor.execute_sync(
                registry.get("list_eval_templates"),
                {},
                MCPRequestContext(user, user.organization, workspace),
            )
    assert error.value.status_code == 413


@pytest.mark.django_db
def test_oversized_workbench_get_preserves_its_write_receipt(user, workspace):
    template = PromptTemplate.objects.create(
        name="Oversized workbench",
        organization=user.organization,
        workspace=workspace,
    )
    with patch.object(
        executor,
        "_normalize_response",
        return_value={"id": str(template.id), "items": ["x" * 100000]},
    ):
        result = executor.execute_sync(
            registry.get("get_prompt_template"),
            {"id": str(template.id)},
            MCPRequestContext(user, user.organization, workspace),
        )
    assert result["id"] == str(template.id)
    assert result["_mcp"]["truncated"] is True


@pytest.mark.django_db
def test_session_counters_do_not_lose_updates_from_stale_instances(mcp_session):
    another = MCPSession.objects.get(pk=mcp_session.pk)
    update_session_counters(mcp_session, False)
    update_session_counters(another, True)
    mcp_session.refresh_from_db()
    assert (mcp_session.tool_call_count, mcp_session.error_count) == (2, 1)


@pytest.mark.django_db
def test_disabling_every_group_keeps_every_tool_disabled(mcp_connection):
    config = mcp_connection.tool_config
    config.enabled_groups = []
    config.save(update_fields=["enabled_groups"])
    assert get_enabled_tools(mcp_connection) == set()


@pytest.mark.django_db(transaction=True)
def test_parallel_first_requests_share_connection_and_config(user, workspace):
    barrier = Barrier(2)

    def initialize():
        try:
            with workspace_context(
                user=user, organization=user.organization, workspace=workspace
            ):
                barrier.wait(timeout=10)
                return get_or_create_connection(user, user.organization, workspace).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(initialize) for _ in range(2)]
        ids = [future.result(timeout=30) for future in futures]
    assert ids[0] == ids[1]
    assert (
        MCPConnection.no_workspace_objects.filter(
            user=user, workspace=workspace
        ).count()
        == 1
    )
    assert (
        MCPToolGroupConfig.no_workspace_objects.filter(connection_id=ids[0]).count()
        == 1
    )


def test_discovery_retains_full_input_schema_and_hides_unmounted_routes():
    from django.test import override_settings

    tool = registry.get("run_prompt")
    assert tool.to_discovery_dict()["input_schema"] == tool.input_schema
    with override_settings(ROOT_URLCONF="mcp_server.urls"):
        assert not tool.is_available()
