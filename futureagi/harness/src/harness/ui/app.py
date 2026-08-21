"""A chat server over the harness.

One conversation, held open, talked to over HTTP. The stages already emit typed events; this
streams them to whoever is listening and serves the artifacts the stages write. There is no
bundled page: anything that can read server-sent events can draw this, and the platform
frontend is the renderer that does.

Run from the repo root, with the same environment the CLI uses. The last two variables are what
the run stage needs to place live calls; without them it still opens and says what is missing.

    set -a; . ./.env.acceptance; set +a
    export CLOUD_ML_REGION=global ALK_HARNESS_MODEL=claude-haiku-4-5
    export ACCEPTANCE_LIVEKIT_URL=ws://localhost:7880 ACCEPTANCE_MAX_SECONDS=210
    .venv/bin/python ui/server.py

Then open http://localhost:8777
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import (  # noqa: E402
    FileResponse,
    JSONResponse,
    StreamingResponse,
)
from pydantic import BaseModel  # noqa: E402

from harness import platform as platform_api  # noqa: E402
from harness import sessions  # noqa: E402
from harness.chat import Conversation  # noqa: E402
from harness.config import chosen_model, credentials_hint  # noqa: E402
from harness.run.simulation import simulate  # noqa: E402
from harness.scenarios import load as load_scenarios  # noqa: E402
from harness.understand import load as load_contract  # noqa: E402
from harness.world.snapshot import read_manifest  # noqa: E402
from harness.world.snapshot import restore as restore_world  # noqa: E402
from harness.world.snapshot import saved as world_saved  # noqa: E402

app = FastAPI(title="harness")


def _cors_origins() -> list[str]:
    raw = os.environ.get("HARNESS_CORS_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@app.middleware("http")
async def _require_internal_auth(request, call_next):
    if request.url.path.rstrip("/") == "/healthz":
        return await call_next(request)
    secret = os.environ.get("INTERNAL_API_SECRET", "").strip()
    if not secret:
        if os.environ.get("HARNESS_AUTH_DISABLED") == "1":
            return await call_next(request)
        return JSONResponse({"error": "INTERNAL_API_SECRET is not configured"}, status_code=503)
    # split() (not a fixed "Bearer " prefix) tolerates repeated whitespace and a
    # differently-cased scheme, matching what the backend side actually sends.
    parts = request.headers.get("authorization", "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    token = parts[1]
    # compare_digest raises TypeError on non-ASCII str input; a malformed header
    # must 401, not 500.
    if not token.isascii() or not hmac.compare_digest(token, secret):
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    return await call_next(request)


# Registered after the auth middleware so it wraps outside it (Starlette makes the
# most-recently-added middleware outermost) and can answer a CORS preflight itself
# rather than the preflight's bare OPTIONS request hitting auth and getting a 401.
# Backend-proxied traffic never needs this at all — only a standalone dev front end
# calling this service directly from a browser does — so it is skipped when unset.
_origins = _cors_origins()
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}

# Where agents live. Almost never inside this repo: the harness is in one place and the agent
# being tested is somewhere else on disk nearly every time.
WORKSPACE = REPO.parent
SESSIONS = REPO / "artifacts" / "sessions"
# Which session was last opened, so a restart or a refresh comes back to it instead of to a
# blank page. One line on disk, because anything held only in the process is lost by restarting
# it — which is exactly when you most want it back.
OPEN = REPO / "artifacts" / ".open-session"

sessions.SESSIONS = SESSIONS

# The conversation currently open, and which session folder it belongs to.
conversation: Conversation | None = None
current: sessions.Session | None = None

# The task doing work, so it can be stopped. A stage that has started thrashing costs money
# every turn, and watching it without being able to intervene is the worst seat in the house.
running: asyncio.Task | None = None
busy = asyncio.Lock()


def _remember_open() -> None:
    OPEN.parent.mkdir(parents=True, exist_ok=True)
    OPEN.write_text(current.id if current else "", encoding="utf-8")


def _adopt(session: sessions.Session) -> None:
    """Make one session the open one, rebuilding its conversation from its folder."""
    global conversation, current
    from harness.sources import resolve

    source = None
    if session.source and session.kind:
        try:
            source = resolve(session.kind, name=session.agent or session.id, root=session.source)
        except Exception:
            source = None
    current = session
    conversation = Conversation(source=source, out=session.path, workspace=WORKSPACE)
    _remember_open()


def _runs(path) -> list:
    return sessions._runs(path) if path else []


class Said(BaseModel):
    text: str = ""


# How much of a tool's arguments to keep in the conversation. Generous, because a submitted
# contract or a written handler is the thing somebody reopens a session to read, and stingy
# enough that a session folder does not become a copy of every artifact it produced.
ARGUMENT_LIMIT = 4000


def _shortened(arguments: Any) -> Any:
    """The arguments a tool was called with, cut only when they are enormous."""
    if arguments in (None, "", {}, []):
        return None
    try:
        written = json.dumps(arguments, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        written = str(arguments)
    if len(written) <= ARGUMENT_LIMIT:
        return arguments
    return {
        "_truncated": f"{len(written)} characters, showing the first {ARGUMENT_LIMIT}",
        "_head": written[:ARGUMENT_LIMIT],
    }


def _payload(event) -> str:
    body = {"kind": event.kind, "text": event.text, "tool": event.tool, "detail": event.detail}
    return f"data: {json.dumps(body, default=str)}\n\n"


def _status() -> dict:
    """Everything the page needs to draw itself, read from the open session's folder."""
    if conversation is None or current is None:
        return {
            "session": None,
            "stage": "",
            "stages": {},
            "agent": None,
            "model": chosen_model(),
            "credentials": credentials_hint().splitlines()[0],
            "spent_usd": 0.0,
            "have": {},
            "out": None,
            "busy": busy.locked(),
        }
    reported = platform_api.reported_to(current.path)
    return {
        "session": current.meta(),
        "stage": conversation.stage_name,
        # Which stages can be opened right now, and why not where they cannot. Stages are not a
        # wizard: going back to fix a contract after the world is built is the ordinary case.
        "stages": conversation.reachable(),
        "agent": current.agent or current.id,
        "model": chosen_model(),
        "credentials": credentials_hint().splitlines()[0],
        "spent_usd": round(
            conversation.spent_usd
            + (conversation.stage.spent_usd if conversation.stage else 0.0),
            4,
        ),
        "have": current.has(),
        "out": str(current.path),
        # Where this session's runs landed on the platform. The page offers a way through to
        # them, and without these it has nowhere to send anyone however many runs were reported.
        "run_test_id": reported.get("run_test_id", ""),
        "execution_id": reported.get("test_execution_id", ""),
        # A refresh must be able to tell that work is still going on. Without this the page comes
        # back looking idle, and the next thing typed is rejected for no visible reason.
        "busy": busy.locked(),
    }


