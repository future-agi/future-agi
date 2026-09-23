"""Expose the OpenAPI-generated MCP tool catalog to in-process AI callers.

Falcon and the tool discovery endpoint read tools from ``ai_tools.registry``.
Each catalog entry in ``mcp_server`` is wrapped as a ``BaseTool`` here so those
callers get exactly the same tools, schemas, and Django API execution path as
external MCP clients, without an HTTP round-trip.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel as PydanticBaseModel

from ai_tools import error_codes
from ai_tools.base import BaseTool, EmptyInput, ToolContext, ToolResult
from ai_tools.registry import registry as tool_registry
from mcp_server.api_executor import (
    APIExecutionError,
    DjangoAPIExecutor,
    MCPRequestContext,
    load_urlconf_outside_workspace,
)
from mcp_server.generated_registry import GeneratedTool, GeneratedToolRegistry
from mcp_server.generated_registry import registry as generated_registry

logger = logging.getLogger(__name__)

# Catalog groups are named after product areas; Falcon's mode routing uses the
# older category vocabulary for the areas that predate the catalog.
GROUP_TO_CATEGORY = {"observability": "tracing"}

_STATUS_TO_ERROR_CODE = {
    400: error_codes.VALIDATION_ERROR,
    401: error_codes.PERMISSION_DENIED,
    403: error_codes.PERMISSION_DENIED,
    404: error_codes.NOT_FOUND,
    413: error_codes.VALIDATION_ERROR,
    422: error_codes.VALIDATION_ERROR,
    429: error_codes.RATE_LIMITED,
    504: error_codes.TIMEOUT_ERROR,
}


def category_for_group(group: str) -> str:
    return GROUP_TO_CATEGORY.get(group, group)


class GeneratedAPITool(BaseTool):
    """A catalog tool executed through ``DjangoAPIExecutor``.

    Validation uses the generated JSON schema directly rather than a Pydantic
    model, so the LLM sees the same contract as MCP clients.
    """

    input_model = EmptyInput

    def __init__(self, generated: GeneratedTool, executor: DjangoAPIExecutor):
        self.generated = generated
        self._executor = executor
        self.name = generated.name
        self.description = generated.description
        self.category = category_for_group(generated.group)

    @property
    def input_schema(self) -> dict:
        return self.generated.input_schema

    @property
    def annotations(self) -> dict:
        return self.generated.annotations

    @property
    def is_read_only(self) -> bool:
        return bool(self.generated.annotations.get("readOnlyHint"))

    def run(self, raw_params: dict | None, context: ToolContext) -> ToolResult:
        return self._invoke(self._coerce_params(raw_params or {}), context)

    def execute(
        self, params: PydanticBaseModel | dict, context: ToolContext
    ) -> ToolResult:
        if isinstance(params, PydanticBaseModel):
            params = params.model_dump(exclude_none=True)
        return self._invoke(dict(params or {}), context)

    def _invoke(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        _ensure_urlconf_loaded_once()
        request_context = MCPRequestContext(
            user=context.user,
            organization=context.organization,
            workspace=context.workspace,
        )
        try:
            result = self._executor.execute_sync(
                self.generated, arguments, request_context
            )
        except APIExecutionError as exc:
            data = exc.data if isinstance(exc.data, dict) else None
            return ToolResult.error(
                str(exc),
                data=data,
                error_code=_STATUS_TO_ERROR_CODE.get(
                    exc.status_code, error_codes.INTERNAL_ERROR
                ),
            )
        except Exception as exc:
            from ai_tools.error_codes import code_from_exception

            logger.exception("Generated tool %s failed: %s", self.name, exc)
            return ToolResult.error(
                f"Tool execution failed: {exc}",
                error_code=code_from_exception(exc),
            )
        return ToolResult(
            content=render_result(result),
            data=result if isinstance(result, dict) else {"result": result},
        )

    def _coerce_params(self, raw_params: dict[str, Any]) -> dict[str, Any]:
        """Parse stringified JSON only where the schema expects a structure.

        LLMs often send ``"[1, 2]"`` for array fields. Free-text string fields
        that happen to start with a bracket must be left alone.
        """
        properties = self.generated.input_schema.get("properties", {})
        cleaned: dict[str, Any] = {}
        for key, value in raw_params.items():
            if isinstance(value, str) and _expects_structure(properties.get(key)):
                stripped = value.strip()
                if stripped.startswith(("[", "{")):
                    try:
                        cleaned[key] = json.loads(stripped)
                        continue
                    except ValueError:
                        pass
            cleaned[key] = value
        return cleaned

    def to_dict(self) -> dict:
        payload = super().to_dict()
        payload["annotations"] = self.generated.annotations
        return payload


_urlconf_loaded = False


def _ensure_urlconf_loaded_once() -> None:
    """Import view modules with no tenant bound before the first API call.

    Falcon binds the caller's workspace around every tool call. If that were
    the first URLconf import in the process, class-level querysets would be
    pinned to that tenant (see ``mcp_server.api_executor.ensure_urlconf_loaded``).
    """
    global _urlconf_loaded
    if _urlconf_loaded:
        return
    load_urlconf_outside_workspace()
    _urlconf_loaded = True


def _expects_structure(schema: dict | None) -> bool:
    if not isinstance(schema, dict):
        return False
    types = schema.get("type")
    if isinstance(types, str):
        types = [types]
    if types and any(t in ("object", "array") for t in types):
        return True
    for variant in schema.get("anyOf", []) or schema.get("oneOf", []) or []:
        if _expects_structure(variant):
            return True
    return False


def render_result(result: Any) -> str:
    """Serialize an API result for the model. JSON keeps field names exact."""
    if result is None:
        return "Done."
    return json.dumps(result, ensure_ascii=False, default=str)


def register_generated_tools(
    target=tool_registry,
    source: GeneratedToolRegistry = generated_registry,
    executor: DjangoAPIExecutor | None = None,
) -> list[GeneratedAPITool]:
    """Register every catalog tool in ``target``. Safe to call more than once."""
    executor = executor or DjangoAPIExecutor()
    registered: list[GeneratedAPITool] = []
    for generated in source.list_all():
        tool = GeneratedAPITool(generated, executor)
        target.register(tool)
        registered.append(tool)
    logger.debug("Registered %d generated API tools", len(registered))
    return registered
