"""Authoring-only regressions for IF-01/02/04/05; expectations in STEP-RECORD.

APIRequestFactory + force_authenticate exercises action contracts, not real
membership authentication. Both organizations/workspaces are persisted; remote
and imported config-push seams are mocked. No test was executed by this packet.

The release line keeps its org-attributed MCP tool test (services/mcp_admin.py
run_tool_test) and its person-scoped batches (services/batch_access.py,
services/delegated_batches.py). The tests below hold those paths to the
same P1 properties the dev refusals had: no other tenant's tool, batch, org id
or cache entry is ever used, scope is refused before any work, and every
failure answer is categorical and value-free.
"""

from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agentcc.models.batch_job import AgentccBatchJob
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.permissions import GATEWAY_ADMIN_MESSAGE
from agentcc.services import mcp_admin
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.delegated_availability import unavailable_response
from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.services.org_config_writer import GATEWAY_SYNC_FAILED_MESSAGE
from agentcc.views.gateway import AgentccGatewayViewSet, log_config_rejected
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db

SCOPE_MESSAGE = "Organization and workspace context are required"
BATCH_NOT_FOUND = "Batch not found"
ERROR_MESSAGE = "Gateway request failed."
gateway_mcp = mcp_admin.gateway_mcp
BATCH_REQUESTS = [{"custom_id": "first", "body": {"model": "test-model"}}]
BATCH_ACTIONS = [
    pytest.param("submit_batch", "post", {"requests": BATCH_REQUESTS}, id="submit"),
    pytest.param("get_batch", "get", {"batch_id": "test-batch"}, id="get"),
    pytest.param("cancel_batch", "post", {"batch_id": "test-batch"}, id="cancel"),
]


@pytest.fixture
def tenants(user, organization, workspace):
    other_org = type(organization).objects.create(name="Gateway R3 Other Organization")
    other_user = type(user).objects.create_user(
        email=f"gateway-r3-{other_org.id}@example.invalid",
        name="Gateway R3 Other User",
        organization=other_org,
        organization_role=user.organization_role,
    )
    other_workspace = type(workspace).objects.create(
        name="Gateway R3 Other Workspace",
        organization=other_org,
        is_default=True,
        is_active=True,
        created_by=other_user,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=other_user,
        organization=other_org,
        defaults={
            "role": user.organization_role,
            "level": Level.ADMIN,
            "is_active": True,
        },
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=other_user,
        workspace=other_workspace,
        defaults={
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "level": Level.WORKSPACE_MEMBER,
            "organization_membership": membership,
            "is_active": True,
        },
    )
    return (
        SimpleNamespace(user=user, organization=organization, workspace=workspace),
        SimpleNamespace(
            user=other_user, organization=other_org, workspace=other_workspace
        ),
    )


@pytest.fixture
def org_configs(tenants):
    return tuple(
        AgentccOrgConfig.no_workspace_objects.create(
            organization=tenant.organization,
            version=1,
            is_active=True,
            deleted=False,
            mcp={"servers": {server: {"enabled": True}}},
            created_by=tenant.user,
        )
        for tenant, server in zip(tenants, ("a-srv", "b-srv"), strict=True)
    )


@pytest.fixture
def seams():
    client = MagicMock(name="gateway_client")
    client.health_check.return_value = {"status": "ok"}
    client.get_org_config.return_value = {}
    client.mcp_tools.return_value = [{"name": "shared", "server": "b-srv"}]
    client.mcp_test_tool.return_value = {"content": [], "isError": False}
    client.submit_batch.return_value = {"batch_id": "test-batch", "status": "pending"}
    client.get_batch.return_value = {"batch_id": "test-batch", "status": "pending"}
    client.cancel_batch.return_value = {"batch_id": "test-batch", "status": "cancelled"}
    with (
        patch(
            "agentcc.views.gateway.get_gateway_client", return_value=client
        ) as factory,
        patch(
            "agentcc.views.gateway.push_all_org_configs", return_value=True
        ) as push_all,
        patch("agentcc.views.gateway.push_org_config", return_value=True) as push_org,
        patch("agentcc.views.gateway.cache") as cache,
        patch("agentcc.views.gateway.logger") as logger,
    ):
        # A small cache double preserves the independent IR health-history path.
        records = {}
        cache.get.side_effect = lambda key: records.get(key, cache.get.return_value)
        cache.get.return_value = None
        cache.set.side_effect = lambda key, value, *args, **kwargs: records.__setitem__(
            key, value
        )
        yield SimpleNamespace(
            client=client,
            factory=factory,
            push_all=push_all,
            push_org=push_org,
            cache=cache,
            logger=logger,
        )


def _dispatch(
    action,
    method,
    payload,
    tenant,
    *,
    organization=None,
    workspace=None,
    override_scope=False,
):
    request = getattr(APIRequestFactory(), method)(
        "/agentcc/gateways/default/", deepcopy(payload), format="json"
    )
    request.organization = organization if override_scope else tenant.organization
    request.workspace = workspace if override_scope else tenant.workspace
    force_authenticate(request, user=tenant.user)
    kwargs = {} if action in ("list", "protect_templates") else {"pk": "default"}
    return AgentccGatewayViewSet.as_view({method: action})(request, **kwargs)


def _assert_error(response, message, status=400):
    builder = (
        AgentccGatewayViewSet._gm.not_found
        if status == 404
        else AgentccGatewayViewSet._gm.bad_request
    )
    assert response.status_code == status
    assert response.data == builder(message).data


def _assert_no_effects(seams):
    seams.factory.assert_not_called()
    assert seams.client.mock_calls == []
    seams.push_all.assert_not_called()
    seams.push_org.assert_not_called()
    assert seams.cache.mock_calls == []


def _assert_permission_refusal(response):
    # IR's IsOrgGatewayAdmin answers in DRF initial(), before the action body.
    assert response.status_code == 403
    assert GATEWAY_ADMIN_MESSAGE in str(response.data)


def _assert_batch_answer(action, response):
    """IR's own answers for a batch nobody may touch: the disabled-feature 409
    for a submit, the same 404 for any batch this caller may not read."""
    if action == "submit_batch":
        assert response.status_code == 409
        assert response.data == unavailable_response(batch=True).data
    else:
        _assert_error(response, BATCH_NOT_FOUND, 404)


