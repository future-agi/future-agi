"""SELECT-only PostgreSQL source shapes for inspection, never executable DDL.

The injected transport owns the exact PeerTarget-bound connection, privileges to
see every requested column/PK, a repeatable/exclusive schema window and deadlines.
current_database() verifies the database, not the server endpoint. No connection,
transaction, migration, ledger or ClickHouse operation is performed here.

Only simulate_agent_definition.languages permits nullable varchar[]: the qualified
transport collapses whole SQL NULL to []. This is a named limitation, not full
source fidelity; NULL elements/multidimensional arrays and row parity are unproven.
Scalar nullability is never relaxed. Returned types contain no CH default clauses.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from tracer.services.clickhouse.oss_cdc_inventory import PeerTarget

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
_DECIMAL = re.compile(r"Decimal\(([1-9][0-9]?),([0-9]|[1-9][0-9])\)\Z")
_UDT_TYPES = {
    "varchar": "String",
    "text": "String",
    "jsonb": "String",
    "bool": "Bool",
    "int4": "Int32",
    "int8": "Int64",
    "float8": "Float64",
    "uuid": "UUID",
    "timestamptz": "DateTime64(6)",
    "_varchar": "Array(String)",
}
_SCALAR_TYPES = frozenset(_UDT_TYPES.values()) - {"Array(String)"}
_USAGE_DERIVED = frozenset(
    {"eval_score", "eval_output_str", "eval_trace_id", "eval_dataset_id"}
)

_COLUMNS_SQL = """
SELECT table_catalog, table_schema, table_name, column_name, ordinal_position,
       udt_schema, udt_name, is_nullable, numeric_precision, numeric_scale,
       domain_name, is_generated,
       count(*) OVER (PARTITION BY table_catalog, table_schema, table_name)
FROM information_schema.columns
WHERE table_catalog = %(database)s AND table_schema = 'public'
  AND table_name = ANY(%(tables)s)
ORDER BY table_name, ordinal_position
"""
_PK_SQL = """
SELECT current_database(), n.nspname, c.relname, a.attname, k.ordinality,
       cardinality(p.conkey), a.attnotnull
FROM pg_catalog.pg_constraint AS p
JOIN pg_catalog.pg_class AS c ON c.oid = p.conrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY AS k(attnum, ordinality)
JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid AND a.attnum = k.attnum
WHERE p.contype = 'p' AND n.nspname = 'public' AND c.relkind IN ('r', 'p')
  AND NOT a.attisdropped AND current_database() = %(database)s
  AND c.relname = ANY(%(tables)s)
