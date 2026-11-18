"""
Prompt building utilities for dynamic prompt generation.

This module provides standalone functions for formatting personas and building
dynamic prompts with variable substitution. Used by both TestExecutor (Celery)
and Temporal workflow activities.

Extracted from TestExecutor to avoid coupling and enable gradual migration.
"""

import json
import re
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger(__name__)


def format_persona_voice_text(
    persona_data: Dict[str, Any],
    agent_version=None,
    row_data: Optional[Dict[str, Any]] = None,
    call_type: str = "inbound",
) -> str:
    """
    Format persona data into structured, human-readable text with behavioral guidance.

    Args:
        persona_data: Dictionary containing persona attributes including:
            - Basic fields: name, gender, age_group, occupation, location
            - Behavioral: personality, communication_style, keywords
            - Speech: languages, accent, multilingual
            - metadata: arbitrary key-value pairs for additional context
        agent_version: AgentVersion instance (for language fallback)
        row_data: Optional dictionary containing row data (for situation context)
        call_type: Type of call - "inbound" (making call) or "outbound" (receiving call)

    Returns:
        Formatted persona text with sections for identity, personality, speech characteristics,
        and detailed behavioral guidance
    """
    from ee.voice.constants.persona_prompt_guides import (
        VOICE_COMMUNICATION_STYLE_GUIDES,
        VOICE_PERSONALITY_GUIDES,
    )

    sections = []

    # =================================================================
    # SECTION 1: IDENTITY & ROLE
    # =================================================================
    identity_parts = []
    name = persona_data.get("name", "")
    profession = persona_data.get("profession") or persona_data.get("occupation", "")
    location = persona_data.get("location", "")
    age_group = persona_data.get("age_group") or persona_data.get("ageGroup", "")
    gender = persona_data.get("gender", "")

    if name:
        identity_parts.append(f"**Name:** {name}")
    if profession:
        identity_parts.append(f"**Occupation:** {profession}")
    if age_group:
        identity_parts.append(f"**Age Group:** {age_group}")
    if location:
        identity_parts.append(f"**Location:** {location}")
    if gender:
        identity_parts.append(f"**Gender:** {gender}")

    if identity_parts:
        sections.append("\n# YOUR IDENTITY\n\n" + "\n".join(identity_parts))

    # =================================================================
    # SECTION 2: CURRENT SITUATION & OBJECTIVE
    # =================================================================
    situation_section = "\n# YOUR CURRENT SITUATION\n\n"
    if row_data and row_data.get("situation"):
        situation_section += f"{row_data['situation']}\n\n"
    else:
        situation_section += "You are engaging in a routine conversation.\n\n"

    call_type_lower = call_type.lower() if call_type else "inbound"
    situation_section += "## Your Role in This Call\n\n"

    if call_type_lower == "outbound":
        # Agent is receiving the call (outbound from the system being tested)
        situation_section += (
            "**You are RECEIVING this call.** Someone is calling you.\n\n"
            "**CRITICAL: You did NOT initiate this call. You are the person being contacted.**\n\n"
            "Your behavior:\n"
            "- Answer the phone based on your personality and current situation\n"
            "- React naturally based on whether you were expecting this call\n"
            "- Let the caller introduce themselves and explain their purpose\n"
            "- YOU are the person being reached out to - respond from that position\n"
            "- Ask questions, express reactions, or raise concerns as this person would\n"
            "- NEVER switch roles and act as if you made the call or are providing the service\n"
            "**Name Verification:**\n"
            "- If the caller addresses you by the wrong name, correct them ONCE naturally\n"
            "- After your initial correction, do NOT keep correcting the name throughout the call\n"
            "- If they persist with the wrong name after your correction, you may show brief frustration\n\n"
            "- Don't let name correction dominate the entire interaction—move forward with the actual purpose of the call\n\n"
        )
    else:  # inbound
        # Agent is making the call (inbound to the system being tested)
        situation_section += (
            "**You are MAKING this call.** You initiated this contact.\n\n"
            "**CRITICAL: YOU started this conversation. You are reaching out to someone.**\n\n"
            "Your behavior:\n"
            "- Start by introducing yourself and stating your purpose clearly\n"
            "- You have a specific reason for calling (based on your situation above)\n"
            "- YOU are seeking something - information, help, service, answers, etc.\n"
            "- Provide information when asked, answer questions, follow their guidance\n"
            "- NEVER switch roles and act as if you're the one receiving the call or providing assistance\n\n"
        )
    situation_section += (
        "**React Naturally:** Respond to what you hear in real-time. "
        "Interrupt politely if needed, ask clarifying questions, express confusion if something is unclear, "
        "or show enthusiasm when appropriate. Stay in YOUR role throughout the entire conversation. "
        "If the agent keeps interrupting or talking over you, react like a real human: pause, "
        "politely ask them to let you finish, or briefly acknowledge the interruption before continuing "
        "what you were saying.\n"
    )

    sections.append(situation_section)

    # =================================================================
    # SECTION 3: PERSONALITY & COMMUNICATION STYLE
    # =================================================================
    personality = persona_data.get("personality", "")
    communication_style = persona_data.get("communication_style") or persona_data.get(
        "communicationStyle", ""
    )
    keywords = persona_data.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [x.strip() for x in keywords.split(",") if x.strip()]

    if personality or communication_style or (isinstance(keywords, list) and keywords):
        personality_section = "# YOUR PERSONALITY & COMMUNICATION\n\n"

        # Personality trait with specific behavioral guidance
        if personality:
            if isinstance(personality, list):
                personality = personality[0]
            elif isinstance(personality, dict):
                personality = list(personality.values())[0]
            personality_lower = personality.lower()
            personality_section += f"## Personality: {personality}\n\n"
            guidance = VOICE_PERSONALITY_GUIDES.get(
                personality_lower,
                "Let this personality trait guide your reactions, responses, and overall demeanor.",
            )
            personality_section += f"{guidance}\n\n"

        # Communication style with specific guidance
        if communication_style:
            if isinstance(communication_style, list):
                communication_style = communication_style[0]
            elif isinstance(communication_style, dict):
                communication_style = list(communication_style.values())[0]
            comm_style_lower = communication_style.lower()
            personality_section += f"## Communication Style: {communication_style}\n\n"
            guidance = VOICE_COMMUNICATION_STYLE_GUIDES.get(
                comm_style_lower,
                "Let this style guide how you express yourself throughout the conversation.",
            )
            personality_section += f"{guidance}\n\n"

        # Keywords/traits if present
        if isinstance(keywords, list) and keywords:
            keywords_str = ", ".join(str(k) for k in keywords)
            personality_section += f"**Key Traits:** {keywords_str}\n\n"
        sections.append(personality_section)

    # =================================================================
    # SECTION 4: LANGUAGE & ACCENT
    # =================================================================
    accent = persona_data.get("accent", "")
    language_data = persona_data.get("language") or persona_data.get("languages")
    if not language_data and agent_version:
        version_snapshot = getattr(agent_version, "configuration_snapshot", {}) or {}
        language_data = version_snapshot.get("language") or version_snapshot.get(
            "languages"
        )

    if accent or language_data:
        language_section = "# LANGUAGE & SPEECH PATTERNS\n\n"
        section_has_content = False

        # Language(s)
        if language_data:
            section_has_content = True
            langs = (
                language_data if isinstance(language_data, list) else [language_data]
            )
            lang_str = (
                ", ".join(str(l) for l in langs)
                if isinstance(langs, list)
                else str(language_data)
            )
            language_section += f"**Language(s):** {lang_str}\n"
            language_section += f"Use vocabulary, expressions, and language patterns natural to someone who speaks {lang_str}.\n"

            # Special handling for multilingual contexts
            if persona_data.get("multilingual"):
                language_section += "You are multilingual. Switch languages naturally based on context while maintaining your persona traits in all languages.\n"

        # Accent - special handling for Indian English
        if accent:
            if accent.lower() == "indian" and language_data:
                langs = (
                    language_data
                    if isinstance(language_data, list)
                    else [language_data]
                )
                lang_str_check = (
                    ", ".join(str(l) for l in langs)
                    if isinstance(langs, list)
                    else str(language_data)
                )
                if lang_str_check.lower().startswith("en"):
                    section_has_content = True
                    language_section += (
                        "**Number Formatting Rules:**\n"
                        "- Always express numbers in words (e.g., 'fifty thousand' not '50,000')\n"
                        "- For sequences like phone numbers, say each digit separately (e.g., 'seven nine two eight' not '7928')\n"
                        "- Never give mobile numbers or pincodes in sequential order like 'one two three four five six'\n\n"
                    )
        if section_has_content:
            sections.append(language_section)

    # =================================================================
    # SECTION 5: CONTEXTUAL AWARENESS
    # =================================================================
    if age_group or profession or location:
        context_section = "# CONTEXTUAL AWARENESS\n\n"
        if age_group:
            context_section += (
                f"**Age Context:** Your age group ({age_group}) influences your knowledge, cultural references, "
                f"interests, and how you relate to topics. Respond with age-appropriate perspective and vocabulary.\n"
            )
        if profession:
            context_section += (
                f"**Professional Context:** Your work as a {profession} shapes your priorities, problem-solving approach, "
                f"and how you view situations. Reference your professional background when relevant.\n"
            )
        if location:
            context_section += (
                f"**Geographic Context:** Being from {location} influences your cultural context, experiences, "
                f"time zone awareness, and regional references. Use examples and perspectives from your location.\n\n"
            )

        sections.append(context_section)

    # =================================================================
    # SECTION 6: ADDITIONAL METADATA (if present)
    # =================================================================
    metadata = persona_data.get("metadata")
    if metadata and isinstance(metadata, dict):
        metadata_parts = []
        for key, value in metadata.items():
            formatted_key = key.replace("_", " ").title()
            metadata_parts.append(f"**{formatted_key}:** {value}")

        if metadata_parts:
            sections.append(
                "# ADDITIONAL CHARACTERISTICS\n\n" + "\n".join(metadata_parts)
            )

    # =================================================================
    # SECTION 7: CORE SIMULATION RULES
    # =================================================================
    rules_section = "# HOW TO BE THIS PERSON\n\n"
    rules_section += (
        "You ARE this person. Embody this character completely in every response.\n\n"
    )
    rules_section += "## Core Rules\n\n"
    rules_section += "1. **Full Embodiment:** Every response must come from this character's perspective, background, and emotional state.\n"
    rules_section += "2. **Natural Speech Only:** Generate ONLY dialogue your character would say. Never include:\n"
    rules_section += "   - Stage directions (e.g., *sighs*, [anxious])\n"
    rules_section += "   - Meta-commentary or explanations\n"
    rules_section += "   - Labels or descriptions of your actions\n"
    rules_section += "3. **Unwavering Consistency:** Maintain your personality, communication style, and accent from start to finish. No exceptions.\n"
    rules_section += "4. **Contextual Authenticity:** Your knowledge, vocabulary, and references must match your age, profession, and location.\n"
    rules_section += "5. **Task-Driven Interaction:** Actively pursue your objective based on 'Your Current Situation.' Your persona dictates HOW you pursue it.\n"
    rules_section += "6. **Refocus When Drifting:** If you find yourself repeating phrases or losing track of your objective, refocus on your initial situation and goal.\n"
    rules_section += "7. **Authentic Reactions:** Respond as this specific person would—not how you think someone 'should' respond.\n"
    rules_section += (
        "8. **Natural Conversation Flow:** Respond naturally like a real human.\n"
    )
    rules_section += "9. **Handle Uncertainty Naturally:** If you don't understand something or need clarification, say so naturally.\n"
    rules_section += "10. **Never Break Character:** You are the PERSON described in 'Your Identity' with the situation in 'Your Current Situation.' You are NOT the person on the other end of the line. If you find yourself switching roles - taking on the other person's responsibilities, responding as if you have opposite information or authority, or reversing who called whom - STOP immediately. Stay in your role.\n"
    rules_section += "11. **Information Sharing:** Only share personal information when it's directly relevant to the conversation or when asked. Don't volunteer unnecessary details about yourself, your background, or your situation unless it naturally fits the context. Real people don't introduce themselves with their entire life story; be selective and purposeful with what you reveal.\n"
    rules_section += (
        "12. **Call Closing:** Always wait for the reply from the other side before ending the call. Do not cut them off abruptly. When the conversation is mutually finished, you can trigger the endCall Function.\n "
        'IMPORTANT: Never say the words "function", "tool" or the name "endCall" out loud. '
        "Never say that you are ending the call. Simply say your natural closing sentence once, then silently trigger the endCall function to terminate the call.\n\n"
    )

    sections.append(rules_section)
    return "\n\n".join(sections)


