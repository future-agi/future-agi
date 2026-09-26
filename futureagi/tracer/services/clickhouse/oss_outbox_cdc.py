"""In-app Postgres -> ClickHouse change capture (``FI_CDC_MODE=outbox``).

The standalone install uses this instead of PeerDB; the distributed install keeps
PeerDB. It adds no container or process:

* Capture. Statement-level AFTER triggers on each landing source table append
  ``(table, pk, op)`` rows to ``fi_cdc_outbox`` in the writer's own
  transaction. That covers every write path: ``save()``, ``QuerySet.update()``,
  ``bulk_create``/``bulk_update``, raw SQL, cascades, hard deletes and
  TRUNCATE. ``post_save`` signals and an ``updated_at`` watermark both miss
  most of these.
* Drain. A Temporal activity runs every 10 seconds, the same interval as the
  PeerDB fact-table mirrors. It re-reads the committed row state by primary
  key and inserts it into the landing tables using PeerDB's column contract
  (``_peerdb_version``, ``_peerdb_is_deleted``, ``_peerdb_synced_at``). A
  primary key that no longer exists gets a tombstone row.
* Install. Creates each landing table exactly as PeerDB would
  (``oss_cdc_bootstrap._source_definitions``), then reuses
  ``oss_cdc_bootstrap.bootstrap_cdc`` for the derived usage columns, the 11
  dictionaries/views and the final qualification. Existing rows are copied by
  a resumable snapshot that the drain finishes.
* Reconcile. Keyset-paged id parity between PG and CH, for TRUNCATE, restores,
  writes with triggers bypassed and rows the drain parked.

Read paths are untouched. ``FINAL``, ``LIMIT 1 BY id``,
``argMax(.., _peerdb_version)`` and ``_peerdb_is_deleted`` keep their meaning
because the physical tables are the ones PeerDB would have created.

Correctness rests on three facts:

1. Every writer (drain, snapshot page, reconcile repair, install) holds one
   Postgres advisory lock across "allocate version, read committed state,
   insert into ClickHouse", and syncs its version clock with the persisted
   floor when it takes the lock. Versions therefore increase in the same order
   as the states they describe, across processes and clock steps.
2. The drain is state-based and idempotent. It never replays captured values;
   it only learns which primary keys changed. A replay after a crash
   re-inserts the same state with a higher version, and ReplacingMergeTree
   collapses the duplicates.
3. Outbox rows are deleted by their exact ``seq``, never by watermark. A
   lower ``seq`` that commits late is still picked up by the next batch.

Operational contract: PostgreSQL 11 or newer (transition tables, EXECUTE
FUNCTION). Connect directly, never through a transaction pooler such as
PgBouncer: the lock is a session advisory lock. Capture must never run
without the drain, so
``ensure_installed()`` manages the triggers and the Temporal schedules
together, and removes both when ``FI_CDC_MODE`` is not ``outbox``. To stop
capture by hand run ``uninstall --apply``; do not just disable the triggers
(the drain re-arms them and re-snapshots the table).

Like ``oss_cdc_install``, the CLI needs no Django settings;
``ensure_installed()`` reaches Temporal (which does) only through a lazy
import. The Temporal activities are in ``tracer.tasks.outbox_cdc``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
from clickhouse_connect.driver.exceptions import (
    OperationalError as ClickHouseOperationalError,
)

from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_cdc_upgrade as upgrade
from tracer.services.clickhouse.oss_cdc_source import (
    SourceError,
    SourceInventory,
    SourceTable,
    inspect_source,
)

MODES = ("outbox", "peerdb", "off")
OUTBOX = "fi_cdc_outbox"
STATE = "fi_cdc_state"
DEADLETTER = "fi_cdc_deadletter"
CLOCK_ROW = "__clock__"
# Synthetic inventory identity handed to bootstrap_cdc. There is no PeerDB
# mirror; the name only has to be non-empty and consistent.
MIRROR_NAME = "fi_outbox_cdc"
# One lock serializes drain, snapshot pages, reconcile repairs and install.
LOCK_KEY = 0x0066_6963_6463_0001
# name -> (event, pg_trigger.tgtype of AFTER <event> FOR EACH STATEMENT,
#          transition table, function)
TRIGGERS = {
    "fi_cdc_ins": ("INSERT", 4, "fi_cdc_new", "fi_cdc_capture_new"),
    "fi_cdc_upd": ("UPDATE", 16, "fi_cdc_new", "fi_cdc_capture_new"),
    "fi_cdc_del": ("DELETE", 8, "fi_cdc_old", "fi_cdc_capture_old"),
    "fi_cdc_trunc": ("TRUNCATE", 32, None, "fi_cdc_capture_truncate"),
}


def _int_env(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


DRAIN_BATCH = _int_env("FI_CDC_DRAIN_BATCH", 5000)
SNAPSHOT_PAGE = _int_env("FI_CDC_SNAPSHOT_PAGE", 10000)
APPLY_CHUNK = 5000
# Keys per reconcile page. Pass B sends them to ClickHouse as an IN list, so
# this also bounds the query text (40 bytes per UUID; max_query_size 256 KiB).
RECONCILE_CHUNK = 2000
# Rows are streamed from a server-side cursor and flushed to ClickHouse in
# blocks of about this many bytes, so wide rows cannot balloon worker memory.
FLUSH_BYTES = _int_env("FI_CDC_FLUSH_BYTES", 16 * 1024 * 1024)
FETCH_ROWS = 500
# Full id-parity sweep interval per table; requested sweeps run sooner.
RECONCILE_EVERY_S = 24 * 3600
# Poison rows parked per table per tick before the table is failed for the tick.
MAX_PARKED_PER_TICK = 20
DEADLETTER_RETRIES = 5
DDL_LOCK_TIMEOUT_MS = 2000
DDL_ATTEMPTS = 5
# ``install --check`` fails when the drain is this far behind.
CHECK_MAX_LAG_S = _int_env("FI_CDC_MAX_LAG_SECONDS", 900)
CHECK_MAX_DEPTH = _int_env("FI_CDC_MAX_OUTBOX_DEPTH", 5_000_000)
INSTALL_SNAPSHOT_S = _int_env("FI_CDC_INSTALL_SNAPSHOT_SECONDS", 30)

_PEERDB_COLUMNS = ("_peerdb_is_deleted", "_peerdb_version")
_SYNCED_AT = "_peerdb_synced_at"
# Arrival time written by snapshot, reconcile and install repairs, which are
# not new arrivals. Continuous eval tasks page on ``_peerdb_synced_at``; only
# drain deltas keep the ``now64()`` default, as PeerDB does.
EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
_DERIVED = upgrade.MIGRATIONS["cdc_002_usage_eval_fields.sql"]


class OutboxCDCError(ValueError):
    """Safe-to-display error: no SQL, row data or credentials."""


def cdc_mode(env=None) -> str:
    value = (os.environ if env is None else env).get("FI_CDC_MODE", "peerdb")
    mode = value.strip().lower()
    if mode not in MODES:
        raise OutboxCDCError("FI_CDC_MODE must be one of: outbox, peerdb, off")
    return mode


def _ident(name: str) -> str:
    if not isinstance(name, str) or not name.isascii() or not name.isidentifier():
        raise OutboxCDCError("unsafe identifier")
    return f'"{name}"'


def _quote(name: str) -> str:
    """Quote a catalog-supplied PG identifier."""
    return '"' + name.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# Postgres capture objects (idempotent, lock-light)
# ---------------------------------------------------------------------------

TABLES_DDL: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS public.{OUTBOX} (
        seq         bigserial PRIMARY KEY,
        table_name  text        NOT NULL,
        pk          text        NOT NULL,
        op          "char"      NOT NULL,
        captured_at timestamptz NOT NULL DEFAULT clock_timestamp()
    ) WITH (autovacuum_vacuum_scale_factor = 0, autovacuum_vacuum_threshold = 5000)
    """,
    f"""
    CREATE TABLE IF NOT EXISTS public.{STATE} (
        table_name             text PRIMARY KEY,
        snapshot_cursor        text,
        snapshot_completed_at  timestamptz,
        reconcile_requested_at timestamptz,
        reconciled_at          timestamptz,
        version_floor          numeric(20, 0) NOT NULL DEFAULT 0
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS public.{DEADLETTER} (
        table_name  text        NOT NULL,
        pk          text        NOT NULL,
        error       text        NOT NULL,
        attempts    integer     NOT NULL DEFAULT 1,
        parked_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
        requeued_at timestamptz,
        PRIMARY KEY (table_name, pk)
    )
    """,
)
# Transition-table triggers may have only one event each (PG restriction),
# hence one function for new rows and one for old rows. PL/pgSQL caches plans
# per trigger, so one body serves every table. Compared to pg_proc.prosrc, so
# an unchanged body is never re-issued.
FUNCTIONS = {
    "fi_cdc_capture_new": f"""
BEGIN
    INSERT INTO public.{OUTBOX} (table_name, pk, op)
    SELECT TG_TABLE_NAME, n.id::text, 'U' FROM fi_cdc_new AS n;
    RETURN NULL;
END
""",
    "fi_cdc_capture_old": f"""
BEGIN
    INSERT INTO public.{OUTBOX} (table_name, pk, op)
    SELECT TG_TABLE_NAME, o.id::text, 'D' FROM fi_cdc_old AS o;
    RETURN NULL;
END
""",
    "fi_cdc_capture_truncate": f"""
BEGIN
    INSERT INTO public.{OUTBOX} (table_name, pk, op) VALUES (TG_TABLE_NAME, '', 'T');
    RETURN NULL;
END
""",
}

