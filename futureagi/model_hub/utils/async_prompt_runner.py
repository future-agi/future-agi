import structlog
from channels.db import database_sync_to_async
from django.db import close_old_connections
from django.utils import timezone

from accounts.models import Organization

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
from model_hub.services.derived_variable_service import (
    extract_derived_variables_from_output,
)
from model_hub.utils.column_utils import is_json_response_format
try:
    from ee.usage.utils.usage_entries import APICallTypeChoices, log_and_deduct_cost_for_api_request
except ImportError:
    from futureagi._ee_stubs import APICallTypeChoices, log_and_deduct_cost_for_api_request


async def run_template_async(
    template,
    execution,
    organization_id,
    version_to_run,
    is_run,
    run_index,
    workspace,
    ws_manager,
):
    from model_hub.views.prompt_template import (
        remove_empty_text_from_messages,
        replace_variables,
    )

    await database_sync_to_async(close_old_connections)()

    try:
        organization = await database_sync_to_async(Organization.objects.get)(
            id=organization_id
        )
    except Organization.DoesNotExist:
        organization = None

    total_iterations = (
        1
        if run_index is not None
        else max(
            (len(values) for values in (execution.variable_names or {}).values()),
            default=1,
        )
    )

    # Extract output_format from config, default to "string"
    config = execution.prompt_config_snapshot
    output_format = config.get("configuration", {}).get("output_format", "string")

    await ws_manager.notify_process_started(
        template_id=str(template.id),
        version=version_to_run if version_to_run else execution.template_version,
        execution_id=str(execution.id) if execution else None,
        process_type="run_prompt" if is_run == "prompt" else "run_evaluation",
        total_iterations=total_iterations,
        output_format=output_format,
    )

    try:
        variable_names = execution.variable_names or {}
        max_len = max((len(values) for values in variable_names.values()), default=1)

        indices_to_process = [run_index] if run_index is not None else range(max_len)

        responses = list(execution.output) if execution.output else [None] * max_len
        value_infos = (
            list(execution.metadata) if execution.metadata else [None] * max_len
        )

        for i in indices_to_process:
            variable_combination = {
                key: values[i] if i < len(values) else None
                for key, values in variable_names.items()
            }
            try:
                prompt_messages = config.get("messages", []).copy()

                messages_with_replacement = replace_variables(
                    prompt_messages,
                    variable_combination,
                    config.get("configuration", {}).get("model"),
                )
                messages_with_replacement = remove_empty_text_from_messages(
                    messages_with_replacement
                )

                tools_to_send = [
                    tool.get("config")
                    for tool in config.get("configuration", {}).get("tools", [])
                    if tool.get("config")
                ]

                run_prompt = RunPrompt(
                    model=config.get("configuration", {}).get("model"),
                    organization_id=organization_id,
                    messages=messages_with_replacement,
                    temperature=config.get("configuration", {}).get("temperature"),
                    frequency_penalty=config.get("configuration", {}).get(
                        "frequency_penalty"
                    ),
                    presence_penalty=config.get("configuration", {}).get(
                        "presence_penalty"
                    ),
                    max_tokens=config.get("configuration", {}).get("max_tokens"),
                    top_p=config.get("configuration", {}).get("top_p"),
                    response_format=config.get("configuration", {}).get(
                        "response_format"
                    ),
                    tool_choice=config.get("configuration", {}).get("tool_choice"),
                    tools=tools_to_send,
                    output_format=output_format,
                    ws_manager=ws_manager,
                    workspace_id=workspace.id if workspace else None,
                    run_prompt_config=config.get("configuration", {}),
                )

                if is_run == "prompt":
                    response, value_info = await run_prompt.litellm_response_async(
                        streaming=True,
                        template_id=template.id,
                        version=version_to_run or execution.template_version,
                        index=i,
                        max_index=max_len,
                        type="run_prompt",
                    )
                    metadata = value_info.get("metadata", {})
                    token_config = metadata.get("usage", {})

                    # Wrap the synchronous DB call
                    await database_sync_to_async(log_and_deduct_cost_for_api_request)(
                        organization,
                        APICallTypeChoices.PROMPT_BENCH.value,
                        config=token_config,
                        source="run_prompt_gen",
                        workspace=workspace,
                    )

                    # Dual-write: emit usage event for new billing system
                    try:
                        try:
                            from ee.usage.schemas.events import UsageEvent
                        except ImportError:
                            UsageEvent = None
                        try:
                            from ee.usage.services.emitter import emit
                        except ImportError:
                            emit = None

                        emit(
                            UsageEvent(
                                org_id=str(organization.id),
                                event_type=APICallTypeChoices.PROMPT_BENCH.value,
                                properties={
                                    "source": "run_prompt_gen",
                                    "source_id": str(template.id),
                                },
                            )
                        )
                    except Exception:
                        pass  # Metering failure must not break the action

                    responses[i] = response
                    value_infos[i] = value_info.get("metadata")

            except Exception as e:
                logger.exception(f"Error for index {i}: {e}")
                await ws_manager.send_error_message(
                    template_id=str(template.id),
                    version=execution.template_version,
                    error=str(e),
                    result_index=i,
                    num_results=max_len,
                    output_format=output_format,
                )
                responses[i] = str(e)

        execution.output = responses
        execution.metadata = value_infos
        execution.is_draft = False
        execution.updated_at = timezone.now()
        await database_sync_to_async(execution.save)(
            update_fields=["output", "is_draft", "updated_at", "metadata"]
        )

        # Extract derived variables from JSON outputs
        response_format = config.get("configuration", {}).get("response_format")

        if is_json_response_format(response_format):
            try:
                # Extract derived variables from the first output
                if responses and responses[0]:
                    derived_vars = await database_sync_to_async(
                        extract_derived_variables_from_output
                    )(responses[0], "output")

                    # Store derived variables in execution metadata
                    if derived_vars.get("is_json"):
                        current_metadata = execution.metadata or [{}]
                        if current_metadata and isinstance(current_metadata, list):
                            if not current_metadata[0]:
                                current_metadata[0] = {}
                            current_metadata[0]["derived_variables"] = {
                                "output": derived_vars
                            }
                            execution.metadata = current_metadata
                            await database_sync_to_async(execution.save)(
                                update_fields=["metadata"]
                            )
            except Exception as e:
                logger.warning(f"Failed to extract derived variables: {e}")

        await ws_manager.send_all_completed_message(
            template_id=str(template.id), version=execution.template_version
        )

    except Exception as e:
        logger.exception(f"Error in run_template_async: {e}")
        await ws_manager.send_error_message(
            template_id=str(template.id),
            version=version_to_run or execution.template_version,
            error=str(e),
            result_index=run_index or 0,
            num_results=1,
            output_format=output_format,
        )
    finally:
        await database_sync_to_async(close_old_connections)()
