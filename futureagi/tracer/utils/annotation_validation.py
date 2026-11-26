"""Shared annotation value validation utility.

Used by:
- tracer/views/observation_span.py (add_annotations endpoint)
- tracer/views/annotation.py (BulkAnnotationView)
- ai_tools/tools/tracing/_annotation_validation.py (MCP tools)

Validates annotation values against label type and settings (TEXT length,
NUMERIC range/step, STAR range, CATEGORICAL options, THUMBS_UP_DOWN boolean).
"""


def validate_annotation_value(
    label_type: str,
    label_settings: dict | None,
    *,
    value: str | None = None,
    value_float: float | None = None,
    value_bool: bool | None = None,
    value_str_list: list[str] | None = None,
) -> str | None:
    """Validate annotation value against label type and settings.

    Args:
        label_type: The annotation type string (e.g. "text", "numeric", "star",
            "categorical", "thumbs_up_down").
        label_settings: The label's settings dict (may be None).
        value: String value (for TEXT).
        value_float: Float value (for NUMERIC/STAR).
        value_bool: Boolean value (for THUMBS_UP_DOWN).
        value_str_list: List of strings (for CATEGORICAL).

    Returns:
        Error message string if validation fails, None if valid.
    """
    from model_hub.models.choices import AnnotationTypeChoices

    settings = label_settings or {}

    if label_type == AnnotationTypeChoices.TEXT.value:
        if value is None:
            return 'TEXT annotation requires "value" field.'
        min_len = settings.get("min_length")
        max_len = settings.get("max_length")
        if min_len is not None and len(value) < min_len:
            return f"Text too short. Minimum length is {min_len} characters."
        if max_len is not None and len(value) > max_len:
            return f"Text too long. Maximum length is {max_len} characters."

    elif label_type in [
        AnnotationTypeChoices.NUMERIC.value,
        AnnotationTypeChoices.STAR.value,
    ]:
        if value_float is None:
            return f'{label_type.upper()} annotation requires "value_float" field.'

        try:
            numeric_value = float(value_float)
        except (TypeError, ValueError):
            return "value_float must be a number."

        if label_type == AnnotationTypeChoices.NUMERIC.value:
            min_val = settings.get("min")
            max_val = settings.get("max")
            if min_val is not None and numeric_value < min_val:
                return f"value_float {numeric_value} is below minimum {min_val}."
            if max_val is not None and numeric_value > max_val:
                return f"value_float {numeric_value} exceeds maximum {max_val}."

            step_size = settings.get("step_size")
            if step_size:
                remainder = (numeric_value - (min_val or 0)) % step_size
                if (
                    remainder not in (0, step_size)
                    and remainder > 1e-6
                    and (step_size - remainder) > 1e-6
                ):
                    return f"value_float must align with step_size {step_size}."

        elif label_type == AnnotationTypeChoices.STAR.value:
            max_stars = settings.get("no_of_stars")
            if max_stars is not None and (
                numeric_value < 1 or numeric_value > max_stars
            ):
                return f"value_float must be between 1 and {max_stars}."

    elif label_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
        if value_bool is None:
            return 'THUMBS_UP_DOWN annotation requires "value_bool" field.'

    elif label_type == AnnotationTypeChoices.CATEGORICAL.value:
        if value_str_list is None:
            if value is not None:
                value_str_list = [v.strip() for v in value.split(",")]
            else:
                return (
                    'CATEGORICAL annotation requires "value_str_list" or "value" field.'
                )

        if not isinstance(value_str_list, list):
            return 'For CATEGORICAL annotations "value_str_list" must be a list.'

        if "options" in settings:
            allowed_options = [opt["label"] for opt in settings["options"]]
            invalid_values = [
                val for val in value_str_list if val not in allowed_options
            ]
            if invalid_values:
                return (
                    f"Invalid categorical values: {', '.join(invalid_values)}. "
                    f"Allowed options: {', '.join(allowed_options)}"
                )

        if not settings.get("multi_choice", True):
            if len(value_str_list) != 1:
                return (
                    "Multiple values provided but this label "
                    "does not allow multi_selection."
                )
    else:
        return f"Unsupported annotation type: {label_type}"

    return None
