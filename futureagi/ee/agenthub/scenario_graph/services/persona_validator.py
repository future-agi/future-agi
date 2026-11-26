"""Persona validation — parse, validate, and fill missing fields.

Replaces the persona validation logic duplicated 3× across ESA:
  - categorize_and_validate_cases() L502-531
  - _generate_raw_cases_from_sda() L1301-1346
  - _convert_sda_data_to_cases() inline parsing
"""

import json
import random
from typing import Any, Dict

import structlog

from ee.agenthub.scenario_graph.persona_configurator import (
    PersonaConfigurator,
)

logger = structlog.get_logger(__name__)


def validate_persona(persona: Any, mode: str) -> Dict[str, Any]:
    """Parse, validate, and fill missing fields in a persona.

    Handles persona as dict, JSON string, or other types.
    Fills missing required fields with random values from PersonaConfigurator defaults.
    Returns a clean dict with only required fields in correct order.

    Args:
        persona: Raw persona data (dict, JSON string, or other).
        mode: Simulation mode ("voice" or "chat").

    Returns:
        Validated persona dict with required fields only, in canonical order.
    """
    # Parse persona to dict
    if isinstance(persona, str):
        try:
            persona = json.loads(persona)
            persona = {k.lower(): v for k, v in persona.items()}
        except Exception:
            persona = {}
    elif isinstance(persona, dict):
        # Clean None keys
        persona = {k: v for k, v in persona.items() if k is not None}
    else:
        persona = {}

    required_fields = PersonaConfigurator.get_required_fields(mode)
    property_dict = PersonaConfigurator.get_property_dict(mode)

    # Fill missing required fields
    for field in required_fields:
        if field not in persona or not persona[field]:
            if field in property_dict:
                prop_value = property_dict[field]
                if isinstance(prop_value, list):
                    persona[field] = random.choice(prop_value)
                else:
                    persona[field] = prop_value
            else:
                persona[field] = "Not Specified"

    # Keep only required fields in canonical order
    return {k: persona[k] for k in required_fields if k in persona}
