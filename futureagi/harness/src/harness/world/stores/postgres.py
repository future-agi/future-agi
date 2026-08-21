"""Postgres, as the worked example of what an engine has to supply.

This is not "the database the harness supports". It is the reference: when the build stage
finds an agent on ClickHouse or MySQL or DuckDB, what it writes is a class this shape, and
what it has to work out is only what is in this file below ``boot_env`` -- how to reach the
engine, how to read what it holds, and how to put that back. Starting a container, finding a
free port, waiting for the thing to genuinely answer and not leaking it afterwards are all in
``ContainerStore`` and are never rewritten.

Nothing here knows what the agent's tools do. The agent keeps its own client, its own SQL and
its own migrations; the only thing that changed is the host on the far end of its DSN. The
schema is not invented either -- the build stage runs the agent's own migrations through
``apply``, so the tables are the agent's tables, spelled the way the agent spells them. A
schema we wrote ourselves would be a guess, and every check written against it would inherit
the guess.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import Held, Snapshot, StoreError
from .container import ContainerStore

SCHEMA = "schema.sql"


def _psycopg() -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise StoreError(
            "psycopg is not installed, so a Postgres store cannot be read. Install it with "
            "`uv sync --extra postgres`."
        ) from exc
    return psycopg


def _tables(connection: Any) -> list[str]:
    rows = connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    ).fetchall()
    return [row[0] for row in rows]


def _column_types(connection: Any, table: str) -> dict[str, str]:
    """Declared column types, so arrays are not coerced into JSON."""
    rows = connection.execute(
        """
        SELECT column_name, data_type
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


