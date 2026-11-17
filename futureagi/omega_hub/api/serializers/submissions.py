"""Submission shapes: the submitter's view, and the maintainer's (SPEC §3.1, §9.3, §9.4).

The submitter never sees who reviewed; the maintainer view never leaves the review API.
"""

from __future__ import annotations

import re
import unicodedata

from omega_hub import conf
from omega_hub.models import AuditEvent, Submission
from omega_hub.services import checker_presence
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
# While no Hub checker is alive (services/checker_presence.py) nothing moves a received
# or checking submission, so it says so and its answer's checksStopped is true, which
# lets a status label agree with the sentence. Both return to normal once a checker is
# alive.
NO_CHECKER_TEXT = {
    "received": "Received. The checks are not running right now. This upload is checked when they start.",
    "checking": "The checks stopped because no Hub checker is running. They start again when one is running.",
}
SUBMITTER_ACTIONS = (
    "submission.",
    "check.passed",
    "check.failed",
    "check.errored",
    "check.claimed",
    "review.approved",
    "review.rejected",
    "review.changes_requested",
)

# A queue row's summary: the manifest's tagline, as one line of plain text.
SUMMARY_CHARS = 160
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
# A tagline holds at most 120 characters (the manifest schema); a far longer one is read
# only this far.
_TAGLINE_READ_CHARS = 2000
_TAG = re.compile(r"<[^<>]*>")
# What the manifest schema never allows in a tagline: backticks, angle and square
# brackets.
_MARKUP = frozenset("`<>[]")
# Control, format (bidirectional overrides, zero-width), surrogate and private-use.
_UNSHOWN = frozenset({"Cc", "Cf", "Cs", "Co"})


def timeline(submission: Submission) -> list[dict]:
    events = AuditEvent.objects.filter(
        target_type="submission", target_id=str(submission.pk)
    ).order_by("at")
    return [
        {"action": event.action, "at": event.at.isoformat()}
        for event in events
        if event.action.startswith(SUBMITTER_ACTIONS)
    ]


def checks_stopped(
    submission: Submission, *, checker_alive: bool | None = None
) -> bool:
    """A received or checking submission whose checks are not running, because no Hub
    checker is alive. `checker_alive` is a reading already taken for this request; None
    asks, and only for a received or checking submission."""
    if submission.status not in NO_CHECKER_TEXT:
        return False
    if checker_alive is None:
        checker_alive = checker_presence.alive()
    return not checker_alive


def for_submitter(submission: Submission, *, checker_alive: bool | None = None) -> dict:
    job = latest_job(submission)
    report = job.report if job and job.report else None
    stopped = checks_stopped(submission, checker_alive=checker_alive)
    sentences = NO_CHECKER_TEXT if stopped else STATUS_TEXT
    out = {
        "id": str(submission.pk),
        "publisher": submission.publisher.handle,
        "channel": submission.channel,
        "status": submission.status,
        "statusText": sentences[submission.status],
        "checksStopped": stopped,
        "name": submission.name,
        "version": submission.version,
        "hints": {"name": submission.hint_name, "version": submission.hint_version},
        "notes": submission.notes,
        "createdAt": submission.created_at.isoformat(),
        "updatedAt": submission.updated_at.isoformat(),
        "timeline": timeline(submission),
        "report": report
        if submission.status == Submission.Status.CHECKS_FAILED
        else None,
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
            "decidedAt": submission.decided_at.isoformat()
            if submission.decided_at
            else None,
        }
    if submission.status == Submission.Status.APPROVED:
        out["confirmedDisclosure"] = submission.confirmed_disclosure
        version = getattr(submission, "published_version", None)
        if version is not None:
            out["published"] = (
                f"{submission.publisher.handle}/{version.entry.name}@{version.version}"
            )
    return out


def for_submitter_list(submissions) -> list[dict]:
    """The submitter's view of many submissions in one answer. Whether a checker is
    alive is asked at most once, and only when one of them is received or checking."""
    rows = list(submissions)
    waiting = any(row.status in NO_CHECKER_TEXT for row in rows)
    # None when nothing is waiting: for_submitter then asks for no row, because only a
    # received or checking row would.
    checker_alive = checker_presence.alive() if waiting else None
    return [for_submitter(row, checker_alive=checker_alive) for row in rows]


def summary_of(manifest) -> str | None:
    """The manifest's tagline as one line of plain text of at most SUMMARY_CHARS
    characters, or None when it has none.

    Tags, the characters a tagline may not hold and unseen characters are removed, and
    every run of whitespace becomes one space. A longer line is cut at a word and ends
    with an ellipsis.
    """
    tagline = manifest.get("tagline") if isinstance(manifest, dict) else None
    if not isinstance(tagline, str):
        return None
    kept = []
    for char in _TAG.sub("", tagline[:_TAGLINE_READ_CHARS]):
        if char.isspace():
            kept.append(" ")
        elif char not in _MARKUP and unicodedata.category(char) not in _UNSHOWN:
            kept.append(char)
    line = " ".join("".join(kept).split())
    if len(line) <= SUMMARY_CHARS:
        return line or None
    cut = line.rfind(" ", SUMMARY_CHARS // 2, SUMMARY_CHARS)
    if cut < 0:
        cut = SUMMARY_CHARS - 1
    return line[:cut].rstrip() + ELLIPSIS


def queue_row(submission: Submission) -> dict:
    job = latest_job(submission)
    report = job.report if job and job.report else {}
    flags = sum(
        1
        for step in report.get("steps", [])
        for item in step.get("findings", [])
        if item.get("severity") == "flag"
    )
    return {
        "id": str(submission.pk),
        "ref": f"{submission.publisher.handle}/{submission.name or submission.hint_name or '?'}",
        "version": submission.version or submission.hint_version,
        "kind": (submission.manifest or {}).get("kind"),
        "summary": summary_of(submission.manifest),
        "tier": submission.publisher.tier,
        "channel": submission.channel,
        "status": submission.status,
        "isUpdate": submission.previous_version_id is not None,
        "flags": flags,
        "createdAt": submission.created_at.isoformat(),
        "reviewer": submission.reviewer.email if submission.reviewer_id else None,
        "reviewStartedAt": submission.review_started_at.isoformat()
        if submission.review_started_at
        else None,
    }
