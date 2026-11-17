"""Provider-wide checks, mirroring CN1's loader and L1
(agentcc-gateway/internal/connectorcatalog/manifest: catalog.go, l1.go) so
a problem shows at generate time, not at the next catalog check. The Go
checks stay the authority; these never accept what they refuse."""

from __future__ import annotations

import json
from typing import Any

MAX_SERVER_TOOLS = 128
TOOL_LIST_TOKEN_BUDGET = 20000
PROTOCOL_SCOPES = ("openid", "offline_access", "profile", "email")
_LEGACY = ("definitions", "dependencies", "additionalItems", "$recursiveRef", "$recursiveAnchor")
_SUBSCHEMA = ("items", "additionalItems", "additionalProperties", "not", "if", "then", "else",
              "contains", "propertyNames", "unevaluatedItems", "unevaluatedProperties")  # fmt: skip
_SCHEMA_MAPS = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas", "dependencies")
_SCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems", "items")


def check_provider(tools: list[dict], items: list[dict], oauth2_scopes: list[str] | None,
                   verify_tool: str, where: str) -> list[str]:  # fmt: skip
    """Problems with the provider's curated items and generated tools taken
    together, as the catalog loader takes them."""
    out: list[str] = []
    every = list(items) + list(tools)
    names = [t.get("name") for t in every]
    for name in sorted({n for n in names if names.count(n) > 1}, key=str):
        out.append(f"{where}: tool {name} is listed twice (tools.items and the overlay)")
    if len(every) > MAX_SERVER_TOOLS:
        out.append(f"{where}: {len(every)} tools; a server lists at most {MAX_SERVER_TOOLS}")
    if verify_tool and verify_tool not in names:
        out.append(f"{where}: auth.verify.tool {verify_tool} is not one of the provider's tools")
    for t in tools:
        for msg in schema_dialect(t.get("inputSchema", {})):
            out.append(f"{where}: tool {t.get('name')}: {msg}")
    out += _scope_union(every, oauth2_scopes, where)
    tokens = tool_list_tokens(every)
    if tokens > TOOL_LIST_TOKEN_BUDGET:
        out.append(f"{where}: tools/list is about {tokens} tokens; the budget is {TOOL_LIST_TOKEN_BUDGET} "
                   "(select fewer operations, project body fields, or shorten descriptions)")  # fmt: skip
    return out


def _scope_union(tools: list[dict], requested: list[str] | None, where: str) -> list[str]:
    if requested is None:
        return []
    out, needed = [], set()
    for t in tools:
        scopes = t.get("scopes")
        if scopes is None:
            out.append(f"{where}: tool {t.get('name')} declares no scopes; "
                       "an oauth2 provider's tools each list the scopes they need")  # fmt: skip
            continue
        for s in scopes:
            needed.add(s)
            if s not in requested:
                out.append(f"{where}: tool {t.get('name')} needs {s}, which auth.oauth2.scopes does not request")
    for s in requested:
        if s not in needed and s not in PROTOCOL_SCOPES:
            out.append(f"{where}: auth.oauth2.scopes requests {s}, which no tool needs (the scope union must equal it)")
    return out


def schema_dialect(schema: Any) -> list[str]:
    """Why a tool's inputSchema is not plain, self-contained 2020-12."""
    if not isinstance(schema, dict):
        return ["inputSchema is not an object"]
    out = []
    if schema.get("type") != "object":
        out.append("inputSchema's root must have type object")

    def visit(node: dict) -> None:
        for key, value in node.items():
            if key in _LEGACY:
                out.append(f"inputSchema uses {key}, a keyword JSON Schema 2020-12 replaced")
            if key == "items" and isinstance(value, list):
                out.append("inputSchema uses items as an array; 2020-12 spells it prefixItems")
            if key == "$ref" and isinstance(value, str) and not value.startswith("#"):
                out.append(f"inputSchema references {value}; only local references are allowed")
            if key in _SCHEMA_MAPS and isinstance(value, dict):
                for sub in value.values():
                    if isinstance(sub, dict):
                        visit(sub)
            elif key in _SUBSCHEMA or key in _SCHEMA_LISTS:
                for sub in value if isinstance(value, list) else [value]:
                    if isinstance(sub, dict):
                        visit(sub)

    visit(schema)
    return out


def _go_json(value: Any) -> str:
    """Go's encoding/json output: compact, with <, > and & escaped."""
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), (" ", "\\u2028"), (" ", "\\u2029")):
        text = text.replace(ch, esc)
    return text


def tool_list_tokens(tools: list[dict]) -> int:
    """manifest.ToolListTokens: characters of each tool as tools/list sends
    it, over four, rounded up (MCP-PLAN.md:773)."""
    chars = 0
    for t in tools:
        ann = t.get("annotations") or {}
        obj: dict[str, Any] = {"name": t.get("name", "")}
        if t.get("title"):
            obj["title"] = t["title"]
        if t.get("description"):
            obj["description"] = t["description"]
        obj["inputSchema"] = t.get("inputSchema") or {"type": "object"}
        if t.get("outputSchema"):
            obj["outputSchema"] = t["outputSchema"]
        obj["annotations"] = {k: bool(ann.get(k, False)) for k in
                              ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")}  # fmt: skip
        chars += len(_go_json(obj))
    return (chars + 3) // 4
