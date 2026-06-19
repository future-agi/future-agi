"""Canonical eval-output normalization for the simulate surface.

Per eval row in ``CallExecution.eval_outputs[<eval_config_id>]``, exactly one
of four filter axes is populated based on the **stored**
``eval_template.config["output"]`` (and ``multi_choice`` flag for choices):

* ``output_pass``    -- bool, for ``Pass/Fail`` templates.
* ``output_score``   -- float, for ``score`` / ``numeric`` templates.
* ``output_choice``  -- str, for ``choices`` (single) templates.
* ``output_choices`` -- list[str], for ``choices`` + ``multi_choice`` templates.

All other axes are ``None`` on the row. ``reason`` templates leave all four
``None`` (free-form text, not filterable). Error / skipped / pending rows also
leave all four ``None``; their ``status`` / ``error`` / ``skipped`` flags
discriminate them.

The dispatch is keyed on the **stored** ``config["output"]`` because
``format_eval_value`` internally promotes ``score`` to the choices branch
when ``choice_scores`` exist. A score eval with ``choice_scores`` emits a
``{"score": ..., "choice": ...}`` dict, but the eval is still a score eval
and ``output_score`` is the right filter axis.
"""

from __future__ import annotations

from typing import Any


def dedupe_preserve_order(items: list[Any]) -> list[Any]:
    """Return ``items`` with duplicates removed, keeping first-seen order."""
    seen: set[Any] = set()
    out: list[Any] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def eval_config_output(custom_eval_config: Any) -> str:
    """Read the stored ``output`` type off an eval template config.

    Never use the runtime-promoted value (``format_eval_value`` internally
    promotes ``score`` to ``choices`` when ``choice_scores`` exist); the
    dispatch rules below are keyed on the **stored** type.
    """
    try:
        return custom_eval_config.eval_template.config.get("output", "score")
    except (AttributeError, TypeError):
        return "score"


def eval_config_multi_choice(custom_eval_config: Any) -> bool:
    """Read the ``multi_choice`` flag off the eval template, defaulting to
    ``False`` when missing. Splits the ``choices`` axis between single and
    multi at write time.
    """
    try:
        return bool(custom_eval_config.eval_template.multi_choice)
    except AttributeError:
        return False


def extract_score(value: Any) -> float | None:
    """Project ``value`` to a numeric score for the ``output_score`` axis.

    Plain numbers pass through. Dicts shaped like ``{"score": ...}`` (the
    ``choice_scores`` shape) yield ``dict["score"]``. Anything else yields
    ``None`` so the filter SQL gets a clean null.
    """
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
    """Project ``value`` to a single choice string for the ``output_choice``
    axis. Plain strings pass through. Dicts shaped like ``{"choice": ...}``
    (the single-choice ``choice_scores`` shape) yield ``dict["choice"]``.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        choice = value.get("choice")
        if isinstance(choice, str):
            return choice
    return None


def extract_choices(value: Any) -> list[str] | None:
    """Project ``value`` to a deduped list of choice strings for the
    ``output_choices`` axis. Plain lists of strings pass through after dedup.
    Dicts shaped like ``{"choices": [...]}`` yield ``dict["choices"]``.
    """
    if isinstance(value, list):
        strings = [v for v in value if isinstance(v, str)]
        if strings:
            return dedupe_preserve_order(strings)
        return None
    if isinstance(value, dict):
        choices = value.get("choices")
        if isinstance(choices, list):
            strings = [v for v in choices if isinstance(v, str)]
            if strings:
                return dedupe_preserve_order(strings)
    return None


def extract_pass(value: Any) -> bool | None:
    """Project ``value`` to a boolean for the ``output_pass`` axis.

    True booleans pass through. ``"Passed"`` / ``"Failed"`` strings (the
    canonical ``format_eval_value`` output for Pass/Fail templates) map to
    ``True`` / ``False``.
    """
    if isinstance(value, bool):
        return value
    if value == "Passed":
        return True
    if value == "Failed":
        return False
    return None


AXIS_KEYS: tuple[str, ...] = (
    "output_pass",
    "output_score",
    "output_choice",
    "output_choices",
)


def empty_axes() -> dict[str, None]:
    """Return all four axis keys set to ``None``. Used for placeholders
    (pending / skipped) and as the default in ``resolve_eval_axes``."""
    return dict.fromkeys(AXIS_KEYS, None)


def resolve_eval_axes(
    value: Any, config_output: str, multi_choice: bool = False
) -> dict[str, Any]:
    """Project ``value`` into the four filter-axis keys, gated by the stored
    ``config_output`` and ``multi_choice`` flag. Returns the four keys with
    exactly one populated (the rest ``None``) for non-null values; all
    ``None`` when ``value`` is ``None`` or the config isn't filter-bearing
    (e.g. ``reason``).

    Single source of truth for both the writer
    (``build_simulate_eval_payload``) and the backfill command.
    """
    axes: dict[str, Any] = empty_axes()
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


def build_simulate_eval_payload(
    *,
    value: Any,
    config_output: str,
    multi_choice: bool = False,
    reason: str = "",
    name: str = "",
    output_type: str | None = None,
    error: Any = None,
    status: str | None = None,
    skipped: bool = False,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Assemble the canonical eval-row dict written into
    ``CallExecution.eval_outputs[<eval_config_id>]``.

    Always emits all four axis keys so every row in the JSONB blob carries
    the same shape regardless of status: filter SQL never needs to inspect
    which keys are present.
    """
    payload: dict[str, Any] = {
        "output": value,
        **resolve_eval_axes(value, config_output, multi_choice),
        "reason": reason,
        "output_type": output_type,
        "name": name,
    }
    if error is not None:
        payload["error"] = error
    if status is not None:
        payload["status"] = status
    if skipped:
        payload["skipped"] = True
    if timestamp is not None:
        payload["timestamp"] = timestamp
    return payload
