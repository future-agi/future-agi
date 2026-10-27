from typing import Any

from rest_framework import serializers

CURSOR_HELP_TEXT = (
    "Opaque continuation token returned by the previous page. When supplied, "
    "do not also send the numbered-page parameter."
)


def validate_cursor_exclusivity(
    serializer: serializers.Serializer,
    attrs: dict[str, Any],
    *,
    page_field: str,
    first_page: int = 0,
) -> dict[str, Any]:
    """Reject ambiguous cursor + numbered-page requests at the API boundary."""

    if attrs.get("cursor") and page_field in getattr(serializer, "initial_data", {}):
        raise serializers.ValidationError(
            {"cursor": f"cursor and {page_field} cannot be used together"}
        )
    if (
        attrs.get("cursor_mode")
        and not attrs.get("cursor")
        and int(attrs.get(page_field, first_page)) != first_page
    ):
        raise serializers.ValidationError(
            {
                "cursor_mode": (
                    f"cursor_mode can start only at {page_field}={first_page}"
                )
            }
        )
    return attrs


__all__ = ["CURSOR_HELP_TEXT", "validate_cursor_exclusivity"]
