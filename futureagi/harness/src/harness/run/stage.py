"""Stage four: run the scenarios against the real agent, and say what came back.

The last stage that was a command rather than a conversation. Nothing about it needed to be:
wiring the world to the assistant and running the checks is already code, and the part worth
having judgement on is which scenario to run and what a failure actually means.

That second part is why this is a stage at all. A failing check has four possible causes and only
one of them is a finding about the agent — the others are a wrong check, a wrong contract, or a
simulated caller that never asked for the thing. Deciding which is reading, not arithmetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import ClaudeAgentOptions

from ..config import (
    artifact_dir,
    UNWANTED,
    gate_hooks,
    chosen_model,
    load_skill,
    permission_gate,
    provider_env,
)
from ..contract import AgentContract
from ..scenario_tools import load_scenarios
from ..session import Stage
from ..tools import qualified
from .tools import RUN_SERVER, TOOL_NAMES, load_results, missing_prerequisites, run_tools

SKILL = "run-scenarios"


def open_stage(
    contract: AgentContract,
    *,
    out: Path | None = None,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 40,
) -> tuple[Stage, Path]:
    """A live run-the-scenarios stage, and where it will write its results."""
    destination = out or artifact_dir(contract.agent)
    server = run_tools(destination, destination, contract=contract)
    allowed = [
        "AskUserQuestion",
        *(qualified(RUN_SERVER, name) for name in TOOL_NAMES),
    ]
    options = ClaudeAgentOptions(
        system_prompt=(
            f"{load_skill(SKILL)}\n\n## This agent\n\n{contract.brief()}"
        ),
        allowed_tools=allowed,
        mcp_servers={RUN_SERVER: server},
        permission_mode="default",
        cwd=str(destination.parent if destination.parent.exists() else Path.cwd()),
        setting_sources=[],
        max_turns=max_turns,
        model=chosen_model(),
        env=provider_env(),
    )
    options.disallowed_tools = list(UNWANTED)
    options.hooks = gate_hooks(allowed)
    options.can_use_tool = permission_gate(ask, allowed)
    return Stage(options, name=SKILL), destination


def opening(contract: AgentContract, destination: Path) -> str:
    """What to tell the stage when it opens.

    Deliberately does not tell it to run everything. Each call costs money and takes minutes, and
    a stage that opens by spending the whole suite gives nobody a chance to say which one they
    cared about.
    """
    written = load_scenarios(destination)
    already = load_results(destination)
    blocked = missing_prerequisites() if contract.modality == "voice" else []
    if blocked:
        return (
            f"There are {len(written)} scenarios for {contract.agent!r}, but a live call cannot "
            "be placed yet:\n  - " + "\n  - ".join(blocked) + "\n\nSay this plainly and stop."
        )
    if already:
        passed = sum(1 for record in already if record["passed"])
        return (
            f"{len(already)} of {len(written)} scenarios for {contract.agent!r} have been run, "
            f"{passed} passing. Say where things stand with read_results, then ask which to run."
        )
    return (
        f"{len(written)} scenarios are ready for {contract.agent!r} and none has been run.\n\n"
        "Run preflight, then list_scenarios, then say which ones you would run first and why. "
        "Do not start running them until you are asked to — each call takes minutes and costs "
        "real money."
    )


def load(destination: Path) -> list[dict[str, Any]]:
    """What has been run for this agent, if anything has."""
    return load_results(Path(destination))
