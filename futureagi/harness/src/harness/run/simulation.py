"""The one thing the harness hands a suite to.

The harness does not run scenarios. It builds a world, writes scenarios against it, and calls
`simulate` once. Everything after that belongs to ALK: how many run at a time, whether the person
is typed to or phoned, where the audio goes, what a report looks like.

That split matters more than it looks. While the harness ran scenarios itself, one at a time,
through its own conversation loop, a suite was only as good as the harness's patience: a run took
as many turns of the chat as it had scenarios, and the simulator driving it was not the one the
product ships. Handing over means the suite runs the same way whether a person triggered it from
the UI, a script did, or nobody did.

Chat and voice are one path here, and they differ in exactly one respect the harness never sees:
a chat agent runs in this process and reaches the world as an object, while a hosted voice agent
runs in somebody else's cloud and reaches the same world over HTTP. Same world, same setup, same
checks, same report. Only the wire differs, and the spec's ``world_kind`` decides it.

A run is a folder. One simulation over a suite is one run, kept whole, so a session accumulates
runs that can be compared rather than one result file that the next run overwrites.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..contract import AgentContract
from ..scenario import Scenario
from .grade import Result

RUNS = "runs"
RUN = "run.json"
RESULT = "result.json"
TRANSCRIPT = "transcript.txt"
CALLS = "calls.json"
logger = logging.getLogger(__name__)

# How many scenarios run at once by default. One, because the shipped default should be the one
# that cannot surprise anybody: a voice suite places real calls that cost real money, and fanning
# out to twenty is a bad thing to learn from a bill.
CONCURRENCY = 1

# What ALK calls the world and the person, per modality. Both are registry names it validates
# against the plugin's own manifest, so a typo is an error here rather than a confusing run.
WORLDS = {"text": ("chat", "chat"), "voice": ("voice", "voice")}
SIMULATORS = {"text": "synthetic_user", "voice": "livekit_simulator"}


def spoken_to(contract: AgentContract) -> bool:
    """Whether this agent is spoken to rather than typed to."""
    return (contract.modality or "text").strip().lower() == "voice"


def new_run_id() -> str:
    return datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S")


def run_root(destination: Path, run_id: str) -> Path:
    return Path(destination) / RUNS / run_id


def every_run(destination: Path) -> list[dict[str, Any]]:
    """Every run in this session, newest first, finished or not.

    A run that is still going is reported too, from the results already written. `run.json` is
    written once, at the end, so requiring it meant an hour-long suite showed nothing at all
    while its results sat on disk: the scenario that finished forty minutes ago was as invisible
    as the one that had not started. `finished` says which kind each is.
    """
    root = Path(destination) / RUNS
    if not root.exists():
        return []
    found: list[dict[str, Any]] = []
    for folder in sorted(root.iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        kept = folder / RUN
        if kept.exists():
            try:
                summary = json.loads(kept.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - one unreadable run never hides the rest
                continue
            summary["finished"] = True
            found.append(summary)
            continue
        done = _cases_so_far(folder)
        if done:
            found.append(
                {
                    "run_id": folder.name,
                    "finished": False,
                    "scenarios": len(done),
                    "passed": sum(1 for one in done if one.get("passed")),
                    "seconds": round(sum(one.get("seconds") or 0 for one in done), 1),
                    "results": done,
                }
            )
    return found


def _cases_so_far(folder: Path) -> list[dict[str, Any]]:
    """The scenarios of an unfinished run that have already been written."""
    done: list[dict[str, Any]] = []
    for case in sorted(folder.iterdir()):
        kept = case / RESULT
        if not case.is_dir() or not kept.exists():
            continue
        try:
            one = json.loads(kept.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a result being written this instant is not an error
            continue
        done.append(
            {
                "scenario": one.get("scenario", case.name),
                "passed": bool(one.get("passed")),
                "met": one.get("met"),
                "of": len(one.get("checkpoints") or []),
                "seconds": one.get("seconds"),
                "recording": one.get("recording", ""),
                "problems": one.get("problems") or [],
            }
        )
    return done


def read_run(destination: Path, run_id: str) -> dict[str, Any]:
    """One run in full: its summary, and every scenario's result, transcript and calls.

    Read from the folder rather than held in memory, so the harness can be asked about a run
    that happened before it was started, and about any single call inside one.
    """
    root = run_root(destination, run_id)
    kept = root / RUN
    if not root.exists():
        raise FileNotFoundError(f"no run {run_id} in {destination}")
    # A run still going has no summary yet, but the scenarios it has finished are readable and
    # worth reading. Only a folder that is not there at all is an error.
    summary = (
        json.loads(kept.read_text(encoding="utf-8"))
        if kept.exists()
        else {"run_id": run_id, "finished": False, "passed": 0}
    )
    summary.setdefault("finished", kept.exists())
    scenarios: list[dict[str, Any]] = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or not (folder / RESULT).exists():
            continue
        one = json.loads((folder / RESULT).read_text(encoding="utf-8"))
        one["transcript"] = _text(folder / TRANSCRIPT)
        one["calls_detail"] = _json(folder / CALLS)
        scenarios.append(one)
    summary["scenarios"] = scenarios
    return summary


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _json(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


async def simulate(
    scenarios: list[Scenario],
    contract: AgentContract,
    world_root: Path,
    *,
    destination: Path | None = None,
    model: str | None = None,
    concurrency: int = CONCURRENCY,
    run_id: str = "",
    on_case_start: Callable[[Scenario], Any] | None = None,
    on_case_done: Callable[[Result], Any] | None = None,
    on_exchange: Callable[[str, dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Run a whole suite through ALK and write it out as one run.

    Returns the run's summary. Results are in the order they were asked for, not the order they
    finished, so a report reads the same however it was scheduled.
    """
    from .models import for_roles

    destination = Path(destination or world_root)
    run_id = run_id or new_run_id()
    root = run_root(destination, run_id)
    root.mkdir(parents=True, exist_ok=True)
    roles = for_roles(model)

    started = time.time()
    room = asyncio.Semaphore(max(1, concurrency))
    ordered: list[Result | None] = [None] * len(scenarios)

    async def one(index: int, scenario: Scenario) -> None:
        async with room:
            if on_case_start:
                notified = on_case_start(scenario)
                if inspect.isawaitable(notified):
                    await notified
            began = time.time()
            folder = root / scenario.name
            folder.mkdir(parents=True, exist_ok=True)
            try:
                result = await _run_one(
                    scenario, contract, world_root, folder, roles=roles,
                    on_exchange=(
                        (lambda turn: on_exchange(scenario.name, turn))
                        if on_exchange else None
                    ),
                )
            except Exception as failed:  # noqa: BLE001 - one bad scenario never stops the suite
                result = Result(
                    scenario=scenario.name,
                    problems=[f"{type(failed).__name__}: {failed}"],
                )
            result.seconds = round(time.time() - began, 1)
            _write_case(folder, result)
            ordered[index] = result
            if on_case_done:
                notified = on_case_done(result)
                if inspect.isawaitable(notified):
                    await notified

    await asyncio.gather(
        *(one(index, scenario) for index, scenario in enumerate(scenarios))
    )
    results = [one for one in ordered if one is not None]

    summary = {
        "run_id": run_id,
        "agent": contract.agent,
        "modality": contract.modality or "text",
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
        "seconds": round(time.time() - started, 1),
        "concurrency": concurrency,
        "models": roles,
        "scenarios": len(results),
        "passed": sum(1 for one in results if one.passed),
        "spent_usd": round(sum(one.spent_usd for one in results), 4),
        # Averaged across the scenarios that reported them, so a suite has one line per metric
        # rather than a number nobody compares. Only over the runs that actually measured it:
        # averaging a missing metric as zero would make a suite look worse the more of it failed
        # to run, which is the opposite of informative.
        "metrics": _averaged([one.measured for one in results]),
        "results": [
            {
                "scenario": one.scenario,
                "passed": one.passed,
                "met": one.met,
                "of": len(one.checkpoints),
                "seconds": one.seconds,
                "recording": one.recording,
                "problems": one.problems,
            }
            for one in results
        ],
    }
    (root / RUN).write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    return summary


