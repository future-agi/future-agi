"""Immutable-key restrictions and finite nomination for manifest readers.

Only logical-key predicates commute with replacement collapse. These helpers
never accept caller SQL, mutable ownership, status, deletion or field timestamps.
A nominated key page is NOT a final-row page: every allowed revision of each key
must still enter the subsequent committed collapse, including tombstones.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from clickhouse_driver.util.escape import escape_param

from tracer.services.derived_metrics.storage import TABLES

MAX_KEY_IN_ITEMS = 1000
MAX_KEY_IN_BYTES = 64 * 1024
MAX_KEY_MERGE_BYTES = 1024 * 1024
_PREFIX = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,47}\Z")
_ESCAPE_CONTEXT = SimpleNamespace(
    server_info=SimpleNamespace(get_timezone=lambda: "UTC")
)


def validate_prefix(prefix):
    if not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix):
        raise ValueError("invalid metric parameter namespace")
    return prefix


def merge_bindings(*bindings):
    """Refuse to silently change the meaning of an already composed relation."""
    result = {}
    for params in bindings:
        for name, value in params.items():
            if name in result and result[name] != value:
                raise ValueError("conflicting metric query bindings")
            result[name] = value
    return result


def _table(table):
    return TABLES[table.name if hasattr(table, "name") else table]


def _sequence(values):
    if not isinstance(values, Sequence) or isinstance(values, str | bytes | bytearray):
        raise ValueError("metric key membership requires a finite sequence")
    return values


def _columns(table, columns):
    names = (columns,) if isinstance(columns, str) else columns
    if (
        not isinstance(names, tuple)
        or not names
        or any(not isinstance(name, str) for name in names)
        or len(set(names)) != len(names)
        or set(names) - set(table.logical_key)
    ):
        raise ValueError(
            "only distinct immutable logical keys can be pushed before collapse"
        )
    return names


def _value(kind, value):
    """Use physical key types, not SQL's sometimes-lossy implicit coercions."""
    try:
        if kind == "UUID":
            if not isinstance(value, str | UUID):
                raise ValueError
            return UUID(str(value))
        if kind.startswith("UInt"):
            width = int(kind[4:])
            if type(value) is not int or not 0 <= value < 1 << width:
                raise ValueError
            return value
        if kind in {"String", "LowCardinality(String)"}:
            if not isinstance(value, str):
                raise ValueError
            return value
        if kind == "DateTime('UTC')":
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.microsecond
            ):
                raise ValueError
            value = value.astimezone(UTC)
            if not 0 <= value.timestamp() < 1 << 32:
                raise ValueError
            return value
    except (ValueError, TypeError, OverflowError):
        pass
    # Do not echo private identifiers or values in a type error.
    raise ValueError("invalid metric logical-key value")


def _member(table, columns, value):
    schema = dict(table.schema)
    names = _columns(table, columns)
    if isinstance(columns, str):
        return _value(schema[columns], value)
    if not isinstance(value, tuple) or len(value) != len(names):
        raise ValueError("metric tuple membership has wrong arity")
    return tuple(
        _value(schema[name], item) for name, item in zip(names, value, strict=True)
    )


def _literal(value, budget):
    # Bound string allocation BEFORE the driver's escaping/UTF-8 conversion.
    # Tuples here have at most the number of logical-key columns.
    atoms = value if isinstance(value, tuple) else (value,)
    if any(isinstance(atom, str) and len(atom) > budget for atom in atoms):
        raise ValueError("metric key membership byte cap exceeded")
    text = str(escape_param(value, _ESCAPE_CONTEXT))
    if len(text.encode("utf-8")) > budget:
        raise ValueError("metric key membership byte cap exceeded")
    return text


def normalize_key_in(
    table, key_in, *, max_items=MAX_KEY_IN_ITEMS, max_bytes=MAX_KEY_IN_BYTES
):
    """Validate before SQL generation; count escaped bindings, including syntax.

    Budgets include fixed clauses and duplicates in the supplied sequences.
    Deduplication is deterministic, but never a way around an input bound.
    None and an empty mapping are unrestricted; an empty sequence is FALSE.
    """
    table = _table(table)
    if key_in is None:
        key_in = {}
    if not isinstance(key_in, Mapping):
        raise ValueError("metric key membership requires a mapping")
    if (
        type(max_items) is not int
        or not 1 <= max_items <= MAX_KEY_IN_ITEMS
        or type(max_bytes) is not int
        or not 1 <= max_bytes <= MAX_KEY_IN_BYTES
    ):
        raise ValueError("invalid metric key membership budget")
    used, items, size, result = set(), 0, 0, {}
    for columns, values in key_in.items():
        names = _columns(table, columns)
        if used.intersection(names):
            raise ValueError("overlapping metric key membership columns")
        used.update(names)
        values = _sequence(values)
        items += len(values)
        if items > max_items:
            raise ValueError("metric key membership item cap exceeded")
        size += 2  # the sequence's parentheses, including an empty sequence
        unique = {}
        for index, value in enumerate(values):
            value = _member(table, columns, value)
            size += (2 if index else 0) + len(
                _literal(value, max_bytes).encode("utf-8")
            )
            if size > max_bytes:
                raise ValueError("metric key membership byte cap exceeded")
            unique[value] = None
        if size > max_bytes:
            raise ValueError("metric key membership byte cap exceeded")
        result[columns] = tuple(unique)
    return result


