import json
import traceback
import asyncio
from typing import Any

from accounts.models import User
from ee.agenthub.prompt_generate_agent.prompts import (
    ANALYSIS_AND_PLANNING_PROMPT,
    ANALYSIS_PROMPT,
    EXECUTE_IMPROVEMENT_PROMPT,
    FEEDBACK_ANALYSIS_PROMPT,
    GENERATION_PROMPT,
    IDENTIFY_CHANGES_PRESERVATION_PROMPT,
    OPTIMIZE_PROMPT_IMPROVEMENT_SUGGESTION_PROMPT,
    PROMPT_IMPROVEMENT_SUGGESTION_PROMPT,
    SYSTEM_PROMPT,
    UNDERSTAND_INTENTION_PROMPT,
    # Deprecated prompts - kept for backward compatibility
    COT_REFINEMENT_PLANNING_PROMPT,
    IMPROVEMENT_PROMPT,
    INITIAL_DRAFT_GENERATION_PROMPT,
    PLANNING_INITIAL_PROMPT,
    VALIDATE_PLANNING_PROMPT,
)
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.core.utils.types import (
    PromptGenerateLLMResponse,
)
from analytics.utils import (
    MixpanelEvents,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.utils import call_websocket
try:
    from ee.usage.models.usage import APICallStatusChoices
except ImportError:
    APICallStatusChoices = None
try:
    from ee.usage.utils.usage_entries import count_text_tokens
except ImportError:
    count_text_tokens = None


def parse_xml_tag(response: str, tag: str) -> str:
    return response.split(f"<{tag}>")[1].split(f"</{tag}>")[0]


def _extract_template_variables(prompt: str) -> set[str]:
    """
    Extract all template variables from a prompt.
    Returns set of variable names (without braces).
    """
    import re

    pattern = r"\{\{([a-zA-Z0-9_]+)\}\}"
    variables = set(re.findall(pattern, prompt))

    return variables


def _validate_template_variables(
    original_prompt: str,
    improved_prompt: str,
    *,
    required_variables: set[str] | None = None,
    allowed_variables: set[str] | None = None,
) -> tuple[bool, str]:
    """Validate template variables against required/allowed sets.

    Returns:
        (True, "") if valid
        (False, "error message") if invalid with details
    """
    original_vars = _extract_template_variables(original_prompt)
    improved_vars = _extract_template_variables(improved_prompt)

    required = required_variables if required_variables is not None else original_vars
    allowed = allowed_variables if allowed_variables is not None else required

    missing_vars = required - improved_vars
    extra_vars = improved_vars - allowed

    if missing_vars:
        missing_list = ", ".join(f"{{{{{var}}}}}" for var in sorted(missing_vars))
        error_msg = (
            f"Template variable validation failed. "
            f"The improved prompt is missing required template variables: {missing_list}."
        )
        return False, error_msg

    if extra_vars:
        extra_list = ", ".join(f"{{{{{var}}}}}" for var in sorted(extra_vars))
        allowed_list = (
            ", ".join(f"{{{{{var}}}}}" for var in sorted(allowed))
            if allowed
            else "NONE"
        )
        error_msg = (
            "Template variable validation failed. "
            f"The improved prompt introduced disallowed template variables: {extra_list}. "
            f"Allowed template variables are: {allowed_list}."
        )
        return False, error_msg

    return True, ""


def _extract_template_variables_from_directive(
    change_plan: str, directive: str
) -> set[str] | None:
    """
    Extract template variables from a single directive line in the change plan.

    Returns:
        None if the directive is not present
        set() if directive is present but specifies NONE/empty
        set([...]) otherwise
    """
    import re

    matches = list(
        re.finditer(rf"(?im)^\s*{re.escape(directive)}\s*:\s*(.*)$", change_plan)
    )
    if not matches:
        return None

    variables: set[str] = set()
    for match in matches:
        remainder = (match.group(1) or "").strip()
        if not remainder or remainder.upper() == "NONE":
            continue
        variables.update(re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", remainder))
    return variables


def _compute_allowed_template_variables(
    original_prompt: str, change_plan: str
) -> tuple[set[str], set[str]]:
    """
    Determine which template variables are allowed/required for the improved prompt.

    Behavior:
    - Defaults to preserving all original variables (backwards compatible).
    - If the change_plan contains variable directives, uses them to allow removing
      variables and (optionally) adding new ones.
    """
    original_vars = _extract_template_variables(original_prompt)

    preserve = _extract_template_variables_from_directive(
        change_plan, "Variables to preserve"
    )
    remove = _extract_template_variables_from_directive(
        change_plan, "Variables to remove"
    )
    add = _extract_template_variables_from_directive(change_plan, "Variables to add")

    directives_present = any(v is not None for v in (preserve, remove, add))
    if not directives_present:
        return set(original_vars), set(original_vars)

    remove_set = set(remove or set())
    remove_set &= original_vars

    add_set = set(add or set())
    # If the model accidentally lists an existing variable under "add", treat it as preserved.
    add_existing = add_set & original_vars
    add_set -= add_existing

    preserve_set = set(preserve or set())
    preserve_set |= add_existing

    # If the model accidentally listed unknown variables under "preserve", treat them as additions.
    unknown_preserve = preserve_set - original_vars
    if unknown_preserve:
        add_set |= unknown_preserve
        preserve_set -= unknown_preserve

    # Conservative default: anything not explicitly removed is preserved.
    preserve_set = (original_vars - remove_set) | (preserve_set & original_vars)

    # If a variable is in both preserve and remove, preserve wins.
    remove_set -= preserve_set

    allowed = preserve_set | add_set
    required = allowed
    return allowed, required


async def _get_llm_response_async(
    llm, messages, streaming, type, uuid, organization_id, current_activity, ws_manager
):
    """Helper to get LLM response using async method when ws_manager is provided"""
    if ws_manager:
        # Stream only the final prompt-generation activities.
        # Intermediate planning/analysis stages should return only final text.
        stream_allowed_activities = {
            "generate_initial_prompt",
            "generate_refined_prompt",
        }
        effective_streaming = bool(streaming) and (
            current_activity in stream_allowed_activities
        )

        return await llm._get_completion_content_async(
            messages=messages,
            streaming=effective_streaming,
            type=type,
            uuid=uuid,
            organization_id=organization_id,
            current_activity=current_activity,
            ws_manager=ws_manager,
        )


class PromptGenerator:
    _config = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN

    def __init__(self):
        self.llm = LLM(
            model_name=self._config.model_name,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            provider=self._config.provider,
        )
        self.organization_id = None

    def generate_prompt(self, payload, call_log_row) -> dict[str, Any]:
        try:
            self.organization_id = (
                payload.get("organization_id")
                if payload.get("organization_id")
                else self.organization_id
            )

            description = payload.get("description", "")

            if not description:
                raise ValueError("Description is required")

            # Step 1: Analyze and plan (combined analysis + validation)
            generation_id = None
            if payload.get("generation_id"):
                generation_id = payload.get("generation_id")
                message = {
                    "generation_id": generation_id,
                    "current_activity": "analyze_prompt_description",
                    "status": "running",
                    "type": "generate_prompt",
                }
                call_websocket(
                    self.organization_id,
                    message=message,
                    send_to_uuid=True,
                    uuid=generation_id,
                )
            validated_analysis = self._get_analysis_and_planning(
                description=description,
                generation_id=generation_id,
                organization_id=self.organization_id,
                current_activity="analyze_prompt_description",
            )

            # Step 2: Generate prompt from the analysis
            if generation_id:
                message = {
                    "generation_id": generation_id,
                    "current_activity": "generate_initial_prompt",
                    "status": "running",
                    "type": "generate_prompt",
                }
                call_websocket(
                    self.organization_id,
                    message=message,
                    send_to_uuid=True,
                    uuid=generation_id,
                )
            generation_response = self._generate_prompt(
                description=description,
                analysis=validated_analysis,
                generation_id=generation_id,
                organization_id=self.organization_id,
                current_activity="generate_initial_prompt",
            )

            if generation_id:
                message["prompt"] = generation_response
                message["status"] = "completed"
                call_websocket(
                    self.organization_id,
                    message=message,
                    send_to_uuid=True,
                    uuid=generation_id,
                )

            prompt_tokens = count_text_tokens(generation_response)
            config = {"output_tokens": prompt_tokens}
            if call_log_row:
                existing_config = call_log_row.config or {}
                if isinstance(existing_config, str):
                    try:
                        existing_config = json.loads(config)
                    except Exception:
                        existing_config = {}
                existing_config.update(config)
                call_log_row.config = json.dumps(existing_config)
                call_log_row.status = APICallStatusChoices.SUCCESS.value
                call_log_row.save(update_fields=["config", "status"])

            uid = payload.get("mixpanel_uid")
            if uid:
                try:
                    user = User.objects.get(id=payload.get("user_id"))
                except Exception:
                    user = None
                properties = get_mixpanel_properties(
                    user=user,
                    uid=uid,
                    cost=float(call_log_row.cost)
                    if call_log_row and call_log_row.cost is not None
                    else 0.0,
                )
                track_mixpanel_event(
                    MixpanelEvents.SDK_PROMPT_GENERATED.value, properties
                )

            return generation_response
        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error occurred while generating prompt: {str(e)}")
            if generation_id:
                message["status"] = "error"
                message["error"] = str(e)
                call_websocket(
                    self.organization_id,
                    message=message,
                    send_to_uuid=True,
                    uuid=generation_id,
                )
            return {}

    def improve_prompt(self, payload, call_log_row):
        original_prompt = payload.get("original_prompt", "")
        improvement_suggestions = payload.get("improvement_suggestions") or ""
        examples = payload.get("examples", "")
        improve_id = payload.get("improve_id")
        organization_id = (
            payload.get("organization_id")
            if payload.get("organization_id")
            else self.organization_id
        )
        user_id = payload.get("user_id")
        uid = payload.get("mixpanel_uid")
        self._improve_prompt(
            original_prompt,
            improvement_suggestions,
            examples,
            improve_id,
            organization_id,
            user_id,
            uid,
            call_log_row,
        )
        # return result

    def _get_analysis(
        self,
        description: str,
        generation_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
    ) -> str:
        try:
            response = self.llm._get_completion_content(
                messages=[
                    {
                        "role": "user",
                        "content": ANALYSIS_PROMPT.format(description=description),
                    }
                ],
                streaming=True,
                type="generation_id",
                uuid=generation_id,
                organization_id=organization_id,
                current_activity=current_activity,
            )
            return response
        except Exception as e:
            logger.exception(f"Error during analysis: {str(e)}")
            return ""

    def _generate_prompt(
        self,
        description: str,
        analysis: str,
        generation_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
    ) -> dict:
        try:
            response = self.llm._get_completion_content(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": GENERATION_PROMPT.format(
                            description=description, analysis=analysis
                        ),
                    },
                ],
                # response_format={"type": "text"},
                streaming=True,
                type="generation_id",
                uuid=generation_id,
                organization_id=organization_id,
                current_activity=current_activity,
            )
            return response
        except Exception as e:
            logger.exception(f"Error during prompt generation: {str(e)}")
            return {"suggested_prompt": "", "explanation": "", "ambiguous": True}

    def _get_analysis_and_planning(
        self,
        description: str,
        generation_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
    ) -> str:
        """Combined analysis + validation in a single LLM call."""
        try:
            response = self.llm._get_completion_content(
                messages=[
                    {
                        "role": "user",
                        "content": ANALYSIS_AND_PLANNING_PROMPT.format(
                            description=description
                        ),
                    }
                ],
                streaming=True,
                type="generation_id",
                uuid=generation_id,
                organization_id=organization_id,
                current_activity=current_activity,
            )
            return response
        except Exception as e:
            logger.exception(f"Error during analysis and planning: {str(e)}")
            return ""

    async def _get_analysis_and_planning_async(
        self,
        description: str,
        generation_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Async combined analysis + validation in a single LLM call."""
        try:
            messages = [
                {
                    "role": "user",
                    "content": ANALYSIS_AND_PLANNING_PROMPT.format(
                        description=description
                    ),
                }
            ]

            if ws_manager:
                response = await _get_llm_response_async(
                    self.llm,
                    messages,
                    True,
                    "generation_id",
                    generation_id,
                    organization_id,
                    current_activity,
                    ws_manager,
                )

            return response
        except Exception as e:
            logger.exception(f"Error during analysis and planning: {str(e)}")
            return ""

    async def _get_analysis_async(
        self,
        description: str,
        generation_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Async version for WebSocket streaming"""
        try:
            messages = [
                {
                    "role": "user",
                    "content": ANALYSIS_PROMPT.format(description=description),
                }
            ]

            if ws_manager:
                response = await _get_llm_response_async(
                    self.llm,
                    messages,
                    True,
                    "generation_id",
                    generation_id,
                    organization_id,
                    current_activity,
                    ws_manager,
                )

            return response
        except Exception as e:
            logger.exception(f"Error during analysis: {str(e)}")
            return ""

    async def _generate_prompt_content_async(
        self,
        description: str,
        analysis: str,
        generation_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Async version for WebSocket streaming - generates the actual prompt content"""
        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": GENERATION_PROMPT.format(
                        description=description, analysis=analysis
                    ),
                },
            ]

            if ws_manager:
                response = await _get_llm_response_async(
                    self.llm,
                    messages,
                    True,
                    "generation_id",
                    generation_id,
                    organization_id,
                    current_activity,
                    ws_manager,
                )

            return response
        except Exception as e:
            logger.exception(f"Error during prompt generation: {str(e)}")
            return ""

    def _improve_prompt_old(
        self, description: str, analysis: str, original_prompt: str
    ) -> dict:
        try:
            response = self.llm._get_completion_content(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": IMPROVEMENT_PROMPT.format(
                            description=description,
                            analysis=analysis,
                            original_prompt=original_prompt,
                        ),
                    },
                ],
                response_format=PromptGenerateLLMResponse,
            )
            return json.loads(response)
        except Exception as e:
            logger.exception(f"Error during prompt improvement: {str(e)}")
            return {"suggested_prompt": "", "explanation": "", "ambiguous": True}

    async def _improve_prompt_async(
        self,
        original_prompt: str,
        improvement_suggestions: str | None = None,
        examples: str | None = None,
        improve_id: str | None = None,
        organization_id: str | None = None,
        user_id: str | None = None,
        uid: str | None = None,
        call_log_row=None,
        ws_manager=None,
    ):
        """
        Async version of _improve_prompt that properly handles WebSocket streaming.
        Uses a 3-step improvement flow with template variable validation.
        """
        try:
            # Extract template variables from original prompt for validation
            original_variables = _extract_template_variables(original_prompt)
            logger.info(
                f"Extracted {len(original_variables)} template variables from original prompt: {original_variables}"
            )

            # Step 1: Understand User Intention
            if improve_id and ws_manager:
                await ws_manager.send_improve_prompt_activity_message(
                    improve_id=improve_id,
                    current_activity="generate_planning",
                    status="running",
                )

            intention_analysis = await self._understand_intention_async(
                original_prompt=original_prompt,
                improvement_suggestions=improvement_suggestions or "",
                improve_id=improve_id,
                organization_id=organization_id,
                current_activity="generate_planning",
                ws_manager=ws_manager,
            )

            # Step 2: Identify Changes and Preservation Requirements
            if improve_id and ws_manager:
                await ws_manager.send_improve_prompt_activity_message(
                    improve_id=improve_id,
                    current_activity="validate_planning",
                    status="running",
                )

            change_plan = await self._identify_changes_preservation_async(
                original_prompt=original_prompt,
                intention_analysis=intention_analysis,
                improve_id=improve_id,
                organization_id=organization_id,
                current_activity="validate_planning",
                ws_manager=ws_manager,
            )

            # Legacy stage 3 (mock): Generate Initial Draft
            if improve_id and ws_manager:
                await ws_manager.send_improve_prompt_activity_message(
                    improve_id=improve_id,
                    current_activity="generate_initial_draft",
                    status="running",
                )

            # Legacy stage 4 (mock): Generate Refinement Planning
            if improve_id and ws_manager:
                await ws_manager.send_improve_prompt_activity_message(
                    improve_id=improve_id,
                    current_activity="generate_refinement_planning",
                    status="running",
                )

            # Step 5 (legacy) / Step 3 (new): Execute Improvement
            if improve_id and ws_manager:
                await ws_manager.send_improve_prompt_activity_message(
                    improve_id=improve_id,
                    current_activity="generate_refined_prompt",
                    status="running",
                )

            allowed_variables, required_variables = _compute_allowed_template_variables(
                original_prompt=original_prompt, change_plan=change_plan
            )
            variables_str = (
                ", ".join(f"{{{{{var}}}}}" for var in sorted(allowed_variables))
                if allowed_variables
                else "NONE"
            )

            improved_prompt = await self._execute_improvement_async(
                original_prompt=original_prompt,
                change_plan=change_plan,
                template_variables=variables_str,
                validation_error="",
                improve_id=improve_id,
                organization_id=organization_id,
                current_activity="generate_refined_prompt",
                ws_manager=ws_manager,
            )

            is_valid, error_message = _validate_template_variables(
                original_prompt,
                improved_prompt,
                required_variables=required_variables,
                allowed_variables=allowed_variables,
            )
            if not is_valid:
                logger.warning(
                    f"Template variable validation failed on first attempt: {error_message}"
                )
                improved_prompt = await self._execute_improvement_async(
                    original_prompt=original_prompt,
                    change_plan=change_plan,
                    template_variables=variables_str,
                    validation_error=error_message,
                    improve_id=improve_id,
                    organization_id=organization_id,
                    current_activity="generate_refined_prompt",
                    ws_manager=ws_manager,
                )
                is_valid, error_message = _validate_template_variables(
                    original_prompt,
                    improved_prompt,
                    required_variables=required_variables,
                    allowed_variables=allowed_variables,
                )
                if not is_valid:
                    raise ValueError(error_message)
            logger.info("Template variable validation passed")

            if improve_id and ws_manager:
                logger.info(f"Sending completed message for improve_id: {improve_id}")
                await ws_manager.send_improve_prompt_completed_message(
                    improve_id=improve_id,
                    prompt=improved_prompt,
                )
                logger.info("Completed message sent")

            # Update call log
            prompt_tokens = count_text_tokens(improved_prompt)
            config = {"output_tokens": prompt_tokens}
            if call_log_row:
                existing_config = call_log_row.config or {}
                if isinstance(existing_config, str):
                    try:
                        existing_config = json.loads(existing_config)
                    except Exception:
                        existing_config = {}
                existing_config.update(config)
                call_log_row.config = json.dumps(existing_config)
                call_log_row.status = APICallStatusChoices.SUCCESS.value
                # Use database_sync_to_async for async context
                if ws_manager:
                    from channels.db import database_sync_to_async

                    await database_sync_to_async(call_log_row.save)()

            # Track mixpanel event
            try:
                if ws_manager and user_id:
                    from channels.db import database_sync_to_async

                    try:
                        user = await database_sync_to_async(User.objects.get)(
                            id=user_id
                        )
                    except User.DoesNotExist:
                        user = None
                elif user_id:
                    user = User.objects.get(id=user_id)
                else:
                    user = None
            except User.DoesNotExist:
                user = None
            if uid:
                properties = get_mixpanel_properties(
                    user=user,
                    uid=uid,
                    cost=float(call_log_row.cost)
                    if call_log_row and call_log_row.cost is not None
                    else 0.0,
                )
                track_mixpanel_event(
                    MixpanelEvents.SDK_PROMPT_IMPROVED.value, properties
                )
        except Exception as e:
            if improve_id and ws_manager:
                await ws_manager.send_improve_prompt_error_message(
                    improve_id=improve_id,
                    error=str(e),
                )

            logger.error(f"Error occurred while generating refined prompt: {str(e)}")
            raise

    async def _generate_prompt_async(
        self,
        description: str,
        generation_id: str | None = None,
        organization_id: str | None = None,
        user_id: str | None = None,
        uid: str | None = None,
        call_log_row=None,
        ws_manager=None,
    ):
        """
        Async version of generate_prompt that properly handles WebSocket streaming.
        This should be used when ws_manager is provided (WebSocket context).
        """
        try:
            if not description:
                raise ValueError("Description is required")

            # Step 1: Analyze the prompt description
            if generation_id and ws_manager:
                await ws_manager.send_generate_prompt_activity_message(
                    generation_id=generation_id,
                    current_activity="analyze_prompt_description",
                    status="running",
                )

            validated_analysis = await self._get_analysis_and_planning_async(
                description=description,
                generation_id=generation_id,
                organization_id=organization_id,
                current_activity="analyze_prompt_description",
                ws_manager=ws_manager,
            )

            # Step 2: Generate prompt from the analysis
            if generation_id and ws_manager:
                await ws_manager.send_generate_prompt_activity_message(
                    generation_id=generation_id,
                    current_activity="generate_initial_prompt",
                    status="running",
                )

            generation_response = await self._generate_prompt_content_async(
                description=description,
                analysis=validated_analysis,
                generation_id=generation_id,
                organization_id=organization_id,
                current_activity="generate_initial_prompt",
                ws_manager=ws_manager,
            )

            if generation_id and ws_manager:
                await ws_manager.send_generate_prompt_completed_message(
                    generation_id=generation_id,
                    prompt=generation_response,
                )

            prompt_tokens = count_text_tokens(generation_response)
            config = {"output_tokens": prompt_tokens}
            if call_log_row:
                existing_config = call_log_row.config or {}
                if isinstance(existing_config, str):
                    try:
                        existing_config = json.loads(existing_config)
                    except Exception:
                        existing_config = {}
                existing_config.update(config)
                call_log_row.config = json.dumps(existing_config)
                call_log_row.status = APICallStatusChoices.SUCCESS.value
                # Use database_sync_to_async for async context
                if ws_manager:
                    from channels.db import database_sync_to_async

                    await database_sync_to_async(call_log_row.save)()

            try:
                if ws_manager and user_id:
                    from channels.db import database_sync_to_async

                    try:
                        user = await database_sync_to_async(User.objects.get)(
                            id=user_id
                        )
                    except User.DoesNotExist:
                        user = None
                elif user_id:
                    user = User.objects.get(id=user_id)
                else:
                    user = None
            except User.DoesNotExist:
                user = None
            if uid:
                properties = get_mixpanel_properties(
                    user=user,
                    uid=uid,
                    cost=float(call_log_row.cost)
                    if call_log_row and call_log_row.cost is not None
                    else 0.0,
                )
                track_mixpanel_event(
                    MixpanelEvents.SDK_PROMPT_GENERATED.value, properties
                )
        except Exception as e:
            if generation_id and ws_manager:
                await ws_manager.send_generate_prompt_error_message(
                    generation_id=generation_id,
                    error=str(e),
                )
            logger.error(f"Error occurred while generating prompt: {str(e)}")
            raise

    def _improve_prompt(
        self,
        original_prompt: str,
        improvement_suggestions: str | None = None,
        examples: str | None = None,
        improve_id=None,
        organization_id=None,
        user_id=None,
        uid=None,
        call_log_row=None,
        ws_manager=None,
    ):
        """
        Sync version of _improve_prompt for backward compatibility (Celery tasks).
        Uses a 3-step improvement flow with template variable validation.
        When ws_manager is provided, this should not be used - use _improve_prompt_async instead.
        """
        # This is kept for backward compatibility with Celery tasks
        # For WebSocket usage, _improve_prompt_async should be used
        if ws_manager:
            raise ValueError(
                "_improve_prompt (sync) should not be called with ws_manager. "
                "Use _improve_prompt_async instead."
            )

        try:
            # Extract template variables from original prompt for validation
            original_variables = _extract_template_variables(original_prompt)
            logger.info(
                f"Extracted {len(original_variables)} template variables from original prompt: {original_variables}"
            )

            # Step 1: Understand User Intention
            if improve_id:
                message = {
                    "improve_id": improve_id,
                    "current_activity": "generate_planning",
                    "status": "running",
                    "type": "improve_prompt",
                }
                call_websocket(
                    organization_id, message=message, send_to_uuid=True, uuid=improve_id
                )

            intention_analysis = self._understand_intention(
                original_prompt=original_prompt,
                improvement_suggestions=improvement_suggestions or "",
                improve_id=improve_id,
                organization_id=organization_id,
                current_activity="generate_planning",
            )

            # Step 2: Identify Changes and Preservation Requirements
            if improve_id:
                message = {
                    "improve_id": improve_id,
                    "current_activity": "validate_planning",
                    "status": "running",
                    "type": "improve_prompt",
                }
                call_websocket(
                    organization_id, message=message, send_to_uuid=True, uuid=improve_id
                )

            change_plan = self._identify_changes_preservation(
                original_prompt=original_prompt,
                intention_analysis=intention_analysis,
                improve_id=improve_id,
                organization_id=organization_id,
                current_activity="validate_planning",
            )

            # Legacy stage 3 (mock): Generate Initial Draft
            if improve_id:
                message = {
                    "improve_id": improve_id,
                    "current_activity": "generate_initial_draft",
                    "status": "running",
                    "type": "improve_prompt",
                }
                call_websocket(
                    organization_id, message=message, send_to_uuid=True, uuid=improve_id
                )

            # Legacy stage 4 (mock): Generate Refinement Planning
            if improve_id:
                message = {
                    "improve_id": improve_id,
                    "current_activity": "generate_refinement_planning",
                    "status": "running",
                    "type": "improve_prompt",
                }
                call_websocket(
                    organization_id, message=message, send_to_uuid=True, uuid=improve_id
                )

            # Step 5 (legacy) / Step 3 (new): Execute Improvement
            if improve_id:
                message = {
                    "improve_id": improve_id,
                    "current_activity": "generate_refined_prompt",
                    "status": "running",
                    "type": "improve_prompt",
                }
                call_websocket(
                    organization_id, message=message, send_to_uuid=True, uuid=improve_id
                )

            allowed_variables, required_variables = _compute_allowed_template_variables(
                original_prompt=original_prompt, change_plan=change_plan
            )
            variables_str = (
                ", ".join(f"{{{{{var}}}}}" for var in sorted(allowed_variables))
                if allowed_variables
                else "NONE"
            )

            improved_prompt = self._execute_improvement(
                original_prompt=original_prompt,
                change_plan=change_plan,
                template_variables=variables_str,
                validation_error="",
                improve_id=improve_id,
                organization_id=organization_id,
                current_activity="generate_refined_prompt",
            )

            is_valid, error_message = _validate_template_variables(
                original_prompt,
                improved_prompt,
                required_variables=required_variables,
                allowed_variables=allowed_variables,
            )
            if not is_valid:
                logger.warning(
                    f"Template variable validation failed on first attempt: {error_message}"
                )
                improved_prompt = self._execute_improvement(
                    original_prompt=original_prompt,
                    change_plan=change_plan,
                    template_variables=variables_str,
                    validation_error=error_message,
                    improve_id=improve_id,
                    organization_id=organization_id,
                    current_activity="generate_refined_prompt",
                )
                is_valid, error_message = _validate_template_variables(
                    original_prompt,
                    improved_prompt,
                    required_variables=required_variables,
                    allowed_variables=allowed_variables,
                )
                if not is_valid:
                    raise ValueError(error_message)
            logger.info("Template variable validation passed")

            if improve_id:
                message = {
                    "improve_id": improve_id,
                    "prompt": improved_prompt,
                    "status": "completed",
                    "type": "improve_prompt",
                }
                call_websocket(
                    organization_id, message=message, send_to_uuid=True, uuid=improve_id
                )

            prompt_tokens = count_text_tokens(improved_prompt)
            config = {"output_tokens": prompt_tokens}
            if call_log_row:
                existing_config = call_log_row.config or {}
                if isinstance(existing_config, str):
                    try:
                        existing_config = json.loads(config)
                    except Exception:
                        existing_config = {}
                existing_config.update(config)
                call_log_row.config = json.dumps(existing_config)
                call_log_row.status = APICallStatusChoices.SUCCESS.value
                call_log_row.save()

            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                user = None
            if uid:
                properties = get_mixpanel_properties(
                    user=user,
                    uid=uid,
                    cost=float(call_log_row.cost)
                    if call_log_row and call_log_row.cost is not None
                    else 0.0,
                )
                track_mixpanel_event(
                    MixpanelEvents.SDK_PROMPT_IMPROVED.value, properties
                )
        except Exception as e:
            if improve_id:
                message = {
                    "improve_id": improve_id,
                    "status": "error",
                    "type": "improve_prompt",
                    "error": str(e),
                }
                call_websocket(
                    organization_id, message=message, send_to_uuid=True, uuid=improve_id
                )
            logger.error(f"Error occurred while generating refined prompt: {str(e)}")

    # New 3-Step Improvement Flow - Async Helper Methods

    async def _understand_intention_async(
        self,
        original_prompt: str,
        improvement_suggestions: str,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Step 1: Understand user intention for prompt improvement."""
        logger.info("Analyzing user intention for prompt improvement")

        messages = [
            {
                "role": "user",
                "content": UNDERSTAND_INTENTION_PROMPT.format(
                    original_prompt=original_prompt,
                    improvement_suggestions=improvement_suggestions,
                ),
            }
        ]

        response = ""
        if ws_manager:
            result = await _get_llm_response_async(
                self.llm,
                messages,
                True,
                "improve_id",
                improve_id,
                organization_id,
                current_activity,
                ws_manager,
            )
            if result:
                response = result

        logger.info(f"User intention analysis completed: {len(response)} characters")
        return response

    async def _identify_changes_preservation_async(
        self,
        original_prompt: str,
        intention_analysis: str,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Step 2: Identify what to change vs what to preserve."""
        logger.info("Identifying changes and preservation requirements")

        messages = [
            {
                "role": "user",
                "content": IDENTIFY_CHANGES_PRESERVATION_PROMPT.format(
                    original_prompt=original_prompt,
                    intention_analysis=intention_analysis,
                ),
            }
        ]

        response = ""
        if ws_manager:
            result = await _get_llm_response_async(
                self.llm,
                messages,
                True,
                "improve_id",
                improve_id,
                organization_id,
                current_activity,
                ws_manager,
            )
            if result:
                response = result

        if ws_manager:
            result = await _get_llm_response_async(
                self.llm,
                messages,
                True,
                "improve_id",
                improve_id,
                organization_id,
                current_activity,
                ws_manager,
            )
            if result:
                response = result

        logger.info(f"Change plan generated: {len(response)} characters")
        return response

    async def _execute_improvement_async(
        self,
        original_prompt: str,
        change_plan: str,
        template_variables: str,
        validation_error: str,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Step 3: Execute the prompt improvement."""
        logger.info("Executing prompt improvement")

        messages = [
            {
                "role": "user",
                "content": EXECUTE_IMPROVEMENT_PROMPT.format(
                    original_prompt=original_prompt,
                    change_plan=change_plan,
                    template_variables=template_variables,
                    validation_error=validation_error,
                ),
            }
        ]

        response = ""
        if ws_manager:
            result = await _get_llm_response_async(
                self.llm,
                messages,
                True,
                "improve_id",
                improve_id,
                organization_id,
                current_activity,
                ws_manager,
            )
            if result:
                response = result

        logger.info(f"Improved prompt generated: {len(response)} characters")
        return response

    # New 3-Step Improvement Flow - Sync Helper Methods (for backward compatibility)

    def _understand_intention(
        self,
        original_prompt: str,
        improvement_suggestions: str,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
    ) -> str:
        """Step 1: Understand user intention for prompt improvement (Sync)."""
        logger.info("Analyzing user intention for prompt improvement (Sync)")

        response = self.llm._get_completion_content(
            messages=[
                {
                    "role": "user",
                    "content": UNDERSTAND_INTENTION_PROMPT.format(
                        original_prompt=original_prompt,
                        improvement_suggestions=improvement_suggestions,
                    ),
                }
            ],
            streaming=True,
            type="improve_id",
            uuid=improve_id,
            organization_id=organization_id,
            current_activity=current_activity,
        )

        logger.info(f"User intention analysis completed: {len(response)} characters")
        return response

    def _identify_changes_preservation(
        self,
        original_prompt: str,
        intention_analysis: str,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
    ) -> str:
        """Step 2: Identify what to change vs what to preserve (Sync)."""
        logger.info("Identifying changes and preservation requirements (Sync)")

        response = self.llm._get_completion_content(
            messages=[
                {
                    "role": "user",
                    "content": IDENTIFY_CHANGES_PRESERVATION_PROMPT.format(
                        original_prompt=original_prompt,
                        intention_analysis=intention_analysis,
                    ),
                }
            ],
            streaming=True,
            type="improve_id",
            uuid=improve_id,
            organization_id=organization_id,
            current_activity=current_activity,
        )

        logger.info(f"Change plan generated: {len(response)} characters")
        return response

    def _execute_improvement(
        self,
        original_prompt: str,
        change_plan: str,
        template_variables: str,
        validation_error: str,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
    ) -> str:
        """Step 3: Execute the prompt improvement (Sync)."""
        logger.info("Executing prompt improvement (Sync)")

        response = self.llm._get_completion_content(
            messages=[
                {
                    "role": "user",
                    "content": EXECUTE_IMPROVEMENT_PROMPT.format(
                        original_prompt=original_prompt,
                        change_plan=change_plan,
                        template_variables=template_variables,
                        validation_error=validation_error,
                    ),
                }
            ],
            streaming=True,
            type="improve_id",
            uuid=improve_id,
            organization_id=organization_id,
            current_activity=current_activity,
        )

        logger.info(f"Improved prompt generated: {len(response)} characters")
        return response

    # Deprecated 5-step flow helper methods - kept for backward compatibility

    async def _generate_planning_async(
        self,
        original_prompt: str,
        improvement_suggestions: str,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Async version for WebSocket streaming"""
        logger.info(
            f"Generating planning for original prompt: {original_prompt} and improvement suggestions: {improvement_suggestions}"
        )

        messages = [
            {
                "role": "user",
                "content": PLANNING_INITIAL_PROMPT.format(
                    original_prompt=original_prompt,
                    improvement_suggestions=improvement_suggestions,
                ),
            }
        ]

        if ws_manager:
            response = await _get_llm_response_async(
                self.llm,
                messages,
                True,
                "improve_id",
                improve_id,
                organization_id,
                current_activity,
                ws_manager,
            )

        return response

    def _generate_planning(
        self,
        original_prompt: str,
        improvement_suggestions: str,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Sync version for backward compatibility - should not be called with ws_manager"""
        if ws_manager:
            raise ValueError(
                "_generate_planning (sync) should not be called with ws_manager. Use _generate_planning_async instead."
            )

        logger.info(
            f"Generating planning for original prompt: {original_prompt} and improvement suggestions: {improvement_suggestions}"
        )

        response = self.llm._get_completion_content(
            messages=[
                {
                    "role": "user",
                    "content": PLANNING_INITIAL_PROMPT.format(
                        original_prompt=original_prompt,
                        improvement_suggestions=improvement_suggestions,
                    ),
                }
            ],
            streaming=True,
            type="improve_id",
            uuid=improve_id,
            organization_id=organization_id,
            current_activity=current_activity,
        )
        return response

    async def _validate_planning_async(
        self,
        description: str,
        planning: str,
        generation_id: str | None = None,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Async version for WebSocket streaming"""
        logger.info(f"Validating planning: {planning}")
        messages = [
            {
                "role": "user",
                "content": VALIDATE_PLANNING_PROMPT.format(
                    task_description=description, planning=planning
                ),
            }
        ]
        msg_type = (
            "generation_id" if generation_id else "improve_id" if improve_id else None
        )
        msg_uuid = generation_id if generation_id else improve_id

        if ws_manager:
            response = await _get_llm_response_async(
                self.llm,
                messages,
                True,
                msg_type,
                msg_uuid,
                organization_id,
                current_activity,
                ws_manager,
            )

        return response

    def _validate_planning(
        self,
        description: str,
        planning: str,
        generation_id: str | None = None,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Sync version for backward compatibility - should not be called with ws_manager"""
        if ws_manager:
            raise ValueError(
                "_validate_planning (sync) should not be called with ws_manager. Use _validate_planning_async instead."
            )

        logger.info(f"Validating planning: {planning}")
        response = self.llm._get_completion_content(
            messages=[
                {
                    "role": "user",
                    "content": VALIDATE_PLANNING_PROMPT.format(
                        task_description=description, planning=planning
                    ),
                }
            ],
            streaming=True,
            type="generation_id"
            if generation_id
            else "improve_id"
            if improve_id
            else None,
            uuid=generation_id if generation_id else improve_id,
            organization_id=organization_id,
            current_activity=current_activity,
        )
        return response

    async def _generate_initial_draft_prompt_async(
        self,
        original_prompt: str,
        planning: str,
        examples: str | None = None,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Async version for WebSocket streaming"""
        logger.info(
            f"Generating prompt for original prompt: {original_prompt} and planning: {planning} and examples: {examples}"
        )
        messages = [
            {
                "role": "user",
                "content": INITIAL_DRAFT_GENERATION_PROMPT.format(
                    original_prompt=original_prompt, planning=planning
                ),
            }
        ]

        if ws_manager:
            response = await _get_llm_response_async(
                self.llm,
                messages,
                True,
                "improve_id",
                improve_id,
                organization_id,
                current_activity,
                ws_manager,
            )

        return response

    def _generate_initial_draft_prompt(
        self,
        original_prompt: str,
        planning: str,
        examples: str | None = None,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Sync version for backward compatibility - should not be called with ws_manager"""
        if ws_manager:
            raise ValueError(
                "_generate_initial_draft_prompt (sync) should not be called with ws_manager. Use _generate_initial_draft_prompt_async instead."
            )

        logger.info(
            f"Generating prompt for original prompt: {original_prompt} and planning: {planning} and examples: {examples}"
        )
        response = self.llm._get_completion_content(
            messages=[
                {
                    "role": "user",
                    "content": INITIAL_DRAFT_GENERATION_PROMPT.format(
                        original_prompt=original_prompt, planning=planning
                    ),
                }
            ],
            streaming=True,
            type="improve_id",
            uuid=improve_id,
            organization_id=organization_id,
            current_activity=current_activity,
        )
        return response

    async def _generate_refinement_planning_async(
        self,
        initial_prompt: str,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Async version for WebSocket streaming"""
        logger.info(
            f"Generating refinement planning for original prompt: {initial_prompt}"
        )
        messages = [
            {
                "role": "user",
                "content": COT_REFINEMENT_PLANNING_PROMPT.format(
                    initial_prompt=initial_prompt
                ),
            }
        ]

        if ws_manager:
            response = await _get_llm_response_async(
                self.llm,
                messages,
                True,
                "improve_id",
                improve_id,
                organization_id,
                current_activity,
                ws_manager,
            )

        return response

    def _generate_refinement_planning(
        self,
        initial_prompt: str,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Sync version for backward compatibility - should not be called with ws_manager"""
        if ws_manager:
            raise ValueError(
                "_generate_refinement_planning (sync) should not be called with ws_manager. Use _generate_refinement_planning_async instead."
            )

        logger.info(
            f"Generating refinement planning for original prompt: {initial_prompt}"
        )
        response = self.llm._get_completion_content(
            messages=[
                {
                    "role": "user",
                    "content": COT_REFINEMENT_PLANNING_PROMPT.format(
                        initial_prompt=initial_prompt
                    ),
                }
            ],
            streaming=True,
            type="improve_id",
            uuid=improve_id,
            organization_id=organization_id,
            current_activity=current_activity,
        )
        return response

    async def _generate_refined_prompt_async(
        self,
        original_prompt: str,
        refinement_planning: str,
        examples: str | None = None,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Async version for WebSocket streaming"""
        logger.info(
            f"Generating refinement prompt for original prompt: {original_prompt} and refinement planning: {refinement_planning} and examples: {examples}"
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": IMPROVEMENT_PROMPT.format(
                    original_prompt=original_prompt, analysis=refinement_planning
                ),
            },
        ]

        if ws_manager:
            response = await _get_llm_response_async(
                self.llm,
                messages,
                True,
                "improve_id",
                improve_id,
                organization_id,
                current_activity,
                ws_manager,
            )

        return response

    def _generate_refined_prompt(
        self,
        original_prompt: str,
        refinement_planning: str,
        examples: str | None = None,
        improve_id: str | None = None,
        organization_id: str | None = None,
        current_activity: str | None = None,
        ws_manager=None,
    ) -> str:
        """Sync version for backward compatibility - should not be called with ws_manager"""
        if ws_manager:
            raise ValueError(
                "_generate_refined_prompt (sync) should not be called with ws_manager. Use _generate_refined_prompt_async instead."
            )

        logger.info(
            f"Generating refinement prompt for original prompt: {original_prompt} and refinement planning: {refinement_planning} and examples: {examples}"
        )
        response = self.llm._get_completion_content(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": IMPROVEMENT_PROMPT.format(
                        original_prompt=original_prompt, analysis=refinement_planning
                    ),
                },
            ],
            streaming=True,
            type="improve_id",
            uuid=improve_id,
            organization_id=organization_id,
            current_activity=current_activity,
        )
        return response


class PromptSuggestionGenerator:
    _config = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN

    def __init__(self):
        self.llm = LLM(
            model_name=self._config.model_name,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            provider=self._config.provider,
        )

    def _prompt_suggestion(self, payload):
        prompt = payload.get("prompt")
        feedback = payload.get("feedback")
        example = payload.get("example")

        analysis = self._generate_planning(prompt, example, feedback)
        generated_suggestion = self._generate_prompt_suggestion(
            prompt, example, analysis
        )
        optmized_suggestion = self._optimize_suggestion(generated_suggestion)
        return optmized_suggestion

    def _generate_planning(self, prompt: str, example: str, feedback: str) -> str:
        try:
            response = self.llm._get_completion_content(
                messages=[
                    {
                        "role": "user",
                        "content": FEEDBACK_ANALYSIS_PROMPT.format(
                            prompt=prompt, example=example, feedback=feedback
                        ),
                    }
                ],
                model=self._config.model_name,
            )
            return parse_xml_tag(response=response, tag="analysis")
        except Exception as e:
            logger.exception(f"Error during prompt improvement: {str(e)}")
            raise e

    def _generate_prompt_suggestion(
        self, prompt: str, example: str, analysis: str
    ) -> str:
        try:
            response = self.llm._get_completion_content(
                messages=[
                    {
                        "role": "user",
                        "content": PROMPT_IMPROVEMENT_SUGGESTION_PROMPT.format(
                            prompt=prompt, example=example, feedback=analysis
                        ),
                    }
                ]
            )
            return parse_xml_tag(response, "suggestion")
        except Exception as e:
            logger.exception(f"Error during prompt improvement: {str(e)}")
            raise e

    def _optimize_suggestion(self, generated_suggestion: str) -> str:
        try:
            response = self.llm._get_completion_content(
                messages=[
                    {
                        "role": "user",
                        "content": OPTIMIZE_PROMPT_IMPROVEMENT_SUGGESTION_PROMPT.format(
                            generated_suggestion=generated_suggestion
                        ),
                    }
                ]
            )
            return parse_xml_tag(response, "suggestion")
        except Exception as e:
            logger.exception(f"Error during prompt improvement: {str(e)}")
            return generated_suggestion


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Generate prompts from an input description"
    )

    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Input description to generate prompt",
    )

    try:
        args = parser.parse_args()
        if not args.input.strip():
            logger.info("Error: Input prompt cannot be empty")
            sys.exit(1)

        agent = PromptGenerator()
        result = agent.generate_prompt(args.input)
        print(result)

    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
