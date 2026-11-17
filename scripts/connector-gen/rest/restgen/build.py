"""One overlay entry plus its spec operation make one tool.

The request contract a generated tool follows (README.md, "Request
contract") is fixed here: path inputs fill the {name} placeholders of
operation.path, the `body` input is the JSON body, every other input is a
query parameter, and a `const` input is always sent with its value."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from .annotate import annotations_for, check_posture, check_scopes
from .model import HTTP_METHODS, PATH_PARAM, Operation, OpRef, Param, SpecReader, base_path, find_refs, origin
from .overlay import ToolOverlay
from .problems import Problems
from .reserved import expand_resource, resource_pattern
from .project import project, require
from .schemaconv import OPAQUE, ConvError, at_path

TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
OPERATION_PATH = re.compile(r"^/[A-Za-z0-9._~:/@!$&'()*+,;=%{}-]{0,255}$")
DOTTED = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}([.][A-Za-z_][A-Za-z0-9_]{0,63}){0,7}$")
MAX_NAMESPACED = 64
BODY = "body"
# A portable tool input name: letters, digits, "_", "." and "-", at most 64.
# Names such as Microsoft Graph's $top or @odata.type fall outside what
# model providers' tool schemas reliably accept, so they are renamed.
INPUT_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# JSON Schema annotations from the generator to the runtime (README.md,
# "Request contract"); the runtime removes them before tools/list.
WIRE_NAME = "x-wireName"  # on a renamed query input: its name on the wire
ORIGIN = "x-origin"  # on each inputSchema root: the vendor API's origin


@dataclass
class ProviderContext:
    app_id: str
    oauth2_scopes: list[str] | None  # None: the provider does not use oauth2
    default_posture: str
    server: str
    credential_params: set[str]
    # The names of the credential parts and constants the provider places in the query, lowercased:
    # agentcc-connectors refuses at load a query input sent under one of them (IR-CN10b-3).
    query_credential_names: set[str] = field(default_factory=set)


def build_tool(reader: SpecReader, refs: list[OpRef], t: ToolOverlay, ctx: ProviderContext,
               probs: Problems) -> dict | None:  # fmt: skip
    """The tool for t, or None after adding why it cannot be built."""
    start = len(probs.items)
    fail = lambda msg: probs.add(f"{t.where}: {msg}")  # noqa: E731
    if not TOOL_NAME.match(t.name):
        fail("name must match ^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    if len(ctx.app_id) + 2 + len(t.name) > MAX_NAMESPACED:
        fail(f"{ctx.app_id}__{t.name} is longer than {MAX_NAMESPACED} characters")
    if not t.operation:
        return None
    found = find_refs(refs, t.operation)
    if not found:
        fail(f"operation {_matcher(t.operation)} is not in the spec")
        return None
    if len(found) > 1:
        labels = "; ".join(f"{r.method} {r.path} {r.ids}" for r in found[:5])
        fail(f"operation {_matcher(t.operation)} matches {len(found)} operations ({labels}); name it more exactly")
        return None
    try:
        op = reader.operation(found[0])
    except ConvError as exc:
        fail(f"operation {found[0].method} {found[0].path}: {exc}")
        return None
    if op.method not in HTTP_METHODS:
        fail(f"operation {op.label}: method {op.method} is not one of {', '.join(HTTP_METHODS)}")
    if op.servers and ctx.server not in op.servers:
        fail(f"operation {op.label} is served from {', '.join(op.servers)}, not {ctx.server}")
    if "{{" in op.path:
        fail(f"operation {op.label}: its path keeps an unfilled collection variable")
    path, props, required = _inputs(op, t, ctx, fail)
    full_path = base_path(ctx.server) + path
    if not OPERATION_PATH.match(full_path):
        fail(f"operation path {full_path} is not a valid operation path")
    annotations = annotations_for(op.method, t, fail)
    check_posture(t, annotations, ctx.default_posture, fail)
    check_scopes(t, op, ctx.oauth2_scopes, fail)
    response = _response(op, t, fail)
    _generic(t, props, fail)
    if len(probs.items) > start:
        return None
    tool: dict[str, Any] = {"name": t.name}
    if t.title:
        tool["title"] = t.title
    tool["description"] = t.description
    tool["annotations"] = annotations
    if t.posture:
        tool["posture"] = t.posture
    if t.scopes is not None:
        tool["scopes"] = list(t.scopes)
    tool["operation"] = {"method": op.method, "path": full_path}
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    schema[ORIGIN] = origin(ctx.server)
    tool["inputSchema"] = schema
    if response:
        tool["response"] = response
    if t.generic:
        tool["generic"] = True
    if t.generic_arguments:
        tool["genericArguments"] = list(t.generic_arguments)
    return tool


def _matcher(m: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in m.items())


def _inputs(op: Operation, t: ToolOverlay, ctx: ProviderContext, fail) -> tuple[str, dict, list[str]]:
    """The path with fixed values filled in, the input properties and the
    required ones: path parameters first, in path order."""
    names = {p.name for p in op.params}
    groups = (("fixed", t.fixed), ("exclude", t.exclude), ("describe", t.describe), ("required", t.required),
              ("rename", t.rename))  # fmt: skip
    for key, group in groups:
        for name in group:
            if name not in names:
                fail(f"params.{key} names {name}, which is not a parameter of {op.label}")
    path = op.path
    reserved = {name[1:] for name in PATH_PARAM.findall(path) if name.startswith("+")}
    placeholders = [name.removeprefix("+") for name in PATH_PARAM.findall(path)]
    exposed: list[Param] = []
    props: dict[str, dict] = {}
    for p in op.params:
        if p.name in reserved:
            try:
                resource_pattern(p.schema)
                if p.location != "path" or p.unsupported:
                    raise ValueError(p.unsupported or "reserved expansion must be a path input")
            except ValueError as exc:
                fail(f"parameter {p.name}: {exc}")
        if p.name in ctx.credential_params:
            if p.location == "path":
                fail(f"parameter {p.name} carries the credential but is in the path")
            continue  # the runtime sends the user's token as the bearer
        if p.name in t.fixed:
            value = t.fixed[p.name]
            _check_fixed(p, value, fail)
            if p.location == "path":
                if p.name in reserved:
                    try:
                        path = path.replace("{+" + p.name + "}", expand_resource(value, p.schema))
                    except ValueError as exc:
                        fail(f"params.fixed.{p.name}: {exc}")
                else:
                    path = path.replace("{" + p.name + "}", quote(str(value), safe=""))
            elif p.location == "query" and not p.unsupported:
                _put(props, p, t, _fixed_property(p, value, t, fail), fail, ctx)
            else:
                fail(f"parameter {p.name}: {p.unsupported or 'only a path or query parameter can be fixed'}")
            continue
        if p.name in t.exclude:
            if p.required:
                fail(f"parameter {p.name} is required by the operation, so it cannot be excluded")
            continue
        if p.unsupported:
            what = "pick another operation" if p.required else "exclude it in params.exclude"
            fail(f"parameter {p.name} ({p.location}): {p.unsupported}; {what}")
            continue
        exposed.append(p)
    for name in placeholders:
        if name not in names:
            fail(f"path placeholder {{{name}}} of {op.label} has no parameter")
    exposed.sort(key=lambda p: (0, placeholders.index(p.name)) if p.location == "path" and p.name in placeholders else (1, 0))
    required: list[str] = []
    for p in exposed:
        desc = t.describe.get(p.name) or p.description
        if not desc:
            fail(f"parameter {p.name} has no description in the spec; add params.describe.{p.name}")
        schema = dict(p.schema)
        schema["description"] = _with_note(desc, p.note)
        name = _put(props, p, t, schema, fail, ctx)
        if p.location == "path" and name != p.name:
            prefix = "{+" if p.name in reserved else "{"
            path = path.replace(prefix + p.name + "}", prefix + name + "}")
        if p.required or p.name in t.required:
            required.append(name)
    for name in t.required:
        if name in names and t.rename.get(name, name) not in props:
            fail(f"params.required names {name}, which is not an input of the tool")
    _body(op, t, props, required, fail)
    if BODY in props:
        for bad in _unsafe_names(props[BODY]):
            fail(f"the request body has a field named {bad!r}, which is not a portable input name "
                 f"({INPUT_NAME.pattern}); leave it out with body.fields")  # fmt: skip
    return path, props, required


def _unsafe_names(schema: Any, seen: int = 0) -> list[str]:
    """Property names anywhere in a schema that INPUT_NAME refuses."""
    out: list[str] = []
    if not isinstance(schema, dict) or seen > 64:
        return out
    for name, sub in (schema.get("properties") or {}).items():
        if not INPUT_NAME.match(str(name)):
            out.append(str(name))
        out += _unsafe_names(sub, seen + 1)
    for key in ("items", "additionalProperties"):
        out += _unsafe_names(schema.get(key), seen + 1)
    for key in ("allOf", "anyOf", "oneOf"):
        for sub in schema.get(key) or []:
            out += _unsafe_names(sub, seen + 1)
    return sorted(set(out))


def _put(props: dict, p: Param, t: ToolOverlay, schema: dict, fail, ctx: ProviderContext) -> str:
    """Add p's input under its model-facing name; return that name.

    A name outside INPUT_NAME (Microsoft Graph's $top, $filter, ...) is not
    portable across model providers, so such a parameter is renamed in
    params.rename. A renamed path parameter renames its placeholder; a
    renamed query parameter carries its name on the wire in WIRE_NAME."""
    name = t.rename.get(p.name, p.name)
    if not INPUT_NAME.match(name):
        fail(f"parameter {p.name}: {name!r} is not a portable tool input name "
             f"({INPUT_NAME.pattern}); rename it in params.rename")  # fmt: skip
    if name in props:
        fail(f"parameter {p.name}: the input name {name} is already taken")
    if name != p.name and p.location == "query":
        schema = {**schema, WIRE_NAME: p.name}
    if p.location == "query" and p.name.lower() in ctx.query_credential_names:
        fail(f"parameter {p.name} would go out as query parameter {p.name}, the name of a credential part "
             f"the provider places in the query (compared without case); exclude it, or list it in "
             f"credentialParams when it is the credential")  # fmt: skip
    props[name] = schema
    return name


def _with_note(desc: str, note: str) -> str:
    if not note or desc.endswith(note):
        return desc
    return f"{desc.rstrip('. ')}. {note}"


def _check_fixed(p: Param, value: Any, fail) -> None:
    type_ = p.schema.get("type")
    ok = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }.get(type_, True)
    if not ok:
        fail(f"params.fixed.{p.name}: {value!r} is not a {type_}")
    if "enum" in p.schema and value not in p.schema["enum"]:
        fail(f"params.fixed.{p.name}: {value!r} is not one of {p.schema['enum']}")


def _fixed_property(p: Param, value: Any, t: ToolOverlay, fail) -> dict:
    desc = t.describe.get(p.name) or p.description
    if not desc:
        fail(f"parameter {p.name} has no description in the spec; add params.describe.{p.name}")
    out = {k: v for k, v in p.schema.items() if k in ("type", "format")}
    out.update({"const": value, "default": value, "description": _with_note(desc, f"Always {value}.")})
    return out


def _body(op: Operation, t: ToolOverlay, props: dict, required: list[str], fail) -> None:
    if op.body is None:
        if t.body is not None:
            fail(f"body: {op.label} takes no request body")
        return
    if BODY in props:
        fail(f"a parameter named {BODY} collides with the request body input")
        return
    if op.body.unsupported:
        fail(f"request body: {op.body.unsupported}; pick another operation")
        return
    spec = t.body or {"fields": None, "required": [], "description": ""}
    schema = copy.deepcopy(op.body.schema)
    own = schema.get("description") if isinstance(schema.get("description"), str) else ""
    desc = spec["description"] or op.body.description or own.strip()
    if not desc:
        fail("the request body has no description in the spec; add body.description")
    if spec["fields"] is not None:
        schema = project(schema, spec["fields"], fail)
    require(schema, spec["required"], fail)
    schema["description"] = desc
    props[BODY] = schema
    if op.body.required:
        required.append(BODY)


def _response(op: Operation, t: ToolOverlay, fail) -> dict | None:
    if t.response is None:
        return None
    r = t.response
    out: dict[str, Any] = {}
    unwrap, fields, max_bytes = r.get("unwrap"), r.get("fields"), r.get("maxBytes")
    if unwrap is not None and (not isinstance(unwrap, str) or not DOTTED.match(unwrap)):
        fail("response.unwrap: must be a dotted path such as value or data.items")
    if fields is not None and (not isinstance(fields, list) or len(fields) > 64 or len(set(map(str, fields))) != len(fields)
                               or not all(isinstance(f, str) and DOTTED.match(f) for f in fields)):  # fmt: skip
        fail("response.fields: must be up to 64 distinct dotted paths")
        fields = None
    if max_bytes is not None and (isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= 1048576):
        fail("response.maxBytes: must be an integer from 1 to 1048576")
    schema = op.response_schema()
    base = schema
    if schema is not None and isinstance(unwrap, str):
        base = at_path(schema, unwrap)
        if base is None:
            fail(f"response.unwrap {unwrap} is not in the success response of {op.label}")
    for f in fields or []:
        if base is not None and base is not OPAQUE and at_path(base, f) is None:
            fail(f"response.fields {f} is not in the success response of {op.label}")
    for key, value in (("unwrap", unwrap), ("fields", fields), ("maxBytes", max_bytes)):
        if value is not None:
            out[key] = value
    return out or None


def _generic(t: ToolOverlay, props: dict, fail) -> None:
    if t.generic_arguments and not t.generic:
        fail("genericArguments needs generic: true")
    for name in t.generic_arguments:
        if name not in props:
            fail(f"genericArguments names {name}, which is not an input of the tool")
