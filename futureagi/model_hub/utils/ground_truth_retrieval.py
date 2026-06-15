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


def format_few_shot_examples(
    examples: list[dict],
    *,
    variable_mapping: dict | None,
    output_column: str | None = None,
    explanation_column: str | None = None,
    injection_format: str = "structured",
) -> str:
    """Render GT examples as a prompt block for the LLM judge."""
    if not examples:
        return ""
    if injection_format == "xml":
        return _format_xml(
            examples, variable_mapping, output_column, explanation_column
        )
    if injection_format == "conversational":
        return _format_conversational(
            examples, variable_mapping, output_column, explanation_column
        )
    return _format_structured(
        examples, variable_mapping, output_column, explanation_column
    )


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


def _format_structured(
    examples: list[dict],
    variable_mapping: dict | None,
    output_column: str | None,
    explanation_column: str | None,
) -> str:
    lines = ["--- Reference Examples (scored by human experts) ---", ""]
    for i, example in enumerate(examples, 1):
        lines.append(f"Example {i}:")
        for tmpl_var, _target, val in _iter_inputs(example, variable_mapping):
            label = tmpl_var.replace("_", " ").title()
            lines.append(f"  {label}: {val}")
        if output_column and output_column in example:
            lines.append(f"  Eval Output: {example[output_column]}")
        if explanation_column and explanation_column in example:
            lines.append(f"  Eval Output Explanation: {example[explanation_column]}")
        lines.append("")
    lines.append("--- End Reference Examples ---")
    return "\n".join(lines)


def _format_conversational(
    examples: list[dict],
    variable_mapping: dict | None,
    output_column: str | None,
    explanation_column: str | None,
) -> str:
    lines = []
    for i, example in enumerate(examples, 1):
        case_parts: list[str] = []
        for tmpl_var, _target, val in _iter_inputs(example, variable_mapping):
            case_parts.append(f"{tmpl_var.replace('_', ' ').title()}: {val}")
        if case_parts:
            lines.append(f"Example {i}: " + " | ".join(case_parts))
        judgement: list[str] = []
        if output_column and output_column in example:
            judgement.append(f"Eval Output: {example[output_column]}")
        if explanation_column and explanation_column in example:
            judgement.append(f"Explanation: {example[explanation_column]}")
        if judgement:
            lines.append("Expert judgment: " + " | ".join(judgement))
        lines.append("")
    return "\n".join(lines)


def _format_xml(
    examples: list[dict],
    variable_mapping: dict | None,
    output_column: str | None,
    explanation_column: str | None,
) -> str:
    lines = ["<reference_examples>"]
    for example in examples:
        attr = ""
        if output_column and output_column in example:
            attr = f' eval_output="{example[output_column]}"'
        lines.append(f"  <example{attr}>")
        for tmpl_var, _target, val in _iter_inputs(example, variable_mapping):
            lines.append(f"    <{tmpl_var}>{val}</{tmpl_var}>")
        if explanation_column and explanation_column in example:
            lines.append(
                f"    <eval_output_explanation>"
                f"{example[explanation_column]}"
                f"</eval_output_explanation>"
            )
        lines.append("  </example>")
    lines.append("</reference_examples>")
    return "\n".join(lines)