def _mcp_gateway(status=None, tools=None, run=None):
    """mcp_admin's real adapter (gateway_admin/mcp.py) over a recording
    transport seam: every MCP admin call is kept with its params and body."""
    calls = []

    def call(method, path, *, params=None, json=None):
        calls.append((method, path, deepcopy(params), deepcopy(json)))
        if (method, path) == ("GET", gateway_mcp.STATUS_PATH):
            return _mcp_status_wire() if status is None else deepcopy(status)
        if (method, path) == ("GET", gateway_mcp.TOOLS_PATH):
            return [] if tools is None else deepcopy(tools)
        if (method, path) == ("POST", gateway_mcp.TEST_PATH):
            if isinstance(run, Exception):
                raise run
            return deepcopy(run)
        raise AssertionError("unexpected MCP admin call")

    return calls, patch.object(gateway_mcp, "_call", side_effect=call)


def _mcp_paths(calls):
    return [(method, path) for method, path, _, _ in calls]


def _assert_only_caller_org(calls, tenant, other):
    for _, _, params, body in calls:
        named = {(params or {}).get("org_id"), (body or {}).get("org_id")} - {None}
        assert named == {str(tenant.organization.id)}
    assert str(other.organization.id) not in repr(calls)


def _assert_tool_not_found(response):
    # One shared answer for another org's tool and an unknown one (mcp_admin).
    assert response.status_code == 404
    assert (
        response.data
        == AgentccGatewayViewSet._gm.custom_error_response(
            404,
            mcp_admin.MCP_TOOL_NOT_FOUND_MESSAGE,
            code=mcp_admin.MCP_TOOL_NOT_FOUND,
        ).data
    )


def _upstream_canaries(tenants):
    # Construct a fake token at runtime; never use a real credential fixture.
    token = "sk" + "-" + ("canary" * 8)
    foreign_org = str(tenants[0].organization.id)
    url = "https://upstream.invalid/admin"
    return (token, foreign_org, url)


def _assert_sanitized(response, logger, canaries):
    observed = repr(response.data) + repr(logger.mock_calls)
    for canary in canaries:
        assert canary not in observed
    for log_call in logger.mock_calls:
        assert log_call[0] in {"warning", "debug", "info"}
        assert len(log_call.args) == 1
        assert isinstance(log_call.args[0], str)
        assert set(log_call.kwargs) <= {"exception_type", "code"}
        assert log_call.kwargs.get("code") in {
            None,
            "upstream_4xx",
            "upstream_5xx",
            "upstream_other",
            "gateway_error",
            "internal_error",
            "sync_failed",
        }


@pytest.mark.parametrize(
    "tools",
    [
        # The PIN gateway's ``server`` attribution is not provably the org's.
        pytest.param([{"name": "shared", "server": "a-srv"}], id="foreign-server"),
        pytest.param(
            [{"name": "shared", "server_id": "a-srv"}], id="foreign-server-id"
        ),
        pytest.param([{"name": "other", "server_id": "b-srv"}], id="unknown-name"),
        pytest.param(
            [
                {"name": "shared", "server_id": "a-srv"},
                {"name": "shared", "server_id": "b-srv"},
            ],
            id="first-match-foreign",
        ),
        pytest.param(
            [
                {"name": "shared", "server": "a-srv"},
                {"name": "shared", "server": "b-srv"},
            ],
            id="ambiguous-global-name",
        ),
        pytest.param([{"name": "shared"}], id="missing-server"),
        pytest.param([{"name": "shared", "server": ["b-srv"]}], id="invalid-server"),
        pytest.param({"tools": [{"name": "shared", "server": "b-srv"}]}, id="non-list"),
        pytest.param([None, "shared", {}], id="malformed-entries"),
    ],
)
def test_mcp_refuses_unowned_unknown_or_ambiguous_tools(
    tools, tenants, org_configs, seams
):
    calls, transport = _mcp_gateway(tools=tools)
    with transport:
        response = _dispatch(
            "test_mcp_tool", "post", {"name": "shared", "arguments": {}}, tenants[1]
        )
    _assert_tool_not_found(response)
    # Attribution is read; nothing is run.
    assert _mcp_paths(calls) == [
        ("GET", gateway_mcp.STATUS_PATH),
        ("GET", gateway_mcp.TOOLS_PATH),
    ]
    _assert_only_caller_org(calls, tenants[1], tenants[0])
    _assert_no_effects(seams)


def test_mcp_runs_only_the_callers_own_tool(tenants, org_configs, seams):
    """Control: the caller's own tool (listed twice) runs once, naming only the
    caller's org, and its result is shown."""
    result = {"content": [{"type": "text", "text": "own result"}], "is_error": False}
    arguments = {"query": "unchanged", "nested": {"limit": 3}, "items": [1, False]}
    calls, transport = _mcp_gateway(
        tools=[
            {"name": "shared", "server_id": "b-srv"},
            {"name": "shared", "server_id": "b-srv"},
        ],
        run=result,
    )
    with transport:
        response = _dispatch(
            "test_mcp_tool",
            "post",
            {"name": "shared", "arguments": arguments},
            tenants[1],
        )
    assert response.status_code == 200
    assert response.data["result"] == result
    assert _mcp_paths(calls) == [
        ("GET", gateway_mcp.STATUS_PATH),
        ("GET", gateway_mcp.TOOLS_PATH),
        ("POST", gateway_mcp.TEST_PATH),
    ]
    assert calls[-1][3] == {
        "org_id": str(tenants[1].organization.id),
        "name": "shared",
        "arguments": arguments,
    }
    _assert_only_caller_org(calls, tenants[1], tenants[0])
    _assert_no_effects(seams)


def test_mcp_without_config_refuses_before_client(tenants, seams):
    calls, transport = _mcp_gateway()
    with transport:
        response = _dispatch(
            "test_mcp_tool", "post", {"name": "shared", "arguments": {}}, tenants[1]
        )
    _assert_error(response, mcp_admin.NO_MCP_SERVERS_MESSAGE)
    assert calls == []
    _assert_no_effects(seams)


