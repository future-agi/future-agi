"""
tool_call_loop_repetition.py

A local (no-LLM, no network call) evaluator + real-time guardrail that detects
when an AI agent is stuck in a tool-call loop: repeating the same tool call,
oscillating between a small cycle of calls, or issuing near-duplicate calls
that differ only in cosmetic ways (timestamps, request ids, whitespace) while
making no real progress.

Why this metric exists
-----------------------
Agent loops are one of the most common and expensive agentic failure modes in
production: an agent calls the same search/API/tool over and over, burns
tokens and provider spend, and never reaches a terminal state. Existing local
metrics in this project cover output quality (groundedness, hallucination,
toxicity, PII, tone) and single-call tool-use correctness, but nothing
currently inspects the *shape of the tool-call sequence itself* to catch
non-terminating behavior. This module fills that gap.

Design goals (matching this project's local-metric conventions)
-----------------------------------------------------------------
- Deterministic and dependency-free (stdlib only) so it can run in the
  sub-10ms "local metrics" / guardrail tier, not the LLM-judge tier.
- Usable two ways:
    1. Post-hoc, over a full trace  -> ToolCallLoopDetector.evaluate(...)
    2. Real-time / streaming, one call at a time -> ToolCallLoopGuardrail
       (for wiring into the gateway / inline guardrail path).
- Returns a score in [0, 1] where 1.0 = no loop detected ("safe", consistent
  with how this project scores other guardrails such as toxicity, i.e.
  higher = safer) plus a human-readable reason and structured diagnostics,
  so it slots into the existing EvalResult-style contract.

Integration notes (for the PR)
-------------------------------
This file is written to be dropped into the metrics package (e.g.
`futureagi/agentic_eval/metrics/tool_call_loop_repetition.py`) and registered
in the local metrics registry the same way `toxicity`, `pii_detection`, etc.
are registered, so it becomes callable as:

    from fi.evals import evaluate
    result = evaluate("tool_call_loop_repetition", tool_calls=trace.tool_calls)

The exact registry hook / base-metric class name will need to match whatever
this repo actually calls it internally (I could not browse the private class
hierarchy directly — see PR description). `ToolCallLoopDetector` below has no
required base class, so it can be adapted to subclass the project's real
`BaseMetric` / `BaseGuardrail` with minimal changes; the detection logic
itself does not need to move.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A single tool/function call made by an agent during a run."""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Any] = None  # optional: the tool's return value/observation
    index: Optional[int] = None  # optional: position in the full trace


@dataclass
class LoopEvalResult:
    """Result contract, mirroring this project's local-metric EvalResult shape
    (score, passed, reason) plus extra structured diagnostics useful for the
    dashboard / trace inspector."""

    score: float  # 1.0 = healthy, 0.0 = severe loop
    passed: bool
    reason: str
    loop_detected: bool
    loop_type: Optional[str] = None  # "exact_repeat" | "oscillation" | "near_duplicate"
    cycle_length: Optional[int] = None
    repeat_count: Optional[int] = None
    first_loop_start_index: Optional[int] = None
    involved_tools: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "passed": self.passed,
            "reason": self.reason,
            "loop_detected": self.loop_detected,
            "loop_type": self.loop_type,
            "cycle_length": self.cycle_length,
            "repeat_count": self.repeat_count,
            "first_loop_start_index": self.first_loop_start_index,
            "involved_tools": self.involved_tools,
        }


# ---------------------------------------------------------------------------
# Normalization / similarity helpers
# ---------------------------------------------------------------------------

# Keys commonly used for nonces / timestamps / trace ids that should NOT count
# as "the arguments changed" when comparing calls for repetition.
_DEFAULT_IGNORED_ARG_KEYS = frozenset({
    "timestamp", "ts", "request_id", "trace_id", "nonce", "idempotency_key",
    "call_id", "id", "created_at",
})


def _normalize_arguments(
    arguments: Dict[str, Any],
    ignored_keys: frozenset,
) -> str:
    """Serialize arguments to a stable, comparable string, dropping
    cosmetic/volatile keys and normalizing key order and whitespace."""
    cleaned = {
        k: v for k, v in (arguments or {}).items() if k not in ignored_keys
    }
    try:
        return json.dumps(cleaned, sort_keys=True, default=str)
    except TypeError:
        # Fall back to a best-effort string representation for
        # non-JSON-serializable arguments.
        return str(sorted(cleaned.items(), key=lambda kv: str(kv[0])))


