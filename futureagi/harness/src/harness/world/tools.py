"""The tools that build a world, and the gate that decides it may be saved.

A deliberately narrow surface. The builder gets no generic file write, because a guardrail needs
something to sit behind: every action goes through a tool that can execute it, check it, and say
what went wrong. Interface design work on coding agents is consistent that this beats handing
over raw access and hoping.

Three habits throughout, for the same reason:

- **execute immediately.** A handler is run the moment it is defined, so a mistake comes back on
  the next turn rather than at save time.
- **say what happened, briefly.** Counts and names, never dumps. More context measurably makes
  agents worse at this.
- **never answer with nothing.** "0 rows inserted" is a result; an empty string is a puzzle.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from ..amend import add_rule, drop_rule, fix_tool, set_modality, unreachable, widen
from ..catalogue import SubGoal, load_catalogue, save_catalogue, validate_sub_goal
from ..checks import run_check, run_world_check
from ..contract import AgentContract
from ..simulator import (
    load_simulator_prompt,
    save_simulator_prompt,
    validate_simulator_prompt,
)
from ..tools import brief as _brief
from ..tools import schema
from .kinds import for_contract
from .mutate import UNDAMAGED, blind, unnoticed
from .probe import dirty_state, probe
from .runtime import GeneratedWorld
from .snapshot import MANIFEST, read_manifest, restore, save
from .stores.written import API as OPS_API

WORLD_SERVER = "world"

# What a handler is actually given. Said again here, and not only in the skill, because this is
# where the mistake surfaces: a handler that crashed has a model reading *this* message, and an
# error naming the failure without naming the API produces the same wrong guess again. Three
# identical attempts at one handler is what that costs.
DB_API = (
    "Inside a handler, `db` reads the world two ways and has no cursors.\n\n"
    "Works on every world, database or not:\n"
    '    db.records("orders")              -> every record in a collection, as dicts\n'
    '    db.find("orders", status="new")   -> the ones whose fields all match\n'
    '    db.collections()                  -> the collection names\n'
    '    db.add("orders", {"id": "o1"})    -> put one record in\n\n'
    "Only where this world has a query language, which not every agent does:\n"
    '    db.query("SELECT * FROM t WHERE id = ?", [x])   -> list of dicts, [] if none\n'
    '    db.one("SELECT * FROM t WHERE id = ?", [x])      -> one dict, or None\n'
    '    db.execute("INSERT INTO t (a) VALUES (?)", [x])  -> number of rows changed\n\n'
    "If this world has no connection, those three raise and the first four are what to use. "
    "Records are dicts read by field name. db.execute returns a count, not a cursor, so calling "
    ".fetchone(), .fetchall() or .lastrowid on any of these is a mistake. You also have `args`, "
    "`ToolError` and `json`, and nothing else. Do not import anything."
)

# Below this, the world is not good enough to build tests on. Synthesis work that measures this
# converges on roughly this bar, and rejects a quarter to a third of what it generates.
ACCEPTABLE = 0.85

# What a world check is, said where the mistake surfaces. A check that inspects nothing is
# the failure this whole mechanism exists to catch, so the answer says what "inspects
# something" means rather than only that the check was rejected.
WORLD_CHECK_HELP = (
    "A world check is Python defining check(world), returning None when it holds or a "
    "sentence saying what is wrong.\n\n"
    "`world.state()` gives every collection this world has. **A collection is not always a list.**\n"
    "A table gives a list of records. A collection the agent's own code keeps is often a mapping "
    "keyed by identifier, and iterating that yields the keys, which are strings. Reading a field "
    "off one of those is where a check written for the wrong shape fails.\n"
    "    held = world.state()['some_collection']\n"
    "    records = list(held.values()) if isinstance(held, dict) else held\n"
    "    wanted = [one for one in records if one.get('status') == 'pending']\n"
    "The shapes this world actually has are listed below, so write for those rather than "
    "guessing.\n\n"
    "A check also has to inspect something that could be wrong. One that returns None without "
    "reading the world passes forever, and it is rejected once the world is broken on purpose "
    "and it stays green."
)


def _shapes(world: Any) -> str:
    """What this world's collections are. Asked of the world, so every gate says the same thing."""
    return world.shapes()

# What to read when a binding to the agent's own code will not run. The failure is nearly always
# the shape of the call rather than the code being unreachable, so the answer says what the
# shapes are instead of only reporting the exception.
BINDING_SCOPE = (
    "Inside a binding, and inside a factory expression, these are the only names that exist:\n"
    "    args        the arguments the agent passed, as a dict\n"
    "    db          the world. db.state is the agent's own state, as adopt_state loaded it\n"
    "    ToolError   to refuse\n"
    "    json\n"
    "plus whatever the binding itself imports from the agent's source. There is no `userdata`, no "
    "`state`, no framework context and no session: if the callable needs one of those, it has to "
    "be constructed in the factory expression out of what is listed above, or it cannot be "
    "reached from here at all."
)

ADOPT_HELP = (
    "A binding is how one of the agent's own callables is reached. Four things can be wrong:\n"
    "  - the module path. It is imported from the agent's source root, so use the path its own "
    "code would use, e.g. package.module.file, not a filesystem path\n"
    "  - the style. 'function' for a module-level def, 'staticmethod' for one on a class, "
    "'method' when an instance has to exist first, in which case `factory` is the expression "
    "that builds it\n"
    "  - first_arg. If the callable takes the agent's state as its first argument, name it here "
    "and the world passes what adopt_state loaded. Leave it empty when the callable connects "
    "for itself\n"
    "  - smoke_arguments. These are passed as keywords, so they have to match the callable's own "
    "parameter names, and their values should be real: look at the world first and use an "
    "identifier that exists, or the call only ever proves the tool can say no\n"
    "If the tool genuinely cannot be reached without editing the agent, say so and ask. Do not "
    "write a replacement for it."
)


