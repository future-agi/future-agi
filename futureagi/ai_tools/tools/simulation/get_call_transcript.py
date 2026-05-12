from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid


class GetCallTranscriptInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    call_execution_id: str = Field(
        default="",
        description="The UUID of the call execution. Omit it to list candidates.",
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
class GetCallTranscriptTool(BaseTool):
    name = "get_call_transcript"
    description = (
        "Returns the conversation transcript for a call execution. "
        "Shows speaker role, content, and timestamps in chronological order."
    )
    category = "simulation"
    input_model = GetCallTranscriptInput

    def execute(
        self, params: GetCallTranscriptInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.test_execution import CallExecution, CallTranscript

        def candidate_calls_result(title: str, detail: str = "") -> ToolResult:
            calls = (
                CallExecution.objects.filter(
                    test_execution__run_test__organization=context.organization
                )
                .select_related("scenario")
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
                    ]
                )
                data.append(
                    {
                        "id": str(call.id),
                        "scenario": scenario_name,
                        "status": call.status,
                    }
                )
            body = detail or "Provide `call_execution_id` to inspect a transcript."
            if rows:
                body += "\n\n" + markdown_table(
                    ["Call ID", "Scenario", "Status"],
                    rows,
                )
            else:
                body += "\n\nNo call executions found in this workspace."
            return ToolResult.needs_input(
                section(title, body),
                data={"requires_call_execution_id": True, "calls": data},
                missing_fields=["call_execution_id"],
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
            call = CallExecution.objects.select_related("scenario").get(
                id=call_ref,
                test_execution__run_test__organization=context.organization,
            )
        except CallExecution.DoesNotExist:
            return candidate_calls_result(
                "Call Execution Not Found",
                f"Call execution `{call_ref}` was not found.",
            )

        scenario_name = call.scenario.name if call.scenario else "—"

        transcripts = CallTranscript.objects.filter(call_execution=call).order_by(
            "start_time_ms"
        )

        info = key_value_block(
            [
                ("Call ID", f"`{call.id}`"),
                ("Scenario", scenario_name),
                ("Messages", str(transcripts.count())),
            ]
        )

        content = section(f"Transcript: {scenario_name}", info)

        if not transcripts.exists():
            content += "\n\n_No transcript entries found for this call._"
            return ToolResult(
                content=content,
                data={"call_id": str(call.id), "transcript": []},
            )

        content += "\n\n---\n\n"

        transcript_data = []
        for t in transcripts[:100]:  # Limit to 100 entries
            role = t.speaker_role.upper()
            time_str = ""
            if t.start_time_ms:
                seconds = t.start_time_ms / 1000
                minutes = int(seconds // 60)
                secs = int(seconds % 60)
                time_str = f"[{minutes:02d}:{secs:02d}] "

            content += f"**{role}:** {time_str}{truncate(t.content, 500)}\n\n"

            transcript_data.append(
                {
                    "speaker_role": t.speaker_role,
                    "content": t.content,
                    "start_time_ms": t.start_time_ms,
                    "end_time_ms": t.end_time_ms,
                }
            )

        if transcripts.count() > 100:
            content += f"\n\n_Showing 100 of {transcripts.count()} transcript entries._"

        return ToolResult(
            content=content,
            data={"call_id": str(call.id), "transcript": transcript_data},
        )
