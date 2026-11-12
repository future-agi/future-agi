"""Gateway scope refusals must precede client acquisition and config pushes.

IR answers in layers, each before any tenant read or gateway call:

- ``IsOrgGatewayAdmin`` (agentcc.permissions) refuses every member of
  ``gateway_admin_actions`` without an organization with 403, in DRF's
  ``initial()``, before the action body runs;
- the view's scope guard refuses a missing organization and a workspace that
  belongs to another organization with the 400 scope refusal;
- list/retrieve/config without an organization answer the gateway's own
  (tenant-free) state only: no tenant query, cache read or config push.

IR authentication resolves the workspace inside the organization and keeps
organization-level operations available without one, so a missing workspace
alone is not a scope failure.
"""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.permissions import GATEWAY_ADMIN_MESSAGE
from agentcc.services.delegated_availability import unavailable_response
from agentcc.views.gateway import AgentccGatewayViewSet

SCOPE_MESSAGE = "Organization and workspace context are required"
BATCH_ID = "scope-test-batch"
BATCH_REQUESTS = [
    {"custom_id": "first", "body": {"model": "scope-model", "prompt": "first"}},
    {"custom_id": "second", "body": {"model": "scope-model", "prompt": "second"}},
]
# Read from the view, not hand-listed: these get the 403 permission refusal.
ADMIN_ACTIONS = AgentccGatewayViewSet.gateway_admin_actions
# The virtual-gateway reads, whose orgless answer is the gateway's own state.
ORGLESS_READS = frozenset({"list", "retrieve", "config"})

# One row per gateway-reaching public action. protect_templates has no gateway
# path and intentionally stays outside this scope-policy sweep.
GATEWAY_ACTIONS = [
    pytest.param("list", "get", {}, id="list"),
    pytest.param("retrieve", "get", {}, id="retrieve"),
    pytest.param("health_check", "post", {}, id="health_check"),
    pytest.param("config", "get", {}, id="config"),
    pytest.param("reload", "post", {}, id="reload"),
    pytest.param(
        "update_config", "post", {"cache": {"enabled": False}}, id="update_config"
    ),
    pytest.param(
        "update_provider",
        "post",
        {"name": "scope-provider", "config": {"models": ["scope-model"]}},
        id="update_provider",
    ),
    pytest.param(
        "remove_provider", "post", {"name": "scope-provider"}, id="remove_provider"
    ),
    pytest.param(
        "toggle_guardrail",
        "post",
        {"name": "pii-detector", "enabled": True},
        id="toggle_guardrail",
    ),
    pytest.param(
        "update_guardrail",
        "post",
        {"name": "pii-detector", "config": {"enabled": True}},
        id="update_guardrail",
    ),
    pytest.param(
        "test_playground",
        "post",
        {"prompt": "scope check", "model": "scope-model"},
        id="test_playground",
    ),
    pytest.param(
        "set_budget",
        "post",
        {"level": "org_limit", "config": {"max_cost": 1}},
        id="set_budget",
    ),
    pytest.param("remove_budget", "post", {"level": "org_limit"}, id="remove_budget"),
    pytest.param(
        "submit_batch",
        "post",
        {"requests": BATCH_REQUESTS, "max_concurrency": 7},
        id="submit_batch",
    ),
    pytest.param("get_batch", "get", {"batch_id": BATCH_ID}, id="get_batch"),
    pytest.param("cancel_batch", "post", {"batch_id": BATCH_ID}, id="cancel_batch"),
    pytest.param("providers", "get", {}, id="providers"),
    pytest.param("mcp_status", "get", {}, id="mcp_status"),
    pytest.param("mcp_tools", "get", {}, id="mcp_tools"),
    pytest.param(
        "update_mcp_server",
        "post",
        {"server_id": "scope-server", "config": {"enabled": True}},
        id="update_mcp_server",
    ),
    pytest.param(
        "remove_mcp_server",
        "post",
        {"server_id": "scope-server"},
        id="remove_mcp_server",
    ),
    pytest.param(
        "update_mcp_guardrails",
        "post",
        {"config": {"enabled": True}},
        id="update_mcp_guardrails",
    ),
    pytest.param(
        "test_mcp_tool",
        "post",
        {"name": "scope-tool", "arguments": {}},
        id="test_mcp_tool",
    ),
    pytest.param("mcp_resources", "get", {}, id="mcp_resources"),
    pytest.param("mcp_prompts", "get", {}, id="mcp_prompts"),
]


