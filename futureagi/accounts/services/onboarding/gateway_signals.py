from __future__ import annotations

import os
from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone

from accounts.models import OnboardingActivationEvent
from accounts.services.onboarding.activation_events import has_event, latest_event
from agentcc.models import AgentccAPIKey, AgentccOrgConfig, AgentccRequestLog
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.models.routing_policy import AgentccRoutingPolicy
from agentcc.services.gateway_client import AGENTCC_GATEWAY_URL

GATEWAY_ID = "default"
GATEWAY_PUBLIC_URL = (
    os.environ.get("AGENTCC_GATEWAY_PUBLIC_URL", "") or AGENTCC_GATEWAY_URL
).rstrip("/")


@dataclass(frozen=True)
class GatewayOnboardingSignals:
    gateway_available: bool = True
    gateway_id: str | None = GATEWAY_ID
    gateway_status: str | None = "available"
    gateway_public_url: str | None = GATEWAY_PUBLIC_URL
    provider_count: int = 0
    provider_credential_id: str | None = None
    provider_name: str | None = None
    provider_health_status: str | None = None
    provider_model_count: int = 0
    key_count: int = 0
    gateway_key_id: str | None = None
    key_prefix: str | None = None
    key_status: str | None = None
    request_count: int = 0
    sample_request_count: int = 0
    request_log_id: str | None = None
    request_id: str | None = None
    request_status_code: int | None = None
    request_is_error: bool = False
    request_error_message: str | None = None
    request_provider: str | None = None
    request_model: str | None = None
    request_resolved_model: str | None = None
    request_latency_ms: int | None = None
    request_cost: str | None = None
    request_cache_hit: bool = False
    request_fallback_used: bool = False
    request_guardrail_triggered: bool = False
    request_started_at: object | None = None
    has_review: bool = False
    reviewed_at: object | None = None
    has_failure_repair: bool = False
    policy_count: int = 0
    policy_type: str | None = None
    policy_id: str | None = None
    policy_route: str | None = None
    policy_synced: bool = False
    is_sample_only: bool = False
    permission_limited: bool = False
    guard_blocked: bool = False
    diagnostics: tuple[str, ...] = ()

    @property
    def has_provider(self):
        return self.provider_count > 0

    @property
    def has_key(self):
        return self.key_count > 0

    @property
    def has_request(self):
        return self.request_count > 0 and not self.is_sample_only

    @property
    def request_failed(self):
        return self.request_is_error or bool(
            self.request_status_code and self.request_status_code >= 400
        )

    @property
    def has_policy(self):
        return self.policy_count > 0

    @property
    def first_loop_completed(self):
        return (
            self.has_key
            and self.has_request
            and self.has_review
            and self.has_policy
            and not self.is_sample_only
        )

    def to_activation_gateway_state(self, stage):
        return {
            "gateway_available": self.gateway_available,
            "gateway_id": self.gateway_id,
            "gateway_status": self.gateway_status,
            "gateway_public_url": self.gateway_public_url,
            "provider_count": self.provider_count,
            "provider_credential_id": self.provider_credential_id,
            "provider_name": self.provider_name,
            "provider_health_status": self.provider_health_status,
            "provider_model_count": self.provider_model_count,
            "has_provider": self.has_provider,
            "has_key": self.has_key,
            "gateway_key_id": self.gateway_key_id,
            "key_prefix": self.key_prefix,
            "key_status": self.key_status,
            "has_request": self.has_request,
            "request_log_id": self.request_log_id,
            "request_id": self.request_id,
            "request_status_code": self.request_status_code,
            "request_is_error": self.request_is_error,
            "request_error_message": self.request_error_message,
            "request_provider": self.request_provider,
            "request_model": self.request_model,
            "request_resolved_model": self.request_resolved_model,
            "request_latency_ms": self.request_latency_ms,
            "request_cost": self.request_cost,
            "request_cache_hit": self.request_cache_hit,
            "request_fallback_used": self.request_fallback_used,
            "request_guardrail_triggered": self.request_guardrail_triggered,
            "has_review": self.has_review,
            "reviewed_at": self.reviewed_at,
            "has_failure_repair": self.has_failure_repair,
            "has_policy": self.has_policy,
            "policy_type": self.policy_type,
            "policy_id": self.policy_id,
            "policy_route": self.policy_route,
            "policy_synced": self.policy_synced,
            "is_sample": self.is_sample_only,
            "sample_request_count": self.sample_request_count,
            "permission_limited": self.permission_limited,
            "guard_blocked": self.guard_blocked,
            "diagnostics": list(self.diagnostics),
            "stage": stage,
        }


