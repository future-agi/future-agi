"""Generate MCP tool definitions from the committed OpenAPI contract.

The catalog decides which API operations are public MCP tools. The OpenAPI
contract remains the source of truth for their request shapes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
SUPPORTED_PARAMETER_LOCATIONS = {"path", "query", "body"}
PATH_PARAMETER_PATTERN = re.compile(r"{([^}]+)}")


class ToolGenerationError(ValueError):
    """Raised when the curated catalog cannot be generated safely."""


def _load_document(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        if path.suffix in {".yaml", ".yml"}:
            document = yaml.safe_load(handle)
        else:
            document = json.load(handle)
    if not isinstance(document, dict):
        raise ToolGenerationError(f"Expected an object in {path}")
    return document


def _parameter_schema(parameter: dict[str, Any]) -> dict[str, Any]:
    if parameter.get("in") == "body":
        schema = copy.deepcopy(parameter.get("schema") or {})
    else:
        schema = {
            key: copy.deepcopy(parameter[key])
            for key in (
                "type",
                "format",
                "enum",
                "default",
                "items",
                "minimum",
                "maximum",
                "minLength",
                "maxLength",
                "pattern",
                "x-nullable",
            )
            if key in parameter
        }
    if parameter.get("description") and "description" not in schema:
        schema["description"] = parameter["description"]
    return schema


def _json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Translate Swagger extensions without changing the accepted request values."""
    result = copy.deepcopy(schema)
    if result.pop("x-json-value", False):
        return {
            key: value
            for key, value in result.items()
            if key in {"description", "title", "default"}
        }
    for extension, kind in (
        ("x-string-or-object", "object"),
        ("x-string-or-array", "array"),
    ):
        if result.pop(extension, False):
            result.pop("type", None)
            result["anyOf"] = [{"type": "string"}, {"type": kind}]
    for key in ("properties", "definitions", "patternProperties"):
        if key in result:
            result[key] = {
                name: _json_schema(child) for name, child in result[key].items()
            }
    for key in ("items", "additionalProperties", "not"):
        if isinstance(result.get(key), dict):
            result[key] = _json_schema(result[key])
    for key in ("allOf", "anyOf", "oneOf"):
        if key in result:
            result[key] = [_json_schema(child) for child in result[key]]
    if result.pop("x-nullable", False):
        # A union also handles nullable references and enums: merely appending
        # null to `type` would still leave their other constraints rejecting it.
        result = {"anyOf": [result, {"type": "null"}]}
    return result


def _resolve_parameter(
    parameter: dict[str, Any], contract: dict[str, Any]
) -> dict[str, Any]:
    ref = parameter.get("$ref")
    if not ref:
        return parameter
    prefix = "#/parameters/"
    if not ref.startswith(prefix):
        raise ToolGenerationError(f"Unsupported parameter reference: {ref}")
    name = ref.removeprefix(prefix)
    try:
        return copy.deepcopy(contract["parameters"][name])
    except KeyError as exc:
        raise ToolGenerationError(f"Missing parameter reference: {ref}") from exc


def _schema_with_definitions(
    schema: dict[str, Any], contract: dict[str, Any]
) -> dict[str, Any]:
    """Attach only the OpenAPI definitions reachable from a request schema."""

    definitions = contract.get("definitions", {})
    reachable: dict[str, Any] = {}

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref")
            prefix = "#/definitions/"
            if isinstance(ref, str) and ref.startswith(prefix):
                name = ref.removeprefix(prefix)
                if name not in definitions:
                    raise ToolGenerationError(f"Missing schema reference: {ref}")
                if name not in reachable:
                    reachable[name] = copy.deepcopy(definitions[name])
                    collect(reachable[name])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    result = copy.deepcopy(schema)
    collect(result)
    if reachable:
        result["definitions"] = dict(sorted(reachable.items()))
    return result


def _dereference_root_schema(
    schema: dict[str, Any], contract: dict[str, Any]
) -> dict[str, Any]:
    ref = schema.get("$ref")
    prefix = "#/definitions/"
    if not isinstance(ref, str) or not ref.startswith(prefix):
        return copy.deepcopy(schema)
    name = ref.removeprefix(prefix)
    try:
        return copy.deepcopy(contract["definitions"][name])
    except KeyError as exc:
        raise ToolGenerationError(f"Missing schema reference: {ref}") from exc


def _operation_parameters(
    path_item: dict[str, Any], operation: dict[str, Any], contract: dict[str, Any]
) -> list[dict[str, Any]]:
    parameters: list[dict[str, Any]] = []
    positions: dict[tuple[str, str], int] = {}
    for raw_parameter in [
        *(path_item.get("parameters") or []),
        *(operation.get("parameters") or []),
    ]:
        parameter = _resolve_parameter(raw_parameter, contract)
        location = parameter.get("in")
        name = parameter.get("name")
        if location not in SUPPORTED_PARAMETER_LOCATIONS:
            raise ToolGenerationError(
                f"Unsupported parameter location {location!r} for {name!r}"
            )
        key = (str(location), str(name))
        if key in positions:
            parameters[positions[key]] = parameter
        else:
            positions[key] = len(parameters)
            parameters.append(parameter)
    return parameters


