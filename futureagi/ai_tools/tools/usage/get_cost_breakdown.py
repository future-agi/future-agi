from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_number,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class GetCostBreakdownInput(PydanticBaseModel):
    days: int = Field(default=30, ge=1, le=90, description="Look back N days")


@register_tool
class GetCostBreakdownTool(BaseTool):
    name = "get_cost_breakdown"
    description = (
        "Returns cost analytics for the current organization. Shows wallet balance, "
        "total spending, and cost breakdown by API call type over the specified period."
    )
    category = "usage"
    input_model = GetCostBreakdownInput

    def execute(
        self, params: GetCostBreakdownInput, context: ToolContext
    ) -> ToolResult:
        from tfc.ee_gating import EEFeature, is_oss

        if is_oss():
            return ToolResult.feature_unavailable(EEFeature.AUDIT_LOGS.value)

        from collections import defaultdict
        from datetime import datetime, timedelta, timezone

        from django.db.models import Count, Sum

        since = datetime.now(timezone.utc) - timedelta(days=params.days)

        # Get subscription info
        sub_info = self._get_subscription(context.organization)

        info = key_value_block(
            [
                ("Organization", context.organization.name),
                ("Subscription", sub_info.get("tier", "—")),
                ("Wallet Balance", f"${format_number(sub_info.get('balance', 0))}"),
                ("Period", f"Last {params.days} days"),
            ]
        )
        content = section("Cost Breakdown", info)

        # Get API call logs
        try:
            from ee.usage.models.usage import APICallLog
        except ImportError:
            APICallLog = None

        if APICallLog is None:
            return ToolResult.feature_unavailable(EEFeature.AUDIT_LOGS.value)

        logs_qs = APICallLog.no_workspace_objects.filter(
            organization=context.organization,
            created_at__gte=since,
        )

        # Total spend
        total_agg = logs_qs.aggregate(
            total_cost=Sum("deducted_cost"),
            total_calls=Count("id"),
        )
        total_cost = float(total_agg["total_cost"] or 0)
        total_calls = total_agg["total_calls"] or 0

        content += f"\n\n### Summary\n\n"
        content += key_value_block(
            [
                ("Total Spend", f"${format_number(total_cost, 4)}"),
                ("Total API Calls", str(total_calls)),
                (
                    "Avg Cost/Call",
                    (
                        f"${format_number(total_cost / total_calls, 6)}"
                        if total_calls > 0
                        else "—"
                    ),
                ),
            ]
        )

        # Breakdown by call type
        by_type = (
            logs_qs.values("api_call_type__name")
            .annotate(
                total_cost=Sum("deducted_cost"),
                call_count=Count("id"),
            )
            .order_by("-total_cost")
        )

        if by_type:
            content += "\n\n### Cost by API Call Type\n\n"
            rows = []
            type_data = []
            for item in by_type:
                call_type = item["api_call_type__name"] or "Unknown"
                cost = float(item["total_cost"] or 0)
                count = item["call_count"]
                pct = (cost / total_cost * 100) if total_cost > 0 else 0

                rows.append(
                    [
                        call_type,
                        str(count),
                        f"${format_number(cost, 4)}",
                        f"{format_number(pct, 1)}%",
                    ]
                )
                type_data.append(
                    {
                        "type": call_type,
                        "calls": count,
                        "cost": cost,
                        "percentage": pct,
                    }
                )

            content += markdown_table(
                ["Call Type", "Calls", "Cost", "% of Total"], rows
            )
        else:
            type_data = []
            content += "\n\n### Cost by API Call Type\n\n_No API calls recorded in this period._"

        # Daily spend trend (last 7 days of the period)
        content += "\n\n### Daily Spend (Recent)\n\n"
        daily_rows = []
        for i in range(min(7, params.days)):
            day_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=i)
            day_end = day_start + timedelta(days=1)

            day_agg = logs_qs.filter(
                created_at__gte=day_start, created_at__lt=day_end
            ).aggregate(cost=Sum("deducted_cost"), calls=Count("id"))

            day_cost = float(day_agg["cost"] or 0)
            day_calls = day_agg["calls"] or 0

            daily_rows.append(
                [
                    day_start.strftime("%Y-%m-%d"),
                    str(day_calls),
                    f"${format_number(day_cost, 4)}",
                ]
            )

        content += markdown_table(["Date", "Calls", "Cost"], daily_rows)

        data = {
            "total_cost": total_cost,
            "total_calls": total_calls,
            "wallet_balance": sub_info.get("balance"),
            "subscription": sub_info.get("tier"),
            "by_type": type_data,
        }

        return ToolResult(content=content, data=data)

    def _get_subscription(self, org) -> dict:
        try:
            try:
                from ee.usage.models.usage import OrganizationSubscription
            except ImportError:
                OrganizationSubscription = None

            if OrganizationSubscription is None:
                return {"tier": "self-hosted", "status": "self-hosted", "balance": None}

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
