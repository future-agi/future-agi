"""Email alert dispatcher.

Two producers record events on an alert, one sender delivers them:

- Ingestion (log_ingestion._after_ingest) records the request-level events
  error.occurred, guardrail.triggered and budget.exceeded on every active alert
  of the org that subscribes to them (``record_request_events``). It never
  sends: after its commit it starts ``dispatch_agentcc_email_alerts_activity``
  on Temporal, off the request thread.
- The every-minute evaluator (``evaluate_email_alerts``, schedule
  ``agentcc-alerts-evaluator``) computes error.rate_spike, latency.spike and
  cost.threshold from the org's own request logs against the alert's
  thresholds, then sends whatever is pending.

``dispatch_pending`` sends an alert's pending events as one email through its
stored provider (SMTP, SendGrid, Resend), at most once per cooldown_minutes:
events that arrive inside the cooldown stay pending and go out together in the
next email. last_triggered_at is the last real send. A provider failure is
logged with the alert id, recorded in last_error, and the events stay pending
for the next tick; nothing here is ever raised into ingestion.

State lives on the alert row (migration 0048_email_alert_state):
``dispatch_state = {"pending": {event: {count, first_at, last_at, context}},
"budget_keys": {key: expires_at}}``. budget.exceeded is recorded once per
budget key per alert (the refusal's level and period start, else
its error code and UTC day; log_ingestion._budget_dedupe_key). A key is kept
until its period is over (claim_budget_key), so a refusal storm never sends a
second email for one period. State writes use queryset updates: they never
change the alert's updated_at.
"""

import math
from datetime import datetime, timedelta
from decimal import Decimal

import structlog
from django.db import connection, transaction
from django.db.models import Aggregate, Count, DecimalField, FloatField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from agentcc.models.email_alert import AgentccEmailAlert

logger = structlog.get_logger(__name__)

REQUEST_EVENTS = ("error.occurred", "guardrail.triggered", "budget.exceeded")
THRESHOLD_EVENTS = ("error.rate_spike", "latency.spike", "cost.threshold")

# The thresholds an alert is evaluated against when it stores none (the console
# shows these values, EmailAlertDialog.jsx THRESHOLD_DEFAULTS).
DEFAULT_THRESHOLDS = {
    "error_rate_percent": 5,
    "latency_ms": 5000,
    "cost_usd": 10,
    "window_minutes": 5,
    "min_requests": 5,
}
MAX_WINDOW_MINUTES = 1440

DISPATCH_ACTIVITY = "dispatch_agentcc_email_alerts_activity"
# The ingestion thread waits at most this long for Temporal to accept the start.
START_TIMEOUT_SECONDS = 2
# budget.exceeded keys a subscriber remembers; past this the keys whose period
# ends first are dropped (a key is pruned anyway once its period is over).
MAX_BUDGET_KEYS = 2000
# A key is kept this long past the end of its period (late log deliveries).
BUDGET_KEY_GRACE = timedelta(days=1)
MAX_ERROR_LENGTH = 1000
MAX_CONTEXT_VALUE_LENGTH = 500


# --- thresholds -----------------------------------------------------------------


def threshold_error(key, value):
    """Why ``value`` is not a valid ``key`` threshold, or None when it is."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return f"{key} must be a number."
    if not math.isfinite(value):
        return f"{key} must be a finite number."
    whole = float(value).is_integer()
    if key == "error_rate_percent" and not 0 < value <= 100:
        return "error_rate_percent must be above 0 and at most 100."
    if key == "latency_ms" and not (whole and value >= 1):
        return "latency_ms must be a whole number of milliseconds, at least 1."
    if key == "cost_usd" and not value > 0:
        return "cost_usd must be above 0."
    if key == "window_minutes" and not (whole and 1 <= value <= MAX_WINDOW_MINUTES):
        return f"window_minutes must be a whole number from 1 to {MAX_WINDOW_MINUTES}."
    if key == "min_requests" and not (whole and value >= 1):
        return "min_requests must be a whole number, at least 1."
    return None


def threshold_errors(thresholds):
    """Errors for the documented keys; other keys are stored and never read."""
    return [
        error
        for key in DEFAULT_THRESHOLDS
        if key in thresholds
        and (error := threshold_error(key, thresholds[key])) is not None
    ]


def effective_thresholds(alert):
    """The alert's thresholds over the defaults (an invalid stored value is ignored)."""
    stored = alert.thresholds if isinstance(alert.thresholds, dict) else {}
    effective = dict(DEFAULT_THRESHOLDS)
    for key in DEFAULT_THRESHOLDS:
        if key in stored and threshold_error(key, stored[key]) is None:
            effective[key] = stored[key]
    return effective


