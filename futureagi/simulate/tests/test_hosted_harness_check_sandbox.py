"""The build's check runs in a chat sandbox of its own: it stops when the check's turn
ends, the check is asked once, a lone Stop starts nothing, and chat waits for the
check's files."""

from __future__ import annotations

import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from simulate.models import (
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationLease,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
)
from simulate.services import hosted_harness
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    register_attempt,
)
from simulate.services.hosted_harness_conversation import (
    PLATFORM_LANDING_WAIT_SECONDS,
    RUNTIME_GIVE_UP_SECONDS,
    RUNTIME_RETRY_BACKOFF_SECONDS,
    enqueue_message,
    issue_conversation_capability,
    platform_landing_pending,
    record_platform_landing_refused,
    recover_stalled_conversations,
)
from simulate.services.hosted_harness_gateway import HostedHarnessGateway
from simulate.services.hosted_sandbox import SandboxNotFoundError
from simulate.tasks import harness_nudges as nudge_tasks
from simulate.tasks import hosted_harness_conversation as conversation_tasks
from simulate.tests.test_harness_amend_archive import NAMES
from simulate.tests.test_harness_versioning_rebuild import _send
from simulate.tests.test_hosted_harness_conversation import (
    _chat_checkpoint,
    _commit_checkpoint,
    _headers,
    _payload,
    _Storage,
)

URL = "https://platform.example"
GUEST = "/simulate/api/harness/conversations"
STOP = nudge_tasks.stop_harness_nudge_sandbox.name
ENSURE = conversation_tasks.ensure_hosted_harness_conversation_runtime.name
Lease = HostedHarnessConversationLease
Message = HostedHarnessConversationMessage
Conversation = HostedHarnessConversation


@pytest.fixture(autouse=True)
def public_url(settings):
    settings.HARNESS_PUBLIC_BASE_URL = URL


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _Storage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


@pytest.fixture
def inline(monkeypatch):
    """The sandbox stop, the runtime start, the promotion and the rebuild check are
    recorded by name; none of them runs."""
    calls = []
    for task in (
        nudge_tasks.stop_harness_nudge_sandbox,
        conversation_tasks.ensure_hosted_harness_conversation_runtime,
        conversation_tasks.promote_hosted_harness_conversation_checkpoint,
        nudge_tasks.check_harness_nudges,
    ):

        def apply_async(*, args, _name=task.name, **_options):
            calls.append((_name, list(args)))

        monkeypatch.setattr(task, "apply_async", apply_async)
    return calls


def _names(calls, name):
    return [args for task, args in calls if task == name]


def _built(organization, capture):
    """A builder-session build: the job, its attempt and the platform's build message."""
    job, _ = create_hosted_job(organization, _payload(), idempotency_key=str(uuid4()))
    capability = register_attempt(
        job.id, endpoint_base_url=URL, provider_ref="sandbox-build"
    )
    conversation, build, _ = enqueue_message(
        job,
        content="Build it",
        client_request_id=f"build-{capability.attempt.id}",
        payload={"from_platform": True},
    )
    return SimpleNamespace(
        job=job,
        attempt=capability.attempt,
        conversation=conversation,
        build=build,
        capture=capture,
    )


def _finished(ctx):
    """The build's own turn acked its message, and the build completed."""
    Message.no_workspace_objects.filter(id=ctx.build.id).update(
        state=Message.State.COMPLETED
    )
    Conversation.no_workspace_objects.filter(id=ctx.conversation.id).update(
        command_acked_through=ctx.build.sequence
    )
    HostedHarnessJob.no_workspace_objects.filter(id=ctx.job.id).update(
        state=HostedHarnessJob.State.COMPLETED,
        terminal_at=timezone.now() - timedelta(minutes=1),
    )
    ctx.job = _job(ctx)


def _job(ctx):
    return HostedHarnessJob.no_workspace_objects.get(id=ctx.job.id)


def _conversation(ctx):
    return Conversation.no_workspace_objects.get(id=ctx.conversation.id)


