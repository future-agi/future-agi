from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid


class GetCallLogsInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    call_execution_id: str = Field(
        default="",
        description="The UUID of the call execution. If omitted, candidates are returned.",
    )
    limit: int = Field(
        default=50, ge=1, le=200, description="Max log entries to return"
    )
    source: str | None = Field(
        default=None, description="Filter by source: agent or customer"
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["call_execution_id"] = (
            normalized.get("call_execution_id")
            or normalized.get("call_id")
            or normalized.get("execution_id")
            or normalized.get("id")
            or ""
        )
        return normalized


@register_tool
class GetCallLogsTool(BaseTool):
    name = "get_call_logs"
    description = (
        "Returns log entries for a call execution. "
        "Shows timestamp, severity, category, and message body."
    )
    category = "simulation"
    input_model = GetCallLogsInput

    def execute(self, params: GetCallLogsInput, context: ToolContext) -> ToolResult:

        from simulate.models.call_log_entry import CallLogEntry
        from simulate.models.test_execution import CallExecution

        def candidate_calls_result(title: str, detail: str = "") -> ToolResult:
            calls = (
                CallExecution.objects.filter(
                    test_execution__run_test__organization=context.organization
                )
                .select_related("test_execution", "scenario")
                .order_by("-created_at")[:10]
            )
            rows = []
            data = []
            for call in calls:
                scenario_name = call.scenario.name if call.scenario else "—"
                rows.append(
                    [
                        f"`{call.id}`",
                        truncate(scenario_name, 40),
                        call.status,
                        format_datetime(call.created_at),
                    ]
                )
                data.append(
                    {
                        "id": str(call.id),
                        "scenario": scenario_name,
                        "status": call.status,
                    }
                )
            body = detail or "Provide `call_execution_id` to inspect call logs."
            if rows:
                body += "\n\n" + markdown_table(
                    ["Call ID", "Scenario", "Status", "Created"],
                    rows,
                )
            else:
                body += "\n\nNo call executions found in this workspace."
            return ToolResult(
                content=section(title, body),
                data={"requires_call_execution_id": True, "calls": data},
            )

        call_ref = str(params.call_execution_id or "").strip()
        if not call_ref:
            return candidate_calls_result("Call Execution Required")
        if not is_uuid(call_ref):
            return candidate_calls_result(
                "Call Execution Not Found",
                f"`{call_ref}` is not a valid call execution UUID.",
            )

        try:
            call = CallExecution.objects.get(
                id=call_ref,
                test_execution__run_test__organization=context.organization,
            )
        except CallExecution.DoesNotExist:
            return candidate_calls_result(
                "Call Execution Not Found",
                f"Call execution `{call_ref}` was not found.",
            )

        qs = CallLogEntry.objects.filter(call_execution=call).order_by("logged_at")

        if params.source:
            qs = qs.filter(source=params.source)

        total = qs.count()
        logs = qs[: params.limit]

        info = key_value_block(
            [
                ("Call ID", f"`{call.id}`"),
                ("Total Log Entries", str(total)),
                ("Showing", str(min(total, params.limit))),
            ]
        )

        content = section("Call Logs", info)

        if not logs:
            content += "\n\n_No log entries found for this call._"
            return ToolResult(
                content=content,
                data={"call_id": str(call.id), "logs": [], "total": 0},
            )

        rows = []
        log_data = []
        for log in logs:
            rows.append(
                [
                    format_datetime(log.logged_at),
                    log.severity_text or str(log.level),
                    log.source,
                    log.category or "—",
                    truncate(log.body, 80),
                ]
            )
            log_data.append(
                {
                    "id": str(log.id),
                    "logged_at": log.logged_at.isoformat() if log.logged_at else None,
                    "level": log.level,
                    "severity": log.severity_text,
                    "source": log.source,
                    "category": log.category,
                    "body": log.body,
                }
            )

        table = markdown_table(
            ["Time", "Severity", "Source", "Category", "Message"], rows
        )
        content += f"\n\n{table}"

        if total > params.limit:
            content += f"\n\n_Showing {params.limit} of {total} entries. Use limit parameter to see more._"

        return ToolResult(
            content=content,
            data={"call_id": str(call.id), "logs": log_data, "total": total},
        )
