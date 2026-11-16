"""Run readiness Fix driver: send a run's commands to the builder chat one at a time,
follow their turns, and settle each command from how its turn ended.

Every transition takes the environment head, then the run, then the conversation
(``fix_runs``' lock order), re-reads the run and does nothing unless it is still in
the state the transition expects, so redelivered or replayed events change nothing.

A command's turn may ask the person a question. The answer is a new message: the
turn waiting on it runs on with it, or, if that turn already ended, the answer gets a
turn of its own. ``current_command_id`` moves to an answer only when its own turn
starts.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

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
from simulate.services.harness_versioning import drafts, nudges, rebuild
from simulate.services.hosted_harness import HostedHarnessError

logger = logging.getLogger(__name__)

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
FIX_DISPATCH_GIVE_UP = timedelta(minutes=30)
FIX_QUESTION_GIVE_UP = timedelta(hours=24)
CHECK_FAILING_GIVE_UP = timedelta(minutes=10)
PROMPT_MAX = 500
# How each give-up settles: (outcome turn, code).
_GIVE_UPS = {
    "not_started": ("not_started", "not_started"),
    "stuck": ("stuck", "stuck"),
    "turn_interrupted": ("interrupted", "turn_interrupted"),
    "publish_failed": ("failed", "publish_failed"),
    "question_unanswered": ("failed", "question_unanswered"),
}
_UNANSWERED = (
    "The builder chat's question went unanswered; answer it in chat, then try the"
    " fix again"
)


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


def _ids(run: ReadinessFixRun, cmd: Mapping[str, Any]) -> list[str]:
    """Every message of the command: itself, the answers to its questions, and the one
    whose turn started last."""
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
    """Where the command's turn is: ``current`` is the message whose turn runs or
    ran last; ``pending`` an answer sent after that turn ended, waiting for a turn of
    its own; ``started``/``ended`` the current turn's events."""

    ids: list[str]
    current: str
    started: Event | None
    ended: Event | None
    pending: Message | None


def _flight(
    conversation: HostedHarnessConversation | None,
    run: ReadinessFixRun,
    cmd: Mapping[str, Any],
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
        # An answer the turn took before ALK ended it belongs to that turn; one the
        # chat never took, or sent after the turn ended, waits for a turn of its own.
        ended_at = ended.emitted_at or ended.created_at
        pending = next(
            (
                reply
                for reply in fix_runs.replies(cmd)
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
    """Fail a waiting message so no sandbox runs it; never an answer the chat already
    took, which the turn it answered consumed."""
    taken = conversation is not None and (
        message.sequence <= conversation.command_acked_through
    )
    if message.reply_to is None or not taken:
        rebuild.fail_command(message.id)


def run_for_command(
    command: Message | None,
) -> tuple[ReadinessFixRun, dict[str, Any]] | None:
    """The run and command a builder message belongs to: a fix's own command (even
    once its run ended, so a late write is recognized), or the person's answer to its
    question. No query for any other message, nor for an answer while Fix is off."""
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
    cmd: Mapping[str, Any],
    question_id: Any,
) -> bool:
    """Whether the question was asked in a turn of ``cmd``: the command itself, or an
    answer chained from one of its questions."""
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


def _remember(cmd: dict[str, Any], question_id: str) -> None:
    cmd["questions"] = list(dict.fromkeys([*(cmd.get("questions") or ()), question_id]))


def _ask(run: ReadinessFixRun, cmd: dict[str, Any], question_id: str) -> None:
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
            (prompt or "")[:PROMPT_MAX] or "The builder chat asked a question",
        ),
    )
    run.state = State.WAITING_ON_YOU


def _own_question(
    conversation: HostedHarnessConversation | None,
    run: ReadinessFixRun,
    cmd: Mapping[str, Any],
) -> str | None:
    """The open question in the chat, when a turn of ``cmd`` asked it."""
    question = _open_question(conversation)
    if question is None or conversation is None:
        return None
    return question if question_command(conversation, run, cmd, question) else None


def _answered(run: ReadinessFixRun, cmd: dict[str, Any]) -> None:
    """The person answered: the command's lines and the run work on."""
    run.question_message_id = None
    fix_runs.set_lines(run, cmd["n"], "fixing", states=("queued", "waiting_on_you"))
    run.state = State.FIXING


