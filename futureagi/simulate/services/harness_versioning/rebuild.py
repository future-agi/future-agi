"""Rebuild: a nudge answered "yes" runs as one builder turn in the environment's chat,
and its result lands as a new default version (or changes nothing).

The turn writes on top of the piece's open draft. Anything other than a landing puts
that draft back as it was. One rebuild is queued or running per environment; while it
is, the environment and suite defaults and their drafts are held.
"""

from __future__ import annotations

import json
import logging
import tarfile
from collections.abc import Mapping
from datetime import timedelta
from functools import partial
from itertools import zip_longest
from types import SimpleNamespace
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from simulate.models import (
    AgentVersion,
    HarnessEnvironmentVersion,
    HarnessNudge,
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationLease,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
)
from simulate.models.harness_versioning import DiscardReason, VersionStatus
from simulate.serializers.harness_job import CREDENTIAL_VALUE_MAX_CHARS
from simulate.services.harness_versioning import nudges
from simulate.services.harness_versioning import read as reads
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_diagnostics import _normalize_log_text

logger = logging.getLogger(__name__)

State = HarnessNudge.State
Target = HarnessNudge.Target
OPEN = nudges._IN_FLIGHT
Message = HostedHarnessConversationMessage

NUDGE_RESULT = ".futureagi-nudge-result.json"
PROOFS = "proofs.json"
NUDGE_NOT_STARTED_AFTER = timedelta(minutes=15)
NUDGE_LANDING_RETRY_AFTER = timedelta(minutes=5)
NUDGE_LANDING_GIVE_UP = timedelta(minutes=15)
NUDGE_STUCK_AFTER = timedelta(minutes=60)
BRIEFING_MAX = 64 * 1024
NOTES_MAX = 4000
PROBLEMS_MAX = 50
PROBLEM_TEXT_MAX = 500
# ALK writes up to PROBLEMS_MAX of each list and keeps the whole file under this.
RESULT_FILE_MAX = 4 * 1024 * 1024
PROOFS_FILE_MAX = 64 * 1024 * 1024
DEFERRED_RETRIES = 20
DEFERRED_RETRY_AFTER = timedelta(minutes=5)


def _lock_nudge(locked: HostedHarnessJob, nudge_id: Any) -> HarnessNudge | None:
    return (
        HarnessNudge.no_workspace_objects.select_for_update()
        .filter(job=locked, id=nudge_id)
        .first()
    )


def _open_nudge(environment_id: Any) -> HarnessNudge | None:
    return (
        HarnessNudge.no_workspace_objects.filter(job_id=environment_id, state__in=OPEN)
        .order_by("created_at")
        .first()
    )


def _conversation(environment_id: Any) -> HostedHarnessConversation | None:
    return HostedHarnessConversation.no_workspace_objects.filter(
        job_id=environment_id
    ).first()


def _trigger(nudge: HarnessNudge) -> AgentVersion | None:
    if nudge.trigger_agent_version_id is None:
        return None
    return AgentVersion._base_manager.filter(id=nudge.trigger_agent_version_id).first()


def _taken_at(nudge: HarnessNudge):
    return parse_datetime(str((nudge.draft_snapshot or {}).get("taken_at") or ""))


def _event_time(value: Any):
    if value is None or hasattr(value, "tzinfo"):
        return value
    return parse_datetime(str(value))


def schedule_nudge_check(environment_id: Any) -> None:
    """Check the environment's rebuild after the caller commits; never raises."""
    transaction.on_commit(partial(nudges._start, "check", str(environment_id)))


# --- reads the conversation and gateway ask -------------------------------------------


def nudge_for_command(message: Message | None) -> HarnessNudge | None:
    """The nudge whose builder message this is, in the message's own environment."""
    if message is None or not (message.payload or {}).get("nudge_id"):
        return None
    return HarnessNudge.no_workspace_objects.filter(
        id=message.payload["nudge_id"],
        command_message_id=message.id,
        job_id=message.conversation.job_id,
    ).first()


def turn_command(conversation: HostedHarnessConversation) -> Message | None:
    """The command whose turn is running (started, not yet acknowledged), else the
    last acknowledged one."""
    commands = conversation.messages.filter(role=Message.Role.USER)
    running = (
        commands.filter(
            state=Message.State.DELIVERED,
            sequence__gt=conversation.command_acked_through,
        )
        .order_by("sequence")
        .first()
    )
    if running is not None:
        return running
    return (
        commands.filter(sequence__lte=conversation.command_acked_through)
        .order_by("-sequence")
        .first()
    )


def only_agent_moved(command: Message | None, environment: HostedHarnessJob) -> bool:
    """A rebuild's save whose environment and suite defaults are still the ones it
    was queued on: they are held, so only a move of the default agent changed the
    authored revision since, and the rebuild still lands (Q2)."""
    from simulate.services.harness_versioning import drafts

    nudge = nudge_for_command(command)
    return (
        nudge is not None
        and nudge.state in OPEN
        and environment.environment_version_id == nudge.base_environment_version_id
        and environment.suite_version_id == nudge.base_suite_version_id
        and drafts.drafts_apply(environment)
    )


def landing_pending(environment: HostedHarnessJob) -> bool:
    """A rebuild's turn ended and its result has not landed yet."""
    return HarnessNudge.no_workspace_objects.filter(
        job_id=environment.id,
        state=State.RUNNING,
        draft_snapshot__turn_sequence__isnull=False,
    ).exists()


def nudge_composition(
    environment: HostedHarnessJob, conversation: HostedHarnessConversation | None
) -> str | None:
    """The piece a sandbox drafts on while a rebuild's command is the next one, else
    ``None`` (every draft over the defaults)."""
    if conversation is None or conversation.job_id != environment.id:
        return None
    nudge = (
        HarnessNudge.no_workspace_objects.filter(
            job_id=environment.id, state__in=OPEN, command_message__isnull=False
        )
        .only("target", "command_message_id")
        .first()
    )
    if nudge is None:
        return None
    from simulate.services.hosted_harness_conversation import _unacked_commands

    lowest = (
        _unacked_commands(conversation)
        .order_by("sequence")
        .values_list("id", flat=True)
        .first()
    )
    return nudge.target if lowest == nudge.command_message_id else None


def wanted_agent_version(
    environment: HostedHarnessJob, conversation: HostedHarnessConversation | None
) -> AgentVersion | None:
    """The agent a chat sandbox runs as: an environment rebuild's trigger, else the
    default when it has a credential set of its own; ``None`` keeps the payload's."""
    from simulate.services.harness_agent_versions import _side_row, own_credential_set

    if nudge_composition(environment, conversation) == Target.ENVIRONMENT:
        trigger = _trigger(_open_nudge(environment.id))
        if trigger is not None:
            return trigger
    if environment.agent_version_id is None:
        return None
    runtime = _side_row(environment.agent_version_id)
    if runtime is None or not own_credential_set(runtime):
        return None
    return AgentVersion._base_manager.filter(id=environment.agent_version_id).first()


def runs_as_wanted_agent(
    lease: HostedHarnessConversationLease, wanted: AgentVersion | None
) -> bool:
    """The sandbox runs as the agent chat wants; one with no agent of its own does."""
    return lease.agent_version_id is None or lease.agent_version_id == getattr(
        wanted, "id", None
    )


def _chat_lease(
    conversation: HostedHarnessConversation, *, live: bool = False
) -> HostedHarnessConversationLease | None:
    """The conversation's own sandbox lease, not released (nor expired, if live)."""
    gone = HostedHarnessConversationLease.State
    return (
        HostedHarnessConversationLease.no_workspace_objects.filter(
            conversation=conversation, attempt__isnull=True
        )
        .exclude(state__in=(gone.RELEASED, gone.EXPIRED) if live else (gone.RELEASED,))
        .first()
    )


def _runs_as_another_agent(conversation: HostedHarnessConversation) -> bool:
    lease = _chat_lease(conversation, live=True)
    return lease is not None and not runs_as_wanted_agent(
        lease, wanted_agent_version(conversation.job, conversation)
    )


def holds_conversation(conversation: HostedHarnessConversation) -> bool:
    """A rebuild's command waits in this conversation, or its result is landing: the
    conversation's messages are not abandoned meanwhile."""
    if conversation.job.environment_id is not None:
        return False
    return (
        landing_pending(conversation.job)
        or HarnessNudge.no_workspace_objects.filter(
            job_id=conversation.job_id,
            state__in=OPEN,
            command_message__state__in=(Message.State.QUEUED, Message.State.DELIVERED),
        ).exists()
    )


