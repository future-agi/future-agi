"""Runtime registry for OpenAPI-generated MCP tools."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from mcp.types import Tool, ToolAnnotations

DEFAULT_MANIFEST_PATH = Path(__file__).with_name("catalog") / "tools.generated.json"


class GeneratedToolRegistryError(ValueError):
    """Raised when the generated tool manifest is invalid."""


@dataclass(frozen=True)
class GeneratedTool:
    name: str
    description: str
    group: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any]
    request: dict[str, Any]
    response: dict[str, Any]
    source: dict[str, Any]

    @cached_property
    def validator(self) -> Draft7Validator:
        return Draft7Validator(self.input_schema)

    def is_available(self) -> bool:
        """Only expose operations mounted in this deployment's URL configuration."""
        from django.urls import Resolver404, resolve

        def placeholder(match: re.Match) -> str:
            schema = self.input_schema["properties"][match.group(1)]
            return (
                "1"
                if schema.get("type") == "integer"
                else "00000000-0000-0000-0000-000000000001"
            )

        path = re.sub(r"{([^}]+)}", placeholder, self.request["path"])
        try:
            resolve(path)
        except Resolver404:
            return False
        return True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GeneratedTool:
        required = {
            "name",
            "description",
            "group",
            "inputSchema",
            "annotations",
            "request",
            "response",
            "source",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise GeneratedToolRegistryError(
                f"Generated tool is missing fields: {', '.join(missing)}"
            )
        return cls(
            name=value["name"],
            description=value["description"],
            group=value["group"],
            input_schema=value["inputSchema"],
            annotations=value["annotations"],
            request=value["request"],
            response=value["response"],
            source=value["source"],
        )

    def to_mcp_tool(self) -> Tool:
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema=self.input_schema,
            annotations=ToolAnnotations.model_validate(self.annotations),
        )

    def to_discovery_dict(self) -> dict[str, Any]:
        required = set(self.input_schema.get("required") or [])
        return {
            "name": self.name,
            "description": self.description,
            "category": self.group,
            "input_schema": self.input_schema,
            "parameters": [
                {
                    "name": name,
                    "type": schema.get("type", "object"),
                    "description": schema.get("description", ""),
                    "required": name in required,
                }
                for name, schema in self.input_schema.get("properties", {}).items()
            ],
        }


class GeneratedToolRegistry:
    def __init__(self, tools: Iterable[GeneratedTool]):
        self._tools: dict[str, GeneratedTool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise GeneratedToolRegistryError(
                    f"Duplicate generated MCP tool: {tool.name}"
                )
            self._tools[tool.name] = tool

    @classmethod
    def from_manifest(cls, path: Path = DEFAULT_MANIFEST_PATH) -> GeneratedToolRegistry:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GeneratedToolRegistryError(
                f"Unable to load generated MCP manifest: {path}"
            ) from exc
        raw_tools = manifest.get("tools")
        if not isinstance(raw_tools, list):
            raise GeneratedToolRegistryError("Generated manifest must contain tools")
        declared_count = manifest.get("tool_count")
        if declared_count != len(raw_tools):
            raise GeneratedToolRegistryError(
                f"Manifest declares {declared_count} tools but contains {len(raw_tools)}"
            )
        return cls(GeneratedTool.from_dict(value) for value in raw_tools)

    def get(self, name: str) -> GeneratedTool | None:
        return self._tools.get(name)

    def list_all(self) -> list[GeneratedTool]:
        return list(self._tools.values())

    def list_by_groups(self, groups: Iterable[str]) -> list[GeneratedTool]:
        enabled = set(groups)
        return [tool for tool in self._tools.values() if tool.group in enabled]

    def count(self) -> int:
        return len(self._tools)


registry = GeneratedToolRegistry.from_manifest()
