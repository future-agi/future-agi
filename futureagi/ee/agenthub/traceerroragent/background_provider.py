"""One-shot raw transports for generation-owned analysis, without LiteLLM.

These return successful provider JSON *before* finish normalization. Errors
are terminal sticky failures in the caller, never evidence or retry inputs.
No retry, fallback, message rewrite or process-global policy is involved.
Opaque/unsupported direct providers are refused, not guessed as OpenAI. Gateway
calls use the existing licensed transport, which returns OpenAI wire JSON.
"""

import json
import os
from types import SimpleNamespace

import httpx

from ee.falcon_ai.llm_client import FalconLLMClient
from tracer.services.analysis_result_completion import IncompleteAnalysis

# Frozen spellings from LiteLlmProvider/ModelConfigs plus Falcon's native alias.
# None explicitly means licensed gateway only: never guess a direct protocol.
# Changes to shared configs must update the closed configuration census.
PROVIDER_ROUTES = (
    ("aws_bedrock_anthropic", "bedrock"),
    ("bedrock", "bedrock"),
    ("anthropic", "anthropic"),
    ("openai", "openai"),
    ("vertex_ai", None),
    ("perplexity", None),
    ("groq", None),
    ("vllm", None),
    ("turing", None),
    ("protect", None),
    ("protect_flash", None),
)


def provider_dialect(provider, *, direct=True):
    for spelling, dialect in PROVIDER_ROUTES:
        if spelling == provider:
            if not direct:
                return "openai"  # the retained gateway's raw wire contract
            if dialect is not None:
                return dialect
            break
    raise IncompleteAnalysis()


def anthropic_payload(payload):
    # Falcon's interactive converter only accepts string-valued system content.
    # Separate those messages first: Judge uses cached text blocks, whose cache
    # controls and boundaries must reach the native API without flattening.
    system, conversation = [], []
    block_system = False
    for message in payload["messages"]:
        if message.get("role") != "system":
            conversation.append(message)
            continue
        content = message["content"]
        if isinstance(content, str):
            system.append({"type": "text", "text": content})
        elif isinstance(content, list) and all(
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
            for block in content
        ):
            block_system = True
            system.extend(dict(block) for block in content)
        else:
            raise IncompleteAnalysis()
    converter = FalconLLMClient.__new__(FalconLLMClient)
    _, messages = converter._convert_messages_to_anthropic(conversation)
    if not block_system:
        system = "\n".join(block["text"] for block in system).strip()
    body = {"messages": messages, "max_tokens": payload["max_tokens"]}
    if system:
        body["system"] = system
    if "temperature" in payload:
        body["temperature"] = payload["temperature"]
    if payload.get("tools"):
        body["tools"] = [
            {
                "name": tool["function"]["name"],
                "description": tool["function"].get("description", ""),
                "input_schema": tool["function"]["parameters"],
            }
            for tool in payload["tools"]
        ]
    choice = payload.get("tool_choice")
    if choice in ("auto", "required", "none"):
        body["tool_choice"] = {"type": {"required": "any"}.get(choice, choice)}
    elif isinstance(choice, dict) and choice.get("type") == "function":
        body["tool_choice"] = {"type": "tool", "name": choice["function"]["name"]}
    elif choice is not None:
        raise IncompleteAnalysis()
    # Do not silently discard a requested output schema or reasoning policy.
    if any(
        payload.get(key) for key in ("response_format", "thinking", "reasoning_effort")
    ):
        raise IncompleteAnalysis()
    return body


