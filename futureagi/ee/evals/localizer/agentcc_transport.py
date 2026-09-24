"""Per-run Claude SDK -> AgentCC transport for Gemini multimodal inspection.

The CLI validates Claude model names and the Python SDK drops audio tool blocks.
AgentCC's Anthropic translator also flattens nested tool media to text. Tools
therefore return opaque, run-scoped media references. This loopback adapter
expands *issued tool results* into user media after the CLI, before AgentCC.
The existing gateway accepts base64 sources with their original MIME type and
passes them to Gemini inlineData, including audio. No media is transcribed.
"""

from __future__ import annotations

import copy
import json
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from aiohttp import web

MODEL = "vertex_ai/gemini-3.7-flash"
WIRE_MODEL = "claude-sonnet-4-6"


@dataclass
class GatewayConfig:
    url: str
    key: str = field(repr=False)
    timeout: float = 90
    max_turns: int = 10
    # This is the CLI's conservative Claude-priced limit, not Gemini billing.
    sdk_budget: float = 1.0

    @classmethod
    def from_env(cls):
        import os

        url = (
            (
                os.getenv("ERROR_LOCALIZER_AGENTCC_URL")
                or os.getenv("AGENTCC_BASE_URL")
                or os.getenv("AGENTCC_INTERNAL_URL")
                or os.getenv("AGENTCC_GATEWAY_URL")
                or "http://agentcc-gateway:8080"
            )
            .strip()
            .rstrip("/")
        )
        if url.endswith("/v1"):
            url = url[:-3]
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid error-localizer AgentCC URL")
        key = (
            os.getenv("ERROR_LOCALIZER_AGENTCC_API_KEY")
            or os.getenv("AGENTCC_HARNESS_API_KEY")
            or os.getenv("AGENTCC_API_KEY")
            or os.getenv("AGENTCC_INTERNAL_API_KEY")
            or ""
        ).strip()
        if not key:
            raise ValueError("Error localization requires an AgentCC API key")
        timeout = float(os.getenv("ERROR_LOCALIZER_TIMEOUT_SECONDS", "90"))
        turns = int(os.getenv("ERROR_LOCALIZER_MAX_TURNS", "10"))
        budget = float(os.getenv("ERROR_LOCALIZER_SDK_BUDGET_USD", "1"))
        if not 0 < timeout <= 600 or not 0 < turns <= 30 or not 0 < budget <= 100:
            raise ValueError("Invalid error-localizer time, turn, or budget limit")
        return cls(url, key, timeout, turns, budget)


class MediaRegistry:
    def __init__(self):
        self.blocks: dict[str, list[dict[str, Any]]] = {}

    def register(self, label: str, data: str, mime_type: str) -> str:
        if not mime_type.startswith(("image/", "audio/")):
            raise ValueError("Unsupported localization media type")
        ref = f"localizer-media:{secrets.token_hex(24)}"
        self.blocks[ref] = [
            {"type": "text", "text": label},
            # AgentCC carries this source through a data URI to Gemini. The
            # real MIME is essential: audio must never be labeled image/jpeg.
            {
                "type": "image",
                "source": {"type": "base64", "media_type": mime_type, "data": data},
            },
        ]
        return ref

    def expand(self, payload: dict) -> dict:
        payload = copy.deepcopy(payload)
        payload["model"] = MODEL
        for message in payload.get("messages", []):
            if message.get("role") != "user" or not isinstance(
                message.get("content"), list
            ):
                continue
            media = []
            seen = set()
            for block in message["content"]:
                if block.get("type") != "tool_result":
                    continue
                content = block.get("content", [])
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                for part in content:
                    ref = part.get("text") if isinstance(part, dict) else None
                    if isinstance(ref, str) and ref in self.blocks and ref not in seen:
                        media.extend(copy.deepcopy(self.blocks[ref]))
                        seen.add(ref)
            message["content"].extend(media)
        return payload


