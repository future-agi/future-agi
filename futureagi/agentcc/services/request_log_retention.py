"""Request Logs retention: delete each organization's old gateway request logs.

An organization admin chooses how many days of Request Logs to keep; no
choice means keep forever. Once a day (03:00 UTC) a schedule deletes the rows
past each organization's retention.

Budgets read request logs: on start the gateway seeds its budget counters from
the spend summary, the cost of the rows in each budget's current period. So a
retention is never shorter than the longest budget period in use, and every
run also keeps the current period of every budget, whatever the retention
says. A ``total`` budget needs every row, so its organization keeps them all.

The purge deletes in short batches, each its own transaction with a lock
timeout. A batch locks only the rows it deletes and skips rows another
transaction holds, so it never blocks the inserts of log ingest. Soft-deleted
rows past the cutoff go too, and so does a deleted row's guardrail feedback.
"""

import time
from datetime import UTC, datetime, timedelta

import structlog
from django.db import DatabaseError, connections, transaction
from django.utils import timezone

from agentcc.models import AgentccOrgConfig, AgentccRequestLogRetention
from agentcc.services.budget_periods import (
    BUDGET_PERIODS,
    DEFAULT_BUDGET_PERIOD,
    period_start,
)
from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.agentcc_request_logs import (
    REQUEST_LOG_RETENTION_HOUR_UTC,
)

logger = structlog.get_logger(__name__)

# The days a budget period can reach back. "total" needs every row.
PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 31}
FOREVER = "forever"

BATCH_SIZE = 5000
SLEEP_BETWEEN_BATCHES = 0.05
LOCK_TIMEOUT = "2s"
STATEMENT_TIMEOUT = "60s"
# One organization's share of a run; the rest waits for the next run.
ORG_TIME_BUDGET_SECONDS = 600
# The whole run, across organizations.
RUN_TIME_BUDGET_SECONDS = 3000
ESTIMATE_CAP = 1_000_000

# Request logs have no replica routing; everything here is on the primary.
DB_ALIAS = "default"

_PURGE_SQL = {
    "started_at": """
        WITH doomed AS (
            SELECT id FROM agentcc_request_log
            WHERE organization_id = %s AND started_at < %s
            ORDER BY started_at
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        ), feedback AS (
            DELETE FROM agentcc_guardrail_feedback
            WHERE request_log_id IN (SELECT id FROM doomed)
        )
        DELETE FROM agentcc_request_log WHERE id IN (SELECT id FROM doomed)
    """,
    # Ingest leaves started_at empty when it cannot parse the timestamp.
    "created_at": """
        WITH doomed AS (
            SELECT id FROM agentcc_request_log
            WHERE organization_id = %s AND started_at IS NULL AND created_at < %s
            ORDER BY created_at
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        ), feedback AS (
            DELETE FROM agentcc_guardrail_feedback
            WHERE request_log_id IN (SELECT id FROM doomed)
        )
        DELETE FROM agentcc_request_log WHERE id IN (SELECT id FROM doomed)
    """,
}


