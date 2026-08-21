"""Serving a real voice agent's tool calls from a generated world.

A hosted voice agent executes its tools by calling a webhook. So the whole integration is one
thing: stand up that webhook, and answer it from the world instead of from canned responses.

That single swap is what the environment was built for. The previous run's known issues were all
the same defect wearing different clothes:

- *"Mocked tools always succeed, including removing an item that was never added."*
- *"Mock responses do not vary by argument, so read-after-write flows are wrong."*
- *"World state does not change unless a scenario sets state_updates, which is often empty."*

A world that really holds rows and can really refuse answers all three, because the reply the
agent hears is produced by running the call rather than by looking it up.

Nothing here decides pass or fail. Grading reads the world afterwards and the calls this server
recorded, through the same sub-goal checks every other run uses.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Mapping

from ..world.runtime import GeneratedWorld

logger = logging.getLogger(__name__)

VAPI_API = os.environ.get("VAPI_API_BASE_URL", "https://api.vapi.ai").rstrip("/")

# Vapi's edge rejects the default urllib User-Agent with a 403 that says nothing about why, while
# the identical request from curl succeeds. Sending one is the whole fix.
_AGENT = "alk-harness/0.1"


class WorldWebhook:
    """The webhook a hosted agent calls, answered by a generated world.

    One world at a time. ``bind`` swaps which world is live between scenarios, so the assistant
    stays configured while every scenario still starts from its own restored copy.
    """

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        # A container must bind 0.0.0.0 on a fixed port or nothing outside can
        # be configured to call it; a laptop keeps loopback and an ephemeral port.
        host = host if host is not None else os.environ.get("HARNESS_WEBHOOK_HOST", "127.0.0.1")
        port = port if port is not None else int(os.environ.get("HARNESS_WEBHOOK_PORT", "0"))
        self._world: GeneratedWorld | None = None
        self._lock = threading.Lock()
        try:
            server = HTTPServer((host, port), _handler_for(self))
        except OSError:
            # A leftover server from an earlier run must not block this one; any free port works
            # because the public URL is discovered after binding.
            logger.warning("port %s busy, binding an ephemeral port instead", port)
            server = HTTPServer((host, 0), _handler_for(self))
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)

    def start(self) -> "WorldWebhook":
        self._thread.start()
        logger.info("world webhook listening on port %s", self.port)
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def bind(self, world: GeneratedWorld) -> None:
        """Make one world live. Its own call log is what grading reads afterwards."""
        with self._lock:
            self._world = world
            world.reset()

    @property
    def calls(self) -> list[Any]:
        with self._lock:
            return list(self._world.calls) if self._world else []

    def respond(self, name: str, arguments: Mapping[str, Any]) -> str:
        """Answer one tool call by running it.

        A refusal is returned as the answer, not as an error: the agent has to hear "that item is
        unavailable" and cope with it, which is the whole reason the world can say no. What it
        must never hear is an acknowledgement for something that did not happen.
        """
        with self._lock:
            world = self._world
        if world is None:
            return "the environment is not ready"

        # Caller hydration is an adapter concern, not a contract tool. The local
        # LiveKit worker normally gets this from its demo API before exposing any
        # conversational tools. Resolve the same safe profile from the generated
        # world without adding a call that scenario grading would mistake for an
        # agent action.
        if name == "lookup_rider_by_phone":
            phone = str(arguments.get("phone") or "")
            state = world.observe().state
            user = next(
                (row for row in state.get("users", []) if str(row.get("phone")) == phone),
                None,
            )
            if user is None:
                return json.dumps({"rider_id": None, "phone": phone})
            market = next(
                (
                    row
                    for row in state.get("market_config", [])
                    if row.get("market") == user.get("default_market")
                ),
                {},
            )
            return json.dumps(
                {
                    **user,
                    "cash_supported_in_market": bool(market.get("cash_supported")),
                    "accessibility_needs": [],
                },
                default=str,
            )

        done = world.handle_tool_call({"name": name, "arguments": dict(arguments)})
        if done is None:
            return f"there is no tool called {name}"
        return done.content or ("done" if done.success else "that could not be done")


def _handler_for(owner: "WorldWebhook"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # silence per-request stderr noise
            return

        def do_POST(self) -> None:  # noqa: N802 - required name
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                payload = {}

            calls = tool_calls(payload)
            if not calls:
                # An agent whose tools are its own HTTP API asks differently: the tool is the
                # path and the body is the arguments, with the answer expected back plainly.
                # Serving both shapes is what lets a world stand in for such an API without the
                # agent being changed to suit us.
                name = self.path.strip("/").split("?")[0]
                if name:
                    answer = owner.respond(name, payload if isinstance(payload, dict) else {})
                    plain = json.dumps(_as_body(answer)).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(plain)))
                    self.end_headers()
                    self.wfile.write(plain)
                    return

            results = [
                {"toolCallId": call_id, "result": owner.respond(name, arguments)}
                for call_id, name, arguments in calls
            ]
            body = json.dumps({"results": results}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _as_body(answer: str) -> Any:
    """A tool's answer as a JSON body, keeping structure when the handler produced any.

    Handlers return text because that is what a spoken agent hears. An HTTP tool API expects an
    object, so a JSON answer is passed through as itself and anything else is wrapped, rather
    than a caller having to parse a string out of a string.
    """
    try:
        parsed = json.loads(answer)
    except (json.JSONDecodeError, TypeError):
        return {"result": answer}
    return parsed if isinstance(parsed, (dict, list)) else {"result": parsed}


def tool_calls(payload: Mapping[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Pull (id, name, arguments) out of a provider's tool-call webhook body."""
    message = payload.get("message") or payload
    raw = message.get("toolCalls") or message.get("toolCallList") or []
    found: list[tuple[str, str, dict[str, Any]]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping):
            continue
        function = entry.get("function") or {}
        name = str(function.get("name") or entry.get("name") or "")
        arguments = function.get("arguments") or entry.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"_raw": arguments}
        if name:
            found.append((str(entry.get("id") or ""), name, dict(arguments)))
    return found


