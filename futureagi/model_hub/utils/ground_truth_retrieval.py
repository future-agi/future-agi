"""Runtime GT injection and few-shot prompt formatting."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _is_empty_value(value: Any) -> bool:
    """Should this runtime value be treated as "no signal" for GT retrieval?

    Falsy-but-legitimate scalars (``0``, ``False``) are NOT empty -
    they're valid eval inputs. Empty means actually absent: ``None``,
    blank/whitespace string, empty list/tuple/set, empty dict.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


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
        if not _is_empty_value(runtime_inputs.get(tmpl_var)):
            return True
        targets = col if isinstance(col, list) else [col]
        for target in targets:
            if target and not _is_empty_value(runtime_inputs.get(target)):
                return True
    return False


def load_ground_truth_config(eval_template) -> dict | None:
    """Return the GT config dict from the template, or ``None``.

    Treats ``enabled=False`` and missing ``ground_truth_id`` as "not
    configured". Callers should fall through silently in that case.
    """
    config = (eval_template.config or {}).get("ground_truth")
    if not config or not config.get("enabled"):
        return None
    if not config.get("ground_truth_id"):
        return None
    return config


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


def _parse_score(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def validate_output_value(
    value: Any,
    output_type_normalized: str | None,
    choice_scores: dict | None = None,
    pass_threshold: float | None = None,  # noqa: ARG001
) -> tuple[bool, str | None]:
    """Validate a candidate eval-output value against a template's output type.

    Used by the FE preview check at upload time so users see immediate
    feedback if their mapped output column carries values incompatible
    with the template type. Returns ``(ok, error_message)``.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return False, "Value is empty."

    out_type = (output_type_normalized or "").lower()
    if out_type == "pass_fail":
        allowed = {"pass", "fail", "true", "false", "0", "1", "yes", "no"}
        if str(value).strip().lower() not in allowed:
            return (
                False,
                "Expected one of: Pass / Fail / True / False / Yes / No.",
            )
        return True, None

    if out_type == "percentage":
        score = _parse_score(value)
        if score is None:
            return False, "Expected a numeric score between 0 and 1."
        if score < 0 or score > 1:
            return False, "Score must lie in the [0, 1] range."
        return True, None

    if out_type == "deterministic":
        if not isinstance(choice_scores, dict) or not choice_scores:
            return True, None
        canonical = {str(k).strip().lower(): k for k in choice_scores.keys()}
        if str(value).strip().lower() not in canonical:
            options = ", ".join(str(k) for k in choice_scores.keys())
            return False, f"Expected one of: {options}."
        return True, None

    return True, None


def get_ground_truth_few_shot_examples(
    gt_config: dict,
    current_input: dict,
) -> list[dict]:
    """Retrieve similar GT rows for few-shot injection.

    Resolves the ``EvalGroundTruth`` from the config and delegates to
    :meth:`GroundTruthService.retrieve_few_shot`. Returns ``[]`` when the
    config doesn't point at a real GT, or when the dataset isn't ready,
    or when the inputs don't have anything to query against.
    """
    from model_hub.models.evals_metric import EvalGroundTruth
    from model_hub.services.ground_truth_service import GroundTruthService

    gt_id = gt_config.get("ground_truth_id")
    max_examples = int(gt_config.get("max_examples", 3))

    try:
        gt = EvalGroundTruth.objects.get(id=gt_id, deleted=False)
    except EvalGroundTruth.DoesNotExist:
        logger.warning("ground_truth_not_found", gt_id=gt_id)
        return []

    if not has_usable_inputs_for_gt(gt.variable_mapping, current_input):
        return []

    return GroundTruthService.retrieve_few_shot(
        gt=gt, inputs=current_input, max_results=max_examples
    )


def inject_ground_truth_context(
    mapped: dict, eval_template, eval_type_id: str = ""
) -> dict:
    """Mutate ``mapped`` with GT context when the template has GT enabled.

    CustomPromptEvaluator path → inject ``ground_truth_few_shot`` (a
    formatted string of retrieved examples).
    Other (Agent) paths → inject ``ground_truth_config`` so the
    evaluator can expose the ``search_ground_truth`` tool.

    Skips entirely when there's no usable input to query against - see
    :func:`has_usable_inputs_for_gt` for the rule.
    """
    from model_hub.models.evals_metric import EvalGroundTruth

    gt_config = load_ground_truth_config(eval_template)
    if not gt_config:
        return mapped

    try:
        gt_obj = EvalGroundTruth.objects.filter(
            id=gt_config["ground_truth_id"], deleted=False
        ).first()
    except Exception:
        gt_obj = None

    if gt_obj is None:
        return mapped

    if not has_usable_inputs_for_gt(gt_obj.variable_mapping, mapped):
        logger.debug(
            "ground_truth_skipped_no_usable_inputs",
            gt_id=str(gt_obj.id),
            eval_type_id=eval_type_id,
            template_id=str(getattr(eval_template, "id", "") or ""),
            variable_mapping=gt_obj.variable_mapping,
            runtime_inputs=mapped,
        )
        return mapped

    gt_config = dict(gt_config)
    gt_config["embedding_status"] = gt_obj.embedding_status

    if (
        eval_type_id == "CustomPromptEvaluator"
        and gt_obj.embedding_status == "completed"
    ):
        examples = get_ground_truth_few_shot_examples(gt_config, mapped)
        output_col, explanation_col = get_label_columns(gt_obj.role_mapping)
        few_shot_text = ""
        if examples:
            few_shot_text = format_few_shot_examples(
                examples,
                variable_mapping=gt_obj.variable_mapping,
                output_column=output_col,
                explanation_column=explanation_col,
                injection_format=gt_config.get("injection_format", "structured"),
            )
            mapped["ground_truth_few_shot"] = few_shot_text
        logger.debug(
            "ground_truth_custom_prompt_injected",
            gt_id=str(gt_obj.id),
            template_id=str(getattr(eval_template, "id", "") or ""),
            variable_mapping=gt_obj.variable_mapping,
            role_mapping=gt_obj.role_mapping,
            output_column=output_col,
            explanation_column=explanation_col,
            runtime_inputs=mapped,
            examples_count=len(examples),
            examples_preview=examples[:3],
            injection_format=gt_config.get("injection_format", "structured"),
            few_shot_text=few_shot_text,
        )
        return mapped

    mapped["ground_truth_config"] = gt_config
    logger.debug(
        "ground_truth_agent_config_injected",
        gt_id=str(gt_obj.id),
        template_id=str(getattr(eval_template, "id", "") or ""),
        eval_type_id=eval_type_id,
        variable_mapping=gt_obj.variable_mapping,
        role_mapping=gt_obj.role_mapping,
        runtime_inputs=mapped,
        ground_truth_config=gt_config,
    )
    return mapped


def format_few_shot_examples(
    examples: list[dict],
    *,
    variable_mapping: dict | None,
    output_column: str | None = None,
    explanation_column: str | None = None,
    injection_format: str = "structured",
) -> str:
    """Render GT examples as a prompt block for the LLM judge.

    Each example row is projected through the rule-prompt's template
    variable names (``{{question}}: ...``) and the labelled output /
    explanation columns. Three layouts:

    * ``structured`` (default): newline-delimited ``Example N:`` blocks
    * ``conversational``: one-line ``Example N: ... | Expert judgment: ...``
    * ``xml``: ``<reference_examples><example>...</example></reference_examples>``
    """
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


def _iter_inputs(example: dict, variable_mapping: dict | None):
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


def _format_structured(examples, variable_mapping, output_column, explanation_column):
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
    examples, variable_mapping, output_column, explanation_column
):
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


def _format_xml(examples, variable_mapping, output_column, explanation_column):
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
