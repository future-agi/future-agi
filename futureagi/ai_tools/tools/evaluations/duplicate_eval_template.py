from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, markdown_table, section
from ai_tools.registry import register_tool


class DuplicateEvalTemplateInput(PydanticBaseModel):
    eval_template_id: str = Field(
        description="Name or UUID of the eval template to duplicate"
    )
    name: str = Field(
        description="Name for the new duplicated template",
        min_length=1,
        max_length=2000,
    )


def _user_template_candidates_result(
    context: ToolContext,
    title: str = "User-Owned Eval Template Required",
    detail: str = "",
) -> ToolResult:
    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate

    templates = list(
        EvalTemplate.objects.filter(
            organization=context.organization,
            owner=OwnerChoices.USER.value,
            deleted=False,
        ).order_by("-created_at")[:10]
    )
    rows = [[f"`{template.id}`", template.name] for template in templates]
    body = detail or "Choose a user-owned eval template to duplicate."
    if rows:
        body += "\n\n" + markdown_table(["ID", "Name"], rows)
    else:
        body += "\n\nNo user-owned eval templates found."
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


def _suggest_unique_name(base_name: str, existing_names: set[str]) -> str:
    clean_base = base_name.strip()
    for suffix in range(2, 100):
        candidate = f"{clean_base}_{suffix}"
        if candidate not in existing_names:
            return candidate
    return f"{clean_base}_copy"


@register_tool
class DuplicateEvalTemplateTool(BaseTool):
    name = "duplicate_eval_template"
    description = (
        "Duplicates a user-owned evaluation template with a new name. "
        "All fields are copied except ID, timestamps, and name. "
        "Only USER-owned templates can be duplicated."
    )
    category = "evaluations"
    input_model = DuplicateEvalTemplateInput

    def execute(
        self, params: DuplicateEvalTemplateInput, context: ToolContext
    ) -> ToolResult:
        from django.utils import timezone

        from ai_tools.resolvers import resolve_eval_template
        from model_hub.models.choices import OwnerChoices
        from model_hub.models.evals_metric import EvalTemplate

        template_obj, err = resolve_eval_template(
            params.eval_template_id, context.organization
        )
        if err:
            return _user_template_candidates_result(
                context,
                "Eval Template Not Found",
                f"{err} Use one of these user-owned template IDs instead.",
            )

        try:
            template = EvalTemplate.objects.get(
                id=template_obj.id,
                organization=context.organization,
                owner=OwnerChoices.USER.value,
                deleted=False,
            )
        except EvalTemplate.DoesNotExist:
            return _user_template_candidates_result(
                context,
                "User-Owned Eval Template Required",
                (
                    f"Template `{template_obj.name}` (`{template_obj.id}`) is not "
                    "a user-owned template that can be duplicated. Choose one of these instead."
                ),
            )

        # Validate name pattern
        import re

        clean_name = params.name.strip()
        if not re.match(r"^[0-9a-z_-]+$", clean_name):
            return ToolResult.error(
                "Name can only contain lowercase alphabets, numbers, hyphens (-), or underscores (_).",
                error_code="VALIDATION_ERROR",
            )
        if clean_name[0] in "-_" or clean_name[-1] in "-_":
            return ToolResult.error(
                "Name cannot start or end with hyphens (-) or underscores (_).",
                error_code="VALIDATION_ERROR",
            )
        if "_-" in clean_name or "-_" in clean_name:
            return ToolResult.error(
                "Name cannot contain consecutive mixed separators (_- or -_).",
                error_code="VALIDATION_ERROR",
            )

        # Check name uniqueness
        existing_qs = EvalTemplate.objects.filter(
            name=params.name,
            organization=context.organization,
            owner=OwnerChoices.USER.value,
            deleted=False,
        )
        existing_template = existing_qs.first()
        if existing_template:
            existing_names = set(
                EvalTemplate.objects.filter(
                    organization=context.organization,
                    owner=OwnerChoices.USER.value,
                    deleted=False,
                    name__startswith=clean_name,
                ).values_list("name", flat=True)
            )
            suggested_name = _suggest_unique_name(clean_name, existing_names)
            info = key_value_block(
                [
                    ("Existing ID", f"`{existing_template.id}`"),
                    ("Existing Name", existing_template.name),
                    ("Suggested New Name", f"`{suggested_name}`"),
                ]
            )
            return ToolResult.needs_input(
                section(
                    "Eval Template Name Already Exists",
                    (
                        "The requested duplicate name is already used. "
                        "Call `duplicate_eval_template` again with the suggested "
                        "name or another unique lowercase name.\n\n"
                        f"{info}"
                    ),
                ),
                data={
                    "requires_name": True,
                    "existing_template_id": str(existing_template.id),
                    "existing_name": existing_template.name,
                    "suggested_name": suggested_name,
                    "source_id": str(template.id),
                    "source_name": template.name,
                },
                missing_fields=["name"],
            )

        # Copy all fields except id, timestamps, and name
        now = timezone.now()
        fields_to_copy = {
            field.name: getattr(template, field.name)
            for field in template._meta.fields
            if field.name not in ["id", "created_at", "updated_at", "name"]
        }
        fields_to_copy["name"] = params.name
        fields_to_copy["organization"] = context.organization
        fields_to_copy["created_at"] = now
        fields_to_copy["updated_at"] = now

        new_template = EvalTemplate.objects.create(**fields_to_copy)

        info = key_value_block(
            [
                ("New ID", f"`{new_template.id}`"),
                ("Name", new_template.name),
                ("Cloned From", f"`{template.id}` ({template.name})"),
            ]
        )

        return ToolResult(
            content=section("Eval Template Duplicated", info),
            data={
                "id": str(new_template.id),
                "name": new_template.name,
                "source_id": str(template.id),
                "source_name": template.name,
            },
        )
