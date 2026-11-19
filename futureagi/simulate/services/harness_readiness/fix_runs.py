"""Run readiness Fix runs: start or add to a run, read it, and lay its progress over
the readiness rows. Writes lock the environment head, then the run (``lock_run``),
then the conversation, and re-read the run under those locks."""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Iterable, Mapping
from functools import partial
from typing import Any, NotRequired, TypedDict, cast
from uuid import UUID

import structlog
from django.db import transaction
from django.db.models import Q, QuerySet
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

logger = structlog.get_logger(__name__)

State = ReadinessFixRun.State
Message = HostedHarnessConversationMessage
# Row statuses a builder fix can change.
_FIXABLE = frozenset({"blocked", "stale", "untested"})
_ROW_IDS = frozenset(one.id for one in CATALOGUE)
# Runs whose autosave may still save the drafts.
UNSAVED = (*APPENDABLE, State.SAVING)
_MAX_TRIES = 3  # How often one run may try a row; bounds its lines and commands.


class FixOutcome(TypedDict):
    turn: str
    code: str | None
    wrote: bool


class FixCommand(TypedDict):
    """One message to the builder chat for some rows, as stored in ``commands``."""

    n: int
    key: str
    request: dict[str, Any]
    rows: list[str]
    message: str
    message_id: str | None
    state: str  # queued, dispatched or settled
    dispatched_at: str | None
    started_at: str | None
    interrupted_at: str | None
    settled_at: str | None
    outcome: FixOutcome | None
    # Set later by the driver and the check.
    questions: NotRequired[list[str]]
    retired: NotRequired[bool]
    wrote: NotRequired[bool]
    landing_retried: NotRequired[int]
    busy_since: NotRequired[str]


class FixLine(TypedDict):
    """One row's try in a run, as stored in ``lines``."""

    row_id: str
    area: str | None
    label: str
    state: str
    reason: dict[str, str] | None
    waiting_on: str | None
    command: int


class FixAutosave(TypedDict, total=False):
    """The save's progress, as stored in ``autosave``."""

    phase: str
    since: str
    waiting_since: str | None
    name: str
    validate_tries: int
    candidates: dict[str, Any]
    revalidated: bool
    recheck: str
    agent_version_id: str | None
    checked: bool
    saved_by: str
    failing_since: str


def autosave(run: ReadinessFixRun) -> FixAutosave:
    return cast(FixAutosave, run.autosave or {})


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


def _request(
    group: str, rows: list[str] | None, picks: Mapping[str, Any]
) -> dict[str, Any]:
    """What a POST asked for, as its key replays it: rows in any order, picks as
    sent."""
    return {
        "group": group,
        "rows": sorted(dict.fromkeys(rows)) if rows else None,
        "picks": {
            name: str(value)
            for name, value in sorted(picks.items())
            if value is not None
        },
    }


def _replayed(
    environment_id: Any, key: str, request: Mapping[str, Any]
) -> ReadinessFixRun | None:
    """The run that took ``key``; 409 when the key came with another request."""
    run = _runs(environment_id).filter(commands__contains=[{"key": key}]).first()
    if run is None:
        return None
    earlier = next(one for one in run.commands if one["key"] == key)
    if earlier.get("request", request) != request:
        raise version_error(
            "idempotency_key_reused",
            "This request key was already used for another fix; fix again",
            409,
        )
    return run


def _open_lines(run: ReadinessFixRun | None) -> dict[str, FixLine]:
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
            "harness_fix_check_schedule_failed",
            environment=environment_id,
            error=type(exc).__name__,
        )


# --- guards other writers call --------------------------------------------------


def require_no_active_fix(
    locked: HostedHarnessJob,
    *,
    states: Iterable[str] = ACTIVE,
    message: str = "A fix is running; run the update when it finishes",
) -> None:
    """Refuse a rebuild or a default move while a fix runs; call under the head
    lock. Nothing is held while Fix is switched off."""
    if not fix_groups.enabled():
        return
    if _runs(locked.id).filter(state__in=states).exists():
        raise version_error("fix_running", message, 409)