def waiting_since(conversation: HostedHarnessConversation, message: Message):
    """When a message started waiting: a rebuild it queued behind restarts the clock."""
    closed = (
        HarnessNudge.no_workspace_objects.filter(
            job_id=conversation.job_id, closed_at__isnull=False
        )
        .order_by("-closed_at")
        .values_list("closed_at", flat=True)
        .first()
    )
    return max(message.created_at, closed) if closed else message.created_at


# --- answer and stop -------------------------------------------------------------------


def _check_answerable(environment: HostedHarnessJob, nudge_id: Any) -> HarnessNudge:
    from simulate.services.harness_versioning import drafts

    nudge = HarnessNudge.no_workspace_objects.filter(
        job=environment, id=nudge_id
    ).first()
    if nudge is None:
        raise nudges.nudge_not_found()
    if nudge.state not in (State.PENDING, State.FAILED):
        raise nudges._closed(nudge)
    if not nudges.nudges_enabled():
        raise drafts._error("nudges_disabled", "Rebuilds aren't switched on", 409)
    return nudge


def _facts(environment: HostedHarnessJob, nudge: HarnessNudge) -> dict[str, Any]:
    """The facts a rebuild is briefed with; an environment version never changes, so a
    suite rebuild keeps its own."""
    if nudge.target == Target.SUITE:
        return dict(nudge.facts or {})
    return nudges.nudge_facts(environment, nudge)


def answer_rebuild(
    environment: HostedHarnessJob, nudge_id: Any, *, user: Any
) -> HarnessNudge:
    """Queue the rebuild a pending (or failed, "Rebuild again") nudge offers."""
    from simulate.services.harness_agent_versions import _busy
    from simulate.services.harness_usage import require_harness_authoring
    from simulate.services.harness_versioning import drafts

    nudge = _check_answerable(environment, nudge_id)
    require_harness_authoring(str(environment.organization_id))
    # Outside any transaction: it may read storage or GitHub.
    facts = _facts(environment, nudge)
    replaced = False
    with transaction.atomic():
        locked = nudges._head(environment.id, lock=True)
        if locked is None:
            raise nudges.nudge_not_found()
        if _busy(locked):
            raise drafts._error(
                "environment_building", "The environment is still being built", 409
            )
        nudge = _lock_nudge(locked, nudge_id)
        if nudge is None:
            raise nudges.nudge_not_found()
        if nudge.state not in (State.PENDING, State.FAILED):
            raise nudges._closed(nudge)
        if (
            nudge.state == State.FAILED
            and HarnessNudge.no_workspace_objects.filter(
                job=locked, target=nudge.target, state=State.PENDING
            ).exists()
        ):
            raise nudges._closed(SimpleNamespace(state=State.REPLACED))
        if (
            nudge.target == Target.SUITE
            and nudge.base_environment_version_id != locked.environment_version_id
        ):
            nudge.state = State.REPLACED
            nudge.save(update_fields=["state", "updated_at"])
            replaced = True
        else:
            for older in HarnessNudge.no_workspace_objects.select_for_update().filter(
                job=locked, state__in=OPEN
            ):
                if older.target != nudge.target:
                    raise drafts.rebuild_running(older.target)
                close_nudge(
                    older,
                    locked=locked,
                    state=State.REPLACED,
                    error={"code": "stopped_by_newer", "by": str(nudge.id)},
                )
            _queue(locked, nudge, facts, user=user)
    if replaced:
        raise nudges._closed(nudge)
    logger.info("harness_nudge_answered nudge=%s answer=yes", nudge.id)
    return nudge


def _queue(
    locked: HostedHarnessJob, nudge: HarnessNudge, facts: dict, *, user: Any
) -> None:
    from simulate.services.harness_versioning import drafts

    nudge.answered_by = user
    nudge.answered_at = timezone.now()
    nudge.facts = facts
    nudge.base_environment_version_id = (
        locked.environment_version_id
        if nudge.target == Target.ENVIRONMENT
        else nudge.base_environment_version_id
    )
    nudge.base_suite_version_id = locked.suite_version_id
    nudge.draft_snapshot = {"answered_default_agent_id": _id(locked.agent_version_id)}
    nudge.command_message = None
    nudge.error = None
    nudge.summary = None
    nudge.result_environment_version = None
    nudge.result_suite_version = None
    nudge.closed_at = None
    if nudge.target == Target.ENVIRONMENT and nudges.facts_empty(facts):
        _close_no_change(locked, nudge)
        return
    draft = drafts.open_draft(locked, nudge.target, lock=True)
    if draft is not None and drafts.is_stale(draft, locked):
        draft.status = VersionStatus.DISCARDED
        draft.discard_reason = DiscardReason.SET_ASIDE_BY_REBUILD
        draft.nudge = nudge
        draft.save(update_fields=["status", "discard_reason", "nudge", "updated_at"])
        nudge.summary = {
            "set_aside": {
                "draft_version_id": str(draft.id),
                "started_from_label": reads._label(
                    getattr(draft.parent, "version_number", None)
                ),
            }
        }
    nudge.state = State.QUEUED
    nudge.save()
    schedule_nudge_check(locked.id)


def _id(value: Any) -> str | None:
    return str(value) if value else None


def stop_rebuild(
    environment: HostedHarnessJob, nudge_id: Any, *, user: Any
) -> HarnessNudge:
    """The person stops a queued or running rebuild; it can be run again."""
    with transaction.atomic():
        locked = nudges._head(environment.id, lock=True)
        nudge = _lock_nudge(locked, nudge_id) if locked is not None else None
        if nudge is None:
            raise nudges.nudge_not_found()
        if nudge.state not in OPEN:
            raise nudges._closed(nudge)
        close_nudge(
            nudge, locked=locked, state=State.FAILED, error={"code": "stopped_by_user"}
        )
    logger.info(
        "harness_nudge_stopped nudge=%s actor=%s", nudge.id, getattr(user, "id", None)
    )
    return nudge


# --- closing -------------------------------------------------------------------------


def fail_nudge_turn(nudge: HarnessNudge) -> None:
    """Mark only the rebuild's own builder message failed, so no sandbox runs it."""
    if nudge.command_message_id:
        Message.no_workspace_objects.filter(
            id=nudge.command_message_id,
            state__in=(Message.State.QUEUED, Message.State.DELIVERED),
        ).update(state=Message.State.FAILED, updated_at=timezone.now())


def close_nudge(
    nudge: HarnessNudge,
    *,
    locked: HostedHarnessJob,
    state: str,
    error: dict | None = None,
    summary: dict | None = None,
    turn_event: Any = None,
    restore: bool = True,
) -> None:
    """The one closer of a queued or running rebuild; the caller holds the
    environment and the nudge."""
    snapshot = dict(nudge.draft_snapshot or {})
    started = bool(snapshot.get("taken_at"))
    if started and state != State.APPLIED and restore:
        restore_draft_snapshot(nudge, locked=locked)
    fail_nudge_turn(nudge)
    now = timezone.now()
    if started:
        record_usage(
            nudge,
            event=turn_event,
            started_at=_taken_at(nudge),
            ended_at=now,
            stopped=turn_event is None,
        )
    nudge.state = state
    nudge.error = error
    if summary:
        nudge.summary = {**(nudge.summary or {}), **summary}
    nudge.closed_at = now
    nudge.save()
    logger.info(
        "harness_nudge_closed nudge=%s state=%s code=%s",
        nudge.id,
        state,
        (error or {}).get("code"),
    )
    transaction.on_commit(
        partial(
            _after_close,
            str(locked.id),
            dispatched=bool(nudge.command_message_id),
        )
    )


def _expire_chat_lease(conversation: HostedHarnessConversation) -> str:
    """Expire the conversation's own sandbox lease; its provider ref, if it had one."""
    lease = _chat_lease(conversation)
    if lease is None:
        return ""
    HostedHarnessConversationLease.no_workspace_objects.filter(id=lease.id).update(
        state=HostedHarnessConversationLease.State.EXPIRED, updated_at=timezone.now()
    )
    ref = str(lease.provider_ref or "")
    return "" if ref.startswith("pending:") else ref


def _start_runtime(conversation_id: str) -> None:
    from simulate.services.hosted_harness_ingress import _public_base_url
    from simulate.tasks.hosted_harness_conversation import schedule_conversation_runtime

    schedule_conversation_runtime(conversation_id, _public_base_url(None))


