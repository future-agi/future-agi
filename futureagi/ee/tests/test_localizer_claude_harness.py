"""Localization contracts plus the real SDK/CLI against a loopback gateway.

No provider credentials, database, S3, or running backend are used.
"""

import base64
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest
from aiohttp import web

from ee.evals.localizer.agentcc_transport import (
    MODEL,
    WIRE_MODEL,
    AgentCCTransport,
    GatewayConfig,
    MediaRegistry,
)
from ee.evals.localizer.claude_harness import (
    ClaudeAgentLocalizer,
    LocalizationCase,
    create_localizer,
)
from ee.evals.localizer.error_localizer import ErrorLocalizer

pytestmark = pytest.mark.unit


def localizer(data=None, types=None):
    return ClaudeAgentLocalizer(
        eval_name="Accuracy",
        rule_prompt="The response {{output}} must agree with {{expected}}.",
        input=data
        or {
            "output": "It was delivered. It arrives Friday.",
            "expected": "It arrives Monday.",
        },
        input_type=types or {"output": "text", "expected": "text"},
        evaluation_result="Failed",
        evaluation_explanation="The delivery date is incorrect.",
        choices=[],
    )


def finding(key="sentence_2", rank="1"):
    return {
        "unit_key": key,
        "rank": rank,
        "reason": "The stated date contradicts the order record.",
        "improvement": "Use the correct delivery date.",
        "rank_reason": "This explains the failed accuracy check.",
    }


def submission(key="output", entries=None, outcome="localized"):
    return {
        "selected_input_key": key,
        "outcome": outcome,
        "entries": entries if entries is not None else [finding()],
        "explanation": "The date is wrong.",
    }


async def test_sentences_attach_exact_original_offsets():
    case = LocalizationCase(localizer(), MediaRegistry())
    await case.inspect_input({"key": "expected"})
    await case.inspect_input({"key": "output"})
    await case.submit_findings(submission())
    item = case.result.analysis["input_1"][0]
    org = item["orgSen"]
    assert (
        case.localizer.input["output"][org["start_idx"] : org["end_idx"]]
        == "It arrives Friday."
    )
    assert case.result.selected_key == "output"
    assert item["rank"] == "1"


@pytest.mark.parametrize(
    "entries",
    [
        [finding("sentence_999")],
        [finding(rank="2")],
        [finding(), finding()],
        [finding(rank="0")],
    ],
)
async def test_rejects_unknown_units_and_invalid_ranks(entries):
    case = LocalizationCase(localizer(), MediaRegistry())
    await case.inspect_input({"key": "output"})
    with pytest.raises(ValueError):
        await case.submit_findings(submission(entries=entries))
    assert case.result is None


async def test_missing_evidence_is_explicit_not_a_generic_failure_finding():
    case = LocalizationCase(localizer(), MediaRegistry())
    with pytest.raises(ValueError):
        await case.submit_findings(submission())
    await case.submit_findings(submission(entries=[], outcome="unlocalizable"))
    assert case.result.analysis == {}
    assert case.result.skip_reason


async def test_whole_input_requires_inspection_and_attaches_full_text():
    case = LocalizationCase(localizer(), MediaRegistry())
    value = submission(entries=[finding("whole_text")], outcome="whole_input")
    with pytest.raises(ValueError):
        await case.submit_findings(value)
    await case.inspect_input({"key": "output"})
    await case.submit_findings(value)
    assert (
        case.result.analysis["input_1"][0]["orgSen"]["text"]
        == case.localizer.input["output"]
    )


def media_units(modality):
    if modality == "audio":
        return (
            {
                "segment_1": {
                    "audio_bytes": base64.b64encode(
                        b"raw-audio-not-a-transcript"
                    ).decode(),
                    "url": "https://storage.invalid/clip.mp3",
                    "start_time": 0,
                    "end_time": 5,
                    "duration": 5,
                }
            },
            [],
            None,
        )
    coords = {
        "top_left": [0, 0],
        "top_right": [20, 0],
        "bottom_left": [0, 20],
        "bottom_right": [20, 20],
    }
    return (
        {
            "patch_1": {
                "image_b64": base64.b64encode(b"image-bytes").decode(),
                "coordinates": coords,
            }
        },
        [],
        (20, 20),
    )


