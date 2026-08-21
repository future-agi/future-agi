"""A stage as a live conversation, emitting what happened as it happens.

The operator experiences one continuous session: point at an agent, watch a contract appear,
correct something, move on. Underneath, each stage is its own session so context stays small and
any stage can be re-entered without redoing the ones before it.

A stage stays open across turns, so a correction is the next thing said rather than a re-run,
and it yields typed events rather than a wall of text. A terminal renders those events as lines;
a browser renders the same events as a transcript on one side and the artifact on the other.
Neither is privileged, which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

TEXT = "text"
TOOL = "tool"
RESULT = "result"
ARTIFACT = "artifact"
DONE = "done"


@dataclass
class Event:
    """One observable thing the stage did.

    ``detail`` carries the data behind what is being shown, not just a label for it: which stage
    emitted this, and for a tool call the arguments it was made with. A terminal renders a line
    and ignores the rest; anything richer needs the data, and re-parsing a rendered line to get
    it back is how a second front end becomes a rewrite.
    """

    kind: str
    text: str = ""
    tool: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        """A terminal-friendly rendering."""
        if self.kind == TEXT:
            return self.text
        if self.kind == TOOL:
            target = self.detail.get("target") or ""
            return f"  [{self.tool}{' ' + target if target else ''}]"
        if self.kind == RESULT:
            marker = "!" if self.detail.get("is_error") else ">"
            body = "\n".join(
                f"  {marker} {row}" for row in self.text.splitlines() if row
            )
            return body or f"  {marker} (no output)"
        if self.kind == ARTIFACT:
            return f"  [saved {self.detail.get('path', '')}]"
        if self.kind == DONE:
            cost = self.detail.get("cost_usd")
            spent = f" ${cost:.4f}" if isinstance(cost, float) else ""
            failure = self.detail.get("error")
            wrong = self.detail.get("unexpected_model") or []
            return (
                f"  [{self.detail.get('outcome', '')} "
                f"turns={self.detail.get('turns', 0)}{spent}]"
                + (f"\n  !! {failure}" if failure else "")
                + (
                    f"\n  !! billed to {', '.join(wrong)}, which is not what was asked for"
                    if wrong
                    else ""
                )
            )
        return self.text


@dataclass
class Turn:
    """What one exchange produced."""

    text: str = ""
    events: list[Event] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    outcome: str = ""
    turns: int = 0
    cost_usd: float | None = None
    error: str = ""


_TARGET_KEYS = (
    "file_path",
    "path",
    "pattern",
    "agent",
    "tool",
    "tool_name",
    "table",
    "name",
)


def _why_it_failed(received: Any) -> str:
    """What actually went wrong, said in terms somebody can act on."""
    status = getattr(received, "api_error_status", None)
    errors = getattr(received, "errors", None) or []
    said = "; ".join(str(error) for error in errors)[:400]
    if "invalid_rapt" in said or "invalid_grant" in said:
        return (
            "the provider rejected the credentials. GOOGLE_APPLICATION_CREDENTIALS is probably "
            "not set in this shell, so it fell back to your gcloud login. Load the env file "
            "first: set -a; . ./.env.acceptance; set +a"
        )
    return f"the model call failed{f' ({status})' if status else ''}: {said or 'no detail given'}"


def readable(tool_name: str) -> str:
    """A tool's name as somebody reading along would say it.

    ``mcp__scenarios__try_calls`` is how the model addresses it and is noise to anybody else.
    """
    bare = tool_name.rsplit("__", 1)[-1]
    return bare.replace("_", " ")


def _target(payload: Any) -> str:
    """A short label for what a tool call was aimed at, for display only."""
    if not isinstance(payload, dict):
        return ""
    for key in _TARGET_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value if len(value) <= 80 else value[:77] + "..."
    return ""


def _result_text(block: ToolResultBlock, limit: int = 600) -> str:
    content = block.content
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    text = content if isinstance(content, str) else str(content)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _saved_path(block: ToolResultBlock) -> str:
    """The path a tool reports having written, if it wrote one.

    Only when the tool actually says it saved something. Matching any path-shaped token in any
    result meant that reading a file announced it as an artifact — the stage looks like it is
    producing output while it is still only looking around, and a front end reloads its panes on
    every read.
    """
    content = block.content
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    if not isinstance(content, str):
        return ""
    said = content.lower()
    if not any(verb in said for verb in ("saved", "wrote", "written")):
        return ""
    for token in content.split():
        # Trimmed before the check, not after. A tool that ends its sentence — "saved to
        # out/contract.json." — produces a token ending in the full stop, so testing the
        # suffix first missed every real save and matched only bare paths, which is what a
        # file *read* returns. The event fired on exactly the wrong occasions.
        cleaned = token.strip(".,;:!?)\"'")
        if cleaned.endswith((".json", ".py", ".sqlite")):
            return cleaned
    return ""


class Stage:
    """One stage of the harness, held open so it can be talked to."""

    def __init__(self, options: ClaudeAgentOptions, *, name: str = "") -> None:
        self._options = options
        self._client: ClaudeSDKClient | None = None
        self.name = name
        self.session_id: str | None = None
        self.history: list[Turn] = []
        # What actually got billed, read back rather than assumed. Asking for a model is not the
        # same as getting one: the CLI has its own default, and a request that quietly does not
        # take shows up only on the invoice, weeks later, as a number nobody can explain.
        self.models_used: set[str] = set()

    def grant(self, server_name: str, server: Any, tool_names: list[str], ask: Any = None) -> None:
        """Give this stage one more tool server, before it opens.

        The permission gate and the PreToolUse hook both close over the granted list when the
        stage is built, so appending to ``allowed_tools`` after the fact changes nothing — the
        hook still denies the new tool. Granting means rebuilding all three together, which is
        why it lives here rather than being three edits every caller must remember.
        """
        if self._client is not None:
            raise RuntimeError("grant before the stage opens; the session is already running")
        from .config import gate_hooks, permission_gate

        added = [f"mcp__{server_name}__{name}" for name in tool_names]
        self._options.mcp_servers = {**(self._options.mcp_servers or {}), server_name: server}
        self._options.allowed_tools = [*(self._options.allowed_tools or []), *added]
        self._options.hooks = gate_hooks(self._options.allowed_tools)
        self._options.can_use_tool = permission_gate(ask, self._options.allowed_tools)

    async def __aenter__(self) -> "Stage":
        self._client = ClaudeSDKClient(options=self._options)
        await self._client.connect()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    @property
    def client(self) -> ClaudeSDKClient:
        if self._client is None:
            raise RuntimeError("stage is not open; use it as an async context manager")
        return self._client

    async def stream(self, message: str) -> AsyncIterator[Event]:
        """Send a message and yield events as they arrive."""
        await self.client.query(message)
        turn = Turn()
        async for received in self.client.receive_response():
            for event in self._events(received, turn):
                # Which stage this came from, stamped once here rather than by every caller,
                # so a front end showing several stages can tell them apart.
                event.detail.setdefault("stage", self.name)
                turn.events.append(event)
                yield event
        self.history.append(turn)

    def _events(self, received: Any, turn: Turn) -> list[Event]:
        if isinstance(received, SystemMessage):
            data = received.data if isinstance(received.data, dict) else {}
            self.session_id = data.get("session_id") or self.session_id
            return []
        if isinstance(received, AssistantMessage):
            events: list[Event] = []
            for block in received.content:
                if isinstance(block, TextBlock):
                    turn.text += block.text
                    events.append(Event(TEXT, text=block.text))
                elif isinstance(block, ToolUseBlock):
                    turn.tools_used.append(block.name)
                    events.append(
                        Event(
                            TOOL,
                            tool=block.name,
                            detail={
                                "target": _target(block.input),
                                "arguments": block.input,
                                "label": readable(block.name),
                            },
                        )
                    )
            return events
        if isinstance(received, ResultMessage):
            # subtype alone is not the outcome. A call that failed upstream still arrives with
            # subtype "success", so reporting it verbatim tells somebody their stage worked when
            # nothing happened at all, and they go looking for the fault in their own request.
            failed = bool(
                getattr(received, "is_error", False)
                or getattr(received, "api_error_status", None)
            )
            turn.outcome = "failed" if failed else received.subtype
            turn.turns = received.num_turns
            turn.cost_usd = received.total_cost_usd
            turn.error = _why_it_failed(received) if failed else ""
            self.session_id = received.session_id or self.session_id
            billed = set(getattr(received, "model_usage", None) or {})
            self.models_used |= billed
            unexpected = self.unexpected_models()
            return [
                Event(
                    DONE,
                    detail={
                        "outcome": turn.outcome,
                        "turns": received.num_turns,
                        "cost_usd": received.total_cost_usd,
                        "error": turn.error,
                        "models": sorted(billed),
                        "unexpected_model": sorted(unexpected),
                    },
                )
            ]
        blocks = getattr(received, "content", None)
        if isinstance(blocks, list):
            events = []
            for block in blocks:
                if not isinstance(block, ToolResultBlock):
                    continue
                # What a tool said back is the only view a caller has of whether the work is
                # going well. Dropping it leaves a run that can only be diagnosed by guessing.
                events.append(
                    Event(
                        RESULT,
                        text=_result_text(block),
                        detail={"is_error": bool(getattr(block, "is_error", False))},
                    )
                )
                path = _saved_path(block)
                if path:
                    turn.artifacts.append(path)
                    events.append(Event(ARTIFACT, detail={"path": path}))
            return events
        return []

    async def say(
        self, message: str, *, on_event: Callable[[Event], None] | None = None
    ) -> Turn:
        """Send a message and wait for the whole reply."""
        async for event in self.stream(message):
            if on_event:
                on_event(event)
        return self.history[-1]

    def unexpected_models(self) -> set[str]:
        """Models that were billed but not the one asked for."""
        asked = getattr(self._options, "model", None)
        if not asked:
            return set()
        return {used for used in self.models_used if asked.split("-2")[0] not in used}

    @property
    def spent_usd(self) -> float:
        return sum(turn.cost_usd or 0.0 for turn in self.history)