def pointed_at(tools: list[dict[str, Any]], webhook_url: str) -> list[dict[str, Any]]:
    """The agent's own tools, with only where they are answered changed.

    The assistant under test already has its tools — the names, the arguments, the enums are the
    agent's, defined by whoever built it. Redefining them here would mean testing an agent we
    wrote rather than theirs, and any drift between the two would show up as a finding about
    them. So nothing is rebuilt: the one thing that changes is the address the call goes to.
    """
    repointed: list[dict[str, Any]] = []
    for tool in tools:
        moved = json.loads(json.dumps(tool))
        moved.setdefault("server", {})["url"] = f"{webhook_url.rstrip('/')}/tool"
        repointed.append(moved)
    return repointed


def fetch_assistant(assistant_id: str, api_key: str) -> dict[str, Any]:
    """The assistant as it stands, so its own tools can be read rather than guessed."""
    import urllib.request

    request = urllib.request.Request(
        f"{VAPI_API}/assistant/{assistant_id}",
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": _AGENT},
    )
    with urllib.request.urlopen(request, timeout=20) as answer:
        return json.loads(answer.read())


def repoint_assistant(
    assistant_id: str, api_key: str, webhook_url: str
) -> list[str]:
    """Send the assistant's existing tool calls to our webhook. Returns the tools moved."""
    import urllib.request

    assistant = fetch_assistant(assistant_id, api_key)
    tools = (assistant.get("model") or {}).get("tools") or []
    if not tools:
        raise RuntimeError(
            f"assistant {assistant_id} has no tools, so there is nothing for the environment "
            "to answer. It is the agent's own tools that get repointed, not ones we add."
        )
    model = json.loads(json.dumps(assistant.get("model") or {}))
    model["tools"] = pointed_at(tools, webhook_url)

    body = json.dumps({"model": model}).encode()
    request = urllib.request.Request(
        f"{VAPI_API}/assistant/{assistant_id}",
        data=body,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": _AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=20) as answer:
        answer.read()
    return [
        str((one.get("function") or {}).get("name") or "") for one in model["tools"]
    ]