def _share_server_id(org_configs):
    for config in org_configs:
        config.mcp = {"servers": {"srv-shared": {"enabled": True}}}
        config.save()


@pytest.mark.parametrize("index", [0, 1], ids=["org-a", "org-b"])
def test_mcp_shared_server_id_runs_only_in_the_callers_pool(
    index, tenants, org_configs, seams
):
    """Both orgs name a server srv-shared. On an org-scoped gateway each run
    names only the caller's org, so the other org's pool is never asked."""
    _share_server_id(org_configs)
    tenant, other = tenants[index], tenants[1 - index]
    result = {"content": [], "is_error": False}
    arguments = {"query": "unchanged", "nested": {"limit": 3}, "items": [1, False]}
    calls, transport = _mcp_gateway(
        tools=[{"name": "shared", "server_id": "srv-shared"}], run=result
    )
    with transport:
        response = _dispatch(
            "test_mcp_tool",
            "post",
            {"name": "shared", "arguments": arguments},
            tenant,
        )
    assert response.status_code == 200
    assert response.data["result"] == result
    assert calls[-1][:2] == ("POST", gateway_mcp.TEST_PATH)
    assert calls[-1][3] == {
        "org_id": str(tenant.organization.id),
        "name": "shared",
        "arguments": arguments,
    }
    _assert_only_caller_org(calls, tenant, other)
    _assert_no_effects(seams)


@pytest.mark.parametrize("index", [0, 1], ids=["org-a", "org-b"])
def test_mcp_shared_registry_runs_nothing(index, tenants, org_configs, seams):
    """A status that is not org-scoped (one registry for every org) cannot
    attribute srv-shared to either org: no tool list is read, nothing runs."""
    _share_server_id(org_configs)
    tenant, other = tenants[index], tenants[1 - index]
    shared_status = {
        "enabled": True,
        "sessions": 1,
        "servers": [{"server_id": "srv-shared", "healthy": True}],
    }
    calls, transport = _mcp_gateway(
        status=shared_status, tools=[{"name": "shared", "server_id": "srv-shared"}]
    )
    with transport:
        response = _dispatch(
            "test_mcp_tool", "post", {"name": "shared", "arguments": {}}, tenant
        )
    _assert_error(response, mcp_admin.NO_MCP_SERVERS_MESSAGE)
    assert _mcp_paths(calls) == [("GET", gateway_mcp.STATUS_PATH)]
    _assert_only_caller_org(calls, tenant, other)
    _assert_no_effects(seams)


def test_mcp_scope_refusal_precedes_org_resolution_and_config_lookup(tenants, seams):
    calls, transport = _mcp_gateway()
    with (
        transport,
        patch.object(AgentccGatewayViewSet, "_current_org") as resolve_org,
        patch.object(AgentccOrgConfig.no_workspace_objects, "filter") as lookup,
    ):
        # The caller's own org passes IsOrgGatewayAdmin; its workspace does not
        # belong to that org.
        response = _dispatch(
            "test_mcp_tool",
            "post",
            {"name": "shared", "arguments": {}},
            tenants[1],
            organization=tenants[1].organization,
            workspace=tenants[0].workspace,
            override_scope=True,
        )
    _assert_error(response, SCOPE_MESSAGE)
    resolve_org.assert_not_called()
    lookup.assert_not_called()
    assert calls == []
    _assert_no_effects(seams)


@pytest.mark.parametrize(("action", "method", "payload"), BATCH_ACTIONS)
def test_batches_never_use_legacy_cache_ownership(
    action, method, payload, tenants, seams
):
    for tenant in tenants:
        # Even a matching legacy ownership entry must not authorize anything:
        # IR reads batch ownership from its job and grant rows, never this cache.
        seams.cache.get.return_value = str(tenant.organization.id)
        with patch("agentcc.views.gateway.delegated_enabled", return_value=False):
            response = _dispatch(action, method, payload, tenant)
        _assert_batch_answer(action, response)
        _assert_no_effects(seams)


@pytest.mark.parametrize(("action", "method", "payload"), BATCH_ACTIONS[1:])
def test_batch_of_another_org_is_not_found(action, method, payload, tenants, seams):
    AgentccBatchJob.objects.create(
        organization=tenants[0].organization,
        created_by=tenants[0].user,
        gateway_batch_id=payload["batch_id"],
        gateway_key_id="",
        total=1,
    )
    response = _dispatch(action, method, payload, tenants[1])
    _assert_error(response, BATCH_NOT_FOUND, 404)
    _assert_no_effects(seams)


@pytest.mark.parametrize(("action", "method", "payload"), BATCH_ACTIONS)
@pytest.mark.parametrize("scope", ["absent", "no-workspace", "foreign-workspace"])
def test_batch_scope_guard_still_precedes_refusal(
    action, method, payload, scope, tenants, seams
):
    organization = None if scope == "absent" else tenants[0].organization
    workspace = tenants[1].workspace if scope == "foreign-workspace" else None
    with patch("agentcc.views.gateway.delegated_enabled", return_value=False):
        response = _dispatch(
            action,
            method,
            payload,
            tenants[0],
            organization=organization,
            workspace=workspace,
            override_scope=True,
        )
    if scope == "absent" and action in AgentccGatewayViewSet.gateway_admin_actions:
        _assert_permission_refusal(response)
    elif scope == "no-workspace":
        # Organization-level (IR keeps these available without a workspace
        # landing): IR's own answers, still with no gateway or cache work.
        _assert_batch_answer(action, response)
    else:
        _assert_error(response, SCOPE_MESSAGE)
    _assert_no_effects(seams)


@pytest.mark.parametrize(
    ("action", "method", "payload"),
    [
        pytest.param(
            "submit_batch", "post", {"requests": "not-a-list"}, id="submit-type"
        ),
        pytest.param("cancel_batch", "post", {"batch_id": []}, id="cancel-type"),
        pytest.param("get_batch", "get", {}, id="missing-query-id"),
    ],
)
def test_batch_validation_is_not_replaced(action, method, payload, tenants, seams):
    response = _dispatch(action, method, payload, tenants[0])
    assert response.status_code == 400
    if action == "get_batch":
        _assert_error(response, "batch_id query param is required")
    seams.client.submit_batch.assert_not_called()
    seams.client.get_batch.assert_not_called()
    seams.client.cancel_batch.assert_not_called()
    assert seams.cache.mock_calls == []


