"""Store the gateway's log webhook batches.

A batch holds request-log entries (log-entry-v2, or the v1 shape) and, next to
them, events: records with a ``kind``. The only kind is ``outcome``
(outcome-event-v1).

The flusher delivers a batch again when a send fails, possibly while the first
delivery is still running, so ingestion is replay-safe. A request the org has
already stored is skipped, attempts are unique on ``attempt_id``, outcome
events on ``event_id``, and each EE usage event's id is derived from the rows
its delivery added.
"""

import calendar
import hashlib
import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import structlog
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.utils import timezone

from agentcc.models import (
    AgentccAPIKey,
    AgentccRequestAttempt,
    AgentccRequestLog,
    AgentccRequestOutcome,
    AgentccSession,
)
from agentcc.services.credential_headers import mask_credential_headers
from agentcc.services.request_log_metadata import (
    GUARDRAIL_TOOL_CALLS_KEY,
    legacy_billing,
    parse_usd,
    tool_calls_metadata,
)

logger = structlog.get_logger(__name__)


# The error codes of the gateway's budget refusals (its APIError code, copied to
# the log entry's top-level error_code by plugins/logging/record.go; a caller's
# metadata never sets it). budget_throttled is a Throttle level past its rate.
BUDGET_REFUSAL_CODES = frozenset(
    {"budget_exceeded", "org_budget_exceeded", "budget_throttled"}
)
# The budget plugin's refusal metadata (level and period).
_BUDGET_PERIODS = ("daily", "weekly", "monthly", "total")
_BUDGET_LEVEL_PREFIXES = ("team:", "user:", "key:", "tag:")
MAX_BUDGET_LEVEL_LEN = 256
# A refusal's period start later than the request itself (beyond clock skew) is
# not the request's period; the entry falls back to the per-day key.
_BUDGET_PERIOD_SKEW = timedelta(days=1)


def _status_code(entry):
    return _non_negative_int(entry.get("status_code")) or 0


def _entry_is_error(entry):
    return bool(entry.get("is_error", False)) or _status_code(entry) >= 400


