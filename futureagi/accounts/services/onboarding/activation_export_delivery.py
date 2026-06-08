from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from accounts.models import OnboardingPaidCloudActivationExportLog
from accounts.services.onboarding.activation_exporter import (
    assert_activation_export_payload_safe,
)

DEFAULT_DELIVERY_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class ActivationExportDeliveryConfig:
    endpoint_url: str
    shared_secret: str
    timeout_seconds: int


@dataclass(frozen=True)
class ActivationExportDeliveryResult:
    log_id: str
    status: str
    idempotency_key: str
    error: str | None = None


@dataclass(frozen=True)
class ActivationExportDeliveryBatchResult:
    run_id: uuid.UUID
    evaluated: int
    delivered: int
    failed: int
    skipped: int
    dry_run: bool
    status_counts: dict
    errors: list[dict]

    def to_payload(self):
        return {
            "run_id": str(self.run_id),
            "evaluated": self.evaluated,
            "delivered": self.delivered,
            "failed": self.failed,
            "skipped": self.skipped,
            "dry_run": self.dry_run,
            "status_counts": self.status_counts,
            "errors": self.errors,
        }


def activation_export_delivery_config(
    *,
    endpoint_url=None,
    shared_secret=None,
    timeout_seconds=None,
) -> ActivationExportDeliveryConfig:
    endpoint = endpoint_url or getattr(
        settings, "ONBOARDING_ACTIVATION_EXPORT_DELIVERY_URL", ""
    )
    secret = shared_secret
    if secret is None:
        secret = getattr(settings, "ONBOARDING_ACTIVATION_EXPORT_SHARED_SECRET", "")
    timeout = timeout_seconds or getattr(
        settings,
        "ONBOARDING_ACTIVATION_EXPORT_TIMEOUT_SECONDS",
        DEFAULT_DELIVERY_TIMEOUT_SECONDS,
    )

    if not endpoint:
        raise ImproperlyConfigured("Activation export delivery URL is not configured.")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https":
        raise ImproperlyConfigured("Activation export delivery URL must use HTTPS.")
    if not secret:
        raise ImproperlyConfigured(
            "Activation export delivery secret is not configured."
        )
    if not isinstance(timeout, int) or timeout < 1:
        raise ImproperlyConfigured(
            "Activation export delivery timeout must be a positive integer."
        )

    return ActivationExportDeliveryConfig(
        endpoint_url=endpoint,
        shared_secret=str(secret),
        timeout_seconds=timeout,
    )


