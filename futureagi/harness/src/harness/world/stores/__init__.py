"""The stores the harness can stand up for an agent, and what every one of them owes a world.

A store is the thing underneath an agent's tools: whatever really holds the records its queries
run against. It is never asked to execute a tool. It is asked to exist, to hold data, to say what
it holds, to let a scenario change a little of it, and to go back to how it was.

Which engine gets stood up is read off the agent, never chosen for it. Postgres and ClickHouse
disagree about dialect, types and what a transaction even means, so testing one against the other
grades an agent on queries it never runs. An engine the harness cannot stand up is an answer, not
a reason to substitute something that merely resembles it.

What a store owes falls into four groups, and most stores care about three:

    lifecycle      start, stop, dsn        stand it up and say where it is
    contents       apply, execute, query   statements, in whatever this engine speaks
    records        collections, holds, records, add, amend, remove
    going back     freeze, restore         between scenarios
                   save_to, load_from      to and from disk, for the base world

The records group is what keeps a scenario from ever naming a store. `world.put`, `world.change`
and `world.drop` land here, so the same scenario runs against SQLite, against Postgres in a
container, or against a structure the agent's own code holds, without a line of it changing.
`state()` comes free from that group, and `Records` provides it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable


class StoreError(RuntimeError):
    """The store could not be stood up, or could not answer.

    Distinct from anything the agent did. A store that will not start is our problem and should
    stop the run loudly, because every result after it would be measured against something that
    is not there.
    """


@dataclass
class Snapshot:
    """Everything a store held at one moment, and what it takes to put it back.

    ``rows`` is kept in the shape ``state()`` reports, so a check written against a world's state
    reads a snapshot without knowing which engine produced it.

    ``counters`` is whatever an engine hands out that is not itself a record: a Postgres sequence,
    a MySQL auto-increment, anything that keeps counting after the rows are gone. Restoring rows
    without restoring these gives the next scenario ids that continue from the last one, and a
    check naming a specific id then fails for a reason that has nothing to do with the agent.
    Engines that hand out nothing of the sort leave it empty, which is not a gap.
    """

    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {name: len(rows) for name, rows in self.rows.items()}


@runtime_checkable
class Store(Protocol):
    """A running store the world's records live in."""

    # What this engine is. ``key`` is the same thing under the name a saved manifest already
    # uses, so a world written before this split still reopens.
    engine: str
    key: str

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def dsn(self) -> str: ...

    # Statements the harness wrote, in whatever this store speaks.
    def apply(self, script: str) -> None: ...
    def execute(self, statement: str, params: Sequence[Any] = ()) -> int: ...
    def query(self, statement: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...

    # What a scenario and its checks need without writing a statement themselves.
    def collections(self) -> list[str]: ...
    def holds(self, collection: str) -> bool: ...
    def records(self, collection: str) -> list[dict[str, Any]]: ...
    def state(self) -> dict[str, list[dict[str, Any]]]: ...
    def add(self, collection: str, record: Mapping[str, Any]) -> int: ...
    def amend(
        self, collection: str, key: str, changes: Mapping[str, Any], *, by: str = ""
    ) -> int: ...
    def remove(self, collection: str, key: str = "", *, by: str = "") -> int: ...

    # Between scenarios, in memory.
    def freeze(self) -> Snapshot: ...
    def restore(self, snapshot: Snapshot) -> None: ...

    # To and from disk, so the base world outlives the process that built it.
    def save_to(self, path: str | Path) -> None: ...
    def load_from(self, path: str | Path) -> None: ...

    def close(self) -> None: ...


class Records:
    """``state`` from the record methods, for any store that has them.

    Kept in one place because the two would otherwise drift, and they are the pair the gates
    compare: the bite gate empties a store and reads ``state``, while a scenario changes it
    through ``add`` and ``amend``. If those disagree about what a collection contains, a check
    passes against something no scenario can produce.
    """

    def state(self) -> dict[str, list[dict[str, Any]]]:
        return {name: self.records(name) for name in self.collections()}  # type: ignore[attr-defined]


class Held:
    """The record methods, and disk, for a store that already answers ``state``.

    The mirror of ``Records``, for stores built the other way round: a container store reads
    everything it holds in one go, and the per-collection questions follow from that. Saving to
    disk is the snapshot as JSON, which works for any engine because a snapshot is already the
    engine-independent shape.

    ``add``, ``amend`` and ``remove`` are not derivable and are left to the engine. A store
    without them refuses loudly rather than silently doing nothing, because the alternative is a
    scenario whose setup appears to run and changes nothing, and a run then graded against a
    world that was never set up.
    """

    engine: str = ""

    @property
    def key(self) -> str:
        return self.engine

    def collections(self) -> list[str]:
        return sorted(self.state())  # type: ignore[attr-defined]

    def holds(self, collection: str) -> bool:
        return collection in self.state()  # type: ignore[attr-defined]

    def records(self, collection: str) -> list[dict[str, Any]]:
        return self.state().get(collection, [])  # type: ignore[attr-defined]

    def execute(self, statement: str, params: Sequence[Any] = ()) -> int:
        self.apply(statement)  # type: ignore[attr-defined]
        return 0

    def query(self, statement: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        raise StoreError(
            f"{self.engine} does not read back arbitrary statements. Read what it holds with "
            "records() or state()."
        )

    def add(self, collection: str, record: Mapping[str, Any]) -> int:
        raise StoreError(_UNWRITABLE.format(engine=self.engine, verb="add to"))

    def amend(
        self, collection: str, key: str, changes: Mapping[str, Any], *, by: str = ""
    ) -> int:
        raise StoreError(_UNWRITABLE.format(engine=self.engine, verb="change"))

    def remove(self, collection: str, key: str = "", *, by: str = "") -> int:
        raise StoreError(_UNWRITABLE.format(engine=self.engine, verb="remove from"))

    def clear(self) -> None:
        """Empty it, by restoring a snapshot that holds nothing."""
        self.restore(Snapshot())  # type: ignore[attr-defined]

    def save_to(self, path: str | Path) -> None:
        import json

        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        frozen = self.freeze()  # type: ignore[attr-defined]
        (root / SAVED).write_text(
            json.dumps(
                {
                    # The schema as the scripts that made it, because the rows alone cannot
                    # come back: a fresh engine has no tables to put them in.
                    "schema": list(getattr(self, "applied", [])),
                    "rows": frozen.rows,
                    "counters": frozen.counters,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    def load_from(self, path: str | Path) -> None:
        import json

        held = Path(path) / SAVED
        if not held.exists():
            raise StoreError(f"no saved store at {held}")
        kept = json.loads(held.read_text(encoding="utf-8"))
        for script in kept.get("schema") or []:
            self.apply(script)  # type: ignore[attr-defined]
        self.restore(Snapshot(rows=kept.get("rows") or {}, counters=kept.get("counters") or {}))  # type: ignore[attr-defined]

    def close(self) -> None:
        self.stop()  # type: ignore[attr-defined]


# What a saved container store is written as. Not the engine's own dump format: a snapshot is
# already engine-independent, and a dump would tie the saved world to the version that wrote it.
SAVED = "store.json"

_UNWRITABLE = (
    "{engine} has no way to {verb} a collection one record at a time, so a scenario cannot set "
    "up on it. Give the store add, amend and remove in this engine's own language."
)

_REGISTRY: dict[str, Callable[..., Store]] = {}

# Names people and manifests actually write, pointing at the engine they mean. Kept explicit
# rather than normalised in code, because guessing which engine an unrecognised word meant is
# how an agent ends up graded against the wrong one.
_ALIASES = {
    "": "in_process",
    "none": "in_process",
    "memory": "in_process",
    "in-memory": "in_process",
    "inprocess": "in_process",
}


def register_store(engine: str, factory: Callable[..., Store]) -> None:
    """Teach the harness an engine. A class and this line.

    The cost of this line is what decides whether "whatever the agent uses" is real or an
    aspiration, which is why the shared work lives in ``ContainerStore`` and an engine
    contributes only what genuinely differs.
    """
    _REGISTRY[engine] = factory


def supported() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def resolve(engine: str = "", **options: Any) -> Store:
    """The store for an engine, or a refusal naming what there is.

    Deliberately not a fallback. An agent on an engine nobody has taught the harness to run is a
    gap worth reporting, and quietly handing it a different store would produce a green suite
    about queries the agent never executes.
    """
    named = (engine or "").strip().lower()
    named = _ALIASES.get(named, named)
    if named not in _REGISTRY:
        raise StoreError(
            f"no store for engine {named!r}; the harness can stand up "
            f"{', '.join(supported()) or 'nothing yet'}. Adding one is a class with the record "
            "methods and a call to register_store, or write_store_ops for an engine in a container."
        )
    return _REGISTRY[named](**options)


# The name the rest of the harness has always called this by.
open_store = resolve


from .inprocess import InProcessStore  # noqa: E402
from .sqlite import SqliteStore  # noqa: E402

register_store(SqliteStore.engine, SqliteStore)
register_store(InProcessStore.engine, InProcessStore)

from .container import ContainerStore, docker, strays  # noqa: E402
from .postgres import PostgresStore  # noqa: E402

# Postgres is registered as the worked example, not as the supported list. An engine the harness
# has never seen is meant to be written at build time against ``ContainerStore`` and proved by
# the gates, rather than waiting for someone to ship a class for it.
register_store(PostgresStore.engine, PostgresStore)

__all__ = [
    "ContainerStore",
    "InProcessStore",
    "PostgresStore",
    "Records",
    "Snapshot",
    "SqliteStore",
    "Store",
    "StoreError",
    "docker",
    "open_store",
    "register_store",
    "resolve",
    "strays",
    "supported",
]
