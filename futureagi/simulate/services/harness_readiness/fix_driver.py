"""Run readiness Fix driver: send a run's commands to the builder chat one at a time
and follow their turns. Locks head, run, then conversation; each transition re-reads
the run and does nothing unless it is still where the transition expects."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from simulate.models import (
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
)
from simulate.models.readiness_fix import ACTIVE, APPENDABLE, ReadinessFixRun
from simulate.services import hosted_harness_conversation as hhc
from simulate.services.harness_agent_versions import _busy
from simulate.services.harness_readiness import fix_groups, fix_runs
from simulate.services.harness_readiness.fix_runs import FixCommand
from simulate.services.harness_versioning import drafts, nudges, rebuild
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

State = ReadinessFixRun.State
Message = HostedHarnessConversationMessage
Event = HostedHarnessConversationEvent

EVENT_KINDS = frozenset(
    {
        "turn_started",
        "turn_completed",
        "turn_interrupted",
        "question_requested",
        "confirmation_requested",
    }
)
_QUESTION_KINDS = ("question_requested", "confirmation_requested")
_DISPATCHABLE = (State.QUEUED, State.FIXING)
# Served question text; the full question is in the chat.
PROMPT_MAX = 140
# The builder's own words on a line it made no change for.
REASON_MAX = 300
# What the builder appends after its reply; not why it made no change.
_HEALTH_SUFFIX = "\n\nStill open from the last health check"


# --- reading --------------------------------------------------------------------


def _time(value: Any) -> datetime | None:
    return parse_datetime(str(value)) if value else None


@contextmanager
def _locked(
    environment_id: Any,
) -> Iterator[tuple[HostedHarnessJob, ReadinessFixRun] | None]:
    """The head and the environment's active run, locked in that order."""
    with transaction.atomic():
        head = nudges._head(environment_id, lock=True)
        run = fix_runs.active_run(head.id, lock=True) if head is not None else None
        yield (head, run) if head is not None and run is not None else None


def _lock_conversation(environment_id: Any) -> HostedHarnessConversation | None:
    return (
        HostedHarnessConversation.no_workspace_objects.select_for_update()
        .filter(job_id=environment_id)
        .first()
    )


def _ids(run: ReadinessFixRun, cmd: FixCommand) -> list[str]:
    """Every message of the command: itself, its answers, and the one whose turn
    started last."""
    return list(
        dict.fromkeys(
            str(one)
            for one in (
                cmd["message_id"],
                run.current_command_id,
                *(reply.id for reply in fix_runs.replies(cmd)),
            )
            if one
        )
    )


@dataclass(frozen=True)
class _Flight:
    """Where the command's turn is: ``current`` runs or ran last, ``started`` and
    ``ended`` are its events, ``pending`` a live answer waiting for its own turn (an
    answer to a turn that already ended gets one; ``current`` moves when it starts)."""

    ids: list[str]
    current: str
    started: Event | None
    ended: Event | None
    pending: Message | None


def _flight(
    conversation: HostedHarnessConversation | None,
    run: ReadinessFixRun,
    cmd: FixCommand,
) -> _Flight:
    ids = _ids(run, cmd)
    events = Event.no_workspace_objects.filter(conversation=conversation)
    started = (
        events.filter(kind="turn_started", payload__command_message_id__in=ids)
        .order_by("-sequence")
        .first()
        if conversation is not None
        else None
    )
    current = (
        str((started.payload or {})["command_message_id"])
        if started is not None
        else str(run.current_command_id or cmd["message_id"])
    )
    ended = (
        events.filter(
            kind="turn_completed",
            payload__command_message_id=current,
            sequence__gt=started.sequence if started is not None else 0,
        )
        .order_by("-sequence")
        .first()
        if conversation is not None
        else None
    )
    pending = None
    if ended is not None and conversation is not None:
        # A live answer the chat never took, or sent after the turn ended, waits for
        # its own turn; one the turn took, or the chat failed or ended, does not.
        ended_at = ended.emitted_at or ended.created_at
        pending = next(
            (
                reply
                for reply in fix_runs.live_replies(cmd)
                if str(reply.id) != current
                and (
                    reply.sequence > conversation.command_acked_through
                    or reply.created_at > ended_at
                )
            ),
            None,
        )
    return _Flight(ids, current, started, ended, pending)


