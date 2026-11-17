"""Routes of the Omega EE provisioning API (CN22a), under /agentcc/.

EE's service credential calls the virtual-server and agent-key routes; an
org Owner or Admin manages that credential under ``mcp/service/credential/``
and the agent keys' budget under ``mcp/service/workspaces/`` (CN22c); the
gateway reports its refusals of agent keys to ``mcp/service/agent-key-refusals/``.
"""

from django.urls import path

from agentcc.views.ee_agent_key_policy import (
    AgentKeyRefusalsView,
    WorkspaceAgentKeyPolicyView,
    WorkspaceAgentKeysView,
)
from agentcc.views.mcp_service_provisioning import (
    DetachVirtualServerView,
    EnsureVirtualServerView,
    MintAgentKeyView,
    RevokeAgentKeyView,
    RevokeServiceCredentialView,
    RotateServiceCredentialView,
    ServiceCredentialView,
)

urlpatterns = [
    # ensure/ before the <vs_id> route, which would otherwise take it.
    path(
        "mcp/service/virtual-servers/ensure/",
        EnsureVirtualServerView.as_view(),
        name="agentcc-mcp-service-vs-ensure",
    ),
    path(
        "mcp/service/virtual-servers/<str:vs_id>/",
        DetachVirtualServerView.as_view(),
        name="agentcc-mcp-service-vs-detach",
    ),
    path(
        "mcp/service/agent-keys/",
        MintAgentKeyView.as_view(),
        name="agentcc-mcp-service-agent-key-mint",
    ),
    path(
        "mcp/service/agent-keys/<str:agent_key_id>/revoke/",
        RevokeAgentKeyView.as_view(),
        name="agentcc-mcp-service-agent-key-revoke",
    ),
    path(
        "mcp/service/credential/",
        ServiceCredentialView.as_view(),
        name="agentcc-mcp-service-credential",
    ),
    path(
        "mcp/service/credential/rotate/",
        RotateServiceCredentialView.as_view(),
        name="agentcc-mcp-service-credential-rotate",
    ),
    path(
        "mcp/service/credential/revoke/",
        RevokeServiceCredentialView.as_view(),
        name="agentcc-mcp-service-credential-revoke",
    ),
    # CN22c: the agent keys' budget and usage (an Owner or Admin), and the
    # gateway's refusals of them (admin token), audited.
    path(
        "mcp/service/workspaces/<uuid:workspace_id>/agent-key-policy/",
        WorkspaceAgentKeyPolicyView.as_view(),
        name="agentcc-mcp-service-agent-key-policy",
    ),
    path(
        "mcp/service/workspaces/<uuid:workspace_id>/agent-keys/",
        WorkspaceAgentKeysView.as_view(),
        name="agentcc-mcp-service-agent-keys-usage",
    ),
    path(
        "mcp/service/agent-key-refusals/",
        AgentKeyRefusalsView.as_view(),
        name="agentcc-mcp-service-agent-key-refusals",
    ),
]
