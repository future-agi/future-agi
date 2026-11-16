"""Run readiness Fix driver: dispatch into the builder chat, following its turns,
settling commands, deadlines, and the guards against rebuilds and default moves."""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from simulate.models import (
    HarnessNudge,
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationLease,
    HostedHarnessConversationMessage,
    ReadinessFixRun,
)
from simulate.services import hosted_harness_conversation as hhc
from simulate.services.harness_readiness import fix_driver, fix_runs, fix_save
from simulate.services.harness_versioning import drafts, rebuild
from simulate.services.hosted_harness import HostedHarnessError, canonical_digest
from simulate.tasks import harness_nudges as nudge_tasks
from simulate.tasks import hosted_harness_conversation as conversation_tasks
from simulate.tests.test_harness_readiness import BASE
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixtures
    readiness_on as readiness_on,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_readiness_fix_run import (  # noqa: F401 - fixture
    _broken,
    _draft,
    _run,
)
from simulate.tests.test_harness_readiness_fix_run import (
    fix_on as fix_on,
)
from simulate.tests.test_harness_versioning_assembly import _sealed_authoring
from simulate.tests.test_hosted_harness_conversation import (
    _put_conversation_checkpoint,
)

pytestmark = pytest.mark.django_db

Message = HostedHarnessConversationMessage
Event = HostedHarnessConversationEvent
LONG_AGO = timedelta(hours=25)
REFUND = {"agent": "support", "tools": [{"name": "refund_order"}]}


@pytest.fixture
def tasks(monkeypatch):
    """Activities and runtime starts are recorded, not run."""
    calls = []

    def record(task):
        def apply_async(*, args, **options):
            calls.append((task.name, list(args), options.get("task_id")))

        monkeypatch.setattr(task, "apply_async", apply_async)

    for task in (
        nudge_tasks.check_harness_readiness_fix,
        nudge_tasks.check_harness_nudges,
        nudge_tasks.stop_harness_nudge_sandbox,
        conversation_tasks.ensure_hosted_harness_conversation_runtime,
        conversation_tasks.promote_hosted_harness_conversation_checkpoint,
    ):
        record(task)
    return calls


def _names(calls, name):
    return [args for task, args, _id in calls if task == name]


def _start(environment, user, rows=("state",), key=None):
    return fix_runs.start_or_append(
        environment,
        group="builder",
        rows=list(rows),
        idempotency_key=key or uuid.uuid4().hex,
        picks={},
        user=user,
    )


def _dispatched(user, workspace, storage, key, rows=("state",)):
    """A run whose first command is in the idle chat."""
    environment = _broken(user, workspace, storage, key)
    _start(environment, user, rows)
    assert fix_driver.dispatch(environment.id, start_runtime=False)
    run = _run(environment)
    message = Message.no_workspace_objects.get(id=run.current_command_id)
    return environment, run, message, message.conversation


def _feed(conversation, capture, *events, execute=True, acked=None, emitted=None):
    """Ingest ``(kind, payload[, message_id])`` events from a live chat sandbox; the
    callbacks after commit run unless ``execute`` is false. The chat takes every
    message unless ``acked`` says up to which; ``emitted`` dates the events."""
    hhc.issue_conversation_capability(
        conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="sandbox-fix",
        attempt=None,
        ttl_seconds=600,
    )
    conversation.refresh_from_db()
    sequence = conversation.event_acked_through
    signed = []
    for kind, payload, *rest in events:
        sequence += 1
        event = {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "event_id": f"ce-{conversation.id}-{sequence}",
            "conversation_id": str(conversation.id),
            "sequence": sequence,
            "kind": kind,
            "message_id": str(rest[0]) if rest else None,
            "stage": "scenarios",
            "invocation_id": "turn-1",
            "function_call_id": None,
            "emitted_at": (emitted or datetime.now(UTC))
            .isoformat()
            .replace("+00:00", "Z"),
            "payload": payload,
        }
        event["digest"] = canonical_digest(event)
        signed.append(event)
    if acked is None:
        acked = (
            conversation.messages.filter(role=Message.Role.USER)
            .order_by("-sequence")
            .values_list("sequence", flat=True)
            .first()
            or 0
        )
    with capture(execute=execute):
        hhc.ingest_conversation_events(
            conversation, acknowledged_through=acked, events=signed
        )
    return sequence


def _started(message):
    return ("turn_started", {"command_message_id": str(message.id)})


def _completed(message, outcome="success", **extra):
    return (
        "turn_completed",
        {"command_message_id": str(message.id), "outcome": outcome, **extra},
    )


def _asked(prompt="Which table holds the orders?"):
    return ("question_requested", {"prompt": prompt}, uuid.uuid4())


def _answer(environment, question_id, text="orders"):
    return hhc.enqueue_message(
        environment,
        content=text,
        client_request_id=uuid.uuid4().hex,
        reply_to=question_id,
    )[1]


def _question_id(conversation):
    conversation.refresh_from_db()
    return conversation.blocking_input["message_id"]


def _lines(run):
    run.refresh_from_db()
    return [(line["row_id"], line["state"]) for line in run.lines]


def _publish(conversation, sequence):
    return hhc.promote_turn_checkpoint(str(conversation.id), sequence)


def _age(message, delta, field="created_at"):
    Message.no_workspace_objects.filter(id=message.id).update(
        **{field: timezone.now() - delta}
    )


def _busy_since(run, index, ago):
    run.refresh_from_db()
    run.commands[index]["busy_since"] = (timezone.now() - ago).isoformat()
    run.save(update_fields=["commands"])


@contextmanager
def _retires():
    """The environments whose chat sandbox was given up."""
    retired = []
    original = rebuild._after_close

    def after_close(environment_id, *, dispatched):
        retired.append(environment_id)
        return original(environment_id, dispatched=dispatched)

    with patch.object(rebuild, "_after_close", side_effect=after_close):
        yield retired


def _checkpoint(environment, conversation, message, storage, basis=None):
    """A checkpoint of a turn that adds a tool on the drafts the chat composed."""
    from simulate.services.hosted_harness_gateway import (
        with_authoring_basis,
        with_draft_basis,
    )

    token = drafts.compose_workspace(environment, conversation).token
    body = with_draft_basis(
        with_authoring_basis(
            _sealed_authoring(contract=REFUND),
            basis or message.payload["_environment_revision"],
        ),
        token,
    )
    return _put_conversation_checkpoint(conversation, storage, body)


def _promote(conversation, message, key, digest, capture):
    with capture(execute=True):
        return hhc.promote_conversation_checkpoint(
            conversation,
            object_key=key,
            digest=digest,
            sequence=10,
            expected_revision=message.payload["_environment_revision"],
            command=message,
        )


