"""A minimal MCP client over Streamable HTTP, as the gateway's own clients
speak it (the cassette recorder, cmd/agentcc-cassette/record/rpc.go and
mcp.go, and M17's probe):

- era "auto" (or empty) tries 2026-07-28's stateless server/discover first:
  every request then carries _meta and the mirrored Mcp-Method, Mcp-Name and
  Mcp-Param-* headers (internal/mcp/upstream/wire/headers.go). Otherwise it
  falls back to a legacy initialize at 2025-11-25, keeps Mcp-Session-Id, sends
  notifications/initialized and MCP-Protocol-Version, and DELETEs the session
  at the end;
- an answer is a JSON body or an SSE stream, matched by request id;
- tools/list is read page by page, refusing a cursor loop.

Answers are decoded as the gateway decodes them (toolshash.loads_go), so the
tools hash of what this client lists equals the gateway's.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from . import toolshash, transport

VERSION_2026 = "2026-07-28"
NEWEST_LEGACY = "2025-11-25"
LEGACY_ERAS = ("2025-11-25", "2025-06-18", "2025-03-26")
CLIENT_INFO = {"name": "futureagi-connector-smoke", "version": "1"}
FATAL_CODES = {-32020, -32021, -32022, -32001}
MAX_PAGES = 100


class MCPError(Exception):
    """The session failed; code is safe to print."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Reply:
    result: object | None
    error: object | None
    status: int


