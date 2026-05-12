from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class DeleteTestExecutionInput(PydanticBaseModel):
    test_execution_id: UUID | None = Field(
        default=None,
        description="The UUID of a single test execution to delete.",
    )
    run_test_id: UUID | None = Field(
        default=None,
        description=(
            "The UUID of the RunTest for bulk delete operations. "
            "Use with test_execution_ids or select_all."
        ),
    )
    test_execution_ids: list[UUID] | None = Field(
        default=None,
        description="List of test execution IDs to delete (for bulk operations).",
    )
    select_all: bool = Field(
        default=False,
        description=(
            "If True, delete all test executions in the run test. "
            "When combined with test_execution_ids, those IDs are excluded."
        ),
    )


@register_tool
class DeleteTestExecutionTool(BaseTool):
    name = "delete_test_execution"
    description = (
        "Soft-deletes test executions by marking them as deleted. "
        "Supports single deletion via test_execution_id or bulk deletion "
        "via run_test_id with test_execution_ids or select_all."
    )
    category = "simulation"
    input_model = DeleteTestExecutionInput

    def execute(
        self, params: DeleteTestExecutionInput, context: ToolContext
    ) -> ToolResult:
        from django.utils import timezone
        from simulate.models.test_execution import TestExecution

        now = timezone.now()
        non_deletable_statuses = ["pending", "running", "cancelling"]

        def candidate_executions_result(title: str, detail: str = "") -> ToolResult:
            executions = list(
                TestExecution.objects.select_related("run_test")
                .filter(run_test__organization=context.organization, deleted=False)
                .order_by("-created_at")[:10]
            )
            rows = []
            data = []
            for execution in executions:
                rows.append(
                    [
                        f"`{execution.id}`",
                        execution.run_test.name if execution.run_test else "—",
                        format_status(execution.status),
                        format_datetime(execution.created_at),
                    ]
                )
                data.append(
                    {
                        "id": str(execution.id),
                        "run_test_id": (
                            str(execution.run_test_id)
                            if execution.run_test_id
                            else None
                        ),
                        "status": execution.status,
                    }
                )
            body = detail or (
                "Provide `test_execution_id`, or provide `run_test_id` with "
                "`test_execution_ids` or `select_all=true`."
            )
            if rows:
                body += "\n\n" + markdown_table(
                    ["Execution ID", "Run Test", "Status", "Created"],
                    rows,
                )
            else:
                body += "\n\nNo test executions found in this workspace."
            return ToolResult(
                content=section(title, body),
                data={
                    "requires_test_execution_id_or_run_test_id": True,
                    "executions": data,
                },
            )

        # Single deletion mode
        if params.test_execution_id and not params.run_test_id:
            try:
                execution = TestExecution.objects.select_related("run_test").get(
                    id=params.test_execution_id,
                    run_test__organization=context.organization,
                )
            except TestExecution.DoesNotExist:
                return candidate_executions_result(
                    "Test Execution Not Found",
                    f"Test execution `{params.test_execution_id}` was not found.",
                )

            if execution.status in non_deletable_statuses:
                return ToolResult.error(
                    f"Cannot delete test execution with status '{execution.status}'. "
                    "Only completed, failed, or cancelled executions can be deleted.",
                    error_code="VALIDATION_ERROR",
                )

            run_test_name = execution.run_test.name if execution.run_test else "—"
            execution.deleted = True
            execution.deleted_at = now
            execution.save(update_fields=["deleted", "deleted_at", "updated_at"])

            info = key_value_block(
                [
                    ("Execution ID", f"`{params.test_execution_id}`"),
                    ("Test", run_test_name),
                    ("Status", "Deleted"),
                ]
            )
            content = section("Test Execution Deleted", info)
            return ToolResult(
                content=content,
                data={
                    "id": str(params.test_execution_id),
                    "run_test": run_test_name,
                    "deleted": True,
                },
            )

        # Bulk deletion mode
        if params.run_test_id:
            from simulate.models.run_test import RunTest

            try:
                run_test = RunTest.objects.get(
                    id=params.run_test_id,
                    organization=context.organization,
                    deleted=False,
                )
            except RunTest.DoesNotExist:
                return candidate_executions_result(
                    "Run Test Not Found",
                    f"Run test `{params.run_test_id}` was not found.",
                )

            if params.select_all:
                executions = TestExecution.objects.filter(
                    run_test=run_test, deleted=False
                )
                if params.test_execution_ids:
                    executions = executions.exclude(id__in=params.test_execution_ids)
            elif params.test_execution_ids:
                executions = TestExecution.objects.filter(
                    id__in=params.test_execution_ids,
                    run_test=run_test,
                    deleted=False,
                )
            else:
                return ToolResult.error(
                    "Either provide test_execution_ids, set select_all=True, "
                    "or use test_execution_id for single deletion.",
                    error_code="VALIDATION_ERROR",
                )

            # Check for running executions that cannot be deleted
            running = executions.filter(status__in=non_deletable_statuses)
            if running.exists():
                running_ids = list(running.values_list("id", flat=True)[:5])
                return ToolResult.error(
                    f"Cannot delete test executions that are currently running. "
                    f"Running IDs: {[str(rid) for rid in running_ids]}",
                    error_code="VALIDATION_ERROR",
                )

            count = executions.update(deleted=True, deleted_at=now)

            info = key_value_block(
                [
                    ("Test", run_test.name),
                    ("Executions Deleted", str(count)),
                    ("Status", "Deleted"),
                ]
            )
            content = section("Test Executions Deleted", info)
            return ToolResult(
                content=content,
                data={
                    "run_test_id": str(params.run_test_id),
                    "run_test": run_test.name,
                    "deleted_count": count,
                },
            )

        return candidate_executions_result("Test Execution Required")
