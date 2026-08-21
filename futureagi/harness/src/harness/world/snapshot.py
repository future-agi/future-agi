"""Freezing a world, and starting every scenario from the same frozen copy.

The database is built once and snapshotted; that snapshot is the base state. A scenario restores
its own copy and layers on whatever it additionally needs, so scenarios cannot inherit each
other's leftovers and a run is repeatable a week later.

Which is why the overlay exists: a scenario that needs a customer with three open orders adds
those rows to a restored copy rather than editing the snapshot. The base world stays the shared
starting point instead of drifting toward whichever scenario was written last.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from .runtime import GeneratedWorld

DATABASE = "world.sqlite"
HANDLERS = "handlers"
MANIFEST = "manifest.json"
STATE = "state.json"


def saved(path: str | Path | None) -> bool:
    """Whether a world has been written here.

    One function, because this question gets asked from six places: the build stage, the
    conversation, the session listing, the CLI and the UI. Asked as "is there a world.sqlite"
    each of those was really asking "is this a SQLite world", so an agent whose state lives in
    services and files saved a world that scored 1.00 and was then invisible to all of them.
    """
    return bool(path) and (Path(path) / MANIFEST).exists()


WORLD_MODULE = "world.py"

_MODULE = '''"""Generated world for {agent}. Do not edit by hand; regenerate instead.