def _signature(call: ToolCall, ignored_keys: frozenset) -> str:
    """A comparable signature for a call: tool name + normalized arguments."""
    return f"{call.name}::{_normalize_arguments(call.arguments, ignored_keys)}"


def _similarity(a: str, b: str) -> float:
    """Cheap, dependency-free string similarity in [0, 1]."""
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def _find_repeating_cycle(
    signatures: Sequence[str],
    max_cycle_length: int = 4,
    min_repeats: int = 3,
) -> Optional[Dict[str, int]]:
    """Look at the *tail* of the signature sequence and check whether it is
    made of a short repeating cycle (e.g. A,B,A,B,A,B or A,A,A,A).

    Returns {"cycle_length": k, "repeat_count": n, "start_index": i} for the
    longest qualifying repetition found, or None.
    """
    n = len(signatures)
    best: Optional[Dict[str, int]] = None

    for cycle_length in range(1, max_cycle_length + 1):
        if cycle_length * min_repeats > n:
            continue

        # Compare every preceding chunk of this length against the final
        # chunk in the sequence; count how many consecutive chunks (walking
        # backwards from the end) are identical to it.
        last_chunk = signatures[n - cycle_length:n]
        repeats = 1
        pos = n - cycle_length
        while pos - cycle_length >= 0:
            chunk = signatures[pos - cycle_length:pos]
            if chunk == last_chunk:
                repeats += 1
                pos -= cycle_length
            else:
                break

        if repeats >= min_repeats:
            start_index = pos
            candidate = {
                "cycle_length": cycle_length,
                "repeat_count": repeats,
                "start_index": start_index,
            }
            # Prefer the result that covers more of the tail (repeats * cycle_length),
            # and among ties prefer the smallest cycle (simplest explanation).
            if best is None or (
                candidate["repeat_count"] * candidate["cycle_length"]
                > best["repeat_count"] * best["cycle_length"]
            ):
                best = candidate

    return best


# ---------------------------------------------------------------------------
# Core detector (post-hoc, full-trace evaluation)
# ---------------------------------------------------------------------------

