import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import structlog

from agentcc.models import AgentccAPIKey, AgentccRequestLog, AgentccSession
from tfc.billing.boundary import get_billing, BillingEventType

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
        return

    webhooks_by_event: dict = {}
    for webhook in webhooks:
        for event_type in webhook.events or []:
            webhooks_by_event.setdefault(event_type, []).append(webhook)

    if not webhooks_by_event:
        return

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

        from agentcc.services.webhook_delivery import deliver_webhook_events

        try:
            deliver_webhook_events(org_id=org.id, limit=max(len(events_to_create), 100))
        except Exception:
            logger.exception("webhook_immediate_delivery_failed", org_id=str(org.id))


MAX_BODY_SIZE = 65536  # 64KB
MAX_SESSION_ID_LEN = 255  # matches VARCHAR(255) DB column


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
    if not headers or not isinstance(headers, dict):
        return headers
    sensitive_keys = {"authorization", "x-api-key", "api-key", "x-auth-token"}
    return {k: "***" if k.lower() in sensitive_keys else v for k, v in headers.items()}


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


def ingest_request_logs(logs):
    if not logs:
        return 0

    org = _resolve_org(logs)
    if not org:
        logger.warning("log_ingestion_skipped_no_org", log_count=len(logs))
        return 0

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
                api_key_id=entry.get("auth_key_id") or entry.get("api_key_id") or "",
                user_id=entry.get("user_id", ""),
                session_id=_sanitize_session_id(entry.get("session_id", "")),
                routing_strategy=entry.get("routing_strategy", ""),
                metadata=entry.get("metadata") or {},
                request_body=_truncate_json(entry.get("request_body")),
                response_body=_truncate_json(entry.get("response_body")),
                request_headers=_sanitize_headers(entry.get("request_headers")),
                response_headers=entry.get("response_headers"),
                guardrail_results=entry.get("guardrail_results"),
            )
            objects.append(obj)
        except Exception:
            logger.exception(
                "failed_to_parse_log_entry",
                request_id=(
                    entry.get("request_id", "") if isinstance(entry, dict) else ""
                ),
                model=entry.get("model", "") if isinstance(entry, dict) else "",
            )

    if objects:
        AgentccRequestLog.no_workspace_objects.bulk_create(objects, ignore_conflicts=True)

    try:
        _dispatch_webhook_events(org, logs)
    except Exception:
        logger.exception("webhook_dispatch_failed", org_id=str(org.id))

    try:
        billing = get_billing()
        cache_hits = sum(1 for o in objects if o.cache_hit)
        regular_requests = len(objects) - cache_hits

        if regular_requests > 0:
            billing.record_usage(
                str(org.id),
                BillingEventType.GATEWAY_REQUEST,
                amount=regular_requests,
                batch_size=len(objects),
            )
        if cache_hits > 0:
            billing.record_usage(
                str(org.id),
                BillingEventType.GATEWAY_CACHE_HIT,
                amount=cache_hits,
            )
    except Exception:
        pass  # Metering failure must not break log ingestion

    try:
        _ensure_sessions_exist(org, logs)
    except Exception:
        logger.exception("ensure_sessions_failed", org_id=str(org.id))

    api_key_ids = {
        entry.get("api_key_id") or entry.get("auth_key_id")
        for entry in logs
        if isinstance(entry, dict)
        and (entry.get("api_key_id") or entry.get("auth_key_id"))
    }
    if api_key_ids:
        now = datetime.now(timezone.utc)
        AgentccAPIKey.no_workspace_objects.filter(
            gateway_key_id__in=api_key_ids,
        ).update(last_used_at=now)

    return len(objects)


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


def _resolve_org(logs):
    from accounts.models import Organization

    for entry in logs:
        if not isinstance(entry, dict):
            continue
        key_id = entry.get("auth_key_id") or entry.get("api_key_id")
        if key_id:
            api_key = (
                AgentccAPIKey.no_workspace_objects.filter(
                    gateway_key_id=key_id,
                )
                .select_related("organization")
                .first()
            )
            if api_key and api_key.organization:
                return api_key.organization

        metadata = entry.get("metadata") or {}
        org_id = metadata.get("org_id")
        if org_id:
            try:
                return Organization.objects.get(id=org_id)
            except Organization.DoesNotExist:
                pass

    return None
