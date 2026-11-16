"""Run readiness Fix autosave: once every command settled, save the builder's drafts
through Validate and Save, re-check the new defaults, and give each line its verdict.

``advance`` is a state machine over ``run.autosave["phase"]``: ``validate`` and
``validating`` while the run is ``saving``, then ``checking``. Each step re-reads the
run under ``fix_runs``' lock order and does nothing unless the run is still where the
step read it, so a repeated check changes nothing. A run that cannot save ends through
``fix_runs.end_without_save``: ``done`` while an open draft holds its changes, else
``failed``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from django.utils import timezone

from simulate.models import HostedHarnessConversationMessage, HostedHarnessJob
from simulate.models.harness_versioning import DiscardReason, VersionStatus
from simulate.models.readiness_fix import APPENDABLE, ReadinessFixRun
from simulate.services.harness_readiness import fix_driver, fix_runs
from simulate.services.harness_readiness import report as reports
from simulate.services.harness_readiness.catalogue import Pairing
from simulate.services.harness_readiness.evidence import certified, pair_bundle
from simulate.services.harness_versioning import (
    candidate_promotion,
    candidate_sets,
    candidates,
    drafts,
    nudges,
    review,
    validation,
)
from simulate.services.harness_versioning.draft_pieces import (
    DRAFT_PIECES,
    is_stale,
    open_draft,
    piece_model,
)
from simulate.services.hosted_harness import HostedHarnessError

logger = logging.getLogger(__name__)

State = ReadinessFixRun.State
SAVE_WAIT_GIVE_UP = timedelta(minutes=15)
VALIDATE_TRIES = 2
CHECK_GIVE_UP = validation.CHECK_DEADLINE
# Steps one check may take: enter saving, validate, promote, check, verdict.
_STEPS = 6
NAME_MAX = 80

# Refusals that clear by themselves: wait, then end with the code.
_WAIT = frozenset({"chat_working", "environment_busy", "environment_building"})
# Validate refusals tried again next check, up to ``VALIDATE_TRIES``.
_RETRY = frozenset({"review_stale", "draft_changed"})
_UNDECIDED = frozenset({"review_undecided", "change_depends_on_rejected"})
# Promote refusals that end the run with their own code.
_PROMOTE_ENDS = frozenset(
    {
        "not_validated",
        "draft_changed",
        "review_stale",
        "rebuild_running",
        "idempotency_key_reused",
    }
)
_MESSAGES = {
    "chat_working": "The builder chat kept working, so the fix wasn't saved; save the"
    " changes when it finishes",
    "environment_busy": "The environment was busy, so the fix wasn't saved; save the"
    " changes when it finishes",
    "environment_building": "The environment was building, so the fix wasn't saved;"
    " save the changes when it finishes",
    "rebuild_running": "An update was running, so the fix wasn't saved; save the"
    " changes when it finishes",
    "draft_stale": "The environment changed while the fix ran; discard or re-apply the"
    " changes in chat",
    "draft_changed": "The unsaved changes moved while the fix saved them; review and"
    " save them",
    "review_stale": "The changes moved while the fix saved them; review and save them",
    "review_undecided": "Some changes have review decisions; finish reviewing them,"
    " then save",
    "validation_unavailable": "The changes couldn't be validated here; review and save"
    " them",
    "validation_failed": "The fix's changes failed validation; review them in chat",
    "validation_timed_out": "Validating the changes took too long; review and save"
    " them",
    "defaults_moved": "The versions changed while the fix saved; validate and save the"
    " changes",
    "not_validated": "The changes weren't validated; validate and save them",
    "idempotency_key_reused": "The fix's save was refused; review and save the changes",
    "validation_refused": "The changes couldn't be validated; review and save them",
    "no_changes": "The builder chat made no changes to save",
    "draft_discarded": "The fix's changes were discarded before they were saved",
}
# What a line kept in an open draft says, when its ending code says more.
_LINE_MESSAGES = {"draft_stale": _MESSAGES["draft_stale"]}
_CHECK_UNAVAILABLE = "The re-check didn't finish; re-check the versions"


def _phase(run: ReadinessFixRun) -> str | None:
    return (run.autosave or {}).get("phase")


def _since(run: ReadinessFixRun, key: str = "since") -> Any:
    return fix_driver._time((run.autosave or {}).get(key))


def _name(run: ReadinessFixRun) -> str:
    """The saved version's name: stable across retries, since Save replays by it."""
    labels = dict.fromkeys(line["label"] for line in run.lines)
    return f"Fix: {', '.join(labels)}"[:NAME_MAX]