def _write_case(folder: Path, result: Result) -> None:
    """One scenario's result, transcript and calls, each in the form it is read in.

    The transcript is written as text because it is read by people, and the calls as JSON
    because they are read by the UI and by the harness looking into a single call.
    """
    body = asdict(result)
    body["passed"] = result.passed
    body["met"] = result.met
    detail = body.pop("calls_detail", None) or []
    (folder / RESULT).write_text(
        json.dumps(body, indent=2, default=str), encoding="utf-8"
    )
    (folder / TRANSCRIPT).write_text(result.transcript or "", encoding="utf-8")
    (folder / CALLS).write_text(
        json.dumps(detail, indent=2, default=str), encoding="utf-8"
    )


async def _run_one(
    scenario: Scenario,
    contract: AgentContract,
    world_root: Path,
    folder: Path,
    *,
    roles: dict[str, str],
    on_exchange: Callable[[dict[str, Any]], Any] | None = None,
) -> Result:
    """One scenario, in its own world, through ALK's runner.

    The world is prepared here and handed in, rather than named in the spec, because isolation
    is ours to guarantee: every scenario starts from the same frozen base with only its own
    setup applied, and a world shared between cases would let the first one decide what the
    second is graded against.
    """

    from ..folder import apply_setup, check_ready
    from ..world.snapshot import restore

    spoken = spoken_to(contract)
    kind = "voice" if spoken else "text"
    adapter, world_kind = WORLDS[kind]

    world = restore(world_root)
    try:
        world.reset()
        applied = apply_setup(scenario, world)
        if not applied.ok:
            raise RuntimeError(f"the scenario's setup did not run: {applied.said}")
        ready = check_ready(scenario, world)
        if not ready.ok:
            raise RuntimeError(
                f"the world is not ready for this scenario: {ready.said}. Running it would "
                "test us rather than the agent."
            )
        # The setup's own calls are not the agent's.
        world.calls = []

        if not spoken:
            # Typed, and driven by a model rather than by ALK's chat simulator.
            #
            # That simulator is deterministic on purpose: an untyped persona gets three fixed
            # lines ("Can you give me the exact next step…"), and a typed one renders utterances
            # from a compiled behaviour policy. Reproducible, and not a simulation of a person.
            # A suite whose user says the same three things to every agent tests one path and
            # calls it coverage.
            #
            # So the conversation is driven here, by a model reading the simulator prompt the
            # build stage wrote for this agent. Everything around it is unchanged: same world,
            # same setup, same checks, same run folder.
            return await _typed_to(scenario, contract, world, world_root, folder, roles=roles)

        # Spoken. The agent is not here: it runs in Vapi, with its own prompt, its own model
        # and its own voice, and the only thing that changes is where its tools are answered.
        # ALK places the call and drives a simulated caller that is a real model over STT and
        # TTS, so this half was never deterministic.
        return await _spoken_to(
            scenario, contract, world, world_root, folder, roles=roles,
            on_exchange=on_exchange,
        )
    finally:
        try:
            world.close()
        except Exception:  # cleanup must never replace a completed scenario result
            logger.exception("world cleanup failed after scenario %s", scenario.name)


