"""Lossless BYOK adapter. Explicit scope, no ambient/global/custom/managed fallback."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.inventory import canonical_json, fingerprint

if TYPE_CHECKING:
    from model_hub.services.skills_hub.v11.types import CompletionResult, SkillScope

ADAPTER_VERSION = "skills-litellm-v1.1"
logger = logging.getLogger(__name__)
# Add model IDs only after approved real cloud protocol/credential/deadline probes.
# This list is intentionally empty by default; offline fixtures do not certify a provider.
STANDARD_PROVIDER_BASES = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
}


@dataclass(frozen=True)
class ResolvedConnection:
    scope: SkillScope
    model_id: str
    provider: str
    connection_id: str
    revision: str
    context_limit: int
    input_price: Decimal
    output_price: Decimal
    pricing_source: str
    api_key: str = field(repr=False, compare=False)

    def identity(self) -> dict:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "connection_id": self.connection_id,
            "revision": self.revision,
            "context_limit": self.context_limit,
            "input_price": str(self.input_price),
            "output_price": str(self.output_price),
            "pricing_source": self.pricing_source,
        }


def _model_spec(model_id: str) -> dict:
    import litellm

    from agentic_eval.core_evals.run_prompt.available_models import OSS_AVAILABLE_MODELS

    if model_id not in getattr(settings, "SKILLS_VERIFIED_MODELS", ()):
        raise SkillsHubError(
            "model_tools_unsupported",
            "Model has not been verified for Skills structured tools.",
            status=422,
        )
    catalog = next(
        (
            entry
            for entry in OSS_AVAILABLE_MODELS
            if isinstance(entry, dict)
            and entry["model_name"] == model_id
            and entry.get("mode") == "chat"
        ),
        None,
    )
    if not catalog or catalog["providers"] not in STANDARD_PROVIDER_BASES:
        raise SkillsHubError(
            "model_tools_unsupported",
            "Only verified standard key transports are supported.",
            status=422,
        )
    info = litellm.model_cost.get(model_id)
    if not info or not info.get("supports_function_calling"):
        raise SkillsHubError(
            "model_tools_unsupported",
            "Structured tools are required for this model.",
            status=422,
        )
    prices = [info.get("input_cost_per_token"), info.get("output_cost_per_token")]
    if any(
        value is None or Decimal(str(value)) <= 0 for value in prices
    ) or not info.get("max_input_tokens"):
        raise SkillsHubError(
            "model_pricing_unavailable",
            "Verified context and token pricing are required.",
            status=422,
        )
    return {
        "provider": catalog["providers"],
        "context_limit": int(info["max_input_tokens"]),
        "input_price": Decimal(str(prices[0])),
        "output_price": Decimal(str(prices[1])),
        "pricing_source": "litellm:"
        + fingerprint({"model": model_id, "prices": prices}),
    }


def resolve_test_connection(*, scope: SkillScope, model_id: str) -> ResolvedConnection:
    from accounts.models.workspace import Workspace
    from model_hub.models.api_key import ApiKey

    if not Workspace.no_workspace_objects.filter(
        id=scope.workspace_id, organization_id=scope.organization_id, is_active=True
    ).exists():
        raise SkillsHubError(
            "workspace_required",
            "An active organization workspace is required.",
            status=403,
        )
    spec = _model_spec(model_id)
    key = (
        ApiKey.no_workspace_objects.filter(
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            deleted=False,
            provider=spec["provider"],
        )
        .order_by("-updated_at", "id")
        .first()
    )
    if key is None or not key.actual_key:
        raise SkillsHubError(
            "model_not_configured",
            "Connect this provider in workspace settings; organization-global keys are not used.",
            status=422,
        )
    # Config JSON can redirect standard transports; this launch supports only explicit keys.
    if key.actual_json:
        raise SkillsHubError(
            "model_tools_unsupported",
            "Configured endpoint overrides are not supported for Skills.",
            status=422,
        )
    return ResolvedConnection(
        scope=scope,
        model_id=model_id,
        connection_id=str(key.id),
        revision=key.updated_at.isoformat(),
        api_key=key.actual_key,
        **spec,
    )


def list_test_models(*, scope: SkillScope) -> list[dict]:
    rows = []
    for model_id in sorted(set(getattr(settings, "SKILLS_VERIFIED_MODELS", ()))):
        try:
            spec = _model_spec(model_id)
        except SkillsHubError:
            continue
        row = {
            "model_id": model_id,
            "provider": spec["provider"],
            "configured": False,
            "tool_support": "supported",
            "context_limit": spec["context_limit"],
            "pricing_status": "known",
            "can_run": False,
            "disabled_reason": None,
        }
        try:
            resolve_test_connection(scope=scope, model_id=model_id)
            row.update(configured=True, can_run=True)
        except SkillsHubError as error:
            row["disabled_reason"] = error.code
        rows.append(row)
    return rows


def call_allowance(
    *,
    connection: ResolvedConnection,
    messages: list[dict],
    tools: list[dict],
    max_output_tokens: int,
) -> tuple[int, Decimal]:
    # One token per UTF-8 byte plus framing is a conservative byte-level-tokenizer bound,
    # unlike the UI's chars/4 estimate. Accepted models must satisfy this bound in probes.
    input_tokens = (
        len(canonical_json({"messages": messages, "tools": tools}).encode()) + 1024
    )
    if (
        max_output_tokens <= 0
        or input_tokens + max_output_tokens > connection.context_limit
    ):
        raise SkillsHubError(
            "context_limit",
            "Complete request exceeds the supported context budget.",
            status=422,
        )
    return input_tokens + max_output_tokens, (
        input_tokens * connection.input_price
        + max_output_tokens * connection.output_price
    ).quantize(Decimal("0.00000001"))


def _normal_message(raw: dict) -> dict:
    message = {"role": "assistant", "content": raw.get("content")}
    if message["content"] is not None and not isinstance(message["content"], str):
        raise SkillsHubError(
            "tool_protocol_invalid",
            "Provider returned unsupported assistant content.",
            status=422,
        )
    calls = raw.get("tool_calls")
    if calls:
        normalized, ids = [], set()
        for call in calls:
            function, call_id = call.get("function"), call.get("id")
            if (
                not isinstance(call_id, str)
                or not call_id
                or len(call_id) > 256
                or call_id in ids
                or call.get("type") != "function"
                or not isinstance(function, dict)
            ):
                raise SkillsHubError(
                    "tool_protocol_invalid",
                    "Provider tool-call identifiers are invalid.",
                    status=422,
                )
            if not isinstance(function.get("name"), str) or not isinstance(
                function.get("arguments"), str
            ):
                raise SkillsHubError(
                    "tool_protocol_invalid",
                    "Provider tool-call arguments are invalid.",
                    status=422,
                )
            ids.add(call_id)
            normalized.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": function["name"],
                        "arguments": function["arguments"],
                    },
                }
            )
        message["tool_calls"] = normalized
    # Nothing else is copied: no reasoning_content, provider metadata or hidden thinking.
    if len(canonical_json(message).encode()) > 65536:
        raise SkillsHubError(
            "evidence_limit",
            "Provider message exceeds the evidence allowance.",
            status=422,
        )
    return message


async def complete_skill_turn(
    *,
    connection: ResolvedConnection,
    messages: list[dict],
    tools: list[dict],
    parameters: dict,
    timeout_ms: int,
) -> CompletionResult:
    import litellm

    from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
        ModelHandlerContext,
    )
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
        PayloadBuilder,
    )
    from model_hub.services.skills_hub.v11.types import CompletionResult

    if (
        not connection.api_key
        or timeout_ms <= 0
        or set(parameters) != {"temperature", "max_output_tokens"}
    ):
        raise SkillsHubError(
            "invalid_test_config",
            "Explicit key, deadline and supported parameters are required.",
            status=400,
        )
    call_allowance(
        connection=connection,
        messages=messages,
        tools=tools,
        max_output_tokens=parameters["max_output_tokens"],
    )
    context = ModelHandlerContext(
        model=connection.model_id,
        messages=messages,
        organization_id=str(connection.scope.organization_id),
        workspace_id=str(connection.scope.workspace_id),
        temperature=parameters["temperature"],
        max_tokens=parameters["max_output_tokens"],
        tools=tools,
        tool_choice="auto" if tools else None,
    )
    payload = PayloadBuilder.build_llm_payload(
        context, connection.provider, connection.api_key
    )
    if (
        payload.get("api_key") != connection.api_key
        or payload.get("messages") != messages
        or (tools and payload.get("tools") != tools)
    ):
        raise SkillsHubError(
            "model_tools_unsupported",
            "Transport did not preserve required key or protocol.",
            status=422,
        )
    # Explicit keys must not be redirected by process-global API-base settings.
    payload["api_base"] = STANDARD_PROVIDER_BASES[connection.provider]
    payload["custom_llm_provider"] = connection.provider
    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            litellm.acompletion(
                **payload,
                timeout=timeout_ms / 1000,
                num_retries=0,
                drop_params=False,
                caching=False,
                stream=False,
            ),
            timeout=timeout_ms / 1000,
        )
    except (TimeoutError, litellm.Timeout) as error:
        raise SkillsHubError(
            "provider_timeout",
            "Provider response was not received before the deadline.",
            status=422,
        ) from error
    except litellm.AuthenticationError as error:
        raise SkillsHubError(
            "provider_auth_failed",
            "Provider rejected the workspace connection.",
            status=422,
        ) from error
    except litellm.RateLimitError as error:
        raise SkillsHubError(
            "provider_rate_limited", "Provider rate limit reached.", status=422
        ) from error
    except litellm.ContextWindowExceededError as error:
        raise SkillsHubError(
            "context_limit", "Provider context limit reached.", status=422
        ) from error
    except litellm.UnsupportedParamsError as error:
        raise SkillsHubError(
            "model_tools_unsupported",
            "Provider rejected required parameters.",
            status=422,
        ) from error
    except litellm.APIError as error:
        raise SkillsHubError(
            "execution_unknown", "Provider request outcome is unknown.", status=422
        ) from error
    except Exception as error:
        # Transport exceptions can contain credentials/payloads. Deliberately do not
        # serialize their message or exception chain into logs or Temporal history.
        logger.warning(
            "Unexpected Skills provider transport failure",
            extra={"error_type": type(error).__name__},
        )
        raise SkillsHubError(
            "execution_unknown", "Provider request outcome is unknown.", status=422
        ) from None
    if not response.choices or len(response.choices) != 1:
        raise SkillsHubError(
            "tool_protocol_invalid",
            "Provider returned an invalid choice count.",
            status=422,
        )
    choice = response.choices[0]
    assistant = _normal_message(choice.message.model_dump(exclude_none=False))
    usage = None
    cost = None
    if response.usage is not None:
        raw = response.usage.model_dump()
        prompt, completion = raw.get("prompt_tokens"), raw.get("completion_tokens")
        if (
            type(prompt) is int
            and type(completion) is int
            and prompt > 0
            and completion >= 0
        ):
            usage = {
                "input_tokens": prompt,
                "output_tokens": completion,
                "total_tokens": prompt + completion,
            }
            cost = (
                prompt * connection.input_price + completion * connection.output_price
            ).quantize(Decimal("0.00000001"))
    return CompletionResult(
        assistant_message=assistant,
        finish_reason=choice.finish_reason,
        provider_request_id=str(response.id)[:255] if response.id else None,
        returned_model=str(response.model)[:255],
        usage=usage,
        latency_ms=round((time.monotonic() - started) * 1000),
        estimated_cost_usd=cost,
        pricing_source=connection.pricing_source,
    )