def _after_close(environment_id: str, *, dispatched: bool) -> None:
    """Hand the chat back: its sandbox stops, and it restarts for waiting messages."""
    from simulate.services.hosted_harness_conversation import (
        _ACTIVE_CONVERSATION_STATES,
        _unacked_commands,
        settle_interrupted_turn,
    )
    from simulate.tasks import harness_nudges as tasks

    try:
        if dispatched:
            with transaction.atomic():
                conversation = (
                    HostedHarnessConversation.no_workspace_objects.select_for_update()
                    .filter(job_id=environment_id)
                    .first()
                )
                if conversation is None:
                    return
                ref = _expire_chat_lease(conversation)
                waiting = _unacked_commands(conversation).exists()
                if conversation.state in _ACTIVE_CONVERSATION_STATES:
                    # The rebuild's turn (or its sandbox's start) is over.
                    settle_interrupted_turn(conversation)
                    conversation.state = (
                        HostedHarnessConversation.State.STARTING
                        if waiting
                        else HostedHarnessConversation.State.COLD
                    )
                    conversation.active_invocation_id = None
                    conversation.save(
                        update_fields=["state", "active_invocation_id", "updated_at"]
                    )
            if ref:
                tasks.schedule_sandbox_stop(str(conversation.id), ref)
            if nudges._head(environment_id) is None:
                return
            if waiting:
                try:
                    _start_runtime(str(conversation.id))
                except Exception:  # noqa: BLE001 - the recovery sweep starts it
                    logger.exception(
                        "harness_nudge_runtime_schedule_failed conversation=%s",
                        conversation.id,
                    )
        if nudges._head(environment_id) is not None:
            tasks.schedule_nudge_check_task(environment_id)
    except Exception:  # noqa: BLE001 - the tick and the recovery sweep retry
        logger.exception(
            "harness_nudge_after_close_failed environment=%s", environment_id
        )


def restore_draft_snapshot(nudge: HarnessNudge, *, locked: HostedHarnessJob) -> None:
    """Put the piece's draft back as the rebuild found it; idempotent. A draft another
    writer (a run's chat) saved or discarded last is kept."""
    from simulate.services.harness_versioning import drafts

    snapshot = (nudge.draft_snapshot or {}).get("piece") or {}
    model = drafts._model(nudge.target)
    draft_id = snapshot.get("draft_version_id")
    others = model.no_workspace_objects.filter(job=locked, status=VersionStatus.DRAFT)
    if draft_id:
        others = others.exclude(id=draft_id)
    for created in others.filter(origin="nudge").select_for_update():
        created.status = VersionStatus.DISCARDED
        created.discard_reason = DiscardReason.REBUILD_RESTORED
        created.save(update_fields=["status", "discard_reason", "updated_at"])
    if not draft_id or others.exists():
        return
    row = model._base_manager.filter(job=locked, id=draft_id, deleted=False).first()
    discarded_by = (row.documents or {}).get("discarded_by") if row else None
    if (
        row is None
        or row.discard_reason == DiscardReason.SET_ASIDE_BY_REBUILD
        or (row.origin != "nudge" and discarded_by != "nudge")
        # A chat that saved the default discarded the rebuild's draft.
        or discarded_by not in (None, "nudge")
        or (
            row.status == VersionStatus.DRAFT
            and row.hash == snapshot.get("hash")
            and row.revision == snapshot.get("revision")
        )
    ):
        return
    drafts.restore_draft(
        locked,
        nudge.target,
        draft_id=draft_id,
        archive_object_key=snapshot["archive_object_key"],
        revision=int(snapshot.get("revision") or 1),
        last_edited_by_id=snapshot.get("last_edited_by_id"),
        origin=snapshot.get("origin"),
    )


def close_open_nudges(locked: HostedHarnessJob, *, code: str) -> None:
    """Close a deleted environment's queued and running rebuilds as failed; their
    drafts go with it, so none is restored."""
    for nudge in HarnessNudge.no_workspace_objects.select_for_update().filter(
        job=locked, state__in=OPEN
    ):
        close_nudge(
            nudge,
            locked=locked,
            state=State.FAILED,
            error={"code": code},
            restore=False,
        )


# --- usage ---------------------------------------------------------------------------

_USAGE_FIELDS = (
    "tokens_in",
    "tokens_out",
    "tokens_cached",
    "usd",
    "unpriced_turns",
    "models",
)


def record_usage(
    nudge: HarnessNudge,
    *,
    event: Any,
    started_at: Any,
    ended_at: Any = None,
    stopped: bool = False,
) -> None:
    """One attempt's spend, keyed by its builder message; never billed. The caller
    saves the nudge."""
    key = _id(nudge.command_message_id)
    usage = dict(nudge.usage or {})
    attempts = [dict(one) for one in usage.get("attempts") or []]
    entry = next(
        (one for one in attempts if one.get("command_message_id") == key), None
    )
    if entry is None:
        entry = {"command_message_id": key, **dict.fromkeys(_USAGE_FIELDS)}
        attempts.append(entry)
    spent = (getattr(event, "payload", None) or {}).get("usage") or {}
    for field in _USAGE_FIELDS:
        if entry.get(field) is None and field in spent:
            entry[field] = spent[field]
    end = _event_time(getattr(event, "emitted_at", None)) or ended_at
    if entry.get("turn_started_at") is None and started_at is not None:
        entry["turn_started_at"] = started_at.isoformat()
    if entry.get("turn_ended_at") is None and end is not None:
        entry["turn_ended_at"] = end.isoformat()
        if started_at is not None:
            entry["sandbox_seconds"] = max(0, int((end - started_at).total_seconds()))
    if event is not None:
        entry["partial"] = False
        entry["stopped"] = False
    elif entry.get("stopped") is None:
        entry["stopped"] = bool(stopped)
        entry["partial"] = True
    usage["attempts"] = attempts
    nudge.usage = usage


# --- the agent default (D7) ----------------------------------------------------------


def _agent_move_refusal(
    locked: HostedHarnessJob, nudge: HarnessNudge, trigger: AgentVersion
) -> str | None:
    """Why an environment rebuild leaves the default agent where it is."""
    from simulate.services.harness_agent_versions import (
        Status,
        _side_row,
        require_agent_pick,
    )

    answered = (nudge.draft_snapshot or {}).get("answered_default_agent_id")
    if _id(locked.agent_version_id) not in (answered, str(trigger.id)):
        return "default_moved_by_hand"
    runtime = _side_row(trigger.id, lock=True)
    if runtime is None or runtime.preflight_status != Status.PASSED:
        return "not_pickable"
    try:
        require_agent_pick(locked, trigger.id, refusal="not pickable", lock=True)
    except HostedHarnessError:
        return "not_pickable"
    return None


def _record_agent_move(locked: HostedHarnessJob, nudge: HarnessNudge) -> None:
    from simulate.services.harness_agent_versions import (
        _record_activation,
        record_hand_move,
    )

    record_hand_move(locked, nudge.answered_by, nudge.trigger_agent_version_id)
    _record_activation(nudge.trigger_agent_version_id, {str(locked.id): "activated"})


def _no_change_move(locked: HostedHarnessJob, nudge: HarnessNudge) -> dict[str, Any]:
    """Nothing to change: the default agent moves to the trigger (unless moved by hand
    meanwhile) and the default environment version is recorded as fitting it."""
    from simulate.services.harness_agent_versions import hand_move
    from simulate.services.harness_versioning.versions import move_agent

    trigger = _trigger(nudge)
    if nudge.target != Target.ENVIRONMENT or trigger is None:
        return {}
    reason = None
    if locked.agent_version_id != trigger.id:
        reason = _agent_move_refusal(locked, nudge, trigger)
        if reason is None:
            try:
                with transaction.atomic():
                    move_agent(
                        locked,
                        trigger,
                        moved=hand_move(nudge.answered_by, trigger.id),
                    )
            except HostedHarnessError as exc:
                locked.refresh_from_db()
                reason = (
                    "not_prepared" if exc.code == "rollback_unavailable" else exc.code
                )
            else:
                from simulate.services.harness_agent_versions import _record_activation

                _record_activation(trigger.id, {str(locked.id): "activated"})
    moved = locked.agent_version_id == trigger.id
    if moved and locked.environment_version_id:
        # E already fits the trigger: later nudges and the run warning see that.
        HarnessEnvironmentVersion.no_workspace_objects.filter(
            id=locked.environment_version_id
        ).update(agent_version_id=trigger.id, updated_at=timezone.now())
    if reason is not None:
        logger.info(
            "harness_nudge_agent_not_moved nudge=%s reason=%s", nudge.id, reason
        )
    return {
        "agent_default_moved": moved,
        **({"agent_not_moved": reason} if reason else {}),
    }


def _fits_line(locked: HostedHarnessJob, nudge: HarnessNudge) -> str:
    environment = reads._message_label(
        reads._label(getattr(locked.environment_version, "version_number", None))
    )
    if nudge.target == Target.SUITE:
        suite = reads._message_label(
            reads._label(getattr(locked.suite_version, "version_number", None))
        )
        return f"{suite} already fits {environment}"
    agent = reads._message_label(nudges._trigger_label(nudge))
    return f"{environment} already fits {agent}"