class ToolCallLoopDetector:
    """Detects non-terminating / repetitive tool-call behavior in an agent
    trace.

    Parameters
    ----------
    min_repeats:
        Minimum number of consecutive cycle repetitions required before a
        loop is flagged (default 3, e.g. A,A,A or A,B,A,B,A,B).
    max_cycle_length:
        Longest call-cycle to search for (default 4). Larger values catch
        longer repeating patterns at extra compute cost; 3-4 covers the vast
        majority of real agent loops (single-call retry loops and simple
        two/three-step oscillations).
    near_duplicate_threshold:
        Similarity in [0, 1] above which two calls' arguments are treated as
        "the same call" even if not byte-identical (default 0.92). Set to
        1.0 to require exact matches only.
    ignored_argument_keys:
        Argument keys to ignore when comparing calls (timestamps, request
        ids, etc). Defaults to a common set; pass your own frozenset to
        override.
    also_compare_results:
        If True (default), and tool results are provided, identical/near
        identical results across a repeated call further increase confidence
        that no real progress is being made (vs. e.g. legitimate polling
        where the result changes each time).
    """

    def __init__(
        self,
        min_repeats: int = 3,
        max_cycle_length: int = 4,
        near_duplicate_threshold: float = 0.92,
        ignored_argument_keys: frozenset = _DEFAULT_IGNORED_ARG_KEYS,
        also_compare_results: bool = True,
    ) -> None:
        if min_repeats < 2:
            raise ValueError("min_repeats must be >= 2")
        if not (0.0 <= near_duplicate_threshold <= 1.0):
            raise ValueError("near_duplicate_threshold must be in [0, 1]")

        self.min_repeats = min_repeats
        self.max_cycle_length = max_cycle_length
        self.near_duplicate_threshold = near_duplicate_threshold
        self.ignored_argument_keys = ignored_argument_keys
        self.also_compare_results = also_compare_results

    def evaluate(self, tool_calls: Sequence[ToolCall]) -> LoopEvalResult:
        if not tool_calls:
            return LoopEvalResult(
                score=1.0,
                passed=True,
                reason="No tool calls in trace; nothing to evaluate.",
                loop_detected=False,
            )

        exact_signatures = [
            _signature(c, self.ignored_argument_keys) for c in tool_calls
        ]

        # 1. Exact / cycle-based repetition (handles both "same call N times"
        #    and "oscillation between 2-4 calls").
        cycle = _find_repeating_cycle(
            exact_signatures,
            max_cycle_length=self.max_cycle_length,
            min_repeats=self.min_repeats,
        )

        if cycle is not None:
            start = cycle["start_index"]
            involved = sorted({
                tool_calls[i].name
                for i in range(start, len(tool_calls))
            })
            loop_type = "exact_repeat" if cycle["cycle_length"] == 1 else "oscillation"

            severity = self._severity(cycle["repeat_count"], cycle["cycle_length"])
            score = round(max(0.0, 1.0 - severity), 4)

            reason = self._describe(
                loop_type=loop_type,
                cycle_length=cycle["cycle_length"],
                repeat_count=cycle["repeat_count"],
                involved=involved,
                start=start,
            )

            return LoopEvalResult(
                score=score,
                passed=score >= 0.5,
                reason=reason,
                loop_detected=True,
                loop_type=loop_type,
                cycle_length=cycle["cycle_length"],
                repeat_count=cycle["repeat_count"],
                first_loop_start_index=start,
                involved_tools=involved,
            )

        # 2. Near-duplicate detection: catches loops where arguments drift
        #    slightly each time (e.g. a retried search query with a trivial
        #    rewording) but the agent is still not making real progress,
        #    which the exact-match cycle search above would miss.
        near_dup = self._find_near_duplicate_run(tool_calls, exact_signatures)
        if near_dup is not None:
            start, repeat_count, involved = near_dup
            severity = self._severity(repeat_count, cycle_length=1)
            score = round(max(0.0, 1.0 - severity * 0.85), 4)  # slightly less severe than exact
            reason = self._describe(
                loop_type="near_duplicate",
                cycle_length=1,
                repeat_count=repeat_count,
                involved=involved,
                start=start,
            )
            return LoopEvalResult(
                score=score,
                passed=score >= 0.5,
                reason=reason,
                loop_detected=True,
                loop_type="near_duplicate",
                cycle_length=1,
                repeat_count=repeat_count,
                first_loop_start_index=start,
                involved_tools=involved,
            )

        return LoopEvalResult(
            score=1.0,
            passed=True,
            reason="No repetitive or non-progressing tool-call pattern detected.",
            loop_detected=False,
        )

    # -- helpers ------------------------------------------------------

    def _find_near_duplicate_run(
        self,
        tool_calls: Sequence[ToolCall],
        exact_signatures: Sequence[str],
    ) -> Optional[tuple]:
        """Scan for a contiguous run of same-tool calls whose arguments (and,
        if available, results) are near-identical by fuzzy similarity, even
        though they weren't byte-identical."""
        n = len(tool_calls)
        i = 0
        while i < n:
            j = i + 1
            run = [i]
            while j < n and tool_calls[j].name == tool_calls[i].name:
                sim = _similarity(exact_signatures[i], exact_signatures[j])
                if sim >= self.near_duplicate_threshold:
                    if self.also_compare_results:
                        r_i = str(tool_calls[i].result) if tool_calls[i].result is not None else ""
                        r_j = str(tool_calls[j].result) if tool_calls[j].result is not None else ""
                        if r_i or r_j:
                            result_sim = _similarity(r_i, r_j)
                            # If results genuinely diverge, treat this as
                            # legitimate iterative work (e.g. pagination),
                            # not a stuck loop.
                            if result_sim < self.near_duplicate_threshold:
                                break
                    run.append(j)
                    j += 1
                else:
                    break

            if len(run) >= self.min_repeats:
                involved = [tool_calls[i].name]
                return run[0], len(run), involved

            i = j if j > i + 1 else i + 1

        return None

    @staticmethod
    def _severity(repeat_count: int, cycle_length: int) -> float:
        """Map (repeat_count, cycle_length) to a severity in [0, 1].

        More repeats -> more severe. Longer cycles are slightly less
        alarming per-repeat than tight single-call loops, since they cover
        more distinct behavior, but still ramp to full severity quickly.
        """
        # Baseline severity kicks in right at the configured min_repeats and
        # saturates by ~2x that many repeats.
        raw = (repeat_count - 1) / max(1, repeat_count)
        cycle_discount = 1.0 if cycle_length == 1 else 0.85
        return min(1.0, raw * cycle_discount * 1.15)

    @staticmethod
    def _describe(
        loop_type: str,
        cycle_length: int,
        repeat_count: int,
        involved: List[str],
        start: int,
    ) -> str:
        tools_str = ", ".join(involved) if involved else "unknown tool(s)"
        if loop_type == "exact_repeat":
            return (
                f"Detected {repeat_count} consecutive identical calls to "
                f"'{tools_str}' starting at step {start}. The agent appears "
                f"stuck repeating the same tool call without making progress."
            )
        if loop_type == "oscillation":
            return (
                f"Detected a repeating {cycle_length}-step call pattern "
                f"({tools_str}) repeated {repeat_count} times starting at "
                f"step {start}. The agent appears to be cycling between the "
                f"same tool calls without reaching a new state."
            )
        return (
            f"Detected {repeat_count} near-duplicate calls to '{tools_str}' "
            f"starting at step {start} (similar arguments and results each "
            f"time). The agent is likely stuck retrying without real progress."
        )


