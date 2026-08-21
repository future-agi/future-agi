"""Database-level operations against the long-lived harness-store Postgres server.

A ``PostgresStore`` starts and stops its own disposable container, one per suite. This is
different: one Postgres server stays up for as long as the harness does, and this module is
everything that happens to it above the level of a single database -- standing up a schema's
data once as a ``hm_`` master, cloning it into a ``hc_`` copy per scenario in milliseconds
instead of replaying every migration and seed row again, and cleaning up the ``ht_`` staging
databases a crashed build leaves behind.

Every operation here runs outside a transaction and closes its connection immediately after:
``CREATE DATABASE`` and ``ALTER DATABASE ... RENAME`` cannot run inside one, and a connection
of ours left idle would itself be the thing blocking the next operation. Every database this
class will touch is named ``hm_``/``hc_``/``ht_`` and nothing else, so a bug here can never
reach a database it did not create.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import Snapshot, StoreError
from .postgres import _adapt, _column_types, _tables

MASTER_PREFIX = "hm_"
COPY_PREFIX = "hc_"
TEMP_PREFIX = "ht_"

# What this class is ever allowed to create or drop. The guard against a bug ever reaching
# `postgres` or `template1`.
_NAME_RE = re.compile(r"^(hm_|hc_|ht_)[a-z0-9_]{1,60}$")

# dsn_for/app_dsn_for rewrite the path and netloc of a URL; a libpq keyword/value DSN
# (`host=... dbname=...`) has neither, so both would silently produce garbage.
_VALID_SCHEMES = ("postgres", "postgresql")


def _psycopg() -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise StoreError(
            "psycopg is not installed, so the harness-store manager cannot connect. Install it "
            "with `uv sync --extra postgres`."
        ) from exc
    return psycopg


def _scheme(dsn: str) -> str:
    try:
        return urlsplit(dsn).scheme
    except Exception:
        return ""


def _redact(dsn: str) -> str:
    """Enough of a DSN to debug a connection failure by, and never the password.

    Falls back to a fixed placeholder on anything unparseable, rather than ever risking the
    raw DSN -- which is exactly the string a bad password lives in -- reaching an error message.
    """
    try:
        parts = urlsplit(dsn)
        if parts.scheme not in _VALID_SCHEMES:
            return "<dsn redacted>"
        return (
            f"host={parts.hostname or ''} port={parts.port or ''} "
            f"dbname={(parts.path or '').lstrip('/')}"
        )
    except Exception:
        return "<dsn redacted>"


def _temp_epoch(name: str) -> int | None:
    body = name[len(TEMP_PREFIX) :]
    try:
        # int(), not float(): float() accepts "inf"/"nan", either of which would make a
        # temp look newer than any cutoff and never get swept.
        return int(body.split("_", 1)[0])
    except ValueError:
        return None


class StoreManagerError(StoreError):
    """The harness-store server refused an operation, or is not reachable."""


class QuotaExceeded(StoreManagerError):
    """A cap the harness holds itself to, not a limit Postgres itself reports."""

    def __init__(self, kind: str, used: int, limit: int) -> None:
        self.kind = kind
        self.used = used
        self.limit = limit
        super().__init__(f"{kind} quota exceeded: {used} of {limit} already in use")


class StoreManager:
    """Every database-level operation against one harness-store Postgres server."""

    def __init__(
        self,
        dsn: str,
        *,
        copies_limit: int = 100,
        builds_limit: int = 4,
        clone_attempts: int = 3,
        statement_timeout: str = "30s",
    ) -> None:
        if clone_attempts < 1:
            raise StoreManagerError("clone_attempts must be at least 1")
        if _scheme(dsn) not in _VALID_SCHEMES:
            raise StoreManagerError(
                "HARNESS_STORE_DSN must be a URL-form DSN (postgresql://...); a libpq "
                "keyword/value DSN cannot be redacted or rewritten for dsn_for/app_dsn_for"
            )
        self.dsn = dsn
        self.copies_limit = copies_limit
        self.builds_limit = builds_limit
        self.clone_attempts = clone_attempts
        self.statement_timeout = statement_timeout

    @classmethod
    def from_env(cls) -> StoreManager:
        dsn = os.environ.get("HARNESS_STORE_DSN", "").strip()
        if not dsn:
            raise StoreManagerError("HARNESS_STORE_DSN is not set")
        return cls(dsn)

    # -- admin connection --------------------------------------------------------------

    @contextmanager
    def _admin(self):
        """A short-lived autocommit connection, generous enough not to die mid-clone.

        `harness_app` gets `self.statement_timeout` from `bootstrap`; this is the harness's
        own admin path, standing up or copying whole databases, and needs its own much longer
        ceiling rather than inheriting the app role's.
        """
        psycopg = _psycopg()
        try:
            connection = psycopg.connect(
                self.dsn,
                autocommit=True,
                connect_timeout=5,
                options="-c statement_timeout=300000",
            )
        except psycopg.Error as exc:
            raise StoreManagerError(
                f"could not connect to harness-store ({_redact(self.dsn)})"
            ) from exc
        try:
            yield connection
        finally:
            connection.close()

    def _list(self, conn: Any, prefix: str) -> list[str]:
        rows = conn.execute(
            "SELECT datname FROM pg_database WHERE left(datname, %s) = %s ORDER BY datname",
            (len(prefix), prefix),
        ).fetchall()
        return [row[0] for row in rows]

    def _validate(self, name: str, prefix: str = "") -> None:
        if not _NAME_RE.match(name):
            raise StoreManagerError(
                f"refusing to touch database {name!r}: not a harness-managed name"
            )
        if prefix and not name.startswith(prefix):
            raise StoreManagerError(f"{name!r} does not start with {prefix!r}")

    def _terminate(self, name: str) -> None:
        with self._admin() as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )

    def _has_activity(self, conn: Any, name: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM pg_stat_activity WHERE datname = %s LIMIT 1", (name,)
            ).fetchone()
            is not None
        )

    # -- setup -----------------------------------------------------------------------

    def bootstrap(self) -> None:
        """Make sure the app role exists with the password and limits it should have now."""
        psycopg = _psycopg()
        from psycopg import sql

        password = os.environ.get("HARNESS_STORE_APP_PASSWORD", "harness-app")
        with self._admin() as conn:
            # The password unavoidably appears in this statement's own text -- there is no
            # bind-parameter form of role DDL -- so it lands in the server log verbatim under
            # log_statement=ddl. That is a server-logging setting, not something this call
            # controls.
            try:
                conn.execute(
                    sql.SQL(
                        "CREATE ROLE harness_app LOGIN PASSWORD {} CONNECTION LIMIT 120"
                    ).format(sql.Literal(password))
                )
            except psycopg.errors.DuplicateObject:
                conn.execute(
                    sql.SQL(
                        "ALTER ROLE harness_app WITH LOGIN PASSWORD {} CONNECTION LIMIT 120"
                    ).format(sql.Literal(password))
                )
            # A runaway agent query must die on its own, not hold a copy's connection slot
            # forever.
            conn.execute(
                sql.SQL("ALTER ROLE harness_app SET statement_timeout = {}").format(
                    sql.Literal(self.statement_timeout)
                )
            )

    # -- building a master -------------------------------------------------------------

    def _load(self, temp: str, scripts: list[str], snapshot: Snapshot) -> None:
        psycopg = _psycopg()
        from psycopg import sql

        with psycopg.connect(self.dsn_for(temp), autocommit=True, connect_timeout=5) as connection:
            for script in scripts:
                if not script.strip():
                    continue
                connection.execute(script)

            # Granted on the temp db, before it becomes the master: CREATE DATABASE ...
            # TEMPLATE copies a database's privileges along with its data, so every clone
            # already has an app role that can read and write, with nothing granted per clone.
            connection.execute("GRANT USAGE ON SCHEMA public TO harness_app")
            connection.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                "TO harness_app"
            )
            connection.execute(
                "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO harness_app"
            )

            tables = _tables(connection)
            if not tables:
                return
            listed = sql.SQL(", ").join(sql.Identifier(table) for table in tables)
            connection.execute(
                sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(listed)
            )

            connection.execute("SET session_replication_role = replica")
            try:
                mode = connection.execute("SHOW session_replication_role").fetchone()[0]
                if mode != "replica":
                    raise StoreManagerError(
                        "session_replication_role did not take effect after being set; a "
                        "transaction-pooling proxy (e.g. PgBouncer) between the manager and "
                        "Postgres, which does not keep one session across statements, would "
                        "explain this"
                    )
                for table, rows in snapshot.rows.items():
                    if not rows or table not in tables:
                        continue
                    columns = list(rows[0])
                    types = _column_types(connection, table)
                    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(table),
                        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                        sql.SQL(", ").join(sql.Placeholder() * len(columns)),
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
                connection.execute("SELECT setval(%s, %s, true)", (f"public.{sequence}", value))

    def _verify(self, temp: str, snapshot: Snapshot) -> None:
        psycopg = _psycopg()
        from psycopg import sql

        with psycopg.connect(self.dsn_for(temp), autocommit=True, connect_timeout=5) as connection:
            tables = set(_tables(connection))
            mismatches = []
            for table, expected in snapshot.counts().items():
                if table in tables:
                    actual = connection.execute(
                        sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                    ).fetchone()[0]
                else:
                    actual = 0
                if actual != expected:
                    mismatches.append(f"{table}: temp has {actual}, snapshot has {expected}")
            if mismatches:
                raise StoreManagerError(
                    f"row counts did not match after materialize: {'; '.join(mismatches)}"
                )

    def materialize(self, master: str, scripts: list[str], snapshot: Snapshot | dict) -> None:
        if not isinstance(snapshot, Snapshot):
            snapshot = Snapshot(
                rows=dict(snapshot.get("rows") or {}),
                counters=dict(snapshot.get("counters") or {}),
            )
        self._validate(master, MASTER_PREFIX)
        psycopg = _psycopg()
        from psycopg import sql

        with self._admin() as conn:
            # hashtext is int4: two different master names could in principle hash to the
            # same lock key, which only ever serializes two unrelated builds that did not
            # need to wait on each other -- it cannot corrupt anything. Advisory locks are
            # scoped to the connection's current database, so every StoreManager pointed at
            # this server must share one admin DSN or two builders of the same master would
            # never actually contend for the same lock.
            conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (master,))
            try:
                exists = conn.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (master,)
                ).fetchone()
                if exists:
                    return

                used = len(self._list(conn, TEMP_PREFIX))
                if used >= self.builds_limit:
                    raise QuotaExceeded("build", used, self.builds_limit)

                temp = f"{TEMP_PREFIX}{int(time.time())}_{secrets.token_hex(4)}"
                stage = "create"
                created = False
                try:
                    conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(temp)))
                    created = True

                    stage = "load"
                    self._load(temp, scripts, snapshot)

                    stage = "verify"
                    self._verify(temp, snapshot)

                    stage = "rename"
                    self._terminate(temp)
                    try:
                        conn.execute(
                            sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                                sql.Identifier(temp), sql.Identifier(master)
                            )
                        )
                    except psycopg.errors.ObjectInUse:
                        self._terminate(temp)
                        conn.execute(
                            sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                                sql.Identifier(temp), sql.Identifier(master)
                            )
                        )
                except Exception as exc:
                    # Only ever drop a temp this call created: a DuplicateDatabase on the
                    # CREATE itself would mean the name collided with someone else's
                    # in-flight build, and force-dropping that would be dropping their work.
                    if created:
                        try:
                            self.drop(temp)
                        except Exception:
                            pass
                    raise StoreManagerError(
                        f"materialize {master} failed at {stage}: {exc}"
                    ) from exc
            finally:
                # The session lock dies with the connection regardless, so a failure to
                # unlock explicitly must never shadow the real error above.
                try:
                    conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (master,))
                except Exception:
                    pass

    # -- copies --------------------------------------------------------------------

    def clone(self, master: str, copy: str | None = None) -> str:
        psycopg = _psycopg()
        from psycopg import sql

        with self._admin() as conn:
            used = len(self._list(conn, COPY_PREFIX))
            if used >= self.copies_limit:
                raise QuotaExceeded("copy", used, self.copies_limit)

            copy = copy or f"{COPY_PREFIX}{secrets.token_hex(6)}"
            self._validate(copy, COPY_PREFIX)
            self._validate(master, MASTER_PREFIX)

            for attempt in range(1, self.clone_attempts + 1):
                try:
                    conn.execute(
                        sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                            sql.Identifier(copy), sql.Identifier(master)
                        )
                    )
                    return copy
                except psycopg.errors.ObjectInUse as exc:
                    if attempt >= self.clone_attempts:
                        raise StoreManagerError(
                            f"could not clone {master} after {attempt} attempts: "
                            "template still has active connections"
                        ) from exc
                    self._terminate(master)
                    time.sleep(0.2 * attempt)
                except psycopg.errors.InvalidCatalogName as exc:
                    raise StoreManagerError(
                        f"master {master} does not exist; materialize it first"
                    ) from exc
                except psycopg.errors.DuplicateDatabase:
                    return copy

    def drop(self, name: str) -> None:
        self._validate(name)
        psycopg = _psycopg()
        from psycopg import sql

        with self._admin() as conn:
            try:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    )
                )
            except psycopg.errors.ObjectInUse:
                self._terminate(name)
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    )
                )

    def sweep_temps(self, older_than_seconds: int = 3600) -> list[str]:
        """Drop `ht_` databases a build never finished renaming.

        A builder that dies between `CREATE DATABASE` and the rename leaks its temp forever
        otherwise; the name carries its own birth time because `pg_database` does not.
        """
        cutoff = time.time() - older_than_seconds
        with self._admin() as conn:
            names = self._list(conn, TEMP_PREFIX)
            stale = []
            for name in names:
                epoch = _temp_epoch(name)
                if epoch is not None and epoch >= cutoff:
                    continue
                # An in-flight build's temp must never be swept, no matter its age: this is
                # what stops a concurrent materialize (still inside _load/_verify) from
                # being pulled out from under itself by another caller's older_than_seconds=0.
                if self._has_activity(conn, name):
                    continue
                stale.append(name)

        dropped = []
        for name in stale:
            try:
                self.drop(name)
            except StoreManagerError:
                continue
            dropped.append(name)
        return dropped

    # -- helpers ---------------------------------------------------------------------

    def masters(self) -> list[str]:
        with self._admin() as conn:
            return self._list(conn, MASTER_PREFIX)

    def copies(self) -> list[str]:
        with self._admin() as conn:
            return self._list(conn, COPY_PREFIX)

    def dsn_for(self, name: str) -> str:
        parts = urlsplit(self.dsn)
        return urlunsplit(parts._replace(path=f"/{name}"))

    def app_dsn_for(self, name: str) -> str:
        parts = urlsplit(self.dsn)
        password = os.environ.get("HARNESS_STORE_APP_PASSWORD", "harness-app")
        netloc = f"harness_app:{password}@{parts.hostname or ''}"
        if parts.port:
            netloc += f":{parts.port}"
        return urlunsplit(parts._replace(netloc=netloc, path=f"/{name}"))
