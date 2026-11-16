"""Run readiness Fix runs: start or add to a run, read it, and lay its progress over
the readiness rows.

Every write follows one lock order inside ``transaction.atomic()``: the environment
head (``nudges._head(env, lock=True)``), then the run (``lock_run``), then the
conversation when needed. Each transition re-reads the run under those locks.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Iterable, Mapping
from functools import partial
from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from simulate.models import (
    HostedHarnessConversation,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
)
from simulate.models.readiness_fix import (
    ACTIVE,
    APPENDABLE,
    OPEN_LINE,
    ReadinessFixRun,
)
from simulate.services.harness_agent_versions import _busy
from simulate.services.harness_readiness import fix_groups
from simulate.services.harness_readiness.catalogue import CATALOGUE, Pairing, RowView
from simulate.services.harness_readiness.report import (
    readiness_report,
    resolve_pairing,
)
from simulate.services.harness_usage import require_harness_authoring
from simulate.services.harness_versioning import drafts, nudges, rebuild
from simulate.services.harness_versioning import read as reads
from simulate.services.harness_versioning.draft_pieces import (
    DRAFT_PIECES,
    open_draft,
    version_error,
)
from simulate.services.hosted_harness import HostedHarnessError, _pin_ids

logger = logging.getLogger(__name__)

State = ReadinessFixRun.State
Message = HostedHarnessConversationMessage
# Row statuses a builder fix can change.
_FIXABLE = frozenset({"blocked", "stale", "untested"})
_ROW_IDS = frozenset(one.id for one in CATALOGUE)


# --- reading runs ---------------------------------------------------------------


def _runs(environment_id: Any) -> QuerySet[ReadinessFixRun]:
    return ReadinessFixRun.no_workspace_objects.filter(job_id=environment_id)


def latest(environment_id: Any) -> ReadinessFixRun | None:
    """The environment's newest run, any state."""
    return (
        _runs(environment_id)
        .select_related(
            "base_environment_version",
            "base_suite_version",
            "result_environment_version",
            "result_suite_version",
        )
        .order_by("-created_at")
        .first()
    )


def active_run(environment_id: Any, *, lock: bool = False) -> ReadinessFixRun | None:
    """The environment's one run in ``ACTIVE``; lock it only under the head lock."""
    runs = _runs(environment_id).filter(state__in=ACTIVE)
    return (runs.select_for_update() if lock else runs).first()


def lock_run(run_id: Any) -> ReadinessFixRun:
    """The run, locked; call after the head lock."""
    return ReadinessFixRun.no_workspace_objects.select_for_update().get(id=run_id)


def touch(run: ReadinessFixRun, fields: Iterable[str]) -> None:
    """Save ``fields`` and move ``updated_at``; only for a change the FE can see."""
    run.state_changed_at = timezone.now()
    run.save(update_fields=[*fields, "state_changed_at", "updated_at"])


def _replayed(environment_id: Any, key: str) -> ReadinessFixRun | None:
    return _runs(environment_id).filter(commands__contains=[{"key": key}]).first()


def _open_lines(run: ReadinessFixRun | None) -> dict[str, Mapping[str, Any]]:
    """Each row's open line, by row id."""
    if run is None:
        return {}
    return {line["row_id"]: line for line in run.lines if line["state"] in OPEN_LINE}


def _any_open_draft(head: HostedHarnessJob, *, lock: bool = False) -> bool:
    """Stale drafts too, as the FE's ``drafts.any``."""
    return any(open_draft(head, piece, lock=lock) is not None for piece in DRAFT_PIECES)


def schedule_check(environment_id: Any) -> None:
    """Check the environment's fix after the caller commits; never raises."""
    transaction.on_commit(partial(_start_check, str(environment_id)))


def _start_check(environment_id: str) -> None:
    from simulate.tasks import harness_nudges as tasks

    try:
        tasks.schedule_fix_check(environment_id)
    except Exception as exc:  # noqa: BLE001 - the tick schedules it again
        logger.warning(
            "harness_fix_check_schedule_failed environment=%s error=%s",
            environment_id,
            type(exc).__name__,
        )


# --- guards other writers call --------------------------------------------------


def require_no_active_fix(locked: HostedHarnessJob) -> None:
    """Refuse a rebuild or a default move while a fix runs; call under the head
    lock. A run left active while Fix is switched off holds nothing."""
    if not fix_groups.enabled():
        return
    if active_run(locked.id) is not None:
        raise version_error(
            "fix_running", "A fix is running; run the update when it finishes", 409
        )