_TRIGGER_STATE_SQL = """
SELECT c.relname, t.tgname, t.tgenabled, t.tgtype, t.tgnewtable, t.tgoldtable,
       t.tgattr::text, t.tgqual IS NULL, p.proname
FROM pg_catalog.pg_trigger AS t
JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid
WHERE n.nspname = 'public' AND NOT t.tgisinternal
  AND c.relname = ANY(%s) AND t.tgname = ANY(%s)
"""


def create_trigger_sql(table: str, name: str) -> str:
    event, _, transition, function = TRIGGERS[name]
    referencing = (
        f"REFERENCING {'NEW' if transition == 'fi_cdc_new' else 'OLD'} "
        f"TABLE AS {transition} "
        if transition
        else ""
    )
    return (
        f"CREATE TRIGGER {name} AFTER {event} ON public.{_ident(table)} "
        f"{referencing}FOR EACH STATEMENT EXECUTE FUNCTION public.{function}()"
    )


def capture_state(pg, tables: Iterable[str]) -> dict[str, dict[str, str]]:
    """Per table and trigger: ``ok``, ``disabled``, ``wrong`` or ``missing``.

    Compares pg_trigger fields rather than pg_get_triggerdef text, so the
    answer does not depend on the server version's formatting.
    """
    tables = tuple(tables)
    found = {}
    for (
        table,
        name,
        enabled,
        tgtype,
        new_table,
        old_table,
        attributes,
        unqualified,
        function,
    ) in pg.execute(_TRIGGER_STATE_SQL, (list(tables), list(TRIGGERS))).fetchall():
        _, wanted_type, transition, wanted_function = TRIGGERS[name]
        if (
            tgtype != wanted_type
            or function != wanted_function
            or attributes != ""
            or not unqualified
            or new_table != (transition if transition == "fi_cdc_new" else None)
            or old_table != (transition if transition == "fi_cdc_old" else None)
        ):
            found[table, name] = "wrong"
        else:
            # 'O' fires in normal sessions, 'A' always; 'D' and 'R' do not.
            found[table, name] = "ok" if enabled in ("O", "A") else "disabled"
    return {
        table: {name: found.get((table, name), "missing") for name in TRIGGERS}
        for table in tables
    }


def broken_capture(states: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    return {
        table: sorted(name for name, state in triggers.items() if state != "ok")
        for table, triggers in states.items()
        if any(state != "ok" for state in triggers.values())
    }


def _ddl(pg, statements: list[str], *, attempts: int = DDL_ATTEMPTS) -> None:
    """One short transaction that gives up on lock waits instead of queueing.

    A DDL statement waiting behind a long app transaction would otherwise
    block every later writer on that table until it gets its lock.
    """
    for attempt in range(1, attempts + 1):
        try:
            with pg.transaction():
                pg.execute(f"SET LOCAL lock_timeout = '{DDL_LOCK_TIMEOUT_MS}ms'")
                for statement in statements:
                    pg.execute(statement)
            return
        except psycopg.errors.LockNotAvailable:
            if attempt == attempts:
                raise OutboxCDCError(
                    "capture DDL could not get its table lock; a long transaction "
                    "holds it. Retry later."
                ) from None
            time.sleep(min(0.5 * 2**attempt, 5.0))


def arm_triggers(
    pg, table: str, triggers: dict[str, str], *, attempts: int = DDL_ATTEMPTS
) -> None:
    """Create missing, replace wrong and enable disabled triggers; skip ok ones."""
    statements = []
    for name, state in triggers.items():
        if state == "disabled":
            statements.append(
                f"ALTER TABLE public.{_ident(table)} ENABLE TRIGGER {name}"
            )
        elif state != "ok":
            if state == "wrong":
                statements.append(f"DROP TRIGGER {name} ON public.{_ident(table)}")
            statements.append(create_trigger_sql(table, name))
    if statements:
        _ddl(pg, statements, attempts=attempts)


def install_capture(pg, tables: Iterable[str], *, attempts: int = DDL_ATTEMPTS) -> dict:
    """Idempotent. A routine restart issues no DDL against the app tables."""
    tables = tuple(tables)
    for statement in TABLES_DDL:
        pg.execute(statement)
    current = dict(
        pg.execute(
            """
            SELECT p.proname, p.prosrc FROM pg_catalog.pg_proc AS p
            JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname = ANY(%s)
            """,
            (list(FUNCTIONS),),
        ).fetchall()
    )
    for name, body in FUNCTIONS.items():
        if current.get(name) != body:
            pg.execute(
                f"CREATE OR REPLACE FUNCTION public.{name}() RETURNS trigger "
                f"LANGUAGE plpgsql AS $fi_cdc${body}$fi_cdc$"
            )
    armed = broken_capture(capture_state(pg, tables))
    busy = []
    for table in armed:
        try:
            arm_triggers(
                pg, table, capture_state(pg, (table,))[table], attempts=attempts
            )
        except OutboxCDCError:
            busy.append(table)  # arm the others first; the caller retries
    if busy:
        raise OutboxCDCError(
            f"capture DDL could not lock {busy}; a long transaction holds them. "
            "Retry later."
        )
    new_state = [
        row[0]
        for row in pg.execute(
            f"INSERT INTO {STATE} (table_name) SELECT unnest(%s::text[]) "
            "ON CONFLICT DO NOTHING RETURNING table_name",
            ([*tables, CLOCK_ROW],),
        ).fetchall()
    ]
    return {"armed": armed, "new_state": sorted(set(new_state) - {CLOCK_ROW})}


def capture_installed(pg) -> bool:
    return bool(
        _outbox_exists(pg)
        or pg.execute(
            """
            SELECT 1 FROM pg_catalog.pg_trigger AS t
            JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid
            WHERE NOT t.tgisinternal AND (t.tgname = ANY(%s) OR p.proname = ANY(%s))
            LIMIT 1
            """,
            (list(TRIGGERS), list(FUNCTIONS)),
        ).fetchall()
    )


def uninstall_capture(pg, *, attempts: int = DDL_ATTEMPTS) -> list[str]:
    """Drop every capture trigger, found by name or function on any table.

    Triggers go first: a trigger left behind after its outbox is dropped
    would fail every write to its table.
    """
    rows = pg.execute(
        """
        SELECT n.nspname, c.relname, t.tgname
        FROM pg_catalog.pg_trigger AS t
        JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid
        WHERE NOT t.tgisinternal AND (t.tgname = ANY(%s) OR p.proname = ANY(%s))
        ORDER BY 1, 2, 3
        """,
        (list(TRIGGERS), list(FUNCTIONS)),
    ).fetchall()
    by_table = defaultdict(list)
    for schema, table, name in rows:
        by_table[schema, table].append(name)
    for (schema, table), names in by_table.items():
        _ddl(
            pg,
            [
                f"DROP TRIGGER IF EXISTS {_quote(name)} ON {_quote(schema)}.{_quote(table)}"
                for name in names
            ],
            attempts=attempts,
        )
    for name in FUNCTIONS:
        pg.execute(f"DROP FUNCTION IF EXISTS public.{name}()")
    for table in (OUTBOX, STATE, DEADLETTER):
        pg.execute(f"DROP TABLE IF EXISTS public.{table}")
    return [
        f"{table}.{name}" for (_, table), names in by_table.items() for name in names
    ]


# ---------------------------------------------------------------------------
# Versions and the writer lock
# ---------------------------------------------------------------------------


class VersionClock:
    """Strictly increasing UInt64 versions: nanoseconds since the epoch, floored.

    The floor is the highest version any earlier writer used, including
    PeerDB-era rows when an install takes over from PeerDB. A later state
    therefore always outranks an earlier one, even across a backwards clock
    step or a replaced version scheme.
    """

    def __init__(self, floor: int = 0, now: Callable[[], int] = time.time_ns):
        self.floor = self.last = int(floor)
        self._now = now

    def next(self) -> int:
        self.last = max(self._now(), self.last + 1)
        return self.last


def load_floor(pg) -> int:
    row = pg.execute(f"SELECT coalesce(max(version_floor), 0) FROM {STATE}").fetchone()
    return int(row[0])


def store_floor(pg, value: int) -> None:
    pg.execute(
        f"UPDATE {STATE} SET version_floor = greatest(version_floor, %s) "
        "WHERE table_name = %s",
        (value, CLOCK_ROW),
    )


@contextmanager
def advisory_lock(pg, *, wait: bool) -> Iterator[bool]:
    if wait:
        pg.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        held = True
    else:
        held = bool(
            pg.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]
        )
    try:
        yield held
    finally:
        if held:
            pg.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


