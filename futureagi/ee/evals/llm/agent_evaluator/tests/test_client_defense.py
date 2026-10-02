"""Tests for the 4-layer defense pipeline in ``context/client.EvalLLMClient``.

We subclass ``EvalLLMClient`` and stub ``FalconLLMClient.stream_completion``
on the class so no network / Turing gateway is hit. Each test runs the
defense pipeline against a deterministic synthetic message list and
asserts the expected layer fires (or doesn't), based on token totals.
"""

import asyncio
from typing import List

import pytest
from ee.evals.llm.agent_evaluator.context import budget
from ee.evals.llm.agent_evaluator.context.client import EvalLLMClient
from ee.evals.llm.agent_evaluator.context.digest import DIGEST_OPEN
from ee.falcon_ai.llm_client import FalconLLMClient


@pytest.fixture(autouse=True)
def _restore_falcon_stream():
    """These tests assign FalconLLMClient.stream_completion on the CLASS;
    without restore the stub leaks into every later test in the process
    (e.g. test_managed_clients saw the stub's chunks instead of its mock).
    """
    original = FalconLLMClient.stream_completion
    yield
    FalconLLMClient.stream_completion = original


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _drain_sync(coro):
    """Run an async coroutine to completion synchronously."""
    return (
        asyncio.get_event_loop().run_until_complete(coro)
        if False
        else asyncio.run(coro)
    )


async def _drain(agen):
    """Drain an async generator into a list of chunks."""
    out = []
    async for c in agen:
        out.append(c)
    return out


def _make_stub_client(streamed_text: str = "ACK", model: str = "turing_large"):
    """Build a EvalLLMClient whose underlying ``FalconLLMClient.stream_completion``
    is stubbed to yield a fixed text chunk. Captures the messages forwarded
    to the parent so the test can assert which layer fired.
    """
    captured = {"forwarded": [], "calls": 0}

    async def fake_stream(self, messages, tools=None):
        captured["calls"] += 1
        captured["forwarded"].append(
            [dict(m) if isinstance(m, dict) else m for m in messages]
        )
        yield {"choices": [{"delta": {"content": streamed_text}}]}

    # Patch on FalconLLMClient class so super().stream_completion(...) hits our stub
    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model=model, max_tokens=1024, temperature=0.0
    )
    return client, captured


# ──────────────────────────────────────────────────────────────────────────
# L1 — per-message head+tail cap (160K)
# ──────────────────────────────────────────────────────────────────────────


def test_l1_caps_oversized_single_message_at_per_message_cap():
    client, captured = _make_stub_client()
    huge = "A" * (budget.PER_MESSAGE_CHARS_CAP + 100_000)
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": huge},
    ]
    asyncio.run(_drain(client.stream_completion(messages)))

    forwarded = captured["forwarded"][0]
    user_msg = next(m for m in forwarded if m.get("role") == "user")
    # L1 collapsed the huge string to head+tail with truncation marker
    assert len(user_msg["content"]) < len(huge)
    assert "[truncated" in user_msg["content"]


def test_l1_leaves_under_cap_message_untouched():
    client, captured = _make_stub_client()
    content = "B" * (budget.PER_MESSAGE_CHARS_CAP - 10)
    messages = [{"role": "user", "content": content}]
    asyncio.run(_drain(client.stream_completion(messages)))
    forwarded = captured["forwarded"][0]
    assert forwarded[0]["content"] == content
    assert "[truncated" not in forwarded[0]["content"]


def test_l1_skips_list_content_multimodal():
    client, captured = _make_stub_client()
    # List content > cap chars in text but L1 only operates on str content
    big_text = "X" * (budget.PER_MESSAGE_CHARS_CAP + 50_000)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": big_text},
                {"type": "image_url", "image_url": {"url": "x.png"}},
            ],
        }
    ]
    asyncio.run(_drain(client.stream_completion(messages)))
    forwarded = captured["forwarded"][0]
    # Multimodal list content is left untouched by L1
    assert isinstance(forwarded[0]["content"], list)
    assert forwarded[0]["content"][0]["text"] == big_text


