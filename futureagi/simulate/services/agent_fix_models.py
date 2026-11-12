"""The improvement engine's model calls, through the platform.

The engine in its sandbox calls an OpenAI-compatible endpoint on its fix's own API with its
fix token. The platform forwards the call to the model gateway with its own key, which never
enters the sandbox, and adds what the call bills the organization (the gateway's cost at the
AI markup) to the fix's spend. Future AGI pays for these calls and bills them as AI credits;
the fix's limit stops them.
"""

import json
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from uuid import uuid5

import httpx
import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from agentcc.services.gateway_client import resolve_gateway_internal_url
from simulate.models import AgentFixRun
from simulate.selectors.agent_fix import committed_usd
from simulate.services.harness_usage import billed_ai_usd, require_agent_fix_usage
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import platform_model_gateway
from tfc.ee_gating import is_oss

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ModelReply:
    status_code: int
    body: bytes
    content_type: str


def complete(fix_run: AgentFixRun, body: bytes) -> ModelReply:
    """Forward one chat completion for the fix's engine and meter what it cost."""
    try:
        request = json.loads(body)
    except ValueError as exc:
        raise HostedHarnessError(
            "fix_model_request_invalid", "the request is not JSON", status_code=400
        ) from exc
    model = request.get("model") if isinstance(request, dict) else None
    if model not in settings.AGENT_FIX_ENGINE_MODELS:
        raise HostedHarnessError(
            "fix_model_not_allowed",
            f"the improvement engine may not call {model!r}",
            status_code=403,
        )
    if request.get("stream"):
        raise HostedHarnessError(
            "fix_model_stream_unsupported",
            "the engine's model calls are not streamed",
            status_code=400,
        )
    # Resolve local configuration before taking a hold: these failures cannot
    # have dispatched a paid request.
    _, key = platform_model_gateway()
    url = f"{resolve_gateway_internal_url().rstrip('/')}/v1/chat/completions"
    reservation = _reserve(fix_run)
    try:
        response = httpx.post(
            url,
            content=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                # The gateway fits/refuses the priced, bounded request before
                # dispatch, including retries/fallbacks. Its cap is before our
                # AI markup, unlike the fix's billed-dollar reservation.
                "x-agentcc-max-cost-usd": str(reservation.gateway_usd),
            },
            timeout=settings.AGENT_FIX_MODEL_TIMEOUT_SECONDS,
        )
    except httpx.TransportError as exc:
        not_sent = isinstance(
            exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
        )
        if not_sent:
            _release(fix_run, reservation)
        # A lost response is not proof of a free call. Keep its liability held,
        # also across workers/restarts, rather than granting a fresh budget.
        logger.warning("agent_fix_model_gateway_unreachable", fix_run=str(fix_run.id))
        raise HostedHarnessError(
            "fix_model_gateway_unreachable",
            "the model gateway could not be reached",
            status_code=502,
            retryable=not_sent,
        ) from exc
    _meter(fix_run, model, response, reservation)
    return ModelReply(
        status_code=response.status_code,
        body=response.content,
        content_type=response.headers.get("content-type", "application/json"),
    )


@dataclass(frozen=True)
class _Reservation:
    billed_usd: Decimal
    gateway_usd: Decimal
    markup: Decimal


def _reserve(fix_run: AgentFixRun) -> _Reservation:
    """Exclusively hold the remaining billed budget; unlock before the model request.

    The gateway owns model prices and output fitting. Give one call all remaining
    room, rather than inventing a second estimator here. Other engine calls retry
    after settlement; check admission takes the same row lock and sees this hold.
    """
    markup = billed_ai_usd(Decimal(1))
    if not markup.is_finite() or markup <= 0:
        raise HostedHarnessError(
            "fix_model_price_unavailable",
            "the AI markup is unavailable",
            status_code=503,
        )
    with transaction.atomic():
        fresh = AgentFixRun.no_workspace_objects.select_for_update().get(id=fix_run.id)
        if fresh.state != AgentFixRun.State.RUNNING:
            raise HostedHarnessError(
                "fix_run_not_running", f"the fix is {fresh.state}", status_code=409
            )
        if fresh.model_reserved_usd > 0:
            raise HostedHarnessError(
                "fix_model_budget_reserved",
                "a model call still holds the fix's remaining budget",
                status_code=429,
                retryable=True,
            )
        budget = Decimal(str(fresh.request["budget_usd"]))
        remaining = (budget - committed_usd(fresh)).quantize(
            Decimal("0.0001"), rounding=ROUND_FLOOR
        )
        # The gateway's monetary unit is a micro-dollar. Never round its cap up
        # past the billed budget when converting out of the snapshotted markup.
        cap = (remaining / markup).quantize(Decimal("0.000001"), rounding=ROUND_FLOOR)
        if remaining <= 0 or cap <= 0:
            raise HostedHarnessError(
                "fix_budget_exhausted",
                f"the fix has used its ${budget:.2f} budget",
                status_code=402,
            )
        # Organization admission can change while the fix still has budget left.
        # Refusals or check failures must not persist a new budget hold.
        require_agent_fix_usage(str(fresh.organization_id))
        fresh.model_reserved_usd = remaining
        fresh.save(update_fields=["model_reserved_usd", "updated_at"])
        return _Reservation(remaining, cap, markup)