def _close_no_change(
    locked: HostedHarnessJob, nudge: HarnessNudge, *, turn_event: Any = None, **extra
) -> None:
    close_nudge(
        nudge,
        locked=locked,
        state=State.NO_CHANGE,
        turn_event=turn_event,
        summary={
            "line": _fits_line(locked, nudge),
            **extra,
            **_no_change_move(locked, nudge),
        },
    )


# --- dispatch ------------------------------------------------------------------------


def _patches(facts: Mapping[str, Any]) -> list[str]:
    found = [
        f"--- {one.get('path')}\n{one['patch']}"
        for one in facts.get("changed_files") or []
        if isinstance(one, Mapping) and one.get("patch")
    ]
    if facts.get("prompt_patch"):
        found.insert(0, f"--- prompt\n{facts['prompt_patch']}")
    return found


def briefing(environment: HostedHarnessJob, nudge: HarnessNudge) -> str:
    """The turn's text: names, labels, counts and the stored (redacted) patches only,
    at most ``BRIEFING_MAX`` bytes; patches go first when it is too long."""
    facts = nudge.facts or {}
    label = reads._message_label(nudges._trigger_label(nudge))
    if nudge.target == Target.SUITE:
        head = [
            f"Rebuild the scenarios for environment {label}.",
            f"What changed in the environment: {facts.get('environment_summary') or 'see the diff'}.",
            "Environment diff (names only): "
            + json.dumps(facts.get("environment_diff") or {}, sort_keys=True),
        ]
    else:
        base = reads._message_label(_labels(nudge)["agent"])
        head = [
            f"Rebuild the environment for agent {label} (it was built for {base}).",
            f"What changed in the agent: {nudges._changes(facts)}.",
            "Changed files: "
            + (
                ", ".join(
                    f"{one.get('path')} ({one.get('status')})"
                    for one in facts.get("changed_files") or []
                    if isinstance(one, Mapping)
                )
                or "none listed"
            ),
        ]
    if nudge.target == Target.SUITE and _added_sub_goals(nudge):
        head.append(
            "Every sub-goal the environment added must be named by at least one"
            " scenario; one scenario may cover several."
        )
    if (nudge.usage or {}).get("attempts"):
        head.append(
            "An earlier attempt at this update ended without landing; none of its"
            " changes were kept. Start from the workspace as it is now and redo"
            " every change."
        )
    head.append("Load builder/agent-update first, and end with report_update.")
    text = "\n".join(head)
    patches = _patches(facts)
    for index, patch in enumerate(patches):
        more = f"\n\nand {len(patches) - index} more patches not shown"
        if len((text + "\n\n" + patch + more).encode()) > BRIEFING_MAX:
            text += more
            break
        text += "\n\n" + patch
    return text.encode()[:BRIEFING_MAX].decode(errors="ignore")


def _user_draft_keys(locked: HostedHarnessJob) -> list[str]:
    """The scenarios the open suite draft adds or changes: the person's own edits."""
    from simulate.services.harness_versioning import drafts

    draft = drafts.fresh_draft(locked, Target.SUITE)
    diff = draft.diff if draft is not None and isinstance(draft.diff, dict) else {}
    return sorted({*(diff.get("added") or []), *(diff.get("changed") or {})})


def _labels(nudge: HarnessNudge) -> dict[str, str | None]:
    refs = reads.version_refs(
        nudge.base_agent_version,
        nudge.base_environment_version,
        nudge.base_suite_version,
    )
    return {piece: (ref or {}).get("label") for piece, ref in refs.items()}


def _promote_finished_turn(conversation: HostedHarnessConversation) -> int:
    """Publish the last finished turn before the rebuild composes on the drafts."""
    from simulate.services.hosted_harness_conversation import promote_turn_checkpoint

    sequence = (
        HostedHarnessConversationEvent.no_workspace_objects.filter(
            conversation=conversation,
            kind="turn_completed",
            payload__outcome="success",
        )
        .order_by("-sequence")
        .values_list("sequence", flat=True)
        .first()
    ) or 0
    if sequence:
        try:
            promote_turn_checkpoint(str(conversation.id), sequence)
        except HostedHarnessError as exc:
            logger.info(
                "harness_nudge_dispatch_promote_refused conversation=%s code=%s",
                conversation.id,
                exc.code,
            )
    return sequence


def sandbox_provider_configured() -> bool:
    from simulate.services.hosted_harness_gateway import HostedHarnessGateway

    try:
        HostedHarnessGateway()
    except HostedHarnessError:
        return False
    return True


def _chat_idle(conversation: HostedHarnessConversation) -> bool:
    from simulate.services.hosted_harness_conversation import _unacked_commands

    return (
        conversation.state
        in (
            HostedHarnessConversation.State.COLD,
            HostedHarnessConversation.State.WARM_IDLE,
            HostedHarnessConversation.State.DEGRADED,
        )
        and conversation.blocking_input is None
        and not _unacked_commands(conversation).exists()
    )


def dispatch_nudge(environment_id: Any, *, start_runtime: bool = True) -> bool:
    """Put a queued rebuild's builder message into an idle chat."""
    from simulate.services.hosted_harness_conversation import (
        enqueue_message,
        ensure_conversation,
        environment_authoring_revision,
    )

    environment = nudges._head(environment_id)
    nudge = (
        HarnessNudge.no_workspace_objects.filter(
            job_id=environment_id, state=State.QUEUED, command_message__isnull=True
        ).first()
        if environment is not None
        else None
    )
    if nudge is None:
        return False
    # Read without locks first: a busy chat waits with no GitHub, storage or
    # checkpoint reads.
    conversation = _conversation(environment_id)
    if conversation is not None and not _chat_idle(conversation):
        return False
    if nudge.target == Target.ENVIRONMENT:
        facts = nudges.nudge_facts(environment, nudge)
        if nudges.facts_empty(facts):
            with transaction.atomic():
                locked = nudges._head(environment_id, lock=True)
                current = _lock_nudge(locked, nudge.id)
                if current is not None and current.state == State.QUEUED:
                    _close_no_change(locked, current)
            return False
    revision = environment_authoring_revision(environment)
    content = briefing(environment, nudge)
    conversation = ensure_conversation(environment)
    promoted = _promote_finished_turn(conversation)
    with transaction.atomic():
        nudge = (
            HarnessNudge.no_workspace_objects.select_for_update(skip_locked=True)
            .filter(id=nudge.id, state=State.QUEUED, command_message__isnull=True)
            .first()
        )
        if nudge is None:
            return False
        conversation = (
            HostedHarnessConversation.no_workspace_objects.select_for_update().get(
                id=conversation.id
            )
        )
        idle = (
            _chat_idle(conversation)
            and not HostedHarnessConversationEvent.no_workspace_objects.filter(
                conversation=conversation,
                kind="turn_completed",
                payload__outcome="success",
                sequence__gt=promoted,
            ).exists()
        )
        if not idle:
            return False
        head = HostedHarnessJob.no_workspace_objects.get(id=environment_id)
        _same, message, _created = enqueue_message(
            environment,
            content=content,
            # One per attempt: a Rebuild again within the same second is another.
            client_request_id=(
                f"nudge-{nudge.id}-{int(nudge.answered_at.timestamp() * 1_000_000)}"
            ),
            kind="nudge",
            payload={
                "nudge_id": str(nudge.id),
                "stage": nudge.target,
                "trigger_label": nudges._trigger_label(nudge),
                "base_labels": _labels(nudge),
                "user_draft_keys": (
                    _user_draft_keys(head) if nudge.target == Target.SUITE else []
                ),
                **(
                    {"new_sub_goals": _added_sub_goals(nudge)}
                    if nudge.target == Target.SUITE
                    else {}
                ),
            },
            server_payload=(
                {"_author_id": str(nudge.answered_by_id)}
                if nudge.answered_by_id
                else {}
            ),
            expected_revision=revision,
        )
        nudge.command_message = message
        nudge.save(update_fields=["command_message", "updated_at"])
        # The next sandbox composes the rebuild's workspace and runs as its agent.
        _expire_chat_lease(conversation)
    logger.info("harness_nudge_dispatched nudge=%s message=%s", nudge.id, message.id)
    if start_runtime and sandbox_provider_configured():
        try:
            _start_runtime(str(conversation.id))
        except Exception:  # noqa: BLE001 - the recovery sweep starts it
            logger.exception("harness_nudge_runtime_schedule_failed nudge=%s", nudge.id)
    return True


# --- the turn ------------------------------------------------------------------------


