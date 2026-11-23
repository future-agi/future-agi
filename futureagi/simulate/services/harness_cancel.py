from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import requests
import structlog
from django.db import transaction
from django.utils import timezone

from simulate.models import HostedHarnessAttempt, HostedHarnessJob

logger = structlog.get_logger(__name__)

Confirmation = HostedHarnessJob.CancelConfirmation

CLEANUP_GRACE = timedelta(minutes=10)
PROVIDER_CHECK_WINDOW = timedelta(minutes=5)

_CONFIRMED = frozenset({Confirmation.SANDBOX_ABSENT, Confirmation.PROVIDER_CALL_ENDED})
_UNCHECKED = {
    "no_key": Confirmation.UNCHECKED_NO_KEY,
    "no_target": Confirmation.UNCHECKED_NO_TARGET,
    "no_calls": Confirmation.UNCHECKED_NO_CALLS,
    "too_many_calls": Confirmation.UNCHECKED_TOO_MANY_CALLS,
}
_UNCONFIRMED = frozenset(
    {Confirmation.PROVIDER_CALL_OPEN, Confirmation.PROVIDER_UNCHECKED}
    | set(_UNCHECKED.values())
)
_TERMINAL = frozenset(
    {
        HostedHarnessJob.State.CANCELED,
        HostedHarnessJob.State.COMPLETED,
        HostedHarnessJob.State.FAILED,
    }
)
_SANDBOX_SOURCES = frozenset({"github", "archive"})
_TARGET_FIELDS = {"vapi": "assistant_id", "retell": "agent_id"}
_KEY_ALIASES = {"vapi": "VAPI_API_KEY", "retell": "RETELL_API_KEY"}
_LIVEKIT = "livekit"
_PROVIDER_CONNECTORS = frozenset({*_TARGET_FIELDS, _LIVEKIT})
_PROVIDER_NAMES = {
    "vapi": "Vapi",
    "retell": "Retell",
    "livekit": "LiveKit",
    "phone": "the phone line",
}
_VAPI_CALLS_URL = "https://api.vapi.ai/call"
_VAPI_WEB_CALL = "vapi.websocketCall"
_VAPI_ENDED = frozenset({"ended"})
_VAPI_PAGE_LIMIT = 100
_RETELL_CALLS_URL = "https://api.retellai.com/v3/list-calls"
_RETELL_ENDED = frozenset({"ended", "not_connected", "error"})
_RETELL_PAGE_LIMIT = 1000
_REQUEST_TIMEOUT_SECONDS = 10


class ProviderCalls(StrEnum):
    ENDED = "ended"
    ACTIVE = "active"
    UNKNOWN = "unknown"
    NO_KEY = "no_key"
    NO_TARGET = "no_target"
    NO_CALLS = "no_calls"
    TOO_MANY_CALLS = "too_many_calls"


def unchecked_confirmation(outcome: ProviderCalls) -> str:
    return _UNCHECKED.get(str(outcome), Confirmation.PROVIDER_UNCHECKED)


class ProviderCallsActive(RuntimeError):
    pass


def _rank(by: str | None) -> int:
    if by in _CONFIRMED:
        return 3
    if by == Confirmation.CLEANUP_FAILED:
        return 2
    if by in _UNCONFIRMED:
        return 1
    return 0


def cancel_status(job: HostedHarnessJob) -> str | None:
    if job.cancel_requested_at is None:
        return None
    return {3: "confirmed", 2: "cleanup_failed", 1: "unconfirmed"}.get(
        _rank(job.cancel_confirmed_by), "requested"
    )


def stop_kind(payload: Mapping[str, Any]) -> str:
    from simulate.services.phone_telephony import uses_platform_phone_telephony

    source_kind = str((payload.get("source") or {}).get("kind") or "")
    connector = provider_connector(payload)
    if source_kind in _SANDBOX_SOURCES or connector == _LIVEKIT:
        return "sandbox"
    if connector or uses_platform_phone_telephony(payload):
        return "provider"
    return "endpoint"


def provider_name(connector: Any) -> str | None:
    return _PROVIDER_NAMES.get(str(connector or "").strip().lower())


def stop_summary(job: HostedHarnessJob) -> dict[str, Any] | None:
    if job.cancel_requested_at is None:
        return None
    payload = job.payload or {}
    kind = stop_kind(payload)
    connector = provider_connector(payload)
    agent = payload.get("agent") if isinstance(payload.get("agent"), Mapping) else {}
    by = job.cancel_confirmed_by or None
    reason = next((key for key, value in _UNCHECKED.items() if value == by), None)
    return {
        "status": cancel_status(job),
        "kind": kind,
        "provider": provider_name(agent.get("connector"))
        if kind == "provider" or connector == _LIVEKIT
        else None,
        "requested_at": job.cancel_requested_at.isoformat(),
        "confirmed_at": job.cancel_confirmed_at.isoformat()
        if job.cancel_confirmed_at
        else None,
        "confirmed_by": Confirmation.PROVIDER_UNCHECKED if reason else by,
        "reason": reason,
    }


