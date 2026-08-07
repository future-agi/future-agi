"""Utilities for preserving template variables (e.g. {{var_name}}) during prompt optimization."""

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


def normalize_prompt_text(text: str) -> str:
    """Normalize Unicode in prompt text from rich text editors / web UI copy-paste.

    - NFKC normalization converts compatibility characters to canonical forms
      (e.g. non-breaking space \\xa0 → regular space, and other variant spaces).
    - Strips zero-width invisible characters that can silently break processing.
    """
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u200b\u200c\u200d\u200e\u200f\ufeff]", "", text)
    return text


def extract_template_variables(prompt: str) -> set[str]:
    """Extract all {{var_name}} template variables from a prompt.

    Returns set of variable names (without braces).
    """
    pattern = r"\{\{([a-zA-Z0-9_]+)\}\}"
    return set(re.findall(pattern, prompt))


def validate_template_variables(
    original_prompt: str,
    improved_prompt: str,
    required_variables: set[str] | None = None,
) -> tuple[bool, str]:
    """Check that improved_prompt contains all required template variables.

    Args:
        original_prompt: The original prompt (used to derive required vars if not given).
        improved_prompt: The optimized/improved prompt to validate.
        required_variables: Explicit set of required variable names. If None, derived from original_prompt.

    Returns:
        (True, "") if valid, (False, error_message) if variables are missing.
    """
    required = (
        required_variables
        if required_variables is not None
        else extract_template_variables(original_prompt)
    )
    if not required:
        return True, ""

    improved_vars = extract_template_variables(improved_prompt)
    missing_vars = required - improved_vars

    if missing_vars:
        missing_list = ", ".join(f"{{{{{var}}}}}" for var in sorted(missing_vars))
        return False, (
            f"The optimized prompt is missing required template variables: {missing_list}. "
            f"These variables are used to inject dynamic data at runtime and MUST be preserved exactly."
        )

    return True, ""


def build_template_variable_instruction(template_variables: set[str]) -> str:
    """Build instruction text telling the LLM optimizer to preserve template variables.

    Args:
        template_variables: Set of variable names (without braces) to preserve.

    Returns:
        Instruction string to append to task_description.
    """
    if not template_variables:
        return ""

    vars_list = ", ".join(f"{{{{{var}}}}}" for var in sorted(template_variables))
    return (
        f"\n\nCRITICAL CONSTRAINT - Template Variable Preservation:\n"
        f"The prompt contains template variables that are dynamically replaced with real data at runtime. "
        f"You MUST preserve ALL of the following template variables exactly as they appear (including the double curly braces): {vars_list}\n"
        f"Do NOT remove, rename, or alter any of these template variables. "
        f"Do NOT replace them with example values or static text. "
        f"They must appear verbatim in the optimized prompt."
    )


def _build_repair_prompt(
    candidate_prompt: str,
    original_prompt: str,
    missing_list: str,
    all_vars_list: str,
) -> str:
    """Build the LLM prompt for repairing template variables."""
    return f"""You are a prompt engineer. An optimizer improved a prompt but accidentally dropped some template variables.

Template variables use double curly braces like {{{{variable_name}}}} and are replaced with real data at runtime. They MUST be preserved exactly.

ORIGINAL PROMPT (has all variables):
---
{original_prompt}
---

IMPROVED PROMPT (missing some variables):
---
{candidate_prompt}
---

MISSING VARIABLES: {missing_list}
ALL REQUIRED VARIABLES: {all_vars_list}

Your task: produce a MERGED prompt that keeps ALL the improvements from the improved prompt but restores the missing template variables in their correct locations (referencing the original prompt for context on where each variable belongs).

Rules:
- Keep the improved prompt's wording, structure, and enhancements
- Restore every missing {{{{variable_name}}}} in the right context
- Every single variable from ALL REQUIRED VARIABLES must appear as {{{{variable_name}}}} in the output
- Do NOT add explanations, just output the merged prompt
- Do NOT wrap in code blocks or quotes"""


def repair_template_variables(
    candidate_prompt: str,
    original_prompt: str,
    required_variables: set[str],
) -> str:
    """Repair a candidate prompt that is missing template variables using LLM calls.

    Uses Claude Sonnet 4.5 (Bedrock) as the primary repair model, falling back
    to gpt-4o if the first attempt still has missing vars.

    Returns the repaired prompt, or the unrepaired candidate_prompt if all
    repair attempts fail.  Never returns original_prompt — the caller must
    always store/score the actual variation, not a silent fallback.
    """
    import litellm

    from agentic_eval.core.utils.model_config import ModelConfigs

    candidate_vars = extract_template_variables(candidate_prompt)
    missing_vars = required_variables - candidate_vars
    if not missing_vars:
        return candidate_prompt

    missing_list = ", ".join(f"{{{{{v}}}}}" for v in sorted(missing_vars))
    all_vars_list = ", ".join(f"{{{{{v}}}}}" for v in sorted(required_variables))

    repair_prompt = _build_repair_prompt(
        candidate_prompt, original_prompt, missing_list, all_vars_list
    )

    prompt_tokens = max(len(original_prompt), len(candidate_prompt)) // 3
    sonnet_cfg = ModelConfigs.SONNET_4_5_BEDROCK_ARN
    max_tokens = max(sonnet_cfg.max_tokens, prompt_tokens * 2)

    models_to_try = [sonnet_cfg.model_name, "gpt-4o"]

    for attempt_model in models_to_try:
        try:
            response = litellm.completion(
                model=attempt_model,
                messages=[{"role": "user", "content": repair_prompt}],
                max_tokens=max_tokens,
                drop_params=True,
            )
            repaired = response.choices[0].message.content.strip()

            # Validate the repair actually worked
            repaired_vars = extract_template_variables(repaired)
            still_missing = required_variables - repaired_vars
            if still_missing:
                logger.warning(
                    f"LLM repair with {attempt_model} still missing vars: {sorted(still_missing)}"
                )
                continue  # Try next model

            logger.info(
                f"Successfully repaired candidate prompt with {attempt_model}: "
                f"restored {len(missing_vars)} template vars"
            )
            return repaired

        except Exception as e:
            logger.error(f"LLM repair with {attempt_model} failed: {e}")
            continue

    logger.warning(
        "All repair attempts failed — returning unrepaired candidate prompt "
        "(missing vars will be evaluated as-is)"
    )
    return candidate_prompt
