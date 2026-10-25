"""Shared helpers for the gateway console tests.

A member client of a chosen role, and a fake gateway client that answers the
way the gateway does: GET /-/config with its server, logging and batch blocks
and org_sections_enforced, /healthz, and the key and chat calls the Guardrails
> Test tab makes. Nothing here reaches a network host.
"""

import uuid

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

GATEWAY = "/agentcc/gateways/default"

LEVEL_ROLES = {
    Level.OWNER: OrganizationRoles.OWNER,
    Level.ADMIN: OrganizationRoles.ADMIN,
    Level.MEMBER: OrganizationRoles.MEMBER,
    Level.VIEWER: OrganizationRoles.MEMBER_VIEW_ONLY,
}

# What GET /-/config answers: the node's server timeouts, logging switches and
# batch facts, and the org sections this node enforces.
NODE_GATEWAY_CONFIG = {
    "server": {
        "port": 8080,
        "host": "0.0.0.0",
        "read_timeout_ms": 60000,
        "write_timeout_ms": 300000,
        "idle_timeout_ms": 120000,
        "max_request_timeout_ms": 300000,
    },
    "logging": {
        "level": "info",
        "request_logging": True,
        "include_bodies": False,
        "content_mode": "metadata_only",
    },
    "batch": {
        "storage": "memory",
        "default_max_concurrency": 10,
        "max_body_bytes": 10485760,
    },
    "org_sections_enforced": ["alerting", "budgets", "ip_acl", "tool_policy"],
    "providers_count": 3,
    "models_count": 7,
    "plugins_count": 12,
}


def user_with_level(organization, level, workspace=None):
    """A user who holds `level` in `organization`, a member of its default
    workspace (or of `workspace`)."""
    user = User.objects.create_user(
        email=f"console-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Console User",
        organization=organization,
        organization_role=LEVEL_ROLES[level],
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=LEVEL_ROLES[level],
        level=level,
        is_active=True,
    )
    workspace = workspace or Workspace.no_workspace_objects.get(
        organization=organization, is_default=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    return user, workspace


def owner_of_new_org(name):
    """A second organization with its default workspace and an Owner."""
    from accounts.models import Organization

    organization = Organization.objects.create(name=name)
    owner = User.objects.create_user(
        email=f"console-owner-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Other Owner",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    workspace = Workspace.objects.create(
        name=f"{name} Workspace",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=owner,
        organization=organization,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=owner,
        workspace=workspace,
        role="Workspace Owner",
        level=Level.OWNER,
        organization_membership=membership,
        is_active=True,
    )
    return organization, owner, workspace


class ClientFactory:
    """Builds authenticated clients and stops their workspace patchers."""

    def __init__(self):
        self._clients = []

    def __call__(self, user, workspace):
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        self._clients.append(client)
        return client

    def close(self):
        for client in self._clients:
            client.stop_workspace_injection()


class FakeGateway:
    """Answers what the views ask of the gateway client, and records it."""

    def __init__(self, *, config=None, health=None, chat=None):
        self.config = config
        self.health = health if health is not None else {"status": "ok"}
        self.chat = chat
        self.calls = []
        self.health_error = None
        self.config_error = None

    def health_check(self):
        self.calls.append("health_check")
        if self.health_error is not None:
            raise self.health_error
        return dict(self.health)

    def get_config(self):
        self.calls.append("get_config")
        if self.config_error is not None:
            raise self.config_error
        return self.config

    def provider_health(self):
        # The operator's breakers: never what an org provider shows.
        self.calls.append("provider_health")
        return {
            "providers": [
                {"id": "openai", "healthy": False, "circuit_state": "open"},
                {"id": "anthropic", "healthy": True, "circuit_state": "half-open"},
            ]
        }

    def create_key(self, name, owner="", models=None, providers=None, metadata=None):
        self.calls.append("create_key")
        return {"id": "key_playground", "key": "sk-playground-test"}

    def revoke_key(self, key_id):
        self.calls.append(("revoke_key", key_id))
        return {}

    def get_org_config(self, org_id):
        self.calls.append("get_org_config")
        return {}

    def send_chat_completion(
        self, prompt, model, api_key, system_prompt=None, cache_control=None
    ):
        self.calls.append("send_chat_completion")
        return self.chat