# ---------------------------------------------------------------------------
# Streaming / real-time guardrail (for inline use in the gateway)
# ---------------------------------------------------------------------------

class ToolCallLoopGuardrail:
    """Incremental variant of ToolCallLoopDetector for real-time / inline use
    (e.g. the Agent Command Center gateway path), where you want to flag a
    loop as soon as it happens rather than waiting for the full trace.

    Usage:
        guardrail = ToolCallLoopGuardrail(min_repeats=3)
        for call in incoming_tool_calls:
            result = guardrail.observe(call)
            if result.loop_detected:
                # block / intervene / surface a warning to the agent
                ...
    """

    def __init__(self, window_size: int = 12, **detector_kwargs: Any) -> None:
        self._detector = ToolCallLoopDetector(**detector_kwargs)
        self._window_size = window_size
        self._buffer: List[ToolCall] = []

    def observe(self, call: ToolCall) -> LoopEvalResult:
        self._buffer.append(call)
        if len(self._buffer) > self._window_size:
            self._buffer = self._buffer[-self._window_size:]
        return self._detector.evaluate(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()


# ---------------------------------------------------------------------------
# Functional entry point, matching this project's `evaluate("<metric>", ...)`
# calling convention (see fi.evals.evaluate).
# ---------------------------------------------------------------------------

def evaluate_tool_call_loop_repetition(
    tool_calls: Sequence[Dict[str, Any]] | Sequence[ToolCall],
    **detector_kwargs: Any,
) -> Dict[str, Any]:
    """Functional wrapper so this metric can be called the same way other
    local metrics in this project are called, e.g.:

        from fi.evals import evaluate
        result = evaluate(
            "tool_call_loop_repetition",
            tool_calls=[
                {"name": "search", "arguments": {"q": "refund policy"}},
                {"name": "search", "arguments": {"q": "refund policy"}},
                {"name": "search", "arguments": {"q": "refund policy"}},
            ],
        )

    Accepts either raw dicts (with "name"/"arguments"/"result" keys, as they
    typically arrive off a trace/span) or ToolCall instances directly.
    """
    normalized: List[ToolCall] = [
        c if isinstance(c, ToolCall) else ToolCall(
            name=c.get("name") or c.get("tool_name") or "unknown_tool",
            arguments=c.get("arguments") or c.get("args") or {},
            result=c.get("result") or c.get("output"),
            index=c.get("index"),
        )
        for c in tool_calls
    ]
    detector = ToolCallLoopDetector(**detector_kwargs)
    return detector.evaluate(normalized).to_dict()


if __name__ == "__main__":
    # Minimal smoke test / usage demo.
    looping_trace = [
        ToolCall(name="search_docs", arguments={"query": "refund policy"}),
        ToolCall(name="search_docs", arguments={"query": "refund policy"}),
        ToolCall(name="search_docs", arguments={"query": "refund policy"}),
        ToolCall(name="search_docs", arguments={"query": "refund policy"}),
    ]
    result = ToolCallLoopDetector().evaluate(looping_trace)
    print(json.dumps(result.to_dict(), indent=2))
