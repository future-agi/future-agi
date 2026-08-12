from copy import deepcopy
from typing import Any, Dict, List, Optional
import difflib
import random
import json_repair


class PersonaConfigurator:
    """
    Configurator for persona properties based on the mode (chat/voice).
    """

    # Common properties applicable to both modes
    COMMON_PROPERTIES = {
        "gender": ["male", "female"],
        "age_group": [
            "18-25",
            "25-32",
            "32-40",
            "40-50",
            "50-60",
            "60+",
        ],
        "location": [
            "United States",
            "Canada",
            "United Kingdom",
            "Australia",
            "India",
        ],
        "profession": [
            "Student",
            "Teacher",
            "Engineer",
            "Doctor",
            "Nurse",
            "Business Owner",
            "Manager",
            "Sales Representative",
            "Customer Service",
            "Technician",
            "Consultant",
            "Accountant",
            "Marketing Professional",
            "Retired",
            "Homemaker",
            "Freelancer",
            "Other",
        ],
        "personality": [
            "Friendly and cooperative",
            "Professional and formal",
            "Cautious and skeptical",
            "Impatient and direct",
            "Detail-oriented",
            "Easy-going",
            "Anxious",
            "Confident",
            "Analytical",
            "Emotional",
            "Reserved",
            "Talkative",
        ],
        "communication_style": [
            "Direct and concise",
            "Detailed and elaborate",
            "Casual and friendly",
            "Formal and polite",
            "Technical",
            "Simple and clear",
            "Questioning",
            "Assertive",
            "Passive",
            "Collaborative",
        ],
        "language": [
            "English",
            "spanish",
            "hindi",
            "german",
            "french",
            "italian",
            "polish",
            "russian",
            "portuguese",
            "japanese",
            "korean",
            "chinese",
            "mandarin",
            "turkish",
            "swedish",
            "dutch",
            "norwegian",
            "finnish",
            "danish",
            "slovak",
            "ukrainian",
            "greek",
            "romanian",
            "georgian",
            "bulgarian",
            "thai",
            "hungarian",
            "czech",
            "croatian",
            "vietnamese",
            "indonesian",
            "malay",
            "tagalog",
            "filipino",
            "arabic",
            "hebrew",
            "telugu",
            "kannada",
            "marathi",
            "bengali",
            "tamil",
            "malayalam",
            "punjabi",
            "gujarati",
        ],
        "name": "realistic full name",
    }

    # Voice-specific properties
    VOICE_PROPERTIES = {
        "accent": [
            "American",
            "Australian",
            "Indian",
            "Canadian",
            "Neutral",
            "spanish",
            "south american",
            "german",
            "french",
            "italian",
            "polish",
            "russian",
            "portuguese",
            "brazilian",
            "japanese",
            "korean",
            "chinese",
            "mandarin",
            "turkish",
            "swedish",
            "dutch",
            "norwegian",
            "finnish",
            "danish",
            "slovak",
            "ukrainian",
            "greek",
            "romanian",
            "georgian",
            "bulgarian",
            "thai",
            "hungarian",
            "czech",
            "croatian",
            "vietnamese",
            "indonesian",
            "malay",
            "malaysian",
            "tagalog",
            "filipino",
            "arabic",
            "hebrew",
            "israeli",
            "telugu",
            "kannada",
            "marathi",
            "bengali",
            "tamil",
            "malayalam",
            "punjabi",
            "gujarati",
        ],
        "conversation_speed": ["0.5", "0.75", "1.0", "1.25", "1.5"],
        "background_sound": ["true", "false"],
        "finished_speaking_sensitivity": [
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
        ],
        "interrupt_sensitivity": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
    }

    # Chat-specific properties
    CHAT_PROPERTIES = {
        "tone": ["formal", "neutral", "casual"],
        "verbosity": ["brief", "balanced", "detailed"],
        "regional_mix": ["none", "light", "moderate", "heavy"],
        "slang_usage": ["none", "light", "moderate", "heavy"],
        "typos_frequency": ["none", "rare", "occasional", "frequent"],
        "punctuation": ["clean", "minimal", "expressive", "erratic"],
        "emoji_usage": ["never", "light", "regular", "heavy"],
    }

    @classmethod
    def normalize_mode(cls, mode: str) -> str:
        normalized = (mode or "").strip().lower()
        if normalized in {"voice"}:
            return "voice"
        if normalized in {"chat", "text"}:
            return "chat"
        return "voice"

    @classmethod
    def get_property_dict(cls, mode: str) -> Dict[str, Any]:
        """
        Get the full property dictionary for the specified mode.
        """
        normalized_mode = cls.normalize_mode(mode)
        properties: Dict[str, Any] = deepcopy(cls.COMMON_PROPERTIES)

        if normalized_mode == "voice":
            properties.update(deepcopy(cls.VOICE_PROPERTIES))
        elif normalized_mode == "chat":
            properties.update(deepcopy(cls.CHAT_PROPERTIES))

        return properties

    @classmethod
    def get_required_fields(cls, mode: str) -> List[str]:
        """
        Get the list of required fields for the specified mode.
        """
        normalized_mode = cls.normalize_mode(mode)
        fields = [
            "name",
            "gender",
            "age_group",
            "location",
            "profession",
            "personality",
            "communication_style",
            "language",
        ]

        if normalized_mode == "voice":
            fields.extend(
                [
                    "accent",
                    "conversation_speed",
                    "finished_speaking_sensitivity",
                    "interrupt_sensitivity",
                    "background_sound",
                ]
            )
        elif normalized_mode == "chat":
            fields.extend(
                [
                    "tone",
                    "verbosity",
                    "regional_mix",
                    "slang_usage",
                    "typos_frequency",
                    "punctuation",
                    "emoji_usage",
                ]
            )

        return fields

    @classmethod
    def get_allowed_fields(cls, mode: str) -> List[str]:
        """Fields allowed in the persona object for a given mode."""
        normalized_mode = cls.normalize_mode(mode)
        mode_fields = (
            list(cls.VOICE_PROPERTIES.keys())
            if normalized_mode == "voice"
            else list(cls.CHAT_PROPERTIES.keys())
        )
        return list(cls.COMMON_PROPERTIES.keys()) + mode_fields

    @classmethod
    def validate_and_correct_persona(
        cls, persona_data: Any, mode: str = "voice", valid_values: Optional[dict] = None
    ) -> Dict[str, Any]:
        """
        Validate and fix persona data against the constraints/properties for the given mode.
        Handles flattening of nested structures, fuzzy matching for values, and default fallbacks.
        """
        # 1. Parse string if needed
        if isinstance(persona_data, str):
            try:
                persona_data = json_repair.loads(persona_data)
            except Exception as e:
                print(f"Error parsing persona string: {e}")
                persona_data = {}

        if not isinstance(persona_data, dict):
            print(
                f"Persona data is not a dict after parsing, resetting. Got: {type(persona_data)}"
            )
            persona_data = {}

        normalized_mode = cls.normalize_mode(mode)

        # 2. Get valid properties/constraints
        if valid_values is None:
            # If no specific constraints provided, use the defaults for the mode
            valid_values = cls.get_property_dict(normalized_mode)

        constraint_keys = set(valid_values.keys())
        constraint_keys.add("name")  # Name is always a valid key

        # 3. Flatten the persona structure
        flattened_persona = cls._flatten_persona(persona_data, constraint_keys)

        # 4. Validate and Fix values
        corrected_data = {}
        for key, gen_val in flattened_persona.items():
            # If key is not in our constraints, just keep it (unless it's 'name' which we keep)
            if key not in valid_values:
                if key == "name":
                    corrected_data[key] = gen_val
                else:
                    corrected_data[key] = gen_val
                continue

            # Handle list/dict values for keys that should be single values
            # (Logic taken from original implementation)
            if isinstance(gen_val, list):
                print(
                    f"Generated value for {key} is a list, taking first element: {gen_val}"
                )
                gen_val = gen_val[0] if gen_val else None
            elif isinstance(gen_val, dict):
                # Take first value if dict
                vals = list(gen_val.values())
                gen_val = vals[0] if vals else None

            allowed_values = valid_values[key]

            # If allowed_values is not a list (e.g. "realistic full name" string),
            # we can't strictly validate against a set, so keep as is.
            if not isinstance(allowed_values, list):
                corrected_data[key] = gen_val
                continue

            # Strict and Fuzzy Matching
            str_allowed_values = [str(av) for av in allowed_values]

            # Direct match check (value or stringified value)
            if gen_val in allowed_values or str(gen_val) in str_allowed_values:
                corrected_data[key] = gen_val
                continue

            # Fuzzy match
            closest_match = difflib.get_close_matches(
                str(gen_val), str_allowed_values, n=1, cutoff=0.6
            )

            if closest_match:
                print(f"Correcting {key} from {gen_val} to {closest_match[0]}")
                corrected_value = closest_match[0]
            else:
                # Fallback to random valid value
                corrected_value = random.choice(allowed_values)
            corrected_data[key] = corrected_value

        # 5. Fill missing keys with random defaults
        for key in valid_values:
            if key not in corrected_data:
                allowed_values = valid_values[key]
                if isinstance(allowed_values, list) and allowed_values:
                    corrected_data[key] = random.choice(allowed_values)
                elif isinstance(allowed_values, str):
                    corrected_data[key] = allowed_values

        return corrected_data

    @staticmethod
    def _flatten_persona(persona: dict, keys_of_interest: set) -> dict:
        """
        Flatten nested dictionary, promoting keys that match `keys_of_interest`.
        """
        flattened = {}

        for key, val in persona.items():
            if key in keys_of_interest:
                if isinstance(val, list):
                    print(
                        f"Generated value for {key} is a list, taking first element.",
                        val,
                    )
                    val = val[0] if val else None
                elif isinstance(val, dict):
                    vals = list(val.values())
                    val = vals[0] if vals else None

                flattened[key] = val
                continue

            # If value is a dict and parent key is NOT a constraint key, check for children to promote
            if isinstance(val, dict):
                inner = val
                inner_keys = set(inner.keys())
                matching_keys = inner_keys & keys_of_interest
                non_matching = inner_keys - keys_of_interest

                # Promote matching keys
                for mk in matching_keys:
                    print(f"Promoting nested key: {mk}")
                    flattened[mk] = inner[mk]

                # Keep non-matching keys nested (optional, maybe not needed for strict validation but good for preserving extra info)
                if non_matching:
                    flattened[key] = {k: inner[k] for k in non_matching}
            else:
                # Non-dict value in a non-constraint key.
                # Original logic: "if isinstance(val, list)... val[0]"
                if isinstance(val, list):
                    val = val[0] if val else None
                elif isinstance(val, dict):
                    pass

                flattened[key] = val

        return flattened
