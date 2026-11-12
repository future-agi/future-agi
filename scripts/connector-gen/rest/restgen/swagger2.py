"""Swagger 2.0 reader (swagger-2), for example Slack's Web API description."""

from __future__ import annotations

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
from .openapi3 import COMMA_NOTE
from .schemaconv import ConvError, Converter

# A non-body Swagger parameter carries its schema keywords inline.
_INLINE = (
    "type", "format", "items", "enum", "default", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "pattern",
    "minItems", "maxItems", "uniqueItems", "multipleOf",
)  # fmt: skip
_JSON = "application/json"


class Swagger2Reader(SpecReader):
    format = "swagger-2"

    def __init__(self, doc: dict):
        self.doc = doc
        resolve = lambda ref: pointer(doc, ref)  # noqa: E731
        self.inputs = Converter(resolve, input_side=True)
        self.outputs = Converter(resolve, input_side=False)
        defs = doc.get("securityDefinitions") or {}
        self.schemes = {k: v for k, v in defs.items() if isinstance(v, dict)}

    def servers(self) -> list[str]:
        host = self.doc.get("host")
        if not isinstance(host, str) or not host:
            return []
        schemes = self.doc.get("schemes") or ["https"]
        if "https" not in schemes:
            return []
        return [normalize_server(f"https://{host}{self.doc.get('basePath') or ''}")]

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
        raw_params = merged_params(self.doc, item, op)
        params, body, form = [], None, []
        for p in raw_params:
            where = str(p.get("in"))
            if where == "body":
                body = Body(bool(p.get("required")), describe(p), self.inputs.convert(p.get("schema")))
            elif where == "formData":
                form.append(p)
            else:
                params.append(self._param(p, where))
        if form:
            body = self._form_body(form, op.get("consumes") or self.doc.get("consumes") or [])
        oauth = {k for k, v in self.schemes.items() if v.get("type") == "oauth2"}
        return Operation(
            method=ref.method,
            path=ref.path,
            label=f"{ref.method} {ref.path}",
            params=params,
            body=body,
            accepted_scopes=oauth_scopes(op.get("security", self.doc.get("security")), oauth),
            response_schema=lambda: self._response(op.get("responses")),
            servers=self.servers(),
        )

    def _inline_schema(self, p: dict) -> dict:
        return self.inputs.convert({k: p[k] for k in _INLINE if k in p})

    def _param(self, p: dict, where: str) -> Param:
        param = Param(p["name"], where, bool(p.get("required")) or where == "path", describe(p), {})
        if where not in ("path", "query"):
            param.unsupported = f"a {where} parameter cannot be a tool input"
            return param
        if p.get("type") == "file":
            param.unsupported = "a file parameter cannot be a tool input"
            return param
        schema = self._inline_schema(p)
        if p.get("type") == "array" and p.get("collectionFormat", "csv") != "multi":
            schema = {"type": "string"}
            param.note = COMMA_NOTE if p.get("collectionFormat", "csv") == "csv" else (
                f"Values separated as Swagger collectionFormat {p['collectionFormat']}."
            )
        param.schema = schema
        return param

    def _form_body(self, form: list[dict], consumes: list) -> Body:
        """Form fields become one JSON body, when the operation accepts JSON."""
        props, required = {}, []
        body = Body(any(bool(p.get("required")) for p in form), "", {})
        if _JSON not in consumes:
            body.unsupported = (
                f"the operation takes form fields as {', '.join(map(str, consumes)) or 'a form'}, not JSON"
            )
            return body
        for p in form:
            if p.get("type") == "file":
                body.unsupported = f"form field {p['name']} is a file upload"
                return body
            try:
                schema = self._inline_schema(p)
            except ConvError as exc:
                body.unsupported = f"form field {p['name']}: {exc}"
                return body
            if describe(p):
                schema["description"] = describe(p)
            props[p["name"]] = schema
            if p.get("required"):
                required.append(p["name"])
        body.schema = {"type": "object", "properties": props}
        if required:
            body.schema["required"] = required
        return body

    def _response(self, responses: Any) -> dict | None:
        resp = deref(self.doc, success_response(responses))
        if not isinstance(resp, dict) or "schema" not in resp:
            return None
        return self.outputs.convert(resp["schema"])