def _sandbox(ctx, provider_ref, *, attempt=None, after=0):
    """A sandbox's chat process holds the lease and has polled once."""
    ctx.capability = issue_conversation_capability(
        ctx.conversation,
        endpoint_base_url=URL,
        provider_ref=provider_ref,
        attempt=attempt,
        ttl_seconds=1800,
        control_only=attempt is not None,
    )
    ctx.client, ctx.headers = APIClient(), _headers(ctx.capability)
    assert _poll(ctx, after) == 200


def _poll(ctx, after):
    return ctx.client.get(
        f"{GUEST}/{ctx.conversation.id}/commands/?after={after}", **ctx.headers
    ).status_code


def _checked(organization, capture):
    """The build finished, the platform asked the check, and its sandbox polled."""
    ctx = _built(organization, capture)
    _finished(ctx)
    hosted_harness._ask_builder_to_check(str(ctx.job.id), str(ctx.attempt.id))
    ctx.check = Message.no_workspace_objects.get(
        conversation=ctx.conversation, client_request_id=f"check-{ctx.attempt.id}"
    )
    _sandbox(ctx, "sandbox-check", after=ctx.build.sequence)
    return ctx


def _post(ctx, acknowledged, *events, execute=True):
    """The guest's events, numbered on from the last one the platform took."""
    first = _conversation(ctx).event_acked_through + 1
    numbered = [
        (first + offset, kind, payload) for offset, (kind, payload) in enumerate(events)
    ]
    with ctx.capture(execute=execute) as callbacks:
        response = _send(
            ctx.client, ctx.conversation, ctx.headers, acknowledged, *numbered
        )
    assert response.status_code == 200, response.content
    return numbered, callbacks


def _turn(ctx, command, *, checkpoint=False, outcome="success", before_end=None):
    """One command's turn, start to end, optionally committing a workspace first."""
    ran = {"command_message_id": str(command.id)}
    # The guest acks a command only when its turn ends.
    _post(ctx, _conversation(ctx).command_acked_through, ("turn_started", ran))
    if before_end is not None:
        before_end()
    ending = []
    if checkpoint:
        body = _chat_checkpoint(NAMES, one_liner="checked")
        ending.append(
            (
                "checkpoint_committed",
                _commit_checkpoint(ctx.client, ctx.conversation, ctx.headers, body),
            )
        )
    ending.append(("turn_completed", {**ran, "outcome": outcome}))
    numbered, _ = _post(ctx, command.sequence, *ending)
    ctx.turn_sequence = numbered[-1][0]
    if checkpoint:
        ctx.checkpoint_sequence, _, committed = numbered[0]
        ctx.digest = committed["digest"]


def _lease(ctx):
    return Lease.no_workspace_objects.get(conversation=ctx.conversation)


def _gateway():
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = MagicMock(runtime_name="r", runtime_digest="d")
    return gateway


def _ensure(ctx):
    """One chat start for the conversation; the provider calls are recorded."""
    gateway = _gateway()
    with (
        patch.object(gateway, "_start_chat_sandbox") as start,
        patch.object(gateway, "_delete_chat_sandbox") as delete,
    ):
        result = gateway.ensure_conversation_runtime(
            _job(ctx), conversation=ctx.conversation, endpoint_base_url=URL
        )
    return result, start, delete


def _publish(ctx, entry):
    """The environment's record of this conversation's published checkpoint."""
    environment = _job(ctx)
    payload = dict(environment.payload or {})
    metadata = dict(payload.get("metadata") or {})
    metadata["conversation_checkpoints"] = {str(ctx.conversation.id): entry}
    payload["metadata"] = metadata
    environment.payload = payload
    environment.save(update_fields=["payload", "updated_at"])
    ctx.job = _job(ctx)
    return ctx.job


