"""What a person's next chat holds after the build's check: the check's files, not the
ones from before it, and a Stop does not leave a question open that nothing can answer.

The cold starts here run the real composition (``chat_workspace_for`` over the stored
environment archive and the conversation's checkpoint) against a provider kept in
memory, and read the archive the new sandbox is given."""

from __future__ import annotations

import io
import json
import tarfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from simulate.models import (
    HostedHarnessAttempt,
    HostedHarnessConversationEvent,
    HostedHarnessJob,
)
from simulate.services import hosted_harness
from simulate.services.hosted_harness import canonical_digest
from simulate.services.hosted_harness_conversation import (
    PLATFORM_LANDING_WAIT_SECONDS,
    enqueue_message,
    environment_authoring_revision,
    give_up_platform_landing,
    platform_landing_pending,
    promote_turn_checkpoint,
    record_platform_landing_refused,
)
from simulate.services.hosted_harness_gateway import (
    HostedHarnessGateway,
    store_authoring_archive,
    with_authoring_basis,
)
from simulate.tasks import hosted_harness_conversation as conversation_tasks
from simulate.tests.test_harness_amend_archive import NAMES, _run_environment
from simulate.tests.test_hosted_harness_check_sandbox import (  # noqa: F401 - fixtures
    GUEST,
    STOP,
    URL,
    Conversation,
    Lease,
    Message,
    _checked,
    _conversation,
    _ensure,
    _job,
    _lease,
    _names,
    _post,
    _sandbox,
    inline,
    storage,
)
from simulate.tests.test_hosted_harness_conversation import (
    _chat_checkpoint,
    _commit_checkpoint,
)

PROMOTE = conversation_tasks.promote_hosted_harness_conversation_checkpoint.name
WORKSPACE = "/work/conversation.tar.gz"


@pytest.fixture(autouse=True)
def public_url(settings):
    settings.HARNESS_PUBLIC_BASE_URL = URL


class _Provider:
    """A sandbox provider kept in memory; each sandbox's uploads are kept by its id."""

    name = "daytona"
    runtime_name = "r"
    runtime_digest = "d"
    create_timeout_seconds = 300
    max_egress_domains = None

    def __init__(self):
        self.uploads = {}
        self.deleted = []

    def create(self, _spec, timeout):
        del timeout
        ref = f"sandbox-chat-{len(self.uploads) + 1}"
        files = self.uploads.setdefault(ref, {})
        return SimpleNamespace(
            id=ref,
            fs=SimpleNamespace(
                upload_file=lambda body, path: files.__setitem__(path, body)
            ),
            process=SimpleNamespace(
                exec=lambda command, timeout: SimpleNamespace(exit_code=0, result=""),
                create_session=lambda name: None,
                execute_session_command=lambda name, request: SimpleNamespace(
                    cmd_id="chat-command"
                ),
            ),
        )

    def get(self, provider_ref, request_timeout):
        del request_timeout
        return SimpleNamespace(id=provider_ref)

    def delete(self, target, *_args, **_kwargs):
        self.deleted.append(target.id)
        return True

    def renew_ttl(self, target, ttl_seconds):
        del target, ttl_seconds


@pytest.fixture
def provider(monkeypatch):
    """Only the source checkout and the egress lookup are stubbed; the workspace the
    sandbox gets is composed for real."""
    from simulate.services import hosted_harness_gateway as gateway_module

    monkeypatch.setattr(
        gateway_module.HostedSourceAcquirer, "acquire", lambda _self, _job: (b"", None)
    )
    monkeypatch.setattr(gateway_module, "_platform_simulator_material", lambda: {})
    monkeypatch.setattr(
        gateway_module, "_resolved_egress_domains", lambda *_args: {"a.example"}
    )
    return _Provider()


def _cold_start(ctx, provider):
    """A person's message starts chat: the archive the new sandbox is given."""
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = provider
    lease = gateway.ensure_conversation_runtime(
        _job(ctx), conversation=_conversation(ctx), endpoint_base_url=URL
    )
    assert lease is not None, "chat did not start"
    return provider.uploads[lease.provider_ref][WORKSPACE]


def _one_liner(archive):
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as files:
        return json.loads(files.extractfile("contract.json").read())["one_liner"]