class PostgresStore(ContainerStore):
    """A Postgres container the agent under test is pointed at."""

    engine = "postgres"
    image = "postgres:16"
    container_port = 5432
    boot_env = {
        "POSTGRES_USER": "{user}",
        "POSTGRES_PASSWORD": "{password}",
        "POSTGRES_DB": "{database}",
    }

    # -- how to reach it -------------------------------------------------------------

    def dsn(self) -> str:
        external = os.environ.get("ALK_POSTGRES_DSN", "").strip()
        if external:
            return external
        host, port = self.address()
        return f"postgresql://{self.user}:{self.password}@{host}:{port}/{self.database}"

    def probe(self) -> None:
        """Really connect. A running container is not yet a database that listens."""
        with _psycopg().connect(self.dsn(), connect_timeout=3) as connection:
            connection.execute("SELECT 1")

    def _connect(self) -> Any:
        """A short-lived autocommit connection.

        Deliberately not pooled and never held open. An idle transaction of ours would block
        the ``TRUNCATE`` in ``restore``, and a reset that hangs on the harness's own connection
        is a very expensive thing to debug.
        """
        return _psycopg().connect(self.dsn(), autocommit=True)

    # -- how to read what it holds ---------------------------------------------------

    def apply(self, script: str) -> None:
        """Run whatever was handed in: the agent's migrations, or its seed."""
        if not script.strip():
            return
        with self._connect() as connection:
            connection.execute(script)
        # Remembered because the snapshot holds rows, not DDL. A restore into a fresh
        # container finds no tables, and restoring rows into a schema that is not there
        # quietly restores nothing.
        self.applied.append(script)

    def _primary_key(self, connection: Any, table: str) -> list[str]:
        """The primary key columns, used only to read rows back in a stable order."""
        rows = connection.execute(
            """
            SELECT a.attname
              FROM pg_index i
              JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
             WHERE i.indrelid = %s::regclass AND i.indisprimary
             ORDER BY array_position(i.indkey, a.attnum)
            """,
            (f'public."{table}"',),
        ).fetchall()
        return [row[0] for row in rows]

    def state(self) -> dict[str, list[dict[str, Any]]]:
        """Every table and its rows, in the shape the checks already expect.

        Ordered by primary key where there is one. Without that the same data comes back in
        whatever order the heap happens to hold it, and a check comparing the first row is
        reading a coin toss rather than the agent's behaviour.
        """
        with self._connect() as connection:
            out: dict[str, list[dict[str, Any]]] = {}
            for table in _tables(connection):
                key = self._primary_key(connection, table)
                order = (
                    " ORDER BY " + ", ".join(f'"{column}"' for column in key)
                    if key
                    else ""
                )
                cursor = connection.execute(f'SELECT * FROM "{table}"{order}')
                columns = [description[0] for description in cursor.description or []]
                out[table] = [
                    dict(zip(columns, row, strict=True)) for row in cursor.fetchall()
                ]
            return out

    # -- how to put it back ----------------------------------------------------------

    def freeze(self) -> Snapshot:
        """Rows and sequence counters, which together are the whole mutable state."""
        with self._connect() as connection:
            counters = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT sequencename, last_value FROM pg_sequences "
                    "WHERE schemaname = 'public'"
                ).fetchall()
                if row[1] is not None
            }
        return Snapshot(rows=self.state(), counters=counters)

    def restore(self, snapshot: Snapshot) -> None:
        """Put the data back exactly as the snapshot found it.

        Foreign keys are suspended for the duration rather than the rows being sorted into
        dependency order: the snapshot was taken from a consistent database, so what goes back
        is consistent by construction, and ordering it would be solving a problem we do not
        have. Counters are set last, so the next scenario's first insert gets the id the first
        scenario's did.
        """
        with self._connect() as connection:
            tables = _tables(connection)
            if not tables:
                return
            listed = ", ".join(f'"{table}"' for table in tables)
            # One statement, so Postgres resolves the dependency order between them itself.
            connection.execute(f"TRUNCATE TABLE {listed} RESTART IDENTITY CASCADE")

            connection.execute("SET session_replication_role = replica")
            try:
                for table, rows in snapshot.rows.items():
                    if not rows or table not in tables:
                        continue
                    columns = list(rows[0])
                    types = _column_types(connection, table)
                    quoted = ", ".join(f'"{column}"' for column in columns)
                    placeholders = ", ".join(["%s"] * len(columns))
                    statement = (
                        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'
                    )
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            statement,
                            [
                                tuple(
                                    _adapt(row.get(column), types.get(column, ""))
                                    for column in columns
                                )
                                for row in rows
                            ],
                        )
            finally:
                connection.execute("SET session_replication_role = DEFAULT")

            for sequence, value in snapshot.counters.items():
                connection.execute(
                    "SELECT setval(%s, %s, true)", (f'public."{sequence}"', value)
                )

    def save_to(self, path: str | Path) -> None:
        """Save both the records and the DDL a fresh Postgres store needs.

        The DDL is every script `apply` ran, not a `pg_dump`: those scripts are the agent's own
        migrations and seed, already proved to work through psycopg, so replaying them needs no
        shell out to the container at all.
        """
        if not self.applied:
            with self._connect() as connection:
                tables = _tables(connection)
            if tables:
                # applied is empty only when nothing ever went through apply() -- an
                # ALK_POSTGRES_DSN store migrated by something outside the harness. Writing
                # an empty schema.sql over a real schema would silently lose it.
                raise StoreError(
                    "no scripts were recorded through apply(), but the database already has "
                    f"tables ({', '.join(tables)}); refusing to write an empty {SCHEMA}"
                )
        Held.save_to(self, path)
        root = Path(path)
        (root / SCHEMA).write_text("\n\n".join(self.applied), encoding="utf-8")

    def load_from(self, path: str | Path) -> None:
        # Held.load_from replays store.json's own "schema" list script by script; applying
        # schema.sql here too would run the same CREATE TABLEs a second time.
        Held.load_from(self, path)

    # -- what a scenario changes -----------------------------------------------------

    def add(self, collection: str, record: Any) -> int:
        columns = list(record)
        quoted = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        with self._connect() as connection:
            types = _column_types(connection, collection)
            cursor = connection.execute(
                f'INSERT INTO "{collection}" ({quoted}) VALUES ({placeholders})',
                tuple(
                    _adapt(record[column], types.get(column, "")) for column in columns
                ),
            )
            return cursor.rowcount

    def amend(self, collection: str, key: str, changes: Any, *, by: str = "") -> int:
        if not by:
            raise StoreError(
                f"{collection} is a table, so changing a record needs the column it is keyed on"
            )
        sets = ", ".join(f'"{column}" = %s' for column in changes)
        with self._connect() as connection:
            types = _column_types(connection, collection)
            cursor = connection.execute(
                f'UPDATE "{collection}" SET {sets} WHERE "{by}" = %s',
                (
                    *(
                        _adapt(value, types.get(column, ""))
                        for column, value in changes.items()
                    ),
                    key,
                ),
            )
            return cursor.rowcount

    def remove(self, collection: str, key: str = "", *, by: str = "") -> int:
        if key and not by:
            raise StoreError(
                f"{collection} is a table, so removing one record needs the column it is keyed on"
            )
        statement = f'DELETE FROM "{collection}"' + (
            f' WHERE "{by}" = %s' if key else ""
        )
        with self._connect() as connection:
            cursor = connection.execute(statement, (key,) if key else ())
            return cursor.rowcount


def _adapt(value: Any, data_type: str = "") -> Any:
    """Hand back a value in the form psycopg will write.

    A list in a JSON column must be wrapped, while a list in an ARRAY column must remain a list
    so psycopg emits a native Postgres array.
    """
    if isinstance(value, dict) or (
        isinstance(value, list) and data_type in ("json", "jsonb")
    ):
        from psycopg.types.json import Jsonb

        return Jsonb(value)
    return value