def _settles(flight: _Flight, command_id: str) -> bool:
    """A turn of ``command_id`` decides the command: it is the current turn and no
    answer waits for a turn after it."""
    return command_id == flight.current and flight.pending is None


def _fail(message: Message, conversation: HostedHarnessConversation | None) -> None:
    """Fail a waiting message so no sandbox runs it; never an answer the chat took."""
    taken = conversation is not None and (
        message.sequence <= conversation.command_acked_through
    )
    if message.reply_to is None or not taken:
        rebuild.fail_command(message.id)


def run_for_command(
    command: Message | None,
) -> tuple[ReadinessFixRun, FixCommand] | None:
    """The run and command of a fix's own message (even once its run ended) or of the
    person's answer to its question; no query for other messages or while off."""
    if command is None:
        return None
    payload = command.payload or {}
    run_id = payload.get("_fix_run_id")
    if run_id:
        run = ReadinessFixRun.no_workspace_objects.filter(id=run_id).first()
        cmd = fix_runs.command(run, payload.get("_fix_command")) if run else None
        return (run, cmd) if run is not None and cmd is not None else None
    if not command.reply_to or not fix_groups.enabled():
        return None
    conversation = command.conversation
    if conversation.job.environment_id is not None:
        return None
    run = fix_runs.active_run(conversation.job_id)
    cmd = fix_runs.in_flight(run) if run is not None else None
    if run is None or cmd is None:
        return None
    if str(command.id) == str(run.current_command_id) or str(command.reply_to) in {
        str(one) for one in cmd.get("questions") or ()
    }:
        return run, cmd
    return None


def _turn_command(conversation: HostedHarnessConversation, sequence: int) -> str | None:
    """The command of the last turn started before ``sequence``."""
    started = (
        Event.no_workspace_objects.filter(
            conversation=conversation, kind="turn_started", sequence__lt=sequence
        )
        .order_by("-sequence")
        .values_list("payload", flat=True)
        .first()
    )
    command_id = (started or {}).get("command_message_id")
    return str(command_id) if command_id else None


def question_command(
    conversation: HostedHarnessConversation,
    run: ReadinessFixRun,
    cmd: FixCommand,
    question_id: Any,
) -> bool:
    """Whether a turn of ``cmd``, or of an answer chained from its questions, asked
    the question."""
    known = set(_ids(run, cmd))
    target = question_id
    for _ in range(8):
        asked = (
            Event.no_workspace_objects.filter(
                conversation=conversation, message_id=target, kind__in=_QUESTION_KINDS
            )
            .order_by("-sequence")
            .values_list("sequence", flat=True)
            .first()
        )
        command_id = _turn_command(conversation, asked) if asked else None
        if command_id is None:
            return False
        if command_id in known:
            return True
        target = (
            Message.no_workspace_objects.filter(
                conversation=conversation, id=command_id, role=Message.Role.USER
            )
            .values_list("reply_to", flat=True)
            .first()
        )
        if not target:
            return False
    return False


def _open_question(conversation: HostedHarnessConversation | None) -> str | None:
    if conversation is None or not drafts._awaiting_answer(conversation):
        return None
    return str((conversation.blocking_input or {})["message_id"])


def _remember(cmd: FixCommand, question_id: str) -> None:
    cmd["questions"] = list(dict.fromkeys([*(cmd.get("questions") or ()), question_id]))


def _ask(run: ReadinessFixRun, cmd: FixCommand, question_id: str) -> None:
    prompt = (
        Message.no_workspace_objects.filter(id=question_id)
        .values_list("content", flat=True)
        .first()
    )
    run.question_message_id = question_id
    _remember(cmd, question_id)
    fix_runs.set_lines(
        run,
        cmd["n"],
        "waiting_on_you",
        fix_runs.reason(
            "builder_question",
            _clip(prompt or "") or "The builder chat asked a question",
        ),
    )
    run.state = State.WAITING_ON_YOU


def _clip(text: str) -> str:
    return text if len(text) <= PROMPT_MAX else text[: PROMPT_MAX - 1].rstrip() + "…"