# --- dispatch -------------------------------------------------------------------


def _next(run: ReadinessFixRun) -> dict[str, Any] | None:
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
        cmd.update(
            state="dispatched",
            message_id=str(message.id),
            dispatched_at=timezone.now().isoformat(),
        )
        fix_runs.set_lines(run, cmd["n"], "fixing", states=("queued",))
        run.current_command_id = message.id
        run.question_message_id = None
        run.state = State.FIXING
        fix_runs.store(run, before)
        if first:
            # The run's first sandbox starts from the defaults it is based on; its
            # later commands and answers keep that working copy.
            rebuild._expire_chat_lease(conversation)
    logger.info("harness_fix_dispatched run=%s message=%s", run.id, message.id)
    if start_runtime and rebuild.sandbox_provider_configured():
        try:
            rebuild._start_runtime(str(conversation.id))
        except Exception:  # noqa: BLE001 - the recovery sweep starts it
            logger.exception("harness_fix_runtime_schedule_failed run=%s", run.id)
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
    Never raises; the check reconciles anything missed. Nothing while Fix is off."""
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
            "harness_fix_event_failed conversation=%s kind=%s", conversation_id, kind
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
        cmd.update(started_at=timezone.now().isoformat(), interrupted_at=None)
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
            if run.state == State.WAITING_ON_YOU and fix_runs.replies(cmd):
                _answered(run, cmd)  # answered inside this turn
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
    fix: tuple[ReadinessFixRun, dict[str, Any]],
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
    fix: tuple[ReadinessFixRun, dict[str, Any]], turn: Any, *, wrote: bool
) -> None:
    """A fix turn's checkpoint published (or it had none): the command settled, unless
    its turn waits on the person's answer. Idempotent; never raises."""
    if not fix_groups.enabled():
        return

    def settle(run, cmd, conversation) -> None:
        wrote_any = wrote or bool(cmd.get("wrote"))
        if _own_question(conversation, run, cmd) is not None:
            # Partial work published; the turn the answer drives settles it.
            cmd["wrote"] = wrote_any
            return
        fix_runs.settle(run, cmd, turn="success", wrote=wrote_any)

    try:
        _settle_published(fix, turn, settle)
    except Exception:  # noqa: BLE001 - the check retries the landing
        logger.exception("harness_fix_published_failed run=%s", fix[0].id)


def on_publish_refused(
    fix: tuple[ReadinessFixRun, dict[str, Any]], exc: HostedHarnessError, turn: Any
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
            message="The builder's change could not be kept",
            conversation=_conversation,
        )

    try:
        _settle_published(fix, turn, settle)
    except Exception:  # noqa: BLE001 - the check gives the landing up
        logger.exception("harness_fix_publish_refused_failed run=%s", fix[0].id)


def draft_command(command: Message | None) -> tuple[str, dict[str, Any]] | None:
    """``None`` drops a fix command's write: its run ended, or the command settled or
    is not the one in flight; its sandbox is then given up, once. ``("", {})`` keeps
    any other write, the person's answers included. Called under the head lock."""
    payload = (command.payload or {}) if command is not None else {}
    if command is None or not payload.get("_fix_run_id"):
        return "", {}
    run = (
        ReadinessFixRun.no_workspace_objects.select_for_update()
        .filter(id=payload["_fix_run_id"])
        .first()
    )
    cmd = fix_runs.command(run, payload.get("_fix_command")) if run else None
    if (
        run is not None
        and cmd is not None
        and run.state in ACTIVE
        and cmd["state"] == "dispatched"
        and str(cmd["message_id"]) == str(command.id)
    ):
        return "", {}
    logger.info("harness_fix_late_write message=%s", command.id)
    if run is not None and cmd is not None and not cmd.get("retired"):
        before = fix_runs.snapshot(run)
        fix_runs.retire(run, cmd)
        fix_runs.store(run, before)
    return None


# --- stall recovery and the checkpoint basis ------------------------------------


