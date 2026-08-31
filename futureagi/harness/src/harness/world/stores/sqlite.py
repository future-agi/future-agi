"""Records in a SQLite file, or in memory.

The default, because it needs nothing installed and nothing standing up. Saving to disk is a
copy of the database rather than a dump, so loading it back is exact and fast, which matters
because every scenario and every probe starts from that copy.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import Records, Snapshot, StoreError

# What SQLite hands out that is not itself a record. Only present once a table is declared
# AUTOINCREMENT, which is why its absence is normal rather than a gap.
COUNTERS = "sqlite_sequence"


class SqliteStore(Records):
    engine = "sqlite"
    key = "sqlite"
    FILE = "world.sqlite"

    def __init__(self, database: str | Path = ":memory:", **_ignored: Any) -> None:
        self.database = str(database)
        self.connection = sqlite3.connect(self.database, check_same_thread=False)
        self.connection.execute("PRAGMA foreign_keys = ON")

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        """Already up. Connecting is what ``__init__`` did, and there is no server to wait for."""

    def stop(self) -> None:
        self.close()

    def dsn(self) -> str:
        return f"sqlite:///{self.database}"

    # -- statements ------------------------------------------------------------------

    def execute(self, statement: str, params: Sequence[Any] = ()) -> int:
        cursor = self.connection.execute(statement, tuple(params))
        self.connection.commit()
        return cursor.rowcount

    def apply(self, script: str) -> None:
        """Several statements at once, which is how a schema or a seed arrives."""
        if not script.strip():
            return
        self.connection.executescript(script)
        self.connection.commit()

    # The name this had before a store was asked to speak more than SQL.
    script = apply

    def query(self, statement: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cursor = self.connection.execute(statement, tuple(params))
        columns = [column[0] for column in (cursor.description or [])]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    # -- records ---------------------------------------------------------------------

    def collections(self) -> list[str]:
        found = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [row["name"] for row in found]

    def holds(self, collection: str) -> bool:
        return bool(
            self.query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", [collection]
            )
        )

    def records(self, collection: str) -> list[dict[str, Any]]:
        return self.query(f'SELECT * FROM "{collection}"')

    def add(self, collection: str, record: Mapping[str, Any]) -> int:
        columns = ", ".join(f'"{name}"' for name in record)
        marks = ", ".join("?" for _ in record)
        return self.execute(
            f'INSERT INTO "{collection}" ({columns}) VALUES ({marks})', list(record.values())
        )

    def amend(
        self, collection: str, key: str, changes: Mapping[str, Any], *, by: str = ""
    ) -> int:
        if not by:
            raise KeyError(
                f"{collection} is a table, so changing a record needs the column it is keyed on"
            )
        sets = ", ".join(f'"{name}" = ?' for name in changes)
        return self.execute(
            f'UPDATE "{collection}" SET {sets} WHERE "{by}" = ?', [*changes.values(), key]
        )

    def remove(self, collection: str, key: str = "", *, by: str = "") -> int:
        if key and not by:
            raise KeyError(
                f"{collection} is a table, so removing one record needs the column it is keyed on"
            )
        sql = f'DELETE FROM "{collection}"' + (f' WHERE "{by}" = ?' if key else "")
        return self.execute(sql, [key] if key else [])

    # -- going back, in memory -------------------------------------------------------

    def freeze(self) -> Snapshot:
        return Snapshot(rows=self.state(), counters=self._counters())

    def restore(self, snapshot: Snapshot) -> None:
        """Put the rows back, and the counters behind them.

        Foreign keys are dropped for the duration rather than the tables being sorted into
        dependency order: any order is wrong for some schema, and a restore that fails on a
        schema the agent really has is worse than one that trusts the snapshot it took itself.
        """
        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            for name in self.collections():
                self.connection.execute(f'DELETE FROM "{name}"')
            for name, rows in snapshot.rows.items():
                for row in rows:
                    if not row:
                        continue
                    columns = ", ".join(f'"{column}"' for column in row)
                    marks = ", ".join("?" for _ in row)
                    self.connection.execute(
                        f'INSERT INTO "{name}" ({columns}) VALUES ({marks})', list(row.values())
                    )
            self._reinstate(snapshot.counters)
            self.connection.commit()
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")

    def _counters(self) -> dict[str, int]:
        if not self.holds(COUNTERS):
            return {}
        return {row["name"]: row["seq"] for row in self.query(f"SELECT name, seq FROM {COUNTERS}")}

    def _reinstate(self, counters: Mapping[str, int]) -> None:
        if not self.holds(COUNTERS):
            return
        self.connection.execute(f"DELETE FROM {COUNTERS}")
        for name, seq in counters.items():
            self.connection.execute(
                f"INSERT INTO {COUNTERS} (name, seq) VALUES (?, ?)", (name, seq)
            )

    def clear(self) -> None:
        """Empty every table, whatever references what.

        Foreign keys are suspended for the duration rather than the tables being sorted into
        dependency order. Deleting them one at a time in the wrong order fails on the referenced
        ones, and a caller that swallows those failures is left believing it emptied a store that
        still holds most of its data.
        """
        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            for name in self.collections():
                self.connection.execute(f'DELETE FROM "{name}"')
            self.connection.commit()
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")

    def take(self, held: str | Path) -> None:
        """Become a copy of another SQLite database: the agent's own.

        The whole file, schema and data together, rather than rows read out and written back. An
        agent's real store carries things a reconstruction loses: its exact types, its indexes,
        its keys, and every oddity in the data that its queries were actually written against.
        """
        origin = sqlite3.connect(f"file:{Path(held)}?mode=ro", uri=True)
        try:
            with self.connection:
                origin.backup(self.connection)
        finally:
            origin.close()

    # -- going back, on disk ---------------------------------------------------------

    def save_to(self, path: str | Path) -> None:
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        # Settled first: an open read transaction makes the copy fail, and a handler that only
        # read leaves one behind.
        self.connection.commit()
        held = root / self.FILE
        # A world whose live database already is the saved file has nothing to copy, and copying
        # it would be a backup onto its own file. SQLite retries a locked destination rather than
        # refusing, so that does not fail: it hangs, with no error and no timeout, and the build
        # stops dead somewhere nobody is looking.
        if self._same_file(held):
            return
        copy = sqlite3.connect(held)
        with copy:
            self.connection.backup(copy)
        copy.close()

    def _same_file(self, held: Path) -> bool:
        if self.database == ":memory:":
            return False
        live = Path(self.database)
        if not live.exists() or not held.exists():
            return str(live) == str(held)
        return live.samefile(held)

    def load_from(self, path: str | Path) -> None:
        held = Path(path) / self.FILE
        if not held.exists():
            raise StoreError(f"no saved store at {held}")
        if self.database == ":memory:":
            origin = sqlite3.connect(held)
            with self.connection:
                origin.backup(self.connection)
            origin.close()
            return
        self.connection.close()
        shutil.copyfile(held, self.database)
        self.connection = sqlite3.connect(self.database, check_same_thread=False)
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()