def _build_input_schema(
    parameters: list[dict[str, Any]],
    contract: dict[str, Any],
    *,
    method: str = "",
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    request_mapping = {"path": [], "query": [], "body": []}

    def add_property(name: str, schema: dict[str, Any], is_required: bool) -> None:
        if name in properties:
            raise ToolGenerationError(f"Conflicting request field: {name}")
        if schema.get("readOnly"):
            return
        properties[name] = schema
        if is_required:
            required.append(name)

    for parameter in parameters:
        location = parameter["in"]
        if location != "body":
            name = parameter["name"]
            add_property(
                name, _parameter_schema(parameter), parameter.get("required", False)
            )
            request_mapping[location].append(name)
            continue

        body_schema = _dereference_root_schema(_parameter_schema(parameter), contract)
        if body_schema.get("type") == "object" or "properties" in body_schema:
            # PATCH is partial: Django `partial_update` does not require the
            # create serializer's fields. Copying `required` onto MCP tools
            # would reject valid updates such as `{id, description}`.
            body_required = (
                set() if method == "patch" else set(body_schema.get("required") or [])
            )
            for name, field_schema in (body_schema.get("properties") or {}).items():
                if field_schema.get("readOnly"):
                    continue
                add_property(name, copy.deepcopy(field_schema), name in body_required)
                request_mapping["body"].append(name)
        else:
            name = parameter.get("name") or "body"
            add_property(name, body_schema, parameter.get("required", False))
            request_mapping["body"].append(name)

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        input_schema["required"] = required
    return (
        _json_schema(_schema_with_definitions(input_schema, contract)),
        request_mapping,
    )


def _annotations(method: str, access: str) -> dict[str, bool]:
    return {
        "readOnlyHint": access == "read",
        "destructiveHint": access == "destructive",
        "idempotentHint": method in {"get", "put", "patch", "delete"},
        # Closed by default: most operations only touch this deployment. Tools
        # that reach an external provider declare `open_world: true` in the
        # catalog, because only a human reviewer knows where a view ends up.
        "openWorldHint": False,
    }


def generate_tool_manifest(contract_path: Path, catalog_path: Path) -> dict[str, Any]:
    contract = _load_document(contract_path)
    catalog = _load_document(catalog_path)
    if contract.get("swagger") != "2.0":
        raise ToolGenerationError(
            "Only the repository's Swagger 2.0 contract is supported"
        )

    catalog_tools = catalog.get("tools")
    if not isinstance(catalog_tools, list):
        raise ToolGenerationError("Catalog must contain a tools list")
    expected_count = catalog.get("expected_tool_count")
    if expected_count is not None and expected_count != len(catalog_tools):
        raise ToolGenerationError(
            f"Catalog expected {expected_count} tools but contains {len(catalog_tools)}"
        )

    generated_tools: list[dict[str, Any]] = []
    names: set[str] = set()
    for entry in catalog_tools:
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ToolGenerationError("Every catalog tool requires a name")
        if name in names:
            raise ToolGenerationError(f"Duplicate MCP tool name: {name}")
        names.add(name)

        operation_ref = entry.get("operation") or {}
        method = str(operation_ref.get("method", "")).lower()
        path = operation_ref.get("path")
        if method not in HTTP_METHODS or not isinstance(path, str):
            raise ToolGenerationError(f"Invalid operation reference for {name}")
        try:
            path_item = contract["paths"][path]
            operation = path_item[method]
        except KeyError as exc:
            raise ToolGenerationError(
                f"OpenAPI operation not found for {name}: {method.upper()} {path}"
            ) from exc

        description = (
            entry.get("description")
            or operation.get("description")
            or operation.get("summary")
        )
        if not description:
            raise ToolGenerationError(f"Tool {name} requires a description")
        access = entry.get("access", "read" if method == "get" else "write")
        if access not in {"read", "write", "destructive"}:
            raise ToolGenerationError(
                f"Invalid access classification for {name}: {access}"
            )

        parameters = _operation_parameters(path_item, operation, contract)
        input_schema, request_mapping = _build_input_schema(
            parameters, contract, method=method
        )
        annotations = _annotations(method, access)
        if "idempotent" in entry:
            if not isinstance(entry["idempotent"], bool):
                raise ToolGenerationError(f"Invalid idempotent hint for {name}")
            annotations["idempotentHint"] = entry["idempotent"]
        if "open_world" in entry:
            if not isinstance(entry["open_world"], bool):
                raise ToolGenerationError(f"Invalid open_world hint for {name}")
            annotations["openWorldHint"] = entry["open_world"]
        path_fields = set(PATH_PARAMETER_PATTERN.findall(path))
        mapped_path_fields = set(request_mapping["path"])
        if path_fields != mapped_path_fields:
            raise ToolGenerationError(
                f"Path parameter mismatch for {name}: expected "
                f"{sorted(path_fields)}, mapped {sorted(mapped_path_fields)}"
            )
        generated_tools.append(
            {
                "name": name,
                "description": description,
                "group": entry["group"],
                "inputSchema": input_schema,
                "annotations": annotations,
                "request": {
                    "method": method.upper(),
                    "path": path,
                    "parameters": request_mapping,
                },
                "response": entry.get("response") or {"unwrap": "result"},
                "source": {"operationId": operation.get("operationId")},
            }
        )

    contract_bytes = contract_path.read_bytes()
    return {
        "schema_version": 1,
        "source_contract": str(contract_path.name),
        "source_contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
        "tool_count": len(generated_tools),
        "tools": generated_tools,
    }


def write_tool_manifest(manifest: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
