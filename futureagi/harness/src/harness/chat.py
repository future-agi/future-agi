"""One conversation, from pointing at an agent to a world you can test against.

You say what you want, it does it, you say the next thing. Stages are not commands you invoke;
they are what the harness moves through while you keep talking. When one produces its artifact
the next opens on the same agent, and anything already built stays correctable by saying so.

Underneath, each stage is still its own session with its own instructions and its own tools, so
context stays small and a stage can be re-entered later without redoing the ones before it. That
is an implementation detail, not something to make somebody manage.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import build as build_stage
from . import reception as reception_stage
from . import scenarios as scenario_stage
from . import understand as understand_stage
from .config import artifact_dir
from .contract import AgentContract
from .run import stage as run_stage
from .session import Stage
from .sources import AgentSource, resolve
from .world.snapshot import saved as world_saved

RECEPTION = "reception"
UNDERSTAND = "understand"
BUILD = "build"
SCENARIOS = "scenarios"
RUN = "run"
DONE = "done"

_NEXT = {
    RECEPTION: UNDERSTAND,
    UNDERSTAND: BUILD,
    BUILD: SCENARIOS,
    SCENARIOS: RUN,
    RUN: DONE,
}


@dataclass
class Conversation:
    """The whole thing, held open."""

    # Both unknown until somebody says which agent this is about, which is itself a stage.
    source: AgentSource | None = None
    out: Path | None = None
    ask: Callable[..., Any] | None = None
    wanted: int = 10
    # Where to look for an agent. Almost never inside this repo: the harness lives in one place
    # and the agent being tested lives in another, so looking only at our own root means the
    # first thing anybody types cannot be found.
    workspace: Path | None = None
    stage_name: str = ""
    stage: Stage | None = None
    # Set by the flow tool when the open stage hands a request to the stage that owns it.
    _handoff: dict = field(default_factory=dict)
    spent_usd: float = 0.0
    history: list[str] = field(default_factory=list)
    _found: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Read off the artifacts rather than defaulting to the first stage. An agent whose world
        # is already built is at the scenarios, and saying otherwise before anything has been
        # opened makes every question about where this conversation is answer wrongly.
        self.stage_name = self.stage_name or self._resume_at()

    # -- what exists so far ----------------------------------------------------------

    @property
    def contract(self) -> AgentContract | None:
        return understand_stage.load(self.out) if self.out else None

    @property
    def world_built(self) -> bool:
        # The manifest, not a database file. Every saved world writes one; only some of them have
        # a SQLite file beside it, and an agent whose state lives in services and files has none.
        # Keyed on the database, such a world stays "not built" forever and the conversation can
        # never leave this stage, however well the build actually went.
        return world_saved(self.out)

    @property
    def scenarios_written(self) -> bool:
        return bool(self.out) and bool(scenario_stage.load(self.out))

    @property
    def anything_run(self) -> bool:
        return bool(self.out) and bool(run_stage.load(self.out))

    def _artifact_for(self, stage_name: str) -> bool:
        return {
            # A contract already on disk settles which agent this is just as well as being told,
            # so coming back to an agent does not mean pointing at its repository again.
            #
            # ``_found`` is checked too, because within the turn that points at an agent the
            # source is not on the conversation yet — it is read off afterwards. Without it, the
            # stage that has just succeeded is told it has produced nothing.
            RECEPTION: self.source is not None
            or self.contract is not None
            or self._found.get("source") is not None,
            UNDERSTAND: self.contract is not None,
            BUILD: self.world_built,
            SCENARIOS: self.scenarios_written,
            RUN: self.anything_run,
            DONE: True,
        }[stage_name]

    # -- moving between stages -------------------------------------------------------

    async def _close(self) -> None:
        if self.stage is not None:
            self.spent_usd += self.stage.spent_usd
            await self.stage.__aexit__(None, None, None)
            self.stage = None

    async def _open(self, stage_name: str) -> str:
        """Open a stage and return the message that starts it."""
        await self._close()
        self.stage_name = stage_name
        if stage_name == RECEPTION:
            self.stage, self._found = reception_stage.open_stage(
                cwd=self.workspace,
                ask=self.ask,
                # The UI allocates a session before the agent has a name. Put a GitHub clone in
                # that existing session rather than creating a second artifact directory.
                source_dir=(self.out / "source") if self.out else None,
            )
            self._grant_flow()
            await self.stage.__aenter__()
            return reception_stage.opening()

        # Deliberately not "is there a source": a contract on disk settles which agent this is,
        # and every stage after the first works from the contract rather than from the source.
        # Only re-reading the agent needs to know where it lives.
        if self.source is None and self.contract is None:
            raise RuntimeError("nobody has said which agent this is about yet")
        if stage_name == UNDERSTAND and self.source is None:
            # This guard has to come before the stage opens: with a contract on disk but no
            # source, reopening understand would otherwise die on source.briefing() instead of
            # saying what is actually missing.
            raise RuntimeError("cannot re-read the agent without knowing where it lives")
        if stage_name == UNDERSTAND:
            self.stage, _ = understand_stage.open_stage(
                self.source, out=self.out, ask=self.ask
            )
            opening = understand_stage.opening(self.source)
            self._grant_flow()
            await self.stage.__aenter__()
            return opening

        contract = self.contract
        if contract is None:
            raise RuntimeError("cannot go further before there is a contract")
        if stage_name == BUILD:
            self.stage, _ = build_stage.open_stage(
                contract,
                out=self.out,
                ask=self.ask,
                # Where the agent's own code lives, so its tools can be bound to rather
                # than rewritten. Empty for an agent given as a specification.
                source_root=str(getattr(self.source, "root", "") or ""),
            )
            opening = build_stage.opening(contract)
        elif stage_name == RUN:
            if not self.scenarios_written:
                raise RuntimeError("cannot run anything before there are scenarios")
            self.stage, _ = run_stage.open_stage(contract, out=self.out, ask=self.ask)
            opening = run_stage.opening(contract, self.out)
        else:
            if not self.world_built:
                raise RuntimeError("cannot write scenarios before there is a world")
            written = len(scenario_stage.load(self.out))
            wanted = written or self.wanted
            self.stage, _ = scenario_stage.open_stage(
                contract, out=self.out, wanted=wanted, ask=self.ask
            )
            opening = scenario_stage.opening(contract, wanted, written)
        self._grant_flow()
        await self.stage.__aenter__()
        return opening

    def next_stage(self) -> str | None:
        """The stage that follows the current one, once this one has produced its artifact."""
        if not self._artifact_for(self.stage_name):
            return None
        following = _NEXT.get(self.stage_name)
        return None if following in (None, DONE) else following

    def _flow_server(self):
        """One tool every stage gets: handing a request to the stage that owns it.

        "Create the world", said while the understand stage is open, used to land in a session
        with no build tools, which could only apologise. The stage is the one that knows the
        request is not its job, so the handoff is a tool it calls; whether moving on is allowed
        is still decided by code, from whether this stage's artifact exists.
        """
        from claude_agent_sdk import create_sdk_mcp_server, tool

        from .tools import schema

        wanted = self._handoff

        @tool(
            "hand_to_next_stage",
            "The person asked for something that belongs to the NEXT stage of this harness — "
            "building the environment when the contract is done, writing scenarios when the "
            "environment is built, running them when they are written. Call this with their "
            "request, word for word; the conversation moves forward and their request is "
            "handled there. Never call it to escape work that is this stage's own.",
            schema({"request": str}, []),
        )
        async def hand_to_next_stage(args: dict[str, Any]) -> dict[str, Any]:
            if not self._artifact_for(self.stage_name):
                return {
                    "content": [{
                        "type": "text",
                        "text": "This stage has not produced its artifact yet, so there is "
                        "nothing to move on from. Finish this stage's work first.",
                    }],
                    "is_error": True,
                }
            if self.next_stage() is None:
                return {
                    "content": [{"type": "text", "text": "there is no stage after this one"}],
                    "is_error": True,
                }
            wanted["request"] = str(args.get("request") or "").strip() or "continue"
            return {
                "content": [{
                    "type": "text",
                    "text": "Handed over. Say one short line that you are moving on, and stop.",
                }]
            }

        return create_sdk_mcp_server(name="flow", version="0.1.0", tools=[hand_to_next_stage])

    # -- talking ---------------------------------------------------------------------

    async def start(self, on_event: Callable[..., Any] | None = None) -> None:
        """Open the stage this agent is up to, and set it going."""
        opening = await self._open(self._resume_at())
        await self.stage.say(opening, on_event=on_event)  # type: ignore[union-attr]

    async def open_quietly(self) -> None:
        """Open the stage without telling it to start.

        A stage's opening message is an instruction to do the stage's work. Sending it because
        somebody said hello means a greeting kicks off a build, so it is only sent when the work
        is actually what was asked for.
        """
        await self._open(self._resume_at())

    def _resume_at(self) -> str:
        """Pick up where the artifacts say this agent got to."""
        if self.source is None and self.contract is None:
            return RECEPTION
        if self.contract is None:
            return UNDERSTAND
        if not self.world_built:
            return BUILD
        if not self.scenarios_written:
            return SCENARIOS
        return RUN

    def _grant_flow(self) -> None:
        if self.stage is not None:
            self.stage.grant("flow", self._flow_server(), ["hand_to_next_stage"], ask=self.ask)

    async def say(
        self, message: str, on_event: Callable[..., Any] | None = None
    ) -> None:
        """Send a message to whichever stage is open."""
        self.history.append(message)
        if self.stage is None:
            await self.open_quietly()
        await self.stage.say(message, on_event=on_event)  # type: ignore[union-attr]
        # Before anything acts on this turn, take up what it established. A handoff in the same
        # turn opens the next stage, and every stage is built from ``self.source``; read it off
        # afterwards instead and that hop dies on an agent nobody has named, taking the turn with
        # it and leaving the conversation in reception with no way forward.
        established = self._take_up()
        moved = False
        # A handoff moves the request, not just the conversation: the next stage opens and is
        # given the person's own words. Bounded, because each hop is a model turn.
        for _hop in range(3):
            request = self._handoff.pop("request", None)
            if not request:
                break
            following = self.next_stage()
            if following is None:
                break
            await self._open(following)
            moved = True
            await self.stage.say(request, on_event=on_event)  # type: ignore[union-attr]
        if established and not moved:
            # Nothing is left to decide once the agent is known, so it goes on rather than making
            # somebody confirm what they already said. Unless a handoff already moved us, which
            # would make this a second hop over the same request.
            await self.advance(on_event=on_event)

    def _take_up(self) -> bool:
        """Take up whatever the turn just established. True if this turn named the agent.

        Reception is the only stage whose result is not a file, so it is the only one the
        conversation has to read back.
        """
        settled = self._found.pop("source", None)
        if settled is None:
            return False
        self.source = settled
        self.out = self.out or artifact_dir(settled.name)
        return True

    def reachable(self) -> dict[str, str]:
        """Every stage, and why it can or cannot be opened right now.

        Stages are not a wizard. Coming back to correct a contract after the world is built is
        the ordinary case, not an exception, so any stage whose input exists can be opened at
        any time. What cannot be skipped is the input itself: there is nothing to build a world
        from without a contract, and nothing to write scenarios against without a world.
        """
        contract = self.contract is not None
        # Every stage after the first works from the contract, so that is the first thing each
        # of them needs; its own input is the second.
        needs_contract = "needs a contract first"
        why = {
            RECEPTION: "",
            UNDERSTAND: ""
            if self.source is not None
            else "cannot re-read the agent without knowing where its source lives",
            BUILD: "" if contract else needs_contract,
            SCENARIOS: ""
            if contract and self.world_built
            else (needs_contract if not contract else "needs a built environment first"),
            RUN: ""
            if contract and self.scenarios_written
            else (needs_contract if not contract else "needs scenarios first"),
        }
        return why

    async def go_to(
        self, stage_name: str, on_event: Callable[..., Any] | None = None
    ) -> str:
        """Open one stage by name, whether or not it is the next one.

        The stage is opened but not set going: its opening message is an instruction to do that
        stage's work, and somebody choosing to look at a stage has not thereby asked for it to
        start spending.
        """
        if stage_name not in _NEXT and stage_name != DONE:
            raise RuntimeError(f"no stage called {stage_name!r}")
        blocked = self.reachable().get(stage_name, "")
        if blocked:
            raise RuntimeError(f"cannot open the {stage_name} stage: {blocked}")
        return await self._open(stage_name)

    async def advance(self, on_event: Callable[..., Any] | None = None) -> str | None:
        """Move to the next stage and start it. Returns the stage entered, or None."""
        following = self.next_stage()
        if following is None:
            return None
        opening = await self._open(following)
        await self.stage.say(opening, on_event=on_event)  # type: ignore[union-attr]
        return following

    async def close(self) -> None:
        await self._close()


def open_conversation(
    *,
    name: str = "",
    path: str = "",
    kind: str = "repo",
    out: Path | None = None,
    ask: Callable[..., Any] | None = None,
    wanted: int = 10,
    workspace: Path | None = None,
) -> Conversation:
    """Open the harness. With nothing, it starts by asking which agent you mean.

    Naming the agent up front is a shortcut for coming back to one already in progress, not the
    way in. Everything it needs can be said.
    """
    source = resolve(kind, name=name, root=path) if name and path else None
    return Conversation(
        source=source,
        out=out or (artifact_dir(name) if name else None),
        ask=ask,
        wanted=wanted,
        workspace=workspace,
    )


async def _demo() -> None:  # pragma: no cover - convenience for manual runs
    conversation = open_conversation(name="demo", path=".")
    await conversation.start()
    await conversation.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
