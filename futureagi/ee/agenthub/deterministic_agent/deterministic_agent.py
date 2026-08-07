"""Model-agnostic deterministic evaluation agent."""

import difflib
import json
import mimetypes
import re
from typing import Any, Dict, List, Optional

import structlog
from ee.agenthub.deterministic_agent.prompts import (
    ANALYSIS_PROMPT,
    PLANNING_PROMPT,
    VALIDATION_PROMPT,
)
from ee.turing.client import TuringClient
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.functions import detect_input_type
from agentic_eval.core.utils.json_utils import extract_dict_from_string
from agentic_eval.core.utils.llm_payloads import standardize_to_data_uri
from agentic_eval.core.utils.model_config import ModelConfigs
from agentic_eval.core_evals.fi_utils.json import JsonHelper
from lxml import etree
from tfc.utils.storage import get_file_from_s3

logger = structlog.get_logger(__name__)


class DeterministicAgent:
    """
    Model-agnostic deterministic evaluation agent.

    Routing logic:
    - provider=turing (or turing_* aliases) -> Turing API Service
    - Other models -> Direct LLM class (litellm)

    This agent does NOT:
    - Expose provider-specific routing for turing_* aliases
    - Create media/online/fallback LLM variants
    """

    TURING_MODEL_PREFIXES = ("turing_", "turing-")
    TURING_PROVIDER = ModelConfigs.TURING_LARGE.provider
    EMPTY_EVAL_RESPONSE_ERROR_MESSAGE = "Uh-oh! we ran into an error. Please try again."
    EMPTY_RESPONSE_RETRIES = 1

    @staticmethod
    def _preview_text(value: Any, limit: int = 300) -> str:
        text = "" if value is None else str(value)
        return text[:limit]

    def _raise_user_facing_error(self, log_key: str, **context: Any) -> None:
        """Log internal context and raise a safe user-facing error."""
        logger.error(
            log_key,
            route="gateway" if self.is_turing_model else "direct_llm",
            model_name=str(self.model_name),
            model_type=str(self.model_type or ""),
            routing_model=str(self.routing_model),
            provider=self._provider_for_logs(self.model_config),
            is_turing=self.is_turing_model,
            supports_audio=self.supports_audio,
            supports_pdf=self.supports_pdf,
            has_knowledge_base=bool(self.knowledge_base_id),
            check_internet=self.check_internet,
            fewshots_count=len(self.fewshots),
            criteria_length=len(self.criteria) if self.criteria else 0,
            empty_response_retries=self.EMPTY_RESPONSE_RETRIES,
            exc_info=True,
            **context,
        )
        raise ValueError(self.EMPTY_EVAL_RESPONSE_ERROR_MESSAGE)

    @staticmethod
    def _provider_for_logs(model_config: Optional[Any]) -> str:
        return model_config.provider if model_config else "unknown"

    @staticmethod
    def _wrap_variable(
        idx: int, inner_blocks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        # Keep variable tags stable because evaluator prompts depend on this format.
        return [
            {"type": "text", "text": f"<variable_{idx + 1}>"},
            *inner_blocks,
            {"type": "text", "text": f"</variable_{idx + 1}>"},
        ]

    @staticmethod
    def _normalize_explanation(explanation: str) -> str:
        """Normalize inline bullet formatting into newline-prefixed bullets."""
        if not explanation:
            return explanation
        if "\n- " in explanation and not re.search(
            r"(?<=[.!?\)])\s*-+\s+", explanation
        ):
            return explanation
        normalized = re.sub(r"(?<=[.!?\)])\s*-\s+", "\n- ", explanation)
        normalized = re.sub(r"(?<=[.!?\)])-\s+", "\n- ", normalized)
        return normalized

    @staticmethod
    def _is_url_like(value: Any) -> bool:
        # Accept remote object references and already-encoded data URIs.
        if not isinstance(value, str):
            return False
        text = value.strip().lower()
        return (
            text.startswith("http://")
            or text.startswith("https://")
            or text.startswith("s3://")
            or text.startswith("gs://")
            or text.startswith("data:")
        )

    def __init__(
        self,
        model_name: Optional[str] = None,
        model_type: Optional[str] = None,
        llm: Optional[LLM] = None,
        criteria: Optional[str] = None,
        check_internet: bool = False,
        fewshots: Optional[List] = None,
        knowledge_base_id: Optional[str] = None,
        turing_api_url: Optional[str] = None,
        **kwargs,
    ) -> None:
        """
        Initialize deterministic agent.

        Args:
            model_name: Model to use. Defaults to turing_large.
            model_type: Model alias/type from eval config (e.g. turing_large).
            llm: Optional pre-configured LLM instance.
            criteria: Evaluation criteria.
            check_internet: Enable internet search.
            fewshots: Few-shot examples.
            knowledge_base_id: Knowledge base ID for RAG.
            turing_api_url: Override Turing API URL.
            **kwargs: Additional arguments (ignored for compatibility).
        """
        if model_name is None and llm and hasattr(llm, "model_name"):
            model_name = llm.model_name
        if model_name is None:
            # Use a safe default when the caller does not pass model/model_type.
            model_name = ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name

        self.model_type = model_type

        self.model_name = str(model_name)

        route_model = model_type if model_type is not None else self.model_name
        config_lookup_model = str(model_type) if model_type else self.model_name
        selected_config = ModelConfigs.get_config(config_lookup_model)
        if selected_config is None and model_type:
            # Fall back to concrete model when alias lookup misses.
            selected_config = ModelConfigs.get_config(self.model_name)

        is_turing_provider = bool(
            selected_config and selected_config.provider == self.TURING_PROVIDER
        )
        is_turing_alias = self._is_turing_model(route_model) or self._is_turing_model(
            self.model_name
        )
        self.is_turing_model = is_turing_provider or is_turing_alias

        if self.is_turing_model:
            if model_type:
                self.routing_model = str(model_type)
            elif selected_config is not None:
                self.routing_model = selected_config.model_name
            else:
                self.routing_model = self.model_name
        else:
            self.routing_model = str(route_model)

        if not self.is_turing_model and selected_config is None:
            # Ensure non-turing flows always have a usable config.
            selected_config = ModelConfigs.VERTEX_GEMINI_2_5_PRO

        self.model_config = selected_config

        self.supports_audio = bool(
            self.model_config and self.model_config.supports_audio
        )
        self.supports_pdf = bool(self.model_config and self.model_config.supports_pdf)

        self.check_internet = check_internet
        self.fewshots = fewshots or []
        self.knowledge_base_id = knowledge_base_id
        self.criteria = criteria or ""

        self.turing_client: Optional[TuringClient] = None
        self.llm: Optional[LLM] = None

        if self.is_turing_model:
            self.turing_client = TuringClient()
        else:
            # Route non-turing requests through the direct litellm wrapper.
            if self.model_config is not None:
                self.llm = LLM(
                    model_name=self.model_config.model_name,
                    temperature=self.model_config.temperature,
                    max_tokens=self.model_config.max_tokens,
                    provider=self.model_config.provider,
                )
            else:
                self.llm = LLM(model_name=model_name)

    def _is_turing_model(self, model_name: Optional[str]) -> bool:
        # Keep legacy turing_* model aliases working via prefix fallback.
        lower_name = (model_name or "").lower()
        return any(
            lower_name.startswith(prefix) for prefix in self.TURING_MODEL_PREFIXES
        )

    def _call_llm(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        stage: str = "runtime",
    ) -> str:
        # Log request/response metadata for each internal stage.
        logger.debug(
            "deterministic_agent_stage_request",
            stage=stage,
            provider=(
                "turing_api"
                if self.is_turing_model
                else self._provider_for_logs(self.model_config)
            ),
            model=(
                self.routing_model
                if self.is_turing_model
                else str(
                    model or (self.llm.model_name if self.llm else self.model_name)
                )
            ),
            message_count=len(messages),
            has_response_format=response_format is not None,
        )

        if self.is_turing_model:
            if self.turing_client is None:
                self._raise_user_facing_error(
                    "deterministic_agent_turing_client_missing",
                    stage=stage,
                )
            # Send the alias (`routing_model`) to Turing API, not provider-native model name.
            try:
                result = self.turing_client.chat_completion(
                    model=self.routing_model,
                    messages=messages,
                    response_format=response_format,
                    knowledge_base_id=self.knowledge_base_id,
                    check_internet=False,
                )
            except ValueError:
                # Modality validation errors are safe to surface to the user.
                raise
            except Exception as e:
                self._raise_user_facing_error(
                    "deterministic_agent_turing_call_failed",
                    stage=stage,
                    model=self.routing_model,
                    error=str(e),
                )
            logger.debug(
                "deterministic_agent_stage_response",
                stage=stage,
                provider="turing_api",
                model=self.routing_model,
                finish_reason="stop",
                content_length=len(str(result)),
            )
            return str(result)

        if self.llm is None:
            self._raise_user_facing_error(
                "deterministic_agent_llm_missing",
                stage=stage,
            )
        # Call litellm directly for non-turing providers.
        try:
            result = self.llm._get_completion_content(
                messages=messages,
                response_format=response_format,
                model=model,
            )
        except Exception as e:
            self._raise_user_facing_error(
                "deterministic_agent_llm_call_failed",
                stage=stage,
                model=(
                    self.routing_model
                    if self.is_turing_model
                    else str(model or self.llm.model_name)
                ),
                error=str(e),
            )
        logger.debug(
            "deterministic_agent_stage_response",
            stage=stage,
            provider=self._provider_for_logs(self.model_config),
            model=(
                self.routing_model
                if self.is_turing_model
                else str(
                    model or (self.llm.model_name if self.llm else self.model_name)
                )
            ),
            finish_reason="stop",
            content_length=len(str(result)),
        )
        return str(result)

    def _retry_validation_response(
        self,
        *,
        validation_messages: List[Dict[str, Any]],
        model: Optional[str],
        stage: str,
    ) -> str:
        """Retry validation call when final output is empty."""
        retry_response = ""
        for attempt in range(self.EMPTY_RESPONSE_RETRIES):
            raw_response = self._call_llm(
                messages=validation_messages,
                model=model,
                stage=f"{stage}_retry_{attempt + 1}",
            )
            retry_response = (
                raw_response
                if isinstance(raw_response, str)
                else str(raw_response or "")
            )
            if retry_response and retry_response.strip():
                return retry_response
        return str(retry_response)

    def _retry_parse_validation_output(
        self,
        *,
        validation_messages: List[Dict[str, Any]],
        model: Optional[str],
        choices: List[str],
        stage: str,
    ) -> Optional[Dict[str, Any]]:
        """Retry validation once and return parsed output when valid."""
        retry_response = self._retry_validation_response(
            validation_messages=validation_messages,
            model=model,
            stage=stage,
        )
        if not retry_response or not retry_response.strip():
            return None

        try:
            parsed_response = extract_dict_from_string(retry_response)
            if not isinstance(parsed_response, dict):
                return None

            selected_choices = parsed_response.get("choices")
            explanation = parsed_response.get("explanation")
            if selected_choices is None or explanation is None:
                return None

            if not isinstance(selected_choices, list):
                selected_choices = [selected_choices]

            def get_closest_match(
                choice: str, available_choices: List[str]
            ) -> Optional[str]:
                matches = difflib.get_close_matches(
                    choice, available_choices, n=1, cutoff=0.9
                )
                return matches[0] if matches else None

            selected_choices = [
                get_closest_match(choice, choices) or choice
                for choice in selected_choices
            ]

            if not all(choice in choices for choice in selected_choices):
                return None
            if not selected_choices or not str(explanation).strip():
                return None

            parsed_response["choices"] = selected_choices
            parsed_response["explanation"] = self._normalize_explanation(
                str(explanation)
            )
            return parsed_response
        except Exception:
            return None

    def _get_kb_blocks(
        self,
        kb_identifier: str,
        seen_file_ids: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        # Resolve knowledge-base document ids into provider file blocks.
        urls = get_file_from_s3(str(kb_identifier))
        if not urls:
            return []

        blocks: List[Dict[str, Any]] = []
        for url in urls:
            if seen_file_ids is not None:
                if url in seen_file_ids:
                    continue
                seen_file_ids.add(url)

            blocks.append(
                {
                    "type": "file",
                    "file": {
                        "file_id": url,
                        "format": mimetypes.guess_type(url)[0] or "application/pdf",
                    },
                }
            )
        return blocks

    def _build_content_block(
        self,
        input_val: Any,
        input_type: str,
        idx: int,
        seen_kb_file_ids: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        # Normalize each supported modality into deterministic prompt blocks.
        if input_type in ("image", "images"):
            if input_val is None or (
                isinstance(input_val, str) and not input_val.strip()
            ):
                self._raise_user_facing_error(
                    "deterministic_agent_image_input_empty",
                    variable_index=idx + 1,
                )
            # Accept a single image or a JSON/list payload of images.
            image_inputs: List[Any]
            if isinstance(input_val, str):
                try:
                    parsed = json.loads(input_val)
                    image_inputs = parsed if isinstance(parsed, list) else [input_val]
                except (json.JSONDecodeError, TypeError):
                    image_inputs = [input_val]
            elif isinstance(input_val, list):
                image_inputs = input_val
            else:
                image_inputs = [input_val]

            if not image_inputs or all(not img for img in image_inputs):
                self._raise_user_facing_error(
                    "deterministic_agent_image_input_empty",
                    variable_index=idx + 1,
                )

            image_blocks: List[Dict[str, Any]] = []
            for image_number, img in enumerate(image_inputs, start=1):
                url = (
                    img
                    if self._is_url_like(img)
                    else standardize_to_data_uri(img, "image/jpeg")
                )
                image_block = {"type": "image_url", "image_url": {"url": url}}
                image_blocks.extend(
                    [
                        {"type": "text", "text": f"<image_{image_number}>"},
                        image_block,
                        {"type": "text", "text": f"</image_{image_number}>"},
                    ]
                )
            return self._wrap_variable(idx, image_blocks)

        if input_type == "audio":
            # Pass audio as data URI and wrap it in <audio> tags.
            if input_val is None or (
                isinstance(input_val, str) and not input_val.strip()
            ):
                self._raise_user_facing_error(
                    "deterministic_agent_audio_input_empty",
                    variable_index=idx + 1,
                )

            url = (
                input_val
                if self._is_url_like(input_val)
                else standardize_to_data_uri(input_val, "audio/mp3")
            )
            audio_block = {"type": "image_url", "image_url": {"url": url}}
            return self._wrap_variable(
                idx,
                [
                    {"type": "text", "text": "<audio>"},
                    audio_block,
                    {"type": "text", "text": "</audio>"},
                ],
            )

        if input_type == "pdf":
            # Send PDFs as provider file blocks.
            if input_val is None or (
                isinstance(input_val, str) and not input_val.strip()
            ):
                self._raise_user_facing_error(
                    "deterministic_agent_pdf_input_empty",
                    variable_index=idx + 1,
                )

            if self._is_url_like(input_val):
                pdf_block = {
                    "type": "file",
                    "file": {"file_id": input_val, "format": "application/pdf"},
                }
            else:
                data_uri = standardize_to_data_uri(input_val, "application/pdf")
                pdf_block = {
                    "type": "file",
                    "file": {"file_data": data_uri, "format": "application/pdf"},
                }
            return self._wrap_variable(
                idx,
                [
                    {"type": "text", "text": "<pdf>"},
                    pdf_block,
                    {"type": "text", "text": "</pdf>"},
                ],
            )

        if input_type == "knowledge_base":
            # Resolve knowledge-base ids into file blocks under one variable.
            kb_blocks = self._get_kb_blocks(
                str(input_val),
                seen_file_ids=seen_kb_file_ids,
            )
            tagged_kb_blocks: List[Dict[str, Any]] = []
            for kb_index, block in enumerate(kb_blocks, start=1):
                tagged_kb_blocks.extend(
                    [
                        {"type": "text", "text": f"<knowledge_base_file_{kb_index}>"},
                        block,
                        {"type": "text", "text": f"</knowledge_base_file_{kb_index}>"},
                    ]
                )
            return self._wrap_variable(idx, tagged_kb_blocks)

        if input_val is None or (isinstance(input_val, str) and not input_val.strip()):
            if isinstance(input_val, list) and all(not i for i in input_val):
                self._raise_user_facing_error(
                    "deterministic_agent_text_input_empty",
                    variable_index=idx + 1,
                )
            if not isinstance(input_val, list):
                self._raise_user_facing_error(
                    "deterministic_agent_text_input_empty",
                    variable_index=idx + 1,
                )

        if isinstance(input_val, list):
            # Keep list inputs deterministic by joining values in order.
            text_value = "\n".join(str(item) for item in input_val)
        else:
            text_value = "" if input_val is None else str(input_val)

        try:
            # Hint the model to reason on raw HTML when content is HTML-like.
            doc = etree.HTML(text_value)
            body = doc.find(".//body") if doc is not None else None
            if body is not None and len(body):
                text_value = f"```html\n{text_value}\n```"
        except Exception:
            pass

        text_block = {"type": "text", "text": text_value}
        return self._wrap_variable(idx, [text_block])

    def _get_prompt(self, payload: Dict[str, Any]) -> tuple:
        """
        Process inputs and create prompt.

        Returns:
            Tuple of (content_blocks, rule_prompt_with_variables)
        """
        inputs = payload.get("inputs", [])
        input_type = payload.get("input_type", [])
        rule_prompt = payload.get("rule_prompt", "")

        if not input_type or len(input_type) < len(inputs):
            # Backfill missing modalities from raw inputs.
            input_type_dict = detect_input_type(inputs)
            for value in input_type_dict.values():
                input_type.append(value)

        if all(value is None for value in inputs):
            self._raise_user_facing_error(
                "deterministic_agent_all_inputs_none",
            )

        content: List[Dict[str, Any]] = []
        variable_mapping: Dict[str, str] = {}
        seen_kb_file_ids: set[str] = set()

        for idx, (input_val, input_t) in enumerate(
            zip(inputs, input_type, strict=False)
        ):
            if input_t == "images":
                # Add guidance so the model treats an image set as one context.
                content.append(
                    {
                        "type": "text",
                        "text": (
                            "A set of images is provided for this evaluation. Consider them "
                            "together where appropriate, and reference image numbers only "
                            "when it helps clarify the explanation."
                        ),
                    }
                )

            blocks = self._build_content_block(
                input_val,
                input_t,
                idx,
                seen_kb_file_ids=seen_kb_file_ids,
            )
            content.extend(blocks)
            # Maintain template variable placeholders expected by eval rules.
            variable_mapping[f"variable_{idx + 1}"] = f" $variable_{idx + 1}$ "

        if self.knowledge_base_id:
            # Append evaluator-level KB files after per-variable blocks.
            all_files = self._get_kb_blocks(
                self.knowledge_base_id,
                seen_file_ids=seen_kb_file_ids,
            )
            if all_files:
                content.append(
                    {
                        "type": "text",
                        "text": "Additional documents supplied as Knowledge Base: ",
                    }
                )
                content.extend(all_files)

        for var_name, value in variable_mapping.items():
            # Replace {{variable_n}} placeholders with tagged variable references.
            rule_prompt = rule_prompt.replace(f"{{{{{var_name}}}}}", str(value))

        return content, rule_prompt

    def _fix_json_response(
        self, invalid_response: str, choices: List[str], multi_choice: bool
    ) -> Dict[str, Any]:
        # Run a last-resort repair pass when the model returns malformed JSON.
        repair_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""You are a JSON repair agent. Fix the following invalid JSON response to match the required format:

Original Response:
{invalid_response}

Required JSON Format:
{{
    "choices": [<list of selections from available choices>],
    "explanation": "<explanation text>"
}}

Available Choices: {choices}
Multiple Choices Allowed: {multi_choice}

Rules:
1. Extract any choices that appear to be selected in the response
2. Extract or summarize the explanation
3. Ensure choices exist in the available choices list
4. If multi_choice is False, only include the most confident choice
5. Return valid JSON matching the required format

Return ONLY the fixed JSON, nothing else.""",
                }
            ],
        }

        fixed_response = self._call_llm(
            messages=[repair_prompt],
            response_format={"type": "json_object"},
            stage="json_repair",
        )
        return JsonHelper.extract_json_from_text(fixed_response)

    def evaluate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Run deterministic scoring for all supported providers.
        logger.info(
            "deterministic_agent_evaluate_start",
            route=("turing_api" if self.is_turing_model else "direct_llm"),
        )

        inputs = payload.get("inputs", [])
        input_type = payload.get("input_type", [])
        choices = payload.get("choices", [])
        multi_choice = payload.get("multi_choice", False)
        rule_prompt = payload.get("rule_prompt", "")

        if not input_type or len(input_type) < len(inputs):
            input_type_dict = detect_input_type(inputs)
            input_type = list(input_type) if input_type else []
            for value in input_type_dict.values():
                input_type.append(value)
            payload["input_type"] = input_type

        has_audio = "audio" in input_type
        has_pdf = "pdf" in input_type
        has_kb = "knowledge_base" in input_type or bool(self.knowledge_base_id)
        eval_context = {
            "input_count": len(inputs),
            "input_types": input_type,
            "choices_count": len(choices) if isinstance(choices, list) else None,
            "multi_choice": multi_choice,
            "rule_prompt_length": len(rule_prompt or ""),
            "has_audio": has_audio,
            "has_pdf": has_pdf,
            "has_kb": has_kb,
        }

        if not self.is_turing_model:
            # Enforce modality capability checks for the direct-provider path.
            if has_audio and not self.supports_audio:
                self._raise_user_facing_error(
                    "deterministic_agent_audio_not_supported",
                    model=str(self.model_name),
                    **eval_context,
                )
            if has_pdf and not self.supports_pdf:
                self._raise_user_facing_error(
                    "deterministic_agent_pdf_not_supported",
                    model=str(self.model_name),
                    **eval_context,
                )
            if has_kb and not self.supports_pdf:
                self._raise_user_facing_error(
                    "deterministic_agent_kb_not_supported",
                    model=str(self.model_name),
                    **eval_context,
                )

        if not rule_prompt:
            self._raise_user_facing_error(
                "deterministic_agent_rule_prompt_missing", **eval_context
            )
        if not choices:
            self._raise_user_facing_error(
                "deterministic_agent_choices_empty", **eval_context
            )
        if not isinstance(choices, list):
            self._raise_user_facing_error(
                "deterministic_agent_choices_invalid_type",
                choices_type=type(choices).__name__,
                **eval_context,
            )

        primary_llm = self.llm

        content, rule_prompt = self._get_prompt(payload)
        content_list = content.copy()

        # Phase 1: plan the rubric-driven approach before scoring.
        planning_content = content.copy()
        planning_content.append(
            {
                "type": "text",
                "text": PLANNING_PROMPT.format(
                    rule_prompt=rule_prompt,
                    choices_text=choices,
                    multi_choice=multi_choice,
                ),
            }
        )
        planning_messages = [{"role": "user", "content": planning_content}]

        planning_response = self._call_llm(
            messages=planning_messages,
            model=(primary_llm.model_name if primary_llm else None),
            stage="planning",
        )

        # Phase 2: analyze content with optional few-shot context.
        analysis_content = content_list.copy()
        if self.fewshots:
            analysis_content.extend(self.fewshots)
        analysis_content.append(
            {
                "type": "text",
                "text": ANALYSIS_PROMPT.format(
                    rule_prompt=rule_prompt,
                    choices_text=choices,
                    multi_choice=multi_choice,
                    plan=planning_response,
                ),
            }
        )
        analysis_messages = [{"role": "user", "content": analysis_content}]

        analysis_response = self._call_llm(
            messages=analysis_messages,
            model=(primary_llm.model_name if primary_llm else None),
            stage="analysis",
        )

        if not analysis_response or "category" not in analysis_response.lower():
            analysis_response = self._call_llm(
                messages=analysis_messages,
                model=(primary_llm.model_name if primary_llm else None),
                stage="analysis_retry",
            )

        # Phase 3: validate and force structured JSON output.
        validation_content = content_list.copy()
        if self.fewshots:
            validation_content.extend(self.fewshots)
        validation_content.append(
            {
                "type": "text",
                "text": VALIDATION_PROMPT.format(
                    rule_prompt=rule_prompt,
                    choices_text=choices,
                    multi_choice=multi_choice,
                    analysis=analysis_response,
                ),
            }
        )
        validation_messages = [{"role": "user", "content": validation_content}]

        response = self._call_llm(
            messages=validation_messages,
            model=(primary_llm.model_name if primary_llm else None),
            stage="validation",
        )

        if not response or not response.strip():
            response = self._retry_validation_response(
                validation_messages=validation_messages,
                model=(primary_llm.model_name if primary_llm else None),
                stage="validation_empty",
            )

        if not response or not response.strip():
            self._raise_user_facing_error(
                "deterministic_agent_empty_validation_response_after_retry",
                stage="validation",
                model=str(primary_llm.model_name if primary_llm else self.model_name),
                check_internet=False,
                response_preview=self._preview_text(response),
                **eval_context,
            )

        try:
            # Parse and validate the model output contract.
            parsed_response = extract_dict_from_string(response)
            if not isinstance(parsed_response, dict):
                self._raise_user_facing_error(
                    "deterministic_agent_parsed_response_not_dict",
                    response_preview=self._preview_text(response),
                    **eval_context,
                )

            selected_choices = parsed_response.get("choices")
            explanation = parsed_response.get("explanation")
            if selected_choices is None or explanation is None:
                retried_result = self._retry_parse_validation_output(
                    validation_messages=validation_messages,
                    model=(primary_llm.model_name if primary_llm else None),
                    choices=choices,
                    stage="validation_missing_fields",
                )
                if retried_result is not None:
                    return retried_result
                self._raise_user_facing_error(
                    "deterministic_agent_missing_fields_after_retry",
                    response_preview=self._preview_text(response),
                    **eval_context,
                )

            if not isinstance(selected_choices, list):
                selected_choices = [selected_choices]

            def get_closest_match(
                choice: str, available_choices: List[str]
            ) -> Optional[str]:
                matches = difflib.get_close_matches(
                    choice, available_choices, n=1, cutoff=0.9
                )
                return matches[0] if matches else None

            selected_choices = [
                get_closest_match(choice, choices) or choice
                for choice in selected_choices
            ]

            if not all(choice in choices for choice in selected_choices):
                self._raise_user_facing_error(
                    "deterministic_agent_selected_choice_not_in_options",
                    selected_choices=selected_choices,
                    choices=choices,
                    response_preview=self._preview_text(response),
                    **eval_context,
                )

            if not selected_choices or not str(explanation).strip():
                retried_result = self._retry_parse_validation_output(
                    validation_messages=validation_messages,
                    model=(primary_llm.model_name if primary_llm else None),
                    choices=choices,
                    stage="validation_empty_final_output",
                )
                if retried_result is not None:
                    return retried_result
                self._raise_user_facing_error(
                    "deterministic_agent_empty_final_output_after_retry",
                    response_preview=self._preview_text(response),
                    **eval_context,
                )

            parsed_response["explanation"] = self._normalize_explanation(
                str(explanation)
            )
            return parsed_response

        except Exception as e:
            # Attempt one JSON-repair pass if parsing fails.
            logger.exception(
                "deterministic_agent_response_parse_failed",
                error=str(e),
                response_preview=self._preview_text(response),
                **eval_context,
            )
            logger.warning("deterministic_agent_response_repair_attempted")
            fixed_response: Dict[str, Any]
            try:
                fixed_response = self._fix_json_response(
                    response, choices, multi_choice
                )
            except Exception as repair_error:
                self._raise_user_facing_error(
                    "deterministic_agent_response_repair_failed",
                    error=str(repair_error),
                    response_preview=self._preview_text(response),
                    **eval_context,
                )

            selected_choices = fixed_response.get("choices")
            explanation = fixed_response.get("explanation")
            if selected_choices is None or explanation is None:
                retried_result = self._retry_parse_validation_output(
                    validation_messages=validation_messages,
                    model=(primary_llm.model_name if primary_llm else None),
                    choices=choices,
                    stage="validation_repair_missing_fields",
                )
                if retried_result is not None:
                    return retried_result
                self._raise_user_facing_error(
                    "deterministic_agent_missing_fields_after_repair",
                    response_preview=self._preview_text(response),
                    **eval_context,
                )

            if not isinstance(selected_choices, list):
                selected_choices = [selected_choices]
                fixed_response["choices"] = selected_choices

            fixed_response["choices"] = [
                choice for choice in selected_choices if choice in choices
            ]

            if not fixed_response["choices"]:
                retried_result = self._retry_parse_validation_output(
                    validation_messages=validation_messages,
                    model=(primary_llm.model_name if primary_llm else None),
                    choices=choices,
                    stage="validation_repair_empty_choices",
                )
                if retried_result is not None:
                    return retried_result
                self._raise_user_facing_error(
                    "deterministic_agent_empty_choices_after_repair",
                    response_preview=self._preview_text(response),
                    **eval_context,
                )

            if not str(explanation).strip():
                retried_result = self._retry_parse_validation_output(
                    validation_messages=validation_messages,
                    model=(primary_llm.model_name if primary_llm else None),
                    choices=choices,
                    stage="validation_repair_empty_explanation",
                )
                if retried_result is not None:
                    return retried_result
                self._raise_user_facing_error(
                    "deterministic_agent_empty_explanation_after_repair",
                    response_preview=self._preview_text(response),
                    **eval_context,
                )

            fixed_response["explanation"] = self._normalize_explanation(
                str(explanation)
            )
            return fixed_response

    @property
    def token_usage(self) -> Dict[str, int]:
        if self.is_turing_model:
            return (
                self.turing_client.token_usage
                if self.turing_client
                else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            )
        if self.llm:
            return self.llm.token_usage
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    @property
    def cost(self) -> Dict[str, float]:
        if self.is_turing_model:
            return (
                self.turing_client.cost
                if self.turing_client
                else {"prompt_cost": 0.0, "completion_cost": 0.0, "total_cost": 0.0}
            )
        if self.llm:
            return self.llm.cost
        return {"prompt_cost": 0.0, "completion_cost": 0.0, "total_cost": 0.0}