class AgentCCTransport:
    def __init__(
        self, config: GatewayConfig, media: MediaRegistry, metadata: dict | None = None
    ):
        self.config = config
        self.media = media
        self.metadata = metadata or {}
        self.token = secrets.token_urlsafe(32)
        self.url = ""
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.request_count = 0
        self._client = None

    def sdk_env(self, config_dir: str) -> dict[str, str]:
        env = {
            "ANTHROPIC_BASE_URL": self.url,
            "ANTHROPIC_AUTH_TOKEN": self.token,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_CUSTOM_HEADERS": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "CLAUDE_CODE_USE_VERTEX": "0",
            "CLAUDE_CODE_USE_BEDROCK": "0",
            "CLAUDE_CODE_USE_FOUNDRY": "0",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CONFIG_DIR": config_dir,
            "CLAUDECODE": "",
            "API_TIMEOUT_MS": str(int(self.config.timeout * 1000)),
        }
        for name in (
            "ANTHROPIC_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_SMALL_FAST_MODEL",
            "CLAUDE_CODE_SUBAGENT_MODEL",
        ):
            env[name] = WIRE_MODEL
        return env

    def _event(self, value: dict, usage: dict):
        if value.get("type") == "message_start":
            message = value.get("message", {})
            message["model"] = WIRE_MODEL
            usage.update(message.get("usage") or {})
        elif value.get("type") == "message_delta":
            usage.update(value.get("usage") or {})
        return value

    async def _handle(self, request: web.Request):
        if not secrets.compare_digest(
            request.headers.get("Authorization", ""), f"Bearer {self.token}"
        ):
            raise web.HTTPUnauthorized()
        if request.path not in {"/v1/messages", "/v1/messages/count_tokens"}:
            raise web.HTTPNotFound()
        payload = self.media.expand(await request.json())
        headers = {
            "Authorization": f"Bearer {self.config.key}",
            "anthropic-version": "2023-06-01",
            "x-agentcc-metadata": json.dumps(
                {"source": "error_localizer", **self.metadata}
            ),
        }
        # Do not forward CLI beta flags for Anthropic-only features to Gemini.
        # A count_tokens request cannot spend inference tokens.
        is_completion = request.path == "/v1/messages"
        usage = {}
        response = None
        try:
            async with self._client.stream(
                "POST", self.config.url + request.path, json=payload, headers=headers
            ) as upstream:
                if upstream.status_code >= 400:
                    # Never relay credential-bearing provider diagnostics to the SDK log.
                    return web.json_response(
                        {
                            "type": "error",
                            "error": {
                                "type": "api_error",
                                "message": f"AgentCC returned HTTP {upstream.status_code}",
                            },
                        },
                        status=upstream.status_code,
                    )
                if is_completion:
                    self.request_count += 1
                if "text/event-stream" not in upstream.headers.get("content-type", ""):
                    value = json.loads(await upstream.aread())
                    if is_completion:
                        usage.update(value.get("usage") or {})
                        value["model"] = WIRE_MODEL
                    return web.json_response(value)
                response = web.StreamResponse(
                    headers={
                        "Content-Type": "text/event-stream",
                        "Cache-Control": "no-cache",
                    }
                )
                await response.prepare(request)
                async for line in upstream.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            line = "data: " + json.dumps(
                                self._event(json.loads(line[6:]), usage)
                            )
                        except json.JSONDecodeError:
                            pass
                    await response.write((line + "\n").encode())
                await response.write_eof()
                return response
        except httpx.HTTPError:
            if response is not None and response.prepared:
                error = {
                    "type": "error",
                    "error": {"type": "api_error", "message": "AgentCC stream failed"},
                }
                await response.write(
                    f"event: error\ndata: {json.dumps(error)}\n\n".encode()
                )
                await response.write_eof()
                return response
            return web.json_response(
                {
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": "AgentCC transport failed",
                    },
                },
                status=502,
            )
        finally:
            # Anthropic message_delta reports cumulative output, not a delta.
            self.usage["prompt_tokens"] += int(usage.get("input_tokens") or 0)
            self.usage["completion_tokens"] += int(usage.get("output_tokens") or 0)

    @asynccontextmanager
    async def serve(self):
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_post("/{tail:.*}", self._handle)
        runner = web.AppRunner(
            app, access_log=None, shutdown_timeout=1, handler_cancellation=True
        )
        async with httpx.AsyncClient(
            timeout=self.config.timeout, follow_redirects=False, trust_env=False
        ) as client:
            self._client = client
            try:
                await runner.setup()
                site = web.TCPSite(runner, "127.0.0.1", 0)
                await site.start()
                self.url = f"http://127.0.0.1:{runner.addresses[0][1]}"
                yield self
            finally:
                await runner.cleanup()
                self._client = None