def _entry_time(entry):
    """The entry's own time in UTC (its timestamp, else now)."""
    parsed = _parse_datetime(entry.get("timestamp") or entry.get("started_at"))
    if parsed is None:
        return timezone.now().astimezone(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _utc_text(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _budget_error_code(entry):
    """The entry's budget refusal code, or None (top-level error_code only)."""
    code = _text(entry.get("error_code"))
    return code if code in BUDGET_REFUSAL_CODES else None


def _budget_refusal(entry):
    """The refusal's level and period, or None when absent or malformed.

    Returns {level, period, period_start (datetime, UTC), action}.
    """
    metadata = _dict(entry.get("metadata"))
    level = _text(metadata.get("budget_exceeded_level"))
    start = _parse_datetime(_text(metadata.get("budget_period_start")))
    if not level or start is None or len(level) > MAX_BUDGET_LEVEL_LEN:
        return None
    if level != "org" and not (
        level.startswith(_BUDGET_LEVEL_PREFIXES) and level.split(":", 1)[1]
    ):
        return None
    start = (start if start.tzinfo else start.replace(tzinfo=UTC)).astimezone(UTC)
    if start > _entry_time(entry) + _BUDGET_PERIOD_SKEW:
        return None
    period = _text(metadata.get("budget_period"))
    action = _text(metadata.get("budget_action"))
    return {
        "level": level,
        "period": period if period in _BUDGET_PERIODS else None,
        "period_start": start,
        "action": action if action in ("block", "throttle") else None,
    }


def _budget_dedupe_key(entry):
    """What a budget.exceeded is sent once per, within an org.

    ("level", level, period start) when the refusal carries its level and
    period start, else ("code", error code, the entry's UTC day).
    """
    refusal = _budget_refusal(entry)
    if refusal is not None:
        return ("level", refusal["level"], _utc_text(refusal["period_start"]))
    return (
        "code",
        _budget_error_code(entry) or "",
        _entry_time(entry).date().isoformat(),
    )


def _budget_key_expiry(entry):
    """When the key's budget period is over (None: it never ends)."""
    refusal = _budget_refusal(entry)
    if refusal is None:
        day = _entry_time(entry).replace(hour=0, minute=0, second=0, microsecond=0)
        return day + timedelta(days=1)
    start = refusal["period_start"]
    if refusal["period"] == "total":
        return None
    if refusal["period"] == "daily":
        return start + timedelta(days=1)
    if refusal["period"] == "weekly":
        return start + timedelta(days=7)
    if refusal["period"] == "monthly":
        year, month = divmod(start.month, 12)
        year, month = start.year + year, month + 1
        return start.replace(
            year=year,
            month=month,
            day=min(start.day, calendar.monthrange(year, month)[1]),
        )
    # No period named: kept for the longest calendar period, a month.
    return start + timedelta(days=31)


def _budget_fields(entry):
    """The budget.exceeded payload fields (None when the refusal carries no
    level and period)."""
    refusal = _budget_refusal(entry) or {}
    start = refusal.get("period_start")
    return {
        "budget_level": refusal.get("level"),
        "budget_period": refusal.get("period"),
        "budget_period_start": _utc_text(start) if start else None,
        "budget_action": refusal.get("action"),
    }


def _request_events(entries):
    """The request-level events each entry produces, in batch order.

    Yields (entry, event types): request.completed always, error.occurred for
    a failed request, guardrail.triggered, and budget.exceeded for a gateway
    budget refusal (its top-level error_code).
    """
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        triggered = ["request.completed"]
        if _entry_is_error(entry):
            triggered.append("error.occurred")
        if bool(entry.get("guardrail_triggered", False)):
            triggered.append("guardrail.triggered")
        if _budget_error_code(entry):
            triggered.append("budget.exceeded")
        yield entry, triggered


def _webhook_payload(entry, event_type):
    payload = {
        "event": event_type,
        "request_id": entry.get("request_id", ""),
        "model": entry.get("model", ""),
        "resolved_model": entry.get("resolved_model", ""),
        "provider": entry.get("provider", ""),
        "latency_ms": _non_negative_int(entry.get("latency_ms")) or 0,
        "input_tokens": _non_negative_int(
            entry.get("prompt_tokens") or entry.get("input_tokens")
        )
        or 0,
        "output_tokens": _non_negative_int(
            entry.get("completion_tokens") or entry.get("output_tokens")
        )
        or 0,
        "total_tokens": _non_negative_int(entry.get("total_tokens")) or 0,
        "status_code": _status_code(entry),
        "is_error": _entry_is_error(entry),
        "error_message": entry.get("error_message", ""),
        "error_code": _text(entry.get("error_code")) or "",
        "cache_hit": bool(entry.get("cache_hit", False)),
        "guardrail_triggered": bool(entry.get("guardrail_triggered", False)),
        "cost": float(parse_usd(entry.get("cost")) or 0),
    }
    if event_type == "budget.exceeded":
        payload.update(_budget_fields(entry))
    return payload


def _claim_webhook_budget_keys(webhooks, budget_entries):
    """The (webhook id, request id) pairs that get a budget.exceeded.

    Each webhook receives one per budget key; the keys it already received
    are kept on the webhook row (budget_event_keys) until their period ends.
    """
    from agentcc.models.webhook import AgentccWebhook
    from agentcc.services.alert_dispatcher import claim_budget_key

    now = timezone.now()
    claimed = set()
    with transaction.atomic():
        locked = AgentccWebhook.no_workspace_objects.select_for_update().filter(
            id__in=[webhook.id for webhook in webhooks]
        )
        for webhook in locked.order_by("id"):
            keys = webhook.budget_event_keys
            changed = False
            for entry in budget_entries:
                keys, new = claim_budget_key(
                    keys,
                    "|".join(_budget_dedupe_key(entry)),
                    _budget_key_expiry(entry),
                    now,
                )
                if new:
                    claimed.add((webhook.id, _request_id(entry)))
                    changed = True
            if changed:
                AgentccWebhook.no_workspace_objects.filter(id=webhook.id).update(
                    budget_event_keys=keys
                )
    return claimed


def _dispatch_webhook_events(org, logs):
    from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent

    webhooks = list(
        AgentccWebhook.no_workspace_objects.filter(
            organization=org,
            is_active=True,
            deleted=False,
        )
    )
    if not webhooks:
        return

    webhooks_by_event: dict = {}
    for webhook in webhooks:
        for event_type in webhook.events or []:
            webhooks_by_event.setdefault(event_type, []).append(webhook)

    if not webhooks_by_event:
        return

    produced = list(_request_events(logs))
    budget_entries = [
        entry for entry, events in produced if "budget.exceeded" in events
    ]
    budget_claims = set()
    if budget_entries and webhooks_by_event.get("budget.exceeded"):
        budget_claims = _claim_webhook_budget_keys(
            webhooks_by_event["budget.exceeded"], budget_entries
        )

    events_to_create = []
    for entry, triggered in produced:
        for event_type in triggered:
            for webhook in webhooks_by_event.get(event_type, []):
                if event_type == "budget.exceeded" and (
                    (webhook.id, _request_id(entry)) not in budget_claims
                ):
                    continue
                events_to_create.append(
                    AgentccWebhookEvent(
                        organization=org,
                        webhook=webhook,
                        event_type=event_type,
                        payload=_webhook_payload(entry, event_type),
                        status=AgentccWebhookEvent.PENDING,
                    )
                )

    if events_to_create:
        AgentccWebhookEvent.no_workspace_objects.bulk_create(
            events_to_create, ignore_conflicts=True
        )
        logger.info(
            "webhook_events_enqueued",
            org_id=str(org.id),
            count=len(events_to_create),
        )

        from agentcc.services.webhook_delivery import deliver_webhook_events

        try:
            deliver_webhook_events(org_id=org.id, limit=max(len(events_to_create), 100))
        except Exception:
            logger.exception("webhook_immediate_delivery_failed", org_id=str(org.id))


def _alert_context(entry, event_type):
    """What an alert email shows for one request-level event."""
    context = {
        "Request ID": _request_id(entry),
        "Model": _text(entry.get("model")),
        "Provider": _text(entry.get("provider")),
        "Status": _status_code(entry) or None,
        "Error code": _text(entry.get("error_code")),
        "Error": _text(entry.get("error_message")),
        "API key": _text(entry.get("auth_key_id") or entry.get("api_key_id")),
    }
    if event_type == "budget.exceeded":
        budget = _budget_fields(entry)
        context.update(
            {
                "Budget level": budget["budget_level"],
                "Budget period": budget["budget_period"],
                "Period start (UTC)": budget["budget_period_start"],
                "Budget action": budget["budget_action"],
            }
        )
    return {key: value for key, value in context.items() if value not in (None, "")}


def _record_alert_events(org, logs):
    """Hand the email alert dispatcher the request-level events of new rows.

    The dispatcher records them on each subscribed alert and starts the send on
    Temporal after commit; nothing is sent on this thread.
    """
    from agentcc.services.alert_dispatcher import REQUEST_EVENTS, record_request_events

    events = []
    for entry, triggered in _request_events(logs):
        for event_type in triggered:
            if event_type not in REQUEST_EVENTS:
                continue
            event = {
                "event": event_type,
                "context": _alert_context(entry, event_type),
                "at": _entry_time(entry).isoformat(),
            }
            if event_type == "budget.exceeded":
                event["budget_key"] = "|".join(_budget_dedupe_key(entry))
                event["budget_key_expires"] = _budget_key_expiry(entry)
            events.append(event)
    if events:
        record_request_events(org, events)


MAX_BODY_SIZE = 65536  # 64KB
MAX_SESSION_ID_LEN = 255  # matches VARCHAR(255) DB column
MAX_REQUEST_ID_LEN = 255  # matches VARCHAR(255) DB column
MAX_ATTEMPTS = 16

OUTCOME_EVENT_KIND = "outcome"
USAGE_EVENT_REQUEST_PREFIX = "agentcc:req:"
USAGE_EVENT_CACHE_HIT_PREFIX = "agentcc:cache_hit:"

# Top-level keys only a log entry v2 carries.
_V2_KEYS = frozenset(
    {
        "generation_id",
        "attempts",
        "attempt_count",
        "cached_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "billing_mode",
        "credential_type",
        "cost_upstream",
        "cost_markup",
        "cost_markup_bps",
        "cost_source",
        "usage_estimated",
        "cost_landed",
        "served_upstream_provider",
        "upstream_key_id",
        "content_mode",
        "agent",
        "routing",
        "app",
        "tags",
        "end_user",
        "guardrail",
    }
)
# Column: key in the entry's agent{} and routing{} objects.
_AGENT_COLUMNS = {
    "agent_framework": "framework",
    "agent_id": "agent_id",
    "agent_version": "agent_version",
    "agent_run_id": "run_id",
    "agent_parent_run_id": "parent_run_id",
    "agent_root_run_id": "root_run_id",
    "agent_task_id": "task_id",
    "agent_workflow": "workflow",
    "agent_step": "step",
    "agent_step_source": "step_source",
    "agent_env": "env",
    "agent_identity_source": "identity_source",
    "budget_scope": "budget_scope",
}
_ROUTING_COLUMNS = {
    "policy_version": "policy_version",
    "policy_variant": "policy_variant",
    "catalog_snapshot_id": "catalog_snapshot_id",
}
_ATTEMPT_TEXT_FIELDS = (
    "kind",
    "reason",
    "provider",
    "model",
    "endpoint_id",
    "upstream_provider",
    "served_upstream_provider",
    "upstream_key_id",
    "credential_type",
    "outcome",
    "error_class",
    "error_origin",
    "error_message",
    "cost_source",
)
_ATTEMPT_INT_FIELDS = (
    "status_code",
    "latency_ms",
    "ttfb_ms",
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "upstream_cost_micros",
    "billed_cost_micros",
    "landed_cost_micros",
    "upstream_fee_bps",
    "processing_fee_bps",
)
# Over-long text in these columns is shortened. Any other over-long value is
# an identifier or an enum value, and is not stored: a cut id joins wrong rows.
_SHORTENED_COLUMNS = frozenset(
    {
        "model",
        "provider",
        "resolved_model",
        "routing_strategy",
        "app_title",
        "app_referer",
        "end_user",
        "endpoint_id",
        "upstream_provider",
        "error_message",
    }
)
# outcome-event-v1's outcome and source values.
_EVENT_OUTCOMES = frozenset({"ok", "bad", "empty", "verify_failed", "retried"})
_EVENT_SOURCES = frozenset(
    {AgentccRequestOutcome.Source.FEEDBACK, AgentccRequestOutcome.Source.PREV_OUTCOME}
)


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _truncate_json(value, max_size=MAX_BODY_SIZE):
    if value is None:
        return None
    serialized = json.dumps(value)
    if len(serialized) <= max_size:
        return value
    return {"_truncated": True, "_original_size": len(serialized)}


def _sanitize_session_id(sid):
    """Return the session ID unchanged, or empty string if it exceeds the DB column length."""
    if not sid:
        return ""
    if len(sid) > MAX_SESSION_ID_LEN:
        logger.warning(
            "session_id_too_long_in_request_log",
            session_id_len=len(sid),
            max_len=MAX_SESSION_ID_LEN,
        )
        return ""
    return sid


def _sanitize_headers(headers):
    """Headers as the request log stores them: no credential header's value.

    Masked here whatever the gateway sent (it masks them too), by the rule
    the gateway uses (``agentcc/services/credential_headers.py``).
    """
    return mask_credential_headers(headers)


def _compute_cost(entry):
    gateway_cost = entry.get("cost", 0)
    try:
        if gateway_cost and Decimal(str(gateway_cost)) > 0:
            return gateway_cost
    except (InvalidOperation, TypeError):
        pass

    metadata = entry.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    try:
        prompt_tokens = int(
            entry.get("prompt_tokens") or entry.get("input_tokens") or 0
        )
    except (TypeError, ValueError):
        prompt_tokens = 0

    try:
        completion_tokens = int(
            entry.get("completion_tokens") or entry.get("output_tokens") or 0
        )
    except (TypeError, ValueError):
        completion_tokens = 0

    try:
        input_characters = int(metadata.get("input_characters") or 0)
    except (TypeError, ValueError):
        input_characters = 0

    try:
        audio_seconds = float(metadata.get("audio_seconds") or 0)
    except (TypeError, ValueError):
        audio_seconds = 0.0

    raw_images_generated = metadata.get("images_generated")
    try:
        images_generated = (
            int(raw_images_generated) if raw_images_generated is not None else None
        )
    except (TypeError, ValueError):
        images_generated = 0

    quality = metadata.get("quality") or "standard"

    from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
    from agentic_eval.core_evals.run_prompt.model_pricing import get_model_info

    for model in [entry.get("resolved_model"), entry.get("model")]:
        if not model:
            continue

        model_info = get_model_info(model)
        is_image_model = bool(
            model_info and model_info.get("mode") == "image_generation"
        )

        if (
            not prompt_tokens
            and not completion_tokens
            and not input_characters
            and not audio_seconds
            and not (images_generated or 0)
            and not is_image_model
        ):
            continue

        token_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "input_characters": input_characters,
            "audio_seconds": audio_seconds,
        }

        if images_generated is not None or is_image_model:
            token_usage["images_generated"] = (
                images_generated if images_generated is not None else 1
            )
            token_usage["quality"] = quality

        try:
            result = calculate_total_cost(model, token_usage)
            total = result.get("total_cost", 0)
            if total:
                return total
        except Exception:
            pass

    return 0


class LogIngestionError(Exception):
    """Part of a batch could not be stored; every other org's part was."""


def ingest_request_logs(logs):
    """Store a log webhook batch; return how many request-log rows it added.

    The gateway flushes one buffer for every org it serves, so a batch mixes
    orgs: each entry is attributed to the org of its own API key (or its own
    metadata org_id). Each org's entries are stored in their own transaction.
    When one org fails, the others are still stored, and LogIngestionError is
    raised once all were tried, so the failure is reported and a redelivery
    only fills in what is missing.
    """
    if not logs:
        return 0

    entries, events = _split_records(logs)
    failed = 0
    if events:
        try:
            _ingest_outcome_events(events)
        except Exception:
            failed += 1
            logger.exception("outcome_event_ingestion_failed", event_count=len(events))

    by_org, unattributed = _group_by_org(entries)
    if unattributed:
        logger.warning("log_ingestion_skipped_no_org", log_count=unattributed)
    added = 0
    for org, org_logs in by_org:
        try:
            added += _ingest_org_logs(org, org_logs)
        except Exception:
            failed += 1
            logger.exception(
                "log_ingestion_org_failed", org_id=str(org.id), log_count=len(org_logs)
            )
    if failed:
        raise LogIngestionError(f"{failed} part(s) of the log batch were not stored")
    return added


def _split_records(logs):
    """Split a batch into log entries (no kind) and outcome events."""
    entries, events = [], []
    for record in logs:
        if not isinstance(record, dict):
            continue
        if "kind" not in record:
            entries.append(record)
        elif record["kind"] == OUTCOME_EVENT_KIND:
            events.append(record)
        else:
            logger.warning(
                "log_batch_unknown_record_kind", kind=str(record["kind"])[:64]
            )
    return entries, events


def _ingest_org_logs(org, logs):
    with transaction.atomic():
        _lock_org_ingestion(org)
        stored = _store_new_entries(org, _new_entries(org, logs))
    if stored:
        _after_ingest(org, [entry for entry, _ in stored], [row for _, row in stored])
    return len(stored)


def _lock_org_ingestion(org):
    """Hold the org's ingestion lock until the transaction ends.

    Two deliveries of one batch can run at once (the flusher resends after its
    15 s timeout); the lock makes the second wait for the first, so its
    duplicate pre-query sees the first's rows. Each org is locked in its own
    transaction, so a delivery never holds two locks and two deliveries cannot
    deadlock.
    """
    if connection.vendor != "postgresql":
        return
    digest = hashlib.sha256(f"agentcc:log_ingest:{org.id}".encode()).digest()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [int.from_bytes(digest[:8], "big", signed=True)],
        )