def require_no_fix_waiting(locked: HostedHarnessJob) -> None:
    """Refuse a manual Save while a fix waits on the user's answer to a question
    still open in the environment's chat."""
    if not fix_groups.enabled():
        return
    if not _runs(locked.id).filter(state=State.WAITING_ON_YOU).exists():
        return
    conversation = rebuild._conversation(locked.id)
    if conversation is not None and drafts._awaiting_answer(conversation):
        raise version_error(
            "fix_waiting",
            "The builder chat is waiting for your answer to a fix; answer it in"
            " chat, then save",
            409,
        )


# --- starting and adding to a run -----------------------------------------------


def _chat_unavailable(environment: HostedHarnessJob) -> bool:
    """The builder chat cannot take a message now (as sending one in chat would
    refuse)."""
    if not drafts.drafts_apply(environment) or _busy(environment):
        return True
    conversation = rebuild._conversation(environment.id)
    if conversation is not None and (
        conversation.state == HostedHarnessConversation.State.RETIRED
    ):
        return True
    metadata = (environment.payload or {}).get("metadata") or {}
    if not (
        metadata.get("authoring_object_key")
        or getattr(conversation, "latest_workspace_object_key", None)
    ):
        return True
    return not rebuild.sandbox_provider_configured()


def _eligible(rows: list[RowView]) -> dict[str, RowView]:
    return {
        row["id"]: row
        for row in rows
        if row.get("fix_group") == "builder"
        and row["status"] in _FIXABLE
        and row["fix"] is not None
        and row["fix"].get("message")
    }


def _pairing_ids(pairing: Pairing) -> tuple[str | None, str | None, str | None]:
    agent = pairing.agent_version
    return (
        str(agent.id) if agent is not None else None,
        str(pairing.environment_version.id),
        str(pairing.suite_version.id),
    )


def _defaults(environment: HostedHarnessJob) -> tuple[str | None, ...]:
    return tuple(str(one) if one else None for one in _pin_ids(environment))


def _chat_unavailable_error() -> HostedHarnessError:
    return version_error(
        "chat_unavailable", "The builder chat can't take a fix right now", 409
    )


def _needs_defaults() -> HostedHarnessError:
    return version_error(
        "fix_needs_defaults",
        "Fix works on the current versions; switch to them first",
        409,
    )


def start_or_append(
    environment: HostedHarnessJob,
    *,
    group: str,
    rows: list[str] | None,
    idempotency_key: str,
    picks: Mapping[str, Any],
    user: Any,
) -> ReadinessFixRun:
    """Start a run for these builder rows, or add them to the active one as one
    more command. ``rows`` ``None`` is every builder row with a fix.

    Raises:
        HostedHarnessError: 404 ``versioning_disabled`` / ``fix_disabled``; 400
            ``fix_group_unsupported`` / ``fix_rows_unknown``; 409
            ``fix_needs_defaults``, ``chat_unavailable``, ``rebuild_running``,
            ``fix_running``, ``unsaved_changes``, ``nothing_to_fix``.
    """
    reads.require_versioning()
    if not fix_groups.enabled():
        raise version_error("fix_disabled", "Run readiness fixes are not enabled", 404)
    replayed = _replayed(environment.id, idempotency_key)
    if replayed is not None:
        return replayed
    if group != "builder":
        raise version_error(
            "fix_group_unsupported", "Only the builder chat's fixes run here", 400
        )
    pairing = resolve_pairing(environment, **picks)
    if _pairing_ids(pairing) != _defaults(environment):
        raise _needs_defaults()
    if _chat_unavailable(environment):
        raise _chat_unavailable_error()
    require_harness_authoring(str(environment.organization_id))
    unknown = [one for one in rows or () if one not in _ROW_IDS]
    if unknown:
        raise version_error(
            "fix_rows_unknown", "Unknown readiness rows", 400, rows=unknown
        )
    # Outside any lock: the report may be read and cached.
    report = readiness_report(pairing)
    eligible = _eligible(report["rows"])
    requested = list(dict.fromkeys(rows)) if rows else list(eligible)
    wanted = [one for one in requested if one in eligible]

    with transaction.atomic():
        locked = nudges._head(environment.id, lock=True)
        if locked is None:
            raise version_error("job_not_found", "Environment not found", 404)
        replayed = _replayed(locked.id, idempotency_key)
        if replayed is not None:
            return replayed
        if _chat_unavailable(locked):
            raise _chat_unavailable_error()
        if _defaults(locked) != _pairing_ids(pairing):
            raise _needs_defaults()
        drafts.require_no_rebuild(locked)
        active = active_run(locked.id, lock=True)
        open_lines = _open_lines(active)
        if active is not None and (
            active.state not in APPENDABLE
            or (wanted and all(one in open_lines for one in wanted))
        ):
            raise version_error(
                "fix_running", "A fix is already running on these rows", 409
            )
        if active is None and _any_open_draft(locked):
            raise version_error(
                "unsaved_changes", "Save or discard the unsaved changes first", 409
            )
        new = [one for one in wanted if one not in open_lines]
        if not new:
            raise version_error(
                "nothing_to_fix", "Nothing here for the builder chat to fix", 409
            )
        run = active or ReadinessFixRun(
            job=locked,
            organization_id=locked.organization_id,
            workspace_id=locked.workspace_id,
            group=group,
            started_by=user,
            base_environment_version_id=locked.environment_version_id,
            base_suite_version_id=locked.suite_version_id,
        )
        _append(run, [eligible[one] for one in new], open_lines, idempotency_key)
        if active is None:
            run.save()
        else:
            touch(run, ["commands", "lines"])
        schedule_check(locked.id)
    return run


