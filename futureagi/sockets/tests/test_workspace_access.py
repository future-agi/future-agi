"""Unit tests for WorkspaceAccessGate (TH-5944 SRP follow-up).

The whole point of pulling this out of PromptStreamConsumer is that these
rules are testable with zero Channels/consumer machinery — no mocked
`send_json`/`close`, no `_make_consumer()` scaffolding. Just user + workspace
id in, a plain result out.
"""

import asyncio
from unittest.mock import MagicMock

from sockets.workspace_access import (
    NOT_FOUND,
    PERMISSION_DENIED,
    WorkspaceAccessGate,
)


def test_resolve_denies_with_not_found_when_workspace_id_missing():
    gate = WorkspaceAccessGate(user=MagicMock(), workspace_id=None)
    result = asyncio.run(gate.resolve())

    assert not result.ok
    assert result.workspace is None
    assert result.org_id is None
    assert result.reason == NOT_FOUND
    assert "workspace_id" in result.message.lower()


def test_resolve_denies_with_not_found_when_workspace_lookup_fails(monkeypatch):
    from sockets.workspace_access import Workspace

    def _raise_dne(*args, **kwargs):
        raise Workspace.DoesNotExist()

    monkeypatch.setattr("sockets.workspace_access.Workspace.objects.get", _raise_dne)

    gate = WorkspaceAccessGate(
        user=MagicMock(), workspace_id="00000000-0000-0000-0000-000000000000"
    )
    result = asyncio.run(gate.resolve())

    assert not result.ok
    assert result.reason == NOT_FOUND


def test_resolve_denies_with_permission_denied_when_user_lacks_access(monkeypatch):
    user = MagicMock()
    user.can_access_workspace = MagicMock(return_value=False)
    workspace_obj = MagicMock(organization_id="org-a")

    monkeypatch.setattr(
        "sockets.workspace_access.Workspace.objects.get", lambda **kw: workspace_obj
    )

    gate = WorkspaceAccessGate(
        user=user, workspace_id="00000000-0000-0000-0000-000000000000"
    )
    result = asyncio.run(gate.resolve())

    assert not result.ok
    assert result.reason == PERMISSION_DENIED
    assert result.workspace is None
    assert result.org_id is None


def test_resolve_grants_and_derives_org_from_workspace(monkeypatch):
    user = MagicMock()
    user.can_access_workspace = MagicMock(return_value=True)
    workspace_obj = MagicMock(organization_id="org-a")

    monkeypatch.setattr(
        "sockets.workspace_access.Workspace.objects.get", lambda **kw: workspace_obj
    )

    gate = WorkspaceAccessGate(
        user=user, workspace_id="00000000-0000-0000-0000-000000000000"
    )
    result = asyncio.run(gate.resolve())

    assert result.ok
    assert result.workspace is workspace_obj
    assert result.org_id == "org-a"
    assert result.reason is None


def test_resolve_org_for_stop_returns_none_without_raising_when_workspace_missing():
    gate = WorkspaceAccessGate(user=MagicMock(), workspace_id=None)
    org_id = asyncio.run(gate.resolve_org_for_stop())
    assert org_id is None


def test_resolve_org_for_stop_skips_the_access_check(monkeypatch):
    """Stop is best-effort — even a user who fails can_access_workspace still
    gets the org back, so the caller can write the Redis stop flag. Only
    `resolve()` (used by execute paths) enforces access.
    """
    user = MagicMock()
    user.can_access_workspace = MagicMock(return_value=False)
    workspace_obj = MagicMock(organization_id="org-a")

    monkeypatch.setattr(
        "sockets.workspace_access.Workspace.objects.get", lambda **kw: workspace_obj
    )

    gate = WorkspaceAccessGate(
        user=user, workspace_id="00000000-0000-0000-0000-000000000000"
    )
    org_id = asyncio.run(gate.resolve_org_for_stop())

    assert org_id == "org-a"
    user.can_access_workspace.assert_not_called()