def _new_entries(org, logs):
    """The first copy of each entry whose request the org has not stored.

    Entries without a request id cannot be matched, so they are all kept.
    """
    seen = _existing_request_ids(org, {_request_id(entry) for entry in logs} - {""})
    new = []
    for entry in logs:
        request_id = _request_id(entry)
        if len(request_id) > MAX_REQUEST_ID_LEN:
            logger.warning("log_entry_request_id_too_long", length=len(request_id))
            continue
        if request_id:
            if request_id in seen:
                continue
            seen.add(request_id)
        new.append(entry)
    return new


def _existing_request_ids(org, request_ids):
    """The request ids the org has already stored, soft-deleted rows included."""
    if not request_ids:
        return set()
    return set(
        AgentccRequestLog._base_manager.filter(
            organization=org, request_id__in=request_ids
        ).values_list("request_id", flat=True)
    )


def _store_new_entries(org, entries):
    """Insert the log rows, attempts and classified outcomes of new entries."""
    stored = []
    for entry in entries:
        try:
            stored.append((entry, _log_row(org, entry)))
        except Exception:
            logger.exception(
                "failed_to_parse_log_entry",
                request_id=_request_id(entry),
                model=str(entry.get("model", "")),
            )
    if not stored:
        return stored

    AgentccRequestLog.no_workspace_objects.bulk_create([row for _, row in stored])
    attempts = [
        attempt for entry, row in stored for attempt in _attempt_rows(org, row, entry)
    ]
    if attempts:
        AgentccRequestAttempt.objects.bulk_create(attempts, ignore_conflicts=True)
    outcomes = [
        outcome
        for _, row in stored
        if (outcome := _classified_outcome_row(org, row)) is not None
    ]
    if outcomes:
        AgentccRequestOutcome.objects.bulk_create(outcomes, ignore_conflicts=True)
    return stored