def _real_request_filter():
    return (Q(metadata__is_sample__isnull=True) | Q(metadata__is_sample=False)) & (
        Q(metadata__sample__isnull=True) | Q(metadata__sample=False)
    )


def _sample_request_filter():
    return Q(metadata__is_sample=True) | Q(metadata__sample=True)


def _latest_gateway_event(
    *,
    organization,
    workspace,
    event_names,
    is_sample=False,
    request_log_id=None,
    request_id=None,
):
    queryset = OnboardingActivationEvent.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        event_name__in=event_names,
        product_path="gateway",
        is_sample=is_sample,
    )
    matchers = Q()
    if request_log_id:
        matchers |= Q(metadata__request_log_id=str(request_log_id))
    if request_id:
        matchers |= Q(metadata__request_id=str(request_id))
        matchers |= Q(metadata__gateway_request_id=str(request_id))
    if matchers:
        event = (
            queryset.filter(matchers).order_by("-occurred_at", "-created_at").first()
        )
        if event:
            return event
    return queryset.order_by("-occurred_at", "-created_at").first()


def _active_key_queryset(organization):
    now = timezone.now()
    return (
        AgentccAPIKey.no_workspace_objects.filter(
            organization=organization,
            status=AgentccAPIKey.ACTIVE,
            deleted=False,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .order_by("-last_used_at", "-updated_at", "-created_at")
    )


def _real_request_queryset(organization):
    return (
        AgentccRequestLog.no_workspace_objects.filter(
            organization=organization,
            deleted=False,
        )
        .filter(_real_request_filter())
        .exclude(request_id__icontains="sample")
        .order_by("-started_at", "-created_at")
    )


def _has_meaningful_config(value):
    if value in (None, "", [], {}):
        return False
    if isinstance(value, dict):
        return any(_has_meaningful_config(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_meaningful_config(item) for item in value)
    return True


def _policy_from_event(organization, workspace):
    event = latest_event(
        organization=organization,
        workspace=workspace,
        event_names=["gateway_policy_created"],
        product_path="gateway",
        is_sample=False,
    )
    if not event:
        return None
    metadata = event.metadata or {}
    policy_type = metadata.get("policy_type")
    return {
        "policy_type": policy_type,
        "policy_id": metadata.get("policy_id"),
        "policy_synced": bool(metadata.get("gateway_synced", True)),
        "created_at": event.occurred_at,
    }


def _policy_from_models(organization):
    guardrail = (
        AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization,
            deleted=False,
            is_active=True,
        )
        .order_by("-updated_at", "-created_at")
        .first()
    )
    if guardrail:
        return {
            "policy_type": "guardrail",
            "policy_id": str(guardrail.id),
            "policy_synced": True,
            "created_at": guardrail.updated_at or guardrail.created_at,
        }

    routing = (
        AgentccRoutingPolicy.no_workspace_objects.filter(
            organization=organization,
            deleted=False,
            is_active=True,
        )
        .order_by("-updated_at", "-created_at")
        .first()
    )
    if routing:
        return {
            "policy_type": "fallback",
            "policy_id": str(routing.id),
            "policy_synced": True,
            "created_at": routing.updated_at or routing.created_at,
        }

    config = (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization,
            deleted=False,
            is_active=True,
        )
        .order_by("-version", "-updated_at", "-created_at")
        .first()
    )
    if not config:
        return None

    config_fields = [
        ("guardrail", "guardrails"),
        ("fallback", "routing"),
        ("budget", "budgets"),
        ("budget", "rate_limiting"),
        ("alert", "alerting"),
    ]
    for policy_type, field_name in config_fields:
        if _has_meaningful_config(getattr(config, field_name, None)):
            return {
                "policy_type": policy_type,
                "policy_id": str(config.id),
                "policy_synced": True,
                "created_at": config.updated_at or config.created_at,
            }
    return None