def start_nudge_turn(nudge_id: Any, *, message_id: Any, started_at: Any) -> None:
    """The rebuild's turn started: snapshot the piece's draft; idempotent."""
    from simulate.services.harness_versioning import drafts

    with transaction.atomic():
        nudge = HarnessNudge.no_workspace_objects.filter(id=nudge_id).first()
        locked = nudges._head(nudge.job_id, lock=True) if nudge is not None else None
        nudge = _lock_nudge(locked, nudge_id) if locked is not None else None
        if (
            nudge is None
            or nudge.state != State.QUEUED
            or str(nudge.command_message_id) != str(message_id)
        ):
            return
        draft = drafts.fresh_draft(locked, nudge.target)
        version = draft or getattr(locked, f"{nudge.target}_version")
        taken = _event_time(started_at) or timezone.now()
        nudge.draft_snapshot = {
            **(nudge.draft_snapshot or {}),
            "target": nudge.target,
            "taken_at": taken.isoformat(),
            "piece": {
                "draft_version_id": _id(getattr(draft, "id", None)),
                "archive_object_key": version.archive_object_key,
                "hash": version.hash,
                "revision": getattr(draft, "revision", None),
                "origin": getattr(draft, "origin", None),
                "last_edited_by_id": _id(getattr(draft, "last_edited_by_id", None)),
                "diff": draft.diff if draft is not None else None,
            },
            # The agent whose values the turn's sandbox holds, masked at landing.
            "ran_as_agent_version_id": _id(
                HostedHarnessConversationLease.no_workspace_objects.filter(
                    conversation__messages=nudge.command_message_id
                )
                .values_list("agent_version_id", flat=True)
                .first()
            ),
            "turn_sequence": None,
        }
        nudge.state = State.RUNNING
        nudge.save(update_fields=["draft_snapshot", "state", "updated_at"])
    logger.info("harness_nudge_running nudge=%s", nudge.id)


def on_turn_event(
    conversation_id: str, kind: str, payload: Mapping[str, Any], sequence: int, at: Any
) -> None:
    """After a turn event commits: start, end or re-check the environment's rebuild."""
    try:
        conversation = HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation_id, job__environment__isnull=True
        ).first()
        if conversation is None:
            return
        command = (
            conversation.messages.filter(
                id=payload.get("command_message_id"), role=Message.Role.USER
            ).first()
            if payload.get("command_message_id")
            else None
        )
        nudge = nudge_for_command(command)
        if nudge is not None and kind == "turn_started":
            if (nudge.draft_snapshot or {}).get("interrupted"):
                # The turn runs again in full; its own ending decides.
                HarnessNudge.no_workspace_objects.filter(
                    id=nudge.id, state__in=OPEN
                ).update(
                    draft_snapshot={
                        key: value
                        for key, value in nudge.draft_snapshot.items()
                        if key != "interrupted"
                    },
                    updated_at=timezone.now(),
                )
            start_nudge_turn(nudge.id, message_id=command.id, started_at=at)
        elif nudge is not None and kind == "turn_interrupted":
            HarnessNudge.no_workspace_objects.filter(
                id=nudge.id, state__in=OPEN
            ).update(
                draft_snapshot={**(nudge.draft_snapshot or {}), "interrupted": True},
                updated_at=timezone.now(),
            )
        elif nudge is not None and kind == "turn_completed":
            end_nudge_turn(conversation_id, nudge.id, sequence, payload)
        elif (
            nudge is None
            and kind == "turn_completed"
            and _runs_as_another_agent(conversation)
        ):
            # Its turn is over: the sandbox stops and chat restarts as the agent wanted.
            _after_close(str(conversation.job_id), dispatched=True)
        if (
            kind != "turn_started"
            and HarnessNudge.no_workspace_objects.filter(
                job_id=conversation.job_id,
                state=State.QUEUED,
                command_message__isnull=True,
            ).exists()
        ):
            schedule_nudge_check(conversation.job_id)
    except Exception:  # noqa: BLE001 - the tick re-checks it
        logger.exception(
            "harness_nudge_turn_event_failed conversation=%s", conversation_id
        )


def end_nudge_turn(
    conversation_id: str, nudge_id: Any, sequence: int, payload: Mapping[str, Any]
) -> None:
    """The fence: the rebuild's turn ended, so its sandbox takes nothing more and no
    sandbox starts until the result lands."""
    from simulate.services.hosted_harness_conversation import (
        _schedule_checkpoint_promotion,
    )
    from simulate.tasks import harness_nudges as tasks

    with transaction.atomic():
        nudge = HarnessNudge.no_workspace_objects.filter(id=nudge_id).first()
        if nudge is not None and nudge.state == State.QUEUED:
            start_nudge_turn(
                nudge.id, message_id=nudge.command_message_id, started_at=None
            )
        locked = nudges._head(nudge.job_id, lock=True) if nudge is not None else None
        nudge = _lock_nudge(locked, nudge_id) if locked is not None else None
        if nudge is None or nudge.state != State.RUNNING:
            return
        snapshot = dict(nudge.draft_snapshot or {})
        if snapshot.get("turn_sequence") is None:
            snapshot["turn_sequence"] = sequence
            nudge.draft_snapshot = snapshot
            nudge.save(update_fields=["draft_snapshot", "updated_at"])
        conversation = (
            HostedHarnessConversation.no_workspace_objects.select_for_update().get(
                id=conversation_id
            )
        )
        _expire_chat_lease(conversation)
    outcome = payload.get("outcome")
    if payload.get("replayed"):
        _schedule_checkpoint_promotion(conversation_id, sequence)
    elif (
        outcome in ("failed", "interrupted")
        or payload.get("interrupted")
        or snapshot.get("interrupted")
    ):
        tasks.schedule_finish_turn(conversation_id, sequence)


def finish_nudge_turn(conversation_id: str, turn_sequence: int) -> None:
    """A rebuild's turn ended without success: it fails and the draft comes back."""
    turn = HostedHarnessConversationEvent.no_workspace_objects.filter(
        conversation_id=conversation_id, kind="turn_completed", sequence=turn_sequence
    ).first()
    if turn is None:
        return
    command = Message.no_workspace_objects.filter(
        id=(turn.payload or {}).get("command_message_id"), role=Message.Role.USER
    ).first()
    nudge = nudge_for_command(command)
    if nudge is None:
        return
    code = (
        "turn_interrupted"
        if (turn.payload or {}).get("outcome") == "interrupted"
        or (turn.payload or {}).get("interrupted")
        or (nudge.draft_snapshot or {}).get("interrupted")
        else "turn_failed"
    )
    with transaction.atomic():
        locked = nudges._head(nudge.job_id, lock=True)
        nudge = _lock_nudge(locked, nudge.id) if locked is not None else None
        if nudge is None:
            return
        if nudge.state in OPEN:
            close_nudge(
                nudge,
                locked=locked,
                state=State.FAILED,
                error={"code": code},
                turn_event=turn,
            )
        else:
            record_usage(nudge, event=turn, started_at=_taken_at(nudge))
            nudge.save(update_fields=["usage", "updated_at"])


def draft_command(command: Message | None) -> tuple[str, dict[str, Any]] | None:
    """How a rebuild's write lands in the drafts: ``None`` drops it (the rebuild, or
    this attempt of it, is over); ``("", {})`` for a command that is not a rebuild's."""
    nudge = nudge_for_command(command)
    if (
        nudge is None
        and command is not None
        and (command.payload or {}).get("nudge_id")
    ):
        logger.info("harness_nudge_late_write message=%s", command.id)
        return None
    if nudge is None:
        job = command.conversation.job if command is not None else None
        if (
            job is not None
            and HarnessNudge.no_workspace_objects.filter(
                job_id=job.environment_id or job.id, state=State.RUNNING
            ).exists()
        ):
            logger.warning(
                "harness_nudge_foreign_write conversation=%s message=%s",
                command.conversation_id,
                command.id,
            )
        return "", {}
    if nudge.state == State.QUEUED:
        start_nudge_turn(nudge.id, message_id=command.id, started_at=None)
        nudge.refresh_from_db()
    if nudge.state != State.RUNNING:
        logger.info("harness_nudge_late_write nudge=%s state=%s", nudge.id, nudge.state)
        return None
    return "nudge", {"pieces": {nudge.target}, "nudge": nudge}


# --- landing -------------------------------------------------------------------------


