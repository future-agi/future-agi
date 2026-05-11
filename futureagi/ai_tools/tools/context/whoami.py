from ai_tools.base import BaseTool, EmptyInput, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool
from tfc.constants.levels import Level
from tfc.permissions.utils import get_effective_workspace_level, get_org_membership


@register_tool
class WhoamiTool(BaseTool):
    name = "whoami"
    description = (
        "Returns the current authenticated user, organization, workspace, "
        "and subscription information. Use this to understand who you are "
        "acting as and what resources are available."
    )
    category = "context"
    input_model = EmptyInput

    def execute(self, params: EmptyInput, context: ToolContext) -> ToolResult:
        user = context.user
        org = context.organization
        workspace = context.workspace

        # Resolve role from level-based RBAC (not the stale legacy field)
        membership = get_org_membership(user)
        org_role = (
            Level.to_org_string(membership.level_or_legacy) if membership else "—"
        )

        # Resolve workspace role
        ws_level = get_effective_workspace_level(user, workspace.id)
        ws_role = Level.to_ws_string(ws_level) if ws_level else "—"

        # Get subscription info
        subscription_info = self._get_subscription(org)

        # Count only accessible workspaces
        from accounts.models.workspace import Workspace, WorkspaceMembership

        if membership and membership.level_or_legacy >= Level.ADMIN:
            workspace_count = Workspace.objects.filter(
                organization=org, is_active=True, deleted=False
            ).count()
        else:
            workspace_count = (
                WorkspaceMembership.no_workspace_objects.filter(
                    user=user,
                    is_active=True,
                    workspace__organization=org,
                    workspace__is_active=True,
                    workspace__deleted=False,
                )
                .values("workspace_id")
                .distinct()
                .count()
            )

        info = key_value_block(
            [
                ("User", f"{user.name} ({user.email})"),
                ("Role", org_role),
                ("Organization", f"{org.name} (`{org.id}`)"),
                ("Workspace", f"{workspace.name} (`{workspace.id}`)"),
                ("Workspace Role", ws_role),
                ("Subscription", subscription_info.get("tier", "—")),
                ("Status", subscription_info.get("status", "—")),
                (
                    "Wallet Balance",
                    (
                        f"${subscription_info['balance']}"
                        if subscription_info.get("balance") is not None
                        else None
                    ),
                ),
                ("Available Workspaces", str(workspace_count)),
            ]
        )

        content = section("Current Context", info)

        return ToolResult(
            content=content,
            data={
                "user_id": str(user.id),
                "user_email": user.email,
                "user_name": user.name,
                "organization_id": str(org.id),
                "organization_name": org.name,
                "organization_role": org_role,
                "workspace_id": str(workspace.id),
                "workspace_name": workspace.name,
                "workspace_role": ws_role,
                "subscription_tier": subscription_info.get("tier"),
                "wallet_balance": subscription_info.get("balance"),
            },
        )

    def _get_subscription(self, org) -> dict:
        # No subscription model when ee is absent.
        try:
            from ee.usage.models.usage import OrganizationSubscription
        except ImportError:
            return {"tier": "self-hosted", "status": "self-hosted", "balance": None}

        try:
            sub = OrganizationSubscription.objects.select_related(
                "subscription_tier"
            ).get(organization=org)
            return {
                "tier": sub.subscription_tier.name if sub.subscription_tier else "—",
                "status": sub.status,
                "balance": float(sub.wallet_balance) if sub.wallet_balance else 0,
            }
        except Exception:
            return {"tier": "Unknown", "status": "Unknown", "balance": None}