@pytest.fixture
def gateway_seams():
    client = MagicMock(name="gateway_client")
    client.health_check.return_value = {}
    client.provider_health.return_value = {"providers": []}
    client.submit_batch.return_value = {"batch_id": BATCH_ID, "status": "pending"}
    client.get_batch.return_value = {"batch_id": BATCH_ID, "status": "pending"}
    client.cancel_batch.return_value = {"batch_id": BATCH_ID, "status": "cancelled"}
    with (
        patch(
            "agentcc.views.gateway.get_gateway_client", return_value=client
        ) as get_client,
        patch(
            "agentcc.views.gateway.push_all_org_configs", return_value=True
        ) as push_all,
        patch("agentcc.views.gateway.push_org_config", return_value=True) as push_org,
        patch("agentcc.views.gateway.cache") as cache,
    ):
        # An old empty ownership entry must not authorize an org-less request.
        # It also makes the old health-check path attempt its config push.
        cache.get.return_value = ""
        yield SimpleNamespace(
            client=client,
            get_client=get_client,
            push_all=push_all,
            push_org=push_org,
            cache=cache,
        )


@contextmanager
def _spy_tenant_reads():
    """The tenant tables gateway actions read first; entered only around the
    request so fixture setup is not counted."""
    with (
        patch.object(AgentccOrgConfig.no_workspace_objects, "filter") as org_configs,
        patch.object(
            AgentccProviderCredential.no_workspace_objects, "filter"
        ) as providers,
    ):
        yield SimpleNamespace(org_configs=org_configs, providers=providers)


def _assert_no_tenant_reads(reads):
    reads.org_configs.assert_not_called()
    reads.providers.assert_not_called()