def _after_ingest(org, entries, rows):
    """What newly stored rows trigger, once they are committed: email alerts,
    customer webhooks, EE metering, sessions and the API keys' last use. Each
    is best effort: the rows are already stored, so a failure here is only
    logged."""
    try:
        _record_alert_events(org, entries)
    except Exception:
        logger.exception("email_alert_events_failed", org_id=str(org.id))

    try:
        _dispatch_webhook_events(org, entries)
    except Exception:
        logger.exception("webhook_dispatch_failed", org_id=str(org.id))

    _emit_usage_events(org, rows)

    try:
        _ensure_sessions_exist(org, entries)
    except Exception:
        logger.exception("ensure_sessions_failed", org_id=str(org.id))

    api_key_ids = {
        entry.get("api_key_id") or entry.get("auth_key_id")
        for entry in entries
        if entry.get("api_key_id") or entry.get("auth_key_id")
    }
    if api_key_ids:
        try:
            AgentccAPIKey.no_workspace_objects.filter(
                gateway_key_id__in=api_key_ids,
            ).update(last_used_at=datetime.now(UTC))
        except Exception:
            logger.exception("api_key_last_used_update_failed", org_id=str(org.id))


def _log_row(org, entry):
    """The AgentccRequestLog row for one entry: the v1 fields and the v2 columns."""
    metadata = (
        dict(entry["metadata"]) if isinstance(entry.get("metadata"), dict) else {}
    )
    # Mapped as sent: a non-stream tool-call block can arrive with the
    # pre-block status_code and no verdict; nothing is inferred.
    tool_calls = _dict(_dict(entry.get("guardrail")).get("tool_calls"))
    if tool_calls:
        metadata[GUARDRAIL_TOOL_CALLS_KEY] = tool_calls_metadata(tool_calls)
    v2 = _v2_columns(entry, metadata)
    status_code = int(entry.get("status_code", 0))
    row = AgentccRequestLog(
        organization=org,
        request_id=_request_id(entry),
        model=_text(entry.get("model")) or "",
        provider=_text(entry.get("provider")) or "",
        resolved_model=_text(entry.get("resolved_model")) or "",
        latency_ms=int(entry.get("latency_ms", 0)),
        started_at=_parse_datetime(entry.get("timestamp") or entry.get("started_at")),
        input_tokens=int(entry.get("prompt_tokens") or entry.get("input_tokens") or 0),
        output_tokens=int(
            entry.get("completion_tokens") or entry.get("output_tokens") or 0
        ),
        total_tokens=int(entry.get("total_tokens", 0)),
        cost=_billed_cost(entry, v2["cost_source"]),
        status_code=status_code,
        is_stream=bool(entry.get("is_stream", False)),
        is_error=bool(entry.get("is_error", False)) or status_code >= 400,
        error_message=_text(entry.get("error_message")) or "",
        cache_hit=bool(entry.get("cache_hit", False)),
        fallback_used=bool(entry.get("fallback_used", False)),
        guardrail_triggered=bool(entry.get("guardrail_triggered", False)),
        api_key_id=_text(entry.get("auth_key_id") or entry.get("api_key_id")) or "",
        user_id=_text(entry.get("user_id")) or "",
        session_id=_sanitize_session_id(_text(entry.get("session_id")) or ""),
        routing_strategy=_text(entry.get("routing_strategy")) or "",
        metadata=metadata,
        request_body=_truncate_json(entry.get("request_body")),
        response_body=_truncate_json(entry.get("response_body")),
        request_headers=_sanitize_headers(entry.get("request_headers")),
        response_headers=_sanitize_headers(entry.get("response_headers")),
        guardrail_results=entry.get("guardrail_results"),
        **v2,
    )
    _fit_to_columns(row)
    return row