def _cache_calls(mock, namespace):
    return [
        c for c in mock.call_args_list if c.args[0].startswith(f"agentcc:{namespace}:")
    ]


def _assert_health_history(response, seams, tenant):
    key = f"agentcc:gateway_last_health_check:{tenant.organization.id}"
    result = response.data["result"]
    assert result["last_health_check_status"] == "healthy"
    assert datetime.fromisoformat(result["last_health_check"]).tzinfo is not None
    writes = _cache_calls(seams.cache.set, "gateway_last_health_check")
    assert len(writes) == 1
    assert writes[0].args == (
        key,
        {"at": result["last_health_check"], "status": "healthy"},
    )
    assert writes[0].kwargs == {"timeout": None}
    assert [
        c.args for c in _cache_calls(seams.cache.get, "gateway_last_health_check")
    ] == [(key,)]


@pytest.mark.parametrize("index", [0, 1], ids=["org-a", "org-b"])
def test_health_syncs_only_caller_org(index, tenants, org_configs, seams):
    tenant = tenants[index]
    response = _dispatch("health_check", "post", {}, tenant)
    assert response.status_code == 200
    result = response.data["result"]
    assert result == {
        "status": "healthy",
        "health": {"status": "ok"},
        "providers": {"providers": []},
        "provider_count": 0,
        "model_count": 0,
        "org_configs_pushed": True,
        "last_health_check": result["last_health_check"],
        "last_health_check_status": "healthy",
    }
    assert datetime.fromisoformat(result["last_health_check"]).tzinfo is not None
    seams.client.health_check.assert_called_once_with()
    seams.push_all.assert_not_called()
    seams.push_org.assert_called_once_with(
        str(tenant.organization.id), org_configs[index]
    )
    seams.client.get_org_config.assert_called_once_with(str(tenant.organization.id))
    key = f"agentcc:gateway_config_synced:{tenant.organization.id}"
    assert [c.args for c in _cache_calls(seams.cache.get, "gateway_config_synced")] == [
        (key,)
    ]
    assert [c.args for c in _cache_calls(seams.cache.set, "gateway_config_synced")] == [
        (key, True, 300)
    ]
    _assert_health_history(response, seams, tenant)
    assert str(tenants[1 - index].organization.id) not in repr(seams.cache.mock_calls)


@pytest.mark.parametrize(
    "failure",
    [False, RuntimeError("synthetic sync failure")],
    ids=["false", "exception"],
)
def test_health_failed_org_sync_does_not_mark_synced(
    failure, tenants, org_configs, seams
):
    if isinstance(failure, Exception):
        seams.push_org.side_effect = failure
    else:
        seams.push_org.return_value = failure
    response = _dispatch("health_check", "post", {}, tenants[0])
    assert response.status_code == 200
    assert response.data["result"]["status"] == "healthy"
    seams.push_all.assert_not_called()
    seams.push_org.assert_called_once_with(
        str(tenants[0].organization.id), org_configs[0]
    )
    assert _cache_calls(seams.cache.set, "gateway_config_synced") == []
    assert response.data["result"]["org_configs_pushed"] is False
    _assert_health_history(response, seams, tenants[0])
    seams.client.get_org_config.assert_not_called()


def test_health_uses_only_own_throttle_key(tenants, org_configs, seams):
    seams.cache.get.return_value = True
    response = _dispatch("health_check", "post", {}, tenants[0])
    assert response.status_code == 200
    assert [c.args for c in _cache_calls(seams.cache.get, "gateway_config_synced")] == [
        (f"agentcc:gateway_config_synced:{tenants[0].organization.id}",)
    ]
    assert _cache_calls(seams.cache.set, "gateway_config_synced") == []
    assert response.data["result"]["org_configs_pushed"] is False
    _assert_health_history(response, seams, tenants[0])
    seams.push_all.assert_not_called()
    seams.push_org.assert_not_called()


@pytest.mark.parametrize(
    ("status", "code"),
    [
        pytest.param(502, "upstream_5xx", id="server-error"),
        pytest.param(409, "upstream_4xx", id="client-error"),
        pytest.param(None, "gateway_error", id="no-status"),
        pytest.param("untrusted-status", "gateway_error", id="string-status"),
        pytest.param(True, "gateway_error", id="boolean-status"),
        pytest.param(9999, "gateway_error", id="out-of-range"),
    ],
)
def test_mcp_tools_gateway_errors_are_value_free(
    status, code, tenants, org_configs, seams
):
    canaries = _upstream_canaries(tenants)
    error = GatewayClientError(" | ".join(canaries), status_code=status)
    with (
        patch.object(
            mcp_admin.gateway_mcp, "get_status", return_value=_mcp_status_wire()
        ),
        patch.object(mcp_admin.gateway_mcp, "list_tools", side_effect=error) as read,
    ):
        response = _dispatch("mcp_tools", "get", {}, tenants[1])
    _assert_mcp_unreachable(response)
    seams.factory.assert_not_called()
    read.assert_called_once_with(str(tenants[1].organization.id))
    seams.client.mcp_test_tool.assert_not_called()
    _assert_sanitized(response, seams.logger, canaries)
    seams.logger.warning.assert_called_once_with(
        "mcp_list_gateway_error", exception_type="GatewayClientError", code=code
    )


def test_mcp_test_tool_gateway_error_is_value_free(tenants, org_configs, seams):
    canaries = _upstream_canaries(tenants)
    calls, transport = _mcp_gateway(
        tools=[{"name": "shared", "server_id": "b-srv"}],
        run=GatewayClientError(" | ".join(canaries), status_code=502),
    )
    with transport:
        response = _dispatch(
            "test_mcp_tool", "post", {"name": "shared", "arguments": {}}, tenants[1]
        )
    _assert_error(response, f"{ERROR_MESSAGE} (upstream_5xx)")
    assert _mcp_paths(calls)[-1] == ("POST", gateway_mcp.TEST_PATH)
    _assert_only_caller_org(calls, tenants[1], tenants[0])
    _assert_no_effects(seams)
    _assert_sanitized(response, seams.logger, canaries)
    seams.logger.warning.assert_called_once_with(
        "test_mcp_tool_gateway_error",
        exception_type="GatewayClientError",
        code="upstream_5xx",
    )


