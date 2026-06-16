"""Pure helpers for GT runtime gating and prompt formatting."""

from __future__ import annotations

from typing import Any, Iterator

from model_hub.utils.eval_input_validation import is_empty_value


def has_usable_inputs_for_gt(
    variable_mapping: dict[str, Any] | None,
    runtime_inputs: dict[str, Any] | None,
) -> bool:
    """Decide whether GT retrieval is worth running.

    Returns ``True`` only if at least one template variable is mapped to
    a GT column AND its runtime value is non-empty. The runtime dict can
    be keyed by the template variable name (canonical) or the GT column
    name (legacy callers). Both are accepted so the gate doesn't mis-skip.
    """
    if not variable_mapping:
        return False
    if not runtime_inputs or not isinstance(runtime_inputs, dict):
        return False
    for tmpl_var, col in variable_mapping.items():
        if not is_empty_value(runtime_inputs.get(tmpl_var)):
            return True
        targets = col if isinstance(col, list) else [col]
        for target in targets:
            if target and not is_empty_value(runtime_inputs.get(target)):
                return True
    return False


def get_label_columns(role_mapping: dict | None) -> tuple[str, str]:
    """Return ``(output_column, explanation_column)`` from ``role_mapping``.

    Canonical keys are ``output`` and ``explanation``; legacy stored
    data may use ``expected_output`` / ``reasoning`` / ``reason`` which
    we accept for back-compat. Either may be ``""`` when not configured.
    """
    if not isinstance(role_mapping, dict):
        return "", ""

    def _first_str(*candidates: Any) -> str:
        for candidate in candidates:
            if isinstance(candidate, list) and candidate:
                candidate = candidate[0]
            if isinstance(candidate, str) and candidate:
                return candidate
        return ""

    output = _first_str(role_mapping.get("output"), role_mapping.get("expected_output"))
    explanation = _first_str(
        role_mapping.get("explanation"),
        role_mapping.get("reasoning"),
        role_mapping.get("reason"),
    )
    return output, explanation


def detect_input_column_types(
    examples: list[dict],
    variable_mapping: dict | None,
) -> dict[str, str]:
    """Return ``{gt_column: modality}`` for the mapped input columns.

    Modality detection is expensive (sniff fallback can hit the network),
    so we run it once over the first non-empty example and reuse the
    result. All rows in a GT dataset share the same column types, so a
    single pass is correct.
    """
    if not examples:
        return {}
    from agentic_eval.core.utils.llm_payloads import detect_and_build_media_blocks

    sample = examples[0]
    sample_keys = sorted({
        col for _var, col, _val in _iter_inputs(sample, variable_mapping)
    })
    sample_inputs = {k: sample.get(k) for k in sample_keys if sample.get(k)}
    if not sample_inputs:
        return {}
    try:
        _, key_types = detect_and_build_media_blocks(
            inputs=sample_inputs,
            required_keys=list(sample_inputs.keys()),
        )
    except Exception:
        return {}
    return {col: modality for col, modality in key_types.items() if modality}


def build_ground_truth_blocks(
    examples: list[dict],
    *,
    variable_mapping: dict | None,
    role_mapping: dict | None,
) -> list[dict]:
    """Render retrieved GT rows as OpenAI content blocks wrapped in delimiter tags."""
    if not examples:
        return []

    from agentic_eval.core.utils.llm_payloads import build_media_content_block

    output_col, explanation_col = get_label_columns(role_mapping)
    key_types = detect_input_column_types(examples, variable_mapping)

    blocks: list[dict] = [
        {
            "type": "text",
            "text": (
                "Reference examples from past evaluations of similar inputs "
                "follow. Each <example_N> contains one past input row (its "
                "fields wrapped in <field_name>...</field_name> tags matching "
                "the input you are judging), the verdict an expert assigned "
                "to that row in <eval_output>, and their reasoning in "
                "<eval_explanation> when available. Use them to calibrate "
                "your own judgment for the current input. Do not copy them "
                "into your output."
            ),
        },
        {"type": "text", "text": "<ground_truth_reference_examples>"},
    ]
    for i, example in enumerate(examples, 1):
        blocks.append({"type": "text", "text": f"<example_{i}>"})
        for tmpl_var, col, val in _iter_inputs(example, variable_mapping):
            modality = key_types.get(col)
            if modality in {"image", "audio"} and val:
                try:
                    blocks.extend(
                        build_media_content_block(val, modality, tmpl_var)
                    )
                except Exception:
                    # Defensive: a single bad URL shouldn't kill the whole block.
                    blocks.append({
                        "type": "text",
                        "text": f"<{tmpl_var}>{val}</{tmpl_var}>",
                    })
            else:
                blocks.append({
                    "type": "text",
                    "text": f"<{tmpl_var}>{val}</{tmpl_var}>",
                })
        if output_col and output_col in example:
            blocks.append({
                "type": "text",
                "text": f"<eval_output>{example[output_col]}</eval_output>",
            })
        if explanation_col and explanation_col in example:
            blocks.append({
                "type": "text",
                "text": (
                    f"<eval_explanation>"
                    f"{example[explanation_col]}"
                    f"</eval_explanation>"
                ),
            })
        blocks.append({"type": "text", "text": f"</example_{i}>"})
    blocks.append({"type": "text", "text": "</ground_truth_reference_examples>"})
    return blocks


def _iter_inputs(
    example: dict, variable_mapping: dict | None
) -> Iterator[tuple[str, str, Any]]:
    """Yield ``(template_variable, gt_column, value)`` for the input side."""
    if not variable_mapping:
        for key, val in (example or {}).items():
            yield key, key, val
        return
    for tmpl_var, col in variable_mapping.items():
        targets = col if isinstance(col, list) else [col]
        for target in targets:
            if target in example:
                yield tmpl_var, target, example[target]