def key_membership_sql(table, key_in, *, param_prefix="pub"):
    """Return only validated, bound, positive immutable-key memberships."""
    validate_prefix(param_prefix)
    normalized = normalize_key_in(table, key_in)
    predicates, params = [], {}
    for index, (columns, values) in enumerate(normalized.items()):
        if not values:
            predicates.append("0")
            continue
        name = f"{param_prefix}_kin_{index}"
        expression = columns if isinstance(columns, str) else f"({', '.join(columns)})"
        predicates.append(f"{expression} IN %({name})s")
        params[name] = values
    return " AND ".join(predicates) or "1", params


def iter_key_membership_batches(
    table,
    *,
    fixed,
    columns,
    values,
    max_items=MAX_KEY_IN_ITEMS,
    max_bytes=MAX_KEY_IN_BYTES,
):
    """Partition a caller-owned finite sequence into disjoint positive queries.

    The input must be canonical, unique and deterministic. We reject duplicates
    (including values normalizing to the same UUID), rather than let consumers
    double-count a logical key across pages. The seen set contains only the keys
    already owned by the caller, not rows or revisions from the physical store.
    This is not a NOT IN batching API: unioning complements is incorrect.
    """
    table = _table(table)
    values = _sequence(values)
    fixed = normalize_key_in(table, fixed, max_items=max_items, max_bytes=max_bytes)
    if any(len(members) != 1 for members in fixed.values()):
        raise ValueError("fixed metric memberships must be singletons")
    normalize_key_in(
        table, {**fixed, columns: ()}, max_items=max_items, max_bytes=max_bytes
    )
    if columns in fixed:
        raise ValueError("overlapping metric key membership columns")
    # Reserve the actual fixed binding representation and varying parentheses.
    size = (
        sum(
            len(_literal(members, max_bytes).encode("utf-8"))
            for members in fixed.values()
        )
        + 2
    )
    item_capacity = max_items - len(fixed)
    if item_capacity < 1 or size > max_bytes:
        raise ValueError("metric membership budget leaves no varying key capacity")
    base_size, page, seen = size, [], set()
    for value in values:
        value = _member(table, columns, value)
        if value in seen:
            raise ValueError("metric membership batches require distinct keys")
        seen.add(value)
        length = len(_literal(value, max_bytes).encode("utf-8"))
        if base_size + length > max_bytes:
            raise ValueError("metric key membership byte cap exceeded")
        if page and (len(page) == item_capacity or size + 2 + length > max_bytes):
            yield {**fixed, columns: tuple(page)}
            page, size = [], base_size
        size += length + (2 if page else 0)
        page.append(value)
    if page:
        yield {**fixed, columns: tuple(page)}


def committed_branches(
    table,
    manifest,
    *,
    org_id,
    project_id,
    overlay=None,
    key_after=None,
    key_in=None,
    param_prefix="pub",
    columns=None,
):
    """Shared exact physical branches for payload collapse and key nomination."""
    # Lazy imports keep the typed membership helpers independent of ORM setup.
    from tracer.models.derived_metric import MetricStateConflict
    from tracer.services.derived_metrics.changes import keyset_after
    from tracer.services.derived_metrics.publication import physical_table

    table = _table(table)
    prefix = validate_prefix(param_prefix)
    names = tuple(table.columns if columns is None else columns)
    if not names or set(names) - set(table.columns):
        raise ValueError("invalid committed metric projection")
    projection = ", ".join(names)
    params = {
        f"{prefix}_org": str(UUID(str(org_id))),
        f"{prefix}_project": str(UUID(str(project_id))),
    }
    scope = f"org_id = toUUID(%({prefix}_org)s) AND project_id = toUUID(%({prefix}_project)s)"
    if key_after:
        if not isinstance(key_after, Mapping) or set(key_after) - set(
            table.logical_key
        ):
            raise ValueError(
                "only immutable logical keys can be pushed before collapse"
            )
        keys = tuple(c for c in table.logical_key if c in key_after)
        predicate, bound = keyset_after(
            keys, tuple(key_after[c] for c in keys), f"{prefix}_after"
        )
        scope += f" AND ({predicate})"
        params.update(bound)
    predicate, bound = key_membership_sql(table, key_in, param_prefix=prefix)
    if predicate != "1":
        scope += f" AND ({predicate})"
    params.update(bound)
    branches = []
    if manifest is not None:
        if str(manifest.project_id) != str(project_id):
            raise MetricStateConflict("manifest project mismatch")
        pairs = manifest.allowed_attempts()
        checkpoint = manifest.checkpoint_attempt_id
        for is_checkpoint in (True, False):
            selected = [
                (g, UUID(str(a)))
                for g, a in pairs
                if (a == checkpoint) == is_checkpoint
            ]
            if not selected:
                continue
            name = f"{prefix}_base" if is_checkpoint else f"{prefix}_deltas"
            params[name] = tuple(selected)
            physical = physical_table(table, checkpoint=is_checkpoint)
            branches.append(
                f"SELECT {projection} FROM {physical.name} PREWHERE {scope} WHERE (generation, attempt_id) IN %({name})s"
            )
    if overlay is not None:
        params[f"{prefix}_overlay"] = UUID(str(overlay.id))
        params[f"{prefix}_overlay_generation"] = overlay.generation
        physical = physical_table(table, checkpoint=overlay.purpose == "compaction")
        branches.append(
            f"SELECT {projection} FROM {physical.name} PREWHERE {scope} WHERE attempt_id = %({prefix}_overlay)s AND generation = %({prefix}_overlay_generation)s"
        )
    return branches, params