def budget_periods(budgets):
    """The periods of every budget with a positive limit in ``budgets``.

    A budget without a period of its own takes ``default_period``, then the
    gateway's default, monthly. ``enabled: false`` turns them all off.
    """
    if not isinstance(budgets, dict) or budgets.get("enabled") is False:
        return set()
    default = budgets.get("default_period") or budgets.get("defaultPeriod")
    if default not in BUDGET_PERIODS:
        default = DEFAULT_BUDGET_PERIOD
    org_period = budgets.get("org_period") or budgets.get("orgPeriod")
    periods = set()

    def add(limit, period):
        try:
            if limit is None or isinstance(limit, bool) or float(limit) <= 0:
                return
        except (TypeError, ValueError):
            return
        periods.add(period if period in BUDGET_PERIODS else default)

    org_limit = budgets.get("org_limit")
    if isinstance(org_limit, dict):
        add(org_limit.get("limit"), org_limit.get("period") or org_period)
    else:
        add(org_limit, org_period)
    hard = budgets.get("hard_limit")
    if isinstance(hard, dict):
        add(hard.get("limit"), hard.get("period") or org_period)
    for key in ("organization", "org"):
        entry = budgets.get(key)
        if isinstance(entry, dict):
            add(entry.get("limit"), entry.get("period") or org_period)
    for level in ("teams", "users", "keys", "tags"):
        entries = budgets.get(level)
        if not isinstance(entries, dict):
            continue
        for entry in entries.values():
            if not isinstance(entry, dict):
                continue
            add(entry.get("limit"), entry.get("period"))
            per_model = entry.get("per_model")
            if isinstance(per_model, dict):
                for limit in per_model.values():
                    add(limit, entry.get("period"))
    return periods


def budget_retention_floor(budgets):
    """The fewest days a retention may keep for these budgets.

    None when no budget applies, FOREVER when one is a total budget.
    """
    periods = budget_periods(budgets)
    if "total" in periods:
        return FOREVER
    if not periods:
        return None
    return max(PERIOD_DAYS[p] for p in periods)


def longest_period(budgets):
    """The budget period behind the floor, for messages."""
    periods = budget_periods(budgets)
    for period in ("total", "monthly", "weekly", "daily"):
        if period in periods:
            return period
    return None


def org_budgets(organization_id):
    """The budgets of the organization's active config, or {}."""
    config = (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization_id=organization_id, is_active=True
        )
        .only("budgets")
        .first()
    )
    return (config.budgets if config else None) or {}


def effective_cutoff(retention_days, budgets, now=None):
    """The time before which rows are deleted, or None to delete nothing.

    The retention's own cutoff, moved back to the start of the current period
    of every budget in use, so no budget loses a row it counts.
    """
    if not retention_days:
        return None
    periods = budget_periods(budgets)
    if "total" in periods:
        return None
    now = now or timezone.now()
    cutoff = now - timedelta(days=retention_days)
    for period in periods:
        cutoff = min(cutoff, period_start(period, now))
    return cutoff


def next_run_at(now=None):
    """When the retention schedule next fires."""
    now = now or datetime.now(UTC)
    run = now.replace(
        hour=REQUEST_LOG_RETENTION_HOUR_UTC, minute=0, second=0, microsecond=0
    )
    return run if run > now else run + timedelta(days=1)