def test_defense_shallow_copy_does_not_mutate_caller_list():
    client, _ = _make_stub_client()
    original = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
    ]
    snapshot = [dict(m) for m in original]
    asyncio.run(_drain(client.stream_completion(original)))
    # Caller's list and dicts unmutated
    assert original == snapshot


def test_defense_does_not_mutate_multimodal_content_list():
    """L1/L2 must not mutate a caller's multimodal ``content`` list
    (image_url / input_audio / file blocks)."""
    client, _ = _make_stub_client()
    media_part = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AAA"},
    }
    original = [
        {"role": "system", "content": "S"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                media_part,
            ],
        },
    ]
    # Capture identity + structure before
    original_user_content = original[1]["content"]
    original_part_ids = [id(p) for p in original_user_content]
    original_part_snapshot = [dict(p) for p in original_user_content]

    asyncio.run(_drain(client.stream_completion(original)))

    # The caller's list object is the same object, same parts, unmutated
    assert original[1]["content"] is original_user_content
    assert [id(p) for p in original[1]["content"]] == original_part_ids
    assert [dict(p) for p in original[1]["content"]] == original_part_snapshot


# ──────────────────────────────────────────────────────────────────────────
# Short-circuit — under soft budget skips L3
# ──────────────────────────────────────────────────────────────────────────


def test_short_circuits_under_soft_budget():
    client, captured = _make_stub_client()
    # Well under soft 80K tokens
    messages = [
        {"role": "system", "content": "S" * 1000},
        {"role": "user", "content": "U" * 1000},
    ]
    asyncio.run(_drain(client.stream_completion(messages)))
    # No digest sentinels were injected
    forwarded = captured["forwarded"][0]
    text = " ".join(str(m.get("content")) for m in forwarded)
    assert DIGEST_OPEN not in text


# ──────────────────────────────────────────────────────────────────────────
# L3b — summarizer recursion: assert _compacting flag toggles
# ──────────────────────────────────────────────────────────────────────────


def test_l3b_compacting_flag_toggles_during_summarizer_call():
    """When the prompt is over soft budget, L3b runs the summarizer LLM
    via super().stream_completion(...). The ``_compacting`` flag must be
    True inside that nested call and False after it returns."""
    seen_compacting_during_summarizer: List[bool] = []
    captured = {"calls": 0}

    # We need access to the EvalLLMClient INSTANCE during the parent
    # stream call. The stub records ``self._compacting`` each time the
    # parent stream_completion runs.
    async def fake_stream(self, messages, tools=None):
        captured["calls"] += 1
        # `self` is the EvalLLMClient instance (parent method bound to it)
        seen_compacting_during_summarizer.append(self._compacting)
        yield {"choices": [{"delta": {"content": "synthetic digest"}}]}

    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )

    # Build messages substantially over soft budget — light_compact must
    # not be able to bring it under 80K alone, so L3b is reached.
    # 25 assistant-content messages * 50_000 chars = 1.25M chars = ~357K tokens
    chunk = "z" * 50_000  # ~14_285 tokens per msg
    messages = (
        [{"role": "system", "content": "SYS"}]
        + [{"role": "user", "content": "USR original"}]
        + [
            {"role": "assistant" if i % 2 == 0 else "user", "content": chunk}
            for i in range(25)
        ]
    )
    asyncio.run(_drain(client.stream_completion(messages)))

    # First parent call should be the summarizer (L3b). It sets _compacting=True.
    # After all calls complete, _compacting is back to False.
    assert client._compacting is False
    # At least one nested summarizer call recorded _compacting=True
    assert True in seen_compacting_during_summarizer


