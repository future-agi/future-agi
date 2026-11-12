"""Small, scrapeable operational metrics for the Hub.

The metrics are derived from durable state rather than process-local counters, so a
restart or a second web replica cannot make the queue appear healthy.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from omega_hub import conf
from omega_hub.models import (
    CheckJob,
    EmailOutbox,
    EmailOutboxStatus,
    Report,
    Submission,
)
from omega_hub.services import bundle_locks, notify


def _age(value, now) -> float:
    if value is None:
        return 0.0
    return max(0.0, (now - value).total_seconds())


def snapshot(*, now=None) -> dict[str, float]:
    now = now or timezone.now()
    queued = CheckJob.objects.filter(status=CheckJob.Status.QUEUED)
    in_review = Submission.objects.filter(status=Submission.Status.IN_REVIEW)
    security = Report.objects.filter(reason=Report.Reason.SECURITY, status=Report.Status.OPEN)
    hour_ago = now - timedelta(hours=1)
    errors = CheckJob.objects.filter(status=CheckJob.Status.ERRORED, finished_at__gte=hour_ago)
    outbox_pending = EmailOutbox.objects.filter(status__in=[EmailOutboxStatus.PENDING, EmailOutboxStatus.SENDING])
    # queued_at, not created_at: a requeued job (an expired lease, a failed attempt) restarts its wait.
    oldest_queued = queued.order_by("queued_at").values_list("queued_at", flat=True).first()
    latest_claim = CheckJob.objects.filter(claimed_at__isnull=False).order_by("-claimed_at").values_list(
        "claimed_at", flat=True
    ).first()
    expired_running = CheckJob.objects.filter(
        status=CheckJob.Status.RUNNING, lease_expires_at__lt=now
    )
    return {
        "omega_hub_checker_queued_jobs": float(queued.count()),
        "omega_hub_checker_oldest_queued_age_seconds": _age(oldest_queued, now),
        "omega_hub_checker_expired_running_jobs": float(expired_running.count()),
        "omega_hub_review_oldest_in_review_age_seconds": _age(in_review.order_by("created_at").values_list("created_at", flat=True).first(), now),
        "omega_hub_security_oldest_open_report_age_seconds": _age(security.order_by("created_at").values_list("created_at", flat=True).first(), now),
        "omega_hub_checker_errors_total_1h": float(errors.count()),
        "omega_hub_email_outbox_pending": float(outbox_pending.count()),
        "omega_hub_email_outbox_oldest_pending_age_seconds": _age(
            outbox_pending.order_by("created_at").values_list("created_at", flat=True).first(), now
        ),
        # Zero means no checker has claimed yet; queue age is the separate
        # signal used for stall paging and avoids false pages after idle time.
        "omega_hub_checker_seconds_since_last_claim": _age(latest_claim, now),
        # 0 when OMEGA_HUB_CHECKER_TOKEN is unset: no checker can authenticate, and uploads are refused.
        "omega_hub_checker_configured": 1.0 if conf.checker_token() else 0.0,
        # Who an operations page reaches: active maintainers plus OMEGA_HUB_OPS_ALERT_EMAILS. 0 pages no one.
        "omega_hub_ops_alert_recipients": float(len(notify.ops_recipients())),
        "omega_hub_bundle_locks_overdue": float(bundle_locks.overdue(now)),
    }


def storage_ready() -> dict[str, float]:
    """1 or 0 from the last operations check's storage reconcile; absent until one has run."""
    from django.core.cache import cache

    from omega_hub.services.storage_provision import STATUS_CACHE_KEY

    value = cache.get(STATUS_CACHE_KEY)
    return {} if value is None else {"omega_hub_storage_ready": 1.0 if value else 0.0}


def prometheus(*, now=None) -> str:
    lines = []
    for name, value in {**snapshot(now=now), **storage_ready()}.items():
        lines.append(f"# HELP {name} Omega Hub operational gauge")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value:g}")
    return "\n".join(lines) + "\n"
