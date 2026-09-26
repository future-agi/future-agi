"""Unit tests for ``context/budget.py`` — token estimation, media
detection, anchor partitioning, and model-aware budgets.

Pure-function module → no fixtures, no I/O, no DB. Runs in seconds.
"""

from ee.evals.llm.agent_evaluator.context import budget
from ee.falcon_ai.context_manager import ContextManager


# ── CHARS_PER_TOKEN single-source-of-truth ───────────────────────────────


def test_chars_per_token_matches_falcon_ai():
    assert budget.CHARS_PER_TOKEN == ContextManager.CHARS_PER_TOKEN
    assert budget.CHARS_PER_TOKEN == 3.5


# ── content_tokens() ─────────────────────────────────────────────────────


def test_content_tokens_none_returns_zero():
    assert budget.content_tokens(None) == 0


def test_content_tokens_empty_string():
    assert budget.content_tokens("") == 0


def test_content_tokens_string_roughly_chars_over_ratio():
    s = "x" * 700
    # 700 / 3.5 = 200
    assert budget.content_tokens(s) == 200


def test_content_tokens_list_text_part():
    parts = [{"type": "text", "text": "x" * 350}]
    assert budget.content_tokens(parts) == 100  # 350/3.5


def test_content_tokens_list_image_part_uses_fixed_estimate():
    parts = [{"type": "image_url", "image_url": {"url": "https://example/x.png"}}]
    assert budget.content_tokens(parts) == budget.EST_TOKENS_PER_IMAGE


def test_content_tokens_list_audio_part():
    parts = [{"type": "input_audio", "input_audio": {"data": "BASE64BLOB", "format": "wav"}}]
    assert budget.content_tokens(parts) == budget.EST_TOKENS_PER_AUDIO_PART


def test_content_tokens_list_file_part():
    parts = [{"type": "file", "file": {"file_data": "BASE64PDF"}}]
    assert budget.content_tokens(parts) == budget.EST_TOKENS_PER_FILE_PART


def test_content_tokens_list_mixed_parts():
    parts = [
        {"type": "text", "text": "x" * 350},          # 100
        {"type": "image_url", "image_url": {}},        # 1500
        {"type": "input_audio", "input_audio": {}},    # 10_000
        {"type": "file", "file": {}},                  # 10_000
    ]
    expected = 100 + budget.EST_TOKENS_PER_IMAGE + budget.EST_TOKENS_PER_AUDIO_PART + budget.EST_TOKENS_PER_FILE_PART
    assert budget.content_tokens(parts) == expected


def test_content_tokens_list_unknown_part_falls_back_to_serialize():
    parts = [{"type": "weird", "payload": "x" * 35}]
    # serialized json length / 3.5
    n = budget.content_tokens(parts)
    assert n > 0


def test_content_tokens_list_non_dict_part_serializes():
    parts = ["a plain string in a list"]
    n = budget.content_tokens(parts)
    assert n > 0


def test_content_tokens_dict_serializes():
    n = budget.content_tokens({"key": "value" * 50})
    assert n > 0


# ── message_tokens() ─────────────────────────────────────────────────────


def test_message_tokens_non_dict_returns_zero():
    assert budget.message_tokens("not a dict") == 0
    assert budget.message_tokens(None) == 0


def test_message_tokens_counts_content():
    m = {"role": "user", "content": "x" * 350}
    assert budget.message_tokens(m) == 100


def test_message_tokens_counts_tool_calls_field():
    tc = [{"id": "call_1", "function": {"name": "lookup", "arguments": "{}"}}]
    m = {"role": "assistant", "content": None, "tool_calls": tc}
    base = budget.message_tokens({"role": "assistant", "content": None})
    with_tc = budget.message_tokens(m)
    assert with_tc > base


def test_message_tokens_tool_calls_unserializable_falls_back():
    class Weird:
        pass
    m = {"role": "assistant", "content": "", "tool_calls": [{"x": Weird()}]}
    # Should not raise
    assert budget.message_tokens(m) >= 0


# ── total_tokens() ───────────────────────────────────────────────────────


def test_total_tokens_empty():
    assert budget.total_tokens([]) == 0


def test_total_tokens_none():
    assert budget.total_tokens(None) == 0


def test_total_tokens_drops_none_entries():
    msgs = [
        {"role": "user", "content": "x" * 350},
        None,
        {"role": "assistant", "content": "y" * 350},
    ]
    assert budget.total_tokens(msgs) == 200


def test_total_tokens_heterogeneous():
    msgs = [
        {"role": "system", "content": "s" * 70},
        {"role": "user", "content": [{"type": "text", "text": "u" * 70}]},
        {"role": "assistant", "content": [{"type": "image_url", "image_url": {}}]},
    ]
    expected = 20 + 20 + budget.EST_TOKENS_PER_IMAGE
    assert budget.total_tokens(msgs) == expected


# ── has_media() ──────────────────────────────────────────────────────────


def test_has_media_string_content_false():
    assert budget.has_media({"role": "user", "content": "just text"}) is False