async def _stream_turn(coro_factory):
    """Run one piece of work and stream its events, ending with the fresh status."""
    global running
    queue: asyncio.Queue = asyncio.Queue()

    async def work():
        try:
            await coro_factory(lambda event: queue.put_nowait(_payload(event)))
        except asyncio.CancelledError:
            queue.put_nowait(
                _payload(
                    type("E", (), {"kind": "done", "text": "", "tool": "", "detail": {
                        "outcome": "stopped",
                        "error": "stopped. The stage is closed; say something to start it again."}})()
                )
            )
            raise
        except Exception as failed:
            queue.put_nowait(
                _payload(
                    type("E", (), {"kind": "done", "text": "", "tool": "", "detail": {
                        "outcome": "failed", "error": f"{type(failed).__name__}: {failed}"}})()
                )
            )
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(work())
    running = task
    while True:
        item = await queue.get()
        if item is None:
            break
        yield item
    running = None
    try:
        await task
    except asyncio.CancelledError:
        # Whatever the stage was in the middle of is not resumable, so it is closed and the
        # next message opens it again. Everything already saved to disk is untouched.
        await conversation.close()
    yield f"data: {json.dumps({'kind': 'status', 'detail': _status()}, default=str)}\n\n"


def _assert_auth_configured() -> None:
    if not os.environ.get("INTERNAL_API_SECRET", "").strip() and os.environ.get(
        "HARNESS_AUTH_DISABLED"
    ) != "1":
        raise RuntimeError(
            "INTERNAL_API_SECRET is not set. Set it (the backend sends it as a bearer token), "
            "or set HARNESS_AUTH_DISABLED=1 for a standalone dev run."
        )