def _append(
    run: ReadinessFixRun,
    rows: list[RowView],
    open_lines: Mapping[str, Mapping[str, Any]],
    key: str,
) -> None:
    """One command for these rows, and a line per row. A gate row whose gate is
    open in an earlier command waits on it and rides on that command."""
    n = len(run.commands) + 1
    carried: list[RowView] = []
    lines: list[dict[str, Any]] = []
    for row in rows:
        gate = open_lines.get(row["blocked_by"] or "")
        fix = row["fix"]
        waits = fix is not None and fix["type"] == "gate" and gate is not None
        if not waits:
            carried.append(row)
        lines.append(
            {
                "row_id": row["id"],
                "area": row["area"],
                "label": row["label"],
                "state": "waiting_on_other" if waits else "queued",
                "reason": None,
                "waiting_on": row["blocked_by"] if waits else None,
                "command": gate["command"] if gate is not None and waits else n,
            }
        )
    command: dict[str, Any] = {
        "n": n,
        "key": key,
        "rows": [row["id"] for row in carried],
        "message": fix_groups.builder_message(carried),
        "message_id": None,
        "state": "queued",
        "dispatched_at": None,
        "started_at": None,
        "interrupted_at": None,
        "settled_at": None,
        "outcome": None,
    }
    if not carried:
        # Every row waits on an earlier command: nothing to send; kept so its key
        # replays. Dispatch, last_settled_at/waiting_since and the dispatch
        # deadline skip such row-less commands.
        command["state"] = "settled"
        command["outcome"] = {"turn": "not_needed", "code": None, "wrote": False}
    run.commands = [*run.commands, command]
    run.lines = [*run.lines, *lines]


# --- reading a run --------------------------------------------------------------


def _saved(run: ReadinessFixRun) -> bool:
    return bool(run.result_environment_version_id or run.result_suite_version_id)


def _moved(run: ReadinessFixRun) -> dict[str, dict[str, Any] | None] | None:
    """The pieces the run's save moved, as ``{id, label}``; ``None`` when none."""
    environment = (
        run.result_environment_version
        if run.result_environment_version_id
        and run.result_environment_version_id != run.base_environment_version_id
        else None
    )
    suite = (
        run.result_suite_version
        if run.result_suite_version_id
        and run.result_suite_version_id != run.base_suite_version_id
        else None
    )
    if environment is None and suite is None:
        return None
    refs = reads.version_refs(None, environment, suite)
    return {"environment": refs["environment"], "suite": refs["suite"]}