def _builder_said(
    conversation: HostedHarnessConversation | None,
    turn: Event,
    started: Event | None,
) -> str:
    """The builder's last reply in ``turn``, ending with why it made no change; ""
    for none, or one that failed or was cut off."""
    if conversation is None:
        return ""
    said = Event.no_workspace_objects.filter(
        conversation=conversation, kind="assistant_message", sequence__lt=turn.sequence
    )
    if turn.invocation_id:
        said = said.filter(invocation_id=turn.invocation_id)
    else:
        said = said.filter(sequence__gt=started.sequence if started is not None else 0)
    payload = said.order_by("-sequence").values_list("payload", flat=True).first() or {}
    if payload.get("error") or payload.get("interrupted"):
        return ""
    text = str(payload.get("text") or "").split(_HEALTH_SUFFIX)[0].strip()
    if len(text) <= REASON_MAX:
        return text
    # Text from before a replay of the turn can lead it: the end says why.
    return "…" + text[-(REASON_MAX - 1) :].lstrip()


def _own_question(
    conversation: HostedHarnessConversation | None,
    run: ReadinessFixRun,
    cmd: FixCommand,
) -> str | None:
    """The open question in the chat, when a turn of ``cmd`` asked it."""
    question = _open_question(conversation)
    if question is None or conversation is None:
        return None
    return question if question_command(conversation, run, cmd, question) else None


def _answered(run: ReadinessFixRun, cmd: FixCommand) -> None:
    """The person answered: the command's lines and the run work on."""
    run.question_message_id = None
    fix_runs.set_lines(run, cmd["n"], "fixing", states=("queued", "waiting_on_you"))
    run.state = State.FIXING


# --- dispatch -------------------------------------------------------------------


def _next(run: ReadinessFixRun) -> FixCommand | None:
    """The lowest queued command that carries rows; row-less ones are never sent."""
    return next(
        (one for one in run.commands if one["state"] == "queued" and one["rows"]),
        None,
    )


def dispatch(environment_id: Any, *, start_runtime: bool = True) -> bool:
    """Put the active run's next command into the idle builder chat; one in flight."""
    head = nudges._head(environment_id)
    run = fix_runs.active_run(head.id) if head is not None else None
    if (
        head is None
        or run is None
        or run.state not in _DISPATCHABLE
        or fix_runs.in_flight(run) is not None
    ):
        return False
    cmd = _next(run)
    if cmd is None or _busy(head):
        return False
    conversation = rebuild._conversation(head.id)
    if conversation is not None and not rebuild._chat_idle(conversation):
        return False
    revision = hhc.environment_authoring_revision(head)
    conversation = hhc.ensure_conversation(head)
    promoted = rebuild._promote_finished_turn(conversation)
    with _locked(head.id) as locked:
        if locked is None:
            return False
        locked_head, run = locked
        cmd = fix_runs.command(run, cmd["n"])
        if (
            run.state not in _DISPATCHABLE
            or fix_runs.in_flight(run) is not None
            or cmd is None
            or cmd["state"] != "queued"
        ):
            return False
        conversation = (
            HostedHarnessConversation.no_workspace_objects.select_for_update().get(
                id=conversation.id
            )
        )
        if not rebuild._idle_after(conversation, promoted):
            return False
        before = fix_runs.snapshot(run)
        first = not any(one.get("message_id") for one in run.commands)
        _same, message, _created = hhc.enqueue_message(
            locked_head,
            content=cmd["message"],
            client_request_id=f"fix-{run.id}-{cmd['n']}",
            # A plain user message: the builder needs no new command kind.
            payload={},
            server_payload={
                "_fix_run_id": str(run.id),
                "_fix_command": cmd["n"],
                **({"_author_id": str(run.started_by_id)} if run.started_by_id else {}),
            },
            expected_revision=revision,
        )
        cmd["state"] = "dispatched"
        cmd["message_id"] = str(message.id)
        cmd["dispatched_at"] = timezone.now().isoformat()
        fix_runs.set_lines(run, cmd["n"], "fixing", states=("queued",))
        run.current_command_id = message.id
        run.question_message_id = None
        run.state = State.FIXING
        fix_runs.store(run, before)
        if first:
            # The first sandbox starts from the run's base; later commands keep it.
            rebuild._expire_chat_lease(conversation)
    logger.info("harness_fix_dispatched", run=str(run.id), message=str(message.id))
    if start_runtime and rebuild.sandbox_provider_configured():
        try:
            rebuild._start_runtime(str(conversation.id))
        except Exception:  # noqa: BLE001 - the recovery sweep starts it
            logger.exception("harness_fix_runtime_schedule_failed", run=str(run.id))
    return True


