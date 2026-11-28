"""Synthetic prompt jobs: no external runner or channel-layer dispatch."""

from collections.abc import Awaitable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from django.core.cache.backends.locmem import LocMemCache

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from sockets import prompt_jobs
from sockets.consumer import DataConsumer
from sockets.prompt_stream_consumer import PromptStreamConsumer
from sockets.targets import relay_delivery, relay_group
from tfc.constants.levels import Level


@pytest.fixture
def bindings(monkeypatch):
    store = LocMemCache(str(uuid4()), {})
    monkeypatch.setattr(prompt_jobs, "cache", store)
    return store


def test_binding_has_seven_day_ttl_and_cannot_be_reassigned(bindings):
    first = SimpleNamespace(id="workspace-a", organization_id="org")
    second = SimpleNamespace(id="workspace-b", organization_id="org")
    with (
        patch.object(bindings, "add", wraps=bindings.add) as add,
        patch.object(prompt_jobs, "uuid4", return_value=uuid4()),
    ):
        with pytest.raises(ValueError):
            prompt_jobs.create_prompt_job("generate", first, "different-org")
        add.assert_not_called()
        prompt_jobs.create_prompt_job("generate", first, "org")
        assert add.call_args is not None
        assert add.call_args.kwargs == {"timeout": 604_800}
        key = add.call_args.args[0]
        with pytest.raises(RuntimeError):
            prompt_jobs.create_prompt_job("generate", second, "org")
        assert bindings.get(key) == ("org", first.id)


@pytest.fixture
def listener(user, organization):
    socket = DataConsumer()
    socket.organization_id = str(organization.pk)
    socket.room_group_name = relay_group(organization.pk)
    socket.user = user
    socket.scope = {}
    socket.channel_name = "synthetic-channel"
    socket.channel_layer = AsyncMock()
    socket.close = AsyncMock()
    socket.send_json = AsyncMock()
    socket._membership.mark_validated()
    return socket


def assert_denial(listener, target):
    frame = listener.send_json.await_args.args[0]
    assert frame["type"] == "subscription_denied"
    assert frame["uuid"] == target
    assert "result is unavailable" in frame["message"].lower()
    assert "reopen" in frame["message"].lower()
    listener.close.assert_not_awaited()


@pytest.mark.django_db(transaction=True)
def test_revoked_job_workspace_does_not_revoke_other_workspace_or_room(
    bindings, user, organization, workspace, listener
):
    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        role="Member", level=Level.MEMBER
    )
    other = Workspace.no_workspace_objects.create(
        name="Other", organization=organization, created_by=user
    )
    WorkspaceMembership.no_workspace_objects.create(workspace=other, user=user)
    target = prompt_jobs.create_prompt_job("generate", workspace, organization.pk)
    retained = prompt_jobs.create_prompt_job("improve", other, organization.pk)
    group, event = relay_delivery(organization.pk, {"chunk": "synthetic"}, target)
    join = async_to_sync(listener.handle_subscribe)
    deliver = async_to_sync(listener.send_data)

    join({"uuid": target})
    deliver(event)
    listener.send_json.assert_awaited_with(event["data"])
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(is_active=False)
    listener.send_json.reset_mock()
    deliver(event)
    assert listener.send_json.await_count == 1
    assert_denial(listener, target)
    listener.channel_layer.group_discard.assert_awaited_once_with(
        group, listener.channel_name
    )
    assert listener.current_subscription is None
    deliver(event)
    assert listener.send_json.await_count == 1
    _, broadcast = relay_delivery(organization.pk, {"notice": "synthetic"})
    deliver(broadcast)
    listener.send_json.assert_awaited_with(broadcast["data"])
    join({"uuid": retained})
    assert listener.current_subscription == relay_group(organization.pk, retained)
    join({"uuid": target})
    assert_denial(listener, target)
    assert listener.current_subscription == relay_group(organization.pk, retained)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cache_outage", [False, True], ids=["eviction", "outage"])