def _interrupted_long_ago(run):
    """The turn started 30 minutes ago and was interrupted 16 minutes ago."""
    now = timezone.now()
    run.refresh_from_db()
    run.commands[0]["interrupted_at"] = (now - timedelta(minutes=16)).isoformat()
    run.save(update_fields=["commands"])
    events = Event.no_workspace_objects.filter(conversation__job_id=run.job_id)
    events.filter(kind="turn_started").update(created_at=now - timedelta(minutes=30))
    events.filter(kind="turn_interrupted").update(
        created_at=now - timedelta(minutes=16)
    )


def _lease_expired(conversation):
    return (
        not HostedHarnessConversationLease.no_workspace_objects.filter(
            conversation=conversation
        )
        .exclude(state="expired")
        .exists()
    )


class TestDispatch:
    def test_an_idle_chat_gets_one_user_message(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment = _broken(user, workspace, storage, "fd-idle")
        run = _start(environment, user, ["state", "tool_routing"])
        before = run.state_changed_at

        assert fix_driver.dispatch(environment.id, start_runtime=False)

        run.refresh_from_db()
        [message] = Message.no_workspace_objects.filter(
            conversation__job=environment, role="user"
        )
        assert message.content == run.commands[0]["message"]
        assert message.payload["command_kind"] == "user_message"
        assert message.payload["_fix_run_id"] == str(run.id)
        assert message.payload["_fix_command"] == 1
        assert message.payload["_author_id"] == str(user.id)
        [sent] = hhc.pending_commands(message.conversation, after=0)["commands"]
        assert sent["kind"] == "user_message"
        assert not any(key.startswith("_") for key in sent["payload"])
        assert (run.state, run.current_command_id) == ("fixing", message.id)
        assert run.commands[0]["state"] == "dispatched"
        assert run.commands[0]["message_id"] == str(message.id)
        assert {line["state"] for line in run.lines} == {"fixing"}
        assert run.state_changed_at > before
        assert fix_runs.serialize(run, environment)["queue"] == []
        # Nothing more to send while it is in flight.
        assert not fix_driver.dispatch(environment.id, start_runtime=False)

    def test_a_busy_chat_or_open_question_waits(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment = _broken(user, workspace, storage, "fd-busy")
        _start(environment, user)
        mine = hhc.enqueue_message(
            environment, content="mine", client_request_id="mine"
        )[1]
        conversation = mine.conversation

        # The person's own message is unanswered.
        assert not fix_driver.dispatch(environment.id, start_runtime=False)
        _feed(conversation, django_capture_on_commit_callbacks, _started(mine))
        assert not fix_driver.dispatch(environment.id, start_runtime=False)
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _asked(),
            _completed(mine),
        )
        # A question the person has not answered holds the chat.
        conversation.refresh_from_db()
        assert conversation.state == "waiting_for_user"
        assert not fix_driver.dispatch(environment.id, start_runtime=False)
        assert _run(environment).state == "queued"
        # A question in the person's own turn is not the fix's.
        assert _lines(_run(environment)) == [("state", "queued")]

    def test_the_next_command_waits_until_the_first_settles(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-one"
        )
        _start(environment, user, ["tool_routing"])
        assert not fix_driver.dispatch(environment.id, start_runtime=False)
        assert len(fix_runs.serialize(_run(environment), environment)["queue"]) == 1

        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message),
        )
        _publish(conversation, sequence)

        assert fix_driver.dispatch(environment.id, start_runtime=False)
        run.refresh_from_db()
        assert [one["state"] for one in run.commands] == ["settled", "dispatched"]
        assert _lines(run) == [("state", "fixing"), ("tool_routing", "fixing")]

    def test_a_row_less_command_is_never_sent(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-rowless"
        )
        # ``reset`` waits on ``state``: its command carries nothing.
        _start(environment, user, ["reset"])
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message),
        )
        _publish(conversation, sequence)

        with patch.object(fix_save, "advance") as advance:
            fix_driver.check(environment.id)

        assert (
            Message.no_workspace_objects.filter(
                conversation=conversation, role="user"
            ).count()
            == 1
        )
        run.refresh_from_db()
        assert _lines(run) == [("state", "fixing"), ("reset", "fixing")]
        # Every command settled: autosave takes the run over.
        assert run.state == "fixing"
        assert [call.args[0].id for call in advance.call_args_list] == [run.id]


