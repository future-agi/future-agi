"""Generation-owned LLM: one transport attempt, never a hidden retry/fallback.

The shared evaluation LLM keeps its interactive resilience policy. Background
resilience belongs to the worker's *new generation*, not to this client. Count
at the send boundary (not at content/tool helper boundaries), and retain only
finish/exception evidence, never prompts, response text or exception payloads.
"""

import asyncio
import time

from agentic_eval.core.llm.audio_utils import preprocess_messages_for_provider
from agentic_eval.core.llm.llm import LITELLM_REQUEST_TIMEOUT, LLM
from ee.agenthub.traceerroragent.background_provider import (
    direct_completion,
    normalize_response,
    provider_dialect,
    response_object,
)
from tracer.services.analysis_result_completion import (
    INTERMEDIATE_FINISHES,
    NORMAL_FINISHES,
    IncompleteAnalysis,
    _field,
)


class BackgroundLLM(LLM):
    def __init__(self, *, witness, **kwargs):
        self._witness = witness
        self._key = object()
        witness.client_tails[self._key] = None
        super().__init__(**kwargs)

    def _reject(self):
        self._witness.failed = True
        raise IncompleteAnalysis()

    def _attempt(self, send, *, raw_provider="openai", **kwargs):
        """Exactly one SDK/transport call; a swallowed error stays sticky."""
        if self._witness.failed or self._witness.fallback_used:
            self._reject()
        call = self._witness.start(self._key)
        call["exception"] = False
        self.last_finish_reason = None
        try:
            from tracer.services.background_transport import before_send

            before_send()
            response = send(**kwargs)
        except BaseException:
            # Every transport/SDK error is terminal, even if its SDK decoded the
            # error body first. Never inspect exception/body fields as evidence,
            # usage, tool calls or a reason to retry within this generation.
            call["exception"] = True
            call["ended"] = True
            self._witness.failed = True
            raise
        try:
            self._witness.observe_raw(call, raw_provider, response)
            response = normalize_response(response, raw_provider)
        except BaseException:
            call["ended"] = True
            self._witness.failed = True
            raise
        self._witness.end(call)
        self.last_finish_reason = call["finish"]
        if self._witness.failed or call["finish"] not in INTERMEDIATE_FINISHES:
            self._reject()
        limit = self._witness.max_calls
        if limit is not None and len(self._witness.calls) >= limit:
            self._reject()
        return response_object(response)

    def _remaining(self, deadline):
        if deadline is None:
            return LITELLM_REQUEST_TIMEOUT
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._witness.failed = True
            raise TimeoutError("tool completion deadline exceeded")
        return remaining

    def _sdk(self, client, deadline=None):
        # Never mutate the retained shared client's retry policy. Refuse an
        # opaque SDK that cannot prove its internal retries are disabled.
        if not callable(getattr(client, "with_options", None)):
            self._reject()
        scoped = client.with_options(max_retries=0, timeout=self._remaining(deadline))
        if scoped is client:
            self._reject()  # the raw error guard must never alter a retained client
        return scoped

    def _try_gateway_completion(self, payload, tools=None, deadline_monotonic=None):
        if self._requires_managed_transport(payload.get("model")):
            from tracer.services.background_transport import current_guard

            if deadline_monotonic is not None and current_guard() is None:
                self._reject()  # bounded managed sends require the transport guard
            return self._try_managed_ai_completion(
                payload, tools=tools, deadline=deadline_monotonic
            )
        if not getattr(self, "_gateway_client", None) or getattr(self, "api_key", None):
            return None  # no attempt, not a failed attempt followed by fallback
        self._require_retained_gateway_access()
        headers = {"X-Org-Id": self.org_id} if getattr(self, "org_id", None) else None
        kwargs = self._build_gateway_request_kwargs(payload, extra_headers=headers)
        if tools:
            kwargs["tools"] = tools
        from ee.agenthub.traceerroragent.background_gateway import sdk_completion

        client = self._sdk(self._gateway_client, deadline_monotonic)
        return self._attempt(sdk_completion, client=client, **kwargs)

    async def _try_gateway_completion_async(self, payload, tools=None):
        # Use the same verified one-shot send path, off the event loop.
        return await asyncio.to_thread(self._try_gateway_completion, payload, tools)

    def _try_managed_ai_completion(self, payload, tools=None, deadline=None):
        if not self._requires_managed_transport(payload.get("model")):
            return None
        from ee.agenthub.traceerroragent.background_gateway import managed_completion

        managed_payload = dict(payload)
        if tools:
            managed_payload["tools"] = tools
        return self._attempt(
            managed_completion, payload=managed_payload, deadline=deadline
        )

    async def _try_managed_ai_completion_async(self, payload, tools=None):
        return await asyncio.to_thread(self._try_managed_ai_completion, payload, tools)

    def _complete(self, payload, *, tools=None, deadline=None):
        try:
            provider_dialect(self.provider, direct=False)  # reject unknown identity
            if self.provider in {"protect", "protect_flash", "vllm"}:
                # Their legacy string/dict APIs discard terminal metadata.
                self._reject()
            if self.provider == "vertex_ai":
                payload.pop("max_tokens", None)
            for key in ("temperature", "max_tokens", "model", "provider"):
                value = payload.get(key)
                if isinstance(value, (list, tuple)) and len(value) == 1:
                    payload[key] = value[0]
            payload["messages"] = preprocess_messages_for_provider(
                payload["messages"], self.provider
            )
            response = self._try_gateway_completion(payload, tools, deadline)
            if response is None:
                dialect = provider_dialect(self.provider)
                options = getattr(self, "provider_api_keys", {}).get(self.provider, {})
                if not isinstance(options, dict):
                    options = {}
                response = self._attempt(
                    direct_completion,
                    raw_provider=dialect,
                    provider=self.provider,
                    payload=payload,
                    timeout=self._remaining(deadline),
                    api_key=getattr(self, "api_key", None),
                    region=options.get("aws_region"),
                    provider_options=options,
                )
            self._update_token_usage(response)
            self._update_cost(response)
            return response
        except BaseException:
            self._witness.failed = True
            raise

    def _get_completion_with_tools(
        self,
        messages,
        tools,
        model=None,
        tool_choice=None,
        drop_params=None,
        timeout_ms=None,
    ):
        try:
            if timeout_ms is not None and timeout_ms <= 0:
                self._reject()
            deadline = (
                time.monotonic() + timeout_ms / 1000 if timeout_ms is not None else None
            )
            payload = self._prepare_completion_payload(
                messages, response_format=None, model=model, drop_params=drop_params
            )
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
            return self._complete(payload, tools=tools, deadline=deadline)
        except BaseException:
            self._witness.failed = True
            raise

    def _get_completion_content(
        self,
        messages,
        model=None,
        response_format=None,
        drop_params=None,
        streaming=False,
        type=None,
        uuid=None,
        organization_id=None,
        current_activity=None,
        reasoning_effort=None,
        thinking_budget=None,
        disable_thinking=None,
    ):
        try:
            if streaming:
                # Background Judge/Chauffeur never publish WebSocket chunks.
                self._reject()
            payload = self._prepare_completion_payload(
                messages,
                response_format,
                model,
                reasoning_effort,
                thinking_budget,
                disable_thinking,
                drop_params,
            )
            response = self._complete(payload)
            content = _field(
                _field(_field(response, "choices")[0], "message"), "content"
            )
            if self.last_finish_reason not in NORMAL_FINISHES or not content:
                self._reject()
            return content
        except BaseException:
            self._witness.failed = True
            raise

    async def _get_completion_content_async(
        self,
        messages,
        model=None,
        response_format=None,
        streaming=False,
        type=None,
        uuid=None,
        organization_id=None,
        current_activity=None,
        reasoning_effort=None,
        thinking_budget=None,
        disable_thinking=None,
        ws_manager=None,
    ):
        return await asyncio.to_thread(
            self._get_completion_content,
            messages,
            model=model,
            response_format=response_format,
            streaming=streaming,
            reasoning_effort=reasoning_effort,
            thinking_budget=thinking_budget,
            disable_thinking=disable_thinking,
        )

    def call_llm(self, prompt, provider, response_format=None):
        if provider != self.provider:
            self._reject()
        return self._get_completion_content(prompt, response_format=response_format)

    # These are retry/fallback entrypoints too. None are valid for a background
    # analysis: refuse before sending, rather than calling a second provider or
    # returning output with no verifiable terminal metadata.
    def _handle_final_fallback(self, messages, payload):
        self._reject()

    async def _handle_final_fallback_async(self, messages, payload):
        self._reject()

    def _handle_protect_completion(self, payload):
        self._reject()

    def transcribe_audio(self, audio_input):
        self._reject()