def _checked_environment(user, workspace, capture, key):
    """A built environment whose files say "original"; the platform asked the check,
    and the check's own sandbox polled."""
    environment = _run_environment(user, workspace, key)
    store_authoring_archive(
        environment,
        _chat_checkpoint(NAMES[:2], one_liner="original"),
        advance_lifecycle=False,
    )
    attempt = HostedHarnessAttempt.no_workspace_objects.get(job=environment)
    conversation, build, _ = enqueue_message(
        environment,
        content="Build it",
        client_request_id=f"build-{attempt.id}",
        payload={"from_platform": True},
    )
    Message.no_workspace_objects.filter(id=build.id).update(
        state=Message.State.COMPLETED
    )
    Conversation.no_workspace_objects.filter(id=conversation.id).update(
        command_acked_through=build.sequence
    )
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        terminal_at=timezone.now() - timedelta(minutes=1)
    )
    ctx = SimpleNamespace(
        job=environment, conversation=conversation, build=build, capture=capture
    )
    hosted_harness._ask_builder_to_check(str(environment.id), str(attempt.id))
    ctx.check = Message.no_workspace_objects.get(
        conversation=conversation, client_request_id=f"check-{attempt.id}"
    )
    _sandbox(ctx, "sandbox-check", after=build.sequence)
    ctx.revision = _job(ctx).payload["metadata"]["authoring_revision"]
    return ctx


def _check_commits_its_files(ctx):
    """The check's turn starts and saves its workspace, which says "checked"."""
    ran = {"command_message_id": str(ctx.check.id)}
    _post(ctx, ctx.build.sequence, ("turn_started", ran))
    body = with_authoring_basis(
        _chat_checkpoint(NAMES[:2], one_liner="checked"), ctx.revision
    )
    committed = _commit_checkpoint(ctx.client, ctx.conversation, ctx.headers, body)
    _post(ctx, ctx.build.sequence, ("checkpoint_committed", committed))
    return ran


@pytest.mark.django_db
def test_a_replayed_check_turn_keeps_the_checks_files_for_the_next_chat(
    user, workspace, storage, inline, provider, django_capture_on_commit_callbacks
):
    ctx = _checked_environment(
        user, workspace, django_capture_on_commit_callbacks, "check-replayed"
    )
    ran = _check_commits_its_files(ctx)
    # The turn's own end never landed; the guest's journal says the command finished,
    # so it reports it again with no outcome (hosted_chat_entrypoint: "replayed").
    numbered, _ = _post(
        ctx, ctx.check.sequence, ("turn_completed", {**ran, "replayed": True})
    )
    turn_sequence = numbered[-1][0]
    assert _lease(ctx).state == Lease.State.EXPIRED
    assert _names(inline, STOP) == [[str(ctx.conversation.id), "sandbox-check"]]
    # The check's edits exist in the conversation's own checkpoint, and its promotion is
    # asked for; chat waits for it as it does for a turn that reported its end.
    saved = storage.objects[_conversation(ctx).latest_workspace_object_key]
    assert _one_liner(saved) == "checked"
    assert _names(inline, PROMOTE) == [[str(ctx.conversation.id), turn_sequence]]
    assert (
        platform_landing_pending(
            job=_job(ctx), conversation=_conversation(ctx)
        )
        is True
    )

    promote_turn_checkpoint(str(ctx.conversation.id), turn_sequence)
    enqueue_message(ctx.job, content="run them", client_request_id="p1")
    archive = _cold_start(ctx, provider)

    assert _one_liner(archive) == "checked"


@pytest.mark.django_db
def test_a_replayed_turn_the_person_stopped_is_not_published_and_chat_does_not_wait(
    user, workspace, storage, inline, provider, django_capture_on_commit_callbacks
):
    ctx = _checked_environment(
        user, workspace, django_capture_on_commit_callbacks, "check-replayed-stopped"
    )
    ran = _check_commits_its_files(ctx)
    interrupt = enqueue_message(
        ctx.job, content="", client_request_id="stop-it", kind="interrupt"
    )[1]
    _post(
        ctx,
        ctx.build.sequence,
        ("turn_interrupted", {**ran, "interrupt_message_id": str(interrupt.id)}),
    )
    # The turn's own end never landed; its replacement reports the command with no
    # outcome, which says nothing about how the turn went.
    _post(ctx, interrupt.sequence, ("turn_completed", {**ran, "replayed": True}))

    assert _names(inline, PROMOTE) == []
    assert (
        platform_landing_pending(
            job=_job(ctx), conversation=_conversation(ctx)
        )
        is False
    )
    enqueue_message(ctx.job, content="run them", client_request_id="p1")
    archive = _cold_start(ctx, provider)

    assert _one_liner(archive) == "original"