class TestTurnEvents:
    def test_a_repeated_start_changes_nothing(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-start"
        )
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        run.refresh_from_db()
        changed, started = run.state_changed_at, run.commands[0]["started_at"]
        assert started

        fix_driver.on_conversation_event(
            str(conversation.id), *_started(message), 9, None
        )

        run.refresh_from_db()
        assert (run.state_changed_at, run.commands[0]["started_at"]) == (
            changed,
            started,
        )

    def test_a_failed_turn_could_not_fix_and_gives_the_sandbox_up(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-failed"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message, "failed"),
        )

        run.refresh_from_db()
        assert run.commands[0]["outcome"] == {
            "turn": "failed",
            "code": "turn_failed",
            "wrote": False,
        }
        assert run.lines[0]["state"] == "could_not_fix"
        assert run.lines[0]["reason"]["code"] == "turn_failed"
        assert _lease_expired(conversation)
        assert _names(tasks, "check_harness_readiness_fix") == [[str(environment.id)]]
        # Delivered again: nothing moves.
        changed = run.state_changed_at
        fix_driver.on_conversation_event(
            str(conversation.id), *_completed(message, "failed"), 9, None
        )
        run.refresh_from_db()
        assert run.state_changed_at == changed

    def test_a_turn_with_no_checkpoint_settles_without_a_write(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-nowrite"
        )
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message),
        )
        run.refresh_from_db()
        assert run.commands[0]["state"] == "dispatched"

        with django_capture_on_commit_callbacks(execute=True):
            _publish(conversation, sequence)

        run.refresh_from_db()
        assert run.commands[0]["outcome"] == {
            "turn": "success",
            "code": None,
            "wrote": False,
        }
        assert _lines(run) == [("state", "fixing")]
        assert run.current_command_id is None
        # A second publish of the same turn is a no-op.
        changed = run.state_changed_at
        _publish(conversation, sequence)
        run.refresh_from_db()
        assert run.state_changed_at == changed

    def test_a_turn_that_writes_the_drafts_settles_with_a_write(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        from simulate.services.hosted_harness_gateway import (
            with_authoring_basis,
            with_draft_basis,
        )

        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-write"
        )
        token = drafts.compose_workspace(environment, conversation).token
        body = with_draft_basis(
            with_authoring_basis(
                _sealed_authoring(contract=REFUND),
                message.payload["_environment_revision"],
            ),
            token,
        )
        _key, digest = _put_conversation_checkpoint(conversation, storage, body)
        refs = drafts.draft_refs(environment)
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            ("checkpoint_committed", {"digest": digest, "size": len(body)}),
            _completed(message),
        )

        with django_capture_on_commit_callbacks(execute=True):
            _publish(conversation, sequence)

        run.refresh_from_db()
        assert drafts.draft_refs(environment) != refs
        assert run.commands[0]["outcome"]["wrote"] is True
        assert _lines(run) == [("state", "fixing")]

    def test_a_refused_publish_could_not_fix(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-refused"
        )
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            ("checkpoint_committed", {"digest": "sha256:" + "0" * 64, "size": 1}),
            _completed(message),
        )
        refused = HostedHarnessError(
            "conversation_checkpoint_stale", "stale", status_code=409
        )

        with (
            patch.object(hhc, "promote_conversation_checkpoint", side_effect=refused),
            pytest.raises(HostedHarnessError),
        ):
            _publish(conversation, sequence)

        run.refresh_from_db()
        assert run.commands[0]["outcome"]["turn"] == "publish_refused"
        assert run.lines[0]["state"] == "could_not_fix"
        assert run.lines[0]["reason"] == {
            "code": "conversation_checkpoint_stale",
            "message": "The builder's change could not be kept",
        }

    def test_an_interrupted_turn_not_started_again_could_not_fix(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-interrupt"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            ("turn_interrupted", {"command_message_id": str(message.id)}),
        )
        run.refresh_from_db()
        assert run.commands[0]["interrupted_at"]
        assert run.lines[0]["state"] == "fixing"
        fix_driver.check(environment.id)
        assert _run(environment).commands[0]["state"] == "dispatched"
        _interrupted_long_ago(run)

        fix_driver.check(environment.id)

        run.refresh_from_db()
        assert run.commands[0]["outcome"]["code"] == "turn_interrupted"
        assert run.lines[0]["reason"]["code"] == "turn_interrupted"

    def test_an_interrupted_turn_started_again_runs_on(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-restart"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            ("turn_interrupted", {"command_message_id": str(message.id)}),
            _started(message),
        )

        run.refresh_from_db()
        assert run.commands[0]["interrupted_at"] is None
        fix_driver.check(environment.id)
        assert _run(environment).commands[0]["state"] == "dispatched"

    def test_a_question_waits_on_the_answer_through_two_rounds(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-question"
        )
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked("Which table?"),
            _completed(message),
        )
        run.refresh_from_db()
        assert run.state == "waiting_on_you"
        assert run.lines[0]["state"] == "waiting_on_you"
        assert run.lines[0]["reason"] == {
            "code": "builder_question",
            "message": "Which table?",
        }
        # The turn's partial work publishes; the command is not settled.
        _publish(conversation, sequence)
        assert _run(environment).commands[0]["state"] == "dispatched"
        assert not fix_driver.dispatch(environment.id, start_runtime=False)

        for round_, last in ((1, False), (2, True)):
            reply = _answer(environment, _question_id(conversation))
            fix_driver.check(environment.id)
            assert _run(environment).state == "fixing"
            events = [_started(reply)]
            if not last:
                events.append(_asked(f"Round {round_}?"))
            events.append(_completed(reply))
            sequence = _feed(conversation, django_capture_on_commit_callbacks, *events)
            run.refresh_from_db()
            assert run.current_command_id == reply.id
            if not last:
                assert run.state == "waiting_on_you"
                assert run.lines[0]["reason"]["message"] == f"Round {round_}?"
            _publish(conversation, sequence)

        run.refresh_from_db()
        assert run.state == "fixing"
        assert run.commands[0]["outcome"]["turn"] == "success"
        assert _lines(run) == [("state", "fixing")]

    def test_a_missed_question_event_is_found_by_the_check(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-missed"
        )
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        _feed(conversation, django_capture_on_commit_callbacks, _asked(), execute=False)
        assert _run(environment).state == "fixing"

        fix_driver.check(environment.id)

        assert _run(environment).state == "waiting_on_you"

    def test_a_question_after_the_command_settled_is_ignored(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-late-question"
        )
        _start(environment, user, ["tool_routing"])
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message, "failed"),
        )
        # Asked by the settled command's turn, before the next one went out.
        _feed(conversation, django_capture_on_commit_callbacks, _asked())

        run.refresh_from_db()
        assert run.state == "fixing"
        assert _lines(run) == [("state", "could_not_fix"), ("tool_routing", "queued")]

    def test_a_late_write_after_the_run_ended_is_dropped_and_retired_once(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-late"
        )
        key, digest = _checkpoint(environment, conversation, message, storage)
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        ReadinessFixRun.no_workspace_objects.filter(id=run.id).update(state="failed")
        refs = drafts.draft_refs(environment)

        with _retires() as retired:
            for _ in range(2):  # re-promoted: still dropped, retired once
                _promote(
                    conversation,
                    message,
                    key,
                    digest,
                    django_capture_on_commit_callbacks,
                )

        assert drafts.draft_refs(environment) == refs
        assert retired == [str(environment.id)]
        assert _lease_expired(conversation)
        assert _names(tasks, "stop_harness_nudge_sandbox")

    def test_a_late_write_after_a_give_up_is_dropped_without_a_second_retire(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-late-give-up"
        )
        key, digest = _checkpoint(environment, conversation, message, storage)
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            ("turn_interrupted", {"command_message_id": str(message.id)}),
        )
        _interrupted_long_ago(run)
        refs = drafts.draft_refs(environment)

        with _retires() as retired:
            with django_capture_on_commit_callbacks(execute=True):
                fix_driver.check(environment.id)
            _promote(
                conversation, message, key, digest, django_capture_on_commit_callbacks
            )

        run.refresh_from_db()
        # Nothing reached a draft, so autosave had nothing to save.
        assert (run.state, run.error["code"]) == ("failed", "no_changes")
        assert run.commands[0]["outcome"]["code"] == "turn_interrupted"
        assert drafts.draft_refs(environment) == refs
        assert retired == [str(environment.id)]


