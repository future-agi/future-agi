"""Shared fakes for the console batch tests.

FakeBatchGateway stands in for GatewayClient: it records every call and
answers as the gateway's admin API does, with test values only. Nothing here
opens a socket.
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.services.gateway_client import GatewayClientError
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

# The fixtures a test module takes with ``from agentcc.tests.batch_support
# import *``; constants and helpers are imported by name.
__all__ = ["fake_gateway", "delivered", "make_client", "other_org_client"]

GATEWAY = "/agentcc/gateways/default"
BATCH_BODY = {
    "requests": [
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "a"}]},
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "b"}]},
    ],
    "max_concurrency": 2,
}
SUMMARY = {
    "total_cost": 0.002,
    "total_input_tokens": 10,
    "total_output_tokens": 2,
    "completed": 2,
    "failed": 0,
    "cancelled": 0,
}

LEVEL_ROLES = {
    Level.OWNER: OrganizationRoles.OWNER,
    Level.ADMIN: OrganizationRoles.ADMIN,
    Level.MEMBER: OrganizationRoles.MEMBER,
    Level.VIEWER: OrganizationRoles.MEMBER_VIEW_ONLY,
}


class FakeBatchGateway:
    """The gateway admin API as far as console batches use it."""

    def __init__(self):
        self.calls = []
        self.minted = {}  # key id -> raw key
        self.key_org = {}  # key id -> the org_id in its metadata
        self.revoked = []
        self.batches = {}  # batch id -> {"org_id", "status", "batch_key"}
        self.next_status = "processing"
        self.get_error = None  # a GatewayClientError get_batch raises
        self.submit_error = None
        self.health_check = MagicMock(return_value={"status": "ok"})

    def create_key(self, name, owner="", models=None, providers=None, metadata=None):
        n = len(self.minted) + 1
        key_id = f"key_batch_{n}_{uuid.uuid4().hex[:6]}"
        raw = f"sk-agentcc-batch-test-{uuid.uuid4().hex}"
        self.minted[key_id] = raw
        self.key_org[key_id] = (metadata or {}).get("org_id", "")
        self.calls.append(
            (
                "create_key",
                {
                    "name": name,
                    "owner": owner,
                    "models": models,
                    "providers": providers,
                    "metadata": metadata,
                },
            )
        )
        return {
            "id": key_id,
            "key": raw,
            "key_prefix": raw[:12],
            "name": name,
            "owner": owner,
            "status": "active",
        }

    def revoke_key(self, key_id):
        self.calls.append(("revoke_key", key_id))
        self.revoked.append(key_id)
        return {"id": key_id, "status": "revoked"}

    def submit_batch(self, requests, max_concurrency=5, batch_key=None):
        self.calls.append(
            (
                "submit_batch",
                {
                    "requests": requests,
                    "max_concurrency": max_concurrency,
                    "batch_key": batch_key,
                },
            )
        )
        if self.submit_error:
            raise self.submit_error
        batch_id = f"batch-{uuid.uuid4().hex[:8]}"
        org_id = next(
            (self.key_org[k] for k, raw in self.minted.items() if raw == batch_key),
            "",
        )
        self.batches[batch_id] = {
            "org_id": org_id,
            "status": "processing",
            "batch_key": batch_key,
        }
        return {
            "batch_id": batch_id,
            "org_id": org_id,
            "status": "processing",
            "total": len(requests),
            "max_concurrency": max_concurrency,
            "created_at": "2026-10-21T10:00:00Z",
        }

    def _answer(self, batch_id, org_id):
        batch = self.batches.get(batch_id)
        if batch is None or batch["org_id"] != (org_id or ""):
            raise GatewayClientError(
                'Gateway returned 404: {"error":"batch not found"}', status_code=404
            )
        return batch

    def get_batch(self, batch_id, org_id=None):
        self.calls.append(("get_batch", {"batch_id": batch_id, "org_id": org_id}))
        if self.get_error:
            raise self.get_error
        batch = self._answer(batch_id, org_id)
        batch["status"] = self.next_status
        result = {
            "batch_id": batch_id,
            "org_id": batch["org_id"],
            "status": self.next_status,
            "total": 2,
            "max_concurrency": 2,
            "created_at": "2026-10-21T10:00:00Z",
        }
        if self.next_status in ("completed", "cancelled"):
            result["completed_at"] = "2026-10-21T10:00:05Z"
            result["results"] = [{"index": 0, "status": "success"}]
            result["summary"] = dict(SUMMARY)
        return result

    def cancel_batch(self, batch_id, org_id=None):
        self.calls.append(("cancel_batch", {"batch_id": batch_id, "org_id": org_id}))
        batch = self._answer(batch_id, org_id)
        batch["status"] = "cancelled"
        return {"batch_id": batch_id, "org_id": batch["org_id"], "status": "cancelled"}

    def named(self, name):
        return [args for n, args in self.calls if n == name]


@pytest.fixture
def fake_gateway():
    """FakeBatchGateway behind every get_gateway_client the batch path uses."""
    fake = FakeBatchGateway()
    with (
        patch("agentcc.views.gateway.get_gateway_client", return_value=fake),
        patch("agentcc.services.batch_jobs.get_gateway_client", return_value=fake),
        patch("agentcc.views.gateway.push_all_org_configs"),
    ):
        yield fake


@pytest.fixture
def delivered():
    """Every webhook POST the delivery service makes, captured, never sent."""
    posts = []
    session = MagicMock()

    def _post(url, data=None, headers=None, timeout=None):
        posts.append({"url": url, "body": json.loads(data), "headers": headers})
        resp = MagicMock()
        resp.status_code = 200
        return resp

    session.post.side_effect = _post
    with (
        patch("agentcc.services.webhook_delivery.ensure_public_http_url"),
        patch(
            "agentcc.services.webhook_delivery.build_ssrf_safe_session",
            return_value=session,
        ),
    ):
        yield posts


def member_of(user, organization, level, workspace=None):
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=LEVEL_ROLES[level],
        level=level,
        is_active=True,
    )
    workspace = workspace or Workspace.objects.create(
        name=f"Workspace {uuid.uuid4().hex[:6]}",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    return workspace


def user_with_level(organization, level, workspace=None):
    user = User.objects.create_user(
        email=f"batch-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Batch User",
        organization=organization,
        organization_role=LEVEL_ROLES[level],
    )
    workspace = member_of(user, organization, level, workspace=workspace)
    return user, workspace


@pytest.fixture
def make_client():
    clients = []

    def build(user, workspace):
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.stop_workspace_injection()


@pytest.fixture
def other_org_client(db, make_client):
    """An Owner of a second organization."""
    other = Organization.objects.create(name="Batch Other Org")
    user, workspace = user_with_level(other, Level.OWNER)
    return other, make_client(user, workspace)