def stop_summaries(test_execution_ids: Iterable[Any]) -> dict[str, dict[str, Any]]:
    jobs = HostedHarnessJob.no_workspace_objects.filter(
        test_execution_id__in=list(test_execution_ids),
        cancel_requested_at__isnull=False,
    ).only(
        "test_execution_id",
        "payload",
        "cancel_requested_at",
        "cancel_confirmed_at",
        "cancel_confirmed_by",
    )
    return {str(job.test_execution_id): stop_summary(job) for job in jobs}


def provider_connector(payload: Mapping[str, Any]) -> str | None:
    from simulate.services.phone_telephony import uses_platform_phone_telephony

    agent = payload.get("agent")
    if not isinstance(agent, Mapping):
        return None
    connector = str(agent.get("connector") or "").strip().lower()
    if connector not in _PROVIDER_CONNECTORS or uses_platform_phone_telephony(payload):
        return None
    return connector


def confirm_cancel(job: HostedHarnessJob) -> None:
    from simulate.services.phone_telephony import uses_platform_phone_telephony

    if (
        job.state not in _TERMINAL
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
    elif provider_connector(payload) is not None:
        from simulate.tasks.hosted_harness_cancel import schedule_provider_call_check

        job_id = str(job.id)
        transaction.on_commit(lambda: schedule_provider_call_check(job_id))


def record_confirmation(job_id: Any, by: str) -> bool:
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job_id
        )
        if _rank(by) <= _rank(locked.cancel_confirmed_by):
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
    payload = job.payload or {}
    connector = provider_connector(payload)
    if connector is None:
        return ProviderCalls.UNKNOWN
    config = payload["agent"].get("config") or {}
    if connector == _LIVEKIT:
        return _livekit_rooms(job, config)
    target = str(config.get(_TARGET_FIELDS[connector]) or "").strip()
    attempt = HostedHarnessAttempt.no_workspace_objects.filter(
        job=job, attempt_number=job.current_attempt_number
    ).first()
    if not target:
        return ProviderCalls.NO_TARGET
    if attempt is None:
        return ProviderCalls.UNKNOWN
    api_key = _job_secrets(job).get(_KEY_ALIASES[connector])
    if not api_key:
        return ProviderCalls.NO_KEY
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


def _job_secrets(job: HostedHarnessJob) -> dict[str, str]:
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.services.hosted_harness_gateway import PlatformSecretResolver

    try:
        resolved = PlatformSecretResolver().resolve(job)
    except HostedHarnessError as error:
        logger.warning(
            "hosted_harness_cancel_provider_key_unavailable",
            job_id=str(job.id),
            code=error.code,
        )
        return {}
    return {str(alias).upper(): value for alias, value in resolved.items()}


def _livekit_rooms(job: HostedHarnessJob, config: Mapping[str, Any]) -> ProviderCalls:
    from simulate.services.phone_telephony import (
        HostedPhoneRoomCleanupError,
        cleanup_livekit_rooms,
    )

    resolved = _job_secrets(job)
    url = str(resolved.get("LIVEKIT_URL") or config.get("livekit_url") or "").strip()
    api_key = str(resolved.get("LIVEKIT_API_KEY") or "").strip()
    api_secret = str(resolved.get("LIVEKIT_API_SECRET") or "").strip()
    if not (url and api_key and api_secret):
        return ProviderCalls.NO_KEY
    try:
        cleanup_livekit_rooms(
            room_prefixes=(f"harness-{str(job.id)[:8]}-",),
            url=url,
            api_key=api_key,
            api_secret=api_secret,
        )
    except (
        HostedPhoneRoomCleanupError,
        requests.RequestException,
        ValueError,
    ) as error:
        logger.warning(
            "hosted_harness_cancel_livekit_rooms_remain",
            job_id=str(job.id),
            error=type(error).__name__,
        )
        return ProviderCalls.ACTIVE
    return ProviderCalls.ENDED


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
    if not isinstance(calls, list):
        return ProviderCalls.UNKNOWN
    if len(calls) >= _VAPI_PAGE_LIMIT:
        return ProviderCalls.TOO_MANY_CALLS
    ours = [
        call
        for call in calls
        if isinstance(call, Mapping) and call.get("type") == _VAPI_WEB_CALL
    ]
    if not ours:
        return ProviderCalls.NO_CALLS
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
    if not isinstance(items, list):
        return ProviderCalls.UNKNOWN
    if body.get("has_more"):
        return ProviderCalls.TOO_MANY_CALLS
    if not items:
        return ProviderCalls.NO_CALLS
    if any(
        not isinstance(item, Mapping) or item.get("call_status") not in _RETELL_ENDED
        for item in items
    ):
        return ProviderCalls.ACTIVE
    return ProviderCalls.ENDED
