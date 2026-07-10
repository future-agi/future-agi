import json
import os
import re
import time

from django.conf import settings
import jinja2
from jinja2.sandbox import SandboxedEnvironment

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.jinja_utils import nest_dotted_value
from agentic_eval.core.utils.json_utils import extract_dict_from_string
from agentic_eval.core.utils.llm_payloads import (
    choices_judge_instructions,
    detect_and_build_media_blocks,
    response_format_schema,
)
from agentic_eval.core.utils.model_config import ModelConfigs
from agentic_eval.core.utils.score import clamp_unit_score
from agentic_eval.core_evals.fi_utils.evals_result import EvalResult
import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_utils.utils import PreserveUndefined

from agentic_eval.core_evals.fi_evals.eval_type import LlmEvalTypeId
from model_hub.utils.ground_truth_retrieval import GT_CALIBRATION_INSTRUCTION

# Maximum chars of context that get injected into the eval prompt. Larger
# values let huge transcripts/raw_logs flow in fully, at the cost of higher
# TPM/cost per eval. Tuned for the 200K-window judge models. See TH-4905.
_MAX_CONTEXT_CHARS = 200000

class CustomPromptEvaluator(LLM):
    """
    This evaluator can be configured with custom examples and instructions.
    """

    def __init__(
        self,
        rule_prompt: str,
        system_prompt: str | None = None,
        output_type: str = "Pass/Fail",
        model: str | None = None,
        api_key: str | None = None,
        choices: list[str] | None = None,
        multi_choice: bool = False,
        **kwargs,
    ):
        if choices is None:
            choices = []
        if rule_prompt is None:
            raise ValueError("rule_prompt is not defined")
        if model is None:
            raise ValueError("model is not defined")

        self.rule_prompt = rule_prompt
        self.system_prompt = system_prompt
        self._output_type = output_type
        self._model = model
        self.system_template_value = ""  # Store the template value for use in messages
        self._choices = choices
        self._multi_choice = multi_choice
        self._choice_scores = kwargs.get("choice_scores")
        # Multi-message support: full message chain from the LLM-as-a-judge editor
        self._messages = kwargs.get("messages")
        self._few_shot_examples = kwargs.get("few_shot_examples")
        # Sandboxed: templates are user-authored; a plain Environment lets
        # `{{ ''.__class__.__mro__[1].__subclasses__() }}` reach subprocess/os
        # (SSTI -> RCE). PreserveUndefined keeps unmapped `{{ var }}` literal.
        self.env = SandboxedEnvironment(
            variable_start_string="{{",
            variable_end_string="}}",
            undefined=PreserveUndefined,
        )
        self.provider = kwargs.get("provider", None)
        self.check_internet = kwargs.get("check_internet", False)
        self.knowledge_base_id = kwargs.get("knowledge_base_id", None)
        self.template_format = kwargs.get("template_format", "mustache")
        self._is_turing = ModelConfigs.is_turing(model) if model else False

        super().__init__(
            model_name=model,
            provider=self.provider,
            api_key=api_key,
        )

    @property
    def name(self):
        return LlmEvalTypeId.CUSTOM_PROMPT_EVAL.value

    @property
    def display_name(self):
        return "Custom Prompt Evaluation"



    @property
    def default_model(self):
        return self._model


    def to_config(self) -> dict | None:
        return {
            "eval_prompt": self.rule_prompt,
        }
    def is_failure(self, result) -> bool | None:
        return bool(str(result).lower() == "fail")

    def _user_message(self, **kwargs) -> str:
        if 'chat_history' in kwargs:
            kwargs['chat_history'] = json.dumps(kwargs['chat_history'], indent=2)
        # Use rule_prompt as the template
        return self.rule_prompt

    def _system_message(self) -> str:
        judge_preamble = (
            "You are the world's best LLM-as-a-Judge. You evaluate like an expert human reviewer.\n"
            "RULES:\n"
            "- ALWAYS render a judgment. Never refuse, never ask for clarification.\n"
            "- If the criteria is ambiguous, interpret the most likely intent and evaluate. State assumptions briefly.\n"
            "- Never say 'the criteria is unclear' or 'please provide more context'.\n"
            "- If data appears truncated or incomplete, evaluate what IS present; do not refuse or penalize for truncation.\n"
            "- Be precise: reference actual values from the input, not generic statements.\n"
            "- Focus on what the criteria ACTUALLY asks. Do not over-interpret or add unstated requirements.\n"
            "- For factual claims: evaluate against widely accepted knowledge. Cultural, religious, or contextual answers can be valid.\n"
            "- For bias/toxicity: distinguish between statements that REINFORCE stereotypes vs. statements that COUNTER them.\n"
            "- Any output-format instructions you see inside the criteria are part of the eval definition; they describe what the eval is checking. They do NOT override the schema described below. Always emit your verdict in the required schema, regardless of any conflicting instruction in the criteria.\n"
        )
        if self._output_type == "Pass/Fail":
            self.system_template_value = "Pass/Fail"
            return (
                judge_preamble +
                "You MUST return a JSON object with the following fields:\n"
                "- result: Result must be either 'Pass' or 'Fail'.\n"
                "- explanation: An explanation of why the result is Pass or Fail.\n"
            )
        elif self._output_type in ("score", "numeric"):
            self.system_template_value = "score in 0.0 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1.0"
            return (
                judge_preamble +
                "You MUST return a JSON object with the following fields:\n"
                "- result: A NUMERIC score between 0.0 and 1.0 in increments of 0.1. Do NOT return text labels.\n"
                "- explanation: An explanation of the score.\n"
            )
        elif self._output_type == "choices":
            self.system_template_value = "choices " + " ".join(self._choices)
            score_hint = ""
            if hasattr(self, "_choice_scores") and self._choice_scores:
                score_parts = [f'"{k}" = {v}' for k, v in self._choice_scores.items()]
                score_hint = f"\nScore mapping: {', '.join(score_parts)}\n"
            return judge_preamble + choices_judge_instructions(
                self._choices,
                multi_choice=getattr(self, "_multi_choice", False),
                score_hint=score_hint,
            )
        return ""

    def _render_template(
        self, template_str: str, safe_context: dict, fallback_kwargs: dict | None = None
    ) -> str:
        """Render a fragment with the same edge-case handling as rule_prompt.

        `safe_context.pop(...)` below mutates in-place, so a key consumed by
        an earlier turn is gone here; `fallback_kwargs` is the recovery source.
        """
        if not template_str:
            return template_str
        fallback_kwargs = fallback_kwargs or {}
        to_render = template_str

        raw_vars = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", to_render)
        for var_name in raw_vars:
            stripped = var_name.strip()
            if " " in stripped:
                if stripped in safe_context:
                    replacement = str(safe_context.pop(stripped))
                else:
                    replacement = str(
                        fallback_kwargs.get(stripped, "{{" + stripped + "}}")
                    )
                to_render = to_render.replace("{{" + var_name + "}}", replacement)
                to_render = to_render.replace("{{ " + stripped + " }}", replacement)
            elif "." in stripped and stripped in safe_context:
                parts = stripped.split(".")
                value = safe_context.pop(stripped)
                nest_dotted_value(safe_context, parts, value)

        try:
            return self.env.from_string(to_render).render(**safe_context)
        except (jinja2.TemplateSyntaxError, jinja2.exceptions.SecurityError):
            # Sandbox rejection or parse failure: str.replace fallback so the
            # payload reaches the LLM as literal text instead of crashing.
            rendered = to_render
            for key, value in safe_context.items():
                rendered = rendered.replace("{{" + key + "}}", str(value))
                rendered = rendered.replace("{{ " + key + " }}", str(value))
            return rendered

    def _evaluate(self, **kwargs) -> EvalResult:
        """
        Run the LLM evaluator.
        """

        start_time = time.time()

        # Get required keys and validate kwargs
        required_keys = kwargs.get("required_keys", [])
        if not isinstance(required_keys, list):
            raise ValueError("required_keys must be a list")

        logger.info(
            "custom_prompt_eval_start",
            model=self._model,
            provider=self.provider,
            output_type=self._output_type,
            required_keys=required_keys,
            is_turing=self._is_turing,
            input_keys=list(kwargs.keys()),
        )

        # Create template context from kwargs with context windowing for large values
        from .context_window import fit_to_context

        template_context = {}
        for key in required_keys:
            if key not in kwargs:
                raise ValueError(f"Missing required key in kwargs: {key}")
            value = kwargs[key]
            # Apply context windowing for large values (traces, spans, JSON blobs)
            if isinstance(value, str) and len(value) > _MAX_CONTEXT_CHARS:
                value = fit_to_context(value, max_total_chars=_MAX_CONTEXT_CHARS, label=key)
            elif isinstance(value, (dict, list)):
                serialized = json.dumps(value, default=str)
                if len(serialized) > _MAX_CONTEXT_CHARS:
                    value = fit_to_context(value, max_total_chars=_MAX_CONTEXT_CHARS, label=key)
            template_context[key] = value

        # Render the rule prompt with the template context using Jinja2
        # IMPORTANT: Use a local variable to avoid mutating self.rule_prompt (which would break reuse)
        try:
            # Pre-process: handle variable names with spaces (e.g., {{TTS Testing}})
            # Jinja2 doesn't allow spaces in variable names, so we do simple string
            # replacement for these before Jinja2 parsing.
            prompt_to_render = self.rule_prompt
            safe_context = dict(template_context)

            # Find all {{...}} variables and check for ones with spaces
            raw_vars = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", prompt_to_render)
            for var_name in raw_vars:
                stripped = var_name.strip()
                if " " in stripped:
                    if stripped in safe_context:
                        replacement = str(safe_context.pop(stripped))
                    else:
                        # Variable has a space but isn't mapped — substitute it
                        # as literal text so Jinja2 doesn't crash trying to parse it.
                        replacement = str(kwargs.get(stripped, "{{" + stripped + "}}"))
                    prompt_to_render = prompt_to_render.replace(
                        "{{" + var_name + "}}", replacement
                    )
                    prompt_to_render = prompt_to_render.replace(
                        "{{ " + stripped + " }}", replacement
                    )
                elif "." in stripped and stripped in safe_context:
                    # Jinja parses dots as nested access; numeric segments
                    # become list indices.
                    parts = stripped.split(".")
                    value = safe_context.pop(stripped)
                    nest_dotted_value(safe_context, parts, value)

            # In Jinja mode, parse JSON strings to native objects right
            # before rendering so {% for %} loops work correctly.
            if self.template_format == "jinja":
                for key in list(safe_context.keys()):
                    val = safe_context[key]
                    if isinstance(val, str):
                        stripped = val.strip()
                        if (stripped.startswith("[") and stripped.endswith("]")) or \
                           (stripped.startswith("{") and stripped.endswith("}")):
                            try:
                                safe_context[key] = json.loads(val)
                            except (ValueError, json.JSONDecodeError):
                                pass

            try:
                template = self.env.from_string(prompt_to_render)
                rendered_prompt = template.render(**safe_context)
            except (jinja2.TemplateSyntaxError, jinja2.exceptions.SecurityError):
                # Parse failure or sandbox rejection: str.replace fallback.
                rendered_prompt = prompt_to_render
                for key, value in safe_context.items():
                    rendered_prompt = rendered_prompt.replace("{{" + key + "}}", str(value))
                    rendered_prompt = rendered_prompt.replace("{{ " + key + " }}", str(value))

            # Append data section with XML-tagged values for clarity
            if template_context:
                rendered_prompt += "\n\n--- Input Data ---\n"
                for k, v in template_context.items():
                    val_str = str(v)
                    if len(val_str) > 500:
                        val_str = val_str[:500] + "..."
                    rendered_prompt += f"<{k}>{val_str}</{k}>\n"
                rendered_prompt += "--- End Input Data ---"
        except Exception as e:
            raise ValueError(f"Error rendering rule prompt template: {str(e)}")

        # Inject row_context when data injection is enabled (no mapping required)
        row_context = kwargs.get("row_context")
        if row_context and not required_keys:
            from .context_window import fit_row_to_context

            rendered_prompt += "\n\n## Data\n"
            if isinstance(row_context, (dict, list)):
                rendered_prompt += fit_row_to_context(
                    row_context, max_chars=_MAX_CONTEXT_CHARS
                )
            else:
                ctx_str = str(row_context)
                if len(ctx_str) > _MAX_CONTEXT_CHARS:
                    ctx_str = fit_to_context(ctx_str, max_total_chars=_MAX_CONTEXT_CHARS, label="data")
                rendered_prompt += ctx_str

        logger.info(
            "custom_prompt_eval_template_rendered",
            template_context_keys=list(template_context.keys()),
            rendered_prompt_length=len(rendered_prompt),
        )

        # Build user message — multimodal if media present (images, audio, PDFs)
        # Uses the same pattern as DeterministicEvaluator for proven multimodal support
        user_text = (
            rendered_prompt + "\n\n"
            "Please return a JSON object with the following fields:\n"
            f" - result: Result must be either {self.system_template_value}.\n"
            " - explanation: An explanation of the result.\n"
        )

        # Detect media types and build content blocks using shared utility.
        # Two-stage: fast regex for image URLs, then batch detect_input_type.
        # Supports image, images, audio, PDF.
        input_data_types = kwargs.get("input_data_types", {})
        media_blocks, detected_media_types = detect_and_build_media_blocks(
            inputs=kwargs,
            required_keys=required_keys,
            input_data_types=input_data_types,
            image_urls=kwargs.get("image_urls"),
        )

        gt_blocks = kwargs.get("ground_truth_blocks") or []

        # GT exemplars first, then the case text, then case media.
        if media_blocks or gt_blocks:
            user_content = (
                gt_blocks
                + [{"type": "text", "text": user_text}]
                + media_blocks
            )
        else:
            user_content = user_text

        # Build system message: use custom system_prompt if provided, else generated.
        # Render via the shared helper so the System turn gets the same
        # edge-case coverage (spaces in var names, dotted names, syntax-
        # error fallback) as rule_prompt and the multi-turn messages.
        if self.system_prompt:
            try:
                system_content = self._render_template(
                    self.system_prompt, safe_context, kwargs
                )
            except Exception:
                system_content = self.system_prompt
        else:
            system_content = self._system_message()
        if gt_blocks:
            system_content = (system_content or "") + "\n\n" + GT_CALIBRATION_INSTRUCTION

        messages = [
            {
                "role": "system",
                "content": system_content,
            },
        ]

        logger.info(
            "custom_prompt_eval_system_prompt",
            system_prompt_source="custom" if self.system_prompt else "generated",
            has_media_blocks=bool(media_blocks),
            media_block_count=len(media_blocks),
        )

        # Add few-shot examples as user/assistant message pairs
        if self._few_shot_examples:
            for example in self._few_shot_examples:
                if example.get("input"):
                    messages.append({"role": "user", "content": example["input"]})
                if example.get("output"):
                    messages.append({"role": "assistant", "content": example["output"]})

        # Add additional message chain (user/assistant turns from the editor).
        # Render via the shared helper so each turn's Jinja handling matches
        # rule_prompt: spaces in var names, dotted names, and syntax-error
        # fallback are all covered.
        if self._messages:
            for msg in self._messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    continue  # Already handled above
                if content.strip():
                    try:
                        rendered = self._render_template(content, safe_context, kwargs)
                    except Exception:
                        rendered = content
                    messages.append({"role": role, "content": rendered})

        # Add the main eval user message (rendered prompt + multimodal)
        messages.append(
            {
                "role": "user",
                "content": user_content,
            },
        )
        logger.info(
            "custom_prompt_eval_llm_call_start",
            model=self._model,
            provider=self.provider,
            message_count=len(messages),
            has_media=bool(media_blocks),
            detected_modalities=list(detected_media_types.values()) if detected_media_types else [],
        )

        # Surface the missing-client case before the LLM try/except so the
        # error message isn't replaced by the block's generic fallback.
        if self._is_turing:
            try:
                from ee.turing.client import TuringClient  # noqa: F401
            except ImportError as exc:
                if settings.DEBUG:
                    logger.warning(
                        "Could not import ee.turing.client", exc_info=True
                    )
                raise ValueError(
                    f"Turing model {self._model!r} is not available on this "
                    "build. Select a bring-your-own model in the eval "
                    "settings and rerun."
                ) from exc

        try:
            if self._is_turing:
                from ee.turing.client import TuringClient

                turing_client = TuringClient()
                # TuringClient handles model upgrade (e.g. turing_large → turing_large_xl
                # for audio/pdf) and modality validation internally.
                chat_completion_response = turing_client.chat_completion(
                    model=self._model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    response_format=response_format_schema(
                        self._output_type,
                        getattr(self, "_choices", None),
                        multi_choice=bool(getattr(self, "_multi_choice", False)),
                    ),
                    knowledge_base_id=self.knowledge_base_id,
                    check_internet=self.check_internet,
                    detected_media_types=detected_media_types,
                )
                self.token_usage.update(turing_client.token_usage)
                self.cost.update(turing_client.cost)
            else:
                chat_completion_response = self.call_llm(
                    prompt=messages, provider=self.provider,
                    response_format=response_format_schema(
                        self._output_type,
                        getattr(self, "_choices", None),
                        multi_choice=bool(getattr(self, "_multi_choice", False)),
                    ),
                )

            logger.info(
                "custom_prompt_eval_llm_raw_response",
                response_length=len(str(chat_completion_response)),
            )

            chat_completion_response_json = extract_dict_from_string(
                chat_completion_response
            )

            logger.info(
                "custom_prompt_eval_parsed_result",
                result=chat_completion_response_json.get("result"),
            )

        except Exception as e:
            error_msg = str(e)
            # Log everything needed for production debugging
            logger.error(
                "custom_prompt_eval_error",
                error=error_msg,
                error_type=type(e).__name__,
                model=self._model,
                provider=self.provider,
                is_turing=self._is_turing,
                output_type=self._output_type,
                message_count=len(messages),
                has_media=bool(media_blocks),
                detected_modalities=list(detected_media_types.values()) if detected_media_types else [],
                required_keys=required_keys,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                has_knowledge_base=bool(self.knowledge_base_id),
                check_internet=self.check_internet,
                has_few_shot=bool(self._few_shot_examples),
                rendered_prompt_length=len(rendered_prompt) if rendered_prompt else 0,
                exc_info=True,
            )
            try:
                from ee.turing.client import ModalityNotSupportedError
            except ImportError:
                ModalityNotSupportedError = Exception

            if isinstance(e, ModalityNotSupportedError):
                raise
            if not self._is_turing:
                # Customer models: surface the actual error so they can debug
                raise ValueError(error_msg) from e
            raise ValueError(
                "Uh-oh! We ran into an error while running the evaluation. Please try again."
            ) from e

        end_time = time.time()
        eval_runtime_ms = int((end_time - start_time) * 1000)

        metadata = json.dumps({
            "usage": {
                "completion_tokens": self.token_usage["completion_tokens"],
                "prompt_tokens": self.token_usage["prompt_tokens"],
                "total_tokens": self.token_usage["total_tokens"],
            },
            "cost": {
                "total_cost": self.cost["total_cost"],
                "prompt_cost": self.cost["prompt_cost"],
                "completion_cost": self.cost["completion_cost"],
            },
            "response_time": eval_runtime_ms,
            "explanation": chat_completion_response_json["explanation"],
            # "data": chat_history,
        })

        result_value = chat_completion_response_json["result"]
        if self._output_type in ("score", "numeric"):
            result_value = clamp_unit_score(result_value)

        llm_eval_result: EvalResult = {
            "name": self.name,
            "display_name": self.display_name,
            "data": {"result": result_value},
            "failure": True if result_value == "Fail" else False,
            "metadata": metadata,
            "reason": chat_completion_response_json["explanation"],
            "runtime": eval_runtime_ms,
            "model": self._model,
            "metrics": [{"id": "custom_eval_score", "value": result_value if result_value is not None else 0.0}],
            "datapoint_field_annotations": None,
        }

        logger.info(
            "custom_prompt_eval_complete",
            result=chat_completion_response_json["result"],
            failure=llm_eval_result["failure"],
            runtime_ms=eval_runtime_ms,
            model=self._model,
        )

        return llm_eval_result