# --- budget.exceeded keys ---------------------------------------------------------


def _expiry(value):
    """A stored expires_at as a datetime; None (never expires) when absent."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else timezone.make_aware(parsed, timezone.utc)


def claim_budget_key(keys, key, expires_at, now):
    """Record ``key`` in a subscriber's budget keys unless it is already there.

    ``keys`` maps key -> expires_at (ISO 8601, or None for a period that never
    ends). Returns (new keys, claimed): claimed is False when the key was
    already recorded, i.e. this budget level and period were already notified.
    Keys whose period ended more than BUDGET_KEY_GRACE ago are dropped.
    """
    kept = {}
    for stored, expires in (keys if isinstance(keys, dict) else {}).items():
        ends = _expiry(expires)
        if ends is None or ends + BUDGET_KEY_GRACE >= now:
            kept[stored] = expires
    if key in kept:
        return kept, False
    kept[key] = expires_at.isoformat() if expires_at else None
    if len(kept) > MAX_BUDGET_KEYS:
        far = now + timedelta(days=36500)
        newest = sorted(kept, key=lambda k: _expiry(kept[k]) or far)[-MAX_BUDGET_KEYS:]
        kept = {k: kept[k] for k in newest}
    return kept, True


# --- recording ------------------------------------------------------------------


def _state(alert):
    state = alert.dispatch_state if isinstance(alert.dispatch_state, dict) else {}
    pending = state.get("pending")
    keys = state.get("budget_keys")
    return {
        "pending": dict(pending) if isinstance(pending, dict) else {},
        "budget_keys": dict(keys) if isinstance(keys, dict) else {},
    }


def _write(alert_id, **fields):
    """Write dispatcher fields without touching the alert's updated_at."""
    AgentccEmailAlert.no_workspace_objects.filter(id=alert_id).update(**fields)


def _clip(value):
    text = value if isinstance(value, str) else str(value)
    if len(text) > MAX_CONTEXT_VALUE_LENGTH:
        return text[:MAX_CONTEXT_VALUE_LENGTH] + "..."
    return text


def _merge(pending, event_type, count, first_at, last_at, context):
    """Fold ``count`` occurrences into pending[event_type]; the latest context wins."""
    current = pending.get(event_type)
    if not isinstance(current, dict):
        pending[event_type] = {
            "count": count,
            "first_at": first_at,
            "last_at": last_at,
            "context": context,
        }
        return
    newer = last_at >= (current.get("last_at") or "")
    pending[event_type] = {
        "count": int(current.get("count") or 0) + count,
        "first_at": min(first_at, current.get("first_at") or first_at),
        "last_at": max(last_at, current.get("last_at") or last_at),
        "context": context if newer else current.get("context") or context,
    }


def _record(alert_ids, events):
    """Merge events into the pending state of each alert that subscribes to them.

    ``events``: dicts with event, context, at (ISO 8601, UTC) and, for
    budget.exceeded, budget_key and budget_key_expires (a datetime or None).
    Returns the ids of the alerts that changed.
    """
    changed = []
    now = timezone.now()
    with transaction.atomic():
        alerts = (
            AgentccEmailAlert.no_workspace_objects.select_for_update()
            .filter(id__in=alert_ids, is_active=True)
            .order_by("id")
        )
        for alert in alerts:
            subscribed = set(alert.events or [])
            state = _state(alert)
            touched = False
            for event in events:
                if event["event"] not in subscribed:
                    continue
                key = event.get("budget_key")
                if key:
                    state["budget_keys"], claimed = claim_budget_key(
                        state["budget_keys"], key, event.get("budget_key_expires"), now
                    )
                    if not claimed:
                        continue
                context = {k: _clip(v) for k, v in event["context"].items()}
                _merge(
                    state["pending"],
                    event["event"],
                    1,
                    event["at"],
                    event["at"],
                    context,
                )
                touched = True
            if touched:
                _write(alert.id, dispatch_state=state)
                changed.append(alert.id)
    return changed


