"""Request-body projection: an overlay keeps only the body fields a tool
needs, by dotted path, so a vendor's large entity (a Microsoft Graph
message or event, a Gmail draft) becomes the few fields a model should set.

A path names a property of the body, or of an object inside it, such as
`Message.subject`. A path through an array steps into its items, as the
response projection does. Every object on the way keeps only the listed
children and refuses any other (`additionalProperties: false`), so the
tool's schema is exactly the projection."""

from __future__ import annotations

from typing import Any


class _Leaf:
    """A path ends here: keep the whole sub-schema."""


LEAF = _Leaf()


def _tree(paths: list[str], fail) -> dict:
    tree: dict = {}
    for path in paths:
        node = tree
        parts = path.split(".")
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            here = node.get(part)
            if here is LEAF or (last and here is not None):
                fail(f"body.fields lists {path} and also a field that contains it or is inside it")
                break
            if last:
                node[part] = LEAF
            else:
                node = node.setdefault(part, {})
    return tree


def _object(schema: Any) -> dict | None:
    """The object schema a path continues into, stepping through arrays."""
    node = schema
    while isinstance(node, dict) and node.get("type") == "array" and "properties" not in node:
        node = node.get("items")
    if isinstance(node, dict) and isinstance(node.get("properties"), dict) and node["properties"]:
        return node
    return None


def _prune(schema: dict, tree: dict, prefix: str, fail) -> dict:
    if schema.get("type") == "array" and "properties" not in schema and isinstance(schema.get("items"), dict):
        out = dict(schema)
        out["items"] = _prune(schema["items"], tree, prefix, fail)
        return out
    obj = _object(schema)
    if obj is None:
        fail(f"body.fields: {prefix[:-1] or 'the request body'} is not an object with declared properties")
        return schema
    props = obj["properties"]
    kept: dict[str, Any] = {}
    for name, sub in tree.items():
        if name not in props:
            listed = ", ".join(sorted(props)[:30])
            fail(f"body.fields names {prefix}{name}, which the request body does not declare (it has: {listed})")
            continue
        kept[name] = props[name] if sub is LEAF else _prune(props[name], sub, f"{prefix}{name}.", fail)
    required = obj.get("required") or []
    missing = [r for r in required if r not in kept]
    if missing:
        fail(f"body.fields leaves out {', '.join(prefix + m for m in missing)}, which the request body requires")
    out = {k: v for k, v in obj.items() if k not in ("properties", "required", "additionalProperties")}
    out["properties"] = kept
    if [r for r in required if r in kept]:
        out["required"] = [r for r in required if r in kept]
    out["additionalProperties"] = False
    return out


def project(schema: dict, paths: list[str], fail) -> dict:
    """The body schema keeping only paths; problems go to fail."""
    return _prune(schema, _tree(paths, fail), "", fail)


def require(schema: dict, paths: list[str], fail) -> None:
    """Mark each dotted path required in its parent object, in place."""
    for path in paths:
        *parents, leaf = path.split(".")
        node: Any = schema
        for part in parents:
            obj = _object(node)
            node = obj["properties"].get(part) if obj else None
        obj = _object(node)
        if obj is None or leaf not in obj["properties"]:
            fail(f"body.required names {path}, which is not a field of the request body")
            continue
        req = obj.setdefault("required", [])
        if leaf not in req:
            req.append(leaf)