def direct_completion(
    *,
    provider,
    payload,
    timeout,
    api_key=None,
    api_url=None,
    region=None,
    provider_options=None,
):
    """A single network request. SDK retry disabling is instance-local."""
    from ee.agenthub.traceerroragent.background_payloads import original_json
    from tracer.services.background_transport import before_send, sync_client_options

    spelling = provider
    provider = provider_dialect(provider)
    if payload.get("stream"):
        raise IncompleteAnalysis()
    model = payload["model"]
    for prefix in (spelling, provider):
        if model.startswith(prefix + "/"):
            model = model[len(prefix) + 1 :]
            break
    if provider == "bedrock":
        import boto3
        from botocore.config import Config

        body = anthropic_payload(payload)
        body["anthropic_version"] = "bedrock-2023-05-31"
        options = provider_options or {}
        credentials = {
            target: options[source]
            for source, target in (
                ("aws_access_key", "aws_access_key_id"),
                ("aws_secret_key", "aws_secret_access_key"),
                ("aws_session_token", "aws_session_token"),
            )
            if options.get(source)
        }
        timeout = before_send(timeout)
        client = boto3.client(
            "bedrock-runtime",
            **credentials,
            region_name=region
            or os.environ.get("AWS_REGION_NAME")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION", "us-west-2"),
            config=Config(
                retries={"total_max_attempts": 1, "mode": "standard"},
                connect_timeout=timeout,
                read_timeout=timeout,
            ),
        )
        try:
            from ee.agenthub.traceerroragent.background_wire import guard_bedrock

            before_send(timeout)
            guard_bedrock(client)
            try:
                before_send(timeout)
                response = client.invoke_model(
                    modelId=model,
                    body=json.dumps(body),
                    contentType="application/json",
                    accept="application/json",
                )
            except Exception:
                # Botocore may decode ERROR bodies internally. They are never
                # evidence: discard the entire exception without reading its
                # response/message, and let the caller fail the witness stickily.
                # No exception text may become a report, usage or retry decision.
                raise IncompleteAnalysis() from None
            # Only successful content reaches the original-body decoder.
            stream = response["body"]
            try:
                return original_json(stream.read().decode("utf-8"))
            finally:
                stream.close()
        finally:
            client.close()
    if provider == "anthropic":
        body = anthropic_payload(payload)
        body["model"] = model
        url = (api_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        headers = {
            "x-api-key": api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
        }
    else:
        body = {
            key: value
            for key, value in payload.items()
            if key not in {"drop_params", "provider"}
        }
        body["model"] = model
        base = (api_url or "https://api.openai.com").rstrip("/")
        url = base + (
            "/chat/completions" if base.endswith("/v1") else "/v1/chat/completions"
        )
        headers = {
            "Authorization": "Bearer "
            + (api_key or os.environ.get("OPENAI_API_KEY", ""))
        }
    # HTTPX's default transport performs zero retries; redirects are disabled.
    timeout = before_send(timeout)
    with httpx.Client(
        timeout=timeout, follow_redirects=False, **sync_client_options()
    ) as client:
        timeout = before_send(timeout)
        response = client.post(url, json=body, headers=headers, timeout=timeout)
        response.raise_for_status()
        return original_json(response.content.decode("utf-8"))


class ResponseRecord(dict):
    """The content/tool API needs attributes and an SDK-style message dump."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def model_dump(self):
        return dict(self)


def response_object(value):
    if isinstance(value, SimpleNamespace):
        value = vars(value)
    if isinstance(value, dict):
        return ResponseRecord(
            {key: response_object(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return [response_object(item) for item in value]
    return value


def normalize_response(response, provider):
    """Called only AFTER the witness has accepted the raw terminal reason."""
    if provider == "openai":
        return response
    content, tools = [], []
    for block in response.get("content", []):
        if block.get("type") == "text":
            content.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tools.append(
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block["input"]),
                    },
                }
            )
    message = {
        "role": "assistant",
        "content": "".join(content),
        "tool_calls": tools or None,
    }
    usage = response.get("usage") or {}
    return {
        "model": response.get("model", ""),
        "choices": [
            {
                "message": message,
                "finish_reason": "tool_calls"
                if response["stop_reason"] == "tool_use"
                else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": (usage.get("input_tokens", 0) or 0)
            + (usage.get("output_tokens", 0) or 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        },
    }