def test_mcp_test_tool_reported_error_is_categorical(tenants, org_configs, seams):
    """A run the gateway reports as failed shows its category, not the text."""
    canaries = _upstream_canaries(tenants)
    calls, transport = _mcp_gateway(
        tools=[{"name": "shared", "server_id": "b-srv"}],
        run={
            "is_error": True,
            "content": [{"type": "text", "text": " | ".join(canaries)}],
        },
    )
    with transport:
        response = _dispatch(
            "test_mcp_tool", "post", {"name": "shared", "arguments": {}}, tenants[1]
        )
    assert response.status_code == 200
    assert response.data["result"] == {
        "error": {"message": ERROR_MESSAGE, "code": "gateway_error"}
    }
    assert _mcp_paths(calls)[-1] == ("POST", gateway_mcp.TEST_PATH)
    _assert_no_effects(seams)
    _assert_sanitized(response, seams.logger, canaries)


GENERIC_ERROR_ACTIONS = [
    pytest.param("list", "get", {}, 400, id="list"),
    pytest.param("retrieve", "get", {}, 404, id="retrieve"),
    pytest.param("health_check", "post", {}, 400, id="health"),
    pytest.param("config", "get", {}, 400, id="config"),
    pytest.param("reload", "post", {}, 400, id="reload"),
    pytest.param(
        "update_config", "post", {"cache": {"enabled": False}}, 400, id="update-config"
    ),
    pytest.param(
        "update_provider",
        "post",
        {"name": "test-provider", "config": {"models": ["test-model"]}},
        400,
        id="update-provider",
    ),
    pytest.param(
        "remove_provider", "post", {"name": "test-provider"}, 400, id="remove-provider"
    ),
    pytest.param(
        "toggle_guardrail",
        "post",
        {"name": "pii-detector", "enabled": True},
        400,
        id="toggle-guardrail",
    ),
    pytest.param(
        "update_guardrail",
        "post",
        {"name": "pii-detector", "config": {"enabled": True}},
        400,
        id="update-guardrail",
    ),
    pytest.param(
        "test_playground",
        "post",
        {"prompt": "test", "model": "test-model"},
        400,
        id="playground",
    ),
    pytest.param(
        "set_budget",
        "post",
        {"level": "org_limit", "config": {"max_cost": 1}},
        400,
        id="set-budget",
    ),
    pytest.param(
        "remove_budget", "post", {"level": "org_limit"}, 400, id="remove-budget"
    ),
    pytest.param("mcp_status", "get", {}, 400, id="mcp-status"),
    pytest.param(
        "update_mcp_server",
        "post",
        {"server_id": "b-srv", "config": {"enabled": True}},
        400,
        id="update-mcp-server",
    ),
    pytest.param(
        "remove_mcp_server", "post", {"server_id": "b-srv"}, 400, id="remove-mcp-server"
    ),
    pytest.param(
        "update_mcp_guardrails",
        "post",
        {"config": {"enabled": True}},
        400,
        id="update-mcp-guardrails",
    ),
]


@pytest.mark.parametrize(
    ("action", "method", "payload", "status"), GENERIC_ERROR_ACTIONS
)
def test_generic_action_errors_are_value_free(
    action, method, payload, status, tenants, seams
):
    canaries = _upstream_canaries(tenants)
    with (
        patch("agentcc.views.gateway.delegated_enabled", return_value=True),
        patch.object(
            AgentccGatewayViewSet,
            "_current_org",
            side_effect=RuntimeError(" | ".join(canaries)),
        ),
    ):
        response = _dispatch(action, method, payload, tenants[1])
    message = (
        "Gateway not found" if status == 404 else f"{ERROR_MESSAGE} (internal_error)"
    )
    if action == "update_mcp_server":
        message = "The MCP server could not be saved."
    _assert_error(response, message, status)
    _assert_sanitized(response, seams.logger, canaries)
    assert seams.logger.mock_calls


@pytest.mark.parametrize(
    "action", ["mcp_status", "mcp_tools", "mcp_resources", "mcp_prompts"]
)
def test_mcp_read_fallback_logs_are_value_free(action, tenants, org_configs, seams):
    canaries = _upstream_canaries(tenants)
    with patch.object(
        mcp_admin.gateway_mcp,
        "get_status",
        side_effect=GatewayClientError(" | ".join(canaries), status_code=503),
    ) as read:
        response = _dispatch(action, "get", {}, tenants[1])
    read.assert_called_once_with(str(tenants[1].organization.id))
    seams.factory.assert_not_called()
    if action != "mcp_status":
        _assert_mcp_unreachable(response)
        assert seams.logger.mock_calls
    else:
        assert response.status_code == 200
        result = response.data["result"]
        assert result["enabled"] is False
        assert result["servers"] == []
        assert result["reason"] == mcp_admin.GATEWAY_UNREACHABLE
        assert all(
            result[key] == 0 for key in ("tools", "sessions", "resources", "prompts")
        )
        assert "features" in result
        # Helper-internal diagnostics are gwc-owned, explicitly excluded by R3.
    _assert_sanitized(response, seams.logger, canaries)


@pytest.mark.parametrize("action", ["health_check", "providers"])
def test_health_and_provider_gateway_errors_are_value_free(action, tenants, seams):
    canaries = _upstream_canaries(tenants)
    error = GatewayClientError(" | ".join(canaries), status_code=502)
    with ExitStack() as stack:
        if action == "health_check":
            seams.client.health_check.side_effect = error
        else:
            stats = stack.enter_context(
                patch("agentcc.views.gateway._org_provider_stats", side_effect=error)
            )
        response = _dispatch(
            action, "post" if action == "health_check" else "get", {}, tenants[1]
        )
    if action == "providers":
        stats.assert_called_once_with(tenants[1].organization)
        seams.factory.assert_not_called()
        assert seams.client.mock_calls == []
    else:
        writes = _cache_calls(seams.cache.set, "gateway_last_health_check")
        assert len(writes) == 1
        assert (
            writes[0].args[0]
            == f"agentcc:gateway_last_health_check:{tenants[1].organization.id}"
        )
        assert writes[0].args[1]["status"] == "unreachable"
        assert _cache_calls(seams.cache.set, "gateway_config_synced") == []
    expected = f"{ERROR_MESSAGE} (upstream_5xx)"
    if action == "health_check":
        assert response.status_code == 400
        assert (
            response.data
            == AgentccGatewayViewSet._gm.bad_request(
                {
                    "status": "unreachable",
                    "error": expected,
                }
            ).data
        )
    else:
        _assert_error(response, expected)
    _assert_sanitized(response, seams.logger, canaries)
    assert seams.logger.mock_calls