def _other_org_workspace(user, organization, workspace):
    # Use the fixture model classes and the same creation fields as conftest.
    other_org = type(organization).objects.create(name="Other Gateway Organization")
    other_workspace = type(workspace).objects.create(
        name="Other Gateway Workspace",
        organization=other_org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    assert other_workspace.organization_id != organization.id
    return other_workspace


def _dispatch(action, method, payload, *, user, organization, workspace):
    factory = APIRequestFactory()
    request = getattr(factory, method)(
        "/agentcc/gateways/default/", deepcopy(payload), format="json"
    )
    request.organization = organization
    request.workspace = workspace
    force_authenticate(request, user=user)
    kwargs = {} if action == "list" else {"pk": "default"}
    return AgentccGatewayViewSet.as_view({method: action})(request, **kwargs)


def _assert_no_gateway_work(seams):
    seams.get_client.assert_not_called()
    seams.client.submit_batch.assert_not_called()
    assert seams.client.mock_calls == []
    seams.push_all.assert_not_called()
    seams.push_org.assert_not_called()
    seams.cache.set.assert_not_called()
    assert seams.cache.mock_calls == []


def _assert_scope_refusal(response, seams):
    # Equality to the existing response builder verifies both its normal error
    # envelope and the exact value-free message, not just any unrelated 400.
    expected = AgentccGatewayViewSet._gm.bad_request(SCOPE_MESSAGE)
    assert response.status_code == 400
    assert response.data == expected.data
    _assert_no_gateway_work(seams)


def _assert_permission_refusal(response, seams):
    # IR's IsOrgGatewayAdmin answers before the action body: no tenant read,
    # client, cache or push can have happened.
    assert response.status_code == 403
    assert GATEWAY_ADMIN_MESSAGE in str(response.data)
    _assert_no_gateway_work(seams)


def _assert_tenant_free_read(action, response, seams):
    assert response.status_code == 200, response.data
    seams.get_client.assert_called_once_with()
    expected = ["health_check"]
    if action == "config":
        expected.append("get_config")
    assert [name for name, _, _ in seams.client.mock_calls] == expected
    seams.push_all.assert_not_called()
    seams.push_org.assert_not_called()
    # Not even the per-org last-health-check record is read without an org.
    assert seams.cache.mock_calls == []
    result = response.data["result"]
    if action == "list":
        (result,) = result
    assert result["last_health_check"] is None
    assert result["last_health_check_status"] is None
    if action == "config":
        assert result["providers"] == {}
    else:
        assert "provider_count" not in result
        assert "model_count" not in result


@pytest.mark.django_db
def test_submit_batch_orgless_refuses_before_gateway(user, gateway_seams):
    with _spy_tenant_reads() as reads:
        response = _dispatch(
            "submit_batch",
            "post",
            {"requests": BATCH_REQUESTS, "max_concurrency": 7},
            user=user,
            organization=None,
            workspace=None,
        )
    _assert_permission_refusal(response, gateway_seams)
    _assert_no_tenant_reads(reads)


@pytest.mark.django_db
def test_submit_batch_without_workspace_follows_ir_availability(
    user, organization, gateway_seams
):
    # An organization-level request (IR allows no workspace landing) is not a
    # scope failure; with delegated batches off it gets IR's 409 and still no
    # client, cache ownership entry or push.
    with patch(
        "agentcc.views.gateway.delegated_enabled", return_value=False
    ) as enabled:
        response = _dispatch(
            "submit_batch",
            "post",
            {"requests": BATCH_REQUESTS, "max_concurrency": 7},
            user=user,
            organization=organization,
            workspace=None,
        )
    assert response.status_code == 409
    assert response.data == unavailable_response(batch=True).data
    enabled.assert_called_once_with(batch=True)
    _assert_no_gateway_work(gateway_seams)


@pytest.mark.django_db
def test_submit_batch_cross_org_workspace_refuses_before_gateway(
    user, organization, workspace, gateway_seams
):
    other_workspace = _other_org_workspace(user, organization, workspace)
    response = _dispatch(
        "submit_batch",
        "post",
        {"requests": BATCH_REQUESTS, "max_concurrency": 7},
        user=user,
        organization=organization,
        workspace=other_workspace,
    )
    _assert_scope_refusal(response, gateway_seams)


@pytest.mark.django_db
@pytest.mark.parametrize(("action", "method", "payload"), GATEWAY_ACTIONS)
def test_gateway_actions_require_org_and_workspace(
    action, method, payload, user, gateway_seams
):
    # Without an organization: admin actions get IR's permission refusal, the
    # virtual-gateway reads answer only the gateway's own state, and every
    # other action gets the scope refusal. None reads a tenant table.
    with _spy_tenant_reads() as reads:
        response = _dispatch(
            action,
            method,
            payload,
            user=user,
            organization=None,
            workspace=None,
        )
    if action in ADMIN_ACTIONS:
        _assert_permission_refusal(response, gateway_seams)
    elif action in ORGLESS_READS:
        _assert_tenant_free_read(action, response, gateway_seams)
    else:
        _assert_scope_refusal(response, gateway_seams)
    _assert_no_tenant_reads(reads)


@pytest.mark.django_db
@pytest.mark.parametrize(("action", "method", "payload"), GATEWAY_ACTIONS)
def test_gateway_actions_refuse_a_workspace_from_another_org(
    action, method, payload, user, organization, workspace, gateway_seams
):
    # The Owner's own organization passes IsOrgGatewayAdmin; the scope guard
    # then refuses the foreign workspace before any tenant read or gateway work.
    other_workspace = _other_org_workspace(user, organization, workspace)
    with _spy_tenant_reads() as reads:
        response = _dispatch(
            action,
            method,
            payload,
            user=user,
            organization=organization,
            workspace=other_workspace,
        )
    _assert_scope_refusal(response, gateway_seams)
    _assert_no_tenant_reads(reads)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("id_field", "max_concurrency"),
    [
        pytest.param("batch_id", 7, id="batch_id-explicit-concurrency"),
        pytest.param("id", 7, id="id-explicit-concurrency"),
        pytest.param("batch_id", None, id="batch_id-default-concurrency"),
        pytest.param("id", None, id="id-default-concurrency"),
    ],
)
def test_submit_batch_authorized_preserves_forwarding_and_ownership(
    id_field, max_concurrency, user, organization, workspace, gateway_seams
):
    # IR submits through a person grant (services/delegated_batches.py), never
    # the admin bearer: the caller's own org, person and workspace are named.
    assert workspace.organization_id == organization.id
    payload = {"requests": deepcopy(BATCH_REQUESTS)}
    if max_concurrency is not None:
        payload["max_concurrency"] = max_concurrency
    result = {id_field: BATCH_ID, "status": "pending"}

    with (
        patch("agentcc.views.gateway.delegated_enabled", return_value=True) as enabled,
        patch(
            "agentcc.services.delegated_batches.submit", return_value=result
        ) as submit,
    ):
        response = _dispatch(
            "submit_batch",
            "post",
            payload,
            user=user,
            organization=organization,
            workspace=workspace,
        )

    assert response.status_code == 200, response.data
    assert response.data["result"] == result
    enabled.assert_called_once_with(batch=True)
    gateway_seams.get_client.assert_called_once_with()
    submit.assert_called_once_with(
        gateway_seams.client,
        organization=organization,
        user=user,
        workspace=workspace,
        requests=BATCH_REQUESTS,
        max_concurrency=5 if max_concurrency is None else max_concurrency,
    )
    # The delegated helper owns every gateway call; the admin-bearer submit and
    # the legacy cache ownership entry are never used.
    gateway_seams.client.submit_batch.assert_not_called()
    assert gateway_seams.client.mock_calls == []
    assert gateway_seams.cache.mock_calls == []
    gateway_seams.push_all.assert_not_called()
    gateway_seams.push_org.assert_not_called()