def _size(value: Any) -> Any:
    return len(value) if isinstance(value, (list, dict, tuple, str)) else value


# What a store file is called, when nobody has said where it is. Extensions rather than names,
# because the name is the agent's business and the extension is the convention.
STORE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".duckdb", ".dump", ".sql")


def _stores_here(source_root: str) -> str:
    """Where the agent's code is, and which files under it look like a store.

    Said rather than left to be guessed. A message that reports a path was wrong without saying
    what the right ones are turns one call into a search, and the search is over a filesystem this
    stage deliberately cannot list.
    """
    if not source_root:
        return (
            "This stage was not told where the agent's code lives, so a relative path has nothing "
            "to resolve against. Give an absolute path, or say that the source root is missing."
        )
    root = Path(source_root)
    seen: list[str] = []
    for path in sorted(root.rglob("*")):
        if len(seen) >= 12:
            break
        if path.is_file() and path.suffix.lower() in STORE_SUFFIXES:
            size = path.stat().st_size
            measure = f"{size // 1024} KB" if size else "empty"
            seen.append(f"    {path.relative_to(root)}  ({measure})")
    if not seen:
        return (
            f"The agent's code is at {root}, and nothing under it looks like a store. If it "
            "builds or downloads one on first run, say so and ask rather than inventing data."
        )
    return "The agent's code is at " + str(root) + ", and these look like stores:\n" + "\n".join(seen)