def require_no_fix_waiting(locked: HostedHarnessJob) -> None:
    """Refuse a manual Save while a fix waits on the user's answer in chat."""
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
    """The builder chat cannot take a message now, as sending one would refuse."""
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


def _hold(
    head: HostedHarnessJob,
    pairing_ids: tuple[str | None, ...],
    active: ReadinessFixRun | None,
) -> HostedHarnessError | None:
    """Why a POST on these versions would be refused now, whatever its rows; shared
    by ``start_or_append`` and each row's ``fix_action``."""
    if _defaults(head) != pairing_ids:
        return _needs_defaults()
    if _chat_unavailable(head):
        return _chat_unavailable_error()
    try:
        drafts.require_no_rebuild(head)
    except HostedHarnessError as error:
        return error
    if active is not None and active.state not in APPENDABLE:
        return _fix_running()
    if active is None and _any_open_draft(head):
        return version_error(
            "unsaved_changes", "Save or discard the unsaved changes first", 409
        )
    return None


def _fix_running() -> HostedHarnessError:
    return version_error("fix_running", "A fix is already running on these rows", 409)


def start_or_append(
    environment: HostedHarnessJob,
    *,
    group: str,
    rows: list[str] | None,
    idempotency_key: str,
    picks: Mapping[str, Any],
    user: Any,
) -> ReadinessFixRun:
    """Start a run for these builder rows (``None``: all), or add them to the active
    one as a command; open, non-builder or ``_MAX_TRIES``-tried rows are skipped.
    Raises ``HostedHarnessError`` (404, 400 or 409) with the refusal's code."""
    reads.require_versioning()
    if not fix_groups.enabled():
        raise version_error("fix_disabled", "Run readiness fixes are not enabled", 404)
    request = _request(group, rows, picks)
    replayed = _replayed(environment.id, idempotency_key, request)
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
        replayed = _replayed(locked.id, idempotency_key, request)
        if replayed is not None:
            return replayed
        active = active_run(locked.id, lock=True)
        if (error := _hold(locked, _pairing_ids(pairing), active)) is not None:
            raise error
        open_lines = _open_lines(active)
        if active is not None and wanted and all(one in open_lines for one in wanted):
            raise _fix_running()
        new = [one for one in wanted if one not in open_lines]
        if not new:
            raise version_error(
                "nothing_to_fix", "Nothing here for the builder chat to fix", 409
            )
        tried = Counter(line["row_id"] for line in (active.lines if active else ()))
        if all(tried[one] >= _MAX_TRIES for one in new):
            raise version_error(
                "fix_tries_used",
                "These rows were tried enough in this fix; fix them again once it ends",
                409,
                rows=new,
            )
        new = [one for one in new if tried[one] < _MAX_TRIES]
        run = active or ReadinessFixRun(
            job=locked,
            organization_id=locked.organization_id,
            workspace_id=locked.workspace_id,
            group=group,
            started_by=user,
            base_environment_version_id=locked.environment_version_id,
            base_suite_version_id=locked.suite_version_id,
        )
        _append(
            run, [eligible[one] for one in new], open_lines, idempotency_key, request
        )
        if active is None:
            run.save()
        else:
            touch(run, ["commands", "lines"])
        schedule_check(locked.id)
    return run