def _found_audio(directory: Path) -> Path | None:
    """The recording a run left behind, if it left one.

    Asked of the directory rather than taken on trust from whatever placed the call: a runner
    that exits badly still returns a path, and a path is not a file.
    """
    if not directory.exists():
        return None
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in (".wav", ".mp3", ".ogg", ".m4a"):
            return path
    return None


async def _typed_to(
    scenario: Scenario,
    contract: AgentContract,
    world: Any,
    world_root: Path,
    folder: Path,
    *,
    roles: dict[str, str],
) -> Result:
    """A typed conversation, with a model on both sides.

    The same grading as every other run: the world it is handed is already set up, and what it
    leaves behind is what the checks read.
    """
    from ..catalogue import load_catalogue
    from . import converse
    from .grade import checkpoints, grade_sub_goals, judge, judge_suite_evals
    from .targets import resolve

    agent = resolve("local")(contract, world, model=roles["agent"])
    transcript = await converse(
        agent, scenario, contract, world_root=world_root, model=roles["user"]
    )
    catalogue = load_catalogue(world_root)
    settled = grade_sub_goals(world, scenario, catalogue, transcript.calls)
    ending = ", ".join(
        f"{name}: {len(rows)} rows" for name, rows in sorted(world.observe().state.items())
    )
    judgements, judged_cost = await judge(
        scenario, transcript, contract, catalogue, model=roles["judge"], ending=ending
    )
    judgements += judge_suite_evals(
        catalogue.suite_evals, scenario, transcript, contract, ending=ending
    )
    result = Result(
        scenario=scenario.name,
        tests=scenario.tests,
        state_failures=[f"{one.name}: {one.said}" for one in settled if not one.held],
        conduct=judgements,
        checkpoints=checkpoints(settled, judgements),
        crashes=[f"{call.name}: {call.error}" for call in transcript.crashed()],
        ended=transcript.ended,
        turns=len(transcript.exchanges),
        calls=len(transcript.calls),
        spent_usd=transcript.spent_usd + judged_cost,
        transcript=transcript.spoken(),
        exchanges=[{"speaker": turn.speaker, "text": turn.text} for turn in transcript.exchanges],
        actions=transcript.actions(),
    )
    result.calls_detail = _calls_of(transcript.calls)
    return result


