"""Run readiness Fix check: reconcile a run's question, give up a command whose turn
can't go on, dispatch the next one and hand a settled run to autosave. Same lock
order as ``fix_driver``."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from simulate.models import (
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationMessage,
)
from simulate.models.readiness_fix import ACTIVE, APPENDABLE, ReadinessFixRun
from simulate.services import hosted_harness_conversation as hhc
from simulate.services.harness_readiness import fix_groups, fix_runs
from simulate.services.harness_readiness.fix_driver import (
    _DISPATCHABLE,
    _QUESTION_KINDS,
    _answered,
    _ask,
    _fail,
    _Flight,
    _flight,
    _lock_conversation,
    _locked,
    _next,
    _own_question,
    _time,
    dispatch,
)
from simulate.services.harness_readiness.fix_runs import FixCommand
from simulate.services.harness_versioning import nudges, rebuild
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

State = ReadinessFixRun.State
Message = HostedHarnessConversationMessage
Event = HostedHarnessConversationEvent

FIX_DISPATCH_GIVE_UP = timedelta(minutes=30)
FIX_QUESTION_GIVE_UP = timedelta(hours=24)
CHECK_FAILING_GIVE_UP = timedelta(minutes=10)
# How each give-up settles: (outcome turn, code).
_GIVE_UPS = {
    "not_started": ("not_started", "not_started"),
    "stuck": ("stuck", "stuck"),
    "turn_interrupted": ("interrupted", "turn_interrupted"),
    "publish_failed": ("failed", "publish_failed"),
    "question_unanswered": ("failed", "question_unanswered"),
}
_UNANSWERED = "The builder chat's question went unanswered; answer it, then fix again"


# --- the check ------------------------------------------------------------------


def check(environment_id: Any) -> None:
    """Move the environment's fix along (see the module); failing for
    ``CHECK_FAILING_GIVE_UP`` ends the run. Nothing while Fix is switched off."""
    if not fix_groups.enabled():
        return
    run = fix_runs.active_run(environment_id)
    if run is None:
        return
    try:
        _check(environment_id)
    except Exception:  # noqa: BLE001 - recorded; the tick runs it again
        logger.exception("harness_fix_check_failed", environment=str(environment_id))
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
    cmd: FixCommand,
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


def _working_since(cmd: FixCommand, flight: _Flight) -> datetime:
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
                "harness_fix_landing_retry_refused",
                environment=str(environment_id),
                code=exc.code,
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
                "harness_fix_check_schedule_failed",
                environment=str(environment_id),
                error=type(exc).__name__,
            )
    return len(environments)
