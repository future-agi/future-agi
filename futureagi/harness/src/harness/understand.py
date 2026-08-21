"""Stage one: read an agent and produce its contract.

The stage is the same whatever the agent is. What changes between a repository, a provider
connection and a pasted definition is where the truth lives, and that comes from the source.

It stays open after the first answer, because a contract is usually right on the second look and
not the first. Correcting it is the next thing said, not a re-run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .config import artifact_dir, load_skill, read_only_session
from .contract import AgentContract
from .session import Stage
from .sources import AgentSource
from .tools import CONTRACT_SERVER, contract_tools, qualified

SKILL = "understand-agent"


def open_stage(
    source: AgentSource,
    *,
    out: Path | None = None,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 70,
) -> tuple[Stage, Path]:
    """A live understand-the-agent stage, and where it will write."""
    destination = out or artifact_dir(source.name)
    options = read_only_session(
        system_prompt=f"{load_skill(SKILL)}\n\n## This agent\n\n{source.briefing()}",
        cwd=source.workdir(),
        mcp_servers={**source.servers(), CONTRACT_SERVER: contract_tools(destination)},
        extra_tools=[
            *source.builtin_tools(),
            qualified(CONTRACT_SERVER, "submit_contract"),
        ],
        max_turns=max_turns,
    )
    if ask is not None:
        options.can_use_tool = ask
    return Stage(options, name=SKILL), destination


def opening(source: AgentSource) -> str:
    # The name is only a label for the artifact folder, and saying so matters: told to "read
    # the agent named verify_fix", a model went hunting the whole workspace for something
    # called verify_fix instead of reading the path it was given.
    return (
        "Read this agent and produce its contract. Where it lives is in your briefing; "
        f"{source.name!r} is only the label its artifacts are filed under, not something to "
        "search for.\n\n"
        "Work through the tools, their exact argument names and types, the constrained argument "
        "values, the rules it enforces, and its data. Ask me if the source genuinely does not "
        "settle something that changes what gets built. Call submit_contract when you are done."
    )


def load(destination: Path) -> AgentContract | None:
    """The contract on disk, if the stage produced one."""
    path = Path(destination) / "contract.json"
    if not path.exists():
        return None
    return AgentContract.model_validate(json.loads(path.read_text(encoding="utf-8")))


async def understand(
    source: AgentSource,
    *,
    out: Path | None = None,
    follow_ups: list[str] | None = None,
    on_event: Callable[..., Any] | None = None,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 70,
) -> AgentContract | None:
    """Run the stage start to finish and return the contract.

    ``follow_ups`` are corrections applied in the same session, the scripted equivalent of an
    operator typing them. ``ask`` handles clarifying questions; without it the model records what
    it could not resolve in ``open_questions`` instead of blocking.
    """
    stage, destination = open_stage(source, out=out, ask=ask, max_turns=max_turns)
    async with stage:
        await stage.say(opening(source), on_event=on_event)
        for follow_up in follow_ups or []:
            await stage.say(follow_up, on_event=on_event)
    return load(destination)
