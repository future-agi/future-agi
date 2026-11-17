"""Maintainer review (SPEC §5.2-§5.4): the soft lock, the decision rules and re-checks.

`decide` is the only caller of `publish`, and the only code that moves a submission to `approved`.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from omega_hub import rules
from omega_hub.errors import HubError
from omega_hub.models import CheckJob, Submission
from omega_hub.services import audit, disclosure, notify
from omega_hub.services.checks import latest_job
from omega_hub.services.publish import flag_ids, publish
from omega_hub.services.states import _APPROVE_TOKEN, move

LOCK = timedelta(hours=2)
REASON_LENGTH = (20, 2000)
MAX_NOTES = 30
NOTE_LENGTH = 500


def _locked_by_other(submission: Submission, user) -> bool:
    return (
        submission.reviewer_id is not None
        and submission.reviewer_id != user.pk
        and submission.review_started_at is not None
        and submission.review_started_at > timezone.now() - LOCK
    )


@transaction.atomic
def start_review(user, submission: Submission, *, take_over: bool = False) -> Submission:
    submission = Submission.objects.select_for_update().get(pk=submission.pk)
    if submission.status not in (Submission.Status.IN_REVIEW, Submission.Status.CHECK_ERROR):
        raise HubError("OMEGA_HUB_BAD_STATE", 409)
    if _locked_by_other(submission, user) and not take_over:
        raise HubError("OMEGA_HUB_REVIEW_LOCKED", 409, extra={"reviewer": str(submission.reviewer_id)})
    previous = submission.reviewer_id
    submission.reviewer = user
    submission.review_started_at = timezone.now()
    submission.save(update_fields=["reviewer", "review_started_at", "updated_at"])
    action = "review.taken_over" if take_over and previous and previous != user.pk else "review.started"
    audit.record(action, target_type="submission", target_id=submission.pk, actor=user,
                 publisher=submission.publisher, data={"previousReviewer": str(previous) if previous else None})
    return submission


def _reason(value) -> str:
    if not isinstance(value, str) or not REASON_LENGTH[0] <= len(value.strip()) <= REASON_LENGTH[1]:
        raise HubError("OMEGA_HUB_REASON_LENGTH", 400)
    return value.strip()


def _notes(value) -> list[dict]:
    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) > MAX_NOTES:
        raise HubError("OMEGA_HUB_NOTES_INVALID", 400)
    clean = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"file", "line", "note"}:
            raise HubError("OMEGA_HUB_NOTES_INVALID", 400)
        file, line, note = item.get("file"), item.get("line"), item.get("note")
        if not isinstance(file, str) or not 1 <= len(file) <= 300 or not isinstance(note, str) \
                or not 1 <= len(note.strip()) <= NOTE_LENGTH or (line is not None and (not isinstance(line, int) or line < 1)):
            raise HubError("OMEGA_HUB_NOTES_INVALID", 400)
        clean.append({"file": file, "line": line, "note": note.strip()})
    return clean


def _checklist(value, version: int) -> dict:
    keys = set(rules.checklist_keys(version))
    if not isinstance(value, dict):
        raise HubError("OMEGA_HUB_CHECKLIST_INCOMPLETE", 400, "the checklist is required to approve")
    if set(value) != keys:
        raise HubError("OMEGA_HUB_RULES_CHANGED", 409, "the checklist keys are not the current rules' keys")
    if not all(value[key] is True for key in keys):
        raise HubError("OMEGA_HUB_CHECKLIST_INCOMPLETE", 400)
    return {key: True for key in sorted(keys)}


def _rules_version(value) -> int:
    current = rules.current_version()
    if value != current:
        raise HubError("OMEGA_HUB_RULES_CHANGED", 409, extra={"rulesVersion": current})
    return current


@transaction.atomic
def decide(user, submission: Submission, payload: dict, *, via: str | None = None) -> Submission:
    submission = Submission.objects.select_for_update(of=("self",)).select_related("publisher", "submitted_by").get(pk=submission.pk)
    if submission.status != Submission.Status.IN_REVIEW:
        raise HubError("OMEGA_HUB_BAD_STATE", 409)
    decision = payload.get("decision")
    if decision not in Submission.Decision.values:
        raise HubError("OMEGA_HUB_INVALID", 400, "decision is approve, reject or request_changes")
    version = _rules_version(payload.get("rulesVersion"))
    now = timezone.now()
    if decision == Submission.Decision.APPROVE:
        if submission.submitted_by_id == user.pk:
            raise HubError("OMEGA_HUB_SELF_APPROVAL", 403)
        if payload.get("notes"):
            raise HubError("OMEGA_HUB_NOTES_INVALID", 400, "change notes go with request_changes only")
        checklist = _checklist(payload.get("checklist"), version)
        job = latest_job(submission)
        if job is None or job.status != CheckJob.Status.PASSED:
            raise HubError("OMEGA_HUB_CHECKS_NOT_PASSED", 409)
        acknowledged = payload.get("acknowledgedFlags") or []
        if not isinstance(acknowledged, list) or set(flag_ids(job.report)) - set(acknowledged):
            raise HubError("OMEGA_HUB_FLAGS_UNACKNOWLEDGED", 400,
                           extra={"flags": sorted(set(flag_ids(job.report)) - set(acknowledged if isinstance(acknowledged, list) else []))})
        confirmed = disclosure.require_confirmed(payload.get("disclosure"), job.report)
        submission.checklist = checklist
        submission.acknowledged_flags = sorted(set(acknowledged))
        submission.confirmed_disclosure = confirmed
        published = publish(submission, job, approver=user, confirmed=confirmed, rules_version=version, via=via)
        reason, action = "", "review.approved"
    else:
        reason = _reason(payload.get("reason"))
        notes = _notes(payload.get("notes"))
        if notes and decision != Submission.Decision.REQUEST_CHANGES:
            raise HubError("OMEGA_HUB_NOTES_INVALID", 400, "change notes go with request_changes only")
        submission.change_notes = notes or None
        published = None
        action = "review.rejected" if decision == Submission.Decision.REJECT else "review.changes_requested"
    submission.decision = decision
    submission.decision_reason = reason
    submission.decided_by = user
    submission.decided_at = now
    submission.rules_version = version
    submission.save()
    target = {"approve": Submission.Status.APPROVED, "reject": Submission.Status.REJECTED,
              "request_changes": Submission.Status.CHANGES_REQUESTED}[decision]
    move(submission, target, reason=reason, _approve=_APPROVE_TOKEN if target == Submission.Status.APPROVED else None)
    audit.record(action, target_type="submission", target_id=submission.pk, actor=user, publisher=submission.publisher,
                 data={"rulesVersion": version, **({"version": str(published.pk)} if published else {}),
                       **({"via": via} if via else {}),
                       **({"notes": len(submission.change_notes or [])} if decision == "request_changes" else {})})
    notify.decision(submission, published)
    return submission


@transaction.atomic
def recheck(user, submission: Submission) -> Submission:
    submission = Submission.objects.select_for_update().get(pk=submission.pk)
    if submission.status not in (Submission.Status.IN_REVIEW, Submission.Status.CHECK_ERROR):
        raise HubError("OMEGA_HUB_BAD_STATE", 409)
    move(submission, Submission.Status.RECEIVED, reason="Automated checks queued again by a maintainer.")
    job = CheckJob.objects.create(submission=submission)
    audit.record("check.requeued", target_type="submission", target_id=submission.pk, actor=user,
                 publisher=submission.publisher, data={"job": str(job.pk), "by": "maintainer"})
    return submission