# --- turn events ----------------------------------------------------------------


def on_conversation_event(
    conversation_id: str,
    kind: str,
    payload: Mapping[str, Any],
    sequence: int,
    message_id: Any,
) -> None:
    """After an event of the environment's own chat commits: follow the fix's turn.
    Never raises (the check reconciles a miss); nothing while Fix is off."""
    if kind not in EVENT_KINDS or not fix_groups.enabled():
        return
    try:
        environment_id = (
            HostedHarnessConversation.no_workspace_objects.filter(
                id=conversation_id, job__environment__isnull=True
            )
            .values_list("job_id", flat=True)
            .first()
        )
        if (
            environment_id is None
            or not ReadinessFixRun.no_workspace_objects.filter(
                job_id=environment_id, state__in=ACTIVE
            ).exists()
        ):
            return
        if kind == "turn_started":
            _turn_started(environment_id, payload.get("command_message_id"))
            return
        if kind == "turn_interrupted":
            _turn_interrupted(environment_id, payload)
        elif kind == "turn_completed":
            _turn_completed(environment_id, payload)
        else:
            _question_asked(environment_id, message_id)
        fix_runs.schedule_check(environment_id)
    except Exception:  # noqa: BLE001 - the check reconciles it
        logger.exception(
            "harness_fix_event_failed", conversation=str(conversation_id), kind=kind
        )


def _turn_started(environment_id: Any, command_id: Any) -> None:
    if not command_id:
        return
    command_id = str(command_id)
    with _locked(environment_id) as locked:
        if locked is None:
            return
        _head, run = locked
        cmd = fix_runs.in_flight(run)
        if run.state not in APPENDABLE or cmd is None:
            return
        if command_id not in _ids(run, cmd):
            return
        if (
            command_id == str(run.current_command_id)
            and cmd["started_at"]
            and not cmd["interrupted_at"]
        ):
            return  # the same start again
        before = fix_runs.snapshot(run)
        if command_id != str(cmd["message_id"]):
            # An answer's own turn: the question it answered is closed.
            _answered(run, cmd)
        run.current_command_id = command_id
        cmd["started_at"] = timezone.now().isoformat()
        cmd["interrupted_at"] = None
        fix_runs.set_lines(run, cmd["n"], "fixing", states=("queued",))
        if run.question_message_id is None:
            run.state = State.FIXING
        fix_runs.store(run, before)


def _turn_interrupted(environment_id: Any, payload: Mapping[str, Any]) -> None:
    """The platform completes an interrupted command, so it is not redelivered: the
    check settles it unless its turn starts again."""
    command_id = payload.get("command_message_id")
    with _locked(environment_id) as locked:
        if locked is None:
            return
        _head, run = locked
        cmd = fix_runs.in_flight(run)
        if cmd is None or cmd["interrupted_at"]:
            return
        # A control message's interrupt names no command: it stops the running turn.
        ours = (
            str(command_id) == str(run.current_command_id)
            if command_id
            else bool(cmd["started_at"])
        )
        if not ours:
            return
        before = fix_runs.snapshot(run)
        cmd["interrupted_at"] = timezone.now().isoformat()
        fix_runs.store(run, before)


def _turn_completed(environment_id: Any, payload: Mapping[str, Any]) -> None:
    if payload.get("replayed"):
        return  # its promotion decides
    command_id = str(payload.get("command_message_id") or "")
    outcome = payload.get("outcome")
    with _locked(environment_id) as locked:
        if locked is None:
            return
        _head, run = locked
        cmd = fix_runs.in_flight(run)
        if cmd is None:
            return
        conversation = _lock_conversation(environment_id)
        if not _settles(_flight(conversation, run, cmd), command_id):
            return
        before = fix_runs.snapshot(run)
        question = _own_question(conversation, run, cmd)
        if question is not None:
            _ask(run, cmd, question)  # in case its event was missed
        else:
            if run.state == State.WAITING_ON_YOU:
                # Answered inside this turn, or its question ended with it.
                _answered(run, cmd)
            if payload.get("interrupted") or outcome == "interrupted":
                fix_runs.settle(
                    run,
                    cmd,
                    turn="interrupted",
                    code="turn_interrupted",
                    conversation=conversation,
                )
            elif outcome == "failed":
                fix_runs.settle(
                    run,
                    cmd,
                    turn="failed",
                    code="turn_failed",
                    conversation=conversation,
                )
        # A success settles when its checkpoint publishes (``on_published``).
        fix_runs.store(run, before)