@contextmanager
def writer(pg, *, wait: bool) -> Iterator[VersionClock | None]:
    """Hold the CDC lock with a clock synced to every earlier writer.

    Yields None when ``wait`` is False and another writer holds the lock. The
    floor is persisted only when a version was used, so an idle drain tick
    writes nothing.
    """
    with advisory_lock(pg, wait=wait) as held:
        if not held:
            yield None
            return
        clock = VersionClock(load_floor(pg))
        try:
            yield clock
        finally:
            if clock.last > clock.floor:
                store_floor(pg, clock.last)


# ---------------------------------------------------------------------------
# Table specs: which columns to copy and how to read them
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]  # CH insertable columns that also exist in PG, CH order
    types: dict[str, str]  # CH type of every insertable column, _peerdb_* included
    ch_columns: frozenset[str]  # every CH column, MATERIALIZED ones included
    select_sql: str  # SELECT <columns>, <arrival time> FROM public."table"
    pk_is_uuid: bool
    drift: tuple[str, ...]  # PG columns absent in CH (PeerDB would ADD them)

    @property
    def pk_index(self) -> int:
        return self.columns.index("id")

    def pk_value(self, text: str):
        return uuid.UUID(text) if self.pk_is_uuid else int(text)

    def type_names(self, names: Iterable[str]) -> list[str]:
        return [self.types[name] for name in names]


def select_expr(column: str, udt: str) -> str:
    c = _ident(column)
    if udt == "jsonb":
        # PG's own text form: what PeerDB's pgoutput text transport carries.
        return f"{c}::text"
    if udt == "_varchar":
        # oss_cdc_source: the qualified transport collapses a whole NULL array
        # to []. Array(String) cannot hold a NULL element, so map it to ''.
        return f"array_replace(coalesce({c}, ARRAY[]::varchar[]), NULL, '')"
    return c


def _arrival_expr(udts: dict[str, str]) -> str:
    stamps = [c for c in ("updated_at", "created_at") if udts.get(c) == "timestamptz"]
    return (
        f"coalesce({', '.join(map(_ident, stamps))})" if stamps else "NULL::timestamptz"
    )


_CH_COLUMNS_SQL = """
SELECT table, name, type, default_kind FROM system.columns
WHERE database = currentDatabase() AND table IN %(tables)s
ORDER BY table, position
"""


def load_specs(
    pg, ch, tables: Iterable[str]
) -> tuple[dict[str, TableSpec], dict[str, str]]:
    """Specs for drainable tables, and a safe error message for each other table.

    A problem in one table (missing in CH, changed key) never stops the rest.
    """
    tables = tuple(tables)
    pg_cols: dict[str, dict[str, str]] = defaultdict(dict)
    for table, column, udt in pg.execute(
        """
        SELECT table_name, column_name, udt_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ANY(%s)
        ORDER BY table_name, ordinal_position
        """,
        (list(tables),),
    ).fetchall():
        pg_cols[table][column] = udt
    pks: dict[str, list[str]] = defaultdict(list)
    for table, column in pg.execute(
        """
        SELECT c.relname, a.attname
        FROM pg_catalog.pg_constraint AS p
        JOIN pg_catalog.pg_class AS c ON c.oid = p.conrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_attribute AS a
          ON a.attrelid = c.oid AND a.attnum = ANY(p.conkey)
        WHERE p.contype = 'p' AND n.nspname = 'public' AND c.relname = ANY(%s)
        """,
        (list(tables),),
    ).fetchall():
        pks[table].append(column)
    ch_cols: dict[str, dict[str, str]] = defaultdict(dict)
    ch_all: dict[str, set[str]] = defaultdict(set)
    for table, column, data_type, kind in ch.query(
        _CH_COLUMNS_SQL, parameters={"tables": tables}
    ).result_rows:
        ch_all[table].add(column)
        if kind not in ("MATERIALIZED", "ALIAS", "EPHEMERAL"):
            ch_cols[table][column] = data_type
    specs, errors = {}, {}
    for table in tables:
        try:
            specs[table] = _spec(
                table, pg_cols[table], pks[table], ch_cols[table], ch_all[table]
            )
        except OutboxCDCError as error:
            errors[table] = str(error)
    return specs, errors


def _spec(table, udts, pk, ch_types, ch_all) -> TableSpec:
    if not udts:
        raise OutboxCDCError(f"{table}: source table missing in PostgreSQL")
    if pk != ["id"]:
        # The capture functions read ``id``; fail closed on anything else.
        raise OutboxCDCError(f"{table}: single-column id primary key required")
    if not ch_types:
        raise OutboxCDCError(f"{table}: landing table missing in ClickHouse")
    if any(column not in ch_types for column in (*_PEERDB_COLUMNS, _SYNCED_AT)):
        raise OutboxCDCError(f"{table}: landing table lacks PeerDB columns")
    columns = tuple(c for c in ch_types if not c.startswith("_peerdb_") and c in udts)
    if "id" not in columns:
        raise OutboxCDCError(f"{table}: id column missing in landing table")
    exprs = [select_expr(c, udts[c]) for c in columns]
    return TableSpec(
        name=table,
        columns=columns,
        types=dict(ch_types),
        ch_columns=frozenset(ch_all),
        select_sql=(
            f"SELECT {', '.join(exprs)}, {_arrival_expr(udts)} "
            f"FROM public.{_ident(table)}"
        ),
        pk_is_uuid=udts["id"] == "uuid",
        drift=tuple(c for c in udts if c not in ch_all),
    )


