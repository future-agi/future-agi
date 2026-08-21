"""The tools that run a scenario against the real agent, and record what happened.

Placing a call was a command before this existed, which made the last stage the only one you
could not simply ask for. Nothing about it needed to be a command: wiring the world to the
assistant and grading afterwards is already code, and choosing which scenario to run and reading
what came back is the part worth having judgement on.

So the same shape as every other stage. The tools do what must be exact — restore the world,
repoint the assistant's own tools, place the call through ALK, run the checks — and the stage
decides what to run and says what it means.

A run takes minutes, not seconds. The tool blocks for that long, and says so, because a stage
that fires a call and returns immediately would report on a conversation that has not happened.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from .. import platform
from ..catalogue import load_catalogue
from ..scenario_tools import load_scenarios
from ..tools import schema
from .call import CASE, place_the_call
from .live import LiveRun, grade, wire

RUN_SERVER = "runs"
RESULTS = "runs.json"

# What a call needs before it can be placed at all, per transport. Checked up front rather than
# three minutes in, because the failure otherwise arrives after the expensive part.
#
# Which transport is in play is decided by the case: the 1.x cases reach a LiveKit worker, the
# 2.x cases a hosted Vapi assistant. Asking for the other one's credentials is how a working
# setup gets reported as broken.
REQUIRED_VAPI = ("VAPI_API_KEY", "VAPI_ASSISTANT_ID")
REQUIRED_LIVEKIT = ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_TARGET_AGENT_NAME")


def _livekit_case() -> bool:
    """Whether the case being run reaches a LiveKit worker rather than a hosted assistant."""
    return os.environ.get("HARNESS_VOICE_CASE", CASE).strip().startswith("1.")


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def missing_prerequisites() -> list[str]:
    """What would stop a live call, in the words of what to do about it."""
    problems: list[str] = []
    livekit = _livekit_case()
    absent = [
        name
        for name in (REQUIRED_LIVEKIT if livekit else REQUIRED_VAPI)
        if not os.environ.get(name)
    ]
    if absent:
        problems.append(
            f"{', '.join(absent)} not set, so there is no way to reach the agent. Load the env "
            "file first:\n    set -a; . ./.env.acceptance; set +a"
        )
    # A LiveKit worker we run ourselves calls the world directly on the network we share with it,
    # so there is nothing to expose. Only a hosted assistant has to reach in from outside.
    exposed = os.environ.get("HARNESS_WEBHOOK_URL") or shutil.which("cloudflared")
    if not livekit and not exposed:
        problems.append(
            "no way to expose the webhook publicly. A hosted agent cannot reach loopback, so "
            "either install cloudflared (brew install cloudflared) or set HARNESS_WEBHOOK_URL "
            "to a tunnel that is already running."
        )
    return problems


def save_results(results: list[dict[str, Any]], destination: Path) -> Path:
    """Keep every run, so a suite can be read after the fact rather than scrolled back to."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / RESULTS
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_results(destination: Path) -> list[dict[str, Any]]:
    path = Path(destination) / RESULTS
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, list) else []
    except json.JSONDecodeError:
        return []


def as_record(run: LiveRun) -> dict[str, Any]:
    return {
        "scenario": run.scenario,
        "passed": bool(run.settled) and run.met == len(run.settled) and not run.problems,
        "met": run.met,
        "of": len(run.settled),
        "settled": [
            {"name": one.name, "held": one.held, "said": one.said, "broken": one.broken}
            for one in run.settled
        ],
        "judged": list(run.judged),
        "calls": list(run.calls),
        "problems": list(run.problems),
    }


