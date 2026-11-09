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
        "Revokes (disables) one of your own personal API keys so it can no longer "
        "be used for authentication."
    )
    category = "users"
    input_model = RevokeApiKeyInput

    def execute(self, params: RevokeApiKeyInput, context: ToolContext) -> ToolResult:

        from accounts.api_key_revocation import publish_key_revocation
        from accounts.models.user import OrgApiKey
        from accounts.models.workspace import OrganizationRoles

        org = context.organization
        actor = context.user

        from accounts.api_key_policy import live_membership

        if live_membership(actor.pk, org.pk) is None:
            return ToolResult.error(
                "Active membership is required.", error_code="PERMISSION_DENIED"
            )

        try:
            api_key = OrgApiKey.no_workspace_objects.get(
                id=params.key_id, organization=org, user=actor, type="user"
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
        # Without this, fi-collector keeps accepting the key until its cache expires.
        publish_key_revocation(api_key)

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