# ---------------------------------------------------------------------------
# Apply: the one write primitive shared by drain, snapshot and reconcile
# ---------------------------------------------------------------------------


def _row_bytes(row) -> int:
    size = 16 * len(row)
    for value in row:
        if isinstance(value, str | bytes):
            size += len(value)
        elif isinstance(value, list):
            size += sum(len(v) if isinstance(v, str | bytes) else 16 for v in value)
    return size


def _copy(pg, ch, spec: TableSpec, sql: str, params, version: int, *, historical: bool):
    """Stream one SELECT from a server-side cursor into ClickHouse.

    Flushes about every FLUSH_BYTES. ``historical`` rows (snapshot, reconcile)
    carry their PG change time as ``_peerdb_synced_at``; drain deltas keep the
    ``now64()`` default. Returns ``(rows, found primary keys, last key)``.
    """
    names = [*spec.columns, *_PEERDB_COLUMNS, *((_SYNCED_AT,) if historical else ())]
    type_names = spec.type_names(names)
    width, pk_index = len(spec.columns), spec.pk_index
    count, found, last, block, size = 0, set(), None, [], 0
    with pg.transaction(), pg.cursor(name="fi_cdc_copy") as cursor:
        cursor.execute(sql, params)
        while rows := cursor.fetchmany(FETCH_ROWS):
            for row in rows:
                values = [*row[:width], 0, version]
                if historical:
                    values.append(row[width] or EPOCH)
                block.append(values)
                size += _row_bytes(row)
                last = row[pk_index]
                found.add(last)
            count += len(rows)
            if size >= FLUSH_BYTES:
                ch.insert(
                    spec.name, block, column_names=names, column_type_names=type_names
                )
                block, size = [], 0
    if block:
        ch.insert(spec.name, block, column_names=names, column_type_names=type_names)
    return count, found, last


def apply_keys(
    pg,
    ch,
    spec: TableSpec,
    keys: Iterable,
    clock: VersionClock,
    stats: Counter,
    *,
    historical: bool = False,
) -> None:
    """Mirror the *current* committed state of ``keys`` (PK values) into CH.

    Found rows are upserted and missing rows are tombstoned. The caller holds
    the writer lock that ``clock`` came from.
    """
    keys = list(dict.fromkeys(keys))
    names = ["id", *_PEERDB_COLUMNS, *((_SYNCED_AT,) if historical else ())]
    for start in range(0, len(keys), APPLY_CHUNK):
        chunk = keys[start : start + APPLY_CHUNK]
        version = clock.next()  # allocated before the read, under the lock
        count, found, _ = _copy(
            pg,
            ch,
            spec,
            f'{spec.select_sql} WHERE "id" = ANY(%s)',
            (chunk,),
            version,
            historical=historical,
        )
        gone = [k for k in chunk if k not in found]
        if gone:
            ch.insert(
                spec.name,
                [[k, 1, version, *((EPOCH,) if historical else ())] for k in gone],
                column_names=names,
                column_type_names=spec.type_names(names),
            )
        stats[f"{spec.name}.upserted"] += count
        stats[f"{spec.name}.tombstoned"] += len(gone)


# ---------------------------------------------------------------------------
# Failure isolation: transient errors fail the tick, poison rows are parked
# ---------------------------------------------------------------------------

# Nothing was lost: the outbox rows stay and the next tick retries.
_TRANSIENT = (
    psycopg.OperationalError,
    psycopg.InterfaceError,
    ClickHouseOperationalError,
    OutboxCDCError,
)
# Not row-specific (undefined column, privileges): fail only this table.
_STRUCTURAL = (psycopg.ProgrammingError,)


class _TableFailed(Exception):
    def __init__(self, error: Exception):
        super().__init__(type(error).__name__)
        self.error = error


def _probe(pg, ch, table: str) -> None:
    try:
        pg.execute("SELECT 1").fetchall()
        ch.query(f"SELECT 1 FROM {table} LIMIT 0")
    except Exception as error:
        raise OutboxCDCError(
            f"{table}: PostgreSQL or ClickHouse unavailable"
        ) from error


def park(pg, table: str, pk: str, error: Exception) -> None:
    # Class name only: driver messages can carry row data.
    pg.execute(
        f"""
        INSERT INTO {DEADLETTER} (table_name, pk, error) VALUES (%s, %s, %s)
        ON CONFLICT (table_name, pk) DO UPDATE SET error = EXCLUDED.error,
            attempts = {DEADLETTER}.attempts + 1, parked_at = clock_timestamp()
        """,
        (table, pk, type(error).__name__),
    )


def _apply_isolated(
    pg, ch, spec, keys: list[str], clock, stats, budget: list[int]
) -> set[str]:
    """Apply ``keys`` (outbox text form); bisect a failure down to poison rows.

    Returns the parked keys. Raises transient errors unchanged and
    ``_TableFailed`` for structural errors or when the park budget runs out.
    """
    try:
        apply_keys(pg, ch, spec, [spec.pk_value(k) for k in keys], clock, stats)
        return set()
    except _TRANSIENT:
        raise
    except _STRUCTURAL as error:
        raise _TableFailed(error) from error
    except Exception as error:
        if len(keys) > 1:
            mid = len(keys) // 2
            return _apply_isolated(
                pg, ch, spec, keys[:mid], clock, stats, budget
            ) | _apply_isolated(pg, ch, spec, keys[mid:], clock, stats, budget)
        _probe(pg, ch, spec.name)  # an outage is not a poison row
        if budget[0] <= 0:
            raise _TableFailed(error) from error
        budget[0] -= 1
        park(pg, spec.name, keys[0], error)
        stats[f"{spec.name}.parked"] += 1
        return {keys[0]}


def requeue_deadletter(pg, *, all_rows: bool = False) -> int:
    """Put parked keys back in the outbox, at most hourly and a few times each.

    A key that keeps failing stays parked (``status`` reports it). ``all_rows``
    requeues everything now, for an operator after a fix.
    """
    condition = (
        "TRUE"
        if all_rows
        else f"attempts < {DEADLETTER_RETRIES} AND (requeued_at IS NULL "
        "OR requeued_at < clock_timestamp() - interval '1 hour')"
    )
    return pg.execute(
        f"""
        WITH due AS (
            UPDATE {DEADLETTER} SET requeued_at = clock_timestamp()
            WHERE {condition} RETURNING table_name, pk
        )
        INSERT INTO {OUTBOX} (table_name, pk, op)
        SELECT table_name, pk, 'U' FROM due
        """
    ).rowcount


# ---------------------------------------------------------------------------
# Drain
# ---------------------------------------------------------------------------


def _outbox_head(pg) -> tuple[int, float]:
    depth, lag = pg.execute(
        f"SELECT count(*), coalesce(extract(epoch FROM clock_timestamp() - "
        f"min(captured_at)), 0) FROM {OUTBOX}"
    ).fetchone()
    return int(depth), float(lag)


def _reset_snapshots(pg, tables: Iterable[str]) -> None:
    pg.execute(
        f"UPDATE {STATE} SET snapshot_cursor = NULL, snapshot_completed_at = NULL "
        "WHERE table_name = ANY(%s)",
        (list(tables),),
    )