def record_request_events(org, events):
    """Record request-level events for the org's alerts and start a send.

    Called by ingestion after its rows are committed. Best effort: a failure is
    logged, never raised. The send runs on Temporal; when the start fails, the
    every-minute evaluator sends the pending events instead.
    """
    events = [e for e in events if e.get("event") in REQUEST_EVENTS]
    if not events:
        return 0
    wanted = {e["event"] for e in events}
    try:
        alert_ids = [
            alert_id
            for alert_id, subscribed in AgentccEmailAlert.no_workspace_objects.filter(
                organization=org, is_active=True
            ).values_list("id", "events")
            if wanted & set(subscribed or [])
        ]
        if not alert_ids:
            return 0
        changed = _record(alert_ids, events)
    except Exception:
        logger.exception("email_alert_record_failed", org_id=str(org.id))
        return 0
    if changed:
        org_id = str(org.id)
        transaction.on_commit(lambda: start_dispatch(org_id))
    return len(changed)


def start_dispatch(org_id):
    """Start the org's send on Temporal; on failure the next tick sends."""
    try:
        from tfc.temporal.drop_in import runner

        runner.start_activity(
            DISPATCH_ACTIVITY,
            args=(str(org_id),),
            queue="default",
            dispatch_timeout_seconds=START_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "email_alert_dispatch_start_failed", org_id=str(org_id), error=str(exc)
        )


# --- sending --------------------------------------------------------------------


def _cooling(alert, now):
    if alert.cooldown_minutes <= 0 or alert.last_triggered_at is None:
        return False
    return now < alert.last_triggered_at + timedelta(minutes=alert.cooldown_minutes)


def _claim(alert_id, now):
    """Take an alert's pending events for one send, or None.

    The claim empties pending and stamps last_triggered_at in one short
    transaction, so two senders never send the same events and no lock is held
    while the provider is called. A failed send gives both back (_release).
    """
    with transaction.atomic():
        alert = (
            AgentccEmailAlert.no_workspace_objects.select_for_update(skip_locked=True)
            .filter(id=alert_id, is_active=True)
            .first()
        )
        if alert is None:
            return None
        state = _state(alert)
        if not state["pending"] or _cooling(alert, now):
            return None
        pending = state["pending"]
        previous = alert.last_triggered_at
        state["pending"] = {}
        alert.dispatch_state = state
        alert.last_triggered_at = now
        _write(alert.id, dispatch_state=state, last_triggered_at=now)
    return alert, pending, previous


def _release(alert, pending, previous, now, error):
    """Give a failed send's events back for the next tick and record the error."""
    with transaction.atomic():
        current = (
            AgentccEmailAlert.no_workspace_objects.select_for_update()
            .filter(id=alert.id)
            .first()
        )
        if current is None:
            return
        state = _state(current)
        for event_type, item in pending.items():
            _merge(
                state["pending"],
                event_type,
                int(item.get("count") or 1),
                item.get("first_at") or "",
                item.get("last_at") or "",
                item.get("context") or {},
            )
        _write(
            current.id,
            dispatch_state=state,
            last_triggered_at=(
                previous
                if current.last_triggered_at == now
                else current.last_triggered_at
            ),
            last_error=(error or "Unknown error")[:MAX_ERROR_LENGTH],
            last_error_at=now,
        )


def _send_one(alert_id, now):
    """Send one alert's pending events; 'sent', 'failed' or None (nothing to send)."""
    from agentcc.services.email_sender import send_alert_digest

    claimed = _claim(alert_id, now)
    if claimed is None:
        return None
    alert, pending, previous = claimed
    try:
        result = send_alert_digest(alert, pending)
    except Exception as exc:  # never raised past the dispatcher
        result = {"success": False, "error": str(exc)}
    if result.get("success"):
        if alert.last_error or alert.last_error_at:
            _write(alert.id, last_error="", last_error_at=None)
        logger.info(
            "email_alert_sent",
            alert_id=str(alert.id),
            org_id=str(alert.organization_id),
            provider=alert.provider,
            events=sorted(pending),
        )
        return "sent"
    # Kept for the next tick: _release gives the events back.
    logger.warning(
        "email_alert_send_failed",
        alert_id=str(alert.id),
        org_id=str(alert.organization_id),
        provider=alert.provider,
        events=sorted(pending),
        error=result.get("error"),
    )
    _release(alert, pending, previous, now, result.get("error"))
    return "failed"


def dispatch_pending(org_id=None, now=None):
    """Send every active alert's pending events (one org's, or all orgs')."""
    now = now or timezone.now()
    alerts = AgentccEmailAlert.no_workspace_objects.filter(is_active=True)
    if org_id:
        alerts = alerts.filter(organization_id=org_id)
    totals = {"sent": 0, "failed": 0}
    for alert_id, state in alerts.values_list("id", "dispatch_state"):
        if not (isinstance(state, dict) and state.get("pending")):
            continue
        try:
            outcome = _send_one(alert_id, now)
        except Exception:
            logger.exception("email_alert_dispatch_failed", alert_id=str(alert_id))
            outcome = "failed"
        if outcome:
            totals[outcome] += 1
    return totals