@pytest.mark.parametrize(
    "modality,unit,org",
    [("audio", "segment_1", "orgSegment"), ("image", "patch_1", "orgPatch")],
)
async def test_media_requires_inspection_and_preserves_locations(modality, unit, org):
    case = LocalizationCase(
        localizer({"output": "asset"}, {"output": modality}), MediaRegistry()
    )
    with patch(
        "ee.evals.localizer.claude_harness._chunk", return_value=media_units(modality)
    ):
        await case.inspect_input({"key": "output"})
        with pytest.raises(ValueError):
            await case.submit_findings(submission(entries=[finding(unit)]))
        content = await case.inspect_units({"key": "output", "unit_keys": [unit]})
        assert content["content"][0]["text"] in case.media.blocks
        await case.submit_findings(submission(entries=[finding(unit)]))
    assert org in case.result.analysis["input_1"][0]


def test_media_expansion_preserves_audio_mime_and_only_expands_tool_results():
    registry = MediaRegistry()
    raw = base64.b64encode(b"native audio").decode()
    ref = registry.register("clip at 0-5s", raw, "audio/mpeg")
    source = {
        "model": WIRE_MODEL,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": ref}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": ref}],
                    }
                ],
            },
        ],
    }
    expanded = registry.expand(source)
    assert expanded["model"] == MODEL
    assert expanded["messages"][0] == source["messages"][0]
    assert len(source["messages"][1]["content"]) == 1
    audio = expanded["messages"][1]["content"][-1]["source"]
    assert audio == {"type": "base64", "media_type": "audio/mpeg", "data": raw}
    assert MediaRegistry().expand(source)["messages"][1] == source["messages"][1]


def test_gateway_credentials_match_alk_and_clear_direct_provider_routes(monkeypatch):
    for name in (
        "ERROR_LOCALIZER_AGENTCC_URL",
        "ERROR_LOCALIZER_AGENTCC_API_KEY",
        "AGENTCC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTCC_BASE_URL", "http://gateway.invalid/v1/")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", "harness-test-secret")
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "internal-test-secret")
    config = GatewayConfig.from_env()
    assert config.url == "http://gateway.invalid"
    assert config.key == "harness-test-secret"
    assert "harness-test-secret" not in repr(config)
    transport = AgentCCTransport(config, MediaRegistry())
    transport.url = "http://127.0.0.1:9999"
    env = transport.sdk_env("/tmp/localizer-test")
    assert env["ANTHROPIC_BASE_URL"] == transport.url
    assert env["ANTHROPIC_AUTH_TOKEN"] == transport.token
    assert env["CLAUDE_CODE_USE_VERTEX"] == env["CLAUDE_CODE_USE_BEDROCK"] == "0"
    assert env["ANTHROPIC_API_KEY"] == ""


def test_factory_uses_sdk_by_default_and_supports_explicit_rollback(monkeypatch):
    kwargs = {
        "eval_name": "eval",
        "rule_prompt": "rule",
        "input": {},
        "input_type": {},
        "evaluation_result": 0,
        "evaluation_explanation": "failed",
        "choices": [],
    }
    monkeypatch.delenv("ERROR_LOCALIZER_BACKEND", raising=False)
    assert isinstance(create_localizer(**kwargs), ClaudeAgentLocalizer)
    monkeypatch.setenv("ERROR_LOCALIZER_BACKEND", "legacy")
    assert type(create_localizer(**kwargs)) is ErrorLocalizer
    monkeypatch.setenv("ERROR_LOCALIZER_BACKEND", "typo")
    with pytest.raises(ValueError):
        create_localizer(**kwargs)


@asynccontextmanager
async def server(handler):
    app = web.Application()
    app.router.add_post("/{tail:.*}", handler)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}"
    finally:
        await runner.cleanup()