def _policy_route(policy_type, request):
    if policy_type == "guardrail":
        return "guardrail"
    if policy_type == "budget":
        return "budget"
    if policy_type in {"fallback", "routing"}:
        return "fallback"
    if request and getattr(request, "guardrail_triggered", False):
        return "guardrail"
    if request and getattr(request, "fallback_used", False):
        return "fallback"
    if request and getattr(request, "is_error", False):
        return "fallback"
    return "budget"


def _failure_repaired(organization, workspace, request, policy):
    status_code = getattr(request, "status_code", None)
    if not request or not (request.is_error or (status_code and status_code >= 400)):
        return False
    request_time = request.started_at or request.created_at
    repair_event = _latest_gateway_event(
        organization=organization,
        workspace=workspace,
        event_names=["gateway_failure_resolved"],
        is_sample=False,
        request_log_id=request.id,
        request_id=request.request_id,
    )
    if repair_event and repair_event.occurred_at >= request_time:
        return True
    later_success = (
        _real_request_queryset(organization)
        .filter(
            started_at__gt=request_time,
            is_error=False,
            status_code__lt=400,
        )
        .filter(
            Q(api_key_id=request.api_key_id)
            | Q(provider=request.provider)
            | Q(model=request.model)
        )
        .exists()
    )
    if later_success:
        return True
    policy_created_at = (policy or {}).get("created_at")
    return bool(policy_created_at and policy_created_at >= request_time)


