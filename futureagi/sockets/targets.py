"""Shared relay/subscriber target names, including established prompt job IDs."""

from uuid import UUID

# Keep the prompt endpoint prefixes: existing clients subscribe to the returned
# generate_<uuid>/improve_<uuid> value, not just its UUID suffix.
TARGET_PATTERN = (
    r"^(?:(?:generate|improve)_)?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![\s\S])"
)


def normalize_socket_target(value: str | UUID) -> str:
    value = str(value)
    prefix = ""
    for candidate in ("generate_", "improve_"):
        if value.startswith(candidate):
            prefix, value = candidate, value[len(candidate) :]
            break
    return prefix + str(UUID(value))