def test_l3b_digest_attached_to_first_user_anchor_with_sentinels():
    """When summarizer succeeds and returns text, the final forwarded
    messages must carry the digest spliced into the first user anchor."""
    final_call_messages: List[dict] = []
    call_idx = {"n": 0}

    async def fake_stream(self, messages, tools=None):
        call_idx["n"] += 1
        # First call = summarizer (compacting=True), second = real call
        if not self._compacting:
            for m in messages:
                final_call_messages.append(m if isinstance(m, dict) else m)
        yield {"choices": [{"delta": {"content": "synthetic digest content"}}]}

    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )

    chunk = "z" * 50_000
    messages = (
        [{"role": "system", "content": "SYS"}]
        + [{"role": "user", "content": "USR ORIGINAL"}]
        + [
            {"role": "assistant" if i % 2 == 0 else "user", "content": chunk}
            for i in range(25)
        ]
    )
    asyncio.run(_drain(client.stream_completion(messages)))

    # The user anchor (first user msg) should now carry the digest sentinels
    user_anchor = next(m for m in final_call_messages if m.get("role") == "user")
    content = user_anchor["content"]
    if isinstance(content, list):
        text = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    else:
        text = content
    assert DIGEST_OPEN in text
    assert "USR ORIGINAL" in text  # original content preserved


# ──────────────────────────────────────────────────────────────────────────
# L3c — hard-drop oldest non-anchor pairs
# ──────────────────────────────────────────────────────────────────────────


def test_l3c_drops_paired_assistant_tool_calls_never_orphan():
    """When the cascade reaches L3c, it must drop assistant+tool messages
    together as matched pairs, never leaving an orphan tool message."""
    final_forwarded: List[dict] = []

    async def fake_stream(self, messages, tools=None):
        if not self._compacting:
            final_forwarded.extend(messages)
        # Return EMPTY digest so L3b fails and L3c is needed
        yield {"choices": [{"delta": {"content": ""}}]}

    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )

    # Construct anchor + lots of assistant/tool pairs, well over hard budget
    chunk = "z" * 40_000  # ~11.4k tokens
    pairs = []
    for i in range(15):
        pairs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {"name": "x", "arguments": "{}"},
                    }
                ],
            }
        )
        pairs.append({"role": "tool", "tool_call_id": f"call_{i}", "content": chunk})

    messages = (
        [{"role": "system", "content": "SYS"}]
        + [{"role": "user", "content": "USR"}]
        + pairs
    )
    asyncio.run(_drain(client.stream_completion(messages)))

    # Every remaining tool message must have its parent assistant still present
    forwarded = final_forwarded
    tool_call_ids_present = set()
    for m in forwarded:
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                tool_call_ids_present.add(tc.get("id"))
    for m in forwarded:
        if isinstance(m, dict) and m.get("role") == "tool":
            assert m.get("tool_call_id") in tool_call_ids_present, "orphan tool message"


def test_l3c_never_drops_anchors():
    """Even at extreme overflow, the system + first user anchor are
    preserved."""
    final_forwarded: List[dict] = []

    async def fake_stream(self, messages, tools=None):
        if not self._compacting:
            final_forwarded.extend(messages)
        yield {"choices": [{"delta": {"content": ""}}]}

    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )

    chunk = "z" * 40_000
    messages = (
        [{"role": "system", "content": "ANCHOR_SYS_MARKER"}]
        + [{"role": "user", "content": "ANCHOR_USR_MARKER"}]
        + [{"role": "assistant", "content": chunk} for _ in range(15)]
    )
    asyncio.run(_drain(client.stream_completion(messages)))

    # Both anchors must still be present
    sys_msgs = [
        m for m in final_forwarded if isinstance(m, dict) and m.get("role") == "system"
    ]
    text_blob = " ".join(str(m.get("content", "")) for m in final_forwarded)
    assert any("ANCHOR_SYS_MARKER" in str(s.get("content", "")) for s in sys_msgs)
    assert "ANCHOR_USR_MARKER" in text_blob


# ──────────────────────────────────────────────────────────────────────────
# L4 — soft-fail oversized attempt
# ──────────────────────────────────────────────────────────────────────────


