import json

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounts.models import OnboardingActivationEvent
from accounts.services.onboarding.constants import (
    ACTIVATION_STAGES,
    ONBOARDING_ACTIVATION_EVENTS,
    PRODUCT_PATHS,
    canonical_activation_event,
    canonical_path,
)

MAX_METADATA_BYTES = 8192
SENSITIVE_METADATA_KEYS = {
    "api_key",
    "authorization",
    "input",
    "messages",
    "output",
    "password",
    "prompt",
    "provider_credentials",
    "secret",
    "token",
    "trace_payload",
}


def validate_event_name(event_name):
    canonical = canonical_activation_event(event_name)
    if canonical not in ONBOARDING_ACTIVATION_EVENTS:
        raise ValidationError("Unsupported onboarding activation event.")
    return canonical


def validate_product_path(product_path):
    if not product_path:
        return ""
    canonical = canonical_path(product_path)
    if canonical not in PRODUCT_PATHS:
        raise ValidationError("Unsupported onboarding product path.")
    return canonical


def validate_activation_stage(activation_stage):
    if not activation_stage:
        return ""
    if activation_stage not in ACTIVATION_STAGES:
        raise ValidationError("Unsupported onboarding activation stage.")
    return activation_stage


def validate_workspace_scope(*, organization, workspace):
    if organization is None:
        raise ValidationError("Organization is required.")
    if workspace is None:
        raise ValidationError("Workspace is required.")
    if workspace.organization_id != organization.id:
        raise ValidationError("Workspace does not belong to organization.")


def _walk_metadata_keys(value):
    if isinstance(value, dict):
        for key, nested_value in value.items():
            yield str(key).lower()
            yield from _walk_metadata_keys(nested_value)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_metadata_keys(item)


def sanitize_activation_metadata(metadata):
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise ValidationError("Activation event metadata must be a dictionary.")

    unsafe_keys = SENSITIVE_METADATA_KEYS.intersection(_walk_metadata_keys(metadata))
    if unsafe_keys:
        raise ValidationError("Activation event metadata contains sensitive keys.")

    try:
        encoded = json.dumps(metadata, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError("Activation event metadata must be JSON-safe.") from exc

    if len(encoded) > MAX_METADATA_BYTES:
        raise ValidationError("Activation event metadata is too large.")

    return metadata


def build_idempotency_key(parts):
    key = ":".join(str(part) for part in parts if part is not None and part != "")
    if not key:
        return None
    if len(key) > 160:
        raise ValidationError("Activation event idempotency key is too long.")
    return key


def _normalize_idempotency_key(idempotency_key):
    if not idempotency_key:
        return None
    if len(idempotency_key) > 160:
        raise ValidationError("Activation event idempotency key is too long.")
    return idempotency_key


def record_event(
    *,
    user,
    organization,
    workspace,
    event_name,
    source,
    product_path=None,
    activation_stage=None,
    metadata=None,
    is_sample=False,
    idempotency_key=None,
    occurred_at=None,
):
    validate_workspace_scope(organization=organization, workspace=workspace)
    canonical_event_name = validate_event_name(event_name)
    canonical_product_path = validate_product_path(product_path)
    canonical_activation_stage = validate_activation_stage(activation_stage)
    safe_metadata = sanitize_activation_metadata(metadata)
    normalized_idempotency_key = _normalize_idempotency_key(idempotency_key)

    defaults = {
        "user": user,
        "event_name": canonical_event_name,
        "product_path": canonical_product_path,
        "activation_stage": canonical_activation_stage,
        "source": source or "",
        "metadata": safe_metadata,
        "is_sample": is_sample,
    }
    if occurred_at is not None:
        defaults["occurred_at"] = occurred_at

    if not normalized_idempotency_key:
        return OnboardingActivationEvent.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            idempotency_key=None,
            **defaults,
        )

    try:
        with transaction.atomic():
            event, _created = (
                OnboardingActivationEvent.no_workspace_objects.get_or_create(
                    organization=organization,
                    workspace=workspace,
                    idempotency_key=normalized_idempotency_key,
                    defaults=defaults,
                )
            )
            return event
    except IntegrityError:
        return OnboardingActivationEvent.no_workspace_objects.get(
            organization=organization,
            workspace=workspace,
            idempotency_key=normalized_idempotency_key,
        )


def _events_queryset(
    *,
    organization,
    workspace,
    event_names=None,
    product_path=None,
    is_sample=None,
):
    validate_workspace_scope(organization=organization, workspace=workspace)
    queryset = OnboardingActivationEvent.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
    )

    if event_names is not None:
        queryset = queryset.filter(
            event_name__in=[validate_event_name(name) for name in event_names]
        )
    if product_path is not None:
        queryset = queryset.filter(product_path=validate_product_path(product_path))
    if is_sample is not None:
        queryset = queryset.filter(is_sample=is_sample)

    return queryset


def has_event(*, organization, workspace, event_name, is_sample=False):
    return _events_queryset(
        organization=organization,
        workspace=workspace,
        event_names=[event_name],
        is_sample=is_sample,
    ).exists()


def latest_event(
    *,
    organization,
    workspace,
    event_names=None,
    product_path=None,
    is_sample=None,
):
    return (
        _events_queryset(
            organization=organization,
            workspace=workspace,
            event_names=event_names,
            product_path=product_path,
            is_sample=is_sample,
        )
        .order_by("-occurred_at", "-created_at")
        .first()
    )


def events_for_workspace(
    *,
    organization,
    workspace,
    event_names=None,
    product_path=None,
    is_sample=None,
    limit=100,
):
    limit = max(0, min(int(limit), 500))
    return list(
        _events_queryset(
            organization=organization,
            workspace=workspace,
            event_names=event_names,
            product_path=product_path,
            is_sample=is_sample,
        ).order_by("-occurred_at", "-created_at")[:limit]
    )


def first_quality_loop_completed(*, organization, workspace, product_path=None):
    return _events_queryset(
        organization=organization,
        workspace=workspace,
        event_names=["first_quality_loop_completed"],
        product_path=product_path,
        is_sample=False,
    ).exists()