def _phase_values(phase: str, **values: Any) -> dict[str, Any]:
    """A new phase: its clock starts now and no wait carries over."""
    return {
        "phase": phase,
        "since": timezone.now().isoformat(),
        "waiting_since": None,
        **values,
    }


@contextmanager
def _same(
    run: ReadinessFixRun,
) -> Iterator[tuple[HostedHarnessJob, ReadinessFixRun] | None]:
    """The head and the run, locked, while the run is still in the state and phase
    ``run`` was read in; else ``None``."""
    with fix_driver._locked(run.job_id) as locked:
        current = locked[1] if locked is not None else None
        same = (
            current is not None
            and current.id == run.id
            and current.state == run.state
            and _phase(current) == _phase(run)
        )
        yield locked if same else None


def _update(run: ReadinessFixRun, **values: Any) -> bool:
    """Merge ``values`` into ``autosave`` while the run is where ``run`` read it.
    Whether it did."""
    with _same(run) as locked:
        if locked is None:
            return False
        _head, current = locked
        before = fix_runs.snapshot(current)
        current.autosave = {**(current.autosave or {}), **values}
        fix_runs.store(current, before)
        return True


def _end(
    run: ReadinessFixRun,
    code: str,
    message: str | None = None,
    *,
    detail: str | None = None,
) -> bool:
    logger.info("harness_fix_not_saved run=%s code=%s detail=%s", run.id, code, detail)
    fix_runs.end_without_save(
        run,
        code,
        message or _MESSAGES[code],
        detail=detail,
        line_message=_LINE_MESSAGES.get(code),
    )
    return False


def _wait(run: ReadinessFixRun, code: str) -> bool:
    """Wait for a refusal that clears by itself, from the first check that met it,
    up to ``SAVE_WAIT_GIVE_UP``; then end with its code."""
    since = _since(run, "waiting_since")
    if since is None:
        _update(run, waiting_since=timezone.now().isoformat())
    elif timezone.now() - since > SAVE_WAIT_GIVE_UP:
        _end(run, code)
    return False


# --- the state machine ----------------------------------------------------------


def advance(run: ReadinessFixRun) -> None:
    """Move a settled run on as far as it can go now: into ``saving``, through
    Validate and Save, the re-check, then the verdict. Called by the check; a step
    that must wait returns and the next check goes on."""
    for _ in range(_STEPS):
        current = fix_runs.active_run(run.job_id)
        if current is None or current.id != run.id:
            return
        step = _step(current)
        if step is None or not step(current):
            return


def _step(run: ReadinessFixRun) -> Callable[[ReadinessFixRun], bool] | None:
    if run.state in APPENDABLE:
        return _enter_saving
    if run.state == State.CHECKING:
        return _checking
    if run.state != State.SAVING:
        return None
    return {"validate": _validate, "validating": _validating}.get(_phase(run) or "")


def _enter_saving(run: ReadinessFixRun) -> bool:
    """Every command settled and no question open in the environment's chat, even
    the person's own: the run saves. A question left past ``FIX_QUESTION_GIVE_UP``
    ends the run."""
    with fix_driver._locked(run.job_id) as locked:
        if locked is None:
            return False
        _head, current = locked
        if current.id != run.id or not fix_driver._all_settled(current):
            return False
        question = fix_driver._open_question(fix_driver._lock_conversation(run.job_id))
        if question is None:
            before = fix_runs.snapshot(current)
            current.state = State.SAVING
            current.autosave = {
                **(current.autosave or {}),
                **_phase_values("validate", name=_name(current), validate_tries=0),
            }
            fix_runs.store(current, before)
            return True
        asked = (
            HostedHarnessConversationMessage.no_workspace_objects.filter(id=question)
            .values_list("created_at", flat=True)
            .first()
        )
    if asked is not None and timezone.now() - asked > fix_driver.FIX_QUESTION_GIVE_UP:
        _end(run, "question_unanswered", fix_driver._UNANSWERED)
    return False


