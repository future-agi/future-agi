import structlog

logger = structlog.get_logger(__name__)


def persona_first(values: list | None, field: str, default: str) -> str:
    """Return the first element of a persona list field.

    Logs a warning when the list holds more than one value because the voice
    mapper consumes only a single string — extra selections are silently dropped
    at runtime without this guard.
    """
    if not values:
        return default
    if len(values) > 1:
        logger.warning(
            "persona_attribute_multi_value_truncated",
            field=field,
            selected=values,
            used=values[0],
        )
    return values[0]
