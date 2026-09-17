"""Prompts and prompt helpers for the agent eval context-management layer.

Intentionally agent-type-neutral so the same compaction step works for
span/trace/session evals, simulation evals, voice evals, and custom-
prompt evals alike. Phrased in terms of "earlier turns" + "facts
established" rather than any eval-specific jargon (spans, drills,
verdicts).
"""

COMPACTION_PROMPT = (
    "You are summarizing earlier turns of an agent's working conversation "
    "into a factual digest. The agent will continue its work using this "
    "digest in place of the original turns, so completeness matters.\n\n"
    "Preserve every concrete fact that was established:\n"
    "  - All identifiers, names, references, and entities looked up\n"
    "  - Actual values returned by tool calls — numbers, statuses, IDs, "
    "text, errors\n"
    "  - Intermediate conclusions, checks, or partial judgments reached\n"
    "  - Constraints, rules, or criteria applied\n"
    "  - Anomalies, errors, or edge cases observed\n"
    "  - Goals, preferences, or instructions noted from the user\n\n"
    "Drop only: filler reasoning, restatements, intermediate thoughts that "
    "did not lead to a fact or decision, redundant content.\n\n"
    "Write a terse, structured digest — short headers + bullets are fine. "
    "Do NOT add a preamble like 'Here is the summary' — output the digest "
    "only."
)


def output_format_instruction(output_type, choices, multi_choice: bool = False) -> str:
    """Single-line instruction describing the required eval output shape."""
    if output_type == "Pass/Fail":
        return "Your final answer must be exactly one of: 'Pass' or 'Fail'."
    if output_type in ("score", "numeric"):
        return (
            "Your final answer must be a numeric score between 0.0 and "
            "1.0, where 0.0 means completely fails the criteria and "
            "1.0 means perfectly meets it."
        )
    if output_type == "choices" and choices:
        choices_str = ", ".join(f"'{c}'" for c in choices)
        if multi_choice:
            return (
                "Your final answer must be a JSON array of one or more of: "
                f"{choices_str}. Select every label that applies; do not "
                "repeat a label; do not invent labels."
            )
        return f"Your final answer must be exactly one of: {choices_str}."
    return "Your final answer must be in plain text."
