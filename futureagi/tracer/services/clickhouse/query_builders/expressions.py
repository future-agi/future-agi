"""Shared SQL expression helpers for ClickHouse query builders."""

from typing import Optional


def annotation_numeric_value_expr(alias: Optional[str] = None) -> str:
    """Return the ClickHouse expression for a numeric annotation value.

    Annotations may store the numeric value under either ``rating`` (star
    ratings) or ``value`` (legacy/numeric). This helper returns the
    ``if(JSONHas(...), ...)`` expression that picks whichever is present.

    Args:
        alias: Optional table alias. When provided, the column is
            referenced as ``{alias}.value``; otherwise it's a bare
            ``value`` reference.
    """
    prefix = f"{alias}." if alias else ""
    return (
        f"if(JSONHas({prefix}value, 'rating'), "
        f"JSONExtractFloat({prefix}value, 'rating'), "
        f"JSONExtractFloat({prefix}value, 'value'))"
    )