async def test_adapter_forwards_only_authorized_requests_and_captures_usage():
    received = []

    async def upstream(request):
        received.append((await request.json(), dict(request.headers)))
        return web.json_response(
            {"model": MODEL, "usage": {"input_tokens": 20, "output_tokens": 5}}
        )

    async with server(upstream) as url:
        bridge = AgentCCTransport(
            GatewayConfig(url, "upstream-test-key"), MediaRegistry()
        )
        async with bridge.serve(), httpx.AsyncClient(trust_env=False) as client:
            assert (
                await client.post(bridge.url + "/v1/messages", json={})
            ).status_code == 401
            result = await client.post(
                bridge.url + "/v1/messages",
                json={"model": WIRE_MODEL},
                headers={"Authorization": f"Bearer {bridge.token}"},
            )
            assert result.json()["model"] == WIRE_MODEL
    assert len(received) == 1
    assert received[0][0]["model"] == MODEL
    assert received[0][1]["Authorization"] == "Bearer upstream-test-key"
    assert bridge.usage == {"prompt_tokens": 20, "completion_tokens": 5}


async def test_real_sdk_cli_transports_native_audio_and_calls_tools():
    """Exercise SDK -> CLI -> adapter -> fake AgentCC, including MCP callbacks."""
    pytest.importorskip("claude_agent_sdk")
    received = []

    async def upstream(request):
        payload = await request.json()
        if request.path.endswith("count_tokens"):
            return web.json_response({"input_tokens": 30})
        received.append(payload)
        assert payload["model"] == MODEL
        prior = sum(1 for m in payload.get("messages", []) if m["role"] == "assistant")
        calls = [
            ("inspect_input", {"key": "output"}),
            ("inspect_units", {"key": "output", "unit_keys": ["segment_1"]}),
            ("submit_findings", submission(entries=[finding("segment_1")])),
        ]
        if prior < len(calls):
            name, args = calls[prior]
            block = {
                "type": "tool_use",
                "id": f"call_{prior}",
                "name": f"mcp__localizer__{name}",
                "input": args,
            }
            stop = "tool_use"
        else:
            block = {"type": "text", "text": "Done."}
            stop = "end_turn"
        message = {
            "id": f"msg_{len(received)}",
            "type": "message",
            "role": "assistant",
            "model": MODEL,
            "content": [block],
            "stop_reason": stop,
            "usage": {"input_tokens": 30, "output_tokens": 10},
        }
        if not payload.get("stream"):
            return web.json_response(message)
        start = {
            **message,
            "content": [],
            "stop_reason": None,
            "usage": {"input_tokens": 30, "output_tokens": 0},
        }
        empty = (
            {**block, "input": {}}
            if block["type"] == "tool_use"
            else {"type": "text", "text": ""}
        )
        delta = (
            {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
            if block["type"] == "tool_use"
            else {"type": "text_delta", "text": block["text"]}
        )
        events = [
            {"type": "message_start", "message": start},
            {"type": "content_block_start", "index": 0, "content_block": empty},
            {"type": "content_block_delta", "index": 0, "delta": delta},
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 10},
            },
            {"type": "message_stop"},
        ]
        return web.Response(
            text="".join(
                f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
            ),
            content_type="text/event-stream",
        )

    el = localizer({"output": "audio"}, {"output": "audio"})
    async with server(upstream) as url:
        with patch(
            "ee.evals.localizer.claude_harness._chunk",
            return_value=media_units("audio"),
        ):
            result = await el._run(GatewayConfig(url, "fake-key", timeout=40))
    assert result.analysis["input_1"][0]["orgSegment"]["end_time"] == 5
    sources = [
        b["source"]
        for p in received
        for m in p.get("messages", [])
        for b in m.get("content", [])
        if isinstance(b, dict) and b.get("type") == "image"
    ]
    assert any(
        s["media_type"] == "audio/mpeg"
        and base64.b64decode(s["data"]) == b"raw-audio-not-a-transcript"
        for s in sources
    )
    assert result.cost["model"] == MODEL
    assert result.cost["token_usage"]["completion_tokens"] >= 30