def estimate_rows(organization_id, cutoff, cap=ESTIMATE_CAP):
    """How many rows a purge at ``cutoff`` would delete, counting at most cap."""
    if cutoff is None:
        return {"count": 0, "capped": False}
    with connections[DB_ALIAS].cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM (
                SELECT 1 FROM agentcc_request_log
                WHERE organization_id = %s AND started_at < %s
                LIMIT %s
            ) dated
            """,
            [organization_id, cutoff, cap + 1],
        )
        dated = cursor.fetchone()[0]
        undated = 0
        if dated <= cap:
            cursor.execute(
                """
                SELECT count(*) FROM (
                    SELECT 1 FROM agentcc_request_log
                    WHERE organization_id = %s AND started_at IS NULL
                      AND created_at < %s
                    LIMIT %s
                ) undated
                """,
                [organization_id, cutoff, cap + 1 - dated],
            )
            undated = cursor.fetchone()[0]
    count = dated + undated
    return {"count": min(count, cap), "capped": count > cap}


def purge_org_request_logs(
    organization_id,
    cutoff,
    *,
    batch_size=BATCH_SIZE,
    time_budget_s=ORG_TIME_BUDGET_SECONDS,
    sleep_s=SLEEP_BETWEEN_BATCHES,
):
    """Delete the organization's request logs from before ``cutoff``.

    Live and soft-deleted rows alike, with their guardrail feedback. Returns
    the rows deleted and whether every row past the cutoff is gone; when the
    time budget runs out first, the next run carries on.
    """
    deleted = 0
    budget_ends = time.monotonic() + time_budget_s
    for column, sql in _PURGE_SQL.items():
        while True:
            if time.monotonic() >= budget_ends:
                logger.info(
                    "request_log_retention_budget_spent",
                    organization_id=str(organization_id),
                    deleted=deleted,
                    pass_=column,
                )
                return {"deleted": deleted, "complete": False, "error": ""}
            try:
                with transaction.atomic(using=DB_ALIAS):
                    with connections[DB_ALIAS].cursor() as cursor:
                        cursor.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                        cursor.execute(
                            f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"
                        )
                        cursor.execute(sql, [organization_id, cutoff, batch_size])
                        count = cursor.rowcount
            except DatabaseError as exc:
                # A lock or statement timeout, most likely. The batches before
                # this one are committed and counted; the next run goes on.
                error = str(exc)[:500]
                logger.warning(
                    "request_log_retention_failed",
                    organization_id=str(organization_id),
                    deleted=deleted,
                    error=error,
                )
                return {"deleted": deleted, "complete": False, "error": error}
            deleted += count
            if count < batch_size:
                break
            if sleep_s:
                time.sleep(sleep_s)
    return {"deleted": deleted, "complete": True, "error": ""}


def run_retention(organization_id=None, *, dry_run=False, now=None):
    """Purge every organization with a retention, or just one.

    Records each run on the organization's retention row. Returns one result
    per organization considered.
    """
    now = now or timezone.now()
    run_ends = time.monotonic() + RUN_TIME_BUDGET_SECONDS
    rows = AgentccRequestLogRetention.no_workspace_objects.filter(
        retention_days__isnull=False
    ).order_by("last_run_finished_at", "created_at")
    if organization_id:
        rows = rows.filter(organization_id=organization_id)

    results = []
    for row in rows:
        org_id = row.organization_id
        budgets = org_budgets(org_id)
        cutoff = effective_cutoff(row.retention_days, budgets, now=now)
        result = {
            "organization_id": str(org_id),
            "retention_days": row.retention_days,
            "cutoff": cutoff.isoformat() if cutoff else None,
        }
        if cutoff is None:
            logger.info(
                "request_log_retention_skipped_total_budget",
                organization_id=str(org_id),
            )
            results.append({**result, "skipped": "total budget", "deleted": 0})
            continue
        if dry_run:
            estimate = estimate_rows(org_id, cutoff)
            results.append(
                {
                    **result,
                    "would_delete": estimate["count"],
                    "capped": estimate["capped"],
                }
            )
            continue
        remaining = run_ends - time.monotonic()
        if remaining <= 0:
            results.append({**result, "skipped": "run time budget", "deleted": 0})
            continue

        AgentccRequestLogRetention.all_objects.filter(pk=row.pk).update(
            last_run_started_at=timezone.now()
        )
        purged = purge_org_request_logs(
            org_id,
            cutoff,
            time_budget_s=min(ORG_TIME_BUDGET_SECONDS, remaining),
        )
        AgentccRequestLogRetention.all_objects.filter(pk=row.pk).update(
            last_run_finished_at=timezone.now(),
            last_run_deleted_rows=purged["deleted"],
            last_run_error=purged["error"],
        )
        logger.info(
            "request_log_retention_run",
            organization_id=str(org_id),
            cutoff=result["cutoff"],
            deleted=purged["deleted"],
            complete=purged["complete"],
        )
        results.append({**result, **purged})
    return results


@temporal_activity(max_retries=0, time_limit=3600, queue="default")
def purge_expired_agentcc_request_logs():
    """The daily retention run. A run cut short resumes the next day."""
    results = run_retention()
    return {
        "organizations": len(results),
        "deleted": sum(r.get("deleted", 0) for r in results),
    }