def test_binding_required_on_every_delivery_and_join(
    bindings, workspace, organization, listener, cache_outage, monkeypatch
):
    target = prompt_jobs.create_prompt_job("improve", workspace, organization.pk)
    group, event = relay_delivery(organization.pk, {"chunk": "synthetic"}, target)
    join = async_to_sync(listener.handle_subscribe)
    join({"uuid": target})
    assert listener.current_subscription == group
    if cache_outage:
        monkeypatch.setattr(bindings, "get", MagicMock(side_effect=ConnectionError))
    else:
        bindings.clear()
    listener.send_json.reset_mock()
    async_to_sync(listener.send_data)(event)
    assert listener.send_json.await_count == 1
    assert_denial(listener, target)
    assert listener.current_subscription is None
    listener.channel_layer.group_discard.assert_awaited_once_with(
        group, listener.channel_name
    )
    join({"uuid": target})
    assert_denial(listener, target)
    listener.channel_layer.group_add.assert_awaited_once()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "kind,id_field,content",
    [
        ("generate", "generation_id", {"statement": "synthetic"}),
        (
            "improve",
            "improve_id",
            {"existing_prompt": "synthetic", "improvement_requirements": "shorter"},
        ),
    ],
)
def test_direct_creator_and_runner_share_bound_target(
    bindings, workspace, organization, user, kind, id_field, content, monkeypatch
):
    stream = PromptStreamConsumer()
    stream.user = user
    stream.session_uuid = "synthetic-session"
    stream.channel_layer = AsyncMock()
    stream.channel_name = "synthetic-channel"
    send = AsyncMock()
    monkeypatch.setattr(stream, "send_json", send)
    monkeypatch.setattr(
        stream,
        "_resolve_workspace_and_org",
        AsyncMock(return_value=(workspace, organization.pk)),
    )
    runs: list[Awaitable[None]] = []
    monkeypatch.setattr(stream, "_create_task", runs.append)
    runner = AsyncMock()

    async def run():
        await getattr(stream, f"handle_{kind}_prompt")(content)
        assert send.await_args is not None
        started = send.await_args.args[0]
        assert started["type"] == "execution_started"
        target = started[id_field]
        assert target.startswith(f"{kind}_")
        assert await prompt_jobs.prompt_job_access(user.pk, organization.pk, target)
        assert len(runs) == 1
        await runs.pop()  # only start once creation/binding is complete
        assert runner.await_args is not None
        args = runner.await_args.kwargs
        assert args[id_field] == target
        assert args["workspace"] == workspace
        assert args["organization_id"] == organization.pk
        callback = args["ws_manager"].consumer_send_json_func
        chunk = {"chunk": "synthetic"}
        await callback(chunk)
        send.assert_awaited_with(chunk)
        bindings.clear()
        await callback(chunk)
        denial = send.await_args.args[0]
        assert denial == {
            "type": "error",
            "message": prompt_jobs.JOB_UNAVAILABLE,
            id_field: target,
            "session_uuid": stream.session_uuid,
        }
        send.reset_mock()
        await callback(chunk)
        send.assert_not_awaited()

    with (
        patch(f"sockets.prompt_stream_consumer.{kind}_prompt_async", runner),
        patch(
            "sockets.prompt_stream_consumer.replace_ids_with_column_name_async",
            new=AsyncMock(return_value="synthetic"),
        ),
    ):
        async_to_sync(run)()


@pytest.mark.django_db(transaction=True)
def test_failed_binding_does_not_start_job(bindings, monkeypatch):
    stream = PromptStreamConsumer()
    monkeypatch.setattr(
        stream,
        "_resolve_workspace_and_org",
        AsyncMock(
            return_value=(SimpleNamespace(id="workspace", organization_id="org"), "org")
        ),
    )
    send = AsyncMock()
    start = MagicMock()
    monkeypatch.setattr(stream, "send_json", send)
    monkeypatch.setattr(stream, "_create_task", start)
    monkeypatch.setattr(bindings, "add", MagicMock(side_effect=ConnectionError))
    async_to_sync(stream.handle_generate_prompt)({"statement": "synthetic"})
    start.assert_not_called()
    assert send.await_args is not None
    assert send.await_args.args[0]["type"] == "error"
