"""
Chat initial message generation for simulated customers.

This module contains the logic to generate a realistic first message in chat
based on persona and scenario situation, with safe fallbacks for production.
"""

import ast
import json

import structlog

from agentic_eval.core.llm.llm import LLM
from simulate.services.chat_constants import CHAT_SIM_MODEL_CONFIG
from simulate.constants.chat_initial_message_prompt import (
    build_chat_initial_message_messages,
)
from simulate.constants.persona_prompt_guides import (
    CHAT_COMMUNICATION_STYLE_GUIDES,
    CHAT_EMOJI_FREQUENCY_GUIDES,
    CHAT_PERSONALITY_GUIDES,
    CHAT_PUNCTUATION_STYLE_GUIDES,
    CHAT_REGIONAL_MIX_GUIDES,
    CHAT_SLANG_LEVEL_GUIDES,
    CHAT_TONE_GUIDES,
    CHAT_TYPO_LEVEL_GUIDES,
    CHAT_VERBOSITY_GUIDES,
)
from simulate.models import CallExecution

logger = structlog.get_logger(__name__)


def _parse_persona_value(persona_value: object) -> dict:
    """Parse a dataset persona cell into a dictionary (supports JSON, python-literal, or dict)."""
    if isinstance(persona_value, dict):
        return persona_value

    if not isinstance(persona_value, str):
        return {}

    raw = persona_value.strip()
    if not raw:
        return {}

    # Prefer JSON (newer pipeline); fallback to python-literal (older pipeline).
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    try:
        parsed = ast.literal_eval(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _get_persona_value(persona: dict, *keys: str) -> str:
    """Return the first non-empty value for the provided persona keys."""
    for key in keys:
        value = persona.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _extract_chat_style(persona: dict) -> dict[str, str]:
    """Normalize chat writing-style fields across new/legacy and snake/camel keys."""
    return {
        "tone": _get_persona_value(persona, "tone") or "neutral",
        "verbosity": _get_persona_value(persona, "verbosity") or "balanced",
        "regional_mix": _get_persona_value(persona, "regional_mix", "regionalMix")
        or "none",
        # New schema keys (preferred), then legacy keys.
        "slang_usage": _get_persona_value(persona, "slang_usage", "slangUsage")
        or _get_persona_value(persona, "slang_level", "slangLevel")
        or "none",
        "typos_frequency": _get_persona_value(
            persona, "typos_frequency", "typosFrequency"
        )
        or _get_persona_value(persona, "typo_level", "typoLevel")
        or "rare",
        "punctuation": _get_persona_value(persona, "punctuation")
        or _get_persona_value(persona, "punctuation_style", "punctuationStyle")
        or "clean",
        "emoji_usage": _get_persona_value(persona, "emoji_usage", "emojiUsage")
        or _get_persona_value(persona, "emoji_frequency", "emojiFrequency")
        or "light",
    }


def _build_chat_initial_message_guidance(persona: dict, style: dict[str, str]) -> str:
    """Build comprehensive guidance for initial-message generation using all persona guides."""

    def _norm(v: str) -> str:
        return (v or "").strip().lower()

    # Extract all persona characteristics
    personality_key = _norm(_get_persona_value(persona, "personality"))
    comm_style_key = _norm(
        _get_persona_value(persona, "communication_style", "communicationStyle")
    )
    tone_key = _norm(style.get("tone", ""))
    verbosity_key = _norm(style.get("verbosity", ""))
    regional_mix_key = _norm(style.get("regional_mix", ""))
    slang_key = _norm(style.get("slang_usage", ""))
    typos_key = _norm(style.get("typos_frequency", ""))
    punctuation_key = _norm(style.get("punctuation", ""))
    emoji_key = _norm(style.get("emoji_usage", ""))

    # Gather all relevant guidance
    guidance_parts = []

    # Core personality and communication
    if personality_key and personality_key in CHAT_PERSONALITY_GUIDES:
        guidance_parts.append(CHAT_PERSONALITY_GUIDES[personality_key])

    if comm_style_key and comm_style_key in CHAT_COMMUNICATION_STYLE_GUIDES:
        guidance_parts.append(CHAT_COMMUNICATION_STYLE_GUIDES[comm_style_key])

    # Writing style characteristics
    if tone_key and tone_key in CHAT_TONE_GUIDES:
        guidance_parts.append(CHAT_TONE_GUIDES[tone_key])

    if verbosity_key and verbosity_key in CHAT_VERBOSITY_GUIDES:
        guidance_parts.append(CHAT_VERBOSITY_GUIDES[verbosity_key])

    if regional_mix_key and regional_mix_key in CHAT_REGIONAL_MIX_GUIDES:
        guidance_parts.append(CHAT_REGIONAL_MIX_GUIDES[regional_mix_key])

    if slang_key and slang_key in CHAT_SLANG_LEVEL_GUIDES:
        guidance_parts.append(CHAT_SLANG_LEVEL_GUIDES[slang_key])

    if typos_key and typos_key in CHAT_TYPO_LEVEL_GUIDES:
        guidance_parts.append(CHAT_TYPO_LEVEL_GUIDES[typos_key])

    if punctuation_key and punctuation_key in CHAT_PUNCTUATION_STYLE_GUIDES:
        guidance_parts.append(CHAT_PUNCTUATION_STYLE_GUIDES[punctuation_key])

    if emoji_key and emoji_key in CHAT_EMOJI_FREQUENCY_GUIDES:
        guidance_parts.append(CHAT_EMOJI_FREQUENCY_GUIDES[emoji_key])

    if not guidance_parts:
        return ""

    return "\n".join(f"- {line}" for line in guidance_parts)


def _generate_chat_initial_message(
    *,
    persona: dict,
    situation: str,
    agent_name: str | None,
    initial_message_hint: str | None = None,
) -> str | None:
    """
    Generate a realistic first customer message for chat using an LLM.
    Returns None on failure so callers can fall back to the default initial message.
    """
    if not situation:
        return None

    style = _extract_chat_style(persona or {})
    guidance = _build_chat_initial_message_guidance(persona or {}, style)
    hint = (initial_message_hint or "").strip()

    # Build compact persona summary for context
    name = _get_persona_value(persona or {}, "name")
    age_group = _get_persona_value(persona or {}, "age_group", "ageGroup")
    location = _get_persona_value(persona or {}, "location")
    language = _get_persona_value(persona or {}, "language", "languages")

    persona_context = []
    if name:
        persona_context.append(f"Name: {name}")
    if age_group:
        persona_context.append(f"Age: {age_group}")
    if location:
        persona_context.append(f"Location: {location}")
    if language:
        persona_context.append(f"Language: {language}")

    persona_brief = " | ".join(persona_context) if persona_context else "General user"

    messages = build_chat_initial_message_messages(
        persona_brief=persona_brief,
        situation=situation,
        guidance=guidance,
        hint=hint,
    )

    try:
        logger.debug(
            "chat_initial_message_prompt_built",
            persona_brief=persona_brief,
            guidance_lines=len(guidance.split("\n")) if guidance else 0,
            hint_present=bool(hint),
        )
        model_cfg = CHAT_SIM_MODEL_CONFIG
        llm = LLM(
            model_name=model_cfg.model_name,
            temperature=0.7,  # Higher for more natural variation
            provider=model_cfg.provider,
            api_key=None,
        )
        text = (llm.call_llm(messages, model_cfg.provider) or "").strip()

        # Clean up any accidental formatting
        text = text.strip('"').strip("'").strip()

        # `LLM.call_llm()` may return an error string instead of raising. Treat that as failure
        # so the caller can fall back to a safe default without leaking error text to users.
        if "LLM call failed:" in text:
            logger.exception("chat_initial_message_llm_failed", error=text)
            return None

        return text or None
    except Exception as e:
        logger.exception("chat_initial_message_generation_failed", error=str(e))
        return None


def get_chat_initial_message(call_execution: CallExecution) -> tuple[str, str]:
    """
    Resolve the initial customer message for a chat simulation.

    Selection order:
    1) LLM-generated message (persona + situation)
    2) `call_execution.call_metadata["initial_message"]` (if provided)
    3) Hardcoded "Hi!"

    Returns:
        (initial_message, source) where source is one of:
        - "llm_generated"
        - "provided_initial_message"
        - "default_hi"
    """
    call_metadata = call_execution.call_metadata or {}
    provided_initial_message = call_metadata.get("initial_message")
    row_data = call_metadata.get("row_data") or {}
    persona = _parse_persona_value(row_data.get("persona"))
    situation = str(row_data.get("situation") or "").strip()

    agent_name = (
        call_execution.test_execution.run_test.agent_definition.agent_name
        if call_execution.test_execution
        and call_execution.test_execution.run_test
        and call_execution.test_execution.run_test.agent_definition
        else None
    )

    generated = _generate_chat_initial_message(
        persona=persona,
        situation=situation,
        agent_name=agent_name,
        initial_message_hint=provided_initial_message,
    )
    if generated:
        return generated, "llm_generated"

    if provided_initial_message and str(provided_initial_message).strip():
        return str(provided_initial_message).strip(), "provided_initial_message"

    return "Hi!", "default_hi"