def request_reconcile(pg, tables: Iterable[str]) -> None:
    pg.execute(
        f"UPDATE {STATE} SET reconcile_requested_at = "
        "coalesce(reconcile_requested_at, clock_timestamp()) WHERE table_name = ANY(%s)",
        (list(tables),),
    )


def _rearm(pg, tables) -> tuple[list[str], dict[str, str]]:
    """Re-arm lost capture. Writes made while it was off were not captured, so
    the table is re-snapshotted (and reconciled when that completes)."""
    rearmed, failed = [], {}
    for table, triggers in capture_state(pg, tables).items():
        if all(state == "ok" for state in triggers.values()):
            continue
        try:
            arm_triggers(pg, table, triggers, attempts=1)
        except OutboxCDCError as error:
            failed[table] = str(error)  # lock busy; next tick retries
            continue
        except psycopg.ProgrammingError as error:
            failed[table] = f"{table}: cannot arm capture ({type(error).__name__})"
            continue
        _reset_snapshots(pg, (table,))
        rearmed.append(table)
    return rearmed, failed


def add_source_columns(
    ch, table: str, source: SourceTable, present: Iterable[str]
) -> list[str]:
    """ADD the PG columns a landing table lacks, as PeerDB does at runtime."""
    present = set(present)
    added = []
    for column, data_type in source.columns:
        if column not in present:
            ch.command(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS `{column}` {data_type}"
            )
            added.append(f"{table}.{column}")
    return added


def _add_drift(pg, ch, specs, target) -> tuple[list[str], dict[str, str]]:
    drifted = tuple(sorted(t for t, spec in specs.items() if spec.drift))
    if not drifted or target is None:
        return [], {}
    try:
        source = inspect_source(_pg_query(pg), source=target, tables=drifted)
    except SourceError as error:
        # Keep draining the known columns; install fails closed on next boot.
        return [], {table: str(error) for table in drifted}
    added = []
    for table in drifted:
        added += add_source_columns(
            ch, table, source.tables[table], specs[table].ch_columns
        )
    return added, {}


def drain(
    pg,
    ch,
    *,
    tables: tuple[str, ...],
    source=None,
    budget_s: float = 8.0,
    batch: int = DRAIN_BATCH,
) -> dict:
    """One tick: re-arm capture, apply outbox batches, then continue snapshots.

    ``source`` (the PG PeerTarget) enables runtime ADD COLUMN for new PG
    columns; without it they are only reported as drift.
    """
    deadline = time.monotonic() + budget_s
    with writer(pg, wait=False) as clock:
        if clock is None:
            return {"skipped": "another writer holds the CDC lock"}
        stats: Counter = Counter()
        rearmed, rearm_failed = _rearm(pg, tables)
        depth, lag = _outbox_head(pg)
        pending = _pending_snapshots(pg, tables)
        specs, errors, added, drift_errors, failed = {}, {}, [], {}, {}
        if depth or pending:
            specs, errors = load_specs(pg, ch, tables)
            added, drift_errors = _add_drift(pg, ch, specs, source)
            if added:
                refreshed, more = load_specs(
                    pg, ch, tuple({a.split(".")[0] for a in added})
                )
                specs.update(refreshed)
                errors.update(more)
                for table in more:
                    specs.pop(table, None)
            has_deadletter = bool(
                pg.execute(f"SELECT EXISTS (SELECT 1 FROM {DEADLETTER})").fetchone()[0]
            )
            budget: dict[str, list[int]] = {}
            while time.monotonic() < deadline:
                done = drain_batch(
                    pg,
                    ch,
                    specs,
                    clock,
                    stats,
                    skip=set(errors) | set(failed),
                    failed=failed,
                    batch=batch,
                    has_deadletter=has_deadletter,
                    budget=budget,
                )
                if done < batch:
                    break
            # Leftover budget goes to pending (re)snapshots, one page at a time.
            for table in pending:
                if table in specs and table not in failed:
                    while time.monotonic() < deadline:
                        if snapshot_page(pg, ch, specs[table], clock, stats):
                            break
            depth, lag = _outbox_head(pg)
        return {
            **stats,
            "outbox_depth": depth,
            "lag_seconds": round(lag, 3),
            "rearmed": rearmed,
            "rearm_failed": rearm_failed,
            "errors": {**errors, **failed},
            "drift": {t: list(s.drift) for t, s in specs.items() if s.drift},
            "drift_errors": drift_errors,
            "added_columns": added,
            "pending_snapshots": _pending_snapshots(pg, tables) if pending else [],
        }


def drain_batch(
    pg,
    ch,
    specs: dict[str, TableSpec],
    clock: VersionClock,
    stats: Counter,
    *,
    skip: set[str] = frozenset(),
    failed: dict[str, str] | None = None,
    batch: int = DRAIN_BATCH,
    has_deadletter: bool = False,
    budget: dict[str, list[int]] | None = None,
) -> int:
    """Apply the oldest ``batch`` outbox rows of the tables not in ``skip``.

    Rows of a table that fails stay in the outbox; the table joins ``failed``
    and the rest of the tick skips it. ``budget`` caps parked keys per table
    for the whole tick.
    """
    failed = {} if failed is None else failed
    budget = {} if budget is None else budget
    rows = pg.execute(
        f"SELECT seq, table_name, pk, op FROM {OUTBOX} "
        "WHERE table_name <> ALL(%s) ORDER BY seq LIMIT %s",
        (sorted(skip), batch),
    ).fetchall()
    if not rows:
        return 0
    keys: dict[str, list[tuple[int, str]]] = defaultdict(list)
    truncated: set[str] = set()
    done: list[int] = []
    for seq, table, pk, op in rows:
        if table not in specs:
            # No longer a landing table (e.g. usage schema deselected).
            stats["unknown_table"] += 1
            done.append(seq)
        elif op == "T":
            truncated.add(table)
            done.append(seq)
        else:
            keys[table].append((seq, pk))
    for table, entries in keys.items():
        pks = list(dict.fromkeys(pk for _, pk in entries))
        budget.setdefault(table, [MAX_PARKED_PER_TICK])
        try:
            parked = _apply_isolated(
                pg, ch, specs[table], pks, clock, stats, budget[table]
            )
        except _TableFailed as failure:
            failed[table] = f"{table}: apply failed ({failure})"
            continue
        if has_deadletter:
            # A key that applies cleanly now is no longer parked.
            pg.execute(
                f"DELETE FROM {DEADLETTER} WHERE table_name = %s AND pk = ANY(%s)",
                (table, [pk for pk in pks if pk not in parked]),
            )
        done.extend(seq for seq, _ in entries)
    if truncated:
        # A TRUNCATE leaves no keys; tombstone by id parity (reconcile).
        request_reconcile(pg, truncated)
    pg.execute(f"DELETE FROM {OUTBOX} WHERE seq = ANY(%s)", (done,))
    stats["outbox_rows"] += len(done)
    return len(rows)


# ---------------------------------------------------------------------------
# Snapshot (resumable, page-at-a-time under the lock)
# ---------------------------------------------------------------------------


def _pending_snapshots(pg, tables: Iterable[str]) -> list[str]:
    return [
        row[0]
        for row in pg.execute(
            f"SELECT table_name FROM {STATE} WHERE snapshot_completed_at IS NULL "
            "AND table_name = ANY(%s) ORDER BY table_name",
            (list(tables),),
        ).fetchall()
    ]