def _v2_columns(entry, metadata):
    """The log entry v2 columns of an entry.

    Top-level fields win, and a v1 entry falls back to the gateway's metadata
    keys. A v2 entry omits a false usage_estimated or routing.auto, so an
    omitted one is stored as False; a v1 entry's stays NULL (not reported).
    """
    agent = _dict(entry.get("agent"))
    routing = _dict(entry.get("routing"))
    app = _dict(entry.get("app"))
    columns = {
        "billing_mode": _text(entry.get("billing_mode")),
        "credential_type": _text(entry.get("credential_type")),
        "cost_upstream": parse_usd(entry.get("cost_upstream")),
        "cost_markup": parse_usd(entry.get("cost_markup")),
        "cost_landed": parse_usd(entry.get("cost_landed")),
        "cost_source": _text(entry.get("cost_source")),
        "usage_estimated": _bool_or_none(entry.get("usage_estimated")),
        "served_upstream_provider": _text(entry.get("served_upstream_provider")),
        "upstream_key_id": _text(entry.get("upstream_key_id")),
        "content_mode": _text(entry.get("content_mode")),
        "cached_tokens": _non_negative_int(entry.get("cached_tokens")),
        "cache_write_tokens": _non_negative_int(entry.get("cache_write_tokens")),
        "reasoning_tokens": _non_negative_int(entry.get("reasoning_tokens")),
        "generation_id": _text(entry.get("generation_id")),
        "agent_turn": _non_negative_int(agent.get("turn")),
        "auto_routed": _bool_or_none(routing.get("auto")),
        "app_title": _text(app.get("title")),
        "app_referer": _text(app.get("referer")),
        "end_user": _text(entry.get("end_user")),
        "tags": _tags(entry.get("tags")),
    }
    for column, key in _AGENT_COLUMNS.items():
        columns[column] = _text(agent.get(key))
    for column, key in _ROUTING_COLUMNS.items():
        columns[column] = _text(routing.get(key))
    for column, value in legacy_billing(metadata).items():
        if columns[column] is None:
            columns[column] = value
    if _is_v2(entry):
        for column in ("usage_estimated", "auto_routed"):
            if columns[column] is None:
                columns[column] = False
    attempt_count = _attempt_count(entry)
    if attempt_count is not None:
        columns["attempt_count"] = attempt_count
    return columns


