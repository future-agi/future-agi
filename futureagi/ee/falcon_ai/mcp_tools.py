"""Bridge between external MCP connector tools and the Falcon AI agent.

Creates BaseTool-compatible wrappers for tools discovered on external MCP
servers, so the agent can call them just like built-in tools.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel as PydanticBaseModel
from pydantic import create_model

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ee.falcon_ai.tool_scope import accessible_connectors, connector_for_execution

logger = logging.getLogger(__name__)


def _build_pydantic_model(name: str, json_schema: dict) -> type[PydanticBaseModel]:
    """Build a Pydantic model from a JSON Schema dict (MCP tool inputSchema)."""
    properties = json_schema.get("properties", {})
    required = set(json_schema.get("required", []))

    field_definitions = {}
    for field_name, field_spec in properties.items():
        field_type = _json_type_to_python(field_spec)
        if field_name in required:
            field_definitions[field_name] = (field_type, ...)
        else:
            # Optional fields accept None with a None default.
            field_definitions[field_name] = (
                field_type | None,
                field_spec.get("default", None),
            )

    if not field_definitions:
        # No properties — create an empty model
        return type(name, (PydanticBaseModel,), {})

    return create_model(name, **field_definitions)


def _json_type_to_python(spec: dict) -> type:
    """Map JSON Schema type to Python type."""
    t = spec.get("type", "string")
    if t == "integer":
        return int
    if t == "number":
        return float
    if t == "boolean":
        return bool
    if t == "array":
        return list
    if t == "object":
        return dict
    return str  # default to string


class MCPToolWrapper(BaseTool):
    """Wraps an external MCP tool so it looks like a native BaseTool.

    The agent sees it as a regular tool with name, description, input_model,
    and can call execute(). Under the hood, execute() calls the MCP server
    via MCPConnectorProxy.
    """

    # These are set per-instance, not per-class
    name: str = ""
    description: str = ""
    category: str = "mcp_external"
    input_model: type[PydanticBaseModel] = PydanticBaseModel

    def __init__(self, connector, tool_schema: dict):
        self._connector = connector
        self._tool_schema = tool_schema

        # Set instance attributes (override class-level)
        tool_name = tool_schema.get("name", "unknown")
        # Prefix with connector name to avoid collisions with built-in tools
        self.name = (
            f"mcp_{connector.name}_{tool_name}".replace("-", "_")
            .replace(" ", "_")
            .lower()
        )
        self._remote_tool_name = tool_name  # original name for the MCP call

        self.description = (
            f"[{connector.name}] {tool_schema.get('description', tool_name)}"
        )

        # Build input model from MCP tool's inputSchema
        input_schema = tool_schema.get("inputSchema", {})
        model_name = f"MCPInput_{self.name}"
        try:
            self.input_model = _build_pydantic_model(model_name, input_schema)
        except Exception:
            logger.warning("Failed to build input model for %s", self.name)
            self.input_model = type(model_name, (PydanticBaseModel,), {})

    @property
    def input_schema(self) -> dict:
        """Return the original MCP inputSchema (already JSON Schema)."""
        schema = self._tool_schema.get("inputSchema", {})
        if not schema.get("properties"):
            return {"type": "object", "properties": {}}
        return schema

    def execute(self, params: PydanticBaseModel, context: ToolContext) -> ToolResult:
        """Execute by calling the MCP server synchronously (fallback)."""
        from ai_tools.background import background_tool_denial

        denial = background_tool_denial(self, context)
        if denial is not None:
            return denial
        result = self._call_mcp_sync(params, context)
        content = result.get("content", "No response")
        is_error = result.get("is_error", False)
        if is_error:
            return ToolResult.error(content)
        return ToolResult(content=content)

    async def async_execute(
        self, params: PydanticBaseModel, context: ToolContext
    ) -> ToolResult:
        """Execute by calling the MCP server asynchronously (preferred).

        This avoids blocking the async event loop — the main reason MCP tools
        fail in Falcon but work in Claude Code.
        """
        from channels.db import database_sync_to_async

        from ai_tools.background import background_tool_denial
        from ee.falcon_ai.mcp_proxy import MCPConnectorProxy

        denial = background_tool_denial(self, context)
        if denial is not None:
            return denial

        raw_params = (
            params.model_dump(exclude_none=True)
            if hasattr(params, "model_dump")
            else {}
        )
        raw_params = {k: v for k, v in raw_params.items() if v is not None}

        connector = await database_sync_to_async(connector_for_execution)(
            self._connector.pk, self._remote_tool_name, context
        )
        if connector is None:
            return ToolResult.permission_denied(
                "Connector is not available in this workspace."
            )

        proxy = MCPConnectorProxy()
        guard = getattr(context, "background_analysis", None)
        if guard is not None:
            proxy.background_analysis = guard
        result = await proxy.execute_tool(connector, self._remote_tool_name, raw_params)
        if guard is not None:
            await guard.checkpoint()

        content = result.get("content", "No response")
        is_error = result.get("is_error", False)
        if is_error:
            return ToolResult.error(content)
        return ToolResult(content=content)

    def _call_mcp_sync(self, params, context):
        """Sync MCP call — used only as fallback from BaseTool.run()."""
        from ee.falcon_ai.mcp_proxy import MCPConnectorProxy

        raw_params = (
            params.model_dump(exclude_none=True)
            if hasattr(params, "model_dump")
            else {}
        )
        raw_params = {k: v for k, v in raw_params.items() if v is not None}

        connector = connector_for_execution(
            self._connector.pk, self._remote_tool_name, context
        )
        if connector is None:
            return {
                "content": "Connector is not available in this workspace.",
                "is_error": True,
            }

        proxy = MCPConnectorProxy()
        guard = getattr(context, "background_analysis", None)
        if guard is not None:
            proxy.background_analysis = guard
        result = proxy.execute_tool_sync(connector, self._remote_tool_name, raw_params)
        if guard is not None:
            guard.check()
        return result


def load_mcp_tools(organization, workspace=None, *, user=None) -> list[MCPToolWrapper]:
    """Load enabled MCP connector tools for the given org/workspace.

    Returns a list of MCPToolWrapper instances ready to be used by the agent.
    """
    try:
        connectors = accessible_connectors(ToolContext(user, organization, workspace))

        tools = []
        for connector in connectors:
            discovered = connector.discovered_tools or []
            enabled = connector.enabled_tool_names or []

            for tool_schema in discovered:
                tool_name = tool_schema.get("name", "")
                # If enabled list is empty, all tools are enabled
                if enabled and tool_name not in enabled:
                    continue

                try:
                    wrapper = MCPToolWrapper(connector, tool_schema)
                    tools.append(wrapper)
                except Exception as e:
                    logger.warning(
                        "Failed to wrap MCP tool %s from %s: %s",
                        tool_name,
                        connector.name,
                        e,
                    )

        logger.info(
            "Loaded %d MCP tools from %d connectors",
            len(tools),
            len(connectors),
        )
        return tools

    except Exception as e:
        logger.error("Failed to load MCP tools: %s", e)
        return []