def test_has_media_list_no_media_false():
    m = {"role": "user", "content": [{"type": "text", "text": "hello"}]}
    assert budget.has_media(m) is False


def test_has_media_list_with_image_true():
    m = {"role": "user", "content": [{"type": "image_url", "image_url": {}}]}
    assert budget.has_media(m) is True


def test_has_media_list_with_audio_true():
    m = {"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]}
    assert budget.has_media(m) is True


def test_has_media_list_with_file_true():
    m = {"role": "user", "content": [{"type": "file", "file": {}}]}
    assert budget.has_media(m) is True


def test_has_media_non_dict_msg_false():
    assert budget.has_media("not a dict") is False
    assert budget.has_media(None) is False


def test_has_media_malformed_list_no_crash():
    # Non-dict entries in list
    m = {"role": "user", "content": ["raw", 42, None]}
    assert budget.has_media(m) is False


# ── split_messages() ─────────────────────────────────────────────────────


def test_split_messages_empty():
    a, m, r = budget.split_messages([])
    assert a == [] and m == [] and r == []


def test_split_messages_only_system():
    msgs = [{"role": "system", "content": "S"}]
    a, m, r = budget.split_messages(msgs)
    # No user found — anchor_end stays at end of leading systems, then min(anchor_end, 6, len)
    assert a == msgs
    assert m == []
    assert r == []


def test_split_messages_anchors_system_then_user():
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
    ]
    a, m, r = budget.split_messages(msgs)
    assert a == msgs
    assert m == []
    # When anchor consumes all messages and recent_count > 0, recent_start >= anchor_end
    assert r == []


def test_split_messages_with_middle_and_recent():
    msgs = (
        [{"role": "system", "content": "S"}]
        + [{"role": "user", "content": "U0"}]
        + [{"role": "assistant", "content": f"A{i}"} for i in range(20)]
    )
    a, m, r = budget.split_messages(msgs)
    # 1 system + 1 user as anchor
    assert len(a) == 2
    # recent is last KEEP_RECENT_PAIRS*2 = 8
    assert len(r) == budget.KEEP_RECENT_PAIRS * 2
    # middle is the rest
    assert len(m) == len(msgs) - len(a) - len(r)


def test_split_messages_only_user_no_system():
    msgs = [{"role": "user", "content": "U"}]
    a, m, r = budget.split_messages(msgs)
    # No leading system; first-user lookahead window starts at 0 and consumes the user
    assert a == msgs
    assert m == []
    assert r == []


def test_split_messages_no_anchors_at_all():
    # No system, no user — only assistants. Lookahead window fails; anchor_end stays at 0.
    msgs = [{"role": "assistant", "content": f"A{i}"} for i in range(10)]
    a, m, r = budget.split_messages(msgs)
    assert a == []
    # recent = last 8
    assert len(r) == budget.KEEP_RECENT_PAIRS * 2
    assert len(m) == len(msgs) - len(r)


def test_split_messages_caps_anchors_at_six():
    # 10 leading system msgs — anchor cap = 6
    msgs = [{"role": "system", "content": f"S{i}"} for i in range(10)]
    msgs.append({"role": "user", "content": "U"})
    a, m, r = budget.split_messages(msgs)
    assert len(a) == 6


# ── budgets_for_model() ──────────────────────────────────────────────────


def test_budgets_for_model_default():
    s, h, g = budget.budgets_for_model("turing_large")
    assert s == budget.BUDGET_DEFAULT_SOFT
    assert h == budget.BUDGET_DEFAULT_HARD
    assert g == budget.BUDGET_DEFAULT_GUARD


def test_budgets_for_model_xl_variant():
    s, h, g = budget.budgets_for_model("turing_large_xl")
    assert s == budget.BUDGET_EXTENDED_SOFT
    assert h == budget.BUDGET_EXTENDED_HARD
    assert g == budget.BUDGET_EXTENDED_GUARD


def test_budgets_for_model_empty_falls_back_to_default():
    s, h, g = budget.budgets_for_model("")
    assert (s, h, g) == (budget.BUDGET_DEFAULT_SOFT, budget.BUDGET_DEFAULT_HARD, budget.BUDGET_DEFAULT_GUARD)


def test_budgets_for_model_none_falls_back_to_default():
    s, h, g = budget.budgets_for_model(None)
    assert (s, h, g) == (budget.BUDGET_DEFAULT_SOFT, budget.BUDGET_DEFAULT_HARD, budget.BUDGET_DEFAULT_GUARD)


def test_budgets_for_model_unknown_name_falls_back_to_default():
    s, h, g = budget.budgets_for_model("some_unknown_alias")
    assert (s, h, g) == (budget.BUDGET_DEFAULT_SOFT, budget.BUDGET_DEFAULT_HARD, budget.BUDGET_DEFAULT_GUARD)


# ── PER_MESSAGE_CHARS_CAP pinned value ───────────────────────────────────


def test_per_message_cap_value():
    assert budget.PER_MESSAGE_CHARS_CAP == 80_000