def left_draft(run: ReadinessFixRun, head: HostedHarnessJob) -> bool:
    """The run ended without saving and its changes wait in an open draft."""
    return run.state == State.DONE and not _saved(run) and _any_open_draft(head)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def serialize(run: ReadinessFixRun, head: HostedHarnessJob) -> dict[str, Any]:
    """The run as the Ready to run panel reads it."""
    base = reads.version_refs(
        None, run.base_environment_version, run.base_suite_version
    )
    queue = [
        command["message"]
        for command in run.commands
        if command["state"] == "queued" and command["message"]
    ]
    return {
        "id": str(run.id),
        "state": run.state,
        "group": run.group,
        "updated_at": run.state_changed_at.isoformat(),
        "started_at": run.created_at.isoformat(),
        "finished_at": _iso(run.finished_at),
        "rows": list(
            dict.fromkeys(
                line["row_id"] for line in run.lines if line["state"] in OPEN_LINE
            )
        ),
        "queue": [{"message": one} for one in dict.fromkeys(queue)],
        "lines": [
            {
                "row_id": line["row_id"],
                "area": line["area"],
                "state": line["state"],
                "reason": line["reason"],
                "waiting_on": line["waiting_on"],
            }
            for line in run.lines
        ],
        "saved": _moved(run),
        "draft": left_draft(run, head),
        "error": run.error,
        "pairing": {
            "environment_version": base["environment"],
            "suite_version": base["suite"],
        },
    }


# --- progress on the readiness rows ---------------------------------------------


def _last_lines(run: ReadinessFixRun) -> dict[str, Mapping[str, Any]]:
    """Each row's latest line."""
    return {line["row_id"]: line for line in run.lines}


def _progress(run: ReadinessFixRun, line: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "state": line["state"],
        "reason": line["reason"],
        "waiting_on": line["waiting_on"],
        "fix_id": str(run.id),
    }


def with_progress(pairing: Pairing, body: Mapping[str, Any]) -> Any:
    """``body`` with the environment's fix run laid over its rows: ``fix_progress``
    on the pairing the run works on or saved, ``fix_result`` on the pairing it
    saved from. Never changes ``body``, which may be the cached report."""
    if not fix_groups.enabled():
        return body
    head = pairing.environment
    run = latest(head.id)
    if run is None:
        return body
    shown = (str(pairing.environment_version.id), str(pairing.suite_version.id))
    defaults = (str(head.environment_version_id), str(head.suite_version_id))
    base = (str(run.base_environment_version_id), str(run.base_suite_version_id))
    result = (str(run.result_environment_version_id), str(run.result_suite_version_id))
    progress: dict[str, dict[str, Any]] = {}
    outcome: dict[str, dict[str, Any]] = {}
    lines = _last_lines(run)
    if run.state in ACTIVE:
        if shown == defaults:
            progress = {row: _progress(run, line) for row, line in lines.items()}
    elif run.state == State.DONE and _saved(run):
        if shown == result:
            progress = {row: _progress(run, line) for row, line in lines.items()}
        elif shown == base:
            moved = _moved(run) or {"environment": None, "suite": None}
            outcome = {
                row: {
                    "state": line["state"],
                    "environment": moved["environment"],
                    "suite": moved["suite"],
                    "fix_id": str(run.id),
                }
                for row, line in lines.items()
                if line["state"] in ("fixed", "could_not_fix")
            }
    elif run.state == State.DONE:
        if shown == defaults and left_draft(run, head):
            progress = {row: _progress(run, line) for row, line in lines.items()}
    elif shown == defaults == base:
        progress = {row: _progress(run, line) for row, line in lines.items()}
    if not progress and not outcome:
        return body
    rows = []
    for row in body["rows"]:
        copied = dict(row)
        found = progress.get(row["id"])
        # A saved fix the verdict called fixed, on a row no longer Supported.
        if found is not None and not (
            found["state"] == "fixed" and row["status"] != "supported"
        ):
            copied["fix_progress"] = found
        if row["id"] in outcome:
            copied["fix_result"] = outcome[row["id"]]
        rows.append(copied)
    return {**body, "rows": rows}


# --- commands and lines -----------------------------------------------------------

# A line its command is still working on.
WORKING_LINE = ("queued", "fixing", "waiting_on_you")
_STORED = (
    "state",
    "commands",
    "lines",
    "current_command_id",
    "question_message_id",
    "error",
    "finished_at",
    "result_environment_version_id",
    "result_suite_version_id",
    "autosave",
)
_REASONS = {
    "turn_failed": "The builder chat's turn failed",
    "turn_interrupted": "The builder chat's turn was interrupted",
    "not_started": "The builder chat didn't start on the fix",
    "stuck": "The builder chat stopped responding",
    "publish_failed": "The builder's change could not be kept",
    "publish_refused": "The builder's change could not be kept",
    "question_unanswered": "The builder chat's question went unanswered",
    "stopped": "The fix was stopped",
}


def snapshot(run: ReadinessFixRun) -> dict[str, Any]:
    """The run's stored fields, to compare against after a transition."""
    return {field: copy.deepcopy(getattr(run, field)) for field in _STORED}