@pytest.mark.parametrize("helper", ["ensure", "delegation", "push"])
def test_private_helper_logs_are_value_free(helper, tenants, org_configs, seams):
    canaries = _upstream_canaries(tenants)
    error = GatewayClientError(" | ".join(canaries), status_code=502)
    view = AgentccGatewayViewSet()
    if helper == "ensure":
        seams.client.get_org_config.side_effect = error
        assert (
            view._ensure_org_config_pushed(
                seams.client, str(tenants[1].organization.id)
            )
            is False
        )
    elif helper == "delegation":
        with (
            patch("agentcc.views.gateway.delegated_enabled", return_value=True),
            patch("agentcc.views.gateway.for_chat", side_effect=error) as delegate,
        ):
            response = _dispatch(
                "test_playground", "post", PLAYGROUND_PAYLOAD, tenants[1]
            )
        delegate.assert_called_once()
        seams.client.send_delegated_chat_completion.assert_not_called()
        _assert_error(response, f"{ERROR_MESSAGE} (upstream_5xx)")
        _assert_sanitized(response, seams.logger, canaries)
    else:
        seams.push_org.side_effect = error
        assert (
            view._push_current_config(tenants[1].organization, org_configs[1]) is False
        )
    response = AgentccGatewayViewSet._gm.bad_request(ERROR_MESSAGE)
    _assert_sanitized(response, seams.logger, canaries)
    assert seams.logger.mock_calls


def test_playground_delegated_errors_are_value_free(tenants, org_configs, seams):
    canaries = _upstream_canaries(tenants)
    delegation = SimpleNamespace(token=canaries[0])
    seams.client.send_delegated_chat_completion.side_effect = GatewayClientError(
        " | ".join(canaries), status_code=502
    )
    with (
        patch("agentcc.views.gateway.delegated_enabled", return_value=True),
        patch("agentcc.views.gateway.for_chat", return_value=delegation) as delegate,
    ):
        response = _dispatch("test_playground", "post", PLAYGROUND_PAYLOAD, tenants[1])
    _assert_error(response, f"{ERROR_MESSAGE} (upstream_5xx)")
    delegate.assert_called_once()
    seams.client.send_delegated_chat_completion.assert_called_once_with(
        delegation, prompt="test", model="test-model", system_prompt=None, params={}
    )
    seams.client.create_key.assert_not_called()
    seams.client.revoke_key.assert_not_called()
    _assert_sanitized(response, seams.logger, canaries)
    assert seams.logger.mock_calls


PLAYGROUND_PAYLOAD = {"prompt": "test", "model": "test-model"}


def _playground(seams, tenant, status, body, headers):
    seams.client.send_delegated_chat_completion.return_value = (status, body, headers)
    delegation = SimpleNamespace(token="-".join(("synthetic", "delegation")))
    with (
        patch("agentcc.views.gateway.delegated_enabled", return_value=True),
        patch("agentcc.views.gateway.for_chat", return_value=delegation) as delegate,
        patch.object(
            AgentccGatewayViewSet, "_ensure_org_config_pushed", return_value=True
        ),
    ):
        response = _dispatch("test_playground", "post", PLAYGROUND_PAYLOAD, tenant)
    delegate.assert_called_once()
    assert seams.client.send_delegated_chat_completion.call_args.args == (delegation,)
    seams.client.create_key.assert_not_called()
    seams.client.revoke_key.assert_not_called()
    return response


@pytest.mark.parametrize(
    ("status", "code"),
    [
        pytest.param(502, "upstream_5xx", id="502"),
        pytest.param(500, "upstream_5xx", id="500"),
        pytest.param(401, "upstream_4xx", id="401"),
        pytest.param(403, "upstream_4xx", id="403-without-guardrail-header"),
        pytest.param(302, "upstream_other", id="302"),
        pytest.param(304, "upstream_other", id="304"),
        pytest.param(101, "upstream_other", id="101"),
    ],
)
def test_playground_upstream_failure_bodies_are_value_free(
    status, code, tenants, org_configs, seams
):
    """Lead slice r1 (TF-01): a non-raising upstream failure tuple never reaches the caller."""
    canaries = _upstream_canaries(tenants)
    response = _playground(
        seams, tenants[1], status, {"error": {"message": " | ".join(canaries)}}, {}
    )
    assert response.status_code == 200
    result = response.data["result"]
    assert result["status_code"] == status
    assert result["body"] == {"error": {"message": ERROR_MESSAGE, "code": code}}
    _assert_sanitized(response, seams.logger, canaries)


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        pytest.param(200, {}, id="success"),
        pytest.param(446, {}, id="guardrail-446"),
        pytest.param(
            403, {"X-Agentcc-Guardrail-Triggered": "true"}, id="guardrail-403"
        ),
    ],
)
def test_playground_success_and_guardrail_bodies_are_shown(
    status, headers, tenants, org_configs, seams
):
    """Controls: the caller's own completion and gateway guardrail blocks stay visible."""
    body = {"choices": [{"message": {"content": "visible to the caller"}}]}
    response = _playground(seams, tenants[1], status, body, headers)
    assert response.status_code == 200
    assert response.data["result"]["body"] == body


