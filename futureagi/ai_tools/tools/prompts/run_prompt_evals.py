from typing import Optional

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import (
    candidate_prompt_eval_configs_result,
    candidate_prompt_versions_result,
    resolve_prompt_eval_configs,
    resolve_prompt_template_for_tool,
    resolve_prompt_version,
)

logger = structlog.get_logger(__name__)


class RunPromptEvalsInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. Omit to list candidates.",
    )
    version_to_run: list[str] = Field(
        default_factory=list,
        description=(
            "List of version strings, names, or IDs to run evals on (e.g. ['v1', 'v2']). "
            "Omit to list candidate versions."
        ),
    )
    prompt_eval_config_ids: list[str] = Field(
        default_factory=list,
        description=(
            "List of PromptEvalConfig IDs or names to run. "
            "Omit to list available eval configs."
        ),
    )
    run_index: Optional[int] = Field(
        default=None,
        description=(
            "Run eval only for a specific variable index (0-based). "
            "If omitted, runs evals for all variable values. "
            "Use this to run evals on a specific variation of variable values."
        ),
    )


@register_tool
class RunPromptEvalsTool(BaseTool):
    name = "run_prompt_evals"
    description = (
        "Runs evaluations on prompt template versions in the Prompt Workbench. "
        "This is the tool required to run evals on a prompt in the workbench — "
        "it executes configured PromptEvalConfigs against the prompt outputs stored "
        "in the specified versions. Use set_eval_config_for_prompt to configure evals first, "
        "then run_prompt to generate output, then this tool to run the evals on that output. "
        "Supports running evals on all variable values or a specific variable index."
    )
    category = "prompts"
    input_model = RunPromptEvalsInput

    def execute(self, params: RunPromptEvalsInput, context: ToolContext) -> ToolResult:
        from django.db import transaction

        from model_hub.models.evals_metric import StatusType
        from model_hub.models.run_prompt import (
            PromptTemplate,
            PromptVersion,
        )
        from model_hub.views.prompt_template import (
            _PROMPT_TEMPLATE_EXECUTOR,
            PromptTemplateViewSet,
            _safe_background_task,
            submit_with_retry,
            track_running_eval_count,
        )

        template, template_result = resolve_prompt_template_for_tool(
            params.template_id,
            context,
            "Prompt Template Required",
        )
        if template_result:
            return template_result

        if not params.version_to_run:
            return candidate_prompt_versions_result(
                template,
                "Prompt Versions Required",
                "Choose one or more versions to evaluate.",
            )

        resolved_versions = []
        seen_version_ids = set()
        for version_ref in params.version_to_run:
            version, version_result = resolve_prompt_version(
                template,
                version_ref,
                "Prompt Version Required",
            )
            if version_result:
                return version_result
            if str(version.id) not in seen_version_ids:
                resolved_versions.append(version)
                seen_version_ids.add(str(version.id))

        version_ids = [str(version.id) for version in resolved_versions]
        version_to_run = [version.template_version for version in resolved_versions]

        if not params.prompt_eval_config_ids:
            return candidate_prompt_eval_configs_result(
                template,
                "Prompt Eval Configs Required",
                "Choose one or more eval configs to run.",
            )

        existing_configs, missing_configs = resolve_prompt_eval_configs(
            template,
            params.prompt_eval_config_ids,
        )
        if missing_configs:
            return candidate_prompt_eval_configs_result(
                template,
                "Prompt Eval Config Not Found",
                "Missing eval config reference(s): "
                + ", ".join(f"`{missing}`" for missing in missing_configs)
                + ". Use one of these config IDs or names.",
            )

        prompt_eval_config_ids = [str(config.id) for config in existing_configs]
        run_index = params.run_index

        # Get executions (prompt versions) — same as the view
        executions = PromptVersion.objects.filter(
            id__in=version_ids,
            original_template=template,
            original_template__organization=context.organization,
            deleted=False,
        )
        if not executions.exists():
            return ToolResult.error(
                f"No versions found matching: {', '.join(version_to_run)}",
                error_code="NOT_FOUND",
            )

        # Check that versions have output — prompt must be run first
        versions_without_output = [
            e.template_version for e in executions if not e.output
        ]
        if versions_without_output:
            return ToolResult.error(
                f"Version(s) {', '.join(versions_without_output)} have no output. "
                "Run the prompt first using run_prompt before running evals.",
                error_code="VALIDATION_ERROR",
            )

        # Validate run_index is within bounds for each execution
        if run_index is not None:
            for execution in executions:
                variable_names = execution.variable_names or {}
                max_len = max(
                    (len(values) for values in variable_names.values()), default=1
                )
                if run_index < 0 or run_index >= max_len:
                    return ToolResult.error(
                        f"run_index {run_index} is out of bounds for version "
                        f"'{execution.template_version}' which has {max_len} variable "
                        f"combination(s) (valid range: 0 to {max_len - 1}).",
                        error_code="VALIDATION_ERROR",
                    )
                # Validate that output exists at the specified index
                if (
                    not execution.output
                    or not isinstance(execution.output, list)
                    or run_index >= len(execution.output)
                    or execution.output[run_index] is None
                ):
                    return ToolResult.error(
                        f"Version '{execution.template_version}' has no output at "
                        f"variable index {run_index}. Run the prompt with those "
                        "variable values first using run_prompt.",
                        error_code="VALIDATION_ERROR",
                    )

        # Initialize eval results and track running count
        # (same logic as run_evals_on_multiple_versions view)
        for execution in executions:
            with transaction.atomic():
                eval_results = execution.evaluation_results or {}
                variable_names = execution.variable_names or {}
                max_len = max(
                    (len(values) for values in variable_names.values()), default=1
                )

                for prompt_eval_config_id in prompt_eval_config_ids:
                    track_running_eval_count(
                        start=True,
                        prompt_config_eval_id=prompt_eval_config_id,
                        operation="set",
                        num=1 if run_index is not None else max_len,
                    )
                    if (
                        run_index is not None
                        and str(prompt_eval_config_id) in eval_results
                        and len(
                            eval_results[str(prompt_eval_config_id)].get("results", [])
                        )
                        > run_index
                    ):
                        eval_results[str(prompt_eval_config_id)]["results"][
                            run_index
                        ] = {"status": StatusType.RUNNING.value}
                    elif (
                        str(prompt_eval_config_id) in eval_results
                        and len(
                            eval_results[str(prompt_eval_config_id)].get("results", [])
                        )
                        > 0
                    ):
                        results = eval_results[str(prompt_eval_config_id)]["results"]
                        for result in results:
                            result["status"] = StatusType.RUNNING.value
                        eval_results[str(prompt_eval_config_id)]["results"] = results

                execution.evaluation_results = eval_results
                execution.save(update_fields=["evaluation_results"])

        # Submit the eval task using the same executor and wrapper as the view
        try:
            viewset = PromptTemplateViewSet()
            submit_with_retry(
                _PROMPT_TEMPLATE_EXECUTOR,
                viewset.run_evals_task,
                template,
                executions,
                prompt_eval_config_ids,
                run_index,
                user_id=str(context.user.id),
            )
        except Exception as e:
            logger.exception("run_prompt_evals_submit_failed", error=str(e))
            return ToolResult.error(
                f"Failed to start evaluation: {str(e)}",
                error_code="INTERNAL_ERROR",
            )

        eval_names = [ec.name for ec in existing_configs]
        info_items = [
            ("Template", template.name),
            ("Versions", ", ".join(version_to_run)),
            ("Evals", ", ".join(eval_names)),
            ("Status", "Running"),
        ]
        if run_index is not None:
            info_items.append(("Variable Index", str(run_index)))

        info = key_value_block(info_items)

        content = section("Prompt Evals Started", info)
        if run_index is not None:
            content += (
                f"\n\n_Running evals only for variable index {run_index}. "
                "Use `get_prompt_template` to check results._"
            )
        else:
            content += (
                "\n\n_Evals are running asynchronously for all variable values. "
                "Use `get_prompt_template` to check results._"
            )

        return ToolResult(
            content=content,
            data={
                "template_id": str(template.id),
                "versions": version_to_run,
                "eval_config_ids": prompt_eval_config_ids,
                "eval_names": eval_names,
                "run_index": run_index,
                "status": "running",
            },
        )