def _append(
    run: ReadinessFixRun,
    rows: list[RowView],
    open_lines: Mapping[str, FixLine],
    key: str,
    request: Mapping[str, Any],
) -> None:
    """One command for these rows, and a line per row. A gate row whose gate is
    open in an earlier command waits on it and rides on that command."""
    n = len(run.commands) + 1
    carried: list[RowView] = []
    lines: list[FixLine] = []
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
    command: FixCommand = {
        "n": n,
        "key": key,
        "request": dict(request),
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
        # Every row rides an earlier command: nothing to send, kept so its key
        # replays. Dispatch, its deadline and last_settled_at skip it.
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


def _draft_is_the_fixs(run: ReadinessFixRun, head: HostedHarnessJob) -> bool:
    """An open draft nobody edited after the run ended (a later edit is not the
    fix's)."""
    opened = [
        found
        for piece in DRAFT_PIECES
        if (found := open_draft(head, piece)) is not None
    ]
    return (
        bool(opened)
        and run.finished_at is not None
        and all(one.updated_at <= run.finished_at for one in opened)
    )


_OUTCOMES = {
    "fixed": "Fixed",
    "saved_unchecked": "Saved; re-check didn't finish",
    "draft_gone": "Changes were saved or discarded",
}


def _by_count(fixed: int, total: int) -> str:
    if fixed == 0:
        return "could_not_fix"
    return "fixed" if fixed == total else "partly_fixed"


def outcome(run: ReadinessFixRun, fix_draft: bool) -> dict[str, Any] | None:
    """How an ended run came out, over each row's latest line; ``None`` while it
    runs. ``fix_draft``: the fix's changes still wait in an open draft."""
    if run.state in ACTIVE:
        return None
    lines = list(_last_lines(run).values())
    fixed = sum(1 for line in lines if line["state"] == "fixed")
    total = len(lines)
    error = (run.error or {}).get("message") or ""
    failed = next(
        (
            line["reason"].get("message") or ""
            for line in lines
            if line["state"] == "could_not_fix" and line["reason"]
        ),
        "",
    )
    if _saved(run):
        kind = (
            _by_count(fixed, total)
            if autosave(run).get("checked")
            else "saved_unchecked"
        )
        message = {
            "could_not_fix": failed or error,
            "partly_fixed": f"Fixed {fixed} of {total}",
        }.get(kind) or _OUTCOMES.get(kind, "")
    elif run.state == State.DONE:
        kind = "unsaved" if fix_draft else "draft_gone"
        message = error if fix_draft else _OUTCOMES[kind]
    else:
        # Failed or stopped: the lines a verdict read Supported still count.
        kind = _by_count(fixed, total)
        message = error or failed
    return {"kind": kind, "fixed": fixed, "total": total, "message": message}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def serialize(run: ReadinessFixRun, head: HostedHarnessJob) -> dict[str, Any]:
    """The run as the Ready to run panel reads it."""
    draft = left_draft(run, head)
    base = reads.version_refs(
        None, run.base_environment_version, run.base_suite_version
    )
    labels = {line["row_id"]: line["label"] for line in run.lines}
    # A short label per queued command; the instruction itself is in the chat.
    queue = [
        ", ".join(labels[row] for row in command["rows"])
        for command in run.commands
        if command["state"] == "queued" and command["rows"]
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
            for line in _last_lines(run).values()
        ],
        "saved": _moved(run),
        "draft": draft,
        "active": run.state in ACTIVE,
        "accepts_rows": run.state in APPENDABLE,
        "outcome": outcome(run, draft and _draft_is_the_fixs(run, head)),
        "error": run.error,
        "pairing": {
            "environment_version": base["environment"],
            "suite_version": base["suite"],
        },
    }


# --- progress on the readiness rows ---------------------------------------------


def _last_lines(run: ReadinessFixRun) -> dict[str, FixLine]:
    """Each row's latest line."""
    return {line["row_id"]: line for line in run.lines}


def _progress(run: ReadinessFixRun, line: FixLine) -> dict[str, Any]:
    return {
        "state": line["state"],
        "reason": line["reason"],
        "waiting_on": line["waiting_on"],
        "fix_id": str(run.id),
    }


def _progress_rows(
    run: ReadinessFixRun,
    head: HostedHarnessJob,
    shown: tuple[str, str],
) -> dict[str, dict[str, Any]]:
    """The run's ``fix_progress`` by row, when ``shown`` is the pairing it works on
    or saved."""
    defaults = (str(head.environment_version_id), str(head.suite_version_id))
    base = (str(run.base_environment_version_id), str(run.base_suite_version_id))
    result = (str(run.result_environment_version_id), str(run.result_suite_version_id))
    if run.state in ACTIVE:
        on = shown == defaults
    elif run.state == State.DONE and _saved(run):
        on = shown == result
    elif run.state == State.DONE:
        on = shown == defaults and left_draft(run, head)
    else:
        on = shown == defaults == base
    if not on:
        return {}
    return {row: _progress(run, line) for row, line in _last_lines(run).items()}


