"""Token estimation, model-aware budgets, and media detection.

Pure-function module: every helper here takes a message (or message
list) and returns a number or boolean. No state, no I/O. Easy to test
in isolation; safe to call from anywhere in the defense pipeline.

Thresholds are calibrated to the evaluator's Turing models
(``turing_large``, ``turing_small``, ``turing_flash``).
"""

import json
from typing import Iterable, List, Tuple

from ee.falcon_ai.context_manager import ContextManager


# ── Char-to-token ratio (single source of truth) ─────────────────────────
#
# Match falcon_ai's existing convention exactly so token estimates
# across the eval defense pipeline and the chat agent loop agree.
# Falcon AI's ContextManager.CHARS_PER_TOKEN = 3.5; we import that
# value rather than redefine it so any future tuning in falcon_ai
# automatically flows through here too.
CHARS_PER_TOKEN = ContextManager.CHARS_PER_TOKEN  # 3.5


# ── Turing token budgets ─────────────────────────────────────────────────

# Standard Turing models (``turing_large``, ``turing_small``,
# ``turing_flash``):
#   Soft = matches falcon_ai's COMPACTION_TOKEN_THRESHOLD (80K).
#   Hard = matches falcon_ai's MAX_HISTORY_TOKENS (120K).
#   Guard = soft-fail ceiling; L4 stashes a marker and lets the call through.
BUDGET_DEFAULT_SOFT = ContextManager.COMPACTION_TOKEN_THRESHOLD   # 80_000
BUDGET_DEFAULT_HARD = ContextManager.MAX_HISTORY_TOKENS           # 120_000
BUDGET_DEFAULT_GUARD = 180_000

# Higher-capacity internal variant for audio / PDF evals.
BUDGET_EXTENDED_SOFT = 500_000
BUDGET_EXTENDED_HARD = 700_000
BUDGET_EXTENDED_GUARD = 900_000


# ── Per-content-part token estimates ─────────────────────────────────────
#
# Conservative caps so we don't over-count (char/3.5 on base64 audio
# would trigger false-positive compaction) and don't under-count
# (would let real overflow slip past).
EST_TOKENS_PER_IMAGE = 1_500
EST_TOKENS_PER_AUDIO_PART = 10_000
EST_TOKENS_PER_FILE_PART = 10_000


# ── Per-message head+tail truncation threshold ───────────────────────────
#
# Single messages above this size get head+tail truncated by the L1
# defense layer. Set higher than ContextManager.MAX_MESSAGE_CHARS (4K)
# because eval system prompts + tool scaffolding can legitimately run
# 20-40K chars without being pathological.
PER_MESSAGE_CHARS_CAP = 80_000  # ~23K tokens at char/3.5


# ── Cap on the compaction call's OWN input ───────────────────────────────
#
# Prevents the summarizer call from itself emitting an empty response
# by sending an oversized prompt.
COMPACTION_INPUT_CAP_CHARS = 200_000


def budgets_for_model(model_name: str) -> Tuple[int, int, int]:
    """Return ``(soft, hard, guard)`` thresholds for a Turing model."""
    name = (model_name or "").lower()
    if name.endswith("_xl"):
        return BUDGET_EXTENDED_SOFT, BUDGET_EXTENDED_HARD, BUDGET_EXTENDED_GUARD
    return BUDGET_DEFAULT_SOFT, BUDGET_DEFAULT_HARD, BUDGET_DEFAULT_GUARD


# ── Token estimation (multimodal-aware) ──────────────────────────────────

def content_tokens(content) -> int:
    """Estimate tokens for any content shape: None / str / list of parts /
    other. Uses conservative caps on media parts so base64 doesn't trip
    char/4 over-counting.
    """
    if content is None:
        return 0
    if isinstance(content, str):
        return max(0, int(len(content) / CHARS_PER_TOKEN))
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                total += max(0, int(len(str(part)) / CHARS_PER_TOKEN))
                continue
            ptype = part.get("type")
            if ptype == "text":
                total += max(0, int(len(part.get("text") or "") / CHARS_PER_TOKEN))
            elif ptype == "image_url":
                total += EST_TOKENS_PER_IMAGE
            elif ptype in ("input_audio", "audio"):
                total += EST_TOKENS_PER_AUDIO_PART
            elif ptype == "file":
                total += EST_TOKENS_PER_FILE_PART
            else:
                try:
                    total += max(0, int(len(json.dumps(part, default=str)) / CHARS_PER_TOKEN))
                except (TypeError, ValueError):
                    total += max(0, int(len(str(part)) / CHARS_PER_TOKEN))
        return total
    # Other shapes — fall back to serialized length
    try:
        return max(0, int(len(json.dumps(content, default=str)) / CHARS_PER_TOKEN))
    except (TypeError, ValueError):
        return max(0, int(len(str(content)) / CHARS_PER_TOKEN))


def message_tokens(msg) -> int:
    """Estimate tokens for a single message, including its tool_calls
    bookkeeping field (which the chat completions payload sends to the
    gateway as part of the request body).
    """
    if not isinstance(msg, dict):
        return 0
    size = content_tokens(msg.get("content"))
    tcs = msg.get("tool_calls")
    if tcs:
        try:
            size += max(0, int(len(json.dumps(tcs, default=str)) / CHARS_PER_TOKEN))
        except (TypeError, ValueError):
            size += 50 * len(tcs)
    return size


def total_tokens(messages: Iterable) -> int:
    """Sum of ``message_tokens`` over a list. Handles ``None`` / empty
    safely.
    """
    if not messages:
        return 0
    return sum(message_tokens(m) for m in messages if m is not None)


# ── Multimodal detection ─────────────────────────────────────────────────

_MEDIA_PART_TYPES = frozenset(
    {"image_url", "input_audio", "audio", "file"},
)


def has_media(msg) -> bool:
    """``True`` if the message carries any image / audio / file content
    part. Such messages must never be sent to the summarizer LLM and
    must never be replaced by a digest — base64 payloads can't be
    summarized in text, and they're often the actual eval target.
    """
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    for part in content:
        if isinstance(part, dict) and part.get("type") in _MEDIA_PART_TYPES:
            return True
    return False


# ── Anchor / middle / recent partition ───────────────────────────────────

KEEP_RECENT_PAIRS = 4   # last N assistant→tool pairs preserved verbatim


def split_messages(
    messages: List,
) -> Tuple[List, List, List]:
    """Partition into ``(anchors, middle, recent)``.

    Anchors are the leading run of ``system`` messages plus the first
    ``user`` message after them (capped at 6 entries to defend against
    pathological inputs).

    Recent is the last ``KEEP_RECENT_PAIRS * 2`` messages — preserves the
    in-flight iteration's continuity so the agent doesn't lose track of
    its current tool calls mid-summarization.

    Middle is everything between — the compactable / droppable region.
    """
    if not messages:
        return [], [], []

    anchor_end = 0
    while (
        anchor_end < len(messages)
        and isinstance(messages[anchor_end], dict)
        and messages[anchor_end].get("role") == "system"
    ):
        anchor_end += 1
    # Pick up the first user message within a short lookahead window
    for i in range(anchor_end, min(anchor_end + 4, len(messages))):
        m = messages[i]
        if isinstance(m, dict) and m.get("role") == "user":
            anchor_end = i + 1
            break
    anchor_end = min(anchor_end, 6, len(messages))

    recent_count = KEEP_RECENT_PAIRS * 2
    recent_start = max(anchor_end, len(messages) - recent_count)

    return (
        messages[:anchor_end],
        messages[anchor_end:recent_start],
        messages[recent_start:],
    )