class TestDeadlines:
    def test_a_command_never_taken_is_not_started(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, _conversation = _dispatched(
            user, workspace, storage, "fd-queued"
        )
        fix_driver.check(environment.id)
        assert _run(environment).commands[0]["state"] == "dispatched"
        _age(message, timedelta(minutes=16))

        fix_driver.check(environment.id)

        run.refresh_from_db()
        assert run.commands[0]["outcome"]["turn"] == "not_started"
        assert run.lines[0]["state"] == "could_not_fix"
        message.refresh_from_db()
        assert message.state == "failed"

    def test_a_command_taken_but_never_started_is_not_started(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, _conversation = _dispatched(
            user, workspace, storage, "fd-taken"
        )
        Message.no_workspace_objects.filter(id=message.id).update(state="delivered")
        fix_driver.check(environment.id)
        assert _run(environment).commands[0]["state"] == "dispatched"
        _age(message, timedelta(minutes=16), "updated_at")

        fix_driver.check(environment.id)

        assert _run(environment).commands[0]["outcome"]["turn"] == "not_started"

    def test_a_turn_with_no_sandbox_for_an_hour_is_stuck(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-stuck"
        )
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        HostedHarnessConversationLease.no_workspace_objects.filter(
            conversation=conversation
        ).update(state="expired")
        Event.no_workspace_objects.filter(
            conversation=conversation, kind="turn_started"
        ).update(created_at=timezone.now() - timedelta(minutes=61))

        fix_driver.check(environment.id)

        run.refresh_from_db()
        assert run.commands[0]["outcome"]["turn"] == "stuck"
        message.refresh_from_db()
        assert message.state == "failed"

    def test_a_lost_landing_is_retried_then_given_up(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-landing"
        )
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message),
        )
        ended = Event.no_workspace_objects.filter(
            conversation=conversation, sequence=sequence
        )
        ended.update(created_at=timezone.now() - timedelta(minutes=6))
        with patch.object(hhc, "promote_turn_checkpoint") as retried:
            fix_driver.check(environment.id)
        retried.assert_called_once_with(str(conversation.id), sequence)
        ended.update(created_at=timezone.now() - timedelta(minutes=16))

        fix_driver.check(environment.id)

        assert _run(environment).commands[0]["outcome"]["code"] == "publish_failed"

    def test_a_question_left_a_day_is_given_up(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-unanswered"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked(),
            _completed(message),
        )
        Message.no_workspace_objects.filter(id=_question_id(conversation)).update(
            created_at=timezone.now() - LONG_AGO
        )

        fix_driver.check(environment.id)

        run.refresh_from_db()
        assert run.commands[0]["outcome"]["code"] == "question_unanswered"
        assert run.lines[0]["state"] == "could_not_fix"

    def test_a_chat_busy_for_half_an_hour_fails_the_run(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment = _broken(user, workspace, storage, "fd-chat-busy")
        run = _start(environment, user)
        hhc.enqueue_message(environment, content="mine", client_request_id="mine")
        fix_driver.check(environment.id)
        run.refresh_from_db()
        assert run.state == "queued"
        assert run.commands[0]["busy_since"]
        _busy_since(run, 0, timedelta(minutes=31))

        fix_driver.check(environment.id)

        run.refresh_from_db()
        assert (run.state, run.error["code"]) == ("failed", "chat_busy")
        assert run.finished_at is not None
        assert run.lines[0]["state"] == "could_not_fix"

    def test_a_chat_busy_run_with_a_draft_ends_unsaved(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-busy-draft"
        )
        _start(environment, user, ["tool_routing"])
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message),
        )
        _publish(conversation, sequence)
        _draft(environment.environment_version)
        hhc.enqueue_message(environment, content="mine", client_request_id="mine")
        fix_driver.check(environment.id)
        run.refresh_from_db()
        _busy_since(run, 1, timedelta(minutes=31))

        fix_driver.check(environment.id)

        run.refresh_from_db()
        assert (run.state, run.error["code"]) == ("done", "chat_busy")
        assert run.lines[0]["state"] == "waiting_on_you"
        assert run.lines[0]["reason"]["code"] == "draft_unsaved"
        assert run.lines[1]["state"] == "could_not_fix"
        assert fix_runs.serialize(run, environment)["draft"] is True

    def test_a_check_failing_for_ten_minutes_ends_the_run(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment = _broken(user, workspace, storage, "fd-errors")
        _start(environment, user)

        with patch.object(fix_driver, "_check", side_effect=RuntimeError("boom")):
            for _ in range(6):
                fix_driver.check(environment.id)
            run = _run(environment)
            assert run.state == "queued"
            since = run.autosave["failing_since"]
            ReadinessFixRun.no_workspace_objects.filter(id=run.id).update(
                autosave={
                    "failing_since": (
                        timezone.now() - timedelta(minutes=11)
                    ).isoformat()
                }
            )
            assert since
            fix_driver.check(environment.id)

        run = _run(environment)
        assert (run.state, run.error["code"]) == ("failed", "internal_error")

    def test_a_passing_check_resets_the_failures(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment = _broken(user, workspace, storage, "fd-reset")
        _start(environment, user)
        with patch.object(fix_driver, "_check", side_effect=RuntimeError("boom")):
            fix_driver.check(environment.id)
        changed = _run(environment).state_changed_at

        with patch.object(fix_driver, "_check"):
            fix_driver.check(environment.id)

        run = _run(environment)
        assert (run.autosave, run.state_changed_at) == (None, changed)


class TestStallAndBasis:
    def test_recovery_leaves_a_held_fix_command_to_the_check(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-stall"
        )
        _age(message, timedelta(minutes=40))
        restarts = []

        counts = hhc.recover_stalled_conversations(restarts.append)

        assert counts["abandoned"] == 0
        message.refresh_from_db()
        assert message.state == "queued"
        fix_driver.check(environment.id)
        assert _run(environment).commands[0]["outcome"]["turn"] == "not_started"

    def test_a_settled_command_restarts_the_wait_of_messages_behind_it(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-since"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message, "failed"),
        )
        mine = hhc.enqueue_message(
            environment, content="mine", client_request_id="mine"
        )[1]
        _age(mine, timedelta(minutes=40))
        mine.refresh_from_db()

        settled = parse_datetime(_run(environment).commands[0]["settled_at"])
        assert rebuild.waiting_since(conversation, mine) == settled

    def test_only_the_agent_moved_keeps_the_fix_checkpoint(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-agent"
        )
        mine = hhc.enqueue_message(
            environment, content="mine", client_request_id="mine"
        )[1]

        basis = message.payload["_environment_revision"]

        assert rebuild.only_agent_moved(message, environment, basis)
        assert not rebuild.only_agent_moved(mine, environment, basis)
        environment.environment_version = _draft(environment.environment_version)
        assert not rebuild.only_agent_moved(message, environment, basis)

    def test_a_fix_checkpoint_on_another_basis_is_stale(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-bogus-basis"
        )
        key, digest = _checkpoint(
            environment, conversation, message, storage, basis="sha256:" + "0" * 64
        )
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        refs = drafts.draft_refs(environment)

        with pytest.raises(HostedHarnessError) as exc:
            _promote(
                conversation, message, key, digest, django_capture_on_commit_callbacks
            )

        assert exc.value.code == "conversation_checkpoint_stale"
        assert drafts.draft_refs(environment) == refs

    def test_a_warm_workspace_from_before_the_environment_moved_is_stale(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        from simulate.services.harness_versioning.versions import note_revision

        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-env-moved"
        )
        # The pairing of an earlier environment default with the same suite.
        old = "sha256:" + "1" * 64
        note_revision(_draft(environment.environment_version), old)
        note_revision(environment.suite_version, old)
        key, digest = _checkpoint(environment, conversation, message, storage, old)
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))

        assert not rebuild.only_agent_moved(message, environment, old)
        with pytest.raises(HostedHarnessError) as exc:
            _promote(
                conversation, message, key, digest, django_capture_on_commit_callbacks
            )
        assert exc.value.code == "conversation_checkpoint_stale"


