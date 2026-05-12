from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class RevokeApiKeyInput(PydanticBaseModel):
    key_id: UUID | None = Field(
        default=None, description="UUID of the API key to revoke"
    )


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
        from accounts.models.workspace import OrganizationRoles

        org = context.organization
        actor = context.user

        # Permission check
        if actor.organization_role not in [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
        ]:
            return ToolResult.error(
                "You do not have permission to revoke API keys. "
                "Only Owner or Admin roles can revoke API keys.",
                error_code="PERMISSION_DENIED",
            )

        from ai_tools.tools.users._utils import candidate_api_keys_result

        if not params.key_id:
            return candidate_api_keys_result(
                context,
                "API Key Required",
                "Choose the API key ID to revoke.",
                enabled_only=True,
            )

        try:
            api_key = OrgApiKey.no_workspace_objects.get(
                id=params.key_id, organization=org
            )
        except OrgApiKey.DoesNotExist:
            return candidate_api_keys_result(
                context,
                "API Key Not Found",
                f"API key `{params.key_id}` was not found. Use one of these IDs instead.",
                enabled_only=True,
            )

        if not api_key.enabled:
            return ToolResult(
                content=section(
                    "API Key Already Revoked",
                    key_value_block(
                        [
                            ("Key ID", f"`{api_key.id}`"),
                            ("Name", api_key.name),
                            ("Type", api_key.type),
                            ("Status", "Already revoked / disabled"),
                        ]
                    ),
                ),
                data={
                    "key_id": str(api_key.id),
                    "name": api_key.name,
                    "type": api_key.type,
                    "enabled": False,
                    "already_revoked": True,
                },
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