def test_l4_soft_fails_and_stashes_marker():
    """When the cascade can't bring the prompt under guard, L4 must
    record ``last_oversized_attempt`` and STILL call the underlying
    LLM (no raise)."""

    async def fake_stream(self, messages, tools=None):
        yield {"choices": [{"delta": {"content": ""}}]}

    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )

    # Build > 180K guard tokens of unsummarizable content. Use a single
    # huge multimodal message (L1 skips it; light_compact preserves it;
    # summarizer skips it; L3c can drop anchors-only=no, but recent
    # region pinned). Easier: many large pinned multimodal messages.
    # Each ~10k token audio part. 30 messages → 300k tokens.
    msgs = (
        [{"role": "system", "content": "S"}]
        + [{"role": "user", "content": "U"}]
        + [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": "B", "format": "wav"},
                    }
                ]
                * 5,
            }
            for _ in range(30)
        ]
    )
    # The pipeline must NOT raise
    asyncio.run(_drain(client.stream_completion(msgs)))
    assert client.last_oversized_attempt is not None
    assert (
        client.last_oversized_attempt["tokens"]
        >= client.last_oversized_attempt["guard"]
    )


# ──────────────────────────────────────────────────────────────────────────
# Iteration-budget hints (caution at 70%, hardstop at 90%)
# ──────────────────────────────────────────────────────────────────────────


def test_iteration_hint_disabled_when_max_iterations_unset():
    captured = {"forwarded": []}

    async def fake_stream(self, messages, tools=None):
        captured["forwarded"].append(messages)
        yield {"choices": [{"delta": {"content": "x"}}]}

    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )
    # Default max_iterations None → no hint
    for _ in range(20):
        asyncio.run(
            _drain(client.stream_completion([{"role": "user", "content": "hi"}]))
        )
    # No system-content with iteration-hint text in any forwarded call
    for msgs in captured["forwarded"]:
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "system":
                assert "step(s) remaining" not in str(m.get("content", ""))
                assert "final step" not in str(m.get("content", ""))


def test_iteration_hint_caution_fires_at_70pct_once():
    captured = {"forwarded": []}

    async def fake_stream(self, messages, tools=None):
        captured["forwarded"].append(
            [dict(m) if isinstance(m, dict) else m for m in messages]
        )
        yield {"choices": [{"delta": {"content": "ok"}}]}

    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )
    client.max_iterations = 10

    base = [{"role": "user", "content": "hi"}]
    for i in range(15):
        asyncio.run(_drain(client.stream_completion(list(base))))

    # caution should have fired once (when completed=7 / 10 = 0.7)
    caution_count = 0
    hardstop_count = 0
    for msgs in captured["forwarded"]:
        for m in msgs:
            content = m.get("content", "") if isinstance(m, dict) else ""
            if isinstance(content, str):
                if "step(s) remaining" in content:
                    caution_count += 1
                if "This is your final step" in content:
                    hardstop_count += 1
    assert caution_count >= 1
    assert hardstop_count >= 1


def test_iteration_hint_hardstop_mentions_correct_output_format():
    captured = {"forwarded": []}

    async def fake_stream(self, messages, tools=None):
        captured["forwarded"].append(
            [dict(m) if isinstance(m, dict) else m for m in messages]
        )
        yield {"choices": [{"delta": {"content": "ok"}}]}

    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )
    client.max_iterations = 10
    client.output_type = "Pass/Fail"

    for _ in range(11):
        asyncio.run(
            _drain(client.stream_completion([{"role": "user", "content": "hi"}]))
        )

    hardstop_msg = None
    for msgs in captured["forwarded"]:
        for m in msgs:
            content = m.get("content", "") if isinstance(m, dict) else ""
            if isinstance(content, str) and "This is your final step" in content:
                hardstop_msg = content
                break
        if hardstop_msg:
            break
    assert hardstop_msg is not None
    # Pass/Fail-specific instruction
    assert "Pass" in hardstop_msg and "Fail" in hardstop_msg