def _json_bytes(payload) -> bytes:
    return json.dumps(
        payload,
        cls=DjangoJSONEncoder,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _delivery_signature(*, body: bytes, shared_secret: str) -> str:
    digest = hmac.new(
        shared_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def activation_export_delivery_payload(log):
    fact_payload = assert_activation_export_payload_safe(log.fact_payload or {})
    payload = {
        "type": "onboarding_activation_fact",
        "export_log_id": str(log.id),
        "idempotency_key": log.idempotency_key,
        "schema_version": log.schema_version,
        "event_cursor": log.event_cursor,
        "evaluated_at": log.evaluated_at,
        "fact": fact_payload,
    }
    return assert_activation_export_payload_safe(payload)


def _headers(*, log, body: bytes, config: ActivationExportDeliveryConfig):
    return {
        "content-type": "application/json",
        "x-futureagi-activation-export-id": str(log.id),
        "x-futureagi-activation-export-key": log.idempotency_key,
        "x-futureagi-activation-export-schema": log.schema_version,
        "x-futureagi-activation-export-signature": _delivery_signature(
            body=body,
            shared_secret=config.shared_secret,
        ),
    }


def _delivery_error(exc):
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code:
        return f"{exc.__class__.__name__}: HTTP {status_code}"
    if isinstance(exc, (ImproperlyConfigured, ValidationError)):
        return str(exc)[:240]
    return exc.__class__.__name__


def _merge_delivery_metadata(metadata, *, run_id, status, now, error=None):
    return {
        **(metadata or {}),
        "delivery": {
            "run_id": str(run_id),
            "status": status,
            "attempted_at": now.isoformat(),
            **({"error": error} if error else {}),
        },
    }


def deliver_activation_export_log(
    log,
    *,
    config: ActivationExportDeliveryConfig | None = None,
    dry_run=True,
    retry_failed=False,
    run_id=None,
    now=None,
) -> ActivationExportDeliveryResult:
    now = now or timezone.now()
    run_id = run_id or uuid.uuid4()
    deliverable_statuses = {OnboardingPaidCloudActivationExportLog.STATUS_READY}
    if retry_failed:
        deliverable_statuses.add(OnboardingPaidCloudActivationExportLog.STATUS_FAILED)
    if log.status not in deliverable_statuses:
        return ActivationExportDeliveryResult(
            log_id=str(log.id),
            status="skipped",
            idempotency_key=log.idempotency_key,
            error=f"status_{log.status}",
        )

    payload = activation_export_delivery_payload(log)
    if dry_run:
        return ActivationExportDeliveryResult(
            log_id=str(log.id),
            status="dry_run",
            idempotency_key=log.idempotency_key,
        )

    config = config or activation_export_delivery_config()
    body = _json_bytes(payload)
    try:
        response = requests.post(
            config.endpoint_url,
            data=body,
            headers=_headers(log=log, body=body, config=config),
            timeout=config.timeout_seconds,
        )
        response.raise_for_status()
    except Exception as exc:
        error = _delivery_error(exc)
        OnboardingPaidCloudActivationExportLog.no_workspace_objects.filter(
            id=log.id,
        ).update(
            status=OnboardingPaidCloudActivationExportLog.STATUS_FAILED,
            metadata=_merge_delivery_metadata(
                log.metadata,
                run_id=run_id,
                status=OnboardingPaidCloudActivationExportLog.STATUS_FAILED,
                now=now,
                error=error,
            ),
        )
        return ActivationExportDeliveryResult(
            log_id=str(log.id),
            status=OnboardingPaidCloudActivationExportLog.STATUS_FAILED,
            idempotency_key=log.idempotency_key,
            error=error,
        )

    OnboardingPaidCloudActivationExportLog.no_workspace_objects.filter(
        id=log.id,
    ).update(
        status=OnboardingPaidCloudActivationExportLog.STATUS_EXPORTED,
        exported_at=now,
        metadata=_merge_delivery_metadata(
            log.metadata,
            run_id=run_id,
            status=OnboardingPaidCloudActivationExportLog.STATUS_EXPORTED,
            now=now,
        ),
    )
    return ActivationExportDeliveryResult(
        log_id=str(log.id),
        status=OnboardingPaidCloudActivationExportLog.STATUS_EXPORTED,
        idempotency_key=log.idempotency_key,
    )


def _ready_export_logs(*, limit, retry_failed=False):
    statuses = [OnboardingPaidCloudActivationExportLog.STATUS_READY]
    if retry_failed:
        statuses.append(OnboardingPaidCloudActivationExportLog.STATUS_FAILED)
    return list(
        OnboardingPaidCloudActivationExportLog.no_workspace_objects.filter(
            status__in=statuses,
        )
        .select_related("organization", "workspace", "user")
        .order_by("evaluated_at", "created_at")[:limit]
    )


def run_onboarding_activation_export_delivery(
    *,
    limit=100,
    dry_run=True,
    retry_failed=False,
    endpoint_url=None,
    shared_secret=None,
    timeout_seconds=None,
    run_id=None,
    now=None,
):
    now = now or timezone.now()
    run_id = run_id or uuid.uuid4()
    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    config = None
    if not dry_run:
        config = activation_export_delivery_config(
            endpoint_url=endpoint_url,
            shared_secret=shared_secret,
            timeout_seconds=timeout_seconds,
        )

    status_counts = Counter()
    errors = []
    delivered = 0
    failed = 0
    skipped = 0
    logs = _ready_export_logs(limit=limit, retry_failed=retry_failed)

    for log in logs:
        result = deliver_activation_export_log(
            log,
            config=config,
            dry_run=dry_run,
            retry_failed=retry_failed,
            run_id=run_id,
            now=now,
        )
        status_counts[result.status] += 1
        if result.status == OnboardingPaidCloudActivationExportLog.STATUS_EXPORTED:
            delivered += 1
        elif result.status == OnboardingPaidCloudActivationExportLog.STATUS_FAILED:
            failed += 1
        elif result.status == "skipped":
            skipped += 1
        if result.error:
            errors.append(
                {
                    "log_id": result.log_id,
                    "idempotency_key": result.idempotency_key,
                    "error": result.error,
                }
            )

    return ActivationExportDeliveryBatchResult(
        run_id=run_id,
        evaluated=len(logs),
        delivered=delivered,
        failed=failed,
        skipped=skipped,
        dry_run=dry_run,
        status_counts=dict(status_counts),
        errors=errors,
    )