def transcript_since(started: float) -> str:
    """What was said on the call that just happened, from the voice runner's own report.

    The voice case owns the call and writes its report where it always has; reaching into that
    report is how the transcript gets onto the run record without the harness re-implementing
    any of the call. Only a report written after this run started counts — the newest file on
    disk is otherwise last week's call wearing today's verdict.
    """
    root = ARTIFACTS_ROOT / "simulation-acceptance"
    if not root.exists():
        return ""
    newest: tuple[float, Path] | None = None
    for report in root.glob("run_*/*/report.json"):
        written = report.stat().st_mtime
        if written >= started and (newest is None or written > newest[0]):
            newest = (written, report)
    if newest is None:
        return ""
    try:
        loaded = json.loads(newest[1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    for result in loaded.get("results") or []:
        spoken = result.get("transcript")
        if isinstance(spoken, str) and spoken.strip():
            return spoken
    return ""


def report(run: LiveRun) -> str:
    """One run, as something worth reading rather than a score."""
    lines = [run.line()]
    lines += [one.line() for one in run.settled]
    lines += [f"  [?] {name} — judged, not settled by code" for name in run.judged]
    if run.problems:
        lines += [f"  !!  {problem}" for problem in run.problems]
    lines.append("")
    lines.append("what the agent actually did:")
    lines += [f"  {call}" for call in run.calls or ["(no tool calls reached the world)"]]
    return "\n".join(lines)


def run_tools(
    world_root: Path,
    destination: Path,
    *,
    contract: Any = None,
    case: str = "",
) -> Any:
    """A server for running one agent's scenarios against the real thing.

    How a scenario runs is decided by what the agent is, not by this stage. A hosted voice agent
    gets the live path — its own tools repointed at the world over a webhook, the call placed
    through ALK. Anything else runs here: the agent stood up from its contract, conversing over
    the same world, graded by the same checks. The scenarios, the world and the grading are
    identical either way; only the transport changes.
    """
    written = load_scenarios(destination)
    catalogue = load_catalogue(destination)
    results = load_results(destination)
    voice_case = case or os.environ.get("HARNESS_VOICE_CASE", "2.1.2")
    live = bool(contract is not None and getattr(contract, "modality", "") == "voice")

    @tool(
        "list_scenarios",
        "The scenarios that can be run, what each one tests, and which of its sub-goals are "
        "settled by code rather than left to a judge.",
        schema({}, []),
    )
    async def list_scenarios(_args: dict[str, Any]) -> dict[str, Any]:
        if not written:
            return _err("no scenarios have been written for this agent yet")
        lines: list[str] = []
        for one in written:
            settled = [
                name
                for name in one.sub_goals
                if (found := catalogue.named(name)) and found.deterministic()
            ]
            judged = [name for name in one.sub_goals if name not in settled]
            ran = next((r for r in results if r["scenario"] == one.name), None)
            mark = "" if ran is None else ("  [last run: PASS]" if ran.get("passed") else "  [last run: FAIL]")
            lines.append(
                f"{one.name}{mark}\n  tests: {one.tests or one.use_case or '—'}\n"
                f"  settled by code: {', '.join(settled) or 'none'}\n"
                f"  judged: {', '.join(judged) or 'none'}"
            )
        return _ok("\n".join(lines))

    @tool(
        "preflight",
        "Check everything a run needs before spending one. For a hosted voice agent that is the "
        "assistant's credentials and a way to expose the webhook publicly; for anything else "
        "the run happens here and needs nothing external. Run this before the first run.",
        schema({}, []),
    )
    async def preflight(_args: dict[str, Any]) -> dict[str, Any]:
        if not live:
            return _ok(
                "Ready. This agent runs here, against the world, from its contract — nothing "
                f"external is needed. {len(written)} scenarios are available."
            )
        problems = missing_prerequisites()
        if problems:
            return _err("Not ready:\n  - " + "\n  - ".join(problems))
        return _ok(
            "Ready. Credentials are set and the webhook can be exposed. "
            f"{len(written)} scenarios are available."
        )

    async def _run_here(scenario: Any) -> dict[str, Any]:
        """The scenario against the agent stood up from its contract, over the same world."""
        from . import run_suite

        if contract is None:
            return _err("no contract is loaded, so there is no agent to stand up")
        graded = await run_suite([scenario], contract, world_root, out=destination)
        results[:] = load_results(destination)
        result = graded[0]
        lines = [result.line()] + [check.line() for check in result.checkpoints]
        if result.transcript:
            lines += ["", "the conversation:", result.transcript]
        answer = "\n".join(lines)
        return _ok(answer) if result.passed else _err(answer)

    @tool(
        "run_simulation",
        "Run the whole suite. One call: every scenario, each in its own copy of the world, "
        "graded, and written out as one run you can come back to.\n\n"
        "This is how a suite is run. Running scenarios one at a time is for looking into a "
        "single failure afterwards, not for getting results.\n\n"
        "`concurrency` is how many run at once. Leave it at 1 for a spoken agent, where every "
        "scenario is a real call. It takes minutes and blocks until the whole suite is done.",
        schema({"concurrency": int, "model": str}, []),
    )
    async def run_simulation(args: dict[str, Any]) -> dict[str, Any]:
        from .simulation import simulate

        if contract is None:
            return _err("no contract is loaded, so there is no agent to run against")
        if not written:
            return _err("there are no scenarios to run")
        # Kept as they finish, because reporting needs the graded results themselves and the
        # summary carries only their rendering.
        produced: list[Any] = []
        summary = await simulate(
            list(written),
            contract,
            world_root,
            destination=destination,
            model=str(args.get("model") or "") or None,
            concurrency=max(1, int(args.get("concurrency") or 1)),
            on_case_done=produced.append,
        )
        results[:] = load_results(destination)
        lines = [
            f"{summary['run_id']}: {summary['passed']}/{summary['scenarios']} passed "
            f"in {summary['seconds']}s, ${summary['spent_usd']}",
            "",
        ]
        for one in summary["results"]:
            mark = "PASS" if one["passed"] else "FAIL"
            note = f"  {one['problems'][0]}" if one["problems"] else ""
            audio = "  [recording]" if one["recording"] else ""
            lines.append(
                f"  {mark}  {one['scenario']}  {one['met']}/{one['of']}{audio}{note}"
            )
        # Reported here too, not only from the run button: a run that reaches the platform only
        # when it was started one particular way leaves the page an unreliable record of what
        # has been run.
        _, said = platform.deliver(
            produced, list(written), destination, modality=contract.modality or "text"
        )
        lines += ["", *said]
        lines += [
            "",
            "read_run gives any one of these in full: the conversation, every tool call with "
            "its arguments, and what each check decided.",
        ]
        return _ok("\n".join(lines))

    @tool(
        "read_run",
        "One run in full, or the list of runs when no id is given. A run holds every scenario's "
        "conversation, every tool call with its arguments and result, and what each check "
        "decided — which is what a failure is diagnosed from.",
        schema({"run_id": str, "scenario": str}, []),
    )
    async def read_run(args: dict[str, Any]) -> dict[str, Any]:
        from .simulation import every_run, read_run as load_run

        run_id = str(args.get("run_id") or "")
        if not run_id:
            runs = every_run(destination)
            if not runs:
                return _ok("No runs yet. run_simulation makes one.")
            return _ok(
                "\n".join(
                    f"  {one['run_id']}  {one.get('passed', 0)}/{one.get('scenarios', 0)} "
                    f"passed  {one.get('seconds', 0)}s"
                    for one in runs
                )
            )
        try:
            whole = load_run(destination, run_id)
        except FileNotFoundError as missing:
            return _err(str(missing))
        wanted = str(args.get("scenario") or "")
        cases = [
            one
            for one in whole.get("scenarios", [])
            if not wanted or one.get("scenario") == wanted
        ]
        if not cases:
            return _err(f"{run_id} has no scenario called {wanted!r}")
        return _ok(json.dumps(cases if wanted else whole, indent=2, default=str)[:6000])

    @tool(
        "run_scenario",
        "Run one scenario against the agent and grade it.\n\n"
        "The world is restored and the scenario's setup applied first. A hosted voice agent is "
        "reached live — its OWN tools are pointed at the world over a webhook and the call is "
        "placed; any other agent is stood up here from its contract and conversed with. Either "
        "way the sub-goals' checks run against what the world holds afterwards plus the calls "
        "that were made.\n\n"
        "It can take minutes and blocks until the run is over. Run one at a time and read what "
        "comes back before running the next.",
        # Both spellings accepted: every model that has driven this stage has guessed
        # `scenario` at least once, and a retry on an argument name is a wasted turn.
        schema({"name": str, "scenario": str}, []),
    )
    async def run_scenario(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or args.get("scenario") or "")
        scenario = next((one for one in written if one.name == name), None)
        if scenario is None:
            return _err(
                f"no scenario called {name!r}. There is: "
                + ", ".join(one.name for one in written)
            )
        if not live:
            return await _run_here(scenario)
        problems = missing_prerequisites()
        if problems:
            return _err(
                "Cannot place a call:\n  - "
                + "\n  - ".join(problems)
                + "\nThis is the environment this harness is running in, not something to fix "
                "in the scenario."
            )

        def placed() -> tuple[LiveRun, str, list[str], str]:
            """The whole call, off the event loop.

            Wiring reads a subprocess's stdout and placing the call blocks for minutes; run
            inline they freeze whatever loop is hosting this tool, which for the web UI means
            the stream, the status endpoint and the stop button all die for the duration.
            """
            world, instruction, webhook, tunnel, url, moved = wire(scenario, world_root)
            started = time.time()
            try:
                # The caller's instruction reaches the voice case through the environment, so
                # how a simulated caller behaves is not decided in two places.
                os.environ["HARNESS_INSTRUCTION"] = instruction
                os.environ["HARNESS_SCENARIO"] = scenario.name
                os.environ["HARNESS_OUTCOME"] = scenario.tests
                os.environ["HARNESS_PERSONA"] = json.dumps(
                    scenario.persona.model_dump(exclude_none=True)
                    if scenario.persona is not None
                    else {"name": "customer"}
                )
                code = place_the_call(voice_case)
                run = grade(scenario, world, world_root)
                if code != 0 and not run.calls:
                    run.problems.append(
                        f"the voice runner exited {code} and no tool call reached the world, "
                        "so this says nothing about the agent"
                    )
            finally:
                webhook.stop()
                if tunnel is not None:
                    tunnel.terminate()
                world.close()
            return run, url, moved, transcript_since(started)

        run, url, moved, spoken = await asyncio.to_thread(placed)

        record = as_record(run)
        record["instruction"] = scenario.instruction
        record["transcript"] = spoken
        # Re-read before writing: the local suite writes the same file, and a list loaded when
        # this stage opened would silently roll back anything recorded since.
        results[:] = [
            r for r in load_results(destination) if r.get("scenario") != scenario.name
        ]
        results.append(record)
        save_results(results, destination)
        answer = f"webhook: {url}/tool\nrepointed: {', '.join(moved)}\n\n{report(run)}"
        return _ok(answer) if not run.problems else _err(answer)

    @tool(
        "read_results",
        "What every scenario did the last time it was run, without running anything.",
        schema({}, []),
    )
    async def read_results(_args: dict[str, Any]) -> dict[str, Any]:
        if not results:
            return _ok("nothing has been run yet")
        lines = []
        for record in results:
            mark = "PASS" if record.get("passed") else "FAIL"
            # Two record shapes share this file: live runs carry settled/judged, local runs
            # carry checkpoints. Both say what failed, and both deserve to be read.
            failed = [
                f"{one.get('name')}: {one.get('said') or one.get('detail') or ''}"
                for one in (record.get("settled") or record.get("checkpoints") or [])
                if not (one.get("held") if "held" in one else one.get("passed"))
            ]
            met = record.get("met", record.get("checkpoints_met", "?"))
            of = record.get("of")
            scored = f"{met}/{of}" if of is not None else str(met)
            lines.append(
                f"{mark}  {record.get('scenario')}  {scored}"
                + ("\n  - " + "\n  - ".join(failed) if failed else "")
            )
        passed = sum(1 for record in results if record.get("passed"))
        return _ok("\n".join(lines) + f"\n\n{passed} of {len(results)} passed")

    server = create_sdk_mcp_server(
        name=RUN_SERVER,
        version="0.1.0",
        tools=[
            list_scenarios,
            preflight,
            run_simulation,
            read_run,
            run_scenario,
            read_results,
        ],
    )
    return server


TOOL_NAMES = (
    "list_scenarios",
    "preflight",
    "run_simulation",
    "read_run",
    "run_scenario",
    "read_results",
)


# Which of the several recordings a call leaves behind is the one worth keeping. Both sides on
# one track, because the question asked of a spoken run is nearly always about the interaction:
# whether the agent talked over the caller, how long it left them waiting, what it heard.
PREFERRED = ("_stereo.wav", "stereo.wav", "combined.wav")


def recording_since(started: float, into: Path) -> str:
    """Copy the audio from the call that just happened into this run's folder.

    ALK records already and writes several tracks under its own artifacts directory. Rather than
    tell it where to put them — which it takes from its manifest, not from the environment — the
    files it wrote are found the same way the transcript is, by being newer than the moment this
    run began, and the one worth keeping is copied in beside the result.
    """
    root = ARTIFACTS_ROOT / "simulation-acceptance"
    if not root.exists():
        return ""
    fresh = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in (".wav", ".mp3", ".ogg")
        and path.stat().st_mtime >= started
    ]
    if not fresh:
        return ""
    chosen = next(
        (one for mark in PREFERRED for one in fresh if one.name.endswith(mark)),
        max(fresh, key=lambda one: one.stat().st_size),
    )
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    landed = into / f"recording{chosen.suffix}"
    shutil.copyfile(chosen, landed)
    return str(landed)
from ..config import ARTIFACTS_ROOT