@pytest.mark.django_db
@pytest.mark.parametrize(
    "ending", ["success", "failed", "replayed", "stop_after_the_turn"]
)
def test_the_end_of_the_platforms_turn_stops_its_sandbox(
    organization, inline, django_capture_on_commit_callbacks, ending
):
    ctx = _checked(organization, django_capture_on_commit_callbacks)
    ran = {"command_message_id": str(ctx.check.id)}
    stop = None
    if ending == "replayed":
        _post(ctx, ctx.check.sequence, ("turn_completed", {**ran, "replayed": True}))
    else:
        if ending == "stop_after_the_turn":
            _post(ctx, ctx.build.sequence, ("turn_started", ran))
            # Sent after the turn's last poll for a Stop, before it reported its end.
            stop = enqueue_message(
                ctx.job, content="", client_request_id="stop-it", kind="interrupt"
            )[1]
            _post(
                ctx,
                ctx.check.sequence,
                ("turn_completed", {**ran, "outcome": "success"}),
            )
        else:
            _turn(ctx, ctx.check, outcome=ending)

    assert _lease(ctx).state == Lease.State.EXPIRED
    assert _conversation(ctx).state == Conversation.State.COLD
    assert _names(inline, STOP) == [[str(ctx.conversation.id), "sandbox-check"]]
    assert _poll(ctx, ctx.check.sequence) == 409
    if stop is not None:
        stop.refresh_from_db()
        assert stop.state == Message.State.COMPLETED


@pytest.mark.django_db
def test_a_stop_that_cannot_be_scheduled_still_ends_the_claim(
    organization, inline, django_capture_on_commit_callbacks, monkeypatch, caplog
):
    def unreachable(*_args):
        raise RuntimeError("scheduler unreachable")

    monkeypatch.setattr(nudge_tasks, "schedule_sandbox_stop", unreachable)
    ctx = _checked(organization, django_capture_on_commit_callbacks)

    with caplog.at_level(logging.ERROR):
        _turn(ctx, ctx.check)

    assert _lease(ctx).state == Lease.State.EXPIRED
    assert _conversation(ctx).state == Conversation.State.COLD
    logged = [record.getMessage() for record in caplog.records]
    assert any("harness_platform_turn_sandbox_stop_failed" in line for line in logged)
    assert not any("harness_nudge_turn_event_failed" in line for line in logged)


