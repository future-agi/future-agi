import copy
import re

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section, truncate
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import resolve_prompt_version

logger = structlog.get_logger(__name__)


class RunPromptInput(PydanticBaseModel):
    template_id: str = Field(
        default="", description="Name or UUID of the prompt template to execute"
    )
    version_id: str = Field(
        default="",
        description="Specific version UUID or name to run. If omitted, uses the default version.",
    )
    variables: dict | None = Field(
        default=None,
        description=(
            "Variable substitutions for {{var}} placeholders in the prompt. "
            'Example: {"name": "Alice", "topic": "science"}'
        ),
    )
    model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Model to use for this execution (e.g., 'gpt-4o', 'claude-sonnet-4-20250514')",
    )


@register_tool
class RunPromptTool(BaseTool):
    name = "run_prompt"
    description = (
        "Executes a prompt template by sending it to an LLM. "
        "Substitutes variables, uses the specified or default version, "
        "and returns the model's response. "
        "Use get_prompt_template to see available variables first."
    )
    category = "prompts"
    input_model = RunPromptInput

    def execute(self, params: RunPromptInput, context: ToolContext) -> ToolResult:
        from django.utils import timezone
        from model_hub.models.run_prompt import PromptTemplate, PromptVersion

        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        # Resolve template by name or UUID
        from ai_tools.resolvers import resolve_prompt_template
        from model_hub.services.derived_variable_service import (
            extract_derived_variables_from_output,
        )
        from model_hub.utils.column_utils import is_json_response_format
        from model_hub.utils.utils import remove_empty_text_from_messages
        from model_hub.views.prompt_template import replace_variables

        try:
            from ee.usage.utils.usage_entries import (
                APICallTypeChoices,
                log_and_deduct_cost_for_api_request,
            )
        except ImportError:
            APICallTypeChoices = None
            log_and_deduct_cost_for_api_request = None

        if not params.template_id:
            templates = PromptTemplate.objects.filter(
                organization=context.organization,
                deleted=False,
            ).order_by("-created_at")[:10]
            rows = [[f"`{template.id}`", template.name] for template in templates]
            if not rows:
                return ToolResult(
                    content=section(
                        "Prompt Template Candidates", "No prompt templates found."
                    ),
                    data={"templates": []},
                )
            from ai_tools.formatting import markdown_table

            return ToolResult(
                content=section(
                    "Prompt Template Candidates",
                    markdown_table(["ID", "Name"], rows),
                ),
                data={
                    "templates": [
                        {"id": str(template.id), "name": template.name}
                        for template in templates
                    ]
                },
            )

        template_obj, err = resolve_prompt_template(
            params.template_id, context.organization, context.workspace
        )
        if err:
            return ToolResult.error(err, error_code="NOT_FOUND")

        try:
            template = PromptTemplate.objects.get(
                id=template_obj.id,
                organization=context.organization,
                deleted=False,
            )
        except PromptTemplate.DoesNotExist:
            return ToolResult.not_found("Prompt Template", str(template_obj.id))

        # Get version
        if params.version_id:
            version, version_result = resolve_prompt_version(
                template,
                params.version_id,
                "Prompt Version Required",
            )
            if version_result:
                return version_result
        else:
            # Get default version (same fallback chain as WebSocket path)
            version = PromptVersion.objects.filter(
                original_template=template, deleted=False, is_default=True
            ).first()
            if not version:
                version = (
                    PromptVersion.objects.filter(
                        original_template=template, deleted=False, is_draft=False
                    )
                    .order_by("-created_at")
                    .first()
                )
            if not version:
                version = (
                    PromptVersion.objects.filter(
                        original_template=template, deleted=False
                    )
                    .order_by("-created_at")
                    .first()
                )
            if not version:
                return ToolResult.error(
                    f"No versions found for template '{template.name}'. "
                    "Create a version first with create_prompt_version.",
                    error_code="NOT_FOUND",
                )

        if not version.prompt_config_snapshot:
            return ToolResult.error(
                "Prompt version has no config snapshot.",
                error_code="VALIDATION_ERROR",
            )

        # Deep copy the config to avoid mutating the stored version
        prompt_configs = copy.deepcopy(version.prompt_config_snapshot)
        # Handle both dict (new format) and list (legacy) storage
        if isinstance(prompt_configs, dict):
            config = prompt_configs
        elif isinstance(prompt_configs, list) and prompt_configs:
            config = prompt_configs[0]
        else:
            return ToolResult.error(
                "Invalid prompt config format in version.",
                error_code="VALIDATION_ERROR",
            )

        configuration = config.get("configuration", {})
        model = params.model

        # Override model in configuration so RunPrompt uses the user-specified model
        configuration["model"] = model

        # Determine variable indices to process
        variables = params.variables or {}
        variable_names = version.variable_names or {}
        max_len = max((len(values) for values in variable_names.values()), default=1)

        # Determine which index to run based on provided variables
        if variables and variable_names:
            var_index = 0
            for idx in range(max_len):
                combo = {
                    k: vals[idx] if idx < len(vals) else None
                    for k, vals in variable_names.items()
                }
                if combo == variables:
                    var_index = idx
                    break
        else:
            var_index = 0

        # Build the variable combination for substitution
        variable_combination = {
            key: values[var_index] if var_index < len(values) else None
            for key, values in variable_names.items()
        }
        # Override with user-provided variables
        variable_combination.update(variables)

        # Use the same replace_variables and remove_empty_text_from_messages
        # as the WebSocket path (run_template_async)
        prompt_messages = config.get("messages", []).copy()
        messages_with_replacement = replace_variables(
            prompt_messages, variable_combination, model,
            template_format=config.get("configuration", {}).get("template_format"),
        )
        messages_with_replacement = remove_empty_text_from_messages(
            messages_with_replacement
        )

        # Check for unsubstituted variables
        remaining_vars = set()
        for msg in messages_with_replacement:
            content = msg.get("content", "")
            if isinstance(content, str):
                remaining_vars.update(re.findall(r"\{\{(\w+)\}\}", content))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        remaining_vars.update(
                            re.findall(r"\{\{(\w+)\}\}", part.get("text", ""))
                        )

        if remaining_vars:
            return ToolResult.error(
                f"Unsubstituted variables found: {', '.join(f'`{{{{{v}}}}}`' for v in remaining_vars)}. "
                "Provide values in the `variables` parameter.",
                error_code="VALIDATION_ERROR",
            )

        # Extract tools from config (same as run_template_async)
        tools_to_send = [
            tool.get("config")
            for tool in configuration.get("tools", [])
            if tool.get("config")
        ]

        # Extract output_format from config
        output_format = configuration.get("output_format", "string")

        # Use RunPrompt — the same class used by the WebSocket path
        # This ensures model resolution, provider detection, API key handling,
        # payload building, and response formatting are all identical.
        try:
            # Usage check requires ee — skip when unavailable and allow the
            # prompt to run.
            try:
                from ee.usage.schemas.event_types import BillingEventType
                from ee.usage.services.metering import check_usage

                usage_check = check_usage(
                    str(context.organization.id),
                    BillingEventType.AI_PROMPT_CREATION,
                )
                if not usage_check.allowed:
                    raise ValueError(usage_check.reason or "Usage limit exceeded")
            except ImportError:
                pass

            run_prompt = RunPrompt(
                model=model,
                organization_id=context.organization.id,
                messages=messages_with_replacement,
                temperature=configuration.get("temperature"),
                frequency_penalty=configuration.get("frequency_penalty"),
                presence_penalty=configuration.get("presence_penalty"),
                max_tokens=configuration.get("max_tokens"),
                top_p=configuration.get("top_p"),
                response_format=configuration.get("response_format"),
                tool_choice=configuration.get("tool_choice"),
                tools=tools_to_send,
                output_format=output_format,
                ws_manager=None,  # No WebSocket for MCP tool
                workspace_id=context.workspace.id if context.workspace else None,
                run_prompt_config=configuration,
            )

            # Execute using non-streaming sync path (same handler chain)
            response_content, value_info = run_prompt.litellm_response(
                streaming=False,
                template_id=template.id,
                version=version.template_version,
                index=var_index,
                max_index=max_len,
                run_type="run_prompt",
            )

            metadata = value_info.get("metadata", {})
            token_config = metadata.get("usage", {})

            # Log usage and deduct cost (skipped when ee is absent).
            if log_and_deduct_cost_for_api_request is not None:
                try:
                    log_and_deduct_cost_for_api_request(
                        context.organization,
                        APICallTypeChoices.PROMPT_BENCH.value,
                        config=token_config,
                        source="run_prompt_gen",
                        workspace=context.workspace,
                    )
                except Exception as cost_err:
                    logger.warning("failed_to_log_usage", error=str(cost_err))

            # Dual-write: emit usage event for new billing system (ee-only).
            try:
                from ee.usage.schemas.events import UsageEvent
                from ee.usage.services.emitter import emit

                emit(
                    UsageEvent(
                        org_id=str(context.organization.id),
                        event_type=APICallTypeChoices.PROMPT_BENCH.value,
                        properties={
                            "source": "run_prompt_gen",
                            "source_id": str(template.id),
                        },
                    )
                )
            except ImportError:
                pass  # No usage emitter when ee is absent.
            except Exception:
                pass  # Metering failure must not break the action

            # Persist response to PromptVersion (same as run_template_async)
            try:
                responses = list(version.output) if version.output else [None] * max_len
                metadata_list = (
                    list(version.metadata) if version.metadata else [None] * max_len
                )

                # Extend lists if needed
                while len(responses) < max_len:
                    responses.append(None)
                while len(metadata_list) < max_len:
                    metadata_list.append(None)

                responses[var_index] = response_content
                metadata_list[var_index] = metadata

                version.output = responses
                version.metadata = metadata_list
                version.is_draft = False
                version.updated_at = timezone.now()
                version.save(
                    update_fields=["output", "metadata", "is_draft", "updated_at"]
                )

                # Extract derived variables from JSON outputs
                # (same as run_template_async)
                response_format = configuration.get("response_format")
                if is_json_response_format(response_format):
                    try:
                        if responses and responses[0]:
                            derived_vars = extract_derived_variables_from_output(
                                responses[0], "output"
                            )
                            if derived_vars.get("is_json"):
                                current_metadata = version.metadata or [{}]
                                if current_metadata and isinstance(
                                    current_metadata, list
                                ):
                                    if not current_metadata[0]:
                                        current_metadata[0] = {}
                                    current_metadata[0]["derived_variables"] = {
                                        "output": derived_vars
                                    }
                                    version.metadata = current_metadata
                                    version.save(update_fields=["metadata"])
                    except Exception as e:
                        logger.warning(
                            "failed_to_extract_derived_variables", error=str(e)
                        )

            except Exception as persist_err:
                logger.warning(
                    "failed_to_persist_prompt_response", error=str(persist_err)
                )

            # Build response for MCP tool
            usage = token_config
            info = key_value_block(
                [
                    ("Template", template.name),
                    ("Version", version.template_version),
                    ("Model", model),
                    (
                        "Input Tokens",
                        str(usage.get("prompt_tokens", "—")),
                    ),
                    (
                        "Output Tokens",
                        str(usage.get("completion_tokens", "—")),
                    ),
                    (
                        "Total Tokens",
                        str(usage.get("total_tokens", "—")),
                    ),
                ]
            )

            content_md = section("Prompt Execution Result", info)
            content_md += f"\n\n### Response\n\n{truncate(str(response_content), 3000)}"

            return ToolResult(
                content=content_md,
                data={
                    "template_id": str(template.id),
                    "version_id": str(version.id),
                    "version": version.template_version,
                    "model": model,
                    "response": response_content,
                    "usage": usage,
                },
            )

        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            logger.exception("run_prompt_tool_failed", error=str(e))
            if "API key not configured for" in str(e):
                return ToolResult(
                    content=section(
                        "Prompt Execution Requires Provider Key",
                        (
                            f"{str(e)}\n\nAdd the provider key in settings, or run with "
                            "a model whose provider key is configured for this workspace."
                        ),
                    ),
                    data={
                        "requires_provider_api_key": True,
                        "template_id": str(template.id),
                        "version_id": str(version.id),
                        "model": model,
                    },
                )
            return ToolResult.error(
                f"Prompt execution failed: {str(e)}",
                error_code=code_from_exception(e),
            )