def _binding(*, module: str, called: str, style: str, first_arg: str, factory: str) -> str:
    """The handler that calls one of the agent's own callables.

    Written as source rather than held as a closure, so it is saved with the world, readable by
    whoever wants to know what actually ran, and restored exactly as every other handler is.

    ``called`` may be a dotted path inside the module, which is how a staticmethod is reached:
    ``CancelPendingOrder.invoke`` imports the class and calls the method on it. Only the first
    segment is imported.
    """
    root = called.split(".")[0]
    reach = f"from {module} import {root}" if module else ""
    state = "db.state, " if first_arg else ""
    # Their code may be async, which is true of every framework-decorated tool. The result is
    # settled here rather than by the caller so that a handler stays synchronous, which is what
    # every other part of the world already assumes.
    if style == "method":
        built = factory or f"{root}()"
        attr = called.split(".", 1)[1] if "." in called else "__call__"
        return (
            f"{reach}\n"
            "from harness.world.runtime import settled\n\n"
            "def handle(args, db):\n"
            f"    instance = {built}\n"
            f"    return settled(instance.{attr}({state}**args))\n"
        )
    return (
        f"{reach}\n"
        "from harness.world.runtime import settled\n\n"
        "def handle(args, db):\n"
        f"    return settled({called}({state}**args))\n"
    )


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def world_tools(
    contract: AgentContract, destination: Path, *, source_root: str = ""
) -> Any:
    """A server exposing the world-building surface for one agent.

    ``source_root`` is where the agent's own code lives. With it, a tool can be bound to the
    agent's own implementation; without it the agent was given as a specification and its
    tools have to be written here.
    """
    # An existing world is picked up rather than replaced. Amending one is the ordinary case
    # once it has been built once, and starting empty every time would mean rebuilding a
    # catalogue from scratch to add a single item to it.
    existing = (destination / MANIFEST).exists()
    # The store comes from what the contract found, not from a default. An agent whose tools keep
    # their own state has no database, and opening one for it would be carrying something unused
    # and describing the world as something it is not.
    named = str(getattr(getattr(contract, "data_store", None), "kind", "") or "")
    world = restore(destination) if existing else GeneratedWorld(":memory:", kind=named)
    world.name = contract.agent
    world.refusal_signature = contract.refusal_signature
    if source_root:
        world.reach(source_root)
    kind = for_contract(contract)
    catalogue = load_catalogue(destination)
    scores: list[float] = []
    # The checks that decide whether this world is usable, written here rather than fixed in
    # advance, because what makes a world usable is a judgement about this agent.
    world_checks: dict[str, str] = dict(read_manifest(destination).get("world_checks") or {}) if existing else {}
    # How many times each tool has been attempted, so a binding that cannot be made to work
    # is told to stop rather than tried indefinitely.
    tried: dict[str, int] = {}
    sequences: list[dict[str, Any]] = (
        list(read_manifest(destination).get("sequences") or []) if existing else []
    )

    def _verified() -> tuple[list[str], list[str], dict[str, list[str]]]:
        """How the world's own checks fare, and which of them cannot fail.

        Run against the world as it stands, and then against worlds broken on purpose. A check
        that stays green through every kind of damage is reported as blind: it is not verifying
        anything, whatever it claims to inspect.
        """
        import tempfile

        failing = [
            name
            for name, source in sorted(world_checks.items())
            if not run_world_check(source, world, name=name).held
        ]
        if not world_checks:
            return failing, [], {}
        # Snapshotted first so each mutation gets its own copy and none inherits another's
        # damage. The world being built is never touched.
        held = Path(tempfile.mkdtemp())
        save(world, held, notes="mutation", sequences=sequences)
        survived = unnoticed(
            held,
            sorted(world_checks.items()),
            run=lambda source, broken: run_world_check(source, broken, name="check"),
            restore=restore,
        )
        return failing, blind(survived), survived

    @tool(
        "create_schema",
        "Run CREATE TABLE statements. Call once with the whole schema; call again to alter it.",
        {"sql": str},
    )
    async def create_schema(args: dict[str, Any]) -> dict[str, Any]:
        try:
            applies = getattr(world.store, "apply", None)
            if applies is not None:
                # A store-backed world speaks through its own engine — the
                # sqlite-era connection only exists for worlds that are files.
                # Off the loop: first touch boots the store's container.
                await asyncio.to_thread(applies, args["sql"])
            else:
                world.connection.executescript(args["sql"])
                world.connection.commit()
        except Exception as failed:
            return _err(f"schema rejected: {failed}")
        tables = sorted(world.state())
        return _ok(f"{len(tables)} tables: {', '.join(tables) or 'none'}")

    @tool(
        "seed",
        "Put records into a collection. Rows is a list of objects whose keys are field names. "
        "Works whether or not this world has a database: a collection that does not exist yet is "
        "made, which is how an agent with no store of its own gets one.",
        {"table": str, "rows": list},
    )
    async def seed(args: dict[str, Any]) -> dict[str, Any]:
        table, rows = str(args["table"]), args.get("rows") or []
        written = 0
        for row in rows:
            if not isinstance(row, dict) or not row:
                continue
            try:
                # Through the world rather than the connection, so this is the same call for a
                # table, for a structure the agent's own code keeps, and for an agent that has no
                # store at all and whose collections the harness is inventing.
                world.put(table, row)
                written += 1
            except Exception as failed:
                return _err(
                    f"{written} records written, then {table} rejected one: {failed}\n"
                    f"{_shapes(world)}"
                )
        total = len(world.state().get(table, []))
        return _ok(f"{written} records put into {table}; {total} there now")

    @tool(
        "change_data",
        "Change or remove rows already in the world: one UPDATE or DELETE statement. Seeding "
        "only ever inserts, so without this a row put in wrong can never be taken out, and the "
        "only way left to make a check pass is to change the contract, which is the wrong "
        "repair. Use inspect_world to read; this is for changing.",
        {"sql": str},
    )
    async def change_data(args: dict[str, Any]) -> dict[str, Any]:
        statement = str(args.get("sql") or "").strip()
        verb = statement.split(None, 1)[0].upper() if statement else ""
        if verb not in ("UPDATE", "DELETE"):
            return _err(
                "this runs one UPDATE or DELETE. Use seed to add rows, create_schema to change "
                "the shape of a table, and inspect_world to look."
            )
        try:
            changed = world.connection.execute(statement).rowcount
            world.connection.commit()
        except Exception as failed:
            world.connection.rollback()
            return _err(f"rejected: {failed}")
        counts = ", ".join(f"{n}: {len(r)}" for n, r in sorted(world.state().items()))
        return _ok(f"{changed} rows changed. The world now holds {counts}")

    @tool(
        "define_handler",
        "Define one tool's implementation. The source must define handle(args, db) and is run "
        "immediately against the seeded world, so errors come straight back.",
        schema(
            {"tool_name": str, "source": str, "smoke_arguments": dict},
            ["tool_name", "source"],
        ),
    )
    async def define_handler(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args["tool_name"])
        if name not in contract.tool_names():
            return _err(
                f"{name!r} is not a tool this agent has. It has: "
                f"{', '.join(sorted(contract.tool_names()))}"
            )
        # Writing a replacement for a tool the agent already implements is the one thing this
        # stage must not do. It changes what is being tested from the agent's behaviour to our
        # reading of it, and the difference does not show up anywhere afterwards.
        if contract.adoptable(name):
            entry = contract.entry_for(name)
            return _err(
                f"{name} already has an implementation, so it is not ours to write. Use "
                f"adopt_tool to bind to {entry.module}.{entry.callable} instead.\n"
                "If you have tried and it genuinely cannot be reached from here, say so with "
                "cannot_reach_tool and what stopped it. That records the reason on the contract "
                "and then lets you write one. Do not write a replacement without it: a generated "
                "stand-in nobody knows is a stand-in is worse than a tool we admit we could not "
                "run."
            )
        world.handlers[name] = str(args["source"])
        # Same reason as adopting one: running it to prove it works must leave the world as it was.
        held = world.checkpoint()
        call = world.call(name, args.get("smoke_arguments") or {})
        world.revert(held)
        if call.refused:
            return _ok(
                f"{name} defined. Smoke call refused, which is a working refusal: {call.error}"
            )
        if not call.ok:
            del world.handlers[name]
            said = f"{name} not kept, it crashed on its smoke call: {call.error}"
            # A crash is nearly always the handler reaching for something it does not have, so
            # the answer says what it does have rather than only what went wrong.
            return _err(f"{said}\n\n{DB_API}")
        return _ok(f"{name} defined and ran. Returned {_brief(call.result)}")

    @tool(
        "adopt_state",
        "Load the agent's own starting state by calling its own loader, so the world holds what "
        "the agent really has rather than a copy of it. Give the module and the callable, for "
        "example the function that reads its data files.",
        schema({"module": str, "callable": str}, ["module", "callable"]),
    )
    async def adopt_state(args: dict[str, Any]) -> dict[str, Any]:
        module = str(args["module"])
        called = str(args["callable"])
        world.reach(source_root)
        try:
            loaded = __import__(module, fromlist=[called])
            factory = getattr(loaded, called)
            world.state_object = factory()
        except Exception as raised:
            return _err(
                f"could not load state with {module}.{called}: "
                f"{type(raised).__name__}: {raised}\n{ADOPT_HELP}"
            )
        summary = (
            {key: _size(value) for key, value in world.state_object.items()}
            if isinstance(world.state_object, dict)
            else type(world.state_object).__name__
        )
        return _ok(f"state loaded from {module}.{called}: {json.dumps(summary, default=str)}")

    @tool(
        "adopt_store",
        "Take the agent's own store as this world's starting data, so the world holds what the "
        "agent really has. Give the path to it, relative to the agent's source or absolute. Use "
        "this whenever the agent ships or builds a store of its own: seeding it by hand instead "
        "produces a smaller, invented dataset that its real queries were never written against.",
        schema({"path": str, "note": str}, ["path"]),
    )
    async def adopt_store(args: dict[str, Any]) -> dict[str, Any]:
        given = str(args["path"]).strip()
        found = Path(given)
        if not found.is_absolute() and source_root:
            found = Path(source_root) / given
        if not found.exists() and source_root:
            # An absolute path that is wrong is nearly always the agent's own repo-relative path
            # read out of its source, so the same name under the real root is worth trying before
            # reporting a miss.
            under = Path(source_root) / Path(given).name
            if under.exists():
                found = under
        if not found.exists():
            return _err(
                f"nothing at {found}.\n{_stores_here(source_root)}"
            )
        if found.is_file() and found.stat().st_size == 0:
            return _err(
                f"{found} is empty, so there is nothing to adopt. If the agent builds or "
                "downloads its store on first run, say so and ask rather than inventing data."
            )
        try:
            # Off the event loop: taking a store can pull an image and boot a
            # container, and the API must stay answerable meanwhile.
            await asyncio.to_thread(world.store.take, found)
        except AttributeError:
            return _err(
                f"a {world.store.engine} store cannot take another one yet. Seed it instead, or "
                "say what it would need."
            )
        except Exception as raised:
            return _err(f"could not take {found}: {type(raised).__name__}: {raised}")
        state = world.state()
        return _ok(
            f"adopted {found.name}: "
            + (", ".join(f"{name}: {len(rows)}" for name, rows in sorted(state.items())) or "nothing")
        )

    @tool(
        "adopt_tool",
        "Bind one tool to the agent's own implementation, so its code runs rather than a "
        "replacement. Give the module and the callable. `style` is how it is invoked: "
        "'function' for a plain function, 'staticmethod' for one hanging off a class, "
        "'method' when an instance has to be built first. `first_arg` names what the agent's "
        "state is passed as, if its signature takes it. The binding runs immediately, so give "
        "`smoke_arguments` that a real record in this world would satisfy: an identifier that is "
        "actually there. A smoke call that refuses proves the binding can refuse, not that it "
        "works.",
        schema(
            {
                "tool_name": str,
                "module": str,
                "callable": str,
                "style": str,
                "first_arg": str,
                "factory": str,
                "binding": str,
                "smoke_arguments": dict,
            },
            ["tool_name"],
        ),
    )
    async def adopt_tool(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args["tool_name"])
        if name not in contract.tool_names():
            return _err(
                f"{name!r} is not a tool this agent has. It has: "
                f"{', '.join(sorted(contract.tool_names()))}"
            )
        if not source_root:
            return _err(
                "there is no agent source on disk to bind to, so nothing can be adopted here. "
                "This agent was given as a specification rather than as code, so its tools have "
                "to be written with define_handler."
            )
        world.reach(source_root)
        # A binding written here wins. The generated shapes cover a plain callable and a method
        # on an object, which is most agents, but no set of shapes covers every framework, and
        # guessing wrong is worse than letting whoever read the code write the two lines.
        written = str(args.get("binding") or "").strip()
        if written:
            binding = written
        elif not str(args.get("module") or ""):
            return _err(
                "give either a module and callable to bind to, or a binding of your own.\n\n"
                + ADOPT_HELP
            )
        else:
            binding = _binding(
                module=str(args["module"]),
                called=str(args["callable"]),
                style=str(args.get("style") or "function"),
                first_arg=str(args.get("first_arg") or ""),
                factory=str(args.get("factory") or ""),
            )
        world.handlers[name] = binding
        # Reverted after, because a smoke call against the agent's own code really does what the
        # tool does: cancelling an order to prove the binding works would spend that order, and
        # every scenario after it starts from this same world. Proving a tool works must not cost
        # a record.
        held = world.checkpoint()
        call = world.call(name, args.get("smoke_arguments") or {})
        world.revert(held)
        if call.refused:
            return _ok(
                f"{name} adopted. Its own code answered with a refusal, which is it working: "
                f"{call.error}"
            )
        if not call.ok:
            del world.handlers[name]
            tried[name] = tried.get(name, 0) + 1
            said = f"{name} not adopted, the binding failed: {call.error}"
            # A name that does not exist is the commonest way this fails, and the answer to it is
            # the list of names that do, not a repeat of the general advice.
            if "nameerror" in (call.error or "").lower():
                said += f"\n\n{BINDING_SCOPE}"
            if tried[name] >= 3:
                said += (
                    f"\n\nThat is {tried[name]} attempts at this one. Some tools cannot be "
                    "reached without editing the agent: a framework may build them inside a "
                    "session that does not exist here. Stop and say so, naming this tool and what "
                    "it would need, and let the person decide. A tool nobody can run is a fact "
                    "worth reporting, and writing a stand-in instead is the one failure that "
                    "leaves no trace."
                )
            return _err(f"{said}\n\n{ADOPT_HELP}")
        return _ok(
            f"{name} adopted and ran, its own code. Returned {_brief(call.result)}"
        )

    @tool(
        "run_tool",
        "Call a defined tool and see what the world does. Use this to check a refusal works.",
        schema({"tool_name": str, "arguments": dict}, ["tool_name"]),
    )
    async def run_tool(args: dict[str, Any]) -> dict[str, Any]:
        call = world.call(str(args["tool_name"]), args.get("arguments") or {})
        if call.refused:
            return _ok(f"refused: {call.error}")
        if not call.ok:
            return _err(f"crashed: {call.error}")
        return _ok(f"ok: {_brief(call.result)}")

    @tool(
        "declare_sequence",
        "Declare a series of calls whose end state should hold, so consistency across calls is "
        "checked. Each call is {tool, arguments}. expect_state keys are 'table.column' or "
        "'table.count'. Declaring the same name again replaces it.\n\n"
        "Every sequence runs on its own from the frozen world: the state is put back before each "
        "one, so they never see each other's rows and expect_state is an absolute count, not a "
        "running total. If a sequence fails, the fault is in that sequence, not in the ones "
        "declared before it.",
        schema({"name": str, "calls": list, "expect_state": dict}, ["name", "calls"]),
    )
    async def declare_sequence(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or f"sequence-{len(sequences)}")
        calls = args.get("calls") or []

        # Checked here rather than at save time. A malformed sequence that only fails three
        # tools later reads as a mystery, and there is nothing to learn from it in between.
        problems: list[str] = []
        if not calls:
            problems.append("no calls: a sequence with no calls checks nothing")
        for index, step in enumerate(calls):
            if not isinstance(step, dict):
                problems.append(
                    f"call {index} is not an object with a tool and arguments"
                )
                continue
            called = str(step.get("tool") or "")
            if not called:
                problems.append(f"call {index} has no tool name")
            elif called not in world.handlers:
                problems.append(
                    f"call {index} names {called!r}, which has no handler yet. Defined: "
                    f"{', '.join(sorted(world.handlers)) or 'none'}"
                )
        if problems:
            return _err(f"{name} not declared:\n  - " + "\n  - ".join(problems))

        replaced = any(existing["name"] == name for existing in sequences)
        sequences[:] = [existing for existing in sequences if existing["name"] != name]
        sequences.append(
            {
                "name": name,
                "calls": calls,
                "expect_state": args.get("expect_state") or {},
            }
        )
        verb = "replaced" if replaced else "declared"
        return _ok(
            f"{name} {verb}. {len(sequences)} sequences: {', '.join(s['name'] for s in sequences)}"
        )

    @tool(
        "drop_sequence",
        "Remove a declared sequence by name, or all of them with name '*'.",
        {"name": str},
    )
    async def drop_sequence(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "")
        if name == "*":
            sequences.clear()
            return _ok("all sequences dropped")
        before = len(sequences)
        sequences[:] = [existing for existing in sequences if existing["name"] != name]
        if len(sequences) == before:
            return _err(
                f"no sequence called {name!r}. Declared: "
                f"{', '.join(s['name'] for s in sequences) or 'none'}"
            )
        return _ok(f"{name} dropped. {len(sequences)} left")

    @tool(
        "amend_contract",
        "Let one of the agent's tools accept values it did not before. Use this when the world "
        "holds something the agent has no way to name: an item added to the menu that item_id "
        "does not list is dead data, and a scenario about it can only fail.\n\n"
        "Only widen where the agent genuinely should accept the value. Say why in one line; it "
        "is recorded on the contract, because a contract nobody can audit is worth nothing.",
        {"tool_name": str, "argument": str, "values": list, "why": str},
    )
    async def amend_contract(args: dict[str, Any]) -> dict[str, Any]:
        done, said = widen(
            contract,
            destination,
            tool_name=str(args.get("tool_name") or ""),
            argument=str(args.get("argument") or ""),
            values=[str(value) for value in (args.get("values") or [])],
            why=str(args.get("why") or ""),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "cannot_reach_tool",
        "Record that a tool's own implementation cannot be run here, so the world may implement "
        "it instead. Only after adopt_tool has genuinely failed: say what stopped it, in one "
        "line. The reason is written onto the contract permanently, because it is the only "
        "record that this tool was a stand-in rather than the agent's own code.",
        schema({"tool_name": str, "why": str}, ["tool_name", "why"]),
    )
    async def cannot_reach_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = unreachable(
            contract,
            destination,
            tool_name=str(args.get("tool_name") or ""),
            why=str(args.get("why") or ""),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "add_rule",
        "Give the agent a hard rule its source did not state, when the operator asks for one. "
        "The agent under test is told every rule and the judge grades against them, so this "
        "changes what is being tested. Say why in one line; it is recorded on the contract.",
        {"rule": str, "why": str},
    )
    async def add_rule_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = add_rule(
            contract,
            destination,
            rule=str(args.get("rule") or ""),
            why=str(args.get("why") or ""),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "set_modality",
        "Correct how a person actually reaches this agent: voice, chat or browser. Modality "
        "picks the world, the simulated person and the transport, so a wrong one does not weaken "
        "a run, it runs a different test. Use it when the operator says where the agent is "
        "deployed and the contract disagrees: an agent's code reads the same answering a chat "
        "window or a phone call, so where it is deployed is something only they can settle.",
        {"modality": str, "why": str},
    )
    async def set_modality_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = set_modality(
            contract,
            destination,
            modality=str(args.get("modality") or ""),
            why=str(args.get("why") or ""),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "inspect_world",
        "Look at what is in the world you are building. With no collection named, lists what "
        "there is and how much is in each. With one, returns records from it. `matching` is plain "
        "text and filters to records containing it, which is how you find a record in a large "
        "collection without reading all of it.",
        schema({"table": str, "limit": int, "matching": str}, []),
    )
    async def inspect_world(args: dict[str, Any]) -> dict[str, Any]:
        state = world.state()
        table = str(args.get("table") or "")
        if not table:
            return _ok(
                "\n".join(f"{name}: {_size(held)}" for name, held in sorted(state.items()))
                or "nothing in the world yet"
            )
        if table not in state:
            return _err(
                f"nothing called {table!r}; there is {', '.join(sorted(state)) or 'nothing'}"
            )
        held = state[table]
        # A collection is a list of rows from a table, or a mapping the agent's own code keeps.
        # Slicing the second one raises, so the shape is handled rather than assumed.
        if isinstance(held, dict):
            found = [{"_key": key, **value} if isinstance(value, dict) else {"_key": key, "value": value}
                     for key, value in held.items()]
        elif isinstance(held, list):
            found = list(held)
        else:
            found = [held]
        matching = str(args.get("matching") or "").strip().lower()
        if matching:
            narrowed = [
                one for one in found if matching in json.dumps(one, default=str).lower()
            ]
            if not narrowed:
                return _ok(
                    f"nothing in {table} contains {matching!r}, out of {len(found)} records."
                )
            found = narrowed
        shown = found[: int(args.get("limit") or 5)]
        return _ok(
            f"{len(found)} records"
            + (f" matching {matching!r}" if matching else "")
            + f", showing {len(shown)}:\n"
            + "\n".join(_brief(one) for one in shown)
        )

    @tool(
        "drop_rule",
        "Take away a hard rule the agent does not really have. A rule nobody has is worse than "
        "a missing one: the agent is told to obey it and graded for not doing something it was "
        "never supposed to do. Say why.",
        {"rule": str, "why": str},
    )
    async def drop_rule_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = drop_rule(
            contract,
            destination,
            rule=str(args.get("rule") or ""),
            why=str(args.get("why") or ""),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "fix_tool",
        "Correct a tool that was read wrong, or remove one the agent does not have. `args` "
        "replaces its argument names in order; `arg_types` and `description` update those. Set "
        "`remove` to take the tool away entirely. Everything downstream is built from these, so "
        "a wrong argument name produces a world that refuses everything. Say why.",
        schema(
            {
                "tool_name": str,
                "args": list,
                "arg_types": dict,
                "description": str,
                "remove": bool,
                "why": str,
            },
            ["tool_name", "why"],
        ),
    )
    async def fix_tool_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = fix_tool(
            contract,
            destination,
            tool_name=str(args.get("tool_name") or ""),
            why=str(args.get("why") or ""),
            args=[str(a) for a in args["args"]] if args.get("args") else None,
            arg_types={
                str(k): str(v) for k, v in (args.get("arg_types") or {}).items()
            },
            description=str(args.get("description") or ""),
            remove=bool(args.get("remove")),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "write_simulator_prompt",
        "Write the prompt that drives the simulated user of this agent, for a conversational "
        "agent only. It is written once and every scenario fills its slots, so leave variables "
        "as {{ instruction }}, {{ persona }} and any others this agent needs.\n\n"
        "It has to cover how a person in this conversation actually behaves: that they are "
        "living the situation rather than describing it, that they speak one turn at a time, "
        "that they never break character or explain that they are testing anything, what they "
        "know and when they may say it, and when the conversation is over. Write it for this "
        "agent, not in general.",
        schema({"prompt": str}, ["prompt"]),
    )
    async def write_simulator_prompt(args: dict[str, Any]) -> dict[str, Any]:
        prompt = str(args.get("prompt") or "")
        problems = validate_simulator_prompt(prompt, require_persona=contract.conversational)
        if problems:
            return _err("Not saved:\n  - " + "\n  - ".join(problems))
        path = save_simulator_prompt(prompt, destination)
        from ..simulator import variables_in

        return _ok(
            f"Saved to {path}. Scenarios must fill: "
            + ", ".join(sorted(variables_in(prompt)))
        )

    @tool(
        "add_sub_goal",
        "Add a named thing this agent can be checked on, shared by every scenario that needs "
        "it. Defined here, once, so results roll up: the same sub-goal failing in seven of "
        "twelve scenarios is one sentence.\n\n"
        "`check` is Python: define check(world, calls) returning a sentence when something is "
        "wrong, or None when it held. `world` is the environment afterwards; `calls` is every "
        "tool call made, each with .name, .arguments, .ok and .refused — so a check can insist "
        "a call happened with the right arguments, not merely that it happened.\n\n"
        "Use `judged` only where nothing observable settles it, saying what a model has to "
        "decide and why code cannot.",
        schema(
            {"name": str, "what": str, "check": str, "judged": str}, ["name", "what"]
        ),
    )
    async def add_sub_goal(args: dict[str, Any]) -> dict[str, Any]:
        sub_goal = SubGoal(
            name=str(args.get("name") or ""),
            what=str(args.get("what") or ""),
            check=str(args.get("check") or ""),
            judged=str(args.get("judged") or ""),
        )
        problems = validate_sub_goal(sub_goal)
        if problems:
            return _err("Not added:\n  - " + "\n  - ".join(problems))
        # Run it here, the same way a handler is run the moment it is defined. A check that raises
        # is not a check, and accepting one now means every scenario that names it is refused later
        # for a reason that looks like the scenario's fault rather than this one's.
        if sub_goal.deterministic():
            outcome = run_check(sub_goal.check, world, list(world.calls), name=sub_goal.name)
            if outcome.broken:
                return _err(
                    f"Not added. {sub_goal.name} is not a working check: {outcome.said}\n\n"
                    f"{world.shapes()}\n\n"
                    "It does not have to hold against the world as it stands, since a sub-goal is "
                    "about what a run leaves behind. It does have to run without raising."
                )
        catalogue.sub_goals = [
            one for one in catalogue.sub_goals if one.name != sub_goal.name
        ]
        catalogue.sub_goals.append(sub_goal)
        save_catalogue(catalogue, destination)
        settled = sum(1 for one in catalogue.sub_goals if one.deterministic())
        return _ok(
            f"{sub_goal.name} added. The catalogue has {len(catalogue.sub_goals)}, "
            f"{settled} settled by code: " + ", ".join(sorted(catalogue.names()))
        )

    @tool(
        "write_env_file",
        "Write one file the environment is built from: a Dockerfile, a compose file, a schema, an "
        "entrypoint, whatever this agent needs. Paths are relative and stay inside the "
        "environment directory. Call it once per file, then build with run_env_command.",
        schema({"path": str, "contents": str}, ["path", "contents"]),
    )
    async def write_env_file(args: dict[str, Any]) -> dict[str, Any]:
        from .workspace import listing, write

        try:
            written = write(destination, str(args["path"]), str(args["contents"]))
        except ValueError as refused:
            return _err(str(refused))
        lines = len(str(args["contents"]).splitlines())
        return _ok(
            f"wrote {written.name}, {lines} lines. The environment now has: "
            + ", ".join(listing(destination))
        )

    @tool(
        "run_env_command",
        "Run one docker or docker compose command from the environment directory: build an image, "
        "bring a store up, run something inside a container. Only container commands run here, so "
        "anything the environment needs belongs in a file it builds from rather than in a "
        "command. Returns the exit code and the output.",
        schema({"command": str}, ["command"]),
    )
    async def run_env_command(args: dict[str, Any]) -> dict[str, Any]:
        from .workspace import run

        # Off the event loop: a docker build takes minutes, and run synchronously
        # it deafens every API endpoint this server has until it finishes.
        code, output = await asyncio.to_thread(run, destination, str(args["command"]))
        shown = output if len(output) <= 2500 else output[:1200] + "\n...\n" + output[-1200:]
        if code != 0:
            return _err(f"exit {code}\n{shown or '(no output)'}")
        return _ok(f"ok\n{shown or '(no output)'}")

    @tool(
        "write_store_ops",
        "Teach the harness an engine it has never stood up: the image, the port it listens on, "
        "the environment it needs to boot, and how to read, reset and change what it holds. "
        "Only needed when the agent's engine is not one inspect_world already lists. Registering "
        "it says nothing about whether it works: that is decided by proving it, not by either "
        f"of us.\n\n{OPS_API}",
        schema(
            {
                "engine": str,
                "image": str,
                "container_port": int,
                "boot_env": dict,
                "dsn_template": str,
                "code": str,
            },
            ["engine", "image", "container_port", "code"],
        ),
    )
    async def write_store_ops(args: dict[str, Any]) -> dict[str, Any]:
        from .stores import StoreError, supported
        from .stores.written import register_written

        try:
            register_written(
                engine=str(args["engine"]),
                image=str(args["image"]),
                container_port=int(args["container_port"]),
                boot_env={str(k): str(v) for k, v in (args.get("boot_env") or {}).items()},
                dsn_template=str(args.get("dsn_template") or ""),
                code=str(args["code"]),
            )
        except (StoreError, SyntaxError, ValueError) as exc:
            return _err(f"not registered: {exc}")
        return _ok(
            f"{args['engine']} registered, alongside {', '.join(supported())}. Whether its "
            "reset is right is decided when the environment is proved, not now."
        )

    @tool(
        "add_world_check",
        "Add one check that decides whether this world is usable. Python defining "
        "check(world) which returns None when it holds, or a sentence saying what is wrong. "
        "`world.state()` gives every collection and its contents. It runs immediately, and it is "
        "later put through a world that has been broken on purpose: a check that stays green "
        "there is not checking anything.",
        schema({"name": str, "code": str, "what": str}, ["name", "code"]),
    )
    async def add_world_check(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args["name"])
        source = str(args["code"])
        outcome = run_world_check(source, world, name=name)
        if outcome.broken:
            return _err(
                f"{name} is not a working check: {outcome.said}\n\n"
                f"{_shapes(world)}\n\n{WORLD_CHECK_HELP}"
            )
        world_checks[name] = source
        held = "holds" if outcome.held else f"fails right now: {outcome.said}"
        return _ok(
            f"{name} added, {len(world_checks)} checks: {', '.join(sorted(world_checks))}.\n"
            f"Against the world as it stands it {held}."
        )

    @tool(
        "check_world",
        "Exercise every tool with a valid call, a nonexistent id, and a missing argument, then "
        "run the declared sequences. Reports what is wrong without saving anything.\n\n"
        "Sequences are run independently from the frozen world, so a failure is never caused by "
        "another sequence. Fix the failures it names; declaring more sequences only adds more "
        "probes to pass.",
        {},
    )
    async def check_world(_args: dict[str, Any]) -> dict[str, Any]:
        report = probe(world, contract, sequences=sequences, kind=kind)
        scores.append(report.score)
        # Saying the score is going nowhere, rather than leaving it to be noticed. A stage that
        # has misdiagnosed something will otherwise keep applying the same non-fix, and every
        # round of that costs money and gets no closer.
        stuck = ""
        if len(scores) >= 3 and len({round(s, 2) for s in scores[-3:]}) == 1:
            stuck = (
                "\n\nThis is the third check with the same score. Whatever you are changing is "
                "not what is failing. Read the failures above literally and fix one of them, or "
                "say what you are stuck on."
            )
        failing, cannot_fail, survived = _verified()
        own = ""
        if world_checks:
            own = f"\n{len(world_checks) - len(failing)}/{len(world_checks)} of your own world checks hold"
            if failing:
                own += "\n  failing: " + ", ".join(failing)
            if cannot_fail:
                own += (
                    "\n  these stayed green even with the world emptied and every tool "
                    "silenced, so they are not checking anything: "
                    + ", ".join(cannot_fail)
                )
            # Said out loud, because the alternative is a person rewriting checks that were right.
            for note in (survived or {}).get(UNDAMAGED, []):
                own += (
                    f"\n  the emptied test could not be run: {note}. Nothing is concluded from "
                    "it, so this is ours to fix rather than yours."
                )
        else:
            own = "\nNo world checks of your own yet. Add them with add_world_check."
        return _ok(f"{report.summary()}\nscore {report.score:.2f}{own}{stuck}")

    @tool(
        "save_world",
        "Freeze the world and write it out. Refused unless it passes its own checks.",
        schema({"notes": str}, []),
    )
    async def save_world(args: dict[str, Any]) -> dict[str, Any]:
        report = probe(world, contract, sequences=sequences, kind=kind)
        if report.score < ACCEPTABLE:
            return _err(
                f"Not saved, the world does not hold up yet.\n{report.summary()}\n"
                f"score {report.score:.2f}, needs {ACCEPTABLE:.2f}"
            )
        if not sequences:
            return _err(
                "Not saved. Declare at least one sequence first: a world whose calls each work "
                "alone can still forget what the previous one did."
            )
        # The world has to prove itself, and the proof has to be capable of failing. Both halves
        # matter: checks nobody wrote verify nothing, and checks that pass a world with no data
        # and no working tools verify nothing either.
        if not world_checks:
            return _err(
                "Not saved. This world has no checks of its own yet. Add them with "
                "add_world_check: what has to be true for this world to be worth testing "
                "against, as code.\n\n" + WORLD_CHECK_HELP
            )
        failing, cannot_fail, _survived = _verified()
        if failing:
            return _err(
                "Not saved. These of your own world checks do not hold: "
                + ", ".join(failing)
                + ".\nFix the world, or the check if the check is what is wrong."
            )
        if cannot_fail:
            return _err(
                "Not saved. These checks stayed green with the world emptied and every tool "
                "silenced, so they are not verifying anything: "
                + ", ".join(cannot_fail)
                + ".\nA check has to inspect something that could actually be wrong. Make each "
                "of them read the part of the world it claims to be about, and fail when it is "
                "missing."
            )
        # The environment is not only the world. Every scenario is a delta on what is built
        # here, so a catalogue nobody wrote means every scenario invents its own wording and
        # nothing rolls up across the suite.
        if not catalogue.sub_goals:
            return _err(
                "Not saved. No sub-goals yet. They are defined here, once, and every scenario "
                "names the ones it needs — that is what makes results add up across the suite. "
                "Add them with add_sub_goal."
            )
        settled = [one for one in catalogue.sub_goals if one.deterministic()]
        if not settled:
            return _err(
                "Not saved. Every sub-goal is judged by a model. Most of what this agent does "
                "leaves a trace in the world or in its calls, and those should be settled by "
                "code; a judge is the fallback for what leaves none."
            )
        if contract.conversational and not load_simulator_prompt(destination):
            return _err(
                "Not saved. This agent is conversational, so it needs a simulator prompt for "
                "the person on the other side. Write it with write_simulator_prompt."
            )
        if contract.conversational:
            problems = validate_simulator_prompt(
                load_simulator_prompt(destination), require_persona=True
            )
            if problems:
                return _err("Not saved. The simulator prompt is incomplete:\n  - " + "\n  - ".join(problems))
        dirty = dirty_state(world, sequences, kind)
        if dirty:
            counts = world.state()
            listed = ", ".join(f"{name} ({len(counts[name])} rows)" for name in dirty)
            return _err(
                f"Not saved. These hold rows left over from building: {listed}.\n"
                "This is the state every scenario starts from, so those rows would appear in "
                "every test as somebody else's order already in the cart. Clear them with "
                "change_data (DELETE FROM ...), keep the catalogue, and save again."
            )
        # What the world publishes when something resets it. Without this a restored world
        # announces no tools at all, so anything driving it through the environment interface
        # sees an agent with nothing to call.
        world.tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": {
                    arg: {
                        "type": spec.arg_types.get(arg, "str"),
                        "values": spec.arg_values.get(arg),
                    }
                    for arg in spec.args
                },
            }
            for spec in contract.tools
        ]
        path = save(
            world,
            destination,
            notes=str(args.get("notes") or ""),
            sequences=sequences,
            # Written out with the world. They are judgement about this agent, and a world reopened
            # without them would have to have them rewritten before it could be saved again.
            world_checks=world_checks,
        )
        tables = world.state()
        return _ok(
            f"Saved to {path}.\n"
            f"{len(world.handlers)} tools, {len(tables)} collections, "
            f"{sum(_size(held) for held in tables.values())} records, "
            f"{len(world_checks)} world checks.\n"
            f"score {report.score:.2f}"
        )

    server = create_sdk_mcp_server(
        name=WORLD_SERVER,
        version="0.1.0",
        tools=[
            create_schema,
            seed,
            change_data,
            define_handler,
            run_tool,
            declare_sequence,
            drop_sequence,
            amend_contract,
            cannot_reach_tool,
            add_rule_tool,
            drop_rule_tool,
            fix_tool_tool,
            set_modality_tool,
            inspect_world,
            write_simulator_prompt,
            add_sub_goal,
            adopt_state,
            adopt_store,
            adopt_tool,
            write_store_ops,
            add_world_check,
            write_env_file,
            run_env_command,
            check_world,
            save_world,
        ],
    )
    return server, world


TOOL_NAMES = (
    "create_schema",
    "seed",
    "change_data",
    "adopt_state",
    "adopt_store",
    "adopt_tool",
    "define_handler",
    "run_tool",
    "declare_sequence",
    "drop_sequence",
    "amend_contract",
    "cannot_reach_tool",
    "add_rule",
    "drop_rule",
    "fix_tool",
    "set_modality",
    "inspect_world",
    "write_simulator_prompt",
    "add_sub_goal",
    "write_store_ops",
    "add_world_check",
    "write_env_file",
    "run_env_command",
    "check_world",
    "save_world",
)