def snapshot_page(
    pg,
    ch,
    spec: TableSpec,
    clock: VersionClock,
    stats: Counter,
    page: int = SNAPSHOT_PAGE,
) -> bool:
    """Copy one keyset page; return True when the table snapshot is complete.

    Capture triggers already exist, so writes racing the snapshot land in the
    outbox and are applied again with a higher version. A completed snapshot
    requests a reconcile to tombstone ids deleted while capture was off.
    """
    (cursor,) = pg.execute(
        f"SELECT snapshot_cursor FROM {STATE} WHERE table_name = %s", (spec.name,)
    ).fetchone()
    version = clock.next()
    if cursor is None:
        sql, params = f'{spec.select_sql} ORDER BY "id" LIMIT %s', (page,)
    else:
        sql, params = (
            f'{spec.select_sql} WHERE "id" > %s ORDER BY "id" LIMIT %s',
            (spec.pk_value(cursor), page),
        )
    count, _, last = _copy(pg, ch, spec, sql, params, version, historical=True)
    stats[f"{spec.name}.snapshot"] += count
    done = count < page
    pg.execute(
        f"UPDATE {STATE} SET snapshot_cursor = %s, "
        "snapshot_completed_at = CASE WHEN %s THEN clock_timestamp() END, "
        "reconcile_requested_at = CASE WHEN %s THEN clock_timestamp() "
        "ELSE reconcile_requested_at END WHERE table_name = %s",
        (str(last) if last is not None else cursor, done, done, spec.name),
    )
    return done


# ---------------------------------------------------------------------------
# Reconcile: id-set parity for TRUNCATE, restores and disabled triggers
# ---------------------------------------------------------------------------


def _ch_live_page_sql(table: str, after: bool, uuid_key: bool) -> str:
    bound = "toUUID(%(after)s)" if uuid_key else "%(after)s"
    return (
        f"SELECT id FROM {table} FINAL WHERE _peerdb_is_deleted = 0"
        + (f" AND id > {bound}" if after else "")
        + " ORDER BY id LIMIT %(limit)s"
    )


def _ch_live_among_sql(table: str) -> str:
    return (
        f"SELECT id FROM {table} FINAL WHERE id IN %(ids)s AND _peerdb_is_deleted = 0"
    )


def reconcile_table(
    pg,
    ch,
    spec: TableSpec,
    stats: Counter,
    *,
    chunk: int = RECONCILE_CHUNK,
) -> None:
    """Re-apply every id whose liveness differs between PG and CH.

    Two keyset-paged passes with short queries: no long PG snapshot pins the
    xmin horizon and no full diff is held in memory. Each pass pages in its own
    database's key order, so the two orders never need to agree. The writer
    lock is taken per repaired page, so the drain keeps running during a long
    sweep; the caller must not hold it (a nested clock would reuse versions).
    Repairs re-read current PG state, so a page decided on stale data is still
    applied correctly.
    """
    t = f"public.{_ident(spec.name)}"

    def repair(keys):
        stats[f"{spec.name}.reconciled"] += len(keys)
        with writer(pg, wait=True) as clock:
            apply_keys(pg, ch, spec, keys, clock, stats, historical=True)

    # Pass A: ids live in CH but absent from PG -> tombstones.
    after = None
    while True:
        params = {"limit": chunk}
        if after is not None:
            params["after"] = str(after) if spec.pk_is_uuid else after
        ids = [
            row[0]
            for row in ch.query(
                _ch_live_page_sql(spec.name, after is not None, spec.pk_is_uuid),
                parameters=params,
            ).result_rows
        ]
        if not ids:
            break
        present = {
            row[0]
            for row in pg.execute(
                f'SELECT "id" FROM {t} WHERE "id" = ANY(%s)', (ids,)
            ).fetchall()
        }
        gone = [i for i in ids if i not in present]
        if gone:
            repair(gone)
        if len(ids) < chunk:
            break
        after = ids[-1]
    # Pass B: ids in PG but not live in CH -> upserts.
    after = None
    while True:
        if after is None:
            rows = pg.execute(f'SELECT "id" FROM {t} ORDER BY 1 LIMIT %s', (chunk,))
        else:
            rows = pg.execute(
                f'SELECT "id" FROM {t} WHERE "id" > %s ORDER BY 1 LIMIT %s',
                (after, chunk),
            )
        ids = [row[0] for row in rows.fetchall()]
        if not ids:
            break
        live = {
            row[0]
            for row in ch.query(
                _ch_live_among_sql(spec.name), parameters={"ids": ids}
            ).result_rows
        }
        missing = [i for i in ids if i not in live]
        if missing:
            repair(missing)
        if len(ids) < chunk:
            break
        after = ids[-1]


def _due_reconciles(pg, tables, *, full: bool, max_age_s: int) -> list[str]:
    return [
        row[0]
        for row in pg.execute(
            f"""
            SELECT table_name FROM {STATE}
            WHERE table_name = ANY(%s) AND snapshot_completed_at IS NOT NULL
              AND (%s OR reconcile_requested_at IS NOT NULL OR reconciled_at IS NULL
                   OR reconciled_at < clock_timestamp() - make_interval(secs => %s))
            ORDER BY table_name
            """,
            (list(tables), full, max_age_s),
        ).fetchall()
    ]


def reconcile(
    pg,
    ch,
    *,
    tables: tuple[str, ...],
    full: bool = False,
    max_age_s: int = RECONCILE_EVERY_S,
) -> dict:
    """Reconcile requested tables and those not swept for ``max_age_s``.

    Tables still snapshotting wait; the snapshot requests a reconcile when it
    completes. Also requeues parked keys that are due another attempt.
    """
    stats: Counter = Counter()
    requeued = requeue_deadletter(pg)
    specs, errors = load_specs(pg, ch, tables)
    swept = []
    for table in _due_reconciles(pg, tables, full=full, max_age_s=max_age_s):
        if table not in specs:
            continue
        (started,) = pg.execute("SELECT clock_timestamp()").fetchone()
        reconcile_table(pg, ch, specs[table], stats)
        # A request made during the sweep (a TRUNCATE, say) stays pending.
        pg.execute(
            f"UPDATE {STATE} SET reconciled_at = %s, reconcile_requested_at = "
            "CASE WHEN reconcile_requested_at <= %s THEN NULL "
            "ELSE reconcile_requested_at END WHERE table_name = %s",
            (started, started, table),
        )
        swept.append(table)
    return {**stats, "swept": swept, "errors": errors, "requeued": requeued}


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def _pg_query(pg) -> Callable[[str, dict], list]:
    return lambda sql, parameters: pg.execute(sql, parameters).fetchall()


def landing_definitions(
    source: SourceInventory, *, include_usage_schema: bool
) -> dict[str, str]:
    """PeerDB's CREATE for each landing table: dev's qualified source profile.

    The derived usage columns are left out; ``bootstrap_cdc`` adds them with
    the packaged cdc_002 migration, as it does for PeerDB-created tables.
    """
    definitions = core._source_definitions(
        source, include_usage_schema=include_usage_schema
    )
    for (table, column), (data_type, kind, expression) in _DERIVED.items():
        if table in definitions:
            field = f", `{column}` {data_type} {kind} {expression}"
            if field not in definitions[table]:
                raise OutboxCDCError(f"{table}: unexpected source profile layout")
            definitions[table] = definitions[table].replace(field, "")
    return definitions


_CH_EXISTING_SQL = (
    "SELECT name FROM system.tables WHERE database = currentDatabase() "
    "AND name IN %(names)s"
)
_CH_TABLE_COLUMNS_SQL = (
    "SELECT name FROM system.columns WHERE database = currentDatabase() "
    "AND table = %(table)s"
)


def ensure_landing_tables(
    ch, source: SourceInventory, *, include_usage_schema: bool
) -> tuple[list[str], list[str]]:
    """Create absent landing tables and ADD PG columns PeerDB would have added."""
    definitions = landing_definitions(source, include_usage_schema=include_usage_schema)
    existing = {
        row[0]
        for row in ch.query(
            _CH_EXISTING_SQL, parameters={"names": tuple(definitions)}
        ).result_rows
    }
    created, added = [], []
    for name, ddl in definitions.items():
        if name not in existing:
            ch.command(ddl)
            created.append(name)
            continue
        present = [
            row[0]
            for row in ch.query(
                _CH_TABLE_COLUMNS_SQL, parameters={"table": name}
            ).result_rows
        ]
        added += add_source_columns(ch, name, source.tables[name], present)
    return created, added


