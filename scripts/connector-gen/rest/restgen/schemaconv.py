"""Vendor schemas to self-contained JSON Schema 2020-12.

Tool schemas are inlined, never referenced: CN1's L1 accepts only local
references and refuses draft-07 keywords, and a tools/list must stand on its
own. Inlining stops at MAX_DEPTH levels and at a cycle, where the schema
keeps only its type (an object stays an object the model may fill freely).
OpenAPI 3.0 `nullable`, Swagger 2.0 `x-nullable`, Discovery's string-typed
`minimum`, `maximum` and `default`, and every `x-*` extension are mapped or
dropped; the input side also drops read-only properties, Microsoft
Graph's navigation properties, which no request body accepts, and OData
control annotations (@odata.type)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

MAX_DEPTH = 6

_KEEP = (
    "type", "format", "enum", "const", "description", "default", "minimum",
    "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
    "pattern", "minItems", "maxItems", "uniqueItems", "multipleOf",
    "minProperties", "maxProperties", "deprecated",
)  # fmt: skip
_NUMERIC = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf")
_MAX_EXAMPLE_CHARS = 200
# OData control annotations (@odata.type, ...). Microsoft Graph marks
# @odata.type required on every entity, but a request names it only to pick
# a derived type, and no model provider accepts "@" in an input name, so the
# input side drops them.
ODATA = "@odata."

OPAQUE = object()  # a schema position the generator cannot see into


class ConvError(ValueError):
    pass


def _number(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _typed(value: Any, type_: Any) -> Any:
    """Discovery writes defaults as strings: "100", "false"."""
    if not isinstance(value, str):
        return value
    if type_ == "boolean" and value in ("true", "false"):
        return value == "true"
    if type_ in ("integer", "number"):
        return _number(value)
    return value


class Converter:
    """Converts one spec's schemas. resolve maps a $ref to its target."""

    def __init__(self, resolve: Callable[[str], Any], *, input_side: bool):
        self.resolve = resolve
        self.input_side = input_side

    def convert(self, node: Any, depth: int = 0, stack: tuple[str, ...] = ()) -> dict:
        if not isinstance(node, dict):
            return {}
        if "$ref" in node:
            return self._ref(node, depth, stack)
        if "allOf" in node:
            return self._all_of(node, depth, stack)
        for key in ("anyOf", "oneOf"):
            if key in node:
                return self._any_of(node, key, depth, stack)
        if node.get("type") == "file":
            raise ConvError("type file (an upload) cannot be a JSON tool input")
        out: dict = {}
        for key in _KEEP:
            if key not in node:
                continue
            value = node[key]
            if key in _NUMERIC:
                value = self._bound(node, key)
                if value is None:
                    continue
            elif key == "default":
                value = _typed(value, node.get("type"))
                if value is None:
                    continue
            elif key == "type" and value == "any":
                continue  # Discovery's "any": no constraint
            out[key] = value
        if "exclusiveMinimum" in out and isinstance(out["exclusiveMinimum"], bool):
            self._exclusive(out, "Minimum")
        if "exclusiveMaximum" in out and isinstance(out["exclusiveMaximum"], bool):
            self._exclusive(out, "Maximum")
        example = node.get("example")
        if isinstance(example, (str, int, float, bool)) and len(str(example)) <= _MAX_EXAMPLE_CHARS:
            out["examples"] = [example]
        self._object(node, out, depth, stack)
        if "items" in node:
            items = node["items"]
            if isinstance(items, dict):
                out["items"] = self._child(items, depth, stack)
            else:  # a tuple form 2020-12 spells prefixItems; keep only the type
                out.setdefault("type", "array")
        return out

    def _bound(self, node: dict, key: str) -> Any:
        value = node[key]
        if isinstance(value, bool):
            return value  # OpenAPI 3.0's boolean exclusive bounds; mapped below
        return _number(value)

    @staticmethod
    def _exclusive(out: dict, which: str) -> None:
        flag = out.pop("exclusive" + which)
        bound = out.get(which.lower())
        if flag and bound is not None:
            out["exclusive" + which] = out.pop(which.lower())

    def _object(self, node: dict, out: dict, depth: int, stack: tuple[str, ...]) -> None:
        props = node.get("properties")
        if isinstance(props, dict):
            kept = {}
            for name, sub in props.items():
                if self.input_side and (self._input_hidden(sub) or str(name).startswith(ODATA)):
                    continue
                kept[str(name)] = self._child(sub, depth, stack)
            out["properties"] = kept
            required = [r for r in node.get("required") or [] if isinstance(r, str) and r in kept]
            if required:
                out["required"] = required
        extra = node.get("additionalProperties")
        if isinstance(extra, bool):
            out["additionalProperties"] = extra
        elif isinstance(extra, dict):
            out["additionalProperties"] = self._child(extra, depth, stack)

    def _input_hidden(self, sub: Any) -> bool:
        if not isinstance(sub, dict):
            return False
        if sub.get("readOnly") is True or sub.get("x-ms-navigationProperty") is True:
            return True
        return False

    def _child(self, node: Any, depth: int, stack: tuple[str, ...]) -> dict:
        if depth + 1 > MAX_DEPTH:
            return self._collapsed(node, stack)
        return self.convert(node, depth + 1, stack)

    def _target(self, node: dict, stack: tuple[str, ...]) -> Any:
        ref = node.get("$ref")
        if not isinstance(ref, str):
            return node
        if ref in stack:
            return {}
        target = self.resolve(ref)
        if target is None:
            raise ConvError(f"$ref {ref} does not resolve in the spec")
        return self._target(target, stack + (ref,)) if isinstance(target, dict) else {}

    def _collapsed(self, node: Any, stack: tuple[str, ...]) -> dict:
        target = self._target(node, stack) if isinstance(node, dict) else {}
        type_ = target.get("type") if isinstance(target, dict) else None
        if not isinstance(type_, str) and isinstance(target, dict) and (
            "properties" in target or "allOf" in target
        ):
            type_ = "object"
        out = {"type": type_} if isinstance(type_, str) and type_ not in ("any", "file") else {}
        if isinstance(node, dict) and isinstance(node.get("description"), str):
            out["description"] = node["description"]
        return out

    def _ref(self, node: dict, depth: int, stack: tuple[str, ...]) -> dict:
        ref = node["$ref"]
        if not isinstance(ref, str):
            raise ConvError("a $ref is not a string")
        if ref in stack:
            return self._collapsed(node, stack)
        target = self.resolve(ref)
        if target is None:
            raise ConvError(f"$ref {ref} does not resolve in the spec")
        out = self.convert(target, depth, stack + (ref,))
        if isinstance(node.get("description"), str):
            out["description"] = node["description"]
        return out

    def _all_of(self, node: dict, depth: int, stack: tuple[str, ...]) -> dict:
        parts = [self.convert(p, depth, stack) for p in node["allOf"] if isinstance(p, dict)]
        rest = {k: v for k, v in node.items() if k != "allOf"}
        if rest:
            parts.append(self.convert(rest, depth, stack))
        if all(p.get("type", "object") == "object" and set(p) <= {
            "type", "properties", "required", "description", "additionalProperties"
        } for p in parts):
            merged: dict = {"type": "object", "properties": {}}
            required: list[str] = []
            for p in parts:
                merged["properties"].update(p.get("properties", {}))
                required += [r for r in p.get("required", []) if r not in required]
                if "description" in p:
                    merged["description"] = p["description"]
                if "additionalProperties" in p:
                    merged["additionalProperties"] = p["additionalProperties"]
            if required:
                merged["required"] = required
            return merged
        return {"allOf": parts}

    def _any_of(self, node: dict, key: str, depth: int, stack: tuple[str, ...]) -> dict:
        members = [m for m in node[key] if isinstance(m, dict) and not _null_member(m)]
        rest = {k: v for k, v in node.items() if k != key}
        if len(members) == 1:
            out = self.convert(members[0], depth, stack)
            if isinstance(rest.get("description"), str):
                out["description"] = rest["description"]
            return out
        out = self.convert(rest, depth, stack) if rest else {}
        out[key] = [self.convert(m, depth, stack) for m in members]
        return out