def _question_asked(environment_id: Any, question_id: Any) -> None:
    if not question_id:
        return
    with _locked(environment_id) as locked:
        if locked is None:
            return
        _head, run = locked
        cmd = fix_runs.in_flight(run)
        if run.state not in APPENDABLE or cmd is None:
            return
        conversation = _lock_conversation(environment_id)
        # Answered meanwhile, or asked again after a restart once answered.
        if conversation is None or not question_command(
            conversation, run, cmd, question_id
        ):
            return
        before = fix_runs.snapshot(run)
        if _open_question(conversation) == str(question_id):
            _ask(run, cmd, str(question_id))
        else:
            # Answered already: kept so the answer is known as the command's.
            _remember(cmd, str(question_id))
        fix_runs.store(run, before)


# --- publishing -----------------------------------------------------------------


def _settle_published(
    fix: tuple[ReadinessFixRun, FixCommand],
    turn: Any,
    settle: Any,
) -> None:
    """Under the locks, settle the run's in-flight command when ``turn`` decides it;
    ``settle(run, cmd, conversation)`` says how."""
    run, cmd = fix
    command_id = str((getattr(turn, "payload", None) or {}).get("command_message_id"))
    with transaction.atomic():
        head = nudges._head(run.job_id, lock=True)
        if head is None:
            return
        run = fix_runs.lock_run(run.id)
        current = fix_runs.command(run, cmd["n"])
        if run.state not in APPENDABLE or current is None:
            return
        if current["state"] != "dispatched":
            return
        conversation = _lock_conversation(run.job_id)
        if not _settles(_flight(conversation, run, current), command_id):
            return
        before = fix_runs.snapshot(run)
        settle(run, current, conversation)
        if fix_runs.store(run, before):
            fix_runs.schedule_check(run.job_id)


def on_published(
    fix: tuple[ReadinessFixRun, FixCommand], turn: Any, *, wrote: bool
) -> None:
    """A fix turn's checkpoint published (or it had none): settle the command unless
    it waits on the person's answer. Idempotent; never raises."""
    if not fix_groups.enabled():
        return

    def settle(run, cmd, conversation) -> None:
        wrote_any = wrote or bool(cmd.get("wrote"))
        if _own_question(conversation, run, cmd) is not None:
            # Partial work published; the turn the answer drives settles it.
            cmd["wrote"] = wrote_any
            return
        # Read before settling, which clears the command's turn.
        said = (
            ""
            if wrote_any
            else _builder_said(
                conversation, turn, _flight(conversation, run, cmd).started
            )
        )
        fix_runs.settle(run, cmd, turn="success", wrote=wrote_any)
        if said:
            fix_runs.set_lines(
                run,
                cmd["n"],
                "could_not_fix",
                fix_runs.reason("no_changes", said),
                states=("fixing",),
            )

    try:
        _settle_published(fix, turn, settle)
    except Exception:  # noqa: BLE001 - the check retries the landing
        logger.exception("harness_fix_published_failed", run=str(fix[0].id))


def on_publish_refused(
    fix: tuple[ReadinessFixRun, FixCommand], exc: HostedHarnessError, turn: Any
) -> None:
    """The fix turn's change was refused for good. Never raises."""
    if not fix_groups.enabled():
        return

    def settle(run, cmd, _conversation) -> None:
        fix_runs.settle(
            run,
            cmd,
            turn="publish_refused",
            code=exc.code,
            message="The change couldn't be kept",
            conversation=_conversation,
        )

    try:
        _settle_published(fix, turn, settle)
    except Exception:  # noqa: BLE001 - the check gives the landing up
        logger.exception("harness_fix_publish_refused_failed", run=str(fix[0].id))


