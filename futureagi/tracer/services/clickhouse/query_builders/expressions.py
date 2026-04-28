"""Shared SQL expression helpers for ClickHouse query builders."""

from typing import Optional


def annotation_numeric_value_expr(
    alias: Optional[str] = None, nullable: bool = False
) -> str:
    """Return the ClickHouse expression for a numeric annotation value.

    Annotations may store the numeric value under either ``rating`` (star
    ratings) or ``value`` (legacy/numeric). This helper returns the
    ``if(JSONHas(...), ...)`` expression that picks whichever is present.

    Args:
        alias: Optional table alias. When provided, the column is
            referenced as ``{alias}.value``; otherwise it's a bare
            ``value`` reference.
        nullable: When True, returns ``Nullable(Float64)`` so missing /
            non-numeric values become SQL NULL instead of 0.0. Use this
            for aggregations (avg, sum, …) so absent values are skipped
            rather than dragging the result toward zero.
    """
    prefix = f"{alias}." if alias else ""
    if nullable:
        return (
            f"if(JSONHas({prefix}value, 'rating'), "
            f"JSONExtract({prefix}value, 'rating', 'Nullable(Float64)'), "
            f"JSONExtract({prefix}value, 'value', 'Nullable(Float64)'))"
        )
    return (
        f"if(JSONHas({prefix}value, 'rating'), "
        f"JSONExtractFloat({prefix}value, 'rating'), "
        f"JSONExtractFloat({prefix}value, 'value'))"
    )
