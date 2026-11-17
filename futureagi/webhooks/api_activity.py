"""``GET /webhooks/endpoints/{id}/activity/``: the endpoint page's Activity card (DESIGN.md section 11.2, "this
endpoint's audit entries"; WH-3 F3).

The entries are the ``AuditLog`` rows of scope ``webhooks`` that ``webhooks.audit`` wrote about this endpoint: those
whose target is the endpoint, and the one-delivery redeliveries (their target is the delivery; the endpoint is in
``metadata.endpoint_id``). Newest first, cursor-paged on ``(created_at, id)``.

Who sees what (section 9.1): the route needs ``VIEW`` on the endpoint (``require_endpoint``: another tenant's or an
invisible endpoint answers 404). The entries' ``changes`` and ``metadata`` carry ``url_display``, header names and
secret tails, which only those who see the endpoint's URL may read (``VIEW_URL``, as in ``api_represent``). Everyone
else gets the action, the time, who did it, the names of the fields that changed, and the metadata keys in
``PUBLIC_METADATA``. The keys that come from the delivery log (``LOG_METADATA``: which delivery and event, its attempt,
status code and outcome) also need ``VIEW_LOG`` on the endpoint's scope: an org-wide endpoint's delivery log is for Org
Admins only (section 9.1), so a workspace Member or Admin reading an org-wide endpoint's Activity sees that a test or a
redelivery happened, not what it delivered. Nothing here was ever a full URL, a secret or a header
value (section 9.2).
"""

from __future__ import annotations

from django.db.models import Q

from accounts.models.audit_log import AuditLog
from webhooks import audit
from webhooks.api_errors import WebhookApiError
from webhooks.permissions import VIEW_LOG, VIEW_URL

DEFAULT_LIMIT = 50

# What anyone who may view the endpoint may read of an entry's metadata: why it happened and how much, never where
# it points, which headers it sends or which secret signs.
PUBLIC_METADATA = (
    "reason",
    "rule",
    "previous_reason",
    "mode",
    "count",
    "queued",
    "expired",
    "bulk",
    "backlog_capped",
    "event_type",
    "event_id",
    "delivery_id",
    "attempt",
    "status_code",
    "outcome",
    "overlap_seconds",
    "expires_at",
    "enabled",
)

# The PUBLIC_METADATA keys that are delivery-log data: shown only with VIEW_LOG on the endpoint's scope.
LOG_METADATA = ("delivery_id", "event_id", "attempt", "status_code", "outcome")


def _scope(caller, ep):
    return AuditLog.objects.filter(
        organization_id=caller.organization_id, scope=audit.SCOPE
    ).filter(
        Q(target_id=audit.target_uuid(ep.id))
        | Q(action=audit.DELIVERY_REDELIVERED, metadata__endpoint_id=ep.id)
    )


def _actor(row) -> dict | None:
    user = row.actor
    if user is None:
        return None
    name = (getattr(user, "name", "") or "").strip() or user.email
    return {"id": str(user.id), "name": name}


def represent(row, *, full: bool, log: bool = True) -> dict:
    raw = row.metadata or {}
    meta = {k: v for k, v in raw.items() if k != "target"}
    if row.action == audit.DELIVERY_REDELIVERED and not raw.get("bulk"):
        meta.setdefault(
            "delivery_id", raw.get("target")
        )  # a one-delivery redelivery targets the delivery
    changes = row.changes or {}
    return {
        "id": str(row.id),
        "action": row.action,
        "at": row.created_at,
        "actor": _actor(row),
        "system": row.actor_id is None,
        # The field names of a diff; a created or deleted entry's snapshot ({"after": ...}) names none.
        "changed": sorted(k for k in changes if k not in ("before", "after")),
        "changes": changes if full else None,
        "metadata": meta
        if full
        else {
            k: meta[k]
            for k in PUBLIC_METADATA
            if k in meta and (log or k not in LOG_METADATA)
        },
    }


def list_activity(caller, ep, params: dict) -> dict:
    qs = _scope(caller, ep).select_related("actor")
    cursor = params.get("cursor")
    if cursor:
        at = qs.filter(id=cursor).values_list("created_at", flat=True).first()
        if at is None:
            raise WebhookApiError(
                "WEBHOOK_FILTER_INVALID",
                "cursor is not an entry of this endpoint's activity.",
                attr="cursor",
            )
        qs = qs.filter(Q(created_at__lt=at) | Q(created_at=at, id__lt=cursor))
    limit = params.get("limit") or DEFAULT_LIMIT
    rows = list(qs.order_by("-created_at", "-id")[: limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    full = caller.can(VIEW_URL, ep.workspace_id)
    log = caller.can(VIEW_LOG, ep.workspace_id)
    return {
        "results": [represent(row, full=full, log=log) for row in rows],
        "next_cursor": str(rows[-1].id) if more and rows else None,
    }
