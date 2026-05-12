from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class ListSimulateEvalConfigsInput(PydanticBaseModel):
    run_test_id: Optional[UUID] = Field(
        default=None,
        description="The UUID of the RunTest (test suite) to list eval configs for. Omit it to list candidate test suites.",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")


@register_tool
class ListSimulateEvalConfigsTool(BaseTool):
    name = "list_simulate_eval_configs"
    description = (
        "Lists evaluation configs configured on a simulation run test (test suite). "
        "These configs define which evals run on call executions during test runs. "
        "Use this to see what evaluations are set up for a test suite."
    )
    category = "simulation"
    input_model = ListSimulateEvalConfigsInput

    def execute(
        self, params: ListSimulateEvalConfigsInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.eval_config import SimulateEvalConfig
        from simulate.models.run_test import RunTest

        if params.run_test_id is None:
            run_tests = (
                RunTest.objects.filter(
                    organization=context.organization,
                    deleted=False,
                )
                .order_by("-created_at")[:10]
            )
            rows = [
                [f"`{run_test.id}`", run_test.name, format_datetime(run_test.created_at)]
                for run_test in run_tests
            ]
            if not rows:
                return ToolResult(
                    content=section("Run Test Candidates", "No run tests found."),
                    data={"run_tests": []},
                )
            return ToolResult(
                content=section(
                    "Run Test Candidates",
                    markdown_table(["ID", "Name", "Created"], rows),
                ),
                data={
                    "run_tests": [
                        {"id": str(run_test.id), "name": run_test.name}
                        for run_test in run_tests
                    ]
                },
            )

        try:
            run_test = RunTest.objects.get(
                id=params.run_test_id,
                organization=context.organization,
            )
        except RunTest.DoesNotExist:
            return ToolResult.not_found("RunTest", str(params.run_test_id))

        qs = SimulateEvalConfig.objects.filter(
            run_test=run_test,
            deleted=False,
        ).select_related("eval_template", "eval_group")

        total = qs.count()
        configs = qs[params.offset : params.offset + params.limit]

        if not configs:
            return ToolResult(
                content=section(
                    f"Eval Configs: {run_test.name}",
                    "_No eval configs found on this test suite. "
                    "Use `create_simulate_eval_config` to add one._",
                ),
                data={"configs": [], "total": 0},
            )

        rows = []
        data_list = []
        for cfg in configs:
            template_name = cfg.eval_template.name if cfg.eval_template else "—"
            group_name = cfg.eval_group.name if cfg.eval_group else "—"
            model = cfg.model or "—"
            mapping_str = truncate(str(cfg.mapping), 40) if cfg.mapping else "—"

            rows.append(
                [
                    f"`{cfg.id}`",
                    cfg.name or template_name,
                    template_name,
                    model,
                    group_name,
                    mapping_str,
                    format_datetime(cfg.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(cfg.id),
                    "name": cfg.name or template_name,
                    "eval_template_id": str(cfg.eval_template_id),
                    "eval_template_name": template_name,
                    "model": cfg.model,
                    "mapping": cfg.mapping,
                    "config": cfg.config,
                    "error_localizer": cfg.error_localizer,
                }
            )

        table = markdown_table(
            ["ID", "Name", "Template", "Model", "Group", "Mapping", "Created"],
            rows,
        )

        content = section(
            f"Eval Configs: {run_test.name} ({total})",
            f"Showing {len(rows)} of {total}\n\n{table}",
        )

        return ToolResult(content=content, data={"configs": data_list, "total": total})
