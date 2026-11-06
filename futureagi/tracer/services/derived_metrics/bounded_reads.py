"""Immutable-key restrictions and finite nomination for manifest readers.

Only logical-key predicates commute with replacement collapse. These helpers
never accept caller SQL, mutable ownership, status, deletion or field timestamps.
A nominated key page is NOT a final-row page: every allowed revision of each key
must still enter the subsequent committed collapse, including tombstones.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

from clickhouse_driver.util.escape import escape_param

from tracer.services.derived_metrics.storage import (
    FIELD_CHECKPOINT,
    FIELD_CHECKPOINT_BUCKETS,
    FIELD_VALUES,
    TABLES,
)

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


@dataclass(frozen=True)
class KeyRelation:
    """Compiler-owned positive membership; large candidates stay on the shard.

    Columns must be immutable logical keys. The relation nominates identities,
    never revisions: every permitted revision of each identity still participates
    in the consumer's collapse. SQL is internal, not a public filter API.
    """

    columns: tuple[str, ...]
    sql: str
    params: dict

    def predicate(self, table):
        names = _columns(_table(table), self.columns)
        return f"({', '.join(names)}) IN ({self.sql})"


@dataclass(frozen=True)
class KeyRange:
    """Inclusive upper / exclusive lower bounds in native immutable key order."""

    columns: tuple[str, ...]
    lower: tuple | None
    upper: tuple
    prefix: str = "checkpoint_range"

    def predicate(self, table):
        names = _columns(_table(table), self.columns)
        validate_prefix(self.prefix)
        _member(table, names, self.upper)
        from tracer.services.derived_metrics.changes import keyset_after

        upper, _ = keyset_after(names, self.upper, self.prefix + "_upper")
        clauses = [f"NOT ({upper})"]
        if self.lower is not None:
            _member(table, names, self.lower)
            lower, _ = keyset_after(names, self.lower, self.prefix + "_lower")
            clauses.append(f"({lower})")
        return " AND ".join(clauses)

    @property
    def params(self):
        from tracer.services.derived_metrics.changes import keyset_after

        _, upper = keyset_after(self.columns, self.upper, self.prefix + "_upper")
        if self.lower is None:
            return upper
        _, lower = keyset_after(self.columns, self.lower, self.prefix + "_lower")
        return merge_bindings(upper, lower)


def windowed_rows_select(
    table,
    manifest,
    *,
    org_id,
    project_id,
    predicate,
    params,
    key_in=None,
    candidate_columns=None,
    frozen_overlay=None,
    param_prefix="window",
):
    """Nominate from RAW history, collapse complete keys, then apply the window.

    Mutable predicates may nominate a superset of identities, but MUST NOT select
    a winning revision. In particular an old row in X with a winner in Y belongs
    to X's candidates and is removed only by the final post-collapse predicate.
    Checkpoint, delta and frozen overlay histories all nominate and all receive
    the same correlated membership. Candidates never become Python literals.
    """
    table = _table(table)
    branches, bound = committed_branches(
        table,
        manifest,
        org_id=org_id,
        project_id=project_id,
        key_in=key_in,
        param_prefix=param_prefix,
    )
    bound = merge_bindings(bound, params)
    ctes = []
    if frozen_overlay is not None:
        overlay, overlay_params = frozen_overlay
        fixed, fixed_params = key_membership_sql(
            table, key_in, param_prefix=param_prefix
        )
        # Render private RowBinary bindings only once, not once per occurrence
        # of C in each branch (which could exceed max_query_size for a legal page).
        ctes.append(f"{param_prefix}_frozen AS ({overlay})")
        branches.append(f"SELECT * FROM {param_prefix}_frozen WHERE {fixed}")
        bound = merge_bindings(bound, overlay_params, fixed_params)
    if not branches:
        branches = [f"SELECT * FROM {table.name} WHERE 0"]
    keys = _columns(
        table,
        candidate_columns
        or tuple(c for c in table.logical_key if c not in {"org_id", "project_id"}),
    )
    candidates = " UNION ALL ".join(
        f"SELECT {', '.join(keys)} FROM ({branch}) WHERE {predicate}"
        for branch in branches
    )
    ctes.append(
        f"{param_prefix}_candidates AS (SELECT DISTINCT {', '.join(keys)} FROM ({candidates}))"
    )
    relation = KeyRelation(
        keys, f"SELECT {', '.join(keys)} FROM {param_prefix}_candidates", {}
    )
    # Each branch here is RAW: the only LIMIT BY is below these memberships.
    selected = " UNION ALL ".join(
        f"SELECT * FROM ({branch}) WHERE {relation.predicate(table)}"
        for branch in branches
    )
    return (
        f"WITH {', '.join(ctes)} SELECT * FROM (SELECT * FROM ({selected}) "
        f"ORDER BY generation DESC, {table.version_column} DESC "
        f"LIMIT 1 BY {', '.join(table.logical_key)}) WHERE {predicate}",
        bound,
    )


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


# A source result is NOT a stageable field batch: no generation, sequence or
# publication metadata exists until every original-page admission check passes.
SOURCE_SCHEMA = tuple(
    (name, kind)
    for name, kind in FIELD_VALUES.schema
    if name
    not in {
        "org_id",
        "project_id",
        "workspace_id",
        "field_id",
        "field_version",
        "generation",
        "attempt_id",
        "fencing_epoch",
        "row_sequence",
        "_version",
    }
) + (("source_rows", "UInt64"),)
SOURCE_PAGE_BYTES = 8 * 1024 * 1024
SOURCE_CAPTURE_BYTES = 16 * 1024 * 1024
SOURCE_FIELD_KEY = ("field_id", "field_version", "level", "unit_id")


@dataclass(frozen=True, repr=False)
class SourceOutputPage:
    """Private typed rows and their exact emitted keys, including tombstones."""

    field_id: UUID
    field_version: int
    records: tuple[tuple[str, str, str], ...]

    @property
    def unit_ids(self):
        return tuple(dict.fromkeys(row[1] for row in self.records))

    @property
    def field_keys(self):
        return tuple(
            dict.fromkeys(
                (self.field_id, self.field_version, level, unit)
                for level, unit, _ in self.records
            )
        )

    def select(self, *, prefix="captured_source"):
        validate_prefix(prefix)
        return (
            f"SELECT * FROM format(RowBinary, %({prefix}_schema)s, unhex(%({prefix}_rows)s))",
            {
                f"{prefix}_schema": ", ".join(f"{n} {t}" for n, t in SOURCE_SCHEMA),
                f"{prefix}_rows": "".join(row[2] for row in self.records),
            },
        )


@dataclass(frozen=True, repr=False)
class CapturedSourceOutput(SourceOutputPage):
    """One successfully exhausted source execution, never a second remap lookup.

    This deliberately finite handle is scoped to ONE <=500-candidate execution,
    not the whole refresh or a durable checkpoint. Total binary capture is capped
    at 16 MiB (32 MiB hex); replay pages are <=8 MiB, except an indivisible
    <=16 MiB key group. Identical repeated observations retain their multiplicity.
    Whole-attempt frozen handles/private indexes are a separate protocol.
    """

    digest: str

    def pages(self, *, max_items=500, max_bytes=MAX_KEY_IN_BYTES):
        # Full field tuples reserve more bytes than any of the downstream scalar
        # field/version/unit or level/unit memberships. Independently validate
        # those at their committed reader as well.
        from tracer.services.derived_metrics.publication import MAX_BATCH_ROWS

        groups = {}
        for record in self.records:
            groups.setdefault(record[:2], []).append(record)
        for membership in iter_key_membership_batches(
            FIELD_VALUES,
            fixed={},
            columns=SOURCE_FIELD_KEY,
            values=self.field_keys,
            max_items=max_items,
            max_bytes=max_bytes,
        ):
            page, size = [], 0
            for _field, _version, level, unit in membership[SOURCE_FIELD_KEY]:
                # A remap chain can drive the same canonical ID and retired raw
                # ID. Keep identical observations together, with multiplicity;
                # field-winner/lifecycle reduction is deliberately NOT done here.
                group = groups[level, unit]
                length = sum(len(r[2]) // 2 for r in group)
                if len(group) > MAX_BATCH_ROWS or length > SOURCE_CAPTURE_BYTES:
                    raise ValueError("metric source key group cap exceeded")
                if page and (
                    len(page) + len(group) > MAX_BATCH_ROWS
                    or size + length > SOURCE_PAGE_BYTES
                ):
                    yield SourceOutputPage(
                        self.field_id, self.field_version, tuple(page)
                    )
                    page, size = [], 0
                page.extend(group)
                size += length
            if page:
                yield SourceOutputPage(self.field_id, self.field_version, tuple(page))


def capture_source_output(
    store, sql, params, *, field_id, field_version, candidate_count
):
    """Capture canonical EMITTED output once, under count and transport-byte caps.

    The source builder owns remapping and retired-alias emission. Inferring this
    domain from requested IDs (or resolving the map again) is not equivalent.
    No handle is exposed before stream exhaustion, including final server errors.
    No LIMIT may hide an overflow. A failed capture cannot freeze or publish rows.
    """
    from tracer.services.derived_metrics.publication import (
        MAX_BATCH_ROWS,
        WRITE_SETTINGS,
    )

    if type(candidate_count) is not int or not 1 <= candidate_count <= MAX_BATCH_ROWS:
        raise ValueError("invalid metric source candidate count")
    field_id = _value("UUID", field_id)
    field_version = _value("UInt32", field_version)
    stream = getattr(store.client, "execute_iter", None)
    if stream is None:
        raise ValueError("metric source capture requires a streaming local client")
    columns = ", ".join(
        "CAST(" + name + ", '" + kind.replace("'", "\\'") + "')"
        for name, kind in SOURCE_SCHEMA
    )
    # Check each row before hex expansion and bound the whole server result.
    # max_block_size also controls upstream source scans: setting it to one
    # makes a broad revision-complete scan execute millions of tiny blocks.
    # Keep the normal writer block size, result overflow=throw, and consume one
    # output row at a time. No partial capture is exposed on a final server error.
    query = (
        f"SELECT level, unit_id, hex(encoded) FROM (SELECT level, unit_id, "
        f"formatRow('RowBinary', {columns}) AS encoded FROM ({sql})) "
        f"WHERE throwIf(length(encoded) > {SOURCE_CAPTURE_BYTES}, 'metric source row byte cap exceeded') = 0"
    )
    settings = {
        **WRITE_SETTINGS,
        "max_block_size": MAX_BATCH_ROWS,
        "preferred_block_size_bytes": 64 * 1024,
        "max_result_rows": 2 * candidate_count,
        "max_result_bytes": 2 * SOURCE_CAPTURE_BYTES + 1024 * 1024,
        "result_overflow_mode": "throw",
    }
    rows, seen, size = [], {}, 0
    iterator = stream(
        query,
        params,
        settings=settings,
        chunk_size=1,
        query_id="metric-source-capture-" + uuid4().hex,
    )
    try:
        for level, unit, encoded in iterator:
            if len(rows) >= 2 * candidate_count:
                raise ValueError("metric source output count cap exceeded")
            if level != "conversation" or not isinstance(unit, str):
                raise ValueError("invalid metric source output key")
            if (level, unit) in seen and seen[level, unit] != encoded:
                raise ValueError("conflicting metric source output key")
            normalize_key_in(
                FIELD_VALUES,
                {
                    SOURCE_FIELD_KEY: ((field_id, field_version, level, unit),),
                },
            )
            if not isinstance(encoded, str) or len(encoded) % 2:
                raise ValueError("invalid metric source encoding")
            size += len(encoded) // 2
            if size > SOURCE_CAPTURE_BYTES:
                raise ValueError("metric source capture byte cap exceeded")
            try:
                bytes.fromhex(encoded)
            except ValueError:
                raise ValueError("invalid metric source encoding") from None
            seen.setdefault((level, unit), encoded)
            rows.append((level, unit, seen[level, unit]))
    except BaseException:
        # A local validation error must not leave unread packets on this shard's
        # connection. No dependent query may observe a partial source handle.
        store.client.disconnect()
        raise
    finally:
        close = getattr(iterator, "close", None)
        if close:
            close()
    rows.sort(key=lambda row: (row[0], row[1]))
    digest = sha256()
    for _level, _unit, encoded in rows:
        digest.update(bytes.fromhex(encoded))
    return CapturedSourceOutput(
        field_id, field_version, tuple(rows), digest.hexdigest()
    )


def checkpoint_unit_predicate(table, key_in, *, prefix, bucket=None):
    """Compiler-derived partition pruning; never hashes a mutable owner/time.

    Keep the original exact key membership too. A hash collision is a pruning
    false positive, not authority to read another unit, attempt or tenant. Hash
    on the server in the physical String type, not Python's process-local hash.
    """
    validate_prefix(prefix)
    if bucket is not None and (
        type(bucket) is not int or not 0 <= bucket < FIELD_CHECKPOINT_BUCKETS
    ):
        raise ValueError("invalid checkpoint unit bucket")
    if table != FIELD_CHECKPOINT:
        return "", {}
    for columns, values in normalize_key_in(table, key_in).items():
        names = (columns,) if isinstance(columns, str) else columns
        if "unit_id" not in names:
            continue
        if not values:
            return " AND 0", {}
        units = (
            values
            if isinstance(columns, str)
            else tuple(value[names.index("unit_id")] for value in values)
        )
        name = f"{prefix}_unit_partitions"
        if bucket is not None:
            # A single global IN set makes every partition seek every requested
            # id, including ids that cannot live there. Correlate the exact unit
            # set with a constant physical bucket before primary-key pruning.
            # Keep hashing in CH: its String hash is the DDL's routing contract.
            return (
                f" AND cityHash64(unit_id) %% {FIELD_CHECKPOINT_BUCKETS} = {bucket}"
                f" AND unit_id IN (SELECT unit FROM (SELECT arrayJoin(%({name})s) AS unit) "
                f"WHERE cityHash64(unit) %% {FIELD_CHECKPOINT_BUCKETS} = {bucket})",
                {name: list(dict.fromkeys(units))},
            )
        return (
            f" AND cityHash64(unit_id) %% {FIELD_CHECKPOINT_BUCKETS} IN "
            f"(SELECT cityHash64(arrayJoin(%({name})s)) %% {FIELD_CHECKPOINT_BUCKETS})",
            {name: list(dict.fromkeys(units))},
        )
    return "", {}


def committed_branches(
    table,
    manifest,
    *,
    org_id,
    project_id,
    overlay=None,
    key_after=None,
    key_in=None,
    key_relation=None,
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
    if key_relation is not None:
        if not isinstance(key_relation, KeyRelation | KeyRange):
            raise ValueError("metric membership requires a compiler-owned relation")
        scope += f" AND ({key_relation.predicate(table)})"
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
            pruning, bound = checkpoint_unit_predicate(physical, key_in, prefix=prefix)
            params = merge_bindings(params, bound)
            branches.append(
                f"SELECT {projection} FROM {physical.name} PREWHERE {scope}{pruning} WHERE (generation, attempt_id) IN %({name})s"
            )
    if overlay is not None:
        params[f"{prefix}_overlay"] = UUID(str(overlay.id))
        params[f"{prefix}_overlay_generation"] = overlay.generation
        physical = physical_table(table, checkpoint=overlay.purpose == "compaction")
        pruning, bound = checkpoint_unit_predicate(physical, key_in, prefix=prefix)
        params = merge_bindings(params, bound)
        branches.append(
            f"SELECT {projection} FROM {physical.name} PREWHERE {scope}{pruning} WHERE attempt_id = %({prefix}_overlay)s AND generation = %({prefix}_overlay_generation)s"
        )
    return branches, merge_bindings(
        params, key_relation.params
    ) if key_relation else params


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


def committed_key_ranges(store, table, manifest, *, org_id, project_id, target_rows):
    """Nominate bounded ranges without round-tripping a large IN list.

    Only the last native-order key crosses the wire. Raw ordered prefixes are
    limited before deduplication so a broad DISTINCT never holds every key. The
    subsequent payload collapse includes *all* allowed revisions in the range;
    post-collapse filtering may empty a range without stopping this iterator.
    """
    from tracer.services.derived_metrics.publication import MAX_COMPACTION_ROWS

    if type(target_rows) is not int or not 1 <= target_rows <= MAX_COMPACTION_ROWS:
        raise ValueError("invalid metric checkpoint range size")
    table = _table(table)
    keys = tuple(c for c in table.logical_key if c not in {"org_id", "project_id"})
    names = ", ".join(keys)
    cursor = None
    while True:
        branches, params = committed_branches(
            table,
            manifest,
            org_id=org_id,
            project_id=project_id,
            key_after=dict(zip(keys, cursor, strict=True))
            if cursor is not None
            else None,
            param_prefix="checkpoint",
            columns=keys,
        )
        if not branches:
            return
        prefixes = " UNION ALL ".join(
            f"SELECT * FROM (SELECT {names} FROM ({branch}) ORDER BY {names} LIMIT %(checkpoint_limit)s)"
            for branch in branches
        )
        rows = store.execute(
            f"SELECT tuple({names}) FROM (SELECT DISTINCT {names} FROM ({prefixes}) "
            f"ORDER BY {names} LIMIT %(checkpoint_limit)s) ORDER BY tuple({names}) DESC LIMIT 1",
            {**params, "checkpoint_limit": target_rows},
        )
        if not rows:
            return
        upper = tuple(rows[0][0])
        if upper == cursor:
            raise ValueError("metric checkpoint range did not advance")
        yield KeyRange(keys, cursor, upper)
        cursor = upper