def peerdb_objects(pg, ch) -> dict[str, list[str]]:
    """PeerDB's leftovers: ``peerflow_slot_*``, ``peerflow_pub_*``, ``_peerdb_raw_*``."""
    slots = pg.execute(
        "SELECT slot_name, active_pid FROM pg_catalog.pg_replication_slots "
        "WHERE slot_name LIKE %s ORDER BY 1",
        (r"peerflow\_slot\_%",),
    ).fetchall()
    publications = pg.execute(
        "SELECT pubname FROM pg_catalog.pg_publication WHERE pubname LIKE %s ORDER BY 1",
        (r"peerflow\_pub\_%",),
    ).fetchall()
    raw = ch.query(
        "SELECT name FROM system.tables WHERE database = currentDatabase() "
        "AND startsWith(name, '_peerdb_raw_') ORDER BY name"
    ).result_rows
    return {
        "slots": [name for name, _ in slots],
        "active_slots": [name for name, pid in slots if pid is not None],
        "publications": [row[0] for row in publications],
        "raw_tables": [row[0] for row in raw],
    }


def take_over_from_peerdb(pg, ch, objects: dict[str, list[str]]) -> None:
    """Drop an inactive PeerDB's slots, publications and raw tables.

    A slot left behind retains WAL forever. Refuses while any slot is active:
    two writers with different version schemes must never share the tables.
    """
    if objects["active_slots"]:
        raise OutboxCDCError(
            "a running PeerDB holds replication slots "
            f"{objects['active_slots']}; stop PeerDB first"
        )
    for slot in objects["slots"]:
        pg.execute("SELECT pg_drop_replication_slot(%s)", (slot,))
    if objects["publications"]:
        _ddl(
            pg,
            [
                f"DROP PUBLICATION IF EXISTS {_quote(p)}"
                for p in objects["publications"]
            ],
        )
    for table in objects["raw_tables"]:
        if not table.isascii() or not table.isidentifier():
            raise OutboxCDCError("unsafe PeerDB raw table name")
        ch.command(f"DROP TABLE IF EXISTS `{table}`")


def _inventory(config, tables) -> core.MirrorInventory:
    # One synthetic "mirror" owns every landing table.
    return core.MirrorInventory(
        config.destination.database,
        tuple((MIRROR_NAME, table) for table in tables),
        config.source,
    )


def _ch_has_live_rows(ch, table: str) -> bool:
    return bool(
        ch.query(
            f"SELECT 1 FROM {table} WHERE _peerdb_is_deleted = 0 LIMIT 1"
        ).result_rows
    )


def _pg_has_rows(pg, table: str) -> bool:
    return bool(
        pg.execute(f"SELECT EXISTS (SELECT 1 FROM public.{_ident(table)})").fetchone()[
            0
        ]
    )


def _ch_max_version(ch, tables) -> int:
    return max(
        (
            int(
                ch.query(f"SELECT max(_peerdb_version) FROM {t}").result_rows[0][0] or 0
            )
            for t in tables
        ),
        default=0,
    )


def install(
    pg,
    ch,
    *,
    config,
    apply: bool,
    takeover_peerdb: bool = False,
    snapshot_budget_s: float = INSTALL_SNAPSHOT_S,
) -> dict:
    """Check (``apply=False``, read-only) or install capture and landing tables.

    Apply is idempotent; the platform bootstrap runs it on every start. The
    snapshot runs for at most ``snapshot_budget_s``; the drain finishes it and
    ``status`` shows progress.
    """
    tables = core.landing_tables(include_usage_schema=config.include_usage_schema)
    peerdb = peerdb_objects(pg, ch)
    source = inspect_source(_pg_query(pg), source=config.source, tables=tables)
    bad = sorted(t for t, s in source.tables.items() if s.primary_key != ("id",))
    if bad:
        raise OutboxCDCError(f"single-column id primary key required: {bad}")
    if not apply:
        return check(pg, ch, config=config, source=source, tables=tables, peerdb=peerdb)
    if config.hosted:
        raise OutboxCDCError("outbox CDC is qualified for single-node installs only")
    leftovers = any(peerdb[k] for k in ("slots", "publications", "raw_tables"))
    if leftovers and not takeover_peerdb:
        raise OutboxCDCError(
            "PeerDB replication objects exist; rerun with --takeover-peerdb to "
            "drop them (PeerDB must be stopped)"
        )
    stats: Counter = Counter()
    # The session lock is re-entrant: the versioned writer below nests in it.
    with advisory_lock(pg, wait=True):
        if leftovers:
            take_over_from_peerdb(pg, ch, peerdb)
        # 1. Capture first: every write after this point is in the outbox.
        capture = install_capture(pg, tables)
        # 2. Landing tables in the exact PeerDB-created shape.
        created, added = ensure_landing_tables(
            ch, source, include_usage_schema=config.include_usage_schema
        )
        # 3. Derived usage columns, dictionaries, views and full qualification,
        #    through the PeerDB-path installer unchanged.
        result = core.bootstrap_cdc(
            ch,
            database=config.destination.database,
            inspect_mirrors=lambda: _inventory(config, tables),
            inspect_source=lambda: source,
            applied_by="oss-outbox-cdc",
            include_usage_schema=config.include_usage_schema,
            # Dictionaries read their CLICKHOUSE source with these credentials.
            ch_user=config.ch_user,
            ch_password=config.ch_password,
        )
        with writer(pg, wait=True) as clock:
            # 4. Versions above anything already in CH (PeerDB-era rows included).
            clock.last = max(clock.last, _ch_max_version(ch, tables))
            # 5. Re-snapshot whatever CH lost or never had while PG kept its
            #    state: a PeerDB takeover (changes after PeerDB stopped were
            #    missed), a re-created landing table, or an emptied one (volume
            #    reset, restore from a PG-only backup, ch25_remove_pg drops).
            #    Tables new to the state table are already pending.
            new = set(capture["new_state"])
            resync = sorted(
                t
                for t in tables
                if t not in new
                and (
                    leftovers
                    or t in created
                    or (not _ch_has_live_rows(ch, t) and _pg_has_rows(pg, t))
                )
            )
            _reset_snapshots(pg, resync)
            # 6. A bounded prefix of the snapshot; the drain does the rest.
            specs, errors = load_specs(pg, ch, tables)
            if errors:
                raise OutboxCDCError(f"landing tables not drainable: {sorted(errors)}")
            deadline = time.monotonic() + snapshot_budget_s
            for table in _pending_snapshots(pg, tables):
                while time.monotonic() < deadline:
                    if snapshot_page(pg, ch, specs[table], clock, stats):
                        break
    return {
        "ready": True,
        "applied": True,
        "armed_triggers": capture["armed"],
        "created_landing": created,
        "added_columns": added,
        "created_dependents": list(result.created),
        "peerdb_takeover": peerdb if leftovers else None,
        "resynced": resync,
        "pending_snapshots": _pending_snapshots(pg, tables),
        "snapshot": dict(stats),
    }