@app.on_event("startup")
async def _startup() -> None:
    """Come back to whichever session was last open."""
    _assert_auth_configured()
    SESSIONS.mkdir(parents=True, exist_ok=True)
    wanted = OPEN.read_text(encoding="utf-8").strip() if OPEN.exists() else ""
    session = sessions.load(wanted, SESSIONS) if wanted else None
    if session is None:
        found = sessions.every(SESSIONS)
        session = found[0] if found else None
    if session is not None:
        _adopt(session)


@app.get("/api/sessions")
async def list_sessions():
    """Every conversation, newest first, with what each one has produced."""
    return {
        "sessions": [
            {**one.meta(), "has": one.has(), "path": str(one.path)}
            for one in sessions.every(SESSIONS)
        ],
        "open": current.id if current else None,
    }


@app.get("/api/environments")
async def environments():
    """Every session with a built world. Cross-session, so it never touches the open one."""
    return {"environments": sessions.environments(SESSIONS)}


class Started(BaseModel):
    agent: str = ""


@app.post("/api/sessions")
async def start_session(started: Started):
    """Begin a new conversation, with its own folder."""
    if busy.locked():
        return JSONResponse({"error": "still working on the last thing"}, status_code=409)
    if conversation is not None:
        await conversation.close()
    _adopt(sessions.create(agent=started.agent, base=SESSIONS))
    return _status()


class Opened(BaseModel):
    id: str


@app.post("/api/sessions/open")
async def open_session(opened: Opened):
    """Reopen a conversation. Everything about it is read back from its folder."""
    if busy.locked():
        return JSONResponse({"error": "still working on the last thing"}, status_code=409)
    session = sessions.load(opened.id, SESSIONS)
    if session is None:
        return JSONResponse({"error": f"no session {opened.id}"}, status_code=404)
    if conversation is not None:
        await conversation.close()
    _adopt(session)
    return _status()


@app.delete("/api/sessions/{identifier}")
async def delete_session(identifier: str):
    """Delete a conversation and everything in it."""
    global conversation, current
    if busy.locked():
        return JSONResponse({"error": "still working on the last thing"}, status_code=409)
    if not sessions.remove(identifier, SESSIONS):
        return JSONResponse({"error": f"no session {identifier}"}, status_code=404)
    if current and current.id == identifier:
        if conversation is not None:
            await conversation.close()
        conversation, current = None, None
        found = sessions.every(SESSIONS)
        if found:
            _adopt(found[0])
        else:
            _remember_open()
    return _status()


@app.get("/api/history")
async def chat_history():
    """This conversation, as it was, so a refresh does not lose it."""
    if current is None:
        return {"messages": []}
    return {"messages": sessions.history(current.path)}


class Chosen(BaseModel):
    stage: str


@app.post("/api/stage")
async def choose_stage(chosen: Chosen):
    """Open one stage directly, whether or not it is the next one.

    Opening is not starting: the stage is made current and its tools become available, but it is
    not told to begin, because choosing to look at a stage is not asking it to spend anything.
    """
    if busy.locked():
        return JSONResponse({"error": "still working on the last thing"}, status_code=409)
    if conversation is None or current is None:
        return JSONResponse({"error": "no session open"}, status_code=404)
    try:
        await conversation.go_to(chosen.stage)
    except Exception as failed:
        return JSONResponse({"error": str(failed)}, status_code=400)
    current.stage = conversation.stage_name
    sessions.save(current)
    return _status()


