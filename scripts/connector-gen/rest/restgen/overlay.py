"""The curated overlay, tools.yaml: which operations become tools and how.

It is read strictly, as the catalog's records are: an unknown key, a null
or a value of the wrong type is a problem, so a typo can never be ignored.
README.md ("tools.yaml") documents every key."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .problems import Problems
from .yamlio import has_null

SCHEMA = "connector-gen/rest-overlay/v1"
SPEC_DIR = "fixtures"
MIN_TOOLS, MAX_TOOLS = 8, 25
POSTURES = ("auto", "ask", "deny")
ANNOTATIONS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
_HTTPS = re.compile(r"^https://[a-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~:/?@!$&'()*+,;=%-]*)?$")
_MATCH_KEYS = ("method", "path", "id", "operationId", "name")


@dataclass
class ToolOverlay:
    name: str
    description: str
    operation: dict[str, str]
    where: str
    title: str = ""
    fixed: dict[str, Any] = field(default_factory=dict)
    exclude: list[str] = field(default_factory=list)
    describe: dict[str, str] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    rename: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None
    annotations: dict[str, bool] = field(default_factory=dict)
    posture: str = ""
    scopes: list[str] | None = None
    scope_source: str = ""
    response: dict[str, Any] | None = None
    generic: bool = False
    generic_arguments: list[str] = field(default_factory=list)


@dataclass
class Overlay:
    spec: str
    tools: list[ToolOverlay]
    server: str = ""
    server_source: str = ""
    credential_params: list[str] = field(default_factory=list)


class _Reader:
    def __init__(self, probs: Problems):
        self.p = probs

    def keys(self, node: Any, where: str, allowed: tuple, required: tuple = ()) -> bool:
        if not isinstance(node, dict):
            self.p.add(f"{where}: must be a mapping")
            return False
        for k in node:
            if k not in allowed:
                self.p.add(f"{where}: unknown key {k!r} (allowed: {', '.join(allowed)})")
        for k in required:
            if k not in node:
                self.p.add(f"{where}: {k} is required")
        return True

    def text(self, node: dict, key: str, where: str, lo: int = 1, hi: int = 1000) -> str:
        v = node.get(key)
        if v is None:
            return ""
        if not isinstance(v, str) or not lo <= len(v.strip()) <= hi:
            self.p.add(f"{where}.{key}: must be text of {lo}-{hi} characters")
            return ""
        return v.strip()

    def strings(self, node: dict, key: str, where: str, lo: int = 0, hi: int = 64) -> list[str]:
        v = node.get(key)
        if v is None:
            return []
        ok = isinstance(v, list) and all(isinstance(s, str) and s for s in v)
        if not ok or not lo <= len(v) <= hi or len(set(v)) != len(v):
            self.p.add(f"{where}.{key}: must be a list of {lo}-{hi} distinct non-empty strings")
            return []
        return list(v)

    def https(self, node: dict, key: str, where: str) -> str:
        v = node.get(key)
        if v is None:
            return ""
        if not isinstance(v, str) or not _HTTPS.match(v):
            self.p.add(f"{where}.{key}: must be an https URL with a lowercase host")
            return ""
        return v

    def flag(self, node: dict, key: str, where: str) -> bool | None:
        v = node.get(key)
        if v is not None and not isinstance(v, bool):
            self.p.add(f"{where}.{key}: must be true or false")
            return None
        return v


def parse_overlay(doc: Any, probs: Problems) -> Overlay | None:
    r = _Reader(probs)
    if has_null(doc):
        probs.add("a null value is not allowed; leave the key out instead")
        return None
    if not r.keys(doc, "tools.yaml", ("schema", "spec", "server", "serverSource", "credentialParams", "tools"),
                  ("schema", "spec", "tools")):  # fmt: skip
        return None
    if doc.get("schema") != SCHEMA:
        probs.add(f"schema must be {SCHEMA}")
    spec = doc.get("spec")
    parts = spec.split("/") if isinstance(spec, str) else []
    if len(parts) < 2 or parts[0] != SPEC_DIR or any(p in ("", ".", "..") for p in parts) or "\\" in spec:
        # The catalog loader (manifest/load.go, classify) accepts only
        # fixtures/** and evals/** beside tools.yaml, so a spec stored
        # anywhere else would fail every catalog check.
        probs.add(f"spec: must be a path under {SPEC_DIR}/ in the provider directory, such as {SPEC_DIR}/spec/<file>")
        spec = ""
    tools_raw = doc.get("tools")
    if not isinstance(tools_raw, list):
        probs.add("tools: must be a list")
        tools_raw = []
    elif not MIN_TOOLS <= len(tools_raw) <= MAX_TOOLS:
        probs.add(f"tools: lists {len(tools_raw)} operations; an overlay selects {MIN_TOOLS}-{MAX_TOOLS}")
    tools = [t for i, raw in enumerate(tools_raw) if (t := _tool(r, raw, f"tools[{i}]"))]
    return Overlay(
        spec=spec,
        tools=tools,
        server=r.https(doc, "server", "tools.yaml"),
        server_source=r.https(doc, "serverSource", "tools.yaml"),
        credential_params=r.strings(doc, "credentialParams", "tools.yaml", 1, 16),
    )


def _tool(r: _Reader, raw: Any, where: str) -> ToolOverlay | None:
    allowed = ("name", "title", "description", "operation", "params", "body", "annotations", "posture",
               "scopes", "scopeSource", "response", "generic", "genericArguments")  # fmt: skip
    if not r.keys(raw, where, allowed, ("name", "operation")):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        r.p.add(f"{where}.name: must be a tool name")
        return None
    where = f"{where} ({name})"
    description = r.text(raw, "description", where)
    if "description" not in raw:
        r.p.add(f"{where}: has no description; every tool states what it does, its formats and an example")
    t = ToolOverlay(name=name, description=description, operation={}, where=where,
                    title=r.text(raw, "title", where, 1, 100))  # fmt: skip
    op = raw.get("operation")
    if r.keys(op, f"{where}.operation", _MATCH_KEYS):
        if not op or not all(isinstance(v, str) and v for v in op.values()):
            r.p.add(f"{where}.operation: names the operation by id, operationId, name, or method and path")
        else:
            t.operation = dict(op)
    params = raw.get("params")
    if params is not None and r.keys(params, f"{where}.params", ("fixed", "exclude", "describe", "required", "rename")):
        fixed = params.get("fixed", {})
        if not isinstance(fixed, dict) or not all(isinstance(v, (str, int, float, bool)) for v in fixed.values()):
            r.p.add(f"{where}.params.fixed: maps a parameter to a string, number or boolean")
        else:
            t.fixed = dict(fixed)
        described = params.get("describe", {})
        if not isinstance(described, dict) or not all(isinstance(v, str) and v.strip() for v in described.values()):
            r.p.add(f"{where}.params.describe: maps a parameter to its description")
        else:
            t.describe = {k: v.strip() for k, v in described.items()}
        renamed = params.get("rename", {})
        if not isinstance(renamed, dict) or not all(isinstance(v, str) and v for v in renamed.values()):
            r.p.add(f"{where}.params.rename: maps a parameter to the input name the tool uses for it")
        else:
            t.rename = dict(renamed)
        t.exclude = r.strings(params, "exclude", f"{where}.params")
        t.required = r.strings(params, "required", f"{where}.params")
    body = raw.get("body")
    if body is not None and r.keys(body, f"{where}.body", ("fields", "required", "description")):
        t.body = {
            "fields": r.strings(body, "fields", f"{where}.body", 1, 64) if "fields" in body else None,
            "required": r.strings(body, "required", f"{where}.body"),
            "description": r.text(body, "description", f"{where}.body"),
        }
    ann = raw.get("annotations")
    if ann is not None and r.keys(ann, f"{where}.annotations", ANNOTATIONS):
        t.annotations = {k: v for k in ANNOTATIONS if (v := r.flag(ann, k, f"{where}.annotations")) is not None}
    posture = raw.get("posture")
    if posture is not None and posture not in POSTURES:
        r.p.add(f"{where}.posture: must be one of {', '.join(POSTURES)}")
    t.posture = posture if posture in POSTURES else ""
    if "scopes" in raw:
        t.scopes = r.strings(raw, "scopes", where, 0, 32)
    t.scope_source = r.https(raw, "scopeSource", where)
    resp = raw.get("response")
    if resp is not None and r.keys(resp, f"{where}.response", ("unwrap", "fields", "maxBytes")):
        t.response = dict(resp)
    t.generic = bool(r.flag(raw, "generic", where))
    t.generic_arguments = r.strings(raw, "genericArguments", where, 1, 4)
    return t
