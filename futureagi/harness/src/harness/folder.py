"""A scenario as a folder of files, and running the code inside it.

A scenario used to be a row in one big JSON file, and its setup was a list of rows to insert.
That was enough while every world was a database. It stopped being enough the moment a world
could hold a service as well as a table: "the weather service starts returning errors" is not
expressible as rows, and neither is "the file is missing" or "the queue is backed up".

So a scenario owns a folder, and the parts that are logic are files:

    scenarios/<name>/
        scenario.json     what it is: instruction, solution, which sub-goals
        setup.py          def setup(world)  — the changes this scenario makes
        ready.py          def ready(world)  — is the world ready for this scenario
        checks/<goal>.py  def check(world, calls) — one per deterministic sub-goal

The files are the artifact, not a rendering of one. Each is executable on its own, so a check
can be run by hand against what a run left behind and answer exactly what it answers inside the
harness. That is the whole point of them being files: something you can open, read and run is
something you can argue with.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalogue import Catalogue
from .scenario import Scenario
from .world.runtime import GeneratedWorld

SCENARIOS = "scenarios"
INDEX = "scenarios.json"

# Appended to every check file the harness writes. The model writes only ``check(world, calls)``;
# this is what makes that same file runnable by a person, so nobody has to keep two versions of
# one truth in step.
_RUNNABLE = '''

if __name__ == "__main__":
    # Run this check by hand against what a run left behind. The first argument is anything
    # inside the saved world's folder, because not every world has a database to name:
    #     python <this file> <world folder>/manifest.json [calls.json]
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    from harness.world.runtime import Call as _Call
    from harness.world.snapshot import restore as _restore

    _world = _restore(_Path(_sys.argv[1]).parent) if len(_sys.argv) > 1 else None
    _calls = []
    if len(_sys.argv) > 2:
        _calls = [_Call(**_one) for _one in _json.loads(_Path(_sys.argv[2]).read_text())]
    _said = check(_world, _calls)
    print("held" if _said is None else f"FAILED: {_said}")
    raise SystemExit(0 if _said is None else 1)
'''


@dataclass
class Outcome:
    """What one piece of a scenario's own code did."""

    ok: bool
    said: str = ""
    broken: bool = False


def _run(source: str, name: str, entry: str, *args: Any) -> Outcome:
    """Execute one function out of a scenario's own code.

    A file that will not compile, or that raises, is **broken** rather than failing: it is our
    mistake, and scoring it as though the world were wrong would send somebody looking in the
    wrong place.
    """
    if not source.strip():
        return Outcome(True)
    namespace: dict[str, Any] = {}
    try:
        exec(compile(source, f"<{name}>", "exec"), namespace)
    except Exception as failed:
        return Outcome(False, f"{name} would not compile: {failed}", broken=True)

    function = namespace.get(entry)
    if not callable(function):
        return Outcome(False, f"{name} defines no {entry}()", broken=True)
    try:
        said = function(*args)
    except Exception as failed:
        return Outcome(
            False, f"{name} raised {type(failed).__name__}: {failed}", broken=True
        )
    # The convention is that a complaint is a sentence, and anything else means it held. An empty
    # string is the case worth naming: it reads as "no complaint" to whoever wrote it, and taking
    # it as a failure produces a rejection with no reason attached, which cannot be acted on and
    # sends the author hunting for a problem that is not there.
    if said is None or said is True or (isinstance(said, str) and not said.strip()):
        return Outcome(True)
    if said is False:
        return Outcome(
            False,
            f"{name} returned False without saying what is wrong. Return the sentence instead, "
            "or None if it holds.",
        )
    return Outcome(False, str(said))


def apply_setup(scenario: Scenario, world: GeneratedWorld) -> Outcome:
    """Make this scenario's changes to the world."""
    return _run(scenario.setup_code, f"{scenario.name}/setup.py", "setup", world)


def check_ready(scenario: Scenario, world: GeneratedWorld) -> Outcome:
    """Whether the world now holds what this scenario presumes."""
    return _run(scenario.ready_code, f"{scenario.name}/ready.py", "ready", world)


def folder_for(destination: Path, name: str) -> Path:
    return Path(destination) / SCENARIOS / name


def write_folder(scenario: Scenario, catalogue: Catalogue, destination: Path) -> Path:
    """Write one scenario out as its own folder of files."""
    root = folder_for(destination, scenario.name)
    (root / "checks").mkdir(parents=True, exist_ok=True)

    body = scenario.model_dump()
    # The code lives in its own files; keeping a second copy in the JSON would let the two drift
    # and leave nobody able to say which one ran.
    body.pop("setup_code", None)
    body.pop("ready_code", None)
    (root / "scenario.json").write_text(
        json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    (root / "setup.py").write_text(
        scenario.setup_code
        or "def setup(world):\n    \"\"\"This scenario runs on the base world unchanged.\"\"\"\n",
        encoding="utf-8",
    )
    (root / "ready.py").write_text(
        scenario.ready_code
        or "def ready(world):\n    \"\"\"Nothing beyond the base world is presumed.\"\"\"\n",
        encoding="utf-8",
    )

    for name in scenario.sub_goals:
        sub_goal = catalogue.named(name)
        if sub_goal is None or not sub_goal.deterministic():
            continue
        (root / "checks" / f"{name}.py").write_text(
            sub_goal.check.rstrip() + "\n" + _RUNNABLE, encoding="utf-8"
        )
    return root


def read_folder(destination: Path, name: str) -> Scenario | None:
    """One scenario, reassembled from its folder."""
    root = folder_for(destination, name)
    body = root / "scenario.json"
    if not body.exists():
        return None
    payload = json.loads(body.read_text(encoding="utf-8"))
    for field, filename in (("setup_code", "setup.py"), ("ready_code", "ready.py")):
        path = root / filename
        payload[field] = path.read_text(encoding="utf-8") if path.exists() else ""
    return Scenario.model_validate(payload)


def write_index(scenarios: list[Scenario], destination: Path) -> Path:
    """The whole suite at a glance, over the folders.

    Regenerated from the folders rather than maintained alongside them, so it can never disagree
    with what is actually on disk.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / INDEX
    path.write_text(
        json.dumps(
            [
                {
                    "name": one.name,
                    "use_case": one.use_case,
                    "tests": one.tests,
                    "instruction": one.instruction,
                    "sub_goals": one.sub_goals,
                    "steps": len(one.solution),
                    "folder": f"{SCENARIOS}/{one.name}",
                }
                for one in scenarios
            ],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def read_all(destination: Path) -> list[Scenario]:
    """Every scenario on disk, read from the folders."""
    root = Path(destination) / SCENARIOS
    if not root.exists():
        return []
    found: list[Scenario] = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        try:
            scenario = read_folder(destination, folder.name)
        except Exception:
            # A folder we cannot read is skipped rather than crashing the stage: the rest of the
            # suite is still usable, and the gap shows up as a missing scenario.
            continue
        if scenario is not None:
            found.append(scenario)
    return found