@pytest.mark.parametrize(
    "action", ["mcp_status", "mcp_tools", "mcp_resources", "mcp_prompts"]
)
@pytest.mark.parametrize(
    "wire_shape", ["json-rpc", "transport", "rpc-content", "entry-error"]
)
def test_mcp_read_http_200_errors_are_value_free(
    action, wire_shape, tenants, org_configs, seams
):
    import httpx

    canaries = _upstream_canaries(tenants)
    diagnostic = " | ".join(canaries)
    if wire_shape == "json-rpc":
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32603, "message": diagnostic},
        }
    elif wire_shape == "transport":
        body = {"is_error": True, "error": diagnostic}
    elif wire_shape == "rpc-content":
        body = {"is_error": True, "content": [{"type": "text", "text": diagnostic}]}
    else:
        entry = {"id": "b-srv", "server": "b-srv", "error": diagnostic}
        body = (
            {"enabled": True, "servers": [entry]} if action == "mcp_status" else [entry]
        )

    # Exercise actual HTTP-200 decoding at the IR helper/view return boundary.
    # Separate tests below execute the real org_status/_org_items orchestration.
    client = GatewayClient("https://gateway.example.invalid")
    path = "/-/mcp/" + action.removeprefix("mcp_")
    helper = "org_" + action.removeprefix("mcp_")

    def decoded(organization):
        return client._request("GET", path, params={"org_id": str(organization.id)})

    with (
        patch("agentcc.services.gateway_client.httpx.Client") as transport,
        patch.object(mcp_admin, helper, side_effect=decoded) as read,
    ):
        request = transport.return_value.__enter__.return_value.request
        request.return_value = httpx.Response(200, json=body)
        response = _dispatch(action, "get", {}, tenants[1])
    if action == "mcp_status":
        assert response.status_code == 200
        assert response.data["result"] == {
            "error": {"message": ERROR_MESSAGE, "code": "gateway_error"}
        }
    else:
        _assert_mcp_unreachable(response)
    seams.factory.assert_not_called()
    read.assert_called_once_with(tenants[1].organization)
    request.assert_called_once()
    assert request.call_args.args == ("GET", "https://gateway.example.invalid" + path)
    assert request.call_args.kwargs["params"] == {
        "org_id": str(tenants[1].organization.id)
    }
    _assert_sanitized(response, seams.logger, canaries)


CONFIG_REJECTION_ACTIONS = [
    pytest.param(
        "update_config", {"routing": {"strategy": "round_robin"}}, id="update-config"
    ),
    pytest.param(
        "toggle_guardrail",
        {"name": "pii-detector", "enabled": True},
        id="toggle-guardrail",
    ),
    pytest.param(
        "update_guardrail",
        {"name": "pii-detector", "config": {"enabled": True}},
        id="update-guardrail",
    ),
    pytest.param(
        "set_budget", {"level": "org_limit", "config": {"max_cost": 1}}, id="set-budget"
    ),
    pytest.param("remove_budget", {"level": "org_limit"}, id="remove-budget"),
    pytest.param(
        "update_mcp_server",
        {"server_id": "b-srv", "config": {"enabled": True}},
        id="update-mcp-server",
    ),
    pytest.param("remove_mcp_server", {"server_id": "b-srv"}, id="remove-mcp-server"),
    pytest.param(
        "update_mcp_guardrails",
        {"config": {"enabled": True}},
        id="update-mcp-guardrails",
    ),
]


@pytest.mark.parametrize("action,payload", CONFIG_REJECTION_ACTIONS)
def test_org_config_rejection_is_local_detail_not_upstream_text(
    action, payload, tenants, seams
):
    message = "The gateway cannot accept this config. routing.default_strategy: Extra inputs are not permitted"
    error = OrgConfigRejected(message)
    with ExitStack() as stack:
        if action == "update_config":
            update = stack.enter_context(
                patch("agentcc.views.gateway.write_config_version", side_effect=error)
            )
        else:
            update = stack.enter_context(
                patch.object(
                    AgentccGatewayViewSet, "_update_org_config", side_effect=error
                )
            )
        rejected = stack.enter_context(
            patch(
                "agentcc.views.gateway.log_config_rejected", wraps=log_config_rejected
            )
        )
        response = _dispatch(action, "post", payload, tenants[1])
    _assert_error(response, message)
    update.assert_called_once()
    rejected.assert_called_once_with(
        action + "_rejected", tenants[1].organization, error
    )
    assert message not in repr(seams.logger.mock_calls)
    _assert_no_effects(seams)


def test_update_config_rejected_routing_keeps_active_version(tenants, seams):
    tenant = tenants[1]
    config = AgentccOrgConfig.no_workspace_objects.create(
        organization=tenant.organization,
        version=1,
        is_active=True,
        routing={"strategy": "round_robin"},
        created_by=tenant.user,
    )
    with patch(
        "agentcc.views.gateway.log_config_rejected", wraps=log_config_rejected
    ) as rejected:
        response = _dispatch(
            "update_config",
            "post",
            {"routing": {"default_strategy": "round-robin"}},
            tenant,
        )
    assert response.status_code == 400
    assert "routing.default_strategy" in response.data["detail"]
    config.refresh_from_db()
    assert config.is_active is True
    assert config.routing == {"strategy": "round_robin"}
    assert list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=tenant.organization)
        .order_by("version")
        .values_list("version", "is_active")
    ) == [(1, True)]
    seams.push_org.assert_not_called()
    rejected.assert_called_once()
    assert rejected.call_args.args[:2] == (
        "update_config_rejected",
        tenant.organization,
    )
    assert isinstance(rejected.call_args.args[2], OrgConfigRejected)
    assert "routing.default_strategy" not in repr(seams.logger.mock_calls)


def test_update_config_upstream_push_failure_stays_value_free(tenants, seams):
    canaries = _upstream_canaries(tenants)
    seams.push_org.side_effect = GatewayClientError(
        " | ".join(canaries), status_code=502
    )
    response = _dispatch(
        "update_config", "post", {"routing": {"strategy": "round_robin"}}, tenants[1]
    )
    assert response.status_code == 200
    assert response.data["result"]["gateway_synced"] is False
    # IR's own sync warning (org_config_writer), not the dev text.
    assert response.data["result"]["gateway_warning"] == GATEWAY_SYNC_FAILED_MESSAGE
    seams.push_org.assert_called_once()
    _assert_sanitized(response, seams.logger, canaries)
    seams.logger.warning.assert_called_once_with(
        "config_push_failed", exception_type="GatewayClientError", code="upstream_5xx"
    )