def _answered_command(
    command: Message,
) -> tuple[ReadinessFixRun, FixCommand] | None:
    """The run (locked) and command whose question ``command`` answers, within its
    head; no query for other messages or while Fix is off."""
    if not command.reply_to or not fix_groups.enabled():
        return None
    question = str(command.reply_to)
    run = (
        fix_runs._runs(command.conversation.job_id)
        .select_for_update()
        .filter(commands__contains=[{"questions": [question]}])
        .order_by("-created_at")
        .first()
    )
    cmd = (
        next(
            (one for one in run.commands if question in (one.get("questions") or ())),
            None,
        )
        if run is not None
        else None
    )
    return (run, cmd) if run is not None and cmd is not None else None


def draft_command(command: Message | None) -> tuple[str, dict[str, Any]] | None:
    """``None`` drops a fix write whose run ended or command is not in flight, and
    gives its sandbox up once; ``("", {})`` keeps any other, and an answer sent after
    its command settled (the person's own write). Under the head lock."""
    if command is None:
        return "", {}
    payload = command.payload or {}
    if payload.get("_fix_run_id"):
        run = (
            ReadinessFixRun.no_workspace_objects.select_for_update()
            .filter(id=payload["_fix_run_id"])
            .first()
        )
        cmd = fix_runs.command(run, payload.get("_fix_command")) if run else None
        own = cmd is not None and str(cmd["message_id"]) == str(command.id)
    else:
        fix = _answered_command(command)
        if fix is None:
            return "", {}
        run, cmd = fix
        settled = _time(cmd.get("settled_at"))
        if settled is not None and command.created_at > settled:
            return "", {}
        own = True
    if (
        run is not None
        and cmd is not None
        and own
        and run.state in ACTIVE
        and cmd["state"] == "dispatched"
    ):
        return "", {}
    logger.info("harness_fix_late_write", message=str(command.id))
    if run is not None and cmd is not None and not cmd.get("retired"):
        before = fix_runs.snapshot(run)
        fix_runs.retire(run, cmd)
        fix_runs.store(run, before)
    return None


# --- stall recovery and the checkpoint basis ------------------------------------


def holds_conversation(conversation: HostedHarnessConversation) -> bool:
    """The active run's command or an answer is queued or delivered here: stall
    recovery leaves it, and what is behind it, to the fix check. Fails nothing."""
    if not fix_groups.enabled() or conversation.job.environment_id is not None:
        return False
    run = fix_runs.active_run(conversation.job_id)
    cmd = fix_runs.in_flight(run) if run is not None else None
    if run is None or cmd is None:
        return False
    held = Message.no_workspace_objects.filter(
        conversation=conversation,
        id__in=_ids(run, cmd),
        state__in=(Message.State.QUEUED, Message.State.DELIVERED),
    )
    if run.state == State.WAITING_ON_YOU:
        # Paused on the person: a message the chat took holds nothing.
        held = held.exclude(
            state=Message.State.DELIVERED,
            sequence__lte=conversation.command_acked_through,
        )
    return held.exists()


def last_settled_at(environment_id: Any) -> datetime | None:
    """When a fix command of the environment last settled; row-less ones never do."""
    if not fix_groups.enabled():
        return None
    settled = [
        when
        for commands in ReadinessFixRun.no_workspace_objects.filter(
            job_id=environment_id
        )
        .order_by("-created_at")
        .values_list("commands", flat=True)[:3]
        for one in commands or ()
        if (when := _time(one.get("settled_at"))) is not None
    ]
    return max(settled, default=None)


def only_agent_moved(
    command: Message | None, environment: HostedHarnessJob, basis: str
) -> bool:
    """The fix's own turn saved while only the default agent moved since its
    workspace was built on the run's base, so its checkpoint is kept."""
    if command is None or not fix_groups.enabled():
        return False
    if not (command.payload or {}).get("_fix_run_id") and not command.reply_to:
        return False
    run = fix_runs.active_run(environment.id)
    cmd = fix_runs.in_flight(run) if run is not None else None
    if (
        run is None
        or cmd is None
        or str(command.id) not in _ids(run, cmd)
        or environment.environment_version_id != run.base_environment_version_id
        or environment.suite_version_id != run.base_suite_version_id
        or not drafts.drafts_apply(environment)
    ):
        return False
    held = Message.no_workspace_objects.filter(
        id__in=[one["message_id"] for one in run.commands if one.get("message_id")]
        + _ids(run, cmd)
    )
    return rebuild.held_basis(
        basis, run.base_environment_version, run.base_suite_version, list(held)
    )
