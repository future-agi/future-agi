"""Shared by the OpenAPI 3.x and Swagger 2.0 readers: local references,
parameter merging, scopes from security requirements, and the success
response."""

from __future__ import annotations

from typing import Any

METHODS = ("get", "post", "put", "patch", "delete")


def pointer(doc: Any, ref: str) -> Any:
    """The target of a local reference such as #/components/schemas/x, or
    None. Remote references are never followed: a spec stands alone."""
    if not ref.startswith("#/"):
        return None
    node = doc
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            return None
    return node


def deref(doc: Any, node: Any, depth: int = 0) -> Any:
    """Follow a chain of $ref objects (parameters, bodies, responses)."""
    while isinstance(node, dict) and isinstance(node.get("$ref"), str) and depth < 16:
        node = pointer(doc, node["$ref"])
        depth += 1
    return node


def merged_params(doc: Any, path_item: dict, op: dict) -> list[dict]:
    """Path-level parameters with the operation's own, the operation winning
    for the same (name, in)."""
    out: dict[tuple[str, str], dict] = {}
    for source in (path_item.get("parameters") or [], op.get("parameters") or []):
        for raw in source:
            p = deref(doc, raw)
            if isinstance(p, dict) and isinstance(p.get("name"), str):
                out[(p["name"], str(p.get("in")))] = p
    return list(out.values())


def oauth_scopes(requirements: Any, oauth_schemes: set[str]) -> list[str] | None:
    """Scopes listed by the security requirements on oauth2 schemes, or None
    when the spec lists none (Microsoft Graph's modules say `azureaadv2: []`)."""
    if not isinstance(requirements, list):
        return None
    scopes: list[str] = []
    for req in requirements:
        if not isinstance(req, dict):
            continue
        for name, listed in req.items():
            if name in oauth_schemes and isinstance(listed, list):
                scopes += [s for s in listed if isinstance(s, str) and s not in scopes]
    return scopes or None


def success_response(responses: Any) -> Any:
    """The operation's success response: 200, 201, 202, 2XX, then any 2xx."""
    if not isinstance(responses, dict):
        return None
    by_code = {str(code): value for code, value in responses.items()}  # YAML may give ints
    for code in ("200", "201", "202", "2XX", "2xx"):
        if code in by_code:
            return by_code[code]
    for code, value in by_code.items():
        if code.startswith("2"):
            return value
    return None


def describe(node: Any) -> str:
    value = node.get("description") if isinstance(node, dict) else None
    return value.strip() if isinstance(value, str) else ""
