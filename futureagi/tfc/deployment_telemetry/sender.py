from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID

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
    REGISTRATION_CLAIM_TIMEOUT_SECONDS,
    get_telemetry_interval_hours,
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
from tfc.deployment_telemetry.transport import TelemetryClient

logger = structlog.get_logger(__name__)


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
        # A FULL deployment with no stored signing secret can't sign its
        # heartbeats, so force a re-registration to (re)obtain one rather
        # than letting it emit heartbeats the receiver will reject.
        missing_secret_for_full = (
            desired_kind == DeploymentTelemetryState.RegistrationKind.FULL
            and not state.instance_secret
        )
        needs_registration = (
            state.registered_at is None
            or state.registration_kind != desired_kind
            or missing_secret_for_full
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
    instance_secret: str | None = None,
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
        update_fields = [
            "telemetry_disabled",
            "registered_at",
            "registration_kind",
            "registration_status",
            "registration_claimed_at",
            "registration_metadata",
            "last_registration_error",
            "updated_at",
        ]
        # The receiver mints the signing secret once and returns it on that
        # registration. Persist it; never overwrite a stored secret with an
        # empty value (re-registrations don't re-issue it).
        if instance_secret and not state.instance_secret:
            state.instance_secret = instance_secret
            update_fields.append("instance_secret")
        state.save(update_fields=update_fields)


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
                # No admin/owner users on this instance yet (or every candidate
                # has an unusable email). Releasing the claim with no other
                # signal would re-fire forever with no log trail — emit a one-
                # line warning so operators see why registration keeps deferring.
                logger.warning(
                    "deployment_telemetry_skipped_no_admin_users",
                    instance_id=str(state.instance_id),
                )
                _release_registration_claim(state.instance_id)
                return False, state.instance_id

        # Registration is unsigned: it's the TOFU first contact that
        # establishes the secret. The response carries the minted secret.
        response = TelemetryClient().post("/telemetry/register/", payload)
        if not response.ok:
            _release_registration_claim(state.instance_id, "request_failed")
            return False, state.instance_id

        issued_secret = (response.data or {}).get("instance_secret")
        # A FULL registration must come away with a signing secret or every
        # heartbeat it sends will be rejected (the receiver requires a valid
        # HMAC). If the response carried none and we don't already have one
        # stored, treat the registration as incomplete and retry next cycle
        # rather than marking ourselves registered and emitting unsigned
        # heartbeats forever.
        is_full = registration_kind == DeploymentTelemetryState.RegistrationKind.FULL
        if is_full and not issued_secret and not state.instance_secret:
            _release_registration_claim(state.instance_id, "missing_secret")
            return False, state.instance_id

        _complete_registration(
            state.instance_id,
            registration_kind,
            payload,
            instance_secret=issued_secret,
        )
        return is_full, state.instance_id
    except Exception:
        logger.warning("deployment_telemetry_registration_failed", exc_info=True)
        _release_registration_claim(state.instance_id, "internal_error")
        return False, state.instance_id


_disclosure_lock = threading.Lock()
_disclosure_logged = False


def _log_disclosure() -> None:
    """Emit a once-per-process disclosure of what telemetry sends."""
    global _disclosure_logged
    with _disclosure_lock:
        if _disclosure_logged:
            return
        _disclosure_logged = True

    if telemetry_is_disabled():
        logger.info(
            "deployment_telemetry_disclosure",
            mode="opt_out",
            sends="one minimal registration ping (instance id + version); "
            "no emails, no heartbeats",
            opt_out_env="FUTURE_AGI_TELEMETRY_DISABLED=true (already set)",
        )
    else:
        logger.info(
            "deployment_telemetry_disclosure",
            mode="enabled",
            sends="registration (active admin user emails + domains) and periodic "
            "usage-count heartbeats; never usage content",
            url=get_telemetry_url(),
            opt_out_env="FUTURE_AGI_TELEMETRY_DISABLED=true",
        )


def attempt_registration() -> bool:
    close_old_connections()
    try:
        try:
            if is_self_hosted_deployment():
                _log_disclosure()
            is_full, _ = ensure_registration()
            return is_full
        except Exception:
            logger.warning(
                "deployment_telemetry_registration_failed", exc_info=True
            )
            return False
    finally:
        close_old_connections()


def compute_previous_utc_window(
    now: datetime | None = None,
    interval_hours: int | None = None,
) -> tuple[datetime, datetime]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
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
        updated_at=now,
    )


def _flush_buffer() -> tuple[int, bool]:
    sent_count = 0
    # The signing secret is per-instance (single singleton state row), so
    # load it once and reuse the signed client for every buffered window.
    secret = DeploymentTelemetryState.objects.values_list(
        "instance_secret", flat=True
    ).first()
    if not secret:
        # Without a secret every heartbeat will be rejected with 401, but
        # ``request_failed`` log lines give no clue why. Surface the root
        # cause once per flush and leave the buffer intact for the next
        # cycle, which will retry registration and (re)mint a secret.
        logger.error("deployment_telemetry_secret_missing_at_flush")
        return 0, False
    client = TelemetryClient(secret=secret)
    for path in pending_windows():
        payload = load_window(path)
        if payload is None:
            delete_window(path)
            continue

        try:
            instance_id = UUID(str(payload["instance_id"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            # A buffered window with a malformed instance_id can never resend,
            # so we drop it — but log first so a later "missing window"
            # investigation has a trail.
            logger.warning(
                "deployment_telemetry_buffer_corrupt",
                path=str(path),
            )
            delete_window(path)
            continue

        _record_heartbeat_attempt(instance_id)
        if not client.post("/telemetry/heartbeat/", payload).ok:
            return sent_count, False

        _record_heartbeat_success(payload)
        delete_window(path)
        sent_count += 1
    return sent_count, True


def _run_telemetry_cycle() -> dict:
    if not is_self_hosted_deployment():
        return {"skipped": True, "reason": "cloud"}

    # Disclose from the scheduled cycle too (deduped once per process), so an
    # install that never sees a fresh signup still logs what telemetry sends.
    _log_disclosure()

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
        logger.warning("deployment_telemetry_cycle_failed", exc_info=True)
        return {"sent": False, "error": "internal_error"}