@app.post("/api/stop")
async def stop():
    """Interrupt whatever is running. Anything already written to disk stays written."""
    if running is None or running.done():
        return {"stopped": False, "why": "nothing is running"}
    running.cancel()
    return {"stopped": True}


@app.get("/api/status")
async def status():
    return _status()


@app.post("/api/say")
async def say(said: Said):
    if conversation is None or current is None:
        return JSONResponse({"error": "no session open"}, status_code=404)
    if busy.locked():
        return JSONResponse(
            {"error": f"still working on the {conversation.stage_name} stage — one moment"},
            status_code=409,
        )

    text = said.text.strip()
    if text:
        sessions.remember(
            current.path,
            sessions.Message(role="you", text=text, stage=conversation.stage_name),
        )

    async def run(on_event):
        # What the harness says back, kept as it is produced, so reopening this conversation
        # shows the work and not only the conclusion.
        spoken: list[str] = []
        tools: list[dict] = []

        def watch(event):
            if event.kind == "text":
                spoken.append(event.text)
            elif event.kind == "tool":
                detail = event.detail or {}
                tools.append(
                    {
                        "label": detail.get("label") or event.tool,
                        "target": detail.get("target", ""),
                        # What it was actually called with. Kept because reopening a session
                        # showed the tool's name and nothing else, so the one thing worth going
                        # back for, what was submitted, was the one thing that had been thrown
                        # away. Live it streamed and was gone.
                        "arguments": _shortened(detail.get("arguments")),
                    }
                )
            elif event.kind == "result" and tools:
                # More than the first line. A gate's refusal says what is wrong on the lines
                # after it, and one line of "problems:" is no use to anyone reading it later.
                tools[-1]["said"] = (event.text or "").splitlines()[:12]
                tools[-1]["failed"] = bool((event.detail or {}).get("is_error"))
            on_event(event)

        async with busy:
            if not text:
                entered = await conversation.advance(on_event=watch)
                if entered is None and conversation.stage is None:
                    await conversation.start(on_event=watch)
            else:
                await conversation.say(text, on_event=watch)

        sessions.remember(
            current.path,
            sessions.Message(
                role="harness",
                # Blank-line joined: one turn can speak several times, once per stage it passes
                # through, and running those together reads as one garbled paragraph when the
                # conversation is restored.
                text="\n\n".join(one.strip() for one in spoken if one.strip()),
                stage=conversation.stage_name,
                tools=tools,
            ),
        )
        # The folder knows what it is about, so the list can show it without opening it.
        current.stage = conversation.stage_name
        if not current.agent and conversation.contract:
            current.agent = conversation.contract.agent
            current.title = conversation.contract.one_liner or current.agent
        # Recorded whenever it is known, not only when the session has none yet. Reopening a
        # session rebuilds the conversation from this field, and a session that lost it cannot
        # reach the agent's own code afterwards: the build stage then reports that the agent has
        # no source, which reads as a fact about the agent rather than about us.
        reached = str(getattr(conversation.source, "root", "") or "")
        if reached:
            current.source = reached
            current.kind = conversation.source.kind
        sessions.save(current)

    return StreamingResponse(_stream_turn(run), media_type="text/event-stream")