def holds_conversation(conversation: HostedHarnessConversation) -> bool:
    """The active run's command or an answer to it is queued or delivered in this
    conversation: stall recovery leaves it, and messages behind it, to the fix check.
    Holding fails nothing; an answer a turn took is never failed."""
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
    workspace was built: the run's environment and suite are still its base, and the
    workspace was built on them, so its checkpoint is kept."""
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


# --- the check ------------------------------------------------------------------


def check(environment_id: Any) -> None:
    """Move the environment's fix along: reconcile a question, end a command whose
    turn can't go on, dispatch the next one, and hand a settled run to autosave. A
    check failing for ``CHECK_FAILING_GIVE_UP`` ends the run. While Fix is switched
    off, runs are left alone."""
    if not fix_groups.enabled():
        return
    run = fix_runs.active_run(environment_id)
    if run is None:
        return
    try:
        _check(environment_id)
    except Exception:  # noqa: BLE001 - recorded; the tick runs it again
        logger.exception("harness_fix_check_failed environment=%s", environment_id)
        _record_failure(run)
    else:
        _clear_failure(run)


def _record_failure(run: ReadinessFixRun) -> None:
    now = timezone.now()
    with transaction.atomic():
        if nudges._head(run.job_id, lock=True) is None:
            return
        run = fix_runs.lock_run(run.id)
        if run.state not in ACTIVE:
            return
        autosave = dict(run.autosave or {})
        since = _time(autosave.get("failing_since"))
        if since is None:
            autosave["failing_since"] = now.isoformat()
            ReadinessFixRun.no_workspace_objects.filter(id=run.id).update(
                autosave=autosave
            )
    if since is not None and now - since > CHECK_FAILING_GIVE_UP:
        if run.state == State.CHECKING or fix_runs._saved(run):
            from simulate.services.harness_readiness import fix_save

            # It saved: it ends done, its re-check given up.
            fix_save.end_saved(run)
            return
        fix_runs.end_without_save(
            run, "internal_error", "The fix stopped on an error; try again"
        )


def _clear_failure(run: ReadinessFixRun) -> None:
    if not (run.autosave or {}).get("failing_since"):
        return
    with transaction.atomic():
        if nudges._head(run.job_id, lock=True) is None:
            return
        run = fix_runs.lock_run(run.id)
        autosave = {
            key: value
            for key, value in (run.autosave or {}).items()
            if key != "failing_since"
        }
        ReadinessFixRun.no_workspace_objects.filter(id=run.id).update(
            autosave=autosave or None
        )


def _check(environment_id: Any) -> None:
    _reconcile_question(environment_id)
    _give_up(environment_id)
    if not dispatch(environment_id):
        if _dispatch_deadline(environment_id):
            return
        _note_busy(environment_id)
    with _locked(environment_id) as locked:
        if locked is not None:
            _head, run = locked
            before = fix_runs.snapshot(run)
            fix_runs.promote_waiting(run)
            fix_runs.store(run, before)
    settled = fix_runs.active_run(environment_id)
    if settled is not None and (
        settled.state in (State.SAVING, State.CHECKING) or _all_settled(settled)
    ):
        _hand_off(settled)


def _reconcile_question(environment_id: Any) -> None:
    """The chat asks a question of the fix's turn, or its answer was sent."""
    with _locked(environment_id) as locked:
        if locked is None:
            return
        _head, run = locked
        cmd = fix_runs.in_flight(run)
        if run.state not in APPENDABLE:
            return
        before = fix_runs.snapshot(run)
        conversation = _lock_conversation(environment_id)
        if cmd is None:
            if run.state == State.WAITING_ON_YOU and _question_closed(
                conversation, run
            ):
                run.question_message_id = None
                run.state = State.FIXING
            fix_runs.store(run, before)
            return
        question = _own_question(conversation, run, cmd)
        if question is not None:
            _ask(run, cmd, question)
        elif run.state == State.WAITING_ON_YOU and (
            fix_runs.replies(cmd) or _question_closed(conversation, run)
        ):
            # Answered, or its turn ended without an answer (it was stopped).
            _answered(run, cmd)
        fix_runs.store(run, before)