def _person_turn(ctx, command, one_liner):
    """The person's own turn in a chat sandbox: it saves its workspace and finishes.
    Returns the turn's sequence and the saved checkpoint's digest."""
    _sandbox(ctx, f"sandbox-person-{command.sequence}", after=ctx.check.sequence)
    ran = {"command_message_id": str(command.id)}
    acked = _conversation(ctx).command_acked_through
    _post(ctx, acked, ("turn_started", ran))
    body = with_authoring_basis(
        _chat_checkpoint(NAMES[:2], one_liner=one_liner), ctx.revision
    )
    committed = _commit_checkpoint(ctx.client, ctx.conversation, ctx.headers, body)
    _post(ctx, acked, ("checkpoint_committed", committed))
    numbered, _ = _post(
        ctx, command.sequence, ("turn_completed", {**ran, "outcome": "success"})
    )
    return numbered[-1][0], committed["digest"]


def _published(ctx):
    """The environment's record of this conversation's published checkpoint."""
    metadata = (_job(ctx).payload or {}).get("metadata") or {}
    return (metadata.get("conversation_checkpoints") or {}).get(
        str(ctx.conversation.id)
    ) or {}


def _lines(ctx, containing):
    return list(
        Message.no_workspace_objects.filter(
            conversation=ctx.conversation,
            role=Message.Role.ASSISTANT,
            content__contains=containing,
        )
    )


@pytest.mark.django_db
def test_chat_started_after_the_wait_does_not_keep_the_checks_files_and_fences_them(
    user, workspace, storage, inline, provider, django_capture_on_commit_callbacks
):
    ctx = _checked_environment(
        user, workspace, django_capture_on_commit_callbacks, "check-slow-publish"
    )
    ran = _check_commits_its_files(ctx)
    numbered, _ = _post(
        ctx, ctx.check.sequence, ("turn_completed", {**ran, "outcome": "success"})
    )
    turn_sequence = numbered[-1][0]
    checkpoint_sequence = numbered[-1][0] - 1
    assert _lease(ctx).state == Lease.State.EXPIRED
    # The promotion is asked for, and has not run: the environment still says
    # "original".
    assert _names(inline, PROMOTE) == [[str(ctx.conversation.id), turn_sequence]]
    assert (
        platform_landing_pending(
            job=_job(ctx), conversation=_conversation(ctx)
        )
        is True
    )
    waiting = enqueue_message(ctx.job, content="run them", client_request_id="p1")[1]

    # The publish has not settled 781 s after the turn: the wait lets chat go.
    HostedHarnessConversationEvent.no_workspace_objects.filter(
        conversation=ctx.conversation, sequence=turn_sequence
    ).update(
        created_at=timezone.now() - timedelta(seconds=PLATFORM_LANDING_WAIT_SECONDS + 1)
    )
    archive = _cold_start(ctx, provider)

    # Chat starts from the files before the check, the check's are fenced off, and the
    # person is told once that they were not kept.
    assert _one_liner(archive) == "original"
    assert _published(ctx).get("refused_sequence") == checkpoint_sequence
    assert len(_lines(ctx, "were not kept")) == 1
    assert (
        platform_landing_pending(
            job=_job(ctx), conversation=_conversation(ctx)
        )
        is False
    )

    # The late landing is dropped.
    revision = environment_authoring_revision(_job(ctx))
    promote_turn_checkpoint(str(ctx.conversation.id), turn_sequence)
    assert environment_authoring_revision(_job(ctx)) == revision == ctx.revision
    assert _published(ctx).get("digest") is None

    # The person's own save, made from the files chat started with, is not stale.
    person_turn, person_digest = _person_turn(ctx, waiting, "mine")
    promote_turn_checkpoint(str(ctx.conversation.id), person_turn)
    assert _published(ctx)["digest"] == person_digest
    assert _published(ctx)["refused_sequence"] == checkpoint_sequence
    assert environment_authoring_revision(_job(ctx)) != revision
    assert _lines(ctx, "were not saved") == []
    # Asking again after the give-up does not tell the person twice.
    assert len(_lines(ctx, "were not kept")) == 1


