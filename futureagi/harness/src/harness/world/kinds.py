"""What a kind of world has to be able to do, so the checks do not care which kind it is.

A world backed by a database and a world backed by a page are different in every detail and the
same in what matters: something either exists in them or does not, an action either takes effect
or is refused, and what an action leaves behind is either carried or lost. Those are the things
worth checking, and none of them mention a table.

So the checks are written against this, and a kind supplies the four answers only it can give:
what exists, what the mutable state is, how to freeze it, and how to put it back. Adding a kind
is a class and a registration; nothing in the gate changes.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .runtime import GeneratedWorld


def _rows(collection: Any) -> list[Any]:
    """One collection's members, whichever shape it is kept in.

    A table gives a list of row mappings. A collection the agent's own code owns is as often a
    mapping keyed by identifier, and iterating that yields keys rather than records, which is how
    a check written for one shape silently reads the other.
    """
    if isinstance(collection, dict):
        return list(collection.values())
    if isinstance(collection, (list, tuple)):
        return list(collection)
    return [collection]


def _identifiers(state: Mapping[str, Any]) -> set[str]:
    found: set[str] = set()
    for name, collection in state.items():
        if isinstance(collection, dict):
            # The keys of a mapping are identifiers in their own right, and usually the ones a
            # tool is called with.
            found.update(str(key) for key in collection if isinstance(key, str) and key)
        for row in _rows(collection):
            if isinstance(row, Mapping):
                found.update(
                    value for value in row.values() if isinstance(value, str) and value
                )
            elif isinstance(row, str) and row:
                found.add(row)
    return found


def _sizes(state: Mapping[str, Any]) -> dict[str, int]:
    return {name: len(_rows(collection)) for name, collection in state.items()}


@runtime_checkable
class WorldKind(Protocol):
    """The per-kind half of a world. The shared half is ``GeneratedWorld``."""

    key: str
    label: str

    def values_present(self, world: GeneratedWorld) -> set[str]:
        """Every identifier this world contains.

        Answers whether the catalogue is complete: a contract that permits a value the world has
        never heard of produces a tool that refuses forever, which is indistinguishable from a
        tool being correctly strict.
        """

    def mutable_state(self, world: GeneratedWorld) -> dict[str, int]:
        """Named parts of the world that an action can change, and how much is in each.

        Used for two things: noticing that a saved world still holds whatever the builder was
        experimenting with, and noticing that a sequence of actions left nothing behind.
        """

    def describe(self, world: GeneratedWorld) -> str:
        """A short human-readable account of what is in the world."""


class SqliteWorld:
    """A world whose state is rows in tables. Tool APIs, and anything with a data store."""

    key = "sqlite"
    label = "a database behind the agent's tools"

    def values_present(self, world: GeneratedWorld) -> set[str]:
        return _identifiers(world.state())

    def mutable_state(self, world: GeneratedWorld) -> dict[str, int]:
        return _sizes(world.state())

    def describe(self, world: GeneratedWorld) -> str:
        counts = self.mutable_state(world)
        return ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))


class BrowserWorld:
    """A world whose state is pages and the actions that change them.

    ALK already carries a browser environment fed DOM snapshots and action fixtures, and it
    already refuses a click matching no fixture. So this is the same move as the database kind:
    generate instances of a shape that exists, rather than invent a mechanism.

    What exists here is the set of things an agent can reach, which is selectors and URLs rather
    than ids; what changes is which snapshot is current and what the actions have mutated.
    """

    key = "browser"
    label = "pages and the actions that change them"

    def values_present(self, world: GeneratedWorld) -> set[str]:
        present: set[str] = set()
        for collection in world.state().values():
            for row in _rows(collection):
                if not isinstance(row, Mapping):
                    continue
                for column in ("url", "selector", "id", "name", "action"):
                    value = row.get(column)
                    if isinstance(value, str) and value:
                        present.add(value)
        return present

    def mutable_state(self, world: GeneratedWorld) -> dict[str, int]:
        return _sizes(world.state())

    def describe(self, world: GeneratedWorld) -> str:
        counts = self.mutable_state(world)
        return ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))


class InProcessWorld:
    """A world whose state the agent's own code holds, rather than a store we stood up.

    This is what an adopted world usually is: the agent's tools were written to act on a structure
    they build themselves, so the world holds that structure and does not interpret it.
    """

    key = "in_process"
    label = "state the agent's own code keeps"

    def values_present(self, world: GeneratedWorld) -> set[str]:
        return _identifiers(world.state())

    def mutable_state(self, world: GeneratedWorld) -> dict[str, int]:
        return _sizes(world.state())

    def describe(self, world: GeneratedWorld) -> str:
        counts = self.mutable_state(world)
        return ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))


_REGISTRY: dict[str, Callable[[], WorldKind]] = {
    SqliteWorld.key: SqliteWorld,
    BrowserWorld.key: BrowserWorld,
    InProcessWorld.key: InProcessWorld,
}


def register_kind(key: str, factory: Callable[[], WorldKind]) -> None:
    """Add a kind of world. Computer use, a filesystem, a queue: a class and this line."""
    _REGISTRY[key] = factory


def resolve(key: str) -> WorldKind:
    if key not in _REGISTRY:
        raise NotImplementedError(
            f"no world kind {key!r}; registered kinds are {', '.join(sorted(_REGISTRY))}"
        )
    return _REGISTRY[key]()


def supported() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def for_contract(contract: Any) -> WorldKind:
    """The kind of world an agent needs, from what the contract says it is.

    Chosen rather than guessed at build time: an agent reachable by voice and by browser is one
    agent with two runtimes, and which world to build is a decision about what is being tested.
    """
    # What the store is, when the contract knows. That is the honest source: how a person reaches
    # the agent says nothing about what its tools read and write, and an agent whose state lives
    # in its own process is not a database however it is spoken to.
    store = getattr(contract, "data_store", None)
    named = str(getattr(store, "kind", "") or "").lower()
    if named in _REGISTRY:
        return resolve(named)
    if named in ("in_process", "memory", "in-memory", "none", ""):
        if named:
            return resolve("in_process")
    modality = str(getattr(contract, "modality", "") or "").lower()
    if modality in ("browser", "computer_use", "cua"):
        return resolve("browser")
    return resolve("sqlite")
