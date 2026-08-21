"""Stage zero: working out which agent you mean.

Everything the harness does is about one agent, so something has to establish which one. That
used to be two flags on a command line, which is the wrong place for it: the whole point is that
you say what you want and it happens, and "here is my agent, set up a test environment for it"
is a sentence, not an invocation.

So this is a stage like any other. It can look around the filesystem to find what you are
pointing at, it asks if what you said is ambiguous, and it finishes by naming the agent and where
it lives. Everything after it, including where artifacts are written, follows from that.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from .config import (
    UNWANTED,
    artifact_dir,
    chosen_model,
    gate_hooks,
    permission_gate,
    provider_env,
)
from .session import Stage
from .sources import AgentSource, clone_github_repository, resolve, supported
from .tools import qualified, schema

RECEPTION_SERVER = "agent"
TOOL_NAMES = ("point_at_agent",)

_INSTRUCTIONS = """
You are the front desk of a harness that builds test environments for agents.

Somebody has arrived with an agent they want tested. Your only job is to work out which agent,
and where it lives, and then call point_at_agent. Nothing else happens until you do.

Usually they will just tell you: a path, a repository, a folder, or a public GitHub URL. Take it.
For a local path, use Read, Glob and Grep to check it exists and to pick a sensible short name if
they did not give one. For a GitHub URL, call point_at_agent with kind "github" and the URL as
path. The harness clones it into this session; do not ask them to clone it themselves. A name is a
label for their artifacts, so lower case and no spaces.

The agent is usually somewhere else on disk, not inside the harness. A path they give you is
relative to where you are looking from, which is a workspace holding many repositories, so try
it as given before deciding it does not exist.

If the path really is not there, say so and say what you did find near it. If they gestured
vaguely at a directory holding several agents, look, and ask which one with AskUserQuestion.

Do not read the agent properly and do not start working anything out about it. That is the next
stage's job and it has its own instructions. Point at the agent, say in one line what you are
about to do, and stop.
"""


def point_at(
    name: str,
    path: str,
    kind: str,
    found: dict[str, AgentSource],
    source_dir: Path | None = None,
) -> dict[str, Any]:
    """Establish which agent this is, or say why it cannot be.

    A plain function rather than only a tool body, so what counts as a reachable agent can be
    exercised without standing up a session.
    """
    name, path, kind = name.strip(), path.strip(), (kind.strip() or "repo")
    if not name:
        return _err("no name: the artifacts have to be filed under something")
    if kind not in supported():
        return _err(f"no such kind {kind!r}; there is {', '.join(supported())}")
    if kind == "repo" and not Path(path).expanduser().exists():
        return _err(
            f"there is nothing at {path!r}. Look again with Glob, and if you cannot find it, "
            "ask where the agent actually lives."
        )
    try:
        if kind == "github":
            root = clone_github_repository(path, source_dir or artifact_dir(name) / "source")
            found["source"] = resolve(kind, name=name, root=root, url=path)
        else:
            found["source"] = resolve(kind, name=name, root=Path(path).expanduser())
    except Exception as failed:
        return _err(f"could not reach that agent: {failed}")
    return {
        "content": [{"type": "text", "text": f"Pointed at {name} ({kind}) at {path}."}]
    }


def open_stage(
    *,
    cwd: str | Path | None = None,
    source_dir: str | Path | None = None,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 20,
) -> tuple[Stage, dict[str, AgentSource]]:
    """A stage that establishes which agent this conversation is about."""
    found: dict[str, AgentSource] = {}

    @tool(
        "point_at_agent",
        "Name the agent this conversation is about and say where it is. `kind` is how it is "
        f"supplied, one of: {', '.join(supported())}. For a repository, `path` is its directory; "
        "for github, it is the public HTTPS repository URL and the harness clones it. Call this "
        "once you know what you are pointing at.",
        schema({"name": str, "path": str, "kind": str}, ["name", "path"]),
    )
    async def point_at_agent(args: dict[str, Any]) -> dict[str, Any]:
        return point_at(
            str(args.get("name") or ""),
            str(args.get("path") or ""),
            str(args.get("kind") or "repo"),
            found,
            Path(source_dir) if source_dir else None,
        )

    server = create_sdk_mcp_server(
        name=RECEPTION_SERVER, version="0.1.0", tools=[point_at_agent]
    )
    allowed = [
        "Read",
        "Glob",
        "Grep",
        "AskUserQuestion",
        *(qualified(RECEPTION_SERVER, name) for name in TOOL_NAMES),
    ]
    options = ClaudeAgentOptions(
        system_prompt=_INSTRUCTIONS.strip(),
        allowed_tools=allowed,
        mcp_servers={RECEPTION_SERVER: server},
        # Not acceptEdits: that auto-approves Edit and Write before the permission callback is
        # consulted, so a stage can rewrite an artifact by hand and skip the tool whose
        # whole job is to validate that change.
        permission_mode="default",
        cwd=str(cwd or Path.cwd()),
        setting_sources=[],
        max_turns=max_turns,
        model=chosen_model(),
        env=provider_env(),
    )
    options.disallowed_tools = list(UNWANTED)
    options.hooks = gate_hooks(allowed)
    options.can_use_tool = permission_gate(ask, allowed)
    return Stage(options, name="reception"), found


def opening() -> str:
    return (
        "Somebody has just opened the harness and has not said anything yet. Greet them in one "
        "short line and ask which agent they want tested and where it lives. Do not list your "
        "capabilities."
    )


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}
