from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    format_status,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetEvalLogDetailInput(PydanticBaseModel):
    log_id: str = Field(
        default="", description="The UUID (log_id) of the eval log entry to retrieve"
    )


def _candidate_eval_logs_result(context: ToolContext, title: str, detail: str = "") -> ToolResult:
    from ee.usage.models.usage import APICallLog

    logs = list(
        APICallLog.objects.filter(organization=context.organization).order_by(
            "-created_at"
        )[:10]
    )
    rows = [
        [
            f"`{log.log_id}`",
            format_status(log.status),
            log.source or "—",
            format_datetime(log.created_at),
        ]
        for log in logs
    ]
    body = detail or "Choose an eval log ID."
    body += "\n\n" + (
        markdown_table(["Log ID", "Status", "Source", "Created"], rows)
        if rows
        else "No eval logs found."
    )
    return ToolResult(
        content=section(title, body),
        data={
            "requires_log_id": True,
            "logs": [{"log_id": str(log.log_id)} for log in logs],
        },
    )


@register_tool
class GetEvalLogDetailTool(BaseTool):
    name = "get_eval_log_detail"
    description = (
        "Returns detailed information about a specific evaluation log entry "
        "including cost, status, token counts, configuration, and timestamps."
    )
    category = "evaluations"
    input_model = GetEvalLogDetailInput

    def execute(
        self, params: GetEvalLogDetailInput, context: ToolContext
    ) -> ToolResult:
        from tfc.ee_gating import EEFeature, is_oss

        if is_oss():
            return ToolResult.feature_unavailable(EEFeature.AUDIT_LOGS.value)

        from django.core.exceptions import ValidationError
        from ee.usage.models.usage import APICallLog

        log_ref = str(params.log_id or "").strip()
        if not log_ref:
            return _candidate_eval_logs_result(context, "Eval Log Required")

        try:
            log = APICallLog.objects.get(
                log_id=log_ref,
                organization=context.organization,
            )
        except (APICallLog.DoesNotExist, ValidationError, ValueError, TypeError):
            return _candidate_eval_logs_result(
                context,
                "Eval Log Not Found",
                f"Eval log `{log_ref}` was not found. Use one of these log IDs instead.",
            )

        info = key_value_block(
            [
                ("Log ID", f"`{log.log_id}`"),
                ("Status", format_status(log.status)),
                ("Cost", format_number(log.cost, 6)),
                ("Deducted Cost", format_number(log.deducted_cost, 6)),
                ("Input Tokens", str(log.input_token_count or 0)),
                ("API Call Type", log.api_call_type.name if log.api_call_type else "—"),
                ("Source", log.source or "—"),
                ("Source ID", f"`{log.source_id}`" if log.source_id else "—"),
                ("Reference ID", f"`{log.reference_id}`" if log.reference_id else "—"),
                ("User", str(log.user) if log.user else "—"),
                ("Created", format_datetime(log.created_at)),
                ("Updated", format_datetime(log.updated_at)),
            ]
        )

        content = section("Eval Log Detail", info)

        # Show config if present
        if log.config and isinstance(log.config, dict) and log.config:
            content += "\n\n### Configuration\n\n"
            content += f"```json\n{truncate(str(log.config), 1000)}\n```"

        return ToolResult(
            content=content,
            data={
                "log_id": str(log.log_id),
                "status": log.status,
                "cost": str(log.cost),
                "deducted_cost": str(log.deducted_cost),
                "input_token_count": log.input_token_count,
                "api_call_type": log.api_call_type.name if log.api_call_type else None,
                "source": log.source,
                "source_id": log.source_id,
                "reference_id": log.reference_id,
                "config": log.config,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            },
        )
