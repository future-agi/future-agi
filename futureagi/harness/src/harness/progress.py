"""What the fan-out is doing right now, written where a UI can read it.

Generating a suite in parallel is the one thing this harness does where nothing appears for
several minutes and then everything appears at once. Told nothing, a person cannot tell a
working run from a hung one, and the honest answer to "is it stuck" is the only thing they want.

So the fan-out writes its own state as it goes: which use cases it split the work into, which
are running, how many scenarios each has proved, and which have finished. A file rather than a
stream, because the reader is a page that may be opened halfway through, refreshed, or opened on
another machine, and each of those has to show the same thing.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

PROGRESS = "generation.json"

WAITING = "waiting"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


def _path(destination: Path) -> Path:
    return Path(destination) / PROGRESS


def _write(destination: Path, state: dict[str, Any]) -> None:
    """Replace the file atomically.

    A reader polling this will otherwise catch a half-written file and show nothing, which looks
    exactly like the failure it is meant to rule out.
    """
    path = _path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as writing:
            json.dump(state, writing, indent=2)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read(destination: Path) -> dict[str, Any]:
    """The current state, or nothing if no suite has been generated here."""
    path = _path(destination)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def planned(
    destination: Path, allocation: list[tuple[str, int]], *, at_once: int, asked: int
) -> None:
    """The split, before any of it starts. Written first so the tree appears immediately."""
    _write(
        destination,
        {
            "state": RUNNING,
            "asked": asked,
            "at_once": at_once,
            "kept": 0,
            "slices": [
                {"use_case": case, "wanted": count, "kept": 0, "state": WAITING}
                for case, count in allocation
            ],
        },
    )


def _change(destination: Path, use_case: str, **fields: Any) -> None:
    state = read(destination)
    for slice_ in state.get("slices", []):
        if slice_.get("use_case") == use_case:
            slice_.update(fields)
            break
    state["kept"] = sum(one.get("kept", 0) for one in state.get("slices", []))
    _write(destination, state)


def started(destination: Path, use_case: str) -> None:
    _change(destination, use_case, state=RUNNING)


def kept(destination: Path, use_case: str, count: int) -> None:
    """How many this slice has proved so far. Called as they land, not at the end."""
    _change(destination, use_case, kept=count)


def finished(destination: Path, use_case: str, count: int) -> None:
    _change(destination, use_case, state=DONE, kept=count)


def failed(destination: Path, use_case: str, why: str) -> None:
    _change(destination, use_case, state=FAILED, why=why[:300])


def settled(destination: Path, *, kept_total: int) -> None:
    """The whole fan-out is over and the suite is written."""
    state = read(destination)
    state["state"] = DONE
    state["kept"] = kept_total
    _write(destination, state)