def committed_key_pages(
    store,
    table,
    manifest,
    *,
    org_id,
    project_id,
    target_rows,
    key_in=None,
    overlay=None,
    param_prefix="nominate",
):
    """Nominate ordered finite full-key pages WITHOUT collapsing broad payloads.

    Each branch contributes its first C distinct keys. ClickHouse (not Python's
    UUID/string ordering) merges those prefixes in the physical key types. Raw
    duplicate revisions can be skipped here ONLY because the payload reader
    subsequently loads every allowed revision of each nominated full key.

    Empty transformed payloads must not end this iterator. Advance using its
    nominated keys, never the last surviving row. target_rows is supplied by
    the caller's measured budget; this helper does not select a production size.
    """
    from tracer.services.derived_metrics.publication import MAX_BATCH_ROWS

    table = _table(table)
    validate_prefix(param_prefix)
    if type(target_rows) is not int or not 1 <= target_rows <= MAX_BATCH_ROWS:
        raise ValueError("invalid metric nomination page size")
    key_in = normalize_key_in(table, key_in)
    keys = tuple(c for c in table.logical_key if c not in {"org_id", "project_id"})
    key_sql = ", ".join(keys)
    structure = ", ".join(f"{c} {dict(table.schema)[c]}" for c in keys)
    cursor = None
    while True:
        branches, _ = committed_branches(
            table,
            manifest,
            org_id=org_id,
            project_id=project_id,
            overlay=overlay,
            key_after=cursor,
            key_in=key_in,
            param_prefix=param_prefix,
            columns=keys,
        )
        literals, merge_size = [], 0
        for branch_index in range(len(branches)):
            after, candidates = cursor, 0
            while candidates < target_rows:
                queries, bound = committed_branches(
                    table,
                    manifest,
                    org_id=org_id,
                    project_id=project_id,
                    overlay=overlay,
                    key_after=after,
                    key_in=key_in,
                    param_prefix=param_prefix,
                    columns=keys,
                )
                remaining = target_rows - candidates
                rows = store.execute(
                    f"{queries[branch_index]} ORDER BY {key_sql} LIMIT %({param_prefix}_limit)s",
                    {**bound, f"{param_prefix}_limit": remaining},
                )
                if not rows:
                    break
                previous = None
                for row in rows:
                    row = _member(table, keys, tuple(row))
                    if row == previous:
                        continue
                    previous = row
                    literal = _literal(row, MAX_KEY_MERGE_BYTES)
                    merge_size += len(literal.encode("utf-8")) + (2 if literals else 0)
                    if merge_size > MAX_KEY_MERGE_BYTES:
                        raise ValueError(
                            "metric key merge byte cap exceeded; reduce nomination size"
                        )
                    literals.append(literal)
                    candidates += 1
                after = dict(zip(keys, rows[-1], strict=True))
                if len(rows) < remaining:
                    break
        if not literals:
            return
        nominated = store.execute(
            f"SELECT DISTINCT {key_sql} FROM format(Values, %({param_prefix}_structure)s, %({param_prefix}_values)s) ORDER BY {key_sql} LIMIT %({param_prefix}_limit)s",
            {
                f"{param_prefix}_structure": structure,
                f"{param_prefix}_values": ", ".join(literals),
                f"{param_prefix}_limit": target_rows,
            },
        )
        if not nominated:
            raise ValueError("metric key merge lost nominated keys")
        for membership in iter_key_membership_batches(
            table, fixed={}, columns=keys, values=nominated
        ):
            yield membership[keys]
        cursor = dict(zip(keys, nominated[-1], strict=True))