class TestGuards:
    def test_no_rebuild_or_default_move_while_a_fix_runs(
        self, user, workspace, storage, tasks, settings
    ):
        from simulate.services.harness_versioning.versions import move_default

        settings.HARNESS_VERSIONING_NUDGES_ENABLED = True
        environment = _broken(user, workspace, storage, "fd-guards")
        nudge = HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization_id=environment.organization_id,
            target="suite",
            state="pending",
            base_environment_version_id=environment.environment_version_id,
            facts={},
        )
        _start(environment, user)
        for piece in ("environment", "suite"):
            current = getattr(environment, f"{piece}_version_id")
            with pytest.raises(HostedHarnessError) as refused:
                move_default(environment, piece, current, expected_default_id=current)
            assert refused.value.code == "fix_running"
        with (
            patch(
                "simulate.services.harness_usage.require_harness_authoring",
                return_value=None,
            ),
            pytest.raises(HostedHarnessError) as refused,
        ):
            rebuild.answer_rebuild(environment, nudge.id, user=user)
        assert refused.value.code == "fix_running"

        # Switched off, a leftover run holds nothing and is left alone.
        settings.HARNESS_READINESS_FIX_ENABLED = False
        current = environment.environment_version_id
        move_default(environment, "environment", current, expected_default_id=current)
        fix_runs.require_no_fix_waiting(environment)
        _run(environment).__class__.no_workspace_objects.filter(job=environment).update(
            state="waiting_on_you"
        )
        fix_runs.require_no_fix_waiting(environment)
        assert fix_driver.tick() == 0
        fix_driver.check(environment.id)
        assert _run(environment).state == "waiting_on_you"

    def test_deleting_the_environment_stops_its_fix(
        self,
        user,
        workspace,
        storage,
        tasks,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-delete"
        )

        with django_capture_on_commit_callbacks(execute=True):
            assert auth_client.delete(f"{BASE}/{environment.id}/").status_code == 204

        run.refresh_from_db()
        assert (run.state, run.error["code"]) == ("stopped", "environment_deleted")
        assert run.lines[0]["state"] == "could_not_fix"
        assert run.lines[0]["reason"]["code"] == "stopped"
        message.refresh_from_db()
        assert message.state == "failed"
        assert _lease_expired(conversation)


class TestTick:
    def test_the_nudge_tick_schedules_each_fix_check(
        self, user, workspace, storage, tasks
    ):
        environment = _broken(user, workspace, storage, "fd-tick")
        _start(environment, user)
        changed = _run(environment).state_changed_at

        assert rebuild.tick_nudges()["fixes"] == 1

        assert (
            "check_harness_readiness_fix",
            [str(environment.id)],
            f"harness-readiness-fix-check-{environment.id}",
        ) in tasks
        assert _run(environment).state_changed_at == changed

    def test_a_start_schedules_a_check_after_commit(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment = _broken(user, workspace, storage, "fd-start-check")

        with django_capture_on_commit_callbacks(execute=True):
            _start(environment, user)

        assert _names(tasks, "check_harness_readiness_fix") == [[str(environment.id)]]

    def test_the_check_dispatches_a_queued_command(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment = _broken(user, workspace, storage, "fd-check")
        _start(environment, user)

        fix_driver.check(environment.id)

        run = _run(environment)
        assert run.state == "fixing"
        assert HostedHarnessConversation.no_workspace_objects.filter(
            job=environment
        ).exists()
        assert _names(tasks, "ensure_hosted_harness_conversation_runtime")


class TestAnswers:
    def test_an_answer_inside_the_running_turn_settles_with_that_turn(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-in-turn"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked("Which table?"),
        )
        assert _run(environment).state == "waiting_on_you"
        reply = _answer(environment, _question_id(conversation))
        # The running turn takes the answer; it never gets a turn of its own.
        _feed(conversation, django_capture_on_commit_callbacks)
        _age(reply, timedelta(minutes=16), "updated_at")
        _age(reply, timedelta(minutes=16))
        fix_driver.check(environment.id)
        run.refresh_from_db()
        assert (run.state, run.commands[0]["state"]) == ("fixing", "dispatched")
        assert run.current_command_id == message.id

        sequence = _feed(
            conversation, django_capture_on_commit_callbacks, _completed(message)
        )
        _publish(conversation, sequence)

        run.refresh_from_db()
        assert run.commands[0]["outcome"]["turn"] == "success"
        assert _lines(run) == [("state", "fixing")]
        reply.refresh_from_db()
        assert reply.state == "delivered"

    def test_a_failed_turn_that_took_an_answer_fails_the_command(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-in-turn-failed"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked(),
        )
        _answer(environment, _question_id(conversation))

        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _completed(message, "failed"),
        )

        run.refresh_from_db()
        assert run.commands[0]["outcome"]["code"] == "turn_failed"
        assert run.state == "fixing"

    def test_the_persons_own_question_after_a_fix_turn_is_not_the_fixs(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-own-question"
        )
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message),
        )
        mine = hhc.enqueue_message(
            environment, content="mine", client_request_id="mine"
        )[1]
        _feed(
            conversation, django_capture_on_commit_callbacks, _started(mine), _asked()
        )
        assert _run(environment).state == "fixing"

        _publish(conversation, sequence)

        run.refresh_from_db()
        assert (run.state, run.commands[0]["outcome"]["turn"]) == ("fixing", "success")


