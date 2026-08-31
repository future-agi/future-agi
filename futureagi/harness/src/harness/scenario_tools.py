"""The tools that write scenarios, and the gates that decide one may be kept.

A scenario is accepted by being *proved*, not by looking right. ``submit_scenario`` puts it
through three gates, in order: the world must end up holding what the scenario presumes, the
reference solution must pass the scenario's own checks, and those same checks must fail when
nothing is done at all.

Every gate is code. No model is asked whether a scenario is good; the environment decides. A
scenario that clears all three is written out as its own folder of runnable files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from .amend import add_rule, drop_rule, fix_tool, widen
from .contract import AgentContract
from .catalogue import Catalogue, SubGoal, load_catalogue, save_catalogue, validate_sub_goal
from .simulator import load_simulator_prompt
from .folder import SCENARIOS, apply_setup, read_all, write_folder, write_index
from .prove import prepared, prove
from .scenario import Scenario, validate_scenario
from .tools import brief, schema
from .world.snapshot import restore

SCENARIO_SERVER = "scenarios"


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def write_scenarios(
    scenarios: list[Scenario], destination: Path, catalogue: Catalogue | None = None
) -> Path:
    """Write every scenario out as its own folder, and regenerate the index over them."""
    catalogue = catalogue if catalogue is not None else load_catalogue(destination)
    for one in scenarios:
        write_folder(one, catalogue, destination)
    _forget_dropped(scenarios, destination)
    return write_index(scenarios, destination)


def _forget_dropped(scenarios: list[Scenario], destination: Path) -> None:
    """Remove the folders of scenarios that are no longer in the suite.

    The folders are the truth, and they are what gets read back. Writing the survivors without
    taking the others away means a dropped scenario returns on the next load, still failing, and
    dropping it appears to do nothing at all.
    """
    import shutil

    root = Path(destination) / SCENARIOS
    if not root.exists():
        return
    keeping = {one.name for one in scenarios}
    for folder in root.iterdir():
        if folder.is_dir() and folder.name not in keeping:
            shutil.rmtree(folder)


def load_scenarios(destination: Path) -> list[Scenario]:
    """Every scenario on disk, read from its folder.

    The folders are the truth. The index beside them is regenerated from these, so it can
    describe them but never contradict them.
    """
    return read_all(destination)


def accept_scenario(
    payload: dict[str, Any],
    *,
    world_root: Path,
    catalogue: Catalogue,
    kept: list[Scenario],
    simulator_prompt: str = "",
) -> dict[str, Any]:
    """Validate one scenario, then prove it. A plain function so both halves are testable."""
    try:
        scenario = Scenario.model_validate(payload)
    except Exception as invalid:
        return _err(f"Not kept. {invalid}"[:600])

    # Read against the world this scenario actually runs in, so a setup that creates the table
    # a check reads is not reported as referring to something that does not exist.
    trial, _applied, _ready = prepared(scenario, world_root)
    try:
        problems = validate_scenario(scenario, catalogue, trial.state(), simulator_prompt)
    finally:
        trial.close()

    if problems:
        return _err("Not kept. Fix these and submit again:\n  - " + "\n  - ".join(problems))

    proof = prove(scenario, catalogue, world_root)
    if not proof.holds:
        said = f"Not kept. {proof.why()}"
        # Code written against the wrong collection shape is the commonest way setup, ready and a
        # check fail here, and the exception alone does not say which collections are mappings and
        # which are lists. The world is asked, so the answer names them.
        if "attribute" in said.lower() or "not subscriptable" in said.lower():
            world = restore(world_root)
            try:
                said += f"\n\n{world.shapes()}"
            finally:
                world.close()
        return _err(said)

    replaced = any(one.name == scenario.name for one in kept)
    kept[:] = [one for one in kept if one.name != scenario.name]
    kept.append(scenario)
    weak = (
        "\nWorth tightening: "
        + ", ".join(proof.weak)
        + " still held with nothing done. The scenario is graded by its other checks, so it was "
        "kept, but those sub-goals will report themselves as held for an agent that did nothing. "
        "A check that asserts the attempt, not only the state it leaves, cannot do that."
        if proof.weak
        else ""
    )
    return _ok(
        f"{scenario.name} {'replaced' if replaced else 'kept'}. All three gates pass: the world "
        "is ready for it, the reference solution passes its checks, and those checks fail when "
        f"nothing is done.{weak}\n{len(kept)} so far: " + ", ".join(one.name for one in kept)
    )


def not_ready(kept: list[Scenario], wanted: int, catalogue: Catalogue) -> list[str]:
    """Why this suite is not worth saving yet."""
    problems: list[str] = []
    if len(kept) < wanted:
        problems.append(
            f"{len(kept)} of the {wanted} asked for. The ones that find something are usually "
            "the awkward ones, so this is worth finishing rather than stopping here. If nobody "
            f"asked for {wanted}, record what they did ask for with aim_for."
        )
    elif len(kept) > wanted:
        problems.append(
            f"{len(kept)} scenarios against a target of {wanted}. If they asked for more, "
            "aim_for records the new size; reopening a suite starts with the target set to what "
            "is already there, so adding to one always reads like this. If you wrote extra "
            "nobody asked for, drop_scenario takes them off."
        )
    # Two scenarios claiming the same use case are either the same test twice, or one of them is
    # mislabelled. Both happened in the same suite: a delivered-order refusal was filed under
    # "cancel a pending order", which is neither what it tests nor distinguishable afterwards
    # from the scenario that really does test that. A use case is how coverage is counted, so a
    # duplicate quietly overstates it.
    claimed: dict[str, list[str]] = {}
    for one in kept:
        case = (one.use_case or "").strip().lower()
        if case:
            claimed.setdefault(case, []).append(one.name)
    for case, names in claimed.items():
        if len(names) > 1:
            problems.append(
                f"{' and '.join(names)} both claim the use case {case!r}. Give each the use case "
                "it actually exercises, or drop the one that duplicates the other. Coverage is "
                "counted by use case, so two scenarios sharing one hides a gap."
            )

    # Sub-goals are shared so results roll up. A suite where every scenario invents its own is a
    # suite whose results cannot be added together.
    used = [name for one in kept for name in one.sub_goals]
    if kept and len(used) > 2 and len(set(used)) == len(used):
        problems.append(
            "no sub-goal is used by more than one scenario, so nothing rolls up across the "
            "suite. Reuse the catalogue where the same thing is being checked."
        )
    return problems


def scenario_tools(
    contract: AgentContract, world_root: Path, destination: Path, *, wanted: int
) -> tuple[Any, list[Scenario]]:
    """A server for writing scenarios against one built environment."""
    kept: list[Scenario] = load_scenarios(destination)
    catalogue = load_catalogue(destination)
    simulator_prompt = load_simulator_prompt(destination)
    target = {"count": wanted}

    scenario_required = ["name", "instruction", "solution", "sub_goals"]
    if contract.conversational:
        scenario_required.append("persona")

    @tool(
        "inspect_world",
        "Look at what is in the world. Without a table, lists the tables and how many rows each "
        "holds; with one, returns rows from it. `matching` is plain text, not SQL.",
        schema({"table": str, "limit": int, "matching": str}, []),
    )
    async def inspect_world(args: dict[str, Any]) -> dict[str, Any]:
        world = restore(world_root)
        try:
            state = world.state()
            table = str(args.get("table") or "")
            if not table:
                lines = [f"{n}: {len(r)} rows" for n, r in sorted(state.items())]
                if catalogue.sub_goals:
                    lines.append(
                        "\nsub-goals available: " + ", ".join(sorted(catalogue.names()))
                    )
                return _ok("\n".join(lines) or "this world has no tables")
            if table not in state:
                return _err(f"no table {table!r}; this world has {', '.join(sorted(state))}")
            rows = state[table]
            matching = str(args.get("matching") or "").strip()
            if matching:
                needle = matching.lower()
                found = [r for r in rows if needle in json.dumps(r, default=str).lower()]
                if not found:
                    return _ok(
                        f"nothing in {table} contains {matching!r}, but it holds {len(rows)} rows."
                    )
                rows = found
            shown = rows[: int(args.get("limit") or 20)]
            return _ok(
                f"{len(rows)} rows, showing {len(shown)}:\n"
                + "\n".join(json.dumps(r, default=str) for r in shown)
            )
        finally:
            world.close()

    @tool(
        "try_calls",
        "Run calls against a throwaway copy of the world and see the state they leave. Use it to "
        "work out a scenario's solution and what its checks should assert.\n\n"
        "`setup_code` is optional: pass the same code you intend to give the scenario and the "
        "calls run against a world it has already changed, so you can see what the agent would "
        "actually face. Nothing is saved.",
        schema({"calls": list, "setup_code": str}, ["calls"]),
    )
    async def try_calls(args: dict[str, Any]) -> dict[str, Any]:
        world = restore(world_root)
        try:
            world.reset()
            trial = Scenario(name="trial", setup_code=str(args.get("setup_code") or ""))
            applied = apply_setup(trial, world)
            if not applied.ok:
                return _err(f"the setup did not run: {applied.said}")
            world.calls = []
            lines: list[str] = []
            for step in args.get("calls") or []:
                if not isinstance(step, dict):
                    return _err("each call must be an object with a tool and arguments")
                call = world.call(str(step.get("tool") or ""), step.get("arguments") or {})
                if call.refused:
                    lines.append(f"{call.name}: refused — {call.error}")
                elif not call.ok:
                    lines.append(f"{call.name}: CRASHED — {call.error}")
                else:
                    lines.append(f"{call.name}: ok — {brief(call.result)}")
            state = world.state()
            lines.append(
                "state afterwards: "
                + ", ".join(f"{n}.count={len(r)}" for n, r in sorted(state.items()))
            )
            for name, rows in sorted(state.items()):
                if rows and len(rows) <= 6:
                    lines.append(f"{name}: " + brief(rows, limit=1200))
            return _ok("\n".join(lines) or "no calls were made")
        finally:
            world.close()

    @tool(
        "add_sub_goal",
        "Add a named thing this agent can be checked on, shared by every scenario that needs it. "
        "`check` is Python: define check(world, calls) returning a sentence when something is "
        "wrong, or None when it held. `world` is the environment afterwards; `calls` is every "
        "tool call made, each with .name, .arguments, .ok and .refused — so a check can insist a "
        "call happened with the right arguments, not merely that it happened.\n\n"
        "Use `judged` only where nothing observable settles it, saying what a model must decide "
        "and why code cannot.",
        schema({"name": str, "what": str, "check": str, "judged": str}, ["name", "what"]),
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
        catalogue.sub_goals = [one for one in catalogue.sub_goals if one.name != sub_goal.name]
        catalogue.sub_goals.append(sub_goal)
        save_catalogue(catalogue, destination)
        return _ok(
            f"{sub_goal.name} added"
            + ("" if sub_goal.deterministic() else " (judged, not deterministic)")
            + f". The catalogue has {len(catalogue.sub_goals)}: "
            + ", ".join(sorted(catalogue.names()))
        )

    @tool(
        "submit_scenario",
        "Keep one scenario. It is put through three gates before it is kept, and told which one "
        "failed if any does:\n"
        "  1. ready     — the world is restored, setup_code runs, then ready_code. The world "
        "must end up holding what this scenario presumes.\n"
        "  2. solvable  — the reference solution is played through that world and the checks of "
        "every sub-goal named must pass.\n"
        "  3. not vacuous — the same checks run again with nothing done at all, and must fail.\n\n"
        "A scenario that clears all three is written out as its own folder of runnable files.",
        schema(
            {
                "name": {
                    "type": "string",
                    "description": "Short identifier, lower case with hyphens or underscores. "
                    "It becomes this scenario's folder name.",
                },
                "use_case": {
                    "type": "string",
                    "description": "Which of the agent's use cases this belongs to.",
                },
                "tests": {
                    "type": "string",
                    "description": "One line: what this scenario is trying to find out.",
                },
                "instruction": {
                    "type": "string",
                    "description": "The task, written to the person the agent is serving. For a "
                    "conversational agent this fills the simulator prompt's slot.",
                },
                "persona": {
                    "type": "object",
                    "description": "Who the simulated person is, separate from the task. Use "
                    "the established voice-scenario shape and only grounded, test-relevant "
                    "details. This fills the simulator prompt's persona slot.",
                    "properties": {
                        "name": {"type": "string"},
                        "gender": {"type": "string"},
                        "age_group": {"type": "string"},
                        "occupation": {"type": "string"},
                        "location": {"type": "string"},
                        "personality": {"type": "string"},
                        "communication_style": {"type": "string"},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "languages": {"type": "array", "items": {"type": "string"}},
                        "accent": {"type": "string"},
                        "multilingual": {"type": "boolean"},
                        "metadata": {"type": "object"},
                    },
                    "required": [
                        "name",
                        "personality",
                        "communication_style",
                        "languages",
                        "accent",
                        "keywords",
                    ],
                },
                "variables": {
                    "type": "object",
                    "description": "Any other slot the simulator prompt asks for, by name. Do "
                    "not put persona here; use the structured persona field.",
                },
                "setup_code": {
                    "type": "string",
                    "description": "Python defining setup(world): the changes this scenario "
                    "makes to the environment before the run. Leave empty to run on the base "
                    "world unchanged. Use world.call(tool, args) to act through the agent's own "
                    "tools, or world.put, world.change and world.drop for what no tool can produce. This is code and not a list of "
                    "rows because a scenario may need more than a table changed.",
                },
                "ready_code": {
                    "type": "string",
                    "description": "Python defining ready(world): return None when the world "
                    "holds what this scenario presumes, or a sentence naming what is missing. "
                    "This is the precondition. If the scenario is about the last five items, "
                    "check there are five. A scenario whose world was never right tests us, not "
                    "the agent.",
                },
                "solution": {
                    "type": "array",
                    "description": "What a correct agent would do: the reference trajectory. "
                    "Never run against the agent under test; it exists to prove the scenario "
                    "can be passed at all.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string"},
                            "arguments": {"type": "object"},
                        },
                    },
                },
                "sub_goals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names from the shared catalogue that must hold. Use the "
                    "existing names wherever one fits, so results add up across the suite.",
                },
                "max_turns": {"type": "integer"},
            },
            scenario_required,
        ),
    )
    async def submit_scenario(args: dict[str, Any]) -> dict[str, Any]:
        return accept_scenario(
            args,
            world_root=world_root,
            catalogue=catalogue,
            kept=kept,
            simulator_prompt=simulator_prompt,
        )

    @tool(
        "amend_contract",
        "Let one of the agent's tools accept values it did not before, when the world holds "
        "something the agent has no way to name. Say why; it is recorded on the contract.",
        schema(
            {"tool_name": str, "argument": str, "values": list, "why": str},
            ["tool_name", "argument", "values", "why"],
        ),
    )
    async def amend_contract(args: dict[str, Any]) -> dict[str, Any]:
        done, said = widen(
            contract,
            world_root,
            tool_name=str(args.get("tool_name") or ""),
            argument=str(args.get("argument") or ""),
            values=[str(v) for v in (args.get("values") or [])],
            why=str(args.get("why") or ""),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "add_rule",
        "Give the agent a hard rule its source did not state, when asked for one. It is told to "
        "the agent under test and graded, so this changes what is being tested. Say why.",
        schema({"rule": str, "why": str}, ["rule", "why"]),
    )
    async def add_rule_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = add_rule(
            contract, world_root, rule=str(args.get("rule") or ""), why=str(args.get("why") or "")
        )
        return _ok(said) if done else _err(said)

    @tool(
        "drop_rule",
        "Take away a hard rule the agent does not really have. Say why.",
        schema({"rule": str, "why": str}, ["rule", "why"]),
    )
    async def drop_rule_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = drop_rule(
            contract, world_root, rule=str(args.get("rule") or ""), why=str(args.get("why") or "")
        )
        return _ok(said) if done else _err(said)

    @tool(
        "fix_tool",
        "Correct a tool that was read wrong, or remove one the agent does not have. Everything "
        "is built from these, so a wrong argument name produces a world that refuses everything.",
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
            world_root,
            tool_name=str(args.get("tool_name") or ""),
            why=str(args.get("why") or ""),
            args=[str(a) for a in args["args"]] if args.get("args") else None,
            arg_types={str(k): str(v) for k, v in (args.get("arg_types") or {}).items()},
            description=str(args.get("description") or ""),
            remove=bool(args.get("remove")),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "aim_for",
        "Set how many scenarios are wanted. Call it whenever the person changes what they are "
        "asking for: a number outright, or asking for more without naming one, in which case the "
        "count is the size of the suite once you have written them. Adding to an existing suite "
        "always needs this, because reopening one starts with the target set to what is already "
        "there.\n\n"
        "What it is not for is saving a suite nobody asked for. Writing extra and then raising "
        "the target to match is how a request for four becomes thirteen that nobody reviews.",
        schema({"count": int}, ["count"]),
    )
    async def aim_for(args: dict[str, Any]) -> dict[str, Any]:
        count = int(args.get("count") or 0)
        if count < 1:
            return _err("that is not a number of scenarios worth writing")
        target["count"] = count
        return _ok(f"aiming for {count}. {len(kept)} written so far")

    @tool(
        "drop_scenario",
        "Remove a scenario by name, or all of them with name '*'.",
        schema({"name": str}, ["name"]),
    )
    async def drop_scenario(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "")
        if name == "*":
            kept.clear()
            return _ok("all scenarios dropped")
        before = len(kept)
        kept[:] = [one for one in kept if one.name != name]
        if len(kept) == before:
            return _err(f"no scenario called {name!r}")
        return _ok(f"{name} dropped. {len(kept)} left")

    @tool(
        "save_scenarios",
        "Write the kept scenarios out. Every one has already been proved by submit_scenario, so "
        "this always saves; anything else worth knowing comes back alongside.",
        schema({}, []),
    )
    async def save_scenarios(_args: dict[str, Any]) -> dict[str, Any]:
        # Always written. Each of these already cleared all three gates on its way in, so this is
        # persistence and not a second opinion: refusing here left proved work in memory only,
        # which is how a suite that asked for fifty and reached twenty-eight saved nothing at all.
        # What is off about the suite is said, not enforced.
        noted = not_ready(kept, target["count"], catalogue)
        path = write_scenarios(kept, destination, catalogue)
        judged = sum(
            1
            for one in kept
            for name in one.sub_goals
            if (found := catalogue.named(name)) and not found.deterministic()
        )
        said = (
            f"Saved {len(kept)} scenarios. Each has its own folder under "
            f"{destination / 'scenarios'} holding scenario.json, setup.py, ready.py and one "
            f"runnable file per check; {path.name} indexes them.\n"
            "Every one cleared all three gates: the world is ready for it, the reference "
            "solution passes its checks, and those checks fail when nothing is done.\n"
            f"{judged} sub-goal references are judged rather than settled by code."
        )
        if noted:
            said += "\n\nWorth looking at, none of it stopping the save:\n  - " + "\n  - ".join(noted)
        return _ok(said)

    server = create_sdk_mcp_server(
        name=SCENARIO_SERVER,
        version="0.1.0",
        tools=[
            inspect_world,
            try_calls,
            add_sub_goal,
            submit_scenario,
            amend_contract,
            add_rule_tool,
            drop_rule_tool,
            fix_tool_tool,
            aim_for,
            drop_scenario,
            save_scenarios,
        ],
    )
    return server, kept


TOOL_NAMES = (
    "inspect_world",
    "try_calls",
    "add_sub_goal",
    "submit_scenario",
    "amend_contract",
    "add_rule",
    "drop_rule",
    "fix_tool",
    "aim_for",
    "drop_scenario",
    "save_scenarios",
)


def world_summary(world_root: Path) -> str:
    """What is in the built environment, for grounding the writer before it asks."""
    world = restore(world_root)
    try:
        state = world.state()
        lines = [f"  {name}: {len(rows)} rows" for name, rows in sorted(state.items())]
        catalogue = load_catalogue(world_root)
        if catalogue.sub_goals:
            lines.append("\nSUB-GOALS already defined (reuse these, do not restate them):")
            lines += [f"  {one.name}: {one.what}" for one in catalogue.sub_goals]
        return "THE BUILT WORLD (restored fresh for every scenario):\n" + "\n".join(lines)
    finally:
        world.close()