def _visible(fields: Mapping[str, Any]) -> tuple[Any, ...]:
    """What the FE reads: state, error, end, the queue and each line."""
    return (
        fields["state"],
        fields["error"],
        fields["finished_at"],
        fields["result_environment_version_id"],
        fields["result_suite_version_id"],
        [
            command["message"]
            for command in fields["commands"]
            if command["state"] == "queued"
        ],
        [
            (line["state"], line["reason"], line["waiting_on"])
            for line in fields["lines"]
        ],
    )


def store(run: ReadinessFixRun, before: Mapping[str, Any]) -> bool:
    """Save what changed since ``before``; ``updated_at`` moves only for a visible
    change. Whether anything changed."""
    changed = [field for field in _STORED if getattr(run, field) != before[field]]
    if not changed:
        return False
    if _visible(snapshot(run)) != _visible(before):
        touch(run, changed)
    else:
        run.save(update_fields=[*changed, "updated_at"])
    return True


def command(run: ReadinessFixRun, n: Any) -> dict[str, Any] | None:
    return next((one for one in run.commands if one["n"] == n), None)


def in_flight(run: ReadinessFixRun) -> dict[str, Any] | None:
    """The one dispatched command not yet settled."""
    return next((one for one in run.commands if one["state"] == "dispatched"), None)


def set_lines(
    run: ReadinessFixRun,
    n: int,
    state: str,
    reason: Mapping[str, Any] | None = None,
    *,
    states: Iterable[str] = WORKING_LINE,
) -> None:
    """The lines command ``n`` carries, in ``states``, to ``state``."""
    states = tuple(states)
    for line in run.lines:
        if line["command"] == n and line["state"] in states:
            line["state"] = state
            line["reason"] = dict(reason) if reason is not None else None


def reason(code: str, message: str | None = None) -> dict[str, str]:
    return {"code": code, "message": message or _REASONS.get(code) or code}


def settle(
    run: ReadinessFixRun,
    cmd: dict[str, Any],
    *,
    turn: str,
    code: str | None = None,
    wrote: bool = False,
    message: str | None = None,
    conversation: HostedHarnessConversation | None = None,
) -> None:
    """Record the command's outcome; its lines stay ``fixing`` until the verdict, or
    become ``could_not_fix``. A failed command's answers the chat has not taken are
    failed, and its sandbox is given up so its change cannot come back from the
    working copy. Caller holds the run."""
    now = timezone.now().isoformat()
    failed = turn != "success"
    cmd.update(
        state="settled",
        settled_at=now,
        outcome={"turn": turn, "code": code, "wrote": bool(wrote)},
    )
    if failed:
        set_lines(run, cmd["n"], "could_not_fix", reason(code or turn, message))
    else:
        set_lines(run, cmd["n"], "fixing", states=("queued", "waiting_on_you"))
    run.current_command_id = None
    run.question_message_id = None
    if run.state == State.WAITING_ON_YOU:
        run.state = State.FIXING
    promote_waiting(run)
    if failed:
        _fail_untaken_replies(run, cmd, conversation)
        retire(run, cmd)


def replies(cmd: Mapping[str, Any]) -> list[Message]:
    """The person's answers to the command's questions, oldest first."""
    questions = cmd.get("questions") or []
    if not questions:
        return []
    return list(
        Message.no_workspace_objects.filter(
            role=Message.Role.USER, reply_to__in=questions
        ).order_by("sequence")
    )


def _fail_untaken_replies(
    run: ReadinessFixRun,
    cmd: Mapping[str, Any],
    conversation: HostedHarnessConversation | None,
) -> None:
    """Fail the answers the chat has not taken (past its ack watermark), so none runs
    after its command ended; a taken one belongs to the turn that took it."""
    conversation = conversation or rebuild._conversation(run.job_id)
    acked = conversation.command_acked_through if conversation is not None else 0
    for reply in replies(cmd):
        if reply.sequence > acked:
            rebuild.fail_command(reply.id)


# Control messages stop or cancel a turn; they are not a turn of the person's.
_CONTROL_KINDS = ("interrupt", "cancel_operation")


