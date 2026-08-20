"""The runtime a generated world runs on.

A generated world is a database plus one handler per tool. The handler decides what a call does;
this decides what a handler is allowed to be, what happens when one fails, and what the world
looks like afterwards. Keeping that here means a generated file stays small enough to read and
correct, and the parts that must be exact are not regenerated every time.

The contract with the rest of the platform is ``EnvironmentAdapter``: ``reset`` publishes the
tools and the starting state, ``handle_tool_call`` executes one call, and the state afterwards is
what the checks grade. A world is therefore drivable by any loop that already drives an
environment, which is the whole reason we generate against this interface rather than inventing
one.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..environment import EnvironmentAdapter, EnvironmentSnapshot, ToolExecutionResult


class ToolError(Exception):
    """A tool refusing for a real reason the agent should see and recover from.

    Distinct from a crash. A refusal is the world working: the id does not exist, the item is
    unavailable, the argument is outside what the tool accepts. A crash is our bug, and the two
    must never look the same to a caller deciding whether the agent behaved correctly.
    """


@dataclass
class Db:
    """The handle a handler gets. Deliberately small: query, execute, one.

    Handlers get a database, not a filesystem and not a network. Anything a handler can reach is
    something a generated world could depend on, and a world that depends on the outside is not
    reproducible.
    """

    # Whatever this agent's records live in. A handler's statements are written in that store's
    # own language, so this passes them through rather than interpreting them.
    store: Any
    # The agent's own state object, where its tools keep what they act on in memory rather than
    # in a database. Their code is the thing that shapes it, so the world holds it and does not
    # interpret it: freezing it is a serialisation, and restoring it is the reverse.
    state: Any = None

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return self.store.query(sql, params)

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return self.store.execute(sql, params)

    # -- reading without a query language ---------------------------------------------
    #
    # Not every agent has a database. One whose state lives in services and files gets a world
    # whose collections the harness invented, and there is no dialect to write a SELECT in. A
    # handler that could only issue SQL would be unable to read the world it was given at all.

    def collections(self) -> list[str]:
        """Every collection this world holds, by name."""
        return list(self.store.collections())

    def records(self, collection: str) -> list[dict[str, Any]]:
        """Every record in one collection. The store-agnostic way to read."""
        return list(self.store.records(collection))

    def find(self, collection: str, **fields: Any) -> list[dict[str, Any]]:
        """The records in a collection whose fields all match what was asked for."""
        return [
            record
            for record in self.records(collection)
            if all(record.get(field) == value for field, value in fields.items())
        ]

    def add(self, collection: str, record: Mapping[str, Any]) -> int:
        return self.store.add(collection, record)


def settled(value: Any) -> Any:
    """The value, with a coroutine run to completion first.

    A tool the agent wrote may well be async: every framework-decorated tool is. Handlers here
    are synchronous, and the build stage is itself inside a running event loop, so ``asyncio.run``
    cannot be called directly. Running it on a worker thread gives it a loop of its own and keeps
    the handler contract unchanged.
    """
    import asyncio
    import inspect

    if not inspect.isawaitable(value):
        return value
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, value).result()


def _is_refusal(raised: BaseException) -> bool:
    """Whether an exception is the world saying no, rather than the world falling over.

    Matched by name as well as by identity. A generated handler often declares its own
    ``ToolError`` rather than using the one already in scope, which is defensive and sensible
    from where it sits, and would otherwise turn every deliberate refusal into a reported crash.
    Relying on an invisible convention being followed is not a way to decide something this
    load-bearing.
    """
    if isinstance(raised, ToolError):
        return True
    return any(base.__name__ == "ToolError" for base in type(raised).__mro__)


@dataclass
class Call:
    """One tool call and what the world did with it."""

    name: str
    arguments: dict[str, Any]
    result: Any = None
    ok: bool = True
    error: str = ""
    refused: bool = False
    # When it happened, seconds since the epoch. What lets a recording and a list of calls be
    # read as one thing: without it the UI can show what the agent did but not when, and "when"
    # is the whole question for a spoken run.
    at: float = 0.0


class GeneratedWorld(EnvironmentAdapter):
    """A database-backed world whose tools are generated per agent.

    Subclasses declare ``name``, ``tools`` and ``handlers``. Everything about execution,
    refusal, and state reporting is here so that a generated subclass carries only the parts
    that are specific to one agent.
    """

    name = "generated"
    tools: list[dict[str, Any]] = []
    handlers: dict[str, str] = {}

    # Where the agent's own code lives, so a handler that binds to one of its tools can import
    # it. Empty when the world implements the tools itself.
    source_root: str = ""
    # The agent's own in-memory state, for tools that take it as an argument instead of
    # connecting to anything. Held opaquely: their code gives it shape.
    state_object: Any = None
    # How this agent says no in a returned value. A tool that answers "Error: no such order" is
    # refusing, and recording that as a success would hide the very behaviour worth testing.
    refusal_signature: str = ""

    def __init__(
        self, database: str | Path = ":memory:", *, store: Any = None, kind: str = ""
    ) -> None:
        from .stores import open_store

        self.database = str(database)
        # Where this agent's records live. Given rather than assumed, because the harness
        # writes statements in whatever the agent's own store speaks and they have to reach
        # it. A world with no store of its own gets one that says so.
        self.store = store or open_store(kind or "sqlite", database=self.database)
        # A world is usable as soon as it is constructed.  SQLite happens to open its
        # connection in __init__, which used to hide this missing lifecycle step; container
        # stores (Postgres, MySQL, …) do not have an address until start() is called.
        # Starting here also makes snapshot.restore() safe, since load_from() immediately
        # writes the frozen rows into the newly-created store.
        self.store.start()
        self.calls: list[Call] = []

    @property
    def connection(self) -> Any:
        """The store's own connection, where it has one.

        Kept so that code written when every world was a SQLite file still works. Anything
        new should go through the store, or through put, change and drop, so it holds for a
        world whose records are somewhere else.
        """
        found = getattr(self.store, "connection", None)
        if found is None:
            raise AttributeError(
                f"this world's store ({getattr(self.store, 'key', 'unknown')}) has no "
                "connection. Use the store, or put, change and drop."
            )
        return found

    def reach(self, source_root: str) -> None:
        """Make the agent's own code importable, so a binding can call it rather than copy it.

        Two directories go on the path, not one. An agent pointed at flatly is imported from where
        it sits, but an agent laid out as a package is nearly always pointed at the part under
        test rather than at its root: `tau_bench/envs/retail` is where the agent is, while
        `tau_bench.envs.retail.data` only resolves from the repository above it. Adding just the
        directory named makes every import the agent's own code writes fail, which arrives as
        "No module named tau_bench" and reads as the package being absent rather than as us
        having pointed at the middle of it.

        The package root is found the way Python finds it: walk up while each directory is itself
        a package, and stop at the first that is not.
        """
        import sys

        self.source_root = str(source_root or "")
        for path in self._import_roots(self.source_root):
            if path not in sys.path:
                sys.path.insert(0, path)

    @staticmethod
    def _import_roots(source_root: str) -> list[str]:
        """Where the agent's code can be imported from: where it sits, and its package root."""
        if not source_root:
            return []
        roots = [source_root]
        here = Path(source_root)
        # Bounded by the filesystem root: `parents` stops there, so a source outside any package
        # simply never enters the loop.
        while (here / "__init__.py").exists() and here.parent != here:
            here = here.parent
            if str(here) not in roots:
                roots.append(str(here))
        return roots

    # -- EnvironmentAdapter ----------------------------------------------------------

    def reset(self, **_context: Any) -> EnvironmentSnapshot:
        self.calls = []
        return EnvironmentSnapshot(tools=list(self.tools), state=self.state())

    def observe(self, **_context: Any) -> EnvironmentSnapshot:
        return EnvironmentSnapshot(tools=list(self.tools), state=self.state())

    def handle_tool_call(
        self, tool_call: Mapping[str, Any], **_context: Any
    ) -> ToolExecutionResult | None:
        name = str(
            tool_call.get("name") or (tool_call.get("function") or {}).get("name") or ""
        )
        call_id = tool_call.get("id") or tool_call.get("tool_call_id")
        arguments = tool_call.get("arguments") or tool_call.get("args") or {}
        if not isinstance(arguments, Mapping):
            arguments = {}

        call = self.call(name, arguments)
        content = (
            json.dumps(call.result, default=str)
            if not isinstance(call.result, str)
            else call.result
        )
        return ToolExecutionResult(
            tool_call_id=call_id,
            tool_name=name or "unknown",
            content=call.error if not call.ok else content,
            result=call.result,
            success=call.ok,
            error=call.error or None,
            state_updates=self.state(),
        )

    # -- execution -------------------------------------------------------------------

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> Call:
        """Execute one call and record it. Never raises: a failure is an outcome, not an event.

        An unknown tool is a refusal rather than a silent success. An agent reaching for a tool
        that does not exist is a finding, and answering it with an acknowledgement is how a test
        passes something it should have caught.
        """
        args = dict(arguments or {})
        if name not in self.handlers:
            return self._record(
                Call(
                    name=name,
                    arguments=args,
                    ok=False,
                    refused=True,
                    error=(
                        f"no such tool {name!r}; this agent has "
                        f"{', '.join(sorted(self.handlers)) or 'none'}"
                    ),
                )
            )

        namespace: dict[str, Any] = {"ToolError": ToolError, "json": json}
        try:
            exec(compile(self.handlers[name], f"<handler:{name}>", "exec"), namespace)
            handle = namespace.get("handle")
            if not callable(handle):
                raise RuntimeError("handler defines no handle(args, db)")
            value = handle(args, Db(self.store, self.state_object))
        except Exception as raised:
            if _is_refusal(raised):
                return self._record(
                    Call(
                        name=name,
                        arguments=args,
                        ok=False,
                        refused=True,
                        error=str(raised),
                    )
                )
            # Our bug, not the agent's. Labelled differently so a run is never scored
            # against a world that fell over.
            return self._record(
                Call(
                    name=name,
                    arguments=args,
                    ok=False,
                    error=f"{type(raised).__name__}: {raised}",
                )
            )
        # A tool of the agent's own may refuse by returning rather than by raising, which is
        # ordinary in code that was never written to be tested. Recording that as a success
        # would hide exactly the behaviour worth measuring, so the agent's own convention
        # decides. Only the recording differs: the value still reaches the agent unchanged.
        if self._refused_by_value(value):
            return self._record(
                Call(
                    name=name,
                    arguments=args,
                    result=value,
                    ok=False,
                    refused=True,
                    error=str(value)[:400],
                )
            )
        return self._record(Call(name=name, arguments=args, result=value))

    def _refused_by_value(self, value: Any) -> bool:
        """Whether a returned value is this agent's way of saying no.

        The convention is recorded as a description, because that is what somebody reading the
        agent's code can actually write: "strings starting with Error:". So the marker is taken
        from inside it rather than treating the whole sentence as a prefix, which would match
        nothing and quietly record every refusal as a success.
        """
        if not isinstance(value, str) or not value:
            return False
        described = (self.refusal_signature or "").strip()
        if not described:
            return False
        for marker in self._markers(described):
            if value.lower().startswith(marker.lower()):
                return True
        return False

    def _markers(self, described: str) -> list[str]:
        """The literal markers named inside a described convention.

        Anything quoted is taken as written, since that is how a convention gets spelled out. With
        nothing quoted the whole description is treated as the marker, which is right when somebody
        recorded just the prefix itself.
        """
        import re

        # A convention written for people gets quoted the way people quote, and a model writing
        # JSON often escapes those quotes. Left in, the backslash ends up inside the marker, so
        # "Error:" is looked for as 'Error:\' and matches nothing at all. Every refusal is then
        # recorded as a success, which is the failure this whole field exists to prevent.
        plain = described.replace('\\"', '"').replace("\\'", "'")
        quoted = re.findall(r"[\"'“”‘’`]([^\"'“”‘’`]{1,40})[\"'“”‘’`]", plain)
        found = [one.strip().strip("\\").strip() for one in quoted]
        # A convention that lists examples separates them, and the separator sits between one
        # closing quote and the next opening one, so it is matched as though it were quoted too.
        # A marker of "," would make any result beginning with a comma a refusal, so anything
        # without a character a message could start with is dropped.
        found = [one for one in found if any(char.isalnum() for char in one)]
        return found or [plain.strip()]

    def _record(self, call: Call) -> Call:
        # Stamped here rather than by the caller, so every call is stamped and none of them
        # depend on whoever made it remembering to.
        call.at = call.at or time.time()
        self.calls.append(call)
        return call

    # -- state -----------------------------------------------------------------------

    def _settle(self) -> None:
        """Close any transaction left open on the connection.

        A handler that only reads still leaves an implicit read transaction behind, and SQLite
        refuses to back up into a connection that has one open: "destination database is in
        use". Left unsettled, the first read-only handler poisons every probe after it, and the
        world can never be checked or saved.
        """
        connection = getattr(self.store, "connection", None)
        if connection is None:
            # Nothing to settle. A store with no transactions has no open one to close, and
            # reaching for a connection it never had would fail every probe on such a world.
            return
        try:
            connection.commit()
        except sqlite3.Error:
            connection.rollback()

    def checkpoint(self) -> Any:
        """A copy of everything the world holds, to come back to.

        Probes and smoke calls mutate: ordering an item inserts a record, cancelling one changes
        it. Without a way back, each runs against the debris of the ones before it, and a check
        expecting three records finds seven.

        Both halves are copied, and that matters more for an adopted world than a generated one.
        A tool the agent wrote changes the structure it was given, in place. Backing up only the
        store would leave those changes permanent, so a smoke call against one record would quietly
        spend it, and whatever ran later against that same record would fail for a reason nothing
        could see.
        """
        import copy as duplicate

        self._settle()
        # Through the store's own freeze rather than a SQLite backup, so a world whose records
        # live somewhere else is revertible too. Every store knows how to go back; only some of
        # them have a connection to copy.
        store = self.store.freeze()
        held = duplicate.deepcopy(self.state_object) if self.state_object is not None else None
        return {"store": store, "state": held}

    def revert(self, checkpoint: Any) -> None:
        """Put everything back as it was when the checkpoint was taken."""
        import copy as duplicate

        self._settle()
        # A bare connection is accepted so that anything written against the older shape of this
        # method keeps working rather than reverting nothing at all, which would be silent.
        if isinstance(checkpoint, sqlite3.Connection):
            checkpoint.backup(self.connection)
            return
        held = (checkpoint or {}).get("store")
        if held is not None:
            self.store.restore(held)
        if (checkpoint or {}).get("state") is not None:
            self.state_object = duplicate.deepcopy(checkpoint["state"])

    def state(self) -> dict[str, Any]:
        """What the checks compare against after a run.

        Tables and their rows, plus whatever the agent's own tools keep in memory. A world that
        adopted the agent's code may have all of its state in the second of those, so a check has
        to be able to see both without knowing which kind of world it is grading.
        """
        found: dict[str, Any] = {
            name: self.store.records(name) for name in self.store.collections()
        }
        if isinstance(self.state_object, dict):
            # Collections the agent's own code owns. Not merged blindly: a table and a key of
            # the same name would silently shadow one another, and a check comparing the wrong
            # one would be wrong in a way nobody could see.
            for key, value in self.state_object.items():
                found.setdefault(str(key), value)
        elif self.state_object is not None:
            found.setdefault("state", self.state_object)
        return found

    # -- changing the world, without naming what it is kept in ------------------------
    #
    # A scenario changes the world before it runs, and it must not have to know whether the world
    # is a database, a mapping the agent's own code owns, or something else again. Speaking SQL
    # here would write SQLite into every scenario ever written, and the store is the one thing
    # this design expects to vary per agent.
    #
    # So the vocabulary is collections and records, which every store has under some name, and
    # each method dispatches on what the collection actually is. The preferred way to change the
    # world is still the agent's own tools, because anything they refuse would have refused the
    # agent too; these are for the states no tool can produce.

    def _table(self, collection: str) -> bool:
        return bool(self.store.holds(collection))

    def _held(self, collection: str) -> Any:
        if isinstance(self.state_object, dict):
            return self.state_object.get(collection)
        return None

    def put(self, collection: str, record: Mapping[str, Any], *, key: str = "") -> None:
        """Add one record to a collection, whatever the collection is kept in."""
        if self._table(collection):
            self.store.add(collection, record)
            return
        held = self._held(collection)
        if isinstance(held, dict):
            if not key:
                raise KeyError(
                    f"{collection} is keyed, so adding to it needs a key: "
                    "world.put(collection, record, key=...)"
                )
            held[key] = dict(record)
            return
        if isinstance(held, list):
            held.append(dict(record))
            return
        # A collection nobody has created yet is made here rather than refused. An agent whose
        # state lives in services and files has no store to declare tables in, so every collection
        # the world needs is one the harness invents: refusing the first record leaves that agent
        # with a world that cannot hold anything at all.
        made = getattr(self.store, "start_collection", None)
        if callable(made):
            made(collection, keyed=bool(key))
            self.store.add(collection, {**record, "_id": key} if key else record)
            return
        raise KeyError(f"no collection called {collection!r}; this world has {sorted(self.state())}")

    def change(self, collection: str, key: str, changes: Mapping[str, Any], *, by: str = "") -> int:
        """Change records in a collection. Returns how many were changed.

        ``by`` names the column a table is keyed on. A collection the agent's own code keeps is
        keyed already, so it is not needed there.
        """
        if self._table(collection):
            return self.store.amend(collection, key, changes, by=by)
        held = self._held(collection)
        if isinstance(held, dict) and key in held:
            if isinstance(held[key], dict):
                held[key].update(dict(changes))
            else:
                held[key] = dict(changes)
            return 1
        raise KeyError(f"nothing called {key!r} in {collection!r}")

    def drop(self, collection: str, key: str = "", *, by: str = "") -> int:
        """Remove a record, or the whole contents of a collection when no key is given."""
        if self._table(collection):
            return self.store.remove(collection, key, by=by)
        held = self._held(collection)
        if isinstance(held, dict):
            if not key:
                count = len(held)
                held.clear()
                return count
            return 1 if held.pop(key, None) is not None else 0
        if isinstance(held, list):
            count = len(held)
            del held[:]
            return count
        raise KeyError(f"no collection called {collection!r}; this world has {sorted(self.state())}")

    def shapes(self) -> str:
        """What this world's collections actually are, in words.

        Said wherever code written against the wrong shape fails. A table gives a list of records;
        a collection the agent's own code keeps is often a mapping keyed by identifier, and
        iterating that yields strings. No amount of general advice substitutes for naming which is
        which, for the world in front of whoever got it wrong.
        """
        lines = []
        for name, held in sorted(self.state().items()):
            if isinstance(held, dict):
                first = next(iter(held), None)
                lines.append(
                    f"  {name}: a mapping of {len(held)} records keyed by identifier"
                    + (f", e.g. {first!r}" if first is not None else "")
                    + ". Iterate .values(), or .items() when the key matters."
                )
            elif isinstance(held, list):
                lines.append(f"  {name}: a list of {len(held)} records. Iterate it directly.")
            else:
                lines.append(f"  {name}: a single {type(held).__name__}.")
        return "This world holds:\n" + ("\n".join(lines) or "  nothing yet")

    def close(self) -> None:
        self.store.close()


@dataclass
class WorldSpec:
    """What a generated world is, before it is written out."""

    agent: str
    schema_sql: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    handlers: dict[str, str] = field(default_factory=dict)
    notes: str = ""