class TestMoreTurns:
    def test_an_interrupted_ending_could_not_fix(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-ended-interrupted"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message, "interrupted"),
        )

        run.refresh_from_db()
        assert run.commands[0]["outcome"] == {
            "turn": "interrupted",
            "code": "turn_interrupted",
            "wrote": False,
        }

    def test_a_replayed_ending_is_left_to_its_promotion(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-replayed"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message, "failed", replayed=True),
        )
        fix_driver.check(environment.id)

        assert _run(environment).commands[0]["state"] == "dispatched"

    def test_a_missed_interrupt_is_found_by_the_check(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-missed-interrupt"
        )
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            ("turn_interrupted", {"command_message_id": str(message.id)}),
            execute=False,
        )
        assert _run(environment).commands[0]["interrupted_at"] is None
        Event.no_workspace_objects.filter(
            conversation=conversation, kind="turn_interrupted"
        ).update(created_at=timezone.now() - timedelta(minutes=16))

        fix_driver.check(environment.id)

        assert _run(environment).commands[0]["outcome"]["code"] == "turn_interrupted"

    def test_an_abandoned_message_is_not_started(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-abandoned"
        )
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        Message.no_workspace_objects.filter(id=message.id).update(state="failed")

        fix_driver.check(environment.id)

        assert _run(environment).commands[0]["outcome"]["turn"] == "not_started"

    def test_a_building_environment_holds_dispatch(
        self, user, workspace, storage, tasks
    ):
        environment = _broken(user, workspace, storage, "fd-building")
        _start(environment, user)

        with patch.object(fix_driver, "_busy", return_value=True):
            assert not fix_driver.dispatch(environment.id, start_runtime=False)

        assert _run(environment).state == "queued"

    def test_a_retryable_refusal_settles_nothing(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-retryable"
        )
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            ("checkpoint_committed", {"digest": "sha256:" + "0" * 64, "size": 1}),
            _completed(message),
        )
        busy = HostedHarnessError("storage_busy", "busy", retryable=True)

        with (
            patch.object(hhc, "promote_conversation_checkpoint", side_effect=busy),
            pytest.raises(HostedHarnessError),
        ):
            _publish(conversation, sequence)

        assert _run(environment).commands[0]["state"] == "dispatched"

    def test_a_checkpoint_that_did_not_publish_settles_nothing(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-unpublished"
        )
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            ("checkpoint_committed", {"digest": "sha256:" + "0" * 64, "size": 1}),
            _completed(message),
        )

        with patch.object(hhc, "promote_conversation_checkpoint", return_value=None):
            _publish(conversation, sequence)

        assert _run(environment).commands[0]["state"] == "dispatched"


class TestRetire:
    def test_a_refusal_that_does_not_rebase_gives_the_sandbox_up(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-missing"
        )
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            ("checkpoint_committed", {"digest": "sha256:" + "0" * 64, "size": 1}),
            _completed(message),
        )

        with (
            django_capture_on_commit_callbacks(execute=True),
            pytest.raises(HostedHarnessError),
        ):
            _publish(conversation, sequence)

        run.refresh_from_db()
        assert run.commands[0]["outcome"]["code"] == "conversation_checkpoint_missing"
        assert _lease_expired(conversation)

    def test_the_persons_turn_after_the_fix_is_not_cut_short(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-not-cut"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            ("turn_interrupted", {"command_message_id": str(message.id)}),
        )
        mine = hhc.enqueue_message(
            environment, content="mine", client_request_id="mine"
        )[1]
        _feed(conversation, django_capture_on_commit_callbacks, _started(mine))
        _interrupted_long_ago(run)

        with _retires() as retired, django_capture_on_commit_callbacks(execute=True):
            fix_driver.check(environment.id)

        run.refresh_from_db()
        assert run.commands[0]["outcome"]["code"] == "turn_interrupted"
        assert retired == []
        assert not _lease_expired(conversation)


def _with_a2_broken(user, workspace, storage, key):
    """``_broken`` with a second agent, A2, whose pairing is certified too."""
    from simulate.tests.test_harness_readiness import (
        FAILED_PROBE,
        PASSED,
        _certify,
        _with_a2,
    )

    environment, a2 = _with_a2(user, workspace, storage, key)
    _certify(
        environment,
        storage,
        checks={**PASSED, "schema_and_seed": "failed", "action_behavior": "1/2"},
        actions=FAILED_PROBE,
    )
    environment.refresh_from_db()
    return environment, a2


def _move_to(environment, storage, a2):
    """Move the default agent to A2; its pairing is certified, so no validation."""
    from simulate.services.harness_versioning.versions import (
        move_default,
        recorded_revisions,
    )
    from simulate.tests.test_harness_readiness import FAILED_PROBE, PASSED, _certify

    before = hhc.environment_authoring_revision(environment)
    a1 = environment.agent_version_id
    _certify(environment, storage, agent=a2)
    move_default(environment, "agent", a2.id, expected_default_id=a1)
    environment.refresh_from_db()
    assert environment.agent_version_id == a2.id
    after = hhc.environment_authoring_revision(environment)
    assert after != before
    # The pair under A2 stands for the same versions, so a later move keeps it.
    for version in (environment.environment_version, environment.suite_version):
        assert {before, after} <= set(recorded_revisions(version))
    # A2's own pairing has rows for the builder to fix too.
    _certify(
        environment,
        storage,
        checks={**PASSED, "schema_and_seed": "failed", "action_behavior": "1/2"},
        actions=FAILED_PROBE,
    )
    environment.refresh_from_db()