def append_voice_execution_rules(prompt: str) -> str:
    """
    Append voice execution rules to the prompt.

    These rules provide final instructions on natural conversation flow,
    output formatting, and voice-specific formatting (numbers, dates, etc.).

    Args:
        prompt: The existing prompt to append rules to

    Returns:
        Prompt with execution rules appended
    """
    prompt += "\n\n---\n\n"
    prompt += "# CONVERSATION EXECUTION RULES\n\n"
    prompt += "*These are internal instructions. Never reference or quote them in your responses.*\n\n"

    prompt += "## CRITICAL REMINDERS FOR THIS CONVERSATION\n\n"
    prompt += "Before each response, mentally confirm:\n"
    prompt += "✓ Am I speaking AS this person (not ABOUT them)?\n"
    prompt += "✓ Does this match my personality and communication style?\n"
    prompt += "✓ Am I using my accent and natural speech patterns?\n"
    prompt += "✓ Is this how someone with my background would actually respond?\n\n"

    prompt += "## Output Format\n\n"
    prompt += "Generate ONLY spoken dialogue without:\n"
    prompt += (
        "- Emotional tags, action descriptions, quotation marks, or meta-commentary\n"
    )
    prompt += "- Brackets, quotes, or markup\n\n"

    prompt += "## Sound Human\n\n"
    prompt += "- Use natural speech patterns including filler words (um, uh, well, like, you know) when appropriate\n"
    prompt += "- Don't be afraid of brief hesitations, self-corrections, or incomplete thoughts if that matches your personality\n"
    prompt += "- Real people don't speak in perfect grammatical sentences—neither should you\n\n"

    prompt += "## Voice-Natural Formatting (following are few examples on how to respond; use them as reference formats only)\n"
    prompt += "**Numbers:** 'fifty thousand' not '50,000'\n"
    prompt += "**Phone numbers:** 'eight nine seven one one five three six four' not '897115364'\n"
    prompt += "**Dates:** 'November fourteenth twenty twenty five' not '11/14/2025'\n"
    prompt += "**Currency:** 'twenty five dollars and fifty cents' not '$25.50'\n"
    prompt += "**Time:** 'three thirty PM' not '3:30 PM'\n"
    prompt += (
        "**Punctuation spacing:** Always add a space after punctuation before the next word "
        "(e.g., 'Thank you. I…' or 'Thank you.. I…', not 'Thank you.I…' or 'Thank you..I…').\n\n"
    )

    prompt += "Be natural and conversational.\n"
    return prompt


