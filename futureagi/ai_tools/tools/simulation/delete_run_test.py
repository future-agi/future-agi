from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.agents._utils import resolve_run_test


class DeleteRunTestInput(PydanticBaseModel):
    run_test_id: str = Field(
        default="",
        description="Run test name or UUID to delete. If omitted, candidates are returned.",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Set true only after the user confirms deletion.",
    )


@register_tool
class DeleteRunTestTool(BaseTool):
    name = "delete_run_test"
    description = "Soft-deletes a test suite (RunTest) by marking it as deleted."
    category = "simulation"
    input_model = DeleteRunTestInput

    def execute(self, params: DeleteRunTestInput, context: ToolContext) -> ToolResult:
        from django.utils import timezone

        run_test, unresolved = resolve_run_test(
            params.run_test_id,
            context,
            title="Run Test Required To Delete",
        )
        if unresolved:
            return unresolved

        if not params.confirm_delete:
            info = key_value_block(
                [
                    ("ID", f"`{run_test.id}`"),
                    ("Name", run_test.name),
                    ("Status", "Awaiting confirmation"),
                ]
            )
            return ToolResult(
                content=section("Confirm Test Suite Deletion", info),
                data={
                    "requires_confirmation": True,
                    "confirm_delete": True,
                    "id": str(run_test.id),
                    "name": run_test.name,
                },
            )

        run_test_name = run_test.name
        run_test.deleted = True
        run_test.deleted_at = timezone.now()
        run_test.save(update_fields=["deleted", "deleted_at", "updated_at"])

        info = key_value_block(
            [
                ("ID", f"`{run_test.id}`"),
                ("Name", run_test_name),
                ("Status", "Deleted"),
            ]
        )

        content = section("Test Suite Deleted", info)

        return ToolResult(
            content=content,
            data={
                "id": str(run_test.id),
                "name": run_test_name,
                "deleted": True,
            },
        )
