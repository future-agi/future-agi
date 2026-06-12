from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import requests
import structlog
from django.db import close_old_connections, transaction
from django.utils import timezone as django_timezone

from tfc.deployment_telemetry.buffer import (
    clear_buffer,
    delete_window,
    load_window,
    pending_windows,
    prune_expired_windows,
    store_window,
)
from tfc.deployment_telemetry.collectors import collect_counts
from tfc.deployment_telemetry.config import (
    MAX_PAYLOAD_BYTES,
    REGISTRATION_CLAIM_TIMEOUT_SECONDS,
    get_telemetry_interval_hours,
    get_telemetry_interval_seconds_override,
    get_telemetry_timeout_seconds,
    get_telemetry_url,
    is_self_hosted_deployment,
    telemetry_is_disabled,
)
from tfc.deployment_telemetry.models import DeploymentTelemetryState
from tfc.deployment_telemetry.payloads import (
    build_full_registration_payload,
    build_heartbeat_payload,
    build_minimal_registration_payload,
)
from tfc.deployment_telemetry.state import get_or_create_telemetry_state

logger = structlog.get_logger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_DELAYS_SECONDS = (0.2, 0.5)


def _post_payload(path: str, payload: dict) -> bool:
    if not is_self_hosted_deployment():
        return False

    body = json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    if len(body) > MAX_PAYLOAD_BYTES:
        logger.warning("deployment_telemetry_payload_too_large", endpoint=path)
        return False

    url = f"{get_telemetry_url()}{path}"
    timeout = get_telemetry_timeout_seconds()
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = requests.post(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            if 200 <= response.status_code < 300:
                return True
            logger.warning(
                "deployment_telemetry_request_failed",
                endpoint=path,
                status_code=response.status_code,
                attempt=attempt + 1,
            )
        except requests.RequestException:
            logger.warning(
                "deployment_telemetry_request_unreachable",
                endpoint=path,
                attempt=attempt + 1,
            )

        if attempt < len(_RETRY_DELAYS_SECONDS):
            time.sleep(_RETRY_DELAYS_SECONDS[attempt])
    return False


def _claim_registration() -> tuple[DeploymentTelemetryState, bool, bool]:
    state = get_or_create_telemetry_state()
    current_disabled = telemetry_is_disabled()
    desired_kind = (
        DeploymentTelemetryState.RegistrationKind.MINIMAL_DISABLED
        if current_disabled
        else DeploymentTelemetryState.RegistrationKind.FULL
    )
    now = django_timezone.now()
    stale_before = now - timedelta(seconds=REGISTRATION_CLAIM_TIMEOUT_SECONDS)

    with transaction.atomic():
        state = DeploymentTelemetryState.objects.select_for_update().get(pk=state.pk)
        state.telemetry_disabled = current_disabled
        needs_registration = (
            state.registered_at is None or state.registration_kind != desired_kind
        )
        if not needs_registration:
            state.save(update_fields=["telemetry_disabled", "updated_at"])
            return (
                state,
                False,
                desired_kind == DeploymentTelemetryState.RegistrationKind.FULL,
            )

        has_fresh_claim = (
            state.registration_status
            == DeploymentTelemetryState.RegistrationStatus.IN_PROGRESS
            and state.registration_claimed_at
            and state.registration_claimed_at > stale_before
        )
        if has_fresh_claim:
            state.save(update_fields=["telemetry_disabled", "updated_at"])
            return state, False, False

        state.registration_status = (
            DeploymentTelemetryState.RegistrationStatus.IN_PROGRESS
        )
        state.registration_claimed_at = now
        state.last_registration_attempt_at = now
        state.last_registration_error = ""
        state.save(
            update_fields=[
                "telemetry_disabled",
                "registration_status",
                "registration_claimed_at",
                "last_registration_attempt_at",
                "last_registration_error",
                "updated_at",
            ]
        )
        return state, True, False


def _release_registration_claim(instance_id: UUID, error: str = "") -> None:
    with transaction.atomic():
        state = DeploymentTelemetryState.objects.select_for_update().get(
            instance_id=instance_id
        )
        state.registration_status = DeploymentTelemetryState.RegistrationStatus.IDLE
        state.registration_claimed_at = None
        state.last_registration_error = error
        state.save(
            update_fields=[
                "registration_status",
                "registration_claimed_at",
                "last_registration_error",
                "updated_at",
            ]
        )


def _complete_registration(
    instance_id: UUID,
    registration_kind: str,
    payload: dict,
) -> None:
    now = django_timezone.now()
    with transaction.atomic():
        state = DeploymentTelemetryState.objects.select_for_update().get(
            instance_id=instance_id
        )
        state.telemetry_disabled = (
            registration_kind
            == DeploymentTelemetryState.RegistrationKind.MINIMAL_DISABLED
        )
        state.registered_at = now
        state.registration_kind = registration_kind
        state.registration_status = DeploymentTelemetryState.RegistrationStatus.IDLE
        state.registration_claimed_at = None
        state.registration_metadata = payload
        state.last_registration_error = ""
        state.last_reported_version = payload["version"]
        state.save(
            update_fields=[
                "telemetry_disabled",
                "registered_at",
                "registration_kind",
                "registration_status",
                "registration_claimed_at",
                "registration_metadata",
                "last_registration_error",
                "last_reported_version",
                "updated_at",
            ]
        )


def ensure_registration() -> tuple[bool, UUID | None]:
    """Returns (is_full_registered, instance_id)."""
    if not is_self_hosted_deployment():
        return False, None

    state, claimed, already_full = _claim_registration()
    if not claimed:
        return already_full, state.instance_id

    current_disabled = state.telemetry_disabled
    registration_kind = (
        DeploymentTelemetryState.RegistrationKind.MINIMAL_DISABLED
        if current_disabled
        else DeploymentTelemetryState.RegistrationKind.FULL
    )
    try:
        if current_disabled:
            payload = build_minimal_registration_payload(state.instance_id)
        else:
            payload = build_full_registration_payload(state.instance_id)
            if payload is None:
                _release_registration_claim(state.instance_id)
                return False, state.instance_id

        if not _post_payload("/telemetry/register/", payload):
            _release_registration_claim(state.instance_id, "request_failed")
            return False, state.instance_id

        _complete_registration(state.instance_id, registration_kind, payload)
        is_full = registration_kind == DeploymentTelemetryState.RegistrationKind.FULL
        return is_full, state.instance_id
    except Exception:
        logger.warning("deployment_telemetry_registration_failed")
        _release_registration_claim(state.instance_id, "internal_error")
        return False, state.instance_id


def attempt_registration() -> bool:
    close_old_connections()
    try:
        try:
            is_full, _ = ensure_registration()
            return is_full
        except Exception:
            logger.warning("deployment_telemetry_registration_failed")
            return False
    finally:
        close_old_connections()


def compute_previous_utc_window(
    now: datetime | None = None,
    interval_hours: int | None = None,
    interval_seconds: int | None = None,
) -> tuple[datetime, datetime]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    override = interval_seconds or get_telemetry_interval_seconds_override()
    if override:
        epoch = current.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed = int((current - epoch).total_seconds())
        boundary = (elapsed // override) * override
        window_end = epoch + timedelta(seconds=boundary)
        return window_end - timedelta(seconds=override), window_end
    interval = interval_hours or get_telemetry_interval_hours()
    boundary_hour = (current.hour // interval) * interval
    window_end = current.replace(
        hour=boundary_hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    return window_end - timedelta(hours=interval), window_end


def _record_heartbeat_attempt(instance_id: UUID) -> None:
    now = django_timezone.now()
    DeploymentTelemetryState.objects.filter(instance_id=instance_id).update(
        last_heartbeat_attempt_at=now,
        updated_at=now,
    )


def _record_heartbeat_success(payload: dict) -> None:
    now = django_timezone.now()
    window_start = datetime.fromisoformat(
        payload["window_start"].replace("Z", "+00:00")
    )
    window_end = datetime.fromisoformat(payload["window_end"].replace("Z", "+00:00"))
    DeploymentTelemetryState.objects.filter(instance_id=payload["instance_id"]).update(
        last_heartbeat_at=now,
        last_heartbeat_window_start=window_start,
        last_heartbeat_window_end=window_end,
        last_reported_version=payload["version"],
        updated_at=now,
    )


def _flush_buffer() -> tuple[int, bool]:
    sent_count = 0
    for path in pending_windows():
        payload = load_window(path)
        if payload is None:
            delete_window(path)
            continue

        try:
            instance_id = UUID(str(payload["instance_id"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            delete_window(path)
            continue

        _record_heartbeat_attempt(instance_id)
        if not _post_payload("/telemetry/heartbeat/", payload):
            return sent_count, False

        _record_heartbeat_success(payload)
        delete_window(path)
        sent_count += 1
    return sent_count, True


def _run_telemetry_cycle() -> dict:
    if not is_self_hosted_deployment():
        return {"skipped": True, "reason": "cloud"}

    registration_is_full, instance_id = ensure_registration()
    if telemetry_is_disabled():
        clear_buffer()
        return {"skipped": True, "reason": "disabled"}

    window_start, window_end = compute_previous_utc_window()
    counts = collect_counts(window_start, window_end)
    payload = build_heartbeat_payload(
        instance_id,
        window_start,
        window_end,
        counts,
    )
    store_window(window_start, window_end, payload)
    prune_expired_windows()

    if not registration_is_full:
        return {
            "sent": False,
            "buffered": True,
            "reason": "registration_incomplete",
        }

    sent_count, flush_complete = _flush_buffer()
    return {
        "sent": sent_count > 0,
        "sent_count": sent_count,
        "flush_complete": flush_complete,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }


def run_telemetry_cycle() -> dict:
    try:
        return _run_telemetry_cycle()
    except Exception:
        logger.warning("deployment_telemetry_cycle_failed")
        return {"sent": False, "error": "internal_error"}
