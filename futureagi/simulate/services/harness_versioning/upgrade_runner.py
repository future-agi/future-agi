"""Independent operator backfill, with short claims and restartable outcomes.

Archive work stays in the existing per-environment transaction/retry service.
The organization lease fences progress/outcome writers, not archive I/O. A stale
worker cannot report completion over its replacement; archive revision checks
and environment locks continue to protect the underlying version writes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from threading import Event, Thread
from uuid import UUID, uuid4

from django.db import connections, transaction
from django.utils import timezone

from simulate.models import HarnessVersioningBackfillOutcome as Outcome
from simulate.models import HarnessVersioningBackfillProgress as Progress
from simulate.models import HostedHarnessJob
from simulate.models.harness_versioning_backfill import BackfillState
from simulate.services.harness_versioning import backfill
from simulate.services.harness_versioning.upgrade_state import (
    HEARTBEAT_SECONDS,
    OUTCOME_CODES,
    live_attempt,
)
from simulate.services.harness_versioning.versions import versioning_enabled


class LeaseLost(RuntimeError):
    """This attempt cannot write state or claim successful completion."""


@dataclass(frozen=True)
class Claim:
    organization_id: UUID
    run_token: UUID
    attempt: int


@dataclass
class RunReport:
    results: list[backfill.BackfillResult] = field(default_factory=list)
    total: int = 0
    busy_organizations: int = 0
    off: bool = False

    @property
    def failed(self):
        return sum(
            result.outcome in backfill.FAILED_OUTCOMES for result in self.results
        )

    @property
    def state(self):
        if self.off:
            return "off"
        if self.failed:
            return "failed"
        if self.busy_organizations:
            return "already_running"
        return "complete"

    def counts(self, *, running=False):
        # Only vocabulary and counts: never ids, tokens, notes or exception text.
        visited = [r for r in self.results if r.outcome != "already_running"]
        skipped = sum(_category(result) == "skipped" for result in visited)
        failed = sum(_category(result) == "failed" for result in visited)
        return {
            "state": "running" if running else self.state,
            "total": self.total,
            "processed": len(visited),
            "succeeded": len(visited) - failed - skipped,
            "failed": failed,
            "skipped": skipped,
            "busy_organizations": self.busy_organizations,
        }


def _category(result):
    if result.outcome in backfill.FAILED_OUTCOMES:
        return "failed"
    if (
        result.outcome == "current"
        and not result.agent_pinned
        and not result.bundle_recorded
        and not result.runs_pinned
    ):
        return "skipped"
    return "succeeded"


def claim_organization(organization_id, *, total):
    """Claim once under a short row lock; a live competitor does no work."""
    with transaction.atomic():
        progress, _ = Progress.objects.select_for_update().get_or_create(
            organization_id=organization_id
        )
        now = timezone.now()
        if live_attempt(progress, now):
            return None
        progress.run_token = uuid4()
        progress.attempt += 1
        progress.state = BackfillState.RUNNING
        progress.total = total
        progress.processed = progress.succeeded = progress.failed = progress.skipped = 0
        progress.outcomes = {}
        progress.started_at = progress.heartbeat_at = now
        progress.finished_at = None
        progress.save()
        return Claim(organization_id, progress.run_token, progress.attempt)


def _owned(claim):
    progress = (
        Progress.objects.select_for_update()
        .filter(
            organization_id=claim.organization_id,
            run_token=claim.run_token,
            state=BackfillState.RUNNING,
        )
        .first()
    )
    if progress is None:
        raise LeaseLost("backfill attempt no longer owns its claim")
    return progress


def heartbeat(claim):
    now = timezone.now()
    changed = Progress.objects.filter(
        organization_id=claim.organization_id,
        run_token=claim.run_token,
        state=BackfillState.RUNNING,
    ).update(heartbeat_at=now, updated_at=now)
    if changed != 1:
        raise LeaseLost("backfill heartbeat no longer owns its claim")


class Heartbeat:
    """A timer, not a per-environment pulse: long archive work stays observable."""

    def __init__(self, claim, *, on_tick=None, interval=HEARTBEAT_SECONDS):
        self.claim = claim
        self.on_tick = on_tick
        self.interval = interval
        self.stop = Event()
        self.failed = Event()
        self.thread = Thread(
            target=self._run, name="harness-backfill-heartbeat", daemon=True
        )

    def _run(self):
        try:
            while not self.stop.wait(self.interval):
                try:
                    heartbeat(self.claim)
                    if self.on_tick is not None:
                        self.on_tick()
                except Exception:  # noqa: BLE001 - no diagnostic text in automatic mode
                    self.failed.set()
                    return
        finally:
            connections.close_all()

    def check(self):
        if self.failed.is_set():
            raise LeaseLost("backfill heartbeat failed; retry the operator command")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self.failed.set()
        if exc_type is None:
            self.check()


def record_started(claim, job_id):
    with transaction.atomic():
        _owned(claim)
        # A missing/deleted target can still count as a failure, but no FK can be
        # created for a hard-deleted row. Never attach another tenant's outcome.
        organization_id = (
            HostedHarnessJob._base_manager.filter(id=job_id)
            .values_list("organization_id", flat=True)
            .first()
        )
        if organization_id is None:
            return
        if organization_id != claim.organization_id:
            raise ValueError("backfill environment organization mismatch")
        previous = Outcome.objects.filter(environment_id=job_id).first()
        if previous is not None and previous.run_token == claim.run_token:
            return
        Outcome.objects.update_or_create(
            environment_id=job_id,
            defaults={
                "run_token": claim.run_token,
                "attempt": claim.attempt,
                "state": BackfillState.RUNNING,
                "outcome_code": "",
                "started_at": timezone.now(),
                "finished_at": None,
            },
        )


def record_result(claim, result):
    with transaction.atomic():
        progress = _owned(claim)
        outcome = (
            Outcome.objects.select_for_update()
            .filter(
                environment_id=result.job_id,
                environment__organization_id=claim.organization_id,
            )
            .first()
        )
        if outcome is not None:
            if outcome.run_token != claim.run_token:
                raise LeaseLost("backfill outcome belongs to another attempt")
            if outcome.state != BackfillState.RUNNING:
                return
        elif HostedHarnessJob._base_manager.filter(id=result.job_id).exists():
            raise LeaseLost("backfill outcome was not started by this attempt")
        code = result.outcome if result.outcome in OUTCOME_CODES else "error"
        category = _category(result) if code != "error" else "failed"
        now = timezone.now()
        if outcome is not None:
            outcome.state = (
                BackfillState.FAILED if category == "failed" else BackfillState.COMPLETE
            )
            outcome.outcome_code = code
            outcome.finished_at = now
            outcome.save(
                update_fields=["state", "outcome_code", "finished_at", "updated_at"]
            )
        progress.processed += 1
        setattr(progress, category, getattr(progress, category) + 1)
        counts = Counter(progress.outcomes)
        counts[code] += 1
        progress.outcomes = dict(counts)
        progress.heartbeat_at = now
        progress.save(
            update_fields=[
                "processed",
                category,
                "outcomes",
                "heartbeat_at",
                "updated_at",
            ]
        )


def finish_claim(claim, *, interrupted=False):
    with transaction.atomic():
        progress = _owned(claim)
        now = timezone.now()
        incomplete = progress.processed != progress.total
        progress.state = (
            BackfillState.FAILED
            if interrupted or incomplete or progress.failed
            else BackfillState.COMPLETE
        )
        progress.finished_at = progress.heartbeat_at = now
        progress.save(
            update_fields=["state", "finished_at", "heartbeat_at", "updated_at"]
        )
        if interrupted or incomplete:
            Outcome.objects.filter(
                environment__organization_id=claim.organization_id,
                run_token=claim.run_token,
                state=BackfillState.RUNNING,
            ).update(
                state=BackfillState.FAILED,
                outcome_code="interrupted",
                finished_at=now,
                updated_at=now,
            )


def _one(job_id, *, automatic):
    if not automatic:
        # Preserve the manual command's existing exception logs and report notes.
        return backfill.run_backfill([job_id])[0]
    try:
        result = backfill.backfill_environment(job_id)
    except Exception:  # noqa: BLE001 - continue with every other environment
        return backfill.BackfillResult(job_id=str(job_id), outcome="error")
    result.notes = []
    return result


def run_managed(job_ids=None, *, automatic=False, on_progress=None):
    """Run outside bootstrap; explicit off is a no-write automatic success.

    Exit-zero 'already_running' is not a completion claim. Manual invocations
    still run with the feature off, preserving the existing repair interface.
    """
    report = RunReport()
    if automatic and not versioning_enabled():
        report.off = True
        if on_progress is not None:
            on_progress(report.counts())
        return report
    ids = (
        list(job_ids)
        if job_ids is not None
        else backfill.environments_to_backfill(backfill.default_models())
    )
    # A repeated --job is reported at each input position, but is only visited
    # and counted once in the organization's persisted attempt.
    unique_ids = {str(job_id): job_id for job_id in ids}
    report.total = len(unique_ids)
    organizations = {
        str(job_id): org_id
        for job_id, org_id in HostedHarnessJob._base_manager.filter(
            id__in=unique_ids.values()
        ).values_list("id", "organization_id")
    }
    groups = {}
    for key, job_id in unique_ids.items():
        groups.setdefault(organizations.get(key), []).append(job_id)
    completed = {}

    def emit():
        if on_progress is not None:
            on_progress(report.counts(running=True))

    for organization_id, group_ids in groups.items():
        if organization_id is None:
            for job_id in group_ids:
                result = _one(job_id, automatic=automatic)
                completed[str(job_id)] = result
                report.results.append(result)
            continue
        claim = claim_organization(organization_id, total=len(group_ids))
        if claim is None:
            report.busy_organizations += 1
            for job_id in group_ids:
                result = backfill.BackfillResult(
                    job_id=str(job_id), outcome="already_running"
                )
                completed[str(job_id)] = result
                report.results.append(result)
            continue
        try:
            emit()
            with Heartbeat(claim, on_tick=emit) as pulse:
                for job_id in group_ids:
                    pulse.check()
                    record_started(claim, job_id)
                    result = _one(job_id, automatic=automatic)
                    pulse.check()
                    record_result(claim, result)
                    completed[str(job_id)] = result
                    report.results.append(result)
                    emit()
            finish_claim(claim)
        except BaseException:
            # Cancellation leaves no success claim. If ownership changed, do
            # not touch the replacement's state. A DB outage reads stale later.
            try:
                finish_claim(claim, interrupted=True)
            except Exception:  # noqa: BLE001 - preserve the original exception
                pass
            raise
    if on_progress is not None:
        on_progress(report.counts())
    report.results = [completed[str(job_id)] for job_id in ids]
    return report
