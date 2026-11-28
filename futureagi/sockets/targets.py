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


def relay_group(organization_id, subscription=None) -> str:
    """Isolate the relay protocol from groups joined by pre-v2 workers."""
    room = f"ws2.{organization_id}"
    if subscription is None:
        return room
    try:
        subscription = normalize_socket_target(subscription)
    except ValueError:
        # Keep opaque identifiers used by older subscription clients.
        pass
    return f"{room}.{subscription}"


def relay_delivery(organization_id, message, subscription=None):
    """Bind the channel-layer destination and its routing metadata together."""
    target = (
        {"kind": "organization", "id": str(organization_id)}
        if subscription is None
        else {"kind": "subscription", "id": str(subscription)}
    )
    return relay_group(organization_id, subscription), {
        "type": "send_data",
        "target": target,
        "data": message,
    }