def _mcp_status_wire():
    return {
        "enabled": True,
        "org_enabled": True,
        "sessions": 1,
        "servers": [{"server_id": "b-srv", "healthy": True, "tool_count": 1}],
    }


def _assert_mcp_unreachable(response):
    assert response.status_code == 502
    assert (
        response.data
        == AgentccGatewayViewSet._gm.custom_error_response(
            502,
            mcp_admin.GATEWAY_UNREACHABLE_MESSAGE,
            code=mcp_admin.GATEWAY_UNREACHABLE,
        ).data
    )


@pytest.mark.parametrize("action", ["mcp_tools", "mcp_resources", "mcp_prompts"])
def test_ir_mcp_disabled_keeps_404_without_diagnostic(
    action, tenants, org_configs, seams
):
    canaries = _upstream_canaries(tenants)
    with patch.object(
        mcp_admin.gateway_mcp,
        "get_status",
        side_effect=GatewayClientError(" | ".join(canaries), status_code=404),
    ) as read:
        response = _dispatch(action, "get", {}, tenants[1])
    assert response.status_code == 404
    assert (
        response.data
        == AgentccGatewayViewSet._gm.custom_error_response(
            404,
            mcp_admin.GATEWAY_MCP_DISABLED_MESSAGE,
            code=mcp_admin.GATEWAY_MCP_DISABLED,
        ).data
    )
    read.assert_called_once_with(str(tenants[1].organization.id))
    seams.factory.assert_not_called()
    _assert_sanitized(response, seams.logger, canaries)


@pytest.mark.parametrize("fetch_name", ["list_resources", "list_prompts"])
def test_ir_mcp_count_failures_keep_value_free_view_response(
    fetch_name, tenants, org_configs, seams
):
    canaries = _upstream_canaries(tenants)
    with (
        patch.object(
            mcp_admin.gateway_mcp, "get_status", return_value=_mcp_status_wire()
        ) as status,
        patch.object(mcp_admin.gateway_mcp, "list_resources", return_value=[]),
        patch.object(mcp_admin.gateway_mcp, "list_prompts", return_value=[]),
        patch.object(
            mcp_admin.gateway_mcp,
            fetch_name,
            side_effect=GatewayClientError(" | ".join(canaries), status_code=502),
        ) as fetch,
    ):
        # The read-only helper logs fetch.__name__; a mock must model that attribute.
        fetch.__name__ = fetch_name
        response = _dispatch("mcp_status", "get", {}, tenants[1])
    assert response.status_code == 200
    result = response.data["result"]
    assert result["enabled"] is True
    assert result["resources"] == result["prompts"] == 0
    assert result["tools"] == result["sessions"] == 1
    assert result["servers"] == _mcp_status_wire()["servers"]
    status.assert_called_once_with(str(tenants[1].organization.id))
    fetch.assert_called_once_with(str(tenants[1].organization.id))
    seams.factory.assert_not_called()
    _assert_sanitized(response, seams.logger, canaries)
    # mcp_admin:239's internal log is an explicit gwc-owned residual, not hidden.


def test_ir_health_non_admin_records_history_without_sync(tenants, org_configs, seams):
    with patch("agentcc.views.gateway.is_gateway_admin", return_value=False) as admin:
        response = _dispatch("health_check", "post", {}, tenants[0])
    assert response.status_code == 200
    assert response.data["result"]["status"] == "healthy"
    assert response.data["result"]["org_configs_pushed"] is False
    admin.assert_called_once()
    _assert_health_history(response, seams, tenants[0])
    assert _cache_calls(seams.cache.get, "gateway_config_synced") == []
    assert _cache_calls(seams.cache.set, "gateway_config_synced") == []
    seams.push_all.assert_not_called()
    seams.push_org.assert_not_called()


@pytest.mark.parametrize(
    "action,method,payload",
    [
        ("test_playground_stream", "post", PLAYGROUND_PAYLOAD),
        ("mcp_served_tools", "get", {}),
    ],
)
def test_ir_additional_gateway_actions_guard_scope_first(
    action, method, payload, tenants, seams
):
    # Without an organization both are refused by IsOrgGatewayAdmin (both are
    # gateway_admin_actions) before the action body runs.
    assert action in AgentccGatewayViewSet.gateway_admin_actions
    with (
        patch("agentcc.views.gateway.for_chat") as delegate,
        patch.object(mcp_admin, "org_served_tools") as read,
        patch("agentcc.views.gateway.delegated_enabled") as enabled,
    ):
        response = _dispatch(action, method, payload, tenants[0], override_scope=True)
    _assert_permission_refusal(response)
    _assert_no_effects(seams)
    delegate.assert_not_called()
    read.assert_not_called()
    enabled.assert_not_called()


@pytest.mark.parametrize(
    "action,method,payload",
    [
        ("test_playground_stream", "post", PLAYGROUND_PAYLOAD),
        ("mcp_served_tools", "get", {}),
    ],
)
def test_ir_additional_gateway_actions_refuse_a_foreign_workspace(
    action, method, payload, tenants, seams
):
    # The caller's own org passes IsOrgGatewayAdmin; the scope guard then
    # refuses a workspace of another org before any delegation or read.
    with (
        patch("agentcc.views.gateway.for_chat") as delegate,
        patch.object(mcp_admin, "org_served_tools") as read,
        patch("agentcc.views.gateway.delegated_enabled") as enabled,
    ):
        response = _dispatch(
            action,
            method,
            payload,
            tenants[1],
            organization=tenants[1].organization,
            workspace=tenants[0].workspace,
            override_scope=True,
        )
    _assert_error(response, SCOPE_MESSAGE)
    _assert_no_effects(seams)
    delegate.assert_not_called()
    read.assert_not_called()
    enabled.assert_not_called()


def test_ir_playground_body_typed_guardrail_block_is_preserved(
    tenants, org_configs, seams
):
    body = {"error": {"type": "guardrail_error", "message": "blocked by own policy"}}
    response = _playground(seams, tenants[1], 403, body, {})
    assert response.status_code == 200
    assert response.data["result"]["body"] == body
    assert response.data["result"]["blocked"] is True
