from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import section
from ai_tools.registry import register_tool


class DeleteEvalTemplateInput(PydanticBaseModel):
    eval_template_id: UUID = Field(
        description="The UUID of the eval template to delete"
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

        try:
            template = EvalTemplate.objects.get(
                id=params.eval_template_id,
                organization=context.organization,
                owner=OwnerChoices.USER.value,
                deleted=False,
            )
        except EvalTemplate.DoesNotExist:
            return ToolResult.not_found(
                "User-owned Eval Template", str(params.eval_template_id)
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
            data={"id": str(params.eval_template_id), "name": name},
        )
