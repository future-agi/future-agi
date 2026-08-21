"""The agent's own data, held where the agent holds it.

Plenty of real agents keep their state in memory, loaded from files their repository ships, and
they are not unusual. There is no engine to stand up for those, no port and no connection string.
Standing up a database for them and hoping the agent notices would be exactly the replication
this path exists to avoid.

So the store is the structure itself, and the agent's own loader is what fills it. The tools
under test then run against that structure the same way they run in production, because it *is*
the thing they run against: unmodified code, its real data, and a copy taken before each scenario
so the next one starts where the last one began.

With no loader given, this holds nothing at all and says so. That is the honest description of a
world whose records the agent's code keeps on itself rather than in anything a store can reach,
and it exists so such a world is not described as a database it does not have.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import Snapshot, StoreError

# Carried alongside a record whose group is keyed rather than listed, because the key is usually
# the id a check needs to name and rebuilding the group without it would throw it away.
ID = "_id"


class InProcessStore:
    """The agent's own in-memory data, as a store.

    ``loader`` is the agent's function, imported from the agent's repository and called, never
    reimplemented, so what is held is what the agent would hold on a cold start.
    """

    engine = "in_process"
    key = "in_process"
    # Deliberately not state.json, which the snapshot uses for the agent's own state object. Two
    # different things sharing one filename means whichever is written second wins, and the world
    # comes back with its records on the wrong side of the seam: the store empty, everything in
    # the agent's state, and the mutation gate then emptying a store that was never holding it.
    FILE = "collections.json"

    def __init__(
        self,
        database: str | Path = "",
        *,
        loader: Callable[[], dict[str, Any]] | None = None,
        module: str = "",
        function: str = "load_data",
        root: str | Path = "",
        **_ignored: Any,
    ) -> None:
        # Takes the same arguments as any other store and uses most of them only when there is a
        # loader, so opening one is the same call whichever kind it turns out to be.
        self.database = str(database or "")
        self.loader = loader
        self.module = module
        self.function = function
        self.root = str(root or "")
        self.data: dict[str, Any] = {}
        self._started = False

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        """Load the agent's data by calling the agent's own loader, if there is one."""
        if self._started or (self.loader is None and not self.module):
            return
        if self.loader is None:
            self.loader = self._imported()
        loaded = self.loader()
        if not isinstance(loaded, dict):
            raise StoreError(
                f"{self.function} returned {type(loaded).__name__}, not a dict of named groups, "
                "so there is nothing a check could read by name"
            )
        self.data = loaded
        self._started = True

    def _imported(self) -> Callable[[], dict[str, Any]]:
        """The agent's loader, imported from the agent's repository.

        Deliberately an import of their code rather than a reimplementation of it. If it will not
        import, that is worth stopping for: the alternative is inventing data and grading the
        agent against a world it has never seen.
        """
        import importlib
        import sys

        if self.root and self.root not in sys.path:
            sys.path.insert(0, self.root)
        try:
            found = importlib.import_module(self.module)
        except ImportError as exc:
            raise StoreError(
                f"cannot import {self.module!r} from {self.root or 'sys.path'}: {exc}. The "
                "agent's own dependencies have to be importable for its loader to run."
            ) from exc
        loader = getattr(found, self.function, None)
        if not callable(loader):
            raise StoreError(f"{self.module}.{self.function} is not a function")
        return loader

    def stop(self) -> None:
        self.data = {}
        self._started = False

    def dsn(self) -> str:
        """Nothing connects to this, which is the point.

        Reported rather than raised: a store with no address is a fact about this kind of agent,
        not a failure, and it is recorded so nothing later goes looking for a connection string
        that was never going to exist.
        """
        return "inprocess://"

    # -- statements ------------------------------------------------------------------

    def apply(self, script: str) -> None:
        """Run a snippet against the data, with ``data`` in scope and nothing else.

        How a seed is expressed for a store with no query language: the same Python the agent's
        own code would use to reach into its structures.
        """
        if not script.strip():
            return
        namespace: dict[str, Any] = {"data": self.data, "json": json}
        try:
            exec(compile(script, "<seed>", "exec"), namespace)  # nosec B102
        except Exception as exc:  # noqa: BLE001 - the caller's snippet, reported as given
            raise StoreError(f"{type(exc).__name__}: {exc}") from exc

    def execute(self, statement: str, params: Sequence[Any] = ()) -> int:
        raise StoreError(
            "this agent keeps its state in its own code, so there is no query language to run "
            "statements in. Change the world through the agent's own tools, or through "
            "world.put, world.change and world.drop."
        )

    def query(self, statement: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return []

    # -- records ---------------------------------------------------------------------

    def collections(self) -> list[str]:
        return sorted(self.data)

    def holds(self, collection: str) -> bool:
        return collection in self.data

    def records(self, collection: str) -> list[dict[str, Any]]:
        return self._rows(self.data.get(collection))

    def state(self) -> dict[str, list[dict[str, Any]]]:
        """Every group and its records, in the shape the checks already expect.

        The agent's structures are usually keyed by id rather than listed, so a mapping becomes
        records with the key carried along. Without that a check counting records in a group
        would be counting nothing, and the id it needs to name would have been thrown away.
        """
        return {name: self._rows(group) for name, group in self.data.items()}

    @staticmethod
    def _rows(group: Any) -> list[dict[str, Any]]:
        if isinstance(group, dict):
            return [
                {ID: key, **value} if isinstance(value, dict) else {ID: key, "value": value}
                for key, value in group.items()
            ]
        if isinstance(group, list):
            return [row if isinstance(row, dict) else {"value": row} for row in group]
        if group is None:
            return []
        return [{"value": group}]

    def start_collection(self, collection: str, *, keyed: bool = False) -> None:
        """Make a collection that does not exist yet.

        For an agent with no store of its own, every collection is one the harness invents, so
        there is nothing to declare them in advance the way a schema does for a database.
        """
        if collection not in self.data:
            self.data[collection] = {} if keyed else []

    def add(self, collection: str, record: Mapping[str, Any]) -> int:
        group = self.data.get(collection)
        if isinstance(group, list):
            group.append(dict(record))
            return 1
        if isinstance(group, dict):
            written = dict(record)
            identifier = written.pop(ID, None)
            if identifier is None:
                raise KeyError(
                    f"{collection} is keyed, so a new record needs its key given as {ID!r}"
                )
            group[identifier] = written
            return 1
        raise KeyError(f"no group {collection!r} here to add to")

    def amend(
        self, collection: str, key: str, changes: Mapping[str, Any], *, by: str = ""
    ) -> int:
        group = self.data.get(collection)
        if isinstance(group, dict) and not by:
            if key not in group:
                return 0
            group[key].update(dict(changes))
            return 1
        for row in self._writable(collection, group, key, by):
            row.update(dict(changes))
        return len(self._writable(collection, group, key, by))

    def remove(self, collection: str, key: str = "", *, by: str = "") -> int:
        group = self.data.get(collection)
        if isinstance(group, dict):
            if not key:
                gone = len(group)
                group.clear()
                return gone
            if by:
                matched = [name for name, row in group.items() if _reads(row, by) == key]
            else:
                matched = [key] if key in group else []
            for name in matched:
                group.pop(name, None)
            return len(matched)
        if isinstance(group, list):
            if not key:
                gone = len(group)
                group.clear()
                return gone
            if not by:
                raise KeyError(
                    f"{collection} is a list, so removing one record needs the field it is keyed on"
                )
            kept = [row for row in group if _reads(row, by) != key]
            gone = len(group) - len(kept)
            group[:] = kept
            return gone
        raise KeyError(f"no group {collection!r} here to remove from")

    def _writable(
        self, collection: str, group: Any, key: str, by: str
    ) -> list[dict[str, Any]]:
        if group is None:
            raise KeyError(f"no group {collection!r} here to change")
        if not by:
            raise KeyError(
                f"{collection} is a list, so changing a record needs the field it is keyed on"
            )
        if isinstance(group, dict):
            # A keyed group stores the key as the mapping's key, because `add` pops ``_id`` out
            # of the record to put it there. So asking to match on ``_id`` finds nothing, changes
            # nothing, and returns zero, which a scenario's setup does not look at: the run is
            # then graded against a world that was never set up. The key is answered here as
            # though it were still a field, which is what whoever wrote it meant.
            if by == ID and key in group:
                row = group[key]
                return [row] if isinstance(row, dict) else []
            found: Any = group.values()
        else:
            found = group
        return [row for row in found if isinstance(row, dict) and _reads(row, by) == key]

    # -- going back ------------------------------------------------------------------

    def clear(self) -> None:
        """Empty every group, keeping its shape: the agent's own code indexes into these."""
        for name, group in self.data.items():
            if isinstance(group, dict):
                group.clear()
            elif isinstance(group, list):
                group.clear()
            else:
                self.data[name] = None

    def freeze(self) -> Snapshot:
        """A deep copy. Nothing sits behind these records, so there are no counters to carry."""
        return Snapshot(rows=copy.deepcopy(self.state()), counters={})

    def restore(self, snapshot: Snapshot) -> None:
        """Put the structure back the way the agent's loader left it.

        Rebuilt from the records rather than kept as a second copy, so restore is checked against
        exactly what ``state`` reports: the thing the gate compares and the thing a check reads
        are then the same thing, and cannot drift apart.
        """
        rebuilt: dict[str, Any] = {}
        for name, rows in snapshot.rows.items():
            original = self.data.get(name)
            if isinstance(original, list):
                rebuilt[name] = [
                    row["value"] if set(row) == {"value"} else dict(row)
                    for row in copy.deepcopy(rows)
                ]
                continue
            keyed: dict[str, Any] = {}
            for row in copy.deepcopy(rows):
                identifier = row.pop(ID, None)
                if identifier is None:
                    continue
                keyed[identifier] = row.get("value") if set(row) == {"value"} else row
            rebuilt[name] = keyed
        # A group the snapshot does not mention is emptied, not carried over: restore has to be
        # able to reproduce a snapshot that holds nothing, or the gate cannot empty the store to
        # find out whether the checks actually bite. The key itself stays, with its original
        # shape, because the agent's own code indexes into it and would not survive its absence.
        for name, group in self.data.items():
            if name not in rebuilt:
                rebuilt[name] = [] if isinstance(group, list) else {}
        self.data.clear()
        self.data.update(rebuilt)

    def save_to(self, path: str | Path) -> None:
        if not self.data:
            return
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        (root / self.FILE).write_text(
            json.dumps(self.data, indent=2, default=str), encoding="utf-8"
        )

    def load_from(self, path: str | Path) -> None:
        held = Path(path) / self.FILE
        if not held.exists():
            return
        self.data = json.loads(held.read_text(encoding="utf-8"))
        self._started = True

    def close(self) -> None:
        return None


def _reads(row: Any, field: str) -> Any:
    return row.get(field) if isinstance(row, dict) else None