@pytest.mark.django_db
def test_a_refusal_survives_a_newer_checkpoint_being_published(
    user, workspace, storage, inline, provider, django_capture_on_commit_callbacks
):
    ctx = _checked_environment(
        user, workspace, django_capture_on_commit_callbacks, "check-refused-kept"
    )
    ran = _check_commits_its_files(ctx)
    numbered, _ = _post(
        ctx, ctx.check.sequence, ("turn_completed", {**ran, "outcome": "success"})
    )
    turn_sequence = numbered[-1][0]
    record_platform_landing_refused(
        conversation_id=str(ctx.conversation.id), turn_sequence=turn_sequence
    )
    refused = _published(ctx)["refused_sequence"]
    assert refused == turn_sequence - 1
    person = enqueue_message(ctx.job, content="run them", client_request_id="p1")[1]

    person_turn, person_digest = _person_turn(ctx, person, "mine")
    promote_turn_checkpoint(str(ctx.conversation.id), person_turn)

    assert _published(ctx)["digest"] == person_digest
    assert _published(ctx)["refused_sequence"] == refused


def _events(ctx, acknowledged, *events):
    """The guest's events, each with its own message id where it has one."""
    first = _conversation(ctx).event_acked_through + 1
    signed = []
    for offset, (kind, payload, message_id) in enumerate(events):
        event = {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "event_id": f"ce-{ctx.conversation.id}-{first + offset}",
            "conversation_id": str(ctx.conversation.id),
            "sequence": first + offset,
            "kind": kind,
            "message_id": message_id,
            "stage": "scenarios",
            "invocation_id": "turn-1",
            "function_call_id": None,
            "emitted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "payload": payload,
        }
        event["digest"] = canonical_digest(event)
        signed.append(event)
    with ctx.capture(execute=True):
        response = ctx.client.post(
            f"{GUEST}/{ctx.conversation.id}/events/",
            {
                "schema_version": "futureagi.harness-conversation-event.v1",
                "acknowledged_through": acknowledged,
                "events": signed,
            },
            format="json",
            **ctx.headers,
        )
    assert response.status_code == 200, response.content


