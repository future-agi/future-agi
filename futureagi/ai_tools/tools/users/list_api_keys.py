from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    format_uuid,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class ListApiKeysInput(PydanticBaseModel):
    key_type: Optional[str] = Field(
        default=None,
        description="Filter by key type: 'system', 'user', or 'mcp'",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")


@register_tool
class ListApiKeysTool(BaseTool):
    name = "list_api_keys"
    description = (
        "Lists API keys for the current organization. Shows key prefix (masked), "
        "type, associated workspace, creation date, and enabled status."
    )
    category = "users"
    input_model = ListApiKeysInput

    def execute(self, params: ListApiKeysInput, context: ToolContext) -> ToolResult:

        from accounts.models.user import OrgApiKey
        from tfc.constants.levels import Level
        from tfc.permissions.utils import get_org_membership

        org = context.organization

        # API keys are sensitive — require admin
        actor_membership = get_org_membership(context.user)
        if actor_membership is None or actor_membership.level_or_legacy < Level.ADMIN:
            return ToolResult.permission_denied(
                "You do not have permission to list API keys. "
                "Requires organization admin or owner role."
            )

        qs = OrgApiKey.no_workspace_objects.filter(organization=org).order_by(
            "-created_at"
        )

        if params.key_type:
            qs = qs.filter(type=params.key_type.lower())

        total = qs.count()
        keys = qs[: params.limit]

        rows = []
        data_list = []
        for key in keys:
            # Mask the API key, only show first 8 chars
            masked_key = f"{key.api_key[:8]}..." if key.api_key else "—"
            status = "active" if key.enabled else "inactive"
            workspace_name = key.workspace.name if key.workspace else "—"
            user_email = key.user.email if key.user else "—"

            rows.append(
                [
                    format_uuid(key.id),
                    key.name or "—",
                    key.type,
                    masked_key,
                    workspace_name,
                    user_email,
                    format_status(status),
                    format_datetime(key.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(key.id),
                    "name": key.name,
                    "type": key.type,
                    "key_prefix": masked_key,
                    "workspace": workspace_name,
                    "user": user_email,
                    "enabled": key.enabled,
                }
            )

        table = markdown_table(
            [
                "ID",
                "Name",
                "Type",
                "Key Prefix",
                "Workspace",
                "User",
                "Status",
                "Created",
            ],
            rows,
        )

        filter_desc = ""
        if params.key_type:
            filter_desc = f" (type: {params.key_type})"

        content = section(
            f"API Keys ({total}){filter_desc}",
            f"Organization: **{org.display_name or org.name}**\n\n{table}",
        )

        return ToolResult(content=content, data={"api_keys": data_list, "total": total})
