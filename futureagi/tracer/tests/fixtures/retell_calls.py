"""Retell call payload factories shared by the poller tests and review probes.

Shapes follow Retell's documented ``POST /v3/list-calls`` and ``GET /v2/get-call``
responses (docs.retellai.com, 2026-09-03). Values are deliberately fictional
(555-01xx numbers, example.invalid hosts). Replace with a sanitised capture once a
restricted Retell key is available.
"""

from __future__ import annotations

from typing import Any

FAKE_AGENT_ID = "agent_fixture_0000000000000000"
FAKE_FROM = "+15550100001"
FAKE_TO = "+15550100002"


def list_item(
    call_id: str,
    start_ms: int | None,
    end_ms: int | None,
    *,
    call_status: str = "ended",
    agent_id: str = FAKE_AGENT_ID,
    **overrides: Any,
) -> dict[str, Any]:
    """One ``/v3/list-calls`` item: lean, no transcript or recording fields."""
    item: dict[str, Any] = {
        "call_id": call_id,
        "agent_id": agent_id,
        "agent_name": "Fixture Agent",
        "agent_version": 3,
        "call_type": "phone_call",
        "direction": "inbound",
        "from_number": FAKE_FROM,
        "to_number": FAKE_TO,
        "call_status": call_status,
        "start_timestamp": start_ms,
        "end_timestamp": end_ms,
        "duration_ms": (end_ms - start_ms) if end_ms is not None and start_ms is not None else None,
        "disconnection_reason": "user_hangup" if call_status == "ended" else "error_llm_websocket_lost_connection",
        "metadata": {},
        "retell_llm_dynamic_variables": {},
        "collected_dynamic_variables": {},
        "call_analysis": {
            "call_summary": "Caller asked about opening hours.",
            "user_sentiment": "Neutral",
            "call_successful": True,
            "in_voicemail": False,
            "custom_analysis_data": {},
        },
        "call_cost": {
            "product_costs": [
                {"product": "retell_platform", "unit_price": 0.1, "cost": 12.0},
                {"product": "elevenlabs_tts", "unit_price": 0.07, "cost": 8.4},
            ],
            "total_duration_seconds": 120,
            "total_duration_unit_price": 0.17,
            "combined_cost": 20.4,
        },
        "llm_token_usage": {"values": [512, 640], "average": 576, "num_requests": 2},
        "latency": {
            "e2e": {"p50": 900, "p90": 1300, "p95": 1450, "p99": 1700, "min": 700, "max": 1800, "num": 12, "values": [900, 1300]},
            "llm": {"p50": 500, "p90": 800, "p95": 900, "p99": 1100, "min": 400, "max": 1200, "num": 12, "values": [500, 800]},
            "tts": {"p50": 250, "p90": 400, "p95": 450, "p99": 500, "min": 200, "max": 520, "num": 12, "values": [250, 400]},
        },
    }
    item.update(overrides)
    return item


def detail(
    call_id: str,
    start_ms: int | None,
    end_ms: int | None,
    *,
    with_recording: bool = True,
    with_analysis: bool = True,
    null_fields: tuple[str, ...] = (),
    **overrides: Any,
) -> dict[str, Any]:
    """One ``/v2/get-call`` body: the list item plus transcript and recording fields.

    ``null_fields`` sets those keys to ``None`` in the detail (the list item keeps its
    value), which is the merge case the fetcher must handle.
    """
    body = list_item(call_id, start_ms, end_ms)
    body.update(
        {
            "transcript": "Agent: Hello, how can I help?\nUser: What are your opening hours?",
            "transcript_object": [
                {"role": "agent", "content": "Hello, how can I help?", "words": [{"word": "Hello,", "start": 0.4, "end": 0.7}, {"word": "help?", "start": 1.6, "end": 1.9}]},
                {"role": "user", "content": "What are your opening hours?", "words": [{"word": "What", "start": 2.5, "end": 2.7}, {"word": "hours?", "start": 3.8, "end": 4.1}]},
            ],
            "transcript_with_tool_calls": [
                {"role": "agent", "content": "Hello, how can I help?", "words": [{"word": "Hello,", "start": 0.4, "end": 0.7}, {"word": "help?", "start": 1.6, "end": 1.9}]},
                {"role": "user", "content": "What are your opening hours?", "words": [{"word": "What", "start": 2.5, "end": 2.7}, {"word": "hours?", "start": 3.8, "end": 4.1}]},
                {"role": "tool_call_invocation", "tool_call_id": "tc_fixture_1", "name": "lookup_hours", "arguments": "{\"location\": \"main\"}"},
                {"role": "tool_call_result", "tool_call_id": "tc_fixture_1", "content": "{\"hours\": \"9-5\"}"},
                {"role": "agent", "content": "We are open nine to five.", "words": [{"word": "We", "start": 5.0, "end": 5.1}, {"word": "five.", "start": 6.2, "end": 6.5}]},
            ],
            "public_log_url": "https://logs.example.invalid/fixture/" + call_id + ".txt",
        }
    )
    if with_recording:
        body["recording_url"] = "https://recordings.example.invalid/fixture/" + call_id + ".wav"
        body["recording_multi_channel_url"] = "https://recordings.example.invalid/fixture/" + call_id + "-stereo.wav"
    if not with_analysis:
        body["call_analysis"] = None
    for key in null_fields:
        body[key] = None
    body.update(overrides)
    return body


def list_page(items: list[dict[str, Any]], *, has_more: bool = False, pagination_key: str | None = None) -> dict[str, Any]:
    """A ``/v3/list-calls`` response envelope."""
    page: dict[str, Any] = {"items": items, "has_more": has_more}
    if pagination_key is not None:
        page["pagination_key"] = pagination_key
    return page