@pytest.mark.django_db
def test_a_stop_with_no_sandbox_does_not_leave_the_question_open(
    organization, inline, django_capture_on_commit_callbacks
):
    ctx = _checked(organization, django_capture_on_commit_callbacks)
    ran = {"command_message_id": str(ctx.check.id)}
    question = str(uuid4())
    _events(ctx, ctx.build.sequence, ("turn_started", ran, None))
    _events(
        ctx,
        ctx.build.sequence,
        ("question_requested", {"prompt": "Which agent?", "options": []}, question),
    )
    # The turn ends with the question unanswered: nothing in a sandbox waits on it now.
    _events(
        ctx, ctx.check.sequence, ("turn_completed", {**ran, "outcome": "failed"}, None)
    )
    assert _conversation(ctx).blocking_input["message_id"] == question
    assert _lease(ctx).state == Lease.State.ACTIVE
    # The kept sandbox idles out.
    Lease.no_workspace_objects.filter(conversation=ctx.conversation).update(
        heartbeat_at=timezone.now() - timedelta(minutes=31),
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    stop = enqueue_message(
        ctx.job, content="", client_request_id="stop-it", kind="interrupt"
    )[1]

    _result, start, _delete = _ensure(ctx)

    stop.refresh_from_db()
    conversation = _conversation(ctx)
    assert conversation.blocking_input is None, {
        "state": conversation.state,
        "blocking_input": conversation.blocking_input,
        "stop": stop.state,
        "sandbox_started": start.called,
    }


def _members(archive):
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as files:
        return {
            member.name: files.extractfile(member).read()
            for member in files.getmembers()
            if member.isfile()
        }


@pytest.mark.django_db
def test_the_next_chat_is_given_the_checks_files_and_the_chats_own_state(
    user, workspace, storage, inline, provider, django_capture_on_commit_callbacks
):
    ctx = _checked_environment(
        user, workspace, django_capture_on_commit_callbacks, "check-lifecycle"
    )
    # The check edits a file and drops a scenario, then its turn ends well.
    ran = {"command_message_id": str(ctx.check.id)}
    _post(ctx, ctx.build.sequence, ("turn_started", ran))
    body = with_authoring_basis(
        _chat_checkpoint(NAMES[:1], one_liner="checked"), ctx.revision
    )
    committed = _commit_checkpoint(ctx.client, ctx.conversation, ctx.headers, body)
    _post(ctx, ctx.build.sequence, ("checkpoint_committed", committed))
    numbered, _ = _post(
        ctx, ctx.check.sequence, ("turn_completed", {**ran, "outcome": "success"})
    )
    turn_sequence = numbered[-1][0]
    assert _names(inline, PROMOTE) == [[str(ctx.conversation.id), turn_sequence]]

    # Its promotion lands, then the person writes and chat cold-starts.
    promote_turn_checkpoint(str(ctx.conversation.id), turn_sequence)
    assert _published(ctx)["digest"] == committed["digest"]
    enqueue_message(ctx.job, content="run them", client_request_id="p1")
    files = _members(_cold_start(ctx, provider))

    # The check's file content, and its removal of a scenario, are what chat starts
    # from.
    assert json.loads(files["contract.json"]) == {"one_liner": "checked"}
    assert f"scenarios/{NAMES[0]}/scenario.json" in files
    assert not [name for name in files if name.startswith(f"scenarios/{NAMES[1]}/")]
    assert [one["name"] for one in json.loads(files["scenarios.json"])] == list(
        NAMES[:1]
    )
    # The chat's own saved state comes from its checkpoint.
    assert files[".claude/sessions/1.key"] == b"private"
    assert files[".futureagi-conversation.json"] == b"{}"


@pytest.mark.django_db
def test_a_promotion_that_lands_during_the_give_up_is_not_fenced_or_reported(
    user, workspace, storage, inline, provider, django_capture_on_commit_callbacks,
    monkeypatch,
):
    from simulate.services import hosted_harness_conversation as conversation_module

    ctx = _checked_environment(
        user, workspace, django_capture_on_commit_callbacks, "check-lands-in-give-up"
    )
    ran = _check_commits_its_files(ctx)
    numbered, _ = _post(
        ctx, ctx.check.sequence, ("turn_completed", {**ran, "outcome": "success"})
    )
    turn_sequence = numbered[-1][0]
    HostedHarnessConversationEvent.no_workspace_objects.filter(
        conversation=ctx.conversation, sequence=turn_sequence
    ).update(
        created_at=timezone.now() - timedelta(seconds=PLATFORM_LANDING_WAIT_SECONDS + 1)
    )
    real = conversation_module.record_platform_landing_refused

    def promotion_commits_first(**kwargs):
        # The give-up has read "unsettled" and is about to take the environment's lock;
        # the check's promotion commits in between.
        promote_turn_checkpoint(str(ctx.conversation.id), turn_sequence)
        return real(**kwargs)

    monkeypatch.setattr(
        conversation_module, "record_platform_landing_refused", promotion_commits_first
    )

    gave_up = give_up_platform_landing(job=_job(ctx), conversation=_conversation(ctx))

    assert gave_up is False
    assert _lines(ctx, "were not kept") == []
    assert "refused_sequence" not in _published(ctx)
    assert _published(ctx)["digest"] is not None


@pytest.mark.django_db
def test_a_check_is_not_fenced_once_a_newer_checkpoint_is_published(
    user, workspace, storage, inline, provider, django_capture_on_commit_callbacks
):
    ctx = _checked_environment(
        user, workspace, django_capture_on_commit_callbacks, "check-newer-published"
    )
    ran = _check_commits_its_files(ctx)
    numbered, _ = _post(
        ctx, ctx.check.sequence, ("turn_completed", {**ran, "outcome": "success"})
    )
    turn_sequence = numbered[-1][0]
    person = enqueue_message(ctx.job, content="run them", client_request_id="p1")[1]
    person_turn, person_digest = _person_turn(ctx, person, "mine")
    promote_turn_checkpoint(str(ctx.conversation.id), person_turn)
    assert _published(ctx)["digest"] == person_digest

    recorded = record_platform_landing_refused(
        conversation_id=str(ctx.conversation.id), turn_sequence=turn_sequence
    )

    assert recorded is False
    assert "refused_sequence" not in _published(ctx)


def _event(ctx, sequence, kind, payload):
    return HostedHarnessConversationEvent.no_workspace_objects.create(
        event_id=f"{ctx.conversation.id}:{sequence}",
        conversation=ctx.conversation,
        sequence=sequence,
        kind=kind,
        payload=payload,
        digest=f"sha256:{sequence}",
        emitted_at=timezone.now(),
        invocation_id="turn-9",
    )


# Past the sequences the helpers above post.
LATER = 900


def _replayed_turn_history(ctx, *earlier):
    """The guest's events for the check's command, then its replayed end; whether
    that end counts as finished well."""
    from simulate.services.hosted_harness_conversation import _turn_finished_well

    ran = {"command_message_id": str(ctx.check.id)}
    for offset, (kind, extra) in enumerate(earlier):
        _event(ctx, LATER + offset, kind, {**ran, **extra})
    end = _event(
        ctx, LATER + len(earlier), "turn_completed", {**ran, "replayed": True}
    )
    return _turn_finished_well(end)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("earlier", "finished_well"),
    [
        ((), True),
        # An earlier end that reported no outcome at all went badly.
        ((("turn_completed", {}),), False),
        ((("turn_completed", {"outcome": "failed"}),), False),
        ((("turn_completed", {"outcome": "success"}),), True),
        # An earlier replay with no outcome says nothing either way.
        ((("turn_completed", {"replayed": True}),), True),
        ((("turn_interrupted", {}),), False),
    ],
    ids=[
        "nothing_before",
        "end_with_no_outcome",
        "failed_end",
        "successful_end",
        "earlier_replay",
        "interrupted",
    ],
)
def test_a_replayed_turn_is_judged_on_the_ends_before_it(
    user, workspace, storage, inline, django_capture_on_commit_callbacks,
    earlier, finished_well,
):
    ctx = _checked_environment(
        user, workspace, django_capture_on_commit_callbacks, "check-judged"
    )

    assert _replayed_turn_history(ctx, *earlier) is finished_well


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("history", "finished_well"),
    [
        # Stopped, then run again in full from the start, then reported again.
        (
            (("turn_started", {}), ("turn_interrupted", {}), ("turn_started", {})),
            True,
        ),
        # Stopped, and never started again: the replay is of the stopped run.
        ((("turn_started", {}), ("turn_interrupted", {})), False),
        # Failed in its first run, resumed, and failed again before the replay.
        (
            (
                ("turn_started", {}),
                ("turn_completed", {"outcome": "failed"}),
                ("turn_started", {}),
                ("turn_completed", {}),
            ),
            False,
        ),
    ],
    ids=["resumed_in_full", "never_resumed", "bad_end_in_the_latest_run"],
)
def test_a_replayed_turn_is_judged_on_its_latest_run_only(
    user, workspace, storage, inline, django_capture_on_commit_callbacks,
    history, finished_well,
):
    ctx = _checked_environment(
        user, workspace, django_capture_on_commit_callbacks, "check-latest-run"
    )

    assert _replayed_turn_history(ctx, *history) is finished_well