@pytest.mark.django_db
def test_a_stop_during_the_check_stops_its_sandbox_when_the_turn_ends(
    organization, inline, django_capture_on_commit_callbacks
):
    ctx = _checked(organization, django_capture_on_commit_callbacks)
    ran = {"command_message_id": str(ctx.check.id)}
    _post(ctx, ctx.build.sequence, ("turn_started", ran))
    interrupt = enqueue_message(
        ctx.job, content="", client_request_id="stop-it", kind="interrupt"
    )[1]
    _post(
        ctx,
        ctx.build.sequence,
        ("turn_interrupted", {**ran, "interrupt_message_id": str(interrupt.id)}),
    )
    assert _names(inline, STOP) == []
    assert _lease(ctx).state == Lease.State.ACTIVE

    _post(
        ctx,
        interrupt.sequence,
        ("turn_completed", {**ran, "outcome": "interrupted"}),
    )

    assert _lease(ctx).state == Lease.State.EXPIRED
    assert _names(inline, STOP) == [[str(ctx.conversation.id), "sandbox-check"]]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "case",
    [
        "build_turn",
        "person_waiting",
        "reply_under_way",
        "question_open",
        "question_open_with_stop",
        "person_turn",
    ],
)
def test_the_sandbox_is_kept(
    organization, inline, django_capture_on_commit_callbacks, case
):
    expected_state = Conversation.State.WARM_IDLE
    stop = None
    if case == "build_turn":
        # The build's own turn ends in the attempt's sandbox, which is not chat's.
        ctx = _built(organization, django_capture_on_commit_callbacks)
        _sandbox(ctx, "sandbox-build", attempt=ctx.attempt)
        _turn(ctx, ctx.build)
    elif case == "person_turn":
        ctx = _built(organization, django_capture_on_commit_callbacks)
        _finished(ctx)
        person = enqueue_message(ctx.job, content="hi", client_request_id="p0")[1]
        _sandbox(ctx, "sandbox-chat", after=ctx.build.sequence)
        _turn(ctx, person)
    elif case == "person_waiting":
        ctx = _checked(organization, django_capture_on_commit_callbacks)
        _turn(
            ctx,
            ctx.check,
            before_end=lambda: enqueue_message(
                ctx.job, content="hi", client_request_id="p1"
            ),
        )
        waiting = Message.no_workspace_objects.get(
            conversation=ctx.conversation, client_request_id="p1"
        )
        assert waiting.state == Message.State.QUEUED
        assert waiting.sequence > _conversation(ctx).command_acked_through
    else:
        # The guest runs one command at a time, so these states are set directly
        # between the turn's end and its follow-up.
        ctx = _checked(organization, django_capture_on_commit_callbacks)
        ran = {"command_message_id": str(ctx.check.id)}
        _post(ctx, ctx.build.sequence, ("turn_started", ran))
        _, callbacks = _post(
            ctx,
            ctx.check.sequence,
            ("turn_completed", {**ran, "outcome": "success"}),
            execute=False,
        )
        if case == "reply_under_way":
            expected_state = Conversation.State.RESPONDING
            Conversation.no_workspace_objects.filter(id=ctx.conversation.id).update(
                state=expected_state
            )
        else:
            expected_state = Conversation.State.WAITING_FOR_USER
            Conversation.no_workspace_objects.filter(id=ctx.conversation.id).update(
                blocking_input={
                    "message_id": str(uuid4()),
                    "kind": "question_requested",
                },
                state=expected_state,
            )
            if case == "question_open_with_stop":
                stop = enqueue_message(
                    ctx.job, content="", client_request_id="stop-it", kind="interrupt"
                )[1]
        for callback in callbacks:
            callback()

    assert _lease(ctx).state == Lease.State.ACTIVE
    assert _names(inline, STOP) == []
    assert _conversation(ctx).state == expected_state
    if stop is not None:
        # A Stop beside an open question is the kept sandbox's to answer.
        stop.refresh_from_db()
        assert stop.state == Message.State.QUEUED


@pytest.mark.django_db
@pytest.mark.parametrize(
    "second_ask", ["while_the_check_waits", "after_the_check_ended"]
)
def test_the_check_is_asked_for_once_per_build(
    organization, inline, django_capture_on_commit_callbacks, second_ask
):
    ctx = _checked(organization, django_capture_on_commit_callbacks)
    if second_ask == "after_the_check_ended":
        _turn(ctx, ctx.check)
        assert _lease(ctx).state == Lease.State.EXPIRED

    hosted_harness._ask_builder_to_check(str(ctx.job.id), str(ctx.attempt.id))

    assert (
        Message.no_workspace_objects.filter(
            conversation=ctx.conversation, client_request_id__startswith="check-"
        ).count()
        == 1
    )
    assert _names(inline, ENSURE) == [[str(ctx.conversation.id), URL]]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "queue",
    [
        ["interrupt"],
        ["cancel_operation"],
        ["interrupt", "user_message"],
        ["user_message"],
        "live_runtime",
        "live_elsewhere",
    ],
    ids=[
        "interrupt",
        "cancel",
        "interrupt_then_message",
        "message",
        "live_runtime",
        "live_elsewhere",
    ],
)
def test_a_stop_with_nothing_running_starts_no_sandbox(
    organization, inline, django_capture_on_commit_callbacks, queue
):
    ctx = _checked(organization, django_capture_on_commit_callbacks)
    _turn(ctx, ctx.check)
    assert platform_landing_pending(_job(ctx), ctx.conversation) is False
    live = None
    elsewhere = queue == "live_elsewhere"
    if queue in ("live_runtime", "live_elsewhere"):
        # A process still polls: in chat's own sandbox, or in the build's.
        live = issue_conversation_capability(
            ctx.conversation,
            endpoint_base_url=URL,
            provider_ref="sandbox-build" if elsewhere else "sandbox-live",
            attempt=ctx.attempt if elsewhere else None,
            ttl_seconds=3600,
            control_only=elsewhere,
        ).lease
        Lease.no_workspace_objects.filter(id=live.id).update(
            state=Lease.State.ACTIVE, heartbeat_at=timezone.now()
        )
        queue = ["interrupt"]
    messages = [
        enqueue_message(
            ctx.job,
            content="" if kind != "user_message" else "run them",
            client_request_id=f"q{index}",
            kind=kind,
        )[1]
        for index, kind in enumerate(queue)
    ]

    result, start, _delete = _ensure(ctx)

    states = {
        message.payload["command_kind"]: Message.no_workspace_objects.get(
            id=message.id
        ).state
        for message in messages
    }
    if elsewhere:
        # A live process is not "nothing running": the Stop is not settled under it.
        assert states == {"interrupt": Message.State.QUEUED}
        assert _conversation(ctx).state != Conversation.State.COLD
        start.assert_called_once()
    elif live is not None:
        assert result.id == live.id
        start.assert_not_called()
        assert states == {"interrupt": Message.State.QUEUED}
    elif "user_message" in queue:
        start.assert_called_once()
        assert set(states.values()) == {Message.State.QUEUED}
    else:
        assert result is None
        start.assert_not_called()
        assert set(states.values()) == {Message.State.COMPLETED}
        assert _conversation(ctx).state == Conversation.State.COLD


