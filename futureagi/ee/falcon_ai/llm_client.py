import asyncio
import copy
import json
import os
import random

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Status codes that are safe to retry (transient errors)
RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


def _inline_refs(schema, _defs=None, _depth=0):
    """Flatten $defs/$ref so Vertex AI accepts the schema; OpenAI/Anthropic tolerate.

    Sibling keys alongside $ref (e.g. description, default, title) are preserved
    and merged over the inlined target — JSON Schema 2020-12 semantics — so
    field-level documentation that helps the model call the tool is not lost.
    """
    if _depth > 30 or not isinstance(schema, (dict, list)):
        return schema
    if isinstance(schema, list):
        return [_inline_refs(x, _defs, _depth + 1) for x in schema]
    pool = {**(_defs or {}), **(schema.get("$defs") or {})}
    ref = schema.get("$ref", "")
    if isinstance(ref, str) and ref.startswith("#/$defs/") and ref[8:] in pool:
        inlined = _inline_refs(copy.deepcopy(pool[ref[8:]]), pool, _depth + 1)
        siblings = {k: _inline_refs(v, pool, _depth + 1)
                    for k, v in schema.items() if k not in ("$ref", "$defs")}
        if isinstance(inlined, dict) and siblings:
            return {**inlined, **siblings}  # siblings win on overlap (2020-12)
        return inlined
    return {k: _inline_refs(v, pool, _depth + 1) for k, v in schema.items() if k != "$defs"}


class EmptyStreamError(RuntimeError):
    """Raised when an upstream streaming response closes without events."""