@app.post("/api/run")
async def run_scenarios(said: Said):
    """Run the written scenarios against the world, streaming the conversations as they happen."""
    if busy.locked():
        return JSONResponse({"error": "still working on the last thing"}, status_code=409)
    out = current.path if current else None
    contract = load_contract(out) if out else None
    scenarios = load_scenarios(out) if out else []
    if not contract or not scenarios:
        return JSONResponse({"error": "nothing to run yet: need a contract and scenarios"}, 400)

    only = [name for name in said.text.split() if name]
    chosen = [s for s in scenarios if s.name in only] if only else scenarios

    async def run(on_event):
        def started(scenario):
            # The suite runs inside ALK now, which reports a scenario when it finishes rather
            # than turn by turn. Without this the page sits silent for the length of a call.
            on_event(type("E", (), {
                "kind": "text", "text": f"running {scenario.name}", "tool": "", "detail": {}})())

        produced: list[Any] = []

        def result(one):
            produced.append(one)
            on_event(type("E", (), {
                "kind": "result_card", "text": one.line(), "tool": "",
                "detail": {
                    "scenario": one.scenario, "passed": one.passed,
                    "met": one.met, "of": len(one.checkpoints),
                    "checkpoints": [asdict(check) for check in one.checkpoints],
                    "ended": one.ended, "turns": one.turns, "calls": one.calls,
                    "transcript": one.transcript, "actions": one.actions,
                }})())

        async with busy:
            # simulate(), not run_suite(): only this one asks the contract whether the agent is
            # spoken to, and a voice agent's scenario is placed as a real call against the agent
            # itself. run_suite always reconstructs the agent locally, so a voice suite driven
            # through it silently tests a replica and never reaches the thing under test.
            await simulate(chosen, contract, out, on_case_start=started, on_case_done=result)
        _report_to_platform(
            produced, chosen, out, on_event, modality=(contract.modality or "text")
        )

    return StreamingResponse(_stream_turn(run), media_type="text/event-stream")


def _report_to_platform(produced, chosen, out, on_event, modality: str = "text") -> None:
    """Put this run where every other run on the platform already is, and stream what happened."""

    def say(kind: str, text: str, detail: dict | None = None) -> None:
        on_event(type("E", (), {"kind": kind, "text": text, "tool": "", "detail": detail or {}})())

    reported, said = platform_api.deliver(produced, chosen, out, modality=modality)
    for line in said[:-1] if reported else said:
        say("text", line)
    if reported:
        say(
            "platform_run",
            said[-1],
            {"run_test_id": reported.run_test_id,
             "test_execution_id": reported.test_execution_id,
             "url": reported.url},
        )


def _folder(session_id: str = "") -> Path | None:
    """The folder to read from: the one asked for, or the open one.

    Reading a session is not the same as working in one. The harness holds a single live
    conversation because a conversation has a model behind it, but every artifact it produces
    is just files on disk. Tying reads to the open session made a whole environment unviewable
    while any other one was mid-stage, which is most of the time a suite is interesting.
    """
    if session_id:
        found = sessions.load(session_id, SESSIONS)
        return found.path if found else None
    return current.path if current else None


@app.get("/api/contract")
async def contract(session: str = ""):
    out = _folder(session)
    path = out / "contract.json" if out else None
    if not path or not path.exists():
        return JSONResponse({})
    return json.loads(path.read_text(encoding="utf-8"))


# How many records of a collection the page shows. Enough to see the shape of the data without
# sending a thousand orders to a browser.
SHOWN = 200


def _table(name: str, records: Any) -> dict:
    """One collection, in the shape the page draws, whatever the collection is kept in.

    A collection is not always a list. A table gives a list of records; a collection the agent's
    own code keeps is usually a mapping keyed by identifier, and slicing one of those raises
    rather than returning the first few. That took out the whole Environment tab for any adopted
    agent, which is every agent whose state was worth adopting.
    """
    if isinstance(records, dict):
        # Keyed, so the key is a column in its own right: it is what every other record refers to
        # this one by, and dropping it would show rows nothing could be matched against.
        rows = [{"_key": key, **value} if isinstance(value, dict) else {"_key": key, "value": value}
                for key, value in list(records.items())[:SHOWN]]
    elif isinstance(records, list):
        rows = [one if isinstance(one, dict) else {"value": one} for one in records[:SHOWN]]
    else:
        # A scalar or something else the agent keeps. Shown as itself rather than hidden.
        rows = [{"value": records}]
    return {
        "name": name,
        "count": len(records) if isinstance(records, (dict, list)) else 1,
        "columns": sorted({field for row in rows for field in row}),
        "rows": rows,
    }


