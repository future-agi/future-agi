from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional, Type

from pydantic import BaseModel as PydanticBaseModel

logger = logging.getLogger(__name__)


class EmptyInput(PydanticBaseModel):
    """Input model for tools that take no parameters."""

    pass


@dataclass
class ToolContext:
    """Context injected into every tool call.

    Carries the authenticated user, organization, and workspace.
    Created from the request in the transport layer (MCP or AI Assistant).
    """

    user: Any  # accounts.models.User
    organization: Any  # accounts.models.Organization
    workspace: Any  # accounts.models.Workspace

    @property
    def user_id(self):
        return self.user.id

    @property
    def organization_id(self):
        return self.organization.id

    @property
    def workspace_id(self):
        return self.workspace.id


@dataclass
class ToolResult:
    """Result returned by every tool execution.

    content: Markdown-formatted string optimized for LLM consumption.
    data: Optional structured data dict (for programmatic use).
    is_error: Whether this result represents an error.
    error_code: Optional structured error code (e.g. NOT_FOUND, VALIDATION_ERROR).
    """

    content: str
    data: Optional[dict] = None
    is_error: bool = False
    error_code: Optional[str] = None

    @classmethod
    def error(
        cls,
        message: str,
        data: Optional[dict] = None,
        error_code: Optional[str] = None,
    ) -> ToolResult:
        return cls(
            content=f"**Error:** {message}",
            data=data,
            is_error=True,
            error_code=error_code or "INTERNAL_ERROR",
        )

    @classmethod
    def not_found(cls, entity_type: str, entity_id: str) -> ToolResult:
        return cls(
            content=f"**Not Found:** {entity_type} with ID `{entity_id}` was not found in this workspace.",
            is_error=True,
            error_code="NOT_FOUND",
        )

    @classmethod
    def permission_denied(cls, message: str) -> ToolResult:
        return cls(
            content=f"**Permission Denied:** {message}",
            is_error=True,
            error_code="PERMISSION_DENIED",
        )

    @classmethod
    def feature_unavailable(cls, feature: str) -> ToolResult:
        return cls(
            content=(
                f"**Feature Unavailable:** `{feature}` is not available on "
                f"your current plan. Upgrade to access it."
            ),
            data={"feature": feature, "upgrade_required": True},
            is_error=True,
            error_code="ENTITLEMENT_DENIED",
        )

    @classmethod
    def validation_error(cls, message: str) -> ToolResult:
        return cls(
            content=f"**Validation Error:** {message}",
            is_error=True,
            error_code="VALIDATION_ERROR",
        )


class BaseTool(ABC):
    """Abstract base class for all AI tools.

    Subclasses must define:
    - name: unique tool identifier (snake_case)
    - description: what the tool does (shown to LLMs)
    - category: tool group (context, evaluations, datasets, tracing, etc.)
    - input_model: Pydantic model for input validation
    - execute(): the actual tool logic
    """

    name: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[str]
    input_model: ClassVar[Type[PydanticBaseModel]] = EmptyInput
    # Wall clock an agent gives this tool; None takes the caller's default.
    timeout_seconds: ClassVar[float | None] = None

    @abstractmethod
    def execute(self, params: PydanticBaseModel, context: ToolContext) -> ToolResult:
        """Execute the tool with validated params and context."""
        ...

    @property
    def input_schema(self) -> dict:
        """Return JSON Schema for the input model."""
        return self.input_model.model_json_schema()

    def run(self, raw_params: dict | None, context: ToolContext) -> ToolResult:
        """Validate input, execute tool, and handle errors.

        This is the public entry point. Transport layers call this.
        Sets the per-request workspace/organization context via ContextVars
        so that BaseModel managers, AutoWorkspaceField, and queryset scoping
        work correctly without touching django.conf.settings.
        """
        from tfc.middleware.workspace_context import workspace_context

        try:
            cleaned = self._clean_params(raw_params or {})
            params = self.input_model.model_validate(cleaned)
        except Exception as e:
            # Include the expected schema so the LLM can self-correct
            schema = self.input_schema
            required = schema.get("required", [])
            props = schema.get("properties", {})
            # Build a compact schema hint
            fields = []
            for name, spec in props.items():
                typ = spec.get("type", "string")
                desc = spec.get("description", "")[:60]
                req = " (REQUIRED)" if name in required else ""
                fields.append(f"  - {name}: {typ}{req} — {desc}")
            schema_hint = "\n".join(fields[:10])
            return ToolResult.error(
                f"Invalid parameters: {e}\n\nExpected schema:\n{schema_hint}\n\nYou sent: {raw_params}",
                error_code="VALIDATION_ERROR",
            )

        try:
            with workspace_context(
                workspace=context.workspace,
                organization=context.organization,
                user=context.user,
            ):
                return self.execute(params, context)
        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            logger.exception(f"Tool {self.name} failed: {e}")
            return ToolResult.error(
                f"Tool execution failed: {e}",
                error_code=code_from_exception(e),
            )

    @staticmethod
    def _clean_params(raw_params: dict) -> dict:
        """Pre-process tool parameters to handle common LLM quirks.

        LLMs sometimes send stringified JSON for list/dict fields.
        This attempts to parse string values that look like JSON.
        """
        import json

        cleaned = {}
        for key, value in raw_params.items():
            if isinstance(value, str) and value.strip().startswith(("[", "{")):
                try:
                    cleaned[key] = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    cleaned[key] = value
            else:
                cleaned[key] = value
        return cleaned

    def to_dict(self) -> dict:
        """Serialize tool metadata for discovery endpoints."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "input_schema": self.input_schema,
        }