class TestAgentMove:
    def test_a_warm_workspace_from_before_the_agent_moved_publishes_the_fix(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, a2 = _with_a2_broken(user, workspace, storage, "fd-warm")
        conversation = hhc.ensure_conversation(environment)
        warm = hhc.environment_authoring_revision(environment)
        _move_to(environment, storage, a2)
        _start(environment, user, ["state"])
        assert fix_driver.dispatch(environment.id, start_runtime=False)
        message = Message.no_workspace_objects.get(
            id=_run(environment).current_command_id
        )
        assert message.payload["_environment_revision"] != warm
        key, digest = _checkpoint(environment, conversation, message, storage, warm)
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        refs = drafts.draft_refs(environment)

        _promote(conversation, message, key, digest, django_capture_on_commit_callbacks)

        assert drafts.draft_refs(environment) != refs

    def test_a_fix_starts_its_chat_afresh_on_an_unrecorded_warm_workspace(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, a2 = _with_a2_broken(user, workspace, storage, "fd-cold")
        conversation = hhc.ensure_conversation(environment)
        hhc.issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-warm",
            attempt=None,
            ttl_seconds=600,
        )
        _move_to(environment, storage, a2)
        _start(environment, user, ["state"])

        assert fix_driver.dispatch(environment.id, start_runtime=False)

        assert _lease_expired(conversation)
        message = Message.no_workspace_objects.get(
            id=_run(environment).current_command_id
        )
        # A stamp no version records is stale; the fresh sandbox's is the command's.
        unrecorded = "sha256:" + "2" * 64
        assert not rebuild.only_agent_moved(message, environment, unrecorded)
        key, digest = _checkpoint(environment, conversation, message, storage)
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        refs = drafts.draft_refs(environment)

        _promote(conversation, message, key, digest, django_capture_on_commit_callbacks)

        assert drafts.draft_refs(environment) != refs

    def test_the_next_command_publishes_from_the_warm_workspace_after_a_move(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, a2 = _with_a2_broken(user, workspace, storage, "fd-next")
        _start(environment, user, ["state"])
        assert fix_driver.dispatch(environment.id, start_runtime=False)
        first = Message.no_workspace_objects.get(
            id=_run(environment).current_command_id
        )
        conversation = first.conversation
        _start(environment, user, ["tool_routing"])
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(first),
            _completed(first),
        )
        _publish(conversation, sequence)
        _move_to(environment, storage, a2)
        assert fix_driver.dispatch(environment.id, start_runtime=False)
        assert not _lease_expired(conversation)
        second = Message.no_workspace_objects.get(
            id=_run(environment).current_command_id
        )
        assert second.id != first.id
        warm = first.payload["_environment_revision"]
        key, digest = _checkpoint(environment, conversation, second, storage, warm)
        _feed(conversation, django_capture_on_commit_callbacks, _started(second))
        refs = drafts.draft_refs(environment)

        _promote(conversation, second, key, digest, django_capture_on_commit_callbacks)

        assert drafts.draft_refs(environment) != refs

    def test_an_answer_after_a_move_publishes_from_the_warm_workspace(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, a2 = _with_a2_broken(user, workspace, storage, "fd-answer")
        _start(environment, user, ["state"])
        assert fix_driver.dispatch(environment.id, start_runtime=False)
        message = Message.no_workspace_objects.get(
            id=_run(environment).current_command_id
        )
        conversation = message.conversation
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked(),
            _completed(message),
        )
        _move_to(environment, storage, a2)
        reply = _answer(environment, _question_id(conversation))
        warm = message.payload["_environment_revision"]
        assert reply.payload["_environment_revision"] != warm
        key, digest = _checkpoint(environment, conversation, reply, storage, warm)
        _feed(conversation, django_capture_on_commit_callbacks, _started(reply))
        refs = drafts.draft_refs(environment)

        _promote(conversation, reply, key, digest, django_capture_on_commit_callbacks)

        assert drafts.draft_refs(environment) != refs

    def test_a_fix_checkpoint_survives_a_move_of_only_the_agent(
        self,
        user,
        workspace,
        storage,
        tasks,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_versioning.versions import move_default
        from simulate.tests.test_harness_readiness import _certify

        environment, a2 = _with_a2_broken(user, workspace, storage, "fd-agent-move")
        a1 = environment.agent_version_id
        _start(environment, user, ["state"])
        assert fix_driver.dispatch(environment.id, start_runtime=False)
        run = _run(environment)
        message = Message.no_workspace_objects.get(id=run.current_command_id)
        conversation = message.conversation
        key, digest = _checkpoint(environment, conversation, message, storage)
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        refs = drafts.draft_refs(environment)

        # A2's pairing is certified, so the move needs no validation first.
        _certify(environment, storage, agent=a2)
        move_default(environment, "agent", a2.id, expected_default_id=a1)
        environment.refresh_from_db()
        assert environment.agent_version_id == a2.id
        assert (
            hhc.environment_authoring_revision(environment)
            != message.payload["_environment_revision"]
        )
        assert rebuild.only_agent_moved(
            message, environment, message.payload["_environment_revision"]
        )
        _promote(conversation, message, key, digest, django_capture_on_commit_callbacks)

        assert drafts.draft_refs(environment) != refs


class TestSwitchedOff:
    def test_hooks_make_no_queries_while_fix_is_off(
        self,
        user,
        workspace,
        storage,
        tasks,
        settings,
        django_assert_num_queries,
        django_capture_on_commit_callbacks,
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-off"
        )
        reply = Message(conversation=conversation, reply_to=uuid.uuid4(), payload={})
        settings.HARNESS_READINESS_FIX_ENABLED = False

        with django_assert_num_queries(0):
            fix_driver.on_conversation_event(
                str(conversation.id), *_completed(message, "failed"), 9, None
            )
            assert fix_driver.last_settled_at(environment.id) is None
            assert fix_driver.run_for_command(reply) is None
            fix_driver.on_published((run, run.commands[0]), None, wrote=False)

        assert _run(environment).commands[0]["state"] == "dispatched"


class TestReverify:
    def test_a_stop_message_does_not_keep_the_sandbox_warm(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-stop"
        )
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        stop = hhc.enqueue_message(
            environment, content="stop", client_request_id="stop", kind="interrupt"
        )[1]
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            (
                "turn_interrupted",
                {
                    "command_message_id": str(message.id),
                    "interrupt_message_id": str(stop.id),
                },
            ),
        )
        stop.refresh_from_db()
        assert stop.state == "completed"
        _interrupted_long_ago(run)

        with django_capture_on_commit_callbacks(execute=True):
            fix_driver.check(environment.id)

        assert _run(environment).commands[0]["outcome"]["code"] == "turn_interrupted"
        assert _lease_expired(conversation)

    def test_time_paused_on_a_question_is_not_stuck(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-paused"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked(),
        )
        Event.no_workspace_objects.filter(
            conversation=conversation, kind="turn_started"
        ).update(created_at=timezone.now() - timedelta(minutes=90))
        _answer(environment, _question_id(conversation))
        _feed(conversation, django_capture_on_commit_callbacks)
        HostedHarnessConversationLease.no_workspace_objects.filter(
            conversation=conversation
        ).update(state="expired")

        fix_driver.check(environment.id)

        assert _run(environment).commands[0]["state"] == "dispatched"

    def test_an_answer_sent_after_the_turn_ended_waits_for_its_own_turn(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-after-end"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked(),
        )
        ended_at = datetime.now(UTC)
        reply = _answer(environment, _question_id(conversation))
        # ALK ended the turn before the answer, but the event arrives after it.
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _completed(message),
            acked=message.sequence,
            emitted=ended_at,
        )
        _publish(conversation, sequence)
        assert _run(environment).commands[0]["state"] == "dispatched"

        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(reply),
            _completed(reply),
        )
        _publish(conversation, sequence)

        run.refresh_from_db()
        assert run.commands[0]["outcome"]["turn"] == "success"
        assert run.current_command_id is None

    def test_a_failed_command_fails_its_untaken_answer(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-untaken"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked(),
            ("turn_interrupted", {"command_message_id": str(message.id)}),
        )
        reply = _answer(environment, _question_id(conversation))
        _interrupted_long_ago(run)
        # Answered right after the interrupt; the wait counts from the answer.
        _age(reply, timedelta(minutes=16))

        fix_driver.check(environment.id)

        assert _run(environment).commands[0]["outcome"]["code"] == "turn_interrupted"
        reply.refresh_from_db()
        assert reply.state == "failed"

    def test_stopping_fails_an_untaken_answer_but_not_a_taken_one(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-stop-answers"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked(),
        )
        taken = _answer(environment, _question_id(conversation))
        _feed(conversation, django_capture_on_commit_callbacks, _asked("Again?"))
        untaken = _answer(environment, _question_id(conversation))

        fix_runs.end_without_save(run, "chat_busy", "stopped")

        taken.refresh_from_db()
        untaken.refresh_from_db()
        assert (taken.state, untaken.state) == ("delivered", "failed")

    def test_a_late_landing_is_tried_once_before_it_is_given_up(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-landing-late"
        )
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message),
        )
        Event.no_workspace_objects.filter(
            conversation=conversation, sequence=sequence
        ).update(created_at=timezone.now() - timedelta(minutes=30))

        with patch.object(hhc, "promote_turn_checkpoint") as retried:
            fix_driver.check(environment.id)
            assert _run(environment).commands[0]["state"] == "dispatched"
            fix_driver.check(environment.id)

        retried.assert_called_once_with(str(conversation.id), sequence)
        assert _run(environment).commands[0]["outcome"]["code"] == "publish_failed"

    def test_a_chat_free_again_dispatches_instead_of_ending(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment = _broken(user, workspace, storage, "fd-free-again")
        run = _start(environment, user)
        mine = hhc.enqueue_message(
            environment, content="mine", client_request_id="mine"
        )[1]
        fix_driver.check(environment.id)
        _busy_since(run, 0, timedelta(minutes=31))
        Message.no_workspace_objects.filter(id=mine.id).update(state="completed")
        HostedHarnessConversation.no_workspace_objects.filter(
            id=mine.conversation_id
        ).update(state="warm_idle", command_acked_through=mine.sequence)

        fix_driver.check(environment.id)

        run.refresh_from_db()
        assert (run.state, run.commands[0]["state"]) == ("fixing", "dispatched")

    def test_an_interrupt_before_the_restart_is_ignored(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-old-interrupt"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            ("turn_interrupted", {"command_message_id": str(message.id)}),
        )
        _interrupted_long_ago(run)
        # Started again, but the hook missed it.
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            execute=False,
        )

        fix_driver.check(environment.id)

        assert _run(environment).commands[0]["state"] == "dispatched"

    def test_recovery_keeps_a_message_queued_behind_a_running_fix_turn(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-behind"
        )
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        mine = hhc.enqueue_message(
            environment, content="mine", client_request_id="mine"
        )[1]
        _age(mine, timedelta(minutes=70))
        HostedHarnessConversationLease.no_workspace_objects.filter(
            conversation=conversation
        ).update(state="expired")

        counts = hhc.recover_stalled_conversations(lambda _id: None)

        assert counts["abandoned"] == 0
        mine.refresh_from_db()
        assert mine.state == "queued"


