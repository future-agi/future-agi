from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class CreateApiKeyInput(PydanticBaseModel):
    name: Optional[str] = Field(
        default=None, description="Optional name/label for the API key"
    )
    key_type: str = Field(
        default="user",
        description="Type of API key: 'system', 'user', or 'mcp'",
    )


@register_tool
class CreateApiKeyTool(BaseTool):
    name = "create_api_key"
    description = (
        "Generates a new API key for the organization. Returns the full API key "
        "and secret - these are only shown once and cannot be retrieved later. "
        "Requires Owner or Admin permissions."
    )
    category = "users"
    input_model = CreateApiKeyInput

    def execute(self, params: CreateApiKeyInput, context: ToolContext) -> ToolResult:
        from django.db import IntegrityError

        from accounts.models.user import OrgApiKey
        from tfc.constants.levels import Level
        from tfc.permissions.utils import get_org_membership

        org = context.organization
        actor = context.user

        # Level-based permission check
        actor_membership = get_org_membership(actor)
        if actor_membership is None or actor_membership.level_or_legacy < Level.ADMIN:
            return ToolResult.permission_denied(
                "You do not have permission to create API keys. "
                "Requires organization admin or owner role."
            )

        # Validate key type
        valid_types = ["system", "user", "mcp"]
        if params.key_type not in valid_types:
            return ToolResult.error(
                f"Invalid key_type '{params.key_type}'. Must be one of: {', '.join(valid_types)}",
                error_code="VALIDATION_ERROR",
            )

        try:
            api_key = OrgApiKey(
                name=params.name or f"{params.key_type}_key",
                organization=org,
                type=params.key_type,
                user=actor,
                workspace=context.workspace,
                enabled=True,
            )
            api_key.save()
        except IntegrityError:
            return ToolResult.error(
                f"A {params.key_type} API key already exists for this organization. "
                "Only one system API key is allowed per organization.",
                error_code="VALIDATION_ERROR",
            )

        info = key_value_block(
            [
                ("Key ID", f"`{api_key.id}`"),
                ("Name", api_key.name),
                ("Type", api_key.type),
                ("API Key", f"`{api_key.api_key}`"),
                ("Secret Key", f"`{api_key.secret_key}`"),
                ("Workspace", context.workspace.name),
                ("Created By", f"{actor.name} ({actor.email})"),
                (
                    "IMPORTANT",
                    "Save these credentials now. The secret key cannot be retrieved later.",
                ),
            ]
        )
        content = section("API Key Created", info)

        return ToolResult(
            content=content,
            data={
                "key_id": str(api_key.id),
                "name": api_key.name,
                "type": api_key.type,
                "api_key": api_key.api_key,
                "secret_key": api_key.secret_key,
            },
        )