def _holds_chat(cmd: Mapping[str, Any]) -> bool:
    """No later message of the person's was taken by the chat since the command and
    its answers: its sandbox still works for the fix."""
    own = Message.no_workspace_objects.filter(
        id__in=[cmd["message_id"], *(one.id for one in replies(cmd))]
    ).order_by("-sequence")
    last = own.first()
    if last is None:
        return False
    return (
        not Message.no_workspace_objects.filter(
            conversation_id=last.conversation_id,
            role=Message.Role.USER,
            sequence__gt=last.sequence,
            state__in=(Message.State.DELIVERED, Message.State.COMPLETED),
        )
        .exclude(payload__command_kind__in=_CONTROL_KINDS)
        .exists()
    )


def retire(run: ReadinessFixRun, cmd: dict[str, Any]) -> None:
    """Once per command whose change did not publish: stop its sandbox after commit,
    so the change cannot come back from the warm working copy. Skipped once the
    person's own turn took the chat, so it is not cut short.

    TODO: a turn of the person's that started on that working copy may still carry
    the fix's unpublished edits into its own save.
    """
    if cmd.get("retired") or not cmd.get("message_id"):
        return
    cmd["retired"] = True
    if _holds_chat(cmd):
        transaction.on_commit(
            partial(rebuild._after_close, str(run.job_id), dispatched=True)
        )


def promote_waiting(run: ReadinessFixRun) -> None:
    """Lines waiting on a gate whose command settled: ``fixing`` after a success,
    else ``could_not_fix``."""
    labels = {line["row_id"]: line["label"] for line in run.lines}
    for line in run.lines:
        if line["state"] != "waiting_on_other":
            continue
        gate = command(run, line["command"])
        if gate is None or gate["state"] != "settled":
            continue
        if (gate["outcome"] or {}).get("turn") == "success":
            line.update(state="fixing", reason=None)
        else:
            label = labels.get(line["waiting_on"] or "") or line["waiting_on"]
            line.update(
                state="could_not_fix",
                reason=reason("waiting_on_failed", f"{label} couldn't be fixed"),
            )


def _stop_in_flight(run: ReadinessFixRun, code: str, message: str) -> None:
    """Fail the message in flight so no sandbox runs it, and settle its command."""
    cmd = in_flight(run)
    if cmd is None:
        return
    fail_waiting(run, cmd)
    settle(run, cmd, turn="stopped", code=code, message=message)


def fail_waiting(run: ReadinessFixRun, cmd: Mapping[str, Any]) -> None:
    """Fail the command and its answers the chat has not taken, so no sandbox runs
    them; an answer it took is never failed."""
    rebuild.fail_command(cmd["message_id"])
    _fail_untaken_replies(run, cmd, None)


def close_active(
    locked: HostedHarnessJob, *, state: str, code: str, message: str
) -> None:
    """End the environment's active run (``stopped`` on deletion); call under the
    head lock."""
    run = active_run(locked.id, lock=True)
    if run is None:
        return
    before = snapshot(run)
    _stop_in_flight(run, "stopped", message)
    closed = reason("stopped", message)
    for line in run.lines:
        if line["state"] in OPEN_LINE:
            line.update(state="could_not_fix", reason=closed)
    run.state = state
    run.error = {"code": code, "message": message}
    run.finished_at = timezone.now()
    store(run, before)


def end_without_save(
    run: ReadinessFixRun,
    code: str,
    message: str,
    *,
    detail: str | None = None,
    line_message: str | None = None,
) -> None:
    """End the run unsaved: ``done`` while an open draft holds its changes, with the
    lines it worked on waiting on the person's save (``line_message`` saying what to
    do with it); else ``failed``. ``detail`` is the refusal's own code when ``code``
    generalizes it."""
    with transaction.atomic():
        head = nudges._head(run.job_id, lock=True)
        if head is None:
            return
        run = lock_run(run.id)
        if run.state not in ACTIVE:
            return
        before = snapshot(run)
        _stop_in_flight(run, code, message)
        unsaved = _any_open_draft(head)
        ended = reason(code, message)
        for line in run.lines:
            if line["state"] == "could_not_fix":
                continue
            carried = command(run, line["command"]) or {}
            if unsaved and (carried.get("outcome") or {}).get("turn") == "success":
                line.update(
                    state="waiting_on_you",
                    reason=reason(
                        "draft_unsaved",
                        line_message
                        or "The change is in unsaved edits; review and save them",
                    ),
                )
            else:
                line.update(state="could_not_fix", reason=ended)
        run.state = State.DONE if unsaved else State.FAILED
        run.error = {
            "code": code,
            "message": message,
            **({"code_detail": detail} if detail else {}),
        }
        run.finished_at = timezone.now()
        store(run, before)