def _checked_with_files(organization, capture):
    """The check's turn succeeded after committing a workspace; nothing published yet."""
    ctx = _checked(organization, capture)
    _turn(ctx, ctx.check, checkpoint=True)
    return ctx


@pytest.mark.django_db
@pytest.mark.parametrize(
    "case",
    ["unpublished", "published", "past_the_wait", "build_turn_only", "cleanup_retried"],
)
def test_chat_start_waits_for_the_checks_files(
    organization, storage, inline, django_capture_on_commit_callbacks, case
):
    if case == "build_turn_only":
        # The latest turn is the build's own, in its attempt's sandbox.
        ctx = _built(organization, django_capture_on_commit_callbacks)
        _sandbox(ctx, "sandbox-build", attempt=ctx.attempt)
        _turn(ctx, ctx.build, checkpoint=True)
        _finished(ctx)
    else:
        ctx = _checked_with_files(organization, django_capture_on_commit_callbacks)
    if case == "published":
        _publish(ctx, {"digest": ctx.digest, "sequence": ctx.checkpoint_sequence})
    elif case == "past_the_wait":
        HostedHarnessConversationEvent.no_workspace_objects.filter(
            conversation=ctx.conversation, sequence=ctx.turn_sequence
        ).update(
            created_at=timezone.now()
            - timedelta(seconds=PLATFORM_LANDING_WAIT_SECONDS + 1)
        )
    elif case == "cleanup_retried":
        # A retried clean-up rewrites the build's end after the check's turn.
        HostedHarnessJob.no_workspace_objects.filter(id=ctx.job.id).update(
            terminal_at=timezone.now() + timedelta(seconds=1)
        )
    person = enqueue_message(ctx.job, content="run them", client_request_id="p1")[1]

    result, start, delete = _ensure(ctx)

    if case in ("unpublished", "cleanup_retried"):
        assert result is None
        start.assert_not_called()
        person.refresh_from_db()
        assert person.state == Message.State.QUEUED
    else:
        start.assert_called_once()
    if case == "published":
        delete.assert_called_once_with("sandbox-check", ctx.conversation.id)