def _calls_of(calls: Any) -> list[dict[str, Any]]:
    """Every call in full, for the timeline and for anyone asking what one call did."""
    return [
        {
            "name": call.name,
            "arguments": call.arguments,
            "result": str(call.result)[:2000],
            "ok": call.ok,
            "refused": call.refused,
            "error": call.error,
            "at": getattr(call, "at", 0.0),
        }
        for call in calls
    ]


async def _spoken_to(
    scenario: Scenario,
    contract: AgentContract,
    world: Any,
    world_root: Path,
    folder: Path,
    *,
    roles: dict[str, str],
    on_exchange: Callable[[dict[str, Any]], Any] | None = None,
) -> Result:
    """A real call, with the agent's own tools answered by this world.

    The agent under test is not reconstructed here and is not running in this process. It is the
    hosted assistant, with its own prompt, model and voice; the only thing that changes for the
    duration is where its tool calls are sent. That makes this the more faithful of the two
    paths, and the reason a spoken suite is worth more than a typed one.

    The call itself belongs to ALK, which drives a simulated caller through speech: a real model
    behind STT and TTS, not a script.
    """
    import os
    import time

    from ..catalogue import load_catalogue
    from .call import place_the_call
    from .conversation import Exchange, Transcript
    from .evidence import measured, newest_report, spoken_times, tracks_in
    from .grade import checkpoints, grade_sub_goals, judge, judge_suite_evals
    from .live import wire
    from .tools import missing_prerequisites

    stopping = missing_prerequisites()
    if stopping:
        raise RuntimeError("cannot place a call:\n  - " + "\n  - ".join(stopping))

    loop = asyncio.get_running_loop()

    def live_exchange(turn: dict[str, Any]) -> None:
        if on_exchange:
            loop.call_soon_threadsafe(on_exchange, turn)

    def placed() -> tuple[Any, str, str]:
        """Everything about the call, off the event loop.

        Wiring reads a subprocess's stdout and the call itself blocks for minutes. Run inline
        they freeze whatever loop is hosting this, which for the UI means the stream, the status
        endpoint and the stop button all stop with it.
        """
        _world, instruction, webhook, tunnel, _url, _moved = wire(
            scenario, world_root, world=world
        )
        started = time.time()
        try:
            os.environ["HARNESS_INSTRUCTION"] = instruction
            os.environ["HARNESS_SCENARIO"] = scenario.name
            os.environ["HARNESS_OUTCOME"] = scenario.tests
            os.environ["HARNESS_PERSONA"] = json.dumps(
                scenario.persona.model_dump(exclude_none=True)
                if scenario.persona is not None
                else {"name": "customer"}
            )
            os.environ["HARNESS_SCRIPTED_CALLER"] = json.dumps(
                scenario.persona.scripted_caller
                if scenario.persona is not None
                and scenario.persona.scripted_caller is not None
                else {}
            )
            code = place_the_call(
                os.environ.get("HARNESS_VOICE_CASE", "2.1.2"),
                on_exchange=live_exchange if on_exchange else None,
            )
        finally:
            try:
                webhook.stop()
            except Exception:
                logger.exception("webhook cleanup failed after scenario %s", scenario.name)
            if tunnel is not None:
                try:
                    tunnel.terminate()
                except Exception:
                    logger.exception("tunnel cleanup failed after scenario %s", scenario.name)
        # Everything the runner recorded about this call, read from the report it wrote.
        return code, newest_report(started)

    code, case = await asyncio.to_thread(placed)
    spoken = str(case.get("transcript") or "")
    # Every track that exists, copied in beside the result so a run is self-contained and the
    # page can fall back when the preferred one is missing.
    kept = _keep_tracks(tracks_in(case), folder)

    catalogue = load_catalogue(world_root)
    settled = grade_sub_goals(world, scenario, catalogue, world.calls)
    # Judged the same way a typed run is. Without this a spoken scenario reports "1/2" when what
    # happened is that one check passed and the other was never asked, which reads as the agent
    # half-failing rather than as the suite not having looked.
    spoken_transcript = Transcript(
        exchanges=[
            Exchange("agent" if line.lower().startswith("assistant") else "customer", line)
            for line in spoken.splitlines()
            if line.strip()
        ],
        calls=list(world.calls),
        ended="finished",
    )
    judgements, judged_cost = await judge(
        scenario,
        spoken_transcript,
        contract,
        catalogue,
        model=roles["judge"],
        ending=", ".join(
            f"{name}: {len(rows)} rows"
            for name, rows in sorted(world.observe().state.items())
        ),
    )
    judgements += judge_suite_evals(
        catalogue.suite_evals,
        scenario,
        spoken_transcript,
        contract,
        ending=", ".join(
            f"{name}: {len(rows)} rows"
            for name, rows in sorted(world.observe().state.items())
        ),
    )
    result = Result(
        scenario=scenario.name,
        tests=scenario.tests,
        state_failures=[f"{one.name}: {one.said}" for one in settled if not one.held],
        conduct=judgements,
        checkpoints=checkpoints(settled, judgements),
        spent_usd=judged_cost,
        ended="finished",
        turns=len([line for line in spoken.splitlines() if line.strip()]),
        calls=len(world.calls),
        transcript=spoken,
        exchanges=_timed_exchanges(spoken_transcript.exchanges, spoken_times(case)),
        recording=(kept[0]["path"] if kept else ""),
    )
    result.tracks = kept
    result.measured = measured(case)
    result.calls_detail = _calls_of(world.calls)
    if code != 0 and not world.calls:
        # A call that failed and never reached the world says nothing about the agent, and must
        # not be recorded as the agent failing.
        result.problems.append(
            f"the voice runner exited {code} and no tool call reached the world"
        )
    return result