# --- the threshold evaluator ----------------------------------------------------


class _Percentile95(Aggregate):
    function = "percentile_cont"
    template = "%(function)s(0.95) WITHIN GROUP (ORDER BY %(expressions)s)"
    output_field = FloatField()


def _p95(logs):
    if connection.vendor == "postgresql":
        return logs.aggregate(p95=_Percentile95("latency_ms"))["p95"] or 0.0
    values = sorted(logs.values_list("latency_ms", flat=True))
    if not values:
        return 0.0
    rank = 0.95 * (len(values) - 1)
    low, high = math.floor(rank), math.ceil(rank)
    return values[low] + (values[high] - values[low]) * (rank - low)


def _window_stats(org_id, window_minutes, now):
    from agentcc.models import AgentccRequestLog

    logs = AgentccRequestLog.no_workspace_objects.filter(
        organization_id=org_id,
        started_at__gt=now - timedelta(minutes=window_minutes),
        started_at__lte=now,
    )
    totals = logs.aggregate(
        requests=Count("id"),
        errors=Count("id", filter=Q(is_error=True)),
        cost=Coalesce(Sum("cost"), Value(Decimal(0)), output_field=DecimalField()),
    )
    totals["p95_ms"] = _p95(logs) if totals["requests"] else 0.0
    return totals


def _money(value):
    return f"{Decimal(value):.4f}".rstrip("0").rstrip(".")


def _threshold_events(alert, thresholds, stats, at):
    subscribed = set(alert.events or [])
    window = thresholds["window_minutes"]
    requests = stats["requests"]
    common = {"Window (minutes)": window, "Requests in window": requests}
    events = []
    enough = requests >= thresholds["min_requests"]
    if "error.rate_spike" in subscribed and enough:
        rate = stats["errors"] * 100.0 / requests
        if rate >= thresholds["error_rate_percent"]:
            events.append(
                {
                    "event": "error.rate_spike",
                    "context": {
                        **common,
                        "Errors in window": stats["errors"],
                        "Error rate (%)": f"{rate:.1f}",
                        "Threshold (%)": thresholds["error_rate_percent"],
                    },
                }
            )
    if "latency.spike" in subscribed and enough:
        p95 = stats["p95_ms"]
        if p95 >= thresholds["latency_ms"]:
            events.append(
                {
                    "event": "latency.spike",
                    "context": {
                        **common,
                        "p95 latency (ms)": round(p95),
                        "Threshold (ms)": thresholds["latency_ms"],
                    },
                }
            )
    if "cost.threshold" in subscribed and requests:
        cost = stats["cost"]
        if cost >= Decimal(str(thresholds["cost_usd"])):
            events.append(
                {
                    "event": "cost.threshold",
                    "context": {
                        **common,
                        "Spend (USD)": _money(cost),
                        "Threshold (USD)": thresholds["cost_usd"],
                    },
                }
            )
    for event in events:
        event["at"] = at
    return events


def evaluate_thresholds(now=None):
    """Record the threshold events each active alert's thresholds cross now."""
    now = now or timezone.now()
    at = now.isoformat()
    alerts = [
        alert
        for alert in AgentccEmailAlert.no_workspace_objects.filter(is_active=True).only(
            "id", "organization_id", "events", "thresholds"
        )
        if set(alert.events or []) & set(THRESHOLD_EVENTS)
    ]
    stats_cache = {}
    recorded = 0
    for alert in alerts:
        try:
            thresholds = effective_thresholds(alert)
            key = (alert.organization_id, thresholds["window_minutes"])
            if key not in stats_cache:
                stats_cache[key] = _window_stats(*key, now)
            events = _threshold_events(alert, thresholds, stats_cache[key], at)
            if events:
                recorded += len(_record([alert.id], events))
        except Exception:
            logger.exception("email_alert_evaluation_failed", alert_id=str(alert.id))
    return recorded


def evaluate_email_alerts(now=None):
    """One tick of the every-minute schedule: evaluate, then send what is pending
    (including what a failed send, a cooldown or a failed start left)."""
    now = now or timezone.now()
    evaluated = evaluate_thresholds(now=now)
    return {"evaluated": evaluated, **dispatch_pending(now=now)}