def _result_files(checkpoint_key: str | None, target: str) -> dict[str, Any]:
    """The result file (and a suite's proofs) from the turn's raw checkpoint: the
    published archive leaves hidden root files out. Locals holding them before
    redaction are named for the error tracker's scrubber."""
    from simulate.services.harness_versioning import archives
    from simulate.services.hosted_harness_conversation import _read_object

    secret_body = _read_object(checkpoint_key) if checkpoint_key else None
    if secret_body is None:
        return {}
    secret_found: dict[str, Any] = {}
    wanted = {NUDGE_RESULT: RESULT_FILE_MAX}
    if target == Target.SUITE:
        wanted[PROOFS] = PROOFS_FILE_MAX
    for path, limit in wanted.items():
        try:
            secret_data = archives.read_members(secret_body, {path}, limit).get(path)
            secret_found[path] = (
                json.loads(secret_data) if secret_data is not None else None
            )
        except (
            HostedHarnessError,
            tarfile.TarError,
            ValueError,
            OSError,
            RecursionError,
            MemoryError,
        ):
            secret_found[path] = None
    return secret_found


def _items(piece: str, diff: Mapping[str, Any] | None) -> set[str]:
    """The names a diff touches: tools, rules, use cases, contract fields, sub-goals,
    world files or scenario keys."""
    if not isinstance(diff, Mapping):
        return set()
    if piece == Target.SUITE:
        return {
            *(diff.get("added") or []),
            *(
                one.get("key")
                for one in diff.get("removed") or []
                if isinstance(one, Mapping)
            ),
            *(diff.get("changed") or {}),
        }
    contract = diff.get("contract") or {}
    names: set[str] = set(contract.get("fields") or [])
    for part in ("tools", "rules", "use_cases"):
        for change in ("added", "removed", "changed"):
            names.update((contract.get(part) or {}).get(change) or [])
    for part in ("sub_goals", "files"):
        for change in ("added", "removed", "changed"):
            names.update((diff.get(part) or {}).get(change) or [])
    return {str(one) for one in names}


def _snapshot_documents(locked: HostedHarnessJob, nudge: HarnessNudge) -> Any:
    """The piece as the turn found it, read from its content-addressed archive."""
    from simulate.services.harness_versioning import archives, drafts

    snapshot = (nudge.draft_snapshot or {}).get("piece") or {}
    target = nudge.target
    default = getattr(locked, f"{target}_version")
    if not snapshot.get("draft_version_id") or default is None:
        return default
    files = archives.read_archive(archives.get_archive(snapshot["archive_object_key"]))
    other = Target.SUITE if target == Target.ENVIRONMENT else Target.ENVIRONMENT
    sides = {
        target: drafts._side(files, target),
        other: drafts._piece_files(locked, other, getattr(locked, f"{other}_version")),
    }
    _split, prepared = drafts._prepared(
        locked, archives.compose(sides["environment"], sides["suite"])
    )
    values = drafts._content(locked, target, prepared, origin="nudge", default=default)
    return SimpleNamespace(documents=values["documents"])


def _buckets(
    nudge: HarnessNudge, before: Any, result: Any
) -> tuple[dict[str, Any], set[str], set[str]]:
    """What the update changed (snapshot -> result), what the person's draft had
    changed (default -> snapshot), and what both touched."""
    from simulate.services.harness_versioning.versions import (
        environment_change,
        suite_change,
    )

    documents = result.documents or {}
    if nudge.target == Target.ENVIRONMENT:
        update, _summary = environment_change(
            before, documents.get("contract") or {}, documents
        )
    else:
        update, _summary = suite_change(
            before, documents.get("scenarios") or [], "nudge"
        )
    edits = ((nudge.draft_snapshot or {}).get("piece") or {}).get("diff")
    return (
        {"update": update, "draft_edits": edits},
        _items(nudge.target, update),
        _items(nudge.target, edits),
    )


def _redacted(
    environment: HostedHarnessJob,
    nudge: HarnessNudge,
    secret_texts: list[str],
    cut: list[bool],
) -> list[str]:
    # Named for the error tracker's scrubber: the texts may hold a value until masked.
    # The environment's own agents too: a suite rebuild names no agent version.
    try:
        return nudges.redact_with_agent_values(
            environment,
            secret_texts,
            [
                nudge.trigger_agent_version_id,
                nudge.base_agent_version_id,
                environment.agent_version_id,
                getattr(nudge.base_environment_version, "agent_version_id", None),
                (nudge.draft_snapshot or {}).get("ran_as_agent_version_id"),
            ],
            cut=cut,
        )
    except HostedHarnessError:
        return ["" for _ in secret_texts]


def _report(
    environment: HostedHarnessJob, nudge: HarnessNudge, secret_result: Mapping
) -> dict:
    """The result file's parts the nudge keeps, redacted, then truncated. Locals that
    hold its texts before masking are named for the error tracker's scrubber."""
    secret_checks = (
        secret_result.get("checks")
        if isinstance(secret_result.get("checks"), Mapping)
        else {}
    )
    secret_problems = [
        one for one in secret_checks.get("problems") or [] if isinstance(one, Mapping)
    ][:PROBLEMS_MAX]
    secret_warnings = [str(one) for one in secret_checks.get("warnings") or []][
        :PROBLEMS_MAX
    ]
    secret_left_out = [
        one for one in secret_result.get("left_out") or [] if isinstance(one, Mapping)
    ][:PROBLEMS_MAX]
    secret_not_checked = list(secret_checks.get("not_checked") or [])[:PROBLEMS_MAX]
    # Every free text the builder wrote, as (part, field, cap), masked in one pass.
    secret_parts = [
        ("notes", None, str(secret_result.get("notes") or ""), NOTES_MAX),
        *(
            ("problems", (index, field), str(one.get(field) or ""), cap)
            for index, one in enumerate(secret_problems)
            for field, cap in (
                ("item", PROBLEM_TEXT_MAX),
                ("kind", 64),
                ("message", PROBLEM_TEXT_MAX),
            )
        ),
        *(("warnings", None, str(one), PROBLEM_TEXT_MAX) for one in secret_warnings),
        *(
            ("not_checked", None, str(one), PROBLEM_TEXT_MAX)
            for one in secret_not_checked
        ),
        *(
            ("left_out", (index, field), str(one.get(field) or ""), PROBLEM_TEXT_MAX)
            for index, one in enumerate(secret_left_out)
            for field in ("key", "why")
        ),
    ]
    # Cut to the cap plus the longest credential, so a value starting inside the cap is
    # whole and the patterns stay cheap. ALK cuts at the same bound; a cut text may end
    # inside a value, so its trailing start of one is masked too.
    secret_texts = [
        _normalize_log_text(text)[: cap + CREDENTIAL_VALUE_MAX_CHARS]
        for _part, _at, text, cap in secret_parts
    ]
    masked = _redacted(
        environment,
        nudge,
        secret_texts,
        [
            len(text) >= cap + CREDENTIAL_VALUE_MAX_CHARS
            for text, (_part, _at, _text, cap) in zip(secret_texts, secret_parts)
        ],
    )
    report: dict[str, Any] = {
        "notes": "",
        "problems": [{} for _ in secret_problems],
        "warnings": [],
        "not_checked": [],
        "left_out": [{} for _ in secret_left_out],
    }
    for (part, at, _text, cap), text in zip(secret_parts, masked, strict=True):
        text = text[:cap]
        if part == "notes":
            report["notes"] = text
        elif at is None:
            report[part].append(text)
        else:
            report[part][at[0]][at[1]] = text
    return {
        **report,
        "passed": secret_checks.get("passed") is True,
        "changed": secret_result.get("changed") is not False,
    }


def _saved_over(
    locked: HostedHarnessJob, nudge: HarnessNudge, draft: Any, snapshot: Mapping
) -> bool:
    """Whether another chat saved over the rebuild's result before it landed."""
    from simulate.services.harness_versioning import drafts

    if draft is not None:
        return draft.origin != "nudge" and draft.hash != snapshot.get("hash")
    # A save equal to the default leaves no draft, only the one it discarded.
    ids = [snapshot["draft_version_id"]] if snapshot.get("draft_version_id") else []
    discarded = (
        drafts._model(nudge.target)
        ._base_manager.filter(
            Q(nudge=nudge) | Q(id__in=ids),
            job=locked,
            status=VersionStatus.DISCARDED,
            deleted=False,
        )
        .exclude(discard_reason=DiscardReason.SET_ASIDE_BY_REBUILD)
    )
    taken = _taken_at(nudge)
    if taken is not None:
        # Only this turn's discards; a retry keeps the nudge.
        discarded = discarded.filter(updated_at__gte=taken)
    return any(
        (documents or {}).get("discarded_by") not in (None, "nudge")
        for documents in discarded.values_list("documents", flat=True)
    )


def _result_draft(locked: HostedHarnessJob, nudge: HarnessNudge) -> Any:
    from simulate.services.harness_versioning import drafts

    return drafts.fresh_draft(locked, nudge.target, lock=True)


