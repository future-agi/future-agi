from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import requests
import structlog
from django.db import transaction
from django.utils import timezone

from simulate.models import HostedHarnessAttempt, HostedHarnessJob

logger = structlog.get_logger(__name__)

Confirmation = HostedHarnessJob.CancelConfirmation

CLEANUP_GRACE = timedelta(minutes=10)

_CONFIRMED = frozenset({Confirmation.SANDBOX_ABSENT, Confirmation.PROVIDER_CALL_ENDED})
_SANDBOX_SOURCES = frozenset({"github", "archive"})
_TARGET_FIELDS = {"vapi": "assistant_id", "retell": "agent_id"}
_KEY_ALIASES = {"vapi": "VAPI_API_KEY", "retell": "RETELL_API_KEY"}
_VAPI_CALLS_URL = "https://api.vapi.ai/call"
_VAPI_WEB_CALL = "vapi.websocketCall"
_VAPI_ENDED = frozenset({"ended"})
_VAPI_PAGE_LIMIT = 100
_RETELL_CALLS_URL = "https://api.retellai.com/v3/list-calls"
_RETELL_ENDED = frozenset({"ended", "not_connected", "error"})
_RETELL_PAGE_LIMIT = 1000
_REQUEST_TIMEOUT_SECONDS = 10


class ProviderCalls(str, Enum):
    ENDED = "ended"
    ACTIVE = "active"
    UNKNOWN = "unknown"


class ProviderCallsActive(RuntimeError):
    pass


def cancel_status(job: HostedHarnessJob) -> str | None:
    if job.cancel_requested_at is None:
        return None
    if job.cancel_confirmed_by == Confirmation.CLEANUP_FAILED:
        return "cleanup_failed"
    if job.cancel_confirmed_at is not None:
        return "confirmed"
    return "requested"


def web_call_connector(payload: Mapping[str, Any]) -> str | None:
    from simulate.services.phone_telephony import uses_platform_phone_telephony

    agent = payload.get("agent")
    if not isinstance(agent, Mapping):
        return None
    connector = str(agent.get("connector") or "").strip().lower()
    if connector not in _TARGET_FIELDS or uses_platform_phone_telephony(payload):
        return None
    return connector


def confirm_cancel(job: HostedHarnessJob) -> None:
    from simulate.services.phone_telephony import uses_platform_phone_telephony

    if (
        job.state != HostedHarnessJob.State.CANCELED
        or job.cancel_requested_at is None
        or job.cancel_confirmed_by in _CONFIRMED
    ):
        return
    payload = job.payload or {}
    source_kind = str((payload.get("source") or {}).get("kind") or "")
    if source_kind in _SANDBOX_SOURCES:
        record_confirmation(job.id, Confirmation.SANDBOX_ABSENT)
    elif uses_platform_phone_telephony(payload):
        record_confirmation(job.id, Confirmation.PROVIDER_CALL_ENDED)
    elif web_call_connector(payload) is not None:
        from simulate.tasks.hosted_harness_cancel import schedule_provider_call_check

        job_id = str(job.id)
        transaction.on_commit(lambda: schedule_provider_call_check(job_id))


def record_confirmation(job_id: Any, by: str) -> bool:
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job_id
        )
        if locked.cancel_confirmed_by in _CONFIRMED or (
            by == Confirmation.CLEANUP_FAILED and locked.cancel_confirmed_at is not None
        ):
            return False
        locked.cancel_confirmed_at = timezone.now()
        locked.cancel_confirmed_by = by
        locked.save(
            update_fields=["cancel_confirmed_at", "cancel_confirmed_by", "updated_at"]
        )
    logger.info("hosted_harness_cancel_confirmed", job_id=str(job_id), by=by)
    return True


def record_overdue_cleanup_failure(attempt_id: Any) -> bool:
    job = (
        HostedHarnessJob.no_workspace_objects.filter(attempts__id=attempt_id)
        .only("id", "cancel_requested_at")
        .first()
    )
    if (
        job is None
        or job.cancel_requested_at is None
        or timezone.now() - job.cancel_requested_at < CLEANUP_GRACE
    ):
        return False
    return record_confirmation(job.id, Confirmation.CLEANUP_FAILED)


def provider_calls(job: HostedHarnessJob) -> ProviderCalls:
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.services.hosted_harness_gateway import PlatformSecretResolver

    payload = job.payload or {}
    connector = web_call_connector(payload)
    if connector is None:
        return ProviderCalls.UNKNOWN
    config = payload["agent"].get("config") or {}
    target = str(config.get(_TARGET_FIELDS[connector]) or "").strip()
    attempt = HostedHarnessAttempt.no_workspace_objects.filter(
        job=job, attempt_number=job.current_attempt_number
    ).first()
    if not target or attempt is None:
        return ProviderCalls.UNKNOWN
    try:
        api_key = PlatformSecretResolver().resolve(job).get(_KEY_ALIASES[connector])
    except HostedHarnessError as error:
        logger.warning(
            "hosted_harness_cancel_provider_key_unavailable",
            job_id=str(job.id),
            code=error.code,
        )
        return ProviderCalls.UNKNOWN
    if not api_key:
        return ProviderCalls.UNKNOWN
    reader = _vapi_calls if connector == "vapi" else _retell_calls
    try:
        return reader(api_key, target, attempt.created_at)
    except (requests.RequestException, ValueError) as error:
        logger.warning(
            "hosted_harness_cancel_provider_unreachable",
            job_id=str(job.id),
            connector=connector,
            error=type(error).__name__,
        )
        return ProviderCalls.ACTIVE


def _vapi_calls(api_key: str, assistant_id: str, since: datetime) -> ProviderCalls:
    response = requests.get(
        _VAPI_CALLS_URL,
        params={
            "assistantId": assistant_id,
            "createdAtGe": since.isoformat(),
            "limit": _VAPI_PAGE_LIMIT,
        },
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    calls = response.json()
    if not isinstance(calls, list) or len(calls) >= _VAPI_PAGE_LIMIT:
        return ProviderCalls.UNKNOWN
    ours = [
        call
        for call in calls
        if isinstance(call, Mapping) and call.get("type") == _VAPI_WEB_CALL
    ]
    if not ours:
        return ProviderCalls.UNKNOWN
    if any(call.get("status") not in _VAPI_ENDED for call in ours):
        return ProviderCalls.ACTIVE
    return ProviderCalls.ENDED


def _retell_calls(api_key: str, agent_id: str, since: datetime) -> ProviderCalls:
    response = requests.post(
        _RETELL_CALLS_URL,
        json={
            "filter_criteria": {
                "agent": [{"agent_id": agent_id}],
                "call_type": {"type": "enum", "op": "in", "value": ["web_call"]},
                "start_timestamp": {
                    "type": "number",
                    "op": "ge",
                    "value": int(since.timestamp() * 1000),
                },
            },
            "limit": _RETELL_PAGE_LIMIT,
        },
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    items = body.get("items") if isinstance(body, Mapping) else None
    if not isinstance(items, list) or not items or body.get("has_more"):
        return ProviderCalls.UNKNOWN
    if any(
        not isinstance(item, Mapping) or item.get("call_status") not in _RETELL_ENDED
        for item in items
    ):
        return ProviderCalls.ACTIVE
    return ProviderCalls.ENDED
