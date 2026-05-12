from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import format_datetime, markdown_table, section
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid


class DeleteEvalTemplateInput(PydanticBaseModel):
    eval_template_id: str = Field(
        default="",
        description="User-owned eval template name or UUID to delete",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Set true only after the user confirms this deletion",
    )


@register_tool
class DeleteEvalTemplateTool(BaseTool):
    name = "delete_eval_template"
    description = (
        "Deletes a user-owned evaluation template (soft delete). "
        "This cascades to related eval metrics, prompt eval configs, "
        "custom eval configs, inline evals, external eval configs, and API call logs. "
        "Only USER-owned templates can be deleted."
    )
    category = "evaluations"
    input_model = DeleteEvalTemplateInput

    def execute(
        self, params: DeleteEvalTemplateInput, context: ToolContext
    ) -> ToolResult:
        from django.db import transaction
        from django.utils import timezone
        from model_hub.models.choices import OwnerChoices
        from model_hub.models.evals_metric import EvalTemplate

        def candidate_templates_result(title: str, detail: str = "") -> ToolResult:
            templates = list(
                EvalTemplate.objects.filter(
                    organization=context.organization,
                    owner=OwnerChoices.USER.value,
                    deleted=False,
                ).order_by("-created_at")[:10]
            )
            rows = [
                [
                    f"`{template.id}`",
                    template.name,
                    format_datetime(template.created_at),
                ]
                for template in templates
            ]
            body = detail or ""
            if rows:
                body = (body + "\n\n" if body else "") + markdown_table(
                    ["ID", "Name", "Created"], rows
                )
            else:
                body = body or "No user-owned eval templates found."
            return ToolResult(
                content=section(title, body),
                data={
                    "requires_eval_template_id": True,
                    "templates": [
                        {"id": str(template.id), "name": template.name}
                        for template in templates
                    ],
                },
            )

        template_ref = str(params.eval_template_id or "").strip()
        if not template_ref:
            return candidate_templates_result(
                "Eval Template Required",
                "Provide `eval_template_id` to preview deletion.",
            )

        qs = EvalTemplate.objects.filter(
            organization=context.organization,
            owner=OwnerChoices.USER.value,
            deleted=False,
        )
        if is_uuid(template_ref):
            template = qs.filter(id=template_ref).first()
        else:
            exact = qs.filter(name__iexact=template_ref)
            template = exact.first() if exact.count() == 1 else None
            if template is None:
                fuzzy = qs.filter(name__icontains=template_ref)
                template = fuzzy.first() if fuzzy.count() == 1 else None
        if template is None:
            return candidate_templates_result(
                "Eval Template Not Found",
                f"User-owned eval template `{template_ref}` was not found.",
            )

        if not params.confirm_delete:
            return ToolResult(
                content=section(
                    "Eval Template Delete Preview",
                    (
                        f"Deletion is ready for `{template.name}` (`{template.id}`). "
                        "Set `confirm_delete=true` after user confirmation to delete it."
                    ),
                ),
                data={
                    "requires_confirmation": True,
                    "eval_template_id": str(template.id),
                    "name": template.name,
                },
            )

        name = template.name
        now = timezone.now()

        with transaction.atomic():
            template.deleted = True
            template.deleted_at = now
            template.save(update_fields=["deleted", "deleted_at"])

            # Cascade soft-delete to related objects
            from model_hub.models.evals_metric import UserEvalMetric

            UserEvalMetric.objects.filter(template=template).update(
                deleted=True, deleted_at=now
            )

            try:
                from model_hub.models.run_prompt import PromptEvalConfig

                PromptEvalConfig.objects.filter(eval_template=template).update(
                    deleted=True, deleted_at=now
                )
            except Exception as e:
                import structlog

                structlog.get_logger(__name__).warning(
                    "cascade_delete_failed", model="PromptEvalConfig", error=str(e)
                )

            try:
                from tracer.models.custom_eval_config import CustomEvalConfig

                CustomEvalConfig.objects.filter(eval_template=template).update(
                    deleted=True, deleted_at=now
                )
            except Exception as e:
                import structlog

                structlog.get_logger(__name__).warning(
                    "cascade_delete_failed", model="CustomEvalConfig", error=str(e)
                )

            try:
                from tracer.models.external_eval_config import ExternalEvalConfig

                ExternalEvalConfig.objects.filter(eval_template=template).update(
                    deleted=True, deleted_at=now
                )
            except Exception as e:
                import structlog

                structlog.get_logger(__name__).warning(
                    "cascade_delete_failed", model="ExternalEvalConfig", error=str(e)
                )

            try:
                from tracer.models.custom_eval_config import InlineEval

                InlineEval.objects.filter(evaluation__eval_template=template).update(
                    deleted=True, deleted_at=now
                )
            except Exception as e:
                import structlog

                structlog.get_logger(__name__).warning(
                    "cascade_delete_failed", model="InlineEval", error=str(e)
                )

            try:
                from ee.usage.models.usage import APICallLog

                APICallLog.objects.filter(source_id=str(template.id)).update(
                    deleted=True, deleted_at=now
                )
            except ImportError:
                # No APICallLog to cascade to when ee is absent.
                pass
            except Exception as e:
                import structlog

                structlog.get_logger(__name__).warning(
                    "cascade_delete_failed", model="APICallLog", error=str(e)
                )

            try:
                from tracer.models.observation_span import EvalLogger

                EvalLogger.objects.filter(
                    custom_eval_config__eval_template=template
                ).update(deleted=True, deleted_at=now)
            except Exception as e:
                import structlog

                structlog.get_logger(__name__).warning(
                    "cascade_delete_failed", model="EvalLogger", error=str(e)
                )

        return ToolResult(
            content=section(
                "Eval Template Deleted",
                f"Template **{name}** and all related configurations have been deleted.",
            ),
            data={"id": str(template.id), "name": name},
        )