def _question_closed(
    conversation: HostedHarnessConversation | None, run: ReadinessFixRun
) -> bool:
    """The turn that asked the run's question was interrupted or completed after
    asking; a sandbox restart alone, which may leave it unasked for a while, is not."""
    question = run.question_message_id
    if question is None:
        return True
    if conversation is None:
        return False
    asked = (
        Event.no_workspace_objects.filter(
            conversation=conversation, message_id=question, kind__in=_QUESTION_KINDS
        )
        .order_by("-sequence")
        .values_list("sequence", flat=True)
        .first()
    )
    return (
        asked is not None
        and Event.no_workspace_objects.filter(
            conversation=conversation,
            kind__in=("turn_interrupted", "turn_completed"),
            sequence__gt=asked,
        ).exists()
    )


def _interrupted_at(
    conversation: HostedHarnessConversation | None,
    cmd: Mapping[str, Any],
    flight: _Flight,
) -> datetime | None:
    """When the current turn was interrupted: as recorded, or from an interrupt event
    after its start that the hook missed."""
    recorded = _time(cmd["interrupted_at"])
    if flight.started is not None and recorded is not None:
        # One recorded before the current turn started is a previous turn's.
        if recorded < flight.started.created_at:
            recorded = None
    if recorded is None and conversation is not None and flight.started is not None:
        event = (
            Event.no_workspace_objects.filter(
                conversation=conversation,
                kind="turn_interrupted",
                sequence__gt=flight.started.sequence,
            )
            .order_by("sequence")
            .first()
        )
        recorded = event.created_at if event is not None else None
    if recorded is None:
        return None
    # An answer sent after the interrupt restarts the wait.
    answered = [reply.created_at for reply in fix_runs.replies(cmd)]
    return max([recorded, *answered])


def _working_since(cmd: Mapping[str, Any], flight: _Flight) -> datetime:
    """When the current turn last had work: its start, or the latest answer, so time
    paused on a question does not count."""
    started = flight.started.created_at if flight.started is not None else None
    answered = [reply.created_at for reply in fix_runs.replies(cmd)]
    return max(one for one in (started, *answered) if one is not None)


def _give_up(environment_id: Any) -> None:
    """Settle the in-flight command when its turn can't go on; retry a lost landing
    outside the locks. An unanswered question ends the run: the chat stays held by
    it, so no later command could go out."""
    now = timezone.now()
    retry: tuple[str, int] | None = None
    code = None
    with _locked(environment_id) as locked:
        if locked is None:
            return
        _head, run = locked
        cmd = fix_runs.in_flight(run)
        if run.state not in APPENDABLE or cmd is None:
            return
        conversation = _lock_conversation(environment_id)
        before = fix_runs.snapshot(run)
        flight = _flight(conversation, run, cmd)
        message = (
            flight.pending
            or Message.no_workspace_objects.filter(id=flight.current).first()
        )
        question = _own_question(conversation, run, cmd)
        interrupted = _interrupted_at(conversation, cmd, flight)
        if message is None:
            code = "not_started"
        elif flight.pending is not None or flight.started is None:
            # Waiting for a turn of its own.
            if rebuild._not_started(message, conversation, now):
                if flight.pending is not None:
                    # Sent after its turn ended: no turn will take it, even acked.
                    rebuild.fail_command(flight.pending.id)
                else:
                    _fail(message, conversation)
                code = "not_started"
        elif message.state == Message.State.FAILED:
            code = "not_started"
        elif question is not None:
            asked = Message.no_workspace_objects.filter(id=question).first()
            if asked is not None and now - asked.created_at > FIX_QUESTION_GIVE_UP:
                code = "question_unanswered"
        elif flight.ended is not None:
            payload = flight.ended.payload or {}
            waited = now - flight.ended.created_at
            # A replayed ending is the promotion's to decide, as a success's is.
            replayed = bool(payload.get("replayed"))
            if not replayed and (
                payload.get("interrupted") or payload.get("outcome") == "interrupted"
            ):
                fix_runs.settle(
                    run,
                    cmd,
                    turn="interrupted",
                    code="turn_interrupted",
                    conversation=conversation,
                )
            elif not replayed and payload.get("outcome") == "failed":
                fix_runs.settle(
                    run,
                    cmd,
                    turn="failed",
                    code="turn_failed",
                    conversation=conversation,
                )
            elif waited > rebuild.NUDGE_LANDING_RETRY_AFTER and (
                conversation is not None
                and cmd.get("landing_retried") != flight.ended.sequence
            ):
                # Tried once per turn before giving up, however late the check comes.
                cmd["landing_retried"] = flight.ended.sequence
                retry = (str(conversation.id), flight.ended.sequence)
            elif waited > rebuild.NUDGE_LANDING_GIVE_UP:
                code = "publish_failed"
        elif interrupted is not None:
            if now - interrupted > rebuild.NUDGE_NOT_STARTED_AFTER:
                code = "turn_interrupted"
        elif now - _working_since(cmd, flight) > rebuild.NUDGE_STUCK_AFTER and (
            rebuild._no_runtime(conversation, now)
        ):
            _fail(message, conversation)
            code = "stuck"
        if code is not None:
            turn, settled_code = _GIVE_UPS[code]
            fix_runs.settle(
                run, cmd, turn=turn, code=settled_code, conversation=conversation
            )
        fix_runs.store(run, before)
    if code == "question_unanswered":
        fix_runs.end_without_save(run, "question_unanswered", _UNANSWERED)
    if retry is not None:
        try:
            hhc.promote_turn_checkpoint(*retry)
        except HostedHarnessError as exc:
            logger.info(
                "harness_fix_landing_retry_refused environment=%s code=%s",
                environment_id,
                exc.code,
            )


