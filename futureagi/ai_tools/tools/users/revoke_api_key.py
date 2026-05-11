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
        from tfc.constants.levels import Level
        from tfc.permissions.utils import get_org_membership

        org = context.organization
        actor = context.user

        # Level-based permission check
        actor_membership = get_org_membership(actor)
        if actor_membership is None or actor_membership.level_or_legacy < Level.ADMIN:
            return ToolResult.permission_denied(
                "You do not have permission to revoke API keys. "
                "Requires organization admin or owner role."
            )

        try:
            api_key = OrgApiKey.no_workspace_objects.get(
                id=params.key_id, organization=org
            )
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
