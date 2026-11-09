"""Intake (SPEC §3.1, §9.3 POST /submissions): store the raw upload, queue a check job.

Django never opens the upload beyond its first bytes (to tell gzip from zip); the checker unpacks it.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from omega_hub import conf, reserved
from omega_hub.errors import HubError
from omega_hub.models import CheckJob, Entry, Publisher, Submission
from omega_hub.services import audit, refs, storage, storage_provision
from omega_hub.services.states import OPEN, move

MAX_RAW_BYTES = 2 * 1024 * 1024
MAX_OPEN_CHECKS = 3
MAX_PER_DAY = 10
GZIP_MAGIC = b"\x1f\x8b"
ZIP_MAGIC = b"PK\x03\x04"


def _check_upload(data: bytes) -> None:
    if len(data) > MAX_RAW_BYTES:
        raise HubError("OMEGA_HUB_BUNDLE_TOO_LARGE", 413)
    if not (data.startswith(GZIP_MAGIC) or data.startswith(ZIP_MAGIC)):
        raise HubError("OMEGA_HUB_BUNDLE_TYPE", 400)


def _check_hints(publisher: Publisher, name, version) -> tuple[str | None, str | None]:
    if name not in (None, ""):
        if not isinstance(name, str) or not reserved.NAME.match(name):
            raise HubError("OMEGA_HUB_INVALID", 400, "name is the manifest id: 3-40 lowercase letters, digits and hyphens")
        moved = refs.moved_to_for(publisher.handle, name)
        if moved:
            raise HubError("OMEGA_HUB_REF_TAKEN", 409, f"{publisher.handle}/{name} moved to {moved}; publish as that entry")
        entry = Entry.objects.filter(publisher=publisher, name=name).first()
        if entry is not None and entry.status == Entry.Status.REMOVED:
            raise HubError("OMEGA_HUB_GONE", 410)
    else:
        name = None
    if version not in (None, ""):
        if not refs.is_semver(version):
            raise HubError("OMEGA_HUB_INVALID", 400, "version is plain MAJOR.MINOR.PATCH, with no leading zeros")
        if name and refs.version_taken({"entry__publisher": publisher, "entry__name": name}, version):
            raise HubError("OMEGA_HUB_VERSION_EXISTS", 409)
    else:
        version = None
    return name, version


def _check_limits(publisher: Publisher) -> None:
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if Submission.objects.filter(publisher=publisher, created_at__gte=start).count() >= MAX_PER_DAY:
        raise HubError("OMEGA_HUB_DAILY_LIMIT", 429)
    waiting = Submission.objects.filter(
        publisher=publisher, status__in=(Submission.Status.RECEIVED, Submission.Status.CHECKING)
    ).count()
    if waiting >= MAX_OPEN_CHECKS:
        raise HubError("OMEGA_HUB_TOO_MANY_OPEN", 429)


def supersede_older(submission: Submission, actor=None) -> None:
    """Older open uploads of the same publisher/name@version are replaced by this one."""
    if not submission.name or not submission.version:
        return
    older = Submission.objects.select_for_update().filter(
        publisher=submission.publisher, status__in=OPEN, created_at__lt=submission.created_at,
    ).exclude(pk=submission.pk)
    for other in older:
        same = (other.name or other.hint_name) == submission.name and (other.version or other.hint_version) == submission.version
        if not same:
            continue
        other.superseded_by = submission
        other.save(update_fields=["superseded_by", "updated_at"])
        move(other, Submission.Status.SUPERSEDED, reason=f"Replaced by submission {submission.pk}.")
        audit.record("submission.superseded", target_type="submission", target_id=other.pk, actor=actor,
                     actor_kind=None if actor else "system", publisher=submission.publisher,
                     data={"by": str(submission.pk)})


def require_checker() -> None:
    """Refuse an upload no checker could ever check. Without OMEGA_HUB_CHECKER_TOKEN the checker's
    claim endpoint answers 401 to every checker, so an accepted upload would wait in "received"
    forever; nothing is stored, and the operations check pages omega_hub_checker_not_configured."""
    if not conf.checker_token():
        raise HubError("OMEGA_HUB_CHECKER_NOT_CONFIGURED", 503)


def receive(user, publisher: Publisher, *, data: bytes, channel: str, name=None, version=None, notes="",
            github_commit=None, enforce_limits: bool = True) -> Submission:
    """`enforce_limits=False` only for the maintainer-run Official import (omega_hub_import_official)."""
    if publisher.suspended_at is not None:
        raise HubError("OMEGA_HUB_PUBLISHER_SUSPENDED", 403)
    require_checker()
    storage_provision.require_ready()
    if channel not in Submission.Channel.values:
        raise HubError("OMEGA_HUB_INVALID", 400, "channel is cli, web or github")
    if channel == Submission.Channel.GITHUB:
        if publisher.tier != Publisher.Tier.OFFICIAL:
            raise HubError("OMEGA_HUB_CHANNEL_NOT_ALLOWED", 403)
        if not isinstance(github_commit, str) or len(github_commit) != 40 or not all(c in "0123456789abcdef" for c in github_commit):
            raise HubError("OMEGA_HUB_INVALID", 400, "githubCommit is a full 40-character commit SHA")
    else:
        github_commit = None
    if not isinstance(notes, str) or len(notes) > 500:
        raise HubError("OMEGA_HUB_INVALID", 400, "notes are at most 500 characters")
    _check_upload(data)
    name, version = _check_hints(publisher, name, version)
    with transaction.atomic():
        # FOR NO KEY UPDATE serialises uploads per publisher without blocking the FK checks (FOR KEY
        # SHARE) that a concurrent claim's audit insert takes on the same row; FOR UPDATE deadlocked
        # with a claim while supersede_older waited on the submission that claim had just moved.
        Publisher.objects.select_for_update(no_key=True).filter(pk=publisher.pk).first()
        if enforce_limits:
            _check_limits(publisher)
        submission = Submission.objects.create(
            publisher=publisher, submitted_by=user, channel=channel, github_commit=github_commit,
            raw_sha256=storage.sha256_hex(data), raw_bytes=len(data), hint_name=name, hint_version=version,
            notes=notes.strip(),
        )
        submission.raw_key = storage.raw_key(submission.pk)
        submission.save(update_fields=["raw_key"])
        storage.put_bytes(submission.raw_key, data, submission.raw_sha256)
        CheckJob.objects.create(submission=submission)
        audit.record("submission.received", target_type="submission", target_id=submission.pk, actor=user,
                     publisher=publisher, data={"channel": channel, "name": name, "version": version,
                                                "rawSha256": submission.raw_sha256})
        if name and version:
            submission.name, submission.version = name, version
            supersede_older(submission, actor=user)
            submission.name = submission.version = None
    return submission


@transaction.atomic
def withdraw(user, submission: Submission) -> Submission:
    from omega_hub.permissions import can_act_for_publisher

    if submission.submitted_by_id != user.pk and not can_act_for_publisher(user, submission.publisher):
        raise HubError("OMEGA_HUB_NOT_PUBLISHER_OWNER", 403)
    move(submission, Submission.Status.WITHDRAWN, reason="Withdrawn.")
    submission.check_jobs.filter(status__in=(CheckJob.Status.QUEUED, CheckJob.Status.RUNNING)).update(
        status=CheckJob.Status.ERRORED, finished_at=timezone.now()
    )
    audit.record("submission.withdrawn", target_type="submission", target_id=submission.pk, actor=user,
                 publisher=submission.publisher)
    return submission
