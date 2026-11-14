"""SDA payload construction — build payloads from agent_context + branch metadata.

Replaces ESA method: _create_branch_sda_payload (L1881-2066).
"""

from typing import Any, Dict, List, Optional

import structlog

from ee.agenthub.scenario_graph.persona_configurator import (
    PersonaConfigurator,
)

logger = structlog.get_logger(__name__)


def build_sda_payload(
    agent_context: Dict[str, Any],
    detailed_branch: Dict[str, Any],
    rows: int,
    mode: str = "voice",
    custom_instruction: Optional[str] = None,
    custom_columns: Optional[List[Dict[str, Any]]] = None,
    property_list: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build an SDA payload for a specific branch.

    This replaces ESA._create_branch_sda_payload, using agent_context dict
    instead of self.agent_definition.

    Args:
        agent_context: Flat dict with agent_name, description, languages, inbound, etc.
        detailed_branch: Branch data with detailedPath, path, start_node, end_node.
        rows: Number of rows (batch_size) to generate.
        mode: "voice" or "chat".
        custom_instruction: Optional user instruction to embed in payload.
        custom_columns: Optional list of custom column definitions.
        property_list: Optional persona property constraints.

    Returns:
        SDA payload dict ready for SyntheticDataAgent.generate_and_validate().
    """
    agent_name = agent_context.get("agent_name", "")
    description = agent_context.get("description", "")
    languages = agent_context.get("languages", ["en"])
    inbound = agent_context.get("inbound", True)

    branch_context_footer = "<conv_branch_info>"

    default_properties = PersonaConfigurator.get_property_dict(mode)

    def get_text(voice_text, chat_text):
        return voice_text if mode == "voice" else chat_text

    call_type_label = get_text("Call Type", "Interaction Type")
    call_type_val = "Inbound" if inbound else "Outbound"

    interaction_term = get_text("call", "chat session")
    interacting_term = get_text("calling", "messaging")

    situation_instruction = get_text(
        "Do not explicitly describe environmental details like traffic noise playing or label emotions directly. Instead, express the customer's situation through natural behavior and context that implies their state (e.g., being in traffic, handling a child at home), without stating sound effects or background cues. Write in third-person.",
        "Do not describe environmental sounds. Focus on the context in which the user is texting (e.g., 'texting while in a meeting', 'messaging from a noisy cafe', 'using voice-to-text while driving'). Include typos or short phrasing if appropriate for the situation. Write in third-person.",
    )

    def build_property_dict(prop_dict: Optional[Dict] = None) -> Dict:
        """Build property dict from user input or defaults."""
        prop_dict = prop_dict or {}
        result = {}
        for key, default_value in default_properties.items():
            result[key] = prop_dict.get(key, default_value)

        # Handle metadata flattening
        if "metadata" in prop_dict:
            for key, value in prop_dict["metadata"].items():
                result[key] = [value]
        if "additional_instruction" in prop_dict:
            result["additional_instruction"] = prop_dict["additional_instruction"]

        return result

    property_dict = {}
    property_list_updated = []
    if property_list:
        property_list_updated = [build_property_dict(p) for p in property_list]
    else:
        property_dict = build_property_dict()

    custom_instruction_str = None
    if custom_instruction:
        custom_instruction_str = (
            f"***IMPORTANT USER INSTRUCTION TO FOLLOW***: {custom_instruction}"
        )

    persona_payload = {
        "requirements": {
            "Dataset Name": f"{agent_name.lower().replace(' ', '_')}_dataset",
            "Dataset Description": (
                f"Create realistic customer personas and scenarios for {agent_name}. "
                f"Agent Purpose: {description}. "
                f"Supported Languages: {languages}. "
                f"{call_type_label}: {call_type_val}. "
                "Focus on scenarios that align with the provided description of the conversation branch "
                f"{branch_context_footer}"
            ),
            "Objective": (
                f"Generate training data for {agent_name} to handle calls effectively once the "
                f"conversation follows the branch description provided below. Ensure each record can be adapted to that flow. {custom_instruction_str or ''}"
                f"{branch_context_footer}"
            ),
            "patterns": (
                "Focus on the agent purpose, reinforce the outcomes implied by the branch description, and maintain "
                "realistic variability across personas, situations, and outcomes that can all map to the branch info below."
                f"{branch_context_footer}"
            ),
        },
        "constraints": [
            {
                "field": "persona",
                "type": "json",
                "content": (
                    "Detailed customer persona profile. For name always generate a realistic full name based on other characteristics. "
                ),
                "property": property_dict if not property_list else {},
            },
            {
                "field": "situation",
                "type": "text",
                "content": (
                    f"Specific situation of the customer when they initiate a {interaction_term} with agent: {agent_name}. "
                    "Situation should be tightly linked to the customer persona. Include only the current situation of the customer in the context "
                    f"of the agent's purpose of {interacting_term} the customer. Make the situation realistic and contextually relevant to the agent definition, "
                    "and ensure it naturally leads to the provided description of the conversation branch below. "
                    f"{situation_instruction}"
                    f"{branch_context_footer}"
                ),
                "property": {
                    "min_length": 30,
                    "max_length": 400,
                    "required_elements": [],
                },
            },
            {
                "field": "outcome",
                "type": "text",
                "content": (
                    "Create a specific Outcome that reflects how the interaction resolves once the conversation follows the branch description provided below. "
                    f"Base it on the agent purpose of {interacting_term} the customer, considering different customer responses and agent capabilities. "
                    "Outcome should be specific and measurable. Write in third-person past tense, 2-4 sentences (45-90 words), "
                    "describing the customer's final decision, the agent's key actions, concrete details like next steps, and the agent's tone or behavior. "
                    "Avoid dialogue or generic lines. Keep it professional and outcome-focused."
                    f"{branch_context_footer}"
                ),
                "property": {
                    "min_length": 30,
                    "max_length": 400,
                    "required_elements": [],
                },
            },
        ],
        "schema": {
            "persona": {"type": "json"},
            "situation": {"type": "text"},
            "outcome": {"type": "text"},
        },
        "batch_size": rows,
        "generation_type": "simulation" if property_list else "",
        "property_list": property_list_updated if property_list else {},
    }

    # Add custom columns to the payload
    if custom_columns:
        for column in custom_columns:
            column_name = column.get("name")
            column_type = column.get("data_type", "text")
            column_description = column.get("description", "")

            # Map data types to constraint types
            constraint_type = "text"  # default
            if column_type in ["json", "persona"]:
                constraint_type = "json"
            elif column_type in ["number", "integer", "float"]:
                constraint_type = "number"
            elif column_type == "boolean":
                constraint_type = "boolean"
            elif column_type == "string":
                constraint_type = "text"
            elif column_type == "datetime":
                constraint_type = "datetime"
            elif column_type == "array":
                constraint_type = "array"

            persona_payload["constraints"].append(
                {
                    "field": column_name,
                    "type": constraint_type,
                    "content": (
                        f"{column_description}. Generate realistic and contextually relevant data "
                        f"for {agent_name} scenarios that can be tailored using the conversation branch information below."
                        f"{branch_context_footer}"
                    ),
                    "property": {
                        "min_length": 10,
                        "max_length": 500,
                        "required_elements": [],
                    }
                    if constraint_type == "text"
                    else {},
                }
            )

            persona_payload["schema"][column_name] = {"type": constraint_type}

    return persona_payload