def _validate(run: ReadinessFixRun) -> bool:
    """Validate the whole draft with the review as it stands, read here."""
    head = nudges._head(run.job_id)
    if head is None:
        return False
    opened = [
        draft
        for piece in DRAFT_PIECES
        if (draft := open_draft(head, piece)) is not None
    ]
    if not opened:
        return _take_user_save(run) or _no_draft(run)
    if any(is_stale(draft, head) for draft in opened):
        return _end(run, "draft_stale")
    if drafts.chat_busy(head):
        return _wait(run, "chat_working")
    reviewed = review.draft_review(head)
    tries = int((run.autosave or {}).get("validate_tries") or 0)
    try:
        body, _running = candidates.validate_drafts(
            head,
            expected_revisions={
                piece: one["revision"] for piece, one in reviewed["drafts"].items()
            },
            expected_review_digest=reviewed["review_digest"],
            user=run.started_by,
        )
    except HostedHarnessError as exc:
        return _validate_refused(run, exc, tries)
    if body is candidates.UNAVAILABLE:
        return _end(run, "validation_unavailable")
    return _update(
        run,
        **_phase_values(
            "validating", candidates=body["candidates"], validate_tries=tries + 1
        ),
    )


def _validate_refused(
    run: ReadinessFixRun, exc: HostedHarnessError, tries: int
) -> bool:
    if _take_user_save(run):
        return True
    code = exc.code
    if code in _WAIT:
        return _wait(run, code)
    if code == "draft_not_found":
        # Gone meanwhile: the next step reads no draft.
        return _update(run, waiting_since=None)
    if code in _RETRY:
        if tries + 1 >= VALIDATE_TRIES:
            return _end(run, code)
        _update(run, validate_tries=tries + 1, waiting_since=None)
        return False
    if code in _UNDECIDED:
        return _end(run, "review_undecided")
    if code in ("draft_stale", "rebuild_running"):
        return _end(run, code)
    return _end(run, "validation_refused", detail=code)


def _validating(run: ReadinessFixRun) -> bool:
    """Follow the live set's validation; promote it once ready."""
    head = nudges._head(run.job_id)
    if head is None:
        return False
    autosave = run.autosave or {}
    wanted = autosave.get("candidates") or {}
    entry = candidate_sets.live_set(head)
    state = candidate_sets.validation_state(head, entry) if entry else None
    if (
        state is None
        or entry is None
        or any(
            candidate_sets.version_id_of(entry, piece)
            != str(wanted.get(f"{piece}_version_id") or "")
            for piece in DRAFT_PIECES
        )
    ):
        if _take_user_save(run):
            return True
        if not fix_runs._any_open_draft(head):
            return _no_draft(run)
        return _end(run, "draft_changed")
    tries = int(autosave.get("validate_tries") or 0)
    match state["state"]:
        case "validating":
            since = _since(run)
            if since is not None and timezone.now() - since > CHECK_GIVE_UP:
                return _end(run, "validation_timed_out")
            return False
        case "retry":
            if tries < VALIDATE_TRIES:
                return _update(run, **_phase_values("validate"))
            return _end(run, "validation_failed")
        case "failed":
            message = str((state.get("error") or {}).get("message") or "")
            return _end(run, "validation_failed", message or None)
        case "check_unavailable":
            # D8: autosave never saves uncertified, as a manual Save may.
            return _end(run, "validation_unavailable")
        case "superseded":
            return _validate_again(run)
        case "ready":
            return _promote(run)
    return False


def _validate_again(run: ReadinessFixRun) -> bool:
    """The defaults moved under the candidates: validate once more, then give up."""
    if (run.autosave or {}).get("revalidated"):
        return _take_user_save(run) or _end(run, "defaults_moved")
    return _update(run, **_phase_values("validate", revalidated=True))


def _checking_values(head: HostedHarnessJob, run: ReadinessFixRun) -> None:
    """The saved defaults are the run's result; the check's clock starts now, before
    any re-check."""
    run.result_environment_version_id = head.environment_version_id
    run.result_suite_version_id = head.suite_version_id
    run.state = State.CHECKING
    run.autosave = {
        **(run.autosave or {}),
        **_phase_values("checking"),
        "agent_version_id": str(head.agent_version_id or "") or None,
    }


def _promote(run: ReadinessFixRun) -> bool:
    """Save the validated candidates as the defaults; the run records them in the
    same transaction, and the key replays a Save a lost update already made."""
    refused: HostedHarnessError | None = None
    with _same(run) as locked:
        if locked is None:
            return False
        head, current = locked
        before = fix_runs.snapshot(current)
        autosave = current.autosave or {}
        try:
            candidate_promotion.promote_candidates(
                head,
                autosave.get("candidates") or {},
                expected_review_digest=None,
                name=autosave["name"],
                idempotency_key=f"fix-{current.id}-save",
                user=current.started_by,
            )
        except HostedHarnessError as exc:
            refused = exc
        else:
            head.refresh_from_db()
            if (head.environment_version_id, head.suite_version_id) == (
                current.base_environment_version_id,
                current.base_suite_version_id,
            ):
                # The drafts were the defaults' own content: the save moved nothing.
                _end_no_changes(current, reports.resolve_pairing(head))
                fix_runs.store(current, before)
                return False
            _checking_values(head, current)
            fix_runs.store(current, before)
            logger.info(
                "harness_fix_saved run=%s environment_version=%s suite_version=%s",
                current.id,
                current.result_environment_version_id,
                current.result_suite_version_id,
            )
            return True
    return _promote_refused(run, refused)


