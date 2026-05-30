from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError
from django.utils import timezone

from accounts.models import (
    OnboardingActivationFactReceipt,
    OnboardingActivationFactReceiptRejection,
)
from accounts.services.onboarding.activation_exporter import (
    ACTIVATION_EXPORT_SCHEMA_VERSION,
    assert_activation_export_payload_safe,
)

RECEIPT_TYPE = "onboarding_activation_fact"
EXPORT_ID_HEADER = "x-futureagi-activation-export-id"
IDEMPOTENCY_KEY_HEADER = "x-futureagi-activation-export-key"
SCHEMA_HEADER = "x-futureagi-activation-export-schema"
SIGNATURE_HEADER = "x-futureagi-activation-export-signature"


@dataclass(frozen=True)
class ActivationFactReceiptResult:
    receipt: OnboardingActivationFactReceipt
    created: bool


def _json_safe(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _json_bytes(payload) -> bytes:
    return json.dumps(
        payload,
        cls=DjangoJSONEncoder,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def activation_fact_body(payload) -> bytes:
    return _json_bytes(payload)


def _payload_hash(payload) -> str:
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def _body_hash(body) -> str:
    if not body:
        return ""
    return hashlib.sha256(bytes(body)).hexdigest()


def _header(headers, name):
    if not headers or not hasattr(headers, "get"):
        return ""
    return (
        headers.get(name)
        or headers.get(name.lower())
        or headers.get(name.upper())
        or ""
    )


def _signature(*, body: bytes, shared_secret: str) -> str:
    digest = hmac.new(
        str(shared_secret).encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def activation_fact_receiver_shared_secret(shared_secret=None):
    secret = shared_secret
    if secret is None:
        secret = getattr(
            settings,
            "ONBOARDING_ACTIVATION_FACT_RECEIVER_SHARED_SECRET",
            None,
        )
    if secret is None:
        secret = getattr(settings, "ONBOARDING_ACTIVATION_EXPORT_SHARED_SECRET", "")
    if not secret:
        raise ImproperlyConfigured("Activation fact receiver secret is not configured.")
    return str(secret)


def activation_fact_signature(*, body: bytes, shared_secret=None) -> str:
    return _signature(
        body=body,
        shared_secret=activation_fact_receiver_shared_secret(shared_secret),
    )


def _rejection_identifiers(payload, headers):
    payload = payload if isinstance(payload, dict) else {}
    headers = headers or {}
    export_log_id = payload.get("export_log_id") or _header(headers, EXPORT_ID_HEADER)
    idempotency_key = payload.get("idempotency_key") or _header(
        headers,
        IDEMPOTENCY_KEY_HEADER,
    )
    schema_version = payload.get("schema_version") or _header(headers, SCHEMA_HEADER)
    try:
        export_log_id = uuid.UUID(str(export_log_id)) if export_log_id else None
    except (TypeError, ValueError):
        export_log_id = None
    return {
        "export_log_id": export_log_id,
        "idempotency_key": str(idempotency_key or "")[:220],
        "schema_version": str(schema_version or "")[:96],
    }


def record_activation_fact_rejection(
    *,
    reason,
    message,
    payload=None,
    headers=None,
    body=None,
    now=None,
):
    identifiers = _rejection_identifiers(payload, headers)
    return OnboardingActivationFactReceiptRejection.no_workspace_objects.create(
        reason=str(reason)[:96],
        message=str(message)[:240],
        payload_hash=_body_hash(body) or (_payload_hash(payload) if payload else ""),
        received_at=now or timezone.now(),
        metadata={
            "has_signature": bool(_header(headers, SIGNATURE_HEADER)),
            "has_export_id_header": bool(_header(headers, EXPORT_ID_HEADER)),
            "has_idempotency_key_header": bool(
                _header(headers, IDEMPOTENCY_KEY_HEADER)
            ),
            "has_schema_header": bool(_header(headers, SCHEMA_HEADER)),
            "body_size": len(body or b""),
        },
        **identifiers,
    )


def verify_activation_fact_signature(
    *,
    body: bytes,
    headers,
    shared_secret=None,
    payload=None,
    now=None,
):
    submitted = _header(headers, SIGNATURE_HEADER)
    if not submitted or not str(submitted).startswith("sha256="):
        record_activation_fact_rejection(
            reason="invalid_signature",
            message="Missing activation fact signature.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise ValidationError({"signature": "Invalid activation fact signature."})

    expected = activation_fact_signature(body=body, shared_secret=shared_secret)
    if not hmac.compare_digest(str(submitted), expected):
        record_activation_fact_rejection(
            reason="invalid_signature",
            message="Activation fact signature did not match.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise ValidationError({"signature": "Invalid activation fact signature."})


def _nested(value, *keys):
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _uuid_field(value, field_name):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise ValidationError({field_name: "Must be a valid UUID."}) from None


def _string(value, default=""):
    if value is None:
        return default
    return str(value)


def _bool(value):
    return bool(value) if value is not None else False


def _cohort_keys(cohorts):
    keys = []
    for cohort in cohorts:
        if not isinstance(cohort, dict):
            continue
        cohort_key = cohort.get("cohort_key")
        if cohort_key:
            keys.append(str(cohort_key))
    return keys


def _validate_headers(payload, headers, body, now):
    required_headers = {
        EXPORT_ID_HEADER: payload.get("export_log_id"),
        IDEMPOTENCY_KEY_HEADER: payload.get("idempotency_key"),
        SCHEMA_HEADER: payload.get("schema_version"),
    }
    missing = [name for name in required_headers if not _header(headers, name)]
    if missing:
        record_activation_fact_rejection(
            reason="header_mismatch",
            message="Missing activation fact routing headers.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise ValidationError({"headers": "Activation fact routing headers required."})

    mismatched = [
        name
        for name, expected in required_headers.items()
        if _header(headers, name) != str(expected)
    ]
    if mismatched:
        record_activation_fact_rejection(
            reason="header_mismatch",
            message="Activation fact headers did not match the payload.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise ValidationError({"headers": "Activation fact headers do not match."})


def _validate_payload(payload, headers, body, now):
    if payload.get("type") != RECEIPT_TYPE:
        record_activation_fact_rejection(
            reason="malformed_payload",
            message="Unsupported activation fact type.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise ValidationError({"type": "Unsupported activation fact type."})

    schema_version = str(payload.get("schema_version") or "")
    if schema_version != ACTIVATION_EXPORT_SCHEMA_VERSION:
        record_activation_fact_rejection(
            reason="unknown_schema_version",
            message="Unsupported activation fact schema version.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise ValidationError({"schema_version": "Unsupported schema version."})

    fact = payload.get("fact")
    if not isinstance(fact, dict):
        record_activation_fact_rejection(
            reason="malformed_payload",
            message="Activation fact payload was not an object.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise ValidationError({"fact": "Activation fact payload is required."})

    if str(fact.get("schema_version") or "") != schema_version:
        record_activation_fact_rejection(
            reason="malformed_payload",
            message="Nested activation fact schema did not match the envelope.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise ValidationError({"fact.schema_version": "Schema version mismatch."})

    try:
        assert_activation_export_payload_safe(payload)
    except ValidationError as exc:
        record_activation_fact_rejection(
            reason="unsafe_payload",
            message="Activation fact payload failed the safety boundary.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise ValidationError(
            {"payload": "Activation fact payload is not safe."}
        ) from exc

    cohorts = _nested(fact, "journey", "cohorts")
    if cohorts is None:
        cohorts = []
    if not isinstance(cohorts, list):
        record_activation_fact_rejection(
            reason="malformed_payload",
            message="Activation fact cohorts were not a list.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise ValidationError({"fact.journey.cohorts": "Must be a list."})

    return fact


def _receipt_fields(payload, fact, payload_hash_value, now):
    cohorts = _nested(fact, "journey", "cohorts") or []
    cohort_keys = _cohort_keys(cohorts)
    lifecycle = fact.get("lifecycle") if isinstance(fact.get("lifecycle"), dict) else {}
    campaign = (
        lifecycle.get("campaign") if isinstance(lifecycle.get("campaign"), dict) else {}
    )
    email = (
        lifecycle.get("email_eligibility")
        if isinstance(lifecycle.get("email_eligibility"), dict)
        else {}
    )
    activation = (
        fact.get("activation") if isinstance(fact.get("activation"), dict) else {}
    )
    deployment = (
        fact.get("deployment") if isinstance(fact.get("deployment"), dict) else {}
    )
    subscription = (
        fact.get("subscription") if isinstance(fact.get("subscription"), dict) else {}
    )
    journey = fact.get("journey") if isinstance(fact.get("journey"), dict) else {}

    return {
        "export_log_id": _uuid_field(payload.get("export_log_id"), "export_log_id"),
        "idempotency_key": _string(payload.get("idempotency_key"))[:220],
        "schema_version": _string(payload.get("schema_version"))[:96],
        "event_cursor": _string(payload.get("event_cursor"))[:160],
        "organization_id_value": _uuid_field(
            _nested(fact, "organization", "id"),
            "fact.organization.id",
        ),
        "workspace_id_value": _uuid_field(
            _nested(fact, "workspace", "id"),
            "fact.workspace.id",
        ),
        "user_id_value": (
            _uuid_field(_nested(fact, "user", "id"), "fact.user.id")
            if _nested(fact, "user", "id")
            else None
        ),
        "deployment_mode": _string(deployment.get("mode"))[:16],
        "deployment_region": _string(deployment.get("region"))[:16],
        "plan_tier": _string(subscription.get("plan_tier"))[:64],
        "activation_stage": _string(activation.get("stage"))[:96],
        "primary_path": _string(activation.get("primary_path"))[:32],
        "is_activated": _bool(activation.get("is_activated")),
        "lifecycle_campaign_key": _string(campaign.get("next_campaign_key"))[:96],
        "lifecycle_template_key": _string(campaign.get("template_key"))[:96],
        "lifecycle_status": _string(campaign.get("status"))[:64],
        "email_next_key": _string(email.get("next_email_key"))[:96],
        "email_eligible": _bool(email.get("eligible")),
        "email_suppressed": _bool(email.get("suppressed")),
        "journey_config_schema_version": _string(journey.get("config_schema_version"))[
            :96
        ],
        "primary_cohort_key": (cohort_keys[0] if cohort_keys else "")[:96],
        "cohort_keys": cohort_keys,
        "journey_cohorts": _json_safe(cohorts),
        "payload_hash": payload_hash_value,
        "payload": _json_safe(payload),
        "evaluated_at": payload.get("evaluated_at"),
        "received_at": now,
        "metadata": {"source": "activation_fact_receiver"},
    }


def _idempotent_receipt(
    idempotency_key, payload_hash_value, payload, headers, body, now
):
    receipt = OnboardingActivationFactReceipt.no_workspace_objects.filter(
        idempotency_key=idempotency_key
    ).first()
    if receipt is None:
        return None
    if receipt.payload_hash != payload_hash_value:
        record_activation_fact_rejection(
            reason="idempotency_conflict",
            message="Activation fact idempotency key was reused with a new payload.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise ValidationError(
            {"idempotency_key": "Payload does not match the existing receipt."}
        )
    return receipt


def receive_activation_fact(
    *,
    payload,
    headers,
    body,
    shared_secret=None,
    now=None,
) -> ActivationFactReceiptResult:
    now = now or timezone.now()
    body = bytes(body or b"")

    verify_activation_fact_signature(
        body=body,
        headers=headers,
        shared_secret=shared_secret,
        payload=payload,
        now=now,
    )
    _validate_headers(payload, headers, body, now)
    fact = _validate_payload(payload, headers, body, now)

    payload_hash_value = _payload_hash(payload)
    try:
        fields = _receipt_fields(payload, fact, payload_hash_value, now)
    except ValidationError:
        record_activation_fact_rejection(
            reason="malformed_payload",
            message="Activation fact identifiers were malformed.",
            payload=payload,
            headers=headers,
            body=body,
            now=now,
        )
        raise
    receipt = _idempotent_receipt(
        fields["idempotency_key"],
        payload_hash_value,
        payload,
        headers,
        body,
        now,
    )
    if receipt is not None:
        return ActivationFactReceiptResult(receipt=receipt, created=False)

    try:
        receipt = OnboardingActivationFactReceipt.no_workspace_objects.create(**fields)
    except IntegrityError:
        receipt = _idempotent_receipt(
            fields["idempotency_key"],
            payload_hash_value,
            payload,
            headers,
            body,
            now,
        )
        if receipt is None:
            raise
        return ActivationFactReceiptResult(receipt=receipt, created=False)

    return ActivationFactReceiptResult(receipt=receipt, created=True)