@pytest.mark.django_db
@pytest.mark.parametrize("settled", ["refused", "environment_not_completed"])
def test_the_wait_ends_when_the_publish_cannot_land(
    organization, storage, inline, django_capture_on_commit_callbacks, settled
):
    ctx = _checked_with_files(organization, django_capture_on_commit_callbacks)
    assert platform_landing_pending(_job(ctx), ctx.conversation) is True

    if settled == "refused":
        # An older landing, and the drafts its workspace was composed on.
        earlier = {
            "digest": "sha256:" + "0" * 64,
            "sequence": ctx.checkpoint_sequence - 1,
            "draft_basis": {"token": "t0"},
        }
        _publish(ctx, earlier)
        assert platform_landing_pending(_job(ctx), ctx.conversation) is True
        record_platform_landing_refused(str(ctx.conversation.id), ctx.turn_sequence)
        assert _job(ctx).payload["metadata"]["conversation_checkpoints"] == {
            str(ctx.conversation.id): {
                **earlier,
                "refused_sequence": ctx.checkpoint_sequence,
            }
        }
    else:
        HostedHarnessJob.no_workspace_objects.filter(id=ctx.job.id).update(
            state=HostedHarnessJob.State.FAILED
        )

    assert platform_landing_pending(_job(ctx), ctx.conversation) is False


@pytest.mark.django_db
def test_the_wait_ends_just_past_its_bound(
    organization, storage, inline, django_capture_on_commit_callbacks
):
    ctx = _checked_with_files(organization, django_capture_on_commit_callbacks)
    turn = HostedHarnessConversationEvent.no_workspace_objects.get(
        conversation=ctx.conversation, sequence=ctx.turn_sequence
    )
    bound = turn.created_at + timedelta(seconds=PLATFORM_LANDING_WAIT_SECONDS)
    assert PLATFORM_LANDING_WAIT_SECONDS == (
        RUNTIME_GIVE_UP_SECONDS - 2 * RUNTIME_RETRY_BACKOFF_SECONDS
    )

    # Exactly at the bound the wait holds; a microsecond past, it does not.
    assert platform_landing_pending(_job(ctx), ctx.conversation, now=bound) is True
    assert (
        platform_landing_pending(
            _job(ctx), ctx.conversation, now=bound + timedelta(microseconds=1)
        )
        is False
    )


@pytest.mark.django_db
def test_a_message_sent_after_the_check_starts_when_its_publish_never_settles(
    organization, storage, inline, django_capture_on_commit_callbacks
):
    ctx = _checked_with_files(organization, django_capture_on_commit_callbacks)
    _, person, _ = enqueue_message(ctx.job, content="run them", client_request_id="p1")
    starts = []

    def schedule(conversation_id):
        assert conversation_id == str(ctx.conversation.id)
        starts.append(_ensure(ctx)[1].called)

    def sweep(after_turn, *, last_asked):
        """A sweep pass ``after_turn`` seconds past the check's turn. The person wrote
        30 s after the turn; the sweep last asked for a start ``last_asked`` s after it."""
        turn_at = timezone.now() - timedelta(seconds=after_turn)
        HostedHarnessConversationEvent.no_workspace_objects.filter(
            conversation=ctx.conversation, sequence=ctx.turn_sequence
        ).update(created_at=turn_at)
        Message.no_workspace_objects.filter(id=person.id).update(
            created_at=turn_at + timedelta(seconds=30)
        )
        Conversation.no_workspace_objects.filter(id=ctx.conversation.id).update(
            updated_at=turn_at + timedelta(seconds=last_asked)
        )
        return recover_stalled_conversations(schedule)

    # The last pass before the wait ends, and the next one, a backoff later.
    last = PLATFORM_LANDING_WAIT_SECONDS - 1
    held = sweep(last, last_asked=last - RUNTIME_RETRY_BACKOFF_SECONDS - 1)
    released = sweep(last + RUNTIME_RETRY_BACKOFF_SECONDS + 1, last_asked=last)

    assert (held["restarted"], held["abandoned"]) == (1, 0)
    assert (released["restarted"], released["abandoned"]) == (1, 0)
    assert starts == [False, True]
    person.refresh_from_db()
    assert person.state == Message.State.QUEUED