def generate_dynamic_prompt(
    prompt_template: str,
    row_data: Dict[str, Any],
    agent_version,
    call_type: Optional[str] = None,
) -> str:
    """
    Generate dynamic prompt by substituting variables from row data.

    This function:
    1. Replaces {{persona}} with formatted persona text (if present)
    2. Replaces other {{variable}} placeholders with row_data values
    3. Appends voice execution rules

    Args:
        prompt_template: The prompt template with variables like {{persona}}, {{situation}}
        row_data: Dictionary containing row data from dataset
        agent_version: Version of the agent being tested (for configuration snapshot)
        call_type: Type of call - "inbound" or "outbound" (optional, inferred from agent_version)

    Returns:
        The final prompt with variables substituted and conversation rules applied
    """
    try:
        # Handle case where agent_version is None (e.g., prompt-based simulations)
        if agent_version is None:
            version_snapshot = {}
            resolved_inbound = True
            resolved_call_type = call_type or "inbound"
            resolved_agent_type = "text"
        else:
            # Determine call type from agent_version if not provided
            version_snapshot = (
                getattr(agent_version, "configuration_snapshot", {}) or {}
            )
            raw_inbound = version_snapshot.get("inbound", None)
            if raw_inbound is None:
                raw_inbound = getattr(agent_version.agent_definition, "inbound", True)
            if isinstance(raw_inbound, str):
                resolved_inbound = raw_inbound.strip().lower() == "true"
            else:
                resolved_inbound = bool(raw_inbound)

            resolved_call_type = call_type or (
                "inbound" if resolved_inbound else "outbound"
            )

            resolved_agent_type = (
                version_snapshot.get("agent_type")
                or version_snapshot.get("agentType")
                or getattr(agent_version.agent_definition, "agent_type", None)
            )

        logger.debug(
            "dynamic_prompt_builder_context",
            call_type=resolved_call_type,
            agent_type=resolved_agent_type,
        )

        # Create a copy of the template
        dynamic_prompt = prompt_template

        # Handle persona variable specially
        if "{{persona}}" in dynamic_prompt and "persona" in row_data:
            persona = row_data["persona"]

            # Parse persona if it's a string
            if isinstance(persona, str):
                try:
                    persona_data = json.loads(persona)
                except json.JSONDecodeError:
                    try:
                        import ast

                        persona_data = ast.literal_eval(persona)
                    except (ValueError, SyntaxError) as e:
                        logger.warning(
                            f"Failed to parse persona string: {e}. Using empty persona."
                        )
                        persona_data = {}
            elif isinstance(persona, dict):
                persona_data = persona
            else:
                persona_data = {}

            # Format persona into structured text (voice only for now)
            # Chat/text formatting would use format_persona_chat_text (not implemented here yet)
            if str(resolved_agent_type).lower() in {"text", "chat"}:
                # TODO: Implement format_persona_chat_text if needed
                logger.warning(
                    "Chat persona formatting not implemented in prompt_builder.py yet"
                )
                persona_text = ""
            else:
                persona_text = format_persona_voice_text(
                    persona_data=persona_data,
                    agent_version=agent_version,
                    row_data=row_data,
                    call_type=resolved_call_type,
                )

            dynamic_prompt = dynamic_prompt.replace("{{persona}}", persona_text)

            # We already inject the situation inside persona_text, so scrub any user-templated {{situation}}
            # 1) Remove "Currently, {{situation}}" (case-insensitive, optional comma/period, tolerant spacing)
            dynamic_prompt = re.sub(
                r"(?i)\s*(?:\.\s*)?currently\s*,?\s*\{\{situation\}\}\s*(?:\.\s*)?",
                "\n\n",
                dynamic_prompt,
            )
            # 2) Fallback: remove any remaining bare "{{situation}}" with optional surrounding space/periods
            dynamic_prompt = re.sub(
                r"\s*(?:\.\s*)?\{\{situation\}\}\s*(?:\.\s*)?",
                "\n\n",
                dynamic_prompt,
            )
            # 3) Polish: collapse spaces and fix punctuation spacing
            dynamic_prompt = re.sub(
                r"[ \t]{2,}", " ", dynamic_prompt
            )  # collapse multiple spaces
            dynamic_prompt = re.sub(
                r"\s+([,.!?;:])", r"\1", dynamic_prompt
            )  # no space before punctuation
            dynamic_prompt = dynamic_prompt.strip()  # trim ends

        # Replace other variables in the template with actual values from row_data
        for key, value in row_data.items():
            if key in {"persona", "situation"}:  # Already handled keys
                continue
            placeholder = f"{{{{{key}}}}}"
            if placeholder in dynamic_prompt:
                dynamic_prompt = dynamic_prompt.replace(placeholder, str(value))

        # =================================================================
        # FINAL INSTRUCTIONS: CONVERSATION ENFORCEMENT
        # =================================================================
        # For voice calls, append voice execution rules
        if str(resolved_agent_type).lower() not in {"text", "chat"}:
            dynamic_prompt = append_voice_execution_rules(dynamic_prompt)

        logger.info(
            "dynamic_prompt_generated",
            call_type=resolved_call_type,
            agent_type=resolved_agent_type,
            prompt_length=len(dynamic_prompt),
        )

        return dynamic_prompt

    except Exception as e:
        logger.error(f"Error generating dynamic prompt: {str(e)}")
        return prompt_template  # Return original template if substitution fails
