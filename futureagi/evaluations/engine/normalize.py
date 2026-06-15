"""Canonical eval-output normalization helpers shared by all writer surfaces."""

from __future__ import annotations

import json


def _dedupe_preserve_order(items):
    """Return ``items`` with duplicates removed in first-seen order."""
    seen = set()
    out = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def dual_write_eval_value(value, config_output, logger_kwargs):
    """Populate ``logger_kwargs`` with the legacy column projections of one
    eval result, dual-writing both the rich (``output_str``) and legacy
    (``output_float`` / ``output_str_list``) shapes so FE readers that still
    consume the typed columns keep working.

    Gating:
      * ``output_float`` is (re-)populated only when ``config_output == "score"``.
      * ``output_str_list`` is (re-)populated only when ``config_output == "choices"``.
      * ``output_bool`` is set for ``bool`` / ``"Passed"``/``"Failed"`` values
        regardless of ``config_output``.
      * Any other ``config_output`` (``Pass/Fail``, ``reason``, ``numeric``, …)
        keeps the legacy isinstance-chain behaviour.

    The dict shape ``{"score": …, "choice": …}`` / ``{"score": …, "choices": […]}``
    comes from ``format_eval_value``'s choices branch; it is serialized as JSON
    into ``output_str`` so it stays inspectable.
    """
    if isinstance(value, bool):
        logger_kwargs["output_bool"] = value
        return
    if value in ("Passed", "Failed"):
        logger_kwargs["output_bool"] = value == "Passed"
        return

    if config_output == "score":
        if isinstance(value, dict):
            logger_kwargs["output_str"] = json.dumps(value)
            score = value.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                logger_kwargs["output_float"] = float(score)
        elif isinstance(value, (int, float)):
            logger_kwargs["output_float"] = float(value)
        elif isinstance(value, list):
            # Score evals never store a list — collapse to the mean so the FE
            # always reads a single scalar from output_float. Elements may be
            # raw numbers or per-item dicts shaped like ``{"score": …, "choice": …}``
            # from the choices-promoted code path; extract the score from each.
            # Keep the original list in output_str so per-element values stay
            # inspectable.
            logger_kwargs["output_str"] = json.dumps(value)
            numerics = []
            for v in value:
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    numerics.append(v)
                elif isinstance(v, dict):
                    s = v.get("score")
                    if isinstance(s, (int, float)) and not isinstance(s, bool):
                        numerics.append(s)
            if numerics:
                logger_kwargs["output_float"] = sum(numerics) / len(numerics)
        else:
            logger_kwargs["output_str"] = str(value)
        return

    if config_output == "choices":
        if isinstance(value, dict):
            logger_kwargs["output_str"] = json.dumps(value)
            choice = value.get("choice")
            choices = value.get("choices")
            if isinstance(choice, str):
                logger_kwargs["output_str_list"] = [choice]
            elif isinstance(choices, list):
                logger_kwargs["output_str_list"] = _dedupe_preserve_order(choices)
        elif isinstance(value, str):
            logger_kwargs["output_str"] = value
            logger_kwargs["output_str_list"] = [value]
        elif isinstance(value, list):
            # Two shapes can arrive here:
            #   * Plain list of choice strings.
            #   * List of per-item dicts shaped like ``{"choice": …}`` /
            #     ``{"choices": [...]}`` (mirrors the dict branch above).
            # Flatten + dedupe to a single ordered list either way. If any
            # element is a dict, also dump the raw list to ``output_str`` so the
            # per-item payloads stay inspectable.
            if any(isinstance(v, dict) for v in value):
                logger_kwargs["output_str"] = json.dumps(value)
            collected = []
            for v in value:
                if isinstance(v, str):
                    collected.append(v)
                elif isinstance(v, dict):
                    inner_choice = v.get("choice")
                    inner_choices = v.get("choices")
                    if isinstance(inner_choice, str):
                        collected.append(inner_choice)
                    elif isinstance(inner_choices, list):
                        collected.extend(c for c in inner_choices if isinstance(c, str))
            logger_kwargs["output_str_list"] = _dedupe_preserve_order(collected)
        elif isinstance(value, (int, float)):
            logger_kwargs["output_float"] = float(value)
        else:
            logger_kwargs["output_str"] = str(value)
        return

    # Other output types (Pass/Fail beyond the "Passed"/"Failed" branch above,
    # ``reason``, ``numeric``, …) — preserve the legacy dispatch.
    if isinstance(value, (int, float)):
        logger_kwargs["output_float"] = float(value)
    elif isinstance(value, list):
        logger_kwargs["output_str_list"] = value
    else:
        logger_kwargs["output_str"] = str(value)


def eval_config_output(obj):
    """Read the stored ``config["output"]`` from a CustomEvalConfig or
    EvalTemplate; falls back to ``"score"``. Always uses the stored type,
    never ``format_eval_value``'s runtime-promoted one."""
    try:
        template = getattr(obj, "eval_template", None) or obj
        return template.config.get("output", "score")
    except (AttributeError, TypeError):
        return "score"


def coerce_to_legacy_scalar(value, config_output):
    """Return a string-safe projection for TextField columns. Lists are
    JSON-serialized so the FE's parser unwraps them back into multiple
    chips; single scalars stay as their plain string form.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "Passed" if value else "Failed"
    if value in ("Passed", "Failed"):
        return value
    if isinstance(value, dict):
        score = value.get("score")
        choice = value.get("choice")
        choices = value.get("choices")
        if config_output == "score":
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                return str(float(score))
            if isinstance(choice, str):
                return choice
            if isinstance(choices, list):
                return json.dumps(choices)
            return json.dumps(value)
        # choices branch (or anything else with a dict shape)
        if isinstance(choice, str):
            return choice
        if isinstance(choices, list):
            return json.dumps(choices)
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            return str(float(score))
        return json.dumps(value)
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def build_simulate_eval_payload(
    *,
    name,
    output,
    reason,
    output_type,
    config_output,
    extra=None,
):
    """Build the canonical per-template entry for ``CallExecution.eval_outputs``.

    Preserves ``output`` verbatim; adds ``output_scalar`` (natural shape — list
    stays list, number stays number) and ``output_dict`` (rich shape or None)."""
    payload = {
        "name": name,
        "output": output,
        "output_scalar": coerce_to_legacy_scalar(output, config_output),
        "output_dict": output if isinstance(output, dict) else None,
        "reason": reason,
        "output_type": output_type,
    }
    if extra:
        for k, v in extra.items():
            if k not in payload:
                payload[k] = v
    return payload
