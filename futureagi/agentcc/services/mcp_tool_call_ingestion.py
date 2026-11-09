"""Replay-safe persistence for gateway ``mcp-tool-call-v1`` records.

Tool-call records share the gateway log webhook with request logs and outcome
records, but they are their own accounting stream: they create
``AgentccToolCall`` rows and never create request usage events.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import structlog
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction
from django.utils import timezone

from accounts.models import Organization
from agentcc.models import AgentccToolCall
from agentcc.models.mcp_credential import URL_MAX_LENGTH

logger = structlog.get_logger(__name__)

# PC.6 is deliberately a closed set.  Other fields in a gateway record are
# either columns above or ignored, so a future gateway field cannot end up in
# an unreviewed JSON column by accident.
_EVIDENCE_FIELDS = ("identity", "account_label", "policy", "flow", "shaped")


def ingest(records: Sequence[object]) -> int:
    """Insert valid tool-call records and return the number newly inserted.

    ``event_id`` is the replay key.  Records lacking an event id or an
    ``org_id`` are rejected independently, including when an API key could
    otherwise identify an organization.  Rejections are logged and do not
    make a mixed request/outcome batch fail.
    """
    if not records:
        return 0

    organizations = {}
    valid = []
    seen_valid_event_ids = set()
    for record in records:
        if not isinstance(record, dict):
            logger.warning("mcp_tool_call_rejected", reason="not_an_object")
            continue
        event_id = _identifier(record.get("event_id"), 64)
        org_id = _identifier(record.get("org_id"), 64)
        if not event_id:
            logger.warning("mcp_tool_call_rejected", reason="missing_event_id")
            continue
        if not org_id:
            logger.warning(
                "mcp_tool_call_rejected", event_id=event_id, reason="missing_org_id"
            )
            continue
        organization = organizations.get(org_id)
        if org_id not in organizations:
            try:
                organization = Organization.objects.filter(id=org_id).first()
            except (ValueError, ValidationError):
                organization = None
            organizations[org_id] = organization
        if organization is None:
            logger.warning(
                "mcp_tool_call_rejected", event_id=event_id, reason="unknown_org_id"
            )
            continue
        if event_id in seen_valid_event_ids:
            logger.info(
                "mcp_tool_call_duplicate",
                event_id=event_id,
                reason="duplicate_in_batch",
            )
            continue
        seen_valid_event_ids.add(event_id)
        valid.append((event_id, organization, record))

    if not valid:
        return 0

    by_organization = {}
    for event_id, organization, record in valid:
        bucket = by_organization.setdefault(organization.pk, (organization, []))
        bucket[1].append((event_id, record))

    inserted = 0
    first_failure = None
    for organization, organization_records in by_organization.values():
        try:
            inserted += _ingest_organization(organization, organization_records)
        except Exception as exc:
            # Keep trying later tenants before reporting a failed partition to the
            # shared webhook caller.  Do not include database error text: it can
            # contain a customer's JSON value or SQL literal.
            first_failure = first_failure or exc
            logger.warning(
                "mcp_tool_call_org_failed",
                organization_id=str(organization.pk),
                row_count=len(organization_records),
                error_type=type(exc).__name__,
            )
    if first_failure is not None:
        raise first_failure
    return inserted


def _ingest_organization(organization, records):
    """Insert one organization's rows without poisoning other organizations.

    The webhook batch can contain records for many tenants.  Keep each tenant
    in its own transaction, and use a savepoint before the bulk insert so a
    database-level rejection can be isolated to the offending row(s).
    """
    event_ids = {event_id for event_id, _ in records}
    with transaction.atomic():
        existing = set(
            AgentccToolCall.objects.filter(
                organization=organization, event_id__in=event_ids
            ).values_list("event_id", flat=True)
        )
        # Reuse the log-ingestion column guard so out-of-range integers and
        # oversized strings cannot turn one tenant's batch into a 5xx.  Mapping
        # is per record: one malformed JSON value must not discard this tenant's
        # other valid calls.
        from agentcc.services.log_ingestion import _fit_to_columns

        rows = []
        for event_id, record in records:
            if event_id in existing:
                continue
            try:
                row = _row(event_id, organization, record)
                _fit_to_columns(row)
            except Exception as exc:
                logger.warning(
                    "mcp_tool_call_rejected",
                    event_id=event_id,
                    reason="unmappable",
                    error_type=type(exc).__name__,
                )
                continue
            rows.append(row)

        if not rows:
            return 0

        try:
            with transaction.atomic():
                AgentccToolCall.objects.bulk_create(rows, ignore_conflicts=True)
        except (DataError, IntegrityError):
            logger.exception(
                "mcp_tool_call_bulk_insert_failed",
                organization_id=str(organization.pk),
                row_count=len(rows),
            )
            stored = []
            for row in rows:
                try:
                    with transaction.atomic():
                        AgentccToolCall.objects.bulk_create(
                            [row], ignore_conflicts=True
                        )
                    stored.append(row.event_id)
                except (DataError, IntegrityError):
                    logger.exception(
                        "mcp_tool_call_row_rejected",
                        organization_id=str(organization.pk),
                        event_id=row.event_id,
                    )
            _queue_exports(organization, stored)
            return len(stored)
        _queue_exports(organization, [row.event_id for row in rows])
    return len(rows)


def _queue_exports(organization, event_ids):
    """Queue the stored calls' SIEM exports in this transaction (G16-SIEM).

    A queue failure is logged and never fails or rolls back the ingestion: the
    calls are the accounting record, their export is best effort on top.
    """
    if not event_ids:
        return
    from agentcc.services import siem_delivery

    try:
        with transaction.atomic():
            siem_delivery.enqueue(organization, event_ids)
    except Exception as exc:
        logger.warning(
            "siem_export_queue_failed",
            organization_id=str(organization.pk),
            row_count=len(event_ids),
            error_type=type(exc).__name__,
        )


# Keep the descriptive name for callers that used the first lane draft; the
# contract-facing entry point is ``ingest``.
ingest_tool_calls = ingest


def _row(event_id, organization, record):
    """Map one validated event into the Django model without trusting shape."""
    agent = record.get("agent") if isinstance(record.get("agent"), dict) else {}
    content_mode = _text(record.get("content_mode"), 8)
    full_content = content_mode == "full"
    return AgentccToolCall(
        organization=organization,
        event_id=event_id,
        tool_call_id=_text(record.get("tool_call_id"), 64),
        request_id=_text(record.get("request_id"), 64),
        generation_id=_text(record.get("generation_id"), 64),
        ts_ms=_non_negative_int(record.get("ts_ms")),
        started_at=_started_at(record),
        api_key_id=_text(record.get("api_key_id"), 255),
        principal_kind=_text(record.get("principal_kind"), 16),
        user_id=_text(record.get("user_id"), 255),
        client_id=_text(record.get("client_id"), URL_MAX_LENGTH),
        virtual_server_id=_text(record.get("virtual_server_id"), 64),
        server_id=_text(record.get("server_id"), 64),
        tool=_text(record.get("tool"), 255),
        upstream_tool=_text(record.get("upstream_tool"), 255),
        tool_revision=_text(record.get("tool_revision"), 64),
        via=_text(record.get("via"), 32),
        era=_text(record.get("era"), 16),
        protocol_version=_text(record.get("protocol_version"), 16),
        session_hash=_text(record.get("session_hash"), 64),
        agent_framework=_text(agent.get("framework"), 32),
        agent_id=_text(agent.get("agent_id"), 128),
        agent_version=_text(agent.get("agent_version"), 128),
        agent_run_id=_text(agent.get("run_id"), 128),
        agent_parent_run_id=_text(agent.get("parent_run_id"), 128),
        agent_root_run_id=_text(agent.get("root_run_id"), 128),
        agent_task_id=_text(agent.get("task_id"), 128),
        agent_trace_id=_text(agent.get("trace_id"), 128),
        agent_workflow=_text(agent.get("workflow"), 128),
        agent_step=_text(agent.get("step"), 32),
        agent_turn=_non_negative_int(agent.get("turn")),
        agent_identity_source=_text(agent.get("identity_source"), 32),
        session_id=_text(agent.get("session_id"), 255),
        outcome=_text(record.get("outcome"), 16),
        is_error=_boolean(record.get("is_error")),
        error_class=_text(record.get("error_class"), 32),
        error_origin=_text(record.get("error_origin"), 16),
        error_code=_text(record.get("error_code"), 64),
        error_message=_error_message(record.get("error_message")),
        approval_id=_text(record.get("approval_id"), 64),
        guardrail=_dict_or_none(record.get("guardrail")),
        latency_ms=_non_negative_int(record.get("latency_ms")),
        upstream_latency_ms=_non_negative_int(record.get("upstream_latency_ms")),
        gateway_overhead_ms=_non_negative_int(record.get("gateway_overhead_ms")),
        bytes_in=_non_negative_int(record.get("bytes_in")),
        bytes_out=_non_negative_int(record.get("bytes_out")),
        billing_mode=_text(record.get("billing_mode"), 16),
        credential_type=_text(record.get("credential_type"), 8),
        credential_source=_text(record.get("credential_source"), 16),
        fallback_from=_text(record.get("fallback_from"), 16),
        upstream_cost_micros=_non_negative_int(record.get("upstream_cost_micros"), 0),
        billed_cost_micros=_non_negative_int(record.get("billed_cost_micros"), 0),
        markup_micros=_non_negative_int(record.get("markup_micros"), 0),
        markup_bps=_non_negative_int(record.get("markup_bps"), 0),
        cost_source=_text(record.get("cost_source"), 32),
        usage_estimated=_boolean(record.get("usage_estimated")),
        dispatched=_boolean(record.get("dispatched")),
        budget_scope=_text(record.get("budget_scope"), 128),
        content_mode=content_mode,
        arguments=_content(record.get("arguments")) if full_content else None,
        result=_content(record.get("result")) if full_content else None,
        evidence=_evidence(record),
    )


def _evidence(record):
    evidence = {}
    for field in _EVIDENCE_FIELDS:
        value = record.get(field)
        if value is None or value == "":
            continue
        if field == "account_label":
            value = _text(value, 256)
            if not value:
                continue
        elif not isinstance(value, dict):
            continue
        evidence[field] = _sanitize_json(value)
    return evidence or None


def _started_at(record):
    """Return a non-null timestamp for cursor pagination.

    The gateway normally sends ``started_at``.  Older or hand-authored
    records may omit it, so fall back to the event timestamp and finally to
    ingestion time rather than persisting a NULL cursor position.
    """
    parsed = _parse_datetime(record.get("started_at"))
    if parsed is not None:
        return parsed
    ts_ms = _non_negative_int(record.get("ts_ms"))
    if ts_ms is not None:
        try:
            return datetime.fromtimestamp(ts_ms / 1000, UTC)
        except (OverflowError, OSError, ValueError):
            pass
    return timezone.now()


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _identifier(value, max_length):
    text = _text(value, None)
    return text if text and len(text) <= max_length else ""


def _text(value, max_length):
    if value is None or isinstance(value, bool | dict | list):
        return ""
    text = _sanitize_text(str(value))
    if not text:
        return ""
    if max_length is not None and len(text) > max_length:
        return ""
    return text


def _error_message(value):
    text = _text(value, None)
    return text[:512]


def _non_negative_int(value, default=None):
    if value is None or isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if number >= 0 else default


def _boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes"}


def _content(value):
    """Store gateway content as sent; the gateway owns the 16 KiB policy."""
    value = _dict_or_value(value)
    return _sanitize_json(value) if value is not None else None


def _sanitize_text(value):
    """Replace PostgreSQL-invalid NULs and lone UTF-16 surrogates."""
    return "".join(
        "�" if char == "\x00" or 0xD800 <= ord(char) <= 0xDFFF else char
        for char in value
    )


def _sanitize_json(value):
    """Sanitize JSON-compatible values without consuming Python recursion depth."""
    if isinstance(value, str):
        return _sanitize_text(value)
    if not isinstance(value, (dict, list, tuple)):
        return value

    def new_container(container):
        return {} if isinstance(container, dict) else []

    sanitized = new_container(value)
    stack = [(value, sanitized)]
    while stack:
        current, target = stack.pop()
        if isinstance(current, dict):
            for key, item in current.items():
                safe_key = _sanitize_text(key) if isinstance(key, str) else key
                if isinstance(item, str):
                    target[safe_key] = _sanitize_text(item)
                elif isinstance(item, (dict, list, tuple)):
                    child = new_container(item)
                    target[safe_key] = child
                    stack.append((item, child))
                else:
                    target[safe_key] = item
            continue

        for item in current:
            if isinstance(item, str):
                target.append(_sanitize_text(item))
            elif isinstance(item, (dict, list, tuple)):
                child = new_container(item)
                target.append(child)
                stack.append((item, child))
            else:
                target.append(item)
    return sanitized


def _dict_or_none(value):
    return _sanitize_json(value) if isinstance(value, dict) else None


def _dict_or_value(value):
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return None