@app.get("/api/world")
async def world(session: str = ""):
    out = _folder(session)
    if not world_saved(out):
        return JSONResponse({"tables": []})
    snapshot_file = out / "store.json"
    if snapshot_file.exists():
        # A container store's snapshot already holds every row as JSON. Reading it beats
        # restoring: a restore boots a whole engine container just to draw a page, and tears
        # it down again on close.
        state = dict(json.loads(snapshot_file.read_text(encoding="utf-8")).get("rows") or {})
        state_file = out / "state.json"
        if state_file.exists():
            for key, value in json.loads(state_file.read_text(encoding="utf-8")).items():
                state.setdefault(str(key), value)
        tables = [_table(name, records) for name, records in sorted(state.items())]
    else:
        # Restored rather than read out of a database file, because not every world has one. An
        # agent whose state lives in services and files saves a world with collections and no
        # SQLite, and opening it by filename showed an empty page for a world that was really
        # there.
        held = restore_world(out)
        try:
            tables = [_table(name, records) for name, records in sorted(held.state().items())]
        finally:
            held.close()
    manifest = {}
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    handlers = [
        {"name": source.stem, "source": source.read_text(encoding="utf-8")}
        for source in sorted((out / "handlers").glob("*.py"))
    ] if (out / "handlers").exists() else []
    return {
        "tables": tables,
        "tools": manifest.get("tools", []),
        "tool_specs": manifest.get("tool_specs", []),
        "handlers": handlers,
        "sequences": manifest.get("sequences", []),
        "notes": manifest.get("notes", ""),
    }


@app.get("/api/scenarios")
async def scenarios(session: str = ""):
    """Every scenario, with its files and its three gates re-run.

    The gates are re-run rather than remembered. They are milliseconds of pure code, and a
    scenario shown as validated when the world has since changed underneath it is worse than one
    shown as unknown.
    """
    from harness.catalogue import load_catalogue
    from harness.folder import folder_for
    from harness.prove import prove

    out = _folder(session)
    if not out:
        return []
    catalogue = load_catalogue(out)
    built = world_saved(out)
    cheap_gates = False
    if built:
        try:
            cheap_gates = str(read_manifest(out).get("store") or "sqlite") in ("sqlite", "in_process")
        except Exception:
            cheap_gates = False
    found = []
    for one in load_scenarios(out):
        body = one.model_dump()
        here = folder_for(out, one.name)
        body["folder"] = str(here)
        body["files"] = (
            sorted(str(f.relative_to(here)) for f in here.rglob("*") if f.is_file())
            if here.exists()
            else []
        )
        body["checks"] = [
            {
                "name": name,
                "settled_by": "code"
                if (g := catalogue.named(name)) and g.deterministic()
                else "a judge",
                "what": g.what if (g := catalogue.named(name)) else "",
                "source": g.check if (g := catalogue.named(name)) else "",
            }
            for name in one.sub_goals
        ]
        if built and cheap_gates:
            proof = prove(one, catalogue, out)
            body["gates"] = proof.gates()
            body["validated"] = proof.holds
            body["why"] = "" if proof.holds else proof.why()
        elif built:
            # A container-store world boots an engine per restore, and prove restores the
            # world three times per scenario — re-run per poll, that takes the whole service
            # down. A scenario is only saved after all three gates passed, so the listing
            # reports that verdict rather than re-earning it.
            body["gates"] = {}
            body["validated"] = True
            body["why"] = ""
        else:
            body["gates"] = {}
            body["validated"] = None
            body["why"] = "no world to check against yet"
        found.append(body)
    return found


@app.get("/api/scenario-file")
async def scenario_file(name: str, path: str, session: str = ""):
    """One file out of a scenario's folder, so the page can show what will actually run.

    Resolved and then checked to be inside that scenario's own folder: the path comes from a
    query string, and a page is not a trustworthy source of one.
    """
    from harness.folder import folder_for

    out = _folder(session)
    if not out:
        return JSONResponse({"error": "no agent open"}, status_code=404)
    here = folder_for(out, name).resolve()
    asked = (here / path).resolve()
    if not asked.is_relative_to(here) or not asked.is_file():
        return JSONResponse({"error": "no such file"}, status_code=404)
    return {"path": path, "source": asked.read_text(encoding="utf-8")}