def _saved_from(environment_id: Any, shown: tuple[str, str]) -> ReadinessFixRun | None:
    """The newest run saved from ``shown`` that moved a piece, whatever ran since."""
    found = (
        _runs(environment_id)
        .filter(
            state=State.DONE,
            base_environment_version_id=UUID(shown[0]),
            base_suite_version_id=UUID(shown[1]),
        )
        .filter(
            Q(result_environment_version__isnull=False)
            | Q(result_suite_version__isnull=False)
        )
        .select_related("result_environment_version", "result_suite_version")
        .order_by("-created_at")
        .first()
    )
    # Nothing moved: the run shows its progress here instead.
    return found if found is not None and _moved(found) is not None else None


def _results(run: ReadinessFixRun | None) -> dict[str, dict[str, Any]]:
    """What a saved run did for each row, on the pairing it saved from."""
    if run is None:
        return {}
    moved = _moved(run) or {"environment": None, "suite": None}
    checked = bool(autosave(run).get("checked"))
    return {
        row: {
            "state": line["state"],
            "environment": moved["environment"],
            "suite": moved["suite"],
            "checked": checked,
            "fix_id": str(run.id),
        }
        for row, line in _last_lines(run).items()
        if line["state"] in ("fixed", "could_not_fix")
    }


def _actions(
    pairing: Pairing, rows: list[RowView], active: ReadinessFixRun | None
) -> dict[str, dict[str, Any]]:
    """Each row's ``fix_action``: whether a POST would take it and, if so, the code
    refusing a POST now (``held``)."""
    open_lines = _open_lines(active)
    tried = Counter(line["row_id"] for line in (active.lines if active else ()))
    available = {
        one
        for one in _eligible(rows)
        if one not in open_lines and tried[one] < _MAX_TRIES
    }
    held = None
    if available:
        try:
            error = _hold(pairing.environment, _pairing_ids(pairing), active)
        except Exception:  # noqa: BLE001 - a readiness read never fails on it
            logger.exception(
                "harness_fix_hold_unreadable", environment=str(pairing.environment.id)
            )
            error = None
        held = error.code if error is not None else None
    return {
        row["id"]: {
            "available": row["id"] in available,
            "held": held if row["id"] in available else None,
        }
        for row in rows
    }


def with_progress(pairing: Pairing, body: Mapping[str, Any]) -> Any:
    """A copy of ``body`` (maybe the cached report) with ``fix_progress``,
    ``fix_result`` and ``fix_action`` laid over its rows."""
    if not fix_groups.enabled():
        return body
    head = pairing.environment
    run = latest(head.id)
    shown = (str(pairing.environment_version.id), str(pairing.suite_version.id))
    progress = _progress_rows(run, head, shown) if run is not None else {}
    results = _results(_saved_from(head.id, shown))
    # The one active run is always the newest.
    active = run if run is not None and run.state in ACTIVE else None
    actions = _actions(pairing, body["rows"], active)
    rows = []
    for row in body["rows"]:
        copied = dict(row)
        found = progress.get(row["id"])
        # A saved fix the verdict called fixed, on a row no longer Supported.
        if found is not None and not (
            found["state"] == "fixed" and row["status"] != "supported"
        ):
            copied["fix_progress"] = found
        if row["id"] in results:
            copied["fix_result"] = results[row["id"]]
        copied["fix_action"] = actions[row["id"]]
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
    "turn_interrupted": "The builder chat was interrupted",
    "not_started": "The builder chat didn't start",
    "stuck": "The builder chat stopped responding",
    "publish_failed": "The change couldn't be kept",
    "publish_refused": "The change couldn't be kept",
    "question_unanswered": "Question went unanswered",
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
    """Save what changed since ``before`` (``updated_at`` moves only for a visible
    change); whether anything did."""
    changed = [field for field in _STORED if getattr(run, field) != before[field]]
    if not changed:
        return False
    if _visible(snapshot(run)) != _visible(before):
        touch(run, changed)
    else:
        run.save(update_fields=[*changed, "updated_at"])
    return True


