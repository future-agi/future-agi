"""Retention for the gateway's request records.

An org's active config sets ``privacy.retention``: ``request_log_days`` for
request logs and outcomes, and ``attempt_days`` (default: the same) for
attempts. A TTL that is unset, null or not a positive whole number keeps rows
forever. Attempts are purged by their own age, because their log FK is
SET_NULL and a log can go first. Managed-credit ledger entries are never
touched: invoices are rebuilt from them.
"""

from dataclasses import dataclass
from datetime import timedelta

import structlog
from django.db.models import Q
from django.utils import timezone

from accounts.models.audit_log import AuditScope
from agentcc.models import (
    AgentccRequestAttempt,
    AgentccRequestLog,
    AgentccRequestOutcome,
)
from agentcc.models.org_config import AgentccOrgConfig
from tfc.utils.audit import log_audit

logger = structlog.get_logger(__name__)

PURGE_BATCH_SIZE = 5000
AUDIT_ACTION_RETENTION_PURGE = "agentcc.retention_purge"


class RetentionPurgeError(Exception):
    """The purge failed for at least one org; every other org was purged."""


@dataclass(frozen=True)
class RetentionPolicy:
    request_log_days: int | None
    attempt_days: int | None

    @property
    def is_set(self):
        return self.request_log_days is not None or self.attempt_days is not None


def _days(value):
    """A TTL in whole days, or None for anything that is not a positive whole
    number. JSON Schema's ``integer`` accepts 30.0, so a whole float counts."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def retention_policy(privacy):
    """The retention TTLs of an org config's ``privacy`` section."""
    retention = privacy.get("retention") if isinstance(privacy, dict) else None
    if not isinstance(retention, dict):
        return RetentionPolicy(None, None)
    log_days = _days(retention.get("request_log_days"))
    attempt_days = _days(retention.get("attempt_days"))
    return RetentionPolicy(log_days, attempt_days or log_days)


def purge_expired_request_logs(*, now=None, batch_size=PURGE_BATCH_SIZE):
    """Delete every org's request records older than its retention TTLs.

    Returns the rows deleted per org that has a TTL, as
    ``{org_id: {"request_logs": n, "outcomes": n, "attempts": n}}``, and writes
    each org's counts to the audit log when anything was deleted. An org whose
    purge fails is logged and skipped, so it cannot hold back the others, and
    RetentionPurgeError is raised at the end naming it: the scheduled activity
    fails and is retried, and a retry deletes only what is still due.
    """
    now = now or timezone.now()
    results = {}
    failed = []
    configs = AgentccOrgConfig.no_workspace_objects.filter(
        is_active=True
    ).select_related("organization")
    for config in configs:
        policy = retention_policy(config.privacy)
        if not policy.is_set:
            continue
        org = config.organization
        try:
            counts = _purge_org(org, policy, now, batch_size)
        except Exception:
            logger.exception("agentcc_retention_purge_failed", org_id=str(org.id))
            failed.append(str(org.id))
            continue
        results[str(org.id)] = counts
        if any(counts.values()):
            log_audit(
                organization=org,
                action=AUDIT_ACTION_RETENTION_PURGE,
                scope=AuditScope.ORGANIZATION,
                target_id=org.id,
                changes={"deleted": counts},
                metadata={
                    "request_log_days": policy.request_log_days,
                    "attempt_days": policy.attempt_days,
                },
            )
        logger.info("agentcc_retention_purged", org_id=str(org.id), **counts)
    if failed:
        raise RetentionPurgeError(
            f"retention purge failed for {len(failed)} org(s): {', '.join(failed)}"
        )
    return results


def _purge_org(org, policy, now, batch_size):
    counts = {"request_logs": 0, "outcomes": 0, "attempts": 0}
    if policy.request_log_days is not None:
        cutoff = now - timedelta(days=policy.request_log_days)
        # A log's age is its request's start; a row without one goes by insert.
        counts["request_logs"] = _purge(
            AgentccRequestLog,
            Q(organization=org)
            & (
                Q(started_at__lt=cutoff)
                | Q(started_at__isnull=True, created_at__lt=cutoff)
            ),
            batch_size,
        )
        counts["outcomes"] = _purge(
            AgentccRequestOutcome,
            Q(organization=org, occurred_at__lt=cutoff),
            batch_size,
        )
    if policy.attempt_days is not None:
        cutoff = now - timedelta(days=policy.attempt_days)
        counts["attempts"] = _purge(
            AgentccRequestAttempt,
            Q(organization=org, created_at__lt=cutoff),
            batch_size,
        )
    return counts


def _purge(model, condition, batch_size):
    """Delete the rows matching condition, batch_size primary keys at a time.

    The base manager sees soft-deleted rows too, and the default ordering is
    dropped so each batch is an index range read, not a sort.
    """
    deleted = 0
    rows = model._base_manager.filter(condition).order_by()
    while True:
        pks = list(rows.values_list("pk", flat=True)[:batch_size])
        if not pks:
            return deleted
        deleted += _delete_batch(model, pks)
        if len(pks) < batch_size:
            return deleted


def _delete_batch(model, pks):
    """Delete one batch of model rows; returns how many of them went.

    A request log has related rows (attempts, guardrail feedback), so Django
    loads the batch before deleting it; only("pk") keeps its bodies unread.
    """
    _, per_model = model._base_manager.filter(pk__in=pks).only("pk").delete()
    return per_model.get(model._meta.label, 0)