class TestLowCleanup:
    def test_a_stopped_question_puts_the_run_back_to_fixing(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-stopped-question"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked(),
        )
        assert _run(environment).state == "waiting_on_you"
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            ("turn_interrupted", {"command_message_id": str(message.id)}),
        )

        fix_driver.check(environment.id)

        run.refresh_from_db()
        assert (run.state, run.question_message_id) == ("fixing", None)
        assert _lines(run) == [("state", "fixing")]

    def test_a_restart_that_drops_the_question_keeps_the_run_waiting(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-restart-question"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked(),
        )
        # The sandbox restarts: the chat no longer reads as asking, no turn ended.
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(state=HostedHarnessConversation.State.COLD)

        fix_driver.check(environment.id)

        run.refresh_from_db()
        assert run.state == "waiting_on_you"
        assert run.question_message_id is not None

    def test_a_question_holds_only_what_the_chat_has_not_taken(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-hold-question"
        )
        _feed(conversation, django_capture_on_commit_callbacks, _started(message))
        message.refresh_from_db()
        conversation.refresh_from_db()
        assert message.state == "delivered"
        assert fix_driver.holds_conversation(conversation)
        _feed(conversation, django_capture_on_commit_callbacks, _asked())
        assert _run(environment).state == "waiting_on_you"
        conversation.refresh_from_db()

        # Paused on the person: the taken command holds nothing.
        assert not fix_driver.holds_conversation(conversation)

    def test_an_answer_taken_after_its_turn_ended_fails_when_not_started(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-taken-late"
        )
        _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _asked(),
            _completed(message),
            emitted=datetime.now(UTC) - timedelta(minutes=30),
        )
        reply = _answer(environment, _question_id(conversation))
        _age(reply, timedelta(minutes=16))
        # The chat took it, but no turn of its own ever started.
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(command_acked_through=reply.sequence)

        fix_driver.check(environment.id)

        assert _run(environment).commands[0]["outcome"]["code"] == "not_started"
        reply.refresh_from_db()
        assert reply.state == "failed"

    def test_a_landing_is_retried_once_per_turn(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fd-landing-turn"
        )
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message),
        )
        Event.no_workspace_objects.filter(
            conversation=conversation, sequence=sequence
        ).update(created_at=timezone.now() - timedelta(minutes=10))
        # An earlier turn of the command was retried already.
        run.refresh_from_db()
        run.commands[0]["landing_retried"] = sequence - 5
        run.save(update_fields=["commands"])

        with patch.object(hhc, "promote_turn_checkpoint") as retried:
            fix_driver.check(environment.id)
            fix_driver.check(environment.id)

        retried.assert_called_once_with(str(conversation.id), sequence)
        assert _run(environment).commands[0]["landing_retried"] == sequence