class FalconLLMClient:
    """LLM client supporting Anthropic (default) and OpenAI-compatible APIs."""

    def __init__(self, provider=None, model=None, max_tokens=None, temperature=None):
        self.use_managed_gateway = provider is None
        self.provider = provider or os.environ.get("FALCON_AI_PROVIDER", "anthropic")
        # Default 16384 (TH-4501): the old 4096 cap truncated long tool-call
        # arguments — e.g. ``create_agent_definition(description=<long prompt>)``
        # lost content when the user's agent prompt exceeded ~16 KB, because the
        # model ran out of output budget mid-argument. 16K tokens gives ~64 KB of
        # output room while staying well within the per-call limits of every
        # provider we support (Claude Sonnet 4.x: 64K, GPT-4o: 16K, Gemini 2.5
        # Pro: 65K). Override via FALCON_AI_MAX_TOKENS if needed.
        self.max_tokens = max_tokens or int(
            os.environ.get("FALCON_AI_MAX_TOKENS", "16384")
        )
        self.temperature = (
            temperature
            if temperature is not None
            else float(os.environ.get("FALCON_AI_TEMPERATURE", "0.2"))
        )

        model_defaults = {
            "anthropic": "claude-sonnet-4-6",
            "bedrock": "us.anthropic.claude-sonnet-4-6",
            "openai": "gpt-4o-mini",
            "vertex_ai": "vertex_ai/gemini-2.5-pro",
        }
        if self.use_managed_gateway:
            self.model = model or "falcon_ai"
        else:
            self.model = model or os.environ.get(
                "FALCON_AI_MODEL",
                model_defaults.get(self.provider, "claude-sonnet-4-20250514"),
            )

        # Gateway cost from agentcc.metadata SSE event (accumulated across iterations)
        self._gateway_cost = 0.0

        # Optional response format (e.g. {"type": "json_schema", ...}) — when set,
        # included in OpenAI-compatible and gateway requests to enforce
        # structured output. Used by eval paths to guarantee valid JSON.
        self.response_format = None

        # Extended thinking — disabled by default, config-only for now
        self.enable_thinking = (
            os.environ.get("FALCON_AI_EXTENDED_THINKING", "").lower() == "true"
        )

        if self.use_managed_gateway:
            self.api_url = None
            self.api_key = None
        elif self.provider == "bedrock":
            # AWS Bedrock — uses boto3 with SigV4 auth, no API key needed
            self.bedrock_region = os.environ.get("AWS_BEDROCK_REGION", "us-west-2")
            self.api_url = None
            self.api_key = None
        elif self.provider == "anthropic":
            self.api_url = os.environ.get(
                "FALCON_AI_LLM_URL", "https://api.anthropic.com"
            )
            self.api_key = os.environ.get(
                "FALCON_AI_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "")
            )
        elif self.provider == "vertex_ai" or self.provider.startswith("turing"):
            self.api_url = os.environ.get(
                "AGENTCC_INTERNAL_URL",
                os.environ.get("AGENTCC_GATEWAY_URL", "http://agentcc-gateway:8090"),
            )
            self.api_key = os.environ.get("AGENTCC_INTERNAL_API_KEY", "")
        else:
            self.api_url = os.environ.get(
                "FALCON_AI_LLM_URL",
                os.environ.get("AGENTCC_GATEWAY_URL", "https://api.openai.com"),
            )
            self.api_key = os.environ.get(
                "FALCON_AI_API_KEY", os.environ.get("OPENAI_API_KEY", "")
            )

    async def stream_completion(self, messages, tools=None):
        """Yield streaming chunks in OpenAI format (normalized from any provider)."""
        if self.use_managed_gateway:
            async for chunk in self._stream_managed(messages, tools):
                yield chunk
        elif self.provider == "bedrock":
            async for chunk in self._stream_bedrock(messages, tools):
                yield chunk
        elif self.provider == "anthropic":
            async for chunk in self._stream_anthropic(messages, tools):
                yield chunk
        else:
            async for chunk in self._stream_openai(messages, tools):
                yield chunk

    async def _stream_managed(self, messages, tools=None):
        from ee.licensing.managed_ai import chat_completion, response_content

        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools or [],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if self.response_format:
            payload["response_format"] = self.response_format

        response = await asyncio.to_thread(chat_completion, payload)
        content = response_content(response)
        if content:
            yield {"choices": [{"delta": {"content": content}, "finish_reason": None}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    async def _stream_bedrock(self, messages, tools=None):
        """Stream from AWS Bedrock using boto3, yielding OpenAI-compatible chunks."""
        import boto3

        system_content, api_messages = self._convert_messages_to_anthropic(messages)

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.max_tokens,
            "messages": api_messages,
        }
        if system_content.strip():
            body["system"] = system_content.strip()

        if tools:
            anthropic_tools = []
            for t in tools:
                func = t.get("function", {})
                anthropic_tools.append(
                    {
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "input_schema": func.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    }
                )
            body["tools"] = anthropic_tools

        # Use an async queue so chunks are yielded incrementally
        q = asyncio.Queue()

        def _invoke():
            try:
                client = boto3.client(
                    "bedrock-runtime", region_name=self.bedrock_region
                )
                response = client.invoke_model_with_response_stream(
                    modelId=self.model,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(body),
                )
                for event in response["body"]:
                    chunk = json.loads(event["chunk"]["bytes"])
                    loop.call_soon_threadsafe(q.put_nowait, chunk)
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, {"error": str(e)})
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _invoke)

        # Convert Bedrock events to OpenAI-compatible format
        current_tool_use = None
        # Map Bedrock content_block index → contiguous OpenAI tool_calls index
        # so multiple parallel tool calls don't collapse to index=0.
        block_index_to_tool_index: dict[int, int] = {}
        next_tool_index = 0
        while True:
            event = await q.get()
            if event is None:
                break
            if "error" in event:
                raise RuntimeError(event["error"])
            event_type = event.get("type", "")

            if event_type == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use":
                    block_idx = event.get("index", 0)
                    tool_idx = next_tool_index
                    next_tool_index += 1
                    block_index_to_tool_index[block_idx] = tool_idx
                    current_tool_use = {
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "arguments": "",
                        "tool_index": tool_idx,
                    }
                    yield {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": tool_idx,
                                            "id": current_tool_use["id"],
                                            "function": {
                                                "name": current_tool_use["name"],
                                                "arguments": "",
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ],
                        "model": self.model,
                    }

            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    yield {
                        "choices": [
                            {
                                "delta": {"content": delta.get("text", "")},
                                "finish_reason": None,
                            }
                        ],
                        "model": self.model,
                    }
                elif delta.get("type") == "input_json_delta" and current_tool_use:
                    current_tool_use["arguments"] += delta.get("partial_json", "")
                    block_idx = event.get("index", 0)
                    tool_idx = block_index_to_tool_index.get(
                        block_idx, current_tool_use["tool_index"]
                    )
                    yield {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": tool_idx,
                                            "function": {
                                                "arguments": delta.get(
                                                    "partial_json", ""
                                                )
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ],
                        "model": self.model,
                    }

            elif event_type == "content_block_stop":
                if current_tool_use:
                    current_tool_use = None

            elif event_type == "message_delta":
                stop = event.get("delta", {}).get("stop_reason", "")
                usage = event.get("usage", {})
                finish = "tool_calls" if stop == "tool_use" else "stop"
                yield {
                    "choices": [{"delta": {}, "finish_reason": finish}],
                    "model": self.model,
                    "usage": {
                        "prompt_tokens": usage.get("input_tokens", 0),
                        "completion_tokens": usage.get("output_tokens", 0),
                    },
                }

    def _convert_messages_to_anthropic(self, messages):
        """Convert OpenAI-format messages to Anthropic format."""
        system_content = ""
        api_messages = []

        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role", "")

            if role == "system":
                system_content += msg["content"] + "\n"
                i += 1
                continue

            if role == "user":
                # Content can be a string or a list of content blocks (multimodal)
                api_messages.append({"role": "user", "content": msg["content"]})
                i += 1
                continue

            if role == "assistant":
                # Check if it has tool_calls (OpenAI format)
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    # Build Anthropic assistant message with tool_use blocks
                    content_blocks = []
                    if msg.get("content"):
                        content_blocks.append({"type": "text", "text": msg["content"]})
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                        content_blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc.get("id", ""),
                                "name": func.get("name", ""),
                                "input": args,
                            }
                        )
                    api_messages.append(
                        {"role": "assistant", "content": content_blocks}
                    )

                    # Now collect all following tool results into a single user message
                    tool_results = []
                    j = i + 1
                    while j < len(messages) and messages[j].get("role") == "tool":
                        tool_msg = messages[j]
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_msg.get("tool_call_id", ""),
                                "content": tool_msg.get("content", ""),
                            }
                        )
                        j += 1
                    if tool_results:
                        api_messages.append({"role": "user", "content": tool_results})
                    i = j
                    continue
                else:
                    api_messages.append(
                        {"role": "assistant", "content": msg.get("content", "")}
                    )
                    i += 1
                    continue

            if role == "tool":
                # Orphaned tool result (shouldn't happen but handle gracefully)
                api_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_call_id", ""),
                                "content": msg.get("content", ""),
                            }
                        ],
                    }
                )
                i += 1
                continue

            # Unknown role — skip
            i += 1

        return system_content.strip(), api_messages

    async def _stream_anthropic(self, messages, tools=None):
        """Stream from Anthropic Messages API, yielding OpenAI-compatible chunks."""
        system_content, api_messages = self._convert_messages_to_anthropic(messages)

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "stream": True,
            "messages": api_messages,
        }
        if system_content.strip():
            payload["system"] = system_content.strip()

        # TODO: When FALCON_AI_EXTENDED_THINKING=true, add thinking parameter:
        # payload["thinking"] = {"type": "enabled", "budget_tokens": 4096}
        # Requires anthropic-version 2025-04-01+ and temperature=1

        if tools:
            # Convert OpenAI tool format to Anthropic format
            anthropic_tools = []
            for t in tools:
                func = t.get("function", {})
                anthropic_tools.append(
                    {
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "input_schema": func.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    }
                )
            payload["tools"] = anthropic_tools

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                f"{self.api_url}/v1/messages",
                json=payload,
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    logger.error(
                        "anthropic_api_error",
                        status=response.status_code,
                        body=error_body.decode()[:500],
                    )
                response.raise_for_status()

                # Track tool use blocks being built
                current_tool_use = None

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    # Convert Anthropic SSE events to OpenAI-compatible chunks
                    if event_type == "content_block_start":
                        block = event.get("content_block", {})
                        if block.get("type") == "tool_use":
                            current_tool_use = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "arguments": "",
                            }
                            # Emit tool call start in OpenAI format
                            yield {
                                "choices": [
                                    {
                                        "delta": {
                                            "tool_calls": [
                                                {
                                                    "index": 0,
                                                    "id": current_tool_use["id"],
                                                    "function": {
                                                        "name": current_tool_use[
                                                            "name"
                                                        ],
                                                        "arguments": "",
                                                    },
                                                }
                                            ]
                                        },
                                        "finish_reason": None,
                                    }
                                ],
                                "model": self.model,
                            }

                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield {
                                "choices": [
                                    {
                                        "delta": {"content": delta.get("text", "")},
                                        "finish_reason": None,
                                    }
                                ],
                                "model": self.model,
                            }
                        elif delta.get("type") == "input_json_delta":
                            # Tool arguments streaming
                            if current_tool_use:
                                partial = delta.get("partial_json", "")
                                current_tool_use["arguments"] += partial
                                yield {
                                    "choices": [
                                        {
                                            "delta": {
                                                "tool_calls": [
                                                    {
                                                        "index": 0,
                                                        "function": {
                                                            "arguments": partial
                                                        },
                                                    }
                                                ]
                                            },
                                            "finish_reason": None,
                                        }
                                    ],
                                    "model": self.model,
                                }

                    elif event_type == "content_block_stop":
                        if current_tool_use:
                            current_tool_use = None

                    elif event_type == "message_delta":
                        stop_reason = event.get("delta", {}).get("stop_reason", "")
                        usage = event.get("usage", {})
                        # Map Anthropic stop reasons to OpenAI finish reasons
                        if stop_reason == "tool_use":
                            finish = "tool_calls"
                        elif stop_reason == "max_tokens":
                            finish = "max_tokens"
                        else:
                            finish = "stop"
                        yield {
                            "choices": [{"delta": {}, "finish_reason": finish}],
                            "model": self.model,
                            "usage": {
                                "prompt_tokens": usage.get("input_tokens", 0),
                                "completion_tokens": usage.get("output_tokens", 0),
                            },
                        }

                    elif event_type == "message_start":
                        msg = event.get("message", {})
                        usage = msg.get("usage", {})
                        if usage:
                            yield {
                                "choices": [{"delta": {}, "finish_reason": None}],
                                "model": msg.get("model", self.model),
                                "usage": {
                                    "prompt_tokens": usage.get("input_tokens", 0),
                                    "completion_tokens": usage.get("output_tokens", 0),
                                },
                            }

    async def _stream_openai(self, messages, tools=None):
        """Stream from OpenAI-compatible API."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            try:
                payload["tools"] = _inline_refs(tools)
            except Exception:
                logger.warning("tool_schema_flatten_failed_fallback_to_raw", exc_info=True)
                payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.response_format:
            try:
                payload["response_format"] = _inline_refs(self.response_format)
            except Exception:
                logger.warning("response_format_flatten_failed_fallback_to_raw", exc_info=True)
                payload["response_format"] = self.response_format

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "x-agentcc-include-metadata": "true",
        }

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                f"{self.api_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                last_usage = None
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    # Capture gateway billing metadata from the final chunk.
                    _agentcc_meta = chunk.get("agentcc_metadata")
                    if _agentcc_meta:
                        self._gateway_cost += _agentcc_meta.get("cost", 0)
                        usage = chunk.get("usage") or _agentcc_meta.get("usage")
                        if not usage:
                            prompt_tokens = _agentcc_meta.get(
                                "prompt_tokens", _agentcc_meta.get("input_tokens", 0)
                            )
                            completion_tokens = _agentcc_meta.get(
                                "completion_tokens",
                                _agentcc_meta.get("output_tokens", 0),
                            )
                            if prompt_tokens or completion_tokens:
                                usage = {
                                    "prompt_tokens": prompt_tokens,
                                    "completion_tokens": completion_tokens,
                                    "total_tokens": _agentcc_meta.get(
                                        "total_tokens",
                                        prompt_tokens + completion_tokens,
                                    ),
                                }
                        if usage:
                            usage = {
                                "prompt_tokens": usage.get(
                                    "prompt_tokens", usage.get("input_tokens", 0)
                                ),
                                "completion_tokens": usage.get(
                                    "completion_tokens", usage.get("output_tokens", 0)
                                ),
                                "total_tokens": usage.get("total_tokens")
                                or (
                                    usage.get("prompt_tokens", usage.get("input_tokens", 0))
                                    + usage.get(
                                        "completion_tokens", usage.get("output_tokens", 0)
                                    )
                                ),
                            }
                            chunk["usage"] = usage
                        if chunk.get("usage") and chunk["usage"] == last_usage:
                            chunk = {k: v for k, v in chunk.items() if k != "usage"}
                    elif chunk.get("usage"):
                        last_usage = chunk["usage"]
                    yield chunk

    async def stream_with_retry(self, messages, tools=None, max_retries=3):
        """Stream completion with retry on retryable errors.

        Uses exponential backoff with 25% jitter to prevent thundering herd.
        Retries on status codes in RETRYABLE_STATUS_CODES and on connection/timeout errors.
        """
        for attempt in range(max_retries):
            try:
                yielded_any_chunk = False
                async for chunk in self.stream_completion(messages, tools):
                    yielded_any_chunk = True
                    yield chunk
                if not yielded_any_chunk:
                    raise EmptyStreamError(
                        "LLM upstream returned an empty stream with no chunks"
                    )
                return
            except EmptyStreamError as e:
                if attempt >= max_retries - 1:
                    raise

                base_delay = min(2**attempt, 30)
                jitter = base_delay * 0.25 * random.random()
                delay = base_delay + jitter
                logger.warning(
                    "llm_retry_empty_stream",
                    error=str(e),
                    attempt=attempt + 1,
                    delay=round(delay, 2),
                )
                await asyncio.sleep(delay)
                continue
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status not in RETRYABLE_STATUS_CODES or attempt >= max_retries - 1:
                    raise

                # Exponential backoff with 25% jitter
                base_delay = min(2**attempt, 30)
                jitter = base_delay * 0.25 * random.random()
                delay = base_delay + jitter

                if status == 429:
                    # Honor Retry-After header if present
                    retry_after = e.response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = min(float(retry_after), 30.0)
                        except (ValueError, TypeError):
                            pass  # Fall through to computed delay
                    logger.warning(
                        "llm_retry_rate_limit",
                        status=429,
                        attempt=attempt + 1,
                        delay=round(delay, 2),
                    )
                else:
                    logger.warning(
                        "llm_retry",
                        status=status,
                        attempt=attempt + 1,
                        delay=round(delay, 2),
                    )
                await asyncio.sleep(delay)
                continue
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt >= max_retries - 1:
                    raise
                # Exponential backoff with 25% jitter
                base_delay = min(2**attempt, 30)
                jitter = base_delay * 0.25 * random.random()
                delay = base_delay + jitter
                logger.warning(
                    "llm_retry_connection",
                    error=str(e),
                    attempt=attempt + 1,
                    delay=round(delay, 2),
                )
                await asyncio.sleep(delay)
                continue

    async def _call_llm_simple(
        self,
        system_prompt,
        user_content,
        max_tokens=256,
        temperature=0.3,
        model=None,
        timeout=30.0,
    ):
        """Non-streaming LLM call. Returns the text response or empty string.

        Uses the managed gateway or the explicitly configured provider.
        """
        if self.use_managed_gateway:
            from ee.licensing.managed_ai import chat_completion, response_content

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": user_content})
            response = await asyncio.to_thread(
                chat_completion,
                {
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            return response_content(response)

        model = model or self.model

        if self.provider == "bedrock":
            import boto3
            from channels.db import database_sync_to_async

            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": user_content}],
            }
            if system_prompt:
                body["system"] = system_prompt

            def _invoke():
                client = boto3.client(
                    "bedrock-runtime", region_name=self.bedrock_region
                )
                resp = client.invoke_model(
                    modelId=model,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(body),
                )
                data = json.loads(resp["body"].read())
                content = data.get("content", [])
                if content and content[0].get("type") == "text":
                    return content[0]["text"].strip()
                return ""

            return await database_sync_to_async(_invoke)()

        if self.provider == "anthropic":
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": user_content}],
            }
            if system_prompt:
                payload["system"] = system_prompt
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            }
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self.api_url}/v1/messages", json=payload, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                content = data.get("content", [])
                if content and content[0].get("type") == "text":
                    return content[0]["text"].strip()
        else:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": user_content})
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self.api_url}/v1/chat/completions", json=payload, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()

        return ""

    async def generate_title(self, user_message, assistant_response):
        """Generate a short conversation title (non-streaming)."""
        user_content = (
            f"Generate a concise 3-5 word title for this conversation. "
            f"Reply with ONLY the title, nothing else.\n\n"
            f"User: {user_message[:200]}\nAssistant: {assistant_response[:200]}"
        )
        result = await self._call_llm_simple(
            system_prompt="",
            user_content=user_content,
            max_tokens=30,
            temperature=0.3,
            timeout=15.0,
        )
        return result.strip('"') if result else "New conversation"

    async def generate_summary(self, system_prompt, conversation_text):
        """Generate a context compaction summary using the LLM.

        Uses a smaller, cheaper model (haiku) for fast summarization.
        Keeps input under 30K chars to stay well within API limits.
        """
        # Truncate aggressively — we only need the key facts
        truncated = conversation_text[:30000]
        if len(conversation_text) > 30000:
            truncated += "\n\n[... older messages omitted ...]"

        user_content = f"Summarize this conversation:\n\n{truncated}"
        model = (
            "claude-haiku-4-5-20251001"
            if self.provider == "anthropic"
            else "gpt-4o-mini"
        )
        result = await self._call_llm_simple(
            system_prompt=system_prompt,
            user_content=user_content,
            max_tokens=2048,
            temperature=0.2,
            model=model,
            timeout=60.0,
        )
        return result or ""