def _attempt_count(entry):
    """How many upstream attempts the request made; None keeps the default (1).

    The flusher omits attempt_count when a request made no attempt, so a v2
    cache hit without attempts[] made none and is stored as 0. A v1 entry
    never reports attempts and keeps the default.
    """
    count = _non_negative_int(entry.get("attempt_count"))
    if count is not None:
        return count
    attempts = entry.get("attempts")
    if isinstance(attempts, list) and attempts:
        return min(len(attempts), MAX_ATTEMPTS)
    if _is_v2(entry) and bool(entry.get("cache_hit")):
        return 0
    return None


def _billed_cost(entry, cost_source):
    """The gateway's cost whenever it priced the request, which it reports with
    a cost_source, even for a cost of 0 such as a managed cache hit. Only an
    entry without one is priced by Django."""
    if cost_source:
        return parse_usd(entry.get("cost")) or Decimal(0)
    return _compute_cost(entry)


def _attempt_rows(org, log_row, entry):
    """The AgentccRequestAttempt rows of a new log row's attempts[]."""
    attempts = entry.get("attempts")
    if not isinstance(attempts, list) or not log_row.request_id:
        return []
    if len(attempts) > MAX_ATTEMPTS:
        logger.warning("log_entry_too_many_attempts", count=len(attempts))
        attempts = attempts[:MAX_ATTEMPTS]
    rows = []
    for attempt in attempts:
        row = _attempt_row(org, log_row, attempt) if isinstance(attempt, dict) else None
        if row is None:
            logger.warning("log_entry_attempt_skipped", request_id=log_row.request_id)
            continue
        rows.append(row)
    return rows


def _attempt_row(org, log_row, attempt):
    attempt_id, index = (
        _text(attempt.get("attempt_id")),
        _non_negative_int(attempt.get("index")),
    )
    if not attempt_id or not index:
        return None
    row = AgentccRequestAttempt(
        organization=org,
        request_log=log_row,
        request_id=log_row.request_id,
        attempt_id=attempt_id,
        index=index,
        started_at=_parse_datetime(attempt.get("started_at")),
        usage_estimated=attempt.get("usage_estimated") is True,
        billable=attempt.get("billable") is True,
        **{field: _text(attempt.get(field)) or "" for field in _ATTEMPT_TEXT_FIELDS},
        **{
            field: _non_negative_int(attempt.get(field))
            for field in _ATTEMPT_INT_FIELDS
        },
    )
    _fit_to_columns(row)
    return row if row.attempt_id and row.index else None