def _unhandled_tools(
    nudge: HarnessNudge, locked: HostedHarnessJob, result: Any
) -> list[dict[str, str]]:
    """Contract tools the saved world has no handler for."""
    from simulate.services.harness_versioning import diffs, drafts

    if nudge.target != Target.ENVIRONMENT:
        return []
    tools = diffs._tools((result.documents or {}).get("contract") or {})
    if not tools:
        return []
    files = drafts._piece_files(locked, "environment", result)
    try:
        manifest = json.loads(files["manifest.json"])
    except (KeyError, ValueError):
        return []
    if not isinstance(manifest, Mapping) or not (
        manifest.get("tables") or manifest.get("tools")
    ):
        return []
    covered = {*(manifest.get("tools") or []), *(manifest.get("runtime_tools") or [])}
    return [
        {
            "item": name,
            "kind": "world",
            "message": "contract tool has no handler: the world was not saved with it",
        }
        for name in sorted(set(tools) - covered)
    ]


def _uncovered_sub_goals(nudge: HarnessNudge, result: Any) -> list[dict[str, str]]:
    """Sub-goals the environment rebuild added that no scenario names."""
    if nudge.target != Target.SUITE:
        return []
    added = _added_sub_goals(nudge)
    named = {
        goal
        for scenario in (result.documents or {}).get("scenarios") or []
        if isinstance(scenario, Mapping)
        for goal in scenario.get("sub_goals") or []
    }
    return [
        {
            "item": name,
            "kind": "sub_goal",
            "message": "added by the environment rebuild; no scenario names it",
        }
        for name in added
        if name not in named
    ]


def _added_sub_goals(nudge: HarnessNudge) -> list[str]:
    diff = (nudge.facts or {}).get("environment_diff") or {}
    return sorted((diff.get("sub_goals") or {}).get("added") or [])


def land_nudge_result(
    nudge: HarnessNudge, *, checkpoint_key: str | None, turn_event: Any
) -> HarnessNudge:
    """Land a finished rebuild turn: no change, failed or applied."""

    if nudge.state != State.RUNNING:
        return _late_usage(nudge, turn_event)
    environment = nudges._head(nudge.job_id)
    if environment is None:
        return nudge
    found = _result_files(checkpoint_key, nudge.target)
    secret_result = found.pop(NUDGE_RESULT, None)
    report = (
        _report(environment, nudge, secret_result)
        if isinstance(secret_result, Mapping)
        and str(secret_result.get("nudge_id")) == str(nudge.id)
        else None
    )
    del secret_result
    before = _snapshot_documents(environment, nudge) if report else None
    with transaction.atomic():
        locked = nudges._head(nudge.job_id, lock=True)
        nudge = _lock_nudge(locked, nudge.id) if locked is not None else None
        if nudge is None:
            return nudge
        if nudge.state != State.RUNNING:
            return _late_usage(nudge, turn_event)
        close = partial(close_nudge, nudge, locked=locked, turn_event=turn_event)
        if report is None:
            close(state=State.FAILED, error={"code": "no_report"})
            return nudge
        draft = _result_draft(locked, nudge)
        snapshot = (nudge.draft_snapshot or {}).get("piece") or {}
        if _saved_over(locked, nudge, draft, snapshot):
            close(state=State.FAILED, error={"code": "draft_changed"})
            return nudge
        result = draft or getattr(locked, f"{nudge.target}_version")
        changes, update, edits = _buckets(nudge, before, result)

        def fail(problems: list[dict[str, Any]]) -> HarnessNudge:
            close(
                state=State.FAILED,
                error={
                    "code": "checks_failed",
                    "problems": [
                        {**one, "in_your_unsaved_edits": one["item"] in edits - update}
                        for one in problems
                    ],
                },
            )
            return nudge

        if not report["passed"]:
            return fail(report["problems"])
        if problems := (
            _unhandled_tools(nudge, locked, result)
            or _uncovered_sub_goals(nudge, result)
        ):
            return fail(problems)
        if (
            nudge.target == Target.ENVIRONMENT
            and report["changed"]
            and (draft is None or draft.hash == snapshot.get("hash"))
        ):
            return fail(
                [
                    {
                        "item": "world",
                        "kind": "world",
                        "message": "the update reported changes but none were saved",
                    }
                ]
            )
        extra = {
            key: report[key]
            for key in ("left_out", "not_checked", "warnings")
            if report[key]
        }
        if draft is None or not report["changed"] or draft.hash == snapshot.get("hash"):
            if nudge.target == Target.SUITE and not snapshot.get("draft_version_id"):
                _validate(locked, nudge, locked.suite_version, found.get(PROOFS))
            _close_no_change(
                locked, nudge, turn_event=turn_event, notes=report["notes"], **extra
            )
            return nudge
        summary = _land(locked, nudge, found.get(PROOFS), extra)
        if summary is None:
            close(state=State.FAILED, error={"code": "not_runnable"})
            return nudge
        version = summary.pop("_version")
        both = sorted(update & edits)
        close(
            state=State.APPLIED,
            summary={
                **summary,
                **changes,
                "draft_edits": changes["draft_edits"]
                and {
                    **changes["draft_edits"],
                    "edited_by": _editor(snapshot.get("last_edited_by_id")),
                },
                "both": [{"kind": nudge.target, "item": one} for one in both],
                "notes": report["notes"],
                **extra,
            },
        )
        setattr(nudge, f"result_{nudge.target}_version", version)
        nudge.save(update_fields=[f"result_{nudge.target}_version", "updated_at"])
    logger.info("harness_nudge_landed nudge=%s version=%s", nudge.id, version.id)
    return nudge


def fail_landing(nudge: HarnessNudge, exc: HostedHarnessError, *, turn_event: Any):
    """The turn's save was refused: the rebuild fails and the draft comes back."""
    _close_if_open(
        nudge.job_id,
        nudge.id,
        error={"code": exc.code, "message": exc.message},
        states=(State.RUNNING,),
        turn_event=turn_event,
    )


def _editor(user_id: Any) -> str | None:
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(id=user_id).first() if user_id else None
    return (reads._person(user) or {}).get("name") or None


def _late_usage(nudge: HarnessNudge, turn_event: Any) -> HarnessNudge:
    """A late or repeated landing only fills the attempt's token counts."""
    if turn_event is not None and nudge.command_message_id:
        record_usage(nudge, event=turn_event, started_at=_taken_at(nudge))
        HarnessNudge.no_workspace_objects.filter(id=nudge.id).update(
            usage=nudge.usage, updated_at=timezone.now()
        )
    return nudge


def _validate(
    locked: HostedHarnessJob, nudge: HarnessNudge, suite: Any, proofs: Any
) -> None:
    from simulate.services.harness_versioning.versions import (
        PROOF_CLOCK_SKEW,
        _member_rows,
        _validation,
    )

    environment_version = nudge.base_environment_version or locked.environment_version
    taken = _taken_at(nudge)
    if suite is None or environment_version is None or taken is None:
        return
    paired = _member_rows(locked, (suite.documents or {}).get("scenarios") or [])
    # Only proofs made after the turn started count: no clock-skew allowance (Q1).
    _validation(
        suite, environment_version, proofs, paired, locked, taken + PROOF_CLOCK_SKEW
    )


def _land(
    locked: HostedHarnessJob, nudge: HarnessNudge, proofs: Any, extra: dict
) -> dict[str, Any] | None:
    """Make the turn's draft the default; ``None`` when the pair can't be run."""
    from simulate.services.harness_versioning import drafts

    trigger = _trigger(nudge) if nudge.target == Target.ENVIRONMENT else None
    reason = None
    if trigger is not None and locked.agent_version_id != trigger.id:
        reason = _agent_move_refusal(locked, nudge, trigger)
    agent = trigger if trigger is not None and reason is None else None
    activated = None
    for moving in (agent, None) if agent is not None else (None,):
        try:
            with transaction.atomic():
                activated = drafts.activate_drafts(
                    locked,
                    {nudge.target},
                    origin="nudge",
                    user=nudge.answered_by,
                    agent_version=moving,
                    nudge=nudge,
                    hold_owner=nudge,
                )
        except HostedHarnessError as exc:
            if exc.code != "draft_not_runnable":
                raise
            locked.refresh_from_db()
            # Not prepared for the trigger: land for the current agent instead.
            reason = "not_prepared"
            continue
        break
    if activated is None:
        return None
    version, created = activated[nudge.target]
    snapshot = (nudge.draft_snapshot or {}).get("piece") or {}
    summary: dict[str, Any] = {
        "piece": nudge.target,
        "version_id": str(version.id),
        "label": reads._label(version.version_number),
        "line": version.summary,
    }
    if created:
        # activate_drafts set origin and nudge.
        version.includes_draft_edits = bool(snapshot.get("draft_version_id"))
        version.snapshot_diff = (
            snapshot.get("diff") if version.includes_draft_edits else None
        )
        fields = ["includes_draft_edits", "snapshot_diff", "updated_at"]
        if nudge.target == Target.ENVIRONMENT and trigger is not None:
            version.agent_version_id = trigger.id
            fields.append("agent_version")
        version.save(update_fields=fields)
    else:
        summary["reused"] = {"version_id": str(version.id), "label": summary["label"]}
    if nudge.target == Target.SUITE:
        _validate(locked, nudge, version, proofs)
    if trigger is not None:
        if locked.agent_version_id == trigger.id and agent is not None:
            _record_agent_move(locked, nudge)
        summary["agent_default_moved"] = locked.agent_version_id == trigger.id
        if reason is not None and locked.agent_version_id != trigger.id:
            summary["agent_not_moved"] = reason
            logger.info(
                "harness_nudge_agent_not_moved nudge=%s reason=%s", nudge.id, reason
            )
    summary["_version"] = version
    return summary


