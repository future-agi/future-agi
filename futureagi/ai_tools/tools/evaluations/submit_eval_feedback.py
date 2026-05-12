from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, markdown_table, section, truncate
from ai_tools.registry import register_tool


class SubmitEvalFeedbackInput(PydanticBaseModel):
    eval_template_id: str = Field(
        default="",
        description="Name or UUID of the eval template this feedback is for. Omit to list candidates.",
    )
    source_id: str = Field(
        default="",
        description=(
            "The source ID (e.g. row ID, trace ID, or log ID) that the feedback references"
        )
    )
    feedback_value: str = Field(
        default="",
        description="Feedback value: 'passed' or 'failed'",
    )
    explanation: Optional[str] = Field(
        default=None,
        max_length=5000,
        description="Optional explanation for the feedback",
    )
    feedback_improvement: Optional[str] = Field(
        default=None,
        max_length=5000,
        description="Optional suggestion for how the evaluation could be improved",
    )
    source: Optional[str] = Field(
        default="eval_playground",
        description=(
            "Source context: 'dataset', 'prompt', 'sdk', 'trace', "
            "'experiment', 'observe', or 'eval_playground'"
        ),
    )
    row_id: Optional[str] = Field(
        default=None,
        description="Optional row ID if feedback is for a dataset row",
    )


@register_tool
class SubmitEvalFeedbackTool(BaseTool):
    name = "submit_eval_feedback"
    description = (
        "Submits feedback on an evaluation result (passed/failed). "
        "Feedback is used to improve evaluation quality over time. "
        "Requires an eval template ID and a source ID referencing the evaluated item. "
        "Call with partial input to get candidate templates or missing fields."
    )
    category = "evaluations"
    input_model = SubmitEvalFeedbackInput

    def execute(
        self, params: SubmitEvalFeedbackInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.resolvers import resolve_eval_template
        from model_hub.models.choices import FeedbackSourceChoices
        from model_hub.models.evals_metric import EvalTemplate, Feedback

        # Validate eval template
        if not params.eval_template_id:
            return _candidate_eval_templates_result(
                context,
                "Eval Template Required For Feedback",
                "Choose the eval template this feedback should attach to.",
            )

        template_obj, lookup_error = resolve_eval_template(
            params.eval_template_id,
            context.organization,
            context.workspace,
        )
        if lookup_error:
            return _candidate_eval_templates_result(
                context,
                "Eval Template Not Found",
                f"{lookup_error} Use one of these IDs or exact names.",
                search=params.eval_template_id,
            )

        try:
            template = EvalTemplate.objects.get(id=template_obj.id, deleted=False)
        except EvalTemplate.DoesNotExist:
            return _candidate_eval_templates_result(
                context,
                "Eval Template Not Found",
                f"Eval template `{params.eval_template_id}` was not found.",
            )

        missing_fields = []
        if not params.source_id:
            missing_fields.append("source_id")
        if not params.feedback_value:
            missing_fields.append("feedback_value")
        if missing_fields:
            return ToolResult.needs_input(
                section(
                    "Feedback Details Required",
                    (
                        f"Eval template `{template.name}` was resolved. Provide "
                        "`source_id` and `feedback_value` (`passed` or `failed`) "
                        "to submit feedback."
                    ),
                ),
                data={
                    "eval_template_id": str(template.id),
                    "eval_template_name": template.name,
                    "requires_source_id": "source_id" in missing_fields,
                    "requires_feedback_value": "feedback_value" in missing_fields,
                },
                missing_fields=missing_fields,
            )

        # Validate feedback value
        valid_values = ["passed", "failed"]
        if params.feedback_value.lower() not in valid_values:
            return ToolResult.error(
                f"Invalid feedback_value '{params.feedback_value}'. Must be one of: {', '.join(valid_values)}",
                error_code="VALIDATION_ERROR",
            )

        # Validate source
        valid_sources = [choice.value for choice in FeedbackSourceChoices]
        source = params.source or "eval_playground"
        if source not in valid_sources:
            return ToolResult.error(
                f"Invalid source '{source}'. Must be one of: {', '.join(valid_sources)}",
                error_code="VALIDATION_ERROR",
            )

        try:
            feedback = Feedback(
                source=source,
                source_id=params.source_id,
                eval_template=template,
                value=params.feedback_value.lower(),
                explanation=params.explanation or "",
                feedback_improvement=params.feedback_improvement or "",
                user=context.user,
                row_id=params.row_id,
                organization=context.organization,
                workspace=context.workspace,
            )
            feedback.save()
        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            return ToolResult.error(
                f"Failed to submit feedback: {str(e)}",
                error_code=code_from_exception(e),
            )

        info = key_value_block(
            [
                ("Feedback ID", f"`{feedback.id}`"),
                ("Template", template.name),
                ("Value", params.feedback_value),
                ("Source", source),
                ("Source ID", f"`{params.source_id}`"),
                ("Explanation", params.explanation or "—"),
            ]
        )

        content = section("Eval Feedback Submitted", info)

        return ToolResult(
            content=content,
            data={
                "feedback_id": str(feedback.id),
                "eval_template_id": str(template.id),
                "eval_template_name": template.name,
                "value": params.feedback_value.lower(),
                "source": source,
                "source_id": params.source_id,
            },
        )


def _candidate_eval_templates_result(
    context: ToolContext,
    title: str,
    detail: str = "",
    search: str = "",
) -> ToolResult:
    from django.db.models import Q
    from model_hub.models.evals_metric import EvalTemplate

    qs = EvalTemplate.no_workspace_objects.filter(
        Q(organization=context.organization) | Q(organization__isnull=True),
        deleted=False,
    )
    search = str(search or "").strip()
    if search:
        qs = qs.filter(name__icontains=search)
    templates = list(qs.order_by("-created_at")[:10])
    rows = [
        [f"`{template.id}`", truncate(template.name, 48), template.owner or "unknown"]
        for template in templates
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["ID", "Name", "Owner"], rows
        )
    else:
        body = body or "No eval templates found."
    return ToolResult.needs_input(
        section(title, body),
        data={
            "requires_eval_template_id": True,
            "templates": [
                {"id": str(template.id), "name": template.name}
                for template in templates
            ],
        },
        missing_fields=["eval_template_id"],
    )