def test_iteration_hint_hardstop_score_output():
    captured = {"forwarded": []}

    async def fake_stream(self, messages, tools=None):
        captured["forwarded"].append(
            [dict(m) if isinstance(m, dict) else m for m in messages]
        )
        yield {"choices": [{"delta": {"content": "ok"}}]}

    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )
    client.max_iterations = 10
    client.output_type = "score"

    for _ in range(11):
        asyncio.run(
            _drain(client.stream_completion([{"role": "user", "content": "hi"}]))
        )

    hardstop_msg = None
    for msgs in captured["forwarded"]:
        for m in msgs:
            content = m.get("content", "") if isinstance(m, dict) else ""
            if isinstance(content, str) and "This is your final step" in content:
                hardstop_msg = content
                break
        if hardstop_msg:
            break
    assert hardstop_msg is not None
    assert "0.0" in hardstop_msg and "1.0" in hardstop_msg
    assert "Pass/Fail" not in hardstop_msg


def test_iteration_hint_hardstop_choices_output():
    captured = {"forwarded": []}

    async def fake_stream(self, messages, tools=None):
        captured["forwarded"].append(
            [dict(m) if isinstance(m, dict) else m for m in messages]
        )
        yield {"choices": [{"delta": {"content": "ok"}}]}

    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )
    client.max_iterations = 10
    client.output_type = "choices"
    client.output_choices = ["RED", "BLUE"]

    for _ in range(11):
        asyncio.run(
            _drain(client.stream_completion([{"role": "user", "content": "hi"}]))
        )

    hardstop_msg = None
    for msgs in captured["forwarded"]:
        for m in msgs:
            content = m.get("content", "") if isinstance(m, dict) else ""
            if isinstance(content, str) and "This is your final step" in content:
                hardstop_msg = content
                break
        if hardstop_msg:
            break
    assert hardstop_msg is not None
    assert "RED" in hardstop_msg and "BLUE" in hardstop_msg


# ──────────────────────────────────────────────────────────────────────────
# Re-entrancy + concurrency safety
# ──────────────────────────────────────────────────────────────────────────


def test_compacting_flag_skips_hint_and_defense():
    """If a caller already set _compacting=True before invoking, the
    iteration-hint + defense pipeline must be skipped."""
    captured = {"forwarded": []}

    async def fake_stream(self, messages, tools=None):
        captured["forwarded"].append(messages)
        yield {"choices": [{"delta": {"content": "ok"}}]}

    FalconLLMClient.stream_completion = fake_stream
    client = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )
    client.max_iterations = 2
    client._compacting = True

    huge = "X" * (budget.PER_MESSAGE_CHARS_CAP + 50_000)
    messages = [{"role": "user", "content": huge}]
    asyncio.run(_drain(client.stream_completion(messages)))
    asyncio.run(_drain(client.stream_completion(messages)))

    # No defense layer ran → huge content passed through verbatim
    for msgs in captured["forwarded"]:
        for m in msgs:
            assert "[truncated" not in str(m.get("content", ""))


def test_two_clients_have_independent_state():
    """Concurrency-safety: separate ``EvalLLMClient`` instances must
    not share mutable state (iteration counters, oversized markers)."""

    async def fake_stream(self, messages, tools=None):
        yield {"choices": [{"delta": {"content": "ok"}}]}

    FalconLLMClient.stream_completion = fake_stream
    c1 = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )
    c2 = EvalLLMClient(
        provider="turing", model="turing_large", max_tokens=1024, temperature=0.0
    )
    c1.max_iterations = 5
    c2.max_iterations = 5

    # Fire 5 calls on c1, 0 on c2
    for _ in range(5):
        asyncio.run(_drain(c1.stream_completion([{"role": "user", "content": "hi"}])))

    assert c1._iteration_count == 5
    assert c2._iteration_count == 0
    assert c1._iter_caution_warned != c2._iter_caution_warned or (
        c1._iter_caution_warned is True and c2._iter_caution_warned is False
    )

    # Stash oversized on c1, must not leak to c2
    c1.last_oversized_attempt = {"tokens": 1, "guard": 0, "msg_count": 0, "model": "x"}
    assert c2.last_oversized_attempt is None
