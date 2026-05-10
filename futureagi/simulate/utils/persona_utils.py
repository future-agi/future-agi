import structlog

logger = structlog.get_logger(__name__)


def persona_first(values: str | list | None, field: str, default: str) -> str:
    """Return a scalar string from a persona field that may be stored as a
    plain string or a list.

    DB rows can store persona attributes as either a scalar string ("male")
    or a list (["male", "female"]). Calling values[0] on a plain string
    would silently return the first character, so we handle all three cases.

    Logs a warning when the list holds more than one value.
    """
    if not values:
        return default
    if isinstance(values, str):
        return values
    if len(values) > 1:
        logger.warning(
            "persona_attribute_multi_value_truncated",
            field=field,
            selected=values,
            used=values[0],
        )
    return values[0]
