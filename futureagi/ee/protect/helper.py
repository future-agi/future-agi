"""
ProtectHelper — centralised Protect and Protect Flash evaluation logic.

Handles metric resolution, prompt construction, input validation,
and response parsing for all protect evaluation types.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import structlog

from ee.prompts.protect_prompts import (
    PROTECT_FLASH_PROMPT_TEMPLATE,
    build_mm_messages_for_protect,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maps eval names to metric keys.
_UI_TO_METRIC = {
    "toxicity": "toxicity",
    "bias": "bias",
    "sexist": "bias",
    "bias detection": "bias",
    "bias_detection": "bias",
    "prompt_injection": "prompt_injection",
    "prompt injection": "prompt_injection",
    "data_privacy_compliance": "privacy",
    "data privacy compliance": "privacy",
    "pii": "privacy",
}

# Maps metric keys to model aliases.
_METRIC_TO_ALIAS = {
    "toxicity": "protect_toxicity",
    "bias": "protect_bias",
    "privacy": "protect_privacy",
    "prompt_injection": "protect_prompt_injection",
}

_ALLOWED_INPUT_TYPES = {"text", "image", "audio"}

# Response parsing patterns.
_LABEL_RE = re.compile(
    r"<\s*label\b[^>]*>\s*['\"`]*\s*(passed|failed)\s*['\"`]*\s*</\s*label\s*>",
    re.I | re.S,
)
_EXPLANATION_RE = re.compile(
    r"<\s*explanation\b[^>]*>(.*?)</\s*explanation\s*>",
    re.I | re.S,
)
_FLASH_OUTPUT_RE = re.compile(r".*:(.*)\n.*:(.*)\n.*:(.*)")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ProtectHelper:
    """Stateless helper for protect evaluation logic."""

    # -- Validation ----------------------------------------------------------

    @staticmethod
    def validate_input_types(input_types: List[str]) -> None:
        """Raise ValueError if any input type is unsupported by protect."""
        normalized = {
            str(t).strip().lower() for t in input_types if t
        }
        if "images" in normalized:
            raise ValueError(
                "Protect does not support image sets (`images`). "
                "Please provide a single image input."
            )
        if "pdf" in normalized or "knowledge_base" in normalized:
            raise ValueError(
                "Protect does not support PDF or knowledge base inputs."
            )
        unsupported = normalized - _ALLOWED_INPUT_TYPES
        if unsupported:
            raise ValueError(
                "Protect does not support input type(s): "
                + ", ".join(sorted(unsupported))
            )

    # -- Metric / alias resolution -------------------------------------------

    @staticmethod
    def resolve_metric(eval_name: str) -> str:
        """Map an eval name to a metric key.

        Raises ValueError if the eval name is not a valid protect metric.
        """
        key = (eval_name or "").strip().lower()
        metric = _UI_TO_METRIC.get(key)
        if metric is None:
            raise ValueError(
                "Protect model supports only toxicity, bias_detection, "
                "pii, and prompt_injection evals."
            )
        return metric

    @staticmethod
    def resolve_alias(eval_name: str, *, is_flash: bool) -> str:
        """Return the model alias for the given eval."""
        if is_flash:
            return "protect_flash"
        metric = ProtectHelper.resolve_metric(eval_name)
        alias = _METRIC_TO_ALIAS.get(metric)
        if alias is None:
            raise ValueError(f"No alias for metric '{metric}'")
        return alias

    # -- Prompt / message construction ---------------------------------------

    @staticmethod
    def build_messages(
        eval_name: str,
        inputs: List[Any],
        input_types: List[str],
        *,
        is_flash: bool,
        max_tokens: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Build the chat messages payload for a protect evaluation."""
        inp = inputs[0] if inputs else ""
        itype = (
            (input_types[0] or "text").strip().lower()
            if input_types
            else "text"
        )

        if is_flash:
            prompt = PROTECT_FLASH_PROMPT_TEMPLATE.format(
                prompt=str(inp),
                response="[No response provided]",
            )
            return [{"role": "user", "content": prompt}]

        # Protect (per-metric)
        # Use "vanilla" suffix (label only, no explanation) when max_tokens
        # is low — saves output tokens and reduces latency for guardrails.
        metric = ProtectHelper.resolve_metric(eval_name)
        suffix = "vanilla" if max_tokens and max_tokens <= 20 else "explanation_assistant"
        return build_mm_messages_for_protect(
            metric=metric,
            input_type=itype,
            input_value=inp or "",
            suffix_style=suffix,
        )

    # -- Response parsing ----------------------------------------------------

    @staticmethod
    def parse_response(content: str, *, is_flash: bool) -> Dict[str, Any]:
        """Parse the model response into a structured result."""
        if is_flash:
            return ProtectHelper._parse_flash_response(content)
        return ProtectHelper._parse_protect_response(content)

    @staticmethod
    def _parse_protect_response(content: str) -> Dict[str, Any]:
        m_label = _LABEL_RE.search(content or "")
        label = m_label.group(1).capitalize() if m_label else "Failed"

        m_expl = _EXPLANATION_RE.search(content or "")
        explanation = m_expl.group(1).strip() if m_expl else ""

        logger.info(
            "protect_parsed_result",
            label=label,
            explanation=explanation[:200] if explanation else "NONE",
        )
        return {"choices": [label], "explanation": explanation, "usage": 0}

    @staticmethod
    def _parse_flash_response(content: str) -> Dict[str, Any]:
        match = _FLASH_OUTPUT_RE.match(content or "")
        if match is None:
            parsed = {
                "prompt_harmfulness": "parse_error",
                "response_refusal": "parse_error",
                "response_harmfulness": "parse_error",
            }
        else:
            harmful_request, response_refusal, harmful_response = match.groups()
            parsed = {
                "prompt_harmfulness": (
                    "yes" if harmful_request.strip().lower() == "yes" else "no"
                ),
                "response_refusal": (
                    "yes"
                    if response_refusal.strip().lower() == "yes"
                    else (
                        "no"
                        if response_refusal.strip().lower() == "no"
                        else "n/a"
                    )
                ),
                "response_harmfulness": (
                    "yes"
                    if harmful_response.strip().lower() == "yes"
                    else (
                        "no"
                        if harmful_response.strip().lower() == "no"
                        else "n/a"
                    )
                ),
            }

        logger.info(
            "protect_flash_parsed_result",
            parsed=parsed,
        )

        is_harmful = parsed.get("prompt_harmfulness") == "yes"
        explanation = (
            f"Content detected as harmful. Prompt harmfulness: "
            f"{parsed.get('prompt_harmfulness', 'no')}"
            if is_harmful
            else "Content appears to be safe and appropriate."
        )
        return {
            "is_harmful": is_harmful,
            "choices": ["Failed" if is_harmful else "Passed"],
            "explanation": explanation,
            "prompt_harmful": is_harmful,
            "response_harmful": None,
            "details": parsed,
            "parsed": parsed,
            "usage": 0,
        }
