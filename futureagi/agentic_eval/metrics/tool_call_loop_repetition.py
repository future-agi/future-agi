"""Local diagnostic scoring for repeated tool calls, not live execution control.

The detector and incremental observer never execute, cancel, or stop tools.
Missing results allow only a repetition diagnosis, not proof of absent progress.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass
class ToolCall:
    """One completed tool call; index optionally identifies its full-trace position."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    index: int | None = None


@dataclass
class LoopEvalResult:
    """Diagnostic score (higher is healthier), reason and detected pattern."""

    score: float
    passed: bool
    reason: str
    loop_detected: bool
    loop_type: str | None = None
    cycle_length: int | None = None
    repeat_count: int | None = None
    first_loop_start_index: int | None = None
    involved_tools: list[str] = field(default_factory=list)
    window_size: int | None = None
    evaluated_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
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
            "window_size": self.window_size,
            "evaluated_calls": self.evaluated_calls,
        }


# The semantic record identifier `id` is deliberately not a volatile key.
_DEFAULT_IGNORED_ARG_KEYS = frozenset(
    {
        "timestamp",
        "ts",
        "request_id",
        "trace_id",
        "nonce",
        "idempotency_key",
        "call_id",
        "created_at",
    }
)


def _serialize(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _normalize_arguments(arguments: dict[str, Any], ignored_keys: frozenset) -> str:
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return _serialize({k: v for k, v in arguments.items() if k not in ignored_keys})


def _signature(call: ToolCall, ignored_keys: frozenset) -> str:
    return _serialize([call.name, _normalize_arguments(call.arguments, ignored_keys)])


def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def _find_repeating_cycle(
    signatures: Sequence[str], max_cycle_length: int = 4, min_repeats: int = 3
) -> dict[str, int] | None:
    """Search every position, preferring longest coverage then the shortest cycle.

    For each possible period, count consecutive matches to the element one
    period earlier. This scans the full trace in O(n * max_cycle_length),
    including a completed loop followed by unrelated healthy calls.
    """
    best = None
    for length in range(1, min(max_cycle_length, len(signatures) // min_repeats) + 1):
        matched = 0
        for i in range(length, len(signatures)):
            matched = matched + 1 if signatures[i] == signatures[i - length] else 0
            repeats = (matched + length) // length
            if repeats < min_repeats:
                continue
            candidate = {
                "cycle_length": length,
                "repeat_count": repeats,
                "start_index": i - matched - length + 1,
            }
            if (
                best is None
                or repeats * length > best["repeat_count"] * best["cycle_length"]
            ):
                best = candidate
    return best


class ToolCallLoopDetector:
    """Score exact cycles and near-duplicate calls without changing execution.

    By default any changed result is progress, even if the rest of a large
    result is unchanged. Set also_compare_results=False only for call-shape
    diagnostics. Custom ignored_argument_keys replace the default volatile
    keys; semantic IDs and non-string argument changes are never fuzzy-matched.
    """

    def __init__(
        self,
        min_repeats: int = 3,
        max_cycle_length: int = 4,
        near_duplicate_threshold: float = 0.92,
        ignored_argument_keys: frozenset = _DEFAULT_IGNORED_ARG_KEYS,
        also_compare_results: bool = True,
    ) -> None:
        if not isinstance(min_repeats, int) or min_repeats < 2:
            raise ValueError("min_repeats must be an integer >= 2")
        if not isinstance(max_cycle_length, int) or max_cycle_length < 1:
            raise ValueError("max_cycle_length must be an integer >= 1")
        if not 0.0 <= near_duplicate_threshold <= 1.0:
            raise ValueError("near_duplicate_threshold must be in [0, 1]")
        self.min_repeats = min_repeats
        self.max_cycle_length = max_cycle_length
        self.near_duplicate_threshold = near_duplicate_threshold
        self.ignored_argument_keys = ignored_argument_keys
        self.also_compare_results = also_compare_results

    def evaluate(self, tool_calls: Sequence[ToolCall]) -> LoopEvalResult:
        signatures = [_signature(c, self.ignored_argument_keys) for c in tool_calls]
        # Apply the same progress rule to exact repetitions and fuzzy runs.
        if self.also_compare_results:
            signatures = [
                _serialize([sig, c.result])
                for sig, c in zip(signatures, tool_calls, strict=True)
            ]
        cycle = _find_repeating_cycle(
            signatures, self.max_cycle_length, self.min_repeats
        )
        if cycle is not None:
            start = cycle["start_index"]
            length = cycle["cycle_length"]
            repeats = cycle["repeat_count"]
            involved = sorted(
                {c.name for c in tool_calls[start : start + length * repeats]}
            )
            return self._result(
                tool_calls,
                start,
                repeats,
                length,
                "exact_repeat" if length == 1 else "oscillation",
                involved,
            )
        near_dup = self._find_near_duplicate_run(tool_calls)
        if near_dup is not None:
            start, repeats = near_dup
            return self._result(
                tool_calls,
                start,
                repeats,
                1,
                "near_duplicate",
                [tool_calls[start].name],
            )
        return LoopEvalResult(
            score=1.0,
            passed=True,
            reason="No repetitive or non-progressing tool-call pattern detected.",
            loop_detected=False,
        )

    def _result(self, calls, start, repeats, length, loop_type, involved):
        severity = self._severity(repeats, length)
        if loop_type == "near_duplicate":
            severity *= 0.85
        # Once the configured minimum is met, even a short/fuzzy cycle must
        # fall below the template's 0.5 pass threshold.
        score = round(min(0.49, max(0.0, 1.0 - severity)), 4)
        position = calls[start].index if calls[start].index is not None else start
        return LoopEvalResult(
            score=score,
            passed=score >= 0.5,
            reason=(
                self._describe(loop_type, length, repeats, involved, position)
                + " Repeated arguments: "
                + "; ".join(
                    f"{call.name}: {_normalize_arguments(call.arguments, self.ignored_argument_keys)[:240]}"
                    for call in calls[start : start + length]
                )
            ),
            loop_detected=True,
            loop_type=loop_type,
            cycle_length=length,
            repeat_count=repeats,
            first_loop_start_index=position,
            involved_tools=involved,
        )

    def _near_arguments(self, first: ToolCall, other: ToolCall) -> bool:
        left = {
            k: v
            for k, v in first.arguments.items()
            if k not in self.ignored_argument_keys
        }
        right = {
            k: v
            for k, v in other.arguments.items()
            if k not in self.ignored_argument_keys
        }
        if left.keys() != right.keys():
            return False
        for key, value in left.items():
            if value == right[key]:
                continue
            # Similar-looking record IDs, pagination cursors and structured
            # values may refer to entirely different work, not cosmetic drift.
            if (
                key == "id"
                or key.endswith("_id")
                or key in {"page", "offset", "cursor"}
            ):
                return False
            if not isinstance(value, str) or not isinstance(right[key], str):
                return False
            if _similarity(value, right[key]) < self.near_duplicate_threshold:
                return False
        return True

    def _find_near_duplicate_run(
        self, tool_calls: Sequence[ToolCall]
    ) -> tuple[int, int] | None:
        for i, first in enumerate(tool_calls):
            j = i + 1
            while j < len(tool_calls) and tool_calls[j].name == first.name:
                other = tool_calls[j]
                if self.also_compare_results and _serialize(first.result) != _serialize(
                    other.result
                ):
                    break
                if not self._near_arguments(first, other):
                    break
                j += 1
            if j - i >= self.min_repeats:
                return i, j - i
        return None

    @staticmethod
    def _severity(repeat_count: int, cycle_length: int) -> float:
        raw = (repeat_count - 1) / max(1, repeat_count)
        cycle_discount = 1.0 if cycle_length == 1 else 0.85
        return min(1.0, raw * cycle_discount * 1.15)

    @staticmethod
    def _describe(loop_type, cycle_length, repeat_count, involved, start) -> str:
        tools = ", ".join(involved)
        return (
            f"Detected {loop_type}: {cycle_length}-step pattern ({tools}) repeated "
            f"{repeat_count} times starting at step {start}. "
            "Diagnostic only; no tool execution was stopped."
        )


class ToolCallLoopGuardrail:
    """Incremental diagnostic observer; the legacy class name does not imply stopping.

    Indices remain full-stream positions after the bounded window is trimmed.
    Explicit ToolCall.index values take precedence. reset starts a fresh stream.
    """

    def __init__(self, window_size: int = 12, **detector_kwargs: Any) -> None:
        self._detector = ToolCallLoopDetector(**detector_kwargs)
        if not isinstance(window_size, int) or window_size < self._detector.min_repeats:
            raise ValueError("window_size must be an integer >= min_repeats")
        self._window_size = window_size
        self._buffer: list[ToolCall] = []
        self._next_index = 0

    def observe(self, call: ToolCall) -> LoopEvalResult:
        if call.index is None:
            call = replace(call, index=self._next_index)
        self._next_index += 1
        self._buffer.append(call)
        self._buffer = self._buffer[-self._window_size :]
        return self._detector.evaluate(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()
        self._next_index = 0


def validate_loop_window(window_size: int, min_repeats: int = 3) -> int:
    """Bound both stored-span reads and deterministic evaluation work."""
    if (
        isinstance(window_size, bool)
        or not isinstance(window_size, int)
        or not min_repeats <= window_size <= 1000
    ):
        raise ValueError("window_size must be an integer >= min_repeats and <= 1000")
    return window_size


def evaluate_tool_call_loop_repetition(
    tool_calls: str | Sequence[dict[str, Any] | ToolCall],
    *,
    window_size: int = 100,
    **detector_kwargs: Any,
) -> dict[str, Any]:
    """Accept flat completed calls or Gateway/OpenAI function-call envelopes.

    Serialized arguments must contain a JSON object. Provide result/output on
    each completed call to distinguish polling progress. This accepts data from
    the Gateway; it does not install a Gateway plugin or enable live stopping.
    """
    if isinstance(tool_calls, str):
        tool_calls = json.loads(tool_calls)
    if not isinstance(tool_calls, (list, tuple)):
        raise ValueError("tool_calls must be a list of completed tool calls")
    validate_loop_window(window_size, detector_kwargs.get("min_repeats", 3))
    normalized = []
    offset = max(0, len(tool_calls) - window_size)
    for index, call in enumerate(tool_calls[offset:], start=offset):
        if isinstance(call, ToolCall):
            normalized.append(
                replace(call, index=index) if call.index is None else call
            )
            continue
        if not isinstance(call, dict):
            raise ValueError("Each tool call must be an object")
        function = call.get("function", call)
        if not isinstance(function, dict):
            raise ValueError("Tool function must be an object")
        name = function.get("name", function.get("tool_name"))
        if not isinstance(name, str) or not name:
            raise ValueError("Tool name must be a non-empty string")
        arguments = function.get("arguments", function.get("args", {}))
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
        normalized.append(
            ToolCall(
                name=name,
                arguments=arguments,
                result=call["result"] if "result" in call else call.get("output"),
                index=call.get("index", index),
            )
        )
    result = ToolCallLoopDetector(**detector_kwargs).evaluate(normalized)
    result.window_size = window_size
    result.evaluated_calls = len(normalized)
    result.reason += (
        f" Evaluated {len(normalized)} most recent tool calls "
        f"(window_size={window_size})."
    )
    return result.to_dict()
