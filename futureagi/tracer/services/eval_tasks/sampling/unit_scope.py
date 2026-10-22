"""Physical-key supersets for a finite page of canonical sampling units."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from tracer.services.clickhouse.v2.id_remap_sql import bounded_survivor_map_subquery

MAX_UNIT_PARAMETER_BYTES = 65_536


def unit_batches(units: Iterable[str]) -> Iterator[list[str]]:
    batch = []
    seen = set()
    size = 0
    for value in units:
        unit = str(value)
        if unit in seen:
            continue
        encoded = len(unit.encode("utf-8")) * 2 + 8
        if encoded > MAX_UNIT_PARAMETER_BYTES:
            raise ValueError("Sampling unit exceeds its parameter budget.")
        if batch and (len(batch) == 1000 or size + encoded > MAX_UNIT_PARAMETER_BYTES):
            yield batch
            batch, seen, size = [], set(), 0
        batch.append(unit)
        seen.add(unit)
        size += encoded
    if batch:
        yield batch


def physical_unit_scope(
    *,
    unit_ids: list[str],
    sampling_unit: str,
    row_type: str,
    attribute_value_sql: str | None,
    bounds_sql: str | None = None,
    span_closure: str = "span",
    param_prefix: str = "sp_new",
) -> tuple[str, dict]:
    """Return an immutable-coordinate seed, never filter values before replay.

    Old/new remapped IDs and mutable custom contact attributes only discover a
    superset. All versions of the touched physical rows/traces are replayed by
    the caller before exact canonical membership is tested.

    ``bounds_sql`` restricts every physical ``spans`` scan to immutable
    coordinates (event hour/partition) and, optionally, a physical-arrival
    ceiling. It must only contain predicates that every version of an
    admitted identity shares, so it can never hide a newer version from the
    caller's latest-state replay. ``span_closure="trace"`` replays the whole
    trace of every seeded span for span rows, which keeps trace-local row
    filters identical to a full-window read.
    """
    if span_closure not in ("span", "trace"):
        raise ValueError("Unsupported sampling unit closure.")
    if not param_prefix.replace("_", "").isalnum():
        raise ValueError("Sampling unit scope parameter prefix is invalid.")
    by_kind = {}
    fallback = {"spans": "span", "traces": "trace", "sessions": "session"}[row_type]
    for unit in unit_ids:
        kind, separator, value = unit.partition(":")
        if not separator or not value or kind not in {sampling_unit, fallback}:
            raise ValueError("Sampling unit escaped the policy namespace.")
        by_kind.setdefault(kind, []).append(value)
    bounds = f" AND {bounds_sql}" if bounds_sql else ""
    predicates = []
    params = {}
    for kind, values in by_kind.items():
        param = f"{param_prefix}_{kind}_ids"
        params[param] = tuple(values)
        placeholder = f"%({param})s"
        if kind in ("span", "trace"):
            column = "id" if kind == "span" else "trace_id"
            predicates.append(f"{column} IN {placeholder}")
        elif kind in ("session", "user"):
            column = "trace_session_id" if kind == "session" else "end_user_id"
            table = (
                "trace_session_id_remap" if kind == "session" else "end_user_id_remap"
            )
            mapping = bounded_survivor_map_subquery(table, candidate_param=param)
            predicates.append(
                f"({column} IN {placeholder} OR {column} IN (SELECT any_id FROM ({mapping}) WHERE survivor_id IN {placeholder}))"
            )
        else:
            if attribute_value_sql is None:
                raise ValueError(
                    "Custom contact scope requires its attribute expression."
                )
            predicates.append(f"({attribute_value_sql}) IN {placeholder}")
    seed = " OR ".join(f"({predicate})" for predicate in predicates)
    scoped_seed = (
        f"FROM spans PREWHERE project_id = %(sp_project)s{bounds} WHERE {seed}"
    )
    if row_type == "spans" and span_closure == "span":
        coordinates = (
            "(observation_type, service_name, toStartOfHour(start_time), trace_id, id)"
        )
        return (
            f"project_id = %(sp_project)s AND {coordinates} IN (SELECT {coordinates} {scoped_seed})",
            params,
        )
    if row_type in ("spans", "traces"):
        return (
            f"project_id = %(sp_project)s AND trace_id IN (SELECT trace_id {scoped_seed})",
            params,
        )
    sessions = f"SELECT trace_session_id {scoped_seed}"
    # A session row's identity/filters may depend on another trace or ID alias.
    # Expand only remap groups touched by the candidate units' session IDs.
    aliases = f"""SELECT arrayJoin(arrayDistinct(arrayConcat(groupArray(old_id), [new_id])))
        FROM trace_session_id_remap FINAL WHERE new_id IN (
            SELECT new_id FROM trace_session_id_remap FINAL
            WHERE old_id IN ({sessions}) OR new_id IN ({sessions})
        ) GROUP BY new_id"""
    return (
        f"""project_id = %(sp_project)s AND trace_id IN (
        SELECT trace_id FROM spans PREWHERE project_id = %(sp_project)s{bounds}
        WHERE trace_session_id IN ({sessions}) OR trace_session_id IN ({aliases})
    )""",
        params,
    )
