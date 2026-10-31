"""Hold newly unpriceable native usage until readers can represent unknown cost."""

import math
from typing import Any

from tracer.utils.normalization import CONTRACT, normalize_attributes
from tracer.utils.normalization.details import needs_detailed_pricing


def requires_cost_coverage(attributes: dict[str, Any]) -> bool:
    """Inspect normalized attributes without inventing a zero or a price.

    Both ingestion implementations understand the canonical cost aliases. For
    detailed usage we require a total or both components, not a partial charge.
    Non-additive summaries have no charge to represent and remain accepted.
    """
    if attributes.get("fi.usage.contribution") == "none" or not needs_detailed_pricing(
        attributes
    ):
        return False

    def component(name: str) -> tuple[bool, float | None]:
        for key in CONTRACT["detailed_cost_keys"][name]:
            if key in attributes:
                value = attributes[key]
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    return True, None
                try:
                    number = float(value)
                except (ValueError, OverflowError):
                    return True, None
                return True, number if math.isfinite(number) and number >= 0 else None
        return False, None

    has_total, total = component("total")
    if has_total:
        return total is None
    _, input_cost = component("input")
    _, output_cost = component("output")
    return (
        input_cost is None
        or output_cost is None
        or not math.isfinite(input_cost + output_cost)
    )


def cost_coverage_error(
    attributes: dict[str, Any], scope: dict[str, Any] | None = None
) -> str:
    if requires_cost_coverage(normalize_attributes(attributes, scope)):
        return CONTRACT["cost_coverage_error"]
    return ""