{notes}
"""

from pathlib import Path

from harness.world.runtime import GeneratedWorld

_HERE = Path(__file__).parent

TOOLS = {tools}


class World(GeneratedWorld):
    name = {agent!r}
    tools = TOOLS
    handlers = {{
        name: (_HERE / "handlers" / f"{{name}}.py").read_text(encoding="utf-8")
        for name in {handler_names}
    }}


def load(database=None):
    """This world, restored from the snapshot beside this file.

    Through `restore` rather than by opening a database directly, because not every world has
    one: an agent whose state lives in services and files keeps its records in the snapshot, and
    naming a SQLite file would hand back an empty world instead of this one.
    """
    from harness.world.snapshot import restore

    return restore(_HERE, into=database) if database else restore(_HERE)
'''


def save(
    world: GeneratedWorld,
    path: str | Path,
    *,
    notes: str = "",
    sequences: list[dict[str, Any]] | None = None,
    world_checks: Mapping[str, str] | None = None,
) -> Path:
    """Write the world out: the snapshot, the handlers, the module, and a manifest."""
    root = Path(path)
    (root / HANDLERS).mkdir(parents=True, exist_ok=True)

    # Through the store, so a world whose records live somewhere other than a SQLite file, or
    # nowhere at all, freezes by its own means rather than by one assumed here.
    world.store.save_to(root)

    for name, source in world.handlers.items():
        (root / HANDLERS / f"{name}.py").write_text(source, encoding="utf-8")

    (root / WORLD_MODULE).write_text(
        _MODULE.format(
            agent=world.name,
            notes=notes or "Generated from the agent's contract.",
            tools=json.dumps(world.tools, indent=4),
            handler_names=json.dumps(sorted(world.handlers)),
        ),
        encoding="utf-8",
    )

    # The agent's own in-memory state, where its tools keep what they act on there rather
    # than in the database. Frozen as JSON so restoring is the exact reverse, and so a
    # person can read what the world starts from.
    if world.state_object is not None:
        # Round-tripped rather than only written. Every scenario restores from this file, so state
        # that does not survive the trip would come back subtly different and every check after
        # it would be grading something else. Better to fail here than to be wrong quietly.
        frozen = json.dumps(world.state_object, indent=2, default=str)
        if json.loads(frozen) != world.state_object:
            raise ValueError(
                "the agent's state does not survive being frozen as JSON, so restoring it would "
                "not give back what was saved. Every scenario starts from that restore, so this "
                "world cannot be trusted. What is in the state that is not plain JSON?"
            )
        (root / STATE).write_text(frozen, encoding="utf-8")

    state = world.state()
    (root / MANIFEST).write_text(
        json.dumps(
            {
                "agent": world.name,
                # Which store this world used, so restoring it opens the same one rather
                # than assuming a database that may never have existed.
                "store": getattr(world.store, "key", "sqlite"),
                "tools": sorted(world.handlers),
                # Written because restore reads it. Without it a restored world publishes no
                # tool descriptions at all, and every later stage has to reconstruct them.
                "tool_specs": list(world.tools),
                "tables": {name: len(rows) for name, rows in state.items()},
                # Kept because they are judgement about this agent, not something a schema
                # implies. A world picked up again can be re-verified without redeclaring them.
                "sequences": list(sequences or []),
                # The world's own checks are judgement about this agent, so a world picked
                # up again keeps them rather than having them rewritten from scratch.
                "world_checks": dict(world_checks or {}),
                # Where the agent's own code lives. Kept because a restored world has to
                # be able to import the tools it was bound to, and a scenario run happens
                # long after the build stage that found the path.
                "source_root": world.source_root,
                # How this agent says no in a returned value. Without it a restored world
                # cannot tell a refusal from a success, so every run records "Error: no such
                # order" as if the call worked, and a check asking whether the agent was
                # refused is answered wrongly rather than reported as unanswerable.
                "refusal_signature": world.refusal_signature,
                "notes": notes,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def restore(path: str | Path, *, into: str | Path | None = None) -> GeneratedWorld:
    """A fresh, independent copy of the frozen world.

    In memory by default, because a scenario should not be able to write back into the snapshot
    every later scenario depends on.
    """
    root = Path(path)
    source = root / DATABASE
    if not (root / MANIFEST).exists():
        raise FileNotFoundError(f"no world snapshot at {root}")

    manifest = read_manifest(root)
    handlers = {
        name: (root / HANDLERS / f"{name}.py").read_text(encoding="utf-8")
        for name in manifest.get("tools", [])
        if (root / HANDLERS / f"{name}.py").exists()
    }

    named = str(manifest.get("store") or "sqlite")
    if into is None:
        world = GeneratedWorld(":memory:", kind=named)
        # Only where there is one. A world whose records the agent's own code keeps has no
        # database file, and demanding one would make it unrestorable.
        if source.exists() and getattr(world.store, "connection", None) is not None:
            origin = sqlite3.connect(source)
            with world.connection:
                origin.backup(world.connection)
            origin.close()
        else:
            # A store that keeps its records somewhere other than a SQLite file loads them its own
            # way. Without this the world comes back with an empty store, and everything a check
            # reads is whatever happened to land in the agent's state instead.
            world.store.load_from(root)
    else:
        target = Path(into)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        world = GeneratedWorld(target, kind=named)

    world.name = manifest.get("agent", "generated")
    world.handlers = handlers
    world.tools = manifest.get("tool_specs", [])
    world.refusal_signature = str(manifest.get("refusal_signature") or "")
    # A world whose handlers bind to the agent's own code cannot run them unless that code
    # is importable again, and the frozen state is what those tools act on.
    reached = str(manifest.get("source_root") or "")
    if reached:
        world.reach(reached)
    frozen_state = root / STATE
    if frozen_state.exists():
        world.state_object = json.loads(frozen_state.read_text(encoding="utf-8"))
    return world


def read_manifest(path: str | Path) -> dict[str, Any]:
    return json.loads((Path(path) / MANIFEST).read_text(encoding="utf-8"))


def apply_overlay(world: GeneratedWorld, overlay: Mapping[str, Any] | None) -> int:
    """Layer one scenario's own rows onto a restored world.

    ``{"table": [{"column": value}, ...]}``. The only sanctioned way a scenario adds data, so the
    base world stays the shared starting point rather than drifting per scenario.
    """
    written = 0
    for table, rows in (overlay or {}).items():
        for row in rows or []:
            if not isinstance(row, Mapping) or not row:
                continue
            columns = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            world.connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(row.values())
            )
            written += 1
    world.connection.commit()
    return written
