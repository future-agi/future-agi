from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import (
    resolve_prompt_template_for_tool,
    resolve_prompt_version,
)


class CommitPromptVersionInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. Omit to list candidates.",
    )
    version_name: str = Field(
        default="",
        description="The version name or UUID to commit (e.g., 'v1', 'v2')",
    )
    message: Optional[str] = Field(default=None, description="Commit message")
    set_default: bool = Field(default=False, description="Set this version as default")


@register_tool
class CommitPromptVersionTool(BaseTool):
    name = "commit_prompt_version"
    description = (
        "Commits a draft prompt version, optionally setting it as default. "
        "Adds a commit message to the version and marks it as committed (non-draft)."
    )
    category = "prompts"
    input_model = CommitPromptVersionInput

    def execute(
        self, params: CommitPromptVersionInput, context: ToolContext
    ) -> ToolResult:
        from django.utils import timezone

        template, template_result = resolve_prompt_template_for_tool(
            params.template_id,
            context,
            "Prompt Template Required",
        )
        if template_result:
            return template_result

        version, version_result = resolve_prompt_version(
            template,
            params.version_name,
            "Prompt Version Required",
        )
        if version_result:
            return version_result

        version.commit_message = params.message or ""
        version.is_draft = False
        if params.set_default:
            version.is_default = True
        version.updated_at = timezone.now()
        version.save(
            update_fields=["is_default", "commit_message", "updated_at", "is_draft"]
        )

        info = key_value_block(
            [
                ("Template", template.name),
                ("Version", version.template_version),
                ("Commit Message", version.commit_message or "—"),
                ("Is Default", "Yes" if version.is_default else "No"),
            ]
        )

        content = section("Version Committed", info)

        return ToolResult(
            content=content,
            data={
                "template_id": str(template.id),
                "version_id": str(version.id),
                "version": version.template_version,
                "is_default": version.is_default,
            },
        )
