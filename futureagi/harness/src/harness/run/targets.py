"""What is being tested, and how the harness talks to it.

The rest of the run does not care what the agent under test is. It says something and gets a
reply back, and whatever tool calls happened in between landed in the world. That is the entire
interface, and keeping it that narrow is what lets the same scenarios, the same world and the
same grading run against an agent hosted anywhere.

Two things are supplied per target: how to say something to it, and how its tool calls reach the
world. ``LocalAgent`` runs the agent in this process from its contract, which needs nothing
except the contract and is what makes a suite runnable the moment the world is built. A hosted
target is the same class with the transport swapped: the agent runs wherever it runs, its tool
calls arrive over a webhook, and the webhook answers from ``world.handle_tool_call``. The world
does not change, the scenarios do not change, and the grading does not change.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from ..config import (
    UNWANTED,
    chosen_model,
    gate_hooks,
    permission_gate,
    provider_env,
)
from ..contract import AgentContract
from ..session import Stage
from ..tools import qualified
from ..world.runtime import GeneratedWorld

AGENT_SERVER = "agent"

_TYPES: dict[str, type] = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": float,
    "bool": bool,
    "boolean": bool,
    "list": list,
    "dict": dict,
}


def _python_type(declared: str) -> type:
    """The type a tool's argument is declared with, as something a schema can carry."""
    lowered = (declared or "").strip().lower()
    if lowered.startswith(("list", "sequence", "array")):
        return list
    if lowered.startswith(("dict", "mapping", "object")):
        return dict
    return _TYPES.get(lowered, str)


def describe(spec: Any, contract: AgentContract) -> str:
    """What the agent is told a tool takes, including the values it accepts.

    The values matter more than they look. An agent whose real schema enumerates its menu knows
    that a Big Mac combo is ``big_mac_combo``; the same agent without them guesses, gets refused,
    and reads as broken when what is broken is the harness that withheld them. Anything the
    contract recorded as permitted, the agent under test is told.
    """
    parts = [spec.description or f"{spec.name} for {contract.agent}"]
    for arg in spec.args:
        values = spec.arg_values.get(arg)
        if isinstance(values, (list, tuple)) and values:
            rendered = ", ".join(str(value) for value in values)
            parts.append(f"  {arg} accepts: {rendered}")
        elif arg in spec.arg_types:
            parts.append(f"  {arg}: {spec.arg_types[arg]}")
    return "\n".join(parts)


def agent_tools(contract: AgentContract, world: GeneratedWorld) -> Any:
    """The agent's own tools, wired to the world so a call really happens.

    Every call goes through ``world.call``, so a refusal comes back as a refusal the agent can
    read and recover from, rather than as a success it will happily build on.
    """

    def bind(spec: Any) -> Any:
        schema = {
            arg: _python_type(spec.arg_types.get(arg, "str")) for arg in spec.args
        }

        @tool(spec.name, describe(spec, contract), schema)
        async def call_tool(
            args: dict[str, Any], _name: str = spec.name
        ) -> dict[str, Any]:
            # Through handle_tool_call, not straight to world.call. That method is the interface
            # ALK's own runners drive an environment by, so going around it would leave the
            # claim that a generated world plugs into them untested — and free to drift.
            done = world.handle_tool_call({"name": _name, "arguments": args})
            if done is None:
                return {
                    "content": [{"type": "text", "text": f"no such tool {_name}"}],
                    "is_error": True,
                }
            return {
                "content": [{"type": "text", "text": done.content or ""}],
                **({} if done.success else {"is_error": True}),
            }

        return call_tool

    return create_sdk_mcp_server(
        name=AGENT_SERVER,
        version="0.1.0",
        tools=[bind(spec) for spec in contract.tools],
    )


def agent_prompt(contract: AgentContract) -> str:
    """The agent under test, as its contract describes it.

    Only what the contract records, because anything added here is a difference between the agent
    being graded and the agent that exists.
    """
    parts = [
        f"You are {contract.agent}: {contract.one_liner}".strip(),
        contract.system_prompt_excerpt.strip(),
    ]
    if contract.hard_constraints:
        parts.append(
            "Rules you must follow:\n  - " + "\n  - ".join(contract.hard_constraints)
        )
    if contract.modality == "voice":
        parts.append(
            "You are speaking out loud. Keep replies to what a person would actually say: "
            "short, no lists, no markdown."
        )
    parts.append(
        "Use your tools to do anything real. Never tell the customer something is done unless a "
        "tool confirmed it, and if a tool refuses, say so plainly and offer what is possible."
    )
    return "\n\n".join(part for part in parts if part)


@runtime_checkable
class Target(Protocol):
    """An agent under test, reachable by saying something to it."""

    key: str

    async def open(self) -> None: ...
    async def say(self, utterance: str) -> str: ...
    async def close(self) -> None: ...
    @property
    def spent_usd(self) -> float: ...


def _drivable(model: str | None) -> None:
    """Refuse a model this target cannot actually run, before a suite is graded on it.

    This target runs on the Claude Agent SDK against Vertex, so the only models it can drive are
    Anthropic's. Handed anything else it does not fail: it produces a session that answers
    nothing, which arrives as a scenario with no turns and no calls and every check red. That
    reads exactly like an agent that ignored the person, and the whole suite is wrong in a way
    nobody would think to question.
    """
    named = (model or "").strip().lower()
    if not named or "claude" in named or named.startswith("anthropic"):
        return
    raise RuntimeError(
        f"this target cannot run {model!r}. It drives the agent through the Claude Agent SDK on "
        "Vertex, which speaks to Anthropic models only. To run the agent on something else, "
        "point the spec's target at one of ALK's own endpoint adapters rather than at this one."
    )


class LocalAgent:
    """The agent run here, from its contract, with its tools bound to the world."""

    key = "local"

    def __init__(
        self,
        contract: AgentContract,
        world: GeneratedWorld,
        *,
        model: str | None = None,
        max_turns: int = 12,
    ) -> None:
        self.contract = contract
        self.world = world
        _drivable(model)
        allowed = [qualified(AGENT_SERVER, spec.name) for spec in contract.tools]
        options = ClaudeAgentOptions(
            system_prompt=agent_prompt(contract),
            allowed_tools=allowed,
            mcp_servers={AGENT_SERVER: agent_tools(contract, world)},
            permission_mode="default",
            setting_sources=[],
            max_turns=max_turns,
            model=chosen_model(model),
            env=provider_env(model),
        )
        # The agent under test gets its own tools and nothing else. A target that can reach a
        # file or a shell is not the agent anybody deployed.
        options.disallowed_tools = list(UNWANTED)
        options.hooks = gate_hooks(allowed)
        options.can_use_tool = permission_gate(granted=allowed)
        self._stage = Stage(options, name="target")

    async def open(self) -> None:
        await self._stage.__aenter__()

    async def say(self, utterance: str) -> str:
        turn = await self._stage.say(utterance)
        return turn.text.strip()

    async def close(self) -> None:
        await self._stage.__aexit__(None, None, None)

    @property
    def spent_usd(self) -> float:
        return self._stage.spent_usd


_REGISTRY: dict[str, Callable[..., Target]] = {LocalAgent.key: LocalAgent}


def register_target(key: str, factory: Callable[..., Target]) -> None:
    """Add a way of reaching an agent. A hosted runtime is a class and this line."""
    _REGISTRY[key] = factory


def resolve(key: str) -> Callable[..., Target]:
    if key not in _REGISTRY:
        raise NotImplementedError(
            f"no target {key!r}; registered targets are {', '.join(sorted(_REGISTRY))}"
        )
    return _REGISTRY[key]


def supported() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
