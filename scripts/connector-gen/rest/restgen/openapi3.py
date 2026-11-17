"""OpenAPI 3.x reader (openapi-3), for example Microsoft Graph's
descriptions."""

from __future__ import annotations

import re
from typing import Any

from .model import Body, Operation, OpRef, Param, SpecReader, normalize_server
from .oashelpers import (
    METHODS,
    deref,
    describe,
    merged_params,
    oauth_scopes,
    pointer,
    success_response,
)
from .schemaconv import Converter

_JSON = re.compile(r"^application/([a-z0-9.+-]+\+)?json(\s*;.*)?$", re.I)
COMMA_NOTE = "Comma-separated values."


def json_media(content: Any) -> tuple[Any, list[str]]:
    """The JSON media type object of a content map, and every type listed."""
    if not isinstance(content, dict):
        return None, []
    types = [str(t) for t in content]
    for media_type, media in content.items():
        if _JSON.match(str(media_type)):
            return media, types
    return None, types


class OpenAPI3Reader(SpecReader):
    format = "openapi-3"

    def __init__(self, doc: dict):
        self.doc = doc
        resolve = lambda ref: pointer(doc, ref)  # noqa: E731
        self.inputs = Converter(resolve, input_side=True)
        self.outputs = Converter(resolve, input_side=False)
        schemes = (doc.get("components") or {}).get("securitySchemes") or {}
        self.schemes = {k: v for k, v in schemes.items() if isinstance(v, dict)}

    def _servers(self, servers: Any) -> list[str]:
        out = []
        for s in servers or []:
            if not isinstance(s, dict) or not isinstance(s.get("url"), str):
                continue
            url = s["url"]
            for name, var in (s.get("variables") or {}).items():
                if isinstance(var, dict) and isinstance(var.get("default"), str):
                    url = url.replace("{" + name + "}", var["default"])
            out.append(normalize_server(url))
        return out

    def servers(self) -> list[str]:
        return self._servers(self.doc.get("servers"))

    def credential_params(self) -> set[str]:
        return {
            s["name"]
            for s in self.schemes.values()
            if s.get("type") == "apiKey" and isinstance(s.get("name"), str)
        }

    def refs(self) -> list[OpRef]:
        out = []
        for path, item in (self.doc.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            for method in METHODS:
                op = item.get(method)
                if isinstance(op, dict):
                    ids = {}
                    if isinstance(op.get("operationId"), str):
                        ids["operationId"] = op["operationId"]
                    out.append(OpRef(method.upper(), str(path), ids, (item, op)))
        return out

    def operation(self, ref: OpRef) -> Operation:
        item, op = ref.raw
        params = [self._param(p) for p in merged_params(self.doc, item, op)]
        oauth = {k for k, v in self.schemes.items() if v.get("type") in ("oauth2", "openIdConnect")}
        security = op.get("security", self.doc.get("security"))
        servers = self._servers(op.get("servers") or item.get("servers")) or self.servers()
        return Operation(
            method=ref.method,
            path=ref.path,
            label=f"{ref.method} {ref.path}",
            params=params,
            body=self._body(op.get("requestBody")),
            accepted_scopes=oauth_scopes(security, oauth),
            response_schema=lambda: self._response(op.get("responses")),
            servers=servers,
        )

    def _param(self, p: dict) -> Param:
        name, where = p["name"], str(p.get("in"))
        param = Param(name, where, bool(p.get("required")) or where == "path", describe(p), {})
        if where in ("header", "cookie"):
            param.unsupported = f"a {where} parameter cannot be a tool input"
            return param
        if "schema" not in p:
            param.unsupported = "the parameter has no schema (it uses content)"
            return param
        schema = self.inputs.convert(p["schema"])
        target = deref(self.doc, p["schema"])
        type_ = schema.get("type") or (target.get("type") if isinstance(target, dict) else None)
        if where == "query" and type_ == "object":
            param.unsupported = "an object in the query string cannot be a tool input"
        elif where == "query" and type_ == "array" and p.get("explode") is False:
            # form style, explode false: one comma-separated value.
            schema = {"type": "string"}
            param.note = COMMA_NOTE
        example = p.get("example")
        if isinstance(example, (str, int, float)) and "examples" not in schema:
            schema["examples"] = [example]
        param.schema = schema
        return param

    def _body(self, raw: Any) -> Body | None:
        body = deref(self.doc, raw)
        if not isinstance(body, dict):
            return None
        media, types = json_media(body.get("content"))
        out = Body(bool(body.get("required")), describe(body), {})
        if media is None:
            out.unsupported = f"the request body is {', '.join(types) or 'untyped'}, not JSON"
        elif isinstance(media, dict) and "schema" in media:
            out.schema = self.inputs.convert(media["schema"])
        return out

    def _response(self, responses: Any) -> dict | None:
        resp = deref(self.doc, success_response(responses))
        if not isinstance(resp, dict):
            return None
        media, _ = json_media(resp.get("content"))
        if not isinstance(media, dict) or "schema" not in media:
            return None
        return self.outputs.convert(media["schema"])
