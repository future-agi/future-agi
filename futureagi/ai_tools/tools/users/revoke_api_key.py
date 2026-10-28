from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class RevokeApiKeyInput(PydanticBaseModel):
    key_id: UUID = Field(description="UUID of the API key to revoke")


@register_tool
class RevokeApiKeyTool(BaseTool):
    name = "revoke_api_key"
    description = (
        "Revokes (disables) an API key so it can no longer be used for authentication. "
        "Requires Owner or Admin permissions."
    )
    category = "users"
    input_model = RevokeApiKeyInput

    def execute(self, params: RevokeApiKeyInput, context: ToolContext) -> ToolResult:

        from accounts.models.user import OrgApiKey
        from accounts.services.api_keys import can_manage_system_keys, visible_api_keys

        org = context.organization
        actor = context.user

        # Permission check
        if not can_manage_system_keys(actor, org):
            return ToolResult.error(
                "You do not have permission to revoke API keys. "
                "Only Owner or Admin roles can revoke API keys.",
                error_code="PERMISSION_DENIED",
            )

        try:
            api_key = visible_api_keys(actor, org).get(id=params.key_id)
        except OrgApiKey.DoesNotExist:
            return ToolResult.not_found("API Key", str(params.key_id))

        if not api_key.enabled:
            return ToolResult.error(
                f"API key '{api_key.name}' is already revoked/disabled.",
                error_code="VALIDATION_ERROR",
            )

        # Mask the key for display
        masked_key = f"{api_key.api_key[:8]}..." if api_key.api_key else "—"

        api_key.enabled = False
        api_key.save()

        info = key_value_block(
            [
                ("Key ID", f"`{api_key.id}`"),
                ("Name", api_key.name),
                ("Type", api_key.type),
                ("Key Prefix", masked_key),
                ("Status", "Revoked / Disabled"),
                ("Revoked By", f"{actor.name} ({actor.email})"),
            ]
        )
        content = section("API Key Revoked", info)

        return ToolResult(
            content=content,
            data={
                "key_id": str(api_key.id),
                "name": api_key.name,
                "type": api_key.type,
                "enabled": False,
            },
        )