def _promote_refused(run: ReadinessFixRun, exc: HostedHarnessError) -> bool:
    if _take_user_save(run):
        return True
    code = exc.code
    if code == "defaults_moved":
        return _validate_again(run)
    if code in _WAIT:
        return _wait(run, code)
    if code in _PROMOTE_ENDS:
        return _end(run, code)
    return _end(run, "validation_refused", detail=code)


def _take_user_save(run: ReadinessFixRun) -> bool:
    """The person saved the run's changes themselves: no open draft is left and a
    default piece moved off the run's base. The run checks what they saved."""
    with _same(run) as locked:
        if locked is None or run.state != State.SAVING:
            return False
        head, current = locked
        moved = (head.environment_version_id, head.suite_version_id) != (
            current.base_environment_version_id,
            current.base_suite_version_id,
        )
        if not moved or fix_runs._any_open_draft(head):
            return False
        before = fix_runs.snapshot(current)
        _checking_values(head, current)
        current.autosave = {**(current.autosave or {}), "saved_by": "user"}
        fix_runs.store(current, before)
        return True


def _no_draft(run: ReadinessFixRun) -> bool:
    """No draft is left and the person did not save it: a run whose turns wrote one
    lost it to a discard, unless a save set it aside with nothing to move; else
    nothing was written, and the lines get the current defaults' verdict."""
    wrote = any((command.get("outcome") or {}).get("wrote") for command in run.commands)
    if wrote and not _saved_unmoved(run):
        return _end(run, "draft_discarded")
    head = nudges._head(run.job_id)
    if head is not None:
        pairing = reports.resolve_pairing(head)
        with _same(run) as locked:
            if locked is not None:
                current = locked[1]
                before = fix_runs.snapshot(current)
                _end_no_changes(current, pairing)
                fix_runs.store(current, before)
    return False


def _saved_unmoved(run: ReadinessFixRun) -> bool:
    """The latest draft set aside since the run started was saved (it names the
    version it was saved as), though the defaults did not move: nothing changed."""
    latest = [
        piece_model(piece)
        .no_workspace_objects.filter(
            job_id=run.job_id,
            status=VersionStatus.DISCARDED,
            discard_reason=DiscardReason.USER,
            updated_at__gte=run.created_at,
        )
        .order_by("-updated_at")
        .values_list("updated_at", "documents")
        .first()
        for piece in DRAFT_PIECES
    ]
    found = [one for one in latest if one is not None]
    if not found:
        return False
    _at, documents = max(found, key=lambda one: one[0])
    return bool((documents or {}).get("saved_version_id"))


def _end_no_changes(run: ReadinessFixRun, pairing: Pairing) -> None:
    """``failed`` ``no_changes``, each line given the pairing's verdict; the caller
    holds the run and stores it."""
    _verdict(
        run,
        # Read under the lock: a failed read must not roll back a save.
        _report(pairing, safe=True),
        state=State.FAILED,
        error={"code": "no_changes", "message": _MESSAGES["no_changes"]},
    )


# --- checking and the verdict ---------------------------------------------------


def _live_check(pairing: Pairing) -> bool:
    bundle = pair_bundle(pairing)
    return bundle is not None and validation.live_validation(bundle.id) is not None


def _recheck(pairing: Pairing) -> str:
    """Re-check the saved pairing unless Validate already certified this assembly and
    no tools are left to read: ``started`` (a check runs, also one already running),
    ``skipped`` (the verdict reads now), or ``unavailable`` (refused or cannot run;
    the verdict cannot tell, as past the deadline)."""
    from ee.usage.exceptions import UsageLimitExceeded

    if _live_check(pairing):
        return "started"
    agent = pairing.agent_version
    if certified(pair_bundle(pairing)) and not (
        agent is not None
        and validation.needs_reading(
            pairing.environment, agent, pairing.environment_version, pairing.runtime
        )
    ):
        return "skipped"
    try:
        started = reports.recheck(pairing)
    except (UsageLimitExceeded, HostedHarnessError) as exc:
        logger.info(
            "harness_fix_recheck_refused environment=%s error=%s",
            pairing.environment.id,
            getattr(exc, "code", type(exc).__name__),
        )
        return "unavailable"
    if started["state"] == "validating" or _live_check(pairing):
        return "started"
    return "skipped" if started["state"] in ("ready", "failed") else "unavailable"


