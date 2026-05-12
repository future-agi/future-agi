from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    format_status,
    key_value_block,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid


class GetCallExecutionInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    call_execution_id: str = Field(
        default="",
        description="The UUID of the call execution",
    )
    include_transcript: bool = Field(
        default=True, description="Include conversation transcript"
    )
    include_eval_results: bool = Field(
        default=True, description="Include evaluation results"
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
class GetCallExecutionTool(BaseTool):
    name = "get_call_execution"
    description = (
        "Returns detailed information about a single call execution within "
        "a test run. Includes transcript, evaluation scores, cost breakdown, "
        "recording URL, and performance metrics. If the call UUID is unknown, "
        "call this with no parameters to list candidates."
    )
    category = "agents"
    input_model = GetCallExecutionInput

    def execute(
        self, params: GetCallExecutionInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.test_execution import CallExecution

        def candidate_calls_result(detail: str = "") -> ToolResult:
            calls = (
                CallExecution.objects.filter(
                    test_execution__run_test__organization=context.organization
                )
                .select_related("test_execution", "scenario")
                .order_by("-created_at")[:10]
            )
            rows = []
            data_list = []
            for call in calls:
                scenario_name = call.scenario.name if call.scenario else "—"
                rows.append(
                    f"- `{call.id}` — {scenario_name} ({format_status(call.status)})"
                )
                data_list.append(
                    {
                        "id": str(call.id),
                        "scenario": scenario_name,
                        "status": call.status,
                        "test_execution_id": str(call.test_execution_id),
                    }
                )
            content = section(
                "Call Execution Candidates",
                (
                    (detail + "\n\n" if detail else "")
                    + "Choose one of these calls, then call "
                    "`get_call_execution` with `call_execution_id`.\n\n"
                    + ("\n".join(rows) if rows else "No call executions found.")
                ),
            )
            return ToolResult(content=content, data={"calls": data_list})

        call_ref = str(params.call_execution_id or "").strip()
        if not call_ref:
            return candidate_calls_result()
        if not is_uuid(call_ref):
            return candidate_calls_result(
                f"`{call_ref}` is not a valid call execution UUID."
            )

        try:
            call = CallExecution.objects.select_related(
                "test_execution", "scenario"
            ).get(
                id=call_ref,
                test_execution__run_test__organization=context.organization,
            )
        except CallExecution.DoesNotExist:
            return candidate_calls_result(f"Call execution `{call_ref}` was not found.")

        scenario_name = call.scenario.name if call.scenario else "—"

        info = key_value_block(
            [
                ("ID", f"`{call.id}`"),
                ("Scenario", scenario_name),
                ("Type", call.simulation_call_type or "—"),
                ("Status", format_status(call.status)),
                (
                    "Overall Score",
                    (
                        format_number(call.overall_score)
                        if call.overall_score is not None
                        else "—"
                    ),
                ),
                (
                    "Duration",
                    f"{call.duration_seconds}s" if call.duration_seconds else "—",
                ),
                ("End Reason", call.ended_reason or "—"),
                ("Phone Number", call.phone_number or "—"),
                ("Started", format_datetime(call.started_at)),
                ("Completed", format_datetime(call.completed_at)),
            ]
        )

        content = section(f"Call: {scenario_name}", info)

        # Cost breakdown
        if call.cost_cents is not None:
            content += "\n\n### Cost\n\n"
            cost_pairs = [("Total", f"${call.cost_cents / 100:.2f}")]
            if call.stt_cost_cents is not None:
                cost_pairs.append(("STT", f"${call.stt_cost_cents / 100:.2f}"))
            if call.llm_cost_cents is not None:
                cost_pairs.append(("LLM", f"${call.llm_cost_cents / 100:.2f}"))
            if call.tts_cost_cents is not None:
                cost_pairs.append(("TTS", f"${call.tts_cost_cents / 100:.2f}"))
            content += key_value_block(cost_pairs)

        # Performance
        if call.response_time_ms:
            content += f"\n\n### Performance\n\n**Avg Response Time:** {call.response_time_ms}ms"

        # Recording
        if call.recording_url:
            content += (
                f"\n\n### Recording\n\n[Listen to recording]({call.recording_url})"
            )

        # Call summary
        if call.call_summary:
            content += f"\n\n### Call Summary\n\n{truncate(call.call_summary, 1000)}"

        # Error
        if call.error_message:
            content += f"\n\n### Error\n\n```\n{truncate(call.error_message, 500)}\n```"

        # Evaluation results
        if params.include_eval_results and call.eval_outputs:
            content += "\n\n### Evaluation Results\n\n"
            if isinstance(call.eval_outputs, dict):
                for eval_name, result in list(call.eval_outputs.items())[:10]:
                    content += f"- **{eval_name}**: {truncate(str(result), 200)}\n"
            elif isinstance(call.eval_outputs, list):
                for item in call.eval_outputs[:10]:
                    content += f"- {truncate(str(item), 200)}\n"

        # Tool evaluation results
        if call.tool_outputs:
            content += "\n\n### Tool Evaluation\n\n"
            content += f"```json\n{truncate(str(call.tool_outputs), 500)}\n```"

        # Transcript
        if params.include_transcript:
            try:
                from simulate.models.chat_message import ChatMessageModel

                messages = ChatMessageModel.objects.filter(
                    call_execution=call
                ).order_by("created_at")[:50]
                if messages:
                    content += "\n\n### Transcript\n\n"
                    for msg in messages:
                        role = msg.role.upper() if msg.role else "?"
                        text = ""
                        if msg.messages and isinstance(msg.messages, list):
                            text = " ".join(str(m) for m in msg.messages)
                        elif msg.content and isinstance(msg.content, list):
                            for c in msg.content:
                                if isinstance(c, dict):
                                    text += c.get("text", str(c)) + " "
                                else:
                                    text += str(c) + " "
                        content += f"**{role}:** {truncate(text.strip(), 300)}\n\n"
            except Exception:
                pass  # Transcript optional, don't fail the tool

        data = {
            "id": str(call.id),
            "scenario": scenario_name,
            "type": call.simulation_call_type,
            "status": call.status,
            "overall_score": (
                float(call.overall_score) if call.overall_score is not None else None
            ),
            "duration_seconds": call.duration_seconds,
            "cost_cents": call.cost_cents,
            "recording_url": call.recording_url,
            "ended_reason": call.ended_reason,
        }

        return ToolResult(content=content, data=data)