def _dumps(value) -> bytes:
    return json.dumps(
        value, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def encode_header_value(v: str) -> str:
    """wire.EncodeHeaderValue: itself when printable ASCII without edge spaces,
    else =?base64?<Base64 of its UTF-8>?=."""
    safe = v == "" or (
        v[0] != " "
        and v[-1] != " "
        and not v.startswith("=?base64?")
        and all(0x20 <= ord(c) <= 0x7E for c in v)
    )
    return (
        v
        if safe
        else "=?base64?" + base64.b64encode(v.encode("utf-8")).decode("ascii") + "?="
    )


def _token(s) -> bool:
    return (
        isinstance(s, str)
        and s != ""
        and all(c in "-_" or c.isascii() and c.isalnum() for c in s)
    )


def mirrored(method: str, params, input_schema) -> dict:
    """The 2026-07-28 mirrored headers of one request (wire.Mirrored, without
    a client's inbound headers)."""
    headers = {"Mcp-Method": method}
    params = params if isinstance(params, dict) else {}
    if method in ("tools/call", "prompts/get") and isinstance(params.get("name"), str):
        headers["Mcp-Name"] = encode_header_value(params["name"])
    if (
        method != "tools/call"
        or not isinstance(params.get("arguments"), dict)
        or not params["arguments"]
    ):
        return headers
    props = input_schema.get("properties") if isinstance(input_schema, dict) else None
    for arg in sorted(props or {}):
        marker = (
            props[arg].get("x-mcp-header") if isinstance(props[arg], dict) else None
        )
        if isinstance(marker, str) and _token(marker):
            name = marker
        elif marker is True and _token(arg):
            name = arg
        else:
            continue
        if arg in params["arguments"]:
            value = params["arguments"][arg]
            text = (
                value
                if isinstance(value, str) and not isinstance(value, toolshash.Number)
                else _dumps(value).decode()
            )
            headers["Mcp-Param-" + name] = encode_header_value(text)
    return headers


def _answers(msg, request_id: int) -> bool:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return False
    rid = msg.get("id")
    if not isinstance(rid, toolshash.Number) or str.__str__(rid) != str(request_id):
        return False
    return ("result" in msg) != ("error" in msg)


class Session:
    def __init__(
        self,
        url: str,
        headers: dict,
        era: str = "auto",
        timeout: float = transport.DEFAULT_TIMEOUT,
        net: transport.Net | None = None,
    ):
        self.url = url
        self.net = net
        self.headers = dict(headers)
        self.era = era or "auto"
        self.timeout = timeout
        self.version = ""
        self.stateless = False
        self.session_id = ""
        self.next_id = 0
        self.schemas = {}  # tool name -> inputSchema, for Mcp-Param headers

    # -- one request --------------------------------------------------------
    def _post(self, method: str, params, body: bytes) -> transport.Response:
        h = dict(self.headers)
        h["Content-Type"] = "application/json"
        h["Accept"] = "application/json, text/event-stream"
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.stateless:
            name = params.get("name") if isinstance(params, dict) else None
            h.update(mirrored(method, params, self.schemas.get(name)))
            h["MCP-Protocol-Version"] = VERSION_2026
        elif method != "initialize" and self.version:
            h["MCP-Protocol-Version"] = self.version
        try:
            return transport.request(
                "POST", self.url, h, body, timeout=self.timeout, net=self.net
            )
        except transport.TransportError as err:
            raise MCPError(err.code) from None

    def call(self, method: str, params=None) -> Reply:
        self.next_id += 1
        rid = self.next_id
        if self.stateless:
            params = dict(params or {})
            meta = dict(params.get("_meta") or {})
            meta.update(
                {
                    "io.modelcontextprotocol/protocolVersion": VERSION_2026,
                    "io.modelcontextprotocol/clientCapabilities": {},
                    "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
                }
            )
            params["_meta"] = meta
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        resp = self._post(method, params, _dumps(msg))
        try:
            if method == "initialize":
                self.session_id = resp.headers.get("mcp-session-id", "")
            if resp.status in (401, 403):
                raise MCPError("credential_refused")
            if 300 <= resp.status < 400:
                raise MCPError("redirect_refused")
            answer = self._read_answer(resp, rid)
        except transport.TransportError as err:
            raise MCPError(err.code) from None
        finally:
            resp.close()
        if answer is None:
            raise MCPError(f"no_answer_http_{resp.status}")
        error = answer.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), toolshash.Number):
            try:
                if int(str.__str__(error["code"])) in FATAL_CODES:
                    raise MCPError(f"protocol_error_{int(str.__str__(error['code']))}")
            except ValueError:
                pass
        return Reply(answer.get("result"), error, resp.status)

    def _read_answer(self, resp: transport.Response, rid: int):
        if resp.content_type() != "text/event-stream":
            body = resp.read()
            try:
                msg = toolshash.loads_go(body)
            except toolshash.ToolsHashError:
                return None
            return msg if _answers(msg, rid) else None
        event, data = "", []
        while True:
            line = resp.readline()
            if not line:
                return None  # the stream ended before the answer
            line = line.rstrip(b"\n").rstrip(b"\r")
            if line == b"":
                if data and event in ("", "message"):
                    try:
                        msg = toolshash.loads_go(b"\n".join(data))
                    except toolshash.ToolsHashError:
                        msg = None
                    if _answers(msg, rid):
                        return msg
                event, data = "", []
                continue
            field, _, value = line.partition(b":")
            if value.startswith(b" "):
                value = value[1:]
            if field == b"event":
                event = value.decode("utf-8", "replace")
            elif field == b"data":
                data.append(value)

    def notify(self, method: str) -> None:
        resp = self._post(method, None, _dumps({"jsonrpc": "2.0", "method": method}))
        resp.close()
        if resp.status // 100 != 2:
            raise MCPError(f"notify_http_{resp.status}")

    # -- the session ----------------------------------------------------------
    def connect(self) -> str:
        era = self.era
        if era in ("auto", VERSION_2026):
            self.stateless = True
            try:
                reply = self.call("server/discover")
                versions = (
                    reply.result.get("supportedVersions")
                    if isinstance(reply.result, dict)
                    else None
                )
                if (
                    reply.error is None
                    and isinstance(versions, list)
                    and VERSION_2026 in versions
                ):
                    self.version = VERSION_2026
                    return self.version
            except MCPError:
                pass
            self.stateless = False
            if era == VERSION_2026:
                raise MCPError("era_2026_not_served")
            era = NEWEST_LEGACY
        elif era not in LEGACY_ERAS:
            raise MCPError(f"era_unsupported_{era}")
        reply = self.call(
            "initialize",
            {"protocolVersion": era, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        version = (
            reply.result.get("protocolVersion")
            if isinstance(reply.result, dict)
            else None
        )
        if reply.error is not None or not isinstance(version, str) or not version:
            raise MCPError("initialize_failed")
        self.version = version
        self.notify("notifications/initialized")
        return self.version

    def list_tools(self) -> list:
        tools, seen, cursor = [], set(), None
        for _ in range(MAX_PAGES):
            reply = self.call("tools/list", {"cursor": cursor} if cursor else None)
            if reply.error is not None or not isinstance(reply.result, dict):
                raise MCPError("tools_list_failed")
            page = reply.result.get("tools") or []
            if not isinstance(page, list):
                raise MCPError("tools_list_failed")
            tools.extend(page)
            for tool in page:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    self.schemas[tool["name"]] = tool.get("inputSchema")
            cursor = reply.result.get("nextCursor")
            if not cursor:
                return tools
            if cursor in seen:
                raise MCPError("tools_list_cursor_loop")
            seen.add(cursor)
        raise MCPError("tools_list_too_many_pages")

    def call_tool(self, name: str, arguments: dict) -> Reply:
        return self.call("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        """End a legacy session, best effort."""
        if not self.session_id:
            return
        h = dict(self.headers)
        h["Mcp-Session-Id"] = self.session_id
        if self.version:
            h["MCP-Protocol-Version"] = self.version
        try:
            transport.request(
                "DELETE",
                self.url,
                h,
                None,
                timeout=min(self.timeout, 10.0),
                net=self.net,
            ).close()
        except transport.TransportError:
            pass
        self.session_id = ""