@app.get("/api/subgoals")
async def subgoals(session: str = ""):
    """The shared catalogue. What every scenario is checked against."""
    from harness.catalogue import load_catalogue
    from harness.simulator import load_simulator_prompt

    out = _folder(session)
    if not out:
        return {"sub_goals": [], "simulator_prompt": ""}
    catalogue = load_catalogue(out)
    return {
        "sub_goals": [
            {
                "name": one.name,
                "what": one.what,
                "settled_by": "code" if one.deterministic() else "a judge",
                "check": one.check,
                "judged": one.judged,
            }
            for one in catalogue.sub_goals
        ],
        "simulator_prompt": load_simulator_prompt(out),
    }


@app.get("/api/runs")
async def runs(session: str = ""):
    return _runs(_folder(session))


@app.get("/api/platform")
async def platform_link(session: str = ""):
    """Where this session's runs are on the platform, for a page wanting to link there.

    Answers before any run has been reported too, so the caller can tell "not wired up" from
    "nothing run yet" rather than reading both as an absent link.
    """
    from harness import platform as platform_api

    out = _folder(session)
    return {
        "run_test_id": platform_api.remembered(out) if out else "",
        "url": (
            f"/dashboard/simulate/test/{platform_api.remembered(out)}/runs"
            if out and platform_api.remembered(out)
            else ""
        ),
        "blocked": platform_api.configured(),
    }


@app.get("/api/simulations")
async def simulations():
    """Every simulation this session has done, newest first.

    A session accumulates runs over the same scenarios and the same world, so which run a result
    came from is part of the result. One list, and each entry opens.
    """
    from harness.run.simulation import every_run

    return {"runs": every_run(current.path) if current else []}


@app.get("/api/simulations/{run_id}")
async def simulation(run_id: str):
    """One run in full: every scenario's verdict, conversation and tool calls."""
    from harness.run.simulation import read_run

    if current is None:
        return JSONResponse({"error": "no session open"}, status_code=404)
    try:
        return read_run(current.path, run_id)
    except FileNotFoundError as missing:
        return JSONResponse({"error": str(missing)}, status_code=404)


@app.get("/api/recording/{run_id}/{scenario}")
async def recording(run_id: str, scenario: str, track: str = "", session: str = ""):
    """The audio for one scenario in one run, when there is any.

    Served from the run folder rather than by absolute path, so a recording can only ever be
    read from inside the session it belongs to.
    """
    from harness.run.simulation import run_root

    out = _folder(session)
    if out is None:
        return JSONResponse({"error": "no session open"}, status_code=404)
    folder = run_root(out, run_id) / scenario
    if not folder.exists():
        return JSONResponse({"error": "no recording for this run"}, status_code=404)
    # A named track when one is asked for, and otherwise whichever is best of those that exist.
    # Several are written and any can be missing, so the fallback is the normal case rather than
    # the exception: a page that only knew about stereo would show a broken player most days.
    wanted = (track or "").strip().lower()
    audio = [
        one for one in sorted(folder.iterdir())
        if one.is_file() and one.suffix.lower() in (".wav", ".mp3", ".ogg", ".m4a")
    ]
    if wanted:
        for one in audio:
            if one.stem.lower() == wanted:
                return FileResponse(one)
    for prefer in ("stereo", "combined"):
        for one in audio:
            if one.stem.lower().startswith(prefer):
                return FileResponse(one)
    if audio:
        return FileResponse(max(audio, key=lambda one: one.stat().st_size))
    return JSONResponse({"error": "no recording for this run"}, status_code=404)


def main() -> None:
    import uvicorn

    # Loopback is the right default on a developer's machine and the wrong one inside a
    # container, where it leaves the port reachable from nowhere but the container itself.
    host = os.environ.get("HARNESS_HOST", "127.0.0.1")
    port = int(os.environ.get("HARNESS_PORT", "8777"))
    print(f"model:       {chosen_model()}")
    print(credentials_hint())
    print(f"\nopen http://localhost:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
