"""Operational health decisions and maintainer paging for the Hub."""

from __future__ import annotations

from django.core.cache import cache
from django.utils import timezone

from omega_hub import conf
from omega_hub.models import Report
from omega_hub.services import checks, metrics, notify, storage_provision, support_hours

CHECKER_STALL_SECONDS = 10 * 60
SECURITY_REPORT_SLA_HOURS = 8


def alerts(*, now=None) -> list[dict]:
    now = now or timezone.now()
    current = metrics.snapshot(now=now)
    result = []
    queued = current["omega_hub_checker_queued_jobs"]
    oldest_queued_age = current["omega_hub_checker_oldest_queued_age_seconds"]
    if queued and oldest_queued_age > CHECKER_STALL_SECONDS:
        result.append({
            "id": "omega_hub_checker_stalled",
            "severity": "critical",
            "message": "Hub checker has not claimed queued work for more than 10 minutes",
            "queuedJobs": int(queued),
            "oldestQueuedAgeSeconds": oldest_queued_age,
        })
    if not current["omega_hub_checker_configured"] and not conf.is_local_env():
        result.append({
            "id": "omega_hub_checker_not_configured",
            "severity": "critical",
            "message": "OMEGA_HUB_CHECKER_TOKEN is not set, so no checker can claim work and uploads are refused",
        })
    if current["omega_hub_bundle_locks_overdue"]:
        result.append({
            "id": "omega_hub_bundle_lock_renewal_overdue",
            "severity": "warning",
            "message": "Live bundles' object locks are close to lapsing or were never renewed: is the retention schedule running?",
            "bundles": int(current["omega_hub_bundle_locks_overdue"]),
        })
    oldest = Report.objects.filter(reason=Report.Reason.SECURITY, status=Report.Status.OPEN).order_by("created_at").values_list("created_at", flat=True).first()
    if oldest is not None:
        calendar = support_hours.current()
        open_hours = support_hours.business_hours(oldest, now, calendar)
        if open_hours >= SECURITY_REPORT_SLA_HOURS:
            result.append({
                "id": "omega_hub_security_report_sla",
                "severity": "critical",
                "message": "An open security report has exceeded the eight business hour SLA",
                "businessHoursOpen": open_hours,
                "supportCalendar": f"{calendar.opens:%H:%M}-{calendar.closes:%H:%M} {calendar.zone.key}, Monday to Friday",
            })
    return result


def page(alert_list: list[dict]) -> int:
    """Queue each newly observed alert once per hour for the Hub maintainers."""
    sent = 0
    for alert in alert_list:
        key = f"omega_hub:alert-paged:{alert['id']}"
        inflight = f"{key}:inflight"
        if cache.get(key) or not cache.add(inflight, True, timeout=60):
            continue
        try:
            if notify.ops_alert(alert):
                cache.set(key, True, timeout=3600)
                sent += 1
        finally:
            cache.delete(inflight)
    return sent


def storage_alert(result: dict) -> dict | None:
    if result.get("ready"):
        return None
    return {
        "id": "omega_hub_storage_unprovisioned",
        "severity": "critical",
        "message": "Hub object storage is not ready, so uploads and approvals answer 503",
        "bucket": result.get("bucket"),
        "problems": result.get("problems", []),
    }


def check_and_page(*, now=None) -> dict:
    """Reap abandoned leases and evaluate the database alerts first, so a slow or unreachable
    object store (bounded by the reconcile timeout) can never delay a checker-stall page."""
    now = now or timezone.now()
    reaped = checks.reap_expired_leases(now=now)
    found = alerts(now=now)
    storage = storage_provision.reconcile()
    storage_provision.remember(storage)
    if (alert := storage_alert(storage)) is not None:
        found.append(alert)
    return {"reaped": reaped, "alerts": found, "storage": storage, "paged": page(found)}