def command(run: ReadinessFixRun, n: Any) -> FixCommand | None:
    return next((one for one in run.commands if one["n"] == n), None)


def in_flight(run: ReadinessFixRun) -> FixCommand | None:
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
    cmd: FixCommand,
    *,
    turn: str,
    code: str | None = None,
    wrote: bool = False,
    message: str | None = None,
    conversation: HostedHarnessConversation | None = None,
) -> None:
    """Record the command's outcome; its lines stay ``fixing`` for the verdict or
    become ``could_not_fix``, and a failed one's untaken answers and sandbox are
    given up. Caller holds the run."""
    failed = turn != "success"
    cmd["state"] = "settled"
    cmd["settled_at"] = timezone.now().isoformat()
    cmd["outcome"] = {"turn": turn, "code": code, "wrote": bool(wrote)}
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


def replies(cmd: FixCommand) -> list[Message]:
    """The person's answers to the command's questions, oldest first; a failed one
    never runs."""
    questions = cmd.get("questions") or []
    if not questions:
        return []
    return list(
        Message.no_workspace_objects.filter(
            role=Message.Role.USER, reply_to__in=questions
        )
        .exclude(state=Message.State.FAILED)
        .order_by("sequence")
    )


def live_replies(cmd: FixCommand) -> list[Message]:
    """The command's answers a sandbox may still run."""
    return [
        reply
        for reply in replies(cmd)
        if reply.state in (Message.State.QUEUED, Message.State.DELIVERED)
    ]


def _fail_untaken_replies(
    run: ReadinessFixRun,
    cmd: FixCommand,
    conversation: HostedHarnessConversation | None,
) -> None:
    """Fail the answers past the chat's ack watermark, so none runs after its command
    ended; a taken one belongs to the turn that took it."""
    conversation = conversation or rebuild._conversation(run.job_id)
    acked = conversation.command_acked_through if conversation is not None else 0
    for reply in replies(cmd):
        if reply.sequence > acked:
            rebuild.fail_command(reply.id, reason="fix_ended")


# Control messages stop or cancel a turn; they are not a turn of the person's.
_CONTROL_KINDS = ("interrupt", "cancel_operation")


def _holds_chat(cmd: FixCommand) -> bool:
    """The chat took no later message of the person's: its sandbox still works for
    the fix."""
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


def retire(run: ReadinessFixRun, cmd: FixCommand) -> None:
    """Once per command whose change did not publish: stop its sandbox after commit,
    so the change can't come back from the warm working copy."""
    if cmd.get("retired") or not cmd.get("message_id"):
        return
    cmd["retired"] = True
    # TODO: when the person's own turn already took the chat it is left running, and
    # it may carry the fix's unpublished edits into the person's save.
    if _holds_chat(cmd):
        transaction.on_commit(
            partial(rebuild._after_close, str(run.job_id), dispatched=True)
        )


def _succeeded(cmd: FixCommand | None) -> bool:
    outcome = cmd["outcome"] if cmd is not None else None
    return outcome is not None and outcome["turn"] == "success"


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
        if _succeeded(gate):
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


def fail_waiting(run: ReadinessFixRun, cmd: FixCommand) -> None:
    """Fail the command and its untaken answers, so no sandbox runs them."""
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
    """End the run unsaved: ``done`` with its lines waiting on the person's save
    (``line_message``) while an open draft holds its changes, else ``failed``.
    ``detail``: the refusal's own code when ``code`` generalizes it."""
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
            if unsaved and _succeeded(command(run, line["command"])):
                line.update(
                    state="waiting_on_you",
                    reason=reason(
                        "draft_unsaved",
                        line_message or "Unsaved changes; review and save them",
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
