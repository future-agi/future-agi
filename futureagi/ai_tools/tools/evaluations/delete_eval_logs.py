from typing import Any

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import format_datetime, format_status, markdown_table, section
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid


class DeleteEvalLogsInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    log_ids: list[str] = Field(
        default_factory=list,
        description="List of log_id UUIDs to delete",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Set true only after the user confirms permanent deletion.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        raw_ids = (
            normalized.get("log_ids")
            or normalized.get("log_id")
            or normalized.get("ids")
            or normalized.get("id")
            or []
        )
        if isinstance(raw_ids, str):
            raw_ids = [part.strip() for part in raw_ids.split(",") if part.strip()]
        elif not isinstance(raw_ids, list):
            raw_ids = [raw_ids]
        normalized["log_ids"] = [
            str(item).strip() for item in raw_ids if str(item).strip()
        ]
        normalized["confirm_delete"] = (
            normalized.get("confirm_delete") is True
            or normalized.get("confirmed") is True
            or normalized.get("force") is True
        )
        return normalized


@register_tool
class DeleteEvalLogsTool(BaseTool):
    name = "delete_eval_logs"
    description = (
        "Deletes evaluation log entries by their log IDs. "
        "This permanently removes the API call log records. "
        "Use get_eval_logs to find log IDs first."
    )
    category = "evaluations"
    input_model = DeleteEvalLogsInput

    def execute(self, params: DeleteEvalLogsInput, context: ToolContext) -> ToolResult:
        from tfc.ee_gating import EEFeature, is_oss

        if is_oss():
            return ToolResult.feature_unavailable(EEFeature.AUDIT_LOGS.value)

        from ee.usage.models.usage import APICallLog

        if not params.log_ids:
            return _delete_log_candidates(context)

        # Find matching logs belonging to this organization
        log_id_strs = [str(lid) for lid in params.log_ids]
        valid_log_ids = [log_id for log_id in log_id_strs if is_uuid(log_id)]
        invalid_ids = [log_id for log_id in log_id_strs if not is_uuid(log_id)]
        if not valid_log_ids:
            result = _delete_log_candidates(context)
            result.data = result.data or {}
            result.data["invalid_ids"] = invalid_ids
            result.content += (
                "\n\nThe supplied value was not a valid log UUID. Use one of the "
                "candidate `log_id` values and ask for confirmation before deletion."
            )
            return result

        logs = APICallLog.objects.filter(
            log_id__in=valid_log_ids,
            organization=context.organization,
        )

        found_ids = {str(log.log_id) for log in logs}
        missing = invalid_ids + [lid for lid in valid_log_ids if lid not in found_ids]

        if not logs.exists():
            return ToolResult.error(
                "No matching log entries found for the provided IDs in this organization.",
                error_code="NOT_FOUND",
            )

        count = logs.count()
        if not params.confirm_delete:
            rows = [
                [
                    f"`{log.log_id}`",
                    format_status(log.status),
                    log.source or "—",
                    format_datetime(log.created_at),
                ]
                for log in logs[:20]
            ]
            content = section(
                "Eval Logs Delete Preview",
                (
                    f"Found **{count}** matching log entry(ies). "
                    "Set `confirm_delete=true` only after the user confirms permanent deletion.\n\n"
                    + markdown_table(["Log ID", "Status", "Source", "Created"], rows)
                ),
            )
            if missing:
                content += f"\n\n_Note: {len(missing)} log ID(s) were not found and would be skipped._"
            return ToolResult(
                content=content,
                data={
                    "requires_confirmation": True,
                    "matched_count": count,
                    "missing_ids": missing,
                },
            )

        logs.delete()

        content = section(
            "Eval Logs Deleted",
            f"Successfully deleted **{count}** log entry(ies).",
        )

        if missing:
            content += (
                f"\n\n_Note: {len(missing)} log ID(s) were not found and skipped._"
            )

        return ToolResult(
            content=content,
            data={
                "deleted_count": count,
                "missing_ids": missing,
            },
        )


def _delete_log_candidates(context: ToolContext) -> ToolResult:
    from ee.usage.models.usage import APICallLog

    logs = APICallLog.objects.filter(
        organization=context.organization,
    ).order_by("-created_at")[:10]
    rows = [
        [
            f"`{log.log_id}`",
            format_status(log.status),
            log.source or "—",
            format_datetime(log.created_at),
        ]
        for log in logs
    ]
    return ToolResult(
        content=section(
            "Eval Log Delete Candidates",
            markdown_table(["Log ID", "Status", "Source", "Created"], rows)
            if rows
            else "No eval logs found.",
        ),
        data={
            "requires_log_ids": True,
            "requires_confirmation": True,
            "logs": [{"log_id": str(log.log_id)} for log in logs],
        },
    )