def saved_pairing(head: HostedHarnessJob, run: ReadinessFixRun) -> Pairing | None:
    """What the run saved: its result versions with the agent default at the save,
    whatever the defaults are now. ``None`` when it can no longer be resolved."""
    try:
        return reports.resolve_pairing(
            head,
            agent_version_id=(run.autosave or {}).get("agent_version_id"),
            environment_version_id=run.result_environment_version_id,
            suite_version_id=run.result_suite_version_id,
        )
    except HostedHarnessError:
        return None


def _checking(run: ReadinessFixRun) -> bool:
    head = nudges._head(run.job_id)
    if head is None:
        return False
    pairing = saved_pairing(head, run)
    if pairing is None:
        _finish(run, None, state=State.DONE, timed_out=True)
        return False
    recheck = (run.autosave or {}).get("recheck")
    if recheck is None:
        recheck = _recheck(pairing)
        if not _update(run, recheck=recheck):
            return False
    timed_out = recheck == "unavailable"
    if recheck == "started":
        since = _since(run)
        late = since is not None and timezone.now() - since >= CHECK_GIVE_UP
        # A job reading the agent's tools leaves a certified bundle reading ready.
        running = _live_check(pairing)
        if running and not late:
            return False
        timed_out = running
    _finish(run, pairing, state=State.DONE, timed_out=timed_out)
    return False


def end_saved(run: ReadinessFixRun) -> None:
    """End a run that saved but whose check keeps failing: ``done``, lines not
    Supported ``check_unavailable``. A saved run never ends ``failed``."""
    current = fix_runs.active_run(run.job_id)
    if current is None or current.id != run.id:
        return
    try:
        head = nudges._head(run.job_id)
        pairing = saved_pairing(head, current) if head is not None else None
    except Exception:  # noqa: BLE001 - e.g. a result version deleted for good
        logger.exception("harness_fix_saved_pairing_unreadable run=%s", run.id)
        pairing = None
    _finish(current, pairing, state=State.DONE, timed_out=True)


def _row_reason(row: Any, line: dict[str, Any]) -> dict[str, str]:
    if row is None:
        return fix_runs.reason("not_reported", line["label"])
    return fix_runs.reason(
        row["reason_code"] or row["status"], row["reason"] or row["label"]
    )


def _report(pairing: Pairing | None, *, safe: bool = False) -> Mapping[str, Any]:
    """The pairing's readiness; with ``safe``, none when it cannot be read."""
    if pairing is None:
        return {"rows": [], "computed_at": None}
    if not safe:
        return reports.readiness_report(pairing)
    try:
        return reports.readiness_report(pairing)
    except Exception:  # noqa: BLE001 - ending the run must not fail on the read
        logger.exception(
            "harness_fix_verdict_unreadable environment=%s", pairing.environment.id
        )
        return {"rows": [], "computed_at": None}


def _verdict(
    run: ReadinessFixRun,
    report: Mapping[str, Any],
    *,
    state: str,
    timed_out: bool = False,
    error: dict[str, str] | None = None,
) -> None:
    """Each line fixed when its row reads Supported, else could not fix (keeping an
    earlier failure). The run ends at the report's ``computed_at``, so the readiness
    the panel reads is never older than the end. The caller holds the run."""
    rows = {row["id"]: row for row in report["rows"]}
    for line in run.lines:
        row = rows.get(line["row_id"])
        if row is not None and row["status"] == "supported":
            line.update(state="fixed", reason=None)
            continue
        earlier = line["reason"] if line["state"] == "could_not_fix" else None
        line.update(
            state="could_not_fix",
            reason=earlier
            or (
                fix_runs.reason("check_unavailable", _CHECK_UNAVAILABLE)
                if timed_out
                else _row_reason(row, line)
            ),
        )
    run.state = state
    run.error = error
    run.finished_at = fix_driver._time(report["computed_at"]) or timezone.now()


def _finish(
    run: ReadinessFixRun,
    pairing: Pairing | None,
    *,
    state: str,
    timed_out: bool = False,
) -> None:
    """End the run on the pairing's verdict, while it is where ``run`` read it."""
    report = _report(pairing, safe=timed_out)
    with _same(run) as locked:
        if locked is None:
            return
        current = locked[1]
        before = fix_runs.snapshot(current)
        _verdict(current, report, state=state, timed_out=timed_out)
        fix_runs.store(current, before)