# The keys a member may hold and still say only "or null".
_NULL_ONLY_KEYS = {"type", "nullable", "description", "title"}


def _null_member(m: dict) -> bool:
    """OpenAPI's way of saying "or null": {type: object, nullable: true}, or
    {type: null}. A member that also says what a value is, such as Microsoft
    Graph's {type: number, format: double, nullable: true} ("a number, or
    null"), is a value branch and is kept (IR-CN15c-7)."""
    if m.get("type") == "null":
        return True
    return m.get("nullable") is True and m.get("type", "object") == "object" and set(m) <= _NULL_ONLY_KEYS


def infer(value: Any, depth: int = 0) -> dict:
    """A schema for an example value (Postman bodies carry no types)."""
    if depth > MAX_DEPTH:
        return {}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array", "items": infer(value[0], depth + 1)} if value else {"type": "array"}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {str(k): infer(v, depth + 1) for k, v in value.items()},
        }
    return {}


def at_path(schema: Any, dotted: str) -> Any:
    """The sub-schema at a dotted response path; OPAQUE where the schema
    cannot be seen into; None when the path is not in it. An array is
    stepped through, since a projection applies to each element."""
    node = schema
    for segment in dotted.split("."):
        while isinstance(node, dict) and node.get("type") == "array" and "properties" not in node:
            if "items" not in node:
                return OPAQUE
            node = node["items"]
        if not isinstance(node, dict):
            return OPAQUE
        props = node.get("properties")
        if not isinstance(props, dict) or not props:
            return OPAQUE
        if segment not in props:
            # Declared properties are the contract a projection is checked
            # against, even where the vendor also allows extra members.
            return None
        node = props[segment]
    return node
