from django.utils import timezone
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid
from ai_tools.tools.agents._utils import resolve_run_test


class DeleteSimulateEvalConfigInput(PydanticBaseModel):
    run_test_id: str = Field(
        default="",
        description="RunTest name or UUID that owns the eval config",
    )
    eval_config_id: str = Field(
        default="",
        description="Eval config name or UUID to delete",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Set true only after the user confirms this deletion",
    )


@register_tool
class DeleteSimulateEvalConfigTool(BaseTool):
    name = "delete_simulate_eval_config"
    description = (
        "Deletes an evaluation config from a simulation run test. "
        "Cannot delete the last eval config — at least one must remain."
    )
    category = "simulation"
    input_model = DeleteSimulateEvalConfigInput

    def execute(
        self, params: DeleteSimulateEvalConfigInput, context: ToolContext
    ) -> ToolResult:
        from simulate.models.eval_config import SimulateEvalConfig

        def candidate_configs_result(run_test, title: str, detail: str = ""):
            configs = list(
                SimulateEvalConfig.objects.filter(
                    run_test=run_test,
                    deleted=False,
                ).order_by("-created_at")[:10]
            )
            rows = [
                [
                    f"`{config.id}`",
                    config.name or "-",
                    (
                        config.eval_template.name
                        if getattr(config, "eval_template", None)
                        else "-"
                    ),
                    format_datetime(config.created_at),
                ]
                for config in configs
            ]
            body = detail or ""
            if rows:
                body = (body + "\n\n" if body else "") + markdown_table(
                    ["ID", "Name", "Template", "Created"], rows
                )
            else:
                body = body or f"No eval configs found on `{run_test.name}`."
            return ToolResult(
                content=section(title, body),
                data={
                    "requires_eval_config_id": True,
                    "run_test_id": str(run_test.id),
                    "configs": [
                        {"id": str(config.id), "name": config.name}
                        for config in configs
                    ],
                },
            )

        run_test, unresolved = resolve_run_test(
            params.run_test_id,
            context,
            title="Run Test Required To Delete Eval Config",
        )
        if unresolved:
            return unresolved

        config_ref = str(params.eval_config_id or "").strip()
        if not config_ref:
            return candidate_configs_result(
                run_test,
                "Eval Config Required",
                "Provide `eval_config_id` to preview deletion.",
            )

        qs = SimulateEvalConfig.objects.filter(run_test=run_test, deleted=False)
        if is_uuid(config_ref):
            eval_config = qs.filter(id=config_ref).first()
        else:
            exact = qs.filter(name__iexact=config_ref)
            eval_config = exact.first() if exact.count() == 1 else None
            if eval_config is None:
                fuzzy = qs.filter(name__icontains=config_ref)
                eval_config = fuzzy.first() if fuzzy.count() == 1 else None
        if eval_config is None:
            return candidate_configs_result(
                run_test,
                "Eval Config Not Found",
                f"Eval config `{config_ref}` was not found on `{run_test.name}`.",
            )

        # Ensure at least one eval config remains
        active_count = SimulateEvalConfig.objects.filter(
            run_test=run_test, deleted=False
        ).count()
        if active_count <= 1:
            return ToolResult.error(
                "Cannot delete the last evaluation config. "
                "At least one evaluation config must remain.",
                error_code="VALIDATION_ERROR",
            )

        if not params.confirm_delete:
            return ToolResult(
                content=section(
                    "Eval Config Delete Preview",
                    (
                        f"Deletion is ready for `{eval_config.name}` (`{eval_config.id}`) "
                        f"on `{run_test.name}`. Set `confirm_delete=true` after user confirmation."
                    ),
                ),
                data={
                    "requires_confirmation": True,
                    "run_test_id": str(run_test.id),
                    "eval_config_id": str(eval_config.id),
                    "name": eval_config.name,
                },
            )

        eval_name = eval_config.name
        eval_config.deleted = True
        eval_config.deleted_at = timezone.now()
        eval_config.save(update_fields=["deleted", "deleted_at"])

        info = key_value_block(
            [
                ("Eval Config", eval_name),
                ("ID", f"`{params.eval_config_id}`"),
                ("Test", run_test.name),
                ("Status", "Deleted"),
            ]
        )

        content = section("Evaluation Config Deleted", info)

        return ToolResult(
            content=content,
            data={
                "eval_config_id": str(params.eval_config_id),
                "eval_config_name": eval_name,
                "run_test_id": str(params.run_test_id),
                "deleted": True,
                "deleted_at": timezone.now(),
            },
        )
