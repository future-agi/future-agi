import uuid
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
def test_unknown_dataset_columns_do_not_block_row_creation(user, workspace):
    dataset = Dataset.objects.create(
        name="MCP columns",
        organization=user.organization,
        workspace=workspace,
        user=user,
    )
    with patch(
        "model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request",
        None,
    ):
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
    assert Row.objects.filter(dataset=dataset).exists()


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


def _org_member_with_own_workspace(organization, name):
    """A member-level user whose only workspace is a non-default one.

    Member level (not admin) means workspace access comes purely from the
    WorkspaceMembership row, so revoking it is a real loss of access.
    """
    from accounts.models import User
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    member = User.objects.create_user(
        email=f"{name}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name=name,
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    org_membership = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=organization,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    own_workspace = Workspace.objects.create(
        name=f"{name} workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=member,
    )
    ws_membership = WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=own_workspace,
        role="Workspace Admin",
        level=Level.WORKSPACE_ADMIN,
        is_active=True,
        organization_membership=org_membership,
    )
    return member, own_workspace, org_membership, ws_membership


def _assert_denied_without_side_effects(context, dataset_name):
    with pytest.raises(APIExecutionError) as error:
        executor.execute_sync(
            registry.get("create_dataset"),
            {"new_dataset_name": dataset_name},
            context,
        )
    assert error.value.status_code == 403
    assert not Dataset.no_workspace_objects.filter(name=dataset_name).exists()

    with pytest.raises(APIExecutionError) as error:
        executor.execute_sync(registry.get("list_datasets"), {}, context)
    assert error.value.status_code == 403


@pytest.mark.django_db
def test_revoked_workspace_membership_fails_closed(user, workspace):
    """Losing the credential's workspace must not fall back to another one.

    Django's resolver would otherwise auto-join the member to the default
    workspace and run the tool there, in a workspace the key never targeted.
    """
    from accounts.models.workspace import WorkspaceMembership

    member, own_workspace, _, ws_membership = _org_member_with_own_workspace(
        user.organization, "revoked-member"
    )
    context = MCPRequestContext(member, user.organization, own_workspace)
    executor.execute_sync(
        registry.get("create_dataset"), {"new_dataset_name": "before revoke"}, context
    )
    assert Dataset.no_workspace_objects.get(name="before revoke").workspace_id == (
        own_workspace.id
    )

    ws_membership.is_active = False
    ws_membership.save(update_fields=["is_active"])

    _assert_denied_without_side_effects(context, "after revoke")
    assert not Dataset.no_workspace_objects.filter(workspace=workspace).exists()
    assert not WorkspaceMembership.no_workspace_objects.filter(
        user=member, workspace=workspace
    ).exists()


@pytest.mark.django_db
def test_deactivated_workspace_fails_closed(user, workspace):
    member, own_workspace, _, _ = _org_member_with_own_workspace(
        user.organization, "inactive-workspace-member"
    )
    context = MCPRequestContext(member, user.organization, own_workspace)

    own_workspace.is_active = False
    own_workspace.save(update_fields=["is_active"])

    _assert_denied_without_side_effects(context, "in deactivated workspace")
    assert not Dataset.no_workspace_objects.filter(workspace=workspace).exists()


@pytest.mark.django_db
def test_revoked_organization_membership_fails_closed(user, workspace):
    """A user removed from the org must get 403, not their next organization."""
    from accounts.models import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import Workspace
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    member, own_workspace, org_membership, _ = _org_member_with_own_workspace(
        user.organization, "moved-member"
    )
    other_org = Organization.objects.create(name="Other Organization")
    OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=other_org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    other_workspace = Workspace.objects.create(
        name="Other default",
        organization=other_org,
        is_default=True,
        is_active=True,
        created_by=member,
    )
    context = MCPRequestContext(member, user.organization, own_workspace)

    org_membership.is_active = False
    org_membership.save(update_fields=["is_active"])

    _assert_denied_without_side_effects(context, "after leaving org")
    assert not Dataset.no_workspace_objects.filter(
        organization=other_org
    ).exists(), "tool must not run in the user's other organization"
    assert not Dataset.no_workspace_objects.filter(workspace=other_workspace).exists()


@pytest.mark.django_db
def test_resolved_tenant_drift_is_denied_even_if_membership_checks_pass(
    user, workspace
):
    """Defense in depth: if Django resolves any tenant other than the one the
    credential authenticated as, the executor refuses rather than proceeding."""
    from accounts.models.workspace import Workspace

    other_workspace = Workspace.objects.create(
        name="Drifted",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    context = MCPRequestContext(user, user.organization, workspace)

    def resolve_elsewhere(self, request, _user):
        request.organization = user.organization
        request.workspace = other_workspace

    with patch(
        "mcp_server.api_executor.APIKeyAuthentication._set_workspace_context",
        resolve_elsewhere,
    ):
        with pytest.raises(APIExecutionError) as error:
            executor.execute_sync(
                registry.get("create_dataset"),
                {"new_dataset_name": "drifted dataset"},
                context,
            )
    assert error.value.status_code == 403
    assert not Dataset.no_workspace_objects.filter(name="drifted dataset").exists()


@pytest.mark.django_db
def test_api_key_context_resolves_tenant_from_the_key(user, workspace):
    """With the OrgApiKey on the context, Django resolves the org from the key
    (as the REST API does) and the key's workspace is honoured."""
    from accounts.models import OrgApiKey

    member, own_workspace, _, _ = _org_member_with_own_workspace(
        user.organization, "keyed-member"
    )
    org_api_key = OrgApiKey.no_workspace_objects.create(
        organization=user.organization,
        workspace=own_workspace,
        user=member,
        type="user",
        enabled=True,
        api_key=f"fi-{uuid.uuid4().hex}",
        secret_key=f"sk-{uuid.uuid4().hex}",
    )
    context = MCPRequestContext(
        member, user.organization, own_workspace, api_key=org_api_key
    )

    executor.execute_sync(
        registry.get("create_dataset"), {"new_dataset_name": "keyed dataset"}, context
    )

    created = Dataset.no_workspace_objects.get(name="keyed dataset")
    assert created.workspace_id == own_workspace.id
    assert created.organization_id == user.organization.id


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
def test_agents_only_connection_sees_exactly_the_legacy_agents_tools(mcp_connection):
    """A pre-migration config with only `agents` enabled must not silently gain
    write access to agent definitions / scenarios, nor lose the tools it had."""
    config = mcp_connection.tool_config
    config.enabled_groups = ["agents"]
    config.save(update_fields=["enabled_groups"])

    assert get_enabled_tools(mcp_connection) == {
        "list_agents",
        "get_agent",
        "list_scenarios",
        "list_test_executions",
        "get_test_execution",
        "run_simulation",
    }


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
