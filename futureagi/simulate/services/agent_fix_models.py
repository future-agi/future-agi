"""The improvement engine's model calls, through the platform.

The engine in its sandbox calls an OpenAI-compatible endpoint on its fix's own API with its
fix token. The platform forwards the call to the model gateway with its own key, which never
enters the sandbox, and adds what the gateway says the call cost to the fix's spend. Future
AGI pays for these calls, and the fix's budget stops them.
"""

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx
import structlog
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from agentcc.services.gateway_client import resolve_gateway_internal_url
from simulate.models import AgentFixRun
from simulate.services.agent_fix_engine import committed_usd
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import platform_model_gateway

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
    fresh = AgentFixRun.no_workspace_objects.get(id=fix_run.id)
    if fresh.state != AgentFixRun.State.RUNNING:
        raise HostedHarnessError(
            "fix_run_not_running", f"the fix is {fresh.state}", status_code=409
        )
    budget = Decimal(str(fresh.request["budget_usd"]))
    if committed_usd(fresh) >= budget:
        raise HostedHarnessError(
            "fix_budget_exhausted",
            f"the fix has used its ${budget:.2f} budget",
            status_code=402,
        )
    _, key = platform_model_gateway()
    try:
        response = httpx.post(
            f"{resolve_gateway_internal_url().rstrip('/')}/v1/chat/completions",
            content=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=settings.AGENT_FIX_MODEL_TIMEOUT_SECONDS,
        )
    except httpx.TransportError as exc:
        logger.warning("agent_fix_model_gateway_unreachable", fix_run=str(fix_run.id))
        raise HostedHarnessError(
            "fix_model_gateway_unreachable",
            "the model gateway could not be reached",
            status_code=502,
            retryable=True,
        ) from exc
    _meter(fix_run, response.headers.get("x-agentcc-cost"))
    return ModelReply(
        status_code=response.status_code,
        body=response.content,
        content_type=response.headers.get("content-type", "application/json"),
    )


def _meter(fix_run: AgentFixRun, cost: str | None) -> None:
    try:
        usd = Decimal(cost or "0")
    except InvalidOperation:
        logger.warning(
            "agent_fix_model_cost_unreadable", fix_run=str(fix_run.id), cost=cost
        )
        return
    if usd > 0:
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
            spent_usd=F("spent_usd") + usd, updated_at=timezone.now()
        )
