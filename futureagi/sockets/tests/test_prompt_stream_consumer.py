"""Unit tests for PromptStreamConsumer (all TH-5944).

Focus areas:
- Close-code constants match the FE cross-side contract.
- `_resolve_workspace_and_org` — the single access gate:
    - missing `workspace_id` → NOT_FOUND
    - malformed UUID (raises Django `ValidationError`) → NOT_FOUND
    - workspace absent / inactive → NOT_FOUND
    - workspace exists but user has no access → PERMISSION_DENIED
    - happy path returns (workspace, org_id) and does NOT close the socket
- `execute_template_async` — cross-workspace-org defensive check → PERMISSION_DENIED.

Any refactor that regresses the multi-org bug should fail one of these.

Tests are sync + `asyncio.run(...)` on the async methods so they run in any
pytest env without depending on the pytest-asyncio plugin registration.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from django.core.exceptions import ValidationError

from sockets.prompt_stream_consumer import (
    WS_CLOSE_CODE_NOT_FOUND,
    WS_CLOSE_CODE_PERMISSION_DENIED,
    WS_CLOSE_CODE_UNAUTHENTICATED,
    PromptStreamConsumer,
)


def _make_consumer(workspace_id=None, user=None):
    consumer = PromptStreamConsumer()
    consumer.scope = {"type": "websocket"}
    consumer.session_uuid = "sess-1"
    consumer.workspace_id = workspace_id
    consumer.user = user or MagicMock()
    consumer.send_json = AsyncMock()
    consumer.close = AsyncMock()
    consumer._last_org_id = None
    return consumer


# ---------------------------------------------------------------------------
# Close-code contract — pinned BE-side. The FE test at
# frontend/src/sections/workbench/createPrompt/__tests__/ws-close-codes.test.js
# physically reads THIS file at test time and asserts the numeric values
# match its own `WS_CLOSE_CODES` constant, so drift on either side breaks CI.
# If you change these numbers, change the FE mirror too and re-run both suites.
# ---------------------------------------------------------------------------


def test_close_code_constants_match_the_ws_contract():
    assert WS_CLOSE_CODE_UNAUTHENTICATED == 4001
    assert WS_CLOSE_CODE_PERMISSION_DENIED == 4003
    assert WS_CLOSE_CODE_NOT_FOUND == 4004


# ---------------------------------------------------------------------------
# _resolve_workspace_and_org — the single access gate
# ---------------------------------------------------------------------------


def test_resolve_returns_none_when_workspace_id_missing():
    consumer = _make_consumer(workspace_id=None)
    workspace, org_id = asyncio.run(consumer._resolve_workspace_and_org())
    assert (workspace, org_id) == (None, None)
    consumer.close.assert_awaited_once_with(code=WS_CLOSE_CODE_NOT_FOUND)
    err_frame = consumer.send_json.await_args.args[0]
    assert err_frame["type"] == "error"
    assert "workspace_id" in err_frame["message"].lower()


def test_resolve_returns_none_when_workspace_id_is_malformed_uuid(monkeypatch):
    """Django `UUIDField.to_python` raises ``ValidationError`` — not ``ValueError``.

    If the except clause forgets ValidationError the socket hangs on any bad
    query string, so pin it explicitly.
    """
    consumer = _make_consumer(workspace_id="not-a-uuid")

    def _raise_validation_error(*args, **kwargs):
        raise ValidationError("bad uuid")

    monkeypatch.setattr(
        "sockets.prompt_stream_consumer.Workspace.objects.get",
        _raise_validation_error,
    )
    workspace, org_id = asyncio.run(consumer._resolve_workspace_and_org())
    assert (workspace, org_id) == (None, None)
    consumer.close.assert_awaited_once_with(code=WS_CLOSE_CODE_NOT_FOUND)


def test_resolve_returns_none_when_workspace_does_not_exist(monkeypatch):
    from sockets.prompt_stream_consumer import Workspace

    consumer = _make_consumer(workspace_id="00000000-0000-0000-0000-000000000000")

    def _raise_dne(*args, **kwargs):
        raise Workspace.DoesNotExist()

    monkeypatch.setattr(
        "sockets.prompt_stream_consumer.Workspace.objects.get", _raise_dne
    )
    workspace, org_id = asyncio.run(consumer._resolve_workspace_and_org())
    assert (workspace, org_id) == (None, None)
    consumer.close.assert_awaited_once_with(code=WS_CLOSE_CODE_NOT_FOUND)


def test_resolve_returns_none_when_user_has_no_workspace_access(monkeypatch):
    consumer = _make_consumer(workspace_id="00000000-0000-0000-0000-000000000000")
    consumer.user.can_access_workspace = MagicMock(return_value=False)

    workspace_obj = MagicMock(organization_id="org-a")
    monkeypatch.setattr(
        "sockets.prompt_stream_consumer.Workspace.objects.get",
        lambda **kw: workspace_obj,
    )

    workspace, org_id = asyncio.run(consumer._resolve_workspace_and_org())
    assert (workspace, org_id) == (None, None)
    consumer.close.assert_awaited_once_with(code=WS_CLOSE_CODE_PERMISSION_DENIED)


def test_resolve_happy_path_pins_org_and_does_not_close(monkeypatch):
    consumer = _make_consumer(workspace_id="00000000-0000-0000-0000-000000000000")
    consumer.user.can_access_workspace = MagicMock(return_value=True)

    workspace_obj = MagicMock(organization_id="org-a")
    monkeypatch.setattr(
        "sockets.prompt_stream_consumer.Workspace.objects.get",
        lambda **kw: workspace_obj,
    )

    workspace, org_id = asyncio.run(consumer._resolve_workspace_and_org())
    assert workspace is workspace_obj
    assert org_id == "org-a"
    consumer.close.assert_not_awaited()
    # _last_org_id cache set for the stop_* handlers.
    assert consumer._last_org_id == "org-a"


# ---------------------------------------------------------------------------
# execute_template_async — cross-workspace-org defensive check
# ---------------------------------------------------------------------------


def test_execute_template_rejects_template_from_a_different_org(monkeypatch):
    """The single-gate model derives org from the workspace. If a client passes
    a template_id belonging to a different org (spoof), the defensive check
    must close 4003 before running.
    """
    consumer = _make_consumer(workspace_id="00000000-0000-0000-0000-000000000000")
    consumer.user.can_access_workspace = MagicMock(return_value=True)

    workspace_obj = MagicMock(id="ws-a", organization_id="org-a")
    # workspace_id=None so this exercises the org-fallback branch, not the
    # workspace-match branch below.
    template_obj = MagicMock(workspace_id=None, organization_id="org-b")

    monkeypatch.setattr(
        "sockets.prompt_stream_consumer.Workspace.objects.get",
        lambda **kw: workspace_obj,
    )
    monkeypatch.setattr(
        "sockets.prompt_stream_consumer.PromptTemplate.objects.get",
        lambda **kw: template_obj,
    )

    asyncio.run(
        consumer.execute_template_async(content={"version": 1}, template_id="tmpl-1")
    )

    consumer.close.assert_awaited_with(code=WS_CLOSE_CODE_PERMISSION_DENIED)
    # An error frame explaining the mismatch was sent before the close.
    frame_payloads = [c.args[0] for c in consumer.send_json.await_args_list]
    assert any(
        p.get("type") == "error" and "organization" in p.get("message", "").lower()
        for p in frame_payloads
    )


def test_execute_template_rejects_template_from_a_different_workspace_same_org(
    monkeypatch,
):
    """Same org, different workspace must ALSO be rejected once the template
    has a workspace_id — org-only scoping let this leak through pre-fix.
    """
    consumer = _make_consumer(workspace_id="ws-b")
    consumer.user.can_access_workspace = MagicMock(return_value=True)

    workspace_obj = MagicMock(id="ws-b", organization_id="org-a")
    template_obj = MagicMock(workspace_id="ws-a", organization_id="org-a")

    monkeypatch.setattr(
        "sockets.prompt_stream_consumer.Workspace.objects.get",
        lambda **kw: workspace_obj,
    )
    monkeypatch.setattr(
        "sockets.prompt_stream_consumer.PromptTemplate.objects.get",
        lambda **kw: template_obj,
    )

    asyncio.run(
        consumer.execute_template_async(content={"version": 1}, template_id="tmpl-1")
    )

    consumer.close.assert_awaited_with(code=WS_CLOSE_CODE_PERMISSION_DENIED)
    frame_payloads = [c.args[0] for c in consumer.send_json.await_args_list]
    assert any(
        p.get("type") == "error"
        and "does not belong to the selected workspace" in p.get("message", "")
        for p in frame_payloads
    )


def test_execute_template_allows_null_workspace_template_in_same_org(monkeypatch):
    """A legacy/org-wide template (workspace_id=None) should run from any
    workspace within its own org — only the org-level check applies.
    """
    consumer = _make_consumer(workspace_id="ws-b")
    consumer.user.can_access_workspace = MagicMock(return_value=True)

    workspace_obj = MagicMock(id="ws-b", organization_id="org-a")
    template_obj = MagicMock(workspace_id=None, organization_id="org-a")

    monkeypatch.setattr(
        "sockets.prompt_stream_consumer.Workspace.objects.get",
        lambda **kw: workspace_obj,
    )
    monkeypatch.setattr(
        "sockets.prompt_stream_consumer.PromptTemplate.objects.get",
        lambda **kw: template_obj,
    )

    asyncio.run(
        consumer.execute_template_async(content={"version": 1}, template_id="tmpl-1")
    )

    # Should proceed past the access checks (it'll fail later on PromptVersion
    # lookup since that isn't mocked here, but it must NOT be closed for
    # permission reasons).
    for call in consumer.close.await_args_list:
        assert call.kwargs.get("code") != WS_CLOSE_CODE_PERMISSION_DENIED