def _classified_outcome_row(org, row):
    """The classified outcome of a new log row whose metadata has outcome_class."""
    outcome = _text(row.metadata.get("outcome_class"))
    if outcome not in AgentccRequestOutcome.Outcome.values:
        if outcome is not None:
            logger.warning("log_entry_unknown_outcome_class", request_id=row.request_id)
        return None
    outcome_row = AgentccRequestOutcome(
        organization=org,
        request_id=row.request_id,
        agent_run_id=row.agent_run_id,
        agent_id=row.agent_id,
        agent_step=row.agent_step,
        model=row.resolved_model or row.model,
        outcome=outcome,
        source=AgentccRequestOutcome.Source.CLASSIFIED,
        occurred_at=row.started_at or timezone.now(),
    )
    _fit_to_columns(outcome_row)
    return outcome_row if outcome_row.request_id else None


def _ingest_outcome_events(events):
    """Store outcome-event-v1 records, once per event_id.

    An event is kept even when its request has not arrived yet: readers join
    on (organization, request_id). An event off the contract is rejected and
    logged by its id and the reason, never by its content.
    """
    orgs = {}
    rows = []
    for event in events:
        row, reason = _outcome_event_row(event, orgs)
        if row is None:
            logger.warning(
                "outcome_event_rejected",
                reason=reason,
                event_id=(_text(event.get("event_id")) or "")[:64],
            )
            continue
        rows.append(row)
    if rows:
        AgentccRequestOutcome.objects.bulk_create(rows, ignore_conflicts=True)


