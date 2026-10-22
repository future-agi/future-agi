import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import structlog
from django.db import connection, transaction
from django.db.models import Q

from agentcc.models import AgentccAPIKey, AgentccRequestLog, AgentccSession
from agentcc.services.credential_headers import mask_credential_headers

logger = structlog.get_logger(__name__)


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
        return 0

    webhooks_by_event: dict = {}
    for webhook in webhooks:
        for event_type in webhook.events or []:
            webhooks_by_event.setdefault(event_type, []).append(webhook)

    if not webhooks_by_event:
        return 0

    events_to_create = []
    for entry in logs:
        if not isinstance(entry, dict):
            continue

        is_error = (
            bool(entry.get("is_error", False))
            or int(entry.get("status_code", 0)) >= 400
        )
        guardrail_triggered = bool(entry.get("guardrail_triggered", False))

        base_payload = {
            "request_id": entry.get("request_id", ""),
            "model": entry.get("model", ""),
            "resolved_model": entry.get("resolved_model", ""),
            "provider": entry.get("provider", ""),
            "latency_ms": int(entry.get("latency_ms", 0)),
            "input_tokens": int(
                entry.get("prompt_tokens") or entry.get("input_tokens") or 0
            ),
            "output_tokens": int(
                entry.get("completion_tokens") or entry.get("output_tokens") or 0
            ),
            "total_tokens": int(entry.get("total_tokens", 0)),
            "status_code": int(entry.get("status_code", 0)),
            "is_error": is_error,
            "error_message": entry.get("error_message", ""),
            "cache_hit": bool(entry.get("cache_hit", False)),
            "guardrail_triggered": guardrail_triggered,
            "cost": float(entry.get("cost", 0) or 0),
        }

        triggered = ["request.completed"]
        if is_error:
            triggered.append("error.occurred")
        if guardrail_triggered:
            triggered.append("guardrail.triggered")

        for event_type in triggered:
            for webhook in webhooks_by_event.get(event_type, []):
                events_to_create.append(
                    AgentccWebhookEvent(
                        organization=org,
                        webhook=webhook,
                        event_type=event_type,
                        payload={**base_payload, "event": event_type},
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
    return len(events_to_create)


def _deliver_webhooks(pending):
    """Deliver the batch's new webhook events at once, within one time budget.

    ``pending`` is (org, events enqueued) pairs. This runs after each org's
    rows are stored and metered, so a slow endpoint holds up no org's rows.
    The budget keeps the response well under the flusher's 15 s timeout, after
    which it resends the whole batch. Events the budget does not reach stay
    pending, and the org's next batch delivers them, as it does retries.
    """
    if not pending:
        return
    from agentcc.services.webhook_delivery import deliver_webhook_events

    deadline = time.monotonic() + WEBHOOK_DELIVERY_BUDGET_S
    for org, enqueued in pending:
        try:
            deliver_webhook_events(
                org_id=org.id, limit=max(enqueued, 100), deadline=deadline
            )
        except Exception:
            logger.exception("webhook_immediate_delivery_failed", org_id=str(org.id))


MAX_BODY_SIZE = 65536  # 64KB
MAX_SESSION_ID_LEN = 255  # matches VARCHAR(255) DB column
MAX_USER_ID_LEN = 255  # matches VARCHAR(255) DB column
MAX_LOGGED_REQUEST_IDS = 20  # skipped entries named in one warning
WEBHOOK_DELIVERY_BUDGET_S = 5.0  # immediate webhook delivery, whole batch


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
    """One org's part of a batch was not stored; every other org's part was."""


def ingest_request_logs(logs):
    """Store a log webhook batch; return how many request-log rows it built.

    The gateway's LogFlusher keeps one buffer for every org it serves, so one
    batch mixes orgs. Each entry is stored under the org of its own API key
    (``auth_key_id``) as Django records it, and an entry whose key Django
    cannot place in exactly one org is skipped and logged, never stored under
    another entry's org. ``metadata.org_id`` never decides an entry's org.

    Each org's entries, and what they trigger (webhooks, usage events,
    sessions, the keys' last use), are handled apart. When one org's part
    fails, the others are still stored and LogIngestionError is raised once
    all were tried. An entry the org already stored (the flusher resends a
    batch after a timeout or a 5xx) is not stored, metered or sent again.
    Webhook delivery runs last, for the whole batch, within a time budget.
    """
    if not logs:
        return 0

    groups, skipped = _group_by_org(logs)
    if skipped:
        logger.warning(
            "log_entry_unattributed",
            skipped=len(skipped),
            reasons=dict(Counter(reason for _, reason in skipped)),
            request_ids=[rid for rid, _ in skipped if rid][:MAX_LOGGED_REQUEST_IDS],
        )

    stored = 0
    failed = 0
    pending_webhooks = []
    for org, org_logs in groups:
        try:
            rows, enqueued = _ingest_org_logs(org, org_logs)
        except Exception:
            failed += 1
            logger.exception(
                "log_ingestion_org_failed", org_id=str(org.id), log_count=len(org_logs)
            )
            continue
        stored += rows
        if enqueued:
            pending_webhooks.append((org, enqueued))
    _deliver_webhooks(pending_webhooks)
    if failed:
        raise LogIngestionError(
            f"The log entries of {failed} org(s) in the batch were not stored"
        )
    return stored


def _ingest_org_logs(org, logs):
    """Store one org's new entries and run what they trigger, for that org only.

    Returns (rows stored, webhook events enqueued). The duplicate check and
    the insert share one transaction under the org's ingestion lock, so two
    deliveries of one batch cannot both store it. What the rows trigger runs
    after the commit and is best effort.
    """
    with transaction.atomic():
        _lock_org_ingestion(org)
        logs = _new_entries(org, logs)
        objects = _build_rows(org, logs)
        if objects:
            AgentccRequestLog.no_workspace_objects.bulk_create(
                objects, ignore_conflicts=True
            )
    if not logs:
        return 0, 0

    enqueued = 0
    try:
        enqueued = _dispatch_webhook_events(org, logs)
    except Exception:
        logger.exception("webhook_dispatch_failed", org_id=str(org.id))

    _emit_usage_events(org, objects)

    try:
        _ensure_sessions_exist(org, logs)
    except Exception:
        logger.exception("ensure_sessions_failed", org_id=str(org.id))

    api_key_ids = {_entry_key_id(entry) for entry in logs} - {""}
    if api_key_ids:
        try:
            AgentccAPIKey.no_workspace_objects.filter(
                organization=org,
                gateway_key_id__in=api_key_ids,
            ).update(last_used_at=datetime.now(UTC))
        except Exception:
            logger.exception("api_key_last_used_update_failed", org_id=str(org.id))

    return len(objects), enqueued


def _lock_org_ingestion(org):
    """Hold the org's ingestion lock until the transaction ends.

    The lock makes the duplicate check and the insert one step for each org,
    so a resend that arrives during the first delivery is stored once. Each
    org is locked in its own transaction, so a delivery never holds two locks
    and two deliveries cannot deadlock.
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
    """The entries the org has not stored yet, each once.

    An entry is matched by (request_id, started_at): the gateway's ULID and
    the request's start time, neither of which a caller sets. A resent entry
    carries the same two values, so the lookup only reads the org's rows that
    started at the batch's own start times (the ``organization, -started_at``
    index), never its whole history. Entries without a request ID, or with a
    timestamp that has no time zone, cannot be matched and are all kept.
    """
    keyed = []
    for entry in logs:
        request_id = str(entry.get("request_id") or "")
        started = _parse_datetime(entry.get("timestamp") or entry.get("started_at"))
        matchable = bool(request_id) and (started is None or started.tzinfo is not None)
        keyed.append((entry, (request_id, started) if matchable else None))

    keys = [key for _, key in keyed if key]
    seen = _stored_keys(org, keys) if keys else set()
    new = []
    for entry, key in keyed:
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        new.append(entry)
    return new


def _stored_keys(org, keys):
    """The (request_id, started_at) pairs of ``keys`` the org already stored.

    Rows with no start time are read only when an entry of the batch has none,
    so a batch with start times never reads the org's undated rows.
    """
    starts = {started for _, started in keys if started is not None}
    when = Q(started_at__in=starts)
    if any(started is None for _, started in keys):
        when |= Q(started_at__isnull=True)
    return set(
        AgentccRequestLog._base_manager.filter(
            when,
            organization=org,
            request_id__in={request_id for request_id, _ in keys},
        ).values_list("request_id", "started_at")
    )


def _build_rows(org, logs):
    """The AgentccRequestLog rows of one org's entries; bad entries are logged."""
    objects = []
    for entry in logs:
        try:
            obj = AgentccRequestLog(
                organization=org,
                request_id=entry.get("request_id", ""),
                model=entry.get("model", ""),
                provider=entry.get("provider", ""),
                resolved_model=entry.get("resolved_model", ""),
                latency_ms=int(entry.get("latency_ms", 0)),
                started_at=_parse_datetime(
                    entry.get("timestamp") or entry.get("started_at")
                ),
                input_tokens=int(
                    entry.get("prompt_tokens") or entry.get("input_tokens") or 0
                ),
                output_tokens=int(
                    entry.get("completion_tokens") or entry.get("output_tokens") or 0
                ),
                total_tokens=int(entry.get("total_tokens", 0)),
                cost=_compute_cost(entry),
                status_code=int(entry.get("status_code", 0)),
                is_stream=bool(entry.get("is_stream", False)),
                is_error=bool(entry.get("is_error", False))
                or int(entry.get("status_code", 0)) >= 400,
                error_message=entry.get("error_message", ""),
                cache_hit=bool(entry.get("cache_hit", False)),
                fallback_used=bool(entry.get("fallback_used", False)),
                guardrail_triggered=bool(entry.get("guardrail_triggered", False)),
                api_key_id=_entry_key_id(entry),
                # The caller's OpenAI ``user`` field, cut to the column's
                # length so the entry is stored and metered.
                user_id=str(entry.get("user_id") or "")[:MAX_USER_ID_LEN],
                session_id=_sanitize_session_id(entry.get("session_id", "")),
                routing_strategy=entry.get("routing_strategy", ""),
                metadata=entry.get("metadata") or {},
                request_body=_truncate_json(entry.get("request_body")),
                response_body=_truncate_json(entry.get("response_body")),
                request_headers=_sanitize_headers(entry.get("request_headers")),
                response_headers=_sanitize_headers(entry.get("response_headers")),
                guardrail_results=entry.get("guardrail_results"),
            )
            objects.append(obj)
        except Exception:
            logger.exception(
                "failed_to_parse_log_entry",
                request_id=entry.get("request_id", ""),
                model=entry.get("model", ""),
            )
    return objects


def _emit_usage_events(org, objects):
    """Meter one org's stored rows as gateway_request and gateway_cache_hit."""
    try:
        try:
            from ee.usage.deployment import DeploymentMode
        except ImportError:
            DeploymentMode = None

        if not DeploymentMode.is_oss():
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.schemas.events import UsageEvent
            except ImportError:
                UsageEvent = None
            try:
                from ee.usage.services.emitter import emit
            except ImportError:
                emit = None

            cache_hits = sum(1 for o in objects if o.cache_hit)
            regular_requests = len(objects) - cache_hits

            if regular_requests > 0:
                emit(
                    UsageEvent(
                        org_id=str(org.id),
                        event_type=BillingEventType.GATEWAY_REQUEST,
                        amount=regular_requests,
                        properties={"batch_size": len(objects)},
                    )
                )
            if cache_hits > 0:
                emit(
                    UsageEvent(
                        org_id=str(org.id),
                        event_type=BillingEventType.GATEWAY_CACHE_HIT,
                        amount=cache_hits,
                    )
                )
    except Exception:
        pass  # Metering failure must not break log ingestion


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


KEY_ORG_FIELDS = ("gateway_key_id", "organization_id", "deleted")


def _entry_key_id(entry):
    """The gateway key ID an entry was authenticated with, or ""."""
    return str(entry.get("auth_key_id") or entry.get("api_key_id") or "")


def _group_by_org(logs):
    """Split a batch into (org, entries) groups by each entry's own API key.

    Returns the groups in first-seen order, and the skipped entries as
    (request_id, reason) pairs.
    """
    from accounts.models import Organization

    entries, skipped = [], []
    for entry in logs:
        if isinstance(entry, dict):
            entries.append(entry)
        else:
            skipped.append(("", "not_an_object"))

    key_org_ids = resolve_key_org_ids(
        AgentccAPIKey._base_manager.filter(
            gateway_key_id__in={_entry_key_id(entry) for entry in entries} - {""}
        ).values_list(*KEY_ORG_FIELDS)
    )
    orgs = Organization.objects.in_bulk(
        {org_id for org_id in key_org_ids.values() if org_id}
    )

    groups: dict = {}
    for entry in entries:
        key_id = _entry_key_id(entry)
        if not key_id:
            reason = "no_key"
        elif key_id not in key_org_ids:
            reason = "unknown_key"
        elif key_org_ids[key_id] is None:
            reason = "ambiguous_key"
        else:
            org = orgs.get(key_org_ids[key_id])
            if org is not None:
                groups.setdefault(org.id, (org, []))[1].append(entry)
                continue
            reason = "unknown_org"
        skipped.append((str(entry.get("request_id") or "")[:64], reason))
    return list(groups.values()), skipped


def resolve_key_org_ids(rows):
    """Map each gateway key ID to the org its AgentccAPIKey rows place it in.

    ``rows`` are (gateway_key_id, organization_id, deleted) tuples. A live row
    decides (at most one exists per key ID). A key with no live row is placed
    by its soft-deleted rows, so a deleted key's entries stay with its own
    org. A key whose rows name more than one org maps to None; a key with no
    row is left out.
    """
    live, deleted = defaultdict(set), defaultdict(set)
    for key_id, org_id, is_deleted in rows:
        (deleted if is_deleted else live)[key_id].add(org_id)
    resolved = {}
    for key_id in live.keys() | deleted.keys():
        candidates = live.get(key_id) or deleted[key_id]
        resolved[key_id] = next(iter(candidates)) if len(candidates) == 1 else None
    return resolved