@pytest.mark.parametrize("case", ["no_findings", "api_error", "max_turns", "timeout"])
async def test_incomplete_sdk_runs_fail_instead_of_synthesizing_findings(
    case, monkeypatch
):
    import asyncio
    from types import SimpleNamespace

    import claude_agent_sdk

    class Client:
        def __init__(self, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def query(self, prompt):
            pass

        async def receive_response(self):
            if case == "timeout":
                await asyncio.sleep(10)
            yield SimpleNamespace(
                is_error=case == "api_error",
                subtype="error_max_turns" if case == "max_turns" else "success",
                terminal_reason="api_error" if case == "api_error" else "completed",
            )

    @asynccontextmanager
    async def no_network(self):
        yield self

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Client)
    monkeypatch.setattr(claude_agent_sdk, "ResultMessage", SimpleNamespace)
    monkeypatch.setattr(AgentCCTransport, "serve", no_network)
    el = localizer()
    with pytest.raises(
        RuntimeError, match="time limit|without submitting|did not finish"
    ):
        await el._run(GatewayConfig("http://unused.invalid", "fake", timeout=0.05))
    assert el.cost["total_cost"] == 0


@pytest.mark.live_llm
@pytest.mark.parametrize("modality", ["text", "image", "audio"])
async def test_live_gemini_localization(modality, monkeypatch):
    """Opt-in synthetic smoke test; uses ALK credentials and makes no S3 writes.

    LOCALIZER_LIVE_ENV_FILE may point to a local env file. Only the named
    AgentCC settings are read; keys and provider responses are never printed.
    """
    import io
    import os

    path = os.getenv("LOCALIZER_LIVE_ENV_FILE")
    if path:
        from dotenv import dotenv_values

        values = dotenv_values(path)
        for name in (
            "AGENTCC_BASE_URL",
            "AGENTCC_HARNESS_API_KEY",
            "AGENTCC_API_KEY",
            "AGENTCC_INTERNAL_URL",
            "AGENTCC_INTERNAL_API_KEY",
        ):
            if values.get(name):
                monkeypatch.setenv(name, values[name])
    config = GatewayConfig.from_env()
    el = localizer()
    if modality == "image":
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (100, 100), "red").save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        units, _, dims = media_units("image")
        units["patch_1"]["image_b64"] = b64
        for corner in ("top_right", "bottom_left", "bottom_right"):
            units["patch_1"]["coordinates"][corner] = [
                v * 5 for v in units["patch_1"]["coordinates"][corner]
            ]
        blocks = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        ]
        monkeypatch.setattr(
            "ee.evals.localizer.claude_harness._chunk",
            lambda *a: (units, blocks, (100, 100)),
        )
        el = localizer({"output": "synthetic-image"}, {"output": "image"})
        el.rule_prompt = "The image must be entirely blue."
        el.evaluation_explanation = (
            "The image does not meet the requested color requirement."
        )
    elif modality == "audio":
        from pydub.generators import Sine

        buf = io.BytesIO()
        Sine(440).to_audio_segment(duration=5000).export(buf, format="mp3")
        units, blocks, dims = media_units("audio")
        units["segment_1"]["audio_bytes"] = base64.b64encode(buf.getvalue()).decode()
        monkeypatch.setattr(
            "ee.evals.localizer.claude_harness._chunk", lambda *a: (units, blocks, dims)
        )
        el = localizer({"output": "synthetic-audio"}, {"output": "audio"})
        el.rule_prompt = "The audio must contain a person saying a friendly greeting."
        el.evaluation_explanation = "The audio fails the spoken greeting criterion."
    result = await el._run(config)
    assert result.selected_key == "output"
    assert result.analysis and not result.skip_reason
    entry = result.analysis["input_1"][0]
    assert {"text": "orgSen", "image": "orgPatch", "audio": "orgSegment"}[
        modality
    ] in entry
    assert result.cost["model"] == MODEL
    assert result.cost["requests"] > 0
    # Observations must come from the media; the verdict does not reveal these.
    reason = entry["reason"].lower()
    if modality == "image":
        assert "red" in reason
    elif modality == "audio":
        assert any(
            word in reason for word in ("tone", "beep", "sine", "sound", "speech")
        )