def _outcome_event_row(event, orgs):
    """The AgentccRequestOutcome row of one event, or None and why not."""
    event_id = _text(event.get("event_id"))
    if not event_id:
        return None, "missing_event_id"
    if len(event_id) > AgentccRequestOutcome._meta.get_field("event_id").max_length:
        return None, "event_id_too_long"  # cutting it could merge two events
    request_id = _text(event.get("generation_id"))
    if not request_id:
        return None, "missing_generation_id"
    outcome, source = _text(event.get("outcome")), _text(event.get("source"))
    if outcome not in _EVENT_OUTCOMES:
        return None, "invalid_outcome"
    if source not in _EVENT_SOURCES:
        return None, "invalid_source"
    ts_ms = _non_negative_int(event.get("ts_ms"))
    try:
        occurred_at = datetime.fromtimestamp(ts_ms / 1000, UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None, "invalid_ts_ms"
    org = _org_by_id(event.get("org_id"), orgs)
    if org is None:
        return None, "unknown_org"
    row = AgentccRequestOutcome(
        organization=org,
        request_id=request_id,
        event_id=event_id,
        outcome=outcome,
        source=source,
        score=_score(event.get("score")),
        agent_run_id=_text(event.get("agent_run_id")),
        agent_id=_text(event.get("agent_id")),
        agent_step=_text(event.get("agent_step")),
        model=_text(event.get("model")) or "",
        occurred_at=occurred_at,
    )
    _fit_to_columns(row)
    if not row.request_id:
        return None, "generation_id_too_long"
    return row, None


def _emit_usage_events(org, rows):
    """Meter the rows a delivery added, as gateway_request and gateway_cache_hit.

    Each event id is uuid5 of the org and the sorted added request ids, so a
    delivery that adds the same rows again (after its first try rolled back)
    emits the same ids, which the usage consumer deduplicates; a delivery
    that adds nothing emits nothing. Metering never fails ingestion.
    """
    try:
        from ee.usage.deployment import DeploymentMode
        from ee.usage.schemas.event_types import BillingEventType
        from ee.usage.schemas.events import UsageEvent
        from ee.usage.services.emitter import emit
    except ImportError:
        return

    try:
        if DeploymentMode.is_oss():
            return
        request_ids = [row.request_id for row in rows]
        cache_hits = sum(1 for row in rows if row.cache_hit)
        if len(rows) > cache_hits:
            emit(
                UsageEvent(
                    event_id=_usage_event_id(
                        USAGE_EVENT_REQUEST_PREFIX, org.id, request_ids
                    ),
                    org_id=str(org.id),
                    event_type=BillingEventType.GATEWAY_REQUEST,
                    amount=len(rows) - cache_hits,
                    properties={"batch_size": len(rows)},
                )
            )
        if cache_hits:
            emit(
                UsageEvent(
                    event_id=_usage_event_id(
                        USAGE_EVENT_CACHE_HIT_PREFIX, org.id, request_ids
                    ),
                    org_id=str(org.id),
                    event_type=BillingEventType.GATEWAY_CACHE_HIT,
                    amount=cache_hits,
                )
            )
    except Exception:
        logger.exception("gateway_usage_emit_failed", org_id=str(org.id))


def _usage_event_id(prefix, org_id, request_ids):
    """uuid5(NAMESPACE_URL, prefix + org + ":" + sha256 of the sorted request ids)."""
    digest = hashlib.sha256("\n".join(sorted(request_ids)).encode()).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{prefix}{org_id}:{digest}"))


def _fit_to_columns(row):
    """Make every value fit its column, so one oversized value cannot fail an
    org's whole insert. Over-long display text is shortened; an over-long
    identifier or enum value, or an out-of-range number, is not stored (NULL,
    or the column's default)."""
    for field in row._meta.concrete_fields:
        value = getattr(row, field.attname)
        if value is None or _fits(field, value):
            continue
        logger.warning(
            "log_value_does_not_fit_column",
            table=row._meta.db_table,
            column=field.name,
        )
        if isinstance(field, models.CharField) and field.name in _SHORTENED_COLUMNS:
            setattr(row, field.attname, value[: field.max_length])
        else:
            setattr(row, field.attname, None if field.null else field.get_default())


def _fits(field, value):
    if isinstance(field, models.CharField):
        return not isinstance(value, str) or len(value) <= field.max_length
    if isinstance(field, models.IntegerField):
        if not isinstance(value, int):
            return True
        low, high = connection.ops.integer_field_range(field.get_internal_type())
        return (low is None or value >= low) and (high is None or value <= high)
    if isinstance(field, models.DecimalField):
        try:
            return abs(Decimal(str(value))) < Decimal(10) ** (
                field.max_digits - field.decimal_places
            )
        except InvalidOperation:
            return False
    return True


def _is_v2(entry):
    return not _V2_KEYS.isdisjoint(entry)


def _request_id(entry):
    value = entry.get("request_id")
    return "" if value is None else str(value)


def _text(value):
    """A text value, or None when it is absent, empty or not a scalar."""
    if value is None or isinstance(value, bool | dict | list):
        return None
    return str(value) or None


def _non_negative_int(value):
    """A non-negative integer, or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _bool_or_none(value):
    return value if isinstance(value, bool) else None


def _score(value):
    """A finite number, or None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        score = float(value)
    except OverflowError:
        return None
    return score if math.isfinite(score) else None


def _tags(value):
    if not isinstance(value, list):
        return None
    return [tag for tag in value if isinstance(tag, str) and tag] or None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _ensure_sessions_exist(org, logs):
    """Auto-create AgentccSession rows for any new session_ids in the batch."""
    if not org:
        return
    session_ids = set()
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("session_id")
        if not sid:
            continue
        if len(sid) > MAX_SESSION_ID_LEN:
            logger.warning(
                "session_id_too_long_skipped",
                session_id_len=len(sid),
                max_len=MAX_SESSION_ID_LEN,
            )
            continue
        session_ids.add(sid)
    if not session_ids:
        return
    existing = set(
        AgentccSession.no_workspace_objects.filter(
            organization=org,
            session_id__in=session_ids,
        ).values_list("session_id", flat=True)
    )
    new_ids = session_ids - existing
    if not new_ids:
        return
    AgentccSession.no_workspace_objects.bulk_create(
        [AgentccSession(organization=org, session_id=sid, name=sid) for sid in new_ids],
        ignore_conflicts=True,
    )
    logger.info(
        "auto_created_sessions",
        count=len(new_ids),
        session_ids=list(new_ids),
    )


def _group_by_org(logs):
    """Split a batch into (org, entries) groups, in first-seen order.

    Returns the groups and the number of entries no org could be found for.
    """
    groups: dict = {}
    orgs: dict = {}
    key_orgs: dict = {}
    id_orgs: dict = {}
    unattributed = 0
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        org = _resolve_entry_org(entry, key_orgs, id_orgs)
        if org is None:
            unattributed += 1
            continue
        orgs.setdefault(org.id, org)
        groups.setdefault(org.id, []).append(entry)
    return [(orgs[org_id], entries) for org_id, entries in groups.items()], unattributed


def _resolve_entry_org(entry, key_orgs, id_orgs):
    """The org an entry belongs to: its API key's, else its metadata org_id.

    key_orgs and id_orgs memoise lookups across the batch.
    """
    key_id = entry.get("auth_key_id") or entry.get("api_key_id")
    if key_id:
        if key_id not in key_orgs:
            api_key = (
                AgentccAPIKey.no_workspace_objects.filter(gateway_key_id=key_id)
                .select_related("organization")
                .first()
            )
            key_orgs[key_id] = api_key.organization if api_key else None
        if key_orgs[key_id] is not None:
            return key_orgs[key_id]

    return _org_by_id(_dict(entry.get("metadata")).get("org_id"), id_orgs)


def _org_by_id(org_id, cache):
    """The org with this id, or None; cache memoises lookups across a batch."""
    from accounts.models import Organization

    org_id = _text(org_id)
    if not org_id:
        return None
    if org_id not in cache:
        try:
            cache[org_id] = Organization.objects.filter(id=org_id).first()
        except (ValueError, ValidationError):
            cache[org_id] = None  # not an org ID at all
    return cache[org_id]
