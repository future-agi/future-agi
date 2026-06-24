"""Per-axis projection of simulate eval outputs."""

from __future__ import annotations

from typing import Any

AXIS_KEYS: tuple[str, ...] = (
    "output_pass",
    "output_score",
    "output_choice",
    "output_choices",
)


def _empty_axes() -> dict[str, None]:
    return dict.fromkeys(AXIS_KEYS, None)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def extract_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, dict):
        score = value.get("score")
        if isinstance(score, int | float) and not isinstance(score, bool):
            return float(score)
    return None


def extract_choice(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        choice = value.get("choice")
        if isinstance(choice, str):
            return choice
    return None


def extract_choices(value: Any) -> list[str] | None:
    if isinstance(value, list):
        strings = [v for v in value if isinstance(v, str)]
        return _dedupe(strings) if strings else None
    if isinstance(value, dict):
        choices = value.get("choices")
        if isinstance(choices, list):
            strings = [v for v in choices if isinstance(v, str)]
            return _dedupe(strings) if strings else None
    return None


def extract_pass(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value == "Passed":
        return True
    if value == "Failed":
        return False
    return None


def resolve_eval_axes(
    value: Any, config_output: str, multi_choice: bool = False
) -> dict[str, Any]:
    axes: dict[str, Any] = _empty_axes()
    if value is None:
        return axes
    if config_output == "Pass/Fail":
        axes["output_pass"] = extract_pass(value)
    elif config_output in ("score", "numeric"):
        axes["output_score"] = extract_score(value)
    elif config_output == "choices":
        if multi_choice:
            axes["output_choices"] = extract_choices(value)
        else:
            axes["output_choice"] = extract_choice(value)
    return axes
