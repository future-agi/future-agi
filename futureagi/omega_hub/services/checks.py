"""The checker's side of Django (SPEC §4.1, §9.4): claim, context, heartbeat and result.

The checker pulls work; Django never runs untrusted code. A lease is 15 minutes; an expired lease
goes back to the queue on the next claim, and after 3 attempts the job errors and the submission
becomes `check_error`.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.db import models, transaction
from django.utils import timezone

from omega_hub.errors import HubError
from omega_hub.models import BlockedDigest, CheckJob, Entry, Submission
from omega_hub.services import audit, bundles, notify, refs, storage, storage_provision
from omega_hub.services.intake import supersede_older
from omega_hub.services.states import OPEN, move

LEASE = timedelta(minutes=15)
MAX_ATTEMPTS = 3
PREFIX = "/omega-hub/internal/checker/jobs"
CODE_HASHES_CACHE = "omega_hub:code_hashes"  # == cache_keys.CODE_HASHES
REPORT_SCHEMA = "omega-hub-check-report/v1"


def _urls(job_id) -> dict:
    base = f"{PREFIX}/{job_id}"
    return {"bundleUrl": f"{base}/bundle", "contextUrl": f"{base}/context", "heartbeatUrl": f"{base}/heartbeat",
            "resultUrl": f"{base}/result", "previousBundleUrl": f"{base}/previous-bundle"}


def code_hashes(exclude_entry_id=None) -> dict[str, list[str]]:
    cached = cache.get(CODE_HASHES_CACHE)
    if cached is None:
        cached = {}
        for entry in Entry.objects.select_related("publisher", "latest_version").filter(
            status=Entry.Status.LISTED, latest_version__isnull=False
        ):
            hashes = sorted({row["sha256"] for row in entry.latest_version.files
                             if row["path"].endswith((*bundles.CODE_EXTENSIONS, ".html"))})
            cached[str(entry.pk)] = [entry.ref, hashes]
        cache.set(CODE_HASHES_CACHE, cached, 300)
    return {ref: hashes for entry_id, (ref, hashes) in cached.items() if entry_id != str(exclude_entry_id)}


def invalidate_code_hashes() -> None:
    cache.delete(CODE_HASHES_CACHE)


def _context_for(submission: Submission, job_id, name: str | None, version: str | None, remix_of: str | None) -> dict:
    handle = submission.publisher.handle
    entry = Entry.objects.filter(publisher=submission.publisher, name=name).first() if name else None
    previous = None
    if entry is not None:
        previous = entry.latest_version or entry.versions.order_by("-semver_key").first()
    remix_ok = remix_blocked = None
    if remix_of:
        try:
            p, n, v = refs.parse_ref(remix_of)
        except HubError:
            p = n = v = None
        target = refs.approved_version(p, n, v) if p and v else None
        remix_ok = target is not None
        remix_blocked = bool(target) and BlockedDigest.objects.filter(lifted_at__isnull=True).filter(
            models.Q(digest=target.digest) | models.Q(bundle_sha256=target.bundle_sha256)).exists()
    return {
        "previousVersion": ({"version": previous.version, "bundleUrl": _urls(job_id)["previousBundleUrl"]}
                            if previous else None),
        "versionTaken": bool(name and refs.version_taken(
            {"entry__publisher": submission.publisher, "entry__name": name}, version)),
        "remixTargetOk": remix_ok,
        "remixTargetBlocked": remix_blocked,
        "movedTo": refs.moved_to_for(handle, name) if name else None,
        "entryRemoved": bool(entry and entry.status == Entry.Status.REMOVED),
        "codeHashes": code_hashes(exclude_entry_id=entry.pk if entry else None),
    }, previous


def _expire_leases(now=None) -> int:
    now = now or timezone.now()
    expired = CheckJob.objects.select_for_update(skip_locked=True).select_related("submission__publisher").filter(
        status=CheckJob.Status.RUNNING, lease_expires_at__lt=now
    )
    count = 0
    for job in expired:
        _fail_attempt(job, "the lease expired")
        count += 1
    return count


@transaction.atomic
def reap_expired_leases(*, now=None) -> int:
    """Reap abandoned checker leases without depending on a live checker.

    The operations schedule calls this independently of ``claim``. A checker
    that dies therefore cannot leave a RUNNING job and a quiet queue forever.
    """
    return _expire_leases(now=now)


def _fail_attempt(job: CheckJob, why: str) -> None:
    submission = job.submission
    if job.attempt >= MAX_ATTEMPTS:
        job.status = CheckJob.Status.ERRORED
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at", "updated_at"])
        if submission.status in OPEN:
            move(submission, Submission.Status.CHECK_ERROR, reason=why)
            audit.record_checker("check.errored", target_type="submission", target_id=submission.pk,
                                 publisher=submission.publisher, data={"job": str(job.pk), "why": why})
            notify.check_error(submission)
        return
    job.status = CheckJob.Status.QUEUED
    job.leased_by = ""
    job.lease_expires_at = None
    # Back in the queue now: the stall alert counts from here, not from the submission's arrival.
    job.queued_at = timezone.now()
    job.save(update_fields=["status", "leased_by", "lease_expires_at", "queued_at", "updated_at"])
    audit.record_checker("check.requeued", target_type="check_job", target_id=job.pk,
                         publisher=submission.publisher, data={"attempt": job.attempt, "why": why})


def _leased(job_id, checker_id: str) -> CheckJob:
    job = CheckJob.objects.select_for_update(of=("self",)).select_related("submission__publisher").filter(pk=job_id).first()
    if job is None:
        raise HubError("OMEGA_HUB_NOT_FOUND", 404)
    if job.status != CheckJob.Status.RUNNING or (checker_id and job.leased_by != checker_id):
        raise HubError("OMEGA_HUB_LEASE_LOST", 409)
    return job


@transaction.atomic
def claim(checker_id: str) -> dict | None:
    _expire_leases()
    if storage_provision.known_unready():
        # A check could not store its bundle: leave the job queued (no attempt is spent) until the
        # bucket is ready. The operations check pages omega_hub_storage_unprovisioned meanwhile.
        return None
    job = (
        CheckJob.objects.select_for_update(skip_locked=True, of=("self",))
        .select_related("submission__publisher")
        .filter(status=CheckJob.Status.QUEUED, submission__status__in=(Submission.Status.RECEIVED, Submission.Status.CHECKING))
        .order_by("created_at")
        .first()
    )
    if job is None:
        return None
    now = timezone.now()
    job.status = CheckJob.Status.RUNNING
    job.attempt += 1
    job.leased_by = checker_id[:120]
    job.lease_expires_at = now + LEASE
    job.claimed_at = now
    job.started_at = job.started_at or now
    job.save(update_fields=["status", "attempt", "leased_by", "lease_expires_at", "claimed_at", "started_at", "updated_at"])
    submission = job.submission
    if submission.status == Submission.Status.RECEIVED:
        move(submission, Submission.Status.CHECKING)
    audit.record_checker("check.claimed", target_type="submission", target_id=submission.pk,
                         publisher=submission.publisher, data={"job": str(job.pk), "attempt": job.attempt})
    context, _ = _context_for(submission, job.pk, submission.hint_name, submission.hint_version, None)
    return {
        "job": str(job.pk), "submission": str(submission.pk), "attempt": job.attempt,
        "publisher": submission.publisher.handle, "publisherTier": submission.publisher.tier,
        "channel": submission.channel, "hints": {"name": submission.hint_name, "version": submission.hint_version},
        **context, **_urls(job.pk),
    }


@transaction.atomic
def context(job_id, checker_id: str, *, name, version, remix_of) -> dict:
    job = _leased(job_id, checker_id)
    submission = job.submission
    ctx, previous = _context_for(submission, job.pk, name, version, remix_of)
    submission.previous_version = previous
    submission.save(update_fields=["previous_version", "updated_at"])
    return ctx


@transaction.atomic
def heartbeat(job_id, checker_id: str) -> dict:
    job = _leased(job_id, checker_id)
    job.lease_expires_at = timezone.now() + LEASE
    job.save(update_fields=["lease_expires_at", "updated_at"])
    return {"leaseExpiresAt": job.lease_expires_at.isoformat()}


def _blocked(report: dict) -> bool:
    for step in report.get("steps", []):
        if step.get("status") == "fail":
            return True
        if any(item.get("severity") == "block" for item in step.get("findings", [])):
            return True
    return False


def _validate_report(report, job: CheckJob) -> None:
    if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
        raise HubError("OMEGA_HUB_REPORT_INVALID", 400)
    if report.get("job") != str(job.pk) or report.get("submission") != str(job.submission_id):
        raise HubError("OMEGA_HUB_REPORT_INVALID", 400, "the report names another job or submission")
    if report.get("outcome") not in ("in_review", "checks_failed", "error") or not isinstance(report.get("steps"), list):
        raise HubError("OMEGA_HUB_REPORT_INVALID", 400, "outcome or steps missing")


@transaction.atomic
def result(job_id, checker_id: str, *, report: dict, bundle: bytes | None, log: bytes | None,
           cast: bytes | None) -> dict:
    job = _leased(job_id, checker_id)
    _validate_report(report, job)
    submission = job.submission
    if report["outcome"] == "error":
        _fail_attempt(job, str(report.get("error") or "the checker could not run the checks")[:500])
        return {"status": job.status}
    checker = report.get("checker") or {}
    job.report = report
    job.finished_at = timezone.now()
    job.checker_version = str(checker.get("version", ""))[:40]
    job.omega_commit = str(checker.get("omegaCommit", ""))[:40]
    job.runner_image = str(checker.get("runnerImage", ""))[:120]
    if log:
        job.log_key = storage.log_key(job.pk)
        storage.put_bytes(job.log_key, log[: 256 * 1024])
    if cast:
        job.cast_key = storage.job_cast_key(job.pk)
        storage.put_bytes(job.cast_key, cast)
    failed = _blocked(report) or report["outcome"] == "checks_failed"
    if bundle:
        sha = storage.sha256_hex(bundle)
        if sha != report.get("bundleSha256"):
            raise HubError("OMEGA_HUB_BUNDLE_MISMATCH", 400, "the bundle's SHA-256 is not the report's bundleSha256")
        files = bundles.read_canonical(bundle)
        manifest = bundles.manifest_of(files)
        declared = report.get("manifest") or {}
        if manifest.get("id") != declared.get("id") or manifest.get("version") != declared.get("version"):
            raise HubError("OMEGA_HUB_BUNDLE_MISMATCH", 400, "the bundle's manifest is not the report's")
        if bundles.catalog_digest(files, manifest) != report.get("digest"):
            raise HubError("OMEGA_HUB_BUNDLE_MISMATCH", 400, "the bundle's digest is not the report's digest")
        key = storage.bundle_key(sha)
        if not storage.get_store().exists(key):
            storage.put_bytes(key, bundle, sha)
        job.canonical_bundle_key = key
        submission.manifest = manifest
        submission.name = manifest.get("id") if isinstance(manifest.get("id"), str) else None
        submission.version = manifest.get("version") if isinstance(manifest.get("version"), str) else None
        submission.save(update_fields=["manifest", "name", "version", "updated_at"])
    elif not failed:
        raise HubError("OMEGA_HUB_REPORT_INVALID", 400, "a passing report needs the canonical bundle")
    job.status = CheckJob.Status.FAILED if failed else CheckJob.Status.PASSED
    job.save()
    if submission.status != Submission.Status.CHECKING:
        return {"status": job.status, "submission": submission.status}
    if failed:
        move(submission, Submission.Status.CHECKS_FAILED, reason="Automated checks failed.")
        audit.record_checker("check.failed", target_type="submission", target_id=submission.pk,
                             publisher=submission.publisher, data={"job": str(job.pk)})
        notify.checks_failed(submission)
        return {"status": job.status, "submission": submission.status}
    newer = Submission.objects.filter(
        publisher=submission.publisher, name=submission.name, version=submission.version,
        status__in=OPEN, created_at__gt=submission.created_at,
    ).exists()
    if newer:
        move(submission, Submission.Status.SUPERSEDED, reason="Replaced by your newer upload.")
        audit.record_checker("submission.superseded", target_type="submission", target_id=submission.pk,
                             publisher=submission.publisher, data={"job": str(job.pk)})
        return {"status": job.status, "submission": submission.status}
    move(submission, Submission.Status.IN_REVIEW, reason="Checks passed. Waiting for a Future AGI maintainer.")
    audit.record_checker("check.passed", target_type="submission", target_id=submission.pk,
                         publisher=submission.publisher, data={"job": str(job.pk)})
    supersede_older(submission)
    return {"status": job.status, "submission": submission.status}


def latest_job(submission: Submission) -> CheckJob | None:
    return submission.check_jobs.order_by("-created_at").first()


def job_bundle(job_id, checker_id: str, previous: bool = False) -> bytes:
    with transaction.atomic():
        job = _leased(job_id, checker_id)
    submission = job.submission
    if not previous:
        return storage.read_bytes(submission.raw_key)
    if submission.previous_version is None:
        raise HubError("OMEGA_HUB_NOT_FOUND", 404)
    return storage.read_bytes(submission.previous_version.bundle_key)
