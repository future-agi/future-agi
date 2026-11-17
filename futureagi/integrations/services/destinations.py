"""The one destination registry (webhooks DESIGN.md section 11.1; owner decision 5).

A read service: it returns one uniform list, ``{kind, id, name, health, used_by[], last_used_at}``, over the connected
apps (``IntegrationConnection``) and the webhook endpoints (``webhooks_endpoint``), so Notify rules, Alerts, Error Feed
and, after WH-6, the gateway pick a destination from one place. It writes nothing and does not change
``IntegrationConnection``'s sync semantics.

``health.state`` is one of ``healthy``, ``failing``, ``slow``, ``off``, ``blocked`` or ``unknown``; ``health.reason``
keeps the source's own reason (a connection's status, an endpoint's ``disabled_reason``), so a caller can word it.

``webhooks_availability()`` says whether this install may show the Webhooks section at all: the section is absent,
never "coming soon", while the dispatcher may not send here (``FI_WEBHOOKS_ENABLED=false``, or the hosted gates of
DESIGN.md sections 6.2 and 6.4), while ``WEBHOOKS_ENCRYPTION_KEYS`` is missing (the K2 key gate), and while section
18's release gate holds (``webhooks.release_gate``: a gate row of section 17 has no live receipt yet).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from integrations.models import ConnectionStatus, IntegrationConnection
from tracer.models.feed_alert import ErrorFeedAlertRule

HEALTH_STATES = ("healthy", "failing", "slow", "off", "blocked", "unknown")

_CONNECTION_HEALTH = {
    ConnectionStatus.ACTIVE: "healthy",
    ConnectionStatus.SYNCING: "healthy",
    ConnectionStatus.BACKFILLING: "healthy",
    ConnectionStatus.PAUSED: "off",
    ConnectionStatus.ERROR: "failing",
}

_ENDPOINT_OFF = {"disabled_manual": "off", "disabled_auto": "off"}


def webhooks_availability() -> dict:
    """``{available, reasons}``; ``reasons`` are codes, never configuration values."""
    from webhooks import config, release_gate
    from webhooks.api_errors import WebhookApiError
    from webhooks.api_validation import require_keys

    reasons = []
    if not config.sending_enabled():
        reasons.append("sending_off")
    elif config.send_blockers():
        reasons.append("hosted_gates")
    try:
        require_keys()
    except WebhookApiError:
        reasons.append("keys_missing")
    if release_gate.section_held():
        reasons.append("release_gate")
    return {"available": not reasons, "reasons": reasons}


def _connection_scope(organization_id, workspace_id) -> Q | None:
    """The connections a workspace sees, by the rule the connections API applies (``BaseModelManager`` and
    ``BaseModelViewSetMixinWithUserOrg``): its own, plus, in the org's default workspace, those of any default
    workspace and those with no workspace. So the registry never misses a connection the Integrations page lists."""
    if workspace_id is None:
        return None
    from accounts.models.workspace import Workspace

    is_default = Workspace.no_workspace_objects.filter(
        id=workspace_id, organization_id=organization_id, is_default=True
    ).exists()
    if not is_default:
        return Q(workspace_id=workspace_id)
    return (
        Q(workspace_id=workspace_id)
        | Q(workspace__is_default=True, workspace__organization_id=organization_id)
        | Q(workspace__isnull=True)
    )


def _connection_rows(organization_id, workspace_id) -> list[dict]:
    qs = IntegrationConnection.no_workspace_objects.filter(
        organization_id=organization_id, deleted=False
    )
    scope = _connection_scope(organization_id, workspace_id)
    if scope is not None:
        qs = qs.filter(scope)
    conns = list(qs.order_by("display_name", "id"))
    rules: dict = {}
    for r in ErrorFeedAlertRule.no_workspace_objects.filter(
        slack_connection_id__in=[c.id for c in conns], deleted=False
    ).order_by("name", "id"):
        rules.setdefault(r.slack_connection_id, []).append(
            {"kind": "error_feed_alert", "id": str(r.id), "name": r.name}
        )
    return [
        {
            "kind": c.platform,
            "id": str(c.id),
            "name": c.display_name,
            "health": {
                "state": _CONNECTION_HEALTH.get(c.status, "unknown"),
                "reason": c.status,
                "since": None,
            },
            "used_by": rules.get(c.id, []),
            "last_used_at": c.last_synced_at,
        }
        for c in conns
    ]


def _endpoint_health(ep, st) -> dict:
    if ep.status == "blocked_by_policy":
        return {"state": "blocked", "reason": "policy", "since": ep.disabled_at}
    if ep.status in _ENDPOINT_OFF:
        return {
            "state": "off",
            "reason": ep.disabled_reason or ep.status,
            "since": ep.disabled_at,
        }
    if st is not None and st.failing_since is not None:
        return {
            "state": "failing",
            "reason": st.last_outcome or None,
            "since": st.failing_since,
        }
    if st is not None and st.lane == "slow":
        return {"state": "slow", "reason": "slow_lane", "since": None}
    return {"state": "healthy", "reason": None, "since": None}


def _last_used(st) -> object:
    """When the endpoint last sent an event: the later of its last success and last failure, which the dispatcher
    keeps on the state row (tests are not counted there). One row per endpoint, never a scan of the attempts."""
    if st is None:
        return None
    seen = [t for t in (st.last_success_at, st.last_failure_at) if t is not None]
    return max(seen) if seen else None


def _endpoint_rows(organization_id, workspace_id, visible) -> list[dict]:
    from webhooks.models import Delivery, Endpoint, EndpointState

    qs = Endpoint.objects.filter(
        organization_id=organization_id, deleted=False
    ).exclude(kind="cli_listen")
    if workspace_id is not None:
        qs = qs.filter(Q(workspace_id=workspace_id) | Q(workspace__isnull=True))
    eps = [
        ep
        for ep in qs.prefetch_related("subscriptions").order_by("name", "id")
        if visible(None if ep.workspace_id is None else str(ep.workspace_id))
    ]
    ids = [ep.id for ep in eps]
    states = {
        s.endpoint_id: s for s in EndpointState.objects.filter(endpoint_id__in=ids)
    }
    since = timezone.now() - timedelta(hours=24)
    stats = {
        row["endpoint_id"]: row
        for row in Delivery.objects.filter(
            endpoint_id__in=ids, is_test=False, created_at__gte=since
        )
        .values("endpoint_id")
        .annotate(
            total=Count("id"),
            succeeded=Count("id", filter=Q(status="succeeded")),
            failed=Count("id", filter=Q(status="failed")),
        )
    }
    out = []
    for ep in eps:
        s = stats.get(ep.id, {})
        out.append(
            {
                "kind": "webhook",
                "id": ep.id,
                "name": ep.name,
                "health": _endpoint_health(ep, states.get(ep.id)),
                "used_by": [
                    {"kind": "event_type", "id": sub.event_type, "name": sub.event_type}
                    for sub in sorted(
                        ep.subscriptions.all(), key=lambda x: x.event_type
                    )
                ],
                "last_used_at": _last_used(states.get(ep.id)),
                "org_wide": ep.workspace_id is None,
                "stats_24h": {
                    "total": s.get("total", 0),
                    "succeeded": s.get("succeeded", 0),
                    "failed": s.get("failed", 0),
                },
            }
        )
    return out


def list_destinations(
    organization_id,
    workspace_id=None,
    *,
    can_view_endpoint: Callable[[str | None], bool] = lambda _ws: False,
    kinds: tuple[str, ...] | None = None,
) -> list[dict]:
    """Every destination of the org (of ``workspace_id`` and the org-wide ones, when given), connected apps first.

    Webhook endpoints are listed only where ``can_view_endpoint(workspace_id or None)`` allows (the webhooks RBAC of
    DESIGN.md section 9.1); the default lists none, so a caller must opt in with the caller's own rule.
    """
    rows = _connection_rows(organization_id, workspace_id)
    rows += _endpoint_rows(organization_id, workspace_id, can_view_endpoint)
    if kinds is not None:
        rows = [r for r in rows if r["kind"] in kinds]
    return rows