def _release(fix_run: AgentFixRun, reservation: _Reservation) -> None:
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        model_reserved_usd=F("model_reserved_usd") - reservation.billed_usd,
        updated_at=timezone.now(),
    )


def _meter(
    fix_run: AgentFixRun,
    model: str,
    response: httpx.Response,
    reservation: _Reservation,
) -> None:
    cost = response.headers.get("x-agentcc-cost")
    # Admission/rate-limit refusals have no paid completion. A successful or
    # ambiguous server response without usage is NOT permission to free a hold.
    if cost is None and response.status_code in (400, 401, 402, 403, 404, 429):
        _release(fix_run, reservation)
        return
    try:
        usd = Decimal(cost) if cost is not None else Decimal("NaN")
    except InvalidOperation:
        usd = Decimal("NaN")
    if not usd.is_finite() or usd < 0:
        logger.warning("agent_fix_model_cost_unreadable", fix_run=str(fix_run.id))
        return
    billed = usd * reservation.markup
    if billed > reservation.billed_usd:
        # The gateway must enforce the sent cap; never pass a violation of that
        # contract on to the customer. Preserve actual cost in the billing event.
        logger.error(
            "agent_fix_model_cap_exceeded",
            fix_run=str(fix_run.id),
            cost_usd=str(usd),
            reserved_usd=str(reservation.billed_usd),
        )
        billed = reservation.billed_usd
    # Spend is stored to four places. Rounding down every tiny call would let
    # real billed credits escape subsequent admission. This rounds accounting,
    # not the customer's bill, up by less than a hundredth of a credit per call.
    accounted = billed.quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        spent_usd=F("spent_usd") + accounted,
        model_reserved_usd=F("model_reserved_usd") - reservation.billed_usd,
        updated_at=timezone.now(),
    )
    if billed > 0:
        _bill(
            fix_run,
            model,
            usd,
            response.headers.get("x-agentcc-request-id"),
            billed_usd=billed,
        )


def _bill(
    fix_run: AgentFixRun,
    model: str,
    usd: Decimal,
    request_id: str | None,
    *,
    billed_usd: Decimal,
) -> None:
    """Bill one engine call to the organization as AI credits. The call has already been
    made and paid for, so a billing failure is logged and never fails it: an error here
    would make the engine retry the call and pay for it twice."""
    if is_oss():
        return
    try:
        from ee.usage.schemas.event_types import BillingEventType
        from ee.usage.schemas.events import UsageEvent
        from ee.usage.services.emitter import emit

        # One event per gateway call, however often it is emitted.
        identity = (
            {"event_id": str(uuid5(fix_run.id, request_id))} if request_id else {}
        )
        emit(
            UsageEvent(
                **identity,
                org_id=str(fix_run.organization_id),
                event_type=BillingEventType.AGENT_FIX,
                # The admission-time markup is already included. Do not reload
                # a possibly changed markup after the call used its reservation.
                amount=float(billed_usd / Decimal("0.01")),
                properties={
                    "source": "agent_fix",
                    "source_id": str(fix_run.id),
                    "fix_run_id": str(fix_run.id),
                    "environment_id": str(fix_run.environment_id),
                    "workspace_id": str(fix_run.workspace_id or ""),
                    "model": model,
                    "raw_cost_usd": str(usd),
                    "gateway_request_id": request_id,
                },
            )
        )
    except Exception:
        logger.exception("agent_fix_billing_emit_failed", fix_run=str(fix_run.id))
