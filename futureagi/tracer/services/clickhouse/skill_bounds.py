"""Driver-independent parameters for embedded skill evidence reads."""

from typing import Any


def bind_outer_skill_bounds(query: str, params: dict[str, Any]) -> dict[str, Any]:
    """Bind a windowless skill fragment to its enclosing query's frozen dates.

    SQL fragments do not own the dates. Complete statements bind the integer
    endpoints before either driver sees the query; the native client also
    calls this for legacy raw SQL. Keep explicit window binds unchanged.
    """
    if (
        "skill_window_start_us" in params and "skill_window_end_us" in params
    ) or "%(skill_window_start_us)s" not in query:
        return params
    from tracer.services.clickhouse.query_builders.base import (
        _parse_dt,
        _unix_microseconds,
    )

    binds = {}
    for edge in ("start", "end"):
        key = f"skill_window_{edge}_us"
        if key in params:
            continue
        outer = f"{edge}_date"
        if outer in params:
            date = _parse_dt(params[outer])
            if date is None:
                raise ValueError(f"Expected a datetime bound for {outer}")
            value = _unix_microseconds(date)
        elif f"{outer}_us" in params:
            value = params[f"{outer}_us"]
        else:
            # A SELECT can itself be embedded in another builder. Only the
            # owner of both endpoints may complete the fragment.
            return params
        binds[key] = value
    return {**params, **binds}
