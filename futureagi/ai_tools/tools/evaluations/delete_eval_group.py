from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import format_datetime, markdown_table, section
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid


class DeleteEvalGroupInput(PydanticBaseModel):
    eval_group_id: str = Field(
        default="",
        description="Eval group name or UUID to delete",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Set true only after the user confirms this deletion",
    )


@register_tool
class DeleteEvalGroupTool(BaseTool):
    name = "delete_eval_group"
    description = (
        "Deletes an evaluation group (soft delete). "
        "This removes the group and clears all template associations."
    )
    category = "evaluations"
    input_model = DeleteEvalGroupInput

    def execute(self, params: DeleteEvalGroupInput, context: ToolContext) -> ToolResult:

        from model_hub.models.eval_groups import EvalGroup

        def candidate_groups_result(title: str, detail: str = "") -> ToolResult:
            groups = list(
                EvalGroup.objects.filter(
                    organization=context.organization,
                    deleted=False,
                ).order_by("-created_at")[:10]
            )
            rows = [
                [f"`{group.id}`", group.name, format_datetime(group.created_at)]
                for group in groups
            ]
            body = detail or ""
            if rows:
                body = (body + "\n\n" if body else "") + markdown_table(
                    ["ID", "Name", "Created"], rows
                )
            else:
                body = body or "No eval groups found."
            return ToolResult(
                content=section(title, body),
                data={
                    "requires_eval_group_id": True,
                    "groups": [
                        {"id": str(group.id), "name": group.name} for group in groups
                    ],
                },
            )

        group_ref = str(params.eval_group_id or "").strip()
        if not group_ref:
            return candidate_groups_result(
                "Eval Group Required",
                "Provide `eval_group_id` to preview deletion.",
            )

        qs = EvalGroup.objects.filter(
            organization=context.organization,
            deleted=False,
        )
        if is_uuid(group_ref):
            group = qs.filter(id=group_ref).first()
        else:
            exact = qs.filter(name__iexact=group_ref)
            group = exact.first() if exact.count() == 1 else None
            if group is None:
                fuzzy = qs.filter(name__icontains=group_ref)
                group = fuzzy.first() if fuzzy.count() == 1 else None
        if group is None:
            return candidate_groups_result(
                "Eval Group Not Found",
                f"Eval group `{group_ref}` was not found.",
            )

        if not params.confirm_delete:
            return ToolResult(
                content=section(
                    "Eval Group Delete Preview",
                    (
                        f"Deletion is ready for `{group.name}` (`{group.id}`). "
                        "Set `confirm_delete=true` after user confirmation to delete it."
                    ),
                ),
                data={
                    "requires_confirmation": True,
                    "eval_group_id": str(group.id),
                    "name": group.name,
                },
            )

        from django.utils import timezone

        name = group.name

        # Soft delete + clear M2M
        group.deleted = True
        group.deleted_at = timezone.now()
        group.save(update_fields=["deleted", "deleted_at"])
        group.eval_templates.through.objects.filter(evalgroup_id=group.id).delete()

        return ToolResult(
            content=section(
                "Eval Group Deleted", f"Group **{name}** has been deleted."
            ),
            data={"id": str(group.id), "name": name},
        )
