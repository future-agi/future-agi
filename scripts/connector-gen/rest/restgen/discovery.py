"""Google API Discovery reader (google-discovery), for Gmail and Calendar.

The server is rootUrl + servicePath; a method's path is relative to it.
Global parameters (key, oauth_token, access_token, alt, fields, ...) are
API-level, never method parameters, so they are never tool inputs."""

from __future__ import annotations

from typing import Any

from .model import Body, Operation, OpRef, Param, SpecReader, normalize_server
from .schemaconv import Converter
from .reserved import expand_resource, resource_pattern

_SCHEMA_KEYS = (
    "type", "format", "enum", "default", "minimum", "maximum", "pattern",
    "items", "properties", "additionalProperties", "$ref", "description",
)  # fmt: skip


class DiscoveryReader(SpecReader):
    format = "google-discovery"

    def __init__(self, doc: dict):
        self.doc = doc
        schemas = doc.get("schemas") or {}
        resolve = lambda ref: schemas.get(ref)  # noqa: E731 - Discovery refs are names
        self.inputs = Converter(resolve, input_side=True)
        self.outputs = Converter(resolve, input_side=False)

    def servers(self) -> list[str]:
        root, service = self.doc.get("rootUrl"), self.doc.get("servicePath") or ""
        if not isinstance(root, str) or not root.startswith("https://"):
            return []
        return [normalize_server(root + service)]

    def refs(self) -> list[OpRef]:
        out: list[OpRef] = []
        self._walk(self.doc, out)
        return out

    def _walk(self, node: dict, out: list[OpRef]) -> None:
        for method in (node.get("methods") or {}).values():
            if isinstance(method, dict) and isinstance(method.get("path"), str):
                out.append(
                    OpRef(
                        str(method.get("httpMethod", "")).upper(),
                        "/" + method["path"],
                        {"id": str(method.get("id", ""))},
                        method,
                    )
                )
        for resource in (node.get("resources") or {}).values():
            if isinstance(resource, dict):
                self._walk(resource, out)

    def operation(self, ref: OpRef) -> Operation:
        m = ref.raw
        params = [self._param(name, p) for name, p in (m.get("parameters") or {}).items()]
        body = None
        if isinstance(m.get("request"), dict):
            # Discovery says nothing on whether a body is optional; a method
            # that declares one is called with one.
            body = Body(True, "", self.inputs.convert(m["request"]))
        scopes = [s for s in m.get("scopes") or [] if isinstance(s, str)] or None
        response = m.get("response")
        op = Operation(
            method=ref.method,
            path=ref.path,
            label=ref.ids["id"] or f"{ref.method} {ref.path}",
            params=params,
            body=body,
            accepted_scopes=scopes,
            response_schema=lambda: self.outputs.convert(response) if isinstance(response, dict) else None,
            servers=self.servers(),
        )
        if "{+" in ref.path:
            for p in op.params:
                if "{+" + p.name + "}" in ref.path:
                    try:
                        resource_pattern(p.schema)
                        if p.location != "path":
                            raise ValueError("reserved expansion must be a path parameter")
                        for value in p.schema.get("enum", []):
                            expand_resource(value, p.schema)
                        if "default" in p.schema:
                            expand_resource(p.schema["default"], p.schema)
                    except ValueError as exc:
                        p.unsupported = str(exc)
        return op

    def _param(self, name: str, p: Any) -> Param:
        if not isinstance(p, dict):
            return Param(name, "query", False, "", {}, "the parameter is not an object")
        where = str(p.get("location", "query"))
        param = Param(name, where, bool(p.get("required")) or where == "path", str(p.get("description") or "").strip(), {})
        if where not in ("path", "query"):
            param.unsupported = f"a {where} parameter cannot be a tool input"
            return param
        schema = self.inputs.convert({k: p[k] for k in _SCHEMA_KEYS if k in p and k != "description"})
        if p.get("repeated") is True:
            schema = {"type": "array", "items": schema}
        param.schema = schema
        return param
