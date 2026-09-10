"""Metered gateway transport for every investigation stage."""

from ee.agenthub.trace_scanner.investigation import ModelReply
from ee.usage.services.gateway_llm_client import call_llm_raw, get_gateway_client


class InvestigationProvider:
    def __init__(self, model: str = "vertex_ai/gemini-3.8-flash"):
        self.model = model
        self.total_cost_usd = 0.0
        self.token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def __call__(
        self, messages: list[dict], tools: list[dict], max_tokens: int
    ) -> ModelReply:
        client = get_gateway_client()
        if client is None:
            # A content-only fallback would silently discard child tool calls and
            # metering. Missing transport is an operational failure, not success.
            raise RuntimeError("Scanner investigation requires a configured gateway")
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_completion_tokens": max_tokens,
        }
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        else:
            kwargs["response_format"] = {"type": "json_object"}
        # Deliberately do not disable model reasoning. The shared client handles
        # transient HTTP retries; the harness separately bounds logical calls.
        result = call_llm_raw(client, **kwargs)
        self.total_cost_usd += result.cost_usd
        response = result.response
        usage = getattr(response, "usage", None)
        call_usage = {}
        for name in self.token_usage:
            count = getattr(usage, name, 0) or 0
            self.token_usage[name] += count
            call_usage[name] = count
        if not response.choices:
            raise ValueError("Gateway returned no choices")
        choice = response.choices[0]
        if choice.finish_reason in ("length", "content_filter"):
            raise ValueError("Gateway response did not complete")
        message = choice.message
        return ModelReply(
            content=message.content,
            tool_calls=[
                call.model_dump(exclude_none=True)
                for call in (message.tool_calls or [])
            ],
            usage=call_usage,
        )
