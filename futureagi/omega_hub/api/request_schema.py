"""Request bodies for the generated API contract (api_contracts/openapi/swagger.json), from omega_hub/openapi.yaml.

The Hub's views read their bodies by hand, so drf-yasg saw no request serializer and the platform's contract
coverage gate (frontend/scripts/check-management-api-contract-coverage.mjs) counted 35 Hub mutations without a
body schema. openapi.yaml already documents every Hub route and body, and test_openapi.py keeps it in step with
the URLConf, so `HubAutoSchema` (set on the Hub's base views) reads the body from there instead of repeating it
in serializers: JSON bodies become one `in: body` parameter, multipart bodies `in: formData` parameters, and a
POST that takes no body gets an empty object.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml
from drf_yasg import openapi

from tfc.utils.api_contracts import ManagementAPIAutoSchema

OPENAPI = Path(__file__).resolve().parents[1] / "openapi.yaml"
FORM = "multipart/form-data"
JSON = "application/json"
_PASSED = ("description", "format", "pattern", "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems")
_EXTRA = {"minLength": "min_length", "maxLength": "max_length", "minItems": "min_items", "maxItems": "max_items"}


@lru_cache(maxsize=1)
def _spec() -> dict:
    return yaml.safe_load(OPENAPI.read_text())


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _documented_path(path: str) -> str:
    """drf-yasg's path names URL kwargs as written (`{submission_id}`); openapi.yaml camel-cases them."""
    return re.sub(r"\{(\w+)\}", lambda m: "{" + _camel(m.group(1)) + "}", path)


def _resolve(node: dict) -> dict:
    while "$ref" in node:
        node = _spec()["components"]["schemas"][node["$ref"].rsplit("/", 1)[-1]]
    return node


def _type_of(node: dict) -> str:
    if node.get("type"):
        return node["type"]
    values = node.get("enum") or ([node["const"]] if "const" in node else [])
    if values and all(isinstance(v, bool) for v in values):
        return openapi.TYPE_BOOLEAN
    if values and all(isinstance(v, int) for v in values):
        return openapi.TYPE_INTEGER
    if "properties" in node or "additionalProperties" in node:
        return openapi.TYPE_OBJECT
    return openapi.TYPE_STRING


def to_schema(node: dict) -> openapi.Schema:
    """An OpenAPI 3 schema from openapi.yaml as a Swagger 2 schema (no oneOf/anyOf/nullable: none are used)."""
    node = _resolve(node)
    kind = _type_of(node)
    kwargs: dict = {_EXTRA.get(key, key): node[key] for key in _PASSED if key in node}
    if "enum" in node or "const" in node:
        kwargs["enum"] = node.get("enum") or [node["const"]]
    if kind == openapi.TYPE_OBJECT:
        kwargs["properties"] = {name: to_schema(child) for name, child in (node.get("properties") or {}).items()}
        if node.get("required"):
            kwargs["required"] = list(node["required"])
        if isinstance(node.get("additionalProperties"), dict):
            kwargs["additional_properties"] = to_schema(node["additionalProperties"])
    if kind == openapi.TYPE_ARRAY:
        kwargs["items"] = to_schema(node.get("items") or {"type": "string"})
    return openapi.Schema(type=kind, **kwargs)


def request_body(path: str, method: str) -> tuple[str, dict | None] | None:
    """(media type, schema) for a documented Hub operation; (JSON, None) when it takes no body; None when
    openapi.yaml does not document the operation at all."""
    operation = (_spec()["paths"].get(_documented_path(path)) or {}).get(method.lower())
    if operation is None:
        return None
    content = (operation.get("requestBody") or {}).get("content") or {}
    if FORM in content:
        return FORM, content[FORM].get("schema") or {}
    if JSON in content:
        return JSON, content[JSON].get("schema") or {}
    return JSON, None


def _form_parameters(schema: dict) -> list[openapi.Parameter]:
    schema = _resolve(schema)
    required = set(schema.get("required") or [])
    parameters = []
    for name, child in (schema.get("properties") or {}).items():
        child = _resolve(child)
        binary = child.get("format") == "binary"
        extra = {"enum": child["enum"]} if "enum" in child else {}
        if "maxLength" in child:
            extra["max_length"] = child["maxLength"]
        parameters.append(openapi.Parameter(name, openapi.IN_FORM, required=name in required,
                                            type=openapi.TYPE_FILE if binary else _type_of(child), **extra))
    return parameters


class HubAutoSchema(ManagementAPIAutoSchema):
    """Takes POST/PATCH/PUT request bodies from openapi.yaml; responses and everything else stay the platform's
    (ManagementAPIAutoSchema, SWAGGER_SETTINGS' default, adds the shared error response)."""

    def _documented(self):
        if self.method.lower() not in ("post", "patch", "put"):
            return None
        return request_body(self.path, self.method)

    def get_consumes(self):
        documented = self._documented()
        return [documented[0]] if documented else super().get_consumes()

    def get_request_body_parameters(self, consumes):
        documented = self._documented()
        if documented is None:
            return super().get_request_body_parameters(consumes)
        media_type, schema = documented
        if media_type == FORM:
            return _form_parameters(schema)
        body = to_schema(schema) if schema is not None else openapi.Schema(
            type=openapi.TYPE_OBJECT, properties={}, description="No body: send nothing, or {}.")
        return [openapi.Parameter("data", openapi.IN_BODY, required=schema is not None, schema=body)]