ORDER BY c.relname, k.ordinality
"""


class SourceError(ValueError):
    """Incomplete or unsupported source metadata; no source profile produced."""


def _identifier(value) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _scalar_type(value: str) -> bool:
    if value in _SCALAR_TYPES:
        return True
    match = _DECIMAL.fullmatch(value)
    if not match:
        return False
    precision, scale = map(int, match.groups())
    return 1 <= precision <= 76 and 0 <= scale <= min(38, precision)


def _column_type(value) -> bool:
    if not isinstance(value, str):
        return False
    if value == "Array(String)":
        return True
    if value.startswith("Nullable(") and value.endswith(")"):
        value = value[9:-1]
    return _scalar_type(value)


@dataclass(frozen=True)
class SourceTable:
    columns: tuple[tuple[str, str], ...]
    primary_key: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.columns, tuple) or not self.columns:
            raise SourceError("nonempty source columns tuple required")
        seen = {}
        for column in self.columns:
            if (
                not isinstance(column, tuple)
                or len(column) != 2
                or not _identifier(column[0])
                or column[0].lower().startswith("_peerdb_")
                or column[0] in seen
                or not _column_type(column[1])
            ):
                raise SourceError("invalid, reserved or duplicate source column")
            seen[column[0]] = column[1]
        if not isinstance(self.primary_key, tuple) or not self.primary_key:
            raise SourceError("nonempty source primary key tuple required")
        keys = set()
        for name in self.primary_key:
            if (
                not _identifier(name)
                or name not in seen
                or name in keys
                or seen[name].startswith("Nullable(")
            ):
                raise SourceError("invalid or nullable source primary key")
            keys.add(name)


@dataclass(frozen=True)
class SourceInventory:
    source: PeerTarget
    tables: dict[str, SourceTable]

    def __post_init__(self):
        if not isinstance(self.source, PeerTarget):
            raise SourceError("validated source peer required")
        if not isinstance(self.tables, dict) or not self.tables:
            raise SourceError("nonempty source tables dictionary required")
        for name, table in self.tables.items():
            if not _identifier(name) or not isinstance(table, SourceTable):
                raise SourceError("invalid source table")
            if name == "usage_apicalllog" and any(
                column.lower() in _USAGE_DERIVED for column, _ in table.columns
            ):
                raise SourceError("source column collides with derived usage field")
        # Do not retain the caller's mutable dictionary as our inventory.
        object.__setattr__(self, "tables", dict(self.tables))


def _read(query, sql, parameters, width):
    try:
        rows = query(sql, parameters)
    except Exception:
        raise SourceError("PostgreSQL source inspection failed") from None
    if not isinstance(rows, Sequence) or isinstance(rows, str | bytes):
        raise SourceError("invalid source metadata response")
    if any(
        not isinstance(row, Sequence)
        or isinstance(row, str | bytes)
        or len(row) != width
        for row in rows
    ):
        raise SourceError("invalid source metadata row")
    return rows


def _ordered(rows, position_index, count_index, *, contiguous):
    if not rows or any(
        type(row[position_index]) is not int
        or row[position_index] <= 0
        or type(row[count_index]) is not int
        or row[count_index] != len(rows)
        for row in rows
    ):
        raise SourceError("incomplete source metadata")
    rows = sorted(rows, key=lambda row: row[position_index])
    positions = [row[position_index] for row in rows]
    if len(set(positions)) != len(rows) or (
        contiguous and positions != list(range(1, len(rows) + 1))
    ):
        raise SourceError("invalid or duplicate source positions")
    return rows


def _mapped_type(row):
    (
        _,
        _,
        table,
        name,
        _,
        udt_schema,
        udt,
        nullable,
        precision,
        scale,
        domain,
        generated,
        _,
    ) = row
    if (
        udt_schema != "pg_catalog"
        or not isinstance(udt, str)
        or domain is not None
        or generated != "NEVER"
        or nullable not in ("YES", "NO")
    ):
        raise SourceError("unsupported source type, domain, generation or nullability")
    if udt == "numeric":
        if (
            type(precision) is not int
            or type(scale) is not int
            or not 1 <= precision <= 76
            or not 0 <= scale <= min(38, precision)
        ):
            raise SourceError("unsupported source decimal precision or scale")
        result = f"Decimal({precision},{scale})"
    else:
        result = _UDT_TYPES.get(udt)
        if result is None:
            raise SourceError("unsupported source builtin type")
    if nullable == "YES":
        if result == "Array(String)":
            if (table, name) != ("simulate_agent_definition", "languages"):
                raise SourceError("unqualified nullable source array")
        else:
            result = f"Nullable({result})"
    return result


def inspect_source(
    query: Callable[[str, dict], Sequence],
    *,
    source: PeerTarget,
    tables: tuple[str, ...],
) -> SourceInventory:
    """Inspect exact public tables with three fixed, parameterized SELECTs.

    Transport returns sequences of positional rows in SELECT-column order. Counts
    detect truncated column/PK results, including omitted trailing positions.
    The transport must not filter information_schema rows by column privileges.
    Dropped PG columns can leave position gaps; PK ordinality cannot have gaps.
    Composite keys retain declared order and require every PG component NOT NULL.
    """
    if not isinstance(source, PeerTarget) or not callable(query):
        raise SourceError("validated source peer and query transport required")
    if (
        not isinstance(tables, tuple)
        or not tables
        or any(not _identifier(table) for table in tables)
        or len(set(tables)) != len(tables)
    ):
        raise SourceError("nonempty unique source table identifiers required")
    identity = _read(query, "SELECT current_database()", {}, 1)
    if len(identity) != 1 or identity[0][0] != source.database:
        raise SourceError("source database identity mismatch")
    names = tuple(sorted(tables))
    columns = {name: [] for name in names}
    keys = {name: [] for name in names}
    for sql, width, target in ((_COLUMNS_SQL, 13, columns), (_PK_SQL, 7, keys)):
        for row in _read(
            query, sql, {"database": source.database, "tables": list(names)}, width
        ):
            database, schema, table, column = row[:4]
            if (
                database != source.database
                or schema != "public"
                or not _identifier(table)
                or table not in target
                or not _identifier(column)
            ):
                raise SourceError("unexpected or unsafe source metadata identity")
            target[table].append(row)
    result = {}
    for table in names:
        column_rows = _ordered(columns[table], 4, 12, contiguous=False)
        key_rows = _ordered(keys[table], 4, 5, contiguous=True)
        nullable = {row[3]: row[7] for row in column_rows}
        if any(row[6] is not True or nullable.get(row[3]) != "NO" for row in key_rows):
            raise SourceError("missing or nullable source primary key column")
        result[table] = SourceTable(
            columns=tuple((row[3], _mapped_type(row)) for row in column_rows),
            primary_key=tuple(row[3] for row in key_rows),
        )
    return SourceInventory(source=source, tables=result)