def collect_gateway_onboarding_signals(*, user, organization, workspace):
    if not organization or not workspace:
        return GatewayOnboardingSignals(
            gateway_available=False,
            gateway_id=None,
            gateway_status="unavailable",
            gateway_public_url=None,
            guard_blocked=True,
            diagnostics=("workspace_missing",),
        )

    providers = AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization,
        deleted=False,
        is_active=True,
    ).order_by("-updated_at", "-created_at")
    provider = providers.first()
    provider_count = providers.count()

    requests = _real_request_queryset(organization)
    latest_request = requests.first()
    request_count = requests.count()
    request_event = _latest_gateway_event(
        organization=organization,
        workspace=workspace,
        event_names=["gateway_request_seen"],
        is_sample=False,
    )
    if request_event and not request_count:
        request_count = 1

    sample_request_count = (
        AgentccRequestLog.no_workspace_objects.filter(
            organization=organization,
            deleted=False,
        )
        .filter(_sample_request_filter())
        .count()
    )
    if has_event(
        organization=organization,
        workspace=workspace,
        event_name="gateway_request_seen",
        is_sample=True,
    ):
        sample_request_count += 1

    keys = _active_key_queryset(organization)
    if latest_request and latest_request.api_key_id:
        matched_key = keys.filter(gateway_key_id=latest_request.api_key_id).first()
        key = matched_key or keys.first()
    else:
        key = keys.first()
    key_count = keys.count()

    request_metadata = (request_event.metadata or {}) if request_event else {}
    request_log_id = (
        str(latest_request.id)
        if latest_request
        else request_metadata.get("request_log_id")
    )
    request_id = (
        latest_request.request_id
        if latest_request
        else (
            request_metadata.get("request_id")
            or request_metadata.get("gateway_request_id")
        )
    )
    request_status_code = (
        latest_request.status_code
        if latest_request
        else (
            request_metadata.get("status_code")
            or request_metadata.get("request_status_code")
        )
    )
    if request_status_code not in {None, ""}:
        request_status_code = int(request_status_code)
    else:
        request_status_code = None
    request_is_error = (
        bool(latest_request.is_error)
        if latest_request
        else bool(
            request_metadata.get("is_error")
            or request_metadata.get("request_is_error")
            or (request_status_code and request_status_code >= 400)
        )
    )
    request_started_at = (
        (latest_request.started_at or latest_request.created_at)
        if latest_request
        else (request_event.occurred_at if request_event else None)
    )

    review_event = _latest_gateway_event(
        organization=organization,
        workspace=workspace,
        event_names=["gateway_log_opened"],
        is_sample=False,
        request_log_id=request_log_id,
        request_id=request_id,
    )

    policy = _policy_from_models(organization) or _policy_from_event(
        organization,
        workspace,
    )
    policy_route = _policy_route(
        policy.get("policy_type") if policy else None, latest_request
    )
    has_failure_repair = _failure_repaired(
        organization,
        workspace,
        latest_request,
        policy,
    )

    diagnostics = []
    if sample_request_count and not request_count:
        diagnostics.append("sample_gateway_request_ignored_for_real_activation")
    if request_is_error and not has_failure_repair:
        diagnostics.append("gateway_first_request_needs_repair")

    return GatewayOnboardingSignals(
        gateway_available=True,
        gateway_id=GATEWAY_ID,
        gateway_status="available",
        gateway_public_url=f"{GATEWAY_PUBLIC_URL}/v1" if GATEWAY_PUBLIC_URL else None,
        provider_count=provider_count,
        provider_credential_id=str(provider.id) if provider else None,
        provider_name=provider.provider_name if provider else None,
        provider_health_status="configured" if provider else None,
        provider_model_count=len(provider.models_list or []) if provider else 0,
        key_count=key_count,
        gateway_key_id=key.gateway_key_id if key else None,
        key_prefix=key.key_prefix if key else None,
        key_status=key.status if key else None,
        request_count=request_count,
        sample_request_count=sample_request_count,
        request_log_id=request_log_id,
        request_id=request_id,
        request_status_code=request_status_code,
        request_is_error=request_is_error,
        request_error_message=(
            (latest_request.error_message or "")[:240]
            if latest_request
            else (request_metadata.get("error_message") or "")[:240] or None
        ),
        request_provider=(
            latest_request.provider
            if latest_request
            else request_metadata.get("provider")
        ),
        request_model=latest_request.model
        if latest_request
        else request_metadata.get("model"),
        request_resolved_model=(
            latest_request.resolved_model
            if latest_request
            else request_metadata.get("resolved_model")
        ),
        request_latency_ms=(
            latest_request.latency_ms
            if latest_request
            else request_metadata.get("latency_ms")
        ),
        request_cost=(
            str(latest_request.cost)
            if latest_request
            else str(request_metadata.get("cost") or "")
        ),
        request_cache_hit=(
            bool(latest_request.cache_hit)
            if latest_request
            else bool(request_metadata.get("cache_hit"))
        ),
        request_fallback_used=(
            bool(latest_request.fallback_used)
            if latest_request
            else bool(request_metadata.get("fallback_used"))
        ),
        request_guardrail_triggered=(
            bool(latest_request.guardrail_triggered)
            if latest_request
            else bool(request_metadata.get("guardrail_triggered"))
        ),
        request_started_at=request_started_at,
        has_review=bool(review_event),
        reviewed_at=review_event.occurred_at if review_event else None,
        has_failure_repair=has_failure_repair,
        policy_count=1 if policy else 0,
        policy_type=policy.get("policy_type") if policy else None,
        policy_id=policy.get("policy_id") if policy else None,
        policy_route=policy_route,
        policy_synced=bool(policy.get("policy_synced")) if policy else False,
        is_sample_only=bool(sample_request_count and not request_count),
        diagnostics=tuple(diagnostics),
    )