def check(pg, ch, *, config, source, tables, peerdb) -> dict:
    """Read-only readiness. Fails when capture could be running without a drain."""
    problems = []
    if config.hosted:
        problems.append("outbox CDC is qualified for single-node installs only")
    if any(peerdb[k] for k in ("slots", "publications", "raw_tables")):
        problems.append("PeerDB replication objects exist")
    state = status(pg, tables=tables)
    if not state["installed"]:
        problems.append("capture is not installed")
    else:
        if state["capture_broken"]:
            problems.append(
                f"capture triggers missing or disabled: {sorted(state['capture_broken'])}"
            )
        if state["lag_seconds"] > CHECK_MAX_LAG_S:
            problems.append(
                f"drain is {int(state['lag_seconds'])} s behind; is the "
                "outbox-cdc-drain schedule running?"
            )
        if state["outbox_depth"] > CHECK_MAX_DEPTH:
            problems.append(f"outbox holds {state['outbox_depth']} rows")
    try:
        core.inspect_bootstrap(
            ch,
            database=config.destination.database,
            inspect_mirrors=lambda: _inventory(config, tables),
            inspect_source=lambda: source,
            require_complete=True,
            include_usage_schema=config.include_usage_schema,
        )
    except (core.BootstrapError, upgrade.CDCUpgradeError) as error:
        problems.append(f"ClickHouse landing schema: {error}")
    return {"ready": not problems, "applied": False, "problems": problems, **state}


def _outbox_exists(pg) -> bool:
    return bool(
        pg.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (f"public.{OUTBOX}",)
        ).fetchone()[0]
    )


def status(pg, *, tables: tuple[str, ...]) -> dict:
    if not _outbox_exists(pg):
        return {"installed": False}
    depth, lag = _outbox_head(pg)
    rows = pg.execute(
        f"SELECT table_name, snapshot_cursor, snapshot_completed_at IS NULL, "
        f"reconcile_requested_at IS NOT NULL FROM {STATE} WHERE table_name = ANY(%s) "
        "ORDER BY table_name",
        (list(tables),),
    ).fetchall()
    (parked,) = pg.execute(f"SELECT count(*) FROM {DEADLETTER}").fetchone()
    return {
        "installed": True,
        "capture_broken": broken_capture(capture_state(pg, tables)),
        "outbox_depth": depth,
        "lag_seconds": round(lag, 3),
        # Table -> last copied key ('' before the first page).
        "pending_snapshots": {
            t: cursor or "" for t, cursor, pending, _ in rows if pending
        },
        "reconcile_requested": [t for t, _, _, requested in rows if requested],
        "parked_keys": parked,
    }


def resync(pg, *, tables: Iterable[str]) -> list[str]:
    """Force a content re-copy (snapshot, then reconcile) of ``tables``."""
    tables = list(tables)
    _reset_snapshots(pg, tables)
    return _pending_snapshots(pg, tables)


# ---------------------------------------------------------------------------
# Entry points: platform bootstrap (in-process) and CLI (no Django)
# ---------------------------------------------------------------------------


def connect(config):
    import clickhouse_connect

    pg = psycopg.connect(
        host=config.source.host,
        port=config.source.port,
        dbname=config.source.database,
        user=config.pg_user,
        password=config.pg_password,
        autocommit=True,
        connect_timeout=10,
        application_name="fi_outbox_cdc",
        options="-c statement_timeout=120000",
    )
    try:
        ch = clickhouse_connect.get_client(
            host=config.destination.host,
            port=config.http_port,
            database=config.destination.database,
            username=config.ch_user,
            password=config.ch_password,
            send_receive_timeout=120,
            # Reconcile reads one table while inserting into it; a shared
            # session would reject the second concurrent query.
            autogenerate_session_id=False,
        )
    except Exception:
        pg.close()
        raise
    return pg, ch


def load_config(env=None):
    from tracer.services.clickhouse.oss_cdc_install import Config

    return Config.from_env(os.environ if env is None else env)


def ensure_installed(
    env=None,
    *,
    schedules: bool = True,
    takeover_peerdb: bool = True,
    snapshot_budget_s: float = INSTALL_SNAPSHOT_S,
) -> dict:
    """Make the install match ``FI_CDC_MODE``. Idempotent; call on every start.

    The platform bootstrap calls this once per container start, after
    ``migrate`` and the ClickHouse native schema phase, whatever the mode:

    * ``outbox``: install capture and landing tables, then create or update
      the drain and reconcile schedules. Capture never runs without a drain.
      An inactive PeerDB's slots are dropped (``takeover_peerdb``); a running
      one makes this fail.
    * ``peerdb``/``off``: remove capture triggers and outbox tables if an
      earlier outbox install left them (they would grow without a drain), and
      delete the schedules.

    ``schedules`` needs Django settings and a reachable Temporal server.
    """
    env = os.environ if env is None else env
    mode = cdc_mode(env)
    config = load_config(env)
    pg, ch = connect(config)
    try:
        if mode == "outbox":
            result = install(
                pg,
                ch,
                config=config,
                apply=True,
                takeover_peerdb=takeover_peerdb,
                snapshot_budget_s=snapshot_budget_s,
            )
        else:
            result = {
                "removed_triggers": uninstall_capture(pg)
                if capture_installed(pg)
                else []
            }
    finally:
        pg.close()
        ch.close()
    if schedules:
        from tfc.temporal.schedules.outbox_cdc import sync_outbox_cdc_schedules

        sync_outbox_cdc_schedules(enabled=mode == "outbox")
        result["schedules"] = "registered" if mode == "outbox" else "removed"
    return {"mode": mode, **result}


def main(argv=None) -> int:
    from tracer.services.clickhouse.oss_cdc_install import InstallError

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command",
        choices=(
            "install",
            "ensure",
            "drain",
            "reconcile",
            "status",
            "resync",
            "requeue",
            "uninstall",
        ),
        help=(
            "install: read-only check, or install with --apply (no schedules). "
            "ensure: what the platform bootstrap runs (needs Django settings)."
        ),
    )
    parser.add_argument("tables", nargs="*", help="resync: tables (default: all)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--takeover-peerdb",
        action="store_true",
        help="drop an inactive PeerDB's slots, publications and raw tables",
    )
    parser.add_argument("--snapshot-seconds", type=float, default=60.0)
    parser.add_argument("--all", action="store_true", help="reconcile every table")
    args = parser.parse_args(argv)
    try:
        if args.command == "ensure":
            import django

            django.setup()
            result = ensure_installed()
            print(json.dumps(result, default=str))
            return 0
        config = load_config()
        pg, ch = connect(config)
        tables = core.landing_tables(include_usage_schema=config.include_usage_schema)
        try:
            if args.command in ("resync", "requeue", "uninstall") and not args.apply:
                raise OutboxCDCError(f"{args.command} changes state; pass --apply")
            if args.command == "install":
                result = install(
                    pg,
                    ch,
                    config=config,
                    apply=args.apply,
                    takeover_peerdb=args.takeover_peerdb,
                    snapshot_budget_s=args.snapshot_seconds,
                )
            elif args.command == "drain":
                result = drain(
                    pg, ch, tables=tables, source=config.source, budget_s=float("inf")
                )
            elif args.command == "reconcile":
                result = reconcile(pg, ch, tables=tables, full=args.all)
            elif args.command == "resync":
                unknown = set(args.tables) - set(tables)
                if unknown:
                    raise OutboxCDCError(f"not landing tables: {sorted(unknown)}")
                result = {"pending_snapshots": resync(pg, tables=args.tables or tables)}
            elif args.command == "requeue":
                result = {"requeued": requeue_deadletter(pg, all_rows=True)}
            elif args.command == "uninstall":
                result = {"removed_triggers": uninstall_capture(pg)}
            else:
                result = status(pg, tables=tables)
        finally:
            pg.close()
            ch.close()
        print(json.dumps(result, default=str))
        return 0 if result.get("ready", True) else 1
    except (
        InstallError,
        OutboxCDCError,
        SourceError,
        core.BootstrapError,
        upgrade.CDCUpgradeError,
    ) as error:
        print(str(error), file=sys.stderr)
    except Exception as error:  # driver messages can carry credentials/data
        print(
            f"outbox CDC {args.command} failed ({type(error).__name__})",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