def test_a_checkpoint_entry_from_before_the_refusal_mark_still_parses():
    from simulate.services.hosted_harness_conversation import (
        parse_checkpoint_entry,
    )

    legacy = {
        "digest": "sha256:" + "1" * 64,
        "sequence": 4,
        "content_revision": "r1",
        "draft_basis": {"token": "t", "environment": {"id": "d", "revision": 2}},
    }

    assert parse_checkpoint_entry(legacy) == legacy
    assert parse_checkpoint_entry({**legacy, "refused_sequence": 7}) == {
        **legacy,
        "refused_sequence": 7,
    }
    assert parse_checkpoint_entry({"refused_sequence": "9"}) == {"refused_sequence": 9}
    assert parse_checkpoint_entry(None) == {}
    assert parse_checkpoint_entry("not an entry") == {}


@pytest.mark.django_db
def test_the_landing_functions_take_their_arguments_by_keyword(
    user, workspace, storage, inline, django_capture_on_commit_callbacks
):
    ctx = _checked_environment(
        user, workspace, django_capture_on_commit_callbacks, "check-keywords"
    )
    for call, args in (
        (platform_landing_pending, (ctx.job, ctx.conversation)),
        (give_up_platform_landing, (ctx.job, ctx.conversation)),
        (record_platform_landing_refused, (str(ctx.conversation.id), 1)),
    ):
        with pytest.raises(TypeError):
            call(*args)
