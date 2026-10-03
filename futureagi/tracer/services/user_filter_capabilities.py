"""Users filter eligibility, separate from graph metric availability."""

from rest_framework import serializers

# These catalog definitions have no per-user predicate. The three population
# metrics remain valid graph outputs; do not reinterpret them as row metrics.
UNSUPPORTED_USERS_SYSTEM_FILTERS = frozenset(
    {
        "dataset",
        "eval_source",
        "active_users",
        "avg_cost_per_user",
        "avg_traces_per_user",
    }
)


def is_native_user_id_filter(item):
    """Only the native user label requires reverse-resolution to a user UUID.

    An attribute/eval/annotation can share the name ``user_id``. It must reach
    its own predicate compiler unchanged instead of becoming account scope.
    Call after public filter normalization/validation.
    """
    if item.get("column_id") != "user_id":
        return False
    cfg = item.get("filter_config") or {}
    family = str(cfg.get("col_type") or "").upper()
    property_id = str(item.get("property_id") or "")
    if property_id and not property_id.startswith("system_attribute:"):
        return False
    return family in ("", "NORMAL", "SYSTEM_METRIC")


def validate_users_filter_capabilities(filters):
    """Validate canonical leaves after FilterItemField has checked bindings.

    Identity matters: a SPAN_ATTRIBUTE (or eval/annotation) may legitimately
    have the same name. Legacy SYSTEM_METRIC leaves without property_id and
    registry-bound system leaves without col_type must both be checked.
    """
    unsupported = sorted(
        {
            item["column_id"]
            for item in filters
            if item["column_id"] in UNSUPPORTED_USERS_SYSTEM_FILTERS
            and (
                str(item["filter_config"].get("col_type") or "").upper()
                == "SYSTEM_METRIC"
                or str(item.get("property_id") or "").startswith("system_attribute:")
            )
        }
    )
    if unsupported:
        raise serializers.ValidationError(
            "Observe Users does not support these system fields as filters: "
            + ", ".join(unsupported)
            + ". Graph metric selections are unaffected; custom attributes must "
            "use their SPAN_ATTRIBUTE identity.",
            code="unsupported_users_filter",
        )
    return filters