def _timed_exchanges(exchanges: list[Any], times: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation with each turn's speech times attached, where they were measured.

    Paired by position, and only when the two agree on how many turns there were. They come
    from the same call but by different routes, so a mismatch means one of them dropped a turn
    -- and pairing them anyway would hang every turn's timing on the wrong words.
    """
    spoken = [{"speaker": turn.speaker, "text": turn.text} for turn in exchanges]
    if len(times) != len(spoken):
        return spoken
    for turn, when in zip(spoken, times, strict=True):
        if when.get("start_time_ms") is None:
            continue
        turn["start_time_ms"] = when["start_time_ms"]
        if when.get("end_time_ms") is not None:
            turn["end_time_ms"] = when["end_time_ms"]
    return spoken


def _keep_tracks(found: list[dict[str, str]], folder: Path) -> list[dict[str, str]]:
    """Copy each recording into this run's folder, keeping the order it was offered in.

    Copied rather than referenced, because the runner's own directory is transient and a run
    that cannot be listened to next week is a run that cannot be shown to anybody.
    """
    import shutil

    folder.mkdir(parents=True, exist_ok=True)
    kept: list[dict[str, str]] = []
    for track in found:
        source = Path(track["path"])
        if not source.exists():
            continue
        landed = folder / f"{track['label'].replace(':', '_')}{source.suffix}"
        try:
            shutil.copyfile(source, landed)
        except OSError:
            continue
        kept.append({"label": track["label"], "path": str(landed)})
    return kept


def _averaged(measured: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each metric's mean over the scenarios that reported it, carrying whether it applied.

    A metric that had nothing to measure scores 1.0, so averaging the lot produces a suite
    summary in which two thirds of the numbers are perfect and none of them mean anything. The
    applicability travels with the average instead of being flattened away, so a reader is never
    shown "browser action safety 1.00" for a suite of phone calls without also being told there
    were no browser actions.
    """
    gathered: dict[str, list[float]] = {}
    applies: dict[str, bool] = {}
    reasons: dict[str, str] = {}
    for one in measured:
        for metric in (one or {}).get("metrics") or []:
            name, value = metric.get("name"), metric.get("score")
            if not name or not isinstance(value, (int, float)):
                continue
            gathered.setdefault(name, []).append(float(value))
            # Applicable anywhere is applicable: one scenario exercising a capability is enough
            # to make the number worth reading across the suite.
            applies[name] = applies.get(name, False) or bool(metric.get("applicable", True))
            if metric.get("reason") and name not in reasons:
                reasons[name] = str(metric["reason"])
    return [
        {
            "name": name,
            "score": round(sum(values) / len(values), 4),
            "applicable": applies.get(name, True),
            "reason": reasons.get(name, ""),
            "cases": len(values),
        }
        for name, values in sorted(gathered.items())
        if values
    ]