def _note_busy(environment_id: Any) -> None:
    """The next command could not go out: its dispatch deadline counts from the
    first check that found the chat busy (not from a time Fix was switched off)."""
    with _locked(environment_id) as locked:
        if locked is None:
            return
        _head, run = locked
        cmd = _next(run)
        if (
            run.state not in _DISPATCHABLE
            or fix_runs.in_flight(run) is not None
            or cmd is None
            or cmd.get("busy_since")
        ):
            return
        before = fix_runs.snapshot(run)
        cmd["busy_since"] = timezone.now().isoformat()
        fix_runs.store(run, before)


def _dispatch_deadline(environment_id: Any) -> bool:
    """End a run whose next command found the chat busy for too long."""
    run = fix_runs.active_run(environment_id)
    if (
        run is None
        or run.state not in _DISPATCHABLE
        or fix_runs.in_flight(run) is not None
    ):
        return False
    cmd = _next(run)
    since = _time(cmd.get("busy_since")) if cmd is not None else None
    if since is None or timezone.now() - since <= FIX_DISPATCH_GIVE_UP:
        return False
    fix_runs.end_without_save(
        run, "chat_busy", "The builder chat stayed busy; try the fix again"
    )
    return True


def _all_settled(run: ReadinessFixRun) -> bool:
    return run.state in APPENDABLE and (
        all(one["state"] == "settled" for one in run.commands)
        and not any(
            line["state"] in ("queued", "waiting_on_other") for line in run.lines
        )
    )


def _hand_off(run: ReadinessFixRun) -> None:
    """Every command settled: autosave, the recheck and the verdict take over."""
    from simulate.services.harness_readiness import fix_save

    fix_save.advance(run)


def tick(*, limit: int = 200) -> int:
    """A check per environment with an active run, least recently ticked first and
    one organization at a time. Nothing while Fix is switched off."""
    from simulate.tasks import harness_nudges as tasks

    if not fix_groups.enabled():
        return 0
    environments = rebuild._fair(
        ReadinessFixRun.no_workspace_objects.filter(state__in=ACTIVE)
        .order_by("updated_at")
        .values_list("organization_id", "job_id"),
        limit,
    )
    ReadinessFixRun.no_workspace_objects.filter(
        job_id__in=environments, state__in=ACTIVE
    ).update(updated_at=timezone.now())
    for environment_id in environments:
        try:
            tasks.schedule_fix_check(environment_id)
        except Exception as exc:  # noqa: BLE001 - the next tick schedules it
            logger.warning(
                "harness_fix_check_schedule_failed environment=%s error=%s",
                environment_id,
                type(exc).__name__,
            )
    return len(environments)
