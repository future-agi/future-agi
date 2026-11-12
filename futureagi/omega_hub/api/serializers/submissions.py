"""Submission shapes: the submitter's view, and the maintainer's (SPEC §3.1, §9.3, §9.4).

The submitter never sees who reviewed; the maintainer view never leaves the review API.
"""

from __future__ import annotations

from omega_hub import conf
from omega_hub.models import AuditEvent, Submission
from omega_hub.services.checks import latest_job
from omega_hub.services.publish import public_check_summary

STATUS_TEXT = {
    "received": "Received. Automated checks start in a moment.",
    "checking": "Automated checks are running: manifest, licence, secrets, dependencies, static scan, sandbox run, demo.",
    "checks_failed": "The automated checks found problems. Fix each finding and publish again.",
    "check_error": "We could not run the checks. A maintainer has been told and will run them again.",
    "in_review": "Checks passed. Waiting for a Future AGI maintainer. Usually within 2 business days.",
    "changes_requested": "A maintainer asked for changes. Answer by publishing a new upload.",
    "rejected": "A maintainer did not accept this submission.",
    "approved": "Published.",
    "withdrawn": "Withdrawn.",
    "superseded": "Replaced by your newer upload.",
}
SUBMITTER_ACTIONS = ("submission.", "check.passed", "check.failed", "check.errored", "check.claimed",
                     "review.approved", "review.rejected", "review.changes_requested")


def timeline(submission: Submission) -> list[dict]:
    events = AuditEvent.objects.filter(target_type="submission", target_id=str(submission.pk)).order_by("at")
    return [{"action": event.action, "at": event.at.isoformat()} for event in events
            if event.action.startswith(SUBMITTER_ACTIONS)]


def for_submitter(submission: Submission) -> dict:
    job = latest_job(submission)
    report = job.report if job and job.report else None
    out = {
        "id": str(submission.pk),
        "publisher": submission.publisher.handle,
        "channel": submission.channel,
        "status": submission.status,
        "statusText": STATUS_TEXT[submission.status],
        "name": submission.name,
        "version": submission.version,
        "hints": {"name": submission.hint_name, "version": submission.hint_version},
        "notes": submission.notes,
        "createdAt": submission.created_at.isoformat(),
        "updatedAt": submission.updated_at.isoformat(),
        "timeline": timeline(submission),
        "report": report if submission.status == Submission.Status.CHECKS_FAILED else None,
        "checkSummary": public_check_summary(report) if report else None,
        "decision": None,
        "confirmedDisclosure": None,
        "published": None,
        "url": f"{conf.app_url()}/submissions/{submission.pk}",
    }
    if submission.decision:
        out["decision"] = {
            "decision": submission.decision,
            "reason": submission.decision_reason or None,
            "notes": submission.change_notes or [],
            "decidedAt": submission.decided_at.isoformat() if submission.decided_at else None,
        }
    if submission.status == Submission.Status.APPROVED:
        out["confirmedDisclosure"] = submission.confirmed_disclosure
        version = getattr(submission, "published_version", None)
        if version is not None:
            out["published"] = f"{submission.publisher.handle}/{version.entry.name}@{version.version}"
    return out


def queue_row(submission: Submission) -> dict:
    job = latest_job(submission)
    report = job.report if job and job.report else {}
    flags = sum(1 for step in report.get("steps", []) for item in step.get("findings", []) if item.get("severity") == "flag")
    return {
        "id": str(submission.pk),
        "ref": f"{submission.publisher.handle}/{submission.name or submission.hint_name or '?'}",
        "version": submission.version or submission.hint_version,
        "kind": (submission.manifest or {}).get("kind"),
        "tier": submission.publisher.tier,
        "channel": submission.channel,
        "status": submission.status,
        "isUpdate": submission.previous_version_id is not None,
        "flags": flags,
        "createdAt": submission.created_at.isoformat(),
        "reviewer": submission.reviewer.email if submission.reviewer_id else None,
        "reviewStartedAt": submission.review_started_at.isoformat() if submission.review_started_at else None,
    }