REFUSALS = {
    # Reported to the person in chat.
    "refused_reported": "conversation_checkpoint_stale",
    # Refused for good, but not reported.
    "refused_unreported": "conversation_checkpoint_conflict",
}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "case",
    ["landed", *REFUSALS, "nothing_waiting", "person_turn"],
)
def test_a_settled_check_starts_the_waiting_chat(
    organization, storage, inline, django_capture_on_commit_callbacks, monkeypatch, case
):
    if case == "person_turn":
        ctx = _built(organization, django_capture_on_commit_callbacks)
        _finished(ctx)
        first = enqueue_message(ctx.job, content="hi", client_request_id="p0")[1]
        _sandbox(ctx, "sandbox-chat", after=ctx.build.sequence)
        _turn(ctx, first, checkpoint=True)
    else:
        ctx = _checked_with_files(organization, django_capture_on_commit_callbacks)
    if case != "nothing_waiting":
        _, waiting, _ = enqueue_message(
            ctx.job, content="run them", client_request_id="p1"
        )
        assert waiting.state == Message.State.QUEUED
        assert waiting.sequence > _conversation(ctx).command_acked_through

    reported = []
    monkeypatch.setattr(
        "simulate.services.hosted_harness_conversation.report_checkpoint_rejection",
        lambda conversation_id, message: reported.append((conversation_id, message)),
    )

    def landed(_conversation_id, _turn_sequence):
        return _publish(
            ctx, {"digest": ctx.digest, "sequence": ctx.checkpoint_sequence}
        )

    def refused(_conversation_id, _turn_sequence):
        raise HostedHarnessError(REFUSALS[case], "refused", status_code=409)

    monkeypatch.setattr(
        "simulate.services.hosted_harness_conversation.promote_turn_checkpoint",
        refused if case in REFUSALS else landed,
    )
    # The start reads the wait when it runs, so the files must have settled first.
    waits_at_start = []
    record = conversation_tasks.ensure_hosted_harness_conversation_runtime.apply_async

    def apply_async(*, args, **options):
        waits_at_start.append(platform_landing_pending(_job(ctx), ctx.conversation))
        record(args=args, **options)

    monkeypatch.setattr(
        conversation_tasks.ensure_hosted_harness_conversation_runtime,
        "apply_async",
        apply_async,
    )
    before = len(_names(inline, ENSURE))

    conversation_tasks.promote_hosted_harness_conversation_checkpoint._original_func(
        str(ctx.conversation.id), ctx.turn_sequence
    )

    started = _names(inline, ENSURE)[before:]
    if case in ("nothing_waiting", "person_turn"):
        assert started == []
        return
    assert started == [[str(ctx.conversation.id), URL]]
    assert waits_at_start == [False]
    assert reported == (
        [(str(ctx.conversation.id), "refused")] if case == "refused_reported" else []
    )
    if case in REFUSALS:
        entry = _job(ctx).payload["metadata"]["conversation_checkpoints"]
        assert entry[str(ctx.conversation.id)] == {
            "refused_sequence": ctx.checkpoint_sequence
        }
    _result, start, _delete = _ensure(ctx)
    start.assert_called_once()


@pytest.mark.parametrize("provider", ["present", "already_gone", "provider_down"])
def test_the_stop_step_deletes_the_sandbox(monkeypatch, caplog, provider):
    deleted = []

    class Provider:
        def get(self, provider_ref, request_timeout):
            del request_timeout
            if provider == "already_gone":
                raise SandboxNotFoundError("gone", status_code=404)
            return SimpleNamespace(id=provider_ref)

        def delete(self, handle, timeout, wait):
            del timeout, wait
            if provider == "provider_down":
                raise RuntimeError("provider down")
            deleted.append(handle.id)

    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.get_sandbox_provider",
        lambda: Provider(),
    )

    with caplog.at_level(logging.ERROR, logger="simulate.hosted_harness_gateway"):
        result = nudge_tasks.stop_harness_nudge_sandbox._original_func(
            str(uuid4()), "sandbox-check"
        )

    assert result == "stopped"
    assert deleted == (["sandbox-check"] if provider == "present" else [])
    failures = [
        record
        for record in caplog.records
        if "retired conversation sandbox deletion failed" in record.getMessage()
    ]
    assert len(failures) == (1 if provider == "provider_down" else 0)