# --- checks, the tick and deferred activations ----------------------------------------


def _command(nudge: HarnessNudge) -> Message | None:
    if not nudge.command_message_id:
        return None
    return Message.no_workspace_objects.filter(id=nudge.command_message_id).first()


def _close_if_open(
    environment_id: Any,
    nudge_id: Any,
    code: str = "",
    *,
    error: dict | None = None,
    states: tuple = OPEN,
    turn_event: Any = None,
) -> None:
    """Fail the rebuild under the locks if it is still in ``states``."""
    with transaction.atomic():
        locked = nudges._head(environment_id, lock=True)
        nudge = _lock_nudge(locked, nudge_id) if locked is not None else None
        if nudge is not None and nudge.state in states:
            close_nudge(
                nudge,
                locked=locked,
                state=State.FAILED,
                error=error or {"code": code},
                turn_event=turn_event,
            )


def _no_runtime(conversation: HostedHarnessConversation | None, now: Any) -> bool:
    """No chat sandbox is live or starting."""
    from simulate.services.hosted_harness_conversation import (
        runtime_is_live,
        runtime_start_pending,
    )

    lease = (
        HostedHarnessConversationLease.no_workspace_objects.filter(
            conversation=conversation
        ).first()
        if conversation is not None
        else None
    )
    return not runtime_is_live(lease, now=now) and not runtime_start_pending(
        lease, now=now
    )


def check_nudges(environment_id: Any) -> None:
    """Move the environment's rebuild along, or end it when it can't go on."""
    from simulate.services.hosted_harness_conversation import promote_turn_checkpoint

    nudge = _open_nudge(environment_id)
    if nudge is None:
        return
    now = timezone.now()
    command = _command(nudge)
    conversation = _conversation(environment_id)
    if nudge.state == State.QUEUED:
        if command is None:
            dispatch_nudge(environment_id)
        elif (
            command.state == Message.State.FAILED
            or (
                command.state == Message.State.QUEUED
                and now - command.created_at > NUDGE_NOT_STARTED_AFTER
            )
            or (
                # Taken, but its turn never started here and no sandbox runs it.
                command.state == Message.State.DELIVERED
                and now - command.updated_at > NUDGE_NOT_STARTED_AFTER
                and _no_runtime(conversation, now)
            )
        ):
            _close_if_open(environment_id, nudge.id, "rebuild_not_started")
        return
    snapshot = nudge.draft_snapshot or {}
    sequence = snapshot.get("turn_sequence")
    if sequence is not None and conversation is not None:
        ended = HostedHarnessConversationEvent.no_workspace_objects.filter(
            conversation=conversation, sequence=sequence
        ).first()
        waited = now - (ended.created_at if ended is not None else now)
        if waited > NUDGE_LANDING_GIVE_UP:
            _close_if_open(environment_id, nudge.id, "landing_failed")
        elif waited > NUDGE_LANDING_RETRY_AFTER and ended is not None:
            payload = ended.payload or {}
            if payload.get("outcome") == "success" or payload.get("replayed"):
                try:
                    promote_turn_checkpoint(str(conversation.id), sequence)
                except HostedHarnessError as exc:
                    logger.info(
                        "harness_nudge_landing_retry_refused nudge=%s code=%s",
                        nudge.id,
                        exc.code,
                    )
            else:
                finish_nudge_turn(str(conversation.id), sequence)
        return
    taken = _taken_at(nudge)
    if (
        taken is not None
        and now - taken > NUDGE_STUCK_AFTER
        and _no_runtime(conversation, now)
    ):
        _close_if_open(environment_id, nudge.id, "rebuild_stuck")


def _fair(rows: Any, limit: int) -> list[Any]:
    """Up to ``limit`` ids from ``(organization, id)`` rows in the order given, one
    organization at a time."""
    by_organization: dict[Any, list[Any]] = {}
    for organization_id, one in rows:
        by_organization.setdefault(organization_id, []).append(one)
    return [
        one for turn in zip_longest(*by_organization.values()) for one in turn if one
    ][:limit]


def tick_nudges(*, limit: int = 200) -> dict[str, int]:
    """Fan out: a check per environment with an open rebuild, and a retry per version
    whose activation waited for a busy environment. Least recently ticked first, so
    every organization's rows come round."""
    from simulate.models import HarnessAgentVersionRuntime
    from simulate.tasks import harness_nudges as tasks

    now = timezone.now()
    # One open rebuild per environment (uniq_harness_nudge_running).
    environments = _fair(
        HarnessNudge.no_workspace_objects.filter(state__in=OPEN)
        .order_by("updated_at")
        .values_list("organization_id", "job_id"),
        limit,
    )
    HarnessNudge.no_workspace_objects.filter(
        job_id__in=environments, state__in=OPEN
    ).update(updated_at=now)
    for environment_id in environments:
        tasks.schedule_nudge_check_task(environment_id)
    # A retry that finds the environment still busy re-records it: it waits a while.
    versions = _fair(
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            preflight_status=HarnessAgentVersionRuntime.PreflightStatus.PASSED,
            preflight_report__activation__contains=[{"outcome": "environment_busy"}],
            updated_at__lte=now - DEFERRED_RETRY_AFTER,
        )
        .order_by("updated_at")
        .values_list("organization_id", "agent_version_id"),
        DEFERRED_RETRIES,
    )
    for version_id in versions:
        tasks.schedule_deferred_activations(version_id)
    return {"environments": len(environments), "versions": len(versions)}


def _busy_entries(version_id: Any) -> list[str]:
    from simulate.services.harness_agent_versions import _side_row

    runtime = _side_row(version_id)
    if runtime is None:
        return []
    return [
        str(entry.get("environment_id"))
        for entry in (runtime.preflight_report or {}).get("activation") or []
        if isinstance(entry, Mapping) and entry.get("outcome") == "environment_busy"
    ]


def retry_deferred_activations(version_id: Any) -> dict[str, str]:
    """Move the environments a version's activation found busy, once they are not.
    A failed or canceled build is not finishing, so its entry is closed; one still
    busy is recorded again, which holds the tick's next retry back."""
    from simulate.services.harness_agent_versions import (
        _activate,
        _busy,
        _record_activation,
    )
    from simulate.services.harness_versioning.versions import UNREADY_STATES

    version = AgentVersion._base_manager.filter(id=version_id).first()
    if version is None:
        return {}
    ready = []
    waiting: dict[str, str] = {}
    for environment_id in _busy_entries(version_id):
        head = nudges._head(environment_id)
        if head is None or not _busy(head):
            ready.append(environment_id)
        else:
            waiting[environment_id] = (
                "environment_not_ready"
                if head.state in UNREADY_STATES
                else "environment_busy"
            )
    _record_activation(version_id, waiting)
    if not ready:
        return {}
    moved = {str(one) for one in _activate(version, ready)}
    return {one: "activated" if one in moved else "not_moved" for one in ready}


def schedule_deferred_activations(environment_id: Any) -> None:
    """After the caller commits, retry the activations that waited for this
    environment to finish building; never raises."""

    def start() -> None:
        from simulate.models import HarnessAgentVersionRuntime
        from simulate.tasks import harness_nudges as tasks

        try:
            for version_id in HarnessAgentVersionRuntime.no_workspace_objects.filter(
                preflight_status=HarnessAgentVersionRuntime.PreflightStatus.PASSED,
                preflight_report__activation__contains=[
                    {
                        "environment_id": str(environment_id),
                        "outcome": "environment_busy",
                    }
                ],
            ).values_list("agent_version_id", flat=True)[:DEFERRED_RETRIES]:
                tasks.schedule_deferred_activations(version_id)
        except Exception as exc:  # noqa: BLE001 - the tick retries them
            logger.warning(
                "harness_deferred_activation_schedule_failed environment=%s error=%s",
                environment_id,
                type(exc).__name__,
            )

    transaction.on_commit(start)
